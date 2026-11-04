# -*- coding: utf-8 -*-
"""以 UniProt 斑马鱼全量（taxid 7955）为唯一真相源，重建 gene_basic.uniprot_id 映射。

背景（2026-11-04 体检）：
  - gene_basic 现有 21,161 个 uniprot_id，其中 491 条与 UniProt 名称不符（错挂），
    164 条 accession 在斑马鱼 UniProt 里根本不存在（实为小鼠/爪蟾/鸡条目）。
  - 原因是历史回填未校验物种与名称。

做法（全部可回滚，不直接改原表）：
  1. 建 gene_uniprot_map 结果表
  2. 按「主名 > 正式同义名 > 数据库别名」三级匹配，reviewed 优先
  3. 唯一命中才采用；多候选记 conflict；无候选记 none
  4. 与现值比对，输出 will_fix / will_clear / will_keep / will_fill

用法：
    python scripts/rebuild_uniprot_map.py            # 生成映射表
    python scripts/rebuild_uniprot_map.py --report   # 只打印统计
"""
from __future__ import annotations

import argparse
import collections
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

DDL = """
CREATE TABLE IF NOT EXISTS gene_uniprot_map (
  gene_id        INT NOT NULL,
  gene_name      VARCHAR(64) NOT NULL,
  uniprot_old    VARCHAR(16)  NULL,
  uniprot_new    VARCHAR(16)  NULL,
  method         VARCHAR(24) NOT NULL,
  n_candidates   SMALLINT    NOT NULL DEFAULT 0,
  candidates     VARCHAR(255) NULL,
  reviewed_new   TINYINT      NULL,
  action         VARCHAR(16) NOT NULL,
  built_at       TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (gene_id),
  KEY idx_action (action),
  KEY idx_new (uniprot_new)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='UniProt accession 重建映射（可回滚）'
"""


def norm(s):
    return (s or "").strip().lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    conn = pymysql.connect(**DB)
    cur = conn.cursor()
    cur.execute(DDL)
    conn.commit()

    # ---------- 真相源 ----------
    cur.execute("SELECT accession, gene_primary, gene_names, reviewed FROM uniprot_protein")
    up = {}
    tok2acc = collections.defaultdict(list)   # 名称 -> [(accession, reviewed)]
    for acc, prim, names, rv in cur.fetchall():
        toks = set()
        if norm(prim):
            toks.add(norm(prim))
        if names:
            for t in names.replace(";", " ").split():
                if norm(t):
                    toks.add(norm(t))
        up[acc] = (norm(prim), toks, rv)
        for t in toks:
            tok2acc[t].append((acc, rv))
    print("真相源 uniprot_protein: %d 条，名称索引 %d 个" % (len(up), len(tok2acc)))

    # ---------- 别名 ----------
    cur.execute("SELECT gene_id, alias FROM gene_alias WHERE alias IS NOT NULL AND alias<>''")
    alias = collections.defaultdict(set)
    for gid, a in cur.fetchall():
        a = norm(a)
        if a and len(a) <= 40:
            alias[gid].add(a)
    print("别名: %d 个基因有别名" % len(alias))

    # ---------- 待处理基因 ----------
    cur.execute("SELECT gene_id, gene_name, uniprot_id FROM gene_basic")
    genes = cur.fetchall()
    print("gene_basic: %d" % len(genes))

    rows = []
    stat = collections.Counter()

    # ---------- 阶段 0：收集候选 ----------
    items = []
    for gid, gname, old in genes:
        g = norm(gname)
        old_n = norm(old) or None
        if g in tok2acc:
            cands = tok2acc[g]
            prim_hit = any(up.get(a, ("",))[0] == g for a, _ in cands)
            method = "name_primary" if prim_hit else "name_synonym"
        else:
            agg = []
            for a in alias.get(gid, ()):
                if a != g:
                    agg.extend(tok2acc.get(a, ()))
            cands = agg
            method = "alias" if agg else "none"
        uniq = {}
        for acc, rv in cands:
            uniq[acc] = max(rv, uniq.get(acc, 0))
        items.append(dict(gid=gid, gname=gname, old=old_n, uniq=uniq, method=method))

    # ---------- 阶段 1：锁定现值（现值在自身候选内 → 保留，最小变更） ----------
    owner = {}      # ACC -> gene_id
    decided = {}    # gene_id -> (action, acc, reviewed)
    for it in items:
        old = it["old"]
        if old and old.upper() in it["uniq"]:
            acc = old.upper()
            if acc not in owner:
                owner[acc] = it["gid"]
                decided[it["gid"]] = ("keep", acc, it["uniq"][acc])

    # ---------- 阶段 2：全局唯一分配（primary > synonym > alias） ----------
    order = {"name_primary": 0, "name_synonym": 1, "alias": 2, "none": 9}
    pend = sorted((it for it in items if it["gid"] not in decided),
                  key=lambda it: (order[it["method"]], it["gname"]))
    for it in pend:
        if not it["uniq"]:
            continue
        free = [a for a in it["uniq"] if a not in owner]
        if not free:
            continue
        rev = sorted(a for a in free if it["uniq"][a] == 1)
        acc = rev[0] if rev else sorted(free, key=lambda a: (len(a), a))[0]
        owner[acc] = it["gid"]
        decided[it["gid"]] = ("assign", acc, it["uniq"][acc])

    # ---------- 汇总 ----------
    for it in items:
        gid, gname, old, method = it["gid"], it["gname"], it["old"], it["method"]
        n = len(it["uniq"])
        candtxt = ",".join(sorted(it["uniq"])[:6]) or None
        d = decided.get(gid)
        if d is None:
            new, rv = None, None
            # 未获得唯一 accession 且现值存在：被他人占用 / 跨物种孤儿 / 候选全被占
            # 三种情况都属"当前值不可信"，一律清空（宁可空，不挂错）
            if old:
                action = "clear"
            else:
                action = "conflict" if it["uniq"] else "none"
        else:
            kind, new, rv = d
            if kind == "keep":
                action = "keep"
            else:
                action = "fix" if old else "fill"
        stat[action] += 1
        stat["m_" + method] += 1
        rows.append((gid, gname, old, new, method, n, candtxt, rv, action))

    print()
    print("动作分布:", dict(stat))
    print()
    print("方法分布:", {k: v for k, v in stat.items() if k.startswith("m_")})

    if args.report:
        print()
        print("fix 样本（旧 -> 新）:")
        for r in [x for x in rows if x[8] == "fix"][:25]:
            print("  %-20s %-11s -> %-11s [%s]" % (r[1], r[2], r[3], r[4]))
        print()
        print("clear 样本（旧值将被清空，属跨物种/错挂脏数据）:")
        for r in [x for x in rows if x[8] == "clear"][:25]:
            print("  %-20s %-11s" % (r[1], r[2]))
        conn.close()
        return

    cur.execute("TRUNCATE TABLE gene_uniprot_map")
    sql = """INSERT INTO gene_uniprot_map
             (gene_id, gene_name, uniprot_old, uniprot_new, method,
              n_candidates, candidates, reviewed_new, action)
             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
    for i in range(0, len(rows), 2000):
        cur.executemany(sql, rows[i:i + 2000])
        conn.commit()
    print()
    print("已写入 gene_uniprot_map: %d 行" % len(rows))

    cur.execute("SELECT action, COUNT(*) FROM gene_uniprot_map GROUP BY action ORDER BY 2 DESC")
    for a, n in cur.fetchall():
        print("  %-8s %d" % (a, n))
    conn.close()


if __name__ == "__main__":
    main()
