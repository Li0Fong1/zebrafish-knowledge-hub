# -*- coding: utf-8 -*-
"""
Rebuild scihub_gold_nohup.log universe from:
  parse.out + new_batch jsonl + gold JSON scan + unreachable + inbox PDFs.

Run on server only:
  python -u scripts/rebuild_parse_universe.py
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RT = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
BASE = Path(os.environ.get("ZEBRA_BASE", str(ROOT)))
os.environ.setdefault("ZEBRA_RUNTIME", str(RT))
os.environ.setdefault("ZEBRA_BASE", str(BASE))
os.environ.setdefault("PROJECT", str(BASE))

from core.entry import bootstrap_app

bootstrap_app()
from scripts.gold_hunt_fulltext import is_pdf_gold

GOLD = RT / "gold_ingest" / "yes_fulltext_gold"
OUT = RT / "logs" / "scihub_gold_nohup.log"
NEW = RT / "logs" / "scihub_new_batch_parse.jsonl"
PARSE_OUT = RT / "logs" / "scihub_gold_parse.out"
UN = RT / "gold_ingest" / "logs" / "gold_unreachable_manifest.jsonl"
INBOX = RT / "gold_ingest" / "pdfku_inbox" / "scihub_batch"
FLOOR = 35487


def main() -> None:
    rows: dict[str, dict] = {}

    def put(o: dict) -> None:
        eno = str(o.get("entry_no") or "").strip()
        if not eno:
            return
        prev = rows.get(eno)
        score = (1 if o.get("gold") else 0, int(o.get("body_len") or 0))
        if prev is None:
            rows[eno] = o
            return
        pscore = (1 if prev.get("gold") else 0, int(prev.get("body_len") or 0))
        if score >= pscore:
            rows[eno] = {**prev, **o}

    for src in (PARSE_OUT, NEW):
        if not src.is_file():
            continue
        with src.open(encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln.startswith("{"):
                    continue
                try:
                    put(json.loads(ln))
                except Exception:
                    continue
    print("after_logs", len(rows))

    n_gold_files = 0
    n_gold_true = 0
    for p in GOLD.glob("batch_*/papers/*.json"):
        n_gold_files += 1
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        eno = str(rec.get("entry_no") or p.stem)
        gold = bool(is_pdf_gold(rec))
        if gold:
            n_gold_true += 1
        put(
            {
                "entry_no": eno,
                "out": str(p),
                "body_len": len(rec.get("body_text") or ""),
                "floor": FLOOR,
                "source": rec.get("source"),
                "gold_tier": rec.get("gold_tier"),
                "gold": gold,
                "status": "gold" if gold else "short_or_fail",
                "from": "gold_json_scan",
            }
        )
    print("gold_json_files", n_gold_files, "is_pdf_gold", n_gold_true, "rows", len(rows))

    if UN.is_file():
        with UN.open(encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                eno = str(o.get("id") or o.get("entry_no") or "").strip()
                if not eno or eno in rows:
                    continue
                put(
                    {
                        "entry_no": eno,
                        "body_len": int(o.get("body_len") or 0),
                        "gold": False,
                        "status": str(o.get("reason") or "unreachable"),
                        "from": "unreachable",
                    }
                )
    print("after_unreachable", len(rows))

    for pdf in INBOX.glob("*.pdf"):
        m = re.match(r"^(\d+)", pdf.stem)
        if not m:
            continue
        eno = m.group(1)
        if eno not in rows:
            put({"entry_no": eno, "pdf": str(pdf), "gold": False, "status": "inbox_unparsed", "from": "inbox"})
    print("after_inbox", len(rows))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "\n".join(
            json.dumps(rows[k], ensure_ascii=False)
            for k in sorted(rows, key=lambda x: int(x) if x.isdigit() else 0)
        )
        + "\n",
        encoding="utf-8",
    )
    gold_n = sum(1 for v in rows.values() if v.get("gold"))
    print(json.dumps({"wrote": str(OUT), "universe": len(rows), "gold_flagged": gold_n}, ensure_ascii=False))


if __name__ == "__main__":
    main()
