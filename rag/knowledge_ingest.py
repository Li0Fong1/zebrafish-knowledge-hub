# -*- coding: utf-8 -*-
"""知识库入库：PDF/文本 → knowledge.d → 触发 RAG 重建。"""
import bootstrap  # noqa: F401

import os
import re
import shutil
from datetime import datetime

from config import KNOWLEDGE_EXTRA_DIR, KNOWLEDGE_UPLOAD_DIR, KNOWLEDGE_PATH
from common_utils import logger


def _safe_filename(name: str) -> str:
    try:
        from file_security import sanitize_filename
        return sanitize_filename(name)
    except ImportError:
        base = os.path.basename(name)
        base = re.sub(r'[<>:"/\\|?*]', "_", base)
        return base or "upload.txt"


def extract_pdf_text(pdf_path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError("请安装 pypdf：pip install pypdf")
    max_pages = int(os.getenv("WEB_UPLOAD_MAX_PDF_PAGES", "120"))
    reader = PdfReader(pdf_path)
    if len(reader.pages) > max_pages:
        raise RuntimeError(f"PDF 页数超过上限（{max_pages} 页）")
    parts = []
    for page in reader.pages[:max_pages]:
        t = page.extract_text() or ""
        if t.strip():
            parts.append(t.strip())
    text = "\n\n".join(parts)
    try:
        from file_security import scan_text_content
        err = scan_text_content(text)
        if err:
            raise RuntimeError(err)
    except ImportError:
        pass
    return text


def ingest_text(content: str, source_name: str) -> str:
    """写入 knowledge.d 并返回目标路径。"""
    os.makedirs(KNOWLEDGE_EXTRA_DIR, exist_ok=True)
    safe = _safe_filename(source_name)
    if not safe.endswith(".txt"):
        safe = os.path.splitext(safe)[0] + ".txt"
    target = os.path.join(KNOWLEDGE_EXTRA_DIR, safe)
    header = f"【来源：{source_name} 入库时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}】\n\n"
    with open(target, "w", encoding="utf-8") as f:
        f.write(header + content.strip() + "\n")
    logger.info(f"[知识库] 已入库：{target}")
    return target


def ingest_pdf_file(pdf_path: str) -> str:
    text = extract_pdf_text(pdf_path)
    if len(text.strip()) < 50:
        raise RuntimeError("PDF 未能提取到有效文本")
    return ingest_text(text, os.path.basename(pdf_path))


def ingest_upload_file(file_path: str, original_name: str, *, tenant_slug: str = "default") -> str:
    from config import KNOWLEDGE_EXTRA_DIR
    try:
        from file_security import check_library_quota
        quota_err = check_library_quota(KNOWLEDGE_EXTRA_DIR)
        if quota_err:
            raise RuntimeError(quota_err)
    except ImportError:
        pass
    tenant_slug = (tenant_slug or "default").strip().lower() or "default"
    tenant_dir = os.path.join(KNOWLEDGE_UPLOAD_DIR, tenant_slug)
    os.makedirs(tenant_dir, exist_ok=True)
    ext = os.path.splitext(_safe_filename(original_name))[1].lower()
    archive_name = _safe_filename(original_name)
    if archive_name:
        try:
            shutil.copy2(file_path, os.path.join(tenant_dir, archive_name))
        except OSError as e:
            logger.warning(f"[知识库] 租户归档失败 {tenant_slug}: {e}")
    source_label = f"[{tenant_slug}] {original_name}"
    if ext == ".pdf":
        text = extract_pdf_text(file_path)
        if len(text.strip()) < 50:
            raise RuntimeError("PDF 未能提取到有效文本")
        return ingest_text(text, source_label)
    if ext in (".txt", ".md"):
        with open(file_path, "r", encoding="utf-8", errors="strict") as f:
            content = f.read()
        try:
            from file_security import scan_text_content
            err = scan_text_content(content)
            if err:
                raise RuntimeError(err)
        except ImportError:
            pass
        return ingest_text(content, source_label)
    raise RuntimeError(f"不支持的文件类型：{ext}，请上传 PDF/TXT/MD")


def rebuild_rag_index():
    """清除 RAG 单例，同步重建 FAISS 索引。"""
    import rag_builder as rb

    rb.rag_instance = None
    rag = rb.get_rag()
    if hasattr(rag, "_build_rag_safe"):
        rag._build_rag_safe()
    return {"rebuilt": True, "knowledge_main": KNOWLEDGE_PATH}


def append_rag_documents(paths) -> dict:
    """把新入库的文本增量加进现有 FAISS 索引；失败时回退整体重建。

    返回 {"mode": append|rebuild|skip_vector|skip|failed, ...}，调用方据此记日志，
    但绝不因为索引问题把文献入库本身判成失败（文本已落盘，关键词检索可用）。

    注意：线上 `.env` 里 `SKIP_KNOWLEDGE_FAISS_REBUILD=1`，即本部署刻意不建
    知识库向量索引（465 MB 索引停在 8-14，检索走关键词 + PubmedYes 分片）。
    这种情况下必须直接跳过：否则会为了 5 篇新文献去加载 15 GB 的嵌入模型，
    白等两三分钟还必然失败。
    """
    files = [p for p in (paths or []) if p and os.path.isfile(p)]
    if not files:
        return {"mode": "skip", "chunks": 0, "files": 0}
    if os.getenv("SKIP_KNOWLEDGE_FAISS_REBUILD", "").strip().lower() in ("1", "true", "yes"):
        return {"mode": "skip_vector", "chunks": 0, "files": len(files)}
    try:
        import rag_builder as rb

        rag = rb.get_rag()
        n = rag.append_documents(files)
        return {"mode": "append", "chunks": n, "files": len(files)}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[知识库] 增量索引失败（{type(e).__name__}），回退整体重建：{e}")
        try:
            rebuild_rag_index()
            return {
                "mode": "rebuild",
                "chunks": 0,
                "files": len(files),
                "fallback_reason": f"{type(e).__name__}: {e}"[:200],
            }
        except Exception as e2:  # noqa: BLE001
            logger.warning(f"[知识库] 整体重建也失败：{type(e2).__name__}: {e2}")
            return {
                "mode": "failed",
                "chunks": 0,
                "files": len(files),
                "error": f"{type(e2).__name__}: {e2}",
            }


def list_knowledge_sources() -> dict:
    sources = []
    if os.path.isfile(KNOWLEDGE_PATH):
        sources.append({"name": "knowledge.txt", "path": KNOWLEDGE_PATH, "type": "main"})
    if os.path.isdir(KNOWLEDGE_EXTRA_DIR):
        for fn in sorted(os.listdir(KNOWLEDGE_EXTRA_DIR)):
            if fn.endswith((".txt", ".md")):
                fp = os.path.join(KNOWLEDGE_EXTRA_DIR, fn)
                sources.append({"name": fn, "path": fp, "type": "extra"})
    return {"count": len(sources), "sources": sources}
