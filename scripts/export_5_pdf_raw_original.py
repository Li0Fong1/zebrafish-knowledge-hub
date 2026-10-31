# -*- coding: utf-8 -*-
"""
五篇 PDF：按 PDF 嵌入文字层原样导出（零清洗），并整理原文 PDF 供查看。
"""
from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
HIT = ROOT / "zebrafish_package" / "结果" / "online_fulltext" / "results.jsonl"
OUT_DIR = ROOT / "解析文件" / "pdf_plaintext_samples"
RAW_PDF_DIR = OUT_DIR / "原文PDF"
RAW_TXT_DIR = OUT_DIR / "原文文字层"
SUMMARY_TXT = OUT_DIR / "五篇PDF纯文本汇总_原文原样.txt"
SUMMARY_MD = OUT_DIR / "五篇PDF纯文本汇总_原文原样.md"
README = OUT_DIR / "查看说明.md"

TARGETS = [
    ("9685", "springer"),
    ("7144", "nature"),
    ("12686", "frontiers"),
    ("12971", "ijdb"),
    ("18472", "jstage"),
]


def load_meta() -> dict[str, dict]:
    want = {a for a, _ in TARGETS}
    found: dict[str, dict] = {}
    with HIT.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            no = str(r.get("entry_no") or "")
            if no in want and no not in found:
                found[no] = r
    return found


def fetch_pdf(url: str, dest: Path, timeout: float = 90.0) -> None:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ZebrafishGeneQA/1.0; research)",
        "Accept": "application/pdf,*/*",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def extract_raw_text_layer(pdf_path: Path) -> str:
    """完全按 PDF 文字层原样：不做 NFKC、连字、断词、删行等任何处理。"""
    doc = fitz.open(pdf_path)
    parts: list[str] = []
    for page in doc:
        parts.append(page.get_text("text") or "")
    doc.close()
    # 仅页与页之间用换行连接；页内内容一字不改
    return "\n".join(parts)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_PDF_DIR.mkdir(parents=True, exist_ok=True)
    RAW_TXT_DIR.mkdir(parents=True, exist_ok=True)
    old_raw = OUT_DIR / "raw_pdf"
    meta = load_meta()

    md_parts = [
        "# 五篇 PDF 纯文本（原文文字层原样）\n\n",
        "> 抽取方式：PyMuPDF `get_text(\"text\")`，**零清洗**。"
        "保留 PDF 内嵌文字层的换行与阅读顺序（含双栏乱序）。\n\n",
        f"> 原文 PDF 目录：`{RAW_PDF_DIR.relative_to(ROOT)}`\n\n",
    ]
    txt_parts: list[str] = []
    readme_rows: list[str] = [
        "# 五篇样本：原文 PDF + 原样文字层",
        "",
        "## 怎么查看",
        "",
        f"1. **打开 PDF 原文**：文件夹 `{RAW_PDF_DIR}`",
        f"2. **对照纯文本**：文件夹 `{RAW_TXT_DIR}`（与 PDF 文字层一致，未做排版重排）",
        f"3. **汇总**：`{SUMMARY_MD.name}` / `{SUMMARY_TXT.name}`",
        "",
        "## 文件清单",
        "",
        "| 条目 | 版式 | PDF | 纯文本 | DOI | 来源 URL |",
        "|---|---|---|---|---|---|",
    ]

    for no, layout in TARGETS:
        info = meta.get(no) or {}
        title = info.get("en_title") or ""
        doi = info.get("doi") or ""
        url = info.get("source_url") or ""
        pdf_name = f"{no}_{layout}.pdf"
        dest = RAW_PDF_DIR / pdf_name

        # 优先重新下载；失败则复用已有 raw_pdf
        ok_download = False
        if url:
            try:
                print(f"download [{no}] {url}")
                fetch_pdf(url, dest)
                ok_download = dest.is_file() and dest.stat().st_size > 1000
                print(f"  saved {dest} ({dest.stat().st_size} bytes)")
            except Exception as e:
                print(f"  download failed: {e}")

        if not ok_download:
            fallback = old_raw / pdf_name
            if fallback.is_file():
                shutil.copy2(fallback, dest)
                print(f"  fallback copy from {fallback}")
            else:
                raise SystemExit(f"missing PDF for {no}")

        # 同步一份到旧目录，方便兼容
        old_raw.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, old_raw / pdf_name)

        raw = extract_raw_text_layer(dest)
        txt_path = RAW_TXT_DIR / f"{no}_{layout}_原文原样.txt"
        header = (
            f"Title: {title}\n"
            f"DOI: {doi}\n"
            f"Layout: {layout}\n"
            f"Method: pymupdf_get_text_ZERO_CLEAN\n"
            f"PDF: {pdf_name}\n"
            f"Chars: {len(raw)}\n"
            f"{'=' * 72}\n\n"
        )
        txt_path.write_text(header + raw, encoding="utf-8")
        # 也写到样品根目录，便于对比
        (OUT_DIR / f"{no}_{layout}_原文原样.txt").write_text(header + raw, encoding="utf-8")

        md_parts.append(f"\n---\n\n## [{no}] {title}\n\n")
        md_parts.append(f"- DOI: `{doi}`\n")
        md_parts.append(f"- PDF: `{RAW_PDF_DIR.name}/{pdf_name}`\n")
        md_parts.append(f"- 方法: `pymupdf_get_text` 零清洗 | 字符数: `{len(raw)}`\n\n")
        md_parts.append("```\n" + raw + "\n```\n")

        txt_parts += [
            "=" * 72,
            f"[{no}] {title}",
            f"doi={doi} method=pymupdf_get_text_ZERO_CLEAN chars={len(raw)}",
            f"pdf={pdf_name}",
            "=" * 72,
            raw,
            "",
        ]
        readme_rows.append(
            f"| {no} | {layout} | `{pdf_name}` | `{no}_{layout}_原文原样.txt` | {doi} | {url} |"
        )
        print(f"  text chars={len(raw)} -> {txt_path.name}")

    SUMMARY_TXT.write_text("\n".join(txt_parts), encoding="utf-8")
    SUMMARY_MD.write_text("".join(md_parts), encoding="utf-8")
    readme_rows += [
        "",
        "## 说明",
        "",
        "- 「原文原样」= PDF 内嵌文字层原样导出，**不**做连字修复、断词合并、段落重排。",
        "- 双栏期刊（如 Springer）在文字层里可能把页眉/作者信息插进正文中间；这与 PDF 阅读器视觉阅读顺序不同，但与文件内文字流一致。",
        "- 请用 PDF 阅读器打开 `原文PDF` 里的文件核对。",
        "",
    ]
    README.write_text("\n".join(readme_rows), encoding="utf-8")
    print("\nDONE")
    print("PDF dir:", RAW_PDF_DIR)
    print("TXT dir:", RAW_TXT_DIR)
    print("README:", README)


if __name__ == "__main__":
    main()
