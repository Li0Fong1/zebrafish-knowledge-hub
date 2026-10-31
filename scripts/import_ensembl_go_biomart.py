# -*- coding: utf-8 -*-
"""从 Ensembl BioMart 批量导入全基因组 GO（不走逐基因 REST）。"""
from __future__ import annotations

import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.entry import bootstrap_app

bootstrap_app()

import pymysql

from common_utils import safe_print
from config import DB_CONFIG, _BASE_DIR

CACHE = os.path.join(_BASE_DIR, "data", "ensembl", "drerio_go_biomart.tsv")
QUERY = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE Query>
<Query virtualSchemaName="default" formatter="TSV" header="1" uniqueRows="1" datasetConfigVersion="0.6">
  <Dataset name="drerio_gene_ensembl" interface="default">
    <Attribute name="ensembl_gene_id"/>
    <Attribute name="go_id"/>
    <Attribute name="name_1006"/>
    <Attribute name="namespace_1003"/>
  </Dataset>
</Query>
"""
NS_MAP = {
    "biological_process": "BP",
    "molecular_function": "MF",
    "cellular_component": "CC",
}


def download() -> str:
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    if os.path.isfile(CACHE) and os.path.getsize(CACHE) > 10000:
        safe_print(f"[缓存] {CACHE}")
        return CACHE
    endpoints = [
        "https://www.ensembl.org/biomart/martservice",
        "https://asia.ensembl.org/biomart/martservice",
        "https://useast.ensembl.org/biomart/martservice",
    ]

    class _R308(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return urllib.request.Request(
                newurl, data=req.data, headers=dict(req.header_items()), method=req.get_method()
            )

        def http_error_308(self, req, fp, code, msg, headers):
            return self.http_error_302(req, fp, code, msg, headers)

    opener = urllib.request.build_opener(_R308)
    body = ("query=" + urllib.parse.quote(QUERY)).encode("utf-8")
    last = None
    for ep in endpoints:
        safe_print(f"[下载] BioMart {ep}")
        req = urllib.request.Request(
            ep, data=body, headers={"User-Agent": "ZebrafishGeneQA/1.0", "Content-Type": "application/x-www-form-urlencoded"}
        )
        try:
            with opener.open(req, timeout=600) as resp, open(CACHE + ".part", "wb") as out:
                while True:
                    buf = resp.read(1024 * 256)
                    if not buf:
                        break
                    out.write(buf)
            os.replace(CACHE + ".part", CACHE)
            last = None
            break
        except Exception as ex:
            last = ex
            safe_print(f"  fail {ex!r}")
    if last:
        raise last
    safe_print(f"[完成] {os.path.getsize(CACHE) / 1024 / 1024:.1f} MB")
    return CACHE


def main():
    path = download()
    conn = pymysql.connect(**DB_CONFIG)
    id_map = {}
    with conn.cursor() as cur:
        cur.execute("SELECT ensembl_id, gene_id FROM gene_basic WHERE ensembl_id IS NOT NULL")
        for eid, gid in cur.fetchall():
            id_map[eid] = gid
            id_map[str(eid).split(".")[0]] = gid
    safe_print(f"Ensembl 映射 {len(id_map)}")
    rows = []
    seen = set()
    with open(path, encoding="utf-8", errors="replace") as f:
        header = f.readline()
        if "Query ERROR" in header or not header:
            raise SystemExit(f"BioMart 失败: {header[:200]}")
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4:
                continue
            eid, go_id, name, ns = parts[0], parts[1], parts[2], parts[3]
            if not eid or not go_id:
                continue
            gid = id_map.get(eid) or id_map.get(eid.split(".")[0])
            if not gid:
                continue
            term = f"{go_id} {name}".strip()[:256]
            cat = NS_MAP.get(ns, (ns or "")[:8])
            key = (gid, term)
            if key in seen:
                continue
            seen.add(key)
            rows.append((gid, term, cat))
    safe_print(f"待写入 GO 行 {len(rows)}")
    if len(rows) < 1000:
        raise SystemExit("GO 行过少，拒绝覆盖 gene_go")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM gene_go")
    conn.commit()
    sql = "INSERT INTO gene_go (gene_id, go_term, go_category) VALUES (%s,%s,%s)"
    with conn.cursor() as cur:
        for i in range(0, len(rows), 2000):
            chunk = rows[i : i + 2000]
            cur.executemany(sql, chunk)
            conn.commit()
            if i % 20000 == 0:
                safe_print(f"  … {i + len(chunk)}")
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT gene_id) FROM gene_go")
        n, g = cur.fetchone()
    conn.close()
    safe_print(f"DONE gene_go rows={n} genes={g}")


if __name__ == "__main__":
    main()
