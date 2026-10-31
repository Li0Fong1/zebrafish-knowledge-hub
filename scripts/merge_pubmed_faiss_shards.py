# -*- coding: utf-8 -*-
"""把 PubmedYes 细碎 FAISS shard 合并到新目录。CPU only，不覆盖源目录 / v1。

默认：v2 → v2_merged，约 16 个细片合成 1 个（约 192 篇/片）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("FAISS_DISABLE_AVX2", "1")
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("EMBEDDING_DEVICE", "cpu")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings


class DummyEmb(Embeddings):
    def __init__(self, dim: int = 2048):
        self.dim = dim

    def embed_documents(self, texts):
        return [[0.0] * self.dim for _ in texts]

    def embed_query(self, text):
        return [0.0] * self.dim


def _load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _log(msg: str, log_path: Path) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def merge_dirs(src: Path, dst: Path, *, group: int, dim: int, log_path: Path) -> dict:
    if src.resolve() == dst.resolve():
        raise SystemExit("refuse: src equals dst")
    mf = _load_json(src / "manifest.json", {})
    shard_ids = sorted(int(x) for x in (mf.get("shards") or []))
    if not shard_ids:
        shard_ids = sorted(
            int(p.name.split("_")[1])
            for p in src.glob("shard_*")
            if (p / "index.faiss").is_file()
        )
    if not shard_ids:
        raise SystemExit(f"no shards in {src}")
    dst.mkdir(parents=True, exist_ok=True)
    out_mf = _load_json(
        dst / "manifest.json",
        {
            "shards": [],
            "shard_info": {},
            "total_papers": 0,
            "total_chunks": 0,
            "status": "building",
            "source": str(src),
            "merged_from": str(src),
            "group_size": group,
            "embedding_dim": mf.get("embedding_dim") or dim,
            "embedding_model": mf.get("embedding_model"),
            "index_version": mf.get("index_version"),
            "chunking": mf.get("chunking"),
        },
    )
    done_groups = set(out_mf.get("shard_info") or {})
    emb = DummyEmb(int(mf.get("embedding_dim") or dim))
    src_ready = str(mf.get("status") or "") == "ready"
    groups = [shard_ids[i : i + group] for i in range(0, len(shard_ids), group)]
    t0 = time.time()
    for gi, ids in enumerate(groups):
        if (not src_ready) and len(ids) < group:
            _log(f"hold partial group {gi} n={len(ids)} until source ready", log_path)
            break
        key = str(gi)
        if key in done_groups and (dst / f"shard_{gi:04d}" / "index.faiss").is_file():
            continue
        loaded = []
        papers = 0
        chunks = 0
        info = mf.get("shard_info") or {}
        for sid in ids:
            sd = src / f"shard_{sid:04d}"
            if not (sd / "index.faiss").is_file():
                _log(f"skip missing {sd}", log_path)
                continue
            vs = FAISS.load_local(str(sd), emb, allow_dangerous_deserialization=True)
            loaded.append(vs)
            meta = info.get(str(sid)) or {}
            papers += int(meta.get("paper_count") or 0)
            chunks += int(getattr(vs.index, "ntotal", 0) or 0)
        if not loaded:
            continue
        base = loaded[0]
        for extra in loaded[1:]:
            base.merge_from(extra)
        out_sd = dst / f"shard_{gi:04d}"
        out_sd.mkdir(parents=True, exist_ok=True)
        base.save_local(str(out_sd))
        shards = list(out_mf.get("shards") or [])
        if gi not in shards:
            shards.append(gi)
        out_mf["shards"] = shards
        out_mf.setdefault("shard_info", {})[key] = {
            "paper_count": papers,
            "chunk_count": int(getattr(base.index, "ntotal", 0) or chunks),
            "src_shards": ids,
        }
        out_mf["total_papers"] = sum(int((out_mf["shard_info"][k] or {}).get("paper_count") or 0) for k in out_mf["shard_info"])
        out_mf["total_chunks"] = sum(int((out_mf["shard_info"][k] or {}).get("chunk_count") or 0) for k in out_mf["shard_info"])
        out_mf["status"] = "building"
        out_mf["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_json(dst / "manifest.json", out_mf)
        elapsed = max(1.0, time.time() - t0)
        _log(
            f"merged group {gi+1}/{len(groups)} shards={ids[:3]}.. ntotal={base.index.ntotal} "
            f"elapsed={elapsed:.0f}s",
            log_path,
        )
        done_groups.add(key)

    if src_ready:
        out_mf["status"] = "ready"
    else:
        out_mf["status"] = "building"
        _log("source still building; merged left as building", log_path)
    out_mf["source_status"] = mf.get("status")
    out_mf["source_papers"] = mf.get("total_papers")
    out_mf["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _save_json(dst / "manifest.json", out_mf)
    _log(
        f"MERGE_OK groups={len(out_mf.get('shards') or [])} papers={out_mf.get('total_papers')} "
        f"chunks={out_mf.get('total_chunks')}",
        log_path,
    )
    return out_mf


def smoke_load(index_dir: Path, dim: int, log_path: Path) -> dict:
    emb = DummyEmb(dim)
    mf = _load_json(index_dir / "manifest.json", {})
    shard_ids = sorted(int(x) for x in (mf.get("shards") or []))
    t0 = time.time()
    n = 0
    loaded = 0
    for sid in shard_ids:
        sd = index_dir / f"shard_{sid:04d}"
        if not (sd / "index.faiss").is_file():
            continue
        vs = FAISS.load_local(str(sd), emb, allow_dangerous_deserialization=True)
        n += int(getattr(vs.index, "ntotal", 0) or 0)
        loaded += 1
    elapsed = round(time.time() - t0, 2)
    out = {
        "dir": str(index_dir),
        "shards_loaded": loaded,
        "ntotal": n,
        "elapsed_sec": elapsed,
        "papers": mf.get("total_papers"),
        "status": mf.get("status"),
    }
    _log(f"SMOKE_LOAD {out}", log_path)
    return out


def main() -> int:
    rt = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
    p = argparse.ArgumentParser()
    p.add_argument("--src", default=str(rt / "faiss_pubmed_yes_qwen3_4b_v2"))
    p.add_argument("--dst", default=str(rt / "faiss_pubmed_yes_qwen3_4b_v2_merged"))
    p.add_argument("--group", type=int, default=16)
    p.add_argument("--dim", type=int, default=2048)
    p.add_argument("--smoke-only", action="store_true")
    args = p.parse_args()
    src = Path(args.src)
    dst = Path(args.dst)
    log_path = rt / "logs" / "_merge_v2.log"
    v1 = rt / "faiss_pubmed_yes_qwen3_4b_v1"
    v1t = rt / "faiss_pubmed_yes_qwen3_4b_v1_titlefix"
    if dst.resolve() in {v1.resolve(), v1t.resolve(), src.resolve()}:
        raise SystemExit("refuse: would overwrite v1/v2")
    if args.smoke_only:
        smoke_load(dst if dst.is_dir() else src, args.dim, log_path)
        return 0
    merge_dirs(src, dst, group=args.group, dim=args.dim, log_path=log_path)
    smoke = smoke_load(dst, args.dim, log_path)
    if smoke["elapsed_sec"] > 180:
        _log(f"WARN smoke load {smoke['elapsed_sec']}s > 180", log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
