# -*- coding: utf-8 -*-
"""从全量对齐单篇生成含全文的汇总 txt/md（仅正文，不含噪声锚点）。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "解析文件" / "pdf_plaintext_samples"
ALIGN = OUT / "全量对齐"
SUMMARY_TXT = OUT / "五篇PDF纯文本汇总.txt"
SUMMARY_MD = OUT / "五篇PDF纯文本汇总.md"

TARGETS = [
    ("9685", "springer"),
    ("7144", "nature"),
    ("12686", "frontiers"),
    ("12971", "ijdb"),
    ("18472", "jstage"),
]


def main() -> None:
    txt_parts = [
        "五篇 PDF 纯文本汇总（全量对齐正文，不含噪声锚点）",
        "对齐: 内容召回100% / 阅读顺序 / 排版 / 章节 / 符号 / 来源",
        f"单篇目录: {ALIGN}",
        "噪声/锚点见各篇 json 的 noise 字段或 txt 文末附录",
        "=" * 72,
        "",
    ]
    md_parts = [
        "# 五篇 PDF 纯文本汇总",
        "",
        "> 全量对齐**正文**合集（不含噪声锚点）。单篇也可看 `全量对齐/`。",
        "",
    ]

    for no, layout in TARGETS:
        jp = ALIGN / f"{no}_{layout}.json"
        if not jp.is_file():
            raise SystemExit(f"missing {jp}")
        meta = json.loads(jp.read_text(encoding="utf-8"))
        title = meta.get("title") or ""
        doi = meta.get("doi") or ""
        recall = (meta.get("metrics") or {}).get("word_recall")
        order_ok = (meta.get("metrics") or {}).get("order_ok")
        body = (meta.get("body_text") or "").rstrip()
        if not body:
            raise SystemExit(f"empty body_text in {jp}")

        txt_parts += [
            "=" * 72,
            f"[{no}] {layout} | {title}",
            f"DOI: {doi} | word_recall={recall} | order_ok={order_ok}",
            f"PDF: 原文PDF/{no}_{layout}.pdf",
            "=" * 72,
            body,
            "",
        ]
        md_parts += [
            "---",
            "",
            f"## [{no}] {layout} | {title}",
            "",
            f"- DOI: `{doi or '【无】'}`",
            f"- 词召回: `{float(recall or 0):.4%}`" if recall is not None else "- 词召回: `【无】`",
            f"- 顺序: `{order_ok}`",
            f"- PDF: `原文PDF/{no}_{layout}.pdf`",
            f"- 单篇: `全量对齐/{no}_{layout}.md`",
            "",
            "```",
            body,
            "```",
            "",
        ]

    SUMMARY_TXT.write_text("\n".join(txt_parts) + "\n", encoding="utf-8")
    SUMMARY_MD.write_text("\n".join(md_parts) + "\n", encoding="utf-8")
    print("WROTE", SUMMARY_TXT, SUMMARY_TXT.stat().st_size)
    print("WROTE", SUMMARY_MD, SUMMARY_MD.stat().st_size)


if __name__ == "__main__":
    main()
