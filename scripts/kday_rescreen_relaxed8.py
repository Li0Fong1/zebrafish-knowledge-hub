# -*- coding: utf-8 -*-
"""
无法判别8 · 放宽策略三档重筛。

档位：
  kday判别为是  — 较明确以斑马鱼为实验/研究主体
  kday待核      — 证据不足但值得人工留下（提及 ZF / 鱼类实验 / 摘要残缺风险）
  kday判别为否  — 几乎可排除斑马鱼主体

用法：
  python -m scripts.kday_rescreen_relaxed8
"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from scripts.kday_judge_undetermined_md import parse_entries
from scripts.judge_literature_zebrafish_batch import _llm_judge_inventory
from scripts.judge_hybrid_triage import triage_zebrafish

SRC = ROOT / "results_md" / "无法判别8.md"
OUT_MD = ROOT / "results_md" / "无法判别8_kday.md"
OUT_JSONL = ROOT / "results_md" / "无法判别8_kday.jsonl"
OUT_REPORT = ROOT / "results_md" / "无法判别8_kday_relaxed_report.md"
OUT_STRATEGY = ROOT / "results_md" / "无法判别8_放宽策略说明.md"

ZF = re.compile(r"zebrafish|Danio\s*rerio|斑马鱼", re.I)
FISH = re.compile(
    r"\b(fish|teleost|medaka|danio|larva|embryo)\b|鱼类|硬骨鱼|鱼卵|幼鱼|鱼苗|鳍|"
    r"模式生物|animal model|model organism",
    re.I,
)
NEG_ZF = re.compile(r"未(特指|明确)?提及斑马鱼|未涉及斑马鱼|非斑马鱼|不是斑马鱼")
OTHER_PRIMARY = re.compile(
    r"\b(mouse|mice|murine|human|patient|rat|rats|drosophila|c\.\s*elegans|"
    r"xenopus|chick|chicken|arabidopsis|yeast|medaka|goldfish|carp|trout|"
    r"salmon|tilapia)\b|小鼠|大鼠|人类|临床|果蝇|拟南芥|金鱼|鲤鱼|鲑鱼|罗非鱼",
    re.I,
)
ZF_EXPERIMENT = re.compile(
    r"(in|using|of|with)\s+zebrafish|"
    r"zebrafish\s+(embryo|larva|embryos|larvae|model|were|was|fish)|"
    r"斑马鱼(胚胎|幼鱼|成鱼|模型|中|上)|"
    r"以斑马鱼为",
    re.I,
)


def _text(e: dict[str, str]) -> tuple[str, str, str]:
    title = " | ".join(x for x in [e.get("EN Title") or "", e.get("ZH Title") or ""] if x)
    parts = []
    for k in ("EN Abstract", "ZH Abstract"):
        v = (e.get(k) or "").strip()
        if v and v not in ("无摘要", "无", "-"):
            parts.append(v)
    abstract = "\n".join(parts)
    return title, abstract, f"{title}\n{abstract}"


def rule_bucket(e: dict[str, str]) -> tuple[str, str, str]:
    """返回 (label, reason, mode)。label 为三档之一。"""
    title, abstract, blob = _text(e)
    en = e.get("EN Abstract") or ""
    zh = e.get("ZH Abstract") or ""
    reason0 = e.get("Reason") or ""

    has_zf = bool(ZF.search(blob))
    has_fish = bool(FISH.search(blob))
    neg = bool(NEG_ZF.search(zh) or NEG_ZF.search(reason0))
    truncated = len(en) > 80 and not en.rstrip().endswith((".", "!", "?", "。"))
    no_abs = en in ("", "无摘要")
    other = bool(OTHER_PRIMARY.search(title + " " + en)) and not ZF_EXPERIMENT.search(blob)

    # 1) 强信号 → 是
    if ZF_EXPERIMENT.search(blob) or ZF.search(title):
        # 标题含 zebrafish 通常主体；实验表述也是
        if ZF.search(title) or ZF_EXPERIMENT.search(en) or re.search(r"斑马鱼(胚胎|幼鱼|模型|中)", zh):
            return "kday判别为是", "规则：标题或摘要出现斑马鱼实验/主体表述", "rule_yes"

    # 2) 明确否定且无正面实验表述 → 否
    if neg and not ZF_EXPERIMENT.search(en) and not ZF.search(title):
        # 中文写「未提及」且英文也无 zebrafish
        if not ZF.search(en):
            return "kday判别为否", "规则：原文写明未提及/未特指斑马鱼，且无英文斑马鱼实验信号", "rule_no_neg"

    # 3) 完全无鱼/斑马鱼 → 否
    if not has_zf and not has_fish:
        return "kday判别为否", "规则：标题摘要无斑马鱼/鱼类相关信号", "rule_no_empty"

    # 4) 其它物种为主且仅顺带可能提鱼 → 偏否，但有 ZF 字样则待核
    if other and has_zf and not ZF_EXPERIMENT.search(blob):
        return "kday待核", "规则：似有其它主体，但出现斑马鱼提及，留给人工", "rule_review_mention"
    if other and not has_zf:
        return "kday判别为否", "规则：更像其它物种主体，无斑马鱼字样", "rule_no_other"

    # 5) 有斑马鱼提及（并列/综述）→ 待核（放宽：不直接否）
    if has_zf:
        return "kday待核", "规则：出现斑马鱼字样但尚不足以确认主体，放宽留下待核", "rule_review_zf"

    # 6) 鱼类实验/胚胎/摘要残缺 → 待核
    if has_fish and (no_abs or truncated or "信息不足" in reason0 or "摘要" in reason0):
        return "kday待核", "规则：鱼类相关且摘要缺失/截断，有错杀风险，待核", "rule_review_fish_gap"
    if has_fish:
        return "kday待核", "规则：鱼类/模式生物相关，物种未锁死斑马鱼，待核", "rule_review_fish"

    return "kday判别为否", "规则：默认排除", "rule_no_default"


def llm_refine_review(e: dict[str, str], rule_label: str) -> dict[str, Any]:
    """对待核档做 LLM：可升为是、保持待核、降为否。"""
    title, abstract, _ = _text(e)
    prompt_abs = (
        "【放宽任务】三选一输出判断结果字段：是 / 待核 / 否\n"
        "是：实验/模型/主要观察对象是斑马鱼。\n"
        "待核：可能相关（提及斑马鱼、鱼类实验未写清物种、摘要太短），值得人工看。\n"
        "否：基本可排除斑马鱼主体。\n"
        "宁可待核，不要把可疑篇打成否。\n\n"
        f"{abstract or '无摘要'}"
    )
    try:
        _t, summary, judgement, reason = _llm_judge_inventory(title, prompt_abs)
    except Exception as exc:
        return {
            "entry_no": e["entry_no"],
            "en_title": e.get("EN Title") or "",
            "kday_label": rule_label,
            "judgement": "待核",
            "reason": f"LLM失败保留待核：{exc}"[:120],
            "mode": "review_llm_fail",
            "summary_zh": e.get("ZH Abstract") or "",
        }

    j = (judgement or "").strip()
    if j == "是":
        label = "kday判别为是"
    elif j in ("否",):
        # 放宽：规则已待核时，LLM 否 → 仍待核（除非理由极硬）
        hard_no = bool(re.search(r"未涉及|无关|小鼠|人类临床|完全没有", reason or ""))
        if rule_label == "kday待核" and not hard_no:
            label = "kday待核"
            reason = (reason or "") + "；放宽策略下保留待核"
        else:
            label = "kday判别为否"
    else:
        label = "kday待核"
        if j not in ("待核", "无法判别"):
            reason = (reason or j or "") + "；归待核"

    return {
        "entry_no": e["entry_no"],
        "en_title": e.get("EN Title") or "",
        "kday_label": label,
        "judgement": {"kday判别为是": "是", "kday判别为否": "否"}.get(label, "待核"),
        "reason": reason or "",
        "mode": "relaxed_llm",
        "summary_zh": summary or e.get("ZH Abstract") or "",
        "rule_seed": rule_label,
    }


def render_entry(entry: dict[str, str], judged: dict[str, Any]) -> str:
    title_zh = entry.get("ZH Title") or entry.get("heading") or judged.get("en_title") or ""
    zh_abs = judged.get("summary_zh") or entry.get("ZH Abstract") or ""
    return "\n".join(
        [
            f"### {entry['entry_no']}. {title_zh}",
            "",
            "| Field | Content |",
            "|------|------|",
            f"| EN Title | {entry.get('EN Title') or judged.get('en_title') or ''} |",
            f"| ZH Title | {title_zh} |",
            f"| ZH Abstract | {zh_abs} |",
            f"| EN Abstract | {entry.get('EN Abstract') or ''} |",
            f"| Judgment | {judged['kday_label']} |",
            f"| Reason | {judged.get('reason') or ''} |",
            f"| Mode | {judged.get('mode') or ''} |",
            "---",
            "",
        ]
    )


def main() -> int:
    entries = parse_entries(SRC.read_text(encoding="utf-8"))
    safe_print(f"放宽重筛 {len(entries)} 条…")

    # Pass1 rules
    seeded: dict[str, dict[str, Any]] = {}
    review_entries: list[dict[str, str]] = []
    for e in entries:
        label, reason, mode = rule_bucket(e)
        rec = {
            "entry_no": e["entry_no"],
            "en_title": e.get("EN Title") or "",
            "kday_label": label,
            "judgement": {"kday判别为是": "是", "kday判别为否": "否"}.get(label, "待核"),
            "reason": reason,
            "mode": mode,
            "summary_zh": e.get("ZH Abstract") or "",
        }
        # hybrid clear_yes boost
        tier, rj, rr = triage_zebrafish(
            (e.get("EN Title") or "") + " " + (e.get("ZH Title") or ""),
            (e.get("EN Abstract") or "") + "\n" + (e.get("ZH Abstract") or ""),
        )
        if tier == "clear_yes":
            rec.update(
                {
                    "kday_label": "kday判别为是",
                    "judgement": "是",
                    "reason": rr or reason,
                    "mode": "hybrid_clear_yes",
                }
            )
            label = "kday判别为是"
        seeded[str(e["entry_no"])] = rec
        if label == "kday待核":
            review_entries.append(e)

    safe_print(f"规则后待核 {len(review_entries)}，送 LLM 细分…")

    # Pass2 LLM on 待核 only (cap workers)
    t0 = time.time()
    if review_entries:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {
                pool.submit(llm_refine_review, e, seeded[str(e["entry_no"])]["kday_label"]): e
                for e in review_entries
            }
            for i, fut in enumerate(as_completed(futs), 1):
                r = fut.result()
                seeded[str(r["entry_no"])] = r
                if i <= 5 or i % 30 == 0:
                    safe_print(f"[{i}/{len(review_entries)}] #{r['entry_no']} {r['kday_label']}")

    # stats
    from collections import Counter

    ctr = Counter(r["kday_label"] for r in seeded.values())
    yes_n, rev_n, no_n = ctr.get("kday判别为是", 0), ctr.get("kday待核", 0), ctr.get("kday判别为否", 0)

    with OUT_JSONL.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(seeded[str(e["entry_no"])], ensure_ascii=False) + "\n")

    header = (
        f"# kday 放宽三档判别 · 无法判别8.md\n\n"
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}\n"
        f"- 总量：{len(entries)}\n"
        f"- **kday判别为是：{yes_n}**\n"
        f"- **kday待核：{rev_n}**（建议人工优先看）\n"
        f"- **kday判别为否：{no_n}**\n"
        f"- 耗时：{time.time() - t0:.0f}s\n\n---\n\n"
    )
    chunks = [header]
    # 是、待核靠前
    order = {"kday判别为是": 0, "kday待核": 1, "kday判别为否": 2}
    sorted_entries = sorted(entries, key=lambda e: (order.get(seeded[str(e["entry_no"])]["kday_label"], 9), int(e["entry_no"])))
    for e in sorted_entries:
        chunks.append(render_entry(e, seeded[str(e["entry_no"])]))
    OUT_MD.write_text("".join(chunks), encoding="utf-8", newline="\n")

    # strategy + report
    strategy = f"""# 无法判别8 · 放宽筛选策略

## 为什么要放宽

严标准（必须「研究主体=斑马鱼」）在本批上几乎全否，但摘要截断/无摘要会导致**错杀**。
目标改为：**高置信留下 + 可疑留下待核 + 明确排除**，宁可人工多看，少漏斑马鱼主体文献。

## 三档定义

| 标记 | 含义 | 处理建议 |
|------|------|----------|
| **kday判别为是** | 标题/摘要有较明确斑马鱼实验或主体表述 | 可直接进下游，抽检即可 |
| **kday待核** | 提及斑马鱼，或鱼类实验未锁物种，或摘要残缺有风险 | **人工优先复核**（看全文/完整摘要） |
| **kday判别为否** | 无鱼/斑马鱼信号，或写明未提及，或其它物种为主体 | 默认可丢弃，抽 1%–2% 质检 |

## 规则要点（Pass1）

1. 标题含 zebrafish / 实验句式（in/using zebrafish、斑马鱼胚胎等）→ **是**
2. 中文写明「未提及斑马鱼」且英文无 ZF → **否**
3. 完全无鱼/ZF 信号 → **否**
4. 出现斑马鱼字样但不够主体 → **待核**（不再直接否）
5. 鱼类/胚胎相关 + 摘要缺失或截断 → **待核**

## LLM（Pass2，仅待核）

- 可升为「是」、维持「待核」、或硬降「否」
- **偏置：宁可待核，不把可疑打成否**

## 本批结果

- 是：{yes_n}
- 待核：{rev_n}
- 否：{no_n}
- 留下合计（是+待核）：{yes_n + rev_n} / {len(entries)} = {(yes_n + rev_n) / max(len(entries), 1) * 100:.1f}%

## 后续建议

1. 人工只看 **待核**（及抽检是）
2. 待核里用 DOI/标题补全文或完整摘要后再终判
3. 同样策略可复制到无法判别9/10
"""
    OUT_STRATEGY.write_text(strategy, encoding="utf-8", newline="\n")
    OUT_REPORT.write_text(
        f"# 放宽重筛汇报\n\n- 是 {yes_n}\n- 待核 {rev_n}\n- 否 {no_n}\n"
        f"- 留下 {yes_n + rev_n}\n- 详见 `{OUT_STRATEGY.name}`\n",
        encoding="utf-8",
        newline="\n",
    )
    safe_print(f"完成：是 {yes_n} · 待核 {rev_n} · 否 {no_n} → {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
