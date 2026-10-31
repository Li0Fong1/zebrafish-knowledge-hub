# -*- coding: utf-8 -*-
"""
8 小时后台战役守护进程：持续跑测评轮次，记录最优分，定期重导出。
用法: python scripts/pdf_align_campaign_daemon.py --hours 8
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pdf_align_perfection_campaign import run_round  # noqa: E402

CAMPAIGN = ROOT / "解析文件" / "pdf_plaintext_samples" / "perfection_campaign"
DAEMON_LOG = CAMPAIGN / "daemon.jsonl"
BEST = CAMPAIGN / "best_score.json"
HEARTBEAT = CAMPAIGN / "heartbeat.txt"


def export_and_summary() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_5_pdf_full_align.py")],
        cwd=str(ROOT),
        check=False,
    )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "llm_repair_align_loop.py")],
        cwd=str(ROOT),
        check=False,
    )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_full_plaintext_summary.py")],
        cwd=str(ROOT),
        check=False,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--sleep", type=float, default=45.0, help="seconds between rounds")
    ap.add_argument("--export-every", type=int, default=20, help="export every N rounds")
    args = ap.parse_args()

    CAMPAIGN.mkdir(parents=True, exist_ok=True)
    end = datetime.now(timezone.utc) + timedelta(hours=args.hours)
    best = None
    n = 0
    print(f"DAEMON start until {end.isoformat()} sleep={args.sleep}s", flush=True)

    while datetime.now(timezone.utc) < end:
        n += 1
        try:
            summary = run_round(f"daemon_{n}_{int(time.time())}")
        except Exception as e:
            summary = {"error": str(e), "avg_score": -1, "round_id": f"err_{n}"}
            print("ROUND ERROR", e, flush=True)

        HEARTBEAT.write_text(
            f"{datetime.now(timezone.utc).isoformat()} round={n} "
            f"score={summary.get('avg_score')} body={summary.get('avg_body_recall')}\n",
            encoding="utf-8",
        )
        with DAEMON_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"n": n, **{k: summary.get(k) for k in (
                "round_id", "avg_score", "avg_body_recall", "total_micro_checks",
                "total_micro_fails", "all_order_ok", "elapsed_s", "error",
            ) if k in summary or k == "error"}}, ensure_ascii=False) + "\n")

        if best is None or float(summary.get("avg_score") or -1) >= float(best.get("avg_score") or -1):
            best = summary
            BEST.write_text(json.dumps(best, ensure_ascii=False, indent=2), encoding="utf-8")

        print(
            f"[{n}] score={summary.get('avg_score')} "
            f"body={summary.get('avg_body_recall')} "
            f"fails={summary.get('total_micro_fails')} "
            f"best={best.get('avg_score') if best else None}",
            flush=True,
        )

        if n % max(1, args.export_every) == 0:
            print("export cycle...", flush=True)
            try:
                export_and_summary()
            except Exception as e:
                print("export fail", e, flush=True)

        # 留一点余量在截止前结束
        if datetime.now(timezone.utc) + timedelta(seconds=args.sleep) >= end:
            break
        time.sleep(args.sleep)

    print("DAEMON final export...", flush=True)
    try:
        export_and_summary()
    except Exception as e:
        print("final export fail", e, flush=True)
    print(
        f"DAEMON done rounds={n} best={best.get('avg_score') if best else None}",
        flush=True,
    )


if __name__ == "__main__":
    main()
