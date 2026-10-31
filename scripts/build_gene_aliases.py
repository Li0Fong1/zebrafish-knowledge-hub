# -*- coding: utf-8 -*-
"""
批量构建基因别名与跨库 ID 映射表。

数据源：
  - NCBI Gene: Danio_rerio.gene_info.gz（taxon 7955）
  - NCBI gene2ensembl.gz
  - 本地 gene_basic（Ensembl GRCz11）
  - 手工常见别名

用法：python -m scripts.build_gene_aliases
"""
from core.entry import bootstrap_app

bootstrap_app()

import gzip
import os
import re
import urllib.request
from typing import Dict, Iterable, List, Optional, Set, Tuple

from common_utils import logger, safe_print
from config import _BASE_DIR, REFERENCE_ASSEMBLY, TAXON_ID
from gene_id_mapper import KNOWN_ALIASES, ensure_alias_tables

NCBI_GENE_INFO = (
    "https://ftp.ncbi.nlm.nih.gov/gene/DATA/GENE_INFO/"
    "Non-mammalian_vertebrates/Danio_rerio.gene_info.gz"
)
NCBI_GENE2ENSEMBL = "https://ftp.ncbi.nlm.nih.gov/gene/DATA/gene2ensembl.gz"
BATCH = 500
NCBI_CACHE_DIR = os.path.join(_BASE_DIR, "data", "ncbi_cache")


def _client():
    from mysql_client import get_mysql_client
    return get_mysql_client()


def _esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("'", "''")


def _download_gz(url: str) -> Iterable[str]:
    os.makedirs(NCBI_CACHE_DIR, exist_ok=True)
    name = url.rsplit("/", 1)[-1]
    cache_path = os.path.join(NCBI_CACHE_DIR, name)
    if not os.path.isfile(cache_path) or os.path.getsize(cache_path) < 1000:
        safe_print(f"  下载 {url} …")
        req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read()
        with open(cache_path, "wb") as f:
            f.write(raw)
        safe_print(f"  已缓存 {name}（{len(raw) // 1024} KB）")
    else:
        safe_print(f"  使用缓存 {name}")
    with gzip.open(cache_path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            yield line.rstrip("\n")


def _load_gene_basic_maps() -> Tuple[Dict[str, int], Dict[str, int], Dict[str, int]]:
    """name/symbol/ensembl -> gene_id"""
    from sqlalchemy import text
    client = _client()
    db = next(client.get_db())
    by_name: Dict[str, int] = {}
    by_symbol: Dict[str, int] = {}
    by_ensembl: Dict[str, int] = {}
    try:
        rows = db.execute(text(
            "SELECT gene_id, gene_name, gene_symbol, ensembl_id FROM gene_basic"
        )).mappings()
        for r in rows:
            gid = r["gene_id"]
            if r["gene_name"]:
                by_name[r["gene_name"].lower()] = gid
            if r["gene_symbol"]:
                by_symbol[r["gene_symbol"].lower()] = gid
            if r["ensembl_id"]:
                base = re.sub(r"\.\d+$", "", r["ensembl_id"])
                by_ensembl[r["ensembl_id"]] = gid
                by_ensembl[base] = gid
    finally:
        db.close()
    return by_name, by_symbol, by_ensembl


def _match_gene_id(
    symbol: str,
    dbxrefs: str,
    by_name: Dict[str, int],
    by_symbol: Dict[str, int],
    by_ensembl: Dict[str, int],
    gene2ensembl: Dict[str, str],
    ncbi_gene_id: str,
) -> Optional[int]:
    sym = (symbol or "").strip().lower()
    if sym and sym in by_name:
        return by_name[sym]
    if sym and sym in by_symbol:
        return by_symbol[sym]

    ens = gene2ensembl.get(ncbi_gene_id)
    if ens:
        base = re.sub(r"\.\d+$", "", ens)
        if ens in by_ensembl:
            return by_ensembl[ens]
        if base in by_ensembl:
            return by_ensembl[base]

    if dbxrefs:
        for part in _split_dbxref_parts(dbxrefs):
            if part.startswith("Ensembl:"):
                eid = part.split(":", 1)[1].strip()
                base = re.sub(r"\.\d+$", "", eid)
                if eid in by_ensembl:
                    return by_ensembl[eid]
                if base in by_ensembl:
                    return by_ensembl[base]
    return None


def _split_dbxref_parts(dbxrefs: str) -> List[str]:
    """NCBI dbXrefs 以 | 分隔，部分来源用逗号。"""
    parts = []
    for chunk in re.split(r"[|,]", dbxrefs or ""):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


def _parse_dbxrefs(dbxrefs: str) -> List[Tuple[str, str]]:
    out = []
    seen: Set[Tuple[str, str]] = set()
    if not dbxrefs:
        return out

    def _add(db_name: str, db_id: str):
        db_id = (db_id or "").strip().split("|")[0].strip()
        if not db_id or "|" in db_id or len(db_id) > 64:
            return
        key = (db_name, db_id)
        if key not in seen:
            seen.add(key)
            out.append(key)

    for part in _split_dbxref_parts(dbxrefs):
        low = part.lower()
        # AllianceGenome:ZFIN:ZDB-GENE-xxx
        if low.startswith("alliancegenome:") and "zfin:" in low:
            zid = part.split("ZFIN:", 1)[-1].split(":", 1)[-1].strip()
            if zid.upper().startswith("ZDB-GENE-"):
                _add("zfin", zid)
            continue
        if ":" not in part:
            continue
        src, val = part.split(":", 1)
        src = src.strip().lower()
        val = val.strip().split("|")[0].strip()
        db_name = None
        if src == "ensembl":
            db_name = "ensembl"
        elif src == "zfin":
            db_name = "zfin"
        elif src in ("refseq", "refseqmrna", "refseq_mrna"):
            db_name = "refseq_mrna"
        elif src in ("refseqprotein", "refseq_protein"):
            db_name = "refseq_protein"
        elif src in ("uniprotkb", "uniprot"):
            db_name = "uniprot"
        if db_name:
            _add(db_name, val)
    return out


def _flush_aliases(rows: List[str], stats: dict):
    if not rows:
        return
    sql = (
        "INSERT IGNORE INTO gene_alias (gene_id, alias, alias_type, source, is_primary) VALUES "
        + ", ".join(rows)
    )
    r = _client().execute_write(sql)
    if r["success"]:
        stats["alias_rows"] += len(rows)


def _flush_xrefs(rows: List[str], stats: dict):
    if not rows:
        return
    sql = (
        "INSERT IGNORE INTO gene_xref (gene_id, db_name, db_id, source) VALUES "
        + ", ".join(rows)
    )
    r = _client().execute_write(sql)
    if r["success"]:
        stats["xref_rows"] += len(rows)


def _load_gene2ensembl() -> Dict[str, str]:
    """NCBI GeneID -> Ensembl gene id"""
    mapping: Dict[str, str] = {}
    for line in _download_gz(NCBI_GENE2ENSEMBL):
        if line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        if cols[0] != str(TAXON_ID):
            continue
        gene_id, ens = cols[1], cols[2]
        if ens:
            mapping[gene_id] = ens
    safe_print(f"  gene2ensembl 映射：{len(mapping)} 条")
    return mapping


def build_ensembl_versions(clear: bool = False):
    """从 gene_basic + gene_xref 构建 Ensembl ID 版本映射表。"""
    from sqlalchemy import text
    from config import ENSEMBL_RELEASE

    ensure_alias_tables()
    client = _client()
    db = next(client.get_db())
    try:
        ddl_path = os.path.join(_BASE_DIR, "sql", "expression_enhancement_migration.sql")
        if os.path.isfile(ddl_path):
            with open(ddl_path, encoding="utf-8") as f:
                for stmt in f.read().split(";"):
                    s = stmt.strip()
                    if not s or s.upper().startswith("USE ") or "CREATE TABLE" not in s.upper():
                        continue
                    if "ensembl_id_version" in s:
                        db.execute(text(s))
            db.commit()

        if clear:
            db.execute(text("DELETE FROM ensembl_id_version"))
            db.commit()

        rows_buf: List[str] = []
        stats = {"rows": 0}

        def flush():
            if not rows_buf:
                return
            sql = (
                "INSERT IGNORE INTO ensembl_id_version "
                "(gene_id, ensembl_id, id_base, version_num, ensembl_release, is_current, source) VALUES "
                + ", ".join(rows_buf)
            )
            client.execute_write(sql)
            stats["rows"] += len(rows_buf)
            rows_buf.clear()

        def add_row(gene_id: int, eid: str, is_current: int, source: str):
            eid = (eid or "").strip()
            if not eid or not eid.startswith("ENS"):
                return
            base = re.sub(r"\.\d+$", "", eid)
            ver = None
            m = re.search(r"\.(\d+)$", eid)
            if m:
                ver = int(m.group(1))
            rows_buf.append(
                f"({gene_id}, '{_esc(eid)}', '{_esc(base)}', "
                f"{ver if ver is not None else 'NULL'}, '{ENSEMBL_RELEASE}', {is_current}, '{source}')"
            )
            if len(rows_buf) >= BATCH:
                flush()

        for r in db.execute(text(
            "SELECT gene_id, ensembl_id FROM gene_basic WHERE ensembl_id IS NOT NULL"
        )).mappings():
            add_row(r["gene_id"], r["ensembl_id"], 1, "gene_basic")

        for r in db.execute(text(
            "SELECT gene_id, db_id FROM gene_xref WHERE db_name='ensembl'"
        )).mappings():
            add_row(r["gene_id"], r["db_id"], 0, "gene_xref")

        flush()
        safe_print(f"  Ensembl 版本映射：{stats['rows']} 条（release {ENSEMBL_RELEASE}）")
    except Exception as e:
        db.rollback()
        safe_print(f"[警告] Ensembl 版本映射：{e}")
    finally:
        db.close()


def build_from_ncbi(clear: bool = False):
    safe_print(f"=== 构建基因别名/ID 映射（锚定 {REFERENCE_ASSEMBLY} / taxon {TAXON_ID}）===")
    if not ensure_alias_tables():
        safe_print("[错误] 无法创建 gene_alias / gene_xref 表")
        return

    if clear:
        _client().execute_write("DELETE FROM gene_xref")
        _client().execute_write("DELETE FROM gene_alias")
        safe_print("  已清空旧别名/xref 数据")

    by_name, by_symbol, by_ensembl = _load_gene_basic_maps()
    safe_print(f"  gene_basic：{len(by_name)} 个符号")

    # gene2ensembl 全物种文件体积大、NCBI 下载慢；直接用空映射，
    # gene_info.gz 的 Ensembl/ZFIN dbXref 已足够完成跨库 ID 关联。
    try:
        if os.path.isfile(os.path.join(NCBI_CACHE_DIR, "gene2ensembl.gz")):
            gene2ensembl = _load_gene2ensembl()
        else:
            safe_print("  [提示] 跳过 gene2ensembl（未缓存，依赖 gene_info Ensembl dbXref）")
            gene2ensembl = {}
    except Exception as e:  # noqa: BLE001
        safe_print(f"  [警告] gene2ensembl 不可用（{e}），使用空映射")
        gene2ensembl = {}

    stats = {"alias_rows": 0, "xref_rows": 0, "genes_linked": 0}
    alias_buf: List[str] = []
    xref_buf: List[str] = []
    linked_genes: Set[int] = set()

    def add_alias(gene_id: int, alias: str, alias_type: str, source: str, is_primary: int = 0):
        a = alias.strip().lower()
        if not a or len(a) > 128:
            return
        alias_buf.append(
            f"({gene_id}, '{_esc(a)}', '{alias_type}', '{source}', {is_primary})"
        )
        if len(alias_buf) >= BATCH:
            _flush_aliases(alias_buf, stats)
            alias_buf.clear()

    def add_xref(gene_id: int, db_name: str, db_id: str, source: str = "ncbi"):
        db_id = (db_id or "").strip().split("|")[0].strip()
        if not db_id or "|" in db_id or len(db_id) > 64:
            return
        xref_buf.append(f"({gene_id}, '{db_name}', '{_esc(db_id)}', '{source}')")
        if len(xref_buf) >= BATCH:
            _flush_xrefs(xref_buf, stats)
            xref_buf.clear()

    # gene_basic 主符号
    from sqlalchemy import text
    client = _client()
    db = next(client.get_db())
    try:
        for r in db.execute(text("SELECT gene_id, gene_name, gene_symbol FROM gene_basic")).mappings():
            gid = r["gene_id"]
            add_alias(gid, r["gene_name"], "symbol", "gene_basic", 1)
            if r["gene_symbol"] and r["gene_symbol"].lower() != r["gene_name"].lower():
                add_alias(gid, r["gene_symbol"], "synonym", "gene_basic", 0)
    finally:
        db.close()

    # 手工别名
    for alias, target in KNOWN_ALIASES.items():
        gid = by_name.get(target.lower())
        if gid:
            add_alias(gid, alias, "synonym", "manual", 0)

    # NCBI gene_info
    safe_print("  解析 NCBI Danio_rerio.gene_info …")
    ncbi_matched = 0
    for line in _download_gz(NCBI_GENE_INFO):
        if line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 15:
            continue
        ncbi_id = cols[1]
        symbol = cols[2]
        synonyms = cols[4]
        dbxrefs = cols[5]

        gid = _match_gene_id(symbol, dbxrefs, by_name, by_symbol, by_ensembl, gene2ensembl, ncbi_id)
        if not gid:
            continue
        ncbi_matched += 1
        linked_genes.add(gid)

        add_xref(gid, "ncbi_gene", ncbi_id, "ncbi")
        if symbol:
            is_pri = 1 if by_name.get(symbol.lower()) == gid else 0
            add_alias(gid, symbol, "symbol", "ncbi", is_pri)
        if synonyms:
            for syn in synonyms.split("|"):
                syn = syn.strip()
                if syn:
                    add_alias(gid, syn, "synonym", "ncbi", 0)
        for db_name, db_id in _parse_dbxrefs(dbxrefs):
            add_xref(gid, db_name, db_id, "ncbi")
            if db_name == "zfin" and db_id.upper().startswith("ZDB-GENE-"):
                add_alias(gid, db_id, "zfin_id", "ncbi", 0)

        ens = gene2ensembl.get(ncbi_id)
        if ens:
            add_xref(gid, "ensembl", ens, "ncbi_gene2ensembl")

    _flush_aliases(alias_buf, stats)
    _flush_xrefs(xref_buf, stats)

    stats["genes_linked"] = len(linked_genes)
    safe_print(f"  NCBI 关联基因：{ncbi_matched}")
    safe_print(f"  写入 alias 批次：{stats['alias_rows']}  写入 xref 批次：{stats['xref_rows']}")
    build_ensembl_versions()
    safe_print("[完成] 别名索引已更新。可提问：p53 对应哪个 Ensembl ID？")


def _load_gene2ensembl_from_db() -> Dict[str, str]:
    """优先从已入库 xref 读取 NCBI→Ensembl，避免重复下载大文件。"""
    mapping: Dict[str, str] = {}
    sql = (
        "SELECT n.db_id AS ncbi_id, e.db_id AS ens FROM gene_xref n "
        "JOIN gene_xref e ON n.gene_id = e.gene_id "
        "WHERE n.db_name='ncbi_gene' AND e.db_name='ensembl'"
    )
    r = _client().execute_sql(sql)
    for row in r.get("data") or []:
        ncbi_id, ens = row.get("ncbi_id"), row.get("ens")
        if ncbi_id and ens:
            mapping[str(ncbi_id)] = str(ens).split("|")[0].strip()
    if mapping:
        safe_print(f"  本地 gene2ensembl：{len(mapping)} 条")
        return mapping
    return _load_gene2ensembl()


def repair_corrupt_xrefs():
    """删除 db_id 含 | 的错误 xref，并从 NCBI gene_info 回补跨库 ID。"""
    safe_print(f"=== 修复损坏 xref（锚定 {REFERENCE_ASSEMBLY}）===")
    if not ensure_alias_tables():
        safe_print("[错误] 无法访问 gene_alias / gene_xref")
        return

    client = _client()
    r = client.execute_sql("SELECT COUNT(*) AS cnt FROM gene_xref WHERE db_id LIKE '%|%'")
    bad = (r.get("data") or [{}])[0].get("cnt", 0)
    safe_print(f"  损坏 xref：{bad} 条")
    if bad:
        client.execute_write("DELETE FROM gene_xref WHERE db_id LIKE '%|%'")
        safe_print("  已删除损坏 xref")

    by_name, by_symbol, by_ensembl = _load_gene_basic_maps()
    gene2ensembl = _load_gene2ensembl_from_db()

    stats = {"alias_rows": 0, "xref_rows": 0}
    alias_buf: List[str] = []
    xref_buf: List[str] = []

    def add_alias(gene_id: int, alias: str, alias_type: str, source: str, is_primary: int = 0):
        a = alias.strip().lower()
        if not a or len(a) > 128:
            return
        alias_buf.append(
            f"({gene_id}, '{_esc(a)}', '{alias_type}', '{source}', {is_primary})"
        )
        if len(alias_buf) >= BATCH:
            _flush_aliases(alias_buf, stats)
            alias_buf.clear()

    def add_xref(gene_id: int, db_name: str, db_id: str, source: str = "ncbi"):
        db_id = (db_id or "").strip().split("|")[0].strip()
        if not db_id or "|" in db_id or len(db_id) > 64:
            return
        xref_buf.append(f"({gene_id}, '{db_name}', '{_esc(db_id)}', '{source}')")
        if len(xref_buf) >= BATCH:
            _flush_xrefs(xref_buf, stats)
            xref_buf.clear()

    safe_print("  重新解析 NCBI Danio_rerio.gene_info …")
    ncbi_matched = 0
    for line in _download_gz(NCBI_GENE_INFO):
        if line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 15:
            continue
        ncbi_id = cols[1]
        symbol = cols[2]
        dbxrefs = cols[5]

        gid = _match_gene_id(symbol, dbxrefs, by_name, by_symbol, by_ensembl, gene2ensembl, ncbi_id)
        if not gid:
            continue
        ncbi_matched += 1

        add_xref(gid, "ncbi_gene", ncbi_id, "ncbi")
        for db_name, db_id in _parse_dbxrefs(dbxrefs):
            add_xref(gid, db_name, db_id, "ncbi")
            if db_name == "zfin" and db_id.upper().startswith("ZDB-GENE-"):
                add_alias(gid, db_id, "zfin_id", "ncbi", 0)
        ens = gene2ensembl.get(ncbi_id)
        if ens:
            add_xref(gid, "ensembl", ens, "ncbi_gene2ensembl")

    _flush_aliases(alias_buf, stats)
    _flush_xrefs(xref_buf, stats)
    safe_print(f"  NCBI 关联：{ncbi_matched}  回补 alias：{stats['alias_rows']}  xref：{stats['xref_rows']}")
    build_ensembl_versions()
    safe_print("[完成] xref 修复完成")


def main():
    import argparse
    p = argparse.ArgumentParser(description="构建基因别名与跨库 ID 映射")
    p.add_argument("--clear", action="store_true", help="清空后重建")
    p.add_argument("--repair-xrefs", action="store_true", help="仅修复损坏 xref 并回补 ZFIN/Ensembl")
    args = p.parse_args()
    if args.repair_xrefs:
        repair_corrupt_xrefs()
    else:
        build_from_ncbi(clear=args.clear)


if __name__ == "__main__":
    main()
