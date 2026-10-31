# -*- coding: utf-8 -*-
"""Crossref 补 literature_paper.year（仅 year IS NULL 且有 DOI）。断点续传。"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "database"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from core.entry import bootstrap_app

bootstrap_app()

from sqlalchemy import text

from mysql_client import get_mysql_client

RT = Path(os.getenv("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
STATE = RT / "logs" / "_crossref_year_state.jsonl"
LOG = RT / "logs" / "_crossref_year.log"
WORKERS = int(os.getenv("CROSSREF_WORKERS", "8"))
LIMIT = int(os.getenv("CROSSREF_YEAR_LIMIT", "0"))  # 0=all


def _ok(y: int) -> bool:
    from datetime import datetime

    return 1980 <= y <= datetime.now().year + 1


def fetch_year(doi: str) -> int | None:
    url = "https://api.crossref.org/works/" + quote(doi)
    req = Request(
        url,
        headers={"User-Agent": "ZebrafishGeneQA/1.0 (mailto:zebrafish@local.dev)"},
    )
    for _ in range(3):
        try:
            with urlopen(req, timeout=25) as r:
                msg = json.loads(r.read().decode("utf-8")).get("message") or {}
            y = None
            for key in ("published-print", "published-online", "created", "issued"):
                parts = ((msg.get(key) or {}).get("date-parts") or [[]])[0]
                if parts:
                    try:
                        yi = int(parts[0])
                        if _ok(yi):
                            y = yi
                            break
                    except (TypeError, ValueError):
                        pass
            return y
        except Exception as e:
            s = str(e)
            if "404" in s or "Not Found" in s:
                return None
            time.sleep(1.2 + random.random())
    return None


def load_done() -> set[str]:
    done: set[str] = set()
    if not STATE.is_file():
        return done
    with STATE.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                d = (rec.get("doi") or "").strip()
                if d:
                    done.add(d)
            except Exception:
                continue
    return done


def main() -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    client = get_mysql_client()
    db = next(client.get_db())
    try:
        rows = db.execute(
            text(
                "SELECT entry_no, doi FROM literature_paper "
                "WHERE year IS NULL AND doi IS NOT NULL AND doi<>'' "
                "AND entry_no IS NOT NULL AND entry_no<>''"
            )
        ).fetchall()
    finally:
        db.close()

    done = load_done()
    todo = [(str(eno), str(doi).strip()) for eno, doi in rows if str(doi).strip() not in done]
    if LIMIT > 0:
        todo = todo[:LIMIT]
    print(f"todo={len(todo)} already={len(done)} workers={WORKERS}", flush=True)

    ok = fail = 0
    batch: list[tuple[int, str]] = []

    def flush_batch():
        nonlocal batch
        if not batch:
            return
        db2 = next(get_mysql_client().get_db())
        try:
            for y, eno in batch:
                db2.execute(
                    text(
                        "UPDATE literature_paper SET year=:y "
                        "WHERE entry_no=:k AND year IS NULL"
                    ),
                    {"y": y, "k": eno},
                )
            db2.commit()
        finally:
            db2.close()
        batch = []

    with STATE.open("a", encoding="utf-8") as sf, ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(fetch_year, doi): (eno, doi) for eno, doi in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            eno, doi = futs[fut]
            try:
                y = fut.result()
            except Exception:
                y = None
            sf.write(json.dumps({"entry_no": eno, "doi": doi, "year": y}, ensure_ascii=False) + "\n")
            sf.flush()
            if y:
                batch.append((y, eno))
                ok += 1
            else:
                fail += 1
            if len(batch) >= 100:
                flush_batch()
            if i % 200 == 0 or i == len(todo):
                print(f" progress {i}/{len(todo)} ok={ok} fail={fail}", flush=True)
                with LOG.open("a", encoding="utf-8") as lf:
                    lf.write(f"{i}/{len(todo)} ok={ok} fail={fail}\n")
    flush_batch()

    db = next(get_mysql_client().get_db())
    try:
        left = db.execute(
            text(
                "SELECT SUM(year IS NOT NULL), SUM(year IS NULL), COUNT(*), "
                "SUM(year IS NULL AND doi IS NOT NULL AND doi<>'') "
                "FROM literature_paper"
            )
        ).fetchone()
    finally:
        db.close()
    print("DONE", {"ok": ok, "fail": fail, "paper_year_notnull/null/total/null_with_doi": tuple(left)}, flush=True)


if __name__ == "__main__":
    main()
