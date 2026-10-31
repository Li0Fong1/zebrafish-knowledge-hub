# -*- coding: utf-8 -*-
"""一键启动夜间守护（detach，关 Cursor/CMD 不影响）。"""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "data" / "literature" / "overnight_supervisor.lock"
PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
PY = str(PYW if PYW.is_file() else ROOT / ".venv" / "Scripts" / "python.exe")
DETACHED = 0x00000008 | 0x00000200 | 0x08000000 | 0x01000000


def main() -> int:
    try:
        rows = subprocess.check_output(
            ["wmic", "process", "where", "Name='pythonw.exe'", "get", "CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        n = sum(1 for ln in rows.splitlines() if "overnight_literature_supervisor" in ln)
        if n > 0:
            print(f"已有 {n} 个夜间守护在运行，跳过启动")
            return 0
    except Exception:
        pass

    log = ROOT / "logs" / f"overnight_supervisor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    f = open(log, "a", encoding="utf-8")
    subprocess.Popen(
        [PY, "-m", "scripts.overnight_literature_supervisor"],
        cwd=str(ROOT),
        stdout=f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=DETACHED,
    )
    meta = {"started_at": datetime.now().isoformat(), "log": str(log)}
    (ROOT / "logs" / "overnight_supervisor_job.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    time.sleep(1)
    print("夜间守护已启动")
    print(f"  日志: {log}")
    print(f"  状态: data/literature/overnight_state.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
