# -*- coding: utf-8 -*-
"""基因序列 / 启动子提取。

cDNA：NCBI RefSeq GRCz12tu CDS/RNA（与 BLAST 同库），识别 `[gene=tp53]` 头。
启动子 / 基因组区：Ensembl REST GRCz11 坐标（须在 JSON 与界面标明组装）。
"""
import bootstrap  # noqa: F401

import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from common_utils import logger

try:  # 集中式外部 HTTP 客户端：禁代理 / 短超时 / 有界重试 / 审计
    from http_client import external_get_json
except ImportError:  # pragma: no cover — 以包形式运行时退回绝对导入
    from database.http_client import external_get_json

_GENE_TAG_RE = re.compile(r"\[gene=([^\]]+)\]", re.I)
_PROTEIN_ID_RE = re.compile(r"\[protein_id=([^\]]+)\]", re.I)
_CDNA_CACHE: Dict[str, dict] = {}
_CDNA_DISK_DIR = Path(os.getenv(
    "CDNA_CACHE_DIR", "/data/zoujin/deng/zebrafish_runtime/cache/cdna"))
# 本地目录里「有没有这个基因」的判定结果，缓存一个进程周期。
# 明显不存在的输入（my17 / 中文 / 随手敲的短序列）不应该触发 Ensembl 网络回退：
# 单次 _api_get 默认 3 次重试 × 25s，实测会让工具页卡 18~45s，
# 用户看到的就是「一会儿快一会儿打不开」。
_LOCAL_GENE_CACHE: Dict[str, bool] = {}
# 候选池按两字母前缀缓存（gene_basic 全表扫描一次约 2s，不该每次失败都付）
_SUGGEST_POOL_CACHE: Dict[str, List[dict]] = {}


def local_gene_known(gene: str) -> bool:
    """只用本地目录判断标识符是否收录（绝不发网络请求）。

    判定不了（DB 异常等）一律返回 True，宁可多走一次有硬上限的网络请求，
    也不能把真基因误判成「不存在」。
    """
    q = str(gene or "").strip()
    if not q:
        return False
    key = q.lower()
    if key in _LOCAL_GENE_CACHE:
        return _LOCAL_GENE_CACHE[key]
    # 非 ASCII / 含空格的口语串不可能是基因符号，连库都不用查
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", q):
        _LOCAL_GENE_CACHE[key] = False
        return False
    try:
        from gene_id_mapper import resolve_gene_identifier

        r = resolve_gene_identifier(q, use_ensembl_fallback=False) or {}
        known = (r.get("status") != "not_found") or bool(r.get("resolved"))
        if not known:
            # GRCz12tu 主表里有些 LOC… 符号没进 gene_basic，别把它们误杀
            from mysql_client import get_mysql_client

            res = get_mysql_client().execute_sql(
                "SELECT 1 FROM gene_master_grcz12tu WHERE symbol=:s LIMIT 1",
                params={"s": q}, max_rows=1)
            known = bool((res or {}).get("data"))
    except Exception:  # noqa: BLE001
        known = True
    _LOCAL_GENE_CACHE[key] = known
    return known


_LOOKUP_CACHE: Dict[str, tuple] = {}
_LOOKUP_TTL = float(os.getenv("GENE_LOOKUP_TTL_SEC", "3600"))

# auto 模式下 Ensembl 启始子请求的硬截止（秒）。必须远小于 extract_sequences 的 45s 总预算，
# 否则「21s 拿 503 + 28s 超时」会把预算吃光，NCBI 兜底根本没机会返回。
ENSEMBL_DEADLINE = float(os.getenv("PROMOTER_ENSEMBL_DEADLINE_S", "12"))


def _lookup_gene(gene: str) -> Optional[dict]:
    """基因符号 → 坐标。带 TTL 缓存：同一基因反复提问不必每次重新解析。"""
    key = str(gene or "").strip().lower()
    item = _LOOKUP_CACHE.get(key)
    if item and (time.time() - item[0]) <= _LOOKUP_TTL:
        return copy.deepcopy(item[1])
    # 交互式路径：本地目录没收录的符号直接返回，Ensembl 只给一次机会、6s 截止。
    # 批量采集脚本仍走 gene_collector 默认的 3×25s，不受影响。
    if not local_gene_known(gene):
        return None
    from gene_collector import resolve_lookup

    val = resolve_lookup(gene, timeout=6, retries=1)
    if val:
        if len(_LOOKUP_CACHE) >= 512:
            oldest = min(_LOOKUP_CACHE.items(), key=lambda kv: kv[1][0])[0]
            _LOOKUP_CACHE.pop(oldest, None)
        _LOOKUP_CACHE[key] = (time.time(), copy.deepcopy(val))
    return val


def _fasta_assembly(path: str) -> str:
    n = (path or "").replace("\\", "/").lower()
    if "grcz12" in n or "049306965" in n:
        return "GRCz12tu"
    if "grcz11" in n:
        return "GRCz11"
    return "local"


def _protein_id_from_header(header: str) -> str:
    m = _PROTEIN_ID_RE.search(header or "")
    return (m.group(1) if m else "").strip()


def _isoform_rank(header: str, seq_len: int) -> tuple:
    pid = _protein_id_from_header(header).upper()
    if pid.startswith("NP_"):
        return (0, -int(seq_len))
    if pid.startswith("XP_"):
        return (1, -int(seq_len))
    return (2, -int(seq_len))


def _header_matches(header: str, needles: list[str]) -> bool:
    raw = header or ""
    h = f" {raw.lower()} "
    h_norm = h.replace(";", " ").replace(",", " ")
    gene_tags = {m.group(1).strip().lower() for m in _GENE_TAG_RE.finditer(raw)}
    for n in needles:
        n = (n or "").strip().lower()
        if not n:
            continue
        if n.startswith("[gene="):
            inner = n[6:].rstrip("]").strip()
            if inner and inner in gene_tags:
                return True
            continue
        if n.startswith("gene_symbol:"):
            sym = n.split(":", 1)[1].strip()
            if n in h_norm:
                return True
            if sym and sym in gene_tags:
                return True
            continue
        if n.startswith("ens") and n in h.replace(".", " "):
            return True
    return False


def _cdna_fasta_paths() -> list[str]:
    from seq_toolkit import _allow_grcz11_fallback, _is_grcz12_fasta, _pick_blast_fasta
    from config import REFERENCE_FASTA, _BASE_DIR

    rt = os.getenv("ZEBRA_RUNTIME") or "/data/zoujin/deng/zebrafish_runtime"
    cands = [
        _pick_blast_fasta(),
        os.path.join(rt, "blast", "grcz12tu_rna.fna"),
        os.path.join(rt, "blast", "grcz12tu_cds.fna"),
        os.path.join(rt, "omics_structured", "10_toolkit_assets", "blast", "Danio_rerio.GRCz12tu.rna.fna"),
        os.path.join(rt, "omics_structured", "10_toolkit_assets", "blast", "Danio_rerio.GRCz12tu.cds.fna"),
    ]
    out: list[str] = []
    seen = set()
    for p in cands:
        if not p or p in seen:
            continue
        seen.add(p)
        if os.path.isfile(p) and os.path.getsize(p) > 500:
            out.append(p)
    have12 = any(_is_grcz12_fasta(p) for p in out)
    if have12:
        return [p for p in out if _is_grcz12_fasta(p)]
    if not _allow_grcz11_fallback():
        return out
    for p in (
        os.path.join(rt, "omics_structured", "10_toolkit_assets", "blast", "Danio_rerio.GRCz11.cdna.all.fa"),
        os.path.join(_BASE_DIR, "data", "reference_cdna.fasta"),
        REFERENCE_FASTA,
    ):
        if not p or p in seen:
            continue
        seen.add(p)
        if os.path.isfile(p) and os.path.getsize(p) > 500:
            out.append(p)
    return out


def _symbol_needles(symbols: List[str], ensembl_id: str = "") -> list[str]:
    needles: list[str] = []
    seen = set()
    for g in symbols:
        g = (g or "").strip()
        if not g:
            continue
        key = g.lower()
        if key in seen:
            continue
        seen.add(key)
        needles.append(f"gene_symbol:{g}")
        needles.append(f"[gene={g}]")
    eid = (ensembl_id or "").split(".")[0].strip()
    if eid:
        needles.append(eid)
    return needles


def _cdna_disk_path(cache_key: str) -> Optional[Path]:
    """cDNA 命中结果的落盘缓存路径（键含 FASTA 指纹，文件一换自动失效）。

    2026-11-12：本地 RefSeq FASTA 是按行全扫来匹配 header 的，冷启动第一次
    取某个基因的 cDNA 实测 5~13 s。虽然 `_CDNA_CACHE` 之后能命中，但进程一重启
    就全清零，于是「每次部署后第一个查序列的人」要额外等十几秒。序列内容对同一
    份 FASTA 是确定的，把它按「关键词 + 各 FASTA 的 名称/大小/mtime」落盘即可，
    下次冷启动只是读一个小 JSON。
    """
    try:
        sig = []
        for p in _cdna_fasta_paths():
            st = os.stat(p)
            sig.append("%s:%d:%d" % (os.path.basename(p), st.st_size, int(st.st_mtime)))
    except OSError:
        return None
    raw = cache_key + "||" + "|".join(sig)
    return _CDNA_DISK_DIR / (hashlib.sha1(raw.encode("utf-8")).hexdigest() + ".json")


def _cdna_disk_get(cache_key: str) -> dict:
    path = _cdna_disk_path(cache_key)
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and data.get("sequence"):
        return data
    return {}


def _cdna_disk_put(cache_key: str, payload: dict) -> None:
    path = _cdna_disk_path(cache_key)
    if path is None or not payload.get("sequence"):
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass  # 目录只读等：退化为纯内存缓存，不影响功能


def _local_cdna_from_fasta(symbols: List[str], ensembl_id: str = "") -> dict:
    needles = _symbol_needles(symbols, ensembl_id)
    if not needles:
        return {}
    cache_key = "|".join(n.lower() for n in needles)
    cached = _CDNA_CACHE.get(cache_key)
    if cached:
        return cached
    disk = _cdna_disk_get(cache_key)
    if disk:
        _CDNA_CACHE[cache_key] = disk
        return disk
    best: Optional[tuple] = None
    best_payload: dict = {}
    from seq_toolkit import _is_grcz12_fasta

    for path in _cdna_fasta_paths():
        rec_hdr = ""
        rec_seq: list[str] = []
        found_np_in_file = False
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith(">"):
                        if rec_hdr and rec_seq and _header_matches(rec_hdr, needles):
                            seq = "".join(rec_seq).upper()
                            if len(seq) >= 60:
                                rank = _isoform_rank(rec_hdr, len(seq))
                                if best is None or rank < best:
                                    best = rank
                                    best_payload = {
                                        "sequence": seq,
                                        "path": path,
                                        "header": rec_hdr,
                                        "protein_id": _protein_id_from_header(rec_hdr),
                                        "assembly": _fasta_assembly(path),
                                    }
                                if rank[0] == 0:
                                    found_np_in_file = True
                        rec_hdr = line[1:].strip()
                        rec_seq = []
                    else:
                        rec_seq.append(line.strip())
                if rec_hdr and rec_seq and _header_matches(rec_hdr, needles):
                    seq = "".join(rec_seq).upper()
                    if len(seq) >= 60:
                        rank = _isoform_rank(rec_hdr, len(seq))
                        if best is None or rank < best:
                            best = rank
                            best_payload = {
                                "sequence": seq,
                                "path": path,
                                "header": rec_hdr,
                                "protein_id": _protein_id_from_header(rec_hdr),
                                "assembly": _fasta_assembly(path),
                            }
                        if rank[0] == 0:
                            found_np_in_file = True
        except OSError as e:
            logger.warning(f"[序列] 读 FASTA 失败 {path}: {e}")
            continue
        if found_np_in_file and _is_grcz12_fasta(path) and best_payload:
            break
    if best_payload:
        logger.info(
            f"[序列] 本地 FASTA 命中 {symbols[0] if symbols else '?'} ← "
            f"{os.path.basename(best_payload.get('path') or '')} "
            f"{best_payload.get('protein_id') or ''}"
        )
        _CDNA_CACHE[cache_key] = best_payload
        _cdna_disk_put(cache_key, best_payload)
    return best_payload


def _ensembl_cdna_by_id(eid: str) -> str:
    from gene_collector import _api_get

    eid = (eid or "").split(".")[0]
    if not eid:
        return ""
    lu = _api_get(f"/lookup/id/{eid}", {"expand": 0}, retries=1, timeout=8)
    tid = ""
    if isinstance(lu, dict):
        tid = (lu.get("canonical_transcript") or "").split(".")[0]
    if not tid:
        return ""
    data = _api_get(f"/sequence/id/{tid}", {"type": "cdna"}, retries=1, timeout=10)
    if isinstance(data, dict):
        return (data.get("seq") or "").upper()
    return ""


def _norm_assembly(value) -> str:
    """auto / GRCz11 / GRCz12tu。认不出来的按 auto 处理。"""
    v = str(value or "").strip().lower().replace(" ", "").replace("_", "")
    if v in ("", "auto", "default", "any"):
        return "auto"
    if "11" in v:
        return "GRCz11"
    if "12" in v or "tu" in v:
        return "GRCz12tu"
    return "auto"


def _grcz12tu_locus(gene: str) -> Optional[dict]:
    """基因在 GRCz12tu 上的坐标，供「按基因取启动子/基因体」使用。

    路径：本地符号 → gene_basic.gene_id → gene_id_map_grcz12tu → gene_master_grcz12tu。
    查不到时再用符号直接匹配主表，兼容只存在于 GRCz12tu 的名字（如 LOC…）。
    """
    from gene_id_mapper import resolve_gene_identifier
    from gene_toolkit import normalize_gene_name
    from mysql_client import get_mysql_client

    name = normalize_gene_name(gene) or gene
    mc = get_mysql_client()
    tu_id = None
    try:
        r = resolve_gene_identifier(name)
        gid = (r.get("canonical") or {}).get("gene_id")
    except Exception:  # noqa: BLE001
        gid = None
    if gid is not None:
        res = mc.execute_sql(
            "SELECT grcz12tu_gene_id FROM gene_id_map_grcz12tu"
            " WHERE source_gene_id=:g LIMIT 1",
            params={"g": int(gid)}, max_rows=1)
        row = ((res or {}).get("data") or [None])[0]
        tu_id = str(row.get("grcz12tu_gene_id")) if row else None
    if not tu_id:
        res = mc.execute_sql(
            "SELECT grcz12tu_gene_id FROM gene_master_grcz12tu WHERE symbol=:s LIMIT 1",
            params={"s": name}, max_rows=1)
        row = ((res or {}).get("data") or [None])[0]
        tu_id = str(row.get("grcz12tu_gene_id")) if row else None
    if not tu_id:
        return None
    res = mc.execute_sql(
        "SELECT grcz12tu_gene_id, symbol, chrom, start_pos, end_pos, strand"
        " FROM gene_master_grcz12tu WHERE grcz12tu_gene_id=:g LIMIT 1",
        params={"g": tu_id}, max_rows=1)
    m = ((res or {}).get("data") or [None])[0]
    if not m or not m.get("chrom") or not m.get("start_pos"):
        return None
    acc = _refseq_accession_for(str(m["chrom"]))
    return {
        "gene_id": tu_id,
        "symbol": m.get("symbol") or name,
        "chrom": str(m["chrom"]),
        "start": int(m["start_pos"]),
        "end": int(m["end_pos"] or m["start_pos"]),
        "strand": (m.get("strand") or "+").strip() or "+",
        "accession": acc.get("accession") or "",
    }


def suggest_gene_symbols(query: str, limit: int = 6) -> List[dict]:
    """查不到基因时给「是不是想查」的候选（前缀取候选池 + 相似度排序）。

    目的：输入 my17 这类不存在的符号时，页面能直接给出 myl7 / myo… 这几个
    真正存在的符号，而不是只丢一句「未取到」。任何异常都静默降级为空列表。
    """
    q = (query or "").strip()
    if len(q) < 2:
        return []
    ql = q.lower()
    prefix = ql[:2]
    pool: List[dict] = []
    try:
        from mysql_client import get_mysql_client

        pool = _SUGGEST_POOL_CACHE.get(prefix) or []
        if not pool:
            seen = set()
            # 不加 LOWER()：列本身是 ci collation，函数包裹会废掉 gene_symbol 的索引。
            # 候选池按前缀缓存，避免每次失败都全表扫 2s。
            res = get_mysql_client().execute_sql(
                "SELECT gene_id, gene_symbol FROM gene_basic"
                " WHERE gene_symbol LIKE :p LIMIT 400",
                params={"p": prefix + "%"}, max_rows=400)
            for row in ((res or {}).get("data") or []):
                sym = str(row.get("gene_symbol") or "").strip()
                if not sym or sym.lower() in seen:
                    continue
                seen.add(sym.lower())
                pool.append({"symbol": sym, "gene_id": row.get("gene_id")})
            _SUGGEST_POOL_CACHE[prefix] = pool
    except Exception:  # noqa: BLE001
        return []
    if not pool:
        return []
    pool = list(pool)

    import difflib

    def _rank(item: dict):
        s = item["symbol"].lower()
        ratio = difflib.SequenceMatcher(None, ql, s).ratio()
        # 前缀命中再加一点分：my17 → myl7 这种「开头一样」的更可能是手误
        bonus = 0.15 if s.startswith(ql[:2]) else 0.0
        return (-(ratio + bonus), len(s))

    pool.sort(key=_rank)
    return pool[:max(1, int(limit))]


def fetch_cdna_sequence(gene: str, assembly: str = "auto") -> dict:
    """取 cDNA/CDS。

    ``assembly``：
      · ``GRCz12tu`` —— 只取本地 RefSeq 编码序列（本地 FASTA，从 ATG 起）；
      · ``GRCz11``   —— 只走 Ensembl REST（按 Ensembl Gene ID 取转录本）；
      · ``auto``     —— 旧行为：先本地 GRCz12tu，取不到再退 Ensembl GRCz11。
    明确指定版本时**不做跨版本回退**，否则用户以为拿到的是 A 版、实际是 B 版。
    """
    from gene_id_mapper import resolve_gene_identifier
    from gene_toolkit import normalize_gene_name

    want = _norm_assembly(assembly)
    name = normalize_gene_name(gene)
    resolved = resolve_gene_identifier(name, use_ensembl_fallback=False)
    can = resolved.get("canonical") or {}
    std = resolved.get("standard") or {}
    eid = (can.get("ensembl_id") or "").split(".")[0]
    shown = (std.get("gene_symbol") or can.get("gene_name") or name or gene).strip()

    seq = ""
    source = ""
    asm_used = ""
    accession = ""
    if want != "GRCz11":
        local_hit = _local_cdna_from_fasta(
            [shown, std.get("gene_symbol") or "", can.get("gene_name") or "", name, gene],
            eid,
        )
        seq = (local_hit.get("sequence") or "") if local_hit else ""
        source = "local_fasta" if seq else ""
        asm_used = (local_hit.get("assembly") if local_hit else "") or ""
        accession = local_hit.get("protein_id") if local_hit else ""

    if len(seq) < 60 and want != "GRCz12tu" and eid:
        seq = _ensembl_cdna_by_id(eid) or seq
        if len(seq) >= 60:
            source = "ensembl_id"
            asm_used = "GRCz11"
            accession = eid
    if len(seq) < 60 and want != "GRCz12tu":
        lookup = _lookup_gene(shown)
        if lookup:
            from gene_collector import fetch_cdna_from_lookup

            seq = fetch_cdna_from_lookup(lookup) or seq
            eid = eid or (lookup.get("id") or "")
            if len(seq) >= 60:
                source = "ensembl_lookup"
                asm_used = "GRCz11"
                accession = eid
    if len(seq) < 60:
        sugg = suggest_gene_symbols(name or gene)
        if want == "GRCz12tu":
            msg = "GRCz12tu 本地 RefSeq 编码序列里没有该基因（试 GRCz11，或把版本改为「自动」）"
        elif want == "GRCz11":
            msg = "Ensembl（GRCz11）未取到该基因的 cDNA（试 GRCz12tu，或把版本改为「自动」）"
        elif sugg:
            msg = "库里没有基因「%s」：本地 GRCz12tu 与 Ensembl 均未命中" % (shown or name or gene)
        else:
            msg = "无 cDNA 序列（本地 GRCz12tu FASTA 与 Ensembl 均未取到）"
        return {"success": False, "gene": shown, "error": msg, "assembly": want,
                "suggestions": sugg}
    fasta_id = accession or eid or shown
    # 本地 CDS FASTA 头里的号是蛋白号（NP_/XP_）。题面问「CDS 的 RefSeq 登录号」时
    # 给的必须是核酸号，所以这里反查一次 NM_；反查不到就如实标注，不拿 NP_ 冒充。
    prot_acc = accession if str(accession).startswith(("NP_", "XP_", "YP_")) else ""
    nt_acc = nucleotide_accession_for_protein(prot_acc) if prot_acc else ""
    if nt_acc:
        accession = nt_acc
    acc_type = ("refseq_mrna" if nt_acc
                else ("protein" if prot_acc
                      else ("ensembl_gene" if str(accession).startswith("ENS") else "")))
    if nt_acc:
        note = ("coding sequence (CDS) from the start codon, not the full-length transcript; "
                "RefSeq mRNA %s (protein %s)" % (nt_acc, prot_acc))
    elif prot_acc:
        note = ("coding sequence (CDS) from the start codon, not the full-length transcript; "
                "the local RefSeq CDS FASTA record carries protein accession %s "
                "(mRNA accession not resolved)" % prot_acc)
    else:
        note = ""
    return {
        "success": True,
        "gene": shown,
        "ensembl_id": eid,
        "sequence_type": "cdna",
        "source": source,
        "assembly": asm_used or ("GRCz12tu" if source == "local_fasta" else "GRCz11"),
        "accession": accession,
        "accession_type": acc_type,
        "protein_accession": prot_acc,
        "note": note,
        "length": len(seq),
        "sequence": seq,
        "fasta": f">{shown}|{fasta_id}|cdna|{asm_used or 'GRCz12tu'}|{source}\n{seq}",
    }


_PROT_CACHE: Dict[str, dict] = {}

# 蛋白号 → 核酸号（RefSeq）缓存：题面要"CDS 的 RefSeq 登录号"时，
# 本地 CDS FASTA 头里只有 NP_/XP_（蛋白号），直接当核酸号给出会把用户带偏。
_NT_ACC_CACHE: Dict[str, str] = {}


def _validate_protein_accession(acc: str) -> bool:
    """蛋白登录号必须是合法 RefSeq 蛋白号（NP_/XP_/YP_ + 数字 + 可选版本）。"""
    return bool(re.fullmatch(r"(?:NP|XP|YP)_\d+(?:\.\d+)?", acc or ""))


def _validate_nucleotide_accession(acc: str) -> bool:
    """核酸登录号必须是合法 RefSeq 核酸号（NM_/XM_/NR_/XR_ + 数字 + 可选版本）。"""
    return bool(re.fullmatch(r"(?:NM|XM|NR|XR)_\d+(?:\.\d+)?", acc or ""))


def nucleotide_accession_for_protein(acc: str) -> str:
    """NP_/XP_ 蛋白号 → 对应 RefSeq 核酸号（NM_/XM_），失败返回空串。

    相对旧版「隐式同步 elink + 静默降级」的改造点：
    - **显式校验**：入参必须是合法蛋白号；返回必须是合法核酸号，否则视为未解析；
    - **显式缓存**：进程内 ``_NT_ACC_CACHE``，负缓存（未解析）同样缓存，避免重复打 NCBI；
    - **显式审计**：每次外部调用都经 ``http_client`` 留下 ``[HTTP-AUDIT]`` 记录，
      网络/HTTP 失败不再被静默吞掉（仍降级返回空串，不影响答案其他部分）。

    网络调用：NCBI elink(protein→nuccore) + esummary 取 accessionversion，
    仅对合法蛋白号发起一次，并优先 mRNA(NM_)。
    """
    acc = str(acc or "").strip()
    if not _validate_protein_accession(acc):
        logger.debug("[NCBI] 蛋白号格式不合法，跳过 elink：%r", acc)
        return ""
    if acc in _NT_ACC_CACHE:
        return _NT_ACC_CACHE[acc]

    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    # 1) elink: protein → nuccore
    link = external_get_json(
        base + "/elink.fcgi",
        params={"dbfrom": "protein", "db": "nuccore", "id": acc, "retmode": "json"},
        timeout=10, attempts=2, audit_tag="ncbi_elink_protein2nt",
    )
    if link is None:
        # 网络/HTTP 失败：审计已由 http_client 记录；降级返回空串并负缓存。
        _NT_ACC_CACHE[acc] = ""
        return ""
    ids: list = []
    for ls in (link.get("linksets") or []):
        for ldb in (ls.get("linksetdbs") or []):
            ids.extend(str(x) for x in (ldb.get("links") or [])[:5])
    if not ids:
        _NT_ACC_CACHE[acc] = ""
        return ""

    # 2) esummary: nuccore → accessionversion
    sm = external_get_json(
        base + "/esummary.fcgi",
        params={"db": "nuccore", "id": ",".join(ids[:5]), "retmode": "json"},
        timeout=10, attempts=2, audit_tag="ncbi_esummary_nt",
    )
    if sm is None:
        _NT_ACC_CACHE[acc] = ""
        return ""

    best = ""
    for nid in ids:
        row = (sm.get("result") or {}).get(nid) or {}
        av = str(row.get("accessionversion") or "")
        if not _validate_nucleotide_accession(av):
            continue
        # 优先 mRNA（NM_），其次其他核酸号
        if av.startswith("NM_"):
            best = av
            break
        if av and not best:
            best = av
    if best:
        logger.info("[NCBI] %s → %s（蛋白号反查核酸号）", acc, best)
    else:
        logger.warning(
            "[NCBI] %s elink 命中 %d 个核酸号但无合法 RefSeq 核酸号", acc, len(ids))
    _NT_ACC_CACHE[acc] = best
    return best


def fetch_protein_sequence(gene: str, assembly: str = "auto") -> dict:
    """取蛋白序列 + UniProt 登录号（UniProt REST，带进程内缓存）。

    本地库只有核酸序列、没有蛋白 FASTA。用户问「蛋白序列 + UniProt 号」时，
    用 cDNA 顶替属于答非所问，故这里直接打 UniProt：一次请求同时拿到
    accession 与氨基酸序列，protein 与 assembly 无关（``assembly`` 仅为接口对齐）。
    """
    import json as _json
    import urllib.parse
    import urllib.request

    from gene_toolkit import normalize_gene_name

    shown = (normalize_gene_name(gene) or gene or "").strip()
    key = shown.lower()
    hit = _PROT_CACHE.get(key)
    if hit:
        return dict(hit, cached=True)

    def _query(q: str) -> dict:
        url = "https://rest.uniprot.org/uniprotkb/search?" + urllib.parse.urlencode(
            {"query": q, "fields": "accession,sequence,protein_name",
             "format": "json", "size": "1"}
        )
        req = urllib.request.Request(url, headers={"User-Agent": "zebrafish-hub/1.0"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=20) as r:
            return _json.loads(r.read().decode("utf-8", "replace"))

    try:
        data = _query("gene_exact:%s AND organism_id:7955" % shown)
        if not (data.get("results") or []):
            data = _query("gene:%s AND organism_id:7955" % shown)
    except Exception as e:  # noqa: BLE001
        return {"success": False, "gene": shown, "error": "UniProt 查询失败：%s" % e}

    results = data.get("results") or []
    if not results:
        return {"success": False, "gene": shown,
                "error": "UniProt 里没有 %s 的蛋白记录（可能不是蛋白编码基因）" % shown}
    r0 = results[0]
    seq = ((r0.get("sequence") or {}).get("value") or "").strip()
    acc = r0.get("primaryAccession") or ""
    if len(seq) < 10:
        return {"success": False, "gene": shown, "error": "UniProt 返回的蛋白序列为空"}
    try:
        pname = (((r0.get("proteinDescription") or {}).get("recommendedName") or {})
                 .get("fullName", {}) or {}).get("value") or ""
    except Exception:  # noqa: BLE001
        pname = ""
    out = {
        "success": True,
        "gene": shown,
        "sequence_type": "protein",
        "source": "uniprot_rest",
        "assembly": "",
        "accession": acc,
        "accession_type": "uniprot",
        "protein_name": pname,
        "length": len(seq),
        "sequence": seq,
        "fasta": ">%s|%s|protein|UniProt\n%s" % (shown, acc, seq),
    }
    _PROT_CACHE[key] = out
    return dict(out)


def fetch_genomic_sequence(gene: str, flank: int = 0, assembly: str = "auto") -> dict:
    """基因体序列。

    ``assembly='GRCz12tu'`` 时用主表坐标 + NCBI EFetch 取 GRCz12tu 序列；
    其余情况沿用 Ensembl REST（GRCz11），与原行为一致。
    """
    from gene_collector import _api_get, SPECIES
    from gene_toolkit import normalize_gene_name

    want = _norm_assembly(assembly)
    # 2026-11-12：auto 模式与 promoter 一致 —— 先 GRCz12tu（本地主表 + 本地
    # 基因组 faidx，毫秒级），失败才走 GRCz11/Ensembl。
    if want in ("GRCz12tu", "auto"):
        loc = _grcz12tu_locus(gene)
        if loc and loc.get("accession"):
            start = max(1, loc["start"] - flank) if flank > 0 else loc["start"]
            end = loc["end"] + flank if flank > 0 else loc["end"]
            strand = "-" if loc["strand"] == "-" else "+"
            seq, src = _ncbi_region_src(loc["accession"], start, end, strand)
            if seq:
                region = f"{loc['chrom']}:{start}-{end}:{strand}"
                return {
                    "success": True,
                    "gene": loc["symbol"],
                    "ensembl_id": "",
                    "sequence_type": "genomic",
                    "source": src,
                    "assembly": "GRCz12tu",
                    "accession": loc["accession"],
                    "region": region,
                    "length": len(seq),
                    "sequence": seq.upper(),
                    "fasta": f">{loc['symbol']}|{loc['accession']}|genomic|GRCz12tu|{region}\n{seq.upper()}",
                }
        if want == "GRCz12tu":
            if not loc:
                return {"success": False, "gene": gene, "assembly": "GRCz12tu",
                        "error": "GRCz12tu 主表里没有该基因的坐标（可改用 GRCz11，或把版本改为「自动」）"}
            return {"success": False, "gene": loc.get("symbol") or gene, "assembly": "GRCz12tu",
                    "error": f"本地没有 GRCz12tu 第 {loc.get('chrom')} 号染色体的登录号（可改用 GRCz11）"}
        # auto：本地没拿到就继续走下面的 GRCz11 路径

    name = normalize_gene_name(gene)
    lookup = _lookup_gene(name)
    if not lookup:
        return {"success": False, "gene": gene, "error": f"未找到基因 {name}"}
    seq_name = lookup.get("seq_region_name") or lookup.get("chromosome")
    start = int(lookup.get("start") or 0)
    end = int(lookup.get("end") or 0)
    strand = int(lookup.get("strand") or 1)
    if not seq_name or start <= 0 or end <= 0:
        return {"success": False, "gene": name, "error": "无基因组坐标"}
    if flank > 0:
        start = max(1, start - flank)
        end = end + flank
    region = f"{seq_name}:{start}-{end}:{strand}"
    # 2026-11-12：先试本地 GRCz11 基因组（毫秒级）；Ensembl 冷启动单次 24.6s，
    # 只在本地文件缺失或染色体名对不上时才回退到网络。
    seq = local_grcz11_region(seq_name, start, end, strand)
    src = "local_faidx"
    if not seq:
        data = _api_get(f"/sequence/region/{SPECIES}/{region}", {}, timeout=25, retries=1)
        seq = (data.get("seq") if isinstance(data, dict) else "") or ""
        src = "ensembl_rest"
    if not seq:
        return {"success": False, "gene": name, "error": "Ensembl 未返回基因组序列"}
    eid = lookup.get("id") or ""
    return {
        "success": True,
        "gene": name,
        "ensembl_id": eid,
        "sequence_type": "genomic",
        "source": src,
        "assembly": "GRCz11",
        "region": region,
        "length": len(seq),
        "sequence": seq.upper(),
        "fasta": f">{name}|{eid}|genomic|GRCz11|{region}\n{seq.upper()}",
    }


# ---------------------------------------------------------------------------
# 本地 GRCz11 基因组区间提取（faidx 语义）
#
# 2026-11-12：promoter / genomic 走 Ensembl region API，冷启动实测单次 24.6s、
# 高峰期 13~16s，是「序列工具页 15 秒」的主要成分。而服务器本地本来就有完整的
# GRCz11 DNA FASTA（Danio_rerio.GRCz11.dna.toplevel.fa.gz，配套 .fai 由
# scripts/_build_grcz11_fai.py 一次性生成）。按 faidx 格式 seek 读取，任意区间
# 毫秒级；Ensembl 只留作本地文件缺失/染色体名对不上时的回退。
# ---------------------------------------------------------------------------

_GRCZ11_FA = Path(os.getenv(
    "GRCZ11_GENOME_FA",
    "/data/zoujin/deng/zebrafish_runtime/omics_structured/01_Ensembl_GRCz11/"
    "Danio_rerio.GRCz11.dna.toplevel.fa",
))
_GRCZ12TU_FA = Path(os.getenv(
    "GRCZ12TU_GENOME_FA",
    "/data/zoujin/deng/zebrafish_runtime/blast/grcz12tu_genome.fna",
))
_FAI_CACHES: Dict[str, Dict[str, tuple]] = {}
_COMP_TABLE = str.maketrans(
    "ACGTNacgtnRYKMBVDHrykmbvdh",
    "TGCANtgcanYRMKVBHDyrmkvbhd",
)


def _revcomp(seq: str) -> str:
    return seq.translate(_COMP_TABLE)[::-1]


def _faidx_cache(fa: Path) -> Dict[str, tuple]:
    """加载某基因组的 .fai 索引（name -> len/offset/linebases/linewidth），进程内缓存。"""
    key = str(fa)
    cached = _FAI_CACHES.get(key)
    if cached is not None:
        return cached
    table: Dict[str, tuple] = {}
    fai = fa.with_name(fa.name + ".fai")
    if fa.is_file() and fai.is_file():
        try:
            with open(fai, "r", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) >= 5:
                        try:
                            table[parts[0]] = (
                                int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]))
                        except ValueError:
                            continue
        except OSError:
            pass
    _FAI_CACHES[key] = table
    return table


def _faidx_chrom(table: Dict[str, tuple], chrom: str):
    """染色体名匹配：原样 → accession 版本号容差 → chr 前缀互查。

    GRCz12tu 主表给的是 NC_133176.1（带版本），而部分调用点传 NC_133176
    （不带版本）；fai 键是 NC_133176.1。不做版本号容差就会静默错过本地命中，
    白白回落到 13~16 s 的网络请求。
    """
    rec = table.get(chrom)
    if rec is not None:
        return rec
    if "." in chrom:
        rec = table.get(chrom.split(".", 1)[0])
        if rec is not None:
            return rec
    else:
        for k, v in table.items():
            if k.split(".", 1)[0] == chrom:
                return v
    if (chrom or "").lower().startswith("chr"):
        return table.get(chrom[3:])
    return table.get("chr" + chrom)


def _faidx_region(fa: Path, chrom: str, start: int, end: int, strand) -> str:
    """按 faidx 语义取 [start,end]（1-based 闭区间）；失败返回空串。

    与 Ensembl/NCBI 语义一致：strand="-" 时返回反向互补序列。文件或索引缺失、
    染色体名对不上、区间越界都静默回空，由调用方回退网络数据源。
    """
    table = _faidx_cache(fa)
    if not table:
        return ""
    rec = _faidx_chrom(table, chrom)
    if rec is None:
        return ""
    ln, off, lb, lw = rec
    s = max(1, int(start))
    e = min(ln, int(end))
    if s > e or lb <= 0 or lw < lb:
        return ""
    byte_start = off + (s - 1) // lb * lw + (s - 1) % lb
    need = e - s + 1
    # 读取量：序列本身 + 中途所有换行的字节开销。
    # 之前只多读一行（need + lw），小区间（启动子 2 kb）碰巧够用，
    # 但基因体 12 kb 跨 158 行就凑不满长度、静默回空回落到 13~16 s 的网络请求。
    raw = b""
    try:
        with open(fa, "rb") as fh:
            fh.seek(byte_start)
            raw = fh.read(need + (need // lb + 2) * (lw - lb))
    except OSError:
        return ""
    out = []
    got = 0
    for b in raw:
        c = chr(b)
        if c == "\n" or c == "\r":
            continue
        out.append(c)
        got += 1
        if got >= need:
            break
    seq = "".join(out).upper()
    if len(seq) < need:
        return ""
    if str(strand or "+") in ("-", "-1", "reverse"):
        seq = _revcomp(seq)
    return seq


def local_grcz11_region(chrom: str, start: int, end: int, strand) -> str:
    return _faidx_region(_GRCZ11_FA, chrom, start, end, strand)


def local_grcz12tu_region(accession: str, start: int, end: int, strand) -> str:
    return _faidx_region(_GRCZ12TU_FA, accession, start, end, strand)


def _promoter_grcz12tu(gene: str, upstream: int, downstream: int) -> dict:
    """按 GRCz12tu 主表坐标算 TSS 窗口，再由 NCBI EFetch 取序列（不经 Ensembl）。"""
    loc = _grcz12tu_locus(gene)
    if not loc:
        return {"success": False, "gene": gene, "assembly": "GRCz12tu",
                "error": "GRCz12tu 主表里没有该基因的坐标（可改用 GRCz11，或把版本改为「自动」）"}
    if not loc["accession"]:
        return {"success": False, "gene": loc["symbol"], "assembly": "GRCz12tu",
                "error": f"本地没有 GRCz12tu 第 {loc['chrom']} 号染色体的 RefSeq 登录号（可改用 GRCz11）"}
    if loc["strand"] == "-":
        tss = loc["end"]
        # downstream=0 表示「只要上游 N bp」：TSS 这一位本身不计入，否则会多 1 bp
        p_start = max(1, tss - downstream + (1 if downstream == 0 else 0))
        p_end = tss + upstream
    else:
        tss = loc["start"]
        p_start = max(1, tss - upstream)
        p_end = tss + downstream - (1 if downstream == 0 else 0)
    sign = "-" if loc["strand"] == "-" else "+"
    seq, src = _ncbi_region_src(loc["accession"], p_start, p_end, sign)
    if not seq:
        return {"success": False, "gene": loc["symbol"], "assembly": "GRCz12tu",
                "error": "未取到启动子区序列（本地基因组与 NCBI 均未命中）"}
    region = f"{loc['chrom']}:{p_start}-{p_end}:{sign}"
    return {
        "success": True,
        "gene": loc["symbol"],
        "ensembl_id": "",
        "sequence_type": "promoter",
        "source": src,
        "assembly": "GRCz12tu",
        "accession": loc["accession"],
        "tss": tss,
        "upstream_bp": upstream,
        "downstream_bp": downstream,
        "region": region,
        "length": len(seq),
        "sequence": seq.upper(),
        "fasta": f">{loc['symbol']}|{loc['accession']}|promoter|GRCz12tu|{region}\n{seq.upper()}",
    }


def _promoter_ensembl(gene: str, upstream: int, downstream: int) -> dict:
    """Ensembl REST（GRCz11）按 TSS 坐标取启动子；失败统一返回 success=False。"""
    from gene_collector import _api_get, SPECIES
    from gene_toolkit import normalize_gene_name

    name = normalize_gene_name(gene)
    lookup = None
    try:
        lookup = _lookup_gene(name)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[序列] Ensembl 查基因失败 {name}: {e}")
    if not lookup:
        return {"success": False, "gene": name, "error": f"未找到基因 {name}"}
    seq_name = lookup.get("seq_region_name") or lookup.get("chromosome")
    start = int(lookup.get("start") or 0)
    end = int(lookup.get("end") or 0)
    strand = int(lookup.get("strand") or 1)
    if not seq_name or start <= 0:
        return {"success": False, "gene": name, "error": "无 TSS 坐标"}
    if strand >= 0:
        tss = start
        p_start = max(1, tss - upstream)
        p_end = tss + downstream - (1 if downstream == 0 else 0)
    else:
        tss = end
        p_start = max(1, tss - downstream + (1 if downstream == 0 else 0))
        p_end = tss + upstream
    region = f"{seq_name}:{p_start}-{p_end}:{strand}"
    # 2026-11-12：先试本地 GRCz11 基因组（毫秒级）；Ensembl 冷启动单次 24.6s，
    # 只在本地文件缺失或染色体名对不上时才回退到网络。
    seq = local_grcz11_region(seq_name, p_start, p_end, strand)
    src = "local_faidx"
    if not seq:
        # 只重试一次：默认 25s x 3 次会把一次慢响应放大成 75s，直接撞满 45s 总预算，
        # 用户拿到「超时」而不是「Ensembl 这次没答上来」。
        # 单次仍留 25s——收到 12s 会让启动子在高延迟时段直接失败（实测过）。
        try:
            data = _api_get(f"/sequence/region/{SPECIES}/{region}", {}, timeout=25, retries=1)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[序列] Ensembl 取启动子失败 {region}: {e}")
            data = None
        seq = (data.get("seq") if isinstance(data, dict) else "") or ""
        src = "ensembl_rest"
    if not seq:
        return {"success": False, "gene": name, "error": f"启动子区序列获取失败（{region}）"}
    eid = lookup.get("id") or ""
    return {
        "success": True,
        "gene": name,
        "ensembl_id": eid,
        "sequence_type": "promoter",
        "source": src,
        "assembly": "GRCz11",
        "tss": tss,
        "upstream_bp": upstream,
        "downstream_bp": downstream,
        "region": region,
        "length": len(seq),
        "sequence": seq.upper(),
        "fasta": f">{name}|{eid}|promoter|GRCz11|{region}\n{seq.upper()}",
    }


def fetch_promoter_sequence(
    gene: str,
    upstream: int = 2000,
    downstream: int = 200,
    assembly: str = "auto",
) -> dict:
    """TSS 上游启动子区。

    ``assembly='GRCz12tu'``：本地主表坐标 + NCBI EFetch。
    ``assembly='GRCz11'``：只走 Ensembl REST，不跨版本回退。
    ``assembly='auto'``：Ensembl 限时 12 s；没答上来就回退 GRCz12tu + NCBI。

    ``ENSEMBL_DEADLINE`` 的意义：``extract_sequences`` 给整轮只有 45 s，而实测
    从服务器连 rest.ensembl.org 会先磨 21 s 拿 503、再磨 28 s 超时——等轮到回退，
    外层早已把这一路 future 取消，回退结果被丢掉，用户仍看到失败。所以必须在
    预算内先放弃 Ensembl。
    """
    import concurrent.futures

    want = _norm_assembly(assembly)
    if want == "GRCz12tu":
        return _promoter_grcz12tu(gene, upstream, downstream)
    if want == "GRCz11":
        return _promoter_ensembl(gene, upstream, downstream)

    res: Dict[str, Any] = {"success": False, "gene": gene, "error": "Ensembl 未返回"}
    # 2026-11-12：auto 模式顺序反转 —— 先 GRCz12tu（本地主表坐标 + 本地基因组
    # faidx，全程毫秒级），失败才试 GRCz11（要先打 Ensembl lookup，冷启动实测
    # 10~12 s，还会把 12 s 预算磨光）。旧顺序等于让每个首次查询都付一次
    # Ensembl 的网络税；序列本身两版都是官方参考，GRCz12tu 与 BLAST/cDNA 同源，
    # 结果口径反而更一致。
    local_first = _promoter_grcz12tu(gene, upstream, downstream)
    if local_first.get("success"):
        return local_first

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(_promoter_ensembl, gene, upstream, downstream)
        try:
            res = fut.result(timeout=ENSEMBL_DEADLINE)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            res = {"success": False, "gene": gene,
                   "error": f"Ensembl 超过 {int(ENSEMBL_DEADLINE)}s 未返回"}
            logger.warning(f"[序列] Ensembl 启动子超过 {ENSEMBL_DEADLINE}s：{gene}")
        except Exception as e:  # noqa: BLE001
            res = {"success": False, "gene": gene, "error": f"Ensembl 失败：{e}"}
    finally:
        # 不等残留线程：urllib 自己会在 25s 超时后收尾
        pool.shutdown(wait=False)

    # GRCz12tu 本地路径失败时不再回退 NCBI 旧兜底（_promoter_grcz12tu 内部已含
    # 本地 faidx → NCBI 的两级），直接给出 Ensembl/GRCz11 的结果或失败原因。
    return res

def _norm_chrom(chrom: str) -> str:
    c = str(chrom or "").strip()
    if not c:
        return ""
    c = re.sub(r"^(chr|Chr|CHR)", "", c).strip()
    return "MT" if c.upper() in ("MT", "M", "MITO") else c


def _refseq_accession_for(chrom: str) -> dict:
    """染色体号 → GRCz12tu 的 RefSeq 登录号（来自本地 genome_info.json）。"""
    from genome_toolkit import genome_info

    want = _norm_chrom(chrom)
    info = genome_info()
    for seq in info.get("chromosomes") or []:
        if str(seq.get("chr") or "") == want:
            return {"accession": seq.get("name") or "", "length": int(seq.get("length") or 0)}
    # 也允许直接给 NC_ 登录号
    if want.upper().startswith("NC_"):
        return {"accession": want, "length": 0}
    return {}


def _ncbi_region_src(accession: str, start: int, end: int, strand: str) -> tuple:
    """按坐标取 GRCz12tu 序列，返回 (seq, source)。

    2026-11-12：**先走本地 FASTA 随机访问**（blast/grcz12tu_genome.fna 1.4 GB +
    .fai 索引早已随 BLAST 库建好，seek 读取毫秒级），本地不可用才回退 NCBI
    EFetch。旧注释说「建索引要分钟级」已不成立——索引就在那里，而且 NCBI 网络往返
    实测 13~16 s，是序列工具页「第一次查询要等十几秒」的主要成分。
    """
    seq = local_grcz12tu_region(accession, start, end, strand)
    if seq:
        return seq, "local_faidx"

    import os as _os

    from config import _BASE_DIR  # noqa: F401  （保持与其它模块一致的配置加载顺序）

    rev = str(strand or "+") in ("-", "-1", "reverse")
    params = {
        "db": "nuccore",
        "id": accession,
        "seq_start": int(start),
        "seq_stop": int(end),
        "strand": 2 if rev else 1,
        "rettype": "fasta",
        "retmode": "text",
    }
    key = _os.getenv("NCBI_API_KEY", "").strip()
    if key:
        params["api_key"] = key
    mail = _os.getenv("NCBI_EMAIL", "").strip()
    if mail:
        params["email"] = mail

    import urllib.parse
    import urllib.request

    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
    with urllib.request.urlopen(req, timeout=int(_os.getenv("NCBI_EFETCH_TIMEOUT", "25"))) as resp:
        text = resp.read().decode("utf-8", "replace")
    seq = "".join(line.strip() for line in text.splitlines() if not line.startswith(">"))
    return re.sub(r"\s+", "", seq).upper(), "ncbi_efetch"


def _ncbi_region(accession: str, start: int, end: int, strand: str) -> str:
    """兼容旧签名：只要序列字符串。"""
    return _ncbi_region_src(accession, start, end, strand)[0]


# 2026-11-07：本机只有两套序列数据源 —— GRCz11（Ensembl REST）与
# GRCz12tu（NCBI RefSeq EFetch）。其它装配（Zv 系列 / GRCz13ab / 品系组装）
# 只有坐标 chain，没有碱基，必须先把区间换算到这两版之一。
_REGION_ASSEMBLIES = {
    "grcz11": "GRCz11", "danrer11": "GRCz11", "zv11": "GRCz11",
    "grcz12tu": "GRCz12tu", "grcz12": "GRCz12tu", "refseq": "GRCz12tu",
    "auto": "GRCz12tu", "": "GRCz12tu",
}


def norm_region_assembly(value: Any) -> str:
    """把装配写法收敛成 GRCz11 / GRCz12tu；不认识返回空串（绝不猜）。"""
    key = re.sub(r"[\s\-_]", "", str(value or "")).lower()
    return _REGION_ASSEMBLIES.get(key, "")


def fetch_region_sequence(
    chrom: str,
    start: int,
    end: int,
    strand: str = "+",
    assembly: str = "GRCz12tu",
) -> dict:
    """按「染色体 + 起止坐标」取序列。

    默认组装 GRCz12tu（与 BLAST / cDNA 同一版参考）；也可选 GRCz11（Ensembl REST，
    与本地注释库坐标一致）。两者都明确写清组装，避免和"按基因名提取"混淆。
    """
    c = _norm_chrom(chrom)
    try:
        s, e = int(start), int(end)
    except (TypeError, ValueError):
        return {"success": False, "error": "坐标必须是整数"}
    if not c:
        return {"success": False, "error": "缺少染色体"}
    if s <= 0 or e <= 0:
        return {"success": False, "error": "坐标必须大于 0"}
    if e < s:
        s, e = e, s
    span = e - s + 1
    if span > 100000:
        return {"success": False, "error": "单次最多 100,000 bp，请缩小区间"}
    # 2026-11-07：以前是 `asm = "GRCz11" if "11" in assembly else "GRCz12tu"`，
    # 任何不认识的版本（Zv9 / GRCz13ab / 品系组装…）都会被静默当成 GRCz12tu，
    # 取出一段坐标完全对不上、但看起来正常的碱基。改成显式白名单，不认识就报错。
    asm = norm_region_assembly(assembly)
    if not asm:
        return {"success": False,
                "error": "不支持的装配版本：%s（本机序列数据仅 GRCz11 / GRCz12tu；"
                         "其它版本请先用坐标换算把区间转到这两版之一）" % (assembly or "空")}
    rev = str(strand or "+") in ("-", "-1", "reverse")

    if asm == "GRCz11":
        try:
            from gene_collector import _api_get, SPECIES

            region = f"{c}:{s}-{e}:{-1 if rev else 1}"
            data = _api_get(f"/sequence/region/{SPECIES}/{region}", {}, retries=2, timeout=20)
            seq = ((data or {}).get("seq") or "").upper() if isinstance(data, dict) else ""
            if not seq:
                return {"success": False, "error": "Ensembl 未返回该区间序列（检查染色体号 / 组装版本）"}
            return _region_payload(c, s, e, rev, "GRCz11", "ensembl_rest", region, seq)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"Ensembl 请求失败：{exc}"}

    hit = _refseq_accession_for(c)
    if not hit.get("accession"):
        return {"success": False,
                "error": f"本地没有 GRCz12tu 第 {c} 号染色体的 RefSeq 登录号（可改用 GRCz11）"}
    acc = hit["accession"]
    if hit.get("length") and e > hit["length"]:
        return {"success": False,
                "error": f"终点 {e} 超出该染色体长度 {hit['length']}（GRCz12tu）"}
    try:
        seq = _ncbi_region(acc, s, e, "-" if rev else "+")
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": f"NCBI EFetch 请求失败：{exc}"}
    if not seq:
        return {"success": False, "error": "NCBI 未返回序列（登录号或区间可能不合法）"}
    region = f"{acc}:{s}-{e}({'-' if rev else '+'})"
    return _region_payload(c, s, e, rev, "GRCz12tu", "ncbi_efetch", region, seq, acc)


def _region_payload(chrom: str, start: int, end: int, rev: bool, assembly: str,
                    source: str, region: str, seq: str, accession: str = "") -> dict:
    return {
        "success": True,
        "sequence_type": "region",
        "chromosome": chrom,
        "start": start,
        "end": end,
        "strand": "-" if rev else "+",
        "assembly": assembly,
        "source": source,
        "accession": accession,
        "region": region,
        "length": len(seq),
        "sequence": seq,
        "fasta": f">{assembly}|chr{chrom}:{start}-{end}({'-' if rev else '+'})|{source}\n{seq}",
    }


# 序列结果缓存：同一装配的同一基因不会变，重复提问不该再等一次 Ensembl。
# TTL 默认 1 小时，最多 256 条（一条序列几十 KB，上限约 20 MB）。
_SEQ_CACHE: Dict[tuple, tuple] = {}
_SEQ_CACHE_MAX = int(os.getenv("SEQ_CACHE_MAX", "256"))
_SEQ_CACHE_TTL = float(os.getenv("SEQ_CACHE_TTL_SEC", "3600"))


def _seq_cache_get(key: tuple):
    item = _SEQ_CACHE.get(key)
    if not item:
        return None
    ts, val = item
    if (time.time() - ts) > _SEQ_CACHE_TTL:
        _SEQ_CACHE.pop(key, None)
        return None
    return copy.deepcopy(val)


def _seq_cache_put(key: tuple, val: dict) -> None:
    if len(_SEQ_CACHE) >= _SEQ_CACHE_MAX:
        oldest = min(_SEQ_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _SEQ_CACHE.pop(oldest, None)
    _SEQ_CACHE[key] = (time.time(), copy.deepcopy(val))


def extract_sequences(gene: str, kinds: Optional[list] = None,
                      assembly: str = "auto",
                      promoter_upstream: Optional[int] = None,
                      promoter_downstream: Optional[int] = None) -> Dict[str, Any]:
    """交互式提取；硬时限避免 Ensembl 慢请求拖死接口。

    ``assembly``：auto / GRCz11 / GRCz12tu。auto 保持旧行为（cDNA 取 GRCz12tu 本地
    RefSeq，启动子与基因体取 Ensembl GRCz11）；指定版本则三类型统一按该版本取，
    且**不跨版本回退**，避免拿到与所选版本不符的序列。
    """
    import concurrent.futures

    want = _norm_assembly(assembly)
    kinds = kinds or ["cdna", "promoter"]
    cache_key = (str(gene or "").strip().lower(), tuple(sorted(kinds)), want,
                 promoter_upstream, promoter_downstream)
    cached = _seq_cache_get(cache_key)
    if cached is not None:
        cached["cached"] = True
        return cached
    if want == "GRCz11":
        policy = {
            "cdna": "Ensembl REST GRCz11（按 Ensembl Gene ID）",
            "promoter": "Ensembl REST GRCz11（TSS 坐标）",
            "genomic": "Ensembl REST GRCz11",
        }
    elif want == "GRCz12tu":
        policy = {
            "cdna": "NCBI RefSeq GRCz12tu CDS（本地 FASTA）",
            "promoter": "NCBI EFetch GRCz12tu（gene_master_grcz12tu 坐标）",
            "genomic": "NCBI EFetch GRCz12tu（gene_master_grcz12tu 坐标）",
        }
    else:
        policy = {
            "cdna": "NCBI RefSeq GRCz12tu CDS/RNA（与 BLAST 同库）",
            "promoter": "Ensembl REST GRCz11（TSS 坐标）",
            "genomic": "Ensembl REST GRCz11",
        }
    out: Dict[str, Any] = {
        "gene": gene,
        "assembly": want,
        "results": {},
        "policy": policy,
    }
    if "protein" in kinds:
        out["policy"]["protein"] = "UniProt REST（蛋白序列与装配版本无关）"

    # 本地目录没收录的标识符：立即失败并给候选，绝不发 Ensembl / NCBI 请求。
    # 以前这类输入会走完 12s Ensembl 截止 + 3×25s 重试，工具页卡 18~45s 甚至超时。
    if not local_gene_known(gene):
        out["success"] = False
        out["error"] = ("库里没有基因「%s」（本地目录未收录，已跳过 Ensembl / NCBI 网络回退）"
                        % str(gene or "").strip())
        out["suggestions"] = suggest_gene_symbols(gene)
        out["results"] = {
            k: {"success": False, "gene": gene,
                "error": "本地目录未收录该基因，未发起网络查询"}
            for k in kinds
        }
        return out

    jobs = []
    if "protein" in kinds:
        jobs.append(("protein", lambda g: fetch_protein_sequence(g)))
    if "cdna" in kinds:
        jobs.append(("cdna", lambda g: fetch_cdna_sequence(g, assembly=want)))
    if "promoter" in kinds:
        _up = 2000 if promoter_upstream is None else int(promoter_upstream)
        _down = 200 if promoter_downstream is None else int(promoter_downstream)
        jobs.append(("promoter",
                     lambda g: fetch_promoter_sequence(g, upstream=_up,
                                                       downstream=_down, assembly=want)))
    if "genomic" in kinds:
        jobs.append(("genomic", lambda g: fetch_genomic_sequence(g, flank=500, assembly=want)))

    # 不能用 `with ThreadPoolExecutor`：`__exit__` 会 join 所有线程，
    # 于是 wait(timeout=budget) 之后仍被慢线程拖到 60~75s，"硬时限" 名存实亡。
    # 显式 shutdown(wait=False) 才是真正到点就返回。
    budget = float(os.getenv("SEQ_EXTRACT_BUDGET_SEC", "30"))
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=min(3, max(1, len(jobs))))
    try:
        futs = {pool.submit(fn, gene): key for key, fn in jobs}
        done, pending = concurrent.futures.wait(futs, timeout=budget)
        for fut in done:
            key = futs[fut]
            try:
                out["results"][key] = fut.result()
            except Exception as e:
                logger.warning(f"[序列] {key} 失败: {e}")
                out["results"][key] = {"success": False, "gene": gene, "error": f"{key} 失败：{e}"}
        for fut in pending:
            key = futs[fut]
            fut.cancel()
            out["results"][key] = {
                "success": False,
                "gene": gene,
                "error": f"{key} 超时（Ensembl，>{int(budget)}s）",
            }
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    ok = any(r.get("success") for r in out["results"].values())
    out["success"] = ok
    if not ok:
        if want == "GRCz12tu":
            out["error"] = "GRCz12tu 未取到任何序列（本地 RefSeq FASTA / NCBI 均未命中）"
        elif want == "GRCz11":
            out["error"] = "GRCz11（Ensembl REST）未取到任何序列，可能超时或无该基因注释"
        else:
            out["error"] = "未能提取任何序列（本地 GRCz12tu FASTA 未命中且 Ensembl 可能超时）"
    else:
        # 只缓存成功结果：失败多为上游临时故障，缓存住会让用户一直看到旧的失败
        _seq_cache_put(cache_key, out)
    return out


def format_sequence_answer(data: dict, lang: str = "zh", full: bool = False) -> str:
    """渲染序列结果。

    ``full=True`` 输出完整序列（提问明确要求「取/给出序列」时），
    否则只给 80 字符预览——预览模式用于「介绍一下这个基因」这类顺带提及的场景。
    """
    en = (lang or "zh").lower().startswith("en")
    if not data.get("success"):
        if en:
            # 库里的 error 是中文，英文回答直接透出会中英混排；给结构化英文说明
            return ("Sequence extraction failed for %s (assembly %s). No record was "
                    "found in the local RefSeq CDS FASTA, NCBI EFetch or Ensembl REST "
                    "for this assembly." % (data.get("gene") or "?", data.get("assembly") or "auto"))
        return data.get("error") or "序列提取失败"
    _asm = str(data.get("assembly") or "").strip()
    _tag = "" if _asm in ("", "auto") else (" · " + _asm)
    lines = [("[Sequence] " if en else "【序列提取】") + str(data.get("gene", "?")) + _tag]
    for key, block in (data.get("results") or {}).items():
        if not block.get("success"):
            lines.append(
                f"  · {key}: {block.get('error', 'failed' if en else '失败')}"
            )
            continue
        kind = block.get("sequence_type") or key
        unit = "aa" if kind == "protein" else "bp"
        asm = block.get("assembly") or ""
        src = block.get("source") or ""
        acc = block.get("accession") or ""
        acc_type = block.get("accession_type") or ""
        _ACC_LABEL = {"refseq_mrna": "RefSeq mRNA", "protein": "protein accession",
                      "uniprot": "UniProt", "ensembl_gene": "Ensembl"}
        acc_txt = ("%s, %s" % (acc, _ACC_LABEL.get(acc_type, acc_type))) if (acc and acc_type) else acc
        extra = " · ".join(x for x in (asm, src, acc_txt) if x)
        label = {"cdna": "CDS/cDNA", "promoter": "promoter",
                 "genomic": "genomic", "protein": "protein"}.get(kind, kind)
        region = block.get("region") or block.get("ensembl_id") or ""
        head = f"  · {label}: {block.get('length')} {unit}"
        if extra:
            head += f" [{extra}]"
        if region:
            head += f" ({region})"
        lines.append(head)
        # 说明按语言给：库里存的 note 是中文，英文回答直接用它会出现中文
        if kind == "cdna":
            lines.append("    " + (
                "coding sequence (CDS) from the start codon, not the full-length transcript"
                if en else "编码序列（CDS，从起始密码子起），不是全长转录本"))
        elif kind == "protein":
            lines.append("    " + ("UniProt canonical protein sequence"
                                   if en else "UniProt 规范蛋白序列"))
        seq = block.get("sequence") or ""
        if not seq:
            continue
        if full:
            lines.append("```")
            for i in range(0, len(seq), 60):
                lines.append(seq[i:i + 60])
            lines.append("```")
        else:
            preview = seq[:80] + ("…" if len(seq) > 80 else "")
            lines.append(f"    {preview}")
    return "\n".join(lines)
