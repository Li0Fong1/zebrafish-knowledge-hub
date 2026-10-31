# Fig 9 / Fig 10 深度审计报告

- 生成时间：2026-09-28T10:32:49
- 复算：seeds=[42, 7, 2026] n=300；服务器：可用
- 结论：**0 项不一致 / 2 项待人工确认 / 共 64 项**

## 一、待处置问题（status=mismatch）

| 层 | 编号 | 断言 | 实际 | 严重度 |
| --- | --- | --- | --- | --- |

## 二、待人工确认（status=unknown）

| 层 | 编号 | 断言 | 说明 |
| --- | --- | --- | --- |
| D1 | D1-recompute | 统计复算（--light 模式本轮跳过） | 直接采用冻结值 n=999 seed=42；需重算时去掉 --light |
| D5 | D5-year-db | Fig10(A) 的「2025」可由注册表直接证实 | 注册表无年份列；已改由 D5-year（NCBI 快照）佐证 |

## 三、复算结果

| seed | 样本 | raw均值 | clean均值 | 去除% | 段/篇 | 段均 | refs标题存在% | refs真实触发% | ack标题存在% | ack真实触发% | 上限命中 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## 四、Fig 10 八项要点取证重判

| # | 要点 | 本 Hub | 通用模型 | LLM 证据 |
| --- | --- | --- | --- | --- |
| 1 | 最新版本精确命名（GRCz12tu） | full | miss | — |
| 2 | 发布年份（2025） | full | miss | — |
| 3 | RefSeq 接入号与版本对应 | full | full | GCF_049306965.1 |
| 4 | Ensembl 注释版本一致（release 116） | full | miss | — |
| 5 | 版本谱系完整（Zv1–Zv7 → GRCz12tu） | full | part |  (列出版本数 3/6，Hub 6/6) |
| 6 | 品系信息（Tuebingen/TU） | full | miss | — |
| 7 | 双锚定策略说明 | full | miss | — |
| 8 | 条目级可追溯引用 | full | miss |  (PMID 数 Hub=3 LLM=0) |

重判汇总：Hub 8/8 完整；通用模型 1/8 完整、1/8 部分、6/8 缺失

## 五、全部记录

| 层 | 编号 | 断言 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| D0 | D0-fig9-pct | 图注百分比与数据一致 | ok | 数据 17.35% → 显示应为 17.4%；en 含=True zh 含=True |
| D0 | D0-fig9-sampled | 图注抽样规模与数据一致 | ok | sampled=999 seed=42 |
| D0 | D0-fig10-source | 图注/图内标注了回答来源 | ok | 图注须写明取自真实运行日志（可追溯） |
| D0 | D0-readme-56,122 | README 含真实数值 原始正文均值 56,122 | ok | token=56,122 |
| D0 | D0-readme-46,904 | README 含真实数值 清洗后均值 46,904 | ok | token=46,904 |
| D0 | D0-readme-17.35 | README 含真实数值 去除比例 17.35 | ok | token=17.35 |
| D0 | D0-readme-193 | README 含真实数值 段/篇 193 | ok | token=193 |
| D0 | D0-fig9-canvas-56,122 | Fig9 图内含 56,122 | ok |  |
| D0 | D0-fig9-canvas-46,904 | Fig9 图内含 46,904 | ok |  |
| D0 | D0-fig9-canvas-193 | Fig9 图内含 193 | ok |  |
| D0 | D0-fig9-canvas-289 | Fig9 图内含 289 | ok |  |
| D0 | D0-fig9-canvas-FAISS | Fig9 图内含 FAISS | ok |  |
| D0 | D0-fig10-canvas-GRCz12tu | Fig10 图内含 GRCz12tu | ok |  |
| D0 | D0-fig10-canvas-GRCz11 | Fig10 图内含 GRCz11 | ok |  |
| D0 | D0-fig10-canvas-GCF_049306965.1 | Fig10 图内含 GCF_049306965.1 | ok |  |
| D0 | D0-fig10-canvas-8/8 | Fig10 图内含 8/8 | ok |  |
| D1 | D1-recompute | 统计复算（--light 模式本轮跳过） | unknown | 直接采用冻结值 n=999 seed=42；需重算时去掉 --light |
| D1 | D1-sections | 图件「11 canonical sections」与实现一致 | ok | rag/fulltext_sections.py CANONICAL_SECTIONS = 11 类 |
| D1 | D1-sections-counted | 抽样语料每篇均含全部规范章节键 | ok | 冻结计数 11 类，每类 [999] 篇（frozen n=999） |
| D1 | D1-disclose-scope | 图件声明抽样范围与生产索引口径差异 | ok | 段数仅刻画抽样语料；生产索引为章节感知切分，不得混用 |
| D1 | D1-disclose-cap | 图件披露语料 120,000 字符上限 | ok | 图注含 120,000=True；上限命中率 7.2% |
| D1 | D1-disclose-stability | 图件披露跨抽样稳定性 | ok | 跨种子极差 3.41% |
| D4 | D4-src-hub | Fig10(A) 来源日志存在 | ok | _a3_answer.txt 2845 字符 |
| D4 | D4-src-llm | Fig10(B) 来源日志存在 | ok | _llm_compare_deepseek.txt 1143 字符 |
| D4 | D4-matrix-summary | Fig10 汇总与取证重判一致 | ok | 图件 ('8', '1', '1', '6') vs 重判 (8,1,1,6)；Hub 8/8 完整 · 通用模型 1 完整 / 1 部分 / 6 缺失 |
| D4 | D4-cell-1 | 矩阵第 1 项与取证一致 | ok | 要点「最新版本精确命名（GRCz12tu）」图件=(完整,缺失) 重判=(完整,缺失) |
| D4 | D4-cell-2 | 矩阵第 2 项与取证一致 | ok | 要点「发布年份（2025）」图件=(完整,缺失) 重判=(完整,缺失) |
| D4 | D4-cell-3 | 矩阵第 3 项与取证一致 | ok | 要点「RefSeq 接入号与版本对应」图件=(完整,完整) 重判=(完整,完整)；LLM 证据：GCF_049306965.1 |
| D4 | D4-cell-4 | 矩阵第 4 项与取证一致 | ok | 要点「Ensembl 注释版本一致（release 116）」图件=(完整,缺失) 重判=(完整,缺失) |
| D4 | D4-cell-5 | 矩阵第 5 项与取证一致 | ok | 要点「版本谱系完整（Zv1–Zv7 → GRCz12tu）」图件=(完整,部分) 重判=(完整,部分)；LLM 证据： (列出版本数 3/6，Hub 6/6) |
| D4 | D4-cell-6 | 矩阵第 6 项与取证一致 | ok | 要点「品系信息（Tuebingen/TU）」图件=(完整,缺失) 重判=(完整,缺失) |
| D4 | D4-cell-7 | 矩阵第 7 项与取证一致 | ok | 要点「双锚定策略说明」图件=(完整,缺失) 重判=(完整,缺失) |
| D4 | D4-cell-8 | 矩阵第 8 项与取证一致 | ok | 要点「条目级可追溯引用」图件=(完整,缺失) 重判=(完整,缺失)；LLM 证据： (PMID 数 Hub=3 LLM=0) |
| D4 | D4-B1 | (B) 措辞「称最新版本为「GRCz12（2024 年发布）」」有原文依据 | ok | 原文称 GRCz12 / 2024 |
| D4 | D4-B2 | (B) 措辞「图内未出现「把 UCSC 别名当作独立版本」的失实描述」有原文依据 | ok | 原文已把 DanRer11 标注为「UCSC 对 GRCz11 的命名」，故图件不得反过来说它被当作独立版本；当前图内已清除该表述=True |
| D4 | D4-B3 | (B) 措辞「称对应「Ensembl release 113」」有原文依据 | ok | 原文称 113 |
| D4 | D4-B4 | (B) 措辞「未提及品系（Tuebingen/TU）与双锚定策略」有原文依据 | ok | 原文含品系=False 含锚定=False（两者均无则成立） |
| D4 | D4-B5 | (B) 措辞「引用为数据库主页链接，非条目级可追溯」有原文依据 | ok | 原文 PMID 数=0，链接均为数据库顶层页 |
| D4 | D4-A-links | (A) 「4 条官方条目级直链」表述准确 | ok | 回答含 13 条链接，其中条目级 4 条；«未源】区 4 条为顶层/分类页（taxonomy、Info/Index、zfin.org、hgGateway） |
| D2 | D2-local-rag/text_clean.py | 本机模块存在且关键常量命中 | ok | sha256[:16]=e59500a1e127a8a3 常量命中={} |
| D2 | D2-srv-rag/text_clean.py | 与服务器同名模块逐字节一致 | ok | local=e59500a1e127a8a3 server=e59500a1e127a8a3 |
| D2 | D2-local-rag/paragraph_split.py | 本机模块存在且关键常量命中 | ok | sha256[:16]=c29ac64ba7b304f0 常量命中={} |
| D2 | D2-srv-rag/paragraph_split.py | 与服务器同名模块逐字节一致 | ok | local=c29ac64ba7b304f0 server=c29ac64ba7b304f0 |
| D2 | D2-local-rag/fulltext_sections.py | 本机模块存在且关键常量命中 | ok | sha256[:16]=58d85c390c898fc6 常量命中={} |
| D2 | D2-srv-rag/fulltext_sections.py | 与服务器同名模块逐字节一致 | ok | local=58d85c390c898fc6 server=58d85c390c898fc6 |
| D2 | D2-local-rag/pubmed_yes_chunking.py | 本机模块存在且关键常量命中 | ok | sha256[:16]=af82f92d7ef0cfe1 常量命中={} |
| D2 | D2-srv-rag/pubmed_yes_chunking.py | 与服务器同名模块逐字节一致 | ok | local=af82f92d7ef0cfe1 server=af82f92d7ef0cfe1 |
| D2 | D2-local-rag/pdf_fulltext_align.py | 本机模块存在且关键常量命中 | ok | sha256[:16]=705e6bb68522a785 常量命中={} |
| D2 | D2-srv-rag/pdf_fulltext_align.py | 与服务器同名模块逐字节一致 | ok | local=705e6bb68522a785 server=705e6bb68522a785 |
| D2 | D2-local-core/config.py | 本机模块存在且关键常量命中 | ok | sha256[:16]=11cce1a74bf31178 常量命中={'PUBMED_YES_SECTION_CHUNKING': True, 'EXIST_ENO_MAX_BODY_CHARS': True, 'PUBMED_YES_SPLIT_MODE': True} |
| D2 | D2-srv-core/config.py | 服务器 config.py 分岔仅限图集配置块（关键常量一致） | ok | 整文件哈希 local=11cce1a74bf31178 / server=3247aeac3511997b（本机新增 FIG_* 配置块，差异属预期）；服务器常量命中={'PUBMED_YES_SECTION_CHUNKING': True, 'EXIST_ENO_MAX_BODY_CHARS': True, 'PUBMED_YES_SPLIT_MODE': True} |
| D2 | D2-srv-chunking | 服务器切分/截断开关与本机一致（0 = 不截断） | ok | FAISS_PUBMED_YES_DIR=/data/zoujin/deng/zebrafish_runtime/faiss_pubmed_yes_qwen3_4b_v2_merged|EXIST_ENO_MAX_BODY_CHARS=0|PUBMED_YES_SPLIT_MODE=paragraph|CORPUS audit_round_01.json|audit_round_02.json|b |
| D3 | D3-chunks-per-paper | 图件已声明「段/篇」与「块/篇」的口径差异（不得混用） | ok | 本地抽样段/篇 192.6 vs 生产索引 7,651,942/32,532 = 235.2 块/篇（差异 22.1%，索引切分模式 paragraph_section）；图件已声明=True |
| D3 | D3-index-scope | 图件显式限定抽样语料范围 | ok | 索引 32,532 篇 / 7,651,942 块；抽样目录 yes_fulltext_gold |
| D3 | D3-corpus-lineage | 抽样语料与服务器语料同源可证 | ok | 本机 batch_* 目录 393 个；服务器 393 个；服务器目录前几项 未取到 |
| D5 | D5-year | Fig10(A)「2025」发布年份由 NCBI 记录佐证 | ok | NCBI assembly_date = 2025-04-04（Apr 4, 2025）；本地注册表无年份列，故以 NCBI 记录为权威依据；来源 https://www.ncbi.nlm.nih.gov/datasets/genome/GCF_049306965.1/ |
| D5 | D5-grcz12tu-facts | GRCz12tu 的品系/命名/级别与 NCBI 一致 | ok | name=GRCz12tu strain=Tuebingen level=Complete Genome |
| D5 | D5-successor | 图件已披露该接入号已被后继版本取代（时效性） | ok | NCBI 标注 GCF_049306965.1 状态 = replaced，后继 = GCF_049306965.2；图件已披露 = True |
| D5 | D5-grcz11-status | 记录了 GRCz11 的 RefSeq 状态（suppressed） | ok | refseq_status = suppressed，date = 2017-05-09；本地锚定依赖 Ensembl release 116 而非 NCBI RefSeq 记录，故不受该状态影响 |
| D5 | D5-assembly-GRCz12tu | 注册表：GRCz12tu 为 NCBI 参考且接入号匹配 | ok | {'accession': 'GCF_049306965.1', 'strain': 'Tuebingen', 'local_anchor': '0', 'ncbi_reference': '1', 'ensembl_release': 'NULL'} |
| D5 | D5-assembly-GRCz11 | 注册表：GRCz11 为本地锚定且 Ensembl release = 116 | ok | {'accession': 'GCF_000002035.6', 'strain': 'Tuebingen', 'local_anchor': '1', 'ncbi_reference': '0', 'ensembl_release': '116'} |
| D5 | D5-strain | 注册表：两个组装品系均为 Tuebingen（支撑 Fig10(A) 的品系声明） | ok | GRCz12tu=Tuebingen / GRCz11=Tuebingen |
| D5 | D5-year-db | Fig10(A) 的「2025」可由注册表直接证实 | unknown | 注册表无年份列；已改由 D5-year（NCBI 快照）佐证 |
| D5 | D5-xref | 标识符映射计数可复现（与 Fig6/Fig8 一致） | ok | gene_assembly_xref = 22628 |
