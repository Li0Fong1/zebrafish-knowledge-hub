# -*- coding: utf-8 -*-
"""过夜并行：启动监听器（失败自动重试直到全部成功）。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print


def main() -> int:
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if not py.is_file():
        py = Path(sys.executable)
    script = ROOT / "scripts" / "overnight_supervisor.py"
    if os.name == "nt":
        subprocess.Popen(
            ["cmd", "/c", "start", "OvernightSupervisor", "cmd", "/k", str(py), "-X", "utf8", str(script)],
            cwd=str(ROOT),
        )
        safe_print("监听器已在新的 CMD 窗口启动（可见进度）")
        safe_print(f"  日志: {ROOT / 'logs' / 'overnight_supervisor.log'}")
        safe_print(f"  查看: python scripts/tail_log.py supervisor")
        safe_print(f"  状态: python scripts/watch_overnight.py")
        return 0
    return subprocess.call([str(py), "-X", "utf8", str(script)], cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
