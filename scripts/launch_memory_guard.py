# -*- coding: utf-8 -*-
"""detach 启动内存循环检测（pythonw，可关 Cursor）。"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "logs" / "memory_guard.pid"
PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
EXE = str(PYW if PYW.is_file() else PY)
FLAGS = 0x00000008 | 0x00000200 | 0x08000000 | 0x01000000  # DETACHED + NEW_PROCESS + NO_WINDOW


def _pid_alive(pid: int) -> bool:
    r = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return str(pid) in (r.stdout or "")


def main() -> int:
    if LOCK.is_file():
        try:
            old = int((LOCK.read_text(encoding="utf-8") or "0").strip().split()[0])
        except Exception:
            old = 0
        if old and _pid_alive(old):
            print(f"memory_guard 已在运行 PID={old}")
            return 0
        LOCK.unlink(missing_ok=True)

    log = ROOT / "logs" / "memory_guard.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = log.open("a", encoding="utf-8")
    fh.write(f"\n===== launch {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh.flush()
    p = subprocess.Popen(
        [EXE, "-u", "-m", "scripts.memory_guard_loop"],
        cwd=str(ROOT),
        stdout=fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=FLAGS if sys.platform == "win32" else 0,
    )
    time.sleep(1.2)
    print(f"memory_guard 已启动 launcher_pid={p.pid} 日志=logs/memory_guard.log")
    print("状态文件=解析文件/memory_guard_status.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
