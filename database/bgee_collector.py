# -*- coding: utf-8 -*-
"""
Bgee 成体/组织表达导入（Danio rerio expr_simple 整合图谱）

数据源：https://www.bgee.org/ftp/bgee_v15_2/download/calls/expr_calls/Danio_rerio_expr_simple.tsv.gz
整合 RNA-seq 与 ZFIN in situ 等，按解剖结构给出 present 调用与 Expression Score。
"""
import bootstrap  # noqa: F401

import csv
import gzip
import io
import os
import re
import urllib.request
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pymysql

from common_utils import logger, safe_print
from config import DB_CONFIG, _BASE_DIR

BGEE_VERSION = "bgee_v15_2"
EXPR_SIMPLE_URL = (
    f"https://www.bgee.org/ftp/{BGEE_VERSION}/download/calls/expr_calls/"
    "Danio_rerio_expr_simple.tsv.gz"
)
DATA_DIR = os.path.join(_BASE_DIR, "data", "bgee")
CACHE_FILE = os.path.join(DATA_DIR, "Danio_rerio_expr_simple.tsv.gz")

# 发育阶段类解剖项（组织表达展示时可选过滤）
DEV_STAGE_ENTITIES = {
    "blastula", "gastrula", "segmentation stage", "pharyngula stage",
    "larval stage", "embryo", "zygote", "cleavage stage", "organogenesis stage",
}


def ensure_expression_schema(conn) -> bool:
    """确保 gene_expression.data_source 列存在。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='gene_expression' AND COLUMN_NAME='data_source'",
            (DB_CONFIG["database"],),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                "ALTER TABLE gene_expression ADD COLUMN data_source VARCHAR(32) "
                "DEFAULT 'atlas_embryo' COMMENT 'atlas_embryo/bgee/zfin'"
            )
            cur.execute(
                "UPDATE gene_expression SET data_source='atlas_embryo' WHERE data_source IS NULL"
            )
    conn.commit()
    return True


def _download(url: str, dest: str, force: bool = False) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest) and not force:
        safe_print(f"  [缓存] {os.path.basename(dest)}")
        return dest
    safe_print(f"  [下载] Bgee expr_simple …")
    req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
    with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(1024 * 512)
            if not chunk:
                break
            out.write(chunk)
    safe_print(f"  [完成] {os.path.getsize(dest) // 1024 // 1024} MB")
    return dest


def _load_ensembl_map(conn) -> Dict[str, int]:
    """Ensembl ID（含去版本）→ gene_id"""
    mapping: Dict[str, int] = {}
    with conn.cursor() as cur:
        cur.execute("SELECT gene_id, ensembl_id FROM gene_basic WHERE ensembl_id IS NOT NULL")
        for gid, eid in cur.fetchall():
            if not eid:
                continue
            eid = eid.strip()
            mapping[eid] = gid
            base = re.sub(r"\.\d+$", "", eid)
            mapping[base] = gid
        cur.execute(
            "SELECT gx.gene_id, gx.db_id FROM gene_xref gx WHERE gx.db_name='ensembl'"
        )
        for gid, eid in cur.fetchall():
            if not eid:
                continue
            eid = eid.strip()
            mapping[eid] = gid
            base = re.sub(r"\.\d+$", "", eid)
            mapping[base] = gid
    return mapping


def _parse_bgee_rows(path: str, min_score: float = 40.0) -> Dict[str, List[Tuple[str, float, str]]]:
    """gene_ensembl -> [(tissue, score, quality), ...]"""
    per_gene: Dict[str, List[Tuple[str, float, str]]] = defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if (row.get("Expression") or "").lower() != "present":
                continue
            eid = (row.get("Gene ID") or "").strip()
            tissue = (row.get("Anatomical entity name") or "").strip().strip('"')
            if not eid or not tissue:
                continue
            try:
                score = float(row.get("Expression score") or 0)
            except ValueError:
                continue
            if score < min_score:
                continue
            quality = (row.get("Call quality") or "").strip()
            per_gene[eid].append((tissue, score, quality))
    for eid in per_gene:
        per_gene[eid].sort(key=lambda x: -x[1])
    return per_gene


def run_bgee_import(
    force_download: bool = False,
    top_tissues: int = 18,
    min_score: float = 40.0,
    skip_dev_stages: bool = False,
    dry_run: bool = False,
) -> dict:
    stats = {"genes": 0, "rows": 0, "skipped": 0}
    safe_print(f"=== Bgee 组织表达导入 ({BGEE_VERSION}) ===")
    path = _download(EXPR_SIMPLE_URL, CACHE_FILE, force=force_download)

    if dry_run:
        per_gene = _parse_bgee_rows(path, min_score=min_score)
        safe_print(f"[预览] 有表达调用的基因约 {len(per_gene)} 个")
        return stats

    conn = pymysql.connect(**DB_CONFIG)
    try:
        ensure_expression_schema(conn)
        id_map = _load_ensembl_map(conn)
        safe_print(f"  Ensembl 映射：{len(id_map)} 条")

        with conn.cursor() as cur:
            cur.execute("DELETE FROM gene_expression WHERE data_source='bgee'")
        conn.commit()
        safe_print("  已清除旧 Bgee 表达记录")

        per_gene = _parse_bgee_rows(path, min_score=min_score)
        insert_sql = (
            "INSERT INTO gene_expression "
            "(gene_id, tissue, expression_stage, expression_level, data_source) "
            "VALUES (%s, %s, %s, %s, 'bgee')"
        )
        batch: List[tuple] = []

        for eid, tissues in per_gene.items():
            gene_id = id_map.get(eid) or id_map.get(re.sub(r"\.\d+$", "", eid))
            if not gene_id:
                stats["skipped"] += 1
                continue

            kept = []
            for tissue, score, quality in tissues:
                if skip_dev_stages and tissue.lower() in DEV_STAGE_ENTITIES:
                    continue
                kept.append((tissue, score, quality))
            if not kept:
                kept = tissues[:top_tissues]
            else:
                kept = kept[:top_tissues]

            for tissue, score, quality in kept:
                stage = f"Bgee {quality}" if quality else "Bgee present"
                tname = tissue[:128] if tissue else tissue
                batch.append((gene_id, tname, stage, round(score, 2)))
                stats["rows"] += 1

            stats["genes"] += 1
            if len(batch) >= 3000:
                with conn.cursor() as cur:
                    cur.executemany(insert_sql, batch)
                conn.commit()
                batch.clear()

        if batch:
            with conn.cursor() as cur:
                cur.executemany(insert_sql, batch)
            conn.commit()

        safe_print(f"  写入基因：{stats['genes']}  表达行：{stats['rows']}  未映射：{stats['skipped']}")
        safe_print("[完成] Bgee 组织表达已入库，可问「tp53 在哪些组织表达」")
    except Exception as e:
        logger.error(f"[bgee_collector] {e}")
        raise
    finally:
        conn.close()
    return stats
