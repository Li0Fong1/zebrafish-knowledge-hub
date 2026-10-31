# -*- coding: utf-8 -*-
"""
产品化全自动启动：租户/工作流初始化 → Web → 过夜监听器。

适合长时间挂机：python scripts/run_product_autostart.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from core.log_io import open_log_append
from common_utils import safe_print
from config import WEB_HOST, WEB_PORT

LOG_DIR = ROOT / "logs"
MAIN_LOG = LOG_DIR / "product_autostart.log"


def _python() -> Path:
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    return venv if venv.is_file() else Path(sys.executable)


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    safe_print(line)
    LOG_DIR.mkdir(exist_ok=True)
    with open_log_append(MAIN_LOG) as f:
        f.write(line + "\n")


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _find_pids(keyword: str) -> list[int]:
    """Windows: 通过 wmic 查找含 keyword 的 python 进程。"""
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", f"name='python.exe'", "get", "ProcessId,CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        if keyword.lower() in line.lower():
            parts = line.strip().split()
            if parts and parts[-1].isdigit():
                pids.append(int(parts[-1]))
    return pids


def _start_detached(args: list[str], log_name: str) -> int:
    LOG_DIR.mkdir(exist_ok=True)
    out_path = LOG_DIR / f"{log_name}.log"
    err_path = LOG_DIR / f"{log_name}.err.log"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONLEGACYWINDOWSSTDIO": "utf-8",
        }
    )
    with open(out_path, "a", encoding="utf-8") as out, open(err_path, "a", encoding="utf-8") as err:
        proc = subprocess.Popen(
            args,
            cwd=str(ROOT),
            stdout=out,
            stderr=err,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )
    return proc.pid


def main() -> int:
    py = _python()
    _log("=== 产品化自动启动 ===")

    _log("步骤 1/3：初始化租户与工作流预置…")
    r = subprocess.run([str(py), "scripts/init_tenant_workflow.py"], cwd=str(ROOT))
    if r.returncode != 0:
        _log(f"[警告] 初始化返回 {r.returncode}，继续启动服务")

    host = WEB_HOST if WEB_HOST not in ("0.0.0.0", "") else "127.0.0.1"
    if _port_open(host, int(WEB_PORT)):
        _log(f"步骤 2/3：Web 已在 {host}:{WEB_PORT} 运行，跳过")
    else:
        _log(f"步骤 2/3：启动 Web http://{WEB_HOST}:{WEB_PORT}")
        pid = _start_detached([str(py), "-m", "api_server"], "product_web")
        _log(f"Web 已后台启动 pid={pid}")

    sup_key = "overnight_supervisor"
    existing = _find_pids(sup_key)
    if existing:
        _log(f"步骤 3/3：过夜监听器已在运行 pid={existing}")
    else:
        _log("步骤 3/3：启动过夜监听器 (18+15+19+08+21+22+23)")
        pid = _start_detached([str(py), "scripts/overnight_supervisor.py"], "product_supervisor")
        _log(f"过夜监听器已后台启动 pid={pid}")

    _log("=== 全部就绪。查看进度: python scripts/tail_log.py supervisor -f ===")
    _log(f"Web 入口: http://127.0.0.1:{WEB_PORT}  工作流 API: /api/workflows/presets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
