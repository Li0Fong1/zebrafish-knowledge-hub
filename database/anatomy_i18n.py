# -*- coding: utf-8 -*-
"""斑马鱼解剖部位 / 胚胎分期的英文名映射（AO / Expression Atlas）。

背景：`gene_expression` 里 `data_source='atlas_embryo'` 的 `tissue` /
`expression_stage` 当年是以**中文**入库的（41 个组织 + 3 个分期），而 Bgee 一侧
落库就是英文。结果是英文界面下表达谱会出现「成体 / 胸腺」这种中文值 —— 表头是英文、
数据是中文，读起来割裂。

这里不改库、不重跑导入，只在**输出时**按 ZFA（Zebrafish Anatomy Ontology）的
规范英文名做一次确定性映射：
  · 命中的中文术语 → 英文名；
  · 未知术语 → 原样返回（绝不臆造翻译，避免把数据改错）；
  · 语言为 zh 时完全不动，中文路径逐字不变。

映射表放在 ``data/anatomy_term_en.json``，新增术语只需加一行，无需改代码。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Dict, Optional

_LOCK = threading.Lock()
_CACHE: Optional[dict] = None
_MTIME: float = 0.0


def _path() -> str:
    try:
        from config import _BASE_DIR  # type: ignore

        base = _BASE_DIR
    except Exception:  # noqa: BLE001
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "data", "anatomy_term_en.json")


def _load() -> dict:
    """读取映射表（带 mtime 失效，改 JSON 不用重启）。"""
    global _CACHE, _MTIME
    p = _path()
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return {"tissue": {}, "stage": {}}
    with _LOCK:
        if _CACHE is not None and mtime == _MTIME:
            return _CACHE
        try:
            with open(p, encoding="utf-8-sig") as fh:
                raw = json.load(fh)
        except Exception:  # noqa: BLE001 - 映射缺失不应影响表达谱本身
            return {"tissue": {}, "stage": {}}
        _CACHE = {
            "tissue": {str(k).strip(): str(v).strip() for k, v in (raw.get("tissue") or {}).items()},
            "stage": {str(k).strip(): str(v).strip() for k, v in (raw.get("stage") or {}).items()},
        }
        _MTIME = mtime
        return _CACHE


def to_en(term: str, kind: str = "tissue") -> str:
    """中文术语 → 英文名；未收录或已是英文则原样返回。"""
    t = (term or "").strip()
    if not t:
        return t
    table: Dict[str, str] = _load().get(kind) or {}
    return table.get(t, t)


def localize(term: str, lang: str = "zh", kind: str = "tissue") -> str:
    """按回答语言决定是否翻译：只有 en 才映射。"""
    if (lang or "zh").lower().startswith("en"):
        return to_en(term, kind=kind)
    return (term or "").strip()


def has_cjk(term: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in (term or ""))
