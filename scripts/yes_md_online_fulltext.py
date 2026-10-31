# -*- coding: utf-8 -*-
"""
是.md 全网在线全文解析：命中/未命中分文件写入，不下载 PDF/XML。

用法：
  python -m scripts.yes_md_online_fulltext --limit 5
  python -m scripts.yes_md_online_fulltext --resume --worker-id 0 --workers 4
  python -m scripts.yes_md_online_fulltext --merge --report-only
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
from rag.dedup_ris_index import load_dedup_ris_index, lookup_dedup_ris
from rag.fulltext_cascade import normalize_doi, resolve_all_ids
from rag.online_fulltext_fetch import online_fetch_fulltext
from scripts.yes_md_pdf_test_report import _find_yes_md, iter_yes_md_entries

OUT_ROOT = ROOT / "zebrafish_package" / "结果" / "online_fulltext"
HIT_JSONL = OUT_ROOT / "results.jsonl"
MISSED_JSONL = OUT_ROOT / "missed.jsonl"
PROGRESS_PATH = OUT_ROOT / "progress.json"
LOG_PATH = OUT_ROOT / "online_run.log"
REPORT_MD = ROOT / "zebrafish_package" / "结果" / "online_fulltext_report.md"
REPORT_JSON = ROOT / "zebrafish_package" / "结果" / "online_fulltext_report.json"
ROUND1_JSONL = ROOT / "zebrafish_package" / "结果" / "xml_full_batch" / "results.jsonl"
ROUND2_JSONL = ROOT / "zebrafish_package" / "结果" / "fulltext_round2" / "results.jsonl"
TOTAL_ENTRIES = 52255


def _log(msg: str, *, log_path: Path = LOG_PATH) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    safe_print(line)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _paths_for_worker(worker_id: int, workers: int) -> tuple[Path, Path, Path, Path]:
    if workers <= 1:
        return HIT_JSONL, MISSED_JSONL, PROGRESS_PATH, LOG_PATH
    return (
        OUT_ROOT / f"results_w{worker_id:02d}.jsonl",
        OUT_ROOT / f"missed_w{worker_id:02d}.jsonl",
        OUT_ROOT / f"progress_w{worker_id:02d}.json",
        OUT_ROOT / f"online_run_w{worker_id:02d}.log",
    )


def _all_shard_paths(prefix: str) -> list[Path]:
    """分片 + 合并文件都纳入，避免单进程写入被 merge 冲掉。"""
    shards = sorted(OUT_ROOT.glob(f"{prefix}_w*.jsonl"))
    merged = OUT_ROOT / f"{prefix}.jsonl"
    out = list(shards)
    if merged.is_file():
        out.append(merged)
    return out


def _load_done(progress_path: Path, hit_path: Path, missed_path: Path) -> set[str]:
    done: set[str] = set()
    # 读取全部 progress，更换 worker 数时也不会漏掉/狂复跑
    for p in [progress_path, *OUT_ROOT.glob("progress_w*.json"), OUT_ROOT / "progress.json"]:
        if not p.is_file():
            continue
        try:
            done |= set(json.loads(p.read_text(encoding="utf-8")).get("done_entry_nos") or [])
        except json.JSONDecodeError:
            pass
    for p in (
        hit_path,
        missed_path,
        *_all_shard_paths("results"),
        *_all_shard_paths("missed"),
    ):
        if not p.is_file():
            continue
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line.strip())
                    if r.get("entry_no"):
                        done.add(str(r["entry_no"]))
                except json.JSONDecodeError:
                    continue
    return done


def _load_existing_fulltext() -> set[str]:
    done: set[str] = set()
    for path, check in (
        (ROUND1_JSONL, lambda r: r.get("xml_type") == "fulltext" and int(r.get("text_chars") or 0) >= 3000),
        (ROUND2_JSONL, lambda r: r.get("status") == "ok" and r.get("content_type") in ("xml", "pdf")),
        (HIT_JSONL, lambda r: r.get("status") == "ok" and int(r.get("text_chars") or 0) >= 3000),
    ):
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line.strip())
                    if check(r):
                        done.add(str(r.get("entry_no")))
                except json.JSONDecodeError:
                    continue
    for p in _all_shard_paths("results"):
        if p == HIT_JSONL:
            continue
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line.strip())
                    if r.get("status") == "ok" and int(r.get("text_chars") or 0) >= 3000:
                        done.add(str(r.get("entry_no")))
                except json.JSONDecodeError:
                    continue
    return done


def _append_jsonl(record: dict[str, Any], *, jsonl_path: Path) -> None:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _save_progress(state: dict[str, Any], *, progress_path: Path) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_shards(prefix: str, out_path: Path) -> int:
    by_no: dict[str, dict[str, Any]] = {}
    # 先读完所有源（含现有合并文件）再回写，防止覆盖丢数据
    for p in _all_shard_paths(prefix):
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
    if not by_no:
        return 0
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as out:
        for no in sorted(by_no.keys(), key=lambda x: int(x) if x.isdigit() else 0):
            out.write(json.dumps(by_no[no], ensure_ascii=False) + "\n")
    tmp.replace(out_path)
    return len(by_no)


def merge_all() -> tuple[int, int]:
    hits = _merge_shards("results", HIT_JSONL)
    missed = _merge_shards("missed", MISSED_JSONL)
    return hits, missed


def _rebuild_stats() -> dict[str, Any]:
    by_step: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    hit_n = 0
    if HIT_JSONL.is_file():
        with HIT_JSONL.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                hit_n += 1
                by_step[r.get("source_step") or "?"] += 1
                by_type[r.get("content_type") or "?"] += 1
    miss_n = 0
    miss_with_doi = 0
    if MISSED_JSONL.is_file():
        with MISSED_JSONL.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                miss_n += 1
                if r.get("doi"):
                    miss_with_doi += 1
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_corpus": TOTAL_ENTRIES,
        "hit": hit_n,
        "missed": miss_n,
        "processed": hit_n + miss_n,
        "hit_rate": round(hit_n / max(hit_n + miss_n, 1) * 100, 2),
        "miss_with_doi": miss_with_doi,
        "by_source_step": dict(by_step),
        "by_content_type": dict(by_type),
        "hit_file": str(HIT_JSONL),
        "missed_file": str(MISSED_JSONL),
    }


def _write_report(stats: dict[str, Any]) -> None:
    lines = [
        "# 全网在线全文解析汇报",
        "",
        f"- **生成时间**：{stats['generated_at']}",
        f"- **语料总量**：{stats['total_corpus']}",
        f"- **已处理**：{stats['processed']}",
        f"- **命中（有全文）**：{stats['hit']}",
        f"- **未命中**：{stats['missed']}",
        f"- **命中率**：{stats['hit_rate']}%",
        f"- **未命中有 DOI**：{stats['miss_with_doi']}",
        "",
        "## 命中来源分布",
        "",
    ]
    for step, cnt in sorted((stats.get("by_source_step") or {}).items(), key=lambda x: -x[1]):
        lines.append(f"- `{step}`：{cnt}")
    lines.extend([
        "",
        "## 输出文件",
        "",
        f"- **命中全文**：`zebrafish_package/结果/online_fulltext/results.jsonl`",
        f"- **未命中清单**：`zebrafish_package/结果/online_fulltext/missed.jsonl`",
        "",
        "## 说明",
        "",
        "1. 全网模式：hunt 多站点 + EPMC/NCBI XML + HTML + Unpaywall/OpenAlex PDF（内存解析）。",
        "2. 合格条件：解析出全文结构，或可用正文 ≥3000 字；PubMed 摘要页不算命中。",
        "3. 不下载 PDF/XML 文件；命中写入 `results.jsonl`，未命中写入 `missed.jsonl`。",
        "4. 未命中条目可配合 `export_fulltext_gap_list` 走 VPN/科研通补全文。",
        "",
    ])
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    REPORT_JSON.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


def _slim_hit(result: dict[str, Any]) -> dict[str, Any]:
    slim = {k: v for k, v in result.items() if k != "attempts"}
    slim["attempt_count"] = len(result.get("attempts") or [])
    slim["bucket"] = "hit"
    return slim


def _slim_missed(result: dict[str, Any], entry: dict[str, str]) -> dict[str, Any]:
    attempts = result.get("attempts") or []
    last_note = attempts[-1].get("note") if attempts else ""
    miss_reason = (
        result.get("miss_reason")
        or last_note
        or result.get("status")
        or "无合格全文，已跳过并标记"
    )
    # 统一标记口径：非全文 / 超时重试 / 硬失败上限都进 missed
    if not str(miss_reason).startswith(("非全文", "网络/403", "无合格全文")):
        miss_reason = f"无合格全文：{miss_reason}"
    return {
        "entry_no": result.get("entry_no"),
        "en_title": result.get("en_title") or entry.get("EN Title", ""),
        "doi": result.get("doi") or "",
        "pmid": result.get("pmid") or "",
        "pmcid": result.get("pmcid") or "",
        "judgment": entry.get("Judgment", ""),
        "status": "missed",
        "miss_reason": miss_reason,
        "attempt_count": len(attempts),
        "last_attempts": attempts[-5:],
        "ris_matched": result.get("ris_matched", False),
        "bucket": "missed",
    }


def process_entry(
    entry: dict[str, str],
    ris_idx: dict[str, dict[str, str]],
    *,
    use_hunt: bool,
    fast: bool = True,
) -> dict[str, Any]:
    title = entry.get("EN Title", "")
    ris_meta = lookup_dedup_ris(title, ris_idx)
    doi = normalize_doi(ris_meta.get("doi") or "")
    ids = resolve_all_ids(title, doi=doi, wos_an=ris_meta.get("an") or "")
    result = online_fetch_fulltext(entry, ids=ids, use_hunt=use_hunt, fast=fast)
    result["doi"] = result.get("doi") or doi
    result["ris_matched"] = bool(ris_meta)
    result["judgment"] = entry.get("Judgment", "")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="全网在线全文解析（命中/未命中分文件）")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--worker-id", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--entry-no", type=str, default="")
    parser.add_argument("--no-hunt", action="store_true", help="跳过多站点 hunt")
    parser.add_argument("--fast", action="store_true", default=True, help="快模式：短超时/少重试/关hunt/并行OA")
    parser.add_argument("--slow", action="store_true", help="关闭快模式（深度全网）")
    parser.add_argument("--skip-existing", action="store_true", default=True)
    parser.add_argument("--no-skip-existing", action="store_false", dest="skip_existing")
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--report-every", type=int, default=100)
    args = parser.parse_args()
    if args.slow:
        args.fast = False
    if args.fast:
        args.no_hunt = True

    if args.merge or args.report_only:
        hits, missed = merge_all()
        stats = _rebuild_stats()
        _write_report(stats)
        _log(f"合并：命中 {hits}，未命中 {missed}；汇报已更新")
        return 0

    hit_path, missed_path, progress_path, log_path = _paths_for_worker(args.worker_id, args.workers)
    ris_idx = load_dedup_ris_index()
    yes_md = _find_yes_md()
    done = _load_done(progress_path, hit_path, missed_path) if args.resume else set()
    existing_ft = _load_existing_fulltext() if args.skip_existing else set()

    if args.entry_no:
        target = args.entry_no.strip()
        for entry in iter_yes_md_entries(yes_md, limit=0):
            if entry.get("entry_no") == target:
                result = process_entry(entry, ris_idx, use_hunt=not args.no_hunt, fast=args.fast)
                if result.get("status") == "ok":
                    _append_jsonl(_slim_hit(result), jsonl_path=hit_path)
                else:
                    _append_jsonl(_slim_missed(result, entry), jsonl_path=missed_path)
                safe_print(json.dumps({k: v for k, v in result.items() if k != "body_text"}, ensure_ascii=False, indent=2))
                return 0
        _log(f"未找到 entry_no={target}", log_path=log_path)
        return 1

    state: dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "worker_id": args.worker_id,
        "workers": args.workers,
        "mode": "online_full_network",
        "done_entry_nos": sorted(done, key=lambda x: int(x) if x.isdigit() else 0),
        "status": "running",
    }
    _save_progress(state, progress_path=progress_path)

    hit_count = miss_count = skipped_existing = 0
    t0 = time.time()
    _log(
        f"全网全文 worker={args.worker_id}/{args.workers} limit={args.limit or 'ALL'} "
        f"hunt={not args.no_hunt} fast={args.fast}",
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
            continue
        if no in existing_ft:
            skipped_existing += 1
            done.add(no)
            state["skipped_existing"] = skipped_existing
            state["done_entry_nos"] = sorted(done, key=lambda x: int(x) if x.isdigit() else 0)
            if skipped_existing % 100 == 0:
                _save_progress(state, progress_path=progress_path)
            continue

        try:
            result = process_entry(entry, ris_idx, use_hunt=not args.no_hunt, fast=args.fast)
            done.add(no)
            if result.get("status") == "ok":
                _append_jsonl(_slim_hit(result), jsonl_path=hit_path)
                hit_count += 1
                if hit_count <= 3 or hit_count % 20 == 0:
                    _log(
                        f"[{no}] HIT {result.get('content_type')} | {result.get('source_step')} | {result.get('text_chars')} chars",
                        log_path=log_path,
                    )
            else:
                _append_jsonl(_slim_missed(result, entry), jsonl_path=missed_path)
                miss_count += 1
                if miss_count <= 5 or miss_count % 100 == 0:
                    _log(f"[{no}] MISS {result.get('doi', '')[:30]}", log_path=log_path)

            state["done_entry_nos"] = sorted(done, key=lambda x: int(x) if x.isdigit() else 0)
            state["hit_count"] = hit_count
            state["miss_count"] = miss_count
            processed = hit_count + miss_count
            if processed % 5 == 0:
                _save_progress(state, progress_path=progress_path)
            if processed % args.report_every == 0:
                merge_all()
                _write_report(_rebuild_stats())
        except Exception as exc:
            _log(f"[{no}] ERROR {exc}", log_path=log_path)
            err_rec = {
                "entry_no": no,
                "en_title": entry.get("EN Title", ""),
                "status": "error",
                "miss_reason": str(exc),
                "bucket": "missed",
            }
            _append_jsonl(err_rec, jsonl_path=missed_path)
            done.add(no)
            miss_count += 1

    state["status"] = "completed"
    state["finished_at"] = datetime.now().isoformat(timespec="seconds")
    _save_progress(state, progress_path=progress_path)
    merge_all()
    _write_report(_rebuild_stats())
    elapsed = time.time() - t0
    _log(
        f"完成：命中 {hit_count}，未命中 {miss_count}，跳过已有 {skipped_existing}，耗时 {elapsed:.0f}s",
        log_path=log_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
