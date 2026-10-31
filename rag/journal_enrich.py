# -*- coding: utf-8 -*-
"""文献期刊名补全：本地库 → PubMed XML/API → Crossref → 题名检索 → 占位（保证非空）。

写回 literature_paper.journal，供脚注 100% 展示刊名。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

_EMAIL = os.getenv("NCBI_EMAIL", "zebrafish-hub@local")
_TOOL = os.getenv("NCBI_TOOL", "zebrafish_gene_qa")
_UA = f"{_TOOL}/1.0 (mailto:{_EMAIL})"
_CACHE: dict[str, str] = {}
# 负缓存：查不到的也记下来（带 TTL），否则同一条会在每次回答里反复联网：
# 之前失败不缓存 → 每条引用每次都要重试 Crossref/PubMed，叠加 0.34s 强制间隔，
# 一次回答能白等好几秒。
_MISS: dict[str, float] = {}
_MISS_TTL = float(os.getenv("JOURNAL_ENRICH_MISS_TTL_SEC", "21600"))  # 6h
_LAST_HTTP = 0.0
_MIN_GAP = float(os.getenv("JOURNAL_ENRICH_GAP_SEC", "0.34"))
_HTTP_TIMEOUT = float(os.getenv("JOURNAL_ENRICH_TIMEOUT_SEC", "6.0"))

# 最终兜底：仍是「刊名字符串」，满足 100% 非空展示；后台可再扫
_FALLBACK_JOURNAL = "Unlisted journal"


def _miss(key: str) -> bool:
    ts = _MISS.get(key)
    if ts is None:
        return False
    if (time.time() - ts) > _MISS_TTL:
        _MISS.pop(key, None)
        return False
    return True


def _mark_miss(key: str) -> None:
    if len(_MISS) > 20000:      # 防无限增长
        _MISS.clear()
    _MISS[key] = time.time()


def _throttle() -> None:
    global _LAST_HTTP
    gap = _MIN_GAP - (time.time() - _LAST_HTTP)
    if gap > 0:
        time.sleep(gap)
    _LAST_HTTP = time.time()


def _http_get(url: str, timeout: float | None = None) -> bytes:
    _throttle()
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=(_HTTP_TIMEOUT if timeout is None else timeout)) as resp:
        return resp.read()


def _norm_journal(raw: str) -> str:
    j = (raw or "").strip()
    j = (
        j.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    j = re.sub(r"\s+", " ", j).strip(" .;,")
    if not j:
        return ""
    # 拒绝把 DOI/URL 当成刊名
    low = j.lower()
    if low.startswith("http") or low.startswith("doi") or re.match(r"^10\.\d{4,}/", j):
        return ""
    if len(j) < 2:
        return ""
    return j[:240]


def _norm_doi(doi: str) -> str:
    raw = (doi or "").strip()
    raw = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", raw)
    raw = raw.strip(" .;")
    if re.match(r"^10\.\d{4,}/\S+$", raw):
        return raw
    return ""


def journal_from_pubmed_xml(raw_xml: str) -> str:
    if not raw_xml or "<" not in raw_xml:
        return ""
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        # 有时是拼接多条 Article，抓第一个 Journal
        m = re.search(
            r"<Journal>(.*?)</Journal>",
            raw_xml,
            flags=re.I | re.S,
        )
        if not m:
            return ""
        try:
            root = ET.fromstring("<Journal>" + m.group(1) + "</Journal>")
        except ET.ParseError:
            return ""
    # ISOAbbreviation 优先（短刊名），否则 Title
    for path in (
        ".//ISOAbbreviation",
        ".//MedlineTA",
        ".//Journal/Title",
        ".//Title",
    ):
        node = root.find(path)
        if node is not None and (node.text or "").strip():
            j = _norm_journal(node.text or "")
            if j:
                return j
    # 正则兜底
    for pat in (
        r"<ISOAbbreviation>([^<]+)</ISOAbbreviation>",
        r"<MedlineTA>([^<]+)</MedlineTA>",
        r"<Journal>.*?<Title>([^<]+)</Title>",
    ):
        m = re.search(pat, raw_xml, flags=re.I | re.S)
        if m:
            j = _norm_journal(m.group(1))
            if j:
                return j
    return ""


def fetch_journal_pubmed_pmid(pmid: str) -> str:
    pid = str(pmid or "").strip()
    if not pid.isdigit():
        return ""
    ck = f"pmid:{pid}"
    if ck in _CACHE:
        return _CACHE[ck]
    if _miss(ck):
        return ""
    try:
        url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            f"?db=pubmed&id={pid}&retmode=json&tool={urllib.parse.quote(_TOOL)}"
            f"&email={urllib.parse.quote(_EMAIL)}"
        )
        data = json.loads(_http_get(url).decode("utf-8", "replace"))
        result = (data.get("result") or {}).get(pid) or {}
        j = _norm_journal(str(result.get("fulljournalname") or result.get("source") or ""))
        if j:
            _CACHE[ck] = j
            return j
    except Exception:
        pass
    try:
        url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
            f"?db=pubmed&id={pid}&retmode=xml&tool={urllib.parse.quote(_TOOL)}"
            f"&email={urllib.parse.quote(_EMAIL)}"
        )
        j = journal_from_pubmed_xml(_http_get(url).decode("utf-8", "replace"))
        if j:
            _CACHE[ck] = j
            return j
    except Exception:
        pass
    _mark_miss(ck)
    return ""


def fetch_journal_crossref_doi(doi: str) -> str:
    d = _norm_doi(doi)
    if not d:
        return ""
    ck = f"doi:{d.lower()}"
    if ck in _CACHE:
        return _CACHE[ck]
    if _miss(ck):
        return ""
    try:
        url = "https://api.crossref.org/works/" + urllib.parse.quote(d)
        data = json.loads(_http_get(url).decode("utf-8", "replace"))
        msg = data.get("message") or {}
        container = msg.get("container-title") or msg.get("short-container-title") or []
        if isinstance(container, list) and container:
            j = _norm_journal(str(container[0]))
            if j:
                _CACHE[ck] = j
                return j
        j = _norm_journal(str(msg.get("publisher") or ""))
        if j:
            _CACHE[ck] = j
            return j
    except Exception:
        pass
    _mark_miss(ck)
    return ""


def fetch_journal_crossref_title(title: str, year: str = "") -> str:
    t = re.sub(r"\s+", " ", (title or "").strip())
    if len(t) < 12:
        return ""
    ck = f"title:{t[:80].lower()}"
    if ck in _CACHE:
        return _CACHE[ck]
    if _miss(ck):
        return ""
    try:
        q = urllib.parse.urlencode({"query.bibliographic": t, "rows": "1"})
        url = "https://api.crossref.org/works?" + q
        data = json.loads(_http_get(url).decode("utf-8", "replace"))
        items = ((data.get("message") or {}).get("items")) or []
        if not items:
            return ""
        item = items[0]
        # 粗校验：题名词重叠
        it = " ".join(item.get("title") or []).lower()
        words = [w for w in re.findall(r"[a-z0-9]{4,}", t.lower())][:8]
        if words:
            hit = sum(1 for w in words if w in it)
            if hit < max(2, len(words) // 3):
                return ""
        if year:
            ys = str(year).strip()[:4]
            issued = (((item.get("issued") or {}).get("date-parts") or [[None]])[0] or [None])[0]
            if ys.isdigit() and issued and abs(int(ys) - int(issued)) > 2:
                return ""
        container = item.get("container-title") or item.get("short-container-title") or []
        if isinstance(container, list) and container:
            j = _norm_journal(str(container[0]))
            if j:
                _CACHE[ck] = j
                return j
    except Exception:
        pass
    _mark_miss(ck)
    return ""


def journal_from_local_pubmed_xml(pmid: str) -> str:
    pid = str(pmid or "").strip()
    if not pid.isdigit():
        return ""
    try:
        from mysql_client import get_mysql_client

        rows = get_mysql_client().execute_sql(
            f"SELECT raw_xml FROM literature_pubmed WHERE pmid='{pid}' LIMIT 1",
            max_rows=1,
        )
        data = (rows or {}).get("data") or []
        if not data:
            return ""
        return journal_from_pubmed_xml(str(data[0].get("raw_xml") or ""))
    except Exception:
        return ""


def persist_journal(
    *,
    journal: str,
    pmid: str = "",
    doi: str = "",
    entry_no: str = "",
    doc_id: str = "",
) -> bool:
    j = _norm_journal(journal)
    if not j or j == _FALLBACK_JOURNAL:
        # 兜底也写库，避免重复打外网；可后续人工扫
        if not j:
            return False
    try:
        from mysql_client import get_mysql_client

        clauses: list[str] = []
        if str(pmid).strip().isdigit():
            clauses.append(f"pmid='{str(pmid).strip()}'")
        d = _norm_doi(doi)
        if d:
            safe = d.replace("'", "''")
            clauses.append(f"doi='{safe}'")
        e = str(entry_no or "").strip()
        if e and re.fullmatch(r"[A-Za-z0-9_-]+", e):
            clauses.append(f"entry_no='{e}'")
        doc = str(doc_id or "").strip()
        if doc and re.fullmatch(r"[A-Za-z0-9_:-]+", doc):
            clauses.append(f"doc_id='{doc}'")
        if not clauses:
            return False
        safe_j = j.replace("\\", "\\\\").replace("'", "''")
        sql = (
            "UPDATE literature_paper SET journal='"
            + safe_j
            + "' WHERE ("
            + " OR ".join(clauses)
            + ") AND (journal IS NULL OR TRIM(journal)='')"
        )
        out = get_mysql_client().execute_write(sql)
        return bool(out.get("success"))
    except Exception:
        return False


def resolve_journal(
    *,
    journal: str = "",
    pmid: str = "",
    doi: str = "",
    title: str = "",
    year: str = "",
    entry_no: str = "",
    doc_id: str = "",
    allow_network: bool = True,
    persist: bool = True,
) -> str:
    """返回非空刊名字符串。"""
    j = _norm_journal(journal)
    if j:
        return j

    # 1) 本地 PubMed XML
    if pmid:
        j = journal_from_local_pubmed_xml(pmid)
        if j:
            if persist:
                persist_journal(journal=j, pmid=pmid, doi=doi, entry_no=entry_no, doc_id=doc_id)
            return j

    if allow_network:
        # 2) Crossref DOI
        if doi:
            j = fetch_journal_crossref_doi(doi)
            if j:
                if persist:
                    persist_journal(journal=j, pmid=pmid, doi=doi, entry_no=entry_no, doc_id=doc_id)
                return j
        # 3) PubMed API
        if pmid:
            j = fetch_journal_pubmed_pmid(pmid)
            if j:
                if persist:
                    persist_journal(journal=j, pmid=pmid, doi=doi, entry_no=entry_no, doc_id=doc_id)
                return j
        # 4) Crossref 题名
        if title:
            j = fetch_journal_crossref_title(title, year=year)
            if j:
                if persist:
                    persist_journal(journal=j, pmid=pmid, doi=doi, entry_no=entry_no, doc_id=doc_id)
                return j

    return _FALLBACK_JOURNAL


def original_links(
    *,
    title: str = "",
    journal: str = "",
    year: str = "",
    doi: str = "",
    pmid: str = "",
) -> list[tuple[str, str]]:
    """返回 (标签, url)：有 DOI/PMID 只给「原文」；否则给检索备选（不单独挂 PubMed 字样）。"""
    links: list[tuple[str, str]] = []
    d = _norm_doi(doi)
    if d:
        # 有 DOI：只保留原文，后面不再跟 PubMed / Scholar 等标签
        links.append(("原文", "https://doi.org/" + d))
        return links

    if pmid and str(pmid).strip().isdigit():
        # 无 DOI 有 PMID：用「原文」指向 PubMed 页，不显示「PubMed」字样
        links.append(("原文", f"https://pubmed.ncbi.nlm.nih.gov/{str(pmid).strip()}/"))
        return links

    q_parts = [p for p in (title, journal, year) if p]
    q = re.sub(r"\s+", " ", " ".join(q_parts).strip())[:220]
    enc = urllib.parse.quote_plus(q) if q else ""
    if enc:
        links.append(("Google", f"https://www.google.com/search?q={enc}"))
        links.append(("Scholar", f"https://scholar.google.com/scholar?q={enc}"))
        links.append(("EuropePMC", f"https://europepmc.org/search?query={enc}"))
    return links


def enrich_hits(hits: list[dict], *, allow_network: bool = True, persist: bool = True) -> None:
    """就地补全 hit['journal']。"""
    for h in hits or []:
        if _norm_journal(str(h.get("journal") or "")):
            continue
        title = str(h.get("title_en") or h.get("title") or h.get("title_zh") or "").strip()
        j = resolve_journal(
            journal=str(h.get("journal") or ""),
            pmid=str(h.get("pmid") or ""),
            doi=str(h.get("doi") or ""),
            title=title,
            year=str(h.get("year") or ""),
            entry_no=str(h.get("entry_no") or ""),
            doc_id=str(h.get("doc_id") or ""),
            allow_network=allow_network,
            persist=persist,
        )
        h["journal"] = j
