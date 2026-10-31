# -*- coding: utf-8 -*-
"""
全库判别混合分档：规则高置信预筛 + LLM 仅处理灰色地带。

- clear_yes / clear_no：规则已定 judgement，LLM 只做中译（省 token）
- gray：完整 LLM 判别（与 economy 模式标准一致）

默认开启：JUDGE_INVENTORY_HYBRID=1
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional, Tuple

from scripts.curate_literature_test_batch import (
    EXPERIMENT_ON_OTHER,
    OTHER_ORGANISMS,
    Article,
    ZF_AS_SUBJECT,
    ZF_RE,
    _count_mentions,
)

INVENTORY_HYBRID = os.getenv("JUDGE_INVENTORY_HYBRID", "1").strip() in ("1", "true", "yes")

TriageTier = Literal["clear_yes", "clear_no", "gray"]


def _signals(title: str, abstract: str) -> dict:
    combined = f"{title}\n{abstract}"
    return {
        "zf_in_title": bool(ZF_RE.search(title)),
        "zf_count": len(ZF_RE.findall(combined)),
        "subject_signals": len(ZF_AS_SUBJECT.findall(combined)),
        "other_count": _count_mentions(combined, OTHER_ORGANISMS),
        "experiment_on_other": bool(abstract and EXPERIMENT_ON_OTHER.search(abstract)),
        "is_review": bool(
            re.search(r"\breview\b|\bmeta-?analysis\b|\bbibliometric\b", combined, re.I)
        ),
    }


def triage_zebrafish(title: str, abstract: str) -> Tuple[TriageTier, Optional[str], str]:
    """
    返回 (tier, judgement, reason)。
    clear 档 judgement 为「是/否」；gray 档 judgement 为 None。
    """
    title = (title or "").strip()
    abstract = (abstract or "").strip()
    s = _signals(title, abstract)

    if s["zf_count"] == 0:
        return "clear_no", "否", "未涉及斑马鱼，研究对象非 Danio rerio"

    if s["zf_in_title"]:
        if not abstract:
            return "clear_yes", "是", "研究对象：标题明确为斑马鱼（仅元数据）"
        if s["experiment_on_other"] and s["subject_signals"] == 0 and s["zf_count"] <= 1:
            return "clear_no", "否", "实验主要在其它物种上进行，斑马鱼非研究对象"
        if s["subject_signals"] >= 1 and not (s["other_count"] >= 4 and s["zf_count"] < s["other_count"]):
            return "clear_yes", "是", "研究对象：标题以斑马鱼为主体"

    if s["subject_signals"] >= 1 or s["zf_count"] >= 2:
        if s["experiment_on_other"] and s["subject_signals"] == 0:
            return "clear_no", "否", "实验对象主要为其他物种"
        if s["other_count"] >= 4 and s["zf_count"] < s["other_count"]:
            return "clear_no", "否", "研究对象以其他模式生物为主"
        if s["subject_signals"] >= 2 or (s["subject_signals"] >= 1 and s["zf_count"] >= 2):
            return "clear_yes", "是", "研究对象：斑马鱼为实验模型/研究材料"

    if s["is_review"] and s["zf_count"] >= 2:
        return "gray", None, ""

    if s["zf_count"] == 1 and s["subject_signals"] == 0:
        return "gray", None, ""

    if s["zf_in_title"] and s["subject_signals"] == 0:
        return "gray", None, ""

    # 与 assess_zebrafish_primary 对齐的剩余明确否
    art = Article(
        path=Path("_triage.txt"),
        filename="_triage.txt",
        title=title,
        abstract=abstract,
    )
    from scripts.curate_literature_test_batch import assess_zebrafish_primary

    ok, reason = assess_zebrafish_primary(art)
    if not ok and s["zf_count"] <= 1 and s["subject_signals"] == 0:
        return "clear_no", "否", reason or "仅提及斑马鱼，研究对象非斑马鱼主体"

    return "gray", None, ""


@dataclass
class HybridStats:
    clear_yes: int = 0
    clear_no: int = 0
    gray: int = 0
    translate_calls: int = 0
    full_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, tier: TriageTier, *, translate: bool) -> None:
        with self._lock:
            if tier == "clear_yes":
                self.clear_yes += 1
            elif tier == "clear_no":
                self.clear_no += 1
            else:
                self.gray += 1
            if translate:
                self.translate_calls += 1
            else:
                self.full_calls += 1

    def to_dict(self) -> dict:
        with self._lock:
            total = max(self.clear_yes + self.clear_no + self.gray, 1)
            clear = self.clear_yes + self.clear_no
            return {
                "clear_yes": self.clear_yes,
                "clear_no": self.clear_no,
                "gray": self.gray,
                "translate_calls": self.translate_calls,
                "full_calls": self.full_calls,
                "clear_pct": round(100 * clear / total, 1),
            }
