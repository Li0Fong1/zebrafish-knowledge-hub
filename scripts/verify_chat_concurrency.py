# -*- coding: utf-8 -*-
"""在服务器上实测问答并发闸：多车道并行 + 排队 + 完成计数。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts/verify_chat_concurrency.py
    .venv\\Scripts\\python.exe -X utf8 scripts/verify_chat_concurrency.py --lanes 4

原理：从服务器本机并发发 N 条不同 X-Session-Id 的轻量问答
（answer_scope=retrieval_debug，只检索不调大模型），同时轮询 /api/chat/queue，
观察 chat_running 是否达到并发上限、chat_queued 是否出现排队。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = r"""
N=%(n)s
rm -f /tmp/_lane_*.out
i=1
while [ $i -le $N ]; do
  s="LANE$i"
  (
    curl -s -m 150 -X POST http://127.0.0.1:8080/api/chat \
      -H "Content-Type: application/json" -H "X-Session-Id: $s" \
      -d '{"question":"tp53","answer_scope":"retrieval_debug"}' \
      -o /dev/null -w "$s http=%%{http_code} t=%%{time_total}\n" > /tmp/_lane_$s.out 2>&1
  ) &
  i=$((i+1))
done
for k in $(seq 1 40); do
  curl -s -m 3 http://127.0.0.1:8080/api/chat/queue
  echo
  sleep 2
done
wait
echo "--- 各车道结果 ---"
cat /tmp/_lane_*.out 2>/dev/null
rm -f /tmp/_lane_*.out
"""


def env(name: str, default: str = "") -> str:
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            if k.strip() == name:
                return val.strip().strip('"').strip("'")
    return default


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lanes", type=int, default=3, help="并发车道数（默认 3）")
    args = ap.parse_args()

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(env("ZF_HOST"), int(env("ZF_PORT", "22")), env("ZF_USER"), env("ZF_PASS"),
              timeout=60, banner_timeout=90, auth_timeout=120,
              allow_agent=False, look_for_keys=False)
    _, o, e = c.exec_command(SCRIPT % {"n": args.lanes}, timeout=280)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")

    peak_run = peak_queued = 0
    lanes_seen: set[str] = set()
    samples = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        peak_run = max(peak_run, d.get("chat_running", 0))
        peak_queued = max(peak_queued, d.get("chat_queued", 0))
        lanes_seen.update(d.get("chat_queue_lanes") or [])
        samples.append((d.get("chat_running", 0), d.get("chat_queued", 0)))

    print(f"并发上限 chat_max_concurrency = {peak_run} 时的观察")
    print(f"  running 峰值 : {peak_run}")
    print(f"  queued  峰值 : {peak_queued}")
    print(f"  出现过的车道 : {sorted(lanes_seen)}")
    print(f"  (running,queued) 采样前 10: {samples[:10]}")
    tail = [l for l in out.splitlines() if not l.strip().startswith("{")]
    print("--- 服务器输出 ---")
    print("\n".join(t for t in tail if t.strip()))
    if err.strip():
        print("--- stderr ---")
        print(err.strip()[:600])

    ok = peak_run >= 2
    print()
    print("结论：" + ("✅ 多条车道确实并行（running 峰值 ≥2）" if ok
                     else "⚠️ 未观察到并行（running 峰值 <2），请检查 CHAT_MAX_CONCURRENCY"))
    c.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
