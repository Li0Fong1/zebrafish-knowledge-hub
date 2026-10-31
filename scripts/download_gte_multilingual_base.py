# -*- coding: utf-8 -*-
"""
下载 Alibaba-NLP/gte-multilingual-base → models/gte-multilingual-base
使用 HF 镜像；不覆盖 models/bge-small-zh-v1.5。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from config import GTE_MULTILINGUAL_BASE_PATH

REPO_ID = "Alibaba-NLP/gte-multilingual-base"


def main() -> int:
    dest = Path(GTE_MULTILINGUAL_BASE_PATH)
    dest.parent.mkdir(parents=True, exist_ok=True)
    marker = dest / "modules.json"
    if marker.is_file() or (dest / "config.json").is_file():
        safe_print(f"[跳过] 本地已存在: {dest}")
        return 0

    safe_print(f"开始下载 {REPO_ID} → {dest}")
    safe_print(f"  HF_ENDPOINT={os.environ.get('HF_ENDPOINT')}")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        safe_print("[失败] 需要 huggingface_hub")
        return 1

    snapshot_download(
        repo_id=REPO_ID,
        local_dir=str(dest),
        local_dir_use_symlinks=False,
        resume_download=True,
    )
    safe_print(f"[完成] {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
