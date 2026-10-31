# -*- coding: utf-8 -*-
"""
后台流水线：
  1. 检查/续传 PubMed 全量导入直至完成
  2. 校验 literature_pubmed 数量
  3. 导出 1000 篇样本并执行斑马鱼研究对象判别 → result/
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

from common_utils import safe_print
from rag.literature_collector import PUBMED_ZEBRAFISH_BROAD, pubmed_total_count

LOG_DIR = ROOT / "logs"
PROGRESS_FILE = ROOT / "data" / "literature" / "pubmed_raw_import.json"
LIT_DIR = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "test" / "literature_test_100"
JUDGE_LIMIT = int(__import__("os").getenv("JUDGE_BATCH_LIMIT", "1000"))
PY = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
if not Path(PY).is_file():
    PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).is_file():
    PY = sys.executable

POLL_SEC = int(__import__("os").getenv("PIPELINE_POLL_SEC", "120"))
MIN_RATIO = float(__import__("os").getenv("PUBMED_MIN_COMPLETE_RATIO", "0.95"))


def _log(msg: str, log_path: Path) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    safe_print(line)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _pubmed_running() -> bool:
    try:
        out = subprocess.check_output(
            [
                "wmic",
                "process",
                "where",
                "Name='pythonw.exe' or Name='python.exe'",
                "get",
                "CommandLine",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return "-m scripts.import_pubmed_raw" in out.lower()
    except Exception:
        return False


def _ensure_pubmed_import(log_path: Path) -> None:
    prog = {}
    if PROGRESS_FILE.is_file():
        prog = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    status = prog.get("status", "")
    imported = int(prog.get("imported") or 0)
    total = int(prog.get("total") or 66960)

    if status == "done" and imported >= total * MIN_RATIO:
        _log(f"PubMed 进度文件已标记完成 ({imported:,}/{total:,})", log_path)
        return

    if _pubmed_running():
        _log(f"PubMed 导入已在运行 ({imported:,}/{total:,})", log_path)
        return

    _log("启动 PubMed 续传导入…", log_path)
    subprocess.run([PY, "-m", "scripts.launch_pubmed_import_detached"], cwd=str(ROOT), check=False)


def _db_pubmed_count() -> int:
    from mysql_client import MySQLClient
    from sqlalchemy import text

    client = MySQLClient()
    db = next(client.get_db())
    try:
        return int(db.execute(text("SELECT COUNT(*) FROM literature_pubmed")).scalar() or 0)
    finally:
        db.close()


def _wait_pubmed_done(log_path: Path) -> bool:
    total = pubmed_total_count(PUBMED_ZEBRAFISH_BROAD)
    _log(f"NCBI 预计总量：{total:,}，等待导入完成（阈值 {MIN_RATIO:.0%}）…", log_path)

    while True:
        _dedupe_workers(log_path)
        _ensure_pubmed_import(log_path)
        db_n = _db_pubmed_count()
        prog = json.loads(PROGRESS_FILE.read_text(encoding="utf-8")) if PROGRESS_FILE.is_file() else {}
        imported = int(prog.get("imported") or db_n)
        status = prog.get("status", "")

        _log(f"PubMed 进度 DB={db_n:,} progress={imported:,}/{total:,} status={status}", log_path)

        if db_n >= total * MIN_RATIO or (status == "done" and db_n >= total * MIN_RATIO):
            _log(f"PubMed 导入达标：{db_n:,} / {total:,}", log_path)
            return True

        time.sleep(POLL_SEC)


def _verify_pubmed(log_path: Path) -> bool:
    total = pubmed_total_count(PUBMED_ZEBRAFISH_BROAD)
    db_n = _db_pubmed_count()
    ok = db_n >= total * MIN_RATIO
    _log(f"校验 PubMed：DB={db_n:,} NCBI={total:,} → {'通过' if ok else '未达标'}", log_path)
    if not ok:
        _log("校验未通过，将重新触发导入并继续等待…", log_path)
        if PROGRESS_FILE.is_file():
            prog = json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
            prog.pop("status", None)
            PROGRESS_FILE.write_text(json.dumps(prog, ensure_ascii=False, indent=2), encoding="utf-8")
        return False
    return True


def _dedupe_workers(log_path: Path) -> None:
    """仅保留 1 个流水线进程（导入由看门狗/launch 管理，此处不杀导入）。"""
    try:
        rows = subprocess.check_output(
            ["wmic", "process", "where", "Name='pythonw.exe'", "get", "ProcessId,CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        pip: list[int] = []
        me = __import__("os").getpid()
        for ln in rows.splitlines():
            parts = ln.strip().split()
            if not parts or not parts[-1].isdigit():
                continue
            pid = int(parts[-1])
            if "background_pubmed_then_judge" in ln and pid != me:
                pip.append(pid)
        for pid in pip:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            _log(f"终止重复流水线 PID={pid}", log_path)
    except Exception:
        pass


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"background_pipeline_{stamp}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log("=== 后台流水线启动 ===", log_path)

    while not _verify_pubmed(log_path):
        _wait_pubmed_done(log_path)

    _log(f"导出 {JUDGE_LIMIT} 篇样本到 {LIT_DIR}…", log_path)
    from scripts.export_literature_sample_from_db import export_sample

    n = export_sample(LIT_DIR, JUDGE_LIMIT)
    _log(f"样本就绪：{n} 篇", log_path)

    _log(f"开始文献判别（{JUDGE_LIMIT} 篇）…", log_path)
    from scripts.judge_literature_zebrafish_batch import run_batch

    stats = run_batch(LIT_DIR, LIT_DIR / "result", limit=JUDGE_LIMIT, resume=True)
    _log(f"判别完成：{json.dumps(stats, ensure_ascii=False)}", log_path)
    _log("=== 全部任务完成 ===", log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
