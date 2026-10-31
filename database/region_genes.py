# -*- coding: utf-8 -*-
"""区间对照：某装配某区间覆盖的基因，以及染色体对应的 RefSeq accession。

用途：Liftover 结果页在给出目标区间时，同时给出「这段落在哪个基因上」「落在哪条
参考序列上」，便于用户核对「同坐标≠同基因」（不同品系装配之间常有片段级增删，
同一段坐标在两边对应的基因会不一样）。

注释来源：gene_assembly_xref（GRCz11 / GRCz12tu 两个装配）。品系装配（T5D / AB /
Tu 等）本机没有基因注释，返回空列表，不猜。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

ASSEMBLY_REPORTS = {
    "GRCz11": "GCA_000002035.4_GRCz11_assembly_report.txt",
    "GRCz12tu": "GCF_049306965.2_GRCz12tu_assembly_report.txt",
    "GRCz12ab": "GCA_052040795.1_GRCz12ab_assembly_report.txt",
}


def _conn():
    import pymysql

    from config import DB_CONFIG

    cfg = dict(DB_CONFIG)
    cfg["charset"] = "utf8mb4"
    return pymysql.connect(**cfg)


def _norm_chrom(chrom: str) -> list[str]:
    """染色体名归一：'chr5' / '5' / 'CP068739.1' 都收，返回可匹配的候选写法。"""
    c = (chrom or "").strip()
    if not c:
        return []
    bare = c[3:] if c.lower().startswith("chr") else c
    if bare.upper() in {"M", "MT"}:
        bare = "MT"
    return [c, bare, "chr" + bare]


def genes_in_span(assembly: str, chrom: str, start: int, end: int, limit: int = 3) -> list[dict[str, Any]]:
    """返回与该区间重叠的基因（按起点排序，最多 limit 个）。

    两个来源合并：
      · gene_region_annotation —— GRCz12tu 的 GFF 原生坐标，以及投影到 GRCz11 的坐标（覆盖全）
      · gene_assembly_xref —— 早期从基因卡导入的 GRCz11 坐标（数量少，但含 GFF 投影漏掉的条目）
    """
    if not assembly or start is None or end is None or end < start:
        return []
    cands = _norm_chrom(chrom)
    if not cands:
        return []
    queries = (
        ("SELECT symbol, ncbi_gene_id, start_pos, end_pos, strand FROM gene_region_annotation "
         "WHERE assembly_name=%s AND chromosome IN (%s,%s,%s) "
         "AND start_pos<=%s AND end_pos>=%s ORDER BY start_pos LIMIT %s"),
        ("SELECT gene_symbol, ncbi_gene_id, start_pos, end_pos, strand FROM gene_assembly_xref "
         "WHERE assembly_name=%s AND chromosome IN (%s,%s,%s) "
         "AND start_pos IS NOT NULL AND end_pos IS NOT NULL "
         "AND start_pos<=%s AND end_pos>=%s ORDER BY start_pos LIMIT %s"),
    )
    try:
        conn = _conn()
    except Exception:
        return []
    seen: set[tuple[str, int]] = set()
    out: list[dict[str, Any]] = []
    try:
        with conn.cursor() as cur:
            for sql in queries:
                try:
                    cur.execute(sql, (assembly, cands[0], cands[1], cands[2], end, start, limit))
                except Exception:
                    continue  # 表还没建/列缺失时跳过这一路，不影响另一路
                for sym, gid, s, e, strand in cur.fetchall():
                    key = ((sym or ""), int(s))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({
                        "symbol": sym or "",
                        "gene_id": str(gid) if gid else "",
                        "start": int(s),
                        "end": int(e),
                        "strand": strand or "",
                    })
    finally:
        conn.close()
    out.sort(key=lambda g: g["start"])
    return [g for g in out if g["symbol"]][:limit]


@lru_cache(maxsize=8)
def _report_map(assembly: str) -> dict[str, str]:
    """display chrom → 主 accession（RefSeq 优先）。"""
    try:
        from coord_liftover import CHAIN_DIR, _load_assembly_report_maps
    except ImportError:  # 从仓库根目录导入时（scripts 侧）
        from database.coord_liftover import CHAIN_DIR, _load_assembly_report_maps

    name = ASSEMBLY_REPORTS.get(assembly or "")
    if not name:
        return {}
    _, disp_to_acc = _load_assembly_report_maps(CHAIN_DIR / name)
    return disp_to_acc


def chrom_accession(assembly: str, chrom: str) -> str:
    """该装配该染色体的参考序列登录号，如 GRCz11 chr5 → NC_007116.7。"""
    m = _report_map(assembly or "")
    if not m:
        return ""
    c = (chrom or "").strip()
    bare = c[3:] if c.lower().startswith("chr") else c
    for key in (c, bare, "chr" + bare):
        if key in m:
            return m[key]
    return ""
