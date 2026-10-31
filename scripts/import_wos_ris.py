# -*- coding: utf-8 -*-
"""导入 WoS RIS（含 wos数据包/Downloads）并入库 RAG + catalog。"""
from core.entry import bootstrap_app

bootstrap_app()

import argparse

from common_utils import safe_print
from literature_models import LiteratureProgress, ingest_articles
from wos_collector import import_all_ris_sources
from knowledge_ingest import rebuild_rag_index


def main():
    parser = argparse.ArgumentParser(description="导入 WoS RIS → RAG（含文献分层）")
    parser.add_argument("--rebuild-rag", action="store_true", help="导入后重建 FAISS 索引")
    args = parser.parse_args()

    safe_print("=" * 60)
    safe_print("  WoS RIS 批量导入（broad + core_genetics 分层）")
    safe_print("=" * 60)

    arts = import_all_ris_sources()
    safe_print(f"  解析合计 {len(arts)} 篇（去重后）")
    if not arts:
        safe_print("  未找到 .ris 文件，请放入 data/wos_import/ 或 wos数据包/Downloads/")
        return 1

    progress = LiteratureProgress()
    pending = [a for a in arts if not progress.is_seen(a)]
    safe_print(f"  待入库 {len(pending)} 篇（跳过已见 {len(arts) - len(pending)}）")

    core_n = sum(1 for a in pending if a.get("tier") == "core_genetics")
    safe_print(f"  其中 core_genetics 精选 {core_n} 篇")

    batch = 500
    total = 0
    for i in range(0, len(pending), batch):
        chunk = pending[i:i + batch]
        n = ingest_articles(chunk, f"wos_import_{i // batch + 1}")
        for a in chunk:
            progress.mark(a)
        progress.save()
        total += n
        safe_print(f"  已入库 {min(i + batch, len(pending))}/{len(pending)}")

    safe_print(f"\n[完成] 本次入库 {total} 篇")
    if args.rebuild_rag:
        safe_print("  重建 RAG 索引…")
        rebuild_rag_index()
        safe_print("  RAG 索引已重建")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
