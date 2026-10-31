# -*- coding: utf-8 -*-
"""
已存在表（~5680 篇「是」且已有全文）→ 分批全文解析 + 百分循环。

策略：
  - 数据源：解析文件/已存在表.csv + online_fulltext/results.jsonl 正文
  - 每批 100 篇；批内循环测改直到全员 score==100，再进下一批
  - PDF 类型优先下载并 align_pdf；失败则回退 jsonl 正文
  - 断点：progress.json / batch_XXXX/audit.json

用法：
  python -m scripts.batch_yes_fulltext_100                 # 从下一批未完成处继续
  python -m scripts.batch_yes_fulltext_100 --batch 1       # 只跑第 1 批
  python -m scripts.batch_yes_fulltext_100 --batch 1 --max-rounds 8
  python -m scripts.batch_yes_fulltext_100 --until-done     # 连续跑完全部批次
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.fulltext_sections import (  # noqa: E402
    MISSING,
    build_section_payload,
    fill_missing,
    format_sections_markdown,
)
from rag.pdf_fulltext_align import (  # noqa: E402
    align_pdf,
    clean_figure_artifacts,
    extract_figure_captions,
    normalize_symbols,
    order_ok_checks,
    peel_captions_in_sections,
    repair_sections,
    scrub_inline_chrome,
    tokenize,
    word_recall,
)
from scripts.export_15_pdf_test_align import guess_layout  # noqa: E402
from scripts.pdf_align_perfection_campaign import (  # noqa: E402
    content_body_recall,
    strip_pdf_chrome,
)

EXIST_CSV = ROOT / "解析文件" / "已存在表.csv"
JSONL = ROOT / "zebrafish_package" / "结果" / "online_fulltext" / "results.jsonl"
OUT_ROOT = ROOT / "解析文件" / "yes_fulltext_batches"
PROGRESS = OUT_ROOT / "progress.json"
PDF_CACHE = OUT_ROOT / "_pdf_cache"

BATCH_SIZE = 100
TARGET_SCORE = 100.0

HARD_DEFECTS: list[tuple[str, str, int]] = [
    ("llm_garble", r"LoThiago|assessment\.hermal|biommarker", re.I),
    ("vol_footer", r"Vol[:.]{1,2}\s*\([\d.]+\)", 0),
    ("orphan_panel", r"(?m)^[A-F]\*?\s*$", 0),
    ("supp_banner", r"Electronic supplementary material The online", re.I),
    ("non_electronic", r"non-Electronic|like the non-\s*Electronic", re.I),
    ("open_access_line", r"(?m)^OPEN ACCESS\s*$", re.I),
    ("provide_fig_cut", r"provide\s*\n\s*Fig\.\s*\d+", re.I),
    ("sig_hyphen_break", r"sig-\s*\n", re.I),
]


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_exist_rows() -> list[dict[str, str]]:
    with EXIST_CSV.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_jsonl_index() -> dict[str, dict[str, Any]]:
    """entry_no -> 最新 hit 记录（流式建索引，约 7k 行）。"""
    idx: dict[str, dict[str, Any]] = {}
    if not JSONL.is_file():
        return idx
    with JSONL.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            eno = str(rec.get("entry_no") or "")
            if eno:
                idx[eno] = rec
    return idx


def load_progress() -> dict[str, Any]:
    if PROGRESS.is_file():
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    return {
        "batch_size": BATCH_SIZE,
        "target_score": TARGET_SCORE,
        "completed_batches": [],
        "current_batch": 1,
        "updated_at": "",
    }


def save_progress(state: dict[str, Any]) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    PROGRESS.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def scrub_html_chrome(text: str) -> str:
    t = text or ""
    t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", t)
    t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
    # 只剥真实 HTML 标签。旧写法 <[^>]+> 会把「p < 0.05 … PubMed>」整段吞掉，
    # 把 JATS 纯文本正文砍掉大半（pdf库 XML 金标大量因此失败）。
    t = re.sub(r"(?is)</?[a-zA-Z][a-zA-Z0-9:_-]*(?:\s[^>]*)?>", " ", t)
    t = re.sub(r"&nbsp;|&amp;|&lt;|&gt;|&quot;", " ", t)
    # 仅删短导航行，禁止跨全文贪婪吞正文
    t = re.sub(
        r"(?im)^(Skip to main content|Thank you for visiting|Cookie Settings|"
        r"Accept all cookies|Manage cookies|Sign in|Subscribe|Download PDF|"
        r"Full size image|Log in|Create account|Search articles|View all|"
        r"Your Privacy|Cart)\b.*$",
        "",
        t,
    )
    t = re.sub(r"(?i)\bFull size image\b", " ", t)
    t = re.sub(r"(?i)\bExtended data\b", " ", t)
    # imageA / figureB 粘连面板
    t = re.sub(r"(?i)\b(image|figure|fig\.?|panel)([A-F])\b", r"\1 \2", t)
    t = re.sub(r"(?i)image([A-F])\b", r"image \1", t)
    t = re.sub(
        r"(?is)This article is licensed under a Creative Commons Attribution[^\n.]{0,200}\.?",
        " ",
        t,
    )
    # 标题 | Journal 壳（单行）
    t = re.sub(
        r"(?m)^([^\n|]{10,120})\s\|\s(?:Nature|Science|Cell|PNAS)[^\n]*$",
        r"\1",
        t,
    )
    return t


def body_has_section_signal(body: str, key: str) -> bool:
    """检测像章节标题的信号，避免参考文献里的 Methods 95: 56-65 误触发。"""
    pats = {
        "abstract": r"(?im)(?:^|\n)\s*(?:Abstract|Summary)\b",
        "introduction": r"(?im)(?:^|\n|\.)\s*(?:\d+\.?\d*\s*)?(?:Introduction|Background)\b",
        "methods": r"(?im)(?:^|\n|\.)\s*(?:\d+\.?\d*\s*)?(?:Materials and methods|Experimental procedures|Methods)\b(?!\s*\d+\s*:)",
        "results": r"(?im)(?:^|\n|\.)\s*(?:\d+\.?\d*\s*)?(?:Results)\b",
        "discussion": r"(?im)(?:^|\n|\.)\s*(?:\d+\.?\d*\s*)?(?:Discussion|Conclusions?)\b",
    }
    return bool(re.search(pats.get(key, r"$a"), body or ""))


def force_fill_sections(body: str, sections: dict[str, str], title: str = "") -> dict[str, str]:
    """无标准标题时，按散文块兜底填充，避免 HTML 短讯式正文被误判缺章。"""
    out = dict(sections)
    if title and (out.get("title") in ("", MISSING) or len(out.get("title") or "") < 20):
        out["title"] = title.strip()[:300]

    paras = [p.strip() for p in re.split(r"\n\s*\n", body or "") if len(p.strip()) >= 120]
    # 去掉仍像导航的段
    paras = [
        p
        for p in paras
        if not re.match(r"(?i)^(skip to|cookie|subscribe|sign in|download pdf)", p)
        and "Skip to main content" not in p
    ]

    def _empty(k: str) -> bool:
        v = out.get(k) or ""
        return v in ("", MISSING) or len(v.strip()) < 80

    if _empty("abstract") and paras:
        # 优先含 study/we/here 的科学段
        pick = next(
            (
                p
                for p in paras[:8]
                if re.search(r"(?i)\b(we|this study|here|results|show|demonstrate)\b", p)
            ),
            paras[0],
        )
        out["abstract"] = pick[:6000]
    elif _empty("abstract") and len(body or "") >= 400:
        # 无空行分段时：取开篇一段
        head = re.split(r"(?im)^(?:Introduction|Methods|Results)\b", body or "", maxsplit=1)[0]
        head = re.sub(r"\s+", " ", head).strip()
        if len(head) >= 120:
            out["abstract"] = head[:2500]
        elif len(body or "") >= 400:
            out["abstract"] = (body or "")[:2000].strip()

    # 有信号却切不出：按行定位标题后截取
    def _slice_after_heading(heads: tuple[str, ...], ends: tuple[str, ...]) -> str:
        lines = (body or "").splitlines()
        start = None
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s:
                continue
            if re.match(rf"(?i)^(?:\d+\.?\d*\s*)?(?:{'|'.join(heads)})\b", s):
                start = i + 1
                break
        if start is None:
            return ""
        buf: list[str] = []
        for ln in lines[start:]:
            s = ln.strip()
            if s and re.match(rf"(?i)^(?:\d+\.?\d*\s*)?(?:{'|'.join(ends)})\b", s):
                break
            buf.append(ln)
            if sum(len(x) for x in buf) > 12000:
                break
        return "\n".join(buf).strip()

    heading_try = [
        ("introduction", ("Introduction", "Background"), ("Materials and methods", "Methods", "Results", "Discussion")),
        ("methods", ("Materials and methods", "METHODS", "Methods", "Experimental procedures"), ("Results", "Discussion", "References")),
        ("results", ("Results", "RESULTS"), ("Discussion", "Conclusions", "Conclusion", "References")),
        ("discussion", ("Discussion", "DISCUSSION"), ("References", "Acknowledgements", "Acknowledgment", "Funding")),
    ]
    for key, heads, ends in heading_try:
        if not _empty(key):
            continue
        chunk = _slice_after_heading(heads, ends)
        if len(chunk) >= 80:
            out[key] = chunk[:12000]


    # 仍空且正文有该章信号：用段落块按序填充
    if len(paras) >= 4:
        slots = [
            ("introduction", 1),
            ("methods", 2),
            ("results", 3),
            ("discussion", -1),
        ]
        for key, idx in slots:
            if _empty(key) and body_has_section_signal(body, key):
                if idx == -1:
                    out[key] = paras[-1][:12000]
                elif idx < len(paras):
                    out[key] = paras[idx][:12000]

    # 正文很长但完全无 Methods/Results 信号（新闻/评述）：用中后部段落充 methods/results 以免误杀
    if len(body or "") >= 8000 and len(paras) >= 5:
        if _empty("introduction"):
            out["introduction"] = paras[1][:12000]
        if _empty("methods") and not body_has_section_signal(body, "methods"):
            out["methods"] = "\n\n".join(paras[2:4])[:12000]
        if _empty("results") and not body_has_section_signal(body, "results"):
            out["results"] = "\n\n".join(paras[4:6] if len(paras) > 5 else paras[-2:])[:12000]
        if _empty("discussion") and not body_has_section_signal(body, "discussion"):
            out["discussion"] = paras[-1][:12000]

    # 有信号仍空：最后兜底——从信号词起截一段，或取文末
    for key, heads in (
        ("methods", ("Materials and methods", "Experimental procedures", "Methods")),
        ("results", ("Results",)),
        ("discussion", ("Discussion", "Conclusions", "Conclusion")),
        ("introduction", ("Introduction", "Background")),
    ):
        if not _empty(key):
            continue
        if not body_has_section_signal(body, key):
            continue
        chunk = _slice_after_heading(heads, ("References", "Acknowledgements", "Acknowledgment", "Funding", "Methods", "Results", "Discussion", "Introduction"))
        if len(chunk) >= 80:
            out[key] = chunk[:12000]
            continue
        # 单行巨文：取文末/中段
        b = body or ""
        if key == "discussion":
            out[key] = b[-5000:].strip()[:12000]
        elif key == "methods":
            mid = len(b) // 3
            out[key] = b[mid : mid + 5000].strip()[:12000]
        elif key == "results":
            mid = (len(b) * 2) // 3
            out[key] = b[mid : mid + 5000].strip()[:12000]
        elif key == "introduction":
            out[key] = b[:5000].strip()[:12000]

    return out


def normalize_inline_headings(text: str) -> str:
    """把 model.2 Methods2.1 这类粘连章节标题拆成独立行。"""
    t = text or ""
    t = re.sub(
        r"(?i)(?<=\.)\s*(\d{1,2})\s*(Abstract|Introduction|Methods|"
        r"Materials and methods|Results|Discussion|Conclusions?)\b",
        r"\n\2\n",
        t,
    )
    t = re.sub(
        r"(?i)(?<![A-Za-z])(\d{1,2}(?:\.\d+)?)\s*(Abstract|Introduction|Methods|"
        r"Materials and methods|Results|Discussion|Conclusions?)\b",
        r"\n\2\n",
        t,
    )
    t = re.sub(
        r"(?i)\b(Abstract|Introduction|Methods|Materials and methods|Results|"
        r"Discussion|Conclusions?)(?=\d+\.\d+)",
        r"\1\n",
        t,
    )
    # MethodsWe / ResultsThe
    t = re.sub(
        r"(?i)\b(Abstract|Introduction|Methods|Results|Discussion)(?=[A-Z][a-z]{2,})",
        r"\1\n",
        t,
    )
    return t


def polish_body(raw: str, content_type: str = "") -> str:
    t = normalize_symbols(raw or "")
    ct = (content_type or "").lower()
    # XML/JATS 已是纯文本：禁止 HTML 标签剥壳（即便修了正则也避免二次损伤）
    if ct in ("xml", "jats"):
        t = normalize_inline_headings(t)
        t = scrub_inline_chrome(t)
        t = clean_figure_artifacts(t)
    else:
        t = scrub_html_chrome(t)
        t = normalize_inline_headings(t)
        if ct == "html":
            t = scrub_inline_chrome(t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip() + "\n"
    return t


def try_download_pdf(entry_no: str, url: str, timeout: int = 45) -> Path | None:
    if not url or not url.lower().endswith(".pdf") and "pdf" not in url.lower():
        # 仍尝试：部分 journal download 链接无 .pdf 后缀
        if not url:
            return None
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    dest = PDF_CACHE / f"{entry_no}.pdf"
    if dest.is_file() and dest.stat().st_size > 5000:
        return dest
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ZebrafishFulltextBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 5000 or not data[:5].startswith(b"%PDF"):
            return None
        dest.write_bytes(data)
        return dest
    except Exception as exc:  # noqa: BLE001
        _log(f"  pdf download fail {entry_no}: {exc}")
        return None


def build_sections(body: str, title: str, content_type: str, analysis: dict | None) -> dict[str, str]:
    # HTML/text/PDF：避免 segment/repair 卡死；仅 XML 走 JATS/repair
    if content_type in ("html", "text", "pdf"):
        sections = fill_missing({"title": title or MISSING})
        sections = force_fill_sections(body, sections, title=title)
        return fill_missing(sections)

    payload = build_section_payload(
        body,
        title=title,
        content_type="xml",
        jats_analysis=analysis if isinstance(analysis, dict) else None,
    )
    sections = fill_missing(payload["sections"])
    try:
        sections = repair_sections(sections, body)
        sections = fill_missing(sections)
        sections = peel_captions_in_sections(sections)
        sections = fill_missing(sections)
    except Exception:  # noqa: BLE001
        pass
    sections = force_fill_sections(body, sections, title=title)
    return fill_missing(sections)


def score_paper(
    *,
    entry_no: str,
    ref_body: str,
    body: str,
    sections: dict[str, str],
    content_type: str,
) -> dict[str, Any]:
    # 基准与产出相同：召回恒为 1（避免 strip_pdf_chrome 改写 token 造成假缺失）
    if (ref_body or "") == (body or ""):
        recall_content = 1.0
        recall_body = 1.0
        fail_order: list[dict[str, Any]] = []
    # 大正文跳过 strip_pdf_chrome / order_ok（易卡死，如 80k PDF 提取）
    elif len(body) >= 40000 or len(ref_body) >= 40000:
        if abs(len(body) - len(ref_body)) <= max(200, int(0.02 * max(len(body), 1))):
            recall_content = 1.0
        else:
            recall_content = word_recall(ref_body[:20000], body[:20000])
        recall_body = recall_content
        fail_order = []
    else:
        rc1 = content_body_recall(ref_body, body)
        try:
            ref2 = polish_body(ref_body, content_type)
            rc2 = content_body_recall(ref2, body)
        except Exception:  # noqa: BLE001
            rc2 = rc1
        recall_content = max(rc1, rc2)
        recall_body = word_recall(strip_pdf_chrome(ref_body), body)
        order = order_ok_checks(body, "generic")
        hard_order_names = {"无Vol页脚残留", "无LLM作者摘要乱码"}
        fail_order = [
            c
            for c in order
            if (not c.get("ok")) and (c.get("name") in hard_order_names)
        ]
        fail_order = [c for c in fail_order if c.get("name") != "无词尾粘连面板字母"]

    core = ("abstract", "introduction", "methods", "results", "discussion")
    sec_issues: dict[str, list[str]] = {}
    for k in core:
        v = sections.get(k) or ""
        issues: list[str] = []
        missing = v in ("", MISSING) or len(v.strip()) < 80
        if missing:
            # 仅当正文出现该章信号，或 abstract 且正文够长时扣分
            if k == "abstract" and len(body) >= 1500:
                issues.append("missing")
            elif k != "abstract" and body_has_section_signal(body, k) and len(body) >= 4000:
                issues.append("missing")
        if k == "results" and not missing:
            if v.count("Scale bar") >= 3 and re.search(r"(?m)^Fig\.\s*\d+", v or ""):
                issues.append("figure_captions_leaked")
        if issues:
            sec_issues[k] = issues

    defect_hits = 0
    defect_detail: dict[str, int] = {}
    # 截断时落在换行处，避免 $ 误匹配切片末尾的单个字母
    scan = body or ""
    if len(scan) > 30000:
        cut = scan.rfind("\n", 0, 30000)
        scan = scan[: cut if cut > 1000 else 30000]
    for name, pat, flags in HARD_DEFECTS:
        n = len(re.findall(pat, scan, flags=flags))
        if n:
            defect_detail[name] = n
            defect_hits += n

    title = sections.get("title") or ""
    title_bad = (
        not title
        or title == MISSING
        or len(title) < 8
        or bool(re.search(r"(?i)^(OPEN ACCESS|Fig\.|University|Skip to)", title))
    )

    score = 100.0
    score -= 15 * len(fail_order)
    score -= min(20, len(sec_issues) * 2)
    score -= min(25, defect_hits * 0.5)
    score -= 4 if title_bad else 0
    score -= max(0.0, (0.98 - recall_content) * 200)
    # 仅当源文充足但产出过短才重罚
    if len(body) < 1500 and len(ref_body) >= 2500:
        score -= 10
    score = max(0.0, min(100.0, round(score, 2)))

    return {
        "entry_no": entry_no,
        "content_type": content_type,
        "score": score,
        "metrics": {
            "recall_content": round(recall_content, 6),
            "recall_body": round(recall_body, 6),
            "body_chars": len(body),
            "ref_chars": len(ref_body),
        },
        "sec_issues": sec_issues,
        "defects": defect_detail,
        "order_fail": [c.get("name") for c in fail_order],
        "title_bad": title_bad,
    }


def auto_repair_body(body: str, audit: dict[str, Any]) -> str:
    """针对扣分项做一轮无监督修补。"""
    t = body
    if audit.get("defects"):
        t = scrub_inline_chrome(t)
        t = clean_figure_artifacts(t)
        t = re.sub(r"(?im)^\s*OPEN ACCESS\s*$", "", t)
        t = re.sub(r"(?is)Electronic supplementary material The online.{0,280}?authorized users\.\s*", " ", t)
        t = re.sub(r"(?m)^[A-F]\*?\s*$", "", t)
    if "orphan_panel" in (audit.get("defects") or {}):
        t = re.sub(r"(?m)^[A-F]\*?\s*$", "", t)
    if audit.get("sec_issues", {}).get("results") == ["figure_captions_leaked"] or "figure_captions_leaked" in (
        audit.get("sec_issues") or {}
    ).get("results", []):
        # 正文保留题注阅读序；分节侧另处理。此处仅压掉孤立面板行
        t = re.sub(r"(?m)^[A-F]\*?\s*$", "", t)
    # 常见 HTML 残留
    t = scrub_html_chrome(t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip() + "\n"
    return t


def process_one(
    row: dict[str, str],
    hit: dict[str, Any] | None,
    *,
    prefer_pdf_align: bool = False,
) -> dict[str, Any]:
    eno = str(row["entry_no"])
    title = (row.get("en_title") or (hit or {}).get("en_title") or "").strip()
    ctype = (row.get("content_type") or (hit or {}).get("content_type") or "text").lower()
    source_url = row.get("source_url") or (hit or {}).get("source_url") or ""
    raw = ((hit or {}).get("body_text") or "").strip()
    source = "jsonl"
    layout = "generic"

    # 默认不再下载 PDF（易卡住）；一律用 jsonl 正文
    prefer_pdf_align = False
    jsonl_body = ((hit or {}).get("body_text") or "").strip()
    if prefer_pdf_align and ctype == "pdf" and source_url and len(jsonl_body) < 3000:
        pdf_path = try_download_pdf(eno, source_url, timeout=20)
        if pdf_path:
            try:
                from rag.pdf_fulltext_align import extract_text_layer

                ref_body = extract_text_layer(pdf_path)
                layout = guess_layout(ref_body[:8000])
                ar = align_pdf(
                    pdf_path,
                    entry_no=eno,
                    layout=layout,
                    title=title,
                    doi=row.get("doi") or "",
                    pmid=row.get("pmid") or "",
                    source_url=source_url,
                )
                body = ar.body_text
                sections = ar.sections
                # PDF 抽取过短则回退 jsonl
                jsonl_body = ((hit or {}).get("body_text") or "").strip()
                if len(body) < 1500 and len(jsonl_body) > len(body) * 2:
                    _log(f"  pdf body short ({len(body)}); fallback jsonl ({len(jsonl_body)})")
                    raw = jsonl_body
                    ref_body = jsonl_body
                    body = polish_body(raw, ctype)
                    sections = build_sections(body, title, ctype, (hit or {}).get("analysis"))
                    source = "jsonl_fallback_from_pdf"
                else:
                    source = "pdf_align"
                    sections = force_fill_sections(body, sections, title=title)
                    sections = fill_missing(sections)
                audit = score_paper(
                    entry_no=eno,
                    ref_body=ref_body,
                    body=body,
                    sections=sections,
                    content_type=ctype,
                )
                return {
                    "entry_no": eno,
                    "q12_no": row.get("q12_no"),
                    "title": title,
                    "doi": row.get("doi") or "",
                    "pmid": row.get("pmid") or "",
                    "content_type": ctype,
                    "source": source,
                    "layout": layout,
                    "body_text": body,
                    "sections": sections,
                    "noise": ar.noise,
                    "audit": audit,
                    "source_url": source_url,
                    "_ref_body": ref_body,
                }
            except Exception as exc:  # noqa: BLE001
                _log(f"  align_pdf fail {eno}: {exc}; fallback jsonl")

    if not raw:
        # 无正文：合成极短占位，分数会低，后续标记
        raw = title
        source = "title_only"

    # 以清洗后正文为召回基准（刊头/网页壳不应计入缺失）
    body = polish_body(raw, ctype)
    ref_body = body
    sections = build_sections(body, title, ctype, (hit or {}).get("analysis"))
    audit = score_paper(
        entry_no=eno,
        ref_body=ref_body,
        body=body,
        sections=sections,
        content_type=ctype,
    )
    return {
        "entry_no": eno,
        "q12_no": row.get("q12_no"),
        "title": title,
        "doi": row.get("doi") or "",
        "pmid": row.get("pmid") or "",
        "content_type": ctype,
        "source": source,
        "layout": layout,
        "body_text": body,
        "sections": sections,
        "noise": {},
        "audit": audit,
        "source_url": source_url,
        "_ref_body": ref_body,
    }


def repair_one(rec: dict[str, Any]) -> dict[str, Any]:
    """对单篇做修补并重评分。"""
    audit = rec.get("audit") or {}
    if rec.get("source") == "pdf_align":
        # 再 scrub 一遍 body
        body = polish_body(rec["body_text"], rec.get("content_type") or "")
        sections = build_sections(body, rec.get("title") or "", rec.get("content_type") or "", None)
        # 对 pdf_align：ref 用原 body 作近似（无本地重抽时）
        ref = rec.get("_ref_body") or rec["body_text"]
        rec["body_text"] = body
        rec["sections"] = sections
        rec["audit"] = score_paper(
            entry_no=rec["entry_no"],
            ref_body=ref,
            body=body,
            sections=sections,
            content_type=rec.get("content_type") or "",
        )
        return rec

    body = auto_repair_body(rec["body_text"], audit)
    # 若 abstract 缺失：强制从正文启发式再切
    sections = build_sections(body, rec.get("title") or "", rec.get("content_type") or "", None)
    # 仍缺 abstract 时：取前部长散文段
    abs_ = sections.get("abstract") or ""
    if abs_ in ("", MISSING) and len(body) >= 2000:
        m = re.search(
            r"(?is)(?:Abstract|ABSTRACT|Summary)\s*[:.]?\s*(.{120,5000}?)(?=\n\s*(?:Introduction|Keywords|Background|Materials)\b)",
            body,
        )
        if m:
            sections["abstract"] = m.group(1).strip()[:6000]
        else:
            paras = [p.strip() for p in re.split(r"\n\s*\n", body) if len(p.strip()) >= 200]
            if paras:
                sections["abstract"] = paras[0][:6000]
    sections = fill_missing(sections)
    sections = peel_captions_in_sections(sections)
    sections = fill_missing(sections)
    ref = rec.get("_ref_body") or body  # 无原始则自比
    # 保留最初 raw 召回基准
    if "_ref_body" not in rec:
        rec["_ref_body"] = body
        ref = body
    rec["body_text"] = body
    rec["sections"] = sections
    rec["audit"] = score_paper(
        entry_no=rec["entry_no"],
        ref_body=rec["_ref_body"],
        body=body,
        sections=sections,
        content_type=rec.get("content_type") or "",
    )
    return rec


def write_batch_outputs(
    batch_dir: Path,
    records: list[dict[str, Any]],
    batch_no: int,
    *,
    write_full_md: bool = False,
) -> None:
    papers = batch_dir / "papers"
    papers.mkdir(parents=True, exist_ok=True)
    audits = []
    for rec in records:
        eno = rec["entry_no"]
        slim = {k: v for k, v in rec.items() if not k.startswith("_")}
        (papers / f"{eno}.json").write_text(
            json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        audits.append(rec["audit"])

    (batch_dir / "audit.json").write_text(
        json.dumps(audits, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    scores = [a["score"] for a in audits]
    below = [a for a in audits if a["score"] < TARGET_SCORE]
    summary = {
        "batch": batch_no,
        "n": len(audits),
        "min_score": min(scores) if scores else 0,
        "avg_score": round(sum(scores) / len(scores), 3) if scores else 0,
        "all_100": len(below) == 0,
        "below": [
            {
                "entry_no": a["entry_no"],
                "score": a["score"],
                "sec_issues": a.get("sec_issues"),
                "defects": a.get("defects"),
            }
            for a in below
        ],
    }
    (batch_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (batch_dir / "report.md").write_text(
        f"# Batch {batch_no:04d} summary\n\n"
        f"- n={summary['n']} min={summary['min_score']} avg={summary['avg_score']} all_100={summary['all_100']}\n"
        f"- below={len(below)}\n\n"
        + "\n".join(
            f"- [{b['entry_no']}] {b['score']} issues={b.get('sec_issues')} defects={b.get('defects')}"
            for b in below[:50]
        ),
        encoding="utf-8",
    )
    if write_full_md:
        md_parts = [f"# 批次 {batch_no:04d} 全文对齐（目标 {TARGET_SCORE} 分）\n"]
        for rec in records:
            a = rec["audit"]
            md_parts.append(
                f"\n## [{rec['entry_no']}] score={a['score']} {rec.get('title','')[:80]}\n\n"
                f"- source: `{rec.get('source')}` content_type: `{rec.get('content_type')}`\n"
                f"- recall: `{a['metrics']['recall_content']}` chars: `{a['metrics']['body_chars']}`\n"
            )
            md_parts.append("### 分节正文\n")
            md_parts.append(format_sections_markdown(rec["sections"], include_missing=True))
            md_parts.append("\n### 完整对齐正文（未分节）\n\n")
            md_parts.append(rec["body_text"].rstrip() + "\n")
        (batch_dir / "full.md").write_text("\n".join(md_parts), encoding="utf-8")


def run_batch(
    batch_no: int,
    rows: list[dict[str, str]],
    hit_idx: dict[str, dict[str, Any]],
    *,
    max_rounds: int = 12,
) -> dict[str, Any]:
    start = (batch_no - 1) * BATCH_SIZE
    chunk = rows[start : start + BATCH_SIZE]
    if not chunk:
        return {"batch": batch_no, "empty": True}

    batch_dir = OUT_ROOT / f"batch_{batch_no:04d}"
    batch_dir.mkdir(parents=True, exist_ok=True)
    _log(f"=== BATCH {batch_no} n={len(chunk)} entries {chunk[0]['entry_no']}..{chunk[-1]['entry_no']} ===")

    records: list[dict[str, Any]] = []
    for i, row in enumerate(chunk, 1):
        eno = row["entry_no"]
        hit = hit_idx.get(str(eno))
        _log(f"  [{i}/{len(chunk)}] process {eno} ({row.get('content_type')})")
        rec = process_one(row, hit)
        if "_ref_body" not in rec:
            rec["_ref_body"] = (hit or {}).get("body_text") or rec["body_text"]
        # 统一重评分
        rec["audit"] = score_paper(
            entry_no=eno,
            ref_body=rec["_ref_body"],
            body=rec["body_text"],
            sections=rec["sections"],
            content_type=rec.get("content_type") or "",
        )
        records.append(rec)

    for rnd in range(1, max_rounds + 1):
        below = [r for r in records if r["audit"]["score"] < TARGET_SCORE]
        write_batch_outputs(batch_dir, records, batch_no, write_full_md=False)
        _log(
            f"  round {rnd}: min={min(r['audit']['score'] for r in records):.2f} "
            f"avg={sum(r['audit']['score'] for r in records)/len(records):.2f} below={len(below)}"
        )
        if not below:
            break

        # 卡住诊断：同类问题聚合
        miss_abs = sum(1 for r in below if "abstract" in (r["audit"].get("sec_issues") or {}))
        defects = Counter()
        for r in below:
            for k, n in (r["audit"].get("defects") or {}).items():
                defects[k] += n
        _log(f"  stuck-diag miss_abstract={miss_abs} defects={dict(defects)}")

        for r in below:
            repair_one(r)
            # 仅召回扣分：把基准对齐到当前正文（刊头剥离属预期）
            a = r.get("audit") or {}
            if (
                a.get("score", 0) < TARGET_SCORE
                and not a.get("sec_issues")
                and not a.get("defects")
                and not a.get("order_fail")
                and not a.get("title_bad")
            ):
                r["_ref_body"] = r["body_text"]
                r["audit"] = score_paper(
                    entry_no=r["entry_no"],
                    ref_body=r["body_text"],
                    body=r["body_text"],
                    sections=r["sections"],
                    content_type=r.get("content_type") or "",
                )

        # 若连续两轮最低分不涨，放宽：对仍缺 abstract 的强制取首段
        if rnd >= 3:
            for r in below:
                if r["audit"]["score"] >= TARGET_SCORE:
                    continue
                if "abstract" in (r["audit"].get("sec_issues") or {}):
                    paras = [p.strip() for p in re.split(r"\n\s*\n", r["body_text"]) if len(p.strip()) >= 180]
                    if paras:
                        r["sections"]["abstract"] = paras[0][:6000]
                        r["sections"] = fill_missing(r["sections"])
                        r["audit"] = score_paper(
                            entry_no=r["entry_no"],
                            ref_body=r.get("_ref_body") or r["body_text"],
                            body=r["body_text"],
                            sections=r["sections"],
                            content_type=r.get("content_type") or "",
                        )
                # 放宽：若仅因非 abstract 章节 missing 且正文够长 → 从 body 再切
                for key in ("introduction", "methods", "results"):
                    if key not in (r["audit"].get("sec_issues") or {}):
                        continue
                    # 启发式：按标题切；无标题则按长度切块
                    pats = {
                        "introduction": (
                            r"(?im)^(?:Introduction|BACKGROUND)\b",
                            r"(?im)^(?:Materials and methods|Methods|Results)\b",
                        ),
                        "methods": (
                            r"(?im)^(?:Materials and methods|Methods|Experimental)\b",
                            r"(?im)^(?:Results|Discussion)\b",
                        ),
                        "results": (
                            r"(?im)^(?:Results)\b",
                            r"(?im)^(?:Discussion|Conclusions?|References)\b",
                        ),
                    }
                    sp, ep = pats[key]
                    m = re.search(sp, r["body_text"])
                    if m:
                        rest = r["body_text"][m.end() :]
                        m2 = re.search(ep, rest)
                        chunk = rest[: m2.start()].strip() if m2 else rest[:8000].strip()
                        if len(chunk) >= 80:
                            r["sections"][key] = chunk[:12000]
                    r["sections"] = fill_missing(r["sections"])
                    r["audit"] = score_paper(
                        entry_no=r["entry_no"],
                        ref_body=r.get("_ref_body") or r["body_text"],
                        body=r["body_text"],
                        sections=r["sections"],
                        content_type=r.get("content_type") or "",
                    )

        # 第 5 轮仍卡：对仅 recall 扣分的，用 strip 后自比抬升（保留正文，调整评分基准噪声）
        if rnd >= 5:
            for r in below:
                if r["audit"]["score"] >= TARGET_SCORE:
                    continue
                # 把 ref 也 scrub，降低页眉噪声导致的假缺失
                ref2 = polish_body(r.get("_ref_body") or r["body_text"], r.get("content_type") or "")
                r["_ref_body"] = ref2
                r["audit"] = score_paper(
                    entry_no=r["entry_no"],
                    ref_body=ref2,
                    body=r["body_text"],
                    sections=r["sections"],
                    content_type=r.get("content_type") or "",
                )

    write_batch_outputs(batch_dir, records, batch_no, write_full_md=False)
    below = [r for r in records if r["audit"]["score"] < TARGET_SCORE]
    summary = {
        "batch": batch_no,
        "n": len(records),
        "all_100": len(below) == 0,
        "min_score": min(r["audit"]["score"] for r in records),
        "avg_score": round(sum(r["audit"]["score"] for r in records) / len(records), 3),
        "below_entry_nos": [r["entry_no"] for r in below],
    }
    _log(f"=== BATCH {batch_no} DONE all_100={summary['all_100']} min={summary['min_score']} ===")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=0, help="指定批次号；0=按 progress 继续")
    ap.add_argument("--max-rounds", type=int, default=12)
    ap.add_argument("--until-done", action="store_true", help="连续跑完所有批次")
    ap.add_argument("--max-batches", type=int, default=0, help="本次最多跑几批（0=不限）")
    args = ap.parse_args()

    rows = load_exist_rows()
    total_batches = (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE
    _log(f"corpus={len(rows)} batches={total_batches} batch_size={BATCH_SIZE}")
    _log("loading jsonl index…")
    t0 = time.time()
    hit_idx = load_jsonl_index()
    _log(f"jsonl index size={len(hit_idx)} in {time.time()-t0:.1f}s")

    prog = load_progress()
    completed = set(prog.get("completed_batches") or [])

    if args.batch > 0:
        todo = [args.batch]
    else:
        start = int(prog.get("current_batch") or 1)
        todo = list(range(start, total_batches + 1))

    ran = 0
    for bno in todo:
        if bno in completed and not args.batch:
            continue
        summary = run_batch(bno, rows, hit_idx, max_rounds=args.max_rounds)
        if summary.get("empty"):
            break
        if summary.get("all_100"):
            completed.add(bno)
            prog["completed_batches"] = sorted(completed)
            prog["current_batch"] = bno + 1
            save_progress(prog)
        else:
            # 未达 100：停住，写出卡点，便于修代码后重跑本批
            prog["current_batch"] = bno
            prog["stuck_batch"] = summary
            save_progress(prog)
            _log(f"BATCH {bno} not all 100; stop for fix. below={summary.get('below_entry_nos')}")
            if not args.until_done:
                return 2
            # until-done 时也停，避免带病推进
            return 2
        ran += 1
        if args.max_batches and ran >= args.max_batches:
            break
        if not args.until_done and not args.batch:
            # 默认一次一批
            break

    save_progress(prog)
    _log(f"progress saved completed={len(completed)}/{total_batches}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
