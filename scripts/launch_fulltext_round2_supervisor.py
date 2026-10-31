# -*- coding: utf-8 -*-
"""
第二轮全文并行监管器：启动多个 worker 分片加速。

环境变量：
  FULLTEXT_ROUND2_WORKERS=4   # 默认 4；有 NCBI API Key 可试 6

用法：
  python -m scripts.launch_fulltext_round2_supervisor
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
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
LOG = ROOT / "logs" / "fulltext_round2_supervisor.log"
OUT_ROOT = ROOT / "zebrafish_package" / "结果" / "fulltext_round2"
WORKERS = max(1, min(int(os.getenv("FULLTEXT_ROUND2_WORKERS", "4")), 8))
CREATE_FLAGS = 0x08000000
LOCK = LOG.parent / "fulltext_round2_supervisor.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
    except OSError:
        pass
    return False


def _acquire_lock() -> bool:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.is_file():
        try:
            old_pid = int(LOCK.read_text(encoding="utf-8").strip())
            if _pid_alive(old_pid):
                _log(f"监管器已在运行 (pid={old_pid})，跳过重复启动")
                return False
        except (ValueError, OSError):
            pass
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_lock() -> None:
    try:
        if LOCK.is_file() and int(LOCK.read_text(encoding="utf-8").strip()) == os.getpid():
            LOCK.unlink(missing_ok=True)
    except (ValueError, OSError):
        pass


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)


def _worker_done(worker_id: int) -> bool:
    p = OUT_ROOT / f"progress_w{worker_id:02d}.json"
    if not p.is_file():
        return False
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("status") == "completed"
    except json.JSONDecodeError:
        return False


def _spawn(worker_id: int) -> subprocess.Popen:
    cmd = [
        PY,
        "-m",
        "scripts.yes_md_fulltext_round2",
        "--resume",
        "--worker-id",
        str(worker_id),
        "--workers",
        str(WORKERS),
        "--report-every",
        "500",
    ]
    log_out = OUT_ROOT / f"stdout_w{worker_id:02d}.log"
    log_err = OUT_ROOT / f"stderr_w{worker_id:02d}.log"
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=log_out.open("w", encoding="utf-8"),
        stderr=log_err.open("w", encoding="utf-8"),
        creationflags=CREATE_FLAGS,
    )


def main() -> int:
    if not _acquire_lock():
        return 1
    try:
        return _run_supervisor()
    finally:
        _release_lock()


def _run_supervisor() -> int:
    _log(f"启动 {WORKERS} 个 round2 worker")
    procs: dict[int, subprocess.Popen] = {}
    for wid in range(WORKERS):
        procs[wid] = _spawn(wid)
        time.sleep(2)

    while True:
        alive = 0
        for wid, proc in list(procs.items()):
            code = proc.poll()
            if code is None:
                alive += 1
                continue
            if _worker_done(wid):
                _log(f"worker {wid} 已完成 (exit={code})")
            else:
                _log(f"worker {wid} 退出 code={code}，5s 后重启")
                time.sleep(5)
                procs[wid] = _spawn(wid)
                alive += 1
        if alive == 0:
            break
        time.sleep(30)

    subprocess.run([PY, "-m", "scripts.yes_md_fulltext_round2", "--report-only", "--merge"], cwd=str(ROOT), check=False)
    _log("全部 worker 结束，已合并汇报")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
