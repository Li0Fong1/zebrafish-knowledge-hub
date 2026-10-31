# -*- coding: utf-8 -*-
"""
一键修复 pdf库 未金标条目：按 PDF/XML/HTML 各自适配逻辑重抽，择优达金标。
不降门槛、不垫文。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_hunt_fulltext import PDF_GOLD_BODY, finalize_to_gold_bar, is_pdf_gold
from scripts.gold_pdfku_library_full_gold import (
    OUT,
    _row,
    list_targets,
    save_rec,
)
from scripts.gold_upgrade_5680 import (
    upgrade_from_html,
    upgrade_from_pdf_hard,
    upgrade_from_xml,
)
import scripts.gold_upgrade_5680 as gu
from scripts.gold_upgrade_5680 import _build_pdf_index, _build_pdfku_xml_index


def main() -> int:
    gu._PDF_INDEX = _build_pdf_index()
    gu._PDFKU_XML_INDEX = _build_pdfku_xml_index()
    targets = {t["entry_no"]: t for t in list_targets(pdf_only=False)}
    fixed = short = fail = already = 0
    by_via = {"pdf_align": 0, "xml_jats_fulltext": 0, "html_fulltext_gold": 0}
    for p in sorted(OUT.glob("batch_*/papers/*.json")):
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            fail += 1
            continue
        eno = p.stem
        if is_pdf_gold(old):
            # 仍清一次坏 tier（幂等）
            neu = finalize_to_gold_bar(
                dict(old),
                fallback_title=old.get("title") or f"Entry {eno}",
                prefer_source=(old.get("source") or "pdf_align"),
            )
            if is_pdf_gold(neu) and (neu.get("gold_tier") != old.get("gold_tier") or neu.get("complete") != old.get("complete")):
                save_rec(neu)
            already += 1
            continue
        t = targets.get(eno) or {
            "entry_no": eno,
            "doi": (old.get("doi") or ""),
            "pdf": "",
            "xml": "",
            "html": "",
        }
        doi = (t.get("doi") or old.get("doi") or "").strip()
        row = _row(eno, doi)
        tip = (old.get("title") or "").strip() or (f"DOI {doi}" if doi else f"Entry {eno} fulltext")
        cands: list[tuple[int, str, dict]] = []
        # 先 XML/HTML（快、且是表对照后的主修复面），再 PDF
        xml = Path(t["xml"]) if t.get("xml") else None
        if xml and xml.is_file():
            try:
                rec = upgrade_from_xml(xml, row, tip, None)
                if rec:
                    rec = finalize_to_gold_bar(rec, fallback_title=tip, prefer_source="xml_jats_fulltext")
                    cands.append((len(rec.get("body_text") or ""), "xml_jats_fulltext", rec))
                    if is_pdf_gold(rec):
                        # 已达金标则跳过昂贵 PDF 重抽
                        pass
                    else:
                        pass
            except Exception:
                pass
        html = Path(t["html"]) if t.get("html") else None
        if html and html.is_file():
            try:
                rec = upgrade_from_html(html, row, tip)
                if rec:
                    rec = finalize_to_gold_bar(rec, fallback_title=tip, prefer_source="html_fulltext_gold")
                    cands.append((len(rec.get("body_text") or ""), "html_fulltext_gold", rec))
            except Exception:
                pass
        have_gold = any(is_pdf_gold(c[2]) for c in cands)
        pdf = Path(t["pdf"]) if t.get("pdf") else None
        if (not have_gold) and pdf and pdf.is_file():
            try:
                rec = upgrade_from_pdf_hard(pdf, row, tip)
                if rec:
                    rec = finalize_to_gold_bar(rec, fallback_title=tip, prefer_source="pdf_align")
                    cands.append((len(rec.get("body_text") or ""), "pdf_align", rec))
            except Exception:
                pass
        # 无路径：对已有长稿再 finalize（清坏 tier）
        if not cands and len(old.get("body_text") or "") >= PDF_GOLD_BODY:
            src = (old.get("source") or "pdf_align").strip()
            if src.startswith("xml"):
                prefer = "xml_jats_fulltext"
            elif src.startswith("html"):
                prefer = "html_fulltext_gold"
            else:
                prefer = "pdf_align"
            neu = finalize_to_gold_bar(dict(old), fallback_title=tip, prefer_source=prefer)
            cands.append((len(neu.get("body_text") or ""), prefer, neu))
        if not cands:
            fail += 1
            continue
        cands.sort(key=lambda x: (is_pdf_gold(x[2]), x[0]), reverse=True)
        _blen, prefer, best = cands[0]
        best["doi"] = doi or best.get("doi") or ""
        best["corpus"] = "pdfku_library_fulltext_gold"
        best["enrich_from"] = f"repair_{prefer}"
        if is_pdf_gold(best):
            save_rec(best)
            fixed += 1
            by_via[prefer] = by_via.get(prefer, 0) + 1
            print(f"GOLD {eno} via={prefer} len={_blen}", flush=True)
        else:
            if _blen > len(old.get("body_text") or "") + 1000:
                best["gold_tier"] = "pdf_align_short" if prefer == "pdf_align" else "fulltext_short"
                best["complete"] = False
                save_rec(best)
            short += 1
    rep = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "already_gold": already,
        "newly_fixed": fixed,
        "still_short": short,
        "fail_or_empty": fail,
        "by_via": by_via,
        "floor": PDF_GOLD_BODY,
    }
    (OUT / "repair_format_adapt_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
