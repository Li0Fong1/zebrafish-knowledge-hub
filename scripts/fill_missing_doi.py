# -*- coding: utf-8 -*-
"""Fill DOI for literature_paper rows that have PMID but no DOI;
then try title-based fill; clear irreducible empty placeholders.

Run on server (uploaded) or via logs/_run_fill_doi.py SSH wrapper.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pymysql

ROOT = Path("/data/zoujin/deng/zebrafish_gene_qa")
OUT = Path("/data/zoujin/deng/zebrafish_runtime/logs/_fill_doi_result.json")
UA = "ZebrafishGeneQA-DOIFill/1.0 (mailto:local@zebrafish.local)"


def load_env() -> dict:
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def conn():
    env = load_env()
    return pymysql.connect(
        host=env.get("MYSQL_HOST", "127.0.0.1"),
        user=env.get("MYSQL_USER", "root"),
        password=env.get("MYSQL_PASSWORD", ""),
        database=env.get("MYSQL_DATABASE", "zebrafish"),
        port=int(env.get("MYSQL_PORT", "3306")),
        charset="utf8mb4",
        autocommit=False,
    )


def http_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def normalize_doi(doi: str | None) -> str:
    if not doi:
        return ""
    d = str(doi).strip()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d, flags=re.I)
    d = d.strip().strip(".")
    if not re.match(r"^10\.\d{4,9}/\S+$", d, re.I):
        return ""
    return d


def doi_from_pmid_ncbi(pmid: str) -> str:
    # NCBI id converter
    url = (
        "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
        f"?ids={urllib.parse.quote(pmid)}&format=json"
    )
    js = http_json(url)
    if isinstance(js, dict):
        for rec in js.get("records") or []:
            d = normalize_doi(rec.get("doi"))
            if d:
                return d
    # esummary articleids
    url2 = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        f"db=pubmed&id={urllib.parse.quote(pmid)}&retmode=json"
    )
    js2 = http_json(url2)
    try:
        uid = str(pmid)
        result = (js2 or {}).get("result") or {}
        row = result.get(uid) or {}
        for aid in row.get("articleids") or []:
            if str(aid.get("idtype") or "").lower() == "doi":
                d = normalize_doi(aid.get("value"))
                if d:
                    return d
    except Exception:
        pass
    return ""


def doi_from_pmid_openalex(pmid: str) -> str:
    url = f"https://api.openalex.org/works/pmid:{urllib.parse.quote(pmid)}"
    js = http_json(url)
    if isinstance(js, dict):
        # ids.doi like https://doi.org/10.xxx
        ids = js.get("ids") or {}
        d = normalize_doi(ids.get("doi") or js.get("doi"))
        if d:
            return d
    return ""


def doi_from_pmid_epmc(pmid: str) -> str:
    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
        f"query=EXT_ID:{urllib.parse.quote(pmid)}%20AND%20SRC:MED&format=json&pageSize=1"
    )
    js = http_json(url)
    try:
        hits = ((js or {}).get("resultList") or {}).get("result") or []
        if hits:
            return normalize_doi(hits[0].get("doi"))
    except Exception:
        pass
    return ""


def doi_from_pmid(pmid: str) -> str:
    return (
        doi_from_pmid_ncbi(pmid)
        or doi_from_pmid_openalex(pmid)
        or doi_from_pmid_epmc(pmid)
    )


def doi_from_title_crossref(title: str) -> str:
    title = (title or "").strip()
    if len(title) < 20 or title.lower().startswith("entry "):
        return ""
    title = re.sub(r"\s+", " ", title)
    url = (
        "https://api.crossref.org/works?"
        + urllib.parse.urlencode({"query.bibliographic": title, "rows": 5})
    )
    js = http_json(url)
    try:
        items = ((js or {}).get("message") or {}).get("items") or []
        tnorm = re.sub(r"\W+", " ", title.lower()).strip()
        best = ("", 0.0)
        for it in items:
            titles = it.get("title") or []
            if not titles:
                continue
            cand = re.sub(r"\W+", " ", str(titles[0]).lower()).strip()
            score = float(it.get("score") or 0)
            ta, tb = set(tnorm.split()), set(cand.split())
            if not ta or not tb:
                continue
            overlap = len(ta & tb) / max(len(ta), 1)
            if tnorm[:50] in cand or cand[:50] in tnorm or overlap >= 0.7 or score >= 50:
                d = normalize_doi(it.get("DOI"))
                if d and score >= best[1]:
                    best = (d, score)
        return best[0]
    except Exception:
        pass
    return ""


def doi_from_title_openalex(title: str) -> str:
    title = (title or "").strip()
    if len(title) < 20 or title.lower().startswith("entry "):
        return ""
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
        {"search": title, "per_page": 3}
    )
    js = http_json(url)
    try:
        for it in (js or {}).get("results") or []:
            dt = it.get("title") or ""
            dt = re.sub(r"\W+", " ", str(dt).lower()).strip()
            tnorm = re.sub(r"\W+", " ", title.lower()).strip()
            ta, tb = set(tnorm.split()), set(dt.split())
            if ta and len(ta & tb) / max(len(ta), 1) >= 0.7:
                ids = it.get("ids") or {}
                d = normalize_doi(ids.get("doi") or it.get("doi"))
                if d:
                    return d
    except Exception:
        pass
    return ""


def fetch_no_doi(cur):
    cur.execute(
        """
        SELECT entry_no, doc_id, pmid, doi, year, source, gold_status,
               IFNULL(title_en, IFNULL(title_zh, '')) AS title
        FROM literature_paper
        WHERE doi IS NULL OR doi=''
        ORDER BY (pmid IS NULL OR pmid=''), entry_no
        """
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def update_doi(cur, *, doc_id: str, doi: str) -> int:
    cur.execute(
        "UPDATE literature_paper SET doi=%s WHERE doc_id=%s AND (doi IS NULL OR doi='')",
        (doi, doc_id),
    )
    return cur.rowcount


def is_empty_placeholder(row: dict) -> bool:
    title = (row.get("title") or "").strip()
    pmid = str(row.get("pmid") or "").strip()
    if pmid and pmid not in ("0", "None"):
        return False
    if re.match(r"(?i)^entry\s+\d+\s+fulltext$", title):
        return True
    if not title and not pmid:
        return True
    return False


def main():
    c = conn()
    cur = c.cursor()
    rows = fetch_no_doi(cur)
    result = {
        "before": len(rows),
        "pmid_filled": [],
        "pmid_miss": [],
        "title_filled": [],
        "title_miss": [],
        "cleared": [],
        "clear_skipped": [],
    }

    # 1) PMID → DOI
    pmid_rows = [r for r in rows if str(r.get("pmid") or "").strip() not in ("", "0", "None")]
    print(f"PMID rows without DOI: {len(pmid_rows)}", flush=True)
    for r in pmid_rows:
        pmid = str(r["pmid"]).strip()
        doi = doi_from_pmid(pmid)
        time.sleep(0.34)
        if not doi:
            result["pmid_miss"].append({"doc_id": r["doc_id"], "pmid": pmid, "title": (r.get("title") or "")[:80]})
            print("MISS_PMID", pmid, flush=True)
            continue
        n = update_doi(cur, doc_id=r["doc_id"], doi=doi)
        result["pmid_filled"].append({"doc_id": r["doc_id"], "pmid": pmid, "doi": doi, "updated": n})
        print("OK_PMID", pmid, "->", doi, flush=True)
    c.commit()

    # refresh remaining
    rows2 = fetch_no_doi(cur)
    print(f"Remaining after PMID fill: {len(rows2)}", flush=True)

    # 2) title → DOI for non-placeholder
    for r in rows2:
        if is_empty_placeholder(r):
            continue
        title = (r.get("title") or "").strip()
        doi = doi_from_title_crossref(title) or doi_from_title_openalex(title)
        time.sleep(0.4)
        if not doi:
            result["title_miss"].append(
                {
                    "doc_id": r["doc_id"],
                    "entry_no": r.get("entry_no"),
                    "title": title[:100],
                    "source": r.get("source"),
                }
            )
            print("MISS_TITLE", r.get("entry_no"), title[:60], flush=True)
            continue
        n = update_doi(cur, doc_id=r["doc_id"], doi=doi)
        result["title_filled"].append(
            {"doc_id": r["doc_id"], "entry_no": r.get("entry_no"), "doi": doi, "updated": n, "title": title[:80]}
        )
        print("OK_TITLE", r.get("entry_no"), "->", doi, flush=True)
    c.commit()

    # 3) clear empty placeholders (no pmid, Entry N fulltext or empty title)
    rows3 = fetch_no_doi(cur)
    for r in rows3:
        if not is_empty_placeholder(r):
            result["clear_skipped"].append(
                {
                    "doc_id": r["doc_id"],
                    "entry_no": r.get("entry_no"),
                    "pmid": r.get("pmid"),
                    "title": (r.get("title") or "")[:100],
                    "reason": "has_usable_title_or_pmid_but_no_doi",
                }
            )
            continue
        # delete gold child first, then paper (FK RESTRICT)
        try:
            cur.execute("DELETE FROM literature_fulltext_gold WHERE doc_id=%s", (r["doc_id"],))
        except Exception as ex:
            result.setdefault("gold_delete_notes", []).append(f"{r['doc_id']}:{ex}")
            # if gold delete fails, skip paper delete
            result["clear_skipped"].append(
                {
                    "doc_id": r["doc_id"],
                    "entry_no": r.get("entry_no"),
                    "title": (r.get("title") or "")[:80],
                    "reason": f"gold_fk_block:{ex}",
                }
            )
            continue
        try:
            cur.execute("DELETE FROM literature_paper WHERE doc_id=%s", (r["doc_id"],))
        except Exception as ex:
            result["clear_skipped"].append(
                {
                    "doc_id": r["doc_id"],
                    "entry_no": r.get("entry_no"),
                    "title": (r.get("title") or "")[:80],
                    "reason": f"paper_delete_fail:{ex}",
                }
            )
            continue
        result["cleared"].append(
            {
                "doc_id": r["doc_id"],
                "entry_no": r.get("entry_no"),
                "title": (r.get("title") or "")[:80],
                "source": r.get("source"),
                "gold_status": r.get("gold_status"),
            }
        )
        print("CLEARED", r["doc_id"], r.get("entry_no"), flush=True)
    c.commit()

    # final stats
    cur.execute("SELECT COUNT(*) FROM literature_paper")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM literature_paper WHERE doi IS NOT NULL AND doi<>''")
    with_doi = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM literature_paper WHERE doi IS NULL OR doi=''")
    no_doi = cur.fetchone()[0]
    result["after"] = {
        "literature_paper": total,
        "with_doi": with_doi,
        "no_doi": no_doi,
        "doi_fill_rate": round(with_doi / total, 6) if total else None,
    }
    # list residual
    cur.execute(
        """
        SELECT entry_no, doc_id, pmid, LEFT(IFNULL(title_en, IFNULL(title_zh,'')), 100), source
        FROM literature_paper WHERE doi IS NULL OR doi=''
        """
    )
    result["residual"] = [
        {"entry_no": a, "doc_id": b, "pmid": c, "title": d, "source": e} for a, b, c, d, e in cur.fetchall()
    ]
    c.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT, flush=True)
    print("AFTER", json.dumps(result["after"], ensure_ascii=False), flush=True)
    print(
        "COUNTS",
        f"pmid_filled={len(result['pmid_filled'])} title_filled={len(result['title_filled'])} "
        f"cleared={len(result['cleared'])} residual={len(result['residual'])}",
        flush=True,
    )


if __name__ == "__main__":
    main()
