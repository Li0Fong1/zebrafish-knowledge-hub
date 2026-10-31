# -*- coding: utf-8 -*-
"""DOI 可信度守卫：挡住「同一个 DOI 挂在多篇不同论文上」的错误外链。

背景（2026-10-10 数据库体检发现）
--------------------------------
`literature_paper` 里 **2,651 个 DOI 对应多行**，且那些行的题名/期刊/年份互不相同。
抽样看，重复得最狠的正是 Methods 里被引烂的经典方法学论文：

| DOI | 重复行数 | 典型题名（该 DOI 的真正主人） |
| --- | --- | --- |
| `10.1006/meth.2001.1262` | 22 | Livak & Schmittgen, 相对定量 2^-ΔΔCt |
| `10.1038/nprot.2007.514` | 18 | Nature Protocols 方法学 |
| `10.1038/nmeth.2019` | 14 | Nature Methods 工具学 |

说明这些行的 DOI 是从**全文的参考文献列表**里抓来的（抽错了），不是论文自身的 DOI。
后果很直接：文末参考文献的「原文」链接会指向**另一篇论文**，期刊补全也会跟着错。

做法
----
1. `scripts/build_doi_conflict_list.py` 扫库，把「一个 DOI 对应 >1 条不同题名」的 DOI
   写成小 JSON（默认几百 KB 以内，只存 DOI 字符串）。
2. 本模块在生成脚注/补刊名时对这些 DOI **拒绝信任**：
   - `_format_ref_line` 不再用它拼「[原文](doi)」，改走 PMID / 检索链接；
   - `resolve_journal` 不再拿它去查刊名。
3. 列表缺失或过期时**自动降级**（`available()=False`，一切照旧），不因守卫本身把功能打坏。

环境变量
--------
- `DOI_GUARD_DISABLE=1` 完全关闭守卫
- `DOI_GUARD_TTL_SEC` 缓存有效期（默认 600 s，文件变更后自动重载）
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

__all__ = ["is_conflicted", "available", "conflict_count", "source_path", "reload"]

_LOCK_TTL = 600.0
_state: dict = {"path": None, "mtime": 0.0, "loaded_at": 0.0, "set": None, "count": 0}


def _candidates() -> list[Path]:
    out: list[Path] = []
    for key in ("ZEBRA_RUNTIME", "ZEBRAFISH_RUNTIME"):
        v = (os.getenv(key) or "").strip()
        if v:
            out.append(Path(v) / "doi_conflicts.json")
    try:
        from config import _BASE_DIR  # type: ignore

        out.append(Path(_BASE_DIR) / "doi_conflicts.json")
        out.append(Path(_BASE_DIR) / "data" / "literature" / "doi_conflicts.json")
    except Exception:  # noqa: BLE001
        pass
    out.append(Path(__file__).resolve().parents[1] / "data" / "literature" / "doi_conflicts.json")
    uniq, seen = [], set()
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


def _norm(doi: str) -> str:
    d = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", (doi or "").strip()).strip(" .;")
    return d.lower()


def _load(force: bool = False) -> None:
    """按需加载；文件 mtime 变化或超过 TTL 就重载。"""
    if (os.getenv("DOI_GUARD_DISABLE") or "").strip() == "1":
        _state.update({"set": set(), "count": 0, "path": None})
        return
    now = time.time()
    if (
        not force
        and _state["set"] is not None
        and (now - _state["loaded_at"]) < _float_env("DOI_GUARD_TTL_SEC", _LOCK_TTL)
    ):
        return
    best: Path | None = None
    best_m = -1.0
    for p in _candidates():
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > best_m:
            best, best_m = p, m
    if best is None:
        _state.update({"set": set(), "count": 0, "path": None, "loaded_at": now})
        return
    try:
        raw = json.loads(best.read_text(encoding="utf-8", errors="replace"))
        dois = raw.get("dois") if isinstance(raw, dict) else raw
        s = {_norm(x) for x in (dois or []) if str(x).strip()}
    except Exception:  # noqa: BLE001
        s = set()
    _state.update({"path": str(best), "mtime": best_m, "loaded_at": now, "set": s, "count": len(s)})


def _float_env(key: str, default: float) -> float:
    try:
        return float((os.getenv(key) or "").strip())
    except (TypeError, ValueError):
        return default


def available() -> bool:
    _load()
    return bool(_state["set"])


def conflict_count() -> int:
    _load()
    return int(_state["count"] or 0)


def source_path() -> str:
    _load()
    return str(_state["path"] or "")


def reload() -> None:
    _load(force=True)


def is_conflicted(doi: str) -> bool:
    """该 DOI 是否属于「一 DOI 多论文」的不可信集合。"""
    _load()
    s = _state["set"] or set()
    if not s:
        return False
    d = _norm(doi)
    return bool(d) and d in s
