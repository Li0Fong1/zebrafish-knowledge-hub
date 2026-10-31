# -*- coding: utf-8 -*-
"""
在线全文 20 篇测试 + 生成 Markdown 报告。

用法：
  python -m scripts.online_fulltext_20_test_report
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from rag.dedup_ris_index import load_dedup_ris_index
from scripts.yes_md_online_fulltext import process_entry
from scripts.yes_md_pdf_test_report import _find_yes_md, iter_yes_md_entries

TEST_DIR = ROOT / "zebrafish_package" / "结果" / "online_fulltext" / "test_20"
JSONL_PATH = TEST_DIR / "results.jsonl"
REPORT_MD = TEST_DIR / "test_20_report.md"
REPORT_JSON = TEST_DIR / "test_20_report.json"
LIMIT = 20


def _run_test() -> list[dict[str, Any]]:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    ris_idx = load_dedup_ris_index()
    yes_md = _find_yes_md()
    rows: list[dict[str, Any]] = []
    t0 = time.time()

    with JSONL_PATH.open("w", encoding="utf-8") as out:
        for entry in iter_yes_md_entries(yes_md, limit=LIMIT, worker_id=0, workers=1):
            no = entry.get("entry_no", "?")
            safe_print(f"处理 [{no}] …")
            try:
                result = process_entry(entry, ris_idx, use_hunt=True)
            except Exception as exc:
                result = {
                    "entry_no": no,
                    "en_title": entry.get("EN Title", ""),
                    "status": "error",
                    "error": str(exc),
                }
            slim = {k: v for k, v in result.items() if k not in ("body_text", "attempts")}
            slim["attempt_count"] = len(result.get("attempts") or [])
            if result.get("status") == "ok":
                out.write(json.dumps({**slim, "body_text": result.get("body_text", "")}, ensure_ascii=False) + "\n")
            rows.append({**slim, "body_preview": (result.get("body_text") or result.get("body_preview") or "")[:600]})
            rows[-1]["attempts"] = result.get("attempts") or []

    elapsed = time.time() - t0
    for r in rows:
        r["elapsed_batch_s"] = round(elapsed, 1)
    return rows


def _write_report(rows: list[dict[str, Any]]) -> None:
    ok = [r for r in rows if r.get("status") == "ok"]
    fail = [r for r in rows if r.get("status") != "ok"]
    elapsed = rows[0].get("elapsed_batch_s", 0) if rows else 0

    by_step: dict[str, int] = {}
    for r in ok:
        step = r.get("source_step") or "?"
        by_step[step] = by_step.get(step, 0) + 1

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(rows),
        "success": len(ok),
        "failed": len(fail),
        "success_rate": round(len(ok) / max(len(rows), 1) * 100, 1),
        "by_source_step": by_step,
        "elapsed_seconds": elapsed,
    }
    REPORT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 在线全文 20 篇测试报告",
        "",
        f"- **生成时间**：{summary['generated_at']}",
        f"- **测试范围**：是.md entry 1–{LIMIT}",
        f"- **模式**：全网 hunt + EPMC/NCBI + Unpaywall/OpenAlex PDF（内存解析，不下载文件）",
        f"- **耗时**：{elapsed:.0f} 秒",
        "",
        "## 汇总",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 测试篇数 | {summary['total']} |",
        f"| **成功获取全文** | **{summary['success']}** |",
        f"| 失败/跳过 | {summary['failed']} |",
        f"| 成功率 | {summary['success_rate']}% |",
        "",
        "## 成功来源",
        "",
    ]
    for step, cnt in sorted(by_step.items(), key=lambda x: -x[1]):
        lines.append(f"- `{step}`：{cnt} 篇")
    lines.extend(["", "## 逐篇明细", ""])

    for r in rows:
        no = r.get("entry_no", "?")
        title = (r.get("en_title") or "")[:80]
        status = r.get("status", "?")
        lines.append(f"### [{no}] {title}")
        lines.append("")
        lines.append(f"- **状态**：{status}")
        if status == "ok":
            lines.append(f"- **来源**：`{r.get('source_step')}`")
            lines.append(f"- **类型**：{r.get('content_type')} | **字符数**：{r.get('text_chars')}")
            lines.append(f"- **DOI**：{r.get('doi') or '—'}")
            genes = (r.get("analysis") or {}).get("gene_mentions") or []
            if genes:
                lines.append(f"- **基因/术语**：{', '.join(genes[:12])}{'…' if len(genes) > 12 else ''}")
            preview = r.get("body_preview") or ""
            if preview:
                lines.append("")
                lines.append("**正文预览：**")
                lines.append("")
                lines.append(f"> {preview[:500]}{'…' if len(preview) > 500 else ''}")
        else:
            lines.append(f"- **DOI**：{r.get('doi') or '—'}")
            attempts = r.get("attempts") or []
            if attempts:
                last = attempts[-3:]
                lines.append(
                    "- **尝试**："
                    + "; ".join(f"{a.get('step')}({a.get('note')})" for a in last)
                )
        lines.append("")

    lines.extend([
        "## 输出文件",
        "",
        f"- JSONL：`{JSONL_PATH.relative_to(ROOT)}`",
        f"- 本报告：`{REPORT_MD.relative_to(ROOT)}`",
    ])
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main() -> int:
    safe_print(f"开始 20 篇在线全文测试 → {TEST_DIR}")
    rows = _run_test()
    _write_report(rows)
    ok = sum(1 for r in rows if r.get("status") == "ok")
    safe_print(f"完成：成功 {ok}/{len(rows)}，报告 → {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
