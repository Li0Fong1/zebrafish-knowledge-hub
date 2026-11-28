# -*- coding: utf-8 -*-
"""英文输出清洗：把混进英文回答里的零星中文与全角标点确定性地换成英文。

为什么需要它
------------
``_english_leak_guard`` 只在「中文占比 ≥15%」时整段翻译，用来兜「整段中文」。
但实测 lang=en 时还有一类**稀疏泄漏**，几个字散在几千字英文里，占比远低于阈值，
整段翻译不会触发，回答却明显不纯：

  · 后端若干标签是硬编码中文、不随 lang 变化
    （``gene_formatter`` 的「查询输入」、``answer_enrichment`` 的「标准名」等）；
  · 模型自行给分期/组织补中文注释（``256-cell·囊胚``、``50% epiboly·原肠胚``）；
  · 中文标点跟着术语一起带出来（``、`` ``：`` ``（``）。

设计原则
--------
1. **只替换已知术语**：来源是 ``data/anatomy_term_en.json``（41 组织 + 10 分期）
   加一张内置系统词表；不臆造翻译，避免把数据改错。
2. **未收录的中文原样保留**，并把残留片段还给调用方记日志，方便人工补词表。
3. **全角标点一律转半角**，英文段落里不该出现 ``、`` ``：`` ``（``。
4. 幂等：对已是纯英文的文本零改动。

用法
----
    from en_output import sanitize_en
    text, leftovers = sanitize_en(text)
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import List, Tuple

_CJK = re.compile(r"[\u4e00-\u9fff]+")

_LOCK = threading.Lock()
_CACHE: dict | None = None
_MTIME: float = 0.0
_RX: re.Pattern | None = None
_MAP: dict = {}

# 后端硬编码中文标签 / 工具提示词 —— 逐条对照实际输出整理
EXTRA_TERMS = {
    "查询输入": "query input",
    "标准名": "canonical symbol",
    "别名": "alias",
    "未找到基因": "gene not found",
    "启动子区序列获取失败": "promoter sequence retrieval failed",
    "序列获取失败": "sequence retrieval failed",
    "无记录": "no record",
    "本地库": "local library",
    "暂无数据": "no data",
    "部分匹配": "partial match",
    "精确匹配": "exact match",
    "转录本": "transcript",
    "染色体": "chromosome",
    "表达量": "expression level",
    "表型": "phenotype",
    "同源基因": "ortholog",
    # UI 提示语（补丁已让源头走 _t()，这里留作二道防线）
    "位置查序列": "sequence by region",
    "查序列": "get sequence",
    "序列工具": "sequence tool",
    "打开本系统序列工具": "open the sequence tool",
}

# 全角 → 半角（含中文标点）。键是不含正则元字符的普通字符，直接 str.translate。
_PUNCT = {
    "，": ", ", "、": ", ", "；": "; ", "：": ": ", "。": ". ",
    "（": " (", "）": ") ", "！": "! ", "？": "? ",
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "【": " [", "】": "] ", "《": " <", "》": "> ",
    "　": " ",
}
_PUNCT_TABLE = str.maketrans(_PUNCT)


def _data_path() -> str:
    try:
        from config import _BASE_DIR  # type: ignore

        base = _BASE_DIR
    except Exception:  # noqa: BLE001
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "data", "anatomy_term_en.json")


def _load() -> dict:
    """读术语表（mtime 失效，改 JSON 不必重启）。缺文件时只用内置词表。"""
    global _CACHE, _MTIME, _RX, _MAP
    path = _data_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    with _LOCK:
        if _CACHE is not None and mtime == _MTIME:
            return _CACHE
        table: dict = {}
        try:
            with open(path, encoding="utf-8-sig") as fh:
                raw = json.load(fh)
            for kind in ("tissue", "stage"):
                for k, v in (raw.get(kind) or {}).items():
                    k, v = str(k).strip(), str(v).strip()
                    if k and v and _CJK.search(k):
                        table[k] = v
        except Exception:  # noqa: BLE001 - 术语表缺失不应影响回答
            pass
        for k, v in EXTRA_TERMS.items():
            table.setdefault(k, v)
        keys = sorted(table, key=len, reverse=True)
        _MAP = table
        _RX = re.compile("|".join(re.escape(k) for k in keys)) if keys else None
        _CACHE = table
        _MTIME = mtime
        return _CACHE


def sanitize_en(text: str) -> Tuple[str, List[str]]:
    """把英文回答里的零星中文/全角标点换成英文。

    返回 ``(清洗后文本, 未收录的中文片段列表)``。片段非空说明词表需要补条目，
    调用方应记日志；文本本身不做破坏性处理（未收录内容原样保留）。
    """
    if not text:
        return text, []
    _load()
    out = text
    if _RX is not None:
        out = _RX.sub(lambda m: _MAP.get(m.group(0), m.group(0)), out)

    # 全角标点转半角；顺便收拾替换后留下的多余空格
    out = out.translate(_PUNCT_TABLE)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.;:!?)\]])", r"\1", out)
    out = re.sub(r"([(\[]) +", r"\1", out)
    out = re.sub(r"[ \t]+$", "", out, flags=re.M)

    leftovers = []
    seen = set()
    for m in _CJK.finditer(out):
        run = m.group(0)
        if run in seen:
            continue
        seen.add(run)
        leftovers.append(run)
    return out, leftovers


def __main__test() -> None:  # pragma: no cover - 手工自测
    cases = [
        "Adult tissues include 成体·胸腺 and 精巢.",
        "RefSeq GCF_049306965.2 标准名: mitfa",
        "1. p53 (查询输入)",
        "256-cell·囊胚 210.5 TPM、50% epiboly·原肠胚 185.3 TPM",
        "promoter: 未找到基因 shha（启动子区序列获取失败）",
        "No Chinese here at all.",
    ]
    for c in cases:
        t, left = sanitize_en(c)
        print("IN :", c)
        print("OUT:", t, "| 残留:", left)


if __name__ == "__main__":  # pragma: no cover
    __main__test()
