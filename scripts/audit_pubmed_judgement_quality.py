# -*- coding: utf-8 -*-
"""审计 PubMed 判别质量，输出需重跑的 PMID 列表。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.judge_literature_zebrafish_batch import INVENTORY_SUMMARY_MAX, NO_ABSTRACT_LABEL
from mysql_client import MySQLClient
from sqlalchemy import text

OUT = ROOT / "data" / "literature" / "pubmed_judgement_reaudit.json"
REASON_OK_META = frozenset({"XML 无标题", "XML 解析失败"})


def _has_chinese(s: str) -> bool:
    return bool(s and re.search(r"[\u4e00-\u9fff]", s))


def main() -> int:
    client = MySQLClient()
    db = next(client.get_db())
    try:
        rows = db.execute(
            text(
                "SELECT pmid, title_en, abstract_en, title_zh, abstract_zh, "
                "judgement, reason, mode FROM literature_pubmed_judgement"
            )
        ).fetchall()
    finally:
        db.close()

    bad: list[dict] = []
    ok = 0
    for pmid, te, ae, tz, az, j, reason, mode in rows:
        issues: list[str] = []
        m = (mode or "").strip()

        if m == "rule":
            if (reason or "") not in REASON_OK_META:
                issues.append("rule_fallback")
        elif m not in ("llm", "hybrid", "meta"):
            issues.append(f"unknown_mode:{m}")

        if m in ("llm", "hybrid", "rule") and te and not (tz or "").strip():
            issues.append("missing_title")

        empty_summary = frozenset({"", NO_ABSTRACT_LABEL, "无摘要", "（无摘要）"})
        if m in ("llm", "hybrid") and ae and len(ae.strip()) > 80:
            az_s = (az or "").strip()
            if az_s in empty_summary or len(az_s) < 8:
                issues.append("missing_summary")
            elif len(az_s) > INVENTORY_SUMMARY_MAX + 40:
                issues.append("summary_too_long")

        r = (reason or "").lower()
        if any(k in r for k in ("http 4", "http 5", "api ", "规则引擎", "requeue_was_rule", "timeout")):
            issues.append("bad_reason")

        if issues:
            bad.append({"pmid": str(pmid), "mode": m, "issues": issues, "reason": (reason or "")[:120]})
        else:
            ok += 1

    by_issue: dict[str, int] = {}
    for b in bad:
        for i in b["issues"]:
            by_issue[i] = by_issue.get(i, 0) + 1

    pmids = [b["pmid"] for b in bad]
    report = {
        "total": len(rows),
        "ok": ok,
        "bad": len(bad),
        "by_issue": by_issue,
        "bad_pmids": pmids,
        "samples": bad[:15],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"已判别 {len(rows):,} 篇")
    print(f"  达标: {ok:,}")
    print(f"  需重跑: {len(bad):,}")
    if by_issue:
        print("  问题分布:", by_issue)
    if bad[:5]:
        print("  样例:", bad[:3])
    print(f"  报告: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
