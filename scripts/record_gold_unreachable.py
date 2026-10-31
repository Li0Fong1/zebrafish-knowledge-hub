# -*- coding: utf-8 -*-
"""
无法金标台账：upsert / append 单条或多条记录。

约定：以后任何流水线在「最终仍无法金标」时调用本工具写入，避免漏记。

用法示例：
  python -m scripts.record_gold_unreachable --id 1000 --reason near_<floor_35487 \\
      --source inbox_172_fail --pdf-path ".../1000_....pdf" --json-path ".../1000.json" \\
      --retried --retry-count 2

  python -m scripts.record_gold_unreachable --from-jsonl path/to/batch.jsonl
  python -m scripts.record_gold_unreachable --rebuild   # 全量重建（调用 rebuild 模块）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_MANIFEST = ROOT / "解析文件" / "gold_unreachable_manifest.jsonl"
DEFAULT_SUMMARY = ROOT / "解析文件" / "gold_unreachable_summary.md"
DATA_MANIFEST = ROOT / "data" / "literature" / "gold_unreachable_manifest.jsonl"
DATA_SUMMARY = ROOT / "data" / "literature" / "gold_unreachable_summary.md"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _norm_id(rec: dict[str, Any]) -> str:
    for k in ("id", "entry_no", "eno", "pmid", "filename"):
        v = rec.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            rid = _norm_id(rec)
            if not rid:
                continue
            prev = out.get(rid)
            if prev is None:
                out[rid] = rec
                continue
            # merge: keep richer fields; bump retry_count; union sources
            merged = dict(prev)
            for k, v in rec.items():
                if v in (None, "", [], {}):
                    continue
                if k == "sources" and isinstance(v, list):
                    old = merged.get("sources") or []
                    if not isinstance(old, list):
                        old = [old] if old else []
                    merged["sources"] = sorted({str(x) for x in old + v if x})
                elif k == "source":
                    srcs = set(merged.get("sources") or [])
                    if merged.get("source"):
                        srcs.add(str(merged["source"]))
                    srcs.add(str(v))
                    merged["source"] = v
                    merged["sources"] = sorted(srcs)
                elif k == "retry_count":
                    try:
                        merged["retry_count"] = max(int(merged.get("retry_count") or 0), int(v))
                    except Exception:
                        merged["retry_count"] = merged.get("retry_count") or v
                elif k == "retried":
                    merged["retried"] = bool(merged.get("retried")) or bool(v)
                else:
                    merged[k] = v
            out[rid] = merged
    return out


def write_manifest(path: Path, rows: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    items = sorted(rows.values(), key=lambda r: str(_norm_id(r)))
    with path.open("w", encoding="utf-8") as fh:
        for rec in items:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def reason_bucket(reason: str, body_len: int | None = None) -> str:
    r = (reason or "").strip()
    if r:
        # normalize common patterns
        low = r.lower()
        if "empty" in low or r in ("empty_0", "无正文/抽取失败"):
            return "empty"
        if "timeout" in low or "worker_timeout" in low:
            return "timeout"
        if "quarantine" in low or "隔离" in low:
            return "quarantine"
        if "no_pdf" in low or "无 pdf" in low or "missing_pdf" in low:
            return "no_pdf"
        if "near" in low or "near_<floor" in low:
            return "near_threshold"
        if "tiny" in low:
            return "tiny_<500"
        if "short" in low and "mid" not in low:
            return "short_<3k"
        if "mid_" in low or "mid<" in low:
            return "mid_<15k"
        if "unreachable" in low or "paywall" in low or "no_legal_oa" in low or "online" in low:
            return "online_exhausted"
        if "正文过短" in r:
            if body_len is not None:
                return body_len_bucket(body_len)
            return "body_short"
        return r[:80]
    if body_len is None:
        return "unknown"
    return body_len_bucket(body_len)


def body_len_bucket(body_len: int) -> str:
    if body_len <= 0:
        return "empty"
    if body_len < 500:
        return "tiny_<500"
    if body_len < 3000:
        return "short_<3k"
    if body_len < 15000:
        return "mid_<15k"
    # floor 35487
    return "near_threshold"


def upsert_one(
    store: dict[str, dict[str, Any]],
    *,
    id: str,
    reason: str = "",
    source: str = "manual",
    pdf_path: str = "",
    json_path: str = "",
    pmid: str = "",
    doi: str = "",
    filename: str = "",
    body_len: int | None = None,
    retried: bool = False,
    retry_count: int = 0,
    final_status: str = "unreachable",
    corpus: str = "",
    gold_tier: str = "",
    extra: dict[str, Any] | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    rid = str(id).strip()
    if not rid:
        raise ValueError("id required")
    bucket = reason_bucket(reason, body_len)
    rec: dict[str, Any] = {
        "id": rid,
        "entry_no": rid,
        "pmid": pmid or "",
        "doi": doi or "",
        "filename": filename or "",
        "local_pdf_path": pdf_path or "",
        "json_path": json_path or "",
        "reason": reason or bucket,
        "reason_bucket": bucket,
        "source": source,
        "sources": [source] if source else [],
        "retried": bool(retried) or int(retry_count or 0) > 0,
        "retry_count": int(retry_count or 0),
        "final_status": final_status or "unreachable",
        "corpus": corpus or "",
        "gold_tier": gold_tier or "",
        "body_len": body_len if body_len is not None else None,
        "updated_at": ts or _now(),
    }
    if extra:
        for k, v in extra.items():
            if k not in rec or rec[k] in (None, "", [], {}):
                rec[k] = v
    prev = store.get(rid)
    if prev is None:
        store[rid] = rec
        return rec
    # merge into prev
    merged = dict(prev)
    for k, v in rec.items():
        if k == "sources":
            old = merged.get("sources") or []
            if not isinstance(old, list):
                old = [old] if old else []
            merged["sources"] = sorted({str(x) for x in list(old) + list(v or []) if x})
        elif k == "source":
            srcs = set(merged.get("sources") or [])
            if merged.get("source"):
                srcs.add(str(merged["source"]))
            if v:
                srcs.add(str(v))
            merged["source"] = v or merged.get("source")
            merged["sources"] = sorted(srcs)
        elif k == "retry_count":
            merged["retry_count"] = max(int(merged.get("retry_count") or 0), int(v or 0))
        elif k == "retried":
            merged["retried"] = bool(merged.get("retried")) or bool(v)
        elif k in ("local_pdf_path", "json_path", "pmid", "doi", "filename", "corpus", "gold_tier"):
            if v and not merged.get(k):
                merged[k] = v
            elif v:
                merged[k] = v
        elif k == "body_len":
            if v is not None:
                merged[k] = v
        elif k == "updated_at":
            merged[k] = v
        else:
            if v not in (None, "", [], {}):
                merged[k] = v
    merged["reason_bucket"] = reason_bucket(
        str(merged.get("reason") or ""),
        merged.get("body_len") if isinstance(merged.get("body_len"), int) else None,
    )
    merged["retried"] = bool(merged.get("retried")) or int(merged.get("retry_count") or 0) > 0
    if not merged.get("final_status"):
        merged["final_status"] = "unreachable"
    store[rid] = merged
    return merged


def write_summary(path: Path, rows: dict[str, dict[str, Any]], *, note: str = "") -> None:
    from collections import Counter

    path.parent.mkdir(parents=True, exist_ok=True)
    by_bucket = Counter()
    by_source = Counter()
    retried_n = 0
    for rec in rows.values():
        by_bucket[str(rec.get("reason_bucket") or "unknown")] += 1
        srcs = rec.get("sources") or ([rec.get("source")] if rec.get("source") else [])
        if isinstance(srcs, str):
            srcs = [srcs]
        for s in srcs:
            if s:
                by_source[str(s)] += 1
        if rec.get("retried"):
            retried_n += 1
    lines = [
        "# 无法金标总账摘要",
        "",
        f"- 生成时间：`{_now()}`",
        f"- 唯一篇数（去重后）：**{len(rows)}**",
        f"- 曾重试仍失败：**{retried_n}**",
        f"- 主清单：`解析文件/gold_unreachable_manifest.jsonl`",
        f"- 续写工具：`python -m scripts.record_gold_unreachable`",
        "",
    ]
    if note:
        lines += [f"> {note}", ""]
    lines += ["## 原因分桶", ""]
    for k, v in by_bucket.most_common():
        lines.append(f"- `{k}`: **{v}**")
    lines += ["", "## 来源标记（一篇可多源，计数可重叠）", ""]
    for k, v in by_source.most_common():
        lines.append(f"- `{k}`: **{v}**")
    lines += [
        "",
        "## 字段约定",
        "",
        "- `id` / `entry_no`：主键",
        "- `local_pdf_path` / `json_path`：本地路径（可空）",
        "- `reason` / `reason_bucket`：原因与分桶",
        "- `source` / `sources`：来源标记（legacy_gap / inbox_172_fail / online_exhausted / test_fix_final 等）",
        "- `retried` / `retry_count`：是否经过测试打回重做",
        "- `final_status`：通常为 `unreachable`",
        "- `updated_at`：时间戳",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mirror_to_data(rows: dict[str, dict[str, Any]]) -> None:
    write_manifest(DATA_MANIFEST, rows)
    write_summary(DATA_SUMMARY, rows, note="与解析文件/ 主清单同步副本，便于 data/literature 续用")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record unreachable-for-gold papers")
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    ap.add_argument("--id", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--source", default="manual")
    ap.add_argument("--pdf-path", default="")
    ap.add_argument("--json-path", default="")
    ap.add_argument("--pmid", default="")
    ap.add_argument("--doi", default="")
    ap.add_argument("--filename", default="")
    ap.add_argument("--body-len", type=int, default=None)
    ap.add_argument("--retried", action="store_true")
    ap.add_argument("--retry-count", type=int, default=0)
    ap.add_argument("--final-status", default="unreachable")
    ap.add_argument("--corpus", default="")
    ap.add_argument("--gold-tier", default="")
    ap.add_argument("--from-jsonl", type=Path, default=None)
    ap.add_argument("--rebuild", action="store_true", help="全量重建台账")
    ap.add_argument("--no-data-mirror", action="store_true")
    args = ap.parse_args(argv)

    if args.rebuild:
        from scripts.rebuild_gold_unreachable_manifest import rebuild

        n = rebuild(manifest=args.manifest, summary=args.summary, mirror_data=not args.no_data_mirror)
        print(f"rebuild_done unique={n}")
        return 0

    store = load_manifest(args.manifest)
    n_in = 0
    if args.from_jsonl and args.from_jsonl.is_file():
        with args.from_jsonl.open(encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                rid = _norm_id(o)
                if not rid:
                    continue
                upsert_one(
                    store,
                    id=rid,
                    reason=str(o.get("reason") or o.get("reason_bucket") or ""),
                    source=str(o.get("source") or "import"),
                    pdf_path=str(o.get("local_pdf_path") or o.get("pdf_path") or o.get("path") or ""),
                    json_path=str(o.get("json_path") or ""),
                    pmid=str(o.get("pmid") or ""),
                    doi=str(o.get("doi") or ""),
                    filename=str(o.get("filename") or ""),
                    body_len=o.get("body_len") if isinstance(o.get("body_len"), int) else None,
                    retried=bool(o.get("retried")),
                    retry_count=int(o.get("retry_count") or 0),
                    final_status=str(o.get("final_status") or "unreachable"),
                    corpus=str(o.get("corpus") or ""),
                    gold_tier=str(o.get("gold_tier") or ""),
                    extra=o,
                    ts=str(o.get("updated_at") or _now()),
                )
                n_in += 1

    if args.id:
        upsert_one(
            store,
            id=args.id,
            reason=args.reason,
            source=args.source,
            pdf_path=args.pdf_path,
            json_path=args.json_path,
            pmid=args.pmid,
            doi=args.doi,
            filename=args.filename,
            body_len=args.body_len,
            retried=args.retried,
            retry_count=args.retry_count,
            final_status=args.final_status,
            corpus=args.corpus,
            gold_tier=args.gold_tier,
        )
        n_in += 1

    if n_in == 0:
        print("nothing_to_write; use --id / --from-jsonl / --rebuild", file=sys.stderr)
        return 2

    write_manifest(args.manifest, store)
    write_summary(args.summary, store)
    if not args.no_data_mirror:
        mirror_to_data(store)
    print(f"upserted={n_in} unique={len(store)} manifest={args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
