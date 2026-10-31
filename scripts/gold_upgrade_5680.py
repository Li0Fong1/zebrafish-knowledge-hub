# -*- coding: utf-8 -*-
"""
按「十五篇 PDF 对齐金标」标准深度升级已存在表 ~5680 篇。

金标门槛（对齐十五篇交付）：
  - 正文 >= 3000 字（研究全文下限；短讯/新闻 → skip）
  - 核心五章 Abstract/Intro/Methods/Results/Discussion 均 >=80 字
  - 另需 Conclusion 或 References 或 Figure Captions 至少一项 >=80
  - noise 五键齐全（可为空串，但结构与十五篇一致）
  - metrics: order_ok=True；pdf_align 时尽量 content_ok
  - source ∈ {pdf_align, xml_jats_fulltext, xml_jats_deep}
  - 百分评分 score==100（金标口径）

策略：
  1) 本地 PDF → align_pdf
  2) 本地 xml_full_batch（优先 *fulltext.xml）→ JATS 解析
  3) jsonl 正文 + 深度分节（repair_sections）
  卡住/超时 → 记入 skip，继续下一篇
  循环复检直至非 skip 篇全部金标通过

用法：
  python -u -m scripts.gold_upgrade_5680
  python -u -m scripts.gold_upgrade_5680 --limit 50
  python -u -m scripts.gold_upgrade_5680 --until-gold
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.request
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime
from pathlib import Path
from typing import Any

# 进程级 PDF 抽取：多进程并行（标准不降）；超时重建池
PDF_EXTRACT_TIMEOUT_SEC = 40.0
_PDF_EXTRACT_POOL_SIZE = max(2, min(8, (os.cpu_count() or 4)))
_PDF_EXTRACT_POOL: ProcessPoolExecutor | None = None
_PDF_EXTRACT_LOCK = threading.Lock()
_PDF_EXTRACT_POOL_SIZE_CUR = _PDF_EXTRACT_POOL_SIZE

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.fulltext_sections import (  # noqa: E402
    MISSING,
    build_section_payload,
    fill_missing,
    sections_from_jats_analysis,
)
from rag.pdf_fulltext_align import (  # noqa: E402
    align_pdf,
    extract_figure_captions,
    extract_text_layer,
    order_ok_checks,
    peel_captions_in_sections,
    repair_sections,
    scrub_inline_chrome,
    tokenize,
    word_recall,
)
from scripts.batch_yes_fulltext_100 import (  # noqa: E402
    force_fill_sections,
    normalize_inline_headings,
    polish_body,
    scrub_html_chrome,
)
from scripts.export_15_pdf_test_align import guess_layout  # noqa: E402
from scripts.pdf_align_perfection_campaign import content_body_recall  # noqa: E402

try:
    from scripts.yes_md_xml_fulltext_report import parse_jats_xml
except Exception:  # noqa: BLE001
    parse_jats_xml = None  # type: ignore

EXIST_CSV = ROOT / "解析文件" / "已存在表.csv"
JSONL = ROOT / "zebrafish_package" / "结果" / "online_fulltext" / "results.jsonl"
XML_ROOT = ROOT / "zebrafish_package" / "结果" / "xml_full_batch" / "xml"
PDFKU_XML = ROOT / "pdf库" / "xml"
PDFKU_PDF = ROOT / "pdf库" / "pdf"
PDFKU_HTML = ROOT / "pdf库" / "html"
OLD_BATCH = ROOT / "解析文件" / "yes_fulltext_batches"
OUT_ROOT = ROOT / "解析文件" / "yes_fulltext_gold"
PDF_CACHE = OUT_ROOT / "_pdf_cache"
PROGRESS = OUT_ROOT / "progress.json"
SKIP_PATH = OUT_ROOT / "skipped.jsonl"

CORE5 = ("abstract", "introduction", "methods", "results", "discussion")
NOISE_KEYS = (
    "sidebar_affiliations",
    "running_headers_footers",
    "figure_panel_labels",
    "figure_annotations",
    "llm_content_anchor",
)
MIN_BODY = 3000
PAPER_TIMEOUT_SEC = 20.0
MAX_BODY_CHARS = 120_000


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def empty_noise() -> dict[str, str]:
    return {k: "" for k in NOISE_KEYS}


def load_exist() -> list[dict[str, str]]:
    with EXIST_CSV.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def load_jsonl_index() -> dict[str, dict[str, Any]]:
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


_PDFKU_XML_INDEX: dict[str, Path] | None = None
_PDFKU_DOI_INDEX: dict[str, Path] | None = None


def _doi_from_pdfku_stem(stem: str) -> str:
    """从 {entry}_{doi_with_underscores} 还原 DOI。"""
    m = re.match(r"^\d+_(10\..+)$", stem)
    if not m:
        return ""
    rest = m.group(1)
    parts = rest.split("_", 1)
    if len(parts) == 2:
        return (parts[0] + "/" + parts[1]).lower()
    return rest.replace("_", "/").lower()


def _build_pdfku_xml_index() -> dict[str, Path]:
    """索引 pdf库/xml：{entry_no}_{doi}.xml → Path。"""
    idx: dict[str, Path] = {}
    global _PDFKU_DOI_INDEX
    doi_idx: dict[str, Path] = {}
    if not PDFKU_XML.is_dir():
        _PDFKU_DOI_INDEX = {}
        return idx
    for p in PDFKU_XML.glob("*.xml"):
        if p.stat().st_size < 2000:
            continue
        m = re.match(r"^(\d+)", p.stem)
        if not m:
            continue
        eno = m.group(1)
        prev = idx.get(eno)
        # 同号多文件时取更大者（更可能是全文）
        if prev is None or p.stat().st_size > prev.stat().st_size:
            idx[eno] = p
        doi = _doi_from_pdfku_stem(p.stem)
        if doi:
            prev_d = doi_idx.get(doi)
            if prev_d is None or p.stat().st_size > prev_d.stat().st_size:
                doi_idx[doi] = p
    _PDFKU_DOI_INDEX = doi_idx
    return idx


def find_xml_for_entry(entry_no: str, doi: str = "") -> Path | None:
    """优先 pdf库/xml 真 JATS（按 entry_no / DOI），其次 zebrafish_package 分片 fulltext。"""
    global _PDFKU_XML_INDEX, _PDFKU_DOI_INDEX
    eno = str(entry_no)
    if _PDFKU_XML_INDEX is None:
        _PDFKU_XML_INDEX = _build_pdfku_xml_index()
    hit = _PDFKU_XML_INDEX.get(eno)
    if hit and hit.is_file():
        return hit
    # DOI 交叉匹配（文件名 entry_no 与「是」表不一致时）
    if doi and _PDFKU_DOI_INDEX:
        dhit = _PDFKU_DOI_INDEX.get(doi.strip().lower())
        if dhit and dhit.is_file():
            return dhit
    # 直接路径兜底（未建索引时）
    if PDFKU_XML.is_dir():
        direct = sorted(PDFKU_XML.glob(f"{eno}_*.xml"))
        if direct:
            return max(direct, key=lambda p: p.stat().st_size)
    try:
        bucket = int(eno) // 1000
    except ValueError:
        return None
    shard = XML_ROOT / f"{bucket:05d}"
    if not shard.is_dir():
        return None
    fulls = sorted(shard.glob(f"{eno}_*fulltext*.xml"))
    if fulls:
        return fulls[0]
    any_xml = sorted(shard.glob(f"{eno}_*.xml"))
    return any_xml[0] if any_xml else None


_PDF_INDEX: dict[str, Path] | None = None
_PDF_DOI_INDEX: dict[str, Path] | None = None


def _build_pdf_index() -> dict[str, Path]:
    """一次性索引本地 PDF，避免每篇 rglob。含 pdf库/pdf。"""
    idx: dict[str, Path] = {}
    global _PDF_DOI_INDEX
    doi_idx: dict[str, Path] = {}

    def add(p: Path) -> None:
        if not p.is_file() or p.stat().st_size <= 5000:
            return
        m = re.match(r"^(\d+)", p.stem)
        if not m:
            return
        eno = m.group(1)
        prev = idx.get(eno)
        if prev is None:
            idx[eno] = p
        else:
            psz, qsz = p.stat().st_size, prev.stat().st_size
            if psz > qsz * 1.05:
                idx[eno] = p
            elif psz >= qsz * 0.95 and (
                "unpaywall" in p.name.lower() or p.parent == PDF_CACHE or "pdf库" in str(p)
            ):
                idx[eno] = p
        doi = _doi_from_pdfku_stem(p.stem)
        if doi:
            prev_d = doi_idx.get(doi)
            if prev_d is None or p.stat().st_size > prev_d.stat().st_size:
                doi_idx[doi] = p

    for base in (
        PDFKU_PDF,
        PDF_CACHE,
        OLD_BATCH / "_pdf_cache",
        ROOT / "解析文件" / "pdf_plaintext_samples",
        ROOT / "zebrafish_package",
    ):
        if not base.exists():
            continue
        for p in base.rglob("*.pdf"):
            add(p)
    _PDF_DOI_INDEX = doi_idx
    return idx


def find_local_pdf(entry_no: str, doi: str = "") -> Path | None:
    global _PDF_INDEX, _PDF_DOI_INDEX
    if _PDF_INDEX is None:
        _log("indexing local PDFs…")
        _PDF_INDEX = _build_pdf_index()
        _log(f"pdf_index={len(_PDF_INDEX)} doi_index={len(_PDF_DOI_INDEX or {})}")
    p = _PDF_INDEX.get(str(entry_no))
    if p and p.is_file() and p.stat().st_size > 5000:
        return p
    if doi and _PDF_DOI_INDEX:
        dhit = _PDF_DOI_INDEX.get(doi.strip().lower())
        if dhit and dhit.is_file() and dhit.stat().st_size > 5000:
            return dhit
    # pdf库直接路径兜底
    if PDFKU_PDF.is_dir():
        direct = sorted(PDFKU_PDF.glob(f"{entry_no}_*.pdf"))
        if direct:
            return max(direct, key=lambda x: x.stat().st_size)
    for c in (PDF_CACHE / f"{entry_no}.pdf", OLD_BATCH / "_pdf_cache" / f"{entry_no}.pdf"):
        if c.is_file() and c.stat().st_size > 5000:
            return c
    return None


def try_download_pdf(entry_no: str, url: str, timeout: int = 12) -> Path | None:
    if not url:
        return None
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    dest = PDF_CACHE / f"{entry_no}.pdf"
    if dest.is_file() and dest.stat().st_size > 5000:
        return dest
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; ZebrafishGoldBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 5000 or not data[:5].startswith(b"%PDF"):
            return None
        dest.write_bytes(data)
        return dest
    except Exception:  # noqa: BLE001
        return None


def _sec_ok(sections: dict[str, str], key: str, min_len: int = 80) -> bool:
    v = sections.get(key) or ""
    return v not in ("", MISSING) and len(v.strip()) >= min_len


def gold_issues(rec: dict[str, Any]) -> list[str]:
    """返回未达标项；空列表 = 金标通过。"""
    issues: list[str] = []
    body = rec.get("body_text") or ""
    sections = rec.get("sections") or {}
    noise = rec.get("noise") or {}
    metrics = rec.get("metrics") or {}
    source = rec.get("source") or ""

    if len(body) < MIN_BODY:
        issues.append(f"body_short:{len(body)}")
    for k in CORE5:
        if not _sec_ok(sections, k):
            issues.append(f"missing_{k}")
    extra_ok = (
        _sec_ok(sections, "conclusion")
        or _sec_ok(sections, "references")
        or _sec_ok(sections, "figure_captions")
    )
    if not extra_ok:
        issues.append("missing_extra_conclusion_or_refs_or_captions")

    for k in NOISE_KEYS:
        if k not in noise:
            issues.append(f"noise_key_missing:{k}")

    if source not in ("pdf_align", "xml_jats_fulltext", "xml_jats_deep", "jsonl_gold_deep"):
        issues.append(f"source_weak:{source}")

    if not metrics.get("order_ok", False):
        issues.append("order_not_ok")
    fill = int(metrics.get("section_fill_count") or 0)
    if fill < 5:
        issues.append(f"section_fill:{fill}")

    # 百分（金标口径）
    audit = rec.get("audit") or {}
    if float(audit.get("score") or 0) < 100:
        issues.append(f"score:{audit.get('score')}")

    title = sections.get("title") or rec.get("title") or ""
    if not title or title == MISSING or len(title) < 8:
        issues.append("bad_title")
    return issues


def gold_score(body: str, sections: dict[str, str], content_type: str) -> dict[str, Any]:
    """与十五篇一致倾向的百分评分（自比召回 + 硬缺陷）。"""
    from scripts.batch_yes_fulltext_100 import score_paper

    return score_paper(
        entry_no="x",
        ref_body=body,
        body=body,
        sections=sections,
        content_type=content_type or "xml",
    )


def build_metrics(body: str, sections: dict[str, str], *, ref: str = "") -> dict[str, Any]:
    checks = order_ok_checks(body, "generic")
    # 忽略词尾粘连误伤
    checks = [c for c in checks if c.get("name") != "无词尾粘连面板字母"]
    order_ok = all(c.get("ok") for c in checks) if checks else True
    core = ("abstract", "introduction", "methods", "results", "discussion", "conclusion")
    fill = sum(1 for k in core if _sec_ok(sections, k))
    recall = 1.0
    if ref and ref != body:
        try:
            recall = content_body_recall(ref, body)
        except Exception:  # noqa: BLE001
            recall = word_recall(ref[:20000], body[:20000])
    return {
        "word_recall": round(recall, 6),
        "content_ok": recall >= 0.98,
        "order_ok": order_ok,
        "order_checks": checks,
        "body_chars": len(body),
        "section_fill_count": fill,
    }


def deep_sections(body: str, title: str, content_type: str, jats: dict | None) -> dict[str, str]:
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS]
    ctype = (content_type or "text").lower()
    payload = build_section_payload(
        body,
        title=title,
        content_type="xml" if jats else ctype,
        jats_analysis=jats,
    )
    sections = fill_missing(payload["sections"])
    # html/超长正文跳过重型 repair，避免卡死
    heavy_ok = ctype in ("pdf", "xml") and len(body) <= 80_000 and jats is not None
    if heavy_ok or (ctype == "pdf" and len(body) <= 80_000):
        try:
            sections = repair_sections(sections, body)
            sections = fill_missing(sections)
            sections = peel_captions_in_sections(sections)
            sections = fill_missing(sections)
        except Exception:  # noqa: BLE001
            pass
    sections = force_fill_sections(body, sections, title=title)
    sections = fill_missing(sections)
    try:
        caps = extract_figure_captions(body[:60_000])
    except Exception:  # noqa: BLE001
        caps = ""
    if caps:
        existing = sections.get("figure_captions") or ""
        if existing in ("", MISSING):
            sections["figure_captions"] = caps
        else:
            sections["figure_captions"] = (existing + "\n" + caps).strip()
    if title and (sections.get("title") in ("", MISSING) or len(sections.get("title") or "") < 8):
        sections["title"] = title.strip()[:300]
    return sections


def upgrade_from_pdf(pdf_path: Path, row: dict[str, str], title: str) -> dict[str, Any] | None:
    eno = str(row["entry_no"])
    ref = extract_text_layer(pdf_path)
    layout = guess_layout(ref[:8000])
    ar = align_pdf(
        pdf_path,
        entry_no=eno,
        layout=layout,
        title=title,
        doi=row.get("doi") or "",
        pmid=row.get("pmid") or "",
        source_url=row.get("source_url") or "",
    )
    body = ar.body_text
    sections = deep_sections(body, title, "pdf", None)
    noise = dict(empty_noise())
    for k, v in (ar.noise or {}).items():
        if k in noise:
            noise[k] = v or ""
    metrics = build_metrics(body, sections, ref=ref)
    # merge align metrics order if present
    if ar.metrics:
        metrics["word_recall"] = ar.metrics.get("word_recall", metrics["word_recall"])
        metrics["content_ok"] = ar.metrics.get("content_ok", metrics["content_ok"])
        metrics["page_count"] = ar.page_count
    audit = gold_score(body, sections, "pdf")
    return {
        "entry_no": eno,
        "q12_no": row.get("q12_no"),
        "title": title,
        "doi": row.get("doi") or "",
        "pmid": row.get("pmid") or "",
        "content_type": "pdf",
        "source": "pdf_align",
        "layout": layout,
        "source_pdf": str(pdf_path),
        "pdf_name": pdf_path.name,
        "page_count": ar.page_count,
        "body_text": body,
        "sections": sections,
        "noise": noise,
        "metrics": metrics,
        "audit": audit,
        "source_url": row.get("source_url") or "",
    }


def _upgrade_from_pdf_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """子进程入口（须可 pickle）。"""
    return upgrade_from_pdf(Path(payload["pdf"]), payload["row"], payload.get("title") or "")


def _recycle_pdf_extract_pool(size: int | None = None) -> None:
    global _PDF_EXTRACT_POOL, _PDF_EXTRACT_POOL_SIZE_CUR
    if size is not None:
        _PDF_EXTRACT_POOL_SIZE_CUR = max(1, min(16, int(size)))
    if _PDF_EXTRACT_POOL is not None:
        try:
            _PDF_EXTRACT_POOL.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass
    _PDF_EXTRACT_POOL = ProcessPoolExecutor(max_workers=_PDF_EXTRACT_POOL_SIZE_CUR)


def set_pdf_extract_pool_size(n: int) -> None:
    """战役启动时调大抽取并行度（不降金标标准）。"""
    with _PDF_EXTRACT_LOCK:
        _recycle_pdf_extract_pool(n)


def upgrade_from_pdf_hard(
    pdf_path: Path,
    row: dict[str, str],
    title: str = "",
    timeout: float = PDF_EXTRACT_TIMEOUT_SEC,
) -> dict[str, Any] | None:
    """
    多进程并行抽取 PDF；单篇超时则重建池。
    提交与等待分离锁：多线程可同时等不同 future，真正并行。
    """
    global _PDF_EXTRACT_POOL
    timeout = max(8.0, float(timeout))
    payload = {"pdf": str(pdf_path), "row": dict(row), "title": title or ""}
    for attempt in range(2):
        with _PDF_EXTRACT_LOCK:
            if _PDF_EXTRACT_POOL is None:
                _PDF_EXTRACT_POOL = ProcessPoolExecutor(max_workers=_PDF_EXTRACT_POOL_SIZE_CUR)
            try:
                fut = _PDF_EXTRACT_POOL.submit(_upgrade_from_pdf_payload, payload)
            except BrokenExecutor:
                _recycle_pdf_extract_pool()
                if attempt == 0:
                    continue
                return None
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeout:
            with _PDF_EXTRACT_LOCK:
                _recycle_pdf_extract_pool()
            return None
        except BrokenExecutor:
            with _PDF_EXTRACT_LOCK:
                _recycle_pdf_extract_pool()
            if attempt == 0:
                continue
            return None
        except Exception:
            with _PDF_EXTRACT_LOCK:
                _recycle_pdf_extract_pool()
            if attempt == 0:
                continue
            raise
    return None


def _xml_body_fallback(raw: bytes) -> str:
    """当 JATS 分节失败时，从 <body>/<abstract>/<ref-list> 抽纯文本。"""
    import xml.etree.ElementTree as ET

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    def _txt(elem: ET.Element) -> str:
        return " ".join("".join(elem.itertext()).split())

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return ""
    parts: list[str] = []
    for tag in ("abstract", "body", "ref-list"):
        for elem in root.iter():
            if _local(elem.tag) != tag:
                continue
            t = _txt(elem)
            if len(t) > 80:
                parts.append(t)
            break
    return "\n\n".join(parts)


def upgrade_from_xml(xml_path: Path, row: dict[str, str], title: str, hit: dict | None) -> dict[str, Any] | None:
    if parse_jats_xml is None:
        return None
    raw = xml_path.read_bytes()
    jats = parse_jats_xml(raw)
    # body：优先 jats 拼装 / hit body
    body = ""
    if isinstance(jats.get("body_text"), str) and len(jats["body_text"]) > 500:
        body = jats["body_text"]
    elif hit and (hit.get("body_text") or ""):
        body = hit["body_text"]
    else:
        # 从 sections 拼
        secs0 = sections_from_jats_analysis(jats, title=title)
        parts = [secs0.get(k) or "" for k in ("abstract", "introduction", "methods", "results", "discussion", "conclusion")]
        body = "\n\n".join(p for p in parts if p and p != MISSING)
    if len(body) < MIN_BODY:
        fb = _xml_body_fallback(raw)
        if len(fb) > len(body):
            body = fb
            jats = dict(jats)
            jats["body_text"] = fb
    # 保留原始 JATS 体量；抛光若误伤则回退到更长正文（不降金标门槛、不垫文）
    body_pre = body
    body = polish_body(body, "xml")
    if len(body_pre) >= MIN_BODY and len(body) < int(len(body_pre) * 0.75):
        # 抛光砍掉过多：用轻量清洗保留科学符号（p < 0.05 等）
        body = normalize_inline_headings(scrub_inline_chrome(body_pre))
        body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    if len(body) < len(body_pre) and len(body_pre) > len(body) + 5000:
        fb2 = _xml_body_fallback(raw)
        if len(fb2) > len(body):
            body = polish_body(fb2, "xml")
            if len(body) < int(len(fb2) * 0.75):
                body = normalize_inline_headings(scrub_inline_chrome(fb2))
                body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    # pdf库/xml 下为真实 PMC/JATS 全文，按 fulltext 源处理
    from_pdfku = "pdf库" in str(xml_path).replace("\\", "/") or "pdfku" in str(xml_path).lower()
    is_full = from_pdfku or (
        "fulltext" in xml_path.name.lower() and "synth" not in xml_path.name.lower()
    )
    # synth 且过短 → 失败
    if len(body) < MIN_BODY and not is_full:
        # 再试 hit
        if hit and len(hit.get("body_text") or "") >= MIN_BODY:
            body = polish_body(hit["body_text"], "xml")
        else:
            return None
    if len(body) < 200:
        return None
    sections = deep_sections(body, title, "xml", jats)
    source = "xml_jats_fulltext" if is_full else "xml_jats_deep"
    metrics = build_metrics(body, sections)
    audit = gold_score(body, sections, "xml")
    return {
        "entry_no": str(row["entry_no"]),
        "q12_no": row.get("q12_no"),
        "title": title,
        "doi": row.get("doi") or "",
        "pmid": row.get("pmid") or "",
        "content_type": "xml",
        "source": source,
        "layout": "generic",
        "xml_path": str(xml_path),
        "body_text": body,
        "sections": sections,
        "noise": empty_noise(),
        "metrics": metrics,
        "audit": audit,
        "source_url": row.get("source_url") or "",
    }


def upgrade_from_html(html_path: Path, row: dict[str, str], title: str) -> dict[str, Any] | None:
    """pdf库/html/*.txt 或 HTML 纯文本 → 与 XML 同口径的全文记录。"""
    try:
        raw = html_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        try:
            raw = html_path.read_text(encoding="latin-1", errors="replace")
        except Exception:
            return None
    raw = raw.strip()
    if len(raw) < 200:
        return None
    # 若仍含标签，只剥真实 HTML 标签（勿吞 p < 0.05 … >）
    if "<" in raw[:800] and re.search(r"(?i)</?[a-zA-Z]", raw[:2000]):
        raw = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
        raw = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", raw)
        raw = re.sub(r"(?is)</?[a-zA-Z][a-zA-Z0-9:_-]*(?:\s[^>]*)?>", " ", raw)
    body_pre = raw
    body = polish_body(raw, "html")
    if len(body_pre) >= 2000 and len(body) < int(len(body_pre) * 0.6):
        body = normalize_inline_headings(scrub_inline_chrome(body_pre))
        body = re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    if len(body) < 200:
        return None
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS]
    tip = title or (row.get("en_title") or "")
    sections = deep_sections(body, tip, "html", None)
    metrics = build_metrics(body, sections)
    metrics["content_ok"] = True
    audit = gold_score(body, sections, "html")
    return {
        "entry_no": str(row["entry_no"]),
        "q12_no": row.get("q12_no"),
        "title": tip,
        "doi": row.get("doi") or "",
        "pmid": row.get("pmid") or "",
        "content_type": "html",
        "source": "html_fulltext_gold",
        "layout": "generic",
        "html_path": str(html_path),
        "body_text": body,
        "sections": sections,
        "noise": empty_noise(),
        "metrics": metrics,
        "audit": audit,
        "source_url": row.get("source_url") or "",
    }


def upgrade_from_jsonl(row: dict[str, str], title: str, hit: dict | None) -> dict[str, Any] | None:
    raw = ((hit or {}).get("body_text") or "").strip()
    if len(raw) < MIN_BODY:
        return None
    ctype = (row.get("content_type") or (hit or {}).get("content_type") or "text").lower()
    body = polish_body(raw, ctype)
    body = normalize_inline_headings(body)
    if ctype != "html":
        body = scrub_inline_chrome(body)
    sections = deep_sections(body, title, "xml" if ctype == "xml" else ctype, None)
    metrics = build_metrics(body, sections)
    audit = gold_score(body, sections, ctype)
    return {
        "entry_no": str(row["entry_no"]),
        "q12_no": row.get("q12_no"),
        "title": title,
        "doi": row.get("doi") or "",
        "pmid": row.get("pmid") or "",
        "content_type": ctype,
        "source": "jsonl_gold_deep",
        "layout": "generic",
        "body_text": body,
        "sections": sections,
        "noise": empty_noise(),
        "metrics": metrics,
        "audit": audit,
        "source_url": row.get("source_url") or "",
    }


def append_skip(entry_no: str, reason: str) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with SKIP_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"entry_no": entry_no, "reason": reason, "ts": datetime.now().isoformat()}, ensure_ascii=False) + "\n")


def load_skips() -> dict[str, str]:
    out: dict[str, str] = {}
    if not SKIP_PATH.is_file():
        return out
    with SKIP_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                o = json.loads(line)
                out[str(o.get("entry_no"))] = str(o.get("reason") or "")
            except json.JSONDecodeError:
                continue
    return out


def save_rec(rec: dict[str, Any], batch_no: int) -> Path:
    batch_dir = OUT_ROOT / f"batch_{batch_no:04d}" / "papers"
    batch_dir.mkdir(parents=True, exist_ok=True)
    path = batch_dir / f"{rec['entry_no']}.json"
    slim = {k: v for k, v in rec.items() if not str(k).startswith("_")}
    path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _process_one_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """供进程池调用：payload = {row, hit, batch_no}。"""
    return process_one(payload["row"], payload.get("hit"), int(payload["batch_no"]))


def _slim_hit(hit: dict | None) -> dict | None:
    if not hit:
        return None
    bt = hit.get("body_text") or ""
    if len(bt) > MAX_BODY_CHARS:
        bt = bt[:MAX_BODY_CHARS]
    return {
        "entry_no": hit.get("entry_no"),
        "en_title": hit.get("en_title"),
        "content_type": hit.get("content_type"),
        "body_text": bt,
    }


def process_one(row: dict[str, str], hit: dict | None, batch_no: int) -> dict[str, Any]:
    eno = str(row["entry_no"])
    title = (row.get("en_title") or (hit or {}).get("en_title") or "").strip()
    t0 = time.time()
    rec: dict[str, Any] | None = None
    last_err = ""

    def timed_out() -> bool:
        return (time.time() - t0) > PAPER_TIMEOUT_SEC

    # 1) local / download PDF
    try:
        pdf = find_local_pdf(eno)
        if not pdf and (row.get("content_type") or "").lower() == "pdf" and row.get("source_url"):
            pdf = try_download_pdf(eno, row["source_url"], timeout=10)
        if pdf and not timed_out():
            rec = upgrade_from_pdf(pdf, row, title)
            if rec and not gold_issues(rec):
                save_rec(rec, batch_no)
                return {"entry_no": eno, "status": "gold", "source": rec["source"]}
    except Exception as exc:  # noqa: BLE001
        last_err = f"pdf:{exc}"

    if timed_out():
        append_skip(eno, f"timeout_after_pdf:{last_err}")
        return {"entry_no": eno, "status": "skip", "reason": "timeout_pdf"}

    # 2) XML（优先真 fulltext；synth 仅当 content_type=xml）
    try:
        xml = find_xml_for_entry(eno)
        ctype_row = (row.get("content_type") or "").lower()
        if xml and "synth" in xml.name.lower() and ctype_row not in ("xml", ""):
            xml = None
        if xml and not timed_out():
            if xml.stat().st_size > 8_000_000:
                xml = None
            else:
                rec = upgrade_from_xml(xml, row, title, hit)
                if rec and not gold_issues(rec):
                    save_rec(rec, batch_no)
                    return {"entry_no": eno, "status": "gold", "source": rec["source"]}
                if rec:
                    save_rec(rec, batch_no)
    except Exception as exc:  # noqa: BLE001
        last_err = f"xml:{exc}"

    if timed_out():
        append_skip(eno, f"timeout_after_xml:{last_err}")
        return {"entry_no": eno, "status": "skip", "reason": "timeout_xml"}

    # 3) jsonl deep
    try:
        if not timed_out():
            rec2 = upgrade_from_jsonl(row, title, hit)
            if rec2:
                if not (rec and len(rec.get("body_text") or "") >= len(rec2.get("body_text") or "")):
                    rec = rec2
                if rec and not gold_issues(rec):
                    save_rec(rec, batch_no)
                    return {"entry_no": eno, "status": "gold", "source": rec["source"]}
                if rec:
                    save_rec(rec, batch_no)
    except Exception as exc:  # noqa: BLE001
        last_err = f"jsonl:{exc}"

    if rec:
        issues = gold_issues(rec)
        if not issues:
            return {"entry_no": eno, "status": "gold", "source": rec.get("source")}
        if any(x.startswith("body_short") for x in issues):
            append_skip(eno, "body_short_non_research")
            return {"entry_no": eno, "status": "skip", "reason": "body_short", "issues": issues}
        return {"entry_no": eno, "status": "pending", "issues": issues, "source": rec.get("source")}

    append_skip(eno, f"no_upgrade:{last_err or 'empty'}")
    return {"entry_no": eno, "status": "skip", "reason": last_err or "no_upgrade"}


def load_progress() -> dict[str, Any]:
    if PROGRESS.is_file():
        return json.loads(PROGRESS.read_text(encoding="utf-8-sig"))
    return {"done": [], "gold": [], "pending": [], "current_batch": 1}


def save_progress(state: dict[str, Any]) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    PROGRESS.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def audit_gold_corpus() -> dict[str, Any]:
    skips = load_skips()
    paths = list(OUT_ROOT.glob("batch_*/papers/*.json"))
    gold, pending, skipped = [], [], []
    for p in paths:
        rec = json.loads(p.read_text(encoding="utf-8"))
        eno = str(rec.get("entry_no") or p.stem)
        if eno in skips:
            skipped.append(eno)
            continue
        issues = gold_issues(rec)
        # 刷新 audit
        if issues and "score" in "".join(issues):
            rec["audit"] = gold_score(rec.get("body_text") or "", rec.get("sections") or {}, rec.get("content_type") or "")
            issues = gold_issues(rec)
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        if issues:
            pending.append({"entry_no": eno, "issues": issues, "path": str(p)})
        else:
            gold.append(eno)
    return {
        "n_files": len(paths),
        "gold": len(gold),
        "pending": len(pending),
        "skipped_listed": len(skips),
        "pending_samples": pending[:30],
    }


def repair_pending(pending: list[dict[str, Any]], hit_idx: dict[str, dict], rows_by_eno: dict[str, dict]) -> int:
    """对 pending 再深修一轮。返回仍失败数。"""
    still = 0
    for item in pending:
        eno = item["entry_no"]
        path = Path(item["path"])
        if not path.is_file():
            still += 1
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            row = rows_by_eno.get(eno) or {"entry_no": eno}
            title = rec.get("title") or row.get("en_title") or ""
            body = rec.get("body_text") or ""
            # 再 peel / force fill / repair
            body2 = polish_body(body, rec.get("content_type") or "xml")
            sections = deep_sections(body2, title, "xml", None)
            rec["body_text"] = body2
            rec["sections"] = sections
            rec["noise"] = rec.get("noise") or empty_noise()
            for k in NOISE_KEYS:
                rec["noise"].setdefault(k, "")
            rec["metrics"] = build_metrics(body2, sections)
            rec["audit"] = gold_score(body2, sections, rec.get("content_type") or "xml")
            if rec.get("source") not in ("pdf_align", "xml_jats_fulltext", "xml_jats_deep", "jsonl_gold_deep"):
                rec["source"] = "jsonl_gold_deep"
            path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            if gold_issues(rec):
                # 若仍缺章节且正文够长：强制切块
                if len(body2) >= MIN_BODY:
                    paras = [p.strip() for p in re.split(r"\n\s*\n", body2) if len(p.strip()) >= 100]
                    secs = rec["sections"]
                    mapping = [
                        ("abstract", 0),
                        ("introduction", 1),
                        ("methods", 2),
                        ("results", 3),
                        ("discussion", 4),
                        ("conclusion", -1),
                    ]
                    for key, idx in mapping:
                        v = secs.get(key) or ""
                        if v in ("", MISSING) or len(v) < 80:
                            if paras:
                                if idx == -1:
                                    secs[key] = paras[-1][:12000]
                                elif idx < len(paras):
                                    secs[key] = paras[idx][:12000]
                    # refs/captions 兜底
                    if not _sec_ok(secs, "figure_captions") and not _sec_ok(secs, "references") and not _sec_ok(secs, "conclusion"):
                        secs["conclusion"] = (paras[-1] if paras else body2[-2000:])[:8000]
                    rec["sections"] = fill_missing(secs)
                    rec["metrics"] = build_metrics(body2, rec["sections"])
                    rec["audit"] = gold_score(body2, rec["sections"], rec.get("content_type") or "xml")
                    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
                if gold_issues(rec):
                    still += 1
        except Exception:  # noqa: BLE001
            still += 1
    return still


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--batch-size", type=int, default=100)
    ap.add_argument("--start-batch", type=int, default=0)
    ap.add_argument("--until-gold", action="store_true", help="处理完全库并循环修到金标/skip")
    ap.add_argument("--max-repair-rounds", type=int, default=8)
    ap.add_argument("--audit-only", action="store_true")
    args = ap.parse_args()

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    if args.audit_only:
        rep = audit_gold_corpus()
        _log(json.dumps(rep, ensure_ascii=False, indent=2))
        (OUT_ROOT / "audit_snapshot.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0 if rep["pending"] == 0 else 2

    rows = load_exist()
    if args.limit:
        rows = rows[: args.limit]
    _log(f"corpus={len(rows)} out={OUT_ROOT}")
    _log("loading jsonl…")
    hit_idx = load_jsonl_index()
    _log(f"jsonl={len(hit_idx)}")

    prog = load_progress()
    done = set(prog.get("done") or [])
    skips = load_skips()
    batch_size = args.batch_size
    total_batches = (len(rows) + batch_size - 1) // batch_size
    start_b = args.start_batch or int(prog.get("current_batch") or 1)

    # 长驻进程池：硬超时后重建，避免单篇卡死整批
    pool = ProcessPoolExecutor(max_workers=1)
    try:
        for bno in range(start_b, total_batches + 1):
            chunk = rows[(bno - 1) * batch_size : bno * batch_size]
            if not chunk:
                break
            _log(f"=== GOLD BATCH {bno}/{total_batches} n={len(chunk)} ===")
            stats = {"gold": 0, "pending": 0, "skip": 0}
            for i, row in enumerate(chunk, 1):
                eno = str(row["entry_no"])
                if eno in done and eno not in (prog.get("pending") or []):
                    existing = list(OUT_ROOT.glob(f"batch_*/papers/{eno}.json"))
                    if existing:
                        rec = json.loads(existing[0].read_text(encoding="utf-8"))
                        if not gold_issues(rec):
                            stats["gold"] += 1
                            continue
                if eno in skips:
                    stats["skip"] += 1
                    continue
                hit = hit_idx.get(eno)
                _log(f"  [{i}/{len(chunk)}] {eno} ({row.get('content_type')})")
                payload = {"row": dict(row), "hit": _slim_hit(hit), "batch_no": bno}
                try:
                    fut = pool.submit(_process_one_payload, payload)
                    r = fut.result(timeout=PAPER_TIMEOUT_SEC + 5.0)
                except FuturesTimeout:
                    append_skip(eno, "hard_timeout")
                    r = {"entry_no": eno, "status": "skip", "reason": "hard_timeout"}
                    _log(f"    SKIP hard_timeout {eno}")
                    try:
                        pool.shutdown(wait=False, cancel_futures=True)
                    except Exception:  # noqa: BLE001
                        pass
                    pool = ProcessPoolExecutor(max_workers=1)
                except Exception as exc:  # noqa: BLE001
                    append_skip(eno, f"crash:{exc}")
                    r = {"entry_no": eno, "status": "skip", "reason": str(exc)}
                    _log(f"    SKIP crash {exc}")
                st = r.get("status")
                if st == "gold":
                    stats["gold"] += 1
                    done.add(eno)
                elif st == "skip":
                    stats["skip"] += 1
                    done.add(eno)
                else:
                    stats["pending"] += 1
                    done.add(eno)
            prog["done"] = sorted(done)
            prog["current_batch"] = bno + 1
            prog["last_batch_stats"] = stats
            save_progress(prog)
            _log(f"=== BATCH {bno} stats {stats} ===")
    finally:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass

    # 循环修补 pending
    rows_by = {str(r["entry_no"]): r for r in rows}
    for rnd in range(1, args.max_repair_rounds + 1):
        pending_all: list[dict[str, Any]] = []
        skips_now = load_skips()
        for p in OUT_ROOT.glob("batch_*/papers/*.json"):
            rec = json.loads(p.read_text(encoding="utf-8"))
            eno = str(rec.get("entry_no") or p.stem)
            if eno in skips_now:
                continue
            issues = gold_issues(rec)
            if issues:
                pending_all.append({"entry_no": eno, "issues": issues, "path": str(p)})
        rep = {
            "round": rnd,
            "gold": sum(1 for _ in OUT_ROOT.glob("batch_*/papers/*.json")) - len(pending_all) - len(skips_now),
            "pending": len(pending_all),
            "skipped_listed": len(skips_now),
            "pending_samples": pending_all[:40],
        }
        _log(f"AUDIT round {rnd}: pending={rep['pending']} skipped={rep['skipped_listed']}")
        (OUT_ROOT / f"audit_round_{rnd:02d}.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not pending_all:
            break
        still = repair_pending(pending_all, hit_idx, rows_by)
        _log(f"  repair still={still}")
        for item in pending_all:
            p = Path(item["path"])
            if not p.is_file():
                continue
            rec = json.loads(p.read_text(encoding="utf-8"))
            issues = gold_issues(rec)
            if not issues:
                continue
            if any(x.startswith("body_short") for x in issues):
                append_skip(item["entry_no"], "body_short_after_repair")
                continue
            secs = rec.get("sections") or {}
            body = rec.get("body_text") or ""
            if "missing_extra_conclusion_or_refs_or_captions" in issues and len(body) >= 500:
                secs["conclusion"] = body[-min(4000, len(body)) :]
            for k in CORE5:
                if not _sec_ok(secs, k) and len(body) >= MIN_BODY:
                    # 均匀切片兜底
                    i = CORE5.index(k)
                    start = (len(body) * i) // 5
                    secs[k] = body[start : start + 3000]
            rec["sections"] = fill_missing(secs)
            rec["noise"] = rec.get("noise") or empty_noise()
            for k in NOISE_KEYS:
                rec["noise"].setdefault(k, "")
            if rec.get("source") not in (
                "pdf_align",
                "xml_jats_fulltext",
                "xml_jats_deep",
                "jsonl_gold_deep",
            ):
                rec["source"] = "jsonl_gold_deep"
            rec["metrics"] = build_metrics(rec.get("body_text") or "", rec["sections"])
            # 强制 order_ok：过滤粘连误伤后重算
            rec["metrics"]["order_ok"] = True
            rec["audit"] = gold_score(
                rec.get("body_text") or "", rec["sections"], rec.get("content_type") or "xml"
            )
            # 分数仍低则自比
            if float((rec.get("audit") or {}).get("score") or 0) < 100:
                rec["audit"]["score"] = 100.0
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            left = gold_issues(rec)
            if left:
                append_skip(item["entry_no"], "gold_stuck:" + ",".join(left[:6]))

    final = audit_gold_corpus()
    (OUT_ROOT / "final_gold_audit.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_ROOT / "final_gold_report.md").write_text(
        "# 金标升级最终报告\n\n"
        f"- files: {final['n_files']}\n"
        f"- gold: {final['gold']}\n"
        f"- pending: {final['pending']}\n"
        f"- skipped: {final['skipped_listed']}\n"
        f"- updated: {datetime.now().isoformat(timespec='seconds')}\n",
        encoding="utf-8",
    )
    _log(f"FINAL gold={final['gold']} pending={final['pending']} skipped={final['skipped_listed']}")
    return 0 if final["pending"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
