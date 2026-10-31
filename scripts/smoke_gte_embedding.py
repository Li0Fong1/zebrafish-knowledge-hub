# -*- coding: utf-8 -*-
"""gte-multilingual-base 烟测：可加载、维数=768、记录耗时。"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("FAISS_DISABLE_AVX2", "1")

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from config import EMBEDDING_MODEL_PATH, GTE_MULTILINGUAL_BASE_PATH

OUT = ROOT / "解析文件" / "事项一_换模落地状态.txt"


def _probe(path: str, expect_dim: int | None) -> dict:
    from rag.rag_builder import BgeEmbedding

    t0 = time.perf_counter()
    emb = BgeEmbedding(path)
    load_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    vec = emb.embed_query("zebrafish hif hypoxia")
    enc_s = time.perf_counter() - t1
    dim = int(len(vec))
    ok = expect_dim is None or dim == expect_dim
    return {
        "path": path,
        "exists": Path(path).is_dir(),
        "load_sec": round(load_s, 3),
        "encode_sec": round(enc_s, 3),
        "dim": dim,
        "expect_dim": expect_dim,
        "ok": ok,
    }


def main() -> int:
    gte = str(GTE_MULTILINGUAL_BASE_PATH)
    bge = str(EMBEDDING_MODEL_PATH)
    results = []
    if not Path(gte).is_dir() or not any(Path(gte).iterdir()):
        safe_print(f"[失败] 模型目录不存在或为空: {gte}")
        safe_print("  请先: python -m scripts.download_gte_multilingual_base")
        return 1

    safe_print("烟测 gte …")
    g = _probe(gte, 768)
    results.append(g)
    safe_print(f"  gte dim={g['dim']} load={g['load_sec']}s encode={g['encode_sec']}s ok={g['ok']}")

    if Path(bge).is_dir():
        safe_print("烟测旧 bge（回滚基线）…")
        b = _probe(bge, 512)
        results.append(b)
        safe_print(f"  bge dim={b['dim']} load={b['load_sec']}s encode={b['encode_sec']}s ok={b['ok']}")

    ok = all(r.get("ok") for r in results if r.get("expect_dim") == 768)
    # 追加写入状态文件头段由 reembed 脚本维护；此处写烟测片段
    smoke = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "results": results,
        "hub_still_old": True,
    }
    smoke_path = ROOT / "logs" / "gte_smoke.json"
    smoke_path.parent.mkdir(parents=True, exist_ok=True)
    smoke_path.write_text(json.dumps(smoke, ensure_ascii=False, indent=2), encoding="utf-8")
    safe_print(f"[完成] smoke → {smoke_path} overall_gte_ok={ok}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
