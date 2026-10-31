# -*- coding: utf-8 -*-
"""发到当前已打开的微信「文件传输助手」：激活窗口 → 粘贴 → 回车。"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.send_five_demo_to_wechat import TXT_PATH, build_text

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_V = 0x56
VK_F = 0x46
VK_A = 0x41
KEYEVENTF_KEYUP = 0x0002


def _key_down(vk: int) -> None:
    user32.keybd_event(vk, 0, 0, 0)


def _key_up(vk: int) -> None:
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _tap(vk: int) -> None:
    _key_down(vk)
    time.sleep(0.04)
    _key_up(vk)
    time.sleep(0.04)


def _ctrl(vk: int) -> None:
    _key_down(VK_CONTROL)
    time.sleep(0.05)
    _tap(vk)
    _key_up(VK_CONTROL)
    time.sleep(0.08)


def _set_clipboard(text: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", suffix=".txt", delete=False) as f:
        f.write(text)
        tmp = f.name
    try:
        cmd = (
            f"$t = Get-Content -LiteralPath '{tmp}' -Raw -Encoding UTF8; "
            "Set-Clipboard -Value $t"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=0x08000000 if __import__('sys').platform == 'win32' else 0)
        if r.returncode != 0:
            raise RuntimeError(r.stderr or "Set-Clipboard failed")
    finally:
        Path(tmp).unlink(missing_ok=True)


def _enum_windows() -> list[tuple[int, str]]:
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
        if buf.value:
            out.append((int(hwnd), buf.value))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return out


def _find_wechat() -> tuple[int, str]:
    # 优先中文标题「微信」
    ranked: list[tuple[int, int, str]] = []
    for hwnd, title in _enum_windows():
        t = title.strip()
        score = -1
        if t == "微信":
            score = 100
        elif t == "Weixin":
            score = 90
        elif t.startswith("微信"):
            score = 80
        elif "Weixin" in t or "WeChat" in t:
            score = 50
        if score > 0:
            ranked.append((score, hwnd, t))
    if not ranked:
        raise RuntimeError("未找到微信窗口，请先打开 PC 微信并点开「文件传输助手」")
    ranked.sort(reverse=True)
    return ranked[0][1], ranked[0][2]


def _activate(hwnd: int) -> None:
    # 允许跨进程抢前台
    try:
        user32.AllowSetForegroundWindow(-1)
    except Exception:
        pass
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None)
    tid_tg = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(tid_fg, tid_tg, True)
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(tid_fg, tid_tg, False)
    time.sleep(0.6)


def _split(text: str, limit: int = 1200) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    n = 0
    for line in text.splitlines():
        if n + len(line) + 1 > limit and buf:
            parts.append("\n".join(buf))
            buf = [line]
            n = len(line)
        else:
            buf.append(line)
            n += len(line) + 1
    if buf:
        parts.append("\n".join(buf))
    return parts


def send_now(text: str, *, open_helper: bool = True) -> None:
    hwnd, title = _find_wechat()
    print(f"activate: {title!r} hwnd={hwnd}")
    _activate(hwnd)
    time.sleep(0.4)

    if open_helper:
        # 新版微信常用 Ctrl+F 搜索；若已在会话里也可再搜一次定位
        _ctrl(VK_F)
        time.sleep(0.5)
        _set_clipboard("文件传输助手")
        time.sleep(0.15)
        _ctrl(VK_A)
        time.sleep(0.1)
        _ctrl(VK_V)
        time.sleep(0.8)
        _tap(VK_RETURN)
        time.sleep(1.2)

    # 点一下输入框区域：End 键常能落到输入框
    _tap(0x23)  # VK_END
    time.sleep(0.2)

    parts = _split(text)
    for i, part in enumerate(parts, 1):
        _set_clipboard(part)
        time.sleep(0.2)
        _ctrl(VK_V)
        time.sleep(0.35)
        _tap(VK_RETURN)
        time.sleep(0.8)
        print(f"sent part {i}/{len(parts)}")

    # 再发文件路径，方便手机端打开
    _set_clipboard(f"本地全文文件：\n{TXT_PATH}")
    time.sleep(0.2)
    _ctrl(VK_V)
    time.sleep(0.3)
    _tap(VK_RETURN)
    print("sent path tip")


def main() -> int:
    print("请保持 PC 微信已登录；3 秒后发送到「文件传输助手」…")
    time.sleep(3)
    text = build_text()
    print("TEXT", TXT_PATH, "chars", len(text))
    send_now(text, open_helper=True)
    print("SEND_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
