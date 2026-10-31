# -*- coding: utf-8 -*-
"""
15 篇全量审计（非抽查）：每篇跑完整指标 + 全量缺陷扫描 + 全章节检查。
输出：测试15篇/full_audit_report.md | full_audit_report.json
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.fulltext_sections import MISSING  # noqa: E402
from rag.pdf_fulltext_align import (  # noqa: E402
    extract_text_layer,
    order_ok_checks,
    tokenize,
    word_recall,
)
from scripts.export_15_pdf_test_align import guess_layout, guess_title  # noqa: E402
from scripts.pdf_align_perfection_campaign import (  # noqa: E402
    ASSERTIONS,
    content_body_recall,
    strip_pdf_chrome,
)

TEST = ROOT / "解析文件" / "pdf_plaintext_samples" / "测试15篇"
ALIGN = TEST / "对齐"
PDF = TEST / "PDF"

KNOWN5 = {"9685", "7144", "12686", "12971", "18472"}

SECTION_KEYS = (
    "title",
    "abstract",
    "introduction",
    "methods",
    "results",
    "discussion",
    "conclusion",
    "references",
    "figure_captions",
    "other",
)

# 全量缺陷规则：(name, pattern, flags, scope)
# scope: body | head2500 | sections_any
DEFECT_RULES: list[tuple[str, str, int]] = [
    ("llm_garble", r"LoThiago|assessment\.hermal|biommarker", re.I),
    ("vol_footer", r"Vol[:.]{1,2}\s*\([\d.]+\)", 0),
    ("analy_hyphen_break", r"analy-\s*\n", re.I),
    ("open_access_line", r"(?m)^OPEN ACCESS\s*$", re.I),
    ("edited_by_line", r"(?m)^EDITED BY\s*$", re.I),
    ("orphan_panel_line", r"(?m)^[A-F]\*?\s*$", 0),
    ("control_chars", r"[\x00-\x08\x0b\x0e-\x1f]", 0),
    ("hyphen_before_fig", r"[A-Za-z]{3,}-\n\n(?:Fig|Figure|TABLE)", re.I),
    ("running_cell_tissue", r"Cell Tissue Res \(\d{4}\)", re.I),
    ("running_j_nanopart", r"J Nanopart Res \(\d{4}\)", re.I),
    ("sci_china_running", r"Sci China Life Sci", re.I),
    ("nature_rh", r"NATURE COMMUNICATIONS", re.I),
    ("frontiers_url", r"frontiersin\.org", re.I),
    ("cc_license", r"Creative Commons", re.I),
    ("download_banner", r"Downloaded from", re.I),
    ("supp_banner_inline", r"Electronic supplementary material The online", re.I),
    ("non_electronic_cut", r"non-Electronic|like the non-\s*Electronic", re.I),
    ("pdf_quote_Bxxx", r"\bB[A-Za-z]{3,}\^", 0),
    ("pdf_quote_caret", r"[A-Za-z]\^[A-Za-z]|``|(?<!\d)''(?!\d)", 0),
    ("orphan_page_num_line", r"(?m)^\d{1,3}\s*$", 0),
    ("page_x_of_y", r"(?i)Page\s+\d+\s+of\s+\d+", 0),
    ("provide_fig_cut", r"provide\s*\n\s*Fig\.\s*\d+", re.I),
    ("sig_hyphen_break", r"sig-\s*\n", re.I),
    ("near_dup_40", r"(.{40,90})\1", 0),
]


def count_all(pat: str, text: str, flags: int = 0) -> list[dict]:
    out = []
    for m in re.finditer(pat, text or "", flags):
        snip = re.sub(r"\s+", " ", m.group(0))[:100]
        out.append({"start": m.start(), "snip": snip})
        if len(out) >= 200:  # 单规则上限，避免爆炸；仍远超「抽查」
            break
    return out


def title_quality(title: str) -> list[str]:
    t = (title or "").strip()
    issues: list[str] = []
    if not t or t == MISSING:
        return ["missing"]
    if len(t) < 30:
        issues.append("too_short")
    if re.match(r"(?i)^\d+\s", t):
        issues.append("starts_with_num")
    if t and t[0].islower() and not re.match(r"(?i)^(scRNA|miRNA|mRNA)", t):
        issues.append("starts_lower")
    if re.search(r"(?i)et al\.|University|Department of Biology|Human Genetics Unit", t):
        issues.append("looks_like_meta")
    if re.search(
        r"(?i)^(line\.|endoskeletal|conducted both|dMRC|neuronal cell death|"
        r"zebrafish \(Danio|evaluation of|Wang Y,|using developing)",
        t,
    ):
        issues.append("looks_like_body")
    return issues


def better_title_from_body(body: str) -> str:
    for ln in (body or "").splitlines()[:25]:
        s = ln.strip()
        if len(s) < 35 or len(s) > 220:
            continue
        if re.match(
            r"(?i)^(REGULAR|ARTICLE|RESEARCH|OPEN|Keywords|Abstract|Introduction|"
            r"ORIGINAL|REVIEW|BRIEF)",
            s,
        ):
            continue
        if re.search(r"(?i)et al\.|University|Department|Received|doi:|https?://", s):
            continue
        if s[0].islower():
            continue
        return s
    return ""


def audit_sections(sections: dict) -> dict:
    report = {}
    for k in SECTION_KEYS:
        v = (sections.get(k) or "").strip()
        info: dict = {
            "chars": len(v),
            "missing": (not v) or v == MISSING or v.startswith("【"),
            "issues": [],
        }
        if info["missing"]:
            info["issues"].append("empty_or_missing")
            report[k] = info
            continue
        if k in ("abstract", "introduction", "methods", "results", "discussion") and len(v) < 80:
            info["issues"].append("too_short")
        if re.match(r"(?i)^(OPEN ACCESS|EDITED BY|ARTICLE|Fig(?:ure|\.)\s*\d+)", v):
            info["issues"].append("junk_start")
        if k == "abstract" and len(v) > 120 and v[-1].isalnum():
            # 长摘要仅末尾未收句：不计入硬失败（对齐正文评分）
            if len(v) < 400 or "." not in v:
                info["issues"].append("truncated_mid_word_or_sentence")
        if k == "introduction" and (
            "Electronic supplementary" in v or v.rstrip().endswith("non-")
        ):
            info["issues"].append("cut_by_sidebar")
        if k == "results":
            fig_n = len(re.findall(r"(?i)\bFig(?:ure|\.)\s*\d+", v))
            info["fig_mentions"] = fig_n
            scale_n = v.count("Scale bar")
            if scale_n >= 3 and re.search(r"(?m)^Fig\.\s*\d+", v):
                info["issues"].append("figure_captions_leaked")
        if k == "conclusion" and "Acknowledgments" in v and len(v) < 1500:
            # 可能误切到致谢
            pass
        report[k] = info
    return report


def missing_tokens(ref: str, hyp: str, top_n: int = 40) -> list[dict]:
    rc, hc = Counter(tokenize(ref)), Counter(tokenize(hyp))
    miss = []
    for w, c in rc.items():
        d = c - hc.get(w, 0)
        if d > 0:
            miss.append({"token": w, "missing": d, "ref_count": c})
    miss.sort(key=lambda x: (-x["missing"], -x["ref_count"], x["token"]))
    return miss[:top_n]


def audit_one(meta: dict) -> dict:
    eno = meta["entry_no"]
    layout = meta["layout"]
    data = json.loads((ALIGN / f"{eno}_{layout}.json").read_text(encoding="utf-8"))
    body = data.get("body_text") or ""
    noise = data.get("noise") or {}
    sections = data.get("sections") or {}
    pdf = PDF / meta["pdf"]
    ref = extract_text_layer(pdf)
    noise_joined = "\n".join(str(v) for v in noise.values())
    hyp_all = body + "\n" + noise_joined
    hyp_body = body

    recall_all = word_recall(ref, hyp_all)
    recall_body = word_recall(ref, hyp_body)
    recall_content = content_body_recall(ref, body)
    miss_body = missing_tokens(strip_pdf_chrome(ref), body, top_n=50)
    miss_all = missing_tokens(ref, hyp_all, top_n=20)

    # order：完整列表 + 区分「论文专用」与「通用」
    order_checks = order_ok_checks(body, layout)
    order_generic = order_ok_checks(body, "generic")
    # 论文专用断言：仅 known5 计分；其它 layout 套用记为 n/a
    paper_asserts = []
    for eno2, expect, pat, name in ASSERTIONS:
        if eno2 == "*":
            hit = bool(re.search(pat, body, flags=re.I | re.S))
            ok = hit if expect else (not hit)
            paper_asserts.append(
                {"name": name, "scope": "*", "ok": ok, "expect": expect, "hit": hit}
            )
        elif eno2 == eno:
            hit = bool(re.search(pat, body, flags=re.I | re.S))
            ok = hit if expect else (not hit)
            paper_asserts.append(
                {
                    "name": name,
                    "scope": eno,
                    "ok": ok,
                    "expect": expect,
                    "hit": hit,
                }
            )

    # 全量缺陷扫描
    defects: dict[str, dict] = {}
    for name, pat, flags in DEFECT_RULES:
        hits = count_all(pat, body, flags)
        # email：全文统计
        defects[name] = {"count": len(hits), "samples": hits[:8]}

    emails = count_all(
        r"(?i)[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", body
    )
    defects["email_anywhere"] = {"count": len(emails), "samples": emails[:8]}

    # 页眉类：按「疑似 running header 行」更严统计
    rh_lines = []
    for i, ln in enumerate(body.splitlines()):
        s = ln.strip()
        if not s:
            continue
        if re.search(
            r"(?i)^(Cell Tissue Res|J Nanopart Res|Sci China Life Sci|"
            r"NATURE COMMUNICATIONS|Biol\. Pharm\. Bull|"
            r"Frontiers in .+ \| www\.frontiersin)",
            s,
        ) or re.search(r"(?i)Page\s+\d+\s+of\s+\d+", s):
            rh_lines.append({"line": i + 1, "snip": s[:120]})
    defects["running_header_lines"] = {
        "count": len(rh_lines),
        "samples": rh_lines[:12],
    }

    sec_report = audit_sections(sections)
    # 章节问题：figure_captions 的 junk_start 若内容够长则降级为提示
    for k in list(sec_report.keys()):
        issues = sec_report[k].get("issues") or []
        if k == "figure_captions" and "junk_start" in issues and sec_report[k]["chars"] >= 200:
            sec_report[k]["issues"] = [i for i in issues if i != "junk_start"]
        if k == "results" and "figure_captions_leaked" in issues:
            # 正文 results 提及 Fig. 属正常；仅当大量 Scale bar 才保留
            pass

    title = meta.get("title") or sections.get("title") or ""
    tq = title_quality(title)
    better = better_title_from_body(body)
    guessed_layout = guess_layout(ref)
    guessed_title = guess_title(ref, title)

    anchor = noise.get("llm_content_anchor") or ""
    anchor_tokens = len(tokenize(anchor)) if anchor else 0

    # 结构：章节标题是否出现在 body
    struct = {
        "has_abstract_h": bool(re.search(r"(?im)^Abstract\b|^ABSTRACT\b", body)),
        "has_intro_h": bool(re.search(r"(?im)^Introduction\b", body)),
        "has_methods_h": bool(
            re.search(r"(?im)^(Materials and methods|Methods|Experimental)\b", body)
        ),
        "has_results_h": bool(re.search(r"(?im)^Results\b", body)),
        "has_discussion_h": bool(re.search(r"(?im)^Discussion\b", body)),
        "has_refs_h": bool(re.search(r"(?im)^References\b", body)),
    }

    # soft-hyphen leftovers that look broken (word-\n lowercase)
    soft_hyphen = count_all(r"[A-Za-z]{3,}-\n[a-z]", body)

    # 评分（可读质量，不含假阴性专用检查）
    fail_universal_order = [c for c in order_generic if not c.get("ok")]
    fail_star_asserts = [a for a in paper_asserts if a["scope"] == "*" and not a["ok"]]
    fail_entry_asserts = [
        a for a in paper_asserts if a["scope"] == eno and not a["ok"]
    ]
    sec_fail_n = sum(1 for k, v in sec_report.items() if v.get("issues") and k in (
        "abstract", "introduction", "methods", "results", "discussion", "conclusion", "title"
    ))
    defect_penalty_keys = [
        "llm_garble",
        "vol_footer",
        "orphan_panel_line",
        "hyphen_before_fig",
        "running_header_lines",
        "supp_banner_inline",
        "non_electronic_cut",
        "pdf_quote_Bxxx",
        "provide_fig_cut",
        "sig_hyphen_break",
        "open_access_line",
    ]
    defect_hits = sum(defects[k]["count"] for k in defect_penalty_keys if k in defects)

    score = 100.0
    score -= 15 * len(fail_universal_order)
    score -= 8 * len(fail_star_asserts)
    if eno in KNOWN5:
        score -= 8 * len(fail_entry_asserts)
    score -= min(20, sec_fail_n * 2)
    score -= min(25, defect_hits * 0.5)
    score -= 4 if tq else 0
    score -= max(0.0, (0.98 - recall_content) * 200)
    score -= 2 if anchor_tokens > 800 else (1 if anchor_tokens > 250 else 0)
    score = max(0.0, min(100.0, round(score, 2)))

    return {
        "entry_no": eno,
        "layout": layout,
        "guessed_layout": guessed_layout,
        "is_known5": eno in KNOWN5,
        "title_stored": title[:200],
        "title_issues": tq,
        "title_better_from_body": better[:200],
        "title_guess_fn": (guessed_title or "")[:200],
        "score": score,
        "metrics": {
            "recall_all": round(recall_all, 6),
            "recall_body": round(recall_body, 6),
            "recall_content": round(recall_content, 6),
            "body_chars": len(body),
            "anchor_tokens": anchor_tokens,
            "page_count": data.get("page_count"),
            "noise_keys": sorted(noise.keys()),
        },
        "order_checks_layout": order_checks,
        "order_ok_layout": all(c.get("ok") for c in order_checks),
        "order_checks_generic": order_generic,
        "order_ok_generic": all(c.get("ok") for c in order_generic),
        "paper_asserts": paper_asserts,
        "structure_headings": struct,
        "sections": sec_report,
        "defects": {
            k: {"count": v["count"], "samples": v["samples"]} for k, v in defects.items()
        },
        "soft_hyphen_breaks": {
            "count": len(soft_hyphen),
            "samples": soft_hyphen[:10],
        },
        "missing_tokens_body_vs_chrome_ref": miss_body,
        "missing_tokens_all_vs_ref": miss_all,
        "fail_summary": {
            "universal_order": [c.get("name") for c in fail_universal_order],
            "star_asserts": [a["name"] for a in fail_star_asserts],
            "entry_asserts": [a["name"] for a in fail_entry_asserts],
            "section_keys_with_issues": [
                k for k, v in sec_report.items() if v.get("issues")
            ],
            "defect_nonzero": [
                k for k, v in defects.items() if v["count"] > 0
            ],
        },
    }


def render_md(rows: list[dict]) -> str:
    lines = [
        "# 15 篇 PDF 对齐 — 全量审计报告",
        "",
        "> 本报告对全部 15 篇逐篇全量扫描（全缺陷规则 + 全章节 + 全量 order/断言），非抽查。",
        "",
        f"- 篇数: **{len(rows)}**",
        f"- 全量词召回=100%: **{sum(1 for r in rows if r['metrics']['recall_all'] >= 1.0 - 1e-9)}/{len(rows)}**",
        f"- 通用 order_ok: **{sum(1 for r in rows if r['order_ok_generic'])}/{len(rows)}**",
        f"- layout 专用 order_ok（含假阴性风险）: **{sum(1 for r in rows if r['order_ok_layout'])}/{len(rows)}**",
        f"- 标题有问题: **{sum(1 for r in rows if r['title_issues'])}/{len(rows)}**",
        f"- 平均可读分: **{sum(r['score'] for r in rows)/len(rows):.2f}**",
        "",
        "## 总览表",
        "",
        "| # | entry | known | layout | score | contentR | bodyR | orderG | orderL | titleQ | defects>0 | sec_issues | anch |",
        "|---:|---|:---:|---|---:|---:|---:|:---:|:---:|---|---:|---:|---:|",
    ]
    for i, r in enumerate(rows, 1):
        tq = ",".join(r["title_issues"]) or "ok"
        dnz = len(r["fail_summary"]["defect_nonzero"])
        si = len(r["fail_summary"]["section_keys_with_issues"])
        lines.append(
            f"| {i} | {r['entry_no']} | {'Y' if r['is_known5'] else ''} | {r['layout']} | "
            f"{r['score']} | {r['metrics']['recall_content']:.2%} | {r['metrics']['recall_body']:.2%} | "
            f"{'Y' if r['order_ok_generic'] else 'N'} | {'Y' if r['order_ok_layout'] else 'N'} | "
            f"{tq} | {dnz} | {si} | {r['metrics']['anchor_tokens']} |"
        )

    # 缺陷矩阵
    all_defect_keys = sorted(
        {k for r in rows for k, v in r["defects"].items() if v["count"] > 0}
    )
    lines += ["", "## 缺陷计数矩阵（全量命中次数）", ""]
    if all_defect_keys:
        header = "| entry | " + " | ".join(all_defect_keys) + " |"
        sep = "|---|" + "|".join(["---:"] * len(all_defect_keys)) + "|"
        lines.append(header)
        lines.append(sep)
        for r in rows:
            cells = [
                str(r["defects"].get(k, {}).get("count", 0)) for k in all_defect_keys
            ]
            lines.append(f"| {r['entry_no']} | " + " | ".join(cells) + " |")
    else:
        lines.append("_无缺陷命中_")

    # 逐篇全量明细
    lines += ["", "## 逐篇全量明细", ""]
    for r in rows:
        m = r["metrics"]
        lines.append(f"### [{r['entry_no']}] score={r['score']} layout={r['layout']}")
        lines.append("")
        lines.append(f"- stored_title: `{r['title_stored']}`")
        lines.append(f"- title_issues: `{r['title_issues'] or ['ok']}`")
        lines.append(f"- better_title_from_body: `{r['title_better_from_body']}`")
        lines.append(
            f"- recall_all={m['recall_all']:.6f} recall_body={m['recall_body']:.6f} "
            f"recall_content={m['recall_content']:.6f} chars={m['body_chars']} "
            f"anchor_tokens={m['anchor_tokens']}"
        )
        lines.append(f"- structure: `{r['structure_headings']}`")
        lines.append(
            f"- order_generic: **{'PASS' if r['order_ok_generic'] else 'FAIL'}** "
            f"`{[c for c in r['order_checks_generic'] if not c.get('ok')]}`"
        )
        lines.append(
            f"- order_layout: **{'PASS' if r['order_ok_layout'] else 'FAIL'}** "
            f"`{[c for c in r['order_checks_layout'] if not c.get('ok')]}`"
        )
        bad_a = [a for a in r["paper_asserts"] if not a["ok"]]
        lines.append(f"- asserts_failed: `{bad_a or 'none'}`")

        lines.append("- sections:")
        for k, info in r["sections"].items():
            if info.get("missing") or info.get("issues"):
                lines.append(
                    f"  - **{k}**: chars={info['chars']} issues={info.get('issues')}"
                )
            else:
                lines.append(f"  - {k}: chars={info['chars']} ok")

        lines.append("- defects (count>0):")
        any_d = False
        for k, v in sorted(r["defects"].items(), key=lambda x: -x[1]["count"]):
            if v["count"] <= 0:
                continue
            any_d = True
            samps = []
            for s in v["samples"][:3]:
                if "line" in s and "snip" in s:
                    samps.append(f"L{s['line']}:{s['snip']}")
                elif "snip" in s:
                    samps.append(s["snip"])
                else:
                    samps.append(str(s)[:80])
            lines.append(f"  - **{k}**: {v['count']} — {samps}")
        if not any_d:
            lines.append("  - (none)")

        sh = r["soft_hyphen_breaks"]
        if sh["count"]:
            lines.append(
                f"- soft_hyphen_breaks: {sh['count']} e.g. {[s['snip'] for s in sh['samples'][:3]]}"
            )

        if r["missing_tokens_body_vs_chrome_ref"]:
            top = r["missing_tokens_body_vs_chrome_ref"][:15]
            lines.append(
                "- top missing tokens (body vs chrome-stripped ref): "
                + ", ".join(f"{t['token']}×{t['missing']}" for t in top)
            )
        lines.append("")

    # 改进汇总
    lines += [
        "## 全量汇总：必须改进项",
        "",
    ]
    # aggregate
    title_bad = [r["entry_no"] for r in rows if r["title_issues"]]
    layout_order_false = [
        r["entry_no"]
        for r in rows
        if (not r["order_ok_layout"]) and r["order_ok_generic"] and not r["is_known5"]
    ]
    body_low = [
        (r["entry_no"], r["metrics"]["recall_body"])
        for r in rows
        if r["metrics"]["recall_body"] < 0.97
    ]
    rh = [
        (r["entry_no"], r["defects"]["running_header_lines"]["count"])
        for r in rows
        if r["defects"]["running_header_lines"]["count"] > 0
    ]
    quote = [
        (r["entry_no"], r["defects"]["pdf_quote_Bxxx"]["count"])
        for r in rows
        if r["defects"]["pdf_quote_Bxxx"]["count"] > 0
    ]
    sec_abs = [
        r["entry_no"]
        for r in rows
        if "truncated_mid_word_or_sentence"
        in (r["sections"].get("abstract") or {}).get("issues", [])
    ]

    lines.append(f"1. **标题抽取失败**（{len(title_bad)}/{len(rows)}）: {title_bad}")
    lines.append(
        f"2. **layout 专用 order 假阴性**（通用过、layout 挂）: {layout_order_false}"
    )
    lines.append(f"3. **body_recall < 97%**: {body_low}")
    lines.append(f"4. **running header 行残留**: {rh}")
    lines.append(f"5. **PDF 引号伪影 Bxxx^**: {quote}")
    lines.append(f"6. **abstract 截断**: {sec_abs}")
    lines.append("")
    lines.append("详细机器可读结果见 `full_audit_report.json`。")
    return "\n".join(lines)


def main() -> None:
    idx = json.loads((TEST / "index.json").read_text(encoding="utf-8"))
    rows = [audit_one(m) for m in idx]

    (TEST / "full_audit_report.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = render_md(sorted(rows, key=lambda r: -r["score"]))
    (TEST / "full_audit_report.md").write_text(md, encoding="utf-8")

    print("WROTE", TEST / "full_audit_report.md")
    print("WROTE", TEST / "full_audit_report.json")
    print(
        f"avg_score={sum(r['score'] for r in rows)/len(rows):.2f} "
        f"orderG={sum(1 for r in rows if r['order_ok_generic'])}/15 "
        f"title_bad={sum(1 for r in rows if r['title_issues'])}/15"
    )
    for r in sorted(rows, key=lambda x: x["score"]):
        print(
            f"  [{r['entry_no']}] score={r['score']} contentR={r['metrics']['recall_content']:.3f} "
            f"defects={len(r['fail_summary']['defect_nonzero'])} "
            f"title={r['title_issues'] or ['ok']}"
        )


if __name__ == "__main__":
    main()
