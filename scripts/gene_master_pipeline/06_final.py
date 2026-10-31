# -*- coding: utf-8 -*-
"""步骤 6：最终整合 —— 生成非冗余 GRCz12tu 基因主表 + 标识符映射证据表。

输入（全部为前面各步产物）：
  gene_basic_to_grcz12tu.tsv   A/B/C 层（id_direct / assembly_xref / synteny）
  supplement_map.tsv           D/E 层（legacy_synteny / symbol_match）
  blast_map.tsv                C' 层（BLAST 序列比对）

输出：
  gene_map/gene_master_grcz12tu.tsv   主表（每个 GRCz12tu 基因一行，非冗余）
  gene_map/gene_id_map.tsv            标识符映射证据（源 → 目标 + 方法 + 分数）
  gene_map/unmapped.tsv               未能映射的本地基因
  并在 MySQL 建表 gene_master_grcz12tu / gene_id_map_grcz12tu
"""
import io, os, sys, json, time
from collections import defaultdict, Counter

sys.path[:0] = ["/data/zoujin/deng/zebrafish_gene_qa"]

RT = "/data/zoujin/deng/zebrafish_runtime"
GM = RT + "/gene_map"

import pymysql

# 方法优先级（数字越大越可信），同目标取最高者作为 best_method
METHOD_RANK = {
    "id_direct": 100,       # NCBI Gene ID 直接对上
    "assembly_xref": 95,    # 库内既有 GRCz12tu 装配坐标
    "blast_100": 90,        # 序列完全一致
    "blast": 70,            # 序列比对命中
    "synteny": 60,          # 共线性投影（区间重叠）
    "symbol_match": 50,     # 标准符号精确匹配
    "legacy_synteny": 40,   # 老版本坐标多跳投影
}

# 视为「非功能」的 biotype（结构 RNA / 假基因 / 免疫片段）
NON_FUNCTIONAL = {
    "tRNA", "rRNA", "snRNA", "snoRNA", "SRP_RNA", "RNase_P_RNA", "RNase_MRP_RNA",
    "pseudogene", "transcribed_pseudogene", "ncRNA_pseudogene", "V_segment", "C_region",
}


def load_tsv(path):
    if not os.path.isfile(path):
        print("  [warn] 缺少 %s" % path)
        return []
    with io.open(path, encoding="utf-8") as fh:
        hdr = fh.readline().rstrip("\n").split("\t")
        out = []
        for line in fh:
            v = line.rstrip("\n").split("\t")
            if len(v) < len(hdr):
                v += [""] * (len(hdr) - len(v))
            out.append(dict(zip(hdr, v)))
        return out


def trim(s, n):
    s = s or ""
    return s if len(s) <= n else s[:n - 3] + "..."


def main():
    env = {}
    for line in io.open("/data/zoujin/deng/zebrafish_gene_qa/.env", encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    cn = pymysql.connect(host=env.get("MYSQL_HOST", "localhost"), port=int(env.get("MYSQL_PORT", "3306")),
                         user=env.get("MYSQL_USER", "root"), password=env.get("MYSQL_PASSWORD", ""),
                         database=env.get("MYSQL_DATABASE", "zebrafish_db"), charset="utf8mb4")
    cur = cn.cursor()

    # ---------- 1) 收集所有映射证据 ----------
    ev = []   # (source_gene_id, target, method, score)
    for r in load_tsv(GM + "/gene_basic_to_grcz12tu.tsv"):
        if r["grcz12tu_gene_id"]:
            ev.append((r["gene_id"], r["grcz12tu_gene_id"], r["method"], r["score"]))
    n_ab = len(ev)
    for r in load_tsv(GM + "/supplement_map.tsv"):
        if r["grcz12tu_gene_id"]:
            ev.append((r["gene_id"], r["grcz12tu_gene_id"], r["method"], r["score"]))
    n_sup = len(ev) - n_ab
    nb = 0
    for r in load_tsv(GM + "/blast_map.tsv"):
        try:
            pid = float(r["pident"])
        except Exception:
            continue
        m = "blast_100" if pid >= 99.5 else "blast"
        ev.append((r["gene_id"], r["grcz12tu_gene_id"], m, r["pident"]))
        nb += 1
    print("证据条数：A/B/C %d，补充 %d，BLAST %d，共 %d" % (n_ab, n_sup, nb, len(ev)))

    # 一个源基因可能有多条证据（多方法），保留最优
    best_of_src = {}
    for src, tgt, m, sc in ev:
        r = METHOD_RANK.get(m, 0)
        prev = best_of_src.get(src)
        if prev is None or r > prev[0]:
            best_of_src[src] = (r, tgt, m, sc)
    print("涉及源基因 %d 个" % len(best_of_src))

    # ---------- 2) 目标端聚合 ----------
    tgt_sources = defaultdict(list)
    tgt_best = {}
    for src, (r, tgt, m, sc) in best_of_src.items():
        tgt_sources[tgt].append(src)
        prev = tgt_best.get(tgt)
        if prev is None or r > prev[0]:
            tgt_best[tgt] = (r, m, sc)
    print("命中 GRCz12tu 基因 %d 个" % len(tgt_sources))

    # ---------- 3) 本地功能证据 ----------
    cur.execute("SELECT gene_id, COUNT(*) FROM gene_go GROUP BY gene_id")
    go = {str(k): v for k, v in cur.fetchall()}
    cur.execute("SELECT gene_id, COUNT(*) FROM gene_kegg GROUP BY gene_id")
    kegg = {str(k): v for k, v in cur.fetchall()}
    cur.execute("SELECT gene_id, gene_function FROM gene_basic")
    func = {str(k): (v or "").strip() for k, v in cur.fetchall()}
    cur.execute("SELECT gene_id, gene_name, gene_symbol, ensembl_id, zfin_id, uniprot_id FROM gene_basic")
    meta = {str(r[0]): r[1:] for r in cur.fetchall()}
    cur.execute("SELECT gene_id, db_name, db_id FROM gene_xref")
    xr = defaultdict(list)
    for gid, db, did in cur.fetchall():
        if did:
            xr[str(gid)].append((db, did))
    cur.execute("SELECT gene_id, alias FROM gene_alias")
    al = defaultdict(set)
    for gid, a in cur.fetchall():
        if a:
            al[str(gid)].add(a)
    print("功能证据：GO %d 基因，KEGG %d 基因，有功能描述 %d 基因" % (len(go), len(kegg), sum(1 for v in func.values() if v)))

    # ---------- 4) GRCz12tu 全集 ----------
    cur.execute("SELECT ncbi_gene_id, gene_symbol, gene_biotype, chromosome, start_pos, end_pos, strand "
                "FROM grcz12tu_gene")
    z12 = {}
    for gid, sym, bt, ch, s, e, st in cur.fetchall():
        z12[str(gid)] = (sym, bt, ch, s, e, st)
    print("GRCz12tu 全集 %d" % len(z12))
    cn.close()

    # ---------- 5) 写主表 ----------
    master = []
    for gid, (sym, bt, ch, s, e, st) in z12.items():
        srcs = tgt_sources.get(gid, [])
        ens, zfi, uni, nci, als = set(), set(), set(), set(), set()
        ngo = nkegg = 0
        has_func = 0
        for sg in srcs:
            m = meta.get(sg)
            if m:
                if m[2]:
                    ens.add(m[2])
                if m[3]:
                    zfi.add(m[3])
                if m[4]:
                    uni.add(m[4])
            for db, did in xr.get(sg, []):
                if db == "ensembl":
                    ens.add(did)
                elif db == "zfin":
                    zfi.add(did)
                elif db == "uniprot":
                    uni.add(did)
                elif db.startswith("ncbi"):
                    nci.add(did)
            als.update(al.get(sg, set()))
            ngo += go.get(sg, 0)
            nkegg += kegg.get(sg, 0)
            if func.get(sg):
                has_func = 1
        is_pc = 1 if bt == "protein_coding" else 0
        excluded = 1 if bt in NON_FUNCTIONAL else 0
        # 功能基因：排除结构 RNA/假基因，且具备至少一类功能证据
        is_functional = 0 if excluded else (1 if (ngo or nkegg or has_func) else (1 if is_pc and srcs else 0))
        r, m, sc = tgt_best.get(gid, (0, "", ""))
        master.append((
            gid, sym or "", bt or "", ch or "", s if s is not None else "", e if e is not None else "", st or "",
            len(srcs), trim(",".join(sorted(srcs)), 500),
            trim(",".join(sorted(ens)), 300), trim(",".join(sorted(zfi)), 300),
            trim(",".join(sorted(uni)), 200), trim(",".join(sorted(nci)), 200),
            trim(",".join(sorted(x for x in als if x)), 300),
            ngo, nkegg, has_func, is_pc, excluded, is_functional, m, sc,
        ))

    with io.open(GM + "/gene_master_grcz12tu.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("grcz12tu_gene_id\tsymbol\tbiotype\tchrom\tstart\tend\tstrand\t"
                 "n_sources\tsource_gene_ids\tensembl_ids\tzfin_ids\tuniprot_ids\tncbi_gene_ids\taliases\t"
                 "n_go\tn_kegg\thas_func_desc\tis_protein_coding\tis_nonfunctional_type\tis_functional\t"
                 "best_method\tbest_score\n")
        for r in master:
            fh.write("\t".join("" if x is None else str(x) for x in r) + "\n")
    print("主表写入 %d 行" % len(master))

    # ---------- 6) 证据表 ----------
    with io.open(GM + "/gene_id_map.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("source_gene_id\tsource_name\tsource_symbol\tgrcz12tu_gene_id\t"
                 "grcz12tu_symbol\tmethod\tscore\n")
        for src, (r, tgt, m, sc) in best_of_src.items():
            mm = meta.get(src, ("", "", "", "", ""))
            z = z12.get(tgt, ("", "", "", "", "", ""))
            fh.write("%s\t%s\t%s\t%s\t%s\t%s\t%s\n" % (src, mm[0] or "", mm[1] or "", tgt, z[0] or "", m, sc))
    print("证据表写入 %d 行" % len(best_of_src))

    # ---------- 7) 未映射 ----------
    cur_total = set(str(r[0]) for r in
                    [[k] for k in meta.keys()])
    mapped_src = set(best_of_src.keys())
    unmapped = sorted(cur_total - mapped_src)
    with io.open(GM + "/unmapped.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("gene_id\tgene_name\tgene_symbol\tensembl_id\tzfin_id\tuniprot_id\n")
        for g in unmapped:
            m = meta.get(g, ("", "", "", "", ""))
            fh.write("%s\t%s\t%s\t%s\t%s\t%s\n" % (g, m[0] or "", m[1] or "", m[2] or "", m[3] or "", m[4] or ""))
    print("未映射 %d 个 -> unmapped.tsv" % len(unmapped))

    # ---------- 8) 统计 ----------
    print("\n===== 汇总 =====")
    print("本地 gene_basic 基因 %d" % len(cur_total))
    print("已映射源基因 %d（%.1f%%），未映射 %d（%.1f%%）"
          % (len(mapped_src), 100.0 * len(mapped_src) / max(1, len(cur_total)),
             len(unmapped), 100.0 * len(unmapped) / max(1, len(cur_total))))
    c = Counter(r[2] for _, r in best_of_src.items())
    print("方法分布:", json.dumps(dict(c), ensure_ascii=False))
    IF = 19  # is_functional 的列下标
    nfunc = sum(1 for r in master if r[IF] == 1)
    nmapped_tgt = sum(1 for r in master if r[7] > 0)
    nfunc_mapped = sum(1 for r in master if r[IF] == 1 and r[7] > 0)
    print("功能基因按 biotype 分布:",
          json.dumps(dict(Counter(r[2] for r in master if r[IF] == 1)), ensure_ascii=False))
    print("排除为非功能类型的:", json.dumps(dict(Counter(r[2] for r in master if r[18] == 1)), ensure_ascii=False))
    print("主表 %d 行：有本地来源 %d，功能基因 %d，其中「有本地来源且功能」%d"
          % (len(master), nmapped_tgt, nfunc, nfunc_mapped))
    multi = sum(1 for r in master if r[7] > 1)
    print("多对一合并：%d 个 GRCz12tu 基因由多个源基因汇聚而来" % multi)


if __name__ == "__main__":
    main()
