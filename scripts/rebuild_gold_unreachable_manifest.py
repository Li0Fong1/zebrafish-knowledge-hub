# -*- coding: utf-8 -*-
"""
全量重建「无法金标」总账：本地语料扫描 + unreachable 注册表 + inbox 失败 + 超时黑名单 + 测试打回。

用法：
  python -m scripts.rebuild_gold_unreachable_manifest
  python -m scripts.record_gold_unreachable --rebuild
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gold_hunt_fulltext import PDF_GOLD_BODY, is_pdf_gold  # noqa: E402
from scripts.record_gold_unreachable import (  # noqa: E402
    DEFAULT_MANIFEST,
    DEFAULT_SUMMARY,
    body_len_bucket,
    mirror_to_data,
    upsert_one,
    write_manifest,
    write_summary,
)

YES = ROOT / "解析文件" / "yes_fulltext_gold"
PDFKU = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
ORPHAN = ROOT / "解析文件" / "pdfku_orphan_fulltext_gold"
PDF_DIR = ROOT / "pdf库" / "pdf"
QUAR_DIR = ROOT / "pdf库" / "_duplicates_quarantine"
LOGS = ROOT / "logs"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _eno_from_name(name: str) -> str | None:
    m = re.match(r"^(\d+)", Path(name).stem)
    return m.group(1) if m else None


def _index_pdfs() -> dict[str, str]:
    out: dict[str, str] = {}
    for base in (PDF_DIR, QUAR_DIR):
        if not base.is_dir():
            continue
        for p in base.rglob("*.pdf"):
            eno = _eno_from_name(p.name)
            if not eno:
                continue
            # prefer standard pdf/ over quarantine
            if eno in out and "quarantine" not in out[eno].lower() and "quarantine" in str(p).lower():
                continue
            if eno not in out or "quarantine" in out[eno].lower():
                out[eno] = str(p)
    return out


def _load_jsonl_unique(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln.startswith("{"):
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            eno = str(o.get("entry_no") or o.get("id") or "").strip()
            if not eno:
                continue
            out[eno] = o  # last write wins
    return out


def _scan_corpus(
    store: dict[str, dict[str, Any]],
    root: Path,
    *,
    corpus: str,
    pdf_index: dict[str, str],
    retried_ids: dict[str, int],
) -> tuple[int, int]:
    gold = 0
    fail = 0
    if not root.is_dir():
        return 0, 0
    for p in root.glob("batch_*/papers/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            upsert_one(
                store,
                id=p.stem,
                reason="json_broken",
                source="legacy_gap",
                json_path=str(p),
                pdf_path=pdf_index.get(p.stem, ""),
                corpus=corpus,
                retried=p.stem in retried_ids,
                retry_count=retried_ids.get(p.stem, 0),
                final_status="unreachable",
            )
            fail += 1
            continue
        if is_pdf_gold(rec):
            gold += 1
            continue
        body = len(rec.get("body_text") or "")
        bucket = body_len_bucket(body)
        tier = str(rec.get("gold_tier") or "")
        reason = bucket
        if tier in ("unreachable_oa", "structural_exhausted_oa"):
            reason = f"online_exhausted:{tier}"
        elif tier:
            reason = f"{bucket}|tier={tier}"
        eno = str(rec.get("entry_no") or p.stem)
        upsert_one(
            store,
            id=eno,
            reason=reason,
            source="legacy_gap",
            pdf_path=str(rec.get("source_pdf") or "") or pdf_index.get(eno, ""),
            json_path=str(p),
            pmid=str(rec.get("pmid") or ""),
            doi=str(rec.get("doi") or ""),
            filename=str(rec.get("pdf_name") or ""),
            body_len=body,
            corpus=corpus,
            gold_tier=tier,
            retried=eno in retried_ids,
            retry_count=retried_ids.get(eno, 0),
            final_status="unreachable",
            extra={"source_field": rec.get("source") or ""},
        )
        fail += 1
    return gold, fail


def _collect_retried_ids() -> dict[str, int]:
    """从测试打回 / rescue / blacklist / repair 日志估计 retry_count。"""
    counts: Counter[str] = Counter()

    # timeout blacklists => at least 1 retry isolation
    for bl in (YES / "_hunt_timeout_blacklist.json", PDFKU / "_hunt_timeout_blacklist.json"):
        if not bl.is_file():
            continue
        try:
            data = json.loads(bl.read_text(encoding="utf-8"))
        except Exception:
            continue
        for eno in data.get("entry_nos") or []:
            counts[str(eno)] += 1

    # pdf extract blacklist jsonl
    pbl = PDFKU / "pdf_extract_blacklist.jsonl"
    if pbl.is_file():
        for eno, _ in _load_jsonl_unique(pbl).items():
            counts[eno] += 1

    # gold_test_fix history samples
    hist = ROOT / "解析文件" / "gold_test_fix_history.jsonl"
    if hist.is_file():
        with hist.open(encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                for key in ("fail_sample_yes", "fail_sample_pdfku"):
                    for eno in (o.get("before") or {}).get(key) or o.get(key) or []:
                        counts[str(eno)] += 1
                after = o.get("after") or {}
                for key in ("fail_sample_yes", "fail_sample_pdfku"):
                    for eno in after.get(key) or []:
                        counts[str(eno)] += 1

    latest = ROOT / "解析文件" / "gold_test_fix_latest.json"
    if latest.is_file():
        try:
            o = json.loads(latest.read_text(encoding="utf-8"))
        except Exception:
            o = {}
        for block in (o.get("before") or {}, o.get("after") or {}, o):
            for key in ("fail_sample_yes", "fail_sample_pdfku"):
                for eno in block.get(key) or []:
                    counts[str(eno)] += 1

    # unreachable registry: multiple attempts => retry signal
    for reg in (
        YES / "unreachable_oa_registry.jsonl",
        PDFKU / "unreachable_oa_registry.jsonl",
        ORPHAN / "unreachable_oa_registry.jsonl",
    ):
        if not reg.is_file():
            continue
        seen: Counter[str] = Counter()
        with reg.open(encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if not ln.startswith("{"):
                    continue
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                eno = str(o.get("entry_no") or "")
                if eno:
                    seen[eno] += 1
        for eno, n in seen.items():
            if n >= 2:
                counts[eno] += min(n, 20)

    return {k: int(v) for k, v in counts.items()}


def _merge_online_exhausted(
    store: dict[str, dict[str, Any]],
    pdf_index: dict[str, str],
    retried_ids: dict[str, int],
) -> int:
    n = 0
    for reg in (
        YES / "unreachable_oa_registry.jsonl",
        PDFKU / "unreachable_oa_registry.jsonl",
        ORPHAN / "unreachable_oa_registry.jsonl",
    ):
        uniq = _load_jsonl_unique(reg)
        for eno, o in uniq.items():
            reason = str(o.get("reason") or "online_exhausted")
            upsert_one(
                store,
                id=eno,
                reason=reason,
                source="online_exhausted",
                pdf_path=pdf_index.get(eno, ""),
                pmid=str(o.get("pmid") or ""),
                doi=str(o.get("doi") or ""),
                retried=eno in retried_ids or int(o.get("tried_urls") or 0) > 0 or True,
                retry_count=max(retried_ids.get(eno, 0), 1 if o.get("permanent") else 0),
                final_status="unreachable",
                corpus="unreachable_registry",
                extra={"permanent": bool(o.get("permanent")), "title": (o.get("title") or "")[:200]},
                ts=str(o.get("updated_at") or _now()),
            )
            n += 1
    return n


def _merge_inbox_fails(
    store: dict[str, dict[str, Any]],
    pdf_index: dict[str, str],
    retried_ids: dict[str, int],
) -> int:
    """inbox 172 中最终未达标（优先用 register 报告 + sync todo + 本地非金标 PDF）。"""
    inbox_enos: set[str] = set()
    body_by: dict[str, int] = {}
    bucket_by: dict[str, str] = {}
    path_by: dict[str, str] = {}

    sync = LOGS / "_server_inbox_sync_status.json"
    if sync.is_file():
        try:
            data = json.loads(sync.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        for item in data.get("todo_sample") or []:
            if not isinstance(item, dict):
                continue
            eno = str(item.get("entry_no") or "")
            if not eno:
                continue
            inbox_enos.add(eno)
            if isinstance(item.get("body_len"), int):
                body_by[eno] = item["body_len"]
            if item.get("path"):
                path_by[eno] = str(item["path"])

    # parse logs: last status per entry
    for name in (
        "_server_inbox_parse_out.txt",
        "_direct_finish_stdout.log",
        "_finish_inbox_stdout.log",
        "_runner_stdout.log",
    ):
        p = LOGS / name
        if not p.is_file():
            continue
        with p.open(encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln.startswith("{") or "entry_no" not in ln:
                    continue
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                eno = str(o.get("entry_no") or "")
                if not eno:
                    continue
                inbox_enos.add(eno)
                if o.get("gold"):
                    # may later be overridden if final not gold; keep for now
                    continue
                if isinstance(o.get("body_len"), int):
                    body_by[eno] = o["body_len"]
                if o.get("bucket"):
                    bucket_by[eno] = str(o["bucket"])

    regp = LOGS / "inbox_register_db_report.json"
    if regp.is_file():
        try:
            rep = json.loads(regp.read_text(encoding="utf-8"))
        except Exception:
            rep = {}
        for item in rep.get("not_gold_samples") or []:
            eno = str(item.get("entry_no") or "")
            if not eno:
                continue
            inbox_enos.add(eno)
            if isinstance(item.get("body_len"), int):
                body_by[eno] = item["body_len"]
            if item.get("bucket"):
                bucket_by[eno] = str(item["bucket"])

    # Reconstruct full inbox set: local PDFs that are not gold in PDFKU (the incremental batch was subset of pdf/)
    # Prefer explicit final list if present
    final = LOGS / "_inbox_batch172_final.json"
    # We may not have full 138 IDs; derive from pdfku not-gold ∩ pdf_dir for those in sync uploaded set.
    # Also: all pdf/ files whose JSON is not gold — but that could be >172.
    # Use: if PDF exists in pdf/ and corresponding PDFKU json is not gold, AND entry appears in any inbox log → inbox fail.

    # Expand: every entry in parse uniqueness that ended not gold
    parse_last: dict[str, dict[str, Any]] = {}
    for name in ("_server_inbox_parse_out.txt", "_direct_finish_stdout.log"):
        p = LOGS / name
        if not p.is_file():
            continue
        with p.open(encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln.startswith("{"):
                    continue
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                eno = str(o.get("entry_no") or "")
                if eno and "gold" in o:
                    parse_last[eno] = o

    not_gold_inbox = {eno for eno, o in parse_last.items() if not o.get("gold")}
    # Also include samples/reasons from register report counts — need full 138
    # Scan PDFKU for PDFs under pdf/ that are not gold: take those as candidate inbox if size matches 172 target set.
    pdf_enos = set(pdf_index.keys())
    # Better approach: rebuild 172 from sync script logic using gold index
    gold_idx = {p.stem: p for p in PDFKU.glob("batch_*/papers/*.json")}
    todo: list[str] = []
    for eno, pdf_path in sorted(pdf_index.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0]):
        # only standard pdf dir
        if "quarantine" in pdf_path.lower():
            continue
        jp = gold_idx.get(eno)
        is_g = False
        body = body_by.get(eno)
        if jp:
            try:
                rec = json.loads(jp.read_text(encoding="utf-8"))
                is_g = is_pdf_gold(rec)
                body = len(rec.get("body_text") or "")
            except Exception:
                is_g = False
        if not is_g:
            todo.append(eno)
            if body is not None:
                body_by[eno] = body
    # The morning incremental selected 172; if current not-gold among pdf/ differs, use intersection with logged inbox_enos when available.
    if len(inbox_enos) >= 50:
        candidates = [e for e in todo if e in inbox_enos or e in not_gold_inbox]
        if len(candidates) < 50:
            candidates = todo
    else:
        candidates = todo

    # If we still have exactly the register not_gold=138 signal, prefer not-gold among pdf/ capped by logged set
    fail_set = set(candidates)
    # Remove those that are actually gold now
    for eno in list(fail_set):
        jp = gold_idx.get(eno)
        if not jp:
            continue
        try:
            if is_pdf_gold(json.loads(jp.read_text(encoding="utf-8"))):
                fail_set.discard(eno)
        except Exception:
            pass

    n = 0
    for eno in sorted(fail_set, key=lambda x: int(x) if x.isdigit() else x):
        # Only mark inbox_172_fail if it was in the inbox campaign OR among pdf/ not-gold that logs touched
        src = "inbox_172_fail"
        if eno not in inbox_enos and eno not in not_gold_inbox and eno not in parse_last:
            # still a local pdf gap under pdf/ — mark as legacy but keep
            src = "pdfku_pdfdir_gap"
        body = body_by.get(eno)
        bucket = bucket_by.get(eno) or (body_len_bucket(body) if isinstance(body, int) else "unknown")
        jp = gold_idx.get(eno)
        upsert_one(
            store,
            id=eno,
            reason=bucket,
            source=src,
            pdf_path=path_by.get(eno) or pdf_index.get(eno, ""),
            json_path=str(jp) if jp else "",
            body_len=body if isinstance(body, int) else None,
            retried=True,  # inbox went through server/local parse attempts
            retry_count=max(retried_ids.get(eno, 0), 1),
            final_status="unreachable",
            corpus="pdfku_inbox",
        )
        n += 1

    # annotate known 138 from report if we can read a full list file later
    _ = final
    return n


def _merge_timeout_quarantine(
    store: dict[str, dict[str, Any]],
    pdf_index: dict[str, str],
    retried_ids: dict[str, int],
) -> int:
    n = 0
    for bl in (YES / "_hunt_timeout_blacklist.json", PDFKU / "_hunt_timeout_blacklist.json"):
        if not bl.is_file():
            continue
        try:
            data = json.loads(bl.read_text(encoding="utf-8"))
        except Exception:
            continue
        for eno in data.get("entry_nos") or []:
            eno = str(eno)
            upsert_one(
                store,
                id=eno,
                reason="timeout_quarantine",
                source="timeout_blacklist",
                pdf_path=pdf_index.get(eno, ""),
                retried=True,
                retry_count=max(retried_ids.get(eno, 0), 1),
                final_status="unreachable",
            )
            n += 1
    pbl = PDFKU / "pdf_extract_blacklist.jsonl"
    for eno, o in _load_jsonl_unique(pbl).items():
        upsert_one(
            store,
            id=eno,
            reason=str(o.get("reason") or "pdf_extract_blacklist"),
            source="timeout_blacklist",
            pdf_path=pdf_index.get(eno, "") or str(o.get("pdf") or ""),
            retried=True,
            retry_count=max(retried_ids.get(eno, 0), 1),
            final_status="unreachable",
        )
        n += 1
    return n


def _mark_test_fix_final(store: dict[str, dict[str, Any]], retried_ids: dict[str, int]) -> int:
    """凡仍在台账且出现在 test_fix 失败样本中的，标记 retried + source。"""
    n = 0
    for eno, rc in retried_ids.items():
        if eno not in store:
            continue
        upsert_one(
            store,
            id=eno,
            reason=str(store[eno].get("reason") or "test_fix_final"),
            source="test_fix_final",
            retried=True,
            retry_count=max(int(store[eno].get("retry_count") or 0), int(rc), 1),
            final_status="unreachable",
            pdf_path=str(store[eno].get("local_pdf_path") or ""),
            json_path=str(store[eno].get("json_path") or ""),
            body_len=store[eno].get("body_len") if isinstance(store[eno].get("body_len"), int) else None,
        )
        n += 1
    return n


def rebuild(
    *,
    manifest: Path = DEFAULT_MANIFEST,
    summary: Path = DEFAULT_SUMMARY,
    mirror_data: bool = True,
) -> int:
    pdf_index = _index_pdfs()
    retried_ids = _collect_retried_ids()
    store: dict[str, dict[str, Any]] = {}

    stats: dict[str, Any] = {"started_at": _now(), "floor": PDF_GOLD_BODY}

    g, f = _scan_corpus(store, YES, corpus="yes_fulltext_gold", pdf_index=pdf_index, retried_ids=retried_ids)
    stats["yes_gold"] = g
    stats["yes_not"] = f
    g, f = _scan_corpus(store, PDFKU, corpus="pdfku_library_fulltext_gold", pdf_index=pdf_index, retried_ids=retried_ids)
    stats["pdfku_gold"] = g
    stats["pdfku_not"] = f
    g, f = _scan_corpus(store, ORPHAN, corpus="pdfku_orphan_fulltext_gold", pdf_index=pdf_index, retried_ids=retried_ids)
    stats["orphan_gold"] = g
    stats["orphan_not"] = f

    stats["online_registry_upserts"] = _merge_online_exhausted(store, pdf_index, retried_ids)
    stats["inbox_upserts"] = _merge_inbox_fails(store, pdf_index, retried_ids)
    stats["timeout_upserts"] = _merge_timeout_quarantine(store, pdf_index, retried_ids)
    stats["test_fix_marks"] = _mark_test_fix_final(store, retried_ids)

    # PDFs with no JSON at all
    gold_enos = {p.stem for p in PDFKU.glob("batch_*/papers/*.json")}
    gold_enos |= {p.stem for p in YES.glob("batch_*/papers/*.json")}
    no_json = 0
    for eno, path in pdf_index.items():
        if "quarantine" in path.lower():
            continue
        if eno in gold_enos:
            continue
        upsert_one(
            store,
            id=eno,
            reason="no_json",
            source="legacy_gap",
            pdf_path=path,
            retried=False,
            retry_count=0,
            final_status="unreachable",
            corpus="pdf_only",
        )
        no_json += 1
    stats["pdf_no_json"] = no_json

    write_manifest(manifest, store)
    note = (
        f"全量重建；yes_not={stats['yes_not']} pdfku_not={stats['pdfku_not']} "
        f"orphan_not={stats['orphan_not']}；合并 online/inbox/timeout/test_fix；去重后 {len(store)} 篇"
    )
    write_summary(summary, store, note=note)
    if mirror_data:
        mirror_to_data(store)

    stats["unique"] = len(store)
    stats["finished_at"] = _now()
    meta = ROOT / "解析文件" / "gold_unreachable_rebuild_meta.json"
    meta.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return len(store)


def main() -> int:
    n = rebuild()
    print(f"DONE unique={n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
