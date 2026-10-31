# -*- coding: utf-8 -*-
"""One-shot: polish pdfku long pdf_align shorts into is_pdf_gold."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_hunt_fulltext import PDF_GOLD_BODY, is_pdf_gold  # noqa: E402
from scripts.gold_pdfku_library_full_gold import (  # noqa: E402
    MANIFEST,
    OUT,
    _append_jsonl,
    _finalize_pdf,
    save_rec,
)


def main() -> int:
    fixed = 0
    checked = 0
    for p in OUT.glob("batch_*/papers/*.json"):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if is_pdf_gold(rec):
            continue
        body = rec.get("body_text") or ""
        if (rec.get("source") or "") != "pdf_align" or len(body) < PDF_GOLD_BODY:
            continue
        checked += 1
        doi = (rec.get("doi") or "").strip()
        eno = str(rec.get("entry_no") or p.stem)
        tip = f"DOI {doi}" if doi else f"Entry {eno} fulltext"
        neu = _finalize_pdf(dict(rec), fallback_title=tip)
        if is_pdf_gold(neu):
            neu["gold_tier"] = "pdf_gold"
            neu["complete"] = True
            neu["corpus"] = "pdfku_library_fulltext_gold"
            save_rec(neu)
            _append_jsonl(
                MANIFEST,
                {
                    "entry_no": eno,
                    "doi": doi,
                    "body_len": len(body),
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "via": "polish_long_short",
                },
            )
            fixed += 1
            print(f"FIX {eno} len={len(body)}", flush=True)
    print(f"DONE checked={checked} fixed={fixed}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
