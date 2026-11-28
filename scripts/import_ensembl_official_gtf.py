#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用 Ensembl 官方 GTF 重建 Ensembl release 的真实基因集（stable ID + 官方坐标）。

## 为什么需要

线上 Ensembl release 维度的 ID/坐标原先来自 MySQL ``ensembl_release_gene``
（ZFIN 派生的二手表）：33,966 行 ``unmapped``（zfin/current_id 全空）、
2,867 组同 release 内重复 symbol；且坐标是靠 hub（GRCz11/GRCz12tu）坐标做
UCSC chain **近似**，并不是该 release 参考基因组上的官方注释坐标。

用户要求「版本互转的答案真实准确」。Ensembl 官方 GTF 的 ``gene`` feature 行
恰好给出每个 release 在**自身参考基因组**上的真实 ENSDARG stable ID、官方
gene symbol 与精确坐标 —— 这是最权威、可直接核对的真值源。

## 数据源（Ensembl FTP 官方 GTF）

    release  assembly  GTF
    54       Zv8       https://ftp.ensembl.org/pub/release-54/gtf/danio_rerio/Danio_rerio.Zv8.54.gtf.gz
    77       Zv9       https://ftp.ensembl.org/pub/release-77/gtf/danio_rerio/Danio_rerio.Zv9.77.gtf.gz
    79       Zv9       https://ftp.ensembl.org/pub/release-79/gtf/danio_rerio/Danio_rerio.Zv9.79.gtf.gz
    80       GRCz10    https://ftp.ensembl.org/pub/release-80/gtf/danio_rerio/Danio_rerio.GRCz10.80.gtf.gz
    116      GRCz11    https://ftp.ensembl.org/pub/release-116/gtf/danio_rerio/Danio_rerio.GRCz11.116.gtf.gz

## 产出表

``ens_gene_official(release, assembly, ensembl_gene_id, symbol, chrom,
start, end, strand, biotype)``，索引 ``(release, ensembl_gene_id)`` 与
``(release, symbol)``。``ensembl_gene_id`` 去掉 ``.N`` 版本后缀。

落盘库与 ``prime_idmap_data.py`` 共用（默认 idmap_data.sqlite3）。
``database/ensembl_version_map.py`` 优先读这张官方表，读不到才回退 ZFIN 二手数据。

## 用法

    python scripts/import_ensembl_official_gtf.py                  # 解析 data/ensembl_gtf/*.gtf.gz
    python scripts/import_ensembl_official_gtf.py --db path/to.sqlite3
    python scripts/import_ensembl_official_gtf.py --gtf-dir /tmp/gtf
    python scripts/import_ensembl_official_gtf.py --status         # 只查看已落盘统计

GTF 缺失时可用 ``--download`` 先拉取（服务器/有外网的机器）。
"""
from __future__ import annotations

import argparse
import gzip
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DB = os.environ.get(
    "IDMAP_DATA_DB",
    "/data/zoujin/deng/zebrafish_runtime/idmap_data.sqlite3",
)

# release -> (assembly, gtf 文件名, FTP URL)
RELEASES = {
    54: ("Zv8", "Danio_rerio.Zv8.54.gtf.gz",
         "https://ftp.ensembl.org/pub/release-54/gtf/danio_rerio/Danio_rerio.Zv8.54.gtf.gz"),
    77: ("Zv9", "Danio_rerio.Zv9.77.gtf.gz",
         "https://ftp.ensembl.org/pub/release-77/gtf/danio_rerio/Danio_rerio.Zv9.77.gtf.gz"),
    79: ("Zv9", "Danio_rerio.Zv9.79.gtf.gz",
         "https://ftp.ensembl.org/pub/release-79/gtf/danio_rerio/Danio_rerio.Zv9.79.gtf.gz"),
    80: ("GRCz10", "Danio_rerio.GRCz10.80.gtf.gz",
         "https://ftp.ensembl.org/pub/release-80/gtf/danio_rerio/Danio_rerio.GRCz10.80.gtf.gz"),
    116: ("GRCz11", "Danio_rerio.GRCz11.116.gtf.gz",
          "https://ftp.ensembl.org/pub/release-116/gtf/danio_rerio/Danio_rerio.GRCz11.116.gtf.gz"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ens_gene_official (
  release INTEGER NOT NULL,
  assembly TEXT NOT NULL,
  ensembl_gene_id TEXT NOT NULL,
  symbol TEXT,
  chrom TEXT,
  start INTEGER,
  end INTEGER,
  strand TEXT,
  biotype TEXT,
  PRIMARY KEY (release, ensembl_gene_id)
);
CREATE INDEX IF NOT EXISTS ix_ego_rel_id  ON ens_gene_official(release, ensembl_gene_id);
CREATE INDEX IF NOT EXISTS ix_ego_rel_sym ON ens_gene_official(release, symbol);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# GTF 第 9 列属性：key "value"; 形式；兼容 value 内出现分号
_ATTR_RE = re.compile(r'(\w+)\s+"([^"]*)"')


def parse_attrs(col: str) -> dict:
    return {k: v for k, v in _ATTR_RE.findall(col)}


def _from_gene_rows(stream, release: int, assembly: str):
    """有 gene feature 的 GTF（r77/r79/r80/r116）：直接取 gene 行。"""
    seen: set = set()
    for line in stream:
        if not line or line[0] == "#":
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 9 or parts[2] != "gene":
            continue
        try:
            start, end = int(parts[3]), int(parts[4])
        except (TypeError, ValueError):
            continue
        attrs = parse_attrs(parts[8])
        gid = (attrs.get("gene_id") or "").split(".")[0].strip()
        if not gid or gid in seen:
            continue
        seen.add(gid)
        yield (release, assembly, gid,
               (attrs.get("gene_name") or "").strip(),
               parts[0].strip(), start, end, parts[6].strip(),
               (attrs.get("gene_biotype") or "").strip())


def _from_exon_rows(stream, release: int, assembly: str):
    """无 gene feature 的老 GTF（r54 / Zv8, 2009）：由官方 exon 聚合基因边界。

    Ensembl r54 的 GTF 只有 exon/CDS/codon 行，没有 gene/transcript 行。
    gene 的 start/end 定义为所有 transcript 的最外边界，对有 exon 的基因而言
    就是全部 exon 的 min(start) ~ max(end) —— 与官方 gene 边界一致。
    第 2 列在旧格式里是 biotype（如 protein_coding），不是 source。
    """
    agg: dict = {}  # gid -> [chrom, strand, min, max, symbol, biotype]
    for line in stream:
        if not line or line[0] == "#":
            continue
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 9 or parts[2] != "exon":
            continue
        attrs = parse_attrs(parts[8])
        gid = (attrs.get("gene_id") or "").split(".")[0].strip()
        if not gid:
            continue
        try:
            s, e = int(parts[3]), int(parts[4])
        except (TypeError, ValueError):
            continue
        chrom, strand = parts[0].strip(), parts[6].strip()
        sym = (attrs.get("gene_name") or "").strip()
        bio = (attrs.get("gene_biotype") or parts[1].strip())
        cur = agg.get(gid)
        if cur is None:
            agg[gid] = [chrom, strand, s, e, sym, bio]
        else:
            if s < cur[2]:
                cur[2] = s
            if e > cur[3]:
                cur[3] = e
            if not cur[4] and sym:
                cur[4] = sym
    for gid, v in agg.items():
        yield (release, assembly, gid, v[4], v[0], v[2], v[3], v[1], v[5])


def iter_gene_rows(gtf_path: Path, release: int, assembly: str):
    """流式产出基因行：优先 gene feature，缺失时从 exon 聚合（r54）。"""
    import itertools

    op = gzip.open if str(gtf_path).endswith(".gz") else open
    fh = op(gtf_path, "rt", encoding="utf-8", errors="replace")
    head: list = []
    has_gene = False
    try:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 9 and parts[2] == "gene":
                has_gene = True
                break
            head.append(line)
            if len(head) > 5000:
                break
        stream = itertools.chain(head, fh)
        if has_gene:
            yield from _from_gene_rows(stream, release, assembly)
        else:
            yield from _from_exon_rows(stream, release, assembly)
    finally:
        fh.close()


def import_one(conn: sqlite3.Connection, release: int, gtf_path: Path) -> int:
    assembly = RELEASES[release][0]
    conn.execute("DELETE FROM ens_gene_official WHERE release=?", (release,))
    n, batch = 0, []
    sql = ("INSERT OR REPLACE INTO ens_gene_official"
           "(release,assembly,ensembl_gene_id,symbol,chrom,start,end,strand,biotype)"
           " VALUES(?,?,?,?,?,?,?,?,?)")
    for row in iter_gene_rows(gtf_path, release, assembly):
        batch.append(row)
        n += 1
        if len(batch) >= 5000:
            conn.executemany(sql, batch)
            conn.commit()
            batch = []
    if batch:
        conn.executemany(sql, batch)
        conn.commit()
    return n


def status(conn: sqlite3.Connection) -> None:
    try:
        rows = conn.execute(
            "SELECT release, assembly, COUNT(*),"
            " SUM(CASE WHEN symbol IS NULL OR symbol='' THEN 1 ELSE 0 END)"
            " FROM ens_gene_official GROUP BY release, assembly ORDER BY release"
        ).fetchall()
    except sqlite3.OperationalError:
        print("ens_gene_official 表不存在（尚未导入）")
        return
    print("%-8s %-8s %10s %12s" % ("release", "assembly", "genes", "no_symbol"))
    total = 0
    for rel, asm, n, nosym in rows:
        print("%-8d %-8s %10d %12d" % (rel, asm, n, nosym or 0))
        total += n
    rng = conn.execute("SELECT MIN(release), MAX(release) FROM ens_gene_official").fetchone()
    idx = conn.execute(
        "SELECT MIN(start), MAX(end) FROM ens_gene_official").fetchone()
    print("total genes=%d  release range=%s  coord span=%s" % (total, rng, idx))


def download(gtf_dir: Path) -> None:
    gtf_dir.mkdir(parents=True, exist_ok=True)
    procs = []
    for rel, (_asm, fname, url) in RELEASES.items():
        dst = gtf_dir / fname
        if dst.is_file() and dst.stat().st_size > 1_000_000:
            print("[skip] %s 已存在 %d bytes" % (fname, dst.stat().st_size), flush=True)
            continue
        print("[get ] %s" % url, flush=True)
        procs.append(subprocess.Popen(["curl", "-m", "1800", "-sS", "-o", str(dst), url]))
    for p in procs:
        p.wait()
    print("[get ] 下载完成", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--gtf-dir", default=str(ROOT / "data" / "ensembl_gtf"))
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--release", type=int, default=0, help="只导入某个 release")
    args = ap.parse_args()

    gtf_dir = Path(args.gtf_dir)
    if args.download:
        download(gtf_dir)

    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)

    if args.status:
        status(conn)
        conn.close()
        return 0

    targets = [args.release] if args.release else sorted(RELEASES)
    t0 = time.time()
    for rel in targets:
        _asm, fname, _url = RELEASES[rel]
        p = gtf_dir / fname
        if not p.is_file():
            print("[SKIP] r%d %s 不存在（先 --download）" % (rel, p), flush=True)
            continue
        n = import_one(conn, rel, p)
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                     ("ens_official_generated_at", time.strftime("%Y-%m-%dT%H:%M:%S")))
        conn.commit()
        print("[ ok ] r%-3d %-8s %6d genes  %.0fs" %
              (rel, RELEASES[rel][0], n, time.time() - t0), flush=True)

    status(conn)
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
