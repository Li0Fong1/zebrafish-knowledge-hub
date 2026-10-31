# -*- coding: utf-8 -*-
"""API 出口密码锁：站点通行证 cookie + X-API-Key 双通道。

设计目标（三条必须同时成立）：

1. **浏览器功能零改动**：打开首页时服务端下发一枚 HMAC 签名通行证（HttpOnly cookie），
   页面内所有同源 ``fetch`` 自动携带，无需前端手写任何 header，也不会弹任何密码框。
2. **直连必须带密码**：任何不带通行证的 ``/api/*`` 调用（curl、脚本、第三方集成、
   爬虫）必须提供 ``X-API-Key``（或 ``Authorization: Bearer``、``?api_key=``），
   否则返回 401 并计入失败封禁。
3. **绝不阻断功能**：密码只从 ``.env`` / 环境变量读取；缺失或格式异常时自动降级为
   “未启用”，只记一条警告，服务照常启动 —— 安全策略不该把站点锁死。

密码明文不出现在源码、日志、任何 GET 响应中；比较一律用 ``secrets.compare_digest``。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Optional, Tuple

from common_utils import logger

COOKIE_NAME = "zf_api_pass"
_PASS_VERSION = "v1"
_DEFAULT_TTL = 86400  # 24 小时

# 无需密码即可访问的端点：探活、前端启动配置、解锁入口本身
_EXEMPT_PATHS = frozenset(
    {
        "/api/health",
        "/api/auth/config",
        "/api/gate/unlock",
        "/api/gate/status",
    }
)

_ENV_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"
)

_cached_key: Optional[str] = None
_warned = False


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _read_env_file(name: str) -> str:
    """兜底：.env 还没被注入环境变量时直接读文件（只读指定键）。"""
    try:
        with open(_ENV_FILE, encoding="utf-8-sig", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def gate_key() -> str:
    """密码锁密钥；空字符串表示未配置。"""
    global _cached_key
    if _cached_key is None:
        _cached_key = _env("API_GATE_KEY") or _read_env_file("API_GATE_KEY")
    return _cached_key


def gate_enabled() -> bool:
    """总开关。``API_GATE_ENABLED=0`` 可整体关闭（排障用），密钥缺失时自动不启用。"""
    if not _env_bool("API_GATE_ENABLED", True):
        return False
    return bool(gate_key())


def _secret() -> bytes:
    raw = _env("API_GATE_SECRET") or _read_env_file("API_GATE_SECRET")
    if not raw:
        raw = gate_key()
    return hmac.new(raw.encode("utf-8"), b"zf-api-gate-v1", hashlib.sha256).digest()


def _fingerprint(request) -> str:
    """可选指纹绑定（默认关）：开了以后换浏览器/改 UA 需要重新打开页面。"""
    if not _env_bool("API_GATE_BIND_UA", False):
        return "-"
    ua = (request.headers.get("user-agent") or "")[:256]
    return hashlib.sha256(ua.encode("utf-8", "ignore")).hexdigest()[:8]


def issue_pass(request) -> str:
    """签发通行证字符串：``v1.<过期时间戳>.<签名>``。"""
    ttl = max(60, _env_int("API_GATE_PASS_TTL", _DEFAULT_TTL))
    exp = int(time.time()) + ttl
    fp = _fingerprint(request)
    mac = hmac.new(
        _secret(), f"{_PASS_VERSION}|{exp}|{fp}".encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    return f"{_PASS_VERSION}.{exp}.{mac}"


def _mac_for(exp: int, fp: str) -> str:
    return hmac.new(
        _secret(), f"{_PASS_VERSION}|{exp}|{fp}".encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]


def _verify_pass(value: str, request, allow_unbound: bool = False) -> bool:
    if not value or value.count(".") != 2:
        return False
    ver, exp_raw, mac = value.split(".")
    if ver != _PASS_VERSION:
        return False
    try:
        exp = int(exp_raw)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    candidates = [_mac_for(exp, _fingerprint(request))]
    if allow_unbound:  # /api/gate/unlock 签发的票不绑浏览器指纹
        candidates.append(_mac_for(exp, "-"))
    return any(secrets_compare(mac, c) for c in candidates)


def secrets_compare(a: str, b: str) -> bool:
    import secrets

    try:
        return secrets.compare_digest(a, b) is True
    except TypeError:
        return False


def _provided_password(request) -> str:
    """从几种常见位置取调用方提供的密码。"""
    key = (
        request.headers.get("x-api-key", "")
        or request.headers.get("x-gate-key", "")
        or request.query_params.get("api_key", "")
        or request.query_params.get("access_token", "")
        or request.headers.get("x-access-token", "")
    ).strip()
    if key:
        return key
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def is_exempt(path: str) -> bool:
    return path in _EXEMPT_PATHS or path.startswith("/api/gate/")


def check_request(request, path: str) -> Tuple[bool, str, str]:
    """返回 ``(是否放行, 提示文案, 错误码)``。未启用时永远放行。"""
    if not gate_enabled():
        global _warned
        if not _warned and not gate_key():
            _warned = True
            logger.warning(
                "[安全] 未配置 API_GATE_KEY，API 密码锁未启用（页面与接口均自由访问）"
            )
        return True, "", ""

    if is_exempt(path):
        return True, "", ""

    supplied = _provided_password(request)
    if supplied and (
        secrets_compare(supplied, gate_key())
        or _verify_pass(supplied, request, allow_unbound=True)
    ):
        return True, "", ""

    cookie = request.cookies.get(COOKIE_NAME, "")
    if cookie and _verify_pass(cookie, request):
        return True, "", ""

    if supplied:
        return (
            False,
            "API 密钥不正确（Header: X-API-Key）",
            "api_key_invalid",
        )
    return (
        False,
        "此接口需要 API 密钥：请携带 X-API-Key，或先在浏览器中打开首页获取通行证",
        "api_key_required",
    )


def set_pass_cookie(response, request) -> None:
    """首页响应时下发通行证；仅在启用时设置。"""
    if not gate_enabled():
        return
    ttl = max(60, _env_int("API_GATE_PASS_TTL", _DEFAULT_TTL))
    secure = (request.url.scheme or "").lower() == "https"
    response.set_cookie(
        COOKIE_NAME,
        issue_pass(request),
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def unlock(password: str) -> Optional[str]:
    """用密码换取通行证字符串（供脚本/第三方集成使用）。密码错误返回 None。"""
    if not gate_enabled():
        return ""
    if not password or not secrets_compare(password.strip(), gate_key()):
        return None
    exp = int(time.time()) + max(60, _env_int("API_GATE_PASS_TTL", _DEFAULT_TTL))
    mac = hmac.new(
        _secret(), f"{_PASS_VERSION}|{exp}|-".encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    return f"{_PASS_VERSION}.{exp}.{mac}"


def gate_status() -> dict:
    """对外可公开的状态（不含密钥本身）。"""
    return {
        "enabled": gate_enabled(),
        "cookie_name": COOKIE_NAME,
        "pass_ttl_seconds": max(60, _env_int("API_GATE_PASS_TTL", _DEFAULT_TTL)),
        "key_header": "X-API-Key",
        "exempt_paths": sorted(_EXEMPT_PATHS),
    }


def masked_key() -> str:
    """给日志用的掩码，例如 ``de****56``。"""
    k = gate_key()
    if not k:
        return "(未配置)"
    if len(k) <= 4:
        return "*" * len(k)
    return f"{k[:2]}{'*' * max(1, len(k) - 4)}{k[-2:]}"
