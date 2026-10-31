# -*- coding: utf-8 -*-
"""
对 results_md/无法判别N.md 做 kday AI 初筛。

标准（与全库一致）：是否以斑马鱼为研究主体/主要实验模型。
标记：
  - kday判别为是
  - kday判别为否
信息不足也归「否」（供人工复核）。

用法：
  python -m scripts.kday_judge_undetermined_md --input results_md/无法判别8.md --resume
"""
from __future__ import annotations

import argparse
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
from scripts.judge_hybrid_triage import triage_zebrafish
from scripts.judge_literature_zebrafish_batch import _llm_judge_inventory
from scripts.judge_rate_limit_guard import InsufficientBalanceError, is_balance_error, is_rate_limit_error

ENTRY_RE = re.compile(
    r"^###\s+(?P<no>\d+)\.\s+(?P<head>.+?)\s*$"
    r"(?P<body>.*?)"
    r"(?=^---\s*$|^###\s+\d+\.|\Z)",
    re.M | re.S,
)
FIELD_RE = re.compile(
    r"^\|\s*(?P<k>EN Title|ZH Title|ZH Abstract|EN Abstract|Judgment|Reason)\s*\|\s*(?P<v>.*?)\s*\|\s*$",
    re.M,
)


def parse_entries(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for m in ENTRY_RE.finditer(text):
        fields = {k: "" for k in ("EN Title", "ZH Title", "ZH Abstract", "EN Abstract", "Judgment", "Reason")}
        for fm in FIELD_RE.finditer(m.group("body")):
            fields[fm.group("k")] = fm.group("v").strip()
        entries.append({
            "entry_no": m.group("no"),
            "heading": m.group("head").strip(),
            "raw": m.group(0),
            **fields,
        })
    return entries


def to_kday_label(judgement: str) -> str:
    j = (judgement or "").strip()
    if j in ("是", "kday判别为是", "yes", "YES"):
        return "kday判别为是"
    return "kday判别为否"


def judge_one(entry: dict[str, str]) -> dict[str, Any]:
    title = " | ".join(
        x for x in [entry.get("EN Title") or "", entry.get("ZH Title") or entry.get("heading") or ""] if x
    )
    abs_parts: list[str] = []
    for k in ("EN Abstract", "ZH Abstract"):
        v = (entry.get(k) or "").strip()
        if v and v not in ("无摘要", "无", "-", "N/A", "n/a"):
            abs_parts.append(v)
    abstract = "\n".join(abs_parts)

    tier, rule_j, rule_reason = triage_zebrafish(title, abstract)
    if tier in ("clear_yes", "clear_no") and rule_j:
        label = to_kday_label(rule_j)
        return {
            "entry_no": entry["entry_no"],
            "en_title": entry.get("EN Title") or title,
            "judgement": rule_j,
            "kday_label": label,
            "reason": rule_reason,
            "mode": f"hybrid_{tier}",
            "summary_zh": entry.get("ZH Abstract") or "",
        }

    try:
        _title, summary, judgement, reason = _llm_judge_inventory(title, abstract or "无摘要")
        # 强制二值：无法判别 → 否（人工再核）
        if judgement not in ("是", "否"):
            reason = (reason or "信息不足") + "；AI归为否待人工复核"
            judgement = "否"
        return {
            "entry_no": entry["entry_no"],
            "en_title": entry.get("EN Title") or title,
            "judgement": judgement,
            "kday_label": to_kday_label(judgement),
            "reason": reason or "",
            "mode": "llm",
            "summary_zh": summary or entry.get("ZH Abstract") or "",
        }
    except Exception as e:
        if is_balance_error(e):
            raise InsufficientBalanceError(str(e)) from e
        return {
            "entry_no": entry["entry_no"],
            "en_title": entry.get("EN Title") or title,
            "judgement": "否",
            "kday_label": "kday判别为否",
            "reason": f"AI失败：{str(e)[:80]}",
            "mode": "error",
            "summary_zh": entry.get("ZH Abstract") or "",
            "error": str(e)[:200],
        }


def render_entry(entry: dict[str, str], judged: dict[str, Any]) -> str:
    title_zh = entry.get("ZH Title") or entry.get("heading") or judged.get("en_title") or ""
    zh_abs = judged.get("summary_zh") or entry.get("ZH Abstract") or ""
    lines = [
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
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="kday AI 判别无法判别N.md")
    ap.add_argument("--input", type=str, default="results_md/无法判别8.md")
    ap.add_argument("--output", type=str, default="")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    in_path = (ROOT / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input)
    out_path = Path(args.output) if args.output else in_path.with_name(in_path.stem + "_kday.md")
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    jsonl_path = out_path.with_suffix(".jsonl")
    progress_path = out_path.with_suffix(".progress.json")
    report_path = out_path.with_suffix(".report.md")

    text = in_path.read_text(encoding="utf-8")
    entries = parse_entries(text)
    if args.limit > 0:
        entries = entries[: args.limit]
    safe_print(f"输入 {in_path.name}：{len(entries)} 条 → {out_path.name}")

    done: dict[str, dict[str, Any]] = {}
    if args.resume and jsonl_path.is_file():
        for line in jsonl_path.open(encoding="utf-8"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if r.get("entry_no"):
                    done[str(r["entry_no"])] = r
            except json.JSONDecodeError:
                continue
        safe_print(f"断点续跑：已有 {len(done)} 条")

    todo = [e for e in entries if str(e["entry_no"]) not in done]
    t0 = time.time()
    yes_n = sum(1 for r in done.values() if r.get("kday_label") == "kday判别为是")
    no_n = sum(1 for r in done.values() if r.get("kday_label") == "kday判别为否")

    def _save_progress() -> None:
        progress_path.write_text(
            json.dumps(
                {
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "total": len(entries),
                    "done": len(done),
                    "yes": yes_n,
                    "no": no_n,
                    "remaining": len(entries) - len(done),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    if todo:
        with jsonl_path.open("a", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futs = {pool.submit(judge_one, e): e for e in todo}
            for i, fut in enumerate(as_completed(futs), 1):
                try:
                    r = fut.result()
                except InsufficientBalanceError as e:
                    safe_print(f"余额不足，停止：{e}")
                    break
                except Exception as e:
                    e0 = futs[fut]
                    r = {
                        "entry_no": e0["entry_no"],
                        "en_title": e0.get("EN Title") or "",
                        "judgement": "否",
                        "kday_label": "kday判别为否",
                        "reason": f"异常：{e}"[:120],
                        "mode": "error",
                    }
                    if is_rate_limit_error(e):
                        time.sleep(3)
                done[str(r["entry_no"])] = r
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                fh.flush()
                if r.get("kday_label") == "kday判别为是":
                    yes_n += 1
                else:
                    no_n += 1
                if i <= 5 or i % 25 == 0:
                    safe_print(
                        f"[{i}/{len(todo)}] #{r['entry_no']} {r['kday_label']} | {r.get('reason','')[:60]}"
                    )
                if i % 20 == 0:
                    _save_progress()

    _save_progress()

    # 写出完整 markdown（按原顺序）
    header = (
        f"# kday AI 判别 · {in_path.name}\n\n"
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}\n"
        f"- 总量：{len(entries)}\n"
        f"- kday判别为是：{yes_n}\n"
        f"- kday判别为否：{no_n}\n"
        f"- 标准：以斑马鱼为研究主体/主要实验模型 → 是；否则 → 否（信息不足归否，待人工复核）\n\n"
        f"---\n\n"
    )
    chunks = [header]
    for e in entries:
        j = done.get(str(e["entry_no"]))
        if not j:
            j = {
                "kday_label": "kday判别为否",
                "reason": "未完成判别",
                "mode": "pending",
                "summary_zh": e.get("ZH Abstract") or "",
                "en_title": e.get("EN Title") or "",
            }
        chunks.append(render_entry(e, j))
    out_path.write_text("".join(chunks), encoding="utf-8", newline="\n")

    report = [
        f"# kday 判别汇报 · {in_path.name}",
        "",
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 条目：{len(entries)}",
        f"- 完成：{len(done)}",
        f"- **kday判别为是：{yes_n}**",
        f"- **kday判别为否：{no_n}**",
        f"- 耗时：{time.time() - t0:.0f}s",
        "",
        f"- 输出 MD：`{out_path.relative_to(ROOT)}`",
        f"- 明细 JSONL：`{jsonl_path.relative_to(ROOT)}`",
        "",
    ]
    report_path.write_text("\n".join(report), encoding="utf-8", newline="\n")
    safe_print(f"完成：是 {yes_n} · 否 {no_n} → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
