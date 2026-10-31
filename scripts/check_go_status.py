# -*- coding: utf-8 -*-
"""检查 enrich 深度注释（GO）完成情况。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

import json
import os

import pymysql

from common_utils import safe_print
from config import DB_CONFIG, _BASE_DIR

PROGRESS_FILE = os.path.join(_BASE_DIR, "data", "ensembl", "enrich_progress.json")
CATALOG_CACHE = os.path.join(_BASE_DIR, "data", "ensembl", "gene_catalog.json")


def main():
    conn = pymysql.connect(**DB_CONFIG)
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(DISTINCT gene_id) FROM gene_go WHERE gene_id IS NOT NULL"
    )
    with_go = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT gene_id) FROM homology_gene")
    with_homology = cur.fetchone()[0]

    cur.execute("SELECT COUNT(DISTINCT gene_id) FROM gene_reference")
    with_refs = cur.fetchone()[0]

    progress_n = 0
    done_ids: set = set()
    if os.path.isfile(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            done_ids = set(data.get("done", []))
            progress_n = len(done_ids)

    ensembl_no_go = 0
    ensembl_no_go_with_homology = 0
    if done_ids:
        ids = list(done_ids)
        for i in range(0, len(ids), 400):
            chunk = ids[i : i + 400]
            ph = ",".join(["%s"] * len(chunk))
            cur.execute(
                f"""
                SELECT gb.gene_id FROM gene_basic gb
                LEFT JOIN (SELECT gene_id, COUNT(*) n FROM gene_go GROUP BY gene_id) g
                  ON gb.gene_id = g.gene_id
                WHERE gb.ensembl_id IN ({ph}) AND (g.n IS NULL OR g.n = 0)
                """,
                chunk,
            )
            for (gid,) in cur.fetchall():
                ensembl_no_go += 1
                cur.execute(
                    "SELECT COUNT(*) FROM homology_gene WHERE gene_id=%s", (gid,)
                )
                if cur.fetchone()[0]:
                    ensembl_no_go_with_homology += 1

    catalog_pc = 0
    if os.path.isfile(CATALOG_CACHE):
        with open(CATALOG_CACHE, encoding="utf-8") as f:
            catalog_pc = sum(
                1
                for g in json.load(f).get("genes", [])
                if g.get("biotype") == "protein_coding"
            )

    conn.close()

    annotatable = with_go + ensembl_no_go
    pct = (with_go / annotatable * 100) if annotatable else 0

    safe_print("【GO 深度注释状态】")
    safe_print(f"  enrich 已处理（progress）： {progress_n}")
    safe_print(f"  数据库已有 GO：          {with_go}（占已处理 {pct:.1f}%）")
    safe_print(f"  已有同源注释：            {with_homology}")
    safe_print(f"  已有 PubMed 关联：        {with_refs}")
    safe_print(f"  Ensembl 源库无 GO：       {ensembl_no_go}（再跑 enrich 也无法生成）")
    if ensembl_no_go:
        safe_print(
            f"    └ 其中仅有同源、无 GO： {ensembl_no_go_with_homology}；"
            f"完全无注释：{ensembl_no_go - ensembl_no_go_with_homology}"
        )
    if catalog_pc:
        safe_print(f"  GTF 蛋白编码基因总数：    {catalog_pc}（参考）")

    if progress_n and ensembl_no_go == 0:
        safe_print("\n✓ 深度注释已全部完成（凡 Ensembl 有 GO 的均已入库）")
    elif progress_n >= with_go and ensembl_no_go > 0:
        safe_print("\n✓ enrich 队列已跑完；剩余为 Ensembl 本身无 GO 的基因，属正常情况")
    elif ensembl_no_go:
        safe_print("\n若 progress 未跑满且仍有缺 GO，可补跑：")
        safe_print("  python -m scripts.collect_genome --enrich-only --enrich-retry-failed")


if __name__ == "__main__":
    main()
