# -*- coding: utf-8 -*-
"""
对 MySQL 库存 literature_pubmed 全量做斑马鱼研究对象判别（与 1000 篇样本同一 LLM 标准）。

默认 turbo 并发 18，429 保险自动降至 12，稳定后恢复 18。

用法：
  python -m scripts.judge_pubmed_inventory --resume --turbo
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from mysql_client import MySQLClient
from sqlalchemy import text

from scripts.curate_literature_test_batch import Article
from scripts.judge_literature_zebrafish_batch import (
    CHECKPOINT_EVERY,
    INVENTORY_ECONOMY,
    INVENTORY_REASON_MAX,
    _fallback_judge,
    _llm_judge,
    _llm_judge_inventory,
    _llm_translate_inventory,
    _thread_llm,
)
from scripts.judge_hybrid_triage import INVENTORY_HYBRID, HybridStats, triage_zebrafish
from scripts.judge_quality_guard import QualityGuard
from scripts.judge_rate_limit_guard import (
    BalanceGuard,
    InsufficientBalanceError,
    RateLimitGuard,
    is_balance_error,
    is_rate_limit_error,
)

PROGRESS_FILE = ROOT / "data" / "literature" / "pubmed_judgement_progress.json"
MIGRATION = ROOT / "sql" / "literature_pubmed_judgement_migration.sql"
BATCH_FETCH = int(os.getenv("JUDGE_INVENTORY_FETCH", "400"))
DB_FLUSH = int(os.getenv("JUDGE_INVENTORY_DB_FLUSH", "40"))
_print_lock = threading.Lock()


@dataclass
class JudgeRow:
    pmid: str
    title_en: str
    abstract_en: str
    title_zh: str
    abstract_zh: str
    judgement: str
    reason: str
    mode: str  # llm | hybrid | meta


@dataclass
class JudgeOutcome:
    row: Optional[JudgeRow]
    retry: bool = False


def _default_workers() -> int:
    if os.getenv("JUDGE_INVENTORY_WORKERS", "").strip().isdigit():
        return max(1, int(os.getenv("JUDGE_INVENTORY_WORKERS", "18")))
    return int(os.getenv("JUDGE_INVENTORY_WORKERS_HIGH", "18"))


def _ensure_schema(client: MySQLClient) -> None:
    if not MIGRATION.is_file():
        return
    db = next(client.get_db())
    try:
        for stmt in MIGRATION.read_text(encoding="utf-8").split(";"):
            s = stmt.strip()
            if s and not s.startswith("--"):
                db.execute(text(s))
        db.commit()
    finally:
        db.close()


def _parse_pubmed(raw: str) -> tuple[str, str]:
    root = ET.fromstring(raw)
    title = (root.findtext(".//ArticleTitle") or "").strip()
    parts = []
    for ab in root.findall(".//AbstractText"):
        label = ab.get("Label") or ""
        txt = (ab.text or "").strip()
        if label and txt:
            parts.append(f"{label}: {txt}")
        elif txt:
            parts.append(txt)
    return title, " ".join(parts).strip()


def _load_progress() -> dict:
    if PROGRESS_FILE.is_file():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_progress(state: dict) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _counts(client: MySQLClient) -> tuple[int, int, int, int, int]:
    db = next(client.get_db())
    try:
        total = int(db.execute(text("SELECT COUNT(*) FROM literature_pubmed")).scalar() or 0)
        done = int(db.execute(text("SELECT COUNT(*) FROM literature_pubmed_judgement")).scalar() or 0)
        yes_n = int(
            db.execute(text("SELECT COUNT(*) FROM literature_pubmed_judgement WHERE judgement='是'")).scalar() or 0
        )
        no_n = int(
            db.execute(text("SELECT COUNT(*) FROM literature_pubmed_judgement WHERE judgement='否'")).scalar() or 0
        )
        unk = int(
            db.execute(
                text("SELECT COUNT(*) FROM literature_pubmed_judgement WHERE judgement='无法判别'")
            ).scalar()
            or 0
        )
    finally:
        db.close()
    return total, done, yes_n, no_n, unk


def _fetch_by_pmids(client: MySQLClient, pmids: list[str]) -> list[tuple[str, str]]:
    if not pmids:
        return []
    db = next(client.get_db())
    try:
        placeholders = ", ".join(f":p{i}" for i in range(len(pmids)))
        params = {f"p{i}": p for i, p in enumerate(pmids)}
        rows = db.execute(
            text(f"SELECT pmid, raw_xml FROM literature_pubmed WHERE pmid IN ({placeholders})"),
            params,
        ).fetchall()
    finally:
        db.close()
    return [(str(r[0]), r[1]) for r in rows]


def _fetch_pending(client: MySQLClient, *, limit: int) -> list[tuple[str, str]]:
    db = next(client.get_db())
    try:
        rows = db.execute(
            text(
                "SELECT p.pmid, p.raw_xml FROM literature_pubmed p "
                "LEFT JOIN literature_pubmed_judgement j ON p.pmid = j.pmid "
                "WHERE j.pmid IS NULL "
                "ORDER BY p.pmid+0 ASC LIMIT :lim"
            ),
            {"lim": limit},
        ).fetchall()
    finally:
        db.close()
    return [(str(r[0]), r[1]) for r in rows]


def _insert_batch(client: MySQLClient, rows: list[JudgeRow]) -> None:
    if not rows:
        return
    db = next(client.get_db())
    try:
        sql = text(
            "INSERT INTO literature_pubmed_judgement "
            "(pmid, title_en, abstract_en, title_zh, abstract_zh, judgement, reason, mode) "
            "VALUES (:pmid, :te, :ae, :tz, :az, :j, :r, :m) "
            "ON DUPLICATE KEY UPDATE "
            "title_zh=VALUES(title_zh), abstract_zh=VALUES(abstract_zh), "
            "judgement=VALUES(judgement), reason=VALUES(reason), mode=VALUES(mode), "
            "judged_at=NOW()"
        )
        for row in rows:
            db.execute(
                sql,
                {
                    "pmid": row.pmid,
                    "te": row.title_en[:8000] if row.title_en else None,
                    "ae": row.abstract_en[:16000] if row.abstract_en else None,
                    "tz": row.title_zh[:8000] if row.title_zh else None,
                    "az": row.abstract_zh[:16000] if row.abstract_zh else None,
                    "j": row.judgement,
                    "r": (row.reason or "")[:512],
                    "m": row.mode,
                },
            )
        db.commit()
    finally:
        db.close()


def _judge_one(
    pmid: str,
    raw: str,
    use_llm: bool,
    guard: RateLimitGuard | None,
    quality: QualityGuard | None,
    hybrid_stats: HybridStats | None = None,
    balance_guard: BalanceGuard | None = None,
) -> JudgeOutcome:
    try:
        title, abstract = _parse_pubmed(raw)
    except ET.ParseError:
        if quality:
            quality.record_meta()
        return JudgeOutcome(
            JudgeRow(pmid, "", "", "", "无摘要", "无法判别", "XML 解析失败", "meta"),
            retry=False,
        )

    if not title:
        if quality:
            quality.record_meta()
        return JudgeOutcome(
            JudgeRow(pmid, "", "", "", "无摘要", "无法判别", "XML 无标题", "meta"),
            retry=False,
        )

    if not use_llm:
        j = _fallback_judge(
            Article(path=Path(f"{pmid}.txt"), filename=f"PUBMED_{pmid}.txt", title=title, abstract=abstract)
        )
        return JudgeOutcome(
            JudgeRow(pmid, title, abstract, j.title_zh, j.abstract_zh, j.result, j.reason, "llm"),
            retry=False,
        )

    if balance_guard and balance_guard.halted():
        return JudgeOutcome(None, retry=True)

    def _on_rl() -> None:
        if guard:
            guard.record_429()

    try:
        llm = _thread_llm()
        tier = "gray"
        judgement = ""
        reason = ""

        if INVENTORY_HYBRID:
            tier, pre_j, rule_reason = triage_zebrafish(title, abstract)
            if tier in ("clear_yes", "clear_no") and pre_j:
                title_zh, abstract_zh = _llm_translate_inventory(
                    title, abstract, llm=llm, on_rate_limit=_on_rl
                )
                judgement = pre_j
                reason = (rule_reason or "")[:INVENTORY_REASON_MAX]
                if hybrid_stats:
                    hybrid_stats.record(tier, translate=True)
                if quality:
                    quality.record_llm_success()
                    quality.clear_retry(pmid)
                return JudgeOutcome(
                    JudgeRow(pmid, title, abstract, title_zh, abstract_zh, judgement, reason, "hybrid"),
                    retry=False,
                )
            if hybrid_stats:
                hybrid_stats.record("gray", translate=False)

        judge_fn = _llm_judge_inventory if INVENTORY_ECONOMY else _llm_judge
        title_zh, abstract_zh, judgement, reason = judge_fn(
            title, abstract, llm=llm, on_rate_limit=_on_rl
        )
        if judgement not in ("是", "否", "无法判别"):
            judgement = "无法判别"
        if quality:
            quality.record_llm_success()
            quality.clear_retry(pmid)
        return JudgeOutcome(
            JudgeRow(pmid, title, abstract, title_zh, abstract_zh, judgement, reason, "llm"),
            retry=False,
        )
    except InsufficientBalanceError as e:
        if balance_guard:
            balance_guard.trip(e)
        if quality:
            quality.record_llm_fail(pmid, str(e))
        return JudgeOutcome(None, retry=True)
    except Exception as e:
        if balance_guard and balance_guard.trip(e):
            if quality:
                quality.record_llm_fail(pmid, str(e))
            return JudgeOutcome(None, retry=True)
        if guard and is_rate_limit_error(e):
            guard.record_429(str(e))
        if quality and not quality.allow_rule_fallback():
            quality.record_llm_fail(pmid, str(e))
            if quality:
                quality.maybe_cooldown()
            return JudgeOutcome(None, retry=True)
        j = _fallback_judge(
            Article(path=Path(f"{pmid}.txt"), filename=f"PUBMED_{pmid}.txt", title=title, abstract=abstract)
        )
        return JudgeOutcome(
            JudgeRow(pmid, title, abstract, j.title_zh, j.abstract_zh, j.result, str(e)[:200], "llm"),
            retry=False,
        )


def _run_batch(
    client: MySQLClient,
    rows: list[tuple[str, str]],
    use_llm: bool,
    workers: int,
    guard: RateLimitGuard | None,
    quality: QualityGuard | None,
    pending_buf: list[JudgeRow],
    flush_fn,
    hybrid_stats: HybridStats | None = None,
    balance_guard: BalanceGuard | None = None,
) -> int:
    skipped = 0

    def _handle(pmid: str, raw: str) -> None:
        nonlocal skipped
        if balance_guard and balance_guard.halted():
            skipped += 1
            return
        out = _judge_one(pmid, raw, use_llm, guard, quality, hybrid_stats, balance_guard)
        if out.row:
            pending_buf.append(out.row)
            if len(pending_buf) >= DB_FLUSH:
                flush_fn(pending_buf)
        elif out.retry:
            skipped += 1

    if workers <= 1:
        for pmid, raw in rows:
            _handle(pmid, raw)
            if use_llm:
                time.sleep(float(os.getenv("JUDGE_REQUEST_INTERVAL", "0.05")))
        return skipped

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(
                _judge_one, pmid, raw, use_llm, guard, quality, hybrid_stats, balance_guard
            ): pmid
            for pmid, raw in rows
        }
        for fut in as_completed(futs):
            if balance_guard and balance_guard.halted():
                break
            try:
                out = fut.result()
                if out.row:
                    pending_buf.append(out.row)
                    if len(pending_buf) >= DB_FLUSH:
                        flush_fn(pending_buf)
                elif out.retry:
                    skipped += 1
            except InsufficientBalanceError as e:
                pmid = futs[fut]
                if balance_guard:
                    balance_guard.trip(e)
                if quality:
                    quality.record_llm_fail(pmid, str(e))
                    skipped += 1
            except Exception as e:
                pmid = futs[fut]
                if guard and is_rate_limit_error(e):
                    guard.record_429(str(e))
                if quality:
                    quality.record_llm_fail(pmid, str(e))
                    skipped += 1
    return skipped


def _process_retry_queue(
    client: MySQLClient,
    use_llm: bool,
    guard: RateLimitGuard | None,
    quality: QualityGuard,
    flush_fn,
    hybrid_stats: HybridStats | None = None,
    balance_guard: BalanceGuard | None = None,
    *,
    workers: int = 8,
) -> int:
    """低并发重试失败队列，直至 LLM 成功或达最大次数。"""
    done = 0
    while True:
        if balance_guard and balance_guard.halted():
            break
        pmids = quality.pop_retry_batch(80)
        if not pmids:
            break
        rows = _fetch_by_pmids(client, pmids)
        pending: list[JudgeRow] = []
        w = min(workers, guard.workers if guard else workers)
        skipped = _run_batch(
            client, rows, use_llm, w, guard, quality, pending, flush_fn, hybrid_stats, balance_guard
        )
        flush_fn(pending)
        done += len(rows) - skipped
        if guard:
            guard.tick_recover()
    return done


def run_inventory(*, resume: bool = True, limit: int = 0, turbo: bool = True) -> dict:
    # 全库判别：更多 LLM 重试，禁止默认规则降级
    os.environ.setdefault("JUDGE_LLM_RETRIES", os.getenv("JUDGE_INVENTORY_LLM_RETRIES", "6"))

    client = MySQLClient()
    _ensure_schema(client)
    use_llm = bool(os.getenv("LLM_API_KEY", "").strip())
    state = _load_progress() if resume else {}
    guard = RateLimitGuard() if turbo and use_llm else None
    quality = QualityGuard() if use_llm else None
    workers = guard.workers if guard else (_default_workers() if turbo and use_llm else 1)
    hybrid_stats = HybridStats() if use_llm and INVENTORY_HYBRID else None
    balance_guard = BalanceGuard() if use_llm else None
    processed_session = 0
    t0 = time.time()

    if quality and resume:
        quality.requeue_non_llm_from_db(client)

    total, done0, _, _, _ = _counts(client)
    qnote = "LLM-only+质量保险" if quality and not quality.allow_rule_fallback() else ""
    econ = "中文一句话+MD" if INVENTORY_ECONOMY else "完整"
    hybrid_note = "混合" if hybrid_stats else ""
    tags = "+".join(x for x in (qnote, econ, hybrid_note) if x)
    mode_label = f"并发×{workers} [TURBO+429保险+{tags}]" if workers > 1 else "串行"
    safe_print(f"PubMed 库存 {total:,} 篇，已判别 {done0:,} 篇，{mode_label}")
    if quality and quality.retry_count:
        safe_print(f"  重试队列待处理: {quality.retry_count} 篇")

    last_pmid = state.get("last_pmid", "")

    def _flush(buf: list[JudgeRow]) -> None:
        nonlocal last_pmid, processed_session
        if not buf:
            return
        _insert_batch(client, buf[:])
        processed_session += len(buf)
        last_pmid = buf[-1].pmid
        buf.clear()
        total_n, done_n, yes_n, no_n, unk_n = _counts(client)
        elapsed = max(time.time() - t0, 1)
        rate = processed_session / elapsed * 60
        w = guard.workers if guard else workers
        q_st = quality.status() if quality else None
        retry_n = quality.retry_count if quality else 0
        with _print_lock:
            safe_print(
                f"  [{done_n:,}/{total_n:,}] +{DB_FLUSH} 篇 "
                f"({100*done_n/max(total_n,1):.1f}%) ~{rate:.0f}篇/分 并发×{w} "
                f"重试队列={retry_n}"
            )
        state.update({
            "last_pmid": last_pmid,
            "done": done_n,
            "total": total_n,
            "yes": yes_n,
            "no": no_n,
            "unknown": unk_n,
            "workers": w,
            "turbo": turbo,
            "hybrid": hybrid_stats.to_dict() if hybrid_stats else None,
            "balance": balance_guard.status() if balance_guard else None,
            "guard": guard.status() if guard else None,
            "quality": q_st,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        _save_progress(state)

    while True:
        if balance_guard and balance_guard.halted():
            safe_print("  [余额不足] 任务已暂停，已完成结果已保存。充值后请 --resume 续跑。")
            break
        if limit > 0 and processed_session >= limit:
            break
        if guard:
            guard.tick_recover()
            workers = guard.workers

        batch_lim = min(BATCH_FETCH, limit - processed_session) if limit > 0 else BATCH_FETCH
        if batch_lim <= 0:
            break
        rows = _fetch_pending(client, limit=batch_lim)
        if not rows:
            break

        pending_buf: list[JudgeRow] = []
        _run_batch(
            client, rows, use_llm, workers, guard, quality,
            pending_buf, _flush, hybrid_stats, balance_guard,
        )
        _flush(pending_buf)
        if balance_guard and balance_guard.halted():
            break

    if (
        quality and use_llm and quality.retry_count
        and not (balance_guard and balance_guard.halted())
    ):
        safe_print(f"  [质量保险] 处理重试队列（{quality.retry_count} 篇，低并发）…")
        _process_retry_queue(
            client, use_llm, guard, quality, _flush, hybrid_stats, balance_guard, workers=8
        )

    total, done, yes_n, no_n, unk = _counts(client)
    w = guard.workers if guard else workers
    stats = {
        "total": total,
        "done": done,
        "yes": yes_n,
        "no": no_n,
        "unknown": unk,
        "finished": done >= total,
        "mode": "llm" if use_llm else "rule",
        "workers": w,
        "turbo": turbo,
        "guard": guard.status() if guard else None,
        "hybrid": hybrid_stats.to_dict() if hybrid_stats else None,
        "balance": balance_guard.status() if balance_guard else None,
        "quality": quality.status() if quality else None,
        "elapsed_min": round((time.time() - t0) / 60, 1),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    state.update(stats)
    _save_progress(state)
    safe_print(f"\n[完成] 判别 {done:,}/{total:,}（是={yes_n:,} 否={no_n:,} 无法={unk:,}）")
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description="PubMed 全库斑马鱼研究对象判别")
    p.add_argument("--resume", action="store_true", help="断点续传")
    p.add_argument("--turbo", action="store_true", help="并发 turbo（默认开启）")
    p.add_argument("--serial", action="store_true", help="强制串行（调试用）")
    p.add_argument("--limit", type=int, default=0, help="本次最多处理篇数，0=全部剩余")
    args = p.parse_args()
    turbo = not args.serial and (args.turbo or True)
    run_inventory(resume=args.resume or True, limit=args.limit, turbo=turbo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
