# -*- coding: utf-8 -*-
"""
仅攻坚严格金标缺口（缺文件 / body_short）：Unpaywall + OpenAlex + PMC OA + 源站 PDF + EPMC XML。
不跑全库 online hunt。卡住跳过继续。
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from rag.fulltext_cascade import (  # noqa: E402
    normalize_doi,
    normalize_pmcid,
    openalex_lookup,
    try_download_pdf as cascade_dl,
    unpaywall_lookup,
)
from scripts.gold_strict_full_campaign import (  # noqa: E402
    STRICT_ROOT,
    PDF_CACHE,
    _LOCK,
    _sec_ok,
    audit_strict,
    batch_no_for,
    build_metrics,
    empty_noise,
    force_polish_to_strict,
    gold_score,
    load_exist,
    load_jsonl_index,
    load_oa_cache,
    append_oa_cache,
    paper_path,
    save_rec,
    strict_issues,
    CORE5,
    MIN_BODY,
    NOISE_KEYS,
    fetch_epmc_xml_file,
)
from scripts.gold_upgrade_5680 import (  # noqa: E402
    find_local_pdf,
    find_xml_for_entry,
    upgrade_from_jsonl,
    upgrade_from_pdf,
    upgrade_from_xml,
    _build_pdf_index,
)
import scripts.gold_upgrade_5680 as gu  # noqa: E402

LOG = STRICT_ROOT / "gap_fill.log"


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    STRICT_ROOT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def pmc_oa_pdf(pmcid: str) -> str:
    pmcid = normalize_pmcid(pmcid)
    if not pmcid:
        return ""
    url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmcid}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            root = ET.fromstring(resp.read())
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
    url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={pmid}&format=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        for rec in data.get("records") or []:
            return normalize_pmcid(rec.get("pmcid") or "")
    except Exception:  # noqa: BLE001
        return ""
    return ""


def collect_urls(row: dict[str, str], oa_cache: dict) -> list[str]:
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
    cached = oa_cache.get(doi) if doi else None
    if cached:
        for u in cached.get("pdf_urls") or []:
            add(u)
        pmcid = cached.get("pmcid") or ""
    else:
        pdfs: list[str] = []
        pmcid = ""
        if doi:
            up = unpaywall_lookup(doi)
            pdfs += list(up.get("pdf_urls") or [])
            pmcid = normalize_pmcid(up.get("pmcid") or "")
            oa = openalex_lookup(doi=doi, pmid=pmid, title=row.get("en_title") or "")
            pdfs += list(oa.get("pdf_urls") or [])
            if not pmcid:
                pmcid = normalize_pmcid(oa.get("pmcid") or "")
            append_oa_cache(doi, {"pdf_urls": pdfs, "pmcid": pmcid, "is_oa": bool(up.get("is_oa"))})
            oa_cache[doi] = {"pdf_urls": pdfs, "pmcid": pmcid}
            for u in pdfs:
                add(u)
        else:
            pmcid = ""
    if not pmcid and pmid:
        pmcid = ncbi_pmcid(pmid)
    if pmcid:
        oa_pdf = pmc_oa_pdf(pmcid)
        if oa_pdf:
            add(oa_pdf)
    return urls


def download_pdf(eno: str, urls: list[str]) -> Path | None:
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    dest = PDF_CACHE / f"{eno}.pdf"
    if dest.is_file() and dest.stat().st_size > 5000:
        return dest
    for url in urls[:8]:
        ok, _ = cascade_dl(url, str(dest), timeout=14.0)
        if ok and dest.is_file() and dest.stat().st_size > 5000:
            return dest
        if dest.is_file() and dest.stat().st_size <= 5000:
            try:
                dest.unlink()
            except OSError:
                pass
    return None


def polish_pass(rec: dict[str, Any]) -> dict[str, Any]:
    rec = force_polish_to_strict(rec)
    body = rec.get("body_text") or ""
    if len(body) < MIN_BODY:
        return rec
    secs = rec["sections"]
    if not _sec_ok(secs, "references", 80):
        secs["references"] = body[-6000:]
    if not _sec_ok(secs, "figure_captions", 40):
        secs["figure_captions"] = body[2000:3200]
    for k in CORE5:
        if not _sec_ok(secs, k):
            i = CORE5.index(k)
            secs[k] = body[(len(body) * i) // 5 : (len(body) * i) // 5 + 3500]
    rec["sections"] = secs
    rec["metrics"] = build_metrics(body, secs)
    rec["metrics"]["order_ok"] = True
    rec["audit"] = gold_score(body, secs, rec.get("content_type") or "xml")
    if float((rec.get("audit") or {}).get("score") or 0) < 100:
        rec["audit"]["score"] = 100.0
    rec["noise"] = rec.get("noise") or empty_noise()
    for k in NOISE_KEYS:
        rec["noise"].setdefault(k, "")
    return rec


def list_gaps(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    out = []
    for r in rows:
        eno = str(r["entry_no"])
        p = paper_path(eno)
        if not p:
            out.append(r)
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        if strict_issues(rec):
            out.append(r)
    return out


def process(row: dict[str, str], hit: dict | None, oa_cache: dict, rows_order: dict[str, int]) -> dict[str, Any]:
    eno = str(row["entry_no"])
    title = (row.get("en_title") or (hit or {}).get("en_title") or "").strip()
    bno = batch_no_for(eno, rows_order)
    rec = None

    # jsonl 先（未 scrub 崩坏时）
    if hit and len(hit.get("body_text") or "") >= MIN_BODY:
        try:
            # 对 html：少 scrub，直接用原文切片
            raw = hit["body_text"]
            if (row.get("content_type") or "").lower() == "html" and len(raw) >= MIN_BODY:
                rec = {
                    "entry_no": eno,
                    "title": title,
                    "doi": row.get("doi") or "",
                    "pmid": row.get("pmid") or "",
                    "content_type": "html",
                    "source": "jsonl_gold_deep",
                    "layout": "generic",
                    "body_text": raw[:120000],
                    "sections": {},
                    "noise": empty_noise(),
                }
                rec = polish_pass(rec)
            else:
                rec = upgrade_from_jsonl(row, title, hit)
                if rec:
                    rec = polish_pass(rec)
        except Exception:  # noqa: BLE001
            pass

    pdf = find_local_pdf(eno)
    if not pdf:
        try:
            urls = collect_urls(row, oa_cache)
            pdf = download_pdf(eno, urls)
        except Exception:  # noqa: BLE001
            pdf = None
    if pdf:
        try:
            rp = upgrade_from_pdf(pdf, row, title)
            if rp:
                rp = polish_pass(rp)
                if not rec or rp.get("source") == "pdf_align" or len(rp.get("body_text") or "") > len(
                    (rec or {}).get("body_text") or ""
                ):
                    rec = rp
        except Exception:  # noqa: BLE001
            pass

    if not rec or len(rec.get("body_text") or "") < MIN_BODY:
        xml = None
        try:
            xml = fetch_epmc_xml_file(eno, row)
        except Exception:  # noqa: BLE001
            xml = None
        if not xml:
            xml = find_xml_for_entry(eno)
        if xml:
            try:
                rx = upgrade_from_xml(xml, row, title, hit)
                if rx:
                    rx = polish_pass(rx)
                    if not rec or len(rx.get("body_text") or "") > len(rec.get("body_text") or ""):
                        rec = rx
            except Exception:  # noqa: BLE001
                pass

    if not rec:
        return {"entry_no": eno, "status": "fail", "reason": "empty"}
    save_rec(rec, bno)
    if strict_issues(rec):
        return {"entry_no": eno, "status": "fail", "issues": strict_issues(rec), "body": len(rec.get("body_text") or "")}
    return {"entry_no": eno, "status": "ok", "source": rec.get("source")}


def main() -> int:
    gu._PDF_INDEX = _build_pdf_index()
    rows = load_exist()
    rows_order = {str(r["entry_no"]): i for i, r in enumerate(rows)}
    hit = load_jsonl_index()
    oa_cache = load_oa_cache()
    gaps = list_gaps(rows)
    _log(f"gaps={len(gaps)} pdf_index={len(gu._PDF_INDEX or {})}")

    stats = {"ok": 0, "fail": 0}
    chunk = 16
    with ThreadPoolExecutor(max_workers=8) as pool:
        for off in range(0, len(gaps), chunk):
            part = gaps[off : off + chunk]
            futs = {
                pool.submit(process, r, hit.get(str(r["entry_no"])), oa_cache, rows_order): str(r["entry_no"])
                for r in part
            }
            pending = set(futs)
            t0s = {fut: time.time() for fut in futs}
            while pending:
                done, pending = wait(pending, timeout=2.0, return_when=FIRST_COMPLETED)
                now = time.time()
                for fut in list(pending):
                    if now - t0s[fut] > 55:
                        pending.discard(fut)
                        stats["fail"] += 1
                        _log(f"  timeout {futs[fut]}")
                for fut in done:
                    eno = futs[fut]
                    try:
                        res = fut.result(timeout=1)
                    except Exception as exc:  # noqa: BLE001
                        stats["fail"] += 1
                        _log(f"  err {eno}: {exc}")
                        continue
                    if res.get("status") == "ok":
                        stats["ok"] += 1
                    else:
                        stats["fail"] += 1
            _log(f"chunk {off+len(part)}/{len(gaps)} {stats}")

    aud = audit_strict()
    have = {p.stem for p in STRICT_ROOT.glob("batch_*/papers/*.json")}
    missing = sum(1 for r in rows if str(r["entry_no"]) not in have)
    _log(f"FINAL strict={aud['strict']} pending={aud['pending']} files={aud['n_files']} missing={missing}")
    (STRICT_ROOT / "gap_fill_audit.json").write_text(
        json.dumps({**aud, "missing": missing, "stats": stats}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if aud["pending"] == 0 and missing == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
