# -*- coding: utf-8 -*-
"""步骤 1：提取 GRCz11 (Ensembl 116) 与 GRCz12tu (RefSeq) 的基因坐标，作为映射两端。

输出：
  gene_map/grcz11_genes.tsv    ensembl_gene_id, symbol, chrom, start, end, strand, biotype, gene_name
  gene_map/grcz12tu_genes.tsv  ncbi_gene_id, symbol, biotype, chrom, start, end, strand, acc
"""
import gzip, io, os, sys, json, time

RT = "/data/zoujin/deng/zebrafish_runtime"
OUT = os.path.join(RT, "gene_map")
os.makedirs(OUT, exist_ok=True)

GTF = "/data/zoujin/deng/zebrafish_gene_qa/data/ensembl/Danio_rerio.GRCz11.116.gtf.gz"


def attr(field, key):
    """从 GTF 属性列取 key "value"。"""
    tag = key + ' "'
    i = field.find(tag)
    if i < 0:
        return None
    i += len(tag)
    j = field.find('"', i)
    return field[i:j] if j > i else None


t0 = time.time()
rows = []
bio = {}
with gzip.open(GTF, "rt", encoding="utf-8", errors="replace") as fh:
    for line in fh:
        if line.startswith("#"):
            continue
        p = line.rstrip("\n").split("\t")
        if len(p) < 9 or p[2] != "gene":
            continue
        f = p[8]
        gid = attr(f, "gene_id")
        if not gid:
            continue
        bt = attr(f, "gene_biotype") or attr(f, "gene_type") or ""
        sym = attr(f, "gene_name") or ""
        chrom = p[0]
        if chrom.startswith("chr"):
            chrom = chrom[3:]
        rows.append((gid, sym, chrom, int(p[3]), int(p[4]), p[6], bt))
        bio[bt] = bio.get(bt, 0) + 1

with io.open(os.path.join(OUT, "grcz11_genes.tsv"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("ensembl_gene_id\tsymbol\tchrom\tstart\tend\tstrand\tbiotype\n")
    for r in rows:
        fh.write("\t".join(str(x) for x in r) + "\n")

print("GRCz11 基因 %d 条 -> %s (%.1fs)" % (len(rows), OUT, time.time() - t0))
print("biotype:", json.dumps(dict(sorted(bio.items(), key=lambda x: -x[1])[:8]), ensure_ascii=False))

# ---- GRCz12tu 从数据库导出 ----
env = {}
for line in io.open("/data/zoujin/deng/zebrafish_gene_qa/.env", encoding="utf-8", errors="replace"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

import pymysql
cn = pymysql.connect(host=env.get("MYSQL_HOST", "localhost"), port=int(env.get("MYSQL_PORT", "3306")),
                     user=env.get("MYSQL_USER", "root"), password=env.get("MYSQL_PASSWORD", ""),
                     database=env.get("MYSQL_DATABASE", "zebrafish_db"), charset="utf8mb4")
cur = cn.cursor()
cur.execute("""SELECT ncbi_gene_id, gene_symbol, gene_biotype, chromosome, start_pos, end_pos, strand,
                      genomic_accession, product_accession
               FROM grcz12tu_gene WHERE start_pos IS NOT NULL""")
ref = cur.fetchall()
with io.open(os.path.join(OUT, "grcz12tu_genes.tsv"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write("ncbi_gene_id\tsymbol\tbiotype\tchrom\tstart\tend\tstrand\tacc\tproduct\n")
    for r in ref:
        fh.write("\t".join("" if x is None else str(x) for x in r) + "\n")
cn.close()

bio2 = {}
for r in ref:
    bio2[r[2]] = bio2.get(r[2], 0) + 1
print("GRCz12tu 基因 %d 条（有坐标）" % len(ref))
print("biotype:", json.dumps(dict(sorted(bio2.items(), key=lambda x: -x[1])[:8]), ensure_ascii=False))
