# -*- coding: utf-8 -*-
"""
斑马鱼基因数据一键采集（Ensembl REST API → MySQL + cDNA FASTA）

采集内容：基础信息、GO、人类/小鼠同源、PubMed 文献、转录本序列（供 BLAST）
"""
import bootstrap  # noqa: F401

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

from config import DB_CONFIG, REFERENCE_FASTA, _BASE_DIR
from common_utils import logger, safe_print

SPECIES = "danio_rerio"
ENSEMBL_BASE = os.getenv("ENSEMBL_REST_URL", "https://rest.ensembl.org")
REQUEST_INTERVAL = float(os.getenv("ENSEMBL_REQUEST_INTERVAL", "0.10"))
CDNA_FASTA = os.path.join(_BASE_DIR, "data", "reference_cdna.fasta")
REFERENCE_GENOME = os.path.join(_BASE_DIR, "data", "reference_genome.fasta")

_rate_lock = threading.Lock()
_last_api_time = 0.0
_stop_event = threading.Event()
API_TIMEOUT = int(os.getenv("ENSEMBL_API_TIMEOUT", "25"))
HOMOLOGY_TIMEOUT = int(os.getenv("ENSEMBL_HOMOLOGY_TIMEOUT", "45"))
NCBI_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def clear_api_stop():
    _stop_event.clear()


def request_api_stop():
    _stop_event.set()


def api_stop_requested() -> bool:
    return _stop_event.is_set()


def _rate_wait() -> bool:
    """全进程共享限速；返回 True 表示收到停止信号。"""
    global _last_api_time
    if _stop_event.is_set():
        return True
    with _rate_lock:
        now = time.monotonic()
        wait = REQUEST_INTERVAL - (now - _last_api_time)
        if wait > 0:
            if _stop_event.wait(wait):
                return True
        _last_api_time = time.monotonic()
    return _stop_event.is_set()

# 斑马鱼常染色体（GRCz11）
CHROMOSOMES = [str(i) for i in range(1, 26)] + ["MT"]


def _api_get(
    path: str,
    params: dict = None,
    retries: int = 3,
    silent: bool = False,
    timeout: int = 0,
) -> Optional[dict | list]:
    if _stop_event.is_set():
        return None
    url = ENSEMBL_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Content-Type": "application/json", "User-Agent": "ZebrafishGeneQA/1.0"}
    req_timeout = timeout or API_TIMEOUT
    for attempt in range(retries):
        if _stop_event.is_set():
            return None
        try:
            if _rate_wait():
                return None
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=req_timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if _stop_event.is_set():
                return None
            if e.code == 429:
                if _stop_event.wait(2 ** attempt):
                    return None
                continue
            if e.code in (400, 404):
                return None
            if not silent:
                logger.warning(f"[采集] API {e.code}: {path}")
            return None
        except KeyboardInterrupt:
            request_api_stop()
            raise
        except Exception as e:
            if _stop_event.is_set():
                return None
            if not silent:
                logger.warning(f"[采集] 请求失败 {path}: {e}")
            if _stop_event.wait(1):
                return None
    return None


def lookup_symbol(symbol: str, species: str = SPECIES, *, silent: bool = False,
                  timeout: int = 0, retries: int = 3) -> Optional[dict]:
    """`timeout` / `retries`：交互式调用可传小值，避免用户等 3×25s。"""
    sym = symbol.strip()
    if not sym:
        return None
    return _api_get(
        f"/lookup/symbol/{species}/{urllib.parse.quote(sym)}",
        {"expand": 1},
        silent=silent, timeout=timeout, retries=retries,
    )


def lookup_by_xref(symbol: str, species: str = SPECIES, *,
                   timeout: int = 0, retries: int = 3) -> Optional[dict]:
    """通过 Ensembl 外部符号/xref 解析常用别名（如 hsp70→hsp70.1、vegfa→vegfaa）。"""
    sym = symbol.strip()
    if not sym:
        return None
    xrefs = _api_get(f"/xrefs/symbol/{species}/{urllib.parse.quote(sym)}",
                     silent=True, timeout=timeout, retries=retries)
    if not isinstance(xrefs, list):
        return None
    gene_id = next((x.get("id") for x in xrefs if x.get("type") == "gene" and x.get("id")), None)
    if not gene_id:
        return None
    return _api_get(f"/lookup/id/{gene_id}", {"expand": 1}, silent=True,
                    timeout=timeout, retries=retries)


def resolve_lookup(symbol: str, species: str = SPECIES, *,
                   timeout: int = 0, retries: int = 3) -> Optional[dict]:
    """批量采集默认 3 次重试；工具页传 timeout=6, retries=1 做有界回退。"""
    lookup = lookup_symbol(symbol, species=species, timeout=timeout, retries=retries)
    if lookup:
        return lookup
    return lookup_by_xref(symbol, species=species, timeout=timeout, retries=retries)


def _parse_xrefs_payload(data: list) -> Tuple[List[dict], Optional[str], List[dict]]:
    """一次 xrefs 响应解析 GO / ZFIN / PubMed。"""
    terms = []
    seen_go = set()
    zfin_id = None
    refs = []
    for item in data:
        db = (item.get("dbname") or "").lower()
        primary = item.get("primary_id") or item.get("display_id") or ""
        if db == "go":
            go_id = primary
            desc = item.get("description") or ""
            label = f"{go_id} {desc}".strip()[:256]
            if label and label not in seen_go:
                seen_go.add(label)
                terms.append({"go_term": label, "go_category": None})
        elif "zfin" in db and not zfin_id:
            zfin_id = str(item.get("display_id") or primary)[:32]
        elif db in ("pubmed", "pubmed_gene") and primary:
            title = (item.get("description") or item.get("info") or "")[:512]
            refs.append({"pubmed_id": str(primary)[:16], "title": title, "journal": None, "publish_year": None})
    return terms[:30], zfin_id, refs[:5]


def fetch_annotation_bundle(ensembl_id: str) -> Tuple[List[dict], Optional[str], List[dict]]:
    """单次 /xrefs/id 调用返回 GO、ZFIN、PubMed（避免重复请求）。"""
    data = _api_get(f"/xrefs/id/{ensembl_id}", {"all_levels": 1}, timeout=API_TIMEOUT)
    if not isinstance(data, list):
        return [], None, []
    return _parse_xrefs_payload(data)


def fetch_ncbi_go_terms(ncbi_gene_id: str) -> List[dict]:
    """NCBI Gene efetch → GO（Ensembl 无 GO 时回退）。"""
    gid = str(ncbi_gene_id or "").strip()
    if not gid.isdigit():
        return []
    import xml.etree.ElementTree as ET

    params = {
        "db": "gene",
        "id": gid,
        "retmode": "xml",
        "email": os.getenv("NCBI_EMAIL", "zebrafish-gene-qa@local.dev"),
    }
    api_key = os.getenv("NCBI_API_KEY", "").strip()
    if api_key:
        params["api_key"] = api_key
    url = f"{NCBI_EUTILS}/efetch.fcgi?{urllib.parse.urlencode(params)}"
    headers = {"User-Agent": "ZebrafishGeneQA/1.0"}
    try:
        if _rate_wait():
            return []
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            root = ET.fromstring(resp.read())
    except Exception as e:
        logger.warning(f"[采集] NCBI GO efetch gene:{gid}: {e}")
        return []

    terms: List[dict] = []
    seen: set = set()
    for onto in root.iter("Gene-ontology"):
        go_id = ""
        label = ""
        for child in onto:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            text = (child.text or "").strip()
            if tag == "Gene-ontology-id":
                go_id = text
            elif tag == "Gene-ontology-term":
                label = text
        if not go_id and not label:
            continue
        term = f"{go_id} {label}".strip()[:256]
        if term and term not in seen:
            seen.add(term)
            terms.append({"go_term": term, "go_category": None})
    return terms[:30]


def fetch_go_terms(ensembl_id: str) -> List[dict]:
    terms, _, _ = fetch_annotation_bundle(ensembl_id)
    return terms


_HOMOLOGY_SPECIES = {"homo_sapiens": "Human", "mus_musculus": "Mouse"}


def fetch_homology_ensembl(ensembl_id: str, species: str = SPECIES) -> List[dict]:
    """Ensembl 直系同源：分物种各查一次（响应小、不易超时）。"""
    rows: List[dict] = []
    for species_key, label in _HOMOLOGY_SPECIES.items():
        data = _api_get(
            f"/homology/id/{species}/{ensembl_id}",
            {"type": "orthologues", "target_species": species_key},
            timeout=HOMOLOGY_TIMEOUT,
            retries=2,
        )
        if not isinstance(data, dict):
            continue
        best = None
        best_score = -1.0
        for block in data.get("data") or []:
            for h in block.get("homologies") or []:
                htype = (h.get("type") or "").lower()
                if "ortholog" not in htype:
                    continue
                target = h.get("target") or {}
                if (target.get("species") or "") != species_key:
                    continue
                name = target.get("display_name")
                if not name and target.get("id"):
                    hit = _api_get(f"/lookup/id/{target['id']}", silent=True)
                    if isinstance(hit, dict):
                        name = hit.get("display_name")
                name = name or target.get("id") or ""
                score = (
                    h.get("dn_alignment_identity")
                    or h.get("perc_id")
                    or target.get("perc_id")
                )
                try:
                    score_f = float(score) if score is not None else 0.0
                except (TypeError, ValueError):
                    score_f = 0.0
                if score_f > best_score:
                    best_score = score_f
                    best = {
                        "species": label,
                        "homo_gene_name": str(name)[:64],
                        "homology_score": score_f or None,
                    }
        if best:
            rows.append(best)
    return rows


def fetch_homology(symbol: str) -> List[dict]:
    """单基因采集：先解析 Ensembl ID 再查同源。"""
    lookup = resolve_lookup(symbol)
    if lookup and lookup.get("id"):
        rows = fetch_homology_ensembl(lookup["id"])
        if rows:
            return rows
    rows = []
    for species, label in (("homo_sapiens", "Human"), ("mus_musculus", "Mouse")):
        hit = lookup_symbol(symbol, species=species, silent=True)
        if not hit:
            continue
        name = hit.get("display_name") or symbol
        rows.append({
            "species": label,
            "homo_gene_name": str(name)[:64],
            "homology_score": 85.0,
        })
    return rows


def fetch_cdna_from_lookup(lookup: dict, *, max_transcripts: int = 3) -> Optional[str]:
    """取 cDNA：优先 canonical / 最长转录本，限制探测条数避免交互接口卡死。"""
    transcripts = list(lookup.get("Transcript") or [])
    if not transcripts:
        return None
    # canonical 优先
    transcripts.sort(key=lambda t: (0 if t.get("is_canonical") else 1, -(t.get("length") or 0)))
    best_seq = None
    best_len = 0
    for tr in transcripts[: max(1, int(max_transcripts))]:
        tid = tr.get("id")
        if not tid:
            continue
        data = _api_get(f"/sequence/id/{tid}", {"type": "cdna"}, retries=2, timeout=min(API_TIMEOUT, 20))
        if isinstance(data, dict):
            seq = (data.get("seq") or "").upper()
            if len(seq) > best_len:
                best_len = len(seq)
                best_seq = seq
                # 已拿到合理长度即可返回，不必扫完
                if best_len >= 300:
                    break
    return best_seq if best_len >= 60 else None


def fetch_xrefs(ensembl_id: str) -> tuple:
    """返回 (zfin_id, pubmed_refs)"""
    _, zfin_id, refs = fetch_annotation_bundle(ensembl_id)
    return zfin_id, refs


def genes_on_chromosome(chrom: str, limit: int = 200) -> List[str]:
    """按染色体扫描基因名（仅 protein_coding）。"""
    chunk = 5_000_000
    max_start = 75_000_000 if chrom != "MT" else 20_000
    symbols = []
    start = 1
    while start <= max_start and len(symbols) < limit:
        end = min(start + chunk - 1, max_start)
        region = f"{chrom}:{start}-{end}"
        data = _api_get(f"/overlap/region/{SPECIES}/{region}", {"feature": "gene"})
        if isinstance(data, list):
            for g in data:
                if g.get("biotype") != "protein_coding":
                    continue
                name = g.get("external_name") or ""
                if name and name not in symbols:
                    symbols.append(name)
                if len(symbols) >= limit:
                    break
        start = end + 1
    return symbols[:limit]


def _sql_escape(val) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return str(val)
    return "'" + str(val).replace("\\", "\\\\").replace("'", "''") + "'"


class GeneCollector:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.conn = None
        self.stats = {"ok": 0, "skip": 0, "fail": 0, "fasta": 0}

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

    def _get_gene_id(self, gene_name: str) -> Optional[int]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT gene_id FROM gene_basic WHERE gene_name=%s LIMIT 1",
                (gene_name.lower(),),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def _upsert_basic(self, info: dict) -> Optional[int]:
        if self.dry_run:
            return 0
        sql = """
        INSERT INTO gene_basic
          (gene_name, gene_symbol, ensembl_id, zfin_id, chromosome, gene_function,
           interpro_domain, uniprot_id, expression_summary)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          gene_symbol=VALUES(gene_symbol), ensembl_id=VALUES(ensembl_id),
          zfin_id=COALESCE(VALUES(zfin_id), zfin_id), chromosome=VALUES(chromosome),
          gene_function=VALUES(gene_function), expression_summary=VALUES(expression_summary)
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, (
                info["gene_name"], info.get("gene_symbol"), info.get("ensembl_id"),
                info.get("zfin_id"), info.get("chromosome"), info.get("gene_function"),
                info.get("interpro_domain"), info.get("uniprot_id"), info.get("expression_summary"),
            ))
            gid = self._get_gene_id(info["gene_name"])
        return gid

    def _replace_children(self, gene_id: int, go_terms, homologs, refs):
        if self.dry_run or not gene_id:
            return
        with self.conn.cursor() as cur:
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

    def collect_one(self, symbol: str, skip_existing: bool = False) -> bool:
        symbol = symbol.strip()
        if not symbol:
            return False
        if skip_existing and not self.dry_run:
            with self.conn.cursor() as cur:
                cur.execute("SELECT 1 FROM gene_basic WHERE gene_name=%s", (symbol.lower(),))
                if cur.fetchone():
                    self.stats["skip"] += 1
                    safe_print(f"  跳过（已存在）：{symbol}")
                    return True

        lookup = resolve_lookup(symbol)
        if not lookup or lookup.get("biotype") not in (None, "protein_coding", "protein_coding_CDS_not_defined"):
            safe_print(f"  [失败] 未找到或非正常蛋白编码基因：{symbol}")
            self.stats["fail"] += 1
            return False

        ensembl_id = lookup.get("id")
        official = (lookup.get("display_name") or symbol).lower()
        gene_name = official
        desc = (lookup.get("description") or lookup.get("biotype") or "")[:2000]
        if official != symbol.lower():
            safe_print(f"  [别名] {symbol} → {official}")
        chrom = str(lookup.get("seq_region_name") or lookup.get("chromosome") or "")

        go_terms, zfin_id, refs = fetch_annotation_bundle(ensembl_id)
        homologs = fetch_homology_ensembl(ensembl_id)
        skip_cdna = os.path.isfile(REFERENCE_GENOME)
        cdna = None if skip_cdna else fetch_cdna_from_lookup(lookup)

        info = {
            "gene_name": gene_name,
            "gene_symbol": symbol if symbol.lower() != gene_name else None,
            "ensembl_id": ensembl_id,
            "zfin_id": zfin_id,
            "chromosome": chrom,
            "gene_function": desc,
            "interpro_domain": None,
            "uniprot_id": None,
            "expression_summary": f"Ensembl GRCz11 · {lookup.get('biotype', '')}",
        }

        if self.dry_run:
            safe_print(f"  [预览] {gene_name} | {ensembl_id} | GO={len(go_terms)} | 同源={len(homologs)}")
            self.stats["ok"] += 1
            return True

        gid = self._upsert_basic(info)
        self._replace_children(gid, go_terms, homologs, refs)
        self.conn.commit()

        if cdna:
            self._append_fasta(gene_name, ensembl_id, cdna)
            self.stats["fasta"] += 1

        safe_print(f"  [成功] {gene_name} | GO {len(go_terms)} | 同源 {len(homologs)} | 文献 {len(refs)}")
        self.stats["ok"] += 1
        return True

    def _append_fasta(self, gene_name: str, ensembl_id: str, seq: str):
        os.makedirs(os.path.dirname(CDNA_FASTA), exist_ok=True)
        with open(CDNA_FASTA, "a", encoding="utf-8") as f:
            f.write(f">{ensembl_id}|{gene_name}\n")
            for i in range(0, len(seq), 80):
                f.write(seq[i:i + 80] + "\n")

    def merge_fasta_for_blast(self):
        """合并采集 cDNA 到 data/reference.fasta（仅 ETL 抽样，不是 Hub BLAST 库）。

        Hub BLAST 使用 BLAST_FASTA / grcz12tu_cds.fna（GRCz12tu），不要把本文件当现网库。
        """
        os.makedirs(os.path.dirname(REFERENCE_FASTA), exist_ok=True)
        parts = []
        if os.path.isfile(REFERENCE_FASTA):
            with open(REFERENCE_FASTA, "r", encoding="utf-8") as f:
                parts.append(f.read().strip())
        if os.path.isfile(CDNA_FASTA):
            with open(CDNA_FASTA, "r", encoding="utf-8") as f:
                parts.append(f.read().strip())
        if not parts:
            return
        merged = "\n".join(p for p in parts if p) + "\n"
        with open(REFERENCE_FASTA, "w", encoding="utf-8") as f:
            f.write(merged)
        safe_print(
            f"[成功] 已更新 ETL 抽样 FASTA：{REFERENCE_FASTA} "
            "（Hub BLAST 请用 BLAST_FASTA=grcz12tu_cds.fna，勿用本文件）"
        )

    def collect_symbols(self, symbols: List[str], skip_existing: bool = False):
        total = len(symbols)
        safe_print(f"\n[采集] 共 {total} 个基因，数据源 Ensembl {SPECIES}\n")
        for i, sym in enumerate(symbols, 1):
            safe_print(f"[{i}/{total}] {sym}")
            try:
                self.collect_one(sym, skip_existing=skip_existing)
            except Exception as e:
                self.stats["fail"] += 1
                safe_print(f"  [异常] {sym}: {e}")
                if self.conn:
                    self.conn.rollback()
        if not self.dry_run:
            self.merge_fasta_for_blast()
        safe_print(
            f"\n[完成] 成功 {self.stats['ok']} | 跳过 {self.stats['skip']} | "
            f"失败 {self.stats['fail']} | cDNA {self.stats['fasta']} 条"
        )


def load_gene_list_from_file(path: str) -> List[str]:
    genes = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            genes.append(line.split()[0])
    return genes


PRESET_CLASSIC = [
    "tp53", "hsp70", "hsp70l", "sox9b", "nanog", "myl7", "actb2", "shha", "bmp4", "wnt11",
    "fgf8a", "runx1", "gata1", "ntla", "pax6a", "otx2", "hoxb1b", "myod1", "col2a1a",
    "mitfa", "dmd", "scn1lab", "brca2", "ptch1", "igf1ra", "mtor", "mapk1", "cdx4",
    "foxd3", "rag1", "vegfa", "tnnt2a", "cxcr4a", "notch1a", "epha4a",
]
