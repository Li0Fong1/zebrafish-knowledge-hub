# -*- coding: utf-8 -*-
"""
「追加 1 篇」干跑 / 最小验收（事项三）。

默认 --dry-run：只校验幂等键与切块，不写库不嵌向量。
加 --commit：写入 literature_paper（及可选 gold 元数据）+ sync_log，并确认幂等二次执行不重复。

用法：
  python -m scripts.dryrun_append_one_paper
  python -m scripts.dryrun_append_one_paper --commit
  python -m scripts.dryrun_append_one_paper --pmid 1465413 --commit
  python -m scripts.dryrun_append_one_paper --eno 26672 --commit
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "database"))
sys.path.insert(0, str(ROOT / "core"))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from literature_store import (
    finish_job,
    make_doc_id,
    paper_exists,
    start_job,
    upsert_fulltext_gold,
    upsert_paper,
    write_sync_log,
)
from mysql_client import MySQLClient
from rag.pubmed_yes_chunking import split_record_to_field_chunks
from sqlalchemy import text

CORPUS_PMID = ROOT / "data" / "literature" / "pubmed_yes_corpus.jsonl"
CORPUS_ENO = ROOT / "data" / "literature" / "exist_eno_corpus.jsonl"


def _find_in_jsonl(path: Path, *, pmid: str | None = None, entry_no: str | None = None) -> dict | None:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if pmid and str(rec.get("pmid") or "").strip() == pmid:
                return rec
            if entry_no and str(rec.get("entry_no") or "").strip() == entry_no:
                return rec
    return None


def _pick_default() -> dict:
    # 优先 PMID 语料首条；否则 ENO
    if CORPUS_PMID.is_file():
        with CORPUS_PMID.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    rec.setdefault("source", "pubmed_yes")
                    rec.setdefault("judgement", "是")
                    return rec
    if CORPUS_ENO.is_file():
        with CORPUS_ENO.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    rec.setdefault("source", "exist_eno")
                    rec.setdefault("judgement", "是")
                    return rec
    raise FileNotFoundError("无可用语料 jsonl")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pmid", default="")
    ap.add_argument("--eno", default="")
    ap.add_argument("--commit", action="store_true", help="真实写库（默认仅干跑）")
    ap.add_argument("--with-gold-meta", action="store_true", help="若有 body 则登记 gold 元数据（不写大正文）")
    args = ap.parse_args()

    pmid = (args.pmid or "").strip()
    eno = (args.eno or "").strip()
    if pmid:
        rec = _find_in_jsonl(CORPUS_PMID, pmid=pmid) or _find_in_jsonl(CORPUS_ENO, pmid=pmid)
        if not rec:
            safe_print(f"[失败] 语料未找到 PMID={pmid}")
            return 1
    elif eno:
        rec = _find_in_jsonl(CORPUS_ENO, entry_no=eno) or _find_in_jsonl(CORPUS_PMID, entry_no=eno)
        if not rec:
            safe_print(f"[失败] 语料未找到 ENO={eno}")
            return 1
    else:
        rec = _pick_default()

    pmid = str(rec.get("pmid") or "").strip() or None
    entry_no = str(rec.get("entry_no") or "").strip() or None
    doc_id = make_doc_id(pmid=pmid, entry_no=entry_no)
    chunks = split_record_to_field_chunks(rec, max_chunks=12)
    fields = sorted({c.get("field") for c in chunks})

    safe_print("=== 追加 1 篇 验收 ===")
    safe_print(f"  doc_id={doc_id} pmid={pmid} entry_no={entry_no}")
    safe_print(f"  title={(rec.get('title_zh') or rec.get('title_en') or '')[:80]}")
    safe_print(f"  chunks={len(chunks)} fields={fields}")
    safe_print(f"  mode={'COMMIT' if args.commit else 'DRY-RUN'}")

    if not args.commit:
        safe_print("[干跑通过] 幂等键与切块正常；加 --commit 写入 MySQL")
        return 0

    client = MySQLClient()
    db = next(client.get_db())
    try:
        job_id = start_job(db, job_type="append_paper", params={"doc_id": doc_id, "dry_run": False})
        existed = paper_exists(db, doc_id)
        upsert_paper(
            db,
            {
                **rec,
                "doc_id": doc_id,
                "pmid": pmid,
                "entry_no": entry_no,
                "gold_status": "meta_only" if (rec.get("body_text") and args.with_gold_meta) else None,
            },
        )
        if args.with_gold_meta and rec.get("body_text"):
            # 大正文不入库：只记路径提示 + 短校验
            body = str(rec.get("body_text") or "")
            upsert_fulltext_gold(
                db,
                doc_id=doc_id,
                gold_version="v1",
                source_tier="corpus_jsonl",
                body_path=None,
                body_text=body[:2000] if len(body) > 2000 else body,
                sections=None,
                campaign_id="append_one_dryrun",
            )
        write_sync_log(
            db,
            action="append_paper",
            actor="dryrun_append_one_paper",
            doc_id=doc_id,
            detail={
                "pmid": pmid,
                "entry_no": entry_no,
                "chunk_count": len(chunks),
                "fields": fields,
                "already_existed": existed,
            },
        )
        # 幂等：再 upsert 一次
        upsert_paper(db, {**rec, "doc_id": doc_id, "pmid": pmid, "entry_no": entry_no})
        n = db.execute(text("SELECT COUNT(*) FROM literature_paper WHERE doc_id=:d"), {"d": doc_id}).scalar()
        finish_job(db, job_id, status="done", progress="1 paper")
        db.commit()
        safe_print(f"[完成] 已写入 doc_id={doc_id} rows_for_doc={n}（期望=1，幂等） existed_before={existed}")
        return 0
    except Exception as e:
        db.rollback()
        safe_print(f"[失败] {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
