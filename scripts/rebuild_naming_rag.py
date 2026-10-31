# -*- coding: utf-8 -*-
"""更新命名/ID 映射知识并重建 RAG 索引。"""
from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from rag.knowledge_ingest import rebuild_rag_index


def main() -> int:
    safe_print("重建 RAG（含 hub_id_naming_guide 等 knowledge.d）…")
    rebuild_rag_index()
    safe_print("[完成] RAG 索引已重建")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
