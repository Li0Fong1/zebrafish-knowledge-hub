# -*- coding: utf-8 -*-
"""从 release/zebrafish_db_full.sql 恢复基因库（gene_basic 等 8 张表）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from core.config import DB_CONFIG

SQL_FILE = ROOT / "release" / "zebrafish_db_full.sql"


def _run_mysql_cli() -> bool:
    if not SQL_FILE.is_file():
        safe_print(f"[错误] 未找到 {SQL_FILE}")
        return False
    mysql = os.getenv("MYSQL_BIN", "mysql")
    cmd = [
        mysql,
        f"-h{DB_CONFIG['host']}",
        f"-P{DB_CONFIG['port']}",
        f"-u{DB_CONFIG['user']}",
        f"-p{DB_CONFIG['password']}",
        DB_CONFIG["database"],
    ]
    safe_print(f"导入 {SQL_FILE.name}（{SQL_FILE.stat().st_size // (1024 * 1024)} MB）…")
    try:
        with SQL_FILE.open("rb") as f:
            r = subprocess.run(cmd, stdin=f, capture_output=True, timeout=3600)
        if r.returncode != 0:
            err = (r.stderr or b"").decode("utf-8", errors="replace")[:500]
            safe_print(f"[错误] mysql 退出码 {r.returncode}: {err}")
            return False
        safe_print("[成功] SQL 导入完成")
        return True
    except FileNotFoundError:
        safe_print("[提示] 未找到 mysql 客户端，改用 Python 流式导入…")
        return False
    except subprocess.TimeoutExpired:
        safe_print("[错误] 导入超时")
        return False


def _run_python_stream() -> bool:
    import pymysql

    if not SQL_FILE.is_file():
        return False
    conn = pymysql.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        database=DB_CONFIG["database"],
        charset="utf8mb4",
        autocommit=True,
    )
    buf = ""
    n_stmt = 0
    try:
        with SQL_FILE.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("--") or not stripped:
                    continue
                buf += line
                if stripped.endswith(";"):
                    with conn.cursor() as cur:
                        cur.execute(buf)
                    buf = ""
                    n_stmt += 1
                    if n_stmt % 500 == 0:
                        safe_print(f"  …已执行 {n_stmt} 条语句")
        if buf.strip():
            with conn.cursor() as cur:
                cur.execute(buf)
        safe_print(f"[成功] Python 流式导入完成（{n_stmt} 条语句）")
        return True
    except Exception as e:
        safe_print(f"[错误] Python 导入失败: {e}")
        return False
    finally:
        conn.close()


def main() -> int:
    if _run_mysql_cli():
        return 0
    return 0 if _run_python_stream() else 1


if __name__ == "__main__":
    raise SystemExit(main())
