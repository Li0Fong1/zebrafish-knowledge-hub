# -*- coding: utf-8 -*-
"""批量回填 literature_paper.journal（本地 XML → Crossref → PubMed API）。

用法:
  python scripts/backfill_literature_journals.py --limit 200
  python scripts/backfill_literature_journals.py --limit 0   # 全库
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "rag", ROOT / "database", ROOT / "llm"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

try:
    from entry import bootstrap_app

    bootstrap_app()
except Exception:
    pass

from journal_enrich import resolve_journal  # noqa: E402
from mysql_client import get_mysql_client  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500, help="0=全部缺刊名行")
    ap.add_argument("--sleep", type=float, default=0.05, help="每条额外间隔")
    ap.add_argument("--network", action="store_true", default=True)
    ap.add_argument("--no-network", action="store_true")
    args = ap.parse_args()
    allow_net = not args.no_network

    client = get_mysql_client()
    lim = "" if args.limit <= 0 else f" LIMIT {int(args.limit)}"
    sql = (
        "SELECT doc_id, pmid, entry_no, doi, title_en, title_zh, year, journal "
        "FROM literature_paper WHERE journal IS NULL OR TRIM(journal)=''"
        f" ORDER BY pmid IS NULL, pmid{lim}"
    )
    rows = client.execute_sql(sql, max_rows=0 if args.limit <= 0 else max(args.limit, 1))
    data = (rows or {}).get("data") or []
    print(f"TODO {len(data)} allow_network={allow_net}", flush=True)
    ok = fail = fallback = 0
    t0 = time.time()
    for i, r in enumerate(data, 1):
        title = str(r.get("title_en") or r.get("title_zh") or "")
        j = resolve_journal(
            journal="",
            pmid=str(r.get("pmid") or ""),
            doi=str(r.get("doi") or ""),
            title=title,
            year=str(r.get("year") or ""),
            entry_no=str(r.get("entry_no") or ""),
            doc_id=str(r.get("doc_id") or ""),
            allow_network=allow_net,
            persist=True,
        )
        if j == "Unlisted journal":
            fallback += 1
        elif j:
            ok += 1
        else:
            fail += 1
        if i % 20 == 0 or i == len(data):
            print(
                f"[{i}/{len(data)}] ok={ok} fallback={fallback} fail={fail} "
                f"elapsed={time.time()-t0:.1f}s last={j[:40]}",
                flush=True,
            )
        if args.sleep > 0:
            time.sleep(args.sleep)
    print(f"DONE ok={ok} fallback={fallback} fail={fail}", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
