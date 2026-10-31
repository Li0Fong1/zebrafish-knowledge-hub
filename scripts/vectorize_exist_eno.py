# -*- coding: utf-8 -*-
"""
将 exist_eno_corpus（无 PMID 的 pdf库/已存在表）嵌入到同一 FAISS_PUBMED_YES 目录，
追加 shard（不覆盖已有 PMID 分片）。

用法：
  python -m scripts.build_exist_eno_corpus
  python -m scripts.vectorize_exist_eno --resume
  python -m scripts.vectorize_exist_eno --reset-eno-shards
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
    EXIST_ENO_CORPUS_JSONL,
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


def _load_manifest() -> dict:
    if MANIFEST.is_file():
        return json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    return {"shards": [], "shard_info": {}, "total_papers": 0, "total_chunks": 0}


def _save_manifest(m: dict) -> None:
    Path(FAISS_PUBMED_YES_DIR).mkdir(parents=True, exist_ok=True)
    m["updated_at"] = datetime.now().isoformat(timespec="seconds")
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_corpus(limit: int = 0) -> list[dict]:
    path = Path(EXIST_ENO_CORPUS_JSONL)
    if not path.is_file():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    return rows


def _eno_shard_ids(manifest: dict) -> list[int]:
    info = manifest.get("shard_info") or {}
    out = []
    for k, v in info.items():
        if isinstance(v, dict) and v.get("kind") == "exist_eno":
            out.append(int(k))
    return sorted(out)


def embed_eno(*, resume: bool = True, limit: int = 0, reset_eno: bool = False) -> dict:
    corpus = _load_corpus(limit=limit)
    if not corpus:
        safe_print("ENO 语料为空，请先: python -m scripts.build_exist_eno_corpus")
        return {}

    manifest = _load_manifest()
    if reset_eno:
        for sid in _eno_shard_ids(manifest):
            sd = Path(FAISS_PUBMED_YES_DIR) / f"shard_{sid:04d}"
            if sd.is_dir():
                shutil.rmtree(sd, ignore_errors=True)
            (manifest.get("shard_info") or {}).pop(str(sid), None)
        # 重算 totals 仅保留非 eno
        papers = chunks = 0
        for k, v in list((manifest.get("shard_info") or {}).items()):
            if isinstance(v, dict) and v.get("kind") == "exist_eno":
                continue
            papers += int(v.get("paper_count") or 0)
            chunks += int(v.get("chunk_count") or 0)
        manifest["total_papers"] = papers
        manifest["total_chunks"] = chunks
        manifest["shards"] = sorted(int(k) for k in (manifest.get("shard_info") or {}) if str(k).isdigit())
        _save_manifest(manifest)
        resume = False

    existing_eno = _eno_shard_ids(manifest)
    start_local = 0
    if resume and existing_eno:
        # 已完成的 eno shard 对应的论文数
        done_papers = sum(
            int((manifest.get("shard_info") or {}).get(str(s), {}).get("paper_count") or 0)
            for s in existing_eno
        )
        start_local = done_papers
        safe_print(f"resume：已嵌入 ENO {done_papers} 篇，从偏移 {start_local} 继续")

    remaining = corpus[start_local:]
    if not remaining:
        safe_print("ENO 已全部嵌入")
        return manifest

    next_shard = 0
    if manifest.get("shard_info"):
        next_shard = max(int(k) for k in manifest["shard_info"] if str(k).isdigit()) + 1
    elif manifest.get("shards"):
        next_shard = max(int(x) for x in manifest["shards"]) + 1

    embedding = BgeEmbedding(EMBEDDING_MODEL_PATH)
    # 16GB 追加 ENO：默认每片 50 篇；EXIST_ENO_RUNTIME_SHARD 可覆盖
    shard_size = max(
        10,
        int(os.getenv("EXIST_ENO_RUNTIME_SHARD", str(min(int(EXIST_ENO_SHARD_PAPERS), 50)))),
    )
    use_sections = str(os.environ.get("PUBMED_YES_SECTION_CHUNKING", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    total_new_papers = 0
    total_new_chunks = 0
    safe_print(
        f"ENO→{FAISS_PUBMED_YES_DIR} model={EMBEDDING_MODEL_PATH} "
        f"shard_size={shard_size} sections={use_sections}"
    )

    for i in range(0, len(remaining), shard_size):
        batch = remaining[i : i + shard_size]
        shard_id = next_shard + (i // shard_size)
        docs: list[Document] = []
        for rec in batch:
            chunks = split_record_to_field_chunks(
                rec,
                chunk_size=PUBMED_YES_CHUNK_SIZE,
                overlap=PUBMED_YES_CHUNK_OVERLAP,
                max_body_chars=EXIST_ENO_MAX_BODY_CHARS,
                max_chunks=EXIST_ENO_MAX_CHUNKS,
                para_max_chars=PUBMED_YES_PARA_MAX_CHARS,
                para_min_chars=PUBMED_YES_PARA_MIN_CHARS,
                use_sections=use_sections,
            )
            for ch in chunks:
                docs.append(
                    Document(
                        page_content=ch["text"],
                        metadata={
                            "pmid": ch.get("pmid") or "",
                            "entry_no": ch.get("entry_no") or "",
                            "doc_id": ch.get("doc_id") or "",
                            "title_zh": ch.get("title_zh"),
                            "has_fulltext": bool(ch.get("has_fulltext")),
                            "chunk_index": ch.get("chunk_index"),
                            "field": ch.get("field"),
                            "source": ch.get("source") or "exist_eno",
                        },
                    )
                )
        safe_print(f"  shard_{shard_id:04d}: {len(batch)} 篇 → {len(docs)} chunks，嵌入中…")
        db_vs = FAISS.from_documents(docs, embedding)
        sd = Path(FAISS_PUBMED_YES_DIR) / f"shard_{shard_id:04d}"
        sd.mkdir(parents=True, exist_ok=True)
        db_vs.save_local(str(sd))

        manifest.setdefault("shard_info", {})[str(shard_id)] = {
            "paper_count": len(batch),
            "chunk_count": len(docs),
            "kind": "exist_eno",
            "first_doc": batch[0].get("doc_id"),
            "last_doc": batch[-1].get("doc_id"),
        }
        manifest["shards"] = sorted(int(k) for k in manifest["shard_info"] if str(k).isdigit())
        manifest["total_papers"] = int(manifest.get("total_papers") or 0) + len(batch)
        manifest["total_chunks"] = int(manifest.get("total_chunks") or 0) + len(docs)
        manifest["eno_papers"] = int(manifest.get("eno_papers") or 0) + len(batch)
        manifest["eno_chunks"] = int(manifest.get("eno_chunks") or 0) + len(docs)
        manifest["chunking"] = {
            "mode": "section" if use_sections else "field_aware",
            "includes": ["pubmed_pmid", "exist_eno"],
            "embedding_model": str(EMBEDDING_MODEL_PATH),
        }
        _save_manifest(manifest)
        total_new_papers += len(batch)
        total_new_chunks += len(docs)
        safe_print(
            f"  已写入 {sd}（本批累计 +{total_new_papers} 篇 / +{total_new_chunks} chunks；"
            f"库内合计 {manifest['total_papers']} 篇 / {manifest['total_chunks']} chunks）"
        )

    safe_print(
        f"[完成] ENO 向量化 +{total_new_papers} 篇 / +{total_new_chunks} chunks → {FAISS_PUBMED_YES_DIR}"
    )
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true", default=True)
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reset-eno-shards", action="store_true")
    ap.add_argument("--build-first", action="store_true", help="先构建语料再嵌入")
    args = ap.parse_args()
    if args.build_first:
        from scripts.build_exist_eno_corpus import build

        build(limit=args.limit, resume=not args.no_resume)
    embed_eno(
        resume=not args.no_resume and not args.reset_eno_shards,
        limit=args.limit,
        reset_eno=args.reset_eno_shards,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
