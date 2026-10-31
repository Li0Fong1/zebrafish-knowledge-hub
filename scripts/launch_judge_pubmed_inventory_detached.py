# -*- coding: utf-8 -*-
"""detach 启动 PubMed 全库判别（可关 Cursor）。"""
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
            if "judge_pubmed_inventory" not in line:
                continue
            parts = line.strip().split()
            if parts and parts[-1].isdigit():
                pids.append(int(parts[-1]))
    except Exception:
        pass
    return pids


def _kill_all_judge() -> None:
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
            print(f"警告: 检测到 {len(pids)} 个重复判别进程")
        if not force:
            print(f"PubMed 全库判别已在运行（PID: {', '.join(map(str, pids))}）")
            print("  重启请: python -m scripts.launch_judge_pubmed_inventory_detached --force")
            return 0
        _kill_all_judge()
        print(f"已停止旧进程: {', '.join(map(str, pids))}")

    log = ROOT / "logs" / f"pubmed_judgement_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"=== launch {datetime.now().isoformat()} ===\n", encoding="utf-8")

    # cmd /c start /B：独立后台，日志追加到文件
    inner = f'"{PY}" -u -m scripts.judge_pubmed_inventory --resume --turbo >> "{log}" 2>&1'
    subprocess.Popen(
        ["cmd", "/c", f"start /B {inner}"],
        cwd=str(ROOT),
        shell=False,
        close_fds=True,
    )
    meta = {"started_at": datetime.now().isoformat(), "log": str(log)}
    (ROOT / "logs" / "pubmed_judgement_job.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    time.sleep(10)
    left = _running()
    if len(left) > 1:
        keep = max(left)
        for pid in left:
            if pid != keep:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        print(f"  已清理重复进程，保留 PID {keep}")
        left = [keep]
    if len(left) == 1:
        print(f"  运行中 PID: {left[0]}")
    elif left:
        print(f"  运行中 PID: {', '.join(map(str, left))}")
    else:
        print("  警告: 未检测到后台进程，请检查日志")
    print("PubMed 全库判别已启动（turbo 并发 + 混合省 token，支持断点续传）")
    print(f"  日志: {log}")
    print(f"  进度: data/literature/pubmed_judgement_progress.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
