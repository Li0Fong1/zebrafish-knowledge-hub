# -*- coding: utf-8 -*-
"""
将金标全文升级后的 PMID 文献追加嵌入（自然段切分），不覆盖原摘要分片。
语料默认：data/literature/pubmed_yes_fulltext_upgrade.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("FAISS_DISABLE_AVX2", "1")

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from common_utils import safe_print
from config import (
    EXIST_ENO_MAX_BODY_CHARS,
    EXIST_ENO_MAX_CHUNKS,
    EXIST_ENO_SHARD_PAPERS,
    FAISS_PUBMED_YES_DIR,
    PUBMED_YES_CHUNK_OVERLAP,
    PUBMED_YES_CHUNK_SIZE,
    PUBMED_YES_PARA_MAX_CHARS,
    PUBMED_YES_PARA_MIN_CHARS,
)
from rag.pubmed_yes_chunking import split_record_to_field_chunks
from rag.rag_builder import BgeEmbedding, EMBEDDING_MODEL_PATH

MANIFEST = Path(FAISS_PUBMED_YES_DIR) / "manifest.json"
DEFAULT_CORPUS = ROOT / "data" / "literature" / "pubmed_yes_fulltext_upgrade.jsonl"
KIND = "pmid_fulltext"


def _load_manifest() -> dict:
    if MANIFEST.is_file():
        return json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    return {"shards": [], "shard_info": {}, "total_papers": 0, "total_chunks": 0}


def _save_manifest(m: dict) -> None:
    Path(FAISS_PUBMED_YES_DIR).mkdir(parents=True, exist_ok=True)
    m["updated_at"] = datetime.now().isoformat(timespec="seconds")
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def _kind_shards(manifest: dict, kind: str) -> list[int]:
    out = []
    for k, v in (manifest.get("shard_info") or {}).items():
        if isinstance(v, dict) and v.get("kind") == kind:
            out.append(int(k))
    return sorted(out)


def embed(*, corpus: Path, resume: bool = True, reset: bool = False, limit: int = 0) -> dict:
    if not corpus.is_file():
        safe_print(f"缺少语料 {corpus}")
        return {}
    rows = []
    with corpus.open(encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    if not rows:
        safe_print("升级列表为空")
        return {}

    manifest = _load_manifest()
    if reset:
        for sid in _kind_shards(manifest, KIND):
            sd = Path(FAISS_PUBMED_YES_DIR) / f"shard_{sid:04d}"
            shutil.rmtree(sd, ignore_errors=True)
            (manifest.get("shard_info") or {}).pop(str(sid), None)
        # 重算 totals
        papers = chunks = 0
        for v in (manifest.get("shard_info") or {}).values():
            if not isinstance(v, dict):
                continue
            papers += int(v.get("paper_count") or 0)
            chunks += int(v.get("chunk_count") or 0)
        manifest["total_papers"] = papers
        manifest["total_chunks"] = chunks
        manifest["shards"] = sorted(int(k) for k in (manifest.get("shard_info") or {}) if str(k).isdigit())
        _save_manifest(manifest)
        resume = False

    done = 0
    if resume:
        done = sum(
            int((manifest.get("shard_info") or {}).get(str(s), {}).get("paper_count") or 0)
            for s in _kind_shards(manifest, KIND)
        )
    remaining = rows[done:]
    if not remaining:
        safe_print("PMID 全文升级分片已全部嵌入")
        return manifest

    next_shard = 0
    if manifest.get("shard_info"):
        next_shard = max(int(k) for k in manifest["shard_info"] if str(k).isdigit()) + 1

    embedding = BgeEmbedding(EMBEDDING_MODEL_PATH)
    for i in range(0, len(remaining), EXIST_ENO_SHARD_PAPERS):
        batch = remaining[i : i + EXIST_ENO_SHARD_PAPERS]
        shard_id = next_shard + (i // EXIST_ENO_SHARD_PAPERS)
        docs: list[Document] = []
        for rec in batch:
            rec = dict(rec)
            rec["corpus_source"] = "pmid_fulltext_gold"
            for ch in split_record_to_field_chunks(
                rec,
                chunk_size=PUBMED_YES_CHUNK_SIZE,
                overlap=PUBMED_YES_CHUNK_OVERLAP,
                max_body_chars=EXIST_ENO_MAX_BODY_CHARS,
                max_chunks=EXIST_ENO_MAX_CHUNKS,
                para_max_chars=PUBMED_YES_PARA_MAX_CHARS,
                para_min_chars=PUBMED_YES_PARA_MIN_CHARS,
            ):
                docs.append(
                    Document(
                        page_content=ch["text"],
                        metadata={
                            "pmid": ch.get("pmid") or rec.get("pmid") or "",
                            "entry_no": ch.get("entry_no") or "",
                            "doc_id": ch.get("doc_id") or f"pmid:{rec.get('pmid')}",
                            "title_zh": ch.get("title_zh"),
                            "has_fulltext": True,
                            "chunk_index": ch.get("chunk_index"),
                            "field": ch.get("field"),
                            "source": "pmid_fulltext_gold",
                        },
                    )
                )
        safe_print(f"  shard_{shard_id:04d}: {len(batch)} 篇 → {len(docs)} chunks（PMID全文）…")
        db_vs = FAISS.from_documents(docs, embedding)
        sd = Path(FAISS_PUBMED_YES_DIR) / f"shard_{shard_id:04d}"
        sd.mkdir(parents=True, exist_ok=True)
        db_vs.save_local(str(sd))
        manifest.setdefault("shard_info", {})[str(shard_id)] = {
            "paper_count": len(batch),
            "chunk_count": len(docs),
            "kind": KIND,
            "first_pmid": batch[0].get("pmid"),
            "last_pmid": batch[-1].get("pmid"),
        }
        manifest["shards"] = sorted(int(k) for k in manifest["shard_info"] if str(k).isdigit())
        manifest["total_papers"] = int(manifest.get("total_papers") or 0) + len(batch)
        # 注意：total_papers 会重复计数同一 PMID 的摘要片+全文片；另记 unique 指标
        manifest["total_chunks"] = int(manifest.get("total_chunks") or 0) + len(docs)
        manifest["pmid_fulltext_papers"] = int(manifest.get("pmid_fulltext_papers") or 0) + len(batch)
        manifest["chunking"] = {
            "mode": "paragraph_field_aware",
            "includes": ["pubmed_pmid", "exist_eno", "pmid_fulltext"],
            "rerank": "hybrid_or_cross_encoder",
            "retrieve_then_rerank": "200->10",
        }
        _save_manifest(manifest)
        safe_print(f"  已写入 {sd}")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=str, default=str(DEFAULT_CORPUS))
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    embed(
        corpus=Path(args.corpus),
        resume=not args.no_resume and not args.reset,
        reset=args.reset,
        limit=args.limit,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
