# -*- coding: utf-8 -*-
"""把后端 Python / 配置文件推送到服务器并可选重启 Hub。

为什么需要这个脚本：`scripts/push_web.py` 只管 web/ 静态资源，后端改动用 SFTP
只能临时写脚本。问题也出在这里 —— 本地改了 `database/genome_toolkit.py` 却没推，
线上 `/api/genome/compare` 一直 503（服务器还是 10-13 的旧版），前端「Liftover」
页面打不开，用户以为功能不存在。

用法：
    python scripts/push_backend.py database/genome_toolkit.py --restart
    python scripts/push_backend.py agent/hub_orchestrator.py llm/zebrafish_prompt.py

行为：
    · 路径相对项目根，映射到服务器 $ZF_BASE 下的同名路径
    · 覆盖前把远端文件备份成 `<file>.bak.<时间戳>`
    · 上传后比对 md5，不一致就报错（不做静默成功）
    · --restart 调 $ZF_RUNTIME/accept_restart_hub.sh，并等 /api/health 回 200
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            if k.strip() == name:
                return val.strip().strip('"').strip("'")
    return default


def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="相对项目根的路径")
    ap.add_argument("--restart", action="store_true", help="推送后重启 Hub")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    host = env("ZF_HOST")
    if not host:
        print("[FAIL] .env 缺少 ZF_HOST")
        return 2
    base = env("ZF_BASE", "/data/zoujin/deng/zebrafish_gene_qa").rstrip("/")
    runtime = env("ZF_RUNTIME", "/data/zoujin/deng/zebrafish_runtime").rstrip("/")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host,
        port=int(env("ZF_PORT", "22")),
        username=env("ZF_USER"),
        password=env("ZF_PASS"),
        timeout=25,
    )
    sftp = client.open_sftp()

    def run(cmd: str, timeout: int = 120) -> str:
        _i, o, e = client.exec_command(cmd, timeout=timeout)
        return (o.read() + e.read()).decode("utf-8", "replace").strip()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    ok = 0
    fail: list[str] = []
    for rel in args.files:
        local = (ROOT / rel).resolve()
        if not local.is_file():
            print(f"[SKIP] 本地不存在：{rel}")
            fail.append(rel)
            continue
        remote = f"{base}/{rel}"
        data = local.read_bytes()
        local_md5 = md5_bytes(data)
        try:
            run(f"mkdir -p '{os.path.dirname(remote)}'")
            if not args.no_backup:
                run(f"test -f '{remote}' && cp -p '{remote}' '{remote}.bak.{stamp}' || true")
            with sftp.file(remote, "wb") as fh:
                fh.write(data)
            remote_md5 = run(f"md5sum '{remote}' | awk '{{print $1}}'")
            if remote_md5 != local_md5:
                print(f"[FAIL] md5 不一致 {rel}: local={local_md5[:10]} remote={remote_md5[:10]}")
                fail.append(rel)
                continue
            print(f"[PUSH] {rel}  ({len(data)} bytes, md5={local_md5[:10]})")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {rel}: {type(e).__name__}: {e}")
            fail.append(rel)

    sftp.close()

    if args.restart:
        print("[..] 重启 Hub")
        print(run(f"bash {runtime}/accept_restart_hub.sh 2>&1 | tail -12", timeout=180))
        healthy = False
        for i in range(30):
            time.sleep(2)
            code = run("curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8080/api/health")
            if code == "200":
                healthy = True
                print(f"[OK] health=200（{2 * (i + 1)}s）")
                break
        if not healthy:
            print("[WARN] health 未在 60s 内恢复，请看日志")
        print("[..] 进程：")
        print(run("ps -eo pid,etime,args | grep '[u]vicorn api.api_server' | head -3"))

    client.close()
    print(f"\n汇总：成功 {ok} / {len(args.files)}")
    if fail:
        print("失败：" + ", ".join(fail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
