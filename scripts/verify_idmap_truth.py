# -*- coding: utf-8 -*-
"""独立真值核验（2026-11-13）——刻意不 import 被测模块。

前面 324/324 清查的「chain 基准」是同一个 lift_region 算的，属自洽性验证。
本脚本用**彼此独立的数据源**交叉核对：

  源 A  Ensembl 官方 REST（外部）    tp53 GRCz11 chr5:24,086,227-24,097,805(+)
  源 B  NCBI 官方注释 gene_assembly_xref（MySQL，非 chain 推导）
  源 C  GRCz12tu 主表 gene_master_grcz12tu（MySQL，RefSeq 注释）
  源 D  离线落盘 coords 表（SQLite，UCSC chain 换算）

核验项：
  A) 官方 GRCz11 坐标 vs 系统输出（API 走 official_xref 分支）
  B) 源 B（官方 GRCz11）vs 源 D（从 GRCz12tu 主表经 chain 换算到 GRCz11）
     —— 两套独立来源的偏差量化，这是 chain 换算准不准的硬证据
  C) 落盘坐标全量健康度：格式、长度守恒、链方向、code 与 span 一致
  D) Ensembl release：zfin 覆盖率、历史 ID 与现用 ID 差异、未收录基因处理
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "core"), str(ROOT / "database")]
DB = Path(os.environ.get(
    "IDMAP_DATA_DB", "/data/zoujin/deng/zebrafish_runtime/idmap_data.sqlite3"))
SPAN_RE = re.compile(r"^([^:]+):([\d,]+)-([\d,]+)(?:\(([+-])\))?$")

EXTERNAL_TRUTH = {          # Ensembl REST lookup/id/ENSDARG00000035559
    "gene": "tp53", "ensembl": "ENSDARG00000035559", "assembly": "GRCz11",
    "chrom": "5", "start": 24086227, "end": 24097805, "strand": "+",
}


def parse_span(s):
    if not s:
        return None
    m = SPAN_RE.match(str(s))
    if not m:
        return None
    return (m.group(1), int(m.group(2).replace(",", "")),
            int(m.group(3).replace(",", "")), m.group(4) or "")


def fmt(c, s, e, st):
    return "%s:%s-%s%s" % (c, format(s, ","), format(e, ","),
                           "(%s)" % st if st in ("+", "-") else "")


def sql_rows(cur, sql, limit=None):
    cur.execute(sql)
    out = []
    while True:
        batch = cur.fetchmany(5000)
        if not batch:
            break
        out.extend(batch)
        if limit and len(out) >= limit:
            return out[:limit]
    return out


def main():
    from config import DB_CONFIG
    import pymysql

    con = pymysql.connect(host=DB_CONFIG["host"], port=DB_CONFIG["port"],
                          user=DB_CONFIG["user"], password=DB_CONFIG["password"],
                          database=DB_CONFIG["database"], charset="utf8mb4",
                          cursorclass=pymysql.cursors.DictCursor)
    sq = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    cur = con.cursor()
    print("=== 0) 独立坐标源盘点 ===", flush=True)
    rows = sql_rows(cur, "SELECT assembly_name, COUNT(*) n FROM gene_assembly_xref "
                         "GROUP BY assembly_name ORDER BY n DESC")
    print("  gene_assembly_xref 各装配行数:", [(r["assembly_name"], r["n"]) for r in rows], flush=True)

    # ---------- A) 外部真值 vs 系统 GRCz11 官方坐标 ----------
    print("\n=== A) Ensembl 官方 REST vs NCBI 官方注释（tp53, GRCz11）===", flush=True)
    cur.execute("SELECT gene_id, gene_name, ensembl_id, zfin_id FROM gene_basic "
                "WHERE gene_name LIKE '%tp53%' OR ensembl_id LIKE 'ENSDARG00000035559%' LIMIT 5")
    print("  gene_basic 里 tp53 的记录:", cur.fetchall(), flush=True)
    cur.execute("SELECT x.chromosome, x.start_pos, x.end_pos, x.strand, b.gene_name, "
                "b.ensembl_id FROM gene_assembly_xref x JOIN gene_basic b "
                "ON b.gene_id=x.gene_id WHERE x.assembly_name='GRCz11' "
                "AND (b.gene_name LIKE '%tp53%' OR b.ensembl_id LIKE 'ENSDARG00000035559%') LIMIT 3")
    got = cur.fetchall()
    for g in got:
        mine = fmt(str(g["chromosome"]), int(g["start_pos"]), int(g["end_pos"]),
                   str(g["strand"] or "+"))
        truth = fmt(EXTERNAL_TRUTH["chrom"], EXTERNAL_TRUTH["start"],
                    EXTERNAL_TRUTH["end"], EXTERNAL_TRUTH["strand"])
        print("  本地官方注释: %-34s 外部 Ensembl REST: %-34s %s" %
              (mine, truth, "MATCH" if mine == truth else "MISMATCH"), flush=True)
        c = parse_span(mine)
        print("  长度=%d bp，外部长度=%d bp" %
              (c[2] - c[1] + 1, EXTERNAL_TRUTH["end"] - EXTERNAL_TRUTH["start"] + 1),
              flush=True)

    # ---------- B) 官方 GRCz11 vs chain 换算到 GRCz11 ----------
    print("\n=== B) 官方注释 vs UCSC chain 换算（两个独立来源）===", flush=True)
    # 用「基因符号」把两套独立注释直接关联，绕开映射表
    # （上一版用 dict 覆盖 gene_id_map_grcz12tu，1,403 个是假性串基因）
    cur.execute(
        "SELECT b.gene_name, x.chromosome, x.start_pos, x.end_pos, x.strand "
        "FROM gene_assembly_xref x JOIN gene_basic b ON b.gene_id=x.gene_id "
        "WHERE x.assembly_name='GRCz11' AND x.chromosome IS NOT NULL "
        "AND x.start_pos IS NOT NULL AND x.end_pos IS NOT NULL")
    by_sym = {}
    for r in cur.fetchall():
        k = str(r["gene_name"] or "").strip().lower()
        if k and k not in by_sym:
            by_sym[k] = r
    cur.execute("SELECT symbol, chrom, start_pos, end_pos, strand "
                "FROM gene_master_grcz12tu WHERE chrom IS NOT NULL "
                "AND start_pos IS NOT NULL AND end_pos IS NOT NULL")
    masters = cur.fetchall()
    print("  官方 GRCz11 坐标基因 %d 个（按符号去重 %d），GRCz12tu 主表 %d 行" %
          (len(by_sym), len(by_sym), len(masters)), flush=True)

    checked = exact = tiny = close = bad = no_lift = 0
    diffs, all_diffs = [], []
    seen_hub = set()
    for m in masters:
        sym = str(m["symbol"] or "").strip().lower()
        o = by_sym.get(sym)
        if not o:
            continue
        hub = (str(m["chrom"]), int(m["start_pos"]), int(m["end_pos"]),
               str(m.get("strand") or "+"))
        st = hub[3] if hub[3] in ("+", "-") else "+"
        key = (hub[0], hub[1], hub[2], st)
        if key in seen_hub:
            continue
        seen_hub.add(key)
        row = sq.execute(
            "SELECT ok, code, span FROM coords WHERE hub='GRCz12tu' AND chrom=? "
            "AND start=? AND end=? AND strand=? AND target='GRCz11'",
            (hub[0], hub[1], hub[2], st)).fetchone()
        if not row:
            continue
        checked += 1
        if not row[0]:
            no_lift += 1
            continue
        lifted = parse_span(row[2])
        if not lifted:
            bad += 1
            continue
        off = (str(o["chromosome"]), int(o["start_pos"]), int(o["end_pos"]),
               str(o["strand"] or "+"))
        if lifted[0] != off[0]:
            bad += 1
            continue
        d = min(abs(lifted[1] - off[1]), abs(lifted[2] - off[2]))
        all_diffs.append(d)
        if d == 0 and lifted[2] == off[2]:
            exact += 1
        elif d <= 100:
            tiny += 1
        elif d <= 5000:
            close += 1
        else:
            bad += 1
        if d > 100 and len(diffs) < 8:
            diffs.append((sym, fmt(*off), row[2]))
    print("  同一基因可比 %d：完全一致 %d，≤100bp %d，≤5kb %d，>5kb/异染色体 %d，chain 失败 %d" %
          (checked, exact, tiny, close, bad, no_lift), flush=True)
    if all_diffs:
        ss = sorted(all_diffs)
        print("  边界偏差：中位数 %d bp，P90 %d bp，最大 %d bp" %
              (ss[len(ss) // 2], ss[int(len(ss) * 0.9)], ss[-1]), flush=True)
    if diffs:
        print("  较大偏差样本（符号 / 官方 GRCz11 / chain 换算）:", flush=True)
        for d in diffs:
            print("    ", d, flush=True)

    # ---------- C) 落盘坐标健康度 ----------
    print("\n=== C) 落盘 939,267 条坐标健康度 ===", flush=True)
    n = sq.execute("SELECT COUNT(*) FROM coords").fetchone()[0]
    codes = {r[0]: r[1] for r in sq.execute(
        "SELECT code, COUNT(*) FROM coords GROUP BY code")}
    print("  总行数 %d，code 分布 %s" % (n, codes), flush=True)
    bad_rows = sq.execute("SELECT COUNT(*) FROM coords WHERE ok=1 AND span=''").fetchone()[0]
    print("  ok=1 但 span 为空: %d" % bad_rows, flush=True)
    malformed = offlen = negstrand = 0
    sample = []
    for c, s, e, st, ok, span, hub_s, hub_e in sq.execute(
            "SELECT chrom,start,end,strand,ok,span, "
            "CAST(start AS INTEGER), CAST(end AS INTEGER) FROM coords WHERE ok=1"):
        p = parse_span(span)
        if not p:
            malformed += 1
            continue
        src_len = int(hub_e) - int(hub_s) + 1
        dst_len = p[2] - p[1] + 1
        if not (src_len / 5 <= dst_len <= src_len * 5):
            offlen += 1
            if len(sample) < 5:
                sample.append((span, src_len, dst_len))
        if p[3] == "-":
            negstrand += 1
    print("  span 格式非法 %d；长度倍率 >5x 或 <1/5x %d；负链结果 %d" %
          (malformed, offlen, negstrand), flush=True)
    if sample:
        print("  长度异常样本:", sample, flush=True)
    by_target = sq.execute(
        "SELECT target, SUM(ok), COUNT(*) FROM coords GROUP BY target ORDER BY target").fetchall()
    print("  各目标版本命中率:", flush=True)
    for t, ok, tot in by_target:
        print("    %-16s %6d/%6d  %.1f%%" % (t, ok, tot, 100.0 * ok / tot), flush=True)

    # ---------- D) Ensembl release 数据内在差异 ----------
    print("\n=== D) Ensembl release 基因集：是否真有版本差异 ===", flush=True)
    by_rel = sq.execute(
        "SELECT release, COUNT(*), SUM(CASE WHEN zfin_id IS NULL OR zfin_id='' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN current_ensembl_gene_id<>ensembl_gene_id "
        "AND current_ensembl_gene_id<>'' THEN 1 ELSE 0 END) "
        "FROM ens_release GROUP BY release ORDER BY release").fetchall()
    print("  release / 行数 / zfin 缺失 / 历史ID≠现用ID:", flush=True)
    for r in by_rel:
        print("    ", r, flush=True)
    mm = sq.execute("SELECT match_method, COUNT(*) FROM ens_release "
                    "GROUP BY match_method ORDER BY 2 DESC LIMIT 8").fetchall()
    print("  match_method 分布:", mm, flush=True)
    cur_ids = sq.execute("SELECT COUNT(*) FROM ens_current").fetchone()[0]
    print("  r116 当前集行数:", cur_ids, flush=True)
    diff = sq.execute(
        "SELECT ensembl_gene_id, gene_symbol, current_ensembl_gene_id FROM ens_release "
        "WHERE current_ensembl_gene_id<>ensembl_gene_id AND current_ensembl_gene_id<>'' "
        "LIMIT 5").fetchall()
    print("  历史 ID 变化样本:", diff, flush=True)
    only_cur = sq.execute(
        "SELECT COUNT(*) FROM ens_current c WHERE NOT EXISTS "
        "(SELECT 1 FROM ens_release r WHERE r.release=54 AND r.ensembl_gene_id=c.ensembl_gene_id)"
    ).fetchone()[0]
    print("  r116 有、r54 没有的基因数（应标 not_in_release）:", only_cur, flush=True)
    dup = sq.execute(
        "SELECT COUNT(*) FROM (SELECT release, gene_symbol FROM ens_release "
        "WHERE gene_symbol<>'' GROUP BY release, gene_symbol HAVING COUNT(*)>1)").fetchone()[0]
    print("  同一 release 内 gene_symbol 重复的组数（symbol 回退匹配可能误配）:", dup, flush=True)
    unmapped = sq.execute(
        "SELECT COUNT(*), SUM(CASE WHEN zfin_id IS NULL OR zfin_id='' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN current_ensembl_gene_id IS NULL OR current_ensembl_gene_id='' "
        "THEN 1 ELSE 0 END) FROM ens_release WHERE match_method='unmapped'").fetchone()
    print("  unmapped 行 / zfin 为空 / current_id 为空:", unmapped, flush=True)

    con.close()
    sq.close()
    print("\n=== 核验完成 ===", flush=True)


if __name__ == "__main__":
    main()
