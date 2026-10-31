# -*- coding: utf-8 -*-
"""富集结果「方面/来源」标注验收（离线，不连库、不联网）。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts\\test_enrichment_labels.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "llm", ROOT / "rag", ROOT / "database", ROOT / "agent"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

RESULTS: list[tuple[str, bool, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), name, detail[:200], flush=True)


def test_go_aspect() -> None:
    from enrichment_toolkit import _go_aspect

    ok = (
        _go_aspect("BP")[0] == "BP"
        and _go_aspect("bp")[1] == "生物学过程"
        and _go_aspect("P")[0] == "BP"
        and _go_aspect("MF")[1] == "分子功能"
        and _go_aspect("cc")[1] == "细胞组分"
        and _go_aspect("")[1] == "方面未标注"
        and _go_aspect("SomethingNew")[0] == "NA"
        and "SomethingNew" in _go_aspect("SomethingNew")[1]
    )
    rec("L1 GO 方面归一（含未知取值不猜）", ok,
        f"{_go_aspect('BP')} | {_go_aspect('')} | {_go_aspect('SomethingNew')}")


def test_go_id_name() -> None:
    from enrichment_toolkit import _go_id_and_name, _go_url

    a = _go_id_and_name("GO:0006974 DNA damage response")
    b = _go_id_and_name("GO:1902262")
    c = _go_id_and_name("DNA damage response")
    ok = (
        a == ("GO:0006974", "DNA damage response")
        and b == ("GO:1902262", "")
        and c == ("", "DNA damage response")
        and "QuickGO/term/GO:1902262" in _go_url(b[0])
    )
    rec("L2 GO 条目拆 ID/名称（只有 ID 也不崩）", ok, f"{a} | {b} | {c}")


def test_pathway_source() -> None:
    from enrichment_toolkit import _pathway_source_of, _pathway_url

    r = _pathway_source_of("", "R-DRE-6804754")
    w = _pathway_source_of("", "WP1234")
    k = _pathway_source_of("kegg_rest", "dre04110")
    u = _pathway_source_of("", "")
    ok = (
        r[0] == "Reactome" and w[0] == "WikiPathways" and k[0] == "KEGG" and u[0] == "NA"
        and "reactome.org" in _pathway_url("R-DRE-6804754", "Reactome")
        and "wikipathways.org" in _pathway_url("WP1234", "WikiPathways")
        and "kegg.jp/entry/dre04110" in _pathway_url("dre04110", "KEGG")
    )
    rec("L3 通路来源归一 + 外链", ok, f"{r} | {w} | {k} | {u}")


def test_format_shows_aspect_and_source() -> None:
    from enrichment_toolkit import format_enrichment_answer

    go = {
        "success": True, "padj_cutoff": 0.05, "significant_count": 46, "total_tested": 52,
        "target_gene_count": 1, "universe_size": 20168,
        "aspect_summary": [{"label": "生物学过程", "count": 40}, {"label": "分子功能", "count": 6}],
        "terms": [
            {"go_term": "GO:0006974 DNA damage response", "go_id": "GO:0006974",
             "term_name": "DNA damage response", "aspect": "BP", "aspect_label": "生物学过程",
             "name_missing": False, "url": "https://www.ebi.ac.uk/QuickGO/term/GO:0006974",
             "padj": 3.22e-04, "pvalue": 4.96e-05, "gene_ratio": "1/1", "bg_ratio": "1/20168",
             "fold_enrichment": 20168.0, "genes": ["tp53"], "gene_count": 1, "term_size": 1},
            {"go_term": "GO:1902262", "go_id": "GO:1902262", "term_name": "", "aspect": "NA",
             "aspect_label": "方面未标注", "name_missing": True, "url": "",
             "padj": 3.22e-04, "pvalue": 4.96e-05, "gene_ratio": "1/1", "bg_ratio": "1/20168",
             "fold_enrichment": 20168.0, "genes": ["tp53"], "gene_count": 1, "term_size": 1},
        ],
    }
    kegg = {
        "success": True, "padj_cutoff": 0.05, "significant_count": 33, "total_tested": 33,
        "universe_size": 8521,
        "source_summary": [{"label": "Reactome 反应通路", "count": 20},
                           {"label": "KEGG 通路", "count": 13}],
        "pathways": [
            {"pathway_id": "R-DRE-6804754", "pathway_name": "Regulation of TP53 Expression",
             "source_code": "Reactome", "source_label": "Reactome 反应通路",
             "url": "https://reactome.org/content/detail/R-DRE-6804754",
             "padj": 3.67e-03, "pvalue": 1e-4, "gene_ratio": "1/1", "bg_ratio": "1/8521",
             "fold_enrichment": 8521.0, "overlap_genes": ["tp53"], "gene_count": 1,
             "pathway_gene_count": 30, "category": "Signal Transduction"},
        ],
    }
    out = format_enrichment_answer(go, kegg)
    ok = (
        "【看的方面】" in out
        and "生物学过程" in out
        and "【方面分布】" in out
        and "Reactome 反应通路" in out
        and "名称未收录" in out
        and "DNA damage response" in out
    )
    rec("L4 文本版带方面/来源/分布", ok, out.replace("\n", " ⏎ ")[:190])


def main() -> int:
    for fn in (test_go_aspect, test_go_id_name, test_pathway_source, test_format_shows_aspect_and_source):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            rec(fn.__name__, False, f"{type(e).__name__}: {e}")
    bad = [r for r in RESULTS if not r[1]]
    print("-" * 68)
    print(f"共 {len(RESULTS)} 项：通过 {len(RESULTS) - len(bad)}，失败 {len(bad)}"
          + (" → " + ", ".join(r[0] for r in bad) if bad else " → 全部通过"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
