# -*- coding: utf-8 -*-
"""
过夜看门狗：保证中文补译 worker 单进程存活，跑完自动更新演示文档后退出。
可关 Cursor，pythonw 独立运行。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

LOG = ROOT / "logs" / "overnight_zh_backfill_watchdog.log"
LOCK = ROOT / "data" / "literature" / "overnight_zh_backfill_watchdog.lock"
PROGRESS = ROOT / "data" / "literature" / "pubmed_judgement_zh_backfill.json"
WORKER_LOG = ROOT / "logs" / "pubmed_judgement_zh_backfill_live.log"
PY = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
PY_WORKER = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).is_file():
    PY = PY_WORKER
POLL = int(__import__("os").getenv("ZH_BACKFILL_POLL_SEC", "90"))
WORKER_TAG = "scripts.backfill_pubmed_judgement_zh"
WORKER_BAT = "run_zh_backfill_worker.bat"
FLAGS = 0x00000008 | 0x00000200 | 0x08000000 | 0x01000000


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _wmic_rows() -> list[tuple[int, str]]:
    try:
        for exe in ("pythonw.exe", "python.exe"):
            out = subprocess.check_output(
                ["wmic", "process", "where", f"Name='{exe}'", "get", "ProcessId,CommandLine"],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            rows: list[tuple[int, str]] = []
            for ln in out.splitlines():
                parts = ln.strip().split()
                if len(parts) >= 2 and parts[-1].isdigit():
                    rows.append((int(parts[-1]), ln))
            if rows:
                return rows
    except Exception:
        pass
    return []


def _worker_pids() -> list[int]:
    return [
        pid
        for pid, ln in _wmic_rows()
        if WORKER_TAG in ln or WORKER_BAT in ln
    ]


def _kill_extras(pids: list[int], keep: int = 1) -> None:
    for pid in pids[keep:]:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        _log(f"终止重复 worker PID={pid}")


def _pending_count() -> int:
    from mysql_client import MySQLClient
    from sqlalchemy import text

    c = MySQLClient()
    db = next(c.get_db())
    try:
        return int(
            db.execute(
                text(
                    """
                    SELECT COUNT(*) FROM literature_pubmed_judgement
                    WHERE mode != 'meta'
                      AND (
                        (title_en IS NOT NULL AND CHAR_LENGTH(title_en) > 3
                         AND (title_zh IS NULL OR title_zh = title_en OR title_zh NOT REGEXP '[一-龥]'))
                        OR
                        (abstract_en IS NOT NULL AND CHAR_LENGTH(abstract_en) > 40
                         AND (abstract_zh IS NULL OR abstract_zh NOT REGEXP '[一-龥]'
                              OR abstract_zh IN ('No abstract','无摘要','')))
                      )
                    """
                )
            ).scalar()
            or 0
        )
    finally:
        db.close()


def _balance_halted() -> bool:
    if not PROGRESS.is_file():
        return False
    try:
        st = json.loads(PROGRESS.read_text(encoding="utf-8"))
        bal = st.get("balance")
        if isinstance(bal, dict) and bal.get("halted"):
            return True
        return st.get("status") == "balance_halted"
    except Exception:
        return False


def _spawn_worker() -> None:
    bat = ROOT / "logs" / WORKER_BAT
    subprocess.Popen(
        ["cmd", "/c", "start", "/B", str(bat)],
        cwd=str(ROOT),
        shell=False,
        creationflags=0x08000000,
    )
    _log("已拉起 worker")


def _finish_demo() -> None:
    try:
        subprocess.run(
            [PY, "-m", "scripts.export_pubmed_judgement_demo"],
            cwd=str(ROOT),
            capture_output=True,
            timeout=120,
        )
        _log("已更新 pubmed_judgement_demo.md")
    except Exception as e:
        _log(f"更新演示文档失败: {e}")


def main() -> int:
    my_pid = __import__("os").getpid()
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
                    return 0
        except ValueError:
            pass
    LOCK.write_text(str(my_pid), encoding="utf-8")
    _log("=== 中文补译过夜看门狗启动 ===")
    try:
        while True:
            try:
                pending = _pending_count()
                if pending == 0:
                    _log("全部中译完成，看门狗退出")
                    workers = _worker_pids()
                    if workers:
                        _kill_extras(workers, 0)
                    _finish_demo()
                    if PROGRESS.is_file():
                        st = json.loads(PROGRESS.read_text(encoding="utf-8"))
                        st.update({
                            "status": "completed",
                            "pending_remaining": 0,
                            "finished_at": datetime.now().isoformat(timespec="seconds"),
                        })
                        PROGRESS.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
                    return 0

                if _balance_halted():
                    _log(f"余额不足，暂停巡检 pending={pending:,}")
                    time.sleep(POLL)
                    continue

                workers = _worker_pids()
                if len(workers) > 1:
                    _kill_extras(workers, 1)
                    workers = workers[:1]
                if not workers:
                    _spawn_worker()

                done = 0
                if PROGRESS.is_file():
                    done = int(json.loads(PROGRESS.read_text(encoding="utf-8")).get("done_session") or 0)
                _log(f"巡检 OK pending={pending:,} worker={len(workers)} session_done={done:,}")
            except Exception as e:
                _log(f"巡检异常: {e}")
            time.sleep(POLL)
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
