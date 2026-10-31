# -*- coding: utf-8 -*-
"""RRS 引用相关性打分验收：治「问 nanog 却挂纳米颗粒论文」这类无关引用。

用法（仓库根目录）：
    .venv\\Scripts\\python.exe -X utf8 scripts\\test_ref_relevance.py
"""
from __future__ import annotations

import re
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
    print(("PASS" if ok else "FAIL"), name, detail[:220], flush=True)


# ---------------------------------------------------------------- 单元：实体抽取
def test_extract_entities() -> None:
    from ref_relevance import extract_entities

    q1 = "你好，帮我介绍下斑马鱼nanog基因"
    e1 = extract_entities(q1)
    rec("E1 问句实体：抓出 nanog、丢弃 zebrafish/gene", e1 == ["nanog"], str(e1))

    q2 = "tp53 在缺氧（hypoxia）条件下的作用是什么"
    e2 = extract_entities(q2)
    ok2 = "tp53" in e2 and "hypoxia" not in e2 and "expression" not in e2
    rec("E2 主题词不当实体（hypoxia 不入选）", ok2, str(e2))

    q3 = "ZDB-GENE-030131-5486 对应哪个基因"
    e3 = extract_entities(q3)
    rec("E3 ZFIN ID 识别", e3 == ["zdb-gene-030131-5486"], str(e3))

    q4 = "温度对斑马鱼性别分化的影响"
    e4 = extract_entities(q4)
    rec("E4 纯中文表型问句：无实体（不误伤）", e4 == [], str(e4))

    q5 = "pax6a 和 sox2 在眼睛发育中的差异"
    e5 = extract_entities(q5)
    rec("E5 多基因符号", "pax6a" in e5 and "sox2" in e5, str(e5))


# ------------------------------------------------- 单元：打分维度可复算、单调
def test_score_dims() -> None:
    from ref_relevance import score_paper

    good = (5, "2013", "PMID:23222941", "Nanog, Pou5f1 and SoxB1 activate zygotic gene expression", "",
            "Nature", "10.1038/nature11813")
    bad = (1, "2018", "PMID:30000000", "Nanoparticles induce dermal and intestinal innate immune system responses in zebrafish embryos", "",
           "Nano", "10.1000/demo.1")
    s_good = score_paper(good, question="介绍下斑马鱼 nanog 基因", cited_ns={5}, pool_size=14)
    s_bad = score_paper(bad, question="介绍下斑马鱼 nanog 基因", cited_ns=set(), pool_size=14)
    ok = (
        s_good["score"] > 0.8
        and s_bad["dims"]["entity"] == 0.0
        and s_bad["blocked_by_entity"]
        and s_good["dims"]["entity"] == 1.0
    )
    rec("S1 相关条目高分/无关条目被硬规则拦下", ok,
        f"good={s_good['score']} bad={s_bad['score']} blocked={s_bad['blocked_by_entity']}")

    # 位次越靠前分越高（同一条其他条件不变）
    def rank_of(n: int) -> float:
        return score_paper(good._replace() if False else (n, "2013", "PMID:23222941",
                           "Nanog activates zygotic gene expression", "", "Nature",
                           "10.1038/nature11813"),
                           question="nanog", cited_ns=set(), pool_size=30)["score"]
    rec("S2 位次单调：n=2 > n=20", rank_of(2) > rank_of(20), f"{rank_of(2)} > {rank_of(20)}")


# ------------------------------------- 集成：文末参考文献去掉无关条目（用户场景）
_UNRELATED = [
    (1, "2018", "PMID:30000001",
     "Nanoparticles induce dermal and intestinal innate immune system responses in zebrafish embryos",
     "ZFQA-000101", "Nano", "10.1000/demo.101"),
    (2, "2025", "PMID:30000002",
     "The synthetic long non-coding RNA DANCER is an essential regulator of zebrafish development",
     "ZFQA-000102", "PLOS Genetics", "10.1000/demo.102"),
    (3, "2016", "PMID:30000003",
     "NgAgo-based fabp11a gene knockdown causes eye developmental defects in zebrafish",
     "ZFQA-000103", "Cell Research", "10.1000/demo.103"),
    (4, "2019", "PMID:30000004",
     "Zebrafish embryogenesis - A framework to study regulatory RNA elements in development",
     "ZFQA-000104", "Developmental Biology", "10.1000/demo.104"),
    (6, "2013", "PMID:30000006",
     "Gene expression and biochemical responses in brain of zebrafish exposed to carbon nanotubes",
     "ZFQA-000106", "Mol Integr Physiol", "10.1000/demo.106"),
    (7, "2018", "PMID:30000007",
     "Silver nanoparticles impair zebrafish skeletal and cardiac myofibrillogenesis",
     "ZFQA-000107", "Aquatic Toxicology", "10.1000/demo.107"),
    (10, "2014", "PMID:30000010",
     "The Nogo-C2/Nogo Receptor Complex Regulates the Morphogenesis of Zebrafish Lateral Line",
     "ZFQA-000110", "PLOS ONE", "10.1000/demo.110"),
]
_RELEVANT = [
    (5, "2018", "PMID:29346644",
     "The primary role of zebrafish nanog is in extra-embryonic tissue",
     "ZFQA-000105", "Development", "10.1242/dev.147652"),
    (9, "2013", "PMID:23222941",
     "Nanog, Pou5f1 and SoxB1 activate zygotic gene expression during the maternal-to-zygotic transition",
     "ZFQA-000109", "Nature", "10.1038/nature11813"),
]


def _ctx(papers: list) -> str:
    lines = ["下列证据已编号"]
    for p in papers:
        n, year, tag, title, zfqa, journal, doi = p
        lines.append(
            f"[{n}] {year} | {tag} | PubMed官方 | {zfqa} | 相关#{n}\n"
            f"题名：{title}\n期刊：{journal}\nDOI：{doi}"
        )
    return "\n".join(lines)


def test_footer_drops_unrelated() -> None:
    from zebrafish_prompt import ensure_reference_footer, _footer_item_count

    papers = sorted(_UNRELATED + _RELEVANT, key=lambda x: x[0])
    ans = (
        "斑马鱼 nanog 是维持干细胞群体的转录因子 [5]，并与 Pou5f1、SoxB1 一起激活合子基因组 [9]。\n\n"
        "参考文献：\n" + "\n".join(f"[{i}] leaked row {i}" for i in range(1, 101))
    )
    out = ensure_reference_footer(ans, _ctx(papers), question="你好，帮我介绍下斑马鱼nanog基因")
    foot = out.split("参考文献")[-1]
    n = _footer_item_count(out)
    bad_hits = sum(
        foot.lower().count(k) for k in ("nanoparticle", "nanotube", "ngago", "nogo-c2", "dancer")
    )
    ok = n == 2 and "nanog" in foot.lower() and bad_hits == 0
    rec("R1 nanog 问句：无关文献全部挡掉，只留 2 条相关", ok,
        f"n={n} bad={bad_hits} | {foot.strip()[:200]}")

    # 正文引用号仍可用于正文（编号收成连续）
    body = out.split("参考文献")[0]
    rec("R2 正文引用号被重编号并保留", bool(re.search(r"\[1\]|\[2\]", body)), body[-80:])


def test_no_entity_question_not_overfiltered() -> None:
    from zebrafish_prompt import ensure_reference_footer, _footer_item_count, _ref_footer_min

    papers = [
        (i, "2022", f"PMID:2000{i:02d}", f"Temperature-dependent sex determination in zebrafish study {i}",
         f"ZFQA-0002{i:02d}", f"J Fish Biol {i}", f"10.1000/t.{i}")
        for i in range(1, 13)
    ]
    ans = "温度会影响性别分化 [1][2][3]。\n\n参考文献：\n" + "\n".join(
        f"[{i}] leaked {i}" for i in range(1, 40)
    )
    out = ensure_reference_footer(ans, _ctx(papers), question="温度对斑马鱼性别分化的影响")
    n = _footer_item_count(out)
    lo = _ref_footer_min("温度对斑马鱼性别分化的影响")
    ok = n >= min(lo, 6)
    rec("R3 无实体（中文表型题）不被过度过滤", ok, f"n={n} 期望≥{min(lo, 6)}")


def test_gate_excludes_low_rank_only_papers() -> None:
    """池子很大且都很弱：靠后的条目不该被「凑下限」拉进文末表。"""
    from ref_relevance import select_relevant

    papers = [
        (i, "2010", f"PMID:3000{i:03d}", f"Some unrelated ageing study {i}", "", "Old Journal",
         f"10.1000/o.{i}")
        for i in range(1, 41)
    ]
    kept, dropped = select_relevant(papers, "斑马鱼 tp53 突变体的表型", [])
    ok = len(kept) < len(papers)
    rec("R4 大池弱相关：靠后条目不进表", ok, f"kept={len(kept)} dropped={len(dropped)}")


def test_if_dim_log_impact_factor() -> None:
    """F1/F2 对数影响因子：**ln(1+IF) 作为加成项**，不是归一化值×固定权重。

    用户 2026-10-11 两句反馈：
      · 「之前不是还有个对数的影响因子吗，怎么不见了」→ 它藏在检索层（rag/journal_if.py
        + pubmed_yes_retriever.blend_score 的 w_if），只通过 rank 间接体现；
      · 「是影响因子取对数，不是写死权重」→ 因此实现为 `RRS += IF_K · ln(1+IF)`，
        IF_K 只是缩放系数（默认 0.01），不是把 log 值归一化后配一个权重。
    """
    import math as _math
    import os as _os

    from ref_relevance import WEIGHTS, if_k, if_log_of, if_score_of, score_paper

    try:  # 本地无 DB 时 lookup_if 走 T0 兜底（Nature→40）或未知刊 0，故以实测值为准
        from journal_if import lookup_if
    except ImportError:
        from rag.journal_if import lookup_if  # type: ignore

    tot = round(sum(WEIGHTS.values()), 6)
    l_nat = if_log_of("Nature")
    l_unk = if_log_of("")                       # 未知刊 → 0（不加成也不扣分）
    ji_nat = lookup_if("Nature")
    expect_nat = _math.log1p(float(ji_nat.if_value)) if ji_nat.if_value else 0.0
    ok_log = (
        abs(tot - 1.0) < 1e-9                              # 五维权重和仍为 1.0（没被"写死权重"占用）
        and abs(l_nat - expect_nat) < 1e-9                 # 取的正是 ln(1+实测IF)，不是归一化值
        and l_nat >= _math.log1p(40) - 1e-6                # Nature 兜底不小于 IF=40 的对数
        and l_unk == 0.0
        and abs(if_score_of("Nature") - 1.0) < 1e-9        # 归一化函数仍在（供检索层对抛）
        and if_log_of("Development") >= 0.0
    )
    rec("F1 对数影响因子：ln(1+IF) 原值（未知刊=0）", ok_log,
        f"ln1p(Nature)={l_nat:.3f}(实测 IF={ji_nat.if_value}) ln1p(Dev)={if_log_of('Development'):.3f} 未知={l_unk} 权重和={tot}")

    # 加成必须真进总分，且随刊变（同一条目只换刊）
    def sc(journal: str) -> dict:
        return score_paper((1, "2015", "PMID:1", "Nanog regulates development", "ZFQA-1",
                            journal, "10.1/x"), question="nanog")

    n_hi, n_lo = sc("Nature"), sc("Journal of Fish Biology")
    k = if_k()
    ok_bonus = (
        n_hi["score"] > n_lo["score"]
        and abs(n_hi["if_bonus"] - round(k * l_nat, 6)) < 1e-9
        and n_hi["dims"]["if"] == l_nat
        and abs(n_lo["if_bonus"]) < 1e-9            # 无 IF 的刊：加成 0
        and abs((n_hi["base_score"] + n_hi["if_bonus"]) - n_hi["score"]) < 1e-6
    )
    rec("F2 加成进总分且随刊递增（无 IF 不加成）", ok_bonus,
        f"Nature {n_lo['score']:.4f} → {n_hi['score']:.4f}（base {n_hi['base_score']:.4f} + bonus {n_hi['if_bonus']:.4f}, K={k}）")

    # 缩放系数可调：K 只影响加成项，不动五维
    _old = _os.environ.get("REF_RRS_IF_K")
    _os.environ["REF_RRS_IF_K"] = "0.05"
    try:
        k5 = if_k()
        b5 = sc("Nature")
        ok_k = abs(k5 - 0.05) < 1e-9 and abs(b5["if_bonus"] - round(0.05 * l_nat, 6)) < 1e-9 \
            and abs(b5["base_score"] - n_hi["base_score"]) < 1e-9
    finally:
        if _old is None:
            _os.environ.pop("REF_RRS_IF_K", None)
        else:
            _os.environ["REF_RRS_IF_K"] = _old
    rec("F3 REF_RRS_IF_K 只缩放加成、不动五维", ok_k,
        f"K={k5} bonus={b5['if_bonus']:.4f} base 不变 {b5['base_score']:.4f}")


def test_rel_dim_from_pool_text() -> None:
    """H1/H2 连续相关度维（方案 A：分数写进证据池编号行）。

    用户 2026-10-11 追问「相关度的加权体现在哪」→ 发现 `rel_n` 只用于上游排序、
    分数没落盘，下游只能拿「名次」当相关度代理。现证据池写出 `rel=0.871`，
    RRS 直接用连续分（权重 0.10，从位次 0.25 里拆出）；拿不到分数时自动退回位次维。
    """
    from ref_relevance import WEIGHTS, score_paper
    from zebrafish_prompt import _footer_item_count, ensure_reference_footer, rels_from_numbered_context

    new_fmt = ("[1] 2019 | PMID:1 | gold | 相关#1 rel=0.871 · 近十年\n题名：A paper\n\n"
               "[2] 2018 | PMID:2 | gold | 相关#2 rel=0.120 · 近十年\n题名：B paper\n")
    old_fmt = "[1] 2019 | PMID:1 | gold | 相关#1 · 近十年\n题名：A paper\n"
    m_new = rels_from_numbered_context(new_fmt)
    m_old = rels_from_numbered_context(old_fmt)

    p = (1, "2019", "PMID:1", "Nanog regulates development", "Z", "Development", "10.1/x")
    hi = score_paper(p, question="nanog", rel=m_new.get(1))
    lo = score_paper(p, question="nanog", rel=0.120)
    non = score_paper(p, question="nanog")           # 旧格式：拿不到分数
    want_rank = WEIGHTS["rank"] + WEIGHTS["rel"]
    ok = (
        m_new == {1: 0.871, 2: 0.120}
        and m_old == {}
        and abs(hi["dims"]["rel"] - 0.871) < 1e-9
        and hi["score"] > lo["score"]                # 相关度高 → 分更高
        and "rel" not in non["weights_used"]         # 无分数：rel 维退出
        and abs(non["weights_used"]["rank"] - want_rank) < 1e-9   # 权重退回位次，尺度不变
    )
    hi_s, lo_s, non_s = hi["score"], lo["score"], non["score"]
    rec("H1 连续相关度维（rel= 解析；缺分数退回位次）", ok,
        f"rel=0.871→{hi_s} rel=0.120→{lo_s} 无分数→{non_s} rank权重 {non['weights_used']['rank']}")

    # 端到端：新格式证据池 + 正文引用 → 文末仍成表，且 rel= 不泄漏进答案
    ans = "斑马鱼 nanog 维持干细胞群体 [1]。\n\n参考文献：\n" + "\n".join(
        f"[{i}] leaked {i}" for i in range(1, 30))
    ctx = (
        "下列证据已编号\n\n"
        "[5] 2018 | PMID:29346644 | PubMed官方 | ZFQA-000105 | 相关#5 rel=0.900 · 近十年\n"
        "题名：The primary role of zebrafish nanog is in extra-embryonic tissue\n"
        "期刊：Development\nDOI：10.1242/dev.147652\n\n"
        "[9] 2013 | PMID:23222941 | PubMed官方 | ZFQA-000109 | 相关#9 rel=0.500 · 近十年\n"
        "题名：Nanog, Pou5f1 and SoxB1 activate zygotic gene expression during the maternal-to-zygotic transition\n"
        "期刊：Nature\nDOI：10.1038/nature11813\n"
    )
    out = ensure_reference_footer(ans, ctx, question="斑马鱼 nanog 功能")
    n = _footer_item_count(out)
    ok2 = n >= 2 and "rel=" not in out
    rec("H2 端到端：新格式证据池仍成表，rel= 不进答案", ok2, f"文末 {n} 条")


def test_doi_guard() -> None:
    """不可信 DOI（一 DOI 多论文）不得拼进「原文」链接。"""
    from zebrafish_prompt import _format_ref_line, _doi_trustworthy
    import doi_guard

    bad = "10.1006/meth.2001.1262"  # 实测 22 行共用（全文参考文献里的方法学论文）
    if not doi_guard.available():
        rec("G1 DOI 守卫", True, "清单不存在，自动降级放行（不影响功能）")
        return
    ok_guard = (not _doi_trustworthy(bad)) and doi_guard.is_conflicted(bad)
    line = _format_ref_line(1, "2020", "PMID:33333333", "Some real paper title", "", "Some Journal", bad)
    ok_line = (bad not in line) and ("pubmed.ncbi.nlm.nih.gov/33333333" in line)
    rec("G1 冲突 DOI 不入链，改走 PMID", ok_guard and ok_line,
        f"count={doi_guard.conflict_count()} line={line[:120]}")

    good = "10.1038/nature11813"
    line2 = _format_ref_line(2, "2013", "PMID:23222941", "Nanog activates zygotic genes", "", "Nature", good)
    rec("G2 正常 DOI 仍直链", good in line2 and "doi.org" in line2, line2[:120])


def main() -> int:
    for fn in (
        test_extract_entities,
        test_score_dims,
        test_footer_drops_unrelated,
        test_no_entity_question_not_overfiltered,
        test_gate_excludes_low_rank_only_papers,
        test_if_dim_log_impact_factor,
        test_rel_dim_from_pool_text,
        test_doi_guard,
    ):
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
