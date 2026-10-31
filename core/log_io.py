# -*- coding: utf-8 -*-
"""UTF-8 日志读写与终端输出（避免 Windows PowerShell / CMD 乱码）。"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import IO, List, Union

PathLike = Union[str, Path]

LOG_PRESETS = {
    "08": "logs/overnight_08_aliases.log",
    "15": "logs/overnight_15_link.log",
    "18": "logs/overnight_18_wos.log",
    "19": "logs/overnight_19_go.log",
    "21": "logs/overnight_21_grcz12.log",
    "22": "logs/overnight_22_idtest.log",
    "23": "logs/overnight_23_rag.log",
    "supervisor": "logs/overnight_supervisor.log",
    "pipeline": "logs/full_pipeline.log",
}


def resolve_log_path(name_or_path: str, root: Path | None = None) -> Path:
    root = root or Path.cwd()
    key = name_or_path.strip().lower()
    if key in LOG_PRESETS:
        return root / LOG_PRESETS[key]
    p = Path(name_or_path)
    return p if p.is_absolute() else root / p


def open_log_append(path: PathLike) -> IO[str]:
    """追加写 UTF-8 日志；新文件写入 BOM，便于 Windows 工具识别。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    is_new = not p.exists() or p.stat().st_size == 0
    f = open(p, "a", encoding="utf-8", newline="\n")
    if is_new:
        f.write("\ufeff")
    return f


def read_lines_utf8(path: PathLike, *, tail: int = 0) -> List[str]:
    p = Path(path)
    if not p.is_file():
        return []
    text = p.read_text(encoding="utf-8-sig", errors="strict")
    lines = text.splitlines()
    if tail > 0:
        return lines[-tail:]
    return lines


def verify_utf8_file(path: PathLike) -> tuple[bool, str]:
    """检查文件是否为合法 UTF-8（不修改文件）。"""
    p = Path(path)
    if not p.is_file():
        return False, "文件不存在"
    try:
        p.read_text(encoding="utf-8-sig", errors="strict")
        return True, "UTF-8 正常"
    except UnicodeDecodeError as e:
        return False, f"非 UTF-8 或损坏: {e}"


def print_log(path: PathLike, *, lines: int = 20, follow: bool = False) -> None:
    """用 WriteConsoleW 输出日志，终端中文不乱码。"""
    import bootstrap

    bootstrap._fix_windows_console()
    p = Path(path)
    if not p.is_file():
        bootstrap.write_console(f"[提示] 日志不存在: {p}\n")
        return

    def _emit(text: str) -> None:
        if not text:
            return
        if not text.endswith("\n"):
            text += "\n"
        bootstrap.write_console(text)

    if not follow:
        for line in read_lines_utf8(p, tail=lines):
            _emit(line)
        return

    _emit(f"--- 跟踪 {p}（Ctrl+C 退出）---")
    with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
        all_lines = f.readlines()
        for line in all_lines[-lines:]:
            _emit(line.rstrip("\n\r"))
        while True:
            chunk = f.read()
            if chunk:
                _emit(chunk.rstrip("\n\r"))
            time.sleep(1)


def subprocess_log_env() -> dict:
    """子进程环境：强制 Python 标准流 UTF-8。"""
    return {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONLEGACYWINDOWSSTDIO": "utf-8",
    }
