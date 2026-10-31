# -*- coding: utf-8 -*-
"""Normalize potato dump to eval pair order; dedupe by question."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "项目阶段" / "01_金标全文" / "清单与验收" / "eval_isomorph_100_20260901.json"
OUT = ROOT / "logs" / "_potato_qa_dump.json"


def main() -> None:
    pairs = json.loads(EVAL.read_text(encoding="utf-8"))["isomorphic_pairs"]
    best: dict[str, dict] = {}
    if OUT.exists():
        for row in json.loads(OUT.read_text(encoding="utf-8")).get("qa") or []:
            q = (row.get("question") or "").strip()
            if not q:
                continue
            n = len((row.get("assistant") or {}).get("content") or "")
            prev = len((best.get(q, {}).get("assistant") or {}).get("content") or "")
            if n >= prev:
                best[q] = row

    ordered = []
    for p in pairs:
        q = p["potato"]
        ordered.append(best.get(q) or {"question": q, "assistant": {"content": ""}})

    OUT.write_text(
        json.dumps({"qa": ordered, "source": "isomorph_pairs"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    n_ok = sum(1 for r in ordered if len((r.get("assistant") or {}).get("content") or "") > 80)
    print("normalized ok=", n_ok, "/ 20")


if __name__ == "__main__":
    main()
