# -*- coding: utf-8 -*-
"""从服务器把后端文件拉回本地（`push_backend.py` 的反向操作）。

为什么需要：本地仓库已经多次落后于线上 —— `database/coord_liftover.py` 在服务器上
有 453 行、本地完全没有，而能力文案却写着「不做坐标 liftover」；`genome_toolkit.py`
也曾是线上比本地新。只往一个方向推，迟早会用旧文件覆盖掉线上新功能。

用法：
    python scripts/pull_backend.py database/coord_liftover.py
    python scripts/pull_backend.py --list                    # 列出线上有、本地没有的 py
    python scripts/pull_backend.py --diff                    # 列出两端内容不同的 py
    python scripts/pull_backend.py --pull-diff               # 把不同的都拉回来（先备份本地）

行为：
    · `--list/--diff` 只读，不改本地文件
    · 拉取前把本地文件改名成 `<file>.local.bak.<时间戳>`
    · 拉完比对 md5，不一致报错
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
DIRS = ["agent", "api", "core", "llm", "rag", "database", "scripts"]


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


def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def norm_md5(b: bytes) -> str:
    """去掉 CR 再算 md5：本地 Git 检出可能是 CRLF，线上永远是 LF。"""
    return hashlib.md5(b.replace(b"\r", b"")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", help="要拉回的相对路径")
    ap.add_argument("--list", action="store_true", help="只列出线上有、本地没有的 .py")
    ap.add_argument("--diff", action="store_true", help="列出两端内容不同的 .py")
    ap.add_argument("--pull-diff", action="store_true", help="把所有不同的 .py 拉回本地")
    args = ap.parse_args()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        env("ZF_HOST"),
        port=int(env("ZF_PORT", "22")),
        username=env("ZF_USER"),
        password=env("ZF_PASS"),
        timeout=25,
    )
    base = env("ZF_BASE", "/data/zoujin/deng/zebrafish_gene_qa").rstrip("/")

    def run(cmd: str, timeout: int = 120) -> str:
        _i, o, e = client.exec_command(cmd, timeout=timeout)
        return (o.read() + e.read()).decode("utf-8", "replace").strip()

    sftp = client.open_sftp()

    def remote_list() -> list[str]:
        cmd = "cd " + base + " && { " + "".join(
            f"find {d} -name '*.py' -not -path '*__pycache__*' -not -name '*.bak*'; " for d in DIRS
        ) + "find . -maxdepth 1 -name '*.py'; } 2>/dev/null | sort"
        return [x for x in run(cmd).splitlines() if x.strip()]

    def pull_one(rel: str, backup: bool = True) -> bool:
        remote = f"{base}/{rel}"
        local = (ROOT / rel).resolve()
        try:
            with sftp.file(remote, "rb") as fh:
                data = fh.read()
        except IOError:
            print(f"[FAIL] 服务器上没有：{rel}")
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        if backup and local.is_file():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            local.rename(local.with_suffix(local.suffix + f".local.bak.{stamp}"))
        local.write_bytes(data)
        got = md5_bytes(local.read_bytes())
        if got != md5_bytes(data):
            print(f"[FAIL] md5 不一致 {rel}")
            return False
        print(f"[PULL] {rel}  ({len(data)} bytes, md5={got[:10]})")
        return True

    if args.list or args.diff:
        missing, changed = [], []
        for rel in remote_list():
            local = ROOT / rel
            if not local.is_file():
                missing.append(rel)
                continue
            try:
                with sftp.file(f"{base}/{rel}", "rb") as fh:
                    rdata = fh.read()
            except IOError:
                continue
            if norm_md5(rdata) != norm_md5(local.read_bytes()):
                changed.append(rel)
        if args.list:
            print(f"线上有、本地没有（{len(missing)}）：")
            for m in missing:
                print("  " + m)
        if args.diff:
            print(f"两端内容不同（{len(changed)}）：")
            for c in changed:
                print("  " + c)
        if args.pull_diff and changed:
            print("\n开始拉回：")
            for c in changed:
                pull_one(c)
        sftp.close()
        client.close()
        return 0

    targets = args.files
    if not targets:
        print("[FAIL] 没给文件；用 --list / --diff 先看差异")
        sftp.close()
        client.close()
        return 2
    ok = sum(1 for t in targets if pull_one(t))
    sftp.close()
    client.close()
    print(f"\n汇总：成功 {ok} / {len(targets)}")
    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
