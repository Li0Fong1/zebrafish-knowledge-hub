# -*- coding: utf-8 -*-
"""detach 启动过夜看门狗。"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "data" / "literature" / "overnight_watchdog.lock"
PY = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
if not Path(PY).is_file():
    PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
FLAGS = 0x00000008 | 0x00000200 | 0x08000000 | 0x01000000


def main() -> int:
    if LOCK.is_file():
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
                print(f"看门狗已在运行 PID={old}")
                return 0
        except ValueError:
            pass
        LOCK.unlink(missing_ok=True)

    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", "Name='pythonw.exe'", "get", "CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if "overnight_watchdog" in out:
            print("看门狗已在运行")
            return 0
    except Exception:
        pass

    log = ROOT / "logs" / f"overnight_watchdog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    f = open(log, "a", encoding="utf-8")
    subprocess.Popen(
        [PY, "-m", "scripts.overnight_watchdog"],
        cwd=str(ROOT),
        stdout=f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=FLAGS,
    )
    time.sleep(1)
    print(f"看门狗已启动，日志 logs/overnight_watchdog.log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
