# -*- coding: utf-8 -*-
"""从本地 GRCz12tu 基因组 FASTA 按区间取序列（faidx 风格）。"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Optional

MAX_SPAN = 200_000  # bp；单次请求上限，避免拖垮服务

_COMP = str.maketrans("ACGTacgtNn", "TGCAtgcaNn")


def _runtime_blast_dir() -> str:
    env = (os.getenv("BLAST_DIR") or "").strip()
    if env:
        return env
    return "/data/zoujin/deng/zebrafish_runtime/blast"


def _genome_fasta() -> str:
    env = (os.getenv("GENOME_FASTA") or "").strip()
    if env and os.path.isfile(env):
        return env
    path = os.path.join(_runtime_blast_dir(), "grcz12tu_genome.fna")
    if os.path.isfile(path):
        return path
    raise FileNotFoundError("未找到 GRCz12tu 基因组 FASTA（GENOME_FASTA / grcz12tu_genome.fna）")


def _genome_info_path() -> str:
    return os.path.join(os.path.dirname(_runtime_blast_dir()), "genome_info.json")


@lru_cache(maxsize=1)
def _chrom_to_acc() -> dict[str, str]:
    path = _genome_info_path()
    out: dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for row in data.get("chromosomes") or []:
        chr_no = str(row.get("chr") or "").strip()
        name = str(row.get("name") or "").strip()
        if chr_no and name:
            out[chr_no] = name
            out[name] = name
            out[name.split(".", 1)[0]] = name
    return out


def resolve_accession(chrom: str) -> str:
    raw = (chrom or "").strip()
    if not raw:
        raise ValueError("缺少染色体")
    raw = re.sub(r"^chr", "", raw, flags=re.I)
    mapping = _chrom_to_acc()
    if raw in mapping:
        return mapping[raw]
    if raw.upper().startswith("NC_"):
        return mapping.get(raw, mapping.get(raw.split(".", 1)[0], raw))
    raise ValueError(f"无法解析染色体标识：{chrom}")


def _fai_path(fasta: str) -> str:
    return fasta + ".fai"


def ensure_fai(fasta: str) -> str:
    """若不存在 .fai 则扫描一次写出；格式与 samtools faidx 兼容。"""
    fai = _fai_path(fasta)
    if os.path.isfile(fai) and os.path.getmtime(fai) >= os.path.getmtime(fasta):
        return fai
    rows: list[str] = []
    name = ""
    length = 0
    offset = 0
    line_bases = 0
    line_width = 0
    with open(fasta, "rb") as fh:
        while True:
            pos = fh.tell()
            line = fh.readline()
            if not line:
                break
            if line.startswith(b">"):
                if name:
                    rows.append(f"{name}\t{length}\t{offset}\t{line_bases}\t{line_width}\n")
                name = line[1:].split()[0].decode("ascii", "replace")
                length = 0
                offset = fh.tell()
                line_bases = 0
                line_width = 0
                continue
            raw = line.rstrip(b"\r\n")
            if not length:
                line_bases = len(raw)
                line_width = len(line)  # include newline bytes as written
            length += len(raw)
        if name:
            rows.append(f"{name}\t{length}\t{offset}\t{line_bases}\t{line_width}\n")
    tmp = fai + ".tmp"
    with open(tmp, "w", encoding="ascii", newline="\n") as out:
        out.writelines(rows)
    os.replace(tmp, fai)
    return fai


@lru_cache(maxsize=1)
def _load_fai(fasta: str) -> dict[str, tuple[int, int, int, int]]:
    fai = ensure_fai(fasta)
    index: dict[str, tuple[int, int, int, int]] = {}
    with open(fai, encoding="ascii") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            name = parts[0]
            index[name] = (int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]))
    return index


def _fetch_plus(fasta: str, acc: str, start: int, end: int) -> str:
    """1-based inclusive coordinates on + strand."""
    index = _load_fai(fasta)
    if acc not in index:
        raise KeyError(f"FASTA 中无此序列：{acc}")
    length, offset, line_bases, line_width = index[acc]
    if start < 1 or end < start or end > length:
        raise ValueError(f"区间越界：{acc}:{start}-{end}（长度 {length}）")
    # convert to 0-based
    a = start - 1
    b = end  # exclusive
    seq_chunks: list[str] = []
    with open(fasta, "rb") as fh:
        pos = a
        while pos < b:
            line_idx = pos // line_bases
            col = pos % line_bases
            file_pos = offset + line_idx * line_width + col
            take = min(line_bases - col, b - pos)
            fh.seek(file_pos)
            chunk = fh.read(take)
            seq_chunks.append(chunk.decode("ascii", "replace"))
            pos += take
    return "".join(seq_chunks).upper()


def extract_region(
    chrom: str,
    start: int,
    end: int,
    strand: str = "+",
    assembly: str = "GRCz12tu",
) -> dict:
    asm = (assembly or "GRCz12tu").strip() or "GRCz12tu"
    if asm not in {"GRCz12tu", "GCF_049306965.1", "GCF_049306965.2"}:
        return {
            "ok": False,
            "error": {
                "code": "unsupported_assembly",
                "message": f"按区间取序列目前只支持标准版 GRCz12tu，收到的是 {asm}。",
            },
            "assembly": asm,
        }
    try:
        s = int(start)
        e = int(end)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error": {"code": "bad_coords", "message": "起点／终点必须是整数。"},
        }
    if e < s:
        s, e = e, s
    span = e - s + 1
    if span > MAX_SPAN:
        return {
            "ok": False,
            "error": {
                "code": "span_too_large",
                "message": f"单次最多提取 {MAX_SPAN} bp，当前 {span} bp。",
            },
            "requested_span": span,
        }
    chrom_disp = re.sub(r"^chr", "", (chrom or "").strip(), flags=re.I)
    try:
        fasta = _genome_fasta()
        acc = resolve_accession(chrom_disp)
        seq = _fetch_plus(fasta, acc, s, e)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": {"code": "extract_failed", "message": str(exc)},
            "assembly": "GRCz12tu",
        }
    st = (strand or "+").strip() or "+"
    if st not in {"+", "-"}:
        st = "+"
    if st == "-":
        seq = seq.translate(_COMP)[::-1]
    return {
        "ok": True,
        "assembly": "GRCz12tu",
        "genome_build": "GRCz12tu",
        "chromosome": chrom_disp,
        "accession": acc,
        "start": s,
        "end": e,
        "strand": st,
        "length": len(seq),
        "sequence": seq,
        "source_fasta": os.path.basename(fasta),
        "method": "local_faidx",
    }
