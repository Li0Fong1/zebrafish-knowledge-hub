# -*- coding: utf-8 -*-
"""基因序列 / 启动子提取。

cDNA：NCBI RefSeq GRCz12tu CDS/RNA（与 BLAST 同库），识别 `[gene=tp53]` 头。
启动子 / 基因组区：Ensembl REST GRCz11 坐标（须在 JSON 与界面标明组装）。
"""
import bootstrap  # noqa: F401

import os
import re
from typing import Any, Dict, List, Optional

from common_utils import logger

_GENE_TAG_RE = re.compile(r"\[gene=([^\]]+)\]", re.I)
_PROTEIN_ID_RE = re.compile(r"\[protein_id=([^\]]+)\]", re.I)
_CDNA_CACHE: Dict[str, dict] = {}


def _lookup_gene(gene: str) -> Optional[dict]:
    from gene_collector import resolve_lookup

    return resolve_lookup(gene)


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


def _local_cdna_from_fasta(symbols: List[str], ensembl_id: str = "") -> dict:
    needles = _symbol_needles(symbols, ensembl_id)
    if not needles:
        return {}
    cache_key = "|".join(n.lower() for n in needles)
    cached = _CDNA_CACHE.get(cache_key)
    if cached:
        return cached
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


def fetch_cdna_sequence(gene: str) -> dict:
    from gene_id_mapper import resolve_gene_identifier
    from gene_toolkit import normalize_gene_name

    name = normalize_gene_name(gene)
    resolved = resolve_gene_identifier(name, use_ensembl_fallback=False)
    can = resolved.get("canonical") or {}
    std = resolved.get("standard") or {}
    eid = (can.get("ensembl_id") or "").split(".")[0]
    shown = (std.get("gene_symbol") or can.get("gene_name") or name or gene).strip()
    local_hit = _local_cdna_from_fasta(
        [shown, std.get("gene_symbol") or "", can.get("gene_name") or "", name, gene],
        eid,
    )
    seq = (local_hit.get("sequence") or "") if local_hit else ""
    source = "local_fasta" if seq else ""
    assembly = local_hit.get("assembly") if local_hit else ""
    accession = local_hit.get("protein_id") if local_hit else ""
    if len(seq) < 60 and eid:
        seq = _ensembl_cdna_by_id(eid) or seq
        if len(seq) >= 60:
            source = "ensembl_id"
            assembly = "GRCz11"
            accession = eid
    if len(seq) < 60:
        lookup = _lookup_gene(shown)
        if lookup:
            from gene_collector import fetch_cdna_from_lookup

            seq = fetch_cdna_from_lookup(lookup) or seq
            eid = eid or (lookup.get("id") or "")
            if len(seq) >= 60:
                source = "ensembl_lookup"
                assembly = "GRCz11"
                accession = eid
    if len(seq) < 60:
        return {
            "success": False,
            "gene": shown,
            "error": "无 cDNA 序列（本地 GRCz12tu FASTA 与 Ensembl 均未取到）",
        }
    fasta_id = accession or eid or shown
    return {
        "success": True,
        "gene": shown,
        "ensembl_id": eid,
        "sequence_type": "cdna",
        "source": source,
        "assembly": assembly or ("GRCz12tu" if source == "local_fasta" else "GRCz11"),
        "accession": accession,
        # 本地 GRCz12tu 的 "cdna" FASTA 实际是 CDS 集合（序列从 ATG 起），
        # 头里的号是**蛋白号 NP_/XP_**，不是核酸号 NM_/XM_。
        # 不加说明的话，用户会把 NP_001258749.1 当成 mRNA 号去 NCBI 检索，然后对不上。
        "accession_type": ("protein" if str(accession).startswith(("NP_", "XP_", "YP_"))
                           else ("ensembl_gene" if str(accession).startswith("ENS") else "")),
        "note": ("编码序列（CDS，从起始密码子起），不是全长转录本；"
                 "accession 为对应蛋白号") if str(accession).startswith(("NP_", "XP_", "YP_")) else "",
        "length": len(seq),
        "sequence": seq,
        "fasta": f">{shown}|{fasta_id}|cdna|{assembly}|{source}\n{seq}",
    }


def fetch_genomic_sequence(gene: str, flank: int = 0) -> dict:
    from gene_collector import _api_get, SPECIES
    from gene_toolkit import normalize_gene_name

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
    data = _api_get(f"/sequence/region/{SPECIES}/{region}", {})
    seq = (data.get("seq") if isinstance(data, dict) else "") or ""
    if not seq:
        return {"success": False, "gene": name, "error": "Ensembl 未返回基因组序列"}
    eid = lookup.get("id") or ""
    return {
        "success": True,
        "gene": name,
        "ensembl_id": eid,
        "sequence_type": "genomic",
        "source": "ensembl_rest",
        "assembly": "GRCz11",
        "region": region,
        "length": len(seq),
        "sequence": seq.upper(),
        "fasta": f">{name}|{eid}|genomic|GRCz11|{region}\n{seq.upper()}",
    }


def fetch_promoter_sequence(
    gene: str,
    upstream: int = 2000,
    downstream: int = 200,
) -> dict:
    from gene_collector import _api_get, SPECIES
    from gene_toolkit import normalize_gene_name

    name = normalize_gene_name(gene)
    lookup = _lookup_gene(name)
    if not lookup:
        return {"success": False, "gene": gene, "error": f"未找到基因 {name}"}
    seq_name = lookup.get("seq_region_name") or lookup.get("chromosome")
    start = int(lookup.get("start") or 0)
    end = int(lookup.get("end") or 0)
    strand = int(lookup.get("strand") or 1)
    if not seq_name or start <= 0:
        return {"success": False, "gene": name, "error": "无 TSS 坐标"}
    if strand >= 0:
        tss = start
        p_start = max(1, tss - upstream)
        p_end = tss + downstream
    else:
        tss = end
        p_start = max(1, tss - downstream)
        p_end = tss + upstream
    region = f"{seq_name}:{p_start}-{p_end}:{strand}"
    data = _api_get(f"/sequence/region/{SPECIES}/{region}", {})
    seq = (data.get("seq") if isinstance(data, dict) else "") or ""
    if not seq:
        return {"success": False, "gene": name, "error": "启动子区序列获取失败"}
    eid = lookup.get("id") or ""
    return {
        "success": True,
        "gene": name,
        "ensembl_id": eid,
        "sequence_type": "promoter",
        "source": "ensembl_rest",
        "assembly": "GRCz11",
        "tss": tss,
        "upstream_bp": upstream,
        "downstream_bp": downstream,
        "region": region,
        "length": len(seq),
        "sequence": seq.upper(),
        "fasta": f">{name}|{eid}|promoter|GRCz11|{region}\n{seq.upper()}",
    }


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


def _ncbi_region(accession: str, start: int, end: int, strand: str) -> str:
    """NCBI EFetch 按坐标取参考序列（GRCz12tu 的 NC_ 登录号）。

    为什么用 EFetch 而不是本地 FASTA 随机访问：1.45 GB 的 genome FASTA 建偏移索引
    要整文件扫一遍（分钟级），而这里一次请求只取几百 bp，NCBI 侧已建好索引。
    """
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
    return re.sub(r"\s+", "", seq).upper()


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
    asm = "GRCz11" if "11" in str(assembly or "") else "GRCz12tu"
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


def extract_sequences(gene: str, kinds: Optional[list] = None) -> Dict[str, Any]:
    """交互式提取；硬时限避免 Ensembl 慢请求拖死接口。"""
    import concurrent.futures

    kinds = kinds or ["cdna", "promoter"]
    out: Dict[str, Any] = {
        "gene": gene,
        "results": {},
        "policy": {
            "cdna": "NCBI RefSeq GRCz12tu CDS/RNA（与 BLAST 同库）",
            "promoter": "Ensembl REST GRCz11（TSS 坐标）",
            "genomic": "Ensembl REST GRCz11",
        },
    }
    jobs = []
    if "cdna" in kinds:
        jobs.append(("cdna", fetch_cdna_sequence))
    if "promoter" in kinds:
        jobs.append(("promoter", fetch_promoter_sequence))
    if "genomic" in kinds:
        jobs.append(("genomic", lambda g: fetch_genomic_sequence(g, flank=500)))

    budget = 45.0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(3, max(1, len(jobs)))) as pool:
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

    ok = any(r.get("success") for r in out["results"].values())
    out["success"] = ok
    if not ok:
        out["error"] = "未能提取任何序列（本地 GRCz12tu FASTA 未命中且 Ensembl 可能超时）"
    return out


def format_sequence_answer(data: dict) -> str:
    if not data.get("success"):
        return data.get("error") or "序列提取失败"
    lines = [f"【序列提取】{data.get('gene', '?')}"]
    for key, block in (data.get("results") or {}).items():
        if not block.get("success"):
            lines.append(f"  · {key}: {block.get('error', '失败')}")
            continue
        asm = block.get("assembly") or ""
        src = block.get("source") or ""
        extra = " ".join(x for x in (asm, src, block.get("accession") or "") if x)
        lines.append(
            f"  · {key}: {block.get('length')} bp "
            f"[{extra}] ({block.get('region') or block.get('ensembl_id', '')})"
        )
        seq = block.get("sequence") or ""
        preview = seq[:80] + ("…" if len(seq) > 80 else "")
        lines.append(f"    {preview}")
    return "\n".join(lines)
