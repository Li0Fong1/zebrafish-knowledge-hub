# -*- coding: utf-8 -*-
"""
从现有 gene_basic / gene_alias / gene_xref / gene_assembly_xref 灌别名并集解析层。

产物：
  name_hit_unique        一词一基因
  name_resolve_collision 一词多基因（禁止静默消歧）
  gene_name_bundle       一基因全名包

不做：人/鼠 ortholog 进唯一命中；不拆 SQLite；不删扁平 gene_alias。

用法：python -m scripts.build_name_resolve_layer
"""
from __future__ import annotations

from core.entry import bootstrap_app

bootstrap_app()

import json
from collections import defaultdict
from typing import Dict, List, Set, Tuple

from common_utils import logger, safe_print
from config import NCBI_REFERENCE_ASSEMBLY
from sqlalchemy import text

# match_kind → priority（越小越优先）；不把 ortholog 写入唯一表
KIND_PRIORITY = {
    "primary_symbol": 10,
    "standard_symbol": 12,  # GRCz12tu RefSeq 标准名
    "alias_manual": 18,
    "alias_zfin": 20,
    "alias_ncbi": 30,
    "alias_uniprot": 40,
    "alias_ensembl": 50,
    "alias_other": 55,
}

SRC_MASK = {
    "zfin": 1,
    "ncbi": 2,
    "uniprot": 4,
    "ensembl": 8,
    "xref": 16,
    "gene_basic": 8,
    "manual": 1,
    "refseq": 2,
}


def _client():
    from mysql_client import get_mysql_client

    return get_mysql_client()


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def ensure_tables() -> None:
    import os
    from config import _BASE_DIR

    path = os.path.join(_BASE_DIR, "sql", "name_resolve_migration.sql")
    with open(path, encoding="utf-8") as f:
        ddl = f.read()
    statements = [s.strip() for s in ddl.split(";") if s.strip() and not s.strip().startswith("--")]
    db = next(_client().get_db())
    try:
        for stmt in statements:
            if stmt.upper().startswith("USE "):
                continue
            db.execute(text(stmt))
        db.commit()
        safe_print("[建表] name_hit_unique / collision / gene_name_bundle OK")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _alias_kind(source: str, alias_type: str) -> str:
    src = (source or "").lower()
    typ = (alias_type or "").lower()
    if src == "manual" or typ == "manual":
        return "alias_manual"
    if "zfin" in src:
        return "alias_zfin"
    if "ncbi" in src or typ in ("synonym", "ncbi", "other_designation"):
        return "alias_ncbi"
    if "uniprot" in src:
        return "alias_uniprot"
    if "ensembl" in src or src == "gene_basic":
        return "alias_ensembl"
    return "alias_other"


def _collect_hits(db) -> Tuple[Dict[str, Dict[int, Tuple[str, int, int]]], Dict[int, dict]]:
    """
    returns:
      hits[query_norm][gene_id] = (best_kind, best_priority, source_mask)
      bundles seed
    """
    hits: Dict[str, Dict[int, Tuple[str, int, int]]] = defaultdict(dict)
    bundles: Dict[int, dict] = {}

    def add(qn: str, gid: int, kind: str, mask_bit: int = 0):
        if not qn or not gid or qn in {"-", ".", "na", "n/a", "null", "none"}:
            return
        if len(qn) > 120:
            return
        pri = KIND_PRIORITY.get(kind, 99)
        cur = hits[qn].get(gid)
        if cur is None or pri < cur[1]:
            hits[qn][gid] = (kind, pri, (cur[2] if cur else 0) | mask_bit)
        elif cur is not None:
            hits[qn][gid] = (cur[0], cur[1], cur[2] | mask_bit)

    # gene_basic
    for r in db.execute(text(
        "SELECT gene_id, gene_name, gene_symbol, zfin_id FROM gene_basic"
    )).mappings():
        gid = int(r["gene_id"])
        bundles[gid] = {
            "zfin_id": (r["zfin_id"] or "").strip() or None,
            "primary_symbol": (r["gene_name"] or r["gene_symbol"] or f"gene_{gid}").strip(),
            "standard_symbol": None,
            "aliases_zfin": set(),
            "aliases_ncbi": set(),
            "aliases_uniprot": set(),
            "aliases_ensembl": set(),
            "aliases_other": set(),
        }
        for col in ("gene_name", "gene_symbol"):
            v = _norm(r.get(col) or "")
            if v:
                add(v, gid, "primary_symbol", SRC_MASK["gene_basic"])

    # GRCz12tu 标准名
    asm = NCBI_REFERENCE_ASSEMBLY
    for r in db.execute(text(
        "SELECT gene_id, gene_symbol FROM gene_assembly_xref "
        "WHERE assembly_name = :a AND gene_symbol IS NOT NULL AND TRIM(gene_symbol) != ''"
    ), {"a": asm}).mappings():
        gid = int(r["gene_id"])
        sym = (r["gene_symbol"] or "").strip()
        if gid not in bundles:
            continue
        bundles[gid]["standard_symbol"] = sym
        add(_norm(sym), gid, "standard_symbol", SRC_MASK["refseq"])

    # gene_alias
    for r in db.execute(text(
        "SELECT gene_id, alias, alias_type, source FROM gene_alias "
        "WHERE alias IS NOT NULL AND TRIM(alias) != ''"
    )).mappings():
        gid = int(r["gene_id"])
        if gid not in bundles:
            continue
        al = _norm(r["alias"])
        kind = _alias_kind(r.get("source") or "", r.get("alias_type") or "")
        bit = 0
        for k, m in SRC_MASK.items():
            if k in (r.get("source") or "").lower():
                bit |= m
                break
        else:
            bit = SRC_MASK.get("ncbi", 2) if kind == "alias_ncbi" else 0
        add(al, gid, kind, bit)
        bucket = {
            "alias_zfin": "aliases_zfin",
            "alias_ncbi": "aliases_ncbi",
            "alias_uniprot": "aliases_uniprot",
            "alias_ensembl": "aliases_ensembl",
            "alias_manual": "aliases_other",
            "alias_other": "aliases_other",
        }.get(kind, "aliases_other")
        raw = (r["alias"] or "").strip()
        if raw and raw.lower() != bundles[gid]["primary_symbol"].lower():
            bundles[gid][bucket].add(raw)

    return hits, bundles


def _flush(db, unique_rows: list, collision_rows: list, bundle_rows: list) -> None:
    if unique_rows:
        db.execute(text(
            "INSERT INTO name_hit_unique (query_norm, gene_id, match_kind, priority, source_mask) "
            "VALUES (:qn, :gid, :kind, :pri, :mask) "
            "ON DUPLICATE KEY UPDATE gene_id=VALUES(gene_id), match_kind=VALUES(match_kind), "
            "priority=VALUES(priority), source_mask=VALUES(source_mask)"
        ), unique_rows)
    if collision_rows:
        db.execute(text(
            "INSERT INTO name_resolve_collision (query_norm, gene_id_count, candidate_gene_ids) "
            "VALUES (:qn, :cnt, :cands) "
            "ON DUPLICATE KEY UPDATE gene_id_count=VALUES(gene_id_count), "
            "candidate_gene_ids=VALUES(candidate_gene_ids)"
        ), collision_rows)
    if bundle_rows:
        db.execute(text(
            "INSERT INTO gene_name_bundle "
            "(gene_id, zfin_id, primary_symbol, standard_symbol, "
            " aliases_zfin, aliases_ncbi, aliases_uniprot, aliases_ensembl, aliases_other) "
            "VALUES (:gid, :zfin, :prim, :std, :az, :an, :au, :ae, :ao) "
            "ON DUPLICATE KEY UPDATE zfin_id=VALUES(zfin_id), primary_symbol=VALUES(primary_symbol), "
            "standard_symbol=VALUES(standard_symbol), aliases_zfin=VALUES(aliases_zfin), "
            "aliases_ncbi=VALUES(aliases_ncbi), aliases_uniprot=VALUES(aliases_uniprot), "
            "aliases_ensembl=VALUES(aliases_ensembl), aliases_other=VALUES(aliases_other)"
        ), bundle_rows)


def build() -> dict:
    ensure_tables()
    db = next(_client().get_db())
    try:
        safe_print("[收集] gene_basic + alias + assembly_xref …")
        hits, bundles = _collect_hits(db)

        db.execute(text("DELETE FROM name_hit_unique"))
        db.execute(text("DELETE FROM name_resolve_collision"))
        db.execute(text("DELETE FROM gene_name_bundle"))

        unique_rows: list = []
        collision_rows: list = []
        n_unique = n_coll = 0

        for qn, gid_map in hits.items():
            if len(gid_map) == 1:
                gid, (kind, pri, mask) = next(iter(gid_map.items()))
                unique_rows.append({
                    "qn": qn, "gid": gid, "kind": kind, "pri": pri, "mask": int(mask),
                })
                n_unique += 1
            else:
                # 按 primary_symbol 排序候选
                ordered = sorted(
                    gid_map.keys(),
                    key=lambda g: (bundles.get(g, {}).get("primary_symbol") or "", g),
                )
                collision_rows.append({
                    "qn": qn,
                    "cnt": len(ordered),
                    "cands": json.dumps(ordered, ensure_ascii=False),
                })
                n_coll += 1

            if len(unique_rows) >= 800:
                _flush(db, unique_rows, [], [])
                unique_rows = []
            if len(collision_rows) >= 400:
                _flush(db, [], collision_rows, [])
                collision_rows = []

        _flush(db, unique_rows, collision_rows, [])

        bundle_rows = []
        for gid, b in bundles.items():
            bundle_rows.append({
                "gid": gid,
                "zfin": b.get("zfin_id"),
                "prim": b["primary_symbol"],
                "std": b.get("standard_symbol"),
                "az": json.dumps(sorted(b["aliases_zfin"]), ensure_ascii=False),
                "an": json.dumps(sorted(b["aliases_ncbi"]), ensure_ascii=False),
                "au": json.dumps(sorted(b["aliases_uniprot"]), ensure_ascii=False),
                "ae": json.dumps(sorted(b["aliases_ensembl"]), ensure_ascii=False),
                "ao": json.dumps(sorted(b["aliases_other"]), ensure_ascii=False),
            })
            if len(bundle_rows) >= 400:
                _flush(db, [], [], bundle_rows)
                bundle_rows = []
        _flush(db, [], [], bundle_rows)
        db.commit()

        report = {
            "name_hit_unique": n_unique,
            "name_resolve_collision": n_coll,
            "gene_name_bundle": len(bundles),
        }
        safe_print(
            f"[完成] unique={n_unique} collision={n_coll} bundles={len(bundles)}"
        )
        return report
    except Exception:
        db.rollback()
        logger.exception("build_name_resolve_layer failed")
        raise
    finally:
        db.close()


def main():
    report = build()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
