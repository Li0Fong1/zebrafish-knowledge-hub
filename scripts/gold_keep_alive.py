# -*- coding: utf-8 -*-
"""
双战役保活：检测到「是」表 hunt 或孤儿完整金标进程停掉/卡死 → 自动重启，不耽误进度。

用法：
  python -u -m scripts.gold_keep_alive --hours 72 --workers 8
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YES = ROOT / "解析文件" / "yes_fulltext_gold"
# pdf库全量完整金标（取代旧 orphan 战役）
PDFKU = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
KEEP_LOG = ROOT / "解析文件" / "gold_keep_alive.log"
YES_STATUS = YES / "hunt_fulltext_status.json"
YES_LIVE = YES / "hunt_progress_live.log"
YES_RUN = ROOT / "解析文件" / "hunt_fulltext_run.log"
PDFKU_STATUS = PDFKU / "status.json"
PDFKU_LOG = PDFKU / "campaign.log"
PDFKU_RUN = ROOT / "解析文件" / "pdfku_library_full_gold_run.log"

STALL_SEC = 120  # 不准卡：2 分钟无进展就回收重启
RESCUE_EVERY_SEC = 2 * 3600  # 夜间定期挽救坏 tier / 重抽 XML-HTML
DONE_CHECK_EVERY_SEC = 10 * 60  # 每 10 分钟检测是否自动收工
DONE_CONFIRM_NEEDED = 1  # 一次确认即可关机（用户要求做完自关）
SHUTDOWN_DELAY_SEC = 180  # 3 分钟倒计时，可 shutdown /a 取消


def _pulse(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except OSError:
        pass
    KEEP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with KEEP_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _ps_json() -> list[dict]:
    from scripts.win_hidden import python_procs

    # Keep-alive itself is often pythonw; exclude=False would never see hunts.
    return python_procs(include_pythonw=True)


def _running(substr: str, *, exclude: str = "") -> bool:
    for row in _ps_json():
        cmd = row.get("CommandLine") or ""
        if substr in cmd and (not exclude or exclude not in cmd):
            return True
    return False


def _kill_match(*needles: str, exclude: str = "gold_keep_alive") -> int:
    killed = 0
    for row in _ps_json():
        cmd = row.get("CommandLine") or ""
        pid = int(row.get("ProcessId") or 0)
        if not pid or not cmd:
            continue
        if exclude and exclude in cmd:
            continue
        if "gold_keep_alive" in cmd:
            continue
        if any(n in cmd for n in needles):
            try:
                from scripts.win_hidden import kill_pid

                if kill_pid(pid):
                    killed += 1
            except Exception:
                pass
    return killed


def _mtime(*paths: Path) -> float:
    times = [p.stat().st_mtime for p in paths if p.is_file()]
    return max(times) if times else 0.0


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def _start_hunt(hours: float, workers: int) -> None:
    YES.mkdir(parents=True, exist_ok=True)
    YES_RUN.parent.mkdir(parents=True, exist_ok=True)
    with YES_RUN.open("a", encoding="utf-8") as fh:
        fh.write(f"\n===== keep_alive hunt {datetime.now().isoformat(timespec='seconds')} =====\n")
    cmd = [
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
        "--start-online",
    ]
    fh = YES_RUN.open("a", encoding="utf-8")
    _detach_popen(cmd, fh)


def _start_pdfku(hours: float, workers: int) -> None:
    PDFKU.mkdir(parents=True, exist_ok=True)
    PDFKU_RUN.parent.mkdir(parents=True, exist_ok=True)
    with PDFKU_RUN.open("a", encoding="utf-8") as fh:
        fh.write(f"\n===== keep_alive pdfku_library {datetime.now().isoformat(timespec='seconds')} =====\n")
    # Step1：本地 pdf库优先抽取，避免 --start-online 跳过 local
    cmd = [
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
    ]
    fh = PDFKU_RUN.open("a", encoding="utf-8")
    _detach_popen(cmd, fh)


_OPEN_FDS: list = []


def _child_env() -> dict:
    """Ensure spawned campaigns see project root on PYTHONPATH."""
    env = os.environ.copy()
    prev = (env.get("PYTHONPATH") or "").strip()
    # Root only — do not prepend core/ (shadows root config/bootstrap shims)
    env["PYTHONPATH"] = str(ROOT) if not prev else str(ROOT) + os.pathsep + prev
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["ZEBRAFISH_PROJECT_ROOT"] = str(ROOT)
    return env


def _detach_popen(cmd: list[str], log_fh) -> None:
    """Spawn child; retain handles so stdout is not closed after return."""
    from scripts.win_hidden import creationflags

    _OPEN_FDS.append(log_fh)
    p = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=_child_env(),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags(),
        close_fds=False,
    )
    _OPEN_FDS.append(p)


def _count_match(substr: str, *, exclude: str = "") -> int:
    n = 0
    for row in _ps_json():
        cmd = row.get("CommandLine") or ""
        if substr in cmd and (not exclude or exclude not in cmd):
            n += 1
    return n


def _run_rescue() -> None:
    """夜间自动挽救：坏 tier / XML-HTML 重抽入金标。"""
    try:
        _pulse("rescue pass start")
        subprocess.run(
            [sys.executable, "-u", "-m", "scripts.gold_rescue_bad_tier"],
            cwd=str(ROOT),
            timeout=45 * 60,
            capture_output=True,
        )
        _pulse("rescue pass done")
    except Exception as exc:  # noqa: BLE001
        _pulse(f"rescue pass err: {exc}")


def _check_auto_done() -> dict:
    try:
        from scripts.gold_shutdown_when_done import full_check

        return full_check()
    except Exception as exc:  # noqa: BLE001
        _pulse(f"auto_done check err: {exc}")
        return {"all_auto_done": False, "error": str(exc)}


def _shutdown_rest(delay: int = SHUTDOWN_DELAY_SEC) -> None:
    """已禁用：用户要求永不自动关机。仅记日志，不停机、不杀进程。"""
    flag = ROOT / "解析文件" / "AUTO_GOLD_DONE_READY_SHUTDOWN.txt"
    flag.write_text(
        f"自动金标本地收工 {datetime.now().isoformat(timespec='seconds')}\n"
        f"自动关机已永久禁用（不会执行 shutdown /s）。delay={delay} 已忽略。\n",
        encoding="utf-8",
    )
    _pulse("AUTO GOLD DONE — auto Windows shutdown DISABLED; keep running")


def _start_watchdog(hours: float, workers: int = 2) -> None:
    if _running("gold_stall_watchdog"):
        return
    WD_LOG = ROOT / "解析文件" / "gold_stall_watchdog.log"
    WD_LOG.parent.mkdir(parents=True, exist_ok=True)
    # Honor keep_alive workers (quiet=2). Never hardcode workers=8.
    w = max(1, min(int(workers), 8))
    fh = WD_LOG.open("a", encoding="utf-8")
    fh.write(f"\n===== keep_alive spawn watchdog {datetime.now().isoformat(timespec='seconds')} =====\n")
    fh.flush()
    _detach_popen(
        [
            sys.executable,
            "-u",
            "-m",
            "scripts.gold_stall_watchdog",
            "--hours",
            str(max(1.0, hours)),
            "--every-sec",
            "300",
            "--stall-sec",
            "300",
            "--workers",
            str(w),
        ],
        fh,
    )
    _pulse(f"spawned gold_stall_watchdog workers={w} every=300s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=72.0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--stall-sec", type=float, default=STALL_SEC)
    ap.add_argument(
        "--no-shutdown",
        action="store_true",
        default=True,
        help="已默认开启：永不自动关机（保留参数兼容旧命令）",
    )
    ap.add_argument(
        "--allow-shutdown",
        action="store_true",
        help="已废弃：自动关机功能已删除，此开关无效",
    )
    args = ap.parse_args()
    # 强制永不关机（用户明确要求删除自动关机）
    args.no_shutdown = True

    end = time.time() + args.hours * 3600
    _pulse(
        f"keep_alive until {datetime.fromtimestamp(end).isoformat(timespec='seconds')} "
        f"workers={args.workers} stall={args.stall_sec}s pdfku_library=1 "
        f"rescue_every={RESCUE_EVERY_SEC}s shutdown_when_done=False(DISABLED)"
    )

    n = _kill_match("gold_hunt_watchdog")
    if n:
        _pulse(f"retired old hunt_watchdog x{n}")
    k = _kill_match("gold_pdfku_orphan_full_gold", "gold_pdfku_orphan_xml_campaign")
    if k:
        _pulse(f"retired old orphan campaigns x{k}")
    while _count_match("gold_hunt_fulltext", exclude="gold_hunt_watchdog") > 1:
        _pulse("dedupe: kill extra hunt")
        _kill_match("gold_hunt_fulltext")
        time.sleep(2)

    # 确保卡死看门狗在跑（workers 与 keep_alive 一致）
    _start_watchdog(args.hours, args.workers)

    hunt_grace = 0.0
    pdfku_grace = 0.0
    last_pulse = 0.0
    last_rescue = 0.0
    last_done_check = 0.0
    done_streak = 0
    last_wd_ensure = 0.0

    while time.time() < end:
        left_h = max(0.5, (end - time.time()) / 3600)
        now = time.time()

        hunt_n = _count_match("gold_hunt_fulltext", exclude="gold_hunt_watchdog")
        hunt_on = hunt_n >= 1
        pdfku_on = _running("gold_pdfku_library_full_gold")

        if hunt_n > 1 and now >= hunt_grace:
            _pulse(f"YES hunt DUPLICATE x{hunt_n} — trim to 1")
            _kill_match("gold_hunt_fulltext")
            time.sleep(2)
            _start_hunt(left_h, args.workers)
            hunt_grace = now + 180
            time.sleep(3)
            continue

        # 收工检测：用 status 心跳；campaign.log 停更但 status 仍活着不算 STALL
        pdfku_mt = _mtime(PDFKU_STATUS)
        if not pdfku_mt:
            pdfku_mt = _mtime(PDFKU_LOG)
        pdfku_age = now - pdfku_mt if pdfku_mt else 9999
        hunt_mt = _mtime(YES_STATUS, YES_LIVE, YES / "hunt_fulltext.log")
        hunt_age = now - hunt_mt if hunt_mt else 9999

        yes_st = _read_json(YES_STATUS)
        pdfku_st = _read_json(PDFKU_STATUS)
        eq = yes_st.get("equal_coverage") or _read_json(YES / "pdf_gold_equal_status.json")
        pcov = pdfku_st.get("coverage") or {}

        if now - last_pulse >= 90:
            _pulse(
                f"yes hunt={hunt_n} age={hunt_age:.0f}s "
                f"pdf_gold={eq.get('pdf_gold_ok')}/{eq.get('exist_unique')} ({eq.get('pct_pdf_gold')}%) "
                f"| pdfku={int(pdfku_on)} age={pdfku_age:.0f}s "
                f"pdf_gold={pcov.get('pdf_gold_ok')} short={pcov.get('pdf_align_short')} "
                f"mode={pdfku_st.get('mode')} prog={pdfku_st.get('progress')}/{pdfku_st.get('need')} "
                f"done_streak={done_streak} wd={int(_running('gold_stall_watchdog'))}"
            )
            last_pulse = now

        # 每 10 分钟确保 watchdog 仍在
        if now - last_wd_ensure >= 600:
            last_wd_ensure = now
            _start_watchdog(left_h, args.workers)

        if last_rescue == 0:
            last_rescue = now
        elif now - last_rescue >= RESCUE_EVERY_SEC:
            last_rescue = now
            _run_rescue()

        # 收工检测：本地能做的都做完 → 连续确认 → 关机休息
        if last_done_check == 0:
            last_done_check = now
        elif now - last_done_check >= DONE_CHECK_EVERY_SEC:
            last_done_check = now
            rep = _check_auto_done()
            (ROOT / "解析文件" / "auto_done_shutdown_status.json").write_text(
                json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if rep.get("all_auto_done"):
                done_streak += 1
                y = rep.get("yes") or {}
                p = rep.get("pdfku") or {}
                _pulse(
                    f"auto_done YES streak={done_streak}/{DONE_CONFIRM_NEEDED} "
                    f"yes_gold={y.get('gold')}/{y.get('total')} "
                    f"pdfku_gold={p.get('gold')}/{p.get('total')} "
                    f"need_human={y.get('need_human')}+{p.get('need_human')}"
                )
                if done_streak >= DONE_CONFIRM_NEEDED:
                    _shutdown_rest(SHUTDOWN_DELAY_SEC)
                    _pulse(
                        "auto done — shutdown disabled; keep supervising "
                        "(3h audit / campaigns stay up)"
                    )
                    done_streak = 0  # 避免反复刷屏；继续挂机监督
            else:
                if done_streak:
                    _pulse(
                        f"auto_done NO — reset streak "
                        f"(yes_pending={(rep.get('yes') or {}).get('pending_local_auto')} "
                        f"pdfku_pending={(rep.get('pdfku') or {}).get('pending_local_auto')})"
                    )
                done_streak = 0

        if not hunt_on and now >= hunt_grace:
            _pulse(f"YES hunt DOWN — restart (age={hunt_age:.0f}s)")
            _kill_match("gold_hunt_fulltext", "spawn_main")
            time.sleep(1)
            _start_hunt(left_h, args.workers)
            hunt_grace = now + 180
            time.sleep(5)
        elif hunt_on and now >= hunt_grace and hunt_age > args.stall_sec:
            _pulse(f"YES hunt STALL age={hunt_age:.0f}s — recycle")
            _kill_match("gold_hunt_fulltext")
            time.sleep(2)
            _kill_match("spawn_main")
            time.sleep(1)
            _start_hunt(left_h, args.workers)
            hunt_grace = now + 180

        if not pdfku_on and now >= pdfku_grace:
            _pulse(f"PDFKU library DOWN — restart (age={pdfku_age:.0f}s)")
            _kill_match(
                "gold_pdfku_library_full_gold",
                "gold_pdfku_orphan_full_gold",
                "gold_pdfku_orphan_xml_campaign",
            )
            time.sleep(1)
            _start_pdfku(left_h, args.workers)
            pdfku_grace = now + 180
            time.sleep(5)
        elif pdfku_on and now >= pdfku_grace and pdfku_age > args.stall_sec:
            _pulse(f"PDFKU STALL age={pdfku_age:.0f}s — recycle")
            _kill_match("gold_pdfku_library_full_gold")
            time.sleep(2)
            _start_pdfku(left_h, args.workers)
            pdfku_grace = now + 180

        time.sleep(20)

    _pulse("deadline — leave workers; keep_alive exit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
