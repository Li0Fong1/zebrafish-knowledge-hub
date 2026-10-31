# -*- coding: utf-8 -*-
"""从服务器增量同步 MySQL（表级 mysqldump）与 FAISS 索引目录。

默认：
  - MySQL：按表 dump → 导入本机（需本机可连目标库，凭据来自本机 .env）
  - FAISS：rsync/scp 同步 runtime 下当前使用的索引目录（约 60GB+，可后台）

用法（本机）:
  python scripts/sync_server_data_incremental.py --mysql-only
  python scripts/sync_server_data_incremental.py --faiss-only --faiss-dir faiss_pubmed_yes_qwen3_4b_v2_merged
  python scripts/sync_server_data_incremental.py --dry-run
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
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

ROOT = Path(__file__).resolve().parents[1]
KEY = ROOT / "logs" / "_zf_sync_ed25519"
KNOWN = ROOT / "logs" / "_zf_known_hosts"
HOST, PORT, USER, PWD = _zf_host(), _zf_port(), _zf_user(), _zf_pass()
REMOTE_BASE = "/data/zoujin/deng/zebrafish_gene_qa"
REMOTE_RT = "/data/zoujin/deng/zebrafish_runtime"
LOCAL_RT = Path(os.getenv("ZEBRAFISH_RUNTIME", str(ROOT / "zebrafish_runtime")))

# 业务表（不含 hub 会话临时表可按需跳过）
TABLES = [
    "assembly_registry",
    "ensembl_id_version",
    "gene",
    "gene_alias",
    "gene_assembly_xref",
    "gene_basic",
    "gene_expression",
    "gene_go",
    "gene_kegg",
    "gene_name_bundle",
    "gene_reference",
    "gene_xref",
    "homology_gene",
    "journal_metric",
    "literature_fulltext_gold",
    "literature_paper",
    "literature_pubmed",
    "literature_pubmed_judgement",
    "literature_vector_chunk",
    "literature_vector_shard",
    "literature_wos",
    "name_hit_unique",
    "name_resolve_collision",
    "workflow_definition",
]


def connect() -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.load_host_keys(str(KNOWN))
    c.set_missing_host_key_policy(paramiko.RejectPolicy())
    c.connect(
        HOST,
        PORT,
        USER,
        key_filename=str(KEY),
        look_for_keys=False,
        allow_agent=False,
        timeout=60,
        banner_timeout=60,
        auth_timeout=60,
    )
    t = c.get_transport()
    if t:
        t.set_keepalive(30)
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 600) -> str:
    _, o, e = c.exec_command(cmd, timeout=timeout)
    o.channel.recv_exit_status()
    return (o.read() + e.read()).decode("utf-8", "replace")


def load_local_env() -> dict[str, str]:
    cfg: dict[str, str] = {}
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def sync_mysql(c: paramiko.SSHClient, *, dry: bool) -> None:
    dump_dir = f"{REMOTE_RT}/logs/_mysql_sync_dump"
    local_dir = ROOT / "logs" / "_mysql_sync_dump"
    local_dir.mkdir(parents=True, exist_ok=True)
    print("MYSQL remote dump dir", dump_dir, flush=True)
    run(c, f"mkdir -p {dump_dir}")
    # remote .env for mysqldump
    remote_env = run(
        c,
        f"python - <<'PY'\n"
        f"from pathlib import Path\n"
        f"cfg={{}}\n"
        f"for line in Path('{REMOTE_BASE}/.env').read_text(errors='replace').splitlines():\n"
        f"  line=line.strip()\n"
        f"  if not line or line.startswith('#') or '=' not in line: continue\n"
        f"  k,_,v=line.partition('='); cfg[k.strip()]=v.strip().strip(chr(34)).strip(chr(39))\n"
        f"print(cfg.get('MYSQL_HOST','127.0.0.1'))\n"
        f"print(cfg.get('MYSQL_PORT','3306'))\n"
        f"print(cfg.get('MYSQL_USER','root'))\n"
        f"print(cfg.get('MYSQL_PASSWORD',''))\n"
        f"print(cfg.get('MYSQL_DATABASE','zebrafish_db'))\n"
        f"PY",
        timeout=30,
    ).strip().splitlines()
    while len(remote_env) < 5:
        remote_env.append("")
    rh, rp, ru, rpw, rdb = remote_env[:5]
    for table in TABLES:
        remote_sql = f"{dump_dir}/{table}.sql"
        print("DUMP", table, flush=True)
        if dry:
            continue
        # --skip-lock-tables / single-transaction for InnoDB
        cmd = (
            f"mysqldump -h{rh} -P{rp} -u{ru} -p{rpw!r} --single-transaction "
            f"--skip-lock-tables --no-tablespaces {rdb} {table} > {remote_sql}"
        )
        # avoid shell quoting issues with password
        cmd = (
            f"MYSQL_PWD={rpw} mysqldump -h{rh} -P{rp} -u{ru} --single-transaction "
            f"--skip-lock-tables --no-tablespaces {rdb} {table} > {remote_sql}"
        )
        out = run(c, cmd, timeout=1800)
        if out.strip():
            print(" dump_msg", out[:200], flush=True)
        # fetch
        sftp = c.open_sftp()
        local_sql = local_dir / f"{table}.sql"
        sftp.get(remote_sql, str(local_sql))
        sftp.close()
        print(" GET", local_sql, local_sql.stat().st_size, flush=True)

    if dry:
        return
    lcfg = load_local_env()
    lh = lcfg.get("MYSQL_HOST", "127.0.0.1")
    lp = lcfg.get("MYSQL_PORT", "3306")
    lu = lcfg.get("MYSQL_USER", "root")
    lpw = lcfg.get("MYSQL_PASSWORD", "")
    ldb = lcfg.get("MYSQL_DATABASE", "zebrafish_db")
    mysql = os.getenv("MYSQL_CLIENT", "mysql")
    for table in TABLES:
        local_sql = local_dir / f"{table}.sql"
        if not local_sql.is_file():
            print("SKIP_MISSING", table, flush=True)
            continue
        print("IMPORT", table, flush=True)
        env = os.environ.copy()
        if lpw:
            env["MYSQL_PWD"] = lpw
        # replace table data: source dump usually includes DROP/CREATE or just INSERT
        proc = subprocess.run(
            [mysql, f"-h{lh}", f"-P{lp}", f"-u{lu}", ldb],
            input=local_sql.read_text(encoding="utf-8", errors="replace"),
            text=True,
            env=env,
            capture_output=True,
        )
        if proc.returncode != 0:
            print("IMPORT_FAIL", table, proc.stderr[:500], flush=True)
        else:
            print("IMPORT_OK", table, flush=True)


def sync_faiss(c: paramiko.SSHClient, faiss_dir: str, *, dry: bool) -> None:
    remote = f"{REMOTE_RT}/{faiss_dir}"
    local = LOCAL_RT / faiss_dir
    local.parent.mkdir(parents=True, exist_ok=True)
    print("FAISS", remote, "->", local, flush=True)
    if dry:
        print(run(c, f"du -sh {remote} 2>/dev/null; ls {remote} | head", timeout=60), flush=True)
        return
    # Prefer scp -r via paramiko SFTP walk is too slow for 60GB; use system scp/rsync if available
    # OpenSSH scp with -P and identity
    cmd = [
        "scp",
        "-P",
        str(PORT),
        "-i",
        str(KEY),
        "-o",
        f"UserKnownHostsFile={KNOWN}",
        "-o",
        "StrictHostKeyChecking=yes",
        "-r",
        f"{USER}@{HOST}:{remote}",
        str(local.parent),
    ]
    print("RUN", " ".join(cmd[:8]), "...", flush=True)
    # Note: 60GB — user should run with network; we start and report
    subprocess.Popen(cmd)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mysql-only", action="store_true")
    ap.add_argument("--faiss-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--faiss-dir",
        default="faiss_pubmed_yes_qwen3_4b_v2_merged",
        help="runtime 下索引目录名",
    )
    args = ap.parse_args()
    c = connect()
    print("SSH_OK", flush=True)
    do_mysql = args.mysql_only or not args.faiss_only
    do_faiss = args.faiss_only or not args.mysql_only
    if args.mysql_only:
        do_faiss = False
    if args.faiss_only:
        do_mysql = False
    if do_mysql:
        sync_mysql(c, dry=args.dry_run)
    if do_faiss:
        sync_faiss(c, args.faiss_dir, dry=args.dry_run)
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
