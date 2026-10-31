# -*- coding: utf-8 -*-
"""对新对齐文件与合集做一轮复检。"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.pdf_fulltext_align import extract_text_layer, order_ok_checks, tokenize, word_recall

OUT = ROOT / "解析文件" / "pdf_plaintext_samples"
ALIGN = OUT / "全量对齐"
PDF = OUT / "原文PDF"
SUMMARY_TXT = OUT / "五篇PDF纯文本汇总.txt"
SUMMARY_MD = OUT / "五篇PDF纯文本汇总.md"

META = {
    "9685": "springer",
    "7144": "nature",
    "12686": "frontiers",
    "12971": "ijdb",
    "18472": "jstage",
}

HOTSPOTS = {
    "12971_ijdb": [
        (True, r"provide\s+spatio-?temporal\s+information", "provide spatio-temporal"),
        (True, r"generating\s+Ca2\+\s+signals?\)", "Ca2+ signals)"),
        (False, r"provide\s*\n\s*Fig\.\s*3", "provide→Fig.3坏切"),
        (False, r"sig-\s*\n", "sig-残留"),
    ],
    "7144_nature": [
        (True, r"\(Fli1:GFP[^\)]*\)\s+and\s+stromal\s+\(TCF", "Fli1 and stromal"),
        (True, r"In vertebrate ontogeny", "In vertebrate"),
        (False, r"\(Fli1:GFP[^\)]*\)\s+and\s+(?:\d+\s*h\.?p\.?f|som\b)", "时间轴插句"),
        (False, r"(?m)^n vertebrate", "drop-cap丢失I"),
    ],
    "12686_frontiers": [
        (True, r"tryptophan\s+were\s+also\s+up-regulated", "tryptophan were also"),
        (False, r"(?m)^were also up-regulated", "孤立 were also"),
    ],
    "9685_springer": [
        (True, r"magnetic resonance imaging \(MRI\).{0,160}hyperthermia \[7\]", "MRI句"),
        (True, r"Thiago Lopes Rocha", "作者正常"),
        (True, r"EDS analyses", "EDS analyses"),
        (False, r"LoThiago|assessment\.hermal|biommarker", "LLM乱码"),
        (False, r"Vol[:.]{1,2}\s*\([\d.]+\)", "Vol页脚"),
        (False, r"analy-\s*\n", "analy-断词"),
    ],
}


def sec_ok(sections: dict, key: str) -> bool:
    v = sections.get(key) or ""
    return (
        v not in ("", "【无】", "【缺失】")
        and not str(v).startswith("【")
        and len(v) >= 80
    )


def main() -> None:
    issues: list[tuple[str, str, str]] = []
    ok_count = 0

    print("=" * 72)
    print("1) 文件存在性与规模")
    print("=" * 72)
    for p in (SUMMARY_TXT, SUMMARY_MD):
        print(f"  {p.name}: exists={p.is_file()} size={p.stat().st_size if p.is_file() else 0}")
    for no, layout in META.items():
        for ext in (".json", ".txt", ".md"):
            p = ALIGN / f"{no}_{layout}{ext}"
            if not p.is_file():
                print(f"  MISSING {p.name}")
                issues.append((no, "缺文件", p.name))

    print()
    print("=" * 72)
    print("2) 指标 + 顺序抽查 + 章节 + 热点")
    print("=" * 72)

    for no, layout in META.items():
        jp = ALIGN / f"{no}_{layout}.json"
        d = json.loads(jp.read_text(encoding="utf-8"))
        body = d["body_text"]
        noise = d.get("noise") or {}
        m = d["metrics"]
        secs = d.get("sections") or {}
        pdf = PDF / f"{no}_{layout}.pdf"
        ref = extract_text_layer(pdf)
        hyp = body + "\n" + "\n".join(str(v) for v in noise.values())
        recall = word_recall(ref, hyp)
        rc, hc = Counter(tokenize(ref)), Counter(tokenize(hyp))
        miss = sorted(
            [(c - hc.get(w, 0), w) for w, c in rc.items() if c > hc.get(w, 0)],
            reverse=True,
        )
        miss_n = sum(c for c, _ in miss)
        checks = order_ok_checks(body, layout)
        order_ok = all(c["ok"] for c in checks)

        print(f"\n[{no}] {layout}")
        print(
            f"  metrics: recall={m.get('word_recall')} missing={m.get('missing_count')} "
            f"order={m.get('order_ok')} fill={m.get('section_fill_count')}"
        )
        print(f"  recheck: recall={recall:.6f} miss_tokens={miss_n} order={order_ok}")
        print(
            "  sections:"
            f" abs={sec_ok(secs, 'abstract')}"
            f" intro={sec_ok(secs, 'introduction')}"
            f" methods={sec_ok(secs, 'methods')}"
            f" results={sec_ok(secs, 'results')}"
            f" disc={sec_ok(secs, 'discussion')}"
            f" concl={sec_ok(secs, 'conclusion')}"
        )

        stored_recall = float(m.get("word_recall") or 0)
        stored_miss = m.get("missing_count")
        if stored_miss is None:
            stored_miss = -1
        else:
            stored_miss = int(stored_miss)
        if abs(stored_recall - recall) > 1e-6 or stored_miss != miss_n:
            issues.append(
                (
                    no,
                    "指标不一致",
                    f"stored={stored_recall}/{stored_miss} live={recall}/{miss_n}",
                )
            )
        if not order_ok:
            for c in checks:
                if not c["ok"]:
                    issues.append((no, "顺序失败", c["name"]))
                    print("    FAIL", c["name"], c.get("snippet"))
        if miss_n:
            issues.append((no, "仍有缺词", str(miss[:10])))
            print("    missing top", miss[:8])
        for k in ("abstract", "discussion"):
            if not sec_ok(secs, k):
                issues.append((no, f"章节空:{k}", ""))
                print(f"    SEC EMPTY {k}")

        for expect, pat, label in HOTSPOTS.get(f"{no}_{layout}", []):
            hit = bool(re.search(pat, body, flags=re.I | re.S))
            if expect and not hit:
                issues.append((no, "热点缺失", label))
                print("    HOTSPOT MISS", label)
            if (not expect) and hit:
                issues.append((no, "坏模式仍在", label))
                print("    HOTSPOT BAD", label)

        if "llm_content_anchor" in body:
            issues.append((no, "正文含锚点", ""))
        if re.search(r"LoThiago|assessment\.hermal|biommarker", body):
            issues.append((no, "LLM乱码", ""))
            print("    BAD LLM garbled")
        vol_n = len(re.findall(r"Vol[:.]{1,2}\s*\([\d.]+\)", body))
        if vol_n:
            issues.append((no, "Vol残留", str(vol_n)))
            print("    BAD Vol", vol_n)
        dang = re.findall(r"[A-Za-z]{3,}-\n\n(?:Fig|Figure|FIGURE|Table)", body)
        if dang:
            issues.append((no, "断词接图题", str(len(dang))))
            print("    BAD hyphen-fig", len(dang))

        if stored_recall >= 1.0 - 1e-12 and order_ok and miss_n == 0:
            ok_count += 1

    print()
    print("=" * 72)
    print("3) 合集正文质量")
    print("=" * 72)
    st = SUMMARY_TXT.read_text(encoding="utf-8")
    sm = SUMMARY_MD.read_text(encoding="utf-8")
    print(f"  txt chars={len(st)} md chars={len(sm)}")
    checks_sum = [
        ("含噪声锚点", "llm_content_anchor" in st or "【噪声" in st),
        ("LoThiago乱码", "LoThiago" in st),
        ("assessment.hermal", "assessment.hermal" in st),
        ("biommarker", "biommarker" in st),
        ("Vol页脚", bool(re.search(r"Vol[:.]{1,2}\s*\([\d.]+\)", st))),
        ("analy-断词", bool(re.search(r"analy-\s*\n", st))),
        ("缺 tryptophan were", "tryptophan were also" not in st),
        ("缺 spatio-temporal information", "spatio-temporal information" not in st),
        (
            "缺 Fli1 and stromal",
            not bool(re.search(r"Fli1:GFP[^\)]*\) and stromal \(TCF", st)),
        ),
        ("缺 In vertebrate", "In vertebrate ontogeny" not in st),
        ("缺 EDS analyses", "EDS analyses" not in st),
        ("缺 Thiago Lopes Rocha", "Thiago Lopes Rocha" not in st),
    ]
    for name, bad in checks_sum:
        status = "FAIL" if bad else "OK"
        if bad:
            issues.append(("合集", name, ""))
        print(f"  {status} {name}")

    for no in META:
        if f"[{no}]" not in st:
            issues.append(("合集", f"缺少条目{no}", ""))
            print(f"  FAIL 缺少条目 {no}")
        else:
            print(f"  OK 含条目 {no}")

    # md 与 txt 正文关键片段一致
    if "LoThiago" in sm or "llm_content_anchor" in sm:
        issues.append(("合集md", "含乱码或锚点", ""))
        print("  FAIL md含乱码或锚点")
    else:
        print("  OK md无乱码/锚点")

    print()
    print("=" * 72)
    print("4) 总判")
    print("=" * 72)
    print(f"  单篇达标 {ok_count}/5")
    print(f"  问题条目数 {len(issues)}")
    if issues:
        print("  问题明细:")
        for it in issues:
            print("   -", it)
        raise SystemExit(1)
    print("  未发现残留问题。")


if __name__ == "__main__":
    main()
