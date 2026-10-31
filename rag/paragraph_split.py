# -*- coding: utf-8 -*-
"""自然段切分（马铃薯论文最优策略）+ 过长段再按句切。"""
from __future__ import annotations

import re


def split_natural_paragraphs(
    text: str,
    *,
    max_chars: int = 360,
    min_chars: int = 110,
    overlap: int = 48,
) -> list[str]:
    """
    按空行自然段切分；过短合并，过长按句号再切，并带少量 overlap。

    默认短语义块试跑：max≈360（约 3～5 句中文 / 2～3 句英文），避免一块十几句稀释信息。
    """
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not t:
        return []
    raw = [p.strip() for p in re.split(r"\n\s*\n+", t) if p.strip()]
    if not raw:
        raw = [t]

    # 合并过短段
    merged: list[str] = []
    buf = ""
    for p in raw:
        if not buf:
            buf = p
        elif len(buf) < min_chars:
            buf = buf + "\n\n" + p
        else:
            merged.append(buf)
            buf = p
    if buf:
        merged.append(buf)

    out: list[str] = []
    for p in merged:
        if len(p) <= max_chars:
            out.append(p)
            continue
        # 按句切
        sentences = re.split(r"(?<=[。！？.!?;；])\s+", p)
        cur = ""
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if not cur:
                cur = s
            elif len(cur) + 1 + len(s) <= max_chars:
                cur = cur + " " + s
            else:
                out.append(cur)
                # overlap: 取末尾若干字符
                tail = cur[-overlap:] if overlap and len(cur) > overlap else ""
                cur = (tail + " " + s).strip() if tail else s
        if cur:
            out.append(cur)
    return out
