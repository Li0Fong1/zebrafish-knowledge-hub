# -*- coding: utf-8 -*-
"""比对本地改动文件与服务器版本（只读，不写入任何文件）。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts/diff_remote.py
    .venv\\Scripts\\python.exe -X utf8 scripts/diff_remote.py api/api_server.py web/js/app.js
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent

# 本次并发闸 + 英文泄漏修复涉及的改动文件
DEFAULT_FILES = [
    "agent/chat_gate.py",          # 新增
    "agent/agent_scheduler.py",
    "api/api_server.py",
    "api/web_security.py",
    "core/common_utils.py",
    "web/index.html",
    "web/js/app.js",
    "web/js/i18n.js",
    "scripts/smoke_chat_gate.py",  # 新增（可选）
]


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


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def main() -> int:
    files = [f.replace("\\", "/").lstrip("/") for f in (sys.argv[1:] or DEFAULT_FILES)]
    host = env("ZF_HOST")
    if not host:
        print("[FAIL] 缺少 ZF_HOST")
        return 2
    base = env("ZF_BASE", "/data/zoujin/deng/zebrafish_gene_qa").rstrip("/")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, int(env("ZF_PORT", "22")), env("ZF_USER"), env("ZF_PASS"),
              timeout=60, banner_timeout=90, auth_timeout=120,
              allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()

    print(f"{'状态':<8} {'文件':<34} {'本地B':>8} {'远端B':>8}  远端修改时间")
    print("-" * 88)
    todo, same, missing = [], [], []
    for rel in files:
        local = ROOT / rel
        remote = f"{base}/{rel}"
        if not local.is_file():
            print(f"{'本地缺':<8} {rel}")
            missing.append(rel)
            continue
        new = local.read_bytes()
        try:
            with sftp.open(remote, "rb") as f:
                old = f.read()
            st = sftp.stat(remote)
            import time as _t
            mtime = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(st.st_mtime))
            size = st.st_size
        except Exception:
            old, mtime, size = None, "-", 0
        if old is None:
            print(f"{'远端无':<8} {rel:<34} {len(new):>8} {'-':>8}  （新文件）")
            todo.append(rel)
            continue
        flag = "SAME" if md5(old) == md5(new) else "DIFF"
        print(f"{flag:<8} {rel:<34} {len(new):>8} {size:>8}  {mtime}")
        (same if flag == "SAME" else todo).append(rel)
    sftp.close()
    c.close()
    print("-" * 88)
    print(f"待推送 {len(todo)}，一致 {len(same)}，本地缺 {len(missing)}")
    if todo:
        print("待推送：" + ", ".join(todo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
