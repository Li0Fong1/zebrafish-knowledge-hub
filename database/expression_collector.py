# -*- coding: utf-8 -*-
"""
斑马鱼表达量批量导入（EBI Expression Atlas E-ERAD-475 胚胎发育时间序列 TPM）

数据源：White et al. 2017 斑马鱼胚胎 18 个发育时期 RNA-seq（Ensembl ID 对齐）
"""
import bootstrap  # noqa: F401

import csv
import os
import urllib.request
from typing import Dict, List, Optional, Tuple

from config import DB_CONFIG, _BASE_DIR
from common_utils import logger, safe_print

EXPERIMENT = "E-ERAD-475"
FTP_BASE = "https://ftp.ebi.ac.uk/pub/databases/microarray/data/atlas/experiments/E-ERAD-475"
DATA_DIR = os.path.join(_BASE_DIR, "data", "ensembl")
TPM_FILE = os.path.join(DATA_DIR, f"{EXPERIMENT}-tpms.tsv")
MARKERS_FILE = os.path.join(DATA_DIR, f"{EXPERIMENT}-tpms-markers.tsv")

# g1–g18 → 发育时期（来自 Expression Atlas markers）
STAGE_LABELS = {
    "g1": "blastula 128-cell",
    "g2": "blastula 1k-cell",
    "g3": "blastula dome",
    "g4": "cleavage 2-cell",
    "g5": "gastrula 50%-epiboly",
    "g6": "gastrula 75%-epiboly",
    "g7": "gastrula shield",
    "g8": "hatching long-pec",
    "g9": "larval day 4",
    "g10": "larval day 5",
    "g11": "larval protruding mouth",
    "g12": "pharyngula prim-15",
    "g13": "pharyngula prim-25",
    "g14": "pharyngula prim-5",
    "g15": "segmentation 1-4 somites",
    "g16": "segmentation 14-19 somites",
    "g17": "segmentation 20-25 somites",
    "g18": "zygote",
}


def _download(url: str, dest: str, force: bool = False) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest) and not force:
        safe_print(f"  [缓存] {os.path.basename(dest)}")
        return dest
    safe_print(f"  [下载] {os.path.basename(dest)} …")
    req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(1024 * 256)
            if not chunk:
                break
            out.write(chunk)
    safe_print(f"  [完成] {os.path.basename(dest)} ({os.path.getsize(dest) // 1024} KB)")
    return dest


def ensure_tpm_files(force: bool = False) -> Tuple[str, str]:
    tpm = _download(f"{FTP_BASE}/{EXPERIMENT}-tpms.tsv", TPM_FILE, force=force)
    markers = _download(f"{FTP_BASE}/{EXPERIMENT}-tpms-markers.tsv", MARKERS_FILE, force=force)
    return tpm, markers


def _parse_tpm_value(raw: str) -> Optional[float]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        parts = [float(x.replace(",", ".")) for x in raw.split(",") if x.strip()]
        return max(parts) if parts else None
    except ValueError:
        return None


def _load_ensembl_to_gene_id(conn) -> Dict[str, int]:
    mapping = {}
    with conn.cursor() as cur:
        cur.execute("SELECT gene_id, ensembl_id FROM gene_basic WHERE ensembl_id IS NOT NULL")
        for gid, eid in cur.fetchall():
            if eid:
                mapping[eid] = gid
    return mapping


class ExpressionCollector:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.conn = None
        self.stats = {"genes": 0, "rows": 0, "skipped": 0}

    def connect(self):
        import pymysql
        self.conn = pymysql.connect(
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            user=DB_CONFIG["user"],
            password=DB_CONFIG["password"],
            database=DB_CONFIG["database"],
            charset=DB_CONFIG["charset"],
            autocommit=False,
        )

    def close(self):
        if self.conn:
            self.conn.close()

    def import_tpm(
        self,
        force_download: bool = False,
        replace_all: bool = True,
        min_tpm: float = 0.5,
        top_stages: int = 0,
    ) -> dict:
        """导入 TPM 到 gene_expression。top_stages=0 表示保存全部 18 个时期。"""
        ensure_tpm_files(force=force_download)
        if self.dry_run:
            with open(TPM_FILE, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f, delimiter="\t")
                n = sum(1 for _ in reader)
            safe_print(f"[预览] TPM 文件约 {n} 个基因")
            return self.stats

        self.connect()
        id_map = _load_ensembl_to_gene_id(self.conn)
        safe_print(f"  数据库 Ensembl 映射：{len(id_map)} 条")

        if replace_all:
            with self.conn.cursor() as cur:
                cur.execute("DELETE FROM gene_expression WHERE data_source='atlas_embryo' OR data_source IS NULL")
            self.conn.commit()
            safe_print("  已清空胚胎 Atlas 表达记录（保留 Bgee 组织数据）")

        insert_sql = """
        INSERT INTO gene_expression (gene_id, tissue, expression_stage, expression_level, data_source)
        VALUES (%s, %s, %s, %s, 'atlas_embryo')
        """
        summary_updates = []
        batch: List[tuple] = []

        with open(TPM_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            stage_cols = [c for c in (reader.fieldnames or []) if c.startswith("g") and c[1:].isdigit()]

            for row in reader:
                eid = (row.get("GeneID") or "").strip()
                gene_id = id_map.get(eid)
                if not gene_id:
                    self.stats["skipped"] += 1
                    continue

                stage_vals = []
                for col in stage_cols:
                    tpm = _parse_tpm_value(row.get(col, ""))
                    if tpm is None or tpm < min_tpm:
                        continue
                    label = STAGE_LABELS.get(col, col)
                    stage_vals.append((label, tpm))

                if not stage_vals:
                    self.stats["skipped"] += 1
                    continue

                stage_vals.sort(key=lambda x: -x[1])
                if top_stages > 0:
                    stage_vals = stage_vals[:top_stages]

                for label, tpm in stage_vals:
                    batch.append((gene_id, "whole embryo", label, round(tpm, 4)))

                peak_label, peak_tpm = stage_vals[0]
                summary_updates.append((
                    f"E-ERAD-475 峰值 {peak_label} TPM={peak_tpm:.1f}（18期胚胎时间序列）",
                    gene_id,
                ))
                self.stats["genes"] += 1

                if len(batch) >= 2000:
                    self._flush(insert_sql, batch)
                    batch.clear()

        if batch:
            self._flush(insert_sql, batch)

        if summary_updates:
            with self.conn.cursor() as cur:
                cur.executemany(
                    "UPDATE gene_basic SET expression_summary=%s WHERE gene_id=%s",
                    summary_updates,
                )
        self.conn.commit()
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM gene_expression")
            total_rows = cur.fetchone()[0]
        safe_print(
            f"\n[完成] 表达导入：基因 {self.stats['genes']} | "
            f"写入记录 {self.stats['rows']} | 未匹配 {self.stats['skipped']}"
        )
        safe_print(f"  gene_expression 表现有 {total_rows} 条")
        return self.stats

    def _flush(self, sql: str, batch: list):
        with self.conn.cursor() as cur:
            cur.executemany(sql, batch)
        self.stats["rows"] += len(batch)
        self.conn.commit()


def run_expression_import(
    force_download: bool = False,
    replace_all: bool = True,
    min_tpm: float = 0.5,
    top_stages: int = 0,
    dry_run: bool = False,
):
    safe_print("=" * 60)
    safe_print("  斑马鱼表达量导入（Expression Atlas E-ERAD-475）")
    safe_print("  18 个胚胎发育时期 · TPM")
    safe_print("=" * 60)
    col = ExpressionCollector(dry_run=dry_run)
    try:
        col.import_tpm(
            force_download=force_download,
            replace_all=replace_all,
            min_tpm=min_tpm,
            top_stages=top_stages,
        )
    finally:
        col.close()
