# -*- coding: utf-8 -*-
"""删除质量不达标记录并加入 LLM 重试队列。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from mysql_client import MySQLClient
from sqlalchemy import text

from scripts.audit_pubmed_judgement_quality import main as audit_main
from scripts.judge_quality_guard import RETRY_FILE

AUDIT = ROOT / "data" / "literature" / "pubmed_judgement_reaudit.json"


def main() -> int:
    audit_main()
    if not AUDIT.is_file():
        print("审计报告不存在")
        return 1
    report = json.loads(AUDIT.read_text(encoding="utf-8"))
    pmids = report.get("bad_pmids") or []
    if not pmids:
        print("无需重跑，全部达标")
        return 0

    client = MySQLClient()
    db = next(client.get_db())
    try:
        for pmid in pmids:
            db.execute(
                text("DELETE FROM literature_pubmed_judgement WHERE pmid = :p"),
                {"p": pmid},
            )
        db.commit()
    finally:
        db.close()

    retry = {"items": {}, "count": 0}
    if RETRY_FILE.is_file():
        try:
            retry = json.loads(RETRY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    items = dict(retry.get("items") or {})
    for pmid in pmids:
        items[pmid] = {
            "attempts": 0,
            "last_error": "quality_reaudit",
            "updated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
        }
    RETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    RETRY_FILE.write_text(
        json.dumps({"items": items, "count": len(items)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已删除 {len(pmids):,} 条不达标记录，加入重试队列")
    print(f"  重试文件: {RETRY_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
