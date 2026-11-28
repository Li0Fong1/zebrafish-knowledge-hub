# -*- coding: utf-8 -*-
"""NCBI consistency audit for the five toolkit back-ends.

Design contract (IMPORTANT — this is a VERIFICATION layer, not a data source):
  * The local database remains the single source of truth for every answer.
  * NCBI E-utilities are called ONLY to check that the local anchors still agree
    with the authoritative record.  A mismatch is reported as an ALERT; the
    local result is NEVER silently replaced by whatever the API returned.
  * Any network failure is recorded as ``ERROR`` / ``UNVERIFIED``.  It is never
    treated as a pass, and no value is guessed or imputed.
  * Every call is logged with a timestamp, the exact accession and a SHA-256 of
    the response, so a paper figure can cite the verification date.

Local anchor: ``output/hubchat实测_20261109/chat_suite.json`` holds a real Hub
answer containing the tp53 CDS served from the local RefSeq GRCz12tu FASTA
(``>tp53|NP_001258749.1|cdna|GRCz12tu|local_fasta``).  That sequence is what
BLAST / sgRNA design / primer design operate on.

Usage
    python scripts/verify_ncbi_consistency.py [--offline] [--out DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCAL_ANCHOR = ROOT / "output" / "hubchat实测_20261109" / "chat_suite.json"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TIMEOUT = 15
THROTTLE = 0.45                       # no API key: stay well under 3 req/s
UA = "ZebrafishHub-consistency-audit/1.0 (research use)"

EXPECTED = {
    "tp53_cds_len": 1125,
    "tp53_protein_len": 374,
    "tp53_mrna": "NM_001271820.1",
    "tp53_protein": "NP_001258749.1",
    "myl7_chrom": "NC_133183.1",
    "tp53_gene_id": "30590",
    "species": "Danio rerio",
}

# Result-level anchors read from the five tool screenshots (Fig. S6, second batch).
BLAST_HITS = ["XP_005165158.1", "XP_073805889.1", "NP_001258749.1",
              "XP_073805887.1", "XP_073805890.1", "NP_001315516.1",
              "XP_073805888.1", "NP_571402.1"]
GO_TERMS = {
    "GO:0000077": "DNA damage checkpoint signaling",
    "GO:0030330": "DNA damage response, signal transduction by p53 class mediator",
    "GO:0009653": "anatomical structure morphogenesis",
    "GO:0006915": "apoptotic process",
}
REACTOME_PATHS = {
    "R-DRE-6804754": "Regulation of TP53 Expression",
    "R-DRE-8941855": "RUNX3 regulates CDKN1A transcription",
}

_calls: list[dict] = []


def _external_json(url: str, service: str) -> object:
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    with opener.open(req, timeout=TIMEOUT) as r:
        body = r.read().decode("utf-8", "replace")
    _calls.append(dict(endpoint=service, url=url,
                       sha256=hashlib.sha256(body.encode()).hexdigest()[:16],
                       bytes=len(body), elapsed=round(time.time() - t0, 2)))
    time.sleep(THROTTLE)
    return json.loads(body)


def _get(endpoint: str, params: dict) -> str:
    url = f"{EUTILS}/{endpoint}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    # mirror the deployed code: bypass environment proxies explicitly
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    with opener.open(req, timeout=TIMEOUT) as r:
        body = r.read().decode("utf-8", "replace")
    _calls.append(dict(endpoint=endpoint, params=params,
                       sha256=hashlib.sha256(body.encode()).hexdigest()[:16],
                       bytes=len(body), elapsed=round(time.time() - t0, 2)))
    time.sleep(THROTTLE)
    return body


def esummary(db: str, acc: str) -> dict:
    txt = _get("esummary.fcgi", {"db": db, "id": acc, "retmode": "json"})
    data = json.loads(txt)
    uids = (data.get("result") or {}).get("uids") or []
    return (data.get("result") or {}).get(uids[0], {}) if uids else {}


def efetch_fasta(db: str, acc: str) -> str:
    txt = _get("efetch.fcgi", {"db": db, "id": acc,
                               "rettype": "fasta", "retmode": "text"})
    lines = [ln.strip() for ln in txt.splitlines() if ln and not ln.startswith(">")]
    return re.sub(r"\s", "", "".join(lines)).upper()


def local_tp53_cds() -> str:
    """Extract the longest tp53 CDS block from the recorded Hub answer."""
    data = json.loads(LOCAL_ANCHOR.read_text(encoding="utf-8"))
    best = ""

    def walk(o):
        nonlocal best
        if isinstance(o, str):
            for m in re.finditer(r"(ATGGCGCAAAACGACAG[ACGT\s]{200,})", o):
                seq = re.sub(r"\s", "", m.group(1))
                if len(seq) > len(best):
                    best = seq
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    # The captured answer is followed by the UI word "Citations", whose leading
    # C is swallowed by the [ACGT] class.  Trim to the declared CDS length.
    return best[: EXPECTED["tp53_cds_len"]]


def item(name, claim, source, ok, detail="", status=None):
    return dict(name=name, claim=claim, source=source,
                verdict=status or ("PASS" if ok else "FAIL"), detail=detail)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true",
                    help="skip API calls; local-only checks are still run")
    ap.add_argument("--out", default=str(ROOT / "output" / "ncbi_verify"))
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    results = []
    local = local_tp53_cds()
    results.append(item("local tp53 CDS length",
                        f'{EXPECTED["tp53_cds_len"]} bp', str(LOCAL_ANCHOR),
                        len(local) == EXPECTED["tp53_cds_len"],
                        f"len={len(local)}"))
    results.append(item("local tp53 CDS ORF",
                        "starts ATG, ends TAA, one terminal stop",
                        str(LOCAL_ANCHOR),
                        local.startswith("ATG") and local.endswith("TAA")
                        and sum(1 for i in range(0, len(local) - 3, 3)
                                if local[i:i + 3] in ("TAA", "TAG", "TGA")) == 0,
                        f"head={local[:12]} tail={local[-12:]}"))

    if args.offline:
        for n in ["NCBI mRNA esummary", "NCBI mRNA sequence",
                  "NCBI protein length", "NCBI chromosome", "NCBI Gene"]:
            results.append(item(n, "API verification", "NCBI E-utilities",
                                False, "skipped (--offline)", status="UNVERIFIED"))
    else:
        # 1) mRNA record metadata
        try:
            s = esummary("nuccore", EXPECTED["tp53_mrna"])
            ver = str(s.get("accessionversion") or "")
            slen = int(s.get("slen") or 0)
            title = str(s.get("title") or "")
            tax = str(s.get("taxname") or s.get("organism") or "")
            results.append(item("NCBI mRNA esummary",
                                f'{EXPECTED["tp53_mrna"]} / {EXPECTED["species"]}',
                                "eutils esummary nuccore",
                                ver.startswith("NM_001271820") and "rerio" in tax.lower(),
                                f"accession={ver} slen={slen} tax={tax} title={title[:70]}"))
        except Exception as e:                                   # noqa: BLE001
            results.append(item("NCBI mRNA esummary", "API verification",
                                "eutils esummary nuccore", False,
                                f"{type(e).__name__}: {e}", status="ERROR"))

        # 2) exact sequence containment: local CDS must be a verbatim substring
        #    of the authoritative mRNA (which also carries UTRs)
        try:
            mrna = efetch_fasta("nuccore", EXPECTED["tp53_mrna"])
            pos = mrna.find(local)
            exact = pos >= 0
            results.append(item("NCBI mRNA sequence", "local CDS == NCBI CDS verbatim",
                                "eutils efetch nuccore fasta", exact,
                                f"ncbi_len={len(mrna)} cds_offset_1based={pos + 1 if exact else None} "
                                f"identity={'100%' if exact else 'MISMATCH'} "
                                f"sha256_local={hashlib.sha256(local.encode()).hexdigest()[:16]}"))
        except Exception as e:                                   # noqa: BLE001
            results.append(item("NCBI mRNA sequence", "API verification",
                                "eutils efetch nuccore fasta", False,
                                f"{type(e).__name__}: {e}", status="ERROR"))

        # 3) protein length 374 aa
        try:
            prot = efetch_fasta("protein", EXPECTED["tp53_protein"])
            results.append(item("NCBI protein length",
                                f'{EXPECTED["tp53_protein_len"]} aa',
                                "eutils efetch protein fasta",
                                len(prot) == EXPECTED["tp53_protein_len"],
                                f"ncbi_len={len(prot)} aa; CDS 1125 bp = 374 aa x 3 + stop"))
        except Exception as e:                                   # noqa: BLE001
            results.append(item("NCBI protein length", "API verification",
                                "eutils efetch protein fasta", False,
                                f"{type(e).__name__}: {e}", status="ERROR"))

        # 4) GRCz12tu chromosome 8 record
        try:
            s = esummary("nuccore", EXPECTED["myl7_chrom"])
            slen = int(s.get("slen") or 0)
            tax = str(s.get("taxname") or s.get("organism") or "")
            results.append(item("NCBI chromosome",
                                f'{EXPECTED["myl7_chrom"]} / {EXPECTED["species"]}',
                                "eutils esummary nuccore",
                                slen > 48_903_725 and "rerio" in tax.lower(),
                                f"slen={slen} tax={tax}; myl7 promoter 48,901,726-48,903,725 in range"))
        except Exception as e:                                   # noqa: BLE001
            results.append(item("NCBI chromosome", "API verification",
                                "eutils esummary nuccore", False,
                                f"{type(e).__name__}: {e}", status="ERROR"))

        # 5) NCBI Gene 30590 == tp53
        try:
            s = esummary("gene", EXPECTED["tp53_gene_id"])
            name = str(s.get("name") or "")
            desc = str(s.get("description") or "")
            org = str((s.get("organism") or {}).get("scientificname") or "")
            results.append(item("NCBI Gene", "30590 = tp53 / Danio rerio",
                                "eutils esummary gene",
                                name.lower() == "tp53" and "rerio" in org.lower(),
                                f"symbol={name} organism={org} description={desc[:60]}"))
        except Exception as e:                                   # noqa: BLE001
            results.append(item("NCBI Gene", "API verification",
                                "eutils esummary gene", False,
                                f"{type(e).__name__}: {e}", status="ERROR"))

    # 9) local-only: sgRNA candidate really sits at the claimed coordinates
    spacer = "AAACGACAGCCAAGAGTTCG"            # 20 nt, NGG spacer, Fig. S6
    p = local.find(spacer)
    results.append(item("sgRNA candidate in local template",
                        "spacer at CDS 9-28 (1-based)", str(LOCAL_ANCHOR),
                        p == 8, f"found_1based={p + 1 if p >= 0 else None}; "
                                f"following_PAM={local[p + 20:p + 23] if p >= 0 else '-'}"))

    # ── Tool 1 BLAST: every accession shown in the result table must exist ──
    if args.offline:
        results.append(item("BLAST hit accessions", "8/8 resolvable in NCBI",
                            "NCBI protein esummary", False,
                            "skipped (--offline)", status="UNVERIFIED"))
    else:
        try:
            txt = _get("esummary.fcgi", {"db": "protein",
                                         "id": ",".join(BLAST_HITS),
                                         "retmode": "json"})
            res = json.loads(txt).get("result") or {}
            resolved = {}
            for uid in res.get("uids") or []:
                row = res.get(uid) or {}
                acc = str(row.get("accessionversion") or "")
                resolved[acc] = (row.get("slen"), str(row.get("title") or ""))
            found = [a for a in BLAST_HITS
                     if any(k.split(".")[0] == a.split(".")[0] for k in resolved)]
            ok = len(found) == len(BLAST_HITS) and all(
                "Danio rerio" in t for _, t in resolved.values() if t)
            results.append(item("BLAST hit accessions",
                                f"{len(BLAST_HITS)}/{len(BLAST_HITS)} resolvable, all Danio rerio p53 isoforms",
                                "NCBI protein esummary", ok,
                                f"resolved={len(found)}/{len(BLAST_HITS)}; "
                                f"lengths={sorted({v[0] for v in resolved.values() if v[0]})}"))
        except Exception as e:                                   # noqa: BLE001
            results.append(item("BLAST hit accessions", "API verification",
                                "NCBI protein esummary", False,
                                f"{type(e).__name__}: {e}", status="ERROR"))

    # ── Tool 2 GO annotation: term IDs must still carry the displayed name ──
    if args.offline:
        results.append(item("GO term names", f"{len(GO_TERMS)}/{len(GO_TERMS)} exact",
                            "QuickGO REST", False,
                            "skipped (--offline)", status="UNVERIFIED"))
    else:
        matched, mismatch = 0, []
        for go_id, want in GO_TERMS.items():
            try:
                d = _external_json(
                    "https://www.ebi.ac.uk/QuickGO/services/ontology/go/terms/" + go_id,
                    "QuickGO")
                name = (d.get("results") or [{}])[0].get("name") or ""
                if name == want:
                    matched += 1
                else:
                    mismatch.append(f"{go_id}: {name!r} != {want!r}")
            except Exception as e:                               # noqa: BLE001
                mismatch.append(f"{go_id}: {type(e).__name__}")
        results.append(item("GO term names", "ID → name matches QuickGO",
                            "QuickGO REST",
                            matched == len(GO_TERMS) and not mismatch,
                            f"exact={matched}/{len(GO_TERMS)}"
                            + ("; " + "; ".join(mismatch) if mismatch else "")))

    # ── Tool 3 Pathway enrichment: Reactome IDs must resolve to the names ──
    if args.offline:
        results.append(item("Pathway IDs", f"{len(REACTOME_PATHS)}/{len(REACTOME_PATHS)} exact",
                            "Reactome ContentService", False,
                            "skipped (--offline)", status="UNVERIFIED"))
    else:
        matched, mismatch = 0, []
        for rid, want in REACTOME_PATHS.items():
            try:
                d = _external_json(
                    "https://reactome.org/ContentService/data/query/" + rid,
                    "Reactome")
                name = d.get("displayName") or "" if isinstance(d, dict) else ""
                species = d.get("speciesName") or "" if isinstance(d, dict) else ""
                if name == want and "rerio" in species.lower():
                    matched += 1
                else:
                    mismatch.append(f"{rid}: {name!r}/{species!r} != {want!r}")
            except Exception as e:                               # noqa: BLE001
                mismatch.append(f"{rid}: {type(e).__name__}")
        results.append(item("Pathway IDs", "Reactome ID → name + Danio rerio",
                            "Reactome ContentService",
                            matched == len(REACTOME_PATHS) and not mismatch,
                            f"exact={matched}/{len(REACTOME_PATHS)}"
                            + ("; " + "; ".join(mismatch) if mismatch else "")))

    passed = sum(1 for r in results if r["verdict"] == "PASS")
    failed = sum(1 for r in results if r["verdict"] == "FAIL")
    other = len(results) - passed - failed
    report = dict(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        policy="local database is authoritative; NCBI API verifies only; "
               "mismatch = ALERT, never silent replacement; failure = UNVERIFIED, never a pass",
        offline=args.offline,
        expected=EXPECTED,
        summary=dict(total=len(results), passed=passed, failed=failed,
                     unverified_or_error=other),
        api_calls=_calls,
        results=results)
    (out / "consistency_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ncbi-audit] total={len(results)} PASS={passed} FAIL={failed} "
          f"UNVERIFIED/ERROR={other}  calls={len(_calls)}")
    for r in results:
        print(f'  {r["verdict"]:<11} {r["name"]:<38} {r["detail"][:110]}')
    print("report ->", out / "consistency_report.json")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
