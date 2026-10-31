# -*- coding: utf-8 -*-
"""全文下载级联：Europe PMC XML → Unpaywall → OpenAlex → 出版社直链 → 摘要降级。"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Optional

from rag.europepmc_collector import REQUEST_INTERVAL as EPMC_INTERVAL
from rag.literature_collector import CONTACT_EMAIL, _api_get, _sleep
from rag.network_resilience import urlopen_with_retry
from rag.pubmed_corpus_utils import epmc_is_open_access, parse_pubmed_xml

EPMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL_EMAIL = os.getenv("UNPAYWALL_EMAIL", CONTACT_EMAIL)
OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", CONTACT_EMAIL)

PMCID_RE = re.compile(r"(PMC\d+)", re.I)
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.I)


def normalize_pmcid(pmcid: str) -> str:
    p = (pmcid or "").strip().upper()
    if not p:
        return ""
    return p if p.startswith("PMC") else f"PMC{p}"


def normalize_doi(doi: str) -> str:
    d = (doi or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.lower().startswith(prefix):
            d = d[len(prefix):]
    return d.strip().rstrip(".")


def _http_json(url: str, *, timeout: float = 30.0, label: str = "HTTP") -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
    with urlopen_with_retry(req, timeout=timeout, label=label) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _title_similar(a: str, b: str) -> bool:
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (s or "").lower())
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    return short in long or long[: min(len(long), 80)] in short


def ncbi_idconv(identifier: str) -> dict[str, str]:
    if not identifier:
        return {}
    q = urllib.parse.urlencode({"ids": identifier, "format": "json", "versions": "no"})
    url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?{q}"
    try:
        data = _http_json(url, label=f"idconv {identifier[:40]}")
    except Exception:
        return {}
    records = data.get("records") or []
    if not records:
        return {}
    r = records[0]
    return {
        "pmid": str(r.get("pmid") or "").strip(),
        "pmcid": normalize_pmcid(str(r.get("pmcid") or "").strip()),
        "doi": normalize_doi(str(r.get("doi") or "").strip()),
        "status": str(r.get("status") or "").strip(),
    }


def esearch_pubmed(term: str, *, retmax: int = 5) -> list[str]:
    if not term:
        return []
    try:
        xml = _api_get("/esearch.fcgi", {"db": "pubmed", "term": term, "retmax": retmax})
    except Exception:
        return []
    return re.findall(r"<Id>(\d+)</Id>", xml)


def epmc_search(query: str, *, page_size: int = 5) -> list[dict[str, Any]]:
    q = urllib.parse.urlencode({
        "query": query,
        "format": "json",
        "pageSize": page_size,
        "resultType": "core",
    })
    url = f"{EPMC_REST}/search?{q}"
    try:
        data = _http_json(url, label=f"EPMC search {query[:40]}")
    except Exception:
        return []
    return (data.get("resultList") or {}).get("result") or []


def epmc_result_to_ids(r: dict[str, Any]) -> dict[str, str]:
    return {
        "pmid": str(r.get("pmid") or "").strip(),
        "pmcid": normalize_pmcid(str(r.get("pmcid") or "").strip()),
        "doi": normalize_doi(str(r.get("doi") or "").strip()),
        "title": str(r.get("title") or "").strip(),
        "journal": str(r.get("journalTitle") or "").strip(),
        "year": str(r.get("pubYear") or "").strip(),
        "is_oa": str(r.get("isOpenAccess") or "").upper() in ("Y", "YES", "TRUE"),
    }


def unpaywall_lookup(doi: str) -> dict[str, Any]:
    doi = normalize_doi(doi)
    if not doi:
        return {}
    q = urllib.parse.urlencode({"email": UNPAYWALL_EMAIL})
    url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?{q}"
    try:
        data = _http_json(url, label=f"Unpaywall {doi[:30]}")
    except Exception:
        return {}
    best = data.get("best_oa_location") or {}
    oa_locs = data.get("oa_locations") or []
    pdf_urls: list[str] = []
    landing_urls: list[str] = []
    for loc in [best, *oa_locs]:
        if not isinstance(loc, dict):
            continue
        pdf = (loc.get("url_for_pdf") or "").strip()
        landing = (loc.get("url") or "").strip()
        if pdf and pdf not in pdf_urls:
            pdf_urls.append(pdf)
        if landing and landing not in landing_urls:
            landing_urls.append(landing)
    return {
        "is_oa": bool(data.get("is_oa")),
        "pmid": str(data.get("pmid") or "").strip(),
        "pmcid": normalize_pmcid(str(data.get("pmcid") or "").strip()),
        "pdf_urls": pdf_urls,
        "landing_urls": landing_urls,
        "host_type": (best.get("host_type") or "").strip(),
        "source": "unpaywall",
    }


def openalex_lookup(*, doi: str = "", pmid: str = "", title: str = "") -> dict[str, Any]:
    if doi:
        path = f"https://api.openalex.org/works/doi:{urllib.parse.quote(normalize_doi(doi))}"
    elif pmid:
        path = f"https://api.openalex.org/works/pmid:{pmid}"
    elif title:
        q = urllib.parse.urlencode({
            "search": title[:200],
            "per-page": 5,
            "mailto": OPENALEX_EMAIL,
        })
        url = f"https://api.openalex.org/works?{q}"
        try:
            data = _http_json(url, label="OpenAlex search")
        except Exception:
            return {}
        for item in data.get("results") or []:
            t = (item.get("title") or "").strip()
            if _title_similar(title, t):
                return _openalex_item(item)
        return {}
    else:
        return {}
    try:
        data = _http_json(path + f"?mailto={urllib.parse.quote(OPENALEX_EMAIL)}", label="OpenAlex work")
    except Exception:
        return {}
    return _openalex_item(data)


def _openalex_item(data: dict[str, Any]) -> dict[str, Any]:
    if not data:
        return {}
    ids = data.get("ids") or {}
    oa = data.get("open_access") or {}
    primary = data.get("primary_location") or {}
    pdf_url = (primary.get("pdf_url") or "").strip()
    landing = (primary.get("landing_page_url") or "").strip()
    pdf_urls = [pdf_url] if pdf_url else []
    landing_urls = [landing] if landing else []
    for loc in data.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        p = (loc.get("pdf_url") or "").strip()
        l = (loc.get("landing_page_url") or "").strip()
        if p and p not in pdf_urls:
            pdf_urls.append(p)
        if l and l not in landing_urls:
            landing_urls.append(l)
    pmid = ""
    pmid_url = str(ids.get("pmid") or "")
    if pmid_url:
        pmid = pmid_url.rsplit("/", 1)[-1]
    pmcid = ""
    for u in landing_urls + pdf_urls:
        m = PMCID_RE.search(u)
        if m:
            pmcid = normalize_pmcid(m.group(1))
            break
    return {
        "is_oa": bool(oa.get("is_oa")),
        "oa_url": (oa.get("oa_url") or "").strip(),
        "pmid": pmid,
        "pmcid": pmcid,
        "doi": normalize_doi(str(ids.get("doi") or "").replace("https://doi.org/", "")),
        "pdf_urls": pdf_urls,
        "landing_urls": landing_urls,
        "source": "openalex",
    }


def publisher_pdf_urls(doi: str) -> list[str]:
    doi = normalize_doi(doi)
    if not doi:
        return []
    urls: list[str] = []
    if "10.1371/" in doi:
        urls.append(f"https://journals.plos.org/plosone/article/file?id={doi}&type=printable")
        urls.append(f"https://journals.plos.org/plosone/article/file?id={doi}&type=manuscript")
    if "10.3389/" in doi:
        art = doi.split("10.3389/", 1)[-1]
        urls.append(f"https://www.frontiersin.org/articles/{art}/pdf")
        urls.append(f"https://www.frontiersin.org/articles/{art}/full")
    if "10.1186/" in doi:
        urls.append(f"https://link.springer.com/content/pdf/{doi}.pdf")
    if "10.1089/zeb" in doi.lower():
        urls.append(f"https://doi.org/{doi}")
    return urls


def extract_pmcid_from_urls(urls: list[str]) -> str:
    for u in urls:
        m = PMCID_RE.search(u or "")
        if m:
            return normalize_pmcid(m.group(1))
    return ""


def _is_meta_record(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in (
        "correction to", "corrected and republished", "erratum", "corrigendum",
        "retraction", "withdrawn", "expression of concern", "publisher note",
    ))


def pick_best_pmid_candidates(title: str, pmids: list[str], *, limit: int = 5) -> list[str]:
    """从 esearch 结果中优先选择非勘误/更正的原始论文。"""
    if not pmids:
        return []
    ordered = list(dict.fromkeys(pmids))[:limit]
    if len(ordered) <= 1:
        return ordered
    meta: list[tuple[str, str]] = []
    for pid in ordered:
        try:
            xml = _api_get("/efetch.fcgi", {"db": "pubmed", "id": pid, "retmode": "xml"})
            _sleep()
            p = parse_pubmed_xml(xml)
            meta.append((pid, p.get("title_en") or ""))
        except Exception:
            meta.append((pid, ""))
    non_meta = [pid for pid, t in meta if not _is_meta_record(t)]
    if non_meta:
        return non_meta + [pid for pid in ordered if pid not in non_meta]
    return ordered


def normalize_title_for_search(title: str) -> list[str]:
    """生成多种标题检索变体（去勘误/卷期后缀）。"""
    t = (title or "").strip()
    variants = [t]
    stripped = re.sub(r"\s*\((?:vol|pg|pp|p\.|no\.|doi)[^)]*\)", "", t, flags=re.I).strip()
    stripped = re.sub(r"\s*[\(\[]\s*(?:corrigendum|correction|erratum|retraction)[^)\]]*[\)\]]", "", stripped, flags=re.I).strip()
    if stripped and stripped not in variants:
        variants.append(stripped)
    if "." in t:
        no_dot = t.rstrip(".")
        if no_dot not in variants:
            variants.append(no_dot)
    return variants


def resolve_all_ids(title: str, *, doi: str = "", pmid: str = "", wos_an: str = "") -> dict[str, Any]:
    """尽可能解析 DOI/PMID/PMCID，并记录发现路径。"""
    doi = normalize_doi(doi)
    pmid = (pmid or "").strip()
    pmcid = ""
    trace: list[str] = []
    candidates: list[dict[str, str]] = []

    if wos_an.startswith("MEDLINE:"):
        pmid = pmid or wos_an.split(":", 1)[1]
        trace.append("ris:medline")

    def add_candidate(src: str, ids: dict[str, str]) -> None:
        nonlocal pmid, pmcid, doi
        c = {
            "source": src,
            "pmid": (ids.get("pmid") or "").strip(),
            "pmcid": normalize_pmcid(ids.get("pmcid") or ""),
            "doi": normalize_doi(ids.get("doi") or doi),
        }
        if c["pmid"] or c["pmcid"] or c["doi"]:
            candidates.append(c)
        if c["doi"]:
            doi = doi or c["doi"]
        if c["pmid"]:
            pmid = pmid or c["pmid"]
        if c["pmcid"]:
            pmcid = pmcid or c["pmcid"]

    def maybe_swap_meta_pmid() -> None:
        nonlocal pmid, pmcid
        if not pmid:
            return
        try:
            xml = _api_get("/efetch.fcgi", {"db": "pubmed", "id": pmid, "retmode": "xml"})
            _sleep()
            cur = parse_pubmed_xml(xml)
            cur_title = cur.get("title_en") or ""
            if not _is_meta_record(cur_title) and not re.search(r"\(vol\s+\d+", title, re.I):
                return
            for tv in normalize_title_for_search(title):
                hits = pick_best_pmid_candidates(tv, esearch_pubmed(f"{tv}[Title]"))
                _sleep()
                if hits and hits[0] != pmid:
                    pmid = hits[0]
                    trace.append("esearch:original_article")
                    conv = ncbi_idconv(pmid)
                    _sleep()
                    pmcid = pmcid or conv.get("pmcid", "")
                    add_candidate("idconv:original_article", conv)
                    break
        except Exception:
            return

    if doi:
        conv = ncbi_idconv(doi)
        _sleep()
        add_candidate("idconv:doi", conv)
        trace.append("idconv:doi")

    if pmid and not pmcid:
        conv = ncbi_idconv(pmid)
        _sleep()
        add_candidate("idconv:pmid", conv)

    if doi and not pmid:
        hits = pick_best_pmid_candidates(title, esearch_pubmed(f"{doi}[doi]"))
        _sleep()
        if hits:
            pmid = hits[0]
            trace.append("esearch:doi")
            conv = ncbi_idconv(pmid)
            _sleep()
            add_candidate("idconv:esearch_doi", conv)

    if not pmid:
        hits: list[str] = []
        for tv in normalize_title_for_search(title):
            hits = pick_best_pmid_candidates(title, esearch_pubmed(f"{tv}[Title]"))
            _sleep()
            if hits:
                pmid = hits[0]
                trace.append("esearch:title_variant")
                conv = ncbi_idconv(pmid)
                _sleep()
                add_candidate("idconv:esearch_title", conv)
                break

    maybe_swap_meta_pmid()

    if pmid:
        oa, pmc_from = epmc_is_open_access(pmid)
        time.sleep(EPMC_INTERVAL)
        if pmc_from:
            add_candidate("epmc:oa", {"pmid": pmid, "pmcid": pmc_from, "doi": doi})
            trace.append("epmc:oa")

    for q in filter(None, [
        f'DOI:"{doi}"' if doi else "",
        f'EXT_ID:{pmid}' if pmid else "",
    ] + [f'TITLE:"{tv[:180]}"' for tv in normalize_title_for_search(title)]):
        for r in epmc_search(q, page_size=5):
            ids = epmc_result_to_ids(r)
            if q.startswith("TITLE") and not _title_similar(title, ids.get("title", "")):
                continue
            add_candidate(f"epmc:{q.split(':')[0].lower()}", ids)
        time.sleep(EPMC_INTERVAL)
        trace.append(f"epmc:{q.split(':')[0].lower()}")

    up: dict[str, Any] = {}
    oa: dict[str, Any] = {}

    if doi:
        up = unpaywall_lookup(doi)
        time.sleep(0.3)
        if up:
            add_candidate("unpaywall", up)
            pmc_from_url = extract_pmcid_from_urls(up.get("pdf_urls", []) + up.get("landing_urls", []))
            if pmc_from_url:
                add_candidate("unpaywall:url_pmcid", {"pmid": up.get("pmid", ""), "pmcid": pmc_from_url, "doi": doi})
            trace.append("unpaywall")

    oa = openalex_lookup(doi=doi, pmid=pmid, title=title if not pmid and not doi else "")
    time.sleep(0.3)
    if oa:
        add_candidate("openalex", oa)
        if oa.get("pmcid"):
            trace.append("openalex:pmcid")
        else:
            trace.append("openalex")

    # 汇总：优先有 pmcid 的候选
    pmcid_candidates = sorted({c["pmcid"] for c in candidates if c.get("pmcid")}, key=len)
    pmid = pmid or next((c["pmid"] for c in candidates if c.get("pmid")), "")
    doi = doi or next((c["doi"] for c in candidates if c.get("doi")), "")
    pmcid = pmcid or (pmcid_candidates[0] if pmcid_candidates else "")

    return {
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "pmcid_candidates": pmcid_candidates,
        "candidates": candidates,
        "trace": trace,
        "unpaywall": up if doi else {},
        "openalex": oa,
    }


def resolve_ids_fast(title: str, *, doi: str = "", pmid: str = "", wos_an: str = "") -> dict[str, Any]:
    """轻量 ID 解析（批处理用）：RIS DOI/MEDLINE + idconv，不做 esearch/unpaywall/openalex。"""
    doi = normalize_doi(doi)
    pmid = (pmid or "").strip()
    pmcid = ""
    trace: list[str] = []
    pmcid_candidates: list[str] = []

    if wos_an.startswith("MEDLINE:"):
        pmid = pmid or wos_an.split(":", 1)[1]
        trace.append("ris:medline")

    if doi:
        conv = ncbi_idconv(doi)
        _sleep()
        pmid = pmid or conv.get("pmid", "")
        pid = normalize_pmcid(conv.get("pmcid") or "")
        if pid:
            pmcid_candidates.append(pid)
            pmcid = pmcid or pid
        doi = doi or conv.get("doi", "")
        trace.append("idconv:doi")

    if doi and not pmid:
        hits = esearch_pubmed(f"{doi}[doi]")
        _sleep()
        if hits:
            pmid = hits[0]
            trace.append("esearch:doi")
            conv = ncbi_idconv(pmid)
            _sleep()
            pid = normalize_pmcid(conv.get("pmcid") or "")
            if pid:
                pmcid_candidates.append(pid)
                pmcid = pmcid or pid
            doi = doi or conv.get("doi", "")

    if doi and not pmid:
        for r in epmc_search(f'DOI:"{doi}"', page_size=3):
            ids_hit = epmc_result_to_ids(r)
            if ids_hit.get("pmid"):
                pmid = ids_hit["pmid"]
                trace.append("epmc:doi")
                pid = normalize_pmcid(ids_hit.get("pmcid") or "")
                if pid:
                    pmcid_candidates.append(pid)
                    pmcid = pmcid or pid
                break
        time.sleep(EPMC_INTERVAL)

    if not pmid and title:
        for tv in normalize_title_for_search(title)[:2]:
            hits = pick_best_pmid_candidates(title, esearch_pubmed(f"{tv}[Title]"))
            _sleep()
            if hits:
                pmid = hits[0]
                trace.append("esearch:title")
                conv = ncbi_idconv(pmid)
                _sleep()
                pid = normalize_pmcid(conv.get("pmcid") or "")
                if pid:
                    pmcid_candidates.append(pid)
                    pmcid = pmcid or pid
                doi = doi or conv.get("doi", "")
                break

    if pmid and not pmcid:
        conv = ncbi_idconv(pmid)
        _sleep()
        pid = normalize_pmcid(conv.get("pmcid") or "")
        if pid:
            pmcid_candidates.append(pid)
            pmcid = pmcid or pid
        doi = doi or conv.get("doi", "")
        trace.append("idconv:pmid")

    pmcid_candidates = list(dict.fromkeys([x for x in pmcid_candidates if x]))
    return {
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "pmcid_candidates": pmcid_candidates,
        "trace": trace,
    }


def resolve_ids_from_ris_meta(
    ris_meta: dict[str, str],
    *,
    title: str = "",
    enrich: bool = True,
) -> dict[str, Any]:
    """从去重 RIS 元数据提取 DOI/PMID/WOS，并按需 idconv 补 PMCID。"""
    doi = normalize_doi(ris_meta.get("doi", ""))
    pmid = ""
    wos_an = (ris_meta.get("an") or "").strip()
    if wos_an.startswith("MEDLINE:"):
        pmid = wos_an.split(":", 1)[1].strip()
    elif pmid:
        wos_an = f"MEDLINE:{pmid}"
    if enrich:
        out = resolve_ids_fast(title, doi=doi, wos_an=wos_an)
        out["ris_abstract"] = (ris_meta.get("abstract") or "").strip()
        return out
    trace: list[str] = ["dedup_ris"]
    if doi:
        trace.append("ris:doi")
    if pmid:
        trace.append("ris:medline")
    if wos_an:
        trace.append("ris:an")
    return {
        "doi": doi,
        "pmid": pmid,
        "pmcid": "",
        "pmcid_candidates": [],
        "trace": trace,
        "ris_abstract": (ris_meta.get("abstract") or "").strip(),
    }


def fetch_ncbi_pmc_xml(pmcid: str) -> tuple[bytes, str, str]:
    pmcid = normalize_pmcid(pmcid)
    if not pmcid:
        return b"", "", ""
    q = urllib.parse.urlencode({"db": "pmc", "id": pmcid, "rettype": "xml"})
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?{q}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
        with urlopen_with_retry(req, timeout=90, label=f"NCBI PMC {pmcid}") as resp:
            raw = resp.read()
        if raw.strip() and b"<article" in raw[:4000].lower():
            return raw, pmcid, url
    except Exception:
        pass
    return b"", "", ""


def fetch_jats_xml_cascade(
    *,
    pmid: str = "",
    pmcid: str = "",
    doi: str = "",
    pmcid_candidates: list[str] | None = None,
) -> tuple[bytes, str, str, str]:
    """
    拉取 JATS XML：Europe PMC → NCBI PMC efetch。
    返回 (bytes, used_id, url, source_label)。
    """
    all_pmcids = list(dict.fromkeys(
        [normalize_pmcid(pmcid)] + [normalize_pmcid(x) for x in (pmcid_candidates or []) if x]
    ))
    all_pmcids = [x for x in all_pmcids if x]

    raw, used, url = fetch_epmc_jats_xml(
        pmid=pmid,
        pmcid=pmcid,
        doi=doi,
        pmcid_candidates=pmcid_candidates,
    )
    if raw:
        return raw, used, url, "epmc_xml"

    for pid in all_pmcids:
        raw, used, url = fetch_ncbi_pmc_xml(pid)
        _sleep()
        if raw:
            return raw, used, url, "ncbi_pmc_xml"
    if pmid:
        conv = ncbi_idconv(pmid)
        pid = conv.get("pmcid") or ""
        if pid and pid not in all_pmcids:
            raw, used, url = fetch_ncbi_pmc_xml(pid)
            _sleep()
            if raw:
                return raw, used, url, "ncbi_pmc_xml"
    return b"", "", "", ""


def fetch_epmc_jats_xml(
    *,
    pmid: str = "",
    pmcid: str = "",
    doi: str = "",
    pmcid_candidates: list[str] | None = None,
) -> tuple[bytes, str, str]:
    """尝试所有 Europe PMC XML 路径，返回 (bytes, used_id, url)。"""
    tried: set[str] = set()
    paths: list[str] = []
    all_pmcids = [normalize_pmcid(pmcid)] + [normalize_pmcid(x) for x in (pmcid_candidates or [])]
    for pid in all_pmcids:
        if pid and pid not in tried:
            paths.append(f"{pid}/fullTextXML")
            tried.add(pid)
    if pmid:
        paths.append(f"MED/{pmid}/fullTextXML")
        conv = ncbi_idconv(pmid)
        pid = conv.get("pmcid") or ""
        if pid and pid not in tried:
            paths.append(f"{pid}/fullTextXML")
            tried.add(pid)
    if doi:
        for r in epmc_search(f'DOI:"{normalize_doi(doi)}"', page_size=5):
            ids = epmc_result_to_ids(r)
            pid = ids.get("pmcid") or ""
            if pid and pid not in tried:
                paths.append(f"{pid}/fullTextXML")
                tried.add(pid)
    last_err = ""
    for path in paths:
        url = f"{EPMC_REST}/{path}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
            with urlopen_with_retry(req, timeout=90, label=f"JATS {path}") as resp:
                raw = resp.read()
            if raw.strip() and b"<article" in raw[:2000].lower():
                return raw, path.split("/")[0], url
        except urllib.error.HTTPError as e:
            last_err = str(e)
            if e.code == 404:
                continue
        except Exception as e:
            last_err = str(e)
        time.sleep(EPMC_INTERVAL)
    return b"", "", last_err


def try_download_pdf(url: str, dest_path: str, *, timeout: float = 25.0) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 1024:
            return False, "响应过小"
        if data[:5].startswith(b"%PDF"):
            Path = __import__("pathlib").Path
            Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
            Path(dest_path).write_bytes(data)
            return True, url
        return False, "非PDF"
    except Exception as e:
        return False, str(e)


def jats_stats(raw: bytes) -> dict[str, Any]:
    stats = {"chars": 0, "sections": 0, "figures": 0, "tables": 0, "refs": 0}
    if not raw.strip():
        return stats
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        stats["chars"] = len(raw)
        return stats

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    text_parts: list[str] = []
    for elem in root.iter():
        tag = local(elem.tag)
        if tag == "p":
            t = " ".join("".join(elem.itertext()).split())
            if len(t) > 20:
                text_parts.append(t)
        elif tag == "fig":
            stats["figures"] += 1
        elif tag in ("table-wrap", "table"):
            stats["tables"] += 1
        elif tag == "ref":
            stats["refs"] += 1
        elif tag == "sec":
            stats["sections"] += 1
    stats["chars"] = len("\n".join(text_parts))
    return stats
