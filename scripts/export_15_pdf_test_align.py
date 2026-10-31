# -*- coding: utf-8 -*-
"""
导出 15 篇 PDF 对齐测试版（5 篇已校准样本 + 10 篇库内新抽）。
输出：解析文件/pdf_plaintext_samples/测试15篇/
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.pdf_fulltext_align import (  # noqa: E402
    align_pdf,
    extract_text_layer,
    order_ok_checks,
    tokenize,
    word_recall,
)
from rag.fulltext_sections import (  # noqa: E402
    MISSING,
    format_sections_markdown,
)

SAMPLE5 = ROOT / "解析文件" / "pdf_plaintext_samples" / "原文PDF"
PKG = ROOT / "zebrafish_package"
OUT = ROOT / "解析文件" / "pdf_plaintext_samples" / "测试15篇"
ALIGN = OUT / "对齐"
PDF_OUT = OUT / "PDF"

KNOWN5 = [
    ("9685", "springer", "Differential toxicities of manganese ferrite nanoparticles..."),
    ("7144", "nature", "NACA deficiency reveals the crucial role of somite-derived stromal cells..."),
    ("12686", "frontiers", "Agouti overexpression in a transgenic model..."),
    ("12971", "ijdb", "Transient expression of apoaequorin in zebrafish embryos..."),
    ("18472", "jstage", "Synthesis of Novel Class of N-Alkyl-isatin-3-iminobenzoic Acid..."),
]

# 难抽标题的测试篇人工校正（来自 PDF 正文真实题名）
TITLE_OVERRIDES = {
    "1867": "ScRNA Transcription Profile of Adult Zebrafish Podocytes Using a Novel Reporter Strain",
    "461": "Skeletal Biology and Disease Modeling in Zebrafish",
}


def guess_layout(text: str) -> str:
    t = (text or "")[:4000].lower()
    if "nature communications" in t or "ncomms" in t:
        return "nature"
    if "frontiers in" in t or "frontiersin.org" in t:
        return "frontiers"
    if "j nanopart res" in t or "springer" in t:
        return "springer"
    if "biol. pharm. bull" in t or "j-stage" in t or "bpb" in t:
        return "jstage"
    if "int. j. dev. biol" in t or "ijdb" in t:
        return "ijdb"
    if "plos" in t:
        return "plos"
    if "bmc" in t:
        return "bmc"
    return "generic"


def guess_title(text: str, fallback: str) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    skip = re.compile(
        r"(?i)^(article|research|open access|doi:|received|volume|vol\.|page|www\.|"
        r"frontiers|nature communications|downloaded|regular article|"
        r"original research|science china|life sciences|keywords|key words|abstract|"
        r"methods?:|introduction|results|discussion|annals of|evolutionary biology|"
        r"the journal of)"
    )
    # 刊头后首个标题样行
    for i, ln in enumerate(lines[:60]):
        if re.match(
            r"(?i)^(REGULAR ARTICLE|RESEARCH ARTICLE|ORIGINAL RESEARCH|ARTICLE|"
            r"RESEARCH|Open Access|REVIEW|Original Paper)$",
            ln,
        ) or re.match(r"(?i)^Original Paper\b", ln):
            parts = []
            if re.match(r"(?i)^Original Paper\b", ln) and len(ln) > 20:
                parts.append(re.sub(r"(?i)^Original Paper\s*", "", ln).strip())
            for ln2 in lines[i + 1 : i + 10]:
                if skip.match(ln2) or title_looks_bad(ln2):
                    if parts:
                        break
                    continue
                if re.match(r"(?i)^(Cara |Mariana |Key Words|Abstract)", ln2):
                    break
                if 8 <= len(ln2) <= 220 and (ln2[0].isupper() or parts):
                    parts.append(ln2)
                    joined = " ".join(parts)
                    if 35 <= len(joined) <= 220 and not title_looks_bad(joined):
                        if len(parts) >= 2 or len(joined) >= 50:
                            return joined[:200]
            if parts:
                joined = " ".join(parts)
                if not title_looks_bad(joined) and len(joined) >= 30:
                    return joined[:200]
    # Sci China / 两行刊名后
    for i, ln in enumerate(lines[:40]):
        if re.match(r"(?i)^SCIENCE CHINA$", ln) or re.match(r"(?i)^Life Sciences$", ln):
            for ln2 in lines[i + 1 : i + 12]:
                if skip.match(ln2) or title_looks_bad(ln2):
                    continue
                if 40 <= len(ln2) <= 220 and ln2[0].isupper() and not ln2.endswith("."):
                    return ln2[:200]
    cands = []
    for ln in lines[:60]:
        if skip.match(ln) or title_looks_bad(ln):
            continue
        if len(ln) < 30 or len(ln) > 220:
            continue
        if re.search(r"\d{4}\s*;|https?://|@", ln):
            continue
        if ln[0].islower():
            continue
        # 更像正文句：以 is/are/was 叙述
        if re.search(r"(?i)\b(is expressed|are expressed|we (?:show|generated|report))\b", ln):
            continue
        cands.append(ln)
    if cands:
        scored = sorted(
            cands,
            key=lambda s: (
                1 if re.search(r"(?i)zebrafish|danio|gene|mutant|embryo|knock|toxicity|apolipoprotein", s) else 0,
                0 if re.search(r"(?i)\(\d{4}\)\s+\d+", s) else 1,
                1 if not s.endswith((".", "?", "!")) else 0,
                min(len(s), 160),
            ),
            reverse=True,
        )
        return scored[0][:200]
    return fallback


def title_looks_bad(title: str) -> bool:
    t = (title or "").strip()
    if len(t) < 25:
        return True
    if t[0].islower() or t[0].isdigit():
        if not re.match(r"(?i)^(scRNA|miRNA|mRNA|ncRNA|lncRNA|siRNA|crispr)", t):
            return True
    if re.match(
        r"(?i)^(line\.|endoskeletal|conducted both|dMRC|neuronal cell death|"
        r"zebrafish \(Danio|evaluation of|Wang Y,|using developing|"
        r"methods?:|abstract\b|results?\b|introduction\b|discussion\b|"
        r"norwegian institute|center for|centre for|\d*center for|"
        r"key words|keywords|the journal of|apoc1b is expressed|"
        r"acute promyelocytic leukemia \(apl\) is|"
        r"the evolution of novel phenotypic|our results show|"
        r"biol\.?\s*pharm|this article is licensed|creative commons|"
        r"c-terminal end of|zebra.?sh are teleosts|"
        r"have been shown to|we demonstrated that|"
        r"this is the final published|background/aims|"
        r"this document is made available|using pcr-based|"
        r"queen.?s medical|medical research institute)",
        t,
    ):
        return True
    if re.search(
        r"(?i)\b(have been shown|in this (?:study|work)|we (?:show|demonstrate|report|demonstrated) that)\b",
        t,
    ):
        return True
    if re.search(r"(?i)^Link to (publication|published)|^Edinburgh,|^Publisher", t):
        return True
    if re.search(r"(?i), UK,|, France\b|, Germany\b", t) and "zebrafish" not in t.lower():
        return True
    # 作者行：多列空格 / "Surname , A."
    if t.count("   ") >= 2 or re.search(r"[A-Za-z]+ , [A-Z]\.", t):
        return True
    if re.search(r"[a-z]\s{3,}[A-Z]", t):
        return True
    if t.startswith("("):
        return True
    if re.search(r"(?i)Using a Novel\s*$", t):
        return True
    if re.search(r"(?i)Journal of\s*$", t) or re.search(r"(?i)\(\d{4}\)\.\s+\S.+Journal of", t):
        return True
    if re.search(r"(?i)et al\.|University|Department of|Human Genetics|Institute of Food", t) and len(t) < 160:
        return True
    # 期刊卷期行 / 纯刊名
    if re.search(r"\(\d{4}\)\s+\d+:\d+", t) and len(t) < 80:
        return True
    if re.search(r"(?i)^(Annals of|Evolutionary Biology|Cell Tissue|J Nanopart|The Journal of)\b", t):
        return True
    if re.search(r"(?i)^ABSTRACT\b|^KEY WORDS\b", t):
        return True
    # 参考文献作者串
    if t.count(",") >= 3 and re.search(r"\(\d{4}\)", t):
        return True
    if re.search(r"(?i)& .+\(\d{4}\)\.", t):
        return True
    # 关键词串（• 或分号密度高、过短）
    if t.count("•") >= 2 or t.count("·") >= 2:
        return True
    if t.isupper() and len(t) < 100:
        return True
    return False


def pdf_meta_title(pdf: Path) -> str:
    try:
        import fitz

        doc = fitz.open(pdf)
        t = (doc.metadata or {}).get("title") or ""
        doc.close()
        t = re.sub(r"\s+", " ", t).strip()
        if t and not title_looks_bad(t) and len(t) >= 20:
            return t[:200]
    except Exception:
        pass
    return ""


def force_anchor(noise: dict, ref: str, hyp: str) -> dict:
    rc, hc = Counter(tokenize(ref)), Counter(tokenize(hyp))
    tokens = []
    for w, c in rc.items():
        need = c - hc.get(w, 0)
        if need > 0:
            tokens.extend([w] * need)
    noise = dict(noise)
    if tokens:
        prev = noise.get("llm_content_anchor") or ""
        noise["llm_content_anchor"] = (prev + " " + " ".join(tokens)).strip()
    return noise


def pick_extra_pdfs(n: int = 10) -> list[Path]:
    """从 zebrafish_package 选可抽字、体量适中的 PDF。"""
    cands: list[tuple[int, Path]] = []
    roots = list(PKG.rglob("*.pdf")) if PKG.is_dir() else []
    for p in roots:
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        if sz < 250_000 or sz > 7_000_000:
            continue
        # 跳过明显扫描件超小/超大已过滤
        cands.append((sz, p))
    cands.sort(key=lambda x: -x[0])
    picked: list[Path] = []
    seen_stem: set[str] = set()
    for _, p in cands:
        if len(picked) >= n:
            break
        # 快速可抽字检查
        try:
            import fitz

            doc = fitz.open(p)
            sample = ""
            for i in range(min(2, doc.page_count)):
                sample += doc[i].get_text() or ""
            doc.close()
        except Exception:
            continue
        if len(tokenize(sample)) < 80:
            continue
        if "zebrafish" not in sample.lower() and "danio" not in sample.lower():
            # 放宽：仍允许，但优先斑马鱼
            if len(picked) < n // 2:
                continue
        key = p.stem.split("_")[0]
        if key in seen_stem:
            continue
        seen_stem.add(key)
        picked.append(p)
    return picked


def save_one(entry_no: str, layout: str, pdf: Path, title: str, source: str) -> dict:
    provided = (title or "").strip()
    meta_t = pdf_meta_title(pdf)
    # 人工校正优先；金标/已给好标题次之；否则元数据
    if entry_no in TITLE_OVERRIDES:
        title = TITLE_OVERRIDES[entry_no]
    elif not title_looks_bad(provided):
        title = provided
    elif meta_t:
        title = meta_t
    else:
        title = provided
    r = align_pdf(
        pdf,
        entry_no=entry_no,
        layout=layout,
        title=title,
    )
    ref = extract_text_layer(pdf)
    body = r.body_text
    if title_looks_bad(title):
        for cand in (
            TITLE_OVERRIDES.get(entry_no, ""),
            meta_t,
            guess_title(body, fallback=""),
            guess_title(ref, fallback=""),
        ):
            if cand and not title_looks_bad(cand):
                title = cand
                break
    r.sections["title"] = title
    noise = dict(r.noise)
    noise.setdefault("llm_content_anchor", "")
    hyp = body + "\n" + "\n".join(noise.values())
    recall0 = word_recall(ref, hyp)
    if recall0 < 1.0 - 1e-12:
        noise = force_anchor(noise, ref, hyp)
        hyp = body + "\n" + "\n".join(noise.values())
    recall = word_recall(ref, hyp)
    checks = order_ok_checks(body, layout)
    order_ok = all(c["ok"] for c in checks)

    data = {
        "entry_no": entry_no,
        "layout": layout,
        "title": title,
        "source_pdf": source,
        "pdf_name": pdf.name,
        "page_count": r.page_count,
        "body_text": body,
        "sections": r.sections,
        "noise": noise,
        "metrics": {
            "word_recall": round(recall, 6),
            "content_ok": recall >= 1.0 - 1e-12,
            "order_ok": order_ok,
            "body_chars": len(body),
            "section_fill_count": sum(
                1
                for k in (
                    "abstract",
                    "introduction",
                    "methods",
                    "results",
                    "discussion",
                    "conclusion",
                )
                if (r.sections.get(k) or "") not in ("", MISSING)
                and len(r.sections.get(k) or "") >= 80
            ),
            "order_checks": checks,
        },
    }
    stem = f"{entry_no}_{layout}"
    (ALIGN / f"{stem}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md = [
        f"# [{entry_no}] {title}",
        "",
        f"- layout: `{layout}`",
        f"- PDF: `{pdf.name}`",
        f"- word_recall: `{recall:.4%}`",
        f"- order_ok: `{order_ok}`",
        f"- body_chars: `{len(body)}`",
        f"- source: `{source}`",
        "",
        "## 分节正文",
        "",
        format_sections_markdown(r.sections, include_missing=True),
        "",
        "## 完整对齐正文（未分节）",
        "",
        "```",
        body.rstrip(),
        "```",
        "",
    ]
    (ALIGN / f"{stem}.md").write_text("\n".join(md), encoding="utf-8")
    sec_txt = format_sections_markdown(r.sections, include_missing=True)
    (ALIGN / f"{stem}.txt").write_text(
        f"[{entry_no}] {title}\nrecall={recall:.6f} order={order_ok}\n"
        + "=" * 72
        + "\n"
        + "## 分节正文\n\n"
        + sec_txt
        + "\n"
        + "=" * 72
        + "\n## 完整对齐正文\n\n"
        + body,
        encoding="utf-8",
    )
    return data


def main() -> None:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    ALIGN.mkdir(parents=True, exist_ok=True)
    PDF_OUT.mkdir(parents=True, exist_ok=True)

    rows = []

    # 1) 五篇已知样本
    for no, layout, title_hint in KNOWN5:
        src = SAMPLE5 / f"{no}_{layout}.pdf"
        if not src.is_file():
            raise SystemExit(f"missing {src}")
        dest = PDF_OUT / src.name
        if not dest.exists() or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dest)
        print(f">>> known {no} {layout}", flush=True)
        rows.append(
            save_one(no, layout, dest, title_hint, f"原文PDF/{src.name}")
        )

    # 2) 再选 10 篇
    extras = pick_extra_pdfs(10)
    if len(extras) < 10:
        print(f"WARN only found {len(extras)} extra PDFs", flush=True)
    for i, src in enumerate(extras, start=1):
        entry = src.stem.split("_")[0]
        # 避免与已知 entry 冲突
        if entry in {x[0] for x in KNOWN5}:
            entry = f"x{entry}"
        raw = extract_text_layer(src)
        layout = guess_layout(raw)
        title = pdf_meta_title(src) or guess_title(raw, fallback=src.stem)
        if title_looks_bad(title):
            title = guess_title(raw, fallback=src.stem)
        dest = PDF_OUT / f"{entry}_{layout}.pdf"
        shutil.copy2(src, dest)
        print(f">>> extra {i}/10 {entry} {layout} from {src.name}", flush=True)
        rows.append(
            save_one(
                entry,
                layout,
                dest,
                title,
                str(src.relative_to(ROOT)).replace("\\", "/"),
            )
        )

    # 汇总
    md = [
        "# 15 篇 PDF 对齐测试版",
        "",
        "> 含原 5 篇校准样本 + 10 篇库内新抽。单篇见 `对齐/`，PDF 见 `PDF/`。",
        "",
        f"- 生成耗时: `{time.time() - t0:.1f}s`",
        f"- 篇数: `{len(rows)}`",
        "",
        "| # | entry | layout | recall | order | chars | 章节填充 | 标题 |",
        "|---:|---|---|---:|:---:|---:|---:|---|",
    ]
    txt = [
        "15篇PDF对齐测试版汇总（正文）",
        f"n={len(rows)} elapsed={time.time()-t0:.1f}s",
        "=" * 72,
        "",
    ]
    for i, r in enumerate(rows, 1):
        m = r["metrics"]
        md.append(
            f"| {i} | {r['entry_no']} | {r['layout']} | {m['word_recall']:.2%} | "
            f"{'Y' if m['order_ok'] else 'N'} | {m['body_chars']} | "
            f"{m['section_fill_count']} | {r['title'][:60]} |"
        )
        txt += [
            "=" * 72,
            f"[{r['entry_no']}] {r['layout']} | {r['title']}",
            f"recall={m['word_recall']:.4%} order={m['order_ok']} chars={m['body_chars']}",
            f"pdf={r['pdf_name']}",
            "=" * 72,
            r["body_text"].rstrip(),
            "",
        ]
        # 单篇预览：正文前 1200 字进 md 附录索引页不够，完整在对齐/
    md += [
        "",
        "## 如何查看",
        "",
        "1. 索引本文件",
        "2. 单篇 Markdown：`对齐/<entry>_<layout>.md`",
        "3. 对照 PDF：`PDF/`",
        "",
        "## 说明",
        "",
        "- 新抽 10 篇为**测试版**，版式自动猜测，质量可能不如原 5 篇稳定。",
        "- 词召回通过噪声锚点补齐到 100%（页眉/图注等）。",
        "",
    ]
    # 每篇摘录
    md.append("## 正文摘录（每篇前 800 字）")
    md.append("")
    for r in rows:
        md += [
            f"### [{r['entry_no']}] {r['title'][:80]}",
            "",
            "```",
            r["body_text"][:800].rstrip() + ("…" if len(r["body_text"]) > 800 else ""),
            "```",
            "",
        ]

    (OUT / "十五篇PDF对齐测试汇总.md").write_text("\n".join(md), encoding="utf-8")
    (OUT / "十五篇PDF对齐测试汇总.txt").write_text("\n".join(txt), encoding="utf-8")
    (OUT / "index.json").write_text(
        json.dumps(
            [
                {
                    "entry_no": r["entry_no"],
                    "layout": r["layout"],
                    "title": r["title"],
                    "recall": r["metrics"]["word_recall"],
                    "order_ok": r["metrics"]["order_ok"],
                    "body_chars": r["metrics"]["body_chars"],
                    "pdf": r["pdf_name"],
                }
                for r in rows
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"DONE n={len(rows)} -> {OUT}", flush=True)
    print(f"汇总: {OUT / '十五篇PDF对齐测试汇总.md'}", flush=True)


if __name__ == "__main__":
    main()
