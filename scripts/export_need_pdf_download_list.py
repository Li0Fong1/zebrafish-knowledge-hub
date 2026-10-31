# -*- coding: utf-8 -*-
"""
导出「本地 PDF / XML / HTML 均无法达到金标」清单，供人工重新下载。

金标 = is_pdf_gold（source∈pdf_align|xml_jats_fulltext|html_fulltext_gold，
同门槛 body/strict/metrics/score）。

输出：
  解析文件/需人工下载PDF_书目清单_latest.csv     — 全量未达金标
  解析文件/需人工下载PDF_仅缺PDF_latest.csv       — 优先：需新下 PDF
  解析文件/需人工下载PDF_书目清单_摘要.json
"""
from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

from scripts.gold_hunt_fulltext import (
    PDF_GOLD_BODY,
    is_pdf_gold,
    load_oa_disk,
    normalize_doi,
)
from scripts.gold_upgrade_5680 import (
    PDFKU_HTML,
    PDFKU_PDF,
    PDFKU_XML,
    _build_pdf_index,
    _build_pdfku_xml_index,
    _doi_from_pdfku_stem,
    find_local_pdf,
)
import scripts.gold_upgrade_5680 as gu

gu._PDF_INDEX = _build_pdf_index()
gu._PDFKU_XML_INDEX = _build_pdfku_xml_index()

ROOT = Path(__file__).resolve().parents[1]
YES = ROOT / "解析文件" / "yes_fulltext_gold"
PDFKU = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
OUT = ROOT / "解析文件"
EXIST = ROOT / "解析文件" / "已存在表.csv"

HEADERS = [
    "DOI",
    "标题",
    "作者",
    "期刊",
    "PMID",
    "PMCID",
    "entry_no",
    "来源库",
    "缺稿原因",
    "处理建议",
    "正文现长",
    "金标门槛",
    "现有来源",
    "本地PDF",
    "本地XML",
    "本地HTML",
    "建议保存文件名",
    "OA线索URL",
    "原文链接",
]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def authors_from_jats(xml_path: Path, limit: int = 30) -> str:
    try:
        root = ET.fromstring(xml_path.read_bytes())
    except Exception:
        return ""
    authors: list[str] = []
    for contrib in root.iter():
        if _local(contrib.tag) != "contrib":
            continue
        ctype = (contrib.attrib.get("contrib-type") or "author").lower()
        if ctype not in ("author", ""):
            continue
        surname = given = ""
        for node in contrib.iter():
            ln = _local(node.tag)
            if ln == "surname":
                surname = " ".join(node.itertext()).strip()
            elif ln == "given-names":
                given = " ".join(node.itertext()).strip()
        name = f"{given} {surname}".strip()
        if name and name not in authors:
            authors.append(name)
        if len(authors) >= limit:
            break
    return "; ".join(authors)


def safe_doi_fname(doi: str) -> str:
    return (doi or "").strip().lower().replace("/", "_").replace(":", "_")


def _html_index() -> dict[str, Path]:
    idx: dict[str, Path] = {}
    if not PDFKU_HTML.is_dir():
        return idx
    for p in PDFKU_HTML.glob("*"):
        if not p.is_file() or p.stat().st_size < 500:
            continue
        m = re.match(r"^(\d+)", p.stem)
        if m:
            eno = m.group(1)
            prev = idx.get(eno)
            if prev is None or p.stat().st_size > prev.stat().st_size:
                idx[eno] = p
    return idx


def _classify(
    *,
    body: int,
    has_pdf: bool,
    has_xml: bool,
    has_html: bool,
    src: str,
    unreach_reason: str,
) -> tuple[str, str]:
    """返回 (缺稿原因, 处理建议)。"""
    assets = []
    if has_pdf:
        assets.append("PDF")
    if has_xml:
        assets.append("XML")
    if has_html:
        assets.append("HTML")
    asset_s = "+".join(assets) if assets else "无本地全文文件"
    if not assets:
        detail = unreach_reason or "无本地 PDF/XML/HTML"
        return detail, "需重新下载全文PDF（优先OA；否则付费墙）"
    if body >= PDF_GOLD_BODY:
        detail = (
            f"本地有{asset_s}且正文={body}≥门槛，但未过金标校验"
            f"（source={src or '无'}；{unreach_reason or 'strict/metrics/tier'}）"
        )
        return detail, "需替换更高质量PDF后重抽（现有稿校验未过）"
    detail = f"本地有{asset_s}，正文={body}，门槛={PDF_GOLD_BODY}；现有 PDF/XML/HTML 均未达金标体量"
    if has_pdf:
        return detail, "需替换更完整PDF或OCR后重抽"
    return detail, "需重新下载全文PDF（本地仅有XML/HTML且不够金标）"


def main() -> None:
    oa = load_oa_disk()
    unreach: dict[str, dict] = {}
    for urp in (
        YES / "unreachable_oa_registry.jsonl",
        PDFKU / "unreachable_oa_registry.jsonl",
    ):
        if not urp.is_file():
            continue
        for line in urp.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
                eno = str(o.get("entry_no") or "")
                if eno and eno not in unreach:
                    unreach[eno] = o
            except json.JSONDecodeError:
                pass

    with EXIST.open(encoding="utf-8-sig", newline="") as fh:
        exist_rows = list(csv.DictReader(fh))
    exist = {str(r["entry_no"]).strip(): r for r in exist_rows if str(r.get("entry_no") or "").strip()}

    yes_papers: dict[str, dict] = {}
    for p in YES.glob("batch_*/papers/*.json"):
        try:
            yes_papers[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass

    pdfku_papers: dict[str, dict] = {}
    for p in PDFKU.glob("batch_*/papers/*.json"):
        try:
            pdfku_papers[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass

    xml_by_eno: dict[str, Path] = {}
    xml_by_doi: dict[str, Path] = {}
    for f in PDFKU_XML.glob("*.xml") if PDFKU_XML.is_dir() else []:
        m = re.match(r"^(\d+)", f.stem)
        if m:
            eno = m.group(1)
            prev = xml_by_eno.get(eno)
            if prev is None or f.stat().st_size > prev.stat().st_size:
                xml_by_eno[eno] = f
        d = (_doi_from_pdfku_stem(f.stem) or "").lower()
        if d and (d not in xml_by_doi or f.stat().st_size > xml_by_doi[d].stat().st_size):
            xml_by_doi[d] = f

    pdf_by_eno: dict[str, Path] = {}
    for f in PDFKU_PDF.glob("*.pdf") if PDFKU_PDF.is_dir() else []:
        m = re.match(r"^(\d+)", f.stem)
        if m:
            eno = m.group(1)
            prev = pdf_by_eno.get(eno)
            if prev is None or f.stat().st_size > prev.stat().st_size:
                pdf_by_eno[eno] = f

    html_by_eno = _html_index()

    rows_out: list[dict[str, str]] = []

    def add_row(**kw: object) -> None:
        rows_out.append({h: str(kw.get(h, "") or "") for h in HEADERS})

    # ---- 是表：未达金标 ----
    for eno, row in exist.items():
        rec = yes_papers.get(eno)
        if rec and is_pdf_gold(rec):
            continue
        doi = (row.get("doi") or (rec or {}).get("doi") or "").strip()
        title = (row.get("en_title") or (rec or {}).get("title") or "").strip()
        journal = (row.get("journal") or "").strip()
        pmid = (row.get("pmid") or (rec or {}).get("pmid") or "").strip()
        pmcid = (row.get("pmcid") or "").strip()
        body = len((rec or {}).get("body_text") or "") if rec else 0
        src = ((rec or {}).get("source") or "").strip()
        reason = (unreach.get(eno) or {}).get("reason") or ""
        local_pdf = find_local_pdf(eno, doi=doi) or pdf_by_eno.get(eno)
        xmlp = xml_by_eno.get(eno) or (xml_by_doi.get(doi.lower()) if doi else None)
        htmlp = html_by_eno.get(eno)
        authors = authors_from_jats(xmlp) if xmlp else ""
        oa_hit = oa.get(normalize_doi(doi), {}) if doi else {}
        urls = oa_hit.get("pdf_urls") or []
        detail, advice = _classify(
            body=body,
            has_pdf=bool(local_pdf),
            has_xml=bool(xmlp),
            has_html=bool(htmlp),
            src=src,
            unreach_reason=str(reason),
        )
        fname = f"{eno}_{safe_doi_fname(doi)}.pdf" if doi else f"{eno}.pdf"
        add_row(
            DOI=doi,
            标题=title[:300],
            作者=authors,
            期刊=journal,
            PMID=pmid,
            PMCID=pmcid,
            entry_no=eno,
            来源库="是表(已存在表)",
            缺稿原因=detail,
            处理建议=advice,
            正文现长=body,
            金标门槛=PDF_GOLD_BODY,
            现有来源=src,
            本地PDF=local_pdf.name if local_pdf else "",
            本地XML=xmlp.name if xmlp else "",
            本地HTML=htmlp.name if htmlp else "",
            建议保存文件名=fname,
            OA线索URL=urls[0] if urls else "",
            原文链接=row.get("source_url") or (f"https://doi.org/{doi}" if doi else ""),
        )

    # ---- pdf库：有本地文件但未达金标（且不在是表已列出的 entry 可后去重）----
    pdfku_enos = set(pdf_by_eno) | set(xml_by_eno) | set(html_by_eno) | set(pdfku_papers)
    for eno in sorted(pdfku_enos, key=lambda x: int(x) if x.isdigit() else 0):
        if eno in exist:
            # 是表已覆盖；若是表已金标则不会进清单，未金标已进
            continue
        rec = pdfku_papers.get(eno)
        if rec and is_pdf_gold(rec):
            continue
        pdfp = pdf_by_eno.get(eno)
        xmlp = xml_by_eno.get(eno)
        htmlp = html_by_eno.get(eno)
        doi = ""
        if pdfp:
            doi = _doi_from_pdfku_stem(pdfp.stem) or ""
        if not doi and xmlp:
            doi = _doi_from_pdfku_stem(xmlp.stem) or ""
        if not doi and htmlp:
            doi = _doi_from_pdfku_stem(htmlp.stem) or ""
        if not doi:
            doi = ((rec or {}).get("doi") or "").strip()
        if not xmlp and doi:
            xmlp = xml_by_doi.get(doi.lower())
        title = ((rec or {}).get("title") or "").strip()
        body = len((rec or {}).get("body_text") or "") if rec else 0
        src = ((rec or {}).get("source") or "").strip()
        reason = (unreach.get(eno) or {}).get("reason") or ""
        authors = authors_from_jats(xmlp) if xmlp else ""
        detail, advice = _classify(
            body=body,
            has_pdf=bool(pdfp),
            has_xml=bool(xmlp),
            has_html=bool(htmlp),
            src=src,
            unreach_reason=str(reason),
        )
        fname = f"{eno}_{safe_doi_fname(doi)}.pdf" if doi else f"{eno}.pdf"
        add_row(
            DOI=doi,
            标题=title[:300],
            作者=authors,
            期刊="",
            PMID=(rec or {}).get("pmid") or "",
            PMCID="",
            entry_no=eno,
            来源库="pdf库(非是表)",
            缺稿原因=detail,
            处理建议=advice,
            正文现长=body,
            金标门槛=PDF_GOLD_BODY,
            现有来源=src,
            本地PDF=pdfp.name if pdfp else "",
            本地XML=xmlp.name if xmlp else "",
            本地HTML=htmlp.name if htmlp else "",
            建议保存文件名=fname,
            OA线索URL="",
            原文链接=f"https://doi.org/{doi}" if doi else "",
        )

    # DOI 去重：是表优先（rows_out 已是是表在前）
    seen_doi: set[str] = set()
    dedup: list[dict[str, str]] = []
    for r in rows_out:
        d = (r.get("DOI") or "").strip().lower()
        if d:
            if d in seen_doi:
                continue
            seen_doi.add(d)
        dedup.append(r)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    path = OUT / f"需人工下载PDF_书目清单_{ts}.csv"
    latest = OUT / "需人工下载PDF_书目清单_latest.csv"
    for pth in (path, latest):
        with pth.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=HEADERS)
            w.writeheader()
            w.writerows(dedup)

    # 优先给同事：明确需要重新下载 PDF 的
    only_dl = [
        r
        for r in dedup
        if "重新下载" in (r.get("处理建议") or "") or "替换" in (r.get("处理建议") or "")
    ]
    dl_path = OUT / "需人工下载PDF_仅缺PDF_latest.csv"
    with dl_path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(only_dl)

    # 严格「本地三种都试过仍不够」子集说明写进摘要
    no_gold_assets = Counter()
    for r in dedup:
        bits = []
        if r.get("本地PDF"):
            bits.append("pdf")
        if r.get("本地XML"):
            bits.append("xml")
        if r.get("本地HTML"):
            bits.append("html")
        no_gold_assets["+".join(bits) if bits else "none"] += 1

    summary = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "pdf_gold_body_floor": PDF_GOLD_BODY,
        "standard": "is_pdf_gold: pdf_align|xml_jats_fulltext|html_fulltext_gold 同门槛",
        "meaning": "清单内 = 本地 PDF/XML/HTML 均未能达到金标，需人工重新下载全文PDF",
        "total_rows": len(dedup),
        "need_redownload_rows": len(only_dl),
        "by_corpus": dict(Counter(r["来源库"] for r in dedup)),
        "by_action": dict(Counter(r["处理建议"] for r in dedup)),
        "by_local_assets": dict(no_gold_assets),
        "files": {
            "full_latest": str(latest),
            "download_only_latest": str(dl_path),
            "timestamped": str(path),
            "summary_json": str(OUT / "需人工下载PDF_书目清单_摘要.json"),
        },
        "save_hint": "请将新 PDF 存为 pdf库/pdf/{建议保存文件名}",
    }
    (OUT / "需人工下载PDF_书目清单_摘要.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
