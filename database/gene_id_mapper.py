# -*- coding: utf-8 -*-
"""基因命名统一解析与跨库 ID 映射。

用户可见标准名 / BLAST / cDNA = RefSeq GCF_049306965.2（GRCz12tu）。
本地库 gene_basic 的 Ensembl ID / 坐标 / 表达 / GO·KEGG = GRCz11 Ensembl r116。
"""
import bootstrap  # noqa: F401

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from common_utils import logger
from config import (
    ENSEMBL_RELEASE,
    NCBI_ASSEMBLY_ACCESSION,
    NCBI_DATASETS_URL,
    NCBI_REFERENCE_ASSEMBLY,
    NCBI_REFERENCE_GENBANK,
    NCBI_REFERENCE_REFSEQ,
    REFERENCE_ASSEMBLY,
    TAXON_ID,
)

ENSEMBL_BASE = "https://rest.ensembl.org"

# 常见口语/文献别名 → 优先解析目标（仍可能有多条候选）
# pax6 不在此表：斑马鱼有 pax6a / pax6b，禁止折成单一亚型。
KNOWN_ALIASES = {
    "p53": "tp53",
    "p63": "tp63",
    "p73": "tp73",
    "shh": "shha",
    "shh-a": "shha",
    "shh-b": "shhb",
    "fgf8": "fgf8a",
    "fgf8a": "fgf8a",
    "pax2": "pax2a",
    "sox9": "sox9b",
    "sox9a": "sox9a",
    "sox9b": "sox9b",
    "sox10": "sox10",
    "notch1": "notch1a",
    "wnt11": "wnt11",
    "t": "ntla",
    "brachyury": "ntla",
    "cmlc2": "myl7",
    "mhc": "myl7",
    "vegf": "vegfaa",
    "vegfa": "vegfaa",
    "vegfr2": "kdrl",
    "hsp70": "hsp70.1",
    "hsp90": "hsp90ab1",
    "erk2": "mapk1",
    "mapk": "mapk1",
    "nacre": "mitfa",
    "scn1a": "scn1lab",
    "col2a1": "col2a1a",
    "ctnt": "tnnt2a",
    "tnnt2": "tnnt2a",
    "beta-actin": "actb2",
    "actb": "actb2",
    "gfp": "egfp",
    "rag2": "rag2",
    "rag1": "rag1",
    "cxcr4": "cxcr4b",
    "cxcl12": "cxcl12b",
    "bmp4": "bmp4",
    "bmp2": "bmp2b",
    "nodal": "ndr1",
    "lefty": "lft1",
    "fli1": "fli1a",
    "gata1": "gata1a",
    "runx1": "runx1",
    "myc": "myca",
    "c-myc": "myca",
    "braf": "braf",
    "kras": "kras",
    "bra": "braf",
}

REFSEQ_PATTERN = re.compile(r"^(NM|NR|NP|XM|XR|NC|NG|NW|NZ)_\d+(?:\.\d+)?$", re.I)
ZFIN_PATTERN = re.compile(r"^ZDB-GENE-\d+-\d+$", re.I)
ENSEMBL_ID_PATTERN = re.compile(r"^ENS[A-Z]+\d+(?:\.\d+)?$", re.I)


def external_url(db_name: str, db_id: str) -> Optional[str]:
    """生成 NCBI / Ensembl / ZFIN / RefSeq 外链。"""
    if not db_id:
        return None
    db = (db_name or "").lower()
    did = db_id.strip()
    if db in ("ncbi_gene", "ncbi") and did.isdigit():
        return f"https://www.ncbi.nlm.nih.gov/gene/{did}"
    if db == "ensembl" or did.upper().startswith("ENS"):
        base = re.sub(r"\.\d+$", "", did)
        return f"https://www.ensembl.org/Danio_rerio/Gene/Summary?g={base}"
    if db == "zfin" or did.upper().startswith("ZDB-GENE"):
        return f"https://zfin.org/{did}"
    if db in ("refseq_mrna", "refseq_protein", "refseq") or re.match(r"^[NX][MR]_\d+", did, re.I):
        return f"https://www.ncbi.nlm.nih.gov/nuccore/{did.split('.')[0]}"
    if db == "uniprot":
        return f"https://www.uniprot.org/uniprotkb/{did}/entry"
    return None


def _client():
    from mysql_client import get_mysql_client
    return get_mysql_client()


def _query(sql: str, limit: int = 50) -> list:
    client = _client()
    db = next(client.get_db())
    try:
        from sqlalchemy import text
        rows = db.execute(text(sql)).mappings().all()
        data = [dict(r) for r in rows]
        if limit and limit > 0:
            return data[:limit]
        return data
    except Exception as e:
        logger.warning(f"[gene_id_mapper] 查询失败: {e}")
        return []
    finally:
        db.close()


def reference_genome_info() -> dict:
    return {
        "species": "Danio rerio",
        "taxon_id": TAXON_ID,
        "assembly": REFERENCE_ASSEMBLY,
        "ncbi_assembly": NCBI_ASSEMBLY_ACCESSION,
        "ensembl_release": ENSEMBL_RELEASE,
        "ncbi_datasets_url": NCBI_DATASETS_URL,
        "ncbi_reference_assembly": NCBI_REFERENCE_ASSEMBLY,
        "ncbi_reference_refseq": NCBI_REFERENCE_REFSEQ,
        "ncbi_reference_genbank": NCBI_REFERENCE_GENBANK,
        "assembly_note": (
            f"标准名 / BLAST / cDNA = RefSeq {NCBI_REFERENCE_REFSEQ}（{NCBI_REFERENCE_ASSEMBLY}）；"
            f"本地库 ID / 坐标 / 表达 / GO·KEGG = {REFERENCE_ASSEMBLY} Ensembl {ENSEMBL_RELEASE}"
            f"（{NCBI_ASSEMBLY_ACCESSION}）。跨版本不得混用坐标，对照表 gene_assembly_xref。"
        ),
        "user_facing_reference": NCBI_REFERENCE_ASSEMBLY,
        "ncbi_url": NCBI_DATASETS_URL,
    }


def refseq_standard_role(mapped: bool = True) -> str:
    if mapped:
        return f"RefSeq {NCBI_REFERENCE_REFSEQ} 标准名"
    return f"本条 RefSeq {NCBI_REFERENCE_REFSEQ} 无唯一注释，沿用本地主名"


def _get_ensembl_versions(gene_id: int) -> list:
    return _query(
        f"SELECT ensembl_id, id_base, version_num, ensembl_release, is_current, source "
        f"FROM ensembl_id_version WHERE gene_id = {int(gene_id)} "
        f"ORDER BY is_current DESC, version_num DESC LIMIT 20"
    )


def _get_tissue_expression(gene_id: int, limit: int = 15) -> list:
    return _query(
        f"SELECT tissue, expression_stage, expression_level, data_source "
        f"FROM gene_expression WHERE gene_id = {int(gene_id)} AND data_source = 'bgee' "
        f"ORDER BY expression_level DESC LIMIT {int(limit)}"
    )


def ensure_schema_extensions() -> bool:
    """别名表 + 表达 data_source + Ensembl 版本表。"""
    ok = ensure_alias_tables()
    import os
    from config import _BASE_DIR
    path = os.path.join(_BASE_DIR, "sql", "expression_enhancement_migration.sql")
    if not os.path.isfile(path):
        return ok
    with open(path, encoding="utf-8") as f:
        ddl = f.read()
    statements = [s.strip() for s in ddl.split(";") if s.strip() and not s.strip().startswith("--")]
    client = _client()
    db = next(client.get_db())
    try:
        from sqlalchemy import text
        for stmt in statements:
            if stmt.upper().startswith("USE "):
                continue
            try:
                db.execute(text(stmt))
            except Exception as e:
                if "Duplicate column" in str(e) or "already exists" in str(e).lower():
                    db.rollback()
                    continue
                raise
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.warning(f"[gene_id_mapper] schema 扩展失败: {e}")
        return ok
    finally:
        db.close()


def ensure_alias_tables() -> bool:
    import os
    from config import _BASE_DIR
    path = os.path.join(_BASE_DIR, "sql", "gene_alias_migration.sql")
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as f:
        ddl = f.read()
    statements = [s.strip() for s in ddl.split(";") if s.strip() and not s.strip().startswith("--")]
    client = _client()
    db = next(client.get_db())
    try:
        from sqlalchemy import text
        for stmt in statements:
            if stmt.upper().startswith("USE "):
                continue
            db.execute(text(stmt))
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        logger.warning(f"[gene_id_mapper] 建表失败: {e}")
        return False
    finally:
        db.close()


def _esc(s: str) -> str:
    return s.replace("'", "''")


def _gene_row_by_id(gene_id: int) -> Optional[dict]:
    rows = _query(
        f"SELECT gene_id, gene_name, gene_symbol, ensembl_id, zfin_id, chromosome "
        f"FROM gene_basic WHERE gene_id = {int(gene_id)} LIMIT 1"
    )
    return rows[0] if rows else None


def _lookup_by_basic(query: str) -> list:
    q = _esc(query.strip())
    ql = _esc(query.strip().lower())
    return _query(
        f"SELECT gene_id, gene_name, gene_symbol, ensembl_id, zfin_id, chromosome "
        f"FROM gene_basic "
        f"WHERE gene_name = '{ql}' OR gene_symbol = '{q}' OR ensembl_id = '{q}' "
        f"OR zfin_id = '{q}' LIMIT 20"
    )


def _lookup_by_alias(alias: str) -> list:
    a = _esc(alias.strip().lower())
    return _query(
        f"SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, "
        f"gb.chromosome, ga.is_primary, ga.alias_type, ga.source AS alias_source "
        f"FROM gene_alias ga JOIN gene_basic gb ON ga.gene_id = gb.gene_id "
        f"WHERE ga.alias = '{a}' "
        f"ORDER BY ga.is_primary DESC, gb.gene_name LIMIT 20"
    )


def _lookup_by_xref(db_id: str) -> list:
    q = _esc(db_id.strip())
    qu = _esc(db_id.strip().upper())
    return _query(
        f"SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, "
        f"gb.chromosome, gx.db_name, gx.db_id "
        f"FROM gene_xref gx JOIN gene_basic gb ON gx.gene_id = gb.gene_id "
        f"WHERE gx.db_id = '{q}' OR gx.db_id = '{qu}' "
        f"OR gx.db_id LIKE '{q}|%' OR gx.db_id LIKE '{qu}|%' "
        f"LIMIT 20"
    )


def _get_xrefs(gene_id: int) -> list:
    return _query(
        f"SELECT db_name, db_id, db_label, source FROM gene_xref "
        f"WHERE gene_id = {int(gene_id)} ORDER BY db_name, db_id"
    )


def _lookup_by_grcz12tu_symbol(query: str) -> list:
    q = _esc(query.strip())
    ql = _esc(query.strip().lower())
    return _query(
        "SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, "
        "gb.chromosome FROM gene_assembly_xref ax "
        "JOIN gene_basic gb ON ax.gene_id = gb.gene_id "
        f"WHERE ax.assembly_name='{NCBI_REFERENCE_ASSEMBLY}' AND (ax.gene_symbol = '{q}' "
        f"OR LOWER(ax.gene_symbol) = '{ql}') LIMIT 20"
    )


def _get_grcz12tu_row(gene_id: int) -> Optional[dict]:
    rows = _query(
        "SELECT gene_symbol, ncbi_gene_id, ensembl_id, chromosome, start_pos, end_pos, source "
        f"FROM gene_assembly_xref WHERE gene_id = {int(gene_id)} "
        f"AND assembly_name='{NCBI_REFERENCE_ASSEMBLY}' LIMIT 1"
    )
    return rows[0] if rows else None


def refseq_standard_for_gene(gene_id) -> Optional[dict]:
    """返回该本地基因在 RefSeq GCF_049306965.2 上的标准名行；无可靠注释则 None。"""
    if not gene_id:
        return None
    row = _get_grcz12tu_row(int(gene_id))
    if not row:
        return None
    symbol = (row.get("gene_symbol") or "").strip()
    ncbi = row.get("ncbi_gene_id")
    ncbi_s = str(ncbi).strip() if ncbi is not None else ""
    source = (row.get("source") or "").strip()
    if not symbol or not ncbi_s or not source.startswith("refseq_"):
        return None
    return row


def _get_aliases(gene_id: int) -> list:
    return _query(
        f"SELECT alias, alias_type, source, is_primary FROM gene_alias "
        f"WHERE gene_id = {int(gene_id)} ORDER BY is_primary DESC, alias",
        limit=0,
    )


def should_try_ensembl_fallback(query: str) -> bool:
    """只有像基因符号 / Ensembl ID 的查询才打 REST，避免垃圾串卡 10s+。"""
    q = (query or "").strip()
    if not q or len(q) > 40:
        return False
    if ENSEMBL_ID_PATTERN.match(q):
        return True
    if ZFIN_PATTERN.match(q) or REFSEQ_PATTERN.match(q) or re.fullmatch(r"\d+", q):
        return False
    if re.search(r"[\s/\\]|[\u4e00-\u9fff]", q):
        return False
    if q.count("_") >= 2:
        return False
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9.-]{1,31}", q):
        return True
    if re.fullmatch(r"(?i)si:ch\d+[-_][A-Za-z0-9.]+", q):
        return True
    return False


def _ensembl_resolve(query: str) -> list:
    """Ensembl REST 兜底：符号或 Ensembl ID。"""
    q = query.strip()
    headers = {"Content-Type": "application/json", "User-Agent": "ZebrafishGeneQA/1.0"}
    out = []

    def _get(path: str):
        url = ENSEMBL_BASE + path
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        if re.match(r"^ENS[A-Z]+\d+", q, re.I):
            data = _get(f"/lookup/id/{urllib.parse.quote(q.split('.')[0])}?expand=0")
            if data and data.get("id"):
                sym = data.get("display_name") or data.get("name")
                out.append({"ensembl_id": data["id"], "symbol": sym, "source": "ensembl_lookup"})
                # 转录本 ID → 所属基因
                if data.get("Parent") and isinstance(data["Parent"], str):
                    out.append({"ensembl_id": data["Parent"], "symbol": sym, "source": "ensembl_parent"})
        else:
            data = _get(f"/xrefs/symbol/danio_rerio/{urllib.parse.quote(q)}?")
            if isinstance(data, list):
                for item in data[:5]:
                    if item.get("type") in ("gene", None) and item.get("id", "").startswith("ENS"):
                        out.append({
                            "ensembl_id": item["id"],
                            "symbol": item.get("display_id") or q,
                            "source": "ensembl_xref_symbol",
                        })
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.debug(f"[gene_id_mapper] Ensembl 兜底失败: {e}")
    return out


def _lookup_by_ensembl_version(ensembl_id: str) -> list:
    q = _esc(ensembl_id.strip())
    base = re.sub(r"\.\d+$", "", q)
    return _query(
        f"SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, gb.chromosome "
        f"FROM ensembl_id_version eiv JOIN gene_basic gb ON eiv.gene_id = gb.gene_id "
        f"WHERE eiv.ensembl_id = '{q}' OR eiv.id_base = '{base}' "
        f"LIMIT 20"
    )


def _lookup_by_ensembl_prefix(ensembl_id: str) -> list:
    q = _esc(ensembl_id.strip())
    base = re.sub(r"\.\d+$", "", q)
    return _query(
        f"SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, gb.chromosome "
        f"FROM gene_basic gb "
        f"WHERE gb.ensembl_id = '{q}' OR gb.ensembl_id LIKE '{base}.%' "
        f"OR gb.ensembl_id = '{base}' "
        f"UNION "
        f"SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, gb.chromosome "
        f"FROM gene_xref gx JOIN gene_basic gb ON gx.gene_id = gb.gene_id "
        f"WHERE gx.db_name = 'ensembl' AND (gx.db_id = '{q}' OR gx.db_id LIKE '{base}%') "
        f"LIMIT 20"
    )


def _lookup_by_refseq(refseq_id: str) -> list:
    q = _esc(refseq_id.strip())
    base = q.split(".")[0]
    return _query(
        f"SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, gb.chromosome "
        f"FROM gene_xref gx JOIN gene_basic gb ON gx.gene_id = gb.gene_id "
        f"WHERE gx.db_name IN ('refseq_mrna','refseq_protein','refseq') "
        f"AND (gx.db_id = '{q}' OR gx.db_id = '{base}' OR gx.db_id LIKE '{base}.%') "
        f"LIMIT 20"
    )


def resolve_batch(queries: list) -> list:
    """批量 ID 解析（Potato Hub 风格）。"""
    out = []
    for q in queries:
        q = (q or "").strip()
        if q:
            out.append(resolve_gene_identifier(q))
    return out


def _rank_candidates(candidates: list, query: str = "") -> list:
    seen = set()
    ranked = []
    for c in candidates:
        gid = c.get("gene_id")
        if gid in seen:
            continue
        seen.add(gid)
        ranked.append(c)
    preferred = KNOWN_ALIASES.get(query.strip().lower())
    ranked.sort(
        key=lambda x: (
            -(x.get("gene_name") == preferred) if preferred else 0,
            -int(x.get("is_primary") or 0),
            x.get("gene_name") or "",
        )
    )
    return ranked


def _query_norm(q: str) -> str:
    return (q or "").strip().lower()


def _name_resolve_tables_ready() -> bool:
    rows = _query(
        "SELECT 1 AS ok FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = 'name_hit_unique' LIMIT 1"
    )
    return bool(rows)


def _lookup_name_hit_unique(query_norm: str) -> Optional[dict]:
    q = _esc(query_norm)
    rows = _query(
        f"SELECT query_norm, gene_id, match_kind, priority, source_mask "
        f"FROM name_hit_unique WHERE query_norm = '{q}' LIMIT 1"
    )
    return rows[0] if rows else None


def _lookup_name_collision(query_norm: str) -> Optional[dict]:
    q = _esc(query_norm)
    rows = _query(
        f"SELECT query_norm, gene_id_count, candidate_gene_ids "
        f"FROM name_resolve_collision WHERE query_norm = '{q}' LIMIT 1"
    )
    return rows[0] if rows else None


def _get_name_bundle(gene_id: int) -> Optional[dict]:
    rows = _query(
        f"SELECT gene_id, zfin_id, primary_symbol, standard_symbol, "
        f"aliases_zfin, aliases_ncbi, aliases_uniprot, aliases_ensembl, aliases_other "
        f"FROM gene_name_bundle WHERE gene_id = {int(gene_id)} LIMIT 1"
    )
    if not rows:
        return None
    row = dict(rows[0])
    for k in (
        "aliases_zfin", "aliases_ncbi", "aliases_uniprot",
        "aliases_ensembl", "aliases_other",
    ):
        v = row.get(k)
        if isinstance(v, str):
            try:
                row[k] = json.loads(v)
            except json.JSONDecodeError:
                row[k] = []
        elif v is None:
            row[k] = []
    return row


def _is_structured_id_query(raw: str) -> bool:
    return bool(
        REFSEQ_PATTERN.match(raw)
        or ZFIN_PATTERN.match(raw)
        or re.fullmatch(r"\d+", raw)
        or re.match(r"^ENS[A-Z]+\d+", raw, re.I)
    )


def _fill_unique_card(result: dict, gene_id: int, match_kind: str = "", priority: int = 0) -> dict:
    """unique 命中后填标准名卡片；不改变 status。"""
    canonical = _gene_row_by_id(gene_id)
    result["resolved"] = True
    result["status"] = "unique"
    result["canonical"] = canonical
    result["match_kind"] = match_kind or None
    result["priority"] = priority or None
    result["name_bundle"] = _get_name_bundle(gene_id)
    result["aliases"] = _get_aliases(gene_id)
    result["xrefs"] = _get_xrefs(gene_id)
    result["ensembl_versions"] = _get_ensembl_versions(gene_id)
    result["tissue_expression"] = _get_tissue_expression(gene_id)
    if canonical and not any(c.get("gene_id") == gene_id for c in result.get("candidates") or []):
        result["candidates"] = [canonical] + list(result.get("candidates") or [])
    elif not result.get("candidates"):
        result["candidates"] = [canonical] if canonical else []

    g12 = refseq_standard_for_gene(gene_id)
    std_sym = ((g12 or {}).get("gene_symbol") or "").strip()
    bundle = result.get("name_bundle") or {}
    if not std_sym:
        std_sym = (bundle.get("standard_symbol") or "").strip()
    local_name = ((canonical or {}).get("gene_name") or "").strip()
    local_symbol = ((canonical or {}).get("gene_symbol") or "").strip()
    mapped = bool(std_sym)
    shown = std_sym or local_name or local_symbol or (f"gene_id:{gene_id}" if gene_id else "")
    result["standard"] = {
        "assembly": NCBI_REFERENCE_ASSEMBLY,
        "refseq": NCBI_REFERENCE_REFSEQ,
        "gene_symbol": shown,
        "role": refseq_standard_role(mapped),
        "source": (
            f"RefSeq {NCBI_REFERENCE_REFSEQ} ({NCBI_REFERENCE_ASSEMBLY})"
            if mapped
            else f"本地库 {REFERENCE_ASSEMBLY}（{NCBI_REFERENCE_REFSEQ} 无唯一注释，沿用本地主名）"
        ),
        "mapped": mapped,
        "local_fallback": bool(shown and not mapped),
        "ncbi_gene_id": (g12 or {}).get("ncbi_gene_id"),
        "chromosome": (g12 or {}).get("chromosome") or ((canonical or {}).get("chromosome")),
        "datasets_url": f"https://www.ncbi.nlm.nih.gov/datasets/genome/{NCBI_REFERENCE_REFSEQ}/",
    }
    hist = []
    for name, typ, src in (
        (local_name, "grcz11_symbol", REFERENCE_ASSEMBLY),
        (local_symbol, "gene_basic.gene_symbol", "gene_basic"),
    ):
        if name and (not std_sym or name.lower() != std_sym.lower()):
            hist.append({
                "alias": name,
                "alias_type": typ,
                "source": src,
                "is_primary": 0,
            })
    seen_hist = {h["alias"].lower() for h in hist}
    for a in result["aliases"] or []:
        al = (a.get("alias") or "").strip()
        if not al or al == "-" or al.lower() in seen_hist or (std_sym and al.lower() == std_sym.lower()):
            continue
        hist.append(a)
        seen_hist.add(al.lower())
    result["aliases"] = hist

    chain = []
    if canonical:
        if std_sym:
            chain.append({
                "type": "symbol", "label": f"RefSeq {NCBI_REFERENCE_REFSEQ} 标准名", "id": std_sym,
            })
        elif canonical.get("gene_name"):
            chain.append({
                "type": "symbol", "label": f"{REFERENCE_ASSEMBLY} 符号", "id": canonical["gene_name"],
            })
        if canonical.get("ensembl_id"):
            eid = canonical["ensembl_id"]
            chain.append({
                "type": "ensembl", "label": "Ensembl", "id": eid,
                "url": external_url("ensembl", eid),
            })
        if canonical.get("zfin_id"):
            zid = canonical["zfin_id"]
            chain.append({
                "type": "zfin", "label": "ZFIN", "id": zid,
                "url": external_url("zfin", zid),
            })
    ncbi_ids = [x for x in result["xrefs"] if x.get("db_name") == "ncbi_gene"]
    if ncbi_ids:
        nid = ncbi_ids[0]["db_id"]
        chain.append({
            "type": "ncbi", "label": "NCBI Gene", "id": nid,
            "url": external_url("ncbi_gene", nid),
        })
    result["id_chain"] = chain

    if canonical:
        for db_name, key in (
            ("ensembl", "ensembl_id"),
            ("zfin", "zfin_id"),
        ):
            if canonical.get(key):
                result["xrefs"].append({
                    "db_name": db_name,
                    "db_id": canonical[key],
                    "db_label": None,
                    "source": "gene_basic",
                })
    return result


def _history_alias_hit(result: dict, raw: str):
    try:
        from symbol_history import lookup_alias
    except Exception:
        return None
    rows = lookup_alias(raw)
    symbols = []
    for row in rows:
        sym = str(row.get("current_symbol") or "").strip()
        if sym and sym.lower() not in {item.lower() for item in symbols}:
            symbols.append(sym)
    if not symbols:
        return None
    ids = []
    for sym in symbols:
        for brow in _lookup_by_basic(sym):
            if str(brow.get("gene_name") or "").lower() != sym.lower():
                continue
            gid = brow.get("gene_id")
            if gid is not None and gid not in ids:
                ids.append(gid)
    if len(symbols) > 1 or len(ids) > 1:
        if len(ids) >= 2:
            return _fill_ambiguous(
                result,
                ids,
                f"别名 {raw} 对到多个基因：{', '.join(symbols)}。请改用 zfin_id。",
            )
        return None
    if len(ids) != 1:
        return None
    out = _fill_unique_card(result, int(ids[0]), match_kind="symbol_alias", priority=0)
    out.setdefault("notes", []).append(f"ZFIN 别名 {raw} → {symbols[0]}")
    return out


def _history_ensembl_hit(result: dict, raw: str):
    try:
        from symbol_history import lookup_ensembl
    except Exception:
        return None
    info = lookup_ensembl(raw)
    hits = [row for row in (info.get("hits") or []) if row.get("current_symbol")]
    if info.get("status") == "ambiguous":
        ids = []
        for row in hits:
            sym = str(row.get("current_symbol") or "")
            for brow in _lookup_by_basic(sym):
                if str(brow.get("gene_name") or "").lower() != sym.lower():
                    continue
                gid = brow.get("gene_id")
                if gid is not None and gid not in ids:
                    ids.append(gid)
        if len(ids) >= 2:
            return _fill_ambiguous(
                result,
                ids,
                "这个旧版 Ensembl 编号对到多个基因。请改用 zfin_id。",
            )
        return None
    if info.get("status") != "unique" or not hits:
        return None
    sym = str(hits[0].get("current_symbol") or "")
    ids = []
    for brow in _lookup_by_basic(sym):
        if str(brow.get("gene_name") or "").lower() == sym.lower():
            gid = brow.get("gene_id")
            if gid is not None and gid not in ids:
                ids.append(gid)
    if len(ids) != 1:
        return None
    method = str(hits[0].get("match_method") or "")
    release = hits[0].get("ensembl_release")
    assembly = hits[0].get("assembly_name")
    out = _fill_unique_card(result, int(ids[0]), match_kind=method or "ensembl_release_gene", priority=0)
    out.setdefault("notes", []).append(
        f"旧版 Ensembl {info.get('query')}（{assembly} release {release}，{method}）→ {sym}"
    )
    return out


def _parse_gene_id_list(raw) -> list:
    """把 name_resolve_collision.candidate_gene_ids 解析成 gene_id 列表。

    2026-10-28：这张表是脚本灌的，历史数据里同一列出现过三种写法 ——
      JSON 数组 "[261, 300]"、逗号串 "261,300"、以及带对象的 JSON
      "[{\\"gene_id\\": 261}]"。以前只 json.loads，遇到逗号串就异常 → 空列表 →
      返回「ambiguous 但没有候选」，actb1 / actb / gapdh 就是这么卡住的。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        text = str(raw).strip()
        if not text:
            return []
        items = None
        try:
            loaded = json.loads(text)
            items = loaded if isinstance(loaded, list) else [loaded]
        except (json.JSONDecodeError, TypeError):
            items = [p for p in re.split(r"[,;|/\s]+", text) if p]
    out = []
    for it in items or []:
        gid = None
        if isinstance(it, dict):
            gid = it.get("gene_id") or it.get("id")
        else:
            gid = it
        try:
            gid = int(str(gid).strip())
        except (TypeError, ValueError):
            continue
        if gid not in out:
            out.append(gid)
    return out


def _fill_ambiguous(result: dict, gene_ids: list, note: str = "") -> dict:
    """歧义：返回候选，不设 canonical，禁止模型消歧。"""
    cands = []
    for gid in gene_ids[:20]:
        row = _gene_row_by_id(int(gid))
        if row:
            cands.append(row)
    if not cands:
        # 一个候选都拿不出来就别自称 ambiguous：前端会显示「多候选 0」，
        # 用户既选不了也查不到。按未匹配返回，交给上层继续走回退路径。
        result["status"] = "not_found"
        result["notes"].append("ambiguous 候选为空，按未匹配处理")
        return result
    result["status"] = "ambiguous"
    result["resolved"] = False
    result["canonical"] = None
    result["standard"] = None
    result["name_bundle"] = None
    result["candidates"] = cands
    result["gene_id_count"] = len(cands)
    names = ", ".join(c.get("gene_name") or "?" for c in cands[:5])
    result["disambiguation"] = (
        note
        or (
            f"“{result.get('query')}”对应 {len(cands)} 个基因：{names}。"
            "请点选候选或改用更精确符号；系统不会自动选定。"
        )
    )
    result["notes"].append("status=ambiguous：禁止静默消歧")
    return result


def resolve_gene_identifier(query: str, use_ensembl_fallback: bool = True) -> dict:
    """
    统一解析入口。三态契约：
      unique     — 恰一 gene_id，填 canonical / standard / name_bundle
      ambiguous  — ≥2 gene_id，canonical=null，只给候选
      not_found  — 未命中

    符号类查询优先走 name_hit_unique / name_resolve_collision（若已灌库）；
    Ensembl/ZFIN/NCBI/RefSeq 等结构化 ID 仍走原路径。
    """
    raw = (query or "").strip()
    result = {
        "query": raw,
        "reference": reference_genome_info(),
        "status": "not_found",
        "resolved": False,
        "canonical": None,
        "candidates": [],
        "aliases": [],
        "xrefs": [],
        "disambiguation": None,
        "match_kind": None,
        "priority": None,
        "name_bundle": None,
        "notes": [],
    }
    if not raw:
        result["notes"].append("空查询")
        return result

    qn = _query_norm(raw)
    if qn in {"-", ".", "na", "n/a", "null", "none"}:
        result["notes"].append("无效查询串")
        return result

    # pax6 在 name_hit_unique 里会被 alias 折成 pax6a；这里先拦住，要求选定 pax6a 或 pax6b。
    if qn == "pax6":
        paralog_ids = []
        for sym in ("pax6a", "pax6b"):
            for row in _lookup_by_basic(sym):
                gid = row.get("gene_id")
                if gid is None or gid in paralog_ids:
                    continue
                if str(row.get("gene_name") or "").lower() == sym:
                    paralog_ids.append(gid)
        if len(paralog_ids) >= 2:
            return _fill_ambiguous(
                result,
                paralog_ids,
                "查询词 pax6 对应旁系同源基因 pax6a 与 pax6b。请选定具体 zfin_id，系统不会自动选边或合并序列。",
            )

    if not _is_structured_id_query(raw):
        history_hit = _history_alias_hit(result, raw)
        if history_hit is not None:
            return history_hit

    # 常见口语别名：先映射到标准查询串再查并集（p53→tp53）
    alias_target = KNOWN_ALIASES.get(qn)
    lookup_keys = [qn]
    if alias_target and _query_norm(alias_target) != qn:
        lookup_keys.append(_query_norm(alias_target))

    # 符号路径：并集层优先（非结构化 ID）
    if not _is_structured_id_query(raw) and _name_resolve_tables_ready():
        for key in lookup_keys:
            hit = _lookup_name_hit_unique(key)
            if hit:
                out = _fill_unique_card(
                    result,
                    int(hit["gene_id"]),
                    match_kind=str(hit.get("match_kind") or ""),
                    priority=int(hit.get("priority") or 0),
                )
                if alias_target and key == _query_norm(alias_target) and key != qn:
                    out["notes"].append(
                        f"常见别名 {raw} → {alias_target}（match_kind={out.get('match_kind')}）"
                    )
                return out
            coll = _lookup_name_collision(key)
            if coll:
                cands = _parse_gene_id_list(coll.get("candidate_gene_ids"))
                if cands:
                    return _fill_ambiguous(result, cands)
                # 候选列解析不出任何 gene_id 时**不要直接 return**：
                # 以前这里会立刻返回「ambiguous + 0 候选」，用户看到「多候选」却一个都点不了，
                # 查 actb1 / actb / gapdh 就卡在这一步。改为继续往下走多路回退，
                # 回退能给出真实候选；真有多条同样会按 ambiguous 返回，不丢歧义保护。

    # 回退：原多路查找（表未灌或结构化 ID）
    candidates = []
    normalized = alias_target or raw

    if REFSEQ_PATTERN.match(raw):
        candidates.extend(_lookup_by_refseq(raw))
        candidates.extend(_lookup_by_xref(raw.split(".")[0]))

    if ZFIN_PATTERN.match(raw):
        candidates.extend(_lookup_by_basic(raw))

    if re.fullmatch(r"\d+", raw):
        candidates.extend(_lookup_by_xref(raw))

    candidates.extend(_lookup_by_basic(normalized))
    if normalized.lower() != raw.lower():
        candidates.extend(_lookup_by_basic(raw))

    candidates.extend(_lookup_by_alias(normalized))
    if normalized.lower() != raw.lower():
        candidates.extend(_lookup_by_alias(raw))

    candidates.extend(_lookup_by_xref(raw))
    candidates.extend(_lookup_by_grcz12tu_symbol(raw))

    if re.match(r"^ENS[A-Z]+\d+", raw, re.I):
        candidates.extend(_lookup_by_ensembl_prefix(raw))
        candidates.extend(_lookup_by_ensembl_version(raw))

    candidates = _rank_candidates(candidates, raw)

    if not candidates and re.match(r"^ENS[A-Z]*G\d+", raw, re.I):
        history_ens = _history_ensembl_hit(result, raw)
        if history_ens is not None:
            return history_ens

    if not candidates and use_ensembl_fallback and should_try_ensembl_fallback(raw):
        for hit in _ensembl_resolve(raw):
            eid = hit.get("ensembl_id")
            if eid:
                candidates.extend(_lookup_by_basic(eid))
        candidates = _rank_candidates(candidates, raw)
        if candidates:
            result["notes"].append("通过 Ensembl REST 在线解析")

    if not candidates:
        result["status"] = "not_found"
        result["notes"].append(
            f"未在本地库（{REFERENCE_ASSEMBLY}）找到匹配；"
            "可运行 scripts/build_gene_aliases 与 scripts/build_name_resolve_layer"
        )
        return result

    # 回退路径也执行三态：多 gene_id → ambiguous
    uniq_ids = []
    seen = set()
    for c in candidates:
        gid = c.get("gene_id")
        if gid is None or gid in seen:
            continue
        seen.add(gid)
        uniq_ids.append(gid)

    if len(uniq_ids) > 1 and not _is_structured_id_query(raw):
        return _fill_ambiguous(result, uniq_ids)

    result["candidates"] = [
        {k: v for k, v in c.items() if k not in ("alias_source", "db_name")}
        for c in candidates
    ]
    primary = candidates[0]
    out = _fill_unique_card(result, int(primary["gene_id"]), match_kind="legacy_lookup", priority=90)
    if raw.lower() in KNOWN_ALIASES and out.get("canonical"):
        shown = (out.get("standard") or {}).get("gene_symbol") or out["canonical"].get("gene_name")
        out["notes"].append(f"常见别名 {raw} → 标准名 {shown}")
    return out


def resolve_to_gene_name(query: str) -> Optional[str]:
    """供 gene_toolkit 调用的简写：仅 unique 返回 gene_name；ambiguous/not_found 返回 None。"""
    r = resolve_gene_identifier(query, use_ensembl_fallback=False)
    if r.get("status") == "unique" and r.get("canonical"):
        return r["canonical"]["gene_name"]
    return None
