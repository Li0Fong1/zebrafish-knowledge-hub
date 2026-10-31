# -*- coding: utf-8 -*-
"""对无法判别8_kday_待核.jsonl 再扫一遍（升是 / 降否 / 仍待核）。"""
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
from scripts.judge_literature_zebrafish_batch import _thread_llm, INVENTORY_OUTPUT_TOKENS, LLM_RETRIES, LLM_RETRY_WAIT
from scripts.judge_rate_limit_guard import is_balance_error, is_rate_limit_error, InsufficientBalanceError

SRC_MD = ROOT / "results_md" / "无法判别8.md"
PENDING_JSONL = ROOT / "results_md" / "无法判别8_kday_待核.jsonl"
MAIN_JSONL = ROOT / "results_md" / "无法判别8_kday.jsonl"
OUT_PENDING_MD = ROOT / "results_md" / "无法判别8_kday_待核.md"
OUT_PENDING_CSV = ROOT / "results_md" / "无法判别8_kday_待核.csv"
OUT_PASS_JSONL = ROOT / "results_md" / "无法判别8_kday_待核_rescan.jsonl"
OUT_REPORT = ROOT / "results_md" / "无法判别8_kday_待核_rescan_report.md"
OUT_MAIN_MD = ROOT / "results_md" / "无法判别8_kday.md"

PROMPT = """你是斑马鱼（Danio rerio）文献复核专家。这是「待核」二次细扫。

【标准】
- 是：实验、模型或主要观察对象是斑马鱼/斑马鱼胚胎/幼鱼（可写「以斑马鱼为研究主体」）。
- 否：未涉及斑马鱼，或仅顺带提及/其它物种为主体，或中文明确写未提及斑马鱼。
- 待核：信息仍不够（无摘要、截断、只写 fish 未写种），无法安心定是/否。

【偏置】证据够清楚就给是或否；只有真不够才留待核。不要因为「综述提到多种模式生物之一」就判是。

Title: {title}
Abstract: {abstract}

严格按下列 Markdown 行格式输出（不要 JSON、不要代码块）：
摘要:用中文写一句话（≤120字；无摘要写「无摘要」）
判断结果:是 或 否 或 待核
理由:一句话原因（≤120字）
"""


def _parse_md(raw: str) -> dict[str, str]:
    out = {"abstract_zh": "", "judgement": "待核", "reason": ""}
    for line in (raw or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line.startswith("摘要:"):
            out["abstract_zh"] = line.split(":", 1)[1].strip()
        elif line.startswith("判断结果:"):
            out["judgement"] = line.split(":", 1)[1].strip()
        elif line.startswith("理由:"):
            out["reason"] = line.split(":", 1)[1].strip()
    j = out["judgement"]
    if j not in ("是", "否", "待核"):
        if "是" in j and "否" not in j:
            out["judgement"] = "是"
        elif "否" in j:
            out["judgement"] = "否"
        else:
            out["judgement"] = "待核"
    return out


def to_label(j: str) -> str:
    return {"是": "kday判别为是", "否": "kday判别为否", "待核": "kday待核"}.get(j, "kday待核")


def judge_one(entry: dict[str, str], prev: dict[str, Any]) -> dict[str, Any]:
    title = " | ".join(x for x in [entry.get("EN Title") or "", entry.get("ZH Title") or ""] if x)
    parts = []
    for k in ("EN Abstract", "ZH Abstract"):
        v = (entry.get(k) or "").strip()
        if v and v not in ("无摘要", "无", "-"):
            parts.append(v)
    abstract = "\n".join(parts) or "无摘要"
    llm = _thread_llm()
    prompt = PROMPT.format(title=title[:800], abstract=abstract[:4000])
    last_err: Exception | None = None
    for attempt in range(LLM_RETRIES):
        try:
            raw = llm.chat([{"role": "user", "content": prompt}], max_new_tokens=max(INVENTORY_OUTPUT_TOKENS, 160))
            data = _parse_md(raw)
            j = data["judgement"]
            return {
                "entry_no": entry["entry_no"],
                "en_title": entry.get("EN Title") or prev.get("en_title") or "",
                "judgement": j,
                "kday_label": to_label(j),
                "reason": data.get("reason") or "",
                "summary_zh": data.get("abstract_zh") or entry.get("ZH Abstract") or "",
                "mode": "pending_rescan_llm",
                "prev_label": prev.get("kday_label"),
                "prev_reason": prev.get("reason"),
            }
        except Exception as e:
            if is_balance_error(e):
                raise InsufficientBalanceError(str(e)) from e
            last_err = e
            wait = LLM_RETRY_WAIT * (2 ** attempt)
            if is_rate_limit_error(e):
                wait *= 2
            safe_print(f"  retry {attempt+1}: {e}")
            time.sleep(wait)
    return {
        "entry_no": entry["entry_no"],
        "en_title": entry.get("EN Title") or "",
        "judgement": "待核",
        "kday_label": "kday待核",
        "reason": f"复扫失败保留待核：{last_err}"[:120],
        "summary_zh": entry.get("ZH Abstract") or "",
        "mode": "pending_rescan_fail",
        "prev_label": prev.get("kday_label"),
    }


def render_entry(entry: dict[str, str], judged: dict[str, Any]) -> str:
    title_zh = entry.get("ZH Title") or entry.get("heading") or judged.get("en_title") or ""

    def esc(s: str) -> str:
        return (s or "").replace("|", "/").replace("\n", " ").strip()

    return "\n".join(
        [
            f"### {entry['entry_no']}. {esc(title_zh)}",
            "",
            "| Field | Content |",
            "|------|------|",
            f"| EN Title | {esc(entry.get('EN Title') or judged.get('en_title') or '')} |",
            f"| ZH Title | {esc(title_zh)} |",
            f"| ZH Abstract | {esc(judged.get('summary_zh') or entry.get('ZH Abstract') or '')} |",
            f"| EN Abstract | {esc(entry.get('EN Abstract') or '')} |",
            f"| Judgment | {judged['kday_label']} |",
            f"| Reason | {esc(judged.get('reason') or '')} |",
            f"| Mode | {esc(judged.get('mode') or '')} |",
            "---",
            "",
        ]
    )


def main() -> int:
    entries = {e["entry_no"]: e for e in parse_entries(SRC_MD.read_text(encoding="utf-8"))}
    prev_rows = [json.loads(l) for l in PENDING_JSONL.open(encoding="utf-8") if l.strip()]
    safe_print(f"待核复扫 {len(prev_rows)} 条…")

    results: list[dict[str, Any]] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {
            pool.submit(judge_one, entries[str(r["entry_no"])], r): r
            for r in prev_rows
            if str(r["entry_no"]) in entries
        }
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            if r["kday_label"] != "kday待核" or i <= 5 or i % 20 == 0:
                safe_print(f"[{i}/{len(futs)}] #{r['entry_no']} {r['kday_label']} | {r.get('reason','')[:60]}")

    results.sort(key=lambda x: int(x["entry_no"]))
    yes = [r for r in results if r["kday_label"] == "kday判别为是"]
    no = [r for r in results if r["kday_label"] == "kday判别为否"]
    pend = [r for r in results if r["kday_label"] == "kday待核"]

    with OUT_PASS_JSONL.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 更新主 jsonl
    main_done = {}
    if MAIN_JSONL.is_file():
        for line in MAIN_JSONL.open(encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                main_done[str(r["entry_no"])] = r
    for r in results:
        main_done[str(r["entry_no"])] = {
            "entry_no": r["entry_no"],
            "en_title": r.get("en_title") or "",
            "judgement": r.get("judgement"),
            "kday_label": r["kday_label"],
            "reason": r.get("reason") or "",
            "mode": r.get("mode") or "",
            "summary_zh": r.get("summary_zh") or "",
        }
    all_entries = parse_entries(SRC_MD.read_text(encoding="utf-8"))
    with MAIN_JSONL.open("w", encoding="utf-8") as fh:
        for e in all_entries:
            fh.write(json.dumps(main_done[str(e["entry_no"])], ensure_ascii=False) + "\n")

    # 仅剩余待核清单
    still = pend
    with PENDING_JSONL.open("w", encoding="utf-8") as fh:
        for r in still:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    header = (
        f"# 无法判别8 · 待核复扫后剩余待核\n\n"
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}\n"
        f"- 本轮扫描：{len(results)}\n"
        f"- 升为是：{len(yes)}\n"
        f"- 降为否：{len(no)}\n"
        f"- **仍待核：{len(still)}**\n\n---\n\n"
    )
    chunks = [header]
    for r in still:
        chunks.append(render_entry(entries[str(r["entry_no"])], r))
    OUT_PENDING_MD.write_text("".join(chunks), encoding="utf-8", newline="\n")

    with OUT_PENDING_CSV.open("w", encoding="utf-8-sig", newline="") as fc:
        fc.write("entry_no,en_title,judgement,reason,mode\n")
        for r in still:
            def cq(x: str) -> str:
                return '"' + (x or "").replace('"', '""').replace("\n", " ") + '"'

            fc.write(
                ",".join(
                    [
                        r["entry_no"],
                        cq(r.get("en_title") or ""),
                        cq(r.get("judgement") or "待核"),
                        cq(r.get("reason") or ""),
                        cq(r.get("mode") or ""),
                    ]
                )
                + "\n"
            )

    # 主 md 重建（是/待核/否 排序）
    order = {"kday判别为是": 0, "kday待核": 1, "kday判别为否": 2}
    from collections import Counter

    ctr = Counter(main_done[str(e["entry_no"])]["kday_label"] for e in all_entries)
    main_header = (
        f"# kday 放宽三档判别 · 无法判别8.md（待核已复扫）\n\n"
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}\n"
        f"- 是：{ctr.get('kday判别为是', 0)}\n"
        f"- 待核：{ctr.get('kday待核', 0)}\n"
        f"- 否：{ctr.get('kday判别为否', 0)}\n\n---\n\n"
    )
    parts = [main_header]
    for e in sorted(all_entries, key=lambda x: (order.get(main_done[str(x["entry_no"])]["kday_label"], 9), int(x["entry_no"]))):
        parts.append(render_entry(e, main_done[str(e["entry_no"])]))
    OUT_MAIN_MD.write_text("".join(parts), encoding="utf-8", newline="\n")

    rep = [
        "# 待核复扫汇报",
        "",
        f"- 扫描：{len(results)}",
        f"- **升为是：{len(yes)}**",
        f"- **降为否：{len(no)}**",
        f"- **仍待核：{len(still)}**",
        f"- 耗时：{time.time()-t0:.0f}s",
        "",
        "## 升为是",
        "",
    ]
    for r in yes:
        rep.append(f"- #{r['entry_no']} {(r.get('en_title') or '')[:80]} — {r.get('reason')}")
    rep += ["", "## 降为否（前40）", ""]
    for r in no[:40]:
        rep.append(f"- #{r['entry_no']} {(r.get('en_title') or '')[:70]} — {r.get('reason')}")
    if len(no) > 40:
        rep.append(f"- … 另有 {len(no)-40} 条否")
    OUT_REPORT.write_text("\n".join(rep), encoding="utf-8", newline="\n")

    safe_print(f"完成：是+{len(yes)} 否+{len(no)} 仍待核{len(still)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
