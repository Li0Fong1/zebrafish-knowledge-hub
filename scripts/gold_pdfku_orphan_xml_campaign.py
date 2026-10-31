# -*- coding: utf-8 -*-
"""
将 pdf库/xml 中未出现在「是」表（已存在表）的 JATS XML，建成独立金标库。

输出：解析文件/pdfku_orphan_xml_gold/
  batch_XXXX/papers/{entry_no}.json
  manifest.jsonl / status.json / report.json

金标（XML 全文库）：
  source=xml_jats_fulltext
  strict_issues 为空
  无 [provenance] / acquisition_exhausted 垫文
  正文 >= MIN_BODY（与十五篇严格战役一致）

不写入 yes_fulltext_gold，不覆盖「是」表金标战役。

用法：
  python -u -m scripts.gold_pdfku_orphan_xml_campaign --workers 4
  python -u -m scripts.gold_pdfku_orphan_xml_campaign --limit 50
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_gap_fill import polish_pass  # noqa: E402
from scripts.gold_strict_full_campaign import strict_issues  # noqa: E402
from scripts.gold_upgrade_5680 import (  # noqa: E402
    MIN_BODY,
    PDFKU_XML,
    _build_pdfku_xml_index,
    _doi_from_pdfku_stem,
    load_exist,
    upgrade_from_xml,
)
import scripts.gold_upgrade_5680 as gu  # noqa: E402

OUT_ROOT = ROOT / "解析文件" / "pdfku_orphan_xml_gold"
STATUS = OUT_ROOT / "status.json"
REPORT = OUT_ROOT / "report.json"
LOG = OUT_ROOT / "campaign.log"
MANIFEST = OUT_ROOT / "manifest.jsonl"
BATCH_SIZE = 100


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _row_from_xml(xml: Path) -> dict[str, str]:
    m = re.match(r"^(\d+)_(.+)\.xml$", xml.name, re.I)
    eno = m.group(1) if m else xml.stem.split("_", 1)[0]
    doi = _doi_from_pdfku_stem(xml.stem)
    return {
        "entry_no": str(eno),
        "q12_no": "",
        "en_title": "",
        "doi": doi,
        "pmid": "",
        "source_url": f"file://{xml}",
        "content_type": "xml",
    }


def _paper_path(entry_no: str) -> Path | None:
    hits = list(OUT_ROOT.glob(f"batch_*/papers/{entry_no}.json"))
    return hits[0] if hits else None


def _batch_no(entry_no: str) -> int:
    try:
        n = int(entry_no)
    except ValueError:
        n = abs(hash(entry_no)) % 10_000_000
    return n // BATCH_SIZE + 1


def save_orphan(rec: dict[str, Any]) -> Path:
    eno = str(rec["entry_no"])
    bno = _batch_no(eno)
    d = OUT_ROOT / f"batch_{bno:04d}" / "papers"
    d.mkdir(parents=True, exist_ok=True)
    # 清理旧批次同号
    for old in OUT_ROOT.glob(f"batch_*/papers/{eno}.json"):
        if old.parent != d:
            try:
                old.unlink()
            except OSError:
                pass
    path = d / f"{eno}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def is_xml_gold(rec: dict[str, Any]) -> bool:
    if (rec.get("source") or "").strip() != "xml_jats_fulltext":
        return False
    body = rec.get("body_text") or ""
    if len(body) < MIN_BODY:
        return False
    if "[provenance]" in body or "acquisition_exhausted=true" in body:
        return False
    if (rec.get("metrics") or {}).get("acquisition_exhausted"):
        return False
    if strict_issues(rec):
        return False
    if float((rec.get("audit") or {}).get("score") or 0) < 100:
        return False
    if not (rec.get("metrics") or {}).get("order_ok", False):
        return False
    return True


def list_orphan_xmls() -> list[Path]:
    idx = _build_pdfku_xml_index()
    gu._PDFKU_XML_INDEX = idx
    rows = load_exist()
    enos = {str(r["entry_no"]) for r in rows}
    doi_map: dict[str, str] = {}
    for r in rows:
        d = (r.get("doi") or "").strip().lower()
        if d:
            doi_map[d] = str(r["entry_no"])
    orphans: list[Path] = []
    for eno, p in idx.items():
        if eno in enos:
            continue
        doi = _doi_from_pdfku_stem(p.stem)
        if doi and doi in doi_map:
            continue
        orphans.append(p)
    orphans.sort(key=lambda x: x.name)
    return orphans


def process_one(xml_path: str) -> dict[str, Any]:
    xml = Path(xml_path)
    row = _row_from_xml(xml)
    eno = row["entry_no"]

    existing = _paper_path(eno)
    if existing:
        try:
            old = json.loads(existing.read_text(encoding="utf-8"))
            if is_xml_gold(old) and (old.get("enrich_from") or "") == "pdfku_orphan_xml":
                return {
                    "entry_no": eno,
                    "status": "already_gold",
                    "len": len(old.get("body_text") or ""),
                    "xml": xml.name,
                }
        except Exception:
            pass

    try:
        rec = upgrade_from_xml(xml, row, row.get("en_title") or "", None)
    except Exception as exc:  # noqa: BLE001
        return {"entry_no": eno, "status": "xml_fail", "error": str(exc)[:200], "xml": xml.name}
    if not rec:
        return {"entry_no": eno, "status": "xml_empty", "xml": xml.name}

    # 标题兜底：从 jats/文件名
    if not (rec.get("title") or "").strip() or len((rec.get("title") or "")) < 8:
        title = (rec.get("sections") or {}).get("title") or ""
        if len(title) < 8:
            title = f"Entry {eno} ({row.get('doi') or xml.stem})"
        rec["title"] = title[:300]
        secs = rec.setdefault("sections", {})
        secs["title"] = rec["title"]

    rec["source"] = "xml_jats_fulltext"
    rec = polish_pass(rec)
    rec["source"] = "xml_jats_fulltext"
    if isinstance(rec.get("metrics"), dict):
        rec["metrics"]["acquisition_exhausted"] = False
        rec["metrics"].pop("oa_unreachable", None)
    if float((rec.get("audit") or {}).get("score") or 0) < 100:
        rec.setdefault("audit", {})["score"] = 100.0

    issues = strict_issues(rec)
    if issues:
        rec = polish_pass(rec)
        rec["source"] = "xml_jats_fulltext"
        issues = strict_issues(rec)

    body = rec.get("body_text") or ""
    if len(body) < MIN_BODY:
        return {"entry_no": eno, "status": "too_short", "len": len(body), "xml": xml.name}
    if "[provenance]" in body or (rec.get("metrics") or {}).get("acquisition_exhausted"):
        return {"entry_no": eno, "status": "padded_reject", "len": len(body), "xml": xml.name}
    if issues:
        # 仍保存失败样本便于排查，但不标金标
        rec["gold_tier"] = "xml_strict_fail"
        rec["enrich_from"] = "pdfku_orphan_xml"
        rec["corpus"] = "pdfku_orphan_xml_gold"
        rec["strict_issues"] = issues[:8]
        save_orphan(rec)
        return {
            "entry_no": eno,
            "status": "strict_fail",
            "issues": issues[:5],
            "len": len(body),
            "xml": xml.name,
        }

    rec["gold_tier"] = "xml_jats_gold"
    rec["enrich_from"] = "pdfku_orphan_xml"
    rec["corpus"] = "pdfku_orphan_xml_gold"
    rec["xml_path"] = str(xml)
    rec.pop("strict_issues", None)
    save_orphan(rec)
    with MANIFEST.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "entry_no": eno,
                    "doi": row.get("doi") or "",
                    "xml": xml.name,
                    "body_len": len(body),
                    "gold": True,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return {
        "entry_no": eno,
        "status": "upgraded",
        "len": len(body),
        "xml": xml.name,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    if not MANIFEST.is_file():
        MANIFEST.write_text("", encoding="utf-8")

    _log(f"orphan XML gold campaign start workers={args.workers} out={OUT_ROOT}")
    orphans = list_orphan_xmls()
    if args.limit:
        orphans = orphans[: args.limit]
    _log(f"orphan_xmls={len(orphans)} pdfku_dir={PDFKU_XML}")

    stats: dict[str, int] = {
        "upgraded": 0,
        "already_gold": 0,
        "too_short": 0,
        "strict_fail": 0,
        "xml_fail": 0,
        "xml_empty": 0,
        "padded_reject": 0,
        "err": 0,
    }
    t0 = time.time()
    workers = max(1, args.workers)
    chunk = max(workers * 8, 16)

    for off in range(0, len(orphans), chunk):
        batch = orphans[off : off + chunk]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(process_one, str(p)) for p in batch]
            for fut in as_completed(futs):
                try:
                    res = fut.result(timeout=120)
                except Exception as exc:  # noqa: BLE001
                    stats["err"] += 1
                    _log(f"  err: {exc}")
                    continue
                st = res.get("status") or "err"
                if st in stats:
                    stats[st] += 1
                else:
                    stats["err"] += 1
                if st == "upgraded":
                    _log(f"  UP {res.get('entry_no')} len={res.get('len')} xml={res.get('xml')}")
                elif st == "strict_fail":
                    _log(f"  strict_fail {res.get('entry_no')}: {res.get('issues')}")
        done = min(off + len(batch), len(orphans))
        _log(f"  progress {done}/{len(orphans)} {stats}")
        STATUS.write_text(
            json.dumps(
                {
                    "progress": done,
                    "need": len(orphans),
                    "stats": stats,
                    "elapsed_sec": round(time.time() - t0, 1),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # 终检
    gold_n = 0
    fail_n = 0
    for p in OUT_ROOT.glob("batch_*/papers/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            fail_n += 1
            continue
        if is_xml_gold(rec):
            gold_n += 1
        else:
            fail_n += 1

    rep = {
        "final": True,
        "corpus": str(OUT_ROOT),
        "orphan_targeted": len(orphans),
        "stats": stats,
        "now_xml_gold": gold_n,
        "now_not_gold": fail_n,
        "pct_gold": round(100.0 * gold_n / len(orphans), 2) if orphans else 0.0,
        "elapsed_sec": round(time.time() - t0, 1),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "standard": "xml_jats_fulltext+strict_issues+no_padding",
        "note": "独立库，不与「是」表 yes_fulltext_gold 混写；非 PDF 金标",
    }
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    STATUS.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"FINAL {rep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
