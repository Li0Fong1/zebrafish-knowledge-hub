# -*- coding: utf-8 -*-
"""严格验收五篇完整版报告；失败则 exit 1 并打印全部问题。"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("FAISS_DISABLE_AVX2", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

REPORT = ROOT / "解析文件" / "向量化_五篇完整测试结果.json"
TARGET_PMIDS = ["1465413", "7597068", "7994634", "8618902", "8893009"]


def main() -> int:
    errors: list[str] = []

    # 0) 报告存在
    if not REPORT.is_file():
        print("[FAIL] 完整报告不存在，先重新导出")
        from scripts.export_five_vectorize_full_report import main as export_main

        code = export_main()
        if code != 0 or not REPORT.is_file():
            return 1

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    cases = report.get("cases") or []
    hub = report.get("hub") or {}

    if len(cases) != 5:
        errors.append(f"cases 数量不是 5：{len(cases)}")

    # 1) PMID 集合与顺序
    pmids = [str(c.get("pmid")) for c in cases]
    if pmids != TARGET_PMIDS:
        errors.append(f"PMID 列表不符：{pmids} vs {TARGET_PMIDS}")

    # 2) 逐条硬检查 + 现场重检索复核
    from rag.pubmed_yes_retriever import PubmedYesRetriever
    from rag.pubmed_yes_corpus_io import MAIN_CORPUS

    corpus = {}
    with open(MAIN_CORPUS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            corpus[str(rec.get("pmid"))] = rec

    r = PubmedYesRetriever()
    if not r.ready:
        errors.append("PubmedYesRetriever 未就绪")

    for i, c in enumerate(cases, 1):
        pmid = str(c.get("pmid"))
        prefix = f"样例{i} PMID={pmid}"

        if not c.get("title_zh") and not c.get("title_en"):
            errors.append(f"{prefix}: 无标题")
        if not (c.get("abstract_en") or c.get("abstract_zh")):
            errors.append(f"{prefix}: 语料无摘要")
        if not c.get("query_full") or len(c["query_full"]) < 20:
            errors.append(f"{prefix}: query_full 过短/空")
        if c.get("self_rank") != 1:
            errors.append(f"{prefix}: self_rank={c.get('self_rank')} 非第1")

        top5 = c.get("top5") or []
        if len(top5) < 1:
            errors.append(f"{prefix}: top5 为空")
        elif str(top5[0].get("pmid")) != pmid:
            errors.append(
                f"{prefix}: Top1 PMID={top5[0].get('pmid')} 与目标不一致"
            )

        for h in top5:
            if not h.get("pmid"):
                errors.append(f"{prefix}: 命中缺 PMID")
            text = h.get("text_full") or ""
            if len(text.strip()) < 40:
                errors.append(f"{prefix}: chunk 文本过短 pmid={h.get('pmid')}")
            if h.get("score") is None:
                errors.append(f"{prefix}: 缺 score")

        # 与语料一致性
        rec = corpus.get(pmid)
        if not rec:
            errors.append(f"{prefix}: 语料中找不到该 PMID")
        else:
            if (c.get("title_en") or "") != (rec.get("title_en") or ""):
                errors.append(f"{prefix}: title_en 与语料不一致")
            abs_en_c = (c.get("abstract_en") or "").strip()
            abs_en_r = (rec.get("abstract_en") or "").strip()
            if abs_en_c and abs_en_r and abs_en_c != abs_en_r:
                errors.append(f"{prefix}: abstract_en 与语料不一致")

        # 现场重检索：同一完整提问必须再拿第1
        if r.ready and c.get("query_full"):
            hits = r.search(c["query_full"], top_k=5)
            if not hits or str(hits[0].get("pmid")) != pmid:
                got = hits[0].get("pmid") if hits else None
                errors.append(f"{prefix}: 复检 Top1={got} 不是目标")
            # 报告内 text 应与现场一致（至少 Top1）
            if hits and top5:
                live = (hits[0].get("text") or "").strip()
                saved = (top5[0].get("text_full") or "").strip()
                if live and saved and live != saved:
                    errors.append(f"{prefix}: Top1 文本与现场检索不一致（报告过期）")

    # 3) Hub
    if hub.get("source") != "pubmed_yes":
        errors.append(f"Hub source={hub.get('source')} 不是 pubmed_yes")
    if not hub.get("has_hits"):
        errors.append("Hub has_hits=False")
    if not (hub.get("excerpt_full") or "").strip():
        errors.append("Hub excerpt 为空")
    if not hub.get("pmids"):
        errors.append("Hub pmids 为空")
    if hub.get("query_full") != cases[0].get("query_full"):
        errors.append("Hub 提问与样例1不一致")

    # 现场重跑 Hub
    from agent.hub_orchestrator import _run_literature

    lit = _run_literature(cases[0]["query_full"])
    if lit.get("source") != "pubmed_yes":
        errors.append(f"Hub 复检 source={lit.get('source')}")
    if not lit.get("has_hits"):
        errors.append("Hub 复检无命中")
    live_excerpt = (lit.get("excerpt") or "").strip()
    saved_excerpt = (hub.get("excerpt_full") or "").strip()
    if live_excerpt and saved_excerpt and live_excerpt != saved_excerpt:
        errors.append("Hub excerpt 与现场不一致（报告过期）")

    # 4) gate 字段
    gate = report.get("gate") or {}
    if not gate.get("all_rank1"):
        errors.append("gate.all_rank1 为 False")
    if not gate.get("hub_pubmed_yes"):
        errors.append("gate.hub_pubmed_yes 为 False")

    if errors:
        print(f"=== 发现 {len(errors)} 个问题 ===")
        for e in errors:
            print("[ERR]", e)
        return 1

    print("=== 完整版五篇验收通过（无错误）===")
    print(f"report: {REPORT}")
    print(f"pmids: {pmids}")
    print(f"hub_chars: {hub.get('chars')} source={hub.get('source')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
