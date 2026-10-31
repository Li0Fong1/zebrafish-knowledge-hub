# -*- coding: utf-8 -*-
"""ZFIN 历史别名与旧版 Ensembl 编号。表不在时回传空结果，不猜测。"""
from __future__ import annotations

import re

_ENSEMBL_RE = re.compile(r"^(ENS[A-Z]*G\d+)", re.I)
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9.\-]{2,31}")


def _rows(sql: str, params: dict | None = None) -> list:
    try:
        from mysql_client import get_mysql_client

        res = get_mysql_client().execute_sql(sql, max_rows=50, params=params or {})
    except Exception:
        return []
    if not res.get("success"):
        return []
    return list(res.get("data") or [])


def lookup_alias(alias: str) -> list[dict]:
    key = (alias or "").strip().lower()
    if len(key) < 2:
        return []
    return _rows(
        "SELECT alias, zfin_id, current_symbol FROM symbol_alias "
        "WHERE alias = :a ORDER BY current_symbol LIMIT 20",
        {"a": key},
    )


def lookup_ensembl(ensembl_gene_id: str, release: int | None = None, assembly_name: str = "") -> dict:
    match = _ENSEMBL_RE.match((ensembl_gene_id or "").strip())
    if not match:
        return {"status": "unmapped", "hits": []}
    gene_id = match.group(1).upper()
    sql = (
        "SELECT ensembl_gene_id, ensembl_release, assembly_name, gene_symbol, "
        "zfin_id, current_symbol, current_ensembl_gene_id, match_method "
        "FROM ensembl_release_gene WHERE ensembl_gene_id = :g"
    )
    params: dict = {"g": gene_id}
    if release:
        sql += " AND ensembl_release = :r"
        params["r"] = int(release)
    if assembly_name:
        sql += " AND assembly_name = :b"
        params["b"] = assembly_name
    sql += " ORDER BY ensembl_release DESC LIMIT 20"
    hits = _rows(sql, params)
    mapped = [row for row in hits if row.get("zfin_id") and row.get("match_method") != "unmapped"]
    zfins = []
    for row in mapped:
        zfin = str(row.get("zfin_id"))
        if zfin not in zfins:
            zfins.append(zfin)
    if len(zfins) == 1:
        status = "unique"
    elif len(zfins) > 1:
        status = "ambiguous"
    else:
        status = "unmapped"
    return {"status": status, "query": gene_id, "hits": hits, "zfin_ids": zfins}


def extra_symbol_queries(question: str, limit: int = 2) -> list[str]:
    """现用名补历史别名，历史别名补现用名。最多 limit 条额外检索。"""
    tokens = []
    seen = set()
    for token in _TOKEN_RE.findall(question or ""):
        key = token.lower()
        if key in seen or key in {"the", "and", "for", "gene", "zebrafish", "danio"}:
            continue
        seen.add(key)
        tokens.append(key)
        if len(tokens) >= 8:
            break
    if not tokens:
        return []
    placeholders = ", ".join(f":t{i}" for i in range(len(tokens)))
    params = {f"t{i}": token for i, token in enumerate(tokens)}
    rows = _rows(
        "SELECT alias, current_symbol, current_symbol_lc FROM symbol_alias "
        f"WHERE alias IN ({placeholders}) OR current_symbol_lc IN ({placeholders}) "
        "ORDER BY (alias REGEXP '[0-9]') ASC, CHAR_LENGTH(alias) ASC LIMIT 20",
        params,
    )
    question_lc = (question or "").lower()
    letter_names = []
    other_names = []
    for row in rows:
        alias = str(row.get("alias") or "")
        current = str(row.get("current_symbol") or "")
        current_lc = str(row.get("current_symbol_lc") or current.lower())
        if current_lc in seen and alias and alias not in question_lc:
            target = alias
        elif alias in seen and current and current.lower() not in question_lc:
            target = current
        else:
            continue
        bucket = letter_names if target.isalpha() else other_names
        if target not in letter_names and target not in other_names:
            bucket.append(target)
    return (letter_names + other_names)[:limit]
