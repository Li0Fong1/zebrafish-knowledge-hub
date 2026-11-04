# -*- coding: utf-8 -*-
"""把 gene_uniprot_map 的重建结果应用到 gene_basic / gene_xref。

安全措施：
  - 应用前整表备份 gene_basic -> gene_basic_bak_uniprot20261104
  - 保留原值到 gene_basic.uniprot_id_old，可逐条回滚
  - 记录 method 便于追溯
  - 全部在一个事务里，失败自动回滚

用法：
    python scripts/apply_uniprot_map.py --dry-run
    python scripts/apply_uniprot_map.py --apply
"""
from __future__ import annotations

import argparse
import os

import pymysql

DB = dict(
    host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
    port=int(os.environ.get("MYSQL_PORT", "3307")),
    user=os.environ.get("MYSQL_USER", "zebrafish"),
    password=os.environ.get("MYSQL_PASSWORD", "zebrafish_remote_2026"),
    database=os.environ.get("MYSQL_DATABASE", "zebrafish_db"),
    charset="utf8mb4",
)

BAK = "gene_basic_bak_uniprot20261104"


def one(cur, sql):
    cur.execute(sql)
    return cur.fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    dry = not args.apply

    conn = pymysql.connect(**DB)
    cur = conn.cursor()

    print("=" * 66)
    print("应用前基线")
    print("=" * 66)
    print("  gene_basic 行数:", one(cur, "SELECT COUNT(*) FROM gene_basic"))
    print("  有 uniprot_id:", one(cur, "SELECT COUNT(*) FROM gene_basic WHERE uniprot_id<>''"))
    print("  gene_xref uniprot:", one(cur, "SELECT COUNT(*) FROM gene_xref WHERE db_name='uniprot'"))
    print("  映射表:", one(cur, "SELECT COUNT(*) FROM gene_uniprot_map"))
    cur.execute("SELECT action, COUNT(*) FROM gene_uniprot_map GROUP BY action ORDER BY 2 DESC")
    print("  动作:", cur.fetchall())

    if dry:
        print()
        print("[dry-run] 不执行。确认无误后加 --apply")
        conn.close()
        return

    # 1) 备份
    print()
    print("[1] 备份 gene_basic ->", BAK)
    cur.execute("DROP TABLE IF EXISTS %s" % BAK)
    cur.execute("CREATE TABLE %s LIKE gene_basic" % BAK)
    cur.execute("INSERT INTO %s SELECT * FROM gene_basic" % BAK)
    conn.commit()
    print("    备份行数:", one(cur, "SELECT COUNT(*) FROM %s" % BAK))

    # 2) 加追溯列
    cur.execute("SHOW COLUMNS FROM gene_basic LIKE 'uniprot_id_old'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE gene_basic ADD COLUMN uniprot_id_old VARCHAR(32) NULL "
                    "COMMENT '重建前的 UniProt accession（2026-11-04 备份，可回滚）'")
    cur.execute("SHOW COLUMNS FROM gene_basic LIKE 'uniprot_method'")
    if not cur.fetchone():
        cur.execute("ALTER TABLE gene_basic ADD COLUMN uniprot_method VARCHAR(24) NULL "
                    "COMMENT 'UniProt 映射依据：name_primary/name_synonym/alias'")
    conn.commit()
    print("[2] 追溯列已就绪")

    # 3) 应用
    try:
        conn.begin()
        cur.execute("""
            UPDATE gene_basic b JOIN gene_uniprot_map m ON m.gene_id = b.gene_id
               SET b.uniprot_id_old = b.uniprot_id,
                   b.uniprot_id     = m.uniprot_new,
                   b.uniprot_method = m.method
             WHERE m.action IN ('fix','fill','clear')""")
        n_basic = cur.rowcount
        print("[3] gene_basic 更新:", n_basic, "行")

        # 4) 同步 gene_xref（uniprot 来源整体重建）
        cur.execute("DELETE FROM gene_xref WHERE db_name='uniprot'")
        n_del = cur.rowcount
        cur.execute("""
            INSERT INTO gene_xref (gene_id, db_name, db_id, db_label, source)
            SELECT gene_id, 'uniprot', uniprot_new, NULL, 'uniprot_rebuild_20261104'
              FROM gene_uniprot_map WHERE uniprot_new IS NOT NULL""")
        n_ins = cur.rowcount
        print("[4] gene_xref 重建: 删 %d / 插 %d" % (n_del, n_ins))

        conn.commit()
        print("[5] 已提交")
    except Exception as e:
        conn.rollback()
        print("!! 失败已回滚:", e)
        raise

    # 5) 校验
    print()
    print("=" * 66)
    print("应用后校验")
    print("=" * 66)
    print("  有 uniprot_id:", one(cur, "SELECT COUNT(*) FROM gene_basic WHERE uniprot_id<>''"))
    print("  唯一 accession:", one(cur, "SELECT COUNT(DISTINCT uniprot_id) FROM gene_basic WHERE uniprot_id<>''"))
    print("  仍跨物种/不存在（不在 uniprot_protein）:", one(cur, """
        SELECT COUNT(*) FROM gene_basic b WHERE b.uniprot_id<>''
          AND NOT EXISTS (SELECT 1 FROM uniprot_protein p WHERE p.accession=b.uniprot_id)"""))
    print("  accession 格式异常:", one(cur, """
        SELECT COUNT(*) FROM gene_basic WHERE uniprot_id<>'' AND uniprot_id NOT REGEXP '^[A-Z0-9]{6,10}$'"""))
    cur.execute("""SELECT uniprot_id, COUNT(*) c FROM gene_basic WHERE uniprot_id<>''
                    GROUP BY uniprot_id HAVING c>1""")
    dup = cur.fetchall()
    print("  一号多基因:", len(dup), dup[:5])

    print()
    print("  关键基因核对:")
    for g in ["tp53", "shha", "ntla", "myod1", "nanog", "myl7", "mitfa", "actb1", "actb2",
              "sox9b", "bmp4", "hsp70l", "kita", "wnt5b", "pou3f1"]:
        cur.execute("SELECT gene_name, uniprot_id, uniprot_id_old FROM gene_basic WHERE gene_name=%s", (g,))
        print("    %-9s %s" % (g, cur.fetchone()))

    conn.close()


if __name__ == "__main__":
    main()
