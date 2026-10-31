# -*- coding: utf-8 -*-
"""
全量冲击金标：清黑名单/unreachable 闸门，rescue 长正文，拉起双战役+看门狗。
不降门槛、不垫文。付费墙无 OA 的仍需人工 PDF。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gold_hunt_fulltext import is_pdf_gold  # noqa: E402

YES = ROOT / "解析文件" / "yes_fulltext_gold"
PDFKU = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
HUNT_BL = YES / "_hunt_timeout_blacklist.json"
PDFKU_BL = PDFKU / "pdf_extract_blacklist.jsonl"
LOG = ROOT / "解析文件" / "gold_force_all_boot.log"


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _ps_python() -> list[dict]:
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    out = subprocess.check_output(
        ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", cmd], text=True, errors="replace"
    , creationflags=0x08000000 if __import__('sys').platform == 'win32' else 0).strip()
    data = json.loads(out) if out else []
    return [data] if isinstance(data, dict) else list(data or [])


def _kill_gold() -> None:
    needles = (
        "gold_keep_alive",
        "gold_hunt_fulltext",
        "gold_pdfku_library",
        "gold_stall_watchdog",
        "gold_rescue",
    )
    for row in _ps_python():
        c = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if pid and any(n in c for n in needles):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            _log(f"kill {pid} {c[:100]}")


def reopen_non_gold(root: Path) -> tuple[int, int]:
    opened = gold = 0
    for p in root.glob("batch_*/papers/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if is_pdf_gold(rec):
            gold += 1
            continue
        dirty = False
        if rec.get("gold_tier") in (
            "unreachable_oa",
            "structural_exhausted_oa",
            "fulltext_step",
        ):
            rec["gold_tier"] = "retry_for_gold"
            dirty = True
        metrics = dict(rec.get("metrics") or {})
        if metrics.get("oa_unreachable"):
            metrics.pop("oa_unreachable", None)
            rec["metrics"] = metrics
            dirty = True
        if dirty:
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            opened += 1
    return opened, gold


def clear_blacklists() -> None:
    if HUNT_BL.is_file():
        HUNT_BL.write_text("[]", encoding="utf-8")
        _log(f"cleared {HUNT_BL.name}")
    if PDFKU_BL.is_file():
        PDFKU_BL.write_text("", encoding="utf-8")
        _log(f"cleared {PDFKU_BL.name}")


def spawn(mod: str, args: list[str], logname: str) -> None:
    log = ROOT / "解析文件" / logname
    fh = log.open("a", encoding="utf-8")
    fh.write(f"\n===== force_all_gold {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh.flush()
    subprocess.Popen(
        [sys.executable, "-u", "-m", mod, *args],
        cwd=str(ROOT),
        stdout=fh,
        stderr=subprocess.STDOUT,
    )
    _log(f"spawn {mod} {' '.join(args)}")


def main() -> int:
    _log("=== force all to gold boot ===")
    _kill_gold()
    time.sleep(2)
    clear_blacklists()
    y_open, y_gold = reopen_non_gold(YES)
    p_open, p_gold = reopen_non_gold(PDFKU)
    _log(f"yes gold={y_gold} reopened={y_open}")
    _log(f"pdfku gold={p_gold} reopened={p_open}")

    # rescue 可能很久：后台跑，不阻塞战役启动
    spawn("scripts.gold_rescue_bad_tier", [], "gold_rescue_force.log")
    time.sleep(1)

    spawn(
        "scripts.gold_stall_watchdog",
        ["--hours", "96", "--every-sec", "300", "--stall-sec", "300", "--workers", "8"],
        "gold_stall_watchdog.log",
    )
    time.sleep(1)
    spawn(
        "scripts.gold_keep_alive",
        ["--hours", "96", "--workers", "8", "--stall-sec", "300", "--no-shutdown"],
        "gold_keep_alive.log",
    )
    time.sleep(10)
    if not any("gold_hunt_fulltext" in (x.get("CommandLine") or "") for x in _ps_python()):
        spawn(
            "scripts.gold_hunt_fulltext",
            ["--hours", "90", "--workers", "8", "--rounds", "40", "--start-online"],
            "gold_hunt_fulltext.log",
        )
    if not any(
        "gold_pdfku_library_full_gold" in (x.get("CommandLine") or "") for x in _ps_python()
    ):
        spawn(
            "scripts.gold_pdfku_library_full_gold",
            ["--hours", "90", "--workers", "8", "--rounds", "40", "--start-online"],
            "gold_pdfku_library_full_gold.log",
        )

    time.sleep(20)
    alive = []
    for row in _ps_python():
        c = row.get("CommandLine") or ""
        if "gold_" in c:
            alive.append(f"{row.get('ProcessId')} {c[:140]}")
    status = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "yes_gold_at_boot": y_gold,
        "pdfku_gold_at_boot": p_gold,
        "reopened_yes": y_open,
        "reopened_pdfku": p_open,
        "alive": alive,
        "note": "自动冲金标已启动；无OA/付费墙条目仍需人工PDF才能达金标",
    }
    (ROOT / "解析文件" / "gold_force_all_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
