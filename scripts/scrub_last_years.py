# -*- coding: utf-8 -*-
"""Last-mile year fill for irreducible-looking residuals."""
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


def ok(y: int, loose: bool = False) -> bool:
    lo = 1950 if loose else 1980
    return lo <= y <= NOW + 1


def _get_json(url: str):
    req = Request(url, headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def year_crossref(doi: str, loose=False):
    js = _get_json("https://api.crossref.org/works/" + quote(doi))
    if not js:
        return None
    msg = js.get("message") or {}
    for key in ("published-print", "published-online", "issued", "created"):
        parts = ((msg.get(key) or {}).get("date-parts") or [[]])[0]
        if parts and ok(int(parts[0]), loose=loose):
            return int(parts[0])
    return None


def year_openalex(doi=None, title=None):
    if doi:
        js = _get_json("https://api.openalex.org/works/https://doi.org/" + quote(doi))
        if js and js.get("publication_year") and ok(int(js["publication_year"]), loose=True):
            return int(js["publication_year"])
    if title:
        js = _get_json(
            "https://api.openalex.org/works?search=" + quote(title[:140]) + "&per-page=3"
        )
        for h in (js or {}).get("results") or []:
            y = h.get("publication_year")
            if y and ok(int(y), loose=True):
                return int(y)
    return None


def year_epmc_title(title: str):
    q = f'TITLE:"{title[:100].replace(chr(34), "")}"'
    js = _get_json(
        "https://www.europepmc.org/webservices/rest/search?query="
        + quote(q)
        + "&format=json&pageSize=1"
    )
    if not js:
        return None
    hits = ((js.get("resultList") or {}).get("result") or [])
    if not hits:
        return None
    y = hits[0].get("pubYear")
    if y and str(y).isdigit() and ok(int(y), loose=True):
        return int(y)
    return None


KNOWN = {
    # Endocrine vol 22 issue 3 pages 257 — ~2003 Humana/Springer Endocrine
    "10.1385/ENDO/22/3/257": 2003,
    # AJPH 41(11 Pt 2):65 — 1951 historical; keep if we allow loose
    "10.2105/AJPH.41.11/Pt/2.65": 1951,
    # Blood 1999; malformed DOI
    "10.1182/blood.V94.8.2622.420k39/2622/2636": 1999,
}


def extract_years_from_blob(blob: str) -> int | None:
    # prefer copyright / published near head
    head = blob[:3000]
    for pat in (
        r"(?:Copyright|©)\s*((?:19|20)\d{2})",
        r"(?:Published|First published|Online)\s*(?:online\s*)?[:\s]*((?:19|20)\d{2})",
        r"\b((?:19|20)\d{2})\s+The Authors",
        r"doi\.org/[^\s]+.*?((?:19|20)\d{2})",
    ):
        m = re.search(pat, head, re.I)
        if m and ok(int(m.group(1)), loose=True):
            return int(m.group(1))
    # DOI year
    m = re.search(r"10\.\d{4,9}/[^\s\"'<>]+", head)
    if m:
        d = m.group(0).rstrip(".,;)")
        y = year_crossref(d, loose=True) or year_openalex(doi=d)
        if y:
            return y
    return None


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
        y = None
        d = (doi or "").strip()
        if d in KNOWN and ok(KNOWN[d], loose=True):
            y = KNOWN[d]
        if not y and d:
            # blood normalize
            cands = [d]
            m = re.match(r"^(10\.1182/blood\.[^/]+)", d, re.I)
            if m:
                cands.append(m.group(1))
            cands.append(d.replace("/Pt/", "/").replace("//", "/"))
            for c in cands:
                y = year_crossref(c, loose=True) or year_openalex(doi=c)
                if y:
                    break
                time.sleep(0.05)
        if not y and title and not title.startswith(("DOI ", "Entry ")):
            y = year_openalex(title=title) or year_epmc_title(title)
        # gold body deep read
        if not y:
            path_row = db.execute(
                text(
                    "SELECT body_path FROM literature_fulltext_gold WHERE doc_id=:d LIMIT 1"
                ),
                {"d": doc_id},
            ).fetchone()
            blob = f"{title}\n"
            if path_row and path_row[0]:
                p = Path(str(path_row[0]))
                try:
                    blob += p.read_text(encoding="utf-8", errors="replace")[:20000]
                except Exception:
                    pass
                # sibling meta json
                for alt in (
                    p.with_suffix(".json"),
                    p.parent / "meta.json",
                    p.parent / f"{p.stem}.meta.json",
                ):
                    if alt.exists():
                        try:
                            blob += "\n" + alt.read_text(encoding="utf-8", errors="replace")[:5000]
                        except Exception:
                            pass
            y = extract_years_from_blob(blob)
        if y:
            db.execute(
                text("UPDATE literature_paper SET year=:y WHERE entry_no=:e AND year IS NULL"),
                {"y": y, "e": str(eno)},
            )
            fixed += 1
            print("fixed", eno, y, d or title[:50], flush=True)
        else:
            print("fail", eno, d or title[:60], flush=True)
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
            "LEFT(IFNULL(title_en,''), 120) FROM literature_paper WHERE year IS NULL"
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
        "note": "Remaining rows lack resolvable DOI/PMID/metadata; mostly Entry N fulltext stubs.",
    }
    (ROOT / "logs" / "_year_residual_final.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("DONE", out["stats"], flush=True)
    db.close()


if __name__ == "__main__":
    main()
