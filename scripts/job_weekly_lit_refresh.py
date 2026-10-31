# -*- coding: utf-8 -*-
"""每周文献刷新（cron 入口）—— 委托给 Hub 的正规采集任务。

历史背景
--------
这个脚本原来是「自己动手」的一条平行管道：单条 EuropePMC 查询 + 主题式 PubMed
采集，把结果直接写成 `rag/knowledge.d/europepmc_weekly_YYYYMMDD.txt`。它能往
知识库塞文本，但**完全不写** MySQL（literature_paper / literature_sync_log），
也不更新 `hub_maintenance` 状态，于是首页的「更新记录」「库内文献 N 篇」永远
停在 8 月，而 hub 自己的周更任务还在因为历史 bug 每次报 new=0。
两条管道各写各的，谁也不知道对方存在。

现在统一走 Hub 的采集任务（`agent/hub_maintenance.run_literature_job`）：
抓取 → knowledge.d → 追加/跳过向量索引 → 写 literature_paper → 写同步日志 →
更新首页状态。本脚本只负责「按点触发 + 不在进程内重复实现」。

调用方式（保持不变，cron 无需改动）：
  python scripts/job_weekly_lit_refresh.py

环境变量：
  ZF_HUB_API          本地 Hub 地址，默认 http://127.0.0.1:8080
  ZF_LIT_MIN_GAP_H    距上次检查不足这么多小时就跳过，默认 24
  ZF_LIT_FORCE=1      忽略上面的间隔检查
  ZF_LIT_DRY_RUN=1    只打印将要做什么
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "rag", ROOT / "api", ROOT / "agent", ROOT / "database"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

API = (os.getenv("ZF_HUB_API") or "http://127.0.0.1:8080").rstrip("/")
MIN_GAP_H = float(os.getenv("ZF_LIT_MIN_GAP_H", "24"))


def _log(msg: str) -> None:
    print(f"[weekly-lit {time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _get(path: str, timeout: float = 20) -> dict:
    with urllib.request.urlopen(f"{API}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(path: str, timeout: float = 30) -> dict:
    req = urllib.request.Request(f"{API}{path}", method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _last_check_ts() -> float:
    try:
        st = _get("/api/hub/maintenance/status")
        return float(((st.get("state") or {}).get("literature_last_ts")) or 0)
    except Exception as e:  # noqa: BLE001
        _log(f"读取 Hub 状态失败：{type(e).__name__}: {e}")
        return 0.0


def _run_direct() -> dict:
    """Hub 服务不可用时的兜底：在独立进程里跑同一条采集任务。"""
    _log("Hub API 不可达，改为进程内直接执行（等价逻辑）")
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    import bootstrap  # noqa: F401

    from hub_maintenance import run_literature_job

    return run_literature_job(trigger="cli")


def main() -> int:
    t0 = time.time()
    dry = os.getenv("ZF_LIT_DRY_RUN", "0").strip().lower() in ("1", "true", "yes")
    force = os.getenv("ZF_LIT_FORCE", "0").strip().lower() in ("1", "true", "yes")

    if dry:
        _log(f"dry-run：将 POST {API}/api/hub/maintenance/run?job=literature")
        return 0

    last = _last_check_ts()
    if last and not force:
        hours = (time.time() - last) / 3600.0
        if hours < MIN_GAP_H:
            _log(f"上次检查 {hours:.1f} 小时前（阈值 {MIN_GAP_H}h），本次跳过")
            return 0
        _log(f"上次检查 {hours:.1f} 小时前，开始更新")

    try:
        res = _post("/api/hub/maintenance/run?job=literature&trigger=auto&background=1", timeout=40)
    except Exception as e:  # noqa: BLE001
        _log(f"触发 Hub 任务失败：{type(e).__name__}: {e}")
        res = _run_direct()
        ok = bool(res.get("ok"))
        _log(f"直接执行完成 ok={ok} new={(res.get('stats') or {}).get('new')} "
             f"papers={(res.get('persist') or {}).get('papers')} elapsed={time.time() - t0:.0f}s")
        return 0 if ok else 1

    if res.get("skipped"):
        _log(f"已有任务在跑（{res.get('reason')}），本次不重复触发")
        return 0
    if not res.get("ok", True):
        _log(f"Hub 拒绝任务：{res.get('error') or res}")
        return 1

    _log(f"已交给 Hub 后台执行，任务进度见 /api/hub/maintenance/status（本次耗时 {time.time() - t0:.1f}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
