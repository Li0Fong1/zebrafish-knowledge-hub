# 期刊图件核验报告（2026-11-05）

核对对象：`Figure/EN_journal_figures/` 下 11 张图 + `Legends.md`。
核对方式：能落到服务器的查服务器（MySQL 精确 COUNT、FAISS manifest、sqlite 表型库），
能落到冻结产物的复算产物（评分 JSON、清洗统计、用量账本、语料 jsonl），
外部注释用 Ensembl REST 逐条比对；最后逐张看渲染结果。

---

## 一、按「是否需要实测」分类

### A. 不需要重测（8 张）
数值来自**已冻结的实验产物或语料快照**，与本次基因库清理无关，可直接使用。

| 图 | 数值来源 | 冻结物 |
|---|---|---|
| Fig2 配置消融 | 60 条盲评总分 / 时延 | `Figure/_config_sweep_scores.json`、`_config_sweep_answers.json` |
| Fig3 评审一致性 | 60 对分数、Spearman ρ | 同上 + `_config_sweep_scores_reasoner*.json` |
| FigS1 年份分布 | 35,261 条目录记录年份 | `.workbuddy/tmp/pdfread/year_hist.json` |
| FigS3 文献流程 | 228,896→100,424→52,255→39,228→32,549→32,532 | 建库期流水线日志（与正文一致） |
| FigS4 RAG 流程 | 360/48 字符、2,048 维、170 shards、500/100 | `core/config.py` 常量 + 索引 manifest |
| FigS5 全文清洗 | 56,122→46,904、52,750→39,370、35.1/34.1/48.5/47.8 | `Figure/_clean_stats.json` |
| FigS7 语料构成 | 12 本期刊计数、35,147/114、15,249/20,012 | `data/literature/literature_catalog.jsonl` |
| FigS8 API 用量 | 76/628/436 M tokens、273,356/2,963/2,330 | `Figure/_llm_usage.json` |

### B. 必须实测（3 张）
数字随数据库状态变化，必须按当前库重算。

| 图 | 依赖 | 本次处理 |
|---|---|---|
| Fig1 系统总览 | 基因库三栏 + 文献库 + 工具版本 | 已按当前库更新并核对 12 个数字 |
| FigS6 数据库规模 | 12 张表行数 + 4 项覆盖率 | 已按当前库重算并重绘 |
| FigS2 命名法对照 | 6 条基因记录（ID、别名、两套坐标） | 逐字段核对，修正 1 处编号 |

---

## 二、逐图核验结果

| 图 | 核验内容 | 结论 |
|---|---|---|
| **Fig1** | 32,139 / 118,174 / 109,508 / 110,632 / 171,079 / 319,343 / 53,513；39,228 / 32,549 / 7,651,942 / 170；BLAST+ 2.17.0 | 全部一致（BLAST 版本读服务器 `blastn -version` = 2.17.0+） |
| **Fig2** | 六配置均值 8.525/8.25/7.975/8.35/8.00/8.35；中位时延 68.3/75.9/73.7/77.4/46.2/117.8 s | 与评分文件复算一致；图上标 8.53/8.25/7.97/8.35/8.00/8.35 为两位小数舍入 |
| **Fig3** | n=60、ρ=0.754；B 评委六均值 6.23/6.16/6.55/6.85/7.08/7.38 | 复算一致 |
| **FigS1** | 目录总量 35,261；窗口内 35,067；2026 为不完整年（1,311） | 一致；窗外 194 条已在图注说明 |
| **FigS2** | 6 基因 × 4 套装配（Zv9 / GRCz10 / GRCz11 / GRCz12tu）的坐标，及 ZFIN / Ensembl / NCBI 编号 | Zv9、GRCz10 取自 `legacy_genome_feature`，GRCz11 取自 Ensembl REST，GRCz12tu 取自 `grcz12tu_gene`，逐条一致 |
| **FigS3** | 六个阶段计数；7,651,942 chunks | 32,549 与 `literature_vector_chunk`（32,549 行，全部 has_fulltext=1）一致；32,532/7,651,942 与索引 manifest 一致；前四段为建库日志值，与正文一致 |
| **FigS4** | 500 候选 / top 100 / 2,048 维 / 170 shards | `core/config.py`：`PUBMED_YES_RETRIEVE_CANDIDATES=500`、`PUBMED_YES_RERANK_TOP_N=100`；manifest 2,048 维、170 shards |
| **FigS5** | 4 个字符数、2 个降幅、4 个占比 | `_clean_stats.json` 复算完全一致（-16.4%、-25.4%） |
| **FigS6** | A 面板 12 根柱、B 面板 4 项覆盖率 | 全部等于当前库 `COUNT(*)` / `COUNT(DISTINCT)` |
| **FigS7** | 12 本期刊、WoS 35,147 / PubMed 114、核心 15,249 / 广义 20,012 | 用与绘图同口径（大写 + 空白归一）复算，12/12 完全一致 |
| **FigS8** | 3 个月 tokens 与请求数 | 与 `_llm_usage.json` 一致 |

---

## 三、本轮查出并修正的问题

1. **FigS2：tbxta 的 Ensembl 编号多了一个 0**
   原图 `ENSDARG000000101576`（12 位数字）→ 改为 `ENSDARG00000101576`（11 位，与其余 5 个基因一致）。
   依据：服务器 `gene_basic.ensembl_id` + Ensembl REST lookup（GRCz11，chr19:14,187,540-14,191,592）。

1b. **FigS2：别名列改为表内逐字条目**（见第五节第 1 条），避免出现无法在 `gene_alias` 中检索到的写法。

2. **Fig2 图注把 partial/full 说成「36 分片 / 170 分片索引」——表述错误**
   代码核对：`answer_depth` 不改变索引分片，`partial` 关闭查询扩写、预览更少候选，`full` 开启扩写并打包更大候选集；
   服务器上也不存在 36 分片的索引目录（现有 170/267/2017/2236/2711 分片五套）。
   图注已改为按「答题深度」描述，与正文 "restricted answer depth (partial)" 一致。

3. **FigS1 未说明窗前记录**
   柱子只画 1990–2026（35,067 条），目录另有 193 条 1990 年前记录（最早 1946）+ 1 条 2027 年脏数据，共 194 条未入图。
   图注已补明。

4. **FigS6 的「Catalogued papers」口径**
   取 `literature_paper` 当前行数 32,725（其中 32,549 有全文、176 无全文），与 FigS3/S4 的 32,549/32,532 构成正常的逐级收敛。

---

## 四、美观性检查（第二轮：彻底去「AI 味」）

这一轮把上一版残留的示意图式构件全部拆掉：

| 上一版 | 现在 |
|---|---|
| Fig1 仍有三个方框 + 分支框 | **完全无框**：查询为文字 + 细引线，两个分支用「标题 + 说明 + 判定词」排布，模块区改为三栏排印 |
| FigS3/S4 仍是方框流程 | 改为**开放式阶段栏**（顶部一条细线 + 编号 + 标题），只保留 04→05 的纵向连接，取消横向箭头 |
| 配色 | 改用**项目主题色：浅蓝 + 白**（`--ds-accent: #106A94`，取自 `web/css/design-system.css`） |
| FigS7 的 PubMed 段（0.3%）在黑条上不可见 | 加引注 "PubMed 114 (0.3%)" 指向条端 |
| 小提琴/箱体为浅色填充 | 白底 + 黑色轮廓，均值线黑色 |

统一参数：Arial 5.4–7.5 pt，轴线 0.6 pt，图形线 0.5–0.8 pt，网格 `#E3E9F1`，正文 `#0D1520` / 次级 `#3A4757`。

主题色：主色 `#106A94`（与站点 `--ds-accent` 同值），色阶 `#E6F0F8 → #BDD7EA → #9CC3DC → #4E8FB8 → #106A94`。
- Fig2 六档填充：`#E6F0F8` / `#BDD7EA`（RAG）、`#DCEAF5` / `#9CC3DC`（Fusion）、`#CBE0F0` / `#4E8FB8`（Hub），partial 浅 / full 深
- Fig2 七维堆叠：`#0B4F6F → #D3E4F0` 五级蓝
- FigS6 / FigS7 最浅条 `#CFE1EF`，FigS5 原始条 `#DCEAF5` + `#7FB0CE` 描边
- 示意图（Fig1 / S2 / S3 / S4）：正文近黑，栏目标题与阶段编号用主色，分隔线 `#CFD8E4`

对比度自查：黑点在每一档蓝底上都可辨；深蓝条上的白字（FigS5 面板 B）对比度足够；浅蓝条 `#E6F0F8` 对白底仍有可辨边界（配 `#106A94` 描边）。
逐张看渲染：无重叠、无越界、无裁切、无图例遮挡；FigS3 两行、FigS4 两段均完整显示。

**FigS3 三改（2026-11-05 12:55）**：改成 3+3 网格 + 底部 Corpus 横带。原 4+3 的回行布局里第二行只有三栏、左侧空一列，
Corpus 悬在中间像被落下；现在两行等宽、03→04 用「下—左—下」的折线回行，Corpus 单列为整幅通栏，语料参数一行读完。
**核查**：S2 36 格 + S3 7 个数 + manifest（32,532 / 7,651,942 / 170 / 2,048）全部一致，TOTAL FAIL = 0。

**FigS2/S3 终稿（2026-11-05 12:50）**：S3 去掉每栏顶部的粗横线（原来像表不像图），改为「编号+标题 / 居中图标 / 单一计数」三段式，
箭头回到图标中线，纵向连接 04→05 加实心头并与文字留出间隙，模型标移到底部左侧成一行。
**核查**：`scripts/verify_figs2_figs3.py` 逐格比对 S2 的 36 个单元格（6 基因 × ZFIN/Ensembl/NCBI/Zv9/GRCz10/GRCz12tu）与 S3 的 7 个计数，**0 失败**。

**FigS3 改版（2026-11-05 12:45）**：加浅蓝圆角底板（`#EAF2F8`）、图标放大、阶段文案精简为「一句说明 + 一个数字」；
底部加两枚品牌标（DeepSeek 鲸、Qwen 六边结，取 simple-icons 的 24×24 路径，统一染色 `#106A94`），分别标注
"DeepSeek-V4.1-Flash · screening" 与 "Qwen3-Embedding-4B · embedding"。

**FigS3 加图（2026-11-05 12:40）**：六个阶段 + Corpus 各配一枚自绘线描图标（显示器 / 文档叠放 / 漏斗 / 文档带勾 / 下载 / 文档带箭头 / 文档堆），统一 5.2 单位见方、0.6 pt 描边、白填充近黑描边（避免压住后方线条）；图标排在阶段标题左侧，编号与标题依次右移。没有采用网上下载的图标库：线宽、圆角与视觉重量难以和正文字重统一，且有授权问题。

---

## 五、按作者决定已处理的两点

1. **FigS2 别名列已全部换成 `gene_alias` 表内逐字条目**（原 `p53`/`shh`/`ace`/`ZF-NTL` 为文献常用名，表内无此串）：

   | 基因 | 现在图上的别名（逐字取自 gene_alias） |
   |---|---|
   | tp53 | brp53, drp53, delta113 p53, zgc:111919 |
   | tbxta | ntl, ntla, ta, zf-ntl |
   | shha | shh-a, syu, vhh-1, vhh1 |
   | fgf8a | fgf-8, acerebellar, etid309886.13 |
   | cyp19a1a | aromatase, cyp19, cyp19a, cyp19a1 |
   | mstnb | mstn, mstn-1, gdf8, zgc:109749 |

   23 个串已逐条回查 `SELECT ... WHERE a.alias IN (...)`，全部命中；图注同步改为「aliases registered for them in ZFIN，verbatim from the alias table」。

2. **FigS3 前四段按「日志自证」保留，不再追加重算**：228,896 / 100,424 / 52,255 / 39,228 为建库期流水线记录，
   与论文 16、17 段完全一致；独立旁证可见服务器日志
   `zebrafish_runtime/logs/faiss_pubmed_yes_qwen3_4b_v1` 类构建记录中的 `papers=39228`（2026-09-06），与第 4 段数量吻合。
   32,549 / 32,532 两段仍可用当前产物复核（`literature_vector_chunk` 32,549 行、索引 manifest 32,532 篇，差 17 篇为设计内不入索引）。

---

## 六、二次核查：A 类 8 张逐项复算（31 项，全部通过）

| 图 | 复算项 | 源值 | 图上 |
|---|---|---|---|
| Fig2 | 六配置总分均值 | 8.525 / 8.25 / 7.975 / 8.35 / 8.00 / 8.35 | 8.53 / 8.25 / 7.97 / 8.35 / 8.00 / 8.35 ✓ |
| Fig2 | 六配置中位时延 | 68.3 / 75.9 / 73.7 / 77.4 / 46.2 / 117.8 s | 一致 ✓ |
| Fig3 | Spearman ρ、n | 0.7545 / 60 | 0.754 / n = 60 ✓ |
| Fig3 | 评委 B 六点均值 | 6.23 / 6.16 / 6.55 / 6.85 / 7.08 / 7.38 | 折线一致 ✓ |
| FigS1 | 目录总量 / 窗口内 / 2026 | 35,261 / 35,067 / 1,311 | 一致 ✓ |
| FigS3 | 六阶段计数 | 228,896 / 100,424 / 52,255 / 39,228 / 32,549 / 32,532 | 一致 ✓ |
| FigS3 | chunk 总数 | 7,651,942 | 一致 ✓ |
| FigS4 | 分块参数 | 360 字符 / 48 重叠 | `PUBMED_YES_CHUNK_SIZE=360`、`OVERLAP=48` ✓ |
| FigS4 | 检索参数 | 500 候选 / top 100 | `RETRIEVE_CANDIDATES=500`、`RERANK_TOP_N=100` ✓ |
| FigS4 | 索引参数 | 2,048 维 / 170 shards | manifest 一致 ✓ |
| FigS4 | 打包上限 | 48,000 字符 | `PUBMED_YES_PACK_CHARS=48000` ✓ |
| FigS5 | 字符数 | 56,122→46,904；52,750→39,370 | 一致 ✓ |
| FigS5 | 降幅 | −16.4%；−25.4% | 复算 16.4 / 25.4 ✓ |
| FigS5 | 规则命中率 | 35.1 / 34.1 / 48.5 / 47.8 | 一致 ✓ |
| FigS7 | 记录总量 | 35,261 | 一致 ✓ |
| FigS7 | 来源构成 | WoS 35,147 / PubMed 114 | 一致（PubMed 占 0.3%，已加引注）✓ |
| FigS7 | 范围构成 | core 15,249 (43.2%) / broad 20,012 | 条形按比例绘制 ✓ |
| FigS7 | 12 本期刊计数 | 1,000 / 791 / 767 / 749 / 722 / 630 / 607 / 587 / 517 / 465 / 456 / 379 | 12/12 一致 ✓ |
| FigS8 | tokens（M） | 76 / 628 / 436 | 一致 ✓ |
| FigS8 | 请求数 | 273,356 / 2,963 / 2,330 | 一致 ✓ |

说明：初跑时出现两处「FAIL」，均为校验脚本自身的比较写法问题（Fig3 的 `\rho` 转义、FigS7 把 43.246% 写成 43.3），
核对源文件与重算值后确认图本身无误。

---

## 七、FigS2 里 tbxta 的 Zv9 空档：按「如实标注」定稿

三条路线（留空 / 换基因 / liftover 补值）经作者确认取第一条。理由与证据：

1. **空档来自注释，不是基因缺失。** 直接查原始 `Danio_rerio.Zv9.79.gtf.gz`（33,737 个 gene 记录），
   `ta` / `ntl` / `tbxta` / `ENSDARG00000101576` 四种写法均无命中；同一 GTF 中其余五个基因都能按
   染色体坐标定位，说明是该版本注释没有把这一位点落成 gene 记录，而非我们关联出错。
2. **旁证：GRCz10 上该基因就在 chr19。** `legacy_genome_feature` 里 tbxta 的 GRCz10 记录为
   `19:14,325,345-14,329,397`，与 GRCz11 的 `19:14,187,540-14,191,592` 相距约 138 kb；
   四个装配里其余五个基因的位移量同样落在 0.1–3.4 Mb，未见异常。
3. **不换基因。** tbxta（ntl / ta / ZF-NTL）正是「同一个基因在不同年代有不同叫法」最典型的例子，
   换掉它会削弱图的论点。
4. **不做 liftover。** 补一个推算值会让该格与其他 35 格的「注释实测值」口径不一致，读者无法分辨。

图注措辞已定稿为：「tbxta carries a dash in the Zv9 column because the Ensembl 79 annotation used here
holds no record for that locus at all; the gap is a property of the annotation, not of the gene」。
结论句仍成立：**六个基因的 ZFIN / Ensembl / NCBI 号四套装配全同，坐标则逐套不同** —— 这正是 Hub 用
ZFIN 号做身份锚点、不用坐标的依据。

---

## 八、FigS2 加入六套品系装配（2026-11-05 13:40）

### 为什么之前没有

这六套装配（ASM1840007v1/T5D、ASM3317019v3/Tübingen、ASM2018471v1/AB、fDanRer4.1、fDreABH1.1、
fDreTuH1.2）都是 **GenBank 草稿（GCA_），没有基因注释**：NCBI 不提供 gene 表，库里也没有对应坐标。
所以第一版图只能画到 Zv9/GRCz10/GRCz11/GRCz12tu 四套有注释的装配。

### 现在怎么补上的

用服务器上现成的 UCSC over.chain，把 GRCz12tu 的注释坐标迁移过去（`scripts/liftover_strain_coords.py`）：

- 一套一跳：GRCz12tu → ASM3317019v3 / ASM2018471v1 / fDanRer4.1 / fDreABH1.1 / fDreTuH1.2
- 两跳：GRCz12tu → GRCz11（danRer11）→ ASM1840007v1（T5D），中间无反向链

### 两道校验（缺一不可）

1. **自检**：同一套代码先投 GRCz11，六个落点必须覆盖已知的 GRCz11 注释区间 —— **6/6 通过**
   （tp53/mstnb 完全一致，tbxta 差 0 bp，fgf8a/shha/cyp19a1a 因 GRCz12tu 含 Gnomon 区间而略宽）。
   这一步同时验证了链文件用的是 GRCz12tu **.1 版**坐标，与库里 .2 版注释在本批基因上完全对齐。
2. **落点校验**：迁移后的 scaffold 必须在该装配报告里登记为同一号染色体，且中点位移 ≤ 5 Mb、
   区间覆盖 ≥ 30%。**36 格中 35 格通过**；未通过的是 fDreABH1.1 的 shha（落到 chr7:10.16 Mb，
   偏离 31 Mb，覆盖仅 38%）——该草稿装配在这一位点没有共线片段，图上留「–」。

### 版式随之调整

原来 8 列横排（基因做行）已放不下，改为**两张上下叠放、以基因为列的转置表**：

- 上表 3 行：ZFIN ID / Ensembl ID (r.116) / NCBI Gene ID —— 六列取值完全相同，直观表达「标识符不变」
- 下表 10 行：4 套注释装配 + 6 套品系装配（† 标记），坐标为 `染色体:起-止`
- 图宽 7.4 in，坐标字号 4.8 pt，实测无跨列溢出

### 核查

`scripts/verify_figs2_figs3.py` 已扩展：18 个标识符格 + 12 个参考坐标格 + 6 个 GRCz12tu 格 +
36 个品系格 + S3 的 7 个计数，**TOTAL FAIL = 0**。

---

## 九、FigS2 改为「基因 ID 对照表」（2026-11-05 18:20）

### 为什么从「坐标」改成「基因 ID」

作者明确：这张图要对比的是**基因 ID**，不是坐标。论文主题是「teleost 基因命名不一致」，ID 才是核心，
坐标只是这个问题的表象之一。

### 三块结构

1. **块 1 跨数据库标识符**（稳定）：ZFIN ID / Ensembl ID / NCBI Gene ID / UniProt ID / Symbol。
   24 个 ID 全部与 `gene_basic` 表一致（核查脚本 0 失败）。
2. **块 2 NCBI 各装配染色体登录号**（变）：GRCz11=NC_007xxx → GRCz12tu=NC_133xxx → GRCz13ab=NC_141xxx。
   18 个 NC_ 号全部与 NCBI Gene esummary 的 locationhist 独立核对一致。
3. **块 3 NCBI 各装配主转录本号**（NM_ 前缀稳定）：只列 NM_ 前缀（不列版本号，避免把
   「注释版本」误当成「装配差异」）；版本升级在脚注用 cyp19a1a NM_131154.3→.4 举例说明。

### 核心结论（图的论点）

**GeneID 稳定，但染色体登录号 NC_ 和转录本版本号都随装配迭代变** —— 这正是 Hub 用 ZFIN ID 做
身份锚点、不用 NCBI 登录号或坐标的原因。

### 关键数据来源

- 跨数据库 ID：`gene_basic`（zfin_id / ensembl_id / uniprot_id）+ NCBI Gene esummary（GeneID）
- NC_ 号：NCBI Gene esummary 的 `locationhist` 字段（权威，含 GRCz11/GRCz12tu/GRCz13ab 三代）
- NM_ 号：GRCz11 GFF（jobs/GCF_000002035.6_GRCz11_genomic.gff.gz）+ GRCz12tu GFF
  （omics_structured/12_GRCz12_backup/GRCz12tu/GCF_049306965.2_GRCz12tu_genomic.gff.gz）

### 发现的额外事实（可写进正文）

- 六基因的 GeneID 在 GRCz11→GRCz12tu→GRCz13ab 三代参考装配里完全不变（30590/30399/30269/30538/30390/798441）
- Ensembl ID 也基本稳定，但 symbol 会变：mstnb 在 Ens54 叫 mstn，Ens77 起才叫 mstnb；tbxta 在 Ens80 叫 ta
- tbxta 在 Zv8/Zv9（Ens54/79）老注释里都没有记录（无 Zv9 注释，与上一版坐标表结论一致）
