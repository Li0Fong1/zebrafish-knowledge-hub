# -*- coding: utf-8 -*-
"""数据库结构升级（2026-11-04）。

做四件事，全部可逆、不动业务数据：
  1. 归档 9 张无引用备份表到 zebrafish_archive（RENAME，可随时移回）
  2. 给 legacy_genome_feature 补三个索引（63,512 行，按 zfin/ensembl/chrom 查）
  3. 跨表引用完整性校验，结果写入 db_integrity_check
  4. 给缺注释的核心表补 COMMENT

用法：
    python scripts/upgrade_db_20261104.py            # 预演
    python scripts/upgrade_db_20261104.py --apply    # 执行
"""
import sys

import pymysql

DB = dict(host="127.0.0.1", port=3307, user="zebrafish",
          password="zebrafish_remote_2026", database="zebrafish_db",
          charset="utf8mb4")
APPLY = "--apply" in sys.argv
ARCHIVE = "zebrafish_archive"

# 无代码引用 + 属于一次性备份 → 移出主库
ARCHIVE_TABLES = [
    "_dedup_map_dedup20261103",
    "gene_alias_bak_dedup20261103",
    "gene_alias_bak_cleanup20261104",
    "gene_assembly_xref_bak_dedup20261103",
    "gene_basic_bak_cleanup20261104",
    "gene_basic_bak_dedup20261103",
    "gene_basic_uniprot_bak_20261031",
    "gene_xref_bak_cleanup20261104",
    "name_hit_unique_bak_dedup20261103",
]

# 列 → 索引名；只加不减
NEW_INDEXES = {
    "legacy_genome_feature": [
        ("idx_zfin", "zfin_id"),
        ("idx_ensembl", "ensembl_gene_id"),
        ("idx_chrom", "chrom, start_pos"),
    ],
}

# 补注释（原来为空的核心表）
COMMENTS = {
    "gene_master_grcz12tu": "GRCz12tu 基因主档（含 symbol/ZFIN/NCBI 聚合），由 grcz12tu_gene 派生",
    "grcz12tu_gene": "GRCz12tu 全量基因位点，RefSeq 原生坐标，坐标类查询的唯一事实源",
    "gene_id_map_grcz12tu": "GRCz11/GRCz12tu 之间的基因 ID 映射（含映射方法与证据等级）",
    "gene_uniprot_map": "gene_id ↔ UniProt accession 重建映射（含 keep/fix/fill/clear 决策）",
    "symbol_alias": "符号历史与别名（symbol_history 模块用）",
    "ensembl_release_gene": "Ensembl 各 release 的基因 ID 版本对照",
    "gene_region_annotation": "按区间切分的基因注释块（区间查基因用）",
    "legacy_genome_feature": "旧组装（GRCz10/11 等）的基因坐标，供跨组装坐标回溯",
    "name_hit_unique": "一词一基因：唯一命中索引（物化，加速精确名称检索）",
    "gene_name_bundle": "一基因全名包（物化，供提示词与唯一命中后展开）",
    "name_resolve_collision": "一词多基因：歧义清单，禁止静默取第一名",
}

conn = pymysql.connect(**DB)
cur = conn.cursor()


def q(sql, args=None):
    # 注意：不能写成 args or () —— 空元组会让 pymysql 执行 % 格式化，
    # SQL 里的字面量百分号（LIKE 'ZDB-GENE-%'）会被当成占位符而报错。
    cur.execute(sql, args)
    return cur.fetchall()


def one(sql, args=None):
    r = q(sql, args)
    return r[0][0] if r else None


def show(t):
    print()
    print("-" * 66)
    print(t)
    print("-" * 66)


print("模式：", "APPLY" if APPLY else "DRY-RUN")

# ============ 1. 归档死表 ============
show("1. 归档无引用备份表")
existing = {r[0] for r in q("""SELECT table_name FROM information_schema.tables
                                WHERE table_schema='zebrafish_db'""")}
to_move = [t for t in ARCHIVE_TABLES if t in existing]
print("  待移出:", len(to_move), "张")
rows_total = 0
for t in to_move:
    n = one("SELECT COUNT(*) FROM `%s`" % t)
    mb = one("""SELECT ROUND((data_length+index_length)/1048576,1) FROM information_schema.tables
                 WHERE table_schema='zebrafish_db' AND table_name=%s""", (t,))
    rows_total += n or 0
    print("    %-40s %8d 行  %6s MB" % (t, n, mb))
print("  合计 %d 行" % rows_total)

# ============ 2. 索引 ============
show("2. 待补索引")
plan_idx = []
for tbl, idxs in NEW_INDEXES.items():
    if tbl not in existing:
        print("  跳过（表不存在）:", tbl)
        continue
    have = {r[0] for r in q("SHOW INDEX FROM `%s`" % tbl)}
    for name, cols in idxs:
        if name in have:
            print("  已存在 %-24s %s" % (tbl + "." + name, cols))
        else:
            plan_idx.append((tbl, name, cols))
            print("  待添加 %-24s (%s)" % (tbl + "." + name, cols))

# ============ 3. 一致性校验（只读）============
show("3. 跨表引用完整性")

CHECKS = [
    ("gene_xref 指向不存在的基因",
     """SELECT COUNT(*) FROM gene_xref x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id
         WHERE b.gene_id IS NULL"""),
    ("gene_go 指向不存在的基因",
     """SELECT COUNT(*) FROM gene_go x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id
         WHERE b.gene_id IS NULL"""),
    ("gene_kegg 指向不存在的基因",
     """SELECT COUNT(*) FROM gene_kegg x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id
         WHERE b.gene_id IS NULL"""),
    ("gene_expression 指向不存在的基因",
     """SELECT COUNT(*) FROM gene_expression x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id
         WHERE b.gene_id IS NULL"""),
    ("gene_reference 指向不存在的基因",
     """SELECT COUNT(*) FROM gene_reference x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id
         WHERE b.gene_id IS NULL"""),
    ("gene_alias 指向不存在的基因",
     """SELECT COUNT(*) FROM gene_alias x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id
         WHERE b.gene_id IS NULL"""),
    ("homology_gene 指向不存在的基因",
     """SELECT COUNT(*) FROM homology_gene x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id
         WHERE b.gene_id IS NULL"""),
    # zfin_id 列只存 ZDB-GENE-*；gene_xref 里还混有 ZDB-MIRNAG/LINCRNAG 等
    # 其他 marker 类型，它们不是"缺漏"，校验时必须限定前缀，否则误报。
    ("gene_basic 有 ZDB-GENE 但 gene_xref 缺行",
     """SELECT COUNT(*) FROM gene_basic b
         WHERE b.zfin_id LIKE 'ZDB-GENE-%'
           AND NOT EXISTS (SELECT 1 FROM gene_xref x
                            WHERE x.gene_id=b.gene_id AND x.db_name='zfin'
                              AND UPPER(x.db_id)=UPPER(b.zfin_id))"""),
    ("gene_xref 的 ZDB-GENE 行与 gene_basic 不符",
     """SELECT COUNT(*) FROM gene_xref x JOIN gene_basic b ON b.gene_id=x.gene_id
         WHERE x.db_name='zfin' AND x.db_id LIKE 'ZDB-GENE-%'
           AND (b.zfin_id IS NULL OR UPPER(b.zfin_id)<>UPPER(x.db_id))"""),
    ("gene_basic 有 uniprot_id 但 gene_xref 无 uniprot 行",
     """SELECT COUNT(*) FROM gene_basic b
         WHERE b.uniprot_id IS NOT NULL AND b.uniprot_id<>''
           AND NOT EXISTS (SELECT 1 FROM gene_xref x
                            WHERE x.gene_id=b.gene_id AND x.db_name='uniprot')"""),
    ("name_hit_unique 指向不存在的基因",
     """SELECT COUNT(*) FROM name_hit_unique x LEFT JOIN gene_basic b ON b.gene_id=x.gene_id
         WHERE b.gene_id IS NULL"""),
    ("gene_name_bundle 与 gene_basic 基因数不一致",
     """SELECT ABS((SELECT COUNT(*) FROM gene_basic) -
                  (SELECT COUNT(*) FROM gene_name_bundle))"""),
]

results = []
for label, sql in CHECKS:
    try:
        v = one(sql) or 0
    except Exception as e:
        v = -1
        label += "（查询失败：%s）" % str(e)[:50]
    results.append((label, v))
    print("  %-42s %s" % (label, "OK" if v == 0 else v))

bad = [r for r in results if r[1] != 0]
print()
print("  异常项:", len(bad), "/", len(results))

# ============ 4. 表注释 ============
show("4. 待补表注释")
plan_cmt = []
for t, c in COMMENTS.items():
    if t not in existing:
        continue
    have = one("""SELECT table_comment FROM information_schema.tables
                   WHERE table_schema='zebrafish_db' AND table_name=%s""", (t,)) or ""
    if have.strip():
        print("  已有 %-32s %s" % (t, have[:30]))
    else:
        plan_cmt.append((t, c))
        print("  待补 %-32s %s" % (t, c[:40]))

if not APPLY:
    print()
    print("DRY-RUN 结束，未修改任何结构。")
    cur.close()
    conn.close()
    sys.exit(0)

# ============ 执行 ============
show("5. 执行")

try:
    # 5.1 建归档库
    cur.execute("CREATE DATABASE IF NOT EXISTS %s DEFAULT CHARSET utf8mb4" % ARCHIVE)
    conn.commit()
    print("  归档库 %s 就绪" % ARCHIVE)

    # 5.2 RENAME 移出（原子，可逆）
    moved = 0
    for t in to_move:
        cur.execute("RENAME TABLE zebrafish_db.`%s` TO %s.`%s`" % (t, ARCHIVE, t))
        moved += 1
    conn.commit()
    print("  已移出 %d 张表到 %s" % (moved, ARCHIVE))

    # 5.2a 先清掉与 gene_basic 不符的 ZDB-GENE 行（gene_basic 是 11-04 按 ZFIN
    # 官方清理过的权威值，xref 里留着旧值只会让 ID 映射给出错的基因）
    cur.execute("""DELETE x FROM gene_xref x JOIN gene_basic b ON b.gene_id=x.gene_id
                    WHERE x.db_name='zfin' AND x.db_id LIKE 'ZDB-GENE-%'
                      AND (b.zfin_id IS NULL OR UPPER(b.zfin_id)<>UPPER(x.db_id))""")
    xref_del = cur.rowcount
    print("  gene_xref 清不符行: %d" % xref_del)

    # 5.2b 补齐 gene_xref 缺失的 ZFIN 行（以 gene_basic 为准）
    cur.execute("""INSERT IGNORE INTO gene_xref (gene_id, db_name, db_id, source)
                   SELECT b.gene_id, 'zfin', b.zfin_id, 'sync20261104'
                     FROM gene_basic b
                    WHERE b.zfin_id LIKE 'ZDB-GENE-%'
                      AND NOT EXISTS (SELECT 1 FROM gene_xref x
                                       WHERE x.gene_id=b.gene_id AND x.db_name='zfin'
                                         AND UPPER(x.db_id)=UPPER(b.zfin_id))""")
    xref_fill = cur.rowcount
    print("  gene_xref 补 ZFIN 行: %d" % xref_fill)

    # 5.3 索引
    added = 0
    for tbl, name, cols in plan_idx:
        cur.execute("ALTER TABLE `%s` ADD INDEX `%s` (%s)" % (tbl, name, cols))
        added += 1
    conn.commit()
    print("  已添加 %d 个索引" % added)

    # 5.4 表注释
    cmt_n = 0
    for t, c in plan_cmt:
        cur.execute("ALTER TABLE `%s` COMMENT = %%s" % t, (c,))
        cmt_n += 1
    conn.commit()
    print("  已补 %d 条表注释" % cmt_n)

    # 5.4b 用执行后的真实值刷新巡检结果
    results = []
    for label, sql in CHECKS:
        try:
            v = one(sql) or 0
        except Exception:
            v = -1
        results.append((label, v))

    # 5.5 校验结果落库
    cur.execute("""CREATE TABLE IF NOT EXISTS db_integrity_check (
        id INT AUTO_INCREMENT PRIMARY KEY,
        checked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        item VARCHAR(120) NOT NULL,
        bad_rows BIGINT NOT NULL,
        note VARCHAR(200),
        KEY idx_time (checked_at)
      ) DEFAULT CHARSET=utf8mb4 COMMENT='跨表引用完整性巡检记录'""")
    cur.executemany(
        "INSERT INTO db_integrity_check (item, bad_rows, note) VALUES (%s,%s,%s)",
        [(lbl, v, "0 为正常") for lbl, v in results])
    conn.commit()
    print("  巡检结果已写入 db_integrity_check")

except Exception as e:
    conn.rollback()
    print("  !! 执行失败：", e)
    cur.close()
    conn.close()
    sys.exit(1)

# ============ 校验 ============
show("6. 执行后校验")
print("  主库表数:", one("""SELECT COUNT(*) FROM information_schema.tables
                            WHERE table_schema='zebrafish_db'"""))
print("  归档库表数:", one("""SELECT COUNT(*) FROM information_schema.tables
                              WHERE table_schema='%s'""" % ARCHIVE))
print("  主库体积: %.1f MB" % (one("""SELECT ROUND(SUM(data_length+index_length)/1048576,1)
                                       FROM information_schema.tables
                                      WHERE table_schema='zebrafish_db'""") or 0))
print("  归档库体积: %.1f MB" % (one("""SELECT ROUND(SUM(data_length+index_length)/1048576,1)
                                        FROM information_schema.tables
                                       WHERE table_schema='%s'""" % ARCHIVE) or 0))
print("  legacy 索引:", [r[0] for r in q("SHOW INDEX FROM legacy_genome_feature")])
print("  gene_basic 行数:", one("SELECT COUNT(*) FROM gene_basic"))

cur.close()
conn.close()
print()
print("完成。")
