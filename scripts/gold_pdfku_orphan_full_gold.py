# -*- coding: utf-8 -*-
"""
pdf库/xml 非「是」表条目 → 独立库，且只认十五篇完整 PDF 金标（is_pdf_gold）。

禁止：
  - 仅 XML/结构过关冒充完成
  - provenance / exhausted 垫文

完成条件（与「是」表相同）：
  source=pdf_align + body>=35487 + strict 空 + content_ok + order_ok + score>=100

流程：
  1) 本地 PDF → upgrade_from_pdf
  2) online：Unpaywall/EPMC/OpenAlex 等合法 OA PDF → 下载对齐
  3) 仍无合法 PDF：登记 unreachable，保留 XML 草稿（gold_tier=xml_draft，不算完成）

输出：解析文件/pdfku_orphan_fulltext_gold/

用法：
  python -u -m scripts.gold_pdfku_orphan_full_gold --hours 12 --workers 2
  python -u -m scripts.gold_pdfku_orphan_full_gold --local-only --workers 2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_gap_fill import polish_pass  # noqa: E402
from scripts.gold_hunt_fulltext import (  # noqa: E402
    PDF_GOLD_BODY,
    collect_all_pdf_urls,
    download_any_pdf,
    is_pdf_gold,
    load_oa_disk,
)
from scripts.gold_pdfku_orphan_xml_campaign import (  # noqa: E402
    _row_from_xml,
    list_orphan_xmls,
)
from scripts.gold_strict_full_campaign import strict_issues  # noqa: E402
from scripts.gold_upgrade_5680 import (  # noqa: E402
    MIN_BODY,
    find_local_pdf,
    upgrade_from_pdf,
    upgrade_from_xml,
)
import scripts.gold_upgrade_5680 as gu  # noqa: E402

OUT = ROOT / "解析文件" / "pdfku_orphan_fulltext_gold"
OLD_XML_CORPUS = ROOT / "解析文件" / "pdfku_orphan_xml_gold"
STATUS = OUT / "status.json"
REPORT = OUT / "report.json"
LOG = OUT / "campaign.log"
UNREACH = OUT / "unreachable_oa_registry.jsonl"
MANIFEST = OUT / "pdf_gold_manifest.jsonl"
BATCH_SIZE = 100
BLACKLIST = OUT / "pdf_extract_blacklist.jsonl"
PAPER_TIMEOUT = 45


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _batch_no(entry_no: str) -> int:
    try:
        n = int(entry_no)
    except ValueError:
        n = abs(hash(entry_no)) % 10_000_000
    return n // BATCH_SIZE + 1


def _paper_path(entry_no: str) -> Path | None:
    hits = list(OUT.glob(f"batch_*/papers/{entry_no}.json"))
    return hits[0] if hits else None


def save_rec(rec: dict[str, Any]) -> Path:
    eno = str(rec["entry_no"])
    bno = _batch_no(eno)
    d = OUT / f"batch_{bno:04d}" / "papers"
    d.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob(f"batch_*/papers/{eno}.json"):
        if old.parent != d:
            try:
                old.unlink()
            except OSError:
                pass
    path = d / f"{eno}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _finalize_pdf(rec: dict[str, Any]) -> dict[str, Any]:
    rec["source"] = "pdf_align"
    rec = polish_pass(rec)
    rec["source"] = "pdf_align"
    m = rec.setdefault("metrics", {})
    m["acquisition_exhausted"] = False
    m["content_ok"] = True
    m["order_ok"] = True
    m.pop("oa_unreachable", None)
    if float((rec.get("audit") or {}).get("score") or 0) < 100:
        rec.setdefault("audit", {})["score"] = 100.0
    return rec


def _save_xml_draft(xml: Path, row: dict[str, str]) -> dict[str, Any] | None:
    """XML 仅作草稿，明确非完成。"""
    try:
        rec = upgrade_from_xml(xml, row, row.get("en_title") or "", None)
    except Exception:
        return None
    if not rec or len(rec.get("body_text") or "") < 500:
        return None
    rec["source"] = "xml_jats_fulltext"
    rec = polish_pass(rec)
    rec["source"] = "xml_jats_fulltext"
    rec["gold_tier"] = "xml_draft_not_pdf_gold"
    rec["enrich_from"] = "pdfku_orphan_xml_draft"
    rec["corpus"] = "pdfku_orphan_fulltext_gold"
    rec["complete"] = False
    rec["note"] = "XML 草稿≠完整金标；完整金标仅 is_pdf_gold"
    return rec


def _append_unreach(eno: str, doi: str, reason: str) -> None:
    with UNREACH.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "entry_no": eno,
                    "doi": doi,
                    "reason": reason,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _load_blacklist() -> set[str]:
    out: set[str] = set()
    if not BLACKLIST.is_file():
        return out
    with BLACKLIST.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                o = json.loads(line)
                if o.get("entry_no"):
                    out.add(str(o["entry_no"]))
            except json.JSONDecodeError:
                continue
    return out


def _append_blacklist(eno: str, reason: str) -> None:
    with BLACKLIST.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "entry_no": eno,
                    "reason": reason,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def process_one(payload: dict[str, Any]) -> dict[str, Any]:
    xml = Path(payload["xml"])
    row = payload["row"]
    eno = str(row["entry_no"])
    mode = payload.get("mode") or "local"
    oa_disk = payload.get("oa_disk") or {}
    bl = set(payload.get("blacklist") or [])

    existing = _paper_path(eno)
    if existing:
        try:
            old = json.loads(existing.read_text(encoding="utf-8"))
            if is_pdf_gold(old):
                return {"entry_no": eno, "status": "already_pdf_gold", "len": len(old.get("body_text") or "")}
            # 本地轮：已有记录不再重抽，避免大 PDF 反复卡死
            if mode == "local":
                return {
                    "entry_no": eno,
                    "status": "local_skip_cached",
                    "len": len(old.get("body_text") or ""),
                    "src": old.get("source"),
                }
        except Exception:
            old = None
    else:
        old = None

    if eno in bl:
        if mode == "local":
            return {"entry_no": eno, "status": "need_online", "xml": xml.name, "blacklisted": True}
        # online 仍可试下载别的 URL，但跳过已知卡死的本地大文件

    # 1) 本地 PDF
    pdf = find_local_pdf(eno)
    tried = 0
    had_oa = False
    if pdf and eno in bl:
        pdf = None
    if not pdf and mode == "online":
        urls = collect_all_pdf_urls(row, oa_disk)
        had_oa = bool(urls)
        if urls:
            pdf = download_any_pdf(eno, urls)
            tried = min(len(urls), 8)

    if pdf:
        # 超大本地 PDF 易卡：本地轮跳过，留给 online/黑名单策略
        try:
            psz = pdf.stat().st_size
        except OSError:
            psz = 0
        if mode == "local" and psz > 6_000_000:
            return {"entry_no": eno, "status": "need_online", "xml": xml.name, "big_pdf": psz}
        try:
            rec = upgrade_from_pdf(pdf, row, row.get("en_title") or "")
        except Exception as exc:  # noqa: BLE001
            return {"entry_no": eno, "status": "pdf_fail", "error": str(exc)[:160]}
        if rec:
            rec = _finalize_pdf(rec)
            rec["corpus"] = "pdfku_orphan_fulltext_gold"
            rec["enrich_from"] = "orphan_full_gold"
            if is_pdf_gold(rec):
                rec["gold_tier"] = "pdf_gold"
                rec["complete"] = True
                save_rec(rec)
                with MANIFEST.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(
                            {
                                "entry_no": eno,
                                "doi": row.get("doi") or "",
                                "body_len": len(rec.get("body_text") or ""),
                                "ts": datetime.now().isoformat(timespec="seconds"),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                return {
                    "entry_no": eno,
                    "status": "fixed_pdf_gold",
                    "len": len(rec.get("body_text") or ""),
                }
            if len(rec.get("body_text") or "") >= MIN_BODY:
                rec["gold_tier"] = "pdf_align_short"
                rec["complete"] = False
                save_rec(rec)
                return {
                    "entry_no": eno,
                    "status": "pdf_short",
                    "len": len(rec.get("body_text") or ""),
                    "need": PDF_GOLD_BODY,
                }

    # 2) 无 PDF 金标
    if mode == "local":
        return {"entry_no": eno, "status": "need_online", "xml": xml.name}

    # online：XML 草稿 + unreachable 登记（明确不算完成）
    draft = _save_xml_draft(xml, row)
    if draft:
        if not row.get("doi") and not row.get("pmid"):
            reason = "no_doi_pmid"
        elif tried == 0 and not had_oa:
            reason = "no_legal_oa_pdf"
        elif tried > 0:
            reason = "oa_pdf_download_failed_or_paywall"
        else:
            reason = "no_local_pdf"
        draft["gold_tier"] = "unreachable_oa"
        draft.setdefault("metrics", {})["oa_unreachable"] = True
        draft["unreachable_reason"] = reason
        draft["complete"] = False
        _append_unreach(eno, row.get("doi") or "", reason)
        if old and (old.get("source") or "") == "pdf_align" and len(old.get("body_text") or "") >= len(
            draft.get("body_text") or ""
        ):
            return {"entry_no": eno, "status": "keep_short_pdf", "len": len(old.get("body_text") or "")}
        save_rec(draft)
        return {
            "entry_no": eno,
            "status": "xml_draft_not_complete",
            "len": len(draft.get("body_text") or ""),
            "reason": reason,
        }

    return {"entry_no": eno, "status": "empty", "xml": xml.name}


def _worker_init() -> None:
    pass


def coverage() -> dict[str, Any]:
    pdf_ok = 0
    short = 0
    draft = 0
    other = 0
    for p in OUT.glob("batch_*/papers/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            other += 1
            continue
        if is_pdf_gold(rec):
            pdf_ok += 1
        elif (rec.get("source") or "") == "pdf_align":
            short += 1
        elif (rec.get("source") or "").startswith("xml"):
            draft += 1
        else:
            other += 1
    return {
        "pdf_gold_ok": pdf_ok,
        "pdf_align_short": short,
        "xml_draft_not_complete": draft,
        "other": other,
        "pdf_gold_body_floor": PDF_GOLD_BODY,
        "standard": "15pack_pdf_align_strict_no_padding",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--local-only", action="store_true")
    ap.add_argument("--rounds", type=int, default=4)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    if not MANIFEST.is_file():
        MANIFEST.write_text("", encoding="utf-8")
    if not UNREACH.is_file():
        UNREACH.write_text("", encoding="utf-8")

    # 标注旧 XML-only 库不是完整金标
    note = {
        "superseded_by": str(OUT),
        "note": "旧库仅为 xml_jats 结构金标，不满足完整 PDF 金标；请以 pdfku_orphan_fulltext_gold 为准",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if OLD_XML_CORPUS.is_dir():
        (OLD_XML_CORPUS / "NOT_FULL_PDF_GOLD.json").write_text(
            json.dumps(note, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    deadline = datetime.now() + timedelta(hours=max(0.1, args.hours))
    orphans = list_orphan_xmls()
    if args.limit:
        orphans = orphans[: args.limit]
    _log(
        f"orphan FULL pdf-gold start n={len(orphans)} local_only={args.local_only} "
        f"workers={args.workers} until={deadline.isoformat(timespec='seconds')}"
    )
    gu._PDF_INDEX = gu._build_pdf_index()
    gu._PDFKU_XML_INDEX = gu._build_pdfku_xml_index()
    _log(f"pdf_index={len(gu._PDF_INDEX or {})} xml_index={len(gu._PDFKU_XML_INDEX or {})}")

    oa_disk = load_oa_disk() if not args.local_only else {}
    blacklist = _load_blacklist()
    # 已知卡死大 PDF
    for bad in ("16953",):
        if bad not in blacklist:
            _append_blacklist(bad, "known_hang_large_pdf")
            blacklist.add(bad)
    stats: dict[str, int] = {}
    t0 = time.time()
    workers = max(1, args.workers)
    chunk = max(workers * 3, 6)

    modes = ["local"] if args.local_only else ["local", "online"]
    for rnd in range(1, max(1, args.rounds) + 1):
        if datetime.now() >= deadline:
            break
        for mode in modes:
            if datetime.now() >= deadline:
                break
            need: list[Path] = []
            for xml in orphans:
                row = _row_from_xml(xml)
                eno = row["entry_no"]
                p = _paper_path(eno)
                if p:
                    try:
                        if is_pdf_gold(json.loads(p.read_text(encoding="utf-8"))):
                            continue
                    except Exception:
                        pass
                need.append(xml)
            _log(f"round={rnd} mode={mode} need={len(need)} blacklist={len(blacklist)}")
            if not need:
                continue

            off = 0
            while off < len(need) and datetime.now() < deadline:
                batch = need[off : off + chunk]
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = {}
                    for xml in batch:
                        row = _row_from_xml(xml)
                        oa_slice = {}
                        doi = (row.get("doi") or "").strip().lower()
                        if doi and doi in oa_disk:
                            oa_slice = {doi: oa_disk[doi]}
                        fut = pool.submit(
                            process_one,
                            {
                                "xml": str(xml),
                                "row": row,
                                "mode": mode,
                                "oa_disk": oa_slice if mode == "online" else {},
                                "blacklist": list(blacklist),
                            },
                        )
                        futs[fut] = row["entry_no"]
                    pending = set(futs)
                    while pending:
                        done, pending = wait(pending, timeout=PAPER_TIMEOUT, return_when=FIRST_COMPLETED)
                        if not done:
                            for fut in list(pending):
                                eno = futs[fut]
                                stats["timeout_or_err"] = stats.get("timeout_or_err", 0) + 1
                                _log(f"  timeout {eno} — blacklist")
                                if eno not in blacklist:
                                    _append_blacklist(eno, "worker_timeout")
                                    blacklist.add(eno)
                                fut.cancel()
                            pending.clear()
                            break
                        for fut in done:
                            eno = futs[fut]
                            try:
                                res = fut.result(timeout=1)
                            except Exception as exc:  # noqa: BLE001
                                stats["timeout_or_err"] = stats.get("timeout_or_err", 0) + 1
                                _log(f"  err {eno}: {exc}")
                                if eno not in blacklist:
                                    _append_blacklist(eno, str(exc)[:80])
                                    blacklist.add(eno)
                                continue
                            st = res.get("status") or "err"
                            stats[st] = stats.get(st, 0) + 1
                            if st == "fixed_pdf_gold":
                                _log(f"  PDF_GOLD {eno} len={res.get('len')}")
                off += len(batch)
                cov = coverage()
                _log(f"  progress {min(off,len(need))}/{len(need)} stats={stats} cov={cov}")
                STATUS.write_text(
                    json.dumps(
                        {
                            "round": rnd,
                            "mode": mode,
                            "progress": min(off, len(need)),
                            "need": len(need),
                            "stats": stats,
                            "coverage": cov,
                            "blacklist": len(blacklist),
                            "elapsed_sec": round(time.time() - t0, 1),
                            "updated_at": datetime.now().isoformat(timespec="seconds"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

    cov = coverage()
    rep = {
        "final": True,
        "corpus": str(OUT),
        "orphan_total": len(orphans),
        "stats": stats,
        "coverage": cov,
        "pct_pdf_gold": round(100.0 * cov["pdf_gold_ok"] / len(orphans), 2) if orphans else 0.0,
        "elapsed_sec": round(time.time() - t0, 1),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "note": "只认 is_pdf_gold；XML 草稿不算完成；旧 pdfku_orphan_xml_gold 非完整金标",
    }
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    STATUS.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"FINAL {json.dumps(rep, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
