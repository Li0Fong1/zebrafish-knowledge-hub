# -*- coding: utf-8 -*-
"""PubMed YES 语料路径、去重统计与分片合并。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_CORPUS = ROOT / "data" / "literature" / "pubmed_yes_corpus.jsonl"
SHARD_DIR = MAIN_CORPUS.parent / "pubmed_yes_corpus_shards"


def shard_path(worker_id: int) -> Path:
    SHARD_DIR.mkdir(parents=True, exist_ok=True)
    return SHARD_DIR / f"w{worker_id:02d}.jsonl"


def output_path(worker_id: int, workers: int) -> Path:
    if workers <= 1:
        return MAIN_CORPUS
    return shard_path(worker_id)


def all_corpus_files() -> list[Path]:
    files: list[Path] = []
    if MAIN_CORPUS.is_file():
        files.append(MAIN_CORPUS)
    if SHARD_DIR.is_dir():
        files.extend(sorted(SHARD_DIR.glob("w*.jsonl")))
    return files


def load_done_pmids() -> set[str]:
    done: set[str] = set()
    for path in all_corpus_files():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    done.add(str(json.loads(line).get("pmid")))
                except json.JSONDecodeError:
                    continue
    return done


def count_unique_pmids() -> int:
    return len(load_done_pmids())


def merge_shards_to_main() -> int:
    """合并主文件 + 分片为单一 jsonl（按 pmid 数值排序），返回唯一篇数。"""
    by_pmid: dict[str, dict] = {}
    for path in all_corpus_files():
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pmid = str(rec.get("pmid") or "")
                if pmid:
                    by_pmid[pmid] = rec
    MAIN_CORPUS.parent.mkdir(parents=True, exist_ok=True)
    with open(MAIN_CORPUS, "w", encoding="utf-8") as out:
        for pmid in sorted(by_pmid.keys(), key=lambda x: int(x) if x.isdigit() else x):
            out.write(json.dumps(by_pmid[pmid], ensure_ascii=False) + "\n")
    return len(by_pmid)
