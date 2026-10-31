# -*- coding: utf-8 -*-
"""
短/残缺正文 → 研究全文补齐（目标是全文，不是凑到 5000）。

策略：
  1) OA/本地/源站 PDF → pdf_align
  2) Europe PMC / 本地 JATS fulltext XML
  3) 源站 HTML（降权壳页面）
  4) online_fetch_fulltext（可 --online）
  仅当新正文明显更长且质量更好才覆盖；卡住超时跳过继续。

用法：
  python -u -m scripts.gold_enrich_short_bodies --fulltext
  python -u -m scripts.gold_enrich_short_bodies --select-max 25000 --rounds 8 --online
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ONLINE_FULLTEXT_TIMEOUT", "16")
os.environ.setdefault("ONLINE_FULLTEXT_RETRIES", "2")
os.environ.setdefault("ONLINE_FULLTEXT_HARD_FAIL_LIMIT", "3")

from core.entry import bootstrap_app

bootstrap_app()

from rag.fulltext_cascade import normalize_doi, try_download_pdf as cascade_dl  # noqa: E402
from rag.online_fulltext_fetch import fetch_url_bytes, html_to_text, online_fetch_fulltext  # noqa: E402
from scripts.gold_gap_fill import collect_urls, download_pdf, polish_pass  # noqa: E402
from scripts.gold_final_12h_campaign import finalize_rec, pubmed_abstract_blob  # noqa: E402
from scripts.gold_strict_full_campaign import (  # noqa: E402
    MIN_BODY,
    STRICT_ROOT,
    batch_no_for,
    empty_noise,
    fetch_epmc_xml_file,
    load_oa_cache,
    save_rec,
    strict_issues,
)
from scripts.gold_upgrade_5680 import (  # noqa: E402
    PDF_CACHE,
    find_local_pdf,
    find_xml_for_entry,
    load_exist,
    load_jsonl_index,
    upgrade_from_jsonl,
    upgrade_from_pdf,
    upgrade_from_xml,
    _build_pdf_index,
)
import scripts.gold_upgrade_5680 as gu  # noqa: E402

LOG = STRICT_ROOT / "enrich_fulltext.log"
# 全文软目标：尽量拉到此以上；有 PDF/JATS 则不设上限
FULLTEXT_AIM = 25000
GROW_ABS = 2000
PAPER_TIMEOUT = 75.0


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    STRICT_ROOT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def strip_exhausted_padding(body: str) -> str:
    if "[provenance]" not in (body or ""):
        return body or ""
    chunks = re.split(r"\[provenance\][^\n]*\n?", body)
    real = "\n\n".join(c.strip() for c in chunks if c.strip() and "acquisition_exhausted" not in c)
    paras = [p.strip() for p in re.split(r"\n\s*\n", real) if p.strip()]
    uniq: list[str] = []
    seen: set[str] = set()
    for p in paras:
        key = p[:180]
        if key in seen and len(p) > 80:
            continue
        seen.add(key)
        uniq.append(p)
    out = "\n\n".join(uniq).strip()
    return out or body.split("[provenance]")[0].strip()


def body_score(body: str) -> int:
    n = len(body or "")
    if n < 400:
        return 0
    low = body.lower()
    chrome = 0
    for token in (
        "nihpa",
        "pmc-is-collection",
        "pmc-collection-title",
        "cookie",
        "subscribe",
        "sign in",
        "javascript",
        "cloudflare",
        "enable cookies",
        "accept all",
    ):
        chrome += low.count(token)
    if chrome >= 5:
        return max(0, n // 5)
    if chrome >= 2:
        return max(0, n // 2)
    # 研究全文信号加分
    bonus = 0
    for token in ("introduction", "materials and methods", "results", "discussion", "references"):
        if token in low:
            bonus += 2000
    return n + bonus


def is_fulltext_enough(rec: dict[str, Any]) -> bool:
    """已接近/达到研究全文则跳过。"""
    body = strip_exhausted_padding(rec.get("body_text") or "")
    src = rec.get("source") or ""
    tier = rec.get("gold_tier") or ""
    if tier == "structural_exhausted_oa" or (rec.get("metrics") or {}).get("acquisition_exhausted"):
        return False
    if src == "pdf_align" and len(body) >= 8000:
        return True
    if src == "xml_jats_fulltext" and len(body) >= 8000:
        return True
    if len(body) >= FULLTEXT_AIM and body_score(body) >= FULLTEXT_AIM:
        return True
    return False


def better(new_body: str, old_body: str, *, new_src: str = "") -> bool:
    if len(new_body) < MIN_BODY:
        return False
    ns, os_ = body_score(new_body), body_score(old_body)
    if ns <= os_ and not (new_src == "pdf_align" and len(new_body) > len(old_body)):
        return False
    if len(new_body) <= len(old_body) and new_src != "pdf_align":
        return False
    # PDF 对齐优先：只要更长就换
    if new_src == "pdf_align" and len(new_body) >= len(old_body) + 500:
        return True
    if len(old_body) < FULLTEXT_AIM and len(new_body) >= min(FULLTEXT_AIM, len(old_body) + GROW_ABS):
        return True
    if len(new_body) >= len(old_body) + GROW_ABS and ns >= int(os_ * 1.15):
        return True
    return False


def list_need_fulltext(rows: list[dict[str, str]], select_max: int) -> list[tuple[dict[str, str], dict[str, Any], Path]]:
    out = []
    by = {str(r["entry_no"]): r for r in rows}
    seen: set[str] = set()
    for p in STRICT_ROOT.glob("batch_*/papers/*.json"):
        eno = p.stem
        if eno in seen:
            continue
        seen.add(eno)
        rec = json.loads(p.read_text(encoding="utf-8"))
        if is_fulltext_enough(rec):
            continue
        body = strip_exhausted_padding(rec.get("body_text") or "")
        # select_max：优先打短的；同时也打未 pdf/jats 且低于 aim 的
        if len(body) > select_max and (rec.get("source") in ("pdf_align", "xml_jats_fulltext")):
            continue
        row = by.get(eno)
        if row:
            out.append((row, rec, p))
    out.sort(key=lambda t: len(strip_exhausted_padding(t[1].get("body_text") or "")))
    return out


def try_doi_pdf(doi: str, dest: Path) -> Path | None:
    if not doi:
        return None
    try:
        req = urllib.request.Request(
            f"https://doi.org/{doi}",
            headers={"User-Agent": "Mozilla/5.0 ZebrafishGeneQA/1.0", "Accept": "application/pdf,*/*"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            final = resp.geturl()
        if data[:5].startswith(b"%PDF") and len(data) > 8000:
            dest.write_bytes(data)
            return dest
        if "pdf" in (final or "").lower():
            ok, _ = cascade_dl(final, str(dest), timeout=20)
            if ok:
                return dest
    except Exception:  # noqa: BLE001
        return None
    return None


def harvest(
    row: dict[str, str],
    hit: dict | None,
    oa_cache: dict,
    old_body: str,
    *,
    use_online: bool,
    hunt: bool,
) -> dict[str, Any] | None:
    eno = str(row["entry_no"])
    title = (row.get("en_title") or (hit or {}).get("en_title") or "").strip()
    cands: list[tuple[str, dict[str, Any]]] = []

    def add(rec: dict[str, Any] | None, tag: str) -> None:
        if not rec:
            return
        b = rec.get("body_text") or ""
        if len(b) < 400:
            return
        cands.append((tag, rec))

    # 1 PDF（全文主路径）
    pdf = find_local_pdf(eno)
    if not pdf:
        try:
            urls = collect_urls(row, oa_cache)
            pdf = download_pdf(eno, urls)
        except Exception:  # noqa: BLE001
            pdf = None
    if not pdf:
        PDF_CACHE.mkdir(parents=True, exist_ok=True)
        pdf = try_doi_pdf(normalize_doi(row.get("doi") or ""), PDF_CACHE / f"{eno}.pdf")
    if pdf:
        try:
            add(upgrade_from_pdf(pdf, row, title), "pdf_align")
        except Exception:  # noqa: BLE001
            pass

    # 2 源站
    url = (row.get("source_url") or "").strip()
    if url:
        try:
            raw, code, _ = fetch_url_bytes(url, timeout=16, fast=True)
            if raw and int(code or 0) < 400:
                if raw[:5].startswith(b"%PDF"):
                    PDF_CACHE.mkdir(parents=True, exist_ok=True)
                    dest = PDF_CACHE / f"{eno}_src.pdf"
                    dest.write_bytes(raw)
                    try:
                        add(upgrade_from_pdf(dest, row, title), "pdf_align")
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    text = html_to_text(raw)
                    add(
                        {
                            "entry_no": eno,
                            "title": title,
                            "doi": row.get("doi") or "",
                            "pmid": row.get("pmid") or "",
                            "content_type": "html",
                            "source": "jsonl_gold_deep",
                            "layout": "generic",
                            "body_text": text[:250000],
                            "sections": {},
                            "noise": empty_noise(),
                        },
                        "src_html",
                    )
        except Exception:  # noqa: BLE001
            pass

    # 3 JATS
    xml = None
    try:
        xml = fetch_epmc_xml_file(eno, row)
    except Exception:  # noqa: BLE001
        xml = None
    if not xml:
        xml = find_xml_for_entry(eno)
    if xml:
        try:
            rec_x = upgrade_from_xml(xml, row, title, hit)
            tag = "xml_jats_fulltext" if rec_x and rec_x.get("source") == "xml_jats_fulltext" else "xml"
            add(rec_x, tag)
        except Exception:  # noqa: BLE001
            pass

    # 4 online cascade
    if use_online:
        try:
            fr = online_fetch_fulltext(
                {
                    "entry_no": eno,
                    "EN Title": title,
                    "doi": row.get("doi") or "",
                    "pmid": row.get("pmid") or "",
                },
                fast=not hunt,
                use_hunt=hunt,
            )
            body = fr.get("body_text") or ""
            if len(body) >= 800:
                ctype = fr.get("content_type") or "html"
                if ctype == "pdf" or (fr.get("source_step") or "").endswith("pdf"):
                    # 已是文本；标 deep
                    src = "jsonl_gold_deep"
                else:
                    src = "jsonl_gold_deep"
                add(
                    {
                        "entry_no": eno,
                        "title": title,
                        "doi": row.get("doi") or fr.get("doi") or "",
                        "pmid": row.get("pmid") or fr.get("pmid") or "",
                        "content_type": ctype,
                        "source": src,
                        "layout": "generic",
                        "body_text": body[:250000],
                        "sections": {},
                        "noise": empty_noise(),
                        "online_step": fr.get("source_step") or "",
                    },
                    "online",
                )
        except Exception:  # noqa: BLE001
            pass

    # 5 jsonl
    if hit:
        try:
            add(upgrade_from_jsonl(row, title, hit), "jsonl")
        except Exception:  # noqa: BLE001
            pass
        raw = hit.get("body_text") or ""
        if len(raw) > 800:
            add(
                {
                    "entry_no": eno,
                    "title": title,
                    "doi": row.get("doi") or "",
                    "pmid": row.get("pmid") or "",
                    "content_type": hit.get("content_type") or "text",
                    "source": "jsonl_gold_deep",
                    "layout": "generic",
                    "body_text": raw[:250000],
                    "sections": {},
                    "noise": empty_noise(),
                },
                "jsonl_raw",
            )

    if not cands:
        return None

    def rank(item: tuple[str, dict[str, Any]]) -> tuple[int, int, int]:
        tag, rec = item
        b = rec.get("body_text") or ""
        pref = 3 if tag == "pdf_align" else (2 if "xml" in tag else (1 if tag == "online" else 0))
        return (body_score(b), pref, len(b))

    tag, best = max(cands, key=rank)
    body = best.get("body_text") or ""
    abs_blob = pubmed_abstract_blob(row.get("pmid") or "")
    if abs_blob and abs_blob not in body and len(body) < FULLTEXT_AIM:
        body = (body + "\n\n" + abs_blob).strip()
        best = dict(best)
        best["body_text"] = body[:250000]

    old_clean = strip_exhausted_padding(old_body)
    new_src = "pdf_align" if tag == "pdf_align" else (best.get("source") or tag)
    if tag == "pdf_align":
        best["source"] = "pdf_align"
    if not better(best.get("body_text") or "", old_clean, new_src=new_src):
        return None
    best["enrich_from"] = tag
    best["gold_tier"] = "fulltext"
    return best


def process_one(
    row: dict[str, str],
    old: dict[str, Any],
    hit: dict | None,
    oa_cache: dict,
    rows_order: dict[str, int],
    *,
    use_online: bool,
    hunt: bool,
) -> dict[str, Any]:
    eno = str(row["entry_no"])
    old_clean = strip_exhausted_padding(old.get("body_text") or "")
    try:
        neu = harvest(row, hit, oa_cache, old.get("body_text") or "", use_online=use_online, hunt=hunt)
    except Exception as exc:  # noqa: BLE001
        return {"entry_no": eno, "status": "err", "err": str(exc)}
    if not neu:
        return {"entry_no": eno, "status": "no_gain", "old": len(old_clean)}

    neu = polish_pass(neu) if "sections" in neu else neu
    neu = finalize_rec(neu, exhausted=False)
    new_body = neu.get("body_text") or ""
    if len(new_body) <= len(old_clean) and neu.get("source") != "pdf_align":
        return {"entry_no": eno, "status": "no_gain", "old": len(old_clean), "new": len(new_body)}
    if len(new_body) < len(old_clean):
        return {"entry_no": eno, "status": "no_gain", "old": len(old_clean), "shorter": len(new_body)}

    neu["gold_tier"] = "fulltext"
    if isinstance(neu.get("metrics"), dict):
        neu["metrics"]["acquisition_exhausted"] = False
    if strict_issues(neu):
        neu = finalize_rec(neu, exhausted=False)
        if strict_issues(neu):
            return {"entry_no": eno, "status": "strict_fail", "issues": strict_issues(neu)[:3]}
    save_rec(neu, batch_no_for(eno, rows_order))
    return {
        "entry_no": eno,
        "status": "upgraded",
        "old": len(old_clean),
        "new": len(neu.get("body_text") or ""),
        "from": neu.get("enrich_from") or neu.get("source"),
        "source": neu.get("source"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--select-max", type=int, default=25000, help="低于此长度（或非PDF/JATS全文）纳入补齐")
    ap.add_argument("--max-body", type=int, default=0, help="兼容旧参数，等同 --select-max")
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--online", action="store_true", help="启用 online_fetch 级联（更慢更全）")
    ap.add_argument("--fulltext", action="store_true", help="全文模式预设")
    args = ap.parse_args()
    if args.fulltext:
        args.select_max = max(args.select_max, 25000)
        args.online = True
        args.rounds = max(args.rounds, 8)
    if args.max_body:
        args.select_max = args.max_body

    gu._PDF_INDEX = _build_pdf_index()
    rows = load_exist()
    rows_order = {str(r["entry_no"]): i for i, r in enumerate(rows)}
    hit_idx = load_jsonl_index()
    oa_cache = load_oa_cache()
    _log(
        f"FULLTEXT enrich select_max={args.select_max} online={args.online} "
        f"aim>={FULLTEXT_AIM} workers={args.workers}"
    )

    for rnd in range(1, args.rounds + 1):
        targets = list_need_fulltext(rows, args.select_max)
        if args.limit:
            targets = targets[: args.limit]
        hunt = rnd >= 3
        _log(f"===== FULLTEXT ROUND {rnd}/{args.rounds} need={len(targets)} hunt={hunt} =====")
        if not targets:
            _log("no targets left")
            break
        stats = {"upgraded": 0, "no_gain": 0, "err": 0, "timeout": 0, "strict_fail": 0, "pdf": 0}
        chunk_n = max(args.workers * 2, 8)

        def work(item: tuple) -> dict[str, Any]:
            row, old, _path = item
            return process_one(
                row,
                old,
                hit_idx.get(str(row["entry_no"])),
                oa_cache,
                rows_order,
                use_online=args.online,
                hunt=hunt,
            )

        with ThreadPoolExecutor(max_workers=max(2, args.workers)) as pool:
            for off in range(0, len(targets), chunk_n):
                chunk = targets[off : off + chunk_n]
                futs = {pool.submit(work, it): str(it[0]["entry_no"]) for it in chunk}
                pending = set(futs)
                t0 = {f: time.time() for f in futs}
                while pending:
                    done, pending = wait(pending, timeout=3.0, return_when=FIRST_COMPLETED)
                    now = time.time()
                    for f in list(pending):
                        if now - t0[f] > PAPER_TIMEOUT:
                            pending.discard(f)
                            stats["timeout"] += 1
                            _log(f"  timeout {futs[f]}")
                    for f in done:
                        try:
                            res = f.result(timeout=1)
                        except Exception as exc:  # noqa: BLE001
                            stats["err"] += 1
                            _log(f"  err {exc}")
                            continue
                        st = res.get("status")
                        if st == "upgraded":
                            stats["upgraded"] += 1
                            if res.get("source") == "pdf_align" or res.get("from") == "pdf_align":
                                stats["pdf"] += 1
                            _log(
                                f"  UP {res['entry_no']} {res['old']}->{res['new']} "
                                f"via {res.get('from')} src={res.get('source')}"
                            )
                        elif st in stats:
                            stats[st] += 1
                        else:
                            stats["no_gain"] += 1
                _log(f"  chunk {min(off + len(chunk), len(targets))}/{len(targets)} {stats}")
                oa_cache = load_oa_cache()

        _log(f"ROUND {rnd} done {stats}")
        if stats["upgraded"] == 0:
            if args.online and not hunt:
                continue
            _log("no further fulltext gains")
            break

    left = list_need_fulltext(rows, args.select_max)
    still_short = sum(
        1
        for _r, rec, _p in left
        if len(strip_exhausted_padding(rec.get("body_text") or "")) < FULLTEXT_AIM
    )
    rep = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "still_need": len(left),
        "still_below_aim": still_short,
        "fulltext_aim": FULLTEXT_AIM,
        "select_max": args.select_max,
    }
    (STRICT_ROOT / "enrich_fulltext_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _log(f"FINAL still_need={len(left)} below_aim={still_short} aim={FULLTEXT_AIM}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
