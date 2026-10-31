# -*- coding: utf-8 -*-
"""全速模式：8 workers、正常优先级、online、看门狗 2 分钟、不开自动关机。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "解析文件"
YES = OUT / "yes_fulltext_gold"
PDFKU = OUT / "pdfku_library_fulltext_gold"


def ps():
    from scripts.win_hidden import python_procs

    return python_procs(include_pythonw=False)


def kill_gold() -> None:
    needles = (
        "gold_keep_alive",
        "gold_hunt_fulltext",
        "gold_pdfku_library",
        "gold_stall_watchdog",
        "gold_rescue",
        "gold_3h_audit",
        "gold_quiet_mode",
        "gold_anti_stall",
        "spawn_main",
    )
    for row in ps():
        c = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if pid and any(n in c for n in needles):
            from scripts.win_hidden import kill_pid; kill_pid(pid)
            print("kill", pid, flush=True)


def _child_env() -> dict:
    import os

    env = os.environ.copy()
    prev = (env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = str(ROOT) if not prev else str(ROOT) + os.pathsep + prev
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["ZEBRAFISH_PROJECT_ROOT"] = str(ROOT)
    return env


def spawn(mod: str, args: list[str], logname: str) -> None:
    log = OUT / logname
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = log.open("a", encoding="utf-8")
    fh.write(f"\n===== full_speed {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh.flush()
    subprocess.Popen(
        [sys.executable, "-u", "-m", mod, *args],
        cwd=str(ROOT),
        env=_child_env(),
        stdout=fh,
        stderr=subprocess.STDOUT,
    )
    print("spawn", mod, " ".join(args), flush=True)


def main() -> None:
    quiet = OUT / "ops_quiet_mode.json"
    lock = OUT / "ops_quiet_mode.lock"
    if lock.is_file() or quiet.is_file():
        try:
            st = json.loads(quiet.read_text(encoding="utf-8-sig")) if quiet.is_file() else {}
            if (
                lock.is_file()
                or st.get("hold") is True
                or st.get("permanent") is True
                or (st.get("action") or "") == "quiet_mode"
            ):
                print(
                    "REFUSED: quiet_mode hold/permanent 生效，禁止切换全速。"
                    "若确需全速，须人工删除 解析文件/ops_quiet_mode.json 与 ops_quiet_mode.lock 并明确授权。",
                    flush=True,
                )
                return
        except Exception:
            print("REFUSED: quiet_mode 锁定存在，默认禁止全速。", flush=True)
            return

    subprocess.run(["shutdown", "/a"], capture_output=True)
    kill_gold()
    time.sleep(2)
    (YES / "_hunt_timeout_blacklist.json").write_text("[]", encoding="utf-8")
    (PDFKU / "pdf_extract_blacklist.jsonl").write_text("", encoding="utf-8")

    # 高性能电源（若可用）
    subprocess.run(
        ["powercfg", "/setactive", "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"],
        capture_output=True,
    )

    spawn(
        "scripts.gold_stall_watchdog",
        ["--hours", "168", "--every-sec", "120", "--stall-sec", "120", "--workers", "8"],
        "gold_stall_watchdog.log",
    )
    spawn(
        "scripts.gold_keep_alive",
        ["--hours", "168", "--workers", "8", "--stall-sec", "120", "--no-shutdown"],
        "gold_keep_alive.log",
    )
    time.sleep(10)
    # 强制 online 高轮次战役（覆盖 keep_alive 默认 rounds）
    for row in ps():
        c = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if pid and ("gold_hunt_fulltext" in c or "gold_pdfku_library_full_gold" in c):
            from scripts.win_hidden import kill_pid; kill_pid(pid)
    time.sleep(1)
    spawn(
        "scripts.gold_hunt_fulltext",
        ["--hours", "160", "--workers", "8", "--rounds", "60"],
        "gold_hunt_fulltext.log",
    )
    spawn(
        "scripts.gold_pdfku_library_full_gold",
        ["--hours", "160", "--workers", "8", "--rounds", "60", "--pdf-only"],
        "gold_pdfku_library_full_gold.log",
    )
    spawn("scripts.gold_rescue_bad_tier", [], "gold_rescue_force.log")
    spawn(
        "scripts.gold_3h_audit",
        ["--hours", "168", "--every-sec", "10800"],
        "gold_3h_audit_daemon.log",
    )
    time.sleep(5)
    rows = []
    for row in ps():
        c = row.get("CommandLine") or ""
        if "gold_" in c:
            rows.append(f"{row.get('ProcessId')} {c[:140]}")
            print(rows[-1], flush=True)
    status = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "full_speed",
        "workers": 8,
        "stall_sec": 120,
        "shutdown": False,
        "alive": rows,
    }
    (OUT / "ops_full_speed.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"mode": "full_speed", "n": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
