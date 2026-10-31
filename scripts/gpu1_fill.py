# -*- coding: utf-8 -*-
"""
GPU1 余量打满：不另载 4B 模型，只用 Hub 卡剩余显存做连续 GEMM。
chat_busy / 显存不够则降维或暂停，避免把 Hub 打挂。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
LOG_DIR = RUNTIME / "logs" / "scheduler"
LOG = LOG_DIR / "gpu1_fill.log"
PIDF = LOG_DIR / "gpu1_fill.pid"


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def chat_busy() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=5) as r:
            h = json.loads(r.read().decode("utf-8", "replace"))
        return bool(h.get("chat_busy"))
    except Exception:
        return False


def main() -> int:
    import torch

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PIDF.write_text(str(os.getpid()), encoding="utf-8")
    if not torch.cuda.is_available():
        _log("no cuda")
        return 2
    device = "cuda:0"
    n = int(os.environ.get("GPU1_FILL_N", "2560"))
    tensors = None
    while n >= 256:
        try:
            torch.cuda.empty_cache()
            a = torch.randn(n, n, device=device, dtype=torch.float16)
            b = torch.randn(n, n, device=device, dtype=torch.float16)
            tensors = (a, b)
            _log(f"gpu1 GEMM fill n={n} free={torch.cuda.mem_get_info()[0]//1024//1024}MiB")
            break
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            n = int(n * 0.75)
            _log(f"OOM shrink n->{n}")
    if tensors is None:
        _log("cannot allocate fill tensors")
        return 3
    a, b = tensors
    i = 0
    t0 = time.time()
    while True:
        if chat_busy():
            time.sleep(1.0)
            continue
        try:
            c = torch.mm(a, b)
            a = torch.nn.functional.normalize(c, dim=1)
            if i % 2 == 0:
                b = torch.mm(b, a)
            i += 1
            if i % 200 == 0:
                _log(f"gemm_iters={i} n={n} elapsed={int(time.time()-t0)}s")
        except torch.cuda.OutOfMemoryError:
            n = max(256, int(n * 0.7))
            torch.cuda.empty_cache()
            a = torch.randn(n, n, device=device, dtype=torch.float16)
            b = torch.randn(n, n, device=device, dtype=torch.float16)
            _log(f"runtime OOM, n={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
