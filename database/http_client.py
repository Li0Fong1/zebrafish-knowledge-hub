# -*- coding: utf-8 -*-
"""集中式外部 HTTP 客户端：统一处理 禁代理 / 短超时 / 有界重试 / 审计。

所有新增的外部 API 客户端（NCBI eutils、QuickGO、Reactome、KEGG REST、
Ensembl REST 等）都必须经由本模块发起请求，禁止在各文件里各自手写
``urllib.request.urlopen`` / ``requests.get``。动机：

1. **禁代理**：本机环境若走系统代理访问 NCBI 会卡 60s 超时（http=000），
   必须显式 ``ProxyHandler({})`` 直连；这是项目里唯一被验证过能正常工作的写法。
2. **短超时 + 有界重试**：外部服务不可用时应快速失败，把决定权交回业务层降级，
   而不是阻塞问答 / 工具流程、甚至把单进程 uvicorn worker 打挂（历史上 151/151
   连接打满的同类事故）。
3. **审计**：让「是否真的发了请求、耗时多久、成功与否、返回多大」可观测，避免再
   出现「截图证明无网络活动」式的误判。

返回结构（dict）统一，便于调用方做显式校验与降级；本模块**不吞异常**——
失败一定在审计日志里留下记录（``[HTTP-AUDIT]`` 前缀），由调用方决定降级策略。
"""
import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from common_utils import logger

# ===================== 默认策略 =====================
# 短超时：外部服务实测直连 0.8s 内返回，给 10s 余量足够；宁可快速失败再重试，
# 也不要让单请求卡住整条流水线。
DEFAULT_TIMEOUT = 10.0
DEFAULT_ATTEMPTS = 2          # 含首次，最多发 2 次
DEFAULT_BACKOFF = 0.5         # 退避基数（秒），第 i 次重试等待 backoff * 2**i
DEFAULT_USER_AGENT = "ZebrafishGeneQA/1.0"

# ===================== 禁代理 opener（模块加载时构造一次） =====================
# 关键：ProjectHandler({}) 清空系统代理。NCBI / Ensembl / QuickGO 等直连即可，
# 走公司/环境代理反而 60s 超时。
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# ===================== 审计环形缓冲 =====================
_AUDIT: List[Dict[str, Any]] = []
_AUDIT_MAX = 200
_AUDIT_LOCK = threading.Lock()


def _record_audit(rec: Dict[str, Any]) -> None:
    """写入内存环形缓冲并打印审计日志（密钥已在 common_utils 出口打码）。"""
    with _AUDIT_LOCK:
        _AUDIT.append(rec)
        if len(_AUDIT) > _AUDIT_MAX:
            _AUDIT[:] = _AUDIT[-_AUDIT_MAX:]
    logger.info(
        "[HTTP-AUDIT] %s %s%s -> outcome=%s status=%s elapsed_ms=%.0f attempts=%d%s",
        rec.get("method"), rec.get("host"), rec.get("path", "")[:80],
        rec.get("outcome"), rec.get("status"), rec.get("elapsed_ms", 0),
        rec.get("attempts", 0),
        (" err=%s" % rec["error"]) if rec.get("error") else "",
    )


def http_audit_report(n: int = 50) -> List[Dict[str, Any]]:
    """返回最近 n 条外部 HTTP 审计记录（新的在后）。"""
    with _AUDIT_LOCK:
        return list(_AUDIT[-n:])


def last_http_audit() -> Optional[Dict[str, Any]]:
    """返回最近一条审计记录，无则 None。"""
    with _AUDIT_LOCK:
        return _AUDIT[-1] if _AUDIT else None


def _short_hash(data: bytes) -> str:
    if not data:
        return ""
    return hashlib.sha256(data).hexdigest()[:16]


def _build_url(base_url: str, params: Optional[Dict[str, Any]]) -> str:
    if not params:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return base_url + sep + urllib.parse.urlencode(params, doseq=True)


def external_request(
    url: str,
    *,
    method: str = "GET",
    params: Optional[Dict[str, Any]] = None,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF,
    accept_json: bool = False,
    audit_tag: str = "",
) -> Dict[str, Any]:
    """统一的外部 HTTP 请求。

    返回 dict（字段含义见下）。不会抛出未捕获异常：网络/HTTP/解析错误都转为
    ``ok=False`` 的审计记录，由调用方决定降级。

    字段：
        ok          bool        是否成功拿到 2xx 且（如要求）解析出 JSON
        status      int|None    最后一次 HTTP 状态码
        json        Any|None    accept_json=True 且成功时的解析结果
        content     bytes|None   响应体原始字节
        text        str|None    解码后的文本
        elapsed_ms  float       总耗时（含重试）
        attempts    int         实际发出的请求次数
        outcome     str         ok / http_error / timeout / network_error /
                               json_error / invalid_status
        error       str|None    失败时的简短原因
    """
    full_url = _build_url(url, params)
    req_headers = {"User-Agent": DEFAULT_USER_AGENT}
    if headers:
        req_headers.update(headers)
    parsed = urllib.parse.urlparse(full_url)
    host = parsed.netloc or "?"
    path = (parsed.path or "") + (("?" + parsed.params) if parsed.params else "")

    t0 = time.monotonic()
    last_status: Optional[int] = None
    last_err = ""
    sent = 0

    for attempt in range(max(1, attempts)):
        sent = attempt + 1
        try:
            req = urllib.request.Request(full_url, data=data, headers=req_headers)
            req.get_method = lambda: method.upper()  # type: ignore[assignment]
            with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
                last_status = getattr(resp, "status", resp.getcode())
                body = resp.read()
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            if last_status is not None and not (200 <= int(last_status) < 300):
                rec = {
                    "ts": time.time(), "tag": audit_tag, "method": method.upper(),
                    "host": host, "path": path, "status": last_status,
                    "elapsed_ms": elapsed_ms, "attempts": sent,
                    "outcome": "http_error",
                    "error": "HTTP %s" % last_status,
                    "resp_sha": _short_hash(body), "resp_len": len(body),
                }
                _record_audit(rec)
                return {
                    "ok": False, "status": int(last_status), "json": None,
                    "content": body, "text": None, "elapsed_ms": elapsed_ms,
                    "attempts": sent, "outcome": "http_error",
                    "error": "HTTP %s" % last_status,
                }
            # 2xx
            text = body.decode("utf-8", "replace")
            parsed_json: Any = None
            if accept_json:
                try:
                    parsed_json = json.loads(text)
                except Exception as je:  # noqa: BLE001
                    out = {
                        "ok": False, "status": int(last_status), "json": None,
                        "content": body, "text": text, "elapsed_ms": elapsed_ms,
                        "attempts": sent, "outcome": "json_error",
                        "error": "JSON 解析失败: %s" % je,
                    }
                    _record_audit({
                        "ts": time.time(), "tag": audit_tag, "method": method.upper(),
                        "host": host, "path": path, "status": int(last_status),
                        "elapsed_ms": elapsed_ms, "attempts": sent,
                        "outcome": "json_error", "error": out["error"],
                        "resp_sha": _short_hash(body), "resp_len": len(body),
                    })
                    return out
            rec = {
                "ts": time.time(), "tag": audit_tag, "method": method.upper(),
                "host": host, "path": path, "status": int(last_status),
                "elapsed_ms": elapsed_ms, "attempts": sent, "outcome": "ok",
                "error": None, "resp_sha": _short_hash(body), "resp_len": len(body),
            }
            _record_audit(rec)
            return {
                "ok": True, "status": int(last_status), "json": parsed_json,
                "content": body, "text": text, "elapsed_ms": elapsed_ms,
                "attempts": sent, "outcome": "ok", "error": None,
            }
        except urllib.error.HTTPError as e:
            last_status = e.code
            last_err = "HTTP %s" % e.code
            # 只对服务端临时故障重试：5xx / 408 / 425。
            # 旧实现反过来重试 400/404/410/429、却不重试 5xx，既浪费一次
            # 请求又让偶发的 502/503 直接失败。4xx 属请求本身或配额问题，
            # 0.5s/1s 短退避重试无意义，还会放大限流，交给调用方处理。
            retryable = e.code >= 500 or e.code in (408, 425)
            if retryable and attempt < max(1, attempts) - 1:
                time.sleep(backoff * (2 ** attempt))
                continue
            break
        except Exception as e:  # noqa: BLE001
            etype = type(e).__name__
            if "timeout" in etype.lower() or "Timeout" in str(e):
                last_err = "timeout"
                outcome = "timeout"
            else:
                last_err = "%s: %s" % (etype, str(e)[:120])
                outcome = "network_error"
            if attempt < max(1, attempts) - 1:
                time.sleep(backoff * (2 ** attempt))
                continue
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            rec = {
                "ts": time.time(), "tag": audit_tag, "method": method.upper(),
                "host": host, "path": path, "status": last_status,
                "elapsed_ms": elapsed_ms, "attempts": sent, "outcome": outcome,
                "error": last_err, "resp_sha": "", "resp_len": 0,
            }
            _record_audit(rec)
            return {
                "ok": False, "status": last_status, "json": None,
                "content": None, "text": None, "elapsed_ms": elapsed_ms,
                "attempts": sent, "outcome": outcome, "error": last_err,
            }

    # 循环非正常结束（如 4xx 提前 break）
    elapsed_ms = (time.monotonic() - t0) * 1000.0
    rec = {
        "ts": time.time(), "tag": audit_tag, "method": method.upper(),
        "host": host, "path": path, "status": last_status,
        "elapsed_ms": elapsed_ms, "attempts": sent, "outcome": "http_error",
        "error": last_err or "unknown", "resp_sha": "", "resp_len": 0,
    }
    _record_audit(rec)
    return {
        "ok": False, "status": last_status, "json": None, "content": None,
        "text": None, "elapsed_ms": elapsed_ms, "attempts": sent,
        "outcome": "http_error", "error": last_err or "unknown",
    }


def external_get_json(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF,
    audit_tag: str = "",
) -> Optional[Any]:
    """GET 并返回解析后的 JSON；失败返回 None（同时已写入审计）。"""
    r = external_request(
        url, params=params, headers=headers, timeout=timeout,
        attempts=attempts, backoff=backoff, accept_json=True, audit_tag=audit_tag,
    )
    return r["json"] if r["ok"] else None


def external_get_bytes(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff: float = DEFAULT_BACKOFF,
    audit_tag: str = "",
) -> Optional[bytes]:
    """GET 并返回原始字节；失败返回 None（同时已写入审计）。"""
    r = external_request(
        url, params=params, headers=headers, timeout=timeout,
        attempts=attempts, backoff=backoff, accept_json=False, audit_tag=audit_tag,
    )
    return r["content"] if r["ok"] else None


if __name__ == "__main__":
    # 自检：不依赖外网，验证模块可导入、审计记录可生成。
    # 真实联网自检请用：python -m database.http_client 时传入 ONLINE=1
    import os as _os
    if _os.getenv("ONLINE") == "1":
        j = external_get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi",
                              params={"retmode": "json"}, timeout=10, audit_tag="selfcheck")
        print("einfo ok:", bool(j))
    print("audit entries:", len(http_audit_report()))
