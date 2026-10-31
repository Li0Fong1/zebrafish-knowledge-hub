# -*- coding: utf-8 -*-
"""
夜间守护：唯一 PubMed 导入 + PubMed 达标后 1000 篇判别，失败自动重启。

用法（已 detach 启动后无需人工干预）：
  python -m scripts.overnight_literature_supervisor
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

from core.entry import bootstrap_app

bootstrap_app()

LOG = ROOT / "logs" / "overnight_supervisor.log"
LOCK = ROOT / "data" / "literature" / "overnight_supervisor.lock"
STATE = ROOT / "data" / "literature" / "overnight_state.json"
PROGRESS = ROOT / "data" / "literature" / "pubmed_raw_import.json"
LIT_DIR = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "test" / "literature_test_100"
JUDGE_DONE = LIT_DIR / "result" / "result_stats.json"
PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
PY = str(PYW if PYW.is_file() else ROOT / ".venv" / "Scripts" / "python.exe")

POLL = int(os.getenv("PIPELINE_POLL_SEC", "120"))
MIN_RATIO = float(os.getenv("PUBMED_MIN_COMPLETE_RATIO", "0.95"))
JUDGE_N = int(os.getenv("JUDGE_BATCH_LIMIT", "1000"))

DETACHED = 0x00000008 | 0x00000200 | 0x08000000 | 0x01000000


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    r = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return str(pid) in (r.stdout or "")


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _state() -> dict:
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {}


def _save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def _procs() -> list[str]:
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", "Name='pythonw.exe'", "get", "CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return [ln for ln in out.splitlines() if "pythonw" in ln.lower()]
    except Exception:
        return []


def _count_import_procs() -> int:
    return sum(1 for ln in _procs() if "import_pubmed_raw_until_done" in ln)


def _other_supervisor_pids() -> list[int]:
    my = os.getpid()
    out: list[int] = []
    try:
        rows = subprocess.check_output(
            ["wmic", "process", "where", "Name='pythonw.exe'", "get", "ProcessId,CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for ln in rows.splitlines():
            if "overnight_literature_supervisor" not in ln:
                continue
            parts = ln.strip().split()
            if parts and parts[-1].isdigit():
                pid = int(parts[-1])
                if pid != my:
                    out.append(pid)
    except Exception:
        pass
    return out


def _import_running() -> bool:
    return _count_import_procs() > 0


def _kill_duplicate_pipelines() -> None:
    """终止旧 background_pubmed_then_judge，避免与守护重复调度。"""
    try:
        rows = subprocess.check_output(
            ["wmic", "process", "where", "Name='pythonw.exe'", "get", "ProcessId,CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for ln in rows.splitlines():
            if "background_pubmed_then_judge" not in ln:
                continue
            parts = ln.strip().split()
            if parts and parts[-1].isdigit():
                pid = int(parts[-1])
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
                _log(f"终止旧流水线 PID={pid}")
    except Exception as e:
        _log(f"清理旧流水线跳过: {e}")


def _kill_duplicate_supervisors(keep: int = 1) -> None:
    """保留 keep 个守护进程（含本进程），其余终止。"""
    my = os.getpid()
    try:
        rows = subprocess.check_output(
            ["wmic", "process", "where", "Name='pythonw.exe'", "get", "ProcessId,CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        pids = []
        for ln in rows.splitlines():
            if "overnight_literature_supervisor" not in ln:
                continue
            parts = ln.strip().split()
            if parts and parts[-1].isdigit():
                pids.append(int(parts[-1]))
        # 优先保留本进程
        if my in pids:
            pids.remove(my)
            pids.insert(0, my)
        for pid in pids[keep:]:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            _log(f"终止重复守护进程 PID={pid}")
    except Exception as e:
        _log(f"清理重复守护跳过: {e}")


def _kill_duplicate_imports(keep: int = 1) -> None:
    """保留 keep 个导入进程，其余终止（避免 NCBI 限流/重复写入）。"""
    try:
        rows = subprocess.check_output(
            ["wmic", "process", "where", "Name='pythonw.exe'", "get", "ProcessId,CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        pids = []
        for ln in rows.splitlines():
            if "import_pubmed_raw_until_done" not in ln:
                continue
            parts = ln.strip().split()
            if parts and parts[-1].isdigit():
                pids.append(int(parts[-1]))
        for pid in pids[keep:]:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            _log(f"终止重复导入进程 PID={pid}")
    except Exception as e:
        _log(f"清理重复进程跳过: {e}")


def _db_pubmed() -> int:
    from mysql_client import MySQLClient
    from sqlalchemy import text

    client = MySQLClient()
    db = next(client.get_db())
    try:
        return int(db.execute(text("SELECT COUNT(*) FROM literature_pubmed")).scalar() or 0)
    finally:
        db.close()


def _pubmed_total() -> int:
    if PROGRESS.is_file():
        return int(json.loads(PROGRESS.read_text(encoding="utf-8")).get("total") or 67056)
    from rag.literature_collector import PUBMED_ZEBRAFISH_BROAD, pubmed_total_count

    try:
        return pubmed_total_count(PUBMED_ZEBRAFISH_BROAD)
    except Exception:
        return 67056


def _launch_import() -> None:
    if _import_running():
        return
    _log("启动 PubMed 导入 (import_pubmed_raw_until_done)")
    log_path = ROOT / "logs" / f"pubmed_import_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_f = open(log_path, "a", encoding="utf-8")
    subprocess.Popen(
        [str(PY), "-m", "scripts.import_pubmed_raw_until_done"],
        cwd=str(ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=DETACHED,
    )


def _run_judgement() -> None:
    _log(f"PubMed 达标，开始导出 {JUDGE_N} 篇并判别…")
    from scripts.export_literature_sample_from_db import export_sample
    from scripts.judge_literature_zebrafish_batch import run_batch

    n = export_sample(LIT_DIR, JUDGE_N)
    _log(f"样本导出 {n} 篇 → {LIT_DIR}")
    stats = run_batch(LIT_DIR, LIT_DIR / "result", limit=JUDGE_N, resume=True)
    _log(f"判别完成: {json.dumps(stats, ensure_ascii=False)}")
    st = _state()
    st["judgement_done"] = True
    st["judgement_stats"] = stats
    st["finished_at"] = datetime.now().isoformat(timespec="seconds")
    _save_state(st)


def main() -> int:
    if LOCK.is_file():
        try:
            old = int(LOCK.read_text(encoding="utf-8").strip())
            if old != os.getpid() and _pid_alive(old):
                _log(f"已有守护进程 PID={old}，退出")
                return 0
            LOCK.unlink(missing_ok=True)
        except ValueError:
            LOCK.unlink(missing_ok=True)

    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    _log("=== 夜间守护启动 ===")
    _kill_duplicate_pipelines()
    _kill_duplicate_supervisors(keep=1)
    _kill_duplicate_imports(keep=1)
    st = _state()

    try:
        while True:
            if _count_import_procs() > 1:
                _kill_duplicate_imports(keep=1)
            others = _other_supervisor_pids()
            if others:
                _kill_duplicate_supervisors(keep=1)

            total = _pubmed_total()
            db_n = _db_pubmed()
            pct = 100 * db_n / max(total, 1)
            prog = json.loads(PROGRESS.read_text(encoding="utf-8")) if PROGRESS.is_file() else {}
            year = prog.get("current_year", "?")
            imp = int(prog.get("imported") or db_n)

            _log(
                f"PubMed DB={db_n:,} progress={imp:,}/{total:,} ({pct:.1f}%) "
                f"year={year} import_running={_import_running()}"
            )

            if db_n < total * MIN_RATIO or db_n < total * 0.99:
                if not _import_running():
                    _launch_import()
            elif not _import_running() and db_n < total * 0.99:
                _launch_import()

            if db_n >= total * MIN_RATIO and not JUDGE_DONE.is_file() and not st.get("judgement_done"):
                try:
                    _run_judgement()
                    st = _state()
                except Exception as e:
                    _log(f"判别失败，下轮重试: {e}")

            if JUDGE_DONE.is_file() and db_n >= total * 0.99:
                _log("=== 全部任务完成（PubMed 100% + 判别） ===")
                break
            if JUDGE_DONE.is_file() and db_n >= total * MIN_RATIO and not _import_running():
                if db_n >= total * 0.99:
                    _log("=== 全部任务完成 ===")
                    break

            time.sleep(POLL)
    finally:
        LOCK.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
