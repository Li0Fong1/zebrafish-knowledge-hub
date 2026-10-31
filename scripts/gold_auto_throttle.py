# -*- coding: utf-8 -*-
"""金标战役自动资源节流（16GB Legion）。

每 30–60s 观察 free RAM / CPU，按水位暂停/升降 workers。
保护 FAISS / 问答 Hub；不自动 full_speed(workers=8)；默认封顶 workers=2（静音）。

用法:
  .\\.venv\\Scripts\\python.exe -u -m scripts.gold_auto_throttle
  .\\.venv\\Scripts\\python.exe -u -m scripts.gold_auto_throttle --once
  .\\.venv\\Scripts\\python.exe -u -m scripts.gold_auto_throttle --detach
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "解析文件"
LOG = ROOT / "logs" / "gold_auto_throttle.log"
LOCK = ROOT / "logs" / "gold_auto_throttle.pid"
STATUS_JSON = OUT / "ops_auto_throttle.json"
STATUS_MD = OUT / "自动资源节流状态.md"

INTERVAL_SEC = 45
PAUSE_FREE_GB = 3.5
PAUSE_CPU = 85.0
START2_FREE_GB = 4.0
START2_CPU = 55.0
ALLOW4_FREE_GB = 7.0
ALLOW4_CPU = 40.0
ALLOW8_FREE_GB = 8.0
ALLOW8_CPU = 30.0
SUSTAIN_8_N = 3
SUSTAIN_4_N = 2
PREFER_MAX_WORKERS = 2  # 永久静音封顶

GOLD_KILL = (
    "gold_keep_alive",
    "gold_hunt_fulltext",
    "gold_pdfku_library_full_gold",
    "gold_stall_watchdog",
    "gold_rescue_bad_tier",
    "gold_hunt_watchdog",
    "gold_anti_stall",
    "gold_full_speed",
    "spawn_main",
)
PROTECT = ("memory_guard", "api_server", "uvicorn", "faiss", "cloudflared")
BELOW_NORMAL = 0x00004000
_LOCK_FD = None
_SPAWN_HOLD: list = []


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    try:
        print(line, flush=True)
    except OSError:
        pass
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        import psutil

        return psutil.pid_exists(int(pid))
    except Exception:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return str(pid) in (r.stdout or "")


def _is_auto_throttle_cmd(cmdline: str) -> bool:
    c = (cmdline or "").replace("\\", "/")
    # Ignore python -c one-liners whose source text mentions this module.
    if " -c " in f" {c} " or c.strip().startswith("-c"):
        return False
    return "-m scripts.gold_auto_throttle" in c


def _acquire_singleton() -> bool:
    """Exclusive lock file — process-scan alone is racy under double-invoke."""
    global _LOCK_FD
    LOG.parent.mkdir(parents=True, exist_ok=True)
    me = os.getpid()
    for attempt in range(3):
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_RDWR)
        except OSError as e:
            _log(f"auto_throttle lock open fail: {e}")
            return False
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                raw = os.read(fd, 64).decode("utf-8", "replace").strip()
                old = int(raw.split()[0]) if raw else 0
            except Exception:
                old = 0
            os.close(fd)
            if old and old != me and _pid_alive(old):
                still = [
                    r
                    for r in _ps_rows()
                    if _is_auto_throttle_cmd(r.get("cmdline") or "")
                    and int(r.get("pid") or 0) not in (0, me)
                    and "--detach" not in (r.get("cmdline") or "")
                ]
                if still:
                    _log(f"another gold_auto_throttle alive {[int(x['pid']) for x in still]}, exit")
                    return False
                # stale lock holder gone
                try:
                    LOCK.unlink(missing_ok=True)
                except Exception:
                    pass
                time.sleep(0.2)
                continue
            st = LOCK.stat() if LOCK.is_file() else None
            age = time.time() - st.st_mtime if st else 999
            if age < 90:
                _log("auto_throttle lock fresh, exit")
                return False
            _log("auto_throttle lock busy, exit")
            return False
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, f"{me}\n".encode())
        _LOCK_FD = fd
        return True
    return False


def _ps_rows() -> list[dict]:
    from scripts.win_hidden import python_procs

    return python_procs(include_pythonw=True)


def _sys_stats() -> dict:
    from scripts.win_hidden import sys_stats

    return sys_stats(cpu_interval=0.3)


def _is_protected(cmdline: str) -> bool:
    c = cmdline or ""
    return any(x in c for x in PROTECT)


def _kill_pid(pid: int) -> None:
    from scripts.win_hidden import kill_pid

    try:
        kill_pid(int(pid))
        _log(f"kill pid={pid}")
    except Exception as e:
        _log(f"kill fail pid={pid}: {e}")


def _set_below_normal(pid: int) -> None:
    from scripts.win_hidden import set_below_normal

    try:
        set_below_normal(int(pid))
    except Exception:
        pass


def _gold_alive() -> dict:
    roles = {
        "keep_alive": [],
        "hunt": [],
        "pdfku": [],
        "stall_watchdog": [],
        "rescue": [],
        "other_gold": [],
    }
    workers = 0
    for row in _ps_rows():
        c = row.get("cmdline") or ""
        pid = int(row.get("pid") or 0)
        if not pid or _is_protected(c):
            continue
        if _is_auto_throttle_cmd(c):
            continue
        m = re.search(r"--workers\s+(\d+)", c)
        if m:
            workers = max(workers, int(m.group(1)))
        if "gold_keep_alive" in c:
            roles["keep_alive"].append(pid)
        elif "gold_stall_watchdog" in c:
            roles["stall_watchdog"].append(pid)
        elif "gold_hunt_fulltext" in c and "hunt_watchdog" not in c:
            roles["hunt"].append(pid)
        elif "gold_pdfku_library_full_gold" in c:
            roles["pdfku"].append(pid)
        elif "gold_rescue" in c:
            roles["rescue"].append(pid)
        elif "gold_" in c or "spawn_main" in c:
            roles["other_gold"].append(pid)
    running = bool(roles["keep_alive"] or roles["hunt"] or roles["pdfku"])
    return {"running": running, "workers": workers, "roles": roles}


def _pause_gold() -> int:
    n = 0
    for row in _ps_rows():
        c = row.get("cmdline") or ""
        pid = int(row.get("pid") or 0)
        if not pid or _is_protected(c) or _is_auto_throttle_cmd(c):
            continue
        if any(x in c for x in GOLD_KILL):
            _kill_pid(pid)
            n += 1
    return n


def _spawn(mod: str, args: list[str], logname: str) -> int | None:
    from scripts.win_hidden import creationflags

    log = OUT / logname
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = log.open("a", encoding="utf-8")
    fh.write(f"\n===== auto_throttle {datetime.now().isoformat(timespec='seconds')} =====\n")
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
    _SPAWN_HOLD.append((p, fh))
    time.sleep(0.6)
    _set_below_normal(p.pid)
    _log(f"spawn pid={p.pid} {mod} {' '.join(args)} poll={p.poll()}")
    return p.pid


def _quiet_hold_on() -> bool:
    quiet = OUT / "ops_quiet_mode.json"
    lock = OUT / "ops_quiet_mode.lock"
    if lock.is_file():
        return True
    if not quiet.is_file():
        return False
    try:
        st = json.loads(quiet.read_text(encoding="utf-8-sig"))
        return bool(
            st.get("hold")
            or st.get("permanent")
            or st.get("locked")
            or (st.get("action") or "") == "quiet_mode"
        )
    except Exception:
        return True


def _start_quiet(workers: int) -> dict:
    """拉起 quiet 战役：keep_alive（hunt/pdfku/stall），禁用 rescue/full_speed。"""
    w = max(1, min(int(workers), PREFER_MAX_WORKERS if _quiet_hold_on() else 4))
    w = min(w, PREFER_MAX_WORKERS)  # overnight: always cap at 2
    gold = _gold_alive()
    # Already in desired quiet shape
    if (
        gold["roles"]["keep_alive"]
        and gold["roles"]["hunt"]
        and gold["roles"]["pdfku"]
        and gold["workers"]
        and gold["workers"] <= w
    ):
        for row in _ps_rows():
            pid = int(row.get("pid") or 0)
            c = row.get("cmdline") or ""
            if pid and any(x in c for x in ("gold_keep_alive", "gold_hunt", "gold_pdfku", "gold_stall")):
                _set_below_normal(pid)
        return {"started": False, "workers": w, "roles": gold["roles"], "no_rescue_full_speed": True}

    # Soft kill noisy extras only
    for row in _ps_rows():
        c = row.get("cmdline") or ""
        pid = int(row.get("pid") or 0)
        if not pid or _is_protected(c):
            continue
        if "gold_rescue" in c or "gold_full_speed" in c or "gold_hunt_watchdog" in c:
            _kill_pid(pid)
        m = re.search(r"--workers\s+(\d+)", c)
        if m and int(m.group(1)) > w and any(
            x in c for x in ("gold_keep_alive", "gold_hunt_fulltext", "gold_pdfku", "gold_stall")
        ):
            _kill_pid(pid)

    time.sleep(1.5)
    ka = _spawn(
        "scripts.gold_keep_alive",
        ["--hours", "168", "--workers", str(w), "--stall-sec", "300", "--no-shutdown"],
        "gold_keep_alive.log",
    )
    time.sleep(8)
    gold = _gold_alive()
    return {
        "started": True,
        "workers": w,
        "keep_alive_pid": ka,
        "roles": gold["roles"],
        "no_rescue_full_speed": True,
    }


def _desired_workers(free_gb: float, cpu: float, streak4: int, streak8: int, current: int) -> tuple[int, str]:
    """返回 (desired_workers, reason)。
    desired=0 → pause；-1 → 等待条件（暂停但未达 start2）。
    静音封顶=2；永不自动升到 8。
    """
    if free_gb < PAUSE_FREE_GB or cpu > PAUSE_CPU:
        return 0, f"pause: free<{PAUSE_FREE_GB}GB or cpu>{PAUSE_CPU}%"

    # permanent quiet: never allow 8; rarely 4
    cap = PREFER_MAX_WORKERS
    if _quiet_hold_on():
        cap = 2

    if free_gb >= START2_FREE_GB and cpu < START2_CPU:
        return min(2, cap), f"start2: free>={START2_FREE_GB}GB cpu<{START2_CPU}%"

    if free_gb >= 3.8 and cpu < 70:
        return min(2, cap), "soft_cap_w2: yellow zone (hold/reduce to 2)"

    return -1, f"wait: free={free_gb} cpu={cpu}"


def _write_status(
    mem: dict,
    action: str,
    desired: int,
    current: int,
    reason: str,
    gold: dict,
    extra: dict | None = None,
) -> None:
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "interval_sec": INTERVAL_SEC,
        "action": action,
        "reason": reason,
        "desired_workers": desired,
        "current_workers": current,
        "running": bool(gold.get("running")),
        "free_gb": mem.get("free_gb"),
        "cpu_pct": mem.get("cpu_pct"),
        "used_pct": mem.get("used_pct"),
        "total_gb": mem.get("total_gb"),
        "roles": gold.get("roles") or {},
        "policy": {
            "pause": f"free<{PAUSE_FREE_GB}GB OR cpu>{PAUSE_CPU}%",
            "workers2": f"free>={START2_FREE_GB}GB AND cpu<{START2_CPU}%",
            "workers4": f"free>={ALLOW4_FREE_GB}GB AND cpu<{ALLOW4_CPU}%",
            "workers8": f"free>{ALLOW8_FREE_GB}GB AND cpu<{ALLOW8_CPU}% sustained×{SUSTAIN_8_N} (prefer_max={PREFER_MAX_WORKERS})",
            "keep_faiss": True,
            "no_rescue_auto": True,
            "quiet_permanent": _quiet_hold_on(),
        },
        "extra": extra or {},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    STATUS_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = (
        f"# 自动资源节流状态\n\n"
        f"- 更新时间：{payload['updated_at']}\n"
        f"- 节流进程 PID：{payload['pid']}\n"
        f"- 动作：{action}\n"
        f"- 原因：{reason}\n"
        f"- 当前 workers：{current} → 目标：{desired}\n"
        f"- 可用内存：{mem.get('free_gb')} GB / 总计 {mem.get('total_gb')} GB（占用 {mem.get('used_pct')}%）\n"
        f"- CPU：{mem.get('cpu_pct')}%\n"
        f"- 静音托管：{'是' if _quiet_hold_on() else '否'}\n\n"
        f"## 策略（16GB）\n"
        f"- 暂停：free < {PAUSE_FREE_GB}GB 或 CPU > {PAUSE_CPU}%\n"
        f"- 静音封顶 workers={PREFER_MAX_WORKERS}\n"
        f"- 保护 FAISS；不自动起 rescue/full_speed\n"
    )
    STATUS_MD.write_text(md, encoding="utf-8")
    # reinforce quiet markers
    quiet = OUT / "ops_quiet_mode.json"
    lock = OUT / "ops_quiet_mode.lock"
    if not lock.is_file() or not quiet.is_file():
        q = {
            "updated_at": payload["updated_at"],
            "action": "quiet_mode",
            "workers": 2,
            "priority": "BelowNormal",
            "rescue": "stopped",
            "hold": True,
            "locked": True,
            "permanent": True,
            "note": "永久静音：由 gold_auto_throttle 托管；禁止 full_speed",
            "managed_by": "scripts.gold_auto_throttle",
        }
        quiet.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
        lock.write_text("permanent_quiet=1\n" + payload["updated_at"] + "\n", encoding="utf-8")
    bal = {
        "mode": "quiet",
        "updated_at": payload["updated_at"],
        "workers": 2,
        "paused": action.startswith("paused"),
        "full_speed_deferred": True,
        "note": "overnight quiet self-heal",
    }
    (OUT / "ops_balanced_mode.json").write_text(
        json.dumps(bal, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def one_cycle(state: dict) -> None:
    mem = _sys_stats()
    free = float(mem.get("free_gb") or 0)
    cpu = float(mem.get("cpu_pct") or 0)
    gold = _gold_alive()
    current = int(gold.get("workers") or 0)
    streak4 = int(state.get("streak4") or 0)
    streak8 = int(state.get("streak8") or 0)
    desired, reason = _desired_workers(free, cpu, streak4, streak8, current)
    action = "hold"
    extra: dict = {"streak4": streak4, "streak8": streak8}
    soft_killed = 0

    # Always kill rescue / full_speed under quiet policy
    for row in _ps_rows():
        c = row.get("cmdline") or ""
        pid = int(row.get("pid") or 0)
        if pid and ("gold_rescue_bad_tier" in c or "gold_full_speed" in c):
            _kill_pid(pid)
            soft_killed += 1
            extra["policy_no_rescue_full_speed"] = True

    if desired == 0:
        killed = _pause_gold()
        action = "paused_idle"
        extra["killed"] = killed
        extra["paused"] = True
    elif desired < 0:
        action = "wait_paused" if not gold.get("running") else f"wait_hold_w{max(current, 0)}"
    else:
        # need workers=desired
        if not gold.get("running") or current == 0:
            info = _start_quiet(desired)
            action = f"started_w{desired}" if info.get("started") else f"start_after_dedupe"
            extra.update({k: info.get(k) for k in ("roles", "keep_alive_pid") if k in info})
        elif current > desired:
            # scale down: restart quiet at lower workers
            _pause_gold()
            time.sleep(1)
            info = _start_quiet(desired)
            action = f"scaled_{current}_to_{desired}"
        else:
            action = f"hold_w{current or desired}"
            # dedupe same-role instances (keep newest)
            roles = gold.get("roles") or {}
            for role, needle in (
                ("keep_alive", "gold_keep_alive"),
                ("stall_watchdog", "gold_stall_watchdog"),
                ("hunt", "gold_hunt_fulltext"),
                ("pdfku", "gold_pdfku_library_full_gold"),
            ):
                pids = sorted(set(roles.get(role) or []))
                if len(pids) <= 1:
                    continue
                keep = max(pids)
                for pid in pids:
                    if pid != keep:
                        # don't wipe last campaign instance of hunt/pdfku if keep_alive will respawn
                        _kill_pid(pid)
                        soft_killed += 1
                        _log(f"dedupe: {role} kill {pid} keep {keep}")
            gold2 = _gold_alive()
            roles2 = gold2.get("roles") or {}
            wiped = not (
                roles2.get("keep_alive")
                and (roles2.get("hunt") or roles2.get("pdfku"))
            )
            if wiped:
                _log("dedupe wiped campaign → restart quiet")
                info = _start_quiet(desired)
                action = "start_after_dedupe"
                extra["start_after_dedupe"] = True
            else:
                gold = gold2
            for row in _ps_rows():
                pid = int(row.get("pid") or 0)
                c = row.get("cmdline") or ""
                if pid and any(x in c for x in ("gold_keep_alive", "gold_hunt", "gold_pdfku", "gold_stall")):
                    _set_below_normal(pid)

    extra["soft_killed"] = soft_killed
    gold = _gold_alive()
    current = int(gold.get("workers") or 0)
    _log(
        f"free={free}GB cpu={cpu}% desired={desired} current={current} action={action} | {reason}"
    )
    state["last_action"] = action
    state["last_desired"] = desired
    state["streak4"] = streak4
    state["streak8"] = streak8
    _write_status(mem, action, desired, current, reason, gold, extra)


def _detach_self() -> int:
    from scripts.win_hidden import creationflags

    py = ROOT / ".venv" / "Scripts" / "python.exe"
    exe = str(py if py.is_file() else sys.executable)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    fh = LOG.open("a", encoding="utf-8")
    fh.write(f"\n===== detach {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh.flush()
    p = subprocess.Popen(
        [exe, "-u", "-m", "scripts.gold_auto_throttle", "--foreground"],
        cwd=str(ROOT),
        stdout=fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags(),
        close_fds=False,
    )
    _SPAWN_HOLD.append((p, fh))
    return int(p.pid)


def main() -> None:
    ap = argparse.ArgumentParser(description="金标自动资源节流")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=INTERVAL_SEC)
    ap.add_argument("--detach", action="store_true", help="pythonw 后台常驻")
    ap.add_argument("--foreground", action="store_true", help="前台循环（detach 子进程用）")
    args = ap.parse_args()

    if args.detach and not args.once:
        living = []
        for row in _ps_rows():
            c = row.get("cmdline") or ""
            pid = int(row.get("pid") or 0)
            if pid and _is_auto_throttle_cmd(c) and "--detach" not in c:
                living.append(pid)
        if living:
            _log(f"detach_dedupe_auto_throttle already living={living}")
            print(f"gold_auto_throttle 已在运行 PID={living[0]}")
            return
        child = _detach_self()
        print(f"gold_auto_throttle 已后台启动 PID={child}")
        return

    if not _acquire_singleton():
        return

    _log(
        f"=== gold_auto_throttle start === interval={args.interval}s prefer_max={PREFER_MAX_WORKERS} "
        f"pause free<{PAUSE_FREE_GB}|cpu>{PAUSE_CPU} w2 free>={START2_FREE_GB}&cpu<{START2_CPU}"
    )
    state: dict = {"streak4": 0, "streak8": 0}
    round_i = 0
    while True:
        round_i += 1
        try:
            _log(f"===== cycle {round_i} =====")
            one_cycle(state)
        except Exception as e:
            _log(f"cycle_error: {e!r}")
        if args.once:
            break
        time.sleep(max(15.0, float(args.interval)))


if __name__ == "__main__":
    main()
