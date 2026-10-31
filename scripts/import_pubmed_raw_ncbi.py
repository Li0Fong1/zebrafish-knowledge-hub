# -*- coding: utf-8 -*-
"""
从 NCBI PubMed 全量采集斑马鱼文献，原样存入 literature_pubmed（raw XML，不做任何加工）。

用法：
  python -m scripts.import_pubmed_raw_ncbi
  python -m scripts.import_pubmed_raw_ncbi --resume
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import xml.etree.ElementTree as ET
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
from rag.literature_collector import (
    PUBMED_ZEBRAFISH_BROAD,
    _MAX_RETRIES,
    _RETRY_BACKOFF,
    _api_get,
    _loads_ncbi_json,
    _sleep,
    pubmed_total_count,
)

PROGRESS_FILE = os.path.join(_BASE_DIR, "data", "literature", "pubmed_raw_import.json")
EFETCH_BATCH = int(os.getenv("PUBMED_EFETCH_BATCH", "100"))
YEAR_START = int(os.getenv("PUBMED_YEAR_START", "1951"))
YEAR_END = int(os.getenv("PUBMED_YEAR_END", str(datetime.now().year + 1)))


def _ensure_schema(client: MySQLClient) -> None:
    sql_path = ROOT / "sql" / "literature_pubmed_migration.sql"
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


def _open_history(query: str) -> Tuple[str, str, int]:
    """esearch + usehistory=y，突破 esearch 单页 9999 条限制。"""
    last_err: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            data = _api_get("/esearch.fcgi", {
                "db": "pubmed",
                "term": query,
                "usehistory": "y",
                "retmax": 0,
                "retmode": "json",
            })
            _sleep()
            parsed = _loads_ncbi_json(data)
            res = parsed.get("esearchresult", {})
            webenv = str(res.get("webenv") or "").strip()
            query_key = str(res.get("querykey") or "").strip()
            count = int(res.get("count") or 0)
            if not webenv or not query_key:
                raise RuntimeError(f"NCBI history 无效: webenv={webenv!r} query_key={query_key!r}")
            return webenv, query_key, count
        except (json.JSONDecodeError, urllib.error.URLError, ConnectionResetError, OSError, TimeoutError, RuntimeError) as e:
            last_err = e
            wait = _RETRY_BACKOFF * (2 ** attempt)
            safe_print(
                f"  [重试] esearch history ({type(e).__name__})，"
                f"{wait:.0f}s 后重试 ({attempt + 1}/{_MAX_RETRIES})…"
            )
            time.sleep(wait)
    raise last_err if last_err else RuntimeError("esearch history 失败")


def _efetch_history(webenv: str, query_key: str, retstart: int, retmax: int) -> str:
    """通过 WebEnv 分页 efetch，可超过 1 万条。"""
    last_err: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            data = _api_get("/efetch.fcgi", {
                "db": "pubmed",
                "query_key": query_key,
                "WebEnv": webenv,
                "retstart": retstart,
                "retmax": retmax,
                "retmode": "xml",
            })
            _sleep()
            if not data.strip():
                raise RuntimeError(f"efetch 空响应 retstart={retstart}")
            return data
        except (urllib.error.URLError, ConnectionResetError, OSError, TimeoutError, RuntimeError) as e:
            last_err = e
            wait = _RETRY_BACKOFF * (2 ** attempt)
            safe_print(
                f"  [重试] efetch history retstart={retstart} ({type(e).__name__})，"
                f"{wait:.0f}s 后重试 ({attempt + 1}/{_MAX_RETRIES})…"
            )
            time.sleep(wait)
    raise last_err if last_err else RuntimeError("efetch history 失败")


def _split_articles(xml_text: str) -> List[Tuple[str, str]]:
    """返回 [(pmid, raw_pubmed_article_xml), ...]"""
    root = ET.fromstring(xml_text)
    out: List[Tuple[str, str]] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = (article.findtext(".//PMID") or "").strip()
        if not pmid:
            continue
        raw = ET.tostring(article, encoding="unicode")
        out.append((pmid, raw))
    return out


def _insert_batch(client: MySQLClient, rows: List[Tuple[str, str]], query: str) -> int:
    if not rows:
        return 0
    db = next(client.get_db())
    n = 0
    try:
        for pmid, raw in rows:
            db.execute(
                text(
                    "INSERT INTO literature_pubmed (pmid, raw_xml, search_query, fetched_at) "
                    "VALUES (:pmid, :raw_xml, :q, NOW()) "
                    "ON DUPLICATE KEY UPDATE raw_xml=VALUES(raw_xml), fetched_at=NOW()"
                ),
                {"pmid": pmid, "raw_xml": raw, "q": query[:512]},
            )
            n += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return n


def _year_queries(base_query: str) -> List[Tuple[int, str]]:
    return [(y, f"({base_query}) AND {y}[PDAT]") for y in range(YEAR_START, YEAR_END + 1)]


def _import_one_year(
    client: MySQLClient,
    *,
    year: int,
    year_query: str,
    base_query: str,
    job_id: int,
    state: dict,
    imported: int,
    t0: float,
    total: int,
) -> int:
    ytotal = pubmed_total_count(year_query)
    if ytotal <= 0:
        safe_print(f"  [{year}] 0 篇，跳过")
        return imported

    safe_print(f"  [{year}] 预计 {ytotal:,} 篇…")
    webenv, query_key, _ = _open_history(year_query)
    retstart = 0
    year_n = 0

    while retstart < ytotal:
        try:
            xml_text = _efetch_history(webenv, query_key, retstart, EFETCH_BATCH)
        except Exception as e:
            safe_print(f"  [{year}] history 重建: {e}")
            webenv, query_key, _ = _open_history(year_query)
            xml_text = _efetch_history(webenv, query_key, retstart, EFETCH_BATCH)

        rows = _split_articles(xml_text)
        if not rows:
            break

        n = _insert_batch(client, rows, base_query)
        year_n += n
        imported += n
        retstart += len(rows)
        state["imported"] = imported
        state["current_year"] = year
        state["year_retstart"] = retstart
        _save_progress(state)

        if year_n % 500 < EFETCH_BATCH:
            elapsed = time.time() - t0
            safe_print(
                f"  [{year}] +{year_n:,} | 总进度 {imported:,}/{total:,} "
                f"({100 * imported / max(total, 1):.1f}%) elapsed={elapsed / 60:.1f}min"
            )

    db = next(client.get_db())
    try:
        db.execute(
            text("UPDATE literature_import_job SET imported_count=:n WHERE id=:id"),
            {"n": imported, "id": job_id},
        )
        db.commit()
    finally:
        db.close()

    done = state.setdefault("years_done", [])
    if year not in done:
        done.append(year)
    state.pop("year_retstart", None)
    _save_progress(state)
    safe_print(f"  [{year}] 完成 +{year_n:,} 篇")
    return imported


def import_all(*, resume: bool = True) -> dict:
    client = MySQLClient()
    _ensure_schema(client)

    query = PUBMED_ZEBRAFISH_BROAD
    total = pubmed_total_count(query)
    safe_print(f"PubMed 检索式：{query}")
    safe_print(f"预计命中：{total:,} 篇（按年份分批，规避 NCBI 1 万条分页上限）")

    state = _load_progress() if resume else {}
    retstart = int(state.get("retstart") or 0)
    imported = int(state.get("imported") or 0)
    job_id = state.get("job_id")
    if state.get("status") == "done" and imported < total:
        safe_print(f"  [修正] 上次误标 done（{imported:,}/{total:,}），继续导入")
        state.pop("status", None)
        state.pop("finished_at", None)

    db = next(client.get_db())
    try:
        if not job_id:
            r = db.execute(
                text(
                    "INSERT INTO literature_import_job "
                    "(job_name, search_query, total_expected, imported_count, retstart, status) "
                    "VALUES ('pubmed_raw_ncbi', :q, :total, :imp, :rs, 'running')"
                ),
                {"q": query[:512], "total": total, "imp": imported, "rs": retstart},
            )
            db.commit()
            job_id = r.lastrowid
        else:
            db.execute(
                text(
                    "UPDATE literature_import_job SET status='running', total_expected=:total "
                    "WHERE id=:id"
                ),
                {"total": total, "id": job_id},
            )
            db.commit()
    finally:
        db.close()

    state.update({
        "job_id": job_id,
        "query": query,
        "total": total,
        "imported": imported,
        "mode": "year_split",
    })
    state.pop("status", None)
    state.pop("finished_at", None)
    _save_progress(state)

    years = _year_queries(query)
    years_done = set(state.get("years_done") or [])
    if resume and state.get("current_year") and state.get("year_retstart"):
        # 续传当前年份：从 years_done 中移除以便重跑该年
        years_done.discard(int(state["current_year"]))

    t0 = time.time()
    for year, year_query in years:
        if year in years_done:
            continue
        imported = _import_one_year(
            client,
            year=year,
            year_query=year_query,
            base_query=query,
            job_id=job_id,
            state=state,
            imported=imported,
            t0=t0,
            total=total,
        )

    db = next(client.get_db())
    try:
        db_count = db.execute(text("SELECT COUNT(*) FROM literature_pubmed")).scalar() or 0
    finally:
        db.close()

    if db_count < total * 0.95:
        raise RuntimeError(f"导入可能不完整：DB={db_count:,} 预期≈{total:,}")

    db = next(client.get_db())
    try:
        db.execute(
            text(
                "UPDATE literature_import_job SET status='done', imported_count=:n, "
                "finished_at=NOW() WHERE id=:id"
            ),
            {"n": int(db_count), "id": job_id},
        )
        db.commit()
    finally:
        db.close()

    state["status"] = "done"
    state["imported"] = int(db_count)
    state["finished_at"] = datetime.now().isoformat(timespec="seconds")
    _save_progress(state)

    return {"total": total, "imported": int(db_count), "job_id": job_id}


def main() -> int:
    p = argparse.ArgumentParser(description="NCBI PubMed 原始 XML 全量入库")
    p.add_argument("--no-resume", action="store_true", help="忽略进度从头开始")
    args = p.parse_args()
    stats = import_all(resume=not args.no_resume)
    safe_print(f"\n[完成] 入库 {stats['imported']:,} / {stats['total']:,} 篇")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
