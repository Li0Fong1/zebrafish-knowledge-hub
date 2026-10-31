# -*- coding: utf-8 -*-
"""工作流预置模板：与前端 SCOPE_TO_PRESET / PRESET_TO_SCOPE 对齐。"""
from __future__ import annotations

PRESET_BY_SCOPE = {
    "literature": "literature_only",
    "gene_db": "gene_enrichment",
    "id_map": "id_only",
    "hub": "full_hub",
    "retrieval_debug": "retrieval_debug",
}

SCOPE_BY_PRESET = {v: k for k, v in PRESET_BY_SCOPE.items()}
# 兼容旧预置名
SCOPE_BY_PRESET.setdefault("literature_rag", "literature")
SCOPE_BY_PRESET.setdefault("gene_db_only", "gene_db")

PRESETS = [
    {
        "slug": "full_hub",
        "name": "Hub 全链路",
        "description": "ID + 基因 + 文献 + 富集并行编排",
        "definition": {
            "nodes": [
                {"id": "in", "type": "input", "label": "输入", "depends": []},
                {"id": "id", "type": "id_resolve", "label": "ID 映射", "depends": ["in"]},
                {"id": "gene", "type": "gene_detail", "label": "基因详情", "depends": ["in"]},
                {"id": "lit", "type": "literature_rag", "label": "文献 RAG", "depends": ["in"]},
                {"id": "enrich", "type": "enrichment", "label": "GO/KEGG 富集", "depends": ["in"]},
                {
                    "id": "out",
                    "type": "synthesizer",
                    "label": "综合报告",
                    "depends": ["id", "gene", "lit", "enrich"],
                },
            ]
        },
    },
    {
        "slug": "literature_only",
        "name": "仅文献 RAG",
        "description": "PubmedYes FAISS → 重排 → 文献合成（不跑基因/富集）",
        "definition": {
            "nodes": [
                {"id": "in", "type": "input", "label": "输入", "depends": []},
                {"id": "lit", "type": "literature_rag", "label": "文献 RAG", "depends": ["in"]},
                {"id": "out", "type": "synthesizer", "label": "综合报告", "depends": ["lit"]},
            ]
        },
    },
    {
        "slug": "gene_enrichment",
        "name": "基因库 + 富集",
        "description": "基因详情与 GO/KEGG（不跑文献 FAISS）",
        "definition": {
            "nodes": [
                {"id": "in", "type": "input", "label": "输入", "depends": []},
                {"id": "gene", "type": "gene_detail", "label": "基因详情", "depends": ["in"]},
                {"id": "enrich", "type": "enrichment", "label": "GO/KEGG 富集", "depends": ["in"]},
                {"id": "out", "type": "synthesizer", "label": "综合报告", "depends": ["gene", "enrich"]},
            ]
        },
    },
    {
        "slug": "id_only",
        "name": "仅 ID 映射",
        "description": "跨库 ID / 官方符号解析",
        "definition": {
            "nodes": [
                {"id": "in", "type": "input", "label": "输入", "depends": []},
                {"id": "id", "type": "id_resolve", "label": "ID 映射", "depends": ["in"]},
                {"id": "out", "type": "synthesizer", "label": "综合报告", "depends": ["id"]},
            ]
        },
    },
    {
        "slug": "retrieval_debug",
        "name": "检索调试",
        "description": "仅检索调试摘要，不调用大模型合成",
        "definition": {
            "nodes": [
                {"id": "in", "type": "input", "label": "输入", "depends": []},
                {"id": "lit", "type": "literature_rag", "label": "文献 RAG", "depends": ["in"]},
            ]
        },
    },
]
