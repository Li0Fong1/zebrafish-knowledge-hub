# -*- coding: utf-8 -*-
"""热控 + 任务自愈守护（中等强度过夜用）。

关注:
- GPU 温度（nvidia-smi）
- 可用内存
- ENO / overnight / gold / Hub 是否存活
过热或内存过低时: 降优先级 / 暂停战役 / 必要时暂停 ENO
恢复后: 由 overnight 自动续跑或本守护拉起

  python -m scripts.thermal_ops_guard --detach
  python -m scripts.thermal_ops_guard --foreground
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.is_file():
    PY = Path(sys.executable)

STATUS = ROOT / "解析文件" / "thermal_ops_guard_status.json"
LOG = ROOT / "logs" / "thermal_ops_guard.log"
LOCK = ROOT / "解析文件" / "thermal_ops_guard.run.lock"
PAUSE_FLAG = ROOT / "解析文件" / "thermal_pause_eno.flag"
ENO_TARGET = 8012
FAISS_MAN = Path(
    os.path.expandvars(r"%LOCALAPPDATA%\ZebrafishGeneQA\faiss_pubmed_yes_gte_mbase_v1\manifest.json")
)

# thresholds (laptop 4060 / 16GB)
GPU_WARN_C = 78
GPU_HOT_C = 85
GPU_CRIT_C = 90
FREE_WARN_GB = 1.8
FREE_CRIT_GB = 1.0
INTERVAL_SEC = 45


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _write_status(d: dict) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    d = dict(d)
    d["updated_at"] = datetime.now().isoformat(timespec="seconds")
    STATUS.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _acquire_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fp = open(LOCK, "a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt

            fp.seek(0)
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        return None
    fp.seek(0)
    fp.truncate()
    fp.write(f"{os.getpid()}\n{datetime.now().isoformat(timespec='seconds')}\n")
    fp.flush()
    return fp


def _free_gb() -> float:
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return round(int(out.strip()) / (1024 * 1024), 2)
    except Exception:
        return -1.0


def _gpu() -> dict:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,utilization.gpu,memory.used,power.draw",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        # e.g. 59, 10, 1272, 12.41
        parts = [p.strip() for p in out.split(",")]
        return {
            "temp_c": float(parts[0]) if parts else -1,
            "util": float(parts[1]) if len(parts) > 1 else -1,
            "mem_used_mb": float(parts[2]) if len(parts) > 2 else -1,
            "power_w": float(parts[3]) if len(parts) > 3 else -1,
        }
    except Exception as e:
        return {"temp_c": -1, "error": str(e)}


def _cpu_pct() -> float:
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-Counter '\\Processor(_Total)\\% Processor Time').CounterSamples.CookedValue",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        return round(float(out.strip()), 1)
    except Exception:
        return -1.0


def _py_procs() -> list[dict]:
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(out or "[]")
        if isinstance(data, dict):
            data = [data]
        return data or []
    except Exception:
        return []


def _find(needle: str) -> list[int]:
    hits = []
    for row in _py_procs():
        cmd = str(row.get("CommandLine") or "")
        pid = row.get("ProcessId")
        if pid and needle in cmd:
            hits.append(int(pid))
    return hits


def _set_priority(pid: int, level: str) -> None:
    # Idle / BelowNormal / Normal
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Process -Id {int(pid)} -EA SilentlyContinue).PriorityClass='{level}'",
            ],
            capture_output=True,
        )
    except Exception:
        pass


def _kill(needle: str) -> int:
    n = 0
    for pid in _find(needle):
        subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
        n += 1
        _log(f"kill {needle} pid={pid}")
    return n


def _spawn(mod_args: list[str], log_name: str, env: dict | None = None) -> int | None:
    log = ROOT / "logs" / log_name
    log.parent.mkdir(parents=True, exist_ok=True)
    fh = log.open("a", encoding="utf-8")
    fh.write(f"\n==== thermal_guard spawn {datetime.now().isoformat()} ====\n")
    fh.flush()
    e = os.environ.copy()
    if env:
        e.update(env)
    flags = 0x08000000 | 0x00000008 | 0x00004000
    p = subprocess.Popen(
        [str(PY), "-u", *mod_args],
        cwd=str(ROOT),
        stdout=fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=e,
        creationflags=flags,
        close_fds=False,
    )
    time.sleep(1.2)
    _set_priority(p.pid, "BelowNormal")
    _log(f"spawn pid={p.pid} {mod_args}")
    return int(p.pid)


def _eno_count() -> int:
    if not FAISS_MAN.is_file():
        return 0
    try:
        m = json.loads(FAISS_MAN.read_text(encoding="utf-8-sig"))
        return int(m.get("eno_papers") or 0)
    except Exception:
        return 0


def _dedupe(needle: str, keep_max_ws: bool = True) -> None:
    rows = []
    for row in _py_procs():
        cmd = str(row.get("CommandLine") or "")
        pid = row.get("ProcessId")
        if not pid or needle not in cmd:
            continue
        try:
            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"(Get-Process -Id {pid}).WorkingSet64",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            ws = int(out.strip() or 0)
        except Exception:
            ws = 0
        rows.append((int(pid), ws))
    if len(rows) <= 1:
        return
    rows.sort(key=lambda x: x[1], reverse=True)
    keep = rows[0][0] if keep_max_ws else min(r[0] for r in rows)
    for pid, ws in rows:
        if pid != keep:
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
            _log(f"dedupe {needle} kill={pid} ws={ws} keep={keep}")


def one_cycle(state: dict) -> None:
    gpu = _gpu()
    free = _free_gb()
    cpu = _cpu_pct()
    eno = _eno_count()
    temp = float(gpu.get("temp_c") or -1)
    mode = "normal"
    actions: list[str] = []

    overnight = _find("overnight_medium_ops")
    # ignore the detach launcher itself if any; prefer foreground workers
    overnight = [p for p in overnight if True]
    eno_pids = _find("vectorize_exist_eno")
    gold = _find("gold_auto_throttle") + _find("gold_keep_alive") + _find("gold_quiet_mode")
    hub = _find("api.api_server")
    thermal_self = _find("thermal_ops_guard")

    # --- thermal ---
    if temp >= GPU_CRIT_C:
        mode = "thermal_crit"
        actions.append(f"gpu_crit={temp}")
        # pause ENO hard
        if eno_pids:
            _kill("vectorize_exist_eno")
            PAUSE_FLAG.write_text(f"paused_at={datetime.now().isoformat()} temp={temp}\n", encoding="utf-8")
            actions.append("paused_eno")
        if gold:
            _kill("gold_auto_throttle")
            _kill("gold_keep_alive")
            _kill("gold_quiet_mode")
            _kill("gold_hunt_fulltext")
            actions.append("paused_gold")
        state["cool_until"] = time.time() + 600  # 10 min cool
    elif temp >= GPU_HOT_C or free <= FREE_CRIT_GB:
        mode = "thermal_hot" if temp >= GPU_HOT_C else "mem_crit"
        actions.append(f"temp={temp} free={free}")
        for pid in eno_pids:
            _set_priority(pid, "Idle")
            actions.append(f"eno_idle:{pid}")
        if gold:
            _kill("gold_hunt_fulltext")
            _kill("gold_pdfku_library_full_gold")
            for pid in gold:
                _set_priority(pid, "Idle")
            actions.append("gold_idle")
        state["cool_until"] = max(float(state.get("cool_until") or 0), time.time() + 180)
    elif temp >= GPU_WARN_C or free <= FREE_WARN_GB or cpu >= 95:
        mode = "throttle"
        actions.append(f"temp={temp} free={free} cpu={cpu}")
        for pid in eno_pids:
            _set_priority(pid, "Idle" if free <= FREE_WARN_GB else "BelowNormal")
        for pid in gold:
            _set_priority(pid, "Idle")
    else:
        mode = "normal"
        for pid in eno_pids:
            _set_priority(pid, "BelowNormal")

    cooling = time.time() < float(state.get("cool_until") or 0)

    # --- health / resume ---
    if not cooling and mode in ("normal", "throttle") and temp < GPU_HOT_C and free > FREE_CRIT_GB + 0.3:
        if PAUSE_FLAG.is_file() and temp < GPU_WARN_C and free > FREE_WARN_GB:
            try:
                PAUSE_FLAG.unlink()
                actions.append("clear_thermal_pause")
            except Exception:
                pass

        # ensure overnight alive (do NOT taskkill /T dedupe — kills ENO children)
        ov_fg = [p for p in _find("overnight_medium_ops") if p]
        # count unique process trees roughly by presence
        if not ov_fg:
            actions.append("restart_overnight")
            _spawn(["-m", "scripts.overnight_medium_ops", "--foreground"], "overnight_medium_ops.log")

        # ENO incomplete and not running
        if eno < ENO_TARGET and not eno_pids and not PAUSE_FLAG.is_file():
            actions.append("resume_eno")
            _spawn(
                ["-m", "scripts.vectorize_exist_eno", "--resume"],
                "eno_gte_embed_stdout.log",
                env={
                    "EXIST_ENO_RUNTIME_SHARD": "50",
                    "PUBMED_YES_SECTION_CHUNKING": "1",
                    "FAISS_DISABLE_AVX2": "1",
                    "HF_ENDPOINT": os.getenv("HF_ENDPOINT") or "https://hf-mirror.com",
                },
            )
        elif len([p for p in eno_pids]) > 3:
            # only collapse clear double heavy workers
            _dedupe("vectorize_exist_eno", keep_max_ws=True)

        # after ENO done: avoid gold if still hot/low mem
        if eno >= ENO_TARGET and free >= 2.2 and temp < GPU_WARN_C:
            if not gold and not _find("gold_auto_throttle"):
                actions.append("ensure_gold_throttle")
                _spawn(["-m", "scripts.gold_auto_throttle", "--detach"], "gold_auto_throttle_guard.log")
            if not hub and free >= 3.5:
                actions.append("ensure_hub")
                _spawn(["-X", "utf8", "-m", "api.api_server"], "api_server_overnight.out.log")

    # if critically low mem while ENO running: do not start gold/hub
    if free <= FREE_WARN_GB:
        for needle in ("gold_hunt_fulltext", "gold_rescue", "gold_full_speed"):
            if _find(needle):
                _kill(needle)
                actions.append(f"kill_heavy:{needle}")

    st = {
        "mode": mode,
        "cooling": cooling,
        "gpu_temp_c": temp,
        "gpu_util": gpu.get("util"),
        "gpu_power_w": gpu.get("power_w"),
        "free_gb": free,
        "cpu_pct": cpu,
        "eno": eno,
        "eno_left": max(0, ENO_TARGET - eno),
        "procs": {
            "overnight": overnight,
            "eno": eno_pids,
            "gold": gold,
            "hub": hub,
        },
        "actions": actions,
        "thresholds": {
            "gpu_warn": GPU_WARN_C,
            "gpu_hot": GPU_HOT_C,
            "gpu_crit": GPU_CRIT_C,
            "free_warn": FREE_WARN_GB,
            "free_crit": FREE_CRIT_GB,
        },
    }
    _write_status(st)
    if actions:
        _log(f"mode={mode} temp={temp} free={free} cpu={cpu} eno={eno} actions={actions}")
    else:
        _log(f"mode={mode} temp={temp} free={free} cpu={cpu} eno={eno} ok")


def run_loop() -> int:
    lock = _acquire_lock()
    if lock is None:
        _log("lock busy — exit")
        return 0
    state: dict = {}
    _log("=== thermal_ops_guard start ===")
    try:
        while True:
            try:
                one_cycle(state)
            except Exception as e:
                _log(f"cycle_error {e!r}")
            # hotter => sleep longer (give cool-down)
            gpu = _gpu()
            temp = float(gpu.get("temp_c") or 0)
            sleep_s = INTERVAL_SEC
            if temp >= GPU_HOT_C:
                sleep_s = 90
            if temp >= GPU_CRIT_C:
                sleep_s = 180
            time.sleep(sleep_s)
    finally:
        try:
            lock.close()
        except Exception:
            pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detach", action="store_true")
    ap.add_argument("--foreground", action="store_true")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    if args.once:
        one_cycle({})
        return 0
    if args.detach and not args.foreground:
        if _find("thermal_ops_guard --foreground"):
            print("thermal_ops_guard already running")
            return 0
        log = LOG
        log.parent.mkdir(parents=True, exist_ok=True)
        fh = log.open("a", encoding="utf-8")
        flags = 0x08000000 | 0x00000008 | 0x00004000
        p = subprocess.Popen(
            [str(PY), "-u", "-m", "scripts.thermal_ops_guard", "--foreground"],
            cwd=str(ROOT),
            stdout=fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=False,
        )
        print(f"thermal_ops_guard detached pid={p.pid}")
        return 0
    return run_loop()


if __name__ == "__main__":
    raise SystemExit(main())
