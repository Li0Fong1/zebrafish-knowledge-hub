# -*- coding: utf-8 -*-
"""Rebuild blind pack using cached hub answers + potato dump (no re-ask)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from run_eval_isomorph import auto_metrics, build_blind_pack, load_potato_cache  # noqa: E402

EVAL = ROOT / "项目阶段" / "01_金标全文" / "清单与验收" / "eval_isomorph_100_20260901.json"
ANS = ROOT / "logs" / "eval_isomorph" / "_eval_isomorph_answers.json"
OUT = ROOT / "logs" / "eval_isomorph"


def main() -> None:
    data = json.loads(EVAL.read_text(encoding="utf-8"))
    answers = {r["q"]: r for r in json.loads(ANS.read_text(encoding="utf-8"))}
    potato = load_potato_cache()
    pair_rows = []
    for p in data.get("isomorphic_pairs") or []:
        zq = p["zebrafish"]
        pq = p["potato"]
        zr = answers.get(zq) or {}
        za = zr.get("answer") or ""
        pa = potato.get(pq, "")
        pair_rows.append(
            {
                "pair_id": p["pair_id"],
                "type": p.get("type"),
                "zebrafish_q": zq,
                "potato_q": pq,
                "zebrafish_answer": za,
                "zebrafish_metrics": zr.get("metrics") or auto_metrics(za, p.get("type") or ""),
                "potato_answer": pa,
                "potato_metrics": auto_metrics(pa, p.get("type") or "") if pa else None,
            }
        )
    build_blind_pack(pair_rows, data.get("rubric") or {}, OUT / "blind_pack.json")
    n_p = sum(1 for r in pair_rows if r["potato_answer"])
    n_z = sum(1 for r in pair_rows if r["zebrafish_answer"])
    print("blind rebuilt", OUT / "盲评打分表.md", "zebrafish", n_z, "potato", n_p)


if __name__ == "__main__":
    main()
