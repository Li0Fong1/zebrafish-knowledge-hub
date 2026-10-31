# -*- coding: utf-8 -*-
"""问答并发闸（Chat Gate）：多机 / 多用户并行提问，同一会话仍串行。

背景
----
原实现用「一把全局 threading.Lock」串行整站对话（`agent_scheduler._chat_run_lock`），
结果是：任何时刻只允许一问在跑，第二台机器提问直接 503，体验远差于 DeepSeek / 豆包 /
Gemini 这类产品。

本模块的目标是把「全站一条车道」换成「多车道 + 可观测排队」：

  1. **全局并发上限 N**：同一时刻最多 N 个问答在执行（默认 3，按「cgroup 余量 ÷ 单请求增量」调）；
  2. **同一 lane 严格串行**：lane 取 用户令牌 / Hub 会话 / 浏览器会话 / IP，
     保证同一个人的多问仍按顺序执行，不会自我并发导致上下文串扰；
  3. **全局 FIFO 公平排队**：先到先服务，排队期间可查询「前面还有几位」；
  4. **排队超时自动失败**：避免请求永久悬挂（默认 900s）；
  5. **可取消**：还没开始的排队任务（例如用户刷新页面、SSE 断开）可以直接撤掉；
  6. **预计等待时间**：按最近完成任务的耗时做 EMA 估算，前端可显示「约还需 Xs」。

配置项（环境变量，均可在 .env / 启动脚本里覆盖）
------------------------------------------------
  CHAT_MAX_CONCURRENCY   全局并发上限，默认 3
  CHAT_QUEUE_WAIT_SEC    单个任务最长排队等待，默认 900（超时返回 503 提示）
  CHAT_QUEUE_MAX         队列最大长度，默认 64（超出返回 429）
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future
from concurrent.futures import TimeoutError as _FutureTimeout
from typing import Callable, Optional

# ===================== 配置 =====================
def _env_int(name: str, default: int, lo: int = 1, hi: int = 128) -> int:
    try:
        v = int(os.getenv(name, str(default)))
    except Exception:
        v = default
    return max(lo, min(hi, v))


def _env_float(name: str, default: float, lo: float = 1.0) -> float:
    try:
        v = float(os.getenv(name, str(default)))
    except Exception:
        v = default
    return max(lo, v)


# 默认并发 3（2026-10-30 实测后确定）。
#
# 历史包袱：2026-10-28 曾把默认压到 1，理由是"单次问答峰值 RSS ≈ 27GB，cgroup
# 上限 36GB，并发 2 必然 OOM（code=137）"。那是**向量分片懒加载**的一次性成本，
# 不是每个请求的边际成本——当时把两件事混为一谈了。
#
# 2026-10-30 实测拆开看（32 核 Xeon Gold 6144 / cgroup 上限 36.8GB）：
#   · 冷进程 RSS 2.7GB；跑满不同主题的问答后，35 个 FAISS 分片逐步载入，
#     RSS 阶梯上涨并稳定在 ≈31.7GB —— 这是**常驻工作集**，gc + malloc_trim
#     收不回来，也不是泄漏（曲线在 31.66GB 处走平）；
#   · 索引就位后，单个请求的边际增量仅 ≈60MB（实测 2.67GB → 2.73GB 峰值）；
#   · 一次 183s 的问答里约 160s 在等远程 DeepSeek API，CPU 累计仅约 20s。
# 也就是说真正的余量是 36.8 − 31.7 ≈ 5.1GB，瓶颈是「余量 ÷ 边际增量」，
# 而不是"每个请求要 27GB"。按每请求留 ~1.5GB 安全垫算，3 车道（≈4.5GB）
# 是这台机器的稳妥上限；机器扩容或换 64GB 上限后可 CHAT_MAX_CONCURRENCY=6。
MAX_CONCURRENCY = _env_int("CHAT_MAX_CONCURRENCY", 3, lo=1, hi=32)
QUEUE_WAIT_SEC = _env_float("CHAT_QUEUE_WAIT_SEC", 900.0, lo=5.0)
QUEUE_MAX = _env_int("CHAT_QUEUE_MAX", 64, lo=1, hi=512)


class QueueFullError(RuntimeError):
    """队列已满（并发 + 排队都饱和）。"""


class ChatTimeoutError(RuntimeError):
    """排队等待超时。"""


class CancelledError(RuntimeError):
    """任务在开始执行前被取消。"""


# ===================== 票据 =====================
class Ticket:
    """一次问答任务的排队凭证。"""

    __slots__ = (
        "id", "lane", "preview", "created_at", "started_at", "finished_at",
        "state", "future", "fn", "position", "_lock", "_timeout",
    )

    def __init__(self, fn: Callable[[], object], lane: str, preview: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.lane = lane or "anon"
        self.preview = (preview or "")[:80]
        self.fn = fn
        self.created_at = time.time()
        self.started_at = 0.0
        self.finished_at = 0.0
        self.state = "queued"  # queued|running|done|error|timeout|cancelled
        self.position = 0
        self.future: Future = Future()
        self._lock = threading.Lock()
        self._timeout = QUEUE_WAIT_SEC

    # ---- 供外部查询 ----
    @property
    def waited(self) -> float:
        base = self.started_at or time.time()
        return max(0.0, base - self.created_at)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "lane": self.lane,
            "state": self.state,
            "position": self.position,
            "waited_sec": int(self.waited),
            "preview": self.preview,
        }

    def cancel(self) -> bool:
        """仅能取消「尚未开始」的任务；已在跑的必须跑完（中途杀进程会留脏状态）。"""
        with self._lock:
            if self.state != "queued":
                return False
            self.state = "cancelled"
        if not self.future.done():
            self.future.set_exception(CancelledError("任务已取消"))
        return True

    def result(self, timeout: Optional[float] = None):
        """阻塞等待结果；超时抛 ChatTimeoutError（任务仍在队列里，不会被丢弃）。"""
        try:
            return self.future.result(timeout=timeout)
        except _FutureTimeout:
            raise ChatTimeoutError(f"排队等待超过 {timeout}s")


# ===================== 并发闸 =====================
class ChatGate:
    """FIFO 排队 + 固定并发 + 同 lane 串行。"""

    def __init__(self, max_concurrency: int = MAX_CONCURRENCY):
        self.max_concurrency = max(1, int(max_concurrency))
        self._cond = threading.Condition()
        self._pending: "deque[Ticket]" = deque()
        self._running: dict[str, Ticket] = {}
        self._lane_inflight: dict[str, int] = {}
        self._workers: list[threading.Thread] = []
        self._workers_lock = threading.Lock()
        self._ema_duration = 60.0  # 近期任务耗时 EMA，用于估算等待
        self._completed = 0

    # ---------- 提交 ----------
    def submit(
        self,
        fn: Callable[[], object],
        *,
        lane: str = "anon",
        preview: str = "",
        timeout: Optional[float] = None,
    ) -> Ticket:
        t = Ticket(fn, lane=lane, preview=preview)
        t._timeout = timeout or QUEUE_WAIT_SEC  # type: ignore[attr-defined]
        with self._cond:
            if len(self._pending) >= QUEUE_MAX:
                raise QueueFullError(f"排队已满（上限 {QUEUE_MAX}），请稍后再试")
            self._pending.append(t)
            self._renumber()
            self._cond.notify_all()
        self._ensure_workers()
        return t

    def _ensure_workers(self):
        with self._workers_lock:
            need = self.max_concurrency - len([w for w in self._workers if w.is_alive()])
            for i in range(max(0, need)):
                w = threading.Thread(
                    target=self._worker_loop,
                    name=f"chat-gate-{len(self._workers) + 1}",
                    daemon=True,
                )
                w.start()
                self._workers.append(w)

    # ---------- 调度 ----------
    def _renumber(self):
        """重算排队位次（从 1 开始）。"""
        for i, t in enumerate(self._pending, start=1):
            t.position = i

    def _expire_locked(self):
        """剔除已超时 / 已取消的排队任务。"""
        if not self._pending:
            return
        now = time.time()
        survivors: "deque[Ticket]" = deque()
        for t in self._pending:
            if t.state == "cancelled":
                continue
            if now - t.created_at > getattr(t, "_timeout", QUEUE_WAIT_SEC):
                t.state = "timeout"
                if not t.future.done():
                    t.future.set_exception(
                        ChatTimeoutError(f"排队等待超过 {getattr(t, '_timeout', QUEUE_WAIT_SEC)}s")
                    )
                continue
            survivors.append(t)
        if len(survivors) != len(self._pending):
            self._pending = survivors
            self._renumber()

    def _pop_runnable_locked(self) -> Optional[Ticket]:
        """取一个「所属 lane 当前空闲」的队首任务；同 lane 串行靠这里保证。"""
        if not self._pending:
            return None
        for t in self._pending:
            if self._lane_inflight.get(t.lane, 0) == 0:
                self._pending.remove(t)
                self._renumber()
                return t
        return None

    def _worker_loop(self):
        while True:
            ticket: Optional[Ticket] = None
            with self._cond:
                while True:
                    self._expire_locked()
                    ticket = self._pop_runnable_locked()
                    if ticket is not None:
                        break
                    self._cond.wait(0.3)

            if ticket.state == "cancelled":
                continue
            ticket.state = "running"
            ticket.started_at = time.time()
            self._lane_inflight[ticket.lane] = self._lane_inflight.get(ticket.lane, 0) + 1
            self._running[ticket.id] = ticket
            t0 = time.time()
            try:
                res = ticket.fn()
            except BaseException as e:  # noqa: BLE001 - 必须兜住，否则 worker 线程死掉
                ticket.state = "error"
                if not ticket.future.done():
                    ticket.future.set_exception(e)
            else:
                ticket.state = "done"
                if not ticket.future.done():
                    ticket.future.set_result(res)
            finally:
                ticket.finished_at = time.time()
                dur = max(1.0, ticket.finished_at - t0)
                # EMA：新样本权重 0.3，避免单次超长任务把预估带飞
                self._ema_duration = self._ema_duration * 0.7 + dur * 0.3
                self._completed += 1
                with self._cond:
                    n = self._lane_inflight.get(ticket.lane, 0) - 1
                    if n <= 0:
                        self._lane_inflight.pop(ticket.lane, None)
                    else:
                        self._lane_inflight[ticket.lane] = n
                    self._running.pop(ticket.id, None)
                    self._cond.notify_all()

    # ---------- 查询 ----------
    def status(self) -> dict:
        with self._cond:
            running = len(self._running)
            queued = len(self._pending)
            lanes = sorted(self._lane_inflight.keys())
            oldest = int(self._pending[0].waited) if self._pending else 0
            preview = ""
            if self._running:
                # 取最早开始的那一个做展示（信息量最大）
                first = min(self._running.values(), key=lambda t: t.started_at or 0)
                preview = first.preview
        return {
            "chat_queue_enabled": True,
            "chat_max_concurrency": self.max_concurrency,
            "chat_running": running,
            "chat_queued": queued,
            "chat_queue_lanes": lanes[:10],
            "chat_queue_oldest_wait_sec": oldest,
            "chat_busy": running >= self.max_concurrency,
            "chat_busy_preview": preview,
            "chat_eta_sec": self.eta_for_position(queued),
            "chat_completed": self._completed,
        }

    def eta_for_position(self, position: int) -> int:
        """给定排队位次，估算还需等待秒数（粗略，用于前端提示）。"""
        if position <= 0:
            return 0
        slots = max(1, self.max_concurrency)
        return int(round(self._ema_duration * (position / slots)))

    def lane_waiting(self, lane: str) -> int:
        with self._cond:
            return sum(1 for t in self._pending if t.lane == lane)


# ===================== 单例 =====================
_gate: Optional[ChatGate] = None
_gate_lock = threading.Lock()


def get_chat_gate() -> ChatGate:
    global _gate
    if _gate is None:
        with _gate_lock:
            if _gate is None:
                _gate = ChatGate()
    return _gate


def submit(fn: Callable[[], object], *, lane: str = "anon", preview: str = "",
           timeout: Optional[float] = None) -> Ticket:
    return get_chat_gate().submit(fn, lane=lane, preview=preview, timeout=timeout)


def status() -> dict:
    try:
        return get_chat_gate().status()
    except Exception:
        return {"chat_queue_enabled": False, "chat_running": 0, "chat_queued": 0,
                "chat_busy": False, "chat_max_concurrency": MAX_CONCURRENCY}


def lane_from_request(headers: dict, client_host: str = "") -> str:
    """从请求里推导「车道 key」：同一 key 串行，不同 key 可并行。

    优先级（2026-10-30 修正）：Hub 会话 > 浏览器会话 ID > 访问令牌 > 客户端 IP。

    ⚠️ 历史 bug：早期把 `x-access-token` 排在第一，但那是**全站共用的访问口令**
    （.env 里的 WEB_ACCESS_TOKEN，所有访客同一个值），不是用户身份。结果所有机器
    都被归到 "u:<同一个token>" 这一条车道上，并发闸形同虚设——开了 4 车道实际还是
    单车道。实测证据：4 个不同会话并发提交，health 里 chat_queue_lanes 只有 1 条。
    因此现在把「每标签页唯一」的 x-session-id 提到 token 之前；token 只在完全没有
    浏览器会话（纯 API 调用）时兜底。

    多台不同机器 = 不同 session-id → 各走各的车道，互不排队。
    """
    hub = (headers.get("x-hub-session") or "").strip()
    if hub:
        return "h:" + hub[:24]
    sid = (headers.get("x-session-id") or "").strip()
    if sid:
        return "s:" + sid[:32]
    token = (headers.get("x-access-token") or "").strip()
    if token:
        return "u:" + token[:16]
    return "ip:" + (client_host or "unknown")
