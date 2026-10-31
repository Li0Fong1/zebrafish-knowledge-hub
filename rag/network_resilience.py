# -*- coding: utf-8 -*-
"""长任务断网检测、等待恢复与 HTTP 重试（NCBI / Europe PMC）。"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

PROBE_URLS = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?db=pubmed&retmode=json",
    "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=zebrafish&pageSize=1&format=json",
)
PROBE_INTERVAL = float(os.getenv("NETWORK_PROBE_INTERVAL", "30"))
PROBE_TIMEOUT = float(os.getenv("NETWORK_PROBE_TIMEOUT", "15"))
WAIT_LOG_INTERVAL = float(os.getenv("NETWORK_WAIT_LOG_INTERVAL", "300"))
HTTP_MAX_RETRIES = int(os.getenv("NETWORK_HTTP_MAX_RETRIES", "6"))
HTTP_BACKOFF = float(os.getenv("NETWORK_HTTP_BACKOFF", "3.0"))

_NETWORK_ERRORS = (
    urllib.error.URLError,
    ConnectionResetError,
    TimeoutError,
    OSError,
    BrokenPipeError,
)


def is_network_error(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (408, 429, 500, 502, 503, 504)
    return isinstance(exc, _NETWORK_ERRORS)


def probe_network(timeout: float = PROBE_TIMEOUT) -> bool:
    """探测 NCBI / Europe PMC 是否可达。"""
    for url in PROBE_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if getattr(resp, "status", 200) == 200:
                    return True
        except Exception:
            continue
    return False


def wait_for_network(
    *,
    log_fn: Optional[Callable[[str], None]] = None,
    reason: str = "",
) -> None:
    """阻塞直到网络恢复；长等待期间周期性打日志。"""
    if probe_network():
        return
    prefix = "[断网等待]"
    if reason:
        prefix = f"{prefix} {reason}"
    t0 = time.time()
    last_log = 0.0
    if log_fn:
        log_fn(f"{prefix} 检测到网络不可用，等待恢复…")
    while True:
        if probe_network():
            if log_fn:
                log_fn(f"{prefix} 网络已恢复（共等待 {int(time.time() - t0)}s）")
            return
        now = time.time()
        if log_fn and now - last_log >= WAIT_LOG_INTERVAL:
            log_fn(f"{prefix} 仍无网络，已等待 {int(now - t0)}s …")
            last_log = now
        time.sleep(PROBE_INTERVAL)


def urlopen_with_retry(
    req: urllib.request.Request,
    *,
    timeout: float = 90,
    max_retries: int = HTTP_MAX_RETRIES,
    backoff: float = HTTP_BACKOFF,
    wait_on_disconnect: bool = True,
    log_fn: Optional[Callable[[str], None]] = None,
    label: str = "HTTP",
):
    """urllib 请求：瞬断重试；持续断网则 wait_for_network 后无限重试。"""
    attempt = 0
    while True:
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (408, 429, 500, 502, 503, 504):
                attempt += 1
                wait = min(backoff * (2 ** min(attempt - 1, 5)), 120.0)
                if log_fn:
                    log_fn(
                        f"  [网络] {label} HTTP {e.code}，{wait:.0f}s 后重试 "
                        f"({attempt}/{max_retries})…"
                    )
                if attempt >= max_retries and wait_on_disconnect:
                    wait_for_network(log_fn=log_fn, reason=label)
                    attempt = 0
                    continue
                if attempt >= max_retries:
                    raise
                time.sleep(wait)
                continue
            raise
        except _NETWORK_ERRORS as e:
            attempt += 1
            wait = min(backoff * (2 ** min(attempt - 1, 5)), 120.0)
            if log_fn:
                log_fn(
                    f"  [网络] {label} 连接失败 ({type(e).__name__})，"
                    f"{wait:.0f}s 后重试 ({attempt}/{max_retries})…"
                )
            if attempt >= max_retries and wait_on_disconnect:
                wait_for_network(log_fn=log_fn, reason=label)
                attempt = 0
                continue
            if attempt >= max_retries:
                raise
            time.sleep(wait)


def call_with_network_recovery(
    fn: Callable[[], T],
    *,
    log_fn: Optional[Callable[[str], None]] = None,
    label: str = "请求",
) -> T:
    """执行 fn；遇网络错误则等待恢复后重试，直至成功。"""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if not is_network_error(e):
                raise
            attempt += 1
            if log_fn:
                log_fn(f"  [网络] {label} 失败 ({type(e).__name__})，第 {attempt} 次")
            wait_for_network(log_fn=log_fn, reason=label)
            time.sleep(min(HTTP_BACKOFF, 10.0))
