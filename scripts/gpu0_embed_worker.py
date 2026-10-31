# -*- coding: utf-8 -*-
"""
GPU0 高利用率常驻嵌入工人。

默认几乎不停编码（SLEEP≈0），大批次长文本，把 V100 算力打起来。
仍保留：低内存暂停、不写 FAISS、不碰 GPU1。
"""
from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("FAISS_DISABLE_AVX2", "1")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
MODEL = Path(os.environ.get("QWEN3_4B_DIR", str(RUNTIME / "models" / "Qwen3-Embedding-4B")))
LOG_DIR = RUNTIME / "logs" / "scheduler"
OUT = LOG_DIR / "query_embed_cache.jsonl"
LOG = LOG_DIR / "gpu0_worker.log"
PIDF = LOG_DIR / "gpu0_worker.pid"
DIM = int(os.environ.get("QWEN3_4B_DIM", "2048"))
BATCH = int(os.environ.get("GPU0_WORKER_BATCH", "48"))
MICRO = int(os.environ.get("GPU0_WORKER_MICRO", "16"))
SLEEP = float(os.environ.get("GPU0_WORKER_SLEEP", "0.05"))
TARGET_CHARS = int(os.environ.get("GPU0_WORKER_CHARS", "1200"))


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def mem_avail_gib() -> float:
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) / (1024 * 1024)
    except Exception:
        return 999.0
    return 999.0


def load_texts() -> list[str]:
    texts: list[str] = []
    corp = RUNTIME / "logs" / "scihub_gold_vectorize_corpus.jsonl"
    if corp.is_file():
        lines = corp.read_text(encoding="utf-8", errors="replace").splitlines()
        random.shuffle(lines)
        for ln in lines[:800]:
            try:
                o = json.loads(ln)
            except Exception:
                continue
            body = (o.get("body_text") or o.get("title") or "").strip()
            if len(body) < 80:
                continue
            # cut windows to keep seq busy
            for off in (0, 800, 1600, 3200):
                chunk = body[off : off + TARGET_CHARS]
                if len(chunk) >= 200:
                    texts.append(chunk)
    seeds = [
        "zebrafish sox2 transcriptional regulation in neural development and regeneration pathways",
        "danio rerio crispr cas9 knockout phenotype screening cardiac regeneration macrophage",
        "斑马鱼心脏再生心肌细胞增殖炎症反应与纤维化调控机制研究摘要与方法讨论",
    ]
    while len(texts) < 64:
        texts.extend(seeds)
    random.shuffle(texts)
    return texts


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PIDF.write_text(str(os.getpid()), encoding="utf-8")
    _log(
        f"gpu0 HIGH-UTIL worker start batch={BATCH} micro={MICRO} sleep={SLEEP} "
        f"visible={os.environ.get('CUDA_VISIBLE_DEVICES')}"
    )

    import torch
    from sentence_transformers import SentenceTransformer

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    kwargs = {
        "trust_remote_code": True,
        "device": device,
        "model_kwargs": {"torch_dtype": torch.float16} if device.startswith("cuda") else {},
    }
    try:
        model = SentenceTransformer(str(MODEL), truncate_dim=DIM, **kwargs)
    except TypeError:
        model = SentenceTransformer(str(MODEL), **kwargs)
    try:
        model.max_seq_length = 512
    except Exception:
        pass
    _log(f"model loaded device={device}")

    texts = load_texts()
    _log(f"text pool n={len(texts)}")
    i = 0
    t0 = time.time()
    encoded = 0
    micro = MICRO
    while True:
        if mem_avail_gib() < 16:
            _log("pause low mem")
            time.sleep(20)
            continue
        batch = [texts[(i + j) % len(texts)] for j in range(BATCH)]
        i += BATCH
        try:
            try:
                vecs = model.encode(
                    batch,
                    normalize_embeddings=True,
                    batch_size=micro,
                    truncate_dim=DIM,
                    show_progress_bar=False,
                )
            except TypeError:
                vecs = model.encode(
                    batch,
                    normalize_embeddings=True,
                    batch_size=micro,
                    show_progress_bar=False,
                )
            encoded += len(batch)
            if encoded % (BATCH * 10) == 0:
                elapsed = max(0.1, time.time() - t0)
                _log(f"encoded={encoded} rate={encoded/elapsed:.1f}/s dim={len(vecs[0])} micro={micro}")
                with OUT.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "encoded": encoded,
                                "rate": round(encoded / elapsed, 2),
                                "ts": datetime.now().isoformat(timespec="seconds"),
                            }
                        )
                        + "\n"
                    )
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower():
                micro = max(2, micro // 2)
                _log(f"OOM -> shrink micro to {micro}; {exc}")
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                time.sleep(2)
                continue
            _log(f"err: {exc}")
            time.sleep(2)
            continue
        except Exception as exc:
            _log(f"err: {exc}")
            time.sleep(2)
            continue
        if SLEEP > 0:
            time.sleep(SLEEP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
