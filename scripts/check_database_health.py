# -*- coding: utf-8 -*-
"""数据库体检：结构 + 数据完整性 + 各功能所需查询能否跑通（只读）。

用法（服务器或本机，能连到 MySQL 即可）：
    python -X utf8 scripts/check_database_health.py            # 全部
    python -X utf8 scripts/check_database_health.py --quick     # 跳过慢查询

设计原则
--------
1. **只读**：全部 SELECT / SHOW，不改一行数据。
2. **按功能点组织**：每条检查都写清「哪个功能依赖它」，失败即代表那个功能会出问题，
   而不是只报一句「表不存在」。
3. 结果分 PASS / WARN / FAIL：
   - FAIL = 功能会直接坏（表/列缺失、关键查询报错）
   - WARN = 数据质量问题（孤儿行、重复、空值率偏高），功能还能用但结果可能不准
4. 退出码：有 FAIL → 1，否则 0（便于接进 selfcheck）。

覆盖的功能点
-----------
基因详情 / 基因搜索 / ID 映射 / GO 注释 / 通路注释 / 表达（Bgee·Atlas）/
同源 / 文献库 / 富集（GO 背景+通路背景）/ 主页统计 / 访客统计 / 对话与账号 / 知识库
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "database", ROOT / "llm", ROOT / "rag", ROOT / "agent"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _load_env_defaults(path: Path) -> int:
    """先加载项目根 .env（不覆盖已有环境变量）。

    与 `core/entry.py::_load_dotenv` 同语义：先有 .env 才有 DB 账号，
    否则 `config.DB_CONFIG` 会退回默认 root，连不上库。
    """
    if not path.is_file():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            if key not in os.environ:
                n += 1
            os.environ.setdefault(key, val)
    return n


_ENV_LOADED = _load_env_defaults(ROOT / ".env")

import bootstrap  # noqa: F401,E402

from sqlalchemy import text  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []  # (级别, 名称, 说明)


def rec(level: str, name: str, detail: str = "") -> None:
    RESULTS.append((level, name, detail))
    print(f"[{level}] {name} — {detail}"[:260], flush=True)


def _db():
    from mysql_client import get_mysql_client

    client = get_mysql_client()
    return next(client.get_db())


def scalar(db, sql: str, params: dict | None = None):
    return db.execute(text(sql), params or {}).scalar()


def rows(db, sql: str, params: dict | None = None, limit: int = 10):
    out = db.execute(text(sql), params or {}).fetchall()
    return out[:limit]


def table_exists(db, name: str) -> bool:
    return bool(scalar(
        db,
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema=DATABASE() AND table_name=:t",
        {"t": name},
    ))


def columns_of(db, name: str) -> set[str]:
    return {
        r[0] for r in rows(
            db,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=DATABASE() AND table_name=:t",
            {"t": name}, limit=500,
        )
    }


# --------------------------------------------------------------------------- A
def section_a_connection(db) -> None:
    print("\n=== A. 连接与实例 ===", flush=True)
    ver = scalar(db, "SELECT VERSION()")
    charset = scalar(db, "SELECT @@character_set_database")
    collation = scalar(db, "SELECT @@collation_database")
    sqlmode = scalar(db, "SELECT @@sql_mode")
    tz = scalar(db, "SELECT @@time_zone")
    dbsize = scalar(db, "SELECT ROUND(SUM(data_length+index_length)/1024/1024,1) "
                         "FROM information_schema.tables WHERE table_schema=DATABASE()")
    ntbl = scalar(db, "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()")
    ntbl_base = scalar(db, "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() "
                           "AND table_type='BASE TABLE'")
    rec("PASS" if ntbl else "FAIL", "A1 连接可用",
        f"MySQL {ver} · charset={charset}/{collation} · time_zone={tz} · "
        f"{ntbl_base} 张基础表 · {dbsize} MB")
    if str(charset).lower() not in ("utf8mb4", "utf8"):
        rec("WARN", "A2 库字符集", f"当前 {charset}，中文/符号建议 utf8mb4")
    else:
        rec("PASS", "A2 库字符集", str(charset))
    if "ONLY_FULL_GROUP_BY" in str(sqlmode):
        rec("WARN", "A3 sql_mode", "含 ONLY_FULL_GROUP_BY：聚合查询写法要小心")
    else:
        rec("PASS", "A3 sql_mode", str(sqlmode)[:90])


# --------------------------------------------------------------------------- B
def section_b_tables(db) -> None:
    print("\n=== B. 关键表是否存在 ===", flush=True)
    expect = {
        "gene_basic": "基因详情 / 搜索 / 富集前景",
        "gene_go": "GO 注释 / GO 富集背景",
        "gene_kegg": "通路注释 / 通路富集背景",
        "gene_expression": "表达（Atlas/Bgee）",
        "gene_xref": "ID 映射 / 外链",
        "literature_paper": "文献库 / 脚注元数据",
    }
    for t, why in expect.items():
        if table_exists(db, t):
            n = scalar(db, f"SELECT COUNT(*) FROM {t}")
            rec("PASS" if n else "WARN", f"B {t}", f"{n:,} 行（{why}）")
        else:
            rec("FAIL", f"B {t}", f"表不存在 → 影响：{why}")
    extra = rows(db, "SELECT table_name, table_rows FROM information_schema.tables "
                     "WHERE table_schema=DATABASE() AND table_type='BASE TABLE' "
                     "ORDER BY table_rows DESC", limit=40)
    print("  全部表（行数，前 40）：", flush=True)
    for r in extra:
        print(f"    {r[0]:<32} {r[1] if r[1] is not None else '?'}", flush=True)


# --------------------------------------------------------------------------- C
def section_c_integrity(db) -> None:
    print("\n=== C. 数据完整性 ===", flush=True)
    if not table_exists(db, "gene_basic"):
        rec("FAIL", "C0 gene_basic 缺失", "后续完整性检查跳过")
        return

    total = scalar(db, "SELECT COUNT(*) FROM gene_basic")
    rec("PASS" if total else "FAIL", "C1 gene_basic 总量", f"{total:,} 个基因")

    dup_id = scalar(db, "SELECT COUNT(*) FROM (SELECT gene_id FROM gene_basic "
                        "GROUP BY gene_id HAVING COUNT(*)>1) x")
    rec("PASS" if dup_id == 0 else "FAIL", "C2 gene_id 唯一", f"重复 {dup_id} 组")

    col = columns_of(db, "gene_basic")
    name_col = "gene_name" if "gene_name" in col else ("symbol" if "symbol" in col else "")
    if name_col:
        blank = scalar(db, f"SELECT COUNT(*) FROM gene_basic "
                           f"WHERE {name_col} IS NULL OR TRIM({name_col})=''")
        rec("PASS" if blank == 0 else "WARN", f"C3 {name_col} 空值", f"{blank} 行空/NULL")
        dup_name = scalar(db, f"SELECT COUNT(*) FROM (SELECT {name_col} FROM gene_basic "
                             f"WHERE TRIM(COALESCE({name_col},''))<>'' GROUP BY {name_col} "
                             f"HAVING COUNT(*)>1) x")
        rec("PASS" if dup_name == 0 else "WARN", f"C4 {name_col} 重名",
            f"{dup_name} 个符号对应多个 gene_id（按符号查可能歧义，需 disambiguation）")
    else:
        rec("WARN", "C3 gene_basic 名称列", f"未找到 gene_name/symbol，实际列：{sorted(col)[:8]}")

    # 子表孤儿行
    child = {
        "gene_go": "GO 注释",
        "gene_kegg": "通路注释",
        "gene_expression": "表达",
        "gene_xref": "ID 映射",
    }
    for t, why in child.items():
        if not table_exists(db, t):
            continue
        orphan = scalar(db, f"SELECT COUNT(*) FROM {t} c "
                            f"LEFT JOIN gene_basic g ON c.gene_id=g.gene_id "
                            f"WHERE g.gene_id IS NULL")
        n = scalar(db, f"SELECT COUNT(*) FROM {t}")
        lvl = "PASS" if orphan == 0 else ("FAIL" if orphan > n * 0.05 else "WARN")
        rec(lvl, f"C5 {t} 孤儿行", f"{orphan}/{n} 行 gene_id 在主表找不到（{why}）")

    # GO 细节
    if table_exists(db, "gene_go"):
        cats = rows(db, "SELECT IFNULL(go_category,'<NULL>') c, COUNT(*) n FROM gene_go "
                        "GROUP BY c ORDER BY n DESC", limit=12)
        rec("PASS", "C6 GO 方面分布",
            " · ".join(f"{r[0]}={r[1]}" for r in cats))
        blank_term = scalar(db, "SELECT COUNT(*) FROM gene_go WHERE go_term IS NULL OR TRIM(go_term)=''")
        rec("PASS" if blank_term == 0 else "WARN", "C7 GO 条目空值", f"{blank_term} 行空")
        id_only = scalar(db, "SELECT COUNT(*) FROM gene_go WHERE go_term REGEXP '^GO:[0-9]{7}$'")
        rec("PASS" if id_only == 0 else "WARN", "C8 GO 只有 ID 无名称",
            f"{id_only} 行（前端已标注「名称未收录」并可跳 QuickGO）")
        dupg = scalar(db, "SELECT COUNT(*) FROM (SELECT gene_id, go_term FROM gene_go "
                          "GROUP BY gene_id, go_term HAVING COUNT(*)>1) x")
        rec("PASS" if dupg == 0 else "WARN", "C9 GO 重复行", f"{dupg} 组重复 (gene_id, go_term)")

    # 通路细节
    if table_exists(db, "gene_kegg"):
        srcs = rows(db, "SELECT IFNULL(source,'<NULL>') s, COUNT(*) n FROM gene_kegg "
                        "GROUP BY s ORDER BY n DESC", limit=12)
        rec("PASS", "C10 通路来源分布", " · ".join(f"{r[0]}={r[1]}" for r in srcs))
        blank_pw = scalar(db, "SELECT COUNT(*) FROM gene_kegg "
                              "WHERE pathway_id IS NULL OR TRIM(pathway_id)=''")
        rec("PASS" if blank_pw == 0 else "FAIL", "C11 通路 ID 空值", f"{blank_pw} 行")
        no_name = scalar(db, "SELECT COUNT(*) FROM gene_kegg "
                             "WHERE pathway_name IS NULL OR TRIM(pathway_name)=''")
        nlvl = "PASS" if no_name == 0 else ("WARN" if no_name < 100 else "WARN")
        rec(nlvl, "C12 通路名为空", f"{no_name} 行（前端回退显示 pathway_id）")
        dupk = scalar(db, "SELECT COUNT(*) FROM (SELECT gene_id, pathway_id FROM gene_kegg "
                          "GROUP BY gene_id, pathway_id HAVING COUNT(*)>1) x")
        rec("PASS" if dupk == 0 else "WARN", "C13 通路重复行", f"{dupk} 组重复 (gene_id, pathway_id)")

    # 文献
    if table_exists(db, "literature_paper"):
        lit = columns_of(db, "literature_paper")
        n = scalar(db, "SELECT COUNT(*) FROM literature_paper")
        rec("PASS", "C14 文献总量", f"{n:,} 篇 · 列 {len(lit)} 个")
        if "gold_status" in lit:
            gs = rows(db, "SELECT IFNULL(gold_status,'<NULL>') g, COUNT(*) n FROM literature_paper "
                          "GROUP BY g ORDER BY n DESC", limit=10)
            rec("PASS", "C15 gold_status 分布", " · ".join(f"{r[0]}={r[1]}" for r in gs))
        for c, label in (("pmid", "PMID"), ("doi", "DOI")):
            if c in lit:
                dup = scalar(db, f"SELECT COUNT(*) FROM (SELECT {c} FROM literature_paper "
                                 f"WHERE {c} IS NOT NULL AND TRIM({c})<>'' GROUP BY {c} "
                                 f"HAVING COUNT(*)>1) x")
                rec("PASS" if dup == 0 else "WARN", f"C16 {label} 重复", f"{dup} 个 {label} 对应多行")
        if "journal" in lit:
            bj = scalar(db, "SELECT COUNT(*) FROM literature_paper "
                            "WHERE journal IS NULL OR TRIM(journal)='' OR journal='Unlisted journal'")
            rec("PASS" if bj == 0 else "WARN", "C17 刊名缺失",
                f"{bj}/{n} 行（脚注会写 Unlisted journal）")


# --------------------------------------------------------------------------- D
def section_d_features(db, quick: bool = False) -> None:
    print("\n=== D. 功能级联（每个功能点跑一次真实查询）===", flush=True)

    def probe(feature: str, sql: str, params: dict | None = None, expect_rows: int = 1) -> None:
        t0 = time.time()
        try:
            r = rows(db, sql, params, limit=3)
            ms = (time.time() - t0) * 1000
            if len(r) >= expect_rows:
                rec("PASS", f"D {feature}", f"{len(r)} 行 / {ms:.0f} ms")
            else:
                rec("WARN", f"D {feature}", f"查询成功但 0 行（{ms:.0f} ms）")
        except Exception as e:  # noqa: BLE001
            rec("FAIL", f"D {feature}", f"{type(e).__name__}: {str(e)[:110]}")

    # 1 基因详情 / 搜索
    probe("基因按符号查（tp53）",
          "SELECT gene_id, gene_name FROM gene_basic WHERE gene_name=:s LIMIT 3", {"s": "tp53"})
    probe("基因模糊搜索（p53）",
          "SELECT gene_id, gene_name FROM gene_basic WHERE gene_name LIKE :s LIMIT 3", {"s": "%p53%"})

    # 2 ID 映射
    if table_exists(db, "gene_xref"):
        cols = columns_of(db, "gene_xref")
        for c in ("ensembl_id", "zfin_id", "ncbi_gene_id"):
            if c in cols:
                probe(f"ID 映射 by {c}",
                      f"SELECT gene_id, {c} FROM gene_xref WHERE {c} IS NOT NULL "
                      f"AND TRIM({c})<>'' LIMIT 3")

    # 3 GO / 通路 / 表达
    if table_exists(db, "gene_go"):
        probe("GO 注释 by gene_id",
              "SELECT go_term, go_category FROM gene_go WHERE gene_id=("
              "SELECT gene_id FROM gene_basic WHERE gene_name='tp53' LIMIT 1) LIMIT 3")
        probe("富集 GO 背景集（全量）",
              "SELECT go_term, COUNT(*) FROM gene_go GROUP BY go_term LIMIT 3")
    if table_exists(db, "gene_kegg"):
        probe("通路注释（富集背景 join 主表）",
              "SELECT k.pathway_id, k.pathway_name FROM gene_kegg k "
              "JOIN gene_basic g ON k.gene_id=g.gene_id LIMIT 3")
        probe("通路按关键词检索（p53）",
              "SELECT pathway_id, pathway_name FROM gene_kegg "
              "WHERE pathway_name LIKE :s LIMIT 3", {"s": "%p53%"})
    if table_exists(db, "gene_expression"):
        probe("表达记录 by gene_id",
              "SELECT gene_id FROM gene_expression WHERE gene_id=("
              "SELECT gene_id FROM gene_basic WHERE gene_name='nanog' LIMIT 1) LIMIT 3")

    # 4 文献
    if table_exists(db, "literature_paper"):
        lit_cols = columns_of(db, "literature_paper")
        title_col = "title_en" if "title_en" in lit_cols else ("title" if "title" in lit_cols else "")
        if title_col:
            probe("文献按 PMID 取",
                  f"SELECT pmid, {title_col} FROM literature_paper "
                  f"WHERE pmid IS NOT NULL AND TRIM(pmid)<>'' LIMIT 3")
        else:
            rec("FAIL", "D 文献取题名", f"literature_paper 无题名列，实际列：{sorted(lit_cols)[:10]}")
        probe("文献按年份统计（首页 years）",
              "SELECT year, COUNT(*) FROM literature_paper WHERE year IS NOT NULL "
              "GROUP BY year ORDER BY year DESC LIMIT 3")
        if not quick:
            if table_exists(db, "literature_fulltext_gold"):
                # 用实际列探活：本表主键是 doc_id，**没有 pmid 列**（曾因此报 1054 Unknown column）。
                gold_cols = columns_of(db, "literature_fulltext_gold")
                keys = [c for c in ("doc_id", "gold_version", "source_tier") if c in gold_cols]
                if keys and "body_text" in gold_cols:
                    probe("全文金标表可读（gold_status=ready 正文来源）",
                          "SELECT " + ", ".join(keys) + ", LENGTH(body_text) "
                          "FROM literature_fulltext_gold LIMIT 3")
                else:
                    rec("FAIL", "D 全文金标表结构", f"缺关键列，实际列：{sorted(gold_cols)[:10]}")
            probe("文献 DOI 冲突面（同一 DOI 多篇标题）",
                  "SELECT doi, COUNT(*) FROM literature_paper WHERE doi IS NOT NULL "
                  "AND TRIM(doi)<>'' GROUP BY doi HAVING COUNT(*)>1 ORDER BY COUNT(*) DESC LIMIT 3",
                  expect_rows=0)

    # 5 统计/账号/会话/知识库
    for t, label in (("hub_stats", "主页统计缓存"), ("users", "账号"), ("conversations", "会话"),
                     ("visit_events", "访客事件"), ("gene_annotation_audit", "注释审计")):
        if table_exists(db, t):
            probe(f"{label}（{t}）", f"SELECT 1 FROM {t} LIMIT 3", expect_rows=1)
    print("  （未出现的表表示本库没有，不影响功能）", flush=True)


def main() -> int:
    quick = "--quick" in sys.argv
    print("数据库体检 @ zebrafish_db（只读）\n" + "=" * 70, flush=True)
    try:
        db = _db()
    except Exception as e:  # noqa: BLE001
        rec("FAIL", "连接数据库", f"{type(e).__name__}: {e}")
        print("=" * 70)
        print("共 1 项：失败 1 → ['连接数据库']")
        return 1
    try:
        section_a_connection(db)
        section_b_tables(db)
        section_c_integrity(db)
        section_d_features(db, quick=quick)
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001
            pass
    n_fail = sum(1 for lv, _, _ in RESULTS if lv == "FAIL")
    n_warn = sum(1 for lv, _, _ in RESULTS if lv == "WARN")
    n_pass = sum(1 for lv, _, _ in RESULTS if lv == "PASS")
    print("=" * 70)
    print(f"共 {len(RESULTS)} 项：PASS {n_pass} · WARN {n_warn} · FAIL {n_fail}")
    if n_fail:
        print("FAIL 明细：")
        for lv, n, d in RESULTS:
            if lv == "FAIL":
                print(f"  - {n}: {d[:160]}")
    if n_warn:
        print("WARN 明细：")
        for lv, n, d in RESULTS:
            if lv == "WARN":
                print(f"  - {n}: {d[:160]}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
