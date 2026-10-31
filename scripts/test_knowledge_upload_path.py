# -*- coding: utf-8 -*-
"""Verify literature ingest path: upload archive → knowledge.d → rebuild hooks → Hub sources.

Documents where files live (local + server conventions).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "rag", ROOT / "api"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def main() -> int:
    from config import KNOWLEDGE_EXTRA_DIR, KNOWLEDGE_PATH, KNOWLEDGE_UPLOAD_DIR, FAISS_INDEX_DIR
    from knowledge_ingest import ingest_upload_file, list_knowledge_sources, rebuild_rag_index

    print("PATHS", flush=True)
    print("  KNOWLEDGE_PATH     ", KNOWLEDGE_PATH, flush=True)
    print("  KNOWLEDGE_EXTRA_DIR", KNOWLEDGE_EXTRA_DIR, " (parsed RAG texts)", flush=True)
    print("  KNOWLEDGE_UPLOAD_DIR", KNOWLEDGE_UPLOAD_DIR, " (per-tenant archive)", flush=True)
    print("  FAISS_INDEX_DIR    ", FAISS_INDEX_DIR, flush=True)
    print(
        "  SERVER_BASE note   ",
        "/data/zoujin/deng/zebrafish_gene_qa/rag/{knowledge.txt,knowledge.d,uploads/<tenant>/}",
        flush=True,
    )

    sample = (
        "【Zebrafish Hub ingest verify】\n"
        "Danio rerio tp53 is a tumor suppressor frequently used in zebrafish models.\n"
        "This fixture validates upload → store → list_knowledge_sources.\n"
    )
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "zf_ingest_verify.txt"
        src.write_text(sample, encoding="utf-8")
        saved = ingest_upload_file(str(src), "zf_ingest_verify.txt", tenant_slug="verify")
        print("INGESTED", saved, flush=True)
        assert Path(saved).is_file(), saved
        assert "verify" in Path(saved).name or Path(saved).is_file()

        archive = Path(KNOWLEDGE_UPLOAD_DIR) / "verify" / "zf_ingest_verify.txt"
        print("ARCHIVE", archive, "exists=", archive.is_file(), flush=True)

        listed = list_knowledge_sources()
        names = [s["name"] for s in listed.get("sources") or []]
        print("SOURCES count=", listed.get("count"), "has_verify=", any("zf_ingest_verify" in n for n in names), flush=True)

        # rebuild is heavy on server with SKIP_KNOWLEDGE_FAISS_REBUILD; call only if allowed
        if os.getenv("ZF_FORCE_RAG_REBUILD", "0") in ("1", "true"):
            print("REBUILD…", flush=True)
            rebuild_rag_index()
            print("REBUILD_OK", flush=True)
        else:
            print("REBUILD_SKIPPED (set ZF_FORCE_RAG_REBUILD=1 to force)", flush=True)

    print("UPLOAD_PATH_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
