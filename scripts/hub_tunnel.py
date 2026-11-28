# -*- coding: utf-8 -*-
"""把服务器上的生产 Hub 映射到本机浏览器（SSH 本地端口转发）。

    本机 127.0.0.1:<LOCAL_PORT>  →  SSH  →  服务器 127.0.0.1:8080（Hub 生产实例）

用途：服务器 Hub 未开公网（仅 43333 可 SSH），因此在本地开一个转发口，用浏览器直连生产
实例，从而获得**完整索引**（32,532 篇 / 7,651,942 块）与 **MySQL 策展库**，
而不是本地那份无 MySQL、旧索引的降级副本。

用法
    python scripts\\hub_tunnel.py                # 默认本机 8080
    python scripts\\hub_tunnel.py --port 18080   # 端口被占用时换一个
按 Ctrl+C 结束（结束即断开转发）。
"""
from __future__ import annotations

import argparse
import os
import select
import socket
import sys
import threading
import time

from pathlib import Path


# ---- 服务器凭据：一律从环境变量或仓库根 .env 读取，源码内不得出现明文 ----
def _zf_env(name: str, default: str = "") -> str:
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        envf = Path(__file__).resolve().parents[1] / ".env"
        for line in envf.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return default


def _zf_required(name: str) -> str:
    val = _zf_env(name)
    if not val:
        raise SystemExit(f"缺少 {name}：请在 .env 或环境变量中设置（源码内不留服务器标识与凭据）")
    return val


def _zf_host() -> str:
    return _zf_required("ZF_HOST")


def _zf_port() -> int:
    return int(_zf_required("ZF_PORT"))


def _zf_user() -> str:
    return _zf_required("ZF_USER")


def _zf_pass() -> str:
    pw = _zf_env("ZF_PASS")
    if not pw:
        raise SystemExit("缺少服务器密码：请在 .env 或环境变量中设置 ZF_PASS（源码内不得硬编码凭据）")
    return pw

HOST, PORT, USER, PWD = _zf_host(), _zf_port(), _zf_user(), _zf_pass()
REMOTE = ("127.0.0.1", 8080)          # 服务器侧 Hub 监听地址


def _pipe(a: socket.socket, b, tag: str) -> None:
    """双向搬运（任一端关闭即收工）。"""
    try:
        while True:
            r, _w, _x = select.select([a, b], [], [], 60)
            if not r:
                continue
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except Exception:
                pass


def _serve(client: socket.socket, transport) -> None:
    try:
        ch = transport.open_channel("direct-tcpip", REMOTE, src_addr=client.getpeername())
    except Exception as e:
        print(f"[tunnel] open_channel 失败: {e!r}", flush=True)
        client.close()
        return
    threading.Thread(target=_pipe, args=(client, ch, "c2s"), daemon=True).start()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080, help="本机监听端口（默认 8080）")
    ap.add_argument("--host", default="127.0.0.1", help="本机监听地址")
    a = ap.parse_args()

    try:
        import paramiko
    except Exception:
        print("缺少 paramiko：请先 .venv\\Scripts\\pip install paramiko")
        return 2

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(HOST, port=PORT, username=USER, password=PWD, timeout=20)
    tr = cli.get_transport()
    tr.set_keepalive(30)                     # 防止长连接被掐断

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind((a.host, a.port))
    except OSError as e:
        print(f"[tunnel] 端口 {a.port} 被占用（{e}）。换一个：--port 18080")
        return 3
    srv.listen(64)
    print(f"[tunnel] 已启动：http://{a.host}:{a.port}  →  {HOST}:{PORT}  →  服务器 {REMOTE[0]}:{REMOTE[1]}",
          flush=True)
    print("[tunnel] 浏览器打开上面的地址即可测试；Ctrl+C 结束。", flush=True)
    try:
        while True:
            try:
                c, _addr = srv.accept()
            except OSError:
                time.sleep(0.2)
                continue
            threading.Thread(target=_serve, args=(c, tr), daemon=True).start()
    except KeyboardInterrupt:
        print("\n[tunnel] 已停止。")
    finally:
        try:
            srv.close()
        except Exception:
            pass
        cli.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
