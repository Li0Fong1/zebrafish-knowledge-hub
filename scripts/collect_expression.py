# -*- coding: utf-8 -*-
"""导入 Expression Atlas 胚胎发育 TPM 表达数据。"""
from core.entry import bootstrap_app

bootstrap_app()

import argparse
import sys

from common_utils import safe_print
from expression_collector import run_expression_import


def main():
    parser = argparse.ArgumentParser(description="从 EBI Expression Atlas 导入斑马鱼胚胎 TPM 表达量")
    parser.add_argument("--force-download", action="store_true", help="重新下载 TPM 文件")
    parser.add_argument("--keep-existing", action="store_true", help="不清空已有表达数据，追加写入")
    parser.add_argument("--min-tpm", type=float, default=0.5, help="最低 TPM 阈值（默认 0.5）")
    parser.add_argument("--top-stages", type=int, default=0, help="每基因最多保存 N 个高表达时期，0=全部18期")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        run_expression_import(
            force_download=args.force_download,
            replace_all=not args.keep_existing,
            min_tpm=args.min_tpm,
            top_stages=args.top_stages,
            dry_run=args.dry_run,
        )
    except Exception as e:
        safe_print(f"[错误] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
