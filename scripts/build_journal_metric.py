# -*- coding: utf-8 -*-
"""从 curated CSV + literature_paper DISTINCT journal 构建 journal_metric 表。"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "database", ROOT / "rag"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from core.entry import bootstrap_app

bootstrap_app()

from sqlalchemy import text

from common_utils import safe_print
from mysql_client import get_mysql_client
from journal_if import (
    IF_NORM_CAP,
    TIER_BOOST,
    UNKNOWN_SCORE,
    if_norm_from_value,
    norm_journal,
    tier_from_if,
)

_MIGRATION = ROOT / "sql" / "journal_metric_migration.sql"
_CURATED = ROOT / "data" / "journal_tier_curated.csv"


def _ensure_table(client) -> None:
    db = next(client.get_db())
    try:
        row = db.execute(
            text(
                "SELECT COUNT(*) AS n FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='journal_metric'"
            )
        ).mappings().first()
        if row and int(row["n"] or 0) > 0:
            safe_print("  [SKIP] journal_metric 已存在")
            return
        for stmt in _MIGRATION.read_text(encoding="utf-8").replace("\r\n", "\n").split(";"):
            s = " ".join(
                ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")
            ).strip()
            if not s or s.upper().startswith("USE "):
                continue
            db.execute(text(s))
        db.commit()
        safe_print("  [OK] journal_metric 已创建")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _upsert(db, row: dict) -> None:
    db.execute(
        text(
            "INSERT INTO journal_metric "
            "(journal_key, display_name, if_value, if_year, if_source, tier, tier_boost, if_norm) "
            "VALUES (:jk, :dn, :iv, :iy, :src, :tier, :tb, :inn) "
            "ON DUPLICATE KEY UPDATE "
            "display_name=VALUES(display_name), if_value=VALUES(if_value), "
            "if_source=VALUES(if_source), tier=VALUES(tier), "
            "tier_boost=VALUES(tier_boost), if_norm=VALUES(if_norm)"
        ),
        row,
    )


def load_curated(client) -> int:
    if not _CURATED.is_file():
        safe_print(f"  [WARN] 缺少 {_CURATED}")
        return 0
    n = 0
    db = next(client.get_db())
    try:
        with _CURATED.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                key = norm_journal(r.get("journal_key") or r.get("display_name") or "")
                if not key:
                    continue
                iv_raw = (r.get("if_value") or "").strip()
                iv = float(iv_raw) if iv_raw else None
                tier = (r.get("tier") or tier_from_if(iv, key)).upper()
                if tier not in TIER_BOOST:
                    tier = "T4"
                inn = if_norm_from_value(iv) if iv is not None else UNKNOWN_SCORE
                _upsert(
                    db,
                    {
                        "jk": key,
                        "dn": (r.get("display_name") or key)[:512],
                        "iv": iv,
                        "iy": 2024,
                        "src": (r.get("if_source") or "curated")[:32],
                        "tier": tier,
                        "tb": TIER_BOOST[tier],
                        "inn": inn,
                    },
                )
                n += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return n


def map_paper_journals(client) -> int:
    """把库内 DISTINCT journal 未入表的写成 T4 占位，便于覆盖率统计。"""
    db = next(client.get_db())
    added = 0
    try:
        rows = db.execute(
            text(
                "SELECT DISTINCT journal FROM literature_paper "
                "WHERE journal IS NOT NULL AND journal <> ''"
            )
        ).mappings().all()
        for r in rows:
            key = norm_journal(r.get("journal") or "")
            if not key:
                continue
            exists = db.execute(
                text("SELECT 1 FROM journal_metric WHERE journal_key=:k LIMIT 1"),
                {"k": key},
            ).first()
            if exists:
                continue
            _upsert(
                db,
                {
                    "jk": key,
                    "dn": (r.get("journal") or key)[:512],
                    "iv": None,
                    "iy": None,
                    "src": "auto",
                    "tier": "T4",
                    "tb": TIER_BOOST["T4"],
                    "inn": UNKNOWN_SCORE,
                },
            )
            added += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return added


def coverage_report(client) -> None:
    db = next(client.get_db())
    try:
        total = db.execute(text("SELECT COUNT(*) FROM literature_paper")).scalar() or 0
        with_j = db.execute(
            text(
                "SELECT COUNT(*) FROM literature_paper "
                "WHERE journal IS NOT NULL AND journal <> ''"
            )
        ).scalar() or 0
        mapped = db.execute(
            text(
                "SELECT COUNT(*) FROM literature_paper lp "
                "JOIN journal_metric jm ON jm.journal_key = LOWER(TRIM(lp.journal)) "
                "OR jm.display_name = lp.journal"
            )
        ).scalar() or 0
        tiers = db.execute(
            text("SELECT tier, COUNT(*) c FROM journal_metric GROUP BY tier ORDER BY tier")
        ).mappings().all()
        safe_print(f"[覆盖] papers={total} with_journal={with_j} join_hit≈{mapped}")
        for t in tiers:
            safe_print(f"  {t['tier']}: {t['c']}")
    finally:
        db.close()


def main() -> int:
    client = get_mysql_client()
    _ensure_table(client)
    n = load_curated(client)
    safe_print(f"[完成] curated 写入/更新 {n} 条")
    a = map_paper_journals(client)
    safe_print(f"[完成] 库内期刊占位新增 {a} 条")
    coverage_report(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
