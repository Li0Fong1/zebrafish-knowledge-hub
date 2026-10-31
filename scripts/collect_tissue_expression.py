# -*- coding: utf-8 -*-
"""采集 Bgee 成体/组织表达（与 enrich 并行无冲突）。"""
from core.entry import bootstrap_app

bootstrap_app()

import argparse

from common_utils import safe_print
from bgee_collector import run_bgee_import


def main():
    p = argparse.ArgumentParser(description="Bgee 组织表达导入")
    p.add_argument("--force-download", action="store_true")
    p.add_argument("--top-tissues", type=int, default=18)
    p.add_argument("--min-score", type=float, default=40.0)
    p.add_argument("--skip-dev-stages", action="store_true", help="跳过 blastula 等发育阶段项")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run_bgee_import(
        force_download=args.force_download,
        top_tissues=args.top_tissues,
        min_score=args.min_score,
        skip_dev_stages=args.skip_dev_stages,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
