# -*- coding: utf-8 -*-
"""Read-only Hub tools for the versioned ZFIN-style gene schema."""
from __future__ import annotations

import time
import unicodedata
from typing import Any, Optional

from mysql_client import get_mysql_client

SCHEMA = "zebrafish_gene_v2_20260922"
MAX_CANDIDATES = 50
MAX_XREFS = 20
MAX_CARDS = 20
MAX_EXPRESSION = 200


def _query(sql: str, params: Optional[dict] = None, max_rows: int = 0) -> list[dict]:
    result = get_mysql_client().execute_sql(sql, max_rows=max_rows, params=params)
    if not result.get("success"):
        raise RuntimeError(result.get("error") or "v2 database query failed")
    return list(result.get("data") or [])


def _meta() -> dict[str, str]:
    rows = _query(
        f"SELECT build_id, norm_version FROM {SCHEMA}.name_resolve_meta "
        "WHERE is_active=1 LIMIT 1",
        max_rows=1,
    )
    if not rows:
        raise RuntimeError("v2 active name_resolve_meta is missing")
    return {
        "build_id": str(rows[0]["build_id"]),
        "norm_version": str(rows[0]["norm_version"]),
    }


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold().strip()


def _envelope(
    tool: str,
    started: float,
    *,
    status: str,
    code: Optional[str],
    data: Any,
) -> dict:
    meta = _meta()
    return {
        **meta,
        "tool": tool,
        "status": status,
        "code": code,
        "data": data,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def _card(zfin_id: str) -> Optional[dict]:
    rows = _query(
        f"SELECT * FROM {SCHEMA}.gene_card WHERE zfin_id=:zfin_id LIMIT 1",
        {"zfin_id": zfin_id},
        max_rows=1,
    )
    return rows[0] if rows else None


def _xrefs_for_gene(zfin_id: str, limit: int = MAX_XREFS) -> list[dict]:
    return _query(
        f"SELECT db, acc, is_primary FROM {SCHEMA}.gene_xref "
        "WHERE zfin_id=:zfin_id ORDER BY db, is_primary DESC, acc "
        "LIMIT :limit",
        {"zfin_id": zfin_id, "limit": min(max(int(limit), 1), MAX_XREFS)},
        max_rows=MAX_XREFS,
    )


def gene_resolve(query: str, limit_candidates: int = MAX_CANDIDATES) -> dict:
    started = time.perf_counter()
    query_norm = _norm(query)
    if not query_norm:
        return _envelope(
            "gene_resolve",
            started,
            status="missing",
            code="not_found",
            data={"query": query, "query_norm": query_norm},
        )
    hits = _query(
        f"SELECT query_norm, status, zfin_id, gene_id_count, match_kind, source_mask "
        f"FROM {SCHEMA}.name_resolve_hit WHERE query_norm=:query_norm LIMIT 1",
        {"query_norm": query_norm},
        max_rows=1,
    )
    if not hits:
        return _envelope(
            "gene_resolve",
            started,
            status="missing",
            code="not_found",
            data={"query": query, "query_norm": query_norm},
        )
    hit = hits[0]
    status = str(hit["status"])
    data: dict[str, Any] = {
        "query": query,
        "query_norm": query_norm,
        "resolution": status,
        "gene_id_count": int(hit["gene_id_count"]),
        "match_kind": hit.get("match_kind"),
        "source_mask": hit.get("source_mask"),
    }
    if status == "unique":
        zfin_id = str(hit["zfin_id"])
        data.update(
            {
                "zfin_id": zfin_id,
                "gene_card": _card(zfin_id),
                "xrefs": _xrefs_for_gene(zfin_id),
                "candidates": [],
            }
        )
        return _envelope(
            "gene_resolve", started, status="ok", code="unique", data=data
        )
    if status == "ambiguous":
        limit = min(max(int(limit_candidates), 1), MAX_CANDIDATES)
        candidates = _query(
            f"SELECT c.zfin_id, c.sort_order, c.match_kind, "
            f"c.primary_symbol_snapshot, g.full_name "
            f"FROM {SCHEMA}.name_resolve_candidate c "
            f"LEFT JOIN {SCHEMA}.gene_card g ON g.zfin_id=c.zfin_id "
            "WHERE c.query_norm=:query_norm ORDER BY c.sort_order LIMIT :limit",
            {"query_norm": query_norm, "limit": limit},
            max_rows=limit,
        )
        data.update({"zfin_id": None, "candidates": candidates})
        return _envelope(
            "gene_resolve", started, status="ok", code="ambiguous", data=data
        )
    raise RuntimeError(f"unsupported name_resolve_hit.status={status}")


def gene_xref_lookup(db: str, acc: str, limit: int = MAX_XREFS) -> dict:
    started = time.perf_counter()
    cap = min(max(int(limit), 1), MAX_XREFS)
    rows = _query(
        f"SELECT x.zfin_id, x.db, x.acc, x.is_primary, "
        f"g.symbol, g.full_name FROM {SCHEMA}.gene_xref x "
        f"LEFT JOIN {SCHEMA}.gene_card g ON g.zfin_id=x.zfin_id "
        "WHERE x.db=:db AND x.acc=:acc ORDER BY x.zfin_id LIMIT :limit",
        {"db": db.strip(), "acc": acc.strip(), "limit": cap},
        max_rows=cap,
    )
    if not rows:
        return _envelope(
            "gene_xref_lookup",
            started,
            status="missing",
            code="no_xref",
            data={"db": db, "acc": acc, "matches": []},
        )
    return _envelope(
        "gene_xref_lookup",
        started,
        status="ok",
        code="multi" if len(rows) > 1 else "one",
        data={"db": db, "acc": acc, "matches": rows},
    )


def gene_history_follow(old_acc: str) -> dict:
    started = time.perf_counter()
    rows = _query(
        f"SELECT event, old_acc, new_zfin_id, old_symbol, new_symbol, seen_at, note "
        f"FROM {SCHEMA}.gene_history WHERE old_acc=:old_acc "
        "ORDER BY new_zfin_id LIMIT 20",
        {"old_acc": old_acc.strip()},
        max_rows=20,
    )
    if not rows:
        return _envelope(
            "gene_history_follow",
            started,
            status="missing",
            code="no_history",
            data={"old_acc": old_acc, "matches": []},
        )
    return _envelope(
        "gene_history_follow",
        started,
        status="ok",
        code="followed",
        data={"old_acc": old_acc, "matches": rows},
    )


def gene_card_get(zfin_id: Optional[str] = None, symbol: Optional[str] = None) -> dict:
    started = time.perf_counter()
    rows: list[dict]
    if zfin_id:
        rows = _query(
            f"SELECT * FROM {SCHEMA}.gene_card WHERE zfin_id=:value LIMIT :limit",
            {"value": zfin_id.strip(), "limit": MAX_CARDS},
            max_rows=MAX_CARDS,
        )
    elif symbol:
        rows = _query(
            f"SELECT * FROM {SCHEMA}.gene_card WHERE symbol=:value "
            "ORDER BY zfin_id LIMIT :limit",
            {"value": symbol.strip(), "limit": MAX_CARDS},
            max_rows=MAX_CARDS,
        )
    else:
        return _envelope(
            "gene_card_get",
            started,
            status="error",
            code="need_zfin_id_or_symbol",
            data=None,
        )
    if not rows:
        return _envelope(
            "gene_card_get",
            started,
            status="missing",
            code="no_gene_card",
            data=[],
        )
    return _envelope(
        "gene_card_get", started, status="ok", code=None, data=rows
    )


def genome_coord_get(genome_build: str, zfin_id: str) -> dict:
    started = time.perf_counter()
    rows = _query(
        f"SELECT genome_build, zfin_id, ensembl_gene_id, chrom, "
        f"start_pos, end_pos, strand FROM {SCHEMA}.genome_feature "
        "WHERE genome_build=:genome_build AND zfin_id=:zfin_id LIMIT 1",
        {"genome_build": genome_build.strip(), "zfin_id": zfin_id.strip()},
        max_rows=1,
    )
    if not rows:
        return _envelope(
            "genome_coord_get",
            started,
            status="missing",
            code="no_coord_in_build",
            data={"genome_build": genome_build, "zfin_id": zfin_id},
        )
    return _envelope(
        "genome_coord_get", started, status="ok", code=None, data=rows[0]
    )


def expression_tpm_get(
    zfin_id: Optional[str] = None,
    gene_id: Optional[str] = None,
    limit: int = MAX_EXPRESSION,
) -> dict:
    started = time.perf_counter()
    cap = min(max(int(limit), 1), MAX_EXPRESSION)
    resolved_gene_id = gene_id.strip() if gene_id else None
    if zfin_id and not resolved_gene_id:
        mapped = _query(
            f"SELECT gene_id FROM {SCHEMA}.expression_gene_map "
            "WHERE zfin_id=:zfin_id ORDER BY mapping_rank, gene_id LIMIT 1",
            {"zfin_id": zfin_id.strip()},
            max_rows=1,
        )
        if mapped:
            resolved_gene_id = str(mapped[0]["gene_id"])
    if not resolved_gene_id:
        return _envelope(
            "expression_tpm_get",
            started,
            status="missing",
            code="no_expression",
            data={"zfin_id": zfin_id, "gene_id": gene_id},
        )
    unmapped = _query(
        f"SELECT matrix_id, gene_id, gene_name, reason "
        f"FROM {SCHEMA}.unmapped_expression_gene "
        "WHERE gene_id=:gene_id LIMIT 1",
        {"gene_id": resolved_gene_id},
        max_rows=1,
    )
    if unmapped:
        return _envelope(
            "expression_tpm_get",
            started,
            status="missing",
            code=str(unmapped[0]["reason"]),
            data=unmapped[0],
        )
    rows = _query(
        f"SELECT matrix_id, gene_id, gene_name, sample_id, tpm "
        f"FROM {SCHEMA}.expression_tpm WHERE gene_id=:gene_id "
        "ORDER BY sample_id LIMIT :limit",
        {"gene_id": resolved_gene_id, "limit": cap},
        max_rows=cap,
    )
    if not rows:
        return _envelope(
            "expression_tpm_get",
            started,
            status="missing",
            code="no_expression",
            data={"zfin_id": zfin_id, "gene_id": resolved_gene_id},
        )
    return _envelope(
        "expression_tpm_get",
        started,
        status="ok",
        code=None,
        data={"zfin_id": zfin_id, "gene_id": resolved_gene_id, "rows": rows},
    )
