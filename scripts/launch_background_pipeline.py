# -*- coding: utf-8 -*-
"""独立后台启动：PubMed 导入 → 校验 → 1000 篇判别。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
PY = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
if not Path(PY).is_file():
    PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).is_file():
    PY = sys.executable

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _pipeline_running() -> bool:
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", "Name='pythonw.exe'", "get", "CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return "background_pubmed_then_judge" in out
    except Exception:
        return False


def main() -> int:
    if _pipeline_running():
        print("后台流水线已在运行，无需重复启动")
        job = LOG_DIR / "background_pipeline_job.json"
        if job.is_file():
            print(f"  见 {job}")
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"background_pipeline_{stamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "a", encoding="utf-8")
    log_f.write(f"detached start {datetime.now().isoformat()}\n")
    log_f.flush()

    subprocess.Popen(
        [PY, "-m", "scripts.background_pubmed_then_judge"],
        cwd=str(ROOT),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=(
            DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB
        ),
    )

    meta = {
        "started_at": datetime.now().isoformat(),
        "log": str(log_path),
        "pubmed_progress": str(ROOT / "data" / "literature" / "pubmed_raw_import.json"),
        "judgement_output": str(ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "test" / "literature_test_100" / "result"),
    }
    (LOG_DIR / "background_pipeline_job.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    time.sleep(1)
    print("后台流水线已启动（可关闭 Cursor）")
    print(f"  日志：{log_path}")
    print(f"  PubMed 进度：{meta['pubmed_progress']}")
    print(f"  判别输出：{meta['judgement_output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
