# -*- coding: utf-8 -*-
"""
全基因组斑马鱼基因采集（Ensembl 最新 release）

用法示例：
  # 推荐：全基因目录 + 全量 cDNA（约 5–15 分钟）
  python -m scripts.collect_genome

  # 仅蛋白编码基因（~2.5 万）
  python -m scripts.collect_genome --biotype protein_coding

  # 额外做 REST 深度注释（慢，可断点续传）
  python -m scripts.collect_genome --enrich

  # 预览不写入
  python -m scripts.collect_genome --dry-run
"""
from core.entry import bootstrap_app

bootstrap_app()

import argparse
import sys

from common_utils import safe_print
from gene_bulk_collector import run_full_collection, get_latest_release, DEFAULT_ENRICH_WORKERS


def main():
    rel = get_latest_release()
    parser = argparse.ArgumentParser(
        description="从 Ensembl 最新 release 全量采集斑马鱼基因（GTF 批量入库 + 可选 cDNA / 深度注释）",
    )
    parser.add_argument("--release", type=int, default=0, help=f"Ensembl release 版本，0=自动（当前 {rel}）")
    parser.add_argument(
        "--biotype", default="all",
        help="基因类型：all（默认全部）| protein_coding | lncRNA 或逗号分隔多种",
    )
    parser.add_argument("--skip-existing", action="store_true", default=True, help="跳过已入库 Ensembl ID（默认开启）")
    parser.add_argument("--no-skip-existing", dest="skip_existing", action="store_false", help="强制更新全部")
    parser.add_argument("--force-download", action="store_true", help="重新下载 GTF / cDNA")
    parser.add_argument("--no-cdna", action="store_true", help="不下载全量 cDNA FASTA")
    parser.add_argument("--enrich", action="store_true", help="REST 深度注释 GO/同源/文献（慢，支持续传）")
    parser.add_argument("--enrich-only", action="store_true", help="仅深度注释，跳过 GTF 入库和 cDNA 下载")
    parser.add_argument("--enrich-limit", type=int, default=0, help="深度注释最多处理 N 个基因，0=不限制")
    parser.add_argument(
        "--enrich-workers", type=int, default=0,
        help="深度注释并行线程数，0=默认(4)或环境变量 ENRICH_WORKERS",
    )
    parser.add_argument(
        "--enrich-cdna", action="store_true",
        help="深度注释时逐基因抓取 cDNA（已有全量 reference_genome.fasta 时默认跳过）",
    )
    parser.add_argument(
        "--enrich-retry-failed", action="store_true",
        help="补跑失败/无 GO 的基因（校正 progress 后只重试 GO 为空的条目）",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅解析 GTF 预览，不写库")
    args = parser.parse_args()

    safe_print("【准备检查】")
    safe_print("  1. MySQL 已启动，且已运行 init_database")
    safe_print("  2. 磁盘空间 ≥ 500 MB（GTF + cDNA）")
    safe_print("  3. 需联网访问 ftp.ensembl.org / rest.ensembl.org")
    safe_print("")

    try:
        run_full_collection(
            release=args.release,
            biotype=args.biotype,
            skip_existing=args.skip_existing,
            force_download=args.force_download,
            with_cdna=not args.no_cdna,
            enrich=args.enrich or args.enrich_only,
            enrich_only=args.enrich_only,
            enrich_limit=args.enrich_limit,
            enrich_workers=args.enrich_workers or DEFAULT_ENRICH_WORKERS,
            enrich_cdna=args.enrich_cdna,
            enrich_retry_failed=args.enrich_retry_failed,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        safe_print("\n[中断] 已停止。深度注释进度已保存，下次加 --enrich 可续传。")
        sys.exit(130)
    except Exception as e:
        safe_print(f"[错误] {e}")
        safe_print("请确认 MySQL 已启动：python -m database.init_database")
        sys.exit(1)


if __name__ == "__main__":
    main()
