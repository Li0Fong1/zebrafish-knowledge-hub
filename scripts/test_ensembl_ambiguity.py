# -*- coding: utf-8 -*-
"""验证 2026-11-13 的「答案正确性」修复：多候选不再冒充唯一 ID。

在服务器上运行：PYTHONPATH=database:core:. python scripts/test_ensembl_ambiguity.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core"), str(ROOT / "database")]
import ensembl_version_map as evm  # noqa: E402

DB = "/data/zoujin/deng/zebrafish_runtime/idmap_data.sqlite3"
API = "http://127.0.0.1:8080"


COOKIE = ""


def get_pass():
    global COOKIE
    with urllib.request.urlopen(API + "/api/gate/pass", timeout=20) as r:
        COOKIE = (r.headers.get("Set-Cookie") or "").split(";")[0]


def post(path, body):
    headers = {"Content-Type": "application/json"}
    if COOKIE:
        headers["Cookie"] = COOKIE
    req = urllib.request.Request(
        API + path, data=json.dumps(body).encode("utf-8"),
        headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    get_pass()
    con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    print("=== 1) 单元层：重复符号必须标 ambiguous ===", flush=True)
    dups = con.execute(
        "SELECT gene_symbol, COUNT(DISTINCT ensembl_gene_id) n FROM ens_release "
        "WHERE release=54 AND (zfin_id IS NULL OR zfin_id='') AND gene_symbol<>'' "
        "GROUP BY gene_symbol HAVING n>1 ORDER BY n DESC LIMIT 5").fetchall()
    assert dups, "没找到重复符号样本"
    for sym, n in dups:
        hits, amb = evm._lookup_release(con, 54, "", sym, "")
        ids = {h["ensembl_gene_id"] for h in hits}
        print("  %-24s 该 release 有 %d 个不同 ID，返回 %d 候选，ambiguous=%s" %
              (sym, n, len(ids), amb), flush=True)
        assert amb and len(ids) > 1, "重复符号未被标为多候选"

    print("\n=== 2) 单元层：有 ZFIN 锚点时仍唯一 ===", flush=True)
    hits, amb = evm._lookup_release(con, 116, "ZDB-GENE-990415-270",
                                    "tp53", "ENSDARG00000035559")
    print("  r116 tp53:", [(h["ensembl_gene_id"], h["match_method"]) for h in hits],
          "ambiguous=", amb, flush=True)
    assert not amb and hits and hits[0]["ensembl_gene_id"] == "ENSDARG00000035559"

    print("\n=== 3) 端到端：重复符号经 API 不得返回确定 ID ===", flush=True)
    # gene_basic 在 MySQL，不在落盘 SQLite；这里用 ens_current（r116 当前集）
    # 找「当前集存在、且 r54 里符号重复」的候选，两个表都在 SQLite。
    live = con.execute(
        "SELECT c.gene_symbol, COUNT(DISTINCT r.ensembl_gene_id) n "
        "FROM ens_current c JOIN ens_release r ON r.gene_symbol=c.gene_symbol "
        "WHERE r.release=54 AND (r.zfin_id IS NULL OR r.zfin_id='') "
        "AND c.gene_symbol<>'' GROUP BY c.gene_symbol HAVING n>1 "
        "ORDER BY n DESC LIMIT 3").fetchall()
    print("  可解析的重复符号样本:", live, flush=True)
    if live:
        sym = live[0][0]
        d = post("/api/tools/id-convert/batch",
                 {"identifiers": [sym], "source_build": "ens:116",
                  "target_build": "ens:54"})
        r = (d.get("results") or [{}])[0]
        c = r.get("canonical") or {}
        print("  输入 %s → status=%s, ensembl_id=%r, candidates=%d, match_kind=%s" %
              (sym, r.get("status"), c.get("ensembl_id"),
               len(r.get("candidates") or []), r.get("match_kind")), flush=True)
        # 若能靠 ZFIN / Ensembl ID 精确命中，unique 是正确的（不受同名行干扰）；
        # 绝不允许「仅靠符号」却给出确定 ID。
        if r.get("status") == "unique":
            assert any(k in str(r.get("match_kind")) for k in ("zfin_id", "ensembl_gene_id")), \
                "符号回退却标成唯一"
            assert c.get("ensembl_id", "").startswith("ENSDARG")
        else:
            assert r.get("status") == "ambiguous" and not c.get("ensembl_id")

    print("\n=== 3b) convert() 层：无 ZFIN/ID、只有重复符号时必须多候选 ===", flush=True)
    import id_version_map as ivm

    orig = ivm._resolve_entities

    def fake(ids):
        return [{
            "query": ids[0], "resolved": True, "status": "unique",
            "candidates": [], "notes": [], "xrefs": [],
            "local": {"gene_name": "zgc:158629"}, "local_id": None,
            "master": None, "local_coords": None,
            "hub_build": "", "hub_coords": None,
            "stable": {"symbol": "zgc:158629", "ensembl": "", "zfin": "",
                       "ncbi": "", "uniprot": ""},
        }]

    ivm._resolve_entities = fake
    try:
        d = evm.convert(["zgc:158629"], "ens:116", "ens:54",
                        evm.make_builds("ens:116", "ens:54"))
    finally:
        ivm._resolve_entities = orig
    r = (d.get("results") or [{}])[0]
    c = r.get("canonical") or {}
    print("  status=%s, canonical.ensembl_id=%r, candidates=%d, match_kind=%s" %
          (r.get("status"), c.get("ensembl_id"),
           len(r.get("candidates") or []), r.get("match_kind")), flush=True)
    assert r.get("status") == "ambiguous", "convert 未标多候选"
    assert not c.get("ensembl_id"), "多候选仍填了确定 ID"
    assert len(r.get("candidates") or []) > 1, "未列出全部候选"

    print("\n=== 4) 端到端：正常基因仍唯一且 ID 正确 ===", flush=True)
    d = post("/api/tools/id-convert/batch",
             {"identifiers": ["tp53", "ENSDARG00000035559", "30590",
                              "ZDB-GENE-990415-270"],
              "source_build": "ens:80", "target_build": "ens:54"})
    for r in d.get("results") or []:
        print("  %-24s %-10s ens=%s" %
              (r["query"], r["status"], (r.get("canonical") or {}).get("ensembl_id")),
              flush=True)
        assert r["status"] == "unique", "正常基因被误标"
        assert (r.get("canonical") or {}).get("ensembl_id", "").startswith("ENSDARG")
    con.close()
    print("\n=== 全部通过 ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
