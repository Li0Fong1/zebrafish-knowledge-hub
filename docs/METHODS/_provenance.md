# METHODS 内部记录（**不属于论文正文**，投稿时不要外发）

本文件收录从正文里搬出来的工程性内容：写作口径、归属声明、数字对账表、待补清单、尾段去向。
对账脚本 `scripts\verify_methods_numbers.py` 从这里读「Numbers and provenance」表。

## 写作口径

> 写作口径：本文件严格参照《马铃薯 AI 智能体》原文 METHODS 的小节结构与叙述方式（`docs/paper/_potato_methods_clean.txt`）。
> 数值纪律：正文中每个数字都在文末「Numbers and provenance」表里给出仓库内的可核对产物；未核实的一律写成 `{{TODO:xxx}}`，**不得编造**。


## Authorship scope（归属声明）

**Author list / Affiliations（题名页用）**

> Kaifeng Qu^1^ · \<Author B\>^2^（待补）
> ^1^ College of Life Sciences, Hunan Normal University, Changsha 410081, China
> ^2^ \<合作者单位\>（待补）
> Correspondence: Kaifeng Qu（\<email\> 待补）

| Owner | 负责内容 | 对应小节 |
| --- | --- | --- |
| **Kaifeng Qu**（Author A） | **斑马鱼 AI 智能体框架搭建**：功能实现、全部智能体相关部分（前端、后端、数据库初始创建）、向量化、文件解析、性能评测、本文写作 | 2.1、2.2、2.4、2.5、2.6、2.7、2.8 |
| **\<Author B\>**（待补） | **组学数据库**（Ensembl/NCBI/ZFIN/Bgee/Expression Atlas 注释数据的收集与整理） | 2.3 中标注部分 |

> 说明：2.3 节分成两段——**（i）智能体侧的库表创建、导入注册与跨库命名解析层**属 Author A；**（ii）组学注释数据本身**属 Author B。


## Numbers and provenance（每个数字的出处，供对账脚本使用）

| 占位符 | 值 | 出处（仓库内可核对产物） |
| --- | --- | --- |
| `papers_ingested` | 32,532（入索引） | `Figure/README.md`「入索引论文」；服务器 `manifest.json` |
| `papers_indexed` | 32,532 | 同上 |
| `chunks` | 7,651,942 | 同上 |
| `faiss_shards` | 170 | 同上 |
| `embed_dim` | 2048 | 同上（Qwen3-Embedding-4B） |
| `chunk_size` / `chunk_overlap` | 360 / 48 | `core/config.py`：`PUBMED_YES_CHUNK_SIZE` / `PUBMED_YES_CHUNK_OVERLAP` |
| `cand` / `topn` / `pack_chars` | 500 / 100 / 48,000 | `core/config.py`：`PUBMED_YES_RETRIEVE_CANDIDATES` / `_RERANK_TOP_N` / `_PACK_CHARS` |
| `clean_sample_n` / `clean_seed` | 999 / 42 | `Figure/README.md`；`scripts/measure_clean_effect.py` |
| `refs_truncation` / `ack_truncation` | 34.1 / 47.8 | `Figure/_clean_stats.json`（`rules.refs_truncation_triggered` / `ack_truncation_triggered`） |
| `bench_n` | 100 | `项目阶段/01_金标全文/清单与验收/eval_isomorph_100_20260901.json` |
| `rubric_total` | 10 | 同文件 `rubric`（7 维） |
| `judge_spearman` | 0.754（逐条总分） | 本次评测：`Figure/_config_sweep_verify.json`（F1） |
| `approx_gap` | 0.1361（配对 Wilcoxon 正态近似 vs 精确的最大差） | 本次评测：`Figure/_config_sweep_verify.json`（`wilcoxon.*.P_normal_approx`） |
| `rate_limit` | 60 | 服务器 `.env`：`WEB_RATE_LIMIT_CHAT` |
| `req_total` / `tokens_total` | 278,649 / 1,140,233,467 | `Figure/_llm_usage.json`（DeepSeek 平台用量页实测 2026-07~09） |
| `req_jul` / `mean_jul` | 273,356 / 278 | 同上（均值 = tokens ÷ requests） |
| `req_aug` / `mean_aug` | 2,963 / 211,910 | 同上 |
| `req_sep` / `mean_sep` | 2,330 / 187,314 | 同上（9 月三个模型合计） |
| 其余 `{{TODO:*}}` | 待补 | Figshare DOI / 基金项目号 / 合作者署名与单位 / 通讯邮箱（代码仓库：已按“出版时公开”处理） |

> **待你补的只剩外部门户信息**（其余版本号/限流/参数/用量数字/作者单位已填实）：
> 1. 代码仓库地址（原 `TODO:repo_url` 占位符）—— **已定：暂不公开**，正文已写
>    “The code will be released upon publication.”；正式 Gitee/GitHub 地址待公开时回填
>    （马铃薯对应：`https://gitee.com/yuxin_2023/potato-knowledge-hub`）
> 2. `{{TODO:data_deposit}}` —— Figshare DOI（马铃薯：`10.6084/m9.figshare.29815859`）；我可以先把待上传的数据包打好
> 3. `{{TODO:funding}}` —— 基金项目名 + 项目号（**不可编造**，请提供）
> 4. 合作者姓名与单位、通讯邮箱
>
> 对账：`.venv\Scripts\python.exe -X utf8 scripts\verify_methods_numbers.py --write`
> —— 脚本会把本模板渲染成 `METHODS.resolved.md`（投稿版），并在每次运行时重新从产物重算全部数字。


# 尾段（已拆出）

尾段五节与马铃薯完全同序同名（`DATA AND CODE AVAILABILITY → FUNDING → ACKNOWLEDGMENTS → AUTHOR CONTRIBUTIONS → SUPPLEMENTAL INFORMATION`），
现已独立到 **`docs/paper/BACK_MATTER.md`**（含整篇稿件的拼接顺序说明与待补清单）。
正文文件 `METHODS.md` 现在只保留 2.1–2.8 散文（投稿正文），本文件承载其余工程性内容，避免同一段文字两个来源、改一处漏一处。
