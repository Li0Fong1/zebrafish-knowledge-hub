# -*- coding: utf-8 -*-
"""
SciHub 解析后流水线（服务器）：

1) 金标检测：is_pdf_gold（不降门槛、不垫文）
2) 未达标 → 硬超时重抽 + finalize，循环修复直至达标或耗尽可修复手段
3) 已金标 → 写入语料 → 按 Qwen3-Embedding-4B 完整标准增量向量化
   （paragraph field-aware 切分、dim=2048、追加 shard，不覆盖 gte）
4) 金标注册 MySQL literature_paper / literature_fulltext_gold

用法（远程）:
  export PROJECT=/data/zoujin/deng/zebrafish_gene_qa
  export RUNTIME=/data/zoujin/deng/zebrafish_runtime
  $PY -u -m scripts.scihub_post_gold_pipeline --mode all
  $PY -u -m scripts.scihub_post_gold_pipeline --mode repair --rounds 5
  $PY -u -m scripts.scihub_post_gold_pipeline --mode vectorize
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_hunt_fulltext import (  # noqa: E402
    PDF_GOLD_BODY,
    finalize_to_gold_bar,
    is_pdf_gold,
)
from scripts.gold_upgrade_5680 import upgrade_from_pdf_hard  # noqa: E402

RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", os.environ.get("RUNTIME", "/data/zoujin/deng/zebrafish_runtime")))
GOLD_OUT = Path(
    os.environ.get(
        "GOLD_OUT",
        str(RUNTIME / "gold_ingest" / "yes_fulltext_gold"),
    )
)
INBOX = Path(
    os.environ.get(
        "SCIHUB_INBOX",
        str(RUNTIME / "gold_ingest" / "pdfku_inbox" / "scihub_batch"),
    )
)
PARSE_LOG = Path(
    os.environ.get(
        "SCIHUB_PARSE_LOG",
        str(RUNTIME / "logs" / "scihub_gold_nohup.log"),
    )
)
LOG_DIR = RUNTIME / "logs"
CORPUS = LOG_DIR / "scihub_gold_vectorize_corpus.jsonl"
DONE_IDS = LOG_DIR / "scihub_gold_vectorize_done_ids.json"
REPAIR_STATE = LOG_DIR / "scihub_gold_repair_state.json"
STATUS = LOG_DIR / "scihub_post_gold_status.json"
PIPELINE_LOG = LOG_DIR / "scihub_post_gold_pipeline.log"
UNREACHABLE = RUNTIME / "gold_ingest" / "logs" / "gold_unreachable_manifest.jsonl"
FAISS_DIR = Path(
    os.environ.get(
        "FAISS_QWEN3_4B_DIR",
        str(RUNTIME / "faiss_pubmed_yes_qwen3_4b_v1"),
    )
)
MODEL_DIR = Path(
    os.environ.get(
        "QWEN3_4B_DIR",
        str(RUNTIME / "models" / "Qwen3-Embedding-4B"),
    )
)
GTE_DIR = RUNTIME / "faiss_pubmed_yes_gte_mbase_v1"
INDEX_VERSION = "qwen3_4b_v1"
EMBED_DIM = int(os.environ.get("QWEN3_4B_DIM", "2048"))
SHARD_PAPERS = int(os.environ.get("QWEN3_4B_SHARD_PAPERS", "20"))
KIND = "scihub_gold_qwen3_4b"
FLOOR = PDF_GOLD_BODY
MAX_REPAIR_ROUNDS = int(os.environ.get("SCIHUB_REPAIR_ROUNDS", "8"))
HARD_TIMEOUT = float(os.environ.get("SCIHUB_REPAIR_TIMEOUT", "180"))


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with PIPELINE_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _write_status(**kw: Any) -> None:
    cur: dict[str, Any] = {}
    if STATUS.is_file():
        try:
            cur = json.loads(STATUS.read_text(encoding="utf-8"))
        except Exception:
            cur = {}
    cur.update(kw)
    cur["updated_at"] = datetime.now().isoformat(timespec="seconds")
    STATUS.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def batch_no(entry_no: str) -> int:
    try:
        n = int(entry_no)
    except ValueError:
        n = abs(hash(entry_no)) % 10_000_000
    return n // 100 + 1


def gold_json_path(eno: str) -> Path:
    return GOLD_OUT / f"batch_{batch_no(eno):04d}" / "papers" / f"{eno}.json"


def find_inbox_pdf(eno: str) -> Path | None:
    folders = [INBOX]
    extra = RUNTIME / "gold_ingest" / "pdfku_inbox"
    for d in (extra / "scihub_batch", extra / "pdf", extra / "pdf_remaining"):
        if d not in folders:
            folders.append(d)
    for folder in folders:
        if not folder.is_dir():
            continue
        hits = sorted(folder.glob(f"{eno}_*.pdf")) + sorted(folder.glob(f"{eno}.pdf"))
        if hits:
            return hits[0]
    return None


def inbox_entry_nos() -> set[str]:
    """Entry numbers from leftover inbox PDFs (not only scihub_batch)."""
    out: set[str] = set()
    extra = RUNTIME / "gold_ingest" / "pdfku_inbox"
    for folder in (extra / "pdf", extra / "pdf_remaining", INBOX):
        if not folder.is_dir():
            continue
        for pdf in folder.glob("*.pdf"):
            m = re.match(r"^(\d+)", pdf.stem)
            if m:
                out.add(m.group(1))
    return out


def parse_log_rows() -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    if not PARSE_LOG.is_file():
        return by
    with PARSE_LOG.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            eno = str(o.get("entry_no") or "").strip()
            if eno:
                by[eno] = o
    return by


def load_rec(eno: str) -> dict[str, Any] | None:
    p = gold_json_path(eno)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_rec(rec: dict[str, Any]) -> Path:
    eno = str(rec["entry_no"])
    path = gold_json_path(eno)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def doi_of(pdf: Path) -> str:
    parts = pdf.stem.split("_", 1)
    if len(parts) == 2 and parts[1].startswith("10."):
        return parts[1].replace("_", "/")
    return ""


def repair_one(eno: str, *, round_i: int) -> dict[str, Any]:
    """Re-extract + finalize. Never pad body to fake gold."""
    pdf = find_inbox_pdf(eno)
    old = load_rec(eno) or {}
    if not pdf or not pdf.is_file():
        return {
            "entry_no": eno,
            "status": "no_pdf",
            "gold": False,
            "round": round_i,
        }
    row = {
        "entry_no": eno,
        "q12_no": str(old.get("q12_no") or ""),
        "en_title": str(old.get("title") or ""),
        "doi": str(old.get("doi") or doi_of(pdf)),
        "pmid": str(old.get("pmid") or ""),
        "source_url": str(old.get("source_url") or ""),
        "content_type": "pdf",
    }
    # 无题名时用本机编号，禁止把 DOI 写进 title（会污染 title chunk）
    tip = row["en_title"] or (f"ENO {eno}" if eno else "local fulltext")
    rec = upgrade_from_pdf_hard(pdf, row, tip, timeout=HARD_TIMEOUT)
    if not rec:
        return {
            "entry_no": eno,
            "status": "extract_fail",
            "gold": False,
            "round": round_i,
            "pdf": str(pdf),
        }
    neu = finalize_to_gold_bar(dict(rec), fallback_title=tip, prefer_source="pdf_align")
    neu["corpus"] = "yes_fulltext_gold"
    neu["enrich_from"] = "scihub_repair"
    neu["source_pdf"] = str(pdf)
    neu["parsed_on"] = "server"
    neu["repair_round"] = round_i
    neu["repaired_at"] = datetime.now().isoformat(timespec="seconds")
    # Keep longer body if old was longer but still not gold? Prefer longer real extract.
    old_body = old.get("body_text") or ""
    new_body = neu.get("body_text") or ""
    if len(old_body) > len(new_body) and is_pdf_gold(old):
        return {
            "entry_no": eno,
            "status": "keep_old_gold",
            "gold": True,
            "round": round_i,
            "body_len": len(old_body),
        }
    if len(old_body) > len(new_body) + 2000 and not is_pdf_gold(neu):
        # new extract worse — keep old, still not gold
        old["repair_round"] = round_i
        old["repair_note"] = "new_extract_shorter_kept_old"
        save_rec(old)
        return {
            "entry_no": eno,
            "status": "kept_old_shorter_new",
            "gold": is_pdf_gold(old),
            "round": round_i,
            "body_len": len(old_body),
            "new_body_len": len(new_body),
        }
    path = save_rec(neu)
    gold = is_pdf_gold(neu)
    return {
        "entry_no": eno,
        "status": "gold" if gold else "still_short",
        "gold": gold,
        "round": round_i,
        "out": str(path),
        "body_len": len(neu.get("body_text") or ""),
        "floor": FLOOR,
        "source": neu.get("source"),
        "gold_tier": neu.get("gold_tier"),
    }


def record_unreachable(
    eno: str,
    reason: str,
    *,
    pdf: str = "",
    body_len: int = 0,
    rounds: int = 0,
    campaign: str = "scihub_13841_post_gold",
    source: str = "scihub_batch_repair",
) -> None:
    UNREACHABLE.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "id": eno,
        "entry_no": eno,
        "reason": reason,
        "source": source,
        "sources": [source],
        "pdf_path": pdf,
        "json_path": str(gold_json_path(eno)),
        "body_len": body_len,
        "retried": True,
        "retry_count": rounds,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "campaign": campaign,
    }
    with UNREACHABLE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run_repair(*, rounds: int, limit: int = 0, only_enos: list[str] | None = None) -> dict[str, Any]:
    rows = parse_log_rows()
    state = _load_json(REPAIR_STATE, {"done_gold": [], "exhausted": {}, "attempts": {}})
    done_gold = set(str(x) for x in (state.get("done_gold") or []))
    exhausted = dict(state.get("exhausted") or {})
    attempts = {str(k): int(v) for k, v in (state.get("attempts") or {}).items()}

    candidates: list[str] = []
    if only_enos:
        candidates = [str(x) for x in only_enos]
    else:
        # Only SciHub parse-log rows (avoid full GOLD_OUT tree walk — too slow).
        for eno, row in rows.items():
            if eno in done_gold or eno in exhausted:
                continue
            if row.get("gold"):
                rec = load_rec(eno)
                if rec and is_pdf_gold(rec):
                    done_gold.add(eno)
                    continue
                # claimed gold but fails is_pdf_gold → repair
                candidates.append(eno)
                continue
            candidates.append(eno)

    candidates = sorted(set(candidates), key=lambda x: (attempts.get(x, 0), x))
    if limit:
        candidates = candidates[:limit]

    _log(f"repair candidates={len(candidates)} rounds={rounds}")
    stats = Counter()
    newly_gold: list[str] = []

    for rnd in range(1, rounds + 1):
        pending = [e for e in candidates if e not in done_gold and e not in exhausted]
        if not pending:
            break
        _log(f"repair round {rnd}/{rounds} pending={len(pending)}")
        _write_status(phase="repair", round=rnd, pending=len(pending), newly_gold=len(newly_gold))
        for eno in pending:
            attempts[eno] = attempts.get(eno, 0) + 1
            res = repair_one(eno, round_i=rnd)
            stats[res.get("status") or "other"] += 1
            print(json.dumps(res, ensure_ascii=False), flush=True)
            if res.get("gold"):
                done_gold.add(eno)
                newly_gold.append(eno)
                continue
            # Exhaust when: no pdf, or body stagnant near-zero after enough rounds,
            # or attempts >= MAX_REPAIR_ROUNDS and body still << floor with no growth.
            body_len = int(res.get("body_len") or 0)
            if res.get("status") == "no_pdf":
                exhausted[eno] = {"reason": "no_pdf", "rounds": attempts[eno]}
                record_unreachable(eno, "no_pdf", rounds=attempts[eno])
            elif attempts[eno] >= MAX_REPAIR_ROUNDS and body_len < int(FLOOR * 0.5):
                exhausted[eno] = {
                    "reason": f"short_after_{attempts[eno]}_rounds",
                    "body_len": body_len,
                }
                record_unreachable(
                    eno,
                    f"short_or_fail_after_repair_{attempts[eno]}",
                    pdf=str(find_inbox_pdf(eno) or ""),
                    body_len=body_len,
                    rounds=attempts[eno],
                )
        state = {
            "done_gold": sorted(done_gold),
            "exhausted": exhausted,
            "attempts": attempts,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        _save_json(REPAIR_STATE, state)

    out = {
        "candidates": len(candidates),
        "newly_gold": len(newly_gold),
        "done_gold": len(done_gold),
        "exhausted": len(exhausted),
        "stats": dict(stats),
    }
    _log(f"repair done {out}")
    _write_status(phase="repair_done", **out)
    return out


FOUR_SECTION_GOLD_TIERS = {"four_sections_gold", "four_sections_relaxed"}


def accepts_vectorize(rec: dict[str, Any]) -> bool:
    """Strict pdf_gold, or four-section gold (no body-length floor)."""
    if is_pdf_gold(rec):
        return True
    tier = str(rec.get("gold_tier") or "").strip()
    if tier in FOUR_SECTION_GOLD_TIERS:
        return True
    if rec.get("gold_rule") == "four_sections_only" and rec.get("gold"):
        return True
    return False


def extra_eno_lists() -> set[str]:
    out: set[str] = set()
    for name in (
        "unsent_entry_nos.txt",
        "unsent_four_promoted.txt",
        "unsent_nongold_enos.txt",
    ):
        p = LOG_DIR / name
        if not p.is_file():
            continue
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            eno = ln.strip()
            if eno:
                out.add(eno)
    return out


def rec_to_corpus_row(rec: dict[str, Any]) -> dict[str, Any] | None:
    if not accepts_vectorize(rec):
        return None
    eno = str(rec.get("entry_no") or "").strip()
    pmid = str(rec.get("pmid") or "").strip()
    body = str(rec.get("body_text") or "")
    if not eno and not pmid:
        return None
    secs = rec.get("sections") or {}
    abs_ = ""
    if isinstance(secs, dict):
        abs_ = str(secs.get("abstract") or "").strip()
    title = str(rec.get("title") or rec.get("title_en") or rec.get("title_zh") or "").strip()
    if title.lower().startswith("doi ") or title.lower().startswith("doi:"):
        title = ""
    if not title:
        z = str(rec.get("zfqa_id") or "").strip()
        title = f"{z} ENO:{eno}".strip() if (z or eno) else ""
    return {
        "pmid": pmid if pmid.isdigit() else "",
        "entry_no": eno,
        "title": title,
        "title_en": title,
        "title_zh": str(rec.get("title_zh") or title),
        "abstract_en": abs_,
        "body_text": body,
        "has_fulltext": True,
        "source": str(rec.get("source") or "pdf_align"),
        "fulltext_source": "scihub_gold",
        "doc_id": f"pmid:{pmid}" if pmid.isdigit() else f"eno:{eno}",
        "sections": secs if isinstance(secs, dict) else {},
    }


def build_corpus(*, limit: int = 0) -> dict[str, Any]:
    """Collect is_pdf_gold rows from scihub parse/repair into vectorize corpus."""
    done = set(_load_json(DONE_IDS, []))
    rows_log = parse_log_rows()
    enos = set(rows_log.keys())
    # include repair-done gold
    state = _load_json(REPAIR_STATE, {})
    enos.update(str(x) for x in (state.get("done_gold") or []))
    # include leftover inbox PDFs (may be gold but absent from scihub parse log)
    enos.update(inbox_entry_nos())
    enos.update(extra_eno_lists())

    out_rows: list[dict[str, Any]] = []
    skipped_done = 0
    not_gold = 0
    for eno in sorted(enos, key=lambda x: int(x) if x.isdigit() else x):
        if eno in done:
            skipped_done += 1
            continue
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
    summary = {
        "corpus_path": str(CORPUS),
        "n": len(out_rows),
        "skipped_already_vectorized_ids": skipped_done,
        "scanned_not_gold": not_gold,
        "floor": FLOOR,
        "enos_considered": len(enos),
    }
    _log(f"build_corpus {summary}")
    _write_status(phase="corpus_ready", **summary)
    return summary


def _gpu_free_mb() -> list[int]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            text=True,
        )
        return [int(x.strip()) for x in out.strip().splitlines() if x.strip()]
    except Exception:
        return []


def run_vectorize(*, limit: int = 0, force_gpu: int | None = None) -> dict[str, Any]:
    """Incremental Qwen3-4B embed into faiss_pubmed_yes_qwen3_4b_v1 (append shards)."""
    if FAISS_DIR.resolve() == GTE_DIR.resolve():
        raise SystemExit("refuse overwrite gte index")

    corp = build_corpus(limit=limit)
    if corp["n"] <= 0:
        _log("vectorize: nothing to embed")
        return {"n": 0, "status": "empty"}

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

    frees = _gpu_free_mb()
    gpu = force_gpu
    if gpu is None:
        # Prefer a free card (Hub often occupies GPU0)
        ranked = sorted(enumerate(frees), key=lambda x: -x[1])
        gpu = ranked[0][0] if ranked else 0
    _log(f"vectorize using GPU {gpu}, free_mb={frees}")
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu)
    device = "cuda:0" if torch.cuda.is_available() else "cpu"

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

    emb = QwenEmb()
    probe = emb.embed_query("zebrafish gene")
    if len(probe) != EMBED_DIM:
        _log(f"WARN probe dim={len(probe)} expected={EMBED_DIM}")

    mf_path = FAISS_DIR / "manifest.json"
    manifest = json.loads(mf_path.read_text(encoding="utf-8")) if mf_path.is_file() else {
        "shards": [],
        "shard_info": {},
        "total_papers": 0,
        "total_chunks": 0,
    }
    next_shard = 0
    if manifest.get("shard_info"):
        next_shard = max(int(k) for k in manifest["shard_info"] if str(k).isdigit()) + 1

    rows: list[dict[str, Any]] = []
    with CORPUS.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))

    done_ids = set(_load_json(DONE_IDS, []))
    rows = [r for r in rows if str(r.get("entry_no") or r.get("pmid") or "") not in done_ids]
    if limit:
        rows = rows[:limit]

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
                            "doc_id": rec.get("doc_id") or "",
                            "title_zh": ch.get("title_zh"),
                            "has_fulltext": True,
                            "chunk_index": ch.get("chunk_index"),
                            "field": ch.get("field"),
                            "section": ch.get("section") or ch.get("field"),
                            "source": "scihub_gold",
                            "index_version": INDEX_VERSION,
                        },
                    )
                )
        _log(f"shard_{shard_id:04d} papers={len(batch)} chunks={len(docs)}")
        _write_status(
            phase="embedding",
            shard=shard_id,
            papers=len(batch),
            chunks=len(docs),
            done_papers=total_new_papers,
            remaining=len(rows) - total_new_papers,
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
        sd = FAISS_DIR / f"shard_{shard_id:04d}"
        sd.mkdir(parents=True, exist_ok=True)
        vs.save_local(str(sd))
        info = {
            "paper_count": len(batch),
            "chunk_count": len(docs),
            "kind": KIND,
            "first_doc": batch[0].get("doc_id"),
            "last_doc": batch[-1].get("doc_id"),
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
        manifest["status"] = "ready"
        manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
        manifest["chunking"] = {
            "mode": "paragraph_field_aware",
            "includes": ["pmid_fulltext_qwen3_4b", "exist_eno_qwen3_4b", KIND],
            "rerank": "hybrid_or_cross_encoder",
        }
        mf_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for rec in batch:
            rid = str(rec.get("entry_no") or rec.get("pmid") or "")
            if rid:
                done_ids.add(rid)
        _save_json(DONE_IDS, sorted(done_ids))
        total_new_papers += len(batch)
        total_new_chunks += len(docs)
        _log(
            f"wrote {sd} cumulative_index papers={manifest['total_papers']} "
            f"chunks={manifest['total_chunks']} new={total_new_papers}/{len(rows)}"
        )

    out = {
        "new_papers": total_new_papers,
        "new_chunks": total_new_chunks,
        "manifest_papers": manifest.get("total_papers"),
        "manifest_chunks": manifest.get("total_chunks"),
        "shards": len(manifest.get("shards") or []),
        "dim": EMBED_DIM,
        "kind": KIND,
        "gpu": gpu,
    }
    _log(f"vectorize done {out}")
    _write_status(phase="vectorize_done", **out)
    return out


def run_register_db(*, limit: int = 0) -> dict[str, Any]:
    """Upsert gold JSON into MySQL (doc_id=eno:{n}; body_path authority).

    Avoids argv-too-long by writing SQL to a temp file (no giant sections_json).
    """
    import hashlib

    mysql_bin = str(RUNTIME / "mysql80" / "bin" / "mysql")
    sock = str(RUNTIME / "mysql.sock")
    mysql_args = [
        mysql_bin,
        f"--socket={sock}",
        "--protocol=SOCKET",
        "-uzebrafish",
        "-pzebrafish_remote_2026",
        "zebrafish_db",
    ]
    # fallback TCP if socket fails
    mysql_tcp = [
        mysql_bin,
        "-h127.0.0.1",
        "-P3307",
        "-uzebrafish",
        "-pzebrafish_remote_2026",
        "zebrafish_db",
    ]
    campaign = "scihub_13841_post_gold"

    def _mysql_run(args: list[str], payload: str) -> subprocess.CompletedProcess:
        return subprocess.run(args, input=payload, capture_output=True, text=True)

    def sql(q: str) -> str:
        p = _mysql_run(mysql_args + ["-N", "-B"], q + "\n")
        if p.returncode != 0 and ("Can't connect" in (p.stderr or "") or "2003" in (p.stderr or "")):
            p = _mysql_run(mysql_tcp + ["-N", "-B"], q + "\n")
        lines = [
            ln
            for ln in ((p.stdout or "") + (p.stderr or "")).splitlines()
            if "Using a password" not in ln and "Warning" not in ln
        ]
        return "\n".join(lines).strip()

    def esc(s: str | None) -> str:
        if s is None:
            return "NULL"
        return "'" + str(s).replace("\\", "\\\\").replace("'", "''") + "'"

    # probe
    probe = sql("SELECT 1")
    if probe.strip() != "1":
        _log(f"register_db mysql probe failed: {probe[:300]}")
        # force tcp args for rest of run
        mysql_args[:] = mysql_tcp
        probe = sql("SELECT 1")
        _log(f"register_db mysql tcp probe: {probe[:80]}")

    rows_log = parse_log_rows()
    state = _load_json(REPAIR_STATE, {})
    enos = set(rows_log.keys()) | set(str(x) for x in (state.get("done_gold") or [])) | inbox_entry_nos()
    registered = 0
    skipped = 0
    batch_sql: list[str] = []

    def flush_batch() -> None:
        nonlocal batch_sql
        if not batch_sql:
            return
        payload = "SET NAMES utf8mb4;\n" + "\n".join(batch_sql) + "\n"
        p = _mysql_run(mysql_args, payload)
        if p.returncode != 0 and ("Can't connect" in (p.stderr or "") or "2003" in (p.stderr or "")):
            p = _mysql_run(mysql_tcp, payload)
        err = (p.stderr or "") + (p.stdout or "")
        if p.returncode != 0:
            _log(f"register_db batch mysql rc={p.returncode} err={err[-500:]}")
        batch_sql = []

    for eno in sorted(enos, key=lambda x: int(x) if x.isdigit() else x):
        rec = load_rec(eno)
        if not rec or not is_pdf_gold(rec):
            skipped += 1
            continue
        path = gold_json_path(eno)
        doc_id = f"eno:{eno}"
        pmid = str(rec.get("pmid") or "").strip()
        title = str(rec.get("title") or rec.get("title_en") or "")[:2000]
        if title.lower().startswith("doi ") or title.lower().startswith("doi:"):
            title = f"ENO {eno}"
        raw_doi = str(rec.get("doi") or "").strip()
        doi = raw_doi if raw_doi and raw_doi.lower() not in ("none", "null", "nan") else None
        source_tier = str(rec.get("gold_tier") or rec.get("source") or "pdf_gold")[:64]
        body = rec.get("body_text") or ""
        sha = hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
        pmid_sql = esc(pmid) if pmid.isdigit() else "NULL"
        # body_path is authority; skip huge sections_json on wire
        batch_sql.append(
            f"""
INSERT INTO literature_paper (
  doc_id, pmid, entry_no, doi, pmcid, source,
  title_en, title_zh, year, journal, judgement,
  has_fulltext, gold_status
) VALUES (
  {esc(doc_id)}, {pmid_sql}, {esc(eno)}, {esc(doi)}, NULL, 'scihub_gold',
  {esc(title)}, NULL, NULL, NULL, '是',
  1, 'ready'
)
ON DUPLICATE KEY UPDATE
  doi=COALESCE(VALUES(doi), doi),
  title_en=COALESCE(VALUES(title_en), title_en),
  has_fulltext=1,
  gold_status='ready',
  updated_at=CURRENT_TIMESTAMP;
INSERT INTO literature_fulltext_gold (
  doc_id, gold_version, source_tier, body_path, body_text,
  sections_json, content_sha256, campaign_id
) VALUES (
  {esc(doc_id)}, 'v1', {esc(source_tier)}, {esc(str(path))}, NULL,
  NULL, {esc(sha)}, {esc(campaign)}
)
ON DUPLICATE KEY UPDATE
  source_tier=VALUES(source_tier),
  body_path=VALUES(body_path),
  content_sha256=VALUES(content_sha256),
  campaign_id=VALUES(campaign_id),
  body_text=NULL,
  updated_at=CURRENT_TIMESTAMP;
"""
        )
        registered += 1
        if len(batch_sql) >= 50:
            flush_batch()
            _log(f"register_db progress {registered}")
        if limit and registered >= limit:
            break

    flush_batch()
    ready = sql("SELECT COUNT(*) FROM literature_paper WHERE gold_status='ready'")
    camp_n = sql(
        f"SELECT COUNT(*) FROM literature_fulltext_gold WHERE campaign_id={esc(campaign)}"
    )
    out = {
        "registered": registered,
        "skipped_not_gold": skipped,
        "ready_now": ready,
        "campaign_rows": camp_n,
    }
    _log(f"register_db {out}")
    _write_status(phase="register_done", **out)
    return out


def wait_parse_idle(*, max_wait_sec: int = 0) -> bool:
    """Return True if gold_parse_on_server.py is not running."""
    t0 = time.time()
    while True:
        p = subprocess.run(["pgrep", "-af", "gold_parse_on_server.py"], capture_output=True, text=True)
        alive = bool((p.stdout or "").strip())
        if not alive:
            return True
        if max_wait_sec and (time.time() - t0) > max_wait_sec:
            return False
        _log("parse still running; sleep 120s")
        time.sleep(120)


def run_all(*, repair_rounds: int, wait_parse: bool) -> dict[str, Any]:
    _write_status(phase="start", policy="gold→vectorize; not-gold→repair-until-gold-or-exhausted")
    if wait_parse:
        wait_parse_idle(max_wait_sec=0)
    # 1) vectorize current gold first (can run while parse finishes if already many gold)
    # Prefer repair first on not-gold so more gold enters corpus — but user asked gold→vectorize
    # and not-gold→repair. Do: repair round → vectorize → register, loop.
    summary: dict[str, Any] = {"steps": []}
    for loop in range(1, 4):
        _log(f"=== pipeline loop {loop} ===")
        r = run_repair(rounds=repair_rounds)
        summary["steps"].append({"repair": r})
        v = run_vectorize(force_gpu=1)  # Hub on GPU0; use GPU1
        summary["steps"].append({"vectorize": v})
        d = run_register_db()
        summary["steps"].append({"register": d})
        # stop if no more repair candidates progressing
        if r.get("newly_gold", 0) == 0 and (v.get("new_papers") or 0) == 0:
            break
    _write_status(phase="all_done", summary=summary)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--mode",
        choices=["repair", "corpus", "vectorize", "register", "all"],
        default="all",
    )
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--gpu", type=int, default=-1)
    ap.add_argument("--wait-parse", action="store_true")
    ap.add_argument("--eno", action="append", default=[])
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"mode={args.mode} gold_out={GOLD_OUT} inbox={INBOX} faiss={FAISS_DIR}")

    if args.mode == "repair":
        run_repair(rounds=args.rounds, limit=args.limit, only_enos=args.eno or None)
    elif args.mode == "corpus":
        build_corpus(limit=args.limit)
    elif args.mode == "vectorize":
        run_vectorize(limit=args.limit, force_gpu=(args.gpu if args.gpu >= 0 else 1))
    elif args.mode == "register":
        run_register_db(limit=args.limit)
    else:
        run_all(repair_rounds=args.rounds, wait_parse=args.wait_parse)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
