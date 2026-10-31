# -*- coding: utf-8 -*-
"""可追加文献库：papers / gold / vector_shards / jobs / sync_log 读写辅助。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import text


def make_doc_id(*, pmid: str | None = None, entry_no: str | None = None) -> str:
    p = str(pmid or "").strip()
    e = str(entry_no or "").strip()
    if p and p.isdigit():
        return f"pmid:{p}"
    if e:
        return f"eno:{e}"
    raise ValueError("需要 pmid 或 entry_no")


def upsert_paper(db, rec: dict[str, Any]) -> str:
    pmid = str(rec.get("pmid") or "").strip() or None
    entry_no = str(rec.get("entry_no") or "").strip() or None
    doc_id = str(rec.get("doc_id") or "").strip() or make_doc_id(pmid=pmid, entry_no=entry_no)
    source = str(rec.get("source") or ("pubmed_yes" if pmid else "exist_eno"))
    db.execute(
        text(
            """
            INSERT INTO literature_paper (
                doc_id, pmid, entry_no, doi, pmcid, source,
                title_en, title_zh, year, journal, judgement,
                has_fulltext, gold_status
            ) VALUES (
                :doc_id, :pmid, :entry_no, :doi, :pmcid, :source,
                :title_en, :title_zh, :year, :journal, :judgement,
                :has_fulltext, :gold_status
            )
            ON DUPLICATE KEY UPDATE
                doi=VALUES(doi),
                pmcid=VALUES(pmcid),
                title_en=COALESCE(VALUES(title_en), title_en),
                title_zh=COALESCE(VALUES(title_zh), title_zh),
                year=COALESCE(VALUES(year), year),
                journal=COALESCE(VALUES(journal), journal),
                judgement=COALESCE(VALUES(judgement), judgement),
                has_fulltext=GREATEST(has_fulltext, VALUES(has_fulltext)),
                gold_status=COALESCE(VALUES(gold_status), gold_status),
                updated_at=CURRENT_TIMESTAMP
            """
        ),
        {
            "doc_id": doc_id,
            "pmid": pmid,
            "entry_no": entry_no,
            "doi": (str(rec.get("doi") or "").strip() or None),
            "pmcid": (str(rec.get("pmcid") or "").strip() or None),
            "source": source,
            "title_en": rec.get("title_en"),
            "title_zh": rec.get("title_zh"),
            "year": int(rec["year"]) if str(rec.get("year") or "").isdigit() else None,
            "journal": rec.get("journal"),
            "judgement": rec.get("judgement") or "是",
            "has_fulltext": 1 if rec.get("has_fulltext") else 0,
            "gold_status": rec.get("gold_status"),
        },
    )
    return doc_id


def upsert_fulltext_gold(
    db,
    *,
    doc_id: str,
    gold_version: str = "v1",
    source_tier: str | None = None,
    body_path: str | None = None,
    body_text: str | None = None,
    sections: dict | None = None,
    campaign_id: str | None = None,
) -> None:
    sha = None
    if body_text:
        sha = hashlib.sha256(body_text.encode("utf-8", errors="replace")).hexdigest()
    elif body_path:
        sha = hashlib.sha256(body_path.encode("utf-8")).hexdigest()
    db.execute(
        text(
            """
            INSERT INTO literature_fulltext_gold (
                doc_id, gold_version, source_tier, body_path, body_text,
                sections_json, content_sha256, campaign_id
            ) VALUES (
                :doc_id, :gold_version, :source_tier, :body_path, :body_text,
                :sections_json, :content_sha256, :campaign_id
            )
            ON DUPLICATE KEY UPDATE
                source_tier=COALESCE(VALUES(source_tier), source_tier),
                body_path=COALESCE(VALUES(body_path), body_path),
                body_text=COALESCE(VALUES(body_text), body_text),
                sections_json=COALESCE(VALUES(sections_json), sections_json),
                content_sha256=COALESCE(VALUES(content_sha256), content_sha256),
                campaign_id=COALESCE(VALUES(campaign_id), campaign_id),
                updated_at=CURRENT_TIMESTAMP
            """
        ),
        {
            "doc_id": doc_id,
            "gold_version": gold_version,
            "source_tier": source_tier,
            "body_path": body_path,
            "body_text": body_text,
            "sections_json": json.dumps(sections, ensure_ascii=False) if sections else None,
            "content_sha256": sha,
            "campaign_id": campaign_id,
        },
    )


def upsert_vector_shard(db, row: dict[str, Any]) -> None:
    mj = row.get("manifest_json")
    if isinstance(mj, (dict, list)):
        mj = json.dumps(mj, ensure_ascii=False)
    db.execute(
        text(
            """
            INSERT INTO literature_vector_shard (
                shard_id, index_version, root_path, shard_path,
                embedding_model, embedding_dim, paper_count, chunk_count,
                status, kind, manifest_json
            ) VALUES (
                :shard_id, :index_version, :root_path, :shard_path,
                :embedding_model, :embedding_dim, :paper_count, :chunk_count,
                :status, :kind, :manifest_json
            )
            ON DUPLICATE KEY UPDATE
                root_path=VALUES(root_path),
                shard_path=VALUES(shard_path),
                embedding_model=VALUES(embedding_model),
                embedding_dim=VALUES(embedding_dim),
                paper_count=VALUES(paper_count),
                chunk_count=VALUES(chunk_count),
                status=VALUES(status),
                kind=VALUES(kind),
                manifest_json=VALUES(manifest_json),
                updated_at=CURRENT_TIMESTAMP
            """
        ),
        {
            "shard_id": int(row["shard_id"]),
            "index_version": row["index_version"],
            "root_path": row["root_path"],
            "shard_path": row.get("shard_path"),
            "embedding_model": row["embedding_model"],
            "embedding_dim": int(row["embedding_dim"]),
            "paper_count": int(row.get("paper_count") or 0),
            "chunk_count": int(row.get("chunk_count") or 0),
            "status": row.get("status") or "ready",
            "kind": row.get("kind"),
            "manifest_json": mj,
        },
    )


def start_job(db, *, job_type: str, params: dict | None = None, index_version: str | None = None) -> int:
    r = db.execute(
        text(
            """
            INSERT INTO literature_pipeline_job (job_type, status, progress, params_json, index_version)
            VALUES (:job_type, 'running', :progress, :params_json, :index_version)
            """
        ),
        {
            "job_type": job_type,
            "progress": "0%",
            "params_json": json.dumps(params or {}, ensure_ascii=False),
            "index_version": index_version,
        },
    )
    return int(r.lastrowid)


def finish_job(db, job_id: int, *, status: str = "done", progress: str = "100%", error: str | None = None) -> None:
    db.execute(
        text(
            """
            UPDATE literature_pipeline_job
            SET status=:status, progress=:progress, last_error=:error, finished_at=:finished_at
            WHERE job_id=:job_id
            """
        ),
        {
            "job_id": job_id,
            "status": status,
            "progress": progress,
            "error": error,
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )


def write_sync_log(
    db,
    *,
    action: str,
    actor: str = "system",
    doc_id: str | None = None,
    index_version: str | None = None,
    detail: dict | None = None,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO literature_sync_log (actor, action, doc_id, index_version, detail_json)
            VALUES (:actor, :action, :doc_id, :index_version, :detail_json)
            """
        ),
        {
            "actor": actor,
            "action": action,
            "doc_id": doc_id,
            "index_version": index_version,
            "detail_json": json.dumps(detail or {}, ensure_ascii=False),
        },
    )


def paper_exists(db, doc_id: str) -> bool:
    r = db.execute(text("SELECT 1 FROM literature_paper WHERE doc_id=:d LIMIT 1"), {"d": doc_id}).first()
    return r is not None


def count_shards(db, index_version: str) -> int:
    return int(
        db.execute(
            text("SELECT COUNT(*) FROM literature_vector_shard WHERE index_version=:v"),
            {"v": index_version},
        ).scalar()
        or 0
    )
