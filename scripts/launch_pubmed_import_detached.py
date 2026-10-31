# -*- coding: utf-8 -*-
"""
独立后台启动 PubMed 原始入库：脱离 Cursor/终端，失败自动续传直到完成。

用法：
  python -m scripts.launch_pubmed_import_detached
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

LOG_DIR = ROOT / "logs"
PROGRESS_FILE = ROOT / "data" / "literature" / "pubmed_raw_import.json"
PY = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
if not Path(PY).is_file():
    PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).is_file():
    PY = sys.executable

# Windows: 脱离父进程/终端，关 CMD 不杀子进程
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _is_running() -> bool:
    try:
        out = subprocess.check_output(
            [
                "wmic",
                "process",
                "where",
                "Name='python.exe'",
                "get",
                "ProcessId,CommandLine",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for ln in out.splitlines():
            low = ln.lower()
            if "-m scripts.import_pubmed_raw" in low:
                return True
        return False
    except Exception:
        return False


def _progress() -> dict:
    if PROGRESS_FILE.is_file():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {}


def _launch(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "a", encoding="utf-8")
    log_f.write(f"\n=== detached launch {datetime.now().isoformat()} ===\n")
    log_f.flush()
    subprocess.Popen(
        [PY, "-m", "scripts.import_pubmed_raw_until_done"],
        cwd=str(ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=(
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB
        ),
    )


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"pubmed_import_{stamp}.log"

    prog = _progress()
    imported = int(prog.get("imported") or 0)
    total = int(prog.get("total") or 66960)
    status = prog.get("status", "")

    if status == "done" and imported >= total:
        print(f"PubMed 已完成：{imported:,}/{total:,}")
        return 0

    if _is_running():
        print("PubMed 导入进程已在运行，无需重复启动")
        print(f"  进度：{imported:,}/{total:,}")
        print(f"  进度文件：{PROGRESS_FILE}")
        return 0

    _launch(log_path)
    meta = {
        "started_at": datetime.now().isoformat(),
        "import_log": str(log_path),
        "progress_file": str(PROGRESS_FILE),
        "resume_from": imported,
        "total": total,
    }
    (LOG_DIR / "pubmed_import_job.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    time.sleep(2)
    print("PubMed 独立后台导入已启动（可关闭 Cursor）")
    print(f"  续传起点：{imported:,}/{total:,}")
    print(f"  日志：{log_path}")
    print(f"  进度：{PROGRESS_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
