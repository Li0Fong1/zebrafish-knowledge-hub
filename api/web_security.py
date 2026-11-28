# -*- coding: utf-8 -*-
"""Web 安全：鉴权、限流、路径防护、响应头、CORS、请求体上限。"""
from __future__ import annotations

import os
import re
import secrets
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple
from urllib.parse import unquote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from common_utils import logger

try:  # 以脚本方式启动（api/ 在 sys.path）
    from api_gate import check_request as gate_check_request
    from api_gate import gate_status as gate_status_info
except ImportError:  # 以包方式导入
    from .api_gate import check_request as gate_check_request
    from .api_gate import gate_status as gate_status_info

_TOKEN_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs",
    "web_access_token.txt",
)

_ALLOWED_METHODS = frozenset({"GET", "POST", "HEAD", "OPTIONS"})
_SUSPICIOUS_PATH = re.compile(
    r"(\.\.|%2e%2e|%252e|\\x00|%00|/etc/passwd|/proc/|\.env|\.git|web\.config)",
    re.I,
)
# /static 直接挂的是整个 web/ 目录，历史上那里堆过 index.html.bak-fish、
# index.src.html、_probe_*.html 这类工作文件。这些不是机密，但暴露了源码
# 结构与调试入口，属于白白增加的攻击面。这里按后缀/前缀直接拒掉，避免以后
# 有人顺手把备份文件扔进 web/ 就公网可读。
_STATIC_DENY = re.compile(
    r"(\.(bak|orig|old|swp|swo|tmp|map|log|sql|py|sh)$"
    r"|\.bak[-\w.]*$"
    r"|\.src\.html$"
    r"|/_probe)",
    re.I,
)


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, "1" if default else "0").lower()
    return v in ("1", "true", "yes", "on")


def is_public_open() -> bool:
    """WEB_AUTH_REQUIRED=0 时公网免令牌（即使 .env 里仍留着旧 token）。"""
    return os.getenv("WEB_AUTH_REQUIRED", "0").strip().lower() in ("0", "false", "off", "no")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def is_strict_mode() -> bool:
    return _env_bool("WEB_STRICT") or _env_bool("PUBLIC_TUNNEL")


def _ensure_access_token() -> str:
    token = os.getenv("WEB_ACCESS_TOKEN", "").strip()
    if token:
        return token
    if not _env_bool("PUBLIC_TUNNEL") and not _env_bool("WEB_AUTH_REQUIRED"):
        return ""
    os.makedirs(os.path.dirname(_TOKEN_FILE), exist_ok=True)
    if os.path.isfile(_TOKEN_FILE):
        with open(_TOKEN_FILE, encoding="utf-8") as f:
            saved = f.read().strip()
            if saved:
                os.environ["WEB_ACCESS_TOKEN"] = saved
                logger.info(f"[安全] 已加载访问令牌（见 {_TOKEN_FILE}）")
                return saved
    token = secrets.token_urlsafe(24)
    with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    os.environ["WEB_ACCESS_TOKEN"] = token
    logger.warning(f"[安全] 已自动生成访问令牌 → {_TOKEN_FILE}")
    return token


class _RateLimiter:
    def __init__(self):
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._auth_fails: Dict[str, Deque[float]] = defaultdict(deque)
        self._banned_until: Dict[str, float] = {}

    def _prune(self, dq: Deque[float], window: float, now: float) -> None:
        while dq and now - dq[0] > window:
            dq.popleft()

    def is_banned(self, key: str) -> Tuple[bool, int]:
        now = time.time()
        until = self._banned_until.get(key, 0)
        if until > now:
            return True, int(until - now)
        return False, 0

    def check(self, key: str, limit: int, window: float = 60.0) -> Tuple[bool, int]:
        now = time.time()
        banned, retry = self.is_banned(key.split(":")[0])
        if banned:
            return False, retry
        dq = self._hits[key]
        self._prune(dq, window, now)
        if len(dq) >= limit:
            # 返回窗口滑动后还需等待的秒数（避免 retry=0 显示含糊的「请稍后再试」）
            oldest = dq[0] if dq else now
            retry_sec = max(1, int(window - (now - oldest)) + 1)
            return False, retry_sec
        dq.append(now)
        return True, 0

    def clear_bucket(self, key: str) -> None:
        self._hits.pop(key, None)

    def record_auth_fail(self, key: str) -> None:
        now = time.time()
        dq = self._auth_fails[key]
        self._prune(dq, 300.0, now)
        dq.append(now)
        max_fails = _env_int("WEB_AUTH_FAIL_MAX", 25)
        if len(dq) >= max_fails:
            ban_sec = _env_int("WEB_AUTH_BAN_SECONDS", 900)
            self._banned_until[key] = now + ban_sec
            logger.warning(f"[安全] IP {key} 鉴权失败过多，封禁 {ban_sec}s")

    def clear_auth_fails(self, key: str) -> None:
        self._auth_fails.pop(key, None)


_limiter = _RateLimiter()
_ACCESS_TOKEN = ""
_ROLE_TOKENS: Dict[str, str] = {}


def _load_role_tokens() -> Dict[str, str]:
    """admin > upload > readonly。同一令牌可只配置 WEB_ACCESS_TOKEN（admin）。"""
    roles: Dict[str, str] = {}
    admin = os.getenv("WEB_ACCESS_TOKEN", "").strip()
    if admin:
        roles["admin"] = admin
    readonly = os.getenv("WEB_TOKEN_READONLY", "").strip()
    if readonly:
        roles["readonly"] = readonly
    upload = os.getenv("WEB_TOKEN_UPLOAD", "").strip()
    if upload:
        roles["upload"] = upload
    return roles


def resolve_token_role(token: str) -> Optional[str]:
    if not token:
        return None
    for role in ("admin", "upload", "readonly"):
        expected = _ROLE_TOKENS.get(role, "")
        if expected and secrets.compare_digest(token, expected):
            return role
    return None


def role_can_upload(role: str) -> bool:
    return role in ("admin", "upload")


def role_can_admin(role: str) -> bool:
    return role == "admin"


def get_access_token() -> str:
    global _ACCESS_TOKEN, _ROLE_TOKENS
    if is_public_open():
        return ""
    if not _ACCESS_TOKEN:
        _ACCESS_TOKEN = _ensure_access_token()
        _ROLE_TOKENS = _load_role_tokens()
        if _ACCESS_TOKEN and "admin" not in _ROLE_TOKENS:
            _ROLE_TOKENS["admin"] = _ACCESS_TOKEN
    return _ACCESS_TOKEN


def get_cors_origins() -> list:
    raw = os.getenv("WEB_CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    if is_strict_mode():
        return []  # 无 Origin 反射，仅同源
    return ["*"]


def auth_config() -> dict:
    from file_security import upload_limits, uploads_enabled
    get_access_token()
    try:
        from payload_crypto import encryption_enabled
        enc_on = encryption_enabled()
    except ImportError:
        enc_on = False

    token_required = False if is_public_open() else (
        bool(get_access_token()) or _env_bool("WEB_AUTH_REQUIRED")
    )
    roles_cfg = []
    if _ROLE_TOKENS.get("admin") or get_access_token():
        roles_cfg.append("admin")
    if _ROLE_TOKENS.get("readonly"):
        roles_cfg.append("readonly")
    if _ROLE_TOKENS.get("upload"):
        roles_cfg.append("upload")
    return {
        "auth_required": token_required,
        "strict_mode": is_strict_mode(),
        "uploads_enabled": uploads_enabled(),
        "rate_limit_chat_per_min": _env_int("WEB_RATE_LIMIT_CHAT", 30),
        "rate_limit_tools_per_min": _env_int("WEB_RATE_LIMIT_TOOLS", 30),
        "rate_limit_api_per_min": _env_int("WEB_RATE_LIMIT_API", 120),
        "rate_limit_upload_per_min": _env_int("WEB_RATE_LIMIT_UPLOAD", 5),
        "upload_limits": upload_limits(),
        "payload_encrypt": enc_on,
        "payload_key_required": enc_on,
        "roles_configured": roles_cfg,
    }


def hub_credential_slot(request: Request) -> tuple[bool, int]:
    """注册/登录共用：每 IP 每分钟最多 20 次。"""
    return _limiter.check(f"{_client_ip(request)}:hub_cred", 20, 60.0)


def note_hub_login_fail(request: Request) -> None:
    _limiter.record_auth_fail(_client_ip(request))


def note_hub_login_ok(request: Request) -> None:
    _limiter.clear_auth_fails(_client_ip(request))


def _client_ip(request: Request) -> str:
    """真实客户端 IP：优先 Cloudflare 写入的 CF-Connecting-IP。

    2026-11-09 修：原先非严格模式直接信 X-Forwarded-For，而这个头客户端
    可任意伪造 —— 换一个假 IP 就能绕过按 IP 的限流与封禁。源站只监听
    127.0.0.1、只经 Cloudflare 隧道对外，CF-Connecting-IP 由 Cloudflare
    在入口覆盖写入，外界无法伪造，是唯一可信来源。
    """
    cf_ip = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf_ip:
        return cf_ip
    if is_strict_mode():
        return request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _extract_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if is_strict_mode():
        return request.headers.get("x-access-token", "").strip()
    return (
        request.headers.get("x-access-token", "")
        or request.query_params.get("access_token", "")
        or ""
    ).strip()


def _path_limit(path: str) -> int:
    if path.startswith("/api/gate/unlock"):
        return _env_int("WEB_RATE_LIMIT_GATE", 8)  # 防密码爆破
    if path.startswith("/api/knowledge/upload"):
        return _env_int("WEB_RATE_LIMIT_UPLOAD", 5)
    if path.startswith("/api/chat"):
        return _env_int("WEB_RATE_LIMIT_CHAT", 30)
    if path.startswith("/api/tools/"):
        return _env_int("WEB_RATE_LIMIT_TOOLS", 30)
    if path.startswith("/api/"):
        return _env_int("WEB_RATE_LIMIT_API", 120)
    return 200


def _path_is_safe(path: str) -> bool:
    decoded = unquote(path)
    if _SUSPICIOUS_PATH.search(decoded):
        return False
    if _STATIC_DENY.search(decoded):
        return False
    if ".." in decoded or "\\" in decoded:
        return False
    return True


class RequestGuardMiddleware(BaseHTTPMiddleware):
    """方法白名单、路径过滤、请求体大小上限。"""

    async def dispatch(self, request: Request, call_next):
        if request.method not in _ALLOWED_METHODS:
            return JSONResponse({"detail": "不允许的请求方法"}, status_code=405)

        path = request.url.path
        if not _path_is_safe(path):
            logger.warning(f"[安全] 拦截可疑路径：{path} from {_client_ip(request)}")
            return JSONResponse({"detail": "非法请求路径"}, status_code=400)

        cl = request.headers.get("content-length")
        if cl:
            try:
                size = int(cl)
            except ValueError:
                return JSONResponse({"detail": "无效的 Content-Length"}, status_code=400)
            max_body = _env_int("WEB_MAX_BODY_BYTES", 600_000)
            if path.startswith("/api/knowledge/upload"):
                max_body = _env_int("WEB_UPLOAD_MAX_BYTES", 5 * 1024 * 1024) + 65536
            if size > max_body:
                return JSONResponse({"detail": "请求体过大"}, status_code=413)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), payment=(), usb=(), "
            "magnetometer=(), gyroscope=(), accelerometer=()"
        )
        response.headers["X-XSS-Protection"] = "0"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"

        # 2026-11-12 安全加固：
        # · HSTS 从 180 天提到 1 年并加 includeSubDomains —— 全站（含 www）只经
        #   Cloudflare 的 HTTPS 对外，不存在明文回退路径，没有理由再留短有效期。
        #   暂不加 preload：preload 列表移除要走数周流程，域名将来若要迁到
        #   非 HTTPS 环境会被锁死，作为一个长期权衡先不开。
        # · COOP/CORP 防止跨站窗口引用与资源盗链（本站不对外提供素材）。
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-site"

        _csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        if is_strict_mode():
            response.headers["Content-Security-Policy"] = _csp
            response.headers["Cache-Control"] = "no-store"
        else:
            # 非严格模式先用「只报告不拦截」上线 CSP：浏览器只在控制台报违规，
            # 页面照常渲染，确认没有误伤后再改成强制模式（把 -Report-Only 去掉）。
            response.headers["Content-Security-Policy-Report-Only"] = _csp
        return response


class PayloadCryptoMiddleware(BaseHTTPMiddleware):
    """解密请求 JSON 载荷；/api/chat 响应可选加密。"""

    async def dispatch(self, request: Request, call_next):
        try:
            from payload_crypto import encryption_enabled, maybe_decrypt_body, maybe_encrypt_response
        except ImportError:
            return await call_next(request)

        if encryption_enabled() and request.method == "POST":
            ct = request.headers.get("content-type", "")
            if "application/json" in ct.lower():
                body = await request.body()
                tok = _extract_token(request)
                new_body, err = maybe_decrypt_body(body, ct, tok or None)
                if err:
                    return JSONResponse({"detail": err}, status_code=400)
                if new_body != body:
                    async def receive():
                        return {"type": "http.request", "body": new_body, "more_body": False}
                    request = Request(request.scope, receive)

        response = await call_next(request)
        if not encryption_enabled():
            return response
        path = request.url.path
        if path != "/api/chat" or response.status_code != 200:
            return response
        try:
            import json
            from starlette.responses import JSONResponse as JR

            if hasattr(response, "body"):
                raw = response.body
            else:
                return response
            data = json.loads(raw.decode("utf-8"))
            tok = _extract_token(request)
            enc = maybe_encrypt_response(data, path, tok or None)
            if enc is not data:
                return JR(enc, status_code=200, headers=dict(response.headers))
        except Exception:
            pass
        return response


class WebSecurityMiddleware(BaseHTTPMiddleware):
    """令牌验证 + 按 IP 限流 + 封禁。"""

    # health 高频轮询不计入限流，避免预热期把桶打满后误伤 /api/chat
    # /api/gate/pass 是基础设施端点（只下发通行证 cookie），不该被业务限流
    # 拦住，否则「补票失败 → 所有接口继续 401」会变成一个死结。
    _PUBLIC_PATHS = frozenset({"/api/auth/config", "/api/health", "/api/gate/pass"})
    _RATE_EXEMPT_PATHS = frozenset({"/api/health", "/api/auth/config", "/api/gate/pass"})

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        ip = _client_ip(request)
        banned, retry = _limiter.is_banned(ip)
        if banned:
            return JSONResponse(
                {"detail": f"IP 已被临时封禁，请 {retry}s 后重试"},
                status_code=403,
            )

        if path not in self._RATE_EXEMPT_PATHS:
            bucket = path.split("/")[2] if path.count("/") > 2 else "api"
            limit = _path_limit(path)
            ok, retry = _limiter.check(f"{ip}:{bucket}", limit)
            if not ok:
                # 对话桶打满时：若上一问仍在处理，优先提示忙碌而非「过于频繁」
                if path.startswith("/api/chat"):
                    try:
                        from agent_scheduler import chat_busy_status
                        st = chat_busy_status() or {}
                        if st.get("chat_busy"):
                            sec = st.get("chat_busy_seconds") or 0
                            queued = int(st.get("chat_queued") or 0)
                            extra = f"，当前 {queued} 位在排队" if queued else ""
                            return JSONResponse(
                                {
                                    "detail": (
                                        f"服务并行已满（{st.get('chat_max_concurrency') or '-'} 条同时问答）"
                                        f"，本次请求需排队{extra}；不是限流。"
                                    )
                                    if sec or queued
                                    else "服务繁忙，请稍候再发；不是限流。"
                                },
                                status_code=503,
                            )
                    except Exception:
                        pass
                msg = f"请求过于频繁，请 {retry}s 后重试" if retry else "请求过于频繁，请稍后再试"
                return JSONResponse({"detail": msg}, status_code=429)

        token_required = False if is_public_open() else (
            bool(get_access_token()) or _env_bool("WEB_AUTH_REQUIRED")
        )
        if token_required and path not in self._PUBLIC_PATHS:
            supplied = _extract_token(request)
            role = resolve_token_role(supplied) if supplied else None
            if not role:
                _limiter.record_auth_fail(ip)
                return JSONResponse(
                    {"detail": "需要有效访问令牌（Header: X-Access-Token）"},
                    status_code=401,
                )
            _limiter.clear_auth_fails(ip)
            request.state.auth_role = role

            if path.startswith("/api/knowledge/upload") and not role_can_upload(role):
                return JSONResponse(
                    {"detail": "当前令牌无上传权限（需 admin 或 upload 角色）"},
                    status_code=403,
                )
            if path.startswith("/api/usage") and not role_can_admin(role):
                return JSONResponse({"detail": "需要管理员令牌"}, status_code=403)
        else:
            request.state.auth_role = "admin"

        # --- API 出口密码锁 ---
        # 放在限流之后：暴力试密码会被上面的 IP 封禁机制拦住。
        # 浏览器带签名通行证自动放行（功能零改动）；无通行证的直连必须给 X-API-Key。
        gate_ok, gate_detail, gate_code = gate_check_request(request, path)
        if not gate_ok:
            _limiter.record_auth_fail(ip)
            return JSONResponse(
                {"detail": gate_detail, "code": gate_code},
                status_code=401,
            )
        if gate_ok:
            request.state.api_gate = "pass"

        if path.startswith("/api/knowledge/upload"):
            from file_security import uploads_enabled
            if not uploads_enabled():
                return JSONResponse({"detail": "文件上传已禁用"}, status_code=403)

        return await call_next(request)
