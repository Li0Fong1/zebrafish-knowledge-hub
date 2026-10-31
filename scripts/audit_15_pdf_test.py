# -*- coding: utf-8 -*-
"""审计 测试15篇 对齐质量，输出 audit_report.json / audit_report.md。"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.pdf_fulltext_align import (  # noqa: E402
    extract_text_layer,
    order_ok_checks,
    tokenize,
    word_recall,
)
from scripts.export_15_pdf_test_align import guess_layout  # noqa: E402
from scripts.pdf_align_perfection_campaign import (  # noqa: E402
    ASSERTIONS,
    content_body_recall,
    run_micro_fuzz,
)

TEST = ROOT / "解析文件" / "pdf_plaintext_samples" / "测试15篇"
ALIGN = TEST / "对齐"
PDF = TEST / "PDF"

GEN = [
    (False, r"LoThiago|assessment\.hermal|biommarker", "no_llm_garble"),
    (False, r"Vol[:.]{1,2}\s*\([\d.]+\)", "no_vol_footer"),
    (False, r"analy-\s*\n", "no_analy_break"),
    (False, r"(?m)^OPEN ACCESS\s*$", "no_open_access_line"),
    (False, r"(?m)^EDITED BY\s*$", "no_edited_by"),
    (False, r"(?m)^[A-F]\*?\s*$", "no_orphan_panel_line"),
    (False, r"[\x00-\x08\x0b\x0e-\x1f]", "no_control_chars"),
]


def title_quality(title: str) -> list[str]:
    t = (title or "").strip()
    issues: list[str] = []
    if len(t) < 30:
        issues.append("too_short")
    if re.match(r"(?i)^\d+\s", t):
        issues.append("starts_with_num")
    if re.search(
        r"(?i)^(line\.|endoskeletal|conducted both|dMRC|neuronal cell death|"
        r"zebrafish \(Danio|evaluation of|Wang Y,)",
        t,
    ):
        issues.append("looks_like_body_or_meta")
    if re.search(r"(?i)et al\.|University|Department of", t) and len(t) < 140:
        issues.append("author_affil")
    if t and t[0].islower():
        issues.append("starts_lower")
    return issues


def load_body(data: dict, eno: str, layout: str) -> str:
    body = data.get("body_text") or data.get("body") or ""
    if body:
        return body
    md = ALIGN / f"{eno}_{layout}.md"
    if md.is_file():
        return md.read_text(encoding="utf-8")
    return ""


def main() -> None:
    idx = json.loads((TEST / "index.json").read_text(encoding="utf-8"))
    rows: list[dict] = []

    for m in idx:
        eno = m["entry_no"]
        layout = m["layout"]
        data = json.loads((ALIGN / f"{eno}_{layout}.json").read_text(encoding="utf-8"))
        body = load_body(data, eno, layout)
        noise = data.get("noise") or {}
        sections = data.get("sections") or {}
        pdf = PDF / m["pdf"]
        ref = extract_text_layer(pdf)
        hyp = body + "\n" + "\n".join(str(v) for v in noise.values())
        recall_all = word_recall(ref, hyp)
        recall_body = word_recall(ref, body)
        recall_content = content_body_recall(ref, body)
        checks = order_ok_checks(body, layout)
        fail_checks = [c for c in checks if not c.get("ok")]

        assert_fails: list[str] = []
        for expect, pat, name in GEN:
            hit = bool(re.search(pat, body, flags=re.I | re.S))
            ok = hit if expect else (not hit)
            if not ok:
                assert_fails.append(name)
        for eno2, expect, pat, name in ASSERTIONS:
            if eno2 not in ("*", eno):
                continue
            hit = bool(re.search(pat, body, flags=re.I | re.S))
            ok = hit if expect else (not hit)
            if not ok:
                assert_fails.append(name)

        fuzz = run_micro_fuzz(body, eno)
        fuzz_kinds = Counter(f["kind"] for f in fuzz)

        sec_issues: list[str] = []
        for k in (
            "abstract",
            "introduction",
            "methods",
            "results",
            "discussion",
            "conclusion",
        ):
            v = (sections.get(k) or "").strip()
            if not v or v.startswith("【") or len(v) < 80:
                sec_issues.append(f"{k}:empty/short")
            elif re.match(r"(?i)^(OPEN ACCESS|EDITED BY|ARTICLE|Fig)", v):
                sec_issues.append(f"{k}:junk_start")

        head = body[:2500]
        chrome_hits: list[str] = []
        for pat, name in [
            (r"(?i)frontiersin\.org", "frontiers_url"),
            (r"(?i)Creative Commons", "cc_license"),
            (r"(?i)NATURE COMMUNICATIONS", "nature_rh"),
            (r"(?i)Downloaded from", "download_banner"),
            (r"(?im)^\s*ARTICLE\s*$", "article_label"),
        ]:
            if re.search(pat, head):
                chrome_hits.append(name)

        hyphen_fig = len(
            re.findall(r"[A-Za-z]{3,}-\n\n(?:Fig|Figure|TABLE)", body, re.I)
        )
        anchor = noise.get("llm_content_anchor") or ""
        anchor_tokens = len(tokenize(anchor)) if anchor else 0
        title = m.get("title") or data.get("title") or ""
        tq = title_quality(title)
        guessed = guess_layout(ref)

        # severity score (higher = worse)
        severity = 0
        severity += 0 if all(c.get("ok") for c in checks) else 3
        severity += len(assert_fails) * 2
        severity += min(10, len(fuzz))
        severity += len(sec_issues)
        severity += len(chrome_hits)
        severity += 2 if tq else 0
        severity += 1 if layout != guessed else 0
        severity += 2 if anchor_tokens > 800 else (1 if anchor_tokens > 200 else 0)
        if recall_content < 0.95:
            severity += 3
        elif recall_content < 0.98:
            severity += 1

        rows.append(
            {
                "entry": eno,
                "layout": layout,
                "guessed": guessed,
                "recall_all": round(recall_all, 6),
                "recall_body": round(recall_body, 4),
                "recall_content": round(recall_content, 4),
                "order_ok": all(c.get("ok") for c in checks),
                "fail_checks": [
                    {
                        "name": c.get("name"),
                        "snippet": str(c.get("snippet") or "")[:140],
                    }
                    for c in fail_checks
                ],
                "assert_fails": assert_fails,
                "fuzz": dict(fuzz_kinds),
                "fuzz_n": len(fuzz),
                "sec_issues": sec_issues,
                "chrome_head": chrome_hits,
                "hyphen_fig": hyphen_fig,
                "anchor_tokens": anchor_tokens,
                "title_issues": tq,
                "title": title[:120],
                "body_chars": len(body),
                "severity": severity,
            }
        )

    rows.sort(key=lambda r: -r["severity"])
    (TEST / "audit_report.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # markdown report
    lines = [
        "# 15 篇 PDF 对齐测试审计",
        "",
        f"- 篇数: {len(rows)}",
        f"- 全量词召回=100%: {sum(1 for r in rows if r['recall_all'] >= 1.0 - 1e-9)}/{len(rows)}",
        f"- order_ok: {sum(1 for r in rows if r['order_ok'])}/{len(rows)}",
        f"- 标题可疑: {sum(1 for r in rows if r['title_issues'])}/{len(rows)}",
        f"- 版式不一致(stored≠guess): {sum(1 for r in rows if r['layout'] != r['guessed'])}/{len(rows)}",
        "",
        "## 总表（按严重度降序）",
        "",
        "| sev | entry | layout→guess | contentR | bodyR | order | asserts | fuzz | sec | chrome | anch | titleQ |",
        "|---:|---|---|---:|---:|:---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in rows:
        tq = ",".join(r["title_issues"]) or "ok"
        lg = r["layout"] if r["layout"] == r["guessed"] else f"{r['layout']}→{r['guessed']}"
        lines.append(
            f"| {r['severity']} | {r['entry']} | {lg} | {r['recall_content']:.2%} | "
            f"{r['recall_body']:.2%} | {'Y' if r['order_ok'] else 'N'} | "
            f"{len(r['assert_fails'])} | {r['fuzz_n']} | {len(r['sec_issues'])} | "
            f"{len(r['chrome_head'])} | {r['anchor_tokens']} | {tq} |"
        )

    lines += ["", "## 问题明细", ""]
    for r in rows:
        if r["severity"] == 0:
            continue
        lines.append(f"### [{r['entry']}] {r['title']}")
        lines.append("")
        if not r["order_ok"]:
            for c in r["fail_checks"]:
                lines.append(f"- order fail: **{c['name']}** — `{c['snippet']}`")
        if r["assert_fails"]:
            lines.append(f"- asserts: {', '.join(r['assert_fails'])}")
        if r["fuzz"]:
            lines.append(f"- fuzz: `{r['fuzz']}`")
        if r["sec_issues"]:
            lines.append(f"- sections: {', '.join(r['sec_issues'])}")
        if r["chrome_head"]:
            lines.append(f"- chrome in head: {', '.join(r['chrome_head'])}")
        if r["title_issues"]:
            lines.append(f"- title: {', '.join(r['title_issues'])}")
        if r["layout"] != r["guessed"]:
            lines.append(f"- layout: stored=`{r['layout']}` guess=`{r['guessed']}`")
        if r["anchor_tokens"] > 200:
            lines.append(
                f"- large content anchor: {r['anchor_tokens']} tokens "
                f"(body recall only {r['recall_body']:.2%} — many tokens parked in noise)"
            )
        if r["hyphen_fig"]:
            lines.append(f"- hyphen-before-figure: {r['hyphen_fig']}")
        lines.append("")

    # priority recommendations
    lines += [
        "## 改进优先级建议",
        "",
        "1. **标题抽取**：新抽 10 篇多数标题取自正文/单位行，影响索引与人工审阅。",
        "2. **版式检测**：误标为 springer/nature/frontiers 会触发专用 order 检查失败（假阴性）。",
        "3. **正文召回 vs 锚点**：body_recall 偏低、anchor 很大 = 可读正文仍缺词，靠噪声补齐。",
        "4. **页眉/许可 chrome**：frontiers/nature 文首仍可能残留。",
        "5. **章节切分**：部分 abstract/methods 过短或空。",
        "6. **order 检查泛化**：当前 springer 检查绑定 9685 MRI 句，不宜套到所有 springer。",
        "",
    ]
    (TEST / "audit_report.md").write_text("\n".join(lines), encoding="utf-8")

    print("WROTE", TEST / "audit_report.md")
    print(
        f"order_ok={sum(1 for r in rows if r['order_ok'])}/{len(rows)} "
        f"title_bad={sum(1 for r in rows if r['title_issues'])} "
        f"layout_mismatch={sum(1 for r in rows if r['layout'] != r['guessed'])}"
    )
    for r in rows[:8]:
        print(
            f"  sev={r['severity']} [{r['entry']}] contentR={r['recall_content']:.3f} "
            f"bodyR={r['recall_body']:.3f} order={r['order_ok']} "
            f"anch={r['anchor_tokens']} titleQ={r['title_issues'] or ['ok']}"
        )


if __name__ == "__main__":
    main()
