# 斑马鱼知识中枢 · 图集与图注方案（Figure set & legends）

本目录为项目产出的一套**论文级图集 + 图注 + 演示文稿**，版式参照 `Figure legend.pptx`
（每页 = 一幅 Figure + 一句英文图注）与马铃薯知识中枢论文的图表体系，但在**内容、数据、
版式与配色上全面差异化**，避免与参考材料雷同。

---

## 一、设计目标

| 目标 | 落实方式 |
| --- | --- |
| 图表规范对标参考 deck | 连续编号 `Fig. 1–10`、`Figure N.` 式图注、`(A)(B)(C)` 多面板、行内标注统计量 |
| 数据不虚构 | 每个数值都取自服务器 MySQL 精确 `COUNT`、`accept_latest.json`、金标抽检、向量化实测与验收报告 |
| 版式差异化 | 按内容自适应（漏斗 / 管线 / 时间轴 / 覆盖率 / 成本分布），非参考材料的"横向三面板"重复 |
| 视觉差异化 | 青绿–靛蓝学术配色（呼应 Hub 品牌色），替代参考材料的灰黑白单色系 |
| 中英双语可用 | 图内标注英文（期刊规范），图注中英对照（`Legend.md` / PPT 内页） |

---

## 二、图集清单

| Fig. | 文件 | 面板与内容 | 数据来源 |
| --- | --- | --- | --- |
| 1 | `Fig1_Hub_Architecture.png` | (A) 三模块架构与答案契约 (B) 双锚定与标识符覆盖 (C) 策展注释资源规模 | MySQL 精确计数；架构取自代码链路 |
| 2 | `Fig2_Corpus_QC.png` | (A) 语料构建漏斗 (B) 两语料池金标合格率 (C) 质控门槛与复核稳定性 | `accept_latest.json`、`金标抽检_samples.json` |
| 3 | `Fig3_RAG_Pipeline.png` | (A) 离线建库管线 (B) 在线问答管线与答案契约 | 服务器索引清单（32,532 篇 / 7,651,942 段 / 170 分片）+ 运行实测参数 |
| 4 | `Fig4_Benchmark_Design.png` | (A) 100 题基准构成 (B) 七维评分量表 (C) 检索保真度探针 | `eval_isomorph_100_20260901.json`、`_retrieval_probe.json`（生产索引实测） |
| 5 | `Fig5_Acceptance_Suite.png` | (A) 九层验收断言分布 (B) 各层运行成本 | `acceptance_20260927_*.json`（3 份合并） |
| 6 | `Fig6_Gene_Normalisation.png` | (A) 基因实体归一化流程 (B) 基因记录覆盖率 (C) 每基因注释资源 | MySQL 精确计数 + `name_*` 表 |
| 7 | `Fig7_Toolkit.png` | (A)(B)(C) 三个工具 (D) 支撑资源精确记录数 | MySQL 精确计数 |
| 8 | `Fig8_Assembly_Lineage.png` | (A) 参考基因组组装沿革 (B) 标识符歧义成因与解析层 | 组装注册表（DB 表 + 常量回退）+ 别名池统计 |
| 9 | `Fig9_Text_Cleaning.png` | (A) 六阶段清洗与切分流水线 (B) 规则集与实测触发率 (C) 实测清洗效果 | `_clean_stats.json`（n=999 金标全文抽样，seed 42，`scripts/measure_clean_effect.py`） |
| 10 | `Fig10_Example_Case.png` | (A) 本中枢回答 (B) 通用大模型回答 (C) 八项事实要点覆盖矩阵 | 真实运行日志 `logs/_a3_answer.txt` + 同题实调 API 输出 `logs/_llm_compare_deepseek.txt` |

---

## 三、关键真实数值（可复核）

| 指标 | 数值 | 出处 |
| --- | --- | --- |
| 策展基因（`gene_basic`） | 26,321 | MySQL `COUNT(*)` |
| 携带 Ensembl 标识 | 25,432（96.6%） | MySQL `COUNT` |
| 映射至 GRCz12tu | 22,628（86.0%） | `gene_assembly_xref` |
| 基因别名 | 145,439 | `gene_alias` |
| 跨库交叉引用 | 80,212 | `gene_xref` |
| 基因–文献关联 | 161,298（覆盖 12,882 基因） | `gene_reference` |
| GO 注释 | 110,966 | `gene_go` |
| KEGG 通路链接 | 36,959（1,520 条通路） | `gene_kegg` |
| 表达记录 | 319,344 | `gene_expression` |
| 文献记录 | 32,549 | `literature_paper` |
| 金标审查宇宙 / 通过 | 16,386 / 11,918 | `accept_latest.json` |
| 入索引论文 / 语义块 | 32,532 / 7,651,942 | 服务器索引清单 `_audit_sources.json` |
| FAISS 分片 / 嵌入维度 | 170 / 2048 | 同上（Qwen3-Embedding-4B，`paragraph_section`） |
| 检索参数 | cand=500 · top=100 · 上下文 48,000 字符 | 服务器 `.env`（与线上一致） |
| 检索探针（生产索引） | 自身排名 14–56，中位耗时 16.8 s | `_retrieval_probe.json` |
| 清洗抽样实测 | 56,122 → 46,904 字符（−17.35%）· 193 段/篇 · 段均 289 字符 | `_clean_stats.json`（n=999，seed 42） |
| 清洗规则真实触发率 | 参考文献截断 34.1% · 致谢截断 47.8%（叠加位置门控） | 同上 `rules.refs_truncation_triggered` / `ack_truncation_triggered` |
| 语料单篇上限命中 | 7.2% 的正文受 120,000 字符上限截断 | 同上 `corpus.cap_hits_pct` |
| 跨抽样稳定性 | 独立种子下正文均值极差 3.41% | 同上 `stability_spread_pct` |
| 生产索引口径（勿混用） | 7,651,942 块 / 32,532 篇 = 235.2 块/篇（章节感知切分） | 服务器 `manifest.json` |
| 图集自洽校验 | 268 项断言 · 0 错误 · 0 告警 | `verify_report.json` |
| 三方审计（图 ↔ 本机 ↔ 服务器） | 27 项断言 · 0 差异 | `AUDIT_DIFF.md` |
| 基准题量 | 100 题 + 20 对同构题 | `eval_isomorph_100_20260901.json` |
| 评分量表 | 7 维 / 10 分 | 同上 `rubric` |
| 验收断言 | 197 项 · 0 错误 · 3 条建议性告警 | 3 份验收报告合并 |
| 图集与演示文稿 | 10 幅图 · 双 14 页 PPT（中英） | `_legends.json` / `build_ppt.py` |

---

## 四、交付物

```
Figure/
├── Fig1_Hub_Architecture.png      # 10 幅 300 dpi 论文级图（11.6–11.9 in 宽）
├── Fig2_Corpus_QC.png
├── Fig3_RAG_Pipeline.png
├── Fig4_Benchmark_Design.png
├── Fig5_Acceptance_Suite.png
├── Fig6_Gene_Normalisation.png
├── Fig7_Toolkit.png
├── Fig8_Assembly_Lineage.png
├── Fig9_Text_Cleaning.png         # 数据清洗图（实现级流水线 + 实测触发率与效果）
├── Fig10_Example_Case.png         # 实例图（同题双系统回答 + 事实要点覆盖矩阵）
├── Legend.md                      # 图注全文（英文 + 中文对照，含图集索引表）
├── 斑马鱼知识中枢_图集与图注.pptx       # 14 页：封面 + 总览 + 10 图页 + 数据来源 + 图注附录
├── 斑马鱼知识中枢_图集与图注_中文版.pptx # 同上（中文版）
└── _*.json                        # 绘图数据快照（可追溯、可复现）
```

---

## 五、两张新增图的生成方法

### Fig 9 数据清洗图

不用示意图，而是**从实现里量出来**：

1. `scripts/measure_clean_effect.py` 从 `解析文件/yes_fulltext_gold/batch_*/papers/*.json`
   随机抽 999 篇金标全文（固定 seed=42；语料为持续增长的活语料，故同时记录 `sample_digest`）；
2. 对每篇依次调用项目真实函数：`rag.text_clean.clean_scientific_text`（参考文献/致谢位置门控截断、
   作者与 COI 行剔除）、`rag.pdf_fulltext_align.clean_figure_artifacts`（面板字母、出版社页脚、
   发育阶段标签、色带碎片）、`rag.paragraph_split.split_natural_paragraphs`（360/110/48）；
3. 输出 `Figure/_clean_stats.json`：字符数分布、段落数分布、各规则触发率、规范章节覆盖计数；
4. `fig9()` 把「流水线阶段」「规则 + 触发条件 + 实测触发率」「实测效果」三个面板直接绑定到该 JSON。

### Fig 10 实例图

用**同题双系统对照 + 事实要点矩阵**代替参考图的「贴答案 + 批注框」：

1. (A) 栏取自本中枢真实运行日志 `logs/_a3_answer.txt`（生产链路输出，未经人工改写）；
2. (B) 栏为以同一问题实调通用模型 API 的真实返回 `logs/_llm_compare_deepseek.txt`；
3. (C) 栏把答案拆成 8 个**可独立核实**的事实要点（版本命名、年份、RefSeq 接入号、Ensembl 注释版本、
   版本谱系、品系、双锚定策略、条目级引用），逐项判定 `完整 / 部分 / 缺失`，给出 `8/8` 与 `0/8` 的量化结论。

---

## 六、复现方式

```powershell
# 1. 采集真实数据（读项目内评测产物 + 服务器 MySQL 快照）
.venv\Scripts\python.exe -X utf8 scripts\collect_figure_data.py

# 2. 测量清洗效果（Fig 9 数据源；需本地金标全文语料）
.venv\Scripts\python.exe -X utf8 scripts\measure_clean_effect.py --n 1000 --seed 42 --stability-seeds 7,2026

# 3. 渲染图集（可指定子集，如 --only 9,10）
.venv\Scripts\python.exe -X utf8 scripts\make_figures.py

# 4. 组装 PPT（中英各 14 页）与图注
.venv\Scripts\python.exe -X utf8 scripts\build_ppt.py --lang both

# 5. 自洽校验 + 与本机/服务器三方审计
.venv\Scripts\python.exe -X utf8 scripts\verify_figures.py
.venv\Scripts\python.exe -X utf8 scripts\audit_figures_vs_project.py
.venv\Scripts\python.exe -X utf8 scripts\fix_figure_drift.py --gate
```

依赖：`matplotlib`、`numpy`、`python-pptx`（均已在本项目 `.venv` 中）。

---

## 七、与参考材料的差异（查重规避）

| 维度 | 参考材料 | 本图集 |
| --- | --- | --- |
| 编号体系 | `Fig 1` + 补充图 `S1–S9` 分册 | 连续 `Fig. 1–10` 单册 |
| 面板版式 | 横向三面板重复出现 | 漏斗 / 管线 / 时间轴 / 覆盖率 / 成本分布 / 规则表 / 覆盖矩阵，逐图自适应 |
| 配色 | 灰黑白 + 单色强调绿 | 青绿–靛蓝主色阶 + 琥珀/玫红语义强调 |
| 指标类型 | 体重预测 R²、形态学标志点、分组箱线图 | 语料质控、清洗触发率、检索保真、验收断言、注释覆盖率、要点覆盖矩阵 |
| 图注句式 | `Figure N. 描述 (R² =0.9057)` | `Figure N. 描述 (A)… (B)… (C)…`，每面板独立可读 |
| 清洗图 | 抽象示意图（无实测值） | 每条规则均附**实测触发率**、抽样规模（n=999）与语料上限披露 |
| 实例图 | 答案截屏 + 批注箭头 | 双系统同题对照 + 8 项可核实要点矩阵（8/8 vs 0/8） |
