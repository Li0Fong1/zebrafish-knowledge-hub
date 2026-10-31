# -*- coding: utf-8 -*-
"""从对齐结果生成 15 篇「分节+全文」Markdown。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.fulltext_sections import format_sections_markdown  # noqa: E402

OUT = ROOT / "解析文件" / "pdf_plaintext_samples" / "测试15篇"
ALIGN = OUT / "对齐"


def main() -> None:
    idx = json.loads((OUT / "index.json").read_text(encoding="utf-8"))
    lines = [
        "# 15 篇 PDF 对齐全文（分节版）",
        "",
        f"- 篇数: {len(idx)}",
        "- 每篇先按 Abstract / Keywords / Introduction / Methods / Results / Discussion / Conclusion 等分节，再附完整正文",
        "",
    ]
    for i, m in enumerate(idx, 1):
        eno, layout = m["entry_no"], m["layout"]
        data = json.loads((ALIGN / f"{eno}_{layout}.json").read_text(encoding="utf-8"))
        title = data.get("title") or m.get("title") or ""
        body = data.get("body_text") or ""
        sections = data.get("sections") or {}
        recall = m.get("recall")
        if recall is None:
            recall = (data.get("metrics") or {}).get("word_recall", "")
        lines += [
            "---",
            "",
            f"## {i}. [{eno}] {title}",
            "",
            f"- layout: `{layout}`",
            f"- recall: `{recall}`",
            f"- chars: `{len(body)}`",
            "",
            "### 分节正文",
            "",
            format_sections_markdown(sections, include_missing=True),
            "",
            "### 完整对齐正文（未分节）",
            "",
            body.rstrip(),
            "",
        ]
    path = OUT / "十五篇PDF对齐全文.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    # 仅分节合集（更轻、便于 RAG）
    sec_only = [
        "# 15 篇 PDF 分节正文",
        "",
        f"- 篇数: {len(idx)}",
        "",
    ]
    for i, m in enumerate(idx, 1):
        eno, layout = m["entry_no"], m["layout"]
        data = json.loads((ALIGN / f"{eno}_{layout}.json").read_text(encoding="utf-8"))
        title = data.get("title") or m.get("title") or ""
        sec_only += [
            "---",
            "",
            f"## {i}. [{eno}] {title}",
            "",
            format_sections_markdown(data.get("sections") or {}, include_missing=True),
            "",
        ]
    path2 = OUT / "十五篇PDF分节正文.md"
    path2.write_text("\n".join(sec_only), encoding="utf-8")
    print("WROTE", path, "mb", round(path.stat().st_size / 1024 / 1024, 2))
    print("WROTE", path2, "mb", round(path2.stat().st_size / 1024 / 1024, 2))


if __name__ == "__main__":
    main()
