# -*- coding: utf-8 -*-
"""去重 merged_corpus RIS 标题索引（缓存 JSON，供是.md DOI/PMID 补全）。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RIS_DIR = ROOT / "项目阶段" / "05_数据与库" / "归档" / "去重之后" / "merged_corpus" / "merged_corpus"
INDEX_PATH = ROOT / "data" / "literature" / "dedup_ris_title_index.json"
META_PATH = ROOT / "data" / "literature" / "dedup_ris_index_meta.json"

RIS_TAGS = (
    ("DO", "doi"),
    ("AN", "an"),
    ("AB", "abstract"),
    ("PY", "year"),
    ("T2", "journal"),
    ("UR", "url"),
    ("AU", "authors"),
)


def find_dedup_ris_dir() -> Path:
    if DEFAULT_RIS_DIR.is_dir():
        return DEFAULT_RIS_DIR
    for p in ROOT.rglob("merged_corpus"):
        if p.is_dir() and list(p.glob("merged_*.ris")):
            return p
    raise FileNotFoundError(f"未找到去重 RIS 目录: {DEFAULT_RIS_DIR}")


def _parse_ris_record(rec: str) -> dict[str, str] | None:
    title_m = re.search(r"^TI  - (.+)$", rec, re.M)
    if not title_m:
        return None
    meta: dict[str, str] = {"title": title_m.group(1).strip()}
    for tag, key in RIS_TAGS:
        if tag == "AU":
            authors = re.findall(r"^AU  - (.+)$", rec, re.M)
            if authors:
                meta[key] = "; ".join(authors[:6])
            continue
        m = re.search(rf"^{tag}  - (.+)$", rec, re.M)
        if m:
            meta[key] = m.group(1).strip()
    return meta


def _merge_meta(old: dict[str, str], new: dict[str, str]) -> dict[str, str]:
    out = dict(old)
    for k, v in new.items():
        if v and not out.get(k):
            out[k] = v
    return out


def build_ris_dir_index(ris_dir: Path) -> dict[str, dict[str, str]]:
    idx: dict[str, dict[str, str]] = {}
    files = sorted(ris_dir.glob("merged_*.ris"))
    for fp in files:
        text = fp.read_text(encoding="utf-8", errors="replace")
        for rec in re.split(r"\n(?=TY  - )", text):
            meta = _parse_ris_record(rec)
            if not meta:
                continue
            key = meta["title"].lower()
            if key in idx:
                idx[key] = _merge_meta(idx[key], meta)
            else:
                idx[key] = meta
    return idx


def _dir_fingerprint(ris_dir: Path) -> dict[str, Any]:
    files = sorted(ris_dir.glob("merged_*.ris"))
    return {
        "ris_dir": str(ris_dir),
        "file_count": len(files),
        "newest_mtime": max((f.stat().st_mtime for f in files), default=0),
    }


def load_dedup_ris_index(*, rebuild: bool = False, ris_dir: Path | None = None) -> dict[str, dict[str, str]]:
    ris_dir = ris_dir or find_dedup_ris_dir()
    fp = _dir_fingerprint(ris_dir)
    if not rebuild and INDEX_PATH.is_file() and META_PATH.is_file():
        try:
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
            if meta.get("newest_mtime") == fp["newest_mtime"] and meta.get("file_count") == fp["file_count"]:
                return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    idx = build_ris_dir_index(ris_dir)
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    meta = {**fp, "records": len(idx), "with_doi": sum(1 for v in idx.values() if v.get("doi"))}
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return idx


def lookup_dedup_ris(title: str, idx: dict[str, dict[str, str]]) -> dict[str, str]:
    key = (title or "").strip().lower()
    if key in idx:
        return idx[key]
    for k, v in idx.items():
        if key[:50] in k or k[:50] in key:
            return v
    return {}
