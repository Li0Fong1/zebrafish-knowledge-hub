# -*- coding: utf-8 -*-
"""看一眼服务端某个 JSON 产物的结构（只读，不改远端）。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts\\peek_remote_json.py /data/zoujin/deng/zebrafish_runtime/genome_info.json
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paramiko  # noqa: E402

try:  # 凭据只从 .env / 环境变量读，源码内不得硬编码（见 RELEASE_SECURITY_AUDIT）
    from _zf_creds import HOST, PORT, USER, require_password
except ImportError:  # 以包形式导入时（python -m scripts.xxx）
    from scripts._zf_creds import HOST, PORT, USER, require_password


def main() -> int:
    remote = sys.argv[1] if len(sys.argv) > 1 else "/data/zoujin/deng/zebrafish_runtime/genome_info.json"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 1600

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, port=PORT, username=USER,
                password=require_password(),
                timeout=45, banner_timeout=60, auth_timeout=60,
                allow_agent=False, look_for_keys=False)
    cli.get_transport().set_keepalive(15)

    def run(cmd: str, timeout: int = 60) -> str:
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
        return out.decode("utf-8", "replace")

    print("=== ls ===")
    print(run("ls -l " + remote + " 2>&1 | cut -c1-140").strip())
    print("\n=== head %d ===" % limit)
    print(run("head -c %d %s" % (limit, remote)).strip())
    print("\n=== 顶层键 ===")
    print(run("python3 -c \"import json,sys;d=json.load(open('%s'));print(type(d).__name__, "
              "list(d.keys())[:20] if isinstance(d,dict) else len(d))\" 2>&1 | tail -3" % remote).strip())
    cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
