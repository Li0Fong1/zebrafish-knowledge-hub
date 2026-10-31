# -*- coding: utf-8 -*-
"""
合并 PubMed + WoS 斑马鱼判别结果，去重后导出上交包。

输入：
  - 项目阶段/05_数据与库/归档/data_export/pubmed_judgement_full/pubmed_judgement_full.csv
  - 项目阶段/05_数据与库/归档/result_wosc.rar（或已解压路径）

输出：项目阶段/05_数据与库/归档/data_export/literature_judgement_merged/
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.import_wos_raw_ris import _record_key, _split_ris_records
from rag.wos_ris_paths import discover_wos_ris_dirs, list_wos_ris_files

PUBMED_CSV = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "pubmed_judgement_full" / "pubmed_judgement_full.csv"
WOS_RAR = ROOT / "项目阶段" / "05_数据与库" / "归档" / "result_wosc.rar"
WOS_MD_EXTRACTED = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "merged_judgement_full" / "_src" / "wos" / "result_95000.md"
OUT_DIR = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "literature_judgement_merged"

COLS = (
    "数据源",
    "文献ID",
    "PMID",
    "WOS_UT",
    "DOI",
    "英文标题",
    "中文标题",
    "英文摘要",
    "中文摘要",
    "判断结果",
    "理由",
    "处理模式",
    "判别时间",
)

WOS_ENTRY_RE = re.compile(
    r"^###\s+(\d+)\s*\n\n"
    r"(?:- \*\*中文标题\*\*：(?P<title_zh>.*?)\n"
    r"- \*\*英文标题\*\*：(?P<title_en>.*?)\n"
    r"- \*\*中文摘要\*\*：(?P<abstract_zh>.*?)\n"
    r"- \*\*英文摘要\*\*：(?P<abstract_en>.*?)\n"
    r"- \*\*判断结果\*\*：(?P<judgement>.*?)\n"
    r"- \*\*理由\*\*：(?P<reason>.*?)\n"
    r"- \*\*mode\*\*：(?P<mode>.*?)\n)",
    re.MULTILINE,
)


def _norm_doi(doi: str) -> str:
    d = (doi or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d.strip()


def _norm_title(title: str) -> str:
    t = re.sub(r"<[^>]+>", " ", title or "")
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _dedup_key(row: dict) -> tuple[str, str]:
    pmid = (row.get("PMID") or "").strip()
    if pmid:
        return ("pmid", pmid)
    doi = _norm_doi(row.get("DOI") or "")
    if doi:
        return ("doi", doi)
    wos = (row.get("WOS_UT") or "").strip()
    if wos:
        return ("wos", wos)
    title = _norm_title(row.get("英文标题") or row.get("中文标题") or "")
    if title:
        return ("title", title[:200])
    rid = (row.get("文献ID") or "").strip()
    return ("id", rid or hashlib.md5(str(row).encode()).hexdigest())


def _resolve_wos_md() -> Path:
    if WOS_MD_EXTRACTED.is_file():
        return WOS_MD_EXTRACTED
    extract_dir = WOS_MD_EXTRACTED.parent
    extract_dir.mkdir(parents=True, exist_ok=True)
    if WOS_RAR.is_file():
        import subprocess

        subprocess.run(
            ["tar", "-xf", str(WOS_RAR), "-C", str(extract_dir)],
            check=True,
            capture_output=True,
        )
    if WOS_MD_EXTRACTED.is_file():
        return WOS_MD_EXTRACTED
    raise FileNotFoundError(f"未找到 WoS 判别 MD：{WOS_MD_EXTRACTED}")


def _build_wos_index_map() -> dict[int, dict[str, str]]:
    """按 RIS 导入顺序建立序号 → wos_ut/pmid/doi。"""
    mapping: dict[int, dict[str, str]] = {}
    idx = 0
    for path in list_wos_ris_files(discover_wos_ris_dirs()):
        with open(path, encoding="utf-8", errors="replace") as f:
            for block in _split_ris_records(f.read()):
                idx += 1
                wos_ut, pmid, doi = _record_key(block)
                mapping[idx] = {
                    "WOS_UT": wos_ut,
                    "PMID": (pmid or "").strip(),
                    "DOI": (doi or "").strip(),
                }
    return mapping


def _parse_wos_md(path: Path, index_map: dict[int, dict[str, str]]) -> Iterator[dict]:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"## 详细结果\s*\n", text)
    body = text[m.end():] if m else text
    judged_at = "2026-07-08 12:14:06"
    for match in WOS_ENTRY_RE.finditer(body):
        idx = int(match.group(1))
        meta = index_map.get(idx, {})
        wos_ut = meta.get("WOS_UT", "")
        pmid = meta.get("PMID", "")
        doi = meta.get("DOI", "")
        lit_id = f"PMID:{pmid}" if pmid else (f"WOS:{wos_ut}" if wos_ut else str(idx))
        yield {
            "数据源": "WoS",
            "文献ID": lit_id,
            "PMID": pmid,
            "WOS_UT": wos_ut,
            "DOI": doi,
            "英文标题": match.group("title_en").strip(),
            "中文标题": match.group("title_zh").strip(),
            "英文摘要": match.group("abstract_en").strip(),
            "中文摘要": match.group("abstract_zh").strip(),
            "判断结果": match.group("judgement").strip(),
            "理由": match.group("reason").strip(),
            "处理模式": match.group("mode").strip(),
            "判别时间": judged_at,
        }


def _load_pubmed_rows(*, wos_only: bool = False) -> list[dict]:
    if not PUBMED_CSV.is_file():
        if wos_only:
            return []
        raise FileNotFoundError(PUBMED_CSV)
    rows: list[dict] = []
    with open(PUBMED_CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            pmid = (r.get("PMID") or "").strip()
            rows.append({
                "数据源": "PubMed",
                "文献ID": f"PMID:{pmid}" if pmid else "",
                "PMID": pmid,
                "WOS_UT": "",
                "DOI": "",
                "英文标题": (r.get("英文标题") or "").strip(),
                "中文标题": (r.get("中文标题") or "").strip(),
                "英文摘要": (r.get("英文摘要") or "").strip(),
                "中文摘要": (r.get("中文摘要") or "").strip(),
                "判断结果": (r.get("判断结果") or "").strip(),
                "理由": (r.get("理由") or "").strip(),
                "处理模式": (r.get("处理模式") or "").strip(),
                "判别时间": (r.get("判别时间") or "").strip(),
            })
    return rows


def _merge_rows(pubmed_rows: list[dict], wos_rows: list[dict]) -> tuple[list[dict], dict]:
    merged: dict[tuple[str, str], dict] = {}
    title_index: dict[str, tuple[str, str]] = {}
    stats = {
        "pubmed_in": len(pubmed_rows),
        "wos_in": len(wos_rows),
        "dup_pmid": 0,
        "dup_doi": 0,
        "dup_title": 0,
        "dup_wos": 0,
        "cross_title": 0,
    }

    def _register_title(row: dict, key: tuple[str, str]) -> None:
        for field in ("英文标题", "中文标题"):
            t = _norm_title(row.get(field) or "")
            if len(t) >= 20:
                title_index.setdefault(t, key)

    def _find_title_key(row: dict) -> tuple[str, str] | None:
        for field in ("英文标题", "中文标题"):
            t = _norm_title(row.get(field) or "")
            if len(t) >= 20 and t in title_index:
                return title_index[t]
        return None

    def _merge_into(old: dict, row: dict, *, cross: str) -> None:
        if cross == "pmid":
            stats["dup_pmid"] += 1
        elif cross == "doi":
            stats["dup_doi"] += 1
        elif cross == "title":
            stats["dup_title"] += 1
        elif cross == "cross_title":
            stats["cross_title"] += 1
        else:
            stats["dup_wos"] += 1
        if row["数据源"] not in old["数据源"]:
            old["数据源"] = f"{old['数据源']};{row['数据源']}"
        if not old.get("WOS_UT") and row.get("WOS_UT"):
            old["WOS_UT"] = row["WOS_UT"]
        if not old.get("DOI") and row.get("DOI"):
            old["DOI"] = row["DOI"]
        if not old.get("PMID") and row.get("PMID"):
            old["PMID"] = row["PMID"]
            old["文献ID"] = f"PMID:{row['PMID']}"

    for r in pubmed_rows:
        key = _dedup_key(r)
        merged[key] = dict(r)
        _register_title(r, key)

    for r in wos_rows:
        key = _dedup_key(r)
        if key in merged:
            _merge_into(merged[key], r, cross=key[0])
            _register_title(merged[key], key)
            continue
        tkey = _find_title_key(r)
        if tkey and tkey in merged:
            _merge_into(merged[tkey], r, cross="cross_title")
            _register_title(merged[tkey], tkey)
            continue
        merged[key] = dict(r)
        _register_title(r, key)

    out = list(merged.values())
    out.sort(key=lambda x: (x.get("PMID") or "z", x.get("WOS_UT") or x.get("文献ID") or ""))
    stats["merged_total"] = len(out)
    return out, stats


def _write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)


def _write_readme(path: Path, stats: dict, jstats: dict) -> None:
    path.write_text(
        "\n".join([
            "# PubMed + WoS 斑马鱼判别 — 合并去重完整结果",
            "",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 数据来源",
            "",
            f"- PubMed 判别：`pubmed_judgement_full.csv`（{stats['pubmed_in']:,} 篇）",
            f"- WoS 判别：`项目阶段/05_数据与库/归档/result_wosc.rar` → `result_95000.md`（{stats['wos_in']:,} 篇）",
            "",
            "## 去重说明",
            "",
            "按 **PMID → DOI → WOS_UT → 标题** 优先级去重；",
            "PubMed 与 WoS 重复时保留 PubMed 字段，并合并数据源标记。",
            "",
            "## 去重统计",
            "",
            f"- PMID 重复：{stats['dup_pmid']:,}",
            f"- DOI 重复：{stats['dup_doi']:,}",
            f"- 标题重复：{stats['dup_title']:,}",
            f"- **跨库标题匹配**：{stats['cross_title']:,}",
            f"- 其他重复：{stats['dup_wos']:,}",
            f"- **合并后合计**：{stats['merged_total']:,} 篇",
            "",
            "## 判别统计",
            "",
            f"| 判断结果 | 数量 |",
            f"|----------|------|",
            f"| 是 | {jstats['是']:,} |",
            f"| 否 | {jstats['否']:,} |",
            f"| 无法判别 | {jstats['无法判别']:,} |",
            "",
            "## 文件",
            "",
            "- `literature_judgement_merged.csv` — 全量（Excel 推荐）",
            "- `literature_judgement_是.csv` / `否.csv` / `无法判别.csv`",
            "- `literature_judgement_merged.zip` — 打包上交",
            "",
        ]),
        encoding="utf-8",
    )


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="合并 PubMed + WoS 判别结果导出")
    p.add_argument(
        "--wos-only",
        action="store_true",
        help="仅导出 WoS 判别（无 PubMed CSV 时使用）",
    )
    args = p.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("1/5 读取 PubMed CSV…")
    pubmed_rows = _load_pubmed_rows(wos_only=args.wos_only)
    print(f"  PubMed: {len(pubmed_rows):,}" + ("（--wos-only 跳过）" if args.wos_only else ""))

    print("2/5 建立 WoS 序号映射（RIS 导入顺序）…")
    index_map = _build_wos_index_map()
    print(f"  RIS 记录: {len(index_map):,}")

    print("3/5 解析 WoS 判别 MD…")
    wos_md = _resolve_wos_md()
    wos_rows = list(_parse_wos_md(wos_md, index_map))
    print(f"  WoS 判别: {len(wos_rows):,}")

    print("4/5 合并去重…")
    merged, stats = _merge_rows(pubmed_rows, wos_rows)
    jstats = {"是": 0, "否": 0, "无法判别": 0}
    for r in merged:
        j = r.get("判断结果") or ""
        if j in jstats:
            jstats[j] += 1
    print(f"  合并后: {len(merged):,}（去重 PMID {stats['dup_pmid']:,}）")

    print("5/5 写入文件…")
    csv_all = OUT_DIR / "literature_judgement_merged.csv"
    _write_csv(csv_all, merged)
    for j in ("是", "否", "无法判别"):
        part = [r for r in merged if r.get("判断结果") == j]
        _write_csv(OUT_DIR / f"literature_judgement_{j}.csv", part)
        print(f"  {j}: {len(part):,}")

    readme = OUT_DIR / "README.md"
    _write_readme(readme, stats, jstats)

    zip_path = OUT_DIR / "literature_judgement_merged.zip"
    names = [
        "README.md",
        "literature_judgement_merged.csv",
        "literature_judgement_是.csv",
        "literature_judgement_否.csv",
        "literature_judgement_无法判别.csv",
    ]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            p = OUT_DIR / name
            if p.is_file():
                zf.write(p, arcname=name)

    print(f"\n完成 → {OUT_DIR}")
    print(f"  CSV: {csv_all.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"  ZIP: {zip_path.stat().st_size / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
