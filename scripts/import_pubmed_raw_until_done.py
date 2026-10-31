# -*- coding: utf-8 -*-
"""PubMed 导入守护：失败自动等待并重试，直到全部完成。"""
from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from scripts.import_pubmed_raw_ncbi import PROGRESS_FILE, import_all

MAX_ROUNDS = int(__import__("os").getenv("PUBMED_IMPORT_MAX_ROUNDS", "999"))
RETRY_WAIT = float(__import__("os").getenv("PUBMED_IMPORT_RETRY_WAIT", "30"))


def main() -> int:
    for round_no in range(1, MAX_ROUNDS + 1):
        try:
            safe_print(f"\n[守护] 第 {round_no} 轮导入开始 {datetime.now().isoformat(timespec='seconds')}")
            stats = import_all(resume=True)
            safe_print(
                f"\n[守护完成] 入库 {stats['imported']:,} / {stats['total']:,} 篇 "
                f"job_id={stats['job_id']}"
            )
            return 0
        except KeyboardInterrupt:
            safe_print("[守护] 收到中断，退出")
            return 130
        except Exception as e:
            safe_print(f"[守护] 第 {round_no} 轮失败: {e}")
            traceback.print_exc()
            if round_no >= MAX_ROUNDS:
                safe_print(f"[守护] 已达最大重试轮数 {MAX_ROUNDS}，退出")
                return 1
            safe_print(f"[守护] {RETRY_WAIT:.0f}s 后自动续传… 进度见 {PROGRESS_FILE}")
            time.sleep(RETRY_WAIT)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
