# -*- coding: utf-8 -*-
"""本项目在服务器上的进程清点 / 关停 / 恢复。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts\\project_procs.py list    # 清点
    .venv\\Scripts\\python.exe -X utf8 scripts\\project_procs.py stop    # 关停（含禁止自动复活）
    .venv\\Scripts\\python.exe -X utf8 scripts\\project_procs.py resume  # 恢复（允许自动复活）

关停原理：
  1) 建哨兵 $RT/HUB_STOPPED —— hub_guard.sh（crontab 每分钟）看到它就跳过，不再复活
     Hub 与公网隧道（这是 hub_guard.sh 自带的开关，不是 hack）。
  2) 先停"会把它拉回来"的守护，再停 uvicorn / cloudflared。
  3) 跨 2 个 cron 周期复查，确认没有复活。
不触碰 mysqld（数据库留着，避免数据风险）。
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
SENTINEL = RT + "/HUB_STOPPED"

SCAN = r"""ps -eo pid=,etime=,cmd= --sort=pid | grep -E \
'zebrafish|zf_page_selfcheck|hub_guard|hub_supervise|hub_watchdog|overnight|uvicorn|cloudflared' \
| grep -v -E 'grep -E|ps -eo|project_procs' | cut -c1-170"""

# 关停目标（用 [x] 括号法避免误杀自己这条命令）
KILL_TARGETS = [
    ("hub_guard.sh", "hub_guar[d]"),
    ("hub_supervise.sh", "hub_supervise[.]sh"),
    ("hub_watchdog.sh", "hub_watchdo[g]"),
    ("zf_page_selfcheck.py", "zf_page_selfcheck[.]py"),
    ("overnight_watchdog.py", "overnight_watchdog[.]py"),
    ("overnight_zh_backfill_watchdog.py", "overnight_zh_backfill_watchdog[.]py"),
    ("overnight_pubmed_yes_fetch_guard.py", "overnight_pubmed_yes_fetch_guard[.]py"),
    ("uvicorn", "uvicorn api[.]api_server"),
    ("cloudflared", "cloudflare[d]"),
]


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "list").lower()
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

    def counts() -> str:
        parts = []
        for name, pat in KILL_TARGETS:
            n = run("pgrep -cf '%s' || echo 0" % pat, 40).split("\n")[0]
            parts.append("%s=%s" % (name, n))
        return " ".join(parts)

    def status() -> None:
        print("  进程:", counts())
        sentinel = run("test -f " + SENTINEL + " && echo Y || echo N", 30)
        print("  哨兵 HUB_STOPPED:", "存在（禁止自动复活）" if sentinel == "Y" else "不存在")
        print("  8080 监听:", run("ss -ltn 2>/dev/null | grep -c ':8080' || echo 0", 40),
              " mysqld:", run("pgrep -cf mysqld || echo 0", 40), "（保留）")

    print("=== 本项目相关进程 ===")
    print(run(SCAN, 60) or "(无)")

    if mode == "list":
        print("\n=== 关键状态 ===")
        status()
        print("\n(仅清点。关停加 stop，恢复加 resume)")
        cli.close()
        return 0

    if mode == "stop":
        print("\n=== 1) 建哨兵，禁止 hub_guard 复活 ===")
        run("touch " + SENTINEL + "; true", 40)
        print("  " + run("ls -l " + SENTINEL + " | cut -c1-120", 40))

        print("\n=== 2) 关停 ===")
        for label, pat in KILL_TARGETS:
            before = run("pgrep -cf '%s' || echo 0" % pat, 40).split("\n")[0]
            run("pkill -TERM -f '%s' 2>/dev/null; true" % pat, 40)
            time.sleep(1.2)
            run("pkill -9 -f '%s' 2>/dev/null; true" % pat, 40)
            time.sleep(0.8)
            after = run("pgrep -cf '%s' || echo 0" % pat, 40).split("\n")[0]
            print("  %-38s %s -> %s   %s" % (label, before, after, "OK" if after == "0" else "仍存活"))

        print("\n=== 3) 跨 2 个 cron 周期复查（每 30 秒） ===")
        for i in (1, 2, 3):
            time.sleep(30)
            print("  第 %d 次:" % i)
            status()

        print("\n=== 4) hub_guard 最新记录 ===")
        print("  " + run("cat " + RT + "/hub_guard_last.json 2>/dev/null || echo '(无)'", 40))

        print("\n=== 5) 残余进程 ===")
        print(run(SCAN, 60) or "(无残余)")
        cli.close()
        return 0

    if mode == "resume":
        print("\n=== 1) 删哨兵 ===")
        run("rm -f " + SENTINEL + "; true", 40)
        print("  哨兵:", "仍存在" if run("test -f " + SENTINEL + " && echo Y || echo N", 30) == "Y" else "已删除")

        print("\n=== 2) 直接拉起 hub_supervise（不等 cron） ===")
        print(run("cd " + RT + " && setsid nohup ./hub_supervise.sh >> logs/hub_supervise.log 2>&1 </dev/null & sleep 3; echo started", 60))

        print("\n=== 3) 等 75 秒后复查 ===")
        for i in (1, 2, 3):
            time.sleep(25)
            print("  第 %d 次:" % i)
            status()
        print("\n  源站健康:", run("curl -s -m 15 -o /dev/null -w '%{http_code}' "
                                   "http://127.0.0.1:8080/api/health", 40))
        cli.close()
        return 0

    print("未知模式:", mode)
    cli.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
