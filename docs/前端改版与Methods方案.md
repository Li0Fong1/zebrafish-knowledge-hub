# Zebrafish Hub · 前端改版 + Methods 写作 方案

> 版本：2026-09-30　范围：`web/`（前端）、`agent/`+`api/`（Agent 补功能）、`docs/paper/`（Methods）
> 依据：你 2026-09-30 的 9 条要求 + 5 个选择题答复（UI 优先 / 顶部 6 项 / 去变色动效保留静态柔和渐变 / 保留首页精灵鱼 / 作者分节写 Methods）

---

## 0. 你的要求 → 交付项对照（验收底线）

| # | 你的原话 | 交付项 | 验收方式 |
|---|---|---|---|
| 1 | 界面搞美观一点，Tools 整在一个栏里 | 顶部单栏 `Home / Search / Tools▾ / Literatures / Agent / About`，11 个工具收进 Tools 下拉 | 浏览器实测：任一页面 1 次点击可达任一工具；截图对比 |
| 2 | 查询框占地太大，右边结果不突出 | 查询区高度 ≤ 90px、结果区 ≥ 屏宽 62%（≥1280px 视口） | 像素探针量取 DOM 盒宽高 |
| 3 | 告诉用户哪个是 Ensembl / ZFIN / NCBI，要给超链接 | 统一「来源徽标」组件 + 每条证据外链 | 随机 5 条结果人工点链；脚本断言链接可拼 |
| 4 | 功能是从哪里获取的信息 | 每功能「数据来源 / 快照日期 / 我们怎么算的」折叠说明 | 11 个工具页全部有 `data-provenance` 块 |
| 5 | 简洁一点，找不到重点 | 删装饰动效、降饱和、三级字阶、单强调色 | 设计令牌表 + 截图评审 |
| 6 | Send 框老是挡内容 | composer 改用 CSS 变量占位（列表底部留白 = 实测高度），不再覆盖 | 滚动到底最后一条消息完整可见 |
| 7 | 背景颜色一直在变，去掉花哨的 | 删除变色/波浪/浮动动画；保留静态柔和渐变底 + 首页精灵鱼 | grep 无 `waveDrift/waveBob/floaty` 动画声明 |
| 8 | 图 6 描述看不懂，要解释 | 表达分数带单位/来源徽标 + tooltip + 外链 | 界面上不再出现无单位的裸数字 |
| 9 | 参照马铃薯 METHODS 把方法写好，谁负责谁写 | `docs/METHODS/METHODS.md`（作者分节）+ 数字对账脚本 | `verify_methods_numbers.py` 全绿 |

---

## 1. 基线事实（必须先对齐，否则改完会被线上覆盖）

| 文件 | 本地 | 服务器（线上） | 处置 |
|---|---|---|---|
| `web/index.html` | 475 行 | 475 行，内容同（仅换行符差异） | 以服务器为准 |
| `web/css/style.css` | 2,509 行 | **3,179 行（+678/−8，7 处）** | **以服务器为准** |
| `web/js/app.js` | 4,198 行 | 4,203 行（4 处差异） | **以服务器为准** |
| `web/js/i18n.js` | **缺失** | 31,221 B | 必须拉回本地，改版不得破坏 i18n |
| `web/js/fish-swim.js`、`web/css/fish-swim.css` | — | — | **已移除（2026-10-09 按需求去掉首页精灵鱼动效）** |

> 流程：`sftp 拉取线上 web/ → 本地覆盖（带 .bak-presync-* 备份）→ 本地改 → 本地起静态服务 + Hub 隧道实测 → 备份 .bak-<特性> → 推线上 → 冒烟`。

---

## 2. 信息架构

```
顶部（唯一导航栏，48px 高）
├─ Home            #/home
├─ Search          #/chat          一等入口（原来叫"智能问答"）
├─ Tools ▾         #/tools         下拉：11 个工具
│    ├─ 基因工作台        #/gene
│    ├─ BLAST            #/blast
│    ├─ GO/KEGG 富集     #/enrich
│    ├─ 通路浏览器       #/kegg
│    ├─ ID 映射          #/id
│    ├─ 序列提取         #/sequence
│    ├─ 一键注释         #/annotate
│    ├─ 文献检索台       #/literature
│    ├─ 入库             #/upload
│    ├─ 检索调试         #/retrieval
│    └─ 访客地图         #/visitor      （新增，对齐马铃薯 Visitor Map）
├─ Literatures    #/literature     直链（常用）
├─ Agent ▾        #/agent          下拉：Gene detail / Database query / KB Q&A / Multi-model fusion
└─ About           #/about          来源、版权、引用方式、数据快照日期
```

- 删除左侧 `rail`（与顶部重复）与 `#toolTabsLegacy` 标签条。
- 路由**全部沿用现有 hash**（深链不能断），只改导航呈现；`#/tools` 为新增聚合页。
- Tools 下拉：键盘可达（`↑↓ Enter Esc`）、`aria-expanded`、点击外部关闭。

---

## 3. 设计令牌（简单又高级：单一强调色 + 大留白 + 弱装饰）

```css
:root{
  --bg:#FBFCFD;            /* 静态底，柔和渐变仅 180deg #F6F8FB → #FBFCFD */
  --panel:#FFFFFF; --line:#E6EAF0; --line-strong:#D5DBE3;
  --ink:#0F1720; --ink-2:#48525E; --ink-3:#7B8592;
  --accent:#0F766E; --accent-weak:#E6F4F2; --accent-ink:#0B5F58;
  --radius:10px; --radius-sm:6px; --shadow-1:0 1px 2px rgba(16,24,40,.05);
  --space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px; --space-6:24px; --space-8:32px;
  --fs-h1:22px; --fs-h2:16px; --fs-body:14px; --fs-cap:12px; --fs-mono:12.5px;
}
```

**删除清单（要求的"去掉花哨"）**
- `@keyframes waveDrift / waveDriftAlt / waveBob`、`.wave-layer / .wave-thumb / .wave-card` 的动画与渐变动效
- `@keyframes floaty` 与 `.viz-a/b/c` 浮动装饰
- `t-gradient` 系列卡片彩色渐变 → 改单色浅底 + 图标描边
- `backdrop-filter: blur()` 玻璃拟态（导航/弹层）→ 改为实色 + 1px 描边
- 页面顶部 `linear-gradient(180deg,#e8eef8,…)` 变色底 → 静态 `--bg`
- `@keyframes typingPulse`（呼吸光晕）→ 保留静态"输入中"点阵
- **保留**：首页 `@keyframes msgIn`（消息入场，0.18s）、`caretBlink`

---

## 4. 页面级改造

### 4.1 Home（`#/home`）
- 三段式：**Hero 搜索（≤200px 高）** → **Tools 宫格（11 卡，静态）** → **今日文献（3 条 + More）**，对齐马铃薯信息流。
- 移除"10 张波浪卡片"的重复导航作用（与 Tools 下拉重复）→ 宫格改为紧凑 4×3，卡片只留图标+名+一行说明。
- （2026-10-09：首页精灵鱼动效已按需求移除。）
- **2026-10-09 第二次收敛（对齐 Potato Agent）**：删掉「指标墙 / 模块卡墙 / 研究影像流」三块，首页只留四块：
  ① 品牌句 + 单行搜索（范围下拉：智能问答 / 基因 / 文献）+ 一行「更强大的数据探索，请用 Zebrafish Agent →」；
  ② **Today's Zebrafish Research（今日研究）**：一天一篇，取本地库真实记录（标题 / 期刊 / 年份 / DOI-PMID / 摘要 4 行截断 / 原文外链）；
  ③ **Latest Updates（文献更新）**：更新历史列表 + **「更新文献」按钮**（增量抓取→入库→必要时重建向量）；
  ④ **Cite Us 页脚**（不编造出版物信息，注明索引快照日期）。
- 首屏高度收紧：旧样式给 `.home-stage` 的 `min-height: 599px` 被覆盖为 `auto`，装饰层 `.home-stage-media/.home-stage-veil` 已从 HTML 删除。
- **2026-10-10 视觉升级（`web/css/home-2026.css` 取代 `home-simple.css`）**：
  · 上一版用了绿色 `#1f9d55`，与全站青绿主色 `--accent(#0E7C6B)` 打架；本版全部改用 refine.css 的设计令牌（深浅色主题自动跟随，无硬编码色）
  · Hero：顶部淡青绿光斑（纯静态，无动效）→ 大写拉开字距的品牌句 → 大号标题 + 62ch 引文宽度 → **一体式胶囊搜索条**（内嵌放大镜图标 + 范围下拉 + 实心主按钮，focus 有描边光圈）→ **4 个快速示例 chips**（点击即填词并直接搜索，复用首页已有逻辑）
  · 今日研究：卡片带左侧青色竖条 + hover 描边/投影，元信息（期刊/年份/PMID）改为 chip；摘要在 78ch 宽内 4 行截断
  · 文献更新：改成表格化时间线（日期圆点 + 等宽动作名 + 灰色明细），整行 hover；按钮改描边→悬停实心
  · Cite Us：左侧青色描边的引用框（`--accent-weak` 底）
  · 链接分层：主链接实心药丸、次链接描边药丸，一眼看出点哪个
  · 补齐 `focus-visible`/`disabled`/`prefers-reduced-motion`，并保留 `#langToggleBtn` 宽度修复
- 数据链路：`GET /api/hub/research-feed` 一次返回 `daily / updates / years / stats / maintenance` 五段，逐段 try/except，缺一段不影响其他段；
  「今日研究」读轻量缓存 `hub_recent_papers.json`（由 `scripts/build_hub_recent_papers.py` 从 63 MB 的 `literature_catalog.jsonl` 预抽 200 条，**不逐请求解析大文件**）；
  缓存重建已接入 `agent/hub_maintenance.refresh_recent_papers()`，文献任务成功后自动刷新（只读派生物，失败不影响主任务）。
- "更新文献"按钮行为：点击先二次确认（该动作会联网抓取并可能重建索引），随后每 5 s 轮询 `/api/hub/maintenance/status`，页面不可见时不轮询。

### 4.2 Search / Chat（`#/chat`）
- 查询框：单行、最大宽 720px、居中；空态才展示快捷提示（3 个 chip，非常驻）。
- 布局：`left: 结果主区 1fr` / `right: 证据与溯源 360px`（≥1280px）；窄屏单列堆叠。
- **composer 不遮挡**：`position: sticky; bottom: 0`，父容器 `padding-bottom: calc(var(--composer-h) + 24px)`，`--composer-h` 由 `ResizeObserver` 写入；去掉半透明叠色。
- 结果卡：首句直答 → 分因素要点 → 每要点尾部**来源徽标**。
- 回答内 `[n]` 与文末文献列表联动高亮（现有能力保留）。

### 4.3 来源徽标与超链接（要求 3）
新增 `web/js/source-badges.js` + `.src-badge` 样式；数据来源与链接规则：

| 来源 | 触发字段 | 链接模板 |
|---|---|---|
| Ensembl | `ensembl_id` | `https://www.ensembl.org/Danio_rerio/Gene/Summary?g=<ENSDARG>` |
| ZFIN | `zfin_id` | `https://zfin.org/<ZDB-GENE-…>` |
| NCBI Gene | `ncbi_gene_id` | `https://www.ncbi.nlm.nih.gov/gene/<id>` |
| NCBI RefSeq 基因组 | 常量 | `GCF_049306965.1`（GRCz12tu） |
| UniProt | `uniprot` | `https://www.uniprot.org/uniprotkb/<acc>` |
| Bgee | `ensembl_id` | `https://www.bgee.org/gene/<ENSDARG>` |
| Expression Atlas | `ensembl_id` | `https://www.ebi.ac.uk/gwb/.../` |
| GO | `go_id` | `https://www.ebi.ac.uk/QuickGO/term/<GO:…>` |
| KEGG | `pathway_id` | `https://www.kegg.jp/entry/<id>` |
| PubMed | `pmid` | `https://pubmed.ncbi.nlm.nih.gov/<pmid>/` |
| 本地库 | 表名 + 快照 | 弹层展示 `gene_basic/gene_expression/gene_go` + 更新日期 |

- 每个徽标：`库名 + 版本/发布号`（如 `Ensembl GRCz11 r116`、`NCBI RefSeq GRCz12tu`），`title` 显示完整 ID。
- 徽标统一渲染函数，避免各页面各写一套（现有 `app.js` 已有 3 处重复拼链接，需合并）。

### 4.4 溯源块（要求 4）
每工具页统一插入：
```
数据来源：本地库 GRCz11 Ensembl r116（gene_basic/gene_expression/gene_go） · NCBI RefSeq GRCz12tu · FAISS 索引 qwen3_4b_v2_short_section
快照：2026-09-30 · 我们怎么算的 ▸（展开：切分/嵌入/检索/重排参数）
```

### 4.5 表达数字可读化（要求 8，图 6 的问题）
现状（已定位）：`agent/answer_enrichment.py:367` 把两个来源拼成一行裸数字——
Bgee v15.2 **Expression Score (0–100)**（`database/gene_formatter.py`）与 EBI Expression Atlas **E-ERAD-475 胚胎 TPM**（`database/expression_collector.py`）。

改法：
- 每个数值渲染为 `Bgee 97.9/100` 或 `Atlas 97.9 TPM`（单位必带）；
- 分组标题写明：`组织表达（Bgee 表达分，0–100，非绝对丰度）` / `胚胎时期表达（Expression Atlas TPM）`；
- 分数后加 `ⓘ` tooltip：「该值是 Bgee 对表达存在性的置信分，越高表示越肯定有表达，不是拷贝数/丰度」；
- 行尾给 Bgee / Expression Atlas 外链。

---

### 4.6 富集结果（`#/enrich`）—— 看得懂「富的是哪一方面」
- 用户反馈（2026-10-10）：① 看不出富集的是哪一方面的；② 结果堆在 `<pre>` 里字号小、灰、看不清。
- 顶部「**这次富集看的是哪些方面**」：GO 三个方面（BP 生物学过程 / CC 细胞组分 / MF 分子功能）
  与通路来源库（KEGG / WikiPathways / Reactome）各带命中条目数；缺标注的条目归入「方面未标注」
  并原样展示库内取值（不猜、不硬塞三分类）。
- 每条结果改为卡片：方面/来源**徐标** + 名称 + ID + 外链（QuickGO / Reactome / WikiPathways / KEGG）
  + 指标标签（q 值 / p 值 / GeneRatio / BgRatio / 富集倍数 / 命中数）+ 命中基因 chip。
- 样式新建 `web/css/enrich-view.css`（提高对比度：正文 #111827，标签 #4b5563 起）；
  原始纯文本结果收到 `<details>` 折叠里（不丢信息）。
- 后端 `database/enrichment_toolkit.py` 同步补齐字段：GO `go_id/term_name/aspect/aspect_label/url/name_missing`
  与 `aspect_summary`；通路 `source/source_code/source_label/url` 与 `source_summary`；TSV 导出新增两列。

### 4.7 参考文献相关性 —— RRS 打分闸门
- 用户反馈（2026-10-10）：问 nanog 却挂纳米颗粒/纳米塑料等无关论文。
- 根因：文末表原先「不足下限就从证据池按序号补齐」，池里的弱相关条目被一并带上。
- 方案：新增条目级打分 RRS（实体命中 / 池内位次 / 正文引用 / 时效 / 元数据），
  低于门槛不入选；不足 2 条成表则整块省略并清掉悬空 `[n]`。
  完整规格（权重表 / 三条硬规则 / 环境变量 / 调优）见 **`docs/引用相关性打分RRS.md`**。

## 5. 代码改动点（文件级）

| 文件 | 改动 |
|---|---|
| `web/index.html` | 导航重写为 6 项 + 2 个下拉；删 rail / legacy tabs；新增 `#/tools`、`#/about`、`#/visitor` 容器；引入 `source-badges.js`、`i18n.js` 顺序校正 |
| `web/css/style.css` | 令牌替换（§3）；删动画清单；导航/宫格/结果卡/composer 重排；新增 `.src-badge`、`.provenance`、`.metric-unit` |
| `web/js/app.js` | 下拉组件、`#/tools` 聚合页、`#/about`、`#/visitor` 路由；结果卡接入徽标；合并 3 处重复外链构造 |
| `web/css/enrich-view.css` | 2026-10-10 新增：富集结果结构化视图样式（方/来源徐标、指标标签、高对比） |
| `llm/ref_relevance.py` | 2026-10-10 新增：引用相关性打分 RRS（条目级，纯文本可复算） |
| `scripts/test_ref_relevance.py` | 2026-10-10 新增：RRS 验收（11 项，含无关引用拦下与不误伤） |
| `scripts/test_enrichment_labels.py` | 2026-10-10 新增：富集方面/来源标注验收（4 项） |
| `web/js/source-badges.js` | 新增：来源注册表 + 渲染 + 链接拼接（单一事实源） |
| `web/css/home-2026.css` | 2026-10-10 新增：首页视觉升级（令牌化配色、胶囊搜索条、快速示例 chips、时间线式更新列表）；取代 `home-simple.css` |
| `web/js/home-feed.js` | 2026-10-09 新增：渲染 `/api/hub/research-feed` + 「更新文献」按钮（确认 → 轮询 → 刷新）；**自带中英两套文案**，不依赖逐词字典 |
| `scripts/build_hub_recent_papers.py` | 2026-10-09 新增：预抽今日研究轻量缓存（可重复重建，只读派生物） |
| `web/js/composer-fit.js` | 新增：`ResizeObserver` 写 `--composer-h`，修「Send 挡内容」 |
| `web/js/i18n.js` | 新增键位（导航/tooltip/溯源文案），不破坏既有键 |
| `agent/answer_enrichment.py` | 表达段落带单位与来源；不再输出裸数字 |
| `api/api_server.py` | 结构化返回 `sources[]`（库名/ID/链接/快照），供前端徽标直接用 |
| `docs/METHODS/METHODS.md` | 新增（§6） |
| `scripts/verify_methods_numbers.py` | 新增：Methods 数字对账 |
| `scripts/verify_web_ui.py` | 新增：静态断言（无动画声明、composer 留白、徽标链接模板、深链可达） |

---

## 6. Agent 五项补功能（按你的勾选）

| 功能 | 现状 | 改动 | 验收 |
|---|---|---|---|
| 多轮上下文记忆 | 已有 `history` 参数，前端未持久化 | `localStorage` 会话 + 侧栏会话列表 + 改名/删除/重命名 | 刷新后历史仍在；第二轮能指代上一轮实体 |
| 工具调用编排 | 各工具各自为政 | `agent/workflow_engine.py` 增加 `plan → tool → answer` 链（BLAST/序列/跨库/富集） | 一句"tp53 的启动子并比对"能自动串联 3 步并回填 |
| 引用与溯源面板 | 只有行内 `[n]` | 右侧证据栏：来源徽标 + 片段 + 相似度 + 外链；点击定位正文 | 每条要点可展开到证据 |
| 会话导出 / 分享 | 无 | 导出 Markdown/JSON、生成只读分享链接（签名 + 有效期） | 导出文件可复现回答与引用 |
| 定时任务 | 夜间运维脚本已有 | 每日文献推送（Home 今日文献）、访客统计（Visitor Map）、任务状态页 | 每日快照生成；访客地图有数据 |

---

## 7. Methods 写作方案（作者分节）

交付：`docs/METHODS/METHODS.md`（英文正文 + 中文对照注释 + 每节 `Owner`），LaTeX 可直接 `\input`。

**结构（严格对齐马铃薯 `METHODS → Processing literatures` 的叙述顺序）**

```
METHODS
2.1 Processing literatures            Author A   检索→期刊门槛→双 LLM 筛选取并集
2.2 Full-text acquisition & cleaning   Author B   PDF/XML 获取、清洗规则、真实触发率
2.3 Segmentation & vectorization       Author B   切分参数、嵌入模型、FAISS 规模
2.4 Structured knowledge base          Author C   GRCz11 r116 表结构、GRCz12tu 解析层、别名三态
2.5 Retrieval & reranking              Author D   粗排 500 → 精排 100 → 48k 打包
2.6 Answer routes & answer contract    Author D   rag/fusion/hub × partial/full、[n] 与文末文献契约
2.7 Agent orchestration & scheduling   Author D   意图路由、多 Agent、资源守卫、自愈
2.8 Web service & reproducibility      Author E   Nginx/限流/加密封包/隧道/一键部署
2.9 Evaluation & statistics            Author E   60 次真实调用、双评委盲评、精确置换检验、13+32 项校验
2.10 Data & code availability          Author E   仓库、快照日期、索引版本
```

> 作者名单：先写 `Author A–E`（匿名投稿可直接用）。**请给我一份真实映射**（谁负责 2.x），我一行替换即可。

**每节模板**
```markdown
### 2.3 Segmentation & vectorization
<!-- Owner: Author B -->

Sentence (one claim per sentence). Units, models, versions, thresholds explicit.

- **Inputs**：`merged_corpus/…`（32,532 篇全文；快照 2026-09-10）
- **Parameters**：paragraph-section 切分 360/110 字符，重叠 48
- **Model**：Qwen3-Embedding-4B，2048 维
- **Outputs**：170 FAISS 分片，7,651,942 语义块（index `qwen3_4b_v2_short_section`）
- **Commands**：`python -m …`（可复跑）
- **Numbers source**：`Figure/_data.json → …`（由 `verify_methods_numbers.py` 对账）
```

**数字纪律**：文中所有数字用占位符 `{{key}}` 或直接引用产物文件键；`scripts/verify_methods_numbers.py` 逐条比对，不一致即 FAIL，杜绝"方法写成不实"。

---

## 8. 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| **M0 基线** | 同步线上 `web/` 到本地（含 `i18n.js`），备份 | 本地 md5 == 线上 md5 |
| **M1 骨架** | 令牌 + 单栏导航 + 去动效 + composer 修复 + Home 三段式 | 浏览器实测 3 张截图；"Send 不再挡内容" |
| **M2 来源与溯源** | 来源徽标组件 + 超链接 + 溯源块 + 表达单位化 | 11 工具页 / 5 条结果人工核验 |
| **M3 Agent 五项** | 按 §6 逐项 | 每项独立验收脚本或人工用例 |
| **M4 Methods** | `METHODS.md` + 对账脚本 | `verify_methods_numbers.py` 全绿 |
| **M5 收口** | 全站冒烟 + 深链遍历 + i18n 双语检查 + 推送线上 | 线上截图 + 回归清单 |

---

## 9. 风险与回滚

| 风险 | 处置 |
|---|---|
| 改本地却被线上覆盖 | 一律"先拉后改再推"，每次推送前 `.bak-<特性>-<日期>` |
| 破坏 i18n（本地缺 31KB `i18n.js`） | M0 必拉；新增键位走 `i18n.js` 注册，不硬编码中文 |
| 深链失效 | 路由 hash 全部保留，新增页只追加 |
| 无 JS 回退 | 导航用真实 `<a href="#/...">`，下拉仅增强 |
| i18n 逐词字典容易漏词（英文界面残留中文） | 首页新区域一律 `data-no-i18n="1"` + 由 `home-feed.js` 按 `documentElement.lang` 输出两套文案；静态区新增中文必须同时注册 `ZH2EN` 词条；改动后需 bump `/static/*.js?v=` 版本号，否则浏览器复用旧字典 |
| 升级后浏览器仍用旧资源 | 每次改 `web/` 静态资源必须同步改 `index.html` 里的 `?v=`（本次 `20261009a` → `…d/e`） |
| 重启服务卡住（公网上不了） | uvicorn 收到 SIGTERM 后会等长连接（SSE）关完才退，可能永远不退→端口已关、进程还在；此时必须 `kill -9`，`hub_supervise.sh` 会在 10 s 内自动拉起 |
