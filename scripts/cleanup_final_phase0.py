#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库最终整理 —— 阶段 0（数据清理，全部可回滚）

执行：
  0.1 归档 gene_basic_bak_uniprot20261104 到 zebrafish_archive
  0.2 重跑一致性巡检，刷新 db_integrity_check
  0.3 清理 gene_alias 垃圾串 + 自指
  0.4 合并 ntla -> tbxta（权威判定：ZFIN 官方符号 tbxta，坐标 chr19）

用法：
  python cleanup_final_phase0.py            # 只预演（dry-run）
  python cleanup_final_phase0.py --apply    # 真执行

回滚：
  - 归档表：RENAME 可逆
  - gene_alias 删除：备份 gene_alias_bak_final20261104 + 删前行存 gene_alias_deleted_final20261104
  - merged_into：改回 NULL 即可（ntla 只有这一处）
"""

import sys
import pymysql

DB = dict(host="127.0.0.1", port=3307, user="zebrafish",
          password="zebrafish_remote_2026", database="zebrafish_db",
          charset="utf8mb4", autocommit=False)

APPLY = "--apply" in sys.argv


def log(msg):
    print(msg, flush=True)


def main():
    conn = pymysql.connect(**DB)
    cur = conn.cursor()

    # ---------- 0.1 归档备份表 ----------
    log("[0.1] 归档备份表")
    cur.execute("""
        SELECT COUNT(*) FROM information_schema.tables
         WHERE table_schema='zebrafish_db' AND table_name='gene_basic_bak_uniprot20261104'
    """)
    exists = cur.fetchone()[0]
    if exists:
        if APPLY:
            cur.execute("""
                RENAME TABLE zebrafish_db.gene_basic_bak_uniprot20261104
                          TO zebrafish_archive.gene_basic_bak_uniprot20261104
            """)
            log("  RENAME 完成")
        else:
            log("  [dry] 将 RENAME gene_basic_bak_uniprot20261104 -> zebrafish_archive")
    else:
        log("  已不在主库，跳过")

    # ---------- 0.3 清理 gene_alias 垃圾串 + 自指 ----------
    log("[0.3] 清理 gene_alias 垃圾串 + 自指")
    cur.execute("""
        SELECT COUNT(*) FROM gene_alias
         WHERE alias='-' OR TRIM(alias)=''
            OR (CHAR_LENGTH(alias)<2 AND alias NOT REGEXP '[A-Za-z0-9]')
    """)
    n_junk = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*) FROM gene_alias a
         JOIN gene_basic b ON b.gene_id=a.gene_id
         WHERE LOWER(a.alias)=LOWER(b.gene_name)
            OR LOWER(a.alias)=LOWER(COALESCE(b.gene_symbol,''))
    """)
    n_self = cur.fetchone()[0]
    log("  垃圾串 %d 条，自指 %d 条" % (n_junk, n_self))

    if APPLY:
        # 备份整表（可回滚）
        cur.execute("DROP TABLE IF EXISTS gene_alias_bak_final20261104")
        cur.execute("CREATE TABLE gene_alias_bak_final20261104 AS SELECT * FROM gene_alias")
        log("  已备份 gene_alias_bak_final20261104 (%d 行)" % n_backup(cur))

        # 删前行先存到 deleted 表（审计）。del_reason 显式定长，避免 AS SELECT
        # 把字符串常量推断成 varchar(1) 导致 "Data too long"。
        cur.execute("DROP TABLE IF EXISTS gene_alias_deleted_final20261104")
        cur.execute("""
            CREATE TABLE gene_alias_deleted_final20261104 (
              id INT NOT NULL, gene_id INT NOT NULL, alias VARCHAR(128),
              alias_type VARCHAR(32), source VARCHAR(32), is_primary TINYINT(1),
              del_reason VARCHAR(16) NOT NULL
            )
        """)
        cur.execute("""
            INSERT INTO gene_alias_deleted_final20261104
            SELECT a.id, a.gene_id, a.alias, a.alias_type, a.source, a.is_primary, 'junk'
              FROM gene_alias a
             WHERE a.alias='-' OR TRIM(a.alias)=''
                OR (CHAR_LENGTH(a.alias)<2 AND a.alias NOT REGEXP '[A-Za-z0-9]')
        """)
        cur.execute("""
            INSERT INTO gene_alias_deleted_final20261104
            SELECT a.id, a.gene_id, a.alias, a.alias_type, a.source, a.is_primary, 'selfref'
              FROM gene_alias a
             JOIN gene_basic b ON b.gene_id=a.gene_id
             WHERE LOWER(a.alias)=LOWER(b.gene_name)
                OR LOWER(a.alias)=LOWER(COALESCE(b.gene_symbol,''))
        """)

        # 删除
        cur.execute("""
            DELETE a FROM gene_alias a
             WHERE a.alias='-' OR TRIM(a.alias)=''
                OR (CHAR_LENGTH(a.alias)<2 AND a.alias NOT REGEXP '[A-Za-z0-9]')
        """)
        n_del_junk = cur.rowcount
        cur.execute("""
            DELETE a FROM gene_alias a
             JOIN gene_basic b ON b.gene_id=a.gene_id
             WHERE LOWER(a.alias)=LOWER(b.gene_name)
                OR LOWER(a.alias)=LOWER(COALESCE(b.gene_symbol,''))
        """)
        n_del_self = cur.rowcount
        log("  已删除 垃圾 %d / 自指 %d" % (n_del_junk, n_del_self))
    else:
        log("  [dry] 将删除 垃圾 %d / 自指 %d" % (n_junk, n_self))

    # ---------- 0.4 合并 ntla -> tbxta ----------
    log("[0.4] 合并 ntla(14) -> tbxta(1194)")
    cur.execute("SELECT merged_into FROM gene_basic WHERE gene_id=14")
    row = cur.fetchone()
    log("  当前 ntla.merged_into = %s" % (row[0] if row else None))
    if APPLY:
        cur.execute("UPDATE gene_basic SET merged_into=1194 WHERE gene_id=14")
        log("  已设置 ntla.merged_into=1194")
    else:
        log("  [dry] 将设置 ntla.merged_into=1194")

    if APPLY:
        conn.commit()
        log("已提交")
    else:
        conn.rollback()
        log("[dry-run] 未提交，已回滚")

    # ---------- 0.2 重跑巡检（放最后，用清理后的真实值） ----------
    if APPLY:
        log("[0.2] 重跑一致性巡检")
        run_integrity_check(cur)
        conn.commit()
        log("巡检结果已刷新")
    else:
        log("[0.2] [dry] 将重跑巡检刷新 db_integrity_check")

    cur.close()
    conn.close()


def n_backup(cur):
    cur.execute("SELECT COUNT(*) FROM gene_alias_bak_final20261104")
    return cur.fetchone()[0]


def run_integrity_check(cur):
    """重跑 12 项一致性检查，写入 db_integrity_check"""
    checks = [
        ("gene_xref 指向不存在的基因",
         "SELECT COUNT(*) FROM gene_xref x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id WHERE b.gene_id IS NULL"),
        ("gene_go 指向不存在的基因",
         "SELECT COUNT(*) FROM gene_go x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id WHERE b.gene_id IS NULL"),
        ("gene_kegg 指向不存在的基因",
         "SELECT COUNT(*) FROM gene_kegg x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id WHERE b.gene_id IS NULL"),
        ("gene_expression 指向不存在的基因",
         "SELECT COUNT(*) FROM gene_expression x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id WHERE b.gene_id IS NULL"),
        ("gene_reference 指向不存在的基因",
         "SELECT COUNT(*) FROM gene_reference x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id WHERE b.gene_id IS NULL"),
        ("gene_alias 指向不存在的基因",
         "SELECT COUNT(*) FROM gene_alias x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id WHERE b.gene_id IS NULL"),
        ("homology_gene 指向不存在的基因",
         "SELECT COUNT(*) FROM homology_gene x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id WHERE b.gene_id IS NULL"),
        ("gene_basic 有 ZDB-GENE 但 gene_xref 缺行",
         "SELECT COUNT(*) FROM gene_basic b WHERE b.zfin_id LIKE 'ZDB-GENE-%' AND NOT EXISTS (SELECT 1 FROM gene_xref x WHERE x.gene_id=b.gene_id AND x.db_name='zfin' AND UPPER(x.db_id)=UPPER(b.zfin_id))"),
        ("gene_xref 的 ZDB-GENE 行与 gene_basic 不符",
         "SELECT COUNT(*) FROM gene_xref x JOIN gene_basic b ON b.gene_id=x.gene_id WHERE x.db_name='zfin' AND x.db_id LIKE 'ZDB-GENE-%' AND (b.zfin_id IS NULL OR UPPER(b.zfin_id)<>UPPER(x.db_id))"),
        ("gene_basic 有 uniprot_id 但 gene_xref 无 uniprot 行",
         "SELECT COUNT(*) FROM gene_basic b WHERE b.uniprot_id<>'' AND NOT EXISTS (SELECT 1 FROM gene_xref x WHERE x.gene_id=b.gene_id AND x.db_name='uniprot')"),
        ("name_hit_unique 指向不存在的基因",
         "SELECT COUNT(*) FROM name_hit_unique x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id WHERE b.gene_id IS NULL"),
        ("gene_name_bundle 与 gene_basic 基因数不一致",
         "SELECT ABS((SELECT COUNT(*) FROM gene_basic) - (SELECT COUNT(*) FROM gene_name_bundle))"),
    ]
    cur.execute("DELETE FROM db_integrity_check")
    for item, sql in checks:
        try:
            cur.execute(sql)
            v = cur.fetchone()[0]
        except Exception as e:
            v = -1
        cur.execute("INSERT INTO db_integrity_check (item, bad_rows, note) VALUES (%s,%s,%s)",
                    (item, v, "0 为正常"))
        log("  %-42s %s" % (item, v))


if __name__ == "__main__":
    main()
