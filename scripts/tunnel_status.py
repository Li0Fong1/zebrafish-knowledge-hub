# -*- coding: utf-8 -*-
"""排查公网 530：源站是否正常 + cloudflared 隧道状态与日志。

用法： .venv\\Scripts\\python.exe -X utf8 scripts/tunnel_status.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paramiko  # noqa: E402

RT = "/data/zoujin/deng/zebrafish_runtime"
try:  # 凭据只从 .env / 环境变量读，源码内不得硬编码（见 RELEASE_SECURITY_AUDIT）
    from _zf_creds import HOST, PORT, USER, require_password
except ImportError:  # 以包形式导入时（python -m scripts.xxx）
    from scripts._zf_creds import HOST, PORT, USER, require_password


def main() -> int:
    pwd = require_password()
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, port=PORT, username=USER, password=pwd, timeout=45,
                banner_timeout=60, auth_timeout=60, allow_agent=False, look_for_keys=False)
    cli.get_transport().set_keepalive(15)

    def run(cmd: str, timeout: int = 120) -> str:
        ch = cli.get_transport().open_session()
        ch.settimeout(timeout)
        ch.exec_command(cmd)
        out = b""
        while True:
            try:
                buf = ch.recv(65536)
            except Exception:
                break
            if not buf:
                break
            out += buf
        ch.close()
        return out.decode("utf-8", "replace").strip()

    print("=== 1) 源站监听端口 ===")
    print(run("ss -ltnp 2>/dev/null | grep -E 'uvicorn|python|:80|:800|:90' | head -10", 40))

    print("\n=== 2) 从服务器本机探测源站 ===")
    print(run("for p in 8000 8001 8080 9000; do "
              "code=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:$p/api/health); "
              "echo \"127.0.0.1:$p -> $code\"; done", 60))

    print("\n=== 3) cloudflared 进程 ===")
    print(run("pgrep -af 'cloudflared' | cut -c1-200", 40) or "(无)")

    print("\n=== 4) cloudflared 日志位置与末尾 ===")
    for lg in ["cloudflared.log", "logs/cloudflared.log", "logs/cf.log", "cloudflared.out"]:
        n = run("test -f " + RT + "/" + lg + " && wc -l < " + RT + "/" + lg + " || echo 0", 30)
        if n not in ("0", ""):
            print(">> %s (%s 行)" % (RT + "/" + lg, n))
            print(run("tail -25 " + RT + "/" + lg + " | cut -c1-220", 40))
    print("[ls -t 最近改动的 log]")
    print(run("ls -t " + RT + "/logs/*.log 2>/dev/null | head -6", 40))

    print("\n=== 5) 日志里出现过的隧道地址 ===")
    print(run("grep -ho 'https://[a-z0-9-]*\\.trycloudflare\\.com' " + RT + "/*.log " + RT + "/logs/*.log 2>/dev/null | tail -3", 40) or "(未找到)")

    print("\n=== 6) 公网复测（3 次） ===")
    print(run("for i in 1 2 3; do "
              "curl -s -m 12 -o /dev/null -w 'health=%{http_code} ' "
              "https://million-paul-input-principles.trycloudflare.com/api/health; sleep 2; done", 90))

    print("\n=== 7) hub_supervise 最近动作 ===")
    print(run("grep -h 'cloudflared' " + RT + "/logs/hub_supervise*.log 2>/dev/null | tail -5 | cut -c1-200", 40) or "(无)")
    cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
