# -*- coding: utf-8 -*-
"""基因问答结构化回答：以数据库字段为准，避免 LLM 笼统编造。"""
import bootstrap  # noqa: F401

import re
from decimal import Decimal
from typing import Optional


def _num(val) -> float:
    if val is None:
        return 0.0
    if isinstance(val, Decimal):
        return float(val)
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _is_generic_function(text: str) -> bool:
    if not text:
        return True
    t = text.strip().lower()
    generic = ("protein_coding", "lncrna", "mirna", "rrna", "ensembl", "unknown", "·")
    return len(t) < 40 or any(g in t for g in generic) and "tp53" not in t and "tumor" not in t


def _intent(question: str) -> set:
    q = question.lower()
    flags = set()
    if any(k in q for k in ("表达", "组织", "fpkm", "tpm", "转录", "时期", "阶段")):
        flags.add("expression")
    if any(k in q for k in ("go", "功能注释", "ontology")):
        flags.add("go")
    if any(k in q for k in ("kegg", "通路", "pathway", "富集")):
        flags.add("kegg")
    if any(k in q for k in ("同源", "human", "mouse", "人类", "小鼠")):
        flags.add("homology")
    if any(k in q for k in ("文献", "pubmed", "参考")):
        flags.add("reference")
    if any(k in q for k in ("染色体", "定位", "ensembl", "zfin", "id", "转换")):
        flags.add("basic")
    if not flags or any(k in q for k in ("是什么", "介绍", "详情", "功能", "干什么")):
        flags.add("overview")
    return flags


def _split_expression_rows(rows: list) -> tuple:
    embryo, tissue = [], []
    for r in rows:
        src = (r.get("data_source") or "atlas_embryo").lower()
        if src == "bgee":
            tissue.append(r)
        else:
            embryo.append(r)
    return embryo, tissue


# 发育阶段类解剖项（组织表达展示时可过滤）
_BGEE_DEV_TERMS = {
    "blastula", "gastrula", "cleaving embryo", "embryo", "early embryo",
    "segmentation stage", "pharyngula stage", "larval stage", "zygote",
    "presomitic mesoderm", "paraxial mesoderm", "tail bud paraxial mesoderm",
    "somite",
}


def format_bgee_tissue_block(
    gene_name: str, rows: list, limit: int = 15, adult_focus: bool = False
) -> str:
    if not rows:
        return ""
    parsed = []
    for r in rows:
        tis = r.get("tissue") or "未知组织"
        if adult_focus and tis.lower() in _BGEE_DEV_TERMS:
            continue
        if adult_focus and any(k in tis.lower() for k in ("embryo", "blastula", "gastrula", "larval")):
            continue
        score = _num(r.get("expression_level"))
        parsed.append((tis, score))
    if not parsed and adult_focus:
        for r in rows:
            tis = r.get("tissue") or "未知组织"
            parsed.append((tis, _num(r.get("expression_level"))))
    parsed.sort(key=lambda x: -x[1])
    peak_tis, peak_score = parsed[0]
    lines = [
        f"【{gene_name} 组织表达（Bgee 整合图谱）】",
        "数据源：Bgee v15.2（RNA-seq + ZFIN in situ 等）；单位：Expression Score（0–100，"
        "表达存在性置信分——越高越确定有表达，**不是**丰度/拷贝数/TPM）",
        f"有表达调用的组织：{len(parsed)} 个",
        f"峰值组织：{peak_tis}，Score = {peak_score:g}/100",
        "",
        "按表达强度从高到低（回复时每个数值后面必须带 \"/100\" 并注明是 Bgee 表达分）：",
    ]
    for i, (tis, score) in enumerate(parsed[:limit], 1):
        lines.append(f"  {i}. {tis} — {score:g}/100（Bgee score）")
    if len(parsed) > limit:
        lines.append(f"  … 另有 {len(parsed) - limit} 个组织")
    return "\n".join(lines)


def format_expression_block(gene_name: str, rows: list, limit: int = 18, prefer_tissue: bool = False) -> str:
    if not rows:
        return f"【表达】未找到 {gene_name} 的表达记录。"

    embryo_rows, tissue_rows = _split_expression_rows(rows)
    parts = []
    adult_focus = prefer_tissue

    if tissue_rows:
        block = format_bgee_tissue_block(
            gene_name, tissue_rows, limit=limit, adult_focus=adult_focus
        )
        if block:
            parts.append(block)

    if embryo_rows and not prefer_tissue:
        parsed = []
        for r in embryo_rows:
            stage = r.get("expression_stage") or r.get("tissue") or "未知"
            tpm = _num(r.get("expression_level"))
            tissue = r.get("tissue") or ""
            parsed.append((stage, tpm, tissue))

        parsed.sort(key=lambda x: -x[1])
        peak_stage, peak_tpm, _ = parsed[0]
        lines = [
            f"【{gene_name} 表达量（Expression Atlas TPM）】",
            "数据源：Expression Atlas（以 E-ERAD-475 整胚发育时间序列为主，含少量人工标注的组织/解剖项样本）；"
            "单位：TPM（转录本每百万映射读段数）",
            f"记录数：{len(parsed)} 条样本（发育时期/组织）",
            f"峰值：{peak_stage}，{peak_tpm:g} TPM",
            "",
            "按 TPM 从高到低（回复时每个数值后面必须带 \"TPM\"）：",
        ]
        for i, (stage, tpm, tissue) in enumerate(parsed[:limit], 1):
            extra = f" [{tissue}]" if tissue and tissue != "whole embryo" else ""
            lines.append(f"  {i}. {stage}{extra} — {tpm:g} TPM")
        if len(parsed) > limit:
            lines.append(f"  … 另有 {len(parsed) - limit} 个时期")
        parts.append("\n".join(lines))

    if tissue_rows and embryo_rows and not prefer_tissue:
        block = format_bgee_tissue_block(
            gene_name, tissue_rows, limit=min(limit, 10), adult_focus=False
        )
        if block:
            parts.append(block)

    if not tissue_rows and embryo_rows:
        parts.append(
            "【提示】本库无该基因的 Bgee 组织表达记录（Expression Score 0–100）；"
            "上表 TPM 来自 Expression Atlas 时间序列/标注样本。"
            "如需补全 Bgee 组织表达，运行 python -m scripts.collect_tissue_expression。"
        )

    return "\n\n".join(p for p in parts if p)


def format_go_block(gene_name: str, rows: list, limit: int = 10) -> str:
    if not rows:
        return f"【GO 注释】{gene_name} 暂无 GO 记录（深度注释进行中或未覆盖）。"
    lines = [f"【GO 注释】共 {len(rows)} 条："]
    for r in rows[:limit]:
        cat = r.get("go_category") or "-"
        lines.append(f"  · [{cat}] {r.get('go_term', '')}")
    if len(rows) > limit:
        lines.append(f"  … 另有 {len(rows) - limit} 条")
    return "\n".join(lines)


def format_kegg_block(gene_name: str, rows: list, limit: int = 12) -> str:
    if not rows:
        return f"【通路注释】{gene_name} 本库暂无 KEGG/WikiPathways/Reactome 记录。"
    lines = [f"【通路注释】共 {len(rows)} 条（KEGG dre / WikiPathways / Reactome）："]
    for r in rows[:limit]:
        cat = r.get("category") or "-"
        lines.append(f"  · {r.get('pathway_id', '-')} {r.get('pathway_name', '')} [{cat}]")
    if len(rows) > limit:
        lines.append(f"  … 另有 {len(rows) - limit} 条")
    return "\n".join(lines)


def format_homology_block(gene_name: str, rows: list) -> str:
    if not rows:
        return f"【同源基因】{gene_name} 暂无同源记录。"
    lines = [f"【同源基因】共 {len(rows)} 条："]
    for r in rows:
        lines.append(
            f"  · {r.get('species', '?')}: {r.get('homo_gene_name', '?')}"
            f"（相似度 {r.get('homology_score', '-')}）"
        )
    return "\n".join(lines)


def format_refs_block(gene_name: str, rows: list, limit: int = 5) -> str:
    if not rows:
        return f"【文献】{gene_name} 暂无 PubMed 引用。"
    lines = [f"【文献】共 {len(rows)} 篇："]
    for r in rows[:limit]:
        title = (r.get("title") or "")[:80]
        lines.append(f"  · PMID {r.get('pubmed_id', '-')} | {title}")
    return "\n".join(lines)


def _function_summary(func: str, go_rows: list = None, limit: int = 3) -> str:
    """功能描述：优先 gene_function 文本，否则用 GO 深度注释摘要。"""
    if func and not _is_generic_function(func):
        return func[:500]
    go_rows = go_rows or []
    if go_rows:
        terms = []
        for r in go_rows[:limit]:
            t = (r.get("go_term") or "").strip()
            if t:
                terms.append(t)
        if terms:
            more = len(go_rows) - len(terms)
            suffix = f" 等共 {len(go_rows)} 条 GO" if more > 0 else ""
            return "；".join(terms) + suffix
    if func:
        return ""
    return ""


def format_basic_block(b: dict, reference: dict = None, go_rows: list = None) -> str:
    from gene_id_mapper import NCBI_REFERENCE_REFSEQ, REFERENCE_ASSEMBLY, refseq_standard_for_gene

    name = b.get("gene_name", "?")
    sym = b.get("gene_symbol")
    std = refseq_standard_for_gene(b.get("gene_id")) or {}
    std_name = (std.get("gene_symbol") or "").strip()
    lines = ["【基本信息】"]
    if std_name:
        lines.append(f"  标准名（RefSeq {NCBI_REFERENCE_REFSEQ}）：{std_name}")
        if name and name.lower() != std_name.lower():
            lines.append(f"  本地原名（{REFERENCE_ASSEMBLY}）：{name}")
        elif sym and str(sym).lower() != std_name.lower():
            lines.append(f"  本地符号：{sym}")
    else:
        lines.append(
            f"  标准名（本条 RefSeq {NCBI_REFERENCE_REFSEQ} 无唯一注释，沿用本地主名）：{name}"
        )
        if sym and str(sym).lower() != str(name).lower():
            lines.append(f"  本地符号：{sym}")
    lines.extend([
        f"  染色体：{b.get('chromosome') or '-'}",
        f"  Ensembl：{b.get('ensembl_id') or '-'}",
        f"  ZFIN：{b.get('zfin_id') or '-'}",
    ])
    if reference:
        lines.insert(
            1,
            f"  标准名来源：RefSeq {reference.get('ncbi_reference_refseq') or NCBI_REFERENCE_REFSEQ}"
            f"（{reference.get('ncbi_reference_assembly') or 'GRCz12tu'}）；"
            f"本地库锚定 {reference.get('assembly')} / {reference.get('ncbi_assembly')}",
        )
    func = b.get("gene_function") or ""
    summary = _function_summary(func, go_rows)
    if b.get("expression_summary"):
        lines.append(f"  表达摘要：{b['expression_summary']}")
    if summary:
        lines.append(f"  功能描述：{summary}")
    elif func:
        lines.append(f"  功能描述：（Ensembl 基础注释，详细功能待深度注释补充）")
    return "\n".join(lines)


def _ensembl_id_display(v: dict) -> str:
    base = v.get("id_base") or v.get("ensembl_id", "")
    ver = v.get("version_num")
    if ver:
        return f"{base}.{ver}"
    return v.get("ensembl_id") or base or "?"


def collect_gene_unification_groups(resolved: dict) -> dict:
    """历史基因版本统一：原名/原版本 vs 标准名/标准版本。

    标准名优先取 RefSeq GCF_049306965.2（GRCz12tu）注释符号。
    本条 RefSeq 无唯一注释时，标准名栏沿用本地主名并标明来源，不留空。
    GRCz11 / Ensembl / 别名一律进原名；Ensembl ID 进原版本。
    历史原有名全部列出，不做条数截断。
    """
    from gene_id_mapper import NCBI_REFERENCE_ASSEMBLY, NCBI_REFERENCE_REFSEQ, REFERENCE_ASSEMBLY

    canonical = resolved.get("canonical") or {}
    standard = resolved.get("standard") or {}
    query = (resolved.get("query") or "").strip()
    mapped = bool(standard.get("mapped") and (standard.get("gene_symbol") or "").strip())
    std_name = (standard.get("gene_symbol") or "").strip() if mapped else ""
    std_lower = std_name.lower()
    refseq = (standard.get("refseq") or NCBI_REFERENCE_REFSEQ).strip()

    orig_names = []
    seen_names = set()

    def _add_name(name: str, note: str = ""):
        n = (name or "").strip()
        if not n or n == "-":
            return
        key = n.lower()
        if (std_lower and key == std_lower) or key in seen_names:
            return
        seen_names.add(key)
        orig_names.append({"name": n, "note": note})

    if query:
        _add_name(query, "查询输入")
    _add_name(canonical.get("gene_name") or "", f"{REFERENCE_ASSEMBLY} / Ensembl")
    _add_name(canonical.get("gene_symbol") or "", "gene_basic.gene_symbol")
    for a in resolved.get("aliases") or []:
        note = (a.get("alias_type") or a.get("source") or "").strip()
        _add_name(a.get("alias") or "", note)

    std_names = []
    role = (standard.get("role") or "").strip()
    if mapped and std_name:
        std_names.append({
            "name": std_name,
            "role": role or f"RefSeq {refseq} 标准名",
            "source": standard.get("source") or f"RefSeq {refseq}",
        })
    else:
        fallback = (
            (standard.get("gene_symbol") or "").strip()
            or (canonical.get("gene_name") or "").strip()
            or query
        )
        std_names.append({
            "name": fallback or "（无）",
            "role": role or f"本条 RefSeq {refseq} 无唯一注释，沿用本地主名",
            "source": standard.get("source") or "",
        })

    std_versions = []
    if mapped:
        std_versions.append({
            "id": refseq,
            "release": standard.get("assembly") or NCBI_REFERENCE_ASSEMBLY,
            "source": "refseq",
        })
        nid = standard.get("ncbi_gene_id")
        if nid not in (None, ""):
            std_versions.append({
                "id": f"NCBI Gene {nid}",
                "release": None,
                "source": "ncbi_gene",
            })

    orig_versions = []
    seen_ver = set()
    for v in resolved.get("ensembl_versions") or []:
        eid = _ensembl_id_display(v)
        if not eid or eid in seen_ver:
            continue
        seen_ver.add(eid)
        orig_versions.append({
            "id": eid,
            "release": v.get("ensembl_release"),
            "source": v.get("source") or "ensembl",
        })
    if canonical.get("ensembl_id") and canonical["ensembl_id"] not in seen_ver:
        orig_versions.append({
            "id": canonical["ensembl_id"],
            "release": None,
            "source": "gene_basic",
        })

    return {
        "orig_names": orig_names,
        "std_names": std_names,
        "orig_versions": orig_versions,
        "std_versions": std_versions,
    }


def format_id_mapping_block(resolved: dict) -> str:
    """跨库 ID / 命名解析结构化回答。"""
    from gene_id_mapper import external_url

    query = str(resolved.get("query") or "").strip()
    if not resolved.get("resolved") and (
        is_biology_mechanism_question(query)
        or is_complaint_followup(query)
        or is_citation_display_question(query)
        or re.search(r"[\u4e00-\u9fff]{6,}", query)
    ):
        try:
            from zebrafish_prompt import answer_is_english

            en = bool(answer_is_english(query))
        except Exception:
            en = False
        if en:
            return (
                "This is not a gene symbol or database ID, so historical gene-version "
                "unification cannot run.\n"
                "ID mapping only accepts tp53 / ENSDARG… / ZDB-GENE… / NCBI Gene IDs. "
                "For mechanism, transcription, or duplication questions, use Hub full "
                "pipeline; literature will be searched and listed at the end as "
                "[n] Title. Year. ZFQA."
            )
        return (
            "这不是基因符号或数据库 ID，不能做「历史基因版本统一」。\n"
            "ID 映射只接受 tp53 / ENSDARG… / ZDB-GENE… / NCBI Gene 号。"
            "机制、转录、复制类问题请用 Hub 全链路，会检索文献并在文末列出 "
            "[n] 题名. 年份. ZFQA。"
        )

    try:
        from zebrafish_prompt import answer_is_english

        en = bool(answer_is_english(query))
    except Exception:
        en = False
    ref = resolved.get("reference") or {}
    if en:
        lines = [
            "[Historical gene-version unification]",
            f"  Query: {resolved.get('query', '?')}",
            f"  Reference genome: {ref.get('assembly')} (NCBI {ref.get('ncbi_assembly')}, taxon {ref.get('taxon_id')})",
            f"  Canonical name source: RefSeq {ref.get('ncbi_reference_refseq')} ({ref.get('ncbi_reference_assembly')})",
            f"  Datasets: https://www.ncbi.nlm.nih.gov/datasets/genome/{ref.get('ncbi_reference_refseq') or 'GCF_049306965.2'}/",
            f"  Local library anchor: {ref.get('assembly')} / Ensembl release {ref.get('ensembl_release')}",
        ]
    else:
        lines = [
            "【历史基因版本统一】",
            f"  查询输入：{resolved.get('query', '?')}",
            f"  参考基因组：{ref.get('assembly')}（NCBI {ref.get('ncbi_assembly')}，taxon {ref.get('taxon_id')}）",
            f"  标准名来源：RefSeq {ref.get('ncbi_reference_refseq')}（{ref.get('ncbi_reference_assembly')}）",
            f"  Datasets：https://www.ncbi.nlm.nih.gov/datasets/genome/{ref.get('ncbi_reference_refseq') or 'GCF_049306965.2'}/",
            f"  本地库锚点：{ref.get('assembly')} / Ensembl release {ref.get('ensembl_release')}",
        ]
    if not resolved.get("resolved"):
        for n in resolved.get("notes") or []:
            lines.append(f"  · {n}")
        return "\n".join(lines)

    groups = collect_gene_unification_groups(resolved)
    c = resolved.get("canonical") or {}

    if groups["orig_names"]:
        lines.extend(["", "[Original / historical symbols]" if en else "【原名 / 历史符号】"])
        for i, item in enumerate(groups["orig_names"], 1):
            if en:
                note = f" ({item['note']})" if item.get("note") else ""
            else:
                note = f"（{item['note']}）" if item.get("note") else ""
            lines.append(f"  {i}. {item['name']}{note}")

    lines.extend(["", "[Canonical name / version]" if en else "【标准名 / 标准版本】"])
    if groups["std_names"]:
        for item in groups["std_names"]:
            role = item.get("role") or ("symbol" if en else "符号")
            sep = ": " if en else "："
            lines.append(f"  · {role}{sep}{item['name']}")
    else:
        name = (c.get("gene_name") or resolved.get("query") or ("(none)" if en else "（无）"))
        if en:
            lines.append(
                f"  · No unique RefSeq GCF_049306965.2 annotation for this entry; "
                f"keeping local primary name: {name}"
            )
        else:
            lines.append(f"  · 本条 RefSeq GCF_049306965.2 无唯一注释，沿用本地主名：{name}")
    std = resolved.get("standard") or {}
    if std.get("mapped"):
        if en:
            lines.append(
                f"  · Standard assembly: {std.get('assembly') or ref.get('ncbi_reference_assembly')} "
                f"({std.get('refseq') or ref.get('ncbi_reference_refseq')})"
            )
            if std.get("ncbi_gene_id"):
                lines.append(f"  · NCBI Gene: {std.get('ncbi_gene_id')}")
            if std.get("chromosome"):
                lines.append(f"  · RefSeq chromosome: {std.get('chromosome')}")
        else:
            lines.append(
                f"  · 标准组装：{std.get('assembly') or ref.get('ncbi_reference_assembly')}（{std.get('refseq') or ref.get('ncbi_reference_refseq')}）"
            )
            if std.get("ncbi_gene_id"):
                lines.append(f"  · NCBI Gene：{std.get('ncbi_gene_id')}")
            if std.get("chromosome"):
                lines.append(f"  · RefSeq 染色体：{std.get('chromosome')}")
    if en:
        lines.extend([
            f"  · Local Ensembl ({ref.get('assembly')}): {c.get('ensembl_id') or '-'}",
            f"  · ZFIN: {c.get('zfin_id') or '-'}",
        ])
    else:
        lines.extend([
            f"  · 本地 Ensembl（{ref.get('assembly')}）：{c.get('ensembl_id') or '-'}",
            f"  · ZFIN：{c.get('zfin_id') or '-'}",
        ])

    if groups["orig_versions"]:
        lines.extend(["", "[Original Ensembl versions]" if en else "【原 Ensembl 版本】"])
        for i, item in enumerate(groups["orig_versions"][:12], 1):
            if en:
                rel = f", release {item['release']}" if item.get("release") else ""
            else:
                rel = f"，release {item['release']}" if item.get("release") else ""
            lines.append(f"  {i}. {item['id']}{rel}")

    chain_hdr = "[Cross-DB ID chain]" if en else "【跨库 ID 链】"
    for node in resolved.get("id_chain") or []:
        url = node.get("url") or external_url(node.get("type", ""), node.get("id", ""))
        if url:
            if chain_hdr not in "\n".join(lines):
                lines.extend(["", chain_hdr])
            lines.append(f"  · {node.get('label')}: {node.get('id')} → {url}")

    xrefs = resolved.get("xrefs") or []
    if xrefs:
        lines.append("")
        lines.append("[Cross-DB IDs]" if en else "【跨库 ID】")
        seen = set()
        for x in xrefs:
            key = (x.get("db_name"), x.get("db_id"))
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  · {x.get('db_name')}: {x.get('db_id')}")

    tissue = resolved.get("tissue_expression") or []
    if tissue:
        lines.append("")
        lines.append("[Bgee tissue expression (top)]" if en else "【Bgee 组织表达（Top）】")
        for t in tissue[:6]:
            lines.append(f"  · {t.get('tissue')}: {t.get('expression_level')}")

    if resolved.get("disambiguation"):
        lines.extend(["", f"  ⚠ {resolved['disambiguation']}"])

    for n in resolved.get("notes") or []:
        lines.append(f"  · {n}")

    return "\n".join(lines)


def answer_from_id_resolve(question: str, resolved: dict) -> str:
    return format_id_mapping_block(resolved)


def answer_from_gene_detail(question: str, detail: dict, reference: dict = None) -> str:
    """根据问题意图，从结构化基因数据生成准确回答（不依赖 LLM 编造）。"""
    b = detail["basic"]
    gene = b.get("gene_name", "?")
    intents = _intent(question)
    parts = []

    if "overview" in intents or "basic" in intents:
        parts.append(format_basic_block(b, reference, go_rows=detail.get("go")))

    if "basic" in intents and detail.get("id_mapping"):
        parts.append(format_id_mapping_block(detail["id_mapping"]))

    if "expression" in intents or ("overview" in intents and detail.get("expression")):
        prefer_tissue = any(k in question.lower() for k in ("组织", "成体", "tissue", "器官", "哪里表达"))
        parts.append(format_expression_block(
            gene, detail.get("expression") or [], prefer_tissue=prefer_tissue
        ))

    if "go" in intents or ("overview" in intents and detail.get("go")):
        parts.append(format_go_block(gene, detail.get("go") or []))

    if "kegg" in intents or ("overview" in intents and detail.get("kegg")):
        parts.append(format_kegg_block(gene, detail.get("kegg") or []))

    if "homology" in intents or ("overview" in intents and detail.get("homology")):
        parts.append(format_homology_block(gene, detail.get("homology") or []))

    if "reference" in intents or ("overview" in intents and detail.get("references")):
        parts.append(format_refs_block(gene, detail.get("references") or []))

    if not parts:
        parts.append(format_basic_block(b, go_rows=detail.get("go")))
        if detail.get("expression"):
            parts.append(format_expression_block(gene, detail["expression"]))

    return "\n\n".join(parts)


def is_reference_genome_question(question: str) -> bool:
    """参考基因组 / 组装版本 / GRCz 跨版本问题。"""
    q = (question or "").lower()
    if re.search(r"grcz\d+", q):
        return True
    if "参考基因组" in q or "基因组组装" in q or "组装版本" in q:
        return True
    if "锚定" in q and ("基因组" in q or "组装" in q or "grcz" in q):
        return True
    version_intent = (
        "版本", "哪些", "什么", "哪个", "有几个", "多少", "几个", "区别", "当前", "最新",
        "用的", "使用", "沿革", "历史", "混用", "坐标",
    )
    genome_topic = (
        "参考基因组", "基因组版本", "基因组组装", "组装版本", "grcz", "assembly",
        "ncbi assembly", "ensembl release", "zv11", "zv10", "zv9",
    )
    return any(k in q for k in genome_topic) and any(i in q for i in version_intent)


def is_gene_version_question(question: str) -> bool:
    """「基因/斑马鱼 + 版本/多少」类歧义问句（易与基因组组装混淆）。"""
    if is_biology_mechanism_question(question) or is_citation_display_question(question):
        return False
    q = (question or "").lower()
    if "版本" not in q and not re.search(r"(多少|几个).{0,6}(版本|版)", q):
        return False
    if is_reference_genome_question(question):
        return False
    if any(k in q for k in ("斑马鱼", "zebrafish", "danio", "基因", "ensembl", "zfin")):
        return True
    return bool(re.search(r"(多少|几个|有哪些).{0,8}版本", q))


def _gene_basic_count() -> int:
    try:
        from mysql_client import get_mysql_client
        res = get_mysql_client().execute_sql("SELECT COUNT(*) AS n FROM gene_basic")
        if res.get("success") and res.get("data"):
            return int(res["data"][0].get("n") or 0)
    except Exception:
        pass
    return 0


def _gene_biotype_counts() -> dict:
    """gene_function 存 Ensembl biotype（如 protein_coding · ensembl）。"""
    try:
        from mysql_client import get_mysql_client
        res = get_mysql_client().execute_sql(
            "SELECT LOWER(TRIM(gene_function)) AS bf, COUNT(*) AS n "
            "FROM gene_basic "
            "WHERE gene_function IS NOT NULL AND TRIM(gene_function) != '' "
            "AND gene_function REGEXP '^[a-z0-9_]+[[:space:]]*·[[:space:]]*(ensembl|havana|rfam|mirbase)' "
            "GROUP BY bf ORDER BY n DESC LIMIT 50"
        )
        if res.get("success") and res.get("data"):
            return {str(r.get("bf") or "unknown"): int(r.get("n") or 0) for r in res["data"]}
    except Exception:
        pass
    return {}


def _count_biotype(biotypes: dict, *keywords: str) -> int:
    total = 0
    for label, n in biotypes.items():
        base = label.split("·")[0].strip()
        if any(k in base for k in keywords):
            total += n
    return total


def is_factual_template_question(question: str) -> bool:
    """论文精准模式：直接返回结构化草稿、不经 API 改写的问句类型。"""
    return is_reference_genome_question(question) or is_gene_version_question(question)


def finalize_academic_structured_answer(text: str, *sources: str) -> str:
    from zebrafish_prompt import academic_provenance_footer, is_academic_mode
    body = (text or "").strip()
    if not body:
        return body
    if not is_academic_mode():
        return body
    footer = academic_provenance_footer(*sources)
    if footer and footer not in body:
        return body + footer
    return body


def answer_gene_version_question(question: str) -> str:
    """回答「基因有多少版本」等歧义问句：区分基因组组装 / 基因数量 / Ensembl ID 版本号。"""
    from gene_id_mapper import reference_genome_info
    from zebrafish_prompt import is_academic_mode

    ref = reference_genome_info()
    anchor = ref.get("assembly", "GRCz11")
    release = ref.get("ensembl_release", "116")
    ncbi = ref.get("ncbi_assembly", "GCF_000002035.6")
    taxon = ref.get("taxon_id", 7955)
    gene_n = _gene_basic_count()
    biotypes = _gene_biotype_counts()
    pc_n = _count_biotype(biotypes, "protein_coding")
    lnc_n = _count_biotype(biotypes, "lncrna", "lincrna")
    mir_n = _count_biotype(biotypes, "mirna", "microrna")

    q = (question or "").lower()
    asks_count = any(k in q for k in ("多少", "几个", "有几个", "多少种"))
    asks_genome = any(k in q for k in ("基因组", "组装", "grcz", "assembly", "参考基因组"))
    std_asm = ref.get("ncbi_reference_assembly", "GRCz12tu")
    std_refseq = ref.get("ncbi_reference_refseq", "GCF_049306965.2")

    if is_academic_mode():
        direct = []
        if asks_genome or (not asks_count and "版本" in q):
            direct.append(
                f"NCBI 现行参考组装为 {std_asm}（{std_refseq}）；"
                f"本地基因库锚定 {anchor}（NCBI {ncbi}，taxon {taxon}，Ensembl {release}）"
            )
        if asks_count or (not asks_genome and "版本" in q):
            pc_line = f"，其中蛋白编码基因 {pc_n} 条" if pc_n else ""
            direct.append(
                f"本地基因库共 {gene_n} 条基因注释{pc_line}（锚定 {anchor}）[本地库]"
            )
        if not direct:
            direct.append(
                f"标准名/BLAST/cDNA={std_asm}；基因注释 {gene_n} 条，锚定 {anchor}"
            )

        lines = [
            "【直接结论】" + "；".join(direct),
            "",
            "【概念界定】「版本」在斑马鱼领域常指以下三类，不可混用：",
            "",
            f"1. 参考基因组组装版本 — NCBI 现行参考 {std_asm}（{std_refseq}）；"
            f"本地基因库仍锚定 {anchor}（NCBI {ncbi}，taxon {taxon}，Ensembl release {release}）。"
            " 历史组装含 GRCz10（Zv10）、Zv9/Zv8 等 [Ensembl/NCBI 沿革]。",
            "",
            f"2. 基因注释条目数 — 非「组装有几个」，而是 {anchor} 注释下的基因记录数。",
            f"  · 本地基因库总计：**{gene_n}** 条 [本地库]",
        ]
        if pc_n:
            lines.append(
                f"  · 蛋白编码基因：**{pc_n}** 条（Ensembl/HAVANA 注释）[本地库]"
            )
        if lnc_n:
            lines.append(f"  · lncRNA 类：**{lnc_n}** 条 [本地库]")
        if mir_n:
            lines.append(f"  · miRNA 类：**{mir_n}** 条 [本地库]")
        if not pc_n:
            lines.append(
                "  · 蛋白编码基因细分计数：本地库未完整标注基因类型，"
                "不得以文献「约 2.6 万」替代本地库实测总数 [本地库] "
            )
        else:
            annotated = sum(biotypes.values())
            if annotated < gene_n:
                lines.append(
                    f"  · 注：本地基因库共 {gene_n} 条，其中 {annotated} 条带 Ensembl/HAVANA 基因类型标签；"
                    f"其余为功能描述文本或混合注释 [本地库]"
                )
        lines.extend([
            "",
            "3. Ensembl 基因 ID 后缀（如 ENSDARG….5）— 表示同一 ID 在后续注释中的修订次数，"
            "不是斑马鱼物种或组装版本数；本库存 Ensembl r116 快照 ID（通常无 .后缀）[Ensembl 规范]。",
            "",
            "【Methods 引用示例】",
            f"Gene annotations were queried from the local zebrafish annotation database (n={gene_n}, "
            f"assembly={anchor}, Ensembl release {release}).",
        ])
        return "\n".join(lines)

    lines = [
        "【斑马鱼「版本」说明】",
        "",
        "这个问题在文献里常指三件事，答案不同：",
        "",
        f"① 参考基因组「组装」有几个版本？（{std_asm} / GRCz11 / GRCz10 等）",
        f"  · NCBI 现行参考：**{std_asm}**（{std_refseq}）— 本系统标准名、BLAST、cDNA 锚定于此。",
        f"  · 本地基因库仍锚定 **{anchor}**（Ensembl {release}，NCBI {ncbi}）的 ID/坐标/表达。",
        "  · 历史上还有 GRCz10（Zv10）、Zv9/Zv8；旧文献可能仍写 GRCz10。",
        "  · 另有个别品系组装（如 AB 品系 DrAB1），不等同于上述参考。",
        "",
        f"② 「有多少个基因」？（注释里的基因条目数，不是组装版本数）",
        f"  · 本地基因库：**{gene_n}** 个基因（锚定 {anchor}，Ensembl release {release}）。",
    ]
    if pc_n:
        lines.append(f"  · 其中蛋白编码基因：**{pc_n}** 个。")
    lines.extend([
        "  · 与教科书常说的「约 2.6 万蛋白编码基因」同量级；具体数目随注释版本略有增减。",
        "",
        "③ Ensembl 基因 ID 后的「.数字」版本号？（如 ENSDARG….5）",
        "  · 表示同一 Ensembl 基因 ID 在后续注释中的修订次数，不是「有几种斑马鱼」。",
        "  · 本库存的是 r116 快照 ID（通常无 .后缀）；换组装或升级 Ensembl 后后缀会变。",
        "",
        f"本系统口径：标准名/BLAST/cDNA={std_asm}；基因注释锚定 {anchor}（NCBI {ncbi}，物种分类号 {taxon}）。",
    ])
    if asks_count and "基因组" not in q and "组装" not in q and "grcz" not in q:
        pc_hint = f"（protein_coding {pc_n}）" if pc_n else ""
        lines.insert(
            2,
            "【直接回答】您问「基因有多少版本」时，多数情况下是在问②："
            f"当前 {anchor} 注释下本地基因库共 **{gene_n}** 条{pc_hint}；"
            f"若指基因组组装，NCBI 现行参考是 {std_asm}，本库坐标层仍是 {anchor}。",
        )
        lines.insert(3, "")
    return "\n".join(lines)


def answer_reference_genome_versions() -> str:
    """参考基因组版本——结构化事实，不依赖 RAG/LLM。"""
    from gene_id_mapper import reference_genome_info

    ref = reference_genome_info()
    anchor = ref.get("assembly", "GRCz11")
    ncbi = ref.get("ncbi_assembly", "GCF_000002035.6")
    release = ref.get("ensembl_release", "116")
    taxon = ref.get("taxon_id", 7955)
    std_asm = ref.get("ncbi_reference_assembly", "GRCz12tu")
    std_refseq = ref.get("ncbi_reference_refseq", "GCF_049306965.2")
    ncbi_url = (
        ref.get("ncbi_datasets_url")
        or f"https://www.ncbi.nlm.nih.gov/datasets/genome/{std_refseq}/"
    )
    gene_n = _gene_basic_count()

    return "\n".join([
        f"【直接结论】NCBI 现行参考组装为 **{std_asm}**（RefSeq {std_refseq}）。"
        "本系统对外标准名、BLAST 核酸库、cDNA 提取均锚定于此。"
        f"本地基因库（Ensembl ID、染色体坐标、表达、GO/KEGG）仍为 **{anchor}**"
        f"（Ensembl release {release}，{ncbi}，taxon {taxon}，约 {gene_n} 条）。",
        "",
        "【组装沿革】",
        f"  1. {std_asm} — NCBI Datasets 现行参考（{std_refseq}）；本系统标准名 / BLAST / cDNA。",
        f"  2. {anchor}（Zv11）— Ensembl 长期默认；本地基因库锚定 [本地库]。",
        "  3. GRCz10（Zv10）— 上一代参考组装，部分旧文献仍引用。",
        "  4. Zv9 / Zv8 及更早 — 早期 Sanger 组装，新研究较少使用。",
        "",
        "【使用注意】",
        f"  · {anchor} 与 {std_asm}（以及 GRCz10）的基因组坐标、部分 Ensembl ID 不可直接混用；跨版本必须先做 ID/坐标映射（gene_assembly_xref）。",
        "  · Ensembl ID 可带版本后缀（如 ENSDARG0000000001.5），换组装后部分 ID 会变化。",
        f"  · 品系差异：参考基因组多基于 Tuebingen（TU）；AB 品系另有 DrAB1 等补充组装，≠ {anchor}。",
        "",
        f"【官方入口】NCBI Datasets → {ncbi_url}",
        "维护方：Wellcome Sanger Institute + ZFIN 社区（Ensembl Danio rerio）。",
    ])


def is_expression_question(question: str) -> bool:
    q = question.lower()
    return any(k in q for k in ("表达", "组织", "fpkm", "tpm", "转录水平", "哪个时期", "哪些时期"))


_MECHANISM_PHRASES = (
    "转录机制", "翻译机制", "复制机制", "剪接机制", "调控机制",
    "表达调控", "表观调控", "作用机制", "分子机制", "启动机制",
)
_MECHANISM_TOPICS = (
    "转录", "翻译", "复制", "剪接", "重组", "修复", "表观", "甲基化",
    "乙酰化", "染色质", "增强子", "启动子", "zga", "mzt", "合子基因组",
    "母源", "调控网络", "基因组激活",
)
_MECHANISM_HINTS = ("机制", "原理", "过程", "途径")


def is_biology_mechanism_question(question: str) -> bool:
    """分子过程/机制题：必须走文献 RAG，禁止当成基因符号或 ID 映射。"""
    q = _strip_dialog_context(question or "").strip().lower()
    if not q:
        return False
    if any(k in q for k in ("叫什么", "官方符号", "id转换", "id 转换", "对应哪个ensembl")):
        return False
    if any(p in q for p in _MECHANISM_PHRASES):
        return True
    if any(h in q for h in _MECHANISM_HINTS) and (
        any(t in q for t in _MECHANISM_TOPICS) or ("基因" in q and "版本" not in q)
    ):
        return True
    return False


def is_standalone_science_question(question: str) -> bool:
    """完整科学问句（即使很短）应开新话题，不要粘上「为什么不显示文献」之类追问。"""
    q = _strip_dialog_context(question or "").strip()
    if is_biology_mechanism_question(q):
        return True
    if is_citation_display_question(q):
        return False
    if len(q) >= 6 and "斑马鱼" in q and any(
        k in q for k in ("基因", "转录", "复制", "表达", "发育", "突变", "染色体", "通路")
    ):
        if any(k in q for k in ("为什么不", "不显示", "引用文献", "参考文献呢")):
            return False
        return True
    return False


def is_citation_display_question(question: str) -> bool:
    """用户在问「正文 [n] 为什么没有文末文献表」，不是新的科学题。"""
    q = _strip_dialog_context(question or "")
    if re.search(
        r"(不显示|没显示|没有显示|看不到|看不见|没出来|没出现|"
        r"怎么没有|为何没有|为什么没有|为什么不|为啥不).{0,16}"
        r"(引用|文献|参考文献|脚注)",
        q,
    ):
        return True
    if re.search(
        r"(引用文献|参考文献).{0,10}(呢|去哪|在哪|怎么没|为何没|为什么没)",
        q,
    ):
        return True
    if re.search(r"你为什么不(显示|给|列).{0,8}(引用|文献)", q):
        return True
    return False


def is_id_mapping_question(question: str) -> bool:
    if (
        is_biology_mechanism_question(question)
        or is_citation_display_question(question)
        or is_complaint_followup(question)
    ):
        return False
    q = _strip_dialog_context(question or "").lower()
    strong = (
        "id转换", "id 转换", "符号转换", "官方符号", "canonical", "命名混乱",
        "ensembl id", "zfin id", "gene id", "ncbi gene", "对应哪个", "跨库",
    )
    if any(k in q for k in strong):
        return True
    if any(k in q for k in ("别名", "叫什么", "基因名")) and any(
        k in q for k in ("对应", "官方", "标准名", "ensembl", "zfin", "ncbi")
    ):
        return True
    if any(k in q for k in ("grcz10", "grcz11", "grcz12")) and any(
        k in q for k in ("id", "符号", "名称", "映射", "对应")
    ):
        return True
    return False


def _strip_dialog_context(question: str) -> str:
    """剥离 Hub 多轮上下文注入的辅助块（effective_question 中的
    「关于基因 xxx：」前缀与「（对话上下文：…）」块），
    避免历史基因符号被误当作本次比较/查询的基因。"""
    q = question or ""
    q = re.sub(r"（对话上下文：.*?）", "", q, flags=re.S)
    q = re.sub(r"^关于基因\s*[^：:]+[：:]\s*", "", q, flags=re.I)
    return q.strip()


_FOLLOWUP_HINTS = (
    "为什么", "为何", "之前", "刚才", "上面", "上文",
    "意思", "解释", "那", "回复", "回答", "不是说", "不是",
    "参考文献", "一篇", "几篇", "只有", "脚注", "引用", "文献吗", "哪篇",
)


def is_complaint_followup(question: str) -> bool:
    """「你这是啥回答啊」类抱怨：解释上一答，禁止当基因符号做 ID 映射。"""
    q = _strip_dialog_context(question or "")
    if not q:
        return False
    if is_biology_mechanism_question(q):
        return False
    keys = (
        "啥回答", "什么回答", "这是啥", "这是什么", "答非所问", "乱回答",
        "不对吧", "你在说什么", "为什么是这个", "这回答", "错了吧",
        "牛头不对", "风马牛",
    )
    return any(k in q for k in keys)


def is_explanatory_followup(question: str) -> bool:
    """判断是否为「追问/指代」类问题（剥离上下文后无新基因目标）。

    此类问题应交由大模型结合上下文解释，而不是被本地基因路由
    （比较 / ID 映射 / 基因详情）当作新基因查询，造成答非所问。
    """
    clean = _strip_dialog_context(question)
    if not clean:
        return False
    if is_biology_mechanism_question(clean) or is_standalone_science_question(clean):
        return False
    if is_complaint_followup(clean):
        return True
    from gene_toolkit import extract_genes_from_question
    if extract_genes_from_question(clean, limit=4):
        return False
    return any(k in clean for k in _FOLLOWUP_HINTS)


def is_gene_comparison_question(question: str) -> bool:
    clean = _strip_dialog_context(question)
    q = clean.lower()
    if not any(
        k in q
        for k in (
            "同一个", "同一基因", "一样吗", "是否相同", "相同吗", "是不是",
            "区别", "不同", "旁系", "同源", "比较", "对比", "vs", "和",
        )
    ):
        return False
    from gene_toolkit import extract_genes_from_question
    return len(extract_genes_from_question(clean, limit=4)) >= 2


def answer_gene_comparison(question: str) -> str:
    """基于本地库比较多个基因符号/ID（不依赖 LLM 编造）。

    输出结构：结论 → 判断依据（别名关系 / 同一记录 ID）→ 精简基因信息。
    结论与依据优先，避免「各基因详情/基本信息」这类冗余标题。
    """
    from gene_toolkit import extract_genes_from_question, get_gene_detail
    from gene_id_mapper import resolve_gene_identifier, reference_genome_info, KNOWN_ALIASES

    # 剥离 Hub 多轮上下文辅助块，只基于用户当前问句提取基因
    question = _strip_dialog_context(question)

    tokens = extract_genes_from_question(question, limit=4)
    if len(tokens) < 2:
        return "[提示] 未识别到两个及以上基因符号，请明确写出，如：shh 和 shha"

    ref = reference_genome_info()
    entries = []
    for tok in tokens:
        r = resolve_gene_identifier(tok)
        c = r.get("canonical") or {}
        entries.append({"query": tok, "resolved": r, "canonical": c})

    resolved_ok = [e for e in entries if e["canonical"].get("gene_id")]
    if len(resolved_ok) < 1:
        return "[错误] 本地库里没有找到匹配的基因，请确认基因符号是否正确（也支持 Ensembl / ZFIN / NCBI 编号）"

    gene_ids = {e["canonical"]["gene_id"] for e in resolved_ok}
    names = [e["canonical"].get("gene_name") or e["query"] for e in resolved_ok]

    lines = [f"【基因比较】（锚定 {ref.get('assembly', 'GRCz11')}）", ""]

    if len(gene_ids) == 1:
        official = (
            (resolved_ok[0]["resolved"].get("standard") or {}).get("gene_symbol")
            or resolved_ok[0]["canonical"].get("gene_name")
            or "?"
        )
        queries = "、".join(f"`{e['query']}`" for e in resolved_ok)
        lines.append(f"结论：**{queries} 是同一个基因**，官方符号为 **`{official}`**。")
        lines.append("")
        lines.append("判断依据：")
        for e in resolved_ok:
            q = e["query"]
            if q.lower() == official.lower():
                continue
            note = KNOWN_ALIASES.get(q.lower())
            if note and note.lower() == official.lower():
                lines.append(f"- `{q}` 是 `{official}` 的官方别名（文献/口语中常用）")
            else:
                lines.append(f"- `{q}` 经 ID 解析归一到基因 `{official}`")
        c = resolved_ok[0]["canonical"]
        ident = (
            f"Ensembl `{c.get('ensembl_id') or '-'}`"
            f" · ZFIN `{c.get('zfin_id') or '-'}`"
            f" · Chr `{c.get('chromosome') or '-'}`"
        )
        lines.append(f"- 两者命中同一条基因记录：{ident}")
        lines.append("")
        lines.append(f"基因信息（`{official}`）：")
        detail = get_gene_detail(official)
        if detail:
            b = detail["basic"]
            info = [
                f"  染色体：{b.get('chromosome') or '-'}",
                f"  Ensembl：{b.get('ensembl_id') or '-'}",
                f"  ZFIN：{b.get('zfin_id') or '-'}",
            ]
            if b.get("expression_summary"):
                info.append(f"  表达：{b['expression_summary']}")
            summary = _function_summary(b.get("gene_function") or "", detail.get("go"))
            if summary:
                info.append(f"  功能：{summary}")
            lines.extend(info)
    else:
        queries = "、".join(f"`{e['query']}`" for e in resolved_ok)
        lines.append(f"结论：**{queries} 不是同一基因**，为不同的基因记录（可能为旁系同源）。")
        lines.append("")
        lines.append("判断依据（各自解析到的官方记录）：")
        for e in resolved_ok:
            c = e["canonical"]
            gname = c.get("gene_name") or "?"
            ens = c.get("ensembl_id") or "-"
            chrom = c.get("chromosome") or "-"
            lines.append(
                f"- `{e['query']}` → **{gname}** | Ensembl `{ens}` | Chr `{chrom}`"
            )
        # 补充 shhb 提示（shh/shha 常见混淆）
        low_names = {n.lower() for n in names}
        if low_names & {"shh", "shha", "shhb"}:
            lines.append("")
            lines.append(
                "提示：斑马鱼 Sonic hedgehog 旁系同源基因为 **shha** 与 **shhb**；"
                "文献中 `shh` 常指 **shha**，但与 **shhb** 不是同一基因。"
            )

    return "\n".join(lines)
