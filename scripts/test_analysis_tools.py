# -*- coding: utf-8 -*-
"""分析工具 API 冒烟测试（基因/BLAST/富集/KEGG/ID解析）。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from core.entry import bootstrap_app

bootstrap_app()

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  OK  {name}")
    else:
        print(f"  FAIL {name}: {detail}")
        FAILURES.append(name)


def main():
    print("=== 分析工具冒烟测试 ===\n")

    # 1. 基因详情
    from gene_toolkit import get_gene_detail, search_genes, normalize_gene_name

    d = get_gene_detail("hsp70")
    check("基因 hsp70→hsp70.1", d and d["basic"]["gene_name"] == "hsp70.1")
    go_n = len(d.get("go", [])) if d else 0
    check("基因 GO 非空", go_n > 0, f"go={go_n}")
    ref_n = len(d.get("references", [])) if d else 0
    check("文献 LIMIT", ref_n <= 10, f"refs={ref_n}")

    rows = search_genes("mapk")
    check("基因搜索 mapk", len(rows) > 0, f"count={len(rows)}")

    # 2. ID 解析
    from gene_id_mapper import resolve_gene_identifier
    from gene_toolkit import batch_id_convert

    r = resolve_gene_identifier("p53")
    check("ID p53", r.get("resolved") and r.get("canonical", {}).get("gene_name") == "tp53")
    batch = batch_id_convert(["p53", "30590", "ZDB-GENE-990415-270"])
    check("批量 ID", batch.get("count", 0) >= 2, str(batch.get("count")))

    # 3. 富集
    from enrichment_toolkit import go_enrichment, kegg_enrichment, kegg_pathway_search

    go = go_enrichment(["tp53", "hsp70", "myl7"], top_n=5)
    check("GO 富集", go.get("success") and (go.get("terms") or go.get("significant_count", 0) >= 0))
    kegg_e = kegg_enrichment(["tp53", "mapk1", "fgf8a"], top_n=5)
    check("KEGG 富集", kegg_e.get("success"))

    # 4. KEGG 通路检索
    kw = kegg_pathway_search("MAPK")
    check("KEGG 检索 MAPK", kw.get("success") and len(kw.get("pathways", [])) > 0)
    p0 = kw["pathways"][0]
    check("KEGG 通路字段", "pathway_id" in p0 and "pathway_name" in p0)

    # 5. BLAST
    from seq_toolkit import run_local_blast, validate_sequence

    sample = "ATGGCTTCTAAGGAAGAAGATCTTCTGGAGGAGAAGGTGAAGGAGCTGCAGAAG"
    ok, msg = validate_sequence(sample)
    check("BLAST 序列校验", ok, msg)
    blast = run_local_blast(sample, top_k=3, evalue=10)
    check("BLAST 运行", blast.get("success") is not False, blast.get("error", blast.get("note", "")))

    # 6. 知识库来源列表
    from knowledge_ingest import list_knowledge_sources

    src = list_knowledge_sources()
    check("知识库列表", isinstance(src, dict))

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项: {', '.join(FAILURES)}")
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
