# -*- coding: utf-8 -*-
"""看清 hub_guard.sh 的触发方式与是否支持"手动停止"开关。

用法： .venv\\Scripts\\python.exe -X utf8 scripts\\guard_probe.py
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

    def run(cmd: str, timeout: int = 90) -> str:
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

    print("=== 1) hub_guard.sh 里出现的关键字（找开关/哨兵文件） ===")
    print(run("grep -nE 'stop|pause|enable|disable|flag|\\.json|\\.lock|sentinel|quiet' "
              + RT + "/hub_guard.sh | head -40 | cut -c1-200", 40) or "(无)")

    print("\n=== 2) hub_guard.sh 前 60 行 ===")
    print(run("head -60 " + RT + "/hub_guard.sh | cut -c1-200", 40) or "(读不到)")

    print("\n=== 3) 谁在拉起它（cron / systemd / 外层 wrapper） ===")
    print("[crontab -l]", run("crontab -l 2>/dev/null | head -20 || echo '(空)'", 40) or "(空)")
    print("[/etc/cron.d]", run("ls -l /etc/cron.d 2>/dev/null | head -20 || echo '(不可读)'", 40))
    print("[cron.d 中含 guard]", run("grep -rl 'hub_guard\\|run-deng-limited' /etc/cron.d /etc/crontab 2>/dev/null || echo '(不可读/无)'", 40))
    print("[systemd timers]", run("systemctl list-timers --all --no-pager 2>/dev/null | grep -iE 'hub|deng|zebra' || echo '(无)'", 40))
    print("[systemd 单元含 guard]", run("systemctl list-units --all --no-pager --type=service 2>/dev/null | grep -iE 'hub|deng|zebra' || echo '(无)'", 40))
    print("[wrapper 内容]", run("head -20 /usr/local/sbin/run-deng-limited 2>/dev/null || echo '(不可读)'", 40))

    print("\n=== 4) runtime 下的哨兵/状态文件 ===")
    print(run("ls -lt " + RT + "/*.json " + RT + "/*.flag " + RT + "/*.stop " + RT + "/*.lock 2>/dev/null | head -20", 40) or "(无)")

    print("\n=== 5) sudo 是否可用（免密） ===")
    print(run("sudo -n true 2>&1 && echo 'SUDO_OK' || echo 'SUDO_NO'", 40))

    print("\n=== 6) 当前 uvicorn 是被谁拉起的 ===")
    print(run("ps -eo pid=,ppid=,etime=,cmd= | grep -E 'uvicorn api[.]api_server' | grep -v grep | cut -c1-160", 40) or "(未运行)")
    cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
