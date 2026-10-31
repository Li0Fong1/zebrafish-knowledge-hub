# -*- coding: utf-8 -*-
"""Phase A 验收：文献脚注期刊/去重/专用区/条数/无DOI双链。"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "llm", ROOT / "rag", ROOT / "database", ROOT / "agent"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

RESULTS: list[tuple[str, str, str]] = []


def rec(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))
    print(("PASS" if ok else "FAIL"), name, detail[:180], flush=True)


def test_format_always_journal_and_title() -> None:
    from zebrafish_prompt import _format_ref_line

    line = _format_ref_line(1, "2020", "PMID:1", "A study", "", "", "10.1038/s41586-020-0001")
    ok = (
        "A study." in line
        and "Unlisted journal." in line
        and "[原文](" in line
        and "ZFQA" not in line
    )
    rec("A4/7 无刊名时仍强制刊名字串+原文", ok, line)

    line2 = _format_ref_line(2, "2021", "PMID:22222222", "", "", "Nature", "")
    ok2 = (
        "Untitled article." in line2
        and "Nature." in line2
        and "[原文](" in line2
        and "[PubMed](" not in line2
        and "pubmed.ncbi.nlm.nih.gov/22222222" in line2
    )
    rec("A7 无DOI有PMID时仅「原文」链(不写PubMed字样)", ok2, line2)


def test_dedupe_same_paper() -> None:
    from zebrafish_prompt import ensure_reference_footer

    ctx = (
        "下列证据已编号\n"
        "[1] 2020 | PMID:111 | PubMed官方 | ZFQA-000001 | 相关#1\n"
        "题名：Same Paper Title\n"
        "期刊：Cell\n"
        "DOI：10.1016/j.cell.2020.01.001\n"
        "[2] 2020 | PMID:111 | PubMed官方 | ZFQA-000001 | 相关#2\n"
        "题名：Same Paper Title\n"
        "期刊：Cell\n"
        "DOI：10.1016/j.cell.2020.01.001\n"
        "[3] 2021 | PMID:222 | PubMed官方 | ZFQA-000002 | 相关#3\n"
        "题名：Other Paper\n"
        "期刊：Science\n"
        "DOI：10.1126/science.abc123\n"
    )
    ans = "结论见 [1][2][3]。\n\n参考文献：\n[1] junk\n[2] junk\n[3] junk"
    out = ensure_reference_footer(ans, ctx, question="tp53是什么")
    foot = out.split("参考文献")[-1]
    n_same = foot.lower().count("same paper title")
    n_cell_doi = foot.count("10.1016/j.cell.2020.01.001")
    ok = n_same <= 1 and n_cell_doi <= 1 and "Other Paper" in foot and "参考文献" in out
    rec("A5 同PMID/DOI去重", ok, f"same={n_same} doi_hits={n_cell_doi} foot={foot[:240]}")


def test_footer_section_and_counts() -> None:
    from zebrafish_prompt import ensure_reference_footer, _footer_item_count, _ref_footer_min, _ref_footer_max

    lines = ["下列证据已编号"]
    for i in range(1, 40):
        lines.append(
            f"[{i}] 2020 | PMID:{1000+i} | PubMed官方 | ZFQA-{i:06d} | 相关#{i}\n"
            f"题名：Paper {i} title\n"
            f"期刊：Dev Journal {i}\n"
            f"DOI：10.1000/demo.{i}"
        )
    ctx = "\n".join(lines)
    # 机制题：专门参考文献区 + 条数在 min~max
    q = "斑马鱼突变基因的表型机制"
    ans = "机制讨论 [1][5][9]。\n\n参考文献\n" + "\n".join(
        f"[{i}] leaked Paper {i}" for i in range(1, 101)
    )
    out = ensure_reference_footer(ans, ctx, question=q)
    ok_section = out.count("参考文献") == 1 and "：\n[" in out.replace(" ", "")
    # 标题应为「参考文献：」
    ok_heading = bool(re.search(r"(?m)^参考文献：\s*$", out))
    n = _footer_item_count(out)
    lo, hi = _ref_footer_min(q), _ref_footer_max(q)
    ok_n = lo <= n <= hi
    # 正文不应残留整表泄漏 [90]
    body = out.split("参考文献")[0]
    ok_no_leak = "[90]" not in body and "leaked Paper 90" not in out
    # 每条都有期刊
    foot = out.split("参考文献")[-1]
    ref_lines = [ln for ln in foot.splitlines() if re.match(r"^\[\d+\]\s+", ln)]
    ok_j = all(
        ("Journal" in ln) or ("journal" in ln.lower()) or ("Dev Journal" in ln) or ("Unlisted" in ln)
        for ln in ref_lines
    ) and len(ref_lines) == n
    rec(
        "A6 专用区+条数+无整表泄漏+条条有刊名",
        ok_section and ok_heading and ok_n and ok_no_leak and ok_j,
        f"n={n} want={lo}-{hi} heading={ok_heading} leak={not ok_no_leak} lines={len(ref_lines)}",
    )


def test_shell_ref_forbidden() -> None:
    from zebrafish_prompt import _format_ref_line

    line = _format_ref_line(1, "2019", "", "", "", "", "10.1000/xyz")
    # 不得只有年+链接
    ok = "Untitled article." in line and "Unlisted journal." in line and "2019." in line
    rec("A7 禁止空壳脚注(仅年+链)", ok, line)


def test_original_links_helpers() -> None:
    from journal_enrich import original_links

    with_doi = original_links(title="T", journal="J", year="2020", doi="10.1000/abc", pmid="123")
    labels = [x[0] for x in with_doi]
    ok1 = labels == ["原文"] and "PubMed" not in labels and "Google" not in labels
    no_doi = original_links(title="Zebrafish tp53", journal="Cell", year="2020", doi="", pmid="99999")
    labels2 = [x[0] for x in no_doi]
    ok2 = labels2 == ["原文"] and "pubmed.ncbi.nlm.nih.gov/99999" in no_doi[0][1] and "PubMed" not in labels2
    bare = original_links(title="Zebrafish tp53", journal="Cell", year="2020", doi="", pmid="")
    labels3 = [x[0] for x in bare]
    ok3 = "Google" in labels3 and "Scholar" in labels3 and "PubMed" not in labels3
    rec("A7 链接策略 DOI/PMID仅原文·无ID才检索备选", ok1 and ok2 and ok3, f"{labels} | {labels2} | {labels3}")


def test_strip_system_generated_heading() -> None:
    """模型写的「参考文献（由系统生成）：」密集段不得与系统脚注并存。"""
    from zebrafish_prompt import ensure_reference_footer

    lines = ["下列证据已编号"]
    for i in range(1, 8):
        lines.append(
            f"[{i}] 2020 | PMID:{1000 + i} | PubMed官方 | ZFQA-{i:06d} | 相关#{i}\n"
            f"题名：Paper {i} unique title\n"
            f"期刊：Dev Journal {i}\n"
            f"DOI：10.1000/demo.{i}"
        )
    ctx = "\n".join(lines)
    dense = (
        "正文结论见 [1][2][3]。\n\n"
        "参考文献（由系统生成）："
        "[1] Paper 1 unique title, Dev Journal 1, 2020, DOI:10.1000/demo.1; "
        "[2] Paper 2 unique title, Dev Journal 2, 2020, DOI:10.1000/demo.2; "
        "[3] Paper 3 unique title, Dev Journal 3, 2020, DOI:10.1000/demo.3;"
    )
    out = ensure_reference_footer(dense, ctx, question="斑马鱼突变基因的表型机制")
    n_heads = len(re.findall(r"(?m)^参考文献", out))
    ok = (
        n_heads == 1
        and "由系统生成" not in out
        and bool(re.search(r"(?m)^参考文献：\s*$", out))
        and "[1]" in out.split("参考文献")[-1]
    )
    rec("A-dual 剥离「由系统生成」只留一节", ok, f"heads={n_heads} snippet={out[-280:]}")


def test_title_twin_journal_paren_dedupe() -> None:
    """同题名同年：Antioxidants vs Antioxidants (Basel, Switzerland) 只留一条。"""
    from zebrafish_prompt import ensure_reference_footer

    title = (
        "Anti-Inflammatory Pathways Modulated by Microbial Polysaccharides "
        "from Euganean Thermal Muds in Zebrafish"
    )
    ctx = (
        "下列证据已编号\n"
        f"[8] 2025 | PMID:88888881 | PubMed官方 | ZFQA-000008 | 相关#8\n"
        f"题名：{title}\n"
        "期刊：Antioxidants\n"
        "\n"
        f"[10] 2025 | PMID:88888882 | PubMed官方 | ZFQA-000010 | 相关#10\n"
        f"题名：{title}\n"
        "期刊：Antioxidants (Basel, Switzerland)\n"
        "DOI：10.3390/antiox14030268\n"
        "[1] 2024 | PMID:111 | PubMed官方 | ZFQA-000001 | 相关#1\n"
        "题名：Other Distinct Paper About Inflammation\n"
        "期刊：Cell\n"
        "DOI：10.1016/j.cell.2024.01.001\n"
    )
    ans = (
        f"见 [8][10][1]。\n\n参考文献：\n"
        f"[8] {title}. Antioxidants. 2025.\n"
        f"[10] {title}. Antioxidants (Basel, Switzerland). 2025.\n"
        "[1] Other Distinct Paper About Inflammation. Cell. 2024."
    )
    out = ensure_reference_footer(ans, ctx, question="斑马鱼炎症机制")
    foot = out.split("参考文献")[-1]
    n_title = foot.lower().count("anti-inflammatory pathways modulated")
    # 应保留带 DOI / 更完整刊名的那条；另一条消失
    ok = n_title == 1 and "10.3390/antiox14030268" in foot and "Other Distinct Paper" in foot
    rec("A-twin 题名刊名括注去重", ok, f"title_hits={n_title} foot={foot[:360]}")


def main() -> int:
    test_format_always_journal_and_title()
    test_dedupe_same_paper()
    test_footer_section_and_counts()
    test_shell_ref_forbidden()
    test_original_links_helpers()
    test_strip_system_generated_heading()
    test_title_twin_journal_paren_dedupe()
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    print(f"\nSUMMARY pass={len(RESULTS)-len(fails)} fail={len(fails)}")
    for t, s, d in fails:
        print(f"  FAIL {t}: {d}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
