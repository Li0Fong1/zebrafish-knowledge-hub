# -*- coding: utf-8 -*-
"""
将现网 faiss_pubmed_yes 登记为只读索引版本 bge_small_v1（事项三 M2）。

不修改 FAISS 文件；仅写 MySQL literature_vector_shard + sync_log。
"""
from __future__ import annotations

import json
import os
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
from config import FAISS_PUBMED_YES_DIR
from literature_store import count_shards, start_job, finish_job, upsert_vector_shard, write_sync_log
from mysql_client import MySQLClient

INDEX_VERSION = "bge_small_v1"
EMBEDDING_MODEL = "models/bge-small-zh-v1.5"
EMBEDDING_DIM = 512


def main() -> int:
    root = Path(FAISS_PUBMED_YES_DIR)
    mf = root / "manifest.json"
    if not mf.is_file():
        safe_print(f"[失败] 缺少 manifest: {mf}")
        return 1
    manifest = json.loads(mf.read_text(encoding="utf-8-sig"))
    shards = manifest.get("shards") or []
    info = manifest.get("shard_info") or {}

    summary = {
        "root_path": str(root),
        "index_version": INDEX_VERSION,
        "embedding_model": EMBEDDING_MODEL,
        "embedding_dim": EMBEDDING_DIM,
        "shard_count": len(shards),
        "total_papers": int(manifest.get("total_papers") or 0),
        "total_chunks": int(manifest.get("total_chunks") or 0),
        "pmid_fulltext_papers": manifest.get("pmid_fulltext_papers"),
        "eno_papers": manifest.get("eno_papers"),
        "eno_chunks": manifest.get("eno_chunks"),
        "chunking": manifest.get("chunking"),
        "started_at": manifest.get("started_at"),
        "updated_at": manifest.get("updated_at"),
        "status": "readonly",
    }

    client = MySQLClient()
    db = next(client.get_db())
    try:
        job_id = start_job(
            db,
            job_type="register_shard",
            params=summary,
            index_version=INDEX_VERSION,
        )
        for sid in shards:
            sid_i = int(sid)
            meta = info.get(str(sid_i)) or info.get(sid_i) or {}
            shard_path = str(root / f"shard_{sid_i:04d}")
            upsert_vector_shard(
                db,
                {
                    "shard_id": sid_i,
                    "index_version": INDEX_VERSION,
                    "root_path": str(root),
                    "shard_path": shard_path,
                    "embedding_model": EMBEDDING_MODEL,
                    "embedding_dim": EMBEDDING_DIM,
                    "paper_count": int(meta.get("paper_count") or 0),
                    "chunk_count": int(meta.get("chunk_count") or 0),
                    "status": "readonly",
                    "kind": meta.get("kind"),
                    "manifest_json": meta,
                },
            )
        # 汇总行：shard_id=-1 存根目录摘要
        upsert_vector_shard(
            db,
            {
                "shard_id": -1,
                "index_version": INDEX_VERSION,
                "root_path": str(root),
                "shard_path": str(root),
                "embedding_model": EMBEDDING_MODEL,
                "embedding_dim": EMBEDDING_DIM,
                "paper_count": int(summary["total_papers"]),
                "chunk_count": int(summary["total_chunks"]),
                "status": "readonly",
                "kind": "index_root",
                "manifest_json": summary,
            },
        )
        write_sync_log(
            db,
            action="register_shard",
            actor="register_faiss_pubmed_yes_baseline",
            index_version=INDEX_VERSION,
            detail=summary,
        )
        finish_job(db, job_id, status="done", progress=f"{len(shards)} shards")
        db.commit()
        n = count_shards(db, INDEX_VERSION)
        safe_print(f"[完成] 已登记 {INDEX_VERSION}：{n} 行（含 root 摘要），只读，未改 FAISS 文件")
        safe_print(f"  path={root}")
        safe_print(f"  papers={summary['total_papers']} chunks={summary['total_chunks']} shards={len(shards)} dim={EMBEDDING_DIM}")
        return 0
    except Exception as e:
        db.rollback()
        safe_print(f"[失败] {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
