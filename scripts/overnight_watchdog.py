# -*- coding: utf-8 -*-
"""
过夜看门狗：每 2 分钟检查一次，保证「1 个导入 + 1 个流水线」存活，任务未完成则自动拉起。
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

LOG = ROOT / "logs" / "overnight_watchdog.log"
LOCK = ROOT / "data" / "literature" / "overnight_watchdog.lock"
PROGRESS = ROOT / "data" / "literature" / "pubmed_raw_import.json"
JUDGE_DONE = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "test" / "literature_test_100" / "result" / "result_stats.json"
PY = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
if not Path(PY).is_file():
    PY = str(ROOT / ".venv" / "Scripts" / "python.exe")

POLL = int(__import__("os").getenv("PIPELINE_POLL_SEC", "120"))
MIN_RATIO = float(__import__("os").getenv("PUBMED_MIN_COMPLETE_RATIO", "0.95"))


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _wmic_rows() -> list[tuple[int, str]]:
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", "Name='pythonw.exe'", "get", "ProcessId,CommandLine"],
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
        return rows
    except Exception:
        return []


def _pids(matcher: str) -> list[int]:
    return [pid for pid, ln in _wmic_rows() if matcher in ln]


def _kill_extras(pids: list[int], keep: int = 1) -> None:
    for pid in pids[keep:]:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        _log(f"终止重复进程 PID={pid}")


def _db_pubmed() -> int:
    from mysql_client import MySQLClient
    from sqlalchemy import text

    c = MySQLClient()
    db = next(c.get_db())
    try:
        return int(db.execute(text("SELECT COUNT(*) FROM literature_pubmed")).scalar() or 0)
    finally:
        db.close()


def _total() -> int:
    if PROGRESS.is_file():
        return int(json.loads(PROGRESS.read_text(encoding="utf-8")).get("total") or 67056)
    return 67056


def _need_import(db_n: int, total: int) -> bool:
    if JUDGE_DONE.is_file():
        return False
    return db_n < total * MIN_RATIO


def main() -> int:
    if LOCK.is_file():
        try:
            old = int(LOCK.read_text(encoding="utf-8").strip())
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {old}", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if str(old) != str(__import__("os").getpid()) and str(old) in (r.stdout or ""):
                return 0
        except ValueError:
            pass
    LOCK.write_text(str(__import__("os").getpid()), encoding="utf-8")
    _log("=== 过夜看门狗启动 ===")
    try:
        while True:
            try:
                if JUDGE_DONE.is_file():
                    imp = _pids("import_pubmed_raw")
                    pip = _pids("background_pubmed_then_judge")
                    if imp:
                        _kill_extras(imp, 0)
                    if pip:
                        _kill_extras(pip, 0)
                    _log("判别已完成，看门狗退出")
                    return 0

                total = _total()
                db_n = _db_pubmed()
                imp = _pids("import_pubmed_raw")
                pip = _pids("background_pubmed_then_judge")

                if len(imp) > 1:
                    _kill_extras(imp, 1)
                    imp = imp[:1]
                if len(pip) > 1:
                    _kill_extras(pip, 1)
                    pip = pip[:1]

                if _need_import(db_n, total) and not imp:
                    _log(f"导入已停止，自动拉起 (DB={db_n:,}/{total:,})")
                    subprocess.run([PY, "-m", "scripts.launch_pubmed_import_detached"], cwd=str(ROOT))

                if not pip:
                    _log("流水线已停止，自动拉起")
                    subprocess.run([PY, "-m", "scripts.launch_background_pipeline"], cwd=str(ROOT))

                _log(
                    f"巡检 OK  DB={db_n:,}/{total:,} ({100*db_n/max(total,1):.1f}%) "
                    f"import={len(imp)} pipeline={len(pip)}"
                )
            except Exception as e:
                _log(f"巡检异常: {e}")

            time.sleep(POLL)
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
