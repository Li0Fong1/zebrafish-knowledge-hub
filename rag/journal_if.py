# -*- coding: utf-8 -*-
"""期刊 IF / tier 查表与场景权重（检索 blend 用）。"""
from __future__ import annotations

import math
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

# 与方案一致：未知刊不惩罚
UNKNOWN_SCORE = float(os.getenv("IF_UNKNOWN_SCORE", "0.45"))
IF_NORM_CAP = float(os.getenv("IF_NORM_CAP", "40"))

TIER_BOOST = {
    "T0": 1.00,
    "T1": 0.88,
    "T2": 0.68,
    "T3": 0.52,
    "T4": 0.45,
}

# IF 必须偏小：本库多数证据来自 Development / eLife / 斑马鱼专刊；
# Nature/Science 篇数少，且 CE 相关分也可能偏低，IF 一大就会把离题高刊顶上来。
# 硬顶 0.06。Nature vs Development 的 if_score 差约 0.4，×0.04 只动 blend 0.016。
IF_WEIGHT_CAP = float(os.getenv("PUBMED_YES_IF_WEIGHT_CAP", "0.06"))
BLEND_PROFILES: dict[str, dict[str, float]] = {
    "default": {"rel": 0.57, "year": 0.39, "if": 0.04},
    "trait": {"rel": 0.54, "year": 0.42, "if": 0.04},
    "fact": {"rel": 0.63, "year": 0.33, "if": 0.04},
    "mechanism": {"rel": 0.56, "year": 0.40, "if": 0.04},
    "recency": {"rel": 0.50, "year": 0.46, "if": 0.04},
    "classic": {"rel": 0.62, "year": 0.34, "if": 0.04},
}

GAMMA = {"classic": 0.70, "mechanism": 0.55}

# Nature / Science / Cell 精确键，避免子刊误判
_T0_EXACT = frozenset(
    {
        "nature",
        "science",
        "cell",
        "nature methods",
        "nature genetics",
        "nature cell biology",
        "nature neuroscience",
        "nature biotechnology",
        "lancet",
        "the lancet",
        "nejm",
        "new england journal of medicine",
    }
)


@dataclass(frozen=True)
class JournalIF:
    journal_key: str
    tier: str
    tier_boost: float
    if_norm: float
    if_value: Optional[float] = None

    @property
    def if_score(self) -> float:
        """排序用对数影响因子，不用档位定值。

        if_norm 已是 log1p(IF) / log1p(上限)。有实测 IF 时只用这一项。
        没有 IF 时才退回未知刊常数，避免无刊名的文献被罚成 0。
        """
        if self.if_value is not None and self.if_value >= 0:
            return self.if_norm
        return UNKNOWN_SCORE


UNKNOWN_IF = JournalIF("unknown", "T4", UNKNOWN_SCORE, UNKNOWN_SCORE, None)

_CACHE: dict[str, JournalIF] | None = None


def if_weight_enabled() -> bool:
    return os.getenv("PUBMED_YES_IF_WEIGHT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def norm_journal(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    for suf in (" ltd", " inc", " the ", " journal of ", " journal "):
        s = s.replace(suf, " ")
    s = re.sub(r"\s+", " ", s).strip()
    # 去掉前导 the
    if s.startswith("the "):
        s = s[4:].strip()
    return s


def if_norm_from_value(if_value: Optional[float]) -> float:
    if if_value is None or if_value < 0:
        return UNKNOWN_SCORE
    return max(0.0, min(1.0, math.log1p(float(if_value)) / math.log1p(IF_NORM_CAP)))


def tier_from_if(if_value: Optional[float], journal_key: str = "") -> str:
    key = norm_journal(journal_key)
    if key in _T0_EXACT:
        return "T0"
    if if_value is None:
        return "T4"
    v = float(if_value)
    if v >= 20:
        return "T0"
    if v >= 8:
        return "T1"
    if v >= 3:
        return "T2"
    if v >= 1:
        return "T3"
    return "T4"


def detect_rank_profile(question: str, answer_type: str = "") -> str:
    q = question or ""
    if re.search(r"最新|近年|近十年|最近|进展|新发现", q):
        return "recency"
    if re.search(r"经典|奠基|开创|首次|里程碑|landmark|seminal", q, re.I):
        return "classic"
    if re.search(r"机制|通路|原理|如何|为什么|作用机制|信号", q):
        return "mechanism"
    at = (answer_type or "").strip().lower()
    if at == "fact":
        return "fact"
    if at == "trait":
        return "trait"
    return "default"


def blend_weights(profile: str) -> dict[str, float]:
    """环境变量可覆盖 default；IF 一律封顶，避免 classic/mechanism 把期刊分做主排序。"""
    base = dict(BLEND_PROFILES.get(profile) or BLEND_PROFILES["default"])
    if profile == "default":
        try:
            wr = float(os.getenv("PUBMED_YES_BLEND_W_REL", str(base["rel"])))
            wy = float(os.getenv("PUBMED_YES_BLEND_W_YEAR", str(base["year"])))
            wi = float(os.getenv("PUBMED_YES_BLEND_W_IF", str(base["if"])))
            base = {"rel": wr, "year": wy, "if": wi}
        except (TypeError, ValueError):
            pass
    wi = min(float(base.get("if") or 0.0), IF_WEIGHT_CAP)
    wr = float(base.get("rel") or 0.0)
    wy = float(base.get("year") or 0.0)
    s = wr + wy + wi
    if s <= 0:
        return dict(BLEND_PROFILES["default"])
    return {"rel": wr / s, "year": wy / s, "if": wi / s}


def _load_cache() -> dict[str, JournalIF]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    out: dict[str, JournalIF] = {}
    try:
        from mysql_client import get_mysql_client

        table = os.getenv("JOURNAL_METRIC_TABLE", "journal_metric")
        sql = (
            f"SELECT journal_key, tier, tier_boost, if_norm, if_value "
            f"FROM {table}"
        )
        rows = get_mysql_client().execute_sql(sql, max_rows=5000)
        for r in (rows or {}).get("data") or []:
            key = norm_journal(str(r.get("journal_key") or ""))
            if not key:
                continue
            tier = str(r.get("tier") or "T4").upper()
            if tier not in TIER_BOOST:
                tier = "T4"
            try:
                tb = float(r.get("tier_boost") if r.get("tier_boost") is not None else TIER_BOOST[tier])
            except (TypeError, ValueError):
                tb = TIER_BOOST[tier]
            try:
                inn = float(r.get("if_norm") if r.get("if_norm") is not None else UNKNOWN_SCORE)
            except (TypeError, ValueError):
                inn = UNKNOWN_SCORE
            try:
                iv = float(r["if_value"]) if r.get("if_value") is not None else None
            except (TypeError, ValueError):
                iv = None
            out[key] = JournalIF(key, tier, tb, inn, iv)
    except Exception:
        out = {}
    _CACHE = out
    return out


def reload_cache() -> None:
    global _CACHE
    _CACHE = None
    _load_cache()


def lookup_if(journal: str) -> JournalIF:
    key = norm_journal(journal)
    if not key:
        return UNKNOWN_IF
    cache = _load_cache()
    if key in cache:
        return cache[key]
    # 前缀/包含弱匹配（避免 Nature Communications 命中 nature）
    if key in _T0_EXACT:
        return JournalIF(key, "T0", TIER_BOOST["T0"], if_norm_from_value(40.0), 40.0)
    for ck, ji in cache.items():
        if len(ck) >= 8 and (ck in key or key in ck):
            return ji
    return UNKNOWN_IF


def lookup_if_batch(journals: list[str]) -> dict[str, JournalIF]:
    return {j: lookup_if(j) for j in journals if j is not None}
