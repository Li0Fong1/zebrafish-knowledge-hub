# -*- coding: utf-8 -*-
"""
并行提升数据完整度（表达量 + 文献 RAG）

与深度注释 --enrich-only 可同时运行，互不冲突。
"""
from core.entry import bootstrap_app

bootstrap_app()

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from common_utils import safe_print


def main():
    parser = argparse.ArgumentParser(
        description="并行运行：表达量导入 + PubMed 文献 RAG 扩充",
    )
    parser.add_argument("--expression-only", action="store_true")
    parser.add_argument("--literature-only", action="store_true")
    parser.add_argument("--per-query", type=int, default=150, help="文献每主题篇数")
    parser.add_argument("--force-download", action="store_true", help="重新下载 TPM")
    args = parser.parse_args()

    run_expr = not args.literature_only
    run_lit = not args.expression_only

    safe_print("=" * 60)
    safe_print("  数据增强：表达量 + 文献 RAG（可与 --enrich-only 并行）")
    safe_print("=" * 60)

    errors = []

    def _expression():
        from expression_collector import run_expression_import
        run_expression_import(force_download=args.force_download)

    def _literature():
        from literature_collector import run_literature_collection
        run_literature_collection(per_query=args.per_query)

    tasks = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        if run_expr:
            tasks.append(("表达量", pool.submit(_expression)))
        if run_lit:
            tasks.append(("文献RAG", pool.submit(_literature)))
        for name, fut in tasks:
            try:
                fut.result()
                safe_print(f"\n[OK] {name} 完成")
            except Exception as e:
                errors.append(f"{name}: {e}")
                safe_print(f"\n[失败] {name}: {e}")

    safe_print("\n" + "=" * 60)
    if errors:
        safe_print("  部分任务失败，请查看上方日志")
        sys.exit(1)
    safe_print("  数据增强完成")
    safe_print("=" * 60)


if __name__ == "__main__":
    main()
