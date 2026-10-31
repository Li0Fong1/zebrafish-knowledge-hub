# -*- coding: utf-8 -*-
"""验证反馈接口：重启服务 → 健康检查 → 正常提交写入 → 短内容被拒。

用法： .venv\\Scripts\\python.exe -X utf8 scripts\\verify_feedback.py
"""
from __future__ import annotations

import os
import sys
import time

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

    if os.environ.get("DO_RESTART") == "1":
        print("重启 uvicorn:", run("kill -9 $(pgrep -f 'uvicorn api[.]api_server') && echo killed"))
        time.sleep(28)
    else:
        print("未重启服务（需要重启请设 DO_RESTART=1）")
    ok = False
    for _ in range(6):
        h = run("curl -s -m 15 http://127.0.0.1:8080/api/health | head -c 60", 40)
        if '"status":"ok"' in h.replace(" ", ""):
            ok = True
            break
        time.sleep(8)
    print("健康:", "ok" if ok else "FAILED")

    body = ('{"message":"自检：核型图与反馈闭环上线验证","contact":"selftest","page":"#/genomes"}')
    print("正常提交:", run(
        "curl -s -m 20 -X POST http://127.0.0.1:8080/api/hub/feedback "
        "-H 'Content-Type: application/json' -d '" + body + "' | head -c 220", 60))
    print("落盘记录:", run("tail -1 " + RT + "/feedback/feedback.jsonl", 40))
    print("短内容 HTTP（应 400）:", run(
        "curl -s -m 15 -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8080/api/hub/feedback "
        "-H 'Content-Type: application/json' -d '{\"message\":\"ab\"}'", 40))
    cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
