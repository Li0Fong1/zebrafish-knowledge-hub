# -*- coding: utf-8 -*-
"""
本机 Web + Cloudflare 临时公网隧道（无需公网 IP / 路由器配置）。

首次运行会自动下载 cloudflared 到 tools/cloudflared.exe。
关闭本窗口即停止对外访问。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(ROOT, "tools")
CLOUDFLARED = os.path.join(TOOLS_DIR, "cloudflared.exe")
CLOUDFLARED_URL = (
    "https://github.com/cloudflare/cloudflared/releases/latest/download/"
    "cloudflared-windows-amd64.exe"
)
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com", re.I)


def _python_exe() -> str:
    venv = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    return venv if os.path.isfile(venv) else sys.executable


def _ensure_cloudflared() -> str:
    os.makedirs(TOOLS_DIR, exist_ok=True)
    if os.path.isfile(CLOUDFLARED) and os.path.getsize(CLOUDFLARED) > 1_000_000:
        return CLOUDFLARED
    print("[下载] cloudflared（内网穿透客户端，约 20MB）…")
    tmp = CLOUDFLARED + ".download"
    urllib.request.urlretrieve(CLOUDFLARED_URL, tmp)
    shutil.move(tmp, CLOUDFLARED)
    print("[完成] cloudflared 已就绪")
    return CLOUDFLARED


def _web_health_ok(port: int) -> bool:
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status == 200
    except OSError:
        return False


def _wait_web(port: int, timeout: int = 180) -> bool:
    for i in range(timeout):
        if _web_health_ok(port):
            return True
        if i and i % 10 == 0:
            print(f"  等待 Web 就绪… ({i}s)")
        time.sleep(1)
    return False


def _relay_output(proc: subprocess.Popen, prefix: str) -> None:
    if proc.stdout is None:
        return
    for line in proc.stdout:
        print(f"{prefix}{line}", end="", flush=True)


def main() -> int:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    os.chdir(ROOT)

    from core.entry import bootstrap_app

    bootstrap_app()

    from common_utils import safe_print
    from config import WEB_PORT

    os.environ["CONSOLE_TITLE"] = "斑马鱼基因问答体 - 公网隧道"
    try:
        import bootstrap
        bootstrap._fix_windows_console()
    except Exception:
        pass

    port = int(WEB_PORT)
    safe_print("=" * 60)
    safe_print("  公网访问（Cloudflare 临时隧道）")
    safe_print(f"  本地 Web：http://127.0.0.1:{port}")
    safe_print("  说明：关闭本窗口即停止对外访问；链接每次启动可能变化")
    safe_print("  上传：公网允许 PDF/TXT/MD 入库（扩展名+魔数+内容过滤，见 file_security）")
    safe_print("  日志：他人访问后，本窗口会显示 [Web] [访问] / [对话] 行")
    safe_print("  DeepSeek 用量：请到 platform.deepseek.com 账单页查看")
    safe_print("=" * 60)

    web_proc: subprocess.Popen | None = None
    we_started_web = False

    if _web_health_ok(port):
        safe_print(f"\n[提示] 端口 {port} 已有 Web 在运行，将直接建立隧道（不再重复启动）")
        safe_print("  若需严格模式+上传过滤，请在 .env 设 WEB_STRICT=1 WEB_ALLOW_UPLOAD=1\n")
    else:
        web_env = os.environ.copy()
        web_env["PUBLIC_TUNNEL"] = "1"
        web_env["WEB_AUTH_REQUIRED"] = "0"
        web_env["WEB_STRICT"] = "0"
        web_env["WEB_ALLOW_UPLOAD"] = "1"
        web_cmd = [_python_exe(), "-X", "utf8", "-m", "api.api_server"]
        web_proc = subprocess.Popen(
            web_cmd,
            cwd=ROOT,
            env=web_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
        )
        we_started_web = True
        threading.Thread(target=_relay_output, args=(web_proc, "[Web] "), daemon=True).start()
    try:
        if not _wait_web(port):
            safe_print(f"[错误] Web 未在 3 分钟内就绪（http://127.0.0.1:{port}）")
            safe_print("  若端口被其它程序占用，请先关闭占用 8080 的窗口，或改 .env 中 WEB_PORT")
            return 1

        cf = _ensure_cloudflared()
        safe_print("\n[隧道] 正在建立公网链接…\n")
        tunnel = subprocess.Popen(
            [cf, "tunnel", "--url", f"http://127.0.0.1:{port}"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert tunnel.stdout is not None
        for line in tunnel.stdout:
            print(line, end="")
            m = URL_RE.search(line)
            if m:
                safe_print("\n" + "=" * 60)
                safe_print(f"  公网地址（发给他人）：{m.group(0)}")
                safe_print(f"  用量统计：{m.group(0)}/api/usage  （或看本窗口 [Web] 日志）")
                safe_print("=" * 60 + "\n")
        tunnel.wait()
        return tunnel.returncode or 0
    except KeyboardInterrupt:
        safe_print("\n[停止] 正在关闭…")
        return 0
    finally:
        if we_started_web and web_proc is not None and web_proc.poll() is None:
            web_proc.terminate()
            try:
                web_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                web_proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
