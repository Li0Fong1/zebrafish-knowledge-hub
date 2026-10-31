# -*- coding: utf-8 -*-
"""
用项目 LLM（ApiLLM）实时监督五篇 PDF 全量对齐质量，并重新检测。

输出：解析文件/pdf_plaintext_samples/LLM监督复检报告.md
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from llm.llm_api import ApiLLM  # noqa: E402
from rag.pdf_fulltext_align import (  # noqa: E402
    CONTENT_THRESHOLD,
    align_pdf,
    extract_text_layer,
    result_to_dict,
)

OUT = ROOT / "解析文件" / "pdf_plaintext_samples"
PDF_DIR = OUT / "原文PDF"
ALIGN_DIR = OUT / "全量对齐"
REPORT = OUT / "LLM监督复检报告.md"

META = {
    "9685": {
        "layout": "springer",
        "title": "Differential toxicities of manganese ferrite nanoparticles from two synthesis methods using developing zebrafish (Danio rerio): towards a biocompatible and safe magnetic nanoparticles",
        "doi": "10.1007/s11051-026-06673-3",
        "pmid": "",
        "source_url": "https://link.springer.com/content/pdf/10.1007/s11051-026-06673-3.pdf",
        "focus": "检查 Introduction 中 MRI as contrast agents ... hyperthermia [7] 是否连贯，中间是否被 Supplementary/作者单位插入",
    },
    "7144": {
        "layout": "nature",
        "title": "NACA deficiency reveals the crucial role of somite-derived stromal cells in haematopoietic niche formation",
        "doi": "10.1038/ncomms9375",
        "pmid": "26411530",
        "source_url": "https://www.nature.com/articles/ncomms9375.pdf",
        "focus": "检查双栏/页眉是否打断正文；章节是否可辨",
    },
    "12686": {
        "layout": "frontiers",
        "title": "Agouti overexpression in a transgenic model regulates integrity, permeability and electrogenic amino acid transport in zebrafish intestine",
        "doi": "10.3389/fmars.2022.1011702",
        "pmid": "",
        "source_url": "https://www.frontiersin.org/articles/10.3389/fmars.2022.1011702/pdf",
        "focus": "检查阅读顺序与章节切分是否合理",
    },
    "12971": {
        "layout": "ijdb",
        "title": "Transient expression of apoaequorin in zebrafish embryos: extending the ability to image calcium transients during later stages of development",
        "doi": "10.1387/ijdb.062151cc",
        "pmid": "16741871",
        "source_url": "https://ijdb.ehu.eus/article/pdf/062151cc",
        "focus": "检查正文连贯性与章节",
    },
    "18472": {
        "layout": "jstage",
        "title": "Synthesis of Novel Class of N-Alkyl-isatin-3-iminobenzoic Acid Derivatives and Their Biological Activity in Zebrafish Embryos and Human Cancer Cell Lines",
        "doi": "10.1248/bpb.b17-00674",
        "pmid": "29249771",
        "source_url": "https://www.jstage.jst.go.jp/article/bpb/41/3/41_b17-00674/_pdf",
        "focus": "检查正文连贯性与章节",
    },
}

SUPERVISE_PROMPT = """你是 PDF→纯文本质量监督员。根据下面证据，对「对齐结果」打分并给结论。

必须检查的 7 项（每项 0-10 分）：
1. content 内容完整（不丢关键科学表述）
2. order 阅读顺序（双栏不乱、侧栏不插进句子中间）
3. layout 排版结构（分页/段落可读）
4. sections 章节可用性（abstract/intro/methods/results/discussion/conclusion/references）
5. noise 噪声处理（页眉页脚/单位是否离开正文阅读流）
6. symbols 符号可读（连字、断词、破折号等）
7. provenance 来源字段是否齐全（doi/pdf/entry）

额外焦点：{focus}

【说明】
- 「PDF文字层抽样」可能含双栏乱序，仅作对照；请以「对齐正文抽样」为主评判 order/noise。
- 页眉页脚若已进入噪声专区而不在对齐正文阅读流中，noise 应给高分。
- 部分期刊原文无独立 Conclusion 标题时，sections 可酌情给分，勿仅因字符为0就整体 fail。

【来源】
entry={entry_no} doi={doi} pdf={pdf_name} pages={pages}

【章节字符数】
{section_sizes}

【PDF文字层抽样】
```
{ref_sample}
```

【对齐正文抽样】
```
{body_sample}
```

【噪声专区抽样】
```
{noise_sample}
```

只输出 JSON（不要 markdown 围栏），格式：
{{
  "pass": true或false,
  "scores": {{"content":0,"order":0,"layout":0,"sections":0,"noise":0,"symbols":0,"provenance":0}},
  "overall": 0到10的整数,
  "issues": ["问题1","问题2"],
  "verdict": "一句话中文结论"
}}
判定：overall>=8 且 content>=8 且 order>=8 才可 pass=true。
"""


def _clip(text: str, n: int = 1800) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n // 2] + "\n...\n" + t[-n // 2 :]


def _find_sample(text: str, needle: str, radius: int = 500) -> str:
    i = (text or "").lower().find(needle.lower())
    if i < 0:
        return _clip(text, 1600)
    a = max(0, i - radius)
    b = min(len(text), i + radius)
    return text[a:b]


def _parse_llm_json(raw: str) -> dict:
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", s)
        if not m:
            return {
                "pass": False,
                "scores": {},
                "overall": 0,
                "issues": ["LLM返回无法解析"],
                "verdict": s[:200],
                "raw": s[:1000],
            }
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return {
                "pass": False,
                "scores": {},
                "overall": 0,
                "issues": ["LLM JSON 解析失败"],
                "verdict": s[:200],
                "raw": s[:1000],
            }


def supervise_one(llm: ApiLLM, entry_no: str, info: dict) -> dict:
    layout = info["layout"]
    pdf = PDF_DIR / f"{entry_no}_{layout}.pdf"
    print(f">>> rule-align [{entry_no}]", flush=True)
    t0 = time.time()
    r = align_pdf(
        pdf,
        entry_no=entry_no,
        layout=layout,
        title=info["title"],
        doi=info.get("doi") or "",
        pmid=info.get("pmid") or "",
        source_url=info.get("source_url") or "",
    )
    print(f"    align {time.time()-t0:.2f}s recall={r.metrics['word_recall']:.4%}", flush=True)

    # 刷新对齐产物
    ALIGN_DIR.mkdir(parents=True, exist_ok=True)
    (ALIGN_DIR / f"{entry_no}_{layout}.json").write_text(
        json.dumps(result_to_dict(r), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ref = extract_text_layer(pdf)
    if entry_no == "9685":
        ref_sample = _find_sample(ref, "magnetic resonance imaging")
        body_sample = _find_sample(r.body_text, "magnetic resonance imaging")
    else:
        ref_sample = _clip(ref, 1600)
        body_sample = _clip(r.body_text, 1600)

    section_sizes = {
        k: (0 if (v in ("", "【无】")) else len(v))
        for k, v in r.sections.items()
        if k != "other"
    }
    noise_sample = _clip(
        (r.noise.get("sidebar_affiliations") or "")
        + "\n"
        + (r.noise.get("running_headers_footers") or ""),
        800,
    )

    prompt = SUPERVISE_PROMPT.format(
        focus=info["focus"],
        word_recall=r.metrics["word_recall"],
        content_ok=r.metrics["content_ok"],
        order_ok=r.metrics["order_ok"],
        section_fill=r.metrics["section_fill_count"],
        entry_no=entry_no,
        doi=r.doi or "【无】",
        pdf_name=pdf.name,
        pages=r.page_count,
        section_sizes=json.dumps(section_sizes, ensure_ascii=False),
        ref_sample=ref_sample,
        body_sample=body_sample,
        noise_sample=noise_sample or "【无】",
    )

    print(f">>> llm-supervise [{entry_no}]", flush=True)
    t1 = time.time()
    raw = llm.chat(
        [
            {
                "role": "system",
                "content": "你是严谨的论文PDF文本质量审核员。只输出合法JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        max_new_tokens=700,
    )
    print(f"    llm {time.time()-t1:.2f}s", flush=True)
    judged = _parse_llm_json(raw)
    judged["entry_no"] = entry_no
    judged["layout"] = layout
    judged["rule"] = {
        "word_recall": r.metrics["word_recall"],
        "content_ok": r.metrics["content_ok"],
        "order_ok": r.metrics["order_ok"],
        "section_fill_count": r.metrics["section_fill_count"],
        "order_checks": r.metrics.get("order_checks"),
    }
    judged["title"] = info["title"]
    return judged


def main() -> None:
    print("init ApiLLM...", flush=True)
    llm = ApiLLM()
    results = []
    for no, info in META.items():
        try:
            results.append(supervise_one(llm, no, info))
        except Exception as e:
            print(f"FAIL [{no}] {e}", flush=True)
            results.append(
                {
                    "entry_no": no,
                    "layout": info["layout"],
                    "pass": False,
                    "scores": {},
                    "overall": 0,
                    "issues": [str(e)],
                    "verdict": f"监督失败: {e}",
                    "title": info["title"],
                    "rule": {},
                }
            )

    lines = [
        "# LLM 实时监督复检报告",
        "",
        f"- 模型：`{llm.model}` @ `{llm.base_url}`",
        f"- 规则阈值：词召回 ≥ {CONTENT_THRESHOLD:.0%}",
        f"- LLM 通过条件：overall≥8 且 content≥8 且 order≥8",
        "",
        "| 条目 | 版式 | 词召回 | 规则内容 | 规则顺序 | LLM overall | LLM pass | 结论 |",
        "|---|---|---:|:---:|:---:|---:|:---:|---|",
    ]
    all_pass = True
    for r in results:
        rule = r.get("rule") or {}
        overall = r.get("overall", 0)
        passed = bool(r.get("pass")) and bool(rule.get("content_ok", False)) and bool(
            rule.get("order_ok", False)
        )
        # 综合：规则与 LLM 都要通过
        if not (r.get("pass") and rule.get("content_ok") and rule.get("order_ok")):
            all_pass = False
            passed = False
        lines.append(
            f"| {r.get('entry_no')} | {r.get('layout')} | "
            f"{float(rule.get('word_recall') or 0):.4%} | "
            f"{'是' if rule.get('content_ok') else '否'} | "
            f"{'是' if rule.get('order_ok') else '否'} | "
            f"{overall} | {'是' if r.get('pass') else '否'} | "
            f"{(r.get('verdict') or '')[:40]} |"
        )

    lines += ["", "## 分篇明细", ""]
    for r in results:
        lines += [
            f"### [{r.get('entry_no')}] {r.get('layout')} — {r.get('title','')[:80]}",
            "",
            f"- **LLM pass**: `{r.get('pass')}` | **overall**: `{r.get('overall')}`",
            f"- **scores**: `{json.dumps(r.get('scores') or {}, ensure_ascii=False)}`",
            f"- **issues**: {r.get('issues') or []}",
            f"- **verdict**: {r.get('verdict')}",
            f"- **rule**: `{json.dumps(r.get('rule') or {}, ensure_ascii=False)}`",
            "",
        ]

    lines += [
        "## 总评",
        "",
        f"**{'全部通过（规则+LLM）' if all_pass else '存在未通过项，见上表'}**",
        "",
    ]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    (OUT / "LLM监督复检结果.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("WROTE", REPORT, flush=True)
    print("ALL_PASS" if all_pass else "HAS_FAIL", flush=True)
    if not all_pass:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
