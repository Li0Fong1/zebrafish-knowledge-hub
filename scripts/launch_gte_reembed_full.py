# -*- coding: utf-8 -*-
"""全速启动旁路 gte 全库重嵌（不覆盖现网，不改 Hub）。

比 quiet 版：Normal 优先级、更大 embed_batch / shard_papers。
16GB 机器可能顶内存——Hub 仍用旧库；FreeGB 过低时自行停。
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
    found: list[dict] = []
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(out or "[]")
        if isinstance(data, dict):
            data = [data]
        for row in data or []:
            cmd = str(row.get("CommandLine") or "")
            if "fulltext_oneshot_vectorize" in cmd:
                found.append({"pid": row.get("ProcessId"), "cmd": cmd[:240]})
    except Exception:
        pass
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--embed-batch", type=int, default=32)
    ap.add_argument("--shard-papers", type=int, default=100)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--kill-existing", action="store_true", help="先杀掉已有 oneshot")
    args = ap.parse_args()

    running = _already_running()
    if running and args.kill_existing:
        for r in running:
            pid = r.get("pid")
            if pid:
                subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
        time.sleep(1.5)
        running = _already_running()

    if running and not args.force:
        print("已有 gte/oneshot 重嵌进程，禁止双开（加 --kill-existing 可先杀）：")
        for r in running:
            print(f"  PID={r['pid']}  {r['cmd']}")
        return 2

    env = os.environ.copy()
    env["PUBMED_YES_SECTION_CHUNKING"] = "1"
    env["HF_ENDPOINT"] = env.get("HF_ENDPOINT") or "https://hf-mirror.com"
    env["GTE_REEMBED_FULL"] = "1"
    env.pop("GTE_REEMBED_QUIET", None)

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
                "mode": "FULL_SPEED",
                "log": str(LOG),
                "progress": str(PROGRESS),
                "limit": args.limit or "ALL",
                "embed_batch": args.embed_batch,
                "shard_papers": args.shard_papers,
                "cmd": cmd,
                "note": "Normal priority; writes GTE dir only; hub stays on old index",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with LOG.open("a", encoding="utf-8") as lf:
        lf.write(f"\n==== FULL SPEED start {datetime.now().isoformat()} ====\n")
        lf.write("cmd: " + " ".join(cmd) + "\n")
        lf.flush()
        # CREATE_NO_WINDOW | DETACHED；不加 BELOW_NORMAL
        flags = 0x08000000 | 0x00000008
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=lf,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=flags,
        )

    time.sleep(2.0)
    alive = proc.poll() is None
    meta = json.loads(JOB.read_text(encoding="utf-8"))
    meta["pid"] = proc.pid
    meta["alive_after_2s"] = alive
    JOB.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    PROGRESS.write_text(
        "\n".join(
            [
                "gte 旁路全库重嵌进度（全速）",
                f"更新: {datetime.now().isoformat(timespec='seconds')}",
                f"pid: {proc.pid}",
                f"alive: {alive}",
                f"embed_batch: {args.embed_batch}",
                f"shard_papers: {args.shard_papers}",
                "priority: Normal (GTE_REEMBED_FULL=1)",
                f"log: {LOG}",
                f"job: {JOB}",
                "status: started_full" if alive else "status: exited_early_check_log",
                f"停: taskkill /PID {proc.pid} /F",
                "Hub: 仍指旧库（验收前勿切）",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print("已后台启动 gte 旁路重嵌（全速 Normal + 大 batch + section）")
    print(f"  pid: {proc.pid}  alive={alive}")
    print(f"  log: {LOG}")
    print(f"  progress: {PROGRESS}")
    return 0 if alive else 1


if __name__ == "__main__":
    raise SystemExit(main())
