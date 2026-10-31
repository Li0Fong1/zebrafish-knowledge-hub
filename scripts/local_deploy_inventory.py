# -*- coding: utf-8 -*-
"""Local inventory: compare key files/dirs vs what should be on server."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

KEY_FILES = [
    "api/api_server.py",
    "scripts/three_day_closeout.py",
    "scripts/scihub_post_gold_pipeline.py",
    "scripts/scihub_dual_gpu_vectorize.py",
    "scripts/full_compliance_audit.py",
    "scripts/export_non_gold_download_guide.py",
    "scripts/rebuild_parse_universe.py",
    "scripts/gold_hunt_fulltext.py",
    "scripts/server_campaign_watch.py",
]

KEY_DIRS = ["api", "core", "scripts", "rag", "database", "deploy/server", "tools/remote_ops"]


def main() -> None:
    rep = {"files": {}, "dirs": {}, "heavy_local_only": {}}
    for rel in KEY_FILES:
        p = ROOT / rel
        rep["files"][rel] = {"exists": p.is_file(), "bytes": p.stat().st_size if p.is_file() else 0}
    for d in KEY_DIRS:
        p = ROOT / d
        n = sum(1 for _ in p.rglob("*") if _.is_file()) if p.is_dir() else 0
        rep["dirs"][d] = {"exists": p.is_dir(), "file_count": n}
    for name in ["pdf库", "解析文件", ".venv"]:
        p = ROOT / name
        if p.is_dir():
            try:
                rep["heavy_local_only"][name] = sum(1 for _ in p.rglob("*") if _.is_file())
            except OSError:
                rep["heavy_local_only"][name] = -1
    out = ROOT / "解析文件" / "local_deploy_inventory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    print("wrote", out)


if __name__ == "__main__":
    main()
