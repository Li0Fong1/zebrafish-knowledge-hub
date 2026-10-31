# -*- coding: utf-8 -*-
"""图集流水线共享配置（scripts/ 下各脚本统一入口）。

读取顺序：进程环境变量 → 项目根 `.env` → 内置默认值。
与 `core/config.py` 中同名配置项保持一致（键名均为 `FIG_*`），
因此库内部与独立脚本看到的是同一套设置。

本模块刻意只依赖标准库（不 import core.config），
以免在渲染/校验这类纯离线脚本中引入重量级依赖。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv() -> None:
    """把项目根 .env 注入 os.environ（不覆盖已存在的变量）。"""
    envf = ROOT / ".env"
    if not envf.is_file():
        return
    try:
        text = envf.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


load_dotenv()


def _int(key: str, default: int) -> int:
    try:
        return int(str(os.getenv(key, default)).strip())
    except Exception:
        return default


def _bool(key: str, default: str = "1") -> bool:
    return str(os.getenv(key, default)).strip().lower() not in ("0", "false", "no", "off")


def _path(key: str, default: Path) -> Path:
    raw = (os.getenv(key) or "").strip()
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else (ROOT / raw)


# ── 路径与输出 ───────────────────────────────────────────────────────────
FIG_DIR = _path("FIG_OUTPUT_DIR", ROOT / "Figure")
FIG_DPI = _int("FIG_DPI", 300)

# ── 演示文稿 ─────────────────────────────────────────────────────────────
FIG_DECK_LANG = (os.getenv("FIG_DECK_LANG") or "both").strip().lower()   # en | zh | both
FIG_DECK_EN_NAME = os.getenv("FIG_DECK_EN_NAME", "斑马鱼知识中枢_图集与图注.pptx")
FIG_DECK_ZH_NAME = os.getenv("FIG_DECK_ZH_NAME", "斑马鱼知识中枢_图集与图注_中文版.pptx")
FIG_DECK_POINTS = _int("FIG_DECK_POINTS", 3)                             # 每图页要点条数上限
FIG_EMIT_LEGEND_MD = _bool("FIG_EMIT_LEGEND_MD", "1")                    # 是否输出 Legend.md

# ── 快照新鲜度（天）：超龄则在修复报告中提示刷新 ─────────────────────────
FIG_SNAPSHOT_MAX_AGE_DAYS = _int("FIG_SNAPSHOT_MAX_AGE_DAYS", 14)
FIG_PROBE_MAX_AGE_DAYS = _int("FIG_PROBE_MAX_AGE_DAYS", 30)
FIG_SNAPSHOT_TIMEOUT_SEC = _int("FIG_SNAPSHOT_TIMEOUT_SEC", 240)

# ── 门禁阈值 ─────────────────────────────────────────────────────────────
FIG_VERIFY_MIN_ASSERTIONS = _int("FIG_VERIFY_MIN_ASSERTIONS", 200)
FIG_AUDIT_ALLOW_MISMATCH = _int("FIG_AUDIT_ALLOW_MISMATCH", 0)

# ── 兼容旧脚本的别名 ─────────────────────────────────────────────────────
OUT_DIR = FIG_DIR


def deck_name(lang: str) -> str:
    """按语言返回演示文稿文件名。"""
    return FIG_DECK_ZH_NAME if lang == "zh" else FIG_DECK_EN_NAME


def summary() -> str:
    return (f"FIG_OUTPUT_DIR={FIG_DIR} | DPI={FIG_DPI} | LANG={FIG_DECK_LANG} | "
            f"POINTS={FIG_DECK_POINTS} | LEGEND_MD={int(FIG_EMIT_LEGEND_MD)} | "
            f"min_assertions={FIG_VERIFY_MIN_ASSERTIONS} | allow_mismatch={FIG_AUDIT_ALLOW_MISMATCH}")
