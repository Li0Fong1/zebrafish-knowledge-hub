# -*- coding: utf-8 -*-
"""标记并导出全部未达金标条目（是表 + pdf库）。"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from scripts.gold_hunt_fulltext import PDF_GOLD_BODY, is_pdf_gold, load_oa_disk, normalize_doi
from scripts.gold_upgrade_5680 import PDFKU_HTML, PDFKU_PDF, PDFKU_XML, _doi_from_pdfku_stem, find_local_pdf

ROOT = Path(__file__).resolve().parents[1]
YES = ROOT / "解析文件" / "yes_fulltext_gold"
PDFKU = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
EXIST = ROOT / "解析文件" / "已存在表.csv"
OUT = ROOT / "解析文件"
MARK = "auto_gold_fail"
MARK_TS = datetime.now().isoformat(timespec="seconds")

HEADERS = [
    "未成功标记",
    "来源库",
    "entry_no",
    "DOI",
    "标题",
    "期刊",
    "PMID",
    "PMCID",
    "缺稿原因",
    "处理建议",
    "正文现长",
    "金标门槛",
    "现有来源",
    "gold_tier",
    "本地PDF",
    "本地XML",
    "本地HTML",
    "建议保存文件名",
    "OA线索URL",
    "原文链接",
]


def _index_enos(base: Path, pattern: str = "*") -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not base.is_dir():
        return out
    for f in base.glob(pattern):
        if not f.is_file():
            continue
        m = re.match(r"^(\d+)", f.stem)
        if not m:
            continue
        eno = m.group(1)
        prev = out.get(eno)
        if prev is None or f.stat().st_size > prev.stat().st_size:
            out[eno] = f
    return out


def _safe_doi(doi: str) -> str:
    return (doi or "").strip().lower().replace("/", "_").replace(":", "_")


def _classify(body: int, has_pdf: bool, has_xml: bool, has_html: bool, src: str, reason: str) -> tuple[str, str]:
    assets = []
    if has_pdf:
        assets.append("PDF")
    if has_xml:
        assets.append("XML")
    if has_html:
        assets.append("HTML")
    asset_s = "+".join(assets) if assets else "无本地全文文件"
    if not assets:
        return (reason or "无本地 PDF/XML/HTML"), "需重新下载全文PDF（优先OA；否则付费墙）"
    if body >= PDF_GOLD_BODY:
        return (
            f"本地有{asset_s}且正文={body}≥门槛，但未过金标校验（source={src or '无'}）",
            "需替换更高质量PDF后重抽（现有稿校验未过）",
        )
    detail = f"本地有{asset_s}，正文={body}，门槛={PDF_GOLD_BODY}；均未达金标"
    if has_pdf:
        return detail, "需替换更完整PDF或OCR后重抽"
    return detail, "需重新下载全文PDF（本地仅有XML/HTML且不够金标）"


def _stamp_fail(path: Path, rec: dict) -> None:
    if rec.get(MARK) is True and rec.get("auto_gold_fail_at"):
        return
    rec[MARK] = True
    rec["auto_gold_fail_at"] = MARK_TS
    rec["human_redownload_needed"] = True
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_fail_mark(path: Path, rec: dict) -> None:
    if MARK not in rec and "human_redownload_needed" not in rec:
        return
    rec.pop(MARK, None)
    rec.pop("auto_gold_fail_at", None)
    rec.pop("human_redownload_needed", None)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    oa = load_oa_disk()
    with EXIST.open(encoding="utf-8-sig", newline="") as fh:
        exist_rows = list(csv.DictReader(fh))
    exist = {str(r["entry_no"]).strip(): r for r in exist_rows if str(r.get("entry_no") or "").strip()}

    yes_paths = {p.stem: p for p in YES.glob("batch_*/papers/*.json")}
    pdfku_paths = {p.stem: p for p in PDFKU.glob("batch_*/papers/*.json")}

    pdf_by = _index_enos(PDFKU_PDF, "*.pdf")
    xml_by = _index_enos(PDFKU_XML, "*.xml")
    html_by = _index_enos(PDFKU_HTML, "*")

    rows: list[dict[str, str]] = []
    stamped = cleared = 0

    def add(**kw: object) -> None:
        rows.append({h: str(kw.get(h, "") or "") for h in HEADERS})

    # ---- 是表 ----
    for eno, meta in exist.items():
        path = yes_paths.get(eno)
        rec = None
        if path:
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                rec = None
        if rec and is_pdf_gold(rec):
            if path:
                _clear_fail_mark(path, rec)
                cleared += 1
            continue
        doi = (meta.get("doi") or (rec or {}).get("doi") or "").strip()
        title = (meta.get("en_title") or (rec or {}).get("title") or "").strip()
        body = len((rec or {}).get("body_text") or "") if rec else 0
        src = ((rec or {}).get("source") or "").strip()
        tier = ((rec or {}).get("gold_tier") or "").strip()
        local_pdf = find_local_pdf(eno, doi=doi) or pdf_by.get(eno)
        xmlp = xml_by.get(eno)
        htmlp = html_by.get(eno)
        detail, advice = _classify(body, bool(local_pdf), bool(xmlp), bool(htmlp), src, tier or "not_gold")
        fname = f"{eno}_{_safe_doi(doi)}.pdf" if doi else f"{eno}.pdf"
        oa_hit = oa.get(normalize_doi(doi), {}) if doi else {}
        urls = oa_hit.get("pdf_urls") or []
        add(
            未成功标记="是",
            来源库="是表",
            entry_no=eno,
            DOI=doi,
            标题=title[:300],
            期刊=(meta.get("journal") or "").strip(),
            PMID=(meta.get("pmid") or (rec or {}).get("pmid") or ""),
            PMCID=(meta.get("pmcid") or ""),
            缺稿原因=detail,
            处理建议=advice,
            正文现长=body,
            金标门槛=PDF_GOLD_BODY,
            现有来源=src,
            gold_tier=tier,
            本地PDF=local_pdf.name if local_pdf else "",
            本地XML=xmlp.name if xmlp else "",
            本地HTML=htmlp.name if htmlp else "",
            建议保存文件名=fname,
            OA线索URL=urls[0] if urls else "",
            原文链接=meta.get("source_url") or (f"https://doi.org/{doi}" if doi else ""),
        )
        if path and rec is not None:
            _stamp_fail(path, rec)
            stamped += 1

    # ---- pdf库（非是表已列）----
    pdfku_enos = set(pdf_by) | set(xml_by) | set(html_by) | set(pdfku_paths)
    for eno in sorted(pdfku_enos, key=lambda x: int(x) if x.isdigit() else 0):
        if eno in exist:
            continue
        path = pdfku_paths.get(eno)
        rec = None
        if path:
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                rec = None
        if rec and is_pdf_gold(rec):
            if path:
                _clear_fail_mark(path, rec)
                cleared += 1
            continue
        pdfp = pdf_by.get(eno)
        xmlp = xml_by.get(eno)
        htmlp = html_by.get(eno)
        doi = ""
        if pdfp:
            doi = _doi_from_pdfku_stem(pdfp.stem) or ""
        if not doi and xmlp:
            doi = _doi_from_pdfku_stem(xmlp.stem) or ""
        if not doi and htmlp:
            doi = _doi_from_pdfku_stem(htmlp.stem) or ""
        if not doi:
            doi = ((rec or {}).get("doi") or "").strip()
        title = ((rec or {}).get("title") or "").strip()
        body = len((rec or {}).get("body_text") or "") if rec else 0
        src = ((rec or {}).get("source") or "").strip()
        tier = ((rec or {}).get("gold_tier") or "").strip()
        detail, advice = _classify(body, bool(pdfp), bool(xmlp), bool(htmlp), src, tier or "not_gold")
        fname = f"{eno}_{_safe_doi(doi)}.pdf" if doi else f"{eno}.pdf"
        add(
            未成功标记="是",
            来源库="pdf库",
            entry_no=eno,
            DOI=doi,
            标题=title[:300],
            期刊="",
            PMID=(rec or {}).get("pmid") or "",
            PMCID="",
            缺稿原因=detail,
            处理建议=advice,
            正文现长=body,
            金标门槛=PDF_GOLD_BODY,
            现有来源=src,
            gold_tier=tier,
            本地PDF=pdfp.name if pdfp else "",
            本地XML=xmlp.name if xmlp else "",
            本地HTML=htmlp.name if htmlp else "",
            建议保存文件名=fname,
            OA线索URL="",
            原文链接=f"https://doi.org/{doi}" if doi else "",
        )
        if path and rec is not None:
            _stamp_fail(path, rec)
            stamped += 1

    # DOI 去重：是表优先
    seen: set[str] = set()
    dedup: list[dict[str, str]] = []
    for r in rows:
        d = (r.get("DOI") or "").strip().lower()
        if d:
            if d in seen:
                continue
            seen.add(d)
        dedup.append(r)

    yes_rows = [r for r in dedup if r["来源库"] == "是表"]
    pdfku_rows = [r for r in dedup if r["来源库"] == "pdf库"]
    simple_headers = [
        "未成功标记",
        "来源库",
        "entry_no",
        "DOI",
        "标题",
        "PMID",
        "处理建议",
        "建议保存文件名",
        "原文链接",
        "OA线索URL",
    ]

    ts = datetime.now().strftime("%Y%m%d_%H%M")

    def write_csv(path: Path, data: list[dict[str, str]], headers: list[str]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
            w.writeheader()
            w.writerows(data)

    files = {
        "all_full": OUT / "未达金标_全部_latest.csv",
        "all_ts": OUT / f"未达金标_全部_{ts}.csv",
        "all_simple": OUT / "未达金标_全部_简表_latest.csv",
        "yes_full": OUT / "是表需人工下载PDF_latest.csv",
        "yes_simple": OUT / "是表需人工下载PDF_简表_latest.csv",
        "yes_prio": OUT / "是表需人工下载PDF_优先下载_latest.csv",
        "pdfku_full": OUT / "pdf库未达金标_latest.csv",
        "pdfku_simple": OUT / "pdf库未达金标_简表_latest.csv",
        "legacy_full": OUT / "需人工下载PDF_书目清单_latest.csv",
        "legacy_dl": OUT / "需人工下载PDF_仅缺PDF_latest.csv",
    }
    write_csv(files["all_full"], dedup, HEADERS)
    write_csv(files["all_ts"], dedup, HEADERS)
    write_csv(files["all_simple"], dedup, simple_headers)
    write_csv(files["yes_full"], yes_rows, HEADERS)
    write_csv(files["yes_simple"], yes_rows, simple_headers)
    write_csv(files["yes_prio"], yes_rows, simple_headers)
    write_csv(files["pdfku_full"], pdfku_rows, HEADERS)
    write_csv(files["pdfku_simple"], pdfku_rows, simple_headers)
    write_csv(files["legacy_full"], dedup, HEADERS)
    write_csv(files["legacy_dl"], dedup, HEADERS)

    by_advice = Counter(r["处理建议"] for r in dedup)
    by_local = Counter()
    for r in dedup:
        bits = []
        if r.get("本地PDF"):
            bits.append("pdf")
        if r.get("本地XML"):
            bits.append("xml")
        if r.get("本地HTML"):
            bits.append("html")
        by_local["+".join(bits) if bits else "none"] += 1

    summary = {
        "updated_at": MARK_TS,
        "pdf_gold_body_floor": PDF_GOLD_BODY,
        "meaning": "未成功标记=是 → 自动战役未能达金标，需人工重下/替换PDF",
        "json_mark_field": MARK,
        "stamped_json": stamped,
        "cleared_gold_marks": cleared,
        "total_fail": len(dedup),
        "yes_fail": len(yes_rows),
        "pdfku_fail": len(pdfku_rows),
        "by_advice": dict(by_advice),
        "by_local_assets": dict(by_local),
        "files": {k: str(v) for k, v in files.items()},
        "save_hint": "新 PDF 存到 pdf库/pdf/{建议保存文件名}",
    }
    (OUT / "未达金标_标记摘要.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "需人工下载PDF_书目清单_摘要.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
