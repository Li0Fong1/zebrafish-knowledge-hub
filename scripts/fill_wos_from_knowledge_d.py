# -*- coding: utf-8 -*-
"""把 rag/knowledge.d 中已入库的 WoS 文本还原为 RIS 写入 literature_wos。"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.entry import bootstrap_app

bootstrap_app()

from sqlalchemy import text

from common_utils import safe_print
from mysql_client import MySQLClient

ROOT_SQL = ROOT / "sql" / "literature_wos_migration.sql"


def _ensure_schema(client: MySQLClient) -> None:
    db = next(client.get_db())
    try:
        for stmt in ROOT_SQL.read_text(encoding="utf-8").replace("\r\n", "\n").split(";"):
            s = " ".join(
                ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")
            ).strip()
            if not s or s.upper().startswith("USE "):
                continue
            db.execute(text(s))
        db.commit()
    finally:
        db.close()

BLOCK_RE = re.compile(r"【WOS\s+([^\]]+)】\s*(.*?)(?=\n【WOS|\Z)", re.S)
META_RE = re.compile(
    r"来源信息：(?P<src>.*?)\n作者：(?P<au>.*?)\n摘要：(?P<ab>.*)",
    re.S,
)


def to_ris(ut: str, title: str, src: str, authors: str, abstract: str, source_file: str) -> str:
    journal, year, doi = "", "", ""
    bits = [b.strip() for b in (src or "").split("|")]
    if bits:
        journal = bits[0]
    for b in bits[1:]:
        if re.fullmatch(r"\d{4}", b):
            year = b
        elif b.upper().startswith("DOI:"):
            doi = b.split(":", 1)[-1].strip()
    lines = ["TY  - JOUR", f"TI  - {title.strip()}"]
    for au in [a.strip() for a in (authors or "").split(";") if a.strip()][:20]:
        lines.append(f"AU  - {au}")
    if journal:
        lines.append(f"JO  - {journal}")
    if year:
        lines.append(f"PY  - {year}")
    if doi:
        lines.append(f"DO  - {doi}")
    if abstract and "无摘要" not in abstract[:12]:
        lines.append(f"AB  - {abstract.strip()}")
    wos = ut.replace("WOS:", "").strip()
    lines.append(f"UT  - WOS:{wos}")
    lines.append("ER  - ")
    return "\n".join(lines) + "\n"


def main():
    client = MySQLClient()
    _ensure_schema(client)
    db = next(client.get_db())
    n = 0
    kd = ROOT / "rag" / "knowledge.d"
    files = sorted(kd.glob("literature*wos*.txt"))
    try:
        for fp in files:
            text_body = fp.read_text(encoding="utf-8", errors="replace")
            for m in BLOCK_RE.finditer(text_body):
                ut = m.group(1).strip()
                rest = m.group(2).strip()
                title, _, tail = rest.partition("\n")
                mm = META_RE.search(tail)
                src = au = ab = ""
                if mm:
                    src, au, ab = mm.group("src"), mm.group("au"), mm.group("ab")
                wos_ut = ut.replace("WOS:", "").strip() or hashlib.md5(title.encode()).hexdigest()[:16]
                doi = ""
                for b in src.split("|"):
                    b = b.strip()
                    if b.upper().startswith("DOI:"):
                        doi = b.split(":", 1)[-1].strip()
                ris = to_ris(ut, title, src, au, ab, fp.name)
                db.execute(
                    text(
                        "INSERT INTO literature_wos (wos_ut, raw_ris, source_file, pmid, doi) "
                        "VALUES (:u,:r,:s,NULL,:d) "
                        "ON DUPLICATE KEY UPDATE raw_ris=VALUES(raw_ris), doi=VALUES(doi)"
                    ),
                    {"u": wos_ut[:64], "r": ris, "s": fp.name, "d": doi or None},
                )
                n += 1
                if n % 200 == 0:
                    db.commit()
                    safe_print(f"  … {n}")
        db.commit()
        total = db.execute(text("SELECT COUNT(*) FROM literature_wos")).scalar()
        safe_print(f"DONE inserted_or_updated={n} table={total}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
