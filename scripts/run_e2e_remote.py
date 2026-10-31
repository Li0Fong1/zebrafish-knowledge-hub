# -*- coding: utf-8 -*-
"""把 e2e_test.py 传到服务器后台跑，并轮询它的日志。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts\\run_e2e_remote.py start [--rounds 6] [--concurrency 3]
    .venv\\Scripts\\python.exe -X utf8 scripts\\run_e2e_remote.py log        # 看最新进度
    .venv\\Scripts\\python.exe -X utf8 scripts\\run_e2e_remote.py tail 120   # 看最后 120 行
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import paramiko  # noqa: E402

RT = "/data/zoujin/deng/zebrafish_runtime"
PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"
LOG = RT + "/logs/e2e_test.log"
try:  # 凭据只从 .env / 环境变量读，源码内不得硬编码（见 RELEASE_SECURITY_AUDIT）
    from _zf_creds import HOST, PORT, USER, require_password
except ImportError:  # 以包形式导入时（python -m scripts.xxx）
    from scripts._zf_creds import HOST, PORT, USER, require_password
LOCAL = Path(__file__).resolve().parent / "e2e_test.py"

# 无参数预设命令：调用方（VS Code 任务 / bat）不必拼任何引号，避免被壳层吃掉。
# 命名约定：aliasbuild = 看别名单列倒排表的构建进度；aliastat = 看落库统计。
ALIAS_LOG = "/tmp/alias_full.log"
_MYSQL = (RT + "/mysql80/bin/mysql -h127.0.0.1 -P3307 -uzebrafish "
          "-pzebrafish_remote_2026 zebrafish_db -B -N -e ")
_PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"
_GQA = "/data/zoujin/deng/zebrafish_gene_qa"

# 注意：每条预设都写成**单个字符串**，不用 + 拼接、不用行尾续行。
# 之前用多行 + 拼接时留下过一个未闭合的括号，整个文件都过不了语法检查。
PRESETS = {
    "aliasbuild": (
        "echo -n 'RUNNING='; pgrep -cf 'build_alias_doc_[i]ndex'; "
        "echo -n 'LOGLINES='; wc -l < " + ALIAS_LOG + "; "
        "grep -E '已扫描|写库|建表|完成' " + ALIAS_LOG + " | tail -8; "
        "echo '--- TAIL ---'; tail -4 " + ALIAS_LOG
    ),
    "aliastat": (
        _MYSQL + "'SELECT alias, title_docs, abstract_docs, body_docs, total_docs "
                 "FROM gene_alias_lit_stat ORDER BY total_docs DESC LIMIT 20'; "
                 "echo '--- DETAIL ROWS ---'; " +
        _MYSQL + "'SELECT COUNT(*) FROM gene_alias_doc'"
    ),
    "buildtp53": (
        "cd " + _GQA + " && rm -f " + ALIAS_LOG + " && setsid nohup " + _PY +
        " -X utf8 -m scripts.build_alias_doc_index --gene tp53 --rebuild > " + ALIAS_LOG +
        " 2>&1 < /dev/null; echo LAUNCHED; sleep 5; pgrep -cf 'build_alias_doc_[i]ndex'"
    ),
    # 四项功能验收（含真实问答，约 4~6 分钟）；输出落 /tmp/t4.log
    "run4": (
        "cd " + _GQA + "/scripts && rm -f /tmp/t4.log && setsid nohup " + _PY +
        " -X utf8 test_4features.py http://127.0.0.1:8080 > /tmp/t4.log 2>&1 < /dev/null; "
        "echo LAUNCHED; sleep 3; pgrep -cf 'test_4features[.]py'"
    ),
    "t4log": (
        "echo -n 'RUNNING='; pgrep -cf 'test_4features[.]py'; "
        "echo '--- LOG ---'; tail -60 /tmp/t4.log"
    ),
}

LOCAL_SH_OUT = Path(__file__).resolve().parents[1] / "解析文件" / "_remote_sh.txt"
LOCAL_PRESET_OUT = Path(__file__).resolve().parents[1] / "解析文件" / "_remote_preset.txt"


def _save(local: Path, text: str) -> None:
    """把远程输出落本地文件：终端偶尔会吞输出（^U / 续行），有文件就能用 read_file 看。"""
    try:
        local.write_text(text, encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "start"
    print("[1/3] 连接 %s:%s ..." % (HOST, PORT), flush=True)
    pwd = require_password()
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, port=PORT, username=USER, password=pwd, timeout=45,
                banner_timeout=60, auth_timeout=60, allow_agent=False, look_for_keys=False)
    cli.get_transport().set_keepalive(15)
    print("[2/3] 已连接", flush=True)

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
        return out.decode("utf-8", "replace")

    if mode == "start":
        extra = []
        if "--rounds" in sys.argv:
            extra += ["--rounds", sys.argv[sys.argv.index("--rounds") + 1]]
        if "--concurrency" in sys.argv:
            extra += ["--concurrency", sys.argv[sys.argv.index("--concurrency") + 1]]
        if "--only" in sys.argv:
            extra += ["--only", sys.argv[sys.argv.index("--only") + 1]]
        sftp = cli.open_sftp()
        sftp.put(str(LOCAL), RT + "/e2e_test.py")
        sftp.close()
        print("已上传 e2e_test.py →", RT + "/e2e_test.py")
        print(run("pgrep -f 'e2e_test[.]py' >/dev/null && pkill -9 -f 'e2e_test[.]py'; "
                  "rm -f %s; echo cleared" % LOG, 60).strip())
        cmd = ("cd %s && setsid nohup %s -u -X utf8 e2e_test.py http://127.0.0.1:8080 %s "
               ">> %s 2>&1 </dev/null & sleep 4; echo started pid=$(pgrep -f 'e2e_test[.]py' | head -1)"
               % (RT, PY, " ".join(extra), LOG))
        print(run(cmd, 60).strip())
        print("\n--- 日志开头 ---")
        print(run("sleep 6; head -25 %s" % LOG, 60).strip())
        cli.close()
        return 0

    if mode == "wait":
        # 阻塞等到远端测试结束，然后把完整日志抓回本地文件（之后直接读文件，不必反复轮询）
        local = Path(__file__).resolve().parents[1] / "解析文件" / "e2e_result.txt"
        print("等待远端测试结束（最多 30 分钟）…", flush=True)
        out = run("for i in $(seq 1 120); do pgrep -f 'e2e_test[.]py' >/dev/null || break; sleep 15; done; "
                  "echo '===E2E-LOG-BEGIN==='; cat %s; echo; echo '===E2E-LOG-END==='; "
                  "echo '--- 结果行 ---'; grep -E '结论|RSS|延迟|失败' %s | tail -12" % (LOG, LOG), 1900)
        local.write_text(out, encoding="utf-8")
        print("已写入 %s（%d 字符）" % (local, len(out)), flush=True)
        cli.close()
        return 0

    if mode == "api":
        # 直接取某个接口的返回（服务器本机打 8080），用于核对字段结构
        path = sys.argv[2] if len(sys.argv) > 2 else "/api/health"
        keep = int(sys.argv[3]) if len(sys.argv) > 3 else 600
        out = run("curl -s -m 60 'http://127.0.0.1:8080%s' | head -c %d" % (path, keep), 90)
        print("GET %s\n%s" % (path, out.strip()), flush=True)
        cli.close()
        return 0

    if mode == "get":
        # 二进制下载：run_e2e_remote.py get <远端路径> <本地路径>
        # 用 SFTP 按字节取，避免 sh+stdout 的换行/编码转换（恢复备份时必须字节一致）
        remote, local = sys.argv[2], sys.argv[3]
        sftp = cli.open_sftp()
        sftp.get(remote, local)
        sftp.close()
        print("已下载 %s → %s" % (remote, local), flush=True)
        cli.close()
        return 0

    if mode == "put":
        # 上传任意本地文件：run_e2e_remote.py put <本地路径> <远端路径>
        local, remote = sys.argv[2], sys.argv[3]
        sftp = cli.open_sftp()
        sftp.put(local, remote)
        sftp.close()
        print("已上传 %s → %s" % (local, remote), flush=True)
        cli.close()
        return 0

    if mode == "sh":
        # 在服务器上跑任意命令：run_e2e_remote.py sh "命令..."
        # 超时给足：以前的 120s 会让"跑得比 2 分钟久"的命令**丢掉全部输出**（表现为命令静默无输出）。
        # 注意：命令里出现嵌套引号时，Windows 的 PowerShell/cmd 会把它吃掉 →
        # 复杂的固定检查请用下面的预设（presets），不要在这里拼引号。
        cmd = " ".join(sys.argv[2:])
        out = run(cmd, 1800)
        _save(LOCAL_SH_OUT, out)
        print(out, flush=True)
        cli.close()
        return 0

    if mode in PRESETS:
        # 无参数预设：命令写在脚本里，调用方不用拼任何引号（任务/批处理里最稳）
        print(PRESETS[mode], flush=True)
        out = run(PRESETS[mode], 600)
        _save(LOCAL_PRESET_OUT, out)
        print(out, flush=True)
        cli.close()
        return 0

    if mode == "diag":
        print("=== 谁重启过 uvicorn / 为什么 ===", flush=True)
        print(run("echo '--- hub_guard.log 最近 25 行 ---'; tail -25 " + RT + "/logs/hub_guard.log 2>/dev/null | cut -c1-160", 60).strip(), flush=True)
        print("\n--- hub_supervise.log 最近 20 行 ---", flush=True)
        print(run("tail -20 " + RT + "/logs/hub_supervise.log 2>/dev/null | cut -c1-160", 60).strip(), flush=True)
        print("\n--- 内存/压力 ---", flush=True)
        print(run("free -m | head -3; echo; grep -E 'oom_kill|max' " + RT + "/logs/hub_supervise.log 2>/dev/null | tail -5", 60).strip(), flush=True)
        cli.close()
        return 0

    lines = 200
    if mode == "tail" and len(sys.argv) > 2:
        lines = int(sys.argv[2])
    elif mode == "log":
        lines = 30
    print("=== 进程 ===", flush=True)
    print(run("pgrep -af 'e2e_test[.]py' | cut -c1-90 || echo '(未在运行 —— 可能已结束)'", 40).strip(), flush=True)
    print("\n=== 服务健康（判断测试期间有没有被守护重启过） ===", flush=True)
    print(run("echo -n 'uvicorn 运行时长: '; ps -o etime= -p $(pgrep -f 'uvicorn api[.]api_server' | head -1) 2>/dev/null | tr -d ' \\n'; "
              "echo; echo -n 'uvicorn RSS(MB): '; ps -o rss= -p $(pgrep -f 'uvicorn api[.]api_server' | head -1) 2>/dev/null | awk '{printf \"%.1f\\n\", $1/1024}'; "
              "echo -n 'health: '; curl -s -m 10 -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:8080/api/health; "
              "echo -n '守护最近动作: '; cat " + RT + "/hub_guard_last.json 2>/dev/null | head -c 200", 60).strip(), flush=True)
    print("\n=== 日志后 %d 行 ===" % lines, flush=True)
    print(run("tail -%d %s" % (lines, LOG), 60).strip(), flush=True)
    print("[3/3] 完成", flush=True)
    cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
