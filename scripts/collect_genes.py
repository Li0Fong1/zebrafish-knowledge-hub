# -*- coding: utf-8 -*-
"""一键采集斑马鱼基因数据（Ensembl → MySQL + BLAST 参考序列）。"""
from core.entry import bootstrap_app

bootstrap_app()

import argparse
import os
import sys

from config import _BASE_DIR
from common_utils import safe_print
from gene_collector import (
    GeneCollector, load_gene_list_from_file, PRESET_CLASSIC,
    genes_on_chromosome, CHROMOSOMES, CDNA_FASTA,
)


def main():
    parser = argparse.ArgumentParser(
        description="从 Ensembl 采集斑马鱼基因数据写入 MySQL，并更新 BLAST cDNA 参考库",
    )
    parser.add_argument("--genes", help="逗号分隔基因名，如 tp53,hsp70,nanog")
    parser.add_argument("--file", help="基因列表文件，每行一个符号")
    parser.add_argument("--preset", choices=["classic"], help="使用内置基因集")
    parser.add_argument("--chromosome", help="按染色体采集，如 5；填 all 扫描全部染色体")
    parser.add_argument("--limit", type=int, default=100, help="染色体模式最大基因数（默认100）")
    parser.add_argument("--skip-existing", action="store_true", help="跳过数据库中已有基因")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入数据库")
    parser.add_argument("--reset-fasta", action="store_true", help="采集前清空 cDNA FASTA 缓存")
    args = parser.parse_args()

    symbols = []
    if args.genes:
        symbols.extend([g.strip() for g in args.genes.split(",") if g.strip()])
    if args.file:
        symbols.extend(load_gene_list_from_file(args.file))
    if args.preset == "classic":
        symbols.extend(PRESET_CLASSIC)
    if args.chromosome:
        if args.chromosome.lower() == "all":
            per_chr = max(10, args.limit // len(CHROMOSOMES))
            for chrom in CHROMOSOMES:
                safe_print(f"[扫描] 染色体 {chrom} ...")
                found = genes_on_chromosome(chrom, limit=per_chr)
                symbols.extend(found)
                if len(symbols) >= args.limit:
                    break
        else:
            symbols.extend(genes_on_chromosome(args.chromosome, limit=args.limit))

    if not symbols:
        default_file = os.path.join(_BASE_DIR, "data", "gene_list.txt")
        if os.path.isfile(default_file):
            symbols = load_gene_list_from_file(default_file)
        else:
            symbols = PRESET_CLASSIC

    # 去重保序
    seen = set()
    unique = []
    for s in symbols:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            unique.append(s)

    max_n = args.limit if args.chromosome else len(unique)
    final_list = unique[:max_n]

    if args.reset_fasta and os.path.isfile(CDNA_FASTA):
        os.remove(CDNA_FASTA)

    safe_print("=" * 60)
    safe_print("  斑马鱼基因数据采集")
    safe_print("  数据源：https://rest.ensembl.org (Danio rerio GRCz11)")
    safe_print("=" * 60)
    safe_print("【准备检查】")
    safe_print("  1. MySQL 已启动，且已运行 init_database 建好表结构")
    safe_print("  2. 本机可访问 Ensembl（需联网）")
    safe_print("  3. 首次建议：python -m scripts.collect_genes --preset classic")
    safe_print("=" * 60)

    collector = GeneCollector(dry_run=args.dry_run)
    try:
        if not args.dry_run:
            collector.connect()
        collector.collect_symbols(final_list, args.skip_existing)
    except Exception as e:
        safe_print(f"[错误] {e}")
        safe_print("请确认 MySQL 已启动且已执行：python -m database.init_database")
        sys.exit(1)
    finally:
        collector.close()


if __name__ == "__main__":
    main()
