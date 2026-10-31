# -*- coding: utf-8 -*-
"""把 RefSeq GCF_049306965.2（GRCz12tu）的**全量**基因导入 grcz12tu_gene，并把装配版本同步到 .2。

为什么单独一张表
----------------
gene_assembly_xref 带外键 gene_id -> gene_basic(gene_id)，只能装得下「能映射到本地主表」的基因，
所以 GRCz12tu 官方 5 万多条基因里只有约 2.2 万条进得去。本脚本写入的 grcz12tu_gene 不受该外键
约束，承载 RefSeq 注释的全量基因；能映射到 gene_basic 的，回填 gene_id 便于联合查询。

本脚本做什么
------------
1. 建表 grcz12tu_gene（sql/grcz12tu_full_migration.sql）。
2. 解析 RefSeq feature_table（.2）全部 gene 行（Primary Assembly，排除 alt/patch/fix），全量写入。
3. 回填 gene_id：gene_xref(ncbi_gene) / gene_assembly_xref(GRCz12tu) 里已有 GeneID -> gene_id 的对应。
4. 同步装配版本：assembly_registry 里 GRCz12tu 的 refseq/genbank 改成 .2；
   gene_assembly_xref 的 source 从 refseq_GCF_049306965.1 改写成 refseq_GCF_049306965.2。
5. 自校验并打印统计。

用法
----
python scripts/import_grcz12tu_full.py                # 正常导入
python scripts/import_grcz12tu_full.py --dry-run      # 只解析、不写库
"""
from __future__ import annotations

import argparse
import gzip
import os
import sys
from typing import Dict, Iterator, List, Optional

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print  # noqa: E402
from config import (  # noqa: E402
    _BASE_DIR,
    NCBI_REFERENCE_ASSEMBLY,
    NCBI_REFERENCE_GENBANK,
    NCBI_REFERENCE_REFSEQ,
)

ASM = NCBI_REFERENCE_REFSEQ          # GCF_049306965.2
SOURCE = f"refseq_{ASM}"             # refseq_GCF_049306965.2
OLD_SOURCE = "refseq_GCF_049306965.1"
FTP_ROOTS = (
    "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/049/306/965/",
    "https://ftp.ncbi.nih.gov/genomes/all/GCF/049/306/965/",
)
ASM_DIR = f"{ASM}_{NCBI_REFERENCE_ASSEMBLY}"
FEATURE_NAME = f"{ASM_DIR}_feature_table.txt.gz"
CACHE_DIR = os.environ.get(
    "NCBI_CACHE_DIR",
    os.path.join(os.environ.get("ZEBRA_RUNTIME", os.path.join(_BASE_DIR, "data")), "ncbi_cache"),
)
FEATURE_CACHE = os.path.join(CACHE_DIR, FEATURE_NAME)
TSV_CACHE = os.path.join(CACHE_DIR, f"{ASM_DIR}_genes_full.tsv")
BATCH = 800


def _client():
    from mysql_client import get_mysql_client
    return get_mysql_client()


def _esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("'", "''")


def _v(s: Optional[str], maxlen: int = 0) -> str:
    t = (s or "").strip()
    if not t:
        return "NULL"
    if maxlen and len(t) > maxlen:
        t = t[:maxlen]
    return "'" + _esc(t) + "'"


def _n(s: Optional[str]) -> str:
    t = (s or "").strip()
    return t if t.isdigit() else "NULL"


def _download(url: str, dest: str, min_bytes: int) -> str:
    import ssl
    import urllib.request
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest) and os.path.getsize(dest) >= min_bytes:
        safe_print(f"  缓存 {os.path.basename(dest)} {os.path.getsize(dest) // 1024} KB")
        return dest
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
    with urllib.request.urlopen(req, timeout=600, context=ctx) as resp, open(dest + ".part", "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    os.replace(dest + ".part", dest)
    safe_print(f"  下载 {os.path.basename(dest)} {os.path.getsize(dest) // 1024} KB")
    return dest


def _iter_genes(path: str) -> Iterator[dict]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        header: Optional[List[str]] = None
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith("#"):
                header = [h.strip().lstrip("#").strip() for h in line.split("\t")]
                continue
            cols = line.split("\t")
            if not header:
                continue
            rec = {header[i]: (cols[i] if i < len(cols) else "") for i in range(len(header))}
            if (rec.get("feature") or "").strip().lower() != "gene":
                continue
            unit = (rec.get("assembly_unit") or "").strip().lower()
            if any(k in unit for k in ("alt", "patch", "fix")):
                continue
            gid = (rec.get("GeneID") or "").strip()
            sym = (rec.get("symbol") or "").strip() or (rec.get("locus_tag") or "").strip()
            if not gid and not sym:
                continue
            yield {
                "ncbi_gene_id": gid,
                "gene_symbol": (rec.get("symbol") or "").strip(),
                "locus_tag": (rec.get("locus_tag") or "").strip(),
                "gene_biotype": (rec.get("class") or "").strip(),
                "chromosome": (rec.get("chromosome") or "").strip(),
                "genomic_accession": (rec.get("genomic_accession") or "").strip(),
                "start_pos": (rec.get("start") or "").strip(),
                "end_pos": (rec.get("end") or "").strip(),
                "strand": (rec.get("strand") or "").strip()[:1],
                "assembly_unit": (rec.get("assembly_unit") or "").strip(),
                "seq_type": (rec.get("seq_type") or "").strip(),
                "product_accession": (rec.get("product_accession") or "").strip(),
            }


def _ensure_table() -> bool:
    path = os.path.join(_BASE_DIR, "sql", "grcz12tu_full_migration.sql")
    if not os.path.isfile(path):
        safe_print(f"[错误] 缺少 {path}")
        return False
    ddl = open(path, encoding="utf-8").read()
    stmts = [s.strip() for s in ddl.split(";") if s.strip() and not s.strip().startswith("--")]
    db = next(_client().get_db())
    try:
        from sqlalchemy import text
        for stmt in stmts:
            if stmt.upper().startswith("USE "):
                continue
            db.execute(text(stmt))
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        safe_print(f"[错误] 建表失败：{e}")
        return False
    finally:
        db.close()


def _migrate_constraints() -> None:
    """旧表兼容：ncbi_gene_id 允许空；唯一键从「GeneID」改为「GeneID+染色体+起点」，
    否则 split gene 的多段会被 ON DUPLICATE 覆盖掉（少 154 行）。"""
    try:
        _client().execute_write(
            "ALTER TABLE grcz12tu_gene MODIFY ncbi_gene_id VARCHAR(16) NULL "
            "COMMENT 'NCBI GeneID（个别位点无 GeneID，留 NULL）'"
        )
    except Exception as e:
        safe_print(f"  [提示] 放宽 GeneID 跳过：{e}")
    try:
        _client().execute_write("ALTER TABLE grcz12tu_gene DROP INDEX uk_geneid")
        safe_print("  已删除旧唯一键 uk_geneid")
    except Exception:
        pass
    try:
        _client().execute_write(
            "ALTER TABLE grcz12tu_gene ADD UNIQUE KEY uk_gene_segment (ncbi_gene_id, chromosome, start_pos)"
        )
        safe_print("  已建片段级唯一键 uk_gene_segment")
    except Exception as e:
        safe_print(f"  [提示] 建片段键跳过：{e}")


def _gene_id_by_ncbi() -> Dict[str, int]:
    """GeneID -> 本地 gene_id（来自已有对照表，用于回填）。"""
    from sqlalchemy import text
    out: Dict[str, int] = {}
    db = next(_client().get_db())
    try:
        for r in db.execute(text(
            "SELECT ncbi_gene_id, gene_id FROM gene_assembly_xref "
            "WHERE assembly_name=:a AND ncbi_gene_id IS NOT NULL AND ncbi_gene_id<>''"
        ), {"a": NCBI_REFERENCE_ASSEMBLY}).mappings():
            gid = str(r["ncbi_gene_id"]).strip()
            if gid and gid not in out:
                out[gid] = int(r["gene_id"])
        n1 = len(out)
        for r in db.execute(text(
            "SELECT db_id, gene_id FROM gene_xref WHERE db_name='ncbi_gene' "
            "AND db_id IS NOT NULL AND db_id<>''"
        )).mappings():
            gid = str(r["db_id"]).strip()
            if gid and gid not in out:
                out[gid] = int(r["gene_id"])
        safe_print(f"  GeneID→gene_id 索引 {n1}（xref）+{len(out)-n1}（gene_xref）= {len(out)}")
    finally:
        db.close()
    return out


def _write_genes(genes: List[dict], idmap: Dict[str, int], dry: bool) -> int:
    cols = ("ncbi_gene_id, gene_symbol, locus_tag, gene_biotype, chromosome, genomic_accession, "
            "start_pos, end_pos, strand, assembly_unit, seq_type, product_accession, gene_id, source")
    dup = "ON DUPLICATE KEY UPDATE gene_symbol=VALUES(gene_symbol), locus_tag=VALUES(locus_tag), " \
          "gene_biotype=VALUES(gene_biotype), chromosome=VALUES(chromosome), " \
          "genomic_accession=VALUES(genomic_accession), start_pos=VALUES(start_pos), " \
          "end_pos=VALUES(end_pos), strand=VALUES(strand), assembly_unit=VALUES(assembly_unit), " \
          "seq_type=VALUES(seq_type), product_accession=VALUES(product_accession), " \
          "gene_id=VALUES(gene_id), source=VALUES(source)"
    buf: List[str] = []
    written = 0
    mapped = 0
    no_gid = 0

    def flush() -> None:
        nonlocal written
        if not buf:
            return
        sql = f"INSERT INTO grcz12tu_gene ({cols}) VALUES " + ",".join(buf) + " " + dup
        _client().execute_write(sql.replace(":", r"\:"))
        written += len(buf)
        buf.clear()

    for rec in genes:
        gid = rec["ncbi_gene_id"]
        if not gid:
            # 无 GeneID 的位点（如部分 tRNA/其它特征）也全量收录，GeneID 留 NULL
            no_gid += 1
        local = idmap.get(gid) if gid else None
        if local:
            mapped += 1
        buf.append(
            "(" + ",".join([
                _v(gid, 16),
                _v(rec["gene_symbol"], 128),
                _v(rec["locus_tag"], 64),
                _v(rec["gene_biotype"], 64),
                _v(rec["chromosome"], 16),
                _v(rec["genomic_accession"], 32),
                _n(rec["start_pos"]),
                _n(rec["end_pos"]),
                _v(rec["strand"], 1),
                _v(rec["assembly_unit"], 32),
                _v(rec["seq_type"], 32),
                _v(rec["product_accession"], 64),
                str(local) if local else "NULL",
                _v(SOURCE, 32),
            ]) + ")"
        )
        if len(buf) >= BATCH:
            if dry:
                written += len(buf)
                buf.clear()
            else:
                flush()
    if dry:
        written += len(buf)
        buf.clear()
    else:
        flush()
    safe_print(f"  写入 {written} 行（能映射到 gene_basic 的 {mapped}；无 GeneID 的位点 {no_gid}）")
    return written


def _sync_versions(dry: bool) -> None:
    """装配版本同步到 .2：注册表 + 已有对照的 source 标记。"""
    from sqlalchemy import text
    db = next(_client().get_db())
    try:
        reg = db.execute(text(
            "SELECT assembly_name, ncbi_refseq_accession FROM assembly_registry WHERE assembly_name=:a"
        ), {"a": NCBI_REFERENCE_ASSEMBLY}).mappings().first()
        safe_print(f"  注册表现状：{dict(reg) if reg else '（无 GRCz12tu 行）'}")
        if dry:
            return
        db.execute(text(
            "UPDATE assembly_registry SET ncbi_refseq_accession=:r, ncbi_genbank_accession=:g, "
            "is_ncbi_reference=1, notes=:n WHERE assembly_name=:a"
        ), {
            "r": NCBI_REFERENCE_REFSEQ,
            "g": NCBI_REFERENCE_GENBANK,
            "n": f"标准名来源 RefSeq {NCBI_REFERENCE_REFSEQ} "
                 f"https://www.ncbi.nlm.nih.gov/datasets/genome/{NCBI_REFERENCE_REFSEQ}/；"
                 f"全量基因位点见 grcz12tu_gene",
            "a": NCBI_REFERENCE_ASSEMBLY,
        })
        n = db.execute(text(
            "UPDATE gene_assembly_xref SET source=:new WHERE assembly_name=:a AND source=:old"
        ), {"new": SOURCE, "a": NCBI_REFERENCE_ASSEMBLY, "old": OLD_SOURCE}).rowcount or 0
        db.commit()
        safe_print(f"  注册表已改为 {NCBI_REFERENCE_REFSEQ}；对照表 source 改写 {n} 行")
    finally:
        db.close()


def _verify() -> None:
    from sqlalchemy import text
    db = next(_client().get_db())
    try:
        total = db.execute(text("SELECT COUNT(*) FROM grcz12tu_gene")).scalar() or 0
        src = db.execute(text("SELECT COUNT(*) FROM grcz12tu_gene WHERE source=:s"), {"s": SOURCE}).scalar() or 0
        mapped = db.execute(text("SELECT COUNT(*) FROM grcz12tu_gene WHERE gene_id IS NOT NULL")).scalar() or 0
        chroms = db.execute(text("SELECT COUNT(DISTINCT chromosome) FROM grcz12tu_gene")).scalar() or 0
        pc = db.execute(text(
            "SELECT COUNT(*) FROM grcz12tu_gene WHERE gene_biotype='protein_coding'")).scalar() or 0
        x12 = db.execute(text(
            "SELECT COUNT(*) FROM gene_assembly_xref WHERE assembly_name=:a"),
            {"a": NCBI_REFERENCE_ASSEMBLY}).scalar() or 0
        reg = db.execute(text(
            "SELECT ncbi_refseq_accession, ncbi_genbank_accession FROM assembly_registry WHERE assembly_name=:a"
        ), {"a": NCBI_REFERENCE_ASSEMBLY}).mappings().first()
        rows = [dict(r) for r in db.execute(text(
            "SELECT gene_symbol, ncbi_gene_id, chromosome, start_pos, end_pos FROM grcz12tu_gene "
            "WHERE gene_symbol IN ('tp53','nanog','shha','hif1aa','vegfaa','tbxta') "
            "ORDER BY gene_symbol"
        )).mappings()]
    finally:
        db.close()
    safe_print("-" * 60)
    safe_print(f"[完成] grcz12tu_gene 全量 {total} 行 | source={SOURCE} {src} | 可映射 gene_basic {mapped} "
               f"| 染色体 {chroms} | 蛋白编码 {pc}")
    safe_print(f"  gene_assembly_xref（GRCz12tu 对照）{x12} 行 | 注册表 {dict(reg) if reg else None}")
    for r in rows:
        safe_print(f"  例：{r['gene_symbol']} GeneID {r['ncbi_gene_id']} chr{r['chromosome']}:"
                   f"{r['start_pos']}-{r['end_pos']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只解析不写库")
    args = ap.parse_args()

    safe_print("=" * 60)
    safe_print(f"  GRCz12tu 全量导入 · RefSeq {ASM}（{NCBI_REFERENCE_ASSEMBLY}）")
    safe_print("=" * 60)

    feat = _download(FTP_ROOTS[0] + ASM_DIR + "/" + FEATURE_NAME, FEATURE_CACHE, 1_000_000)
    genes = list(_iter_genes(feat))
    safe_print(f"  解析 gene 行 {len(genes)}（Primary Assembly）")
    if len(genes) < 30000:
        safe_print(f"[错误] 基因数过少（{len(genes)}），拒绝导入")
        return 1

    if args.dry_run:
        idmap: Dict[str, int] = {} if args.dry_run else {}
        safe_print("  dry-run：跳过建表/写库/版本同步")
        _write_genes(genes, idmap, dry=True)
        return 0

    if not _ensure_table():
        return 1
    _migrate_constraints()
    idmap = _gene_id_by_ncbi()
    _write_genes(genes, idmap, dry=False)
    _sync_versions(dry=False)
    _verify()
    return 0


if __name__ == "__main__":
    sys.exit(main())
