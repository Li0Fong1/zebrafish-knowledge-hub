# -*- coding: utf-8 -*-
"""多租户：表初始化、默认租户、请求上下文解析。"""
from __future__ import annotations

import os
from typing import Optional

from common_utils import logger

_DEFAULT_SLUG = os.getenv("DEFAULT_TENANT_SLUG", "default")
_SCHEMA_OK = False


def _client():
    from mysql_client import get_mysql_client
    return get_mysql_client()


def _apply_migration() -> bool:
    global _SCHEMA_OK
    from config import _BASE_DIR
    path = os.path.join(_BASE_DIR, "sql", "tenant_workflow_migration.sql")
    if not os.path.isfile(path):
        return False
    with open(path, encoding="utf-8") as f:
        ddl = f.read()
    statements = [s.strip() for s in ddl.split(";") if s.strip() and not s.strip().startswith("--")]
    db = next(_client().get_db())
    try:
        from sqlalchemy import text
        for stmt in statements:
            if stmt.upper().startswith("USE "):
                continue
            db.execute(text(stmt))
        db.commit()
        _SCHEMA_OK = True
        return True
    except Exception as e:
        db.rollback()
        logger.warning(f"[tenant] 迁移失败: {e}")
        return False
    finally:
        db.close()


def ensure_tenant_schema() -> bool:
    global _SCHEMA_OK
    if _SCHEMA_OK:
        return True
    return _apply_migration()


def ensure_default_tenant() -> int:
    ensure_tenant_schema()
    db = next(_client().get_db())
    try:
        from sqlalchemy import text
        row = db.execute(
            text("SELECT tenant_id FROM tenant WHERE slug=:s LIMIT 1"),
            {"s": _DEFAULT_SLUG},
        ).mappings().first()
        if row:
            return int(row["tenant_id"])
        db.execute(
            text(
                "INSERT INTO tenant (slug, name, plan) VALUES (:slug, :name, 'standard')"
            ),
            {"slug": _DEFAULT_SLUG, "name": "默认实验室"},
        )
        db.commit()
        row = db.execute(
            text("SELECT tenant_id FROM tenant WHERE slug=:s LIMIT 1"),
            {"s": _DEFAULT_SLUG},
        ).mappings().first()
        return int(row["tenant_id"])
    finally:
        db.close()


def get_tenant_by_slug(slug: str) -> Optional[dict]:
    if not slug:
        return None
    ensure_tenant_schema()
    db = next(_client().get_db())
    try:
        from sqlalchemy import text
        row = db.execute(
            text(
                "SELECT tenant_id, slug, name, plan, is_active FROM tenant "
                "WHERE slug=:s AND is_active=1 LIMIT 1"
            ),
            {"s": slug.strip().lower()},
        ).mappings().first()
        return dict(row) if row else None
    finally:
        db.close()


def resolve_tenant_id(slug: Optional[str]) -> int:
    if slug:
        t = get_tenant_by_slug(slug)
        if t:
            return int(t["tenant_id"])
    return ensure_default_tenant()
