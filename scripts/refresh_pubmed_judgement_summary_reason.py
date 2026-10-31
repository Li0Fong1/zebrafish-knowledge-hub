# -*- coding: utf-8 -*-
"""
按新规范刷新 PubMed 已有判别结果的 abstract_zh 与 reason（不改变 judgement）。

用法：
  python -m scripts.refresh_pubmed_judgement_summary_reason --resume --turbo
  python -m scripts.refresh_pubmed_judgement_summary_reason --limit 10
  python -m scripts.launch_refresh_pubmed_judgement_detached --force
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from mysql_client import MySQLClient
from sqlalchemy import text

from scripts.judge_literature_zebrafish_batch import (
    NO_ABSTRACT_ZH,
    _llm_refresh_summary_reason,
    _thread_llm,
)
from scripts.judge_rate_limit_guard import BalanceGuard, InsufficientBalanceError, RateLimitGuard, is_rate_limit_error

PROGRESS = ROOT / "data" / "literature" / "pubmed_judgement_refresh.json"
BATCH = int(os.getenv("JUDGE_REFRESH_FETCH", "200"))
DB_FLUSH = int(os.getenv("JUDGE_REFRESH_DB_FLUSH", "40"))
_print_lock = threading.Lock()


def _count_total(client: MySQLClient) -> int:
    db = next(client.get_db())
    try:
        return int(
            db.execute(
                text("SELECT COUNT(*) FROM literature_pubmed_judgement WHERE mode != 'meta'")
            ).scalar()
            or 0
        )
    finally:
        db.close()


def _fetch_batch(client: MySQLClient, *, cursor: int, limit: int) -> list[tuple]:
    db = next(client.get_db())
    try:
        raw = db.execute(
            text(
                """
                SELECT pmid, title_en, abstract_en, abstract_zh, reason, judgement, mode
                FROM literature_pubmed_judgement
                WHERE mode != 'meta' AND pmid+0 > :cur
                ORDER BY pmid+0 ASC
                LIMIT :lim
                """
            ),
            {"cur": cursor, "lim": limit},
        ).fetchall()
    finally:
        db.close()
    return [tuple(r) for r in raw]


def _update_batch(client: MySQLClient, updates: list[tuple[str, str, str, str]]) -> None:
    if not updates:
        return
    db = next(client.get_db())
    try:
        sql = text(
            "UPDATE literature_pubmed_judgement "
            "SET abstract_zh=:az, reason=:rs, judged_at=NOW() WHERE pmid=:p"
        )
        for pmid, az, rs, _ in updates:
            db.execute(sql, {"p": pmid, "az": az[:16000], "rs": rs[:512]})
        db.commit()
    finally:
        db.close()


def _one(
    row: tuple,
    guard: RateLimitGuard | None,
    balance: BalanceGuard | None,
) -> tuple[str, str, str, str] | None:
    pmid, te, ae, _az, _rs, judgement, _mode = row
    if balance and balance.halted():
        return None

    def _on_rl() -> None:
        if guard:
            guard.record_429()

    try:
        new_az, new_rs = _llm_refresh_summary_reason(
            te or "",
            ae or "",
            judgement or "无法判别",
            llm=_thread_llm(),
            on_rate_limit=_on_rl,
        )
        if not new_az:
            new_az = NO_ABSTRACT_ZH
        return pmid, new_az, new_rs, judgement or ""
    except InsufficientBalanceError as e:
        if balance:
            balance.trip(e)
        return None
    except Exception as e:
        if balance and balance.trip(e):
            return None
        if guard and is_rate_limit_error(e):
            guard.record_429(str(e))
        safe_print(f"  [跳过] {pmid}: {e}")
        return None


def run(*, resume: bool = True, turbo: bool = True, limit: int = 0) -> dict:
    client = MySQLClient()
    state: dict = {}
    if resume and PROGRESS.is_file():
        state = json.loads(PROGRESS.read_text(encoding="utf-8"))

    guard = RateLimitGuard() if turbo else None
    balance = BalanceGuard()
    workers = guard.workers if guard else 1
    cursor = int(state.get("cursor_pmid") or 0)
    done_session = int(state.get("done_session") or 0)
    if limit <= 0 and state.get("status") == "limit_reached":
        state["status"] = "running"
    total = _count_total(client)
    t0 = time.time()

    safe_print(
        f"刷新 PubMed 摘要/理由：共 {total:,} 篇（跳过 meta），"
        f"cursor={cursor}，并发×{workers}"
    )
    state.setdefault("started_at", datetime.now().isoformat(timespec="seconds"))
    state["total"] = total

    while True:
        if balance.halted():
            safe_print("  [余额不足] 刷新已暂停")
            state["status"] = "balance_halted"
            break
        if limit > 0 and done_session >= limit:
            state["status"] = "limit_reached"
            break
        if guard:
            guard.tick_recover()
            workers = guard.workers
        batch_lim = min(BATCH, limit - done_session) if limit > 0 else BATCH
        if batch_lim <= 0:
            break

        rows = _fetch_batch(client, cursor=cursor, limit=batch_lim)
        if not rows:
            state["status"] = "completed"
            break

        buf: list[tuple[str, str, str, str]] = []
        if workers <= 1:
            for row in rows:
                out = _one(row, guard, balance)
                if out:
                    buf.append(out)
                    if len(buf) >= DB_FLUSH:
                        _update_batch(client, buf)
                        done_session += len(buf)
                        buf.clear()
                if balance.halted():
                    break
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(_one, row, guard, balance): row for row in rows}
                for fut in as_completed(futs):
                    if balance.halted():
                        break
                    out = fut.result()
                    if out:
                        buf.append(out)
                        if len(buf) >= DB_FLUSH:
                            _update_batch(client, buf)
                            done_session += len(buf)
                            buf.clear()

        _update_batch(client, buf)
        done_session += len(buf)
        cursor = max(int(r[0]) for r in rows)
        elapsed = max(time.time() - t0, 1)
        rate = done_session / elapsed * 60
        with _print_lock:
            safe_print(
                f"  已刷新 {done_session:,}/{total:,} 篇 ~{rate:.0f}篇/分 cursor={cursor}"
            )

        state.update({
            "cursor_pmid": cursor,
            "done_session": done_session,
            "workers": workers,
            "balance": balance.status(),
            "status": "running",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        PROGRESS.parent.mkdir(parents=True, exist_ok=True)
        PROGRESS.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    remaining = max(0, total - done_session) if state.get("status") != "completed" else 0
    if state.get("status") == "completed":
        remaining = 0
        state["finished_at"] = datetime.now().isoformat(timespec="seconds")
        safe_print(f"[完成] 共刷新 {done_session:,} 篇，正在导出 CSV…")
        try:
            from scripts.export_pubmed_judgement_full import main as export_main

            export_main()
            state["exported_at"] = datetime.now().isoformat(timespec="seconds")
            safe_print("[完成] 已导出 项目阶段/05_数据与库/归档/data_export/pubmed_judgement_full/")
        except Exception as e:
            safe_print(f"  [警告] 导出失败: {e}")

    stats = {
        "done_session": done_session,
        "remaining_estimate": remaining,
        "elapsed_min": round((time.time() - t0) / 60, 1),
        "status": state.get("status", "running"),
    }
    state.update(stats)
    PROGRESS.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    safe_print(f"[结束] 本次刷新 {done_session:,} 篇，状态={stats['status']}")
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description="刷新 PubMed 判别摘要/理由（不改 judgement）")
    p.add_argument("--resume", action="store_true", help="断点续跑（默认开启）")
    p.add_argument("--reset", action="store_true", help="从头重跑")
    p.add_argument("--turbo", action="store_true", default=True)
    p.add_argument("--serial", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    args = p.parse_args()
    if args.reset and PROGRESS.is_file():
        PROGRESS.unlink()
    run(resume=not args.reset, turbo=not args.serial, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
