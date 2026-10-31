# -*- coding: utf-8 -*-
"""
检索重排：对齐马铃薯「向量召回大量候选 → 重排 → top10」。

优先尝试 CrossEncoder（bge-reranker）；不可用时用混合打分（向量距离 + 词面重合 + 字段权）。
"""
from __future__ import annotations

import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RERANKER = ROOT / "models" / "bge-reranker-base"


_STOPWORDS = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "of",
    "in",
    "on",
    "to",
    "for",
    "with",
    "by",
    "from",
    "is",
    "are",
    "be",
    "as",
    "at",
    "gene",
    "genes",
    "expression",
    "function",
    "functions",
    "role",
    "roles",
    "what",
    "how",
    "please",
    "query",
    "zebrafish",  # 库内几乎篇篇有，词面加权易淹没基因符号
    "斑马鱼",
    "功能",
    "表达",
    "基因",
}


def _tokenize(q: str) -> list[str]:
    q = (q or "").lower()
    zh = re.findall(r"[\u4e00-\u9fff]{2,}", q)
    en = re.findall(r"[a-z][a-z0-9\-]{1,}", q)
    return [t for t in (zh + en) if t not in _STOPWORDS and len(t) >= 2]


def _gene_like_tokens(query: str) -> list[str]:
    """短基因符号 / 含数字的标识（sox2、hoxb1b、pou5f3）。"""
    q = (query or "").lower()
    return re.findall(r"\b[a-z]{2,}[0-9][a-z0-9]*\b", q)


def lexical_score(query: str, text: str) -> float:
    toks = _tokenize(query)
    if not toks:
        return 0.0
    blob = (text or "").lower()
    hit = sum(1 for t in toks if t in blob)
    return hit / max(1, len(toks))


def hybrid_rerank_score(query: str, hit: dict[str, Any]) -> float:
    """分数越大越好。"""
    adj = float(hit.get("score") or 0.0)  # L2 调整后，越小越好
    sim = 1.0 / (1.0 + max(0.0, adj))
    text = hit.get("text") or ""
    lex = lexical_score(query, text)
    fw = float(hit.get("field_weight") or 1.0)
    field_bonus = min(fw / 2.0, 1.2)
    # 精确 PMID/ENO 命中置顶
    if hit.get("match") in ("pmid_exact", "eno_exact"):
        return 100.0 + sim
    blob = text.lower()
    genes = _gene_like_tokens(query)
    gene_hits = sum(1 for g in genes if g in blob)
    gene_bonus = (gene_hits / max(1, len(genes))) if genes else 0.0
    # 向量为主；基因符号强加权；弱化泛化词面
    return 0.50 * sim + 0.35 * gene_bonus + 0.10 * lex + 0.05 * field_bonus


def _prefer_cuda_device(torch) -> str:
    env = (os.getenv("ZEBRA_TORCH_DEVICE") or os.getenv("BGE_RERANKER_DEVICE") or "").strip().lower()
    # 支持 cpu / cuda / cuda:N（N 可为 0、1、…）
    if env == "cpu" or (env.startswith("cuda") and _is_valid_cuda_device(env, torch)):
        return env
    return "cuda" if torch.cuda.is_available() else "cpu"


def _is_valid_cuda_device(env: str, torch) -> bool:
    """env 形如 cuda 或 cuda:N；仅当对应 GPU 可用且索引在范围内才返回 True。"""
    if not torch.cuda.is_available():
        return False
    if env == "cuda":
        return True
    if not env.startswith("cuda:"):
        return False
    try:
        idx = int(env.split(":", 1)[1])
    except ValueError:
        return False
    return 0 <= idx < torch.cuda.device_count()


@lru_cache(maxsize=1)
def _load_cross_encoder():
    model_path = Path(os.getenv("BGE_RERANKER_PATH", str(DEFAULT_RERANKER)))
    if not model_path.is_dir():
        return None
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        import torch

        tok = AutoTokenizer.from_pretrained(str(model_path))
        model = AutoModelForSequenceClassification.from_pretrained(str(model_path))
        device = _prefer_cuda_device(torch)
        model.to(device)
        model.eval()
        return tok, model, torch, device
    except Exception:
        return None


def cross_encoder_scores(query: str, texts: list[str], *, batch_size: int | None = None) -> list[float] | None:
    packed = _load_cross_encoder()
    if not packed or not texts:
        return None
    tok, model, torch, device = packed
    if batch_size is None:
        # CPU 上过小 batch 会把 200 候选拖到一分钟；CUDA 可更大
        batch_size = 32 if str(device).startswith("cuda") else 16
    scores: list[float] = []
    with torch.no_grad():
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            pairs = [[query, t[:1200]] for t in batch]
            inputs = tok(
                pairs,
                padding=True,
                truncation=True,
                max_length=512,
                return_tensors="pt",
            )
            inputs = {k: v.to(device) for k, v in inputs.items()}
            logits = model(**inputs, return_dict=True).logits.view(-1).float()
            scores.extend(float(x) for x in logits.cpu().tolist())
    return scores


def rerank_hits(query: str, hits: list[dict[str, Any]], *, top_n: int = 10) -> list[dict[str, Any]]:
    if not hits:
        return []
    if len(hits) <= top_n and all(h.get("match") in ("pmid_exact", "eno_exact") for h in hits):
        return hits[:top_n]

    ce = cross_encoder_scores(query, [h.get("text") or "" for h in hits])
    ranked: list[tuple[float, dict]] = []
    for i, h in enumerate(hits):
        if ce is not None:
            # 精确命中仍置顶
            base = float(ce[i])
            if h.get("match") in ("pmid_exact", "eno_exact"):
                base += 50.0
            # 混合一点词面，避免 CE 漂移
            score = 0.85 * base + 0.15 * lexical_score(query, h.get("text") or "")
            method = "cross_encoder"
        else:
            score = hybrid_rerank_score(query, h)
            method = "hybrid"
        hh = dict(h)
        hh["rerank_score"] = score
        hh["rerank_method"] = method
        ranked.append((score, hh))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [h for _, h in ranked[:top_n]]
