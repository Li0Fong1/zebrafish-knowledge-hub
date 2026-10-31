# -*- coding: utf-8 -*-
"""直连 MySQL 写入 journal_metric（不走 bootstrap）。"""
from __future__ import annotations

import csv
import math
import os
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURATED = ROOT / "data" / "journal_tier_curated.csv"
TIER_BOOST = {"T0": 1.0, "T1": 0.88, "T2": 0.68, "T3": 0.52, "T4": 0.45}
UNKNOWN = 0.45
CAP = 40.0


def norm_journal(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:].strip()
    return re.sub(r"\s+", " ", s).strip()


def if_norm(v):
    if v is None:
        return UNKNOWN
    return max(0.0, min(1.0, math.log1p(float(v)) / math.log1p(CAP)))


def load_env():
    env = {}
    for p in (ROOT / ".env", Path("/data/zoujin/deng/zebrafish_gene_qa/.env")):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def main() -> int:
    import pymysql

    env = load_env()
    conn = pymysql.connect(
        host=env.get("MYSQL_HOST", "127.0.0.1"),
        port=int(env.get("MYSQL_PORT", "3307")),
        user=env.get("MYSQL_USER", "zebrafish"),
        password=env.get("MYSQL_PASSWORD", ""),
        database=env.get("MYSQL_DATABASE", "zebrafish_db"),
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=15,
        read_timeout=60,
        write_timeout=60,
    )
    print("connected", flush=True)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS journal_metric ("
            "id BIGINT AUTO_INCREMENT PRIMARY KEY,"
            "journal_key VARCHAR(256) NOT NULL,"
            "display_name VARCHAR(512) NULL,"
            "issn_print VARCHAR(16) NULL,"
            "issn_electronic VARCHAR(16) NULL,"
            "if_value DECIMAL(8,3) NULL,"
            "if_year SMALLINT NULL,"
            "if_source VARCHAR(32) NOT NULL DEFAULT 'curated',"
            "tier CHAR(2) NOT NULL DEFAULT 'T4',"
            "tier_boost DECIMAL(4,3) NOT NULL DEFAULT 0.450,"
            "if_norm DECIMAL(4,3) NOT NULL DEFAULT 0.450,"
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,"
            "UNIQUE KEY uk_journal_key (journal_key)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
        conn.commit()
        n = 0
        if CURATED.is_file():
            with CURATED.open(encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    key = norm_journal(r.get("journal_key") or r.get("display_name") or "")
                    if not key:
                        continue
                    raw = (r.get("if_value") or "").strip()
                    iv = float(raw) if raw else None
                    tier = (r.get("tier") or "T4").upper()
                    if tier not in TIER_BOOST:
                        tier = "T4"
                    cur.execute(
                        "INSERT INTO journal_metric "
                        "(journal_key, display_name, if_value, if_year, if_source, tier, tier_boost, if_norm) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), if_value=VALUES(if_value), "
                        "tier=VALUES(tier), tier_boost=VALUES(tier_boost), if_norm=VALUES(if_norm)",
                        (
                            key,
                            (r.get("display_name") or key)[:512],
                            iv,
                            2024,
                            (r.get("if_source") or "curated")[:32],
                            tier,
                            TIER_BOOST[tier],
                            if_norm(iv),
                        ),
                    )
                    n += 1
            conn.commit()
        print(f"curated_upsert={n}", flush=True)
        cur.execute("SELECT COUNT(*) FROM journal_metric")
        print(f"journal_metric_rows={cur.fetchone()[0]}", flush=True)
        cur.execute("SELECT tier, COUNT(*) FROM journal_metric GROUP BY tier ORDER BY tier")
        for tier, cnt in cur.fetchall():
            print(f"  {tier}: {cnt}", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
