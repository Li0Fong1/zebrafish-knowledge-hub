# -*- coding: utf-8 -*-
"""
过夜守护：保证 PubMed YES fetch 单监管器 + 4 worker，卡死自动重启。

与 fetch 监管器分离锁，避免互相干扰。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.network_resilience import probe_network
from rag.pubmed_yes_corpus_io import count_unique_pmids, merge_shards_to_main
from rag.pubmed_yes_source import count_pubmed_yes_pmids

LOG = ROOT / "logs" / "pubmed_yes_fetch_guard.log"
LOCK = ROOT / "data" / "literature" / "pubmed_yes_fetch_guard.lock"
SUP_LOCK = ROOT / "data" / "literature" / "pubmed_yes_fetch_supervisor.lock"
PROGRESS = ROOT / "data" / "literature" / "pubmed_yes_fetch.json"
REPORT = ROOT / "data" / "literature" / "pubmed_yes_fetch_completion_report.md"
PYW = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
WORKERS = int(os.getenv("PUBMED_YES_FETCH_WORKERS", "4"))
POLL = int(os.getenv("PUBMED_YES_FETCH_GUARD_SEC", "120"))
STALL_SEC = int(os.getenv("PUBMED_YES_FETCH_GUARD_STALL_SEC", "900"))
SUP_TAG = "pubmed_yes_fetch_supervisor"
WORKER_TAG = "fetch_pubmed_yes_corpus"
CREATE_FLAGS = 0x08000000


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        print(line, flush=True)


def _wmic_rows() -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for exe in ("python.exe", "pythonw.exe"):
        try:
            out = subprocess.check_output(
                ["wmic", "process", "where", f"Name='{exe}'", "get", "ProcessId,CommandLine"],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for ln in out.splitlines():
                parts = ln.strip().split()
                if len(parts) >= 2 and parts[-1].isdigit():
                    rows.append((int(parts[-1]), ln))
        except Exception:
            pass
    return rows


def _supervisor_pids() -> list[int]:
    return sorted(
        pid for pid, ln in _wmic_rows()
        if SUP_TAG in ln and "overnight_pubmed_yes_fetch_guard" not in ln
    )


def _worker_pids_by_slot() -> dict[int, list[int]]:
    by_slot: dict[int, list[int]] = {i: [] for i in range(WORKERS)}
    for pid, ln in _wmic_rows():
        if WORKER_TAG not in ln:
            continue
        slot = -1
        if "--worker-id" in ln:
            try:
                idx = ln.split("--worker-id")[1].strip().split()[0]
                slot = int(idx)
            except (ValueError, IndexError):
                pass
        if 0 <= slot < WORKERS:
            by_slot[slot].append(pid)
        else:
            by_slot.setdefault(-1, []).append(pid)
    return by_slot


def _kill(pid: int, reason: str = "") -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    _log(f"终止 PID={pid}" + (f" ({reason})" if reason else ""))


def _kill_all_fetch() -> None:
    for pid in _supervisor_pids():
        _kill(pid, "监管器")
    for pid, ln in _wmic_rows():
        if WORKER_TAG in ln:
            _kill(pid, "worker")
    SUP_LOCK.unlink(missing_ok=True)


def _dedupe() -> None:
    sups = _supervisor_pids()
    for pid in sups[:-1]:
        _kill(pid, "重复监管器")
    by_slot = _worker_pids_by_slot()
    for slot, pids in by_slot.items():
        for pid in sorted(pids)[:-1]:
            _kill(pid, f"重复 worker slot={slot}")
    extras = by_slot.get(-1, [])
    for pid in extras:
        _kill(pid, "无 slot worker")


def _start_supervisor() -> None:
    if _supervisor_pids():
        return
    SUP_LOCK.unlink(missing_ok=True)
    subprocess.Popen(
        [PYW, "-m", "scripts.pubmed_yes_fetch_supervisor"],
        cwd=str(ROOT),
        creationflags=CREATE_FLAGS,
    )
    _log("已拉起 fetch 监管器")


def _start_monitor() -> None:
    for pid, ln in _wmic_rows():
        if "monitor_pubmed_yes_fetch_complete" in ln:
            return
    subprocess.Popen(
        [PYW, "-m", "scripts.monitor_pubmed_yes_fetch_complete"],
        cwd=str(ROOT),
        creationflags=CREATE_FLAGS,
    )
    _log("已拉起完成监控")


def _is_complete() -> bool:
    try:
        total = count_pubmed_yes_pmids()
    except Exception:
        total = 46878
    if PROGRESS.is_file():
        try:
            total = int(json.loads(PROGRESS.read_text(encoding="utf-8")).get("total_yes") or total)
        except Exception:
            pass
    return count_unique_pmids() >= total


def _write_morning_status(done: int, total: int, note: str = "") -> None:
    path = ROOT / "data" / "literature" / "pubmed_yes_fetch_overnight_status.md"
    path.write_text(
        "\n".join([
            "# PubMed YES Fetch 过夜状态",
            "",
            f"更新时间：{datetime.now().isoformat(timespec='seconds')}",
            f"进度：**{done:,} / {total:,}** ({done / max(total, 1) * 100:.2f}%)",
            f"备注：{note or '运行中'}",
            "",
            "日志：",
            "- `logs/pubmed_yes_fetch_guard.log`",
            "- `logs/pubmed_yes_fetch_supervisor.log`",
            "- `logs/pubmed_yes_fetch_w00.log` ~ `w03.log`",
        ]),
        encoding="utf-8",
    )


def _acquire_guard_lock() -> bool:
    my_pid = os.getpid()
    for pid, ln in _wmic_rows():
        if pid == my_pid:
            continue
        if "overnight_pubmed_yes_fetch_guard" in ln:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            _log(f"终止重复守护 PID={pid}")
    time.sleep(1)
    if LOCK.is_file():
        try:
            old = int(LOCK.read_text(encoding="utf-8").strip())
            if old != my_pid:
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {old}", "/NH"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                )
                if str(old) in (r.stdout or ""):
                    return False
        except ValueError:
            pass
        LOCK.unlink(missing_ok=True)
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(my_pid).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def main() -> int:
    if not _acquire_guard_lock():
        return 0

    _log("=== 过夜守护启动 ===")
    _kill_all_fetch()
    time.sleep(3)
    _start_supervisor()
    time.sleep(12)
    _start_monitor()

    last_done = count_unique_pmids()
    last_progress_at = time.time()

    try:
        while True:
            try:
                if _is_complete():
                    n = merge_shards_to_main()
                    _log(f"fetch 完成 unique={n:,}，守护退出")
                    _write_morning_status(n, 46878, "已完成")
                    return 0

                _dedupe()
                sups = _supervisor_pids()
                if not sups:
                    _log("监管器缺失，重启")
                    _start_supervisor()

                by_slot = _worker_pids_by_slot()
                active_workers = sum(1 for s in range(WORKERS) if by_slot.get(s))
                done = count_unique_pmids()
                total = 46878
                if PROGRESS.is_file():
                    try:
                        total = int(json.loads(PROGRESS.read_text(encoding="utf-8")).get("total_yes") or total)
                    except Exception:
                        pass

                if done > last_done:
                    last_done = done
                    last_progress_at = time.time()

                network_paused = False
                if PROGRESS.is_file():
                    try:
                        network_paused = bool(
                            json.loads(PROGRESS.read_text(encoding="utf-8")).get("network_paused")
                        )
                    except Exception:
                        network_paused = False
                stalled = (
                    done < total
                    and time.time() - last_progress_at > STALL_SEC
                    and probe_network()
                    and not network_paused
                )
                if stalled:
                    _log(f">{STALL_SEC}s 无进展且网络正常 (done={done:,})，全量重启 fetch")
                    _kill_all_fetch()
                    time.sleep(3)
                    _start_supervisor()
                    last_progress_at = time.time()
                elif not probe_network():
                    _log(f"断网 done={done:,}，等待恢复（不重启 fetch）")

                _write_morning_status(
                    done, total,
                    f"监管器={len(sups)} worker_slots={active_workers}/{WORKERS}",
                )
                _log(
                    f"巡检 done={done:,}/{total:,} sup={len(sups)} "
                    f"workers={active_workers}/{WORKERS} stalled={stalled}"
                )
            except Exception as e:
                _log(f"巡检异常: {e}")
            time.sleep(POLL)
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
