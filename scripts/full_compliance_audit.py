# -*- coding: utf-8 -*-
"""
全量解析 + 向量化达标审计（服务器）。

输出：
  logs/scheduler/full_compliance_report.json
  logs/scheduler/non_compliant_parse.jsonl
  logs/scheduler/non_compliant_vectorize.jsonl
  logs/scheduler/non_compliant_ledger.jsonl
  logs/scheduler/non_compliant_db.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
BASE = Path(os.environ.get("ZEBRA_BASE", str(ROOT)))
LOG_DIR = RUNTIME / "logs" / "scheduler"
REPORT = LOG_DIR / "full_compliance_report.json"
NC_PARSE = LOG_DIR / "non_compliant_parse.jsonl"
NC_VEC = LOG_DIR / "non_compliant_vectorize.jsonl"
NC_LEDGER = LOG_DIR / "non_compliant_ledger.jsonl"
NC_DB = LOG_DIR / "non_compliant_db.jsonl"
FLOOR = 35487


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def mysql_query(sql: str) -> str:
    binp = str(RUNTIME / "mysql80" / "bin" / "mysql")
    sock = str(RUNTIME / "mysql.sock")
    args = [binp, f"--socket={sock}", "--protocol=SOCKET", "-uzebrafish", "-pzebrafish_remote_2026", "zebrafish_db", "-N", "-B"]
    p = subprocess.run(args, input=sql + "\n", capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        args = [binp, "-h127.0.0.1", "-P3307", "-uzebrafish", "-pzebrafish_remote_2026", "zebrafish_db", "-N", "-B"]
        p = subprocess.run(args, input=sql + "\n", capture_output=True, text=True, timeout=120)
    lines = [ln for ln in ((p.stdout or "") + (p.stderr or "")).splitlines() if "Using a password" not in ln and "Warning" not in ln]
    return "\n".join(lines).strip()


def run_audit(*, limit_sample: int = 0) -> dict[str, Any]:
    os.environ.setdefault("ZEBRA_RUNTIME", str(RUNTIME))
    os.environ.setdefault("ZEBRA_BASE", str(BASE))
    os.chdir(BASE)
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    from core.entry import bootstrap_app

    bootstrap_app()
    from scripts.gold_hunt_fulltext import is_pdf_gold, strict_issues
    from scripts.scihub_post_gold_pipeline import (
        DONE_IDS,
        FAISS_DIR,
        GTE_DIR,
        UNREACHABLE,
        _load_json,
        find_inbox_pdf,
        load_rec,
        parse_log_rows,
    )
    from scripts.three_day_closeout import gold_json_path, inbox_pdfs, load_unreachable_ids, universe_enos

    rows = universe_enos()
    done_vec = set(str(x) for x in _load_json(DONE_IDS, []))
    un_ids = load_unreachable_ids()

    gold_enos: list[str] = []
    not_enos: list[str] = []
    nc_parse: list[dict[str, Any]] = []
    nc_vec: list[dict[str, Any]] = []
    nc_ledger: list[dict[str, Any]] = []
    nc_db: list[dict[str, Any]] = []

    for eno, row in rows.items():
        rec = load_rec(eno)
        log_gold = bool(row.get("gold"))
        file_exists = gold_json_path(eno).is_file()
        is_gold = bool(rec and is_pdf_gold(rec))

        if is_gold:
            gold_enos.append(eno)
            if eno not in done_vec:
                nc_vec.append({
                    "entry_no": eno,
                    "issue": "gold_not_vectorized",
                    "body_len": len(rec.get("body_text") or ""),
                    "source": rec.get("source"),
                    "json_path": str(gold_json_path(eno)),
                })
            continue

        not_enos.append(eno)
        body_len = int(row.get("body_len") or 0)
        if rec:
            body_len = max(body_len, len(rec.get("body_text") or ""))

        if log_gold and not is_gold:
            issues = strict_issues(rec) if rec else ["no_json"]
            nc_parse.append({
                "entry_no": eno,
                "issue": "log_claimed_gold_but_fails_is_pdf_gold",
                "log_status": row.get("status"),
                "body_len": body_len,
                "strict_issues": issues[:8] if isinstance(issues, list) else str(issues),
                "json_path": str(gold_json_path(eno)) if file_exists else "",
            })
        elif file_exists and rec and not is_gold:
            nc_parse.append({
                "entry_no": eno,
                "issue": "json_exists_not_gold",
                "log_status": row.get("status"),
                "body_len": body_len,
                "floor": FLOOR,
                "gap": FLOOR - body_len if body_len < FLOOR else 0,
                "strict_issues": (strict_issues(rec) or [])[:8],
                "source": rec.get("source"),
            })
        elif not file_exists:
            nc_parse.append({
                "entry_no": eno,
                "issue": "no_gold_json",
                "log_status": row.get("status"),
                "body_len": body_len,
                "pdf": str(find_inbox_pdf(eno) or row.get("pdf") or ""),
            })

        if eno not in un_ids:
            nc_ledger.append({
                "entry_no": eno,
                "issue": "not_gold_missing_unreachable_ledger",
                "body_len": body_len,
                "log_status": row.get("status"),
                "pdf": str(find_inbox_pdf(eno) or row.get("pdf") or ""),
            })

    # DB check for all gold (batched)
    missing_db: list[str] = []
    for i in range(0, len(gold_enos), 200):
        chunk = gold_enos[i : i + 200]
        if not chunk:
            continue
        ins = ",".join("'" + e.replace("'", "") + "'" for e in chunk)
        got = mysql_query(
            f"SELECT entry_no FROM literature_paper WHERE gold_status='ready' AND entry_no IN ({ins})"
        )
        have = {ln.strip() for ln in got.splitlines() if ln.strip()}
        for e in chunk:
            if e not in have:
                missing_db.append(e)
                nc_db.append({
                    "entry_no": e,
                    "issue": "gold_missing_db_ready",
                    "json_path": str(gold_json_path(e)),
                })

    # FAISS manifest
    mf: dict[str, Any] = {}
    if (FAISS_DIR / "manifest.json").is_file():
        mf = json.loads((FAISS_DIR / "manifest.json").read_text(encoding="utf-8"))

    # unreachable stats
    un_reasons = Counter()
    un_campaigns = Counter()
    un_in_universe = 0
    if UNREACHABLE.is_file():
        with UNREACHABLE.open(encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                rid = str(o.get("id") or o.get("entry_no") or "")
                if rid in rows:
                    un_in_universe += 1
                un_reasons[str(o.get("reason") or "unknown")[:80]] += 1
                un_campaigns[str(o.get("campaign") or "unknown")] += 1

    inbox_n = len(inbox_pdfs())
    scihub_log_n = len(parse_log_rows())

    # task progress
    near_floor = sum(
        1 for e in not_enos
        if int((rows.get(e) or {}).get("body_len") or 0) >= 20000
        or (load_rec(e) and len((load_rec(e) or {}).get("body_text") or "") >= 20000)
    )
    far_short = len(not_enos) - near_floor

    inbox_missing_json = 0
    for pdf in inbox_pdfs():
        import re as _re
        m = _re.match(r"^(\d+)", pdf.stem)
        eno = m.group(1) if m else pdf.stem
        if not gold_json_path(eno).is_file():
            inbox_missing_json += 1

    checks = {
        "universe_logged": len(rows) >= 13841,
        "gold_all_vectorized": len(nc_vec) == 0,
        "not_gold_all_ledgered": len(nc_ledger) == 0,
        "gold_all_db_ready": len(missing_db) == 0,
        "faiss_ready": mf.get("status") == "ready",
        "faiss_dim_2048": int(mf.get("embedding_dim") or 0) == 2048,
        "gte_preserved": (GTE_DIR / "manifest.json").is_file(),
        "inbox_all_have_json": inbox_missing_json == 0,
    }

    report = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "pass": all(checks.values()),
        "checks": checks,
        "summary": {
            "universe_total": len(rows),
            "scihub_log_rows": scihub_log_n,
            "inbox_pdf_remaining": inbox_n,
            "inbox_missing_json": inbox_missing_json,
            "gold_total": len(gold_enos),
            "not_gold_total": len(not_enos),
            "gold_vectorized": len(gold_enos) - len(nc_vec),
            "gold_pending_vectorize": len(nc_vec),
            "not_gold_ledgered_in_universe": len(not_enos) - len(nc_ledger),
            "not_gold_missing_ledger": len(nc_ledger),
            "not_gold_near_floor_20k": near_floor,
            "not_gold_far_short_lt_20k": far_short,
            "non_compliant_parse_records": len(nc_parse),
            "non_compliant_db_records": len(nc_db),
            "unreachable_lines_total": sum(1 for _ in UNREACHABLE.open()) if UNREACHABLE.is_file() else 0,
            "unreachable_in_universe": un_in_universe,
        },
        "faiss": {
            "path": str(FAISS_DIR),
            "status": mf.get("status"),
            "total_papers": mf.get("total_papers"),
            "total_chunks": mf.get("total_chunks"),
            "shards": len(mf.get("shards") or []),
            "embedding_dim": mf.get("embedding_dim"),
            "index_version": mf.get("index_version"),
        },
        "mysql": {
            "paper_ready": mysql_query("SELECT COUNT(*) FROM literature_paper WHERE gold_status='ready'"),
            "gold_body_path": mysql_query(
                "SELECT SUM(IFNULL(body_path,'')!=''), COUNT(*) FROM literature_fulltext_gold"
            ),
            "campaign_scihub": mysql_query(
                "SELECT COUNT(*) FROM literature_fulltext_gold WHERE campaign_id='scihub_13841_post_gold'"
            ),
        },
        "unreachable_top_reasons": dict(un_reasons.most_common(12)),
        "unreachable_campaigns": dict(un_campaigns.most_common(8)),
        "non_compliant_counts": {
            "parse": len(nc_parse),
            "vectorize": len(nc_vec),
            "ledger": len(nc_ledger),
            "db": len(nc_db),
        },
        "samples": {
            "pending_vec": [r["entry_no"] for r in nc_vec[:15]],
            "missing_ledger": [r["entry_no"] for r in nc_ledger[:15]],
            "parse_issues": nc_parse[:15],
            "missing_db": missing_db[:15],
        },
        "output_files": {
            "report": str(REPORT),
            "non_compliant_parse": str(NC_PARSE),
            "non_compliant_vectorize": str(NC_VEC),
            "non_compliant_ledger": str(NC_LEDGER),
            "non_compliant_db": str(NC_DB),
        },
    }

    if limit_sample:
        nc_parse = nc_parse[:limit_sample]
        nc_vec = nc_vec[:limit_sample]
        nc_ledger = nc_ledger[:limit_sample]
        nc_db = nc_db[:limit_sample]

    _write_jsonl(NC_PARSE, nc_parse)
    _write_jsonl(NC_VEC, nc_vec)
    _write_jsonl(NC_LEDGER, nc_ledger)
    _write_jsonl(NC_DB, nc_db)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"pass": report["pass"], "checks": checks, "summary": report["summary"]}, ensure_ascii=False))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-sample", type=int, default=0)
    args = ap.parse_args()
    rep = run_audit(limit_sample=args.limit_sample)
    return 0 if rep.get("pass") else 2


if __name__ == "__main__":
    raise SystemExit(main())
