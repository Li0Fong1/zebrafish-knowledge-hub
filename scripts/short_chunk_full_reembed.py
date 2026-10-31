# -*- coding: utf-8 -*-
"""全库短语义块 + 章节切分重嵌（写入 qwen3_4b_v2，不覆盖现网 v1 / gte）。

约束：
  - 长文 max_chunks / max_body_chars = 0（不截断）
  - title 禁止写成 DOI；无 DOI 用 ZFQA / ENO
  - 只占用一张卡：启动前设 CUDA_VISIBLE_DEVICES，进程内用 cuda:0
"""
from __future__ import annotations

import json
import os
import subprocess
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

RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", os.environ.get("RUNTIME", "/data/zoujin/deng/zebrafish_runtime")))
GOLD_OUT = Path(os.environ.get("GOLD_OUT", str(RUNTIME / "gold_ingest" / "yes_fulltext_gold")))
OUT_DIR = Path(os.environ.get("FAISS_QWEN3_4B_V2_DIR", str(RUNTIME / "faiss_pubmed_yes_qwen3_4b_v2")))
V1_DIR = Path(os.environ.get("FAISS_QWEN3_4B_DIR", str(RUNTIME / "faiss_pubmed_yes_qwen3_4b_v1")))
GTE_DIR = RUNTIME / "faiss_pubmed_yes_gte_mbase_v1"
MODEL_DIR = Path(os.environ.get("QWEN3_4B_DIR", str(RUNTIME / "models" / "Qwen3-Embedding-4B")))
LOG_DIR = RUNTIME / "logs"
DONE_IDS = LOG_DIR / "qwen3_4b_v2_done_ids.json"
STATUS = LOG_DIR / "qwen3_4b_v2_reembed_status.json"
LOG = LOG_DIR / "qwen3_4b_v2_reembed.log"
EMBED_DIM = int(os.environ.get("QWEN3_4B_DIM", "2048"))
SHARD_PAPERS = int(os.environ.get("QWEN3_4B_SHARD_PAPERS", "12"))
INDEX_VERSION = "qwen3_4b_v2_short_section"
KIND = "full_reembed_short_section"

MYSQL = [
    str(RUNTIME / "mysql80" / "bin" / "mysql"),
    "--protocol=SOCKET",
    f"--socket={RUNTIME / 'mysql.sock'}",
    "-uzebrafish",
    "-pzebrafish_remote_2026",
    "zebrafish_db",
    "-N",
    "-B",
]


def _mysql_env() -> dict[str, str]:
    env = os.environ.copy()
    for k in ("MYSQL_HOST", "MYSQL_TCP_PORT", "MYSQL_PORT"):
        env.pop(k, None)
    env["MYSQL_UNIX_PORT"] = str(RUNTIME / "mysql.sock")
    return env


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _save(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def _load(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _mysql(sql: str) -> str:
    p = subprocess.run(MYSQL + ["-e", sql], capture_output=True, text=True, env=_mysql_env())
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "")[-400:])
    return p.stdout


def _clean_title(raw: str, *, zfqa: str = "", eno: str = "") -> str:
    t = (raw or "").strip()
    low = t.lower()
    if low.startswith("doi ") or low.startswith("doi:") or (t.startswith("10.") and "/" in t[:20]):
        t = ""
    if t:
        return t
    bits = [x for x in (zfqa, f"ENO:{eno}" if eno else "") if x]
    return " ".join(bits) or "local paper"


def _batch_no(entry_no: str) -> int:
    try:
        n = int(entry_no)
    except ValueError:
        n = abs(hash(entry_no)) % 10_000_000
    return n // 100 + 1


def _find_json(eno: str, body_path: str) -> Path | None:
    cands: list[Path] = []
    if body_path:
        cands.append(Path(body_path))
    if eno:
        cands.append(GOLD_OUT / f"batch_{_batch_no(eno):04d}" / "papers" / f"{eno}.json")
        extra = RUNTIME / "gold_ingest"
        for root in (extra / "yes_fulltext_gold", extra / "pdfku_library_fulltext_gold"):
            cands.append(root / f"batch_{_batch_no(eno):04d}" / "papers" / f"{eno}.json")
    for p in cands:
        if p.is_file():
            return p
    return None


def _row_from_mysql_and_json(parts: list[str]) -> dict[str, Any] | None:
    # doc_id, entry_no, pmid, doi, zfqa_id, title_en, title_zh, year, body_path
    while len(parts) < 9:
        parts.append("")
    doc_id, eno, pmid, doi, zfqa, title_en, title_zh, year, body_path = [str(x or "") for x in parts[:9]]
    rec: dict[str, Any] = {}
    jp = _find_json(eno, body_path)
    if jp:
        try:
            rec = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            rec = {}
    title = _clean_title(
        title_en or rec.get("title") or rec.get("title_en") or title_zh or rec.get("title_zh") or "",
        zfqa=zfqa,
        eno=eno,
    )
    secs = rec.get("sections") or {}
    if not isinstance(secs, dict):
        secs = {}
    abs_ = str(rec.get("abstract_en") or secs.get("abstract") or "").strip()
    body = str(rec.get("body_text") or "").strip()
    if not body and not abs_ and not title:
        return None
    return {
        "pmid": pmid if pmid.isdigit() else "",
        "entry_no": eno,
        "zfqa_id": zfqa,
        "doi": doi,
        "title": title,
        "title_en": title,
        "title_zh": (title_zh or rec.get("title_zh") or title).strip(),
        "year": year,
        "abstract_en": abs_,
        "body_text": body,
        "has_fulltext": bool(body),
        "doc_id": doc_id or (f"pmid:{pmid}" if pmid.isdigit() else f"eno:{eno}"),
        "sections": secs,
        "source": "full_reembed",
    }


def collect_rows(*, limit: int = 0) -> list[dict[str, Any]]:
    done = set(str(x) for x in _load(DONE_IDS, []))
    sql = (
        "SELECT p.doc_id, COALESCE(p.entry_no,''), COALESCE(p.pmid,''), COALESCE(p.doi,''), "
        "COALESCE(p.zfqa_id,''), COALESCE(p.title_en,''), COALESCE(p.title_zh,''), "
        "COALESCE(p.year,''), COALESCE(g.body_path,'') "
        "FROM literature_paper p "
        "LEFT JOIN literature_fulltext_gold g ON g.doc_id=p.doc_id "
        "WHERE p.gold_status='ready' ORDER BY p.doc_id"
    )
    rows: list[dict[str, Any]] = []
    skipped = 0
    empty = 0
    for ln in _mysql(sql).splitlines():
        parts = ln.split("\t")
        rec = _row_from_mysql_and_json(parts)
        if not rec:
            empty += 1
            continue
        rid = str(rec.get("entry_no") or rec.get("pmid") or rec.get("doc_id") or "")
        if rid in done:
            skipped += 1
            continue
        rows.append(rec)
        if limit and len(rows) >= limit:
            break
    _log(f"collect ready={len(rows)+skipped+empty} todo={len(rows)} skipped_done={skipped} empty={empty}")
    return rows


def _forbid_live_dirs(out: Path) -> None:
    for bad in (V1_DIR, GTE_DIR):
        if out.resolve() == bad.resolve():
            raise SystemExit(f"refuse overwrite live index {bad}")


def run(*, limit: int = 0) -> dict[str, Any]:
    _forbid_live_dirs(OUT_DIR)
    os.environ.setdefault("EXIST_ENO_MAX_CHUNKS", "0")
    os.environ.setdefault("EXIST_ENO_MAX_BODY_CHARS", "0")
    os.environ.setdefault("PUBMED_YES_SECTION_CHUNKING", "1")

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

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    _log(f"out={OUT_DIR} model={MODEL_DIR} device={device} visible={os.environ.get('CUDA_VISIBLE_DEVICES')}")

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
            self.batch = max(1, int(os.environ.get("QWEN3_4B_EMBED_BATCH", "2")))

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
                    _log(f"OOM drop batch -> {bs}")

        def embed_query(self, text: str) -> list[float]:
            return self.embed_documents([text])[0]

        def __call__(self, text):
            if isinstance(text, str):
                return self.embed_query(text)
            return self.embed_documents(list(text))

    rows = collect_rows(limit=limit)
    if not rows:
        _log("nothing to embed")
        return {"n": 0, "status": "empty"}

    emb = QwenEmb()
    probe = emb.embed_query("zebrafish gene")
    if len(probe) != EMBED_DIM:
        _log(f"WARN probe dim={len(probe)} expected={EMBED_DIM}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mf_path = OUT_DIR / "manifest.json"
    manifest = _load(mf_path, {
        "shards": [],
        "shard_info": {},
        "total_papers": 0,
        "total_chunks": 0,
        "index_version": INDEX_VERSION,
        "embedding_dim": EMBED_DIM,
        "status": "building",
    })
    next_shard = 0
    if manifest.get("shard_info"):
        next_shard = max(int(k) for k in manifest["shard_info"] if str(k).isdigit()) + 1

    done_ids = set(str(x) for x in _load(DONE_IDS, []))
    t0 = time.time()
    total_new_papers = 0
    total_new_chunks = 0
    for i in range(0, len(rows), SHARD_PAPERS):
        batch = rows[i : i + SHARD_PAPERS]
        shard_id = next_shard + (i // SHARD_PAPERS)
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
                            "zfqa_id": rec.get("zfqa_id") or "",
                            "doc_id": rec.get("doc_id") or "",
                            "title_zh": ch.get("title_zh") or rec.get("title_zh") or rec.get("title_en"),
                            "year": rec.get("year") or "",
                            "has_fulltext": bool(rec.get("has_fulltext")),
                            "chunk_index": ch.get("chunk_index"),
                            "field": ch.get("field"),
                            "section": ch.get("section") or ch.get("field"),
                            "source": KIND,
                            "index_version": INDEX_VERSION,
                        },
                    )
                )
        _log(f"shard_{shard_id:04d} papers={len(batch)} chunks={len(docs)}")
        _save(
            STATUS,
            {
                "phase": "embedding",
                "shard": shard_id,
                "papers": len(batch),
                "chunks": len(docs),
                "done_papers": total_new_papers,
                "remaining": len(rows) - total_new_papers,
                "elapsed_sec": int(time.time() - t0),
            },
        )
        texts = [d.page_content for d in docs]
        metas = [d.metadata for d in docs]
        vecs: list = []
        micro = max(8, int(os.environ.get("QWEN3_4B_DOC_MICRO", "48")))
        for j in range(0, len(texts), micro):
            vecs.extend(emb.embed_documents(texts[j : j + micro]))
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        vs = FAISS.from_embeddings(
            text_embeddings=list(zip(texts, vecs)),
            embedding=emb,
            metadatas=metas,
        )
        sd = OUT_DIR / f"shard_{shard_id:04d}"
        sd.mkdir(parents=True, exist_ok=True)
        vs.save_local(str(sd))
        shards = list(manifest.get("shards") or [])
        if shard_id not in shards:
            shards.append(shard_id)
        manifest["shards"] = shards
        manifest.setdefault("shard_info", {})[str(shard_id)] = {
            "paper_count": len(batch),
            "chunk_count": len(docs),
            "kind": KIND,
        }
        manifest["total_papers"] = int(manifest.get("total_papers") or 0) + len(batch)
        manifest["total_chunks"] = int(manifest.get("total_chunks") or 0) + len(docs)
        manifest["embedding_model"] = str(MODEL_DIR)
        manifest["embedding_dim"] = EMBED_DIM
        manifest["index_version"] = INDEX_VERSION
        manifest["status"] = "building"
        manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
        manifest["chunking"] = {
            "mode": "paragraph_section",
            "max_chunks": 0,
            "max_body_chars": 0,
        }
        _save(mf_path, manifest)
        for rec in batch:
            rid = str(rec.get("entry_no") or rec.get("pmid") or rec.get("doc_id") or "")
            if rid:
                done_ids.add(rid)
        _save(DONE_IDS, sorted(done_ids))
        total_new_papers += len(batch)
        total_new_chunks += len(docs)
        elapsed = max(1.0, time.time() - t0)
        ppm = total_new_papers / elapsed * 60
        _log(
            f"wrote {sd} new={total_new_papers}/{len(rows)} chunks={total_new_chunks} "
            f"ppm={ppm:.1f} index_papers={manifest['total_papers']}"
        )

    manifest["status"] = "ready"
    _save(mf_path, manifest)
    out = {
        "new_papers": total_new_papers,
        "new_chunks": total_new_chunks,
        "manifest_papers": manifest.get("total_papers"),
        "manifest_chunks": manifest.get("total_chunks"),
        "shards": len(manifest.get("shards") or []),
        "out": str(OUT_DIR),
    }
    _log(f"vectorize done {out}")
    _save(STATUS, {"phase": "done", **out})
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    run(limit=args.limit)
