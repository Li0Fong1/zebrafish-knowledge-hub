# -*- coding: utf-8 -*-
"""Server-side campaign watchdog — cron every 30min optional."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
LOG = RUNTIME / "logs" / "scheduler" / "campaign_watch.jsonl"
PY = os.environ.get("ZEBRA_PY", "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python")


def _log(obj: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    obj["ts"] = datetime.now().isoformat(timespec="seconds")
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    print(json.dumps(obj, ensure_ascii=False))


def _pgrep(pattern: str) -> bool:
    p = subprocess.run(["pgrep", "-f", pattern], capture_output=True)
    return p.returncode == 0


def main() -> None:
    os.chdir(ROOT)
    os.environ.setdefault("ZEBRA_RUNTIME", str(RUNTIME))
    os.environ.setdefault("ZEBRA_BASE", str(ROOT))
    os.environ.setdefault("PROJECT", str(ROOT))

    snap = {
        "closeout": _pgrep("scripts.three_day_closeout"),
        "repair": _pgrep("scihub_post_gold_pipeline --mode repair"),
        "dual_vec": _pgrep("task_dual_vec_loop"),
        "hub": _pgrep("uvicorn api.api_server"),
        "demo0": _pgrep("gpu0_embed_worker"),
        "demo1": _pgrep("hub_gpu1_pulse"),
    }
    accept = RUNTIME / "logs" / "scheduler" / "accept_latest.json"
    if accept.is_file():
        try:
            snap["accept"] = json.loads(accept.read_text(encoding="utf-8")).get("pass")
        except Exception:
            pass
    _log(snap)

    if not snap["closeout"] or not snap["repair"]:
        sh = RUNTIME / "gold_ingest" / "scripts" / "kick_closeout_repair.sh"
        if sh.is_file():
            subprocess.Popen(["bash", str(sh)], cwd=str(ROOT))
            _log({"action": "rekick_closeout_repair"})


if __name__ == "__main__":
    main()
