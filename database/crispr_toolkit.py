# -*- coding: utf-8 -*-
"""sgRNA / 引物设计 · 本地引擎（对齐 CHOPCHOP 与 CRISPRScan 的公开做法）

设计原则（重要）：
  · 不复制任何外部工具的源码或权重表；所有打分项都在本文件里**透明可读、逐项返回**，
    便于复核、写进方法学，也便于按需调参。
  · 覆盖 CHOPCHOP 与 CRISPRScan 的核心能力：
        - 双链 PAM 枚举（SpCas9 NGG/NAG、Cas12a TTTV、SaCas9 NNGRRT）
        - GC / 多 T（U6 终止子）/ 同聚物 / 发夹倾向 等规则项
        - ORF 内位置偏好（早期外显子更利于移码敲除）
        - T7 / SP6 体外转录模板（CRISPRScan 常用场景）
        - 转录组（GRCz12tu CDS）特异性筛查：种子区近同源命中计数
  · 特异性筛查用本地 BLAST+（与 /api/tools/blast 同一参考库）；BLAST 不可用时
    明确返回 `offtarget_skipped`，不伪造脱靶数字。

术语：spacer = 20 nt 引导序列；PAM = protospacer adjacent motif。
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

import bootstrap  # noqa: F401

from common_utils import logger

# ---------------------------------------------------------------------------
# PAM 定义（side 表示 PAM 相对 spacer 的位置）
# ---------------------------------------------------------------------------

PAM_TABLE: Dict[str, dict] = {
    "NGG": {"motif": "NGG", "side": "3", "spacer": 20, "enzyme": "SpCas9",
            "note": "标准 SpCas9 PAM，敲除首选"},
    "NAG": {"motif": "NAG", "side": "3", "spacer": 20, "enzyme": "SpCas9",
            "note": "非经典 PAM，效率较低，作为备选"},
    "TTTV": {"motif": "TTTV", "side": "5", "spacer": 23, "enzyme": "Cas12a(Cpf1)",
             "note": "5' 端 PAM，适合 T7 体外转录与多靶点"},
    "NNGRRT": {"motif": "NNGRRT", "side": "3", "spacer": 21, "enzyme": "SaCas9",
               "note": "小尺寸 Cas9，便于 AAV 递送"},
}

_IUPAC = {
    "A": "A", "C": "C", "G": "G", "T": "T",
    "R": "[AG]", "Y": "[CT]", "S": "[GC]", "W": "[AT]",
    "K": "[GT]", "M": "[AC]", "B": "[CGT]", "D": "[AGT]",
    "H": "[ACT]", "V": "[ACG]", "N": "[ACGT]",
}

_COMP = str.maketrans("ACGTNacgtn", "TGCANtgcan")


def revcomp(seq: str) -> str:
    return (seq or "").translate(_COMP)[::-1].upper()


def gc_percent(seq: str) -> float:
    s = (seq or "").upper()
    if not s:
        return 0.0
    gc = sum(1 for c in s if c in "GC")
    return round(100.0 * gc / len(s), 1)


def normalize_dna(seq: str) -> str:
    """去空白/数字/FASTA 头，只保留 ACGTN。"""
    s = re.sub(r"^>.*$", "", seq or "", flags=re.M)
    return re.sub(r"[^ACGTNacgtn]", "", s).upper()


# ---------------------------------------------------------------------------
# PAM 枚举
# ---------------------------------------------------------------------------

def _pam_regex(motif: str) -> str:
    return "".join(_IUPAC.get(c, c) for c in motif.upper())


def enumerate_candidates(seq: str, enzyme: str = "NGG") -> List[dict]:
    """双链 PAM 枚举，返回候选 spacer 列表（含链、坐标、PAM）。"""
    conf = PAM_TABLE.get((enzyme or "NGG").upper())
    if not conf:
        return []
    spacer_len = int(conf["spacer"])
    pam_re = re.compile(_pam_regex(conf["motif"]), re.I)
    side = conf["side"]
    out: List[dict] = []

    def scan(sequence: str, strand: str) -> None:
        n = len(sequence)
        for m in pam_re.finditer(sequence):
            if side == "3":
                start = m.start() - spacer_len
                if start < 0:
                    continue
                spacer = sequence[start:m.start()]
                pam = m.group(0)
            else:
                start = m.end()
                if start + spacer_len > n:
                    continue
                spacer = sequence[start:start + spacer_len]
                pam = m.group(0)
            if len(spacer) != spacer_len or "N" in spacer.upper():
                continue
            if strand == "+":
                fwd_start, fwd_end = start + 1, start + spacer_len
            else:
                # 负链坐标换算回正链（1-based）
                fwd_start = n - (start + spacer_len) + 1
                fwd_end = n - start
            out.append({
                "spacer": spacer.upper(),
                "pam": pam.upper(),
                "strand": strand,
                "start": fwd_start,
                "end": fwd_end,
                "gc": gc_percent(spacer),
                "enzyme": conf["enzyme"],
                "pam_type": conf["motif"],
            })

    scan(seq, "+")
    rc = revcomp(seq)
    scan(rc, "-")
    # 同一位点（正负链）去重，保留先出现的
    seen = set()
    uniq = []
    for c in out:
        key = (c["spacer"], c["strand"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    return uniq


# ---------------------------------------------------------------------------
# 打分（逐项返回，便于复核）
# ---------------------------------------------------------------------------

def _flag_homopolymer(spacer: str, run: int = 5) -> bool:
    return re.search(r"(.)\1{%d,}" % (run - 1), spacer) is not None


def _flag_hairpin(spacer: str, min_stem: int = 4) -> bool:
    """极简发夹倾向：spacer 内出现 ≥4 nt 的反向互补片段。"""
    s = spacer.upper()
    for i in range(len(s) - min_stem + 1):
        stem = s[i:i + min_stem]
        if revcomp(stem) in s and revcomp(stem) != stem:
            return True
    return False


def score_candidate(cand: dict, *, cds_len: int, add_t7: bool = False) -> dict:
    """规则打分：返回各分项与总分（0~100），所有项都写清楚。"""
    spacer = cand["spacer"]
    gc = float(cand["gc"])
    parts: Dict[str, float] = {}

    # 1) GC 含量：40%~60% 满分，越偏离越低（权重 0.30）
    if 40.0 <= gc <= 60.0:
        parts["gc"] = 1.0
    elif gc < 40.0:
        parts["gc"] = max(0.0, (gc - 25.0) / 15.0)
    else:
        parts["gc"] = max(0.0, (75.0 - gc) / 15.0)

    # 2) U6 终止子：连续 TTTT 会提前终止转录（权重 0.18）
    parts["polyT"] = 0.15 if re.search(r"TTTT", spacer) else 1.0

    # 3) 同聚物与极端碱基组成（权重 0.10）
    parts["homopolymer"] = 0.2 if _flag_homopolymer(spacer, 5) else 1.0

    # 4) 发夹 / 二级结构倾向（权重 0.10）
    parts["hairpin"] = 0.35 if _flag_hairpin(spacer, 4) else 1.0

    # 5) 位置偏好：敲除希望落在 CDS 前 1/3 且尽量靠 5'（权重 0.22）
    if cds_len and cds_len > 0:
        frac = max(0.0, min(1.0, (cand["start"] - 1) / float(cds_len)))
        if frac <= 0.10:
            pos = 1.0
        elif frac <= 0.33:
            pos = 0.9
        elif frac <= 0.66:
            pos = 0.7
        else:
            pos = 0.5
    else:
        frac, pos = 0.0, 0.75
    parts["position"] = pos

    # 6) 种子区 GC（PAM 近端 8 nt，过高易脱靶、过低活性差）（权重 0.10）
    seed = spacer[-8:]
    seed_gc = gc_percent(seed)
    parts["seed_gc"] = 1.0 if 30.0 <= seed_gc <= 70.0 else 0.6

    weights = {"gc": 0.30, "polyT": 0.18, "homopolymer": 0.10,
               "hairpin": 0.10, "position": 0.22, "seed_gc": 0.10}
    efficiency = sum(parts[k] * w for k, w in weights.items()) * 100.0

    # 7) PAM 类型微调：非经典 PAM 打折
    pam_factor = {"NGG": 1.0, "NAG": 0.9, "TTTV": 0.95, "NNGRRT": 0.9}.get(
        cand.get("pam_type", "NGG"), 1.0)

    out = dict(cand)
    out.update({
        "gc_percent": gc,
        "position_frac": round(frac, 3),
        "score_parts": {k: round(v, 3) for k, v in parts.items()},
        "efficiency_score": round(efficiency * pam_factor, 1),
    })

    # 8) T7 / SP6 体外转录模板（CRISPRScan 场景）
    if add_t7:
        lead = "" if spacer.upper().startswith("G") else "G"
        out["t7_template"] = "TAATACGACTCACTATAG" + lead + spacer
        out["t7_note"] = "T7 启动子 + 引导序列（缺 G 时自动补 1 个 G）"
        out["sp6_template"] = "ATTTAGGTGACACTATAG" + lead + spacer
    return out


# ---------------------------------------------------------------------------
# 特异性（脱靶）筛查：本地 BLAST+ 一次批量查询
# ---------------------------------------------------------------------------

def _batch_blastn(queries: Dict[str, str], *, max_targets: int = 60,
                  evalue: float = 1000.0, db: Optional[str] = None,
                  timeout: int = 420) -> Dict[str, List[dict]]:
    """把多条短查询一次提交 blastn（blastn-short），按 qseqid 归组。

    db: 不传 = 转录组库（cdna_db）；传 "genome" = 全基因组库（genome_db）。
    """
    from seq_toolkit import BLAST_DB, BLAST_OUTFMT, _blast_bin, ensure_blast_database

    blastn = _blast_bin("blastn")
    if not blastn:
        raise RuntimeError("未找到 blastn")
    if db == "genome":
        info = genome_db_status()
        if not info.get("ready"):
            raise RuntimeError(info.get("error") or "全基因组 BLAST 库未就绪")
        target_db = info["path"]
        max_targets = max(int(max_targets), 60)
    else:
        info = ensure_blast_database()
        if not info.get("success"):
            raise RuntimeError(info.get("error") or "BLAST 库不可用")
        target_db = BLAST_DB

    with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=False, encoding="utf-8") as qf:
        for key, seq in queries.items():
            qf.write(">%s\n%s\n" % (key.replace(" ", "_"), seq))
        qpath = qf.name
    opath = qpath + ".tsv"
    try:
        cmd = [
            blastn, "-query", qpath, "-db", target_db, "-out", opath,
            "-outfmt", BLAST_OUTFMT, "-evalue", str(evalue),
            "-max_target_seqs", str(max(int(max_targets), 20)),
            "-num_threads", os.getenv("BLAST_THREADS", "2"),
            "-task", "blastn-short",
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
        grouped: Dict[str, List[dict]] = {}
        from seq_toolkit import _gene_symbol_from_title

        with open(opath, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 14:
                    continue
                title = cols[13]
                grouped.setdefault(cols[0], []).append({
                    "subject_id": cols[1],
                    "identity": round(float(cols[2]), 1),
                    "alignment_length": int(float(cols[3])),
                    "mismatch": int(float(cols[4])),
                    "qstart": int(float(cols[6])),
                    "qend": int(float(cols[7])),
                    "sstart": int(float(cols[8])),
                    "send": int(float(cols[9])),
                    "evalue": float(cols[10]),
                    "bitscore": round(float(cols[11]), 1),
                    "title": title,
                    "gene": _gene_symbol_from_title(title),
                })
        return grouped
    finally:
        for p in (qpath, opath):
            try:
                os.remove(p)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 参考库：转录组（CDS，快）与全基因组（慢、更全）
# ---------------------------------------------------------------------------

def _genome_fasta_paths() -> List[str]:
    """候选基因组 FASTA 路径（环境变量优先，其次运行时 blast 目录）。"""
    out: List[str] = []
    env = (os.getenv("BLAST_GENOME_FASTA") or "").strip()
    if env:
        out.append(env)
    try:
        from seq_toolkit import BLAST_DIR

        out.extend([
            os.path.join(BLAST_DIR, "grcz12tu_genome.fna"),
            os.path.join(BLAST_DIR, "grcz12tu_genome.fa"),
            os.path.join(BLAST_DIR, "genome.fna"),
        ])
    except Exception:
        pass
    return out


def genome_db_status() -> dict:
    """全基因组 BLAST 库是否可用（未建库时返回原因，不抛错）。"""
    from seq_toolkit import BLAST_DIR

    db = os.path.join(BLAST_DIR, "genome_db")
    for suffix in (".nin", ".nsq"):
        if not os.path.isfile(db + suffix):
            fa = next((p for p in _genome_fasta_paths() if os.path.isfile(p)), "")
            return {
                "ready": False,
                "path": db,
                "fasta": fa,
                "error": ("全基因组 BLAST 库未就绪（缺 %s.nin）："
                          "请把 GRCz12tu 基因组 FASTA 放到 %s 并跑 makeblastdb" % (db, BLAST_DIR)),
            }
    fa = next((p for p in _genome_fasta_paths() if os.path.isfile(p)), "")
    return {"ready": True, "path": db, "fasta": fa,
            "size_mb": round(sum(os.path.getsize(db + s) for s in (".nin", ".nsq", ".nhr")
                                  if os.path.isfile(db + s)) / 1048576.0, 1)}


def offtarget_scan(candidates: List[dict], *, max_mismatch: int = 3,
                   top_queries: Optional[int] = None,
                   scope: str = "transcriptome") -> Tuple[Dict[str, dict], str]:
    """对候选做特异性筛查；返回 (结果表, 说明/跳过原因)。

    scope="transcriptome"：GRCz12tu CDS 转录组，快（默认）；
    scope="genome"：GRCz12tu 全基因组，更全但慢——只扫前 12 个候选（界面可看到说明）。
    """
    use_genome = (scope or "").lower().startswith("genome")
    if use_genome:
        picks = candidates[:min(len(candidates), 12)]
    else:
        picks = candidates if not top_queries else candidates[:max(1, int(top_queries))]
    queries = {}
    for i, c in enumerate(picks):
        seq = (c["spacer"] + c["pam"]) if c.get("pam") else c["spacer"]
        queries["cand%d" % i] = seq
    if not queries:
        return {}, "没有候选可筛查"

    try:
        grouped = _batch_blastn(queries, db="genome" if use_genome else None,
                                timeout=900 if use_genome else 420)
    except Exception as e:                      # noqa: BLE001
        logger.warning("[CRISPR] 脱靶筛查跳过：%s", e)
        return {}, "offtarget_skipped: %s" % str(e)[:200]

    scope_label = "GRCz12tu 全基因组" if use_genome else "GRCz12tu 转录组(CDS)"
    note = "已用本地 BLAST+ 扫描 %s（≤%d 个错配且近全长视为近同源）%s" % (
        scope_label, max_mismatch, "；全基因组模式只扫前 12 个候选" if use_genome else "")

    out: Dict[str, dict] = {}
    for i, c in enumerate(picks):
        key = "cand%d" % i
        hits = grouped.get(key) or []
        full = len(c["spacer"]) - 2          # 只算近全长比对，避免 12 nt 子串误计成脱靶
        self_hit = None
        near: List[dict] = []
        for h in hits:
            if h["mismatch"] == 0 and h["alignment_length"] >= len(c["spacer"]) - 1:
                self_hit = self_hit or h
                continue
            if h["mismatch"] <= max_mismatch and h["alignment_length"] >= full:
                near.append(h)

        # 特异性分数：无近同源 = 1.0，命中越多越低
        spec = 1.0 / (1.0 + 0.6 * len(near))
        genes: Dict[str, int] = {}
        loci: List[dict] = []
        for h in near:
            if use_genome:
                loci.append({
                    "chrom": str(h.get("subject_id") or "")[:40],
                    "start": h.get("sstart"), "end": h.get("send"),
                    "mismatch": h["mismatch"],
                })
            else:
                g = (h.get("gene") or "").strip()
                if g:
                    genes[g] = genes.get(g, 0) + 1
        out[c["spacer"]] = {
            "near_mismatch_hits": len(near),
            "scope": "genome" if use_genome else "transcriptome",
            "off_target_genes": [{"gene": g, "hits": n} for g, n in
                                 sorted(genes.items(), key=lambda kv: -kv[1])[:8]],
            "off_target_loci": sorted(loci, key=lambda x: (x["mismatch"], str(x["chrom"])))[:8],
            "top_offtargets": [
                {"title": (h.get("title") or "")[:90], "mismatch": h["mismatch"],
                 "identity": h["identity"], "gene": h.get("gene") or ""}
                for h in sorted(near, key=lambda x: (x["mismatch"], -x["bitscore"]))[:5]
            ],
            "specificity_score": round(spec, 3),
        }
    return out, note


# ---------------------------------------------------------------------------
# sgRNA 主流程
# ---------------------------------------------------------------------------

def resolve_template(target: str) -> dict:
    """输入可以是基因名，也可以是 DNA 序列；统一返回模板序列 + 来源说明。"""
    raw = (target or "").strip()
    if not raw:
        return {"success": False, "error": "请输入基因名或 DNA 序列"}
    seq = normalize_dna(raw)
    if len(seq) >= 30 and len(seq) >= 0.8 * len(re.sub(r"\s", "", raw)):
        return {"success": True, "sequence": seq, "source": "pasted_sequence",
                "label": "粘贴序列", "length": len(seq), "gene": ""}
    # 当作基因名
    from sequence_toolkit import fetch_cdna_sequence

    block = fetch_cdna_sequence(raw) or {}
    if block.get("success") and block.get("sequence"):
        asm = block.get("assembly") or ""
        return {
            "success": True,
            "sequence": normalize_dna(block["sequence"]),
            "source": block.get("source") or "local",
            "label": "cDNA" + (" · " + asm if asm else ""),
            "assembly": asm,
            "gene": block.get("gene") or raw,
            "ensembl_id": block.get("ensembl_id") or "",
            "length": len(normalize_dna(block["sequence"])),
            "cdna_length": block.get("length") or 0,
        }
    return {"success": False, "error": block.get("error") or "无法解析为基因或序列"}


def design_sgrna(target: str, *, enzyme: str = "NGG", top_n: int = 20,
                 max_mismatch: int = 3, off_target: bool = True,
                 add_t7: bool = False, offtarget_scope: str = "transcriptome") -> dict:
    """sgRNA 设计主流程：解析模板 → PAM 枚举 → 打分 → 特异性筛查 → 排序。"""
    tpl = resolve_template(target)
    if not tpl.get("success"):
        return {"success": False, "error": tpl.get("error")}
    seq = tpl["sequence"]
    conf = PAM_TABLE.get((enzyme or "NGG").upper()) or PAM_TABLE["NGG"]

    cands = enumerate_candidates(seq, enzyme)
    if not cands:
        return {"success": False, "error": "在该序列中未找到 %s PAM 候选" % conf["motif"],
                "template": tpl}

    cds_len = len(seq)
    scored = [score_candidate(c, cds_len=cds_len, add_t7=add_t7) for c in cands]
    # 先按效率排序，取前若干做特异性筛查
    scored.sort(key=lambda x: -x["efficiency_score"])
    scan_pool = scored[:max(int(top_n) * 2, 30)]
    offtarget_note = "未开启脱靶筛查"
    if off_target:
        table, offtarget_note = offtarget_scan(scan_pool, max_mismatch=max_mismatch,
                                               scope=offtarget_scope)
        for c in scan_pool:
            info = table.get(c["spacer"]) or {}
            c["offtarget"] = info
            if info:
                spec = float(info.get("specificity_score", 1.0))
                c["specificity_score"] = spec
                c["final_score"] = round(0.72 * c["efficiency_score"] + 28.0 * spec, 1)
            else:
                # 未扫到（如全基因组模式只扫前 12 个）：不给默认满分，标为未测
                c["specificity_score"] = None
                c["final_score"] = round(0.72 * c["efficiency_score"] + 14.0, 1)
    for c in scan_pool:
        c.setdefault("specificity_score", None)
        c.setdefault("final_score", c["efficiency_score"])

    scored.sort(key=lambda x: (-(x.get("final_score") or 0.0), -x["efficiency_score"]))
    kept = scored[:max(1, min(int(top_n), 60))]
    gdb = genome_db_status()
    return {
        "success": True,
        "kind": "sgrna",
        "enzyme": conf["enzyme"],
        "pam_type": conf["motif"],
        "pam_note": conf["note"],
        "template": tpl,
        "candidate_total": len(scored),
        "candidates": kept,
        "offtarget_note": offtarget_note,
        "offtarget_scope": "genome" if (offtarget_scope or "").lower().startswith("genome") else "transcriptome",
        "genome_db": {"ready": bool(gdb.get("ready")), "size_mb": gdb.get("size_mb")},
    }


# ---------------------------------------------------------------------------
# 引物设计
# ---------------------------------------------------------------------------

# SantaLucia 1998 统一最近邻参数（ΔH kcal/mol, ΔS cal/mol·K）
_NN = {
    "AA": (-7.9, -22.2), "TT": (-7.9, -22.2),
    "AT": (-7.2, -20.4), "TA": (-7.2, -21.3),
    "CA": (-8.5, -22.7), "TG": (-8.5, -22.7),
    "GT": (-8.4, -22.4), "AC": (-8.4, -22.4),
    "CT": (-7.8, -21.0), "AG": (-7.8, -21.0),
    "GA": (-8.2, -22.2), "TC": (-8.2, -22.2),
    "CG": (-10.6, -27.2),
    "GC": (-9.8, -24.4),
    "GG": (-8.0, -19.9), "CC": (-8.0, -19.9),
}
_INIT_H, _INIT_S = 0.2, -5.7      # 起始项
_TERM_AT_H, _TERM_AT_S = 2.2, 6.9  # 末端 A/T 不对称惩罚
_NA_CONC = 0.05                   # 假定 50 mM Na+（常用 PCR 缓冲）
_CT = 0.5e-6                      # 引物浓度 0.5 µM


def tm_nearest_neighbor(seq: str) -> float:
    """Tm 估算：SantaLucia 1998 最近邻热力学 + 盐校正（50 mM Na+，0.5 µM 引物）。

    比旧的 GC 校正公式准得多（短引物尤其明显）；仍未做二价离子/掺杂校正。
    """
    import math

    s = (seq or "").upper()
    if len(s) < 2:
        return 0.0

    dh, ds = _INIT_H, _INIT_S
    for i in range(len(s) - 1):
        pair = s[i:i + 2]
        h, sv = _NN.get(pair, (0.0, 0.0))
        dh += h
        ds += sv
    # 末端 A/T 惩罚（5' 与 3' 端）
    for base in (s[0], s[-1]):
        if base in "AT":
            dh += _TERM_AT_H
            ds += _TERM_AT_S
    # 盐校正：ΔS += 0.368 × (N-1) × ln[Na+]
    ds += 0.368 * (len(s) - 1) * math.log(_NA_CONC)

    tm = (1000.0 * dh) / (ds + 1.987 * math.log(_CT / 4.0)) - 273.15
    return round(tm, 1)


def tm_estimate(seq: str) -> float:
    """对外接口：最近邻 Tm（失败时回退 GC 校正公式）。"""
    try:
        return tm_nearest_neighbor(seq)
    except Exception:  # noqa: BLE001
        s = (seq or "").upper()
        n = len(s)
        if not n:
            return 0.0
        gc = sum(1 for c in s if c in "GC")
        if n >= 14:
            return round(64.9 + 41.0 * (gc - 16.4) / n, 1)
        return round(2.0 * (n - gc) + 4.0 * gc, 1)


def _primer_ok(p: str) -> bool:
    if not (18 <= len(p) <= 25):
        return False
    gc = gc_percent(p)
    if not (40.0 <= gc <= 70.0):
        return False
    if re.search(r"(.)\1{4,}", p):          # ≥5 连续同碱基
        return False
    if p.endswith("AAA") or p.endswith("TTT") or p.endswith("CCC") or p.endswith("GGG"):
        return False
    tm = tm_estimate(p)
    if not (50.0 <= tm <= 68.0):             # 常用 PCR 可用的 Tm 窗口
        return False
    return True


def _best_primer_at(seq: str, start: int, *, forward: bool) -> Optional[dict]:
    """在给定起点挑一个最优长度（Tm 最接近 60 ℃），挑不到返回 None。"""
    best = None
    for L in range(18, 26):
        if forward:
            piece = seq[start:start + L]
        else:
            piece = seq[start - L:start]
            if len(piece) != L:
                continue
            piece_rev = revcomp(piece)
            cand = {"seq": piece_rev, "start": start - L + 1, "end": start,
                    "tm": tm_estimate(piece_rev), "gc": gc_percent(piece_rev)}
            if not _primer_ok(piece_rev):
                continue
            gap = abs(cand["tm"] - 60.0)
            if best is None or gap < best[0]:
                best = (gap, cand)
            continue
        if len(piece) != L:
            continue
        cand = {"seq": piece, "start": start + 1, "end": start + L,
                "tm": tm_estimate(piece), "gc": gc_percent(piece)}
        if not _primer_ok(piece):
            continue
        gap = abs(cand["tm"] - 60.0)
        if best is None or gap < best[0]:
            best = (gap, cand)
    return best[1] if best else None


def _dimer_risk(fwd: str, rev: str, tail: int = 5) -> bool:
    """3' 端互补（尾 5 nt 内出现 ≥4 nt 互补）即认为有二聚体风险。"""
    a = fwd[-tail:].upper()
    b = revcomp(rev[-tail:])
    return any(a[i:i + 4] in b for i in range(len(a) - 3)) if len(a) >= 4 else False


def design_primers(template: str, *, amplicon_min: int = 80, amplicon_max: int = 300,
                   max_pairs: int = 8, add_t7: bool = False,
                   off_target: bool = False, offtarget_scope: str = "transcriptome") -> dict:
    """规则式引物设计：长度/GC/Tm/同聚物/3' 端/二聚体 + 产物长度约束。"""
    tpl = resolve_template(template)
    if not tpl.get("success"):
        return {"success": False, "error": tpl.get("error")}
    seq = tpl["sequence"]
    n = len(seq)
    if n < max(120, amplicon_min):
        return {"success": False, "error": "模板过短（%d bp），无法设计 %d bp 以上产物" % (n, amplicon_min),
                "template": tpl}

    fwd: List[dict] = []
    for i in range(0, min(n - 18, 900)):                 # 只在模板 5' 区取前向引物
        cand = _best_primer_at(seq, i, forward=True)
        if cand:
            fwd.append(cand)
    rev: List[dict] = []
    for j in range(n, max(19, n - 900), -1):             # 3' 端逆序取反向引物
        cand = _best_primer_at(seq, j, forward=False)
        if cand:
            rev.append(cand)

    pairs: List[dict] = []
    for f in fwd:
        for r in rev:
            amp = r["end"] - f["start"] + 1
            if amp < amplicon_min or amp > amplicon_max:
                continue
            dtm = abs(f["tm"] - r["tm"])
            if dtm > 3.0:
                continue
            penalty = 0.0
            if _dimer_risk(f["seq"], r["seq"]):
                penalty += 6.0
            if _flag_hairpin(f["seq"]) or _flag_hairpin(r["seq"]):
                penalty += 4.0
            # 产物越接近 150~250 bp 越稳，越靠模板 5' 越省事
            target_amp = (amplicon_min + amplicon_max) / 2.0
            penalty += min(6.0, abs(amp - target_amp) / 60.0)
            # Tm 偏离 60 ℃ 扣分（45~70 ℃ 区间内线性）
            penalty += min(6.0, (abs(f["tm"] - 60.0) + abs(r["tm"] - 60.0)) / 5.0)
            score = 100.0 - dtm * 6.0 - penalty
            pairs.append({
                "forward": f["seq"], "reverse": r["seq"],
                "forward_pos": "%d-%d" % (f["start"], f["end"]),
                "reverse_pos": "%d-%d" % (r["start"], r["end"]),
                "forward_tm": f["tm"], "reverse_tm": r["tm"], "tm_diff": round(dtm, 1),
                "forward_gc": f["gc"], "reverse_gc": r["gc"],
                "product_size": amp,
                "score": round(score, 1),
                "hairpin_risk": bool(_flag_hairpin(f["seq"]) or _flag_hairpin(r["seq"])),
                "dimer_risk": bool(_dimer_risk(f["seq"], r["seq"])),
            })
    if not pairs:
        return {"success": False, "error": "未找到满足约束的引物对（可放宽产物长度或 TM 差）",
                "template": tpl, "forward_candidates": len(fwd), "reverse_candidates": len(rev)}

    pairs.sort(key=lambda p: -p["score"])
    # 去重：同一产物长度区间只留最高分，避免一堆相似引物
    picked: List[dict] = []
    seen = set()
    for p in pairs:
        key = (p["product_size"] // 10, p["forward"][:12], p["reverse"][:12])
        if key in seen:
            continue
        seen.add(key)
        picked.append(p)
        if len(picked) >= max(1, min(int(max_pairs), 20)):
            break

    note = ""
    if add_t7:
        for p in picked:
            p["forward_tailed"] = "TAATACGACTCACTATAG" + p["forward"]
            p["reverse_tailed"] = p["reverse"]
        note = "已附 T7 启动子尾（TAATACGACTCACTATAG…）用于体外转录模板扩增"
    if off_target and picked:
        use_genome = (offtarget_scope or "").lower().startswith("genome")
        qs = {}
        for i, p in enumerate(picked[:8]):
            qs["f%d" % i] = p["forward"]
            qs["r%d" % i] = p["reverse"]
        try:
            grouped = _batch_blastn(qs, max_targets=30, evalue=100.0,
                                    db="genome" if use_genome else None,
                                    timeout=900 if use_genome else 420)
            for i, p in enumerate(picked[:8]):
                hits = [h for h in (grouped.get("f%d" % i) or []) + (grouped.get("r%d" % i) or [])
                        if h["mismatch"] <= 2 and h["alignment_length"] >= len(p["forward"]) - 2]
                others: Dict[str, int] = {}
                loci: List[dict] = []
                for h in hits:
                    if use_genome:
                        loci.append({"chrom": str(h.get("subject_id") or "")[:40],
                                     "start": h.get("sstart"), "end": h.get("send"),
                                     "mismatch": h["mismatch"]})
                    else:
                        g = (h.get("gene") or "").strip()
                        if g:
                            others[g] = others.get(g, 0) + 1
                p["specific_hits"] = len(hits)
                p["off_target_genes"] = [{"gene": g, "hits": n} for g, n in
                                         sorted(others.items(), key=lambda kv: -kv[1])[:6]]
                p["off_target_loci"] = sorted(loci, key=lambda x: (x["mismatch"], str(x["chrom"])))[:6]
                p["specific_note"] = "%s内近全长 ≤2 错配命中 %d 处%s" % (
                    "全基因组" if use_genome else "转录组",
                    len(hits),
                    ("（" + "、".join(list(others)[:4]) + "）") if others else "",
                )
        except Exception as e:                            # noqa: BLE001
            note = (note + " · " if note else "") + "引物特异性筛查跳过：%s" % str(e)[:120]

    return {
        "success": True,
        "kind": "primer",
        "template": tpl,
        "pairs": picked,
        "pair_total": len(pairs),
        "note": note,
    }
