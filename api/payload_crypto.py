# -*- coding: utf-8 -*-
"""API 载荷 AES-256-GCM 加解密（防明文抓包分析）。"""
from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Any, Optional, Tuple

from common_utils import logger

_ENC_VERSION = "v1"


def _env_bool(name: str) -> bool:
    return os.getenv(name, "0").lower() in ("1", "true", "yes", "on")


def get_payload_key(token: Optional[str] = None) -> bytes:
    if token:
        return _derive_key_from_token(token)
    raw = os.getenv("WEB_PAYLOAD_KEY", "").strip()
    if raw:
        try:
            key = base64.urlsafe_b64decode(raw + "=="[: (4 - len(raw) % 4) % 4])
        except Exception:
            key = raw.encode("utf-8")
        return key[:32].ljust(32, b"\0")[:32]
    return b""


def _derive_key_from_token(token: str) -> bytes:
    import hashlib
    hint = os.getenv("WEB_ACCESS_TOKEN", "")[:8]
    salt = (os.getenv("WEB_PAYLOAD_SALT", "zfqa-payload-v1") + hint).encode()
    return hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, 12000)[:32]


def encryption_enabled() -> bool:
    return _env_bool("WEB_PAYLOAD_ENCRYPT")


def encrypt_json(obj: Any, token: Optional[str] = None) -> dict:
    key = get_payload_key(token)
    if not key:
        raise RuntimeError("无法派生载荷密钥")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise RuntimeError("请安装 cryptography：pip install cryptography") from e

    plaintext = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    nonce = secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return {
        "enc": _ENC_VERSION,
        "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
        "payload": base64.urlsafe_b64encode(ct).decode("ascii"),
    }


def decrypt_envelope(body: dict, token: Optional[str] = None) -> Tuple[Optional[Any], Optional[str]]:
    if not isinstance(body, dict) or body.get("enc") != _ENC_VERSION:
        return None, "无效的加密信封"
    key = get_payload_key(token)
    if not key:
        return None, "服务端未启用载荷加密"
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = base64.urlsafe_b64decode(body["nonce"] + "==")
        ct = base64.urlsafe_b64decode(body["payload"] + "==")
        plain = AESGCM(key).decrypt(nonce, ct, None)
        return json.loads(plain.decode("utf-8")), None
    except Exception as e:
        logger.warning(f"[加密] 解密失败: {e}")
        return None, "载荷解密失败"


def maybe_decrypt_body(raw: bytes, content_type: str, token: Optional[str] = None) -> Tuple[bytes, Optional[str]]:
    """若 body 为加密 JSON 信封则解密为明文 JSON bytes。"""
    if not encryption_enabled():
        return raw, None
    if "json" not in (content_type or "").lower():
        return raw, None
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw, None
    if not isinstance(body, dict) or body.get("enc") != _ENC_VERSION:
        return raw, None
    obj, err = decrypt_envelope(body, token)
    if err:
        return raw, err
    return json.dumps(obj, ensure_ascii=False).encode("utf-8"), None


def maybe_encrypt_response(data: Any, path: str, token: Optional[str] = None) -> Any:
    if not encryption_enabled():
        return data
    if not path.startswith("/api/chat"):
        return data
    try:
        return encrypt_json(data, token)
    except Exception as e:
        logger.warning(f"[加密] 响应加密失败，回退明文: {e}")
        return data
