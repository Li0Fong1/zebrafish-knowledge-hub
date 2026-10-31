# -*- coding: utf-8 -*-
"""
是.md 第二轮全文获取：以去重 merged_corpus RIS 为主源补 DOI/PMID，再跑 OA 级联。

用法：
  python -m scripts.yes_md_fulltext_round2 --build-index
  python -m scripts.yes_md_fulltext_round2 --limit 10
  python -m scripts.yes_md_fulltext_round2 --resume
"""
from __future__ import annotations

import argparse
import json
import re
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
from rag.dedup_ris_index import load_dedup_ris_index, lookup_dedup_ris
from rag.fulltext_cascade import (
    extract_pmcid_from_urls,
    fetch_jats_xml_cascade,
    jats_stats,
    normalize_pmcid,
    openalex_lookup,
    publisher_pdf_urls,
    resolve_ids_from_ris_meta,
    try_download_pdf,
    unpaywall_lookup,
)
from rag.knowledge_ingest import extract_pdf_text
from rag.literature_collector import _api_get, _sleep
from rag.pubmed_corpus_utils import parse_pubmed_xml
from scripts.yes_md_pdf_test_report import _find_yes_md, iter_yes_md_entries

OUT_ROOT = ROOT / "zebrafish_package" / "结果" / "fulltext_round2"
OUT_XML = OUT_ROOT / "xml"
OUT_PDF = OUT_ROOT / "pdf"
JSONL_PATH = OUT_ROOT / "results.jsonl"
PROGRESS_PATH = OUT_ROOT / "progress.json"
REPORT_MD = ROOT / "zebrafish_package" / "结果" / "fulltext_round2_report.md"
REPORT_JSON = ROOT / "zebrafish_package" / "结果" / "fulltext_round2_report.json"
LOG_PATH = OUT_ROOT / "round2_run.log"
ROUND1_JSONL = ROOT / "zebrafish_package" / "结果" / "xml_full_batch" / "results.jsonl"


def _paths_for_worker(worker_id: int, workers: int) -> tuple[Path, Path, Path]:
    if workers <= 1:
        return JSONL_PATH, PROGRESS_PATH, LOG_PATH
    return (
        OUT_ROOT / f"results_w{worker_id:02d}.jsonl",
        OUT_ROOT / f"progress_w{worker_id:02d}.json",
        OUT_ROOT / f"round2_run_w{worker_id:02d}.log",
    )


def _all_jsonl_paths() -> list[Path]:
    paths = sorted(OUT_ROOT.glob("results_w*.jsonl"))
    if JSONL_PATH.is_file():
        paths.append(JSONL_PATH)
    return paths


def merge_worker_jsonl() -> int:
    """合并各 worker 分片到 results.jsonl（按 entry_no 去重，保留最新）。"""
    by_no: dict[str, dict[str, Any]] = {}
    for p in _all_jsonl_paths():
        if p == JSONL_PATH and len(list(OUT_ROOT.glob("results_w*.jsonl"))) > 0:
            continue
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    no = str(r.get("entry_no") or "")
                    if no:
                        by_no[no] = r
                except json.JSONDecodeError:
                    continue
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with JSONL_PATH.open("w", encoding="utf-8") as out:
        for no in sorted(by_no.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            out.write(json.dumps(by_no[no], ensure_ascii=False) + "\n")
    return len(by_no)


def _log(msg: str, *, log_path: Path = LOG_PATH) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    safe_print(line)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _xml_shard(entry_no: str) -> Path:
    return OUT_XML / f"{int(entry_no) // 1000:05d}"


def _load_round1_fulltext() -> set[str]:
    done: set[str] = set()
    if not ROUND1_JSONL.is_file():
        return done
    with ROUND1_JSONL.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("xml_type") == "fulltext" and int(r.get("text_chars") or 0) >= 3000:
                done.add(str(r.get("entry_no")))
    return done


def _load_done(progress_path: Path = PROGRESS_PATH, jsonl_path: Path = JSONL_PATH) -> set[str]:
    done: set[str] = set()
    if progress_path.is_file():
        try:
            done |= set(json.loads(progress_path.read_text(encoding="utf-8")).get("done_entry_nos") or [])
        except json.JSONDecodeError:
            pass
    if jsonl_path.is_file():
        with jsonl_path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line.strip())
                    if r.get("entry_no"):
                        done.add(str(r["entry_no"]))
                except json.JSONDecodeError:
                    continue
    return done


def _save_progress(state: dict[str, Any], *, progress_path: Path = PROGRESS_PATH) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(record: dict[str, Any], *, jsonl_path: Path = JSONL_PATH) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def fetch_pubmed_abstract(pmid: str) -> dict[str, str]:
    if not pmid:
        return {}
    raw = _api_get("/efetch.fcgi", {"db": "pubmed", "id": pmid, "retmode": "xml"})
    _sleep()
    return parse_pubmed_xml(raw)


def cascade_round2(entry: dict[str, str], ris_idx: dict[str, dict[str, str]]) -> dict[str, Any]:
    no = entry.get("entry_no", "?")
    title = entry.get("EN Title", "")
    ris_meta = lookup_dedup_ris(title, ris_idx)
    ids = resolve_ids_from_ris_meta(ris_meta, title=title, enrich=True)

    result: dict[str, Any] = {
        "entry_no": no,
        "en_title": title,
        "zh_title": entry.get("ZH Title", ""),
        "doi": ids.get("doi", ""),
        "pmid": ids.get("pmid", ""),
        "pmcid": ids.get("pmcid", ""),
        "pmcid_candidates": ids.get("pmcid_candidates", []),
        "id_trace": ids.get("trace", []),
        "ris_matched": bool(ris_meta),
        "content_type": "",
        "source_step": "",
        "source_url": "",
        "file_path": "",
        "text_chars": 0,
        "xml_stats": {},
        "status": "pending",
        "attempts": [],
    }

    def attempt(step: str, ok: bool, note: str = "") -> None:
        result["attempts"].append({"step": step, "ok": ok, "note": note[:200]})

    pmcid_list = list(dict.fromkeys(
        [normalize_pmcid(ids.get("pmcid", ""))]
        + [normalize_pmcid(x) for x in ids.get("pmcid_candidates", []) if x]
    ))
    pmcid_list = [x for x in pmcid_list if x]

    for pmcid in pmcid_list or [""]:
        raw, used, url, xml_src = fetch_jats_xml_cascade(
            pmid=ids.get("pmid", ""),
            pmcid=pmcid,
            doi=ids.get("doi", ""),
            pmcid_candidates=ids.get("pmcid_candidates", []),
        )
        time.sleep(0.35)
        if raw:
            shard = _xml_shard(no)
            shard.mkdir(parents=True, exist_ok=True)
            fname = f"{no}_{used or pmcid or 'epmc'}_fulltext.xml"
            path = shard / fname
            path.write_bytes(raw)
            stats = jats_stats(raw)
            result.update({
                "content_type": "xml",
                "source_step": xml_src,
                "source_url": url,
                "file_path": str(path),
                "text_chars": stats.get("chars", 0),
                "xml_stats": stats,
                "pmcid": normalize_pmcid(used if str(used).upper().startswith("PMC") else pmcid),
                "status": "ok",
            })
            attempt(xml_src, True, url)
            return result
        attempt("epmc_xml+ncbi", False, f"pmcid={pmcid or '-'}")

    doi = ids.get("doi", "")
    if doi:
        up = unpaywall_lookup(doi)
        time.sleep(0.3)
        for pdf_url in up.get("pdf_urls", []):
            pmc_from = extract_pmcid_from_urls([pdf_url] + up.get("landing_urls", []))
            if pmc_from and pmc_from not in pmcid_list:
                raw, used, url, xml_src = fetch_jats_xml_cascade(pmcid=pmc_from, pmid=ids.get("pmid", ""))
                if raw:
                    shard = _xml_shard(no)
                    shard.mkdir(parents=True, exist_ok=True)
                    path = shard / f"{no}_{used}_fulltext.xml"
                    path.write_bytes(raw)
                    stats = jats_stats(raw)
                    result.update({
                        "content_type": "xml",
                        "source_step": f"unpaywall→{xml_src}",
                        "source_url": url,
                        "file_path": str(path),
                        "text_chars": stats.get("chars", 0),
                        "xml_stats": stats,
                        "pmcid": pmc_from,
                        "status": "ok",
                    })
                    attempt(f"unpaywall→{xml_src}", True, url)
                    return result
            shard = _xml_shard(no)
            shard.mkdir(parents=True, exist_ok=True)
            dest = shard / f"{no}_unpaywall.pdf"
            ok, note = try_download_pdf(pdf_url, str(dest))
            attempt("unpaywall_pdf", ok, note)
            if ok:
                text = extract_pdf_text(str(dest))
                result.update({
                    "content_type": "pdf",
                    "source_step": "unpaywall_pdf",
                    "source_url": pdf_url,
                    "file_path": str(dest),
                    "text_chars": len(text),
                    "status": "ok",
                })
                return result

        oa = openalex_lookup(doi=doi, pmid=ids.get("pmid", ""), title=title)
        time.sleep(0.3)
        for pdf_url in oa.get("pdf_urls", []):
            pmc_from = extract_pmcid_from_urls([pdf_url] + oa.get("landing_urls", []))
            if pmc_from:
                raw, used, url, xml_src = fetch_jats_xml_cascade(pmcid=pmc_from, pmid=ids.get("pmid", ""))
                if raw:
                    shard = _xml_shard(no)
                    shard.mkdir(parents=True, exist_ok=True)
                    path = shard / f"{no}_{used}_fulltext.xml"
                    path.write_bytes(raw)
                    stats = jats_stats(raw)
                    result.update({
                        "content_type": "xml",
                        "source_step": f"openalex→{xml_src}",
                        "source_url": url,
                        "file_path": str(path),
                        "text_chars": stats.get("chars", 0),
                        "xml_stats": stats,
                        "status": "ok",
                    })
                    attempt(f"openalex→{xml_src}", True, url)
                    return result
            shard = _xml_shard(no)
            shard.mkdir(parents=True, exist_ok=True)
            dest = shard / f"{no}_openalex.pdf"
            ok, note = try_download_pdf(pdf_url, str(dest))
            attempt("openalex_pdf", ok, note)
            if ok:
                text = extract_pdf_text(str(dest))
                result.update({
                    "content_type": "pdf",
                    "source_step": "openalex_pdf",
                    "source_url": pdf_url,
                    "file_path": str(dest),
                    "text_chars": len(text),
                    "status": "ok",
                })
                return result

        for pdf_url in publisher_pdf_urls(doi):
            shard = _xml_shard(no)
            shard.mkdir(parents=True, exist_ok=True)
            dest = shard / f"{no}_publisher.pdf"
            ok, note = try_download_pdf(pdf_url, str(dest))
            attempt("publisher_pdf", ok, note)
            if ok:
                text = extract_pdf_text(str(dest))
                result.update({
                    "content_type": "pdf",
                    "source_step": "publisher_pdf",
                    "source_url": pdf_url,
                    "file_path": str(dest),
                    "text_chars": len(text),
                    "status": "ok",
                })
                return result

    result.update({"status": "skipped_no_fulltext", "content_type": "", "source_step": "no_fulltext_skip"})
    attempt("all", False, "无 OA 全文，保留当前阶段跳过")
    return result


def _is_fulltext_result(result: dict[str, Any], *, min_chars: int = 500) -> bool:
    """仅 XML / PDF 且文本量达标才算全文成功。"""
    ct = result.get("content_type") or ""
    chars = int(result.get("text_chars") or 0)
    return ct in ("xml", "pdf") and chars >= min_chars and result.get("status") == "ok"


def _rebuild_stats() -> dict[str, Any]:
    by_type: Counter[str] = Counter()
    by_step: Counter[str] = Counter()
    has_doi = 0
    total = 0
    paths = _all_jsonl_paths()
    if not paths:
        return {"total": 0, "by_content_type": {}, "by_source_step": {}, "with_doi": 0}
    seen: set[str] = set()
    for p in paths:
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                no = str(r.get("entry_no") or "")
                if no in seen:
                    continue
                seen.add(no)
                total += 1
                by_type[r.get("content_type") or "unknown"] += 1
                by_step[r.get("source_step") or "unknown"] += 1
                if r.get("doi"):
                    has_doi += 1
    return {
        "total": total,
        "with_doi": has_doi,
        "by_content_type": dict(by_type),
        "by_source_step": dict(by_step),
    }


def _write_report(stats: dict[str, Any]) -> None:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    by_type = stats.get("by_content_type") or {}
    lines = [
        "# 是.md 第二轮全文获取汇报（去重 RIS 补 DOI）",
        "",
        f"- **生成时间**：{generated_at}",
        f"- **已处理**：{stats.get('total', 0)} 篇",
        f"- **有 DOI**：{stats.get('with_doi', 0)}",
        f"- **XML 全文**：{by_type.get('xml', 0)}",
        f"- **PDF 全文**：{by_type.get('pdf', 0)}",
        f"- **无全文跳过**：{stats.get('skipped_no_fulltext', 0)}（保留第一轮结果，不写入降级内容）",
        f"- **输出目录**：`zebrafish_package/结果/fulltext_round2/`",
        "",
        "## 说明",
        "",
        "1. ID 主源：`项目阶段/05_数据与库/归档/去重之后/merged_corpus/merged_corpus`（101 个 RIS）。",
        "2. 仅成功获取 **XML/PDF 全文** 才写入结果；摘要/RIS/占位 **一律跳过**。",
        "3. 第一轮已获 JATS 全文的条目自动跳过。",
        "",
    ]
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    REPORT_JSON.write_text(
        json.dumps({"generated_at": generated_at, **stats}, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="是.md 第二轮全文（去重 RIS 补 DOI）")
    parser.add_argument("--build-index", action="store_true", help="仅构建/刷新 RIS 索引缓存")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--merge", action="store_true", help="合并 worker 分片 JSONL 到 results.jsonl")
    parser.add_argument("--report-every", type=int, default=500)
    parser.add_argument("--worker-id", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--skip-round1-fulltext", action="store_true", default=True)
    parser.add_argument("--no-skip-round1-fulltext", action="store_false", dest="skip_round1_fulltext")
    args = parser.parse_args()

    if args.build_index:
        idx = load_dedup_ris_index(rebuild=True)
        _log(f"RIS 索引已构建：{len(idx)} 条，DOI {sum(1 for v in idx.values() if v.get('doi'))}")
        return 0

    if args.report_only:
        if args.merge or list(OUT_ROOT.glob("results_w*.jsonl")):
            n = merge_worker_jsonl()
            _log(f"已合并 worker 分片：{n} 篇")
        stats = _rebuild_stats()
        _write_report(stats)
        _log(f"汇报已更新：{stats['total']} 篇")
        return 0

    if args.merge:
        n = merge_worker_jsonl()
        _log(f"已合并 worker 分片：{n} 篇")
        return 0

    jsonl_path, progress_path, log_path = _paths_for_worker(args.worker_id, args.workers)

    ris_idx = load_dedup_ris_index()
    _log(f"RIS 索引加载：{len(ris_idx)} 条", log_path=log_path)

    done = _load_done(progress_path, jsonl_path) if args.resume else set()
    skip_round1 = _load_round1_fulltext() if args.skip_round1_fulltext else set()

    state: dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "worker_id": args.worker_id,
        "workers": args.workers,
        "done_entry_nos": sorted(done, key=lambda x: int(x) if x.isdigit() else 0),
        "processed_this_run": 0,
        "skipped_round1": 0,
        "skipped_done": 0,
        "errors": 0,
        "status": "running",
    }
    _save_progress(state, progress_path=progress_path)

    yes_md = _find_yes_md()
    processed = 0
    skipped = 0
    skipped_no_fulltext = 0
    t0 = time.time()
    _log(
        f"开始第二轮全文：limit={args.limit or 'ALL'} resume={args.resume} "
        f"worker={args.worker_id}/{args.workers}",
        log_path=log_path,
    )

    for entry in iter_yes_md_entries(
        yes_md,
        limit=args.limit,
        worker_id=args.worker_id,
        workers=args.workers,
    ):
        no = entry.get("entry_no", "?")
        if args.resume and no in done:
            state["skipped_done"] = state.get("skipped_done", 0) + 1
            continue
        if no in skip_round1:
            skipped += 1
            state["skipped_round1"] = skipped
            done.add(no)
            state["done_entry_nos"] = sorted(done, key=lambda x: int(x) if x.isdigit() else 0)
            if skipped % 500 == 0:
                _save_progress(state, progress_path=progress_path)
            continue

        try:
            result = cascade_round2(entry, ris_idx)
            if not _is_fulltext_result(result):
                skipped_no_fulltext += 1
                state["skipped_no_fulltext"] = skipped_no_fulltext
                done.add(no)
                state["done_entry_nos"] = sorted(done, key=lambda x: int(x) if x.isdigit() else 0)
                if skipped_no_fulltext <= 5 or skipped_no_fulltext % 200 == 0:
                    _log(f"[{no}] 无全文，跳过（保留当前阶段）", log_path=log_path)
                if skipped_no_fulltext % 100 == 0:
                    _save_progress(state, progress_path=progress_path)
                continue

            slim = {k: v for k, v in result.items() if k != "attempts"}
            slim["attempt_count"] = len(result.get("attempts") or [])
            slim["worker_id"] = args.worker_id
            _append_jsonl(slim, jsonl_path=jsonl_path)
            done.add(no)
            processed += 1
            state["last_entry_no"] = no
            state["processed_this_run"] = processed
            state["done_entry_nos"] = sorted(done, key=lambda x: int(x) if x.isdigit() else 0)

            if processed % 20 == 0:
                _save_progress(state, progress_path=progress_path)
            if processed % args.report_every == 0:
                merge_worker_jsonl()
                _write_report(_rebuild_stats())
                elapsed = time.time() - t0
                _log(
                    f"进度 +{processed} | 最新 [{no}] {result.get('content_type')} | {processed/elapsed:.2f}/s",
                    log_path=log_path,
                )
            elif processed <= 3:
                _log(
                    f"[{no}] {result.get('content_type')} | {result.get('source_step')} | doi={result.get('doi','')[:30]}",
                    log_path=log_path,
                )
        except Exception as exc:
            state["errors"] = int(state.get("errors", 0)) + 1
            _log(f"[{no}] ERROR {exc}", log_path=log_path)
            _append_jsonl({"entry_no": no, "status": "error", "error": str(exc)}, jsonl_path=jsonl_path)
            done.add(no)

    state["status"] = "completed"
    state["finished_at"] = datetime.now().isoformat(timespec="seconds")
    _save_progress(state, progress_path=progress_path)
    merge_worker_jsonl()
    stats = _rebuild_stats()
    _write_report({**stats, "skipped_no_fulltext": int(state.get("skipped_no_fulltext", 0))})
    _log(
        f"完成 worker {args.worker_id}: 全文 {processed}，无全文跳过 {skipped_no_fulltext}，"
        f"沿用第一轮 {skipped}，累计写入 {stats['total']}，"
        f"XML={stats['by_content_type'].get('xml',0)} PDF={stats['by_content_type'].get('pdf',0)}",
        log_path=log_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
