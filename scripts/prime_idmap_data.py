#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量 ID 互转的离线预计算：全库基因 × 18 版本坐标 + Ensembl release 基因集。

## 为什么需要

2026-11-13 实测：用户在 Liftover「批量 ID 互转」里选任一非 GRCz11/GRCz12tu
的版本，首次请求要现场把 UCSC chain 加载进**单进程 uvicorn**（常驻 18.8 GB），
GRCz12ab 126.9 s、Zv8 32.4 s、GRCz10 20.9 s，累积到 Zv6 直接把进程打挂、
由守护重启。转换算法本身是对的（324 组清查全通过），问题在坐标换算的链加载。

本脚本把「坐标换算」整体搬到**离线独立进程**：按目标版本逐个加载链、算完全库
基因就清链缓存 + malloc_trim 归还内存，结果落盘 SQLite。线上 `_coords_for()`
改查这张表，永不现场加载链 → 任意版本组合毫秒级，服务不再被打挂。

同时把 `ensembl_release_gene`（4 个真实 Ensembl release：54/77/79/80）与
`ensembl_id_version`（r116 当前集）导出到同一个库，供 Ensembl 版本间 ID 转换用。

## 用法（服务器）

    PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
    $PY scripts/prime_idmap_data.py                 # 全量（约 10–30 分钟）
    $PY scripts/prime_idmap_data.py --coords-only  # 只算坐标（P0 根治）
    $PY scripts/prime_idmap_data.py --ensembl-only # 只导 Ensembl 数据
    $PY scripts/prime_idmap_data.py --status       # 查看落盘进度

断点续跑：已完成的 target 记在 meta 表，中断后重跑自动跳过。
产物路径可用环境变量 IDMAP_DATA_DB 覆盖。
"""
from __future__ import annotations

import ctypes
import gc
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "database"), str(ROOT / "core"), str(ROOT)]

DB = Path(os.environ.get(
    "IDMAP_DATA_DB",
    "/data/zoujin/deng/zebrafish_runtime/idmap_data.sqlite3"))

import coord_liftover as cl  # noqa: E402
import id_version_map as ivm  # noqa: E402
from mysql_client import MySQLClient  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS coords (
  hub TEXT NOT NULL, chrom TEXT NOT NULL, start INTEGER NOT NULL,
  end INTEGER NOT NULL, strand TEXT NOT NULL, target TEXT NOT NULL,
  ok INTEGER NOT NULL, code TEXT NOT NULL, span TEXT NOT NULL,
  PRIMARY KEY (hub, chrom, start, end, strand, target)
);
CREATE TABLE IF NOT EXISTS ens_release (
  release INTEGER NOT NULL, assembly TEXT NOT NULL,
  ensembl_gene_id TEXT NOT NULL, gene_symbol TEXT, zfin_id TEXT,
  current_symbol TEXT, current_ensembl_gene_id TEXT, match_method TEXT
);
CREATE INDEX IF NOT EXISTS ix_ens_rel_id ON ens_release(release, ensembl_gene_id);
CREATE INDEX IF NOT EXISTS ix_ens_rel_zfin ON ens_release(release, zfin_id);
CREATE INDEX IF NOT EXISTS ix_ens_rel_sym ON ens_release(release, gene_symbol);
CREATE TABLE IF NOT EXISTS ens_current (
  ensembl_gene_id TEXT PRIMARY KEY, gene_symbol TEXT, zfin_id TEXT,
  gene_id INTEGER, version_num INTEGER
);
CREATE INDEX IF NOT EXISTS ix_ens_cur_zfin ON ens_current(zfin_id);
CREATE INDEX IF NOT EXISTS ix_ens_cur_sym ON ens_current(gene_symbol);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def release_chain_memory():
    try:
        cl._loader.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001
        pass


def rss_mb():
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except Exception:  # noqa: BLE001
        pass
    return -1


def meta_get(conn, key, default=""):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def meta_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))
    conn.commit()


def collect_hub_coords(conn=None):
    """两类 hub 坐标：GRCz12tu 主表 / GRCz11 官方 xref，去重后返回。

    断点续跑时优先从已落盘的 coords 表还原坐标集合 —— 中断重跑不必再打
    MySQL（线上 uvicorn 线程多，MySQL max_connections 容易瞬时打满）。
    """
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT DISTINCT hub, chrom, start, end, strand FROM coords").fetchall()
            if len(rows) >= 10000:
                genes = sorted({(str(r[0]), str(r[1]), int(r[2]), int(r[3]), str(r[4]))
                                for r in rows})
                print("[coords] 从落盘表还原 %d 条 hub 坐标（零 MySQL 连接）" %
                      len(genes), flush=True)
                return genes
        except Exception:  # noqa: BLE001
            pass
    c = MySQLClient()
    out = set()
    r = c.execute_sql(
        "SELECT chrom, start_pos, end_pos, strand FROM gene_master_grcz12tu "
        "WHERE chrom IS NOT NULL AND start_pos IS NOT NULL AND end_pos IS NOT NULL",
        max_rows=0)
    for x in r.get("data") or []:
        try:
            out.add(("GRCz12tu", str(x["chrom"]), int(x["start_pos"]),
                     int(x["end_pos"]), str(x.get("strand") or "+")))
        except (TypeError, ValueError, KeyError):
            continue
    n12 = len(out)
    r = c.execute_sql(
        "SELECT chromosome, start_pos, end_pos, strand FROM gene_assembly_xref "
        "WHERE assembly_name='GRCz11' AND chromosome IS NOT NULL "
        "AND start_pos IS NOT NULL AND end_pos IS NOT NULL", max_rows=0)
    for x in r.get("data") or []:
        try:
            out.add(("GRCz11", str(x["chromosome"]), int(x["start_pos"]),
                     int(x["end_pos"]), str(x.get("strand") or "+")))
        except (TypeError, ValueError, KeyError):
            continue
    genes = sorted(out)
    print("[coords] hub=G12tu %d 条，+G11 %d 条，去重后 %d 条" %
          (n12, len(out) - n12, len(genes)), flush=True)
    return genes


def fmt_span(chrom, start, end, strand=""):
    if not chrom or start is None or end is None:
        return ""
    return "%s:%s-%s%s" % (chrom, format(int(start), ","), format(int(end), ","),
                           "(%s)" % strand if strand in ("+", "-") else "")


def prime_coords(conn, genes, builds):
    """按目标版本分批：加载链 → 全库换算 → 落盘 → 释放链内存。"""
    done = set(json.loads(meta_get(conn, "coords_done_builds", "[]")))
    # resolve_route 每次都重建「链文件是否存在」字典并 BFS，99 万次会白烧几千万次
    # stat；路由只跟 (hub,target) 有关，这里按对缓存。
    orig_route = cl.resolve_route
    route_cache = {}

    def cached_route(src, tgt):
        k = (src, tgt)
        if k not in route_cache:
            route_cache[k] = orig_route(src, tgt)
        return route_cache[k]

    cl.resolve_route = cached_route
    t0 = time.time()
    total_written = 0
    for i, target in enumerate(builds, 1):
        if target in done:
            print("[coords] %2d/%d %-16s 已完成，跳过" % (i, len(builds), target), flush=True)
            continue
        batch = []
        n_ok = 0
        for hub, chrom, start, end, strand in genes:
            if hub == target:      # 本版官方坐标由 _coords_for 的 native 分支直出
                continue
            d = cl.lift_region(hub, chrom, start, end, strand, target)
            ok = bool(d.get("ok"))
            span = fmt_span(d.get("chromosome"), d.get("start"), d.get("end"),
                            d.get("strand")) if ok else ""
            code = str(d.get("code") or ("liftover_ok" if ok else "liftover_failed"))
            batch.append((hub, chrom, start, end, strand, target,
                          1 if ok else 0, code, span))
            n_ok += 1 if ok else 0
            if len(batch) >= 5000:
                conn.executemany("INSERT OR REPLACE INTO coords VALUES(?,?,?,?,?,?,?,?,?)", batch)
                conn.commit()
                total_written += len(batch)
                batch = []
        if batch:
            conn.executemany("INSERT OR REPLACE INTO coords VALUES(?,?,?,?,?,?,?,?,?)", batch)
            total_written += len(batch)
            batch = []
        done.add(target)
        meta_set(conn, "coords_done_builds", json.dumps(sorted(done)))
        conn.commit()
        print("[coords] %2d/%d %-16s 写入 %d 条，命中 %d，累计 %d，rss=%dMB，%.0fs" %
              (i, len(builds), target, len(genes), n_ok, total_written,
               rss_mb(), time.time() - t0), flush=True)
        route_cache.clear()
        release_chain_memory()
    cl.resolve_route = orig_route
    meta_set(conn, "coords_generated_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    meta_set(conn, "coords_hub_genes", str(len(genes)))
    print("[coords] 完成：%d 条坐标记录，%.0fs" % (total_written, time.time() - t0), flush=True)


def export_ensembl(conn):
    """导出 4 个历史 Ensembl release 基因集 + r116 当前集。"""
    c = MySQLClient()
    t0 = time.time()
    conn.execute("DELETE FROM ens_release")
    r = c.execute_sql(
        "SELECT ensembl_gene_id, ensembl_release, assembly_name, gene_symbol, zfin_id, "
        "current_symbol, current_ensembl_gene_id, match_method FROM ensembl_release_gene",
        max_rows=0)
    rows = []
    for x in r.get("data") or []:
        rows.append((int(x["ensembl_release"]), str(x.get("assembly_name") or ""),
                     str(x.get("ensembl_gene_id") or ""), str(x.get("gene_symbol") or ""),
                     str(x.get("zfin_id") or ""), str(x.get("current_symbol") or ""),
                     str(x.get("current_ensembl_gene_id") or ""),
                     str(x.get("match_method") or "")))
        if len(rows) >= 5000:
            conn.executemany("INSERT INTO ens_release VALUES(?,?,?,?,?,?,?,?)", rows)
            rows = []
    if rows:
        conn.executemany("INSERT INTO ens_release VALUES(?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    n_hist = conn.execute("SELECT COUNT(*) FROM ens_release").fetchone()[0]
    rels = [r[0] for r in conn.execute(
        "SELECT DISTINCT release FROM ens_release ORDER BY release").fetchall()]
    print("[ensembl] 历史基因集 %d 行，release=%s" % (n_hist, rels), flush=True)

    conn.execute("DELETE FROM ens_current")
    r = c.execute_sql(
        "SELECT b.gene_id, b.gene_name, b.ensembl_id, b.zfin_id, v.version_num "
        "FROM gene_basic b LEFT JOIN ensembl_id_version v "
        "ON v.id_base = b.ensembl_id WHERE b.ensembl_id IS NOT NULL AND b.ensembl_id <> ''",
        max_rows=0)
    seen, rows = set(), []
    for x in r.get("data") or []:
        eid = str(x.get("ensembl_id") or "").split(".")[0].strip()
        if not eid or eid in seen:
            continue
        seen.add(eid)
        try:
            vn = int(x.get("version_num") or 0)
        except (TypeError, ValueError):
            vn = 0
        rows.append((eid, str(x.get("gene_name") or ""), str(x.get("zfin_id") or ""),
                     int(x.get("gene_id") or 0), vn))
        if len(rows) >= 5000:
            conn.executemany("INSERT OR REPLACE INTO ens_current VALUES(?,?,?,?,?)", rows)
            rows = []
    if rows:
        conn.executemany("INSERT OR REPLACE INTO ens_current VALUES(?,?,?,?,?)", rows)
    conn.commit()
    n_cur = conn.execute("SELECT COUNT(*) FROM ens_current").fetchone()[0]
    print("[ensembl] r116 当前集 %d 行，%.0fs" % (n_cur, time.time() - t0), flush=True)
    meta_set(conn, "ensembl_releases", json.dumps(rels))
    meta_set(conn, "ensembl_current_release", "116")
    meta_set(conn, "ensembl_generated_at", time.strftime("%Y-%m-%dT%H:%M:%S"))


def status(conn):
    print("DB:", DB, "exists=", DB.is_file(), flush=True)
    for t in ("coords", "ens_release", "ens_current"):
        try:
            print("  %-12s %d 行" % (t, conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]),
                  flush=True)
        except Exception as e:  # noqa: BLE001
            print("  %-12s ERR %s" % (t, e), flush=True)
    print("  coords_done_builds =", meta_get(conn, "coords_done_builds", "[]"), flush=True)
    print("  coords_hub_genes   =", meta_get(conn, "coords_hub_genes", "-"), flush=True)
    print("  ensembl_releases   =", meta_get(conn, "ensembl_releases", "[]"), flush=True)
    print("  generated_at       =", meta_get(conn, "coords_generated_at", "-"), flush=True)


def main():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    if "--status" in sys.argv:
        status(conn)
        return 0
    builds = list(ivm.SUPPORTED_BUILDS)
    print("[init] DB=%s builds=%d" % (DB, len(builds)), flush=True)
    if "--ensembl-only" not in sys.argv:
        genes = collect_hub_coords(conn)
        prime_coords(conn, genes, builds)
    if "--coords-only" not in sys.argv:
        export_ensembl(conn)
    status(conn)
    # 落盘库交给线上「只读」连接使用：先 checkpoint，再退回单文件 journal，
    # 免得 mode=ro 的读者还要依赖 -wal / -shm 文件与目录写权限。
    conn.commit()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    except Exception:  # noqa: BLE001
        pass
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
