# -*- coding: utf-8 -*-
"""detach 启动中文补译过夜看门狗（可关 Cursor）。"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "data" / "literature" / "overnight_zh_backfill_watchdog.lock"
PY = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
if not Path(PY).is_file():
    PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
FLAGS = 0x00000008 | 0x00000200 | 0x08000000 | 0x01000000
WORKER_TAG = "scripts.backfill_pubmed_judgement_zh"
WATCHDOG_TAG = "overnight_zh_backfill_watchdog"
WORKER_BAT = "run_zh_backfill_worker.bat"


def _kill_matching(tag: str) -> None:
    tags = (tag,) if tag != WORKER_TAG else (WORKER_TAG, WORKER_BAT)
    for exe in ("python.exe", "pythonw.exe", "cmd.exe"):
        try:
            out = subprocess.check_output(
                ["wmic", "process", "where", f"Name='{exe}'", "get", "ProcessId,CommandLine"],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for ln in out.splitlines():
                if not any(t in ln for t in tags):
                    continue
                parts = ln.strip().split()
                if parts and parts[-1].isdigit():
                    subprocess.run(["taskkill", "/PID", parts[-1], "/F"], capture_output=True)
        except Exception:
            pass


def main() -> int:
    force = "--force" in sys.argv or "-f" in sys.argv
    if LOCK.is_file() and not force:
        try:
            old = int(LOCK.read_text(encoding="utf-8").strip())
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {old}", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if str(old) in (r.stdout or ""):
                print(f"过夜看门狗已在运行 PID={old}")
                return 0
        except ValueError:
            pass

    if force:
        _kill_matching(WORKER_TAG)
        _kill_matching(WATCHDOG_TAG)
        LOCK.unlink(missing_ok=True)
        time.sleep(2)

    log = ROOT / "logs" / f"overnight_zh_backfill_watchdog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    f = open(log, "a", encoding="utf-8")
    subprocess.Popen(
        [PY, "-m", "scripts.overnight_zh_backfill_watchdog"],
        cwd=str(ROOT),
        stdout=f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=FLAGS,
    )
    time.sleep(3)
    print("中文补译过夜看门狗已启动（自动续跑 + 跑完更新演示文档）")
    print(f"  看门狗日志: logs/overnight_zh_backfill_watchdog.log")
    print(f"  补译日志:   logs/pubmed_judgement_zh_backfill_live.log")
    print(f"  进度:       data/literature/pubmed_judgement_zh_backfill.json")
    print("  可安全关闭 Cursor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
