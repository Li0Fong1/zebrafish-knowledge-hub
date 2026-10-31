# -*- coding: utf-8 -*-
"""Hub 组装口径回归：工具栏每个功能断言 GRCz12tu / GRCz11 分工。

运行：HUB_BASE=http://127.0.0.1:8080 python scripts/test_hub_assembly_consistency.py
现网也可在服务器上对本机 8080 跑。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("HUB_BASE", "http://127.0.0.1:8080").rstrip("/")
TP53 = (
    "ATGGCGCAAAACGACAGCCAAGAGTTCGCGGAGCTCTGGGAGAAGAATTTGATAAGTATTCAGCCCCCAGGTGGTGGCTCTTGCTGGGAC"
)
PROTEIN = "MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQAPILSRVGDGTQDNLSGAEKAVQVKVKALPDAQFEVVHSLAKWKRQTLGQHDFSAGEGLYNVFVKGIDKPVPFQET"
SHORT = "ATGCATGCATGCATGCA"

FAILS: list[str] = []
PASSES: list[str] = []


def _req(method: str, path: str, body=None, timeout=120):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            return r.status, payload
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        return e.code, payload
    except Exception as e:
        return 0, {"error": str(e)}


def check(name: str, ok: bool, detail: str):
    line = f"{name}: {detail}"
    if ok:
        PASSES.append(line)
        print("PASS", line, flush=True)
    else:
        FAILS.append(line)
        print("FAIL", line, flush=True)


def main() -> int:
    st, health = _req("GET", "/api/health", timeout=20)
    check(
        "health",
        st == 200 and isinstance(health, dict) and health.get("blast_is_grcz12") is True,
        f"status={st} blast_is_grcz12={health.get('blast_is_grcz12') if isinstance(health, dict) else None}"
        f" assembly={health.get('blast_assembly') if isinstance(health, dict) else None}"
        f" fasta={health.get('blast_fasta') if isinstance(health, dict) else None}",
    )

    st, body = _req("POST", "/api/tools/blast", {"sequence": TP53, "top_k": 5, "evalue": 0.01}, timeout=90)
    res = (body.get("result") if isinstance(body, dict) else None) or {}
    hits = res.get("hits") or []
    db = str(res.get("database") or "")
    db_ok = "grcz12" in db.lower() or "049306965" in db.lower() or res.get("assembly") == "GRCz12tu"
    genes = [str(h.get("gene_symbol") or "").lower() for h in hits[:5]]
    ident = hits[0].get("identity_percent") if hits else None
    check(
        "blast_tp53",
        st == 200 and res.get("success") and hits and db_ok and "tp53" in genes,
        f"status={st} assembly={res.get('assembly')} db={os.path.basename(db)} genes={genes} ident={ident}",
    )

    st, body = _req("POST", "/api/tools/blast", {"sequence": SHORT, "top_k": 3}, timeout=20)
    check("blast_short_422", st == 422, f"status={st}")

    st, body = _req("POST", "/api/tools/blast", {"sequence": PROTEIN, "top_k": 3}, timeout=20)
    res = (body.get("result") if isinstance(body, dict) else None) or {}
    err = str(res.get("error") or body)
    check(
        "blast_protein_reject",
        st in (200, 422) and ("核酸" in err or st == 422 or res.get("success") is False),
        f"status={st} error={err[:120]}",
    )

    st, body = _req("GET", "/api/tools/sequence?gene=tp53&types=cdna", timeout=90)
    cdna = ((body.get("results") or {}).get("cdna") if isinstance(body, dict) else None) or {}
    check(
        "sequence_cdna_grcz12",
        st == 200
        and cdna.get("success")
        and cdna.get("assembly") == "GRCz12tu"
        and cdna.get("source") == "local_fasta"
        and int(cdna.get("length") or 0) >= 60,
        f"status={st} assembly={cdna.get('assembly')} source={cdna.get('source')} "
        f"len={cdna.get('length')} acc={cdna.get('accession')}",
    )

    st, body = _req("GET", "/api/gene/tp53", timeout=30)
    basic = (body.get("basic") if isinstance(body, dict) else None) or {}
    std = (body.get("standard") if isinstance(body, dict) else None) or {}
    check(
        "gene_tp53",
        st == 200 and str(basic.get("gene_name") or "").lower() in ("tp53", "tp53")
        and (std.get("assembly") == "GRCz12tu" or not std),
        f"status={st} name={basic.get('gene_name')} std={std.get('gene_symbol')} asm={std.get('assembly')}",
    )

    st, body = _req("GET", "/api/tools/gene-resolve?q=" + urllib.parse.quote("p53"), timeout=30)
    std = (body.get("standard") if isinstance(body, dict) else None) or {}
    check(
        "id_p53_to_tp53",
        st == 200 and body.get("resolved") and str(std.get("gene_symbol") or "").lower() == "tp53"
        and std.get("assembly") == "GRCz12tu",
        f"status={st} resolved={body.get('resolved') if isinstance(body, dict) else None} "
        f"std={std.get('gene_symbol')} asm={std.get('assembly')}",
    )

    st, body = _req(
        "GET",
        "/api/tools/enrichment?" + urllib.parse.urlencode({"genes": "tp53,hsp70,myl7", "type": "both", "top_n": "6"}),
        timeout=90,
    )
    go = (body.get("go") if isinstance(body, dict) else None) or {}
    kegg = (body.get("kegg") if isinstance(body, dict) else None) or {}
    check(
        "enrich_xref",
        st == 200 and go.get("success") is not False and (go.get("terms") or kegg.get("pathways") or go.get("success")),
        f"status={st} go={go.get('success')} n_go={len(go.get('terms') or [])} "
        f"kegg={kegg.get('success')} n_kegg={len(kegg.get('pathways') or [])}",
    )

    st, body = _req("GET", "/api/tools/kegg?" + urllib.parse.urlencode({"q": "p53"}), timeout=30)
    paths = (body.get("pathways") if isinstance(body, dict) else None) or []
    check("kegg_search", st == 200 and len(paths) >= 1, f"status={st} n={len(paths)}")

    st, body = _req("GET", "/api/tools/annotate?gene=tp53", timeout=60)
    check(
        "annotate_tp53",
        st == 200 and isinstance(body, dict) and body.get("success") is not False,
        f"status={st} success={body.get('success') if isinstance(body, dict) else None}",
    )

    print("---", f"{len(PASSES)} pass / {len(FAILS)} fail", flush=True)
    for line in FAILS:
        print("  -", line, flush=True)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
