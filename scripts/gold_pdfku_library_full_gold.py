# -*- coding: utf-8 -*-
"""
pdf库 全量完整金标战役。

覆盖：
  pdf库/pdf/*.pdf  — PDF 对齐金标
  pdf库/xml/*.xml  — 与 PDF 金标同门槛的全文金标（body/strict/metrics/score）
  pdf库/html/*.txt — 同上

完成 = is_pdf_gold()（已扩展）：
  source ∈ {pdf_align, xml_jats_fulltext, html_fulltext_gold}
  + body>=35487 + strict 空 + content/order/score + 无垫文

输出：解析文件/pdfku_library_fulltext_gold/
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_gap_fill import polish_pass  # noqa: E402
from scripts.gold_strict_full_campaign import strict_issues  # noqa: E402
from scripts.gold_hunt_fulltext import (  # noqa: E402
    PDF_GOLD_BODY,
    collect_all_pdf_urls,
    download_any_pdf,
    finalize_to_gold_bar,
    is_pdf_gold,
    load_oa_disk,
)
from scripts.gold_upgrade_5680 import (  # noqa: E402
    MIN_BODY,
    PDFKU_HTML,
    PDFKU_PDF,
    PDFKU_XML,
    _build_pdf_index,
    _build_pdfku_xml_index,
    _doi_from_pdfku_stem,
    find_local_pdf,
    set_pdf_extract_pool_size,
    upgrade_from_html,
    upgrade_from_pdf_hard,
    upgrade_from_xml,
)
import scripts.gold_upgrade_5680 as gu  # noqa: E402

OUT = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
STATUS = OUT / "status.json"
REPORT = OUT / "report.json"
LOG = OUT / "campaign.log"
MANIFEST = OUT / "pdf_gold_manifest.jsonl"
UNREACH = OUT / "unreachable_oa_registry.jsonl"
BLACKLIST = OUT / "pdf_extract_blacklist.jsonl"
BATCH_SIZE = 100
PAPER_TIMEOUT = 35  # 单批内「无人完成」上限
FUTURE_SOFT_SEC = 45  # 单篇软超时，到点拉黑继续（防 PDF/XML 卡死整批）
HEARTBEAT_SEC = 15
COV_EVERY = 8  # 降频 coverage，加快推进


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


def _build_paper_index() -> dict[str, Path]:
    idx: dict[str, Path] = {}
    for p in OUT.glob("batch_*/papers/*.json"):
        idx[p.stem] = p
    return idx


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


def _row(eno: str, doi: str = "", title: str = "") -> dict[str, str]:
    return {
        "entry_no": str(eno),
        "q12_no": "",
        "en_title": title or "",
        "doi": doi or "",
        "pmid": "",
        "source_url": "",
        "content_type": "pdf",
    }


def _finalize_pdf(rec: dict[str, Any], *, fallback_title: str = "") -> dict[str, Any]:
    return finalize_to_gold_bar(rec, fallback_title=fallback_title, prefer_source="pdf_align")


def _extract_xml_html_candidates(
    payload: dict[str, Any],
    row: dict[str, str],
    *,
    tip: str,
) -> list[tuple[int, str, dict[str, Any]]]:
    """按格式各自适配抽取，返回 [(body_len, prefer_source, rec), ...] 按体量降序。"""
    out: list[tuple[int, str, dict[str, Any]]] = []
    xml = Path(payload["xml"]) if payload.get("xml") else None
    html = Path(payload["html"]) if payload.get("html") else None
    if xml and xml.is_file():
        try:
            rec = upgrade_from_xml(xml, row, tip, None)
            if rec and len(rec.get("body_text") or "") >= 200:
                out.append((len(rec["body_text"]), "xml_jats_fulltext", rec))
        except Exception:
            pass
    if html and html.is_file():
        try:
            rec = upgrade_from_html(html, row, tip)
            if rec and len(rec.get("body_text") or "") >= 200:
                out.append((len(rec["body_text"]), "html_fulltext_gold", rec))
        except Exception:
            pass
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _try_xml_html_gold(
    payload: dict[str, Any],
    row: dict[str, str],
    *,
    tip: str,
    doi: str,
    beat_len: int = 0,
) -> dict[str, Any] | None:
    """XML/HTML 各自适配冲击金标；仅当优于 beat_len 或达金标时落盘。"""
    eno = str(payload["entry_no"])
    cands = _extract_xml_html_candidates(payload, row, tip=tip)
    if not cands:
        return None
    for _blen, prefer, cand in cands:
        neu = finalize_to_gold_bar(
            dict(cand),
            fallback_title=tip or f"DOI {doi}" or f"Entry {eno}",
            prefer_source=prefer,
        )
        neu["doi"] = doi or neu.get("doi") or ""
        neu["corpus"] = "pdfku_library_fulltext_gold"
        neu["enrich_from"] = f"pdfku_{prefer}"
        nlen = len(neu.get("body_text") or "")
        if is_pdf_gold(neu):
            save_rec(neu)
            _append_jsonl(
                MANIFEST,
                {
                    "entry_no": eno,
                    "doi": doi,
                    "body_len": nlen,
                    "source": neu.get("source"),
                    "ts": datetime.now().isoformat(timespec="seconds"),
                },
            )
            return {
                "entry_no": eno,
                "status": "fixed_pdf_gold",
                "len": nlen,
                "via": neu.get("source"),
            }
        # 未达金标：落盘短稿/微稿，避免反复空转；auto_done 才能收尾
        if nlen >= 200 and nlen > beat_len:
            neu["gold_tier"] = "fulltext_short"
            neu["complete"] = False
            save_rec(neu)
            return {
                "entry_no": eno,
                "status": "xml_html_short" if nlen >= MIN_BODY else "xml_html_tiny",
                "len": nlen,
                "need": PDF_GOLD_BODY,
                "issues": strict_issues(neu)[:3],
                "via": prefer,
            }
    # 有 xml/html 路径但抽不出正文：写占位，标记需人工
    xml = Path(payload["xml"]) if payload.get("xml") else None
    html = Path(payload["html"]) if payload.get("html") else None
    if (xml and xml.is_file()) or (html and html.is_file()):
        stub = {
            "entry_no": eno,
            "doi": doi,
            "title": tip or f"Entry {eno}",
            "body_text": "",
            "source": "xml_jats_fulltext" if xml and xml.is_file() else "html_fulltext_gold",
            "gold_tier": "extract_empty",
            "complete": False,
            "corpus": "pdfku_library_fulltext_gold",
            "metrics": {"content_ok": False, "order_ok": False, "acquisition_exhausted": False},
            "sections": {"title": tip or f"Entry {eno}"},
            "audit": {"score": 0},
        }
        save_rec(stub)
        return {"entry_no": eno, "status": "xml_html_empty", "len": 0}
    return None


def _load_set(path: Path, key: str = "entry_no") -> set[str]:
    out: set[str] = set()
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                o = json.loads(line)
                if o.get(key):
                    out.add(str(o[key]))
            except json.JSONDecodeError:
                continue
    return out


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def list_targets(*, pdf_only: bool = False) -> list[dict[str, Any]]:
    """每个目标：entry_no + doi + 可选 pdf/xml/html 路径。PDF 优先单独成条。"""
    pdf_by_eno: dict[str, Path] = {}
    pdf_by_doi: dict[str, Path] = {}
    for p in PDFKU_PDF.glob("*.pdf") if PDFKU_PDF.is_dir() else []:
        if p.stat().st_size < 5000:
            continue
        m = re.match(r"^(\d+)", p.stem)
        if not m:
            continue
        eno = m.group(1)
        prev = pdf_by_eno.get(eno)
        if prev is None or p.stat().st_size > prev.stat().st_size:
            pdf_by_eno[eno] = p
        d = _doi_from_pdfku_stem(p.stem)
        if d and (d not in pdf_by_doi or p.stat().st_size > pdf_by_doi[d].stat().st_size):
            pdf_by_doi[d] = p

    xml_by_eno: dict[str, Path] = {}
    xml_by_doi: dict[str, Path] = {}
    if not pdf_only and PDFKU_XML.is_dir():
        for p in PDFKU_XML.glob("*.xml"):
            if p.stat().st_size < 2000:
                continue
            m = re.match(r"^(\d+)", p.stem)
            if not m:
                continue
            eno = m.group(1)
            prev = xml_by_eno.get(eno)
            if prev is None or p.stat().st_size > prev.stat().st_size:
                xml_by_eno[eno] = p
            d = _doi_from_pdfku_stem(p.stem)
            if d and (d not in xml_by_doi or p.stat().st_size > xml_by_doi[d].stat().st_size):
                xml_by_doi[d] = p

    html_by_eno: dict[str, Path] = {}
    if not pdf_only and PDFKU_HTML.is_dir():
        for p in PDFKU_HTML.glob("*"):
            if not p.is_file() or p.stat().st_size < 500:
                continue
            m = re.match(r"^(\d+)", p.stem)
            if m:
                html_by_eno[m.group(1)] = p

    # 键空间：PDF + XML + HTML（无交集时各自独立；效果达金标门槛即算完成）
    keys: set[str] = set(pdf_by_eno)
    if not pdf_only:
        keys |= set(xml_by_eno) | set(html_by_eno)
    targets: list[dict[str, Any]] = []
    for eno in sorted(keys, key=lambda x: int(x) if x.isdigit() else 0):
        pdf = pdf_by_eno.get(eno)
        xml = xml_by_eno.get(eno)
        html = html_by_eno.get(eno)
        doi = ""
        if pdf:
            doi = _doi_from_pdfku_stem(pdf.stem)
        if not doi and xml:
            doi = _doi_from_pdfku_stem(xml.stem)
        if not doi and html:
            doi = _doi_from_pdfku_stem(html.stem)
        # DOI 交叉补 PDF：禁止把「别的 entry_no」的 PDF 安到本条上（防串号卡死）
        if not pdf and doi and doi in pdf_by_doi:
            cand = pdf_by_doi[doi]
            cm = re.match(r"^(\d+)", cand.stem)
            if cm and cm.group(1) == eno:
                pdf = cand
        if not xml and doi and doi in xml_by_doi:
            candx = xml_by_doi[doi]
            cm = re.match(r"^(\d+)", candx.stem)
            if cm and cm.group(1) == eno:
                xml = candx
        targets.append(
            {
                "entry_no": eno,
                "doi": doi,
                "pdf": str(pdf) if pdf else "",
                "xml": str(xml) if xml else "",
                "html": str(html) if html else "",
                "priority": 0 if pdf else (1 if xml else 2),
            }
        )
    # PDF 优先
    targets.sort(key=lambda t: (t["priority"], int(t["entry_no"]) if str(t["entry_no"]).isdigit() else 0))
    return targets


def coverage() -> dict[str, Any]:
    pdf_ok = short = draft = other = 0
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
        elif (rec.get("source") or "").startswith("xml") or (rec.get("source") or "").startswith("html"):
            draft += 1
        else:
            other += 1
    return {
        "pdf_gold_ok": pdf_ok,
        "pdf_align_short": short,
        "draft_not_complete": draft,
        "other": other,
        "pdf_gold_body_floor": PDF_GOLD_BODY,
        "standard": "15pack_fulltext_gold_equiv_pdf_xml_html",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def process_one(payload: dict[str, Any]) -> dict[str, Any]:
    eno = str(payload["entry_no"])
    doi = (payload.get("doi") or "").strip()
    mode = payload.get("mode") or "local"
    row = _row(eno, doi)
    bl = set(payload.get("blacklist") or [])

    existing = _paper_path(eno)
    old = None
    if existing:
        try:
            old = json.loads(existing.read_text(encoding="utf-8"))
            if is_pdf_gold(old):
                return {"entry_no": eno, "status": "already_pdf_gold", "len": len(old.get("body_text") or "")}
            # 已有够长 PDF/XML/HTML：只补标题/抛光，避免重抽
            old_src = (old.get("source") or "").strip()
            if old_src in ("pdf_align", "xml_jats_fulltext", "html_fulltext_gold", "xml_jats_deep") and len(
                old.get("body_text") or ""
            ) >= PDF_GOLD_BODY:
                tip = f"DOI {doi}" if doi else f"Entry {eno} fulltext"
                prefer = (
                    "pdf_align"
                    if old_src == "pdf_align"
                    else ("html_fulltext_gold" if old_src.startswith("html") else "xml_jats_fulltext")
                )
                neu = finalize_to_gold_bar(dict(old), fallback_title=tip, prefer_source=prefer)
                if is_pdf_gold(neu):
                    neu["corpus"] = "pdfku_library_fulltext_gold"
                    save_rec(neu)
                    _append_jsonl(
                        MANIFEST,
                        {
                            "entry_no": eno,
                            "doi": doi,
                            "body_len": len(neu.get("body_text") or ""),
                            "source": neu.get("source"),
                            "ts": datetime.now().isoformat(timespec="seconds"),
                        },
                    )
                    return {
                        "entry_no": eno,
                        "status": "fixed_pdf_gold",
                        "len": len(neu.get("body_text") or ""),
                        "via": neu.get("source"),
                    }
        except Exception:
            old = None

    # 解析 PDF 路径
    # local 且目标自带 xml/html：禁止全局 find_local_pdf（5658 库易串号抽大 PDF 卡死整批）
    pdf: Path | None = None
    if payload.get("pdf"):
        pdf = Path(payload["pdf"])
        if not pdf.is_file():
            pdf = None
    allow_global_pdf = not (
        mode == "local" and ((payload.get("xml") or "").strip() or (payload.get("html") or "").strip())
    )
    if not pdf and allow_global_pdf:
        pdf = find_local_pdf(eno, doi=doi)
    # 黑名单：跳过 PDF，仍可用 XML/HTML
    if eno in bl:
        pdf = None

    tried = 0
    had_oa = False
    if (not pdf or eno in bl) and mode == "online":
        urls = collect_all_pdf_urls(row, payload.get("oa_disk") or {})
        had_oa = bool(urls)
        if urls:
            got = download_any_pdf(eno, urls)
            if got:
                pdf = got
            tried = min(len(urls), 8)

    if pdf and pdf.is_file():
        try:
            psz = pdf.stat().st_size
        except OSError:
            psz = 0
        if psz > 5_000_000:
            # 超大 PDF：先 XML/HTML；仍不够则硬超时抽 PDF（不再直接 need_online 放跑）
            tip = f"DOI {doi}" if doi else f"Entry {eno} fulltext"
            got = _try_xml_html_gold(payload, row, tip=tip, doi=doi, beat_len=0)
            if got and got.get("status") == "fixed_pdf_gold":
                return got
            beat = int(got.get("len") or 0) if got else 0
            try:
                rec_big = upgrade_from_pdf_hard(pdf, row, "")
            except Exception as exc:  # noqa: BLE001
                if got:
                    return got
                return {"entry_no": eno, "status": "pdf_fail", "error": str(exc)[:160], "big_pdf": psz}
            if rec_big is None:
                if got:
                    return got
                return {
                    "entry_no": eno,
                    "status": "pdf_extract_timeout",
                    "pdf": str(pdf),
                    "big_pdf": psz,
                }
            tip_b = tip
            rec_big = _finalize_pdf(rec_big, fallback_title=tip_b)
            rec_big["corpus"] = "pdfku_library_fulltext_gold"
            rec_big["enrich_from"] = "pdfku_library"
            rec_big["doi"] = doi or rec_big.get("doi") or ""
            if is_pdf_gold(rec_big):
                rec_big["gold_tier"] = "pdf_gold"
                rec_big["complete"] = True
                save_rec(rec_big)
                return {
                    "entry_no": eno,
                    "status": "fixed_pdf_gold",
                    "len": len(rec_big.get("body_text") or ""),
                    "via": "pdf_align_big",
                }
            pdf_len_b = len(rec_big.get("body_text") or "")
            if got and beat >= pdf_len_b:
                return got
            if pdf_len_b >= MIN_BODY:
                rec_big["gold_tier"] = "pdf_align_short"
                rec_big["complete"] = False
                save_rec(rec_big)
                return {
                    "entry_no": eno,
                    "status": "pdf_short",
                    "len": pdf_len_b,
                    "need": PDF_GOLD_BODY,
                    "big_pdf": psz,
                }
            if got:
                return got
            return {"entry_no": eno, "status": "need_online", "big_pdf": psz}
        try:
            # 子进程硬超时：线程超时杀不掉持 GIL 的 PDF 解析（如 18709）
            rec = upgrade_from_pdf_hard(pdf, row, "")
        except Exception as exc:  # noqa: BLE001
            return {"entry_no": eno, "status": "pdf_fail", "error": str(exc)[:160]}
        if rec is None:
            return {"entry_no": eno, "status": "pdf_extract_timeout", "pdf": str(pdf)}
        if rec:
            tip = ""
            if doi:
                tip = f"DOI {doi}"
            elif payload.get("pdf"):
                tip = Path(payload["pdf"]).stem.replace("_", " ")[:200]
            rec = _finalize_pdf(rec, fallback_title=tip)
            rec["corpus"] = "pdfku_library_fulltext_gold"
            rec["enrich_from"] = "pdfku_library"
            rec["doi"] = doi or rec.get("doi") or ""
            # 再验一次；若仅 bad_title 再强补
            if not is_pdf_gold(rec) and len(rec.get("body_text") or "") >= PDF_GOLD_BODY:
                rec = _finalize_pdf(rec, fallback_title=tip or f"Entry {eno} fulltext")
            if is_pdf_gold(rec):
                rec["gold_tier"] = "pdf_gold"
                rec["complete"] = True
                save_rec(rec)
                _append_jsonl(
                    MANIFEST,
                    {
                        "entry_no": eno,
                        "doi": doi,
                        "body_len": len(rec.get("body_text") or ""),
                        "pdf": str(pdf),
                        "ts": datetime.now().isoformat(timespec="seconds"),
                    },
                )
                return {"entry_no": eno, "status": "fixed_pdf_gold", "len": len(rec.get("body_text") or "")}
            # PDF 短：不早退——对照是表逻辑，继续用本地 XML/HTML 冲击更长全文
            pdf_len = len(rec.get("body_text") or "")
            tip2 = tip or (f"DOI {doi}" if doi else f"Entry {eno} fulltext")
            got = _try_xml_html_gold(payload, row, tip=tip2, doi=doi, beat_len=pdf_len)
            if got and got.get("status") == "fixed_pdf_gold":
                return got
            if got and got.get("status") == "xml_html_short" and int(got.get("len") or 0) > pdf_len:
                return got
            if pdf_len >= MIN_BODY:
                rec["gold_tier"] = "pdf_align_short"
                rec["complete"] = False
                save_rec(rec)
                return {
                    "entry_no": eno,
                    "status": "pdf_short",
                    "len": pdf_len,
                    "need": PDF_GOLD_BODY,
                    "issues": strict_issues(rec)[:3],
                }

    if mode == "local":
        # 无 PDF 或 PDF 未产出：本地 XML/HTML 按各自适配逻辑冲击金标
        tip = f"DOI {doi}" if doi else f"Entry {eno} fulltext"
        got = _try_xml_html_gold(payload, row, tip=tip, doi=doi, beat_len=0)
        if got:
            return got
        return {"entry_no": eno, "status": "need_online"}

    # online：先尽量 XML/HTML 达金标
    tip = f"DOI {doi}" if doi else f"Entry {eno} fulltext"
    got = _try_xml_html_gold(payload, row, tip=tip, doi=doi, beat_len=0)
    if got:
        # fixed_pdf_gold / xml_html_short 均已落盘；短稿不算完成但勿再标 unreachable 空转
        if got.get("status") == "fixed_pdf_gold":
            return got
        if got.get("status") == "xml_html_short":
            return got

    xml = Path(payload["xml"]) if payload.get("xml") else None
    if xml and xml.is_file():
        # 已尝试过仍不够：仅当正文过短时登记 unreachable
        try:
            draft = upgrade_from_xml(xml, row, tip, None)
        except Exception:
            draft = None
        if draft and len(draft.get("body_text") or "") >= 500:
            draft = finalize_to_gold_bar(draft, fallback_title=tip, prefer_source="xml_jats_fulltext")
            if is_pdf_gold(draft):
                draft["corpus"] = "pdfku_library_fulltext_gold"
                save_rec(draft)
                return {
                    "entry_no": eno,
                    "status": "fixed_pdf_gold",
                    "len": len(draft.get("body_text") or ""),
                    "via": "xml_jats_fulltext",
                }
            body_len = len(draft.get("body_text") or "")
            if body_len >= PDF_GOLD_BODY:
                draft["gold_tier"] = "fulltext_short"
                draft["complete"] = False
                draft["corpus"] = "pdfku_library_fulltext_gold"
                if not (old and is_pdf_gold(old)):
                    save_rec(draft)
                return {
                    "entry_no": eno,
                    "status": "xml_html_short",
                    "len": body_len,
                    "need": PDF_GOLD_BODY,
                }
            reason = (
                "no_doi_pmid"
                if not doi
                else (
                    "no_legal_oa_pdf"
                    if tried == 0 and not had_oa
                    else "oa_pdf_download_failed_or_paywall"
                    if tried
                    else "no_local_pdf"
                )
            )
            draft["gold_tier"] = "unreachable_oa" if tried or not had_oa else "xml_draft_not_pdf_gold"
            draft["complete"] = False
            draft["corpus"] = "pdfku_library_fulltext_gold"
            draft["unreachable_reason"] = reason
            draft.setdefault("metrics", {})["oa_unreachable"] = True
            if not (old and (old.get("source") or "") == "pdf_align"):
                save_rec(draft)
            _append_jsonl(
                UNREACH,
                {
                    "entry_no": eno,
                    "doi": doi,
                    "reason": reason,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                },
            )
            return {"entry_no": eno, "status": "xml_draft_not_complete", "reason": reason}

    return {"entry_no": eno, "status": "empty"}


def _start_daemon_heartbeat(state: dict[str, Any]) -> None:
    """独立线程写 status，主线程卡在网络/抽取时 keep_alive 仍能看见活着。"""

    def _loop() -> None:
        while not state.get("stop"):
            try:
                payload = {
                    "round": state.get("round"),
                    "mode": state.get("mode"),
                    "progress": state.get("progress"),
                    "need": state.get("need"),
                    "stats": state.get("stats") or {},
                    "coverage": state.get("coverage")
                    or {
                        "note": "daemon_heartbeat",
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    },
                    "elapsed_sec": state.get("elapsed_sec"),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                    "heartbeat": True,
                    "daemon": True,
                    "pending": state.get("pending"),
                }
                STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            time.sleep(15)

    th = threading.Thread(target=_loop, name="pdfku-heartbeat", daemon=True)
    th.start()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1, help="本机默认1，防OOM；服务器可再调高")
    ap.add_argument("--hours", type=float, default=48.0)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--pdf-only", action="store_true", help="只处理 pdf库/pdf（最快出完整金标）")
    ap.add_argument("--local-only", action="store_true")
    ap.add_argument("--start-online", action="store_true", help="跳过 local，直接 online（回收后加速）")
    args = ap.parse_args()
    # 本机金标补缺：默认并硬顶 workers<=2（可用环境变量 GOLD_LOCAL_WORKERS_CAP 调整）
    import os as _os
    _cap = int(_os.environ.get("GOLD_LOCAL_WORKERS_CAP", "1"))
    args.workers = max(1, min(int(args.workers), max(1, _cap)))

    # 全局套接字超时，避免 urllib 无 timeout 路径把工人钉死
    socket.setdefaulttimeout(20)

    OUT.mkdir(parents=True, exist_ok=True)
    for p in (MANIFEST, UNREACH, BLACKLIST):
        if not p.is_file():
            p.write_text("", encoding="utf-8")

    deadline = datetime.now() + timedelta(hours=max(0.1, args.hours))
    targets = list_targets(pdf_only=args.pdf_only)
    if args.limit:
        targets = targets[: args.limit]
    n_pdf = sum(1 for t in targets if t.get("pdf"))
    _log(
        f"pdfku LIBRARY full-gold start targets={len(targets)} with_pdf={n_pdf} "
        f"pdf_only={args.pdf_only} workers={args.workers} until={deadline.isoformat(timespec='seconds')}"
    )
    gu._PDF_INDEX = _build_pdf_index()
    gu._PDFKU_XML_INDEX = _build_pdfku_xml_index()
    _log(f"pdf_index={len(gu._PDF_INDEX or {})} xml_index={len(gu._PDFKU_XML_INDEX or {})}")

    # 多进程并行抽 PDF（标准不变）
    extract_n = max(1, min(2, max(1, int(args.workers))))  # 本机默认<=1；禁止再强制开满进程池
    set_pdf_extract_pool_size(extract_n)
    _log(f"pdf_extract_pool={extract_n}")

    blacklist = _load_set(BLACKLIST)
    oa_disk = {} if args.local_only else load_oa_disk()
    stats: dict[str, int] = {}
    t0 = time.time()
    # local：与抽取池同宽；online：降并发避免把主线程/网络拖死
    local_workers = extract_n
    online_workers = max(1, min(2, max(1, int(args.workers))))  # 禁止 floor=4 导致本机OOM
    if args.local_only:
        modes = ["local"]
    elif args.start_online:
        modes = ["online", "local"]
    else:
        modes = ["local", "online"]
    if args.pdf_only and not args.start_online:
        modes = ["local", "online"]

    last_cov: dict[str, Any] | None = None
    batch_i = 0
    hb_state: dict[str, Any] = {
        "stop": False,
        "round": 0,
        "mode": "",
        "progress": 0,
        "need": 0,
        "stats": stats,
        "coverage": None,
        "elapsed_sec": 0,
        "pending": 0,
    }
    _start_daemon_heartbeat(hb_state)

    for rnd in range(1, max(1, args.rounds) + 1):
        if datetime.now() >= deadline:
            break
        for mode in modes:
            if datetime.now() >= deadline:
                break
            workers = local_workers if mode == "local" else online_workers
            chunk = max(workers * 2, 8) if mode == "online" else max(workers * 3, 12)
            paper_idx = _build_paper_index()
            need = []
            for t in targets:
                eno = t["entry_no"]
                p = paper_idx.get(eno)
                if p:
                    try:
                        old = json.loads(p.read_text(encoding="utf-8"))
                        if is_pdf_gold(old):
                            continue
                        body_len = len(old.get("body_text") or "")
                        tier = old.get("gold_tier") or ""
                        # local：已抽过短稿（正文够用）不再反复重扫，避免每次重启卡在同一批
                        if mode == "local" and body_len >= MIN_BODY and body_len < PDF_GOLD_BODY:
                            continue
                        # local：够长但未金标 → 只做 finalize，不重抽 PDF
                        if mode == "local" and body_len >= PDF_GOLD_BODY:
                            # 仍进队列，由 process_one 走抛光捷径
                            pass
                        # online：仅跳过已确认不可达且正文未达体量者
                        if mode == "online":
                            if (
                                body_len < PDF_GOLD_BODY
                                and (
                                    tier in ("unreachable_oa",)
                                    or (old.get("metrics") or {}).get("oa_unreachable")
                                )
                            ):
                                continue
                            if eno in blacklist and not (
                                (t.get("xml") or t.get("html") or "").strip() and body_len >= PDF_GOLD_BODY
                            ):
                                continue
                    except Exception:
                        pass
                # local：有 PDF 或 XML/HTML 均可冲击金标（同门槛）
                if mode == "local" and not (
                    (t.get("pdf") or "").strip()
                    or (t.get("xml") or "").strip()
                    or (t.get("html") or "").strip()
                ):
                    continue
                # local 优先啃「尚无 JSON / 正文极短」的 XML，而不是反复扫已短稿
                if mode == "local" and p:
                    try:
                        old2 = json.loads(p.read_text(encoding="utf-8"))
                        if len(old2.get("body_text") or "") >= MIN_BODY:
                            # 仅保留够长待抛光者
                            if len(old2.get("body_text") or "") < PDF_GOLD_BODY:
                                continue
                    except Exception:
                        pass
                need.append(t)
            # local：XML/HTML 优先，PDF 靠后，避免一上来抽卡死 PDF
            if mode == "local":
                need.sort(
                    key=lambda t: (
                        0 if (t.get("xml") or t.get("html")) and not (t.get("pdf") or "").strip() else 1,
                        0 if (t.get("xml") or "").strip() else 1,
                        int(t["entry_no"]) if str(t["entry_no"]).isdigit() else 0,
                    )
                )
            _log(
                f"round={rnd} mode={mode} need={len(need)} bl={len(blacklist)} "
                f"indexed={len(paper_idx)} workers={workers}"
            )
            if not need:
                continue
            off = 0
            hb_state["round"] = rnd
            hb_state["mode"] = mode
            hb_state["need"] = len(need)
            while off < len(need) and datetime.now() < deadline:
                batch = need[off : off + chunk]
                batch_i += 1
                hb_state["progress"] = min(off, len(need))
                hb_state["stats"] = stats
                hb_state["elapsed_sec"] = round(time.time() - t0, 1)
                hb_state["pending"] = len(batch)
                # 批次开始先写心跳，避免 online 长等待被 keep_alive 误判 STALL
                STATUS.write_text(
                    json.dumps(
                        {
                            "round": rnd,
                            "mode": mode,
                            "progress": min(off, len(need)),
                            "need": len(need),
                            "stats": stats,
                            "coverage": last_cov
                            or {
                                "pdf_gold_ok": None,
                                "note": "heartbeat",
                                "updated_at": datetime.now().isoformat(timespec="seconds"),
                            },
                            "elapsed_sec": round(time.time() - t0, 1),
                            "updated_at": datetime.now().isoformat(timespec="seconds"),
                            "heartbeat": True,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                pool_cls = ProcessPoolExecutor if mode == "local" else ThreadPoolExecutor
                pool = pool_cls(max_workers=workers)
                try:
                    futs = {}
                    started: dict[Any, float] = {}
                    for t in batch:
                        doi = (t.get("doi") or "").strip().lower()
                        oa_slice = {doi: oa_disk[doi]} if doi and doi in oa_disk else {}
                        fut = pool.submit(
                            process_one,
                            {
                                **t,
                                "mode": mode,
                                "oa_disk": oa_slice if mode == "online" else {},
                                "blacklist": list(blacklist),
                            },
                        )
                        futs[fut] = t["entry_no"]
                        started[fut] = time.time()
                    pending = set(futs)
                    last_hb = time.time()
                    while pending:
                        hb_state["pending"] = len(pending)
                        hb_state["elapsed_sec"] = round(time.time() - t0, 1)
                        # 单篇软超时：进程池可真正丢掉卡死工人
                        now = time.time()
                        for fut in list(pending):
                            if now - started.get(fut, now) < FUTURE_SOFT_SEC:
                                continue
                            eno = futs[fut]
                            stats["timeout_or_err"] = stats.get("timeout_or_err", 0) + 1
                            _log(f"  soft-timeout {eno} — blacklist")
                            if eno not in blacklist:
                                _append_jsonl(
                                    BLACKLIST,
                                    {
                                        "entry_no": eno,
                                        "reason": "future_soft_timeout",
                                        "ts": datetime.now().isoformat(timespec="seconds"),
                                    },
                                )
                                blacklist.add(eno)
                            fut.cancel()
                            pending.discard(fut)
                        if not pending:
                            break
                        if now - last_hb >= HEARTBEAT_SEC:
                            STATUS.write_text(
                                json.dumps(
                                    {
                                        "round": rnd,
                                        "mode": mode,
                                        "progress": min(off, len(need)),
                                        "need": len(need),
                                        "stats": stats,
                                        "coverage": last_cov
                                        or {
                                            "pdf_gold_ok": None,
                                            "note": "in_batch_heartbeat",
                                            "updated_at": datetime.now().isoformat(timespec="seconds"),
                                        },
                                        "elapsed_sec": round(time.time() - t0, 1),
                                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                                        "heartbeat": True,
                                        "pending": len(pending),
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                            last_hb = now
                        done, pending = wait(
                            pending,
                            timeout=min(PAPER_TIMEOUT, HEARTBEAT_SEC),
                            return_when=FIRST_COMPLETED,
                        )
                        if not done:
                            # 整段无人完成 → 拉黑剩余，避免 stall 死循环
                            if time.time() - min(started[f] for f in pending) >= PAPER_TIMEOUT:
                                for fut in list(pending):
                                    eno = futs[fut]
                                    stats["timeout_or_err"] = stats.get("timeout_or_err", 0) + 1
                                    _log(f"  timeout {eno} — blacklist")
                                    if eno not in blacklist:
                                        _append_jsonl(
                                            BLACKLIST,
                                            {
                                                "entry_no": eno,
                                                "reason": "worker_timeout",
                                                "ts": datetime.now().isoformat(timespec="seconds"),
                                            },
                                        )
                                        blacklist.add(eno)
                                    fut.cancel()
                                pending.clear()
                                break
                            continue
                        for fut in done:
                            eno = futs[fut]
                            try:
                                res = fut.result(timeout=1)
                            except Exception as exc:  # noqa: BLE001
                                stats["timeout_or_err"] = stats.get("timeout_or_err", 0) + 1
                                _log(f"  err {eno}: {exc}")
                                continue
                            st = res.get("status") or "err"
                            stats[st] = stats.get(st, 0) + 1
                            if st == "fixed_pdf_gold":
                                _log(f"  PDF_GOLD {eno} len={res.get('len')} via={res.get('via')}")
                            elif st == "pdf_extract_timeout":
                                _log(f"  timeout-extract {eno} — blacklist")
                                if eno not in blacklist:
                                    _append_jsonl(
                                        BLACKLIST,
                                        {
                                            "entry_no": eno,
                                            "reason": "pdf_extract_timeout",
                                            "ts": datetime.now().isoformat(timespec="seconds"),
                                        },
                                    )
                                    blacklist.add(eno)
                finally:
                    # 绝不等待已挂起的工人（进程池可强杀）
                    try:
                        pool.shutdown(wait=False, cancel_futures=True)
                    except TypeError:
                        pool.shutdown(wait=False)
                    if mode == "local" and isinstance(pool, ProcessPoolExecutor):
                        try:
                            pool._processes  # noqa: SLF001
                            for p in list(getattr(pool, "_processes", {}).values()):
                                try:
                                    p.kill()
                                except Exception:
                                    pass
                        except Exception:
                            pass
                off += len(batch)
                hb_state["progress"] = min(off, len(need))
                hb_state["pending"] = 0
                if last_cov is None or batch_i % COV_EVERY == 0:
                    last_cov = coverage()
                    hb_state["coverage"] = last_cov
                cov = last_cov or coverage()
                hb_state["coverage"] = cov
                _log(f"  progress {min(off,len(need))}/{len(need)} {stats} cov={cov}")
                STATUS.write_text(
                    json.dumps(
                        {
                            "round": rnd,
                            "mode": mode,
                            "progress": min(off, len(need)),
                            "need": len(need),
                            "stats": stats,
                            "coverage": cov,
                            "elapsed_sec": round(time.time() - t0, 1),
                            "updated_at": datetime.now().isoformat(timespec="seconds"),
                            "workers": workers,
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
        "targets": len(targets),
        "with_local_pdf": n_pdf,
        "stats": stats,
        "coverage": cov,
        "pct_pdf_gold": round(100.0 * cov["pdf_gold_ok"] / len(targets), 2) if targets else 0.0,
        "elapsed_sec": round(time.time() - t0, 1),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "note": "只认 is_pdf_gold；PDF/XML/HTML 同门槛（body/strict/metrics/score）；未达体量不算完成",
    }
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    STATUS.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"FINAL {json.dumps(rep, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
