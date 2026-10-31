# -*- coding: utf-8 -*-
"""
用金标全文升级 pubmed_yes_corpus（对齐马铃薯「全文入向量」）。
对已有 PMID 行写入 body_text / sections 摘要，并产出待重嵌清单。

用法：
  python -m scripts.upgrade_pubmed_corpus_with_gold_fulltext
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from config import EXIST_ENO_MAX_BODY_CHARS, PUBMED_YES_CORPUS_JSONL
from rag.text_clean import clean_scientific_text

YES_GOLD = ROOT / "解析文件" / "yes_fulltext_gold"
PDFKU_GOLD = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
OUT_UPGRADED = ROOT / "data" / "literature" / "pubmed_yes_fulltext_upgrade.jsonl"
REPORT = ROOT / "解析文件" / "pubmed_corpus_gold_fulltext_upgrade.json"


def _gold_by_pmid() -> dict[str, dict]:
    by: dict[str, dict] = {}
    for root in (YES_GOLD, PDFKU_GOLD):
        if not root.is_dir():
            continue
        for p in root.glob("batch_*/papers/*.json"):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            pmid = str(rec.get("pmid") or "").strip()
            if not pmid.isdigit():
                continue
            body = clean_scientific_text(str(rec.get("body_text") or ""))
            secs = rec.get("sections") or {}
            abs_ = ""
            if isinstance(secs, dict):
                abs_ = str(secs.get("abstract") or "").strip()
            if len(body) < 500 and not abs_:
                continue
            prev = by.get(pmid)
            if prev and len(prev.get("body_text") or "") >= len(body):
                continue
            by[pmid] = {
                "pmid": pmid,
                "body_text": body[: max(EXIST_ENO_MAX_BODY_CHARS, 60000)],
                "abstract_en": abs_,
                "title": str(rec.get("title") or ""),
                "entry_no": str(rec.get("entry_no") or p.stem),
                "source": str(rec.get("source") or ""),
                "has_fulltext": True,
            }
    return by


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    corpus_path = Path(PUBMED_YES_CORPUS_JSONL)
    if not corpus_path.is_file():
        safe_print(f"缺少语料 {corpus_path}")
        return 1

    gold = _gold_by_pmid()
    safe_print(f"金标可升级 PMID {len(gold)}")

    rows = []
    with corpus_path.open(encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))

    upgraded = 0
    already = 0
    out_rows = []
    for rec in rows:
        pmid = str(rec.get("pmid") or "").strip()
        g = gold.get(pmid)
        old_body = (rec.get("body_text") or "").strip()
        if g and len(g.get("body_text") or "") >= 500:
            if len(old_body) >= 500 and len(old_body) >= len(g["body_text"]) * 0.8:
                already += 1
            else:
                rec["body_text"] = g["body_text"]
                rec["has_fulltext"] = True
                if g.get("abstract_en") and not (rec.get("abstract_en") or "").strip():
                    rec["abstract_en"] = g["abstract_en"]
                rec["fulltext_source"] = g.get("source") or "gold_json"
                upgraded += 1
                out_rows.append(rec)
        if args.limit and upgraded >= args.limit:
            break

    # 写回主语料（全量）
    tmp = corpus_path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(corpus_path)

    OUT_UPGRADED.parent.mkdir(parents=True, exist_ok=True)
    with OUT_UPGRADED.open("w", encoding="utf-8") as f:
        for rec in out_rows:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    summary = {
        "corpus": str(corpus_path),
        "gold_pmid_with_body": len(gold),
        "upgraded": upgraded,
        "already_had_fulltext": already,
        "upgrade_list": str(OUT_UPGRADED),
        "upgrade_list_n": len(out_rows),
    }
    REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    safe_print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
