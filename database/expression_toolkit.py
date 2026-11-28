# -*- coding: utf-8 -*-
"""表达数据层（本地）：组织表达谱 + 胚胎分期表达 + 组织 Top 基因 + 多基因对比。

数据来源：``gene_expression`` 表（319,344 行 / 24,245 个基因），两组数据**单位不同，不能混比**：
  · ``data_source='bgee'``        —— Bgee expression score（0–100），按**解剖实体/组织**给分；
                                    该组的 ``expression_stage`` 字段存的是 Bgee 证据质量（gold/silver）
  · ``data_source='atlas_embryo'`` —— Expression Atlas TPM（E-ERAD-475），按**胚胎分期**给分；
                                    覆盖的是 marker 基因子集（约 2 千行），不是全库

因此对外统一返回 ``source`` 字段，界面必须分开画、分开标注单位。
"""
from __future__ import annotations

import bootstrap  # noqa: F401

import time
from typing import Dict, List, Optional

from common_utils import logger

# Expression Atlas（E-ERAD-475）分期排序：从早到晚（本地库里出现的取值都在这里）
_STAGE_ORDER = [
    "cleaving embryo", "blastula", "gastrula", "segmentation 1-4 somites",
    "segmentation 5-9 somites", "segmentation 10-13 somites", "segmentation 14-19 somites",
    "segmentation 20-25 somites", "pharyngula prim-5", "pharyngula prim-15", "pharyngula prim-25",
    "hatching long-pec", "larval protruding mouth", "larval day 4", "larval day 5",
]


def _client():
    from mysql_client import get_mysql_client

    return get_mysql_client()


def _stage_key(stage: str) -> int:
    try:
        return _STAGE_ORDER.index(str(stage))
    except ValueError:
        return len(_STAGE_ORDER) + (0 if stage else 99)


def resolve_gene(gene: str) -> Optional[dict]:
    """基因名/符号/别名/Ensembl/ZFIN → gene_basic 一行。"""
    from gene_toolkit import gene_id_of

    gid = gene_id_of(gene)
    if not gid:
        return None
    res = _client().execute_sql(
        "SELECT gene_id, gene_name, gene_symbol, ensembl_id, zfin_id, chromosome, gene_function"
        " FROM gene_basic WHERE gene_id = :gid LIMIT 1", params={"gid": gid}, max_rows=0)
    rows = res.get("data") or []
    return rows[0] if rows else None


def gene_profile(gene: str, top_tissues: int = 24, lang: str = "zh") -> dict:
    """单个基因的表达谱：Bgee 组织分（0–100） + Atlas 胚胎分期 TPM。

    ``lang='en'`` 时把 Atlas 落库的中文组织/分期按 ZFA 英文名输出（见
    ``anatomy_i18n``）；默认 zh 时行为与原来逐字一致。
    """
    from anatomy_i18n import localize

    info = resolve_gene(gene)
    if not info:
        err = ("No such gene in the local library: %s" % gene) if lang == "en" else ("本地库找不到该基因：%s" % gene)
        return {"ok": False, "error": err}
    gid = info["gene_id"]
    res = _client().execute_sql(
        "SELECT tissue, expression_stage, expression_level, data_source"
        " FROM gene_expression WHERE gene_id = :gid", params={"gid": gid}, max_rows=0)
    rows = res.get("data") or []

    tissues, stages, zfin = [], [], []
    for r in rows:
        src = (r.get("data_source") or "").strip()
        level = float(r.get("expression_level") or 0)
        if src == "bgee":
            tissues.append({"tissue": localize(r.get("tissue") or "-", lang, "tissue"), "level": level,
                            "quality": r.get("expression_stage") or ""})
        elif src.startswith("zfin"):
            # ZFIN 表达是定性记录（组织 + 时期 + 检测方法），没有数值分数
            zfin.append({
                "tissue": localize(r.get("tissue") or "-", lang, "tissue"),
                "stage": localize(r.get("expression_stage") or "-", lang, "stage"),
                "source": src,
            })
        elif src == "atlas_embryo":
            # 2026-10-28：带上 tissue。
            # 同一个"成体"分期下会挂多条记录（不同成体组织），但原来只返回 stage+level，
            # 界面上就变成两行一模一样的「成体 45.2 / 成体 38.7」，看着像重复数据。
            # tissue 为空时保持原样（胚胎分期本身没有组织维度）。
            t = (r.get("tissue") or "").strip()
            item = {"stage": localize(r.get("expression_stage") or "-", lang, "stage"), "level": level}
            if t and t != "-":
                item["tissue"] = localize(t, lang, "tissue")
            stages.append(item)

    tissues.sort(key=lambda x: -x["level"])
    stages.sort(key=lambda x: _stage_key(x["stage"]))
    detail = {
        "gene_id": gid,
        "gene_name": info.get("gene_name"),
        "gene_symbol": info.get("gene_symbol"),
        "ensembl_id": info.get("ensembl_id"),
        "zfin_id": info.get("zfin_id"),
        "chromosome": info.get("chromosome"),
        "gene_function": info.get("gene_function"),
    }
    return {
        "ok": True,
        "gene": detail,
        "bgee": {
            "source": "bgee",
            "unit": "expression score (0–100)",
            "n_tissues": len(tissues),
            "tissues": tissues[:top_tissues],
            "all_tissues": tissues,
            "max": tissues[0] if tissues else None,
        },
        "atlas": {
            "source": "atlas_embryo",
            "unit": "TPM (E-ERAD-475, marker subset)",
            "n_stages": len(stages),
            "stages": stages,
        },
        "zfin": {
            "source": "zfin",
            "unit": "qualitative (anatomy + stage + assay)",
            "n_records": len(zfin),
            "records": zfin[:top_tissues],
            "all_records": zfin,
        },
        "n_rows": len(rows),
        "note": "三组单位不同：Bgee 是 0–100 表达存在性置信分（按组织），"
                "Expression Atlas 是 TPM（按胚胎分期，仅 marker 基因），ZFIN 是定性的组织与时期记录。"
                "不要跨组比较数值。",
        "note_en": "The three data sets use different units: Bgee is a 0–100 expression-existence "
                   "confidence score (by anatomical entity), Expression Atlas reports TPM by embryonic "
                   "stage (marker-gene subset only), and ZFIN provides qualitative anatomy and stage "
                   "records. Do not compare values across the three.",
    }


_TISSUES = {"t": 0.0, "data": None}


def tissue_index(ttl: int = 900) -> List[dict]:
    """组织列表（含覆盖基因数），按覆盖度排序。"""
    if _TISSUES["data"] is not None and time.time() - _TISSUES["t"] < ttl:
        return _TISSUES["data"]
    res = _client().execute_sql(
        "SELECT tissue AS t, COUNT(DISTINCT gene_id) AS g, MAX(expression_level) AS mx"
        " FROM gene_expression WHERE data_source = 'bgee' GROUP BY tissue ORDER BY g DESC", max_rows=0)
    data = [{"tissue": r.get("t"), "genes": int(r.get("g") or 0),
             "max_level": float(r.get("mx") or 0)} for r in (res.get("data") or []) if r.get("t")]
    _TISSUES.update({"t": time.time(), "data": data})
    return data


def top_in_tissue(tissue: str, limit: int = 30) -> dict:
    """某个组织里表达最高的基因（Bgee score 排序）。"""
    name = (tissue or "").strip()
    if not name:
        return {"ok": False, "error": "缺少 tissue"}
    cap = max(1, min(int(limit or 30), 200))
    res = _client().execute_sql(
        "SELECT gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, gb.chromosome,"
        "       ge.expression_level, ge.expression_stage"
        " FROM gene_expression ge JOIN gene_basic gb ON gb.gene_id = ge.gene_id"
        " WHERE ge.data_source = 'bgee' AND ge.tissue = :t"
        " ORDER BY ge.expression_level DESC LIMIT %d" % cap,
        params={"t": name}, max_rows=0)
    rows = res.get("data") or []
    return {"ok": True, "tissue": name, "unit": "expression score (0–100)",
            "count": len(rows),
            "genes": [{"gene_name": r.get("gene_name"), "gene_symbol": r.get("gene_symbol"),
                       "ensembl_id": r.get("ensembl_id"), "zfin_id": r.get("zfin_id"),
                       "chromosome": r.get("chromosome"),
                       "level": float(r.get("expression_level") or 0),
                       "quality": r.get("expression_stage") or ""} for r in rows]}


def compare_genes(genes: List[str], top_tissues: int = 12) -> dict:
    """多基因对比：取这些基因共同覆盖度最高的若干个组织做矩阵。"""
    ids, labels = [], []
    for g in genes[:8]:
        info = resolve_gene(g)
        if info:
            ids.append(info["gene_id"])
            labels.append(info.get("gene_name") or g)
    if len(ids) < 2:
        return {"ok": False, "error": "至少需要 2 个能在本地库解析到的基因"}
    placeholders = ",".join([":g%d" % i for i in range(len(ids))])
    params = {"g%d" % i: gid for i, gid in enumerate(ids)}
    res = _client().execute_sql(
        "SELECT gene_id, tissue, expression_level FROM gene_expression"
        " WHERE data_source = 'bgee' AND gene_id IN (%s)" % placeholders, params=params, max_rows=0)
    rows = res.get("data") or []
    per_gene: Dict[int, Dict[str, float]] = {gid: {} for gid in ids}
    tissue_cov: Dict[str, int] = {}
    for r in rows:
        gid = int(r.get("gene_id"))
        t = r.get("tissue") or "-"
        per_gene.setdefault(gid, {})[t] = float(r.get("expression_level") or 0)
        tissue_cov[t] = tissue_cov.get(t, 0) + 1
    tissues = [t for t, _ in sorted(tissue_cov.items(), key=lambda kv: -kv[1])][:top_tissues]
    return {
        "ok": True,
        "unit": "expression score (0–100)",
        "genes": labels,
        "tissues": tissues,
        "matrix": [[per_gene.get(gid, {}).get(t) for t in tissues] for gid in ids],
    }
