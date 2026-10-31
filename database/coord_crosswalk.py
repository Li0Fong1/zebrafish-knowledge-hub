# -*- coding: utf-8 -*-
"""S0–S3 坐标契约：静态覆盖率、基因级对照、HMAC design_bundle。

标准坐标只读 gene_assembly_xref（assembly_name=GRCz12tu）。
gene_basic.chromosome / start_pos 只当作 GRCz11，不当作标准版。
覆盖率数字来自已对账快照，请求路径不做全表 COUNT。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

STANDARD_BUILD = "GRCz12tu"
LEGACY_BUILD = "GRCz11"
# 新到旧。只有数据库里真的有坐标的版本才填数字。
KNOWN_BUILDS = ("GRCz12tu", "GRCz11", "GRCz10", "Zv9", "Zv8")
STANDARD_REFSEQ = "GCF_049306965.2"
XREF_RELEASE = "refseq_GCF_049306965.2_20261029"
TOOL_VERSION = "hub_coord_v1"
OFFTARGET_NOTICE = (
    "Hub target sequence is GRCz12tu; "
    "external CHOPCHOP/CRISPRscan off-target background is GRCz11/danRer11"
)
SNAPSHOT_PATH = Path(
    os.getenv(
        "ZEBRAFISH_XREF_SNAPSHOT_PATH",
        "/data/zoujin/deng/zebrafish_runtime/xref_coverage_snapshot.json",
    )
)

# 2026-10-20 对账闭环：v2 gene_card 唯一 zfin_id = 30332。
# has_standard_coord 22549 = 双版本 21761 + target_only 788。
_SNAPSHOT = {
    "denominator": 30332,
    "denom_zfin": 30332,
    "has_standard_coord": 22549,
    "only_legacy": 3830,
    "target_only": 788,
    "neither_coord": 3953,
    "both_standard_and_legacy": 21761,
    "xref_release": XREF_RELEASE,
    "v2_build_id": "20260922T084955Z-33db970c",
    "standard_build": STANDARD_BUILD,
    "legacy_build": LEGACY_BUILD,
    "source": "static_snapshot",
}

_REGION_RE = re.compile(
    r"^(?:chr)?([0-9]{1,2}|mt|m)\s*:\s*([0-9,]+)\s*[-–]+\s*([0-9,]+)",
    re.I,
)
_cached_snapshot: Optional[dict] = None


def provenance() -> dict:
    return {
        "standard_build": STANDARD_BUILD,
        "xref_release": XREF_RELEASE,
        "assembly_registry": f"{STANDARD_BUILD}:{STANDARD_REFSEQ}",
        "tool_version": TOOL_VERSION,
        "legacy_build": LEGACY_BUILD,
    }


def coverage_snapshot() -> dict:
    """读静态快照。档案缺失时用已对账常数，不对 MySQL 做全表 COUNT。"""
    global _cached_snapshot
    if _cached_snapshot is not None:
        return dict(_cached_snapshot)
    data = dict(_SNAPSHOT)
    try:
        if SNAPSHOT_PATH.is_file():
            loaded = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and loaded.get("denominator") == 30332:
                data = loaded
        else:
            SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
            SNAPSHOT_PATH.write_text(
                json.dumps(_SNAPSHOT, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except Exception:
        data = dict(_SNAPSHOT)
    _cached_snapshot = data
    return dict(data)


def _rows(sql: str, params: dict) -> list:
    from mysql_client import get_mysql_client

    res = get_mysql_client().execute_sql(sql, max_rows=8, params=params)
    if not res.get("success"):
        return []
    return list(res.get("data") or [])


def _strand(value: Any) -> str:
    text = str(value or "").strip()
    if text in ("1", "+1", "+"):
        return "+"
    if text in ("-1", "-"):
        return "-"
    return text or "+"


def _zfin_ok(value: Any) -> bool:
    return str(value or "").upper().startswith("ZDB-GENE-")


def _authoritative_zfin(gene_id: Optional[int], symbol: str, basic_zfin: Optional[str]):
    auth = None
    if symbol:
        for sql in (
            "SELECT zfin_id FROM zebrafish_gene_v2_20260922.gene_card "
            "WHERE LOWER(symbol) = LOWER(:sym) LIMIT 1",
            "SELECT zfin_id FROM zebrafish_gene_v2_20260922.gene_card "
            "WHERE LOWER(symbol_display) = LOWER(:sym) LIMIT 1",
        ):
            rows = _rows(sql, {"sym": symbol})
            if rows and _zfin_ok(rows[0].get("zfin_id")):
                auth = str(rows[0]["zfin_id"]).strip()
                break
    if not auth and gene_id is not None:
        rows = _rows(
            "SELECT db_id AS zfin_id FROM gene_xref "
            "WHERE gene_id = :gid AND LOWER(db_name) = 'zfin' ORDER BY id ASC LIMIT 1",
            {"gid": int(gene_id)},
        )
        if rows and _zfin_ok(rows[0].get("zfin_id")):
            auth = str(rows[0]["zfin_id"]).strip()
    if not auth and _zfin_ok(basic_zfin):
        auth = str(basic_zfin).strip()
    legacy = basic_zfin if _zfin_ok(basic_zfin) and auth and basic_zfin != auth else None
    return auth, legacy


def _coord_for_gene(gene_id: Optional[int], symbol: str, basic_zfin: Optional[str]) -> dict:
    auth, legacy_zfin = _authoritative_zfin(gene_id, symbol, basic_zfin)
    feats = []
    if auth:
        feats = _rows(
            "SELECT genome_build, chrom, start_pos, end_pos, strand "
            "FROM zebrafish_gene_v2_20260922.genome_feature WHERE zfin_id = :z",
            {"z": auth},
        )
        feats.extend(
            _rows(
                "SELECT genome_build, chrom, start_pos, end_pos, strand "
                "FROM legacy_genome_feature WHERE zfin_id = :z",
                {"z": auth},
            )
        )
    by_build = {}
    for row in feats:
        build = str(row.get("genome_build") or "")
        if build and row.get("start_pos") is not None and build not in by_build:
            by_build[build] = row
    xref = []
    basic = []
    if gene_id is not None:
        xref = _rows(
            "SELECT chromosome, start_pos, end_pos, ncbi_gene_id FROM gene_assembly_xref "
            "WHERE gene_id = :gid AND assembly_name = :asm AND start_pos IS NOT NULL LIMIT 1",
            {"gid": int(gene_id), "asm": STANDARD_BUILD},
        )
        basic = _rows(
            "SELECT chromosome, start_pos, end_pos, strand, zfin_id, gene_name "
            "FROM gene_basic WHERE gene_id = :gid LIMIT 1",
            {"gid": int(gene_id)},
        )
    std = None
    v2_std = by_build.get(STANDARD_BUILD)
    if xref:
        row = xref[0]
        std = {
            "genome_build": STANDARD_BUILD,
            "chromosome": str(row.get("chromosome") or ""),
            "start": int(row["start_pos"]),
            "end": int(row["end_pos"]) if row.get("end_pos") is not None else None,
            "strand": _strand((v2_std or {}).get("strand")),
            "source_table": "gene_assembly_xref",
        }
    leg = None
    v2_leg = by_build.get(LEGACY_BUILD)
    if v2_leg and v2_leg.get("start_pos") is not None:
        leg = {
            "genome_build": LEGACY_BUILD,
            "chromosome": str(v2_leg.get("chrom") or ""),
            "start": int(v2_leg["start_pos"]),
            "end": int(v2_leg["end_pos"]) if v2_leg.get("end_pos") is not None else None,
            "strand": _strand(v2_leg.get("strand")),
            "source_table": "zebrafish_gene_v2_20260922.genome_feature",
        }
    elif basic and basic[0].get("start_pos") is not None and int(basic[0].get("start_pos") or 0) > 0:
        row = basic[0]
        leg = {
            "genome_build": LEGACY_BUILD,
            "chromosome": str(row.get("chromosome") or ""),
            "start": int(row["start_pos"]),
            "end": int(row["end_pos"]) if row.get("end_pos") is not None else None,
            "strand": _strand(row.get("strand")),
            "source_table": "gene_basic",
        }
    if std and leg:
        state = "has_standard_coord"
    elif std and not leg:
        state = "target_only"
    elif leg and not std:
        state = "only_legacy"
    else:
        state = None
    active = STANDARD_BUILD if std else LEGACY_BUILD
    coordinates = {}
    for name in KNOWN_BUILDS:
        if name == STANDARD_BUILD and std:
            coordinates[name] = std
            continue
        if name == LEGACY_BUILD and leg:
            coordinates[name] = leg
            continue
        row = by_build.get(name)
        if row and row.get("start_pos") is not None:
            coordinates[name] = {
                "genome_build": name,
                "chromosome": str(row.get("chrom") or ""),
                "start": int(row["start_pos"]),
                "end": int(row["end_pos"]) if row.get("end_pos") is not None else None,
                "strand": _strand(row.get("strand")),
                "source_table": (
                    "legacy_genome_feature"
                    if name not in {STANDARD_BUILD, LEGACY_BUILD}
                    else "zebrafish_gene_v2_20260922.genome_feature"
                ),
            }
        else:
            coordinates[name] = {
                "genome_build": name,
                "start": None,
                "available": False,
            }
    return {
        "zfin_id": auth,
        "legacy_zfin_id": legacy_zfin,
        "coord_state": state,
        "genome_build": active if state else None,
        "source_build": LEGACY_BUILD if leg else None,
        "target_build": STANDARD_BUILD,
        "standard_coord": std,
        "legacy_coord": leg,
        "coordinates": coordinates,
        "ncbi_gene_id": str(xref[0]["ncbi_gene_id"]) if xref and xref[0].get("ncbi_gene_id") else None,
    }


def _secret() -> bytes:
    env = os.getenv("ZEBRAFISH_DESIGN_BUNDLE_SECRET", "").strip()
    if env:
        return env.encode("utf-8")
    try:
        from config import DB_CONFIG

        seed = f"hub-design-bundle|{DB_CONFIG.get('host')}|{DB_CONFIG.get('user')}|{DB_CONFIG.get('password')}|{XREF_RELEASE}"
    except Exception:
        seed = f"hub-design-bundle|{XREF_RELEASE}"
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _sign_body(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hmac.new(_secret(), raw, hashlib.sha256).hexdigest()


def issue_design_bundle(info: dict, ttl_seconds: int = 3600) -> Optional[dict]:
    """只在 coord_state=has_standard_coord 且标准坐标齐全时签发。"""
    if info.get("coord_state") != "has_standard_coord":
        return None
    coord = info.get("standard_coord") or {}
    if coord.get("start") is None or not info.get("zfin_id"):
        return None
    span = f"{coord.get('chromosome')}:{coord.get('start')}-{coord.get('end')}"
    identity = f"{STANDARD_BUILD}|{info.get('zfin_id')}|{span}|{coord.get('strand') or '+'}"
    now = int(time.time())
    payload = {
        "zfin_id": info.get("zfin_id"),
        "genome_build": STANDARD_BUILD,
        "coord_span": span,
        "strand": coord.get("strand") or "+",
        "seq_sha256": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        "xref_release": XREF_RELEASE,
        "offtarget_db_notice": OFFTARGET_NOTICE,
        "issued_at": now,
        "confirmed_at": now,
        "expires_at": now + max(60, int(ttl_seconds)),
    }
    payload["hmac_sig"] = _sign_body({k: v for k, v in payload.items() if k != "hmac_sig"})
    return payload


def verify_design_bundle(bundle: Any, expected_gene_symbol: Optional[str] = None) -> dict:
    if not isinstance(bundle, dict):
        return {"valid": False, "reason": "design_bundle 必须是物件"}
    signed = {k: v for k, v in bundle.items() if k != "hmac_sig"}
    sig = bundle.get("hmac_sig")
    if not isinstance(sig, str) or not hmac.compare_digest(_sign_body(signed), sig.strip()):
        return {"valid": False, "reason": "hmac_sig 不符，字段被改过或凭证不是服务器签发"}
    if bundle.get("xref_release") != XREF_RELEASE:
        return {"valid": False, "reason": "xref_release 与目前快照不一致"}
    if bundle.get("genome_build") != STANDARD_BUILD:
        return {"valid": False, "reason": "genome_build 不是 GRCz12tu"}
    expires = bundle.get("expires_at")
    if not isinstance(expires, (int, float)) or int(expires) < int(time.time()):
        return {"valid": False, "reason": "design_bundle 已过期"}
    if expected_gene_symbol:
        pass
    return {"valid": True, "payload": bundle, "offtarget_db_notice": OFFTARGET_NOTICE}


def _apply_identity(result: dict, info: dict) -> None:
    result["zfin_id"] = info.get("zfin_id")
    result["legacy_zfin_id"] = info.get("legacy_zfin_id")
    result["coord_state"] = info.get("coord_state")
    result["genome_build"] = info.get("genome_build")
    result["source_build"] = info.get("source_build")
    result["target_build"] = STANDARD_BUILD
    result["xref_release"] = XREF_RELEASE
    result["provenance"] = provenance()
    result["coordinates"] = info.get("coordinates") or {
        STANDARD_BUILD: info.get("standard_coord"),
        LEGACY_BUILD: info.get("legacy_coord"),
    }
    canon = result.get("canonical")
    if isinstance(canon, dict) and info.get("zfin_id"):
        if info.get("legacy_zfin_id"):
            canon["legacy_zfin_id"] = info["legacy_zfin_id"]
        canon["zfin_id"] = info["zfin_id"]
    for node in result.get("id_chain") or []:
        if isinstance(node, dict) and node.get("type") == "zfin" and info.get("zfin_id"):
            node["id"] = info["zfin_id"]
    if info.get("coord_state") == "only_legacy":
        result["code"] = "no_coord_in_standard_build"
        result["coordinates"][STANDARD_BUILD] = None
        result["error"] = {
            "code": "no_coord_in_standard_build",
            "message": (
                "This gene has no standard GRCz12tu coordinates; "
                "GRCz11 coordinates must not be treated as the standard build."
                if _answer_en()
                else "此基因在 GRCz12tu 没有标准坐标，不能把 GRCz11 坐标当成标准版。"
            ),
        }


def attach_contract(result: dict) -> dict:
    """在既有 gene-resolve / id-convert 结果上补契约字段，不改原有 candidates 结构。"""
    if not isinstance(result, dict):
        return result
    result["provenance"] = provenance()
    result["xref_release"] = XREF_RELEASE
    result["target_build"] = STANDARD_BUILD
    if result.get("status") == "ambiguous":
        result["ok"] = False
        result["resolved"] = False
        result["canonical"] = None
        result["design_bundle"] = None
        result["error"] = {
            "code": "ambiguous",
            "message": result.get("disambiguation") or (
                "Multiple candidates — please choose a zfin_id."
                if _answer_en()
                else "存在多个候选，请选定 zfin_id。"
            ),
        }
        return result
    canon = result.get("canonical") or {}
    if result.get("status") != "unique" or not canon.get("gene_id"):
        result["ok"] = False
        result["design_bundle"] = None
        return result
    info = _coord_for_gene(
        int(canon["gene_id"]),
        str(canon.get("gene_name") or ""),
        canon.get("zfin_id"),
    )
    _apply_identity(result, info)
    result["ensembl_id"] = canon.get("ensembl_id")
    result["ncbi_gene_id"] = info.get("ncbi_gene_id")
    result["ok"] = info.get("coord_state") in ("has_standard_coord", "target_only")
    result["design_bundle"] = issue_design_bundle(info)
    if info.get("coord_state") == "only_legacy":
        result["ok"] = False
        result["design_bundle"] = None
    elif not info.get("coord_state"):
        result["ok"] = False
        result["design_bundle"] = None
        result["error"] = {
            "code": "no_coord_in_standard_build",
            "message": (
                "No usable GRCz12tu standard coordinates."
                if _answer_en()
                else "没有可用的 GRCz12tu 标准坐标。"
            ),
        }
    return result


def contract_for_symbol(symbol: str) -> dict:
    from gene_id_mapper import resolve_gene_identifier

    resolved = resolve_gene_identifier(symbol, use_ensembl_fallback=False)
    attach_contract(resolved)
    return {
        "zfin_id": resolved.get("zfin_id"),
        "genome_build": resolved.get("genome_build"),
        "coord_state": resolved.get("coord_state"),
        "source_build": resolved.get("source_build"),
        "target_build": resolved.get("target_build"),
        "xref_release": XREF_RELEASE,
        "provenance": resolved.get("provenance") or provenance(),
        "design_bundle": resolved.get("design_bundle"),
        "status": resolved.get("status"),
        "ok": resolved.get("ok"),
        "error": resolved.get("error"),
    }


def _answer_en(question: str = "") -> bool:
    try:
        from zebrafish_prompt import answer_is_english

        return bool(answer_is_english(question))
    except Exception:
        return False


def _unmapped_region(raw: str, chrom: str, start: int, end: int) -> dict:
    return {
        "ok": False,
        "status": "failed",
        "query": raw,
        "coord_state": "only_legacy",
        "source_build": LEGACY_BUILD,
        "target_build": STANDARD_BUILD,
        "genome_build": LEGACY_BUILD,
        "xref_release": XREF_RELEASE,
        "design_bundle": None,
        "coordinates": {STANDARD_BUILD: None, LEGACY_BUILD: {
            "genome_build": LEGACY_BUILD,
            "chromosome": chrom,
            "start": start,
            "end": end,
        }},
        "data": {
            "source_build": LEGACY_BUILD,
            "target_build": STANDARD_BUILD,
            "coord_state": "only_legacy",
            "design_bundle": None,
        },
        "provenance": provenance(),
        "error": {
            "code": "no_coord_in_standard_build",
            "message": (
                "No GRCz12tu gene-level crosswalk for this interval. "
                "The system does not invent standard coordinates and does not do base-level liftover here."
                if _answer_en(raw)
                else "此区间没有 GRCz12tu 基因级对照。系统不做碱基级 liftover，也不伪造标准坐标。"
            ),
        },
    }


def genome_coord(q: str = "", region: str = "", zfin_id: str = "") -> dict:
    raw = (region or q or zfin_id or "").strip()
    if not raw:
        return {
            "ok": False,
            "status": "failed",
            "provenance": provenance(),
            "error": {
                "code": "empty_query",
                "message": (
                    "Provide q, zfin_id, or region."
                    if _answer_en()
                    else "请提供 q、zfin_id 或 region。"
                ),
            },
        }
    from coord_liftover import genome_coord_liftover

    lifted = genome_coord_liftover(raw)
    if lifted is not None:
        return lifted

    matched = _REGION_RE.match(raw.replace(" ", ""))
    if matched and not raw.upper().startswith("ZDB-"):
        chrom = matched.group(1)
        start = int(matched.group(2).replace(",", ""))
        end = int(matched.group(3).replace(",", ""))
        hits = _rows(
            "SELECT zfin_id FROM zebrafish_gene_v2_20260922.genome_feature "
            "WHERE (chrom = :c OR chrom = :c2) AND start_pos = :s AND end_pos = :e LIMIT 1",
            {"c": chrom, "c2": f"chr{chrom}", "s": start, "e": end},
        )
        if not hits or not hits[0].get("zfin_id"):
            return _unmapped_region(raw, chrom, start, end)
        raw = str(hits[0]["zfin_id"])

    from gene_id_mapper import resolve_gene_identifier

    resolved = resolve_gene_identifier(raw, use_ensembl_fallback=False)
    attach_contract(resolved)
    state = resolved.get("coord_state")
    out = {
        "ok": bool(resolved.get("ok")),
        "status": "ambiguous" if resolved.get("status") == "ambiguous" else (
            "success" if resolved.get("ok") else "failed"
        ),
        "query": raw,
        "zfin_id": resolved.get("zfin_id"),
        "symbol": ((resolved.get("canonical") or {}).get("gene_name")),
        "source_build": resolved.get("source_build"),
        "target_build": STANDARD_BUILD,
        "genome_build": resolved.get("genome_build"),
        "coord_state": state,
        "xref_release": XREF_RELEASE,
        "coordinates": resolved.get("coordinates"),
        "candidates": resolved.get("candidates") if resolved.get("status") == "ambiguous" else None,
        "disambiguation": resolved.get("disambiguation"),
        "design_bundle": resolved.get("design_bundle"),
        "provenance": provenance(),
        "error": resolved.get("error"),
    }
    out["data"] = {
        "zfin_id": out.get("zfin_id"),
        "symbol": out.get("symbol"),
        "source_build": out.get("source_build"),
        "target_build": STANDARD_BUILD,
        "coord_state": state,
        "design_bundle": out.get("design_bundle"),
    }
    return out
