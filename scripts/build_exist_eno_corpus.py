# -*- coding: utf-8 -*-
"""
从「已存在表」构建无 PMID 文献（主要为 pdf库）向量化语料。

键：doc_id = eno:{entry_no}
文本：优先金标 JSON 的 sections（abstract/intro/…）+ 截断 body。

用法：
  python -m scripts.build_exist_eno_corpus
  python -m scripts.build_exist_eno_corpus --limit 100
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from config import EXIST_ENO_CORPUS_JSONL, EXIST_ENO_MAX_BODY_CHARS

EXIST_CSV = ROOT / "解析文件" / "已存在表.csv"
YES_GOLD = ROOT / "解析文件" / "yes_fulltext_gold"
PDFKU_GOLD = ROOT / "解析文件" / "pdfku_library_fulltext_gold"


def _load_gold_index() -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for root in (PDFKU_GOLD, YES_GOLD):
        if not root.is_dir():
            continue
        for p in root.glob("batch_*/papers/*.json"):
            # pdf库优先：后写 yes 不覆盖已有 pdfku（先扫 pdfku）
            if p.stem not in idx:
                idx[p.stem] = p
    return idx


def _sections_text(sections: dict) -> tuple[str, str, str]:
    """返回 title, abstract, body_from_sections。"""
    if not isinstance(sections, dict):
        return "", "", ""
    title = str(sections.get("title") or "").strip()
    abstract = str(sections.get("abstract") or "").strip()
    parts = []
    for k in ("introduction", "results", "discussion", "conclusion", "methods"):
        v = str(sections.get(k) or "").strip()
        if v:
            parts.append(f"【{k}】\n{v}")
    return title, abstract, "\n\n".join(parts)


def build(*, limit: int = 0, resume: bool = True) -> dict:
    if not EXIST_CSV.is_file():
        raise SystemExit(f"缺少 {EXIST_CSV}")

    gold_idx = _load_gold_index()
    safe_print(f"金标 JSON 索引 {len(gold_idx)} 篇")

    done: set[str] = set()
    out_path = Path(EXIST_ENO_CORPUS_JSONL)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if resume and out_path.is_file():
        with out_path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(str(json.loads(line).get("doc_id") or ""))
                except Exception:
                    continue
        safe_print(f"resume 已有 {len(done)} 条")

    rows = list(csv.DictReader(EXIST_CSV.open(encoding="utf-8-sig", newline="")))
    targets = []
    for r in rows:
        eno = (r.get("entry_no") or "").strip()
        pmid = (r.get("pmid") or "").strip()
        if not eno:
            continue
        # 有 PMID 的走原 pubmed_yes 管线；这里只补无 PMID
        if pmid.isdigit():
            continue
        doc_id = f"eno:{eno}"
        if doc_id in done:
            continue
        targets.append(r)

    if limit > 0:
        targets = targets[:limit]
    safe_print(f"待写入无 PMID 文献 {len(targets)} 篇（已存在表无 PMID 总量见统计）")

    written = 0
    skipped_no_gold = 0
    skipped_empty = 0
    mode = "a" if (resume and done) else "w"
    with out_path.open(mode, encoding="utf-8") as fout:
        for r in targets:
            eno = (r.get("entry_no") or "").strip()
            doc_id = f"eno:{eno}"
            gp = gold_idx.get(eno)
            title_en = (r.get("en_title") or r.get("zh_title") or "").strip()
            title_zh = (r.get("zh_title") or r.get("en_title") or "").strip()
            doi = (r.get("doi") or "").strip()
            abstract = ""
            body = ""
            source = "exist_table"
            if gp and gp.is_file():
                try:
                    rec = json.loads(gp.read_text(encoding="utf-8"))
                except Exception:
                    rec = {}
                source = str(rec.get("source") or "gold_json")
                st, sa, sb = _sections_text(rec.get("sections") or {})
                title_en = title_en or st or str(rec.get("title") or "")
                title_zh = title_zh or title_en
                abstract = sa
                body = sb or str(rec.get("body_text") or "")
                doi = doi or str(rec.get("doi") or "")
                if not abstract and body:
                    # 无独立摘要时用正文前段充当摘要块
                    abstract = body[:2500]
            else:
                skipped_no_gold += 1
                # 仍可用表内标题做弱向量
                if not title_en and not title_zh:
                    skipped_empty += 1
                    continue

            if len(body) > EXIST_ENO_MAX_BODY_CHARS:
                body = body[:EXIST_ENO_MAX_BODY_CHARS]

            if not (title_en or title_zh or abstract or body):
                skipped_empty += 1
                continue

            out = {
                "doc_id": doc_id,
                "entry_no": eno,
                "pmid": "",
                "title_en": title_en,
                "title_zh": title_zh,
                "abstract_en": abstract,
                "abstract_zh": "",
                "body_text": body,
                "doi": doi,
                "has_fulltext": bool(body and len(body) >= 500),
                "corpus_source": "exist_eno",
                "source": source,
                "judgement": "是",
            }
            fout.write(json.dumps(out, ensure_ascii=False) + "\n")
            written += 1
            if written % 500 == 0:
                safe_print(f"  …已写 {written}")

    # 统计
    total_no_pmid = sum(1 for r in rows if (r.get("entry_no") or "").strip() and not (r.get("pmid") or "").strip().isdigit())
    summary = {
        "exist_rows": len(rows),
        "exist_no_pmid": total_no_pmid,
        "written_this_run": written,
        "corpus_path": str(out_path),
        "corpus_lines": sum(1 for _ in out_path.open(encoding="utf-8")) if out_path.is_file() else 0,
        "skipped_no_gold": skipped_no_gold,
        "skipped_empty": skipped_empty,
        "gold_index": len(gold_idx),
    }
    rep = ROOT / "解析文件" / "exist_eno_corpus_build.json"
    rep.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    safe_print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()
    build(limit=args.limit, resume=not args.no_resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
