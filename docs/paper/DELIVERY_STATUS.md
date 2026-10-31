# 交付状态（2026-10-01）

> 本文件只记录**事实与待办**，不重复论文内容。数值纪律同 METHODS：不确定的一律写"待办"。

## 已完成的交付物（本地已验证）

| 交付物 | 位置 | 验证方式 |
| --- | --- | --- |
| Figshare 数据包（36 文件，1.15 MB） | `docs/paper/data_package/` | `python -X utf8 scripts\build_figshare_package.py --check` → `36 files, 0 problem(s)` |
| LaTeX 尾段（与马铃薯同序 5 节） | `docs/paper/BACK_MATTER.tex` | 结构检查：括号平衡 0、5 节顺序正确、占位符齐全、无未转义字符（本机无 pdflatex，未做真实编译） |
| 数据包构建脚本 | `scripts/build_figshare_package.py` | 幂等可重跑；空文件/缺文件记入 `MANIFEST.json → skipped`，不静默造数 |
| METHODS 数字核对 | `scripts/verify_methods_numbers.py --write` | `[verify-methods] PASS` |
| 前端 8 个脚本语法 | `web/js/*.js` | `node --check` 全量通过 |

数据包内容分层：`01_benchmark`（100 题 + 20 对同构题 + 7 维评分细则）、`02_raw_outputs`（原始回答、
盲评包、A/B 解密表、评委原始输出 6 份）、`03_scoring`（评分表 + 5 次独立评委复评）、
`04_statistics`（精确置换检验 + FigS4 图统计与像素级审计）、`05_provenance`（服务器/索引快照、
清洗触发率、LLM 用量）。

## ✅ 已部署上线（2026-10-02 17:15–17:22）

**公网地址**：`https://caught-monsters-remix-probe.trycloudflare.com`（旧域名已被 Cloudflare 回收；**隧道每重启一次域名就变一次**）

本轮推送并验证的 7 个文件：

| 文件 | 版本戳 | 公网验证 |
| --- | --- | --- |
| `web/index.html` | — | 200；含 39 个 `aria-label`；导航 `Literature`（单数） |
| `web/css/refine.css` | `?v=20261001q` | 200 |
| `web/js/app.js` | `?v=20261001f` | 200 |
| `web/js/live-update.js` | `?v=20261001c` | 200 |
| `web/js/ui-feedback.js` | `?v=20261001a` | 200 |
| `web/js/i18n.js` | `?v=20261001a` | 200 |
| `api/api_server.py` | — | 重启后 `/api/hub/stream-version` 200、`/api/hub/events` SSE 正常 |

**公网实测（2026-10-02）**：`/` 200；`/api/health` → `{"status":"ok","mysql":true,"rag":true}`；
`/api/hub/stream-version` → `{"ok":true,"version":"ab78f72dcbe59b51",...}`；首页 8 个统计值取到**服务器真实数据**
（KEGG `1,520`、映射 `22,628`、xref `80,212`，非本地演示值），刷新时间 `2026-10-02 06:53:19 UTC`，
`liveMode=sse`（真实 SSE 连接，非轮询回退），且**未出现"演示快照"标注**。

**残余进程（已按用户要求清理，只留必需）**：`hub_supervise.sh`(2759427) + `uvicorn`(2759433) + `cloudflared`(2758903)；
游离 python 进程 0 个；已删掉自己用过的 `/tmp/ct_zf`、`/tmp/ct`（未触碰其他用户的 `/tmp/*.py`）。

**诚实说明（三条限制，不等于"已彻底完成"）**：
1. `crontab` 对 `deng` 用户**无权限**（`You (deng) are not allowed to use this program (crontab)`），守护 cron 属于 root，
   本机无法安装/修改；实测守护在 17:13/17:16/17:17/17:20 有触发记录 → 自愈是**外部（root cron）在跑**。
2. quick tunnel 域名**每次重启隧道都会变**；要固定地址需改用 named tunnel（固定子域）或服务器 Nginx + 443（需服务器侧改动）。
3. Hub 被机器资源压力杀掉时存在约 1 分钟的 502 窗口，随后由守护自动拉起。

## 线上事故与修复（2026-10-03）：Hub 每 2~3 分钟被 OOM 杀掉

**症状**：站点间歇 502；`logs/hub_guard.log` 每 2~3 分钟一条 `hub health=000 -> restart`（自 **09-30** 起就有，10-03 当天 280 次）。

**根因（实测证据，非推测）**

| 证据 | 读数 |
| --- | --- |
| `logs/hub_supervise.log` | **`hub exited code=` 一条都没有** → uvicorn 不是自己退出，是**进程组被秒杀**（连守护一起带走），所以没有 traceback |
| cgroup | `user-1005.slice` `memory.max = 38654705664`（36 GB）；`memory.events` → **`oom_kill` 714→760** 持续增长 |
| 进程内存 | 重启后 uvicorn RSS 20 秒内 **27.2 GB → 30.8 GB**（page cache 可回收，OOM 由匿名内存决定） |
| cgroup 归属 | uvicorn 跑在 `…/user@1005.service/app.slice/run-*.scope`，该 scope 自身 `memory.max=max`，真上限在**上一级** `user-1005.slice` |
| 触发者 | 预热日志时序：0.5B 就绪 → RAG 就绪 → **PubmedYes（Qwen3-Embedding-4B + 多分片 + CrossEncoder）开始加载** → 约 40 秒后进程消失 |
| 索引规模 | 磁盘上 `faiss_pubmed_yes_qwen3_4b_v2_merged` = **63 GB / 170 分片**（另 `_v2` = 62 GB / 2711 分片）——**单个索引就超过 36 GB 上限** |

**修复（3 个文件，均已部署且哈希校验一致）**

1. `api/api_server.py`
   - 新增内存闸门 `_mem_info()/_mem_headroom_mb()/_mem_gate()`：**逐级向上**找最近一个设了上限的 cgroup；余量 = 上限 − 匿名内存（file cache 可回收不计）；无 cgroup 时用 `MemAvailable` 兜底。
   - `HUB_PUBMED_WARMUP` 默认 **0**：不再启动即预热这套重资源（36 GB 装不下），改为首次检索按需加载。
   - 预热串行化：PubmedYes 前先等 RAG 后台线程收工 + `gc.collect()`（避免两段并发顶穿上限）。
   - `/api/health` 新增 `mem_limit_mb/mem_used_mb/mem_headroom_mb`；`pubmed_status` 新增 `low_mem` 态。
2. `rag/pubmed_yes_retriever.py`
   - 分片加载加**内存预算**（`PUBMED_YES_SHARD_RESERVE_MB`，默认 4000 MB）：装得下多少装多少，不再被 OOM 带走。
   - 返回体新增 `shards_total/shards_skipped_low_mem/partial`，**部分加载会明示**，不静默降质。
3. `rag/rag_builder.py`
   - 新增 `_normalize_device()`：无 `torch.cuda.is_available()` 时把 `cuda:*` 归一为 `cpu`。
     修掉两个真 bug：① 缓存键按 device 区分，Hub 主链用 `cpu`、PubmedYes 用 `cuda:1` → 同一 4B 模型**双载**；
     ② 无 GPU 机器上按 cuda 构造**必抛异常**（`[预热] PubmedYes 加载第N次失败` 的根源）。
   - **2026-10-09 补**：device 归一只是治标——`rag_builder` 还会因**两种模块名**被自己重复导入，各持一份模块级缓存，仍是双载。已加 `_process_wide_cache()`（缓存挂 `sys`）+ `sys.modules` 双名登记，实测加载计数降为 **1**。

**验证（2026-10-03 23:37 起）**

| 指标 | 修复前 | 修复后 |
| --- | --- | --- |
| 存活 | **~90 秒必死** | >2 分 45 秒且未再死 |
| RSS | 27~30 GB | 8.4 GB → 稳定 **4.7 GB** |
| `/api/health` | 间歇 000 | 200 @0.047 s，`mem_limit_mb=36864` |
| 守护重启 | 每 2~3 分钟 | 23:37 之后**无重启** |

**2026-10-09 更新：文献检索已在线可用（部分分片，明示不静默）**

先前“服务未指向索引、实际 0 分片”的结论**是探测方法造成的误判，现已更正**：

| 项 | 实测 |
| --- | --- |
| 索引接线 | `$BASE/.env` 中 `FAISS_PUBMED_YES_DIR=/data/zoujin/deng/zebrafish_runtime/faiss_pubmed_yes_qwen3_4b_v2_merged`；`.env` 由 `core/entry.py:_load_dotenv()` 在启动时写入 `os.environ`，因此 `/proc/<pid>/environ` 里看不到（先前用不带 `.env` 的 `python -c` 探测，只看到不存在的回退默认值） |
| 实体核对 | `os.path.isdir=True`，`shard_*/index.faiss = 170` → 与 METHODS 口径一致 |
| 真根因 | `rag_builder` 被以**两种模块名**重复导入（`from rag_builder import …` 与 `from rag.rag_builder import …`）→ 模块级缓存各一份 → Qwen3-Embedding-4B **被加载两份**（各约 15 GB）→ 匿名内存 33 GB/36 GB，分片预算闸门如实拒绝加载任何分片（表现为 `0/170`） |
| 修复 | `rag/rag_builder.py` 新增 `_process_wide_cache()`：缓存挂到 `sys` 上，并把模块同时登记到两个名字下；部署后新会话日志中“嵌入模型加载完成”**计数 = 1**（修复前：启动 1 次 + 首次检索 1 次 = 2 次） |
| 线上实测 | `POST /api/retrieval/debug` → `ready:true, shards_loaded:36, shards_total:170, shards_skipped_low_mem:134, partial:true`；500 候选 → 交叉编码器重排 100 条；首次 39.6 s、二次 27.7 s，返回 118 KB 真实结果 |
| 稳定性 | 连调 2 次检索 + 3 次 `/api/health` 均 200；`oom_kill` 停在 760 **未再增长**；`anon 33.0 GB / 36 GB`，`memory.pressure some avg10=0.15` |

**仍然存在的客观限制（如实披露）**：全量索引 63 GB > 36 GB 上限，线上只能常驻 **36/170 分片（约 21%）**，返回体以 `partial:true` 明示；论文离线评测用的是全量索引。要让线上也覆盖全量，仍需三选一：① 抬高 slice 上限（`sudo systemctl set-property user-1005.slice MemoryMax=80G`）；② 重建更小索引（量化/降维/换更小嵌入）；③ 分片换入换出（工程量大）。



### 历史记录：那次为什么推不上去（已解决，保留以便复盘）

**根因（2026-10-01 实测确认，不再是猜测）**：服务器 `deng` 账号**已过期**。ssh 明确提示
`Your account has expired; please contact your system administrator.`，随后
`Connection closed by 175.0.34.167 port 43333`；paramiko 侧对应"**密码正确**时连接在 ~0.3 s 内被
披断、**密码错误**时反而正常返回 `Authentication failed.`（~3 s）"。
需管理员执行 `sudo chage -E -1 deng`（或 `usermod -e "" deng`）。
在此之前任何密码都登不进去，与网络、输入法无关。修复方式：管理员执行 `sudo chage -E -1 deng`（**已完成**）。
当时以下文件**已修好但还没部署**（**现已全部部署上线，见上表**）：

1. `web/js/provenance.js` —— 上一批部署（m3c，2026-09-30）里带入一个**语法错误**（`toolUpload` 与
   `toolOps` 之间缺逗号，导致整个文件解析失败 → 工具页溯源块与结果卡来源徽标失效）。**已在本仓库修好**。
2. `web/index.html` —— 版本号已升到 `provenance.js?v=20260930d` / `app.js?v=20260930k`，用于穿透浏览器与
   边缘缓存（不升版本号会出现"改了没生效"）。

推送命令（可在本机执行，或在服务器上直接改）：

```powershell
# 本机
.venv\Scripts\python.exe -X utf8 scripts\push_web.py   # 若未就绪，按下述 SFTP 手动推
# 目标路径
<BASE>/web/js/provenance.js
<BASE>/web/index.html
# 静态文件不需要重启 Hub
```

## 待外部信息（不编造，保留占位）

- 代码仓库地址：**已定**按“出版时公开”处理，正文写 "The code will be released upon publication."（暂不公开；正式 Gitee/GitHub 地址待公开时回填）
- `{{TODO:data_deposit}}`：上传 `docs/paper/data_package/` 后回填 Figshare DOI
- `{{TODO:funding}}`：基金项目名 + 项目号
- `\tbd{Author B}`：组学数据合作者姓名 + 单位
- `\tbd{Corresponding email}`：通讯邮箱

## ✅ 已上机核实（2026-10-02，SSH 恢复后执行完毕）

- **BLAST+ 版本号**：作者选择保留 `v2.17.0+`，**已核实为真**（不再是"待核实"）。
  - 实测输出（`scripts/probe_tool_versions.py`，落档 `docs/paper/data_package/05_provenance/tool_versions.json`）：
    `blastn: 2.17.0+`、`makeblastdb: 2.17.0+`，conda 包为 `blast 2.17.0 hb02a186_1 bioconda`，
    二进制 `<RT>/conda_blast/bin/blastn`（`Aug 22 22:17`）。
    **结论：METHODS 中 `BLAST+ v2.17.0+` 有据可依，可以定稿。**
  - 同时复核：`mysql` 客户端与 `mysqld` 均 `8.0.36`；运行环境 Python `3.10.20`。
  - 原核实思路（保留）：引擎由 `BLAST_BIN=$RT/conda_blast/bin` 指定，本地无法反推版本，只能上机读。
  - 核实命令（只读）：

    ```powershell
    .venv\Scripts\python.exe -X utf8 scripts\probe_tool_versions.py   # 写入 05_provenance/tool_versions.json
    ```

- **MySQL 8.0.36**：无需再查（服务器原始输出 `Ver 8.0.36 for Linux on x86_64` 见
  `项目阶段/05_数据与库/快照/_db_bad_title.txt`、`项目阶段/04_运维静音自愈/证明归档/_eno_title_check.txt`）。
  注意：本机 Windows 版为 8.0.45，勿混用。
- **Hub 掉线根因取证**（OOM 级清理）：`journalctl -k | grep -i oom`、`/sys/fs/cgroup/…/memory.events`、`systemd-oomd` 状态（需管理员）。

---

## 2026-10-09 服务已停机（只停斑马鱼项目，其他不动）

| 项 | 状态 |
| --- | --- |
| uvicorn（Hub）/ `hub_supervise.sh` / `cloudflared_run` 隧道 | **已停止**；端口 8080 无监听；公网链接失效 |
| 项目内存 | 33–35 GB → **0.31 GB**（cgroup anon） |
| MySQL（`mysqld`，326 MB）与系统进程 | **保留运行**（不属于要停的范围） |
| GPU | 从未占用（机器上 2×V100 一直是被其他用户的进程占用） |

**停止开关**（本次新增，解决“root cron 每分钟把 Hub 拉起来”的问题）：

- `$RT/hub_guard.sh` 开头新增判据：存在 `$RT/HUB_STOPPED` 时直接 `exit 0`（只写一份状态快照），不再复活 Hub 与隧道；改动前已备份为 `hub_guard.sh.bak_<时间戳>`。
- 观测证据：`$RT/hub_guard_last.json` = `{"ts":"2026-10-09 10:26:01","skipped":"HUB_STOPPED"}`，跨 cron 周期复查无进程复活。

**恢复服务**：

```bash
rt=/data/zoujin/deng/zebrafish_runtime
rm -f $rt/HUB_STOPPED
setsid nohup bash $rt/hub_supervise.sh >/dev/null 2>&1 &
# 隧道由守护下一分钟自动拉起；quick tunnel 重启会换域名，新地址见 $rt/public_url.txt
```

### 一、索引核查（结论：索引文件本身无问题）

| 检查项 | 实测 |
| --- | --- |
| 清单状态 | `faiss_pubmed_yes_qwen3_4b_v2_merged/manifest.json`：`status=ready`、`source_status=ready`、`index_version=qwen3_4b_v2_short_section`、`embedding_dim=2048`、`embedding_model=…/Qwen3-Embedding-4B`、`total_papers=32532`、`total_chunks=7651942`、`updated_at=2026-09-10` |
| 分片完整性 | **170/170** 分片均含 `index.faiss` + `index.pkl`；**0 字节文件 0 个**；manifest 的 170 个分片号与目录**一一对应（无缺失、无多出）**；分片 151–431 MB，合计 58.4 GB |
| 合并链 | 170 片 = 源索引 `faiss_pubmed_yes_qwen3_4b_v2`（2,711 片）按 `group_size=16` 合并，manifest 逐片记录 `src_shards` |
| 文献检索（线上） | `POST /api/retrieval/debug` → `ready:true`、`shards_loaded=32/170`、`partial:true`；500 候选 → 交叉编码器重排 100 条（预留 6000 MB 后装 32 片） |
| 知识库 RAG（线上） | 走前端同路径 `POST /api/chat/stream` 实测 **7.8 s / 811 字真实回答**（含 ENSDARG00000029688、ZDB-GENE-990415-91、Bgee 表达分）→ `data/faiss_index`（463 MB+85 MB）与 4B 模型**维度与模型一致、可用** |
| 库-索引差 | 数据库 `literature_paper` **32,549** 行 vs 索引 32,532 篇 = **17 篇（0.05%）**：其中 10 行 `gold_status = pdf_only(9)/pdf_fallback_not_gold(1)`（按设计不入索引），余 ~7 篇疑似切分 0 块；快照之后**无新增**（`created_at > 2026-09-10` = 0）→ 差额稳定、不扩大 |
| 磁盘 | `$RT` 下 6 个 `faiss*` 目录合计 **163 GB**（活跃的 `_v2_merged` 62 GB）；`/data` 尚余 753 GB → **不删任何索引**（7 个 `deploy/server/*.sh` 与 5 个 `scripts/*.py` 仍引用 `_v1/_v2`，删了会断） |

### 二、省资源档（共享服务器友好，已上线）

| 配置 | 值 | 作用 |
| --- | --- | --- |
| `HUB_WARMUP_RAG` | `0` | **本次新增开关（默认 1）**：启动只加载 0.5B 轻量模型，4B 嵌入与知识库索引改为首次访问按需加载 |
| `HUB_PUBMED_WARMUP` | `0` | 不预热文献分片 |
| `PUBMED_YES_SHARD_RESERVE_MB` | `6000` | 装不下就不装，返回体带 `partial` |
| `EMBEDDING_DEVICE` | `cpu`（原 `cuda:0`） | 显式锁 CPU；机器上 2×V100 常被别人占满，不经 `hub_supervise.sh` 直接启动也不会去抢显存 |

实测：空闲 RSS **35.2 GB → 2.5 GB**（cgroup anon 2.5/36 GB，余量 33.5 GB），**显存 0**（GPU 上只有别人的两个进程）。

**状态标签已改名**：`pubmed_status` 由 `low_mem` 改为 **`on_demand`**，并新增 `pubmed_on_demand` / `pubmed_skip_reason`；语义上区分“按配置不预热（正常）”与“内存不足（告警）”。

> ⚠️ **运维注意**：省资源档下 `pubmed_ready=false` 是**预期状态**；`scripts/three_hour_watchdog.py`、`hub_full_live_test.py`、`hub_regression_min.py` 等**按 `pubmed_ready` 判活**的脚本不要在此档运行（会反复重启 Hub）。

### 三、部署漂移（待人工合并，已加防御）

| 项 | 实情 |
| --- | --- |
| 缺文件 | 服务器缺 `agent/intent_router.py` → `POST /api/chat` 的旧模式（`mode=rag\|db\|fusion\|gene`）报 `ModuleNotFoundError`（**500**）；原因：只有传旧 UI 模式才走这个 import，Hub 前端走 `/api/chat/stream` 与 `answer_scope`，所以一直没暴露 |
| 已修 | 已补传 `agent/intent_router.py`（md5 两侧一致 `027dd672…`） |
| 内容漂移 | **6 个文件双向漂移**：`agent/agent_scheduler.py`、`agent/hub_orchestrator.py`、`agent/workflow_engine.py`、`core/config.py`、`database/gene_id_mapper.py`、`database/gene_toolkit.py`。服务器侧含 `ROUTE_A_*` 热修（本地仓库没有），本地侧含 `run_general_assistant` 等新功能（服务器没有）→ **不能单方向覆盖**，需要人工 3-way 合并 |
| 防御 | `api/api_server.py` 对 `run_general_assistant` 缺失时**优雅降级**（记告警 + 走通用路径），不再把整个 `/api/chat` 打成 500 |
| 回归 | `POST /api/chat` `mode=db` → **200**，23.5 s，2,664 字真实回答；`/api/chat/stream` 正常；`/api/retrieval/debug` 正常 |

### 四、首页精简 + 文献更新入口（2026-10-09，已上线）

| 项 | 内容 |
| --- | --- |
| 需求 | ① 去掉首页精灵鱼相关；② 首页按 Potato Agent 的样子简化；③ 把「文献更新」做到首页上 |
| 首页结构 | 只剩四块：品牌句+单行搜索 → **今日研究**（一天一篇真实文献）→ **文献更新**（历史列表 +「更新文献」按钮）→ **Cite Us**；删除指标墙/模块卡墙/研究影像流 |
| 新接口 | `GET /api/hub/research-feed` → `daily / updates / years / stats / maintenance`（逐段 try/except，无 `*_error`） |
| 新缓存 | `hub_recent_papers.json`（200 条、摘要 ≤600 字）由 `scripts/build_hub_recent_papers.py` 从 35,147 行 catalog 预抽；**API 不再逐请求解析 63 MB 大文件** |
| 新前端 | `web/css/home-2026.css`（2026-10-10 视觉升级，取代 `home-simple.css`）、`web/js/home-feed.js` |
| 更新闭环 | `agent/hub_maintenance.refresh_recent_papers()` 在文献任务成功后自动重建缓存（只读派生物，失败只记日志） |
| 语言一致性 | 默认英文。新区域带 `data-no-i18n="1"`，文案由 `home-feed.js` 按 `documentElement.lang` 出中英两套；`i18n.js` 补 3 条 hero 词条；实测英文模式首页**零中文残留**、中文模式零英文残留（数据字段除外） |
| 顺手修复 | 顶栏「中 / EN」按钮被 `.icon-btn` 固定 32px 挤成三行 → `#langToggleBtn` 自适应宽度；更新条目 title/detail 因 span 同行而断行 → 改 block |
| 浏览器验证 | 英文/中文各一遍 DOM 扫描；今日研究取到真实记录（PNAS 2026，DOI 10.1073/pnas.2511871123） |
| 自检 | `selfcheck.py --git` **8/8**（把 `参考文件/` 加入 `.gitignore` 与扫描排除项：该目录含其他项目 README 与服务器主机信息） |
| ⚠️ 重启教训 | uvicorn 收 SIGTERM 后会等 SSE 长连接关完，可能**永远不退出**（端口已关、进程还在、`hub_supervise.sh` 的 `wait` 不返回 → 不会自动重启）。此时必须 `kill -9`，supervisor 10 s 内自动拉起；`hub_guard.sh`（root cron）也会兜底 |

### 五、富集「方面/来源」标注 + 引用相关性打分（2026-10-10，已上线）

| 项 | 内容 |
| --- | --- |
| 需求 | ① 富集结果看不出「富的是哪一方面」；② 富集结果字小灰暗看不清；③ 文末参考文献混入无关论文（问 nanog 却挂纳米颗粒/纳米塑料），要求在既有打分方案上重做一个打分方案 |
| 富集方面 | `database/enrichment_toolkit.py` 补齐字段：GO `go_id/term_name/aspect/aspect_label/url/name_missing` + `aspect_summary`；通路 `source/source_code/source_label/url` + `source_summary`。GO 看三个方面（BP/CC/MF），通路看三个库（KEGG/WikiPathways/Reactome） |
| 富集显示 | `web/css/enrich-view.css`（新）+ `web/js/app.js::renderEnrichResult` 重写：顶部「这次富集看的是哪些方面」分布 + 每条卡片（方面/来源徽标、指标标签、外链、命中基因 chip），原始文本收进 `<details>` |
| 引用打分 | **RRS**（`llm/ref_relevance.py`）：实体 0.30 / 池内位次 0.25 / 正文引用 0.20 / 时效 0.15 / 元数据 0.10；门槛 0.50（无实体问句 0.40）；**点名了实体但题名不含且正文未引用 → 直接淘汰**；不足 2 条则不成表并清掉悬空 `[n]` |
| 接线 | `llm/zebrafish_prompt.py::_filter_by_relevance()`，在 `ensure_reference_footer()` 选号前过滤证据池（替掉「不足下限按序号补齐」的行为） |
| 规格文档 | `docs/引用相关性打分RRS.md`（权重表 / 三条硬规则 / 实体抽取坑 / 环境变量 / 调优手册） |
| 验收 | `scripts/test_ref_relevance.py` **11/11**（含 nanog 场景：无关文献全挡掉、只留 2 条相关；中文表型题不被过度过滤）；`scripts/test_enrichment_labels.py` **4/4**；回归 `scripts/test_phase_a_references.py` **8/8** |
| 接口实测 | `/api/tools/enrichment?genes=tp53` → `aspect_summary=[{生物学过程:4},{分子功能:1}]`、`source_summary=[{Reactome 反应通路:5}]`、条款带 `aspect_label` / `source_label` / `url` |
| ⚠️ 内存 | 交付时再次发现 uvicorn RSS 涨到 32.8 GB、cgroup **35.97/36.0 GB（99.9%）**——懒加载的 4B 模型与 FAISS 分片不会自己释放。已 `kill -9` 回收（RSS→0.7 GB）；`hub_guard.sh` 新增 `HUB_RSS_MAX_MB`（默认 20000）自动回收规则，**但 root cron 自 2026-10-09 12:10 起已不再执行它**，deng 账号又无 `crontab` 权限 → 需管理员恢复 cron 才能长期自动兜底 |

### 六、主界面二次收敛 + 首问提速 + 巡检自愈升级（2026-10-11）

| 项 | 内容 |
| --- | --- |
| 首页「更简更高级」 | 容器 1080→**1000px**、间距 28→**24px**、hero 上间距 34→**26px**；标题 `clamp(28px,3.2vw,40px)` + `max-width:20ch`（一行读完不折行超过 2 行）；副标题降到 13.5px / `--ink-3`（**主次分层靠亮度差，不靠加粗**）；**删掉 ZDB-GENE chip（4→3）**，把「Zebrafish Agent →」并入 chips 行右对齐（少一整行空白）；更新列表只显示 **3 条**（完整历史去文献页）；Cite Us 区块内边距压缩 20% |
| 首问提速：冷启动预热 | 实测**冷启动首问 238.5s** vs 热态 52.6/49.7s —— 瓶颈是 `HUB_WARMUP_RAG=0`（省内存）让 4B 嵌入 + 知识库索引要等**用户第一个请求**才同步加载。修法：`hub_watchdog.sh` 在重启后 **+65s 自动发一次后台 warm ping**（`curl -X POST /api/chat {"mode":"rag"}`），把这 4 分钟从「用户等」改成「后台跑」 |
| 提速：刊名补全不再逐条联网 | `rag/journal_enrich.py` 加**负缓存**（`JOURNAL_ENRICH_MISS_TTL_SEC`，默认 6 h，上限 2 万条）——原先查不到会每次重试；HTTP 超时 20s → **6s**（`JOURNAL_ENRICH_TIMEOUT_SEC`）。`llm/zebrafish_prompt.py::_enrich_papers_journals()` 加**整批时间预算** 2 s（`REF_JOURNAL_BUDGET_SEC`），超预算降级为缓存/本地 XML，每条引用最多 3 次联网 × 0.34s 间隔的开销被压住 |
| 巡检自愈升级 | `deploy/hub_watchdog.sh`：① 重启后自动 warm ping；② **回收判据重做**——原来只看 RSS>20 GB，热态本来就 18~32 GB，导致「刚热起来就被回收、下次访问再冷启动 4 分钟」；现改为 **cgroup 匿名内存≥95% 且 空闲≥15 min 且 存活>10 min** 才回收，不踢正在提问的人；③ 内存判据从 `free -m` 换成 **cgroup v2**：进程落在 `session-XXXX.scope`，其自身 `memory.max` 是字面量 `max`，真实的 35 GB 上限在祖先 `user-1005.slice`（要**逐级向上找**）；且只算 `memory.stat` 的 **anon**（`memory.current` 含页缓存，读 58 GB 分片后会假报 99%） |
| 巡检自愈实测 | 杀 uvicorn → **10s 自愈**（health 全程 200）；无人访问下 11:36:27 warm ping → 11:40:29 重栈就绪，RSS **2.5 GB → 32 GB 全在后台完成**；快照 `{"health":"200","action":"none","rss_mb":32000,"mem_pct":86}` |
| ⚠️ 踩坑 | `pkill -f hub_watchdog.sh` 不能和「启动命令」写在同一行——启动命令里的明文路径会被 `pkill -f` **反向匹配到自己这个 shell**，把自己连坐杀掉，脚本静默不启动。已加 `deploy/start_watchdog.sh` 专用重启入口 |
| ⚠️ 踩坑 | `llm/zebrafish_prompt.py` 用了 `time.time()` 却漏 `import time` → 自检 `refs-rrs` 2 项 `NameError` 失败（本地 12/12、服务器 13/13 修复后通过） |
| 自检 | `selfcheck.py --git` **10/10** |
| 引用「变少」真根因（2026-10-11 定性） | 用**界面真实请求体**（`mode=auto + answer_scope=hub`）实测：正文 9 个编号 / 文末 **20 条**，正常；但旧 `mode=rag` 路径（问答页切「知识库问答」）走 `agent_scheduler.run_task_detail`，**根本不生成参考文献段** → 正文 `[1]..[5]` 全成悬空编号。修法：`api/api_server.py` 新增 **`_ensure_footer_any_path()`** 全路径兜底（正文有 `[n]` 而无「参考文献」段时，用 `result["context"]` 证据池补出；实测日志 `[引用] 兜底补出文末参考文献：5 条`） |
| 打分规则定位修正（用户要求“在原机制上改”） | 原来的错误是把 RRS 做成“门槛淘汰”，不符合原机制。现已改为**加权精排层**，原链完全不动：`FAISS 粗排 500 → CrossEncoder 精排 100 → 【RRS 五维修加权重排】取前 25/12 → 文末 20~25 条`（`.env` 已定 `REF_FOOTER_MIN=20 / MAX=25`）。`REF_RRS_MIN` 退为只供显式 `min_score=` 的调试开关，默认流程不做门槛淘汰 |
| 打分规则不再展示在界面 | 按用户要求移除问答页详情栏里的「引用打分规则」折叠块（HTML + CSS + i18n 词条一并删干净，不在界面里胉示规则） |
| 语言一致性（界面语言优先） | 前端 `app.js` 随请求发 `lang`；后端 `ChatRequest.lang` → `zebrafish_prompt.set_answer_language()`（ContextVar，在 `_run_chat_core` 里设，因为流式路径用 `threading.Thread`，路由层设不会传播；线程池复用所以要**每次都设**）。规则：英文界面 → 全文英文；中文界面 → 除基因符号/ID/库名/组装名/PMID/DOI 外全中文，禁中英混排。实测：EN 界面 + 中文提问 → 中文 0 字；ZH 界面 + 英文提问 → 去链接后中文占比 77% |
| 引用条数验收（修复后实测） | 真实界面：机制题正文 13 个编号 / 文末 **20 条**；事实/其他题 18 个 / **19 条**；旧 rag 路径由兜底补出 5 条（原来 0 条悬空） |
| 「对数影响因子」回到打分（2026-10-11 追加，含两次澄清） | 它原本只在**检索层**（`rag/journal_if.py` + `pubmed_yes_retriever.blend_score` 的 `w_if=0.04`），只通过 `rank` 间接体现；用户先问「怎么不见了」，再澄清「**是影响因子取对数，不是写死权重**」。最终实现：`RRS = 五维归一化加权 + IF_K·ln(1+IF)`（对数原值作**加成项**，不是归一化值×固定权重），`IF_K` 默认 0.01 → Nature `ln(1+50.5)=3.94 → +0.039`（与检索层 0.04 同量级）；未知刊加成 0（不惩罚）。权重表恢复五维（`meta` 回到 0.10）。表里有真数据：Nature 50.5・eLife 6.4・Development 4.6・PLOS ONE 2.9・Zebrafish 1.7 |
| ⚠️ 坑 1：浮点比较让开关静默失效（已修） | `_weights()` 里原写 `if w["meta"] - delta >= 0.0`，实际 `0.06 - 0.060000000000000005 = -6.9e-18` → 条件为假 → 开关完全无效。该函数的旧设计已在「对数作加成」重构中移除；教训保留：合法性判断一律 `x <= limit + 1e-9` + `max(0.0, …)` |
| ⚠️ 坑 2：Py3.10 f-string 嵌套同类引号 | `f"if={w2["if"]}"` 本地 .venv（3.13）不报，服务器 **3.10 直接 SyntaxError**。改为先取变量再插值 |
| 「相关度加权」补上连续分（2026-10-11，用户问「相关度的加权体现在哪」） | 排查发现：上游 `rel_n`（归一化语义相关度）**只用于算 blend 排序，分数没落盘** → 下游 RRS 只能拿「名次」当相关度代理（相邻名次的分数可能差很多/几乎一样）。**方案 A**：`rag/pubmed_yes_retriever` 把 `rel_n` 存进 hit 与 group，证据池编号行写出 `相关#3 rel=0.871`（头部同步告知模型“不要写进答案”）；`zebrafish_prompt.rels_from_numbered_context()` 解析 → RRS 新增 `rel` 维 0.10（从 `rank` 0.25 里拆：`rank 0.15 + rel 0.10`，相关度总量仍 0.75）。**拿不到分数时 0.10 自动退给 `rank`** → 旧路径尺度不变（测试 H1 锁定）。另加 `rel=` 泄漏清理（`ensure_reference_footer` 开头） |
| ⚠️ 真事故：检索因嵌入模型竞态整条失败（已修） | 实测 `hub.log`：16:06~16:07 **连续 3 次** `PubmedYesRetriever 失败：Cannot copy out of meta tensor; no data!` → 文献检索整条链失败 → **答案没有证据池也没有引用**（表现为“引用少/没有”）。原因：并发加载（知识库 RAG 线程 + PubmedYes 首检）的 accelerate/torch 竞态，**预热路径本就有重试，检索路径没有**。修法：`rag/rag_builder.py::BgeEmbedding._build_model()` 加 3 次重试（仅针对 meta-tensor/to_empty 错误，其他异常直接抛），3s 间隔 |
| 行内引用回到方括号 | 按用户要求把上标胶囊徽标改回朴素 `[1]`：`web/js/chat-polish.js` 加 `CITE_BADGE=false` 开关（关掉时连 MutationObserver 都不挂，省流式输出期间每 350ms 一次的全量 DOM 遍历）；实现保留，改回 `true` 即可恢复胶囊 |
| ⚠️ 待管理员 | `hub_guard.sh`（root cron）仍失效、`deng` 无 `crontab` 权限 → 目前由新版 `hub_watchdog.sh`（自带 5 分钟循环）完全兜底；建议恢复 root cron 或给 `deng` crontab 权限 |

### 七、四项新功能：别名 / 复制体 / sgRNA·引物 / 主界面改版（2026-10-12）

用户给了 4 项任务（并明确「不懂的点先问」），先提问确认方案再动手：① 别名只在「基因身份/别名」类问题触发 + 先用本地题名匹配再对命中别名做向量检索；② 复制体只用本地 `homology_gene` 推导，展示在正文小节 + 详情卡片；③ sgRNA/引物做**本地引擎 + 一键跳转对标**；④ 只有 Genes 做成真功能页，其余入口标「即将上线」；品牌图替换顶栏 logo / 聊天空态 / 首页 hero / favicon。

| 项 | 内容与实测 |
| --- | --- |
| 主界面改版（对齐 `6.png`） | 页头改成**白色胶囊导航**（首项为蓝色品牌按钮 `🐟 Zebrafish Agent`）+ 居中大标题/副标题 + 两栏：左「Daily Zebrafish Research」（`English/中文` 胶囊、绿色更新状态条、日期胶囊、**2～3 条论文**、检索框 + chips、文献更新、Cite Us），右品牌卡（徽标 + `ZEBRAFISH AGENT` + `— AI for Zebrafish Research —` + Quick Start/Sign in/Register + 条款）。旧首页的检索/更新/引用**全部保留**在左卡内。导航新增 **Genes**（真功能页）与 **Dashboard**，`Coming soon → Tools`；Genomes/Expression/Phenotype 保留入口但内容标「即将上线」。新文件：`web/css/home-agent.css`、`web/css/tools-agent.css`、`web/js/agent-home.js` |
| 品牌图（`12.png` → 全站） | `scripts/make_brand_assets.py` 从 `12.png` 生成 `zf-agent-mark.png`（900²，含字全图→首页右卡）、`zf-agent-emblem.png`（512²，圆形徽标→顶栏 logo / 聊天空态 / 功能页空态）、`favicon.png`(64²)、`apple-touch-icon.png`(180²)；`index.html` 加 `<link rel="icon">`；设计稿归档 `docs/design/` |
| 任务① 别名（首句列全部历史名） | 新增 `llm/gene_alias_evidence.py` + `gene_toolkit.alias_block()`：tp53 实测 **13 个别名**（brp53/drp53/etid22686.5/fb40d06/p53/wu:fb40d06/zdb-gene-990415-270/zgc:111919 + 描述名）；题名级扫描 1.96 s，命中 **p53 169 篇 / tp53 32 篇**；真实问答实测首句：`斑马鱼 tp53 的标准符号为 tp53，历史上还被称为 brp53 / drp53 / …（NCBI）`，并如实说明哪些别名在文献中没出现过 |
| 任务② 复制体 / WGD 分工 | `gene_toolkit.paralog_block()/co_orthologs_by_human()` + `GET /api/genes/orthologs`：`sox9b → Human SOX9 → [sox9b, sox9a]`、`MITF → [mitfa, mitfb]`、`hsp70l → HSPA2 → 5 个复制体`；真实问答实测输出小节「基因组复制与分工」+ 表格（`斑马鱼复制体 | 人/鼠同源基因 | 相似度 | 证据文献编号`），18 条引用、无 `rel=` 泄漏 |
| 任务③ sgRNA / 引物本地引擎 | `database/crispr_toolkit.py`（657 行）+ `POST /api/tools/crispr/design`、`POST /api/tools/primer/design` + `web/js/crispr.js`。实测：51 个 NGG 候选、**1.55 s**（含一次批量 BLAST 脱靶筛查，近全长 ≤3 错配）；返回 T7 模板 `TAATACGACTCACTATAG+G+spacer`；引物给定基因名 → cDNA 模板 + `Tm 59.8/60.2 ℃`（**SantaLucia 1998 最近邻**，非旧 GC 公式）+ 近同源命中基因名（sox8b/sox9a）。界面对标按钮：CHOPCHOP / CRISPRscan / Primer3web（先复制序列再跳） |
| 任务④ Genes 功能页 | 复用功能页骨架（`#/genes`）：搜索（先算符号、再 ZFIN/NCBI/UniProt 别名、Entrez/Ensembl ID）与「浏览全部」；实测 `brp53 → tp53`、`ZDB-GENE-990415-270 → tp53`；点行出基因卡（基础 / 别名 chips / 复制体 / 表达 / GO / KEGG / 文献）。后端 `GET /api/genes/search` 加 `browse=1`，新增 `GET /api/genes/orthologs`；`GET /api/gene/{name}` 增 `aliases` / `paralogs` 两个块 |
| 首页论文多条 | `/api/hub/research-feed` 新增 `daily_list`（同候选池顺延 3 条，仍按日期确定性选取）；实测页面渲染 **3 条**（含 PubMed/DOI 外链与免费全文） |
| 实测踩坑 | ① `PrimerRequest.template` 原 `min_length=10` → 输基因名 `sox9b`（5 字符）直接 422；已降到 2。② 特异性初版把 **15～16 nt 子串**也算近同源 → 数字虚高；改为「近全长（≥len−2）+ 错配阈值」并把命中**基因名**列出来（sox8b 是真实同族，不是误报）。③ 脱靶筛查原只扫前 24 个候选，导致排到前面的候选 `specificity` 是默认值；已改为全量扫描 |
| 服务器验证 | 服务重启后 watchdog 日志 `health=000 -> respawn supervise / respawn done health=200 after 15s`；`mem_pct` 判定走 cgroup `anon`（86%）；新端点全部实测通过；静态资源经 `scripts/push_web.py`（备份 + 校验）推送，无需重启 |
| 本节自检 | `py_compile` 全部通过；`node --check` 6 个 JS 文件通过；浏览器实测：EN/中文切换、首页 3 条论文、Genes 浏览/检索、sgRNA 20 候选表 + T7 复制按钮 + 跳转链接 |
| 待办/边界 | 本地 sgRNA/引物只做**转录组（GRCz12tu CDS）**层脱靶，不是全基因组；未复刻 Doench/CRISPRscan 活性模型（口径见 `docs/sgRNA与引物设计.md`）；`fish-swim.png` 精灵鱼动画**未替换**（它是装饰动效，替换成静态 logo 会破坏动画，需用户确认） |

### 八、按 6.png 全站改版 + 全基因组脱靶（2026-10-12 第二轮）

用户三项指令：① 精灵鱼换 12.png 静态图并去掉动画；② 上全基因组级脱靶（下载 GRCz12tu 基因组 + 建库）；③ root cron 暂不处理；另加：**完全按 6.png 改**（背景同款、**其他界面也要适配**、图里的框做成下拉框）。

| 项 | 内容与实测 |
| --- | --- |
| 全站背景（6.png 同款） | 新增 `web/css/theme-agent.css`（最后加载）+ `web/assets/brand/agent-bg.svg`（鱼/DNA/水泡/海草剪影）：浅蓝渐变 + 极淡平铺剪影，**首页/问答/工具页/欢迎弹窗全站共用**（浏览器实测 `body::after` 已挂载 `agent-bg.svg`，功能页与问答页均为白卡片 + 18px 圆角 + 柔和投影） |
| 去掉动效背景 | 问答页原来的**真实照片轮播（每 5.5 s 换一张）**与功能页波浪/照片舞台全部停用（`.chat-atmosphere` 等 `display:none`；`initChatReefFlow()` 内 early return，代码保留可一键恢复）；`fish-swim.png` 精灵鱼本就在仓库里已无引用（`fish-swim.css` 不存在），按用户要求统一为 12.png 静态徽标 |
| 覆盖权重坑 | `simplify.css` 大量用 `body.theme-atelier … !important`；只写 `.feature-card{...!important}` **盖不动**（实测圆角仍是 12px）→ 必须抬到同等级 `body.theme-atelier .feature-card`，改后实测 18px ✓ |
| 「框 → 下拉框」 | 顶栏：`Genomes / Genes / Expression / Phenotype / Tools / Agent / About` 全部是下拉框（About 新增「关于本平台 / 数据来源与算法 / 引用方式」）；首页**日期由胶囊升级为日期下拉框**（最近 14 天，切日期即按该日重新确定性选取，界面明写"同候选池内按日期确定性选取"）；sgRNA/引物面板新增**脱靶范围下拉框**（转录组 / 全基因组） |
| 日期下拉 | `GET /api/hub/research-feed?day=YYYY-MM-DD`（`md5(day)%pool` 确定性选取）；前端 `home-feed.js` 生成选项 + `change` 重新拉取。⚠️ 踩坑：`toISOString()` 按 UTC 换日，UTC+8 下"今天"会算成昨天 → 改为用本地年月日拼 ISO（实测修前首项是 10月11日、修后 10月12日 ✓） |
| 全基因组脱靶（②） | 下载 NCBI `GCF_049306965.2_GRCz12tu_genomic.fna.gz`（约 1.09 GB）；**为什么这么费劲**：服务器国际出口单连接仅 12–30 KB/s，**8 路分段并行合计 75–163 KB/s**；国内镜像（清华 1.28 MB/s）没有 Ensembl/UCSC 基因组，CNGB `ftp.cngb.org/pub/ncbi/genomes/` 只镜像部分 accession（`/049/` 404）。做法：`$RT/blast/dl_genome.sh` 8 路并行 + 断点续传 + 重试，下载完 `build_genome_db.sh` 哨兵自动 `makeblastdb -out genome_db` |
| 引擎改动 | `crispr_toolkit`：`_batch_blastn(db=...)` 可指定库；`genome_db_status()`（未建库返回原因不报错）；`offtarget_scan(scope=genome)` 只扫前 12 个候选并返回**染色体+坐标**（全基因组无基因名）；API `offtarget_scope` 透传；界面显示当前范围并在未扫到的候选上标"未测"（**不再默认给满分**） |
| 优雅降级实测 | 库未就绪时 `POST /api/tools/crispr/design {offtarget_scope:"genome"}` 返回 `ok=True` + `offtarget_note: offtarget_skipped: 全基因组 BLAST 库未就绪（缺 …genome_db.nin）…`，候选照常给出（0.08 s），不 500 |
| 本轮自检 | `py_compile`（api/database）+ `node --check`（app/home-feed/crispr/i18n）通过；浏览器实测：首页背景+日期下拉（今天 2026-10-12 ✓）+ 3 条论文、Genes 页白卡 18px、问答页三栏正常且背景静态、CRISPR 页两个范围下拉就位 |
| 待补 | 基因组建库完成后的全基因组脱靶实测数据（本节后续补） |
| **全基因组脱靶完成（2026-10-13）** | 基因组 FASTA 下载完成：`GCF_049306965.2_GRCz12tu_genomic.fna.gz` **435,810,822 B（≈415 MB）→ 解压 1.47 GB（25 条染色体）**；`makeblastdb` 建库 345 MB（`.nsq`），rc=0。实测：sgRNA 全基因组模式 **6.8 s**（只扫前 12 候选），命中能给出 `NC_133187.1:1310178-1310156 (mm1)` 这类**染色体+坐标+错配数**；引物全基因组模式 **6.6 s**（前 8 对，报“全基因组内近全长 ≤2 错配命中 N 处”+位点）。对比：同一 sox9b 转录组模式 6.2 s |
| ⚠️ 下载踩的两个坑 | ① 我一度把文件大小当成 1.09 GB（实际 415 MB）→ 第一版脚本“提前完成”且 `cat part.*` 合并出错、`gunzip` 失败，白花数小时；**修正**：先验 `Content-Length` + 合并后比对总字节 + `gzip -t` 通过才解压（`dl_genome2.sh`）。② 服务器国际出口**速率波动极大**（20～238 KB/s），且会随时间下降 → 解法是**重开连接**（实测 70 → 238 KB/s）；最终一次重开后拿了 10 MB/s，**43 秒下完**。③ 国内镜像不可用已逐一实测（清华/USTC/南大/上交/BFSU 无基因组镜像；CNGB `/049/` 404） |
| 能力探测接口 | `GET /api/tools/crispr/capabilities` → `{transcriptome:{ready,size_mb}, genome:{ready,size_mb,fasta,error}}`；前端据此把「全基因组」选项置灰并显示库大小（未就绪时置灰 + 后端返回 `offtarget_skipped`，不伪造数字） |

### 九、中英文双语完整性：中文模式全中文 / 英文模式全英文（2026-10-13）

用户指令：「中文模式除原是英文外全改中文，英文模式全改英文。」

**做法（先量、再修、再量）**：写了一段全站扫描脚本，对 19–20 条路由 × 中/英两模式，展开所有 `details`
后收集**文本节点 + `placeholder/title/aria-label/alt` 属性**，分别统计「中文模式下仍为纯英文」与
「英文模式下仍含汉字」的条目（排除 API 路径、JSON 片段、crontab 命令行等标识符）。

| 项 | 实测 |
| --- | --- |
| 修前 → 修后 | 中文模式残留英文 **89 → 31**；英文模式残留中文 **110 → 1** |
| 剩余 31 条是什么 | **全部是原生英文专名/术语**，符合用户口径不翻译：`Zebrafish Agent`、`BLAST`、`KEGG`、`GO`、`cDNA`、`GRCz12tu`、`GRCz11`、`CHOPCHOP ↗`、`CRISPRscan ↗`、`Primer3web ↗`、`SpCas9 · NGG`、`SaCas9 · NNGRRT`、`NCBI RefSeq`、`RefSeq CDS`、`FAISS · CE`、`≤5 MB`、`· 3xx/4xx/5xx`、示例 ID（`p53 30590 ZDB-GENE-990415-270`）、`title=BLAST` |
| 剩余 1 条 | 服务端 429 详情（中文、带秒数）——已加规则 `请求过于频繁，请 Ns 后重试` → `Too many requests — retry in Ns` |
| 根因①（结构性） | 运行时词典只做「中→英」精确匹配 + 前缀/正则规则，**英文模式下由规则生成的动态文案没有反向条目**，切回中文后残留英文（典型：`Context: 0 turns`、`Found N records`、`Imagery N`） |
| 修法① | `i18n.js` 引擎改为**原文缓存（WeakMap）**：每个文本节点/属性记住最初原文，切换语言**从原文重译**，不再在已翻译结果上再翻一次；自己写入的变更用「上次输出值」判据跳过，业务侧改了文案则重记原文 → 翻译**可逆**，规则/前缀类文案也能回到中文 |
| 根因②（写反） | 词条写成 `"Dashboard": "看板"`（方向反了）→ 英文模式冒出中文「看板」、中文模式却是英文「Dashboard」。已改为 `"看板": "Dashboard"` |
| 根因③（JS 拼句子） | 运维看板与 `provenance.js`（「数据来源与算法（X）」）是**中文原稿 + JS 拼接**：kv 行被 `<b>` 拆成碎片（`窗口` / `条访问日志（扫描 20000 行）· 独立来源` / `· 状态`），词典逐字翻译会碎；标题又是 `数据来源与算法（` + 面板名 + `）` 拼接 |
| 修法③ | `provenance.js` 改为**源头双语**（新增 `PANELS_EN`，按当前语言渲染、`hub-lang-change` 时重建、整块 `data-no-i18n` 自管，不再交给词典）；运维页把碎片片段与**动态**文案进规则表：`1 周/3 天/6 小时/… 分钟/… 秒`、`条访问日志（扫描 N 行）· 独立来源`、`· 状态 2xx`、`读取失败：X`、429 提示 |
| 词条策略 | 一条 `中文 → English` 词条会自动生成反向 `EN2ZH`（first-wins 去重）→ **中英两侧一起修**。新增/修正约 110 条：导航段（`Genomes/Expression/Phenotype/Operations/Dashboard/Chats/Workbench/Local library`、`Gene detail/Database query/Knowledge-base Q&A/Multi-model fusion`）、运维页 40+ 条、导出/清空/工作流预设等 `title` 与 `aria-label`、`租户：默认实验室`、上传通道说明、`引用方式 → Cite Us` |
| 反向对齐 | 把工具页里**英文短语式徽标**改回中文原稿，英文再由词条反向给出：`One-shot→一键汇总`、`Multi-layer→多层整合`、`Batch ready→支持批量`、`FASTA out→FASTA 输出`、`RAG ingest→RAG 入库`、`Multi-source→多来源`、`Fisher exact→Fisher 精确检验`、`BH-FDR→BH-FDR 校正`、`unique / ambiguous / not_found→唯一 / 歧义 / 未命中`、`Expression · GO→表达 · GO`、`Gene workbench→Gene Workbench`（对齐既有词条值，避免大小写不一致导致反向失效） |
| ⚠️ 前端部署坑 | **只加 `?cb=` 不够**：它只换 HTML 的缓存键，子资源仍命中旧 `?v=`（实测页面里 `script src` 还是旧版本号，改了 JS 却"看不到效果"）。改 `web/js|x.css` 必须同步升级 `index.html` 的 `?v=` |
| 验证 | `selfcheck` 9–10/10；浏览器实测 20 条路由：中文模式导航 `搜索 \| 基因组▾ \| 基因▾ \| 表达▾ \| 表型▾ \| 工具▾ \| 文献 \| 智能体▾ \| 看板 \| 关于▾`，英文模式 `Search \| Genomes▾ \| Genes▾ \| Expression▾ \| Phenotype▾ \| Tools▾ \| Literature \| Agent▾ \| Dashboard \| About▾`；**点界面按钮切 zh→en→zh 往返一致**（往返后 `enCJK=1`，仅剩「中 / EN」开关自身，故意 `data-no-i18n`） |

**顺带的一次内存处置（2026-10-13 探测）**：巡检查到 uvicorn 运行 4 h 10 min、**RSS 32.2 GB（余量仅 770 MB）**
→ `kill -9` 让 `hub_supervise.sh` 重拉（约 15 s）+ `POST /api/warmup`，RSS 回到 0.6 GB、余量 31.9 GB。
随后核实守护策略属**设计内**：`hub_watchdog.sh` 只在「cgroup **匿名**内存 ≥95% **且** 空闲 >900 s **且**
运行 >600 s」才回收（避免把正在提问的人踢掉），另有 `HUB_RSS_MAX_MB=34000` 兜底；
健康接口报的 `mem_used/mem_limit` 含页缓存，会比守护判据悲观（当时 86% vs 99.9%）。

### 十、聊天区中英残留修复 + 视觉层「Glass Ocean」+ 主界面背景图（2026-10-13 第三轮）

用户三项反馈：① 英文模式下"参考文献 / 原文链接 / 引用与溯源"仍是中文；② 界面还不够美观，
希望参考同类站点（马铃薯 AI，ynnu.edu.cn）的整体观感（但保留我们的导航/功能）；
③ 主界面要像 `6.png` 那样有背景图。

**(1) 聊天区为什么一直不改中文 → 英文**

| 项 | 内容 |
| --- | --- |
| 根因 | 「引用与溯源」面板、`参考文献` 标题、来源徽标是**渲染在聊天答案气泡里**（`.bubble.md-body`）的 UI 外壳，而 i18n 把 `.md-body` 整块列入保护（防机器翻译破坏答案）→ 外壳也跟着不翻 |
| 修法 | ① 给这些外壳打 `data-i18n-live="1"`（`prov-panel`、`md-refs` 的 `h3`、`prov-answer-badges` 行）；② 引擎新增 `walkLive()`：`setLang` 与 MutationObserver 都会**单独再走一遍**带标记的区域（因为递归 `walk()` 遇到被保护的父节点就停了，到不了标记处，防抖 180 ms） |
| 顺带放开 | `SKIP_SEL` 去掉 `.tool-result / .tool-results / #chatLog`——工具结果区除 `<pre>/<code>` 外都是我们自己渲染的 UI；要保护的单个容器标 `data-no-i18n="1"` 即可。放开后发现并补齐：`结果来自`、各页"口径说明"（`身份/坐标 = Ensembl…` 等 9 条，新增 `TOOL_METHOD_EN`）、`本地库/本地全文库` 徽标（`source-badges.js` 改为双语标签）、About 弹窗（`nav-menu.js` 的 `aboutHtml()` 改为双语 + 切语言重建） |
| 验证（构造真实气泡） | 英文模式下 `参考文献 → References`、`引用与溯源 → Citations & sources`、`引用条目（1）→ Citations (1)`、`本条信息来自 → This answer draws on`；再切回中文可逆（`References → 参考文献`）|
| 仍保留中文的地方 | 引用条目**正文**（模型输出/数据）、用户提问、答案正文——这些属于内容，不翻译（口径与"中文模式除原是英文外全改中文"一致：学术专名 BLAST/KEGG/cDNA/GRCz12tu/CHOPCHOP… 一律保留原文） |

**(2) 视觉层「Glass Ocean」`web/css/glass-agent.css`（全站，必须排在 theme-agent.css 之后）**

参考同类站点的观感（浅蓝渐变底 `#f7f9fd→#edf2fb`、毛玻璃顶栏、白卡 + 大柔光投影、深蓝主按钮），
用我们自己的 CSS 重新实现，保留原有 DOM 与功能（导航下拉、每日研究、检索、引用、工具页、脱靶范围下拉）：

- 顶栏：`rgba(255,255,255,.55)` + `backdrop-filter: blur(16px)` + 16px 圆角 + 柔光；导航胶囊 12px 圆角、hover 抬升、active 蓝底
- 卡片：`rgba(255,255,255,.62~.72)` + blur(18~20px) + 1px 白边 + `0 26px 70px rgba(8,14,28,.14)` + 22px 圆角（首页两栏、工具面板、模态、prov 块统一）
- 按钮：主按钮 `#1d2d50`（含 `#homeSearchBtn` / `#sendBtn`，见下方优先级坑）、次按钮毛玻璃白
- 列表：论文条目改分隔线式（标题 700、期刊/PMID 用强调蓝），`agent-updates / agent-cite` 改 18px 玻璃块
- 全部规则限定 `body:not(.dark)`，**暗色主题完全不受影响**

**(3) 主界面背景图（水下场景，对应 6.png）**

- 新增 `web/assets/brand/ocean-scene.svg`：斑马鱼（白条纹剪影，4 条不同大小/角度）+ 左右海草丛 + 右侧 DNA 双螺旋 + 气泡 + 水体渐变，全部用低透明度绘制
- **坑 1（关键）**：把场景做成 `body::before/::after`（`position: fixed` + `z-index: 0`）**完全看不见**——`.app` 是 `position: relative; z-index: 1`，内容层整片盖在上面（实测：临时插 `z-index: 5` 的红块可见、`z-index: 0` 不可见）。**修法：背景画在 `html` 的 canvas 背景上**（多背景：场景图 + 两层径向高光 + 线性渐变，全部 `background-attachment: fixed`），canvas 永远在所有内容之下
- **坑 2**：`html body.theme-atelier { background: #f9fafb !important }`（0,1,2）会盖住画布 → body 的透明化必须写 `html body.theme-atelier:not(.dark)`（0,2,2）才赢
- **坑 3**：两条大鱼原本正好落在白卡后面 → 重排场景元素到卡片外缘与上方，并把 hero 做成"场景带"（`background: 渐变 + ocean-scene.svg`，`center 22%`，26px 圆角）——标题浮在水下场景上，主界面一眼就能看到背景图（实测 hero 带里能看到鱼、气泡、DNA）
- **坑 4（按钮优先级）**：`#homeSearchBtn` / `#sendBtn` 在 `style.css`/`simplify.css`/`refine.css` 里是 **ID 选择器 + !important**（`--accent` teal 或 teal 渐变），类选择器压不住；聊天发送键还被 `#view-chat #chatForm #sendBtn` 三个 ID 盯上 → 必须升到同级（`html body:not(.dark) #view-chat #chatForm #sendBtn`）并把 `background-image: none` 一起写死
- **坑 5（窄屏）**：1144px 视口下 11 个导航项会折行/被截断 → ≤1400px 顶栏改**两行布局**（上排 ☰ + 状态/账号/语言，下排导航整行铺开），实测 `scrollWidth == clientWidth`（不截断）
- **坑 6（重复文案）**：Agent 菜单原为 `Gene detail <small>基因详情</small>`，双向词典生效后中英两侧都重复（`Gene detail Gene detail`）→ 改为中文原稿 `基因详情`（英文由词条反向给出），删除重复 small

| 本轮自检与实测 | 结果 |
| --- | --- |
| `selfcheck.py` | 9/9（含 frontend 语法） |
| 公网页实测 | 顶栏毛玻璃 + 两行不截断；卡片 22px 圆角 / 62–72% 白；主按钮 `rgb(29,45,80)`；`html` 背景含 `ocean-scene.svg`；hero 场景带渲染出鱼与 DNA；问答页右上可见淡场景水印；工具页（CRISPR）玻璃卡片 + DNA 水印 |
| 双语回归 | 中文模式导航 `搜索\|基因组▾\|基因▾\|表达▾\|表型▾\|工具▾\|文献\|智能体▾\|看板\|关于▾`；英文模式 `Search\|Genomes▾\|…\|Dashboard\|About▾`；切换往返一致 |
| 服务器探测 | `health=200`、RSS 18.1 GB（热态）、watchdog `action=none` |

**(4) 首页次要信息改折叠框（2026-10-13 第四轮，用户要求）**

用户："这块内容最好在一个框内展现即可，是下拉框，然后需要看全貌的话下拉就可。"

- 把 `.home-updates`（文献更新）与 `.home-cite`（引用本平台）由 `<section>/<footer>` 改成 `<details class="… agent-fold">`：
  **默认收起**，`<summary>` 常显标题 + 状态（文献更新后面跟"已是最新（每 168 小时自动检查）"）+ 右侧 ▾（展开时旋转 180°）
- 「更新文献」按钮从 summary 移到正文里——否则点按钮会连带触发展开/收起
- 实测（收起态）：文献更新框 **52 px**、引用框 **47 px**（原来两块合计约 560 px）；展开后分别 382 / 178 px，内容与按钮齐全
- 中英双语都验证：中文 `文献更新 已是最新…▾` / `引用本平台 ▾`，英文 `Latest Updates Up to date (auto-checked every 168 h) ▾` / `Cite Us ▾`；展开后正文无中文残留，`aria-label` 也随语言切换（`home-feed.js` 只按 ID 取元素，改成 `<details>` 后不用改 JS）

**(5)「限定范围」滚动区 + 下拉/分段控件对齐同类站点（2026-10-13 第五轮）**

用户："像土豆这样限定范围的下拉框，不然不好看。"

先说清同类站点（`potato-agent.ynnu.edu.cn`）实测到的做法（**只取观感，自己写 CSS**）：

| 它们 | 实测值 |
| --- | --- |
| 每日研究列表容器 `daily-updates-scroll` | **固定高 463 px**、`overflow-y: auto`、**`scrollbar-width: thin`**，内容 7263 px 在里面滚 |
| 原生 `select.model-select` | `appearance: auto`（保留原生弹层）+ 透明底、**`padding: 4px 24px 4px 2px`**（右侧留箭头位）、13px/650 |
| 语言分段控件 `.daily-updates-language` | 容器 `rgba(20,33,61,.06)` + `radius 8px` + `padding 3px`；选中项**白底 `radius 6px`、13px/700、高 32px** |

我们照此改（`glass-agent.css`）：

- **列表限定范围**：`.agent-papers`（每日论文）与 `.home-upd-list` 固定 `max-height: 460px` + 内部滚动 + `scrollbar-width: thin` + 自绘 8px 圆角滑块（实测 `clientHeight 460 / scrollHeight 872`，滚动条已出现——这正是用户截图里强调的那根滚动条）
- **原生下拉统一**：所有 `select` 改为 `appearance: none` + 圆角 10px + 1px 细边 + hover/聚焦态 + **自绘 11px 箭头**（`background-position: right 9px center`）+ `scrollbar-width: thin`；搜索条里的「范围」下拉保留胶囊形以贴合输入框
- **语言分段控件**：容器 8px / padding 3px / 浅灰底，选中项白底 6px、13px/700、高 30px（实测 中文 按钮 `class=active`、`background: #fff`）
- 问答页会话列表也加 `max-height + thin` 兜底

**(6) 视觉层「Clean Ocean」`web/css/clean-ui.css`（2026-10-15，用户："不够美观，你学习一下土豆的界面"）**

上一版（glass-agent）走"毛玻璃 + 大圆角 + 大投影"，观感偏"糖果感"；这一轮把同类站点
（PotatoOmics）的**设计事实**逐项量下来，照它的"克制、发丝线、单一强调色"重做一层：

| 维度 | 它们的实测值 | 我们改为 |
| --- | --- | --- |
| 页面容器 | 1129 px | `.agent-home` max-width **1130**、padding 16/14/40 |
| 卡片 | 圆角 **8 px**、`rgba(255,255,255,.68)`、边 0.67px 白、阴影 `0 26px 70px rgba(8,14,28,.18)` | 同值（原来 22 px + 更大投影） |
| 卡片头 | padding **22 24 16**、gap 18、发丝下边线 | 同值 |
| 字阶 | 面板标题 **24/700**、条目标题 **17/750 lh1.42**、摘要 **15/400 lh1.62** | 同值（原 17/600、12.5px 等混杂） |
| 状态条 | **通栏**浅绿 `rgba(22,101,52,.07)` + 深绿字 13/700 + padding 9 24 | 同值（原来是小胶囊带边框） |
| 导航条 / 项 | 条：半透明白 + 14 px 圆角 + padding 8 + `0 18px 42px rgba(8,15,30,.12)`；项：**高 40 / 圆角 8 / 14px/800 / padding 0 16** | 条同值；项高 38 / 圆角 8 / 14px/750 |
| 按钮 | 高 **50** / 圆角 **16** / 16px/600 / `#1d2d50` + `0 14px 30px rgba(29,45,80,.18)` | 同值（原来 44 px + 999 px 胶囊） |
| 分隔线 | 0.67~1 px `rgba(20,33,61,.1)` 贯穿列表 | 全站列表/表格改发丝线 |
| 底色 | `linear-gradient(#f7f9fd → #edf2fb)` | 同值；水下场景降为"很淡的水印"（叠一层 86~92% 白纱），兼顾之前"要有背景图"的要求 |

另外把强调色**收口**：旧样式里按钮/链接是 teal（`--accent:#0E7C6B`、`--zf-teal-*` 渐变），
与深蓝主色并存显脏 → 在浅色主题下把这几个变量统一改为深蓝，工具页"扫 PAM 并打分"、
选中 tab、聊天发送键实测全部为 `rgb(29,45,80)`、圆角 10 px。

实测（浏览器）：首页卡片 8px/0.68/`0 26px 70px`、卡片头 76 px 高且 `22 24 16`、标题 24/700、
状态条通栏 763 px（= 卡片宽）浅绿、条目 `18 24 17` + 标题 17/750 + 摘要 15/400、
导航项 8 px/13.5/750、品牌按钮深蓝 8 px、主按钮 16 px、搜索条 14 px；
工具页面板 8 px/0.68、`#crisprRunBtn` 与选中 tab 深蓝 10 px；聊天发送键深蓝 10 px。

### 十、导航"点了不跳转"修复（2026-10-15）

用户："为什么点击这些基因组基因之类的按钮无法实现正确跳转。"

**先实测定位（不猜）**：逐组打开下拉、点其中的链接、比对 `location.hash` 与渲染出的面板：

| 导航分组 | 菜单内容 | 点击结果 |
| --- | --- | --- |
| **基因组 / 表达 / 表型** | **一个 `<a>` 都没有**，只有 `nav-soon-item`（`aria-disabled="true"` 的"即将上线"） | **永远不跳转** ← 用户遇到的正是这个 |
| 基因 / 工具 / 智能体 / 关于 | 都是真实链接 | 正常（点「基因库搜索」→ `#/genes` + `view-feature` ✓） |

**修法**：

1. 三个"死菜单"接上真实可达的页面（保留一条诚实的"即将上线"在最下面）：
   - 基因组 → `BLAST 序列比对`(#/blast，GRCz12tu 基因组/转录组库) + `序列提取`(#/sequence)
   - 表达 → `基因表达`(#/gene，Bgee / Expression Atlas) + `批量检索基因`(#/genes)
   - 表型 → `表型相关文献检索`(#/literature) + `基因功能注释`(#/annotate)
2. **父级按钮第二次点击直接进主页面**：给各组按钮加 `data-nav-primary`，`nav-menu.js` 里"菜单已展开时再点父级 → 关菜单并跳主页面"（否则用户点父级只看到菜单开合，观感就是"点了没反应"）。

**验证（25 个链接全量点测）**：`failures: []` —— 6 个分组 25 条链接，每条 `location.hash` 与预期一致、渲染面板也正确（blast→toolBlast、sequence→toolSequence、genes→toolGenes、gene→toolGene、literature→toolRetrieval、annotate→toolAnnotate、enrich/kegg/id/crispr/upload→各自面板、ops→toolOps、chat→view-chat）；父级二次点击实测 `#/blast` ✓；同时补齐 11 条新菜单文案的中英词条。

## 服务器运行状态（2026-10-05 已确证，不再是"需人工确认"）
- **根因已确证并修复**：见上文「线上事故与修复（2026-10-03）」——Hub 启动预热把内存吃满 `user-1005.slice`
  的 36 GB 上限，被内核 OOM 秒杀（`memory.events` 的 `oom_kill` 持续增长），外部 root cron 的
  `hub_guard.sh` 每分钟保活 → 现象是"每 2~3 分钟无 traceback 掉线"。
  修复后实测：Hub **连续存活 13.5 小时**、负载 **0.13**、CPU **0.5%**、RSS 19 GB。
- **保活机制**：`deploy/hub_guard.sh` 由 **root cron 每分钟**执行（`deng` 用户无 `crontab` 权限，
  自身无法增删该定时任务）；它按 `/api/health` 判定并拉起 `hub_supervise.sh` + `uvicorn` + 隧道。
  ⚠️ 因此**手工杀掉 Hub 会在 1 分钟内被自动拉起**，"停服"必须由管理员摘掉该 cron。
- **公网地址**：由 **cloudflared 快速隧道**提供，**每次重启隧道域名都会变**。
  当前实例（2026-10-05 实测 `/api/health` 200）：`https://caught-monsters-remix-probe.trycloudflare.com`。
  权威来源以服务器文件为准：`cat <RT>/public_url.txt`、`hub_guard_last.json` 的 `public_url` 字段，
  或 `/api/health` 的 `public_url`。若需**固定**地址：改用 named tunnel（固定子域）或服务器 Nginx + 443（需服务器侧改动）。


---

## 十二、三个导航区"真数据化"：Genomes / Expression / Phenotype（2026-10-15）

用户要求"这些功能都要真正实现"。此前三个导航分组里只有指向别处的链接，本次各自落成**独立页面 + 本地真实数据**，
不再有"即将上线"占位；每页都写清数据来源、单位、版本与口径。

### 12.1 后端数据层（新增 3 个模块）
- `database/genome_toolkit.py`：组装登记（`assembly_registry`）、染色体表（长度/GC/基因数/密度）、本地比对库规模。
  染色体长度与 GC **由本机 GRCz12tu FASTA 逐条实测**（`$RT/build_chrom_lengths.py` 一次扫描 35.5 s 写入
  `$RT/genome_info.json`，25 条 / **1,448,791,966 bp** / 平均 GC 37.11%），FASTA 里没有 kind 字段，
  染色体号从头部"chromosome N"解析后按 `NC_` 登录号回退，不按行序硬编号。
- `database/expression_toolkit.py`：本地表达表 `gene_expression`（**319,344 行 / 24,245 基因**）的三种用法
  ——单基因组织谱（Bgee 表达分 0–100）、某组织 Top 基因、多基因×共同组织矩阵。
  **Bgee 与 Expression Atlas 单位不同（分 / TPM）分区块呈现，不跨组比较**（Bgee 行的 `expression_stage`
  存的是质量分而非分期，这一点写进了页面说明）。
- `database/phenotype_toolkit.py`：ZFIN 官方表型注释 `phenoGeneCleanData_fish.txt` 本地解析为 **SQLite 索引**
  （避开 MySQL 建表权限问题；临时文件 + `os.replace` 原子替换，构建失败不破坏旧索引）。
  实测结果：**171,079 条注释 / 8,029 个基因 / 20,971 个「解剖实体+PATO」术语组合**，构建 3.4 s。

### 12.2 接口（新增 10 个）
`GET /api/genome/overview`、`/api/genome/chromosome`、`/api/expression/gene|tissues|top|compare`、
`/api/phenotype/stats|gene|search`、`POST /api/phenotype/build-index`（后台线程、幂等、可强制）。

### 12.3 前端（新增 3 个页面）
`web/js/{genomes,expression,phenotype}.js` + `#toolGenomes/#toolExpression/#toolPhenotype` 面板；
基因组页用条形图展示 25 条染色体长度分布并可按染色体追到基因列表；表达页三模式（基因谱 / 组织 Top / 多基因对比）；
表型页双向检索 + 常用术语一键查询 + ZDB-PUB 文献号直连 zfin.org + 本地文献兜底。
三页都接入统一外壳（主视觉、输入卡、结果卡、`data-i18n-live`）与溯源面板（来源徽章 + 口径说明）。

### 12.4 过程中发现并修掉的 5 个真问题（都不是猜的）
1. **ZFIN 列序整体偏移**：原实现把 `ao_id` 读成第 6 列，实际第 7 列才是 ZFA ID → 页面显示的是 `ZFA:…/PATO:…`
   编号而不是术语名。用"逐列填充率统计"确定真实列序后修正，并把统计写入 `meta.file_stats`
   （主文件 171,079 行 **0 跳过**；`pheno_fish.txt` 列结构不同 68,330 行未纳入，明确记录而非默默丢弃）。
2. **符号口径差异导致 `tp53` 查不到表型**：本地库主符号是 `p53`、ZFIN 用 `tp53`，ZDB-GENE 号也有新旧两版
   → 改为"符号 / gene_name / 全部别名 / 输入词"多候选匹配，取命中最多的，并把 `matched_symbol`
   与 `tried[{term,rows}]` 返回页面（实测 `tp53` **146 条 / 75 组 / 40 篇文献**，别名 `brp53`、Ensembl ID 均可命中）。
3. **`IN (SELECT … LIMIT n)` 静默失败**：导致基因库字段返回 null 而不报错 → 改两步查询 + 检查 `success`。
4. **直接打开 URL 时新页面空白**：`app.js`（同步脚本）先于 defer 脚本执行，事件已发过 → 模块内自查路由补一次初始化。
5. **英文模式残留中文**：溯源注记取错页面（旧标签条默认 active）+ 后端说明文案只有中文
   → 前者按可见面板反查 key，后者改成 `note/note_en`、`label/label_en` 成对返回，并补 `成体/热应激/应激` 词条。
   修后实测三页英文模式 **CJK 残留 = 0**。

### 12.5 验证
- 后端：`/api/genome/overview` 25 条染色体 / 1.45 Gb；`chromosome=5` 返回基因列表；
  `/api/expression/gene?name=tp53` → 18 个组织（cleaving embryo 97.9 最高）+ 3 个 Atlas 分期；
  `/api/expression/top?tissue=spleen` → hbba1 100、hbaa1 100（血红蛋白，符合脾脏生物学）；
  `/api/phenotype/search?q=curvature` → whole organism·increased curvature（96 基因）、post-vent region（81）、
  vertebral column（60），候选基因 hsbp1b/kif6/ptk7a/dnah10。
- 前端：三页中英双语实测通过，排版沿用 Clean Ocean 层（卡片 8 px、条目 17/750 + 15/400、按钮 16 px）。

### 12.6 三页 + Hub 的 5 分钟巡检与自愈（`$RT/zf_page_selfcheck.py`）

上线后按"每 5 分钟看一次、卡了就修"的要求，在服务器上跑一个轻量巡检（后台、`setsid nohup`、48 轮 ≈ 4 小时）：

- **断言 5 项真实接口**（不是只 ping 端口）：`/api/genome/overview` 有 `"ready":true`、
  `/api/expression/gene?name=tp53` 有 `"ok":true`、`/api/expression/tissues` 覆盖 1,151 个组织、
  `/api/phenotype/stats` 有 `"index_ready":true`、`/api/phenotype/gene?name=tp53` 有 `"matched_symbol":"tp53"`。
  断言用"双方都去掉空白后比较"，避免 `{"ok": true}` 这类空格差异造成**误报**。
- **自愈两类**：① health 非 ok 或 ≥2 项断言失败 → `kill -9 uvicorn`，由既有 `hub_supervise` 在 10–15 s 内拉起；
  ② **内存濒危**（uvicorn RSS > 12 GB，或宿主 headroom < 2 GB）→ 同样回收。两次自愈之间 15 分钟冷却，
  避免反复重启打扰正在使用的人。
- **实测效果**：首轮即命中内存阈值（RSS **18,515 MB**、headroom 8,306 MB）→ 自动回收后
  **RSS 549 MB**、宿主 `mem_used` 从 36.8 GB 降到 20.8 GB、headroom 回到 **24.4 GB**，`health_after=true`。
  这正是历史上"内存打满被 OOM 秒杀"的前兆，现在由巡检在阈值处提前化解。
- 记录：`$RT/logs/zf_page_selfcheck.log`（每轮一行 JSON）、`$RT/zf_page_selfcheck_last.json`（最新一轮）、
  运行日志 `$RT/logs/zf_page_selfcheck_run.log`。
- 踩坑记录：写巡检脚本时用 `pkill -f 脚本名` 会**匹配到自己那条命令行把自己杀掉**（命令行里含脚本名），
  必须拆成"只杀"与"只启动"两条独立命令，或用 `zf_page_selfcheck[.]py` 这种方括号写法。

---

## 十三、回应"工具栏功能去哪了 / 下拉三角形蒙蔽视听"（2026-10-15）

用户三点意见，逐条落成改动：

### 13.1 功能没丢，但入口被藏了 → 恢复常显工具栏
诊断结论：14 个工具页 + 3 个组学页 + 运维/关于 = **16 个真实页面一直都在**，但入口全部塞进顶部下拉，
而旧版横向标签条（`#toolTabsLegacy`）被我之前的改版设成了 `hidden`，于是"看不到有多少功能"。
修复：新增 `web/js/tool-rail.js` + `.tool-rail` 样式，在功能页主视觉下方铺一条**常显工具栏**
（16 个入口、当前页高亮、右上角标注 "16 tools"），标签按语言直接渲染，`hashchange` / `zf-feature-open` /
`hub-lang-change` 都会重绘。实测：`count: 16`，`active: Gene workbench`，`display: flex`。

### 13.2 About 原来是"假入口"（3 条都指向 #/home）→ 做成真页面
新增 `#/about` 页面（`web/js/about.js` + `#toolAbout` 面板），三章都是实测内容：
- **定位与范围**：平台做什么 + 7 行数据规模表（26,321 基因 / 145,945 别名 / 31,085 同源 / 319,344 表达行 /
  171,079 表型注释 / 25 条染色体 1.448 Gb / BLAST 库 / 32,863 篇文献）；
- **数据来源与算法**：6 个数据源逐一列"用在哪" + 5 条算法口径（BLAST+ 2.17、Fisher+BH、粗排 500→精排 100、
  表型多候选符号匹配、Bgee 分 vs Atlas TPM 不跨组比较）；
- **引用方式**：可复制的 Cite 文本（自动填访问日期）+ 建议共引的原始数据库 + 边界与免责
  （计算结论需实验验证；缺失一律"无记录"，不做零值填充）。
顶部 About 三项改为真实路由 `#/about/scope`、`#/about/data`、`#/about/cite`（章节直跳）。

### 13.3 下拉里重复项充数 → 换成各分组独有入口
- Expression：`基因工作台`（与 Genes 重复）→ **多基因表达对比**（`#/expression/mode=compare`）
- Phenotype：`表型相关文献`/`基因功能注释`（与 Literature / Tools 重复）→ **表型术语排行**（`#/phenotype/mode=term`）
  与 **按基因看表型**（`#/phenotype/mode=gene`）

### 13.4 借鉴马铃薯 Daily Research 的"目的" → 补上闭环
马铃薯首页的价值不在样式，而在**每天一眼看懂新进展、并能立刻往下查**。对照我们的首页 Daily Zebrafish Research
（已有：日期选择 + 标题 + 期刊 + PMID + 摘要 + PubMed 链接），缺的是"进入本库"这一步：
现在每条新增 **「在本库检索 →」**（`#/literature/<题名>`，直达本地全文检索台并预填题名），
把"速览"接到"深查"，形成闭环。实测 `daily_list = 3`（2026-10-13），链接已随 feed 渲染。

### 13.5 本轮修掉的两个"改了却没效果"的坑
1. **改了 JS 却没提升 `?v=`**：新增的 `#/about` 路由在浏览器里始终回落到首页 —— 因为 `app.js` 的缓存版本号没变，
   浏览器仍在用旧脚本。规则重申：**只要改了 js/css，必须同步 bump `index.html` 里的 `?v=`**。
   （本轮顺带补 bump：app.js / i18n.js / provenance.js / home-feed.js / about.js / clean-ui.css）
2. **同步渲染被外壳清空**：`about.js` 首次同步 `render()` 填好的内容，随后被 `app.js` 打开功能页时
   `toolResult.innerHTML = ""` 清掉（新三页因为是"先取数后渲染"才没暴露这个问题）。
   修法：首次渲染延后一拍（`setTimeout(render, 80)`），与外壳的清空动作错开。

### 13.6 巡检守护的阈值修正（重要，避免"修理变成打扰"）
首版把内存阈值设成 12 GB 就回收，但实测 **uvicorn 预热后常驻 18.4 GB 属正常值**（4 分钟内即到位），
于是"自愈"变成了周期性重启，公网出现多次 502（cloudflared 日志 `connection refused`）。
现改为 **RSS > 28 GB 且 headroom < 1.2 GB 才回收**，冷却 15 分钟；修正后 uvicorn 连续运行不再被误杀，
公网 health/index 稳定 200。巡检节奏按用户要求回到 **每 7 分钟一轮**。

---

## 十四、布局重做（2026-10-15 二轮）：工具栏分组化 + 状态栏不再吓人

用户反馈"这个布局太难看了"。诊断：上一版把 16 个入口平铺成两行 pill 标签云，无结构、和顶部导航抢视觉。

### 14.1 工具栏 v2：按用途分两行，行首带组标签
- 结构：`FIND & SEQUENCE`（基因工作台 / 基因库搜索 / 基因组总览 / 表达谱 / 基因–表型 / BLAST / 序列提取 / 跨库 ID 映射）
  与 `ANALYSIS & DATA`（富集 / 通路 / sgRNA·引物 / 一键注释 / 文献检索台 / 入库 / 运维看板 / 关于）—— 各 8 项，一行放得下、不滚动。
- 视觉：去掉彩色 pill（改透明底 + hover 淡蓝 + 当前页实心渐变蓝），行首 84 px 大写小字组标签，右上角极淡 "16 个工具 · 点击直达"（<1280 px 自动隐藏）。
- 动效与响应式：`transition .15s`；<860 px 自动改为"组标签在上、条目在下"的竖排。

### 14.2 布局精修：输入卡收窄、结果卡加宽
`grid-template-columns: minmax(320px, 372px) minmax(0, 1fr)`（<1020 px 单列）—— 长结论与宽表格更好读。

### 14.3 About 左卡：从"三个大色块按钮"改为章节导航列表
整行宽、左对齐、右侧 `›`、当前章节淡蓝底 + 左侧 3 px 竖条（`::after` 箭头在面板被 park 时用 `content: none` 关掉，避免伪元素泄漏到其它页）。

### 14.4 状态栏不再长期显示"仍在加载"（用户会把这句话理解成"卡住"）
原来只要"文献库/模型预热中"就显示 pending 文案，而核心（MySQL + RAG）其实可用。现在：核心可用时显示绿色
**「服务在线」**，预热细节（模型 / 检索库 / 文献库）放进 `title` 悬浮说明；只有核心不可用才显示"仍在加载…"。

### 14.5 本轮抓到并修掉的一个真 bug
**About 内容串页**：表达谱页的结果区里出现了 About 的"数据规模表" —— `about.js` 的延迟回调
（`hashchange` / `hub-lang-change`）没有路由守卫，把 About 内容画到了别的功能页。修法：`render()` 开头
`if (!isAbout()) return;`，语言切换回调同样加守卫。修后实测：`#/expression` 结果区正确显示空态
"尚无表达查询"，`#/genomes` 显示 "Loading genome information…" 后出真数据。

---

## 十五、全局布局重设计：新增独立设计层 `web/css/polish.css`（2026-10-15 三轮）

用户要求：全局重排、**所有功能与顶部功能栏全部保留**、在现有基础上"好看简洁高级一些"。

### 15.1 为什么单独开一层
现有样式是 `style.css` → `simplify.css` → `refine.css` → `theme-agent.css` → `glass-agent.css` → `clean-ui.css`
共六层叠加，再往上加不敢改。本轮新建**第七层** `polish.css`（`index.html` 里最后引入），
原则：**只覆盖视觉属性（颜色/字号/圆角/阴影/间距），不动任何布局结构与功能**，便于随时回滚。

### 15.2 设计语言 "Slate & Ink"（"高级"的具体做法）
1. **卡片去玻璃化**：不再用半透明卡片 + `0 26px 70px` 大阴影（这个组合是"糖果色"观感的根源），
   改**纯白卡 + 1px 细边 + 双层轻阴影**（`0 1px 2px / 0 10px 28px rgba(15,27,51,.06)`）。
2. **圆角统一**：卡片 12 / 控件与按钮 8 / 小徽章 6（此前卡片 8、按钮 16，圆得发浅）。
3. **字号阶梯**：30 / 24 / 16 / 14 / 13 / 12.5，行高 1.25~1.7；表头 11.5px 大写小字。
4. **蓝色只用在两处**：可点的主操作（主按钮、当前工具）与当前所在位置（active 态）。
5. **间距按 4 的倍数**：卡内 20、区块间 18、元素间 8~12。
6. 全局统一：细滚动条、`:focus-visible` 蓝环、正文 `antialiased`、极淡冷灰页底 (`#f7f9fc → #f2f5fa`)。

### 15.3 覆盖到的区域（全部保留原功能）
顶部导航（高度/字号/hover/下拉卡）· 状态徽章（绿点 + 低饱和色）· 分组工具栏 · 功能页 hero
（去掉大面积蓝渐变与装饰光斑，改白卡 + 左侧 3px 蓝竖条、并去掉与顶栏重复的品牌行）· 输入/结果卡 ·
按钮与表单（统一 40px / 8px / focus 蓝环）· 表格（表头灰、行 40px、hover 淡蓝）· 首页 Daily 文献卡与折叠卡 ·
欢迎与侧栏按钮 · 聊天气泡与输入条 · 响应式（≤1020 单列、≤720 字号下调）。

### 15.4 验证
截图逐页确认：首页（Daily Research + 侧栏）、`#/genomes`（hero + 工具栏 + 键值卡 + 染色体表）、
`#/expression`、`#/phenotype`、`#/about` 均正常；顶部主导航 10 个条目与工具栏 16 个入口全部保留，
**本轮零功能改动**。

---

## 十六、向 PotatoOmics 取长补短（2026-10-15 四轮）：每个功能页都有「本页数据」

对着土豆的 Genome Browser 页比对后，差距不在配色而在**信息结构**：它左栏先摆一张参数表
（`Default locus`: Assembly / Genome Type / Sample / Reference / Size 776,674,386 bp / Genes 38,176 /
Transcripts 61,208 / DOI），一眼就知道"这页有什么数据、口径是什么"。本轮补齐同类能力。

### 16.1 新增 `web/js/page-meta.js`：16 个功能页的「本页数据」卡
- 位置与土豆一致：**输入控件之下、同一栏内**的键值表（标签左、值右、虚线分隔）。
- 内容两层：**静态事实**（版本、覆盖对象数、口径 —— 全部本库实测数字，保证接口不通时也有内容）+
  **动态事实**（`genomes` / `expression` / `phenotype` / `literature` 四页直接打真接口刷新）。
- 举例（Genomes 页实测渲染）：Assembly `GRCz12tu (GCF_049306965.2)` · Sequences `25` ·
  Total length `1.45 Gb` · Mean GC `37.11%` · Annotated genes `26,321` · BLAST libraries `cdna 51.9 MB / genome 345.5 MB`。
- 由 **`tool-rail.js` 驱动调用**（它的 render 已在各种时序下验证可控），并做路由守卫，避免画到别的页。

### 16.2 顶部新增「数据带」一行
参考土豆顶部那行数据库全称，我们在主导航下方加一行实测数据带：
`本地注释 26,321 基因 · 表达 319,344 行 / 24,245 基因 · ZFIN 表型 171,079 条 · 文献 32,863 篇 · 染色体 25 条 / 1.45 Gb`
（中英双语，窄屏自动省略号截断）—— 让"这个库有多大"在任何页面都可见。

### 16.3 本轮真正咬人的坑（值得记住）
1. **`?v=` 版本号替换静默失败**：用脚本 `str.replace('...20261018b','...20261018c')` 时若原文不是那个值，
   替换不报错、也不生效 → 浏览器继续用旧 JS（旧版没有静态事实）→ 表现为"代码明明改了却完全没反应"。
   连续踩了两次。**规矩：改版本号必须回读校验（本次已加 `print('bump ok' if 新值 in 文本 else 'BUMP FAILED')`）**。
2. **静态兜底不能省**：数据卡最初只有动态事实，接口慢/瞬时失败时整块空白；补上静态事实后即使离线也有内容。
3. 数据卡容器改为**按需自建**（找不到 `#featureAside` 就在输入卡体内插入一个），不再依赖 index.html 的时序。

---

## 十七、按"推荐步骤"全部补齐（2026-10-15 五轮）：三项一次做完

### 17.1 首页右侧 Agent 卡：三颗按钮纵向排齐（对齐 PotatoOmics）
`#agentQuickStart / #agentSignIn / #agentRegister` 改为整行宽、左对齐、纵向堆叠（`flex: 0 0 100%`），
与土豆右侧卡片的 Quick Start / Sign in / Register 一致。实测截图：Quick Start（主色）与 Sign in 上下排列。

### 17.2 基因组可视化（补上"看得见的图"，不再只有表格）
新增**核型总览** `.zf-karyo`（`genomes.js` 的 `karyoHtml()`）：25 条横条，
**宽度 ∝ 染色体长度、颜色深浅 ∝ 基因密度**（三档 is-low/mid/high），悬停显示
`chrN · 长度 Mb · 基因数 · 个/Mb`，**点击直接列出该染色体基因**（走已有 `loadChrom`）。
实测渲染：`chr1 60.2 Mb · 1208 genes · 19.1 per Mb`（is-high）、`chr5 75.3 Mb · 1368 · 17.3`（is-mid）……
全部来自本机 GRCz12tu FASTA 实测 + 本地注释库计数，无一处估算。

### 17.3 反馈闭环（对齐土豆右下角 Feedback）：前后端都做了
- 后端新增 `POST /api/hub/feedback`：校验 4–4000 字符、同进程 60 s 内最多 5 条（内存限流），
  追加写入 `$RT/feedback/feedback.jsonl`；**不采集 IP / 账号**（只记录用户主动填的内容 + 页面地址）。
- 前端新增 `web/js/feedback.js` + 右下角固定按钮与面板：内容框、可选联系方式、
  提交 / **复制诊断信息**（URL、语言、主题、UA、时间）/ 关闭，Esc 可关，`Ctrl/⌘+Enter` 提交。
- 实测：正常提交 → `{"ok":true,"saved_to":"…/feedback/feedback.jsonl"}`，落盘记录
  `{"at":"2026-10-14 01:22:20","message":"自检：核型图与反馈闭环上线验证","contact":"selftest","page":"#/genomes"}`；
  2 个字符的内容被拒（HTTP 400）。

### 17.4 顺带修掉：数据卡重复显示两遍
动态事实成功后原实现又拼了一遍静态事实（同一项显示两次）。改为：**动态成功只显示动态，动态为空才回退静态**；
同时给静态值加 `VALUE_EN` 映射（如 `25（1–25 号染色体）` → `25 (chr 1–25)`），保证英文模式不出现中文值。

---

## 十八、v3 布局重做（2026-10-21）：删掉重复入口 + hero 双栏 KPI

### 18.1 按用户意见删除"功能页上方那条工具条"
用户指出：工具栏（顶部 Tools 下拉）的职责就是"把功能相近的收拢、点击跳转"，
功能页再放一条 16 入口的横条属于**明确重复**。处理：
- 删除 `#toolRail` 容器与 `tool-rail.js` 引用（文件保留以便回滚，默认不再加载）。
- 顶部主导航 10 项、Tools 下拉 16 项**一个不少**，跳转能力完全保留。

### 18.2 hero 改成"左文案 + 右 KPI 大数字"双栏（视觉上的明显升级）
- `.feature-stage` 由单栏块改为两列栅格（`minmax(0,1fr) minmax(0,392px)`），窄屏回落单列；
  波浪/光斑装饰层 `display:none` 不再占位。
- 右侧 6 张 KPI 卡（`.fk-item`：19px 粗体值 + 11px 大写小标签，浅蓝渐变底 + 1px 蓝边）：
  Genomes 页实测为 `GRCz12tu (GCF_049306965.2)` / `25` / `1.45 Gb` / `37.11%` / `26,321` / `51.9 / 345.5 MB`。
- 同时清掉结果卡里与 KPI 重复的那组键值（同一页不重复展示同一信息）。

### 18.3 KPI 的数据与渲染放在 `app.js`（同步执行，最稳）
调试过程中发现：`page-meta.js` 这类 **defer 脚本**在当前页面（含欢迎门/多脚本）下经常赶不上路由首屏，
KPI 位置长期空白。最终方案：KPI 数据表 `PAGE_KPIS` 与渲染函数 `renderFeatureKpis()` **内联进 `app.js`**
（页面路由的主控，必定执行），在 `openToolPage()` 里**同步渲染**；`page-meta.js` 退化为可选增强。
16 个页面各有自己的 KPI（例：Phenotype `171,079 / 8,029 / 20,971`；Literature `32,863 / 29,221 / 500→100 / 170 shards`）。

---

## 十九、界面重做：「Paper & Ink」极简学术风（2026-10-15）

### 19.1 为什么要再做一次（问题定性）
此前界面累计叠了 **12 层样式表**（style → simplify → refine → home-2026 → chat-2026 →
enrich-view → home-agent → tools-agent → theme-agent → glass-agent → clean-ui → polish），
每层都在"加东西"：光斑、波浪、玻璃拟态、彩色渐变、6 套阴影、漂浮圆角卡。
结果是配色不统一、层级夸张、同一信息重复出现。所以这轮**不调细节，先收敛系统**。

### 19.2 结构：删掉一切重复入口
| 删除项 | 原因 |
| --- | --- |
| 左侧 `side-rail`（H/Q/G/B… 11 项） | 与顶部工具栏重复；用户明确"只保留工具栏与 logo" |
| `page-meta.js`（整文件不再引用） | 它会在输入卡里**再画一套** KPI，与 hero 右侧重复 |
| 顶栏 `.top-dateline` 超长数据行 | 挤压首屏；已移入首页 hero 下方（`.home-dateline`）作为数据规模说明 |
| 波浪/光斑/空状态光球/聊天区装饰动画 | 纯装饰噪声，`display:none` |

### 19.3 视觉令牌（新的唯一配色来源）
纸白 `#fafaf9` + 纯白卡 + 近黑 `#101418` 字 + **唯一强调色**深青 `#0e7f74`；
分隔一律 1px 发丝线（`rgba(16,20,24,.09)`），卡阴影压到 `0 1px 2px`；
数字统一 `tabular-nums`；微标题 11px 大写 + `.11em` 字距；正文 13–14.5px / 1.7。
深色模式 = 同一套令牌换值（`#0b0f10 / #121719 / #eef2f2 / #4fd1c5`）。

### 19.4 版式：功能页 hero 改"编辑式页头 + 规格清单"
左侧：短横线 + 青色 eyebrow → 33px 紧字距标题（`-.024em`）→ 60ch 导语 → 顿号分隔的元信息（不再是胶囊）。
右侧：把 6 张彩色 KPI 方卡换成**发丝线分隔的规格清单**（标签左、等宽值右），更像实验室数据表。
首屏即可读到本页关键数字，且与"输入/结果"两张卡在视觉上分层清晰。

### 19.5 踩到的三个坑（都已修，供后人省时间）
1. **顶栏被旧层强制折成两行**：`clean-ui.css` 把顶栏做成漂浮玻璃卡 + 白胶囊导航 +
   深色 logo 块；`glass-agent.css` 又在 `@media (max-width:1400px)` 里用
   `order + flex-basis:100%` 把导航挤到第二行（类计数 3 > 我的 2，所以我的 `!important` 输了）。
   → 对策：新层统一用 `html body.theme-atelier …` 前缀（3 类 + 2 元素）压过去，
   并把 `<a class="nav-brand-pill">` 从 `<nav>` 里**移出来**做独立品牌块，
   导航才能在窄屏整行铺开而不折字。1420px 以下改为"logo 行 + 工具栏行"两行式。
2. **深色模式下配色被还原**：`refine.css` 有 `body.theme-atelier.dark .top-nav-link
   { color: rgb(201,219,222) !important }` —— 它比 `html body.theme-atelier .x` 多一个类。
   → 对策：深色再写一层带 `.dark` 的覆盖块（第 11 节）。
3. **中文长标签被挤成竖排**：`button { white-space: nowrap !important }` +
   `.tool-row { flex-wrap: wrap }`（宁换行不压扁）。

### 19.6 顺带修掉的 i18n 时序 bug
KPI 标签语言取自 `<html lang>`，而 i18n 是"先画牌、再切语言"，导致中文界面出现
`Assembly / 基因` 半中半英混排。→ 在 `app.js` 同时监听 `hub-lang-change` **和**
`<html lang>` 的 MutationObserver，并延后一拍重渲染。已实测 6 个标签全中文。

### 19.7 验证与部署
- 本地预览：`scripts/preview_web.py 8099`（把 `/static/*` 映射到 `web/`，带离线演示 API），
  可在服务器停机时照常验收界面。
- 已用浏览器计算样式逐项核对：hero 透明无卡、`#featureTitle` 33px/620/近黑、
  卡片白底 + 发丝线 + 12px 圆角、`.fk-v` 右对齐等宽。
- 浅色 / 深色 / 中文 / 英文 / 多页面（genomes、blast、chat、home）均已实测。
- 已同步到服务器静态目录（`push_web.py`，三文件 `SAME`）；**本项目服务当前处于停机状态**
  （`$RT/HUB_STOPPED` 哨兵），下次 `scripts/project_procs.py resume` 后即为新版界面。
- 回退方式：删掉 `index.html` 里那一行 `premium.css` 引用即可（其余文件未改动视觉以外的逻辑）。

### 19.8 工具栏去重（2026-10-15 续）
问题：同一功能在两个下拉里各出现一次（用户指出 BLAST 既在「基因组」又在「工具」）。
用脚本把工具栏所有入口按 `href` 统计，查出 **6 组重复**并逐一归位：

| 重复项 | 原出现位置 | 现在只保留 |
| --- | --- | --- |
| BLAST 序列比对 | 基因组▾ / 工具▾ | 基因组▾ |
| 序列提取 | 基因组▾ / 工具▾ | 基因组▾ |
| 基因工作台 | 基因▾ / 工具▾ / 智能体▾ | 基因▾ |
| 文献检索台 | 工具▾ / 顶层「文献」 | 顶层「文献」 |
| 运维（Operations） | 智能体▾ / 顶层「看板」 | 顶层「看板」 |
| 默认问答 | 顶层「搜索」/ 智能体▾「Hub 多 Agent」 | 顶层「搜索」（并加 `data-nav-mode="auto"`，点击即回到自动路由） |

去重原则（写进代码注释）：**一个功能在工具栏只出现一次**；
只有"同一页面的不同模式"才允许汇聚在下拉里（表达▾、表型▾、智能体▾ 的模式项，
与 `#/expression/mode=…`、`#/phenotype/mode=…` 同一惯例）。
入口总数 33 → 26。

同时处理了同类问题——**页内重复导航块**：
聊天页顶部原有一排 `.mode-tabs`（Hub 多Agent / 基因详情 / 数据库查询 / 知识库问答 /
多模型融合），与工具栏「智能体」下拉是同一套入口，正是用户此前要求删除的模式。
处理方式：视觉隐藏（`display:none`）但**保留 DOM** —— 因为 `nav-menu.js` 是通过
点击 `.mode-btn` 来切换模式的，直接删按钮会让下拉失效。
为不丢"当前模式"的可见性，改为一行的 `#chatModeIndicator`（"当前模式 · 数据库查询"），
由 `app.js` 的 `syncModeIndicator()` 在切模式/切语言时同步。
实测：点下拉「数据库查询」→ 指示器更新、隐藏按钮 `active` 变为 `db`；点顶层「Search」→ 回到自动路由。

顺带补了 3 个**原本就漏翻**的导航项（英文模式下会露出中文）：
`多基因表达对比`、`表型术语排行`、`按基因看表型` 及其副标题，共 6 条 i18n 词条。
校验：英文模式下工具栏 33 个标签 **0 个中文残留**。

### 19.9 布局链修复 + 方块尺寸（2026-10-15 续二）

**① 首页“每日斑马鱼研究”是空的（其实是我自己造成的预览假象）**
根因两层：
- 首页内容来自 `GET /api/hub/research-feed`，而本地预览服务 `scripts/preview_web.py` 没实现该接口
  → 501 → “每日一文 / 更新历史 / 年度分布” 全空；**线上一直是好的**（真实接口在 `api/api_server.py`）。
- 修法不是造数字：新增 `scripts/fetch_preview_data.py`，把服务器上**真实产物**抓成本地快照——
  `$RT/hub_recent_papers.json`（200 条候选池）+ MySQL 真实查询（论文总数 32,549；年度分布
  2026=670/2025=1509/…；`literature_sync_log` 6 条）+ `$RT/genome_info.json`（25 条染色体长度/GC）
  + `assembly_registry` + BLAST 库文件大小（cdna 51.9 MB / genome 345.5 MB）。
  预览服务再用与线上**同一套选取规则**（`md5(day) % 候选池`）复刻 `/api/hub/research-feed`，
  顺带实现 `/api/genome/overview` 与 `/api/genome/chromosome`。
  实测：首页 3 篇真实论文（标题/期刊/PMID/三行摘要/两个外链）、基因组页 25 条核型条 + 3 张表 + 31 行数据。

**② “搜索界面怎么那么矮”——是我上一轮引入的回归**
`.shell` 原本是 `grid`（单列）负责把高度撑满；上一轮为了删 `side-rail` 写成 `display:block`，
把纵向拉伸链打断了：`.shell-main` 只剩内容高（484px），聊天面板只剩 **398px**、会话栏 253px，
下方空出一大片。→ 恢复 `grid` 单列并补 `#view-chat > main { align-items: stretch }`，
面板 398 → **563px**（视口 714），会话栏与对话区等高。

**③ 顺手把方块尺寸调匀**
- 功能页阅读宽度收到 1180px，输入卡 340px → **400px**（原来表单内只有 303px，长说明挤成窄条）
- 首页论文摘要 `line-clamp: 3`、标题 clamp 2 行：每卡 307 → **234px**，3 篇完整可见（不再内部滚动）
- 首页右栏（品牌/入口）改 `position: sticky`，避开左栏 1206px 长列表造成的下方大片留白
- 聊天页：空状态 hero 标 84px、标题 19px、输入区 min-height 58px

**④ 踩到的坑（已修）**
- `#view-chat { display:flex !important }` 的 **ID 特异性压过了 `.app-view.hidden { display:none }`**
  → 切到其它页面时聊天页仍然留在屏幕上，和功能页内容叠在一起。
  必须写成 `#view-chat:not(.hidden)`。同类风险：任何给 view/panel 容器设 `display` 的 ID 规则都要带 `:not(.hidden)`。
- `html` 上被旧层挂了 `ocean-scene.svg` 水印 + 双层渐变（`clean-ui.css` / `glass-agent.css`），
  纸张白底上会浮出“鱼骨”水印 → 预览层里把 `html` 背景也压平。
### 19.10 功能页重叠/覆盖：根因 + 加固 + 系统性扫描（2026-10-15 续三）

**症状**：英文模式下"功能页重叠"——截图里聊天页（hero + 输入框 + 会话栏）与
功能页（Gene Workbench 的 INPUT / RESULTS）**同时在屏幕上互相覆盖**。

**根因**：19.9 那轮为了修"聊天界面太矮"，我写了
`#view-chat { display: flex !important; }`。ID 选择器的特异性（1,0,0）**高于**
`.app-view.hidden { display: none !important }`（0,2,0），于是 `showOnly()` 给
`#view-chat` 加上 `hidden` 类也压不住它 → 切到功能页后聊天页仍然显示。**是我引入的回归。**

**两层修法（第二层才是关键）**：
1. CSS：改成 `#view-chat:not(.hidden)`（让 `.hidden` 能生效）。
2. **JS 加固**：`app.js` 的 `showOnly()` 不再只切 class，而是**同时写内联 display**——
   隐藏时 `el.style.setProperty("display", "none", "important")`，显示时
   `el.style.removeProperty("display")`（把 display 交回样式表，聊天页需要 flex）。
   内联 `!important` 的优先级**高于任何作者样式表 `!important`**，所以从这之后，
   不论哪一层 CSS 怎么写，都不可能再出现"两个视图同时在"。
   这一层还顺带免疫了浏览器缓存旧 CSS 的情况。

**系统性扫描（一次把所有同类问题找干净）**：写了三段检测，逐路由跑——
① 多个 `.app-view` 同时可见；② 同一容器内**兄弟块矩形相交**（>6px×6px）；
③ 元素**横向溢出**（`scrollWidth > clientWidth + 3`，英文比中文长，最容易在这里翻车）。
首轮 8 页扫出 3 处英文溢出并修复：

| 元素 | 溢出 | 修法 |
| --- | --- | --- |
| `.chat-hero-mark` | 94 > 84 | 容器 `overflow:hidden` + 内部 `img/svg` 撑满 100% |
| `.prov-head` | 264 > 248 | 标题行 `flex-wrap: wrap` + `min-width: 0` |
| `.src-badge.is-local` | 256 > 247 | 徽章 `max-width:100%` + `overflow-wrap: anywhere` |

复扫（chat / home / genomes / literature / expression）：**0 问题**。
视图互斥实测：home→`block`、chat→`flex`、feature→`block`，其余一律 `none [inline:none]`。

### 19.11 顶栏两处"重叠"（2026-10-15 续四）

**① 「Sign in」与「中 / EN」相交 18px**
逐元素量矩形发现 `#userAccountBtn("Sign in") ∩ #langToggleBtn("中 / EN") = 18px`：
旧层给 `#userChip` 写死了 `width: 160px`，而"访客 · 快速入门 + 登录"比它宽，
内容**溢出容器**顶到右边按钮上。修法：顶栏右侧每个控件 `flex: 0 0 auto`、
chip 不设固定宽度（`width: auto; max-width: 260px`）、名字允许省略号
（`min-width:0 + text-overflow: ellipsis`）——保证"宁省略名字，不挤按钮"。
修复后 EN/ZH 两种语言实测相交均为 **0**，"Sign in" 右缘到语言按钮留 17px。

**② 窄屏下顶栏被压矮，两行溢出压住页面内容（更隐蔽）**
顶栏是 `.app`（`height:100vh` 的 flex 列）的子项，默认 `flex-shrink: 1`。
在内容较高的页面上它会被**压缩**：768px 视口实测顶栏只剩 **27px**，而里面
（logo 行 44px + 工具栏行 38px）需要 ~82px → 工具栏行溢出到页面上，
和正文叠在一起。修法：`header.site-header { flex: 0 0 auto !important }`
（禁止压缩）+ 两行布局的 `min-height` 用 `auto` 而不是 `0`。

**断点矩阵验收**（1440 / 1314 / 1024 / 768 × chat / home / genomes / literature，
共 16 个组合）检查 5 项：多视图同显、兄弟块相交、横向溢出、顶栏压内容、
顶栏控件出屏 → **全部 0 问题**。

### 19.12 深色模式适配 + 文字对比度可达性（2026-10-15 续五）

用户反馈"黑暗模式下不适配"。不用眼睛看，写成**文字对比度审计**：遍历可见文字元素，
逐层合成有效背景色（支持 `rgba()` 与 `color(srgb …)` 两种写法），按 WCAG 算对比度，
低于 AA（正文 4.5 / 大号字 3.0）就列出来。

**深色模式首轮 11 处不达标**，三类根因：

| 类型 | 例子 | 实测 | 修法 |
| --- | --- | --- | --- |
| 令牌过暗 | `--p-ink-3 = .46` | 4.19–4.28 | 深色 `.62`、浅色 `.60`（均 ≥4.9） |
| 硬编码蓝链接 | `a { color: rgb(43,126,196) }` | 4.20 | 深色下锚点改青色强调色 `#7ee0d6`（导航类排除） |
| 硬编码状态色 | `.home-upd-status #64748b`、`#homeAgentStatusText 绿` | **2.94** | 深色下改 `--p-ink-2` / `#58d3a4` |

**顺带修了深色下真正的"看不清"**：核型条上的染色体编号原来一律白字，
压在浅色条（`.is-low #c8d6d4`）上对比度只有 ~1.6，深色模式下条变浅青后更是 **1.13**。
→ 改成按底色深浅选字色（浅底深字 / 深底白字），深色下统一深字。

**浅色模式也一并提升**（审计同样扫出 2.9–4.5 的弱化小字）。踩到的特异性坑：
`glass-agent.css` 有 `.agent-fold > summary.agent-fold-sum .home-upd-status`（**3 个类**）+ `!important`，
比 `html body.theme-atelier .home-upd-status`（2 个类）高，一直抢着生效。
→ 覆盖规则统一升级为 `html body.theme-atelier #appRoot <sel>`（带一个 ID，稳压所有类选择器）。

**最终验收**：浅色 + 深色 × (home / chat / genomes / literature / ops) = **10 个组合全部 0 问题**。

**方法沉淀**：审计脚本必须能解析 `color(srgb r g b / a)`（0–1 值域），
否则会把白色算成近黑、得出"1.04"这种假结论（本轮就踩了一次，浪费一轮）。
改 CSS 后**先升 `?v=` 再测量**，否则浏览器用缓存，会误判为"没生效"。

---

## 20. 端到端功能测试（全接口 + 重复问答 + 并发）（2026-10-15 续六）

用户要求：「测试一下所有功能是否正确能用，多次使用多次回答是否会出问题」。
不做人工点击，写成**可重复执行的脚本**，在服务器上对着 `127.0.0.1:8080` 真跑一遍。

### 20.1 方法

| 文件 | 作用 |
| --- | --- |
| `scripts/e2e_test.py` | 纯标准库，五个阶段：A 接口全扫 / A2 真实数据 / A3 重计算 / B 同问 N 次 / C 多模式 / D 并发 N 路 / E 汇总 |
| `scripts/run_e2e_remote.py` | `start`（SFTP 上传 + `setsid nohup … -u` 后台跑）/ `tail N` / `log` / `wait`（跑完把日志抓回本地）/ `diag`（看谁重启过 uvicorn）/ `api <path>`（直接取某接口返回核对字段）/ `sh "命令"`（在服务器上跑任意命令） |

**只有跑通不算数**：每个用例都带校验器（`j_ok` / `has_keys` / `min_count` / `non_empty_list` / `gene_detail` / `chat_ok`），
断言**返回体结构**而不只是 HTTP 200 —— 否则「200 但内容是空的」会被放过。

### 20.2 结果（2026-10-15 21:5x – 00:1x）

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| A | 接口连通性全扫 **39 个用例** | **39 PASS / 0 FAIL**（首轮 2 个 FAIL 是校验器自身 bug，见 20.3） |
| A2 | 表达-组织 Top（`tissue=spleen`）、多基因对比 | PASS，293ms / 89ms |
| A3 | BLAST 比对、CRISPR 设计、引物设计 | PASS，3.97s / 2.88s / 5.49s |
| B | **同一问题连续问 6 次** | **6/6 PASS，0 报错**；延迟 49.3–70.3s（中位 59.6s），答案 3432–4464 字 —— 无漂移、无退化、无"越问越慢" |
| C | 多模式问答 auto/rag/db/gene/fusion 各 1 次 | **6/6 PASS**；`db` 1335ms，`gene` 17.4s，`fusion` 33.0s，`rag` 61.4s，`auto` 83.5–131.8s |
| D | **并发 3 路同时问** | 1 PASS + 2 × `HTTP 503`（原因见 20.4，**属设计行为**，浏览器端不会出现） |
| E | uvicorn RSS | 30.57 GB → 32.50 GB（+1.93 GB），**全程未被 OOM/守护重启**（uptime 连续） |

最慢的单点：`POST /api/retrieval/debug` **56.8s**（检索调试会跑完整 RAG 链路，属预期但不适合放在常规交互路径）。

### 20.3 首轮 2 个 FAIL 全是校验器写错（不是产品问题）

| 用例 | 报错 | 真相 |
| --- | --- | --- |
| `GET /api/gene/tp53` | 「缺字段 `gene`」 | 接口返回 `basic / expression / go / homology / kegg / references / aliases / paralogs / …`，**没有 `gene` 这个字段** |
| `GET /api/tools/annotate?gene=tp53` | 「`detail: {…}`」 | `detail` 在这里**就是数据本体**（注释结果），不是错误信息 |

第二版又踩了一次：改用 `min_count(["basic"], 1)` 后仍 FAIL —— 因为 **`basic` 是字典（单条基因记录）而不是列表**，
`len()` 拿不到就返回"计数不足（实际 None）"。

**沉淀规律**：写接口测试时先 `run_e2e_remote.py api <path>` **把真实返回体打出来**再写校验器；
`min_count` 已改为 dict/list/str 都算长度；基因详情改用专用校验器 `gene_detail`（检查 `basic.gene_name` 并回显 `chromosome`）。
两条复跑均 PASS（147ms / 3849ms）。

### 20.4 并发 3 路出现 503 的真因：全局单并发锁（**不是**内存、不是限流）

`api/api_server.py`：

```python
_chat_run_lock = threading.Lock()          # agent/agent_scheduler.py：全局唯一
_CHAT_LOCK_WAIT_SEC = float(os.getenv("CHAT_LOCK_WAIT_SEC", "45"))   # 默认等 45s
...
if not _chat_run_lock.acquire(timeout=_CHAT_LOCK_WAIT_SEC):
    raise HTTPException(503, detail=f"上一问仍在处理中（已约 {sec}s），请稍候再发；不是限流。")
```

实测一次回答要 **49–70s**，而等待上限只有 **45s** → 3 路并发时必然有 2 路超时拿 503。
日志佐证：`hub.log` 连续两行 `POST /api/chat 503`，且不伴随任何 `mem_gate / low_mem` 记录。

**为什么不把等待时间调大**：公网走 cloudflared quick tunnel，Cloudflare 对单请求约 **100s** 就返回 524；
等待 90s 再叠上 60s 生成，只会把"明确的中文提示"变成"看不懂的网关超时"，观感更差。
**真正该做的是前端排队，而前端已经做了**：`app.js` 里 `if (isSending || queueProcessing)` 入队（上限 8 条，提示"📥 已加入队列（剩余 N 个）"），
并用 `/api/health` 的 `chat_busy` 轮询驱动 `processQueue()` 自动续跑 ——
**所以浏览器用户正常使用不会看到这个 503**，只有裸调 API（本轮压测就是这么打的）才会。

### 20.5 资源特性（回答"多次使用会不会出问题"）

- uvicorn 常驻 RSS 稳定在 **30–32.5 GB**（4B 嵌入模型 + RAG 索引常驻），
  `/api/health` 报 `mem_limit_mb: 36864 / mem_headroom_mb: 938` —— **余量不到 1 GB**，这是真的紧。
- 因此**单并发锁是必要的保护**（并发跑 2 个回答有 OOM 风险），不是可以随手去掉的瓶颈。
- 长时间高强度使用会看到 `hub_supervise.log` 里的 `hub exited code=137`（SIGKILL 回收），
  守护 10–15s 内自动拉起；重启窗口内不可用。**测试期间未发生**（uptime 连续，RSS 未触顶）。

### 20.6 结论

1. **功能全通**：39 个接口 + 3 类重计算工具 + 6 种问答模式，全部正确返回。
2. **重复问答不会出问题**：连问 6 次 0 报错，延迟与答案长度都在正常波动内。
3. **单用户/单会话可以放心连续用**；**多人同时提问**由前端队列兜住，不会互相干扰。
4. 唯一真实的体验短板是**"一个问题要等 50–70s"**（不是错误，是慢），
   以及 `retrieval/debug` 56.8s；若要提速应从这里入手，而不是改并发上限。

---

## 21. 四项功能复核 + 升级到「摘要 / 全文级」证据（2026-10-17）

用户先要求"测试这几个功能能不能正常显示使用"，测试全通过后又追加两条口径：
**① 别名文献检索要扩到摘要/全文级**、**② 复制体要给出"哪个复制体保留了哪个功能"的结论**。
同时明确 ①「**只在需要时触发**，一般不需要触发，不然显得太死板」。

### 21.1 为什么"全文级"不是加个 SQL LIKE 就行

查清了两件事：

| 事实 | 影响 |
| --- | --- |
| `literature_paper` **没有摘要列**（只有 `title_en/title_zh/year/journal`） | 摘要级无法在线查 |
| `literature_fulltext_gold` 有 32,549 行，但 **`body_text` 全为空（0 行）** | 正文按设计**外置磁盘**，`body_path` 指向 `解析文件/yes_fulltext_gold/batch_*/papers/*.json` |

→ 结论：必须**离线扫一遍金标语料落表**，线上查表才是毫秒级。

### 21.2 实现

| 件 | 说明 |
| --- | --- |
| `sql/gene_alias_doc_migration.sql` | `gene_alias_lit_stat`（精确计数：题名/摘要/全文各命中多少篇 + 去重总数）+ `gene_alias_doc`（明细样本，带命中片段） |
| `scripts/build_alias_doc_index.py` | 离线构建；`--gene/--genes/--rebuild/--limit/--list`；**快筛**（先用裸别名正则预筛，未命中就不解析 JSON） |
| `llm/gene_alias_fulltext.py` | `index_ready` / `alias_fulltext_evidence` / `copy_role_evidence` / `format_fulltext_block` / `format_copy_role_lines`，300s 进程内缓存，**无索引返回 None → 自动降级为题名级** |
| `GET /api/genes/alias-literature?name=` | 新增只读接口，返回 `level=fulltext` 与可引用样本 |
| `llm/gene_alias_evidence.py`、`llm/zebrafish_prompt.py`、`api/api_server.py` | 接入：全文级可用时把口径从"仅题名匹配"改成"题名/摘要/全文"；复制体分支要求写功能分工 |

**匹配规则（关键）**：词边界 `(?<![a-z0-9])别名(?![a-z0-9])`，否则 `p53` 会把 `tp53` 也算进去。

**触发口径**：只在「别名 / 历史名 / 叫什么」这类身份问题触发（复用 `is_alias_question`），
功能、表达、表型类问题**不加任何开销**。

### 21.3 实测

| 项 | 数字 |
| --- | --- |
| 全量构建耗时 | 扫 **31,707 篇**用 **127 秒**（快筛生效） |
| tp53 | 命中 **3,916 篇**；`p53` 题名 28 / 摘要 441 / **全文 3,628**；`tp53` 8 / 132 / 1,025 |
| 已建索引 | tp53（13 别名 / 4,905）、sox9b（11 / 1,221）、sox9a（10 / 612） |
| 任务② 分工证据 | `sox9b` → 1,221 篇、`sox9a` → 612 篇，均带 DOI/标题/片段 |
| 问答延迟 | ① 79.5s、② 81.6s（此前 167s 是冷启动叠加，非稳态） |

### 21.4 本轮修掉的 4 个真 bug

1. **`import bootstrap` 不加载 `.env`** → 必须 `from core.entry import bootstrap_app; bootstrap_app()`，
   否则 MySQL 回退 `root@localhost` 直接 1045；
2. **建表 SQL 切分顺序错**：先 `split(';')` 再判 `startswith('--')`，会把"注释行 + CREATE TABLE"整段当注释丢掉
   → 表现为"建表就绪"却报表不存在。**正确做法：先逐行去注释，再按分号切**；
3. **`execute_write()` 走 `text(sql)`**，别名里的冒号（`wu:fb40d06`）被 SQLAlchemy 当绑定变量 →
   写入一律用 `next(client.get_db())` + `db.execute(text(sql), params)` 参数化；
4. **远程执行通道 120s 超时会吞掉长命令的全部输出**（这解释了此前一堆"命令静默无输出"）→ 改 1800s，
   并把远程输出同时落本地文件（终端偶发被控制字符卡死时还能用 `read_file` 看）。

### 21.5 验收结果（`scripts/test_4features.py`）

新增 8 条断言（全文级 level/scanned/摘要/全文命中/样本片段、分工证据/索引状态/带文献）。

| 任务 | 结果 |
| --- | --- |
| ① 别名（含全文级） | **11/11** |
| ② 复制体（含功能分工证据） | **10/10** |
| ③ sgRNA / 引物 | **7/7** |
| ④ 前端（12.png / 6.png / Genes / Tools） | **14/14** |
| **合计** | **42/42 全部通过 ✅** |

### 21.6 浏览器实测又抓到一个"静默降级"bug（2026-10-17）

前面 42 项全绿，但**在浏览器里真问一遍**才暴露出问题：回答里报的是
「p53 题名命中 **169** 篇 / tp53 32 篇」——这是**旧的题名级**数字，而且 169 是把 `tp53`
误算进 `p53` 的虚高值（真实 28 篇）。

根因：`gene_alias_evidence._resolve_gene()` 优先返回 **`gene_symbol`**（`tp53` → `"p53"`），
而倒排表是按 **`gene_name`**（`"tp53"`）建的 → `WHERE gene_name='p53'` 查不到 →
代码"优雅降级"回题名级。**降级本身是对的，错在静默**：接口一切正常，只有答案里的数字不对。

修法（两处）：
1. `gene_alias_fulltext.canonical_gene_name()`：查索引前先用 `gene_id_of()` + `gene_basic`
   把 symbol/别名归一成 `gene_name`，`index_ready` / `alias_fulltext_evidence` /
   `copy_role_evidence` 三处入口统一调用；
2. `gene_alias_evidence.alias_title_evidence()` 的题名兜底也改成**词边界**匹配
   （`(?<![a-z0-9])p53(?![a-z0-9])`），否则 `p53` 永远把 `tp53` 算进去。

修复后同一问题（浏览器实测）：

> 在本地全文语料（31707 篇）中实际出现的别名及命中篇数：**p53 题名 28、摘要 441、全文 3628（去重 3629）**；
> tp53 题名 8、摘要 132、全文 1025；tumor suppressor p53 摘要 11、全文 143 …
> **本地库未见记录**：brp53、etid22686.5、fb40d06、wu:fb40d06 在本地全文语料中均未出现，命中数为 0。

**教训**：接口级断言只能证明"字段在、结构对"，**证明不了"模型实际拿到的是哪一档证据"**。
凡是"有降级分支"的功能，必须在浏览器里问一遍、看它引用的数字对不对。

### 21.7 运维注意：改后端后的重启方式

`pkill -f 'uvicorn api[.]api_server'` 发的是 **SIGTERM**，浏览器还开着 `/api/hub/events` 长连接时，
uvicorn 会停在 `Waiting for connections to close` 长达数分钟（`/api/health` 返回 000）。
需要立刻生效时用 **`pkill -9`**，`hub_supervise.sh` 会在 10 秒内重新拉起。

### 21.8 文末参考文献："没有表"和"只有 6 条"是三个独立原因（2026-10-17）

用户反馈两点：① 只剩一句「参考文献（系统生成）：PMID 15292172 等。」；② 列表只有 6 条，
达不到 20~25 的标准。查下来是**三处叠加**：

| # | 原因 | 表现 |
| --- | --- | --- |
| 1 | `hub_orchestrator` 末尾统一脚注的门禁是 `if isinstance(ctx,str) and len(ctx.strip()) > 40:` | 「基因详情」类任务没有 RAG 上下文 → **整段跳过**，模型自己写的占位行留在正文 |
| 2 | 条数受**可用证据**限制（`_ref_footer_min` 虽为 20，补不足就补不足） | RRS 相关性地板筛完只剩几条 → **只有 6 条** |
| 3 | `task=基因详情` 走 `answer_from_gene_detail()` | 这条路径**本身不生成**文末表 |

**修法**（把"全文别名倒排表"变成参考文献的第三来源）：

| 件 | 作用 |
| --- | --- |
| `gene_alias_fulltext.alias_reference_items(gene, limit)` | 从 `gene_alias_doc` + `literature_paper` 取题名/期刊/年/DOI，先"题名命中"、再按命中数与年份排序 |
| `zebrafish_prompt._alias_reference_tuples(question)` | 仅对别名/复制体问题生效，转成证据池同款 7 元组；其他问题返回 `[]`，**原逻辑完全不变** |
| `ensure_reference_footer` | ① **无 papers 时**直接用别名索引出整张表（并清掉模型占位行）；② **有 papers 但不足 min_n 时**用别名索引续补 |
| `hub_orchestrator` 门禁 | 放宽为 `_has_ctx or _alias_q` |

**实测**：确定性单测（构造带占位行的答案）→ 输出 **25 条**、占位行消失；
浏览器真问「tp53 都有哪些别名和历史名？」→ 最新一条回答 **20 条**、无占位行。

**测量坑（值得记住）**：统计 `[n]` 时**不能整页数**——历史消息会污染结果（本轮先误得 11 条）；
必须只看**最后一条 `.msg.bot`**。另外服务刚重启时发送按钮是灰的（模型预热中），
此时提问只会得到一条系统提示，不会真的发出。





