# -*- coding: utf-8 -*-
"""
补全 literature_pubmed_judgement 中未中译的 title_zh / abstract_zh。

仅翻译，不修改 judgement / reason / mode。

用法：
  python -m scripts.backfill_pubmed_judgement_zh --resume --turbo
  python -m scripts.backfill_pubmed_judgement_zh --pmids 42387966,42377268
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
    NO_ABSTRACT_LABEL,
    NO_ABSTRACT_ZH,
    _has_chinese,
    _llm_backfill_zh,
    _thread_llm,
)
from scripts.judge_rate_limit_guard import BalanceGuard, InsufficientBalanceError, RateLimitGuard, is_rate_limit_error

PROGRESS = ROOT / "data" / "literature" / "pubmed_judgement_zh_backfill.json"
BATCH = int(os.getenv("JUDGE_BACKFILL_FETCH", "200"))
DB_FLUSH = int(os.getenv("JUDGE_BACKFILL_DB_FLUSH", "40"))
_print_lock = threading.Lock()


def _needs_title(title_en: str | None, title_zh: str | None) -> bool:
    te = (title_en or "").strip()
    if len(te) < 4:
        return False
    tz = (title_zh or "").strip()
    return not _has_chinese(tz) or tz == te


def _needs_summary(abstract_en: str | None, abstract_zh: str | None) -> bool:
    ae = (abstract_en or "").strip()
    az = (abstract_zh or "").strip()
    if az in (NO_ABSTRACT_LABEL, NO_ABSTRACT_ZH, "", "无摘要"):
        return len(ae) > 40
    if len(ae) <= 40 and not az:
        return False
    return not _has_chinese(az)


def _fetch_pending(client: MySQLClient, *, limit: int, pmids: list[str] | None = None) -> list[tuple]:
    db = next(client.get_db())
    try:
        if pmids:
            ph = ", ".join(f":p{i}" for i in range(len(pmids)))
            params = {f"p{i}": p for i, p in enumerate(pmids)}
            q = f"""
                SELECT pmid, title_en, abstract_en, title_zh, abstract_zh, mode
                FROM literature_pubmed_judgement
                WHERE pmid IN ({ph}) AND mode != 'meta'
            """
            raw = db.execute(text(q), params).fetchall()
        else:
            raw = db.execute(
                text(
                    """
                    SELECT pmid, title_en, abstract_en, title_zh, abstract_zh, mode
                    FROM literature_pubmed_judgement
                    WHERE mode != 'meta'
                      AND (
                        (title_en IS NOT NULL AND CHAR_LENGTH(title_en) > 3
                         AND (title_zh IS NULL OR title_zh = title_en OR title_zh NOT REGEXP '[一-龥]'))
                        OR
                        (abstract_en IS NOT NULL AND CHAR_LENGTH(abstract_en) > 40
                         AND (abstract_zh IS NULL OR abstract_zh NOT REGEXP '[一-龥]'
                              OR abstract_zh IN ('No abstract','无摘要','')))
                      )
                    ORDER BY pmid+0 ASC
                    LIMIT :lim
                    """
                ),
                {"lim": limit},
            ).fetchall()
    finally:
        db.close()
    out = []
    for r in raw:
        pmid, te, ae, tz, az, mode = r
        nt = _needs_title(te, tz)
        ns = _needs_summary(ae, az)
        if nt or ns:
            out.append((str(pmid), te, ae, tz, az, nt, ns))
    return out


def _update_batch(client: MySQLClient, updates: list[tuple[str, str, str]]) -> None:
    if not updates:
        return
    db = next(client.get_db())
    try:
        sql = text(
            "UPDATE literature_pubmed_judgement SET title_zh=:tz, abstract_zh=:az WHERE pmid=:p"
        )
        for pmid, tz, az in updates:
            db.execute(sql, {"p": pmid, "tz": tz[:8000], "az": az[:16000]})
        db.commit()
    finally:
        db.close()


def _one(
    row: tuple,
    guard: RateLimitGuard | None,
    balance: BalanceGuard | None,
) -> tuple[str, str, str] | None:
    pmid, te, ae, tz, az, nt, ns = row
    if balance and balance.halted():
        return None

    def _on_rl() -> None:
        if guard:
            guard.record_429()

    try:
        new_tz, new_az = _llm_backfill_zh(
            te or "",
            ae or "",
            az or "",
            need_title=nt,
            need_summary=ns,
            llm=_thread_llm(),
            on_rate_limit=_on_rl,
        )
        if not nt:
            new_tz = (tz or te or "").strip()
        if not ns:
            new_az = (az or NO_ABSTRACT_ZH).strip()
        return pmid, new_tz, new_az
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


def _count_pending(client: MySQLClient, *, pmids: list[str] | None = None) -> int:
    return len(_fetch_pending(client, limit=100000, pmids=pmids))


def run(*, resume: bool = True, turbo: bool = True, limit: int = 0, pmids: list[str] | None = None) -> dict:
    client = MySQLClient()
    state = {}
    if resume and PROGRESS.is_file():
        state = json.loads(PROGRESS.read_text(encoding="utf-8"))
    guard = RateLimitGuard() if turbo else None
    balance = BalanceGuard()
    workers = guard.workers if guard else 1
    done_session = 0
    t0 = time.time()

    pending_total = _count_pending(client, pmids=pmids)
    safe_print(f"待中译补全约 {pending_total:,} 篇，并发×{workers}")
    state.setdefault("started_at", datetime.now().isoformat(timespec="seconds"))
    state["pending_at_start"] = state.get("pending_at_start") or pending_total

    while True:
        if balance.halted():
            safe_print("  [余额不足] 中译补全已暂停")
            state["status"] = "balance_halted"
            break
        if limit > 0 and done_session >= limit:
            break
        if guard:
            guard.tick_recover()
            workers = guard.workers
        batch_lim = min(BATCH, limit - done_session) if limit > 0 else BATCH
        if batch_lim <= 0:
            break
        rows = _fetch_pending(client, limit=batch_lim, pmids=pmids)
        if not rows:
            state["status"] = "completed"
            break
        if pmids:
            # 指定 pmid 只跑一轮
            pass

        buf: list[tuple[str, str, str]] = []
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
        elapsed = max(time.time() - t0, 1)
        rate = done_session / elapsed * 60
        with _print_lock:
            safe_print(f"  已中译补全 {done_session:,} 篇 ~{rate:.0f}篇/分")
        pending_left = _count_pending(client, pmids=pmids)
        state.update({
            "done_session": done_session,
            "pending_remaining": pending_left,
            "workers": workers,
            "balance": balance.status(),
            "status": "running",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        PROGRESS.parent.mkdir(parents=True, exist_ok=True)
        PROGRESS.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        if pmids:
            break

    stats = {
        "done_session": done_session,
        "pending_remaining": _count_pending(client, pmids=pmids),
        "elapsed_min": round((time.time() - t0) / 60, 1),
    }
    if stats["pending_remaining"] == 0:
        stats["status"] = "completed"
        stats["finished_at"] = datetime.now().isoformat(timespec="seconds")
    state.update(stats)
    PROGRESS.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    safe_print(f"[完成] 本次中译补全 {done_session:,} 篇")
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description="PubMed 判别结果中文补译")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--turbo", action="store_true", default=True)
    p.add_argument("--serial", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--pmids", type=str, default="", help="逗号分隔，仅处理指定 PMID")
    args = p.parse_args()
    pmids = [x.strip() for x in args.pmids.split(",") if x.strip()] or None
    run(resume=args.resume or True, turbo=not args.serial, limit=args.limit, pmids=pmids)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
