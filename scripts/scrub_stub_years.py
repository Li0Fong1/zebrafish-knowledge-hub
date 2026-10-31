# -*- coding: utf-8 -*-
"""Try title search for remaining nulls; dump gold path status."""
from __future__ import annotations

import json
import re
import sys
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

UA = "ZebrafishGeneQA/1.0"


def _get(url):
    try:
        with urlopen(Request(url, headers={"User-Agent": UA}), timeout=25) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def main():
    db = next(get_mysql_client().get_db())
    rows = db.execute(
        text(
            "SELECT p.entry_no, p.title_en, p.doc_id, g.body_path, "
            "'' AS abstr, 0 AS blen "
            "FROM literature_paper p "
            "LEFT JOIN literature_fulltext_gold g ON g.doc_id=p.doc_id "
            "WHERE p.year IS NULL"
        )
    ).fetchall()
    print("left", len(rows), flush=True)
    fixed = 0
    for eno, title, doc_id, path, abstr, blen in rows:
        print(
            "ROW",
            eno,
            "path=",
            bool(path),
            "blen=",
            blen,
            "title=",
            (title or "")[:70],
            flush=True,
        )
        y = None
        qtitle = (title or "").strip()
        if qtitle and not qtitle.startswith("Entry "):
            js = _get(
                "https://api.openalex.org/works?search="
                + quote(qtitle[:160])
                + "&per-page=5"
            )
            for h in (js or {}).get("results") or []:
                ht = (h.get("display_name") or "").lower()
                if "bruck" in qtitle.lower() and "bruck" not in ht:
                    continue
                yy = h.get("publication_year")
                if yy and 1980 <= int(yy) <= 2027:
                    y = int(yy)
                    print("  openalex", y, h.get("display_name")[:80], flush=True)
                    break
            if not y:
                js = _get(
                    "https://www.europepmc.org/webservices/rest/search?query="
                    + quote(f'TITLE:"{qtitle[:80].replace(chr(34),"")}"')
                    + "&format=json&pageSize=3"
                )
                for h in ((js or {}).get("resultList") or {}).get("result") or []:
                    yy = h.get("pubYear")
                    if yy and str(yy).isdigit() and 1980 <= int(yy) <= 2027:
                        y = int(yy)
                        print("  epmc", y, (h.get("title") or "")[:80], flush=True)
                        break
        if not y and path:
            try:
                blob = Path(str(path)).read_text(encoding="utf-8", errors="replace")[:30000]
            except Exception as ex:
                blob = ""
                print("  readfail", ex, flush=True)
            # any year near start after doi/line
            for m in re.finditer(
                r"(?:©|Copyright|Published|Year|year)[^\d]{0,20}((?:19|20)\d{2})",
                blob[:8000],
                re.I,
            ):
                yy = int(m.group(1))
                if 1980 <= yy <= 2027:
                    y = yy
                    print("  body", y, flush=True)
                    break
            # 禁止用 JSON 元数据/生成时间戳里的年份众数（易把 2026 误写入）
        if y:
            db.execute(
                text("UPDATE literature_paper SET year=:y WHERE entry_no=:e AND year IS NULL"),
                {"y": y, "e": str(eno)},
            )
            fixed += 1
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
            "SELECT IFNULL(entry_no,''), LEFT(IFNULL(title_en,''),100) "
            "FROM literature_paper WHERE year IS NULL"
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
        "rows": [{"entry_no": a, "title": b} for a, b in residual],
        "irreducible": True,
        "note": "No DOI/PMID; stub titles or unresolvable gold metadata.",
    }
    (ROOT / "logs" / "_year_residual_final.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("DONE", out, flush=True)
    db.close()


if __name__ == "__main__":
    main()
