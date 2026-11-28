# -*- coding: utf-8 -*-
"""只读查询后台服务最近被调用的日志（服务器上），结果落到本地 docs/。

采集内容：
  1. 日志文件清单（logs/ 下按修改时间倒序，大小 + 时间）
  2. hub.log 最近 N 行的 API 调用记录（uvicorn access log：时间 / 方法 / 路径 / 状态码 / 耗时）
  3. 按接口路径聚合的调用次数 Top 20
  4. 错误调用（4xx/5xx）汇总
  5. 服务健康与进程状态

不修改服务器任何文件；只读 tail/grep。报告：docs/后台服务调用日志.md
用法：python scripts/query_service_logs.py [行数，默认 400]
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import remote_exec as rexc  # noqa: E402

RUNTIME = rexc.env("ZF_RUNTIME", "/data/zoujin/deng/zebrafish_runtime")
LOGS = RUNTIME + "/logs"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 400

# 远程只读命令
CMD_FILES = (
    'echo "=== logs 目录文件（按时间倒序）==="; '
    'ls -lt --time-style=long-iso %s 2>/dev/null | head -30; '
    'echo "=== hub.log 大小/时间 ==="; '
    'ls -lh --time-style=long-iso %s/hub.log 2>/dev/null' % (LOGS, LOGS)
)

# uvicorn access + 应用日志尾部
CMD_TAIL = 'tail -n %d %s/hub.log 2>/dev/null' % (N, LOGS)

CMD_ERR = (
    'echo "=== 近 1000 行里的错误/异常 ==="; '
    'tail -n 1000 %s/hub.log 2>/dev/null | grep -iE "traceback|exception| 5[0-9][0-9] | 4[0-9][0-9] |error|failed" '
    '| tail -40 || true' % LOGS
)

CMD_STATUS = (
    'echo "=== 进程 ==="; '
    "ps -eo pid,etime,rss,args | grep -E 'uvicorn api[.]api_server:app' | grep -v grep || true; "
    'echo "=== health ==="; '
    'curl -s -m 8 http://127.0.0.1:8080/api/health | head -c 600; echo'
)


def scrub(text: str) -> str:
    """抹掉任何可能的口令字面量，报告不记录凭据原值。"""
    pat = re.compile(r"(?:[A-Za-z][A-Za-z0-9]*_remote_[0-9]{4}|deng[0-9]_[0-9]{4,})")
    out = []
    for ln in (text or "").splitlines():
        low = ln.lower()
        if any(k in low for k in ("password=", "passwd=", "zf_pass=", "api_key=", "token=")) and "=" in ln:
            out.append("  <已脱敏>")
        else:
            out.append(pat.sub("<redacted>", ln))
    return "\n".join(out)


def summarize_access(raw: str) -> str:
    """从 access log 提取 方法 路径 状态码，统计 Top。"""
    pat = re.compile(r'"(GET|POST|PUT|DELETE)\s+([^\s?]+)[^"]*"\s+(\d{3})')
    cnt: dict[str, int] = {}
    errs: list[str] = []
    total = 0
    for m in pat.finditer(raw):
        method, path, code = m.group(1), m.group(2), m.group(3)
        total += 1
        key = "%s %s -> %s" % (method, path, code)
        cnt[key] = cnt.get(key, 0) + 1
        if code.startswith(("4", "5")):
            errs.append(key)
    lines = ["  HTTP 调用总数（本次抓取）：%d" % total]
    lines.append("  --- 调用 Top 20 ---")
    for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])[:20]:
        lines.append("    %4d  %s" % (v, k))
    lines.append("  --- 4xx/5xx 次数：%d ---" % len(errs))
    for k, v in sorted({e: errs.count(e) for e in set(errs)}.items(), key=lambda kv: -kv[1])[:15]:
        lines.append("    %4d  %s" % (v, k))
    return "\n".join(lines)


def main() -> int:
    secs: list[str] = []
    raw_tail = ""
    for title, cmd in [
        ("日志文件清单", CMD_FILES),
        ("服务状态", CMD_STATUS),
        ("错误汇总", CMD_ERR),
    ]:
        try:
            rc, out = rexc.run(cmd, timeout=90)
        except Exception as exc:  # noqa: BLE001
            out = "采集失败：%s %s" % (type(exc).__name__, exc)
        secs += ["## %s" % title, "", "```", scrub(out.strip()), "```", ""]

    try:
        rc, raw_tail = rexc.run(CMD_TAIL, timeout=90)
    except Exception as exc:  # noqa: BLE001
        raw_tail = "采集失败：%s" % exc

    secs += [
        "## 调用统计（基于 hub.log 最近 %d 行）" % N, "",
        "```", summarize_access(raw_tail), "```", "",
        "## hub.log 最近 %d 行（脱敏）" % N, "",
        "```", scrub(raw_tail.strip()), "```", "",
    ]

    lines = [
        "# 后台服务调用日志查询",
        "",
        "- 查询时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 目标：%s" % rexc.env("ZF_HOST", ""),
        "- 说明：只读查询；已脱敏，不记录任何凭据原值。",
        "",
    ] + secs

    rp = ROOT / "docs" / "后台服务调用日志.md"
    rp.parent.mkdir(exist_ok=True)
    rp.write_text("\n".join(lines), encoding="utf-8")
    print("查询完成：%s" % rp)
    print("（详细内容请直接读报告文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
