# -*- coding: utf-8 -*-
"""增量文献的「解析 → 分块 → 向量化 → 落盘」离线工序。

为什么要独立成一个进程
----------------------
线上 `.env` 里 `SKIP_KNOWLEDGE_FAISS_REBUILD=1`：主知识库小索引是 768 维
（gte-multilingual-base），而 `EMBEDDING_MODEL_PATH` 指向 Qwen3-Embedding-4B
（2048 维）。若在 API 进程里直接 append，维度不匹配会写坏索引；把 4B 模型载进
常驻进程又会吃掉几 GB 内存。所以旧的 weekly 任务干脆整段跳过向量化
（日志里就是 `skip_vector / chunks 0`），新文献只留下文本和 MySQL 记录。

这里把这一步补齐，且**不占 API 进程内存**：
  · 由 hub_maintenance 在文献入库完成后用 subprocess 拉起，独立进程跑完即退；
  · 用与主索引一致的 gte-multilingual-base（768 维）做真实稠密向量化；
  · 结果写成独立的增量索引目录 `data/faiss_lit_delta/`（index.faiss +
    chunks.jsonl + manifest.json），不改动、不污染主索引；
  · 文本同时留在 knowledge.d，API 进程里的关键词段落表会自动热加载（见
    rag_builder._maybe_reload_paragraphs），所以新文献**当次即可被检索到**。

用法：
  python -m scripts.literature_vector_delta --files a.txt b.txt
  python -m scripts.literature_vector_delta --pending   # 读待处理清单
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import bootstrap  # noqa: F401
from config import _BASE_DIR

DELTA_DIR = Path(os.getenv("LIT_DELTA_DIR") or (Path(_BASE_DIR) / "data" / "faiss_lit_delta"))
PENDING = DELTA_DIR / "pending.jsonl"
MANIFEST = DELTA_DIR / "manifest.json"
MODEL_PATH = os.getenv("LIT_DELTA_MODEL") or str(Path(_BASE_DIR) / "models" / "gte-multilingual-base")
DEVICE = os.getenv("LIT_DELTA_DEVICE", "cpu")
CHUNK_SIZE = int(os.getenv("LIT_DELTA_CHUNK_SIZE", "500"))
CHUNK_OVERLAP = int(os.getenv("LIT_DELTA_CHUNK_OVERLAP", "60"))


def _log(msg: str) -> None:
    print("[lit-delta] %s" % msg, flush=True)


def enqueue(paths) -> int:
    """由 API 进程调用：把新增文本追加到待处理清单（毫秒级，不加载模型）。"""
    DELTA_DIR.mkdir(parents=True, exist_ok=True)
    fresh = [str(p) for p in paths if p and os.path.isfile(str(p))]
    if not fresh:
        return 0
    with PENDING.open("a", encoding="utf-8") as f:
        for p in fresh:
            f.write(json.dumps({"path": p, "ts": time.time()}, ensure_ascii=False) + "\n")
    return len(fresh)


def _read_pending() -> list:
    if not PENDING.is_file():
        return []
    seen, out = set(), []
    for line in PENDING.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            p = (json.loads(line) or {}).get("path")
        except Exception:
            p = None
        if p and p not in seen and os.path.isfile(p):
            seen.add(p)
            out.append(p)
    return out


def _split(paths) -> list:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "；", "，", " "],
    )
    chunks = []
    for p in paths:
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for piece in splitter.split_text(text):
            piece = (piece or "").strip()
            if len(piece) < 20:
                continue
            chunks.append({"text": piece, "source": os.path.basename(p)})
    return chunks


def build(paths) -> dict:
    """真正的向量化工序：分块 → 嵌入 → FAISS 内积索引 → 落盘。"""
    chunks = _split(paths)
    if not chunks:
        return {"ok": False, "reason": "no_chunks", "chunks": 0}
    import numpy as np
    import faiss
    from sentence_transformers import SentenceTransformer

    _log("loading embedding model %s on %s" % (MODEL_PATH, DEVICE))
    model = SentenceTransformer(MODEL_PATH, device=DEVICE, trust_remote_code=True)
    vecs = model.encode(
        [c["text"] for c in chunks], normalize_embeddings=True,
        batch_size=32, show_progress_bar=False,
    )
    arr = np.asarray(vecs, dtype="float32")
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    dim = int(arr.shape[1])
    DELTA_DIR.mkdir(parents=True, exist_ok=True)

    # 已有增量索引则继续追加，保证每周的新文献都累积在同一份向量库里
    idx_path = DELTA_DIR / "index.faiss"
    chunks_path = DELTA_DIR / "chunks.jsonl"
    old_chunks = []
    if idx_path.is_file() and chunks_path.is_file():
        try:
            index = faiss.read_index(str(idx_path))
            if index.d == dim:
                old_chunks = [json.loads(x) for x in chunks_path.read_text(encoding="utf-8").splitlines() if x.strip()]
            else:
                _log("dim changed %s -> %s, rebuild delta from scratch" % (index.d, dim))
                index = faiss.IndexFlatIP(dim)
        except Exception:
            index = faiss.IndexFlatIP(dim)
    else:
        index = faiss.IndexFlatIP(dim)

    index.add(arr)
    faiss.write_index(index, str(idx_path))
    all_chunks = old_chunks + chunks
    with chunks_path.open("w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    manifest = {
        "ok": True,
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": os.path.basename(MODEL_PATH.rstrip("/")),
        "dim": dim,
        "new_chunks": len(chunks),
        "total_chunks": int(index.ntotal),
        "files": len(paths),
        "device": DEVICE,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _log("done: +%d chunks, total %d, dim %d" % (len(chunks), index.ntotal, dim))
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--enqueue", nargs="*", default=None,
                    help="只把文件写进待处理清单，不加载模型")
    args = ap.parse_args(argv)
    if args.enqueue is not None:
        print(json.dumps({"enqueued": enqueue(args.enqueue)}, ensure_ascii=False))
        return 0
    paths = _read_pending() if args.pending else list(args.files or [])
    if not paths:
        print(json.dumps({"ok": False, "reason": "no_files"}, ensure_ascii=False))
        return 0
    try:
        try:
            result = build(paths)
        except Exception as exc:  # noqa: BLE001
            _log("FAILED %s: %s" % (type(exc).__name__, exc))
            result = {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)[:200]}
    finally:
        # 无论成败都释放互斥目录，否则下次增量任务会一直以为有进程在跑
        lock = DELTA_DIR / "worker.lock"
        if lock.is_dir():
            try:
                lock.rmdir()
            except OSError:
                pass
    # 成功后清空待处理清单，避免下周重复向量化同一批文件
    if result.get("ok") and args.pending and PENDING.is_file():
        try:
            PENDING.unlink()
        except OSError:
            pass
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
