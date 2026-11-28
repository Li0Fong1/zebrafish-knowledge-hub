# -*- coding: utf-8 -*-
"""用 NCBI Gene 的 gene_pubmed 取某个基因实体的参考文献，不用旧称做纯文字搜索。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{1,20}")
_STOP = {
    "the", "and", "for", "gene", "genes", "zebrafish", "danio", "rerio",
    "ncbi", "pubmed", "literature", "paper", "papers", "pmid", "what",
    "with", "from", "this", "that", "about", "which", "when", "where",
}
_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
_SHOW = 12

# 2026-11-06：热门基因（tp53 等）在 NCBI Gene 上关联上千个 PMID，旧实现把全部
# PMID 按 80 个一批串行请求 ESummary，实测单问 38s+，且每次提问都重来一遍。
# 最终答案只展示 _SHOW=12 篇，没必要给 1000 篇取元数据。这里做三件事：
#   ① 最多取 _ESUMMARY_MAX_PMIDS 个 PMID；② 单请求短超时 + 整段硬时间预算；
#   ③ 按 PMID 批次做磁盘缓存，同基因/同批次的后续提问秒级命中。
_ESUMMARY_MAX_PMIDS = int(os.getenv("GENE_BIB_ESUMMARY_MAX_PMIDS", "160"))
_ESUMMARY_BUDGET_SEC = float(os.getenv("GENE_BIB_ESUMMARY_BUDGET_SEC", "12"))
_NCBI_TIMEOUT_SEC = float(os.getenv("GENE_BIB_NCBI_TIMEOUT_SEC", "8"))
_ESUMMARY_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "esummary_cache"
_ESUMMARY_CACHE_TTL = 7 * 24 * 3600


def _rows(sql: str, params: dict | None = None, limit: int = 20) -> list:
    try:
        from mysql_client import get_mysql_client

        res = get_mysql_client().execute_sql(sql, max_rows=limit, params=params or {})
    except Exception:
        return []
    if not res.get("success"):
        return []
    return list(res.get("data") or [])


def _write(sql: str, params: dict | list | None = None) -> None:
    from sqlalchemy import text
    from mysql_client import get_mysql_client

    db = next(get_mysql_client().get_db())
    try:
        db.execute(text(sql), params or {})
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _ensure_cache() -> None:
    _write(
        """
        CREATE TABLE IF NOT EXISTS ncbi_gene_bibliography (
          ncbi_gene_id VARCHAR(16) NOT NULL,
          symbol VARCHAR(64) NOT NULL,
          pmid VARCHAR(16) NOT NULL,
          title VARCHAR(500) NULL,
          pubdate VARCHAR(40) NULL,
          sortpubdate VARCHAR(32) NULL,
          pmid_count INT NOT NULL,
          fetched_at DATETIME NOT NULL,
          PRIMARY KEY (ncbi_gene_id, pmid)
        )
        """
    )


def _ncbi_get(script: str, params: dict) -> dict:
    params = dict(params)
    params.setdefault("retmode", "json")
    params.setdefault("tool", "zebrafish_hub")
    url = _EUTILS + script + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "zebrafish_hub"})
    with urllib.request.urlopen(req, timeout=_NCBI_TIMEOUT_SEC) as handle:
        return json.loads(handle.read().decode("utf-8", errors="replace"))


def _esummary_cache_path(chunk: list[str]) -> Path:
    key = hashlib.md5(",".join(chunk).encode()).hexdigest()
    return _ESUMMARY_CACHE_DIR / f"{key}.json"


def _esummary_cached(chunk: list[str]) -> list[dict] | None:
    """命中且未过期直接返回；过期也保留作网络失败时的兜底。"""
    p = _esummary_cache_path(chunk)
    try:
        if not p.is_file():
            return None
        obj = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - float(obj.get("ts") or 0) > _ESUMMARY_CACHE_TTL:
            return None
        return list(obj.get("papers") or [])
    except Exception:
        return None


def _esummary_stale(chunk: list[str]) -> list[dict]:
    p = _esummary_cache_path(chunk)
    try:
        return list((json.loads(p.read_text(encoding="utf-8")) or {}).get("papers") or [])
    except Exception:
        return []


def _esummary_store(chunk: list[str], papers: list[dict]) -> None:
    try:
        _ESUMMARY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _esummary_cache_path(chunk)
        p.write_text(
            json.dumps({"ts": time.time(), "papers": papers}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _lookup_symbol(symbol: str) -> str:
    rows = _rows(
        "SELECT ncbi_gene_id FROM zebrafish_gene_v2_20260922.gene_card "
        "WHERE LOWER(symbol)=:s AND ncbi_gene_id IS NOT NULL AND ncbi_gene_id<>'' LIMIT 1",
        {"s": symbol.lower()},
        limit=1,
    )
    if rows and rows[0].get("ncbi_gene_id"):
        return str(rows[0]["ncbi_gene_id"]).split(".")[0]
    rows = _rows(
        "SELECT ax.ncbi_gene_id FROM gene_basic gb "
        "JOIN gene_assembly_xref ax ON ax.gene_id = gb.gene_id "
        "WHERE LOWER(gb.gene_name)=:s AND ax.ncbi_gene_id IS NOT NULL AND ax.ncbi_gene_id<>'' "
        "LIMIT 1",
        {"s": symbol.lower()},
        limit=1,
    )
    if rows and rows[0].get("ncbi_gene_id"):
        return str(rows[0]["ncbi_gene_id"]).split(".")[0]
    return ""


def _resolve_token(token: str) -> dict:
    """同一编号系统（此处为 NCBI Gene ID）编号相同时，多个符号视为同一基因。"""
    key = token.lower()
    alias_rows = _rows(
        "SELECT DISTINCT current_symbol FROM symbol_alias WHERE alias=:a LIMIT 8",
        {"a": key},
        limit=8,
    )
    symbols = []
    for row in alias_rows:
        sym = str(row.get("current_symbol") or "").strip()
        if sym and sym.lower() not in {item.lower() for item in symbols}:
            symbols.append(sym)
    direct = _lookup_symbol(key)
    if direct and key not in {item.lower() for item in symbols}:
        symbols.insert(0, token)
    if not symbols:
        if not direct:
            return {"status": "miss"}
        return {
            "status": "unique",
            "symbol": token,
            "ncbi_gene_id": direct,
            "query": token,
        }

    by_id: dict[str, list[str]] = {}
    for sym in symbols:
        gene_id = _lookup_symbol(sym)
        if not gene_id:
            continue
        by_id.setdefault(gene_id, []).append(sym)

    if not by_id:
        return {"status": "no_ncbi_gene_id", "symbol": symbols[0]}

    if len(by_id) > 1:
        # 不同 NCBI Gene ID：真歧义，请使用者指定符号
        names: list[str] = []
        for syms in by_id.values():
            for sym in syms:
                if sym.lower() not in {item.lower() for item in names}:
                    names.append(sym)
        return {"status": "ambiguous", "alias": token, "symbols": names}

    gene_id = next(iter(by_id))
    # 优先用别名表的现行符号；否则用查得到编号的第一个
    preferred = ""
    for row in alias_rows:
        sym = str(row.get("current_symbol") or "").strip()
        if sym and _lookup_symbol(sym) == gene_id:
            preferred = sym
            break
    if not preferred:
        preferred = by_id[gene_id][0]
    return {
        "status": "unique",
        "symbol": preferred,
        "ncbi_gene_id": gene_id,
        "query": token,
    }


def genes_in_question(question: str, limit: int = 2) -> dict:
    tokens = []
    seen = set()
    for token in _TOKEN_RE.findall(question or ""):
        key = token.lower()
        if key in seen or key in _STOP:
            continue
        seen.add(key)
        tokens.append(token)
        if len(tokens) >= 8:
            break
    genes = []
    ambiguous = []
    used_ids = set()
    for token in tokens:
        item = _resolve_token(token)
        if item.get("status") == "ambiguous":
            ambiguous.append(item)
            continue
        if item.get("status") != "unique":
            continue
        gene_id = str(item.get("ncbi_gene_id") or "")
        if not gene_id or gene_id in used_ids:
            continue
        used_ids.add(gene_id)
        genes.append(item)
        if len(genes) >= limit:
            break
    return {"genes": genes, "ambiguous": ambiguous}


def _cached(gene_id: str) -> list[dict]:
    rows = _rows(
        "SELECT symbol, pmid, title, pubdate, sortpubdate, pmid_count, fetched_at "
        "FROM ncbi_gene_bibliography WHERE ncbi_gene_id=:g "
        "ORDER BY sortpubdate DESC LIMIT 20",
        {"g": gene_id},
        limit=20,
    )
    if not rows:
        return []
    fetched = str(rows[0].get("fetched_at") or "")[:19]
    try:
        when = datetime.strptime(fetched, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return []
    if datetime.now(timezone.utc) - when > timedelta(days=14):
        return []
    return rows


def _store(symbol: str, gene_id: str, papers: list[dict], pmid_count: int) -> None:
    _ensure_cache()
    _write("DELETE FROM ncbi_gene_bibliography WHERE ncbi_gene_id=:g", {"g": gene_id})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    payload = [
        {
            "ncbi_gene_id": gene_id,
            "symbol": symbol[:64],
            "pmid": str(paper["pmid"])[:16],
            "title": (paper.get("title") or "")[:500],
            "pubdate": (paper.get("pubdate") or "")[:40],
            "sortpubdate": (paper.get("sortpubdate") or "")[:32],
            "pmid_count": int(pmid_count),
            "fetched_at": now,
        }
        for paper in papers
        if paper.get("pmid")
    ]
    if not payload:
        return
    _write(
        """
        INSERT INTO ncbi_gene_bibliography
        (ncbi_gene_id, symbol, pmid, title, pubdate, sortpubdate, pmid_count, fetched_at)
        VALUES
        (:ncbi_gene_id, :symbol, :pmid, :title, :pubdate, :sortpubdate, :pmid_count, :fetched_at)
        """,
        payload,
    )


_RANK_MEMO: dict[tuple[str, str], tuple[float, dict]] = {}


def _memo_get(gene_id: str, question: str) -> dict | None:
    item = _RANK_MEMO.get((gene_id, question.strip()))
    if not item:
        return None
    if time.time() - item[0] > 900:
        return None
    return item[1]


def _memo_put(gene_id: str, question: str, payload: dict) -> None:
    _RANK_MEMO[(gene_id, question.strip())] = (time.time(), payload)


def _store_links_table() -> None:
    _write(
        """
        CREATE TABLE IF NOT EXISTS ncbi_gene_link_cache (
          ncbi_gene_id VARCHAR(16) NOT NULL PRIMARY KEY,
          pmid_count INT NOT NULL,
          pmids MEDIUMTEXT NOT NULL,
          fetched_at DATETIME NOT NULL
        )
        """
    )


def _store_links(gene_id: str, ids: list[str]) -> None:
    _store_links_table()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _write("DELETE FROM ncbi_gene_link_cache WHERE ncbi_gene_id=:g", {"g": gene_id})
    _write(
        """
        INSERT INTO ncbi_gene_link_cache (ncbi_gene_id, pmid_count, pmids, fetched_at)
        VALUES (:g, :n, :p, :t)
        """,
        {"g": gene_id, "n": len(ids), "p": ",".join(ids), "t": now},
    )


def _cached_links(gene_id: str) -> list[str]:
    try:
        _store_links_table()
    except Exception:
        return []
    rows = _rows(
        "SELECT pmids, fetched_at FROM ncbi_gene_link_cache WHERE ncbi_gene_id=:g LIMIT 1",
        {"g": gene_id},
        limit=1,
    )
    if not rows:
        return []
    fetched = str(rows[0].get("fetched_at") or "")[:19]
    try:
        when = datetime.strptime(fetched, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return []
    if datetime.now(timezone.utc) - when > timedelta(days=14):
        return []
    return [item for item in str(rows[0].get("pmids") or "").split(",") if item.isdigit()]


def _link_ids(gene_id: str) -> list[str]:
    cached = _cached_links(gene_id)
    if cached:
        return cached
    seen: set[str] = set()
    out: list[str] = []
    for item in _elink(gene_id):
        pmid = str(item).strip()
        if not pmid.isdigit() or pmid in seen:
            continue
        seen.add(pmid)
        out.append(pmid)
    if out:
        _store_links(gene_id, out)
    return out


def _yes():
    """取已就绪的 PubmedYes 单例；未就绪时只触发后台预热，绝不同步加载。

    2026-11-06：旧实现在这里同步 get_pubmed_yes_retriever()，而本函数被
    bibliography_for_question() 在每次文献问答最开头调用 —— 170 个分片冷载
    实测 140.2s，直接把 /api/chat 拖成 524 超时。未就绪时返回 None 即可：
    _gene_bibliography 仍会用 NCBI Gene 的 gene_pubmed + ESummary 生成
    带标题/PMID 的参考文献块，回答不缺依据，且是秒级。
    """
    try:
        from rag.pubmed_yes_retriever import (
            ensure_pubmed_yes_retriever_async,
            peek_pubmed_yes_retriever,
        )
    except ImportError:
        return None
    yes = peek_pubmed_yes_retriever()
    if yes is None:
        try:
            ensure_pubmed_yes_retriever_async()
        except Exception:
            pass
        return None
    if not yes.ready:
        return None
    return yes


def _one_local_hit(yes, pmid: str) -> dict | None:
    found = yes.lookup_pmid(str(pmid), limit=4)
    if not found:
        return None
    pick = found[0]
    for item in found:
        if item.get("field") == "abstract":
            pick = item
            break
    hit = dict(pick)
    hit["match"] = "gene_pubmed"
    hit["pmid"] = str(hit.get("pmid") or pmid)
    return hit


def _rank_local(question: str, ids: list[str]) -> tuple[list[dict], str]:
    yes = _yes()
    if yes is None or not ids:
        return [], ""
    hits = []
    seen: set[str] = set()
    for pmid in ids:
        hit = _one_local_hit(yes, pmid)
        if hit is None or hit["pmid"] in seen:
            continue
        seen.add(hit["pmid"])
        hits.append(hit)
    if not hits:
        return [], ""
    try:
        from config import PUBMED_YES_RERANK_TOP_N, PUBMED_YES_RETRIEVE_CANDIDATES

        coarse_n = int(PUBMED_YES_RETRIEVE_CANDIDATES)
        fine_n = int(PUBMED_YES_RERANK_TOP_N)
    except Exception:
        coarse_n, fine_n = 500, 100
    try:
        from rag.rerank import hybrid_rerank_score, rerank_hits
    except ImportError:
        from rerank import hybrid_rerank_score, rerank_hits

    if len(hits) > coarse_n:
        hits = sorted(hits, key=lambda item: hybrid_rerank_score(question, item), reverse=True)[:coarse_n]
    ranked = rerank_hits(question, hits, top_n=fine_n)
    blended = yes.diversify_and_recency(
        ranked,
        question=question,
        unique_papers=max(fine_n, len(ranked)),
        max_chunks=max(fine_n, len(ranked)),
    )
    profile = str(blended[0].get("rank_profile") or "default") if blended else ""
    return list(blended or []), profile


def _rrs_tools():
    root = Path(__file__).resolve().parents[1]
    for extra in (root, root / "llm", root / "rag"):
        if extra.is_dir() and str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    from ref_relevance import extract_entities, score_paper

    return extract_entities, score_paper


_ZEBRA_HINT = ("zebrafish", "danio", "rerio", "larvae", "embryo", "embryonic", "morpholino")
# 明确的「别的物种/疾病模型」语境词：题名带这些、又不带任何斑马鱼线索时降权
_OTHER_SPECIES = ("patient", "cancer", "carcinoma", "tumor", "tumour", "oncogen",
                  "mice", "mouse", "murine", "human", "rat ", "porcine", "bovine",
                  "cell line", "hek293", "xenograft", "clinical")


def _species_adj(title: str) -> float:
    """题名的物种语境修正（只影响排序，不淘汰条目）。

    NCBI Gene 的关联文献是宽口径：查 zebrafish nanog 会带回
    「Targeting Cyclin D-CDK4/6 … SCP3-NANOG Axis」这类人类肿瘤论文。
    题名命中基因名，实体判据拦不住，但它显然不是这条斑马鱼问句的证据。
    这里给「别的物种语境 + 无斑马鱼线索」的条目一个小降权（-0.08，
    约等于半个位次档），让它沉到列表末尾而不是直接消失。
    """
    t = (title or "").lower()
    if not t:
        return 0.0
    if any(k in t for k in _ZEBRA_HINT):
        return 0.0
    if any(k in t for k in _OTHER_SPECIES):
        return -0.08
    return 0.0


def _rrs_order(question: str, papers: list[dict], rel_map: dict[str, float],
               entities: list | None = None) -> list[dict]:
    extract_entities, score_paper = _rrs_tools()
    # 基因文献场景下实体就是**这个基因**：由调用方显式传入（含 a/b 旁系同源简写），
    # 不再用正则从问句里猜 —— 猜出来的 "of"/"in"/"profile"/"functions" 会让
    # 「题名是否含该基因」这一判据形同虚设，导致实体阻塞从不触发。
    ents = list(entities) if entities is not None else extract_entities(question)
    pool = max(2, len(papers))

    def _score(paper: dict, n: int, rel):
        tag = f"PMID:{paper.get('pmid') or ''}"
        return score_paper(
            (n, paper.get("pubdate") or "", tag, paper.get("title") or "", "", paper.get("journal") or "", paper.get("doi") or ""),
            question=question,
            entities=ents,
            rel=rel,
            pool_size=pool,
        )

    first = []
    for paper in papers:
        rel = rel_map.get(paper["pmid"])
        scored = _score(paper, 1, rel)
        first.append((scored["score"], paper))
    first.sort(key=lambda item: item[0], reverse=True)
    second = []
    dropped = 0
    for index, (_old, paper) in enumerate(first, 1):
        rel = rel_map.get(paper["pmid"])
        scored = _score(paper, index, rel)
        # 问句点名了基因/ID，而这篇题名里根本没有它 → 它不可能是这句话的证据。
        # 之前这里不看 blocked_by_entity，只按分数取前 12 篇，于是「问 myl7 表达组织」
        # 会配上一串视网膜/黑色素瘤论文（NCBI Gene 的关联文献本就是宽口径）。
        # 宁缺毋滥：被 block 的直接不要，宁可只列 3 篇也不凑满 12 篇。
        if scored.get("blocked_by_entity"):
            dropped += 1
            continue
        row = dict(paper)
        adj = _species_adj(paper.get("title") or "")
        row["rrs"] = round(scored["score"] + adj, 6)
        row["rel_n"] = None if rel is None else float(rel)
        second.append((scored["score"] + adj, row))
    second.sort(key=lambda item: item[0], reverse=True)
    return [row for _score_value, row in second]


def _entity_forms(symbol: str) -> list[str]:
    """基因符号 → 题名匹配用的等价写法。

    斑马鱼旁系同源用 a/b 后缀，文献题名常写去掉后缀的写法（shha → Shh、
    mitfa → Mitf）。只对「末位是 a/b 且前一位不是数字」的符号去尾，避免
    bmp2b → bmp2 这种跨家族的误扩。
    """
    s = str(symbol or "").strip().lower()
    if not s:
        return []
    forms = [s]
    if len(s) > 3 and s[-1] in "ab" and not s[-2].isdigit():
        forms.append(s[:-1])
    return forms


# 别名里这些泛词不参与题名匹配：它们会命中大量与本基因无关的论文
# （例：myl7 的别名 "mhc" 会命中主要组织相容性复合体论文）。
_ALIAS_BLOCK = {
    "gene", "genes", "protein", "proteins", "factor", "factors", "receptor",
    "kinase", "domain", "like", "type", "class", "member", "family", "alpha",
    "beta", "gamma", "delta", "epsilon", "zeta", "omega", "isoform", "subunit",
    "putative", "unknown", "similar", "related", "regulatory", "regulator",
    "chain", "light", "heavy", "myosin", "actin", "binding", "motif",
}

# 别名形态的 ID 前缀（ZFIN/NCBI 内部编号，不能当题名匹配词）
_ALIAS_ID_RE = re.compile(
    r"^(?:zdb-|zgc:|wu:|fb\d|etid|ensdarg|ensdart|ensdarp|nm_|xm_|np_|xp_|otc|ch\d|si:)", re.I)

_ALIAS_CACHE: dict[str, list[str]] = {}


def _json_list_local(raw) -> list:
    """gene_name_bundle 的别名列是 JSON 字符串，解析失败时退回逗号切分。"""
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        val = json.loads(text)
        if isinstance(val, list):
            return [str(x) for x in val]
    except Exception:
        pass
    return [x.strip().strip('"') for x in text.strip("[]").split(",") if x.strip()]


def _alias_forms(symbol: str) -> list[str]:
    """取该基因在文献题名里可能出现的**历史名/别名**（myl7 → cmlc2）。

    为什么需要：NCBI Gene 关联的文献里有大量老论文用历史名写题名，
    例如 myl7 的 26 篇关联文献里题名全写 cmlc2 —— 只认当前符号会让
    实体判据把这些**真正的对口文献**全部淘汰（实测 kept=0）。

    收紧规则（宁可少放也不乱放）：
      · 丢掉 ID 形态（ZDB-GENE-… / zgc:… / wu:… / ENSDARG… / NM_…）
      · 丢掉长度 <4 且不含数字的短缩写（mhc/tel 这类跨领域撞车概率高）
      · 丢掉 _ALIAS_BLOCK 里的泛词
      · 全小写去重，最多 12 个（避免实体维被别名稀释）
    """
    key = str(symbol or "").strip().lower()
    if not key:
        return []
    if key in _ALIAS_CACHE:
        return _ALIAS_CACHE[key]
    rows = _rows(
        "SELECT aliases_ncbi, aliases_zfin, aliases_other FROM gene_name_bundle "
        "WHERE LOWER(primary_symbol)=:s OR LOWER(standard_symbol)=:s LIMIT 1",
        {"s": key},
        limit=1,
    )
    out: list[str] = []
    seen: set[str] = set()

    def _push(value: str) -> None:
        a = re.sub(r"\s+", " ", str(value or "")).strip().strip(",;").lower()
        if not a or a in seen:
            return
        if _ALIAS_ID_RE.match(a) or a.startswith("zdb-"):
            return
        if any(c.isdigit() for c in a) is False and len(a) < 4:
            return
        words = a.split(" ")
        if len(words) > 1 and (len(words) > 5 or any(len(w) < 3 for w in words)):
            return
        if a in _ALIAS_BLOCK or (len(words) == 1 and a in _ALIAS_BLOCK):
            return
        seen.add(a)
        out.append(a)

    for row in rows:
        for field in ("aliases_ncbi", "aliases_zfin", "aliases_other"):
            for a in _json_list_local(row.get(field)):
                _push(a)
                if len(out) >= 12:
                    break
            if len(out) >= 12:
                break
    if len(_ALIAS_CACHE) > 500:
        _ALIAS_CACHE.clear()
    _ALIAS_CACHE[key] = out
    return out


def bibliography_for_gene(symbol: str, gene_id: str, question: str = "") -> dict:
    memo = _memo_get(gene_id, question) if question else None
    if memo:
        return memo
    ids = _link_ids(gene_id)
    ask = question or symbol
    try:
        blended, profile = _rank_local(ask, ids) if ids else ([], "")
    except Exception:
        blended, profile = [], ""
    rel_map = {}
    blend_map = {}
    for hit in blended:
        pmid = str(hit.get("pmid") or "")
        if not pmid:
            continue
        if hit.get("rel_n") is not None:
            rel_map[pmid] = float(hit["rel_n"])
        if hit.get("blend_score") is not None:
            blend_map[pmid] = float(hit["blend_score"])
    metas = _esummary(ids) if ids else []
    if metas:
        # 实体判据 = 当前符号（含 a/b 去尾）+ 该基因的历史名/别名。
        # 只认当前符号会把「题名写 cmlc2 的老论文」当成无关文献全部淘汰（myl7 kept=0）。
        entities = list(dict.fromkeys(_entity_forms(symbol) + _alias_forms(symbol)))
        ordered = _rrs_order(ask, metas, rel_map, entities=entities)
        papers = []
        for paper in ordered[:_SHOW]:
            pmid = paper["pmid"]
            papers.append({
                "pmid": pmid,
                "title": paper.get("title") or "",
                "pubdate": paper.get("pubdate") or "",
                "rel_n": paper.get("rel_n"),
                "blend_score": blend_map.get(pmid),
                "rrs": paper.get("rrs"),
            })
        payload = {
            "symbol": symbol,
            "ncbi_gene_id": gene_id,
            "pmid_count": len(ids),
            "papers": papers,
            "source": "gene_pubmed_rrs" if papers else "gene_pubmed_no_relevant",
            "rank_profile": profile or "default",
            "ranked_hits": blended,
            # gene_pool 的语义是「手上真有一批能用的、确实是这个基因的文献」。
            # 之前只要 ESummary 取到元数据就算 True，于是 myl7 这种「NCBI 关联文献
            # 全是顺带提及、题名无一命中」的基因会被判成有证据池，把下游
            # PubMed 主题检索兜底一起挡掉，最后既没有基因文献也没有主题文献。
            "gene_pool": bool(papers),
        }
    else:
        payload = {
            "symbol": symbol,
            "ncbi_gene_id": gene_id,
            "pmid_count": len(ids),
            "papers": [],
            "source": "gene_pubmed_ends",
            "ranked_hits": [],
            "gene_pool": False,
        }
    if question:
        _memo_put(gene_id, question, payload)
    return payload


def _elink(gene_id: str) -> list[str]:
    data = _ncbi_get("elink.fcgi", {
        "dbfrom": "gene",
        "db": "pubmed",
        "linkname": "gene_pubmed",
        "id": gene_id,
    })
    ids = []
    for linkset in data.get("linksets") or []:
        for block in linkset.get("linksetdbs") or []:
            if block.get("linkname") != "gene_pubmed":
                continue
            for item in block.get("links") or []:
                ids.append(str(item))
    return ids


def _esummary(pmids: list[str]) -> list[dict]:
    ids = [str(p) for p in (pmids or []) if str(p).isdigit()][:_ESUMMARY_MAX_PMIDS]
    deadline = time.time() + _ESUMMARY_BUDGET_SEC
    papers: list[dict] = []
    for start in range(0, len(ids), 80):
        if time.time() >= deadline:
            break
        chunk = ids[start:start + 80]
        cached = _esummary_cached(chunk)
        if cached is not None:
            papers.extend(cached)
            continue
        try:
            data = _ncbi_get("esummary.fcgi", {"db": "pubmed", "id": ",".join(chunk)})
        except Exception:
            # 网络抖动不能把整问拖死：有旧缓存就用旧的，否则跳过这一批
            papers.extend(_esummary_stale(chunk))
            continue
        result = data.get("result") or {}
        batch: list[dict] = []
        for uid in result.get("uids") or chunk:
            row = result.get(str(uid)) or {}
            if not isinstance(row, dict):
                continue
            doi = ""
            for art in row.get("articleids") or []:
                if not isinstance(art, dict):
                    continue
                if str(art.get("idtype") or "").lower() == "doi":
                    doi = str(art.get("value") or "")
                    break
            batch.append({
                "pmid": str(uid),
                "title": str(row.get("title") or "").replace("\n", " ").strip(),
                "pubdate": str(row.get("pubdate") or ""),
                "sortpubdate": str(row.get("sortpubdate") or ""),
                "journal": str(row.get("fulljournalname") or row.get("source") or ""),
                "doi": doi,
            })
        if batch:
            _esummary_store(chunk, batch)
            papers.extend(batch)
    papers.sort(key=lambda item: item.get("sortpubdate") or "", reverse=True)
    return papers


def _answer_en(question: str = "") -> bool:
    try:
        from zebrafish_prompt import answer_is_english

        return bool(answer_is_english(question))
    except Exception:
        return False


def _paper_year(pubdate) -> str:
    """从 PubMed 的 pubdate（'2022 Mar 18' / '2022 Aug' / '2022'）里取年份。"""
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", str(pubdate or ""))
    return m.group(1) if m else ""


def _paper_line(paper: dict) -> str:
    """一条文献渲染成 markdown 列表项：标题本身是 PubMed 链接，年份跟在后面。

    2026-10-31 用户反馈：原来写成「- PMID 35304872（2022 Mar 18） rrs=0.828 标题」，
    PMID 号、内部打分 rrs 都是后台的东西，用户不该看到，而且整行不可点。
    现在只留标题 + 年份，PMID 藏进链接地址。
    """
    title = str(paper.get("title") or "").strip().rstrip(".")
    pmid = str(paper.get("pmid") or "").strip()
    if not title:
        return ""
    # 方括号会破坏 markdown 链接语法，换成圆括号（标题里极少见，不影响阅读）
    label = title.replace("[", "(").replace("]", ")")
    year = _paper_year(paper.get("pubdate"))
    suffix = f" · {year}" if year else ""
    if not pmid.isdigit():
        return f"- {label}{suffix}"
    return f"- [{label}](https://pubmed.ncbi.nlm.nih.gov/{pmid}/){suffix}"


def _block(found: dict, question: str = "") -> str:
    en = _answer_en(question)
    lines: list[str] = []
    for item in found.get("ambiguous") or []:
        names = (", ".join if en else "、".join)(item.get("symbols") or [])
        if en:
            lines.append(
                f"{item.get('alias')} maps to {names}. "
                "Bibliography was not taken from only one of them. Please specify one symbol."
            )
        else:
            lines.append(
                f"{item.get('alias')} 同时对到 {names}。没有改抓其中一个基因的 Bibliography。请指定其中一个符号。"
            )
    for gene in found.get("genes") or []:
        symbol = str(gene.get("symbol") or "").strip()
        papers = gene.get("papers") or []
        total = gene.get("pmid_count") or 0
        body = [ln for ln in (_paper_line(p) for p in papers) if ln]
        if not body:
            continue
        if en:
            head = f"**{symbol} references**"
            if total:
                head += (f" — {total} papers linked to this gene in NCBI Gene, "
                         f"{len(body)} shown below.")
        else:
            head = f"**{symbol} 相关文献**"
            if total:
                head += f"（NCBI Gene 上关联 {total} 篇，以下列出 {len(body)} 篇）"
        lines.append(head)
        lines.extend(body)
        gene_url = f"https://www.ncbi.nlm.nih.gov/gene/{gene.get('ncbi_gene_id')}"
        if str(gene.get("ncbi_gene_id") or "").isdigit():
            lines.append(
                f"[{'All papers on the NCBI Gene page' if en else '在 NCBI Gene 页查看全部'}]"
                f"({gene_url})"
            )
    return "\n".join(lines)


def bibliography_for_question(question: str) -> dict:
    found = genes_in_question(question)
    genes = []
    for item in found["genes"]:
        try:
            genes.append(bibliography_for_gene(item["symbol"], item["ncbi_gene_id"], question))
        except Exception:
            continue
    payload = {"genes": genes, "ambiguous": found["ambiguous"]}
    pmids = []
    for gene in genes:
        for paper in gene["papers"]:
            if paper["pmid"] not in pmids:
                pmids.append(paper["pmid"])
    payload["pmids"] = pmids
    ranked_hits = []
    for gene in genes:
        for hit in gene.get("ranked_hits") or []:
            ranked_hits.append(hit)
    payload["ranked_hits"] = ranked_hits
    payload["gene_pool"] = any(gene.get("gene_pool") for gene in genes)
    payload["answer_block"] = _block(payload, question)
    return payload
