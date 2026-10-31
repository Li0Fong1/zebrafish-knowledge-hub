# -*- coding: utf-8 -*-
"""从语料 jsonl 批量登记 literature_fulltext_gold（事项三 M3）。

默认只写元数据 + 正文截断预览，避免把整篇全文塞进 MySQL。
  python -m scripts.backfill_literature_fulltext_gold_from_corpus
  python -m scripts.backfill_literature_fulltext_gold_from_corpus --corpus data/literature/exist_eno_corpus.jsonl --batch 100
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "database"))
sys.path.insert(0, str(ROOT / "core"))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from literature_store import (
    finish_job,
    make_doc_id,
    start_job,
    upsert_fulltext_gold,
    upsert_paper,
    write_sync_log,
)
from mysql_client import MySQLClient

PROG = ROOT / "解析文件" / "gold_meta_backfill_progress.txt"


def _write_prog(text: str) -> None:
    PROG.parent.mkdir(parents=True, exist_ok=True)
    PROG.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--corpus",
        default=str(ROOT / "data" / "literature" / "pubmed_yes_corpus.jsonl"),
    )
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--sleep-ms", type=int, default=5)
    ap.add_argument("--min-body", type=int, default=800, help="最短 body_text 才登记金标")
    ap.add_argument("--preview-chars", type=int, default=2000)
    ap.add_argument("--campaign-id", default="corpus_gold_meta_v1")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    path = Path(args.corpus)
    if not path.is_file():
        safe_print(f"[失败] missing {path}")
        return 1

    client = MySQLClient()
    db = next(client.get_db())
    job = start_job(
        db,
        job_type="backfill_fulltext_gold_meta",
        params={"corpus": str(path), "min_body": args.min_body},
    )
    db.commit()

    scanned = upserted = skipped = 0
    t0 = time.time()
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                scanned += 1
                if args.limit and upserted >= args.limit:
                    break
                try:
                    rec = json.loads(line)
                except Exception:
                    skipped += 1
                    continue
                body = str(rec.get("body_text") or "")
                if len(body) < int(args.min_body):
                    skipped += 1
                    continue
                pmid = str(rec.get("pmid") or "").strip() or None
                entry_no = str(rec.get("entry_no") or "").strip() or None
                doc_id = make_doc_id(pmid=pmid, entry_no=entry_no)
                upsert_paper(
                    db,
                    {
                        **rec,
                        "doc_id": doc_id,
                        "pmid": pmid,
                        "entry_no": entry_no,
                        "gold_status": "meta_preview",
                    },
                )
                preview = body[: int(args.preview_chars)]
                upsert_fulltext_gold(
                    db,
                    doc_id=doc_id,
                    gold_version="v1",
                    source_tier=str(rec.get("fulltext_source") or "corpus_jsonl"),
                    body_path=None,
                    body_text=preview,
                    sections=None,
                    campaign_id=args.campaign_id,
                )
                upserted += 1
                if upserted % max(1, int(args.batch)) == 0:
                    db.commit()
                    _write_prog(
                        "\n".join(
                            [
                                "gold meta backfill",
                                f"corpus: {path.name}",
                                f"scanned: {scanned}",
                                f"upserted: {upserted}",
                                f"skipped: {skipped}",
                                f"elapsed_sec: {round(time.time() - t0, 1)}",
                            ]
                        )
                    )
                    safe_print(f"  progress upserted={upserted} scanned={scanned}")
                if args.sleep_ms > 0:
                    time.sleep(args.sleep_ms / 1000.0)

        write_sync_log(
            db,
            action="backfill_fulltext_gold_meta",
            actor="backfill_literature_fulltext_gold_from_corpus",
            detail={
                "corpus": str(path),
                "scanned": scanned,
                "upserted": upserted,
                "skipped": skipped,
            },
        )
        finish_job(db, job, status="done", progress=f"{upserted}/{scanned}")
        db.commit()
        _write_prog(
            "\n".join(
                [
                    "gold meta backfill",
                    "status: done",
                    f"corpus: {path.name}",
                    f"scanned: {scanned}",
                    f"upserted: {upserted}",
                    f"skipped: {skipped}",
                    f"elapsed_sec: {round(time.time() - t0, 1)}",
                ]
            )
        )
        safe_print(f"[完成] gold meta scanned={scanned} upserted={upserted} skipped={skipped}")
        return 0
    except Exception as e:
        db.rollback()
        finish_job(db, job, status="error", error=str(e))
        db.commit()
        safe_print(f"[失败] {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
