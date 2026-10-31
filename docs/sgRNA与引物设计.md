# sgRNA 与引物设计（本地引擎）

> 入口：顶部导航 **Tools ▾ → sgRNA / 引物设计**，或直接访问 `#/crispr`
> 后端：`database/crispr_toolkit.py` · 接口：`POST /api/tools/crispr/design`、`POST /api/tools/primer/design`

## 1. 为什么是「本地引擎 + 一键跳转」

用户要求对标 [CHOPCHOP](https://chopchop.cbu.uib.no/) 与 [CRISPRscan](https://www.crisprscan.org/?page=sequence) 的长处。我们的做法是：

- **把两者的核心能力本地化**：双链 PAM 枚举、规则打分、转录组特异性筛查、T7/SP6 体外转录模板；
- **不复制任何外部工具的源码或权重表**：所有打分项都在本仓库里逐项可读、可调参，界面与文档都写明口径；
- **权威活性预测交给原站**：页面上提供 CHOPCHOP / CRISPRscan / Primer3web 的跳转，点击时会先把当前序列写入剪贴板，到对方站点直接粘贴即可（避免对方参数不兼容）。

> 这一取舍是明确的设计决定，不是"功能没做完"：活性预测模型（Doench / Moreno-Mateos 等）依赖各自训练集与权重，本地复刻无法保证与发表模型一致；把"可解释的规则打分 + 可复核的对外跳转"组合起来更不容易误导实验。

## 2. sgRNA 设计

输入：**基因名**（如 `sox9b`，自动取 cDNA，GRCz12tu CDS 优先）或**粘贴 DNA 序列**（建议 ≥50 bp）。

### 2.1 PAM 表

| PAM 类型 | 酶 | spacer 长度 | PAM 方位 | 说明 |
|---|---|---|---|---|
| NGG | SpCas9 | 20 nt | 3′ | 标准 PAM，敲除首选 |
| NAG | SpCas9 | 20 nt | 3′ | 非经典 PAM，效率较低，备选 |
| TTTV | Cas12a (Cpf1) | 23 nt | 5′ | 适合 T7 体外转录、多靶点 |
| NNGRRT | SaCas9 | 21 nt | 3′ | 小尺寸 Cas9，便于 AAV 递送 |

正负链都会枚举；正链坐标 1-based，负链换算回正链坐标。

### 2.2 打分（0～100，逐项返回在 `score_parts`）

| 分项 | 权重 | 规则 |
|---|---|---|
| `gc` | 0.30 | GC 40%～60% 满分；<40% 或 >60% 线性衰减 |
| `polyT` | 0.18 | spacer 内出现 `TTTT` 会被 U6 提前终止，仅 0.15 分 |
| `position` | 0.22 | 落在 CDS 前 1/3 且越靠 5′ 越高（利于移码敲除） |
| `homopolymer` | 0.10 | ≥5 连续同碱基扣分 |
| `hairpin` | 0.10 | spacer 内出现 ≥4 nt 反向互补片段，降为 0.35 分 |
| `seed_gc` | 0.10 | PAM 近端 8 nt 的 GC 在 30%～70% 之外降分 |

再按 PAM 类型乘系数（NGG 1.0 / NAG 0.9 / TTTV 0.95 / NNGRRT 0.9）。
有脱靶筛查时，**综合分 = 0.72 × 效率分 + 28 × 特异性分**。

### 2.3 特异性（脱靶）筛查

界面上有**脱靶范围下拉框**，两种口径：

| 范围 | 参考库 | 速度 | 说明 |
|---|---|---|---|
| **转录组**（默认） | `$RT/blast/cdna_db`（GRCz12tu CDS，53 MB） | 实测 6.2 s（156 候选） | 命中能给出**基因名**（sox8b / sox9a 这类同族基因一目了然） |
| **全基因组** | `$RT/blast/genome_db`（GRCz12tu，库 345 MB；FASTA 1.4 GB / 25 条染色体） | 实测 **6.8 s**（只扫前 12 个候选） | 覆盖内含子/基因间区，命中给出**染色体 + 起止坐标 + 错配数**（如 `NC_133187.1:1310178-1310156 (mm1)`） |

共同规则：

- 用本地 **BLAST+**（`blastn-short`，一次批量提交全部候选，因此 20 个候选只需一次 blastn）；
- 只有 **近全长比对**（比对长度 ≥ 查询长度 − 2）且错配 ≤ 3（引物 ≤ 2）才算近同源，避免 12 nt 子串误计；
- 特异性分 = `1 / (1 + 0.6 × 近同源命中数)`；未扫到（基因组模式下排后 的候选）**不给默认满分**，标为未测；
- BLAST 不可用或基因组库未建时返回 `offtarget_skipped: <原因>`，**不伪造脱靶数字**。

**全基因组库现状与来源**（已完成并实测）：

- 基因组 FASTA：NCBI `GCF_049306965.2_GRCz12tu_genomic.fna.gz`，**压缩 435,810,822 B（≈415 MB）→ 解压 1.47 GB（25 条染色体）**；
  由 `$RT/blast/dl_genome2.sh` 8 路分段并行下载（带断点续传、`--speed-limit` 慢速自动重连、**gzip 校验后才解压**），
  实测 43 s 下完（带宽 10 MB/s 时），随后 `build_genome_db.sh` 用 `makeblastdb -dbtype nucl -out genome_db` 建库（约 40 s，库 345 MB）；
- **为什么要这么费劲**：该服务器国际出口在差时段只有 20～80 KB/s/连接（8 路并行也只 75–238 KB/s），
  而真实文件大小一度被误估成 1.09 GB（实际 415 MB），导致第一版脚本“提前完成”且合并出错；现在的脚本会先验证
  `Content-Length`、合并后比对总字节、`gzip -t` 通过才解压；
- 国内镜像不可用已逐一实测：清华/USTC/南大/上海交大/BFSU 均无 Ensembl/UCSC 基因组镜像；
  CNGB `ftp.cngb.org/pub/ncbi/genomes/` 只镜像部分 accession（`/049/` 是 404）；
- **界面会先探测**：`GET /api/tools/crispr/capabilities` 返回两个库是否就绪；未就绪时「全基因组」选项**置灰**，
  且后端会返回 `offtarget_skipped: …未就绪…`，不伪造数字。实测：库未就绪时接口仍 200、候选正常返回（0.08 s）。

### 2.4 T7 / SP6 体外转录模板

勾选后每个候选额外给出：

- `t7_template = TAATACGACTCACTATAG + [G] + spacer`（spacer 若不以 G 开头会自动补 1 个 G，这是 T7 高转录效率的常用做法）；
- `sp6_template` 同理；
- 界面提供一键复制。

## 3. 引物设计

输入同样支持基因名或模板序列（≥120 bp）。

- 候选长度 18～25 nt，且每个 5′/3′ 起点只保留 **Tm 最接近 60 ℃** 的那个长度；
- 过滤：GC 40%～70%、无 ≥5 连续同碱基、3′ 端不是 `AAA/TTT/CCC/GGG`、Tm 落在 50～68 ℃；
- **Tm 用 SantaLucia 1998 最近邻热力学**估算（ΔH/ΔS 参数表 + 末端 A/T 惩罚 + 50 mM Na⁺ 盐校正 + 0.5 µM 引物浓度），比旧的 GC 校正公式准得多；
- 配对条件：产物长度落在用户给定区间（默认 80～300 bp）、`|ΔTm| ≤ 3 ℃`；
- 打分：`100 − 6×|ΔTm| − 二聚体(6) − 发夹(4) − 产物长度偏离(≤6) − Tm 偏离 60 ℃(≤6)`；
- 风险标记：3′ 端互补（二聚体）、引物内 ≥4 nt 反向互补（发夹）；
- 特异性：可选，对前 8 对引物做一次批量 BLAST（近全长 ≤2 错配），并列出命中的**基因名**；
- 可选加 **T7 启动子尾**（`TAATACGACTCACTATAG`）用于体外转录模板扩增。

## 4. 已知边界（写清楚，避免误用）

- **全基因组脱靶已可用，但默认仍是转录组**：选“全基因组”时只扫前 12 个候选（1.4 GB 库的 blastn-short
  明显慢于转录组），如果需要全量扫描，建议先看转录组结果缩小候选再单选验证。
- 基因组库为 **GRCz12tu（GCF_049306965.2）**；本地基因注释/坐标仍为 GRCz11 r116，
  引用坐标时请注意两者不是同一版组装（界面会标明命中的染色体 accesscion）。
- 没有实现 Doench/CRISPRscan 的机器学习活性预测（见 §1 的取舍），**权威评分请以原站为准**。
- 引物二聚体/发夹是简化检测（4～5 nt 级别的互补/反向互补），不是 Primer3 的完整热力学二聚体搜索；定量 PCR 请按仪器要求收窄条件。
- 打分参数可通过环境变量/代码常量调整；若做过实测校准，请把结论补写进本节。

## 5. 相关文件与接口

| 位置 | 作用 |
|---|---|
| `database/crispr_toolkit.py` | 引擎主体（PAM 枚举 / 打分 / 脱靶筛查 / 引物设计 / `genome_db_status`） |
| `$RT/blast/dl_genome2.sh` | 基因组分段并行下载（断点续传 + 慢速重连 + gzip 校验） |
| `$RT/blast/build_genome_db.sh` | `makeblastdb` 建全基因组库 |
| `$RT/blast/cdna_db` / `$RT/blast/genome_db` | 转录组库（53 MB） / 全基因组库（345 MB） |
| `GET /api/tools/crispr/capabilities` | 两个库的就绪状态与大小（前端据此置灰选项） |
| `POST /api/tools/crispr/design` | 参数：`target, enzyme, top_n, max_mismatch, off_target, add_t7, offtarget_scope` |
| `POST /api/tools/primer/design` | 参数：`template, amplicon_min, amplicon_max, max_pairs, add_t7, off_target, off-target_scope` |
