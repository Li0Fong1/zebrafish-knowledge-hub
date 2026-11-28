# -*- coding: utf-8 -*-
"""最小服务器连通性测试：只建 SSH 连接 + 执行 hostname/date，不碰任何数据。

结果写 docs/服务器连接测试.md。
用法：python scripts/probe_server_conn.py
"""
from __future__ import annotations

import datetime
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

HOST = ""
PORT = 22
try:
    import remote_exec as rexc
    HOST = rexc.env("ZF_HOST", "")
    PORT = int(rexc.env("ZF_PORT", "22"))
except Exception as exc:  # noqa: BLE001
    HOST = "?import-fail:%s" % type(exc).__name__

lines = [
    "# 服务器连接测试", "",
    "- 时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "- 目标：%s:%s" % (HOST, PORT), "",
]

# 1) DNS
t0 = time.time()
try:
    ip = socket.gethostbyname(HOST)
    lines.append("- DNS 解析：**成功** → %s（%.2fs）" % (ip, time.time() - t0))
except Exception as exc:  # noqa: BLE001
    lines.append("- DNS 解析：**失败** %s %s（%.2fs）" % (type(exc).__name__, exc, time.time() - t0))

# 2) TCP 端口
t0 = time.time()
try:
    s = socket.create_connection((HOST, PORT), 8)
    s.close()
    lines.append("- TCP %d：**通**（%.2fs）" % (PORT, time.time() - t0))
except Exception as exc:  # noqa: BLE001
    lines.append("- TCP %d：**不通** %s %s（%.2fs）" % (PORT, type(exc).__name__, exc, time.time() - t0))

# 3) SSH 认证 + 一条命令
t0 = time.time()
try:
    c = rexc.connect()
    rc, out = rexc.run("hostname; date -Is; uptime | head -1", timeout=30)
    c.close()
    lines += [
        "- SSH 连接与命令：**成功**（%.2fs）" % (time.time() - t0),
        "",
        "```",
        out.strip(),
        "```",
        "",
        "**结论：服务器可连接。**",
    ]
except Exception as exc:  # noqa: BLE001
    lines += [
        "- SSH 连接与命令：**失败** %s：%s（%.2fs）" % (type(exc).__name__, exc, time.time() - t0),
        "",
        "**结论：服务器当前连不上，见上方失败环节。**",
    ]

rp = ROOT / "docs" / "服务器连接测试.md"
rp.parent.mkdir(exist_ok=True)
rp.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines[-6:]))
print("报告：%s" % rp)
