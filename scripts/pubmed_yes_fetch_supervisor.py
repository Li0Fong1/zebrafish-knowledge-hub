# -*- coding: utf-8 -*-
"""
PubMed YES fetch 监管器：并发 worker、崩溃自动重启、完成后合并分片。

环境变量：
  PUBMED_YES_FETCH_WORKERS=4   # 并发 worker 数（默认 4，有 NCBI Key 时可 4–6）
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

from rag.network_resilience import probe_network, wait_for_network
from rag.pubmed_yes_corpus_io import count_unique_pmids, merge_shards_to_main

from rag.pubmed_yes_source import count_pubmed_yes_pmids

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
LOG = ROOT / "logs" / "pubmed_yes_fetch_supervisor.log"
LOCK = ROOT / "data" / "literature" / "pubmed_yes_fetch_supervisor.lock"
PROGRESS = ROOT / "data" / "literature" / "pubmed_yes_fetch.json"
WORKERS = max(1, min(int(os.getenv("PUBMED_YES_FETCH_WORKERS", "4")), 8))
WORKER_TAG = "fetch_pubmed_yes_corpus"
RESTART_WAIT = int(os.getenv("PUBMED_YES_FETCH_RESTART_SEC", "30"))
POLL = int(os.getenv("PUBMED_YES_FETCH_SUP_POLL_SEC", "60"))
CREATE_FLAGS = 0x08000000
_MUTEX_HANDLE = None
_MUTEX_NAME = "Local\\PubMedYesFetchSupervisorV1"


def _acquire_singleton_mutex() -> bool:
    """Windows 命名互斥，防止多个监管器并发启动。"""
    global _MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    import ctypes

    handle = ctypes.windll.kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
        return False
    _MUTEX_HANDLE = handle
    return True


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _total_yes() -> int:
    try:
        n = count_pubmed_yes_pmids()
        if n > 0:
            return n
    except Exception:
        pass
    if PROGRESS.is_file():
        try:
            n = int(json.loads(PROGRESS.read_text(encoding="utf-8")).get("total_yes") or 0)
            if n > 0:
                return n
        except Exception:
            pass
    return 46878


def _save_global_progress(*, status: str = "running") -> None:
    done = count_unique_pmids()
    total = _total_yes()
    st = {
        "total_yes": total,
        "status": status,
        "done_pmids_count": done,
        "pending": max(total - done, 0),
        "workers": WORKERS,
        "network_online": probe_network(),
        "last_heartbeat_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if PROGRESS.is_file():
        try:
            old = json.loads(PROGRESS.read_text(encoding="utf-8"))
            old.update(st)
            st = old
        except Exception:
            pass
    PROGRESS.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_complete() -> bool:
    total = _total_yes()
    done = count_unique_pmids()
    return total > 0 and done >= total


def _spawn_worker(worker_id: int) -> subprocess.Popen:
    log_path = ROOT / "logs" / f"pubmed_yes_fetch_w{worker_id:02d}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "a", encoding="utf-8")
    _log(f"拉起 worker {worker_id}/{WORKERS - 1} → {log_path.name}")
    cmd = [
        PY, "-u", "-m", "scripts.fetch_pubmed_yes_corpus",
        "--resume", "--worker-id", str(worker_id), "--workers", str(WORKERS),
    ]
    # 一期默认只要摘要；设 PUBMED_YES_SKIP_FULLTEXT=0 可拉 PMC 全文
    if os.getenv("PUBMED_YES_SKIP_FULLTEXT", "1") not in ("0", "false", "False"):
        cmd.append("--skip-fulltext")
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        creationflags=CREATE_FLAGS,
    )


def _acquire_lock() -> bool:
    my_pid = os.getpid()
    if LOCK.is_file():
        try:
            old = int(LOCK.read_text(encoding="utf-8").strip())
            if old != my_pid:
                r = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {old}", "/NH"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
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
    if not _acquire_singleton_mutex():
        _log("监管器已在运行（mutex），退出")
        return 0
    if not _acquire_lock():
        _log("监管器已在运行（lock），退出")
        return 0

    _log(f"=== PubMed YES fetch 监管器启动（{WORKERS} 并发 worker）===")
    procs: dict[int, subprocess.Popen] = {}
    try:
        while not _is_complete():
            if not probe_network():
                _log("断网，等待恢复…")
                wait_for_network(log_fn=_log, reason="fetch 监管器")

            for wid, proc in list(procs.items()):
                if proc.poll() is None:
                    continue
                code = proc.returncode
                _log(f"worker {wid} 退出 code={code}，将重启")
                del procs[wid]

            for wid in range(WORKERS):
                if wid not in procs:
                    procs[wid] = _spawn_worker(wid)

            done = count_unique_pmids()
            _save_global_progress()
            _log(f"进度 done={done:,}/{_total_yes():,} 活跃 worker={sum(1 for p in procs.values() if p.poll() is None)}/{WORKERS}")

            if _is_complete():
                break
            time.sleep(POLL)

        for proc in procs.values():
            if proc.poll() is None:
                proc.wait(timeout=300)

        n = merge_shards_to_main()
        _save_global_progress(status="completed")
        _log(f"fetch 全部完成，已合并语料 unique={n:,} → data/literature/pubmed_yes_corpus.jsonl")
        return 0
    finally:
        for proc in procs.values():
            if proc.poll() is None:
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/F"], capture_output=True)
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
