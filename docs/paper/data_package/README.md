# Zebrafish Knowledge Hub — evaluation & provenance data package

本包对应 `docs/METHODS/METHODS.md`（§2.8 Performance evaluation）与 `docs/paper/BACK_MATTER.md`
（DATA AND CODE AVAILABILITY）中承诺的可复核产物：100 题基准、盲评输入、原始输出、评分表与精确置换检验结果。

## 构建方式（可复现）

```bash
.venv\Scripts\python.exe -X utf8 scripts\build_figshare_package.py
```

原始产物**逐字节复制**（`kind=verbatim`）；CSV/Markdown 只是格式转换（`kind=derived`，
由脚本从对应源文件生成，未重算任何数值）。全部文件的 sha256 见 `MANIFEST.json`。

### 01_benchmark — 基准题与评分细则

100 题基准（含原 30 题）、20 对同构题（斑马鱼 ↔ 马铃薯）与 7 维评分细则；CSV 为便于传阅的派生视图。

| 文件 | 类型 | 字节 |
| --- | --- | --- |
| `01_benchmark/benchmark_100_questions.csv` | derived | 5434 |
| `01_benchmark/benchmark_100_questions.source.json` | verbatim | 22519 |
| `01_benchmark/benchmark_30_questions.source.json` | verbatim | 3533 |
| `01_benchmark/isomorphic_pairs.csv` | derived | 1847 |
| `01_benchmark/rubric.md` | derived | 724 |

### 02_raw_outputs — 原始输出与盲评输入

各配置的原始回答与自动指标、交给评委的盲评包，以及 A/B 解密表（用于验证盲法确实成立）。

| 文件 | 类型 | 字节 |
| --- | --- | --- |
| `02_raw_outputs/blind_label_secrets.json` | verbatim | 1203 |
| `02_raw_outputs/blind_pack.json` | verbatim | 121695 |
| `02_raw_outputs/blind_scoring_sheet_50.md` | verbatim | 63133 |
| `02_raw_outputs/config_sweep_answers.json` | verbatim | 307822 |
| `02_raw_outputs/eval_isomorph_50_answers.json` | verbatim | 165642 |
| `02_raw_outputs/eval_isomorph_50_result.json` | verbatim | 160556 |
| `02_raw_outputs/eval_isomorph_answers.json` | verbatim | 71101 |
| `02_raw_outputs/eval_isomorph_result.json` | verbatim | 114260 |
| `02_raw_outputs/judge_raw_deepseek-chat.jsonl` | verbatim | 10962 |
| `02_raw_outputs/judge_raw_reasoner_p1.jsonl` | verbatim | 2267 |
| `02_raw_outputs/judge_raw_reasoner_p2.jsonl` | verbatim | 2239 |
| `02_raw_outputs/judge_raw_reasoner_p3.jsonl` | verbatim | 2262 |
| `02_raw_outputs/judge_raw_reasoner_p4.jsonl` | verbatim | 2246 |
| `02_raw_outputs/judge_raw_reasoner_p5.jsonl` | verbatim | 2175 |

### 03_scoring — 评分表

评委逐维打分明细（JSON 为原始文件，CSV 为同一数据的扁平视图）。

| 文件 | 类型 | 字节 |
| --- | --- | --- |
| `03_scoring/config_sweep_scores.csv` | derived | 3022 |
| `03_scoring/config_sweep_scores.json` | verbatim | 16536 |
| `03_scoring/config_sweep_scores_reasoner.json` | verbatim | 16780 |
| `03_scoring/config_sweep_scores_reasoner_p1.json` | verbatim | 3498 |
| `03_scoring/config_sweep_scores_reasoner_p2.json` | verbatim | 3490 |
| `03_scoring/config_sweep_scores_reasoner_p3.json` | verbatim | 3495 |
| `03_scoring/config_sweep_scores_reasoner_p4.json` | verbatim | 3499 |
| `03_scoring/config_sweep_scores_reasoner_p5.json` | verbatim | 3509 |

### 04_statistics — 统计检验与图统计

精确置换检验结果与一致性检查；FigS4 汇总统计与其像素级审计记录。

| 文件 | 类型 | 字节 |
| --- | --- | --- |
| `04_statistics/config_sweep_verify.json` | verbatim | 5006 |
| `04_statistics/config_sweep_verify_summary.md` | derived | 2163 |
| `04_statistics/figs4c_audit.json` | verbatim | 8934 |
| `04_statistics/figs4c_summary.json` | verbatim | 6102 |

### 05_provenance — 数值出处

论文正文中每个数字到仓库产物的对应关系：服务器/索引快照、清洗触发率、LLM API 用量。

| 文件 | 类型 | 字节 |
| --- | --- | --- |
| `05_provenance/audit_sources.json` | verbatim | 5350 |
| `05_provenance/clean_stats.json` | verbatim | 2051 |
| `05_provenance/llm_usage.json` | verbatim | 3159 |
| `05_provenance/server_env_probe.json` | verbatim | 1061 |
| `05_provenance/tool_versions.json` | verbatim | 1787 |

## 复算与核对

```bash
# 重算 METHODS 中的全部数字并与论文文本比对（含占位符检查）
.venv\Scripts\python.exe -X utf8 scripts\verify_methods_numbers.py --write
# 图 S4 的像素级审计（几何反演 + 图-数一致性）
.venv\Scripts\python.exe -X utf8 scripts\audit_figs4c_figure.py
```

## 许可与来源

- 从公开库下载的注释数据（Ensembl GRCz11 r116、NCBI RefSeq GRCz12tu、ZFIN、Bgee v15.2、EBI Expression Atlas E-ERAD-475）
  遵循各库自身条款；本包只包含**我们自己的派生结果**，不重新分发原始数据库文件。
- 文献题录来自 PubMed / Europe PMC；本包只含 PMID 级引用与统计，不含全文。
- 基准题、评分细则、原始回答与统计结果默认以 CC BY 4.0 发布（如与期刊政策冲突，以期刊要求为准）。

## 待补（不编造，留占位）

- `{{TODO:data_deposit}}`：上传本包后从 Figshare 回填 DOI
- 代码仓库：正文已按“出版时公开”处理（*The code will be released upon publication.*），
  正式 Gitee/GitHub 地址待公开后回填，故本包不再保留仓库地址占位符。

## 本次未能纳入的文件（如实记录，未构造替代品）

- `logs/_config_sweep_judge_raw_reasoner.jsonl` → 02_raw_outputs/judge_raw_deepseek-reasoner.jsonl（empty）

_生成时间：2026-10-09 08:07:21_
