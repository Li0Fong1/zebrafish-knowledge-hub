# -*- coding: utf-8 -*-
"""Windows process helpers that never flash a visible console.

Hot loops (auto_throttle / keep_alive / stall_watchdog / memory_guard) used to
call powershell.exe via subprocess without CREATE_NO_WINDOW, which pops a
PowerShell window every cycle. Prefer psutil; fall back to hidden PowerShell only.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Any

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Detached + no window — for background campaign / loop launchers
DETACH_FLAGS = (
    DETACHED_PROCESS
    | CREATE_NEW_PROCESS_GROUP
    | CREATE_NO_WINDOW
    | CREATE_BREAKAWAY_FROM_JOB
)


def creationflags() -> int:
    return CREATE_NO_WINDOW if sys.platform == "win32" else 0


def detach_flags() -> int:
    return DETACH_FLAGS if sys.platform == "win32" else 0


def python_procs(*, include_pythonw: bool = True) -> list[dict[str, Any]]:
    """List python/pythonw processes with cmdline (no PowerShell).

    Each row has both lowercase and CIM-style keys so callers can use either:
      pid / ProcessId, cmdline / CommandLine, ws_mb
    """
    import psutil

    want = {"python.exe", "pythonw.exe"} if include_pythonw else {"python.exe"}
    rows: list[dict[str, Any]] = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (p.info.get("name") or "").lower()
            if name not in want:
                continue
            cmd_list = p.info.get("cmdline") or []
            cmdline = " ".join(str(x) for x in cmd_list) if cmd_list else ""
            ws_mb = 0.0
            try:
                mi = p.memory_info()
                ws_mb = round(float(mi.rss) / (1024 * 1024), 1)
            except (psutil.NoSuchProcess, psutil.AccessDenied, MemoryError, OSError):
                ws_mb = 0.0
            pid = int(p.info["pid"])
            rows.append(
                {
                    "pid": pid,
                    "ProcessId": pid,
                    "cmdline": cmdline,
                    "CommandLine": cmdline,
                    "ws_mb": ws_mb,
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, MemoryError):
            continue
        except Exception:
            continue
    return rows


def mem_stats() -> dict[str, float]:
    import psutil

    vm = psutil.virtual_memory()
    return {
        "total_gb": round(vm.total / (1024**3), 3),
        "free_gb": round(vm.available / (1024**3), 3),
        "used_gb": round(vm.used / (1024**3), 3),
        "used_pct": round(float(vm.percent), 2),
    }


def sys_stats(*, cpu_interval: float = 0.25) -> dict[str, float]:
    import psutil

    d = mem_stats()
    d["cpu_pct"] = round(float(psutil.cpu_percent(interval=cpu_interval)), 1)
    return d


def set_below_normal(pid: int) -> None:
    if pid <= 0:
        return
    try:
        import psutil

        p = psutil.Process(int(pid))
        cls = getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
        p.nice(cls)
    except Exception:
        pass


def kill_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil

        psutil.Process(int(pid)).kill()
        return True
    except Exception:
        pass
    r = subprocess.run(
        ["taskkill", "/PID", str(pid), "/F"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags(),
    )
    return r.returncode == 0


def powershell_output(cmd: str, *, timeout: int = 60) -> str:
    """Hidden PowerShell only — use when psutil cannot cover the need."""
    return subprocess.check_output(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-Command",
            cmd,
        ],
        text=True,
        errors="replace",
        timeout=timeout,
        creationflags=creationflags(),
    ).strip()


def powershell_run(cmd: str, *, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-Command",
            cmd,
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        creationflags=creationflags(),
    )
