# -*- coding: utf-8 -*-
"""巡检状态快查（本地跑）：读服务器上 zf_page_selfcheck 的轮次与最近几轮结果。

用法： .venv\\Scripts\\python.exe -X utf8 scripts\\check_pages_status.py
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
PUBLIC = "https://million-paul-input-principles.trycloudflare.com"


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

    py = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"
    print("守护实例:", run("pgrep -cf 'zf_page_selfcheck[.]py' || echo 0", 40))
    print("轮次数:", run("wc -l < " + RT + "/logs/zf_page_selfcheck_run.log", 40))
    print("最近几轮（原始记录，截断显示）:")
    print(run("tail -4 " + RT + "/logs/zf_page_selfcheck_run.log | cut -c1-230", 40))
    print("公网:", run("curl -s -m 15 -o /dev/null -w 'health=%{http_code} ' " + PUBLIC + "/api/health ; "
                      "curl -s -m 15 -o /dev/null -w 'index=%{http_code}' " + PUBLIC + "/", 60))
    print("uvicorn 运行时长:", run("ps -o etime= -p $(pgrep -f 'uvicorn api[.]api_server' | head -1)", 40))
    cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
