# -*- coding: utf-8 -*-
"""Kill stale one-shot jobs older than N hours; never touch Hub/MySQL/tunnel.

  python -m scripts.cleanup_stale_oneshots --max-hours 12 --dry-run
  python -m scripts.cleanup_stale_oneshots --max-hours 12
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import time


KEEP_SUBSTR = (
    "mysqld",
    "uvicorn api.api_server",
    "cloudflared",
    "systemd",
    "dbus-daemon",
)

# Commandline snippets that should not live forever
STALE_SUBSTR = (
    "while pgrep -f",
    "modelscope.hub.snapshot_download",
    "three_hour_watchdog",
    "overnight_medium_ops",
    "hub_gpu1_pulse",
    "LIKE CONCAT('%', LOWER(g.gene_name)",
    "scripts.collect_genome",  # only if combined with while — handled below
)


def _ps() -> list[tuple[int, float, str]]:
    out = subprocess.check_output(["ps", "-u", os.environ.get("USER", "deng"), "-o", "pid=,etime=,cmd="], text=True)
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, etime, cmd = parts[0], parts[1], parts[2]
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        rows.append((pid, _etime_hours(etime), cmd))
    return rows


def _etime_hours(etime: str) -> float:
    # [[dd-]hh:]mm:ss
    days = 0
    if "-" in etime:
        d, etime = etime.split("-", 1)
        days = int(d)
    bits = [int(x) for x in etime.split(":")]
    if len(bits) == 3:
        h, m, s = bits
    elif len(bits) == 2:
        h, m, s = 0, bits[0], bits[1]
    else:
        h, m, s = 0, 0, bits[0]
    return days * 24 + h + m / 60 + s / 3600


def _keep(cmd: str) -> bool:
    return any(k in cmd for k in KEEP_SUBSTR)


def _stale(cmd: str) -> bool:
    if "while pgrep" in cmd:
        return True
    if "snapshot_download" in cmd or "modelscope" in cmd:
        return True
    if "three_hour_watchdog" in cmd:
        return True
    if "hub_gpu1_pulse" in cmd or "HUB_PULSE" in cmd:
        return True
    if "LOWER(CAST(f.sections_json" in cmd or "LIKE CONCAT('%', LOWER(g.gene_name)" in cmd:
        return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-hours", type=float, default=12.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    killed = []
    for pid, hours, cmd in _ps():
        if _keep(cmd):
            continue
        if hours < args.max_hours:
            continue
        if not _stale(cmd):
            continue
        print(f"{'DRY' if args.dry_run else 'KILL'} pid={pid} age_h={hours:.1f} cmd={cmd[:160]}")
        if not args.dry_run:
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.5)
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            killed.append(pid)
    print(f"done killed={len(killed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
