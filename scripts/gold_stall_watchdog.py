# -*- coding: utf-8 -*-
"""
每 5 分钟检查金标战役是否卡住；卡住则自动修复并重启。

判定卡住：
  - 进程在跑，但 status 进度/金标数连续 5 分钟不变，或 status 心跳停更 ≥5 分钟
  - 或进程直接挂掉

修复动作：
  1) 把当前进度窗口附近条目写入黑名单（跳过卡死 PDF 抽取）
  2) 杀掉 hunt / pdfku（含可能挂死的抽取子进程）
  3) 重启 keep_alive（若未在）或直接拉起双战役
  4) 写 watchdog 日志

用法：
  python -u -m scripts.gold_stall_watchdog --hours 72
  python -u -m scripts.gold_stall_watchdog --once   # 只检查一次
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
YES = ROOT / "解析文件" / "yes_fulltext_gold"
PDFKU = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
WD_LOG = ROOT / "解析文件" / "gold_stall_watchdog.log"
WD_STATE = ROOT / "解析文件" / "gold_stall_watchdog_state.json"
PDFKU_STATUS = PDFKU / "status.json"
PDFKU_LOG = PDFKU / "campaign.log"
PDFKU_BL = PDFKU / "pdf_extract_blacklist.jsonl"
YES_STATUS = YES / "hunt_fulltext_status.json"
YES_RUN = ROOT / "解析文件" / "hunt_fulltext_run.log"
PDFKU_RUN = ROOT / "解析文件" / "pdfku_library_full_gold_run.log"
KEEP_LOG = ROOT / "解析文件" / "gold_keep_alive.log"

CHECK_EVERY_SEC = 2 * 60  # 用户要求不准卡：2 分钟一轮
DEFAULT_STALL_SEC = 2 * 60


def _pulse(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except OSError:
        pass
    WD_LOG.parent.mkdir(parents=True, exist_ok=True)
    with WD_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _ps() -> list[dict]:
    from scripts.win_hidden import python_procs

    return python_procs(include_pythonw=True)


def _running(substr: str) -> list[int]:
    pids = []
    for row in _ps():
        cmd = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if pid and substr in cmd:
            pids.append(pid)
    return pids


def _kill(*needles: str) -> int:
    n = 0
    for row in _ps():
        cmd = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if not pid or "gold_stall_watchdog" in cmd:
            continue
        if "gold_keep_alive" in cmd and "gold_stall_watchdog" not in needles:
            # 默认不杀 keep_alive，除非显式传入
            if "gold_keep_alive" not in needles:
                continue
        if any(x in cmd for x in needles):
            from scripts.win_hidden import kill_pid; kill_pid(pid)
            n += 1
    return n


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _snapshot() -> dict:
    pst = _read_json(PDFKU_STATUS)
    yst = _read_json(YES_STATUS)
    pcov = pst.get("coverage") or {}
    eq = yst.get("equal_coverage") or _read_json(YES / "pdf_gold_equal_status.json")
    now = time.time()
    return {
        "ts": now,
        "iso": datetime.now().isoformat(timespec="seconds"),
        "pdfku_on": bool(_running("gold_pdfku_library_full_gold")),
        "hunt_on": bool(_running("gold_hunt_fulltext")),
        "keep_on": bool(_running("gold_keep_alive")),
        "pdfku_progress": pst.get("progress"),
        "pdfku_need": pst.get("need"),
        "pdfku_mode": pst.get("mode"),
        "pdfku_gold": pcov.get("pdf_gold_ok"),
        "pdfku_status_mtime": PDFKU_STATUS.stat().st_mtime if PDFKU_STATUS.is_file() else 0,
        "pdfku_log_mtime": PDFKU_LOG.stat().st_mtime if PDFKU_LOG.is_file() else 0,
        "yes_gold": eq.get("pdf_gold_ok"),
        "yes_status_mtime": YES_STATUS.stat().st_mtime if YES_STATUS.is_file() else 0,
        "pdfku_stats": pst.get("stats") or {},
        "indexed": len(list(PDFKU.glob("batch_*/papers/*.json"))) if PDFKU.is_dir() else 0,
    }


def _load_state() -> dict:
    return _read_json(WD_STATE)


def _save_state(st: dict) -> None:
    WD_STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _blacklist_window(progress: int | None, need: int | None, n: int = 48) -> int:
    """根据当前 progress，把 targets 中接下来一段 entry 拉黑，躲开卡死窗口。"""
    try:
        sys.path.insert(0, str(ROOT))
        from scripts.gold_pdfku_library_full_gold import list_targets
    except Exception as exc:
        _pulse(f"blacklist import fail: {exc}")
        return 0
    targets = list_targets(pdf_only=False)
    # 与战役一致：跳过已金标后的 need 顺序很难复原，直接按 targets 偏移粗拉黑
    off = int(progress or 0)
    chunk = targets[max(0, off) : max(0, off) + n]
    if not chunk and targets:
        chunk = targets[:n]
    PDFKU.mkdir(parents=True, exist_ok=True)
    added = 0
    with PDFKU_BL.open("a", encoding="utf-8") as fh:
        for t in chunk:
            eno = str(t.get("entry_no") or "")
            if not eno:
                continue
            fh.write(
                json.dumps(
                    {
                        "entry_no": eno,
                        "reason": "stall_watchdog_window",
                        "ts": datetime.now().isoformat(timespec="seconds"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            added += 1
    return added


def _child_env() -> dict:
    import os

    env = os.environ.copy()
    prev = (env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = str(ROOT) if not prev else str(ROOT) + os.pathsep + prev
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["ZEBRAFISH_PROJECT_ROOT"] = str(ROOT)
    return env


def _start_keep_alive(hours: float, workers: int) -> None:
    KEEP_LOG.parent.mkdir(parents=True, exist_ok=True)
    fh = KEEP_LOG.open("a", encoding="utf-8")
    fh.write(f"\n===== stall_watchdog restart {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh.flush()
    subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "scripts.gold_keep_alive",
            "--hours",
            str(max(1.0, hours)),
            "--workers",
            str(workers),
            "--stall-sec",
            "300",
            "--no-shutdown",
        ],
        cwd=str(ROOT),
        env=_child_env(),
        stdout=fh,
        stderr=subprocess.STDOUT,
    )


def _start_campaigns(hours: float, workers: int) -> None:
    YES.mkdir(parents=True, exist_ok=True)
    PDFKU.mkdir(parents=True, exist_ok=True)
    with YES_RUN.open("a", encoding="utf-8") as fh:
        fh.write(f"\n===== watchdog hunt {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh_y = YES_RUN.open("a", encoding="utf-8")
    subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "scripts.gold_hunt_fulltext",
            "--hours",
            str(max(0.5, hours)),
            "--workers",
            str(workers),
            "--rounds",
            "12",
        ],
        cwd=str(ROOT),
        env=_child_env(),
        stdout=fh_y,
        stderr=subprocess.STDOUT,
    )
    with PDFKU_RUN.open("a", encoding="utf-8") as fh:
        fh.write(f"\n===== watchdog pdfku {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh_p = PDFKU_RUN.open("a", encoding="utf-8")
    subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-m",
            "scripts.gold_pdfku_library_full_gold",
            "--hours",
            str(max(0.5, hours)),
            "--workers",
            str(workers),
            "--rounds",
            "12",
            "--pdf-only",
        ],
        cwd=str(ROOT),
        env=_child_env(),
        stdout=fh_p,
        stderr=subprocess.STDOUT,
    )


def diagnose_and_fix(cur: dict, prev: dict, *, hours: float, workers: int) -> None:
    _pulse(
        f"STALL DETECTED pdfku_prog={prev.get('pdfku_progress')}→{cur.get('pdfku_progress')} "
        f"gold={prev.get('pdfku_gold')}→{cur.get('pdfku_gold')} "
        f"indexed={prev.get('indexed')}→{cur.get('indexed')} — FIX"
    )
    # 卡死时清黑名单，避免同一批永久跳过导致假进度
    try:
        yes_bl = YES / "_hunt_timeout_blacklist.json"
        if yes_bl.is_file():
            yes_bl.write_text("[]", encoding="utf-8")
        if PDFKU_BL.is_file():
            PDFKU_BL.write_text("", encoding="utf-8")
        _pulse("cleared hunt+pdfku blacklists on stall")
    except Exception as exc:  # noqa: BLE001
        _pulse(f"blacklist clear err: {exc}")

    added = _blacklist_window(cur.get("pdfku_progress"), cur.get("pdfku_need"), n=32)
    _pulse(f"blacklisted hang window n={added}")

    killed = 0
    killed += _kill("gold_pdfku_library_full_gold")
    killed += _kill("gold_hunt_fulltext")
    killed += _kill("spawn_main")
    _pulse(f"killed workers={killed}")
    time.sleep(3)

    if not _running("gold_keep_alive"):
        _pulse("keep_alive down — restart keep_alive")
        _start_keep_alive(hours, workers)
        time.sleep(8)
    else:
        time.sleep(8)
        if not _running("gold_pdfku_library_full_gold") or not _running("gold_hunt_fulltext"):
            _pulse("campaigns down — direct start local-first (--pdf-only)")
            _start_campaigns(hours, workers)

    time.sleep(5)
    _pulse(
        f"after fix keep={bool(_running('gold_keep_alive'))} "
        f"pdfku={bool(_running('gold_pdfku_library_full_gold'))} "
        f"hunt={bool(_running('gold_hunt_fulltext'))}"
    )


def is_stalled(cur: dict, prev: dict | None, stall_sec: float) -> tuple[bool, str]:
    now = cur["ts"]
    # 进程死了
    if not cur["pdfku_on"]:
        return True, "pdfku_process_down"
    if not cur["hunt_on"]:
        # hunt 可短暂停，但若 yes 也不更新则算
        if now - float(cur.get("yes_status_mtime") or 0) > stall_sec:
            return True, "hunt_down_and_silent"

    if not prev:
        return False, "no_prev"

    # 心跳停更
    if now - float(cur.get("pdfku_status_mtime") or 0) >= stall_sec:
        return True, "pdfku_status_freeze"

    # 进度与金标与入库数全不变
    same_prog = cur.get("pdfku_progress") == prev.get("pdfku_progress")
    same_gold = cur.get("pdfku_gold") == prev.get("pdfku_gold")
    same_idx = cur.get("indexed") == prev.get("indexed")
    elapsed = now - float(prev.get("ts") or now)
    if elapsed >= stall_sec - 5 and same_prog and same_gold and same_idx and cur["pdfku_on"]:
        # 若 need 已经是 0 则不算卡，是做完了
        if int(cur.get("pdfku_need") or 0) == 0:
            return False, "need_empty"
        return True, "progress_frozen"

    return False, "ok"


def tick(*, hours: float, workers: int, stall_sec: float) -> dict:
    cur = _snapshot()
    st = _load_state()
    prev = st.get("last")
    stalled, reason = is_stalled(cur, prev, stall_sec)
    _pulse(
        f"check pdfku={int(cur['pdfku_on'])} hunt={int(cur['hunt_on'])} "
        f"prog={cur.get('pdfku_progress')}/{cur.get('pdfku_need')} "
        f"gold={cur.get('pdfku_gold')} idx={cur.get('indexed')} "
        f"reason={reason}"
    )
    if stalled:
        diagnose_and_fix(cur, prev or {}, hours=hours, workers=workers)
        cur = _snapshot()
        st["last_fix_at"] = cur["iso"]
        st["last_fix_reason"] = reason
        st["fix_count"] = int(st.get("fix_count") or 0) + 1
    st["last"] = cur
    st["updated_at"] = cur["iso"]
    _save_state(st)
    return {"stalled": stalled, "reason": reason, "snap": cur}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--every-sec", type=float, default=CHECK_EVERY_SEC)
    ap.add_argument("--stall-sec", type=float, default=DEFAULT_STALL_SEC)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    stall_sec = float(args.stall_sec)
    end = time.time() + args.hours * 3600
    _pulse(
        f"stall_watchdog start every={args.every_sec}s stall={stall_sec}s "
        f"until={datetime.fromtimestamp(end).isoformat(timespec='seconds')}"
    )

    # 启动先拍一张基线
    st = _load_state()
    st["last"] = _snapshot()
    _save_state(st)

    if args.once:
        tick(hours=args.hours, workers=args.workers, stall_sec=stall_sec)
        return 0

    while time.time() < end:
        time.sleep(max(30.0, float(args.every_sec)))
        try:
            tick(
                hours=max(0.5, (end - time.time()) / 3600),
                workers=args.workers,
                stall_sec=stall_sec,
            )
        except Exception as exc:  # noqa: BLE001
            _pulse(f"tick err: {exc}")
    _pulse("watchdog deadline exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
