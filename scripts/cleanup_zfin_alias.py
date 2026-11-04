# -*- coding: utf-8 -*-
"""按方案执行 ZFIN ID 与错挂别名的清理（带整表备份、回滚列、审计日志）。

用法：
    python scripts/cleanup_zfin_alias.py            # 预演，不改数据
    python scripts/cleanup_zfin_alias.py --apply    # 执行

方案来源：cleanup_zfin_plan / cleanup_alias_plan / cleanup_orphan_zfin
  cleanup_zfin_plan : keep(不动) / fix(改成 ZFIN 官方对应的真 ID) / clear(清空)
  cleanup_alias_plan: delete(别名已归属另一基因) / keep(同一基因的两条记录)

安全措施：
  1. gene_basic / gene_alias / gene_xref 整表备份到 *_bak_cleanup20261104
  2. gene_basic 新增 zfin_id_old 列，逐条可回滚
  3. 被删除的别名整行备份到 gene_alias_deleted_20261104
  4. 每一步写 gene_cleanup_log（表/字段/旧值/新值/原因/时间）
  5. 执行前后各做一次一致性校验
"""
import sys

import pymysql

DB = dict(host="127.0.0.1", port=3307, user="zebrafish",
          password="zebrafish_remote_2026", database="zebrafish_db",
          charset="utf8mb4")
APPLY = "--apply" in sys.argv
SUFFIX = "20261104"

conn = pymysql.connect(**DB)
cur = conn.cursor()


def q(sql, args=None):
    cur.execute(sql, args or ())
    return cur.fetchall()


def one(sql, args=None):
    r = q(sql, args)
    return r[0][0] if r else None


def show(title):
    print()
    print("-" * 66)
    print(title)
    print("-" * 66)


# ============ 0. 读方案 ============
plans = q("SELECT gene_id, zfin_old, action, COALESCE(zfin_new,'') "
          "FROM cleanup_zfin_plan WHERE action IN ('fix','clear')")
fixes = [p for p in plans if p[2] == "fix"]
clears = [p for p in plans if p[2] == "clear"]
orphans = q("SELECT gene_id, zfin_old, suggest FROM cleanup_orphan_zfin "
            "WHERE suggest IS NOT NULL AND suggest<>''")
alias_del = q("SELECT alias_id, gene_id, alias FROM cleanup_alias_plan WHERE action='delete'")

print("方案：fix %d / clear %d / 孤立可改正 %d / 别名删除 %d"
      % (len(fixes), len(clears), len(orphans), len(alias_del)))
print("模式：", "APPLY（会改数据）" if APPLY else "DRY-RUN（只读）")

# ============ 1. 前置检查 ============
show("1. 前置检查")

# fix 目标是否已被其他记录占用
conflict = []
for gid, old, _a, new in fixes:
    n = one("SELECT COUNT(*) FROM gene_basic WHERE zfin_id=%s AND gene_id<>%s", (new, gid))
    if n:
        conflict.append((gid, new, n))
if conflict:
    print("  !! fix 目标已被占用，需跳过：", conflict)
    fixes = [f for f in fixes if f[0] not in {c[0] for c in conflict}]
else:
    print("  fix 目标 ID 均未被其他记录占用")

print("  待改基因在 gene_basic 中存在性:",
      one("SELECT COUNT(*) FROM gene_basic WHERE gene_id IN (%s)"
          % ",".join(str(p[0]) for p in plans)) == len(plans))
print("  待删别名在 gene_alias 中存在性:",
      one("SELECT COUNT(*) FROM gene_alias WHERE id IN (%s)"
          % ",".join(str(a[0]) for a in alias_del)) == len(alias_del))

# ============ 2. 预演样本 ============
show("2. 将发生的变更（样本）")
print("  fix（旧 -> 新）：")
for gid, old, _a, new in fixes[:8]:
    nm = one("SELECT gene_name FROM gene_basic WHERE gene_id=%s", (gid,))
    print("    id=%-6d %-20s %s -> %s" % (gid, nm, old, new))
print("  clear（清空）：")
for gid, old, _a, _n in clears[:8]:
    nm = one("SELECT gene_name FROM gene_basic WHERE gene_id=%s", (gid,))
    print("    id=%-6d %-20s %s -> NULL" % (gid, nm, old))
print("  删除别名（样本）：")
for aid, gid, al in alias_del[:8]:
    nm = one("SELECT gene_name FROM gene_basic WHERE gene_id=%s", (gid,))
    print("    alias_id=%-6d %-18s 挂在 %s" % (aid, al[:18], nm))

if not APPLY:
    print()
    print("DRY-RUN 结束，未修改任何数据。加 --apply 执行。")
    cur.close()
    conn.close()
    sys.exit(0)

# ============ 3. 备份 ============
show("3. 备份")
tables = [
    ("gene_basic", "gene_basic_bak_cleanup%s" % SUFFIX),
    ("gene_alias", "gene_alias_bak_cleanup%s" % SUFFIX),
    ("gene_xref", "gene_xref_bak_cleanup%s" % SUFFIX),
]
for src, dst in tables:
    cur.execute("DROP TABLE IF EXISTS %s" % dst)
    cur.execute("CREATE TABLE %s AS SELECT * FROM %s" % (dst, src))
    print("  %-30s <- %s (%d 行)" % (dst, src, one("SELECT COUNT(*) FROM %s" % dst)))

cur.execute("DROP TABLE IF EXISTS gene_alias_deleted_%s" % SUFFIX)
cur.execute("""CREATE TABLE gene_alias_deleted_%s AS
               SELECT a.* FROM gene_alias a
               JOIN cleanup_alias_plan p ON p.alias_id=a.id AND p.action='delete'""" % SUFFIX)
print("  %-30s %d 行别名将被删除（已另存）"
      % ("gene_alias_deleted_%s" % SUFFIX, one("SELECT COUNT(*) FROM gene_alias_deleted_%s" % SUFFIX)))

cols = [r[0] for r in q("SHOW COLUMNS FROM gene_basic")]
if "zfin_id_old" not in cols:
    cur.execute("ALTER TABLE gene_basic ADD COLUMN zfin_id_old VARCHAR(32) NULL")
    print("  已新增回滚列 gene_basic.zfin_id_old")
else:
    print("  回滚列 zfin_id_old 已存在，跳过")

cur.execute("""CREATE TABLE IF NOT EXISTS gene_cleanup_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    table_name VARCHAR(32) NOT NULL,
    gene_id INT,
    field VARCHAR(32),
    old_value VARCHAR(128),
    new_value VARCHAR(128),
    action VARCHAR(16),
    reason VARCHAR(200),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY (gene_id), KEY (action)
  ) DEFAULT CHARSET=utf8mb4""")
conn.commit()

# ============ 4. 执行 ============
show("4. 执行变更")
before_total = one("SELECT COUNT(*) FROM gene_basic")
before_zfin = one("SELECT COUNT(*) FROM gene_basic WHERE zfin_id IS NOT NULL AND zfin_id<>''")
before_alias = one("SELECT COUNT(*) FROM gene_alias")
print("  变更前：gene_basic %d（有 zfin %d）· gene_alias %d"
      % (before_total, before_zfin, before_alias))

try:
    conn.begin()

    # 4.1 fix
    n_fix = 0
    for gid, old, _a, new in fixes:
        cur.execute("UPDATE gene_basic SET zfin_id_old=%s, zfin_id=%s WHERE gene_id=%s", (old, new, gid))
        cur.execute("""INSERT INTO gene_cleanup_log
                       (table_name,gene_id,field,old_value,new_value,action,reason)
                       VALUES ('gene_basic',%s,'zfin_id',%s,%s,'fix','按 ZFIN 官方清单改正')""",
                    (gid, old, new))
        n_fix += cur.rowcount
    print("  fix  gene_basic.zfin_id :", n_fix)

    # 4.2 clear
    n_clr = 0
    for gid, old, _a, _n in clears:
        cur.execute("UPDATE gene_basic SET zfin_id_old=%s, zfin_id=NULL WHERE gene_id=%s AND zfin_id=%s",
                    (old, gid, old))
        cur.execute("""INSERT INTO gene_cleanup_log
                       (table_name,gene_id,field,old_value,new_value,action,reason)
                       VALUES ('gene_basic',%s,'zfin_id',%s,NULL,'clear','该 ID 应属同组另一条记录')""",
                    (gid, old))
        n_clr += cur.rowcount
    print("  clear gene_basic.zfin_id:", n_clr)

    # 4.3 孤立假 ID 改正
    n_orph = 0
    for gid, old, new in orphans:
        cur.execute("UPDATE gene_basic SET zfin_id_old=%s, zfin_id=%s WHERE gene_id=%s AND zfin_id=%s",
                    (old, new, gid, old))
        cur.execute("""INSERT INTO gene_cleanup_log
                       (table_name,gene_id,field,old_value,new_value,action,reason)
                       VALUES ('gene_basic',%s,'zfin_id',%s,%s,'fix','该 ID 在 ZFIN 不存在，按名称改正')""",
                    (gid, old, new))
        n_orph += cur.rowcount
    print("  fix  孤立假 ZFIN ID  :", n_orph)

    # 4.4 同步 gene_xref（zfin 行）
    # gene_xref 有唯一键 uk_gene_db(gene_id, db_name, db_id)，直接 UPDATE 会撞上
    # 已经存在的那条正确记录，所以按「先删旧值、再 INSERT IGNORE 新值」做。
    n_x1 = n_x2 = 0
    for gid, old, act, new in plans:
        cur.execute("""DELETE FROM gene_xref
                        WHERE gene_id=%s AND db_name='zfin' AND LOWER(db_id)=LOWER(%s)""",
                    (gid, old))
        n_x2 += cur.rowcount
        if act == "fix":
            cur.execute("""INSERT IGNORE INTO gene_xref (gene_id, db_name, db_id)
                           VALUES (%s,'zfin',%s)""", (gid, new))
            n_x1 += cur.rowcount
    for gid, old, new in orphans:
        cur.execute("""DELETE FROM gene_xref
                        WHERE gene_id=%s AND db_name='zfin' AND LOWER(db_id)=LOWER(%s)""",
                    (gid, old))
        n_x2 += cur.rowcount
        cur.execute("""INSERT IGNORE INTO gene_xref (gene_id, db_name, db_id)
                       VALUES (%s,'zfin',%s)""", (gid, new))
        n_x1 += cur.rowcount
    print("  gene_xref 同步       : 补/改 %d / 删旧 %d" % (n_x1, n_x2))

    # 4.5 同步 gene_alias 里的 zfin_id 类型行
    cur.execute("""UPDATE gene_alias a
                     JOIN cleanup_zfin_plan p ON p.gene_id=a.gene_id AND p.action='fix'
                      SET a.alias=LOWER(p.zfin_new)
                    WHERE a.alias_type='zfin_id' AND LOWER(a.alias)=LOWER(p.zfin_old)""")
    n_a1 = cur.rowcount
    cur.execute("""INSERT INTO gene_cleanup_log
                   (table_name,gene_id,field,old_value,new_value,action,reason)
                   SELECT 'gene_alias', a.gene_id, 'alias', a.alias, NULL,
                          'delete', '随 ZFIN ID 一并清空'
                     FROM gene_alias a
                     JOIN cleanup_zfin_plan p ON p.gene_id=a.gene_id AND p.action='clear'
                    WHERE a.alias_type='zfin_id' AND LOWER(a.alias)=LOWER(p.zfin_old)""")
    cur.execute("""DELETE a FROM gene_alias a
                     JOIN cleanup_zfin_plan p ON p.gene_id=a.gene_id AND p.action='clear'
                    WHERE a.alias_type='zfin_id' AND LOWER(a.alias)=LOWER(p.zfin_old)""")
    n_a2 = cur.rowcount
    print("  gene_alias 同步      : 改 %d / 删 %d" % (n_a1, n_a2))

    # 4.6 删除错挂别名
    cur.execute("""INSERT INTO gene_cleanup_log
                   (table_name,gene_id,field,old_value,new_value,action,reason)
                   SELECT 'gene_alias', a.gene_id, 'alias', a.alias, NULL, 'delete',
                          CONCAT('该符号属于另一个基因 gene_id=', p.owner_gene_id)
                     FROM gene_alias a
                     JOIN cleanup_alias_plan p ON p.alias_id=a.id AND p.action='delete'""")
    cur.execute("""DELETE a FROM gene_alias a
                   JOIN cleanup_alias_plan p ON p.alias_id=a.id AND p.action='delete'""")
    n_ad = cur.rowcount
    print("  删除错挂别名         :", n_ad)

    cur.execute("UPDATE cleanup_zfin_plan SET applied=1")
    cur.execute("UPDATE cleanup_alias_plan SET applied=1")

    conn.commit()
    print("  事务已提交")
except Exception as e:
    conn.rollback()
    print("  !! 执行失败，已回滚：", e)
    cur.close()
    conn.close()
    sys.exit(1)

# ============ 5. 校验 ============
show("5. 执行后校验")
after_total = one("SELECT COUNT(*) FROM gene_basic")
after_zfin = one("SELECT COUNT(*) FROM gene_basic WHERE zfin_id IS NOT NULL AND zfin_id<>''")
after_alias = one("SELECT COUNT(*) FROM gene_alias")

print("  gene_basic 行数      : %d -> %d  %s"
      % (before_total, after_total, "OK" if before_total == after_total else "!! 变化了"))
print("  有 zfin_id 的记录    : %d -> %d" % (before_zfin, after_zfin))
print("  gene_alias 行数      : %d -> %d" % (before_alias, after_alias))

dup = one("""SELECT COUNT(*) FROM (
               SELECT zfin_id FROM gene_basic
                WHERE zfin_id IS NOT NULL AND zfin_id<>''
                GROUP BY zfin_id HAVING COUNT(*)>1) t""")
print("  一号多记录（ZFIN）   :", dup, "OK" if dup == 0 else "!! 仍有重复")

dup2 = one("""SELECT COUNT(*) FROM (
                SELECT uniprot_id FROM gene_basic
                 WHERE uniprot_id IS NOT NULL AND uniprot_id<>''
                 GROUP BY uniprot_id HAVING COUNT(*)>1) t""")
print("  一号多记录（UniProt）:", dup2, "OK" if dup2 == 0 else "!! 回归")

roll = one("""SELECT COUNT(*) FROM gene_basic b
               JOIN gene_basic_bak_cleanup%s k ON k.gene_id=b.gene_id
              WHERE b.zfin_id_old IS NOT NULL AND b.zfin_id_old<>k.zfin_id""" % SUFFIX)
print("  回滚列与备份一致性   :", "OK" if roll == 0 else "!! %d 条不符" % roll)

print()
print("  抽查关键基因：")
for nm in ("sox9b", "fgf8a", "actb1", "actb2", "mapk1", "tp53", "her6", "ntla", "cxcr7"):
    r = q("""SELECT gene_id, gene_name, COALESCE(zfin_id,'-'), COALESCE(zfin_id_old,'-')
               FROM gene_basic WHERE gene_name=%s LIMIT 2""", (nm,))
    print("    %-8s %s" % (nm, r))

cur.close()
conn.close()
print()
print("完成。回滚方式见 docs/数据清理_20261104.md")
