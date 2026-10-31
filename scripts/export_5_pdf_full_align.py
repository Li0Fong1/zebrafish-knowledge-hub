# -*- coding: utf-8 -*-
"""五篇 PDF 全量对齐导出（轻量：不扫大 jsonl、汇总不含全文）。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.pdf_fulltext_align import (  # noqa: E402
    CONTENT_THRESHOLD,
    align_pdf,
    render_article_md,
    render_article_txt,
    result_to_dict,
)

OUT = ROOT / "解析文件" / "pdf_plaintext_samples"
PDF_DIR = OUT / "原文PDF"
ALIGN_DIR = OUT / "全量对齐"
SUMMARY_TXT = OUT / "五篇PDF纯文本汇总.txt"
SUMMARY_MD = OUT / "五篇PDF纯文本汇总.md"
REPORT = OUT / "重叠度核对报告.md"
README = OUT / "查看说明.md"

# 内置元数据，避免扫描 260MB+ results.jsonl
META = {
    "9685": {
        "layout": "springer",
        "en_title": "Differential toxicities of manganese ferrite nanoparticles from two synthesis methods using developing zebrafish (Danio rerio): towards a biocompatible and safe magnetic nanoparticles",
        "doi": "10.1007/s11051-026-06673-3",
        "pmid": "",
        "source_url": "https://link.springer.com/content/pdf/10.1007/s11051-026-06673-3.pdf",
    },
    "7144": {
        "layout": "nature",
        "en_title": "NACA deficiency reveals the crucial role of somite-derived stromal cells in haematopoietic niche formation",
        "doi": "10.1038/ncomms9375",
        "pmid": "26411530",
        "source_url": "https://www.nature.com/articles/ncomms9375.pdf",
    },
    "12686": {
        "layout": "frontiers",
        "en_title": "Agouti overexpression in a transgenic model regulates integrity, permeability and electrogenic amino acid transport in zebrafish intestine",
        "doi": "10.3389/fmars.2022.1011702",
        "pmid": "",
        "source_url": "https://www.frontiersin.org/articles/10.3389/fmars.2022.1011702/pdf",
    },
    "12971": {
        "layout": "ijdb",
        "en_title": "Transient expression of apoaequorin in zebrafish embryos: extending the ability to image calcium transients during later stages of development",
        "doi": "10.1387/ijdb.062151cc",
        "pmid": "16741871",
        "source_url": "https://ijdb.ehu.eus/article/pdf/062151cc",
    },
    "18472": {
        "layout": "jstage",
        "en_title": "Synthesis of Novel Class of N-Alkyl-isatin-3-iminobenzoic Acid Derivatives and Their Biological Activity in Zebrafish Embryos and Human Cancer Cell Lines",
        "doi": "10.1248/bpb.b17-00674",
        "pmid": "29249771",
        "source_url": "https://www.jstage.jst.go.jp/article/bpb/41/3/41_b17-00674/_pdf",
    },
}


def main() -> None:
    t_all = time.time()
    ALIGN_DIR.mkdir(parents=True, exist_ok=True)
    txt_dir = OUT / "原文文字层"
    txt_dir.mkdir(parents=True, exist_ok=True)
    for p in txt_dir.glob("*"):
        if p.is_file():
            p.unlink()

    rows = []
    all_ok = True
    summary_lines = [
        "五篇 PDF 全量对齐汇总（索引；全文见 全量对齐/）",
        "对齐: 内容≥99% / 顺序 / 排版分页 / 章节 / 噪声专区 / 符号 / 来源",
        "",
    ]
    md_lines = [
        "# 五篇 PDF 全量对齐汇总",
        "",
        "> 对齐：内容完整 · 阅读顺序 · 排版分页 · 章节 · 噪声专区 · 符号规范化 · 来源追溯",
        ">",
        "> **全文在** `全量对齐/`（每篇 md/txt/json）。本文件为索引与指标。",
        "",
        "| 条目 | 版式 | 词召回 | 内容 | 顺序 | 章节填充 | 单篇 |",
        "|---|---|---:|:---:|:---:|---:|---|",
    ]

    for no, info in META.items():
        layout = info["layout"]
        pdf = PDF_DIR / f"{no}_{layout}.pdf"
        print(f">>> [{no}] {layout} start", flush=True)
        t0 = time.time()
        if not pdf.is_file():
            raise SystemExit(f"missing {pdf}")

        r = align_pdf(
            pdf,
            entry_no=no,
            layout=layout,
            title=info["en_title"],
            doi=info.get("doi") or "",
            pmid=info.get("pmid") or "",
            source_url=info.get("source_url") or "",
        )
        ok = bool(r.metrics["content_ok"] and r.metrics["order_ok"])
        all_ok = all_ok and ok
        rows.append(r)

        # 单篇：只写对齐目录（避免汇总膨胀）
        d = result_to_dict(r)
        (ALIGN_DIR / f"{no}_{layout}.json").write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (ALIGN_DIR / f"{no}_{layout}.md").write_text(render_article_md(r), encoding="utf-8")
        txt = render_article_txt(r)
        (ALIGN_DIR / f"{no}_{layout}.txt").write_text(txt, encoding="utf-8")
        (txt_dir / f"{no}_{layout}_全量对齐.txt").write_text(txt, encoding="utf-8")

        summary_lines += [
            f"[{no}] {layout} | recall={r.metrics['word_recall']:.4%} | "
            f"content={r.metrics['content_ok']} order={r.metrics['order_ok']} | "
            f"sections={r.metrics['section_fill_count']} | 全量对齐/{no}_{layout}.txt",
            f"  title: {info['en_title'][:100]}",
            f"  doi: {r.doi or '【无】'}",
            "",
        ]
        md_lines.append(
            f"| {no} | {layout} | {r.metrics['word_recall']:.4%} | "
            f"{'是' if r.metrics['content_ok'] else '否'} | "
            f"{'是' if r.metrics['order_ok'] else '否'} | "
            f"{r.metrics['section_fill_count']} | "
            f"[md](全量对齐/{no}_{layout}.md) / [txt](全量对齐/{no}_{layout}.txt) / [json](全量对齐/{no}_{layout}.json) |"
        )
        print(
            f"<<< [{no}] {round(time.time()-t0,2)}s recall={r.metrics['word_recall']:.4%} ok={ok}",
            flush=True,
        )

    SUMMARY_TXT.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    SUMMARY_MD.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    report = [
        "# PDF 全量对齐核对报告",
        "",
        "## 对齐维度",
        "",
        "1. 内容词召回 ≥99%",
        "2. 阅读顺序（双栏/侧栏）",
        "3. 排版分页",
        "4. 章节结构",
        "5. 噪声专区",
        "6. 符号规范化",
        "7. 来源追溯",
        "",
        f"- 阈值：≥ {CONTENT_THRESHOLD:.0%}",
        f"- **总评**：{'全部达标' if all_ok else '未全部达标'}",
        "",
        "| 条目 | 版式 | 词召回 | 内容 | 顺序 | 章节填充 |",
        "|---|---|---:|:---:|:---:|---:|",
    ]
    for r in rows:
        report.append(
            f"| {r.entry_no} | {r.layout} | {r.metrics['word_recall']:.4%} | "
            f"{'是' if r.metrics['content_ok'] else '否'} | "
            f"{'是' if r.metrics['order_ok'] else '否'} | {r.metrics['section_fill_count']} |"
        )
    report += ["", f"单篇目录：`{ALIGN_DIR.relative_to(ROOT)}`", ""]
    REPORT.write_text("\n".join(report), encoding="utf-8")
    README.write_text(
        "\n".join(
            [
                "# 五篇样本：全量对齐",
                "",
                "- `原文PDF/` — PDF 原文",
                "- `全量对齐/` — 每篇 md/txt/json（含章节与来源）",
                "- `五篇PDF纯文本汇总.md` — 索引与指标",
                "- `重叠度核对报告.md`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"DONE in {round(time.time()-t_all,2)}s ALL_OK={all_ok}", flush=True)
    if not all_ok:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
