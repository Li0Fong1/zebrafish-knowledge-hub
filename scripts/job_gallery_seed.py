# -*- coding: utf-8 -*-
"""Every-3-days gallery seed: scrape open sources → mirror into web/assets/zf-gallery/live/.

Wraps api.gallery_feed mirror helpers (same path Hub serves).
Env:
  ZF_GALLERY_MAX=120
  ZF_GALLERY_WEB=  (default: <repo>/web)
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))


def main() -> int:
    from gallery_feed import (  # noqa: E402
        _commons_search,
        _dedupe,
        _inat,
        _mirror_items,
        _openverse,
    )

    web = Path(os.getenv("ZF_GALLERY_WEB") or (ROOT / "web")).resolve()
    live = web / "assets" / "zf-gallery" / "live"
    live.mkdir(parents=True, exist_ok=True)
    max_n = int(os.getenv("ZF_GALLERY_MAX", "120"))

    t0 = time.time()
    print("[gallery-seed] fetch iNat latest/hot…", flush=True)
    latest = _inat("created_at", pages=3, per_page=40)
    hot = _inat("votes", pages=3, per_page=40)
    print(f"  latest={len(latest)} hot={len(hot)}", flush=True)
    commons = _commons_search(
        "zebrafish (confocal OR fluorescence OR microscopy OR embryo OR transgenic) filetype:bitmap",
        40,
        12,
    )
    openverse = _openverse(30, 12)
    print(f"  commons={len(commons)} openverse={len(openverse)}", flush=True)

    raw = _dedupe(hot + latest + commons + openverse)
    mirrored = _mirror_items(web, raw, max_n=max_n)
    ok = sum(1 for x in mirrored if x.get("mirrored"))
    n = len(
        [
            p
            for p in live.iterdir()
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        ]
    )
    print(
        f"[gallery-seed] mirrored_ok={ok} live_files={n} elapsed={time.time()-t0:.1f}s path={live}",
        flush=True,
    )
    return 0 if n >= 20 else 1


if __name__ == "__main__":
    raise SystemExit(main())
