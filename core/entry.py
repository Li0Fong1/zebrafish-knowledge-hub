# -*- coding: utf-8 -*-
"""统一入口引导：修复路径、控制台编码、第三方日志。"""
import os
import sys


def bootstrap_app():
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "utf-8")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    os.chdir(root)
    _load_dotenv(os.path.join(root, ".env"))
    from core import path_setup  # noqa: F401
    import bootstrap  # noqa: F401
    bootstrap._fix_windows_console()


def _load_dotenv(path: str):
    """加载项目根目录 .env（不覆盖已存在的环境变量）。"""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)
