# -*- coding: utf-8 -*-
"""WoS RIS 文件目录发现（兼容 wos数据包/Downloads、WosRIS导入 等命名）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

from config import _BASE_DIR


def discover_wos_ris_dirs() -> List[str]:
    """返回可能存在 .ris/.ciw 的目录列表（按优先级）。"""
    seen: set[str] = set()
    ordered: List[str] = []

    def _add(path: str) -> None:
        norm = os.path.normpath(path)
        if norm in seen or not os.path.isdir(norm):
            return
        seen.add(norm)
        ordered.append(norm)

    _add(os.path.join(_BASE_DIR, "data", "wos_import"))
    _add(os.path.join(_BASE_DIR, "wos数据包", "Downloads"))
    _add(os.path.join(_BASE_DIR, "wos数据包"))

    root = Path(_BASE_DIR)
    archive = root / "项目阶段" / "05_数据与库" / "归档"
    _add(str(archive / "WosRIS导入"))
    bundle_ris = (
        archive
        / "data_export"
        / "zebrafish_gene_complete_20260625_1218"
        / "02_literature"
        / "wos_ris"
    )
    if bundle_ris.is_dir():
        _add(str(bundle_ris))

    # 根目录遗留命名 + 阶段5归档目录下的 wos*ris* 文件夹
    scan_roots = [root]
    if archive.is_dir():
        scan_roots.append(archive)
    for base in scan_roots:
        for p in sorted(base.iterdir()):
            if not p.is_dir():
                continue
            name = p.name.lower()
            if "wos" in name and "ris" in name:
                _add(str(p))

    return ordered


def list_wos_ris_files(dirs: List[str] | None = None) -> List[str]:
    """列出目录内全部 RIS/CIW 文件（去重、排序）。"""
    files: List[str] = []
    seen: set[str] = set()
    for directory in dirs or discover_wos_ris_dirs():
        if not os.path.isdir(directory):
            continue
        for fn in sorted(os.listdir(directory)):
            if not fn.lower().endswith((".ris", ".ciw")):
                continue
            path = os.path.join(directory, fn)
            if path in seen:
                continue
            seen.add(path)
            files.append(path)
    return files
