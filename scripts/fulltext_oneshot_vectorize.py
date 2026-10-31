# -*- coding: utf-8 -*-
"""
全文 oneshot / 旁路重嵌入口（替代空的 fulltext_oneshot_vectorize.py）。

硬约束：
  - 默认写入 FAISS_PUBMED_YES_GTE_DIR（gte 旁路目录），禁止覆盖现网 faiss_pubmed_yes
  - 默认 embedding = GTE_MULTILINGUAL_BASE_PATH；可用 --model 覆盖
  - 默认开启 section 切块（事项二并入阶段 C）
  - 低并行：单进程、小 batch；勿与 Hub 双开全量

用法：
  python -m scripts.fulltext_oneshot_vectorize --limit 30
  python -m scripts.fulltext_oneshot_vectorize --limit 50 --resume
  python -m scripts.fulltext_oneshot_vectorize --full   # 全库（慢，16GB 慎用）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "database"))
sys.path.insert(0, str(ROOT / "core"))

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("FAISS_DISABLE_AVX2", "1")

from core.entry import bootstrap_app

bootstrap_app()

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from common_utils import safe_print
from config import (
    EXIST_ENO_MAX_BODY_CHARS,
    EXIST_ENO_MAX_CHUNKS,
    FAISS_PUBMED_YES_DIR,
    FAISS_PUBMED_YES_GTE_DIR,
    GTE_MULTILINGUAL_BASE_PATH,
    PUBMED_YES_CORPUS_JSONL,
    PUBMED_YES_EMBED_BATCH,
    PUBMED_YES_PARA_MAX_CHARS,
    PUBMED_YES_PARA_MIN_CHARS,
    PUBMED_YES_SHARD_PAPERS,
)
from rag.pubmed_yes_chunking import split_record_to_field_chunks
from rag.rag_builder import BgeEmbedding

INDEX_VERSION = "gte_mbase_v1"
EMBEDDING_DIM = 768
STATUS_FILE = ROOT / "解析文件" / "事项一_换模落地状态.txt"
PROGRESS_FILE = ROOT / "解析文件" / "gte_reembed_progress.txt"
JOB_META = ROOT / "logs" / "gte_reembed_job.json"


def _forbid_old_dir(out_dir: Path) -> None:
    old = Path(FAISS_PUBMED_YES_DIR).resolve()
    if out_dir.resolve() == old:
        raise SystemExit(
            f"拒绝写入现网目录 {old}。请使用旁路 FAISS_PUBMED_YES_GTE_DIR。"
        )


def _load_manifest(out_dir: Path) -> dict:
    mf = out_dir / "manifest.json"
    if mf.is_file():
        return json.loads(mf.read_text(encoding="utf-8-sig"))
    return {
        "shards": [],
        "shard_info": {},
        "total_papers": 0,
        "total_chunks": 0,
        "index_version": INDEX_VERSION,
        "embedding_model": "Alibaba-NLP/gte-multilingual-base",
        "embedding_dim": EMBEDDING_DIM,
        "status": "building",
    }


def _save_manifest(out_dir: Path, m: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    m["updated_at"] = datetime.now().isoformat(timespec="seconds")
    (out_dir / "manifest.json").write_text(
        json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _iter_corpus(path: Path, *, limit: int = 0, skip: int = 0):
    """流式读语料，避免全库 list() 撑爆 16GB。"""
    n = 0
    seen = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            if seen < skip:
                seen += 1
                continue
            yield json.loads(line)
            n += 1
            if limit and n >= limit:
                break


def _count_corpus_lines(path: Path) -> int:
    n = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def _write_progress(extra: dict) -> None:
    lines = [
        "gte 旁路全库重嵌进度",
        f"更新: {datetime.now().isoformat(timespec='seconds')}",
        f"pid: {os.getpid()}",
        f"out: {extra.get('out_dir', FAISS_PUBMED_YES_GTE_DIR)}",
        f"index_version: {INDEX_VERSION} dim={EMBEDDING_DIM}",
        f"Hub: 仍指旧库（勿切换至验收完成）",
        "",
    ]
    for k, v in extra.items():
        lines.append(f"{k}: {v}")
    lines += [
        "",
        "查日志: logs/gte_reembed_full.log",
        "停进程: taskkill /PID <pid> /F   （或结束下方 python -m scripts.fulltext_oneshot_vectorize）",
        "旧库只读: 禁止写入 faiss_pubmed_yes",
    ]
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_status(extra: dict) -> None:
    lines = [
        "事项一：换模落地状态",
        f"更新: {datetime.now().isoformat(timespec='seconds')}",
        "",
        f"目标模型: Alibaba-NLP/gte-multilingual-base → {GTE_MULTILINGUAL_BASE_PATH}",
        f"旁路目录: {FAISS_PUBMED_YES_GTE_DIR}",
        f"现网目录(只读保留): {FAISS_PUBMED_YES_DIR}",
        f"Hub 默认: 仍指向旧库（未切换）",
        f"index_version: {INDEX_VERSION} dim={EMBEDDING_DIM}",
        "",
        "== 进度 ==",
    ]
    for k, v in extra.items():
        lines.append(f"  {k}: {v}")
    lines += [
        "",
        "== 运维 ==",
        f"  进度文件: {PROGRESS_FILE}",
        f"  日志: logs/gte_reembed_full.log",
        f"  job: {JOB_META}",
        "  停: taskkill /PID <见进度文件 pid> /F",
        "",
        "== 验收后切 Hub（本阶段勿做）==",
        "  1) manifest.embedding_dim==768 且 total_papers≥语料≈98%",
        "  2) 旧 faiss_pubmed_yes 完整未改",
        "  3) 用户确认后改 FAISS_PUBMED_YES_DIR + EMBEDDING_MODEL_PATH 并重启 API",
    ]
    smoke = ROOT / "logs" / "gte_smoke.json"
    if smoke.is_file():
        lines += ["", "== 烟测 ==", smoke.read_text(encoding="utf-8")[:1200]]
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_progress(extra)


def embed_batch(
    *,
    corpus: Path,
    out_dir: Path,
    model_path: str,
    limit: int,
    resume: bool,
    use_sections: bool,
    shard_papers: int,
    embed_batch: int,
) -> dict:
    _forbid_old_dir(out_dir)
    if not corpus.is_file():
        raise FileNotFoundError(f"缺少语料 {corpus}")
    if not Path(model_path).is_dir():
        raise FileNotFoundError(f"缺少模型 {model_path}；先 download_gte_multilingual_base")

    # 静音才降优先级；全速（GTE_REEMBED_FULL=1）保持 Normal
    try:
        import ctypes

        if str(os.environ.get("GTE_REEMBED_FULL", "")).strip() in {"1", "true", "yes", "on"}:
            NORMAL = 0x00000020
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), NORMAL
            )
        else:
            BELOW_NORMAL = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL
            )
    except Exception:
        pass

    manifest = _load_manifest(out_dir)
    done = int(manifest.get("total_papers") or 0) if resume else 0
    corpus_total = _count_corpus_lines(corpus)
    target_total = min(limit, corpus_total) if limit > 0 else corpus_total
    if done >= target_total:
        safe_print("已全部完成（本 limit 范围内）")
        _write_status(
            {
                "papers": manifest.get("total_papers"),
                "chunks": manifest.get("total_chunks"),
                "status": "ready_or_done",
                "out_dir": str(out_dir),
            }
        )
        return manifest

    next_shard = 0
    if manifest.get("shard_info"):
        next_shard = max(int(k) for k in manifest["shard_info"] if str(k).isdigit()) + 1

    # 降低默认 batch，防 16GB OOM
    os.environ["PUBMED_YES_EMBED_BATCH"] = str(embed_batch)
    os.environ.setdefault("PUBMED_YES_SECTION_CHUNKING", "1" if use_sections else "0")
    embedding = BgeEmbedding(model_path)
    # 维数抽检
    probe = embedding.embed_query("zebrafish")
    if len(probe) != EMBEDDING_DIM:
        raise RuntimeError(f"期望 dim={EMBEDDING_DIM}，实际 {len(probe)}")

    job_id = None
    try:
        from literature_store import start_job, upsert_vector_shard, write_sync_log, finish_job
        from mysql_client import MySQLClient

        client = MySQLClient()
        db = next(client.get_db())
        try:
            job_id = start_job(
                db,
                job_type="embed_gte_mbase_v1",
                params={
                    "out_dir": str(out_dir),
                    "model": model_path,
                    "embed_batch": embed_batch,
                    "shard_papers": shard_papers,
                    "sections": use_sections,
                    "resume_from": done,
                    "corpus_total": corpus_total,
                },
                index_version=INDEX_VERSION,
            )
            write_sync_log(
                db,
                action="start_embed_job",
                actor="fulltext_oneshot_vectorize",
                index_version=INDEX_VERSION,
                detail={"job_id": job_id, "pid": os.getpid()},
            )
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()
    except Exception as e:
        safe_print(f"  [警告] MySQL job 登记跳过: {e}")

    t0 = time.perf_counter()
    batch: list[dict] = []
    shard_offset = 0
    to_process = max(0, target_total - done)
    processed = 0

    def _flush_shard(batch_recs: list[dict], shard_id: int) -> None:
        nonlocal manifest
        docs: list[Document] = []
        for rec in batch_recs:
            for ch in split_record_to_field_chunks(
                rec,
                max_body_chars=EXIST_ENO_MAX_BODY_CHARS,
                max_chunks=EXIST_ENO_MAX_CHUNKS,
                para_max_chars=PUBMED_YES_PARA_MAX_CHARS,
                para_min_chars=PUBMED_YES_PARA_MIN_CHARS,
                use_sections=use_sections,
            ):
                docs.append(
                    Document(
                        page_content=ch["text"],
                        metadata={
                            "pmid": ch.get("pmid") or "",
                            "entry_no": ch.get("entry_no") or "",
                            "title_zh": ch.get("title_zh"),
                            "has_fulltext": bool(ch.get("has_fulltext")),
                            "chunk_index": ch.get("chunk_index"),
                            "field": ch.get("field"),
                            "section": ch.get("section") or ch.get("field"),
                            "source": ch.get("source") or "pubmed_yes",
                            "index_version": INDEX_VERSION,
                        },
                    )
                )
        safe_print(f"  shard_{shard_id:04d}: {len(batch_recs)} 篇 → {len(docs)} chunks …")
        vs = FAISS.from_documents(docs, embedding)
        sd = out_dir / f"shard_{shard_id:04d}"
        sd.mkdir(parents=True, exist_ok=True)
        vs.save_local(str(sd))

        manifest.setdefault("shard_info", {})[str(shard_id)] = {
            "paper_count": len(batch_recs),
            "chunk_count": len(docs),
            "kind": "pmid_fulltext_gte",
            "source": "oneshot_gte_mbase_v1",
            "first_pmid": str(batch_recs[0].get("pmid") or ""),
            "last_pmid": str(batch_recs[-1].get("pmid") or ""),
        }
        manifest["shards"] = sorted(int(k) for k in manifest["shard_info"] if str(k).isdigit())
        manifest["total_papers"] = int(manifest.get("total_papers") or 0) + len(batch_recs)
        manifest["total_chunks"] = int(manifest.get("total_chunks") or 0) + len(docs)
        manifest["embedding_model"] = model_path
        manifest["embedding_dim"] = EMBEDDING_DIM
        manifest["index_version"] = INDEX_VERSION
        manifest["chunking"] = {
            "mode": "paragraph_section_aware" if use_sections else "paragraph_field_aware",
            "split": "paragraph",
            "max_body_chars": EXIST_ENO_MAX_BODY_CHARS,
            "max_chunks": EXIST_ENO_MAX_CHUNKS,
            "section_chunking": use_sections,
        }
        manifest["parent_corpus_version"] = "bge_small_v1_readonly"
        manifest["status"] = "building"
        if not manifest.get("started_at"):
            manifest["started_at"] = datetime.now().isoformat(timespec="seconds")
        _save_manifest(out_dir, manifest)

        elapsed = max(0.1, time.perf_counter() - t0)
        rate = processed / elapsed  # papers/sec in this run
        remain = max(0, to_process - processed)
        eta_sec = int(remain / rate) if rate > 0 else -1
        pct = round(100.0 * int(manifest["total_papers"]) / max(1, corpus_total), 2)
        _write_status(
            {
                "papers": manifest["total_papers"],
                "chunks": manifest["total_chunks"],
                "shards": len(manifest["shards"]),
                "corpus_total": corpus_total,
                "pct": f"{pct}%",
                "elapsed_sec": round(elapsed, 1),
                "rate_papers_per_sec": round(rate, 4),
                "eta_sec": eta_sec,
                "eta_human": f"{eta_sec // 3600}h{(eta_sec % 3600) // 60}m" if eta_sec >= 0 else "n/a",
                "out_dir": str(out_dir),
                "sections": use_sections,
                "embed_batch": embed_batch,
                "status": "building",
            }
        )
        try:
            from literature_store import upsert_vector_shard, write_sync_log
            from mysql_client import MySQLClient

            client = MySQLClient()
            db = next(client.get_db())
            try:
                upsert_vector_shard(
                    db,
                    {
                        "shard_id": shard_id,
                        "index_version": INDEX_VERSION,
                        "root_path": str(out_dir),
                        "shard_path": str(sd),
                        "embedding_model": model_path,
                        "embedding_dim": EMBEDDING_DIM,
                        "paper_count": len(batch_recs),
                        "chunk_count": len(docs),
                        "status": "building",
                        "kind": "pmid_fulltext_gte",
                        "manifest_json": manifest["shard_info"][str(shard_id)],
                    },
                )
                write_sync_log(
                    db,
                    action="register_shard",
                    actor="fulltext_oneshot_vectorize",
                    index_version=INDEX_VERSION,
                    detail={"shard_id": shard_id, "papers": len(batch_recs)},
                )
                if job_id is not None:
                    from sqlalchemy import text as sa_text

                    db.execute(
                        sa_text(
                            "UPDATE literature_pipeline_job SET progress=:p WHERE job_id=:j"
                        ),
                        {
                            "p": f"{pct}% papers={manifest['total_papers']}/{corpus_total}",
                            "j": job_id,
                        },
                    )
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
        except Exception as e:
            safe_print(f"  [警告] MySQL 登记跳过: {e}")

    for rec in _iter_corpus(corpus, limit=to_process, skip=done):
        batch.append(rec)
        if len(batch) >= shard_papers:
            shard_id = next_shard + shard_offset
            processed += len(batch)
            _flush_shard(batch, shard_id)
            shard_offset += 1
            batch = []

    if batch:
        shard_id = next_shard + shard_offset
        processed += len(batch)
        _flush_shard(batch, shard_id)

    if job_id is not None:
        try:
            from literature_store import finish_job
            from mysql_client import MySQLClient

            client = MySQLClient()
            db = next(client.get_db())
            try:
                finish_job(db, job_id, status="done", progress="100%")
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
        except Exception:
            pass

    manifest["status"] = "ready" if int(manifest.get("total_papers") or 0) >= corpus_total else "building"
    _save_manifest(out_dir, manifest)
    _write_status(
        {
            "papers": manifest["total_papers"],
            "chunks": manifest["total_chunks"],
            "shards": len(manifest.get("shards") or []),
            "corpus_total": corpus_total,
            "status": manifest["status"],
            "out_dir": str(out_dir),
            "elapsed_sec": round(time.perf_counter() - t0, 1),
        }
    )
    safe_print(
        f"[完成] → {out_dir} papers={manifest['total_papers']} chunks={manifest['total_chunks']}"
    )
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="旁路 gte 重嵌（不覆盖现网 FAISS）")
    ap.add_argument("--limit", type=int, default=30, help="试嵌篇数；--full 时忽略")
    ap.add_argument("--full", action="store_true", help="全库（慢）")
    ap.add_argument("--resume", action="store_true", default=True)
    ap.add_argument("--no-resume", action="store_true")
    ap.add_argument("--out-dir", default="", help="默认 FAISS_PUBMED_YES_GTE_DIR")
    ap.add_argument("--model", default="", help="默认 GTE_MULTILINGUAL_BASE_PATH")
    ap.add_argument("--corpus", default="", help="默认 pubmed_yes_corpus.jsonl")
    ap.add_argument("--no-sections", action="store_true", help="关闭 section 切块")
    ap.add_argument("--shard-papers", type=int, default=min(200, PUBMED_YES_SHARD_PAPERS))
    ap.add_argument("--embed-batch", type=int, default=min(16, PUBMED_YES_EMBED_BATCH))
    args = ap.parse_args()

    out_dir = Path(args.out_dir or FAISS_PUBMED_YES_GTE_DIR)
    model = args.model or GTE_MULTILINGUAL_BASE_PATH
    corpus = Path(args.corpus or PUBMED_YES_CORPUS_JSONL)
    limit = 0 if args.full else max(1, int(args.limit))

    safe_print("=== oneshot / 旁路重嵌 ===")
    safe_print(f"  out={out_dir}")
    safe_print(f"  model={model}")
    safe_print(f"  corpus={corpus} limit={limit or 'ALL'}")
    safe_print(f"  sections={not args.no_sections} batch={args.embed_batch}")
    safe_print(f"  现网旧库保留: {FAISS_PUBMED_YES_DIR}")

    embed_batch(
        corpus=corpus,
        out_dir=out_dir,
        model_path=model,
        limit=limit,
        resume=not args.no_resume,
        use_sections=not args.no_sections,
        shard_papers=max(10, int(args.shard_papers)),
        embed_batch=max(4, int(args.embed_batch)),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
