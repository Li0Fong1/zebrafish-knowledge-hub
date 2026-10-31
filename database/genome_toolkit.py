# -*- coding: utf-8 -*-
"""基因组数据层（本地）：组装登记 + 染色体长度/GC + 基因分布 + 本地比对库规模。

数据来源（全部本地、可回溯，界面上也要如实标注）：
  · ``$RT/genome_info.json``   由 ``$RT/build_chrom_lengths.py`` 扫描 GRCz12tu 基因组 FASTA 生成
                              （25 条染色体，长度 + GC%，FASTA 头部自带 "chromosome N"，不是猜的）
  · ``assembly_registry`` 表    组装登记：GRCz11（本地注释锚定，Ensembl r116）/ GRCz12tu（RefSeq 标准）
  · ``gene_basic.chromosome``  按染色体统计基因数（**本地注释是 GRCz11 坐标系**，与 GRCz12tu 长度
                              版本不同，界面上必须写清，只用于看分布趋势）

设计原则：所有统计都带来源与版本；缺文件/缺表时返回 ``ready: False`` + 原因，不编造数字。
"""
from __future__ import annotations

import bootstrap  # noqa: F401

import json
import os
import re
import time
from typing import List, Optional

from common_utils import logger


def _blast_dir() -> str:
    from seq_toolkit import BLAST_DIR

    return BLAST_DIR


def _runtime_dir() -> str:
    """运行期目录（缓存 / 下载 / 索引都在这一层）。"""
    return os.path.dirname(_blast_dir())


def _client():
    from mysql_client import get_mysql_client

    return get_mysql_client()


# ---------------------------------------------------------------- 染色体长度

_INFO = {"mtime": 0.0, "data": None}


def genome_info() -> dict:
    """染色体长度/GC（读缓存文件；文件更新自动重载）。"""
    path = os.path.join(_runtime_dir(), "genome_info.json")
    if not os.path.isfile(path):
        return {"ready": False, "error": "缺少染色体长度缓存：%s（跑 build_chrom_lengths.py 生成）" % path,
                "chromosomes": []}
    try:
        mtime = os.path.getmtime(path)
        if _INFO["data"] is None or _INFO["mtime"] != mtime:
            with open(path, encoding="utf-8") as fh:
                _INFO["data"] = json.load(fh)
            _INFO["mtime"] = mtime
    except Exception as exc:  # noqa: BLE001
        logger.error("读取 genome_info.json 失败：%s", exc)
        return {"ready": False, "error": str(exc), "chromosomes": []}
    out = dict(_INFO["data"] or {})
    out["ready"] = True
    return out


_COUNTS = {"t": 0.0, "data": None}


def chromosome_gene_counts(ttl: int = 600) -> dict:
    """按染色体统计基因数；非纯数字取值（``-``、``MT``、散装 scaffold）单独汇总。"""
    if _COUNTS["data"] is not None and time.time() - _COUNTS["t"] < ttl:
        return _COUNTS["data"]
    res = _client().execute_sql(
        "SELECT chromosome, COUNT(*) AS c FROM gene_basic GROUP BY chromosome", max_rows=0)
    per, other, total = {}, 0, 0
    for row in (res.get("data") or []):
        chrom = str(row.get("chromosome") or "").strip()
        cnt = int(row.get("c") or 0)
        total += cnt
        if re.fullmatch(r"\d+", chrom):
            per[chrom] = per.get(chrom, 0) + cnt
        else:
            other += cnt
    data = {"per_chromosome": per, "other": other, "total": total}
    _COUNTS.update({"t": time.time(), "data": data})
    return data


def chromosome_overview() -> List[dict]:
    """合并「GRCz12tu 实测长度」与「本地注释的染色体基因数」。"""
    info = genome_info()
    counts = chromosome_gene_counts()
    rows: List[dict] = []
    for seq in info.get("chromosomes") or []:
        name = str(seq.get("name") or "")
        chr_no = str(seq.get("chr") or "")
        if not chr_no:
            m = re.match(r"^NC_(\d+)\.", name)
            chr_no = str(int(m.group(1)) - 133175) if m else ""
        length = int(seq.get("length") or 0)
        genes = counts["per_chromosome"].get(chr_no, 0)
        rows.append({
            "chromosome": chr_no,
            "accession": name,
            "length": length,
            "length_mb": round(length / 1048576, 1) if length else None,
            "gc": seq.get("gc"),
            "gene_count": genes,
            "gene_density": round(genes / (length / 1e6), 1) if length else 0.0,
        })
    rows.sort(key=lambda r: (int(r["chromosome"]) if r["chromosome"].isdigit() else 99))
    return rows


# ---------------------------------------------------------------- 组装 / 库

def assembly_table() -> List[dict]:
    res = _client().execute_sql(
        "SELECT assembly_name, ncbi_refseq_accession, ncbi_genbank_accession, strain,"
        " is_local_anchor, is_ncbi_reference, ensembl_assembly, ensembl_release, notes"
        " FROM assembly_registry ORDER BY is_local_anchor DESC, assembly_name", max_rows=0)
    return res.get("data") or []


def library_stats() -> List[dict]:
    """本地比对库 / 序列文件规模（回答"我们比对的是什么库、多大"）。"""
    bd = _blast_dir()
    items = [
        ("cdna_db", "转录组 BLAST 库（GRCz12tu CDS）", "Transcriptome BLAST DB (GRCz12tu CDS)",
         "cdna_db.nsq", "blastn 转录组脱靶筛查用", "for transcriptome off-target screening with blastn"),
        ("genome_db", "全基因组 BLAST 库（GRCz12tu）", "Whole-genome BLAST DB (GRCz12tu)",
         "genome_db.nsq", "blastn 全基因组脱靶筛查用", "for whole-genome off-target screening with blastn"),
        ("cds", "CDS FASTA（GRCz12tu）", "CDS FASTA (GRCz12tu)",
         "grcz12tu_cds.fna", "建库源序列", "source sequences for building the DB"),
        ("genome", "基因组 FASTA（GRCz12tu）", "Genome FASTA (GRCz12tu)",
         "grcz12tu_genome.fna", "建库源序列 / 染色体长度来源",
         "source sequences for the DB / source of chromosome lengths"),
    ]
    out: List[dict] = []
    for key, label, label_en, fname, note, note_en in items:
        path = os.path.join(bd, fname)
        size = os.path.getsize(path) if os.path.isfile(path) else 0
        out.append({"key": key, "label": label, "label_en": label_en, "file": fname,
                    "note": note, "note_en": note_en,
                    "size_mb": round(size / 1048576, 1), "ready": size > 0})
    return out


def overview() -> dict:
    """基因组页一次性数据：组装表 + 染色体表 + 库规模 + 统计口径说明。"""
    info = genome_info()
    counts = chromosome_gene_counts()
    chroms = chromosome_overview()
    total_len = int(info.get("total_length") or 0)
    gc_vals = [c.get("gc") for c in chroms if c.get("gc") is not None]
    return {
        "ready": bool(info.get("ready")),
        "error": info.get("error"),
        "assembly": info.get("assembly") or "GRCz12tu",
        "source_fasta": info.get("source_fasta"),
        "n_sequences": info.get("n_sequences") or len(chroms),
        "total_length": total_len,
        "total_length_gb": round(total_len / 1e9, 2) if total_len else None,
        "mean_gc": round(sum(gc_vals) / len(gc_vals), 2) if gc_vals else None,
        "gene_total": counts.get("total"),
        "gene_other": counts.get("other"),
        "chromosomes": chroms,
        "assemblies": assembly_table(),
        "libraries": library_stats(),
        "note": "染色体长度与 GC 为 GRCz12tu 序列实测（BLAST 建库用的同一份 FASTA，本地扫描得到）；"
                "基因数按本地注释库的染色体编号统计（本地注释坐标 = GRCz11 / Ensembl r116）。"
                "两者版本不同，仅用于看分布趋势，不做坐标换算。",
        "note_en": "Chromosome lengths and GC are measured on the GRCz12tu sequences (the same FASTA "
                   "the BLAST databases were built from, scanned locally); gene counts are grouped by "
                   "chromosome in the local annotation library (local annotation coordinates = GRCz11 / "
                   "Ensembl release 116). The two use different assembly versions, so they are shown to "
                   "indicate distribution only — no coordinate conversion is applied.",
    }


def chromosome_genes(chrom: str, limit: int = 60) -> List[dict]:
    """某染色体上的基因（按符号排序）。"""
    if not re.fullmatch(r"\d+", str(chrom or "")):
        return []
    cap = max(1, min(int(limit or 60), 300))
    res = _client().execute_sql(
        "SELECT gene_id, gene_name, gene_symbol, ensembl_id, zfin_id, gene_function"
        " FROM gene_basic WHERE chromosome = :c ORDER BY gene_name LIMIT %d" % cap,
        params={"c": str(chrom)}, max_rows=0)
    return res.get("data") or []


# ------------------------------------------------------------ 装配之间对比

def grcz11_chrom_lengths(ttl_days: int = 7) -> dict:
    """Ensembl REST 取 GRCz11 各染色体长度（带磁盘缓存）。

    为什么要联网：本地只有 GRCz12tu 的 FASTA（BLAST 建库那份），没有 GRCz11 序列文件；
    而"两个装配差多少"正是基因组页值得回答的问题。取不到就返回 ready=False + 原因，
    页面据此只显示 GRCz12tu 一侧，不编数字。
    """
    cache = os.path.join(_runtime_dir(), "grcz11_chrom_lengths.json")
    try:
        if os.path.isfile(cache) and time.time() - os.path.getmtime(cache) < ttl_days * 86400:
            with open(cache, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("chromosomes"):
                data["ready"] = True
                data["cached"] = True
                return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 GRCz11 染色体长度缓存失败：%s", exc)

    try:
        from gene_collector import _api_get

        info = _api_get("/info/assembly/danio_rerio", {"bands": 0}, retries=2, timeout=20)
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "error": f"Ensembl 请求失败：{exc}", "chromosomes": []}
    if not isinstance(info, dict):
        return {"ready": False, "error": "Ensembl 未返回组装信息（可能被限流）", "chromosomes": []}

    per = {}
    for region in info.get("top_level_region") or []:
        name = str(region.get("name") or "")
        length = int(region.get("length") or 0)
        if not length:
            continue
        chrom = name
        if name.upper() in ("MT", "M"):
            chrom = "MT"
        elif not re.fullmatch(r"\d+", name):
            continue          # 只保留 1–25 与线粒体，散装 scaffold 不进对比表
        per[chrom] = length

    data = {
        "assembly": info.get("assembly_name") or "GRCz11",
        "source": "Ensembl REST /info/assembly/danio_rerio",
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "chromosomes": [{"chromosome": k, "length": v} for k, v in
                        sorted(per.items(), key=lambda kv: (kv[0] != "MT", int(kv[0]) if kv[0].isdigit() else 99))],
    }
    try:
        with open(cache, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("写 GRCz11 染色体长度缓存失败：%s", exc)
    data["ready"] = bool(per)
    return data


def chrom_compare() -> dict:
    """逐染色体对比：GRCz12tu 实测长度 vs GRCz11（Ensembl）长度 + 注释基因数。"""
    info = genome_info()
    counts = chromosome_gene_counts()
    g11 = grcz11_chrom_lengths()
    len11 = {c["chromosome"]: int(c["length"]) for c in (g11.get("chromosomes") or [])}
    rows = []
    for row in chromosome_overview():
        chrom = row["chromosome"]
        l12 = int(row.get("length") or 0)
        l11 = len11.get(chrom) or 0
        rows.append({
            **row,
            "length_grc11": l11 or None,
            "length_grc11_mb": round(l11 / 1048576, 1) if l11 else None,
            "delta_mb": round((l12 - l11) / 1048576, 2) if (l12 and l11) else None,
            "delta_pct": round((l12 - l11) / l11 * 100, 2) if (l12 and l11) else None,
        })
    return {
        "ready": bool(info.get("ready")),
        "grc11": {"ready": bool(g11.get("ready")), "error": g11.get("error"),
                  "source": g11.get("source"), "fetched_at": g11.get("fetched_at")},
        "rows": rows,
        "note": "GRCz12tu 长度为本地基因组 FASTA 实测；GRCz11 长度来自 Ensembl REST（在线取一次后缓存 7 天）。"
                "两列都是整条染色体的组装长度，只用于看两版装配的差异；"
                "想把具体区间换到 GRCz12tu 坐标，在问答里问「GRCz11 chr5:24086227-24097805 换算到 GRCz12tu」，"
                "系统会走 UCSC chain 做碱基级换算（只支持指向 GRCz12tu 的方向）。",
        "note_en": "GRCz12tu lengths are measured on the local genome FASTA; GRCz11 lengths come from "
                   "Ensembl REST (fetched once, cached for 7 days). Both columns are whole-chromosome "
                   "assembly lengths for comparing the two builds only. For an actual interval, ask in chat: "
                   "\"GRCz11 chr5:24086227-24097805 to GRCz12tu\" and the system lifts it base-by-base with "
                   "UCSC chains (only towards GRCz12tu).",
    }
