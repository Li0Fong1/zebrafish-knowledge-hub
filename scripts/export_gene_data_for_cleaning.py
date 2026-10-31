# -*- coding: utf-8 -*-
"""
导出斑马鱼基因 MySQL 数据为 CSV + 数据目录，供外部清洗使用。

用法：
  python scripts/export_gene_data_for_cleaning.py
  python -m scripts.export_gene_data_for_cleaning
  python -m scripts.export_gene_data_for_cleaning --out 项目阶段/05_数据与库/归档/data_export/my_batch
  python -m scripts.export_gene_data_for_cleaning --queue-only
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

import argparse
import csv
import json
import os
import shutil
from datetime import datetime

from common_utils import safe_print
from config import (
    ENSEMBL_RELEASE,
    NCBI_ASSEMBLY_ACCESSION,
    NCBI_REFERENCE_ASSEMBLY,
    REFERENCE_ASSEMBLY,
    TAXON_ID,
    _BASE_DIR,
)
from mysql_client import MySQLClient

TABLES = [
    ("gene_basic", "SELECT * FROM gene_basic ORDER BY gene_id"),
    (
        "gene_expression",
        "SELECT ge.id, ge.gene_id, gb.gene_name, gb.ensembl_id, ge.tissue, "
        "ge.expression_stage, ge.expression_level, ge.data_source "
        "FROM gene_expression ge JOIN gene_basic gb ON ge.gene_id = gb.gene_id "
        "ORDER BY ge.gene_id, ge.id",
    ),
    (
        "gene_go",
        "SELECT go.id, go.gene_id, gb.gene_name, gb.ensembl_id, go.go_term, go.go_category "
        "FROM gene_go go JOIN gene_basic gb ON go.gene_id = gb.gene_id "
        "ORDER BY go.gene_id, go.id",
    ),
    (
        "homology_gene",
        "SELECT h.id, h.gene_id, gb.gene_name, gb.ensembl_id, h.species, "
        "h.homo_gene_name, h.homology_score "
        "FROM homology_gene h JOIN gene_basic gb ON h.gene_id = gb.gene_id "
        "ORDER BY h.gene_id, h.id",
    ),
    (
        "gene_reference",
        "SELECT r.id, r.gene_id, gb.gene_name, gb.ensembl_id, r.pubmed_id, "
        "r.title, r.journal, r.publish_year "
        "FROM gene_reference r JOIN gene_basic gb ON r.gene_id = gb.gene_id "
        "ORDER BY r.gene_id, r.id",
    ),
    (
        "gene_alias",
        "SELECT ga.id, ga.gene_id, gb.gene_name, ga.alias, ga.alias_type, ga.source, ga.is_primary "
        "FROM gene_alias ga JOIN gene_basic gb ON ga.gene_id = gb.gene_id "
        "ORDER BY ga.gene_id, ga.id",
    ),
    (
        "gene_xref",
        "SELECT gx.id, gx.gene_id, gb.gene_name, gx.db_name, gx.db_id, gx.db_label, gx.source "
        "FROM gene_xref gx JOIN gene_basic gb ON gx.gene_id = gb.gene_id "
        "ORDER BY gx.gene_id, gx.id",
    ),
    (
        "gene_assembly_xref",
        "SELECT ax.id, ax.gene_id, gb.gene_name, gb.ensembl_id, ax.assembly_name, "
        "ax.gene_symbol, ax.ncbi_gene_id, ax.refseq_mrna, ax.chromosome, ax.start_pos, ax.end_pos "
        "FROM gene_assembly_xref ax JOIN gene_basic gb ON ax.gene_id = gb.gene_id "
        "ORDER BY ax.gene_id, ax.id",
    ),
]

# 人工清洗优先队列（过夜流水线结束后重点处理）
CLEANING_QUEUES = [
    (
        "queue_no_go",
        """
        SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, gb.chromosome,
          LEFT(gb.gene_function, 800) AS gene_function,
          (SELECT COUNT(*) FROM homology_gene h WHERE h.gene_id = gb.gene_id) AS homology_count,
          (SELECT COUNT(*) FROM gene_reference r WHERE r.gene_id = gb.gene_id) AS reference_count,
          (SELECT GROUP_CONCAT(CONCAT(db_name, ':', db_id) SEPARATOR '; ')
             FROM gene_xref gx WHERE gx.gene_id = gb.gene_id LIMIT 8) AS xrefs,
          'P1_缺少GO' AS priority, '补充 GO 术语（GO:xxxx 描述）' AS cleaning_task
        FROM gene_basic gb
        WHERE gb.ensembl_id IS NOT NULL
          AND (gb.gene_function LIKE '%protein_coding%' OR gb.expression_summary LIKE '%protein_coding%')
          AND NOT EXISTS (SELECT 1 FROM gene_go go WHERE go.gene_id = gb.gene_id)
        ORDER BY homology_count DESC, gb.gene_id
        """,
    ),
    (
        "queue_weak_function",
        """
        SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, gb.chromosome,
          gb.gene_function,
          (SELECT COUNT(*) FROM gene_go go WHERE go.gene_id = gb.gene_id) AS go_count,
          'P2_功能描述占位' AS priority,
          '将 protein_coding·ensembl 占位替换为真实功能摘要（中文或英文均可）' AS cleaning_task
        FROM gene_basic gb
        WHERE gb.gene_function REGEXP 'protein_coding|lncRNA|miRNA|pseudogene'
          AND (gb.gene_function LIKE '%ensembl%' OR gb.gene_function LIKE '% · %')
          AND CHAR_LENGTH(IFNULL(gb.gene_function, '')) < 120
        ORDER BY go_count DESC, gb.gene_id
        LIMIT 8000
        """,
    ),
    (
        "queue_no_zfin",
        """
        SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id, gb.chromosome,
          LEFT(gb.gene_function, 300) AS gene_function,
          'P2_缺ZFIN' AS priority,
          '补充 zfin_id 或 gene_xref 中 ZDB-GENE-*' AS cleaning_task
        FROM gene_basic gb
        WHERE gb.ensembl_id IS NOT NULL
          AND (gb.zfin_id IS NULL OR gb.zfin_id = '')
          AND NOT EXISTS (
            SELECT 1 FROM gene_xref gx
            WHERE gx.gene_id = gb.gene_id
              AND (gx.db_name LIKE '%zfin%' OR gx.db_id LIKE 'ZDB-GENE-%')
          )
        ORDER BY gb.gene_id
        LIMIT 5000
        """,
    ),
    (
        "queue_no_literature",
        """
        SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id, gb.zfin_id,
          (SELECT COUNT(*) FROM gene_go go WHERE go.gene_id = gb.gene_id) AS go_count,
          'P3_缺文献' AS priority,
          '补充代表性 PubMed（pubmed_id + title + journal + year）' AS cleaning_task
        FROM gene_basic gb
        WHERE gb.ensembl_id IS NOT NULL
          AND (gb.gene_function LIKE '%protein_coding%' OR gb.expression_summary LIKE '%protein_coding%')
          AND NOT EXISTS (SELECT 1 FROM gene_reference r WHERE r.gene_id = gb.gene_id)
        ORDER BY go_count DESC, gb.gene_id
        LIMIT 3000
        """,
    ),
    (
        "queue_naming_conflict",
        """
        SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.ensembl_id,
          (SELECT COUNT(*) FROM gene_alias ga WHERE ga.gene_id = gb.gene_id) AS alias_count,
          (SELECT GROUP_CONCAT(ga.alias SEPARATOR ' | ')
             FROM gene_alias ga WHERE ga.gene_id = gb.gene_id LIMIT 12) AS aliases,
          'P2_命名复核' AS priority,
          '核对 gene_name / gene_symbol / alias 是否一致，合并口语名' AS cleaning_task
        FROM gene_basic gb
        WHERE gb.gene_symbol IS NOT NULL AND gb.gene_symbol <> ''
          AND LOWER(gb.gene_symbol) <> LOWER(gb.gene_name)
        ORDER BY alias_count DESC, gb.gene_id
        LIMIT 2000
        """,
    ),
]

MASTER_SQL = """
SELECT
  gb.gene_id,
  gb.gene_name,
  gb.gene_symbol,
  gb.ensembl_id,
  gb.zfin_id,
  gb.chromosome,
  LEFT(gb.gene_function, 500) AS gene_function,
  gb.expression_summary,
  (SELECT COUNT(*) FROM gene_alias ga WHERE ga.gene_id = gb.gene_id) AS alias_count,
  (SELECT COUNT(*) FROM gene_xref gx WHERE gx.gene_id = gb.gene_id) AS xref_count,
  (SELECT COUNT(*) FROM gene_expression ge WHERE ge.gene_id = gb.gene_id) AS expression_count,
  (SELECT COUNT(*) FROM gene_go go WHERE go.gene_id = gb.gene_id) AS go_count,
  (SELECT COUNT(*) FROM homology_gene h WHERE h.gene_id = gb.gene_id) AS homology_count,
  (SELECT COUNT(*) FROM gene_reference r WHERE r.gene_id = gb.gene_id) AS reference_count
FROM gene_basic gb
ORDER BY gb.gene_id
"""

SAMPLE_SIZE = 200


def _client():
    return MySQLClient()


def _export_csv(sql: str, path: str, max_rows: int = 0) -> int:
    client = _client()
    res = client.execute_sql(sql, max_rows=max_rows if max_rows else 10_000_000)
    if not res.get("success"):
        raise RuntimeError(res.get("error") or "query failed")
    rows = res.get("data") or []
    if not rows:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("")
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def _table_exists(name: str) -> bool:
    r = _client().execute_sql(f"SHOW TABLES LIKE '{name}'")
    return bool(r.get("data"))


def _write_catalog(out_dir: str, stats: dict):
    catalog_path = os.path.join(out_dir, "00_数据目录.md")
    lines = [
        "# 斑马鱼基因数据导出包（供清洗）",
        "",
        f"- **导出时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **参考基因组**：{REFERENCE_ASSEMBLY}（{NCBI_ASSEMBLY_ACCESSION}）",
        f"- **NCBI 现行参考**：{NCBI_REFERENCE_ASSEMBLY}",
        f"- **Ensembl release**：{ENSEMBL_RELEASE}",
        f"- **NCBI taxon**：{TAXON_ID}（Danio rerio）",
        f"- **数据库名**：zebrafish_db",
        "",
        "## 1. 目录结构",
        "",
        "```",
        "00_数据目录.md          ← 本文件（说明与清洗要点）",
        "01_schema/              ← 表结构说明",
        "02_tables/              ← 各表 CSV（主数据）",
        "03_master/              ← 基因主表（每基因一行汇总）",
        "04_samples/             ← 各表前 200 行样例（快速预览）",
        "05_stats/               ← 行数统计与质量提示",
        "06_cleaning_queue/      ← ★ 人工清洗优先队列（先处理这里）",
        "```",
        "",
        "## 2. 表说明与行数",
        "",
        "| 文件 | 说明 | 行数 | 主键/关联 |",
        "|------|------|------|-----------|",
    ]
    desc = {
        "gene_basic": "基因主表：官方符号、Ensembl/ZFIN、染色体、功能描述",
        "gene_expression": "表达量：组织/发育时期/TPM或Score；含 Bgee 与 Atlas 胚胎数据",
        "gene_go": "GO 功能注释（BP/MF/CC）",
        "homology_gene": "跨物种同源基因及相似度",
        "gene_reference": "基因关联 PubMed 文献（当前较少）",
        "gene_alias": "别名/历史符号/口语名（如 p53→tp53）",
        "gene_xref": "跨库 ID（NCBI、ZFIN ZDB-GENE、RefSeq 等）",
        "ensembl_id_version": "Ensembl ID 版本映射",
        "gene_assembly_xref": "GRCz11/GRCz12tu 组装版本 ID 映射",
        "gene_master": "汇总表：每基因一行 + 各子表计数",
        "queue_no_go": "★ P1：蛋白编码但无 GO（需人工补注释）",
        "queue_weak_function": "P2：功能描述仍为 Ensembl 占位文本",
        "queue_no_zfin": "P2：缺 ZFIN / ZDB-GENE",
        "queue_no_literature": "P3：缺 PubMed 关联（经典基因优先）",
        "queue_naming_conflict": "P2：gene_name 与 gene_symbol 不一致",
    }
    for name, cnt in stats.items():
        loc = "06_cleaning_queue" if name.startswith("queue_") else "02_tables"
        if name == "gene_master":
            loc = "03_master"
        lines.append(
            f"| `{name}.csv` | {desc.get(name, name)} | {cnt:,} | {loc} |"
        )

    lines.extend([
        "",
        "## 3. 人工清洗工作流（推荐顺序）",
        "",
        "1. 打开 **`06_cleaning_queue/queue_no_go.csv`** — 无 GO 基因（过夜 enrich 后仍缺注释）",
        "2. **`queue_weak_function.csv`** — 把占位描述改成真实功能",
        "3. **`queue_no_zfin.csv` / `queue_naming_conflict.csv`** — ID 与命名统一",
        "4. **`queue_no_literature.csv`** — 补充关键基因文献（可选）",
        "5. 全量表在 `02_tables/`，可按需修改后整表交回",
        "",
        "清洗完成后放入 `项目阶段/05_数据与库/归档/data_export/cleaned_in/`，运行菜单 **14** 或：",
        "`python -m scripts.import_cleaned_gene_data --dir 项目阶段/05_数据与库/归档/data_export/cleaned_in`",
        "",
        "## 4. 字段要点（gene_basic）",
        "",
        "| 字段 | 含义 | 清洗建议 |",
        "|------|------|----------|",
        "| gene_name | 官方基因符号（主键语义） | 统一小写；合并重复记录 |",
        "| gene_symbol | 常用别名 | 可与 gene_name 合并或写入 alias |",
        "| ensembl_id | Ensembl 基因 ID | 锚定 GRCz11 r116；去重 |",
        "| zfin_id | ZFIN 符号或 ID | 补充 ZDB-GENE-* 到 xref |",
        "| chromosome | 染色体 | 1-25 或 MT；统一格式 |",
        "| gene_function | 功能描述 | 多为 Ensembl biotype 摘要，需深度注释 |",
        "",
        "## 5. 已知数据质量问题（请优先处理）",
        "",
        "1. **gene_function** 大量仅为 `protein_coding · ensembl_havana`，缺乏生物学功能文本",
        "2. **gene_reference** 绝大多数基因无 PMID 关联（需 curated 文献）",
        "3. **homology_gene** 覆盖不全，部分基因为空",
        "4. **gene_alias / gene_xref** 已批量构建，但需核对 ZFIN ZDB-GENE 与 Ensembl 一致性",
        "5. **gene_expression** 存在两来源：`bgee`（组织）与 `atlas_embryo`（胚胎 TPM），清洗时保留 `data_source`",
        "6. 口语别名（p53、shh）在 alias 表，官方符号在 gene_name（tp53、shha）",
        "",
        "## 6. 清洗交付建议格式",
        "",
        "- 返回 **UTF-8 CSV**（与导出一致），文件名保持不变或附 `_cleaned` 后缀",
        "- 不要删除 `gene_id` 列；新增/修改行请说明规则",
        "- 功能描述、GO、文献、同源可分开提交",
        "- 附简短 **清洗日志**（改了什么、多少条）",
        "",
        "## 7. 联系方式",
        "",
        "（请填写你的姓名/邮箱/微信）",
        "",
    ])
    with open(catalog_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _write_schema(out_dir: str):
    schema_dir = os.path.join(out_dir, "01_schema")
    os.makedirs(schema_dir, exist_ok=True)
    src_sql = os.path.join(_BASE_DIR, "sql", "zebrafish_db_init.sql")
    if os.path.isfile(src_sql):
        shutil.copy2(src_sql, os.path.join(schema_dir, "zebrafish_db_init.sql"))

    meta = {
        "reference_assembly": REFERENCE_ASSEMBLY,
        "ncbi_assembly": NCBI_ASSEMBLY_ACCESSION,
        "ensembl_release": ENSEMBL_RELEASE,
        "taxon_id": TAXON_ID,
        "tables": {
            "gene_basic": ["gene_id", "gene_name", "gene_symbol", "ensembl_id", "zfin_id",
                           "chromosome", "gene_function", "interpro_domain", "uniprot_id", "expression_summary"],
            "gene_expression": ["id", "gene_id", "gene_name", "ensembl_id", "tissue",
                                "expression_stage", "expression_level", "data_source"],
            "gene_go": ["id", "gene_id", "gene_name", "ensembl_id", "go_term", "go_category"],
            "homology_gene": ["id", "gene_id", "gene_name", "ensembl_id", "species",
                              "homo_gene_name", "homology_score"],
            "gene_reference": ["id", "gene_id", "gene_name", "ensembl_id", "pubmed_id",
                               "title", "journal", "publish_year"],
            "gene_alias": ["id", "gene_id", "gene_name", "alias", "alias_type", "source", "is_primary"],
            "gene_xref": ["id", "gene_id", "gene_name", "db_name", "db_id", "db_label", "source"],
            "ensembl_id_version": ["id", "gene_id", "gene_name", "ensembl_id", "id_base",
                                   "version_num", "ensembl_release", "is_current", "source"],
        },
    }
    with open(os.path.join(schema_dir, "tables.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _write_quality_notes(out_dir: str, stats: dict):
    notes = os.path.join(out_dir, "05_stats", "data_quality_notes.txt")
    os.makedirs(os.path.dirname(notes), exist_ok=True)
    with open(notes, "w", encoding="utf-8") as f:
        f.write("数据质量快照\n")
        f.write("=" * 40 + "\n")
        for k, v in stats.items():
            f.write(f"{k}: {v}\n")
        f.write("\nexpression 按来源:\n")
        r = _client().execute_sql(
            "SELECT data_source, COUNT(*) AS cnt FROM gene_expression GROUP BY data_source ORDER BY cnt DESC"
        )
        for row in r.get("data") or []:
            f.write(f"  {row.get('data_source')}: {row.get('cnt')}\n")
        f.write("\n无 GO 注释的基因数:\n")
        r2 = _client().execute_sql(
            "SELECT COUNT(*) AS cnt FROM gene_basic gb WHERE NOT EXISTS "
            "(SELECT 1 FROM gene_go go WHERE go.gene_id = gb.gene_id)"
        )
        f.write(f"  {((r2.get('data') or [{}])[0].get('cnt', 0))}\n")
        f.write("\n无表达数据的基因数:\n")
        r3 = _client().execute_sql(
            "SELECT COUNT(*) AS cnt FROM gene_basic gb WHERE NOT EXISTS "
            "(SELECT 1 FROM gene_expression ge WHERE ge.gene_id = gb.gene_id)"
        )
        f.write(f"  {((r3.get('data') or [{}])[0].get('cnt', 0))}\n")


def _export_cleaning_queues(out_dir: str, stats: dict) -> None:
    queue_dir = os.path.join(out_dir, "06_cleaning_queue")
    os.makedirs(queue_dir, exist_ok=True)
    safe_print("  导出人工清洗队列 …")
    for name, sql in CLEANING_QUEUES:
        path = os.path.join(queue_dir, f"{name}.csv")
        cnt = _export_csv(sql, path, max_rows=10_000_000)
        stats[name] = cnt
        safe_print(f"    {name}: {cnt:,} 行")

    readme = os.path.join(queue_dir, "README_清洗说明.txt")
    with open(readme, "w", encoding="utf-8") as f:
        f.write("人工清洗队列说明\n")
        f.write("=" * 40 + "\n\n")
        f.write("请优先处理 queue_no_go.csv（无 GO 注释）\n")
        f.write("每行含 priority 与 cleaning_task 字段说明任务\n\n")
        f.write("交付：UTF-8 CSV，保留 gene_id 列\n")
        f.write("交回路径：项目阶段/05_数据与库/归档/data_export/cleaned_in/\n\n")
        for name, _ in CLEANING_QUEUES:
            f.write(f"  {name}.csv: {stats.get(name, 0)} 行\n")


def export_all(out_dir: str, *, queue_only: bool = False):
    os.makedirs(out_dir, exist_ok=True)
    tables_dir = os.path.join(out_dir, "02_tables")
    samples_dir = os.path.join(out_dir, "04_samples")
    master_dir = os.path.join(out_dir, "03_master")
    stats_dir = os.path.join(out_dir, "05_stats")
    for d in (tables_dir, samples_dir, master_dir, stats_dir):
        os.makedirs(d, exist_ok=True)

    stats = {}
    safe_print(f"导出到：{out_dir}")

    if queue_only:
        _export_cleaning_queues(out_dir, stats)
        _write_catalog(out_dir, stats)
        cleaned_in = os.path.join(_BASE_DIR, "项目阶段", "05_数据与库", "归档", "data_export", "cleaned_in")
        os.makedirs(cleaned_in, exist_ok=True)
        safe_print("[完成] 清洗队列导出完毕")
        return stats

    for name, sql in TABLES:
        if name in ("ensembl_id_version", "gene_assembly_xref") and not _table_exists(name):
            safe_print(f"  [跳过] {name} 表不存在")
            continue
        safe_print(f"  导出 {name} …")
        path = os.path.join(tables_dir, f"{name}.csv")
        cnt = _export_csv(sql, path, max_rows=10_000_000)
        stats[name] = cnt
        sample_sql = sql.rstrip().rstrip(";") + f" LIMIT {SAMPLE_SIZE}"
        _export_csv(sample_sql, os.path.join(samples_dir, f"{name}_sample.csv"), max_rows=SAMPLE_SIZE)

    safe_print("  导出 gene_master …")
    master_path = os.path.join(master_dir, "gene_master.csv")
    stats["gene_master"] = _export_csv(MASTER_SQL, master_path, max_rows=10_000_000)

    _export_cleaning_queues(out_dir, stats)

    with open(os.path.join(stats_dir, "row_counts.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    _write_schema(out_dir)
    _write_quality_notes(out_dir, stats)
    _write_catalog(out_dir, stats)
    cleaned_in = os.path.join(_BASE_DIR, "项目阶段", "05_数据与库", "归档", "data_export", "cleaned_in")
    os.makedirs(cleaned_in, exist_ok=True)
    with open(os.path.join(cleaned_in, "README.txt"), "w", encoding="utf-8") as f:
        f.write("将清洗后的 CSV 放在此目录，然后运行菜单 14 或 import_cleaned_gene_data\n")
    safe_print("[完成] 导出完毕")
    return stats


def main():
    p = argparse.ArgumentParser(description="导出斑马鱼基因数据供外部清洗")
    p.add_argument(
        "--out",
        default="",
        help="输出目录，默认 项目阶段/05_数据与库/归档/data_export/zebrafish_gene_YYYYMMDD",
    )
    p.add_argument(
        "--queue-only",
        action="store_true",
        help="仅导出 06_cleaning_queue 人工清洗队列",
    )
    args = p.parse_args()
    stamp = datetime.now().strftime("%Y%m%d")
    out = args.out or os.path.join(_BASE_DIR, "项目阶段", "05_数据与库", "归档", "data_export", f"zebrafish_gene_{stamp}")
    export_all(out, queue_only=args.queue_only)
    cleaned_in = os.path.join(_BASE_DIR, "项目阶段", "05_数据与库", "归档", "data_export", "cleaned_in")
    safe_print(f"\n请打包整个文件夹发送：\n  {out}")
    safe_print(f"清洗交回目录：\n  {cleaned_in}")


if __name__ == "__main__":
    main()
