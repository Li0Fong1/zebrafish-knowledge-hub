# -*- coding: utf-8 -*-
"""CPU 原地替换 FAISS 中 DOI 污染的 title 向量，不重嵌整 shard，避免与 Hub 抢 GPU。"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("FAISS_DISABLE_AVX2", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
_PATCH_DEVICE = os.getenv("PATCH_DEVICE", "cpu")
if not str(_PATCH_DEVICE).startswith("cuda"):
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["EMBEDDING_DEVICE"] = "cpu"
    os.environ["PUBMED_EMBEDDING_DEVICE"] = "cpu"

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "database", ROOT / "llm", ROOT / "rag"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from core.entry import bootstrap_app

bootstrap_app()

import numpy as np
from langchain_community.vectorstores import FAISS

from rag.rag_builder import BgeEmbedding
from config import EMBEDDING_MODEL_PATH

FAISS_DIR = Path(
    os.getenv("FAISS_PUBMED_YES_DIR", "/data/zoujin/deng/zebrafish_runtime/faiss_pubmed_yes_qwen3_4b_v1")
)
MAP = Path(os.getenv("DOI_TITLE_MAP", "/data/zoujin/deng/zebrafish_runtime/logs/doi_title_map.jsonl"))
LOG = Path(os.getenv("TITLE_PATCH_LOG", "/data/zoujin/deng/zebrafish_runtime/logs/_patch_title_cpu.log"))
DOI_RE = re.compile(r"(?i)(?:doi[:\s]*)?(10\.\d{4,9}/[^\s\"'<>]+)")


def is_bad_title(text: str) -> bool:
    low = (text or "").lower()
    if "doi 10." in low or "doi:10." in low or "doi: 10." in low:
        return True
    return bool(re.search(r"10\.\d{4,9}/", low))


def load_titles() -> dict:
    out = {}
    if not MAP.is_file():
        print(f"NO_MAP {MAP}", flush=True)
        return out
    with MAP.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            eno = str(rec.get("entry_no") or "").strip()
            t = (rec.get("title") or "").strip()
            if eno and t and not is_bad_title(t):
                out.setdefault(eno, t)
    return out


def _rev_map(vs) -> dict:
    rev = {}
    for i, did in (vs.index_to_docstore_id or {}).items():
        rev[str(did)] = int(i)
    return rev


def patch_shard(sd: Path, embedding, titles: dict) -> int:
    vs = FAISS.load_local(str(sd), embedding, allow_dangerous_deserialization=True)
    dc = getattr(getattr(vs, "docstore", None), "_dict", None)
    if not isinstance(dc, dict) or vs.index.ntotal <= 0:
        return 0
    rev = _rev_map(vs)
    jobs = []  # (pos, doc_id, new_text)
    for doc_id, doc in dc.items():
        meta = dict(getattr(doc, "metadata", None) or {})
        if str(meta.get("field") or "") != "title":
            continue
        text = getattr(doc, "page_content", "") or ""
        if not is_bad_title(text):
            continue
        eno = str(meta.get("entry_no") or "").strip()
        new_title = titles.get(eno)
        if not new_title:
            continue
        pos = rev.get(str(doc_id))
        if pos is None:
            continue
        new_text = f"【PDFKU ENO:{eno}】 标题（英）：{new_title} 标题（中）：{new_title}"
        jobs.append((pos, doc_id, new_text, new_title, meta))
    if not jobs:
        return 0
    vecs = embedding.embed_documents([j[2] for j in jobs])
    xb = vs.index.reconstruct_n(0, vs.index.ntotal)
    xb = np.asarray(xb, dtype="float32")
    for (pos, doc_id, new_text, new_title, meta), vec in zip(jobs, vecs):
        arr = np.asarray(vec, dtype="float32")
        if arr.shape[0] != xb.shape[1]:
            continue
        xb[pos] = arr
        doc = dc[doc_id]
        doc.page_content = new_text
        meta = dict(getattr(doc, "metadata", None) or {})
        meta["title_zh"] = new_title
        doc.metadata = meta
    import faiss

    d = xb.shape[1]
    metric = getattr(vs.index, "metric_type", faiss.METRIC_INNER_PRODUCT)
    if int(metric) == int(faiss.METRIC_L2):
        new_index = faiss.IndexFlatL2(d)
    else:
        new_index = faiss.IndexFlatIP(d)
    new_index.add(xb)
    vs.index = new_index
    tmp = sd.parent / f"_tmp_{sd.name}"
    if tmp.exists():
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    vs.save_local(str(tmp))
    import shutil

    bak = sd.parent / f"_bak_{sd.name}"
    if bak.exists():
        shutil.rmtree(bak, ignore_errors=True)
    sd.rename(bak)
    tmp.rename(sd)
    shutil.rmtree(bak, ignore_errors=True)
    return len(jobs)


def main():
    titles = load_titles()
    print(f"titles={len(titles)} dir={FAISS_DIR}", flush=True)
    embedding = BgeEmbedding(EMBEDDING_MODEL_PATH, device=_PATCH_DEVICE)
    n_shard = n_chunk = 0
    shards = sorted(FAISS_DIR.glob("shard_*"))
    for sd in shards:
        if not (sd / "index.faiss").is_file():
            continue
        try:
            n = patch_shard(sd, embedding, titles)
        except Exception as ex:
            print(f"FAIL {sd.name}: {ex!r}", flush=True)
            continue
        if n:
            n_shard += 1
            n_chunk += n
            print(f"fixed {sd.name} {n}", flush=True)
            LOG.parent.mkdir(parents=True, exist_ok=True)
            with LOG.open("a", encoding="utf-8") as f:
                f.write(f"{sd.name}\t{n}\n")
    print(f"DONE shards={n_shard} chunks={n_chunk}", flush=True)


if __name__ == "__main__":
    main()
