# -*- coding: utf-8 -*-
"""项目全维度运行时体检（只读，不修改任何线上数据）。

采集：系统资源 / 进程 / 守护 / 网络与公网缓存 / MySQL / SQLite /
      日志错误 / 前端静态资源 / 磁盘占用。
结果写入 docs/运行时体检数据.md，供后续分析。

所有输出经过脱敏：不打印 .env、不打印口令，命中凭据特征一律抹掉。
用法：python scripts/full_audit.py
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import remote_exec as rexc  # noqa: E402

BASE = rexc.env("ZF_BASE", "/data/zoujin/deng/zebrafish_gene_qa")
RUNTIME = rexc.env("ZF_RUNTIME", "/data/zoujin/deng/zebrafish_runtime")
PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"

SECRET_LINE = re.compile(
    r"([A-Za-z][A-Za-z0-9]*_remote_[0-9]{4}|deng[0-9]_[0-9]{4,}|(?<![0-9])123456(?![0-9]))"
)

SERVER_PROBE = r'''
import os, sqlite3, sys

BASE = "/data/zoujin/deng/zebrafish_gene_qa"
RUNTIME = "/data/zoujin/deng/zebrafish_runtime"
sys.path[:0] = [BASE, BASE + "/core", BASE + "/database", BASE + "/scripts"]
os.chdir(BASE)

print("=== MySQL ===")
try:
    from mysql_client import get_mysql_client
    c = get_mysql_client()
    qs = [
        ("tables", "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='zebrafish_db'"),
        ("threads_connected", "SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='THREADS_CONNECTED'"),
        ("max_used_connections", "SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='MAX_USED_CONNECTIONS'"),
        ("max_connections", "SELECT VARIABLE_VALUE FROM performance_schema.global_variables WHERE VARIABLE_NAME='MAX_CONNECTIONS'"),
        ("slow_queries", "SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='SLOW_QUERIES'"),
        ("buffer_pool_MB", "SELECT ROUND(VARIABLE_VALUE/1048576) FROM performance_schema.global_variables WHERE VARIABLE_NAME='INNODB_BUFFER_POOL_SIZE'"),
        ("top_tables", "SELECT table_name, table_rows, ROUND(data_length/1048576) FROM information_schema.tables WHERE table_schema='zebrafish_db' ORDER BY data_length DESC LIMIT 8"),
        ("missing_fk_index", "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='zebrafish_db'"),
    ]
    for name, q in qs:
        try:
            r = c.execute_sql(q, max_rows=10)
            print("  %-22s %s" % (name, str(r)[:400]))
        except Exception as exc:
            print("  %-22s ERR %s" % (name, type(exc).__name__))
except Exception as exc:
    print("  MySQL unavailable:", type(exc).__name__, str(exc)[:120])

print()
print("=== SQLite 落盘库 ===")
p = RUNTIME + "/idmap_data.sqlite3"
try:
    print("  size_MB  %.1f" % (os.path.getsize(p) / 1048576.0))
    con = sqlite3.connect("file:%s?mode=ro" % p, uri=True, timeout=5)
    print("  journal_mode", con.execute("PRAGMA journal_mode").fetchone())
    for t in ("coords", "ens_release", "ens_current", "ens_gene_official", "meta"):
        try:
            n = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            print("  %-22s %d" % (t, n))
        except Exception:
            print("  %-22s MISSING" % t)
    try:
        print("  coords_done_builds", str(con.execute("SELECT value FROM meta WHERE key='coords_done_builds'").fetchone())[:300])
    except Exception:
        pass
    con.close()
except Exception as exc:
    print("  ERR", type(exc).__name__, str(exc)[:150])

print()
print("=== 前端静态资源引用一致性 ===")
import re as _re
try:
    html = open(BASE + "/web/index.html", encoding="utf-8", errors="replace").read()
    refs = sorted(set(_re.findall(r"app\.[a-f0-9]+\.(?:c[0-9]\.js|css)", html)))
    print("  index.html bytes", len(html.encode("utf-8")))
    print("  refs", refs)
    for f in refs:
        fp = BASE + "/web/dist/" + f
        print("   ", "OK  " if os.path.isfile(fp) else "MISS", f)
    print("  has_closing_html", "</html>" in html)
    print("  script_count", html.count("<script"))
    dist = os.listdir(BASE + "/web/dist")
    print("  dist_total_files", len(dist))
    print("  dist_unreferenced", len([x for x in dist if x not in refs]))
except Exception as exc:
    print("  ERR", type(exc).__name__, str(exc)[:150])

print("SERVER_PROBE_DONE")
'''

# (章节, 小标题, 命令, 超时秒)
TASKS = [
    ("系统资源", "主机与负载", """
echo "host=$(hostname) date=$(date -Is)"
echo "-- uptime --"; uptime
echo "-- cpu --"; nproc
echo "-- mem --"; free -h
echo "-- disk --"; df -h /data
echo "-- loadavg --"; cat /proc/loadavg
""", 40),
    ("系统资源", "关键进程", """
ps -eo pid,ppid,etime,rss,pcpu,args --sort=-rss | head -1
ps -eo pid,ppid,etime,rss,pcpu,args | grep -E 'uvicorn api[.]api_server:app|mysqld|cloudflared' | grep -v grep | cut -c1-220
echo "-- uvicorn 进程数 --"
pgrep -fc 'uvicorn api.api_server:app' || true
""", 40),
    ("系统架构", "守护与定时器", """
echo "-- user timers --"; systemctl --user list-timers --no-pager 2>&1 | head -15
echo "-- key units --"
for u in zf-mysql-guard.timer zebrafish-hub-watchdog.service zebrafish-hub-guard.timer; do
  printf '  %-40s active=%s enabled=%s\\n' "$u" "$(systemctl --user is-active $u 2>&1)" "$(systemctl --user is-enabled $u 2>&1)"
done
echo "-- linger --"; loginctl show-user $(id -un) 2>/dev/null | grep -i linger || true
""", 60),
    ("网络", "监听端口与健康检查", """
echo "-- listening --"; ss -lntp 2>/dev/null | grep -E ':3307|:8080' || true
echo "-- local health --"; curl -s -m 10 http://127.0.0.1:8080/api/health | head -c 500; echo
echo "-- local homepage headers --"; curl -s -m 10 -D - -o /dev/null http://127.0.0.1:8080/ | head -12
""", 60),
    ("网络", "公网与 CDN 缓存", """
echo "-- public homepage headers --"
curl -s -m 25 -D - -o /tmp/_audit_home.html https://zebrafish-hub.top/ | head -18
echo "-- public homepage integrity --"
wc -c /tmp/_audit_home.html 2>/dev/null || true
echo "  closing_html=$(grep -c '</html>' /tmp/_audit_home.html 2>/dev/null || echo 0)"
echo "  script_tags=$(grep -c '<script' /tmp/_audit_home.html 2>/dev/null || echo 0)"
echo "-- public health --"
curl -s -m 15 -o /dev/null -w 'code=%{http_code} time=%{time_total}s size=%{size_download}\\n' https://zebrafish-hub.top/api/health
""", 90),
    ("日志", "错误与慢查询", """
echo "-- log files --"; ls -lh %s/logs/ 2>/dev/null | tail -15
echo "-- hub.log recent errors --"
tail -300 %s/logs/hub.log 2>/dev/null | grep -iE 'traceback|exception|error|fatal' | tail -20 || true
echo "-- slow log --"
wc -l %s/logs/mysql-slow.log 2>/dev/null || true
ls -lh %s/logs/mysql-slow.log 2>/dev/null || true
""" % (RUNTIME, RUNTIME, RUNTIME, RUNTIME), 60),
    ("磁盘", "运行时目录占用", """
du -sh %s/* 2>/dev/null | sort -h | tail -15
echo "-- total --"; du -sh %s 2>/dev/null
""" % (RUNTIME, RUNTIME), 90),
]


def scrub(text: str) -> str:
    """抹掉任何可能的凭据字面量及 .env 赋值行。"""
    out = []
    for ln in text.splitlines():
        low = ln.lower()
        if any(k in low for k in ("pass=", "password=", "passwd=", "secret=", "token=", "api_key=", "-p")) \
                and ("=" in ln or "-p" in ln):
            # 保守：整行不输出，避免泄露
            out.append("  <已脱敏：凭据相关行>")
            continue
        out.append(SECRET_LINE.sub("<redacted>", ln))
    return "\n".join(out)


def main() -> int:
    lines = [
        "# 运行时体检数据（只读采集）",
        "",
        "- 采集时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 目标：%s" % rexc.env("ZF_HOST", "(未配置)"),
        "- 说明：本报告不含任何凭据；凭据相关行已脱敏。",
        "",
    ]

    # 上传并执行服务器侧 Python 探针
    probe_ok = False
    try:
        c = rexc.connect()
        try:
            sftp = c.open_sftp()
            with sftp.file("/tmp/_full_audit_probe.py", "wb") as fh:
                fh.write(SERVER_PROBE.encode("utf-8"))
            sftp.close()
        finally:
            c.close()
        rc, out = rexc.run("%s /tmp/_full_audit_probe.py" % PY, timeout=180)
        lines += ["## 数据库与前端静态资源", "", "```", scrub(out.strip()), "```", ""]
        probe_ok = "SERVER_PROBE_DONE" in out
    except Exception as exc:  # noqa: BLE001
        lines += ["## 数据库与前端静态资源", "", "采集失败：%s" % type(exc).__name__, ""]

    cur_section = ""
    for section, title, cmd, timeout in TASKS:
        if section != cur_section:
            lines += ["## %s" % section, ""]
            cur_section = section
        lines += ["### %s" % title, "", "```"]
        try:
            rc, out = rexc.run(cmd, timeout=timeout)
            lines.append(scrub(out.strip()) or "(无输出)")
        except Exception as exc:  # noqa: BLE001
            lines.append("采集异常：%s %s" % (type(exc).__name__, str(exc)[:120]))
        lines += ["```", ""]

    # 本地：凭据残留统计（只报数量，不报值）
    cred_re = re.compile(SECRET_LINE.pattern)
    ctx_re = re.compile(
        r"password|passwd|pwd|-p\b|zf_pass|secret|token|credential|"
        r"mysql|db_pass|db_pwd|create_engine|api_key|auth", re.I)
    skip = {".git", ".workbuddy", "__pycache__", "data", "解析文件", "_shots", "论文学习", "测试集"}
    # 这两个工具自身含结构化特征串用于识别，不计入残留
    tools = {"scrub_credentials.py", "full_audit.py"}
    n_files = 0
    for dirpath, dirnames, filenames in __import__("os").walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if fn in tools or not fn.endswith(
                    (".py", ".bat", ".sh", ".sql", ".md", ".json", ".cnf",
                     ".example", ".txt", ".cfg", ".ini")):
                continue
            p = Path(dirpath) / fn
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                if any(cred_re.search(ln) and ctx_re.search(ln)
                       for ln in text.splitlines()):
                    n_files += 1
            except OSError:
                pass
    lines += [
        "## 本地凭据残留",
        "",
        "- 仍含凭据字面量的文件数：**%d**（目标 0）" % n_files,
        "- 详情见 docs/凭据清理报告.md",
        "",
        "---",
        "",
        "采集完成：数据库探针 %s" % ("成功" if probe_ok else "失败/部分失败"),
        "",
    ]

    out_dir = ROOT / "docs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "运行时体检数据.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print("体检完成，报告：%s" % out_path)
    print("数据库探针=%s 凭据残留文件数=%d" % ("成功" if probe_ok else "失败", n_files))
    print("（详细内容请直接读报告文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
