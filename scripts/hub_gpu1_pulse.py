# -*- coding: utf-8 -*-
"""
GPU1 Hub 拉满：优先打 /api/embed/warmup（纯嵌入），chat_busy 时让路。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
BASE = Path(os.environ.get("ZEBRA_BASE", "/data/zoujin/deng/zebrafish_gene_qa"))
LOG_DIR = RUNTIME / "logs" / "scheduler"
LOG = LOG_DIR / "hub_gpu1_pulse.log"
PIDF = LOG_DIR / "hub_gpu1_pulse.pid"
SLEEP = float(os.environ.get("HUB_PULSE_SLEEP", "0"))
CONCURRENCY = max(1, int(os.environ.get("HUB_PULSE_CONCURRENCY", "6")))
BATCH = int(os.environ.get("HUB_PULSE_BATCH", "32"))
ROUNDS = int(os.environ.get("HUB_PULSE_ROUNDS", "4"))
CHARS = int(os.environ.get("HUB_PULSE_CHARS", "1200"))


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def health() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=8) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"status": "down", "error": str(exc)}


def token() -> str:
    p = BASE / ".env"
    if not p.is_file():
        return ""
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if ln.startswith("WEB_ACCESS_TOKEN="):
            return ln.split("=", 1)[1].strip()
    return ""


def warmup_once(tok: str) -> int:
    req = urllib.request.Request(
        "http://127.0.0.1:8080/api/embed/warmup",
        data=json.dumps({"batch": BATCH, "rounds": ROUNDS, "chars": CHARS}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Access-Token": tok},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        body = json.loads(r.read().decode("utf-8", "replace"))
    return int(body.get("encoded") or 0)


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PIDF.write_text(str(os.getpid()), encoding="utf-8")
    tok = token()
    if not tok:
        _log("no WEB_ACCESS_TOKEN; exit")
        return 2
    _log(
        f"hub GPU1 FULL pulse start sleep={SLEEP} concurrency={CONCURRENCY} "
        f"batch={BATCH} rounds={ROUNDS}"
    )
    i = 0
    fails = 0
    encoded = 0
    t0 = time.time()
    while True:
        h = health()
        if h.get("status") != "ok":
            _log(f"hub not ready: {h.get('status')}")
            time.sleep(8)
            continue
        # embed warmup 不依赖 pubmed_ready；chat 忙时让路
        if h.get("chat_busy"):
            time.sleep(1.5)
            continue
        if not h.get("pubmed_ready"):
            # 仍可打 embedding；降低并发避免和 FAISS 预热抢显存
            pass
        try:
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
                futs = [pool.submit(warmup_once, tok) for _ in range(CONCURRENCY)]
                for fut in as_completed(futs):
                    encoded += int(fut.result() or 0)
            fails = 0
            i += CONCURRENCY
            if i % (CONCURRENCY * 5) == 0:
                elapsed = max(0.1, time.time() - t0)
                _log(f"warmups={i} encoded={encoded} rate={encoded/elapsed:.1f}/s")
        except Exception as exc:
            fails += 1
            _log(f"pulse fail#{fails}: {exc}")
            time.sleep(min(30, 2 * fails))
            continue
        if SLEEP > 0:
            time.sleep(SLEEP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
