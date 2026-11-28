# -*- coding: utf-8 -*-
"""基因注释器：整合 GO / 表达 / 同源 / 跨库 ID（Potato Hub 风格一键注释）。"""
import bootstrap  # noqa: F401

from typing import Any, Dict, List


def annotate_gene(gene: str) -> Dict[str, Any]:
    from gene_toolkit import get_gene_detail, normalize_gene_name
    from gene_id_mapper import resolve_gene_identifier
    from enrichment_toolkit import go_enrichment

    name = normalize_gene_name(gene)
    detail = get_gene_detail(name)
    id_map = resolve_gene_identifier(name)
    if not detail and not id_map.get("resolved"):
        return {"success": False, "gene": gene, "error": f"未找到基因 {name}"}

    b = (detail or {}).get("basic") or {}
    canon = id_map.get("canonical") or {}
    gene_name = b.get("gene_name") or canon.get("gene_name") or name

    go_rows: List[dict] = (detail or {}).get("go") or []
    expr_rows: List[dict] = (detail or {}).get("expression") or []
    homo_rows: List[dict] = (detail or {}).get("homology") or []
    refs: List[dict] = (detail or {}).get("references") or []

    ora = go_enrichment([gene_name], top_n=8, min_gene_count=1) if gene_name else {"terms": []}

    top_tissues = sorted(
        expr_rows,
        key=lambda r: float(r.get("expression_level") or 0),
        reverse=True,
    )[:8]

    std = (id_map.get("standard") or {})
    std_name = (std.get("gene_symbol") or "").strip() if std.get("mapped") else ""
    summary_lines = [
        f"标准名（RefSeq GCF_049306965.2）：{std_name or (gene_name + '（本条 RefSeq 无唯一注释，沿用本地主名）')}",
        f"本地原名：{gene_name}",
        f"Ensembl：{b.get('ensembl_id') or canon.get('ensembl_id') or '-'}",
        f"ZFIN：{b.get('zfin_id') or canon.get('zfin_id') or '-'}",
        f"染色体：{b.get('chromosome') or canon.get('chromosome') or '-'}",
        f"GO 注释（库内）：{len(go_rows)} 条",
        f"组织表达记录：{len(expr_rows)} 条",
        f"同源基因：{len(homo_rows)} 条",
        f"关联文献：{len(refs)} 条",
    ]
    if top_tissues:
        summary_lines.append(
            "Top 组织表达：" + ", ".join(
                f"{t.get('tissue')}({float(t.get('expression_level') or 0):.0f})"
                for t in top_tissues[:5]
            )
        )

    return {
        "success": True,
        "gene": gene_name,
        "detail": detail,
        "id_mapping": id_map,
        "go_annotations": go_rows[:15],
        "go_ora_context": (ora.get("terms") or [])[:5],
        "expression_top": top_tissues,
        "homology": homo_rows[:8],
        "references": refs[:10],
        "summary": "\n".join(summary_lines),
    }


def _summary_en(data: Dict[str, Any]) -> str:
    """英文版概览（与中文 summary 同源字段，避免中英混排）。"""
    detail = data.get("detail") or {}
    b = detail.get("basic") or {}
    mapping = data.get("id_mapping") or {}
    canon = mapping.get("canonical") or {}
    std = mapping.get("standard") or {}
    std_name = (std.get("gene_symbol") or "").strip() if std.get("mapped") else ""
    gene_name = data.get("gene") or b.get("gene_name") or "?"
    lines = [
        "Canonical name (RefSeq GCF_049306965.2): "
        + (std_name or f"{gene_name} (no unique RefSeq annotation; local primary name kept)"),
        f"Local primary name: {gene_name}",
        f"Ensembl: {b.get('ensembl_id') or canon.get('ensembl_id') or '-'}",
        f"ZFIN: {b.get('zfin_id') or canon.get('zfin_id') or '-'}",
        f"Chromosome: {b.get('chromosome') or canon.get('chromosome') or '-'}",
        f"GO annotations (local): {len(detail.get('go') or [])}",
        f"Tissue expression records: {len(detail.get('expression') or [])}",
        f"Orthologues: {len(detail.get('homology') or [])}",
        f"Linked publications: {len(detail.get('references') or [])}",
    ]
    top = data.get("expression_top") or []
    if top:
        lines.append(
            "Top tissue expression: " + ", ".join(
                f"{t.get('tissue')}({float(t.get('expression_level') or 0):.0f})"
                for t in top[:5]
            )
        )
    return "\n".join(lines)


def format_annotator_answer(data: dict, lang: str = "zh") -> str:
    en = (lang or "zh").lower().startswith("en")
    if not data.get("success"):
        return data.get("error") or ("Annotation failed" if en else "注释失败")
    lines = [
        "[Gene annotation report]" if en else "【基因注释报告】",
        _summary_en(data) if en else data.get("summary", ""),
    ]
    go = data.get("go_annotations") or []
    if go:
        lines.append("\nGO (top in local library):" if en else "\nGO（库内 Top）：")
        for g in go[:8]:
            lines.append(f"  · [{g.get('go_category') or '?'}] {g.get('go_term', '')[:70]}")
    refs = data.get("references") or []
    if refs:
        lines.append("\nPublications (top):" if en else "\n文献（Top）：")
        for r in refs[:5]:
            pm = r.get("pubmed_id") or "?"
            lines.append(f"  · PMID {pm}: {(r.get('title') or '')[:60]}")
    return "\n".join(lines)
