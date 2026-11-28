# -*- coding: utf-8 -*-
"""远程 SQL 快速通道：python scripts/remote_sql.py "SELECT 1"

固定走 3307 的 zebrafish 库，避免每次手写引号层数。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASS = os.environ.get("ZF_PASS") or ""
DB = os.environ.get("ZF_DB") or "zebrafish_db"
MYSQL = (
    f"mysql -h127.0.0.1 -P3307 -uzebrafish -pzebrafish_remote_2026 {DB} -N -e "
)


def main() -> None:
    sql = " ".join(sys.argv[1:])
    if not sql:
        raise SystemExit("usage: remote_sql.py \"SELECT ...\"")
    # 传给远端 bash 的字符串，SQL 用双引号包住；SQL 内部的单引号保持原样
    escaped = sql.replace('"', '\\"')
    cmd = f'{MYSQL}"{escaped}" 2>&1 | grep -v "insecure"'
    env = dict(os.environ, ZF_PASS=PASS)
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "remote_exec.py"), cmd],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
    )
    print((r.stdout or "").rstrip())
    if r.returncode != 0:
        print("[stderr]", (r.stderr or "").rstrip()[-500:], file=sys.stderr)
        sys.exit(r.returncode)


if __name__ == "__main__":
    main()
