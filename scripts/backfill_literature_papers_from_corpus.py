# -*- coding: utf-8 -*-
"""从 pubmed_yes_corpus.jsonl 低优先级回填 literature_paper（事项三 M1）。

不写 FAISS、不加载 embedding；可与 gte 重嵌并行，默认小事务批量。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "database"))
sys.path.insert(0, str(ROOT / "core"))

from core.entry import bootstrap_app

bootstrap_app()

from config import PUBMED_YES_CORPUS_JSONL
from literature_store import start_job, finish_job, upsert_paper, write_sync_log
from mysql_client import MySQLClient

PROGRESS = ROOT / "解析文件" / "papers_backfill_progress.txt"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=PUBMED_YES_CORPUS_JSONL)
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep-ms", type=int, default=20, help="每批后休眠，降低与重嵌争用")
    args = ap.parse_args()

    try:
        import ctypes

        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000
        )
    except Exception:
        pass

    corpus = Path(args.corpus)
    if not corpus.is_file():
        raise SystemExit(f"缺少语料 {corpus}")

    client = MySQLClient()
    db = next(client.get_db())
    job_id = None
    try:
        job_id = start_job(
            db,
            job_type="backfill_papers_from_corpus",
            params={"corpus": str(corpus), "batch": args.batch},
        )
        db.commit()
    except Exception:
        db.rollback()

    n = 0
    ok = 0
    t0 = time.perf_counter()
    batch: list[dict] = []

    def flush() -> None:
        nonlocal ok, batch
        if not batch:
            return
        for rec in batch:
            upsert_paper(
                db,
                {
                    "pmid": rec.get("pmid"),
                    "entry_no": rec.get("entry_no"),
                    "doi": rec.get("doi"),
                    "pmcid": rec.get("pmcid"),
                    "source": rec.get("source") or "pubmed_yes",
                    "title_en": rec.get("title_en") or rec.get("title"),
                    "title_zh": rec.get("title_zh"),
                    "year": rec.get("year"),
                    "journal": rec.get("journal"),
                    "judgement": rec.get("judgement") or "是",
                    "has_fulltext": bool(rec.get("has_fulltext") or rec.get("body") or rec.get("fulltext")),
                    "gold_status": rec.get("gold_status"),
                },
            )
            ok += 1
        db.commit()
        batch = []

    try:
        with corpus.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                rec = json.loads(line)
                batch.append(rec)
                n += 1
                if len(batch) >= max(1, args.batch):
                    flush()
                    PROGRESS.write_text(
                        f"更新: {datetime.now().isoformat(timespec='seconds')}\n"
                        f"pid: {os.getpid()}\n"
                        f"scanned: {n}\n"
                        f"upserted: {ok}\n"
                        f"elapsed_sec: {round(time.perf_counter()-t0,1)}\n",
                        encoding="utf-8",
                    )
                    if args.sleep_ms > 0:
                        time.sleep(args.sleep_ms / 1000.0)
                if args.limit and n >= args.limit:
                    break
        flush()
        if job_id is not None:
            finish_job(db, job_id, status="done", progress=f"{ok}/{n}")
            write_sync_log(
                db,
                action="backfill_papers",
                actor="backfill_literature_papers_from_corpus",
                detail={"upserted": ok, "scanned": n},
            )
            db.commit()
    except Exception as e:
        db.rollback()
        if job_id is not None:
            try:
                finish_job(db, job_id, status="failed", progress=f"{ok}/{n}", error=str(e))
                db.commit()
            except Exception:
                pass
        raise
    finally:
        db.close()

    PROGRESS.write_text(
        f"更新: {datetime.now().isoformat(timespec='seconds')}\n"
        f"status: done\nscanned: {n}\nupserted: {ok}\n"
        f"elapsed_sec: {round(time.perf_counter()-t0,1)}\n",
        encoding="utf-8",
    )
    print(f"[完成] papers 回填 scanned={n} upserted={ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
