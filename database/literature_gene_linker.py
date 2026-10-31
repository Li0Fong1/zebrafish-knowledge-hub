# -*- coding: utf-8 -*-
"""从文献 catalog 提取基因符号，增量写入 gene_reference。"""
import bootstrap  # noqa: F401

import json
import os
import re
import time
from typing import Dict, Iterator, List, Optional, Set, Tuple

from common_utils import logger, safe_print
from config import _BASE_DIR
from gene_id_mapper import KNOWN_ALIASES

CATALOG_FILE = os.path.join(_BASE_DIR, "data", "literature", "literature_catalog.jsonl")
PROGRESS_FILE = os.path.join(_BASE_DIR, "data", "literature", "gene_link_progress.json")

TOKEN_RE = re.compile(r"\b([a-zA-Z][a-zA-Z0-9.-]{0,20})\b")

STOP_TOKENS = frozenset({
    "a", "an", "the", "and", "or", "for", "with", "from", "that", "this", "were", "was",
    "are", "is", "in", "on", "at", "to", "of", "by", "as", "be", "has", "have", "had",
    "not", "but", "can", "may", "also", "used", "using", "use", "study", "studies",
    "zebrafish", "danio", "rerio", "embryo", "embryos", "larvae", "adult", "fish",
    "gene", "genes", "cell", "cells", "protein", "proteins", "mrna", "cdna", "dna", "rna",
    "go", "kegg", "hpf", "fpkm", "tpm", "blast", "crispr", "cas9", "sirna", "mo",
    "morpholino", "knockout", "mutant", "mutants", "wild", "type", "control", "group",
    "fig", "figure", "table", "supplementary", "results", "conclusion", "background",
    "introduction", "method", "methods", "discussion", "abstract", "pmid", "doi",
    "ncbi", "ensembl", "zfin", "refseq", "pubmed", "model", "models", "pathway",
    "signaling", "development", "developmental", "expression", "analysis", "data",
    "show", "shown", "found", "role", "involved", "human", "mouse", "rat", "both",
    "two", "three", "one", "new", "well", "high", "low", "via", "after", "during",
})

MAX_GENES_PER_PAPER = 40
BATCH_INSERT = 300


class GeneSymbolIndex:
    """别名 → gene_id 倒排索引（启动时一次性加载）。"""

    def __init__(self):
        self.alias_to_gene: Dict[str, int] = {}
        self._load()

    def _load(self):
        from sqlalchemy import text
        from mysql_client import get_mysql_client

        client = get_mysql_client()
        db = next(client.get_db())
        try:
            rows = db.execute(text(
                "SELECT gb.gene_id, gb.gene_name, ga.alias "
                "FROM gene_basic gb "
                "LEFT JOIN gene_alias ga ON gb.gene_id = ga.gene_id"
            )).mappings().all()
        finally:
            db.close()

        for row in rows:
            gid = int(row["gene_id"])
            for field in ("gene_name", "alias"):
                alias = (row.get(field) or "").strip().lower()
                if not alias or len(alias) < 2:
                    continue
                if alias not in self.alias_to_gene:
                    self.alias_to_gene[alias] = gid

        for alias, canonical in KNOWN_ALIASES.items():
            a = alias.lower()
            c = canonical.lower()
            if c in self.alias_to_gene and a not in self.alias_to_gene:
                self.alias_to_gene[a] = self.alias_to_gene[c]

        logger.info(f"[gene_linker] 索引 {len(self.alias_to_gene)} 个别名")

    def lookup(self, token: str) -> Optional[int]:
        t = token.lower().strip()
        if t in KNOWN_ALIASES:
            t = KNOWN_ALIASES[t].lower()
        return self.alias_to_gene.get(t)


def _load_progress() -> dict:
    if os.path.isfile(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"catalog_line": 0, "total_links": 0, "papers_processed": 0}


def _save_progress(progress: dict):
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False)


def _paper_ref_key(art: dict) -> str:
    pmid = str(art.get("pmid") or "").strip()
    if pmid:
        return pmid
    wos = str(art.get("wos_ut") or "").strip()
    if wos:
        return f"WOS:{wos}"
    doi = str(art.get("doi") or "").strip()
    if doi:
        return f"DOI:{doi[:48]}"
    return ""


def _load_existing_pairs() -> Set[Tuple[int, str]]:
    from sqlalchemy import text
    from mysql_client import get_mysql_client

    pairs: Set[Tuple[int, str]] = set()
    client = get_mysql_client()
    db = next(client.get_db())
    try:
        rows = db.execute(text(
            "SELECT gene_id, pubmed_id FROM gene_reference WHERE pubmed_id IS NOT NULL"
        )).mappings().all()
        for r in rows:
            pairs.add((int(r["gene_id"]), str(r["pubmed_id"]).strip()))
    finally:
        db.close()
    return pairs


def extract_gene_ids(text: str, index: GeneSymbolIndex) -> Set[int]:
    if not text:
        return set()
    found: Set[int] = set()
    for m in TOKEN_RE.finditer(text):
        token = m.group(1).lower()
        if token in STOP_TOKENS:
            continue
        if len(token) < 2:
            continue
        if len(token) == 2 and token not in KNOWN_ALIASES:
            continue
        gid = index.lookup(token)
        if gid:
            found.add(gid)
        if len(found) >= MAX_GENES_PER_PAPER:
            break
    return found


def _iter_catalog(from_line: int = 0) -> Iterator[Tuple[int, dict]]:
    if not os.path.isfile(CATALOG_FILE):
        return
    with open(CATALOG_FILE, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i < from_line:
                continue
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError:
                continue


def _parse_year(val) -> Optional[int]:
    try:
        y = int(str(val)[:4])
        return y if 1900 <= y <= 2100 else None
    except (TypeError, ValueError):
        return None


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    return s[:n] if len(s) > n else s


def _insert_refs(rows: List[dict]) -> int:
    if not rows:
        return 0
    from sqlalchemy import text
    from mysql_client import get_mysql_client

    client = get_mysql_client()
    db = next(client.get_db())
    inserted = 0
    sql = text(
        "INSERT INTO gene_reference (gene_id, pubmed_id, title, journal, publish_year) "
        "SELECT :gene_id, :pubmed_id, :title, :journal, :publish_year "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM gene_reference r "
        "  WHERE r.gene_id = :gene_id AND r.pubmed_id = :pubmed_id"
        ")"
    )
    try:
        for i in range(0, len(rows), BATCH_INSERT):
            chunk = rows[i:i + BATCH_INSERT]
            for row in chunk:
                result = db.execute(sql, row)
                inserted += result.rowcount or 0
            db.commit()
    except Exception as e:
        db.rollback()
        raise RuntimeError(f"写入 gene_reference 失败: {e}") from e
    finally:
        db.close()
    return inserted


def link_catalog_batch(
    index: Optional[GeneSymbolIndex] = None,
    max_papers: int = 5000,
    from_line: Optional[int] = None,
    existing_pairs: Optional[Set[Tuple[int, str]]] = None,
) -> dict:
    """
    增量处理 catalog，返回统计。
    from_line=None 时从 progress 文件续传。
    """
    if not os.path.isfile(CATALOG_FILE):
        safe_print(f"  [gene_linker] 未找到 catalog: {CATALOG_FILE}")
        return {"papers": 0, "links": 0, "inserted": 0, "done": True}

    progress = _load_progress()
    start_line = progress["catalog_line"] if from_line is None else from_line
    if index is None:
        index = GeneSymbolIndex()
    if existing_pairs is None:
        existing_pairs = _load_existing_pairs()

    pending: List[dict] = []
    stats = {"papers": 0, "links": 0, "inserted": 0, "last_line": start_line}

    for line_no, art in _iter_catalog(start_line):
        ref_key = _paper_ref_key(art)
        if not ref_key:
            stats["last_line"] = line_no + 1
            continue

        text_blob = " ".join([
            art.get("title") or "",
            art.get("abstract") or "",
        ])
        gene_ids = extract_gene_ids(text_blob, index)
        stats["papers"] += 1

        title = _truncate(art.get("title") or "", 512)
        journal = _truncate(art.get("journal") or "", 128)
        year = _parse_year(art.get("year"))

        for gid in gene_ids:
            pair = (gid, ref_key)
            if pair in existing_pairs:
                continue
            existing_pairs.add(pair)
            pending.append({
                "gene_id": gid,
                "pubmed_id": ref_key,
                "title": title,
                "journal": journal,
                "publish_year": year,
            })
            stats["links"] += 1

        stats["last_line"] = line_no + 1

        if len(pending) >= BATCH_INSERT:
            n = _insert_refs(pending)
            stats["inserted"] += n
            pending.clear()

        if stats["papers"] >= max_papers:
            break

    if pending:
        n = _insert_refs(pending)
        stats["inserted"] += n

    progress["catalog_line"] = stats["last_line"]
    progress["total_links"] = progress.get("total_links", 0) + stats["inserted"]
    progress["papers_processed"] = progress.get("papers_processed", 0) + stats["papers"]
    progress["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save_progress(progress)

    total_lines = sum(1 for _ in open(CATALOG_FILE, encoding="utf-8"))
    stats["done"] = stats["last_line"] >= total_lines
    stats["catalog_total"] = total_lines
    return stats


def run_gene_linking(
    max_papers: int = 5000,
    loop: bool = False,
    interval: int = 120,
    reset: bool = False,
):
    if reset:
        _save_progress({"catalog_line": 0, "total_links": 0, "papers_processed": 0})
        safe_print("  [gene_linker] 进度已重置")

    safe_print("=" * 60)
    safe_print("  文献 → 基因关联（增量写入 gene_reference）")
    safe_print(f"  数据源: {CATALOG_FILE}")
    if loop:
        safe_print(f"  模式: 持续增量（每 {interval}s 检查新文献，可与菜单 11 并行）")
        safe_print("  停止: Ctrl+C")
    safe_print("=" * 60)

    index = GeneSymbolIndex()
    try:
        from sqlalchemy import text
        from mysql_client import get_mysql_client
        client = get_mysql_client()
        db = next(client.get_db())
        try:
            row = db.execute(text(
                "SELECT CHARACTER_MAXIMUM_LENGTH AS n FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME='gene_reference' "
                "AND COLUMN_NAME='pubmed_id'"
            )).mappings().first()
            if row and int(row["n"] or 16) < 64:
                db.execute(text(
                    "ALTER TABLE gene_reference MODIFY pubmed_id VARCHAR(64) NULL"
                ))
                db.commit()
                safe_print("  [gene_linker] pubmed_id 列已扩展至 VARCHAR(64)")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"[gene_linker] schema 迁移跳过: {e}")

    existing_pairs = _load_existing_pairs()
    safe_print(f"  已有 gene_reference 关联 {len(existing_pairs)} 条")

    total_inserted = 0
    while True:
        stats = link_catalog_batch(
            index=index,
            max_papers=max_papers,
            existing_pairs=existing_pairs,
        )
        total_inserted += stats["inserted"]
        safe_print(
            f"  本批: 处理 {stats['papers']} 篇 | 匹配 {stats['links']} 条 | "
            f"新入库 {stats['inserted']} 条 | catalog 行 {stats['last_line']}/{stats.get('catalog_total', '?')}"
        )

        if stats.get("done"):
            if loop:
                safe_print(
                    f"  catalog 已追平（{stats['last_line']}/{stats.get('catalog_total', '?')} 行），"
                    f"等待菜单 11 写入新文献… {interval}s 后再检查"
                )
                time.sleep(interval)
                continue
            safe_print(f"\n[完成] catalog 已全部处理，累计新入库 {total_inserted} 条")
            break
        if not loop:
            safe_print(
                f"\n[暂停] 仍有未处理文献，进度已保存。"
                f"加 --loop 可自动续跑（与菜单 11 并行）"
            )
            break
        if stats["papers"] == 0:
            safe_print(f"  本批无新文献，{interval}s 后再检查…")
            time.sleep(interval)
        else:
            time.sleep(1)

    progress = _load_progress()
    safe_print(
        f"  累计关联 {progress.get('total_links', 0)} 条 | "
        f"已处理文献 {progress.get('papers_processed', 0)} 篇"
    )
    return stats
