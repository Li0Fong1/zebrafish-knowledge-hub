# -*- coding: utf-8 -*-
"""低并行/静音启动旁路 gte 全库重嵌（不覆盖现网，不改 Hub）。

注意 16GB：勿与 Web/Hub 双开全量 FAISS；Hub 已占用时用本脚本（BelowNormal + 小 batch）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
LOG = ROOT / "logs" / "gte_reembed_full.log"
JOB = ROOT / "logs" / "gte_reembed_job.json"
PROGRESS = ROOT / "解析文件" / "gte_reembed_progress.txt"


def _already_running() -> list[dict]:
    """检测是否已有 oneshot/gte 重嵌在跑，避免双开。"""
    found: list[dict] = []
    try:
        import ctypes
        from ctypes import wintypes

        # 用 WMIC/PowerShell 更稳；此处轻量：读 job + 任务列表
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(out or "[]")
        if isinstance(data, dict):
            data = [data]
        for row in data or []:
            cmd = str(row.get("CommandLine") or "")
            if "fulltext_oneshot_vectorize" in cmd or "launch_gte_reembed" in cmd:
                if "launch_gte_reembed_quiet" in cmd and "fulltext_oneshot" not in cmd:
                    continue
                found.append({"pid": row.get("ProcessId"), "cmd": cmd[:240]})
    except Exception:
        pass
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help=">0 则只嵌 N 篇；0=全库")
    ap.add_argument("--embed-batch", type=int, default=8)
    ap.add_argument("--shard-papers", type=int, default=50)
    ap.add_argument("--force", action="store_true", help="即使疑似已有进程也启动（危险）")
    args = ap.parse_args()

    running = _already_running()
    if running and not args.force:
        print("已有 gte/oneshot 重嵌进程，禁止双开：")
        for r in running:
            print(f"  PID={r['pid']}  {r['cmd']}")
        print(f"  进度: {PROGRESS}")
        print(f"  日志: {LOG}")
        return 2

    env = os.environ.copy()
    env["PUBMED_YES_SECTION_CHUNKING"] = "1"
    env["HF_ENDPOINT"] = env.get("HF_ENDPOINT") or "https://hf-mirror.com"
    env["FAISS_DISABLE_AVX2"] = "1"
    # 提示子进程：低优（子进程内也会 SetPriorityClass）
    env["GTE_REEMBED_QUIET"] = "1"

    cmd = [
        PY,
        "-u",
        "-m",
        "scripts.fulltext_oneshot_vectorize",
        "--embed-batch",
        str(args.embed_batch),
        "--shard-papers",
        str(args.shard_papers),
    ]
    if args.limit and args.limit > 0:
        cmd += ["--limit", str(args.limit)]
    else:
        cmd.append("--full")

    LOG.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    JOB.write_text(
        json.dumps(
            {
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "log": str(LOG),
                "progress": str(PROGRESS),
                "limit": args.limit or "ALL",
                "embed_batch": args.embed_batch,
                "shard_papers": args.shard_papers,
                "cmd": cmd,
                "note": "writes FAISS_PUBMED_YES_GTE_DIR only; hub stays on old index; BelowNormal",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    PROGRESS.write_text(
        "\n".join(
            [
                "gte 旁路全库重嵌进度",
                f"更新: {datetime.now().isoformat(timespec='seconds')}",
                "status: launching",
                f"log: {LOG}",
                "Hub: 仍指旧库（验收前勿切）",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with LOG.open("a", encoding="utf-8") as lf:
        lf.write(f"\n==== start {datetime.now().isoformat()} ====\n")
        lf.write("cmd: " + " ".join(cmd) + "\n")
        lf.flush()
        # CREATE_NO_WINDOW | DETACHED | BELOW_NORMAL_PRIORITY_CLASS
        flags = 0x08000000 | 0x00000008 | 0x00004000
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=lf,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=flags,
        )

    # 稍等确认进程存活
    time.sleep(2.0)
    alive = True
    try:
        alive = proc.poll() is None
    except Exception:
        pass

    meta = json.loads(JOB.read_text(encoding="utf-8"))
    meta["pid"] = proc.pid
    meta["alive_after_2s"] = alive
    JOB.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    PROGRESS.write_text(
        "\n".join(
            [
                "gte 旁路全库重嵌进度",
                f"更新: {datetime.now().isoformat(timespec='seconds')}",
                f"pid: {proc.pid}",
                f"alive: {alive}",
                f"log: {LOG}",
                f"job: {JOB}",
                "status: started" if alive else "status: exited_early_check_log",
                "停: taskkill /PID " + str(proc.pid) + " /F",
                "Hub: 仍指旧库（验收前勿切）",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print("已后台启动 gte 旁路重嵌（BelowNormal + 小 batch + section）")
    print(f"  pid: {proc.pid}  alive={alive}")
    print(f"  log: {LOG}")
    print(f"  progress: {PROGRESS}")
    print(f"  job: {JOB}")
    print("  Hub 未切换；旧 faiss_pubmed_yes 只读保留")
    return 0 if alive else 1


if __name__ == "__main__":
    raise SystemExit(main())
