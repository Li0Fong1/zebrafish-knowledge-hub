# 标识符统一映射到 GRCz12tu

把本机与服务器上分散的基因标识符（Ensembl / ZFIN / NCBI Gene / UniProt / 别名 / 老版本坐标）
统一收敛到最新参考基因组 **GRCz12tu（GCF_049306965.2）**，去重后得到非冗余功能基因集合。

产出两张表：

| 表 | 内容 |
|---|---|
| `gene_master_grcz12tu` | 每个 GRCz12tu 基因一行，聚合全部本地来源标识符与功能证据 |
| `gene_id_map_grcz12tu` | 每个本地源基因一条映射记录（源 → 目标 + 方法 + 分数），可追溯 |

## 映射分五层，按可信度递减

| 层 | 方法 | 依据 | 命中数 |
|---|---|---|---|
| A | `id_direct` | NCBI Gene ID 直接对上 `grcz12tu_gene` | 22,539 |
| B | `assembly_xref` | 库内既有的 GRCz12tu 装配坐标 | 10 |
| C | `synteny` | Ensembl GRCz11 坐标经 UCSC chain 投影后与目标基因区间重叠 | 4,397 |
| C′ | `blast_100` / `blast` | cDNA 序列 blastn 到 GRCz12tu CDS 库 | 237 / 542 |
| D | `legacy_synteny` | Zv8/Zv9/GRCz10 老坐标多跳投影 | 88 |
| E | `symbol_match` | 标准符号 / 别名精确匹配 | 471 |

同一个源基因命中多种方法时取可信度最高的一条。

## 运行顺序

前置：服务器上需有
`zebrafish_runtime/liftover/danRer11ToGCF_049306965.2.over.chain.gz`、
`zebrafish_runtime/blast/grcz12tu_cds.fna`、`data/ensembl/Danio_rerio.GRCz11.116.gtf.gz`。

```bash
PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
BLAST=/data/zoujin/deng/zebrafish_runtime/conda_blast/bin

# 0) 建 BLAST 库（只需一次，约 10 秒）
$BLAST/makeblastdb -in /data/zoujin/deng/zebrafish_runtime/blast/grcz12tu_cds.fna \
  -dbtype nucl -parse_seqids -title grcz12tu_cds \
  -out /data/zoujin/deng/zebrafish_runtime/blast/grcz12tu_cds_db

$PY 01_extract_coords.py      # 两端基因坐标 → gene_map/*.tsv
$PY 02_synteny.py             # 共线性投影（chain 载入约 40s，之后 32k 基因 5s）
$PY 03_merge.py               # 合并 A/B/C 层，列出待 BLAST 清单
$PY 04_blast.py extract       # 从 Ensembl cDNA 取 query 序列
$PY 04_blast.py blast         # blastn（约 100s / 32 线程）
$PY 05_supplement.py          # D/E 层补漏
$PY 06_final.py               # 汇总 → 主表 + 证据表 + unmapped
$PY 07_import.py              # 写入 MySQL
```

中间产物都在 `/data/zoujin/deng/zebrafish_runtime/gene_map/`。

## 当前结果

- 本地 `gene_basic` 32,564 个基因 → **28,284 个（86.9%）** 成功落到 GRCz12tu
- 非冗余功能基因 **24,390** 个（蛋白编码 23,991 + lncRNA 299 + miRNA 65 + 其他 35）
- 2,441 个 GRCz12tu 基因由多个本地基因汇聚（多对一已合并）

## 功能基因口径

排除结构 RNA 与假基因（tRNA / rRNA / snRNA / snoRNA / SRP_RNA / pseudogene 等），
其余要求至少一类功能证据（GO 注释、KEGG 通路、或基因功能描述）。
口径写在 `06_final.py` 的 `NON_FUNCTIONAL` 与 `is_functional` 判定处，调整后重跑 06/07 即可。

## 未映射的 4,280 个

绝大多数是 ZFIN 早期的 EST / 克隆命名条目（`wu:`、`im:`、`sb:`、`id:`、`si:` 前缀），
既没有 Ensembl 基因模型、也没有 GRCz11 之后的坐标，在老版本注释里也已不再维护。
它们保留了记录在 `gene_map/unmapped.tsv`，不进入功能基因主表。

## 已知限制

- `02_synteny.py` 的重叠判定是「区间交集最大值」，同一位点若有多个重叠基因
  （反义转录本、嵌套基因），会取交集最大的那个；同链候选有 +1bp 加权但不足以覆盖全部边界情况。
- BLAST 阈值：`pident ≥ 99.5%` 记为 `blast_100`，其余命中按 bitscore 取最优，
  未设 coverage 下限 —— 短外显子可能命中同家族成员，这类记录分数较低，可按 `best_method` 过滤。
- GRCz12tu 是 NCBI RefSeq 注释，大量基因只有 `LOC*` 编号而无可读符号；
  符号匹配（E 层）因此天然受限。
