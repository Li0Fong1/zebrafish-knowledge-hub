# 补充图 S4 / S5

两张图按「**做减法**」重做：流水线只保留「一个节点 = 一个图标 + 一句标签」；
对比图直接使用**真实界面截图**，不再用生成的文字面板。所有数值与文本均来自本项目真实产物。

| 文件 | 尺寸 | 对标 | 结构 |
| --- | --- | --- | --- |
| `FigS4_Pipelines_EN.png` | 13.4 × 3.9 in · 300 dpi（4080 × 1230 px，2026-10-08 改为参考图格式后收紧） | 参考图 Fig. B（英文版） | (A) 论文清洗与向量化 + (B) 斑马鱼知识提问与作答生成，各一行**左→右** |
| `FigS5_QA_Comparison.png` | 12.4 × 3.9 in · 300 dpi（3780 × 1217 px） | 参考图 Fig. S5 | 提问条 + **三张真实截图**并排（ChatGPT / DeepSeek / Zebrafish Hub），文案英文 |
| ~~`FigS4_Literature_Pipeline.png`~~ | 12.4 × 4.6 in | 旧中文版 (a)–(j) 蛇形流水线 | 已由英文版取代；需要时运行 `make_figures_pipeline_compare.py` 可再生 |

### 英文版 FigS4_Pipelines_EN.png

用词取「**平常词、见名知意**」原则（例：文献解析 → literature parsing，不用抽象词）：

- **(A) Pipeline of paper cleaning and vectorization**：Literature texts (PDF / XML full text)
  → Paragraph cleaning (remove non-scientific paragraphs) → Clean texts (46,904 of 56,122 characters)
  → Vectorize texts (Qwen3-Embedding-4B · 2,048-d) → Store in FAISS (170 shards)
  → Knowledge database (32,532 papers · 7,651,942 chunks)
- **(B) Pipeline of query and answer generation for zebrafish knowledge**：User input (multi-turn session)
  → Rewrite query (DeepSeek · context-aware) → Vectorize query (same 2,048-d model)
  → Retrieve texts (top 500 candidates) → Rerank texts (cross-encoder · top 100)
  → Summarize (Qwen3 · 48,000-char input) → Generate answer (cited, grounded answer)

两个脚注分别记录真实流程事实：段落清洗在切分前切除参考文献/致谢/作者行
（触发率 34.1% / 47.8%），切分窗 360 / 110 字符、重叠 48（`Figure/_clean_stats.json`）；
(B) 的检索读取 (A) 建成的知识库（32,532 篇 / 7,651,942 块 / 170 分片），答案带行内引用。

> **防坑机制（曾踩过）**：图标在单位空间绘制，必须经 `icon_at()` 注入等比缩放。
> 曾因在 `block()` 之外直接调用图标函数，图标按单位尺寸（0.66×0.60 整幅坐标）
> 画满全图并把 B 面板中间 5 个节点的文字白底盖住（图上只剩 `User input` 和残字 `R`/`D`）。
> 现在有三道防线：`_it()` 缺变换即报错、`_sanity_artists()` 扫超画布 30% 宽的图形、
> `_raster_ink_check()` 在 PNG 上逐条验证 26 个标签/子说明确有墨迹。

---

## FigS4 节点与读数来源

| 节点 | 标签 | 读数 | 来源 |
| --- | --- | --- | --- |
| (a) | PubMed 检索导出 | 32,549 条记录 | MySQL `literature_paper` |
| (b) | 题录合并去重 | 标题 / 摘要 / DOI | — |
| (c) | 全文可用性筛查 | 保留 99.9% → 32,532 篇 | 索引清单 `total_papers` |
| (d) | 金标审查池 | 16,386 篇 | `_data.json` → `alignment.counts.universe` |
| (e) | 体量 / 来源门控 | 正文 ≥ 35,487 字符 | `_data.json` → `gold.gold_bar.body_floor` |
| (f) | 通过金标 QC | 11,918 篇（另册 4,468） | `alignment.counts.gold / not_gold` |
| (g) | 全文对齐 / 条目直链 | PDF · JATS-XML · HTML | `gold.gold_bar.sources_allowed` |
| (h) | 纯文本 / XML 语料 | 正文 + 规范章节 | `rag/fulltext_sections.py`（11 类章节） |
| (i) | 清洗与切分 | 去参考文献 34.1% · 去致谢 47.8% | `_clean_stats.json` → `rules.*_truncation_triggered` |
| (j) | 向量化入库 | 2048-d · 170 分片 | 索引清单 `embedding_dim` / `shard_count` |

标题条汇总：题录 32,549 条 → 入索引 32,532 篇 → 金标 11,918 篇 → 切分 235.2 块/篇
→ 总计 7,651,942 块（Qwen3-Embedding-4B，170 分片）。

---

## FigS5 用的三张真实截图

**问题（英文版文案）**：`What are the functions of the zebrafish nanog gene? (brief answers requested)`
三个系统均以**同一提问、同一“回答简短”要求**采集，截图为**未编辑原件**（仅 DeepSeek 裁去思考块）。

| 面板 | 源文件（项目根目录） | 原图 | 裁剪后 | 说明 |
| --- | --- | --- | --- | --- |
| ChatGPT | `ChatGPT.png` | 1270×565 | 863×390 | 直接取回答区 |
| DeepSeek | `DeepSeek.png` | 1072×715 | 836×336 | **裁去「已思考」推理块**，只保留最终回答 |
| Zebrafish Hub | `Zebrafish Hub.png` | 1248×654 | 1248×622 | 完整回答（含基因标识行与行内引用） |

每个面板下方的短注**只描述截图里能直接看到的东西**，不做主观评定：

- ChatGPT：6 条功能要点 · 无基因标识 / 无引用
- DeepSeek：5 条要点 + 小结 · 无基因标识 / 无引用
- Zebrafish Hub：基因标识 + 6 条要点 + 行内引用 `[n]`

> 参考的判定基准（服务器 MySQL 真实注释）仍保留在 `Figure/_nanog_facts.json`：
> `gene_id=5` · `ENSDARG00000075113` · `Q8JFR5` · 24 号染色体 · InterPro `IPR013275 Homeobox NANOG`
> · GO 27 条 · 表达 20 条 · 文献 12 篇（`Figure/_nanog_facts.json` 的 `reference` 条数；此前的 53 为旧口径）。需要时可据此恢复 7 项要点覆盖矩阵。

---

## FigS4(C) 统计图：`FigS4_Stats_EN.png`

参考图 C 比较的是「切分方式 × 检索深度」9 组配置下的生成结果得分。本项目索引的切分方式是**已固定的**
（`paragraph_section`，360/110 字符、重叠 48），没有 chunk100/chunk200 三套索引
——**该维度无法复刻，也不冒充**。改成同一件事的精神：**固定题集，改变真实可切的配置**。

| 项 | 值 |
| --- | --- |
| 配置（6 组） | 作答路线 `rag` / `fusion` / `hub` × 作答深度 `partial` / `full`（`/api/chat` 请求级真实参数） |
| 题集 | 项目自带固定题集取前 10 题（`logs/eval_isomorph/_eval_isomorph_answers.json`），每题 6 配置各跑一次 = **60 次真实调用** |
| 生成 | `scripts/collect_config_sweep.py`（增量落盘 `Figure/_config_sweep_answers.json`，可断点续跑） |
| 打分 | `scripts/score_config_sweep.py`：项目自带 7 维量表（0–10，0.25 分档），每题 6 份答案打乱标号 A–F **盲评**；解析失败整题作废，**绝不补 0** |
| 统计 | 手写**精确置换检验**（双侧）：Mann–Whitney 枚举全部 C(20,10)=184,756 种标签分配；配对 Wilcoxon 符号翻转 2¹⁰ 全枚举。出图前由 `scripts/verify_config_sweep.py` 独立复核 |
| 出图 | `scripts/make_figS4_stats.py` → 双面板（C1 量表分 / C2 行内引用去重数） |

**为什么用精确检验**：n=10 且并列极多（量表离散、引用常为 0）时正态近似不可靠——实测近似值与精确值最大差 **0.60**，所以图上的 P 值全部改成精确检验。

**出图前校验（`verify_config_sweep.py`，13 项通过 + 1 项警告 + 0 失败）**

- A 覆盖完整 / B 盲评双射且与确定性打乱一致 / C 维度合法且总分=各维度之和（曾因 `round(x,1)` 把 8.25 写成 8.2，已修）
- D 精确检验与独立蒙特卡洛一致（最大 ΔP=0.0031；Wilcoxon ΔP=0.0000）
- E 行内引用计数可由答案原文复算（0 条不符）
- F 两评委（deepseek-chat / deepseek-reasoner）逐条总分 Spearman **0.754**，四个引用对比方向全一致；
  但**配置均值排序不可复现（Spearman −0.03）**——六组均值跨度仅 0.55/10，属噪声；
- G 图上数字可由评分文件完全复算

**结果（真实）**

- **C1 量表分**：六组无显著差异（P 0.47–0.97）→ 量表**饱和**（39% 的维度六组同分，
  同题极差均值仅 2.05/10），换作答路线或深度并不改变量表分；
- **C2 行内引用去重数**：`hub` 明显更高——hub_partial 均值 12.8、hub_full 18.3，
  而 rag/fusion 只有 4.1–5.0（**2.56–3.66×**）；`fusion_full vs hub_full` **P=0.013**、`rag_full vs hub_full` **P=0.016**
  （配对 Wilcoxon 精确检验 0.0059 / 0.0078）。即：配置主要改变**引用密度**，而不是量表分；
- **多重比较**：4 个 C2 检验做 Holm 校正后，`rag_full vs hub_full` = **0.047**（仍显著）、
  `fusion_full vs hub_full` = **0.052**（边缘）；若把 C1+C2 共 11 个检验合并校正，则 P_adj = 0.14 / 0.16（不再显著）。
  图注里的这几个数字**由图脚本从数据现算后注入**（`holm_c2` / `holm_all`），不再手写，避免图注与核查分叉；
- **引用可信度（核查新增）**：hub 答案把行内 `[n]` 全部兜到文末编号文献列表
  （hub_full **10/10** 题、hub_partial 8/10；rag/fusion 4–6/10），因此 hub 的引用数是端到端可核的；
  剩下的 rag/fusion 答案返回文本里**没有**文献列表（行内仍带 `[n]`），所以 C2 计的是“标记数”而非
  “可核条目数”——图注已明确披露，不再写“cite more distinct sources”这种不可核的表述。

**被排除的配置**（实测输出为演示占位内容，不能进评分）：`answer_scope=gene_db`
（返回 `ENSDARG0000000000001`、`ZDB-GENE-990415-270` 等示例块）、`mode=general`（同样带演示块）。

自校验 `checks=6 mismatch=0`：样本量一致 / 量表满分=10 / 无作废题 / 无异常放大图形 /
标题脚注与面板不重叠 / 文字栅格墨迹。

### 出图后的独立审计：`audit_figs4c_figure.py`（31 通过 + 1 警告 + 0 失败）

审计脚本**不共用出图脚本的中间结果**，而是从答案、评委回执、PNG、几何摘要各源头重新算一遍，
分层 8 层 32 项，报告写入 `Figure/FIGS4C_AUDIT.md`、`Figure/_figs4c_audit.json`：

| 层 | 内容 | 关键结果 |
| --- | --- | --- |
| L1 溯源 | 答案/评分覆盖、评分文件可由评委回执**纯函数复算**、同题 6 份答案互不相同、无演示占位、引用计数可复算、行内编号可对应文献列表 | 全部通过；L1.7a **警告**：仅 51.7% 的答案行内编号有显式编号列表（rag/fusion 部分答案无列表） |
| L2 设计 | 每题×每配置恰好一次、两面板同批题同 n | 60/60、n=10 |
| L3 盲评 | 标签↔配置双射且与确定性打乱一致、位置无偏（最大 4/10）、回执中不出现配置名 | 均通过 |
| L4 统计 | 精确 MW vs 2 万次独立蒙特卡洛（ΔP=0.0030）、置换不变性、全等样本 P=1、Holm 校正 | 均通过；C2 面板内 Holm 后 `rag_full|hub_full` =0.047 仍显著、`fusion_full|hub_full` =0.052 边缘；11 检验合并校正后 0.14/0.16 |
| L5 稳健 | 留一法（删任一题，MW/Wilcoxon 各 20/20 仍 <0.05）、换评委 B（C1 仍全不显著，最小 P=0.092）、换指标（累计引用数同向）、效应量（HL 中位差 −16.0 / −15.0，bootstrap 95% CI 不含 0） | 均通过 |
| L6 图-数（像素级） | 用几何摘要里的坐标轴矩形 + xlim/ylim **重建数据→像素仿射映射**（与出图锚点自洽 Δ=0.00px），再用配置色抠出箱体填充像素，反解到数据空间 | 箱体 Q1/Q3 包含且内缩 ≤8.8px（中位 3.2px，= 半线宽 + 抗锯齿 + 散点半径）；中位线 Δ=0.9px；图上 P 值与比较对可复算；**支架横线端点逐条匹配到对应两箱体中心（C1 7/7、C2 4/4）**；1 个退化箱（Q1=Q3）只画中位线 |
| L7 可复现 | 重跑出图 PNG **逐字节一致**（md5 `b23fbc1d16`） | 通过 |
| L8 结论-事实 | “量表饱和”有直接证据（39% 维度同分 / 同题极差 20%）；“hub 引用更多”有文末列表支撑；“2.5–3.7×”与实测一致 | 均通过 |

复现：`.venv\Scripts\python.exe -X utf8 scripts\audit_figs4c_figure.py`（退出码 0 = 无失败项）。


---

## 复现与自校验

```powershell
# 生成两张图（约 2 秒）+ 15 项自校验
.venv\Scripts\python.exe -X utf8 scripts\make_figures_pipeline_compare.py --check

# 英文版流程图（A/B 两行，约 1 秒）
.venv\Scripts\python.exe -X utf8 scripts\make_figS4_pipelines_en.py

# 统计图 + 32 项独立审计（核查报告写 Figure/FIGS4C_AUDIT.md）
.venv\Scripts\python.exe -X utf8 scripts\make_figS4_stats.py
.venv\Scripts\python.exe -X utf8 scripts\audit_figs4c_figure.py

# 可选：重新采集 nanog 证据（Hub 生产 API + 通用模型 API）
.venv\Scripts\python.exe -X utf8 scripts\collect_nanog_evidence.py
```

自校验内容：图片存在/可解码/分辨率 ≥2800 px/非空白、**文字无遮挡重叠**（逐对包围盒）、
FigS4 十节点读数可回溯、FigS5 三张截图均已嵌入且源文件存在。结果写入 `_selfcheck_s4_s5.json`。

> 说明：这两张图**不参与** `Figure/` 下 Fig1–Fig10 的 268 项校验契约（放子目录避免 glob 冲突），
> 由自带 `--check` 独立把关；主流水线回归为 268/268、57/57、27/27 全绿。

---

## 参考格式来源（登记）

- **参考图本地文件**（第三方未刊稿，**已在 `.gitignore` 中排除，不随公开仓库分发**）：
  - `数据清洗图.png` —— 对标参考图 **Fig. S4**（A 建库管线 + B 问答管线 + C 箱线图 P 值）
  - `实例图.png` —— 对标参考图 **Fig. S5**（助手 vs 通用大模型同题对比）
  - 另见 `马铃薯AI智能体.pdf`、`Figure legend.pptx`、`海洋鱼类数据库.pdf`
- **对齐的做法**：FigS4 (A)/(B) 采用参考图的**格式**——一行节点、每节点 = 大图标 + 短标签 +
  **圆括号里的方法/读数注**、无脚注段落；节点内容与读数全部来自本项目真实产物。
  按作者要求**不做照葫芦画瓢**，因此配色、图标、措辞、节点划分均为本项目自绘（防查重）。
- **FigS5 的有意差异**：参考图是竖排「答案文本 + 批注框」，本项目改为**横向三栏未编辑真实截图**
  （作者明确要求"直接去这些大模型问个问题然后截图"）；面板短注只陈述截图可见事实，不做质量评价
  （参考图中的 "Wrong results / Fabricated…" 一类判断在本项目里改为可核事实，避免不可核的断言）。
- 若需进一步复刻参考图的具体配色/尺寸，请说明后再对齐。

## 减负记录（2026-10-08）

| 项 | 改动 | 事实影响 |
| --- | --- | --- |
| FigS4 版式 | **改为参考图 Fig. S4 的格式**：删掉两行脚注段落，读数下沉到**各自节点的圆括号注**（如 `(170 shards)`、`(split 360 / 110 chars, overlap 48; 46,904 of 56,122 characters)`） | 无（完整句子版保留在 `Figure/Legend.md` 图注） |
| FigS4 画布 | 4.6 in → **3.9 in**（4080×1230 px），去掉脚注留白 | 无 |
| FigS5 顶部说明 | 2 行 → **1 行**（`Same question · same "answer briefly" instruction · screenshots unedited (DeepSeek thinking block cropped)`） | 无 |
| FigS5 面板小标签 | 中栏 `General-purpose LLM (thinking block cropped)` → `·` 连接式，统一长度 | 无 |
| FigS5 截图 | 三张截图改为**顶部对齐**（三栏读起来是一行，不再上下错位） | 无（不裁切回答内容，仅对齐） |
| FigS4 面板标识 | 面板标题移出画面，**只留粗体字母 `A` / `B`**（对齐参考图；标题交由 `Figure/Legend.md` 图注定义） | 无（图注已写明 (A)/(B) 各是什么） |
| FigS5 短注样式 | 三栏面板短注改为**彩色 callout 胶囊**（绿/橙/蓝，随面板配色；对齐参考图 Fig. S5 的批注框视觉） | 无（措辞不变：只陈述截图可见事实，不做质量评价） |
| 审计 | 新增 `scripts/audit_s4_s5_figures.py`：出图脚本 `--check` + **独立重读数据源** + 反向查表（图上读数必须能在源产物中找到） | 24 项记录 / 0 mismatch，写入 `_selfcheck_s4_s5.json` |
