# -*- coding: utf-8 -*-
"""UniProt 蛋白注释层导入（zebrafish, taxid 7955）。

只做三件事：
  1. 建 uniprot_protein 表（若不存在）
  2. 从 data/uniprot/uniprot_7955_all.tsv.gz 灌入蛋白级注释
  3. 与 gene_basic.uniprot_id 对账，输出覆盖率与孤儿 accession

不修改 gene_basic / gene_xref，纯新增。可重复执行（UPSERT）。

用法：
    python scripts/import_uniprot_protein.py [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import os
import sys
from pathlib import Path

import pymysql

DB = dict(
    host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
    port=int(os.environ.get("MYSQL_PORT", "3307")),
    user=os.environ.get("MYSQL_USER", "zebrafish"),
    password=__import__("os").environ["MYSQL_PASSWORD"],
    database=os.environ.get("MYSQL_DATABASE", "zebrafish_db"),
    charset="utf8mb4",
)

DDL = """
CREATE TABLE IF NOT EXISTS uniprot_protein (
  accession     VARCHAR(16)  NOT NULL,
  entry_name    VARCHAR(32)      NULL,
  protein_name  VARCHAR(512)     NULL,
  gene_names    VARCHAR(512)     NULL,
  gene_primary  VARCHAR(64)      NULL,
  length        INT              NULL,
  mass          INT              NULL,
  reviewed      TINYINT          NOT NULL DEFAULT 0,
  organism      VARCHAR(64)      NOT NULL DEFAULT 'Danio rerio',
  function_cc   MEDIUMTEXT       NULL,
  subcell_cc    VARCHAR(2048)    NULL,
  keywords      VARCHAR(1024)    NULL,
  go_ids        VARCHAR(2048)    NULL,
  refseq_prot   VARCHAR(255)     NULL,
  ensembl_ids   VARCHAR(255)     NULL,
  source_file   VARCHAR(128)     NULL,
  imported_at   TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (accession),
  KEY idx_gene_primary (gene_primary),
  KEY idx_reviewed (reviewed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UniProt 蛋白级注释（斑马鱼 taxid 7955）'
"""

# TSV 列名 -> 表列
COLMAP = {
    "Entry": "accession",
    "Entry Name": "entry_name",
    "Protein names": "protein_name",
    "Gene Names": "gene_names",
    "Length": "length",
    "Mass": "mass",
    "Reviewed": "reviewed",
    "Function [CC]": "function_cc",
    "Subcellular location [CC]": "subcell_cc",
    "Keywords": "keywords",
    "Gene Ontology IDs": "go_ids",
    "RefSeq": "refseq_prot",
    "Ensembl": "ensembl_ids",
}


def clip(v, n):
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    return v[:n]


def parse(tsv_gz: Path):
    with gzip.open(tsv_gz, "rt", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.reader(io.TextIOWrapper(fh.buffer, encoding="utf-8",
                                             errors="replace"), delimiter="\t")
        header = next(reader)
        idx = {}
        for i, h in enumerate(header):
            h = h.strip()
            if h in COLMAP:
                idx[COLMAP[h]] = i
        missing = {"accession", "length"} - set(idx)
        if missing:
            raise SystemExit("TSV 缺少必要列: %s (表头: %s)" % (missing, header))
        for row in reader:
            if not row or len(row) < 2:
                continue
            rec = {}
            for col, i in idx.items():
                rec[col] = row[i] if i < len(row) else None
            acc = (rec.get("accession") or "").strip()
            if not acc:
                continue
            gn = (rec.get("gene_names") or "").split()
            rec["gene_primary"] = gn[0] if gn else None
            rec["reviewed"] = 1 if (rec.get("reviewed") or "").lower() == "reviewed" else 0
            rec["length"] = int(rec["length"]) if (rec.get("length") or "").isdigit() else None
            rec["mass"] = int(rec["mass"]) if (rec.get("mass") or "").isdigit() else None
            yield acc, rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--file", default="data/uniprot/uniprot_7955_all.tsv.gz")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit("找不到 %s，请先下载" % path)

    conn = pymysql.connect(**DB)
    cur = conn.cursor()
    cur.execute(DDL)

    sql = """
    INSERT INTO uniprot_protein
      (accession, entry_name, protein_name, gene_names, gene_primary,
       length, mass, reviewed, function_cc, subcell_cc, keywords, go_ids,
       refseq_prot, ensembl_ids, source_file)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
      entry_name=VALUES(entry_name), protein_name=VALUES(protein_name),
      gene_names=VALUES(gene_names), gene_primary=VALUES(gene_primary),
      length=VALUES(length), mass=VALUES(mass), reviewed=VALUES(reviewed),
      function_cc=VALUES(function_cc), subcell_cc=VALUES(subcell_cc),
      keywords=VALUES(keywords), go_ids=VALUES(go_ids),
      refseq_prot=VALUES(refseq_prot), ensembl_ids=VALUES(ensembl_ids),
      source_file=VALUES(source_file)
    """
    batch, n, nrev = [], 0, 0
    for acc, r in parse(path):
        batch.append((acc, clip(r.get("entry_name"), 32), clip(r.get("protein_name"), 512),
                      clip(r.get("gene_names"), 512), clip(r.get("gene_primary"), 64),
                      r["length"], r["mass"], r["reviewed"],
                      clip(r.get("function_cc"), 60000), clip(r.get("subcell_cc"), 2048),
                      clip(r.get("keywords"), 1024), clip(r.get("go_ids"), 2048),
                      clip(r.get("refseq_prot"), 255), clip(r.get("ensembl_ids"), 255),
                      path.name))
        nrev += r["reviewed"]
        n += 1
        if len(batch) >= 2000:
            if not args.dry_run:
                cur.executemany(sql, batch)
                conn.commit()
            batch.clear()
            print("  ... %d 条" % n, flush=True)
    if batch and not args.dry_run:
        cur.executemany(sql, batch)
        conn.commit()

    print("导入完成: 解析 %d 条，其中 reviewed %d 条%s" % (n, nrev, "（dry-run 未写库）" if args.dry_run else ""))

    # 对账
    cur.execute("SELECT COUNT(*) FROM uniprot_protein")
    print("uniprot_protein 总行:", cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM uniprot_protein WHERE reviewed=1")
    print("  其中 reviewed:", cur.fetchone()[0])
    cur.execute("""
        SELECT COUNT(*) FROM gene_basic b
         WHERE b.uniprot_id<>'' AND EXISTS (
           SELECT 1 FROM uniprot_protein p WHERE p.accession=b.uniprot_id)""")
    hit = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM gene_basic WHERE uniprot_id<>''")
    tot = cur.fetchone()[0]
    print("gene_basic 有 accession %d，能在 uniprot_protein 找到 %d (%.2f%%)"
          % (tot, hit, hit * 100.0 / max(tot, 1)))
    cur.execute("""
        SELECT b.uniprot_id, b.gene_name FROM gene_basic b
         WHERE b.uniprot_id<>'' AND NOT EXISTS (
           SELECT 1 FROM uniprot_protein p WHERE p.accession=b.uniprot_id) LIMIT 15""")
    miss = cur.fetchall()
    if miss:
        print("  孤儿 accession 示例:", miss[:8])
    cur.execute("""
        SELECT COUNT(*) FROM gene_basic b JOIN uniprot_protein p ON p.accession=b.uniprot_id
         WHERE p.gene_primary IS NOT NULL
           AND LOWER(p.gene_primary) <> LOWER(b.gene_name)""")
    print("  基因名与 UniProt 主名不一致:", cur.fetchone()[0])

    conn.close()


if __name__ == "__main__":
    main()
