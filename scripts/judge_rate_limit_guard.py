# -*- coding: utf-8 -*-
"""判别任务 429 限流保险：默认高并发，大量 429 自动降档，稳定后恢复。"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional

from common_utils import safe_print

WORKERS_HIGH = int(os.getenv("JUDGE_INVENTORY_WORKERS_HIGH", "18"))
WORKERS_LOW = int(os.getenv("JUDGE_INVENTORY_WORKERS_LOW", "12"))
WINDOW_SEC = float(os.getenv("JUDGE_429_WINDOW_SEC", "60"))
THRESHOLD = int(os.getenv("JUDGE_429_THRESHOLD", "5"))
RECOVER_SEC = float(os.getenv("JUDGE_429_RECOVER_SEC", "180"))


def is_rate_limit_error(exc: BaseException) -> bool:
    s = str(exc).lower()
    return "429" in s or "rate limit" in s or "too many request" in s or "quota" in s


def is_balance_error(exc: BaseException) -> bool:
    """DeepSeek 余额不足 / 402：不应重试，应立即停任务。"""
    s = str(exc).lower()
    return (
        "402" in s
        or "insufficient balance" in s
        or "余额不足" in s
        or "payment required" in s
    )


class InsufficientBalanceError(RuntimeError):
    """API 余额不足，调用方应停止批量任务。"""


class BalanceGuard:
    """余额不足（402）时全局停火，避免 18 并发反复重试空烧。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stopped = False
        self.detail = ""

    def halted(self) -> bool:
        with self._lock:
            return self.stopped

    def trip(self, exc: BaseException) -> bool:
        if not is_balance_error(exc):
            return False
        with self._lock:
            if not self.stopped:
                self.stopped = True
                self.detail = str(exc)[:300]
                safe_print(
                    "  [余额不足] 检测到 API 402/Insufficient Balance → "
                    "立即停止 LLM 调用。请充值后再 python -m scripts.launch_judge_pubmed_inventory_detached --resume"
                )
        return True

    def status(self) -> dict | None:
        with self._lock:
            if not self.stopped:
                return None
            return {"stopped": True, "detail": self.detail}


class RateLimitGuard:
    """
    并发保险：
      - 正常：WORKERS_HIGH（默认 18）
      - 滑动窗口内 429 ≥ THRESHOLD（默认 5 次/60s）：降至 WORKERS_LOW（12）
      - 降档后连续 RECOVER_SEC（默认 180s）无 429：恢复 WORKERS_HIGH
    """

    def __init__(
        self,
        *,
        on_change: Optional[Callable[[int, str], None]] = None,
    ) -> None:
        self.workers_high = WORKERS_HIGH
        self.workers_low = WORKERS_LOW
        self.window_sec = WINDOW_SEC
        self.threshold = THRESHOLD
        self.recover_sec = RECOVER_SEC
        self._lock = threading.Lock()
        self._429_times: list[float] = []
        self._last_429_at: float = 0.0
        self._workers = self.workers_high
        self._throttled = False
        self._on_change = on_change

    @property
    def workers(self) -> int:
        with self._lock:
            return self._workers

    def record_429(self, detail: str = "") -> None:
        now = time.time()
        with self._lock:
            self._429_times.append(now)
            self._last_429_at = now
            cutoff = now - self.window_sec
            self._429_times = [t for t in self._429_times if t >= cutoff]
            n = len(self._429_times)
            if n >= self.threshold and self._workers > self.workers_low:
                self._workers = self.workers_low
                self._throttled = True
                msg = (
                    f"[保险] {n} 次 429/{self.window_sec:.0f}s → "
                    f"并发 {self.workers_high}→{self.workers_low}"
                )
                if detail:
                    msg += f" ({detail[:80]})"
                safe_print(f"  {msg}")
                if self._on_change:
                    self._on_change(self._workers, msg)

    def tick_recover(self) -> None:
        """每批任务开始前调用：无 429 足够久则恢复高并发。"""
        now = time.time()
        with self._lock:
            cutoff = now - self.window_sec
            self._429_times = [t for t in self._429_times if t >= cutoff]
            if not self._throttled or self._workers >= self.workers_high:
                return
            if self._last_429_at <= 0:
                return
            quiet = now - self._last_429_at
            if quiet >= self.recover_sec and not self._429_times:
                self._workers = self.workers_high
                self._throttled = False
                msg = (
                    f"[保险] {quiet:.0f}s 无 429 → "
                    f"并发恢复 {self.workers_low}→{self.workers_high}"
                )
                safe_print(f"  {msg}")
                if self._on_change:
                    self._on_change(self._workers, msg)

    def status(self) -> dict:
        with self._lock:
            return {
                "workers": self._workers,
                "workers_high": self.workers_high,
                "workers_low": self.workers_low,
                "throttled": self._throttled,
                "recent_429": len(self._429_times),
                "last_429_at": self._last_429_at or None,
            }
