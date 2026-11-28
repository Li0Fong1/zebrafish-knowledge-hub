# -*- coding: utf-8 -*-
"""把本地生成的 Ensembl 官方基因集部署到线上（一键）。

链路：
  1. SFTP 上传 data/idmap_local.sqlite3  → /tmp/ens_official_src.sqlite3
  2. SFTP 上传 database/ensembl_version_map.py、
     scripts/import_ensembl_official_gtf.py
  3. 服务器上把 ens_gene_official 表合并进 idmap_data.sqlite3（ATTACH + INSERT）
  4. 校验服务器端行数 / release 分布 / tp53 坐标，并做一次纯 SQLite 的转换自测

用法（在项目根目录）：
    python scripts/deploy_ensembl_official.py
    python scripts/deploy_ensembl_official.py --skip-code   # 只同步数据

连接信息沿用 scripts/remote_exec.py 的 .env 约定（ZF_HOST/ZF_PORT/ZF_USER/ZF_PASS）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import remote_exec as re  # noqa: E402

LOCAL_DB = ROOT / "data" / "idmap_local.sqlite3"
REMOTE_TMP_DB = "/tmp/ens_official_src.sqlite3"
REMOTE_BASE = re.env("ZF_BASE", "/data/zoujin/deng/zebrafish_gene_qa")
REMOTE_RUNTIME = re.env("ZF_RUNTIME", "/data/zoujin/deng/zebrafish_runtime")
REMOTE_MAIN_DB = REMOTE_RUNTIME + "/idmap_data.sqlite3"
PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"

MERGE_SQL = r'''
import sqlite3, sys
main_path, src_path = sys.argv[1], sys.argv[2]
con = sqlite3.connect(main_path)
con.execute("PRAGMA journal_mode=WAL")
con.execute("""CREATE TABLE IF NOT EXISTS ens_gene_official (
  release INTEGER NOT NULL, assembly TEXT NOT NULL,
  ensembl_gene_id TEXT NOT NULL, symbol TEXT, chrom TEXT,
  start INTEGER, end INTEGER, strand TEXT, biotype TEXT,
  PRIMARY KEY (release, ensembl_gene_id))""")
con.execute("CREATE INDEX IF NOT EXISTS ix_ego_rel_id  ON ens_gene_official(release, ensembl_gene_id)")
con.execute("CREATE INDEX IF NOT EXISTS ix_ego_rel_sym ON ens_gene_official(release, symbol)")
con.execute("ATTACH DATABASE ? AS src", (src_path,))
con.execute("DELETE FROM ens_gene_official")
con.execute("INSERT INTO ens_gene_official SELECT release,assembly,ensembl_gene_id,symbol,chrom,start,end,strand,biotype FROM src.ens_gene_official")
con.commit()
rows = con.execute("SELECT release, COUNT(*) FROM ens_gene_official GROUP BY release ORDER BY release").fetchall()
print("server ens_gene_official:", rows)
tp = con.execute("SELECT ensembl_gene_id,chrom,start,end,strand FROM ens_gene_official WHERE release=116 AND symbol='tp53' COLLATE NOCASE").fetchall()
print("server r116 tp53:", tp)
con.close()
'''


def sh(cmd: str, timeout: int = 120) -> tuple[int, str]:
    return re.run(cmd, timeout=timeout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-code", action="store_true")
    args = ap.parse_args()

    if not LOCAL_DB.is_file():
        print("[FAIL] 本地库不存在：%s（先跑 scripts/import_ensembl_official_gtf.py）" % LOCAL_DB)
        return 2

    c = re.connect()
    try:
        sftp = c.open_sftp()
        try:
            print("[put] %s -> %s" % (LOCAL_DB, REMOTE_TMP_DB), flush=True)
            sftp.put(str(LOCAL_DB), REMOTE_TMP_DB)
            if not args.skip_code:
                for local, remote in [
                    (ROOT / "database" / "ensembl_version_map.py",
                     REMOTE_BASE + "/database/ensembl_version_map.py"),
                    (ROOT / "scripts" / "import_ensembl_official_gtf.py",
                     REMOTE_BASE + "/scripts/import_ensembl_official_gtf.py"),
                ]:
                    sftp.put(str(local), remote)
                    print("[put] %s -> %s" % (local.name, remote), flush=True)
        finally:
            sftp.close()
    finally:
        c.close()

    sftp_put_sql = "/tmp/_merge_ens_official.py"
    c = re.connect()
    try:
        sftp = c.open_sftp()
        with sftp.file(sftp_put_sql, "wb") as fh:
            fh.write(MERGE_SQL.encode("utf-8"))
        sftp.close()
    finally:
        c.close()

    rc, out = sh("%s %s %s %s" % (PY, sftp_put_sql, REMOTE_MAIN_DB, REMOTE_TMP_DB), timeout=300)
    print(out)
    rc2, out2 = sh("%s -c \"import sqlite3;print(sqlite3.connect('%s').execute('SELECT COUNT(*) FROM ens_gene_official').fetchone())\""
                   % (PY, REMOTE_MAIN_DB), timeout=60)
    print(out2)
    return 0 if (rc == 0 and rc2 == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
