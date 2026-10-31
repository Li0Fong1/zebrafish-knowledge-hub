# -*- coding: utf-8 -*-
"""降温静音：降并发、低优先级、停掉最耗电的 rescue；战役继续但不抢前台。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "解析文件"
RUN_LOCK = OUT / "ops_quiet_mode.run.lock"

# Keep child stdio handles alive for process lifetime
_SPAWN_HOLD: list = []


def ps():
    from scripts.win_hidden import python_procs

    # Campaigns run as pythonw.exe — must include or quiet kills nothing.
    return python_procs(include_pythonw=True)


def kill_match(*needles: str) -> int:
    n = 0
    me = __import__("os").getpid()
    for row in ps():
        c = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if not pid or pid == me:
            continue
        if "gold_quiet_mode" in c:
            continue
        if any(x in c for x in needles):
            from scripts.win_hidden import kill_pid

            kill_pid(pid)
            print("kill", pid, c[:90], flush=True)
            n += 1
    return n


def set_below_normal(pid: int) -> None:
    from scripts.win_hidden import set_below_normal as _nice

    _nice(pid)


def spawn(mod: str, args: list[str], logname: str) -> int | None:
    from scripts.win_hidden import creationflags

    log = OUT / logname
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = log.open("a", encoding="utf-8")
    fh.write(f"\n===== quiet {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh.flush()
    p = subprocess.Popen(
        [sys.executable, "-u", "-m", mod, *args],
        cwd=str(ROOT),
        stdout=fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags(),
        close_fds=False,
    )
    # Critical: retain Popen + fh so Windows does not close child stdout
    _SPAWN_HOLD.append((p, fh))
    time.sleep(0.8)
    set_below_normal(p.pid)
    print("spawn", p.pid, mod, " ".join(args), "poll", p.poll(), flush=True)
    return p.pid


def _acquire_run_lock():
    """Prevent double quiet_mode (Cursor/shell sometimes double-invokes)."""
    OUT.mkdir(parents=True, exist_ok=True)
    fp = open(RUN_LOCK, "a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt

            fp.seek(0)
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        print("quiet_mode already running (lock busy) — exit", flush=True)
        return None
    fp.seek(0)
    fp.truncate()
    fp.write(f"{__import__('os').getpid()}\n{datetime.now().isoformat(timespec='seconds')}\n")
    fp.flush()
    return fp


def main() -> None:
    lock_fp = _acquire_run_lock()
    if lock_fp is None:
        return

    try:
        # 1) stop noisy / high-worker parents
        kill_match(
            "gold_rescue_bad_tier",
            "gold_hunt_watchdog",
            "gold_hunt_fulltext",
            "gold_pdfku_library_full_gold",
            "gold_stall_watchdog",
            "gold_keep_alive",
            "gold_anti_stall",
            "spawn_main",
        )
        time.sleep(2)

        workers = "2"
        ka_pid = spawn(
            "scripts.gold_keep_alive",
            ["--hours", "168", "--workers", workers, "--stall-sec", "300", "--no-shutdown"],
            "gold_keep_alive.log",
        )
        time.sleep(10)

        # Trim duplicates: prefer just-spawned keep_alive; else newest PID
        prefer = {ka_pid} if ka_pid else set()
        for needle in (
            "gold_keep_alive",
            "gold_stall_watchdog",
            "gold_hunt_fulltext",
            "gold_pdfku_library_full_gold",
        ):
            hits = []
            for row in ps():
                c = row.get("CommandLine") or ""
                pid = int(row.get("ProcessId") or 0)
                if not pid or needle not in c:
                    continue
                if needle == "gold_hunt_fulltext" and "gold_hunt_watchdog" in c:
                    continue
                hits.append(pid)
            hits = sorted(set(hits))
            if len(hits) <= 1:
                continue
            keep = next((p for p in hits if p in prefer), max(hits))
            from scripts.win_hidden import kill_pid

            for pid in hits:
                if pid != keep:
                    kill_pid(pid)
                    print("dedupe", needle, "kill", pid, "keep", keep, flush=True)

        for row in ps():
            c = row.get("CommandLine") or ""
            pid = int(row.get("ProcessId") or 0)
            if not pid:
                continue
            if any(
                x in c
                for x in (
                    "gold_keep_alive",
                    "gold_hunt_fulltext",
                    "gold_pdfku",
                    "gold_stall_watchdog",
                    "spawn_main",
                )
            ):
                set_below_normal(pid)

        subprocess.run(
            ["powercfg", "/setactive", "381b4222-f694-41f0-9685-ff5bb260df2e"],
            capture_output=True,
        )
        print("power plan -> Balanced", flush=True)

        time.sleep(2)
        rows = []
        for row in ps():
            c = row.get("CommandLine") or ""
            pid = int(row.get("ProcessId") or 0)
            if pid and "gold_" in c and "gold_quiet_mode" not in c:
                set_below_normal(pid)
                rows.append(f"{pid} {c[:120]}")
                print("nice", pid, flush=True)

        status = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "action": "quiet_mode",
            "workers": 2,
            "priority": "BelowNormal",
            "rescue": "stopped",
            "power": "Balanced",
            "hold": True,
            "permanent": True,
            "alive": rows,
            "keep_alive_pid": ka_pid,
            "note": "永久静音：workers=2，BelowNormal；禁止 full_speed/rescue；不得自动改回全速",
        }
        (OUT / "ops_quiet_mode.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (OUT / "ops_quiet_mode.lock").write_text(
            "permanent_quiet=1\n" + datetime.now().isoformat(timespec="seconds") + "\n",
            encoding="utf-8",
        )
        print(json.dumps(status, ensure_ascii=False, indent=2), flush=True)
    finally:
        try:
            lock_fp.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
