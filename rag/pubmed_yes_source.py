# -*- coding: utf-8 -*-
"""PubMed 判别「是」文献清单（fetch / 向量化统一数据源，不含 WoS 合并结果）。"""
from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBMED_JUDGEMENT_CSV = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "pubmed_judgement_full" / "pubmed_judgement_full.csv"
PUBMED_YES_CSV = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "pubmed_judgement_full" / "pubmed_judgement_是.csv"
# 金标战役产出的「已存在」清单（字段为小写 pmid / en_title…）；作判别 CSV 缺失时的一期兜底源
EXISTING_YES_CSV = ROOT / "解析文件" / "已存在表.csv"

NO_ABS = {"", "无摘要", "No abstract", "no abstract"}


def _clean(s: str) -> str:
    return (s or "").strip()


def _csv_candidates() -> list[Path]:
    out: list[Path] = []
    for p in (PUBMED_YES_CSV, PUBMED_JUDGEMENT_CSV, EXISTING_YES_CSV):
        if p.is_file():
            out.append(p)
    return out


def _row_to_record(row: dict) -> dict | None:
    """判别 CSV 行 → 统一记录；已存在表行（无「判断结果」列）有 PMID 即视为「是」。"""
    pmid = _clean(row.get("PMID") or row.get("pmid"))
    if not pmid:
        return None
    # 已存在表没有判断列；有列时必须为「是」
    if "判断结果" in row and _clean(row.get("判断结果")) not in ("是",):
        return None
    abstract_en = _clean(row.get("英文摘要") or row.get("abstract_en"))
    abstract_zh = _clean(row.get("中文摘要") or row.get("abstract_zh"))
    if abstract_en in NO_ABS:
        abstract_en = ""
    if abstract_zh in NO_ABS:
        abstract_zh = ""
    title_en = (
        _clean(row.get("英文标题"))
        or _clean(row.get("en_title"))
        or _clean(row.get("中文标题"))
        or _clean(row.get("zh_title"))
    )
    title_zh = (
        _clean(row.get("中文标题"))
        or _clean(row.get("zh_title"))
        or _clean(row.get("英文标题"))
        or _clean(row.get("en_title"))
    )
    return {
        "pmid": pmid,
        "title_en": title_en,
        "title_zh": title_zh,
        "abstract_en": abstract_en or abstract_zh,
        "abstract_zh": abstract_zh or abstract_en,
        "doi": _clean(row.get("DOI") or row.get("doi")),
        "judgement": "是",
        "reason": _clean(row.get("理由")),
        "mode": _clean(row.get("处理模式") or row.get("source_step")),
    }


def _load_from_csv() -> tuple[dict[str, dict], list[str]]:
    by_pmid: dict[str, dict] = {}
    for path in _csv_candidates():
        with open(path, encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                rec = _row_to_record(row)
                if rec:
                    by_pmid[rec["pmid"]] = rec
        if by_pmid:
            break
    order = sorted(by_pmid.keys(), key=lambda x: int(x) if x.isdigit() else x)
    return by_pmid, order


def _load_from_mysql() -> tuple[dict[str, dict], list[str]]:
    try:
        from core.entry import bootstrap_app

        bootstrap_app()
        from mysql_client import MySQLClient
        from sqlalchemy import text

        client = MySQLClient()
        db = next(client.get_db())
        try:
            rows = db.execute(
                text(
                    """
                    SELECT pmid, title_en, title_zh, abstract_en, abstract_zh,
                           judgement, reason, mode
                    FROM literature_pubmed_judgement
                    WHERE judgement = '是'
                    ORDER BY pmid+0 ASC
                    """
                )
            ).fetchall()
        finally:
            db.close()
    except Exception:
        return {}, []

    by_pmid: dict[str, dict] = {}
    for pmid, te, tz, ae, az, j, reason, mode in rows:
        rec = _row_to_record({
            "PMID": str(pmid),
            "英文标题": te or "",
            "中文标题": tz or "",
            "英文摘要": ae or "",
            "中文摘要": az or "",
            "判断结果": j or "",
            "理由": reason or "",
            "处理模式": mode or "",
        })
        if rec:
            by_pmid[rec["pmid"]] = rec
    order = sorted(by_pmid.keys(), key=lambda x: int(x) if x.isdigit() else x)
    return by_pmid, order


@lru_cache(maxsize=1)
def load_pubmed_yes_records() -> tuple[dict[str, dict], list[str]]:
    """
    返回 (pmid -> 记录, 按 PMID 数值排序的 pmid 列表)。
    数据源：PubMed 全库判别结果（pubmed_judgement_full），不含 WoS 合并。
    """
    by_pmid, order = _load_from_csv()
    if by_pmid:
        return by_pmid, order
    return _load_from_mysql()


def count_pubmed_yes_pmids() -> int:
    _, order = load_pubmed_yes_records()
    return len(order)


def pubmed_yes_pmid_set() -> set[str]:
    by_pmid, _ = load_pubmed_yes_records()
    return set(by_pmid.keys())


# 兼容旧名（曾用整合 merged 清单）
load_merged_yes_records = load_pubmed_yes_records
count_merged_yes_pmids = count_pubmed_yes_pmids
merged_yes_pmid_set = pubmed_yes_pmid_set
MERGED_CSV = PUBMED_YES_CSV
