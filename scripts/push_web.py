# -*- coding: utf-8 -*-
"""把本地 web/ 静态文件推到服务器（带备份 + 校验 + 顺带报告公网地址）。

背景：本仓库不是 git 仓库，服务器 `web/` 是线上权威版本。静态文件（html/css/js）
推送后**不需要重启 Hub**。

用法：
    # 推送默认待办清单（provenance.js 语法修复 + index.html 版本号）
    .venv\\Scripts\\python.exe -X utf8 scripts\\push_web.py

    # 推送任意文件
    .venv\\Scripts\\python.exe -X utf8 scripts\\push_web.py web/js/app.js web/css/simplify.css

    # 只检查差异（不写入）
    .venv\\Scripts\\python.exe -X utf8 scripts\\push_web.py --dry-run

连接信息按下面的默认值走，也可用环境变量覆盖：
    ZF_HOST / ZF_PORT / ZF_USER / ZF_PASS / ZF_BASE / ZF_RUNTIME

注意：SSH 认证阶段 EOF **不一定**是"连接惩罚期"。实测判别法（2026-10-01）：
  · 密码错 → paramiko 报 `Authentication failed.`，约 3 s 返回（PAM 故意延迟）；
  · 密码对但账户/会话层被拒 → 连接在 ~0.3 s 内被掐断，报 `transport shut down or saw EOF`。
已确认的一次原因：`deng` 账号过期（ssh 提示 `Your account has expired`），
需管理员 `sudo chage -E -1 deng`。详见 /memories/repo/server-access.md。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

import paramiko


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

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_FILES = [
    "web/js/provenance.js",   # 2026-09-30 m3c 批次带入的语法错误（缺逗号）修复
    "web/index.html",         # 资源版本号升级，穿透浏览器与边缘缓存
]

HOST = _zf_host()
PORT = _zf_port()
USER = _zf_user()
PASS = _zf_pass()
BASE = os.environ.get("ZF_BASE", "/data/zoujin/deng/zebrafish_gene_qa")
RUNTIME = os.environ.get("ZF_RUNTIME", "/data/zoujin/deng/zebrafish_runtime")
TAG = time.strftime("%Y%m%d")


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def connect(retries: int = 3, pause: int = 45):
    last = None
    for i in range(retries):
        if i:
            time.sleep(pause)
        try:
            c = paramiko.SSHClient()
            c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            c.connect(HOST, PORT, USER, PASS, timeout=60, banner_timeout=90,
                      auth_timeout=120, allow_agent=False, look_for_keys=False)
            return c
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"连接失败（第 {i + 1}/{retries} 次）：{type(e).__name__} {e}")
    raise SystemExit(
        f"无法连接服务器：{last}\n"
        "判别（别再把 EOF 一律当'连接惩罚期'）：\n"
        "  · 密码错 → 'Authentication failed.'，约 3 s（PAM 延迟）；\n"
        "  · 密码对但账户/会话层被拒 → ~0.3 s 内被掐断，'transport shut down or saw EOF'。\n"
        "    已确认的一次原因是 deng 账号过期（ssh 提示 'Your account has expired'），\n"
        "    需管理员执行 `sudo chage -E -1 deng`。详见 /memories/repo/server-access.md"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", default=DEFAULT_FILES, help="仓库内相对路径，默认推送待办清单")
    ap.add_argument("--dry-run", action="store_true", help="只比较差异，不写入")
    args = ap.parse_args()

    files = args.files or DEFAULT_FILES
    # Windows 下命令行常传 web\js\app.js，反斜杠在 Linux 上会变成文件名的一部分，
    # 于是写成 /data/.../web\js\app.js 这种垃圾文件，而回读校验读的是同一个错路径，
    # 结果会“假成功”。这里统一规范成正斜杠。
    files = [f.replace("\\", "/").lstrip("/") for f in files]
    for rel in files:
        if not (ROOT / rel).exists():
            print(f"本地不存在，跳过：{rel}")
            return 1

    c = connect()
    sftp = c.open_sftp()
    pushed, same, failed = [], [], []
    for rel in files:
        local = ROOT / rel
        remote = f"{BASE}/{rel}"
        new = local.read_bytes()
        try:
            with sftp.open(remote, "rb") as f:
                old = f.read()
        except Exception:
            old = None
        if old is not None and md5(old) == md5(new):
            print(f"SAME  {rel}")
            same.append(rel)
            continue
        if args.dry_run:
            print(f"DIFF  {rel}（本地 {len(new)} B，远端 {len(old) if old else 0} B）")
            continue
        if old is not None:
            with sftp.open(f"{remote}.bak-pushweb-{TAG}", "wb") as f:
                f.write(old)
        with sftp.open(remote, "wb") as f:
            f.write(new)
        # 回读校验
        with sftp.open(remote, "rb") as f:
            back = f.read()
        ok = md5(back) == md5(new)
        if ok:
            # 确认远端确实落在预期路径（防止写到了别的目录/文件名）
            try:
                ok = sftp.stat(remote).st_size == len(new)
            except Exception:  # noqa: BLE001
                ok = False
        print(f"{'PUSH ' if ok else 'FAIL '} {rel}  {len(new)} B{'  (已备份)' if old is not None else '  (新文件)'}")
        (pushed if ok else failed).append(rel)
    sftp.close()

    _, out, _ = c.exec_command(
        f'echo "== 公网地址 =="; cat {RUNTIME}/public_url.txt 2>/dev/null; echo; '
        f'echo "== Hub 健康 =="; curl -s -m 20 -o /dev/null -w "%{{http_code}}\\n" http://127.0.0.1:8080/api/health; '
        f'echo "== 守护最近动作 =="; tail -2 {RUNTIME}/logs/hub_guard.log',
        timeout=120,
    )
    print(out.read().decode("utf-8", "replace"))
    c.close()

    print(f"\n汇总：推送 {len(pushed)}，未变 {len(same)}，失败 {len(failed)}")
    if pushed:
        print("静态文件无需重启 Hub；如页面没变化，检查 index.html 的 ?v= 是否已升级。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
