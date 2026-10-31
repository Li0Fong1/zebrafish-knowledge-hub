# -*- coding: utf-8 -*-
"""
双卡常驻调度器（资源护栏 + 多线开工）。

稳态布局
  GPU1: Hub（Qwen3 嵌入 + 检索服务，低延迟）
  GPU0: 常驻后台嵌入工人（增量向量化 / 抽检）
  CPU : 近门槛 PDF 修复（限流 1～2，nice），MySQL 注册

护栏（超限则休眠，不硬刚）
  - loadavg_1m < NPROC*0.75
  - MemAvailable > 20GiB
  - Hub chat_busy → 暂缓 CPU 重修复（仍允许 GPU0 轻量队列）
  - GPU0 显存占用异常 → 停工人并告警

队列文件
  runtime/logs/scheduler/queue.jsonl
  runtime/logs/scheduler/state.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
BASE = Path(os.environ.get("ZEBRA_BASE", "/data/zoujin/deng/zebrafish_gene_qa"))
LOG_DIR = RUNTIME / "logs" / "scheduler"
QUEUE = LOG_DIR / "queue.jsonl"
STATE = LOG_DIR / "state.json"
SCHED_LOG = LOG_DIR / "scheduler.log"
PIDF = LOG_DIR / "scheduler.pid"
NEAR_LIST = LOG_DIR / "near_floor_enos.json"
FLOOR = 35487
NEAR_MIN = 20000

# guards
MAX_LOAD_RATIO = float(os.environ.get("SCHED_MAX_LOAD_RATIO", "0.70"))
MIN_AVAIL_GIB = float(os.environ.get("SCHED_MIN_AVAIL_GIB", "20"))
REPAIR_BATCH = int(os.environ.get("SCHED_REPAIR_BATCH", "8"))
REPAIR_ROUNDS = int(os.environ.get("SCHED_REPAIR_ROUNDS", "2"))
SLEEP_BUSY = int(os.environ.get("SCHED_SLEEP_BUSY", "45"))
SLEEP_IDLE = int(os.environ.get("SCHED_SLEEP_IDLE", "20"))
VEC_BATCH = int(os.environ.get("SCHED_VEC_BATCH", "40"))


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with SCHED_LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _save_state(**kw: Any) -> None:
    cur: dict[str, Any] = {}
    if STATE.is_file():
        try:
            cur = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            cur = {}
    cur.update(kw)
    cur["updated_at"] = datetime.now().isoformat(timespec="seconds")
    STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")


def nproc() -> int:
    try:
        return os.cpu_count() or 8
    except Exception:
        return 8


def loadavg() -> float:
    try:
        return os.getloadavg()[0]
    except Exception:
        return 0.0


def mem_avail_gib() -> float:
    try:
        txt = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
        for ln in txt.splitlines():
            if ln.startswith("MemAvailable:"):
                kb = int(ln.split()[1])
                return kb / (1024 * 1024)
    except Exception:
        pass
    return 999.0


def gpu_stats() -> list[dict[str, int]]:
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
        rows = []
        for ln in out.strip().splitlines():
            parts = [p.strip() for p in ln.split(",")]
            if len(parts) >= 4:
                rows.append(
                    {
                        "index": int(parts[0]),
                        "used": int(parts[1]),
                        "total": int(parts[2]),
                        "util": int(parts[3]),
                    }
                )
        return rows
    except Exception:
        return []


def hub_health() -> dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=8) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"status": "down", "error": str(exc)}


def resources_ok(*, need_gpu0_headroom: bool = False) -> tuple[bool, str]:
    la = loadavg()
    lim = nproc() * MAX_LOAD_RATIO
    if la > lim:
        return False, f"loadavg {la:.2f}>{lim:.2f}"
    avail = mem_avail_gib()
    if avail < MIN_AVAIL_GIB:
        return False, f"mem_avail {avail:.1f}GiB<{MIN_AVAIL_GIB}"
    gpus = gpu_stats()
    if need_gpu0_headroom and gpus:
        g0 = next((g for g in gpus if g["index"] == 0), None)
        if g0 and g0["used"] > g0["total"] - 1500 and g0["util"] < 5:
            # model resident OK; only fail if nearly full AND another unexpected proc
            pass
        if g0 and g0["used"] > g0["total"] - 200:
            return False, f"gpu0 almost full used={g0['used']}"
    h = hub_health()
    if h.get("chat_busy"):
        return False, "hub_chat_busy"
    return True, "ok"


def build_near_floor_list() -> list[str]:
    """enos with body in [NEAR_MIN, FLOOR) from parse log, not currently gold on disk."""
    sys.path.insert(0, str(BASE))
    os.chdir(BASE)
    from core.entry import bootstrap_app

    bootstrap_app()
    from scripts.gold_hunt_fulltext import is_pdf_gold
    from scripts.scihub_post_gold_pipeline import load_rec, parse_log_rows

    rows = parse_log_rows()
    out: list[str] = []
    for eno, row in rows.items():
        if row.get("gold"):
            continue
        bl = int(row.get("body_len") or 0)
        if bl < NEAR_MIN or bl >= FLOOR:
            continue
        rec = load_rec(eno)
        if rec and is_pdf_gold(rec):
            continue
        out.append(eno)
    out = sorted(set(out), key=lambda x: int(x) if x.isdigit() else x)
    NEAR_LIST.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"near_floor_list n={len(out)}")
    return out


def enqueue(job: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    job = dict(job)
    job["enqueued_at"] = datetime.now().isoformat(timespec="seconds")
    with QUEUE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(job, ensure_ascii=False) + "\n")


def run_repair_batch(enos: list[str]) -> dict[str, Any]:
    sys.path.insert(0, str(BASE))
    os.chdir(BASE)
    from core.entry import bootstrap_app

    bootstrap_app()
    try:
        from scripts.gold_upgrade_5680 import set_pdf_extract_pool_size

        set_pdf_extract_pool_size(1)
    except Exception:
        pass
    from scripts.scihub_post_gold_pipeline import repair_one, load_rec
    from scripts.gold_hunt_fulltext import is_pdf_gold as _is_gold

    stats = {"tried": 0, "gold": 0, "still_short": 0, "fail": 0, "new_gold_enos": []}
    for eno in enos:
        ok, why = resources_ok()
        if not ok:
            _log(f"repair pause: {why}")
            break
        stats["tried"] += 1
        best = None
        for rnd in range(1, REPAIR_ROUNDS + 1):
            res = repair_one(eno, round_i=rnd)
            best = res
            if res.get("gold"):
                break
        if best and best.get("gold"):
            stats["gold"] += 1
            stats["new_gold_enos"].append(eno)
        elif best and best.get("status") == "still_short":
            stats["still_short"] += 1
        else:
            stats["fail"] += 1
        rec = load_rec(eno)
        if rec and _is_gold(rec) and eno not in stats["new_gold_enos"]:
            stats["new_gold_enos"].append(eno)
            if not best or not best.get("gold"):
                stats["gold"] += 1
    return stats


def run_vectorize_gpu0(limit: int = VEC_BATCH) -> dict[str, Any]:
    """Incremental embed on GPU0 only (Hub should be on GPU1)."""
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "0"
    env["ZEBRA_RUNTIME"] = str(RUNTIME)
    env["ZEBRA_BASE"] = str(BASE)
    env["QWEN3_4B_EMBED_BATCH"] = env.get("QWEN3_4B_EMBED_BATCH", "6")
    env["QWEN3_4B_DOC_MICRO"] = env.get("QWEN3_4B_DOC_MICRO", "48")
    # build then single partition using all pending on this worker
    py = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"
    # build corpus
    p1 = subprocess.run(
        [py, "-u", "-m", "scripts.scihub_dual_gpu_vectorize", "--build-only"],
        cwd=str(BASE),
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    _log(f"vec build rc={p1.returncode} {(p1.stdout or '')[-400:]}")
    # use partition 0/1 = all rows (single GPU worker)
    p2 = subprocess.run(
        [
            py,
            "-u",
            "-m",
            "scripts.scihub_dual_gpu_vectorize",
            "--partition",
            "0/1",
            "--gpu-label",
            "0",
            "--limit",
            str(limit),
        ],
        cwd=str(BASE),
        env=env,
        capture_output=True,
        text=True,
        timeout=7200,
    )
    _log(f"vec run rc={p2.returncode} {(p2.stdout or '')[-600:]}")
    return {"build_rc": p1.returncode, "vec_rc": p2.returncode}


def run_register() -> None:
    py = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"
    env = os.environ.copy()
    env["ZEBRA_RUNTIME"] = str(RUNTIME)
    env["ZEBRA_BASE"] = str(BASE)
    p = subprocess.run(
        [py, "-u", "-m", "scripts.scihub_post_gold_pipeline", "--mode", "register"],
        cwd=str(BASE),
        env=env,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    _log(f"register rc={p.returncode} {(p.stdout or '')[-400:]}")


def hub_warmup_pulse() -> None:
    """Light query so Hub GPU1 shows periodic util (non-blocking, skip if busy)."""
    h = hub_health()
    if h.get("chat_busy") or h.get("status") != "ok":
        return
    try:
        # health already hits; retrieval debug if token present
        envp = BASE / ".env"
        token = ""
        if envp.is_file():
            for ln in envp.read_text(encoding="utf-8", errors="replace").splitlines():
                if ln.startswith("WEB_ACCESS_TOKEN="):
                    token = ln.split("=", 1)[1].strip()
                    break
        if not token:
            return
        req = urllib.request.Request(
            "http://127.0.0.1:8080/api/retrieval/debug",
            data=json.dumps({"question": "zebrafish sox2 expression", "top_k": 2}).encode(),
            headers={"Content-Type": "application/json", "X-Access-Token": token},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            _ = r.read(200)
        _log("hub_warmup_pulse ok")
    except Exception as exc:
        _log(f"hub_warmup_pulse skip: {exc}")


def scheduler_loop(*, cycles: int = 0) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # single-instance lock
    lock_path = LOG_DIR / "scheduler.instance.lock"
    lock_fh = open(lock_path, "w", encoding="utf-8")
    if os.name != "nt":
        import fcntl

        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _log("another scheduler holds lock; exit")
            return 0
    lock_fh.write(str(os.getpid()))
    lock_fh.flush()

    PIDF.write_text(str(os.getpid()), encoding="utf-8")
    _log("=== scheduler start ===")
    _save_state(phase="start", pid=os.getpid())

    near = build_near_floor_list()
    cursor = 0
    n = 0
    while True:
        n += 1
        if cycles and n > cycles:
            break
        gpus = gpu_stats()
        h = hub_health()
        ok, why = resources_ok()
        _save_state(
            phase="tick",
            tick=n,
            resources_ok=ok,
            why=why,
            loadavg=loadavg(),
            mem_avail_gib=round(mem_avail_gib(), 2),
            gpus=gpus,
            hub_status=h.get("status"),
            pubmed_ready=h.get("pubmed_ready"),
            near_left=max(0, len(near) - cursor),
        )
        _log(f"tick={n} ok={ok} why={why} gpus={gpus} near_left={max(0,len(near)-cursor)}")

        if not ok:
            time.sleep(SLEEP_BUSY)
            continue

        # 1) CPU repair near-floor batch
        if cursor < len(near):
            batch = near[cursor : cursor + REPAIR_BATCH]
            cursor += len(batch)
            _log(f"repair batch {batch[:5]}... n={len(batch)}")
            stats = run_repair_batch(batch)
            _log(f"repair stats {stats}")
            _save_state(last_repair=stats)
            if stats.get("new_gold_enos"):
                enqueue({"type": "vectorize", "enos": stats["new_gold_enos"]})
                # 2) GPU0 vectorize new gold
                ok2, why2 = resources_ok()
                if ok2:
                    _log("vectorize on GPU0 after repair")
                    v = run_vectorize_gpu0(limit=max(VEC_BATCH, len(stats["new_gold_enos"])))
                    _save_state(last_vectorize=v)
                    run_register()
                else:
                    _log(f"defer vectorize: {why2}")
        else:
            # rebuild list periodically
            if n % 20 == 0:
                near = build_near_floor_list()
                cursor = 0
            # keep Hub GPU warm lightly + optional pending vec drain
            hub_warmup_pulse()
            # drain any pending gold not vectorized
            ok2, why2 = resources_ok()
            if ok2:
                v = run_vectorize_gpu0(limit=VEC_BATCH)
                _save_state(last_vectorize_idle=v)

        time.sleep(SLEEP_IDLE)

    _log("=== scheduler stop ===")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=0, help="0=forever")
    ap.add_argument("--build-near-only", action="store_true")
    args = ap.parse_args()
    if args.build_near_only:
        build_near_floor_list()
        return 0
    return scheduler_loop(cycles=args.cycles)


if __name__ == "__main__":
    raise SystemExit(main())
