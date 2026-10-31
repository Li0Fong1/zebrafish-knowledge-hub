# -*- coding: utf-8 -*-
"""
是.md 全量 XML 全文分析批处理（可断点续跑）。

保证每篇均有可分析 XML：JATS 全文 → PubMed XML → 是.md 合成 JATS。

用法：
  python -m scripts.yes_md_xml_full_batch
  python -m scripts.yes_md_xml_full_batch --resume
  python -m scripts.yes_md_xml_full_batch --limit 100
  python -m scripts.yes_md_xml_full_batch --worker-id 0 --workers 4
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from rag.fulltext_cascade import (
    fetch_jats_xml_cascade,
    jats_stats,
    resolve_all_ids,
    resolve_ids_fast,
)
from scripts.yes_md_pdf_test_report import (
    _find_merged_ris,
    _find_yes_md,
    build_ris_title_index,
    iter_yes_md_entries,
    lookup_ris_meta,
)
from scripts.yes_md_web_xml_hunt import (
    SITE_CATALOG,
    _fetch_url,
    _is_jats_or_nlm_xml,
    _xml_text_chars,
    attach_xml_analysis,
    build_synth_jats,
)

OUT_ROOT = ROOT / "zebrafish_package" / "结果" / "xml_full_batch"
XML_DIR = OUT_ROOT / "xml"
JSONL_PATH = OUT_ROOT / "results.jsonl"
PROGRESS_PATH = OUT_ROOT / "progress.json"
REPORT_MD = ROOT / "zebrafish_package" / "结果" / "xml_full_batch_report.md"
REPORT_JSON = ROOT / "zebrafish_package" / "结果" / "xml_full_batch_report.json"
LOG_PATH = OUT_ROOT / "batch_run.log"


def _xml_shard_dir(entry_no: str) -> Path:
    bucket = int(entry_no) // 1000
    return XML_DIR / f"{bucket:05d}"


def _load_done() -> set[str]:
    done: set[str] = set()
    if PROGRESS_PATH.is_file():
        try:
            done |= set(json.loads(PROGRESS_PATH.read_text(encoding="utf-8")).get("done_entry_nos") or [])
        except json.JSONDecodeError:
            pass
    if JSONL_PATH.is_file():
        with JSONL_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if rec.get("entry_no"):
                        done.add(str(rec["entry_no"]))
                except json.JSONDecodeError:
                    continue
    return done


def _save_progress(state: dict[str, Any]) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(record: dict[str, Any]) -> None:
    JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JSONL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    safe_print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _pick_abstract(entry: dict[str, str], ids: dict[str, Any]) -> str:
    for key in ("EN Abstract", "ZH Abstract", "Reason"):
        val = (entry.get(key) or "").strip()
        if val and val not in ("无摘要", "无"):
            return val
    return entry.get("EN Title") or entry.get("ZH Title") or ""


def process_entry_fast(
    entry: dict[str, str],
    ris_idx: dict[str, dict[str, str]],
    *,
    full_id_resolve: bool = False,
    synth_without_ris: bool = True,
) -> dict[str, Any]:
    no = entry.get("entry_no", "?")
    title = entry.get("EN Title", "")
    ris_meta = lookup_ris_meta(title, ris_idx)
    has_ris_id = bool(ris_meta.get("doi") or str(ris_meta.get("an", "")).startswith("MEDLINE:"))

    if synth_without_ris and not has_ris_id:
        ids = {"doi": "", "pmid": "", "pmcid": "", "pmcid_candidates": [], "trace": ["yes_md:local"]}
    elif full_id_resolve:
        ids = resolve_all_ids(title, doi=ris_meta.get("doi", ""), wos_an=ris_meta.get("an", ""))
    else:
        ids = resolve_ids_fast(title, doi=ris_meta.get("doi", ""), wos_an=ris_meta.get("an", ""))

    result: dict[str, Any] = {
        "entry_no": no,
        "en_title": title,
        "zh_title": entry.get("ZH Title", ""),
        "doi": ids.get("doi", ""),
        "pmid": ids.get("pmid", ""),
        "pmcid": ids.get("pmcid", ""),
        "winning_site": "",
        "winning_site_name": "",
        "winning_url": "",
        "xml_path": "",
        "xml_type": "",
        "text_chars": 0,
        "xml_stats": {},
        "status": "pending",
        "id_trace": ids.get("trace") or [],
    }

    raw = b""
    site_key = ""
    url = ""

    if ids.get("pmid") or ids.get("pmcid") or ids.get("doi"):
        raw, used, url, src = fetch_jats_xml_cascade(
            pmid=ids.get("pmid", ""),
            pmcid=ids.get("pmcid", ""),
            doi=ids.get("doi", ""),
            pmcid_candidates=ids.get("pmcid_candidates", []),
        )
        if raw:
            site_key = "europe_pmc" if src == "epmc_xml" else "ncbi_pmc"

    if raw and _is_jats_or_nlm_xml(raw):
        shard = _xml_shard_dir(no)
        shard.mkdir(parents=True, exist_ok=True)
        fname = f"{no}_{ids.get('pmcid') or ids.get('pmid') or site_key}_fulltext.xml"
        path = shard / fname
        path.write_bytes(raw)
        stats = jats_stats(raw)
        chars = _xml_text_chars(raw)
        is_full = chars > 3000 and (
            stats.get("sections", 0) >= 2
            or site_key in ("europe_pmc", "ncbi_pmc")
        )
        result.update({
            "winning_site": site_key,
            "winning_site_name": SITE_CATALOG.get(site_key, site_key),
            "winning_url": url,
            "xml_path": str(path),
            "xml_type": "fulltext" if is_full else "partial_xml",
            "text_chars": chars,
            "xml_stats": stats,
            "status": "ok",
        })
        return attach_xml_analysis(result)

    if ids.get("pmid"):
        pub_url = (
            f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?"
            f"db=pubmed&id={ids['pmid']}&retmode=xml"
        )
        pub_raw, code, _ = _fetch_url(pub_url, timeout=30)
        if pub_raw and _is_jats_or_nlm_xml(pub_raw):
            shard = _xml_shard_dir(no)
            shard.mkdir(parents=True, exist_ok=True)
            path = shard / f"{no}_PMID{ids['pmid']}_pubmed.xml"
            path.write_bytes(pub_raw)
            stats = jats_stats(pub_raw)
            chars = _xml_text_chars(pub_raw)
            result.update({
                "winning_site": "ncbi_pubmed",
                "winning_site_name": SITE_CATALOG["ncbi_pubmed"],
                "winning_url": pub_url,
                "xml_path": str(path),
                "xml_type": "pubmed_xml",
                "text_chars": chars,
                "xml_stats": stats,
                "status": "ok",
            })
            return attach_xml_analysis(result)

    abstract = _pick_abstract(entry, ids)
    synth = build_synth_jats(entry, ids, abstract)
    shard = _xml_shard_dir(no)
    shard.mkdir(parents=True, exist_ok=True)
    path = shard / f"{no}_synth_jats.xml"
    path.write_bytes(synth)
    stats = jats_stats(synth)
    chars = _xml_text_chars(synth)
    result.update({
        "winning_site": "yes_md_synth",
        "winning_site_name": SITE_CATALOG["yes_md_synth"],
        "winning_url": "local://yes_md_synth",
        "xml_path": str(path),
        "xml_type": "synthetic_jats",
        "text_chars": chars,
        "xml_stats": stats,
        "status": "ok",
    })
    return attach_xml_analysis(result)


def _slim_record(record: dict[str, Any]) -> dict[str, Any]:
    slim = {k: v for k, v in record.items() if k != "xml_analysis"}
    analysis = record.get("xml_analysis") or {}
    if analysis:
        slim["xml_analysis"] = {
            "title": analysis.get("title", ""),
            "body_char_count": analysis.get("body_char_count", 0),
            "section_count": len(analysis.get("section_stats") or {}),
            "figure_count": analysis.get("figure_count", 0),
            "table_count": analysis.get("table_count", 0),
            "ref_count": analysis.get("ref_count", 0),
            "zebrafish_terms": analysis.get("zebrafish_terms") or [],
            "gene_mentions": (analysis.get("gene_mentions") or [])[:10],
            "preview": (analysis.get("preview") or "")[:300],
        }
    return slim


def render_batch_report(stats: dict[str, Any], *, generated_at: str) -> str:
    total = stats.get("total", 0)
    by_type = stats.get("by_type") or {}
    by_site = stats.get("by_site") or {}
    lines = [
        "# 是.md 全量 XML 全文分析汇报",
        "",
        f"- **生成时间**：{generated_at}",
        f"- **已处理**：{total} 篇",
        f"- **JATS 全文**：{by_type.get('fulltext', 0)}",
        f"- **部分 XML**：{by_type.get('partial_xml', 0)}",
        f"- **PubMed XML 降级**：{by_type.get('pubmed_xml', 0)}",
        f"- **合成 JATS**：{by_type.get('synthetic_jats', 0)}",
        f"- **XML 目录**：`zebrafish_package/结果/xml_full_batch/xml/`",
        f"- **JSONL**：`zebrafish_package/结果/xml_full_batch/results.jsonl`",
        "",
        "## 网点命中统计",
        "",
        "| 网点 | 篇数 |",
        "|---|---:|",
    ]
    for site, n in sorted(by_site.items(), key=lambda x: -x[1]):
        lines.append(f"| {site} | {n} |")
    lines.extend([
        "",
        "## 说明",
        "",
        "1. 每篇文献保证生成可分析 XML 文件（全文 / PubMed / 合成 JATS）。",
        "2. 批处理使用轻量 ID 解析 + Europe PMC / NCBI PMC 级联，支持 `--resume` 断点续跑。",
        "3. 详细逐篇记录见 `results.jsonl`；进度见 `xml_full_batch/progress.json`。",
        "",
    ])
    return "\n".join(lines)


def _rebuild_stats_from_jsonl() -> dict[str, Any]:
    by_type: Counter[str] = Counter()
    by_site: Counter[str] = Counter()
    total = 0
    if not JSONL_PATH.is_file():
        return {"total": 0, "by_type": {}, "by_site": {}}
    with JSONL_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            by_type[rec.get("xml_type") or "unknown"] += 1
            by_site[rec.get("winning_site_name") or rec.get("winning_site") or "unknown"] += 1
    return {
        "total": total,
        "by_type": dict(by_type),
        "by_site": dict(by_site),
    }


def _write_reports(stats: dict[str, Any]) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    REPORT_MD.write_text(render_batch_report(stats, generated_at=generated_at), encoding="utf-8", newline="\n")
    REPORT_JSON.write_text(
        json.dumps({"generated_at": generated_at, **stats}, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="是.md 全量 XML 全文分析批处理")
    parser.add_argument("--limit", type=int, default=0, help="0=全部")
    parser.add_argument("--resume", action="store_true", help="跳过已完成条目")
    parser.add_argument("--report-only", action="store_true", help="仅从 JSONL 重生成汇报")
    parser.add_argument("--report-every", type=int, default=200, help="每 N 篇更新一次汇报")
    parser.add_argument("--worker-id", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--full-id-resolve", action="store_true", help="使用完整 resolve_all_ids（慢）")
    args = parser.parse_args()

    if args.report_only:
        stats = _rebuild_stats_from_jsonl()
        _write_reports(stats)
        _log(f"汇报已更新：{stats['total']} 篇")
        return 0

    yes_md = _find_yes_md()
    ris_idx = build_ris_title_index(_find_merged_ris())
    done = _load_done() if args.resume else set()

    state: dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "worker_id": args.worker_id,
        "workers": args.workers,
        "done_entry_nos": sorted(done, key=lambda x: int(x) if x.isdigit() else 0),
        "processed_this_run": 0,
        "skipped": 0,
        "errors": 0,
        "last_entry_no": "",
        "status": "running",
    }
    _save_progress(state)

    stats = _rebuild_stats_from_jsonl()
    processed = 0
    skipped = 0
    errors = 0
    t0 = time.time()

    _log(
        f"开始全量 XML 分析：{yes_md.name} "
        f"(worker {args.worker_id}/{args.workers}, resume={args.resume}, limit={args.limit or 'ALL'})"
    )

    for entry in iter_yes_md_entries(
        yes_md,
        limit=args.limit,
        worker_id=args.worker_id,
        workers=args.workers,
    ):
        no = entry.get("entry_no", "?")
        if args.resume and no in done:
            skipped += 1
            continue

        title = (entry.get("EN Title") or "")[:70]
        try:
            result = process_entry_fast(entry, ris_idx, full_id_resolve=args.full_id_resolve)
            slim = _slim_record(result)
            _append_jsonl(slim)
            done.add(no)
            processed += 1
            state["done_entry_nos"] = sorted(done, key=lambda x: int(x) if x.isdigit() else 0)
            state["processed_this_run"] = processed
            state["skipped"] = skipped
            state["errors"] = errors
            state["last_entry_no"] = no
            state["last_heartbeat_at"] = datetime.now().isoformat(timespec="seconds")

            if processed % 20 == 0:
                _save_progress(state)

            if processed % args.report_every == 0:
                stats = _rebuild_stats_from_jsonl()
                _write_reports(stats)
                elapsed = time.time() - t0
                rate = processed / elapsed if elapsed > 0 else 0
                _log(
                    f"进度 {len(done)} 篇 | 本轮 +{processed} | 跳过 {skipped} | "
                    f"最新 [{no}] {result.get('xml_type')} | {rate:.2f} 篇/秒"
                )
            elif processed <= 5 or processed % 50 == 0:
                _log(f"[{no}] {result.get('xml_type')} | {title}")

        except Exception as exc:
            errors += 1
            state["errors"] = errors
            _log(f"[{no}] ERROR: {exc}")
            err_rec = {
                "entry_no": no,
                "en_title": entry.get("EN Title", ""),
                "status": "error",
                "error": str(exc),
            }
            _append_jsonl(err_rec)
            done.add(no)

    stats = _rebuild_stats_from_jsonl()
    state["status"] = "completed"
    state["finished_at"] = datetime.now().isoformat(timespec="seconds")
    state["processed_this_run"] = processed
    state["skipped"] = skipped
    state["errors"] = errors
    _save_progress(state)
    _write_reports(stats)

    elapsed = int(time.time() - t0)
    _log(
        f"完成：本轮处理 {processed}，跳过 {skipped}，错误 {errors}，"
        f"累计 {stats['total']} 篇，耗时 {elapsed}s"
    )
    _log(f"汇报：{REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
