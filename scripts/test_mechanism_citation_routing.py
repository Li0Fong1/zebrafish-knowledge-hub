# -*- coding: utf-8 -*-
"""机制题不得走 ID 映射；引用展示题不得跑检索；短中文科学题不得粘上下文。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "llm", ROOT / "core", ROOT / "agent", ROOT / "database"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from gene_formatter import (  # noqa: E402
    is_biology_mechanism_question,
    is_citation_display_question,
    is_id_mapping_question,
    is_gene_version_question,
    is_standalone_science_question,
    is_explanatory_followup,
    is_complaint_followup,
    format_id_mapping_block,
)
from hub_orchestrator import (  # noqa: E402
    expand_question_with_history,
    _looks_like_followup,
    _run_id_mapper,
    DEFAULT_ANSWER_SCOPE,
    normalize_answer_scope,
    is_corpus_stats_question,
)
from zebrafish_prompt import (  # noqa: E402
    ensure_reference_footer,
    answer_citation_display_question,
    papers_from_numbered_context,
    is_system_meta_question,
)


def test_classifiers():
    assert is_biology_mechanism_question("斑马鱼基因复制机制")
    assert is_biology_mechanism_question("斑马鱼基因的转录机制")
    assert is_biology_mechanism_question("ZGA 与母源-合子转换机制")
    assert not is_biology_mechanism_question("tp53 对应哪个 Ensembl")
    assert not is_biology_mechanism_question("p53")
    assert not is_id_mapping_question("斑马鱼基因复制机制")
    assert not is_id_mapping_question("斑马鱼基因的转录机制")
    assert not is_id_mapping_question("你这是啥回答啊")
    assert not is_gene_version_question("斑马鱼基因复制机制")
    assert is_standalone_science_question("斑马鱼基因复制机制")
    assert not _looks_like_followup("斑马鱼基因复制机制")
    assert is_citation_display_question("你为什么不显示引用文献")
    assert is_citation_display_question("为什么不显示参考文献")
    assert not is_citation_display_question("斑马鱼基因的转录机制")
    assert not is_system_meta_question("你为什么不显示引用文献")
    assert is_complaint_followup("你这是啥回答啊")
    assert is_explanatory_followup("你这是啥回答啊")
    assert is_explanatory_followup("你为什么不显示引用文献")
    assert not is_explanatory_followup("斑马鱼基因复制机制")
    assert not is_biology_mechanism_question("你这是啥回答啊")


def test_anaphora_does_not_reach_past_geneless_turn():
    """上一句没有基因时，不能回头去抓更早的 p53。"""
    hist = [
        {"role": "user", "content": "p53 是什么"},
        {"role": "assistant", "content": "tp53 是肿瘤抑制基因。"},
        {"role": "user", "content": "斑马鱼的突变基因"},
        {"role": "assistant", "content": "突变不是一个固定清单。"},
    ]
    effective, _h, expanded = expand_question_with_history("它还有什么功能", hist)
    assert expanded is True
    assert "p53" not in effective
    assert "tp53" not in effective
    assert "关于基因" not in effective


def test_broad_words_are_not_followups():
    hist = [
        {"role": "user", "content": "p53 是什么"},
        {"role": "assistant", "content": "tp53。"},
    ]
    for q in ("再讲讲心脏发育", "引用格式怎么写", "还有哪些数据库"):
        effective, _h, expanded = expand_question_with_history(q, hist)
        assert expanded is False, (q, effective)
        assert "p53" not in effective


def test_short_cite_followup_keeps_previous_answer():
    hist = [
        {"role": "user", "content": "斑马鱼基因的转录机制"},
        {"role": "assistant", "content": "转录依赖母源因子 [1]。"},
    ]
    effective, _h, expanded = expand_question_with_history("只有一篇吗", hist)
    assert expanded is True
    assert "上一答摘要" in effective
    assert "关于基因" not in effective


def test_assembly_token_is_not_a_gene():
    from gene_toolkit import extract_gene_from_question

    assert extract_gene_from_question("请用 GRCz11 和 GRCz12tu 说明染色体") is None
    assert extract_gene_from_question("mRNA 表达怎么测") is None
    assert extract_gene_from_question("tp53 在哪些组织表达") == "tp53"


def test_product_howto_sequence_display():
    from zebrafish_prompt import is_hub_product_howto_question, answer_hub_product_howto

    q = "斑马鱼的基因序列是以什么方式展示的"
    assert is_hub_product_howto_question(q)
    assert not is_hub_product_howto_question("斑马鱼基因的转录机制")
    out = answer_hub_product_howto(q)
    assert "序列" in out and "GRCz12tu" in out
    assert "文献检索这次没有完成" not in out
    effective, _h, expanded = expand_question_with_history(
        q,
        [
            {"role": "user", "content": "p53 是什么"},
            {"role": "assistant", "content": "tp53。"},
        ],
    )
    # 完整产品问句应独立，不粘基因；短路在 run_scoped_chat，这里至少不展开成关于 p53
    assert "关于基因 p53" not in effective


def test_literature_stats_not_glued_to_previous_gene():
    """上一问是突变基因、更早出现过 p53 时，文献统计不得改写成关于 p53。"""
    hist = [
        {"role": "user", "content": "p53 是什么"},
        {"role": "assistant", "content": "tp53 是肿瘤抑制基因。"},
        {"role": "user", "content": "斑马鱼的突变基因"},
        {
            "role": "assistant",
            "content": "斑马鱼的“突变基因”不是一个固定清单，而是按基因、等位类型记录。",
        },
    ]
    q = "斑马鱼的文献统计"
    assert is_corpus_stats_question(q)
    assert not _looks_like_followup(q)
    effective, kept, expanded = expand_question_with_history(q, hist)
    assert expanded is False, (expanded, effective)
    assert effective == q
    assert "p53" not in effective
    assert "tp53" not in effective
    assert kept == []


def test_real_followup_still_uses_latest_gene():
    hist = [
        {"role": "user", "content": "p53 是什么"},
        {"role": "assistant", "content": "tp53。"},
        {"role": "user", "content": "shha 在哪表达"},
        {"role": "assistant", "content": "中线高表达。"},
    ]
    effective, _h, expanded = expand_question_with_history("它还有什么功能", hist)
    assert expanded is True
    assert "关于基因 shha" in effective
    assert "关于基因 p53" not in effective


def test_history_not_glued():
    hist = [
        {"role": "user", "content": "斑马鱼基因的转录机制"},
        {"role": "assistant", "content": "……[62][63]\n"},
        {"role": "user", "content": "你为什么不显示引用文献"},
        {"role": "assistant", "content": "……"},
    ]
    q = "斑马鱼基因复制机制"
    effective, _h, expanded = expand_question_with_history(q, hist)
    assert expanded is False, (expanded, effective)
    assert "对话上下文" not in effective, effective
    assert effective == q


def test_citation_short_circuit_text():
    hist = [
        {
            "role": "assistant",
            "content": "结论 [1][2]。\n\n参考文献：\n[1] AAA. 2020. ZFQA-000001\n[2] BBB. 2021. ZFQA-000002",
        }
    ]
    out = answer_citation_display_question("你为什么不显示引用文献", hist)
    assert "ZFQA-000001" in out
    assert "历史基因版本统一" not in out
    assert "【历史基因版本统一】" not in out


def test_complaint_not_glued_as_gene():
    hist = [
        {"role": "user", "content": "斑马鱼基因的转录机制"},
        {"role": "assistant", "content": "【历史基因版本统一】\n查询输入：斑马鱼基因的转录机制"},
    ]
    q = "你这是啥回答啊"
    effective, _h, expanded = expand_question_with_history(q, hist)
    assert expanded is True
    text = format_id_mapping_block({
        "query": effective,
        "resolved": False,
        "notes": ["未在本地库找到匹配"],
        "reference": {},
    })
    assert "【历史基因版本统一】" not in text, text
    assert "build_gene_aliases" not in text


def test_id_mapper_skip_block():
    unresolved = {
        "query": "斑马鱼基因的转录机制",
        "resolved": False,
        "notes": ["未在本地库（GRCz11）找到匹配；可运行 scripts/build_gene_aliases 重建别名索引"],
        "reference": {
            "assembly": "GRCz11",
            "ncbi_assembly": "GCF_000002035.6",
            "taxon_id": 7955,
            "ncbi_reference_refseq": "GCF_049306965.1",
            "ncbi_reference_assembly": "GRCz12tu",
            "ensembl_release": "116",
        },
    }
    text = format_id_mapping_block(unresolved)
    assert "【历史基因版本统一】" not in text, text
    assert "build_gene_aliases" not in text
    assert "不是基因符号" in text


def test_default_scope_is_hub():
    assert DEFAULT_ANSWER_SCOPE == "hub"
    assert normalize_answer_scope("") == "hub"
    assert normalize_answer_scope(None) == "hub"
    assert normalize_answer_scope("auto") == "hub"
    assert normalize_answer_scope("default") == "hub"


def test_id_mapper_never_resolves_chinese_sentence():
    for q in (
        "斑马鱼基因的转录机制",
        "斑马鱼基因复制机制",
        "你这是啥回答啊",
        "你这是啥回答啊\n（对话上下文：上一问：斑马鱼基因的转录机制）",
    ):
        r = _run_id_mapper(q)
        assert r.get("resolved") is False, (q, r)
        assert "build_gene_aliases" not in str(r)
        text = format_id_mapping_block(r)
        assert "【历史基因版本统一】" not in text, (q, text)
        assert "build_gene_aliases" not in text


def test_copy_mechanism_not_id_template():
    q = "斑马鱼基因复制机制"
    assert is_biology_mechanism_question(q)
    assert not is_id_mapping_question(q)
    text = format_id_mapping_block({
        "query": q,
        "resolved": False,
        "notes": ["未在本地库找到匹配"],
        "reference": {},
    })
    assert "【历史基因版本统一】" not in text
    assert "不是基因符号" in text


def test_footer_from_pack():
    ctx = (
        "下列证据已编号，共 3 篇\n\n"
        "[1] 2020 | PMID:1 | gold | ZFQA-000111 | 相关#1 · 近十年\n"
        "题名：Paper One\nabstract...\n\n"
        "[2] 2021 | PMID:2 | gold | ZFQA-000222 | 相关#2 · 近十年\n"
        "题名：Paper Two\n\n"
        "[62] 2017 | PMID:62 | gold | ZFQA-001041 | 相关#62 · 近十年\n"
        "题名：Zebrafish Znfl1 proteins control hoxb1b\n"
    )
    papers = papers_from_numbered_context(ctx)
    assert [p[0] for p in papers] == [1, 2, 62], papers
    body = "早期胚胎依赖母源 mRNA [62]。转录因子结合增强子 [1][2]。"
    out = ensure_reference_footer(body, ctx, question="斑马鱼基因的转录机制")
    assert "参考文献" in out
    assert "Paper One" in out
    assert "Zebrafish Znfl1" in out
    assert "ZFQA" not in out.split("参考文献")[-1]
    head = out.split("参考文献")[0]
    assert "[62]" not in head
    assert "早期胚胎依赖母源 mRNA [3]。" in head
    assert "转录因子结合增强子 [1][2]。" in head
    foot = [ln for ln in out.split("参考文献")[-1].splitlines() if ln.startswith("[")]
    assert foot[0].startswith("[1] ") and "Paper One" in foot[0]
    assert foot[1].startswith("[2] ") and "Paper Two" in foot[1]
    assert foot[2].startswith("[3] ") and "Zebrafish Znfl1" in foot[2]


def test_footer_compact_user_jump_order():
    """生产样本：正文 [15][17][63]…[1]，文末不得再跳号。"""
    ctx = "下列证据已编号\n" + "\n".join(
        f"[{i}] 2020 | PMID:{i} | gold | ZFQA-{i:06d} | 相关#{i}\n题名：Paper {i} title\n"
        for i in range(1, 101)
    )
    body = (
        "ZGA 是分水岭事件 [15][17][63]。"
        "TBP [62]，ORFeome [44]。"
        "一句话概括 [1][7][22]。"
    )
    out = ensure_reference_footer(body, ctx, question="斑马鱼的转录机制")
    foot_ns = re.findall(r"(?m)^\[(\d+)\]\s+", out.split("参考文献")[-1])
    assert foot_ns == [str(i) for i in range(1, len(foot_ns) + 1)], foot_ns
    assert 20 <= len(foot_ns) <= 25
    head = out.split("参考文献")[0]
    assert "[63]" not in head and "[44]" not in head
    assert "ZGA 是分水岭事件 [15][16][20]。" in head
    assert "一句话概括 [1][7][17]。" in head
    foot = out.split("参考文献")[-1]
    assert "Paper 15 title" in foot and "Paper 17 title" in foot and "Paper 63 title" in foot
    assert "ZFQA" not in foot
    lines = [ln for ln in foot.splitlines() if ln.startswith("[")]
    assert "Paper 1 title" in lines[0]
    assert "Paper 2 title" in lines[1]
    assert lines[-1].startswith("[20] ") and "Paper 63 title" in lines[-1]


def main():
    test_classifiers()
    test_history_not_glued()
    test_product_howto_sequence_display()
    test_literature_stats_not_glued_to_previous_gene()
    test_real_followup_still_uses_latest_gene()
    test_anaphora_does_not_reach_past_geneless_turn()
    test_broad_words_are_not_followups()
    test_short_cite_followup_keeps_previous_answer()
    test_assembly_token_is_not_a_gene()
    test_citation_short_circuit_text()
    test_complaint_not_glued_as_gene()
    test_id_mapper_skip_block()
    test_default_scope_is_hub()
    test_id_mapper_never_resolves_chinese_sentence()
    test_copy_mechanism_not_id_template()
    test_footer_from_pack()
    test_footer_compact_user_jump_order()
    print("OK mechanism_citation_routing")


if __name__ == "__main__":
    main()
