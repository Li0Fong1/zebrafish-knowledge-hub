# -*- coding: utf-8 -*-
"""把六个基因的 GRCz12tu 坐标投到 6 套 NCBI 品系装配，双重校验后输出 JSON。

为什么必须 liftOver：这 6 套品系装配（ASM1840007v1/T5D、ASM3317019v3/Tübingen、ASM2018471v1/AB、
fDanRer4.1、fDreABH1.1、fDreTuH1.2）都是 GenBank 草稿，**没有基因注释**，NCBI 不提供 gene 表，
只能由 GRCz12tu 的 RefSeq 注释经 over.chain 迁移。

两道校验：
1) 同一套代码先投 GRCz11，落点必须覆盖已知的 GRCz11 注释区间（6/6），否则坐标口径有问题；
2) 投到品系装配后，落点 scaffold 必须在该装配报告里登记为同一号染色体；
   落到 Un_scaffold 或别的染色体，多半是旁系同源或链断裂，不得写进图。
"""
import gzip
import json
from pathlib import Path

D = Path("/data/zoujin/deng/zebrafish_runtime/liftover")

GENES = {  # GRCz12tu（GCF_049306965.2）RefSeq 注释，染色体号
    "tp53": ("chr5", 24742747, 24754325),
    "tbxta": ("chr19", 15329574, 15333686),
    "shha": ("chr7", 41099546, 41239343),
    "fgf8a": ("chr13", 31862086, 31888465),
    "cyp19a1a": ("chr18", 42544993, 42579695),
    "mstnb": ("chr9", 42668002, 42671003),
}
EXPECT_GRCZ11 = {
    "tp53": ("chr5", 24086227, 24097805),
    "tbxta": ("chr19", 14187540, 14191592),
    "shha": ("chr7", 40884012, 40893295),
    "fgf8a": ("chr13", 28495419, 28501985),
    "cyp19a1a": ("chr18", 39620535, 39636348),
    "mstnb": ("chr9", 40939336, 40943137),
}
STRAINS = [  # 显示名, GenBank 登录号, 装配报告, 一跳链（T5D 走两跳）
    ("ASM1840007v1", "GCA_018400075.1", "GCA_018400075.1_ASM1840007v1_assembly_report.txt", None),
    ("ASM3317019v3", "GCA_033170195.3", "GCA_033170195.3_ASM3317019v3_assembly_report.txt",
     "GCF_049306965.1ToGCA_033170195.3.over.chain.gz"),
    ("ASM2018471v1", "GCA_020184715.1", "GCA_020184715.1_ASM2018471v1_assembly_report.txt",
     "GCF_049306965.1ToGCA_020184715.1.over.chain.gz"),
    ("fDanRer4.1", "GCA_944039275.1", "GCA_944039275.1_fDanRer4.1_assembly_report.txt",
     "GCF_049306965.1ToGCA_944039275.1.over.chain.gz"),
    ("fDreABH1.1", "GCA_903684865.1", "GCA_903684865.1_fDreABH1.1_assembly_report.txt",
     "GCF_049306965.1ToGCA_903684865.1.over.chain.gz"),
    ("fDreTuH1.2", "GCA_903684855.2", "GCA_903684855.2_fDreTuH1.2_assembly_report.txt",
     "GCF_049306965.1ToGCA_903684855.2.over.chain.gz"),
]


def read_report(name):
    """{登录号: (序列名, 角色)}，GenBank 与 RefSeq 两种写法都收。"""
    m = {}
    for line in open(D / name, encoding="utf-8", errors="replace"):
        if line.startswith("#"):
            continue
        p = line.rstrip("\n").split("\t")
        if len(p) < 7:
            continue
        for acc in (p[4], p[6]):
            if acc and acc != "na":
                m[acc] = (p[0], p[1])
    return m


def lift(path, tnames, start, end, min_cov=0.30):
    """把 [start,end) 从 t 投到 q，返回 (cov, qname, qs, qe) 或 None。

    两个坑：块行是「无缩进、Tab 分隔」的（不是 UCSC 原生的行首空格）；
    每块之后 q 也要推进 size，只加 dq 会把坐标越算越小。
    """
    best = None
    cur = None
    with gzip.open(path, "rt", errors="replace") as f:
        for line in f:
            if line.startswith("chain"):
                if cur and cur["blocks"]:
                    best = _better(best, _apply(cur, start, end))
                h = line.split()
                cur = ({"q": h[7], "qsize": int(h[8]), "qstrand": h[9],
                        "tpos": int(h[5]), "qpos": int(h[10]), "blocks": []}
                       if h[2] in tnames else None)
            elif cur is not None:
                p = line.split()
                if not p or not p[0].isdigit():
                    continue
                size = int(p[0])
                cur["blocks"].append((cur["tpos"], cur["qpos"], size))
                cur["tpos"] += size
                cur["qpos"] += size
                if len(p) == 3:
                    cur["tpos"] += int(p[1])
                    cur["qpos"] += int(p[2])
    if cur and cur["blocks"]:
        best = _better(best, _apply(cur, start, end))
    return best if best and best[0] >= min_cov else None


def _apply(ch, start, end):
    acc = {}
    cov = 0
    for tpos, qpos, size in ch["blocks"]:
        lo, hi = max(start, tpos), min(end, tpos + size)
        if hi <= lo:
            continue
        cov += hi - lo
        if ch["qstrand"] == "+":
            qs, qe = qpos + (lo - tpos), qpos + (hi - tpos)
        else:
            qs = ch["qsize"] - qpos - (hi - tpos)
            qe = ch["qsize"] - qpos - (lo - tpos)
        d = acc.setdefault(ch["q"], [10 ** 12, 0])
        d[0] = min(d[0], qs)
        d[1] = max(d[1], qe)
    if not acc:
        return None
    prim = {k: v for k, v in acc.items() if "_alt" not in k and "_fix" not in k}
    pool = prim or acc
    qname, (qs, qe) = max(pool.items(), key=lambda kv: kv[1][1] - kv[1][0])
    return (cov / (end - start), qname, qs, qe)


def _better(a, b):
    """两条候选链比较：主序列优先，其次看覆盖度。

    同一位点常同时命中主序列与 _alt/未定位 scaffold 上的旁系同源，
    只按覆盖度选会把 cyp19a1a 投到 chr18_KZ115581v1_alt 上去（自检因此 5/6）。
    """
    if b is None:
        return a
    if a is None:
        return b
    pa = "_alt" not in a[1] and "_fix" not in a[1]
    pb = "_alt" not in b[1] and "_fix" not in b[1]
    if pa != pb:
        return a if pa else b
    return b if b[0] > a[0] else a


# chr5 -> NC_133180.1（源端两种命名都要能用）
nc_of = {}
for acc, (seq, _role) in read_report("GCF_049306965.2_GRCz12tu_assembly_report.txt").items():
    if acc.startswith("NC_") and seq.startswith("chr"):
        nc_of[seq] = acc

def _norm(seq):
    """'chr5' / '5' / 'SUPER_5' -> '5'，用于同染色体判定。"""
    t = str(seq).replace("SUPER_", "").replace("chr", "")
    return t.lstrip("0") or t


out = {"selfcheck": {}, "strains": {}}
p11 = D / "GCF_049306965.1ToDanRer11.over.chain.gz"

print("== 自检：GRCz12tu -> GRCz11（要求覆盖已知注释区间）")
ok = 0
for g, (cn, s, e) in sorted(GENES.items()):
    r = lift(p11, {nc_of[cn], cn}, s, e)
    exp = EXPECT_GRCZ11[g]
    hit = bool(r) and r[1] == exp[0] and r[2] <= exp[1] + 2000 and r[3] >= exp[2] - 2000
    ok += hit
    out["selfcheck"][g] = {"cov": round(r[0], 3) if r else None,
                           "got": [r[1], r[2], r[3]] if r else None,
                           "expect": list(exp), "ok": hit}
    print(f"   {g:9s} cov={out['selfcheck'][g]['cov']} {out['selfcheck'][g]['got']} "
          f"期望 {exp} {'OK' if hit else 'MISMATCH'}")
print(f"   -> {ok}/6\n")

for name, acc, rep, chain in STRAINS:
    rmap = read_report(rep)
    res = {}
    for g, (cn, s, e) in sorted(GENES.items()):
        if chain:
            r = lift(D / chain, {nc_of[cn], cn}, s, e)
            cov = r[0] if r else None
        else:
            h1 = lift(p11, {nc_of[cn], cn}, s, e)
            h2 = lift(D / ("danRer11To%s.over.chain.gz" % acc), {h1[1]}, h1[2], h1[3]) if h1 else None
            r = h2
            cov = min(h1[0], h2[0]) if (h1 and h2) else None
        if not r:
            res[g] = None
            continue
        seq, role = rmap.get(r[1], (r[1] if r[1].startswith(("chr", "SUPER_")) else "?", "?"))
        res[g] = {"seq": r[1], "chrom": seq, "role": role,
                  "start": r[2], "end": r[3], "cov": round(cov, 3),
                  "same_chrom": _norm(seq) == _norm(cn)}
    out["strains"][name] = res
    good = sum(1 for v in res.values() if v and v["same_chrom"])
    print(f"== {name}: 同染色体且可投 {good}/6")
    for g, v in res.items():
        if v:
            print(f"   {g:9s} {v['seq']}({v['chrom']}) {v['start']:,}-{v['end']:,} "
                  f"cov={v['cov']} {'OK' if v['same_chrom'] else '**异染色体**'}")
        else:
            print(f"   {g:9s} FAIL")

print("\n@@JSON@@")
print(json.dumps(out, ensure_ascii=False))
