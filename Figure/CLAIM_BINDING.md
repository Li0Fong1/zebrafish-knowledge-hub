# 断言级溯源审计报告（CLAIM BINDING）

- 生成时间：2026-09-28T10:53:16
- 结论：**0 项不一致 / 0 项待人工确认 / 共 57 项**

## 一、待处置（mismatch）

| 层 | 编号 | 断言 | 实际 | 严重度 |
| --- | --- | --- | --- | --- |

## 二、待人工确认（unknown）

| 层 | 编号 | 断言 | 说明 |
| --- | --- | --- | --- |

## 三、C2 绑定明细（源码/配置）

| 断言 | 状态 | 落点 |
| --- | --- | --- |
| 绑定：Agent orchestrator 意图路由 / 工具编排 | ok | agent/hub_orchestrator.py:1183 命中 def .*orchestrat|class .*Orchestrat（编排器实现） |
| 绑定：RAG 知识库模块存在 | ok | rag/pubmed_yes_retriever.py:175 命中 def retrieve|def search（检索实现） |
| 绑定：策展基因库（MySQL 表） | ok | database/ 目录内命中 21 个文件（目录存在即可（占位）） |
| 绑定：Integrated toolkit: ID map | ok | database/tool_orchestrator.py:2 命中 _SEQ_KW|工具（工具路由） |
| 绑定：Sequence retrieval CDS / protein / promoter | ok | database/sequence_toolkit.py:2 命中 启动子|promoter（启动子提取实现） |
| 绑定：Promoter 坐标锚定 GRCz11（TSS 上游默认 2 kb） | ok | llm/zebrafish_prompt.py:132 命中 GRCz11.*2\s*kb|2\s*kb.*GRCz11（启动子坐标口径） |
| 绑定：GO / KEGG enrichment | ok | database/ 目录内命中 8 个文件（富集工具） |
| 绑定：Layout-aware text cleaning | ok | rag/pdf_fulltext_align.py:916 命中 def clean_figure_artifacts（版面伪影清理） |
| 绑定：非科学内容在切分前移除 | ok | rag/text_clean.py:7 命中 _REF_START|_ACK_START（截断规则） |
| 绑定：Paragraph + section segmentation | ok | rag/pubmed_yes_chunking.py:56 命中 section（章节切分） |
| 绑定：Per-paper coarse scoring | ok | agent/hub_orchestrator.py:1128 命中 candidates_before_rerank（粗排候选计数） |
| 绑定：Cross-encoder fine scoring | ok | rag/rerank.py:5 命中 CrossEncoder（交叉编码器重排） |
| 绑定：Answer contract: direct first sentence | ok | llm/zebrafish_prompt.py:493 命中 def ensure_（契约保障函数） |
| 绑定：Reference footer / 引用页脚 | ok | llm/zebrafish_prompt.py:1116 命中 ensure_reference_footer（页脚保障） |
| 绑定：GE formula（ge_formula 维度） | ok | llm/zebrafish_prompt.py:493 命中 ensure_trait_ge_formula（GE 公式保障） |
| 绑定：评分量表 7 维度 | ok | logs/eval_isomorph_50/blind_pack.json:5 命中 "id": "direct_first"（盲评包内的维度定义） |
| 绑定：评分量表含 no_review_shell | ok | logs/eval_isomorph_50/blind_pack.json:35 命中 "id": "no_review_shell"（维度键） |
| 绑定：九层验收 L0..L8 | ok | scripts/ 目录内命中 4 个文件（验收脚本层命名） |
| 绑定：Dual anchoring（双锚定） | ok | .env:40 命中 FAISS_PUBMED_YES_DIR|EMBEDDING_DIM（运行时配置） |
| 绑定：REST API 入口 | ok | api/api_server.py:15 命中 FastAPI|app\s*=（服务入口） |
| 绑定：Same symbol, different genes across assemblies | ok | database/ 目录内命中 5 个文件（别名表 + 组装映射表支撑） |
| 绑定：Retired accessions in legacy literature | ok | database/ 目录内命中 6 个文件（跨库引用与文献关联表支撑） |

## 四、全部记录

| 层 | 编号 | 断言 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| C1 | C1-papers | 事实键「papers」已在图内落脚且与权威值一致 | ok | 权威 ['32,532', '32532']；出现于 Fig [1, 2, 3, 4, 9] |
| C1 | C1-chunks | 事实键「chunks」已在图内落脚且与权威值一致 | ok | 权威 ['7,651,942', '7651942']；出现于 Fig [1, 2, 3, 9] |
| C1 | C1-shards | 事实键「shards」已在图内落脚且与权威值一致 | ok | 权威 ['170']；出现于 Fig [1, 2, 3, 9] |
| C1 | C1-dim | 事实键「dim」已在图内落脚且与权威值一致 | ok | 权威 ['2,048', '2048']；出现于 Fig [1, 2, 3, 9] |
| C1 | C1-cand | 事实键「cand」已在图内落脚且与权威值一致 | ok | 权威 ['500']；出现于 Fig [1, 3, 4] |
| C1 | C1-topn | 事实键「topn」已在图内落脚且与权威值一致 | ok | 权威 ['100']；出现于 Fig [1, 2, 3, 4, 6] |
| C1 | C1-pack | 事实键「pack」已在图内落脚且与权威值一致 | ok | 权威 ['48,000', '48000']；出现于 Fig [3] |
| C1 | C1-genes | 事实键「genes」已在图内落脚且与权威值一致 | ok | 权威 ['26,321', '26321']；出现于 Fig [1, 6, 8] |
| C1 | C1-xrefs | 事实键「xrefs」已在图内落脚且与权威值一致 | ok | 权威 ['80,212', '80212']；出现于 Fig [1, 6, 7] |
| C1 | C1-go | 事实键「go」已在图内落脚且与权威值一致 | ok | 权威 ['110,966', '110966']；出现于 Fig [1, 6, 7] |
| C1 | C1-kegg_links | 事实键「kegg_links」已在图内落脚且与权威值一致 | ok | 权威 ['36,959', '36959']；出现于 Fig [1, 6, 7] |
| C1 | C1-kegg_pw | 事实键「kegg_pw」已在图内落脚且与权威值一致 | ok | 权威 ['1,520', '1520']；出现于 Fig [6, 7] |
| C1 | C1-expr | 事实键「expr」已在图内落脚且与权威值一致 | ok | 权威 ['319,344', '319344']；出现于 Fig [1, 6, 7] |
| C1 | C1-refs | 事实键「refs」已在图内落脚且与权威值一致 | ok | 权威 ['161,298', '161298']；出现于 Fig [1, 6, 7] |
| C1 | C1-alias | 事实键「alias」已在图内落脚且与权威值一致 | ok | 权威 ['145,439', '145439']；出现于 Fig [1, 6, 8] |
| C1 | C1-grc12tu_map | 事实键「grc12tu_map」已在图内落脚且与权威值一致 | ok | 权威 ['22,628', '22628']；出现于 Fig [1, 6, 7, 8] |
| C1 | C1-cross-figure | 跨图复述的事实键已逐键校验一致 | ok | ≥2 图复述 15 个：{'papers': [1, 2, 3, 4, 9], 'chunks': [1, 2, 3, 9], 'shards': [1, 2, 3, 9], 'dim': [1, 2, 3, 9], 'cand': [1, 3, 4], 'topn': [1, 2, 3, 4, 6], 'genes': [1, 6, 8], 'xrefs': [1, 6, 7], 'go': [1, 6, 7], 'kegg_links': [1, 6, 7], 'kegg_pw': [6, 7], 'expr': [1, 6, 7], 'refs': [1, 6, 7], 'alias': [1, 6, 8], 'grc12tu_map': [1, 6, 7, 8]}；单图孤证 1 个：{'pack': [3]} |
| C2 | C2-agent/hub_orchestrator.py-Agent orches | 绑定：Agent orchestrator 意图路由 / 工具编排 | ok | agent/hub_orchestrator.py:1183 命中 def .*orchestrat|class .*Orchestrat（编排器实现） |
| C2 | C2-rag/pubmed_yes_retriever.py-RAG 知识库模块存在 | 绑定：RAG 知识库模块存在 | ok | rag/pubmed_yes_retriever.py:175 命中 def retrieve|def search（检索实现） |
| C2 | C2-database/ | 绑定：策展基因库（MySQL 表） | ok | database/ 目录内命中 21 个文件（目录存在即可（占位）） |
| C2 | C2-database/tool_orchestrator.py-Integrated t | 绑定：Integrated toolkit: ID map | ok | database/tool_orchestrator.py:2 命中 _SEQ_KW|工具（工具路由） |
| C2 | C2-database/sequence_toolkit.py-Sequence ret | 绑定：Sequence retrieval CDS / protein / promoter | ok | database/sequence_toolkit.py:2 命中 启动子|promoter（启动子提取实现） |
| C2 | C2-llm/zebrafish_prompt.py-Promoter 坐标锚 | 绑定：Promoter 坐标锚定 GRCz11（TSS 上游默认 2 kb） | ok | llm/zebrafish_prompt.py:132 命中 GRCz11.*2\s*kb|2\s*kb.*GRCz11（启动子坐标口径） |
| C2 | C2-database/ | 绑定：GO / KEGG enrichment | ok | database/ 目录内命中 8 个文件（富集工具） |
| C2 | C2-rag/pdf_fulltext_align.py-Layout-aware | 绑定：Layout-aware text cleaning | ok | rag/pdf_fulltext_align.py:916 命中 def clean_figure_artifacts（版面伪影清理） |
| C2 | C2-rag/text_clean.py-非科学内容在切分前移除 | 绑定：非科学内容在切分前移除 | ok | rag/text_clean.py:7 命中 _REF_START|_ACK_START（截断规则） |
| C2 | C2-rag/pubmed_yes_chunking.py-Paragraph +  | 绑定：Paragraph + section segmentation | ok | rag/pubmed_yes_chunking.py:56 命中 section（章节切分） |
| C2 | C2-agent/hub_orchestrator.py-Per-paper co | 绑定：Per-paper coarse scoring | ok | agent/hub_orchestrator.py:1128 命中 candidates_before_rerank（粗排候选计数） |
| C2 | C2-rag/rerank.py-Cross-encode | 绑定：Cross-encoder fine scoring | ok | rag/rerank.py:5 命中 CrossEncoder（交叉编码器重排） |
| C2 | C2-llm/zebrafish_prompt.py-Answer contr | 绑定：Answer contract: direct first sentence | ok | llm/zebrafish_prompt.py:493 命中 def ensure_（契约保障函数） |
| C2 | C2-llm/zebrafish_prompt.py-Reference fo | 绑定：Reference footer / 引用页脚 | ok | llm/zebrafish_prompt.py:1116 命中 ensure_reference_footer（页脚保障） |
| C2 | C2-llm/zebrafish_prompt.py-GE formula（g | 绑定：GE formula（ge_formula 维度） | ok | llm/zebrafish_prompt.py:493 命中 ensure_trait_ge_formula（GE 公式保障） |
| C2 | C2-logs/eval_isomorph_50/blind_pack.json-评分量表 7 维度 | 绑定：评分量表 7 维度 | ok | logs/eval_isomorph_50/blind_pack.json:5 命中 "id": "direct_first"（盲评包内的维度定义） |
| C2 | C2-logs/eval_isomorph_50/blind_pack.json-评分量表含 no_rev | 绑定：评分量表含 no_review_shell | ok | logs/eval_isomorph_50/blind_pack.json:35 命中 "id": "no_review_shell"（维度键） |
| C2 | C2-scripts/ | 绑定：九层验收 L0..L8 | ok | scripts/ 目录内命中 4 个文件（验收脚本层命名） |
| C2 | C2-.env-Dual anchori | 绑定：Dual anchoring（双锚定） | ok | .env:40 命中 FAISS_PUBMED_YES_DIR|EMBEDDING_DIM（运行时配置） |
| C2 | C2-api/api_server.py-REST API 入口 | 绑定：REST API 入口 | ok | api/api_server.py:15 命中 FastAPI|app\s*=（服务入口） |
| C2 | C2-database/ | 绑定：Same symbol, different genes across assemblies | ok | database/ 目录内命中 5 个文件（别名表 + 组装映射表支撑） |
| C2 | C2-database/ | 绑定：Retired accessions in legacy literature | ok | database/ 目录内命中 6 个文件（跨库引用与文献关联表支撑） |
| C3 | C3-fig5-sum | Fig5 九层检查数之和 = 标题总数 | ok | 各层 [33, 24, 20, 30, 43, 16, 18, 8, 5] 和=197 vs 标题 197 |
| C3 | C3-fig5-warn | Fig5 各层告警之和 = 标题告警数 | ok | 各层告警 [1, 2] 和=3 vs 标题 3 |
| C3 | C3-fig5-time | Fig5 各层耗时之和在合理范围 | ok | 各层耗时和=910.8s |
| C3 | C3-fig6-pct | Fig6 覆盖率 = 计数 ÷ 基因总数 | ok | 基数 26321；不匹配 无 |
| C3 | C3-fig1-pct | Fig1 覆盖率与 Fig6 同源（96.6% / 86.0%） | ok | Fig1 含 96.6%=True / 86.0%=True |
| C4 | C4-coverage | 画布非数字断言的源码绑定覆盖率（目标 ≥80%） | ok | 32/32 = 100.0%（已排除含数字条目、面板标题与轴标题） |
| C5 | C5-13,534 | 图件未残留陈旧值「13,534」 | ok | 旧索引论文数（应为 32,532）；未出现 |
| C5 | C5-516,476 | 图件未残留陈旧值「516,476」 | ok | 旧语义块数（应为 7,651,942）；未出现 |
| C5 | C5-1,269 | 图件未残留陈旧值「1,269」 | ok | 旧 FAISS 分片数（应为 170）；未出现 |
| C5 | C5-candidates = 200 | 图件未残留陈旧值「candidates = 200」 | ok | 旧候选数（应为 500）；未出现 |
| C5 | C5-candidates=200 | 图件未残留陈旧值「candidates=200」 | ok | 旧候选数（应为 500）；未出现 |
| C5 | C5-top 10 reranked | 图件未残留陈旧值「top 10 reranked」 | ok | 旧 top-k（应为 100）；未出现 |
| C5 | C5-self-rank = 1 | 图件未残留陈旧值「self-rank = 1」 | ok | 旧探针结果；未出现 |
| C5 | C5-174 ms | 图件未残留陈旧值「174 ms」 | ok | 旧探针耗时；未出现 |
| C5 | C5-2,342 characters | 图件未残留陈旧值「2,342 characters」 | ok | 旧上下文长度；未出现 |
| C5 | C5-5.8 k | 图件未残留陈旧值「5.8 k」 | ok | 旧上下文长度；未出现 |
| C5 | C5-994 papers | 图件未残留陈旧值「994 papers」 | ok | 旧测试索引；未出现 |
| C5 | C5-snapshot | 数据快照 _data.json 内无过时参数残留 | ok | 命中 无；该文件被 make_figures.py 与 verify_figures.py 读取，过时值会污染「合法数值」白名单 |
