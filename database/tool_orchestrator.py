# -*- coding: utf-8 -*-
"""根据用户问题自动检测并调用分析工具（并行、尽可能多）。"""
import bootstrap  # noqa: F401

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set

from common_utils import logger

_DNA_SEQ = re.compile(r"[ATCGNatcgn]{20,}")
_ENRICH_KW = ("富集", "go富集", "kegg富集", "ora", "enrichment", "over-representation")
_KEGG_KW = ("kegg", "通路", "pathway", "信号通路", "代谢通路")
_ID_KW = (
    "id转换", "id 转换", "符号转换", "ensembl id", "zfin id", "refseq", "ncbi",
    "对应哪个", "官方符号", "映射", "跨库", "entrez",
)
_SEQ_KW = ("序列", "cdna", "cdna", "启动子", "promoter", "fasta", "提取序列", "碱基")
_ANNOT_KW = ("注释", "annotat", "annotation", "go注释", "功能注释")


def _extract_sequence(question: str) -> Optional[str]:
    from seq_toolkit import normalize_sequence, validate_sequence

    m = _DNA_SEQ.search(question.replace(" ", "").replace("\n", ""))
    if not m:
        m = re.search(r"[ATCGNatcgn]{20,}", question)
    if not m:
        return None
    ok, seq = validate_sequence(m.group(0))
    return seq if ok else None


def _extract_genes(question: str, limit: int = 8) -> List[str]:
    from gene_toolkit import extract_genes_from_question

    return extract_genes_from_question(question, limit=limit)


def detect_auto_tools(question: str, primary_task_type: str = "") -> List[str]:
    """返回待调用的工具名：gene, id_resolve, enrichment, kegg, blast。

    检测到基因时尽可能并行调用 gene / id_resolve / enrichment / kegg（单基因也跑富集 ORA）。
    """
    q = (question or "").lower()
    tools: List[str] = []
    genes = _extract_genes(question)
    seq = _extract_sequence(question)

    if seq:
        tools.append("blast")

    if genes:
        # 基础工具：gene（基因详情）+ id_resolve（ID 解析），这两个是轻量数据库查询。
        # 之前这里无条件 extend enrichment / kegg / annotator，任何单基因问题（如
        # 「提取 mstn 坐标」「tp53 的功能」）都会触发 GO/KEGG ORA 富集 + 一键注释 + 序列提取，
        # 一堆重计算并行跑，是 chat 慢的主要来源。改为按关键词条件触发。
        tools.extend(["gene", "id_resolve"])
        # 只在明确要序列时才提取序列。之前 `or len(genes) == 1` 会让任何单基因问题
        # 都触发 sequence 工具，去本地 FASTA 里查 CDS/启动子序列 —— 重计算（CPU 拉满）。
        if any(k in q for k in _SEQ_KW):
            tools.append("sequence")
        if any(k in q for k in _ENRICH_KW):
            tools.append("enrichment")
        if any(k in q for k in _KEGG_KW):
            tools.append("kegg")
        if any(k in q for k in _ANNOT_KW):
            tools.append("annotator")
    else:
        id_like = (
            any(k in q for k in _ID_KW)
            or re.search(r"ZDB-GENE-\d+-\d+", question, re.I)
            or re.search(r"ENS[A-Z]+\d+", question, re.I)
            or re.search(r"(?:NM|NR|NP|XM|XR|NC)_\d+", question, re.I)
            or re.fullmatch(r"\s*\d{4,9}\s*", question.strip())
        )
        if id_like:
            tools.append("id_resolve")

    if primary_task_type == "序列比对" and "blast" in tools:
        tools.remove("blast")
    if primary_task_type == "功能富集" and "enrichment" in tools:
        tools.remove("enrichment")

    seen: Set[str] = set()
    ordered: List[str] = []
    for t in tools:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def _run_gene(gene: str) -> dict:
    from gene_toolkit import get_gene_detail, normalize_gene_name

    name = normalize_gene_name(gene)
    detail = get_gene_detail(name)
    if not detail:
        return {"success": False, "gene": gene, "error": "未找到"}
    return {"success": True, "gene": name, "detail": detail}


def _run_id_resolve(token: str) -> dict:
    from gene_id_mapper import resolve_gene_identifier

    return resolve_gene_identifier(token)


def _run_enrichment(genes: List[str]) -> dict:
    from enrichment_toolkit import (
        go_lookup, kegg_lookup, format_enrichment_answer, format_annotation_answer,
    )

    # 2026-11-07：与工具页统一口径 —— 单个基因给注释，多个基因给富集
    go_res = go_lookup(genes)
    kegg_res = kegg_lookup(genes)
    ann = next(
        (r for r in (go_res, kegg_res) if isinstance(r, dict) and r.get("mode") == "annotation"),
        None,
    )
    return {
        "go": go_res,
        "kegg": kegg_res,
        "mode": "annotation" if ann else "enrichment",
        "summary": format_annotation_answer(ann) if ann else format_enrichment_answer(go_res, kegg_res),
    }


def _run_kegg(question: str, genes: List[str]) -> dict:
    from enrichment_toolkit import kegg_lookup, kegg_pathway_search

    # 2026-11-07：有基因就按基因查（1 个给通路注释、多个给通路富集），与工具页同一口径。
    # 以前把问题里的泛词「通路」当成关键词去过滤英文通路名，几乎必然过滤成空结果。
    if genes:
        return kegg_lookup(genes)
    kw = ""
    for k in _KEGG_KW:
        if k in question.lower() and len(k) >= 4:
            kw = k
            break
    return kegg_pathway_search(kw, None)


def _run_blast(seq: str) -> dict:
    from seq_toolkit import run_local_blast, format_blast_answer, blast_tsv_export

    result = run_local_blast(seq, top_k=10, evalue=10.0)
    return {
        "result": result,
        "summary": format_blast_answer(result),
        "tsv": blast_tsv_export(result),
    }


def _run_sequence(gene: str, question: str) -> dict:
    from sequence_toolkit import extract_sequences, format_sequence_answer

    kinds = ["cdna", "promoter"]
    if "基因组" in question or "genomic" in question.lower():
        kinds.append("genomic")
    data = extract_sequences(gene, kinds=kinds)
    data["summary"] = format_sequence_answer(data)
    return data


def _run_annotator(gene: str) -> dict:
    from gene_annotator_toolkit import annotate_gene, format_annotator_answer

    data = annotate_gene(gene)
    if data.get("success"):
        data["summary"] = format_annotator_answer(data)
    return data


def _tool_label(name: str) -> str:
    return {
        "gene": "基因详情",
        "id_resolve": "ID 解析",
        "enrichment": "GO/KEGG 富集",
        "kegg": "KEGG 通路",
        "blast": "BLAST 比对",
        "sequence": "序列/启动子",
        "annotator": "基因注释",
    }.get(name, name)


def run_auto_tools(
    question: str,
    primary_task_type: str = "",
    existing: Optional[dict] = None,
) -> Dict[str, Any]:
    """并行执行检测到的工具，返回 tool_results / tools_invoked。"""
    tools = detect_auto_tools(question, primary_task_type)
    if not tools:
        return {"tools_invoked": [], "tool_results": {}}

    genes = _extract_genes(question)
    seq = _extract_sequence(question)
    existing = existing or {}
    results: Dict[str, Any] = {}
    errors: Dict[str, str] = {}

    def _cached_gene_detail(gene: str) -> Optional[dict]:
        gd = existing.get("gene_detail")
        if not gd:
            return None
        from gene_toolkit import normalize_gene_name

        bname = (gd.get("basic") or {}).get("gene_name", "")
        if bname and normalize_gene_name(bname).lower() == normalize_gene_name(gene).lower():
            return gd
        return None

    def _dispatch(name: str):
        try:
            if name == "gene" and genes:
                cached = _cached_gene_detail(genes[0])
                if cached:
                    bname = (cached.get("basic") or {}).get("gene_name", genes[0])
                    return name, {"success": True, "gene": bname, "detail": cached, "cached": True}
                return name, _run_gene(genes[0])
            if name == "id_resolve":
                token = genes[0] if genes else question.strip().split()[0]
                for pat in (
                    r"ZDB-GENE-\d+-\d+",
                    r"ENS[A-Z]+\d+(?:\.\d+)?",
                    r"(?:NM|NR|NP|XM|XR|NC)_\d+(?:\.\d+)?",
                    r"\b\d{4,9}\b",
                ):
                    m = re.search(pat, question, re.I)
                    if m:
                        token = m.group(0)
                        break
                return name, _run_id_resolve(token)
            if name == "enrichment" and genes:
                return name, _run_enrichment(genes)
            if name == "kegg":
                return name, _run_kegg(question, genes)
            if name == "blast" and seq:
                return name, _run_blast(seq)
            if name == "sequence" and genes:
                return name, _run_sequence(genes[0], question)
            if name == "annotator" and genes:
                return name, _run_annotator(genes[0])
            return name, {"skipped": True}
        except Exception as e:
            logger.warning(f"[自动工具] {name} 失败：{e}")
            return name, {"success": False, "error": str(e)}

    with ThreadPoolExecutor(max_workers=min(7, len(tools))) as pool:
        futs = {pool.submit(_dispatch, t): t for t in tools}
        for fut in as_completed(futs):
            name, payload = fut.result()
            if payload.get("skipped"):
                continue
            if payload.get("success") is False and payload.get("error"):
                errors[name] = payload["error"]
            results[name] = payload

    invoked = [_tool_label(t) for t in tools if t in results]
    return {
        "tools_invoked": invoked,
        "tool_results": results,
        "tool_errors": errors,
    }


def merge_tool_results_into_answer(answer: str, auto: Dict[str, Any]) -> str:
    # 回答正文只保留「结论 → 原因分析 → 总结」，不再追加
    # 「【自动分析】已联动右侧工具…」这类工具调用痕迹。
    # 自动工具的调用结果仍通过 result["tools_invoked"] / result["tool_results"]
    # 交给前端侧边栏「分析工具」面板展示，不影响可追溯性。
    return answer or ""


def augment_chat_result(question: str, result: dict) -> dict:
    """在问答结果上附加自动工具调用结果，并合并 gene_detail / enrichment 等字段。"""
    if result.get("error") and not result.get("answer"):
        return result

    primary = result.get("task_type", "")
    auto = run_auto_tools(question, primary, existing=result)
    result["tools_invoked"] = auto.get("tools_invoked", [])
    result["tool_results"] = auto.get("tool_results", {})

    tr = result["tool_results"]
    if tr.get("gene", {}).get("detail") and not result.get("gene_detail"):
        result["gene_detail"] = tr["gene"]["detail"]
    if tr.get("enrichment") and not result.get("enrichment"):
        result["enrichment"] = tr["enrichment"]
    if tr.get("blast", {}).get("result") and not result.get("blast"):
        result["blast"] = tr["blast"]["result"]
    if tr.get("id_resolve") and not result.get("id_mapping"):
        result["id_mapping"] = tr["id_resolve"]

    result["answer"] = merge_tool_results_into_answer(result.get("answer", ""), auto)
    return result
