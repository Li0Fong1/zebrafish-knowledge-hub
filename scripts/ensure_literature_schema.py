# -*- coding: utf-8 -*-
"""确保 PubMed / WoS 文献相关表已创建。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from sqlalchemy import text

from common_utils import safe_print
from mysql_client import MySQLClient

MIGRATIONS = (
    ROOT / "sql" / "literature_pubmed_migration.sql",
    ROOT / "sql" / "literature_pubmed_judgement_migration.sql",
    ROOT / "sql" / "literature_wos_migration.sql",
    ROOT / "sql" / "literature_vector_chunk_migration.sql",
    ROOT / "sql" / "literature_appendable_store_migration.sql",
    ROOT / "sql" / "literature_zfqa_id_migration.sql",
    ROOT / "sql" / "journal_metric_migration.sql",
)


def _apply_sql(client: MySQLClient, path: Path) -> None:
    db = next(client.get_db())
    try:
        for stmt in path.read_text(encoding="utf-8").replace("\r\n", "\n").split(";"):
            s = " ".join(
                ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")
            ).strip()
            if not s or s.upper().startswith("USE "):
                continue
            db.execute(text(s))
        db.commit()
        safe_print(f"  [OK] {path.name}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    client = MySQLClient()
    for path in MIGRATIONS:
        if not path.is_file():
            safe_print(f"  [SKIP] 未找到 {path}")
            continue
        _apply_sql(client, path)
    safe_print("[完成] 文献表结构已就绪")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
