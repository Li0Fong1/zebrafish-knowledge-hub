# -*- coding: utf-8 -*-
"""把 FAISS manifest 中的分片登记到 literature_vector_shard（事项三收口）。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "database"))
sys.path.insert(0, str(ROOT / "core"))

from core.entry import bootstrap_app

bootstrap_app()

from config import FAISS_PUBMED_YES_DIR
from literature_store import finish_job, start_job, upsert_vector_shard, write_sync_log
from mysql_client import MySQLClient


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index-dir", default=FAISS_PUBMED_YES_DIR)
    ap.add_argument("--index-version", default="gte_mbase_v1")
    args = ap.parse_args()
    man = Path(args.index_dir) / "manifest.json"
    if not man.is_file():
        raise SystemExit(f"missing {man}")
    m = json.loads(man.read_text(encoding="utf-8-sig"))
    info = m.get("shard_info") or {}
    client = MySQLClient()
    db = next(client.get_db())
    job = start_job(
        db,
        job_type="register_vector_shards",
        params={"index_dir": str(args.index_dir), "shards": len(info)},
        index_version=args.index_version,
    )
    db.commit()
    n = 0
    try:
        for sid, meta in sorted(info.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
            if not isinstance(meta, dict):
                continue
            path = str(Path(args.index_dir) / f"shard_{int(sid):04d}")
            upsert_vector_shard(
                db,
                {
                    "index_version": args.index_version,
                    "shard_id": int(sid),
                    "root_path": str(args.index_dir),
                    "shard_path": path,
                    "paper_count": int(meta.get("paper_count") or 0),
                    "chunk_count": int(meta.get("chunk_count") or 0),
                    "kind": meta.get("kind") or "pubmed_pmid",
                    "embedding_model": m.get("embedding_model")
                    or os.getenv("EMBEDDING_MODEL_PATH", ""),
                    "embedding_dim": int(m.get("embedding_dim") or 768),
                    "status": "ready",
                    "manifest_json": meta,
                },
            )
            n += 1
        write_sync_log(
            db,
            action="register_shards",
            index_version=args.index_version,
            detail={"registered": n, "index_dir": str(args.index_dir)},
        )
        finish_job(db, job, status="done", progress=f"{n}/{len(info)}")
        db.commit()
    except Exception as e:
        db.rollback()
        finish_job(db, job, status="error", error=str(e))
        db.commit()
        raise
    print(f"registered {n} shards index_version={args.index_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
