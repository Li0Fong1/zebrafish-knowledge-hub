# -*- coding: utf-8 -*-
"""
GO / KEGG 过表达分析（ORA）— Fisher 精确检验 + Benjamini-Hochberg FDR。

输出字段对齐 clusterProfiler 风格：GeneRatio、BgRatio、pvalue、padj、fold_enrichment。
"""
import bootstrap  # noqa: F401

import json
import os
import re
import time
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from config import KEGG_PATHWAYS_JSON
from common_utils import logger
from stats_utils import benjamini_hochberg, enrichment_metrics, fisher_enrichment_pvalue


def _client():
    from mysql_client import get_mysql_client
    return get_mysql_client()


def _load_go_annotations() -> List[dict]:
    """全量 GO 注释（不受 execute_sql 10 行限制）。"""
    from sqlalchemy import text
    client = _client()
    db = next(client.get_db())
    try:
        rows = db.execute(text(
            "SELECT g.gene_id, g.gene_name, go.go_term, go.go_category "
            "FROM gene_go go JOIN gene_basic g ON go.gene_id = g.gene_id"
        )).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


def _resolve_gene_ids(gene_names: List[str]) -> Tuple[List[int], List[str], List[str]]:
    """解析基因名 → gene_id，返回 (ids, resolved_names, not_found)。

    2026-11-07：用户可能直接贴 ID 而不是符号（「一个 gene id 直接输出该基因的注释」），
    所以除符号外还直配 gene_basic.ensembl_id / zfin_id，并容忍 Ensembl 的版本后缀
    （ENSDARG00000035559.5 → ENSDARG00000035559）。
    """
    from gene_toolkit import normalize_gene_name
    from config import NCBI_REFERENCE_ASSEMBLY
    from sqlalchemy import text

    ids: List[int] = []
    resolved: List[str] = []
    not_found: List[str] = []
    client = _client()
    db = next(client.get_db())
    try:
        for raw in gene_names:
            token = (raw or "").strip()
            if not token:
                continue
            name = normalize_gene_name(token)
            if not name:
                continue
            esc = name.replace("'", "''")
            # ID 直配：原样 token，外加去掉 Ensembl 版本后缀的形态
            id_forms = [token]
            if "." in token:
                id_forms.append(token.split(".")[0])
            id_clause = " OR ".join(
                "ensembl_id='%s' OR zfin_id='%s'" % (f.replace("'", "''"), f.replace("'", "''"))
                for f in id_forms
            )
            row = db.execute(text(
                f"SELECT gene_id, gene_name FROM gene_basic "
                f"WHERE gene_name='{esc}' OR gene_symbol='{esc}' OR {id_clause} LIMIT 1"
            )).mappings().first()
            if not row:
                asm = str(NCBI_REFERENCE_ASSEMBLY).replace("'", "''")
                row = db.execute(text(
                    "SELECT gb.gene_id, gb.gene_name FROM gene_assembly_xref ax "
                    "JOIN gene_basic gb ON ax.gene_id = gb.gene_id "
                    f"WHERE ax.assembly_name='{asm}' AND ax.gene_symbol='{esc}' LIMIT 1"
                )).mappings().first()
            if row:
                ids.append(row["gene_id"])
                resolved.append(row["gene_name"])
            else:
                not_found.append(token)
    finally:
        db.close()
    return ids, resolved, not_found


def _ora_test(
    term_genes: Set[int],
    target_ids: Set[int],
    universe: Set[int],
    min_count: int = 2,
) -> Optional[dict]:
    N = len(universe)
    M = len(term_genes & universe)
    n = len(target_ids & universe)
    k = len(term_genes & target_ids & universe)
    if k < min_count or n == 0 or M == 0:
        return None
    p = fisher_enrichment_pvalue(k, n, M, N)
    fold, gene_ratio, bg_ratio = enrichment_metrics(k, n, M, N)
    return {
        "gene_count": k,
        "study_size": n,
        "term_size": M,
        "universe_size": N,
        "gene_ratio": gene_ratio,
        "bg_ratio": bg_ratio,
        "fold_enrichment": fold,
        "pvalue": p,
    }


# ---------------------------------------------------------------------------
# 方面/来源标注（2026-10-10）：富集结果要能直接看出「富的是哪一方面」
#   · GO  => 三个方面 BP 生物学过程 / CC 细胞组分 / MF 分子功能
#   · 通路 => 三个库 KEGG dre / WikiPathways / Reactome
# 库里的取值不完全统一（go_category 可能为空，source 可能是 kegg_rest 等），
# 所以这里做一层「归一 + 兜底 + 外链」，未知取值也照原样展示，不猜不丢。
# ---------------------------------------------------------------------------
_GO_ASPECTS = {
    "bp": ("BP", "生物学过程", "Biological Process"),
    "p": ("BP", "生物学过程", "Biological Process"),
    "process": ("BP", "生物学过程", "Biological Process"),
    "biological_process": ("BP", "生物学过程", "Biological Process"),
    "生物过程": ("BP", "生物学过程", "Biological Process"),
    "生物学过程": ("BP", "生物学过程", "Biological Process"),
    "cc": ("CC", "细胞组分", "Cellular Component"),
    "c": ("CC", "细胞组分", "Cellular Component"),
    "component": ("CC", "细胞组分", "Cellular Component"),
    "cellular_component": ("CC", "细胞组分", "Cellular Component"),
    "细胞组分": ("CC", "细胞组分", "Cellular Component"),
    "细胞成分": ("CC", "细胞组分", "Cellular Component"),
    "mf": ("MF", "分子功能", "Molecular Function"),
    "f": ("MF", "分子功能", "Molecular Function"),
    "function": ("MF", "分子功能", "Molecular Function"),
    "molecular_function": ("MF", "分子功能", "Molecular Function"),
    "分子功能": ("MF", "分子功能", "Molecular Function"),
}

_GO_ID_RE = re.compile(r"\bGO:\d{7}\b")


def _go_aspect(category: str) -> tuple[str, str, str]:
    """GO 方面归一：(代码, 中文名, 英文名)。无法判断时返回 ('NA', '方面未标注', '')。"""
    c = (category or "").strip().lower()
    if c in _GO_ASPECTS:
        return _GO_ASPECTS[c]
    if c:
        # 库里出现没见过的写法：原样展示，不硬塞进三分类
        return ("NA", f"方面未标注（库内取值：{category.strip()[:24]}）", "")
    return ("NA", "方面未标注", "")


def _go_id_and_name(term: str) -> tuple[str, str]:
    """从 go_term 里拆出 GO ID 与名称（库里有时只存 ID，有时只存名称）。"""
    raw = (term or "").strip()
    m = _GO_ID_RE.search(raw)
    go_id = m.group(0) if m else ""
    name = _GO_ID_RE.sub("", raw).strip(" ;:,-")
    name = re.sub(r"\s{2,}", " ", name)
    return go_id, name


def _go_url(go_id: str) -> str:
    return f"https://www.ebi.ac.uk/QuickGO/term/{go_id}" if go_id else ""


_PATHWAY_SOURCES = {
    "kegg": ("KEGG", "KEGG 通路"),
    "kegg_rest": ("KEGG", "KEGG 通路（REST 实时取回）"),
    "kegg_dre": ("KEGG", "KEGG 通路"),
    "wikipathways": ("WikiPathways", "WikiPathways 通路"),
    "wiki": ("WikiPathways", "WikiPathways 通路"),
    "reactome": ("Reactome", "Reactome 反应通路"),
    "r-dre": ("Reactome", "Reactome 反应通路"),
}


def _pathway_source_of(source: str, pathway_id: str) -> tuple[str, str, str]:
    """通路来源归一：优先 source 列，其次按 ID 前缀兜底。

    返回 (代码, 展示名, 全称)；都用不上时给 '未标注'。
    """
    s = (source or "").strip().lower()
    if s in _PATHWAY_SOURCES:
        code, label = _PATHWAY_SOURCES[s]
        return code, label, code
    pid = (pathway_id or "").strip()
    if pid.upper().startswith("R-DRE-") or pid.upper().startswith("R-"):
        return "Reactome", "Reactome 反应通路", "Reactome"
    if pid.upper().startswith("WP"):
        return "WikiPathways", "WikiPathways 通路", "WikiPathways"
    if re.match(r"(?i)^(dre|ko|map|hsa)\d{5}$", pid):
        return "KEGG", "KEGG 通路", "KEGG"
    if s:
        return s, f"来源：{source.strip()[:24]}", s
    return "NA", "来源未标注", ""


def _pathway_url(pathway_id: str, source_code: str) -> str:
    pid = (pathway_id or "").strip()
    if not pid:
        return ""
    if source_code == "Reactome":
        return f"https://reactome.org/content/detail/{pid}"
    if source_code == "WikiPathways":
        base = pid if pid.upper().startswith("WP") else pid
        return f"https://www.wikipathways.org/pathways/{base}.html"
    if source_code == "KEGG":
        return f"https://www.kegg.jp/entry/{pid}"
    return ""


def go_enrichment(
    gene_names: Optional[List[str]] = None,
    top_n: int = 20,
    padj_cutoff: float = 0.05,
    min_gene_count: int = 2,
    category: Optional[str] = None,
) -> dict:
    """GO ORA 富集（Fisher + BH-FDR）。"""
    try:
        bg_rows = _load_go_annotations()
        if not bg_rows:
            return {"success": False, "error": "GO 注释为空，请先运行 enrich 深度注释", "terms": []}

        term_to_genes: Dict[str, Set[int]] = defaultdict(set)
        term_category: Dict[str, str] = {}
        term_to_names: Dict[str, Set[str]] = defaultdict(set)
        universe: Set[int] = set()

        for r in bg_rows:
            gid = r["gene_id"]
            term = r["go_term"]
            universe.add(gid)
            term_to_genes[term].add(gid)
            term_to_names[term].add(r["gene_name"])
            if r.get("go_category"):
                term_category[term] = r["go_category"]

        if gene_names:
            target_list, resolved, not_found = _resolve_gene_ids(gene_names)
            target_ids = set(target_list)
            if not target_ids:
                return {"success": False, "error": f"未找到指定基因：{not_found}", "terms": []}
        else:
            target_ids = universe
            resolved = []
            not_found = []

        raw_terms = []
        for term, tset in term_to_genes.items():
            cat = term_category.get(term, "")
            if category and cat.upper() != category.upper():
                continue
            mc = 1 if (gene_names and len(target_ids) < 8) else min_gene_count
            stat = _ora_test(tset, target_ids, universe, min_count=mc)
            if not stat:
                continue
            overlap_gids = tset & target_ids
            overlap_names = sorted(
                {r["gene_name"] for r in bg_rows if r["gene_id"] in overlap_gids}
            )[:20]
            stat.update({
                "go_term": term,
                "go_category": cat,
                "genes": overlap_names,
            })
            # 方面标注：把这行结论「说的是哪一方面」讲清楚
            go_id, go_name = _go_id_and_name(term)
            code, label, full = _go_aspect(cat)
            stat.update({
                "go_id": go_id,
                "term_name": go_name or term,
                "aspect": code,
                "aspect_label": label,
                "aspect_full": full,
                "url": _go_url(go_id),
                "name_missing": (not go_name) and bool(go_id),
            })
            raw_terms.append(stat)

        if not raw_terms:
            return {
                "success": True,
                "type": "GO",
                "method": "ORA Fisher + BH-FDR",
                "input_genes": resolved or ["全部 GO 注释基因"],
                "not_found": not_found,
                "target_gene_count": len(target_ids),
                "universe_size": len(universe),
                "terms": [],
                "significant_count": 0,
                "aspect_summary": [],
            }

        pvalues = [t["pvalue"] for t in raw_terms]
        padjs = benjamini_hochberg(pvalues)
        for t, adj in zip(raw_terms, padjs):
            t["padj"] = round(adj, 6)

        raw_terms.sort(key=lambda x: (x["padj"], x["pvalue"], -x["fold_enrichment"]))
        sig = [t for t in raw_terms if t["padj"] <= padj_cutoff]
        display = sig[:top_n] if sig else raw_terms[:top_n]

        logger.info(f"[富集] GO ORA：{len(sig)} 项 padj≤{padj_cutoff}")
        counts: Dict[str, int] = {}
        for t in display:
            k = t.get("aspect_label") or "方面未标注"
            counts[k] = counts.get(k, 0) + 1
        aspect_summary = [
            {"label": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
        ]
        return {
            "success": True,
            "type": "GO",
            "method": "ORA Fisher + BH-FDR",
            "input_genes": resolved or gene_names or ["全部 GO 注释基因"],
            "not_found": not_found,
            "target_gene_count": len(target_ids),
            "universe_size": len(universe),
            "padj_cutoff": padj_cutoff,
            "terms": display,
            "significant_count": len(sig),
            "total_tested": len(raw_terms),
            "aspect_summary": aspect_summary,
        }
    except Exception as e:
        logger.error(f"[富集] GO 失败: {e}")
        return {"success": False, "error": str(e), "terms": []}


# ---------------------------------------------------------------------------
# 单基因注释（2026-11-07）
#   用户反馈 GO 页与 KEGG 页「逻辑不统一」：GO 收基因列表做富集，KEGG 收通路关键词。
#   统一后的口径（GO 与 KEGG 完全一致）：
#     · 输入 1 个基因  → 该基因的注释明细（这里），不做任何统计
#     · 输入 ≥2 个基因 → ORA 富集（go_enrichment / kegg_enrichment）
#   单基因做 Fisher 检验没有统计意义（前景 n=1，每个条目 GeneRatio 都是 1/n），
#   所以宁可给注释清单，也不给一堆 p=1 的伪显著结果。
# ---------------------------------------------------------------------------
_ASPECT_ORDER = {"BP": 0, "CC": 1, "MF": 2}


def _split_genes(gene_names) -> List[str]:
    """统一清洗入参：接受 list 或逗号/分号/空白分隔的字符串。"""
    if gene_names is None:
        return []
    if isinstance(gene_names, str):
        raw = re.split(r"[,，;；\s]+", gene_names)
    else:
        raw = []
        for g in gene_names:
            raw.extend(re.split(r"[,，;；\s]+", str(g)))
    return [g.strip() for g in raw if g and g.strip()]


def _go_rows_for_genes(gene_names: List[str]) -> Tuple[List[dict], List[str], List[str]]:
    """定向取若干基因的 GO 注释行，返回 (rows, resolved, not_found)。"""
    ids, resolved, not_found = _resolve_gene_ids(gene_names)
    if not ids:
        return [], resolved, not_found
    from sqlalchemy import text
    id_list = ",".join(str(int(i)) for i in ids)  # int 强转，无注入面
    client = _client()
    db = next(client.get_db())
    try:
        rows = db.execute(text(
            "SELECT g.gene_id, g.gene_name, go.go_term, go.go_category "
            "FROM gene_go go JOIN gene_basic g ON go.gene_id = g.gene_id "
            f"WHERE go.gene_id IN ({id_list})"
        )).mappings().all()
        return [dict(r) for r in rows], resolved, not_found
    finally:
        db.close()


def go_annotation(gene_names) -> dict:
    """单个基因的 GO 注释明细（不做富集统计）。"""
    names = _split_genes(gene_names)
    if not names:
        return {"success": False, "mode": "annotation", "type": "GO",
                "error": "请提供基因名", "terms": []}
    try:
        rows, resolved, not_found = _go_rows_for_genes(names)
        if not resolved:
            return {"success": False, "mode": "annotation", "type": "GO",
                    "error": "未找到指定基因：%s" % "、".join(not_found or names),
                    "terms": [], "not_found": not_found}

        terms: List[dict] = []
        seen: Set[str] = set()
        for r in rows:
            term = r.get("go_term") or ""
            if not term or term in seen:
                continue
            seen.add(term)
            go_id, go_name = _go_id_and_name(term)
            code, label, full = _go_aspect(r.get("go_category") or "")
            terms.append({
                "go_id": go_id,
                "go_term": term,
                "term_name": go_name or term,
                "aspect": code,
                "aspect_label": label,
                "aspect_full": full,
                "url": _go_url(go_id),
                "name_missing": (not go_name) and bool(go_id),
            })
        terms.sort(key=lambda t: (_ASPECT_ORDER.get(t["aspect"], 3), t["term_name"]))

        counts: Dict[str, int] = {}
        for t in terms:
            counts[t["aspect_label"]] = counts.get(t["aspect_label"], 0) + 1
        return {
            "success": True,
            "mode": "annotation",
            "type": "GO",
            "gene": "、".join(resolved),
            "input_genes": resolved,
            "not_found": not_found,
            "total": len(terms),
            "terms": terms,
            "aspect_summary": [
                {"label": k, "count": v}
                for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
            ],
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"[注释] GO 单基因注释失败: {e}")
        return {"success": False, "mode": "annotation", "type": "GO",
                "error": str(e), "terms": []}


_KEGG_GLOBAL = {
    "dre01100", "dre01110", "dre01120",
    "dre01200", "dre01210", "dre01212", "dre01230", "dre01232", "dre01250",
}
_PATHWAY_CACHE: dict = {"rows": None, "ts": 0.0}


def _load_kegg_pathways() -> list:
    """通路注释：优先 gene_kegg（KEGG dre / WikiPathways / Reactome），文件兜底。"""
    cached = _PATHWAY_CACHE.get("rows")
    if cached and (time.time() - float(_PATHWAY_CACHE.get("ts") or 0)) < 120:
        return cached
    try:
        from sqlalchemy import text
        client = _client()
        db = next(client.get_db())
        try:
            rows = db.execute(text(
                "SELECT p.pathway_id, p.pathway_name, p.category, p.source, g.gene_name "
                "FROM gene_kegg p JOIN gene_basic g ON p.gene_id = g.gene_id"
            )).mappings().all()
            by = {}
            seen_genes: Dict[str, Set[str]] = {}
            for r in rows:
                pid = r["pathway_id"]
                rec = by.get(pid)
                if rec is None:
                    rec = {
                        "id": pid,
                        "name": r["pathway_name"] or pid,
                        "category": r["category"] or "",
                        "source": r["source"] or "",
                        "genes": [],
                    }
                    by[pid] = rec
                    seen_genes[pid] = set()
                src = (r["source"] or "")
                if src == "kegg_rest" and r["pathway_name"]:
                    rec["name"] = r["pathway_name"]
                    rec["category"] = r["category"] or rec["category"]
                    rec["source"] = src
                gname = r["gene_name"]
                if gname and gname not in seen_genes[pid]:
                    seen_genes[pid].add(gname)
                    rec["genes"].append(gname)
            if by:
                out = [by[k] for k in sorted(by)]
                _PATHWAY_CACHE["rows"] = out
                _PATHWAY_CACHE["ts"] = time.time()
                return out
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[富集] 从数据库读取通路失败，回退文件: {e}")
    if not os.path.isfile(KEGG_PATHWAYS_JSON):
        return []
    with open(KEGG_PATHWAYS_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def _kegg_gene_universe(pathways: list) -> Set[str]:
    genes: Set[str] = set()
    for p in pathways:
        for g in p.get("genes", []):
            genes.add(g.lower())
    return genes


def kegg_pathway_search(keyword: str = "", gene_names: Optional[List[str]] = None) -> dict:
    pathways = _load_kegg_pathways()
    if not pathways:
        return {"success": False, "error": "KEGG 通路注释文件缺失", "pathways": []}

    kw = keyword.strip().lower()
    genes = [g.strip().lower() for g in (gene_names or []) if g.strip()]
    matched = []
    for p in pathways:
        pgenes = [g.lower() for g in p.get("genes", [])]
        name = p.get("name", "").lower()
        pid = p.get("id", "")
        if genes and not any(g in pgenes for g in genes):
            if not kw or kw not in name and kw not in pid.lower():
                continue
        if kw and kw not in name and kw not in pid.lower() and not any(kw in g for g in pgenes):
            continue
        overlap = [g for g in genes if g in pgenes] if genes else []
        code, label, full = _pathway_source_of(p.get("source", ""), p.get("id", ""))
        matched.append({
            "pathway_id": p.get("id"),
            "pathway_name": p.get("name"),
            "category": p.get("category", ""),
            "source": (p.get("source") or "").strip(),
            "source_code": code,
            "source_label": label,
            "source_full": full,
            # 外链必须按来源拼：Reactome 的 R-DRE-* 拼 genome.jp 一定 404
            "url": _pathway_url(p.get("id", ""), code),
            "genes_in_pathway": pgenes[:40],
            "pathway_gene_count": len(pgenes),
            "overlap_genes": overlap,
            "overlap_count": len(overlap),
        })
    matched.sort(key=lambda x: x["overlap_count"], reverse=True)
    matched = matched[:15]
    src_counts: Dict[str, int] = {}
    for m in matched:
        k = m.get("source_label") or "来源未标注"
        src_counts[k] = src_counts.get(k, 0) + 1
    return {
        "success": True,
        "type": "KEGG",
        "pathways": matched,
        "source_summary": [
            {"label": k, "count": v}
            for k, v in sorted(src_counts.items(), key=lambda kv: -kv[1])
        ],
    }


def kegg_enrichment(
    gene_names: List[str],
    top_n: int = 15,
    padj_cutoff: float = 0.05,
    min_gene_count: int = 2,
) -> dict:
    """通路 ORA 富集（Fisher + BH-FDR）。背景为 gene_kegg（KEGG/WikiPathways/Reactome）。"""
    if not gene_names:
        return {"success": False, "error": "请提供基因列表", "pathways": []}

    pathways = _load_kegg_pathways()
    if not pathways:
        return {"success": False, "error": "通路注释缺失", "pathways": []}

    _, resolved, not_found = _resolve_gene_ids(gene_names)
    target = set(g.lower() for g in resolved)
    if not target:
        return {"success": False, "error": f"未找到基因：{not_found}", "pathways": []}

    ora_pathways = [
        p for p in pathways
        if p.get("id") not in _KEGG_GLOBAL
        and (p.get("category") or "") != "Global and overview maps"
    ]
    universe = _kegg_gene_universe(ora_pathways)
    N = len(universe)
    n = len(target & universe)
    mc = 1 if n < 8 else min_gene_count

    raw = []
    for p in ora_pathways:
        pgenes = set(g.lower() for g in p.get("genes", []))
        M = len(pgenes & universe)
        k = len(pgenes & target & universe)
        if k < mc:
            continue
        pval = fisher_enrichment_pvalue(k, n, M, N)
        fold, gene_ratio, bg_ratio = enrichment_metrics(k, n, M, N)
        raw.append({
            "pathway_id": p.get("id"),
            "pathway_name": p.get("name"),
            "category": p.get("category", ""),
            "source": (p.get("source") or "").strip(),
            "gene_count": k,
            "pathway_gene_count": len(pgenes),
            "gene_ratio": gene_ratio,
            "bg_ratio": bg_ratio,
            "fold_enrichment": fold,
            "pvalue": pval,
            "overlap_genes": sorted(pgenes & target),
        })

    # 来源标注：R-DRE-… 是 Reactome、WP… 是 WikiPathways、dre… 是 KEGG
    for item in raw:
        code, label, full = _pathway_source_of(item.get("source", ""), item.get("pathway_id", ""))
        item["source_code"] = code
        item["source_label"] = label
        item["source_full"] = full
        item["url"] = _pathway_url(item.get("pathway_id", ""), code)

    if not raw:
        return {
            "success": True,
            "type": "KEGG",
            "method": "ORA Fisher + BH-FDR",
            "input_genes": resolved,
            "not_found": not_found,
            "pathways": [],
            "significant_count": 0,
            "source_summary": [],
        }

    padjs = benjamini_hochberg([x["pvalue"] for x in raw])
    for item, adj in zip(raw, padjs):
        item["padj"] = round(adj, 6)

    raw.sort(key=lambda x: (x["padj"], x["pvalue"], -x["fold_enrichment"]))
    sig = [x for x in raw if x["padj"] <= padj_cutoff]
    display = sig[:top_n] if sig else raw[:top_n]

    src_counts: Dict[str, int] = {}
    for x in display:
        k = x.get("source_label") or "来源未标注"
        src_counts[k] = src_counts.get(k, 0) + 1
    source_summary = [
        {"label": k, "count": v}
        for k, v in sorted(src_counts.items(), key=lambda kv: -kv[1])
    ]

    return {
        "success": True,
        "type": "KEGG",
        "method": "ORA Fisher + BH-FDR",
        "input_genes": resolved,
        "not_found": not_found,
        "target_gene_count": n,
        "universe_size": N,
        "padj_cutoff": padj_cutoff,
        "pathways": display,
        "significant_count": len(sig),
        "total_tested": len(raw),
        "source_summary": source_summary,
    }


def kegg_annotation(gene_names) -> dict:
    """单个基因的通路注释明细（不做富集统计）。"""
    try:
        return _kegg_annotation(gene_names)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[注释] KEGG 单基因注释失败: {e}")
        return {"success": False, "mode": "annotation", "type": "KEGG",
                "error": str(e), "pathways": []}


def _kegg_annotation(gene_names) -> dict:
    names = _split_genes(gene_names)
    if not names:
        return {"success": False, "mode": "annotation", "type": "KEGG",
                "error": "请提供基因名", "pathways": []}

    pathways = _load_kegg_pathways()
    if not pathways:
        return {"success": False, "mode": "annotation", "type": "KEGG",
                "error": "通路注释缺失", "pathways": []}

    _, resolved, not_found = _resolve_gene_ids(names)
    if not resolved:
        return {"success": False, "mode": "annotation", "type": "KEGG",
                "error": "未找到指定基因：%s" % "、".join(not_found or names),
                "pathways": [], "not_found": not_found}

    want = {g.lower() for g in resolved}
    hits: List[dict] = []
    for p in pathways:
        pgenes = [g.lower() for g in p.get("genes", [])]
        overlap = [g for g in want if g in pgenes]
        if not overlap:
            continue
        code, label, full = _pathway_source_of(p.get("source", ""), p.get("id", ""))
        hits.append({
            "pathway_id": p.get("id"),
            "pathway_name": p.get("name"),
            "category": p.get("category", ""),
            "source": (p.get("source") or "").strip(),
            "source_code": code,
            "source_label": label,
            "source_full": full,
            "url": _pathway_url(p.get("id", ""), code),
            "pathway_gene_count": len(pgenes),
            "matched_genes": sorted(overlap),
        })
    # 排序：KEGG 官方在前，其余按通路编号，便于对照原站
    _src_rank = {"KEGG": 0, "WikiPathways": 1, "Reactome": 2}
    hits.sort(key=lambda x: (_src_rank.get(x["source_code"], 3), str(x["pathway_id"])))

    counts: Dict[str, int] = {}
    for h in hits:
        k = h.get("source_label") or "来源未标注"
        counts[k] = counts.get(k, 0) + 1
    return {
        "success": True,
        "mode": "annotation",
        "type": "KEGG",
        "gene": "、".join(resolved),
        "input_genes": resolved,
        "not_found": not_found,
        "total": len(hits),
        "pathways": hits,
        "source_summary": [
            {"label": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
        ],
    }


def go_lookup(gene_names=None, **kwargs) -> dict:
    """统一入口：1 个基因 → GO 注释；0 或 ≥2 个基因 → GO 富集。"""
    names = _split_genes(gene_names)
    if len(names) == 1:
        return go_annotation(names)
    res = go_enrichment(names or None, **kwargs)
    res["mode"] = "enrichment"
    return res


def kegg_lookup(gene_names=None, **kwargs) -> dict:
    """统一入口：1 个基因 → 通路注释；≥2 个基因 → 通路富集。"""
    names = _split_genes(gene_names)
    if len(names) == 1:
        return kegg_annotation(names)
    res = kegg_enrichment(names, **kwargs)
    res["mode"] = "enrichment"
    return res


def format_annotation_answer(res: dict, lang: str = "zh") -> str:
    """单基因注释的文本版（聊天用）。"""
    if not res:
        return ""
    en = str(lang or "zh").lower().startswith("en")
    if not res.get("success"):
        msg = res.get("error") or ("annotation failed" if en else "注释查询失败")
        return "[Annotation] %s" % msg if en else "【注释】%s" % msg

    gene = res.get("gene") or "-"
    is_go = (res.get("type") or "").upper() == "GO"
    items = res.get("terms") if is_go else res.get("pathways")
    items = items or []
    lines: List[str] = []

    if not items:
        return (
            "[Annotation] %s has no %s record in the local library." % (gene, "GO" if is_go else "pathway")
            if en else
            "【注释】%s 在本库没有%s注释记录。" % (gene, " GO " if is_go else "通路")
        )

    if en:
        lines.append(
            "[Annotation] %s — %d %s records (single-gene annotation, no enrichment statistics)."
            % (gene, len(items), "GO" if is_go else "pathway")
        )
        # 英文分布直接从条目字段统计 —— res 里的 aspect_summary/source_summary
        # 带的是中文标签（生物学过程 / Reactome 反应通路），混进英文回答会很怪。
        buckets: Dict[str, int] = {}
        for it in items:
            k = (it.get("aspect_full") or it.get("aspect")) if is_go else (it.get("source_code") or it.get("source"))
            k = k or "unannotated"
            buckets[k] = buckets.get(k, 0) + 1
        if buckets:
            lines.append("[Breakdown] " + " | ".join("%s: %d" % (k, v) for k, v in buckets.items()))
        for i, it in enumerate(items[:30], 1):
            if is_go:
                # 英文回答不能用 aspect_label（那是中文标签），退回方面缩写/全称
                lines.append("%d. [%s] %s (%s)" % (
                    i, it.get("aspect_full") or it.get("aspect") or it.get("aspect_label") or "-",
                    it.get("term_name") or it.get("go_term") or "-", it.get("go_id") or "-"))
            else:
                lines.append("%d. [%s] %s %s" % (
                    i, it.get("source_full") or it.get("source_code") or it.get("source_label") or "-",
                    it.get("pathway_id") or "-", it.get("pathway_name") or "-"))
        if len(items) > 30:
            lines.append("... %d more (see the tool page for the full list)" % (len(items) - 30))
    else:
        lines.append(
            "【注释】%s —— 共 %d 条%s注释（单基因注释，不做富集统计）。"
            % (gene, len(items), " GO " if is_go else "通路")
        )
        summ = res.get("aspect_summary") if is_go else res.get("source_summary")
        if summ:
            lines.append("【分布】" + " · ".join("%s %d 项" % (s["label"], s["count"]) for s in summ))
        for i, it in enumerate(items[:30], 1):
            if is_go:
                lines.append("%d. [%s] %s（%s）" % (
                    i, it.get("aspect_label") or "-",
                    it.get("term_name") or it.get("go_term") or "-", it.get("go_id") or "-"))
            else:
                lines.append("%d. [%s] %s %s" % (
                    i, it.get("source_label") or "-",
                    it.get("pathway_id") or "-", it.get("pathway_name") or "-"))
        if len(items) > 30:
            lines.append("…… 其余 %d 条见工具页" % (len(items) - 30))

    nf = res.get("not_found") or []
    if nf:
        lines.append("(Not found: %s)" % ", ".join(nf) if en else "（未收录：%s）" % "、".join(nf))
    return "\n".join(lines)


def format_enrichment_answer(go_result: dict = None, kegg_result: dict = None) -> str:
    """给聊天/工具页看的文本版：每条都标出「方面」与「来源库」。"""
    lines = []
    if go_result and go_result.get("success"):
        if go_result.get("terms"):
            sig_n = go_result.get("significant_count", 0)
            tot_n = go_result.get("total_tested", "?")
            lines.append(
                f"【看的方面】GO 三个方面：BP 生物学过程 / CC 细胞组分 / MF 分子功能"
                f"（本库 GO 注释；Fisher 精确检验 + BH 校正，显著 padj≤{go_result.get('padj_cutoff', 0.05)}）"
            )
            summ = go_result.get("aspect_summary") or []
            if summ:
                lines.append(
                    "【方面分布】" + " · ".join(f"{s['label']} {s['count']} 项" for s in summ)
                )
            lines.append(
                f"\n[GO ORA] Fisher + BH-FDR | 显著：{sig_n}/{tot_n}"
                f" | 前景基因 {go_result.get('target_gene_count', '?')} 个"
                f" | 背景 {go_result.get('universe_size', '?')} 个"
            )
            for i, t in enumerate(go_result["terms"][:8], 1):
                genes = ", ".join(t.get("genes", [])[:5]) or "-"
                aspect = t.get("aspect_label") or "方面未标注"
                gid = t.get("go_id") or ""
                name = t.get("term_name") or gid or "(无名称)"
                tail = f"（{gid}）" if gid else ""
                if t.get("name_missing"):
                    tail += " 名称未收录，可点链接到 QuickGO 查看"
                lines.append(
                    f"{i}. [{aspect}] {name}{tail}\n"
                    f"   padj={t['padj']:.2e} p={t['pvalue']:.2e} | "
                    f"GeneRatio={t['gene_ratio']} BgRatio={t['bg_ratio']} | "
                    f"FC={t['fold_enrichment']} | 命中基因：{genes}"
                )
        elif go_result.get("error"):
            lines.append(f"[GO] {go_result['error']}")
        else:
            lines.append("[GO] 无显著富集项（可放宽 padj 或扩充 GO 注释）")

    if kegg_result and kegg_result.get("success"):
        if kegg_result.get("pathways"):
            lines.append(
                f"\n【看的方面】通路库：KEGG dre / WikiPathways / Reactome"
                f" | Fisher + BH | 显著：{kegg_result.get('significant_count', 0)}/"
                f"{kegg_result.get('total_tested', '?')} | 背景基因 {kegg_result.get('universe_size', '?')}"
            )
            summ = kegg_result.get("source_summary") or []
            if summ:
                lines.append(
                    "【来源分布】" + " · ".join(f"{s['label']} {s['count']} 项" for s in summ)
                )
            for i, p in enumerate(kegg_result["pathways"][:6], 1):
                genes = ", ".join(p.get("overlap_genes", [])) or "-"
                src = p.get("source_label") or "来源未标注"
                lines.append(
                    f"{i}. [{src}] {p['pathway_id']} {p['pathway_name']}\n"
                    f"   padj={p['padj']:.2e} | GeneRatio={p['gene_ratio']} | "
                    f"FC={p['fold_enrichment']} | 命中基因：{genes}"
                )
        elif kegg_result.get("error"):
            lines.append(f"[通路] {kegg_result['error']}")

    if not lines:
        return "[结果] 未找到显著富集项。请确认基因名、GO 注释（enrich）及 KEGG 数据。"
    return "\n".join(lines)


def enrichment_tsv_export(go_result: dict = None, kegg_result: dict = None) -> str:
    """导出 TSV 供发表/作图（新增「方面/来源」两列）。"""
    rows = [
        "analysis\tid\tname\taspect_or_source\tcategory\tgene_count\tgene_ratio\t"
        "bg_ratio\tfold_enrichment\tpvalue\tpadj\tgenes\turl"
    ]
    if go_result and go_result.get("terms"):
        for t in go_result["terms"]:
            genes = ";".join(t.get("genes", []))
            name = t.get("term_name") or t.get("go_term", "")
            rows.append(
                f"GO\t{t.get('go_id') or t['go_term']}\t{name}\t{t.get('aspect_label', '')}\t"
                f"{t.get('go_category', '')}\t"
                f"{t['gene_count']}\t{t['gene_ratio']}\t{t['bg_ratio']}\t{t['fold_enrichment']}\t"
                f"{t['pvalue']}\t{t['padj']}\t{genes}\t{t.get('url', '')}"
            )
    if kegg_result and kegg_result.get("pathways"):
        for p in kegg_result["pathways"]:
            genes = ";".join(p.get("overlap_genes", []))
            rows.append(
                f"KEGG\t{p['pathway_id']}\t{p['pathway_name']}\t{p.get('source_label', '')}\t"
                f"{p.get('category', '')}\t"
                f"{p['gene_count']}\t{p['gene_ratio']}\t{p['bg_ratio']}\t{p['fold_enrichment']}\t"
                f"{p['pvalue']}\t{p['padj']}\t{genes}\t{p.get('url', '')}"
            )
    return "\n".join(rows)
