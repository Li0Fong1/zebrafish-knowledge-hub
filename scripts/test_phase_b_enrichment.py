# -*- coding: utf-8 -*-
"""Phase B 验收：实体双链、资料补全、映射、库清单。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "agent", ROOT / "database", ROOT / "llm", ROOT / "api"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))
    print(("PASS" if ok else "FAIL"), name, str(detail)[:200], flush=True)


def test_dual_links_and_resources():
    from answer_enrichment import enrich_chat_result, linkify_answer_body

    detail = {
        "basic": {
            "gene_name": "tp53",
            "gene_symbol": "tp53",
            "ensembl_id": "ENSDARG00000035559",
            "zfin_id": "ZDB-GENE-990415-270",
            "chromosome": "5",
            "gene_function": "tumor protein p53",
            "uniprot_id": "P79734",
            "expression_summary": "ubiquitous",
        },
        "standard": {"gene_symbol": "tp53", "assembly": "GRCz12tu"},
        "go": [{"go_term": "GO:0006915 apoptotic process"}],
        "kegg": [{"pathway_id": "dre04115", "pathway_name": "p53 signaling"}],
        "expression": [{"tissue": "liver", "expression_level": 12.3}],
        "aliases": [{"alias": "p53"}],
        "xrefs": [{"db_name": "ncbi_gene", "db_id": "30590"}],
    }
    result = {
        "answer": "tp53 与 ENSDARG00000035559 和 ZDB-GENE-990415-270 相关，见 GO:0006915。\n\n参考文献：\n[1] x",
        "gene_detail": detail,
        "context": "证据",
    }
    out = enrich_chat_result("tp53 的功能与版本映射", result)
    ans = out["answer"]
    ok_local = "#/gene/tp53" in ans
    ok_zfin = "zfin.org" in ans.lower()
    ok_ens = "ensembl.org" in ans.lower()
    ok_panel = "本地资料补全" in ans
    ok_map = "跨库基因名映射" in ans
    ok_dbs = ("本次用到的组学库" in ans or "本次用到的库" in ans) and "ZFIN" in ans
    ok_seq = "#/sequence/" in ans or "序列" in ans
    rec(
        "B8-13 双链+补全+映射+库清单",
        ok_local and ok_zfin and ok_ens and ok_panel and ok_map and ok_dbs,
        f"local={ok_local} zfin={ok_zfin} ens={ok_ens} panel={ok_panel} map={ok_map} dbs={ok_dbs} seq={ok_seq}",
    )
    rp = out.get("resource_panel") or {}
    rec("B resource_panel 字段", bool(rp.get("databases_used")) and "tp53" in (rp.get("genes") or []), rp)
    names = [d.get("name") for d in (rp.get("databases_used") or [])]
    rec("B 本地文献库无 PubmedYes", "本地文献库" in names and not any("PubmedYes" in str(n) for n in names), names)
    rec("B 清单含 ZFIN", "ZFIN" in names, names)


def test_lit_footer_dbs_in_list():
    """文献答里正文写了 ZFIN/Ensembl，即使无 gene_detail，也应进「本次用到的组学库与资料」。"""
    from answer_enrichment import enrich_chat_result

    result = {
        "answer": (
            "该基因在发育中起关键作用。身份可参考 ZFIN 与 Ensembl；"
            "蛋白条目见 UniProt。\n\n参考文献：\n[1] x"
        ),
        "context": "证据片段",
    }
    out = enrich_chat_result("斑马鱼某通路文献综述", result)
    ans = out["answer"]
    names = [d.get("name") for d in ((out.get("resource_panel") or {}).get("databases_used") or [])]
    ok_zfin = "ZFIN" in names and "- [ZFIN](" in ans
    ok_ens = any("Ensembl" in str(n) for n in names)
    ok_uni = "UniProt" in names
    ok_local = "本地文献库" in names and not any("PubmedYes" in str(n) for n in names)
    ok_title = "本次用到的组学库与资料" in ans
    rec(
        "B 脚注库进清单",
        ok_zfin and ok_ens and ok_uni and ok_local and ok_title,
        f"names={names} zfin={ok_zfin} ens={ok_ens} uni={ok_uni} local={ok_local} title={ok_title}",
    )


def test_omics_question_forces_dbs():
    """问句含 ZFIN/映射/基因组/组装时，即使答句未点名，也应强制挂组学库。"""
    from answer_enrichment import enrich_chat_result

    result = {
        "answer": "该符号在不同组装版本中对应关系需对照本地映射表。\n\n参考文献：\n[1] x",
        "context": "hit",
    }
    out = enrich_chat_result("ZFIN 与 Ensembl 基因组组装如何映射到 GRCz12tu 标准名", result)
    names = [d.get("name") for d in ((out.get("resource_panel") or {}).get("databases_used") or [])]
    ok = (
        "ZFIN" in names
        and any("Ensembl" in str(n) for n in names)
        and any("NCBI" in str(n) or "RefSeq" in str(n) for n in names)
    )
    rec("B 问句强制组学库", ok, names)


def test_linkify_skips_existing():
    from answer_enrichment import linkify_answer_body

    raw = "见 [tp53](https://example.com/x) 与 plain"
    out = linkify_answer_body(raw, ["tp53"], {})
    # 已有链接不应被拆坏
    ok = "[tp53](https://example.com/x)" in out
    rec("B8 保留已有 markdown 链", ok, out[:120])


def test_id_patterns():
    from answer_enrichment import linkify_answer_body

    out = linkify_answer_body(
        "通路 dre04115 与 NM_131327 值得关注",
        [],
        {},
    )
    ok = "kegg.jp" in out and "nuccore" in out and "#/id/" in out
    rec("B12 GO/KEGG/RefSeq 外链", ok, out[:200])


def test_once_per_url_gene_and_go():
    """同一 ZFIN/Ensembl/QuickGO URL 整篇至多一次；基因后续提及保持纯文本。"""
    from answer_enrichment import linkify_answer_body

    detail = {
        "basic": {
            "gene_name": "hsp70",
            "gene_symbol": "hsp70",
            "ensembl_id": "ENSDARG00000029688",
            "zfin_id": "ZDB-GENE-990415-91",
            "uniprot_id": "Q90473",
            "ncbi_gene_id": "30171",
            "expression_summary": "stress inducible",
        },
        "expression": [{"tissue": "gill", "expression_level": 40}],
        "xrefs": [{"db_name": "ncbi_gene", "db_id": "30171"}],
    }
    raw = (
        "hsp70 是热休克蛋白。身份见 ENSDARG00000029688 与 ZDB-GENE-990415-91。"
        "定位含 GO:0005737 与 GO:0005829。"
        "再次提到 hsp70 与 GO:0005737 不应再刷链。"
        "UniProt Q90473；GeneID 30171；GEO GSE12345；通路 WP1234。"
    )
    out = linkify_answer_body(raw, ["hsp70"], {"hsp70": detail})

    zfin_url = "https://zfin.org/ZDB-GENE-990415-91"
    ens_url = "https://www.ensembl.org/Danio_rerio/Gene/Summary?g=ENSDARG00000029688"
    go1 = "https://www.ebi.ac.uk/QuickGO/term/GO:0005737"
    go2 = "https://www.ebi.ac.uk/QuickGO/term/GO:0005829"

    ok_zfin_once = out.count(zfin_url) == 1
    ok_ens_once = out.count(ens_url) == 1
    ok_go1_once = out.count(go1) == 1
    ok_go2_once = out.count(go2) == 1  # 不同 GO 可各链一次
    ok_gene_local_once = out.count("#/gene/hsp70") == 1
    # 第二次 hsp70 / 重复 GO 应仍以纯文本出现
    ok_plain_gene = "再次提到 hsp70" in out
    ok_plain_go = "与 GO:0005737 不应" in out or out.count("GO:0005737") >= 2
    ok_ncbi = "ncbi.nlm.nih.gov/gene/30171" in out
    ok_uni = "uniprot.org" in out.lower()
    ok_bgee = "bgee.org/gene/" in out.lower()
    ok_geo = "ncbi.nlm.nih.gov/geo/" in out
    ok_wp = "wikipathways.org" in out.lower()
    rec(
        "B once-per-URL 基因/GO",
        ok_zfin_once
        and ok_ens_once
        and ok_go1_once
        and ok_go2_once
        and ok_gene_local_once
        and ok_plain_gene
        and ok_ncbi
        and ok_uni,
        (
            f"zfin={out.count(zfin_url)} ens={out.count(ens_url)} "
            f"go1={out.count(go1)} go2={out.count(go2)} "
            f"gene_local={out.count('#/gene/hsp70')} "
            f"ncbi={ok_ncbi} uni={ok_uni} bgee={ok_bgee} geo={ok_geo} wp={ok_wp}"
        ),
    )
    rec(
        "B 组学库外链覆盖",
        ok_bgee and ok_geo and ok_wp,
        f"bgee={ok_bgee} geo={ok_geo} wp={ok_wp}",
    )


def test_omics_accessions_in_db_list():
    """正文出现 GEO / WikiPathways / ArrayExpress 时应进入资料清单。"""
    from answer_enrichment import enrich_chat_result

    result = {
        "answer": "公开数据见 GSE99000、WikiPathways WP554 与 E-MTAB-1234。\n\n参考文献：\n[1] x",
        "context": "hit",
    }
    out = enrich_chat_result("表达实验归档有哪些", result)
    names = [d.get("name") for d in ((out.get("resource_panel") or {}).get("databases_used") or [])]
    ok = (
        any("GEO" in str(n) for n in names)
        and any("WikiPathways" in str(n) for n in names)
        and any("ArrayExpress" in str(n) for n in names)
    )
    rec("B GEO/WP/AE 进清单", ok, names)


def test_shared_budget_with_materials():
    """正文已挂的基因外链，资料补全块不再重复同一 ZFIN/Ensembl URL。"""
    from answer_enrichment import enrich_chat_result

    detail = {
        "basic": {
            "gene_symbol": "tp53",
            "gene_name": "tp53",
            "ensembl_id": "ENSDARG00000035559",
            "zfin_id": "ZDB-GENE-990415-270",
            "chromosome": "5",
            "gene_function": "p53",
            "uniprot_id": "P79734",
            "expression_summary": "ubiquitous",
        },
        "standard": {"gene_symbol": "tp53"},
        "go": [{"go_term": "GO:0006915 apoptotic process"}],
        "kegg": [{"pathway_id": "dre04115", "pathway_name": "p53 signaling"}],
        "expression": [{"tissue": "liver", "expression_level": 12.3}],
        "xrefs": [{"db_name": "ncbi_gene", "db_id": "30590"}],
    }
    result = {
        "answer": "tp53 功能概述。再次提到 tp53。\n\n参考文献：\n[1] x",
        "gene_detail": detail,
        "context": "证据",
    }
    out = enrich_chat_result("tp53 的功能", result)
    ans = out["answer"]
    zfin_gene = ans.count("https://zfin.org/ZDB-GENE-990415-270")
    ens_gene = ans.count(
        "https://www.ensembl.org/Danio_rerio/Gene/Summary?g=ENSDARG00000035559"
    )
    ok_panel = "本地资料补全" in ans
    names = [d.get("name") for d in ((out.get("resource_panel") or {}).get("databases_used") or [])]
    ok = zfin_gene == 1 and ens_gene == 1 and ok_panel and "ZFIN" in names
    rec(
        "B 正文+资料补全共享去重",
        ok,
        f"zfin_url={zfin_gene} ens_url={ens_gene} panel={ok_panel} names_has_zfin={'ZFIN' in names}",
    )


def test_materials_zfin_force_preserved():
    """维护升级：问句点名 ZFIN/资料库时清单必含 ZFIN。"""
    from answer_enrichment import enrich_chat_result

    out = enrich_chat_result(
        "zfin呢？斑马鱼基因资料库",
        {"answer": "可查阅权威注释。\n\n参考文献：\n[1] x", "context": "hit"},
    )
    names = [d.get("name") for d in ((out.get("resource_panel") or {}).get("databases_used") or [])]
    ans = out["answer"]
    ok = "ZFIN" in names and "- [ZFIN](" in ans
    rec("B 资料库 ZFIN 强制保留", ok, names)


def test_dotted_gene_no_prefix_spam():
    """hsp70 不得拆进 hsp70.1；外链挂在正文首次出现的符号上。"""
    from answer_enrichment import linkify_answer_body

    detail = {
        "basic": {
            "gene_symbol": "hsp70",
            "ensembl_id": "ENSDARG00000029688",
            "zfin_id": "ZDB-GENE-990415-91",
        },
    }
    raw = "先说 hsp70，再说 hsp70.1 与 hsp70。"
    out = linkify_answer_body(
        raw,
        ["hsp70", "hsp70.1"],
        {"hsp70": detail, "hsp70.1": detail},
    )
    ok = (
        "[hsp70 (ZFIN" not in out
        and out.count("#/gene/hsp70)") == 1  # local gene once (not hsp70.1)
        and "#/gene/hsp70.1" in out
        and out.count("https://zfin.org/ZDB-GENE-990415-91") == 1
    )
    rec("B 点号基因不前缀误链", ok, out[:220])


def main():
    test_dual_links_and_resources()
    test_lit_footer_dbs_in_list()
    test_omics_question_forces_dbs()
    test_linkify_skips_existing()
    test_id_patterns()
    test_once_per_url_gene_and_go()
    test_omics_accessions_in_db_list()
    test_shared_budget_with_materials()
    test_materials_zfin_force_preserved()
    test_dotted_gene_no_prefix_spam()
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"\nSUMMARY pass={len(RESULTS)-len(fails)} fail={len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
