# -*- coding: utf-8 -*-
"""斑马鱼基因工具集（参照 Potato Knowledge Hub 的 BLAST/ID转换/注释工具，本地轻量实现）。"""
import bootstrap  # noqa: F401

import json
import re
from typing import Optional

from common_utils import logger


def _client():
    from mysql_client import get_mysql_client
    return get_mysql_client()


def normalize_gene_name(name: str) -> str:
    """基因名规范化：优先别名索引，其次手工映射。"""
    from gene_id_mapper import KNOWN_ALIASES, resolve_to_gene_name

    raw = name.strip()
    if not raw:
        return raw
    resolved = resolve_to_gene_name(raw)
    if resolved:
        return resolved
    key = raw.lower()
    return KNOWN_ALIASES.get(key, key)


_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _detail_from_gene_card(name: str) -> Optional[dict]:
    """gene_basic 没有时，仍用 gene_card.symbol 回答。数字开头的符号也走这里。"""
    key = (name or "").strip().lower()
    if not key:
        return None
    res = _client().execute_sql(
        "SELECT zfin_id, symbol, ncbi_gene_id, ensembl_gene_id, chrom_grcz11 "
        "FROM zebrafish_gene_v2_20260922.gene_card "
        "WHERE LOWER(symbol) = :low LIMIT 1",
        params={"low": key},
    )
    if not res.get("success") or not res.get("data"):
        return None
    row = res["data"][0]
    symbol = row.get("symbol") or key
    basic = {
        "gene_id": None,
        "gene_name": symbol,
        "gene_symbol": symbol,
        "ensembl_id": row.get("ensembl_gene_id"),
        "zfin_id": row.get("zfin_id"),
        "chromosome": row.get("chrom_grcz11"),
        "gene_function": None,
        "interpro_domain": None,
        "uniprot_id": None,
        "expression_summary": None,
        "ncbi_gene_id": row.get("ncbi_gene_id"),
    }
    return {
        "basic": basic,
        "expression": [],
        "go": [],
        "homology": [],
        "kegg": [],
        "references": [],
        "aliases": {"zfin_id": row.get("zfin_id") or "", "items": [], "groups": {}, "count": 0},
        "paralogs": {"groups": []},
        "catalog_assembly": "GRCz11",
        "standard": {},
        "source_table": "zebrafish_gene_v2_20260922.gene_card",
    }


def get_gene_detail(gene_name: str) -> Optional[dict]:
    """综合查询：基础信息 + 表达 + GO + 同源 + 文献。"""
    name = normalize_gene_name(gene_name)
    sql = """
    SELECT gene_id, gene_name, gene_symbol, ensembl_id, zfin_id, chromosome,
           gene_function, interpro_domain, uniprot_id, expression_summary
    FROM gene_basic WHERE gene_name = :name LIMIT 1
    """
    res = _client().execute_sql(sql, params={"name": name})
    if not res["success"] or not res["data"]:
        sql2 = """
        SELECT gene_id, gene_name, gene_symbol, ensembl_id, zfin_id, chromosome,
               gene_function, interpro_domain, uniprot_id, expression_summary
        FROM gene_basic WHERE gene_symbol = :name OR gene_name = :name LIMIT 1
        """
        res = _client().execute_sql(sql2, params={"name": name})
        if not res["success"] or not res["data"]:
            from config import NCBI_REFERENCE_ASSEMBLY

            sql3 = """
            SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, gb.chromosome,
                   gb.gene_function, gb.interpro_domain, gb.uniprot_id, gb.expression_summary
            FROM gene_basic gb
            JOIN gene_assembly_xref ax ON gb.gene_id = ax.gene_id
            WHERE ax.assembly_name = :asm AND ax.gene_symbol = :name
            LIMIT 1
            """
            res = _client().execute_sql(sql3, params={"name": name, "asm": NCBI_REFERENCE_ASSEMBLY})
            if not res["success"] or not res["data"]:
                return _detail_from_gene_card(name)

    basic = res["data"][0]
    gid = basic["gene_id"]

    def q(table, cols, order_by="", max_rows=10):
        if not _SAFE_IDENT.match(table) or not _SAFE_IDENT.match(cols.replace(",", "").replace(" ", "")):
            if not _SAFE_IDENT.match(table):
                return []
            if any(not _SAFE_IDENT.match(c.strip()) for c in cols.split(",") if c.strip()):
                return []
        if order_by and any(
            not _SAFE_IDENT.match(p.strip())
            for p in order_by.replace(" DESC", "").replace(" ASC", "").split(",")
            if p.strip()
        ):
            order_by = ""
        s = f"SELECT {cols} FROM {table} WHERE gene_id = :gid"
        if order_by:
            s += f" ORDER BY {order_by}"
        s += f" LIMIT {int(max_rows)}"
        r = _client().execute_sql(s, max_rows=max_rows, params={"gid": gid})
        return r["data"] if r["success"] else []

    # Bgee 与胚胎 Atlas 分源查询，避免 TPM 峰值淹没组织表达
    bgee_res = _client().execute_sql(
        "SELECT tissue, expression_stage, expression_level, data_source "
        "FROM gene_expression WHERE gene_id = :gid AND data_source = 'bgee' "
        "ORDER BY expression_level DESC LIMIT 20",
        max_rows=20,
        params={"gid": gid},
    )
    bgee_expr = bgee_res["data"] if bgee_res["success"] else []
    embryo_expr = _client().execute_sql(
        "SELECT tissue, expression_stage, expression_level, data_source "
        "FROM gene_expression WHERE gene_id = :gid AND "
        "(data_source = 'atlas_embryo' OR data_source IS NULL) "
        "ORDER BY expression_level DESC LIMIT 20",
        max_rows=20,
        params={"gid": gid},
    )
    embryo_rows = embryo_expr["data"] if embryo_expr["success"] else []

    # 坐标：gene_basic 只有 chromosome，起止在注释目录里（data/ensembl/gene_catalog.json）。
    # 放在这里而不是 API 层，是为了让「聊天里的基因回答」也能用上同一份坐标，
    # 否则只有 /api/gene/ 这一个入口有位置，其它路径还是只给染色体号。
    locus = None
    try:
        from gene_locus import get_locus

        locus = get_locus(basic.get("gene_name") or name, basic.get("ensembl_id") or "")
    except Exception:
        locus = None

    return {
        "basic": basic,
        "locus": locus,
        "expression": bgee_expr + embryo_rows,
        "go": q("gene_go", "go_term, go_category"),
        "homology": q("homology_gene", "species, homo_gene_name, homology_score"),
        "kegg": q("gene_kegg", "pathway_id, pathway_name, category", max_rows=40),
        "references": q("gene_reference", "pubmed_id, title, journal, publish_year"),
        "aliases": alias_block(gid),
        "paralogs": paralog_block(gid),
        "catalog_assembly": "GRCz11",
        "standard": _gene_standard_block(gid, basic),
    }


# ---------------------------------------------------------------------------
# 别名（任务①）：ZFIN / NCBI / UniProt / Ensembl / 其他历史名
# ---------------------------------------------------------------------------

_ALIAS_FIELDS = (
    ("aliases_zfin", "zfin", "ZFIN"),
    ("aliases_ncbi", "ncbi", "NCBI"),
    ("aliases_uniprot", "uniprot", "UniProt"),
    ("aliases_ensembl", "ensembl", "Ensembl"),
    ("aliases_other", "other", "其他/历史描述名"),
)


def _json_list(raw) -> list:
    """gene_name_bundle 的 aliases_* 是 JSON 字符串数组，容错解析。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text or text in ("[]", "null"):
        return []
    try:
        val = json.loads(text)
    except Exception:
        return [p.strip() for p in text.split(",") if p.strip()]
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


def gene_id_of(name: str) -> Optional[int]:
    """按符号 / 名称 / 别名 / 跨库 ID 解析 gene_id（供别名、同源等上层模块复用）。"""
    raw = (name or "").strip()
    if not raw:
        return None
    candidates = []
    try:
        candidates.append(normalize_gene_name(raw))
    except Exception:
        pass
    candidates.append(raw)
    seen = set()
    for cand in candidates:
        if not cand or cand.lower() in seen:
            continue
        seen.add(cand.lower())
        try:
            res = _client().execute_sql(
                "SELECT gene_id FROM gene_basic WHERE gene_name = :n OR gene_symbol = :n "
                "OR ensembl_id = :n OR zfin_id = :n LIMIT 1",
                params={"n": cand},
            )
            if res.get("success") and res.get("data"):
                return res["data"][0]["gene_id"]
        except Exception:
            continue
    # 别名表兜底（p53 → tp53 这类历史名）
    try:
        res = _client().execute_sql(
            "SELECT gene_id FROM gene_alias WHERE alias = :n OR alias = :nl LIMIT 1",
            params={"n": raw, "nl": raw.lower()},
        )
        if res.get("success") and res.get("data"):
            return res["data"][0]["gene_id"]
    except Exception:
        pass
    return None


def alias_block(gid) -> dict:
    """汇总一个基因的全部别名（含历史名），带来源标签。

    来源：
      · gene_name_bundle（ZFIN/NCBI/UniProt/Ensembl/其他 五类 JSON 别名，主力）
      · gene_alias（符号/同义词表，做补充与去重）
    """
    out = {"zfin_id": "", "primary_symbol": "", "standard_symbol": "",
           "groups": {}, "items": [], "count": 0}
    seen = set()

    def _push(alias: str, source: str, label: str, is_primary: bool = False) -> None:
        a = (alias or "").strip()
        if not a:
            return
        key = a.lower()
        if key in seen:
            return
        seen.add(key)
        out["items"].append({
            "alias": a, "source": source, "source_label": label,
            "is_primary": bool(is_primary),
            "is_id_like": bool(re.match(r"^(zdb-|zgc:|wu:|fb|etid|ens|nm_|xm_|np_)\S*", key)),
        })
        out["groups"].setdefault(source, []).append(a)

    try:
        res = _client().execute_sql(
            "SELECT zfin_id, primary_symbol, standard_symbol, aliases_zfin, aliases_ncbi, "
            "aliases_uniprot, aliases_ensembl, aliases_other "
            "FROM gene_name_bundle WHERE gene_id = :gid LIMIT 1",
            params={"gid": gid},
        )
        row = (res.get("data") or [None])[0] if res.get("success") else None
    except Exception:
        row = None

    if row:
        out["zfin_id"] = str(row.get("zfin_id") or "")
        out["primary_symbol"] = str(row.get("primary_symbol") or "")
        out["standard_symbol"] = str(row.get("standard_symbol") or "")
        if out["standard_symbol"]:
            _push(out["standard_symbol"], "standard", "标准符号", True)
        for field, source, label in _ALIAS_FIELDS:
            for a in _json_list(row.get(field)):
                _push(a, source, label)

    try:
        res = _client().execute_sql(
            "SELECT alias, alias_type, source, is_primary FROM gene_alias WHERE gene_id = :gid",
            params={"gid": gid}, max_rows=200,
        )
        rows = res.get("data") or [] if res.get("success") else []
    except Exception:
        rows = []
    for r in rows:
        _push(r.get("alias"), "catalog", "本地别名表(%s)" % (r.get("source") or r.get("alias_type") or "?",),
              bool(r.get("is_primary")))

    out["count"] = len(out["items"])
    return out


# ---------------------------------------------------------------------------
# 复制体 / 共直系同源（任务②）：硬骨鱼全基因组复制后的“一个人类基因 ↔ 多个斑马鱼基因”
# ---------------------------------------------------------------------------

def _gene_labels(gids) -> dict:
    """批量取展示名：{gene_id: {symbol, chromosome, gene_name}}（优先标准符号）。"""
    ids = [g for g in dict.fromkeys(gids or []) if g]
    if not ids:
        return {}
    placeholders = ", ".join(":g%d" % i for i in range(len(ids)))
    params = {"g%d" % i: g for i, g in enumerate(ids)}
    try:
        res = _client().execute_sql(
            "SELECT b.gene_id, b.gene_symbol, b.gene_name, b.chromosome, b.ensembl_id, n.standard_symbol "
            "FROM gene_basic b LEFT JOIN gene_name_bundle n ON n.gene_id = b.gene_id "
            "WHERE b.gene_id IN (%s)" % placeholders,
            params=params, max_rows=len(ids),
        )
        rows = res.get("data") or [] if res.get("success") else []
    except Exception:
        rows = []
    out = {}
    for r in rows:
        sym = (r.get("standard_symbol") or r.get("gene_symbol") or r.get("gene_name") or "").strip()
        out[r.get("gene_id")] = {
            "symbol": sym,
            "chromosome": (r.get("chromosome") or ""),
            "gene_name": (r.get("gene_name") or ""),
            "ensembl_id": (r.get("ensembl_id") or ""),
        }
    return out


def paralog_block(gid) -> dict:
    """按“人/鼠同源基因名”反推斑马鱼内共直系同源（复制体）关系。

    原理（纯本地、可复现）：
      斑马鱼经历了硬骨鱼特有的全基因组复制（Ts3R/WGD）。若一个人类基因在
      homology_gene 里同时对应两个斑马鱼基因，这两个斑马鱼基因就是该人类基因的
      共直系同源（co-ortholog），通常就是保留下来的一对复制体；但它们的功能
      可能发生亚功能化/新功能化（分工），需用文献证据判断。
    """
    out = {"self": [], "groups": [], "is_duplicated": False,
           "note": "复制关系按“同一物种 + 同一同源基因名”推导；"
                   "功能分工（亚功能化/新功能化）需文献支持。"}
    try:
        res = _client().execute_sql(
            "SELECT species, homo_gene_name, homology_score FROM homology_gene "
            "WHERE gene_id = :gid AND homo_gene_name IS NOT NULL AND homo_gene_name <> '' "
            "ORDER BY homology_score DESC LIMIT 8",
            params={"gid": gid}, max_rows=8,
        )
        pairs = res.get("data") or [] if res.get("success") else []
    except Exception:
        pairs = []

    # 一次性把每个（物种, 同源基因）的其它斑马鱼基因查出来，再统一取名字（避免 N+1）
    _pairs_with_others = []
    _all_others = []
    for p in pairs:
        species = (p.get("species") or "").strip()
        homo = (p.get("homo_gene_name") or "").strip()
        if not species or not homo:
            continue
        try:
            r2 = _client().execute_sql(
                "SELECT h.gene_id, h.homology_score, b.gene_symbol, b.gene_name, b.chromosome "
                "FROM homology_gene h JOIN gene_basic b ON b.gene_id = h.gene_id "
                "WHERE h.species = :sp AND h.homo_gene_name = :homo AND h.gene_id <> :gid",
                params={"sp": species, "homo": homo, "gid": gid}, max_rows=20,
            )
            others = r2.get("data") or [] if r2.get("success") else []
        except Exception:
            others = []
        _pairs_with_others.append((p, others))
        _all_others.extend(others)

    labels = _gene_labels([gid] + [o.get("gene_id") for o in _all_others]) if _all_others else _gene_labels([gid])
    self_info = labels.get(gid) or {}
    self_sym = self_info.get("symbol", "")
    self_chr = self_info.get("chromosome", "")
    for p, others in _pairs_with_others:
        species = (p.get("species") or "").strip()
        homo = (p.get("homo_gene_name") or "").strip()
        if not species or not homo:
            continue
        out["self"].append({
            "species": species, "homo_gene_name": homo,
            "score": float(p.get("homology_score") or 0),
        })
        copies = [{
            "gene_id": gid, "symbol": self_sym, "chromosome": self_chr,
            "ensembl_id": self_info.get("ensembl_id", ""),
            "score": float(p.get("homology_score") or 0), "is_self": True,
        }]
        for o in others:
            info = labels.get(o.get("gene_id")) or {}
            copies.append({
                "gene_id": o.get("gene_id"),
                "symbol": info.get("symbol") or (o.get("gene_symbol") or o.get("gene_name") or ""),
                "chromosome": info.get("chromosome", "") or (o.get("chromosome") or ""),
                "ensembl_id": info.get("ensembl_id", ""),
                "score": float(o.get("homology_score") or 0),
                "is_self": False,
            })
        copies.sort(key=lambda c: (-c["score"], c["symbol"]))
        dup = len(copies) > 1
        out["is_duplicated"] = out["is_duplicated"] or dup
        out["groups"].append({
            "species": species, "homo_gene_name": homo,
            "n_copies": len(copies), "duplicated": dup, "copies": copies,
        })
    return out


def co_orthologs_by_human(human_symbol: str, limit: int = 12) -> dict:
    """反向：给人类/小鼠基因名，列出斑马鱼的全部共直系同源（复制体）。"""
    q = (human_symbol or "").strip()
    if not q:
        return {"query": q, "groups": [], "is_duplicated": False}
    out = {"query": q, "groups": [], "is_duplicated": False}
    try:
        res = _client().execute_sql(
            "SELECT h.species, h.gene_id, h.homology_score, b.gene_symbol, b.gene_name, b.chromosome "
            "FROM homology_gene h JOIN gene_basic b ON b.gene_id = h.gene_id "
            "WHERE UPPER(h.homo_gene_name) = :sym",
            params={"sym": q.upper()}, max_rows=_sql_limit(limit, 40),
        )
        rows = res.get("data") or [] if res.get("success") else []
    except Exception:
        rows = []
    by_species: dict = {}
    labels = _gene_labels([r.get("gene_id") for r in rows])
    for r in rows:
        sp = (r.get("species") or "").strip() or "?"
        info = labels.get(r.get("gene_id")) or {}
        by_species.setdefault(sp, []).append({
            "gene_id": r.get("gene_id"),
            "symbol": info.get("symbol") or (r.get("gene_symbol") or r.get("gene_name") or ""),
            "chromosome": info.get("chromosome", "") or (r.get("chromosome") or ""),
            "ensembl_id": info.get("ensembl_id", ""),
            "score": float(r.get("homology_score") or 0),
        })
    for sp, copies in by_species.items():
        copies.sort(key=lambda c: (-c["score"], c["symbol"]))
        out["groups"].append({
            "species": sp, "homo_gene_name": q.upper(),
            "n_copies": len(copies), "duplicated": len(copies) > 1, "copies": copies,
        })
        out["is_duplicated"] = out["is_duplicated"] or len(copies) > 1
    return out


def _gene_standard_block(gid, basic: dict) -> dict:
    from gene_id_mapper import NCBI_REFERENCE_ASSEMBLY, NCBI_REFERENCE_REFSEQ, refseq_standard_for_gene

    g12 = refseq_standard_for_gene(gid)
    std_sym = ((g12 or {}).get("gene_symbol") or "").strip()
    return {
        "assembly": NCBI_REFERENCE_ASSEMBLY,
        "refseq": NCBI_REFERENCE_REFSEQ,
        "gene_symbol": std_sym or (basic.get("gene_name") or ""),
        "mapped": bool(g12 and std_sym),
    }


def _sql_limit(limit: int, cap: int = 50) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 20
    return max(1, min(n, cap))


def search_genes(keyword: str, limit: int = 20, browse: bool = False) -> list:
    """基因检索：符号 / 别名（含 ZFIN·NCBI·UniProt 历史名）/ ZFIN·Ensembl ID / 功能词。

    browse=True 且关键词为空时，按符号顺序列出前 N 个（基因库浏览入口）。
    """
    kw = keyword.strip()
    lim = _sql_limit(limit, 60)
    from config import NCBI_REFERENCE_ASSEMBLY

    if not kw:
        if not browse:
            return []
        sql_browse = f"""
        SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.chromosome, gb.ensembl_id, gb.zfin_id,
               SUBSTRING(COALESCE(gb.gene_function, ''), 1, 160) AS gene_function,
               n.standard_symbol AS standard_symbol
        FROM gene_basic gb
        LEFT JOIN gene_name_bundle n ON n.gene_id = gb.gene_id
        ORDER BY COALESCE(NULLIF(n.standard_symbol, ''), gb.gene_name) ASC
        LIMIT {lim}
        """
        res = _client().execute_sql(sql_browse, max_rows=lim)
        return res["data"] if res["success"] else []

    like = f"%{kw}%"
    like_l = f"%{kw.lower()}%"
    # JSON 别名数组用“带引号”的模式匹配，避免 p53 命中 tp53 之外的噪音
    json_like = f'%"%{kw}%"%'
    sql = f"""
    SELECT DISTINCT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.chromosome, gb.ensembl_id, gb.zfin_id,
           SUBSTRING(COALESCE(gb.gene_function, ''), 1, 160) AS gene_function,
           n.standard_symbol AS standard_symbol,
           (SELECT GO.go_term FROM gene_go GO WHERE GO.gene_id = gb.gene_id LIMIT 1) AS go_preview
    FROM gene_basic gb
    LEFT JOIN gene_alias ga ON gb.gene_id = ga.gene_id
    LEFT JOIN gene_name_bundle n ON n.gene_id = gb.gene_id
    LEFT JOIN gene_assembly_xref ax ON gb.gene_id = ax.gene_id AND ax.assembly_name = :asm
    WHERE gb.gene_name LIKE :like
       OR gb.gene_symbol LIKE :like
       OR gb.gene_function LIKE :like
       OR gb.ensembl_id LIKE :like
       OR gb.zfin_id LIKE :like
       OR ga.alias LIKE :like_l
       OR ax.gene_symbol LIKE :like
       OR n.standard_symbol LIKE :like
       OR n.aliases_zfin LIKE :json_like
       OR n.aliases_ncbi LIKE :json_like
       OR n.aliases_uniprot LIKE :json_like
       OR n.aliases_ensembl LIKE :json_like
       OR n.aliases_other LIKE :json_like
    LIMIT {lim}
    """
    res = _client().execute_sql(
        sql,
        max_rows=lim,
        params={"like": like, "like_l": like_l, "json_like": json_like,
                "asm": NCBI_REFERENCE_ASSEMBLY},
    )
    return res["data"] if res["success"] else []


def id_convert(identifier: str) -> dict:
    """Ensembl / NCBI / ZFIN / RefSeq / 符号 互查（统一解析层）。"""
    from gene_id_mapper import resolve_gene_identifier

    resolved = resolve_gene_identifier(identifier)
    matches = resolved.get("candidates") or []
    if resolved.get("canonical") and not any(
        m.get("gene_id") == resolved["canonical"].get("gene_id") for m in matches
    ):
        matches = [resolved["canonical"]] + matches
    return {
        "query": identifier,
        "reference": resolved.get("reference"),
        "resolved": resolved.get("resolved", False),
        "canonical": resolved.get("canonical"),
        "matches": matches,
        "aliases": resolved.get("aliases") or [],
        "xrefs": resolved.get("xrefs") or [],
        "ensembl_versions": resolved.get("ensembl_versions") or [],
        "tissue_expression": resolved.get("tissue_expression") or [],
        "id_chain": resolved.get("id_chain") or [],
        "disambiguation": resolved.get("disambiguation"),
        "notes": resolved.get("notes") or [],
    }


def list_genes_by_chromosome(chromosome: str, limit: int = 50) -> list:
    chr_val = chromosome.strip().replace("chr", "")
    lim = _sql_limit(limit, 50)
    sql = f"""
    SELECT gene_name, gene_symbol, gene_function
    FROM gene_basic WHERE chromosome = :chr
    ORDER BY gene_name LIMIT {lim}
    """
    res = _client().execute_sql(sql, max_rows=lim, params={"chr": chr_val})
    return res["data"] if res["success"] else []


def go_term_summary(limit: int = 15) -> list:
    """GO 术语统计（轻量富集概览）。"""
    lim = _sql_limit(limit, 50)
    sql = f"""
    SELECT go_term, go_category, COUNT(*) AS gene_count
    FROM gene_go GROUP BY go_term, go_category
    ORDER BY gene_count DESC LIMIT {lim};
    """
    res = _client().execute_sql(sql, max_rows=lim)
    return res["data"] if res["success"] else []


def database_stats() -> dict:
    tables = ["gene_basic", "gene_expression", "gene_go", "homology_gene", "gene_reference", "gene_alias", "gene_xref"]
    stats = {}
    for t in tables:
        if not _SAFE_IDENT.match(t):
            stats[t] = 0
            continue
        r = _client().execute_sql(f"SELECT COUNT(*) AS cnt FROM {t}")
        if r["success"] and r["data"]:
            stats[t] = r["data"][0]["cnt"]
        else:
            stats[t] = 0
    return stats


def batch_id_convert(identifiers: list) -> dict:
    """批量 ID 互查，返回 TSV 友好结构。"""
    from gene_id_mapper import resolve_batch

    rows = resolve_batch(identifiers)
    tsv_lines = ["query\tresolved\tgene_name\tensembl_id\tzfin_id\tncbi_gene\tchromosome"]
    for r in rows:
        c = r.get("canonical") or {}
        ncbi = next((x["db_id"] for x in (r.get("xrefs") or []) if x.get("db_name") == "ncbi_gene"), "")
        tsv_lines.append(
            f"{r.get('query','')}\t{1 if r.get('resolved') else 0}\t"
            f"{c.get('gene_name','')}\t{c.get('ensembl_id','')}\t{c.get('zfin_id','')}\t{ncbi}\t{c.get('chromosome','')}"
        )
    return {"results": rows, "count": len(rows), "tsv": "\n".join(tsv_lines)}


def _gene_token_pattern():
    """字母开头的基因符号，例如 tp53。结尾不含连字符，避免把 tp53-1 整段吞掉。"""
    return r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,15})(?![A-Za-z0-9])"


def _digit_leading_pattern():
    """数字开头、后面还有字母或连字符的名称。纯数字不在这里。"""
    return r"(?<![A-Za-z0-9])([0-9][0-9A-Za-z.'\-]{1,63})(?![A-Za-z0-9.'\-])"


def is_digit_leading_gene_token(tok: str) -> bool:
    """数字开头即可查。纯数字留给 NCBI Gene ID，不当作基因符号。"""
    text = (tok or "").strip()
    if len(text) < 2 or not text[0].isdigit() or text.isdigit():
        return False
    if re.search(r"[A-Za-z]", text):
        return True
    return bool(re.search(r"[.'\-].*[0-9]|[0-9].*[.'\-]", text))


def whole_digit_leading_query(question: str) -> Optional[str]:
    """整句就是一个数字开头的名称时，连空格一起保留，例如 1-agpat 8。"""
    text = (question or "").strip().strip("\"'“”“”")
    if not is_digit_leading_gene_token(text):
        return None
    if re.search(r"[\u4e00-\u9fff]", text) or len(text) > 160:
        return None
    return text


def _is_gene_stopword(cand: str) -> bool:
    return cand.lower() in {
        "hpf", "go", "kegg", "dna", "rna", "crispr", "fpkm", "tpm", "blast",
        "mapk", "pathway", "zebrafish", "danio", "rerio", "ensembl", "zfin",
        "ncbi", "refseq", "gene", "id", "mrna", "cdna", "and", "or", "the",
        "vs", "http", "https", "www", "com",
        # 文献元数据词：多轮上下文拼接（如“上文PMID：25535837”）时防泄漏为基因名
        "pmid", "wos", "doi", "pubmed", "abstract", "title", "journal",
        "year", "literature", "paper", "article", "author", "source",
        "pubmed_yes", "scihub", "xml", "pdf", "fulltext", "full_text",
        "zga", "mzt", "orf", "utr", "ncrna", "lncrna", "mirna",
        "for", "do", "with", "from", "this", "that", "what", "analysis",
        "enrich", "enrichment", "ora", "fisher", "both", "type", "terms",
        # 组装、品系、方法词，不是基因符号
        "grcz", "grcz10", "grcz11", "grcz12", "grcz12tu", "assembly",
        "pcr", "gfp", "rfp", "gpu", "cpu", "cuda", "pdf", "pmc",
        "ab", "tu", "hub", "rag", "llm", "sql", "api",
    }


def extract_gene_from_question(question: str) -> Optional[str]:
    """从问题中提取基因符号或数据库 ID（支持命名混乱场景）。"""
    if not question:
        return None
    # ZFIN
    m = re.search(r"ZDB-GENE-\d+-\d+", question, re.I)
    if m:
        return m.group(0)
    # Ensembl 基因/转录本
    m = re.search(r"ENS[A-Z]+\d+(?:\.\d+)?", question, re.I)
    if m:
        return m.group(0)
    # RefSeq
    m = re.search(r"(?:NM|NR|NP|XM|XR|NC|NG)_\d+(?:\.\d+)?", question, re.I)
    if m:
        return m.group(0)
    # NCBI Gene ID（需上下文或单独出现）
    m = re.search(r"\b(\d{4,9})\b", question)
    if m and any(k in question.lower() for k in (
        "ncbi", "gene id", "基因id", "gi:", "id转换", "转换", "entrez",
    )):
        return m.group(1)
    # 引号内符号
    m = re.search(r"['\"]([a-zA-Z][a-zA-Z0-9.-]{1,20})['\"]", question)
    if m:
        return m.group(1)
    # 基因符号（兼容 tp53在哪些组织 等中英文连写，以及任意数字开头的名称）
    found = extract_genes_from_question(question, limit=1)
    return found[0] if found else None


def extract_genes_from_question(question: str, limit: int = 5) -> list:
    """从问题中提取多个基因符号（用于比较/并列查询）。"""
    if not question:
        return []
    ranked = []
    seen = set()
    covered = []

    def add(pos: int, cand: str) -> None:
        text = (cand or "").strip().lower()
        if _is_gene_stopword(text) or text in seen or len(text) < 2:
            return
        seen.add(text)
        ranked.append((pos, text))

    whole = whole_digit_leading_query(question)
    if whole:
        add(0, whole)
    for match in re.finditer(_digit_leading_pattern(), question):
        tok = match.group(1)
        if is_digit_leading_gene_token(tok):
            add(match.start(1), tok)
            covered.append(match.span(1))
    for match in re.finditer(_gene_token_pattern(), question):
        start = match.start(1)
        if any(span_start <= start < span_end for span_start, span_end in covered):
            continue
        add(start, match.group(1))
    ranked.sort(key=lambda item: item[0])
    return [text for _, text in ranked[:limit]]


def question_has_digit_leading_gene(question: str) -> bool:
    if whole_digit_leading_query(question):
        return True
    for match in re.finditer(_digit_leading_pattern(), question or ""):
        if is_digit_leading_gene_token(match.group(1)):
            return True
    return False


def extract_identifiers_from_text(text: str) -> list:
    """从多行文本批量提取 ID/符号（ID 转换工具用）。"""
    found = []
    seen = set()
    for line in (text or "").replace(",", "\n").replace(";", "\n").splitlines():
        token = line.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        found.append(token)
    return found[:200]
