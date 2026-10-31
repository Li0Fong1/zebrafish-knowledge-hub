# -*- coding: utf-8 -*-
"""工作流持久化：预置模板、运行记录、步骤。"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Optional

from common_utils import logger


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    return obj


def _dumps(obj: Any) -> str:
    return json.dumps(_json_safe(obj), ensure_ascii=False)


def _db():
    from mysql_client import get_mysql_client
    return next(get_mysql_client().get_db())


def seed_presets() -> int:
    from tenant_service import ensure_default_tenant
    from workflow_presets import PRESETS
    from sqlalchemy import text

    ensure_default_tenant()
    db = _db()
    n = 0
    try:
        for p in PRESETS:
            row = db.execute(
                text(
                    "SELECT workflow_id FROM workflow_definition "
                    "WHERE tenant_id IS NULL AND slug=:slug LIMIT 1"
                ),
                {"slug": p["slug"]},
            ).mappings().first()
            def_json = _dumps(p["definition"])
            if row:
                db.execute(
                    text(
                        "UPDATE workflow_definition SET name=:name, description=:desc, "
                        "definition_json=:def, is_preset=1, is_active=1 "
                        "WHERE workflow_id=:wid"
                    ),
                    {
                        "name": p["name"],
                        "desc": p.get("description"),
                        "def": def_json,
                        "wid": row["workflow_id"],
                    },
                )
            else:
                db.execute(
                    text(
                        "INSERT INTO workflow_definition "
                        "(tenant_id, slug, name, description, definition_json, is_preset, is_active) "
                        "VALUES (NULL, :slug, :name, :desc, :def, 1, 1)"
                    ),
                    {
                        "slug": p["slug"],
                        "name": p["name"],
                        "desc": p.get("description"),
                        "def": def_json,
                    },
                )
            n += 1
        db.commit()
        logger.info(f"[workflow] 已同步 {n} 个预置模板")
        return n
    except Exception as e:
        db.rollback()
        logger.warning(f"[workflow] 预置同步失败: {e}")
        raise
    finally:
        db.close()


def list_presets() -> List[dict]:
    from sqlalchemy import text

    db = _db()
    try:
        rows = db.execute(
            text(
                "SELECT workflow_id, slug, name, description, definition_json "
                "FROM workflow_definition WHERE is_preset=1 AND is_active=1 "
                "ORDER BY CASE slug WHEN 'full_hub' THEN 0 ELSE 1 END, workflow_id"
            )
        ).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            d["definition"] = json.loads(d.pop("definition_json") or "{}")
            out.append(d)
        return out
    finally:
        db.close()


def get_preset_by_slug(slug: str) -> Optional[dict]:
    from sqlalchemy import text

    db = _db()
    try:
        row = db.execute(
            text(
                "SELECT workflow_id, slug, name, description, definition_json "
                "FROM workflow_definition WHERE slug=:slug AND is_preset=1 AND is_active=1 LIMIT 1"
            ),
            {"slug": slug},
        ).mappings().first()
        if not row:
            return None
        d = dict(row)
        d["definition"] = json.loads(d.pop("definition_json") or "{}")
        return d
    finally:
        db.close()


def create_run(tenant_id: int, preset_slug: str, input_text: str, workflow_id: Optional[int] = None) -> int:
    from sqlalchemy import text

    db = _db()
    try:
        db.execute(
            text(
                "INSERT INTO workflow_run (tenant_id, workflow_id, preset_slug, input_text, status) "
                "VALUES (:tid, :wid, :slug, :inp, 'pending')"
            ),
            {
                "tid": tenant_id,
                "wid": workflow_id,
                "slug": preset_slug,
                "inp": input_text,
            },
        )
        db.commit()
        run_id = int(db.execute(text("SELECT LAST_INSERT_ID() AS id")).scalar() or 0)
        return run_id
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def mark_run_started(run_id: int) -> None:
    from sqlalchemy import text

    db = _db()
    try:
        db.execute(
            text("UPDATE workflow_run SET status='running', started_at=NOW() WHERE run_id=:id"),
            {"id": run_id},
        )
        db.commit()
    finally:
        db.close()


def save_steps(run_id: int, steps: List[dict]) -> None:
    from sqlalchemy import text

    db = _db()
    try:
        for s in steps:
            db.execute(
                text(
                    "INSERT INTO workflow_run_step "
                    "(run_id, node_id, node_type, label, status, summary, output_json, elapsed_ms, sort_order) "
                    "VALUES (:rid, :nid, :nt, :lb, :st, :sum, :out, :ms, :ord)"
                ),
                {
                    "rid": run_id,
                    "nid": s.get("node_id"),
                    "nt": s.get("node_type"),
                    "lb": s.get("label"),
                    "st": s.get("status"),
                    "sum": s.get("summary"),
                    "out": _dumps(s.get("output")) if s.get("output") else None,
                    "ms": s.get("elapsed_ms", 0),
                    "ord": s.get("sort_order", 0),
                },
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def finish_run(run_id: int, status: str, output: Any, error: Optional[str] = None) -> None:
    from sqlalchemy import text

    db = _db()
    try:
        db.execute(
            text(
                "UPDATE workflow_run SET status=:st, output_json=:out, error_message=:err, "
                "finished_at=NOW() WHERE run_id=:id"
            ),
            {
                "st": status,
                "out": _dumps(output) if output is not None else None,
                "err": error,
                "id": run_id,
            },
        )
        db.commit()
    finally:
        db.close()


def get_run(run_id: int, tenant_id: Optional[int] = None) -> Optional[dict]:
    from sqlalchemy import text

    db = _db()
    try:
        sql = (
            "SELECT run_id, tenant_id, workflow_id, preset_slug, input_text, status, "
            "output_json, error_message, started_at, finished_at, created_at "
            "FROM workflow_run WHERE run_id=:id"
        )
        params: dict = {"id": run_id}
        if tenant_id is not None:
            sql += " AND tenant_id=:tid"
            params["tid"] = tenant_id
        row = db.execute(text(sql), params).mappings().first()
        if not row:
            return None
        run = dict(row)
        if run.get("output_json"):
            run["output"] = json.loads(run["output_json"])
        run.pop("output_json", None)

        steps = db.execute(
            text(
                "SELECT node_id, node_type, label, status, summary, output_json, elapsed_ms, sort_order "
                "FROM workflow_run_step WHERE run_id=:id ORDER BY sort_order"
            ),
            {"id": run_id},
        ).mappings().all()
        run["steps"] = []
        for s in steps:
            sd = dict(s)
            if sd.get("output_json"):
                sd["output"] = json.loads(sd["output_json"])
            sd.pop("output_json", None)
            run["steps"].append(sd)
        return run
    finally:
        db.close()


def list_runs(tenant_id: int, limit: int = 20) -> List[dict]:
    from sqlalchemy import text

    db = _db()
    try:
        rows = db.execute(
            text(
                "SELECT run_id, preset_slug, input_text, status, started_at, finished_at, created_at "
                "FROM workflow_run WHERE tenant_id=:tid ORDER BY run_id DESC LIMIT :lim"
            ),
            {"tid": tenant_id, "lim": limit},
        ).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()
