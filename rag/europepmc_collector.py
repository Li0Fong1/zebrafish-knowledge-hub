# -*- coding: utf-8 -*-
"""Europe PMC 开放文献检索（补充 WoS/PubMed 未覆盖条目）。"""
import bootstrap  # noqa: F401

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, List, Optional

from common_utils import safe_print

EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
REQUEST_INTERVAL = float(os.getenv("EPMC_REQUEST_INTERVAL", "0.8"))
_RETRY_BACKOFF = float(os.getenv("EPMC_RETRY_BACKOFF", "3.0"))
_MAX_WAIT = float(os.getenv("EPMC_MAX_WAIT", "120"))
_TRANSIENT_HTTP = frozenset({429, 500, 502, 503, 504})

# 与 PubMed 广搜等价的 Europe PMC 检索（国内网络通常更稳定）
EPMC_ZEBRAFISH_BROAD = '(zebrafish) OR ("Danio rerio")'

DEFAULT_EPMC_QUERIES = [
    ("epmc_zebrafish", "zebrafish"),
    ("epmc_danio", "Danio rerio"),
    ("epmc_zebrafish_gene", "zebrafish AND (gene OR genetics)"),
    ("epmc_zebrafish_dev", "zebrafish AND (development OR embryo)"),
    ("epmc_zebrafish_disease", "zebrafish AND (disease OR cancer OR drug screening)"),
]


def _epmc_request(params: dict) -> dict:
    """Europe PMC 请求：遇网络/503 等临时错误时自动等待并重试，直到成功。"""
    url = EPMC_BASE + "?" + urllib.parse.urlencode(params)
    attempt = 0
    while True:
        attempt += 1
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in _TRANSIENT_HTTP:
                raise
            wait = min(_MAX_WAIT, _RETRY_BACKOFF * (2 ** min(attempt - 1, 6)))
            safe_print(
                f"  [网络] Europe PMC HTTP {e.code}，{wait:.0f}s 后自动重试（第 {attempt} 次，不中断）…"
            )
            time.sleep(wait)
        except (urllib.error.URLError, ConnectionResetError, json.JSONDecodeError, OSError, TimeoutError) as e:
            wait = min(_MAX_WAIT, _RETRY_BACKOFF * (2 ** min(attempt - 1, 6)))
            safe_print(
                f"  [网络] Europe PMC {type(e).__name__}，{wait:.0f}s 后自动重试（第 {attempt} 次，不中断）…"
            )
            time.sleep(wait)


def _result_to_article(r: dict) -> Optional[dict]:
    title = (r.get("title") or "").strip()
    abstract = (r.get("abstractText") or "").strip()
    pmid = str(r.get("pmid") or "").strip()
    pmcid = str(r.get("pmcid") or "").strip()
    doi = (r.get("doi") or "").strip()
    journal = (r.get("journalTitle") or "")
    if not journal:
        ji = r.get("journalInfo") or {}
        j = ji.get("journal") if isinstance(ji, dict) else {}
        journal = (j.get("title") if isinstance(j, dict) else "") or ""
    year = str(r.get("pubYear") or "")[:4]
    if not title or not (pmid or pmcid or doi):
        return None
    return {
        "source": "pubmed",
        "id_label": f"PMID:{pmid}" if pmid else f"PMCID:{pmcid}",
        "title": title,
        "abstract": abstract,
        "journal": journal,
        "year": year,
        "doi": doi,
        "pmid": pmid,
        "epmc_id": pmid or pmcid or doi,
        "authors": "",
    }


def epmc_total_count(query: str = EPMC_ZEBRAFISH_BROAD) -> int:
    data = _epmc_request({"query": query, "format": "json", "pageSize": 1, "cursorMark": "*", "resultType": "core"})
    return int(data.get("hitCount") or 0)


def search_europepmc_page(
    query: str,
    cursor: str = "*",
    page_size: int = 200,
    sort: str = "",
) -> tuple:
    """返回 (articles, next_cursor, raw_count)。raw_count 为 API 原始命中条数（含被过滤项）。

    sort: 可选排序，如 "P_PDATE_D desc"（按首次出版日期倒序）。
    增量采集靠它把「最新文献」排到第一页，否则默认相关性排序下
    新文献会散落在十几万条结果里，翻页永远翻不到。
    """
    params = {
        "query": query,
        "format": "json",
        "pageSize": page_size,
        "cursorMark": cursor,
        "resultType": "core",
    }
    if sort:
        params["sort"] = sort
    data = _epmc_request(params)
    results = data.get("resultList", {}).get("result") or []
    articles = []
    for r in results:
        art = _result_to_article(r)
        if art:
            articles.append(art)
    next_cursor = data.get("nextCursorMark") or cursor
    return articles, next_cursor, len(results)


def search_europepmc(query: str, page_size: int = 100, max_results: int = 500) -> List[dict]:
    articles = []
    cursor = "*"
    while len(articles) < max_results:
        fetch = min(page_size, max_results - len(articles))
        params = {
            "query": query,
            "format": "json",
            "pageSize": fetch,
            "cursorMark": cursor,
            "resultType": "core",
        }
        data = _epmc_request(params)
        results = data.get("resultList", {}).get("result") or []
        if not results:
            break
        for r in results:
            art = _result_to_article(r)
            if not art:
                continue
            articles.append(art)
            if len(articles) >= max_results:
                break
        next_cursor = data.get("nextCursorMark")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(REQUEST_INTERVAL)
    return articles


def collect_europepmc(per_query: int = 300) -> List[dict]:
    safe_print("\n[Europe PMC]（开放获取，补充 PubMed/WoS）")
    all_arts = []
    for name, query in DEFAULT_EPMC_QUERIES:
        safe_print(f"  检索 {name}: {query}")
        batch = search_europepmc(query, max_results=per_query)
        safe_print(f"    → {len(batch)} 篇")
        all_arts.extend(batch)
    return all_arts
