# -*- coding: utf-8 -*-
"""
清空斑马鱼数据库全部业务数据 + 本地文献/RAG/缓存（不可逆）。

用法：python -m scripts.wipe_all_zebrafish_data --confirm WIPE
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from sqlalchemy import text

from common_utils import safe_print
from config import _BASE_DIR
from mysql_client import MySQLClient

# 保留目录结构，只清内容
WIPE_DIRS = [
    os.path.join(_BASE_DIR, "data", "literature"),
    os.path.join(_BASE_DIR, "data", "wos_import"),
    os.path.join(_BASE_DIR, "data", "faiss_index"),
    os.path.join(_BASE_DIR, "rag", "knowledge.d"),
    os.path.join(_BASE_DIR, "rag", "uploads"),
]

WIPE_FILES = [
    os.path.join(_BASE_DIR, "rag", "knowledge.txt"),
    os.path.join(_BASE_DIR, "data", "ensembl", "literature_progress.json"),
    os.path.join(_BASE_DIR, "data", "ensembl", "literature_progress.json.bak"),
    os.path.join(_BASE_DIR, "data", "ensembl", "literature_epmc_cursor.txt"),
    os.path.join(_BASE_DIR, "data", "literature", "gene_link_progress.json"),
]


def _list_tables(client: MySQLClient) -> list[str]:
    r = client.execute_sql(
        "SELECT TABLE_NAME AS t FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME",
        max_rows=500,
    )
    if not r.get("success"):
        raise RuntimeError(r.get("error"))
    return [row["t"] for row in (r.get("data") or [])]


def wipe_mysql(client: MySQLClient) -> None:
    tables = _list_tables(client)
    if not tables:
        safe_print("  MySQL：无表可清")
        return
    db = next(client.get_db())
    try:
        db.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for t in tables:
            safe_print(f"  TRUNCATE `{t}` …")
            db.execute(text(f"TRUNCATE TABLE `{t}`"))
        db.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        db.commit()
        safe_print(f"  MySQL：已清空 {len(tables)} 张表")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _clear_dir(path: str) -> int:
    p = Path(path)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
        return 0
    n = 0
    for child in p.iterdir():
        if child.is_file():
            child.unlink()
            n += 1
        elif child.is_dir():
            shutil.rmtree(child)
            n += 1
    return n


def wipe_local() -> None:
    total = 0
    for d in WIPE_DIRS:
        n = _clear_dir(d)
        total += n
        safe_print(f"  目录 {d}：删除 {n} 项")
    for f in WIPE_FILES:
        if os.path.isfile(f):
            os.remove(f)
            total += 1
            safe_print(f"  文件 {f}：已删")
    # 重建 wos readme
    wos = os.path.join(_BASE_DIR, "data", "wos_import")
    os.makedirs(wos, exist_ok=True)
    with open(os.path.join(wos, "README.txt"), "w", encoding="utf-8") as wf:
        wf.write("WoS RIS 导入目录（已清空）\n")
    safe_print(f"  本地文献/RAG 共清理 {total} 项")


def main() -> int:
    p = argparse.ArgumentParser(description="清空数据库与本地文献/RAG 数据")
    p.add_argument("--confirm", required=True, help="必须传入 WIPE 才执行")
    args = p.parse_args()
    if args.confirm != "WIPE":
        safe_print("[拒绝] 需要 --confirm WIPE")
        return 1

    safe_print("【开始清空】斑马鱼数据库 + 文献/RAG 本地数据 …")
    client = MySQLClient()
    wipe_mysql(client)
    wipe_local()
    safe_print("[完成] 全部数据已清空")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
