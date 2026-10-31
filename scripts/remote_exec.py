# -*- coding: utf-8 -*-
"""在服务器上执行一条 shell 命令并把输出打回本地。

为什么单独做这个脚本：排查线上问题时每次都临时写 `.tmp_ssh.py`，
写完就删、下次再写，既浪费时间也容易把引号层数写错（多层 `python -c "..."` 里
嵌括号会让 bash 报语法错误）。这里固定成一条通道：

    python scripts/remote_exec.py "tail -20 logs/hub.log"
    python scripts/remote_exec.py --cwd /data/zoujin/deng/zebrafish_runtime "ls logs"

连接信息沿用 `scripts/push_backend.py` 的约定：优先环境变量，其次项目根 `.env`
里的 `ZF_HOST / ZF_PORT / ZF_USER / ZF_PASS / ZF_BASE`。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            if k.strip() == name:
                return val.strip().strip('"').strip("'")
    return default


def connect() -> paramiko.SSHClient:
    host = env("ZF_HOST")
    if not host:
        raise SystemExit("[FAIL] .env 缺少 ZF_HOST")
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(
        host,
        port=int(env("ZF_PORT", "22")),
        username=env("ZF_USER"),
        password=env("ZF_PASS"),
        timeout=25,
    )
    return c


def run(cmd: str, timeout: int = 120) -> tuple[int, str]:
    c = connect()
    try:
        _i, o, e = c.exec_command(cmd, timeout=timeout)
        out = (o.read() + e.read()).decode("utf-8", "replace")
        rc = o.channel.recv_exit_status()
        return rc, out
    finally:
        c.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="+", help="要执行的命令（多段会以空格拼接）")
    ap.add_argument("--cwd", default="", help="先 cd 到该目录（默认 $ZF_BASE）")
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    cmd = " ".join(args.command)
    cwd = args.cwd or env("ZF_BASE", "/data/zoujin/deng/zebrafish_gene_qa")
    if cwd:
        cmd = f"cd {cwd} && {cmd}"
    rc, out = run(cmd, timeout=args.timeout)
    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
