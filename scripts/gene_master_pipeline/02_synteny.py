# -*- coding: utf-8 -*-
"""步骤 2b：共线性投影（快速版 + 与 lift_region 交叉验证）。

绕过 resolve_route 的逐次 is_file 开销，直接加载 chain 做批量转换。
用法：python step2b_synteny_fast.py [limit] [--verify]
"""
import io, os, sys, time, json
from bisect import bisect_left
from collections import defaultdict

sys.path[:0] = ["/data/zoujin/deng/zebrafish_gene_qa",
                "/data/zoujin/deng/zebrafish_gene_qa/core",
                "/data/zoujin/deng/zebrafish_gene_qa/database"]

RT = "/data/zoujin/deng/zebrafish_runtime"
GM = RT + "/gene_map"
CHAIN = "danRer11ToGCF_049306965.2.over.chain.gz"


def load_tsv(path):
    with io.open(path, encoding="utf-8") as fh:
        hdr = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) < len(hdr):
                p += [""] * (len(hdr) - len(p))
            yield dict(zip(hdr, p))


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


def overlap_best(idx, chrom, s, e, strand=None, probe=600):
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
        if ov <= 0:
            continue
        if strand and r.get("strand") == strand:
            ov += 1
        if ov > bestov:
            bestov, best = ov, r
    return best, int(bestov)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    verify = "--verify" in sys.argv
    limit = int(args[0]) if args else 0

    from pyliftover import LiftOver
    from database.coord_liftover import CHAIN_DIR, _chrom_maps_12tu

    acc2disp, _ = _chrom_maps_12tu()
    print("assembly report 载入 accession %d 条" % len(acc2disp))
    t0 = time.time()
    lo = LiftOver(str(CHAIN_DIR / CHAIN))
    print("chain 载入 %.1fs" % (time.time() - t0))

    def lift(chrom, pos):
        q = chrom if str(chrom).startswith("chr") else "chr" + str(chrom)
        try:
            h = lo.convert_coordinate(q, pos - 1)
        except Exception:
            return None
        if not h:
            return None
        top = h[0]
        c, p, st = top[0], top[1], top[2]
        return acc2disp.get(c, c), int(p) + 1, st

    z11 = list(load_tsv(GM + "/grcz11_genes.tsv"))
    z12 = list(load_tsv(GM + "/grcz12tu_genes.tsv"))
    idx12 = build_index(z12)
    if limit:
        z11 = z11[:limit]

    t0 = time.time()
    out_rows, stat = [], defaultdict(int)
    for n, g in enumerate(z11, 1):
        gid, sym = g["ensembl_gene_id"], g["symbol"]
        try:
            s, e = int(g["start"]), int(g["end"])
        except Exception:
            stat["bad_coord"] += 1
            out_rows.append((gid, sym, "", "", "", "bad_coord", 0, 0.0, "")); continue
        a = lift(g["chrom"], s)
        b = lift(g["chrom"], e)
        if not a or not b or a[0] != b[0]:
            stat["liftover_failed"] += 1
            out_rows.append((gid, sym, "", "", "", "liftover_failed", 0, 0.0, "")); continue
        tc = a[0]
        ts, te = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])
        old_len, new_len = e - s + 1, te - ts + 1
        if new_len <= 0 or new_len > old_len * 5 or old_len > new_len * 5:
            stat["length_outlier"] += 1
            out_rows.append((gid, sym, "", "", "", "length_outlier", 0, 0.0, "")); continue
        hit, ov = overlap_best(idx12, str(tc), ts, te, g.get("strand"))
        if not hit:
            stat["no_overlap"] += 1
            out_rows.append((gid, sym, "", "", "", "no_overlap", 0, 0.0, CHAIN)); continue
        stat["mapped"] += 1
        out_rows.append((gid, sym, hit["ncbi_gene_id"], hit["symbol"], hit["biotype"],
                         "synteny", ov, round(ov * 100.0 / max(1, old_len), 1), CHAIN))
        if n % 5000 == 0:
            print("  ... %d/%d %.0fs mapped=%d" % (n, len(z11), time.time() - t0, stat["mapped"]))

    out = GM + ("/grcz11_to_grcz12tu.tsv" if not limit else "/_sample_fast.tsv")
    with io.open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("ensembl_gene_id\tsource_symbol\tgrcz12tu_gene_id\tgrcz12tu_symbol\t"
                 "grcz12tu_biotype\tmethod\toverlap_bp\toverlap_pct\tchain\n")
        for r in out_rows:
            fh.write("\t".join(str(x) for x in r) + "\n")

    print("\n完成 %.1fs -> %s" % (time.time() - t0, out))
    print("统计:", json.dumps(dict(stat), ensure_ascii=False))
    tot = max(1, len(out_rows))
    print("总计 %d，映射 %d（%.1f%%）" % (len(out_rows), stat["mapped"], 100.0 * stat["mapped"] / tot))

    if verify:
        from database.coord_liftover import lift_region
        agree = diff = 0
        sample = z11[:60]
        for g in sample:
            s, e = int(g["start"]), int(g["end"])
            r = lift_region("GRCz11", g["chrom"], s, e, g["strand"] or "+", "GRCz12tu")
            hv = (r.get("chromosome"), r.get("start"), r.get("end")) if r.get("ok") else None
            a = lift(g["chrom"], s); b = lift(g["chrom"], e)
            if a and b and a[0] == b[0]:
                lo_, hi_ = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])
                hf = (a[0], lo_, hi_)
            else:
                hf = None
            if hv and hf and hv[0] == hf[0] and abs(hv[1] - hf[1]) <= 2 and abs(hv[2] - hf[2]) <= 2:
                agree += 1
            elif hv is None and hf is None:
                agree += 1
            else:
                diff += 1
                if diff <= 5:
                    print("  差异 %s: lift_region=%s  直连=%s" % (g["ensembl_gene_id"], hv, hf))
        print("交叉验证：一致 %d，差异 %d / %d" % (agree, diff, len(sample)))


if __name__ == "__main__":
    main()
