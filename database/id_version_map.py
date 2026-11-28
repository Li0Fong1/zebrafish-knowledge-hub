# -*- coding: utf-8 -*-
"""批量 ID 互转的「版本」维度：18 个装配任意来源 → 任意目标。

背景
----
Liftover 页的「坐标换算」早就是「来源装配 → 目标装配」（18 个版本、36 条
UCSC chain、306 个有序版本对全部可达）；「批量 ID 互转」原来却只有
GRCz11 / GRCz12tu 两个版本，因为只有这两版有本地基因 ID 对照表。

数据现实（2026-11-12 线上库实测）
--------------------------------
  · ``gene_basic`` / ``gene_xref``          —— GRCz11 本地基因库（32,139 / 别名 118,174）
  · ``gene_assembly_xref``                  —— GRCz11 6,865 行、GRCz12tu 23,950 行官方坐标
  · ``gene_id_map_grcz12tu``  28,284 行    —— GRCz11 基因 → GRCz12tu 基因
  · ``gene_master_grcz12tu``  53,359 行    —— GRCz12tu 主表（符号/坐标/Ensembl/ZFIN/NCBI/UniProt）
  · 其余 16 个装配（Zv3–Zv9 / GRCz10 / GRCz12ab / GRCz13ab / 6 个 NCBI 品系组装）
    **没有独立基因注释表**，只有 UCSC chain 可以做坐标换算。

转换语义（诚实口径，不伪造版本专属 ID）
--------------------------------------
  1. GRCz11 / GRCz12tu 各自的同版本互查：完全沿用原有的本地库互查，行为不变；
     两版互相转换也由本地对照表决定输出形态，但解析统一走「本地库 → 主表」，
     顺带修掉旧路径不认 UniProt ID 的缺陷。
  2. 其余任意组合：把输入 ID 解析到**基因实体**（本地库 → 映射表 → GRCz12tu 主表），
     输出该实体的**跨版本稳定标识符**（基因符号 / Ensembl gene ID / ZFIN / NCBI Gene /
     UniProt —— 这些标识不随参考组装版本改变），并把 hub 坐标经 UCSC chain
     换算成**来源版本坐标**与**目标版本坐标**。坐标才是版本特异的那一维。
     结果里用 ``identity_basis=stable_ids_with_chain_coords`` 与 notes 明确标注，
     绝不把 GRCz11/GRCz12tu 的 ID 冒充成 Zv3 等版本的"专属 ID"。
  3. chain 换算失败（跨断裂点 / 无坐标）只让坐标留空并写明原因，ID 转换本身照常
     成立；不给近似坐标、不给假 ID。

版本清单以 ``coord_liftover.SELECTABLE_BUILDS`` 为唯一事实来源，避免两处漂移。
返回结构沿用前端 ``flatten()`` 认识的形状（canonical / standard / xrefs），
另外补 ``target_coords`` / ``source_coords`` 与顶层 ``builds``。
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple

BUILD_GRCZ11 = "GRCz11"
BUILD_GRCZ12TU = "GRCz12tu"
LOCAL_TABLE_BUILDS = (BUILD_GRCZ11, BUILD_GRCZ12TU)


def _build_catalog() -> Tuple[str, ...]:
    """18 个装配以 coord_liftover 为准；导入失败时退回两版（离线单测用）。"""
    try:
        from coord_liftover import SELECTABLE_BUILDS
        out = tuple(str(b) for b in SELECTABLE_BUILDS if b)
        if out:
            return out
    except Exception:  # noqa: BLE001 - 坐标模块依赖链文件，导入不应拖垮 ID 转换
        pass
    return LOCAL_TABLE_BUILDS


SUPPORTED_BUILDS = _build_catalog()

_ALIASES = {
    "grcz11": BUILD_GRCZ11,
    "grcz11ab": BUILD_GRCZ11,
    "zv11": BUILD_GRCZ11,
    "danrer11": BUILD_GRCZ11,
    "grcz12tu": BUILD_GRCZ12TU,
    "grcz12": BUILD_GRCZ12TU,
    "grcz12tubingen": BUILD_GRCZ12TU,
}


def normalize_build(value: Optional[str]) -> Optional[str]:
    """收敛到 SUPPORTED_BUILDS 之一；别名交给 coord_liftover；不认识返回 None。"""
    v = (value or "").strip()
    if not v:
        return None
    if v in SUPPORTED_BUILDS:
        return v
    # 2026-11-13：Ensembl release 版本（ens:54 … ens:116）与 18 个 NCBI 装配
    # 并列为「来源/目标版本」。这里先认它，交给 ensembl_version_map 走另一条管线。
    try:
        from ensembl_version_map import normalize as _ens_normalize
        e = _ens_normalize(v)
        if e:
            return e
    except Exception:  # noqa: BLE001
        pass
    key = re.sub(r"[\s\-_]", "", v).lower()
    if key in _ALIASES:
        return _ALIASES[key]
    try:
        from coord_liftover import normalize_build as _cl_normalize
        c = _cl_normalize(v)
        if c in SUPPORTED_BUILDS:
            return c
    except Exception:  # noqa: BLE001
        pass
    return None


def _client():
    from mysql_client import get_mysql_client

    return get_mysql_client()


def _split_first(text: Any) -> str:
    """逗号分隔的多值字段取第一个（Ensembl/ZFIN/NCBI/UniProt 可能有多条）。"""
    s = str(text or "").strip()
    if not s:
        return ""
    return s.split(",")[0].strip()


def _in_clause(values: Iterable[Any], cast=int) -> str:
    """把一批 ID 拼成 IN 子句（全部经类型强转，杜绝拼接注入）。

    ⚠ 仅用于**数值列**（int/bigint）。输出的字面量不带引号，
    若用于 varchar 列（如 grcz12tu_gene_id）会触发 MySQL 隐式类型转换，
    导致索引/主键完全失效（实测 48518 行全表扫描、慢 3000 倍）。
    字符串列请改用 :func:`_quote_list`。
    """
    out = []
    for v in values:
        try:
            out.append(str(cast(v)))
        except (TypeError, ValueError):
            continue
    return ",".join(out) if out else "NULL"


def _quote_list(values: Iterable[str]) -> str:
    safe = []
    for v in values:
        s = re.sub(r"[^0-9A-Za-z._\-]", "", str(v or ""))
        if s:
            safe.append("'%s'" % s)
    return ",".join(safe) if safe else "NULL"


def _base_row(query: str, builds: dict) -> Dict[str, Any]:
    return {
        "query": query,
        "resolved": False,
        "status": "not_found",
        "canonical": None,
        "standard": None,
        "candidates": [],
        "xrefs": [],
        "aliases": [],
        "match_kind": None,
        "notes": [],
        "builds": builds,
    }


def _row_from_master(query: str, master: dict, builds: dict, method: str = "", score: Any = "") -> Dict[str, Any]:
    """用 GRCz12tu 主表的一行，拼成前端认识的 result 结构。"""
    symbol = (master.get("symbol") or "").strip()
    chrom = str(master.get("chrom") or "").strip()
    start = master.get("start_pos")
    end = master.get("end_pos")
    strand = (master.get("strand") or "").strip()
    coords = ""
    if chrom and start is not None and end is not None:
        coords = "%s:%s-%s" % (chrom, format(int(start), ","), format(int(end), ","))
        if strand:
            coords += "(%s)" % strand

    ens = _split_first(master.get("ensembl_ids"))
    zfin = _split_first(master.get("zfin_ids"))
    ncbi = _split_first(master.get("ncbi_gene_ids"))
    uni = _split_first(master.get("uniprot_ids"))

    xrefs = []
    if ens:
        xrefs.append({"db_name": "ensembl", "db_id": ens})
    if ncbi:
        xrefs.append({"db_name": "ncbi_gene_grcz12tu", "db_id": ncbi})
    if zfin:
        xrefs.append({"db_name": "zfin", "db_id": zfin})
    if uni:
        xrefs.append({"db_name": "uniprot", "db_id": uni})

    return {
        "query": query,
        "resolved": True,
        "status": "unique",
        "canonical": {
            "gene_id": master.get("grcz12tu_gene_id"),
            "gene_name": symbol,
            "gene_symbol": "",
            "ensembl_id": ens,
            "zfin_id": zfin,
            "uniprot_id": uni,
            "chromosome": chrom,
        },
        "standard": {
            "gene_symbol": symbol,
            "assembly": BUILD_GRCZ12TU,
            "role": "canonical",
            "source": "GRCz12tu 主表",
        },
        "candidates": [],
        "xrefs": xrefs,
        "aliases": [],
        "match_kind": method or "id_map",
        "notes": [],
        "target_coords": coords,
        "match_score": score,
        "builds": builds,
    }


def _better(a: dict, b: dict) -> bool:
    """两条映射谁更可信：id_direct 优先，其次 score 高者优先。"""
    ra = 2 if str(a.get("method")) == "id_direct" else 1
    rb = 2 if str(b.get("method")) == "id_direct" else 1
    if ra != rb:
        return ra > rb
    try:
        return float(a.get("score") or 0) > float(b.get("score") or 0)
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------- GRCz11 → GRCz12tu

def _to_grcz12tu(identifiers: List[str], builds: dict) -> Dict[str, Any]:
    from gene_id_mapper import resolve_batch

    rows = resolve_batch(identifiers)
    local_ids = []
    for r in rows:
        c = r.get("canonical") or {}
        if r.get("resolved") and c.get("gene_id") is not None:
            local_ids.append(c["gene_id"])

    maps: Dict[int, dict] = {}
    masters: Dict[str, dict] = {}
    if local_ids:
        res = _client().execute_sql(
            "SELECT source_gene_id, grcz12tu_gene_id, grcz12tu_symbol, method, score"
            " FROM gene_id_map_grcz12tu WHERE source_gene_id IN (%s)" % _in_clause(local_ids),
            max_rows=0,
        )
        for m in res.get("data") or []:
            sid = int(m["source_gene_id"])
            prev = maps.get(sid)
            # 同一 source_gene_id 可能有多条映射：优先 id_direct，其次比 score
            if prev is None or _better(m, prev):
                maps[sid] = m
        tu_ids = [str(m.get("grcz12tu_gene_id") or "") for m in maps.values()]
        tu_ids = [t for t in tu_ids if t]
        if tu_ids:
            res2 = _client().execute_sql(
                "SELECT grcz12tu_gene_id, symbol, chrom, start_pos, end_pos, strand,"
                " ensembl_ids, zfin_ids, uniprot_ids, ncbi_gene_ids"
                " FROM gene_master_grcz12tu WHERE grcz12tu_gene_id IN (%s)"
                % _quote_list(tu_ids),
                max_rows=0,
            )
            for m in res2.get("data") or []:
                masters[str(m["grcz12tu_gene_id"])] = m

    out = []
    for r in rows:
        q = r.get("query") or ""
        c = r.get("canonical") or {}
        gid = c.get("gene_id")
        if not r.get("resolved") or gid is None:
            base = _base_row(q, builds)
            base["status"] = r.get("status") or "not_found"
            base["candidates"] = r.get("candidates") or []
            base["notes"] = list(r.get("notes") or [])
            out.append(base)
            continue
        m = maps.get(int(gid))
        master = masters.get(str(m.get("grcz12tu_gene_id"))) if m else None
        if not master:
            row = _base_row(q, builds)
            row["status"] = "unmapped"
            row["resolved"] = True
            row["canonical"] = c
            row["notes"] = ["GRCz12tu 主表中没有该基因的对应记录"]
            out.append(row)
            continue
        row = _row_from_master(q, master, builds, method=str(m.get("method") or ""), score=m.get("score"))
        row["source_gene_id"] = gid
        out.append(row)

    return _pack(out, identifiers, builds)


# ---------------------------------------------------------------- GRCz12tu → GRCz11

def _norm_symbol(s: Any) -> str:
    return re.sub(r"\s+", "", str(s or "").strip().lower())


def _match_masters(identifiers: List[str]) -> Dict[str, dict]:
    """按 符号 → NCBI → Ensembl → ZFIN 的顺序，把输入匹配到 GRCz12tu 主表行。"""
    picked: Dict[str, dict] = {}
    if not identifiers:
        return picked

    symbols = [i for i in identifiers if re.fullmatch(r"[A-Za-z0-9._\-]{1,40}", i or "")]
    numeric = [i for i in identifiers if re.fullmatch(r"\d{3,12}", i or "")]
    ensembl = [i for i in identifiers if re.match(r"^ENS[A-Z]*\d+(\.\d+)?$", i or "", re.I)]
    zfin = [i for i in identifiers if re.match(r"^ZDB-GENE-", i or "", re.I)]

    def _run(sql: str) -> List[dict]:
        try:
            res = _client().execute_sql(sql, max_rows=0)
            return list(res.get("data") or [])
        except Exception:  # noqa: BLE001 - 单类匹配失败不应拖垮整批
            return []

    cols = ("grcz12tu_gene_id, symbol, chrom, start_pos, end_pos, strand,"
            " ensembl_ids, zfin_ids, uniprot_ids, ncbi_gene_ids")

    by_symbol: Dict[str, dict] = {}
    by_ncbi: Dict[str, dict] = {}
    by_ens: Dict[str, dict] = {}
    by_zfin: Dict[str, dict] = {}

    if symbols:
        for m in _run(
            "SELECT %s FROM gene_master_grcz12tu WHERE symbol IN (%s)"
            % (cols, _quote_list(symbols))
        ):
            by_symbol.setdefault(_norm_symbol(m.get("symbol")), m)

    if numeric:
        pattern = "(^|,)(%s)(,|$)" % "|".join(sorted(set(numeric)))
        for m in _run(
            "SELECT %s FROM gene_master_grcz12tu"
            " WHERE REPLACE(ncbi_gene_ids,' ','') REGEXP '%s'" % (cols, pattern)
        ):
            for nid in str(m.get("ncbi_gene_ids") or "").split(","):
                by_ncbi.setdefault(nid.strip(), m)

    if ensembl:
        bases = sorted({e.split(".")[0].upper() for e in ensembl})
        pattern = "(^|,)(%s)(\\.\\d+)?(,|$)" % "|".join(bases)
        for m in _run(
            "SELECT %s FROM gene_master_grcz12tu WHERE ensembl_ids REGEXP '%s'" % (cols, pattern)
        ):
            for e in str(m.get("ensembl_ids") or "").split(","):
                by_ens.setdefault(e.strip().split(".")[0].upper(), m)

    if zfin:
        pattern = "(^|,)(%s)(,|$)" % "|".join(sorted({z.upper() for z in zfin}))
        for m in _run(
            "SELECT %s FROM gene_master_grcz12tu"
            " WHERE UPPER(REPLACE(zfin_ids,' ','')) REGEXP '%s'" % (cols, pattern)
        ):
            for z in str(m.get("zfin_ids") or "").split(","):
                by_zfin.setdefault(z.strip().upper(), m)

    for ident in identifiers:
        key = _norm_symbol(ident)
        hit = by_symbol.get(key)
        if not hit and re.fullmatch(r"\d{3,12}", ident or ""):
            hit = by_ncbi.get(ident)
        if not hit and re.match(r"^ENS[A-Z]*\d+", ident or "", re.I):
            hit = by_ens.get(ident.split(".")[0].upper())
        if not hit and re.match(r"^ZDB-GENE-", ident or "", re.I):
            hit = by_zfin.get(ident.upper())
        if hit:
            picked[ident] = hit
    return picked


def _to_grcz11(identifiers: List[str], builds: dict) -> Dict[str, Any]:
    masters = _match_masters(identifiers)
    tu_ids = [str(m.get("grcz12tu_gene_id") or "") for m in masters.values() if m.get("grcz12tu_gene_id")]

    tu_to_local: Dict[str, int] = {}
    if tu_ids:
        res = _client().execute_sql(
            "SELECT grcz12tu_gene_id, source_gene_id FROM gene_id_map_grcz12tu"
            " WHERE grcz12tu_gene_id IN (%s)" % _quote_list(tu_ids),
            max_rows=0,
        )
        for m in res.get("data") or []:
            tu_to_local[str(m["grcz12tu_gene_id"])] = int(m["source_gene_id"])

    basics = {}
    xref_map: Dict[int, List[dict]] = {}
    local_ids = sorted(set(tu_to_local.values()))
    if local_ids:
        res = _client().execute_sql(
            "SELECT gene_id, gene_name, gene_symbol, ensembl_id, zfin_id, chromosome"
            " FROM gene_basic WHERE gene_id IN (%s)" % _in_clause(local_ids),
            max_rows=0,
        )
        for b in res.get("data") or []:
            basics[int(b["gene_id"])] = b
        res2 = _client().execute_sql(
            "SELECT gene_id, db_name, db_id FROM gene_xref WHERE gene_id IN (%s)"
            % _in_clause(local_ids),
            max_rows=0,
        )
        for x in res2.get("data") or []:
            xref_map.setdefault(int(x["gene_id"]), []).append(
                {"db_name": x.get("db_name"), "db_id": x.get("db_id")}
            )

    out = []
    for ident in identifiers:
        master = masters.get(ident)
        if not master:
            out.append(_base_row(ident, builds))
            continue
        tu_gid = str(master.get("grcz12tu_gene_id") or "")
        local_gid = tu_to_local.get(tu_gid)
        basic = basics.get(local_gid) if local_gid is not None else None
        if not basic:
            row = _base_row(ident, builds)
            row["status"] = "unmapped"
            row["resolved"] = True
            row["notes"] = ["本地库（GRCz11）没有该 GRCz12tu 基因的对应记录"]
            row["canonical"] = {
                "gene_id": tu_gid,
                "gene_name": master.get("symbol") or "",
                "gene_symbol": "",
                "ensembl_id": _split_first(master.get("ensembl_ids")),
                "zfin_id": _split_first(master.get("zfin_ids")),
                "uniprot_id": _split_first(master.get("uniprot_ids")),
                "chromosome": str(master.get("chrom") or ""),
            }
            out.append(row)
            continue
        xrefs = xref_map.get(local_gid) or []
        # gene_xref 里同时存在 ncbi_gene 与 ncbi_gene_grcz12tu 两行（同一个号），
        # 目标是 GRCz11 时只保留前者，并按 (db, id) 去重，避免表格里出现两个 NCBI。
        dedup, seen_x = [], set()
        for x in xrefs:
            if str(x.get("db_name")) == "ncbi_gene_grcz12tu":
                continue
            key = (x.get("db_name"), x.get("db_id"))
            if key in seen_x:
                continue
            seen_x.add(key)
            dedup.append(x)
        xrefs = dedup
        ncbi = next((x["db_id"] for x in xrefs if x.get("db_name") == "ncbi_gene"), "")
        uni = next((x["db_id"] for x in xrefs if x.get("db_name") == "uniprot"), "")
        row = {
            "query": ident,
            "resolved": True,
            "status": "unique",
            "canonical": {
                "gene_id": local_gid,
                "gene_name": basic.get("gene_name") or "",
                "gene_symbol": basic.get("gene_symbol") or "",
                "ensembl_id": basic.get("ensembl_id") or "",
                "zfin_id": basic.get("zfin_id") or "",
                "uniprot_id": uni,
                "chromosome": basic.get("chromosome") or "",
            },
            "standard": {
                "gene_symbol": basic.get("gene_name") or "",
                "assembly": BUILD_GRCZ11,
                "role": "canonical",
                "source": "本地库 GRCz11",
            },
            "candidates": [],
            "xrefs": xrefs,
            "aliases": [],
            "match_kind": "id_map",
            "notes": [],
            "target_coords": "",
            "builds": builds,
        }
        if ncbi and not any(x.get("db_name") == "ncbi_gene" for x in xrefs):
            row["xrefs"] = xrefs + [{"db_name": "ncbi_gene", "db_id": ncbi}]
        out.append(row)

    return _pack(out, identifiers, builds)


# ---------------------------------------------------------------- 任意版本通用管线
# 除 GRCz11 / GRCz12tu 外的 16 个装配没有基因注释表，只有 UCSC chain。
# 因此"跨版本 ID 转换"= 稳定标识符（版本无关）+ 由 chain 换算出的版本特异坐标。
# 这两件事分开标注，绝不把中枢版本的 ID 伪装成目标版本专属 ID。

_STABLE_NOTE_ZH = (
    "该装配没有独立基因注释表：基因符号 / Ensembl / ZFIN / NCBI Gene / UniProt "
    "为跨版本稳定标识；坐标是 UCSC chain 从 {hub} 换算得到的对应基因组区间，"
    "不是该版本的官方基因注释坐标（与数据库注释边界实测有几十 bp ~ 几 kb 差异）；"
    "换算失败一律留空，不给近似坐标。"
)
_STABLE_NOTE_EN = (
    "This assembly has no independent gene annotation table: symbol / Ensembl / ZFIN / "
    "NCBI Gene / UniProt are version-stable identifiers. Coordinates are genomic "
    "intervals lifted by UCSC chain from {hub}, NOT official annotation coordinates "
    "for this assembly (differences of tens of bp to a few kb were measured). Failed "
    "conversions are left blank and never approximated."
)


def _fmt_span(chrom: Any, start: Any, end: Any, strand: Any = "") -> str:
    """统一坐标写法：5:24,742,747-24,754,325(+)（与 GRCz12tu 主表一致）。"""
    c = str(chrom or "").strip()
    if not c or start is None or end is None:
        return ""
    try:
        s, e = int(start), int(end)
    except (TypeError, ValueError):
        return ""
    if e < s:
        return ""
    sg = str(strand or "").strip()
    return "%s:%s-%s%s" % (c, format(s, ","), format(e, ","),
                           "(%s)" % sg if sg in ("+", "-") else "")


def _hub_coords(row: Optional[dict], hub_build: str) -> Optional[dict]:
    """主表 / xref 行 → hub 坐标字典（没有坐标就返回 None，不猜）。"""
    if not row:
        return None
    chrom = row.get("chrom") if hub_build == BUILD_GRCZ12TU else row.get("chromosome")
    c = str(chrom or "").strip()
    s, e = row.get("start_pos"), row.get("end_pos")
    if not c or s is None or e is None:
        return None
    try:
        s, e = int(s), int(e)
    except (TypeError, ValueError):
        return None
    if e < s:
        return None
    sg = str(row.get("strand") or "+").strip()
    return {"chrom": c, "start": s, "end": e, "strand": sg if sg in ("+", "-") else "+"}


def _coord_db_path() -> Optional[str]:
    """离线预计算的坐标库；不存在时退回在线 chain（开发/离线环境）。"""
    import os

    p = os.environ.get("IDMAP_DATA_DB", "").strip() or (
        "/data/zoujin/deng/zebrafish_runtime/idmap_data.sqlite3")
    return p if os.path.isfile(p) else None


@lru_cache(maxsize=8192)
def _lift_cached(hub_build: str, chrom: str, start: int, end: int,
                 strand: str, target: str) -> tuple:
    """(ok, code, span, detail)：优先查离线预计算的坐标表。

    2026-11-13 P0 修复：以前这里直接调 ``coord_liftover.lift_region``，
    非本地版本的首次请求会把 UCSC chain 现场加载进单进程 uvicorn（常驻
    18.8 GB）。实测 GRCz12ab 126.9 s、Zv8 32.4 s，累积到 Zv6 会把进程打挂、
    由守护重启。现在坐标由 ``scripts/prime_idmap_data.py`` 在独立进程里
    全量算好落盘，线上只读表：查到即返回；查不到明确给 ``not_precomputed``
    并留空坐标，绝不在线加载链。没有落盘库时（开发机 / 离线单测）保留原来的
    在线 chain 行为；清查脚本 monkeypatch 本函数的做法也照常有效。
    """
    db = _coord_db_path()
    if db:
        import sqlite3

        try:
            con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2)
            row = con.execute(
                "SELECT ok, code, span FROM coords WHERE hub=? AND chrom=? "
                "AND start=? AND end=? AND strand=? AND target=?",
                (hub_build, chrom, start, end, strand or "+", target)).fetchone()
            con.close()
            if row:
                return (bool(row[0]), str(row[1] or ""), str(row[2] or ""), "")
            return (False, "not_precomputed", "", "")
        except Exception as e:  # noqa: BLE001 - 落盘库异常不应让整批 ID 转换失败
            return (False, "coord_cache_error", "", str(e)[:120])
    try:
        from coord_liftover import lift_region
        d = lift_region(hub_build, chrom, start, end, strand or "+", target)
    except Exception as e:  # noqa: BLE001 - 链文件异常不应让整批 ID 转换失败
        return (False, "liftover_error", "", str(e)[:120])
    if not d.get("ok"):
        return (False, str(d.get("code") or "liftover_failed"), "", "")
    return (True, str(d.get("code") or "liftover_ok"),
            _fmt_span(d.get("chromosome"), d.get("start"), d.get("end"), d.get("strand")), "")


def _coords_for(entity: dict, build: str) -> dict:
    """某个装配下这个基因的坐标：本版官方坐标 > chain 换算 > 明确留空。"""
    hc = entity.get("hub_coords")
    hub = entity.get("hub_build") or ""
    if hc and build == hub:
        return {"coords": _fmt_span(hc["chrom"], hc["start"], hc["end"], hc["strand"]),
                "basis": "native", "code": "ok", "ok": True}
    if build == BUILD_GRCZ12TU and entity.get("master"):
        m2 = _hub_coords(entity["master"], BUILD_GRCZ12TU)
        if m2:
            return {"coords": _fmt_span(m2["chrom"], m2["start"], m2["end"], m2["strand"]),
                    "basis": "native", "code": "ok", "ok": True}
    if build == BUILD_GRCZ11 and entity.get("local_coords"):
        lc = entity["local_coords"]
        return {"coords": _fmt_span(lc["chrom"], lc["start"], lc["end"], lc["strand"]),
                "basis": "official_xref", "code": "ok", "ok": True}
    if not hc or not hub:
        return {"coords": "", "basis": "unavailable", "code": "no_hub_coords", "ok": False}
    ok, code, span, detail = _lift_cached(hub, hc["chrom"], hc["start"], hc["end"],
                                          hc["strand"], build)
    return {"coords": span if ok else "", "basis": "ucsc_chain" if ok else "unavailable",
            "code": code, "ok": ok, "detail": detail}


def _xref_value(xrefs: List[dict], names: Tuple[str, ...]) -> str:
    for x in xrefs or []:
        if str(x.get("db_name") or "").lower() in names:
            return str(x.get("db_id") or "")
    return ""


def _stable_ids(e: dict) -> Dict[str, str]:
    """一个基因实体的版本无关标识符集合（ZFIN/NCBI/UniProt/Ensembl/符号）。"""
    m = e.get("master") or {}
    l = e.get("local") or {}
    xr = e.get("xrefs") or []
    return {
        "symbol": str(l.get("gene_name") or m.get("symbol") or "").strip(),
        "ensembl": _split_first(m.get("ensembl_ids")) or str(l.get("ensembl_id") or "") or
                   _xref_value(xr, ("ensembl",)),
        "zfin": _split_first(m.get("zfin_ids")) or str(l.get("zfin_id") or "") or
                _xref_value(xr, ("zfin",)),
        "ncbi": _split_first(m.get("ncbi_gene_ids")) or
                _xref_value(xr, ("ncbi_gene", "ncbi_gene_grcz12tu")),
        "uniprot": _split_first(m.get("uniprot_ids")) or str(l.get("uniprot_id") or "") or
                   _xref_value(xr, ("uniprot",)),
    }


def _resolve_entities(identifiers: List[str]) -> List[dict]:
    """把任意版本的输入 ID 解析到「基因实体」：本地库 → 映射表 → GRCz12tu 主表。

    一次批量最多 200 条，所有 SQL 都按 IN 批量查，不逐条打库。
    """
    from gene_id_mapper import resolve_batch

    rows = resolve_batch(identifiers)
    ents: List[dict] = []
    local_ids = set()
    for r in rows:
        c = r.get("canonical") or {}
        gid = c.get("gene_id")
        e = {
            "query": r.get("query") or "",
            "status": r.get("status") or ("unique" if r.get("resolved") else "not_found"),
            "resolved": bool(r.get("resolved")),
            "candidates": list(r.get("candidates") or []),
            "notes": list(r.get("notes") or []),
            "local": None, "local_id": None, "master": None,
            "xrefs": list(r.get("xrefs") or []),
            "local_coords": None, "hub_build": "", "hub_coords": None,
        }
        if e["resolved"] and gid is not None:
            try:
                e["local_id"] = int(gid)
                local_ids.add(e["local_id"])
            except (TypeError, ValueError):
                e["local_id"] = None
            e["local"] = {
                "gene_id": gid,
                "gene_name": c.get("gene_name") or "",
                "gene_symbol": c.get("gene_symbol") or "",
                "ensembl_id": c.get("ensembl_id") or "",
                "zfin_id": c.get("zfin_id") or "",
                "uniprot_id": c.get("uniprot_id") or "",
                "chromosome": c.get("chromosome") or "",
            }
        ents.append(e)

    # 本地库没命中的，再查 GRCz12tu 主表（覆盖 GRCz12tu 特有 / 新基因）
    missed = [e["query"] for e in ents if not e["resolved"] and e["query"]]
    extra_masters = _match_masters(missed) if missed else {}
    tu_to_local: Dict[str, int] = {}
    extra_tu = sorted({str(m.get("grcz12tu_gene_id") or "")
                       for m in extra_masters.values() if m.get("grcz12tu_gene_id")})
    if extra_tu:
        try:
            res = _client().execute_sql(
                "SELECT grcz12tu_gene_id, source_gene_id FROM gene_id_map_grcz12tu"
                " WHERE grcz12tu_gene_id IN (%s)" % _quote_list(extra_tu), max_rows=0)
            for x in res.get("data") or []:
                tu_to_local[str(x["grcz12tu_gene_id"])] = int(x["source_gene_id"])
                local_ids.add(int(x["source_gene_id"]))
        except Exception:  # noqa: BLE001
            pass

    xref_map: Dict[int, List[dict]] = {}
    master_by_local: Dict[int, dict] = {}
    coord11: Dict[int, dict] = {}
    if local_ids:
        in_ids = _in_clause(local_ids)
        try:
            res = _client().execute_sql(
                "SELECT gene_id, db_name, db_id FROM gene_xref WHERE gene_id IN (%s)" % in_ids,
                max_rows=0)
            for x in res.get("data") or []:
                xref_map.setdefault(int(x["gene_id"]), []).append(
                    {"db_name": x.get("db_name"), "db_id": x.get("db_id")})
        except Exception:  # noqa: BLE001
            pass
        best_maps: Dict[int, dict] = {}
        try:
            res = _client().execute_sql(
                "SELECT source_gene_id, grcz12tu_gene_id, method, score"
                " FROM gene_id_map_grcz12tu WHERE source_gene_id IN (%s)" % in_ids, max_rows=0)
            for m in res.get("data") or []:
                sid = int(m["source_gene_id"])
                prev = best_maps.get(sid)
                if prev is None or _better(m, prev):
                    best_maps[sid] = m
        except Exception:  # noqa: BLE001
            best_maps = {}
        tu_ids = [str(m.get("grcz12tu_gene_id") or "") for m in best_maps.values()]
        tu_ids = [t for t in tu_ids if t]
        if tu_ids:
            try:
                res = _client().execute_sql(
                    "SELECT * FROM gene_master_grcz12tu WHERE grcz12tu_gene_id IN (%s)"
                    % _quote_list(tu_ids), max_rows=0)
                by_tu = {str(m["grcz12tu_gene_id"]): m for m in (res.get("data") or [])}
                for sid, m in best_maps.items():
                    mm = by_tu.get(str(m.get("grcz12tu_gene_id") or ""))
                    if mm:
                        master_by_local[sid] = mm
            except Exception:  # noqa: BLE001
                pass
        try:
            res = _client().execute_sql(
                "SELECT gene_id, chromosome, start_pos, end_pos, strand"
                " FROM gene_assembly_xref WHERE assembly_name='GRCz11'"
                " AND gene_id IN (%s)" % in_ids, max_rows=0)
            for x in res.get("data") or []:
                one = _hub_coords(x, BUILD_GRCZ11)
                if one:
                    coord11[int(x["gene_id"])] = one
        except Exception:  # noqa: BLE001
            pass

    for e in ents:
        q = e["query"]
        if not e["resolved"] and q in extra_masters:
            master = extra_masters[q]
            e["master"] = master
            tu_id = str(master.get("grcz12tu_gene_id") or "")
            lid = tu_to_local.get(tu_id)
            if lid is not None:
                e["local_id"] = lid
                e["local"] = {
                    "gene_id": lid, "gene_name": str(master.get("symbol") or ""),
                    "gene_symbol": "", "ensembl_id": "", "zfin_id": "",
                    "uniprot_id": "", "chromosome": str(master.get("chrom") or ""),
                }
            e["resolved"] = True
            e["status"] = "unique"
        lid = e["local_id"]
        if lid is not None:
            if xref_map.get(lid):
                seen = {(str(x.get("db_name")), str(x.get("db_id"))) for x in e["xrefs"]}
                for x in xref_map[lid]:
                    key = (str(x.get("db_name")), str(x.get("db_id")))
                    if key not in seen:
                        seen.add(key)
                        e["xrefs"].append(x)
            if not e["master"] and master_by_local.get(lid):
                e["master"] = master_by_local[lid]
            e["local_coords"] = coord11.get(lid)
        mcoords = _hub_coords(e["master"], BUILD_GRCZ12TU) if e["master"] else None
        if mcoords:
            e["hub_build"], e["hub_coords"] = BUILD_GRCZ12TU, mcoords
        elif e["local_coords"]:
            e["hub_build"], e["hub_coords"] = BUILD_GRCZ11, e["local_coords"]
        e["stable"] = _stable_ids(e)
    return ents


def _stable_xrefs(s: Dict[str, str]) -> List[dict]:
    return [
        {"db_name": "ensembl", "db_id": s["ensembl"]},
        {"db_name": "zfin", "db_id": s["zfin"]},
        {"db_name": "ncbi_gene", "db_id": s["ncbi"]},
        {"db_name": "uniprot", "db_id": s["uniprot"]},
    ]


def _row_from_stable(query: str, e: dict, builds: dict, target: str,
                     coords: dict) -> dict:
    """无独立注释表的目标版本：稳定标识符 + chain 坐标，并写明口径。"""
    s = e.get("stable") or {}
    gene_id = e.get("local_id")
    if gene_id is None and e.get("master"):
        gene_id = e["master"].get("grcz12tu_gene_id")
    notes = []
    if not builds.get("target_has_gene_table"):
        notes.append(_STABLE_NOTE_ZH.format(hub=e.get("hub_build") or "GRCz11"))
    if not coords.get("ok"):
        notes.append("未换算出 %s 坐标（%s），未给近似坐标" %
                     (target, coords.get("code") or "no_hub_coords"))
    return {
        "query": query,
        "resolved": True,
        "status": "unique",
        "canonical": {
            "gene_id": gene_id,
            "gene_name": s.get("symbol") or "",
            "gene_symbol": "",
            "ensembl_id": s.get("ensembl") or "",
            "zfin_id": s.get("zfin") or "",
            "uniprot_id": s.get("uniprot") or "",
            "chromosome": "",
        },
        "standard": {
            "gene_symbol": s.get("symbol") or "",
            "assembly": target,
            "role": "version_stable_identifier",
            "source": "跨版本稳定标识 + UCSC chain 坐标",
        },
        "candidates": [], "xrefs": _stable_xrefs(s), "aliases": [],
        "match_kind": "cross_build_stable_id",
        "notes": notes,
        "target_coords": coords.get("coords") or "",
        "coord_status": coords.get("code"),
        "coord_basis": coords.get("basis"),
        "identity_basis": "stable_ids_with_chain_coords",
        "builds": builds,
    }


def _row_from_local(query: str, e: dict, builds: dict, coords: dict) -> dict:
    """目标 GRCz11：本地库标识符（与旧 _to_grcz11 同形）+ 官方/chain 坐标。"""
    l = e.get("local") or {}
    s = e.get("stable") or {}
    xrefs = []
    seen = set()
    for x in e.get("xrefs") or []:
        name = str(x.get("db_name") or "")
        if name == "ncbi_gene_grcz12tu":
            continue
        key = (name, str(x.get("db_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        xrefs.append(x)
    if s.get("ncbi") and not any(str(x.get("db_name")) == "ncbi_gene" for x in xrefs):
        xrefs.append({"db_name": "ncbi_gene", "db_id": s["ncbi"]})
    return {
        "query": query, "resolved": True, "status": "unique",
        "canonical": {
            "gene_id": e.get("local_id"),
            "gene_name": l.get("gene_name") or "",
            "gene_symbol": l.get("gene_symbol") or "",
            "ensembl_id": l.get("ensembl_id") or s.get("ensembl") or "",
            "zfin_id": l.get("zfin_id") or s.get("zfin") or "",
            "uniprot_id": l.get("uniprot_id") or s.get("uniprot") or "",
            "chromosome": l.get("chromosome") or "",
        },
        "standard": {"gene_symbol": l.get("gene_name") or "", "assembly": BUILD_GRCZ11,
                     "role": "canonical", "source": "本地库 GRCz11"},
        "candidates": [], "xrefs": xrefs, "aliases": [],
        "match_kind": "cross_build_stable_id",
        "notes": [], "target_coords": coords.get("coords") or "",
        "coord_status": coords.get("code"), "coord_basis": coords.get("basis"),
        "identity_basis": "stable_ids_with_chain_coords" if not coords.get("ok") else "local_table",
        "builds": builds,
    }


def _convert_generic(identifiers: List[str], src: str, tgt: str, builds: dict) -> Dict[str, Any]:
    """任意 18 个装配之间的批量 ID 转换（含非中枢版本的同版本互查）。"""
    ents = _resolve_entities(identifiers)
    out: List[dict] = []
    ok_coords = 0
    basis_count: Dict[str, int] = {}
    for e in ents:
        q = e["query"]
        if not e["resolved"]:
            row = _base_row(q, builds)
            row["status"] = e["status"] or "not_found"
            row["candidates"] = e["candidates"]
            row["notes"] = e["notes"]
            row["source_coords"] = ""
            row["coord_status"] = "not_resolved"
            out.append(row)
            continue
        src_c = _coords_for(e, src)
        tgt_c = _coords_for(e, tgt)
        if tgt == BUILD_GRCZ12TU and e.get("master"):
            row = _row_from_master(q, e["master"], builds, method="cross_build_route", score="")
        elif tgt == BUILD_GRCZ11 and e.get("local"):
            row = _row_from_local(q, e, builds, tgt_c)
        else:
            row = _row_from_stable(q, e, builds, tgt, tgt_c)
        row["source_coords"] = src_c.get("coords") or ""
        row["source_coord_status"] = src_c.get("code")
        row["source_coord_basis"] = src_c.get("basis")
        row["target_coords"] = tgt_c.get("coords") or ""
        row["coord_status"] = tgt_c.get("code")
        row["coord_basis"] = tgt_c.get("basis")
        if tgt_c.get("ok"):
            ok_coords += 1
        b = str(tgt_c.get("basis") or "unavailable")
        basis_count[b] = basis_count.get(b, 0) + 1
        out.append(row)
    builds["coord_summary"] = {
        "total": len(ents), "ok": ok_coords,
        "missing": len(ents) - ok_coords, "basis": basis_count,
    }
    return _pack(out, identifiers, builds)


# ---------------------------------------------------------------- 对外入口

def _pack(rows: List[dict], identifiers: List[str], builds: dict) -> Dict[str, Any]:
    tsv_lines = ["query\tstatus\tgene_name\tensembl_id\tzfin_id\tncbi_gene\tuniprot"
                 "\tchromosome\tsource_coords\ttarget_coords\tcoord_status"]
    for r in rows:
        c = r.get("canonical") or {}
        ncbi = next(
            (x.get("db_id") for x in (r.get("xrefs") or [])
             if str(x.get("db_name", "")).startswith("ncbi_gene")),
            "",
        )
        tsv_lines.append(
            "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" % (
                r.get("query", ""), r.get("status", ""),
                c.get("gene_name", ""), c.get("ensembl_id", ""), c.get("zfin_id", ""),
                ncbi, c.get("uniprot_id", ""), c.get("chromosome", ""),
                r.get("source_coords", ""), r.get("target_coords", ""),
                r.get("coord_status", ""),
            )
        )
    return {
        "results": rows,
        "count": len(rows),
        "builds": builds,
        "tsv": "\n".join(tsv_lines),
    }


def convert_batch(identifiers: List[str], source_build: Optional[str] = None,
                  target_build: Optional[str] = None) -> Dict[str, Any]:
    """18 个装配任选来源 / 目标的批量 ID 互转。

    GRCz11 ⇄ GRCz12tu 走本地对照表（旧行为不变）；其余任意组合走通用管线
    （稳定标识符 + UCSC chain 换算出的该版本坐标）。不传版本时等价于旧的
    GRCz11 同库互查，老调用方完全不受影响。
    """
    ids = [str(i).strip() for i in (identifiers or []) if str(i).strip()]
    hint = "%d 个装配（%s …）" % (len(SUPPORTED_BUILDS), " / ".join(SUPPORTED_BUILDS[:4]))
    if source_build and normalize_build(source_build) is None:
        raise ValueError("不认识的原版本：%s（可选：%s）" % (source_build, hint))
    if target_build and normalize_build(target_build) is None:
        raise ValueError("不认识的目标版本：%s（可选：%s）" % (target_build, hint))
    src = normalize_build(source_build) or BUILD_GRCZ11
    tgt = normalize_build(target_build) or src

    # 2026-11-13：任一端是 Ensembl release（ens:54 … ens:116）→ 走 release
    # 基因集管线，输出该 release 的 ENSDARG ID；与 NCBI 装配混选也支持。
    try:
        from ensembl_version_map import is_release
        if is_release(src) or is_release(tgt):
            from ensembl_version_map import convert as _ens_convert, make_builds
            return _ens_convert(ids, src, tgt, make_builds(src, tgt))
    except ImportError:  # 模块缺失时退回原管线，不影响既有功能
        pass

    local_pair = src in LOCAL_TABLE_BUILDS and tgt in LOCAL_TABLE_BUILDS
    builds = {
        "source": src,
        "target": tgt,
        "converted": src != tgt,
        "supported": list(SUPPORTED_BUILDS),
        "identity_basis": "local_table" if local_pair else "stable_ids_with_chain_coords",
        "source_has_gene_table": src in LOCAL_TABLE_BUILDS,
        "target_has_gene_table": tgt in LOCAL_TABLE_BUILDS,
    }

    # 同版本且是本地锚定版：完全等价于原来的同库互查（行为不变）。
    if src == tgt and src in LOCAL_TABLE_BUILDS:
        from gene_toolkit import batch_id_convert
        res = batch_id_convert(ids)
        res["builds"] = builds
        return res

    # 其余一律走统一管线。GRCz11 ⇄ GRCz12tu 的输出形态仍由本地对照表决定
    # （_row_from_master / _row_from_local），但解析顺序统一为「本地库 → 主表」，
    # 修掉旧 _to_grcz11 只认 符号/NCBI/Ensembl/ZFIN、不认 UniProt 的缺陷
    # （清查中 P79734 在 GRCz12tu→GRCz11 会漏命中）。
    # _to_grcz12tu / _to_grcz11 保留作历史实现与回滚参考。
    return _convert_generic(ids, src, tgt, builds)


def build_capabilities() -> Dict[str, Any]:
    """给前端 / 审计用的版本能力清单（含 chain 路由覆盖）。"""
    routable = []
    try:
        from coord_liftover import list_supported_pairs
        routable = list(list_supported_pairs().get("routable_pairs") or [])
    except Exception:  # noqa: BLE001
        pass
    try:
        from ensembl_version_map import build_catalog
        ens = build_catalog()
    except Exception:  # noqa: BLE001
        ens = []
    return {
        "builds": list(SUPPORTED_BUILDS),
        "ensembl_releases": ens,
        "gene_table_builds": list(LOCAL_TABLE_BUILDS),
        "identity_basis": {
            "gene_table_builds": "local_table",
            "other_builds": "stable_ids_with_chain_coords",
            "ensembl_releases": "ensembl_release_gene_set",
        },
        "routable_pairs": routable,
        "pair_count": len(SUPPORTED_BUILDS) * len(SUPPORTED_BUILDS),
        "release_pair_count": len(ens) * len(ens),
    }
