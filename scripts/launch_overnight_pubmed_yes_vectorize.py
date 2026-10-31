# -*- coding: utf-8 -*-
"""detach 启动 PubMed YES 语料+向量化（可关 Cursor，不耗 DeepSeek token）。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PY).is_file():
    PY = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
LOG = ROOT / "logs" / "pubmed_yes_vectorize_live.log"
JOB = ROOT / "logs" / "pubmed_yes_vectorize_job.json"
TAG = "vectorize_pubmed_judgement_yes"
FLAGS = 0x00000008 | 0x00000200 | 0x08000000 | 0x01000000


def _running() -> list[int]:
    pids: list[int] = []
    try:
        out = subprocess.check_output(
            ["wmic", "process", "get", "ProcessId,CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in out.splitlines():
            if TAG not in line:
                continue
            parts = line.strip().split()
            if parts and parts[-1].isdigit():
                pids.append(int(parts[-1]))
    except Exception:
        pass
    return pids


def main() -> int:
    force = "--force" in sys.argv or "-f" in sys.argv
    pids = _running()
    if pids and not force:
        print(f"PubMed YES 向量化已在运行（PID: {', '.join(map(str, pids))}）")
        print("  重启: python -m scripts.launch_overnight_pubmed_yes_vectorize --force")
        return 0
    if force:
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(2)

    sys.path.insert(0, str(ROOT))
    from rag.pubmed_yes_source import count_pubmed_yes_pmids

    bat = ROOT / "logs" / "run_pubmed_yes_vectorize_worker.bat"
    subprocess.Popen(
        ["cmd", "/c", "start", "/B", str(bat)],
        cwd=str(ROOT),
        shell=False,
        creationflags=0x08000000,
    )
    JOB.write_text(
        json.dumps({"started_at": datetime.now().isoformat(), "log": str(LOG)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    time.sleep(5)
    left = _running()
    if len(left) > 1:
        keep = max(left)
        for pid in left:
            if pid != keep:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        left = [keep]
    print("PubMed YES 向量化已启动（PubMed 判别「是」+ 已 fetch 语料，本地 BGE，不耗 DeepSeek token）")
    print(f"  目标: PubMed 判别「是」 {count_pubmed_yes_pmids():,} 篇")
    print(f"  日志: {LOG}")
    print(f"  语料: data/literature/pubmed_yes_corpus.jsonl")
    print(f"  索引: %LOCALAPPDATA%\\ZebrafishGeneQA\\faiss_pubmed_yes\\")
    if left:
        print(f"  PID: {left[0]}")
    print("  可安全关闭 Cursor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
