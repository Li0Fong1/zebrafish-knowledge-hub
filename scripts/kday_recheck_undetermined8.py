# -*- coding: utf-8 -*-
"""复查无法判别8中可能被错杀为「否」的条目。"""
from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.kday_judge_undetermined_md import parse_entries, to_kday_label
from scripts.judge_literature_zebrafish_batch import _llm_judge_inventory


SRC = ROOT / "results_md" / "无法判别8.md"
JSONL = ROOT / "results_md" / "无法判别8_kday.jsonl"
OUT_AUDIT = ROOT / "results_md" / "无法判别8_kday_recheck.jsonl"
OUT_MD = ROOT / "results_md" / "无法判别8_kday_recheck_report.md"

PROMPT_EXTRA = (
    "【复查重点】二次复核，防止把「以斑马鱼为实验主体」的文章错判为否。"
    "若实验/模型明确在斑马鱼胚胎或成鱼上开展 → 判「是」。"
    "若仅并列举例、EST比对、综述泛提多种模式生物 → 判「否」。"
    "证据不足也判「否」，理由写明不确定。"
)

zf = re.compile(r"zebrafish|Danio\s*rerio|斑马鱼", re.I)
fish = re.compile(r"\b(fish|teleost|medaka|danio)\b|鱼类|硬骨鱼|鱼卵|幼鱼|模式生物", re.I)
neg = re.compile(r"未(特指|明确)?提及斑马鱼|未涉及斑马鱼|非斑马鱼")


def score_entry(e: dict) -> tuple[int, list[str]]:
    title = " ".join([e.get("EN Title") or "", e.get("ZH Title") or ""])
    en = e.get("EN Abstract") or ""
    zh = e.get("ZH Abstract") or ""
    reason = e.get("Reason") or ""
    score, tags = 0, []
    if zf.search(title) or zf.search(en):
        score += 5
        tags.append("en_or_title_zf")
    if zf.search(zh) and not neg.search(zh):
        score += 4
        tags.append("zh_zf_positive")
    if fish.search(title) or fish.search(en) or fish.search(zh):
        score += 2
        tags.append("fish_related")
    if en in ("", "无摘要") or any(k in reason for k in ("摘要缺失", "无摘要", "信息不足")):
        if score >= 2 or fish.search(title):
            score += 2
            tags.append("missing_abs_risk")
    if re.search(r"包括斑马鱼|提及斑马鱼|斑马鱼等|和斑马鱼|与斑马鱼|涉及斑马鱼", zh):
        score += 3
        tags.append("zh_includes_zf")
    return score, tags


def rejudge(item: tuple) -> dict:
    s, tags, e = item
    title = " | ".join(x for x in [e.get("EN Title") or "", e.get("ZH Title") or ""] if x)
    abs_parts = []
    for k in ("EN Abstract", "ZH Abstract"):
        v = (e.get(k) or "").strip()
        if v and v not in ("无摘要", "无", "-"):
            abs_parts.append(v)
    abstract = "\n".join(abs_parts) or "无摘要"
    _t, summary, judgement, reason = _llm_judge_inventory(title, f"{PROMPT_EXTRA}\n\n{abstract}")
    if judgement not in ("是", "否"):
        judgement = "否"
        reason = (reason or "信息不足") + "；复查仍不足"
    return {
        "entry_no": e["entry_no"],
        "en_title": e.get("EN Title") or "",
        "score": s,
        "tags": tags,
        "orig_reason": e.get("Reason") or "",
        "judgement": judgement,
        "kday_label": to_kday_label(judgement),
        "reason": reason,
        "summary_zh": summary,
        "mode": "llm_recheck",
    }


def main() -> int:
    entries = parse_entries(SRC.read_text(encoding="utf-8"))
    done = {
        str(r["entry_no"]): r
        for r in (json.loads(l) for l in JSONL.open(encoding="utf-8") if l.strip())
    }
    cands = []
    for e in entries:
        s, tags = score_entry(e)
        if s >= 5:
            cands.append((s, tags, e))
    cands.sort(key=lambda x: -x[0])
    print(f"recheck_pool={len(cands)}")

    results = []
    flip_yes = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(rejudge, c) for c in cands]
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            r["prev_label"] = done.get(str(r["entry_no"]), {}).get("kday_label")
            results.append(r)
            if r["kday_label"] == "kday判别为是":
                flip_yes.append(r)
                print(f"FLIP_YES #{r['entry_no']} | {r['reason'][:80]}")
            if i <= 5 or i % 25 == 0:
                print(f"[{i}/{len(cands)}] #{r['entry_no']} {r['kday_label']} score={r['score']}")

    results.sort(key=lambda x: (-(x["kday_label"] == "kday判别为是"), -x["score"], int(x["entry_no"])))
    with OUT_AUDIT.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    yes_n = len(flip_yes)
    no_n = len(results) - yes_n
    lines = [
        "# 无法判别8 · kday 错杀复查",
        "",
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}",
        "- 全量原判：均为 kday判别为否（1336）",
        f"- 本次重点复查（风险分≥5）：**{len(results)}** 条",
        f"- 复查后改为 **kday判别为是：{yes_n}**",
        f"- 复查仍为否：{no_n}",
        f"- 耗时：{time.time() - t0:.0f}s",
        "",
        "## 结论",
        "",
    ]
    if yes_n == 0:
        lines += [
            "在风险最高的一批里，二次 LLM 复查仍未发现可确认「以斑马鱼为研究主体」的条目。",
            "此前「全否」**未发现明确错杀**；残留风险主要在无摘要/截断摘要（靠当前文件无法翻案）。",
            "",
            "## 高风险仍为否样例（前25）",
            "",
        ]
        for r in results[:25]:
            lines.append(
                f"- #{r['entry_no']} score={r['score']} | {r['reason'][:70]} | {(r.get('en_title') or '')[:55]}"
            )
    else:
        lines += ["以下由否翻是（建议人工复核）：", ""]
        for r in flip_yes:
            lines += [
                f"### #{r['entry_no']} {(r.get('en_title') or '')[:100]}",
                f"- 复查理由：{r.get('reason')}",
                f"- 原 Reason：{r.get('orig_reason')}",
                "",
            ]

    OUT_MD.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"DONE yes={yes_n} no={no_n}")
    print(f"report={OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
