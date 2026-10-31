# -*- coding: utf-8 -*-
"""
将清洗后的 CSV 导回 MySQL（zebrafish_db）。

用法：
  # 1. 先备份（推荐）
  scripts\\backup_database.bat

  # 2. 把清洗后的 CSV 放到同一目录（保持文件名）
  #    例如 项目阶段/05_数据与库/归档/data_export/cleaned_in/gene_basic.csv

  # 3. 导入（按 gene_id / id 更新或整表替换）
  python -m scripts.import_cleaned_gene_data --dir 项目阶段/05_数据与库/归档/data_export/cleaned_in
  python -m scripts.import_cleaned_gene_data --dir 项目阶段/05_数据与库/归档/data_export/cleaned_in --tables gene_basic,gene_reference
  python -m scripts.import_cleaned_gene_data --dir 项目阶段/05_数据与库/归档/data_export/cleaned_in --mode replace --tables gene_expression

模式：
  upsert   默认。有主键则更新，无则插入（gene_basic / 带 id 的子表）
  replace  先 DELETE/TRUNCATE 再整表导入（仅建议用于单表全量重导）
"""
from core.entry import bootstrap_app

bootstrap_app()

import argparse
import csv
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from common_utils import logger, safe_print
from config import _BASE_DIR
from mysql_client import MySQLClient

BATCH = 500

# 导出时子表含 gene_name/ensembl_id，导入时忽略
IGNORE_COLS = {"gene_name", "ensembl_id"}

TABLES = {
    "gene_basic": {
        "file": "gene_basic.csv",
        "columns": [
            "gene_id", "gene_name", "gene_symbol", "ensembl_id", "zfin_id",
            "chromosome", "gene_function", "interpro_domain", "uniprot_id", "expression_summary",
        ],
        "pk": "gene_id",
    },
    "gene_expression": {
        "file": "gene_expression.csv",
        "columns": ["id", "gene_id", "tissue", "expression_stage", "expression_level", "data_source"],
        "pk": "id",
    },
    "gene_go": {
        "file": "gene_go.csv",
        "columns": ["id", "gene_id", "go_term", "go_category"],
        "pk": "id",
    },
    "homology_gene": {
        "file": "homology_gene.csv",
        "columns": ["id", "gene_id", "species", "homo_gene_name", "homology_score"],
        "pk": "id",
    },
    "gene_reference": {
        "file": "gene_reference.csv",
        "columns": ["id", "gene_id", "pubmed_id", "title", "journal", "publish_year"],
        "pk": "id",
    },
    "gene_alias": {
        "file": "gene_alias.csv",
        "columns": ["id", "gene_id", "alias", "alias_type", "source", "is_primary"],
        "pk": "id",
    },
    "gene_xref": {
        "file": "gene_xref.csv",
        "columns": ["id", "gene_id", "db_name", "db_id", "db_label", "source"],
        "pk": "id",
    },
}


def _client() -> MySQLClient:
    return MySQLClient()


def _esc(val: Any) -> str:
    if val is None or val == "":
        return "NULL"
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return str(val)
    s = str(val).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"


def _read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _normalize_row(row: dict, columns: List[str]) -> dict:
    out = {}
    for col in columns:
        if col in row and row[col] not in (None, ""):
            out[col] = row[col]
        elif col in row:
            out[col] = None
    return out


def _validate_gene_ids(db, gene_ids: List[int]) -> List[int]:
    if not gene_ids:
        return []
    missing = []
    chunk = 500
    for i in range(0, len(gene_ids), chunk):
        part = gene_ids[i : i + chunk]
        ids_sql = ",".join(str(int(x)) for x in part)
        r = db.execute(text(f"SELECT gene_id FROM gene_basic WHERE gene_id IN ({ids_sql})"))
        found = {row[0] for row in r}
        for gid in part:
            if int(gid) not in found:
                missing.append(int(gid))
    return missing


def _upsert_batch(db, table: str, columns: List[str], rows: List[dict], pk: str):
    if not rows:
        return 0
    col_list = ", ".join(columns)
    updates = ", ".join(f"{c}=VALUES({c})" for c in columns if c != pk)
    values_sql = []
    for row in rows:
        vals = ", ".join(_esc(row.get(c)) for c in columns)
        values_sql.append(f"({vals})")
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES "
        + ", ".join(values_sql)
        + f" ON DUPLICATE KEY UPDATE {updates}"
    )
    db.execute(text(sql))
    return len(rows)


def _insert_batch(db, table: str, columns: List[str], rows: List[dict]):
    if not rows:
        return 0
    col_list = ", ".join(columns)
    values_sql = []
    for row in rows:
        vals = ", ".join(_esc(row.get(c)) for c in columns)
        values_sql.append(f"({vals})")
    sql = f"INSERT INTO {table} ({col_list}) VALUES " + ", ".join(values_sql)
    db.execute(text(sql))
    return len(rows)


def _find_csv(csv_dir: str, filename: str) -> Optional[str]:
    candidates = [
        os.path.join(csv_dir, filename),
        os.path.join(csv_dir, "02_tables", filename),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def import_table(
    table: str,
    csv_dir: str,
    mode: str = "upsert",
    dry_run: bool = False,
) -> dict:
    cfg = TABLES[table]
    path = _find_csv(csv_dir, cfg["file"])
    if not path:
        return {"success": False, "error": f"未找到 {cfg['file']}（请放在目录或 02_tables/ 下）"}

    raw_rows = _read_csv(path)
    columns = cfg["columns"]
    pk = cfg["pk"]
    rows = [_normalize_row(r, columns) for r in raw_rows]

    # 去掉空主键行
    if pk == "gene_id":
        rows = [r for r in rows if r.get("gene_id")]
    else:
        rows = [r for r in rows if r.get("gene_id")]

    client = _client()
    db = next(client.get_db())
    stats = {"table": table, "file": path, "read": len(raw_rows), "imported": 0}

    try:
        if table != "gene_basic":
            gene_ids = list({int(r["gene_id"]) for r in rows if r.get("gene_id")})
            missing = _validate_gene_ids(db, gene_ids)
            if missing:
                db.close()
                return {
                    "success": False,
                    "error": f"{table}: 存在无效 gene_id（不在 gene_basic 中）: {missing[:10]}… 共 {len(missing)} 个",
                }

        if dry_run:
            stats["success"] = True
            stats["dry_run"] = True
            return stats

        if mode == "replace":
            db.execute(text(f"DELETE FROM {table}"))
            db.commit()
            safe_print(f"  [{table}] 已清空旧数据")

        imported = 0
        buf = []
        for row in rows:
            buf.append(row)
            if len(buf) >= BATCH:
                if mode == "replace":
                    imported += _insert_batch(db, table, columns, buf)
                else:
                    imported += _upsert_batch(db, table, columns, buf, pk)
                buf.clear()
        if buf:
            if mode == "replace":
                imported += _insert_batch(db, table, columns, buf)
            else:
                imported += _upsert_batch(db, table, columns, buf, pk)

        db.commit()
        stats["imported"] = imported
        stats["success"] = True
        return stats
    except Exception as e:
        db.rollback()
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def import_all(csv_dir: str, tables: Optional[List[str]], mode: str, dry_run: bool):
    order = [
        "gene_basic",
        "gene_alias",
        "gene_xref",
        "gene_expression",
        "gene_go",
        "homology_gene",
        "gene_reference",
    ]
    if tables:
        order = [t for t in order if t in tables]

    safe_print(f"导入目录: {csv_dir}")
    safe_print(f"模式: {mode}" + (" (试运行)" if dry_run else ""))
    safe_print(f"表: {', '.join(order)}")
    safe_print("")

    results = []
    for table in order:
        safe_print(f"→ {table} …")
        r = import_table(table, csv_dir, mode=mode, dry_run=dry_run)
        results.append(r)
        if r.get("success"):
            safe_print(f"  OK 读取 {r.get('read', 0)} 行，写入 {r.get('imported', 0)} 行")
        else:
            safe_print(f"  FAIL {r.get('error')}")
            break

    ok = all(r.get("success") for r in results)
    if ok and not dry_run:
        safe_print("\n[完成] 清洗数据已导入。建议运行 scripts\\test_id_mapping 或 Web 抽查几条基因。")
    return ok


def main():
    p = argparse.ArgumentParser(description="导入清洗后的基因 CSV 到 MySQL")
    p.add_argument(
        "--dir",
        required=True,
        help="清洗后 CSV 所在目录（含 gene_basic.csv 等）",
    )
    p.add_argument(
        "--tables",
        default="",
        help="逗号分隔表名，默认全部。例：gene_basic,gene_reference",
    )
    p.add_argument(
        "--mode",
        choices=("upsert", "replace"),
        default="upsert",
        help="upsert=按主键更新；replace=清空该表后重导",
    )
    p.add_argument("--dry-run", action="store_true", help="只读 CSV 不写入")
    args = p.parse_args()

    csv_dir = args.dir
    if not os.path.isabs(csv_dir):
        csv_dir = os.path.join(_BASE_DIR, csv_dir)
    if not os.path.isdir(csv_dir):
        safe_print(f"[错误] 目录不存在: {csv_dir}")
        return 1

    tables = [t.strip() for t in args.tables.split(",") if t.strip()] or None
    if tables:
        bad = [t for t in tables if t not in TABLES]
        if bad:
            safe_print(f"[错误] 未知表: {bad}，可选: {list(TABLES.keys())}")
            return 1

    ok = import_all(csv_dir, tables, args.mode, args.dry_run)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
