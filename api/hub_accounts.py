# -*- coding: utf-8 -*-
"""Hub 可选账号：注册 / 登录 / 会话。快速入门（guest）不走账号也可全功能。"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from mysql_client import MySQLClient

_USER_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fff]{2,32}$")
_SESSION_DAYS = 30


def _ensure_schema(db) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS hub_user (
                user_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(64) NOT NULL,
                password_hash VARCHAR(128) NOT NULL,
                salt VARCHAR(64) NOT NULL,
                display_name VARCHAR(64) NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login_at DATETIME NULL,
                UNIQUE KEY uk_username (username)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS hub_user_session (
                session_token CHAR(64) NOT NULL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NOT NULL,
                KEY idx_user (user_id),
                KEY idx_exp (expires_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS hub_user_conversations (
                user_id BIGINT NOT NULL PRIMARY KEY,
                payload MEDIUMTEXT NOT NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    )
    db.commit()


def _hash_password(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    )
    return digest.hex()


def _validate_username(username: str) -> str:
    u = (username or "").strip()
    if not _USER_RE.match(u):
        raise ValueError("用户名需 2～32 位（字母/数字/下划线/中文）")
    return u


def _validate_password(password: str) -> str:
    p = password or ""
    if len(p) < 6 or len(p) > 72:
        raise ValueError("密码需 6～72 位")
    return p


def _issue_session(db, row) -> dict[str, Any]:
    token = secrets.token_hex(32)
    expires = datetime.now() + timedelta(days=_SESSION_DAYS)
    uid = int(row["user_id"])
    db.execute(
        text(
            "INSERT INTO hub_user_session (session_token, user_id, expires_at) "
            "VALUES (:t, :uid, :exp)"
        ),
        {"t": token, "uid": uid, "exp": expires.strftime("%Y-%m-%d %H:%M:%S")},
    )
    db.execute(
        text("UPDATE hub_user SET last_login_at=NOW() WHERE user_id=:uid"),
        {"uid": uid},
    )
    return {
        "session_token": token,
        "expires_at": expires.isoformat(timespec="seconds"),
        "user": {
            "user_id": uid,
            "username": row["username"],
            "display_name": row["display_name"] or row["username"],
        },
    }


def register_user(username: str, password: str, display_name: str | None = None) -> dict[str, Any]:
    """注册并直接签发会话，避免注册成功后再登录一次。"""
    username = _validate_username(username)
    password = _validate_password(password)
    dn = (display_name or "").strip()[:64] or username
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)
    client = MySQLClient()
    db = next(client.get_db())
    try:
        _ensure_schema(db)
        exists = db.execute(
            text("SELECT user_id FROM hub_user WHERE LOWER(username)=LOWER(:u) LIMIT 1"),
            {"u": username},
        ).first()
        if exists:
            raise ValueError("用户名已被注册")
        db.execute(
            text(
                "INSERT INTO hub_user (username, password_hash, salt, display_name) "
                "VALUES (:u, :h, :s, :d)"
            ),
            {"u": username, "h": pw_hash, "s": salt, "d": dn},
        )
        row = db.execute(
            text("SELECT user_id, username, display_name FROM hub_user WHERE username=:u"),
            {"u": username},
        ).mappings().first()
        sess = _issue_session(db, row)
        db.commit()
        return sess
    except IntegrityError:
        db.rollback()
        raise ValueError("用户名已被注册")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def login_user(username: str, password: str) -> dict[str, Any]:
    username = _validate_username(username)
    password = _validate_password(password)
    client = MySQLClient()
    db = next(client.get_db())
    try:
        _ensure_schema(db)
        row = db.execute(
            text(
                "SELECT user_id, username, display_name, password_hash, salt "
                "FROM hub_user WHERE LOWER(username)=LOWER(:u) LIMIT 1"
            ),
            {"u": username},
        ).mappings().first()
        if not row:
            raise ValueError("用户名或密码错误")
        calc = _hash_password(password, row["salt"])
        if not hmac.compare_digest(calc, row["password_hash"]):
            raise ValueError("用户名或密码错误")
        sess = _issue_session(db, row)
        db.commit()
        return sess
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def logout_session(token: str) -> None:
    if not token:
        return
    client = MySQLClient()
    db = next(client.get_db())
    try:
        _ensure_schema(db)
        db.execute(text("DELETE FROM hub_user_session WHERE session_token=:t"), {"t": token})
        db.commit()
    finally:
        db.close()


def user_from_session(token: str) -> Optional[dict[str, Any]]:
    if not token:
        return None
    client = MySQLClient()
    db = next(client.get_db())
    try:
        _ensure_schema(db)
        row = db.execute(
            text(
                """
                SELECT u.user_id, u.username, u.display_name, s.expires_at
                FROM hub_user_session s
                JOIN hub_user u ON u.user_id=s.user_id
                WHERE s.session_token=:t
                LIMIT 1
                """
            ),
            {"t": token},
        ).mappings().first()
        if not row:
            return None
        exp = row["expires_at"]
        if isinstance(exp, str):
            exp = datetime.fromisoformat(exp.replace(" ", "T"))
        if exp < datetime.now():
            db.execute(text("DELETE FROM hub_user_session WHERE session_token=:t"), {"t": token})
            db.commit()
            return None
        return {
            "user_id": int(row["user_id"]),
            "username": row["username"],
            "display_name": row["display_name"] or row["username"],
        }
    finally:
        db.close()


def load_user_conversations(user_id: int) -> Optional[dict[str, Any]]:
    """返回 {activeId, conversations} 或 None。"""
    uid = int(user_id)
    client = MySQLClient()
    db = next(client.get_db())
    try:
        _ensure_schema(db)
        row = db.execute(
            text("SELECT payload FROM hub_user_conversations WHERE user_id=:uid LIMIT 1"),
            {"uid": uid},
        ).mappings().first()
        if not row or not row.get("payload"):
            return None
        import json

        data = json.loads(row["payload"])
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None
    finally:
        db.close()


def save_user_conversations(user_id: int, payload: dict[str, Any]) -> None:
    """按 user_id 覆盖保存对话（专属隔离）。"""
    import json

    uid = int(user_id)
    # 限体量：最多 40 会话，单条消息截断由前端保证
    convs = payload.get("conversations") or []
    if not isinstance(convs, list):
        raise ValueError("conversations 格式无效")
    slim = {
        "activeId": payload.get("activeId"),
        "conversations": convs[:40],
    }
    raw = json.dumps(slim, ensure_ascii=False)
    if len(raw.encode("utf-8")) > 4_500_000:
        raise ValueError("对话数据过大，请删除部分历史后再同步")
    client = MySQLClient()
    db = next(client.get_db())
    try:
        _ensure_schema(db)
        db.execute(
            text(
                """
                INSERT INTO hub_user_conversations (user_id, payload, updated_at)
                VALUES (:uid, :p, NOW())
                ON DUPLICATE KEY UPDATE payload=:p, updated_at=NOW()
                """
            ),
            {"uid": uid, "p": raw},
        )
        db.commit()
    finally:
        db.close()
