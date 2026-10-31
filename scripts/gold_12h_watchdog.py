# -*- coding: utf-8 -*-
"""看门狗：若 12h 战役意外退出且尚未闭环，则自动重启直至达标或超过总时限。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = ROOT / "解析文件" / "yes_fulltext_gold" / "final_12h_status.json"
LOG = ROOT / "解析文件" / "yes_fulltext_gold" / "watchdog.log"
TOTAL_HOURS = 12.0


def log(msg: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def is_done() -> bool:
    if not STATUS.is_file():
        return False
    try:
        st = json.loads(STATUS.read_text(encoding="utf-8-sig"))
    except Exception:
        return False
    return (
        bool(st.get("final"))
        and int(st.get("pending") or 1) == 0
        and int(st.get("missing") or 1) == 0
    )


def main() -> int:
    deadline = datetime.now() + timedelta(hours=TOTAL_HOURS)
    log(f"watchdog start deadline={deadline.isoformat(timespec='seconds')}")
    while datetime.now() < deadline:
        if is_done():
            log("DONE — corpus closed")
            return 0
        remain_h = max(0.3, (deadline - datetime.now()).total_seconds() / 3600.0)
        # 最后 2 小时强制 close-out
        cmd = [
            sys.executable,
            "-u",
            "-m",
            "scripts.gold_final_12h_campaign",
            "--hours",
            f"{remain_h:.2f}",
            "--workers",
            "3",
        ]
        if remain_h <= 2.0:
            cmd.append("--close-out")
        log(f"spawn: {' '.join(cmd)}")
        proc = subprocess.run(cmd, cwd=str(ROOT))
        log(f"child exit={proc.returncode}")
        if is_done():
            log("DONE after child")
            return 0
        time.sleep(15)
    # 超时前最后强制收口
    log("deadline — force close-out")
    subprocess.run(
        [
            sys.executable,
            "-u",
            "-m",
            "scripts.gold_final_12h_campaign",
            "--hours",
            "0.5",
            "--workers",
            "2",
            "--close-out",
        ],
        cwd=str(ROOT),
    )
    log(f"final status done={is_done()}")
    return 0 if is_done() else 2


if __name__ == "__main__":
    raise SystemExit(main())
