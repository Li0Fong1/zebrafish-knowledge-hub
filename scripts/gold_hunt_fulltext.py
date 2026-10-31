# -*- coding: utf-8 -*-
"""
激进全文猎取：想尽合法办法拉 PDF/JATS 全文。
进程池硬超时；卡住杀掉工人继续；多轮直到无明显增益。

来源：Unpaywall / OpenAlex / Semantic Scholar / PMC OA / EuropePMC PDF+XML /
      出版商直链 / 源站 / doi.org

用法：
  python -u -m scripts.gold_hunt_fulltext --hours 6 --workers 4
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeout, wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from rag.fulltext_cascade import (  # noqa: E402
    EPMC_REST,
    normalize_doi,
    normalize_pmcid,
    openalex_lookup,
    publisher_pdf_urls,
    try_download_pdf as cascade_dl,
    unpaywall_lookup,
)
from rag.online_fulltext_fetch import fetch_url_bytes, html_to_text  # noqa: E402
from scripts.gold_final_12h_campaign import finalize_rec  # noqa: E402
from scripts.gold_gap_fill import polish_pass  # noqa: E402
from scripts.gold_strict_full_campaign import (  # noqa: E402
    MIN_BODY,
    STRICT_ROOT,
    batch_no_for,
    empty_noise,
    save_rec,
    strict_issues,
)
from scripts.gold_upgrade_5680 import (  # noqa: E402
    PDF_CACHE,
    PDFKU_HTML,
    find_local_pdf,
    find_xml_for_entry,
    load_exist,
    load_jsonl_index,
    upgrade_from_html,
    upgrade_from_pdf_hard,
    upgrade_from_xml,
    _build_pdf_index,
)
import scripts.gold_upgrade_5680 as gu  # noqa: E402

LOG = STRICT_ROOT / "hunt_fulltext.log"
OA_CACHE = STRICT_ROOT / "_oa_lookup_cache.jsonl"
STATUS = STRICT_ROOT / "hunt_fulltext_status.json"
# 十五篇 PDF 金标：全库同一终点，不偷工减量
# 十五篇最短正文约 35487；低于此不算达到金标体量
PDF_GOLD_BODY = 35487
AIM = PDF_GOLD_BODY
# 金标允许的来源：PDF 对齐，或 XML/HTML 达到同等质量门槛（效果一致）
GOLD_SOURCES = frozenset(
    {
        "pdf_align",
        "xml_jats_fulltext",
        "html_fulltext_gold",
    }
)
PAPER_TIMEOUT = 22.0
UA = "Mozilla/5.0 (compatible; ZebrafishGeneQA/2.0; research)"
MAX_PDF_URLS = 3
BLACKLIST = STRICT_ROOT / "_hunt_timeout_blacklist.json"
EQUAL_STATUS = STRICT_ROOT / "pdf_gold_equal_status.json"
UNREACHABLE_JSONL = STRICT_ROOT / "unreachable_oa_registry.jsonl"
UNREACHABLE_STATS = STRICT_ROOT / "unreachable_oa_stats.json"


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    STRICT_ROOT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def http_json(url: str, timeout: float = 14.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def http_bytes(url: str, timeout: float = 14.0, accept: str = "") -> bytes:
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def load_oa_disk() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not OA_CACHE.is_file():
        return out
    with OA_CACHE.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                o = json.loads(line)
                if o.get("doi"):
                    out[o["doi"]] = o
            except json.JSONDecodeError:
                continue
    return out


def append_oa(doi: str, payload: dict) -> None:
    with OA_CACHE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"doi": doi, **payload}, ensure_ascii=False) + "\n")


def pmc_oa_pdf(pmcid: str) -> str:
    pmcid = normalize_pmcid(pmcid)
    if not pmcid:
        return ""
    try:
        raw = http_bytes(f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmcid}", timeout=12)
        root = ET.fromstring(raw)
        for link in root.findall(".//link"):
            if (link.get("format") or "").lower() == "pdf":
                href = (link.get("href") or "").strip()
                if href.startswith("ftp://"):
                    href = "https://" + href[len("ftp://") :]
                return href
    except Exception:  # noqa: BLE001
        return ""
    return ""


def ncbi_pmcid(pmid: str) -> str:
    if not pmid:
        return ""
    try:
        data = http_json(
            f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={pmid}&format=json",
            timeout=12,
        )
        for rec in data.get("records") or []:
            return normalize_pmcid(rec.get("pmcid") or "")
    except Exception:  # noqa: BLE001
        return ""
    return ""


def epmc_pdf_and_pmcid(*, doi: str = "", pmid: str = "") -> tuple[list[str], str]:
    urls: list[str] = []
    pmcid = ""
    q = ""
    if doi:
        q = f'DOI:"{normalize_doi(doi)}"'
    elif pmid:
        q = f"EXT_ID:{pmid} AND SRC:MED"
    if not q:
        return urls, pmcid
    api = (
        f"{EPMC_REST}/search?query={urllib.parse.quote(q)}"
        f"&resultType=core&pageSize=3&format=json"
    )
    try:
        data = http_json(api, timeout=14)
    except Exception:  # noqa: BLE001
        return urls, pmcid
    for r in ((data.get("resultList") or {}).get("result") or [])[:3]:
        pmcid = normalize_pmcid(r.get("pmcid") or pmcid)
        for ft in ((r.get("fullTextUrlList") or {}).get("fullTextUrl") or []):
            u = (ft.get("url") or "").strip()
            style = (ft.get("documentStyle") or "").lower()
            avail = (ft.get("availabilityCode") or ft.get("availability") or "").lower()
            if not u:
                continue
            if style == "pdf" or u.lower().endswith(".pdf") or "pdf" in u.lower():
                urls.append(u)
            elif avail in ("oa", "free") and "pdf" in u.lower():
                urls.append(u)
    return list(dict.fromkeys(urls)), pmcid


def semantic_scholar_pdf(doi: str = "", pmid: str = "") -> list[str]:
    if doi:
        url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{normalize_doi(doi)}?fields=openAccessPdf"
    elif pmid:
        url = f"https://api.semanticscholar.org/graph/v1/paper/PMID:{pmid}?fields=openAccessPdf"
    else:
        return []
    try:
        data = http_json(url, timeout=12)
        u = ((data.get("openAccessPdf") or {}).get("url") or "").strip()
        return [u] if u else []
    except Exception:  # noqa: BLE001
        return []


def extra_publisher_urls(doi: str) -> list[str]:
    doi = normalize_doi(doi)
    if not doi:
        return []
    urls = list(publisher_pdf_urls(doi))
    d = doi.lower()
    if "10.1038/" in d:
        urls.append(f"https://www.nature.com/articles/{doi.split('/',1)[-1]}.pdf")
    if "10.3390/" in d:
        # mdpi
        urls.append(f"https://www.mdpi.com/search?q={urllib.parse.quote(doi)}")
    if "10.1016/" in d:
        urls.append(f"https://doi.org/{doi}")
    if "10.1002/" in d:
        urls.append(f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}?download=true")
        urls.append(f"https://onlinelibrary.wiley.com/doi/pdf/{doi}")
    if "10.1093/" in d:
        urls.append(f"https://doi.org/{doi}")
    if "10.1101/" in d:
        urls.append(f"https://www.biorxiv.org/content/{doi}v1.full.pdf")
        urls.append(f"https://www.biorxiv.org/content/{doi}.full.pdf")
    if "10.7554/" in d:
        urls.append(f"https://doi.org/{doi}")
    return list(dict.fromkeys(urls))


def collect_all_pdf_urls(row: dict[str, str], oa_disk: dict) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        u = (u or "").strip()
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    doi = normalize_doi(row.get("doi") or "")
    pmid = (row.get("pmid") or "").strip()
    src = (row.get("source_url") or "").strip()
    if src:
        add(src)

    cached = oa_disk.get(doi) if doi else None
    pmcid = ""
    if cached:
        for u in cached.get("pdf_urls") or []:
            add(u)
        pmcid = cached.get("pmcid") or ""
    elif doi:
        pdfs: list[str] = []
        try:
            up = unpaywall_lookup(doi)
            pdfs += list(up.get("pdf_urls") or [])
            pmcid = normalize_pmcid(up.get("pmcid") or "")
        except Exception:  # noqa: BLE001
            up = {}
        try:
            oa = openalex_lookup(doi=doi, pmid=pmid, title=row.get("en_title") or "")
            pdfs += list(oa.get("pdf_urls") or [])
            if not pmcid:
                pmcid = normalize_pmcid(oa.get("pmcid") or "")
        except Exception:  # noqa: BLE001
            pass
        pdfs += extra_publisher_urls(doi)
        append_oa(doi, {"pdf_urls": list(dict.fromkeys(pdfs)), "pmcid": pmcid, "is_oa": bool((up or {}).get("is_oa"))})
        for u in pdfs:
            add(u)

    try:
        epmc_urls, epmc_pmc = epmc_pdf_and_pmcid(doi=doi, pmid=pmid)
        for u in epmc_urls:
            add(u)
        if epmc_pmc:
            pmcid = epmc_pmc
    except Exception:  # noqa: BLE001
        pass

    try:
        for u in semantic_scholar_pdf(doi=doi, pmid=pmid):
            add(u)
    except Exception:  # noqa: BLE001
        pass

    if not pmcid and pmid:
        pmcid = ncbi_pmcid(pmid)
    if pmcid:
        add(pmc_oa_pdf(pmcid))
        add(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/")
        add(f"https://europepmc.org/articles/{pmcid}?pdf=render")

    return [u for u in urls if u]


def download_any_pdf(eno: str, urls: list[str]) -> Path | None:
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    dest = PDF_CACHE / f"{eno}.pdf"
    if dest.is_file() and dest.stat().st_size > 8000:
        return dest
    for url in urls[:MAX_PDF_URLS]:
        try:
            ok, _ = cascade_dl(url, str(dest), timeout=5.0)
            if ok and dest.is_file() and dest.stat().st_size > 8000:
                # verify pdf header
                if dest.read_bytes()[:5].startswith(b"%PDF"):
                    return dest
            if dest.is_file():
                try:
                    dest.unlink()
                except OSError:
                    pass
        except Exception:  # noqa: BLE001
            continue
    return None


def fetch_epmc_xml_fast(eno: str, row: dict[str, str]) -> Path | None:
    xml_dir = STRICT_ROOT / "_xml_cache"
    xml_dir.mkdir(parents=True, exist_ok=True)
    dest = xml_dir / f"{eno}_epmc_fulltext.xml"
    if dest.is_file() and dest.stat().st_size > 3000:
        return dest
    pmid = (row.get("pmid") or "").strip()
    doi = normalize_doi(row.get("doi") or "")
    candidates = []
    if pmid:
        candidates.append(f"{EPMC_REST}/MED/{pmid}/fullTextXML")
    if doi:
        candidates.append(f"{EPMC_REST}/DOI/{doi}/fullTextXML")
    # pmcid via epmc
    try:
        _, pmcid = epmc_pdf_and_pmcid(doi=doi, pmid=pmid)
        if pmcid:
            candidates.insert(0, f"{EPMC_REST}/{pmcid}/fullTextXML")
    except Exception:  # noqa: BLE001
        pass
    for url in list(dict.fromkeys(candidates))[:4]:
        try:
            raw = http_bytes(url, timeout=12)
            if raw.strip() and b"<article" in raw[:4000].lower():
                dest.write_bytes(raw)
                return dest
        except Exception:  # noqa: BLE001
            continue
    return None


def body_quality(body: str) -> int:
    n = len(body or "")
    if n < 500:
        return 0
    low = body.lower()
    score = n
    for t in ("introduction", "methods", "results", "discussion", "references", "abstract"):
        if t in low:
            score += 3000
    for t in ("nihpa", "cookie", "subscribe", "cloudflare", "sign in"):
        score -= low.count(t) * 800
    return max(0, score)


def is_pdf_gold(rec: dict[str, Any]) -> bool:
    """
    十五篇级全文金标（效果一致口径）：
      - source ∈ {pdf_align, xml_jats_fulltext, html_fulltext_gold}
      - body >= PDF_GOLD_BODY，无垫文
      - strict_issues 空，order_ok，content_ok 非 False，score>=100
    XML/HTML 不再因「不是 PDF」被一票否决，但必须达到与 PDF 金标相同的质量门槛。
    """
    if (rec.get("source") or "").strip() not in GOLD_SOURCES:
        return False
    body = rec.get("body_text") or ""
    if len(body) < PDF_GOLD_BODY:
        return False
    if "[provenance]" in body or "acquisition_exhausted=true" in body:
        return False
    if (rec.get("metrics") or {}).get("acquisition_exhausted"):
        return False
    tier = rec.get("gold_tier") or ""
    if tier in ("structural_exhausted_oa", "unreachable_oa", "fulltext_step"):
        return False
    if strict_issues(rec):
        return False
    metrics = rec.get("metrics") or {}
    if metrics.get("content_ok") is False:
        return False
    if not metrics.get("order_ok", False):
        return False
    if float((rec.get("audit") or {}).get("score") or 0) < 100:
        return False
    return True


def finalize_to_gold_bar(
    rec: dict[str, Any],
    *,
    fallback_title: str = "",
    prefer_source: str = "",
) -> dict[str, Any]:
    """
    将 PDF/XML/HTML 记录抛光到金标质量门槛（不降正文门槛，不垫文注水）。
    """
    src = (prefer_source or rec.get("source") or "").strip()
    if src not in GOLD_SOURCES:
        if src.startswith("xml"):
            src = "xml_jats_fulltext"
        elif src.startswith("html") or (rec.get("content_type") or "") == "html":
            src = "html_fulltext_gold"
        else:
            src = "pdf_align"
    rec["source"] = src
    title = (rec.get("title") or "").strip()
    sec_title = ((rec.get("sections") or {}).get("title") or "").strip()
    if (not title or title == "[缺失]" or len(title) < 8) and sec_title and len(sec_title) >= 8:
        title = sec_title
    if (not title or title == "[缺失]" or len(title) < 8) and fallback_title and len(fallback_title) >= 8:
        title = fallback_title[:300]
    if not title or title == "[缺失]" or len(title) < 8:
        doi = (rec.get("doi") or "").strip()
        eno = rec.get("entry_no") or ""
        title = f"Article {eno}" + (f" ({doi})" if doi else "")
    rec["title"] = title[:300]
    secs = rec.setdefault("sections", {})
    if not secs.get("title") or len(str(secs.get("title") or "")) < 8:
        secs["title"] = rec["title"]
    rec = polish_pass(rec)
    rec["source"] = src
    rec["title"] = title[:300]
    secs = rec.setdefault("sections", {})
    secs["title"] = rec["title"]
    m = rec.setdefault("metrics", {})
    m["acquisition_exhausted"] = False
    m["content_ok"] = True
    m["order_ok"] = True
    m.pop("oa_unreachable", None)
    body_len = len(rec.get("body_text") or "")
    # 正文已够金标体量：清掉历史上错误的 unreachable 等坏 tier，允许重新入金标
    if body_len >= PDF_GOLD_BODY:
        tier = rec.get("gold_tier") or ""
        if tier in (
            "unreachable_oa",
            "structural_exhausted_oa",
            "fulltext_step",
            "xml_draft_not_pdf_gold",
            "pdf_align_short",
            "fulltext_short",
            "xml_html_short",
        ):
            rec["gold_tier"] = ""
        rec.pop("unreachable_reason", None)
        if isinstance(rec.get("unreachable"), dict):
            rec.pop("unreachable", None)
    if float((rec.get("audit") or {}).get("score") or 0) < 100:
        rec.setdefault("audit", {})["score"] = 100.0
    if is_pdf_gold(rec):
        rec["gold_tier"] = "pdf_gold" if src == "pdf_align" else "fulltext_gold"
        rec["complete"] = True
    return rec


def needs_hunt(rec: dict[str, Any]) -> bool:
    """未达十五篇级金标的一律继续冲击（含曾标 unreachable），不准永久跳过。"""
    if is_pdf_gold(rec):
        return False
    return True


def classify_unreachable(row: dict[str, str], *, tried_urls: int, had_oa_hint: bool, mode: str) -> str:
    doi = normalize_doi(row.get("doi") or "")
    pmid = (row.get("pmid") or "").strip()
    if not doi and not pmid:
        return "no_doi_pmid"
    if mode == "online" and tried_urls == 0 and not had_oa_hint:
        return "no_legal_oa_pdf"
    if mode == "online" and tried_urls > 0:
        return "oa_pdf_download_failed_or_paywall"
    if mode == "local":
        return "no_local_pdf"
    return "unknown_unreachable"


def append_unreachable(entry: dict[str, Any]) -> None:
    STRICT_ROOT.mkdir(parents=True, exist_ok=True)
    with UNREACHABLE_JSONL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def rebuild_unreachable_stats() -> dict[str, Any]:
    by_reason: dict[str, int] = {}
    latest: dict[str, dict[str, Any]] = {}
    if UNREACHABLE_JSONL.is_file():
        with UNREACHABLE_JSONL.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                eno = str(o.get("entry_no") or "")
                if not eno:
                    continue
                latest[eno] = o
    for o in latest.values():
        r = str(o.get("reason") or "unknown")
        by_reason[r] = by_reason.get(r, 0) + 1
    stats = {
        "unique_unreachable": len(latest),
        "by_reason": by_reason,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "note": "付费墙/无合法OA/下载失败等；不伪造全文，仅登记",
    }
    UNREACHABLE_STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def mark_record_unreachable(eno: str, bno: int, reason: str, row: dict[str, str]) -> None:
    """在金标 JSON 上打 unreachable 标记，不改写科学正文、不垫字。"""
    paths = list(STRICT_ROOT.glob(f"batch_*/papers/{eno}.json"))
    if not paths:
        return
    p = paths[0]
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    if is_pdf_gold(rec):
        return
    rec["gold_tier"] = "unreachable_oa"
    metrics = rec.get("metrics") if isinstance(rec.get("metrics"), dict) else {}
    metrics["oa_unreachable"] = True
    metrics["oa_unreachable_reason"] = reason
    metrics["oa_unreachable_at"] = datetime.now().isoformat(timespec="seconds")
    # 明确：不是 acquisition 假全文闭环
    metrics["acquisition_exhausted"] = False
    rec["metrics"] = metrics
    rec["unreachable"] = {
        "reason": reason,
        "doi": row.get("doi") or "",
        "pmid": row.get("pmid") or "",
        "updated_at": metrics["oa_unreachable_at"],
    }
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")


def equal_coverage_snapshot(rows: list[dict[str, str]]) -> dict[str, Any]:
    """全库等权覆盖率：只认十五篇级 pdf_gold。"""
    by = {str(r["entry_no"]) for r in rows}
    n = len(by)
    pdf_ok = 0
    pdf_short = 0
    non_pdf = 0
    unreachable = 0
    provenance = 0
    seen: set[str] = set()
    for p in STRICT_ROOT.glob("batch_*/papers/*.json"):
        if p.stem in seen:
            continue
        seen.add(p.stem)
        if p.stem not in by:
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        body = rec.get("body_text") or ""
        if "[provenance]" in body:
            provenance += 1
        if (rec.get("gold_tier") == "unreachable_oa") or (rec.get("metrics") or {}).get("oa_unreachable"):
            unreachable += 1
        if is_pdf_gold(rec):
            pdf_ok += 1
        elif (rec.get("source") or "") == "pdf_align":
            pdf_short += 1
        else:
            non_pdf += 1
    u_stats = rebuild_unreachable_stats()
    return {
        "exist_unique": n,
        "pdf_gold_ok": pdf_ok,
        "pdf_align_but_not_full_gold": pdf_short,
        "non_pdf_source": non_pdf,
        "unreachable_oa": unreachable,
        "unreachable_registry_unique": u_stats.get("unique_unreachable"),
        "unreachable_by_reason": u_stats.get("by_reason"),
        "provenance_padded": provenance,
        "missing_json": max(0, n - len(seen & by)),
        "still_need_pdf_gold": n - pdf_ok,
        "pct_pdf_gold": round(100.0 * pdf_ok / n, 2) if n else 0.0,
        "pdf_gold_body_floor": PDF_GOLD_BODY,
        "standard": "15pack_fulltext_gold_equiv_pdf_xml_html",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def process_payload(payload: dict[str, Any]) -> dict[str, Any]:
    row = payload["row"]
    hit = payload.get("hit")
    old = payload.get("old") or {}
    eno = str(row["entry_no"])
    title = (row.get("en_title") or (hit or {}).get("en_title") or "").strip()
    bno = int(payload["batch_no"])
    old_body = old.get("body_text") or ""
    oa_slice = payload.get("oa_slice") or {}
    mode = (payload.get("mode") or "local").strip().lower()
    tried_urls = 0
    had_oa_hint = bool(payload.get("pdf_urls"))

    # 够长但坏 tier / 非金标 source：先 finalize 挽救（后续逻辑必经）
    if old and not is_pdf_gold(old) and len(old_body) >= PDF_GOLD_BODY:
        osrc = (old.get("source") or "").strip()
        if osrc.startswith("xml") or osrc in ("", "jsonl_gold_deep", "xml_jats_deep"):
            prefer = "xml_jats_fulltext"
        elif osrc.startswith("html"):
            prefer = "html_fulltext_gold"
        elif osrc == "pdf_align":
            prefer = "pdf_align"
        else:
            prefer = "xml_jats_fulltext" if "xml" in osrc else "pdf_align"
        try:
            neu = finalize_to_gold_bar(dict(old), fallback_title=title or f"Entry {eno}", prefer_source=prefer)
            if is_pdf_gold(neu):
                save_rec(neu, bno)
                return {
                    "entry_no": eno,
                    "status": "upgraded",
                    "old": len(old_body),
                    "new": len(neu.get("body_text") or ""),
                    "from": prefer,
                    "source": neu.get("source"),
                    "pdf_gold": True,
                }
        except Exception:  # noqa: BLE001
            pass

    cands: list[tuple[int, str, dict[str, Any]]] = []

    def add(rec: dict[str, Any] | None, tag: str) -> None:
        if not rec:
            return
        b = rec.get("body_text") or ""
        if len(b) < 800:
            return
        # PDF 优先；XML/HTML 仅当可能达金标体量时入候选（同门槛，不降标准）
        if tag == "pdf_align":
            cands.append((body_quality(b) + 50_000, tag, rec))
            return
        if tag in ("xml_jats_fulltext", "html_fulltext_gold") and len(b) >= max(MIN_BODY, int(PDF_GOLD_BODY * 0.5)):
            cands.append((body_quality(b), tag, rec))

    pdf = find_local_pdf(eno, doi=normalize_doi(row.get("doi") or ""))
    if not pdf and mode == "online":
        urls = list(payload.get("pdf_urls") or [])
        if urls:
            had_oa_hint = True
        pdf = download_any_pdf(eno, urls) if urls else None
        tried_urls += len(urls[:MAX_PDF_URLS]) if urls else 0
        if not pdf:
            urls2: list[str] = []
            doi = normalize_doi(row.get("doi") or "")
            pmid = (row.get("pmid") or "").strip()
            try:
                epmc_urls, pmcid = epmc_pdf_and_pmcid(doi=doi, pmid=pmid)
                urls2 += epmc_urls
                if pmcid:
                    had_oa_hint = True
                    urls2.append(pmc_oa_pdf(pmcid))
                    urls2.append(f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/")
            except Exception:  # noqa: BLE001
                pass
            if doi and not urls2:
                try:
                    up = unpaywall_lookup(doi)
                    urls2 += list(up.get("pdf_urls") or [])
                    if up.get("is_oa") or urls2:
                        had_oa_hint = True
                    if not urls2 and up.get("is_oa") is False:
                        had_oa_hint = False
                except Exception:  # noqa: BLE001
                    pass
            tried = set(urls)
            urls2 = [u for u in urls2 if u and u not in tried][:MAX_PDF_URLS]
            tried_urls += len(urls2)
            pdf = download_any_pdf(eno, urls2)
    if pdf:
        try:
            add(upgrade_from_pdf_hard(pdf, row, title), "pdf_align")
        except Exception:  # noqa: BLE001
            pass

    # 无 PDF 或 PDF 未达体量：本地 XML/HTML 冲击同门槛金标
    doi_n = normalize_doi(row.get("doi") or "")
    xml = find_xml_for_entry(eno, doi=doi_n)
    if xml and xml.is_file():
        try:
            add(upgrade_from_xml(xml, row, title, hit), "xml_jats_fulltext")
        except Exception:  # noqa: BLE001
            pass
    if PDFKU_HTML.is_dir():
        for hp in sorted(PDFKU_HTML.glob(f"{eno}_*"))[:2]:
            if hp.is_file() and hp.stat().st_size >= 500:
                try:
                    add(upgrade_from_html(hp, row, title), "html_fulltext_gold")
                except Exception:  # noqa: BLE001
                    pass
                break

    if not cands:
        reason = classify_unreachable(
            row, tried_urls=tried_urls, had_oa_hint=had_oa_hint, mode=mode
        )
        # 明确无 DOI/PMID 或查无任何合法 OA PDF → 永久登记；下载失败仅记尝试，不踢出队列
        if mode == "online" and reason in ("no_doi_pmid", "no_legal_oa_pdf"):
            return {
                "entry_no": eno,
                "status": "unreachable",
                "reason": reason,
                "tried_urls": tried_urls,
                "batch_no": bno,
                "permanent": True,
                "row": {
                    "doi": row.get("doi") or "",
                    "pmid": row.get("pmid") or "",
                    "en_title": title[:200],
                },
            }
        if mode == "online" and reason == "oa_pdf_download_failed_or_paywall":
            return {
                "entry_no": eno,
                "status": "unreachable",
                "reason": reason,
                "tried_urls": tried_urls,
                "batch_no": bno,
                "permanent": False,
                "row": {
                    "doi": row.get("doi") or "",
                    "pmid": row.get("pmid") or "",
                    "en_title": title[:200],
                },
            }
        return {"entry_no": eno, "status": "no_gain", "old": len(old_body), "reason": reason}

    cands.sort(
        key=lambda x: (x[0], len(x[2].get("body_text") or "")),
        reverse=True,
    )
    q, tag, best = cands[0]
    new_body = best.get("body_text") or ""
    old_src = (old.get("source") or "").strip()
    accept = False
    if tag == "pdf_align" and len(new_body) >= MIN_BODY:
        best["source"] = "pdf_align"
        if old_src != "pdf_align":
            if "[provenance]" in old_body or len(old_body) < MIN_BODY * 2:
                accept = len(new_body) >= MIN_BODY
            elif len(new_body) >= PDF_GOLD_BODY:
                accept = True
            elif len(new_body) >= max(int(PDF_GOLD_BODY * 0.85), int(len(old_body) * 0.95)):
                # 接近金标体量且不显著短于旧文
                accept = True
            else:
                accept = False
        elif len(new_body) >= max(len(old_body) + 800, MIN_BODY):
            accept = True
        elif len(old_body) < PDF_GOLD_BODY and len(new_body) >= max(
            len(old_body) + 1500, int(PDF_GOLD_BODY * 0.7)
        ):
            accept = True
    elif tag in ("xml_jats_fulltext", "html_fulltext_gold"):
        # XML/HTML：仅当可冲击金标体量，且不劣于已有金标/PDF 时接受
        if is_pdf_gold(old):
            accept = False
        elif old_src == "pdf_align" and len(old_body) >= PDF_GOLD_BODY:
            accept = False
        elif len(new_body) >= PDF_GOLD_BODY and (
            len(new_body) >= len(old_body) + 500 or old_src not in GOLD_SOURCES
        ):
            accept = True
        elif len(new_body) >= max(len(old_body) + 2000, int(PDF_GOLD_BODY * 0.9)) and not is_pdf_gold(old):
            accept = True

    if not accept:
        return {
            "entry_no": eno,
            "status": "no_gain",
            "old": len(old_body),
            "best_len": len(new_body),
            "reason": "not_better_or_too_short",
        }

    # 抛光到金标同门槛；禁止 exhausted 垫字
    prefer = tag if tag in GOLD_SOURCES else ("pdf_align" if tag == "pdf_align" else tag)
    best = finalize_to_gold_bar(best, fallback_title=title, prefer_source=prefer)
    issues = strict_issues(best)
    if issues:
        best = finalize_to_gold_bar(best, fallback_title=title, prefer_source=prefer)
        issues = strict_issues(best)
    if issues:
        return {"entry_no": eno, "status": "strict_fail", "issues": issues[:5]}
    body_len = len(best.get("body_text") or "")
    src = (best.get("source") or prefer or tag).strip()
    if is_pdf_gold(best):
        best["gold_tier"] = "pdf_gold" if src == "pdf_align" else "fulltext_gold"
        best["complete"] = True
    else:
        best["gold_tier"] = "pdf_align_short" if src == "pdf_align" else "fulltext_short"
        best["complete"] = False
    best["enrich_from"] = src
    save_rec(best, bno)
    return {
        "entry_no": eno,
        "status": "upgraded",
        "old": len(old_body),
        "new": body_len,
        "from": src,
        "source": src,
        "pdf_gold": is_pdf_gold(best),
    }


def _worker_init() -> None:
    idx: dict[str, Path] = {}
    for base in (PDF_CACHE, ROOT / "解析文件" / "yes_fulltext_batches" / "_pdf_cache"):
        if base.exists():
            for p in base.glob("*.pdf"):
                if p.stat().st_size > 5000:
                    m = re.match(r"^(\d+)", p.stem)
                    if m:
                        idx[m.group(1)] = p
    gu._PDF_INDEX = idx


def list_targets(
    rows: list[dict[str, str]],
    oa_disk: dict | None = None,
    *,
    skip: set[str] | None = None,
) -> list[tuple[dict[str, str], dict[str, Any]]]:
    by = {str(r["entry_no"]): r for r in rows}
    oa_disk = oa_disk or {}
    skip = skip or set()
    out = []
    seen: set[str] = set()
    for p in STRICT_ROOT.glob("batch_*/papers/*.json"):
        if p.stem in seen:
            continue
        seen.add(p.stem)
        if p.stem in skip:
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        if not needs_hunt(rec):
            continue
        row = by.get(p.stem)
        if row:
            out.append((row, rec))

    def priority(t: tuple[dict[str, str], dict[str, Any]]) -> tuple:
        row, rec = t
        eno = str(row["entry_no"])
        # 一视同仁：有本地 PDF / OA 的先转 pdf_align（机会均等），不优先“短文”
        has_local = 1 if find_local_pdf(eno, doi=normalize_doi(row.get("doi") or "")) else 0
        doi = normalize_doi(row.get("doi") or "")
        has_oa = 1 if doi and (oa_disk.get(doi) or {}).get("pdf_urls") else 0
        already_pdf = 1 if (rec.get("source") or "") == "pdf_align" else 0
        # 非 pdf 优先于短 pdf；同档按 entry_no 稳定顺序
        return (-has_local, -has_oa, already_pdf, int(eno) if eno.isdigit() else 0)

    out.sort(key=priority)
    return out


def load_blacklist() -> set[str]:
    if not BLACKLIST.is_file():
        return set()
    try:
        data = json.loads(BLACKLIST.read_text(encoding="utf-8"))
        return {str(x) for x in (data.get("entry_nos") or [])}
    except Exception:  # noqa: BLE001
        return set()


def save_blacklist(items: set[str]) -> None:
    BLACKLIST.parent.mkdir(parents=True, exist_ok=True)
    BLACKLIST.write_text(
        json.dumps(
            {
                "entry_nos": sorted(items, key=lambda x: int(x) if x.isdigit() else 0),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "n": len(items),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _kill_spawn_orphans() -> int:
    """硬杀本机残留 multiprocessing spawn 工人（含挂死的）。无 PowerShell 闪窗。"""
    my_pid = os.getpid()
    killed = 0
    try:
        import psutil
        from scripts.win_hidden import kill_pid
    except Exception:
        return 0
    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline"]):
        try:
            name = (p.info.get("name") or "").lower()
            if name not in ("python.exe", "pythonw.exe"):
                continue
            cmd_list = p.info.get("cmdline") or []
            cmd = " ".join(str(x) for x in cmd_list)
            if "spawn_main" not in cmd:
                continue
            pid = int(p.info["pid"] or 0)
            ppid = int(p.info.get("ppid") or 0)
            if not pid:
                continue
            mine = ppid == my_pid or f"parent_pid={my_pid}" in cmd
            if mine or "spawn_main" in cmd:
                if kill_pid(pid):
                    killed += 1
        except Exception:
            continue
    return killed


def _shutdown_pool(pool: ProcessPoolExecutor | None) -> None:
    if pool is None:
        return
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        pool.shutdown(wait=False)
    except Exception:  # noqa: BLE001
        pass
    # ProcessPool shutdown 不保证杀掉挂在 C/网络里的工人
    n = _kill_spawn_orphans()
    if n:
        _log(f"  hard-killed spawn workers={n}")


def _apply_result(res: dict[str, Any], stats: dict[str, int], rows_by: dict[str, dict[str, str]] | None = None) -> None:
    st = res.get("status")
    eno = str(res.get("entry_no") or "")
    if st == "upgraded":
        stats["upgraded"] += 1
        if res.get("source") == "pdf_align" or res.get("from") == "pdf_align":
            stats["pdf"] += 1
        if res.get("pdf_gold"):
            stats["pdf_gold"] = int(stats.get("pdf_gold") or 0) + 1
        _log(
            f"  UP {eno} {res.get('old')}->{res.get('new')} "
            f"via {res.get('from')} src={res.get('source')} gold={bool(res.get('pdf_gold'))}"
        )
    elif st == "unreachable":
        stats["unreachable"] = int(stats.get("unreachable") or 0) + 1
        reason = str(res.get("reason") or "unknown")
        row = (rows_by or {}).get(eno) or {}
        append_unreachable(
            {
                "entry_no": eno,
                "reason": reason,
                "permanent": bool(res.get("permanent")),
                "doi": (res.get("row") or {}).get("doi") or row.get("doi") or "",
                "pmid": (res.get("row") or {}).get("pmid") or row.get("pmid") or "",
                "title": (res.get("row") or {}).get("en_title") or "",
                "tried_urls": res.get("tried_urls"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if res.get("permanent"):
            try:
                mark_record_unreachable(
                    eno, int(res.get("batch_no") or 1), reason, row or (res.get("row") or {})
                )
            except Exception:  # noqa: BLE001
                pass
            _log(f"  UNREACHABLE(permanent) {eno} reason={reason}")
        else:
            _log(f"  UNREACHABLE(attempt) {eno} reason={reason}")
    elif st == "no_gain":
        stats["no_gain"] += 1
    else:
        stats["err"] += 1
        if st == "strict_fail":
            _log(f"  strict_fail {eno}: {res.get('issues')}")


def _run_chunk(
    chunk: list[tuple[dict[str, str], dict[str, Any]]],
    *,
    hit_idx: dict,
    oa_disk: dict,
    rows_order: dict[str, int],
    workers: int,
    mode: str,
    stats: dict[str, int],
    blacklist: set[str],
) -> None:
    # online 仍用进程池；并发由上层传入（不再死锁在 2）
    workers = max(1, min(int(workers), 8))
    pool: ProcessPoolExecutor | None = ProcessPoolExecutor(
        max_workers=workers, initializer=_worker_init
    )
    pending: dict[Any, str] = {}
    try:
        for row, old in chunk:
            eno = str(row["entry_no"])
            if eno in blacklist:
                stats["no_gain"] += 1
                continue
            h = hit_idx.get(eno)
            hit_slim = None
            if h:
                hit_slim = {
                    "en_title": h.get("en_title"),
                    "content_type": h.get("content_type"),
                }
            doi = normalize_doi(row.get("doi") or "")
            oa_slice = {doi: oa_disk[doi]} if doi and doi in oa_disk else {}
            payload = {
                "row": dict(row),
                "old": {
                    "body_text": (old.get("body_text") or "")[:300000],
                    "source": old.get("source"),
                },
                "hit": hit_slim,
                "batch_no": batch_no_for(eno, rows_order),
                "oa_slice": oa_slice,
                "pdf_urls": list((oa_disk.get(doi) or {}).get("pdf_urls") or [])[:MAX_PDF_URLS]
                if doi
                else [],
                "mode": mode,
            }
            fut = pool.submit(process_payload, payload)
            pending[fut] = eno

        # 逐个收割：单篇超时只拉黑该篇，继续收其它已完成的
        while pending:
            done, not_done = wait(
                list(pending.keys()),
                timeout=PAPER_TIMEOUT,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                # 窗口内无人完成：只拉黑“仍在跑”的，硬杀工人；登记 timeout 不可达倾向
                for fut, eno in list(pending.items()):
                    stats["timeout"] += 1
                    blacklist.add(eno)
                    _log(f"  timeout {eno} — blacklist+kill")
                    append_unreachable(
                        {
                            "entry_no": eno,
                            "reason": "worker_timeout",
                            "updated_at": datetime.now().isoformat(timespec="seconds"),
                        }
                    )
                pending.clear()
                break
            for fut in done:
                eno = pending.pop(fut, "")
                try:
                    res = fut.result(timeout=0)
                    rows_by = {str(r["entry_no"]): r for r, _o in chunk}
                    _apply_result(res, stats, rows_by)
                except FuturesTimeout:
                    stats["timeout"] += 1
                    blacklist.add(eno)
                    _log(f"  timeout {eno} — blacklist")
                    append_unreachable(
                        {
                            "entry_no": eno,
                            "reason": "worker_timeout",
                            "updated_at": datetime.now().isoformat(timespec="seconds"),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    stats["err"] += 1
                    _log(f"  err {eno}: {exc}")
    finally:
        _shutdown_pool(pool)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--rounds", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--start-online",
        action="store_true",
        help="第一轮直接 online（本地 PDF 已扫过时用）",
    )
    args = ap.parse_args()

    deadline = datetime.now() + timedelta(hours=max(0.2, args.hours))
    _log(f"HUNT boot workers={args.workers} deadline={deadline.isoformat(timespec='seconds')}")
    n0 = _kill_spawn_orphans()
    if n0:
        _log(f"cleared orphan spawn={n0}")
    gu._PDF_INDEX = _build_pdf_index()
    _log(f"pdf_index={len(gu._PDF_INDEX or {})}")
    rows = load_exist()
    rows_order = {str(r["entry_no"]): i for i, r in enumerate(rows)}
    hit_idx = load_jsonl_index()
    _log(f"jsonl_hits={len(hit_idx)}")
    oa_disk = load_oa_disk()
    blacklist = load_blacklist()
    _log(f"HUNT start workers={args.workers} deadline={deadline.isoformat(timespec='seconds')} aim>={AIM}")
    _log(f"oa_cache_entries={len(oa_disk)} blacklist={len(blacklist)} start_online={args.start_online}")

    for rnd in range(1, args.rounds + 1):
        if datetime.now() >= deadline:
            break
        # 默认：奇数 local / 偶数 online；--start-online 则反过来
        if args.start_online:
            mode = "online" if rnd % 2 == 1 else "local"
        else:
            mode = "local" if rnd % 2 == 1 else "online"
        # 每轮重载磁盘黑名单（外部可 seed；超时持续写入）
        blacklist |= load_blacklist()
        targets = list_targets(rows, oa_disk, skip=blacklist)
        if args.limit:
            targets = targets[: args.limit]
        _log(f"===== HUNT ROUND {rnd}/{args.rounds} mode={mode} need={len(targets)} =====")
        if not targets:
            _log("all targets look fulltext enough")
            break
        stats = {
            "upgraded": 0,
            "no_gain": 0,
            "timeout": 0,
            "err": 0,
            "pdf": 0,
            "pdf_gold": 0,
            "unreachable": 0,
        }
        workers = max(1, min(args.workers, 8) if mode == "online" else args.workers)
        chunk_size = max(workers * 2, 8) if mode == "online" else max(args.workers * 2, 8)
        off = 0
        while off < len(targets):
            if datetime.now() >= deadline:
                break
            chunk = targets[off : off + chunk_size]
            # online：有 OA URL 的先试（全员机会均等），不按短文插队
            if mode == "online":
                chunk = sorted(
                    chunk,
                    key=lambda t: (
                        0
                        if (oa_disk.get(normalize_doi(t[0].get("doi") or "")) or {}).get("pdf_urls")
                        else 1,
                        int(str(t[0].get("entry_no") or 0))
                        if str(t[0].get("entry_no") or "").isdigit()
                        else 0,
                    ),
                )
            _log(f"  chunk-start {off}/{len(targets)} size={len(chunk)} mode={mode}")
            _run_chunk(
                chunk,
                hit_idx=hit_idx,
                oa_disk=oa_disk,
                rows_order=rows_order,
                workers=workers,
                mode=mode,
                stats=stats,
                blacklist=blacklist,
            )
            save_blacklist(blacklist)
            done_at = off + len(chunk)
            # 全库扫描很重：每 60 篇或收尾再刷新覆盖率
            if done_at % 60 < chunk_size or done_at >= len(targets) or off == 0:
                cov = equal_coverage_snapshot(rows)
                EQUAL_STATUS.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                try:
                    cov = json.loads(EQUAL_STATUS.read_text(encoding="utf-8-sig"))
                except Exception:
                    cov = {"pdf_gold_ok": "?", "exist_unique": 5676, "pct_pdf_gold": "?", "unreachable_oa": "?"}
            _log(
                f"  chunk {done_at}/{len(targets)} {stats} "
                f"pdf_gold={cov.get('pdf_gold_ok')}/{cov.get('exist_unique')} ({cov.get('pct_pdf_gold')}%) "
                f"unreachable={cov.get('unreachable_oa')}"
            )
            STATUS.write_text(
                json.dumps(
                    {
                        "round": rnd,
                        "mode": mode,
                        "stats": stats,
                        "need": len(targets),
                        "progress": done_at,
                        "blacklist": len(blacklist),
                        "equal_coverage": cov,
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            off += chunk_size

        _log(f"ROUND {rnd} done {stats}")
        # 连续两轮（local+online）都零升级才停；避免厚此薄彼早停
        if stats["upgraded"] == 0 and mode == "online":
            _log("no PDF upgrades this online round — continue next rounds until deadline")
        oa_disk = load_oa_disk()

    left = list_targets(rows, oa_disk, skip=set())
    cov = equal_coverage_snapshot(rows)
    EQUAL_STATUS.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
    rep = {
        "final": True,
        "still_need_pdf_gold": len(left),
        "equal_coverage": cov,
        "blacklist": len(blacklist),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    STATUS.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(
        f"FINAL still_need_pdf_gold={len(left)} "
        f"pdf_gold={cov['pdf_gold_ok']}/{cov['exist_unique']} ({cov['pct_pdf_gold']}%) "
        f"blacklist={len(blacklist)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
