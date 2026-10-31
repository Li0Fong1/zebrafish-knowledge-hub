# -*- coding: utf-8 -*-
"""只填 literature_paper.year 空值：PubMed XML / WoS RIS / 金标「来源信息」。不覆盖已有年。"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "database"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from core.entry import bootstrap_app

bootstrap_app()

from sqlalchemy import text

from mysql_client import get_mysql_client

YEAR_OK = re.compile(r"^(19|20)\d{2}$")
XML_YEAR = re.compile(r"<Year>\s*((?:19|20)\d{2})\s*</Year>", re.I)
XML_MED = re.compile(r"<MedlineDate>\s*((?:19|20)\d{2})", re.I)
RIS_PY = re.compile(r"(?m)^PY  - ((?:19|20)\d{2})")
SRC_YEAR = re.compile(r"(?:19|20)\d{2}")
DOI_YEAR_PATS = [
    re.compile(r"-(19\d{2}|20\d{2})-"),
    re.compile(r"\.(19\d{2}|20\d{2})\."),
    re.compile(r"/(19\d{2}|20\d{2})/"),
]


def year_from_doi(doi: str) -> int | None:
    d = (doi or "").strip()
    if not d:
        return None
    for pat in DOI_YEAR_PATS:
        m = pat.search(d)
        if m:
            y = int(m.group(1))
            if _ok(y):
                return y
    return None


def _ok(y: int) -> bool:
    from datetime import datetime

    return 1980 <= y <= datetime.now().year + 1


def _parse_xml(blob: str) -> int | None:
    m = XML_YEAR.search(blob or "")
    if not m:
        m = XML_MED.search(blob or "")
    if not m:
        return None
    y = int(m.group(1))
    return y if _ok(y) else None


def _parse_ris(blob: str) -> int | None:
    m = RIS_PY.search(blob or "")
    if not m:
        return None
    y = int(m.group(1))
    return y if _ok(y) else None


def _parse_source_line(text: str) -> int | None:
    for line in (text or "").splitlines()[:24]:
        if "来源信息" not in line:
            continue
        for m in SRC_YEAR.finditer(line):
            y = int(m.group(0))
            if _ok(y):
                return y
    return None


def _flush(db, rows: list[tuple[int, str]], by: str) -> int:
    if not rows:
        return 0
    n = 0
    for y, key in rows:
        if by == "pmid":
            r = db.execute(
                text("UPDATE literature_paper SET year=:y WHERE pmid=:k AND year IS NULL"),
                {"y": y, "k": key},
            )
        else:
            r = db.execute(
                text("UPDATE literature_paper SET year=:y WHERE entry_no=:k AND year IS NULL"),
                {"y": y, "k": key},
            )
        n += r.rowcount or 0
    db.commit()
    return n


def main() -> None:
    client = get_mysql_client()
    db = next(client.get_db())
    stats = {"pubmed": 0, "wos": 0, "gold": 0, "doi": 0}
    only = (os.getenv("FILL_ONLY") or "all").strip().lower()
    try:
        if only in ("all", "pubmed"):
            print("== pubmed xml ==", flush=True)
            batch: list[tuple[int, str]] = []
            last = ""
            while True:
                rows = db.execute(
                    text(
                        "SELECT pmid, LEFT(raw_xml, 8000) FROM literature_pubmed "
                        "WHERE pmid > :last ORDER BY pmid LIMIT 400"
                    ),
                    {"last": last},
                ).fetchall()
                if not rows:
                    break
                last = str(rows[-1][0])
                for pmid, xml in rows:
                    y = _parse_xml(xml or "")
                    if y and str(pmid).isdigit():
                        batch.append((y, str(pmid)))
                if len(batch) >= 200:
                    stats["pubmed"] += _flush(db, batch, "pmid")
                    print(f" pubmed_updated={stats['pubmed']} last={last}", flush=True)
                    batch = []
            stats["pubmed"] += _flush(db, batch, "pmid")

        if only in ("all", "wos"):
            print("== wos ris ==", flush=True)
            batch = []
            rows = db.execute(
                text(
                    "SELECT pmid, LEFT(raw_ris, 4000) FROM literature_wos "
                    "WHERE pmid IS NOT NULL AND pmid<>''"
                )
            ).fetchall()
            for pmid, ris in rows:
                y = _parse_ris(ris or "")
                if y and str(pmid).isdigit():
                    batch.append((y, str(pmid)))
            stats["wos"] += _flush(db, batch, "pmid")

        if only in ("all", "gold"):
            print("== gold json ==", flush=True)
            gold_rows = db.execute(
                text(
                    "SELECT p.entry_no, g.body_path FROM literature_paper p "
                    "JOIN literature_fulltext_gold g ON g.doc_id=p.doc_id "
                    "WHERE p.year IS NULL AND p.entry_no IS NOT NULL AND p.entry_no<>'' "
                    "AND g.body_path IS NOT NULL AND g.body_path<>''"
                )
            ).fetchall()
            batch = []
            seen = 0
            for eno, path in gold_rows:
                seen += 1
                p = Path(str(path))
                if not p.is_file():
                    continue
                try:
                    obj = json.loads(p.read_text(encoding="utf-8", errors="replace")[:2_000_000])
                except Exception:
                    continue
                y = None
                raw_y = obj.get("year") or obj.get("pub_year")
                if raw_y not in (None, ""):
                    try:
                        yi = int(str(raw_y)[:4])
                        if _ok(yi):
                            y = yi
                    except (TypeError, ValueError):
                        pass
                y = y or year_from_doi(str(obj.get("doi") or ""))
                y = y or _parse_source_line(str(obj.get("body_text") or "")[:2500])
                if y:
                    batch.append((y, str(eno)))
                if len(batch) >= 200:
                    stats["gold"] += _flush(db, batch, "eno")
                    print(f" gold_updated={stats['gold']} seen={seen}", flush=True)
                    batch = []
            stats["gold"] += _flush(db, batch, "eno")

        if only in ("all", "doi"):
            print("== doi heuristic ==", flush=True)
            batch = []
            doi_rows = db.execute(
                text(
                    "SELECT entry_no, doi FROM literature_paper "
                    "WHERE year IS NULL AND doi IS NOT NULL AND doi<>''"
                )
            ).fetchall()
            for eno, doi in doi_rows:
                y = year_from_doi(str(doi))
                if y and eno:
                    batch.append((y, str(eno)))
                if len(batch) >= 200:
                    stats["doi"] += _flush(db, batch, "eno")
                    print(f" doi_updated={stats['doi']}", flush=True)
                    batch = []
            stats["doi"] += _flush(db, batch, "eno")

        left = db.execute(
            text("SELECT SUM(year IS NOT NULL), SUM(year IS NULL), COUNT(*) FROM literature_paper")
        ).fetchone()
        print("STATS", stats, "paper_year_notnull/null/total", tuple(left), flush=True)
    finally:
        db.close()


if __name__ == "__main__":
    main()
