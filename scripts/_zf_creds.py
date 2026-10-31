# -*- coding: utf-8 -*-
"""服务器凭据读取（发布安全：源码内不得硬编码主机名/密码）。

优先级：进程环境变量 > 仓库根 `.env`；两处都没有时**明确报错**，不提供兜底默认值。
这与 docs/paper/RELEASE_SECURITY_AUDIT.md 的处置口径一致。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """把仓库根 .env 读进 os.environ（已存在的变量不覆盖）。"""
    p = ROOT / ".env"
    if not p.is_file():
        return
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()


def _get(*names: str, default: str = "") -> str:
    for n in names:
        v = (os.environ.get(n) or "").strip()
        if v:
            return v
    return default


HOST = _get("ZF_HOST", "REMOTE_HOST")
PORT = int(_get("ZF_PORT", "REMOTE_PORT", default="22") or 22)
USER = _get("ZF_USER", "REMOTE_USER")
PASS = _get("ZF_PASS", "REMOTE_PASS")


def require_password() -> str:
    """取服务器密码；缺失即退出，绝不回退到源码里的默认值。"""
    if not PASS:
        raise SystemExit(
            "缺少服务器密码：请在 .env 或环境变量中设置 ZF_PASS（源码内不得硬编码凭据）"
        )
    return PASS
