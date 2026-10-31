# -*- coding: utf-8 -*-
"""找出五篇对齐文本相对原 PDF 的：缺失 / 截断 / 顺序问题。"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.entry import bootstrap_app

bootstrap_app()

from rag.pdf_fulltext_align import (  # noqa: E402
    extract_text_layer,
    normalize_symbols,
    tokenize,
)

OUT = ROOT / "解析文件" / "pdf_plaintext_samples"
PDF_DIR = OUT / "原文PDF"
ALIGN = OUT / "全量对齐"
REPORT = OUT / "缺失截断顺序问题清单.md"
JSON_OUT = OUT / "缺失截断顺序问题清单.json"

TARGETS = [
    "9685_springer",
    "7144_nature",
    "12686_frontiers",
    "12971_ijdb",
    "18472_jstage",
]


def hyp_all(data: dict) -> str:
    noise = data.get("noise") or {}
    return "\n".join(
        [
            data.get("body_text") or "",
            noise.get("sidebar_affiliations") or "",
            noise.get("running_headers_footers") or "",
            noise.get("figure_panel_labels") or "",
            noise.get("llm_content_anchor") or "",
        ]
    )


def missing_with_context(ref: str, hyp: str, top: int = 30) -> list[dict]:
    rc, hc = Counter(tokenize(ref)), Counter(tokenize(hyp))
    miss = [(c - hc.get(w, 0), w) for w, c in rc.items() if c > hc.get(w, 0)]
    miss.sort(reverse=True)
    out = []
    low = ref.lower()
    for cnt, w in miss[:top]:
        # find up to cnt contexts
        contexts = []
        start = 0
        for _ in range(min(cnt, 3)):
            i = low.find(w, start)
            if i < 0:
                break
            contexts.append(re.sub(r"\s+", " ", ref[max(0, i - 45) : i + len(w) + 45]))
            start = i + len(w)
        out.append({"word": w, "missing_count": cnt, "pdf_contexts": contexts})
    return out


def find_truncated_sentences(body: str) -> list[dict]:
    """正文中疑似不完整的句子/段落结尾。"""
    issues = []
    # 以介词/连词/冠词结尾的段落
    for m in re.finditer(
        r"(?m)^(.{20,200}?)\b(the|a|an|of|to|in|on|for|and|or|with|by|from|as|at|into|than|that|which|this|these|those)\s*$",
        body,
        flags=re.I,
    ):
        line = m.group(0).strip()
        if len(line) < 25:
            continue
        # 下一非空行若像新标题/图注则更可疑
        after = body[m.end() : m.end() + 120]
        nxt = ""
        for ln in after.splitlines():
            if ln.strip():
                nxt = ln.strip()
                break
        if re.match(r"^(Fig\.|FIGURE|Table|References|ABSTRACT|INTRODUCTION|\d+\s)", nxt, re.I):
            issues.append(
                {
                    "type": "截断疑似",
                    "snippet": re.sub(r"\s+", " ", line)[-120:],
                    "next_line": nxt[:80],
                }
            )
    # 以连字符结尾（未完成断词）
    for m in re.finditer(r"(?m).{15,80}-\s*$", body):
        issues.append(
            {
                "type": "断词未完成",
                "snippet": re.sub(r"\s+", " ", m.group(0).strip())[-100:],
                "next_line": "",
            }
        )
    # provide/used to 后直接 Fig
    for m in re.finditer(
        r"(?is).{0,80}(provide|used to|shown in|see)\s*(?:\n)+(Fig\.|FIGURE|Table)\b.{0,40}",
        body,
    ):
        issues.append(
            {
                "type": "句意被图打断/后文缺失",
                "snippet": re.sub(r"\s+", " ", m.group(0)),
                "next_line": "",
            }
        )
    # 去重
    seen = set()
    uniq = []
    for it in issues:
        key = (it["type"], it["snippet"][:60])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq[:25]


def find_order_issues(body: str, layout: str, ref: str) -> list[dict]:
    issues = []
    # 侧栏插入
    if re.search(r"(?is)Supplementary Information.{0,100}(hyperthermia|contrast agents)", body):
        issues.append({"type": "顺序", "detail": "Supplementary 插入科学句中", "snippet": ""})
    # 面板字母
    n = len(re.findall(r"(?m)^[A-F]\*?\s*$", body))
    if n:
        issues.append({"type": "顺序", "detail": f"孤立面板字母行 x{n}", "snippet": ""})
    glued = re.findall(r"[a-z]{4,}[A-F](?:\s|$)", body)
    if glued:
        issues.append({"type": "顺序", "detail": "词尾粘连面板字母", "snippet": ", ".join(glued[:8])})
    # running head
    for pat, name in [
        (r"(?i)frontiersin\.org", "Frontiers 页脚进正文"),
        (r"(?i)NATURE COMMUNICATIONS\s*\|\s*DOI", "Nature running head 进正文"),
        (r"(?im)^Vol\.:?\s*\([\d.]+\)\s*$", "Springer Vol 页脚进正文"),
    ]:
        if re.search(pat, body):
            issues.append({"type": "顺序", "detail": name, "snippet": ""})
    # Springer MRI：对齐应连贯；原 PDF 层应乱序——对比说明
    if layout == "springer":
        ok = bool(
            re.search(
                r"magnetic resonance imaging \(MRI\).{0,160}hyperthermia \[7\]",
                body,
                re.S,
            )
        )
        ref_broken = bool(
            re.search(
                r"magnetic resonance imaging \(MRI\).{0,200}Supplementary Information",
                ref,
                re.S,
            )
        )
        if not ok:
            issues.append({"type": "顺序", "detail": "MRI→hyperthermia 未连贯", "snippet": ""})
        else:
            issues.append(
                {
                    "type": "顺序说明",
                    "detail": "MRI句在对齐正文中已连贯"
                    + ("；原PDF文字层存在侧栏打断（对齐已纠正）" if ref_broken else ""),
                    "snippet": "",
                }
            )
    # 正文中 Methods 出现在 Introduction 句子中间的粗检：小写续写前突然全大写标题
    for m in re.finditer(
        r"(?m)([a-z][^\n]{30,120})\n(MATERIALS AND METHODS|Results|RESULTS|DISCUSSION)\n([a-z].{20,80})",
        body,
    ):
        issues.append(
            {
                "type": "顺序可疑",
                "detail": "标题插入小写续写之间",
                "snippet": re.sub(r"\s+", " ", m.group(0))[:160],
            }
        )
    return issues


def compare_endings(ref: str, body: str) -> list[dict]:
    """对比文末是否明显短一截。"""
    issues = []
    # 取原版最后 400 字母数字词，看对齐是否覆盖
    rw = tokenize(ref)
    hw = set(tokenize(body))
    tail = rw[-80:] if len(rw) >= 80 else rw
    missing_tail = [w for w in tail if w not in hw]
    if len(missing_tail) >= 8:
        issues.append(
            {
                "type": "文末可能不完整",
                "detail": f"原版文末约80词中有{len(missing_tail)}词未在正文出现（可能在噪声区或真缺）",
                "snippet": " ".join(missing_tail[:20]),
            }
        )
    # 正文最后一句
    last = re.sub(r"\s+", " ", body.strip()[-240:])
    if re.search(r"\b(the|of|and|to|in|a)\s*$", last, re.I) or last.endswith("-"):
        issues.append({"type": "文末截断疑似", "detail": "正文结尾不像完整句", "snippet": last})
    return issues


def main() -> None:
    all_rows = []
    md = [
        "# 五篇对齐文本 vs 原 PDF：缺失 / 截断 / 顺序问题清单",
        "",
        "> 对照对象：原 PDF **嵌入文字层** + 对齐正文（`全量对齐/*.json`）。",
        "> 「缺词」若仅出现在页眉/面板并已进噪声专区，会在归类中标明。",
        "",
    ]

    for name in TARGETS:
        no, layout = name.split("_", 1)
        print(f">>> {name}", flush=True)
        data = json.loads((ALIGN / f"{name}.json").read_text(encoding="utf-8"))
        ref = extract_text_layer(PDF_DIR / f"{name}.pdf")
        body = data.get("body_text") or ""
        hyp = hyp_all(data)
        noise = data.get("noise") or {}

        miss_all = missing_with_context(ref, hyp, top=25)
        miss_body = missing_with_context(ref, body, top=40)
        # 正文缺但总 hyp 不缺 → 在噪声区
        hyp_words = Counter(tokenize(hyp))
        body_only_missing = []
        for item in miss_body:
            w = item["word"]
            if hyp_words[w] >= Counter(tokenize(ref))[w]:
                body_only_missing.append({**item, "where": "已在噪声专区，正文阅读流中不可见"})
            else:
                body_only_missing.append({**item, "where": "全文（含噪声）仍缺 → 真缺失"})

        true_missing = [x for x in miss_all if x["missing_count"] > 0]
        trunc = find_truncated_sentences(body)
        order = find_order_issues(body, layout, ref)
        endings = compare_endings(ref, body)

        # 从噪声专区抽样说明
        noise_keys = {k: (v[:200] + ("…" if len(v) > 200 else "")) for k, v in noise.items() if (v or "").strip()}

        row = {
            "entry_no": no,
            "layout": layout,
            "title": data.get("title"),
            "true_missing_words": true_missing,
            "body_invisible_but_in_noise": [x for x in body_only_missing if "噪声" in x.get("where", "")][:15],
            "truncation_suspects": trunc,
            "order_issues": order,
            "ending_issues": endings,
            "noise_zones_present": list(noise_keys.keys()),
        }
        all_rows.append(row)

        md += [
            f"## [{no}] {layout}",
            "",
            f"- 标题：{data.get('title')}",
            f"- 全文词召回（含噪声专区）：缺词条目数 **{len(true_missing)}**",
            "",
            "### 1) 相对原 PDF 仍缺失的词（真缺失）",
            "",
        ]
        if not true_missing:
            md.append("无（含噪声专区后词召回完整）。")
        else:
            md.append("| 缺次 | 词 | 原 PDF 上下文 |")
            md.append("|---:|---|---|")
            for it in true_missing[:20]:
                ctx = (it["pdf_contexts"][0] if it["pdf_contexts"] else "").replace("|", "\\|")
                md.append(f"| {it['missing_count']} | `{it['word']}` | {ctx} |")

        md += ["", "### 2) 正文里看不到、但在噪声专区里的内容（阅读流“像缺失”）", ""]
        noise_miss = [x for x in body_only_missing if "噪声" in x.get("where", "")]
        if not noise_miss:
            md.append("无特别项，或正文已覆盖主要词。")
        else:
            md.append("| 词 | 说明 | 原 PDF 上下文 |")
            md.append("|---|---|---|")
            for it in noise_miss[:12]:
                ctx = (it["pdf_contexts"][0] if it["pdf_contexts"] else "").replace("|", "\\|")
                md.append(f"| `{it['word']}` | {it['where']} | {ctx} |")
        md.append(f"\n噪声专区字段：`{', '.join(noise_keys.keys()) or '无'}`\n")

        md += ["### 3) 后文不完整 / 截断疑似", ""]
        items = trunc + endings
        if not items:
            md.append("未发现明显截断模式。")
        else:
            for it in items[:15]:
                md.append(f"- **{it['type']}**：`{it.get('snippet','')}`")
                if it.get("next_line"):
                    md.append(f"  - 下一行：`{it['next_line']}`")
                if it.get("detail"):
                    md.append(f"  - {it['detail']}")

        md += ["", "### 4) 顺序有误 / 顺序说明", ""]
        if not order:
            md.append("未发现顺序问题。")
        else:
            for it in order:
                md.append(f"- **{it['type']}**：{it['detail']}")
                if it.get("snippet"):
                    md.append(f"  - `{it['snippet']}`")
        md.append("")

    # 总表
    summary = [
        "## 总览",
        "",
        "| 条目 | 真缺词条数 | 截断疑似 | 顺序问题 |",
        "|---|---:|---:|---:|",
    ]
    for r in all_rows:
        n_order = sum(1 for x in r["order_issues"] if x["type"] in ("顺序", "顺序可疑"))
        summary.append(
            f"| {r['entry_no']} | {len(r['true_missing_words'])} | "
            f"{len(r['truncation_suspects'])+len(r['ending_issues'])} | {n_order} |"
        )
    summary.append("")
    md = md[:5] + summary + md[5:]

    REPORT.write_text("\n".join(md), encoding="utf-8")
    JSON_OUT.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", REPORT, flush=True)


if __name__ == "__main__":
    main()
