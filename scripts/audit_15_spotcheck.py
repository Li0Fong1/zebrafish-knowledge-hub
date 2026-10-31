# -*- coding: utf-8 -*-
"""补充抽查：真实正文缺陷（非假阳性 order 检查）。"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST = ROOT / "解析文件" / "pdf_plaintext_samples" / "测试15篇"
ALIGN = TEST / "对齐"


def main() -> None:
    idx = json.loads((TEST / "index.json").read_text(encoding="utf-8"))
    lines = ["# 补充抽查：真实可读性问题", ""]
    for m in idx:
        eno, layout = m["entry_no"], m["layout"]
        data = json.loads((ALIGN / f"{eno}_{layout}.json").read_text(encoding="utf-8"))
        body = data.get("body_text") or ""
        secs = data.get("sections") or {}
        hits: list[str] = []

        if re.search(r"(?i)Cell Tissue Res \(\d{4}\)", body):
            hits.append("running_footer_in_body")
        if re.search(r"(?i)Sci China Life Sci", body) and "References" not in body[:500]:
            # may be citation; only flag if looks like header
            if re.search(r"(?im)^.*Sci China Life Sci.*$", body):
                hits.append("sci_china_line")
        if re.search(r"\bB[A-Za-z]{3,}\^", body):
            hits.append("pdf_quote_artifact_Bxxx^")
        if "non-Electronic" in body or re.search(
            r"like the non-\s*\n?Electronic", body
        ):
            hits.append("intro_cut_by_sidebar")
        if re.search(r"(?i)Electronic supplementary material The online", body):
            hits.append("supp_banner_inline")
        if "@" in body[:3500] and re.search(
            r"(?i)[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", body[:3500]
        ):
            hits.append("email_in_head")
        if re.search(r"(?im)^\d{2,3}\s*$", body):
            hits.append("orphan_page_numbers")

        res = secs.get("results") or ""
        if res.count("Fig.") >= 3 and "Results" not in res[:40]:
            hits.append("many_figs_inside_results_field")
        abs_ = (secs.get("abstract") or "").rstrip()
        if abs_ and len(abs_) > 120 and abs_[-1].isalnum():
            hits.append("abstract_truncated")
        intro = secs.get("introduction") or ""
        if "Electronic supplementary" in intro or intro.rstrip().endswith("non-"):
            hits.append("intro_section_cut")

        # better title candidate from body
        title_real = ""
        for ln in body.splitlines()[:20]:
            s = ln.strip()
            if len(s) < 35 or len(s) > 200:
                continue
            if re.match(
                r"(?i)^(REGULAR|ARTICLE|RESEARCH|OPEN|Keywords|Abstract|Introduction)",
                s,
            ):
                continue
            if re.search(r"(?i)et al\.|University|Department|Received|doi:", s):
                continue
            if s[0].islower():
                continue
            title_real = s
            break

        lines.append(f"## [{eno}] {layout}")
        lines.append(f"- stored_title: `{m.get('title','')[:100]}`")
        lines.append(f"- better_title_guess: `{title_real[:100]}`")
        lines.append(f"- real_issues: {hits or ['(none flagged)']}")
        if res:
            lines.append(f"- results_head: `{res[:90].replace(chr(10), ' ')}`")
        lines.append("")

    out = TEST / "audit_spotcheck.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("WROTE", out)
    for block in lines:
        if block.startswith("## ") or block.startswith("- real_issues"):
            print(block)


if __name__ == "__main__":
    main()
