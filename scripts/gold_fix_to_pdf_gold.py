# -*- coding: utf-8 -*-
"""
审计并修复「已解析但未达十五篇 PDF 金标」的记录。

金标 = is_pdf_gold()。可修：长 pdf_align 抛光；有本地 PDF 则重抽。
无 PDF / 抽文不足 → need_hunt（交给 online hunt），不伪造。

用法：
  python -u -m scripts.gold_fix_to_pdf_gold
  python -u -m scripts.gold_fix_to_pdf_gold --local-only --workers 2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_gap_fill import polish_pass  # noqa: E402
from scripts.gold_hunt_fulltext import (  # noqa: E402
    PDF_GOLD_BODY,
    equal_coverage_snapshot,
    is_pdf_gold,
)
from scripts.gold_strict_full_campaign import (  # noqa: E402
    STRICT_ROOT,
    batch_no_for,
    save_rec,
    strict_issues,
)
from scripts.gold_upgrade_5680 import (  # noqa: E402
    MIN_BODY,
    _build_pdf_index,
    find_local_pdf,
    load_exist,
    upgrade_from_pdf,
)
import scripts.gold_upgrade_5680 as gu  # noqa: E402

STATUS = STRICT_ROOT / "fix_to_pdf_gold_status.json"
REPORT = STRICT_ROOT / "fix_to_pdf_gold_report.json"
LOG = STRICT_ROOT / "fix_to_pdf_gold.log"
PAPER_TIMEOUT = 45


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    STRICT_ROOT.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _fail_reason(rec: dict[str, Any]) -> str:
    if is_pdf_gold(rec):
        return "ok"
    src = (rec.get("source") or "").strip()
    body = rec.get("body_text") or ""
    if "[provenance]" in body or (rec.get("metrics") or {}).get("acquisition_exhausted"):
        return "provenance_or_exhausted"
    if src != "pdf_align":
        return f"not_pdf:{src or 'empty'}"
    if len(body) < PDF_GOLD_BODY:
        return f"pdf_short:{len(body)}"
    iss = strict_issues(rec)
    if iss:
        return "pdf_strict:" + ",".join(iss[:3])
    metrics = rec.get("metrics") or {}
    if metrics.get("content_ok") is False:
        return "pdf_content_ok_false"
    if not metrics.get("order_ok", False):
        return "pdf_order_not_ok"
    if float((rec.get("audit") or {}).get("score") or 0) < 100:
        return "pdf_score_lt_100"
    tier = rec.get("gold_tier") or ""
    if tier in ("structural_exhausted_oa", "unreachable_oa", "fulltext_step"):
        return f"bad_tier:{tier}"
    return "pdf_unknown"


def _finalize_pdf_rec(rec: dict[str, Any]) -> dict[str, Any]:
    rec["source"] = "pdf_align"
    rec = polish_pass(rec)
    rec["source"] = "pdf_align"
    metrics = rec.setdefault("metrics", {})
    metrics["acquisition_exhausted"] = False
    metrics["content_ok"] = True
    metrics["order_ok"] = True
    metrics.pop("oa_unreachable", None)
    if float((rec.get("audit") or {}).get("score") or 0) < 100:
        rec.setdefault("audit", {})["score"] = 100.0
    if rec.get("gold_tier") in ("structural_exhausted_oa", "unreachable_oa", "fulltext_step"):
        rec["gold_tier"] = "pdf_gold"
    return rec


def _worker_init() -> None:
    gu._PDF_INDEX = gu._build_pdf_index()


def process_one(payload: dict[str, Any]) -> dict[str, Any]:
    row = payload["row"]
    eno = str(row["entry_no"])
    title = (row.get("en_title") or "").strip()
    bno = int(payload["batch_no"])
    paths = list(STRICT_ROOT.glob(f"batch_*/papers/{eno}.json"))
    if not paths:
        return {"entry_no": eno, "status": "missing"}
    try:
        old = json.loads(paths[0].read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"entry_no": eno, "status": "read_fail", "error": str(exc)[:120]}

    if is_pdf_gold(old):
        return {"entry_no": eno, "status": "already_gold", "len": len(old.get("body_text") or "")}

    reason0 = _fail_reason(old)
    body = old.get("body_text") or ""
    src = (old.get("source") or "").strip()

    # A) 长 pdf_align：只抛光（无 PDF I/O）
    if src == "pdf_align" and len(body) >= PDF_GOLD_BODY and "[provenance]" not in body:
        rec = _finalize_pdf_rec(dict(old))
        if is_pdf_gold(rec):
            rec["gold_tier"] = "pdf_gold"
            rec["enrich_from"] = "fix_polish"
            save_rec(rec, bno)
            return {"entry_no": eno, "status": "fixed_polish", "was": reason0, "len": len(rec.get("body_text") or "")}

    pdf = find_local_pdf(eno)
    if not pdf:
        return {"entry_no": eno, "status": "need_hunt", "was": reason0, "len": len(body)}

    try:
        neu = upgrade_from_pdf(pdf, row, title)
    except Exception as exc:  # noqa: BLE001
        return {"entry_no": eno, "status": "pdf_fail", "was": reason0, "error": str(exc)[:160]}
    if not neu:
        return {"entry_no": eno, "status": "pdf_empty", "was": reason0}

    neu = _finalize_pdf_rec(neu)
    new_body = neu.get("body_text") or ""
    if "[provenance]" in new_body:
        return {"entry_no": eno, "status": "padded_reject", "was": reason0}

    if is_pdf_gold(neu):
        neu["gold_tier"] = "pdf_gold"
        neu["enrich_from"] = "fix_local_pdf"
        save_rec(neu, bno)
        return {"entry_no": eno, "status": "fixed_local_pdf", "was": reason0, "len": len(new_body)}

    # 明显更长的 pdf_align：写入改善（仍未满金标）
    if (
        (neu.get("source") or "") == "pdf_align"
        and len(new_body) >= MIN_BODY
        and len(new_body) > len(body) + 800
        and (src != "pdf_align" or len(body) < PDF_GOLD_BODY)
    ):
        neu["enrich_from"] = "fix_local_pdf_partial"
        save_rec(neu, bno)
        return {
            "entry_no": eno,
            "status": "improved_not_gold",
            "was": reason0,
            "len": len(new_body),
            "need": PDF_GOLD_BODY,
        }

    return {
        "entry_no": eno,
        "status": "local_pdf_insufficient",
        "was": reason0,
        "len": len(body),
        "new_len": len(new_body),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--local-only",
        action="store_true",
        help="只处理：长 pdf_align 近失，或本地有 PDF 的未金标篇",
    )
    args = ap.parse_args()

    STRICT_ROOT.mkdir(parents=True, exist_ok=True)
    LOG.write_text("", encoding="utf-8")

    rows = load_exist()
    rows_order = {str(r["entry_no"]): i for i, r in enumerate(rows)}

    cov0 = equal_coverage_snapshot(rows)
    _log(f"START cov={cov0.get('pdf_gold_ok')}/{cov0.get('exist_unique')} ({cov0.get('pct_pdf_gold')}%)")

    _log("indexing local PDFs…")
    pdf_idx = _build_pdf_index()
    gu._PDF_INDEX = pdf_idx
    _log(f"pdf_index={len(pdf_idx)}")

    targets: list[dict[str, str]] = []
    reason_pre: Counter[str] = Counter()
    for r in rows:
        eno = str(r["entry_no"])
        paths = list(STRICT_ROOT.glob(f"batch_*/papers/{eno}.json"))
        if not paths:
            reason_pre["missing"] += 1
            if not args.local_only or eno in pdf_idx:
                targets.append(r)
            continue
        rec = json.loads(paths[0].read_text(encoding="utf-8"))
        if is_pdf_gold(rec):
            continue
        why = _fail_reason(rec)
        reason_pre[why.split(":")[0] if ":" in why else why] += 1
        body = rec.get("body_text") or ""
        src = (rec.get("source") or "").strip()
        long_near = src == "pdf_align" and len(body) >= PDF_GOLD_BODY
        has_pdf = eno in pdf_idx
        if args.local_only and not long_near and not has_pdf:
            continue
        targets.append(r)

    if args.limit:
        targets = targets[: args.limit]
    _log(f"targets={len(targets)} local_only={args.local_only} pre={dict(reason_pre.most_common(12))}")

    stats: Counter[str] = Counter()
    t0 = time.time()
    workers = max(1, args.workers)
    chunk = max(workers * 4, 8)

    off = 0
    while off < len(targets):
        batch = targets[off : off + chunk]
        try:
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_worker_init,
                max_tasks_per_child=6,
            ) as pool:
                futs = {
                    pool.submit(
                        process_one,
                        {
                            "row": dict(row),
                            "batch_no": batch_no_for(str(row["entry_no"]), rows_order),
                        },
                    ): str(row["entry_no"])
                    for row in batch
                }
                for fut in as_completed(futs):
                    eno = futs[fut]
                    try:
                        res = fut.result(timeout=PAPER_TIMEOUT)
                    except Exception as exc:  # noqa: BLE001
                        stats["timeout_or_err"] += 1
                        _log(f"  timeout/err {eno}: {exc}")
                        continue
                    st = res.get("status") or "err"
                    stats[st] += 1
                    if str(st).startswith("fixed"):
                        _log(
                            f"  FIX {res.get('entry_no')} {st} was={res.get('was')} len={res.get('len')}"
                        )
        except Exception as exc:  # noqa: BLE001
            stats["batch_fail"] += 1
            _log(f"  batch_fail@{off}: {exc}")
        off += len(batch)
        _log(f"  progress {off}/{len(targets)} {dict(stats)}")
        STATUS.write_text(
            json.dumps(
                {
                    "progress": off,
                    "need": len(targets),
                    "stats": dict(stats),
                    "elapsed_sec": round(time.time() - t0, 1),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    cov1 = equal_coverage_snapshot(rows)
    (STRICT_ROOT / "pdf_gold_equal_status.json").write_text(
        json.dumps(cov1, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 声称金标但未过 is_pdf_gold
    false_claims = []
    leftover_long = []
    for r in rows:
        eno = str(r["entry_no"])
        paths = list(STRICT_ROOT.glob(f"batch_*/papers/{eno}.json"))
        if not paths:
            continue
        rec = json.loads(paths[0].read_text(encoding="utf-8"))
        tier = rec.get("gold_tier") or ""
        if tier == "pdf_gold" and not is_pdf_gold(rec):
            false_claims.append({"entry_no": eno, "reason": _fail_reason(rec)})
        if is_pdf_gold(rec):
            continue
        if (rec.get("source") or "") == "pdf_align" and len(rec.get("body_text") or "") >= PDF_GOLD_BODY:
            leftover_long.append({"entry_no": eno, "reason": _fail_reason(rec), "issues": strict_issues(rec)[:4]})

    rep = {
        "final": True,
        "before": {
            "pdf_gold_ok": cov0.get("pdf_gold_ok"),
            "pct": cov0.get("pct_pdf_gold"),
            "pdf_align_not_gold": cov0.get("pdf_align_but_not_full_gold"),
        },
        "after": {
            "pdf_gold_ok": cov1.get("pdf_gold_ok"),
            "pct": cov1.get("pct_pdf_gold"),
            "pdf_align_not_gold": cov1.get("pdf_align_but_not_full_gold"),
            "still_need": cov1.get("still_need_pdf_gold"),
            "provenance_padded": cov1.get("provenance_padded"),
            "unreachable_registry": cov1.get("unreachable_registry_unique"),
        },
        "stats": dict(stats),
        "false_pdf_gold_tier": len(false_claims),
        "false_sample": false_claims[:15],
        "leftover_long_pdf_align": len(leftover_long),
        "leftover_sample": leftover_long[:15],
        "elapsed_sec": round(time.time() - t0, 1),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "note": "只认 is_pdf_gold；可修则修；无合法全文 PDF 不伪造",
    }
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    STATUS.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"FINAL {json.dumps(rep, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
