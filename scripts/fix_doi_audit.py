# -*- coding: utf-8 -*-
"""Clear bad title-matched DOIs; fill only PMID-API DOIs verified by Crossref title overlap.

Do NOT invent publisher DOIs. JAALAS/Comp Med often have no registered DOI — leave residual.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pymysql

ROOT = Path("/data/zoujin/deng/zebrafish_gene_qa")
OUT = Path("/data/zoujin/deng/zebrafish_runtime/logs/_fix_doi_audit.json")
UA = "ZebrafishGeneQA-DOIAudit/1.0"


def env():
    e = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            e[k.strip()] = v.strip().strip('"').strip("'")
    return e


def db():
    ev = env()
    return pymysql.connect(
        host=ev.get("MYSQL_HOST", "127.0.0.1"),
        user=ev.get("MYSQL_USER", "root"),
        password=ev.get("MYSQL_PASSWORD", ""),
        database=ev.get("MYSQL_DATABASE", "zebrafish"),
        port=int(ev.get("MYSQL_PORT", "3306")),
        charset="utf8mb4",
        autocommit=False,
    )


def http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def norm_doi(d: str) -> str:
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", (d or "").strip(), flags=re.I).strip().strip(".")
    return d if re.match(r"^10\.\d{4,9}/\S+$", d, re.I) else ""


def tokens(s: str) -> set[str]:
    s = re.sub(r"&#x[0-9a-fA-F]+;", " ", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return set(re.sub(r"\W+", " ", s.lower()).split()) - {
        "the", "and", "of", "in", "a", "an", "for", "on", "with", "to", "by",
        "danio", "rerio",
    }


def overlap(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), 1)


def crossref_meta(doi: str):
    js = http_json("https://api.crossref.org/works/" + urllib.parse.quote(doi))
    if not js:
        return None
    msg = js.get("message") or {}
    titles = msg.get("title") or []
    return {"title": titles[0] if titles else "", "type": msg.get("type"), "DOI": msg.get("DOI")}


def is_junk_doi(doi: str) -> bool:
    d = (doi or "").lower()
    return (
        d.endswith(".s001")
        or "/s00" in d
        or d.startswith("10.32388/")
        or d.startswith("10.1007/978-")
        or ".s00" in d
    )


def pmid_doi_candidates(pmid: str) -> list[str]:
    out: list[str] = []
    # NCBI idconv
    js = http_json(
        "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
        f"?ids={urllib.parse.quote(pmid)}&format=json"
    )
    for rec in (js or {}).get("records") or []:
        d = norm_doi(rec.get("doi"))
        if d and d not in out:
            out.append(d)
    # esummary
    js2 = http_json(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        f"db=pubmed&id={urllib.parse.quote(pmid)}&retmode=json"
    )
    try:
        row = ((js2 or {}).get("result") or {}).get(str(pmid)) or {}
        for aid in row.get("articleids") or []:
            if str(aid.get("idtype") or "").lower() == "doi":
                d = norm_doi(aid.get("value"))
                if d and d not in out:
                    out.append(d)
    except Exception:
        pass
    # OpenAlex
    js3 = http_json(f"https://api.openalex.org/works/pmid:{urllib.parse.quote(pmid)}")
    if isinstance(js3, dict):
        d = norm_doi((js3.get("ids") or {}).get("doi") or js3.get("doi"))
        if d and d not in out:
            out.append(d)
    # EuropePMC
    js4 = http_json(
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
        f"query=EXT_ID:{pmid}%20AND%20SRC:MED&format=json&pageSize=1"
    )
    try:
        hits = ((js4 or {}).get("resultList") or {}).get("result") or []
        if hits:
            d = norm_doi(hits[0].get("doi"))
            if d and d not in out:
                out.append(d)
    except Exception:
        pass
    return out


def verify_doi(title: str, doi: str, min_ov: float = 0.55) -> tuple[bool, float, str]:
    if not doi or is_junk_doi(doi):
        return False, 0.0, ""
    meta = crossref_meta(doi)
    cr_title = (meta or {}).get("title") or ""
    ov = overlap(title, cr_title)
    ok = bool(meta) and ov >= min_ov and (meta.get("type") or "") != "component"
    return ok, ov, cr_title


def main():
    c = db()
    cur = c.cursor()
    report: dict = {
        "cleared_bad": [],
        "filled": [],
        "kept_ok": [],
        "residual": [],
        "stats": {},
    }

    # 1) Clear suspicious pattern DOIs after verifying they don't match title
    cur.execute(
        """
        SELECT doc_id, pmid, doi, IFNULL(title_en, IFNULL(title_zh,''))
        FROM literature_paper
        WHERE doi LIKE '%.s00%' OR doi LIKE '%/s00%' OR doi LIKE '10.32388/%'
           OR doi LIKE '10.1007/978-%'
           OR doi LIKE '%.s001'
        """
    )
    suspicious = list(cur.fetchall())
    print("suspicious_pattern", len(suspicious), flush=True)
    for doc_id, pmid, doi, title in suspicious:
        ok, ov, cr_t = verify_doi(title, doi, min_ov=0.55)
        time.sleep(0.2)
        if ok:
            report["kept_ok"].append({"doc_id": doc_id, "doi": doi, "ov": ov})
            print("KEEP_PATTERN", doc_id, doi, ov, flush=True)
        else:
            cur.execute("UPDATE literature_paper SET doi=NULL WHERE doc_id=%s", (doc_id,))
            report["cleared_bad"].append(
                {"doc_id": doc_id, "pmid": str(pmid or ""), "old": doi, "ov": ov, "cr_title": cr_t[:80], "title": title[:80]}
            )
            print("CLEAR", doc_id, doi, "ov", ov, flush=True)
    c.commit()

    # 2) Audit recently-filled PMIDs that still look wrong: re-check ALL rows that
    #    currently have PMID and a DOI, but only among the known problem set + no-doi set.
    problem_pmids = [
        "28662755", "26310458", "26224437", "26045453", "22330714",
        "22330247", "19887024", "20412683", "19930828", "38570674",
    ]
    cur.execute(
        """
        SELECT doc_id, pmid, doi, IFNULL(title_en, IFNULL(title_zh,''))
        FROM literature_paper
        WHERE pmid IN (%s)
        """ % ",".join(["%s"] * len(problem_pmids)),
        problem_pmids,
    )
    for doc_id, pmid, doi, title in cur.fetchall():
        pmid_s = str(pmid or "").strip()
        if doi:
            ok, ov, cr_t = verify_doi(title, doi, min_ov=0.55)
            time.sleep(0.2)
            if not ok:
                cur.execute("UPDATE literature_paper SET doi=NULL WHERE doc_id=%s", (doc_id,))
                report["cleared_bad"].append(
                    {
                        "doc_id": doc_id,
                        "pmid": pmid_s,
                        "old": doi,
                        "ov": ov,
                        "cr_title": cr_t[:80],
                        "title": title[:80],
                        "via": "problem_pmid_audit",
                    }
                )
                print("CLEAR_PMID", doc_id, doi, "ov", ov, flush=True)
            else:
                report["kept_ok"].append({"doc_id": doc_id, "pmid": pmid_s, "doi": doi, "ov": ov})
    c.commit()

    # also clear wrong DOI on eno duplicates of same titles (Stress Leukogram eno:14432 etc.)
    cur.execute(
        """
        SELECT doc_id, pmid, doi, IFNULL(title_en, IFNULL(title_zh,''))
        FROM literature_paper
        WHERE doi IS NOT NULL AND doi<>''
          AND (
            doi LIKE '%.s00%' OR doi LIKE '%/s00%' OR doi LIKE '10.32388/%'
            OR doi LIKE '10.1007/978-%'
            OR doi IN (
              '10.1021/acs.est.7b03937.s001',
              '10.1021/acsestwater.3c00128.s001',
              '10.1007/s10641-009-9539-7',
              '10.1095/biolreprod.108.073643',
              '10.3791/52156',
              '10.32388/k4asn1'
            )
          )
        """
    )
    for doc_id, pmid, doi, title in cur.fetchall():
        ok, ov, cr_t = verify_doi(title, doi, min_ov=0.55)
        time.sleep(0.15)
        if not ok:
            cur.execute("UPDATE literature_paper SET doi=NULL WHERE doc_id=%s", (doc_id,))
            report["cleared_bad"].append(
                {"doc_id": doc_id, "old": doi, "ov": ov, "via": "known_bad_list", "title": title[:80]}
            )
            print("CLEAR_KNOWN_BAD", doc_id, doi, flush=True)
    c.commit()

    # 3) For every no-DOI row with PMID: try API DOI + Crossref verify
    cur.execute(
        """
        SELECT doc_id, entry_no, pmid, IFNULL(title_en, IFNULL(title_zh,''))
        FROM literature_paper
        WHERE (doi IS NULL OR doi='') AND pmid IS NOT NULL AND pmid<>''
        """
    )
    missing = list(cur.fetchall())
    print("no_doi_with_pmid", len(missing), flush=True)
    for doc_id, entry_no, pmid, title in missing:
        pmid_s = str(pmid).strip()
        cands = pmid_doi_candidates(pmid_s)
        time.sleep(0.35)
        chosen = ""
        chosen_ov = 0.0
        for d in cands:
            ok, ov, cr_t = verify_doi(title, d, min_ov=0.55)
            time.sleep(0.2)
            if ok and ov > chosen_ov:
                chosen, chosen_ov = d, ov
        if chosen:
            cur.execute("UPDATE literature_paper SET doi=%s WHERE doc_id=%s", (chosen, doc_id))
            report["filled"].append(
                {"doc_id": doc_id, "pmid": pmid_s, "doi": chosen, "ov": chosen_ov, "title": title[:80]}
            )
            print("FILL", doc_id, chosen, chosen_ov, flush=True)
        else:
            report["residual"].append(
                {
                    "doc_id": doc_id,
                    "entry_no": entry_no,
                    "pmid": pmid_s,
                    "title": title[:100],
                    "reason": "no_verified_doi_in_apis_or_crossref",
                    "tried": cands,
                }
            )
            print("RESIDUAL_PMID", doc_id, pmid_s, flush=True)
    c.commit()

    # 4) no-DOI without PMID: keep if usable title; note residual (do not clear real papers)
    cur.execute(
        """
        SELECT doc_id, entry_no, pmid, source, IFNULL(title_en, IFNULL(title_zh,''))
        FROM literature_paper
        WHERE doi IS NULL OR doi=''
        """
    )
    all_no = list(cur.fetchall())
    residual_docs = {r["doc_id"] for r in report["residual"]}
    for doc_id, entry_no, pmid, source, title in all_no:
        if doc_id in residual_docs:
            continue
        t = (title or "").strip()
        usable = bool(str(pmid or "").strip()) or (
            len(t) >= 20 and not re.match(r"(?i)^entry\s+\d+\s+fulltext$", t) and t.lower() not in ("", "null", "none")
        )
        report["residual"].append(
            {
                "doc_id": doc_id,
                "entry_no": entry_no,
                "pmid": str(pmid or "") or None,
                "source": source,
                "title": t[:100],
                "reason": "irreducible_no_verified_doi" if usable else "empty_placeholder_should_have_been_cleared",
            }
        )

    cur.execute("SELECT COUNT(*) FROM literature_paper")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM literature_paper WHERE doi IS NOT NULL AND doi<>''")
    with_doi = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM literature_paper WHERE pmid IS NOT NULL AND pmid<>''")
    with_pmid = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM literature_paper WHERE year IS NOT NULL")
    with_year = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM literature_fulltext_gold")
    gold = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM gene_basic")
    genes = cur.fetchone()[0]

    report["stats"] = {
        "literature_paper": total,
        "with_doi": with_doi,
        "no_doi": total - with_doi,
        "doi_fill_rate": round(with_doi / total, 6) if total else None,
        "with_pmid": with_pmid,
        "with_year": with_year,
        "null_year": total - with_year,
        "literature_fulltext_gold": gold,
        "gene_basic": genes,
        "cleared_bad_n": len(report["cleared_bad"]),
        "filled_n": len(report["filled"]),
        "residual_n": len(report["residual"]),
    }
    c.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("STATS", json.dumps(report["stats"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
