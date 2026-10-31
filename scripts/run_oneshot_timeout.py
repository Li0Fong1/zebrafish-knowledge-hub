# -*- coding: utf-8 -*-
"""Run a one-shot command with hard timeout; always exit (no forever wait).

Usage (remote):
  python -m scripts.run_oneshot_timeout --timeout 7200 -- \\
    /path/to/python -u -m scripts.some_job

Or:
  ZEBRA_ONESHOT_TIMEOUT=3600 python -m scripts.run_oneshot_timeout -- cmd...
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser(description="One-shot with timeout; exit when done or timed out")
    ap.add_argument("--timeout", type=int, default=int(os.getenv("ZEBRA_ONESHOT_TIMEOUT", "7200")))
    ap.add_argument("--kill-grace", type=int, default=15)
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="command after --")
    args = ap.parse_args()
    cmd = args.cmd
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("need command after --", file=sys.stderr)
        return 2

    print(f"[oneshot] timeout={args.timeout}s cmd={' '.join(cmd)}", flush=True)
    t0 = time.time()
    try:
        proc = subprocess.Popen(cmd)
    except Exception as e:
        print(f"[oneshot] spawn fail: {e}", file=sys.stderr)
        return 127

    try:
        rc = proc.wait(timeout=args.timeout)
        print(f"[oneshot] exit={rc} elapsed={time.time() - t0:.1f}s", flush=True)
        return int(rc)
    except subprocess.TimeoutExpired:
        print(f"[oneshot] TIMEOUT {args.timeout}s — terminating", flush=True)
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=args.kill_grace)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        print(f"[oneshot] killed elapsed={time.time() - t0:.1f}s", flush=True)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
