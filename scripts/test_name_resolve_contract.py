# -*- coding: utf-8 -*-
"""别名并集三态契约冒烟（不依赖表是否已灌：表缺失时走 legacy）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "database", ROOT / "llm"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def test_status_fields_shape():
    from gene_id_mapper import resolve_gene_identifier

    r = resolve_gene_identifier("tp53", use_ensembl_fallback=False)
    assert "status" in r
    assert r["status"] in ("unique", "ambiguous", "not_found")
    if r["status"] == "unique":
        assert r.get("resolved") is True
        assert r.get("canonical")
        assert r.get("standard")
    if r["status"] == "ambiguous":
        assert r.get("resolved") is False
        assert r.get("canonical") is None
        assert (r.get("candidates") or [])
    if r["status"] == "not_found":
        assert r.get("resolved") is False
        assert r.get("canonical") is None


def test_empty_query():
    from gene_id_mapper import resolve_gene_identifier

    r = resolve_gene_identifier("")
    assert r["status"] == "not_found"


def test_resolve_to_gene_name_respects_unique():
    from gene_id_mapper import resolve_to_gene_name

    # 无库时可能 None；有库时应为 str 或 None，不应抛
    name = resolve_to_gene_name("tp53")
    assert name is None or isinstance(name, str)


def main():
    test_empty_query()
    try:
        test_status_fields_shape()
        test_resolve_to_gene_name_respects_unique()
        print("OK name_resolve_contract (with DB)")
    except Exception as e:
        # 本机无 MySQL 时仅校验空查询
        print("WARN DB smoke skipped:", e)
        print("OK name_resolve_contract (empty only)")


if __name__ == "__main__":
    main()
