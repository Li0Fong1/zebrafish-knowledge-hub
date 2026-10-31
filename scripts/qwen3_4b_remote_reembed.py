# -*- coding: utf-8 -*-
"""Qwen3-Embedding-4B 旁路下载 + 全库重嵌（Linux 服务器）。

硬约束：
- 不覆盖 gte 索引目录
- Hub 不自动切换
- 可断点续跑；OOM 自动降 batch；显存不足则等待
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("FAISS_DISABLE_AVX2", "1")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

HOME = Path(os.path.expanduser("~"))
BASE = Path(os.environ.get("ZEBRA_BASE", str(HOME / "zebrafish_gene_qa")))
RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", str(HOME / "zebrafish_runtime")))
MODEL_DIR = Path(os.environ.get("QWEN3_4B_DIR", str(RUNTIME / "models" / "Qwen3-Embedding-4B")))
OUT_DIR = Path(
    os.environ.get(
        "FAISS_QWEN3_4B_DIR",
        str(RUNTIME / "faiss_pubmed_yes_qwen3_4b_v1"),
    )
)
CORPUS_PMID = BASE / "data" / "literature" / "pubmed_yes_corpus.jsonl"
CORPUS_ENO = BASE / "data" / "literature" / "exist_eno_corpus.jsonl"
LOG = RUNTIME / "logs" / "qwen3_4b_reembed.log"
STATUS = RUNTIME / "logs" / "qwen3_4b_status.json"
HF_ID = os.environ.get("QWEN3_4B_HF_ID", "Qwen/Qwen3-Embedding-4B")
EMBED_DIM = int(os.environ.get("QWEN3_4B_DIM", "2048"))
SHARD_PAPERS = int(os.environ.get("QWEN3_4B_SHARD_PAPERS", "20"))
EMBED_BATCH = int(os.environ.get("QWEN3_4B_EMBED_BATCH", "2"))
MAX_SEQ = int(os.environ.get("QWEN3_4B_MAX_SEQ", "512"))
MIN_FREE_MB = int(os.environ.get("QWEN3_4B_MIN_FREE_MB", "12000"))
INDEX_VERSION = "qwen3_4b_v1"


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_status(**kw) -> None:
    cur = {}
    if STATUS.is_file():
        try:
            cur = json.loads(STATUS.read_text(encoding="utf-8"))
        except Exception:
            cur = {}
    cur.update(kw)
    cur["updated_at"] = datetime.now().isoformat(timespec="seconds")
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")


def gpu_free_mb() -> list[int]:
    import subprocess

    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
        return [int(x.strip()) for x in out.strip().splitlines() if x.strip()]
    except Exception:
        return []


def pick_gpu() -> int | None:
    frees = gpu_free_mb()
    for i, mb in enumerate(frees):
        if mb >= MIN_FREE_MB:
            return i
    return None


def _model_weights_ok(model_dir: Path) -> bool:
    if not (model_dir / "config.json").is_file():
        return False
    if any(model_dir.glob("*.safetensors")) or any(model_dir.glob("pytorch_model*.bin")):
        idx = model_dir / "model.safetensors.index.json"
        if idx.is_file():
            try:
                meta = json.loads(idx.read_text(encoding="utf-8"))
                files = sorted(set((meta.get("weight_map") or {}).values()))
                return bool(files) and all((model_dir / f).is_file() for f in files)
            except Exception:
                return False
        return True
    return False


def download_model() -> None:
    if _model_weights_ok(MODEL_DIR):
        log(f"model already present: {MODEL_DIR}")
        write_status(phase="model_ready", model=str(MODEL_DIR))
        return
    MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading {HF_ID} -> {MODEL_DIR}")
    write_status(phase="downloading_model", hf_id=HF_ID, model=str(MODEL_DIR))
    try:
        from modelscope.hub.snapshot_download import snapshot_download as ms_dl

        ms_dl("Qwen/Qwen3-Embedding-4B", local_dir=str(MODEL_DIR))
    except Exception as e:
        log(f"modelscope failed ({e}); try huggingface_hub")
        from huggingface_hub import snapshot_download

        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        try:
            snapshot_download(repo_id=HF_ID, local_dir=str(MODEL_DIR), resume_download=True)
        except TypeError:
            snapshot_download(repo_id=HF_ID, local_dir=str(MODEL_DIR))
    if not _model_weights_ok(MODEL_DIR):
        raise SystemExit("download finished but model weights incomplete")
    log("download done")
    write_status(phase="model_ready", model=str(MODEL_DIR))


def wait_gpu(max_hours: float = 168) -> int:
    t0 = time.time()
    while True:
        g = pick_gpu()
        frees = gpu_free_mb()
        write_status(phase="wait_gpu", free_mb=frees, need_mb=MIN_FREE_MB, picked=g)
        if g is not None:
            log(f"GPU {g} free enough: {frees}")
            return g
        if (time.time() - t0) > max_hours * 3600:
            raise SystemExit("GPU wait timeout")
        log(f"wait GPU free>={MIN_FREE_MB}MB, now={frees}")
        time.sleep(60)


class QwenEmbed:
    def __init__(self, model_path: str, device: str, dim: int):
        from sentence_transformers import SentenceTransformer
        import torch

        self.batch = max(1, EMBED_BATCH)
        self.dim = dim
        kwargs = {
            "trust_remote_code": True,
            "device": device,
            "model_kwargs": {"torch_dtype": torch.float16} if device.startswith("cuda") else {},
        }
        try:
            self.model = SentenceTransformer(model_path, truncate_dim=dim, **kwargs)
        except TypeError:
            self.model = SentenceTransformer(model_path, **kwargs)
        try:
            self.model.max_seq_length = MAX_SEQ
        except Exception:
            pass
        log(f"loaded embed model on {device}, target_dim={dim}, max_seq={MAX_SEQ}, batch={self.batch}")

    def _encode_once(self, texts, batch_size: int):
        try:
            vecs = self.model.encode(
                texts,
                normalize_embeddings=True,
                batch_size=batch_size,
                truncate_dim=self.dim,
                show_progress_bar=False,
            )
        except TypeError:
            import numpy as np

            vecs = self.model.encode(
                texts,
                normalize_embeddings=True,
                batch_size=batch_size,
                show_progress_bar=False,
            )
            vecs = np.asarray(vecs)[:, : self.dim]
            norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
            vecs = vecs / norms
        return vecs

    def embed_documents(self, texts):
        import torch

        bs = self.batch
        while True:
            try:
                vecs = self._encode_once(texts, bs)
                return vecs.tolist()
            except torch.cuda.OutOfMemoryError:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                if bs <= 1:
                    # last resort: one-by-one
                    if len(texts) == 1:
                        raise
                    log(f"OOM at batch={bs}; encode one-by-one n={len(texts)}")
                    out = []
                    for t in texts:
                        out.extend(self.embed_documents([t]))
                    return out
                bs = max(1, bs // 2)
                self.batch = bs
                log(f"OOM: drop embed batch -> {bs}")

    def embed_query(self, text):
        return self.embed_documents([text])[0]


def _iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _count_jsonl(path: Path) -> int:
    n = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def _load_manifest() -> dict:
    mf = OUT_DIR / "manifest.json"
    if mf.is_file():
        return json.loads(mf.read_text(encoding="utf-8"))
    return {
        "shards": [],
        "shard_info": {},
        "total_papers": 0,
        "total_chunks": 0,
        "index_version": INDEX_VERSION,
        "embedding_model": str(MODEL_DIR),
        "embedding_dim": EMBED_DIM,
        "status": "building",
        "kind_order": ["pmid_fulltext_qwen3_4b", "exist_eno_qwen3_4b"],
    }


def _save_manifest(m: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    m["updated_at"] = datetime.now().isoformat(timespec="seconds")
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def embed_corpus(corpus: Path, *, kind: str, embedding, resume: bool = True) -> None:
    sys.path.insert(0, str(BASE))
    sys.path.insert(0, str(BASE / "core"))
    os.chdir(BASE)
    from core.entry import bootstrap_app

    bootstrap_app()
    from langchain_core.embeddings import Embeddings

    class _Emb(Embeddings):
        def embed_documents(self, texts):
            return embedding.embed_documents(texts)

        def embed_query(self, text):
            return embedding.embed_query(text)

        # langchain_community FAISS.add_texts may call embedding_function(text)
        def __call__(self, text):
            if isinstance(text, str):
                return self.embed_query(text)
            return self.embed_documents(list(text))

    emb = _Emb()
    manifest = _load_manifest()
    done_kind = sum(
        int(v.get("paper_count") or 0)
        for v in (manifest.get("shard_info") or {}).values()
        if isinstance(v, dict) and v.get("kind") == kind
    )
    total = _count_jsonl(corpus)
    log(f"corpus {corpus.name} total={total} already_kind={done_kind} kind={kind}")
    if resume and done_kind >= total:
        log(f"kind {kind} already complete")
        return

    next_shard = 0
    if manifest.get("shard_info"):
        next_shard = max(int(k) for k in manifest["shard_info"] if str(k).isdigit()) + 1

    batch = []
    skipped = 0
    for rec in _iter_jsonl(corpus):
        if resume and skipped < done_kind:
            skipped += 1
            continue
        batch.append(rec)
        if len(batch) < SHARD_PAPERS:
            continue
        _flush(batch, next_shard, kind, emb, manifest)
        next_shard += 1
        batch = []
    if batch:
        _flush(batch, next_shard, kind, emb, manifest)


def _flush(batch, shard_id, kind, emb, manifest):
    import torch
    from langchain_community.vectorstores import FAISS
    from langchain_core.documents import Document
    from rag.pubmed_yes_chunking import split_record_to_field_chunks
    from config import (
        EXIST_ENO_MAX_BODY_CHARS,
        EXIST_ENO_MAX_CHUNKS,
        PUBMED_YES_PARA_MAX_CHARS,
        PUBMED_YES_PARA_MIN_CHARS,
    )

    docs = []
    for rec in batch:
        for ch in split_record_to_field_chunks(
            rec,
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
                        "pmid": ch.get("pmid") or "",
                        "entry_no": ch.get("entry_no") or "",
                        "title_zh": ch.get("title_zh"),
                        "has_fulltext": bool(ch.get("has_fulltext")),
                        "chunk_index": ch.get("chunk_index"),
                        "field": ch.get("field"),
                        "section": ch.get("section") or ch.get("field"),
                        "source": ch.get("source") or "pubmed_yes",
                        "index_version": INDEX_VERSION,
                    },
                )
            )
    log(f"shard_{shard_id:04d} papers={len(batch)} chunks={len(docs)} kind={kind}")
    write_status(
        phase="embedding",
        shard=shard_id,
        papers_in_shard=len(batch),
        chunks_in_shard=len(docs),
        kind=kind,
        total_papers=manifest.get("total_papers"),
        dim=EMBED_DIM,
    )
    # Pre-encode then from_embeddings — avoids langchain calling embedding as callable
    texts = [d.page_content for d in docs]
    metas = [d.metadata for d in docs]
    vecs: list = []
    micro = max(8, int(os.environ.get("QWEN3_4B_DOC_MICRO", "48")))
    for i in range(0, len(texts), micro):
        vecs.extend(emb.embed_documents(texts[i : i + micro]))
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    text_emb = list(zip(texts, vecs))
    vs = FAISS.from_embeddings(text_embeddings=text_emb, embedding=emb, metadatas=metas)
    sd = OUT_DIR / f"shard_{shard_id:04d}"
    sd.mkdir(parents=True, exist_ok=True)
    vs.save_local(str(sd))
    first = batch[0]
    last = batch[-1]
    info = {
        "paper_count": len(batch),
        "chunk_count": len(docs),
        "kind": kind,
        "first_pmid": str(first.get("pmid") or ""),
        "last_pmid": str(last.get("pmid") or ""),
        "first_doc": f"eno:{first.get('entry_no')}" if first.get("entry_no") else "",
        "last_doc": f"eno:{last.get('entry_no')}" if last.get("entry_no") else "",
    }
    shards = list(manifest.get("shards") or [])
    if shard_id not in shards:
        shards.append(shard_id)
    manifest["shards"] = shards
    manifest.setdefault("shard_info", {})[str(shard_id)] = info
    manifest["total_papers"] = int(manifest.get("total_papers") or 0) + len(batch)
    manifest["total_chunks"] = int(manifest.get("total_chunks") or 0) + len(docs)
    manifest["embedding_model"] = str(MODEL_DIR)
    manifest["embedding_dim"] = EMBED_DIM
    manifest["index_version"] = INDEX_VERSION
    manifest["status"] = "building"
    _save_manifest(manifest)
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    log(
        f"wrote shard_{shard_id:04d} cumulative papers={manifest['total_papers']} chunks={manifest['total_chunks']}"
    )


def main() -> int:
    import torch

    LOG.parent.mkdir(parents=True, exist_ok=True)
    log("=== qwen3-4b reembed start ===")
    write_status(phase="start", out_dir=str(OUT_DIR), dim=EMBED_DIM)
    gte = RUNTIME / "faiss_pubmed_yes_gte_mbase_v1"
    if OUT_DIR.resolve() == gte.resolve():
        raise SystemExit("refuse overwrite gte index")

    download_model()

    emb = None
    gpu = None
    for attempt in range(1, 500):
        gpu = wait_gpu()
        # re-check after short settle (race with other users)
        time.sleep(3)
        frees = gpu_free_mb()
        if gpu >= len(frees) or frees[gpu] < MIN_FREE_MB:
            log(f"GPU {gpu} lost free after settle: {frees}; wait again")
            continue
        os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu)
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            emb = QwenEmbed(str(MODEL_DIR), device="cuda:0", dim=EMBED_DIM)
            break
        except torch.cuda.OutOfMemoryError:
            log(f"load OOM on GPU {gpu} attempt={attempt}; frees={frees}")
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            emb = None
            time.sleep(30)
            continue
    if emb is None:
        raise SystemExit("failed to load model onto GPU")

    probe = emb.embed_query("zebrafish gene")
    if len(probe) != EMBED_DIM:
        log(f"WARN probe dim={len(probe)} expected={EMBED_DIM}; continue with actual={len(probe)}")
    write_status(phase="embedding", probe_dim=len(probe), dim=EMBED_DIM, gpu=gpu)

    if CORPUS_PMID.is_file():
        embed_corpus(CORPUS_PMID, kind="pmid_fulltext_qwen3_4b", embedding=emb, resume=True)
    else:
        log(f"missing {CORPUS_PMID}")
    if CORPUS_ENO.is_file():
        embed_corpus(CORPUS_ENO, kind="exist_eno_qwen3_4b", embedding=emb, resume=True)
    else:
        log(f"missing {CORPUS_ENO}")

    m = _load_manifest()
    m["status"] = "ready"
    _save_manifest(m)
    write_status(phase="done", manifest_papers=m.get("total_papers"), chunks=m.get("total_chunks"))
    log("=== qwen3-4b reembed DONE ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
