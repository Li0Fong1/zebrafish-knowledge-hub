# -*- coding: utf-8 -*-
"""直连 MySQL 赋 ZFQA 号（不走 bootstrap，避免加载模型卡死）。"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_env() -> dict:
    env: dict[str, str] = {}
    for p in (ROOT / ".env", Path("/data/zoujin/deng/zebrafish_gene_qa/.env")):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> int:
    try:
        import pymysql
    except ImportError:
        print("need pymysql", file=sys.stderr)
        return 1

    env = _load_env()
    host = env.get("MYSQL_HOST", os.getenv("MYSQL_HOST", "127.0.0.1"))
    port = int(env.get("MYSQL_PORT", os.getenv("MYSQL_PORT", "3307")))
    user = env.get("MYSQL_USER", os.getenv("MYSQL_USER", "zebrafish"))
    pwd = env.get("MYSQL_PASSWORD", os.getenv("MYSQL_PASSWORD", ""))
    dbn = env.get("MYSQL_DATABASE", os.getenv("MYSQL_DATABASE", "zebrafish_db"))
    print(f"connect {host}:{port}/{dbn} as {user}", flush=True)

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=pwd,
        database=dbn,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=15,
        read_timeout=120,
        write_timeout=120,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION innodb_lock_wait_timeout=5")
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='literature_paper' "
                "AND COLUMN_NAME='zfqa_id'"
            )
            if int(cur.fetchone()[0] or 0) == 0:
                print("ADD COLUMN zfqa_id …", flush=True)
                cur.execute(
                    "ALTER TABLE literature_paper "
                    "ADD COLUMN zfqa_id VARCHAR(16) NULL COMMENT 'ZFQA stable id' AFTER doc_id"
                )
                try:
                    cur.execute("ALTER TABLE literature_paper ADD UNIQUE KEY uk_zfqa_id (zfqa_id)")
                except Exception as ex:
                    print(f"unique key warn: {ex}", flush=True)
                conn.commit()

            cur.execute(
                "SELECT MAX(CAST(SUBSTRING(zfqa_id, 6) AS UNSIGNED)) "
                "FROM literature_paper WHERE zfqa_id REGEXP '^ZFQA-[0-9]{6}$'"
            )
            seq = int(cur.fetchone()[0] or 0)
            print(f"max_seq={seq}", flush=True)

            batch = 500
            assigned = 0
            while True:
                cur.execute(
                    "SELECT doc_id FROM literature_paper "
                    "WHERE zfqa_id IS NULL OR zfqa_id='' "
                    "ORDER BY doc_id LIMIT %s",
                    (batch,),
                )
                rows = cur.fetchall()
                if not rows:
                    break
                for (doc_id,) in rows:
                    seq += 1
                    zid = f"ZFQA-{seq:06d}"
                    cur.execute(
                        "UPDATE literature_paper SET zfqa_id=%s WHERE doc_id=%s",
                        (zid, doc_id),
                    )
                    assigned += 1
                conn.commit()
                print(f"assigned={assigned} seq={seq}", flush=True)

            cur.execute("SELECT COUNT(*) FROM literature_paper")
            total = int(cur.fetchone()[0])
            cur.execute(
                "SELECT COUNT(*) FROM literature_paper "
                "WHERE zfqa_id IS NOT NULL AND zfqa_id<>''"
            )
            filled = int(cur.fetchone()[0])
            print(f"[验收] total={total} with_zfqa={filled} missing={total - filled}", flush=True)
            cur.execute(
                "SELECT zfqa_id, pmid FROM literature_paper "
                "WHERE zfqa_id IS NOT NULL ORDER BY zfqa_id LIMIT 5"
            )
            for r in cur.fetchall():
                print(f"  {r[0]} pmid={r[1]}", flush=True)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
