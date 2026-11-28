# -*- coding: utf-8 -*-
"""FigS2 数据独立复核：GTF 直查 + 反向 liftOver 回投。

三条互不依赖的证据线：
A. Zv9 行：直接读 Ensembl 79 的原始 GTF（不经 legacy_genome_feature 表）；
B. GRCz10 行：直接读 Ensembl 80 的原始 GTF；
C. 品系行：把图上坐标用**反向链**投回 GRCz12tu，看是否落回注释区间（正向投一遍、反向再投一遍）。

任何一条对不上，说明图上那格有问题。
"""
import gzip
import re
from pathlib import Path

GTF = Path("/data/zoujin/deng/zebrafish_runtime/legacy_gtf")
LV = Path("/data/zoujin/deng/zebrafish_runtime/liftover")

GENES = ["tp53", "tbxta", "shha", "fgf8a", "cyp19a1a", "mstnb"]
# 图上的值（染色体:起-止，无千分位）
FIG = {
    "Zv9": {"tp53": ("5", 25755162, 25766740), "tbxta": None,
            "shha": ("7", 43613639, 43620963), "fgf8a": ("13", 28719021, 28725587),
            "cyp19a1a": ("18", 38057824, 38073637), "mstnb": ("9", 42012379, 42016003)},
    "GRCz10": {"tp53": ("5", 23582427, 23594005), "tbxta": ("19", 14325345, 14329397),
               "shha": ("7", 40603939, 40611263), "fgf8a": ("13", 28364969, 28371535),
               "cyp19a1a": ("18", 39639527, 39655340), "mstnb": ("9", 41137549, 41141350)},
    "GRCz12tu": {"tp53": ("5", 24742747, 24754325), "tbxta": ("19", 15329574, 15333686),
                 "shha": ("7", 41099546, 41239343), "fgf8a": ("13", 31862086, 31888465),
                 "cyp19a1a": ("18", 42544993, 42579695), "mstnb": ("9", 42668002, 42671003)},
}
# 品系行：图上值（染色体号, start, end），以及回投用的反向链
STRAIN = {
    "ASM1840007v1": {"chain": ["GCA_018400075.1ToDanRer11.over.chain.gz",
                               "danRer11ToGCF_049306965.1.over.chain.gz"],
                     "cells": {"tp53": ("5", 23555694, 23567267, "CP068739.1"),
                               "tbxta": ("19", 14305023, 14309066, "CP068753.1"),
                               "shha": ("7", 40427050, 40557692, "CP068741.1"),
                               "fgf8a": ("13", 28310646, 28336845, "CP068747.1"),
                               "cyp19a1a": ("18", 39583044, 39612517, "CP068752.1"),
                               "mstnb": ("9", 41077038, 41080065, "CP068743.1")}},
    "ASM3317019v3": {"chain": ["GCA_033170195.3ToGCF_049306965.1.over.chain.gz"],
                     "cells": {"tp53": ("5", 24554487, 24566065),
                               "tbxta": ("19", 15332528, 15333640),
                               "shha": ("7", 40944204, 41084027),
                               "fgf8a": ("13", 31902407, 31928792),
                               "cyp19a1a": ("18", 42534330, 42569034),
                               "mstnb": ("9", 42797854, 42800855)}},
    "ASM2018471v1": {"chain": ["GCA_020184715.1ToGCF_049306965.1.over.chain.gz"],
                     "cells": {"tp53": ("5", 26157902, 26169474),
                               "tbxta": ("19", 15674015, 15678191),
                               "shha": ("7", 42093661, 42149464),
                               "fgf8a": ("13", 31308551, 31336693),
                               "cyp19a1a": ("18", 42598112, 42630230),
                               "mstnb": ("9", 42547651, 42550746)}},
    "fDanRer4.1": {"chain": ["GCA_944039275.1ToGCF_049306965.1.over.chain.gz"],
                   "cells": {"tp53": ("SUPER_5", 24602672, 24614150),
                             "tbxta": ("SUPER_19", 15281334, 15285444),
                             "shha": ("SUPER_7", 41178034, 41286159),
                             "fgf8a": ("SUPER_13", 30846295, 30872678),
                             "cyp19a1a": ("SUPER_18", 42087913, 42117333),
                             "mstnb": ("SUPER_9", 41343952, 41346953)}},
    "fDreABH1.1": {"chain": ["GCA_903684865.1ToGCF_049306965.1.over.chain.gz"],
                   "cells": {"tp53": ("5", 25971935, 25983510),
                             "tbxta": ("19", 15271233, 15275337),
                             "shha": None,
                             "fgf8a": ("13", 28562624, 28590459),
                             "cyp19a1a": ("18", 40900371, 40929792),
                             "mstnb": ("9", 41028347, 41031363)}},
    "fDreTuH1.2": {"chain": ["GCA_903684855.2ToGCF_049306965.1.over.chain.gz"],
                   "cells": {"tp53": ("5", 24398760, 24410336),
                             "tbxta": ("19", 14983735, 14987843),
                             "shha": ("7", 39921771, 39974316),
                             "fgf8a": ("13", 28653376, 28679734),
                             "cyp19a1a": ("18", 40425779, 40455190),
                             "mstnb": ("9", 41151181, 41154182)}},
}
ALIAS = {"tbxta": ["tbxta", "ntl", "ta"], "cyp19a1a": ["cyp19a1a", "cyp19a"],
         "mstnb": ["mstnb", "mstn"], "fgf8a": ["fgf8a", "fgf8"],
         "shha": ["shha", "shh"], "tp53": ["tp53"]}


def gtf_genes(path: Path) -> dict:
    """{gene_name: (seq, start, end)}，只取 feature=gene。"""
    out = {}
    with gzip.open(path, "rt", errors="replace") as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 9 or p[2] != "gene":
                continue
            m = re.search(r'gene_name "([^"]+)"', p[8])
            if not m:
                continue
            out.setdefault(m.group(1), (p[0], int(p[3]), int(p[4])))
    return out


def lift(path, tnames, start, end, min_cov=0.30):
    best, cur = None, None
    with gzip.open(path, "rt", errors="replace") as f:
        for line in f:
            if line.startswith("chain"):
                if cur and cur["b"]:
                    best = _better(best, _apply(cur, start, end))
                h = line.split()
                cur = ({"q": h[7], "qsize": int(h[8]), "st": h[9], "tp": int(h[5]),
                        "qp": int(h[10]), "b": []} if h[2] in tnames else None)
            elif cur is not None:
                p = line.split()
                if not p or not p[0].isdigit():
                    continue
                n = int(p[0])
                cur["b"].append((cur["tp"], cur["qp"], n))
                cur["tp"] += n
                cur["qp"] += n
                if len(p) == 3:
                    cur["tp"] += int(p[1]); cur["qp"] += int(p[2])
    if cur and cur["b"]:
        best = _better(best, _apply(cur, start, end))
    return best if best and best[0] >= min_cov else None


def _apply(ch, s, e):
    cov, acc = 0, {}
    for tp, qp, n in ch["b"]:
        lo, hi = max(s, tp), min(e, tp + n)
        if hi <= lo:
            continue
        cov += hi - lo
        if ch["st"] == "+":
            a, b = qp + (lo - tp), qp + (hi - tp)
        else:
            a, b = ch["qsize"] - qp - (hi - tp), ch["qsize"] - qp - (lo - tp)
        d = acc.setdefault(ch["q"], [10 ** 12, 0])
        d[0] = min(d[0], a); d[1] = max(d[1], b)
    if not acc:
        return None
    prim = {k: v for k, v in acc.items() if "_alt" not in k and "_fix" not in k}
    qn, (a, b) = max((prim or acc).items(), key=lambda kv: kv[1][1] - kv[1][0])
    return (cov / (e - s), qn, a, b)


def _better(a, b):
    if b is None:
        return a
    if a is None:
        return b
    pa = "_alt" not in a[1] and "_fix" not in a[1]
    pb = "_alt" not in b[1] and "_fix" not in b[1]
    if pa != pb:
        return a if pa else b
    return b if b[0] > a[0] else a


def overlap(a0, a1, b0, b1):
    return max(0, min(a1, b1) - max(a0, b0)) / (b1 - b0)


bad = 0

print("A. Zv9 行 vs 原始 GTF（Ensembl 79）")
z9 = gtf_genes(GTF / "Danio_rerio.Zv9.79.gtf.gz")
for g in GENES:
    hit = next(((n, z9[n]) for n in ALIAS[g] if n in z9), None)
    fig = FIG["Zv9"][g]
    if not hit:
        ok = fig is None
        print(f"  {g:9s} GTF: 无记录   图上: {'–' if fig is None else fig}  {'OK' if ok else 'FAIL'}")
    else:
        n, (seq, s0, e0) = hit
        ok = fig and seq == fig[0] and s0 == fig[1] and e0 == fig[2]
        print(f"  {g:9s} GTF({n}): {seq}:{s0:,}-{e0:,}  图上: {fig}  {'OK' if ok else 'FAIL'}")
    bad += not ok

print("\nB. GRCz10 行 vs 原始 GTF（Ensembl 80）")
z10 = gtf_genes(GTF / "Danio_rerio.GRCz10.80.gtf.gz")
for g in GENES:
    hit = next(((n, z10[n]) for n in ALIAS[g] if n in z10), None)
    fig = FIG["GRCz10"][g]
    if not hit:
        ok = fig is None
        print(f"  {g:9s} GTF: 无记录   图上: {fig}  {'OK' if ok else 'FAIL'}")
    else:
        n, (seq, s0, e0) = hit
        ok = fig and seq == fig[0] and s0 == fig[1] and e0 == fig[2]
        print(f"  {g:9s} GTF({n}): {seq}:{s0:,}-{e0:,}  图上: {fig}  {'OK' if ok else 'FAIL'}")
    bad += not ok

print("\nC. 品系行反向回投（图上坐标 → GRCz12tu，要求重叠 ≥ 80%）")
for name, d in STRAIN.items():
    for g in GENES:
        c = d["cells"][g]
        if not c:
            print(f"  {name:14s} {g:9s} 图上为「–」")
            continue
        chrom, s0, e0 = c[0], c[1], c[2]
        seq = c[3] if len(c) > 3 else chrom
        cur = (seq, s0, e0)
        okc = True
        for ch in d["chain"]:
            r = lift(LV / ch, {cur[0], "chr" + cur[0].replace("chr", "")}, cur[1], cur[2])
            if not r:
                okc = False
                break
            cur = (r[1], r[2], r[3])
        if not okc:
            print(f"  {name:14s} {g:9s} 回投失败  {seq}:{s0:,}-{e0:,}")
            bad += 1
            continue
        fs, f0, f1 = FIG["GRCz12tu"][g]
        ov = overlap(cur[1], cur[2], f0, f1)
        ok = ov >= 0.80
        bad += not ok
        print(f"  {name:14s} {g:9s} 回到 {cur[0]}:{cur[1]:,}-{cur[2]:,}  "
              f"覆盖注释区间 {ov*100:.0f}%  {'OK' if ok else 'FAIL'}")

print(f"\n问题单元格：{bad}")
