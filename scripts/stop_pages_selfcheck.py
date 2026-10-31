# -*- coding: utf-8 -*-
"""关闭服务器上的页面巡检守护（zf_page_selfcheck.py）。

用途：用户不需要巡检时，彻底停掉它，并检查是否有 cron / supervisor 会把它拉回来。

用法： .venv\\Scripts\\python.exe -X utf8 scripts\\stop_pages_selfcheck.py
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

    print("=== 1) 停止前的巡检进程 ===")
    print(run("pgrep -af 'zf_page_selfcheck[.]py' || echo '(无)'", 40) or "(无)")

    print("\n=== 2) 发送 TERM ===")
    run("pkill -TERM -f 'zf_page_selfcheck[.]py' ; echo sent", 40)
    run("sleep 3", 20)

    left = run("pgrep -cf 'zf_page_selfcheck[.]py' || echo 0", 40)
    if left not in ("0", ""):
        print("TERM 后仍存活 %s 个，改用 KILL" % left)
        run("pkill -9 -f 'zf_page_selfcheck[.]py' ; echo killed", 40)
        run("sleep 2", 20)
        left = run("pgrep -cf 'zf_page_selfcheck[.]py' || echo 0", 40)

    print("剩余巡检进程:", left)

    print("\n=== 3) 是否有东西会把它拉回来 ===")
    print("[crontab]", run("crontab -l 2>/dev/null | grep -i 'selfcheck' || echo '(无)'", 40) or "(无)")
    print("[systemd]", run("systemctl --user list-units --no-pager 2>/dev/null | grep -i 'selfcheck' || echo '(无)'", 40) or "(无)")
    print("[hub_supervise 是否含 selfcheck]",
          run("grep -c 'selfcheck' " + RT + "/hub_supervise.sh 2>/dev/null || echo 0", 40))

    print("\n=== 4) 保留仍在跑的服务（未动） ===")
    print("uvicorn:", run("ps -o etime= -p $(pgrep -f 'uvicorn api[.]api_server' | head -1) 2>/dev/null || echo '(未运行)'", 40))
    print("cloudflared:", run("pgrep -cf 'cloudflared' || echo 0", 40))
    print("公网:", run("curl -s -m 15 -o /dev/null -w 'health=%{http_code}' "
                      "https://million-paul-input-principles.trycloudflare.com/api/health", 60))

    print("\n=== 5) 最终确认 ===")
    print("巡检实例:", run("pgrep -cf 'zf_page_selfcheck[.]py' || echo 0", 40))
    cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
