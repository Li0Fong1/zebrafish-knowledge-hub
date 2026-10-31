# PubMed 判别「是」文献 — 下载与向量化方案

> 版本：v1.0 · 2026-07-08  
> 依据数据：`项目阶段/05_数据与库/归档/data_export/pubmed_judgement_full/pubmed_judgement_是.csv`（46,878 篇）  
> 目标：为问答体/RAG 提供可检索的斑马鱼主研究文献向量库

---

## 1. 背景与范围

### 1.1 已有资产

| 资产 | 状态 | 说明 |
|------|------|------|
| 判别结果 CSV/ZIP | ✅ 已完成 | `pubmed_judgement_是.csv`，46,878 篇 |
| MySQL `literature_pubmed_judgement` | ✅ 已完成 | 含中英标题、中英摘要、判断结果、理由 |
| MySQL `literature_pubmed` | ✅ 约 67k 篇 | 存 **原始 PubMed XML**（`raw_xml`） |
| 现有 RAG | ⚠️ 未对接 | FAISS + `bge-small-zh-v1.5`，`knowledge.d` 当前为空 |
| 向量与判别的桥接 | ❌ 缺失 | 无任何脚本读取「是」子集做向量化 |

### 1.2 本次范围

- **纳入**：`判断结果 = 是` 的全部 46,878 篇  
- **排除**：否 / 无法判别 / meta 模式无效 XML  
- **交付物**：可增量构建、可断点续跑、带 PMID 元数据的向量索引 + 文献目录

---

## 2. 总体架构

```
pubmed_judgement_是.csv / MySQL
        │
        ▼
┌───────────────────┐
│ ① 文献获取层       │  优先本地 raw_xml，缺失再 NCBI efetch / Europe PMC
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ ② 文本规范化层     │  解析 XML → 结构化字段 → 向量化用正文
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ ③ 分块层           │  RecursiveCharacterTextSplitter（512/50）
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ ④ 嵌入层           │  bge-small-zh-v1.5（与现有 RAG 一致）
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ ⑤ 向量存储层       │  分片 FAISS + chunk 元数据表（MySQL/SQLite）
└─────────┬─────────┘
          ▼
┌───────────────────┐
│ ⑥ 检索接入层       │  扩展现有 ZebraFishRAG，支持 PMID 过滤与引用
└───────────────────┘
```

---

## 3. 文献下载方案

### 3.1 三级获取策略（推荐）

| 优先级 | 来源 | 适用 | 说明 |
|--------|------|------|------|
| **P0** | `literature_pubmed.raw_xml` | 已在库中的 PMID | **零网络成本**，全库导入时已完成 efetch |
| **P1** | NCBI E-utilities `efetch` | P0 缺失的 PMID | 复用 `import_pubmed_raw_ncbi.py` 的 `_api_get`，3 req/s |
| **P2** | Europe PMC REST | NCBI 失败 / 需 PMC 全文 | 复用 `europepmc_collector.py`，国内更稳 |

**不建议**对 4.6 万篇全部重新下载：MySQL 中已有绝大部分 XML，仅对 JOIN 后 `raw_xml IS NULL` 的 PMID 补拉即可。

### 3.2 正文粒度选择（需你确认）

| 方案 | 覆盖率 | 向量质量 | 成本 | 建议 |
|------|--------|----------|------|------|
| **A. 仅摘要** | ~100% | 中 | 低 | **一期默认**：摘要已判别、已中译，够支撑基因/表型问答 |
| **B. 摘要 + PMC 开放全文** | ~30–40% OA | 高 | 中 | **二期增强**：Europe PMC `fullTextXML` |
| **C. 摘要 + 全文（含非 OA）** | 低/合规风险 | 最高 | 高 | 不推荐批量做 |

**推荐路径**：先做 **方案 A**，验证检索效果后再对 PMC OA 子集做 **方案 B** 增量升级。

### 3.3 下载脚本设计（拟新建）

```
scripts/vectorize_pubmed_judgement_yes.py
  --phase fetch|chunk|embed|merge|all
  --resume
  --source mysql|csv          # 默认 mysql
  --text-mode abstract|full   # 默认 abstract
  --batch-size 500
  --workers 4
```

**进度文件**：`data/literature/pubmed_yes_vectorize.json`  
**失败队列**：`data/literature/pubmed_yes_fetch_retry.json`

---

## 4. 向量化文本规范

### 4.1 单篇向量化输入模板

与现有 `format_article()` 对齐，并增加判别字段：

```text
【PUBMED PMID:42390631】亚慢性铜暴露改变成年斑马鱼（Danio rerio）的生长性能…
来源信息：Journal of XXX | 2024 | DOI:10.xxxx | PMID:42390631 | JUDGEMENT:是
标题（英）：Subchronic copper exposure alters…
标题（中）：亚慢性铜暴露改变成年斑马鱼…
摘要（英）：{abstract_en 前 3000 字}
摘要（中）：{abstract_zh 一句话或完整中文摘要}
```

**嵌入用主文本**（二选一，可配置）：

- `zh` 模式（默认）：中文标题 + 中文摘要 → 适配 `bge-small-zh-v1.5`  
- `bilingual` 模式：中文摘要 + 英文摘要 → 跨语言检索更好，token 略多

### 4.2 分块参数

沿用现有配置（`core/config.py`），可在批量场景微调：

| 参数 | 默认值 | 批量建议 | 说明 |
|------|--------|----------|------|
| `RAG_CHUNK_SIZE` | 512 | **768** | 摘要较长时可略增 |
| `RAG_CHUNK_OVERLAP` | 50 | **80** | 减少基因名/通路被截断 |
| 分隔符 | LangChain 默认 | `["\n\n", "\n", "。", "；", " "]` | 中文友好 |

**估算 chunk 数**（摘要模式）：

- 平均摘要 ~1,500 字符 → 约 **2 chunks/篇**  
- 46,878 篇 × 2 ≈ **9.4 万 vectors**

---

## 5. 嵌入与向量库

### 5.1 嵌入模型

| 项 | 选择 |
|----|------|
| 模型 | `models/bge-small-zh-v1.5`（与现 RAG 一致） |
| 维度 | 512 |
| 设备 | CPU（可环境变量切 GPU） |
| 归一化 | `normalize_embeddings=True`（与 `BgeEmbedding` 一致） |

**批量推理**：batch_size=64，避免 9 万条逐条 encode 过慢。

### 5.2 向量存储（推荐：分片 FAISS + 元数据表）

现有单体 FAISS 全量 rebuild **无法承载 4.6 万篇**（内存、构建时间、469+ 个 txt 文件、300 文件上传上限）。

**推荐方案：分片索引**

```
%LOCALAPPDATA%/ZebrafishGeneQA/faiss_pubmed_yes/
  shard_0000/   index.faiss + index.pkl   # PMID 00000001–00005000
  shard_0001/
  ...
  manifest.json                           # 分片清单、构建时间、向量总数
```

**元数据表**（MySQL 新建，不存向量本体）：

```sql
CREATE TABLE literature_vector_chunk (
    chunk_id     BIGINT AUTO_INCREMENT PRIMARY KEY,
    pmid         VARCHAR(16) NOT NULL,
    shard_id     SMALLINT NOT NULL,
    chunk_index  SMALLINT NOT NULL,
    char_start   INT,
    char_end     INT,
    title_zh     VARCHAR(512),
    judgement    VARCHAR(16) DEFAULT '是',
    text_preview VARCHAR(300),
    embedded_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_pmid (pmid),
    KEY idx_shard (shard_id)
);
```

**检索时**：对各 shard 并行 top-k → 合并排序 → 取全局 top-K（K=10~20）。

### 5.3 备选方案对比

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| 分片 FAISS + MySQL 元数据 | 与现栈一致、无新依赖 | 检索需 merge | **推荐一期** |
| Chroma / Milvus | 原生 metadata filter | 新依赖、部署成本 | 二期数据量 >20 万再考虑 |
| 单文件 FAISS | 简单 | 4.6 万篇 rebuild OOM 风险 | 不推荐 |

---

## 6. 实施阶段（建议 4 步）

### 阶段 0：环境准备（0.5 天）

- [ ] 确认 `models/bge-small-zh-v1.5` 已下载  
- [ ] 确认 `FAISS_INDEX_DIR` 磁盘空间 ≥ **5 GB**（摘要模式索引约 200–400 MB；含全文则 2–5 GB）  
- [ ] 从 CSV 导入 PMID 清单到临时表或进度 JSON  

### 阶段 1：文献获取（1 天，可过夜）

```sql
-- 待补拉清单
SELECT j.pmid FROM literature_pubmed_judgement j
LEFT JOIN literature_pubmed p ON j.pmid = p.pmid
WHERE j.judgement = '是' AND (p.raw_xml IS NULL OR p.raw_xml = '');
```

- 本地命中：直接解析 XML 提取 `title/abstract/journal/year/doi`  
- 缺失：NCBI efetch 批量补拉 → 写回 `literature_pubmed`  
- 输出：`data/literature/pubmed_yes_corpus.jsonl`（一篇一行，规范化 JSON）

### 阶段 2：分块 + 嵌入（1–2 天，CPU 可过夜）

- 按 batch 500 篇读取 jsonl  
- 分块 → embed → 写入 shard FAISS（每 shard **5,000 篇 / ~1 万向量**）  
- 同步写入 `literature_vector_chunk`  
- **断点续跑**：进度记录 `last_pmid`、`shard_id`

**性能预估**（CPU + bge-small）：

| 指标 | 估算 |
|------|------|
| 嵌入吞吐 | ~300–600 chunks/min |
| 9.4 万 chunks | **约 3–5 小时** |
| 峰值内存 | ~2–4 GB |

### 阶段 3：检索接入（0.5 天）

- 扩展 `ZebraFishRAG`：新增 `pubmed_yes_retriever`  
- 查询流程：用户问题 → embed → 多 shard 检索 → 合并 → 注入 `get_context()`  
- 返回格式带 **PMID + 中文标题**，便于前端展示引用  

### 阶段 4：验收与增量（0.5 天）

- [ ] 随机 50 个基因/表型问题，人工看 top-5 相关性  
- [ ] 对比「仅 knowledge.txt」vs「+ pubmed_yes 索引」的回答质量  
- [ ] 新判别「是」文献 → 增量 append 到最新 shard  

---

## 7. 与现有系统的关系

```
现有                          新增
─────────────────────────────────────────────
knowledge.txt                 保留（默认知识）
knowledge.d/literature_*.txt  不再用 469 个小文件方案
FAISS (默认目录)              保留旧索引
FAISS (pubmed_yes/)           新建独立索引，避免 rebuild 互相影响
menu 11 collect_literature    并行存在；本方案用判别后精筛子集
hub RAG get_context()         扩展为多源检索（默认知识 + 文献向量）
```

**原则**：判别「是」索引 **独立目录**，不破坏现有 RAG；问答时 **加权合并** 两路检索结果。

---

## 8. 资源与成本估算

| 项目 | 摘要模式（方案 A） | 摘要+PMC全文（方案 B） |
|------|-------------------|------------------------|
| 网络下载 | 几乎为 0（用已有 XML） | +5k–15k 篇 PMC 请求 |
| 磁盘（语料+索引） | ~1–2 GB | ~5–15 GB |
| 构建 CPU 时间 | 3–5 h | 1–2 天 |
| 查询延迟 | +50–150 ms（10 shard merge） | 略增 |

**NCBI 合规**：efetch ≤ 3 req/s（无 API Key）；有 Key 可 10 req/s。

---

## 9. 风险与对策

| 风险 | 对策 |
|------|------|
| 部分 PMID 无 abstract | 用 title + 中文摘要兜底；仍无则跳过并记录 |
| 中文摘要为一句话（hybrid 模式） | 向量化时 **拼接 abstract_en**，摘要字段用「中+英」 |
| FAISS 全量 rebuild OOM | **分片 + 增量**，禁止 4.6 万篇一次性 load 到内存 |
| `knowledge.d` 300 文件上限 | 本方案 **不写 knowledge.d**，直写 FAISS shard |
| 嵌入模型未下载 | 启动前检查 `models/bge-small-zh-v1.5`，失败则提示 |
| 重复向量 | PMID + chunk_index 唯一约束，重建前先按 shard 清理 |

---

## 10. 验收标准

1. **覆盖率**：≥ 99% 的「是」文献进入向量库（允许无摘要极少数跳过）  
2. **可检索**：给定 PMID，能召回对应 chunk  
3. **可引用**：RAG 回答中可展示 PMID + 中文标题  
4. **可恢复**：中断后 `--resume` 从未完成 PMID 继续，不重复 embed  
5. **独立部署**：删除 `faiss_pubmed_yes/` 不影响原有 `faiss_index/`  

---

## 11. 已确认配置（2026-07-08）

| # | 选项 | **确认** |
|---|------|----------|
| 1 | 正文粒度 | **一期含 PMC 开放全文**（无全文则摘要兜底） |
| 2 | 嵌入文本 | **中英标题 + 中英摘要 + PMC 正文** |
| 3 | 向量库 | **分片 FAISS** |
| 4 | 下载 | **本地 XML 优先 + Europe PMC 全文** |
| 5 | Token | **不消耗 DeepSeek**（本地 BGE + 公开 API） |

---

## 12. 已实现脚本

| 命令 | 作用 |
|------|------|
| `python -m scripts.fetch_pubmed_yes_corpus --resume` | 拉取语料 + PMC 全文 → jsonl |
| `python -m scripts.vectorize_pubmed_judgement_yes --phase embed --resume` | 分块 + 本地嵌入 → FAISS |
| `python -m scripts.vectorize_pubmed_judgement_yes --phase all --resume` | 一键 fetch + embed |
| `python -m scripts.vectorize_pubmed_judgement_yes --phase test --query "..."` | 检索测试 |
| `logs\run_pubmed_yes_vectorize.bat` | 过夜后台（可关 Cursor） |

**语料**：`data/literature/pubmed_yes_corpus.jsonl`  
**索引**：`%LOCALAPPDATA%\ZebrafishGeneQA\faiss_pubmed_yes\`

---

## 13. 下一步（执行）

```powershell
# 小规模试跑（3 篇，验证流程）
python -m scripts.vectorize_pubmed_judgement_yes --phase all --limit 3 --no-resume

# 全量过夜（约 46,878 篇；fetch ~10h + embed ~数小时，均不耗 DeepSeek token）
python -m scripts.vectorize_pubmed_judgement_yes --phase all --resume
# 或双击 logs\run_pubmed_yes_vectorize.bat
```

---

*文档位置：`docs/pubmed_judgement_yes_vectorization_plan.md`*
