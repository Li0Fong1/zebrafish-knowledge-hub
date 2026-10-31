# -*- coding: utf-8 -*-
"""上机核实关键工具版本号，并写入论文数据包（METHODS 数字对账用）。

背景：METHODS 2.7/2.8 声明了 BLAST+ 与 MySQL 版本，但**仓库里没有记录服务器端真实版本**：
`database/seq_toolkit.py` 的 engine 串是硬编码 "NCBI BLAST+ blastn"，不含版本；
conda 安装是 `-c bioconda blast` **未锁版本**，所以只能上机读 `blastn -version`。

本脚本**只读**：不动服务、不改文件、不重启。
连不上（账号过期 / sshd 拒绝）会如实报错，不猜数字。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts\\probe_tool_versions.py
    .venv\\Scripts\\python.exe -X utf8 scripts\\probe_tool_versions.py --print-only

输出：
    docs/paper/provenance/tool_versions.json

连接信息按下面的默认值走，也可用环境变量覆盖：
    ZF_HOST / ZF_PORT / ZF_USER / ZF_PASS / ZF_BASE / ZF_RUNTIME
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
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
OUT = ROOT / "docs" / "paper" / "data_package" / "05_provenance" / "tool_versions.json"

HOST = _zf_host()
PORT = _zf_port()
USER = _zf_user()
PASS = _zf_pass()
BASE = os.environ.get("ZF_BASE", "/data/zoujin/deng/zebrafish_gene_qa")
RT = os.environ.get("ZF_RUNTIME", "/data/zoujin/deng/zebrafish_runtime")

CONDA = "/data/zoujin/deng/miniconda3/bin/conda"
PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"

# 只读探针：命令 → 说明
PROBES = {
    "blastn_version": f"{RT}/conda_blast/bin/blastn -version 2>&1 | head -n 3",
    "makeblastdb_version": f"{RT}/conda_blast/bin/makeblastdb -version 2>&1 | head -n 3",
    "blast_bin_ls": f"ls -l {RT}/conda_blast/bin/blastn {RT}/conda_blast/bin/makeblastdb 2>&1",
    "conda_blast_pkg": (
        f"{CONDA} list -p {RT}/conda_blast 2>/dev/null | grep -iE '^(blast|ncbi-blast|blast\\+)' "
        "|| echo '(conda list 不可用或非 conda 安装)'"
    ),
    "mysql_client_version": f"{RT}/mysql80/bin/mysql --version 2>&1",
    "mysqld_version": f"{RT}/mysql80/bin/mysqld --version 2>&1",
    "python_version": f"{PY} -V 2>&1",
    "env_blast_keys": (
        f"grep -hE '^(BLAST_BIN|BLAST_DB_DIR|BLAST_FASTA)=' {BASE}/.env 2>/dev/null "
        "|| echo '(未在 .env 显式配置，走代码默认)'"
    ),
}

RE_BLAST = re.compile(r"(blast[ntx]|makeblastdb):\s*([0-9][0-9.]+\+?)")
RE_MYSQL = re.compile(r"(?:Distrib|Ver)\s+([0-9]+\.[0-9]+\.[0-9]+)")
RE_PY = re.compile(r"Python\s+([0-9.]+)")


def connect(retries: int = 3, pause: int = 45) -> paramiko.SSHClient:
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
        "已知两类原因，别混：\n"
        "  1) 提示 'Your account has expired' → 账号过期，需管理员 `sudo chage -E -1 deng`；\n"
        "  2) 密码正确时连接被立刻掐断（<0.3s）→ 同样是账户/会话层被拒，不是网络抖动；\n"
        "     （密码错误会走 ~3s 的失败路径并正常返回 'Authentication failed.'）\n"
        "详见 /memories/repo/server-access.md"
    )


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 40) -> str:
    _, out, err = c.exec_command(cmd, timeout=timeout)
    out.channel.settimeout(timeout)
    out.channel.recv_exit_status()
    return (out.read() + err.read()).decode("utf-8", "replace").strip()


def parse_versions(raw: dict) -> dict:
    """从原始输出里抽出规范化版本号；抽不到就留空（不猜）。"""
    v = {}
    m = RE_BLAST.search(raw.get("blastn_version", ""))
    if m:
        v["blastn"] = m.group(2)
    m = RE_BLAST.search(raw.get("makeblastdb_version", ""))
    if m:
        v["makeblastdb"] = m.group(2)
    m = RE_MYSQL.search(raw.get("mysql_client_version", ""))
    if m:
        v["mysql_client"] = m.group(1)
    m = RE_MYSQL.search(raw.get("mysqld_version", ""))
    if m:
        v["mysqld"] = m.group(1)
    m = RE_PY.search(raw.get("python_version", ""))
    if m:
        v["python"] = m.group(1)
    pkg = raw.get("conda_blast_pkg", "")
    m = re.search(r"^blast\s+(\S+)", pkg, re.M)
    if m:
        v["conda_blast_package"] = m.group(1)
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-only", action="store_true", help="只打印，不写文件")
    args = ap.parse_args()

    print(f"=== 上机核实工具版本 | {HOST}:{PORT} ===")
    c = connect()
    try:
        raw = {}
        for key, cmd in PROBES.items():
            raw[key] = run(c, cmd)
            print(f"  [{key}] {raw[key].splitlines()[0] if raw[key] else '(空)'}")
    finally:
        c.close()

    tz = timezone(timedelta(hours=8))
    payload = {
        "_note": (
            "服务器端工具版本实拿快照（供 METHODS 数字对账）。只读探测，由 "
            "scripts/probe_tool_versions.py 生成；未取到的一律留空，**不猜**。"
        ),
        "probed_at": datetime.now(tz).isoformat(timespec="seconds"),
        "host": f"{HOST}:{PORT}",
        "base": BASE,
        "runtime": RT,
        "tool_versions": parse_versions(raw),
        "raw": raw,
    }

    v = payload["tool_versions"]
    print("\n=== 规范化版本 ===")
    for k in ("blastn", "makeblastdb", "conda_blast_package", "mysql_client", "mysqld", "python"):
        print(f"  {k:22s} {v.get(k, '(未取到)')}")

    if args.print_only:
        print("\n(--print-only：未写文件)")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n已写入 {OUT.relative_to(ROOT)}")
    print("提示：回填 METHODS 前先跑 scripts\\verify_methods_numbers.py 确认一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
