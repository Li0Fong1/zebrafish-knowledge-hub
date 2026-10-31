# -*- coding: utf-8 -*-
"""
自动金标战役收尾检测（自动关机已永久禁用）。

完成定义（非 100% 金标，因付费墙无法自动）：
  - 本地 PDF/XML/HTML 能冲击的都已处理完（无「有本地全文却未落盘/正文极短」漏网）
  - 检测为 auto_done 时仅写状态；不再执行 Windows shutdown /s

接入 keep_alive；也可单独：
  python -u -m scripts.gold_shutdown_when_done --check-only
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gold_hunt_fulltext import is_pdf_gold  # noqa: E402
from scripts.gold_upgrade_5680 import PDFKU_HTML, PDFKU_PDF, PDFKU_XML  # noqa: E402

YES = ROOT / "解析文件" / "yes_fulltext_gold"
PDFKU = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
EXIST = ROOT / "解析文件" / "已存在表.csv"
STATUS = ROOT / "解析文件" / "auto_done_shutdown_status.json"
DONE_FLAG = ROOT / "解析文件" / "AUTO_GOLD_DONE_READY_SHUTDOWN.txt"

SHUTDOWN_DELAY_SEC = 300  # 5 分钟后关机，可 shutdown /a 取消


def _index_enos(base: Path, pattern: str = "*") -> set[str]:
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


def build_local_sets() -> tuple[set[str], set[str], set[str]]:
    return (
        _index_enos(PDFKU_PDF, "*.pdf"),
        _index_enos(PDFKU_XML, "*.xml"),
        _index_enos(PDFKU_HTML, "*"),
    )


def corpus_auto_done(
    out: Path,
    enos: set[str],
    pdf_enos: set[str],
    xml_enos: set[str],
    html_enos: set[str],
) -> dict:
    papers = {p.stem: p for p in out.glob("batch_*/papers/*.json")}
    gold = 0
    pending: list[str] = []
    need_human = 0
    for eno in enos:
        has_local = eno in pdf_enos or eno in xml_enos or eno in html_enos
        p = papers.get(eno)
        if not p:
            # 有本地全文却从未落盘 → 自动战役没收尾
            if has_local:
                pending.append(eno)
            else:
                need_human += 1
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pending.append(eno)
            continue
        if is_pdf_gold(rec):
            gold += 1
            continue
        # 已有 JSON = 已尝试过（金标/短稿/空抽）→ 剩余只能人工重下
        need_human += 1
    return {
        "total": len(enos),
        "gold": gold,
        "need_human": need_human,
        "pending_local_auto": len(pending),
        "pending_sample": pending[:15],
        "auto_done": len(pending) == 0,
        "pct_gold": round(100.0 * gold / len(enos), 2) if enos else 0.0,
    }


def full_check() -> dict:
    exist: set[str] = set()
    if EXIST.is_file():
        with EXIST.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                eno = str(r.get("entry_no") or "").strip()
                if eno:
                    exist.add(eno)
    pdf_e, xml_e, html_e = build_local_sets()
    yes = corpus_auto_done(YES, exist, pdf_e, xml_e, html_e)
    pdfku_enos = set(pdf_e) | set(xml_e) | set(html_e)
    pdfku_enos |= {p.stem for p in PDFKU.glob("batch_*/papers/*.json")}
    pdfku = corpus_auto_done(PDFKU, pdfku_enos, pdf_e, xml_e, html_e)
    return {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "yes": yes,
        "pdfku": pdfku,
        "all_auto_done": bool(yes["auto_done"] and pdfku["auto_done"]),
    }


def schedule_shutdown(delay: int = SHUTDOWN_DELAY_SEC) -> None:
    """已禁用：不再调用 Windows shutdown /s。"""
    DONE_FLAG.write_text(
        f"自动金标收工于 {datetime.now().isoformat(timespec='seconds')}\n"
        f"自动关机已永久禁用，不会执行 shutdown /s（delay={delay} 忽略）。\n",
        encoding="utf-8",
    )
    print("schedule_shutdown: DISABLED (no Windows shutdown)", flush=True)


def stop_campaigns() -> None:
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps], text=True, errors="replace"
        , creationflags=0x08000000 if __import__('sys').platform == 'win32' else 0).strip()
        data = json.loads(out) if out else []
        if isinstance(data, dict):
            data = [data]
    except Exception:
        data = []
    needles = ("gold_hunt_fulltext", "gold_pdfku_library", "gold_rescue_bad_tier")
    for row in data or []:
        cmd = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if not pid:
            continue
        if "gold_keep_alive" in cmd:
            continue
        if any(n in cmd for n in needles):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--shutdown-now", action="store_true", help="立刻预约关机（测试）")
    ap.add_argument("--delay", type=int, default=SHUTDOWN_DELAY_SEC)
    args = ap.parse_args()

    if args.shutdown_now:
        schedule_shutdown(args.delay)
        print("shutdown DISABLED — refused to schedule Windows shutdown", args.delay)
        return 0

    rep = full_check()
    STATUS.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if args.check_only:
        return 0 if rep["all_auto_done"] else 1

    if rep["all_auto_done"]:
        # 仅停战役进程，绝不关机
        stop_campaigns()
        schedule_shutdown(args.delay)
        print("AUTO DONE → campaigns stopped; Windows shutdown DISABLED")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
