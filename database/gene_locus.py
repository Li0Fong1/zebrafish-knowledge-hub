# -*- coding: utf-8 -*-
"""基因坐标索引（GRCz11 / Ensembl release 116）。

为什么需要这个模块：
    `gene_basic` 表只有 `chromosome`，没有起止坐标 —— 于是「基因位置」页
    只能回答「在哪条染色体」，回答不了「在哪一段」，页面名和内容对不上。

    本地 `data/ensembl/gene_catalog.json`（32,520 条，含 start/end/strand/biotype）
    本来就是排序注释时导出的，坐标一直在，只是没有暴露给接口。这里做成只读索引，
    `/api/gene/{name}` 按需挂一个 `locus` 块。

口径必须写清楚：
    坐标是 **GRCz11**（Ensembl r116），而 BLAST / 取序列默认 GRCz12tu。
    两者不是同一套装配，跨装配不能直接拿去取序列 —— 要换版本得先走 Liftover。
    因此返回值里带 `assembly` 与 `assembly_note`，前端照实显示，不做静默换算。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"loaded": False, "by_name": {}, "by_symbol": {}, "by_ensembl": {},
                           "release": None, "count": 0, "path": None}


def _candidates() -> list[Path]:
    """按顺序找目录文件：环境变量 → 项目 data/ → 运行时目录。"""
    out: list[Path] = []
    env = os.getenv("GENE_CATALOG_PATH")
    if env:
        out.append(Path(env))
    root = Path(__file__).resolve().parents[1]
    out.append(root / "data" / "ensembl" / "gene_catalog.json")
    for k in ("ZEBRA_RUNTIME", "ZF_RUNTIME"):
        rt = os.getenv(k)
        if rt:
            out.append(Path(rt) / "data" / "ensembl" / "gene_catalog.json")
    return out


def _load() -> None:
    if _CACHE["loaded"]:
        return
    with _LOCK:
        if _CACHE["loaded"]:
            return
        path = next((p for p in _candidates() if p.is_file()), None)
        if path is None:
            _CACHE.update({"loaded": True, "count": 0, "path": None})
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            _CACHE.update({"loaded": True, "count": 0, "path": str(path)})
            return
        by_name: dict[str, dict] = {}
        by_symbol: dict[str, dict] = {}
        by_ensembl: dict[str, dict] = {}
        genes = raw.get("genes") if isinstance(raw, dict) else raw
        for g in genes or []:
            if not isinstance(g, dict):
                continue
            if g.get("start") is None or g.get("end") is None:
                continue
            name = str(g.get("gene_name") or "").strip()
            if name:
                by_name.setdefault(name.lower(), g)
            sym = str(g.get("gene_symbol") or "").strip()
            if sym:
                by_symbol.setdefault(sym.lower(), g)
            eid = str(g.get("ensembl_id") or "").strip()
            if eid:
                by_ensembl.setdefault(eid.upper(), g)
        _CACHE.update({
            "loaded": True,
            "by_name": by_name,
            "by_symbol": by_symbol,
            "by_ensembl": by_ensembl,
            "release": (raw.get("release") if isinstance(raw, dict) else None),
            "count": len(by_name),
            "path": str(path),
        })


def _pack(g: dict) -> dict:
    start, end = int(g["start"]), int(g["end"])
    return {
        "assembly": "GRCz11",
        "source": "Ensembl release %s" % (_CACHE.get("release") or "116"),
        "chromosome": str(g.get("chromosome") or ""),
        "start": start,
        "end": end,
        "length": end - start + 1,
        "strand": g.get("strand") or "",
        "biotype": g.get("biotype") or g.get("gene_function") or "",
        "ensembl_id": g.get("ensembl_id") or "",
        "gene_symbol": g.get("gene_name") or "",
        # 这是「没有 GRCz12tu 坐标时的回落」，必须说明它是哪一套坐标系
        "assembly_note": "该基因暂无 GRCz12tu 坐标，以下为 GRCz11 注释坐标（Ensembl r116），需 liftover 才能与 GRCz12tu 混用",
    }


def get_locus(name: str = "", ensembl_id: str = "") -> dict | None:
    """按符号 / 别名 / Ensembl ID 取坐标；找不到返回 None（调用方自己决定怎么显示）。"""
    _load()
    if not _CACHE["count"]:
        return None
    key = (name or "").strip()
    if key:
        g = _CACHE["by_name"].get(key.lower()) or _CACHE["by_symbol"].get(key.lower())
        if g:
            return _pack(g)
    eid = (ensembl_id or "").strip().upper()
    if eid:
        g = _CACHE["by_ensembl"].get(eid)
        if g:
            return _pack(g)
    return None


def status() -> dict:
    """给自检/审计用：索引是否加载、共多少条、用的哪个文件。"""
    _load()
    return {
        "ready": bool(_CACHE["count"]),
        "count": _CACHE["count"],
        "release": _CACHE["release"],
        "path": _CACHE["path"],
    }
