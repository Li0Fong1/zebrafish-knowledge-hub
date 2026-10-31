# -*- coding: utf-8 -*-
"""文献 txt 文件名：序号 + 来源 + ID + 标题片段，便于区分。"""
from __future__ import annotations

import re
from typing import Optional

_INVALID = re.compile(r'[<>:"/\\|?*]')
_HTML = re.compile(r"<[^>]+>")


def safe_token(s: str, max_len: int = 40) -> str:
    s = _INVALID.sub("_", s.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] if s else "unknown"


def title_slug(title: str, *, max_words: int = 10, max_len: int = 72) -> str:
    """标题 → 可读文件名片段（多词下划线连接）。"""
    t = _HTML.sub("", title or "")
    t = re.sub(r"[^\w\s-]", " ", t)
    words = [w for w in t.split() if w][:max_words]
    slug = "_".join(words)
    slug = re.sub(r"_+", "_", slug).strip("_")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("_")
    return slug or "untitled"


def extract_record_id(
    *,
    pmid: str = "",
    wos_ut: str = "",
    doi: str = "",
    id_label: str = "",
    meta_line: str = "",
) -> str:
    if pmid:
        return safe_token(pmid, 24)
    if wos_ut:
        return safe_token(wos_ut, 28)
    if doi:
        return safe_token(doi.replace("/", "_"), 32)
    for text in (id_label, meta_line):
        m = re.search(r"PMID:(\S+)", text)
        if m:
            return safe_token(m.group(1), 24)
        m = re.search(r"WOS:(\S+)", text)
        if m:
            return safe_token(m.group(1), 28)
        m = re.search(r"DOI:(\S+)", text)
        if m:
            return safe_token(m.group(1).replace("/", "_"), 32)
    return "noid"


def index_prefix(index: int, *, total: int = 0) -> str:
    """序号宽度：≤999 用 3 位，≥1000 用 4 位（上限 9999）。"""
    width = 4 if max(index, total) >= 1000 else 3
    return f"{index:0{width}d}"


def build_literature_filename(
    index: int,
    source: str,
    record_id: str,
    title: str,
    *,
    total: int = 0,
) -> str:
    """
    示例：003_PUBMED_18022944_Morphological_types_connectivity_horizontal_cells_zebrafish_retina.txt
    """
    src = safe_token((source or "UNK").upper(), 12)
    rid = safe_token(record_id, 32)
    slug = title_slug(title)
    return f"{index_prefix(index, total=total)}_{src}_{rid}_{slug}.txt"


def build_literature_filename_from_dict(art: dict, index: int, *, total: int = 0) -> str:
    return build_literature_filename(
        index,
        art.get("source") or "unknown",
        extract_record_id(
            pmid=str(art.get("pmid") or ""),
            wos_ut=str(art.get("wos_ut") or ""),
            doi=str(art.get("doi") or ""),
            id_label=str(art.get("id_label") or ""),
        ),
        str(art.get("title") or ""),
        total=total,
    )
