# -*- coding: utf-8 -*-
"""NCBI / Europe PMC 检索的「斑马鱼范围」统一收口。

背景
----
NCBI 是全球性大库（Entrez 覆盖 PubMed / Gene / Nucleotide / Assembly …），
本平台只服务斑马鱼（*Danio rerio*, taxid **7955**）这一个物种。
如果不做限定，自由文本检索会大量命中人、小鼠、果蝇、斑马鱼近缘种等无关文献，
既浪费配额，也会让答案里混进非斑马鱼的证据。

本模块把「什么叫斑马鱼范围」集中成一处定义，并提供两个注入函数：

* :func:`scope_pubmed` —— 给 PubMed esearch 的自由文本检索式补上物种限定
* :func:`scope_epmc`   —— 给 Europe PMC 的 query 补上物种限定

设计要点
--------
1. **幂等**：检索式里已经带物种限定的，原样返回，不会重复拼接。
2. **不动标识符检索**：``[doi]`` / ``[pmid]`` / ``[uid]`` 这类按唯一标识精确
   取记录的检索，加物种限定反而会让本该命中一篇的查询变成零结果，因此跳过。
3. **Organism 字段优先**：PubMed 的 ``[Organism]`` 由 NCBI 从 MeSH 与物种表
   映射，比作者在摘要里提没提 "zebrafish" 可靠得多；自由文本与 MeSH 作为补充，
   保证老文献（MeSH 未完善）也不会漏。
"""
from __future__ import annotations

import re

# ── 物种身份 ────────────────────────────────────────────────────────────────
ZEBRAFISH_TAXID = 7955
ZEBRAFISH_SCIENTIFIC = "Danio rerio"
ZEBRAFISH_COMMON = "zebrafish"

#: PubMed ``[Organism]`` 限定（NCBI 侧由物种表映射，最可靠）
PUBMED_ORGANISM_CLAUSE = '"Danio rerio"[Organism]'

#: 自由文本 / MeSH 补充限定（覆盖 MeSH 尚未标全的老文献）
PUBMED_FREETEXT_CLAUSE = (
    "zebrafish[Title/Abstract] OR "
    '"Danio rerio"[Title/Abstract] OR '
    "zebrafish[MeSH Terms] OR "
    '"Danio rerio"[MeSH Terms]'
)

#: 完整 PubMed 物种限定（供外部直接引用，如批量采集的总量统计）
PUBMED_ZEBRAFISH_SCOPE = f"({PUBMED_ORGANISM_CLAUSE} OR {PUBMED_FREETEXT_CLAUSE})"

#: 斑马鱼相关的关键词全集 —— 仅在需要「判定一段文本是否斑马鱼语境」时使用。
#: 包含学名、俗名、常用缩写、模式生物库与品系名（品系名常出现在材料方法里）。
ZEBRAFISH_KEYWORDS: tuple[str, ...] = (
    "zebrafish",
    "zebra fish",
    "danio rerio",
    "danio",
    "dre",
    "zfin",
    "zf",
    "tuebingen",
    "tübingen",
    "tubingen",
    "ab wild-type",
    "wild-type ab",
    "casper",
    "golden mutant",
)

# ── 判定用正则 ──────────────────────────────────────────────────────────────
#: 已带物种限定的检索式（避免重复拼接）
_SCOPED_RE = re.compile(
    r"\[organism\]|zebrafish|danio\s+rerio|taxid\s*7955|taxonomy_id\s*7955",
    re.IGNORECASE,
)

#: 标识符检索：按唯一 ID 精确取记录，不应追加物种限定
_IDENTIFIER_RE = re.compile(
    r"\[(doi|pmid|uid|edat|pdat|dp|accession|auid|lid)\]",
    re.IGNORECASE,
)

_KEYWORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in ("zebrafish", "zebra fish", "danio rerio", "zfin")) + r")\b",
    re.IGNORECASE,
)


def is_identifier_query(term: str) -> bool:
    """按唯一标识精确检索（DOI / PMID / 日期范围等）→ True。"""
    return bool(_IDENTIFIER_RE.search(term or ""))


def already_scoped(term: str) -> bool:
    """检索式里是否已含斑马鱼限定。"""
    return bool(_SCOPED_RE.search(term or ""))


def scope_pubmed(term: str) -> str:
    """给 PubMed 检索式补上斑马鱼物种限定（幂等、跳过标识符检索）。

    例::

        >>> scope_pubmed("sox9b CRISPR")
        '(sox9b CRISPR) AND ("Danio rerio"[Organism] OR zebrafish[Title/Abstract] ...)'
        >>> scope_pubmed("10.1038/nature12345[doi]")
        '10.1038/nature12345[doi]'
    """
    t = (term or "").strip()
    if not t:
        return t
    if already_scoped(t):
        return t
    if is_identifier_query(t):
        return t
    return f"({t}) AND {PUBMED_ZEBRAFISH_SCOPE}"


def scope_epmc(query: str) -> str:
    """给 Europe PMC 的 query 补上斑马鱼限定（幂等、跳过标识符检索）。

    Europe PMC 无 ``[Organism]`` 字段，用 ``ORGANISM:`` 与自由文本双保险。
    """
    q = (query or "").strip()
    if not q:
        return q
    if already_scoped(q):
        return q
    if is_identifier_query(q):
        return q
    return f'({q}) AND (ORGANISM:"Danio rerio" OR "Danio rerio" OR zebrafish)'


def looks_zebrafish(text: str) -> bool:
    """粗判一段文本是否斑马鱼语境（用于召回后过滤，不做硬性拦截）。"""
    return bool(_KEYWORD_RE.search(text or ""))
