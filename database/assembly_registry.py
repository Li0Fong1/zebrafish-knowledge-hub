# -*- coding: utf-8 -*-
"""斑马鱼参考组装（genome assembly）权威注册表。

用途
----
1. 回答「斑马鱼有几个基因组版本 / 有哪些组装 / 版本之间差异 / 该用哪个版本」这类
   **领域元数据问题**时，作为本地权威材料注入提示词，避免依赖大模型记忆而导致
   清单不全、版本号错误。
2. 提供**不依赖大模型**的兜底清单（registry_complete_answer），保证回答完整性。

数据口径
--------
以 NCBI RefSeq / Ensembl / ZFIN / UCSC 公开信息为准；把握不足的字段一律留空，
**不编造日期与登录号**。仅收录 Danio rerio 参考系列组装与常用品系组装。
"""

from __future__ import annotations

from typing import Dict, List

# ── 参考组装（新 → 旧） ────────────────────────────────────────────────────
# 字段含义：
#   name     主用名
#   aliases  常见别名 / 登录号
#   refseq   NCBI RefSeq 登录号（不确定则留空）
#   released 发布年份（不确定则留空）
#   ensembl  Ensembl 侧使用情况
#   strain   参考品系
#   role     与本系统 / 研究实践的关系
ASSEMBLIES: List[Dict[str, str]] = [
    {
        "name": "GRCz12tu",
        "aliases": ["GCF_049306965.2", "GCF_049306965.1"],
        "refseq": "GCF_049306965.2",
        "released": "2025",
        "ensembl": "Ensembl 后续版本逐步切换采用",
        "strain": "Tuebingen (TU)",
        "role": "NCBI Datasets 现行参考组装；本系统的**标准名解析 / BLAST 核酸库 / cDNA 提取**锚定于此",
    },
    {
        "name": "GRCz11",
        "aliases": ["Zv11", "danRer11", "GCF_000002035.6"],
        "refseq": "GCF_000002035.6",
        "released": "2017",
        "ensembl": "Ensembl 92 起为默认（本系统本地库为 release 116）",
        "strain": "Tuebingen (TU)",
        "role": "本系统**本地库锚定版本**：基因 ID / 染色体坐标 / 表达 / GO / KEGG；UCSC 上对应 danRer11",
    },
    {
        "name": "GRCz10",
        "aliases": ["Zv10", "GCF_000002035.5"],
        "refseq": "GCF_000002035.5",
        "released": "2015",
        "ensembl": "Ensembl 84–91 为默认",
        "strain": "Tuebingen (TU)",
        "role": "上一代参考组装，旧文献/旧注释常基于此版本",
    },
    {
        "name": "Zv9",
        "aliases": ["GRCz9", "danRer7"],
        "refseq": "",
        "released": "2010",
        "ensembl": "Ensembl 57–83 为默认",
        "strain": "Tuebingen (TU)",
        "role": "首个完成度较高的 Sanger 组装，大量经典发育/遗传文献基于它",
    },
    {
        "name": "Zv8",
        "aliases": ["danRer6"],
        "refseq": "",
        "released": "2008",
        "ensembl": "Ensembl 49–56",
        "strain": "Tuebingen (TU)",
        "role": "过渡组装",
    },
    {
        "name": "Zv1–Zv7",
        "aliases": ["danRer1–danRer5"],
        "refseq": "",
        "released": "2001–2007",
        "ensembl": "早期 Ensembl 版本",
        "strain": "Tuebingen (TU)",
        "role": "Sanger 早期迭代组装（逐年提升连续性），仅用于历史对照",
    },
]

# ── 非参考 / 品系特有组装（研究常见，不要与参考混淆） ──────────────────────
STRAIN_ASSEMBLIES: List[Dict[str, str]] = [
    {"name": "NHGRI-1", "note": "NHGRI 品系组装，常用于变异/遗传作图对照"},
    {"name": "AB × TU 等品系组装", "note": "特定实验室品系，坐标与参考不一致，不可直接混用"},
    {"name": "DrAB1 / DrAB2 等 AB 系补充组装", "note": "AB 品系补充组装，**不等于** GRCz11"},
]

# ── 本系统锚定关系（回答必须体现，保证可追溯） ─────────────────────────────
LOCAL_ANCHORS: Dict[str, str] = {
    "标准名解析 / BLAST 核酸库 / cDNA 提取": "GRCz12tu（RefSeq GCF_049306965.2）",
    "本地基因 ID / 坐标 / 表达 / GO / KEGG": "GRCz11（Ensembl release 116，GCF_000002035.6）",
}

# ── 官方入口（回答缺失来源时强制补齐） ─────────────────────────────────────
OFFICIAL_SOURCES: List[Dict[str, str]] = [
    {"label": "NCBI Datasets · Danio rerio (taxon 7955)",
     "url": "https://www.ncbi.nlm.nih.gov/datasets/taxonomy/7955/"},
    {"label": "Ensembl · Danio rerio",
     "url": "https://www.ensembl.org/Danio_rerio/Info/Index"},
    {"label": "ZFIN · 斑马鱼模型生物数据库",
     "url": "https://zfin.org/"},
    {"label": "UCSC Genome Browser · danRer11 (GRCz11)",
     "url": "https://genome.ucsc.edu/cgi-bin/hgGateway?db=danRer11"},
]

# ── 触发词：命中即按「领域元数据」处理（完整清单 + 来源，不压缩） ──────────
def _load_db_rows() -> List[Dict[str, str]]:
    """从 `assembly_registry` 数据表读取权威行（失败返回空表）。

    该表由运维侧维护，包含本模块常量没有的字段（is_local_anchor /
    is_ncbi_reference / ensembl_release / notes），是**权威来源**；
    模块常量仅作离线兜底，以避免「表 vs 常量」双源漂移。
    """
    try:
        # 2026-11-13：复用全局单例，别每次 new 一个 engine —— 线上实测曾因
        # 反复创建连接池把 MySQL 的 151 个连接全部占满（1040 Too many
        # connections），新访客的首次查询直接拿不到连接。
        from mysql_client import get_mysql_client  # type: ignore
        res = get_mysql_client().execute_sql(
            "SELECT assembly_name, ncbi_refseq_accession, ncbi_genbank_accession, "
            "strain, is_local_anchor, is_ncbi_reference, ensembl_assembly, "
            "ensembl_release, notes FROM assembly_registry"
        )
        out: List[Dict[str, str]] = []
        for row in (res.get("data") or []):
            name = str(row.get("assembly_name") or "").strip()
            if not name:
                continue
            item: Dict[str, str] = {"name": name}
            ref = str(row.get("ncbi_refseq_accession") or "").strip()
            if ref:
                item["refseq"] = ref
            strain = str(row.get("strain") or "").strip()
            if strain:
                item["strain"] = strain
            ens = str(row.get("ensembl_assembly") or "").strip()
            rel = str(row.get("ensembl_release") or "").strip()
            if ens or rel:
                item["ensembl"] = ("%s release %s" % (ens, rel)).strip() if rel else ens
            note = str(row.get("notes") or "").strip()
            if note:
                item["role"] = note
            flags = []
            if str(row.get("is_local_anchor") or "").strip() in ("1", "True", "true"):
                flags.append("本系统本地锚定")
            if str(row.get("is_ncbi_reference") or "").strip() in ("1", "True", "true"):
                flags.append("NCBI 现行参考")
            if flags:
                item["role"] = (item.get("role") or "") + "（" + "／".join(flags) + "）"
            out.append(item)
        return out
    except Exception:
        return []


def effective_assemblies() -> List[Dict[str, str]]:
    """组装清单：**数据表优先、常量兜底补齐**（保证版本沿革完整）。"""
    db = _load_db_rows()
    if not db:
        return [dict(a) for a in ASSEMBLIES]
    by_name = {a["name"]: dict(a) for a in ASSEMBLIES}
    merged: List[Dict[str, str]] = []
    for row in db:
        item = dict(by_name.get(row["name"], {}))
        item.update({k: v for k, v in row.items() if v})
        merged.append(item)
    seen = {m["name"] for m in merged}
    for a in ASSEMBLIES:  # 表中未列出的历史版本仍保留
        if a["name"] not in seen:
            merged.append(dict(a))
    return merged


TRIGGERS = (
    "基因组版本", "基因组的版本", "组装版本", "参考基因组", "参考组装",
    "assembly", "assemblies", "annotation release", "注释版本",
    "几个版本", "有哪些版本", "版本有哪些", "版本沿革", "版本历史", "版本差异",
    "grcz", "zv9", "zv10", "zv11", "danrer", "gcf_000002035", "gcf_049306965",
    "基因组有多大", "基因组大小", "染色体数目", "多少条染色体",
)


def is_assembly_question(question: str) -> bool:
    """是否属于「组装 / 版本 / 基因组基础参数」类领域元数据问题。"""
    q = (question or "").strip().lower()
    if len(q) < 4:
        return False
    if not any(t in q for t in TRIGGERS):
        return False
    # 排除纯产品用法问题（问界面怎么用）
    if any(k in q for k in ("怎么用", "怎么操作", "界面上", "按钮")):
        return False
    return True


def registry_material() -> str:
    """生成供提示词使用的权威材料块（紧凑、结构化）。"""
    lines = ["【本地权威材料 · 斑马鱼参考组装注册表（以本表为准，不得改写版本号）】"]
    lines.append("版本（新→旧） | RefSeq | 年份 | Ensembl 使用情况 | 品系 | 与本系统/实践的关系")
    for a in effective_assemblies():
        lines.append(
            "{} | {} | {} | {} | {} | {}".format(
                a["name"], a.get("refseq") or "—", a.get("released") or "—",
                a.get("ensembl") or "—", a.get("strain") or "—", a.get("role") or "",
            )
        )
    lines.append("")
    lines.append("非参考/品系特有组装（勿与参考混淆）：")
    for s in STRAIN_ASSEMBLIES:
        lines.append("- {}：{}".format(s["name"], s["note"]))
    lines.append("")
    lines.append("本系统锚定关系：")
    for k, v in LOCAL_ANCHORS.items():
        lines.append("- {} → {}".format(k, v))
    lines.append("")
    lines.append("官方入口（回答的【来源】必须包含其中的 2–3 条）：")
    for s in OFFICIAL_SOURCES:
        lines.append("- {}：{}".format(s["label"], s["url"]))
    lines.append("")
    lines.append(
        "写作要求：先给结论（现行参考 + 本地锚定），再给**完整清单**（上面每条都要出现，不得删减、"
        "不得只写两三条），然后给版本兼容提醒，最后给【来源】。本主题属学术/组学元数据问题，"
        "**必须**给出官方来源链接；若本地文献库未命中，写明「本地文献库未命中，以下依据官方资源」，"
        "严禁以「系统/产品元问题」为理由拒绝给出来源或文献。"
    )
    return "\n".join(lines)


def registry_complete_answer() -> str:
    """不依赖大模型的兜底答案（当模型输出明显不全时追加/替换）。"""
    rows = []
    for a in effective_assemblies():
        rows.append(
            "| {} | {} | {} | {} | {}".format(
                a["name"], a.get("refseq") or "—", a.get("released") or "—",
                a.get("ensembl") or "—", a.get("role") or "",
            )
        )
    lines = [
        "**结论**：斑马鱼（Danio rerio）现行参考组装是 **GRCz12tu（RefSeq GCF_049306965.2）**；"
        "本系统的标准名解析 / BLAST / cDNA 锚定 GRCz12tu，而本地基因 ID、坐标、表达、GO/KEGG 仍锚定 **GRCz11**（Ensembl release 116）。",
        "",
        "**参考组装清单（新 → 旧）**",
        "",
        "| 组装 | RefSeq | 年份 | Ensembl | 与本系统 / 研究实践的关系 |",
        "| --- | --- | --- | --- | --- |",
        *rows,
        "",
        "**非参考 / 品系特有组装**（勿与参考组装混淆）",
        *["- {}：{}".format(s["name"], s["note"]) for s in STRAIN_ASSEMBLIES],
        "",
        "**版本兼容提醒**",
        "- 不同组装的染色体坐标与部分 Ensembl ID 不可直接混用，跨版本需先做 ID/坐标映射。",
        "- Ensembl ID 可带版本后缀（如 ENSDARG00000000001.5），换组装后可能变化。",
        "- 参考组装基于 Tuebingen (TU) 品系；AB 品系的补充组装不等于 GRCz11。",
        "",
        "**来源**（官方资源）",
        *["- {}：{}".format(s["label"], s["url"]) for s in OFFICIAL_SOURCES],
    ]
    return "\n".join(lines)


def official_sources_block() -> str:
    """精简来源块（用于后处理补齐）。"""
    lines = ["", "**来源**（官方资源）"]
    for s in OFFICIAL_SOURCES[:3]:
        lines.append("- {}：{}".format(s["label"], s["url"]))
    return "\n".join(lines)
