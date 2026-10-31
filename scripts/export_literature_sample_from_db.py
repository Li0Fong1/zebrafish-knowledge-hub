# -*- coding: utf-8 -*-
"""从 MySQL（literature_pubmed + literature_wos）抽取 N 篇文献为 txt 样本。"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from rag.literature_models import format_article
from scripts.literature_txt_names import build_literature_filename_from_dict, index_prefix
from sqlalchemy import text

from mysql_client import MySQLClient


def _parse_pubmed_xml(raw: str) -> dict:
    root = ET.fromstring(raw)
    title = (root.findtext(".//ArticleTitle") or "").strip()
    abstract_parts = []
    for ab in root.findall(".//AbstractText"):
        label = ab.get("Label") or ""
        txt = (ab.text or "").strip()
        if label:
            abstract_parts.append(f"{label}: {txt}")
        elif txt:
            abstract_parts.append(txt)
    abstract = " ".join(abstract_parts).strip()
    journal = (root.findtext(".//Journal/Title") or root.findtext(".//ISOAbbreviation") or "").strip()
    year = (root.findtext(".//PubDate/Year") or "")[:4]
    pmid = (root.findtext(".//PMID") or "").strip()
    doi = ""
    for eid in root.findall(".//ArticleId"):
        if eid.get("IdType") == "doi":
            doi = (eid.text or "").strip()
            break
    return {
        "source": "pubmed",
        "id_label": f"PMID:{pmid}",
        "pmid": pmid,
        "title": title,
        "abstract": abstract,
        "journal": journal,
        "year": year,
        "doi": doi,
    }


def _parse_wos_ris(raw: str) -> dict:
    fields: dict = {}
    key = None
    for line in raw.splitlines():
        if line.startswith("ER  -"):
            break
        m = re.match(r"^([A-Z0-9]{2})  - (.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            fields.setdefault(key, []).append(val)
        elif key and line.startswith("   "):
            fields[key][-1] += " " + line.strip()
    title = " ".join(fields.get("TI", []) or fields.get("T1", []))
    abstract = " ".join(fields.get("AB", []))
    journal = " ".join(fields.get("JO", []) or fields.get("JF", []) or fields.get("T2", []))
    year = (fields.get("PY") or fields.get("Y1") or [""])[0][:4]
    doi = (fields.get("DO", [""])[0]).strip()
    pmid = (fields.get("PM", [""])[0]).strip()
    ut_raw = (fields.get("UT", [""])[0] or fields.get("AN", [""])[0]).strip()
    wos_ut = ut_raw.replace("WOS:", "").strip()
    authors = "; ".join(fields.get("AU", [])[:8])
    return {
        "source": "wos",
        "id_label": f"WOS:{wos_ut}" if wos_ut else (f"DOI:{doi}" if doi else title[:40]),
        "title": title,
        "abstract": abstract,
        "journal": journal,
        "year": year,
        "doi": doi,
        "pmid": pmid,
        "wos_ut": wos_ut,
        "authors": authors,
    }


def _fetch_pubmed(client: MySQLClient, limit: int) -> list:
    db = next(client.get_db())
    try:
        rows = db.execute(
            text(
                "SELECT pmid, raw_xml FROM literature_pubmed "
                "ORDER BY pmid+0 LIMIT :lim"
            ),
            {"lim": limit * 3},
        ).fetchall()
    finally:
        db.close()
    out = []
    step = max(1, len(rows) // max(limit, 1))
    for i in range(0, len(rows), step):
        if len(out) >= limit:
            break
        pmid, raw = rows[i]
        try:
            art = _parse_pubmed_xml(raw)
            if art.get("title"):
                out.append(art)
        except ET.ParseError:
            continue
    return out[:limit]


def _fetch_wos(client: MySQLClient, limit: int) -> list:
    db = next(client.get_db())
    try:
        rows = db.execute(
            text("SELECT wos_ut, raw_ris FROM literature_wos ORDER BY wos_ut LIMIT :lim"),
            {"lim": limit * 3},
        ).fetchall()
    finally:
        db.close()
    out = []
    step = max(1, len(rows) // max(limit, 1))
    for i in range(0, len(rows), step):
        if len(out) >= limit:
            break
        _, raw = rows[i]
        art = _parse_wos_ris(raw)
        if art.get("title"):
            out.append(art)
    return out[:limit]


def export_sample(out_dir: Path, n: int = 1000) -> int:
    client = MySQLClient()
    pub_q = min(700, n)
    wos_q = min(250, max(0, n - pub_q))
    remain = n - pub_q - wos_q

    pub_arts = _fetch_pubmed(client, pub_q + remain)
    wos_arts = _fetch_wos(client, wos_q)

    sample = pub_arts[: pub_q + remain] + wos_arts[:wos_q]
    sample = sample[:n]

    if out_dir.exists():
        for p in out_dir.glob("*.txt"):
            if p.name != "00_索引.txt":
                p.unlink()
    else:
        out_dir.mkdir(parents=True, exist_ok=True)

    fmt_src = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "test" / "literature_test_100" / "zebrafish_judgement_1000(format).md"
    if fmt_src.is_file():
        (out_dir / fmt_src.name).write_text(fmt_src.read_text(encoding="utf-8"), encoding="utf-8")

    total = len(sample)
    index_lines = [
        f"# 文献测试样本 {total} 篇（来自 MySQL）",
        "",
        "序号\t文件名\t来源\t标题",
    ]
    for i, art in enumerate(sample, 1):
        fname = build_literature_filename_from_dict(art, i, total=total)
        (out_dir / fname).write_text(format_article(art), encoding="utf-8")
        title = (art.get("title") or "")[:80]
        src = (art.get("source") or "unknown").upper()
        index_lines.append(f"{index_prefix(i, total=total)}\t{fname}\t{src}\t{title}")

    (out_dir / "00_索引.txt").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    return total


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("-n", "--count", type=int, default=1000)
    p.add_argument("--out", default=str(ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "test" / "literature_test_1000"))
    args = p.parse_args()
    n = export_sample(Path(args.out), args.count)
    print(f"已从 MySQL 导出 {n} 篇 → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
