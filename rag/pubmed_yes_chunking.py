# -*- coding: utf-8 -*-
"""YES 文献：分字段切块、字段权重、查询扩写（自然段 + 短语义块试跑参数）。"""
from __future__ import annotations

import re
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import (
    PUBMED_YES_CHUNK_OVERLAP,
    PUBMED_YES_CHUNK_SIZE,
    PUBMED_YES_FIELD_WEIGHT_ABSTRACT,
    PUBMED_YES_FIELD_WEIGHT_BODY,
    PUBMED_YES_FIELD_WEIGHT_CONCLUSION,
    PUBMED_YES_FIELD_WEIGHT_DISCUSSION,
    PUBMED_YES_FIELD_WEIGHT_FIGURE_CAPTIONS,
    PUBMED_YES_FIELD_WEIGHT_INTRODUCTION,
    PUBMED_YES_FIELD_WEIGHT_KEYWORDS,
    PUBMED_YES_FIELD_WEIGHT_META,
    PUBMED_YES_FIELD_WEIGHT_METHODS,
    PUBMED_YES_FIELD_WEIGHT_MIXED,
    PUBMED_YES_FIELD_WEIGHT_OTHER,
    PUBMED_YES_FIELD_WEIGHT_REFERENCES,
    PUBMED_YES_FIELD_WEIGHT_RESULTS,
    PUBMED_YES_FIELD_WEIGHT_TITLE,
    PUBMED_YES_PARA_MAX_CHARS,
    PUBMED_YES_PARA_MIN_CHARS,
    PUBMED_YES_QUERY_EXPAND,
    PUBMED_YES_SECTION_CHUNKING,
    PUBMED_YES_SPLIT_MODE,
)
from rag.paragraph_split import split_natural_paragraphs
from rag.text_clean import clean_scientific_text

_PARA_MAX = int(PUBMED_YES_PARA_MAX_CHARS)
_PARA_MIN = int(PUBMED_YES_PARA_MIN_CHARS)

FIELD_WEIGHTS: dict[str, float] = {
    "title": float(PUBMED_YES_FIELD_WEIGHT_TITLE),
    "abstract": float(PUBMED_YES_FIELD_WEIGHT_ABSTRACT),
    "body": float(PUBMED_YES_FIELD_WEIGHT_BODY),
    "meta": float(PUBMED_YES_FIELD_WEIGHT_META),
    "mixed": float(PUBMED_YES_FIELD_WEIGHT_MIXED),
    "conclusion": float(PUBMED_YES_FIELD_WEIGHT_CONCLUSION),
    "results": float(PUBMED_YES_FIELD_WEIGHT_RESULTS),
    "discussion": float(PUBMED_YES_FIELD_WEIGHT_DISCUSSION),
    "methods": float(PUBMED_YES_FIELD_WEIGHT_METHODS),
    "introduction": float(PUBMED_YES_FIELD_WEIGHT_INTRODUCTION),
    "references": float(PUBMED_YES_FIELD_WEIGHT_REFERENCES),
    "figure_captions": float(PUBMED_YES_FIELD_WEIGHT_FIGURE_CAPTIONS),
    "other": float(PUBMED_YES_FIELD_WEIGHT_OTHER),
    "keywords": float(PUBMED_YES_FIELD_WEIGHT_KEYWORDS),
}

# 正文 section 标签（检索权重键 = field）
SECTION_BODY_FIELDS = (
    "introduction",
    "methods",
    "results",
    "discussion",
    "conclusion",
    "references",
    "figure_captions",
    "other",
    "keywords",
)

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


def _chunk_cap(n: int | None) -> int | None:
    """0 / 负数 / None → 不截断。"""
    if n is None:
        return None
    try:
        v = int(n)
    except (TypeError, ValueError):
        return None
    return None if v <= 0 else v


def field_weight(field: str | None, section: str | None = None) -> float:
    for raw in (field, section):
        if not raw:
            continue
        key = str(raw).strip().lower()
        if key in FIELD_WEIGHTS:
            return float(FIELD_WEIGHTS[key])
    return float(FIELD_WEIGHTS["mixed"])


def adjust_l2_score(raw_score: float, field: str | None, section: str | None = None) -> float:
    w = max(0.1, field_weight(field, section))
    return float(raw_score) / w


def infer_field_from_text(text: str, meta: dict | None = None) -> str:
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
        return "title"
    if has_body:
        return "body"
    if has_meta_only:
        return "meta"
    if has_abs:
        return "abstract"
    return "mixed"


def split_record_to_section_chunks(
    rec: dict[str, Any],
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
    max_body_chars: int | None = None,
    max_chunks: int | None = None,
    split_mode: str | None = None,
    para_max_chars: int | None = None,
    para_min_chars: int | None = None,
) -> list[dict[str, Any]]:
    """
    优先按 fulltext_sections 切 section，field=section_key；失败则整篇 body。
    供旁路 A/B 与阶段 C 重嵌使用；不改现网默认切块行为。
    """
    from rag.fulltext_sections import MISSING, build_section_payload

    pmid = str(rec.get("pmid") or "")
    title_zh = (rec.get("title_zh") or rec.get("title_en") or "").strip()
    title_en = (rec.get("title_en") or "").strip()
    mode = (split_mode or PUBMED_YES_SPLIT_MODE or "paragraph").strip().lower()
    ov = int(overlap if overlap is not None else PUBMED_YES_CHUNK_OVERLAP)
    cs = int(chunk_size if chunk_size is not None else PUBMED_YES_CHUNK_SIZE)
    pmax = int(para_max_chars if para_max_chars is not None else _PARA_MAX)
    pmin = int(para_min_chars if para_min_chars is not None else _PARA_MIN)
    max_chunks = _chunk_cap(max_chunks)
    max_body_chars = _chunk_cap(max_body_chars)
    sp = _splitter(cs, ov)
    out: list[dict[str, Any]] = []
    ci = 0

    def _pieces(field: str, body: str) -> list[str]:
        body = clean_scientific_text(body or "").strip()
        if not body:
            return []
        if field == "title":
            return [body]
        if mode == "paragraph":
            return split_natural_paragraphs(body, max_chars=pmax, min_chars=pmin, overlap=ov)
        return sp.split_text(body) if len(body) > cs else [body]

    def _add(field: str, body: str, *, section: str | None = None) -> None:
        nonlocal ci
        for piece in _pieces(field, body):
            text = f"{_header(rec)}\n{piece}"
            out.append(
                {
                    "text": text,
                    "field": field,
                    "section": section or field,
                    "pmid": pmid,
                    "entry_no": str(rec.get("entry_no") or ""),
                    "title_zh": title_zh,
                    "has_fulltext": bool(rec.get("has_fulltext")),
                    "chunk_index": ci,
                    "source": "pubmed_yes" if pmid.isdigit() else "exist_eno",
                }
            )
            ci += 1
            if max_chunks is not None and ci >= int(max_chunks):
                return

    title_block = "\n".join(
        x
        for x in (
            f"标题（英）：{title_en}" if title_en else "",
            f"标题（中）：{title_zh}" if title_zh else "",
        )
        if x
    )
    _add("title", title_block)
    if max_chunks is not None and ci >= int(max_chunks):
        return out

    meta = _meta_line(rec)
    if meta:
        _add("meta", meta)
    if max_chunks is not None and ci >= int(max_chunks):
        return out

    ae = (rec.get("abstract_en") or "").strip()
    az = (rec.get("abstract_zh") or "").strip()
    abs_parts = []
    if ae:
        abs_parts.append(f"摘要（英）：{ae}")
    if az:
        abs_parts.append(f"摘要（中）：{az}")
    if abs_parts:
        _add("abstract", "\n".join(abs_parts))
    if max_chunks is not None and ci >= int(max_chunks):
        return out

    body = (rec.get("body_text") or "").strip()
    if max_body_chars is not None and body and len(body) > int(max_body_chars):
        body = body[: int(max_body_chars)]

    sectioned = False
    if body:
        payload = build_section_payload(
            body,
            title=title_en or title_zh,
            content_type=str(rec.get("content_type") or ""),
            jats_analysis=rec.get("jats_analysis") if isinstance(rec.get("jats_analysis"), dict) else None,
        )
        sections = payload.get("sections") or {}
        if payload.get("sectioned"):
            sectioned = True
            for key in SECTION_BODY_FIELDS:
                raw = (sections.get(key) or "").strip()
                if not raw or raw == MISSING:
                    continue
                label = key.replace("_", " ")
                _add(key, f"{label}：\n{raw}", section=key)
                if max_chunks is not None and ci >= int(max_chunks):
                    return out
        if not sectioned:
            _add("body", "正文：\n" + body, section="body")

    if not out:
        _add("mixed", title_zh or title_en or f"PMID:{pmid or rec.get('entry_no')}", section="mixed")
    return out


def split_record_to_field_chunks(
    rec: dict[str, Any],
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
    max_body_chars: int | None = None,
    max_chunks: int | None = None,
    split_mode: str | None = None,
    para_max_chars: int | None = None,
    para_min_chars: int | None = None,
    use_sections: bool | None = None,
) -> list[dict[str, Any]]:
    """
    按字段分别切块并打 field 标签。

    默认 split_mode=paragraph：自然段优先；过长段再按句切到 para_max_chars。
    短语义块试跑默认 para_max≈360（约 3～5 句中文）。
    use_sections=True 或 PUBMED_YES_SECTION_CHUNKING=1 时走 section 切块。
    """
    if use_sections is None:
        use_sections = bool(PUBMED_YES_SECTION_CHUNKING)
    if use_sections:
        return split_record_to_section_chunks(
            rec,
            chunk_size=chunk_size,
            overlap=overlap,
            max_body_chars=max_body_chars,
            max_chunks=max_chunks,
            split_mode=split_mode,
            para_max_chars=para_max_chars,
            para_min_chars=para_min_chars,
        )

    pmid = str(rec.get("pmid") or "")
    title_zh = (rec.get("title_zh") or rec.get("title_en") or "").strip()
    title_en = (rec.get("title_en") or "").strip()
    mode = (split_mode or PUBMED_YES_SPLIT_MODE or "paragraph").strip().lower()
    ov = int(overlap if overlap is not None else PUBMED_YES_CHUNK_OVERLAP)
    cs = int(chunk_size if chunk_size is not None else PUBMED_YES_CHUNK_SIZE)
    pmax = int(para_max_chars if para_max_chars is not None else _PARA_MAX)
    pmin = int(para_min_chars if para_min_chars is not None else _PARA_MIN)
    max_chunks = _chunk_cap(max_chunks)
    max_body_chars = _chunk_cap(max_body_chars)
    sp = _splitter(cs, ov)
    out: list[dict[str, Any]] = []
    ci = 0

    def _pieces(field: str, body: str) -> list[str]:
        body = clean_scientific_text(body or "").strip()
        if not body:
            return []
        if field == "title":
            return [body]
        if mode == "paragraph":
            return split_natural_paragraphs(body, max_chars=pmax, min_chars=pmin, overlap=ov)
        return sp.split_text(body) if len(body) > cs else [body]

    def _add(field: str, body: str) -> None:
        nonlocal ci
        for piece in _pieces(field, body):
            text = f"{_header(rec)}\n{piece}"
            out.append(
                {
                    "text": text,
                    "field": field,
                    "pmid": pmid,
                    "entry_no": str(rec.get("entry_no") or ""),
                    "title_zh": title_zh,
                    "has_fulltext": bool(rec.get("has_fulltext")),
                    "chunk_index": ci,
                    "source": "pubmed_yes" if pmid.isdigit() else "exist_eno",
                }
            )
            ci += 1
            if max_chunks is not None and ci >= int(max_chunks):
                return

    title_block = "\n".join(
        x
        for x in (
            f"标题（英）：{title_en}" if title_en else "",
            f"标题（中）：{title_zh}" if title_zh else "",
        )
        if x
    )
    _add("title", title_block)
    if max_chunks is not None and ci >= int(max_chunks):
        return out

    meta = _meta_line(rec)
    if meta:
        _add("meta", meta)
    if max_chunks is not None and ci >= int(max_chunks):
        return out

    ae = (rec.get("abstract_en") or "").strip()
    az = (rec.get("abstract_zh") or "").strip()
    abs_parts = []
    if ae:
        abs_parts.append(f"摘要（英）：{ae}")
    if az:
        abs_parts.append(f"摘要（中）：{az}")
    if abs_parts:
        _add("abstract", "\n".join(abs_parts))
    if max_chunks is not None and ci >= int(max_chunks):
        return out

    body = (rec.get("body_text") or "").strip()
    if body:
        if max_body_chars is not None and len(body) > int(max_body_chars):
            body = body[: int(max_body_chars)]
        _add("body", "正文：\n" + body)

    if not out:
        _add("mixed", title_zh or title_en or f"PMID:{pmid or rec.get('entry_no')}")
    return out


def _header(rec_or_pmid: Any) -> str:
    if isinstance(rec_or_pmid, dict):
        pmid = str(rec_or_pmid.get("pmid") or "").strip()
        eno = str(rec_or_pmid.get("entry_no") or "").strip()
        if pmid and pmid.isdigit():
            return f"【PUBMED YES PMID:{pmid}】"
        if eno:
            return f"【PDFKU ENO:{eno}】"
        doc_id = str(rec_or_pmid.get("doc_id") or "").strip()
        if doc_id:
            return f"【DOC:{doc_id}】"
        return "【LITERATURE】"
    pmid = str(rec_or_pmid or "?").strip()
    if pmid.isdigit():
        return f"【PUBMED YES PMID:{pmid}】"
    return f"【DOC:{pmid}】"


def _meta_line(rec: dict[str, Any]) -> str:
    parts: list[str] = []
    if rec.get("entry_no"):
        parts.append(f"ENO:{rec['entry_no']}")
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


def expand_queries(query: str, *, enabled: bool | None = None) -> list[str]:
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


def topical_match_boost(query: str, text: str) -> float:
    """主题词命中加权；未命中略降。"""
    q = (query or "").lower()
    t = (text or "").lower()
    keys = []
    if re.search(r"hif|缺氧|低氧|hypoxia", q, re.I):
        keys += ["hif", "hypoxia", "缺氧", "低氧"]
    if re.search(r"线粒体|mitochondri", q, re.I):
        keys += ["mitochondri", "线粒体"]
    if not keys:
        return 1.0
    hit = any(k in t for k in keys)
    return 1.85 if hit else 0.72


def keyword_title_boost(query: str, text: str, title_zh: str | None = None) -> float:
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
