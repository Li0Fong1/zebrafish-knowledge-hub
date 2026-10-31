# -*- coding: utf-8 -*-
"""
将 pdf库/xml 下全部 JATS XML 按十五篇级严格金标写入 yes_fulltext_gold。

规则（不偷工）：
  - 真实 upgrade_from_xml + polish_pass；不做 exhausted 假垫文
  - 已是合格 pdf_align 且正文足够 → 不降级覆盖
  - XML 达标后 source=xml_jats_fulltext，须过 strict_issues
  - 与 PDF 金标战役并行时：仅当 XML 明显更优且当前非 pdf_align 才写入

用法：
  python -u -m scripts.gold_pdfku_xml_campaign --workers 4
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_gap_fill import polish_pass  # noqa: E402
from scripts.gold_strict_full_campaign import (  # noqa: E402
    STRICT_ROOT,
    batch_no_for,
    save_rec,
    strict_issues,
)
from scripts.gold_upgrade_5680 import (  # noqa: E402
    MIN_BODY,
    PDFKU_XML,
    _build_pdfku_xml_index,
    find_xml_for_entry,
    load_exist,
    load_jsonl_index,
    upgrade_from_xml,
)
import scripts.gold_upgrade_5680 as gu  # noqa: E402

STATUS = STRICT_ROOT / "pdfku_xml_campaign_status.json"
LOG = STRICT_ROOT / "pdfku_xml_campaign.log"
REPORT = STRICT_ROOT / "pdfku_xml_campaign_report.json"


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    STRICT_ROOT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _worker_init() -> None:
    gu._PDFKU_XML_INDEX = gu._build_pdfku_xml_index()


def _body_q(body: str) -> int:
    n = len(body or "")
    if n < 500:
        return 0
    low = body.lower()
    score = n
    for t in ("introduction", "methods", "results", "discussion", "references", "abstract"):
        if t in low:
            score += 3000
    return score


def process_one(payload: dict[str, Any]) -> dict[str, Any]:
    row = payload["row"]
    eno = str(row["entry_no"])
    title = (row.get("en_title") or "").strip()
    bno = int(payload["batch_no"])
    xml = find_xml_for_entry(eno, doi=(row.get("doi") or ""))
    if not xml:
        return {"entry_no": eno, "status": "no_xml"}

    # 现有金标记录
    old_paths = list(STRICT_ROOT.glob(f"batch_*/papers/{eno}.json"))
    old: dict[str, Any] | None = None
    if old_paths:
        try:
            old = json.loads(old_paths[0].read_text(encoding="utf-8"))
        except Exception:
            old = None

    old_body = (old or {}).get("body_text") or ""
    old_src = ((old or {}).get("source") or "").strip()
    # 已是合格 pdf_align：不降级覆盖
    if old_src == "pdf_align" and len(old_body) >= MIN_BODY and not (
        "[provenance]" in old_body
    ):
        return {
            "entry_no": eno,
            "status": "keep_pdf_align",
            "old": len(old_body),
            "xml": xml.name,
        }
    # 已由本战役升过且仍严格通过 → 跳过（支持断点续跑）
    if (
        old
        and (old.get("enrich_from") or "") == "pdfku_xml"
        and old_src == "xml_jats_fulltext"
        and len(old_body) >= MIN_BODY
        and not strict_issues(old)
        and "[provenance]" not in old_body
    ):
        return {
            "entry_no": eno,
            "status": "already_xml_gold",
            "old": len(old_body),
            "xml": xml.name,
        }

    try:
        rec = upgrade_from_xml(xml, row, title, payload.get("hit"))
    except Exception as exc:  # noqa: BLE001
        return {"entry_no": eno, "status": "xml_fail", "error": str(exc)[:200]}
    if not rec:
        return {"entry_no": eno, "status": "xml_empty", "xml": xml.name}

    rec["source"] = "xml_jats_fulltext"
    rec = polish_pass(rec)
    rec["source"] = "xml_jats_fulltext"
    if isinstance(rec.get("metrics"), dict):
        rec["metrics"]["acquisition_exhausted"] = False
        rec["metrics"].pop("oa_unreachable", None)
    issues = strict_issues(rec)
    if issues:
        rec = polish_pass(rec)
        rec["source"] = "xml_jats_fulltext"
        issues = strict_issues(rec)

    new_body = rec.get("body_text") or ""
    if len(new_body) < MIN_BODY:
        return {
            "entry_no": eno,
            "status": "too_short",
            "len": len(new_body),
            "xml": xml.name,
        }

    accept = False
    if not old:
        accept = True
    elif old_src != "pdf_align":
        if len(new_body) >= max(len(old_body) + 500, MIN_BODY) and _body_q(new_body) >= _body_q(old_body):
            accept = True
        elif not issues and (strict_issues(old) or len(old_body) < len(new_body)):
            accept = True
        elif "[provenance]" in old_body and len(new_body) >= MIN_BODY:
            accept = True
    if not accept:
        return {
            "entry_no": eno,
            "status": "no_gain",
            "old": len(old_body),
            "new": len(new_body),
            "old_src": old_src,
        }

    if issues:
        return {
            "entry_no": eno,
            "status": "strict_fail",
            "issues": issues[:5],
            "len": len(new_body),
        }
    if "[provenance]" in new_body or (rec.get("metrics") or {}).get("acquisition_exhausted"):
        return {"entry_no": eno, "status": "padded_reject", "len": len(new_body)}

    for p in list(STRICT_ROOT.glob(f"batch_*/papers/{eno}.json")):
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (cur.get("source") or "") == "pdf_align" and len(cur.get("body_text") or "") >= MIN_BODY:
            return {
                "entry_no": eno,
                "status": "keep_pdf_align",
                "old": len(cur.get("body_text") or ""),
                "xml": xml.name,
            }

    rec["gold_tier"] = "xml_jats_gold"
    rec["enrich_from"] = "pdfku_xml"
    rec["xml_path"] = str(xml)
    save_rec(rec, bno)
    return {
        "entry_no": eno,
        "status": "upgraded",
        "old": len(old_body),
        "new": len(new_body),
        "old_src": old_src,
        "xml": xml.name,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--process",
        action="store_true",
        help="用进程池（默认线程池，更稳、可与 PDF hunt 并行）",
    )
    args = ap.parse_args()

    _log(f"PDFKU XML campaign start workers={args.workers} process={args.process}")
    idx = _build_pdfku_xml_index()
    gu._PDFKU_XML_INDEX = idx
    _log(f"pdfku_xml_index={len(idx)} dir={PDFKU_XML}")

    rows = load_exist()
    rows_order = {str(r["entry_no"]): i for i, r in enumerate(rows)}
    hit_idx = load_jsonl_index()

    targets = []
    seen: set[str] = set()
    for r in rows:
        eno = str(r["entry_no"])
        if eno in idx:
            targets.append(r)
            seen.add(eno)
            continue
        # DOI 交叉：文件名 entry 与「是」表不一致
        doi = (r.get("doi") or "").strip().lower()
        if doi and gu._PDFKU_DOI_INDEX and doi in gu._PDFKU_DOI_INDEX:
            targets.append(r)
            seen.add(eno)
    if args.limit:
        targets = targets[: args.limit]
    _log(
        f"exist_with_pdfku_xml={len(targets)} / exist={len(rows)} "
        f"(eno_hit={sum(1 for r in targets if str(r['entry_no']) in idx)})"
    )

    stats = {
        "upgraded": 0,
        "keep_pdf_align": 0,
        "already_xml_gold": 0,
        "no_gain": 0,
        "strict_fail": 0,
        "too_short": 0,
        "padded_reject": 0,
        "xml_fail": 0,
        "xml_empty": 0,
        "no_xml": 0,
        "err": 0,
    }
    t0 = time.time()
    workers = max(1, args.workers)
    chunk = max(workers * 4, 8)

    def _run_batch(pool, batch: list[dict[str, str]]) -> None:
        futs = []
        for row in batch:
            eno = str(row["entry_no"])
            h = hit_idx.get(eno)
            hit_slim = None
            if h:
                hit_slim = {
                    "en_title": h.get("en_title"),
                    "content_type": h.get("content_type"),
                    "body_text": (h.get("body_text") or "")[:40000],
                }
            payload = {
                "row": dict(row),
                "hit": hit_slim,
                "batch_no": batch_no_for(eno, rows_order),
            }
            futs.append(pool.submit(process_one, payload))
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
                _log(
                    f"  UP {res.get('entry_no')} {res.get('old')}->{res.get('new')} "
                    f"was={res.get('old_src')} xml={res.get('xml')}"
                )
            elif st == "strict_fail":
                _log(f"  strict_fail {res.get('entry_no')}: {res.get('issues')}")

    off = 0
    while off < len(targets):
        batch = targets[off : off + chunk]
        try:
            if args.process:
                with ProcessPoolExecutor(
                    max_workers=workers,
                    initializer=_worker_init,
                    max_tasks_per_child=8,
                ) as pool:
                    _run_batch(pool, batch)
            else:
                gu._PDFKU_XML_INDEX = idx
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    _run_batch(pool, batch)
        except Exception as exc:  # noqa: BLE001
            stats["err"] += 1
            _log(f"  batch_fail@{off}: {exc}; retry serial")
            for row in batch:
                try:
                    eno = str(row["entry_no"])
                    h = hit_idx.get(eno)
                    hit_slim = None
                    if h:
                        hit_slim = {
                            "en_title": h.get("en_title"),
                            "content_type": h.get("content_type"),
                            "body_text": (h.get("body_text") or "")[:40000],
                        }
                    res = process_one(
                        {
                            "row": dict(row),
                            "hit": hit_slim,
                            "batch_no": batch_no_for(eno, rows_order),
                        }
                    )
                    st = res.get("status") or "err"
                    if st in stats:
                        stats[st] += 1
                    else:
                        stats["err"] += 1
                    if st == "upgraded":
                        _log(
                            f"  UP {res.get('entry_no')} {res.get('old')}->{res.get('new')} "
                            f"was={res.get('old_src')} xml={res.get('xml')}"
                        )
                except Exception as e2:  # noqa: BLE001
                    stats["err"] += 1
                    _log(f"  serial_err {row.get('entry_no')}: {e2}")
        off += len(batch)
        _log(f"  progress {off}/{len(targets)} {stats}")
        STATUS.write_text(
            json.dumps(
                {
                    "progress": off,
                    "need": len(targets),
                    "stats": stats,
                    "elapsed_sec": round(time.time() - t0, 1),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    xml_ok = 0
    xml_strict = 0
    pdf_keep = 0
    for eno in idx:
        if eno not in rows_order:
            continue
        paths = list(STRICT_ROOT.glob(f"batch_*/papers/{eno}.json"))
        if not paths:
            continue
        rec = json.loads(paths[0].read_text(encoding="utf-8"))
        src = rec.get("source") or ""
        if src == "pdf_align":
            pdf_keep += 1
        elif src.startswith("xml_jats"):
            xml_ok += 1
            if not strict_issues(rec):
                xml_strict += 1
    rep = {
        "final": True,
        "pdfku_xml_files": len(idx),
        "exist_matched": len(targets),
        "targeted": len(targets),
        "stats": stats,
        "matched_now_pdf_align": pdf_keep,
        "matched_now_xml_jats_source": xml_ok,
        "matched_now_xml_jats_strict_pass": xml_strict,
        "elapsed_sec": round(time.time() - t0, 1),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "note": (
            "仅处理「是」表∩pdf库/xml；XML 金标=xml_jats_fulltext+strict；"
            "不覆盖合格 pdf_align；不做假垫文；非「是」表 XML 不入库"
        ),
    }
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    STATUS.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"FINAL {rep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
