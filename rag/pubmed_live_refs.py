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
import re
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


# ---------------------------------------------------------------------------
# 通用主题检索（2026-11-08 新增）
#
# 上面的 search_refs_for_question 只覆盖「组装/数据库/注释/测序」四类领域元数据，
# 检索式写死且有题名锚点过滤。实测代价：问「CRISPR-based studies of zebrafish
# morphogenesis」，本地 FAISS 返回 pmids=[]，主题表也匹配不上，于是模型手里
# 一篇可引文献都没有 —— 题面却要求 Provide inline citations，只能靠内部知识空口作答
# （同题另一次甚至直接拒答「本文献集合不含该主题」）。
#
# 这里补一条**不依赖主题表**的通道：把问句里的实词拼成 PubMed 检索式，
# 加斑马鱼约束，按相关度取回真实条目。仍只返回题名/期刊/年份/PMID，不冒充本地库证据。
# ---------------------------------------------------------------------------

# 与 ref_relevance 的实体停用词保持独立：这里只关心"检索式别被虚词污染"
_TOPIC_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "what", "which", "how", "why",
    "when", "where", "does", "did", "are", "is", "was", "were", "has", "have", "had",
    "can", "could", "would", "should", "about", "into", "than", "then", "them", "they",
    "you", "your", "our", "its", "his", "her", "not", "also", "more", "most", "any",
    "all", "list", "show", "give", "tell", "explain", "introduce", "find", "search",
    "please", "zebrafish", "danio", "rerio", "fish", "there", "their", "these", "those",
    "some", "such", "other", "many", "much", "between", "across", "during", "after",
    "before", "using", "use", "used", "based", "study", "studies", "paper", "papers",
    "literature", "report", "reports", "reported", "provide", "answer", "briefly",
    "brief", "english", "citation", "citations", "inline", "please",
    # 投稿/出版类词：会命中「Annual Report on Sex in Preclinical Studies … Publications」
    # 这种与主题无关的统计报告（实测污染「sex determination 有哪些关键文献」）
    "publication", "publications", "journal", "journals", "editorial", "commentary",
    # 泛词：进了检索式只会把主题稀释成"随便什么文献"
    "relationship", "related", "relate", "relates", "exists", "exist", "article",
    "articles", "focus", "focused", "discuss", "discusses", "address", "addresses",
    "key", "main", "common", "commonly", "determine", "determines", "determined",
    "factor", "factors", "role", "roles", "function", "functions", "type", "types",
    "level", "levels", "form", "forms", "model", "models", "approach", "approaches",
    "method", "methods", "evidence", "current", "recent", "new", "novel", "specific",
}


def _topic_keywords(question: str, limit: int = 6) -> List[str]:
    """问句 → 检索关键词（保序去重，去掉虚词与斑马鱼本身）。"""
    q = (question or "").lower()
    q = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", q)          # 去掉括号补注
    q = re.sub(r"[^a-z0-9\-\s]", " ", q)                  # 标点转空格
    out: List[str] = []
    for tok in q.split():
        tok = tok.strip("-")
        # 连字符复合词（crispr-based / rna-seq）按部件分别入式：
        # PubMed 里极少有 "crispr-based"[tiab] 这种写法，整体当检索词会 0 命中
        parts = [x for x in tok.split("-") if x] or [tok]
        for part in parts:
            if len(part) < 3 or part in _TOPIC_STOP:
                continue
            if part.isdigit():
                continue
            if part not in out:
                out.append(part)
            if len(out) >= limit:
                return out
    return out


def build_topic_query(question: str, max_terms: int = 5) -> str:
    """通用检索式：斑马鱼约束 + 实词 OR 组（宽松版，strict 无结果时兜底）。"""
    kws = _topic_keywords(question, limit=max_terms)
    if not kws:
        return ""
    ors = " OR ".join('{}[tiab]'.format(k) for k in kws)
    return '("Danio rerio"[tiab] OR zebrafish[tiab]) AND ({})'.format(ors)


def build_topic_query_strict(question: str, max_terms: int = 2) -> str:
    """精确版：实词 AND 组。

    OR 版实测会把泛词（relationship / scales）放大成主题，召回一堆无关论文；
    先 AND 求精确，命中不足再退回 OR。
    """
    kws = _topic_keywords(question, limit=max_terms)
    if len(kws) < 2:
        return ""
    ands = " AND ".join('{}[tiab]'.format(k) for k in kws)
    return '("Danio rerio"[tiab] OR zebrafish[tiab]) AND ({})'.format(ands)


def build_topic_query_title(question: str, max_terms: int = 3) -> str:
    """最严版：实词全部限定在**题名**里。

    实测（2026-11-08）：问「CRISPR-based studies of zebrafish morphogenesis」，
    用 [tiab] 的 AND 会把「摘要里提过 CRISPR」的普通形态发生论文排到前面，
    题名根本看不出关联；改成题名约束后剩下的正是 CRISPR 方法/应用论文。
    """
    kws = _topic_keywords(question, limit=max_terms)
    if len(kws) < 2:
        return ""
    ands = " AND ".join('{}[ti]'.format(k) for k in kws)
    return '("Danio rerio"[tiab] OR zebrafish[tiab]) AND ({})'.format(ands)


def search_topic_refs(question: str, limit: int = 6) -> List[Dict[str, str]]:
    """通用主题检索：返回 [{pmid,title,journal,year}]；无关键词/失败返回 []。

    三级降级，宁少勿滥：
      1) 全部实词限定题名（最准，通常 0~3 篇）
      2) 实词 AND（题名/摘要共现），题名至少命中 1 个
      3) 实词 OR，题名至少命中 2 个
    任何一级够 2 篇就停。
    """
    kws = _topic_keywords(question)
    if not kws or limit <= 0:
        return []
    for term, min_hits in (
        (build_topic_query_title(question), 2),
        (build_topic_query_strict(question), 1),
        (build_topic_query(question), 2 if len(kws) >= 2 else 1),
    ):
        if not term:
            continue
        items = _topic_search(term, kws, limit, min_hits=min_hits)
        if len(items) >= min(2, limit):
            return items
    return []


def _topic_search(term: str, kws: List[str], limit: int, min_hits: int = 1) -> List[Dict[str, str]]:
    cache = _load()
    # 前缀 v2：过滤/排序口径变了，旧缓存（含泛词召回）不能复用
    key = "topic2#{}#{}#{}".format(term, limit, min_hits)
    hit = cache.get(key)
    if isinstance(hit, dict) and time.time() - float(hit.get("ts") or 0) < CACHE_TTL:
        return list(hit.get("items") or [])
    try:
        q1 = urllib.parse.urlencode(
            {"db": "pubmed", "term": term, "retmax": max(limit * 6, 24),
             "sort": "relevance", "retmode": "json"}
        )
        sr = _get_json("{}/esearch.fcgi?{}".format(EUTILS, q1))
        ids = [str(i) for i in ((sr.get("esearchresult") or {}).get("idlist") or [])
               if str(i).isdigit()]
        if not ids:
            raise RuntimeError("esearch empty")
        q2 = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "json"})
        sm = _get_json("{}/esummary.fcgi?{}".format(EUTILS, q2))
        res = sm.get("result") or {}
        rows: List[tuple] = []
        for pid in ids:
            r = res.get(pid)
            if not isinstance(r, dict):
                continue
            title = str(r.get("title") or "").strip().rstrip(".")
            if not title:
                continue
            # 题名必须命中足够多的实词，否则就是"凑数"老路
            low = title.lower()
            overlap = sum(1 for k in kws if k in low)
            if overlap < max(1, int(min_hits)):
                continue
            rows.append((overlap, len(rows), {
                "pmid": pid,
                "title": title,
                "journal": str(r.get("fulljournalname") or r.get("source") or "").strip(),
                "year": str(r.get("pubdate") or "")[:4].strip(),
            }))
        # 按「题名命中实词个数」从多到少排，同分保持 PubMed relevance 次序。
        # 不排序的后果（实测 Q30 温度题）：PubMed 相关度把一篇《斑马鱼下丘脑发育》
        # 排在第一位（它只命中 development 一个词），模型只能拿它当温度证据引用。
        rows.sort(key=lambda t: (-t[0], t[1]))
        if len(kws) >= 3 and rows and rows[0][0] >= 2:
            rows = [r for r in rows if r[0] >= 2]
        items = [r[2] for r in rows[:limit]]
    except Exception:
        return []
    cache[key] = {"ts": time.time(), "items": items}
    _save()
    return items


def render_pack(items: List[Dict[str, str]]) -> str:
    """把在线检索结果渲染成证据池编号行，供 LLM 正文引用 [n] 与文末成表。

    格式与 `rag/pubmed_yes_retriever.py::_paper_block` 对齐：
        [1] 2024 | PMID:12345678 | pubmed_live
        题名：Title
        期刊：Journal · 年份：2024 · DOI: 无 · https://pubmed.ncbi.nlm.nih.gov/12345678/
    """
    lines: List[str] = []
    for n, it in enumerate(items, start=1):
        pmid = str(it.get("pmid") or "")
        year = str(it.get("year") or "")
        title = str(it.get("title") or "").strip()
        journal = str(it.get("journal") or "").strip()
        if not title or not pmid:
            continue
        lines.append("[{}] {} | PMID:{} | pubmed_live".format(n, year, pmid))
        lines.append("题名：{}".format(title))
        # 期刊/DOI 缺失时不要写「无」：下游脚注解析会把占位词当成刊名，
        # 最终渲染成 "Title. 无 · https://pubmed…"（英文答案里出现中文，机制检查直接判错）
        bits = []
        if journal:
            bits.append("期刊：{}".format(journal))
        if year:
            bits.append("年份：{}".format(year))
        bits.append("PMID：{}".format(pmid))
        bits.append("https://pubmed.ncbi.nlm.nih.gov/{}/".format(pmid))
        lines.append(" · ".join(bits))
        lines.append("")
    return "\n".join(lines).strip()
