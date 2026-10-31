# -*- coding: utf-8 -*-
"""detach 启动 PubMed 判别结果中文补译（可关 Cursor）。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.is_file():
    PY = ROOT / ".venv" / "Scripts" / "pythonw.exe"
TAG = "backfill_pubmed_judgement_zh"


def _running() -> list[int]:
    pids: list[int] = []
    try:
        out = subprocess.check_output(
            ["wmic", "process", "get", "ProcessId,CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in out.splitlines():
            if TAG not in line:
                continue
            parts = line.strip().split()
            if parts and parts[-1].isdigit():
                pids.append(int(parts[-1]))
    except Exception:
        pass
    return pids


def _kill_all() -> None:
    for _ in range(3):
        pids = _running()
        if not pids:
            return
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(2)


def main() -> int:
    force = "--force" in sys.argv or "-f" in sys.argv
    pids = _running()
    if pids:
        if len(pids) > 1:
            print(f"警告: 检测到 {len(pids)} 个重复中译进程")
        if not force:
            print(f"中文补译已在运行（PID: {', '.join(map(str, pids))}）")
            print("  重启请: python -m scripts.launch_backfill_pubmed_judgement_zh_detached --force")
            return 0
        _kill_all()
        print(f"已停止旧进程: {', '.join(map(str, pids))}")

    log = ROOT / "logs" / "pubmed_judgement_zh_backfill_live.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if not log.exists():
        log.write_text(f"=== launch {datetime.now().isoformat()} ===\n", encoding="utf-8")

    inner = f'"{PY}" -u -m scripts.backfill_pubmed_judgement_zh --resume --turbo >> "{log}" 2>&1'
    subprocess.Popen(
        ["cmd", "/c", f"start /B {inner}"],
        cwd=str(ROOT),
        shell=False,
        close_fds=True,
    )
    meta = {"started_at": datetime.now().isoformat(), "log": str(log)}
    (ROOT / "logs" / "pubmed_judgement_zh_backfill_job.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    time.sleep(8)
    left = _running()
    if len(left) > 1:
        keep = max(left)
        for pid in left:
            if pid != keep:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        left = [keep]
    if left:
        print(f"  运行中 PID: {', '.join(map(str, left))}")
    else:
        print("  警告: 未检测到后台进程，请检查日志")
    print("PubMed 中文补译已启动（turbo 并发，支持断点续传）")
    print(f"  日志: {log}")
    print(f"  进度: data/literature/pubmed_judgement_zh_backfill.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
