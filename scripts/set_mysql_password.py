# -*- coding: utf-8 -*-
"""把本机 MySQL root 密码迁移为 .env 里的新密码，并回写 .env。

用法（**必须管理员权限**，因为要先启动 MySQL80 服务）::

    .venv\\Scripts\\python.exe -X utf8 -m scripts.set_mysql_password
    .venv\\Scripts\\python.exe -X utf8 -m scripts.set_mysql_password --dry-run

工作方式：
1. 从 .env 读取旧密码 ``MYSQL_PASSWORD`` 与新密码 ``MYSQL_PASSWORD_NEW``；
2. 用旧密码连上 MySQL，把 ``mysql.user`` 里所有 root 账户的密码改为新密码；
3. 用新密码重新连接，校验库与表数量；
4. 成功后把 ``MYSQL_PASSWORD`` 写成新值并删除 ``MYSQL_PASSWORD_NEW``。

密码只存在于 .env（已被 .gitignore 忽略、ACL 已收紧），不进源码、不进命令行历史。
重复执行是安全的：新旧密码相同时只做连接校验。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def read_env() -> dict:
    data = {}
    with open(ENV_PATH, encoding="utf-8-sig", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def write_env(updates: dict, drop=()) -> None:
    """只改动指定键，其余行（含注释、顺序、BOM）原样保留。"""
    raw = ENV_PATH.read_text(encoding="utf-8")
    for key, value in updates.items():
        if re.search(rf"(?m)^{re.escape(key)}=", raw):
            raw = re.sub(rf"(?m)^{re.escape(key)}=.*$", f"{key}={value}", raw)
        else:
            if not raw.endswith("\n"):
                raw += "\n"
            raw += f"{key}={value}\n"
    for key in drop:
        raw = re.sub(rf"(?m)^{re.escape(key)}=.*\n?", "", raw)
    ENV_PATH.write_text(raw, encoding="utf-8")


def _quote_literal(text: str) -> str:
    """MySQL 字符串字面量转义（仅用于账号名/主机名，均已白名单校验）。"""
    return str(text).replace("\\", "\\\\").replace("'", "\\'")


_SAFE_IDENT = re.compile(r"^[A-Za-z0-9_.%:\-]{1,64}$")


def connect(host, port, user, password, database=None):
    import pymysql

    return pymysql.connect(
        host=host,
        port=int(port),
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        connect_timeout=8,
        autocommit=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只检查，不修改")
    args = ap.parse_args()

    if not ENV_PATH.is_file():
        print(f"[错误] 找不到 {ENV_PATH}")
        return 2

    env = read_env()
    host = env.get("MYSQL_HOST", "127.0.0.1")
    port = env.get("MYSQL_PORT", "3306")
    user = env.get("MYSQL_USER", "root")
    old_pwd = env.get("MYSQL_PASSWORD", "")
    new_pwd = env.get("MYSQL_PASSWORD_NEW", "").strip()
    dbname = env.get("MYSQL_DATABASE", "")

    print(f"目标：{user}@{host}:{port}  库 {dbname or '(未指定)'}")
    if not new_pwd:
        print("[提示] .env 里没有 MYSQL_PASSWORD_NEW，说明密码已经迁移过了。")
        new_pwd = old_pwd

    # 1) 探测当前可用密码（旧密码优先，其次新密码）
    conn = None
    used_pwd = None
    for candidate in (old_pwd, new_pwd):
        if candidate is None:
            continue
        try:
            conn = connect(host, port, user, candidate, dbname or None)
            used_pwd = candidate
            break
        except Exception as e:  # noqa: BLE001
            print(f"[连接失败] 密码长度 {len(candidate or '')}：{e}")
    if conn is None:
        print("\n[错误] 用旧密码和新密码都连不上 MySQL。请先确认服务已启动：")
        print("       管理员 PowerShell 执行：  net start MySQL80")
        return 3

    if old_pwd == new_pwd:
        print("[跳过] 新旧密码一致，仅做连通性校验。")
    else:
        with conn.cursor() as cur:
            cur.execute("SELECT user, host FROM mysql.user WHERE user = %s", (user,))
            accounts = cur.fetchall()
        print(f"发现 {len(accounts)} 个 {user} 账户：{accounts}")

        if args.dry_run:
            print("[dry-run] 将执行 ALTER USER，但不会真正修改。")
        else:
            with conn.cursor() as cur:
                for acct_user, acct_host in accounts:
                    if not (_SAFE_IDENT.match(acct_user) and _SAFE_IDENT.match(acct_host)):
                        print(f"[跳过] 账号名含特殊字符，未自动修改：{acct_user}@{acct_host}")
                        continue
                    sql = (
                        f"ALTER USER '{_quote_literal(acct_user)}'@"
                        f"'{_quote_literal(acct_host)}' IDENTIFIED BY "
                        f"'{_quote_literal(new_pwd)}'"
                    )
                    cur.execute(sql)
                    print(f"[已改] {acct_user}@{acct_host}")
            print("[完成] 密码已更新")

    conn.close()

    # 2) 用新密码复核
    try:
        c2 = connect(host, port, user, new_pwd, dbname or None)
        with c2.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (dbname,))
            tables = cur.fetchone()[0]
            cur.execute("SELECT VERSION()")
            ver = cur.fetchone()[0]
        c2.close()
        print(f"[校验] 新密码连接成功：MySQL {ver}，{dbname} 共 {tables} 张表")
    except Exception as e:  # noqa: BLE001
        print(f"[错误] 新密码连接失败：{e}")
        return 4

    # 3) 回写 .env
    if not args.dry_run and old_pwd != new_pwd:
        write_env({"MYSQL_PASSWORD": new_pwd}, drop=["MYSQL_PASSWORD_NEW"])
        print("[完成] .env 已更新：MYSQL_PASSWORD 已替换为新密码，临时键已删除")
    elif args.dry_run:
        print("[dry-run] 未修改 .env")

    print("\n下一步：重启应用使新密码生效（启动.bat）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
