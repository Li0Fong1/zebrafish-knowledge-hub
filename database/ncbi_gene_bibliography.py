# -*- coding: utf-8 -*-
"""用 NCBI Gene 的 gene_pubmed 取某个基因实体的参考文献，不用旧称做纯文字搜索。"""
from __future__ import annotations

import json
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
    with urllib.request.urlopen(req, timeout=25) as handle:
        return json.loads(handle.read().decode("utf-8", errors="replace"))


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
    try:
        from rag.pubmed_yes_retriever import (
            get_pubmed_yes_retriever,
            peek_pubmed_yes_retriever,
            pubmed_yes_loading,
        )
    except ImportError:
        return None
    yes = peek_pubmed_yes_retriever()
    if yes is None and not pubmed_yes_loading():
        try:
            yes = get_pubmed_yes_retriever()
        except Exception:
            return None
    if yes is None or not yes.ready:
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


def _rrs_order(question: str, papers: list[dict], rel_map: dict[str, float]) -> list[dict]:
    extract_entities, score_paper = _rrs_tools()
    entities = extract_entities(question)
    pool = max(2, len(papers))

    def _score(paper: dict, n: int, rel):
        tag = f"PMID:{paper.get('pmid') or ''}"
        return score_paper(
            (n, paper.get("pubdate") or "", tag, paper.get("title") or "", "", paper.get("journal") or "", paper.get("doi") or ""),
            question=question,
            entities=entities,
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
    for index, (_old, paper) in enumerate(first, 1):
        rel = rel_map.get(paper["pmid"])
        scored = _score(paper, index, rel)
        row = dict(paper)
        row["rrs"] = scored["score"]
        row["rel_n"] = None if rel is None else float(rel)
        second.append((scored["score"], row))
    second.sort(key=lambda item: item[0], reverse=True)
    return [row for _score_value, row in second]


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
        ordered = _rrs_order(ask, metas, rel_map)
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
            "source": "gene_pubmed_rrs",
            "rank_profile": profile or "default",
            "ranked_hits": blended,
            "gene_pool": True,
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
    papers = []
    for start in range(0, len(pmids), 80):
        chunk = pmids[start:start + 80]
        data = _ncbi_get("esummary.fcgi", {"db": "pubmed", "id": ",".join(chunk)})
        result = data.get("result") or {}
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
            papers.append({
                "pmid": str(uid),
                "title": str(row.get("title") or "").replace("\n", " ").strip(),
                "pubdate": str(row.get("pubdate") or ""),
                "sortpubdate": str(row.get("sortpubdate") or ""),
                "journal": str(row.get("fulljournalname") or row.get("source") or ""),
                "doi": doi,
            })
    papers.sort(key=lambda item: item.get("sortpubdate") or "", reverse=True)
    return papers


def _answer_en(question: str = "") -> bool:
    try:
        from zebrafish_prompt import answer_is_english

        return bool(answer_is_english(question))
    except Exception:
        return False


def _block(found: dict, question: str = "") -> str:
    en = _answer_en(question)
    if en:
        lines = [
            "NCBI Gene bibliography (gene_pubmed). These PMIDs are attached to the gene entity; "
            "this is not a free-text search on old aliases."
        ]
    else:
        lines = [
            "NCBI Gene bibliography（gene_pubmed）。这些 PMID 挂在基因实体上，不是用旧称做纯文字搜索。"
        ]
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
        if gene.get("source") == "gene_pubmed_rrs":
            if en:
                lines.append(
                    f"{gene['symbol']} has NCBI Gene ID {gene['ncbi_gene_id']}; "
                    f"gene_pubmed lists {gene['pmid_count']} papers. "
                    f"The {len(gene['papers'])} papers below are selected only from those links, "
                    "ordered by RRS (Reference Relevance Score)."
                )
            else:
                lines.append(
                    f"{gene['symbol']} 的 NCBI Gene ID 是 {gene['ncbi_gene_id']}，"
                    f"gene_pubmed 共 {gene['pmid_count']} 篇。"
                    f"下面 {len(gene['papers'])} 篇只从这些链接里选出，"
                    f"顺序是 RRS（Reference Relevance Score，引用相关性分数）。"
                )
        else:
            if en:
                lines.append(
                    f"{gene['symbol']} has NCBI Gene ID {gene['ncbi_gene_id']}; "
                    f"gene_pubmed lists {gene['pmid_count']} papers. "
                    "Below are a few older and a few newer entries."
                )
            else:
                lines.append(
                    f"{gene['symbol']} 的 NCBI Gene ID 是 {gene['ncbi_gene_id']}，"
                    f"gene_pubmed 共 {gene['pmid_count']} 篇。下面列出较早和较新的各数篇。"
                )
        for paper in gene["papers"]:
            if en:
                date = f" ({paper['pubdate']})" if paper.get("pubdate") else ""
            else:
                date = f"（{paper['pubdate']}）" if paper.get("pubdate") else ""
            score = ""
            if paper.get("rel_n") is not None:
                score += f" rel={float(paper['rel_n']):.3f}"
            if paper.get("rrs") is not None:
                score += f" rrs={float(paper['rrs']):.3f}"
            lines.append(f"- PMID {paper['pmid']}{date}{score} {paper.get('title') or ''}".rstrip())
        if en:
            lines.append(f"Gene page: https://www.ncbi.nlm.nih.gov/gene/{gene['ncbi_gene_id']}")
        else:
            lines.append(f"基因页：https://www.ncbi.nlm.nih.gov/gene/{gene['ncbi_gene_id']}")
    return "\n".join(lines) if len(lines) > 1 else ""


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
