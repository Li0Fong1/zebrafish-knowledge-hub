# -*- coding: utf-8 -*-
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "解析文件"


def ps():
    from scripts.win_hidden import python_procs

    return python_procs(include_pythonw=False)


NEEDLES = (
    "gold_keep_alive",
    "gold_hunt_fulltext",
    "gold_pdfku_library",
    "gold_stall_watchdog",
    "gold_rescue",
    "gold_3h_audit",
    "gold_force_all",
    "boot_ops_no_shutdown",
)


def main() -> None:
    subprocess.run(["shutdown", "/a"], capture_output=True)
    killed = []
    for row in ps():
        c = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if pid and any(n in c for n in NEEDLES):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            killed.append({"pid": pid, "cmd": c[:140]})
            print("kill", pid, c[:120], flush=True)

    # also stop agent 3h sleep loops if identifiable
    for row in ps():
        c = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if pid and "AGENT_LOOP_TICK_gold3h" in c:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            killed.append({"pid": pid, "cmd": c[:140]})
            print("kill loop", pid, flush=True)

    left = []
    for row in ps():
        c = row.get("CommandLine") or ""
        if any(n in c for n in NEEDLES):
            left.append(c[:120])

    status = {
        "paused": True,
        "killed": killed,
        "still_running": left,
    }
    (OUT / "gold_campaigns_paused.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"killed_n": len(killed), "still_running": left}, ensure_ascii=False))


if __name__ == "__main__":
    main()
