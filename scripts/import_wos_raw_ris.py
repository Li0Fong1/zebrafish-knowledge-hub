# -*- coding: utf-8 -*-
"""
从 WoS 导出的 RIS 文件批量入库 literature_wos（单条 RIS 原文，不做任何加工）。

默认目录：data/wos_import、wos数据包/Downloads、项目阶段/05_数据与库/归档/WosRIS导入 等（见 rag.wos_ris_paths）

用法：
  python -m scripts.import_wos_raw_ris
  python -m scripts.import_wos_raw_ris --dir "wos数据包/Downloads"
  python -m scripts.import_wos_raw_ris --resume
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from sqlalchemy import text

from common_utils import safe_print
from config import _BASE_DIR
from mysql_client import MySQLClient

from rag.wos_ris_paths import discover_wos_ris_dirs, list_wos_ris_files

PROGRESS_FILE = os.path.join(_BASE_DIR, "data", "literature", "wos_raw_import.json")
DEFAULT_DIRS = discover_wos_ris_dirs()
INSERT_BATCH = int(os.getenv("WOS_RAW_INSERT_BATCH", "200"))


def _ensure_schema(client: MySQLClient) -> None:
    sql_path = ROOT / "sql" / "literature_wos_migration.sql"
    if not sql_path.is_file():
        raise FileNotFoundError(sql_path)
    db = next(client.get_db())
    try:
        for stmt in sql_path.read_text(encoding="utf-8").replace("\r\n", "\n").split(";"):
            s = " ".join(
                ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")
            ).strip()
            if not s or s.upper().startswith("USE "):
                continue
            db.execute(text(s))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _load_progress() -> dict:
    if os.path.isfile(PROGRESS_FILE):
        return json.loads(Path(PROGRESS_FILE).read_text(encoding="utf-8"))
    return {}


def _save_progress(state: dict) -> None:
    os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
    Path(PROGRESS_FILE).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _split_ris_records(text: str) -> List[str]:
    blocks = re.split(r"\n(?=TY  - )", text)
    out: List[str] = []
    for block in blocks:
        block = block.strip()
        if not block.startswith("TY"):
            continue
        if "ER  -" not in block:
            block += "\nER  -"
        out.append(block)
    return out


def _field(block: str, tag: str) -> str:
    prefix = f"{tag}  - "
    for line in block.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def _record_key(block: str) -> Tuple[str, str, str]:
    """返回 (wos_ut, pmid, doi)。"""
    ut_raw = _field(block, "UT") or _field(block, "AN")
    wos_ut = ut_raw.replace("WOS:", "").strip()
    pmid = _field(block, "PM")
    doi = _field(block, "DO")
    if not wos_ut:
        if pmid:
            wos_ut = f"MEDLINE_{pmid}"
        elif doi:
            wos_ut = f"DOI_{doi.lower()}"
        else:
            wos_ut = "RIS_" + hashlib.md5(block.encode("utf-8", errors="replace")).hexdigest()[:20]
    return wos_ut, pmid, doi


def _list_ris_files(dirs: List[str]) -> List[str]:
    return list_wos_ris_files(dirs)


def _insert_batch(
    client: MySQLClient,
    rows: List[Tuple[str, str, str, str, str]],
) -> int:
    if not rows:
        return 0
    db = next(client.get_db())
    n = 0
    try:
        for wos_ut, raw_ris, source_file, pmid, doi in rows:
            db.execute(
                text(
                    "INSERT INTO literature_wos (wos_ut, raw_ris, source_file, pmid, doi, imported_at) "
                    "VALUES (:ut, :raw, :src, :pmid, :doi, NOW()) "
                    "ON DUPLICATE KEY UPDATE raw_ris=VALUES(raw_ris), source_file=VALUES(source_file), "
                    "pmid=VALUES(pmid), doi=VALUES(doi), imported_at=NOW()"
                ),
                {
                    "ut": wos_ut[:64],
                    "raw": raw_ris,
                    "src": source_file[:512],
                    "pmid": pmid[:16] or None,
                    "doi": doi[:256] or None,
                },
            )
            n += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return n


def import_all(*, dirs: List[str], resume: bool = True) -> dict:
    client = MySQLClient()
    _ensure_schema(client)

    ris_files = _list_ris_files(dirs)
    if not ris_files:
        raise FileNotFoundError(f"未找到 RIS 文件，目录：{dirs}")

    safe_print(f"WoS RIS 原始入库：{len(ris_files)} 个文件")
    for p in ris_files[:5]:
        safe_print(f"  - {p}")
    if len(ris_files) > 5:
        safe_print(f"  … 共 {len(ris_files)} 个")

    state = _load_progress() if resume else {}
    file_idx = int(state.get("file_idx") or 0)
    record_idx = int(state.get("record_idx") or 0)
    imported = int(state.get("imported") or 0)
    skipped = int(state.get("skipped") or 0)
    total_records = int(state.get("total_records") or 0)
    job_id = state.get("job_id")

    if total_records <= 0:
        safe_print("  统计总记录数…")
        for path in ris_files:
            with open(path, encoding="utf-8", errors="replace") as f:
                total_records += len(_split_ris_records(f.read()))
        safe_print(f"  预计 {total_records:,} 条 RIS 记录（含跨文件重复 UT）")

    db = next(client.get_db())
    try:
        if not job_id:
            r = db.execute(
                text(
                    "INSERT INTO literature_import_job "
                    "(job_name, search_query, total_expected, imported_count, retstart, status) "
                    "VALUES ('wos_raw_ris', :q, :total, :imp, :rs, 'running')"
                ),
                {
                    "q": ";".join(dirs)[:512],
                    "total": total_records,
                    "imp": imported,
                    "rs": file_idx,
                },
            )
            db.commit()
            job_id = r.lastrowid
        else:
            db.execute(
                text(
                    "UPDATE literature_import_job SET status='running', total_expected=:total "
                    "WHERE id=:id"
                ),
                {"total": total_records, "id": job_id},
            )
            db.commit()
    finally:
        db.close()

    state.update({
        "job_id": job_id,
        "dirs": dirs,
        "total_records": total_records,
        "file_idx": file_idx,
        "record_idx": record_idx,
        "imported": imported,
        "skipped": skipped,
    })
    _save_progress(state)

    t0 = time.time()
    while file_idx < len(ris_files):
        path = ris_files[file_idx]
        fn = os.path.basename(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            records = _split_ris_records(f.read())

        if record_idx >= len(records):
            file_idx += 1
            record_idx = 0
            state["file_idx"] = file_idx
            state["record_idx"] = 0
            _save_progress(state)
            continue

        batch: List[Tuple[str, str, str, str, str]] = []
        while record_idx < len(records):
            block = records[record_idx]
            record_idx += 1
            wos_ut, pmid, doi = _record_key(block)
            batch.append((wos_ut, block, fn, pmid, doi))
            if len(batch) >= INSERT_BATCH:
                break

        n = _insert_batch(client, batch)
        imported += n
        state["imported"] = imported
        state["record_idx"] = record_idx
        state["current_file"] = fn
        _save_progress(state)

        if imported % 1000 < INSERT_BATCH or file_idx == len(ris_files) - 1:
            elapsed = time.time() - t0
            safe_print(
                f"  进度 {imported:,}/{total_records:,} "
                f"({100 * imported / max(total_records, 1):.1f}%) "
                f"file={file_idx + 1}/{len(ris_files)} {fn} elapsed={elapsed / 60:.1f}min"
            )

        if record_idx >= len(records):
            file_idx += 1
            record_idx = 0
            state["file_idx"] = file_idx
            state["record_idx"] = 0
            _save_progress(state)

        db = next(client.get_db())
        try:
            db.execute(
                text(
                    "UPDATE literature_import_job SET imported_count=:n, retstart=:rs WHERE id=:id"
                ),
                {"n": imported, "rs": file_idx, "id": job_id},
            )
            db.commit()
        finally:
            db.close()

    db = next(client.get_db())
    try:
        db.execute(
            text(
                "UPDATE literature_import_job SET status='done', imported_count=:n, "
                "retstart=:rs, finished_at=NOW() WHERE id=:id"
            ),
            {"n": imported, "rs": file_idx, "id": job_id},
        )
        db.commit()
        unique = db.execute(text("SELECT COUNT(*) FROM literature_wos")).scalar()
    finally:
        db.close()

    state["status"] = "done"
    state["unique_in_db"] = int(unique or 0)
    state["finished_at"] = datetime.now().isoformat(timespec="seconds")
    _save_progress(state)

    return {
        "files": len(ris_files),
        "total_records": total_records,
        "imported": imported,
        "unique_in_db": int(unique or 0),
        "job_id": job_id,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="WoS RIS 原始记录全量入库")
    p.add_argument(
        "--dir",
        action="append",
        dest="dirs",
        help="RIS 目录（可多次指定；默认 wos数据包/Downloads + data/wos_import）",
    )
    p.add_argument("--no-resume", action="store_true", help="忽略进度从头开始")
    args = p.parse_args()

    dirs = args.dirs or DEFAULT_DIRS
    stats = import_all(dirs=dirs, resume=not args.no_resume)
    safe_print(
        f"\n[完成] 处理 {stats['imported']:,} 条 RIS 记录，"
        f"库内唯一 {stats['unique_in_db']:,} 篇（{stats['files']} 个文件）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
