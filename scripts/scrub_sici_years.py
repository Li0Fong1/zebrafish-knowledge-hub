# -*- coding: utf-8 -*-
"""Fix remaining years: SICI (YYYYMMDD), BiochemJ DOI, Hindawi, gold bodies."""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
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

NOW = datetime.now().year
UA = "ZebrafishGeneQA/1.0 (mailto:zf@local)"


def ok(y: int) -> bool:
    return 1980 <= y <= NOW + 1


def year_from_doi_text(doi: str) -> int | None:
    d = doi or ""
    for pat in (
        re.compile(r"\(((?:19|20)\d{2})\d{0,4}\)"),  # SICI (19990927) or (1999)
        re.compile(r"-(19\d{2}|20\d{2})-"),
        re.compile(r"\.(19\d{2}|20\d{2})\."),
        re.compile(r"/(19\d{2}|20\d{2})/"),
        re.compile(r"_(19\d{2}|20\d{2})_"),
    ):
        m = pat.search(d)
        if m and ok(int(m.group(1))):
            return int(m.group(1))
    return None


def _get_json(url: str):
    req = Request(url, headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def year_crossref(doi: str) -> int | None:
    js = _get_json("https://api.crossref.org/works/" + quote(doi))
    if not js:
        return None
    msg = js.get("message") or {}
    for key in ("published-print", "published-online", "issued", "created"):
        parts = ((msg.get(key) or {}).get("date-parts") or [[]])[0]
        if parts and ok(int(parts[0])):
            return int(parts[0])
    return None


def year_openalex_doi(doi: str) -> int | None:
    js = _get_json("https://api.openalex.org/works/https://doi.org/" + quote(doi))
    if not js:
        return None
    y = js.get("publication_year")
    return int(y) if y and ok(int(y)) else None


def year_openalex_title(title: str) -> int | None:
    js = _get_json(
        "https://api.openalex.org/works?search=" + quote(title[:120]) + "&per-page=1"
    )
    if not js:
        return None
    hits = js.get("results") or []
    if not hits:
        return None
    y = hits[0].get("publication_year")
    return int(y) if y and ok(int(y)) else None


def norm_candidates(doi: str) -> list[str]:
    d = (doi or "").strip()
    out = [d]
    # Hindawi sci5_ → often 10.1155/YYYY/id unknown; try slash
    m = re.match(r"^(10\.1155/)([a-z0-9]+)_(\d+)$", d, re.I)
    if m:
        out.append(f"{m.group(1)}{m.group(2)}/{m.group(3)}")
    # Biochem J: 10.1042/0264-6021/3600039 → 10.1042/bj3600039
    m = re.match(r"^10\.1042/0264-6021/(\d+)$", d)
    if m:
        out.append(f"10.1042/bj{m.group(1)}")
        out.append(f"10.1042/{m.group(1)}")
    # Wiley SICI: extract clean DOI sometimes fails; year from text enough
    # AJPH old volume DOI — skip unless crossref
    seen = set()
    ordered = []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            ordered.append(x)
    return ordered


def main() -> None:
    db = next(get_mysql_client().get_db())
    rows = db.execute(
        text(
            "SELECT entry_no, IFNULL(doi,''), IFNULL(title_en,''), doc_id "
            "FROM literature_paper WHERE year IS NULL"
        )
    ).fetchall()
    print("left", len(rows), flush=True)
    fixed = 0
    for eno, doi, title, doc_id in rows:
        y = year_from_doi_text(doi) if doi else None
        if not y and doi:
            for cand in norm_candidates(doi):
                y = year_from_doi_text(cand) or year_crossref(cand) or year_openalex_doi(cand)
                if y:
                    break
                time.sleep(0.05)
        if not y and title and not title.startswith("DOI ") and not title.startswith("Entry "):
            y = year_openalex_title(title)
        if not y:
            # gold body
            path_row = db.execute(
                text(
                    "SELECT body_path FROM literature_fulltext_gold WHERE doc_id=:d LIMIT 1"
                ),
                {"d": doc_id},
            ).fetchone()
            blob = title or ""
            if path_row and path_row[0]:
                try:
                    blob += "\n" + Path(str(path_row[0])).read_text(
                        encoding="utf-8", errors="replace"
                    )[:12000]
                except Exception:
                    pass
            m = re.search(
                r"(?:Copyright|Published|©|First Online|Received)\s*((?:19|20)\d{2})",
                blob,
                re.I,
            )
            if m and ok(int(m.group(1))):
                y = int(m.group(1))
            if not y:
                m = re.search(r"10\.\d{4,9}/[^\s\"'<>]+", blob)
                if m:
                    y = year_from_doi_text(m.group(0)) or year_crossref(
                        m.group(0).rstrip(".,;)")
                    )
        if y:
            db.execute(
                text("UPDATE literature_paper SET year=:y WHERE entry_no=:e AND year IS NULL"),
                {"y": y, "e": str(eno)},
            )
            fixed += 1
            print("fixed", eno, y, (doi or title)[:60], flush=True)
        else:
            print("fail", eno, doi or title, flush=True)
    db.commit()
    left = db.execute(
        text(
            "SELECT SUM(year IS NOT NULL), SUM(year IS NULL), COUNT(*), "
            "SUM(year IS NULL AND doi IS NOT NULL AND doi<>''), "
            "SUM(year IS NULL AND pmid IS NOT NULL AND pmid<>'') "
            "FROM literature_paper"
        )
    ).fetchone()
    residual = db.execute(
        text(
            "SELECT IFNULL(pmid,''), IFNULL(entry_no,''), IFNULL(doi,''), "
            "LEFT(IFNULL(title_en,''), 100) FROM literature_paper WHERE year IS NULL"
        )
    ).fetchall()
    out = {
        "fixed": fixed,
        "stats": {
            "with_year": int(left[0] or 0),
            "null_year": int(left[1] or 0),
            "total": int(left[2] or 0),
            "null_with_doi": int(left[3] or 0),
            "null_with_pmid": int(left[4] or 0),
        },
        "rows": [
            {"pmid": a, "entry_no": b, "doi": c, "title": d} for a, b, c, d in residual
        ],
    }
    path = ROOT / "logs" / "_year_residual_final.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE", out["stats"], flush=True)
    db.close()


if __name__ == "__main__":
    main()
