# -*- coding: utf-8 -*-
"""P1/P2 修复回归：搜索 SQL LIMIT、Ensembl 垃圾查询守卫。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "llm", ROOT / "core", ROOT / "database"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from gene_id_mapper import should_try_ensembl_fallback  # noqa: E402
from gene_toolkit import _sql_limit  # noqa: E402


def test_sql_limit():
    assert _sql_limit(5) == 5
    assert _sql_limit(0) == 1
    assert _sql_limit(999, 50) == 50
    assert _sql_limit("12") == 12


def test_search_sql_interpolated():
    src = (ROOT / "database" / "gene_toolkit.py").read_text(encoding="utf-8")
    assert "LIMIT {int(limit)}" not in src
    assert "def _sql_limit" in src
    fn = src.split("def search_genes", 1)[1].split("def id_convert", 1)[0]
    assert "LIMIT {lim}" in fn


def test_ensembl_guard():
    assert should_try_ensembl_fallback("tp53")
    assert should_try_ensembl_fallback("shha")
    assert should_try_ensembl_fallback("ENSDARG00000035559")
    assert should_try_ensembl_fallback("nkx2.2a")
    assert not should_try_ensembl_fallback("not_a_gene_xyz_zzz")
    assert not should_try_ensembl_fallback("斑马鱼有几条染色体")
    assert not should_try_ensembl_fallback("p53 对应哪个标准名")
    assert not should_try_ensembl_fallback("")
    assert not should_try_ensembl_fallback("30590")  # NCBI，本地已查


def test_fasta_header_match():
    from sequence_toolkit import _header_matches

    hdr = (
        "ENSDART00000103202.6 cdna gene:ENSDARG00000035559.8 "
        "gene_symbol:tp53 description:tumor protein p53"
    )
    assert _header_matches(hdr, ["gene_symbol:tp53", "ENSDARG00000035559"])
    assert not _header_matches(hdr, ["gene_symbol:wrap53"])
    assert not _header_matches("gene_symbol:wrap53 description", ["gene_symbol:tp53"])

    ncbi = (
        "lcl|NC_007116.1_cds_NP_001258749.1_1 [gene=tp53] "
        "[protein=cellular tumor antigen p53 isoform 1] [protein_id=NP_001258749.1]"
    )
    assert _header_matches(ncbi, ["gene_symbol:tp53", "[gene=tp53]"])
    assert not _header_matches(ncbi, ["gene_symbol:wrap53"])
    wrap = "lcl|NC_007116.1_cds_XP_1 [gene=wrap53] [protein_id=XP_001]"
    assert not _header_matches(wrap, ["[gene=tp53]", "gene_symbol:tp53"])


def test_blast_picker_no_silent_grcz11(monkeypatch=None):
    import os
    from seq_toolkit import _pick_blast_fasta, _allow_grcz11_fallback

    if _allow_grcz11_fallback():
        return
    picked = _pick_blast_fasta()
    if picked:
        n = picked.replace("\\", "/").lower()
        assert "grcz12" in n or "049306965" in n, picked
    else:
        assert os.getenv("BLAST_ALLOW_GRCZ11_FALLBACK", "0") not in ("1", "true")


def main():
    test_sql_limit()
    test_search_sql_interpolated()
    test_ensembl_guard()
    test_fasta_header_match()
    test_blast_picker_no_silent_grcz11()
    print("OK fix_search_stream_ensembl")


if __name__ == "__main__":
    main()
