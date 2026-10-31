# -*- coding: utf-8 -*-
"""
Web of Science 文献采集。

支持两种方式：
1. Clarivate WoS Starter API（需 .env 中 WOS_API_KEY，机构订阅）
2. 从 WoS 网站导出 RIS 文件，放入 data/wos_import/ 后批量导入

WoS 网站检索示例：TS=zebrafish → 导出 → RIS
"""
import bootstrap  # noqa: F401

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Iterator, List, Optional

from common_utils import logger, safe_print
from config import _BASE_DIR
from rag.wos_ris_paths import discover_wos_ris_dirs

WOS_API_BASE = os.getenv("WOS_API_BASE", "https://api.clarivate.com/apis/wos-starter/v1")
WOS_IMPORT_DIR = os.path.join(_BASE_DIR, "data", "wos_import")
WOS_EXTRA_DIRS = [d for d in discover_wos_ris_dirs() if d != WOS_IMPORT_DIR]
REQUEST_INTERVAL = float(os.getenv("WOS_REQUEST_INTERVAL", "1.0"))

# WoS 广搜：凡标题/摘要/关键词出现 zebrafish 或 Danio rerio 均采集
WOS_BROAD_QUERIES = [
    ("wos_zebrafish", "TS=zebrafish"),
    ("wos_danio", "TS=(Danio rerio)"),
]

# 细分主题（仅 --with-themes 时启用）
WOS_THEME_QUERIES = [
    ("wos_zebrafish_genetics", "TS=zebrafish AND TS=(gene OR CRISPR OR genome)"),
    ("wos_zebrafish_dev", "TS=zebrafish AND TS=(development OR embryogenesis)"),
    ("wos_zebrafish_disease", "TS=zebrafish AND TS=(disease OR cancer OR drug)"),
    ("wos_zebrafish_neuro", "TS=zebrafish AND TS=(brain OR neuron OR behavior)"),
]


def _api_key() -> Optional[str]:
    return os.getenv("WOS_API_KEY", "").strip() or None


def _parse_ris_records(text: str) -> List[dict]:
    records = []
    blocks = re.split(r"\n(?=TY  - )", text)
    for block in blocks:
        block = block.strip()
        if not block.startswith("TY"):
            continue
        fields: dict = {}
        key = None
        for line in block.splitlines():
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
        if not title:
            continue
        records.append({
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
        })
    return records


def import_ris_directory(directory: str = WOS_IMPORT_DIR) -> List[dict]:
    if not os.path.isdir(directory):
        return []
    articles = []
    for fn in sorted(os.listdir(directory)):
        if not fn.lower().endswith((".ris", ".ciw")):
            continue
        path = os.path.join(directory, fn)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            recs = _parse_ris_records(text)
            safe_print(f"  RIS {fn}: {len(recs)} 篇")
            articles.extend(recs)
        except OSError as e:
            logger.warning(f"[WoS] 读取 {fn} 失败: {e}")
    return articles


def import_all_ris_sources() -> List[dict]:
    """从 data/wos_import 及 wos数据包/Downloads 等目录导入 RIS。"""
    from literature_tiers import tag_articles

    seen: set = set()
    all_arts: List[dict] = []
    dirs = [WOS_IMPORT_DIR] + [d for d in WOS_EXTRA_DIRS if d != WOS_IMPORT_DIR]
    for directory in dirs:
        if not os.path.isdir(directory):
            continue
        batch = import_ris_directory(directory)
        for art in batch:
            key = (
                (art.get("wos_ut") or "").strip()
                or (art.get("doi") or "").strip().lower()
                or (art.get("pmid") or "").strip()
                or (art.get("title") or "")[:80]
            )
            if key in seen:
                continue
            seen.add(key)
            all_arts.append(art)
    return tag_articles(all_arts)


def search_wos_api(query: str, limit: int = 100) -> List[dict]:
    key = _api_key()
    if not key:
        safe_print("  [WoS] 未配置 WOS_API_KEY，跳过 API 检索（可用 RIS 导入）")
        return []

    articles = []
    page = 1
    page_size = min(50, limit)
    while len(articles) < limit:
        params = urllib.parse.urlencode({
            "q": query,
            "limit": page_size,
            "page": page,
            "db": "WOS",
        })
        url = f"{WOS_API_BASE}/documents?{params}"
        req = urllib.request.Request(url, headers={
            "X-ApiKey": key,
            "User-Agent": "ZebrafishGeneQA/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"WoS API HTTP {e.code}: {body}") from e

        hits = data.get("hits") or data.get("data") or []
        if not hits:
            break
        for hit in hits:
            if isinstance(hit, dict) and "source" in hit:
                src = hit.get("source") or hit
            else:
                src = hit
            title = (src.get("title") or src.get("article_title") or "").strip()
            abstract = (src.get("abstract") or src.get("abstract_text") or "").strip()
            journal = (src.get("source") or {}).get("sourceTitle") if isinstance(src.get("source"), dict) else src.get("journal")
            if isinstance(journal, dict):
                journal = journal.get("title") or ""
            year = str(src.get("pubyear") or src.get("year") or "")[:4]
            doi = (src.get("doi") or "").strip()
            ut = (src.get("ut") or src.get("uid") or "").replace("WOS:", "").strip()
            pmid = ""
            for ident in src.get("identifiers") or []:
                if isinstance(ident, dict) and ident.get("type") == "pmid":
                    pmid = str(ident.get("value") or "")
            if not title:
                continue
            articles.append({
                "source": "wos",
                "id_label": f"WOS:{ut}" if ut else title[:40],
                "title": title,
                "abstract": abstract,
                "journal": journal or "",
                "year": year,
                "doi": doi,
                "pmid": pmid,
                "wos_ut": ut,
                "authors": "",
            })
            if len(articles) >= limit:
                break
        if len(hits) < page_size:
            break
        page += 1
        time.sleep(REQUEST_INTERVAL)
    return articles[:limit]


def collect_wos(
    per_query: int = 200,
    import_ris: bool = True,
    broad_only: bool = True,
    max_total: int = 0,
) -> List[dict]:
    if per_query <= 0:
        per_query = 500000
    safe_print("\n[Web of Science] 广搜 zebrafish / Danio rerio")
    all_arts: List[dict] = []

    if import_ris and os.path.isdir(WOS_IMPORT_DIR):
        files = [f for f in os.listdir(WOS_IMPORT_DIR) if f.lower().endswith((".ris", ".ciw"))]
        extra_files = 0
        for d in WOS_EXTRA_DIRS:
            if os.path.isdir(d) and d != WOS_IMPORT_DIR:
                extra_files += sum(
                    1 for f in os.listdir(d) if f.lower().endswith((".ris", ".ciw"))
                )
        if files or extra_files:
            safe_print(
                f"  导入 RIS：{WOS_IMPORT_DIR} ({len(files)} 文件)"
                + (f" + 额外目录 ({extra_files} 文件)" if extra_files else "")
            )
            all_arts.extend(import_all_ris_sources())

    queries = list(WOS_BROAD_QUERIES)
    if not broad_only:
        queries.extend(WOS_THEME_QUERIES)

    if _api_key():
        remaining = max_total if max_total > 0 else per_query * len(queries)
        for name, query in queries:
            limit = min(per_query if broad_only else per_query, remaining) if max_total > 0 else per_query
            if limit <= 0:
                break
            safe_print(f"  API 检索 {name}: {query}（最多 {limit} 篇）")
            try:
                batch = search_wos_api(query, limit=limit)
                safe_print(f"    → {len(batch)} 篇")
                all_arts.extend(batch)
                remaining -= len(batch)
            except Exception as e:
                safe_print(f"    [警告] {name} 失败: {e}")
            time.sleep(REQUEST_INTERVAL)
    elif not all_arts:
        safe_print(
            "  [提示] 无 WOS_API_KEY 且无 RIS 文件。\n"
            "  请在 Web of Science 检索 TS=zebrafish → 全选 → 导出 RIS → 放入:\n"
            f"    {WOS_IMPORT_DIR}\n"
            "  （可分批导出，多次放入，程序自动续传去重）"
        )
    return all_arts
