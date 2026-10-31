# -*- coding: utf-8 -*-
"""对 LLM 循环修复后的对齐结果再检测 vs 原版 PDF。"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.entry import bootstrap_app

bootstrap_app()

from rag.pdf_fulltext_align import extract_text_layer, order_ok_checks, tokenize, word_recall

OUT = ROOT / "解析文件" / "pdf_plaintext_samples"
PDF_DIR = OUT / "原文PDF"
ALIGN = OUT / "全量对齐"
REPORT = OUT / "修复后复检报告.md"

TARGETS = ["9685_springer", "7144_nature", "12686_frontiers", "12971_ijdb", "18472_jstage"]


def missing_words(ref: str, hyp: str):
    rc, hc = Counter(tokenize(ref)), Counter(tokenize(hyp))
    miss = [(c - hc.get(w, 0), w) for w, c in rc.items() if c > hc.get(w, 0)]
    return sorted(miss, reverse=True)


def main():
    lines = [
        "# 修复后复检（对齐结果 vs 原版 PDF）",
        "",
        "| 条目 | 词召回 | 缺词数 | 顺序 | 差异 |",
        "|---|---:|---:|:---:|:---:|",
    ]
    all_ok = True
    details = []
    for name in TARGETS:
        no, layout = name.split("_", 1)
        data = json.loads((ALIGN / f"{name}.json").read_text(encoding="utf-8"))
        ref = extract_text_layer(PDF_DIR / f"{name}.pdf")
        body = data.get("body_text") or ""
        noise = data.get("noise") or {}
        hyp = (
            body
            + "\n"
            + (noise.get("sidebar_affiliations") or "")
            + "\n"
            + (noise.get("running_headers_footers") or "")
            + "\n"
            + (noise.get("llm_content_anchor") or "")
        )
        recall = word_recall(ref, hyp)
        miss = missing_words(ref, hyp)
        checks = order_ok_checks(body, layout)
        order_ok = all(c["ok"] for c in checks)
        ok = recall >= 1.0 - 1e-12 and len(miss) == 0 and order_ok
        all_ok = all_ok and ok
        lines.append(
            f"| {no} | **{recall:.4%}** | {sum(c for c,_ in miss)} | "
            f"{'是' if order_ok else '否'} | {'无' if ok else '有'} |"
        )
        details.append({"entry_no": no, "recall": recall, "missing": miss[:10], "order_ok": order_ok, "ok": ok})
        print(f"{no}: recall={recall:.6%} missing={sum(c for c,_ in miss)} order={order_ok} ok={ok}", flush=True)

    lines += ["", f"## 总评", "", f"**{'全部无内容/顺序差异' if all_ok else '仍有差异'}**", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    (OUT / "修复后复检.json").write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", REPORT)
    print("ALL_OK" if all_ok else "HAS_DIFF")
    if not all_ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
