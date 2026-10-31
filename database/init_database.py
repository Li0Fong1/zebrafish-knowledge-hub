# -*- coding: utf-8 -*-
"""一键初始化 MySQL 斑马鱼基因数据库。"""
from core.entry import bootstrap_app

bootstrap_app()

import os
import sys

from config import DB_CONFIG, _BASE_DIR
from common_utils import safe_print

SQL_FILE = os.path.join(_BASE_DIR, "sql", "zebrafish_db_init.sql")


def run_init():
    try:
        import pymysql
    except ImportError:
        safe_print("[错误] 请先安装 pymysql：pip install pymysql")
        sys.exit(1)

    if not os.path.isfile(SQL_FILE):
        safe_print(f"[错误] SQL 文件不存在：{SQL_FILE}")
        sys.exit(1)

    with open(SQL_FILE, "r", encoding="utf-8") as f:
        sql_content = f.read()

    # 去掉单行注释后按分号拆分
    statements = []
    buf = []
    for line in sql_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
    full = "\n".join(buf)
    for stmt in full.split(";"):
        s = stmt.strip()
        if s:
            statements.append(s)

    safe_print(f"[信息] 连接 MySQL {DB_CONFIG['host']}:{DB_CONFIG['port']} ...")
    conn = pymysql.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        charset=DB_CONFIG["charset"],
        autocommit=True,
    )

    try:
        with conn.cursor() as cur:
            for i, stmt in enumerate(statements, 1):
                try:
                    cur.execute(stmt)
                    if stmt.upper().startswith("SELECT"):
                        rows = cur.fetchall()
                        if rows:
                            safe_print("[结果]", rows[-1] if rows else rows)
                except Exception as e:
                    safe_print(f"[警告] 语句 {i} 执行异常：{e}")
        safe_print("[成功] 数据库初始化完成！")
        safe_print(f"       数据库名：{DB_CONFIG['database']}")
        safe_print("       可运行 python -m database.init_database 验证连接")
    finally:
        conn.close()


if __name__ == "__main__":
    run_init()
