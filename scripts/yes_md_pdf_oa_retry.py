# -*- coding: utf-8 -*-
"""通过 NCBI PMC OA 接口补下 PDF。"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from rag.knowledge_ingest import extract_pdf_text
from scripts.yes_md_pdf_test_report import (
    REPORT_JSON,
    REPORT_MD,
    _save_outputs,
    render_report_md,
)

OUT_DIR = ROOT / "zebrafish_package" / "结果" / "pdf_parse_test"


def oa_pdf_url(pmcid: str) -> str:
    url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmcid}"
    with urllib.request.urlopen(url, timeout=20) as resp:
        root = ET.fromstring(resp.read())
    for link in root.findall(".//link"):
        if (link.get("format") or "").lower() == "pdf":
            href = link.get("href") or ""
            if href:
                return href
    return ""


def main() -> int:
    data = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    results = data["results"]
    changed = False
    for r in results:
        pmcid = r.get("pmcid") or ""
        if not pmcid or r.get("pdf_download_ok"):
            continue
        try:
            pdf_url = oa_pdf_url(pmcid)
            if not pdf_url:
                continue
            no = r["entry_no"]
            dest = OUT_DIR / f"{no}_oa.pdf"
            with urllib.request.urlopen(pdf_url, timeout=30) as resp:
                blob = resp.read()
            if not blob[:5].startswith(b"%PDF"):
                continue
            dest.write_bytes(blob)
            text = extract_pdf_text(str(dest))
            r["pdf_download_ok"] = True
            r["pdf_download_note"] = pdf_url
            r["pdf_path"] = str(dest)
            if len(text.strip()) > r.get("char_count", 0):
                r["parse_source"] = "pdf"
                r["char_count"] = len(text)
                r["text_preview"] = text[:1500]
            changed = True
            print(f"[{no}] PDF OK {len(blob)} bytes")
        except Exception as e:
            print(f"[{no}] PDF fail: {e}")
    if changed:
        generated_at = data.get("generated_at", "")
        _save_outputs(results, out_md=REPORT_MD, out_json=REPORT_JSON, generated_at=generated_at)
        print("updated report")
    else:
        print("no pdf downloaded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
