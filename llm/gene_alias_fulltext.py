# -*- coding: utf-8 -*-
"""任务① 扩展：**全文级**别名文献证据（读 gene_alias_doc / gene_alias_lit_stat）。

数据由 `scripts/build_alias_doc_index.py` 离线构建：
它扫一遍 `解析文件/yes_fulltext_gold/batch_*/papers/*.json`（3.2 万篇金标全文），
把每个别名在**题名 / 摘要 / 全文**里的命中情况落表。

为什么不在线扫：`literature_paper` 没有摘要列、`literature_fulltext_gold.body_text` 全空
（正文外置磁盘），在线 SQL 拿不到全文；离线落表后查询是**毫秒级**。

降级策略（重要）：没有索引时本模块返回 None，调用方自动退回原来的**题名级**扫描 ——
所以任何时候都不会因为"某个基因还没建索引"而答不出来。
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from common_utils import logger

# 进程内小缓存：一次提问里 build_alias_evidence 会被调用 1~2 次，避免重复查库
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 300.0
_CANON: Dict[str, str] = {}


def _q(sql: str, params: Optional[dict] = None, max_rows: int = 500) -> List[dict]:
    from mysql_client import get_mysql_client

    res = get_mysql_client().execute_sql(sql, params=params or {}, max_rows=max_rows)
    if not res.get("success"):
        raise RuntimeError(res.get("error") or "查询失败")
    return res.get("data") or []


def canonical_gene_name(gene: str) -> str:
    """把 gene_symbol / 别名归一成 `gene_basic.gene_name`（倒排表就是按它建的）。

    为什么必须有这一步（2026-10-17 实测的真实 bug）：
      `gene_alias_evidence._resolve_gene()` 优先返回 **gene_symbol**（tp53 → "p53"），
      而倒排表里存的是 **gene_name**（"tp53"）。于是 `WHERE gene_name='p53'` 查不到，
      代码就"优雅降级"回题名级 —— 表现是"明明建了全文索引，回答里还是题名数字"，
      而且旧的题名扫描把 `tp53` 也计进了 `p53`（169 篇 vs 真实 28 篇）。
    """
    g = (gene or "").strip()
    if not g:
        return g
    k = g.lower()
    if k in _CANON:
        return _CANON[k]
    out = g
    try:
        from gene_toolkit import gene_id_of

        gid = gene_id_of(g)
        if gid:
            rows = _q("SELECT gene_name FROM gene_basic WHERE gene_id = :gid",
                      {"gid": gid}, max_rows=1)
            name = str(((rows or [{}])[0]).get("gene_name") or "").strip()
            if name:
                out = name
    except Exception as e:  # noqa: BLE001
        logger.warning("[别名·全文] 基因名归一化失败（沿用原值 %s）：%s" % (g, e))
    _CANON[k] = out
    return out


def index_ready(gene: str) -> Optional[dict]:
    """索引是否已建？返回 {'scanned_docs','built_at','aliases'}，未建返回 None。"""
    gene = canonical_gene_name(gene)
    try:
        rows = _q(
            "SELECT COUNT(*) AS n, MAX(scanned_docs) AS scanned, MAX(built_at) AS built_at "
            "FROM gene_alias_lit_stat WHERE gene_name = :g",
            {"g": gene}, max_rows=1,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[别名·全文] 索引探测失败（降级为题名级）：%s" % e)
        return None
    row = (rows or [{}])[0]
    n = int(row.get("n") or 0)
    if n <= 0:
        return None
    return {"aliases": n, "scanned_docs": int(row.get("scanned") or 0),
            "built_at": str(row.get("built_at") or "")}


def alias_fulltext_evidence(gene: str, *, top_per_alias: int = 3) -> Optional[dict]:
    """全文级别名证据；未建索引返回 None（调用方退回题名级）。"""
    if not gene:
        return None
    # 必须先归一化：调用方传进来的往往是 gene_symbol（tp53 → "p53"），
    # 而倒排表是按 gene_basic.gene_name 建的 —— 不归一化就会“查不到 → 静默降级”。
    gene = canonical_gene_name(gene)
    ck = gene.lower()
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]

    info = index_ready(gene)
    if not info:
        _CACHE[ck] = (time.time(), None)
        return None

    stats = _q(
        "SELECT alias, alias_key, alias_source, title_docs, abstract_docs, body_docs, total_docs "
        "FROM gene_alias_lit_stat WHERE gene_name = :g ORDER BY total_docs DESC",
        {"g": gene}, max_rows=200,
    )
    # 每个别名取最值得引用的 top-N 篇（带命中片段 + 题名）
    detail = _q(
        "SELECT alias, alias_key, doc_id, pmid, doi, field, hits, snippet, year, journal, title FROM ("
        "  SELECT d.alias AS alias, d.alias_key AS alias_key, d.doc_id AS doc_id, d.pmid AS pmid,"
        "         d.doi AS doi, d.field AS field, d.hits AS hits, d.snippet AS snippet,"
        "         d.year AS year, d.journal AS journal,"
        "         COALESCE(p.title_en, p.title_zh, '') AS title,"
        "         ROW_NUMBER() OVER (PARTITION BY d.alias_key ORDER BY d.hits DESC, d.year DESC) AS rn"
        "  FROM gene_alias_doc d LEFT JOIN literature_paper p ON p.doc_id = d.doc_id"
        "  WHERE d.gene_name = :g"
        ") t WHERE rn <= :n",
        {"g": gene, "n": max(1, int(top_per_alias))}, max_rows=2000,
    )
    by_alias: Dict[str, List[dict]] = {}
    for r in detail:
        by_alias.setdefault(str(r.get("alias_key") or ""), []).append({
            "doc_id": r.get("doc_id") or "",
            "pmid": str(r.get("pmid") or ""),
            "doi": str(r.get("doi") or ""),
            "title": (r.get("title") or "")[:140],
            "year": str(r.get("year") or ""),
            "journal": (r.get("journal") or "")[:70],
            "field": r.get("field") or "",
            "hits": int(r.get("hits") or 0),
            "snippet": (r.get("snippet") or "")[:300],
        })

    items = []
    for s in stats:
        key = str(s.get("alias_key") or "")
        n = int(s.get("total_docs") or 0)
        items.append({
            "alias": s.get("alias") or "",
            "source": s.get("alias_source") or "",
            "title_docs": int(s.get("title_docs") or 0),
            "abstract_docs": int(s.get("abstract_docs") or 0),
            "body_docs": int(s.get("body_docs") or 0),
            "total_docs": n,
            "samples": by_alias.get(key, []),
        })
    if not items:
        _CACHE[ck] = (time.time(), None)
        return None

    ev = {
        "gene": gene,
        "level": "fulltext",
        "scanned_docs": info["scanned_docs"],
        "built_at": info["built_at"],
        "items": items,
        "any_hit": any(i["total_docs"] > 0 for i in items),
    }
    _CACHE[ck] = (time.time(), ev)
    return ev


def alias_reference_items(gene: str, *, limit: int = 25) -> List[dict]:
    """从全文倒排表取「可引用文献」条目（题名 / 期刊 / 年 / DOI / PMID），按证据强度排序。

    为什么需要它（2026-10-17 用户反馈两个现象）：
      · 「参考文献（系统生成）：PMID … 等。」——通用 RAG 证据池被相关性地板筛空时，
        `ensure_reference_footer` 拿到 0 条证据就原样返回，模型那句占位话就留在正文里；
      · 「只列了 6 条，不到 20~25」——证据池本来就只有 6 篇合格条目，补无可补。
    而别名类问题其实有几千篇精确实证（倒排表），只是一直没喂给参考文献生成器。

    排序：先"题名命中"（说明论文就是讲这个别名的），再按命中次数与年份。
    """
    gene = canonical_gene_name(gene)
    if not gene:
        return []
    rows = _q(
        "SELECT d.doc_id, d.pmid, d.doi, d.field, d.hits, d.year, d.journal,"
        "       COALESCE(p.title_en, p.title_zh, '') AS title"
        " FROM gene_alias_doc d LEFT JOIN literature_paper p ON p.doc_id = d.doc_id"
        " WHERE d.gene_name = :g",
        {"g": gene}, max_rows=4000,
    )
    if not rows:
        return []
    prio = {"title": 0, "abstract": 1, "body": 2}
    best: Dict[str, dict] = {}
    for r in rows:
        did = str(r.get("doc_id") or "")
        if not did:
            continue
        cand = {
            "doc_id": did,
            "pmid": str(r.get("pmid") or "").strip(),
            "doi": str(r.get("doi") or "").strip(),
            "title": str(r.get("title") or "").strip(),
            "journal": str(r.get("journal") or "").strip(),
            "year": str(r.get("year") or "").strip(),
            "field": str(r.get("field") or "body"),
            "hits": int(r.get("hits") or 0),
        }
        cur = best.get(did)
        if cur is None or (prio.get(cand["field"], 3), -cand["hits"], -int(cand["year"] or 0)) < (
                prio.get(cur["field"], 3), -cur["hits"], -int(cur["year"] or 0)):
            best[did] = cand
    items = [v for v in best.values() if v["title"]]
    items.sort(key=lambda x: (prio.get(x["field"], 3), -x["hits"],
                              -int(x["year"]) if x["year"].isdigit() else 0))
    return items[: max(1, int(limit))]


def clear_cache() -> None:
    _CACHE.clear()


def copy_role_evidence(symbols: List[str], *, top_per: int = 2, max_copies: int = 10) -> Dict[str, dict]:
    """任务②：**每个复制体自己的文献证据** —— 用来回答「哪个复制体保留了哪个功能」。

    思路：倒排表是按"基因"建的，直接把复制体当成一个基因去查它自己的命中即可，
    不需要新表、不需要新扫描（前提是该复制体也建过索引，见
    `scripts/build_alias_doc_index.py --from-paralogs <基因>`）。

    返回 {symbol: {"indexed": bool, "total_docs": int, "samples": [...]}}；
    没建索引就返回 indexed=False —— 提示词会据此要求模型写「证据不足」，而不是编造分工。
    """
    out: Dict[str, dict] = {}
    for sym in (symbols or [])[:max(1, int(max_copies))]:
        s = canonical_gene_name(str(sym or "").strip())
        if not s or s in out:
            continue
        try:
            stat = _q(
                "SELECT SUM(total_docs) AS tot, SUM(body_docs) AS body FROM gene_alias_lit_stat "
                "WHERE gene_name = :g", {"g": s}, max_rows=1,
            )
            tot = int(((stat or [{}])[0]).get("tot") or 0)
            if tot <= 0:
                out[s] = {"indexed": False, "total_docs": 0, "samples": []}
                continue
            n = max(1, int(top_per))
            det = _q(
                "SELECT d.doc_id, d.pmid, d.doi, d.field, d.hits, d.snippet, d.year, d.journal,"
                " COALESCE(p.title_en, p.title_zh, '') AS title"
                " FROM gene_alias_doc d LEFT JOIN literature_paper p ON p.doc_id = d.doc_id"
                " WHERE d.gene_name = :g ORDER BY d.hits DESC, d.year DESC LIMIT %d" % n,
                {"g": s}, max_rows=100,
            )
            out[s] = {
                "indexed": True,
                "total_docs": tot,
                "samples": [{
                    "doc_id": r.get("doc_id") or "",
                    "pmid": str(r.get("pmid") or ""),
                    "doi": str(r.get("doi") or ""),
                    "title": (r.get("title") or "")[:140],
                    "year": str(r.get("year") or ""),
                    "journal": (r.get("journal") or "")[:70],
                    "snippet": (r.get("snippet") or "")[:300],
                } for r in det],
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("[复制体] %s 证据查询失败：%s" % (s, e))
            out[s] = {"indexed": False, "total_docs": 0, "samples": []}
    return out


def format_copy_role_lines(role_ev: Dict[str, dict], copies: List[str], lang: str = "zh",
                           *, per_copy: int = 2) -> List[str]:
    """把每个复制体的证据写成若干行（供 format_paralog_prompt_block 拼接）。"""
    en = (lang or "zh").lower().startswith("en")
    lines: List[str] = []
    for c in copies:
        ev = role_ev.get(c) or {}
        if not ev.get("indexed"):
            lines.append(("  - %s → no local index / no hits: functional role must be reported as "
                          "'insufficient evidence'" % c) if en else
                         ("  - %s → 本地未建索引或无命中：分工只能写「证据不足」" % c))
            continue
        head = ("  - %s → %d local papers mention it" % (c, ev["total_docs"])) if en else \
               ("  - %s → 本地 %d 篇文献提到它" % (c, ev["total_docs"]))
        lines.append(head)
        for s in (ev.get("samples") or [])[:max(1, int(per_copy))]:
            cite = ("PMID %s" % s["pmid"]) if s["pmid"] else (
                ("DOI %s" % s["doi"]) if s["doi"] else s["doc_id"])
            lines.append("      · %s（%s）%s" % (cite, s.get("year") or "", s["title"][:90]))
            if s.get("snippet"):
                lines.append("        片段：%s" % s["snippet"][:170])
    return lines


def format_fulltext_block(ev: dict, lang: str = "zh", alias_lines: str = "",
                          want_stats: bool = False) -> str:
    """把全文级证据写成提示词块。

    alias_lines：可选，由调用方传入「全部别名与历史名 + 来源」那一段，
    这样本块自带完整别名清单，模型不需要再去别处找。

    want_stats（2026-10-28 新增，默认 False）：
      只有当用户**明确**问了「某个写法在文献里出现多少次 / 文献里怎么用」时才为 True。
      默认 False = 别名类问题只回答别名本身（清单 + 来源 + 权威 ID），
      不再输出本地语料命中篇数、题名/摘要/全文计数、"本地库未见记录" 这类本地统计。
    """
    en = (lang or "zh").lower().startswith("en")
    alias_pre = (alias_lines or "").strip()

    if not want_stats:
        # 只给权威别名清单，不带任何本地语料统计
        if not alias_pre:
            return ""
        if en:
            return (
                "【GENE ALIAS RECORDS · authoritative local records (ZFIN / NCBI / Ensembl), "
                "do not invent】\n"
                f"{alias_pre}\n"
                "【SCOPE】The user asked for the aliases only. List every alias / historical name "
                "with its source. Do NOT report any local-corpus hit statistics, do NOT write "
                "'no local record' for an alias, and do NOT split the list into seen/unseen "
                "groups. No content unrelated to the question.\n"
            )
        return (
            "【基因别名记录 · 本地权威记录（ZFIN / NCBI / Ensembl），禁止编造】\n"
            f"{alias_pre}\n"
            "【回答范围】用户问的就是别名：给出全部别名与历史名并标注来源即可。"
            "禁止输出任何本地语料命中统计（各写法命中多少篇、“本地库未见记录”、"
            "“未出现过”之类），也不要把别名拆成「出现过 / 没出现过」两组；"
            "不要输出与问题无关的内容。\n"
        )

    if not ev or not ev.get("items"):
        return ""
       
    items = ev["items"]
    lines = []
    for it in items[:12]:
        if it["total_docs"] <= 0:
            lines.append("  - %s → 0（本地全文语料中未出现）" % it["alias"])
            continue
        smp = it["samples"][0] if it["samples"] else None
        ex = ""
        if smp:
            cite = ("PMID %s" % smp["pmid"]) if smp["pmid"] else (
                ("DOI %s" % smp["doi"]) if smp["doi"] else smp["doc_id"])
            ex = "｜例：%s（%s）%s｜片段：%s" % (cite, smp.get("year") or "", smp["title"][:70],
                                              smp["snippet"][:120])
        lines.append("  - %s [%s] 题名 %d · 摘要 %d · 全文 %d（去重 %d）%s"
                     % (it["alias"], it.get("source") or "?", it["title_docs"],
                        it["abstract_docs"], it["body_docs"], it["total_docs"], ex))
    head_src = ("本地金标**全文**语料（%d 篇，已离线建索引，%s）"
                % (ev.get("scanned_docs") or 0, ev.get("built_at") or ""))
    if en:
        return (
            "【GENE ALIAS EVIDENCE · FULL-TEXT level, local gold corpus, do not invent】\n"
            "%sSource: %s\nAlias → document counts (title / abstract / full text, deduped):\n%s\n"
            "Use these counts; the first sentence of your answer must still list ALL historical "
            "aliases with sources. Mention which aliases actually appear in the literature and how "
            "many papers (say 'no local record' when a count is 0).\n"
            % (alias_lines, head_src, "\n".join(lines))
        )
    return (
        "【基因别名证据 · **全文级**（本地金标全文语料，已离线建倒排，禁止编造）】\n"
        "%s来源：%s\n别名 → 命中文献数（题名 / 摘要 / 全文，已去重）：\n%s\n"
        "【强制格式】回答**第一句**仍必须列出该基因的全部历史别名并标注来源；"
        "随后说明哪些别名在文献中真正出现过、各有多少篇（为 0 的写「本地库未见记录」，不要编造）；"
        "引用时请给 PMID/DOI，便于核对。\n"
        % (alias_lines, head_src, "\n".join(lines))
    )
