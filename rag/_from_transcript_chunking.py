# -*- coding: utf-8 -*-
"""YES 文献：分字段切块、字段权重、查询扩写。"""
from __future__ import annotations

import re
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import (
    PUBMED_YES_CHUNK_OVERLAP,
    PUBMED_YES_CHUNK_SIZE,
    PUBMED_YES_FIELD_WEIGHT_ABSTRACT,
    PUBMED_YES_FIELD_WEIGHT_BODY,
    PUBMED_YES_FIELD_WEIGHT_META,
    PUBMED_YES_FIELD_WEIGHT_MIXED,
    PUBMED_YES_FIELD_WEIGHT_TITLE,
    PUBMED_YES_QUERY_EXPAND,
)

# 字段 → 检索权重（越大越优先；最终 adjusted = raw_L2 / weight）
FIELD_WEIGHTS: dict[str, float] = {
    "title": float(PUBMED_YES_FIELD_WEIGHT_TITLE),
    "abstract": float(PUBMED_YES_FIELD_WEIGHT_ABSTRACT),
    "body": float(PUBMED_YES_FIELD_WEIGHT_BODY),
    "meta": float(PUBMED_YES_FIELD_WEIGHT_META),
    "mixed": float(PUBMED_YES_FIELD_WEIGHT_MIXED),
}

# 专名 / 主题中英扩写（补「无权重 + 中英不对齐」导致的召回缺口）
_EXPAND_RULES: list[tuple[re.Pattern[str], list[str]]] = [
    (
        re.compile(r"hif|缺氧诱导|低氧诱导|缺氧|低氧", re.I),
        [
            "zebrafish HIF hypoxia-inducible factor",
            "zebrafish hypoxia HIF-1 HIF-2",
            "斑马鱼 HIF 缺氧 hypoxia",
        ],
    ),
    (
        re.compile(r"线粒体|mitochondri", re.I),
        [
            "zebrafish mitochondria mitochondrial",
            "斑马鱼 线粒体 mitochondria",
        ],
    ),
    (
        re.compile(r"\btp53\b|p53", re.I),
        ["zebrafish tp53 p53", "斑马鱼 tp53 p53"],
    ),
    (
        re.compile(r"心脏|cardiomy|heart", re.I),
        ["zebrafish heart cardiac cardiomyocyte", "斑马鱼 心脏 cardiac"],
    ),
]


def field_weight(field: str | None) -> float:
    if not field:
        return FIELD_WEIGHTS["mixed"]
    return float(FIELD_WEIGHTS.get(str(field), FIELD_WEIGHTS["mixed"]))


def adjust_l2_score(raw_score: float, field: str | None) -> float:
    """L2 越小越好；除以权重后，高权字段更靠前。"""
    w = max(0.1, field_weight(field))
    return float(raw_score) / w


def infer_field_from_text(text: str, meta: dict | None = None) -> str:
    """新索引读 metadata.field；旧索引按正文标签推断。"""
    meta = meta or {}
    tagged = (meta.get("field") or "").strip().lower()
    if tagged in FIELD_WEIGHTS:
        return tagged
    t = text or ""
    head = t[:160]
    has_title = ("标题（" in head) or ("标题(" in head) or head.lstrip().startswith("【PUBMED")
    has_abs = ("摘要（" in t[:500]) or ("摘要(" in t[:500])
    has_body = ("正文（" in t[:240]) or ("正文(" in t[:240]) or t.lstrip().startswith("正文")
    has_meta_only = ("来源信息" in head) and not has_abs and not has_body
    if has_title and not has_abs and not has_body:
        return "title"
    if has_abs and not has_body and not has_title:
        return "abstract"
    if has_body and not has_title and not has_abs:
        return "body"
    if has_title and has_abs and not has_body:
        return "title"  # 标题+摘要开头块：按标题权，突出题名命中
    if has_body:
        return "body"
    if has_meta_only:
        return "meta"
    if has_abs:
        return "abstract"
    return "mixed"


def _header(pmid: str) -> str:
    return f"【PUBMED YES PMID:{pmid}】"


def _meta_line(rec: dict[str, Any]) -> str:
    parts: list[str] = []
    if rec.get("journal"):
        parts.append(str(rec["journal"]))
    if rec.get("year"):
        parts.append(str(rec["year"]))
    if rec.get("doi"):
        parts.append(f"DOI:{rec['doi']}")
    if rec.get("pmcid"):
        parts.append(str(rec["pmcid"]))
    parts.append("JUDGEMENT:是")
    if rec.get("has_fulltext"):
        parts.append("FULLTEXT:yes")
    return "来源信息：" + " | ".join(parts) if parts else ""


def _splitter(chunk_size: int | None = None, overlap: int | None = None) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=int(chunk_size or PUBMED_YES_CHUNK_SIZE),
        chunk_overlap=int(overlap or PUBMED_YES_CHUNK_OVERLAP),
        separators=["\n\n", "\n", "。", ". ", "；", "，", ",", " "],
    )


def split_record_to_field_chunks(
    rec: dict[str, Any],
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[dict[str, Any]]:
    """
    按字段分别切块并打 field 标签，供嵌入使用。

    权重意图：
      title > abstract > body > meta
    每块正文仍带 PMID 头，便于检索命中后直接引用。
    """
    pmid = str(rec.get("pmid") or "?")
    title_zh = (rec.get("title_zh") or rec.get("title_en") or "").strip()
    title_en = (rec.get("title_en") or "").strip()
    sp = _splitter(chunk_size, overlap)
    out: list[dict[str, Any]] = []
    ci = 0
    cs = int(chunk_size or PUBMED_YES_CHUNK_SIZE)

    def _add(field: str, body: str) -> None:
        nonlocal ci
        body = (body or "").strip()
        if not body:
            return
        prefix = f"{_header(pmid)}\n"
        if field == "title":
            pieces = [body]
        else:
            pieces = sp.split_text(body) if len(body) > cs else [body]
        for piece in pieces:
            text = prefix + piece
            out.append(
                {
                    "text": text,
                    "field": field,
                    "pmid": pmid,
                    "title_zh": title_zh,
                    "has_fulltext": bool(rec.get("has_fulltext")),
                    "chunk_index": ci,
                    "source": "pubmed_yes",
                }
            )
            ci += 1

    title_block = "\n".join(
        x
        for x in (
            f"标题（英）：{title_en}" if title_en else "",
            f"标题（中）：{title_zh}" if title_zh else "",
        )
        if x
    )
    _add("title", title_block)

    meta = _meta_line(rec)
    if meta:
        _add("meta", meta)

    ae = (rec.get("abstract_en") or "").strip()
    az = (rec.get("abstract_zh") or "").strip()
    abs_parts = []
    if ae:
        abs_parts.append(f"摘要（英）：{ae}")
    if az:
        abs_parts.append(f"摘要（中）：{az}")
    if abs_parts:
        _add("abstract", "\n".join(abs_parts))

    body = (rec.get("body_text") or "").strip()
    if body:
        _add("body", "正文（PMC开放获取）：\n" + body)

    if not out:
        _add("mixed", title_zh or title_en or f"PMID:{pmid}")
    return out


def expand_queries(query: str, *, enabled: bool | None = None) -> list[str]:
    """中英/专名扩写；返回去重后的查询列表（原查询在首）。"""
    use = PUBMED_YES_QUERY_EXPAND if enabled is None else enabled
    q = (query or "").strip()
    if not q:
        return []
    if not use:
        return [q]
    seen: set[str] = set()
    out: list[str] = []

    def _push(s: str) -> None:
        s = " ".join((s or "").split())
        if not s or s in seen:
            return
        seen.add(s)
        out.append(s)

    _push(q)
    for pat, extras in _EXPAND_RULES:
        if pat.search(q):
            for e in extras:
                _push(e)
    latin = re.findall(r"[A-Za-z][A-Za-z0-9\-]{1,20}", q)
    if re.search(r"[\u4e00-\u9fff]", q) and latin:
        _push("zebrafish " + " ".join(latin[:6]))
    return out[:5]


def keyword_title_boost(query: str, text: str, title_zh: str | None = None) -> float:
    """查询词落在标题中时额外加权（乘到 field weight 上）。返回 >=1.0 的因子。"""
    q = (query or "").strip().lower()
    if not q:
        return 1.0
    title = ((title_zh or "") + "\n" + (text or "")[:300]).lower()
    tokens = [t for t in re.split(r"[\s，。、；;,/]+", q) if len(t) >= 2]
    specials = []
    for t in tokens:
        if re.match(r"^[a-z]{2,}\d*$", t, re.I) or t in ("线粒体", "缺氧", "低氧", "hif"):
            specials.append(t)
    hit = 0
    for t in specials or tokens[:4]:
        if t.lower() in title:
            hit += 1
    if hit <= 0:
        return 1.0
    return 1.0 + 0.25 * min(hit, 3)
