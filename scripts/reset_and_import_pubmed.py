# -*- coding: utf-8 -*-
"""
一键：检测 NCBI API → 清空全部数据 → PubMed 斑马鱼文献原样入库（后台任务入口）。

用法：
  python -m scripts.reset_and_import_pubmed
  python -m scripts.reset_and_import_pubmed --skip-wipe   # 仅续传导入
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print

LOG_DIR = ROOT / "logs"
PY = sys.executable


def _run(cmd: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n=== {datetime.now().isoformat()} ===\n")
        log.flush()
        proc = subprocess.run(cmd, cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)
    return proc.returncode


def test_ncbi() -> bool:
    from scripts.test_ncbi_key import _try_search

    ok0, msg0 = _try_search(use_key=False)
    safe_print(f"NCBI 无 Key：{'OK' if ok0 else 'FAIL'} — {msg0}")
    key = os.getenv("NCBI_API_KEY", "").strip()
    if key:
        ok1, msg1 = _try_search(use_key=True)
        safe_print(f"NCBI 有 Key：{'OK' if ok1 else 'FAIL'} — {msg1}")
        return ok1 or ok0
    return ok0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--skip-wipe", action="store_true")
    p.add_argument("--foreground", action="store_true", help="前台运行导入（默认写日志后台）")
    args = p.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    master_log = LOG_DIR / f"pubmed_reset_import_{stamp}.log"

    safe_print("【1/3】检测 NCBI API …")
    if not test_ncbi():
        safe_print("[失败] NCBI API 不可用，已中止")
        return 1

    if not args.skip_wipe:
        safe_print("【2/3】清空数据库与文献/RAG 本地数据 …")
        rc = _run([PY, "-m", "scripts.wipe_all_zebrafish_data", "--confirm", "WIPE"], master_log)
        if rc != 0:
            safe_print(f"[失败] 清空数据 exit={rc}，见 {master_log}")
            return rc
    else:
        safe_print("【2/3】跳过清空（--skip-wipe）")

    safe_print("【3/3】启动 PubMed 全量原始入库 …")
    import_cmd = [PY, "-m", "scripts.import_pubmed_raw_ncbi"]

    if args.foreground:
        rc = _run(import_cmd, master_log)
        safe_print(f"导入结束 exit={rc}，日志 {master_log}")
        return rc

    # 后台：Windows start /B
    import_log = LOG_DIR / f"pubmed_import_{stamp}.log"
    with open(import_log, "w", encoding="utf-8") as f:
        f.write(f"started {datetime.now().isoformat()}\n")
    subprocess.Popen(
        [PY, "-m", "scripts.import_pubmed_raw_ncbi"],
        cwd=str(ROOT),
        stdout=open(import_log, "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    meta = {
        "started_at": datetime.now().isoformat(),
        "master_log": str(master_log),
        "import_log": str(import_log),
        "progress_file": str(ROOT / "data" / "literature" / "pubmed_raw_import.json"),
    }
    (LOG_DIR / "pubmed_import_job.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    safe_print(f"后台导入已启动\n  日志：{import_log}\n  进度：{meta['progress_file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
