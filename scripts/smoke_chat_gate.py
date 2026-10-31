# -*- coding: utf-8 -*-
"""chat_gate 冒烟测试：验证多机并行、同会话串行、排队超时与取消。

跑法：python scripts/smoke_chat_gate.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app  # noqa: E402

bootstrap_app()

from agent.chat_gate import (  # noqa: E402
    ChatGate, ChatTimeoutError, CancelledError, QueueFullError, lane_from_request,
)

FAILS = []


def check(name: str, cond: bool, extra: str = ""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (f" — {extra}" if extra else ""))
    if not cond:
        FAILS.append(name)


def test_parallel_and_serial():
    print("\n[1] 多会话并行 + 同会话串行")
    gate = ChatGate(max_concurrency=2)
    events = []
    lock = __import__("threading").Lock()

    def job(tag, sec=0.6):
        def _f():
            with lock:
                events.append(("start", tag, time.time()))
            time.sleep(sec)
            with lock:
                events.append(("end", tag, time.time()))
            return tag
        return _f

    t0 = time.time()
    # 三个不同 lane → 应并行（并发 2），第三个排队
    a = gate.submit(job("A", 0.8), lane="ip:10.0.0.1", preview="A")
    b = gate.submit(job("B", 0.8), lane="ip:10.0.0.2", preview="B")
    c = gate.submit(job("C", 0.3), lane="ip:10.0.0.3", preview="C")
    st = gate.status()
    check("并发上限=2", st["chat_max_concurrency"] == 2)
    check("C 进入排队", c.state == "queued" and c.position >= 1, f"state={c.state} pos={c.position}")

    ra, rb, rc = a.result(timeout=30), b.result(timeout=30), c.result(timeout=30)
    dur = time.time() - t0
    check("三个任务都完成", (ra, rb, rc) == ("A", "B", "C"))
    # 串行需 0.8+0.8+0.3=1.9s；并发 2 应明显更短
    check("多会话确实并行（总耗时 < 1.6s）", dur < 1.6, f"dur={dur:.2f}s")

    # 同 lane 三个任务：必须严格串行
    seq = []
    gate2 = ChatGate(max_concurrency=4)
    tickets = [
        gate2.submit(job(f"S{i}", 0.25), lane="ip:same", preview=f"S{i}") for i in range(3)
    ]
    for t in tickets:
        t.result(timeout=30)
    for tag, name, _ts in []:
        pass
    # 用重叠检测：同 lane 任务区间不应重叠
    intervals = {}
    ev = []

    def job2(tag):
        def _f():
            ev.append(("s", tag, time.time()))
            time.sleep(0.25)
            ev.append(("e", tag, time.time()))
            return tag
        return _f

    gate3 = ChatGate(max_concurrency=4)
    tk = [gate3.submit(job2(f"T{i}"), lane="ip:same") for i in range(3)]
    for t in tk:
        t.result(timeout=30)
    starts = [ts for k, tag, ts in ev if k == "s"]
    ends = [ts for k, tag, ts in ev if k == "e"]
    overlap = any(starts[i + 1] < ends[i] - 1e-6 for i in range(len(starts) - 1))
    check("同会话串行（无重叠）", not overlap, f"n={len(starts)}")


def test_timeout_and_cancel():
    print("\n[2] 排队超时 / 取消")
    gate = ChatGate(max_concurrency=1)

    def slow():
        time.sleep(2.0)
        return "slow"

    first = gate.submit(slow, lane="ip:1")
    second = gate.submit(lambda: "x", lane="ip:2", timeout=1.0)
    try:
        second.result(timeout=3.0)
        check("排队超时抛 ChatTimeoutError", False)
    except ChatTimeoutError:
        check("排队超时抛 ChatTimeoutError", True)
    first.result(timeout=10)
    check("首个任务不受影响", first.state == "done")

    gate2 = ChatGate(max_concurrency=1)
    blocker = gate2.submit(lambda: time.sleep(1.5), lane="ip:1")
    victim = gate2.submit(lambda: "nope", lane="ip:2")
    time.sleep(0.2)
    check("未开始任务可取消", victim.cancel() is True)
    try:
        victim.result(timeout=2)
        check("取消后 result 抛 CancelledError", False)
    except CancelledError:
        check("取消后 result 抛 CancelledError", True)
    blocker.result(timeout=10)


def test_queue_full():
    print("\n[3] 队列上限")
    gate = ChatGate(max_concurrency=1)
    gate.submit(lambda: time.sleep(3), lane="ip:hold")
    try:
        for i in range(200):
            gate.submit(lambda: None, lane=f"ip:{i}", timeout=60)
        check("超上限抛 QueueFullError", False)
    except QueueFullError:
        check("超上限抛 QueueFullError", True)


def test_lane_key():
    print("\n[4] 车道 key 推导")
    l1 = lane_from_request({"x-access-token": "tok1234567890abcdef"}, "1.1.1.1")
    l2 = lane_from_request({"x-session-id": "sess-abc"}, "2.2.2.2")
    l3 = lane_from_request({}, "3.3.3.3")
    check("令牌优先", l1.startswith("u:"), l1)
    check("会话 ID 次之", l2.startswith("s:"), l2)
    check("无标识退回 IP", l3 == "ip:3.3.3.3", l3)
    check("不同机器不同车道", l3 != lane_from_request({}, "4.4.4.4"))


if __name__ == "__main__":
    test_parallel_and_serial()
    test_timeout_and_cancel()
    test_queue_full()
    test_lane_key()
    print("\n" + ("全部通过" if not FAILS else f"失败项：{FAILS}"))
    sys.exit(1 if FAILS else 0)
