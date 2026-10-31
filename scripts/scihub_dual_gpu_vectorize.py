# -*- coding: utf-8 -*-
"""
双 GPU 并行增量向量化（Qwen3-4B 完整标准）。

用法：
  # 先构建待嵌语料
  python -u -m scripts.scihub_dual_gpu_vectorize --build-only

  # 两卡各跑一个分区（由 shell 分别设 CUDA_VISIBLE_DEVICES）
  CUDA_VISIBLE_DEVICES=0 python -u -m scripts.scihub_dual_gpu_vectorize --partition 0/2 --gpu-label 0
  CUDA_VISIBLE_DEVICES=1 python -u -m scripts.scihub_dual_gpu_vectorize --partition 1/2 --gpu-label 1
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.scihub_post_gold_pipeline import (  # noqa: E402
    CORPUS,
    DONE_IDS,
    FAISS_DIR,
    FLOOR,
    GTE_DIR,
    INDEX_VERSION,
    KIND,
    LOG_DIR,
    MODEL_DIR,
    REPAIR_STATE,
    SHARD_PAPERS,
    EMBED_DIM,
    _load_json,
    _log,
    _save_json,
    inbox_entry_nos,
    load_rec,
    parse_log_rows,
    rec_to_corpus_row,
)

LOCK = LOG_DIR / "scihub_dual_gpu.lock"
ALLOC = LOG_DIR / "scihub_shard_alloc.json"
STATUS = LOG_DIR / "scihub_dual_gpu_status.json"


def _make_lock():
    class _L:
        def __enter__(self):
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.fh = open(LOCK, "a+", encoding="utf-8")
            if os.name != "nt":
                import fcntl

                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
            return self

        def __exit__(self, *exc):
            if os.name != "nt":
                import fcntl

                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            self.fh.close()

    return _L()


def build_pending_corpus(*, limit: int = 0) -> dict[str, Any]:
    done = set(_load_json(DONE_IDS, []))
    rows_log = parse_log_rows()
    enos = set(rows_log.keys())
    state = _load_json(REPAIR_STATE, {})
    enos.update(str(x) for x in (state.get("done_gold") or []))
    enos.update(inbox_entry_nos())

    out_rows: list[dict[str, Any]] = []
    skipped_done = 0
    not_gold = 0
    for eno in sorted(enos, key=lambda x: int(x) if x.isdigit() else x):
        if eno in done:
            skipped_done += 1
            continue
        # prefer log-gold first
        row_log = rows_log.get(eno) or {}
        if row_log and not row_log.get("gold"):
            # still try if file is gold after repair
            pass
        rec = load_rec(eno)
        if not rec:
            continue
        row = rec_to_corpus_row(rec)
        if not row:
            not_gold += 1
            continue
        out_rows.append(row)
        if limit and len(out_rows) >= limit:
            break

    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    with CORPUS.open("w", encoding="utf-8") as fh:
        for r in out_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # init shard allocator from current manifest
    mf = FAISS_DIR / "manifest.json"
    next_shard = 0
    if mf.is_file():
        m = json.loads(mf.read_text(encoding="utf-8"))
        if m.get("shard_info"):
            next_shard = max(int(k) for k in m["shard_info"] if str(k).isdigit()) + 1
    with _make_lock():
        _save_json(ALLOC, {"next_shard": next_shard, "updated_at": datetime.now().isoformat(timespec="seconds")})

    summary = {
        "corpus_path": str(CORPUS),
        "n": len(out_rows),
        "skipped_done": skipped_done,
        "not_gold": not_gold,
        "next_shard_start": next_shard,
        "floor": FLOOR,
        "enos_considered": len(enos),
    }
    _log(f"dual build_corpus {summary}")
    STATUS.write_text(json.dumps({"phase": "corpus_ready", **summary, "updated_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _claim_shard() -> int:
    with _make_lock():
        alloc = _load_json(ALLOC, {"next_shard": 0})
        sid = int(alloc.get("next_shard") or 0)
        alloc["next_shard"] = sid + 1
        alloc["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_json(ALLOC, alloc)
        return sid


def _commit_shard(shard_id: int, info: dict[str, Any], paper_ids: list[str]) -> dict[str, Any]:
    mf_path = FAISS_DIR / "manifest.json"
    with _make_lock():
        manifest = json.loads(mf_path.read_text(encoding="utf-8")) if mf_path.is_file() else {
            "shards": [],
            "shard_info": {},
            "total_papers": 0,
            "total_chunks": 0,
        }
        shards = list(manifest.get("shards") or [])
        if shard_id not in shards:
            shards.append(shard_id)
        manifest["shards"] = shards
        manifest.setdefault("shard_info", {})[str(shard_id)] = info
        manifest["total_papers"] = int(manifest.get("total_papers") or 0) + int(info.get("paper_count") or 0)
        manifest["total_chunks"] = int(manifest.get("total_chunks") or 0) + int(info.get("chunk_count") or 0)
        manifest["embedding_model"] = str(MODEL_DIR)
        manifest["embedding_dim"] = EMBED_DIM
        manifest["index_version"] = INDEX_VERSION
        manifest["status"] = "ready"
        manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
        manifest["chunking"] = {
            "mode": "paragraph_field_aware",
            "includes": ["pmid_fulltext_qwen3_4b", "exist_eno_qwen3_4b", KIND],
            "dual_gpu": True,
        }
        mf_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        done_ids = set(_load_json(DONE_IDS, []))
        done_ids.update(paper_ids)
        _save_json(DONE_IDS, sorted(done_ids))
        return manifest


def run_partition(*, part: int, parts: int, gpu_label: int, limit: int = 0) -> dict[str, Any]:
    if FAISS_DIR.resolve() == GTE_DIR.resolve():
        raise SystemExit("refuse overwrite gte index")
    if not CORPUS.is_file():
        raise SystemExit(f"missing corpus {CORPUS}; run --build-only first")

    import torch
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document
    from langchain_core.embeddings import Embeddings
    from sentence_transformers import SentenceTransformer

    from config import (
        EXIST_ENO_MAX_BODY_CHARS,
        EXIST_ENO_MAX_CHUNKS,
        PUBMED_YES_CHUNK_OVERLAP,
        PUBMED_YES_CHUNK_SIZE,
        PUBMED_YES_PARA_MAX_CHARS,
        PUBMED_YES_PARA_MIN_CHARS,
    )
    from rag.pubmed_yes_chunking import split_record_to_field_chunks

    # CUDA_VISIBLE_DEVICES already set by launcher → always cuda:0 inside process
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    _log(f"partition {part}/{parts} gpu_label={gpu_label} device={device} visible={os.environ.get('CUDA_VISIBLE_DEVICES')}")

    class QwenEmb(Embeddings):
        def __init__(self) -> None:
            kwargs = {
                "trust_remote_code": True,
                "device": device,
                "model_kwargs": {"torch_dtype": torch.float16} if device.startswith("cuda") else {},
            }
            try:
                self.model = SentenceTransformer(str(MODEL_DIR), truncate_dim=EMBED_DIM, **kwargs)
            except TypeError:
                self.model = SentenceTransformer(str(MODEL_DIR), **kwargs)
            try:
                self.model.max_seq_length = int(os.environ.get("QWEN3_4B_MAX_SEQ", "512"))
            except Exception:
                pass
            # Full V100 free → larger batch
            self.batch = max(1, int(os.environ.get("QWEN3_4B_EMBED_BATCH", "8")))

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            bs = self.batch
            while True:
                try:
                    try:
                        vecs = self.model.encode(
                            texts,
                            normalize_embeddings=True,
                            batch_size=bs,
                            truncate_dim=EMBED_DIM,
                            show_progress_bar=False,
                        )
                    except TypeError:
                        import numpy as np

                        vecs = self.model.encode(
                            texts,
                            normalize_embeddings=True,
                            batch_size=bs,
                            show_progress_bar=False,
                        )
                        vecs = np.asarray(vecs)[:, :EMBED_DIM]
                        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
                        vecs = vecs / norms
                    return vecs.tolist()
                except torch.cuda.OutOfMemoryError:
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    if bs <= 1:
                        out: list[list[float]] = []
                        for t in texts:
                            out.extend(self.embed_documents([t]))
                        return out
                    bs = max(1, bs // 2)
                    self.batch = bs
                    _log(f"gpu{gpu_label} OOM drop batch -> {bs}")

        def embed_query(self, text: str) -> list[float]:
            return self.embed_documents([text])[0]

        def __call__(self, text):
            if isinstance(text, str):
                return self.embed_query(text)
            return self.embed_documents(list(text))

    emb = QwenEmb()
    probe = emb.embed_query("zebrafish gene")
    _log(f"gpu{gpu_label} probe_dim={len(probe)}")

    all_rows: list[dict[str, Any]] = []
    with CORPUS.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                all_rows.append(json.loads(line))

    done_ids = set(_load_json(DONE_IDS, []))
    all_rows = [r for r in all_rows if str(r.get("entry_no") or r.get("pmid") or "") not in done_ids]
    # stable partition
    rows = [r for i, r in enumerate(all_rows) if i % parts == part]
    if limit:
        rows = rows[:limit]
    _log(f"gpu{gpu_label} rows={len(rows)} / pending_total={len(all_rows)}")

    total_new = 0
    total_chunks = 0
    for i in range(0, len(rows), SHARD_PAPERS):
        batch = rows[i : i + SHARD_PAPERS]
        shard_id = _claim_shard()
        docs: list[Document] = []
        for rec in batch:
            for ch in split_record_to_field_chunks(
                rec,
                chunk_size=PUBMED_YES_CHUNK_SIZE,
                overlap=PUBMED_YES_CHUNK_OVERLAP,
                max_body_chars=EXIST_ENO_MAX_BODY_CHARS,
                max_chunks=EXIST_ENO_MAX_CHUNKS,
                para_max_chars=PUBMED_YES_PARA_MAX_CHARS,
                para_min_chars=PUBMED_YES_PARA_MIN_CHARS,
                use_sections=True,
            ):
                docs.append(
                    Document(
                        page_content=ch["text"],
                        metadata={
                            "pmid": ch.get("pmid") or rec.get("pmid") or "",
                            "entry_no": ch.get("entry_no") or rec.get("entry_no") or "",
                            "doc_id": rec.get("doc_id") or "",
                            "title_zh": ch.get("title_zh"),
                            "has_fulltext": True,
                            "chunk_index": ch.get("chunk_index"),
                            "field": ch.get("field"),
                            "section": ch.get("section") or ch.get("field"),
                            "source": "scihub_gold",
                            "index_version": INDEX_VERSION,
                            "gpu": gpu_label,
                        },
                    )
                )
        _log(f"gpu{gpu_label} shard_{shard_id:04d} papers={len(batch)} chunks={len(docs)}")
        texts = [d.page_content for d in docs]
        metas = [d.metadata for d in docs]
        vecs: list = []
        micro = max(16, int(os.environ.get("QWEN3_4B_DOC_MICRO", "64")))
        for j in range(0, len(texts), micro):
            vecs.extend(emb.embed_documents(texts[j : j + micro]))
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        vs = FAISS.from_embeddings(text_embeddings=list(zip(texts, vecs)), embedding=emb, metadatas=metas)
        sd = FAISS_DIR / f"shard_{shard_id:04d}"
        sd.mkdir(parents=True, exist_ok=True)
        vs.save_local(str(sd))
        info = {
            "paper_count": len(batch),
            "chunk_count": len(docs),
            "kind": KIND,
            "gpu": gpu_label,
            "partition": f"{part}/{parts}",
            "first_doc": batch[0].get("doc_id"),
            "last_doc": batch[-1].get("doc_id"),
        }
        ids = [str(r.get("entry_no") or r.get("pmid") or "") for r in batch if r.get("entry_no") or r.get("pmid")]
        manifest = _commit_shard(shard_id, info, ids)
        total_new += len(batch)
        total_chunks += len(docs)
        _log(
            f"gpu{gpu_label} wrote {sd} new={total_new}/{len(rows)} "
            f"index_papers={manifest.get('total_papers')} chunks={manifest.get('total_chunks')}"
        )
        STATUS.write_text(
            json.dumps(
                {
                    "phase": "embedding",
                    "gpu": gpu_label,
                    "partition": f"{part}/{parts}",
                    "shard": shard_id,
                    "done_in_partition": total_new,
                    "partition_total": len(rows),
                    "manifest_papers": manifest.get("total_papers"),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    out = {
        "gpu": gpu_label,
        "partition": f"{part}/{parts}",
        "new_papers": total_new,
        "new_chunks": total_chunks,
    }
    _log(f"gpu{gpu_label} partition done {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--partition", default="", help="e.g. 0/2")
    ap.add_argument("--gpu-label", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.build_only:
        build_pending_corpus(limit=args.limit)
        return 0

    if not args.partition or "/" not in args.partition:
        raise SystemExit("need --partition i/n")
    a, b = args.partition.split("/", 1)
    run_partition(part=int(a), parts=int(b), gpu_label=args.gpu_label, limit=args.limit)
    return 0


if __name__ == "__main__":
    # remove broken helper leftover if any
    raise SystemExit(main())
