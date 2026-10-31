# -*- coding: utf-8 -*-
"""领域元数据问题的 PubMed 实时文献补充（L7）

背景
----
本地文献库（FAISS）对「基因组组装 / 版本沿革 / 资源总览」这类**领域元数据**问题
覆盖有限，检索出来的多是泛化结果（如某基因的转录组研究）。此前的做法是
「宁缺毋滥」——不附文献，但用户会觉得答案不完整、不科学。

本模块用 NCBI E-utilities 实时检索**官方索引**，返回带真实 PMID 的文献条目，
由调用方在回答中以「PubMed 官方索引实时检索」明确标注，
**不冒充本地文献库证据**，也不编造引用。

特性
----
- esearch + esummary（JSON），限定 Title/Abstract，按相关度排序
- 主题识别（组装 / 数据库 / 注释 / 测序）自动生成英文检索式
- 7 天磁盘缓存（`$ZEBRA_RUNTIME/logs/cache/pubmed_live_refs.json`），避免重复请求
- 任何网络/解析异常均静默降级为 `[]`，绝不阻塞回答
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CACHE_TTL = 7 * 24 * 3600
TIMEOUT = 12

_CACHE: Dict[str, Any] = {}
_LOADED = False

# 主题定义： (问题关键词, 检索式, 题名必须命中的核心词)
# “核心词”用于二次过滤——例如组装类必须题名带 genome，
# 否则 "MultiSite Assembly of Gateway Induced Clones (MAGIC)" 这类
# 仅含 Assembly 一词的克隆工具箱文献会被误当组装文献。
_TOPICS = [
    (
        ("组装", "版本", "grcz", "参考基因组", "assembly", "genome version", "reference genome"),
        '("Danio rerio" OR zebrafish)[Title] AND '
        '(genome assembly OR reference genome OR reference sequence OR GRCz11 OR GRCz12 '
        'OR genome version OR karyotype)[Title]',
        ("genome", "grcz", "karyotype", "chromosom"),
    ),
    (
        ("数据库", "资源", "组学库", "database", "portal", "resource", "knowledgebase"),
        '("Danio rerio" OR zebrafish)[Title] AND '
        '(database OR resource OR portal OR knowledgebase)[Title]',
        ("database", "resource", "portal", "knowledgebase", "knowledge base", "information network", "ontolog", "alliance"),
    ),
    (
        ("注释", "annotation", "go 注释", "kegg", "通路"),
        '("Danio rerio" OR zebrafish)[Title] AND '
        '(annotation OR gene ontology OR KEGG OR pathway)[Title]',
        ("annotation", "gene ontology", "ontolog", "kegg", "pathway"),
    ),
    (
        ("测序", "转录组", "芯片", "rna-seq", "sequencing", "transcriptome"),
        '("Danio rerio" OR zebrafish)[Title] AND '
        '(transcriptome OR RNA-seq OR sequencing)[Title]',
        ("transcriptom", "rna-seq", "sequencing"),
    ),
]

_FALLBACK = (
    '("Danio rerio" OR zebrafish)[Title] AND '
    '(genome OR annotation OR database)[Title]'
)

# 题名相关性短语锚点（备用二次保险，按短语而非单词匹配）
_TITLE_ANCHORS = (
    "genome assembly", "reference genome", "genome sequence", "genome project",
    "genome version", "grcz", "karyotype", "assembl", "chromosome-level",
    "database", "resource", "portal", "knowledgebase", "knowledge base",
    "annotation", "gene ontology", "ontolog", "kegg", "pathway",
    "transcriptom", "rna-seq", "sequencing", "expression atlas",
)


# 组学锚点：除主题短语外，题名还需带至少一个组学词，
# 防止 "...an expanded resource for translatable CircRNAs" 这类仅含 resource 的文献混入
_GENOMICS_ANCHORS = (
    "genome", "genomic", "gene", "zebrafish", "danio", "annotation",
    "transcriptom", "allele", "variant", "chromosom", "assembly", "grcz",
    "ontolog", "pathway", "expression", "sequence",
)


def _title_relevant(title: str, required: tuple = ()) -> bool:
    t = (title or "").lower()
    if not t or not any(a in t for a in _TITLE_ANCHORS):
        return False
    if not any(g in t for g in _GENOMICS_ANCHORS):
        return False
    if required and not any(r in t for r in required):
        return False
    return True


def _cache_file() -> str:
    rt = os.environ.get("ZEBRA_RUNTIME") or ""
    if not rt:
        return ""
    d = os.path.join(rt, "logs", "cache")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return ""
    return os.path.join(d, "pubmed_live_refs.json")


def _load() -> Dict[str, Any]:
    global _CACHE, _LOADED
    if _LOADED:
        return _CACHE
    _LOADED = True
    p = _cache_file()
    if p and os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                _CACHE = json.load(f) or {}
        except Exception:
            _CACHE = {}
    return _CACHE


def _save() -> None:
    p = _cache_file()
    if not p:
        return
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f, ensure_ascii=False)
    except Exception:
        pass


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishHub/1.0 (research use)"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def topic_for(question: str) -> tuple:
    """返回 (pubmed 检索式, 题名必须命中的核心词)。"""
    q = (question or "").strip()
    if not q:
        return _FALLBACK, ()
    low = q.lower()
    for keys, term, required in _TOPICS:
        if any(k.lower() in low for k in keys):
            return term, required
    return _FALLBACK, ()


def build_queries(question: str) -> List[str]:
    """按问题主题生成 1~2 条英文 PubMed 检索式（向后兼容）。"""
    term, _req = topic_for(question)
    return [term]


def search_refs_for_question(question: str, limit: int = 3) -> List[Dict[str, str]]:
    """按问题主题检索（带主题核心词过滤），推荐入口。"""
    term, required = topic_for(question)
    return search_refs(term, limit=limit, required=required)


def search_refs(term: str, limit: int = 3, required: tuple = ()) -> List[Dict[str, str]]:
    """实时检索 PubMed，返回 [{pmid,title,journal,year}]；失败返回 []。"""
    term = (term or "").strip()
    if not term or limit <= 0:
        return []
    cache = _load()
    key = "{}#{}#{}".format(term, limit, "|".join(required or ()))
    hit = cache.get(key)
    if isinstance(hit, dict) and time.time() - float(hit.get("ts") or 0) < CACHE_TTL:
        return list(hit.get("items") or [])
    try:
        q1 = urllib.parse.urlencode(
            {"db": "pubmed", "term": term, "retmax": max(limit * 6, 20),
             "sort": "relevance", "retmode": "json"}
        )
        sr = _get_json("{}/esearch.fcgi?{}".format(EUTILS, q1))
        raw_ids = (sr.get("esearchresult") or {}).get("idlist") or []
        ids = [str(i) for i in raw_ids if str(i).isdigit()]
        if not ids:
            raise RuntimeError("esearch empty")
        q2 = urllib.parse.urlencode(
            {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
        )
        sm = _get_json("{}/esummary.fcgi?{}".format(EUTILS, q2))
        res = sm.get("result") or {}
        items: List[Dict[str, str]] = []
        for pid in ids:
            r = res.get(pid)
            if not isinstance(r, dict):
                continue
            title = str(r.get("title") or "").strip().rstrip(".")
            if not title or not _title_relevant(title, required):
                continue
            items.append(
                {
                    "pmid": pid,
                    "title": title,
                    "journal": str(r.get("fulljournalname") or r.get("source") or "").strip(),
                    "year": str(r.get("pubdate") or "")[:4].strip(),
                }
            )
            if len(items) >= limit:
                break
    except Exception:
        return []
    cache[key] = {"ts": time.time(), "items": items}
    _save()
    return items
