# -*- coding: utf-8 -*-
"""步骤 3：把 gene_basic 的标识符汇聚到 GRCz12tu。

优先级：
  A) ncbi_gene_id → grcz12tu_gene.ncbi_gene_id      （ID 直连，最可靠）
  B) 已有 gene_assembly_xref(GRCz12tu) 坐标           （库内既有）
  C) ensembl_id → 共线性投影表                        （synteny）
  D) 其余 → 留给 BLAST 兜底

输出：
  gene_map/gene_basic_to_grcz12tu.tsv   gene_id, name, symbol, grcz12tu_gene_id, symbol12, biotype, method, score
  gene_map/pending_blast.tsv            待 BLAST 的 gene_id 及其可用序列线索
  stdout: 覆盖率统计
"""
import io, os, sys, json, time
from collections import defaultdict, Counter

sys.path[:0] = ["/data/zoujin/deng/zebrafish_gene_qa",
                "/data/zoujin/deng/zebrafish_gene_qa/core",
                "/data/zoujin/deng/zebrafish_gene_qa/database"]

RT = "/data/zoujin/deng/zebrafish_runtime"
GM = RT + "/gene_map"

env = {}
for line in io.open("/data/zoujin/deng/zebrafish_gene_qa/.env", encoding="utf-8", errors="replace"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

import pymysql


def load_tsv(path):
    with io.open(path, encoding="utf-8") as fh:
        hdr = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < len(hdr):
                p += [""] * (len(hdr) - len(p))
            yield dict(zip(hdr, p))


def main():
    cn = pymysql.connect(host=env.get("MYSQL_HOST", "localhost"), port=int(env.get("MYSQL_PORT", "3306")),
                         user=env.get("MYSQL_USER", "root"), password=env.get("MYSQL_PASSWORD", ""),
                         database=env.get("MYSQL_DATABASE", "zebrafish_db"), charset="utf8mb4")
    cur = cn.cursor()

    # ---- GRCz12tu 目标全集 ----
    cur.execute("SELECT ncbi_gene_id, gene_symbol, gene_biotype, chromosome, start_pos, end_pos, strand FROM grcz12tu_gene")
    z12 = {}
    for gid, sym, bt, ch, s, e, st in cur.fetchall():
        z12[str(gid)] = {"symbol": sym, "biotype": bt, "chrom": ch, "start": s, "end": e, "strand": st}
    print("GRCz12tu 目标基因 %d" % len(z12))

    # ---- gene_basic ----
    cur.execute("SELECT gene_id, gene_name, gene_symbol, ensembl_id, zfin_id, uniprot_id, chromosome FROM gene_basic")
    basic = cur.fetchall()
    print("gene_basic %d 条" % len(basic))

    # ---- gene_xref: gene_id -> {db: id} ----
    cur.execute("SELECT gene_id, db_name, db_id FROM gene_xref")
    xref = defaultdict(dict)
    for gid, db, did in cur.fetchall():
        if did:
            xref[gid][db] = did
    print("gene_xref 覆盖 %d 个基因" % len(xref))

    # ---- 已有 GRCz12tu 装配坐标 ----
    cur.execute("SELECT gene_id, ncbi_gene_id, gene_symbol, start_pos, end_pos FROM gene_assembly_xref "
                "WHERE assembly_name='GRCz12tu'")
    asm12 = {}
    for gid, ncid, sym, s, e in cur.fetchall():
        asm12[gid] = (str(ncid) if ncid else "", sym)
    print("库内已有 GRCz12tu 坐标 %d 条" % len(asm12))

    # ---- 共线性表：ensembl -> grcz12tu ----
    syn = {}
    syn_score = {}
    for r in load_tsv(GM + "/grcz11_to_grcz12tu.tsv"):
        if r["grcz12tu_gene_id"]:
            syn[r["ensembl_gene_id"]] = r["grcz12tu_gene_id"]
            syn_score[r["ensembl_gene_id"]] = (r["overlap_pct"], r["grcz12tu_symbol"], r["grcz12tu_biotype"])
    print("共线性表 %d 条可用" % len(syn))

    cn.close()

    # ---- 逐基因映射 ----
    rows, pending = [], []
    stat = Counter()
    for gid, name, sym, ens, zfin, uni, chrom in basic:
        target = ""
        method = ""
        score = ""
        x = xref.get(gid, {})

        # A) NCBI Gene ID 直连
        ncid = x.get("ncbi_gene") or ""
        if ncid and str(ncid) in z12:
            target, method, score = str(ncid), "id_direct", "1.0"
        # B) 库内已有 GRCz12tu 坐标
        elif gid in asm12 and asm12[gid][0] and asm12[gid][0] in z12:
            target, method, score = asm12[gid][0], "assembly_xref", "1.0"
        # C) 共线性
        elif ens and ens in syn:
            target = syn[ens]
            method = "synteny"
            score = syn_score.get(ens, ("", "", ""))[0]
        if target:
            z = z12.get(target, {})
            rows.append((gid, name or "", sym or "", target, z.get("symbol") or "",
                         z.get("biotype") or "", method, score, ens or "", zfin or "", uni or ""))
            stat[method] += 1
        else:
            pending.append((gid, name or "", sym or "", ens or "", zfin or "", uni or "", chrom or ""))
            stat["pending_blast"] += 1

    with io.open(GM + "/gene_basic_to_grcz12tu.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("gene_id\tgene_name\tgene_symbol\tgrcz12tu_gene_id\tgrcz12tu_symbol\t"
                 "grcz12tu_biotype\tmethod\tscore\tensembl_id\tzfin_id\tuniprot_id\n")
        for r in rows:
            fh.write("\t".join(str(v) for v in r) + "\n")

    with io.open(GM + "/pending_blast.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("gene_id\tgene_name\tgene_symbol\tensembl_id\tzfin_id\tuniprot_id\tchromosome\n")
        for r in pending:
            fh.write("\t".join(str(v) for v in r) + "\n")

    tot = len(basic)
    print("\n===== 覆盖统计 =====")
    print(json.dumps(dict(stat), ensure_ascii=False))
    done = tot - stat["pending_blast"]
    print("gene_basic %d：已映射 %d（%.1f%%），待 BLAST %d（%.1f%%）"
          % (tot, done, 100.0 * done / tot, stat["pending_blast"], 100.0 * stat["pending_blast"] / tot))

    # 待 BLAST 的标识符构成
    c = Counter()
    for r in pending:
        c["has_ens" if r[3] else ("has_zfin" if r[4] else ("has_uni" if r[5] else "no_id"))] += 1
    print("待 BLAST 构成:", json.dumps(dict(c), ensure_ascii=False))

    # 目标端覆盖
    used = set(r[3] for r in rows)
    print("命中的 GRCz12tu 基因 %d 个（占全集 %.1f%%）" % (len(used), 100.0 * len(used) / max(1, len(z12))))

    # 多对一检查
    tgt_count = Counter(r[3] for r in rows)
    multi = {k: v for k, v in tgt_count.items() if v > 1}
    print("多对一（多个源基因落到同一 GRCz12tu 基因）：%d 个目标，涉及 %d 条源记录"
          % (len(multi), sum(multi.values())))
    if multi:
        top = sorted(multi.items(), key=lambda x: -x[1])[:5]
        for t, n in top:
            who = [r[1] for r in rows if r[3] == t][:6]
            print("   %s x%d  <- %s" % (t, n, ",".join(who)))

    # 一致性抽查：源 symbol 与目标 symbol 相同的比例
    same = sum(1 for r in rows if r[2] and r[4] and r[2].lower() == r[4].lower())
    has_both = sum(1 for r in rows if r[2] and r[4])
    print("符号一致性：%d/%d（%.1f%%）源符号与目标符号相同"
          % (same, max(1, has_both), 100.0 * same / max(1, has_both)))
    for m in ("id_direct", "assembly_xref", "synteny"):
        s = sum(1 for r in rows if r[6] == m and r[2] and r[4] and r[2].lower() == r[4].lower())
        n = sum(1 for r in rows if r[6] == m and r[2] and r[4])
        if n:
            print("   %-14s %d/%d (%.1f%%)" % (m, s, n, 100.0 * s / n))


if __name__ == "__main__":
    main()
