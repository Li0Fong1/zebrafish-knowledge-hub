# -*- coding: utf-8 -*-
"""为 literature_paper 全量赋稳定自编号 ZFQA-000001 …（幂等，已有号跳过）。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "database"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from core.entry import bootstrap_app

bootstrap_app()

from sqlalchemy import text

from common_utils import safe_print
from mysql_client import get_mysql_client

_MIGRATION = ROOT / "sql" / "literature_zfqa_id_migration.sql"
_ZFQA_RE = re.compile(r"^ZFQA-(\d{6})$")


def _ensure_column(client) -> None:
    db = next(client.get_db())
    try:
        row = db.execute(
            text(
                "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='literature_paper' "
                "AND COLUMN_NAME='zfqa_id'"
            )
        ).mappings().first()
        if row and int(row["n"] or 0) > 0:
            safe_print("  [SKIP] zfqa_id 列已存在")
            return
        for stmt in _MIGRATION.read_text(encoding="utf-8").replace("\r\n", "\n").split(";"):
            s = " ".join(
                ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")
            ).strip()
            if not s or s.upper().startswith("USE "):
                continue
            db.execute(text(s))
        db.commit()
        safe_print("  [OK] zfqa_id 列与唯一索引已创建")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _max_seq(client) -> int:
    db = next(client.get_db())
    try:
        row = db.execute(
            text(
                "SELECT MAX(CAST(SUBSTRING(zfqa_id, 6) AS UNSIGNED)) AS mx "
                "FROM literature_paper WHERE zfqa_id REGEXP '^ZFQA-[0-9]{6}$'"
            )
        ).mappings().first()
        return int(row["mx"] or 0) if row else 0
    finally:
        db.close()


def assign(*, batch: int = 2000) -> int:
    """批量赋号：MySQL 8 ROW_NUMBER 一次 UPDATE 一批，避免逐行锁竞争。"""
    client = get_mysql_client()
    safe_print("  [1/4] 检查 zfqa_id 列…")
    _ensure_column(client)
    safe_print("  [2/4] 读取当前最大序号…")
    seq = _max_seq(client)
    safe_print(f"  [2/4] 当前最大序号 {seq}")
    assigned = 0
    while True:
        db = next(client.get_db())
        try:
            pending = db.execute(
                text(
                    "SELECT COUNT(*) AS n FROM literature_paper "
                    "WHERE zfqa_id IS NULL OR zfqa_id=''"
                )
            ).scalar() or 0
            if not pending:
                break
            lim = min(int(batch), int(pending))
            safe_print(f"  [3/4] 本批赋号 {lim} 条（剩余 {pending}）…")
            db.execute(text("SET SESSION innodb_lock_wait_timeout = 120"))
            try:
                db.execute(
                    text(
                        "UPDATE literature_paper lp "
                        "INNER JOIN ( "
                        "  SELECT doc_id, ROW_NUMBER() OVER (ORDER BY doc_id) + :base AS rn "
                        "  FROM literature_paper "
                        "  WHERE zfqa_id IS NULL OR zfqa_id='' "
                        "  ORDER BY doc_id LIMIT :lim "
                        ") t ON lp.doc_id = t.doc_id "
                        "SET lp.zfqa_id = CONCAT('ZFQA-', LPAD(t.rn, 6, '0'))"
                    ),
                    {"base": seq, "lim": lim},
                )
            except Exception as bulk_ex:
                safe_print(f"  [WARN] 批量 UPDATE 失败，改逐行: {bulk_ex!r}")
                rows = db.execute(
                    text(
                        "SELECT doc_id FROM literature_paper "
                        "WHERE zfqa_id IS NULL OR zfqa_id='' ORDER BY doc_id LIMIT :lim"
                    ),
                    {"lim": lim},
                ).mappings().all()
                for row in rows:
                    seq += 1
                    db.execute(
                        text("UPDATE literature_paper SET zfqa_id=:z WHERE doc_id=:d"),
                        {"z": f"ZFQA-{seq:06d}", "d": row["doc_id"]},
                    )
                assigned += len(rows)
                db.commit()
                safe_print(f"  [4/4] 累计已赋号 {assigned} 条（当前序号 {seq}）")
                continue
            db.commit()
            seq += lim
            assigned += lim
            safe_print(f"  [4/4] 累计已赋号 {assigned} 条（当前序号 {seq}）")
        except Exception as ex:
            db.rollback()
            safe_print(f"  [ERR] 赋号失败: {ex!r}")
            raise
        finally:
            db.close()
    return assigned


def verify(client) -> None:
    db = next(client.get_db())
    try:
        total = db.execute(text("SELECT COUNT(*) AS n FROM literature_paper")).scalar() or 0
        filled = db.execute(
            text("SELECT COUNT(*) AS n FROM literature_paper WHERE zfqa_id IS NOT NULL AND zfqa_id<>''")
        ).scalar() or 0
        sample = db.execute(
            text(
                "SELECT doc_id, zfqa_id, pmid, entry_no FROM literature_paper "
                "WHERE zfqa_id IS NOT NULL ORDER BY zfqa_id LIMIT 5"
            )
        ).mappings().all()
        safe_print(f"[验收] total={total} with_zfqa={filled} missing={total - filled}")
        for r in sample:
            safe_print(f"  {r['zfqa_id']} doc={r['doc_id']} pmid={r.get('pmid')} eno={r.get('entry_no')}")
    finally:
        db.close()


def main() -> int:
    client = get_mysql_client()
    n = assign()
    safe_print(f"[完成] 本次新赋 {n} 条")
    verify(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
