#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库最终整理 —— 阶段 1（建单一事实源，纯 SQL 灌库，秒级完成）

用 INSERT ... SELECT 一次性灌库，避免逐条 INSERT 在 SSH 通道下超时。

做四件事：
  1. 建 gene_name_index（唯一事实源）+ gene_name_conflict（歧义显式化）
  2. 从 gene_alias（已清垃圾+自指）+ gene_basic 灌入索引
  3. 歧义串写 gene_name_conflict；家族描述型标 is_current=0
  4. 验收

风险：两张新表可整表 DROP，不影响现有查询路径。

用法：
  python build_name_index.py            # 预演
  python build_name_index.py --apply    # 真建
"""

import sys
import pymysql

DB = dict(host="127.0.0.1", port=3307, user="zebrafish",
          password="zebrafish_remote_2026", database="zebrafish_db",
          charset="utf8mb4", autocommit=True)

APPLY = "--apply" in sys.argv


def log(msg):
    print(msg, flush=True)


def main():
    conn = pymysql.connect(**DB)
    cur = conn.cursor()

    if not APPLY:
        log("[dry-run] 只统计，不建表")
        cur.execute("SELECT COUNT(*) FROM gene_basic WHERE name_kind='symbol'")
        n_sym = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM gene_alias
             WHERE alias<>'-' AND TRIM(alias)<>'' AND CHAR_LENGTH(alias)>=2
        """)
        n_alias = cur.fetchone()[0]
        log("  符号 %d 条，别名 %d 条" % (n_sym, n_alias))
        cur.close(); conn.close(); return

    log("[1] 建表")
    cur.execute("DROP TABLE IF EXISTS gene_name_index")
    cur.execute("DROP TABLE IF EXISTS gene_name_conflict")
    cur.execute("""
    CREATE TABLE gene_name_index (
      id          INT AUTO_INCREMENT PRIMARY KEY,
      gene_id     INT NOT NULL,
      name        VARCHAR(200) NOT NULL,
      name_norm   VARCHAR(200) NOT NULL,
      kind        VARCHAR(16)  NOT NULL,
      source      VARCHAR(24)  NOT NULL,
      `rank`      TINYINT      NOT NULL DEFAULT 50,
      is_current  TINYINT(1)   NOT NULL DEFAULT 1,
      created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
      UNIQUE KEY uk_name_gene_src (name_norm, gene_id, source),
      KEY idx_name (name_norm),
      KEY idx_gene (gene_id)
    ) DEFAULT CHARSET=utf8mb4
    """)
    cur.execute("""
    CREATE TABLE gene_name_conflict (
      id          INT AUTO_INCREMENT PRIMARY KEY,
      name_norm   VARCHAR(200) NOT NULL UNIQUE,
      name        VARCHAR(200) NOT NULL,
      gene_ids    VARCHAR(400) NOT NULL,
      n_genes     INT NOT NULL,
      reason      VARCHAR(200),
      status      VARCHAR(16) DEFAULT 'open',
      resolved_to INT NULL,
      created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    ) DEFAULT CHARSET=utf8mb4
    """)
    log("  两张表已建")

    log("[2] 灌入索引（纯 SQL）")
    # 符号
    cur.execute("""
        INSERT IGNORE INTO gene_name_index (gene_id,name,name_norm,kind,source,`rank`,is_current)
        SELECT gene_id, gene_name,
               LOWER(REGEXP_REPLACE(gene_name,'[^a-zA-Z0-9]','')),
               'symbol','local',100,1
          FROM gene_basic WHERE name_kind='symbol'
    """)
    n_sym = cur.rowcount
    # 别名
    cur.execute("""
        INSERT IGNORE INTO gene_name_index (gene_id,name,name_norm,kind,source,`rank`,is_current)
        SELECT a.gene_id, a.alias,
               LOWER(REGEXP_REPLACE(a.alias,'[^a-zA-Z0-9]','')),
               CASE WHEN a.alias_type='full_name' THEN 'full_name'
                    WHEN a.alias_type IN ('former','nomenclature') THEN 'former'
                    ELSE 'synonym' END,
               COALESCE(a.source,'local'),
               CASE WHEN a.alias_type='full_name' THEN 40
                    WHEN a.alias_type IN ('former','nomenclature') THEN 40
                    ELSE 60 END,
               1
          FROM gene_alias a
         WHERE a.alias<>'-' AND TRIM(a.alias)<>'' AND CHAR_LENGTH(a.alias)>=2
    """)
    n_alias = cur.rowcount
    log("  符号 %d 条，别名 %d 条" % (n_sym, n_alias))

    log("[3] 写冲突表（歧义串）")
    # 一个 name_norm 对应 >=2 基因的串，用 GROUP_CONCAT 汇总 gene_id
    cur.execute("""
        INSERT INTO gene_name_conflict (name_norm,name,gene_ids,n_genes,reason,status)
        SELECT name_norm,
               MIN(name),
               GROUP_CONCAT(DISTINCT gene_id ORDER BY gene_id SEPARATOR ','),
               COUNT(DISTINCT gene_id),
               'ambiguous','open'
          FROM gene_name_index
         GROUP BY name_norm
        HAVING COUNT(DISTINCT gene_id) >= 2
    """)
    n_conf = cur.rowcount
    log("  歧义串 %d 条" % n_conf)

    log("[4] 家族描述型歧义串标 is_current=0（挂 >=6 基因且是描述型）")
    cur.execute("""
        UPDATE gene_name_index idx
          JOIN gene_name_conflict c ON c.name_norm=idx.name_norm
           SET idx.is_current=0
         WHERE c.n_genes >= 6
           AND (c.name REGEXP '-like$' OR c.name REGEXP 'family' OR c.name REGEXP 'receptor'
             OR c.name REGEXP '^novel ' OR c.name REGEXP 'protein' OR c.name REGEXP 'domain'
             OR c.name REGEXP 'putative' OR c.name REGEXP '^histone ' OR c.name REGEXP '^mhc'
             OR c.name REGEXP 'ligase' OR c.name REGEXP 'serine' OR c.name REGEXP 'interferon'
             OR c.name REGEXP 'glutathione' OR c.name REGEXP 'olfactomedin'
             OR c.name REGEXP 'microfibril' OR c.name REGEXP 'chemerin' OR c.name REGEXP 'gtpase'
             OR c.name REGEXP 'nlr' OR c.name REGEXP 'nacht' OR c.name REGEXP 'tripartite'
             OR c.name REGEXP 'immune' OR c.name REGEXP 'c-type' OR c.name REGEXP 'trim'
             OR c.name REGEXP 'ubiquitin' OR c.name REGEXP 'calcium-sensing'
             OR c.name REGEXP 'lectin' OR c.name REGEXP 'slam' OR c.name REGEXP 'zinc-binding'
             OR c.name REGEXP 'kinase')
    """)
    n_desc = cur.rowcount
    # 同步冲突表的 reason
    cur.execute("""
        UPDATE gene_name_conflict c
           SET c.reason='family_descriptive'
         WHERE c.n_genes >= 6
           AND (c.name REGEXP '-like$' OR c.name REGEXP 'family' OR c.name REGEXP 'receptor'
             OR c.name REGEXP '^novel ' OR c.name REGEXP 'protein' OR c.name REGEXP 'domain'
             OR c.name REGEXP 'putative' OR c.name REGEXP '^histone ' OR c.name REGEXP '^mhc'
             OR c.name REGEXP 'ligase' OR c.name REGEXP 'serine' OR c.name REGEXP 'interferon'
             OR c.name REGEXP 'glutathione' OR c.name REGEXP 'olfactomedin'
             OR c.name REGEXP 'microfibril' OR c.name REGEXP 'chemerin' OR c.name REGEXP 'gtpase'
             OR c.name REGEXP 'nlr' OR c.name REGEXP 'nacht' OR c.name REGEXP 'tripartite'
             OR c.name REGEXP 'immune' OR c.name REGEXP 'c-type' OR c.name REGEXP 'trim'
             OR c.name REGEXP 'ubiquitin' OR c.name REGEXP 'calcium-sensing'
             OR c.name REGEXP 'lectin' OR c.name REGEXP 'slam' OR c.name REGEXP 'zinc-binding'
             OR c.name REGEXP 'kinase')
    """)
    log("  家族描述型标 is_current=0 共 %d 条索引（涉及 %d 个串）"
        % (n_desc, cur.execute("SELECT COUNT(*) FROM gene_name_conflict WHERE reason='family_descriptive'")))

    log("[5] 验收")
    cur.execute("SELECT COUNT(*) FROM gene_name_index")
    n_idx = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT name_norm) FROM gene_name_index")
    n_norm = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM gene_name_conflict")
    n_conf2 = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM gene_name_conflict WHERE reason='family_descriptive'")
    n_fam = cur.fetchone()[0]
    log("  索引 %d 行 / %d 唯一名称；冲突 %d 条（家族描述型 %d）"
        % (n_idx, n_norm, n_conf2, n_fam))

    cur.close(); conn.close()


if __name__ == "__main__":
    main()
