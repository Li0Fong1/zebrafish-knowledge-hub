# -*- coding: utf-8 -*-
"""
抽取 5 篇不同版式 PDF → 纯文本，按来源做针对性清洗，汇总成一份文档。
"""
from __future__ import annotations

import io
import json
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
HIT = ROOT / "zebrafish_package" / "结果" / "online_fulltext" / "results.jsonl"
OUT_DIR = ROOT / "解析文件" / "pdf_plaintext_samples"
OUT_MD = OUT_DIR / "五篇PDF纯文本汇总.md"
OUT_TXT = OUT_DIR / "五篇PDF纯文本汇总.txt"
RAW_DIR = OUT_DIR / "raw_pdf"

# 5 篇：Springer / Nature / Frontiers / IJDB / J-STAGE（版式差异大）
TARGETS = [
    ("9685", "springer"),
    ("7144", "nature"),
    ("12686", "frontiers"),
    ("12971", "ijdb"),
    ("18472", "jstage"),
]


def fetch_pdf(url: str, dest: Path, timeout: float = 60.0) -> tuple[bytes, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ZebrafishGeneQA/1.0; research)",
        "Accept": "application/pdf,*/*",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = (resp.headers.get("Content-Type") or "").lower()
    dest.write_bytes(data)
    return data, ctype


def extract_pypdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages[:100]:
        t = (page.extract_text() or "").strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def extract_pdfminer(data: bytes) -> str:
    try:
        from pdfminer.high_level import extract_text
    except ImportError:
        return ""
    return (extract_text(io.BytesIO(data)) or "").strip()


def best_extract(data: bytes, layout: str) -> tuple[str, str]:
    """按版式选更好的抽取策略。"""
    t1 = extract_pypdf(data)
    t2 = extract_pdfminer(data)
    # 选更长且空格/断词更合理的
    candidates = [("pypdf", t1), ("pdfminer", t2)]
    candidates = [(n, t) for n, t in candidates if t]
    if not candidates:
        return "", "none"
    # Nature/Springer 有时 pdfminer 更好；默认取更长
    name, text = max(candidates, key=lambda x: len(x[1]))
    # 若差距不大，偏好 pdfminer（版面更稳）
    if t2 and len(t2) >= len(t1) * 0.9:
        name, text = "pdfminer", t2
    if layout in ("nature", "springer") and t2 and len(t2) > 500:
        name, text = "pdfminer", t2
    return text, name


_LIGATURES = {
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬀ": "ff",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "ft",
    "ﬆ": "st",
}


def _fix_ligatures(text: str) -> str:
    for a, b in _LIGATURES.items():
        text = text.replace(a, b)
    return text


def _fix_dashes_and_spaces(text: str) -> str:
    # 常见 PDF 破折号/连字符乱码 → 标准字符
    text = text.replace("\u00ad", "")  # soft hyphen
    text = text.replace("\ufeff", "")
    text = text.replace("\xa0", " ")
    text = text.replace("\u2009", " ").replace("\u200a", " ").replace("\u202f", " ")
    # 损坏的区间符：163�?69 → 163-69（尽力）
    text = re.sub(r"(\d)\uFFFD\?(\d)", r"\1-\2", text)
    text = re.sub(r"(\d)�\?(\d)", r"\1-\2", text)
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    text = text.replace("´", "'").replace("`", "'")
    text = text.replace("“", '"').replace("”", '"').replace("„", '"')
    # 双空格压成单空格（保留换行）
    text = re.sub(r"[^\S\n]{2,}", " ", text)
    return text


def _reflow_paragraphs(text: str) -> str:
    """把 PDF 硬换行拼回段落，尽量贴近原文连续正文。"""
    lines = text.split("\n")
    out: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            out.append(buf.strip())
        buf = ""

    for raw in lines:
        line = raw.strip()
        if not line:
            flush()
            out.append("")
            continue
        # 单独成行的图注字母 / 页码
        if re.fullmatch(r"[a-z]", line) or re.fullmatch(r"\d{1,3}", line):
            continue
        if not buf:
            buf = line
            continue
        # 上一行以连字符结束：直接拼接
        if buf.endswith("-") and line[:1].islower():
            buf = buf[:-1] + line
            continue
        # 上一行未结束句子且本行像续行
        if (
            not buf.endswith((".", "?", "!", ":", ";"))
            and line[:1].islower()
        ) or (
            buf[-1:].isalnum()
            and line[:1].islower()
            and len(buf) < 100
        ):
            buf = buf + " " + line
            continue
        # 标题式短行：另起
        if len(line) < 60 and line.isupper():
            flush()
            buf = line
            continue
        # 新句子/新段
        if line[:1].isupper() and buf.endswith((".", "?", "!")):
            flush()
            buf = line
            continue
        # 默认：同段续写（PDF 常见折行）
        if len(buf) < 120 and not re.match(
            r"^(Abstract|Introduction|Methods|Results|Discussion|Conclusion|References|KEY WORDS|Keywords)\b",
            line,
            re.I,
        ):
            buf = buf + " " + line
        else:
            flush()
            buf = line
    flush()
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_common(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\x0c", "\n")  # form feed
    t = _fix_ligatures(t)
    t = _fix_dashes_and_spaces(t)
    # 断词连字符换行：exam-\nple → example
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def clean_by_layout(text: str, layout: str) -> str:
    t = clean_common(text)
    noise_pats = [
        r"(?im)^https?://\S+\s*$",
        r"(?im)^doi:\s*\S+\s*$",
        r"(?im)^©\s*\d{4}.*$",
        r"(?im)^page\s+\d+\s+of\s+\d+\s*$",
        r"(?im)^\d+\s+page\s+\d+\s+of\s+\d+\s*$",
        r"(?im)^\d+\s*/\s*\d+\s*$",
        r"(?im)^downloaded from.*$",
        r"(?im)^this article is licensed under.*$",
        r"(?im)^vol\.:\s*\(\d+\)\s*$",
        r"(?im)^&\s*\d{4}\s+macmillan.*$",
        r"(?im)^all rights reserved\.?\s*$",
        r"(?im)^open access\s*$",
        r"(?im)^frontiersin\.org\s*$",
        r"(?im)^received\s+\d+.*/\s*accepted.*$",
    ]
    if layout == "nature":
        noise_pats += [
            r"(?im)^nature communications.*$",
            r"(?im)^www\.nature\.com/naturecommunications\s*$",
            r"(?im)^article\s*$",
            r"(?im)^naca deficiency reveals.*niche formation\s*$",
        ]
    elif layout == "springer":
        noise_pats += [
            r"(?im)^springer.*$",
            r"(?im)^j\s*nanopart\s*res.*$",
            r"(?im)^journal of nanoparticle research.*$",
            r"(?im)^research\s*$",
            r"(?im)^supplementary information\s*$",
        ]
    elif layout == "frontiers":
        noise_pats += [
            r"(?im)^frontiers in.*$",
            r"(?im)^published:\s*.*$",
            r"(?im)^doi:\s*10\.3389.*$",
            r"(?im)^type\s+original research.*$",
            r"(?im)^edited by\s*$",
            r"(?im)^reviewed by\s*$",
            r"(?im)^specialty section\s*$",
            r"(?im)^citation\s*$",
            r"(?im)^copyright\s*$",
            r"(?im)^\*correspondence\s*$",
            r"(?im)^received\s+\d+.*$",
            r"(?im)^accepted\s+\d+.*$",
            r"(?im)^published\s+\d+.*$",
        ]
    elif layout == "jstage":
        noise_pats += [
            r"(?im)^biol\.\s*pharm\.\s*bull\..*$",
            r"(?im)^vol\.\s*\d+.*$",
            r"(?im)^regular article\s*$",
        ]
    elif layout == "ijdb":
        noise_pats += [
            r"(?im)^int\.\s*j\.\s*dev\.\s*biol\..*$",
            r"(?im)^technical article\s*$",
        ]

    lines = []
    for line in t.split("\n"):
        s = line.strip()
        if not s:
            lines.append("")
            continue
        # Springer 页码残留："Vol.: (0123456789) 146 Page 2 of 24"
        if re.search(r"Page\s+\d+\s+of\s+\d+", s, re.I):
            continue
        if re.fullmatch(r"Vol\.:\s*\(\d+\)", s):
            continue
        skip = False
        for pat in noise_pats:
            if re.match(pat, s):
                skip = True
                break
        if not skip:
            lines.append(line)
    t = "\n".join(lines)
    t = _reflow_paragraphs(t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t


def load_records() -> dict[str, dict]:
    want = {a for a, _ in TARGETS}
    found: dict[str, dict] = {}
    for line in HIT.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        no = str(r.get("entry_no") or "")
        if no in want:
            found[no] = r
    return found


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--reuse-pdf", action="store_true", help="复用 raw_pdf 已下载文件")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    found = load_records()
    blocks_md: list[str] = []
    blocks_txt: list[str] = []
    summary: list[str] = []

    blocks_md.append("# 五篇不同版式 PDF → 纯文本汇总\n")
    blocks_md.append(
        "从 `results.jsonl` 选取 5 篇 `content_type=pdf`、版式不同的文献；"
        "重新下载 PDF，按版式做抽取与清洗后汇总。\n"
    )

    for no, layout in TARGETS:
        r = found.get(no)
        if not r:
            summary.append(f"{no} MISSING")
            continue
        url = r.get("source_url") or ""
        title = r.get("en_title") or ""
        doi = r.get("doi") or ""
        pdf_path = RAW_DIR / f"{no}_{layout}.pdf"
        print(f"[{no}] {layout} …", flush=True)
        err = ""
        data = b""
        if args.reuse_pdf and pdf_path.is_file():
            data = pdf_path.read_bytes()
            print(f"  reuse {pdf_path.name} bytes={len(data)}", flush=True)
        else:
            try:
                data, ctype = fetch_pdf(url, pdf_path)
                print(f"  bytes={len(data)} ctype={ctype} magic={data[:5]!r}", flush=True)
            except Exception as e:
                err = str(e)
                print(f"  DOWNLOAD FAIL: {e}", flush=True)
                if pdf_path.is_file():
                    data = pdf_path.read_bytes()
                    print(f"  fallback local pdf bytes={len(data)}", flush=True)

        text = ""
        engine = "none"
        if data.startswith(b"%PDF"):
            text, engine = best_extract(data, layout)
            text = clean_by_layout(text, layout)
        elif data and not err:
            err = f"not a PDF magic (got {data[:20]!r})"
            text = clean_by_layout(r.get("body_text") or "", layout)
            engine = "fallback_body_text"

        if not text and (r.get("body_text") or ""):
            text = clean_by_layout(r.get("body_text") or "", layout)
            engine = engine if engine != "none" else "fallback_body_text"

        host = urlparse(url).netloc
        meta = (
            f"entry_no={no} | layout={layout} | host={host} | doi={doi} | "
            f"engine={engine} | chars={len(text)} | url={url}"
        )
        summary.append(meta)
        print(f"  -> {meta}", flush=True)

        # 单篇也落盘
        single = OUT_DIR / f"{no}_{layout}.txt"
        header = (
            f"Title: {title}\nDOI: {doi}\nSource: {url}\nLayout: {layout}\n"
            f"Engine: {engine}\nChars: {len(text)}\nError: {err or '—'}\n"
            f"{'=' * 72}\n\n"
        )
        single.write_text(header + text, encoding="utf-8")

        blocks_md.append(f"\n---\n\n## [{no}] {title}\n")
        blocks_md.append(f"- 版式标签：`{layout}`（{host}）\n")
        blocks_md.append(f"- DOI：{doi or '—'}\n")
        blocks_md.append(f"- URL：{url}\n")
        blocks_md.append(f"- 抽取引擎：`{engine}` | 纯文字符数：{len(text)}\n")
        if err:
            blocks_md.append(f"- 下载备注：{err}\n")
        blocks_md.append("\n### 纯文本全文\n\n```\n")
        blocks_md.append(text)
        blocks_md.append("\n```\n")

        blocks_txt.append("=" * 72)
        blocks_txt.append(f"[{no}] {title}")
        blocks_txt.append(meta)
        blocks_txt.append("=" * 72)
        blocks_txt.append(text)
        blocks_txt.append("")

        time.sleep(0.2)

    OUT_MD.write_text("".join(blocks_md), encoding="utf-8")
    OUT_TXT.write_text("\n".join(blocks_txt), encoding="utf-8")
    (OUT_DIR / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("WROTE", OUT_MD)
    print("WROTE", OUT_TXT)


if __name__ == "__main__":
    main()
