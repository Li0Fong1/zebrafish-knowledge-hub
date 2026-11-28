# -*- coding: utf-8 -*-
"""Ensembl release 版本间的批量 ID 转换（2026-11-13）。

## 数据现实（线上库实测，不编造）

``ensembl_release_gene`` 有 4 个真实历史基因集，共 123,660 行：

==========  ==========  =======
release     装配        行数
==========  ==========  =======
54          Zv8         24,233
77          Zv9         33,737
79          Zv9         33,737
80          GRCz10      31,953
==========  ==========  =======

字段含 ``ensembl_gene_id`` / ``gene_symbol`` / ``zfin_id`` /
``current_ensembl_gene_id`` / ``match_method`` —— 后者把历史 ID 指到现用 ID。
另有 ``ensembl_id_version``（r116，14,038 行，带 .N 版本后缀）与
``gene_basic.ensembl_id``，构成当前集 r116（GRCz11 锚点）。

这些表由 ``scripts/prime_idmap_data.py`` 离线导出到同一个 SQLite 库，
线上只读，不占 uvicorn 内存。

## 口径（必须诚实）

1. ENSDARG gene ID 是**跨 release 稳定标识**，不是「某个组装专属 ID」；
   本模块输出的是「该 release 基因集里这个基因所用的 Ensembl ID」。
2. 本地**没有** Ensembl 官方 ``stable_id_event`` / ``gene_archive`` 级 ID
   History（退役、合并、拆分）。只提供本地 4 个历史基因集 + 当前集的对照；
   查不到就明说「该 release 未收录此基因」，不伪造 ID、不编 release 号。
3. 不宣称精确的 release 起止区间（官方未给权威表）；下拉只列本地实有的 5 个。
4. 坐标由 UCSC chain 换算（查离线落盘表），失败留空并给 code。
5. GRCz12tu 是 NCBI RefSeq 注释，不是 Ensembl 官方基因集，故不在本分组。

## 版本值

统一用 ``ens:<release>`` 作为来源/目标版本值（``ens:54`` … ``ens:116``），
与 18 个 NCBI 装配名互不冲突；``release_assembly()`` 给出对应装配，用于复用
UCSC chain 坐标换算。
"""
from __future__ import annotations

import re
import sqlite3
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

PREFIX = "ens:"
CURRENT_RELEASE = 116

# 落盘库不可用时（开发机 / 单元测试）的静态清单，与线上实有数据一致
_FALLBACK: Dict[int, str] = {54: "Zv8", 77: "Zv9", 79: "Zv9",
                             80: "GRCz10", 116: "GRCz11"}

_NOTE_ZH = (
    "数据来源：Ensembl 官方 FTP 的 GTF gene 注释（r54/r77/r79/r80/r116）。"
    "ENSDARG 基因 ID 为跨 release 稳定标识；输出是目标 release 官方基因集中该基因的 "
    "ID 与坐标。坐标是该 release 参考基因组上的官方注释坐标（r54 无 gene 行，"
    "由官方 exon 聚合出基因边界），不是 chain 换算的近似值；查不到即标未收录，"
    "同名符号多候选时列全部候选，不猜。"
)
_NOTE_EN = (
    "Source: official Ensembl GTF gene annotation (r54/r77/r79/r80/r116). ENSDARG "
    "gene IDs are stable across releases; output is the ID and coordinates of this "
    "gene in the target release's official gene set. Coordinates are official "
    "annotation coordinates on that release's reference genome (r54 has no gene "
    "rows, so gene bounds are aggregated from official exons), not chain-lifted "
    "approximations. Missing genes are reported as not annotated; ambiguous symbols "
    "list all candidates without guessing."
)


def _db_path() -> Optional[str]:
    try:
        from id_version_map import _coord_db_path
        return _coord_db_path()
    except Exception:  # noqa: BLE001
        return None


def _connect(path: str) -> Optional[sqlite3.Connection]:
    try:
        return sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=2)
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=1)
def releases() -> Dict[int, str]:
    """{release: 装配}；优先读落盘库，读不到用静态清单。"""
    out = dict(_FALLBACK)
    p = _db_path()
    if not p:
        return out
    con = _connect(p)
    if con is None:
        return out
    try:
        rows = con.execute(
            "SELECT release, MIN(assembly) FROM ens_release "
            "GROUP BY release ORDER BY release").fetchall()
        for r, a in rows:
            out[int(r)] = str(a or "")
    except Exception:  # noqa: BLE001
        pass
    finally:
        con.close()
    out[CURRENT_RELEASE] = "GRCz11"
    return dict(sorted(out.items()))


def normalize(value: Optional[str]) -> Optional[str]:
    """把 ens:54 / Ensembl r54 / e54 / ens54 收敛到 ens:54；非本组返回 None。"""
    v = (value or "").strip()
    if not v:
        return None
    if v.startswith(PREFIX):
        tail = v[len(PREFIX):]
        if tail.isdigit() and int(tail) in releases():
            return "%s%d" % (PREFIX, int(tail))
        return None
    m = re.fullmatch(r"(?:ensembl\s*)?[re]\s*[:_-]?\s*(\d{2,3})", v, re.I)
    if m and int(m.group(1)) in releases():
        return "%s%d" % (PREFIX, int(m.group(1)))
    return None


def is_release(value: Optional[str]) -> bool:
    return normalize(value) is not None


def release_num(value: str) -> int:
    return int(normalize(value)[len(PREFIX):])


def label(value: str, lang: str = "zh") -> str:
    n = release_num(value)
    a = releases().get(n, "")
    if lang.lower().startswith("en"):
        return "Ensembl r%d" % n
    return "Ensembl r%d" % n


def release_assembly(value: str) -> str:
    """Ensembl release → 对应参考组装（用于 UCSC chain 坐标换算）。"""
    return releases().get(release_num(value), "GRCz11")


def build_catalog() -> List[dict]:
    """给 /api/tools/id-convert/builds 与前端用。"""
    return [{"value": "%s%d" % (PREFIX, r), "label": "Ensembl r%d" % r,
             "label_en": "Ensembl r%d" % r, "release": r, "assembly": a,
             "row_count": None} for r, a in releases().items()]


def _lookup_release(con, rel: int, zfin: str, symbol: str, ens: str):
    """目标 release 里找这个基因，返回 ``(hits, ambiguous)``。

    2026-11-13 独立核验发现的历史隐患（必须按此口径处理）：
      · ``ens_release`` 有 33,966 行 ``unmapped``，zfin_id 与
        current_ensembl_gene_id **全部为空**；另有 2,867 组「同一 release 内
        gene_symbol 重复」。
      · 旧实现在 ZFIN 缺失时退化到 gene_symbol 并取 ``LIMIT 5`` 的第一行，
        status 仍标 ``unique`` —— 遇到重复符号会**返回一个看似确定的错 ID**。

    现在严格分层：ZFIN / Ensembl ID 是唯一锚点；只有符号可匹配且该符号对应
    多个不同 ENSDARG 时，返回 ``ambiguous=True``，由调用方标「多候选」且
    **不填 canonical.ensembl_id**，绝不猜。
    """
    if not con or not rel:
        return [], False
    cur_table = "ens_current" if rel == CURRENT_RELEASE else "ens_release"
    cols = ("ensembl_gene_id, gene_symbol, zfin_id" if rel == CURRENT_RELEASE else
            "ensembl_gene_id, gene_symbol, zfin_id, current_symbol, "
            "current_ensembl_gene_id, match_method")
    where_rel = "" if rel == CURRENT_RELEASE else " AND release=?"
    rel_args = () if rel == CURRENT_RELEASE else (rel,)

    def query(col, val, limit=12):
        rows = con.execute(
            "SELECT %s FROM %s WHERE %s=?%s LIMIT %d"
            % (cols, cur_table, col, where_rel, limit), (val,) + rel_args).fetchall()
        out = []
        for r in rows:
            if rel == CURRENT_RELEASE:
                out.append({"ensembl_gene_id": r[0], "gene_symbol": r[1], "zfin_id": r[2],
                            "current_ensembl_gene_id": "",
                            "match_method": "current_set:" + col})
            else:
                out.append({"ensembl_gene_id": r[0], "gene_symbol": r[1], "zfin_id": r[2],
                            "current_symbol": r[3], "current_ensembl_gene_id": r[4],
                            "match_method": (r[5] or "") + ":" + col})
        return out

    def distinct_ids(hits):
        return {h.get("ensembl_gene_id") for h in hits if h.get("ensembl_gene_id")}

    # 1) ZFIN：跨 release 最稳的唯一锚点
    if zfin:
        hits = query("zfin_id", zfin)
        if hits:
            return hits, len(distinct_ids(hits)) > 1
    # 2) Ensembl gene ID 精确（含历史 ID）
    if ens:
        hits = query("ensembl_gene_id", ens)
        if hits:
            return hits, len(distinct_ids(hits)) > 1
    # 3) 只剩符号：重复符号必须明说「多候选」，不能冒充唯一
    for v in (symbol, symbol.lower() if symbol else ""):
        if not v:
            continue
        hits = query("gene_symbol", v)
        if hits:
            return hits, len(distinct_ids(hits)) > 1
    return [], False


# ---------------------------------------------------------------- 官方 GTF 数据层
# 2026-11-13：Ensembl release 的真实基因集改由官方 FTP 的 GTF gene 注释提供
# （scripts/import_ensembl_official_gtf.py 落盘到 ens_gene_official 表）。
# 坐标是该 release 参考基因组上的官方注释坐标；stable ID 跨 release 稳定。

_OFFICIAL_COLS = "ensembl_gene_id, symbol, chrom, start, end, strand, biotype, assembly"
_OFFICIAL_KEYS = ("ensembl_gene_id", "symbol", "chrom", "start", "end",
                  "strand", "biotype", "assembly")


def _official_ready(con) -> bool:
    """落盘库里是否有官方 GTF 基因集（有则优先用）。"""
    if not con:
        return False
    try:
        n = con.execute("SELECT COUNT(*) FROM ens_gene_official").fetchone()[0]
        return n > 1000
    except Exception:  # noqa: BLE001
        return False


def _official_rows(con, rel: int, gid: str, symbol: str):
    """在官方 GTF 基因集里定位一个基因：stable ID 优先，符号（不分大小写）次之。

    返回 ``(rows, ambiguous, via)``：
      · rows 每项含 ensembl_gene_id / symbol / chrom / start / end / strand /
        biotype / assembly；
      · ambiguous 仅当符号命中多个不同 ENSDARG 时为 True（由调用方标「多候选」）；
      · via ∈ {"stable_id", "symbol", ""}。
    """
    if not con or not rel:
        return [], False, ""
    if gid:
        try:
            cur = con.execute(
                "SELECT %s FROM ens_gene_official WHERE release=? AND ensembl_gene_id=?"
                % _OFFICIAL_COLS, (rel, gid))
            hits = [dict(zip(_OFFICIAL_KEYS, r)) for r in cur.fetchall()]
        except Exception:  # noqa: BLE001
            hits = []
        if hits:
            return hits, False, "stable_id"
    if symbol:
        try:
            cur = con.execute(
                "SELECT %s FROM ens_gene_official WHERE release=? AND symbol=? COLLATE NOCASE"
                % _OFFICIAL_COLS, (rel, symbol))
            hits = [dict(zip(_OFFICIAL_KEYS, r)) for r in cur.fetchall()]
        except Exception:  # noqa: BLE001
            hits = []
        if hits:
            ids = {h["ensembl_gene_id"] for h in hits}
            return hits, len(ids) > 1, "symbol"
    return [], False, ""


def _official_convert(ids, src_rel, tgt_rel, builds, con, src_build, tgt_build):
    """两端都是 Ensembl release 且官方 GTF 数据可用：输出目标 release 的真实 ID 与坐标。"""
    import id_version_map as ivm

    proxies = [_proxy_for(con, q, src_rel) for q in ids]
    ents = ivm._resolve_entities(proxies)
    for e, q in zip(ents, ids):
        e["query"] = q
    miss = [i for i, e in enumerate(ents) if not e.get("resolved")]
    if miss:
        second = ivm._resolve_entities([ids[i] for i in miss])
        for i, e2 in zip(miss, second):
            if e2.get("resolved"):
                e2["query"] = ids[i]
                ents[i] = e2

    src_asm = release_assembly(src_build)
    tgt_asm = release_assembly(tgt_build)
    rows: List[dict] = []
    ok_coords = 0

    for e in ents:
        q = e["query"]
        if not e.get("resolved"):
            r = ivm._base_row(q, builds)
            r["status"] = e.get("status") or "not_found"
            r["candidates"] = e.get("candidates") or []
            r["notes"] = list(e.get("notes") or [])
            r["source_coords"] = ""
            r["coord_status"] = "not_resolved"
            rows.append(r)
            continue

        s = e.get("stable") or {}
        gid = (s.get("ensembl") or "").strip()
        sym = (s.get("symbol") or "").strip()
        zfin, uni = s.get("zfin") or "", s.get("uniprot") or ""

        src_hits, src_amb, _sv = _official_rows(con, src_rel, gid, sym)
        tgt_hits, tgt_amb, tgt_via = _official_rows(con, tgt_rel, gid, sym)

        notes = [_NOTE_ZH, _NOTE_EN]
        target = tgt_hits[0] if (tgt_hits and not tgt_amb) else None
        target_gid = target["ensembl_gene_id"] if target else ""
        target_sym = (target["symbol"] or sym) if target else sym
        target_coords = (ivm._fmt_span(target["chrom"], target["start"],
                                       target["end"], target["strand"])
                         if target else "")
        if target_coords:
            ok_coords += 1

        if tgt_amb:
            notes.append("目标 release 中该标识对应多个不同 Ensembl ID；本地缺少唯一锚点，"
                         "已列出全部候选，不猜、不任选其一。")
        elif not target_gid:
            notes.append("该基因未收录于 Ensembl r%d 官方基因集，不伪造 ID。" % tgt_rel)
        elif tgt_via == "stable_id":
            notes.append("按 ENSDARG stable ID 在 r%d 官方基因集命中；坐标为 r%d 官方 "
                         "GTF 注释坐标。" % (tgt_rel, tgt_rel))
        elif tgt_via == "symbol":
            notes.append("按官方 gene symbol 在 r%d 命中（stable ID 在该 release 缺失或改名）；"
                         "坐标为 r%d 官方 GTF 注释坐标。" % (tgt_rel, tgt_rel))

        src_coords = ""
        if src_hits and not src_amb:
            sh = src_hits[0]
            src_coords = ivm._fmt_span(sh["chrom"], sh["start"], sh["end"], sh["strand"])

        xrefs = list(ivm._stable_xrefs(s))
        xrefs.insert(0, {"db_name": "ensembl_release_target",
                         "db_id": ("r%d:%s" % (tgt_rel, target_gid)) if target_gid else ""})
        if src_hits and not src_amb:
            xrefs.insert(1, {"db_name": "ensembl_release_source",
                             "db_id": "r%d:%s" % (src_rel, src_hits[0]["ensembl_gene_id"])})

        status = "unique"
        if tgt_amb:
            status = "ambiguous"
        elif not target_gid:
            status = "not_in_release" if (zfin or sym) else "not_found"

        match_kind = ("official_ambiguous_symbol" if tgt_amb else
                      ("official_stable_id" if tgt_via == "stable_id" else
                       ("official_symbol" if tgt_via == "symbol" else
                        "ensembl_official_not_annotated")))

        rows.append({
            "query": q, "resolved": True, "status": status,
            "canonical": {
                "gene_id": e.get("local_id") or (e.get("master") or {}).get("grcz12tu_gene_id"),
                "gene_name": target_sym,
                "gene_symbol": "",
                "ensembl_id": target_gid or "",
                "zfin_id": zfin, "uniprot_id": uni,
                "chromosome": (target["chrom"] if target else "") or "",
            },
            "standard": {
                "gene_symbol": target_sym, "assembly": tgt_asm,
                "role": "ensembl_release_gene_set",
                "source": "Ensembl r%d 官方 GTF" % tgt_rel,
            },
            "candidates": [{"gene_name": h.get("symbol"),
                            "ensembl_id": h.get("ensembl_gene_id")}
                           for h in (tgt_hits if tgt_amb else tgt_hits[1:4])],
            "xrefs": xrefs, "aliases": [],
            "match_kind": match_kind,
            "notes": notes,
            "source_coords": src_coords,
            "source_coord_status": "ok" if src_coords else "not_in_release",
            "source_coord_basis": "ensembl_official_gtf",
            "target_coords": target_coords,
            "coord_status": ("ok" if target_coords else
                             ("ambiguous" if tgt_amb else "not_in_release")),
            "coord_basis": "ensembl_official_gtf",
            "identity_basis": "ensembl_official_gtf",
            "ensembl_source_release": src_rel,
            "ensembl_target_release": tgt_rel,
            "builds": builds,
        })

    builds["coord_summary"] = {"total": len(ents), "ok": ok_coords,
                               "missing": len(ents) - ok_coords}
    return ivm._pack(rows, ids, builds)


def _proxy_for(con, q: str, src_release: Optional[int]) -> str:
    """历史 release 的 ENSDARG 可能不在当前库；换成 ZFIN / 符号供通用解析。"""
    if not con or not re.match(r"^ENSDARG\d+", q, re.I):
        return q
    # 优先取有 ZFIN 的行：unmapped 行 zfin 为空，退化到 gene_symbol 时
    # 可能撞上重复符号，把输入解析到另一个基因。
    tries = []
    if src_release and src_release != CURRENT_RELEASE:
        tries.append(("SELECT zfin_id, gene_symbol, current_ensembl_gene_id "
                      "FROM ens_release WHERE ensembl_gene_id=? AND release=? "
                      "ORDER BY (zfin_id IS NULL OR zfin_id='') ASC, "
                      "(current_ensembl_gene_id IS NULL OR current_ensembl_gene_id='') ASC "
                      "LIMIT 1", (q.upper(), src_release)))
    tries.append(("SELECT zfin_id, gene_symbol FROM ens_current "
                  "WHERE ensembl_gene_id=? ORDER BY (zfin_id IS NULL OR zfin_id='') ASC "
                  "LIMIT 1", (q.upper(),)))
    for sql, args in tries:
        try:
            row = con.execute(sql, args).fetchone()
            if row:
                return str(next((x for x in row if x), q))
        except Exception:  # noqa: BLE001
            continue
    return q


def convert(identifiers: List[str], source_build: str,
            target_build: str, builds: dict) -> Dict[str, Any]:
    """Ensembl release（或 Ensembl ↔ NCBI 装配混合）批量的转换入口。"""
    import id_version_map as ivm

    ids = [str(i).strip() for i in (identifiers or []) if str(i).strip()]
    p = _db_path()
    con = _connect(p) if p else None
    src_rel = release_num(source_build) if is_release(source_build) else None
    tgt_rel = release_num(target_build) if is_release(target_build) else None

    # 2026-11-13：两端都是 Ensembl release 且官方 GTF 基因集可用 → 走官方真实数据
    # （stable ID 跨 release 关联 + 该 release 官方注释坐标），不再用 chain 近似。
    if src_rel and tgt_rel and _official_ready(con):
        return _official_convert(ids, src_rel, tgt_rel, builds, con,
                                 source_build, target_build)

    # 1) 通用实体解析（历史 release 的 ENSDARG 先换成 ZFIN/符号）
    proxies = [_proxy_for(con, q, src_rel) for q in ids]
    ents = ivm._resolve_entities(proxies)
    for e, q in zip(ents, ids):
        e["query"] = q
    miss = [i for i, e in enumerate(ents) if not e.get("resolved")]
    if miss:
        second = ivm._resolve_entities([ids[i] for i in miss])
        for i, e2 in zip(miss, second):
            if e2.get("resolved"):
                e2["query"] = ids[i]
                ents[i] = e2

    # 2) 目标标识 + 目标坐标
    rows: List[dict] = []
    ok_coords = 0
    for e in ents:
        q = e["query"]
        if not e.get("resolved"):
            r = ivm._base_row(q, builds)
            r["status"] = e.get("status") or "not_found"
            r["candidates"] = e.get("candidates") or []
            r["notes"] = list(e.get("notes") or [])
            r["source_coords"] = ""
            r["coord_status"] = "not_resolved"
            rows.append(r)
            continue
        s = e.get("stable") or {}
        zfin, sym, ens = s.get("zfin", ""), s.get("symbol", ""), s.get("ensembl", "")
        tgt_hits, tgt_amb = (_lookup_release(con, tgt_rel, zfin, sym, ens)
                             if (con and tgt_rel) else ([], False))
        src_hits, _ = (_lookup_release(con, src_rel, zfin, sym, ens)
                       if (con and src_rel) else ([], False))

        src_asm = release_assembly(source_build) if src_rel else source_build
        tgt_asm = release_assembly(target_build) if tgt_rel else target_build
        sc = ivm._coords_for(e, src_asm)
        tc = ivm._coords_for(e, tgt_asm)
        if tc.get("ok"):
            ok_coords += 1

        notes = [_NOTE_ZH, _NOTE_EN]
        # 多候选时绝不填 canonical.ensembl_id —— 符号重复会给出看似确定的错 ID
        target_ens = tgt_hits[0]["ensembl_gene_id"] if (tgt_hits and not tgt_amb) else ""
        if tgt_amb:
            notes.append("目标 release 中该标识对应多个不同 Ensembl ID；本地缺少 "
                         "ZFIN/现用 ID 锚点，无法判定唯一答案，已列出全部候选，"
                         "不猜、不任选其一。")
        elif tgt_rel and not target_ens:
            notes.append("该基因未收录于 Ensembl r%d 基因集（本地无此 release 的注释行），"
                         "不伪造 ID" % tgt_rel)
        if tgt_hits and tgt_hits[0].get("current_ensembl_gene_id"):
            cur = tgt_hits[0]["current_ensembl_gene_id"]
            if cur and cur != target_ens:
                notes.append("该历史 ID 对应现用 ID：%s" % cur)
        if not tc.get("ok"):
            notes.append("未换算出 %s 坐标（%s），未给近似坐标" %
                         (tgt_asm, tc.get("code") or "no_hub_coords"))

        # 目标 release 没给出唯一 ID 时，连「当前库稳定 ENSDARG」也不要塞进
        # xrefs：前端 Ensembl 行标的是目标 release，回退显示会被误读成答案。
        base_xrefs = ivm._stable_xrefs(s)
        if tgt_rel and not target_ens:
            base_xrefs = [x for x in base_xrefs
                          if str(x.get("db_name") or "") != "ensembl"]
        xrefs = list(base_xrefs)
        xrefs.insert(0, {"db_name": "ensembl_release_target",
                         "db_id": "r%d:%s" % (tgt_rel, target_ens)
                         if (tgt_rel and target_ens) else ""})
        if src_hits and not tgt_amb:
            xrefs.insert(1, {"db_name": "ensembl_release_source",
                             "db_id": "r%d:%s" % (src_rel, src_hits[0]["ensembl_gene_id"])})

        status = "unique"
        if tgt_amb:
            status = "ambiguous"
        elif tgt_rel and not target_ens:
            status = "not_in_release" if zfin or sym else "not_found"
        rows.append({
            "query": q, "resolved": True, "status": status,
            "canonical": {
                "gene_id": e.get("local_id") or (e.get("master") or {}).get("grcz12tu_gene_id"),
                "gene_name": (tgt_hits[0].get("gene_symbol")
                              if (tgt_hits and not tgt_amb) else "") or sym,
                "gene_symbol": "",
                # 目标 release 的 Ensembl ID 为主输出；多候选时不填；
                # 没有 release 维度时才用跨库稳定 ID
                "ensembl_id": target_ens or ("" if tgt_rel else ens),
                "zfin_id": zfin, "uniprot_id": s.get("uniprot", ""),
                "chromosome": "",
            },
            "standard": {
                "gene_symbol": (tgt_hits[0].get("gene_symbol")
                                if (tgt_hits and not tgt_amb) else "") or sym,
                "assembly": tgt_asm, "role": "ensembl_release_gene_set",
                "source": "Ensembl r%d 基因集" % tgt_rel if tgt_rel else "跨库稳定标识",
            },
            "candidates": [{"gene_name": h.get("gene_symbol"),
                            "ensembl_id": h.get("ensembl_gene_id")}
                           for h in (tgt_hits if tgt_amb else tgt_hits[1:4])],
            "xrefs": xrefs, "aliases": [],
            "match_kind": ("ambiguous_symbol_match" if tgt_amb else
                           (tgt_hits[0].get("match_method") if tgt_hits
                            else "ensembl_release_not_annotated")),
            "notes": notes,
            "source_coords": sc.get("coords") or "",
            "source_coord_status": sc.get("code"),
            "source_coord_basis": sc.get("basis"),
            "target_coords": tc.get("coords") or "",
            "coord_status": tc.get("code"),
            "coord_basis": tc.get("basis"),
            "identity_basis": "ensembl_release_gene_set",
            "ensembl_source_release": src_rel,
            "ensembl_target_release": tgt_rel,
            "builds": builds,
        })
    builds["coord_summary"] = {"total": len(ents), "ok": ok_coords,
                               "missing": len(ents) - ok_coords}
    if con:
        con.close()
    return ivm._pack(rows, ids, builds)


def make_builds(src: str, tgt: str) -> dict:
    """给 convert_batch 用：补齐 release 相关的 builds 标签。"""
    return {
        "source": src, "target": tgt, "converted": src != tgt,
        "source_kind": "ensembl_release" if is_release(src) else "ncbi_assembly",
        "target_kind": "ensembl_release" if is_release(tgt) else "ncbi_assembly",
        "source_label": label(src) if is_release(src) else src,
        "target_label": label(tgt) if is_release(tgt) else tgt,
        "source_assembly": release_assembly(src) if is_release(src) else src,
        "target_assembly": release_assembly(tgt) if is_release(tgt) else tgt,
        "source_ensembl_release": release_num(src) if is_release(src) else None,
        "target_ensembl_release": release_num(tgt) if is_release(tgt) else None,
        "identity_basis": "ensembl_release_gene_set",
        "ensembl_releases": build_catalog(),
    }
