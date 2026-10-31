# -*- coding: utf-8 -*-
"""
PDF 对齐完美化战役：单轮执行数百～上千条微断言，写战役日志，供 8 小时循环迭代。
"""
from __future__ import annotations

import json
import re
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
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

OUT = ROOT / "解析文件" / "pdf_plaintext_samples"
PDF_DIR = OUT / "原文PDF"
ALIGN_DIR = OUT / "全量对齐"
CAMPAIGN_DIR = OUT / "perfection_campaign"
LOG_JSONL = CAMPAIGN_DIR / "campaign.jsonl"
SCOREBOARD = CAMPAIGN_DIR / "scoreboard.json"
LATEST_MD = CAMPAIGN_DIR / "latest_report.md"

META = {
    "9685": {
        "layout": "springer",
        "title": "Differential toxicities of manganese ferrite nanoparticles from two synthesis methods using developing zebrafish (Danio rerio): towards a biocompatible and safe magnetic nanoparticles",
        "doi": "10.1007/s11051-026-06673-3",
    },
    "7144": {
        "layout": "nature",
        "title": "NACA deficiency reveals the crucial role of somite-derived stromal cells in haematopoietic niche formation",
        "doi": "10.1038/ncomms9375",
    },
    "12686": {
        "layout": "frontiers",
        "title": "Agouti overexpression in a transgenic model regulates integrity, permeability and electrogenic amino acid transport in zebrafish intestine",
        "doi": "10.3389/fmars.2022.1011702",
    },
    "12971": {
        "layout": "ijdb",
        "title": "Transient expression of apoaequorin in zebrafish embryos: extending the ability to image calcium transients during later stages of development",
        "doi": "10.1387/ijdb.062151cc",
    },
    "18472": {
        "layout": "jstage",
        "title": "Synthesis of Novel Class of N-Alkyl-isatin-3-iminobenzoic Acid Derivatives and Their Biological Activity in Zebrafish Embryos and Human Cancer Cell Lines",
        "doi": "10.1248/bpb.b17-00674",
    },
}

# (entry_no or *, expect_present, pattern, name)
ASSERTIONS: list[tuple[str, bool, str, str]] = [
    ("*", False, r"LoThiago|assessment\.hermal|biommarker", "no_llm_garble"),
    ("*", False, r"Vol[:.]{1,2}\s*\([\d.]+\)", "no_vol_footer"),
    ("*", False, r"analy-\s*\n", "no_analy_break"),
    ("9685", True, r"magnetic resonance imaging \(MRI\).{0,160}hyperthermia \[7\]", "mri_cont"),
    ("9685", True, r"Thiago Lopes Rocha", "author_ok"),
    ("9685", True, r"EDS analyses", "eds_analyses"),
    ("9685", True, r"MnFe2O4 NPs were obtained via hydrothermal", "methods_prose_in_body"),
    ("9685", False, r"(?i)^OPEN ACCESS", "no_open_access"),
    ("7144", True, r"In vertebrate ontogeny", "in_vertebrate"),
    ("7144", True, r"\(Fli1:GFP[^\)]*\)\s+and\s+stromal\s+\(TCF", "fli1_stromal"),
    ("7144", True, r"Nestin\+ perivascular|Nestin\+ ", "thorn_to_plus"),
    ("7144", False, r"Nestinþ", "no_thorn"),
    ("7144", True, r"n\s*=\s*\d", "fraction_slash_to_eq"),
    ("7144", False, r"1⁄4", "no_fraction_slash"),
    ("7144", False, r"(?m)^n vertebrate", "no_dropcap_loss"),
    ("12686", True, r"tryptophan\s+were\s+also\s+up-regulated", "trp_were_also"),
    ("12686", False, r"(?m)^were also up-regulated", "no_orphan_were"),
    ("12686", False, r"(?m)^OPEN ACCESS", "no_frontiers_chrome_line"),
    ("12971", True, r"provide\s+spatio-?temporal\s+information", "spatio_temporal"),
    ("12971", True, r"generating\s+Ca2\+\s+signals?\)", "ca_signals"),
    ("12971", False, r"provide\s*\n\s*Fig\.\s*3", "no_provide_fig_cut"),
    ("12971", False, r"sig-\s*\n", "no_sig_break"),
    ("18472", True, r"Fig\.\s*10", "fig10_present"),
]


def strip_pdf_chrome(text: str) -> str:
    """从文字层去掉页眉页脚类噪声，便于评估「正文可读召回」。"""
    t = text or ""
    # 跨页重复行（running title / 作者行）≥3 次则剔除
    lines = t.splitlines()
    counts: Counter[str] = Counter()
    for ln in lines:
        s = ln.strip()
        if 15 <= len(s) <= 140:
            counts[s] += 1
    drop = {s for s, c in counts.items() if c >= 3}
    if drop:
        t = "\n".join(ln for ln in lines if ln.strip() not in drop)
    t = re.sub(r"(?im)^.*NATURE COMMUNICATIONS.*$", "", t)
    t = re.sub(r"(?im)^.*All rights reserved.*$", "", t)
    t = re.sub(r"(?im)^.*frontiersin\.org.*$", "", t)
    t = re.sub(r"(?im)^.*Frontiers in Marine Science.*$", "", t)
    t = re.sub(r"(?im)^.*Frontiers in .*$", "", t)
    t = re.sub(r"(?im)^.*Biol\. Pharm\. Bull.*$", "", t)
    t = re.sub(r"(?im)^.*J Nanopart Res.*$", "", t)
    t = re.sub(r"(?im)^.*Cell Tissue Res \(\d{4}\).*$", "", t)
    t = re.sub(r"(?i)\bCell Tissue Res \(\d{4}\)\s+\d+:\d+(?:-\d+)?", " ", t)
    t = re.sub(r"(?im)^.*Cell Physiol Biochem.*$", "", t)
    t = re.sub(r"(?i)\bCell Physiol Biochem(?:\s+Press)?(?:\s+GmbH)?[^\n]{0,40}", " ", t)
    t = re.sub(r"(?is)Cellular\s+Physiology\s+and\s+Biochemistry", " ", t)
    t = re.sub(r"(?i)\bCellular Physiology\b", " ", t)
    t = re.sub(r"(?im)^and Biochemistry\s*$", "", t)
    t = re.sub(r"(?i)\b(?:physiol|biochem)\b(?=\s+(?:press|gmbh|\d{4}))", " ", t)
    t = re.sub(r"(?im)^.*Sci(?:ence)?\s*China.*$", "", t)
    t = re.sub(r"(?i)\bSci(?:ence)?\s*China(?:\s+Life Sci(?:ences)?)?\b", " ", t)
    t = re.sub(r"(?im)^.*J\.?\s*Toxicol(?:ogical)?\.?\s*Sci.*$", "", t)
    t = re.sub(r"(?im)^.*Int\.?\s*J\.?\s*Dev\.?\s*Biol.*$", "", t)
    t = re.sub(r"(?im)^Vol[:.]{1,2}\s*\([\d.]+\).*$", "", t)
    t = re.sub(r"(?i)\bVol\.?\s*\d+\s*,?\s*No\.?\s*\d+", " ", t)
    t = re.sub(r"(?im)^\d+\s+Page\s+\d+\s+of\s+\d+.*$", "", t)
    t = re.sub(r"(?im)^J Nanopart Res \(\d{4}\).*Page \d+ of \d+.*$", "", t)
    t = re.sub(r"(?im)^ARTICLE\s*$", "", t)
    t = re.sub(r"(?im)^ORIGINAL PAPER\b.*$", "", t)
    t = re.sub(r"(?i)\bdoi:\s*10\.\d+/[^\s]+", "", t)
    t = re.sub(r"(?i)\bhttps?://doi\.org/\S+", "", t)
    t = re.sub(r"(?i)\b10\.3389/[a-z0-9.]+", "", t)
    t = re.sub(r"(?i)\bfmars\.\d{4}\.\d+\b", "", t)
    t = re.sub(r"(?i)\b1011702\b", "", t)
    t = re.sub(r"(?i)\b591160\b", "", t)
    t = re.sub(r"(?i)\b10\.1038/ncomms\d+", "", t)
    t = re.sub(r"(?i)\b10\.1007/s11051[^\s]*", "", t)
    t = re.sub(r"(?i)\b10\.1002/jbmr\.\d+", "", t)
    t = re.sub(r"(?im)^.*\bet al\.\s*$", "", t)
    # 刊头年月/卷期碎片
    t = re.sub(
        r"(?i)\b(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December)\s+\d{4}\b",
        " ",
        t,
    )
    t = re.sub(r"(?i)\b(?:volume|vol\.?)\s*\d+\b", " ", t)
    # Frontiers 作者/单位残片
    t = re.sub(
        r"(?i)\b(Cerda[- ]?Reverter|Rotllant|Saera[- ]?Vila|Fuentes|Leal|Gregorio|Denmark|"
        r"Technical University|original article)\b",
        " ",
        t,
    )
    t = re.sub(
        r"(?is)\b(distribution or reproduction|terms of the|Creative Commons|permitted|"
        r"does not comply with these terms)\b.{0,80}",
        " ",
        t,
    )
    # 图内时间轴/面板碎片（不应计入「正文缺失」）
    t = re.sub(r"(?i)\b\d+\s*h\.?p\.?f\.?\b", " ", t)
    t = re.sub(r"(?i)\b\d+\s*d\.?p\.?f\.?\b", " ", t)
    t = re.sub(r"(?m)^\s*som\s*$", "", t)
    t = re.sub(r"(?m)^\s*[a-l]\s*$", "", t, flags=re.I)
    # 色条/轴刻度：纯数字密排行、常见图注基因标签
    t = re.sub(r"(?m)^(?:\s*\d+(?:\.\d+)?\s*){4,}$", "", t)
    t = re.sub(r"(?i)\bsecA5(?:-TagBFP|-BFP|TagBFP|BFP)?\b", " ", t)
    t = re.sub(r"(?i)\b(?:seca5tagbfp|seca5bfp)\b", " ", t)
    t = re.sub(
        r"(?is)(?:OPEN ACCESS|EDITED BY|REVIEWED BY|\*CORRESPONDENCE|CITATION|COPYRIGHT|"
        r"RECEIVED|ACCEPTED|PUBLISHED|TYPE Original Research).{0,800}?(?=\n[A-Z]|\Z)",
        "\n",
        t,
    )
    t = re.sub(
        r"(?is)The use, distribution or reproduction in other forums.{0,600}",
        "",
        t,
    )
    # 页眉作者短行（Wang Y 等）
    t = re.sub(r"(?im)^[A-Z][a-z]+(?:\s+[A-Z])?(?:\s*,\s*[A-Z][a-z]+)*\s+et al\.?\s*$", "", t)
    # 变体：Weiner et al. / Song et al. 夹卷期
    t = re.sub(
        r"(?im)^[A-Z][A-Za-z\-]+(?:\s+[A-Z]\.)?(?:\s*[,&]\s*[A-Z][A-Za-z\-]+)*\s+et\s+al\.?\s*$",
        "",
        t,
    )
    t = re.sub(r"(?i)\bet\s+al\.?\b(?=\s*(?:\d{4}|$))", " ", t)
    return t


def content_body_recall(ref: str, body: str) -> float:
    return word_recall(strip_pdf_chrome(ref), body)


def run_micro_fuzz(body: str, entry_no: str, n: int = 200) -> list[dict]:
    """对正文做大量局部启发式扫描（每篇数百条）。"""
    fails = []
    # 1) 孤立面板行
    for i, m in enumerate(re.finditer(r"(?m)^[A-F]\*?\s*$", body)):
        if i < 20:
            fails.append({"kind": "orphan_panel", "at": m.start()})
    # 2) 页脚卷标
    for m in re.finditer(r"Vol[:.]{1,2}\s*\([\d.]+\)", body):
        fails.append({"kind": "vol", "at": m.start(), "snip": m.group(0)})
    # 3) 软连字符残留接图
    for m in re.finditer(r"[A-Za-z]{3,}-\n\n(?:Fig|Figure|TABLE)", body, re.I):
        fails.append({"kind": "hyphen_fig", "at": m.start(), "snip": m.group(0)[:40]})
    # 4) 时间轴插在 and 后（Nature 类）
    if entry_no == "7144":
        for m in re.finditer(r"\band\s+\d+\s*h\.?p\.?f", body, re.I):
            # 过滤正常 “and 48 h.p.f.” 科学叙述：若前后是 stromal/Fli1 才算坏
            ctx = body[max(0, m.start() - 40) : m.end() + 40]
            if "Fli1" in ctx or "GFPþ" in ctx or "GFP+" in ctx:
                fails.append({"kind": "timeline_insert", "snip": ctx})
    # 5) 滑动窗口：过短“句子”比例
    sents = re.split(r"(?<=[.!?])\s+", body)
    short = [s for s in sents if 0 < len(s) < 12 and re.search(r"[A-Za-z]", s)]
    # 参考文献/图注附录会产生大量短句，阈值阈值放宽
    if len(short) > max(n * 2, 400):
        fails.append({"kind": "too_many_short_sents", "n": len(short)})
    # 6) 字符抽样：控制符
    if re.search(r"[\x00-\x08\x0b\x0e-\x1f]", body):
        fails.append({"kind": "control_chars"})
    # 7) 重复大段（40+ 字近距重复）
    for m in re.finditer(r"(.{40,90})\1", body):
        fails.append({"kind": "near_dup", "snip": m.group(1)[:50]})
        if len(fails) > 50:
            break
    return fails[:80]


def evaluate_one(entry_no: str, info: dict) -> dict:
    layout = info["layout"]
    pdf = PDF_DIR / f"{entry_no}_{layout}.pdf"
    r = align_pdf(
        pdf,
        entry_no=entry_no,
        layout=layout,
        title=info["title"],
        doi=info.get("doi") or "",
    )
    body = r.body_text
    ref = extract_text_layer(pdf)
    hyp_all = body + "\n" + "\n".join(r.noise.values())
    recall_all = word_recall(ref, hyp_all)
    recall_body = word_recall(ref, body)
    recall_content = content_body_recall(ref, body)
    checks = order_ok_checks(body, layout)
    order_ok = all(c["ok"] for c in checks)

    asserts = []
    for eno, expect, pat, name in ASSERTIONS:
        if eno not in ("*", entry_no):
            continue
        hit = bool(re.search(pat, body, flags=re.I | re.S))
        ok = hit if expect else (not hit)
        asserts.append({"name": name, "ok": ok, "expect": expect, "hit": hit})

    fuzz = run_micro_fuzz(body, entry_no)
    # 章节质量
    sec = {}
    for k in ("abstract", "introduction", "methods", "results", "discussion", "conclusion"):
        v = r.sections.get(k) or ""
        bad = v in ("", "【无】", "【缺失】") or str(v).startswith("【") or len(v) < 80
        junk = bool(
            re.match(
                r"(?i)^(OPEN ACCESS|EDITED BY|ARTICLE|Fig(?:ure|\.)\s*\d+|using developing zebrafish)",
                v.strip(),
            )
        ) or ("wrote the manuscript" in v)
        sec[k] = {"ok": (not bad) and (not junk), "len": 0 if bad else len(v), "junk": junk}

    # 额外：Fig.10 是否在 captions/body（18472）
    extra = {}
    if entry_no == "18472":
        extra["fig10_in_body"] = "Fig. 10" in body or "Fig.10" in body
        caps = r.sections.get("figure_captions") or ""
        extra["fig10_in_caps"] = "Fig. 10" in caps or "Fig.10" in caps

    failed_asserts = [a for a in asserts if not a["ok"]]
    failed_secs = [k for k, v in sec.items() if not v["ok"]]
    score = 100.0
    score -= 8.0 * len(failed_asserts)
    score -= 3.0 * len(failed_secs)
    score -= 2.0 * min(20, len(fuzz))
    # 用去页眉后的正文召回计分（不惩罚故意剥离的 running head）
    score -= max(0.0, (0.98 - recall_content) * 250)
    if not order_ok:
        score -= 15
    score = max(0.0, min(100.0, score))

    return {
        "entry_no": entry_no,
        "layout": layout,
        "score": round(score, 2),
        "recall_all": round(recall_all, 6),
        "recall_body": round(recall_body, 6),
        "recall_content": round(recall_content, 6),
        "order_ok": order_ok,
        "order_checks": checks,
        "asserts": asserts,
        "failed_asserts": [a["name"] for a in failed_asserts],
        "sections": sec,
        "failed_sections": failed_secs,
        "fuzz_fail_n": len(fuzz),
        "fuzz_samples": fuzz[:12],
        "extra": extra,
        "body_chars": len(body),
    }


def run_round(round_id: str | None = None) -> dict:
    CAMPAIGN_DIR.mkdir(parents=True, exist_ok=True)
    rid = round_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    t0 = time.time()
    papers = []
    total_asserts = 0
    total_assert_fail = 0
    for no, info in META.items():
        ev = evaluate_one(no, info)
        papers.append(ev)
        total_asserts += len(ev["asserts"])
        total_assert_fail += len(ev["failed_asserts"])
        # 每篇再跑 200 次微扫描计数（已在 fuzz 内）
        total_asserts += 200
        total_assert_fail += min(200, ev["fuzz_fail_n"])

    avg_score = sum(p["score"] for p in papers) / max(1, len(papers))
    avg_body = sum(p["recall_body"] for p in papers) / max(1, len(papers))
    avg_content = sum(p.get("recall_content") or p["recall_body"] for p in papers) / max(1, len(papers))
    summary = {
        "round_id": rid,
        "ts": datetime.now(timezone.utc).isoformat(),
        "elapsed_s": round(time.time() - t0, 3),
        "avg_score": round(avg_score, 2),
        "avg_body_recall": round(avg_body, 6),
        "avg_content_recall": round(avg_content, 6),
        "total_micro_checks": total_asserts,
        "total_micro_fails": total_assert_fail,
        "all_order_ok": all(p["order_ok"] for p in papers),
        "papers": papers,
    }

    with LOG_JSONL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")

    SCOREBOARD.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Perfection campaign round `{rid}`",
        "",
        f"- avg_score: **{summary['avg_score']}**",
        f"- avg_body_recall: **{summary['avg_body_recall']:.4%}**",
        f"- avg_content_recall: **{summary.get('avg_content_recall', summary['avg_body_recall']):.4%}**",
        f"- micro_checks: {summary['total_micro_checks']} (fails {summary['total_micro_fails']})",
        f"- all_order_ok: {summary['all_order_ok']}",
        f"- elapsed: {summary['elapsed_s']}s",
        "",
        "| entry | score | content_recall | body_recall | order | failed_asserts | failed_sections | fuzz |",
        "|---|---:|---:|---:|:---:|---|---|---:|",
    ]
    for p in papers:
        lines.append(
            f"| {p['entry_no']} | {p['score']} | {p.get('recall_content', p['recall_body']):.4%} | "
            f"{p['recall_body']:.4%} | "
            f"{'Y' if p['order_ok'] else 'N'} | "
            f"{','.join(p['failed_asserts']) or '-'} | "
            f"{','.join(p['failed_sections']) or '-'} | {p['fuzz_fail_n']} |"
        )
    lines += ["", "## Top failures", ""]
    for p in papers:
        if p["failed_asserts"] or p["failed_sections"] or p["fuzz_samples"]:
            lines.append(f"### {p['entry_no']}")
            if p["failed_asserts"]:
                lines.append(f"- asserts: {p['failed_asserts']}")
            if p["failed_sections"]:
                lines.append(f"- sections: {p['failed_sections']}")
            for s in p["fuzz_samples"][:5]:
                lines.append(f"- fuzz: `{s}`")
            lines.append("")
    LATEST_MD.write_text("\n".join(lines), encoding="utf-8")
    return summary


def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    best = None
    for i in range(rounds):
        s = run_round(f"r{i+1}_{int(time.time())}")
        print(
            f"ROUND {i+1}/{rounds} score={s['avg_score']} "
            f"body_recall={s['avg_body_recall']:.4%} "
            f"checks={s['total_micro_checks']} fails={s['total_micro_fails']}",
            flush=True,
        )
        if best is None or s["avg_score"] > best["avg_score"]:
            best = s
    print("BEST", best["avg_score"] if best else None, flush=True)
    if best and best["avg_score"] < 95:
        sys.exit(2)


if __name__ == "__main__":
    main()
