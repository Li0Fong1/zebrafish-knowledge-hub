# -*- coding: utf-8 -*-
"""
序列比对（可发表级）：优先 NCBI BLAST+ blastn。

参考库：NCBI RefSeq GRCz12tu CDS/RNA（BLAST_FASTA / grcz12tu_cds.fna）。
缺库时禁止静默改用 GRCz11 或 data/reference.fasta 抽样库。
索引：BLAST_DB_DIR/cdna_db（makeblastdb）。
"""
import bootstrap  # noqa: F401

import math
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

from config import REFERENCE_FASTA, _BASE_DIR
from common_utils import logger

_DNA_RE = re.compile(r"[^ATCGNatcgn\s]")


def _default_blast_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ZebrafishGeneQA", "blast")


BLAST_DIR = os.getenv("BLAST_DB_DIR", _default_blast_dir())
BLAST_DB = os.path.join(BLAST_DIR, "cdna_db")
# 斑马鱼 tp53 GRCz12tu 编码区 90 bp（工具栏「填入测试序列」同源）
BLAST_SAMPLE_SEQ = (
    "ATGGCGCAAAACGACAGCCAAGAGTTCGCGGAGCTCTGGGAGAAGAATTTGATAAGTATTCAGCCCCCAGGTGGTGGCTCTTGCTGGGAC"
)
BLAST_VERSION_FILE = os.path.join(BLAST_DIR, "db_source.txt")

# blastn outfmt 6 标准字段（可写入补充表）
BLAST_OUTFMT = (
    "6 qseqid sseqid pident length mismatch gapopen "
    "qstart qend sstart send evalue bitscore qcovs stitle"
)


def _blast_bin(name: str) -> Optional[str]:
    """查找 blastn / makeblastdb 可执行文件。"""
    env_bin = os.getenv("BLAST_BIN", "").strip()
    if env_bin:
        cand = os.path.join(env_bin, name + (".exe" if os.name == "nt" else ""))
        if os.path.isfile(cand):
            return cand
    bundled = os.path.join(_BASE_DIR, "tools", "blast+", "bin", name)
    if os.name == "nt":
        bundled += ".exe"
    if os.path.isfile(bundled):
        return bundled
    return shutil.which(name)


def normalize_sequence(seq: str) -> str:
    """去除空白与 FASTA 头（>...），只保留核酸字符。"""
    if not seq:
        return ""
    if ">" in seq:
        parts = []
        for line in seq.splitlines():
            line = line.strip()
            if not line or line.startswith(">"):
                continue
            parts.append(line)
        seq = "".join(parts)
    return re.sub(r"\s+", "", seq).upper()


def validate_sequence(seq: str) -> tuple:
    seq = normalize_sequence(seq)
    if len(seq) < 20:
        return False, "序列长度至少 20 bp"
    if len(seq) > 50000:
        return False, "序列长度不能超过 50000 bp"
    cleaned = _DNA_RE.sub("", seq)
    if len(cleaned) / max(len(seq), 1) < 0.95:
        return False, "请提供有效的核酸序列（A/T/C/G/N）"
    return True, seq


def _pick_reference_fasta() -> str:
    for path in (
        os.getenv("REFERENCE_FASTA", ""),
        os.path.join(_BASE_DIR, "data", "reference_genome.fasta"),
        REFERENCE_FASTA,
        os.path.join(_BASE_DIR, "data", "reference.fasta"),
    ):
        if path and os.path.isfile(path):
            return path
    return REFERENCE_FASTA


def _is_grcz12_fasta(path: str) -> bool:
    n = (path or "").replace("\\", "/").lower()
    return "grcz12" in n or "049306965" in n


def _allow_grcz11_fallback() -> bool:
    return os.getenv("BLAST_ALLOW_GRCZ11_FALLBACK", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _fasta_ok(path: str) -> bool:
    return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 500


def _pick_blast_fasta() -> str:
    """只选 NCBI RefSeq GRCz12tu（RNA/CDS）。缺库时禁止静默改用 GRCz11 / 74 基因抽样。"""
    rt = os.getenv("ZEBRA_RUNTIME") or ""
    env_fa = (os.getenv("BLAST_FASTA") or "").strip()
    cands_12 = [
        env_fa if _is_grcz12_fasta(env_fa) else "",
        os.path.join(BLAST_DIR, "grcz12tu_rna.fna"),
        os.path.join(BLAST_DIR, "grcz12tu_cds.fna"),
        os.path.join(rt, "blast", "grcz12tu_rna.fna"),
        os.path.join(rt, "blast", "grcz12tu_cds.fna"),
        os.path.join(rt, "omics_structured", "10_toolkit_assets", "blast", "Danio_rerio.GRCz12tu.rna.fna"),
        os.path.join(rt, "omics_structured", "10_toolkit_assets", "blast", "Danio_rerio.GRCz12tu.cds.fna"),
        os.path.join(_BASE_DIR, "data", "Danio_rerio.GRCz12tu.rna.fna"),
        os.path.join(_BASE_DIR, "data", "Danio_rerio.GRCz12tu.cds.fna"),
    ]
    for p in cands_12:
        if _fasta_ok(p):
            return p
    if not _allow_grcz11_fallback():
        return ""
    cands_11 = [
        env_fa,
        os.path.join(rt, "omics_structured", "10_toolkit_assets", "blast", "Danio_rerio.GRCz11.cdna.all.fa"),
        os.path.join(_BASE_DIR, "data", "Danio_rerio.GRCz11.cdna.all.fa"),
        os.path.join(_BASE_DIR, "data", "reference_cdna.fasta"),
        _pick_reference_fasta(),
    ]
    for p in cands_11:
        if _fasta_ok(p):
            return p
    return ""


def blast_library_status() -> dict:
    fasta = _pick_blast_fasta()
    is12 = bool(fasta and _is_grcz12_fasta(fasta))
    return {
        "blast_fasta": os.path.basename(fasta) if fasta else "",
        "blast_is_grcz12": is12,
        "blast_assembly": "GRCz12tu" if is12 else ("GRCz11" if fasta else ""),
        "blast_allow_grcz11_fallback": _allow_grcz11_fallback(),
    }


def _db_fingerprint(fasta: str) -> str:
    st = os.stat(fasta)
    return f"{os.path.abspath(fasta)}\t{st.st_size}\t{int(st.st_mtime)}"


def _unique_fasta(src: str, dst: str) -> int:
    """写出去重 FASTA（同 ID 只留第一条），供 makeblastdb 使用。"""
    recs = _parse_fasta(src)
    seen: set[str] = set()
    n = 0
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(dst, "w", encoding="utf-8") as fh:
        for rec in recs:
            rid = (rec.get("id") or "").strip()
            if not rid or rid in seen:
                continue
            seen.add(rid)
            seq = rec.get("sequence") or ""
            if not seq:
                continue
            fh.write(f">{rid}\n")
            for i in range(0, len(seq), 80):
                fh.write(seq[i : i + 80] + "\n")
            n += 1
    return n


def ensure_blast_database(force: bool = False) -> dict:
    """构建 BLAST+ 核酸库（makeblastdb）。

    必须直接 ``-out`` 到 BLAST_DB。BLAST+ 5 的 .njs 记下了库名，
    不能把 cdna_db_new.* 改名成 cdna_db.*，否则 blastn 会报 ndb not found。
    """
    fasta = _pick_blast_fasta()
    if not fasta or not os.path.isfile(fasta):
        return {
            "success": False,
            "error": (
                "未找到 NCBI RefSeq GRCz12tu FASTA（BLAST_FASTA / grcz12tu_cds.fna）。"
                "禁止静默改用 GRCz11 或 74 基因抽样库。"
                "若必须临时回退，设置 BLAST_ALLOW_GRCZ11_FALLBACK=1。"
            ),
        }

    makeblastdb = _blast_bin("makeblastdb")
    os.makedirs(BLAST_DIR, exist_ok=True)
    unique_fa = os.path.join(BLAST_DIR, "reference_unique.fasta")
    fp = _db_fingerprint(fasta)

    if not force and os.path.isfile(BLAST_DB + ".nin") and os.path.isfile(BLAST_VERSION_FILE):
        with open(BLAST_VERSION_FILE, encoding="utf-8") as f:
            cached_src = f.read().strip()
        if cached_src == fp or cached_src == os.path.abspath(fasta):
            return {"success": True, "database": BLAST_DB, "source": fasta, "cached": True}

    if not makeblastdb:
        return {
            "success": False,
            "error": "未找到 makeblastdb。请运行：python -m scripts.setup_blast_linux",
            "need_blast_plus": True,
        }

    src = fasta
    n = None
    if os.path.getsize(fasta) < 8_000_000:
        n = _unique_fasta(fasta, unique_fa)
        src = unique_fa

    cmd = [
        makeblastdb,
        "-in", src,
        "-dbtype", "nucl",
        "-title", "Danio rerio GRCz12tu" if _is_grcz12_fasta(fasta) else "Danio rerio GRCz11 cDNA",
        "-out", BLAST_DB,
    ]
    try:
        subprocess.run(
            cmd, check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=1800,
        )
        with open(BLAST_VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(fp)
        logger.info(f"[BLAST] 索引已构建：{BLAST_DB} src={src} seqs={n}")
        return {"success": True, "database": BLAST_DB, "source": fasta, "seqs": n, "cached": False}
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": (e.stderr or str(e))[:500]}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _gene_symbol_from_title(title: str) -> str:
    raw = str(title or "")
    m = re.search(r"\[gene=([A-Za-z0-9._-]+)\]", raw, re.I)
    if m:
        return m.group(1)
    m = re.search(r"gene_symbol:([A-Za-z0-9._-]+)", raw, re.I)
    if m:
        return m.group(1)
    m = re.search(r"\(([A-Za-z0-9._-]+)\),\s*(?:mRNA|ncRNA|transcript)", raw, re.I)
    if m:
        return m.group(1)
    if "|" in raw:
        tail = raw.split("|")[-1].strip()
        if tail and not tail.upper().startswith("ENS"):
            return tail.split()[0]
    return ""


def _filter_weak_hits(hits: List[dict], query_len: int) -> List[dict]:
    """丢掉 12–25 bp 偶然种子命中（旧 74 基因库会把 tp53 打成 tal1/sox10）。"""
    min_aln = 18 if query_len < 50 else max(50, int(0.7 * query_len))
    kept = []
    for h in hits:
        aln = int(h.get("alignment_length") or 0)
        bit = float(h.get("bitscore") or 0)
        qcov = float(h.get("query_coverage_percent") or 0)
        if aln >= min_aln or bit >= 50 or qcov >= 60:
            title = h.get("subject_title") or h.get("subject_id") or ""
            h["gene_symbol"] = _gene_symbol_from_title(title)
            kept.append(h)
    return kept


def _parse_blast_tabular(lines: List[str]) -> List[dict]:
    hits = []
    for line in lines:
        if not line.strip():
            continue
        cols = line.strip().split("\t")
        if len(cols) < 13:
            continue
        title = cols[13] if len(cols) > 13 else cols[1]
        hits.append({
            "subject_id": cols[1],
            "identity_percent": round(float(cols[2]), 2),
            "alignment_length": int(float(cols[3])),
            "mismatch": int(float(cols[4])),
            "gapopen": int(float(cols[5])),
            "qstart": int(float(cols[6])),
            "qend": int(float(cols[7])),
            "sstart": int(float(cols[8])),
            "send": int(float(cols[9])),
            "evalue": float(cols[10]),
            "bitscore": round(float(cols[11]), 1),
            "query_coverage_percent": round(float(cols[12]), 1) if cols[12] else 0.0,
            "subject_title": title,
            "gene_symbol": _gene_symbol_from_title(title) or _gene_symbol_from_title(cols[1]),
        })
    return hits


def _run_blastn(query_seq: str, top_k: int = 10, evalue: float = 10.0) -> dict:
    blastn = _blast_bin("blastn")
    if not blastn:
        return {"success": False, "error": "未找到 blastn", "need_blast_plus": True, "hits": []}

    db_info = ensure_blast_database()
    if not db_info.get("success"):
        return {"success": False, "error": db_info.get("error"), "hits": []}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=False, encoding="utf-8") as qf:
        qf.write(f">query\n{query_seq}\n")
        query_path = qf.name

    out_path = query_path + ".blast.tsv"
    try:
        cmd = [
            blastn,
            "-query", query_path,
            "-db", BLAST_DB,
            "-out", out_path,
            "-outfmt", BLAST_OUTFMT,
            "-evalue", str(evalue),
            "-max_target_seqs", str(max(int(top_k), 20)),
            "-num_threads", os.getenv("BLAST_THREADS", "4"),
            "-task", "blastn-short" if len(query_seq) < 50 else "blastn",
        ]
        subprocess.run(
            cmd, check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=300,
        )
        with open(out_path, encoding="utf-8", errors="replace") as f:
            hits = _parse_blast_tabular(f.readlines())
        hits = _filter_weak_hits(hits, len(query_seq))[:top_k]
        src = db_info.get("source") or ""
        note = None
        if src and _is_grcz12_fasta(src):
            note = "库：NCBI RefSeq GRCz12tu（GCF_049306965，Danio rerio）"
        else:
            try:
                if src and os.path.isfile(src) and os.path.getsize(src) > 8_000_000:
                    note = "库：Ensembl GRCz11 全转录组 cDNA（Danio rerio）"
            except OSError:
                note = None
        out = {
            "success": True,
            "engine": "NCBI BLAST+ blastn",
            "query_length": len(query_seq),
            "database": src,
            "hit_count": len(hits),
            "hits": hits,
        }
        out["assembly"] = "GRCz12tu" if src and _is_grcz12_fasta(src) else "GRCz11"
        if note:
            out["note"] = note
        return out
    except subprocess.CalledProcessError as e:
        return {"success": False, "error": (e.stderr or str(e))[:500], "hits": []}
    finally:
        for p in (query_path, out_path):
            try:
                os.remove(p)
            except OSError:
                pass


def _estimate_bitscore(raw_score: float, lamb: float = 1.28, K: float = 0.46) -> float:
    if raw_score <= 0:
        return 0.0
    return round((lamb * raw_score - math.log(K)) / math.log(2), 1)


def _estimate_evalue(score: float, query_len: int, subject_len: int, db_total_len: int) -> float:
    """Karlin-Altschul 近似 E-value（回退模式）。"""
    lamb, K = 1.28, 0.46
    eff_space = max(query_len * db_total_len, 1)
    e = K * eff_space * math.exp(-lamb * score)
    return e if e >= 1e-300 else 1e-300


def _build_kmer_index(refs: List[dict], k: int = 11) -> dict:
    index: Dict[str, set] = {}
    for i, rec in enumerate(refs):
        seq = rec["sequence"]
        for j in range(len(seq) - k + 1):
            mer = seq[j:j + k]
            if "N" in mer:
                continue
            index.setdefault(mer, set()).add(i)
    return index


def _run_blast_fallback(query_seq: str, top_k: int = 10) -> dict:
    """k-mer 预筛 + 局部比对 + E-value 估算（无 BLAST+ 时回退）。"""
    fasta = _pick_blast_fasta()
    if not fasta:
        return {
            "success": False,
            "error": (
                "未找到 NCBI RefSeq GRCz12tu FASTA，且已禁止使用 GRCz11/抽样库。"
                "请配置 BLAST_FASTA 或安装 NCBI BLAST+。"
            ),
            "need_blast_plus": True,
            "hits": [],
        }
    if os.path.isfile(fasta) and os.path.getsize(fasta) > 20_000_000:
        return {
            "success": False,
            "error": "全转录组库需 NCBI BLAST+；未找到 blastn，无法在内存中索引 100MB+ FASTA",
            "need_blast_plus": True,
            "hits": [],
        }
    refs = _parse_fasta(fasta)
    if not refs:
        return {"success": False, "error": f"参考库不存在：{fasta}", "hits": []}

    try:
        from Bio.Align import PairwiseAligner
    except ImportError:
        return {"success": False, "error": "请安装 biopython", "hits": []}

    k = 11
    index = _build_kmer_index(refs, k=k)
    q_mers = [query_seq[i:i + k] for i in range(len(query_seq) - k + 1) if "N" not in query_seq[i:i + k]]
    cand_scores: Dict[int, int] = {}
    for mer in q_mers:
        for idx in index.get(mer, ()):
            cand_scores[idx] = cand_scores.get(idx, 0) + 1
    if not cand_scores:
        return {
            "success": True,
            "engine": "Biopython fallback (k-mer indexed)",
            "query_length": len(query_seq),
            "database": fasta,
            "hit_count": 0,
            "hits": [],
            "assembly": "GRCz12tu" if _is_grcz12_fasta(fasta) else "GRCz11",
            "note": "未找到 k-mer 种子匹配；建议安装 BLAST+：python -m scripts.setup_blast",
        }

    top_cands = sorted(cand_scores, key=lambda i: -cand_scores[i])[:200]
    db_total_len = sum(len(r["sequence"]) for r in refs)

    aligner = PairwiseAligner()
    aligner.mode = "local"
    aligner.match_score = 2
    aligner.mismatch_score = -3
    aligner.open_gap_score = -5
    aligner.extend_gap_score = -2

    hits = []
    for idx in top_cands:
        rec = refs[idx]
        ref_seq = rec["sequence"]
        try:
            score = aligner.score(query_seq, ref_seq)
        except Exception:
            continue
        if score <= 0:
            continue
        alns = aligner.align(query_seq, ref_seq)
        best = alns[0] if alns else None
        identity = 0.0
        aln_len = 0
        qstart = qend = sstart = send = 0
        mismatch = 0
        if best is not None:
            qstart, qend = best.aligned[0][0][0], best.aligned[0][-1][1]
            sstart, send = best.aligned[1][0][0], best.aligned[1][-1][1]
            aln_len = max(qend - qstart, send - sstart, 1)
            matches = sum(
                1 for i in range(min(qend - qstart, send - sstart))
                if query_seq[qstart + i] == ref_seq[sstart + i]
            )
            identity = round(100.0 * matches / aln_len, 2)
            mismatch = aln_len - matches
        bitscore = _estimate_bitscore(float(score))
        evalue = _estimate_evalue(float(score), len(query_seq), len(ref_seq), db_total_len)
        qcov = round(100.0 * (qend - qstart) / len(query_seq), 1) if len(query_seq) else 0
        hits.append({
            "subject_id": rec["id"],
            "identity_percent": identity,
            "alignment_length": aln_len,
            "mismatch": mismatch,
            "gapopen": 0,
            "qstart": qstart + 1,
            "qend": qend,
            "sstart": sstart + 1,
            "send": send,
            "evalue": evalue,
            "bitscore": bitscore,
            "query_coverage_percent": qcov,
            "subject_title": rec["id"],
            "gene_symbol": _gene_symbol_from_title(rec.get("id") or ""),
            "raw_score": round(float(score), 1),
        })

    hits.sort(key=lambda x: (x["evalue"], -x["bitscore"]))
    hits = hits[:top_k]
    return {
        "success": True,
        "engine": "Biopython fallback (k-mer indexed, E-value estimated)",
        "query_length": len(query_seq),
        "database": fasta,
        "hit_count": len(hits),
        "hits": hits,
        "assembly": "GRCz12tu" if _is_grcz12_fasta(fasta) else "GRCz11",
        "note": "回退模式 E-value 为估算值；发表请安装 BLAST+ 后重建索引",
    }


def _parse_fasta(path: str) -> List[Dict]:
    if not os.path.isfile(path):
        return []
    records = []
    header = None
    seq_parts = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith(">"):
                if header:
                    records.append({"id": header, "sequence": "".join(seq_parts).upper()})
                header = line[1:].split()[0]
                seq_parts = []
            else:
                seq_parts.append(line.replace(" ", ""))
        if header:
            records.append({"id": header, "sequence": "".join(seq_parts).upper()})
    return records


def _json_safe(v):
    """把 numpy 标量等转成 JSON 可序列化的 Python 类型。"""
    if v is None or isinstance(v, (str, bool)):
        return v
    item = getattr(v, "item", None)
    if callable(item):
        try:
            v = item()
        except Exception:
            return str(v)
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return float(v)
    return v


def _json_safe_result(result: dict) -> dict:
    hits = []
    seen: set[str] = set()
    for h in result.get("hits") or []:
        if not isinstance(h, dict):
            continue
        row = {k: _json_safe(val) for k, val in h.items()}
        sid = str(row.get("subject_id") or "")
        if sid and sid in seen:
            continue
        if sid:
            seen.add(sid)
        hits.append(row)
    out = dict(result)
    out["hits"] = hits
    out["hit_count"] = len(hits)
    if "query_length" in out:
        out["query_length"] = int(_json_safe(out.get("query_length") or 0))
    return out


def run_local_blast(query: str, top_k: int = 10, evalue: float = 10.0) -> dict:
    """可发表级 blastn：优先 BLAST+，否则 k-mer 回退。"""
    ok, msg = validate_sequence(query)
    if not ok:
        return {"success": False, "error": msg, "hits": []}

    query_seq = msg
    if _blast_bin("blastn"):
        result = _run_blastn(query_seq, top_k=top_k, evalue=evalue)
        if result.get("success"):
            logger.info(f"[BLAST+] {len(query_seq)}bp → {result.get('hit_count')} hits")
            return _json_safe_result(result)
        err = str(result.get("error") or "")
        logger.warning(f"[BLAST+] 失败，不回退到抽样库：{err[:300]}")
        # BLAST+ 已安装时禁止静默掉进 74 基因 k-mer，避免把 tp53 打成无关基因
        if not result.get("need_blast_plus"):
            return _json_safe_result(result)

    result = _run_blast_fallback(query_seq, top_k=top_k)
    logger.info(f"[BLAST fallback] {len(query_seq)}bp → {result.get('hit_count')} hits")
    return _json_safe_result(result)


def format_blast_answer(result: dict) -> str:
    if not result.get("success"):
        err = result.get("error", "未知错误")
        if result.get("need_blast_plus"):
            err += "\n  → 运行 python -m scripts.setup_blast 安装 NCBI BLAST+"
        return f"[错误] 序列比对失败：{err}"
    if not result.get("hits"):
        return "[结果] 未找到显著匹配（E-value 阈值内无 hit）。"

    lines = [
        f"[成功] {result.get('engine', 'BLAST')} | 查询 {result['query_length']} bp | "
        f"{result.get('assembly') or ''} | 库 {os.path.basename(result.get('database', ''))}",
        "Columns: Subject | Identity | AlnLen | E-value | BitScore | Qcov",
    ]
    for i, h in enumerate(result["hits"], 1):
        e_str = f"{h['evalue']:.2e}" if h["evalue"] < 0.01 else f"{h['evalue']:.3g}"
        gene = (h.get("gene_symbol") or "").strip()
        subj = f"{gene} ({h['subject_id']})" if gene else h["subject_id"]
        lines.append(
            f"{i}. {subj} | {h['identity_percent']}% | {h['alignment_length']}bp | "
            f"E={e_str} | Bit={h['bitscore']} | Qcov={h.get('query_coverage_percent', '-')}%\n"
            f"   q[{h.get('qstart','?')}-{h.get('qend','?')}] s[{h.get('sstart','?')}-{h.get('send','?')}]"
        )
    if result.get("note"):
        lines.append(f"[说明] {result['note']}")
    return "\n".join(lines)


def blast_tsv_export(result: dict) -> str:
    """导出为 TSV（可直接用于补充材料）。"""
    header = "rank\tsubject_id\tpident\tlength\tmismatch\tgapopen\tqstart\tqend\tsstart\tsend\tevalue\tbitscore\tqcovs\ttitle"
    if not result.get("hits"):
        return header
    rows = [header]
    for i, h in enumerate(result["hits"], 1):
        rows.append(
            f"{i}\t{h['subject_id']}\t{h['identity_percent']}\t{h['alignment_length']}\t"
            f"{h.get('mismatch', '')}\t{h.get('gapopen', '')}\t{h.get('qstart', '')}\t{h.get('qend', '')}\t"
            f"{h.get('sstart', '')}\t{h.get('send', '')}\t{h['evalue']}\t{h['bitscore']}\t"
            f"{h.get('query_coverage_percent', '')}\t{h.get('subject_title', h['subject_id'])}"
        )
    return "\n".join(rows)
