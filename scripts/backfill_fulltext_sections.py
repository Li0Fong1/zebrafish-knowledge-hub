# -*- coding: utf-8 -*-
"""
对已命中全文回填章节结构（title/abstract/methods/results/...）。

用法：
  python -m scripts.backfill_fulltext_sections
  python -m scripts.backfill_fulltext_sections --limit 100
  python -m scripts.backfill_fulltext_sections --shards-only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.fulltext_sections import attach_sections_to_hit

OUT = ROOT / "zebrafish_package" / "结果" / "online_fulltext"
HIT = OUT / "results.jsonl"
SECTIONED = OUT / "results_sectioned.jsonl"
PROGRESS = OUT / "section_backfill_progress.json"
REPORT = OUT / "section_backfill_report.md"
LOG = OUT / "section_backfill.log"


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _iter_hit_files(*, shards_only: bool) -> list[Path]:
    shards = sorted(OUT.glob("results_w*.jsonl"))
    if shards_only:
        return shards
    files = list(shards)
    if HIT.is_file():
        files.append(HIT)
    return files


def _load_done() -> set[str]:
    if not PROGRESS.is_file():
        return set()
    try:
        return set(json.loads(PROGRESS.read_text(encoding="utf-8")).get("done") or [])
    except json.JSONDecodeError:
        return set()


def _save_progress(done: set[str], stats: dict[str, Any]) -> None:
    PROGRESS.write_text(
        json.dumps({"done": sorted(done, key=lambda x: int(x) if x.isdigit() else 0), **stats}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_report(stats: dict[str, Any]) -> None:
    lines = [
        "# 全文章节回填汇报",
        "",
        f"- **生成时间**：{stats.get('generated_at')}",
        f"- **输入命中**：{stats.get('input_hits')}",
        f"- **已回填**：{stats.get('sectioned')}",
        f"- **章节可用（≥2 核心段或 methods/results）**：{stats.get('sectioned_ok')}",
        f"- **跳过坏行**：{stats.get('bad_lines')}",
        f"- **输出**：`zebrafish_package/结果/online_fulltext/results_sectioned.jsonl`",
        "",
        "## 说明",
        "",
        "1. 按 `docs/fulltext_section_prompt.md`：提取 title/abstract/introduction/methods/results/discussion/conclusion。",
        "2. 在扁平 `body_text` 上规则切分；保留原 hit 全部字段并追加 `sections`。",
        "3. 与在线抓取流水线共用同一套切分逻辑。",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--shards-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--rewrite-sources", action="store_true", help="同时改写源 results*.jsonl")
    args = parser.parse_args()

    done = _load_done() if args.resume else set()
    by_no: dict[str, dict[str, Any]] = {}
    bad = 0
    for path in _iter_hit_files(shards_only=args.shards_only):
        if not path.is_file():
            continue
        for line in path.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            no = str(r.get("entry_no") or "")
            if not no:
                continue
            # 同号保留更长正文
            old = by_no.get(no)
            if old is None or int(r.get("text_chars") or 0) >= int(old.get("text_chars") or 0):
                by_no[no] = r

    nos = sorted(by_no.keys(), key=lambda x: int(x) if x.isdigit() else 0)
    if args.limit > 0:
        nos = nos[: args.limit]

    _log(f"回填开始：候选 {len(nos)}，已完成 {len(done)}，坏行 {bad}")
    OUT.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.resume and SECTIONED.is_file() else "w"
    sectioned_ok = 0
    wrote = 0
    t0 = time.time()

    # resume 时已有输出先计入
    existing_out: dict[str, dict[str, Any]] = {}
    if args.resume and SECTIONED.is_file() and mode == "a":
        for line in SECTIONED.open(encoding="utf-8"):
            try:
                r = json.loads(line)
                existing_out[str(r.get("entry_no"))] = r
            except json.JSONDecodeError:
                continue

    with SECTIONED.open(mode, encoding="utf-8") as out_fh:
        for no in nos:
            if no in done and no in existing_out:
                if existing_out[no].get("sectioned"):
                    sectioned_ok += 1
                continue
            hit = attach_sections_to_hit(by_no[no])
            out_fh.write(json.dumps(hit, ensure_ascii=False) + "\n")
            wrote += 1
            done.add(no)
            if hit.get("sectioned"):
                sectioned_ok += 1
            if wrote % 200 == 0:
                _save_progress(
                    done,
                    {
                        "status": "running",
                        "wrote": wrote,
                        "sectioned_ok": sectioned_ok,
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    },
                )
                _log(f"进度 wrote={wrote}/{len(nos)} sectioned_ok={sectioned_ok}")

    if args.rewrite_sources:
        # 用 sectioned 结果覆盖合并文件；分片仅追加 sections 字段
        section_map = {}
        for line in SECTIONED.open(encoding="utf-8"):
            try:
                r = json.loads(line)
                section_map[str(r.get("entry_no"))] = r
            except json.JSONDecodeError:
                continue
        for path in _iter_hit_files(shards_only=False):
            rows = []
            for line in path.open(encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                no = str(r.get("entry_no") or "")
                if no in section_map:
                    rows.append(section_map[no])
                else:
                    rows.append(attach_sections_to_hit(r))
            path.write_text(
                "\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + ("\n" if rows else ""),
                encoding="utf-8",
            )
            _log(f"已改写 {path.name} n={len(rows)}")

    stats = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_hits": len(nos),
        "sectioned": len(done),
        "sectioned_ok": sectioned_ok,
        "bad_lines": bad,
        "wrote_this_run": wrote,
        "elapsed_sec": round(time.time() - t0, 1),
        "status": "completed",
    }
    _save_progress(done, stats)
    _write_report(stats)
    _log(f"完成：wrote={wrote} done={len(done)} sectioned_ok={sectioned_ok} -> {SECTIONED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
