# -*- coding: utf-8
"""从 literature_catalog.jsonl 增量提取基因–PMID 关联 → gene_reference。"""
from core.entry import bootstrap_app

bootstrap_app()

import argparse
import sys

from common_utils import safe_print
from literature_gene_linker import run_gene_linking


def main():
    parser = argparse.ArgumentParser(
        description="文献 catalog → gene_reference 增量关联（可与菜单 11 并行）"
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=5000,
        help="每批最多处理文献数（默认 5000）",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="持续增量模式：catalog 追完后定期等待新文献",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=120,
        help="--loop 模式下无新文献时的等待秒数（默认 120）",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="重置进度，从头处理 catalog（不删已有 gene_reference）",
    )
    args = parser.parse_args()

    try:
        run_gene_linking(
            max_papers=args.max_papers,
            loop=args.loop,
            interval=args.interval,
            reset=args.reset,
        )
    except KeyboardInterrupt:
        safe_print("\n[中断] 进度已保存，下次运行自动续传。")
        sys.exit(130)
    except Exception as e:
        safe_print(f"[错误] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
