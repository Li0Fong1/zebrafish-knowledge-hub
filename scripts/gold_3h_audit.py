# -*- coding: utf-8 -*-
"""
每 3 小时核查金标进度；若推进后仍有未金标，查原因并补漏洞。

动作：
  1) 统计是表/pdf库金标数与缺口分桶（无本地/短PDF/仅XMLHTML/坏tier）
  2) 对比上轮快照：无新增金标或缺口几乎不动 → 触发补洞
  3) 补洞：rescue 长正文、重开 unreachable、清卡死黑名单、确保战役在跑
  4) 写 解析文件/gold_3h_audit_latest.json + 历史快照

用法：
  python -u -m scripts.gold_3h_audit --once
  python -u -m scripts.gold_3h_audit --hours 96 --every-sec 10800
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gold_hunt_fulltext import PDF_GOLD_BODY, is_pdf_gold  # noqa: E402
from scripts.gold_upgrade_5680 import PDFKU_HTML, PDFKU_PDF, PDFKU_XML  # noqa: E402

YES = ROOT / "解析文件" / "yes_fulltext_gold"
PDFKU = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
EXIST = ROOT / "解析文件" / "已存在表.csv"
OUT = ROOT / "解析文件"
LATEST = OUT / "gold_3h_audit_latest.json"
HIST = OUT / "gold_3h_audit_history.jsonl"
STATE = OUT / "gold_3h_audit_state.json"
LOG = OUT / "gold_3h_audit.log"
HUNT_BL = YES / "_hunt_timeout_blacklist.json"
PDFKU_BL = PDFKU / "pdf_extract_blacklist.jsonl"

EVERY_SEC = 3 * 60 * 60


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _ps() -> list[dict]:
    from scripts.win_hidden import python_procs

    return python_procs(include_pythonw=False)


def _alive_map() -> dict[str, bool]:
    text = " ".join((r.get("CommandLine") or "") for r in _ps())
    return {
        "keep_alive": "gold_keep_alive" in text,
        "hunt": "gold_hunt_fulltext" in text,
        "pdfku": "gold_pdfku_library_full_gold" in text,
        "watchdog": "gold_stall_watchdog" in text,
        "rescue": "gold_rescue_bad_tier" in text,
        "audit": "gold_3h_audit" in text,
    }


def _index_enos(base: Path, pattern: str = "*") -> set[str]:
    import re

    out: set[str] = set()
    if not base.is_dir():
        return out
    for f in base.glob(pattern):
        if not f.is_file():
            continue
        m = re.match(r"^(\d+)", f.stem)
        if m:
            out.add(m.group(1))
    return out


def _scan_corpus(root: Path) -> dict:
    gold = 0
    not_gold = 0
    buckets = Counter()
    long_not_gold = 0
    samples: list[dict] = []
    for p in root.glob("batch_*/papers/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            buckets["bad_json"] += 1
            not_gold += 1
            continue
        if is_pdf_gold(rec):
            gold += 1
            continue
        not_gold += 1
        body = len(rec.get("body_text") or "")
        src = (rec.get("source") or "").strip()
        tier = (rec.get("gold_tier") or "").strip()
        if body >= PDF_GOLD_BODY:
            long_not_gold += 1
            buckets["long_body_not_gold"] += 1
        elif tier in ("unreachable_oa", "retry_for_gold") or (rec.get("metrics") or {}).get(
            "oa_unreachable"
        ):
            buckets["marked_unreachable_or_retry"] += 1
        elif src.startswith("pdf"):
            buckets["pdf_short"] += 1
        elif src.startswith("xml"):
            buckets["xml_short"] += 1
        elif src.startswith("html"):
            buckets["html_short"] += 1
        else:
            buckets["other_short"] += 1
        if len(samples) < 12:
            samples.append(
                {
                    "entry_no": p.stem,
                    "body": body,
                    "source": src,
                    "tier": tier,
                    "reason": "long_finalize" if body >= PDF_GOLD_BODY else "need_better_fulltext",
                }
            )
    return {
        "gold": gold,
        "not_gold": not_gold,
        "long_not_gold": long_not_gold,
        "buckets": dict(buckets),
        "samples": samples,
    }


def snapshot() -> dict:
    pdf_e = _index_enos(PDFKU_PDF, "*.pdf")
    xml_e = _index_enos(PDFKU_XML, "*.xml")
    html_e = _index_enos(PDFKU_HTML, "*")
    yes = _scan_corpus(YES)
    pdfku = _scan_corpus(PDFKU)
    # 无 JSON 但有本地文件（漏处理）
    yes_enos = {p.stem for p in YES.glob("batch_*/papers/*.json")}
    import csv

    exist: set[str] = set()
    if EXIST.is_file():
        with EXIST.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                e = str(r.get("entry_no") or "").strip()
                if e:
                    exist.add(e)
    yes_pending_local = sorted(
        e for e in exist if e not in yes_enos and (e in pdf_e or e in xml_e or e in html_e)
    )
    pdfku_enos = {p.stem for p in PDFKU.glob("batch_*/papers/*.json")}
    local_all = pdf_e | xml_e | html_e
    pdfku_pending_local = sorted(e for e in local_all if e not in pdfku_enos)[:50]
    alive = _alive_map()
    return {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "floor": PDF_GOLD_BODY,
        "yes": yes,
        "pdfku": pdfku,
        "yes_pending_local_count": len(yes_pending_local),
        "yes_pending_local_sample": yes_pending_local[:15],
        "pdfku_pending_local_count": len([e for e in local_all if e not in pdfku_enos]),
        "pdfku_pending_local_sample": pdfku_pending_local[:15],
        "alive": alive,
        "total_gold": yes["gold"] + pdfku["gold"],
        "total_not_gold": yes["not_gold"] + pdfku["not_gold"],
    }


def _spawn(mod: str, args: list[str], logname: str) -> None:
    log = OUT / logname
    fh = log.open("a", encoding="utf-8")
    fh.write(f"\n===== 3h_audit spawn {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh.flush()
    subprocess.Popen(
        [sys.executable, "-u", "-m", mod, *args],
        cwd=str(ROOT),
        stdout=fh,
        stderr=subprocess.STDOUT,
    )
    _log(f"spawn {mod} {' '.join(args)}")


def ensure_campaigns() -> list[str]:
    actions: list[str] = []
    alive = _alive_map()
    if not alive.get("watchdog"):
        _spawn(
            "scripts.gold_stall_watchdog",
            ["--hours", "96", "--every-sec", "300", "--stall-sec", "300", "--workers", "8"],
            "gold_stall_watchdog.log",
        )
        actions.append("start_watchdog")
    if not alive.get("keep_alive"):
        _spawn(
            "scripts.gold_keep_alive",
            ["--hours", "96", "--workers", "8", "--stall-sec", "300", "--no-shutdown"],
            "gold_keep_alive.log",
        )
        actions.append("start_keep_alive")
        time.sleep(8)
    alive2 = _alive_map()
    if not alive2.get("hunt"):
        _spawn(
            "scripts.gold_hunt_fulltext",
            ["--hours", "90", "--workers", "8", "--rounds", "40", "--start-online"],
            "gold_hunt_fulltext.log",
        )
        actions.append("start_hunt")
    if not alive2.get("pdfku"):
        _spawn(
            "scripts.gold_pdfku_library_full_gold",
            ["--hours", "90", "--workers", "8", "--rounds", "40", "--start-online"],
            "gold_pdfku_library_full_gold.log",
        )
        actions.append("start_pdfku")
    if not alive2.get("rescue") and not alive.get("rescue"):
        _spawn("scripts.gold_rescue_bad_tier", [], "gold_rescue_force.log")
        actions.append("start_rescue")
    return actions


def reopen_unreachable(root: Path, limit: int = 5000) -> int:
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
        if rec.get("gold_tier") in ("unreachable_oa", "structural_exhausted_oa", "fulltext_step"):
            rec["gold_tier"] = "retry_for_gold"
            dirty = True
        metrics = dict(rec.get("metrics") or {})
        if metrics.get("oa_unreachable"):
            metrics.pop("oa_unreachable", None)
            rec["metrics"] = metrics
            dirty = True
        if dirty:
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            n += 1
    return n


def clear_blacklists() -> None:
    if HUNT_BL.is_file():
        HUNT_BL.write_text("[]", encoding="utf-8")
    if PDFKU_BL.is_file():
        PDFKU_BL.write_text("", encoding="utf-8")


def patch_gaps(snap: dict, prev: dict | None) -> dict:
    """若仍有未金标：查原因并补洞。"""
    actions: list[str] = []
    reasons: list[str] = []

    yes_ng = snap["yes"]["not_gold"]
    pdfku_ng = snap["pdfku"]["not_gold"]
    pending = snap["yes_pending_local_count"] + snap["pdfku_pending_local_count"]
    long_bad = snap["yes"]["long_not_gold"] + snap["pdfku"]["long_not_gold"]

    gold_delta = 0
    if prev:
        gold_delta = snap["total_gold"] - int(prev.get("total_gold") or 0)

    if yes_ng + pdfku_ng + pending == 0:
        return {
            "need_patch": False,
            "actions": [],
            "reasons": ["all_gold_or_no_json_gap"],
            "gold_delta": gold_delta,
        }

    # 原因归类
    if long_bad:
        reasons.append(f"long_body_not_gold={long_bad} → rescue/finalize")
    if pending:
        reasons.append(f"pending_local_no_json={pending} → restart campaigns")
    if snap["yes"]["buckets"].get("pdf_short") or snap["pdfku"]["buckets"].get("pdf_short"):
        reasons.append("pdf_short → need better PDF/OCR or online OA")
    if snap["yes"]["buckets"].get("xml_short") or snap["pdfku"]["buckets"].get("xml_short"):
        reasons.append("xml_short → need PDF download")
    if snap["yes"]["buckets"].get("html_short") or snap["pdfku"]["buckets"].get("html_short"):
        reasons.append("html_short → need PDF download")
    if snap["yes"]["buckets"].get("marked_unreachable_or_retry") or snap["pdfku"]["buckets"].get(
        "marked_unreachable_or_retry"
    ):
        reasons.append("unreachable marks → reopen for online retry")

    stalled = prev is not None and gold_delta <= 0
    if stalled:
        reasons.append(f"gold_delta={gold_delta} stalled → aggressive patch")

    # 补洞动作
    if long_bad or stalled:
        if not _alive_map().get("rescue"):
            _spawn("scripts.gold_rescue_bad_tier", [], "gold_rescue_force.log")
            actions.append("rescue")
    need_reopen = bool(
        snap["yes"]["buckets"].get("marked_unreachable_or_retry")
        or snap["pdfku"]["buckets"].get("marked_unreachable_or_retry")
        or stalled
        or pending
    )
    if need_reopen:
        clear_blacklists()
        actions.append("clear_blacklist")
        y = reopen_unreachable(YES)
        p = reopen_unreachable(PDFKU)
        actions.append(f"reopen_unreachable yes={y} pdfku={p}")
    actions.extend(ensure_campaigns())

    return {
        "need_patch": True,
        "actions": actions,
        "reasons": reasons,
        "gold_delta": gold_delta,
        "stalled": stalled,
    }


def run_once() -> dict:
    prev = None
    if LATEST.is_file():
        try:
            prev = json.loads(LATEST.read_text(encoding="utf-8")).get("snapshot")
        except Exception:
            prev = None
    snap = snapshot()
    patch = patch_gaps(snap, prev)
    report = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot": snap,
        "patch": patch,
        "prev_total_gold": (prev or {}).get("total_gold"),
    }
    LATEST.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with HIST.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "at": report["updated_at"],
                    "total_gold": snap["total_gold"],
                    "total_not_gold": snap["total_not_gold"],
                    "yes_gold": snap["yes"]["gold"],
                    "pdfku_gold": snap["pdfku"]["gold"],
                    "gold_delta": patch.get("gold_delta"),
                    "actions": patch.get("actions"),
                    "reasons": patch.get("reasons"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    STATE.write_text(
        json.dumps(
            {"last_run": report["updated_at"], "every_sec": EVERY_SEC},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _log(
        f"gold={snap['total_gold']} not={snap['total_not_gold']} "
        f"delta={patch.get('gold_delta')} actions={patch.get('actions')} "
        f"reasons={patch.get('reasons')}"
    )
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--hours", type=float, default=168.0)
    ap.add_argument("--every-sec", type=float, default=EVERY_SEC)
    args = ap.parse_args()

    if args.once:
        rep = run_once()
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    end = time.time() + max(1.0, args.hours) * 3600
    _log(f"3h audit loop until {datetime.fromtimestamp(end).isoformat(timespec='seconds')}")
    # 启动先跑一轮
    run_once()
    while time.time() < end:
        time.sleep(max(60.0, args.every_sec))
        if time.time() >= end:
            break
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001
            _log(f"audit err: {exc}")
    _log("3h audit loop end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
