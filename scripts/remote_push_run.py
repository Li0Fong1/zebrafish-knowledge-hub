# -*- coding: utf-8 -*-
"""把本地脚本传到服务器并执行，用完即删。

为什么需要它：本轮要给 FigS2 补品系装配的坐标，而 12 GB 的 over.chain 文件
全在服务器上，本地没有副本；又不能把上百行 Python 塞进 `remote_exec.py "..."`
的多层引号里（必炸）。所以固定成「上传 → 运行 → 取回 stdout → 删远端临时文件」。

用法：
    python scripts/remote_push_run.py scripts/liftover_strain_coords.py
    python scripts/remote_push_run.py scripts/liftover_strain_coords.py --keep

连接信息与 `remote_exec.py` 同源：环境变量优先，其次项目根 `.env`。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
REMOTE_TMP = "/tmp/_wb_push_run.py"


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script", help="要上传执行的本地 .py")
    ap.add_argument("--cwd", default="", help="远端工作目录，默认 $ZF_BASE")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--keep", action="store_true", help="保留远端临时文件")
    ap.add_argument("-a", "--arg", action="append", default=[], help="传给脚本的参数")
    args = ap.parse_args()

    host = env("ZF_HOST")
    if not host:
        raise SystemExit("[FAIL] .env 缺少 ZF_HOST")
    src = Path(args.script)
    if not src.is_file():
        raise SystemExit(f"[FAIL] 找不到 {src}")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, port=int(env("ZF_PORT", "22")), username=env("ZF_USER"),
              password=env("ZF_PASS"), timeout=25)
    try:
        sftp = c.open_sftp()
        with sftp.file(REMOTE_TMP, "wb") as fh:
            fh.write(src.read_bytes())
        sftp.close()

        cwd = args.cwd or env("ZF_BASE", "/data/zoujin/deng/zebrafish_gene_qa")
        argstr = " ".join(args.arg)
        cmd = f"cd {cwd} && python3 {REMOTE_TMP} {argstr}"
        _i, o, e = c.exec_command(cmd, timeout=args.timeout)
        out = (o.read() + e.read()).decode("utf-8", "replace")
        rc = o.channel.recv_exit_status()

        if not args.keep:
            s2 = c.open_sftp()
            try:
                s2.remove(REMOTE_TMP)
            except OSError:
                pass
            s2.close()

        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
        return 0 if rc == 0 else 1
    finally:
        c.close()


if __name__ == "__main__":
    raise SystemExit(main())
