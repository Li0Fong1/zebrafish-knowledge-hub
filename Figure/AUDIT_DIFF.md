# 图集 × 本机 × 服务器 一致性审计

- 服务器索引权威值：**32,532 篇 / 7,651,942 块 / 170 分片 / 2048 维**（2026-09-10T00:18:42）
- 检索参数：candidates **500** → rerank top **100** 篇，打包预算 **48000** 字符
- 断言总数 27，需修正 0

| 级别 | 断言 | 图 | 图上值 | 权威值 | 类型 |
|---|---|---|---|---|---|
| ✅ critical | idx_chunks | 1,2,3 | Fig1=7,651,942, Fig2=7,651,942 | 7,651,942 | stale-index |
| ✅ critical | idx_papers | 1,2,3 | Fig1=32,532, Fig3=32,532 | 32,532 | stale-index |
| ✅ critical | idx_shards | 1,2,3 | Fig1=170, Fig2=170, Fig3=170 | 170 | stale-index |
| ✅ critical | param_candidates | 1,3 | Fig3=500 | 500 | param-drift |
| ✅ critical | param_pack | 3 | Fig3=48,000 | 48,000 | param-drift |
| ✅ critical | param_topn | 1,3 | Fig1=100, Fig3=100 | 100 | param-drift |
| ✅ critical | probe_selfrank | 4 | Fig4=14 | 14 | stale-index |
| ✅ high | db_gene_alias | 1,2,6,7 | Fig1=145,439, Fig6=145,439 | 145,439 | wrong-claim |
| ✅ high | db_gene_assembly_xref | 1,2,6,7 | Fig1=22,628, Fig6=22,628, Fig7=22,628 | 22,628 | wrong-claim |
| ✅ high | db_gene_basic | 1,2,6,7 | Fig1=26,321, Fig6=26,321 | 26,321 | wrong-claim |
| ✅ high | db_gene_expression | 1,2,6,7 | Fig1=319,344, Fig6=319,344, Fig7=319,344 | 319,344 | wrong-claim |
| ✅ high | db_gene_go | 1,2,6,7 | Fig1=110,966, Fig6=110,966, Fig7=110,966 | 110,966 | wrong-claim |
| ✅ high | db_gene_kegg | 1,2,6,7 | Fig1=36,959, Fig6=36,959, Fig7=36,959 | 36,959 | wrong-claim |
| ✅ high | db_gene_reference | 1,2,6,7 | Fig1=161,298, Fig6=161,298, Fig7=161,298 | 161,298 | wrong-claim |
| ✅ high | db_gene_xref | 1,2,6,7 | Fig1=80,212, Fig6=80,212, Fig7=80,212 | 80,212 | wrong-claim |
| ✅ high | db_genes_ensembl | 1,2,6,7 | Fig1=25,432, Fig6=25,432, Fig7=25,432 | 25,432 | wrong-claim |
| ✅ high | db_genes_with_expr | 1,2,6,7 | Fig6=24,245 | 24,245 | wrong-claim |
| ✅ high | db_genes_with_refs | 1,2,6,7 | Fig6=12,882 | 12,882 | wrong-claim |
| ✅ high | db_kegg_pathways | 1,2,6,7 | Fig6=1,520, Fig7=1,520 | 1,520 | wrong-claim |
| ✅ high | db_literature_paper | 1,2,6,7 | Fig2=32,549 | 32,549 | wrong-claim |
| ✅ high | idx_dim | 3 | Fig3=2048 | 2,048 | wrong-claim |
| ✅ high | probe_latency | 4 | Fig4=16.8 | 16.8 | stale-index |
| ✅ medium | idx_mode | 3 | Fig3=Paragraph + section | paragraph_section | scope-diff |
| ✅ medium | idx_model | 3 | Fig3=Qwen3-Embedding-4B | Qwen3-Embedding-4B | scope-diff |
| ✅ medium | idx_updated | 1,3 | Fig1=2026-09-10, Fig3=2026-09-10 | 2026-09-10T00:18:42 | scope-diff |

## 修复指令

