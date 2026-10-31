# -*- coding: utf-8 -*-
"""清掉剩余缺年：父书 DOI、SICI 内嵌年、DOI 规范化、OpenAlex/EPMC/Crossref、金标启发式。"""
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


def norm_doi(doi: str) -> str:
    d = (doi or "").strip()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d, flags=re.I)
    d = re.sub(r"^doi:\s*", "", d, flags=re.I)
    # 10.1093/jmcb_mjab064 → 10.1093/jmcb/mjab064
    d = re.sub(r"^(10\.\d+)/([a-z]+)_([a-z0-9.]+)$", r"\1/\2/\3", d, flags=re.I)
    # 10.1155/2014_123 → 10.1155/2014/123
    d = re.sub(r"^(10\.1155/)(\d{4})_(\d+)$", r"\1\2/\3", d)
    # 10.1023/A/1009271017998 → 10.1023/A:1009271017998
    d = re.sub(r"^(10\.1023/A)/(\d+)$", r"\1:\2", d, flags=re.I)
    # 10.1042/0264-6021/3600039 → 10.1042/bj3600039 (Biochem J legacy sometimes)
    return d


def year_from_doi_text(doi: str) -> int | None:
    d = doi or ""
    for pat in (
        re.compile(r"\(((?:19|20)\d{2})\d{0,4}\)"),  # SICI (19990927) / (1999)
        re.compile(r"-(19\d{2}|20\d{2})-"),
        re.compile(r"-(?:0?)([0-2]\d)-"),  # Springer short year -026- → 2026
        re.compile(r"\.(19\d{2}|20\d{2})\."),
        re.compile(r"/(19\d{2}|20\d{2})/"),
        re.compile(r"_(19\d{2}|20\d{2})_"),
        re.compile(r"/(19\d{2}|20\d{2})_"),
    ):
        m = pat.search(d)
        if not m:
            continue
        raw = m.group(1)
        y = int(raw)
        if len(raw) == 2:
            y = 2000 + y if y <= 30 else 1900 + y
        if ok(y):
            return y
    return None


def parent_dois(doi: str) -> list[str]:
    d = norm_doi(doi)
    out: list[str] = []
    # Springer chapter: 10.1007/978-x-y-z/N → book
    m = re.match(r"^(10\.\d+/978-[0-9-]+)/\d+$", d)
    if m:
        out.append(m.group(1))
    # generic trailing /digits chapter
    m = re.match(r"^(10\.\d+/.+?)/\d+$", d)
    if m and m.group(1) not in out:
        out.append(m.group(1))
    # SICI junk: keep base before (
    m = re.match(r"^(10\.\d+/[^(]+)\(", d)
    if m:
        base = m.group(1).rstrip("/")
        if base not in out:
            out.append(base)
    return out


def _get_json(url: str, timeout: int = 25) -> dict | None:
    req = Request(url, headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def year_epmc(*, doi: str | None = None, pmid: str | None = None, title: str | None = None) -> int | None:
    if pmid:
        q = f"EXT_ID:{pmid}"
    elif doi:
        q = f'DOI:"{doi}"'
    elif title:
        q = f'TITLE:"{title[:120].replace(chr(34), "")}"'
    else:
        return None
    url = (
        "https://www.europepmc.org/webservices/rest/search?query="
        + quote(q)
        + "&format=json&pageSize=1"
    )
    js = _get_json(url)
    if not js:
        return None
    hits = ((js.get("resultList") or {}).get("result") or [])
    if not hits:
        return None
    y = hits[0].get("pubYear")
    if y and str(y).isdigit() and ok(int(y)):
        return int(y)
    return None


def year_crossref(doi: str) -> int | None:
    url = "https://api.crossref.org/works/" + quote(doi)
    js = _get_json(url)
    if not js:
        return None
    msg = js.get("message") or {}
    for key in ("published-print", "published-online", "issued", "created"):
        parts = ((msg.get(key) or {}).get("date-parts") or [[]])[0]
        if parts and ok(int(parts[0])):
            return int(parts[0])
    return None


def year_openalex(*, doi: str | None = None, pmid: str | None = None, title: str | None = None) -> int | None:
    if doi:
        url = "https://api.openalex.org/works/https://doi.org/" + quote(doi)
    elif pmid:
        url = "https://api.openalex.org/works/pmid:" + quote(str(pmid))
    elif title:
        url = "https://api.openalex.org/works?search=" + quote(title[:100]) + "&per-page=1"
    else:
        return None
    js = _get_json(url)
    if not js:
        return None
    if title and "results" in js:
        hits = js.get("results") or []
        if not hits:
            return None
        y = hits[0].get("publication_year")
        return int(y) if y and ok(int(y)) else None
    y = js.get("publication_year")
    return int(y) if y and ok(int(y)) else None


def year_ncbi_pubmed(pmid: str) -> int | None:
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        f"db=pubmed&id={quote(str(pmid))}&retmode=json"
    )
    js = _get_json(url)
    if not js:
        return None
    try:
        rec = (js.get("result") or {}).get(str(pmid)) or {}
        # pubdate like "2024 Mar 12" or "2024"
        for key in ("pubdate", "epubdate", "sortpubdate"):
            raw = rec.get(key) or ""
            m = re.search(r"(19\d{2}|20\d{2})", str(raw))
            if m and ok(int(m.group(1))):
                return int(m.group(1))
    except Exception:
        return None
    return None


def resolve_doi_year(raw_doi: str) -> int | None:
    raw = (raw_doi or "").strip()
    if not raw:
        return None
    y = year_from_doi_text(raw)
    if y:
        return y
    candidates = [raw, norm_doi(raw)]
    candidates.extend(parent_dois(raw))
    # unique preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for c in candidates:
        c = (c or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        ordered.append(c)
    for d in ordered:
        y = year_from_doi_text(d)
        if y:
            return y
        y = year_crossref(d) or year_epmc(doi=d) or year_openalex(doi=d)
        if y:
            return y
        time.sleep(0.05)
    return None


def main() -> None:
    db = next(get_mysql_client().get_db())

    # 1) PMID rows
    pmids = db.execute(
        text(
            "SELECT pmid, IFNULL(doi,''), IFNULL(title_en,'') FROM literature_paper "
            "WHERE year IS NULL AND pmid IS NOT NULL AND pmid<>''"
        )
    ).fetchall()
    print("pmid_null", [p[0] for p in pmids], flush=True)
    for pmid, doi, title in pmids:
        y = (
            year_ncbi_pubmed(str(pmid))
            or year_epmc(pmid=str(pmid))
            or year_openalex(pmid=str(pmid))
        )
        if not y and doi:
            y = resolve_doi_year(str(doi))
        if not y and title:
            y = year_openalex(title=str(title)) or year_epmc(title=str(title))
        if not y:
            row = db.execute(
                text("SELECT LEFT(raw_xml, 80000) FROM literature_pubmed WHERE pmid=:p"),
                {"p": str(pmid)},
            ).fetchone()
            blob = (row[0] if row else "") or ""
            m = re.search(r"<Year>\s*((?:19|20)\d{2})\s*</Year>", blob) or re.search(
                r"<MedlineDate>\s*((?:19|20)\d{2})", blob
            )
            if m and ok(int(m.group(1))):
                y = int(m.group(1))
        if not y and str(pmid) == "38570674":
            # Lab Anim (NY) Research Highlight; DOI 10.1038/s41684-024-01360-z
            y = 2024
        if y:
            db.execute(
                text("UPDATE literature_paper SET year=:y WHERE pmid=:p AND year IS NULL"),
                {"y": y, "p": str(pmid)},
            )
            print("pmid_fixed", pmid, y, flush=True)
        else:
            print("pmid_fail", pmid, flush=True)
    db.commit()

    # 2) DOI rows
    dois = db.execute(
        text(
            "SELECT entry_no, doi FROM literature_paper "
            "WHERE year IS NULL AND doi IS NOT NULL AND doi<>''"
        )
    ).fetchall()
    print("doi_retry", len(dois), flush=True)
    okn = fail = 0
    for eno, doi in dois:
        y = resolve_doi_year(str(doi))
        if y:
            db.execute(
                text(
                    "UPDATE literature_paper SET year=:y WHERE entry_no=:e AND year IS NULL"
                ),
                {"y": y, "e": str(eno)},
            )
            okn += 1
        else:
            fail += 1
            print("doi_fail", eno, doi, flush=True)
        if (okn + fail) % 20 == 0:
            db.commit()
            print("progress", okn, fail, flush=True)
            time.sleep(0.1)
    db.commit()

    # 3) residual gold / title heuristics
    rows = db.execute(
        text(
            "SELECT p.entry_no, g.body_path, p.title_en, p.doc_id FROM literature_paper p "
            "LEFT JOIN literature_fulltext_gold g ON g.doc_id=p.doc_id "
            "WHERE p.year IS NULL"
        )
    ).fetchall()
    fixed_nd = 0
    for eno, path, title, doc_id in rows:
        blob = f"{title or ''}\n{doc_id or ''}\n"
        if path:
            try:
                blob += Path(str(path)).read_text(encoding="utf-8", errors="replace")[:8000]
            except Exception:
                pass
        y = None
        for pat in (
            r"(?:Copyright|Published|©|First published|Accepted)\s*((?:19|20)\d{2})",
            r"(?:year|Year|YEAR)[\s:=]+((?:19|20)\d{2})",
            r"\(((?:19|20)\d{2})\)\s*$",
        ):
            m = re.search(pat, blob, re.I | re.M)
            if m and ok(int(m.group(1))):
                y = int(m.group(1))
                break
        if not y:
            # DOI in body
            m = re.search(r"10\.\d{4,9}/[^\s\"'<>]+", blob)
            if m:
                y = resolve_doi_year(m.group(0).rstrip(".,;)"))
        if not y:
            continue
        db.execute(
            text("UPDATE literature_paper SET year=:y WHERE entry_no=:e AND year IS NULL"),
            {"y": y, "e": str(eno)},
        )
        fixed_nd += 1
    db.commit()

    left = db.execute(
        text(
            "SELECT SUM(year IS NOT NULL), SUM(year IS NULL), COUNT(*), "
            "SUM(year IS NULL AND doi IS NOT NULL AND doi<>''), "
            "SUM(year IS NULL AND pmid IS NOT NULL AND pmid<>'') "
            "FROM literature_paper"
        )
    ).fetchone()
    print(
        "DONE",
        {"doi_ok": okn, "doi_fail": fail, "rest_fixed": fixed_nd, "stats": tuple(left)},
        flush=True,
    )

    # dump irreducible residuals for acceptance doc
    residual = db.execute(
        text(
            "SELECT IFNULL(pmid,''), IFNULL(entry_no,''), IFNULL(doi,''), "
            "LEFT(IFNULL(title_en,''), 100) FROM literature_paper WHERE year IS NULL "
            "ORDER BY entry_no LIMIT 200"
        )
    ).fetchall()
    out = ROOT / "logs" / "_year_residual_final.json"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "stats": {
                        "with_year": int(left[0] or 0),
                        "null_year": int(left[1] or 0),
                        "total": int(left[2] or 0),
                        "null_with_doi": int(left[3] or 0),
                        "null_with_pmid": int(left[4] or 0),
                    },
                    "rows": [
                        {"pmid": a, "entry_no": b, "doi": c, "title": d}
                        for a, b, c, d in residual
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("residual_file", str(out), flush=True)
    except Exception as ex:
        print("residual_write_fail", ex, flush=True)
    db.close()


if __name__ == "__main__":
    main()
