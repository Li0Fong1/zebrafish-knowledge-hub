# -*- coding: utf-8 -*-
"""步骤 5：补充映射层 —— 符号精确匹配 + legacy 多跳共线性。

针对 step3 未能映射、且 Ensembl cDNA 里取不到序列的历史条目：
  D) legacy 老版坐标（Zv8/Zv9/GRCz10）多跳投影到 GRCz12tu
  E) 标准符号 / ZFIN 别名精确匹配 GRCz12tu 基因符号

输出：gene_map/supplement_map.tsv
"""
import io, os, sys, json, time
from bisect import bisect_left
from collections import defaultdict, Counter

sys.path[:0] = ["/data/zoujin/deng/zebrafish_gene_qa",
                "/data/zoujin/deng/zebrafish_gene_qa/core",
                "/data/zoujin/deng/zebrafish_gene_qa/database"]

RT = "/data/zoujin/deng/zebrafish_runtime"
GM = RT + "/gene_map"

import pymysql


def load_tsv(path):
    with io.open(path, encoding="utf-8") as fh:
        hdr = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            v = line.rstrip("\n").split("\t")
            if len(v) < len(hdr):
                v += [""] * (len(hdr) - len(v))
            yield dict(zip(hdr, v))


def build_index(rows):
    bychrom = defaultdict(list)
    for r in rows:
        try:
            r["_s"] = int(r["start"]); r["_e"] = int(r["end"])
        except Exception:
            continue
        bychrom[str(r["chrom"])].append(r)
    idx = {}
    for c, lst in bychrom.items():
        lst.sort(key=lambda x: x["_s"])
        idx[c] = ([x["_s"] for x in lst], [x["_e"] for x in lst], lst)
    return idx


def overlap_best(idx, chrom, s, e, probe=800):
    if chrom not in idx:
        return None, 0
    starts, ends, rows = idx[chrom]
    mid = (s + e) // 2
    i = bisect_left(starts, mid)
    lo = max(0, i - probe); hi = min(len(rows), i + probe)
    best, bestov = None, 0
    for j in range(lo, hi):
        r = rows[j]
        if r["_e"] < s - 5000:
            continue
        if r["_s"] > e + 5000:
            break
        ov = min(e, r["_e"]) - max(s, r["_s"]) + 1
        if ov > bestov:
            bestov, best = ov, r
    return best, int(bestov)


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

    # GRCz12tu 全集
    cur.execute("SELECT ncbi_gene_id, gene_symbol, gene_biotype, chromosome, start_pos, end_pos FROM grcz12tu_gene")
    z12 = {}
    sym12 = defaultdict(list)
    idx_rows = []
    for gid, sym, bt, ch, s, e in cur.fetchall():
        z12[str(gid)] = {"symbol": sym, "biotype": bt}
        if sym:
            sym12[sym.lower()].append(str(gid))
        if s is not None and e is not None:
            idx_rows.append({"ncbi_gene_id": str(gid), "symbol": sym, "biotype": bt,
                             "chrom": ch, "start": s, "end": e})
    idx12 = build_index(idx_rows)
    print("GRCz12tu %d，带符号 %d，带坐标 %d" % (len(z12), len(sym12), len(idx_rows)))

    nos = list(load_tsv(GM + "/no_sequence.tsv"))
    print("待补充 %d 条" % len(nos))
    ids = [r["gene_id"] for r in nos]
    zfin_ids = {r["zfin_id"]: r["gene_id"] for r in nos if r["zfin_id"]}
    ens_ids = {r["ensembl_id"]: r["gene_id"] for r in nos if r["ensembl_id"]}

    out = {}
    stat = Counter()

    # ---------- E) 符号 / 别名精确匹配 ----------
    # gene_name_bundle 提供整理好的标准符号
    name2gid = {}
    for gid in ids:
        pass
    cur.execute("SELECT gene_id, primary_symbol, standard_symbol, aliases_zfin, aliases_ncbi, "
                "aliases_uniprot, aliases_ensembl, aliases_other FROM gene_name_bundle")
    bundle = {}
    for row in cur.fetchall():
        bundle[str(row[0])] = [x for x in row[1:] if x]
    print("gene_name_bundle %d 条" % len(bundle))

    for r in nos:
        gid = r["gene_id"]
        cands = []
        if gid in bundle:
            cands.extend(bundle[gid])
        if r["gene_name"]:
            cands.append(r["gene_name"])
        if r["gene_symbol"] and not r["gene_symbol"].startswith("ZDB-"):
            cands.append(r["gene_symbol"])
        hit = None
        for c in cands:
            key = str(c).strip().lower()
            if not key or key.startswith("zdb-"):
                continue
            tgt = sym12.get(key)
            if tgt:
                hit = tgt[0]
                break
        if hit:
            out[gid] = (hit, "symbol_match", "1.0", r["gene_name"])
            stat["symbol_match"] += 1

    # ---------- D) legacy 多跳共线性 ----------
    legacy = {}
    for col, mapping in (("zfin_id", zfin_ids), ("ensembl_gene_id", ens_ids)):
        vals = list(mapping.keys())
        for i in range(0, len(vals), 800):
            chunk = vals[i:i + 800]
            ph = ",".join(["%s"] * len(chunk))
            cur.execute("SELECT `%s`, genome_build, chrom, start_pos, end_pos FROM legacy_genome_feature "
                        "WHERE `%s` IN (%s)" % (col, col, ph), chunk)
            for key, build, chrom, s, e in cur.fetchall():
                gid = mapping.get(key)
                if gid is None or s is None:
                    continue
                # 优先 GRCz10 > Zv9 > Zv8
                rank = {"GRCz10": 3, "Zv9": 2, "Zv8": 1}.get(build, 0)
                prev = legacy.get(gid)
                if prev is None or rank > prev[0]:
                    legacy[gid] = (rank, build, chrom, s, e)
    print("legacy 取到坐标 %d 条" % len(legacy))
    cn.close()

    if legacy:
        from database.coord_liftover import lift_region
        t0 = time.time()
        n = 0
        for gid, (rank, build, chrom, s, e) in legacy.items():
            if gid in out:
                continue
            n += 1
            res = lift_region(build, chrom, s, e, "+", "GRCz12tu")
            if not res.get("ok"):
                stat["legacy_" + (res.get("code") or "fail")] += 1
                continue
            hit, ov = overlap_best(idx12, res["chromosome"], res["start"], res["end"])
            if hit:
                out[gid] = (hit["ncbi_gene_id"], "legacy_synteny", str(round(ov * 100.0 / max(1, e - s + 1), 1)), build)
                stat["legacy_synteny"] += 1
            else:
                stat["legacy_no_overlap"] += 1
            if n % 200 == 0:
                print("  ... legacy %d/%d %.0fs" % (n, len(legacy), time.time() - t0))

    with io.open(GM + "/supplement_map.tsv", "w", encoding="utf-8", newline="\n") as fh:
        fh.write("gene_id\tgrcz12tu_gene_id\tmethod\tscore\tnote\n")
        for gid, (t, m, sc, note) in out.items():
            fh.write("%s\t%s\t%s\t%s\t%s\n" % (gid, t, m, sc, note))
    print("\n补充映射 %d 条 -> supplement_map.tsv" % len(out))
    print("统计:", json.dumps(dict(stat), ensure_ascii=False))
    remain = len(nos) - len(out)
    print("仍无法映射 %d 条（%.1f%%）" % (remain, 100.0 * remain / max(1, len(nos))))


if __name__ == "__main__":
    main()
