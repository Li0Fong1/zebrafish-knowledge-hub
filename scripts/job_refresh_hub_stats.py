# -*- coding: utf-8 -*-
"""Bump / warm Hub dashboard stats by hitting /api/hub/stats (and tools/stats).

Env:
  ZF_HUB_URL=http://127.0.0.1:8080
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request


def _get(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "zf-stats-job/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main() -> int:
    base = (os.getenv("ZF_HUB_URL") or "http://127.0.0.1:8080").rstrip("/")
    ok = True
    for path in ("/api/hub/stats", "/api/tools/stats", "/api/health"):
        url = base + path
        try:
            data = _get(url)
            keys = list(data.keys())[:8] if isinstance(data, dict) else type(data).__name__
            print(f"[stats] OK {path} keys={keys}", flush=True)
        except Exception as e:
            ok = False
            print(f"[stats] FAIL {path}: {type(e).__name__}: {e}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
