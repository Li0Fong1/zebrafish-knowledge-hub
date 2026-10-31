# -*- coding: utf-8 -*-
"""对剩余缺口多轮强攻：source_url / Unpaywall / PMC OA / EPMC / doi.org。"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from rag.fulltext_cascade import normalize_doi, try_download_pdf as cascade_dl, unpaywall_lookup  # noqa: E402
from rag.online_fulltext_fetch import fetch_url_bytes, html_to_text  # noqa: E402
from scripts.gold_gap_fill import collect_urls, download_pdf, polish_pass, pmc_oa_pdf, ncbi_pmcid  # noqa: E402
from scripts.gold_strict_full_campaign import (  # noqa: E402
    MIN_BODY,
    STRICT_ROOT,
    audit_strict,
    batch_no_for,
    empty_noise,
    fetch_epmc_xml_file,
    load_oa_cache,
    paper_path,
    save_rec,
    strict_issues,
)
from scripts.gold_upgrade_5680 import (  # noqa: E402
    PDF_CACHE,
    find_local_pdf,
    find_xml_for_entry,
    load_exist,
    load_jsonl_index,
    upgrade_from_jsonl,
    upgrade_from_pdf,
    upgrade_from_xml,
    _build_pdf_index,
)
import scripts.gold_upgrade_5680 as gu  # noqa: E402


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def gaps(rows):
    out = []
    for r in rows:
        eno = str(r["entry_no"])
        p = paper_path(eno)
        if not p:
            out.append(r)
            continue
        if strict_issues(json.loads(p.read_text(encoding="utf-8"))):
            out.append(r)
    return out


def try_doi_pdf(doi: str, dest: Path) -> Path | None:
    if not doi:
        return None
    url = f"https://doi.org/{doi}"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 ZebrafishGeneQA/1.0",
                "Accept": "application/pdf,*/*",
            },
        )
        with urllib.request.urlopen(req, timeout=18) as resp:
            data = resp.read()
            final = resp.geturl()
        if data[:5].startswith(b"%PDF"):
            dest.write_bytes(data)
            return dest
        # 若落地是 pdf 链接
        if "pdf" in (final or "").lower():
            ok, _ = cascade_dl(final, str(dest), timeout=15)
            if ok:
                return dest
    except Exception:  # noqa: BLE001
        return None
    return None


def process_one(r, hit, oa_cache, rows_order):
    eno = str(r["entry_no"])
    title = (r.get("en_title") or (hit or {}).get("en_title") or "").strip()
    bno = batch_no_for(eno, rows_order)
    rec = None

    # 1 source_url
    url = (r.get("source_url") or "").strip()
    if url:
        try:
            raw, code, _ = fetch_url_bytes(url, timeout=15, fast=True)
            if raw and int(code or 0) < 400:
                if raw[:5].startswith(b"%PDF"):
                    PDF_CACHE.mkdir(parents=True, exist_ok=True)
                    dest = PDF_CACHE / f"{eno}.pdf"
                    dest.write_bytes(raw)
                    rec = upgrade_from_pdf(dest, r, title)
                else:
                    text = html_to_text(raw)
                    if len(text) >= MIN_BODY:
                        rec = {
                            "entry_no": eno,
                            "title": title,
                            "doi": r.get("doi") or "",
                            "pmid": r.get("pmid") or "",
                            "content_type": "html",
                            "source": "jsonl_gold_deep",
                            "layout": "generic",
                            "body_text": text[:120000],
                            "sections": {},
                            "noise": empty_noise(),
                        }
        except Exception:  # noqa: BLE001
            pass

    # 2 OA pdf urls
    pdf = find_local_pdf(eno)
    if not pdf:
        try:
            urls = collect_urls(r, oa_cache)
            pdf = download_pdf(eno, urls)
        except Exception:  # noqa: BLE001
            pdf = None
    if not pdf:
        doi = normalize_doi(r.get("doi") or "")
        PDF_CACHE.mkdir(parents=True, exist_ok=True)
        pdf = try_doi_pdf(doi, PDF_CACHE / f"{eno}.pdf")
    if pdf:
        try:
            rp = upgrade_from_pdf(pdf, r, title)
            if rp and (
                not rec
                or rp.get("source") == "pdf_align"
                or len(rp.get("body_text") or "") > len((rec or {}).get("body_text") or "")
            ):
                rec = rp
        except Exception:  # noqa: BLE001
            pass

    # 3 xml
    if not rec or len(rec.get("body_text") or "") < MIN_BODY:
        xml = None
        try:
            xml = fetch_epmc_xml_file(eno, r)
        except Exception:  # noqa: BLE001
            pass
        if not xml:
            xml = find_xml_for_entry(eno)
        if xml:
            try:
                rx = upgrade_from_xml(xml, r, title, hit)
                if rx and (not rec or len(rx.get("body_text") or "") > len(rec.get("body_text") or "")):
                    rec = rx
            except Exception:  # noqa: BLE001
                pass

    # 4 jsonl raw html less scrub
    if (not rec or len(rec.get("body_text") or "") < MIN_BODY) and hit:
        raw = hit.get("body_text") or ""
        if len(raw) >= MIN_BODY:
            rec = {
                "entry_no": eno,
                "title": title,
                "doi": r.get("doi") or "",
                "pmid": r.get("pmid") or "",
                "content_type": hit.get("content_type") or "text",
                "source": "jsonl_gold_deep",
                "layout": "generic",
                "body_text": raw[:120000],
                "sections": {},
                "noise": empty_noise(),
            }
        else:
            try:
                rj = upgrade_from_jsonl(r, title, hit)
                if rj:
                    rec = rj
            except Exception:  # noqa: BLE001
                pass

    if not rec:
        return False
    rec = polish_pass(rec)
    save_rec(rec, bno)
    return not strict_issues(rec)


def main() -> int:
    gu._PDF_INDEX = _build_pdf_index()
    rows = load_exist()
    rows_order = {str(r["entry_no"]): i for i, r in enumerate(rows)}
    hit = load_jsonl_index()
    oa_cache = load_oa_cache()
    for rnd in range(1, 4):
        g = gaps(rows)
        _log(f"ROUND {rnd} gaps={len(g)}")
        if not g:
            break
        ok = 0
        for i, r in enumerate(g, 1):
            try:
                if process_one(r, hit.get(str(r["entry_no"])), oa_cache, rows_order):
                    ok += 1
            except Exception as exc:  # noqa: BLE001
                _log(f"  err {r.get('entry_no')}: {exc}")
            if i % 40 == 0:
                _log(f"  {i}/{len(g)} ok_this_round={ok}")
            time.sleep(0.05)
        aud = audit_strict()
        _log(f"  round_done ok={ok} strict={aud['strict']} pending={aud['pending']} files={aud['n_files']}")
        if aud["pending"] == 0 and aud["n_files"] >= len(rows):
            break
    aud = audit_strict()
    have = {p.stem for p in STRICT_ROOT.glob("batch_*/papers/*.json")}
    missing = sum(1 for r in rows if str(r["entry_no"]) not in have)
    _log(f"FINAL strict={aud['strict']} pending={aud['pending']} missing={missing} / {len(rows)}")
    (STRICT_ROOT / "assault_final.json").write_text(
        json.dumps({**aud, "missing": missing, "corpus": len(rows)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if missing == 0 and aud["pending"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
