# -*- coding: utf-8 -*-
"""
修复未达 is_pdf_gold 的条目（是表 + pdfku 库）。

策略（不造假、不垫文）：
  1) 已有够长 pdf_align → _finalize_pdf（补标题/metrics/score）
  2) 本地有 PDF → 硬超时重抽 + finalize
  3) 含 provenance 垫文 → 优先重抽；抽不到则降级标记（不算金标）
  4) 仅 XML/无 PDF → 跳过（留给 online hunt）

用法：
  python -u -m scripts.gold_repair_not_gold
  python -u -m scripts.gold_repair_not_gold --corpus yes
  python -u -m scripts.gold_repair_not_gold --corpus pdfku --limit 200
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_hunt_fulltext import PDF_GOLD_BODY, is_pdf_gold  # noqa: E402
from scripts.gold_pdfku_library_full_gold import _finalize_pdf  # noqa: E402
from scripts.gold_strict_full_campaign import strict_issues  # noqa: E402
from scripts.gold_upgrade_5680 import (  # noqa: E402
    _build_pdf_index,
    _build_pdfku_xml_index,
    find_local_pdf,
    upgrade_from_pdf_hard,
)
import scripts.gold_upgrade_5680 as gu  # noqa: E402

YES = ROOT / "解析文件" / "yes_fulltext_gold"
PDFKU = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
LOG = ROOT / "解析文件" / "gold_repair_not_gold.log"
REPORT = ROOT / "解析文件" / "gold_repair_not_gold_report.json"


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _iter_papers(root: Path):
    for p in sorted(root.glob("batch_*/papers/*.json")):
        yield p


def _row(rec: dict[str, Any]) -> dict[str, str]:
    return {
        "entry_no": str(rec.get("entry_no") or ""),
        "q12_no": str(rec.get("q12_no") or ""),
        "en_title": str(rec.get("title") or ""),
        "doi": str(rec.get("doi") or ""),
        "pmid": str(rec.get("pmid") or ""),
        "source_url": str(rec.get("source_url") or ""),
    }


def _save(path: Path, rec: dict[str, Any]) -> None:
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")


def _strip_padding(body: str) -> str:
    if "[provenance]" not in body and "acquisition_exhausted" not in body:
        return body
    # 去掉垫文段落，保留原文主体
    parts = []
    for para in body.split("\n\n"):
        if "[provenance]" in para or "acquisition_exhausted" in para:
            continue
        parts.append(para)
    return "\n\n".join(parts).strip()


def repair_one(path: Path, *, corpus: str) -> dict[str, Any]:
    eno = path.stem
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"entry_no": eno, "status": "read_err", "error": str(exc)[:120]}

    if is_pdf_gold(rec):
        return {"entry_no": eno, "status": "already_gold"}

    body = rec.get("body_text") or ""
    doi = (rec.get("doi") or "").strip()
    padded = "[provenance]" in body or "acquisition_exhausted" in body
    tip = f"DOI {doi}" if doi else f"Entry {eno} fulltext"

    # 1) 够长 pdf_align：只抛光
    if (rec.get("source") or "") == "pdf_align" and len(body) >= PDF_GOLD_BODY and not padded:
        neu = _finalize_pdf(dict(rec), fallback_title=tip)
        if is_pdf_gold(neu):
            neu["gold_tier"] = "pdf_gold"
            neu["complete"] = True
            neu["repaired_by"] = "finalize_only"
            neu["repaired_at"] = datetime.now().isoformat(timespec="seconds")
            _save(path, neu)
            return {"entry_no": eno, "status": "fixed_finalize", "len": len(neu.get("body_text") or "")}

    # 2) 本地 PDF 重抽
    pdf = find_local_pdf(eno, doi=doi)
    if not pdf and rec.get("source_pdf"):
        cand = Path(str(rec["source_pdf"]))
        if cand.is_file():
            pdf = cand
    if pdf and pdf.is_file():
        try:
            psz = pdf.stat().st_size
        except OSError:
            psz = 0
        if psz > 12_000_000:
            return {"entry_no": eno, "status": "skip_huge_pdf", "size": psz}
        got = upgrade_from_pdf_hard(pdf, _row(rec), tip, timeout=55.0)
        if got:
            neu = _finalize_pdf(got, fallback_title=tip)
            neu["doi"] = doi or neu.get("doi") or ""
            neu["corpus"] = corpus
            neu["repaired_by"] = "reextract_pdf"
            neu["repaired_at"] = datetime.now().isoformat(timespec="seconds")
            if is_pdf_gold(neu):
                neu["gold_tier"] = "pdf_gold"
                neu["complete"] = True
                _save(path, neu)
                return {
                    "entry_no": eno,
                    "status": "fixed_reextract",
                    "len": len(neu.get("body_text") or ""),
                    "pdf": str(pdf),
                }
            # 比旧稿更长/更好则保存 short，供后续 online
            new_len = len(neu.get("body_text") or "")
            old_len = len(body)
            if new_len >= max(old_len + 500, int(old_len * 1.05)) or (
                padded and "[provenance]" not in (neu.get("body_text") or "")
            ):
                neu["gold_tier"] = "pdf_align_short" if new_len < PDF_GOLD_BODY else neu.get("gold_tier")
                neu["complete"] = False
                _save(path, neu)
                return {
                    "entry_no": eno,
                    "status": "improved_short",
                    "old": old_len,
                    "new": new_len,
                    "need": PDF_GOLD_BODY,
                    "issues": strict_issues(neu)[:3],
                }
            return {
                "entry_no": eno,
                "status": "reextract_no_gain",
                "len": new_len,
                "need": PDF_GOLD_BODY,
                "issues": strict_issues(neu)[:3],
            }
        return {"entry_no": eno, "status": "reextract_timeout_or_fail", "pdf": str(pdf)}

    # 3) 垫文且无 PDF：剥垫文后仍不达标则标记
    if padded:
        cleaned = _strip_padding(body)
        if cleaned != body:
            rec["body_text"] = cleaned
            rec.setdefault("metrics", {})["acquisition_exhausted"] = False
            rec["gold_tier"] = "needs_oa_pdf"
            rec["complete"] = False
            rec["repaired_by"] = "strip_padding"
            rec["repaired_at"] = datetime.now().isoformat(timespec="seconds")
            _save(path, rec)
            return {"entry_no": eno, "status": "stripped_padding", "len": len(cleaned)}

    return {
        "entry_no": eno,
        "status": "need_oa_or_local_pdf",
        "src": rec.get("source") or "",
        "len": len(body),
        "need": PDF_GOLD_BODY,
    }


def repair_corpus(root: Path, *, corpus: str, limit: int = 0) -> dict[str, Any]:
    stats: Counter[str] = Counter()
    fixed = 0
    t0 = time.time()
    paths = [p for p in _iter_papers(root)]
    todo = []
    for p in paths:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not is_pdf_gold(rec):
            todo.append(p)
    if limit:
        todo = todo[:limit]
    _log(f"{corpus}: not_gold={len(todo)} / papers={len(paths)}")
    for i, p in enumerate(todo, 1):
        res = repair_one(p, corpus=corpus)
        st = res.get("status") or "err"
        stats[st] += 1
        if st.startswith("fixed_"):
            fixed += 1
            _log(f"  [{i}/{len(todo)}] GOLD {res.get('entry_no')} via {st} len={res.get('len')}")
        elif st == "improved_short":
            _log(
                f"  [{i}/{len(todo)}] improved {res.get('entry_no')} "
                f"{res.get('old')}->{res.get('new')}"
            )
        elif i % 50 == 0 or st.endswith("fail") or "timeout" in st:
            _log(f"  [{i}/{len(todo)}] {res.get('entry_no')} {st}")
    elapsed = round(time.time() - t0, 1)
    # recount gold
    gold = 0
    for p in _iter_papers(root):
        try:
            if is_pdf_gold(json.loads(p.read_text(encoding="utf-8"))):
                gold += 1
        except Exception:
            pass
    out = {
        "corpus": corpus,
        "root": str(root),
        "attempted": len(todo),
        "fixed_to_gold": fixed,
        "stats": dict(stats),
        "pdf_gold_after": gold,
        "elapsed_sec": elapsed,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _log(f"{corpus} DONE fixed={fixed} gold_now={gold} stats={dict(stats)}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", choices=["all", "yes", "pdfku"], default="all")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    gu._PDF_INDEX = _build_pdf_index()
    gu._PDFKU_XML_INDEX = _build_pdfku_xml_index()
    _log(
        f"repair start idx_pdf={len(gu._PDF_INDEX or {})} "
        f"idx_xml={len(gu._PDFKU_XML_INDEX or {})} corpus={args.corpus}"
    )

    reports = []
    if args.corpus in ("all", "yes"):
        reports.append(repair_corpus(YES, corpus="yes_fulltext_gold", limit=args.limit))
    if args.corpus in ("all", "pdfku"):
        reports.append(
            repair_corpus(PDFKU, corpus="pdfku_library_fulltext_gold", limit=args.limit)
        )

    rep = {
        "reports": reports,
        "pdf_gold_body_floor": PDF_GOLD_BODY,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"REPORT {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
