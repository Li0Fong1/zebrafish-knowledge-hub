# -*- coding: utf-8 -*-
"""
金标：测试 → 不过则修复 → 再测，循环直到无可自动提升或全员金标。

不做：降门槛、垫文、Windows 关机。
做：rescue / 清黑名单 / 重开 unreachable / 确保战役在跑 / 全库 is_pdf_gold 验收。

用法：
  python -u -m scripts.gold_test_fix_loop --once
  python -u -m scripts.gold_test_fix_loop --hours 72 --every-sec 1800
"""
from __future__ import annotations

import argparse
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
OUT = ROOT / "解析文件"
LOG = OUT / "gold_test_fix_loop.log"
REPORT = OUT / "gold_test_fix_latest.json"
HIST = OUT / "gold_test_fix_history.jsonl"
HUNT_BL = YES / "_hunt_timeout_blacklist.json"
PDFKU_BL = PDFKU / "pdf_extract_blacklist.jsonl"


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _ps() -> list[dict]:
    cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", cmd], text=True, errors="replace"
        , creationflags=0x08000000 if __import__('sys').platform == 'win32' else 0).strip()
    except Exception:
        return []
    if not out:
        return []
    data = json.loads(out)
    return [data] if isinstance(data, dict) else list(data or [])


def _alive() -> dict[str, bool]:
    text = " ".join((r.get("CommandLine") or "") for r in _ps())
    return {
        "keep_alive": "gold_keep_alive" in text,
        "hunt": "gold_hunt_fulltext" in text,
        "pdfku": "gold_pdfku_library_full_gold" in text,
        "watchdog": "gold_stall_watchdog" in text,
        "rescue": "gold_rescue_bad_tier" in text,
        "test_fix": "gold_test_fix_loop" in text and "--once" not in text,
    }


def _spawn(mod: str, args: list[str], logname: str) -> None:
    log = OUT / logname
    fh = log.open("a", encoding="utf-8")
    fh.write(f"\n===== test_fix {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh.flush()
    subprocess.Popen(
        [sys.executable, "-u", "-m", mod, *args],
        cwd=str(ROOT),
        stdout=fh,
        stderr=subprocess.STDOUT,
    )
    _log(f"spawn {mod} {' '.join(args)}")


def ensure_full_speed() -> list[str]:
    actions: list[str] = []
    a = _alive()
    if not a.get("watchdog"):
        _spawn(
            "scripts.gold_stall_watchdog",
            ["--hours", "168", "--every-sec", "120", "--stall-sec", "120", "--workers", "8"],
            "gold_stall_watchdog.log",
        )
        actions.append("start_watchdog")
    if not a.get("keep_alive"):
        _spawn(
            "scripts.gold_keep_alive",
            ["--hours", "168", "--workers", "8", "--stall-sec", "120", "--no-shutdown"],
            "gold_keep_alive.log",
        )
        actions.append("start_keep_alive")
        time.sleep(10)
    a2 = _alive()
    if not a2.get("hunt"):
        _spawn(
            "scripts.gold_hunt_fulltext",
            ["--hours", "160", "--workers", "8", "--rounds", "60", "--start-online"],
            "gold_hunt_fulltext.log",
        )
        actions.append("start_hunt")
    if not a2.get("pdfku"):
        _spawn(
            "scripts.gold_pdfku_library_full_gold",
            ["--hours", "160", "--workers", "8", "--rounds", "60", "--start-online"],
            "gold_pdfku_library_full_gold.log",
        )
        actions.append("start_pdfku")
    return actions


def count_gold(root: Path) -> tuple[int, int, list[str]]:
    gold = notg = 0
    fails: list[str] = []
    for p in root.glob("batch_*/papers/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            notg += 1
            if len(fails) < 20:
                fails.append(p.stem)
            continue
        if is_pdf_gold(rec):
            gold += 1
        else:
            notg += 1
            if len(fails) < 20:
                fails.append(p.stem)
    return gold, notg, fails


def test_all() -> dict:
    yg, yn, yf = count_gold(YES)
    pg, pn, pf = count_gold(PDFKU)
    total_g, total_n = yg + pg, yn + pn
    passed = total_n == 0
    return {
        "passed": passed,
        "yes_gold": yg,
        "yes_not": yn,
        "pdfku_gold": pg,
        "pdfku_not": pn,
        "total_gold": total_g,
        "total_not": total_n,
        "fail_sample_yes": yf,
        "fail_sample_pdfku": pf,
        "alive": _alive(),
    }


def reopen_non_gold(root: Path, limit: int = 8000) -> int:
    n = 0
    for p in root.glob("batch_*/papers/*.json"):
        if n >= limit:
            break
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if is_pdf_gold(rec):
            continue
        dirty = False
        if rec.get("gold_tier") in (
            "unreachable_oa",
            "structural_exhausted_oa",
            "fulltext_step",
            "pdf_align_short",
            "fulltext_short",
            "extract_empty",
            "retry_for_gold",
        ):
            rec["gold_tier"] = "retry_for_gold"
            dirty = True
        m = dict(rec.get("metrics") or {})
        if m.get("oa_unreachable"):
            m.pop("oa_unreachable", None)
            rec["metrics"] = m
            dirty = True
        if dirty:
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            n += 1
    return n


def fix_gaps() -> dict:
    actions: list[str] = []
    if HUNT_BL.is_file():
        HUNT_BL.write_text("[]", encoding="utf-8")
        actions.append("clear_hunt_blacklist")
    if PDFKU_BL.is_file():
        PDFKU_BL.write_text("", encoding="utf-8")
        actions.append("clear_pdfku_blacklist")
    y = reopen_non_gold(YES)
    p = reopen_non_gold(PDFKU)
    actions.append(f"reopen yes={y} pdfku={p}")
    # rescue（后台，可能很长）
    if not _alive().get("rescue"):
        _spawn("scripts.gold_rescue_bad_tier", [], "gold_rescue_force.log")
        actions.append("start_rescue")
    actions.extend(ensure_full_speed())
    # 顺带跑一次 3h audit 补洞逻辑
    try:
        subprocess.run(
            [sys.executable, "-u", "-m", "scripts.gold_3h_audit", "--once"],
            cwd=str(ROOT),
            timeout=180,
            capture_output=True,
        )
        actions.append("audit_once")
    except Exception as exc:  # noqa: BLE001
        actions.append(f"audit_err:{exc}")
    return {"actions": actions}


def cycle_once(prev_gold: int | None = None) -> dict:
    _log("=== TEST ===")
    t0 = test_all()
    _log(
        f"test gold={t0['total_gold']} not={t0['total_not']} "
        f"yes={t0['yes_gold']}/{t0['yes_gold']+t0['yes_not']} "
        f"pdfku={t0['pdfku_gold']}/{t0['pdfku_gold']+t0['pdfku_not']} "
        f"pass={t0['passed']}"
    )
    fix = None
    t1 = t0
    if not t0["passed"]:
        _log("=== FIX (test failed) ===")
        fix = fix_gaps()
        _log(f"fix actions={fix.get('actions')}")
        time.sleep(5)
        _log("=== RETEST ===")
        t1 = test_all()
        _log(
            f"retest gold={t1['total_gold']} not={t1['total_not']} "
            f"delta={t1['total_gold'] - t0['total_gold']} pass={t1['passed']}"
        )
    report = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "before": t0,
        "fix": fix,
        "after": t1,
        "gold_delta": t1["total_gold"] - t0["total_gold"],
        "all_gold": t1["passed"],
        "prev_cycle_gold": prev_gold,
        "note": (
            "全员金标"
            if t1["passed"]
            else "未全员金标：持续战役+修复；付费墙/无全文条目需人工 PDF"
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with HIST.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "at": report["updated_at"],
                    "gold": t1["total_gold"],
                    "not": t1["total_not"],
                    "delta": report["gold_delta"],
                    "passed": t1["passed"],
                    "actions": (fix or {}).get("actions"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--every-sec", type=float, default=1800.0)
    ap.add_argument(
        "--stop-when-plateau",
        type=int,
        default=3,
        help="连续 N 轮 gold 无增长且仍有缺口则记 plateau（不关机，继续监督）",
    )
    args = ap.parse_args()

    ensure_full_speed()
    time.sleep(3)

    if args.once:
        rep = cycle_once()
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep.get("all_gold") else 1

    end = time.time() + max(1.0, args.hours) * 3600
    _log(f"test-fix loop until {datetime.fromtimestamp(end).isoformat(timespec='seconds')}")
    plateau = 0
    prev = None
    while time.time() < end:
        rep = cycle_once(prev_gold=prev)
        cur = rep["after"]["total_gold"]
        if rep.get("all_gold"):
            _log("ALL GOLD — loop keeps supervising but no fix needed")
            plateau = 0
        else:
            if prev is not None and cur <= prev:
                plateau += 1
            else:
                plateau = 0
            if plateau >= args.stop_when_plateau:
                _log(
                    f"PLATEAU x{plateau}: gold stuck at {cur}, "
                    f"remaining={rep['after']['total_not']} need human PDF; keep campaigns"
                )
                # 仍继续确保战役在跑，但不空转加重修复频率
                ensure_full_speed()
        prev = cur
        time.sleep(max(60.0, args.every_sec))
    _log("test-fix loop end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
