# -*- coding: utf-8 -*-
"""用 RefSeq GCF_049306965.1（GRCz12tu）注释建对照，并以该注释基因为标准名。

标准名只取本条 RefSeq 组装上的 gene.symbol（feature_table，失败则退 GFF Name）。
gene_info 全字段（Symbol / LocusTag / Synonyms / dbXrefs / 命名权威符号 /
Other_designations）只用于对齐本地 gene_basic，不作为标准名来源。
ZFIN 官方 1:1（本地 GRCz11 Ensembl → ZFIN → NCBI GeneID）补改名位点。
不用本地 gene_basic.zfin_id。不覆盖 v1/v2 索引，不改 literature_paper。
"""
from __future__ import annotations

from core.entry import bootstrap_app

bootstrap_app()

import gzip
import os
import shutil
import subprocess
import urllib.request
from typing import Dict, Iterator, List, Optional

from common_utils import safe_print
from config import (
    _BASE_DIR,
    ENSEMBL_RELEASE,
    NCBI_ASSEMBLY_ACCESSION,
    NCBI_GENBANK_ACCESSION,
    NCBI_REFERENCE_ASSEMBLY,
    NCBI_REFERENCE_GENBANK,
    NCBI_REFERENCE_REFSEQ,
    NCBI_REFERENCE_STRAIN,
    REFERENCE_ASSEMBLY,
    TAXON_ID,
)
from gene_id_mapper import ensure_alias_tables

ASM_DIR = f"{NCBI_REFERENCE_REFSEQ}_{NCBI_REFERENCE_ASSEMBLY}"
FTP_ROOTS = (
    "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/049/306/965/" + ASM_DIR + "/",
    "https://ftp.ncbi.nih.gov/genomes/all/GCF/049/306/965/" + ASM_DIR + "/",
)
GENE_INFO_URL = (
    "https://ftp.ncbi.nlm.nih.gov/gene/DATA/GENE_INFO/"
    "Non-mammalian_vertebrates/Danio_rerio.gene_info.gz"
)
FEATURE_NAME = f"{ASM_DIR}_feature_table.txt.gz"
GFF_NAME = f"{ASM_DIR}_genomic.gff.gz"
CACHE_DIR = os.environ.get(
    "NCBI_CACHE_DIR",
    os.path.join(os.environ.get("ZEBRA_RUNTIME", os.path.join(_BASE_DIR, "data")), "ncbi_cache"),
)
INFO_CACHE = os.path.join(CACHE_DIR, "Danio_rerio.gene_info.gz")
FEATURE_CACHE = os.path.join(CACHE_DIR, FEATURE_NAME)
GFF_CACHE = os.path.join(CACHE_DIR, GFF_NAME)
ZFIN_GENE_URL = "https://zfin.org/downloads/gene.txt"
ZFIN_ENS_URL = "https://zfin.org/downloads/ensembl_1_to_1.txt"
ZFIN_ALIAS_URL = "https://zfin.org/downloads/aliases.txt"
ZFIN_GENE_CACHE = os.path.join(CACHE_DIR, "zfin_gene.txt")
ZFIN_ENS_CACHE = os.path.join(CACHE_DIR, "zfin_ensembl_1_to_1.txt")
ZFIN_ALIAS_CACHE = os.path.join(CACHE_DIR, "zfin_aliases.txt")
BATCH = 400
UA = "ZebrafishGeneQA/1.0 (RefSeq GCF_049306965.1 xref)"
REFSEQ_SOURCE = f"refseq_{NCBI_REFERENCE_REFSEQ}"


def _client():
    from mysql_client import get_mysql_client
    return get_mysql_client()


def _esc(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("'", "''")


def _sql_val(v: Optional[str]) -> str:
    if not v:
        return "NULL"
    return f"'{_esc(str(v))}'"


def _download(urls, dest: str, min_bytes: int) -> str:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest) and os.path.getsize(dest) >= min_bytes:
        safe_print(f"  缓存 {os.path.basename(dest)} {os.path.getsize(dest) // 1024} KB")
        return dest
    tmp = dest + ".part"
    last_err = None
    url_list = [urls] if isinstance(urls, str) else list(urls)
    for url in url_list:
        safe_print(f"  下载 {url}")
        try:
            curl = shutil.which("curl")
            if curl:
                cmd = [
                    curl, "-fsSL", "--retry", "8", "--retry-delay", "4",
                    "--continue-at", "-", "--connect-timeout", "30",
                    "--max-time", "1800", "-A", UA, "-o", tmp, url,
                ]
                subprocess.check_call(cmd)
            else:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=900) as resp, open(tmp, "wb") as fh:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
            if os.path.isfile(tmp) and os.path.getsize(tmp) >= min_bytes:
                os.replace(tmp, dest)
                safe_print(f"  已存 {os.path.basename(dest)} {os.path.getsize(dest) // 1024} KB")
                return dest
            last_err = RuntimeError(f"文件过小 {os.path.getsize(tmp) if os.path.isfile(tmp) else 0}")
        except Exception as e:
            last_err = e
            safe_print(f"  [警告] {e}")
    raise RuntimeError(f"下载失败 {dest}: {last_err}")


def _ensure_assembly_tables() -> bool:
    path = os.path.join(_BASE_DIR, "sql", "assembly_xref_migration.sql")
    if not os.path.isfile(path):
        safe_print("[错误] 缺少 assembly_xref_migration.sql")
        return False
    ddl = open(path, encoding="utf-8").read()
    statements = [s.strip() for s in ddl.split(";") if s.strip() and not s.strip().startswith("--")]
    db = next(_client().get_db())
    try:
        from sqlalchemy import text
        for stmt in statements:
            if stmt.upper().startswith("USE "):
                continue
            db.execute(text(stmt))
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        safe_print(f"[错误] 建表失败: {e}")
        return False
    finally:
        db.close()


def _seed_registry() -> None:
    from sqlalchemy import text
    db = next(_client().get_db())
    sql = text(
        "INSERT INTO assembly_registry "
        "(assembly_name, ncbi_refseq_accession, ncbi_genbank_accession, strain, "
        "is_local_anchor, is_ncbi_reference, ensembl_assembly, ensembl_release, notes) "
        "VALUES (:a,:r,:g,:s,:la,:nr,:ea,:er,:n) "
        "ON DUPLICATE KEY UPDATE "
        "ncbi_refseq_accession=VALUES(ncbi_refseq_accession), "
        "ncbi_genbank_accession=VALUES(ncbi_genbank_accession), "
        "strain=VALUES(strain), is_local_anchor=VALUES(is_local_anchor), "
        "is_ncbi_reference=VALUES(is_ncbi_reference), notes=VALUES(notes)"
    )
    rows = [
        dict(
            a=REFERENCE_ASSEMBLY, r=NCBI_ASSEMBLY_ACCESSION, g=NCBI_GENBANK_ACCESSION,
            s="Tuebingen", la=1, nr=0, ea=REFERENCE_ASSEMBLY, er=ENSEMBL_RELEASE,
            n="本系统 gene_basic 锚定",
        ),
        dict(
            a=NCBI_REFERENCE_ASSEMBLY, r=NCBI_REFERENCE_REFSEQ, g=NCBI_REFERENCE_GENBANK,
            s=NCBI_REFERENCE_STRAIN, la=0, nr=1, ea="GRCz12", er=None,
            n=f"标准名来源 RefSeq {NCBI_REFERENCE_REFSEQ} https://www.ncbi.nlm.nih.gov/datasets/genome/{NCBI_REFERENCE_REFSEQ}/",
        ),
    ]
    try:
        for row in rows:
            db.execute(sql, row)
        db.commit()
    finally:
        db.close()


def _uniq(buckets: Dict[str, set]) -> dict:
    return {k: next(iter(v)) for k, v in buckets.items() if len(v) == 1}


def _usable_key(raw: str, *, extra: bool = False) -> str:
    k = (raw or "").strip().lower()
    if not k or k in ("-", "n/a", "na", "none", "null"):
        return ""
    if extra:
        if " " in k or len(k) < 3:
            return ""
        if k.startswith(("hypothetical", "predicted", "putative", "novel ", "unnamed")):
            return ""
    return k


def _load_local_maps() -> dict:
    """索引 gene_basic 唯一 Ensembl/主名/符号。不用本地 zfin_id，不用旧 NCBI xref。"""
    from collections import defaultdict
    from sqlalchemy import text
    db = next(_client().get_db())
    ens_b: Dict[str, set] = defaultdict(set)
    name_b: Dict[str, set] = defaultdict(set)
    symbol_b: Dict[str, set] = defaultdict(set)
    id_name: Dict[int, str] = {}
    id_ens: Dict[int, str] = {}
    alias_b: Dict[str, set] = defaultdict(set)
    try:
        for r in db.execute(text(
            "SELECT gene_id, gene_name, gene_symbol, ensembl_id FROM gene_basic"
        )).mappings():
            gid = int(r["gene_id"])
            id_name[gid] = (r["gene_name"] or "").strip()
            if r["ensembl_id"]:
                ens = str(r["ensembl_id"]).split(".")[0].upper()
                id_ens[gid] = ens
                ens_b[ens].add(gid)
            if r["gene_name"]:
                name_b[str(r["gene_name"]).lower()].add(gid)
            if r["gene_symbol"]:
                symbol_b[str(r["gene_symbol"]).lower()].add(gid)
        try:
            for r in db.execute(text(
                "SELECT gene_id, alias FROM gene_alias "
                "WHERE alias IS NOT NULL AND alias<>'' AND alias<>'-'"
            )).mappings():
                a = _usable_key(str(r["alias"] or ""), extra=True)
                if a:
                    alias_b[a].add(int(r["gene_id"]))
        except Exception:
            pass
    finally:
        db.close()
    maps = {
        "ens": _uniq(ens_b),
        "name": _uniq(name_b),
        "symbol": _uniq(symbol_b),
        "alias": _uniq(alias_b),
        "id_name": id_name,
        "id_ens": id_ens,
    }
    safe_print(
        f"  唯一索引 Ensembl {len(maps['ens'])} / "
        f"gene_name {len(maps['name'])} / gene_symbol {len(maps['symbol'])} / "
        f"别名 {len(maps['alias'])} "
        f"（已丢弃重名 {len(ens_b)-len(maps['ens'])} Ensembl / "
        f"{len(name_b)-len(maps['name'])} gene_name）"
    )
    return maps


def _parse_dbxrefs(raw: str) -> dict:
    out = {"ensembl": "", "zfin": "", "hgnc": ""}
    for part in (raw or "").replace(",", "|").split("|"):
        part = part.strip()
        if ":" not in part:
            if part.upper().startswith("ENS"):
                out["ensembl"] = part.split(".")[0].upper()
            elif part.upper().startswith("ZDB-GENE"):
                out["zfin"] = part.upper()
            continue
        db, val = part.split(":", 1)
        db, val = db.strip(), val.strip()
        if "ensembl" in db.lower() or val.upper().startswith("ENS"):
            for tok in val.replace(";", " ").split():
                tok = tok.strip()
                if tok.upper().startswith("ENS"):
                    out["ensembl"] = tok.split(".")[0].upper()
                    break
        if db.upper() == "ZFIN" or val.upper().startswith("ZDB-GENE"):
            for tok in val.replace(";", " ").split():
                tok = tok.strip()
                if tok.upper().startswith("ZDB-"):
                    out["zfin"] = tok.upper()
                    break
            if not out["zfin"] and val.upper().startswith("ZDB-"):
                out["zfin"] = val.upper()
    return out


def _iter_gene_info(path: str) -> Iterator[dict]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        header = fh.readline()
        if not header.startswith("#"):
            fh.seek(0)
        for line in fh:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 14:
                continue
            if str(cols[0]) != str(TAXON_ID):
                continue
            symbol = (cols[2] or "").strip()
            if not symbol or symbol == "NEWENTRY":
                continue
            syns = [s.strip() for s in (cols[4] or "").split("|") if s.strip() and s.strip() != "-"]
            other = [s.strip() for s in (cols[13] or "").split("|") if s.strip() and s.strip() != "-"]
            x = _parse_dbxrefs(cols[5] or "")
            locus = (cols[3] or "").strip() if cols[3] not in ("-", "") else ""
            official = (cols[10] or "").strip() if len(cols) > 10 and cols[10] not in ("-", "") else ""
            full_name = (cols[11] or "").strip() if len(cols) > 11 and cols[11] not in ("-", "") else ""
            yield {
                "ncbi_gene_id": (cols[1] or "").strip(),
                "symbol": symbol,
                "locus_tag": locus,
                "synonyms": syns,
                "other": other,
                "ensembl": x["ensembl"],
                "zfin": x["zfin"],
                "chromosome": (cols[6] or "").strip() if cols[6] not in ("-", "") else "",
                "kind": (cols[9] or "").strip(),
                "official": official or symbol,
                "full_name": full_name,
            }


def _load_gene_info_by_id(path: str) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    n = 0
    for rec in _iter_gene_info(path):
        n += 1
        gid = rec.get("ncbi_gene_id") or ""
        if gid:
            out[gid] = rec
    safe_print(f"  gene_info {n} 条，按 GeneID {len(out)}（全字段用于对齐；标准名仍取 RefSeq feature_table）")
    return out


def _iter_tsv(path: str) -> Iterator[list]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip() or line.startswith("#"):
                continue
            yield line.rstrip("\n").split("\t")


def _load_zfin_maps() -> dict:
    """ZFIN 官方一对一：本地 GRCz11 Ensembl → ZFIN → NCBI GeneID。不用本地脏 zfin_id。"""
    from collections import defaultdict
    ens_b: Dict[str, set] = defaultdict(set)
    ens_label_b: Dict[str, set] = defaultdict(set)
    zfin_ncbi_b: Dict[str, set] = defaultdict(set)
    zfin_sym_b: Dict[str, set] = defaultdict(set)
    prev_b: Dict[str, set] = defaultdict(set)
    try:
        gene_path = _download(ZFIN_GENE_URL, ZFIN_GENE_CACHE, min_bytes=50_000)
        for cols in _iter_tsv(gene_path):
            if len(cols) < 4 or not str(cols[0]).upper().startswith("ZDB-GENE-"):
                continue
            zfin = cols[0].strip().upper()
            ncbi = (cols[3] or "").strip()
            sym = (cols[2] or "").strip().lower()
            if ncbi.isdigit():
                zfin_ncbi_b[zfin].add(ncbi)
            if sym:
                zfin_sym_b[sym].add(zfin)
    except Exception as e:
        safe_print(f"  [警告] ZFIN gene.txt：{e}")
    try:
        ens_path = _download(ZFIN_ENS_URL, ZFIN_ENS_CACHE, min_bytes=50_000)
        for cols in _iter_tsv(ens_path):
            if len(cols) < 4 or not str(cols[0]).upper().startswith("ZDB-GENE-"):
                continue
            zfin = cols[0].strip().upper()
            ens = (cols[3] or "").split(".")[0].upper()
            label = (cols[2] or "").strip().lower()
            if ens.startswith("ENS"):
                ens_b[ens].add(zfin)
                if label:
                    ens_label_b[ens].add(label)
    except Exception as e:
        safe_print(f"  [警告] ZFIN ensembl_1_to_1：{e}")
    try:
        alias_path = _download(ZFIN_ALIAS_URL, ZFIN_ALIAS_CACHE, min_bytes=50_000)
        for cols in _iter_tsv(alias_path):
            if len(cols) < 4 or not str(cols[0]).upper().startswith("ZDB-GENE-"):
                continue
            zfin = cols[0].strip().upper()
            prev = (cols[3] or "").strip().lower()
            if prev and prev != "-":
                prev_b[prev].add(zfin)
    except Exception as e:
        safe_print(f"  [警告] ZFIN aliases：{e}")
    maps = {
        "ens_to_zfin": _uniq(ens_b),
        "ens_to_label": _uniq(ens_label_b),
        "zfin_to_ncbi": _uniq(zfin_ncbi_b),
        "sym_to_zfin": _uniq(zfin_sym_b),
        "prev_to_zfin": _uniq(prev_b),
    }
    safe_print(
        f"  ZFIN Ensembl 1:1 {len(maps['ens_to_zfin'])} / "
        f"ZFIN→NCBI {len(maps['zfin_to_ncbi'])} / "
        f"符号 {len(maps['sym_to_zfin'])} / 曾用名 {len(maps['prev_to_zfin'])} / "
        f"Ensembl→现行符号 {len(maps['ens_to_label'])}"
    )
    return maps


def _info_key_index(info_by_id: Dict[str, dict], on_asm: set) -> dict:
    """gene_info 全字段倒排：只收这条 RefSeq 组装上的 GeneID，键冲突则丢弃。"""
    from collections import defaultdict
    primary: Dict[str, set] = defaultdict(set)
    extra: Dict[str, set] = defaultdict(set)
    zfin_b: Dict[str, set] = defaultdict(set)
    ens_b: Dict[str, set] = defaultdict(set)
    for geneid, info in info_by_id.items():
        if geneid not in on_asm:
            continue
        for key in (info.get("symbol"), info.get("official"), info.get("locus_tag")):
            k = _usable_key(key)
            if k:
                primary[k].add(geneid)
        extras = list(info.get("synonyms") or []) + list(info.get("other") or [])
        extras.append(info.get("full_name") or "")
        for key in extras:
            k = _usable_key(key, extra=True)
            if k:
                extra[k].add(geneid)
        zfin = (info.get("zfin") or "").strip().upper()
        if zfin.startswith("ZDB-GENE-"):
            zfin_b[zfin].add(geneid)
        ens = (info.get("ensembl") or "").split(".")[0].upper()
        if ens.startswith("ENS"):
            ens_b[ens].add(geneid)
    extra = {k: v for k, v in extra.items() if k not in primary}
    return {
        "primary": _uniq(primary),
        "extra": _uniq(extra),
        "zfin": _uniq(zfin_b),
        "ens": _uniq(ens_b),
    }


def _is_primary_unit(unit: str) -> bool:
    u = (unit or "").strip().lower()
    if not u:
        return True
    if "alt" in u or "patch" in u or "fix" in u:
        return False
    return True


def _iter_feature_table(path: str) -> Iterator[dict]:
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        header = None
        for line in fh:
            if not line.strip():
                continue
            if line.startswith("#"):
                header = [h.strip().lstrip("#").strip() for h in line.rstrip("\n").split("\t")]
                continue
            cols = line.rstrip("\n").split("\t")
            if header:
                row = {header[i]: (cols[i] if i < len(cols) else "") for i in range(len(header))}
            else:
                # NCBI 固定列：feature class assembly assembly_unit seq_type chromosome
                # genomic_accession start end strand product_accession non-redundant_refseq
                # related_accession name symbol GeneID locus_tag ...
                keys = [
                    "feature", "class", "assembly", "assembly_unit", "seq_type",
                    "chromosome", "genomic_accession", "start", "end", "strand",
                    "product_accession", "non-redundant_refseq", "related_accession",
                    "name", "symbol", "GeneID", "locus_tag",
                ]
                row = {keys[i]: (cols[i] if i < len(cols) else "") for i in range(len(keys))}
            if (row.get("feature") or "").strip().lower() != "gene":
                continue
            if not _is_primary_unit(row.get("assembly_unit") or ""):
                continue
            geneid = (row.get("GeneID") or "").strip()
            symbol = (row.get("symbol") or "").strip()
            if not symbol:
                symbol = (row.get("locus_tag") or "").strip()
            if not geneid and not symbol:
                continue
            start_s, end_s = (row.get("start") or "").strip(), (row.get("end") or "").strip()
            yield {
                "ncbi_gene_id": geneid,
                "symbol": symbol,
                "kind": (row.get("class") or "").strip(),
                "chromosome": (row.get("chromosome") or "").strip(),
                "start_pos": int(start_s) if start_s.isdigit() else None,
                "end_pos": int(end_s) if end_s.isdigit() else None,
                "strand": (row.get("strand") or "").strip()[:1],
            }


def _gff_genes(path: str) -> List[dict]:
    if not os.path.isfile(path) or os.path.getsize(path) < 1_000_000:
        return []
    out: Dict[str, dict] = {}
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line or line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 9 or cols[2] != "gene":
                continue
            attrs = {}
            for part in cols[8].split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    attrs[k.strip()] = v.strip()
            gid = ""
            for chunk in (attrs.get("Dbxref") or "").split(","):
                chunk = chunk.strip()
                if chunk.startswith("GeneID:"):
                    gid = chunk.split(":", 1)[1]
                    break
            symbol = attrs.get("Name") or attrs.get("gene") or attrs.get("locus_tag") or ""
            if not gid and not symbol:
                continue
            key = gid or f"sym:{symbol.lower()}"
            if key in out:
                continue
            out[key] = {
                "ncbi_gene_id": gid,
                "symbol": symbol,
                "kind": attrs.get("gene_biotype") or "",
                "chromosome": cols[0],
                "start_pos": int(cols[3]),
                "end_pos": int(cols[4]),
                "strand": cols[6],
            }
    safe_print(f"  GFF 基因 {len(out)}（{NCBI_REFERENCE_REFSEQ}）")
    return list(out.values())


def _assign_strict(genes: List[dict], info_by_id: Dict[str, dict], maps: dict, zfin_maps: dict) -> list:
    """把本地基因对齐到 GCF_049306965.1 的 GeneID，标准名=feature_table.symbol。

    对齐用完整 gene_info 以及 ZFIN Ensembl 一对一 → NCBI。
    不用本地 gene_basic.zfin_id（有错号）。一对多的键一律丢弃。
    """
    rec_by_ncbi = {
        (r.get("ncbi_gene_id") or "").strip(): r
        for r in genes if (r.get("ncbi_gene_id") or "").strip()
    }
    on_asm = set(rec_by_ncbi)
    info_idx = _info_key_index(info_by_id, on_asm)
    used_local: set[int] = set()
    assigned: list = []
    local_of_ncbi: Dict[str, int] = {}

    def claim(local_gid: Optional[int], ncbi: Optional[str], method: str) -> bool:
        """同一 RefSeq GeneID 可挂多个本地旧名；一个本地基因只能挂一个 GeneID。"""
        ncbi = (ncbi or "").strip()
        rec = rec_by_ncbi.get(ncbi)
        if not local_gid or not rec:
            return False
        local_gid = int(local_gid)
        if local_gid in used_local:
            return False
        if not (rec.get("symbol") or "").strip():
            return False
        used_local.add(local_gid)
        local_of_ncbi[ncbi] = local_of_ncbi.get(ncbi, 0) + 1
        assigned.append((local_gid, rec, method))
        return True

    def ncbi_via_zfin(zfin: str) -> str:
        zfin = (zfin or "").upper()
        if not zfin:
            return ""
        ncbi = zfin_maps.get("zfin_to_ncbi", {}).get(zfin) or ""
        if not ncbi:
            ids = info_idx["zfin"].get(zfin) or ""
            ncbi = ids if isinstance(ids, str) else ""
        return ncbi if ncbi in on_asm else ""

    # 1) RefSeq 符号 = 本地主名
    for rec in genes:
        sl = _usable_key(rec.get("symbol") or "")
        if sl:
            claim(maps["name"].get(sl), rec.get("ncbi_gene_id"), "refseq_symbol")

    # 2) 本地 Ensembl（GRCz11）→ ZFIN 1:1 → NCBI
    for ens, local_gid in maps["ens"].items():
        zfin = zfin_maps.get("ens_to_zfin", {}).get(ens)
        claim(local_gid, ncbi_via_zfin(zfin or ""), "zfin_ensembl")

    # 2b) gene_info dbXrefs Ensembl（若仍是 GRCz11 号）
    for ens, ncbi in info_idx["ens"].items():
        claim(maps["ens"].get(ens), ncbi, "gene_info_ensembl")

    # 3) gene_info 正式符号 / 命名权威符号 / locus_tag
    for key, ncbi in info_idx["primary"].items():
        claim(maps["name"].get(key), ncbi, "gene_info_symbol")
        claim(maps["symbol"].get(key), ncbi, "gene_info_symbol_alt")

    # 4) RefSeq 符号 = 本地 gene_symbol
    for rec in genes:
        sl = _usable_key(rec.get("symbol") or "")
        if sl:
            claim(maps["symbol"].get(sl), rec.get("ncbi_gene_id"), "refseq_local_symbol")

    # 5) gene_info 全部同义 / Other_designations / 全名（不含空格长描述）
    for key, ncbi in info_idx["extra"].items():
        claim(maps["name"].get(key), ncbi, "gene_info_synonym")
        claim(maps["symbol"].get(key), ncbi, "gene_info_synonym_alt")

    # 6) ZFIN 现行符号 / 曾用名 → NCBI（GeneID 必须在本条 RefSeq 上）
    for key, zfin in zfin_maps.get("sym_to_zfin", {}).items():
        claim(maps["name"].get(key), ncbi_via_zfin(zfin), "zfin_symbol")
        claim(maps["symbol"].get(key), ncbi_via_zfin(zfin), "zfin_symbol_alt")
    for key, zfin in zfin_maps.get("prev_to_zfin", {}).items():
        k = _usable_key(key, extra=True)
        if not k:
            continue
        claim(maps["name"].get(k), ncbi_via_zfin(zfin), "zfin_previous")
        claim(maps["symbol"].get(k), ncbi_via_zfin(zfin), "zfin_previous_alt")

    # 7) ZFIN 1:1 现行符号 → 本条 RefSeq 唯一 symbol（gene.txt 没有 NCBI 时也能挂）
    ft_sym_uniq: Dict[str, str] = {}
    from collections import defaultdict as _dd
    _ftb: Dict[str, set] = _dd(set)
    for rec in genes:
        sl = _usable_key(rec.get("symbol") or "")
        ncbi = (rec.get("ncbi_gene_id") or "").strip()
        if sl and ncbi:
            _ftb[sl].add(ncbi)
    ft_sym_uniq = {k: next(iter(v)) for k, v in _ftb.items() if len(v) == 1}
    for ens, local_gid in maps["ens"].items():
        lab = _usable_key(zfin_maps.get("ens_to_label", {}).get(ens) or "")
        if lab:
            claim(local_gid, ft_sym_uniq.get(lab), "zfin_label")

    # 8) 本地唯一别名 → 本条 RefSeq / gene_info 唯一键
    for key, local_gid in (maps.get("alias") or {}).items():
        claim(local_gid, ft_sym_uniq.get(key), "alias_ft")
        claim(local_gid, info_idx["primary"].get(key), "alias_info")
        claim(local_gid, info_idx["extra"].get(key), "alias_info_x")

    from collections import Counter
    c = Counter(m for _, _, m in assigned)
    leftover = len(maps.get("id_name") or {}) - len(assigned)
    shared = sum(1 for n in local_of_ncbi.values() if n > 1)
    safe_print(
        f"  对齐 {len(assigned)} / 本地 {len(maps.get('id_name') or {})} "
        f"（未挂 {leftover}；{shared} 个 GeneID 挂了多个本地名）："
        + " / ".join(f"{k} {v}" for k, v in c.most_common())
    )
    unused_info = sum(1 for gid in on_asm if gid not in local_of_ncbi and gid in info_by_id)
    safe_print(
        f"  本条 RefSeq GeneID {len(on_asm)}；gene_info 覆盖其中 "
        f"{sum(1 for g in on_asm if g in info_by_id)}；仍无本地基因的 GeneID {len(on_asm) - len(local_of_ncbi)}"
        f"（其中 gene_info 有记录 {unused_info}）"
    )
    shown = 0
    for gid, name in (maps.get("id_name") or {}).items():
        if gid in used_local:
            continue
        ens = (maps.get("id_ens") or {}).get(gid) or ""
        safe_print(f"  未挂例 {name or gid} {ens}")
        shown += 1
        if shown >= 15:
            break
    return assigned


def _feature_symbol_by_geneid(genes: List[dict]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rec in genes:
        gid = (rec.get("ncbi_gene_id") or "").strip()
        sym = (rec.get("symbol") or "").strip()
        if gid and sym and gid not in out:
            out[gid] = sym
    return out


def _verify_rows(ft_sym: Dict[str, str]) -> list:
    from sqlalchemy import text
    db = next(_client().get_db())
    bad = []
    try:
        rows = db.execute(text(
            "SELECT gene_id, gene_symbol, ncbi_gene_id, source "
            "FROM gene_assembly_xref WHERE assembly_name=:a"
        ), {"a": NCBI_REFERENCE_ASSEMBLY}).mappings().all()
        seen_gid = set()
        ncbi_sym: Dict[str, str] = {}
        for r in rows:
            gene_id = int(r["gene_id"])
            symbol = (r["gene_symbol"] or "").strip()
            ncbi = (r["ncbi_gene_id"] or "").strip()
            source = (r["source"] or "").strip()
            if gene_id in seen_gid:
                bad.append(f"重复 gene_id {gene_id}")
            seen_gid.add(gene_id)
            if not ncbi:
                bad.append(f"gene_id {gene_id} 无 NCBI GeneID")
                continue
            if ncbi in ncbi_sym and ncbi_sym[ncbi] != symbol:
                bad.append(f"NCBI GeneID {ncbi} 标准名冲突 {ncbi_sym[ncbi]} vs {symbol}")
            ncbi_sym[ncbi] = symbol
            if source != REFSEQ_SOURCE:
                bad.append(f"GeneID {ncbi} source={source} 不是 {REFSEQ_SOURCE}")
            want = ft_sym.get(ncbi)
            if not want:
                bad.append(f"GeneID {ncbi} 不在 GCF_049306965.1 feature_table")
            elif want != symbol:
                bad.append(f"GeneID {ncbi} 库内 {symbol} ≠ RefSeq {want}")
        famous = (
            ("fgf8a", "fgf8a"), ("sox9b", "sox9b"), ("rag1", "rag1"),
            ("tp53", "tp53"), ("shha", "shha"), ("hif1aa", "hif1aa"),
            ("gata1", "gata1a"), ("isl1", "isl1a"), ("vegfa", "vegfaa"),
            ("ntla", "tbxta"),
        )
        for local, want_sym in famous:
            row = db.execute(text(
                "SELECT ax.gene_symbol FROM gene_assembly_xref ax "
                "JOIN gene_basic gb ON ax.gene_id=gb.gene_id "
                "WHERE ax.assembly_name=:a AND LOWER(gb.gene_name)=:n LIMIT 1"
            ), {"a": NCBI_REFERENCE_ASSEMBLY, "n": local}).mappings().first()
            if not row:
                bad.append(f"{local} 未挂 RefSeq 标准名")
            elif (row["gene_symbol"] or "").strip().lower() != want_sym:
                bad.append(f"{local} 标准名写成了 {row['gene_symbol']}（应为 {want_sym}）")
    finally:
        db.close()
    return bad


def _raw_write(sql: str) -> None:
    """execute_write 走 SQLAlchemy text()，会把 si:ch211 里的冒号当成绑定参数。"""
    res = _client().execute_write(sql.replace(":", r"\:"))
    if not res.get("success"):
        raise RuntimeError(res.get("error") or "write failed")


def _flush_asm(buf: list, stats: dict) -> None:
    if not buf:
        return
    sql = (
        "INSERT INTO gene_assembly_xref "
        "(gene_id, assembly_name, gene_symbol, ncbi_gene_id, chromosome, "
        "start_pos, end_pos, strand, source) VALUES "
        + ",".join(buf)
        + " ON DUPLICATE KEY UPDATE "
        "gene_symbol=VALUES(gene_symbol), ncbi_gene_id=VALUES(ncbi_gene_id), "
        "chromosome=VALUES(chromosome), start_pos=VALUES(start_pos), "
        "end_pos=VALUES(end_pos), strand=VALUES(strand), source=VALUES(source)"
    )
    _raw_write(sql)
    stats["asm"] += len(buf)
    buf.clear()


def _flush_alias(buf: list, stats: dict) -> None:
    if not buf:
        return
    sql = (
        "INSERT IGNORE INTO gene_alias (gene_id, alias, alias_type, source, is_primary) VALUES "
        + ",".join(buf)
    )
    _raw_write(sql)
    stats["alias"] += len(buf)
    buf.clear()


def _flush_xref(buf: list, stats: dict) -> None:
    if not buf:
        return
    sql = (
        "INSERT IGNORE INTO gene_xref (gene_id, db_name, db_id, source) VALUES "
        + ",".join(buf)
    )
    _raw_write(sql)
    stats["xref"] += len(buf)
    buf.clear()


def _load_refseq_genes() -> List[dict]:
    genes: List[dict] = []
    try:
        feat = _download([root + FEATURE_NAME for root in FTP_ROOTS], FEATURE_CACHE, min_bytes=2_000_000)
        seen = set()
        for rec in _iter_feature_table(feat):
            key = rec["ncbi_gene_id"] or f"sym:{rec['symbol'].lower()}"
            if key in seen:
                continue
            seen.add(key)
            genes.append(rec)
        safe_print(f"  feature_table 基因 {len(genes)}（{NCBI_REFERENCE_REFSEQ}）")
    except Exception as e:
        safe_print(f"  [警告] feature_table 失败：{e}")
    if len(genes) >= 10000:
        return genes
    safe_print("  改用 genomic.gff 基因 Name")
    gff = _download([root + GFF_NAME for root in FTP_ROOTS], GFF_CACHE, min_bytes=5_000_000)
    return _gff_genes(gff)


def build() -> int:
    safe_print("=" * 60)
    safe_print(f"  标准名 = RefSeq {NCBI_REFERENCE_REFSEQ}（{NCBI_REFERENCE_ASSEMBLY}）gene.symbol")
    safe_print(f"  taxon {TAXON_ID}")
    safe_print("=" * 60)
    if not ensure_alias_tables() or not _ensure_assembly_tables():
        return 1
    _seed_registry()
    maps = _load_local_maps()
    info_path = _download(GENE_INFO_URL, INFO_CACHE, min_bytes=50_000)
    info_by_id = _load_gene_info_by_id(info_path)
    zfin_maps = _load_zfin_maps()
    genes = _load_refseq_genes()
    if len(genes) < 10000:
        safe_print(f"[错误] RefSeq 注释基因过少（{len(genes)}），拒绝用 gene_info 冒充标准名")
        return 1

    _client().execute_write(
        f"DELETE FROM gene_assembly_xref WHERE assembly_name='{_esc(NCBI_REFERENCE_ASSEMBLY)}'"
    )
    _client().execute_write(
        f"DELETE FROM gene_alias WHERE source='{_esc(REFSEQ_SOURCE)}'"
    )
    _client().execute_write(
        f"DELETE FROM gene_xref WHERE source='{_esc(REFSEQ_SOURCE)}'"
    )
    _client().execute_write(
        "DELETE FROM gene_xref WHERE source='gene_info' "
        "AND db_name IN ('ensembl','zfin','ncbi_gene')"
    )
    safe_print(f"  已清空 {NCBI_REFERENCE_ASSEMBLY} 旧对照与本源别名，按 RefSeq 重写")

    assigned = _assign_strict(genes, info_by_id, maps, zfin_maps)
    ft_sym = _feature_symbol_by_geneid(genes)
    stats = {
        "parsed": len(genes), "matched": len(assigned), "unmatched": len(genes) - len(assigned),
        "asm": 0, "alias": 0, "xref": 0, "pc_ok": 0,
    }
    asm_buf: list[str] = []
    alias_buf: list[str] = []
    xref_buf: list[str] = []

    for gid, rec, _method in assigned:
        official = (rec.get("symbol") or "").strip()
        if rec.get("ncbi_gene_id"):
            want = ft_sym.get(rec["ncbi_gene_id"])
            if want and want != official:
                continue
        kind = (rec.get("kind") or "").lower()
        if "protein" in kind or kind in ("protein_coding", "protein-coding"):
            stats["pc_ok"] += 1
        info = info_by_id.get(rec.get("ncbi_gene_id") or "") or {}
        chrom = rec.get("chromosome") or info.get("chromosome") or ""
        start = rec.get("start_pos")
        end = rec.get("end_pos")
        strand = rec.get("strand") or ""
        start_sql = str(int(start)) if start else "NULL"
        end_sql = str(int(end)) if end else "NULL"
        asm_buf.append(
            f"({gid}, '{_esc(NCBI_REFERENCE_ASSEMBLY)}', '{_esc(official)}', "
            f"{_sql_val(rec.get('ncbi_gene_id'))}, {_sql_val(chrom)}, "
            f"{start_sql}, {end_sql}, {_sql_val(strand)}, '{_esc(REFSEQ_SOURCE)}')"
        )
        if rec.get("ncbi_gene_id"):
            xref_buf.append(
                f"({gid}, 'ncbi_gene', '{_esc(rec['ncbi_gene_id'])}', '{_esc(REFSEQ_SOURCE)}')"
            )
        if info.get("ensembl"):
            xref_buf.append(
                f"({gid}, 'ensembl', '{_esc(info['ensembl'])}', '{_esc(REFSEQ_SOURCE)}')"
            )
        if info.get("zfin"):
            xref_buf.append(
                f"({gid}, 'zfin', '{_esc(info['zfin'])}', '{_esc(REFSEQ_SOURCE)}')"
            )
        names = [(official, "refseq_symbol")]
        names.extend((s, "synonym") for s in info.get("synonyms") or [])
        names.extend((s, "other") for s in info.get("other") or [])
        names.extend((s, "locus_tag") for s in [info.get("locus_tag")] if s)
        names.extend((s, "nomenclature") for s in [info.get("official")] if s)
        names.extend((s, "full_name") for s in [info.get("full_name")] if s)
        for name, typ in names:
            low = (name or "").strip().lower()
            if not low or low == "-" or len(low) > 120:
                continue
            alias_buf.append(
                f"({gid}, '{_esc(low)}', '{typ}', '{_esc(REFSEQ_SOURCE)}', "
                f"{1 if typ == 'refseq_symbol' else 0})"
            )
        if len(asm_buf) >= BATCH:
            _flush_asm(asm_buf, stats)
        if len(alias_buf) >= BATCH:
            _flush_alias(alias_buf, stats)
        if len(xref_buf) >= BATCH:
            _flush_xref(xref_buf, stats)

    _flush_asm(asm_buf, stats)
    _flush_alias(alias_buf, stats)
    _flush_xref(xref_buf, stats)

    bad = _verify_rows(ft_sym)
    if bad:
        safe_print(f"[错误] RefSeq 标准名核对失败 {len(bad)} 条，清空对照")
        for line in bad[:20]:
            safe_print(f"  {line}")
        _client().execute_write(
            f"DELETE FROM gene_assembly_xref WHERE assembly_name='{_esc(NCBI_REFERENCE_ASSEMBLY)}'"
        )
        return 1

    db = next(_client().get_db())
    try:
        from sqlalchemy import text
        n = db.execute(text(
            "SELECT COUNT(*) FROM gene_assembly_xref WHERE assembly_name=:a"
        ), {"a": NCBI_REFERENCE_ASSEMBLY}).scalar() or 0
        src = db.execute(text(
            "SELECT COUNT(*) FROM gene_assembly_xref WHERE assembly_name=:a AND source=:s"
        ), {"a": NCBI_REFERENCE_ASSEMBLY, "s": REFSEQ_SOURCE}).scalar() or 0
        basic = db.execute(text("SELECT COUNT(*) FROM gene_basic")).scalar() or 0
        diff = db.execute(text(
            "SELECT COUNT(*) FROM gene_assembly_xref ax JOIN gene_basic gb ON ax.gene_id=gb.gene_id "
            "WHERE ax.assembly_name=:a AND LOWER(ax.gene_symbol)<>LOWER(gb.gene_name)"
        ), {"a": NCBI_REFERENCE_ASSEMBLY}).scalar() or 0
        samples = [dict(r) for r in db.execute(text(
            "SELECT ax.ncbi_gene_id, ax.gene_symbol, gb.gene_name, gb.ensembl_id FROM gene_assembly_xref ax "
            "JOIN gene_basic gb ON ax.gene_id=gb.gene_id "
            "WHERE ax.assembly_name=:a AND LOWER(ax.gene_symbol)<>LOWER(gb.gene_name) "
            "ORDER BY gb.gene_name LIMIT 12"
        ), {"a": NCBI_REFERENCE_ASSEMBLY}).mappings().all()]
        unmapped = [dict(r) for r in db.execute(text(
            "SELECT gb.gene_name, gb.ensembl_id FROM gene_basic gb "
            "LEFT JOIN gene_assembly_xref ax ON ax.gene_id=gb.gene_id "
            "AND ax.assembly_name=:a WHERE ax.gene_id IS NULL "
            "ORDER BY gb.gene_name LIMIT 12"
        ), {"a": NCBI_REFERENCE_ASSEMBLY}).mappings().all()]
        n_unmapped = db.execute(text(
            "SELECT COUNT(*) FROM gene_basic gb LEFT JOIN gene_assembly_xref ax "
            "ON ax.gene_id=gb.gene_id AND ax.assembly_name=:a WHERE ax.gene_id IS NULL"
        ), {"a": NCBI_REFERENCE_ASSEMBLY}).scalar() or 0
    finally:
        db.close()
    rate = (int(n) / int(basic) * 100) if basic else 0
    safe_print(
        f"[完成] RefSeq 基因 {stats['parsed']} | 对照 {n}/{basic} ({rate:.1f}%) "
        f"| source={src} | 改名 {diff} | 未挂 {n_unmapped} | 蛋白编码命中 {stats['pc_ok']} "
        f"| 别名+{stats['alias']} xref+{stats['xref']}"
    )
    safe_print(f"  核对：0 条标准名与 GCF_049306965.1 feature_table 不符")
    for r in samples:
        safe_print(
            f"  改名 {r['gene_name']} → {r['gene_symbol']} "
            f"(GeneID {r['ncbi_gene_id']}, {r['ensembl_id']})"
        )
    for r in unmapped:
        safe_print(f"  仍未挂 {r['gene_name']} {r['ensembl_id']}")
    if int(n) < 15000 or int(src) != int(n):
        safe_print(f"[警告] RefSeq 对照异常（{n} / source {src}）")
        return 1
    return 0


def main() -> None:
    raise SystemExit(build())


if __name__ == "__main__":
    main()
