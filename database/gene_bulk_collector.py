# -*- coding: utf-8 -*-
"""
斑马鱼全基因组基因批量采集（Ensembl 最新 release GTF → MySQL）

策略：
  1. catalog  — 下载并解析 GTF，批量写入 gene_basic（分钟级，~3 万基因）
  2. cdna     — 下载 Ensembl 全量 cDNA FASTA（供 BLAST / 离线分析）
  3. enrich   — REST API 深度注释 GO / 同源 / 文献 / 单基因 cDNA（慢，可断点续传）
"""
import bootstrap  # noqa: F401

import gzip
import json
import os
import shutil
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from typing import Dict, List, Optional, Set, Tuple

from config import DB_CONFIG, _BASE_DIR
from common_utils import logger, safe_print
from gene_collector import (
    GeneCollector,
    ENSEMBL_BASE,
    SPECIES,
    REQUEST_INTERVAL,
    REFERENCE_GENOME,
    _api_get,
    api_stop_requested,
    clear_api_stop,
    request_api_stop,
    fetch_annotation_bundle,
    fetch_cdna_from_lookup,
    fetch_homology_ensembl,
)

ASSEMBLY = "GRCz11"
DATA_DIR = os.path.join(_BASE_DIR, "data", "ensembl")
PROGRESS_FILE = os.path.join(DATA_DIR, "enrich_progress.json")
CATALOG_CACHE = os.path.join(DATA_DIR, "gene_catalog.json")

FTP_BASE = "https://ftp.ensembl.org/pub"
BATCH_INSERT = 800
DEFAULT_ENRICH_WORKERS = max(1, min(6, int(os.getenv("ENRICH_WORKERS", "4"))))


def get_latest_release() -> int:
    data = _api_get("/info/data/")
    if isinstance(data, dict) and data.get("releases"):
        return int(data["releases"][-1])
    return 116


def _gtf_url(release: int) -> str:
    return (
        f"{FTP_BASE}/release-{release}/gtf/danio_rerio/"
        f"Danio_rerio.{ASSEMBLY}.{release}.gtf.gz"
    )


def _cdna_url(release: int) -> str:
    return (
        f"{FTP_BASE}/release-{release}/fasta/danio_rerio/cdna/"
        f"Danio_rerio.{ASSEMBLY}.cdna.all.fa.gz"
    )


def _local_gtf_path(release: int) -> str:
    return os.path.join(DATA_DIR, f"Danio_rerio.{ASSEMBLY}.{release}.gtf.gz")


def _local_cdna_path(release: int) -> str:
    return os.path.join(DATA_DIR, f"Danio_rerio.{ASSEMBLY}.cdna.all.fa.gz")


def _local_cdna_fasta(release: int) -> str:
    return os.path.join(DATA_DIR, f"Danio_rerio.{ASSEMBLY}.cdna.all.fa")


def download_file(url: str, dest: str, force: bool = False) -> str:
    """下载大文件，显示简单进度。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest) and not force:
        size_mb = os.path.getsize(dest) / (1024 * 1024)
        safe_print(f"  [缓存] 已存在 {os.path.basename(dest)} ({size_mb:.1f} MB)")
        return dest

    safe_print(f"  [下载] {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
    tmp = dest + ".part"
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            chunk = 1024 * 256
            with open(tmp, "wb") as out:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    out.write(buf)
                    done += len(buf)
                    if total > 0 and done % (chunk * 40) < chunk:
                        pct = done * 100 // total
                        safe_print(f"    … {done // (1024 * 1024)} MB / {total // (1024 * 1024)} MB ({pct}%)")
        os.replace(tmp, dest)
    except Exception:
        if os.path.isfile(tmp):
            os.remove(tmp)
        raise

    size_mb = os.path.getsize(dest) / (1024 * 1024)
    safe_print(f"  [完成] {os.path.basename(dest)} ({size_mb:.1f} MB)")
    return dest


def _parse_gtf_attributes(attr: str) -> dict:
    out = {}
    for part in attr.strip().split(";"):
        part = part.strip()
        if not part or " " not in part:
            continue
        key, val = part.split(" ", 1)
        out[key] = val.strip().strip('"')
    return out


def parse_gtf_genes(gtf_gz: str, biotypes: Optional[Set[str]] = None) -> List[dict]:
    """从 GTF 提取 gene 特征（每个 gene_id 一条）。"""
    genes: Dict[str, dict] = {}
    with gzip.open(gtf_gz, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 9 or cols[2] != "gene":
                continue
            attrs = _parse_gtf_attributes(cols[8])
            gid = attrs.get("gene_id")
            if not gid:
                continue
            biotype = attrs.get("gene_biotype") or "unknown"
            if biotypes and biotype not in biotypes:
                continue
            symbol = (attrs.get("gene_name") or gid).lower()[:64]
            genes[gid] = {
                "ensembl_id": gid,
                "gene_name": symbol,
                "gene_symbol": None,
                "chromosome": cols[0],
                "gene_function": biotype,
                "biotype": biotype,
                "start": int(cols[3]),
                "end": int(cols[4]),
                "strand": cols[6],
                "source": attrs.get("gene_source") or cols[1],
            }
    return list(genes.values())


def dedupe_gene_names(records: List[dict]) -> List[dict]:
    """同名旁系同源基因：保留首条符号，其余用 Ensembl ID 作主键并记录别名。"""
    seen: Dict[str, str] = {}
    out = []
    for rec in records:
        name = rec["gene_name"]
        if name not in seen:
            seen[name] = rec["ensembl_id"]
            out.append(rec)
            continue
        rec = dict(rec)
        rec["gene_symbol"] = name
        rec["gene_name"] = rec["ensembl_id"].lower()[:64]
        out.append(rec)
    return out


def biotype_filter_from_arg(arg: str) -> Optional[Set[str]]:
    if not arg or arg.lower() in ("all", "*"):
        return None
    return {b.strip() for b in arg.split(",") if b.strip()}


class GenomeCollector:
    """全基因组批量采集器。"""

    def __init__(self, release: int = 0, dry_run: bool = False):
        self.release = release or get_latest_release()
        self.dry_run = dry_run
        self.conn = None
        self.stats = {"inserted": 0, "updated": 0, "skipped": 0}

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
            self.conn = None

    def _load_done_with_go(self) -> Set[str]:
        """已有 GO 注释的 Ensembl ID（作为 progress 真实完成集）。"""
        self.connect()
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT gb.ensembl_id
                FROM gene_basic gb
                INNER JOIN gene_go g ON gb.gene_id = g.gene_id
                WHERE gb.ensembl_id IS NOT NULL
                """
            )
            return {row[0] for row in cur.fetchall() if row[0]}

    def ensure_gtf(self, force: bool = False) -> str:
        path = _local_gtf_path(self.release)
        download_file(_gtf_url(self.release), path, force=force)
        return path

    def load_catalog(self, biotypes: Optional[Set[str]] = None, force_download: bool = False) -> List[dict]:
        gtf = self.ensure_gtf(force=force_download)
        safe_print(f"\n[解析] GTF release-{self.release} …")
        records = parse_gtf_genes(gtf, biotypes=biotypes)
        records = dedupe_gene_names(records)
        safe_print(f"  共 {len(records)} 个基因")
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CATALOG_CACHE, "w", encoding="utf-8") as f:
            json.dump({"release": self.release, "count": len(records), "genes": records}, f, ensure_ascii=False)
        return records

    def import_catalog(
        self,
        biotypes: Optional[Set[str]] = None,
        skip_existing: bool = False,
        force_download: bool = False,
    ) -> int:
        records = self.load_catalog(biotypes=biotypes, force_download=force_download)
        if self.dry_run:
            by_type: Dict[str, int] = {}
            for r in records:
                by_type[r["biotype"]] = by_type.get(r["biotype"], 0) + 1
            safe_print("[预览] 按 biotype 统计：")
            for bt, n in sorted(by_type.items(), key=lambda x: -x[1])[:15]:
                safe_print(f"  {bt}: {n}")
            return len(records)

        self.connect()
        existing_ids: Set[str] = set()
        if skip_existing:
            with self.conn.cursor() as cur:
                cur.execute("SELECT ensembl_id FROM gene_basic WHERE ensembl_id IS NOT NULL")
                existing_ids = {row[0] for row in cur.fetchall() if row[0]}

        sql = """
        INSERT INTO gene_basic
          (gene_name, gene_symbol, ensembl_id, chromosome, gene_function, expression_summary)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          ensembl_id=VALUES(ensembl_id),
          chromosome=VALUES(chromosome),
          gene_function=VALUES(gene_function),
          expression_summary=VALUES(expression_summary)
        """
        batch = []
        for rec in records:
            if skip_existing and rec["ensembl_id"] in existing_ids:
                self.stats["skipped"] += 1
                continue
            batch.append((
                rec["gene_name"],
                rec.get("gene_symbol"),
                rec["ensembl_id"],
                rec["chromosome"],
                f"{rec['biotype']} · {rec['source']}",
                f"Ensembl {ASSEMBLY} release-{self.release} · {rec['biotype']}",
            ))
            if len(batch) >= BATCH_INSERT:
                self._flush_batch(sql, batch)
                batch.clear()
        if batch:
            self._flush_batch(sql, batch)
        self.conn.commit()
        safe_print(
            f"\n[入库] 写入 {self.stats['inserted']} 条 | 跳过 {self.stats['skipped']}"
        )
        return len(records)

    def _flush_batch(self, sql: str, batch: list):
        with self.conn.cursor() as cur:
            cur.executemany(sql, batch)
        self.stats["inserted"] += len(batch)

    def download_full_cdna(self, force: bool = False, link_reference: bool = True) -> str:
        gz = _local_cdna_path(self.release)
        download_file(_cdna_url(self.release), gz, force=force)
        fa = _local_cdna_fasta(self.release)
        if not os.path.isfile(fa) or force:
            safe_print(f"  [解压] {os.path.basename(gz)} …")
            with gzip.open(gz, "rb") as src, open(fa, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
            size_mb = os.path.getsize(fa) / (1024 * 1024)
            safe_print(f"  [完成] {os.path.basename(fa)} ({size_mb:.1f} MB)")
        if link_reference and not self.dry_run:
            ref_genome = os.path.join(_BASE_DIR, "data", "reference_genome.fasta")
            if os.path.isfile(ref_genome):
                os.remove(ref_genome)
            try:
                os.link(fa, ref_genome)
            except OSError:
                shutil.copy2(fa, ref_genome)
            safe_print(f"[BLAST] 全基因组参考库：{ref_genome}")
            safe_print("  提示：可在 .env 设置 REFERENCE_FASTA=data/reference_genome.fasta 启用全库 BLAST")
        return fa

    def enrich_genes(
        self,
        biotypes: Optional[Set[str]] = None,
        skip_existing: bool = True,
        limit: int = 0,
        resume: bool = True,
        workers: int = DEFAULT_ENRICH_WORKERS,
        skip_cdna: Optional[bool] = None,
        retry_empty_go: bool = False,
        go_backfill: bool = False,
    ):
        """REST 深度注释（蛋白编码基因约 2.5 万，支持并行 + 断点续传）。

        go_backfill=True：仅补 GO，跳过同源（减少超时）；
        「源库无 GO」记为无GO而非失败，避免无限重试。
        """
        if biotypes is None:
            biotypes = {"protein_coding"}
        if skip_cdna is None:
            skip_cdna = os.path.isfile(REFERENCE_GENOME)
        if go_backfill:
            workers = max(1, min(workers, 3))
        workers = max(1, min(workers, 8))

        done_ids: Set[str] = set()
        if resume and os.path.isfile(PROGRESS_FILE):
            with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
                done_ids = set(json.load(f).get("done", []))

        if retry_empty_go:
            actual_with_go = self._load_done_with_go()
            if go_backfill:
                confirmed_no_go = done_ids - actual_with_go
                done_ids = actual_with_go | confirmed_no_go
                self._save_progress(done_ids)
                safe_print(
                    f"\n[补跑] 有 GO {len(actual_with_go)} | "
                    f"已确认无 GO {len(confirmed_no_go)}（不再重试）"
                )
            else:
                stale = len(done_ids - actual_with_go)
                done_ids = actual_with_go
                self._save_progress(done_ids)
                safe_print(
                    f"\n[补跑] progress 已校正：移除 {stale} 条「标记完成但 GO 为空」"
                    f" | 真实已完成 {len(done_ids)}"
                )
            self.close()

        self.connect()
        conditions = ["gb.ensembl_id IS NOT NULL"]
        params: list = []
        if skip_existing:
            conditions.append("(g.n IS NULL OR g.n = 0)")
        if biotypes:
            # gene_function 来自 GTF（可靠）；expression_summary 可能被 Atlas/Bgee 表达导入覆盖
            bt_clauses = [
                "(gb.gene_function LIKE %s OR gb.expression_summary LIKE %s)" for _ in biotypes
            ]
            conditions.append("(" + " OR ".join(bt_clauses) + ")")
            for bt in biotypes:
                params.extend([f"%{bt}%", f"%{bt}%"])

        query = f"""
        SELECT gb.gene_id, gb.gene_name, gb.ensembl_id
        FROM gene_basic gb
        LEFT JOIN (SELECT gene_id, COUNT(*) AS n FROM gene_go GROUP BY gene_id) g
          ON gb.gene_id = g.gene_id
        WHERE {" AND ".join(conditions)}
        ORDER BY gb.gene_id
        """
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        self.close()

        pending = []
        for gene_id, gene_name, ensembl_id in rows:
            if ensembl_id in done_ids:
                continue
            pending.append((gene_id, gene_name, ensembl_id))
            if limit and len(pending) >= limit:
                break

        total = len(pending)
        resumed = len(done_ids)
        if resumed:
            safe_print(
                f"\n[深度注释] 续传：已完成 {resumed} | 本次待处理 {total} | "
                f"workers={workers} | skip_cdna={skip_cdna} | release-{self.release}"
            )
        else:
            safe_print(
                f"\n[深度注释] 待处理 {total} 个基因 | workers={workers} | "
                f"skip_cdna={skip_cdna} | 限速 {1/REQUEST_INTERVAL:.0f} req/s | release-{self.release}"
            )
        if total == 0:
            return

        clear_api_stop()
        progress_lock = threading.Lock()
        stats = {"ok": 0, "empty": 0, "fail": 0, "done": 0}
        t0 = time.time()
        fasta_lock = threading.Lock()
        pool = None
        interrupted = False
        pending_futs: Set = set()

        def _db_connect():
            import pymysql
            return pymysql.connect(
                host=DB_CONFIG["host"],
                port=DB_CONFIG["port"],
                user=DB_CONFIG["user"],
                password=DB_CONFIG["password"],
                database=DB_CONFIG["database"],
                charset=DB_CONFIG["charset"],
                autocommit=False,
            )

        def _replace_children(conn, gene_id: int, go_terms, homologs, refs):
            with conn.cursor() as cur:
                if go_backfill:
                    cur.execute("DELETE FROM gene_go WHERE gene_id=%s", (gene_id,))
                    for t in go_terms:
                        cur.execute(
                            "INSERT INTO gene_go (gene_id, go_term, go_category) VALUES (%s,%s,%s)",
                            (gene_id, t["go_term"], t.get("go_category")),
                        )
                    return
                for table in ("gene_go", "homology_gene", "gene_reference"):
                    cur.execute(f"DELETE FROM {table} WHERE gene_id=%s", (gene_id,))
                for t in go_terms:
                    cur.execute(
                        "INSERT INTO gene_go (gene_id, go_term, go_category) VALUES (%s,%s,%s)",
                        (gene_id, t["go_term"], t.get("go_category")),
                    )
                for h in homologs:
                    cur.execute(
                        "INSERT INTO homology_gene (gene_id, species, homo_gene_name, homology_score) VALUES (%s,%s,%s,%s)",
                        (gene_id, h["species"], h["homo_gene_name"], h.get("homology_score")),
                    )
                for r in refs:
                    cur.execute(
                        "INSERT INTO gene_reference (gene_id, pubmed_id, title, journal, publish_year) VALUES (%s,%s,%s,%s,%s)",
                        (gene_id, r.get("pubmed_id"), r.get("title"), r.get("journal"), r.get("publish_year")),
                    )

        def _process_one(item: Tuple[int, str, str]) -> Tuple[str, str]:
            """返回 (go|empty|error, ensembl_id)。"""
            if api_stop_requested():
                return "error", item[2]
            gene_id, gene_name, ensembl_id = item
            conn = _db_connect()
            try:
                lookup = _api_get(
                    f"/lookup/id/{ensembl_id}",
                    {"expand": 1} if not skip_cdna else None,
                )
                if not lookup:
                    if skip_cdna:
                        lookup = {}
                    else:
                        conn.close()
                        return "error", ensembl_id

                go_terms, zfin_id, refs = fetch_annotation_bundle(ensembl_id)
                homologs: List = []
                if not go_backfill:
                    homologs = fetch_homology_ensembl(ensembl_id)

                cdna = None
                if not skip_cdna:
                    cdna = fetch_cdna_from_lookup(lookup)

                desc = (lookup.get("description") or "")[:2000]
                if desc or zfin_id:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE gene_basic SET gene_function=%s, zfin_id=COALESCE(%s,zfin_id) WHERE gene_id=%s",
                            (desc or None, zfin_id, gene_id),
                        )

                _replace_children(conn, gene_id, go_terms, homologs, refs)
                conn.commit()

                if cdna:
                    with fasta_lock:
                        GeneCollector(dry_run=False)._append_fasta(gene_name, ensembl_id, cdna)

                conn.close()
                if go_terms:
                    return "go", ensembl_id
                if go_backfill:
                    return "empty", ensembl_id
                if homologs or refs or zfin_id:
                    return "go", ensembl_id
                return "empty", ensembl_id
            except Exception as e:
                conn.rollback()
                conn.close()
                logger.warning(f"[深度注释] {gene_name}: {e}")
                return "error", ensembl_id

        def _maybe_log():
            with progress_lock:
                i = stats["done"]
                ok = stats["ok"]
                empty = stats["empty"]
                fail = stats["fail"]
            if i == 1 or i % 10 == 0 or i == total:
                elapsed = time.time() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (total - i) / rate / 3600 if rate > 0 else 0
                global_i = resumed + i
                global_total = resumed + total
                if go_backfill:
                    safe_print(
                        f"[{global_i}/{global_total}] 有GO{ok} 无GO{empty} 失败{fail}  "
                        f"{rate * 3600:.0f} 基因/h  预计剩余 {eta:.1f}h"
                    )
                else:
                    safe_print(
                        f"[{global_i}/{global_total}] 本次成功{ok} 无GO{empty} 失败{fail}  "
                        f"{rate * 3600:.0f} 基因/h  预计剩余 {eta:.1f}h"
                    )

        def _record_result(kind: str, eid: str):
            with progress_lock:
                stats["done"] += 1
                if kind == "go":
                    stats["ok"] += 1
                    done_ids.add(eid)
                elif kind == "empty":
                    stats["empty"] += 1
                    done_ids.add(eid)
                else:
                    stats["fail"] += 1
                if stats["ok"] and stats["ok"] % 20 == 0:
                    self._save_progress(done_ids)
            _maybe_log()

        safe_print(
            f"  已启动 {workers} worker，每完成 10 个基因汇报；"
            f"{'GO补全模式：跳过同源；' if go_backfill else ''}"
            f"单基因 timeout 会自动重试，偶发警告可忽略"
        )

        try:
            if workers == 1:
                for item in pending:
                    if api_stop_requested():
                        break
                    kind, eid = _process_one(item)
                    _record_result(kind, eid)
            else:
                pool = ThreadPoolExecutor(max_workers=workers)
                futures = [pool.submit(_process_one, item) for item in pending]
                pending_futs = set(futures)
                while pending_futs and not api_stop_requested():
                    done, pending_futs = wait(
                        pending_futs, timeout=1.0, return_when=FIRST_COMPLETED,
                    )
                    for fut in done:
                        kind, eid = fut.result()
                        _record_result(kind, eid)
        except KeyboardInterrupt:
            interrupted = True
            request_api_stop()
            safe_print("\n[中断] 正在停止 worker（最多等待当前 API 超时）…")
            if pool is not None:
                for fut in pending_futs:
                    fut.cancel()
                pool.shutdown(wait=False, cancel_futures=True)
        finally:
            if pool is not None and not interrupted:
                pool.shutdown(wait=True)
            clear_api_stop()
            self._save_progress(done_ids)

        if interrupted:
            raise KeyboardInterrupt

        ok, empty, fail = stats["ok"], stats["empty"], stats["fail"]
        if ok and not skip_cdna:
            GeneCollector(dry_run=False).merge_fasta_for_blast()
        safe_print(
            f"\n[深度注释完成] 有GO {ok} | 无GO {empty} | 失败 {fail} | 累计 {len(done_ids)}"
        )

    def _save_progress(self, done_ids: Set[str]):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump({"release": self.release, "done": sorted(done_ids)}, f)


def run_full_collection(
    release: int = 0,
    biotype: str = "all",
    skip_existing: bool = True,
    force_download: bool = False,
    with_cdna: bool = True,
    enrich: bool = False,
    enrich_only: bool = False,
    enrich_limit: int = 0,
    enrich_workers: int = DEFAULT_ENRICH_WORKERS,
    enrich_cdna: bool = False,
    enrich_retry_failed: bool = False,
    dry_run: bool = False,
):
    gc = GenomeCollector(release=release, dry_run=dry_run)
    bt = biotype_filter_from_arg(biotype)

    safe_print("=" * 60)
    safe_print("  斑马鱼全基因组基因采集")
    safe_print(f"  Ensembl release-{gc.release} · {SPECIES} · {ASSEMBLY}")
    safe_print("=" * 60)

    try:
        if not enrich_only:
            count = gc.import_catalog(
                biotypes=bt,
                skip_existing=skip_existing,
                force_download=force_download,
            )
            safe_print(f"[阶段1] 基因目录入库完成，共 {count} 条")

            if with_cdna and not dry_run:
                safe_print("\n[阶段2] 下载全量 cDNA …")
                gc.download_full_cdna(force=force_download)

        if enrich and not dry_run:
            enrich_bt = {"protein_coding"} if biotype == "all" else (bt or {"protein_coding"})
            safe_print("\n[阶段3] REST 深度注释（GO/同源/文献）…")
            skip_cdna = False if enrich_cdna else None
            if skip_cdna is not False and os.path.isfile(REFERENCE_GENOME):
                safe_print("  已检测到全量 cDNA，跳过逐基因序列抓取（BLAST 质量不变）")
            safe_print(
                f"  并行 workers={enrich_workers}，限速约 {1/REQUEST_INTERVAL:.0f} req/s；"
                "Ctrl+C 可中断（约 25s 内响应，进度自动保存）"
            )
            gc.enrich_genes(
                biotypes=enrich_bt,
                skip_existing=True,
                limit=enrich_limit,
                resume=True,
                workers=enrich_workers,
                skip_cdna=skip_cdna,
                retry_empty_go=enrich_retry_failed,
            )
    finally:
        gc.close()

    safe_print("\n" + "=" * 60)
    safe_print("  全基因组采集流程结束")
    safe_print("=" * 60)
