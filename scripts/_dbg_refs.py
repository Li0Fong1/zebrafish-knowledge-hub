# -*- coding: utf-8 -*-
"""临时诊断：为什么别名类问题没走全文别名索引出参考文献。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.entry import bootstrap_app  # noqa: E402

bootstrap_app()

from zebrafish_prompt import (  # noqa: E402
    classify_answer_type, _ref_footer_min, _ref_footer_max, _alias_reference_tuples,
)

q = "tp53 都有哪些别名和历史名？"
print("question =", q)
print("qtype    =", classify_answer_type(q))
print("min/max  =", _ref_footer_min(q), "/", _ref_footer_max(q))

# 逐层排查（不吞异常）
try:
    from gene_alias_evidence import is_alias_question, is_ortholog_question
    print("is_alias =", is_alias_question(q), " is_ortholog =", is_ortholog_question(q))
except Exception as e:  # noqa: BLE001
    print("IMPORT gene_alias_evidence FAILED:", e)

try:
    from gene_toolkit import extract_gene_from_question
    print("gene     =", extract_gene_from_question(q))
except Exception as e:  # noqa: BLE001
    print("IMPORT/localize gene FAILED:", e)

try:
    from gene_alias_fulltext import alias_reference_items
    items = alias_reference_items("tp53", limit=25)
    print("alias_reference_items(tp53) =", len(items))
    for it in items[:3]:
        print("   ", it.get("year"), "|", (it.get("title") or "")[:70], "|", it.get("doi"))
except Exception as e:  # noqa: BLE001
    import traceback
    print("alias_reference_items FAILED:", e)
    traceback.print_exc()

t = _alias_reference_tuples(q, limit=25)
print("_alias_reference_tuples =", len(t))
for x in t[:3]:
    print("   tuple:", x[0], x[1], x[3][:60])
