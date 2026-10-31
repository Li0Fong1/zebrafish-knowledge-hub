# METHODS 声明—实现一致性审计（2026-10-01）

回答一个具体问题：*METHODS 里写的，是不是完全按实际情况来的？*

方法：把 METHODS 里每条可核查的技术声明，逐条回到本仓库的实现/产物里找证据。
结论分三档：**✅ 已核实** / **🔧 已按实现改写** / **⚠️ 需作者确认**（人工环节或外部平台事实）。

---

## 一、已核实（有代码或产物为证）

| 声明 | 证据 | 答案 |
| --- | --- | --- |
| 文献来自 PubMed / Europe PMC / **Web of Science Core Collection** | `rag/wos_collector.py`（Clarivate WoS Starter API + RIS 导入）、`rag/multi_literature_collector.py`（WoS+PubMed 编排）、`rag/europepmc_collector.py`、`agent/hub_maintenance.py`（7 天增量含 WoS）；`merged_corpus/merged_corpus/merged_*.ris` 共 101 个文件，抽查前 12 个即 12,000 条记录且带 `AN - WOS:000280426400297` 这类 WoS accession | ✅ 确实用了 WoS |
| 筛选标准句："whether the study primarily uses zebrafish as the research subject or model" | `scripts/judge_literature_zebrafish_batch.py` 的判定提示词里含逐字相同的英文标准：`Is this study PRIMARILY using zebrafish as the research subject/model? Mention-only or control/background...` | ✅ 逐字一致 |
| 存在**确定性规则引擎**（正则编码同一标准） | `scripts/judge_hybrid_triage.py`：`TriageTier = Literal["clear_yes","clear_no","gray"]`；`scripts/kday_rescreen_relaxed8.py`：`rule_yes` / `rule_no_neg` / `rule_no_empty`（含 `模式生物\|animal model\|model organism` 等正则） | ✅ 存在 |
| 无摘要/信息不足的记录**不自动剔除**，留待人工 | `NO_ABSTRACT_LABEL = "No abstract"` + 判定结果枚举 `是 | 否 | 无法判别`；`judge_quality_guard.py` 对结构异常（无正文/XML 损坏）记 `无法判别` | ✅ |
| 全文以 PDF 或 PMC XML 获取 | `rag/fulltext_cascade.py`、`scripts/yes_md_fulltext_cascade.py`、`rag/online_fulltext_fetch.py` | ✅ |
| 提取工具与**版本号**：PyMuPDF 1.28.0 / pypdf 6.13.3 / pdfplumber 0.11.10 / lxml 6.1.1 / FastAPI 0.137.2 / Uvicorn 0.49.0 | 与本地 venv 实测版本 **6/6 完全一致** | ✅ |
| 清洗 6 条规则 + 位置门控（refs >35%、ack >25% 且落在后半） | `rag/text_clean.py`、`rag/pdf_fulltext_align.py`（此前会话已逐条核对） | ✅ |
| 章节权重 + `adjusted_L2 = raw_L2 / weight` | 检索实现与 `_audit_sources.json` 一致 | ✅ |
| 规模数字：32,532 篇 / 7,651,942 chunks / 170 分片 / 2048 维 | `Figure/_audit_sources.json`；`verify_methods_numbers.py` **PASS**。**2026-10-09 更正**：先前写的“线上服务没有指向索引／实际 0 分片”**是误判**——`$BASE/.env` 里 `FAISS_PUBMED_YES_DIR` 正指向 `<RT>/faiss_pubmed_yes_qwen3_4b_v2_merged`（`isdir=True`、`shard_*/index.faiss=170`），而 `.env` 由 `core/entry.py` 在启动时写入 `os.environ`，故 `/proc/<pid>/environ` 里查不到（先前用不带 `.env` 的 `python -c` 探测，只看到不存在的回退默认值）。真正的限制有两层：① **`rag_builder` 重复导入导致 4B 嵌入模型被加载两份**（2026-10-09 已修：缓存改为 `sys` 级共享，新会话加载计数 = 1）；② 全量索引 63 GB > 用户 slice `memory.max = 36 GB`，线上只能常驻 **36/170 分片（约 21%）**，返回体以 `partial:true` 明示。线上实测 `POST /api/retrieval/debug` 返回 `ready:true / shards_loaded:36 / shards_total:170 / partial:true`，500 候选 → 重排 100 条。 | ✅ 数字属实；✅ 线上已接入（**部分分片，已明示**） |
| 双评委 = `deepseek-chat` 与 `deepseek-reasoner`，逐条 Spearman 0.754 | `Figure/_config_sweep_scores.json`（`"model": "deepseek-chat"`, 60 行）与 `Figure/_config_sweep_scores_reasoner.json`（`"model": "deepseek-reasoner"`, 60 行）；`_config_sweep_verify.json`：F0 覆盖 60/60、F1 `Spearman=0.754` | ✅ 属实（且 METHODS 已明说是 LLM-as-a-judge，非冒充人工评委） |
| 盲标 A–F（确定性种子）+ 评委看不到配置名 | `logs/eval_isomorph/blind_pack.json` + `_blind_label_secrets.json`（每对的 A/B 归属不同） | ✅ |
| 精确置换检验（Mann–Whitney 全 C(20,10)；Wilcoxon 全 2^n 枚举） | `_config_sweep_verify.json` 的 D1（|ΔP|≤0.02，实测最大 0.0031）、D2（最大 0.0000） | ✅ |

## 三、2026-10-09 索引完整性核查（新增）

| 核查项 | 证据 | 结论 |
| --- | --- | --- |
| 活跃索引清单 | `faiss_pubmed_yes_qwen3_4b_v2_merged/manifest.json`：`status=ready`、`source_status=ready`、`index_version=qwen3_4b_v2_short_section`、`embedding_dim=2048`、`total_papers=32532`、`total_chunks=7651942` | ✅ 与 METHODS 口径一致 |
| 分片完整性 | 170/170 分片含 `index.faiss`+`index.pkl`；0 字节文件 0 个；manifest 分片号与目录一一对应（无缺无多）；合计 58.4 GB | ✅ 无损坏、无缺失 |
| 合并链 | 170 片由源索引 `_v2`（2,711 片）按 `group_size=16` 合并，manifest 逐片记录 `src_shards` | ✅ 可追溯 |
| 线上文献检索 | `POST /api/retrieval/debug` → `ready:true`、`shards_loaded=32/170`、`partial:true`、500 候选 → 重排 100 条 | ✅ 可用（**部分分片，已明示**；受 36 GB 上限约束） |
| 线上知识库 RAG | `POST /api/chat/stream`（前端同路径）实测 7.8 s / 811 字真实回答，含 ENSDARG00000029688、ZDB-GENE-990415-91、Bgee 表达分 | ✅ `data/faiss_index` 与 4B 模型维度/模型一致 |
| 库-索引差异 | DB 32,549 行 vs 索引 32,532 篇 = 17 篇（0.05%）：10 行为 `gold_status=pdf_only/pdf_fallback_not_gold`（设计上不入索引），余 ~7 篇疑似切分 0 块；快照后无新增 | ⚠️ 差异稳定且已知，不随时间扩大 |
| 磁盘冗余 | `$RT` 下 6 个 `faiss*` 目录共 163 GB（活跃 62 GB）；`/data` 余 753 GB | ✅ 保留不删（多处脚本仍引用 `_v1/_v2`） |
| 部署树漂移 | 服务器缺 `agent/intent_router.py`（已补传）；6 个文件与仓库**双向**不一致（服务器含 `ROUTE_A_*` 热修，仓库含新功能） | ⚠️ 待人工 3-way 合并；`api_server.py` 已加优雅降级，`/api/chat` 不再 500 |

## 二、已按实现改写（原文与代码不符，已修正）

**2.1 第二段原写法**：

> "Screening was performed independently twice — once by a deterministic rule engine …, and once by a reasoning LLM (`deepseek-reasoner`). … a study was kept when it was present in the results of **either** pass (union policy) …"

**与实现不符的两点**：

1. **不是"两次独立打全量 + 取并集"**，而是 **规则优先的混合分档（triage）**：`judge_hybrid_triage.py` 把每条记录分成 `clear_yes / clear_no / gray`，**clear 两档由规则直接定**，只有 `gray` 灰带交给 LLM（省 token，标准一致）。另有一道 `judge_quality_guard.py` **禁止静默降级**：LLM 调用失败、或某条仍带着"来自规则的裁定"，就把该条**重新入队**，而不是默认按规则结论成交。
2. **筛选用的不是 `deepseek-reasoner`**。全仓库搜 `deepseek-reasoner` 只命中评分/出图脚本（`scripts/score_config_sweep.py`、`scripts/make_figures_pipeline_compare.py`、`Figure/supplementary/README.md`）与本文档本身；筛选脚本里没有该模型名。

**改写后**（`docs/METHODS/METHODS.md` 2.1，已生效）：如实写成 rule-first hybrid triage + 灰带交 LLM + 质量守卫禁止静默降级 + 记录也出现在 WoS 导出时**以 PubMed 裁定为准**（`scripts/resolve_merged_judgement_conflicts.py`，WoS 作参考），并保留"无摘要不自动剔除"与"剩余分歧人工复核"。

## 三、需作者确认（我无法从仓库核实）

| 项 | 为什么需要你确认 |
| --- | --- |
| "the disagreements and not_classifiable records were **re-examined manually**" | 人工动作，仓库里看不到。若实际没有系统化人工复核，这句要改成"人工抽查 N 条"。 |
| `deepseek-reasoner` 是否也参与过筛选的某次重跑 | 代码里没有；如果你在服务器上曾用 env 覆盖模型跑过筛选，请告知，我把这句补回去。 |
| **API 用量/成本**（278,649 请求 / 1,140,233,467 tokens，按用途拆分） | 来自你提供的 DeepSeek 平台截图，`Figure/_llm_usage.json` 记录了来源；属外部平台事实。 |
| **MySQL 8.0** | ✅ **已核实为 8.0.36**。服务器原始输出：`…/mysql80/bin/mysql  Ver 8.0.36 for Linux on x86_64 (MySQL Community Server - GPL)`，见 `项目阶段/05_数据与库/快照/_db_bad_title.txt`、`项目阶段/04_运维静音自愈/证明归档/_eno_title_check.txt`；从服务器导出的 dump 亦记 `Server version 8.0.36`。⚠️ 本机 Windows 版是 **8.0.45**，勿混用。 |
| **BLAST+ 2.17.0+** | ✅ **已核实为真（2026-10-02 上机实测）**。服务器原始输出：`blastn: 2.17.0+`、`makeblastdb: 2.17.0+`，conda 包 `blast 2.17.0 hb02a186_1 bioconda`，二进制 `<RT>/conda_blast/bin/blastn`（`Aug 22 22:17`）。留档：`docs/paper/data_package/05_provenance/tool_versions.json`（由 `scripts/probe_tool_versions.py` 生成，同批复核 `mysqld 8.0.36`、`Python 3.10.20`）。**结论：METHODS 保留 `BLAST+ v2.17.0+` 有据可依。** ~~原 ⚠️ 说明（保留以备复盘）~~：引擎确为 `NCBI BLAST+ blastn`（`logs/_blast_probe2.txt` 的 API 实拍 + `BLAST_BIN=$RT/conda_blast/bin`）；**随仓库分发**的本地包确为 2.17.0+（`tools/blast+/ncbi-blast-2.17.0+/ncbi_package_info`：`blastn: 2.17.0+`、`Package: blast 2.17.0, build Jul 1 2025`；`scripts/setup_blast_linux.py` 亦从 NCBI LATEST 取，TUNA 直链为 `ncbi-blast-2.17.0+-x64-linux.tar.gz`）。**但服务器实际调用的 `$RT/conda_blast` 是 bioconda 未锁版本安装**（`_conda_blast_env.py`：`conda create -p … -c bioconda -c conda-forge blast`），其版本号**代码从未记录** —— `database/seq_toolkit.py:354` 的 engine 串是硬编码 `"NCBI BLAST+ blastn"`。二选一：① METHODS 改为不带版本号（零风险）；② 上机核实后回填，用 `scripts/probe_tool_versions.py`（已备好，SSH 恢复即可跑）。 |
| `<Author B>` 收集/整理了组学注释数据 | METHODS 已标注 "to be confirmed"，需当事人确认署名与分工。 |

## 四、复算入口（任何人可重跑）

```bash
.venv\Scripts\python.exe -X utf8 scripts\verify_methods_numbers.py --write   # → PASS
.venv\Scripts\python.exe -X utf8 scripts\build_figshare_package.py --check    # → 36 files, 0 problem(s)
```
