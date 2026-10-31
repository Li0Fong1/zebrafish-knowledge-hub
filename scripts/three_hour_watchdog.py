# -*- coding: utf-8 -*-
"""
每 3 小时自动验收；未达标则只修缺口（向量化 / 台账 / 注册）。
收口战役在跑时只做轻量快照，不叠加重活。
单实例 flock；超限休眠。永不关机、不覆盖 gte、不杀 MySQL。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
BASE = Path(os.environ.get("ZEBRA_BASE", str(ROOT)))
LOG_DIR = RUNTIME / "logs" / "scheduler"
LOG = LOG_DIR / "three_hour_watchdog.log"
LOCK = LOG_DIR / "three_hour_watchdog.lock"
PIDF = LOG_DIR / "three_hour_watchdog.pid"
CLOSEOUT_PID = LOG_DIR / "three_day_closeout.pid"
ACCEPT = LOG_DIR / "accept_latest.json"
INTERVAL = int(os.environ.get("WATCHDOG_SEC", str(3 * 3600)))
PY = os.environ.get("ZEBRA_PY", "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python")


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def acquire() -> bool:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "w", encoding="utf-8")
    if os.name != "nt":
        import fcntl

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
    fh.write(str(os.getpid()))
    fh.flush()
    globals()["_lock_fh"] = fh
    return True


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def closeout_running() -> bool:
    if not CLOSEOUT_PID.is_file():
        return False
    try:
        pid = int(CLOSEOUT_PID.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        return False
    if not pid or not pid_alive(pid):
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except Exception:
        cmd = ""
    return "three_day_closeout" in cmd


def loadavg() -> float:
    try:
        return os.getloadavg()[0]
    except Exception:
        return 0.0


def mem_avail_gib() -> float:
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) / (1024 * 1024)
    except Exception:
        pass
    return 999.0


def hub_health() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=8) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"status": "down", "error": str(exc)}


def restart_hub() -> None:
    _log("restart Hub on GPU1 (pubmed_ready/health fail)")
    env_prefix = r"""
set -eu
BASE=/data/zoujin/deng/zebrafish_gene_qa
RUNTIME=/data/zoujin/deng/zebrafish_runtime
PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
pkill -f 'uvicorn api.api_server:app' || true
sleep 6
source /data/zoujin/deng/miniconda3/etc/profile.d/conda.sh
conda activate zebrafish
export PYTHONPATH=$BASE:$BASE/core:$BASE/database
set -a
source "$BASE/.env"
set +a
export CUDA_VISIBLE_DEVICES=1
export EMBEDDING_DEVICE=cuda:0
export LLM_DEVICE=cpu
export EMBEDDING_DIM=2048
export SKIP_KNOWLEDGE_FAISS_REBUILD=1
export FAISS_DISABLE_AVX2=1
cd "$BASE"
nohup $PY -m uvicorn api.api_server:app --host 127.0.0.1 --port 8080 >>"$RUNTIME/logs/hub.log" 2>&1 &
echo $! > "$RUNTIME/hub.pid"
"""
    subprocess.run(["bash", "-lc", env_prefix], timeout=60)
    for _ in range(20):
        time.sleep(12)
        h = hub_health()
        _log(f"hub restart wait status={h.get('status')} pubmed={h.get('pubmed_ready')}")
        if h.get("status") == "ok" and h.get("pubmed_ready"):
            return


def run_mod(*args: str, timeout: int = 7200) -> int:
    n = os.cpu_count() or 8
    if loadavg() > n * 0.70 or mem_avail_gib() < 20:
        _log(f"skip {' '.join(args)} resources load={loadavg():.2f} mem={mem_avail_gib():.1f}")
        return 3
    env = os.environ.copy()
    env["ZEBRA_RUNTIME"] = str(RUNTIME)
    env["ZEBRA_BASE"] = str(BASE)
    p = subprocess.run(
        [PY, "-u", "-m", "scripts.three_day_closeout", *args],
        cwd=str(BASE),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    _log(f"{args} rc={p.returncode} {((p.stdout or '') + (p.stderr or ''))[-400:]}")
    return p.returncode


def tick() -> None:
    _log("=== 3h tick ===")
    _log(f"snap load={loadavg():.2f} mem={mem_avail_gib():.1f}GiB hub={hub_health().get('status')}")
    if closeout_running():
        _log("closeout still running → snapshot only")
        run_mod("--accept-only", timeout=1800)
        return
    rc = run_mod("--accept-only", timeout=1800)
    if rc == 0:
        _log("ACCEPT_PASS")
        h = hub_health()
        if h.get("status") != "ok" or not h.get("pubmed_ready"):
            restart_hub()
        return
    _log("ACCEPT_FAIL → fix gaps")
    h = hub_health()
    if h.get("status") != "ok" or not h.get("pubmed_ready"):
        restart_hub()
    run_mod("--fix-gaps", timeout=6 * 3600)
    rc2 = run_mod("--accept-only", timeout=1800)
    _log("ACCEPT after fix " + ("PASS" if rc2 == 0 else "STILL_FAIL"))
    if ACCEPT.is_file():
        try:
            rep = json.loads(ACCEPT.read_text(encoding="utf-8"))
            _log(f"accept_latest pass={rep.get('pass')} checks={rep.get('checks')}")
        except Exception:
            pass


def main() -> int:
    if not acquire():
        _log("watchdog already running")
        return 0
    try:
        os.nice(15)
    except Exception:
        pass
    PIDF.write_text(str(os.getpid()), encoding="utf-8")
    _log(f"watchdog start interval={INTERVAL}s")
    tick()
    while True:
        time.sleep(INTERVAL)
        tick()


if __name__ == "__main__":
    raise SystemExit(main())
