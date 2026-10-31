# -*- coding: utf-8 -*-
"""GRCz12tu 非冗余功能基因主表查询。

数据来源：gene_master_grcz12tu / gene_id_map_grcz12tu（由 scripts 侧
「标识符统一映射到 GRCz12tu」管线产出，见 .workbuddy/tmp/step1~step7）。

映射方法（可信度由高到低）：
    id_direct      NCBI Gene ID 直接对上 GRCz12tu 的基因
    assembly_xref  库内既有的 GRCz12tu 装配坐标
    blast_100      序列比对 identity ≥ 99.5%
    blast          序列比对命中
    synteny        共线性投影（GRCz11 坐标经 chain 投影后区间重叠）
    symbol_match   标准符号精确匹配
    legacy_synteny 老版本（Zv8/Zv9/GRCz10）坐标多跳投影
"""

from __future__ import annotations

from typing import Any

from config import DB_CONFIG

METHOD_LABEL = {
    "id_direct": "NCBI Gene ID 直连",
    "assembly_xref": "库内既有 GRCz12tu 坐标",
    "blast_100": "序列比对（identity≥99.5%）",
    "blast": "序列比对",
    "synteny": "共线性投影",
    "symbol_match": "符号精确匹配",
    "legacy_synteny": "老版本坐标多跳投影",
}

METHOD_RANK = {
    "id_direct": 100, "assembly_xref": 95, "blast_100": 90, "blast": 70,
    "synteny": 60, "symbol_match": 50, "legacy_synteny": 40,
}


def _conn():
    import pymysql

    cfg = dict(DB_CONFIG)
    cfg["charset"] = "utf8mb4"   # DB_CONFIG 可能已带 charset，这里覆盖而非重复传参
    return pymysql.connect(**cfg)


def master_stats() -> dict[str, Any]:
    """主表概览：总基因数、功能基因数、覆盖情况、各方法命中数。"""
    cn = _conn()
    try:
        cur = cn.cursor()
        cur.execute("SELECT COUNT(*) FROM gene_master_grcz12tu")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM gene_master_grcz12tu WHERE is_functional=1")
        functional = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM gene_master_grcz12tu WHERE n_sources>0")
        with_source = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM gene_master_grcz12tu WHERE is_functional=1 AND n_sources>0")
        func_with_source = cur.fetchone()[0]
        cur.execute("""SELECT biotype, COUNT(*) n FROM gene_master_grcz12tu
                       WHERE is_functional=1 GROUP BY biotype ORDER BY n DESC""")
        biotypes = [{"biotype": b, "n": n} for b, n in cur.fetchall()]
        cur.execute("""SELECT method, COUNT(*) n FROM gene_id_map_grcz12tu
                       GROUP BY method ORDER BY n DESC""")
        methods = [{"method": m, "label": METHOD_LABEL.get(m, m), "n": n}
                   for m, n in cur.fetchall()]
        cur.execute("SELECT COUNT(*) FROM gene_id_map_grcz12tu")
        mapped_sources = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM gene_basic")
        total_sources = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM gene_master_grcz12tu WHERE n_sources>1")
        merged = cur.fetchone()[0]
        return {
            "ok": True,
            "reference": "GRCz12tu (GCF_049306965.2)",
            "master_total": total,
            "master_with_local_source": with_source,
            "functional_total": functional,
            "functional_with_source": func_with_source,
            "merged_multi_source": merged,
            "source_genes_total": total_sources,
            "source_genes_mapped": mapped_sources,
            "source_genes_unmapped": max(0, total_sources - mapped_sources),
            "coverage_pct": round(100.0 * mapped_sources / max(1, total_sources), 1),
            "functional_biotypes": biotypes,
            "methods": methods,
        }
    finally:
        cn.close()


def master_search(q: str = "", functional_only: bool = True, biotype: str = "",
                  chrom: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """按符号 / ID / 别名检索主表。"""
    cn = _conn()
    try:
        cur = cn.cursor()
        where, args = [], []
        if q:
            where.append("(symbol LIKE %s OR grcz12tu_gene_id LIKE %s OR ensembl_ids LIKE %s "
                         "OR zfin_ids LIKE %s OR uniprot_ids LIKE %s OR aliases LIKE %s)")
            like = "%" + q + "%"
            args += [like] * 6
        if functional_only:
            where.append("is_functional=1")
        if biotype:
            where.append("biotype=%s")
            args.append(biotype)
        if chrom:
            where.append("chrom=%s")
            args.append(chrom)
        sql = "SELECT COUNT(*) FROM gene_master_grcz12tu"
        if where:
            sql += " WHERE " + " AND ".join(where)
        cur.execute(sql, args)
        total = cur.fetchone()[0]

        sql = ("SELECT grcz12tu_gene_id, symbol, biotype, chrom, start_pos, end_pos, strand, "
               "n_sources, ensembl_ids, zfin_ids, uniprot_ids, n_go, n_kegg, best_method "
               "FROM gene_master_grcz12tu")
        if where:
            sql += " WHERE " + " AND ".join(where)
        # 有关键词时按「符号精确 → 符号前缀 → GO 数」排，否则按 GO 数（浏览模式）
        if q:
            sql += " ORDER BY (symbol=%s) DESC, (symbol LIKE %s) DESC, n_go DESC, symbol LIMIT %s OFFSET %s"
            extra = [q, q + "%"]
        else:
            sql += " ORDER BY n_go DESC, symbol LIMIT %s OFFSET %s"
            extra = []
        cur.execute(sql, args + extra + [max(1, min(int(limit), 200)), max(0, int(offset))])
        cols = ["gene_id", "symbol", "biotype", "chrom", "start", "end", "strand",
                "n_sources", "ensembl_ids", "zfin_ids", "uniprot_ids", "n_go", "n_kegg", "method"]
        items = []
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            d["method_label"] = METHOD_LABEL.get(d["method"], d["method"])
            items.append(d)
        return {"ok": True, "total": total, "items": items,
                "limit": limit, "offset": offset}
    finally:
        cn.close()


def resolve_identifier(name: str) -> dict[str, Any]:
    """给定任意本地标识符（基因名 / 别名 / Ensembl / ZFIN / UniProt / NCBI ID），
    返回它在 GRCz12tu 上的落点与映射依据。"""
    if not name:
        return {"ok": False, "error": "empty"}
    cn = _conn()
    try:
        cur = cn.cursor()
        cur.execute("""SELECT gene_id, gene_name, gene_symbol, ensembl_id, zfin_id, uniprot_id
                       FROM gene_basic WHERE gene_name=%s OR gene_symbol=%s LIMIT 5""", (name, name))
        base = cur.fetchall()
        src_ids = [r[0] for r in base]
        if not src_ids:
            cur.execute("""SELECT DISTINCT gene_id FROM gene_alias WHERE alias=%s LIMIT 5""", (name,))
            src_ids = [r[0] for r in cur.fetchall()]
        if not src_ids:
            cur.execute("""SELECT DISTINCT gene_id FROM gene_xref WHERE db_id=%s LIMIT 5""", (name,))
            src_ids = [r[0] for r in cur.fetchall()]
        if not src_ids:
            return {"ok": False, "error": "not_found", "query": name}

        ph = ",".join(["%s"] * len(src_ids))
        cur.execute("""SELECT source_gene_id, source_name, grcz12tu_gene_id, grcz12tu_symbol,
                              method, score FROM gene_id_map_grcz12tu WHERE source_gene_id IN (%s)""" % ph,
                    src_ids)
        maps = cur.fetchall()
        if not maps:
            return {"ok": False, "error": "unmapped", "query": name, "source_gene_ids": src_ids}

        tgt_ids = [m[2] for m in maps]
        ph2 = ",".join(["%s"] * len(tgt_ids))
        cur.execute("""SELECT grcz12tu_gene_id, symbol, biotype, chrom, start_pos, end_pos, strand,
                              n_go, n_kegg, is_functional FROM gene_master_grcz12tu
                       WHERE grcz12tu_gene_id IN (%s)""" % ph2, tgt_ids)
        info = {r[0]: r for r in cur.fetchall()}

        out = []
        for sid, sname, tid, tsym, method, score in maps:
            t = info.get(tid)
            out.append({
                "source_gene_id": sid, "source_name": sname,
                "grcz12tu_gene_id": tid, "symbol": tsym,
                "method": method, "method_label": METHOD_LABEL.get(method, method), "score": score,
                "biotype": t[2] if t else "", "chrom": t[3] if t else "",
                "start": t[4] if t else None, "end": t[5] if t else None,
                "strand": t[6] if t else "", "n_go": t[7] if t else 0,
                "n_kegg": t[8] if t else 0, "is_functional": t[9] if t else 0,
            })
        out.sort(key=lambda x: -METHOD_RANK.get(x["method"], 0))
        return {"ok": True, "query": name, "hits": out, "best": out[0]}
    finally:
        cn.close()
