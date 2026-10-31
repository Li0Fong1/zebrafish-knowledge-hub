# -*- coding: utf-8 -*-
"""科学正文清洗（对齐马铃薯：去作者/单位/参考文献/致谢等噪声）。"""
from __future__ import annotations

import re

_REF_START = re.compile(
    r"(?im)^\s*(references|bibliography|文献引用|参考文献|reference list)\s*$"
)
_ACK_START = re.compile(
    r"(?im)^\s*(acknowledgements?|acknowledgments?|致谢|funding|author contributions?)\s*$"
)
_AUTHOR_LINE = re.compile(
    r"(?im)^\s*(corresponding author|作者贡献|利益冲突|conflict of interest).{0,200}$"
)


def clean_scientific_text(text: str, *, drop_refs: bool = True) -> str:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not t.strip():
        return ""
    if drop_refs:
        m = _REF_START.search(t)
        if m and m.start() > len(t) * 0.35:
            t = t[: m.start()]
        m2 = _ACK_START.search(t)
        if m2 and m2.start() > len(t) * 0.25:
            # 致谢常在文末；若在后半则截断
            if m2.start() > len(t) * 0.5:
                t = t[: m2.start()]
    lines = []
    for ln in t.split("\n"):
        s = ln.strip()
        if _AUTHOR_LINE.match(s):
            continue
        lines.append(ln)
    t = "\n".join(lines)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()
