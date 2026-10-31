# -*- coding: utf-8 -*-
"""PubMed 判别「是」文献语料：XML 解析、PMC 全文、双语格式化。"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Optional

from rag.europepmc_collector import REQUEST_INTERVAL as EPMC_INTERVAL
from rag.network_resilience import call_with_network_recovery, urlopen_with_retry

EPMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"
EPMC_SEARCH = f"{EPMC_REST}/search"
FULLTEXT_MAX_CHARS = int(__import__("os").getenv("PUBMED_YES_FULLTEXT_MAX", "80000"))


def _epmc_json(url: str, *, label: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
    with urlopen_with_retry(req, timeout=60, label=label) as resp:
        return json.loads(resp.read().decode("utf-8"))


def epmc_is_open_access(pmid: str) -> tuple[bool, str]:
    """查询 Europe PMC 是否开放获取，返回 (is_oa, pmcid)。"""
    q = urllib.parse.urlencode({
        "query": f"EXT_ID:{pmid}",
        "format": "json",
        "pageSize": 1,
        "resultType": "core",
    })
    url = f"{EPMC_SEARCH}?{q}"
    data = call_with_network_recovery(
        lambda: _epmc_json(url, label=f"EPMC OA {pmid}"),
        label=f"EPMC OA {pmid}",
    )
    results = (data.get("resultList") or {}).get("result") or []
    if not results:
        return False, ""
    r = results[0]
    pmcid = str(r.get("pmcid") or "").strip()
    oa = str(r.get("isOpenAccess") or "").upper() in ("Y", "YES", "TRUE")
    return oa, pmcid


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_pubmed_xml(raw: str) -> dict[str, str]:
    """从 PubMed XML 提取 title / abstract / journal / year / doi / pmcid。"""
    out = {
        "title_en": "",
        "abstract_en": "",
        "journal": "",
        "year": "",
        "doi": "",
        "pmcid": "",
    }
    if not (raw or "").strip():
        return out
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return out
    out["title_en"] = (root.findtext(".//ArticleTitle") or "").strip()
    abs_parts: list[str] = []
    for ab in root.findall(".//AbstractText"):
        label = ab.get("Label") or ""
        txt = "".join(ab.itertext()).strip()
        if label and txt:
            abs_parts.append(f"{label}: {txt}")
        elif txt:
            abs_parts.append(txt)
    out["abstract_en"] = " ".join(abs_parts).strip()
    out["journal"] = (
        root.findtext(".//Journal/Title")
        or root.findtext(".//ISOAbbreviation")
        or ""
    ).strip()
    out["year"] = (root.findtext(".//PubDate/Year") or "")[:4]
    for eid in root.findall(".//ArticleId"):
        id_type = (eid.get("IdType") or "").lower()
        val = (eid.text or "").strip()
        if id_type == "doi" and val:
            out["doi"] = val
        elif id_type == "pmc" and val:
            v = val.upper()
            out["pmcid"] = v if v.startswith("PMC") else f"PMC{v}"
    return out


def fetch_pmc_fulltext(pmid: str, pmcid: str = "", *, skip_precheck: bool = False) -> tuple[str, str, bool]:
    """
    从 Europe PMC 拉取开放获取全文（JATS XML → 纯文本）。
    返回 (body_text, pmcid_used, success)。不调用 LLM / DeepSeek。
    """
    if not skip_precheck and not pmcid:
        oa, pmc_from_search = epmc_is_open_access(pmid)
        if pmc_from_search:
            pmcid = pmc_from_search
        if not oa and not pmcid:
            return "", "", False

    paths: list[str] = []
    if pmcid:
        paths.append(f"{pmcid}/fullTextXML")
    paths.append(f"MED/{pmid}/fullTextXML")
    last_err: Optional[Exception] = None
    for path in paths:
        url = f"{EPMC_REST}/{path}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
            with urlopen_with_retry(req, timeout=120, label=f"EPMC fulltext {pmid}") as resp:
                raw = resp.read()
            text = _jats_body_to_text(raw)
            if text.strip():
                used = path.split("/")[0]
                return text[:FULLTEXT_MAX_CHARS], used, True
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                continue
            time.sleep(min(30.0, EPMC_INTERVAL * 3))
        except Exception as e:
            last_err = e
            from rag.network_resilience import is_network_error

            if is_network_error(e):
                raise
            time.sleep(EPMC_INTERVAL)
    if last_err and getattr(last_err, "code", None) != 404:
        raise last_err
    return "", pmcid or "", False


def _jats_body_to_text(raw: bytes) -> str:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return re.sub(r"<[^>]+>", " ", raw.decode("utf-8", errors="replace"))
    body = None
    for elem in root.iter():
        if _local(elem.tag) == "body":
            body = elem
            break
    paras: list[str] = []
    target = body if body is not None else root
    for elem in target.iter():
        if _local(elem.tag) == "p":
            t = " ".join("".join(elem.itertext()).split())
            if len(t) > 20:
                paras.append(t)
    return "\n\n".join(paras)


def format_bilingual_document(rec: dict[str, Any]) -> str:
    """中英标题+摘要为主；有 PMC 全文则追加正文块。"""
    pmid = rec.get("pmid") or "?"
    lines = [
        f"【PUBMED YES PMID:{pmid}】",
        f"标题（英）：{(rec.get('title_en') or '').strip()}",
        f"标题（中）：{(rec.get('title_zh') or rec.get('title_en') or '').strip()}",
    ]
    meta = []
    if rec.get("journal"):
        meta.append(str(rec["journal"]))
    if rec.get("year"):
        meta.append(str(rec["year"]))
    if rec.get("doi"):
        meta.append(f"DOI:{rec['doi']}")
    if rec.get("pmcid"):
        meta.append(str(rec["pmcid"]))
    meta.append("JUDGEMENT:是")
    if rec.get("has_fulltext"):
        meta.append("FULLTEXT:yes")
    if meta:
        lines.append("来源信息：" + " | ".join(meta))
    ae = (rec.get("abstract_en") or "").strip()
    az = (rec.get("abstract_zh") or "").strip()
    if ae:
        lines.append(f"摘要（英）：{ae}")
    if az:
        lines.append(f"摘要（中）：{az}")
    body = (rec.get("body_text") or "").strip()
    if body:
        lines.append("正文（PMC开放获取）：")
        lines.append(body)
    return "\n".join(lines) + "\n"


def corpus_record_to_json(rec: dict[str, Any]) -> str:
    return json.dumps(rec, ensure_ascii=False)
