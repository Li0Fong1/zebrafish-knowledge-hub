# -*- coding: utf-8 -*-
"""
多源文献采集编排：Web of Science + PubMed（凡提到 zebrafish 均采集）。

用法见 scripts/collect_literature.py
"""
import bootstrap  # noqa: F401

import os
import time
from typing import List, Optional, Set

from common_utils import safe_print
from literature_models import LiteratureProgress, ingest_articles
from literature_collector import (
    PUBMED_ZEBRAFISH_BROAD,
    PUBMED_QUERIES,
    search_pubmed_page,
    pubmed_total_count,
    fetch_abstracts,
)
from wos_collector import collect_wos
from europepmc_collector import (
    EPMC_ZEBRAFISH_BROAD,
    REQUEST_INTERVAL as EPMC_INTERVAL,
    epmc_total_count,
    search_europepmc_page,
)


def _dedupe_articles(articles: List[dict]) -> List[dict]:
    seen: Set[str] = set()
    out = []
    for a in articles:
        key = (
            (a.get("pmid") or "").strip()
            or (a.get("wos_ut") or "").strip()
            or (a.get("doi") or "").strip().lower()
            or (a.get("title") or "").strip()[:80]
        )
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(a)
    return out


# ---- 本次运行的新增产物（供调用方写库、建索引） ----
# 采集过程是流式入库的（每满 100 篇落盘一次），这里把「这批到底加了哪些」
# 累积下来，供 hub_maintenance 写 literature_sync_log / literature_paper，
# 以及只把新增文件追加进 FAISS，而不是每次整体重建。
_NEW_RECORDS: List[dict] = []
_NEW_PATHS: List[str] = []
_STAGE_CB = None


def _stage(name: str, **info) -> None:
    cb = _STAGE_CB
    if cb is None:
        return
    try:
        cb(name, info)
    except Exception:  # noqa: BLE001 - 进度回调绝不能打断采集
        pass


def take_new_records() -> List[dict]:
    """取出本次新入库的文献记录（取出即清空）。"""
    global _NEW_RECORDS
    out, _NEW_RECORDS = _NEW_RECORDS, []
    return out


def take_new_paths() -> List[str]:
    global _NEW_PATHS
    out, _NEW_PATHS = _NEW_PATHS, []
    return out


def _ingest_and_mark(batch: List[dict], progress: LiteratureProgress, batch_name: str) -> int:
    if not batch:
        return 0
    paths: List[str] = []
    n = ingest_articles(batch, batch_name, paths_out=paths)
    _NEW_PATHS.extend(paths)
    _NEW_RECORDS.extend(batch)
    for a in batch:
        progress.mark(a)
    progress.save()
    return n


def _resolve_cap(max_results: int, total: int) -> int:
    """max_results <= 0 表示不限制，采到数据库返回的全部命中数。"""
    if max_results <= 0:
        return total
    return min(max_results, total)


# ---- 增量采集参数（定时任务 / 手动「更新文献」都走增量） ----
# 按首次出版日期倒序：新文献排第一页。默认相关性排序下，新文献散落在十几万条
# 结果里，无论翻多少页都撞不上（这是 8-10 月一直采到 0 篇的另一半原因）。
EPMC_NEWEST_FIRST = os.getenv("EPMC_NEWEST_FIRST", "P_PDATE_D desc")
# 连续这么多页都没扫到未入库条目就收工，避免在历史结果集里空翻
EMPTY_PAGE_LIMIT = int(os.getenv("EPMC_EMPTY_PAGE_LIMIT", "6"))
# 配额模式下「翻页但零新增」时的确认性补扫，页数上限
GAP_FILL_PAGES = int(os.getenv("EPMC_GAP_FILL_PAGES", "10"))


def _ncbi_reachable() -> bool:
    try:
        pubmed_total_count(PUBMED_ZEBRAFISH_BROAD)
        return True
    except Exception:
        return False


def _collect_pubmed_via_epmc(
    progress: LiteratureProgress,
    max_results: int = 0,
    page_size: int = 200,
    resume: bool = True,
    gap_fill: bool = False,
    max_pages: int = 0,
) -> dict:
    """NCBI 不可用时，经 Europe PMC 镜像采集 PubMed 文献。

    两种模式：
      · 全量（max_results <= 0）：沿用 cursor 一路翻到底，可断点续传；
      · 增量（max_results > 0，定时任务和手动「更新文献」都是这种）：
        每次从第 1 页开始按出版日期倒序扫，采满配额或连续多页无新增即停。

    2026-10 修复：原先的终止条件是 `len(progress.pmids) >= cap`，把「库内
    累计条数」当成了「本次已采条数」。库里早有 1064 个 PMID，配额只有 80，
    于是每次第一轮就 break，一篇都不抓（catalog 自 7-12 起没再变过）。
    """
    query = EPMC_ZEBRAFISH_BROAD
    total = epmc_total_count(query)
    unlimited = max_results <= 0
    incremental = not unlimited
    cap = _resolve_cap(max_results, total)
    if gap_fill or incremental:
        # 增量/补扫一律从头扫最新：长期游标会随新文献入库而漂移，接着翻只会越翻越旧
        cursor = "*"
    else:
        cursor = progress.epmc_cursor if resume else "*"
    sort = EPMC_NEWEST_FIRST if incremental else ""
    label = "补扫" if gap_fill else ("增量" if incremental else "续传")
    safe_print(f"  [Europe PMC 镜像] 检索式: {query}")
    goal = f"全部 {total} 篇" if unlimited else f"{cap} 篇"
    safe_print(f"  命中约 {total} 篇，目标：{goal} | {label} cursor={cursor[:20]}…")

    stats = {"searched": 0, "new": 0, "skipped": 0, "files": 0, "pages": 0}
    pending: List[dict] = []
    prev_cursor = None
    last_report = time.time()
    stale_retry = 0
    session_hits = 0
    empty_streak = 0

    while True:
        # 配额按「本次新增（含待入库）」算，不是「库内累计」
        if incremental and (stats["new"] + len(pending)) >= cap:
            break
        if max_pages and stats["pages"] >= max_pages:
            break
        fetch = min(page_size, 1000)
        articles, next_cursor, raw_count = search_europepmc_page(
            query, cursor=cursor, page_size=fetch, sort=sort
        )

        if raw_count == 0:
            if cursor != "*" and stale_retry == 0:
                safe_print("  [续传] cursor 无结果，从头补扫未入库条目…")
                cursor = "*"
                progress.epmc_cursor = "*"
                stale_retry += 1
                continue
            break

        stats["pages"] += 1
        stats["searched"] += raw_count
        session_hits += raw_count
        if not gap_fill:
            progress.epmc_hits_scanned += raw_count

        page_before = len(pending)
        for art in articles:
            if progress.is_seen(art):
                stats["skipped"] += 1
                continue
            pending.append(art)
        page_new = len(pending) - page_before

        now = time.time()
        if now - last_report >= 10:
            safe_print(
                f"  扫描第 {stats['pages']} 页 | 跳过 {stats['skipped']} | "
                f"待入库 {len(pending)} | PMID {len(progress.pmids)}/{total} "
                f"(本次新增 {stats['new']})"
            )
            last_report = now
        _stage("collecting", pages=stats["pages"], new=stats["new"],
               skipped=stats["skipped"], pending=len(pending), total=total)

        # 配额要精确：一次待入库可能超过剩余配额（一页就是 200 条），这里截断，
        # 否则设 5 篇会一口气入库 193 篇。
        quota_left = max(0, cap - stats["new"]) if incremental else None
        need_flush = len(pending) >= 100 or (
            quota_left is not None and len(pending) >= quota_left > 0
        )
        if need_flush:
            batch = _dedupe_articles(pending)
            if quota_left is not None:
                batch = batch[:quota_left]
            pending.clear()
            n = _ingest_and_mark(batch, progress, f"epmc_broad_{len(batch)}")
            stats["new"] += n
            stats["files"] += 1

        progress.epmc_cursor = next_cursor
        progress.save()

        # 增量模式：连着好几页都是老条目就收工，别在历史结果集里空转
        if page_new <= 0:
            empty_streak += 1
            if incremental and empty_streak >= EMPTY_PAGE_LIMIT:
                safe_print(
                    f"  连续 {empty_streak} 页没有未入库条目，本次增量结束"
                    f"（已扫 {stats['searched']} 条，新增 {stats['new']} 篇）"
                )
                break
        else:
            empty_streak = 0

        if next_cursor == cursor or next_cursor == prev_cursor:
            break

        prev_cursor = cursor
        cursor = next_cursor
        time.sleep(EPMC_INTERVAL)

    if pending:
        batch = _dedupe_articles(pending)
        if incremental:
            batch = batch[: max(0, cap - stats["new"])]
        if batch:
            n = _ingest_and_mark(batch, progress, f"epmc_broad_final_{len(batch)}")
            stats["new"] += n
            stats["files"] += 1

    stats["total"] = total
    stats["session_hits"] = session_hits
    return stats


def _maybe_epmc_gap_fill(
    progress: LiteratureProgress,
    stats: dict,
    max_results: int,
    page_size: int,
) -> dict:
    """分页未扫全或 cursor 过早终止时，从头补扫漏采条目。

    配额模式（定时任务）下的处理：正常情况不补扫 —— 增量采集本身就从最新
    往后扫到采满，补扫只会把刚扫过的几页再扫一遍。只有「确实翻了页却一条
    新的都没采到」时，才往前多翻几页确认，避免因为排序里混了老记录就把
    整周判成没有新文献。
    """
    total = stats.get("total") or 0
    if total <= 0:
        return stats
    if max_results > 0:
        if stats.get("new", 0) > 0 or stats.get("pages", 0) == 0:
            return stats
        safe_print(f"  [补扫] 本次翻页未采到新条目，再往前确认 {GAP_FILL_PAGES} 页…")
        gap_stats = _collect_pubmed_via_epmc(
            progress, max_results, page_size, resume=True, gap_fill=True,
            max_pages=GAP_FILL_PAGES,
        )
        for k in ("new", "skipped", "files", "searched", "pages", "session_hits"):
            stats[k] = stats.get(k, 0) + gap_stats.get(k, 0)
        return stats
    unique_ids = len(progress.pmids | progress.epmc_ids)
    scanned = progress.epmc_hits_scanned
    need_gap = (
        scanned < total * 0.97
        or (stats.get("pages", 0) == 0 and stats.get("new", 0) == 0)
        or unique_ids < total * 0.90
    )
    if not need_gap:
        if unique_ids < total:
            safe_print(
                f"  [说明] EPMC 命中 {total} 条含无 PMID/仅预印本等，"
                f"已入库唯一标识 {unique_ids} 条（PMID {len(progress.pmids)}）"
            )
        return stats
    safe_print(
        f"\n  [补扫] API 累计扫描 {scanned}/{total}，唯一标识 {unique_ids}，"
        f"疑似漏采，从头补扫未入库条目…"
    )
    gap_stats = _collect_pubmed_via_epmc(
        progress, max_results, page_size, resume=True, gap_fill=True
    )
    for k in ("new", "skipped", "files", "searched", "pages", "session_hits"):
        stats[k] = stats.get(k, 0) + gap_stats.get(k, 0)
    progress.epmc_hits_scanned = max(progress.epmc_hits_scanned, total)
    progress.save()
    return stats


def _collect_pubmed_themes(
    progress: LiteratureProgress,
    per_query: int,
    include_genes: bool,
    gene_per_query: int,
) -> dict:
    from literature_collector import CLASSIC_GENE_QUERIES, _gene_queries_from_file, search_pubmed

    stats = {"new": 0, "skipped": 0, "files": 0}
    pubmed_pmids: List[str] = []
    queries = list(PUBMED_QUERIES)
    for name, q in queries:
        pmids = search_pubmed(q, retmax=per_query)
        new = [p for p in pmids if p not in progress.pmids]
        safe_print(f"  {name}: 检索 {len(pmids)} | 待下载 {len(new)}")
        pubmed_pmids.extend(new)

    if include_genes:
        seen_gq = set()
        for gq in list(CLASSIC_GENE_QUERIES) + _gene_queries_from_file():
            if gq in seen_gq:
                continue
            seen_gq.add(gq)
            pmids = search_pubmed(gq, retmax=gene_per_query)
            pubmed_pmids.extend(p for p in pmids if p not in progress.pmids)

    seen_p = set()
    unique_pmids = []
    for p in pubmed_pmids:
        if p not in seen_p:
            seen_p.add(p)
            unique_pmids.append(p)

    pending: List[dict] = []
    for i in range(0, len(unique_pmids), 80):
        for art in fetch_abstracts(unique_pmids[i:i + 80]):
            art.setdefault("source", "pubmed")
            art.setdefault("id_label", f"PMID:{art.get('pmid', '')}")
            if progress.is_seen(art):
                stats["skipped"] += 1
            else:
                pending.append(art)
        if len(pending) >= 100:
            batch = _dedupe_articles(pending)
            n = _ingest_and_mark(batch, progress, f"pubmed_theme_{i}")
            stats["new"] += n
            stats["files"] += 1
            pending.clear()

    if pending:
        batch = _dedupe_articles(pending)
        n = _ingest_and_mark(batch, progress, f"pubmed_theme_final_{len(batch)}")
        stats["new"] += n
        stats["files"] += 1
    return stats


def _collect_pubmed_broad(
    progress: LiteratureProgress,
    max_results: int = 0,
    page_size: int = 200,
    fetch_chunk: int = 80,
    resume: bool = True,
    pubmed_backend: str = "auto",
) -> dict:
    """分页检索 PubMed 广搜；NCBI 失败时自动切换 Europe PMC。"""
    backend = (pubmed_backend or "auto").lower()
    if backend == "epmc":
        stats = _collect_pubmed_via_epmc(progress, max_results, page_size, resume)
        return _maybe_epmc_gap_fill(progress, stats, max_results, page_size)
    if backend == "auto" and not _ncbi_reachable():
        safe_print("  [提示] NCBI 连接不稳定，自动改用 Europe PMC 镜像采集 PubMed 文献")
        stats = _collect_pubmed_via_epmc(progress, max_results, page_size, resume)
        return _maybe_epmc_gap_fill(progress, stats, max_results, page_size)

    query = PUBMED_ZEBRAFISH_BROAD
    total = pubmed_total_count(query)
    unlimited = max_results <= 0
    cap = _resolve_cap(max_results, total)
    retstart = progress.pubmed_broad_retstart if resume else 0
    # 注意：这里曾用 len(progress.pmids) 反推 retstart「避免从头重扫」，但
    # 「库内已入库条数」≠「PubMed 结果集里已翻到第几条」（翻页按 200 条跳号，
    # 库里还混着大量 WoS 条目），估出来的位置会把整轮都跳过。宁可从头扫。
    safe_print(f"  检索式: {query}")
    safe_print(
        f"  PubMed 命中约 {total} 篇，目标：{'全部 ' + str(total) + ' 篇' if unlimited else str(cap) + ' 篇'} | "
        f"已入库 PMID {len(progress.pmids)} | 续传位置 retstart={retstart}"
    )

    stats = {"searched": 0, "new": 0, "skipped": 0, "files": 0}
    pending_arts: List[dict] = []

    try:
        while unlimited or stats["new"] < cap:
            fetch_n = page_size if unlimited else max(1, min(page_size, cap - stats["new"]))
            pmids = search_pubmed_page(query, retstart=retstart, retmax=fetch_n)
            if not pmids:
                break
            retstart += len(pmids)
            stats["searched"] += len(pmids)
            progress.pubmed_broad_retstart = retstart
            progress.save()

            new_pmids = [p for p in pmids if p not in progress.pmids]
            stats["skipped"] += len(pmids) - len(new_pmids)
            safe_print(
                f"  分页 {retstart}/{'全部 ' + str(total) if unlimited else str(cap)}: "
                f"本页 {len(pmids)} | 新增 {len(new_pmids)} | 累计入库 {len(progress.pmids)}"
            )

            for i in range(0, len(new_pmids), fetch_chunk):
                chunk_pmids = new_pmids[i:i + fetch_chunk]
                for art in fetch_abstracts(chunk_pmids):
                    art.setdefault("source", "pubmed")
                    art.setdefault("id_label", f"PMID:{art.get('pmid', '')}")
                    if progress.is_seen(art):
                        stats["skipped"] += 1
                        continue
                    pending_arts.append(art)

                if len(pending_arts) >= 100:
                    batch = _dedupe_articles(pending_arts)
                    n = _ingest_and_mark(batch, progress, f"pubmed_broad_{retstart}_{len(batch)}")
                    stats["new"] += n
                    stats["files"] += 1
                    pending_arts.clear()
                    safe_print(f"    已入库 {stats['new']} 篇新文献")

            if len(pmids) < fetch_n:
                break
    except Exception as e:
        if pending_arts:
            batch = _dedupe_articles(pending_arts)
            n = _ingest_and_mark(batch, progress, f"pubmed_broad_partial_{len(batch)}")
            stats["new"] += n
            stats["files"] += 1
        progress.save()
        if backend == "auto":
            safe_print(f"\n  [提示] NCBI 采集中断 ({type(e).__name__})，切换 Europe PMC 镜像续采…")
            epmc_stats = _collect_pubmed_via_epmc(progress, max_results, page_size, resume=True)
            epmc_stats = _maybe_epmc_gap_fill(progress, epmc_stats, max_results, page_size)
            stats["new"] += epmc_stats["new"]
            stats["files"] += epmc_stats.get("files", 0)
            stats["skipped"] += epmc_stats.get("skipped", 0)
            return stats
        safe_print(
            f"\n  [中断] 进度已保存（retstart={progress.pubmed_broad_retstart}，"
            f"已入库 {len(progress.pmids)} 篇）。请重新运行菜单 11 续传。"
        )
        raise

    if pending_arts:
        batch = _dedupe_articles(pending_arts)
        n = _ingest_and_mark(batch, progress, f"pubmed_broad_final_{len(batch)}")
        stats["new"] += n
        stats["files"] += 1

    return stats


def run_multi_source_collection(
    sources: Optional[List[str]] = None,
    max_pubmed: int = 0,
    wos_per_query: int = 0,
    broad_only: bool = True,
    per_query: int = 200,
    include_genes: bool = False,
    gene_per_query: int = 40,
    rebuild_index: bool = True,
    resume: bool = True,
    import_wos_ris: bool = True,
    pubmed_backend: str = "auto",
    on_stage=None,
):
    """
    sources: wos, pubmed 的子集；默认两者都采。
    broad_only: True 时只广搜 zebrafish，不加细分主题。
    on_stage: 可选的进度回调 (stage_name, info_dict)，
              stage ∈ collecting / indexing / done，用于前端显示「跑到哪一步」。
    """
    global _STAGE_CB, _NEW_RECORDS, _NEW_PATHS
    _STAGE_CB = on_stage
    _NEW_RECORDS, _NEW_PATHS = [], []
    if sources is None:
        sources = ["wos", "pubmed"]
    sources = [s.lower().strip() for s in sources]

    progress = LiteratureProgress()
    if not resume:
        progress.pmids = set()
        progress.wos_ut = set()
        progress.dois = set()
        progress.epmc_ids = set()
        progress.pubmed_broad_retstart = 0
        progress.epmc_cursor = "*"
        progress.epmc_hits_scanned = 0

    safe_print("=" * 60)
    safe_print("  斑马鱼文献采集（WoS + PubMed，凡提到 zebrafish 均采集）")
    safe_print(f"  来源: {', '.join(sources)}")
    safe_print("  网络中断/503 将自动等待重试，采完为止（Ctrl+C 可手动停止）")
    safe_print(
        f"  已有 PMID {len(progress.pmids)} | WoS {len(progress.wos_ut)} | DOI {len(progress.dois)}"
    )
    safe_print("=" * 60)

    stats = {"wos": 0, "pubmed": 0, "new": 0, "skipped": 0, "files": 0}

    # --- Web of Science ---
    if "wos" in sources:
        _stage("collecting", source="wos", new=0)
        wos_arts = collect_wos(
            per_query=wos_per_query,
            import_ris=import_wos_ris,
            broad_only=broad_only,
            max_total=wos_per_query,
        )
        pending = [a for a in wos_arts if not progress.is_seen(a)]
        stats["skipped"] += len(wos_arts) - len(pending)
        stats["wos"] = len(pending)
        for i in range(0, len(pending), 100):
            batch = pending[i:i + 100]
            n = _ingest_and_mark(batch, progress, f"wos_{i}_{len(batch)}")
            stats["new"] += n
            stats["files"] += 1
            safe_print(f"  WoS 已入库 {min(i + 100, len(pending))}/{len(pending)} 篇")

    # --- PubMed ---
    if "pubmed" in sources:
        safe_print("\n[PubMed] 广搜 zebrafish / Danio rerio")
        if broad_only:
            pm = _collect_pubmed_broad(
                progress, max_results=max_pubmed, resume=resume, pubmed_backend=pubmed_backend
            )
        else:
            pm = _collect_pubmed_themes(progress, per_query, include_genes, gene_per_query)
        stats["pubmed"] = pm["new"]
        stats["new"] += pm["new"]
        stats["skipped"] += pm.get("skipped", 0)
        stats["files"] += pm.get("files", 0)

    if rebuild_index and stats["new"] > 0:
        # 只把新增的几个文件追加进索引：整体重建要重嵌 63 MB 文本，纯 CPU 上几小时，
        # 而每周增量只有几百篇。append 失败会自动回退整体重建。
        _stage("indexing", new=stats["new"], files=len(_NEW_PATHS))
        safe_print("\n[文献采集] 增量更新 RAG 向量索引 …")
        from knowledge_ingest import append_rag_documents
        stats["index"] = append_rag_documents(list(_NEW_PATHS))
        safe_print(f"  RAG 索引已更新：{stats['index']}")

    _stage("done", new=stats["new"])
    _STAGE_CB = None
    safe_print(
        f"\n[完成] 本次新增 {stats['new']} 篇 | WoS {stats['wos']} | PubMed {stats['pubmed']} | "
        f"累计 PMID {len(progress.pmids)} | WoS {len(progress.wos_ut)} | DOI {len(progress.dois)} | "
        f"EPMC 已扫 {progress.epmc_hits_scanned} 条"
    )
    return stats
