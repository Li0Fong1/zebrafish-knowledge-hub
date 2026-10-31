# -*- coding: utf-8 -*-
"""
挽救：正文已达门槛但因坏 tier / 错误 source 未入金标的记录。
覆盖是表 + pdfku。不垫文、不降门槛。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_hunt_fulltext import GOLD_SOURCES, PDF_GOLD_BODY, finalize_to_gold_bar, is_pdf_gold
from scripts.gold_strict_full_campaign import STRICT_ROOT, save_rec as yes_save
from scripts.gold_pdfku_library_full_gold import OUT as PDFKU_OUT, save_rec as pdfku_save
from scripts.gold_upgrade_5680 import (
    PDFKU_HTML,
    PDFKU_XML,
    find_xml_for_entry,
    upgrade_from_html,
    upgrade_from_xml,
)
import scripts.gold_upgrade_5680 as gu
from scripts.gold_upgrade_5680 import _build_pdfku_xml_index


def _prefer(src: str, ctype: str = "") -> str:
    s = (src or "").strip()
    if s in GOLD_SOURCES:
        return s
    if s.startswith("xml") or ctype == "xml":
        return "xml_jats_fulltext"
    if s.startswith("html") or ctype == "html":
        return "html_fulltext_gold"
    if s.startswith("pdf"):
        return "pdf_align"
    return "xml_jats_fulltext" if "xml" in s else "pdf_align"


def rescue_corpus(name: str, root: Path, saver) -> dict:
    gu._PDFKU_XML_INDEX = _build_pdfku_xml_index()
    fixed = skipped = already = reextract = 0
    via = Counter()
    for p in root.glob("batch_*/papers/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        eno = p.stem
        if is_pdf_gold(rec):
            already += 1
            continue
        body = rec.get("body_text") or ""
        bl = len(body)
        tip = (rec.get("title") or f"Entry {eno}").strip()
        doi = (rec.get("doi") or "").strip()
        src = (rec.get("source") or "").strip()
        ctype = (rec.get("content_type") or "").strip()

        candidates: list[tuple[int, str, dict]] = []

        # 1) 现有够长正文：直接 finalize 清坏 tier / 纠正 source
        if bl >= PDF_GOLD_BODY:
            prefer = _prefer(src, ctype)
            neu = finalize_to_gold_bar(dict(rec), fallback_title=tip, prefer_source=prefer)
            candidates.append((len(neu.get("body_text") or ""), prefer, neu))

        # 2) 不够或 finalize 仍失败：重抽本地 XML/HTML
        row = {
            "entry_no": eno,
            "doi": doi,
            "pmid": rec.get("pmid") or "",
            "en_title": tip,
            "q12_no": rec.get("q12_no") or "",
            "source_url": rec.get("source_url") or "",
        }
        xml = find_xml_for_entry(eno, doi=doi)
        if xml and xml.is_file():
            try:
                xrec = upgrade_from_xml(xml, row, tip, None)
                if xrec:
                    xrec = finalize_to_gold_bar(
                        xrec, fallback_title=tip, prefer_source="xml_jats_fulltext"
                    )
                    candidates.append(
                        (len(xrec.get("body_text") or ""), "xml_jats_fulltext", xrec)
                    )
                    reextract += 1
            except Exception:
                pass
        if PDFKU_HTML.is_dir():
            for hp in list(PDFKU_HTML.glob(f"{eno}_*"))[:1]:
                if hp.is_file():
                    try:
                        hrec = upgrade_from_html(hp, row, tip)
                        if hrec:
                            hrec = finalize_to_gold_bar(
                                hrec, fallback_title=tip, prefer_source="html_fulltext_gold"
                            )
                            candidates.append(
                                (len(hrec.get("body_text") or ""), "html_fulltext_gold", hrec)
                            )
                            reextract += 1
                    except Exception:
                        pass
                break

        if not candidates:
            skipped += 1
            continue
        candidates.sort(key=lambda x: (is_pdf_gold(x[2]), x[0]), reverse=True)
        _n, prefer, best = candidates[0]
        if is_pdf_gold(best):
            best["doi"] = doi or best.get("doi") or ""
            if name == "pdfku":
                best["corpus"] = "pdfku_library_fulltext_gold"
                saver(best)
            else:
                # yes save_rec(rec, batch_no)
                bno = int(eno) // 100 + 1 if eno.isdigit() else 1
                try:
                    from scripts.gold_strict_full_campaign import batch_no_for

                    bno = batch_no_for(eno)
                except Exception:
                    pass
                saver(best, bno)
            fixed += 1
            via[prefer] += 1
            print(f"[{name}] GOLD {eno} via={prefer} len={_n}", flush=True)
        else:
            # 显著更长则覆盖短稿
            if _n > bl + 2000:
                best["complete"] = False
                best["gold_tier"] = "fulltext_short" if prefer != "pdf_align" else "pdf_align_short"
                if name == "pdfku":
                    best["corpus"] = "pdfku_library_fulltext_gold"
                    saver(best)
                else:
                    bno = int(eno) // 100 + 1 if eno.isdigit() else 1
                    try:
                        from scripts.gold_strict_full_campaign import batch_no_for

                        bno = batch_no_for(eno)
                    except Exception:
                        pass
                    saver(best, bno)
            skipped += 1

    return {
        "corpus": name,
        "already": already,
        "fixed": fixed,
        "skipped": skipped,
        "reextract_attempts": reextract,
        "via": dict(via),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> int:
    reps = []
    reps.append(rescue_corpus("yes", STRICT_ROOT, yes_save))
    reps.append(rescue_corpus("pdfku", PDFKU_OUT, pdfku_save))
    out = ROOT / "解析文件" / "gold_rescue_bad_tier_report.json"
    out.write_text(json.dumps(reps, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(reps, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
