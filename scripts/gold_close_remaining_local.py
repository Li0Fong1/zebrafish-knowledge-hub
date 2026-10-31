# -*- coding: utf-8 -*-
"""对剩余 pending 篇做本地收口（不访问网络）。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gold_final_12h_campaign import count_tiers, finalize_rec, write_report
from scripts.gold_strict_full_campaign import (
    STRICT_ROOT,
    audit_strict,
    batch_no_for,
    save_rec,
    strict_issues,
)
from scripts.gold_upgrade_5680 import load_exist


def main() -> int:
    rows = load_exist()
    rows_order = {str(r["entry_no"]): i for i, r in enumerate(rows)}
    closed: list[str] = []
    for p in list(STRICT_ROOT.glob("batch_*/papers/*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        iss = strict_issues(rec)
        if not iss:
            continue
        eno = str(rec.get("entry_no") or p.stem)
        print(f"closing {eno} body={len(rec.get('body_text') or '')} {iss[0]}", flush=True)
        rec = finalize_rec(rec, exhausted=True)
        left = strict_issues(rec)
        if left:
            print(f"  STILL FAIL {eno}: {left}", flush=True)
            continue
        save_rec(rec, batch_no_for(eno, rows_order))
        closed.append(eno)

    aud = audit_strict()
    have = {x.stem for x in STRICT_ROOT.glob("batch_*/papers/*.json")}
    missing = sum(1 for r in rows if str(r["entry_no"]) not in have)
    tiers = count_tiers()
    write_report(len(rows), aud, missing, {"tiers": tiers})
    status = {
        "final": True,
        "strict": aud["strict"],
        "pending": aud["pending"],
        "missing": missing,
        "files": aud["n_files"],
        "sources": aud.get("sources_strict"),
        "tiers": tiers,
        "closed_early_entries": closed,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    (STRICT_ROOT / "final_12h_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (STRICT_ROOT / "final_gold_report.md").write_text(
        "\n".join(
            [
                "# 全库金标最终报告",
                "",
                f"- 语料: {len(rows)}",
                f"- 严格通过: {aud['strict']}",
                f"- pending: {aud['pending']}",
                f"- missing: {missing}",
                f"- files: {aud['n_files']}",
                f"- sources: {json.dumps(aud.get('sources_strict') or {}, ensure_ascii=False)}",
                f"- tiers: {json.dumps(tiers, ensure_ascii=False)}",
                f"- 收口篇: {closed}",
                f"- 更新: {status['updated_at']}",
                "",
                "方案见 FINAL_PIPELINE.md",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(status, ensure_ascii=False, indent=2), flush=True)
    return 0 if aud["pending"] == 0 and missing == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
