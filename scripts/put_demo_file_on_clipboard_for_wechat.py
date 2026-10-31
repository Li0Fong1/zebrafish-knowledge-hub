# -*- coding: utf-8 -*-
"""把测试结果文件放进剪贴板（可在微信里 Ctrl+V 发文件），并打开微信+文件夹。"""
from __future__ import annotations

import ctypes
import os
import struct
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.send_five_demo_to_wechat import TXT_PATH, build_text

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

CF_HDROP = 15
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040


def _copy_files_to_clipboard(paths: list[Path]) -> None:
    """CF_HDROP：微信/资源管理器均可 Ctrl+V 粘贴为文件。"""
    abs_paths = [str(p.resolve()) for p in paths]
    # DROPFILES + 双 null 结尾的多字符串（Unicode）
    files_blob = ("\0".join(abs_paths) + "\0\0").encode("utf-16-le")
    # DROPFILES 结构：pFiles, pt, fNC, fWide
    header = struct.pack("Iiiii", 20, 0, 0, 0, 1)  # pFiles=20, fWide=1
    data = header + files_blob

    if not user32.OpenClipboard(None):
        raise OSError("OpenClipboard failed")
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(data))
        if not h:
            raise OSError("GlobalAlloc failed")
        p = kernel32.GlobalLock(h)
        if not p:
            kernel32.GlobalFree(h)
            raise OSError("GlobalLock failed")
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_HDROP, h):
            kernel32.GlobalFree(h)
            raise OSError("SetClipboardData CF_HDROP failed")
    finally:
        user32.CloseClipboard()


def _set_text_clipboard(text: str) -> None:
    import tempfile

    with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", suffix=".txt", delete=False) as f:
        f.write(text)
        tmp = f.name
    try:
        cmd = (
            f"$t = Get-Content -LiteralPath '{tmp}' -Raw -Encoding UTF8; "
            "Set-Clipboard -Value $t"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", cmd],
            check=False, capture_output=True, text=True,
        creationflags=0x08000000 if __import__('sys').platform == 'win32' else 0)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _find_wechat_hwnd() -> int | None:
    out: list[tuple[int, str]] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        t = (buf.value or "").strip()
        if t in ("微信", "Weixin") or t.startswith("微信"):
            out.append((int(hwnd), t))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    if not out:
        return None
    out.sort(key=lambda x: 0 if x[1] == "微信" else 1)
    return out[0][0]


def main() -> int:
    text = build_text()
    if not TXT_PATH.is_file():
        raise SystemExit(f"missing {TXT_PATH}")

    # 1) 资源管理器选中文件
    subprocess.Popen(["explorer", "/select,", str(TXT_PATH.resolve())])
    time.sleep(0.8)

    # 2) 文件进剪贴板（微信里 Ctrl+V 会发文件）
    _copy_files_to_clipboard([TXT_PATH])
    print("CLIPBOARD_FILE", TXT_PATH)

    # 3) 激活微信
    hwnd = _find_wechat_hwnd()
    if hwnd:
        user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        print("WECHAT_ACTIVATED", hwnd)
    else:
        print("WECHAT_NOT_FOUND")

    print()
    print("=" * 50)
    print("请在 3 秒内完成：")
    print("1) 点开「文件传输助手」")
    print("2) 用鼠标点一下下方输入框")
    print("3) 按 Ctrl+V ，再按回车（会发出 txt 文件）")
    print("=" * 50)
    print(f"文件路径：{TXT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
