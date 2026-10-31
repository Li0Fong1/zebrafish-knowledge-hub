# -*- coding: utf-8 -*-
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "解析文件" / "pdf_plaintext_samples"
txt_dir = root / "原文文字层"
out_md = root / "五篇PDF纯文本汇总.md"

targets = [
    ("9685", "springer"),
    ("7144", "nature"),
    ("12686", "frontiers"),
    ("12971", "ijdb"),
    ("18472", "jstage"),
]

parts = [
    "# 五篇 PDF 纯文本汇总",
    "",
    "> 按阅读顺序抽取：双栏先左后右；侧栏置文末；去页眉页脚；轻量断词修复。",
    "",
    "> PDF 目录：`原文PDF/`",
    "",
]

for no, layout in targets:
    p = txt_dir / f"{no}_{layout}_阅读顺序.txt"
    raw = p.read_text(encoding="utf-8")
    title = ""
    body = raw
    sep = "=" * 72
    if sep in raw:
        head, body = raw.split(sep, 1)
        for line in head.splitlines():
            if line.startswith("Title:"):
                title = line[6:].strip()
        body = body.lstrip("\n")
    parts += [
        "---",
        "",
        f"## [{no}] {layout} | {title}",
        "",
        f"- PDF：`原文PDF/{no}_{layout}.pdf`",
        f"- 单篇：`原文文字层/{no}_{layout}_阅读顺序.txt`",
        "",
        "```",
        body.rstrip(),
        "```",
        "",
    ]

out_md.write_text("\n".join(parts) + "\n", encoding="utf-8")
print("OK", out_md, out_md.stat().st_size)
