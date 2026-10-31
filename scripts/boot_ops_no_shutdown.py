# -*- coding: utf-8 -*-
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "解析文件"


def ps():
    from scripts.win_hidden import python_procs

    return python_procs(include_pythonw=False)


def kill_match(*needles: str) -> None:
    for row in ps():
        c = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if pid and any(n in c for n in needles):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            print("kill", pid, c[:120], flush=True)


def spawn(mod: str, args: list[str], logname: str) -> None:
    log = OUT / logname
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = log.open("a", encoding="utf-8")
    fh.write(f"\n===== boot {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh.flush()
    subprocess.Popen(
        [sys.executable, "-u", "-m", mod, *args],
        cwd=str(ROOT),
        stdout=fh,
        stderr=subprocess.STDOUT,
    )
    print("spawn", mod, " ".join(args), flush=True)


def main() -> int:
    r = subprocess.run(["shutdown", "/a"], capture_output=True, text=True, errors="replace")
    print("shutdown /a:", (r.stdout or r.stderr or "ok/none").strip()[:200], flush=True)

    flag = OUT / "AUTO_GOLD_DONE_READY_SHUTDOWN.txt"
    if flag.is_file():
        flag.unlink()
        print("removed AUTO_GOLD_DONE_READY_SHUTDOWN.txt", flush=True)

    # kill keep_alive that would auto-shutdown
    for row in ps():
        c = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if pid and "gold_keep_alive" in c and "--no-shutdown" not in c:
            kill_match("gold_keep_alive")
            time.sleep(1)
            break

    alive = " ".join((r.get("CommandLine") or "") for r in ps())

    if "gold_stall_watchdog" not in alive:
        spawn(
            "scripts.gold_stall_watchdog",
            ["--hours", "168", "--every-sec", "300", "--stall-sec", "300", "--workers", "8"],
            "gold_stall_watchdog.log",
        )
    if "gold_keep_alive" not in alive or not any(
        "gold_keep_alive" in (r.get("CommandLine") or "")
        and "--no-shutdown" in (r.get("CommandLine") or "")
        for r in ps()
    ):
        if "gold_keep_alive" in alive:
            kill_match("gold_keep_alive")
            time.sleep(1)
        spawn(
            "scripts.gold_keep_alive",
            ["--hours", "168", "--workers", "8", "--stall-sec", "300", "--no-shutdown"],
            "gold_keep_alive.log",
        )
        time.sleep(12)

    alive2 = " ".join((r.get("CommandLine") or "") for r in ps())
    if "gold_hunt_fulltext" not in alive2:
        spawn(
            "scripts.gold_hunt_fulltext",
            ["--hours", "160", "--workers", "8", "--rounds", "40", "--start-online"],
            "gold_hunt_fulltext.log",
        )
    if "gold_pdfku_library_full_gold" not in alive2:
        spawn(
            "scripts.gold_pdfku_library_full_gold",
            ["--hours", "160", "--workers", "8", "--rounds", "40", "--start-online"],
            "gold_pdfku_library_full_gold.log",
        )
    if "gold_rescue_bad_tier" not in alive2:
        spawn("scripts.gold_rescue_bad_tier", [], "gold_rescue_force.log")

    has_3h = any(
        "gold_3h_audit" in (r.get("CommandLine") or "")
        and "--hours" in (r.get("CommandLine") or "")
        and "--once" not in (r.get("CommandLine") or "")
        for r in ps()
    )
    if not has_3h:
        spawn(
            "scripts.gold_3h_audit",
            ["--hours", "168", "--every-sec", "10800"],
            "gold_3h_audit_daemon.log",
        )

    time.sleep(5)
    print("=== ALIVE ===", flush=True)
    rows = []
    for row in ps():
        c = row.get("CommandLine") or ""
        if any(
            x in c
            for x in (
                "gold_keep_alive",
                "gold_hunt_fulltext",
                "gold_pdfku",
                "gold_stall_watchdog",
                "gold_rescue",
                "gold_3h_audit",
            )
        ):
            print(row.get("ProcessId"), c[:160], flush=True)
            rows.append(c[:160])

    ka_ok = any(
        "gold_keep_alive" in (r.get("CommandLine") or "")
        and "--no-shutdown" in (r.get("CommandLine") or "")
        for r in ps()
    )
    status = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "shutdown_cancelled": True,
        "keep_alive_no_shutdown": ka_ok,
        "audit_3h": has_3h
        or any("gold_3h_audit" in (r.get("CommandLine") or "") for r in ps()),
        "alive": rows,
    }
    (OUT / "ops_boot_no_shutdown.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"keep_alive_no_shutdown": ka_ok, "n_alive": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
