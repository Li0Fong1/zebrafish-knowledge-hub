# -*- coding: utf-8 -*-
"""补全无 GO 基因的 Ensembl 深度注释。"""
from core.entry import bootstrap_app

bootstrap_app()

import argparse

from common_utils import safe_print
from gene_bulk_collector import GenomeCollector


def _count_no_go():
    from sqlalchemy import text
    from mysql_client import get_mysql_client
    client = get_mysql_client()
    db = next(client.get_db())
    try:
        row = db.execute(text(
            "SELECT COUNT(*) AS n FROM gene_basic gb "
            "WHERE NOT EXISTS (SELECT 1 FROM gene_go g WHERE g.gene_id = gb.gene_id)"
        )).mappings().first()
        return int(row["n"] or 0)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="GO 深度注释补全（Ensembl enrich）")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--retry-failed", action="store_true", default=True)
    args = parser.parse_args()

    before = _count_no_go()
    safe_print("=" * 60)
    safe_print(f"  GO 深度注释补全（当前无 GO 基因：{before}）")
    safe_print("=" * 60)

    collector = GenomeCollector(dry_run=False)
    collector.enrich_genes(
        workers=args.workers,
        retry_empty_go=args.retry_failed,
        go_backfill=True,
    )
    collector.close()

    after = _count_no_go()
    safe_print(f"\n[完成] 无 GO 基因：{before} → {after}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
