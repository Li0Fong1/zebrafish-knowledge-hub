# -*- coding: utf-8 -*-
"""
按人眼阅读顺序抽取 PDF 文字，并更新五篇汇总。
- 双栏：先左栏后右栏
- 侧栏（Supplementary / 作者单位 / e-mail）挪到当页末尾，避免插入正文
- 仅做轻量可读修复：去软连字符、合并断词换行；不删正文句子
"""
from __future__ import annotations

import re
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "解析文件" / "pdf_plaintext_samples"
PDF_DIR = OUT / "原文PDF"
TXT_DIR = OUT / "原文文字层"
SUMMARY = OUT / "五篇PDF纯文本汇总.txt"

TARGETS = [
    ("9685", "springer", "Differential toxicities of manganese ferrite nanoparticles from two synthesis methods using developing zebrafish (Danio rerio): towards a biocompatible and safe magnetic nanoparticles"),
    ("7144", "nature", "NACA deficiency reveals the crucial role of somite-derived stromal cells in haematopoietic niche formation"),
    ("12686", "frontiers", "Agouti overexpression in a transgenic model regulates integrity, permeability and electrogenic amino acid transport in zebrafish intestine"),
    ("12971", "ijdb", "Transient expression of apoaequorin in zebrafish embryos: extending the ability to image calcium transients during later stages of development"),
    ("18472", "jstage", "Synthesis of Novel Class of N-Alkyl-isatin-3-iminobenzoic Acid Derivatives and Their Biological Activity in Zebrafish Embryos and Human Cancer Cell Lines"),
]

_SIDEBAR = re.compile(
    r"(?is)^(?:\s*Supplementary Information\b"
    r"|\s*[A-Z]\.\s*[A-Z]\.[\s\u00a0].{0,80}(?:·|\*)"
    r"|\s*Laboratory of\b"
    r"|\s*e-mail:"
    r"|\s*Nanomedicine Integrated Research Center\b"
    r")"
)

def _mid_x(block) -> float:
    return (block[0] + block[2]) / 2.0


def _is_sidebar_block(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith("Supplementary Information"):
        return True
    if "e-mail:" in t or "e‑mail:" in t:
        return True
    # 作者单位短块：以姓名缩写开头且含机构词
    if re.match(r"^[A-Z]\.\s*[A-Z]\.", t) and (
        "Laboratory" in t
        or "Institute" in t
        or "University" in t
        or "Center" in t
        or "Centre" in t
    ):
        return True
    if t.startswith("Nanomedicine Integrated Research Center"):
        return True
    return False


def _is_header_footer_block(text: str, page: fitz.Page, block) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if re.fullmatch(r"Vol\.:?\s*\([\d.]+\)", t):
        return True
    if re.fullmatch(r"\d+\s+Page\s+\d+\s+of\s+\d+", t.replace("\t", " ")):
        return True
    if re.fullmatch(r"J Nanopart Res \(\d{4}\) \d+:\d+", t):
        return True
    # 页眉/页脚区域的短 running head
    y0, y1 = block[1], block[3]
    h = float(page.rect.height)
    if (y1 < h * 0.08 or y0 > h * 0.92) and len(t) < 120:
        if re.search(
            r"(Page \d+ of \d+|Vol\.|NATURE COMMUNICATIONS|All rights reserved|doi\.org)",
            t,
            re.I,
        ):
            return True
    return False


def extract_page_parts(page: fitz.Page) -> tuple[str, list[str]]:
    """返回 (正文, 侧栏块列表)。侧栏不插入正文，留给全文末尾。"""
    blocks = [
        b
        for b in (page.get_text("blocks") or [])
        if len(b) >= 7 and b[6] == 0 and (b[4] or "").strip()
    ]
    if not blocks:
        return (page.get_text("text") or ""), []

    width = float(page.rect.width)
    mid = width * 0.5
    body_blocks: list = []
    side_texts: list[str] = []

    for b in blocks:
        txt = b[4]
        if _is_header_footer_block(txt, page, b):
            continue
        if _is_sidebar_block(txt):
            side_texts.append(txt.rstrip())
            continue
        body_blocks.append(b)

    left = [b for b in body_blocks if _mid_x(b) < mid]
    right = [b for b in body_blocks if _mid_x(b) >= mid]
    two_col = len(left) >= 2 and len(right) >= 2

    if two_col:
        left.sort(key=lambda b: (b[1], b[0]))
        right.sort(key=lambda b: (b[1], b[0]))
        ordered = left + right
    else:
        ordered = sorted(body_blocks, key=lambda b: (round(b[1] / 8.0), b[0]))

    body = "\n".join(b[4].rstrip() for b in ordered)
    return body, side_texts


def light_readable(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\u00ad", "")  # soft hyphen
    t = t.replace("\ufeff", "").replace("\xa0", " ")
    t = t.replace("\u2003", " ").replace("\u2009", " ").replace("\u200b", "")
    # exam-\nple → example
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    # 压缩行尾空格
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() + "\n"


def extract_pdf(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    pages: list[str] = []
    sides: list[str] = []
    for page in doc:
        body, side = extract_page_parts(page)
        if body.strip():
            pages.append(body)
        sides.extend(side)
    doc.close()
    text = "\n\n".join(pages)
    if sides:
        text += "\n\n----- First-page sidebar / affiliations -----\n\n"
        text += "\n\n".join(sides)
    return light_readable(text)


def main() -> None:
    TXT_DIR.mkdir(parents=True, exist_ok=True)
    summary_parts = [
        "五篇 PDF 纯文本汇总（按阅读顺序：双栏先左后右；侧栏置文末；去页眉页脚；轻量断词修复）",
        f"PDF 目录: {PDF_DIR}",
        "=" * 72,
        "",
    ]

    for no, layout, title in TARGETS:
        pdf = PDF_DIR / f"{no}_{layout}.pdf"
        if not pdf.is_file():
            raise SystemExit(f"missing {pdf}")
        text = extract_pdf(pdf)
        out = TXT_DIR / f"{no}_{layout}_阅读顺序.txt"
        header = (
            f"Title: {title}\n"
            f"Layout: {layout}\n"
            f"Method: column_reading_order + sidebar_to_end + drop_headers + light_hyphen_fix\n"
            f"PDF: {pdf.name}\n"
            f"Chars: {len(text)}\n"
            f"{'=' * 72}\n\n"
        )
        out.write_text(header + text, encoding="utf-8")

        summary_parts += [
            "=" * 72,
            f"[{no}] {layout} | {title}",
            f"PDF: 原文PDF/{pdf.name}",
            "=" * 72,
            text.rstrip(),
            "",
        ]

        # 抽查 MRI 句是否连贯
        if no == "9685":
            m = re.search(
                r"magnetic resonance imaging \(MRI\).{0,120}hyperthermia \[7\]",
                text,
                flags=re.S,
            )
            print("9685 MRI→hyperthermia contiguous:", bool(m))
            if m:
                print("  snippet:", re.sub(r"\s+", " ", m.group(0))[:160])

        print(f"[{no}] chars={len(text)} -> {out.name}")

    SUMMARY.write_text("\n".join(summary_parts) + "\n", encoding="utf-8")
    print("WROTE", SUMMARY)


if __name__ == "__main__":
    main()
