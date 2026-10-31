# -*- coding: utf-8 -*-
"""
Potato Knowledge Hub 风格多 Agent 编排。

 specialist 并行采集 → 主任务回答 → 自动工具联动 → 综合 Agent 补全可追溯摘要。
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from common_utils import logger

AGENT_LABELS = {
    "router": "路由 Agent",
    "id_mapper": "ID 映射 Agent",
    "gene_analyst": "基因分析 Agent",
    "literature": "文献 RAG Agent",
    "enrichment": "富集分析 Agent",
    "database": "数据库 Agent",
    "synthesizer": "综合 Agent",
}

# 前端「回答范围」：只跑选定链路，减轻 16GB 机并行压力
ANSWER_SCOPES = {
    "hub": "Hub 全链路",
    "literature": "仅文献 RAG",
    "gene_db": "仅基因/数据库",
    "id_map": "仅 ID 映射",
    "retrieval_debug": "检索调试摘要",
}
DEFAULT_ANSWER_SCOPE = "hub"  # 公网 Hub 默认：Hub 全链路

# 使用策略：部分回答（快）/ 整体回答（全）
ANSWER_DEPTHS = {
    "partial": "部分回答",
    "full": "整体回答",
}
DEFAULT_ANSWER_DEPTH = "full"  # 公网 Hub 默认：完整/全部回答（不限字数）

_FOLLOWUP_MARKERS = (
    "它", "牠", "该基因", "这个基因", "这个", "上面", "上述", "前述", "刚才", "刚才的",
    "再", "还有", "哪篇", "哪些里", "补充", "继续", "同样", "同一", "此外", "另外",
    "那篇", "这些", "那些", "上文", "前述文献", "相关的",
    "参考文献", "一篇", "几篇", "只有", "脚注", "引用",
)


def _normalize_history(history: Optional[List[Any]], *, max_turns: int = 8) -> List[Dict[str, str]]:
    """规范化前端传来的多轮历史：[{role, content}, ...]。"""
    out: List[Dict[str, str]] = []
    if not history:
        return out
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or item.get("text") or "").strip()
        if role in ("assistant", "bot", "ai"):
            role = "assistant"
        elif role in ("user", "human"):
            role = "user"
        else:
            continue
        if not content:
            continue
        # 去掉前端展示用的范围前缀
        if content.startswith("【") and "】" in content[:40]:
            content = content.split("】", 1)[-1].strip() or content
        out.append({"role": role, "content": content[:2500]})
    return out[-max_turns:]


def _history_blob(history: List[Dict[str, str]]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in history)


_ANAPHORA = (
    "它", "牠", "该基因", "这个基因", "这个", "上面", "上述", "前述", "刚才",
    "哪篇", "哪些里", "这些", "那些", "上文", "前述文献",
)


def _is_complete_new_topic(question: str) -> bool:
    """完整新问句，不是「它 / 上面」这类追问。"""
    q = (question or "").strip()
    if not q or any(m in q for m in _ANAPHORA):
        return False
    if any(k in q for k in (
        "文献统计", "论文统计", "多少篇", "多少文献", "文献有多少", "文献数量",
        "全库", "多少基因",
    )):
        return True
    if "统计" in q and any(k in q for k in ("文献", "论文", "数据库", "基因库", "收录")):
        return True
    if "斑马鱼" in q and len(q) >= 6:
        return True
    return False


def is_corpus_stats_question(question: str) -> bool:
    """问文献库/基因库有多少，不是在问某一个基因。"""
    q = (question or "").strip()
    if not q or any(m in q for m in _ANAPHORA):
        return False
    try:
        from gene_toolkit import extract_genes_from_question

        if extract_genes_from_question(q, limit=1):
            return False
    except Exception:
        pass
    if any(k in q for k in (
        "文献统计", "论文统计", "多少篇", "多少文献", "文献有多少", "文献数量",
    )):
        return True
    return "统计" in q and any(k in q for k in ("文献", "论文", "数据库", "基因库", "全库", "收录"))


def _looks_like_followup(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    try:
        from gene_formatter import (
            is_biology_mechanism_question,
            is_citation_display_question,
            is_standalone_science_question,
        )

        if is_citation_display_question(q):
            return True
        if is_biology_mechanism_question(q) or is_standalone_science_question(q):
            return False
    except Exception:
        pass
    if _is_complete_new_topic(q):
        return False
    # 只有指代才算追问。「再 / 还有 / 引用 / 几篇」会出现在全新问题里，不能单独触发。
    if any(m in q for m in _ANAPHORA):
        return True
    if len(q) <= 8 and not re.search(r"[A-Za-z]{2,}|ZDB-GENE|ENS[A-Z]|\d{5,}", q):
        if any(k in q for k in ("怎么", "哪些", "什么", "为何", "为什么", "讲讲", "介绍")):
            return False
        return True
    return False


def expand_question_with_history(
    question: str,
    history: Optional[List[Any]] = None,
) -> tuple[str, List[Dict[str, str]], bool]:
    """
    智能对话上下文管理：
    - 新问题与上文「关联」（指代/同一基因/短追问）→ 合并上下文一起分析；
    - 新问题与上文「不关联」（全新基因的完整新话题）→ 清空历史独立回答，互不污染。
    返回 (effective_question, normalized_history, expanded)。
    """
    from gene_toolkit import extract_genes_from_question

    q = (question or "").strip()
    if not q:
        return q, [], False
    hist = _normalize_history(history)
    if not hist:
        return q, hist, False

    # ---- 关联判定 ----
    # 1) 当前问句明确含指代词/追问标记 → 强关联
    strong_followup = _looks_like_followup(q)

    # 2) 只认上一句用户问里的基因。更早的 p53 不能跨过后来的新话题再粘回来。
    cur_genes = [g for g in extract_genes_from_question(q, limit=6)]
    last_user = next((m["content"] for m in reversed(hist) if m["role"] == "user"), "")
    hist_genes: List[str] = []
    seen = set()
    _noise = {
        "user", "assistant", "pmid", "http", "https", "danio", "rerio", "zebrafish",
        "gene", "genes", "stress", "mitochondrial", "expression", "tissue", "the",
        "and", "for", "with", "from", "this", "that", "in", "on", "of", "to", "is",
        "are", "was", "were", "related", "literature", "paper", "abstract",
    }
    for g in extract_genes_from_question(last_user, limit=4):
        gl = g.lower()
        if gl in seen or gl in _noise or len(gl) < 2:
            continue
        seen.add(gl)
        hist_genes.append(g)

    cur_set = {g.lower() for g in cur_genes}
    hist_set = {g.lower() for g in hist_genes}
    same_gene = bool(cur_set & hist_set)

    # 3) 关联判定结论
    # 无基因的短句 / 指代 / 与上文同基因 → 关联
    try:
        from gene_formatter import is_standalone_science_question, is_citation_display_question

        standalone = is_standalone_science_question(q)
        citation_ui = is_citation_display_question(q)
    except Exception:
        standalone = False
        citation_ui = False
    if citation_ui:
        related = True
    elif standalone:
        related = False
    elif strong_followup:
        related = True
    elif same_gene:
        related = True
    elif cur_genes and hist_genes and not same_gene:
        # 当前提出全新基因的完整问句 → 新话题，独立回答
        related = False
    else:
        # 默认：完整问句视为新话题，不强行关联
        related = False

    if not related:
        # 新话题：清空历史，独立回答，避免旧问题污染
        return q, [], False

    # ---- 关联：合并最近相关上下文 ----
    blob = _history_blob(hist)
    pmids = re.findall(r"PMID[:\s]*(\d{5,9})", blob, flags=re.I)
    last_asst = next((m["content"] for m in reversed(hist) if m["role"] == "assistant"), "")

    # 只保留最近 2 轮作为上下文（够用且避免串扰）
    recent = _normalize_history(hist, max_turns=4)

    ctx_bits: List[str] = []
    if hist_genes:
        ctx_bits.append("上文基因：" + "、".join(hist_genes[:4]))
    if pmids:
        ctx_bits.append("上文PMID：" + ", ".join(pmids[:5]))
    if last_user:
        ctx_bits.append("上一问：" + last_user[:180])
    # 文献追问才带上一答。普通新问题里的「文献」二字不能把上一答整段贴进来。
    if last_asst and (
        any(m in q for m in _ANAPHORA)
        or any(k in q for k in ("一篇", "几篇", "只有", "脚注", "引用", "参考文献"))
    ):
        ctx_bits.append("上一答摘要：" + last_asst[:800])

    if not ctx_bits:
        return q, recent, True

    if hist_genes:
        # 用最近一次出现的基因，不用更早一轮里的旧基因
        effective = f"关于基因 {hist_genes[-1]}：{q}\n（对话上下文：{'；'.join(ctx_bits)}）"
    else:
        effective = f"{q}\n（对话上下文：{'；'.join(ctx_bits)}）"
    return effective, recent, True


def normalize_answer_scope(scope: Optional[str]) -> str:
    s = (scope or "").strip().lower()
    if s in ("", "auto", "default"):
        return DEFAULT_ANSWER_SCOPE
    if s in ANSWER_SCOPES:
        return s
    aliases = {
        "rag": "literature",
        "lit": "literature",
        "pubmed": "literature",
        "gene": "gene_db",
        "db": "gene_db",
        "id": "id_map",
        "idmap": "id_map",
        "id_mapping": "id_map",
        "mapping": "id_map",
        "full": "hub",
        "debug": "retrieval_debug",
        "retrieval": "retrieval_debug",
    }
    return aliases.get(s, DEFAULT_ANSWER_SCOPE)


def normalize_answer_depth(depth: Optional[str]) -> str:
    d = (depth or "").strip().lower()
    if d in ("", "auto", "default"):
        return DEFAULT_ANSWER_DEPTH
    if d in ANSWER_DEPTHS:
        return d
    aliases = {
        "部分": "partial",
        "部分回答": "partial",
        "fast": "partial",
        "quick": "partial",
        "strategy_partial": "partial",
        "整体": "full",
        "整体回答": "full",
        "complete": "full",
        "comprehensive": "full",
        "slow": "full",
        "strategy_full": "full",
        "hub_full": "full",
    }
    return aliases.get(d, DEFAULT_ANSWER_DEPTH)


def _trace_step(
    agent_id: str,
    status: str,
    summary: str,
    *,
    ms: int = 0,
    detail: Optional[dict] = None,
) -> dict:
    return {
        "agent": agent_id,
        "label": AGENT_LABELS.get(agent_id, agent_id),
        "status": status,
        "summary": summary,
        "ms": ms,
        "detail": detail or {},
    }


def _looks_trait_or_strain(question: str) -> bool:
    q = question or ""
    ql = q.lower()
    if re.search(r"(?<![A-Za-z])(AB|TU|WIK)(?![A-Za-z])", q):
        return True
    return any(
        k in ql
        for k in (
            "品系", "外形", "性状", "体色", "鳍长", "体型", "密度", "温度",
            "反荫蔽", "脊柱", "鳞片",
        )
    )


def _plan_specialists(question: str, task_type: str, *, depth: str = "full") -> List[str]:
    q = (question or "").lower()
    depth = normalize_answer_depth(depth)
    try:
        from gene_formatter import is_biology_mechanism_question
        mech = is_biology_mechanism_question(question)
    except Exception:
        mech = False

    # 部分回答：少 specialist，跳过富集；按路由只跑主路径 + 必要一项
    if depth == "partial":
        agents: List[str] = []
        try:
            from zebrafish_prompt import is_hub_product_howto_question

            if is_hub_product_howto_question(question):
                return []
        except Exception:
            pass
        # 品系/外形性状题绝不能只走基因卡（AB/TU 会被误当成基因符号）
        if mech or _looks_trait_or_strain(question):
            agents = ["literature"]
        elif task_type in ("数据库查询",):
            agents = ["database"]
        elif task_type in ("基因详情",):
            agents = ["gene_analyst"]
        elif any(k in q for k in ("富集", "kegg", "go富集", "通路")):
            agents = ["gene_analyst"]  # 部分模式不做 ORA，仅基因摘要
        else:
            # 默认文献为主（候选→CE→按篇去重编号）
            agents = ["literature"]
        # ID 类问题才加映射
        if (not mech) and any(k in q for k in ("ensembl", "zfin", "zdb-gene", "对应", "别名", "id", "ncbi")):
            if "id_mapper" not in agents:
                agents.insert(0, "id_mapper")
        return agents

    try:
        from zebrafish_prompt import is_hub_product_howto_question

        if is_hub_product_howto_question(question):
            return []
    except Exception:
        pass

    if mech:
        agents = ["literature"]
        try:
            from gene_toolkit import extract_genes_from_question
            if extract_genes_from_question(question, limit=1):
                agents = ["gene_analyst", "literature"]
        except Exception:
            pass
        return agents

    agents = ["id_mapper", "gene_analyst", "literature"]
    try:
        from gene_toolkit import extract_genes_from_question
        genes = extract_genes_from_question(question, limit=3)
    except Exception:
        genes = []
    if genes or any(k in q for k in ("富集", "kegg", "go富集", "通路")):
        agents.append("enrichment")
    if task_type in ("数据库查询",):
        agents.append("database")
    if _looks_trait_or_strain(question) and "literature" not in agents:
        agents.append("literature")
    seen = set()
    out: List[str] = []
    for a in agents:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def _id_mapper_skip(query: str, note: str) -> dict:
    return {
        "query": (query or "")[:80],
        "resolved": False,
        "notes": [note],
    }


def _run_id_mapper(question: str) -> dict:
    from gene_id_mapper import resolve_gene_identifier
    from gene_toolkit import (
        extract_genes_from_question,
        _is_gene_stopword,
        _gene_token_pattern,
        is_digit_leading_gene_token,
    )
    from gene_formatter import (
        is_biology_mechanism_question,
        is_citation_display_question,
        is_complaint_followup,
        _strip_dialog_context,
    )
    import re

    clean = _strip_dialog_context(question)
    q = (clean or question or "").strip()
    if is_biology_mechanism_question(q):
        return _id_mapper_skip(q, "本题是分子机制问题，不是基因符号，已跳过 ID 映射")
    if is_complaint_followup(q) or is_citation_display_question(q):
        return _id_mapper_skip(q, "本题不是基因符号或数据库 ID，已跳过 ID 映射")
    # 整句中文（无明确 ID）禁止当基因符号
    has_id = bool(re.search(
        r"ZDB-GENE-\d+-\d+|ENS[A-Z]+\d+|(?:NM|NR|NP|XM|XR|NC)_\d+",
        q,
        re.I,
    ))
    cjk_n = len(re.findall(r"[\u4e00-\u9fff]", q))
    latin = extract_genes_from_question(q, limit=1)
    if cjk_n >= 4 and not has_id and not latin:
        return _id_mapper_skip(q, "问句中未识别到基因符号或数据库 ID，已跳过 ID 映射")

    token = q
    for pat in (
        r"ZDB-GENE-\d+-\d+",
        r"ENS[A-Z]+\d+(?:\.\d+)?",
        r"(?:NM|NR|NP|XM|XR|NC)_\d+(?:\.\d+)?",
        r"\b\d{4,9}\b",
    ):
        m = re.search(pat, q, re.I)
        if m:
            token = m.group(0)
            break
    if latin and (
        is_digit_leading_gene_token(latin[0])
        or not re.search(r"ZDB-GENE|ENS[A-Z]|\d{5,}", token, re.I)
    ):
        token = latin[0]
    elif token == q or len(token) > 40:
        m = re.search(_gene_token_pattern(), q)
        cand = m.group(1) if m else ""
        if cand and not _is_gene_stopword(cand):
            token = cand
        else:
            return _id_mapper_skip(q, "问句中未识别到基因符号或数据库 ID，已跳过 ID 映射")
    if re.search(r"[\u4e00-\u9fff]", token):
        return _id_mapper_skip(q, "问句中未识别到基因符号或数据库 ID，已跳过 ID 映射")
    return resolve_gene_identifier(token)


def _question_for_gene_extract(question: str) -> str:
    """去掉对话上下文块，避免「上文基因：p53」被当成这一问的基因。保留「关于基因 X」前缀。"""
    return re.sub(r"（对话上下文：.*?）", "", question or "", flags=re.S).strip()


def _run_gene_analyst(question: str) -> dict:
    from gene_toolkit import extract_genes_from_question, get_gene_detail, normalize_gene_name

    genes = extract_genes_from_question(_question_for_gene_extract(question), limit=2)
    if not genes:
        return {"skipped": True}
    name = normalize_gene_name(genes[0])
    detail = get_gene_detail(name)
    return {"gene": name, "detail": detail, "success": bool(detail)}


def _lit_pack_chars(_depth: str = "full") -> int:
    """精细 100 篇进大模型的打包预算；部分/完整回答都给够，避免依据被裁短。"""
    try:
        from config import PUBMED_YES_PACK_CHARS

        return max(24000, int(PUBMED_YES_PACK_CHARS))
    except Exception:
        return 48000


def _run_literature(
    question: str,
    max_chars: int | None = None,
    *,
    retrieve_candidates: int | None = None,
    expand: bool | None = None,
) -> dict:
    """文献检索：优先 PubMed YES 分片库，其次 Hub 小库 faiss_index。"""
    from full_rag_chat import truncate_context

    excerpt = ""
    source = "none"
    pmids: list[str] = []
    pubmed_error = ""
    budget = max(8000, int(max_chars) if max_chars is not None else _lit_pack_chars())
    bib_block = ""
    gene_hits: list[dict] = []
    gene_pool = False
    try:
        from ncbi_gene_bibliography import bibliography_for_question

        bib = bibliography_for_question(question)
        bib_block = bib.get("answer_block") or ""
        gene_hits = list(bib.get("ranked_hits") or [])
        gene_pool = bool(bib.get("gene_pool"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Hub] NCBI Gene bibliography 失败：{e}")
        gene_pool = False

    try:
        from rag.pubmed_yes_retriever import (
            get_pubmed_yes_retriever,
            peek_pubmed_yes_retriever,
            pubmed_yes_loading,
        )

        # 预热/首载进行中时不在对话锁内阻塞等 35 shard，回退 Hub 小库
        yes = peek_pubmed_yes_retriever()
        if yes is None and not pubmed_yes_loading():
            yes = get_pubmed_yes_retriever()
        if gene_hits and yes is not None and yes.ready:
            # 候选已是 gene_pubmed。沿用精排后的 blend 顺序打包，不再做全库语义召回。
            ctx = yes.get_context(question, top_k=None, max_chars=budget, hits=gene_hits)
            excerpt = truncate_context(ctx, max_chars=budget)
            seen: set[str] = set()
            for hit in gene_hits:
                pmid = str(hit.get("pmid") or "").strip()
                if pmid and pmid not in seen:
                    seen.add(pmid)
                    pmids.append(pmid)
            if excerpt and len(excerpt.strip()) > 40:
                source = "gene_pubmed_blend"
        elif gene_pool:
            logger.info("[Hub] 基因文献已用 gene_pubmed 候选，不再做全库语义召回")
        elif yes is not None and yes.ready:
            # 只 search 一次：hits 同时用于 PMID 列表与上下文打包，避免双倍 FAISS+CE
            hits = yes.search(
                question,
                top_k=None,
                expand=expand,
                candidates=retrieve_candidates,
            )
            # 任务①：别名/历史名类问题 → 用题名命中最多的历史别名再检索，合并进同一批 hits，
            # 这样别名对应的文献也进入编号池，正文可正常引用（最多 2 个别名，控制时延）。
            try:
                from gene_alias_evidence import extra_alias_queries, is_alias_question

                if is_alias_question(question):
                    seen_keys = {
                        str(h.get("pmid") or h.get("entry_no") or h.get("doc_id") or "") for h in hits
                    }
                    added = 0
                    for q2 in extra_alias_queries(question, limit=2):
                        more = yes.search(q2, top_k=None, expand=False, candidates=80)
                        for h in (more or []):
                            key = str(h.get("pmid") or h.get("entry_no") or h.get("doc_id") or "")
                            if key and key not in seen_keys:
                                seen_keys.add(key)
                                hits.append(h)
                                added += 1
                    if added:
                        logger.info(f"[Hub] 别名补充检索命中 {added} 篇（历史名 → 文献池）")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[Hub] 别名补充检索失败（不影响主检索）：{e}")
            pmids = []
            seen = set()
            for h in hits:
                p = str(h.get("pmid") or "").strip()
                if p and p not in seen:
                    seen.add(p)
                    pmids.append(p)
                else:
                    eno = str(h.get("entry_no") or "").strip()
                    tag = f"eno:{eno}" if eno else str(h.get("doc_id") or "").strip()
                    if tag and tag not in seen:
                        seen.add(tag)
                        pmids.append(tag)
            ctx = yes.get_context(question, top_k=None, max_chars=budget, hits=hits)
            excerpt = truncate_context(ctx, max_chars=budget)
            if excerpt and len(excerpt.strip()) > 40:
                source = "pubmed_yes"
        elif pubmed_yes_loading() or yes is None:
            logger.info("[Hub] PubmedYes 仍在预热，本问回退 Hub 小库（不占用对话锁等 FAISS）")
    except Exception as e:
        pubmed_error = str(e)
        logger.warning(f"[Hub] PubmedYesRetriever 失败，不改用小库编答案: {e}")

    # 主库失败时不回退 Hub 小库。那是另一套索引，会把离题文章写成正式回答。
    if source not in {"pubmed_yes", "gene_pubmed_blend"} and not pubmed_error and not gene_pool:
        try:
            from rag_builder import get_rag

            ctx = get_rag().get_context(question)
            excerpt = truncate_context(ctx, max_chars=min(budget, 2400))
            if excerpt and len(excerpt.strip()) > 40:
                source = "hub_faiss"
        except Exception as e:
            logger.warning(f"[Hub] Hub FAISS 失败: {e}")

    if bib_block:
        excerpt = (bib_block + "\n\n" + (excerpt or "")).strip()
        if source in {"none", ""}:
            source = "ncbi_gene_pubmed"

    return {
        "chars": len(excerpt or ""),
        "excerpt": excerpt,
        "has_hits": bool(excerpt and len(excerpt.strip()) > 40),
        "source": "pubmed_yes_failed" if pubmed_error else source,
        "error": pubmed_error,
        "pmids": pmids,
        "max_chars": budget,
        "retrieve_candidates": retrieve_candidates,
        "ncbi_bibliography": bib_block,
    }


def _run_enrichment(question: str) -> dict:
    from gene_toolkit import extract_genes_from_question
    from enrichment_toolkit import go_enrichment, kegg_enrichment

    genes = extract_genes_from_question(_question_for_gene_extract(question), limit=8)
    if not genes:
        return {"skipped": True}
    return {"genes": genes, "go": go_enrichment(genes), "kegg": kegg_enrichment(genes)}


def _run_database(question: str) -> dict:
    from agent_scheduler import agent_scheduler
    from config import TASK_TYPES

    return agent_scheduler.run_task_detail(question, TASK_TYPES["DB_QUERY"])


def _dispatch_specialist(agent_id: str, question: str, task_type: str) -> tuple:
    t0 = time.time()
    try:
        if agent_id == "id_mapper":
            payload = _run_id_mapper(question)
            ok = payload.get("resolved")
            sym = (payload.get("canonical") or {}).get("gene_name") or payload.get("query")
            summary = f"解析 {'成功' if ok else '未命中'}：{sym or question[:24]}"
            return agent_id, _trace_step(agent_id, "ok" if ok else "miss", summary, ms=_ms(t0), detail={"resolved": ok}), payload
        if agent_id == "gene_analyst":
            payload = _run_gene_analyst(question)
            if payload.get("skipped"):
                return agent_id, _trace_step(agent_id, "skip", "未检测到基因符号", ms=_ms(t0)), payload
            ok = payload.get("success")
            summary = f"基因 {payload.get('gene')} {'已加载' if ok else '未找到'}"
            return agent_id, _trace_step(agent_id, "ok" if ok else "miss", summary, ms=_ms(t0)), payload
        if agent_id == "literature":
            payload = _run_literature(question, max_chars=_lit_pack_chars())
            ok = payload.get("has_hits")
            src = payload.get("source") or "none"
            n_pmid = len(payload.get("pmids") or [])
            summary = f"RAG[{src}] {payload.get('chars', 0)} 字符 / PMID {n_pmid}"
            return agent_id, _trace_step(
                agent_id, "ok" if ok else "miss", summary, ms=_ms(t0),
                detail={"source": src, "pmids": payload.get("pmids")},
            ), payload
        if agent_id == "enrichment":
            payload = _run_enrichment(question)
            if payload.get("skipped"):
                return agent_id, _trace_step(agent_id, "skip", "无基因列表，跳过富集", ms=_ms(t0)), payload
            ngo = len((payload.get("go") or {}).get("significant") or [])
            summary = f"ORA 显著 GO 项 {ngo}"
            return agent_id, _trace_step(agent_id, "ok", summary, ms=_ms(t0)), payload
        if agent_id == "database":
            payload = _run_database(question)
            n = payload.get("row_count") or 0
            summary = f"SQL 返回 {n} 行"
            return agent_id, _trace_step(agent_id, "ok", summary, ms=_ms(t0)), payload
    except Exception as e:
        logger.warning(f"[Hub] {agent_id} 失败: {e}")
        return agent_id, _trace_step(agent_id, "error", str(e)[:120], ms=_ms(t0)), {"error": str(e)}
    return agent_id, _trace_step(agent_id, "skip", "未知 Agent", ms=_ms(t0)), {}


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


def _merge_partials(result: dict, partials: Dict[str, Any]) -> dict:
    idm = partials.get("id_mapper")
    if idm and idm.get("resolved") and not result.get("id_mapping"):
        result["id_mapping"] = idm
    gene = partials.get("gene_analyst")
    if gene and gene.get("detail") and not result.get("gene_detail"):
        result["gene_detail"] = gene["detail"]
    lit = partials.get("literature")
    if lit and lit.get("excerpt") and not result.get("context"):
        result["context"] = lit["excerpt"]
    enrich = partials.get("enrichment")
    if enrich and enrich.get("go") and not result.get("enrichment"):
        result["enrichment"] = enrich
    db = partials.get("database")
    if db and db.get("data") and not result.get("data"):
        for k in ("data", "sql", "row_count", "answer"):
            if db.get(k) and not result.get(k):
                result[k] = db[k]
    return result


def _synthesize_answer(result: dict, trace: List[dict]) -> str:
    """综合 Agent：直接返回主回答正文。

    不再在正文后追加「Hub 多 Agent 编排摘要」——这些可追溯信息已在
    侧边「回答详情 / Agent 编排」中完整展示，正文只保留结论/原因分析/总结。
    """
    answer = (result.get("answer") or "").rstrip()
    if not answer or answer.startswith("[错误]"):
        return answer
    return answer


def _scope_footer(
    scope: str,
    depth: str = "full",
    extra_lines: Optional[List[str]] = None,
) -> str:
    """回答末尾附加信息：仅保留有意义的 extra_lines，不再输出“回答范围/使用策略” meta 行。"""
    if not extra_lines:
        return ""
    return "\n\n---\n" + "\n".join(extra_lines)


_REF_HEAD_RE = re.compile(
    r"^[ \t]*(?:\*\*)?[ \t]*(参考文献|参考资料|references?|bibliography)[ \t]*(?:\*\*)?[ \t]*[:：]?[ \t]*$",
    re.I | re.M,
)


def _strip_trailing_reference_block(answer: str) -> str:
    """去掉模型自贴的文末文献表（通用车道不应有）。

    只在「标题 + 编号/年份条目」确实出现在尾部时才切；旧实现是出现「参考文献」
    四个字就从头切开，用户问「怎么管理参考文献」时会被拦腰截断。
    """
    text = answer or ""
    if not text:
        return text
    last = None
    for m in _REF_HEAD_RE.finditer(text):
        last = m
    if last is None:
        return text
    tail = text[last.end():]
    if len(tail) > len(text) * 0.55:
        return text
    if not re.search(r"(\[\d+\]|PMID|DOI|20\d\d)", tail, re.I):
        return text
    return text[: last.start()].rstrip()


def run_general_assistant(
    question: str,
    *,
    tier: str = "D",
    history: Optional[List[Any]] = None,
) -> dict:
    """邻近生物学 (C) / 通用闲聊 (D)：无 PubmedYes、无参考文献脚注、轻量回答。"""
    from common_utils import empty_result, _reasoning_max_tokens
    from config import TASK_TYPES
    from zebrafish_prompt import (
        GENERAL_ASSISTANT_SYSTEM_PROMPT,
        build_general_assistant_user_prompt,
        use_system_prompt,
        normalize_visible_answer,
        OFF_TOPIC_REPLY,
    )

    t0 = time.time()
    tier_u = (tier or "D").upper()
    scope = "adjacent" if tier_u == "C" else "general"
    prompt = build_general_assistant_user_prompt(question, tier=tier_u, history=history)
    answer = ""
    try:
        from llm_loader import get_reasoning_llm

        with use_system_prompt(GENERAL_ASSISTANT_SYSTEM_PROMPT):
            answer = get_reasoning_llm().generate(
                prompt, max_new_tokens=min(1600, _reasoning_max_tokens())
            )
        answer = normalize_visible_answer(answer or "", question=question)
    except Exception as e:
        logger.warning(f"[General] LLM 失败：{e}")
        answer = ""

    if not (answer or "").strip():
        answer = OFF_TOPIC_REPLY

    # 去掉模型自贴的文末文献表：只在它确实是「标题 + 编号条目」时才切，
    # 否则「怎么管理参考文献」这类正常答案会被从中间截断。
    answer = _strip_trailing_reference_block(answer)

    return {
        **empty_result(),
        "answer": answer.strip(),
        "task_type": TASK_TYPES["GENERAL"],
        "hub_mode": False,
        "answer_scope": scope,
        "intent_tier": tier_u,
        "skip_enrich": True,  # C/D 均不灌组学清单 / 基因双链刷屏
        "skip_reference_footer": True,
        "agent_trace": [
            _trace_step(
                "router",
                "ok",
                f"general_assistant tier={tier_u}",
                ms=_ms(t0),
                detail={"tier": tier_u, "scope": scope},
            )
        ],
        "specialists": [],
    }


def run_literature_only(question: str, *, depth: str = "full") -> dict:
    """仅文献：PubmedYes FAISS 候选→CE 重排→按篇去重编号后送 LLM。"""
    from common_utils import empty_result, _synthesize_rag_answer
    from config import TASK_TYPES
    from intent_router import classify_question_intent, is_general_lane_tier, TIER_E, safety_refusal_reply

    depth = normalize_answer_depth(depth)
    # 完整回答（默认 full）：打包更长上下文、全量候选，让 DeepSeek 有充分素材展开
    pack_chars = _lit_pack_chars(depth)
    # 一律走粗 500 篇 / 精 100 篇；部分回答只关查询扩写
    retrieve_candidates = None
    expand = False if depth == "partial" else None

    intent = classify_question_intent(question)
    tier = intent.get("tier")
    if tier == TIER_E:
        return {
            **empty_result(),
            "answer": safety_refusal_reply(),
            "task_type": TASK_TYPES["GENERAL"],
            "agent_trace": [],
            "answer_scope": "safety",
            "answer_depth": depth,
            "hub_mode": False,
            "intent_tier": TIER_E,
            "skip_enrich": True,
        }
    if is_general_lane_tier(tier):
        return run_general_assistant(question, tier=tier)

    trace: List[dict] = []
    t0 = time.time()
    lit = _run_literature(
        question,
        max_chars=pack_chars,
        retrieve_candidates=retrieve_candidates,
        expand=expand,
    )
    ok = lit.get("has_hits")
    src = lit.get("source") or "none"
    n_pmid = len(lit.get("pmids") or [])
    trace.append(
        _trace_step(
            "literature",
            "ok" if ok else "miss",
            f"RAG[{src}] {lit.get('chars', 0)} 字符 / PMID {n_pmid}",
            ms=_ms(t0),
            detail={"source": src, "pmids": lit.get("pmids"), "max_chars": pack_chars},
        )
    )

    gene_detail = None
    specialists = ["literature"]
    # 整体回答：文献之外补基因卡片（仍不跑富集/全链路）
    if depth == "full":
        t_g = time.time()
        gene_payload = _run_gene_analyst(question)
        if gene_payload.get("skipped"):
            trace.append(_trace_step("gene_analyst", "skip", "未检测到基因符号", ms=_ms(t_g)))
        else:
            specialists.append("gene_analyst")
            gene_detail = gene_payload.get("detail")
            ok_g = gene_payload.get("success")
            trace.append(
                _trace_step(
                    "gene_analyst",
                    "ok" if ok_g else "miss",
                    f"基因 {gene_payload.get('gene')} {'已加载' if ok_g else '未找到'}",
                    ms=_ms(t_g),
                )
            )

    ctx = lit.get("excerpt") or ""
    t_ans = time.time()
    if ctx and len(ctx.strip()) > 40:
        answer = _synthesize_rag_answer(question, ctx, gene_detail=gene_detail)
        from zebrafish_prompt import ensure_reference_footer, ensure_trait_ge_formula

        answer = ensure_reference_footer(answer, ctx, question=question)
        answer = ensure_trait_ge_formula(answer, question)
    else:
        answer = (
            "本地文献库 / PubmedYes 未命中足够相关片段。"
            "请换关键词重试，或改选「Hub 全链路 / 仅基因库」。"
            "在未命中时我不会编造库内坐标或表达量。"
        )
    trace.append(
        _trace_step("synthesizer", "ok", f"文献范围合成（{ANSWER_DEPTHS[depth]}）", ms=_ms(t_ans))
    )

    answer = (answer or "").rstrip()
    bib_block = lit.get("ncbi_bibliography") or ""
    if (
        bib_block
        and "基因页：https://www.ncbi.nlm.nih.gov/gene/" not in answer
        and "基因頁：https://www.ncbi.nlm.nih.gov/gene/" not in answer
        and "Gene page: https://www.ncbi.nlm.nih.gov/gene/" not in answer
    ):
        answer += "\n\n" + bib_block
    answer += _scope_footer(
        "literature",
        depth,
        [f"检索源：{src} · 按篇编号打包≤{pack_chars}字"],
    )
    return {
        **empty_result(),
        "answer": answer,
        "task_type": TASK_TYPES["RAG_QUERY"],
        "context": ctx or None,
        "gene_detail": gene_detail,
        "agent_trace": trace,
        "hub_mode": False,
        "answer_scope": "literature",
        "answer_depth": depth,
        "specialists": specialists,
    }


def run_gene_db_only(question: str, *, depth: str = "full") -> dict:
    """仅基因/数据库：不跑文献 FAISS。部分=主路径；整体=ID+基因(+SQL)。"""
    from agent_scheduler import route_task, agent_scheduler
    from common_utils import empty_result
    from config import TASK_TYPES
    from gene_formatter import (
        answer_gene_version_question,
        answer_reference_genome_versions,
        is_biology_mechanism_question,
        is_gene_version_question,
        is_reference_genome_question,
    )
    from zebrafish_prompt import is_zebrafish_in_scope

    depth = normalize_answer_depth(depth)
    if is_biology_mechanism_question(question):
        return run_literature_only(question, depth=depth)
    from intent_router import classify_question_intent, is_general_lane_tier, TIER_E, safety_refusal_reply

    intent = classify_question_intent(question)
    tier = intent.get("tier")
    if tier == TIER_E:
        return {
            **empty_result(),
            "answer": safety_refusal_reply(),
            "task_type": TASK_TYPES["GENERAL"],
            "agent_trace": [],
            "answer_scope": "safety",
            "answer_depth": depth,
            "hub_mode": False,
            "intent_tier": TIER_E,
            "skip_enrich": True,
        }
    if is_general_lane_tier(tier) or not is_zebrafish_in_scope(question):
        return run_general_assistant(question, tier=tier or "D")

    # 组装 / 库规模事实题：直接结构化回答，避免误走基因卡
    qlow = (question or "").lower()
    if (
        is_reference_genome_question(question)
        or is_gene_version_question(question)
        or ("蛋白编码" in question)
        or ("全基因组" in question and "基因" in question)
        or ("收录" in question and "基因" in question)
    ):
        t0 = time.time()
        if is_reference_genome_question(question) or re.search(r"grcz\d+", qlow):
            answer = answer_reference_genome_versions()
            tip = "参考基因组/组装事实"
        else:
            answer = answer_gene_version_question(question)
            tip = "基因库规模/版本消歧"
        return {
            **empty_result(),
            "answer": (answer or "").rstrip() + _scope_footer("gene_db", depth),
            "task_type": TASK_TYPES["DB_QUERY"],
            "agent_trace": [
                _trace_step("synthesizer", "ok", tip, ms=_ms(t0)),
            ],
            "hub_mode": False,
            "answer_scope": "gene_db",
            "answer_depth": depth,
            "specialists": [],
        }

    trace: List[dict] = []
    partials: Dict[str, Any] = {}
    t_router = time.time()
    routed = route_task(question)
    if routed == TASK_TYPES["DB_QUERY"]:
        task_type = TASK_TYPES["DB_QUERY"]
    else:
        task_type = TASK_TYPES["GENE_DETAIL"]
    trace.append(
        _trace_step(
            "router",
            "ok",
            f"基因/库范围 → {task_type}（{ANSWER_DEPTHS[depth]}）",
            ms=_ms(t_router),
            detail={"task_type": task_type, "routed": routed, "depth": depth},
        )
    )

    if depth == "partial":
        specialists = ["gene_analyst"] if task_type != TASK_TYPES["DB_QUERY"] else ["database"]
        qlow = (question or "").lower()
        if any(k in qlow for k in ("ensembl", "zfin", "zdb-gene", "对应", "别名", "ncbi")):
            specialists = ["id_mapper"] + specialists
    else:
        specialists = ["id_mapper", "gene_analyst"]
        if task_type == TASK_TYPES["DB_QUERY"]:
            specialists.append("database")

    with ThreadPoolExecutor(max_workers=min(3, len(specialists))) as pool:
        futs = {pool.submit(_dispatch_specialist, a, question, task_type): a for a in specialists}
        for fut in as_completed(futs):
            agent_id, step, payload = fut.result()
            trace.append(step)
            partials[agent_id] = payload

    t_main = time.time()
    result = agent_scheduler.run_task_detail(question, task_type)
    trace.append(_trace_step("synthesizer", "ok", f"主任务完成（{task_type}）", ms=_ms(t_main)))
    result = _merge_partials(result, partials)
    result["answer"] = (result.get("answer") or "").rstrip() + _scope_footer("gene_db", depth)
    result["agent_trace"] = trace
    result["hub_mode"] = False
    result["answer_scope"] = "gene_db"
    result["answer_depth"] = depth
    result["specialists"] = specialists
    return result


def run_id_map_only(question: str, *, depth: str = "full") -> dict:
    """仅 ID 映射：跨库符号/Ensembl/ZFIN/NCBI，不跑文献与 LLM。"""
    from common_utils import empty_result
    from config import TASK_TYPES
    from gene_formatter import (
        format_id_mapping_block,
        is_biology_mechanism_question,
        is_complaint_followup,
        is_explanatory_followup,
    )
    depth = normalize_answer_depth(depth)
    if is_biology_mechanism_question(question):
        return run_literature_only(question, depth=depth)
    if is_complaint_followup(question) or is_explanatory_followup(question):
        try:
            from zebrafish_prompt import answer_is_english

            en = bool(answer_is_english(question))
        except Exception:
            en = False
        return {
            **empty_result(),
            "answer": (
                "This is not a gene symbol, so historical gene-version unification cannot run. "
                "For mechanism / transcription / duplication questions, use Hub full pipeline "
                "literature search; if you are following up on the previous answer, keep asking "
                "the original scientific question in chat."
                if en
                else (
                    "这不是基因符号，不能做「历史基因版本统一」。"
                    "机制/转录/复制类问题请走 Hub 全链路文献检索；"
                    "若在追问上一答，请在聊天里继续问原科学问题。"
                )
            ),
            "task_type": TASK_TYPES["GENE_DETAIL"],
            "agent_trace": [_trace_step("id_mapper", "ok", "skip_non_gene_query", ms=0)],
            "answer_scope": "id_map",
            "answer_depth": depth,
            "hub_mode": False,
        }
    from intent_router import classify_question_intent, is_general_lane_tier, TIER_E, safety_refusal_reply

    intent = classify_question_intent(question)
    tier = intent.get("tier")
    if tier == TIER_E:
        return {
            **empty_result(),
            "answer": safety_refusal_reply(),
            "task_type": TASK_TYPES["GENERAL"],
            "agent_trace": [],
            "answer_scope": "safety",
            "answer_depth": depth,
            "hub_mode": False,
            "intent_tier": TIER_E,
            "skip_enrich": True,
        }
    if is_general_lane_tier(tier):
        return run_general_assistant(question, tier=tier or "D")

    trace: List[dict] = []
    t0 = time.time()
    idm = _run_id_mapper(question)
    if not idm.get("resolved"):
        return run_literature_only(question, depth=depth)
    ok = bool(idm.get("resolved"))
    sym = (idm.get("canonical") or {}).get("gene_name") or idm.get("query")
    trace.append(
        _trace_step(
            "id_mapper",
            "ok" if ok else "miss",
            f"解析 {'成功' if ok else '未命中'}：{sym or question[:24]}",
            ms=_ms(t0),
            detail={"resolved": ok},
        )
    )

    answer = format_id_mapping_block(idm)
    gene_detail = None
    specialists = ["id_mapper"]
    # 整体回答：附带基因卡片（仍不跑文献）
    if depth == "full" and ok:
        t_g = time.time()
        gene_payload = _run_gene_analyst(question)
        if gene_payload.get("skipped"):
            # 用映射到的官方符号再查一次
            from gene_toolkit import get_gene_detail, normalize_gene_name

            name = normalize_gene_name(str(sym or ""))
            detail = get_gene_detail(name) if name else None
            if detail:
                gene_detail = detail
                specialists.append("gene_analyst")
                trace.append(
                    _trace_step("gene_analyst", "ok", f"基因 {name} 已加载", ms=_ms(t_g))
                )
            else:
                trace.append(_trace_step("gene_analyst", "skip", "未加载到基因卡片", ms=_ms(t_g)))
        else:
            specialists.append("gene_analyst")
            gene_detail = gene_payload.get("detail")
            ok_g = gene_payload.get("success")
            trace.append(
                _trace_step(
                    "gene_analyst",
                    "ok" if ok_g else "miss",
                    f"基因 {gene_payload.get('gene')} {'已加载' if ok_g else '未找到'}",
                    ms=_ms(t_g),
                )
            )
            if gene_detail and gene_detail.get("function"):
                answer = (answer or "").rstrip() + f"\n\n【功能摘要】\n{gene_detail.get('function')}"

    answer = (answer or "").rstrip() + _scope_footer("id_map", depth)
    return {
        **empty_result(),
        "answer": answer,
        "task_type": TASK_TYPES["GENE_DETAIL"],
        "id_mapping": idm,
        "gene_detail": gene_detail,
        "agent_trace": trace,
        "hub_mode": False,
        "answer_scope": "id_map",
        "answer_depth": depth,
        "specialists": specialists,
    }


def run_retrieval_debug_only(question: str, *, depth: str = "full") -> dict:
    """检索调试：返回候选数/重排摘要，不调用 LLM。"""
    from common_utils import empty_result
    from config import TASK_TYPES

    depth = normalize_answer_depth(depth)
    # 与正式问答同一打包预算；部分回答只缩短候选预览条数
    pack_chars = _lit_pack_chars(depth)
    cand_preview = 20 if depth == "partial" else 40

    result = empty_result()
    result["task_type"] = TASK_TYPES["RAG_QUERY"]
    result["answer_scope"] = "retrieval_debug"
    result["answer_depth"] = depth
    result["hub_mode"] = False
    t0 = time.time()
    try:
        from rag.pubmed_yes_retriever import (
            get_pubmed_yes_retriever,
            peek_pubmed_yes_retriever,
            pubmed_yes_loading,
        )

        yes = peek_pubmed_yes_retriever()
        if yes is None and not pubmed_yes_loading():
            yes = get_pubmed_yes_retriever()
        if yes is None or not yes.ready:
            msg = "PubmedYes FAISS 未就绪（预热中或尚未加载），请稍候再试检索调试。"
            result["answer"] = msg + _scope_footer("retrieval_debug", depth)
            result["agent_trace"] = [
                _trace_step("literature", "miss", msg, ms=_ms(t0))
            ]
            return result

        dbg = yes.debug_search(
            question, top_k=None, max_chars=pack_chars, candidate_preview=cand_preview
        )
        result["retrieval_debug"] = dbg
        cand = dbg.get("candidates_before_rerank")
        after = dbg.get("after_rerank_top_n")
        method = dbg.get("rerank_method") or "-"
        cfg = dbg.get("config") or {}
        lines = [
            f"查询：{question}",
            f"使用策略：{ANSWER_DEPTHS[depth]}",
            f"检索配置：候选≈{cfg.get('retrieve_candidates', '?')} → 重排 top{cfg.get('rerank_top_n', 100)}"
            f"（rerank={cfg.get('rerank_enabled')}）",
            f"本轮：重排前候选 {cand} · 重排后 {after} · 方法 {method}",
            "",
            "【重排 Top】",
        ]
        for row in (dbg.get("ranked") or [])[:20]:
            rank = row.get("rank") or "?"
            score = row.get("rerank_score")
            score_s = f"{score:.4f}" if isinstance(score, (int, float)) else str(score or "-")
            pmid = row.get("pmid") or row.get("entry_no") or row.get("doc_id") or "-"
            title = (row.get("title_zh") or row.get("preview") or "")[:80]
            lines.append(f"{rank}. score={score_s} | {pmid} | {title}")
        if not dbg.get("ranked"):
            lines.append("（无命中）")
        hub_pack = dbg.get("hub_pack") or {}
        lines.append("")
        lines.append(
            f"打包：{hub_pack.get('chunks_included', '?')} 块 / "
            f"{hub_pack.get('total_chars', '?')} 字符（未送 LLM）"
        )
        lines.append(f"耗时：{dbg.get('elapsed_sec', '?')} 秒")
        lines.append("说明：本范围仅返回检索排序摘要，未调用大模型生成回答。")
        result["answer"] = "\n".join(lines) + _scope_footer("retrieval_debug", depth)
        packed_prev = hub_pack.get("context_preview_head") or ""
        if not packed_prev:
            for c in (hub_pack.get("order") or [])[:3]:
                packed_prev += (c.get("preview") or "")[:400] + "\n\n"
        result["context"] = packed_prev.strip()[:4000] or None
        result["agent_trace"] = [
            _trace_step(
                "literature",
                "ok" if after else "miss",
                f"调试检索 候选{cand}→top{after} ({method})",
                ms=_ms(t0),
                detail={"rerank_method": method},
            )
        ]
    except Exception as e:
        logger.warning(f"[Hub] retrieval_debug 失败: {e}")
        result["answer"] = f"[错误] 检索调试失败：{e}" + _scope_footer("retrieval_debug", depth)
        result["error"] = str(e)
        result["agent_trace"] = [
            _trace_step("literature", "error", str(e)[:120], ms=_ms(t0))
        ]
    return result


def run_hub_orchestration(
    question: str,
    *,
    task_type_override: Optional[str] = None,
    depth: str = "full",
) -> dict:
    """多 Agent 编排入口。depth=partial 时减少 specialist / 跳过富集与自动工具。"""
    from agent_scheduler import route_task, agent_scheduler
    from tool_orchestrator import augment_chat_result
    from intent_router import (
        classify_question_intent,
        is_general_lane_tier,
        TIER_E,
        safety_refusal_reply,
    )
    from common_utils import empty_result
    from config import TASK_TYPES

    depth = normalize_answer_depth(depth)
    intent = classify_question_intent(question)
    tier = intent.get("tier")
    if tier == TIER_E:
        return {
            **empty_result(),
            "answer": safety_refusal_reply(),
            "task_type": TASK_TYPES["GENERAL"],
            "agent_trace": [],
            "answer_depth": depth,
            "intent_tier": TIER_E,
            "skip_enrich": True,
        }
    if is_general_lane_tier(tier):
        return run_general_assistant(question, tier=tier)

    trace: List[dict] = []
    partials: Dict[str, Any] = {}

    t_router = time.time()
    task_type = task_type_override or route_task(question)
    trace.append(
        _trace_step(
            "router",
            "ok",
            f"路由 → {task_type}（{ANSWER_DEPTHS[depth]}）intent={tier}",
            ms=_ms(t_router),
            detail={"task_type": task_type, "depth": depth, "intent": intent},
        )
    )

    specialists = _plan_specialists(question, task_type, depth=depth)
    if specialists:
        with ThreadPoolExecutor(max_workers=min(5, len(specialists))) as pool:
            futs = {pool.submit(_dispatch_specialist, a, question, task_type): a for a in specialists}
            for fut in as_completed(futs):
                agent_id, step, payload = fut.result()
                trace.append(step)
                partials[agent_id] = payload

    t_main = time.time()
    lit = partials.get("literature") or {}
    excerpt = (lit.get("excerpt") or "").strip()
    if (not excerpt or len(excerpt) <= 40) and _looks_trait_or_strain(question):
        t_retry = time.time()
        lit_retry = _run_literature(
            f"{question} zebrafish strain morphology AB TU phenotype",
            max_chars=_lit_pack_chars(),
        )
        excerpt_retry = (lit_retry.get("excerpt") or "").strip()
        if excerpt_retry and len(excerpt_retry) > 40:
            excerpt = excerpt_retry
            lit = lit_retry
            partials["literature"] = lit_retry
            trace.append(
                _trace_step(
                    "literature",
                    "ok",
                    f"性状/品系题二次检索 {lit_retry.get('chars', 0)} 字",
                    ms=_ms(t_retry),
                )
            )
    gdetail = (partials.get("gene_analyst") or {}).get("detail")
    lit_failed = (lit.get("source") == "pubmed_yes_failed")
    if lit_failed:
        excerpt = ""
    if excerpt and len(excerpt) > 40:
        from common_utils import empty_result, _synthesize_rag_answer

        result = empty_result()
        result["task_type"] = task_type
        result["context"] = excerpt
        if gdetail:
            result["gene_detail"] = gdetail
        result["answer"] = _synthesize_rag_answer(
            question, excerpt, gene_detail=gdetail
        )
        from zebrafish_prompt import ensure_reference_footer, ensure_trait_ge_formula

        result["answer"] = ensure_reference_footer(result["answer"], excerpt, question=question)
        result["answer"] = ensure_trait_ge_formula(result["answer"], question)
        result = _merge_partials(result, {k: v for k, v in partials.items() if k != "literature"})
        n_paper = excerpt.count("\n[") + (1 if excerpt.lstrip().startswith("[") else 0)
        n_num = len(re.findall(r"^\[\d+\]\s", excerpt, re.M))
        if n_num:
            n_paper = n_num
        trace.append(
            _trace_step(
                "synthesizer",
                "ok",
                f"用文献编号证据合成（约 {n_paper} 篇 / {len(excerpt)} 字）",
                ms=_ms(t_main),
            )
        )
    elif lit_failed:
        from common_utils import empty_result

        try:
            from gene_formatter import is_biology_mechanism_question as _is_mech
            from zebrafish_prompt import is_hub_product_howto_question, answer_hub_product_howto

            need_lit = _is_mech(question) or _looks_trait_or_strain(question)
            if is_hub_product_howto_question(question):
                result = empty_result()
                result["task_type"] = task_type
                result["answer"] = answer_hub_product_howto(question)
                result = _merge_partials(result, partials)
                trace.append(
                    _trace_step("synthesizer", "ok", "产品用法（文献失败已绕过）", ms=_ms(t_main))
                )
            elif gdetail:
                from gene_formatter import answer_from_gene_detail

                result = empty_result()
                result["task_type"] = task_type
                result["gene_detail"] = gdetail
                result["answer"] = (
                    answer_from_gene_detail(question, gdetail).rstrip()
                    + "\n\n文献检索这次没有完成，上面只根据本地基因库，没有编文献。"
                )
                trace.append(
                    _trace_step("literature", "error", "文献主库失败，已停止用小库编造", ms=_ms(t_main))
                )
                result = _merge_partials(result, partials)
            elif need_lit:
                from zebrafish_prompt import classify_answer_type, ensure_trait_ge_formula

                result = empty_result()
                result["task_type"] = task_type
                if classify_answer_type(question) == "trait" or _looks_trait_or_strain(question):
                    draft = (
                        "**突变性状由遗传与环境共同决定**，可概括为 "
                        "表型 = 基因型 + 环境 + G×E。\n\n"
                        "- **遗传**：突变等位类型、遗传背景/品系、修饰基因与剂量。\n"
                        "- **环境**：温度、密度、营养、光照等饲养条件可改变外显与严重度。\n"
                        "- **互作（G×E）**：同一突变在不同品系或饲养条件下表型可明显不同。\n\n"
                        "（文献检索暂不可用，以上是决定框架，未附文献编号；文献库恢复后再问可补证据。）"
                    )
                    result["answer"] = ensure_trait_ge_formula(draft, question)
                else:
                    result["answer"] = (
                        "文献检索这次没有完成（服务器显存不足，文献向量库未加载），"
                        "没有改用别的库编答案。请过一会儿再问，或先问不依赖文献的基因/统计类问题。"
                    )
                trace.append(
                    _trace_step("literature", "error", "文献主库失败，已停止用小库编造", ms=_ms(t_main))
                )
                result = _merge_partials(result, partials)
            else:
                result = agent_scheduler.run_task_detail(question, task_type)
                trace.append(
                    _trace_step(
                        "synthesizer",
                        "ok",
                        f"文献失败，改走主任务（{task_type}）",
                        ms=_ms(t_main),
                    )
                )
                result = _merge_partials(result, partials)
        except Exception:
            result = empty_result()
            result["task_type"] = task_type
            result["answer"] = "文献检索这次没有完成，没有改用别的库编答案。请过一会儿再问。"
            result = _merge_partials(result, partials)
    else:
        result = agent_scheduler.run_task_detail(question, task_type)
        trace.append(_trace_step("synthesizer", "ok", f"主任务完成（{task_type}，无文献摘录）", ms=_ms(t_main)))
        result = _merge_partials(result, partials)
    if depth == "full":
        try:
            result = augment_chat_result(question, result)
        except Exception as e:
            logger.warning(f"[Hub] 自动工具失败: {e}")

    from gene_toolkit import question_has_digit_leading_gene

    gdetail = result.get("gene_detail") or (partials.get("gene_analyst") or {}).get("detail")
    if question_has_digit_leading_gene(question) and isinstance(gdetail, dict) and gdetail.get("basic"):
        from gene_formatter import answer_from_gene_detail

        result["gene_detail"] = gdetail
        result["answer"] = answer_from_gene_detail(question, gdetail)
    result["answer"] = _synthesize_answer(result, trace)
    # 统一脚注：hub 路径以前漏了这一步 —— 实测文末出现 31 条（>上限 25），
    # 且证据池里的 `rel=` 分数泄漏进答案正文。放在 scope 说明之前，保证顺序。
    # 2026-10-17 补充：原来只在 `context` 够长时才补脚注，导致「基因详情」这类
    #   没有 RAG 上下文的路径整段被跳过 —— 表现就是文末只剩模型自己写的一句
    #   「参考文献（系统生成）：PMID … 等。」或者只有寥寥几条。
    #   别名/复制体类问题改用「全文别名倒排表」出表，所以没有 context 也要进来。
    try:
        _ctx = result.get("context") or ""
        _has_ctx = isinstance(_ctx, str) and len(_ctx.strip()) > 40
        _alias_q = False
        try:
            from gene_alias_evidence import is_alias_question, is_ortholog_question

            _alias_q = bool(is_alias_question(question) or is_ortholog_question(question))
        except Exception:  # noqa: BLE001
            _alias_q = False
        if _has_ctx or _alias_q:
            from zebrafish_prompt import ensure_reference_footer

            result["answer"] = ensure_reference_footer(
                result["answer"], _ctx if isinstance(_ctx, str) else "", question=question
            )
    except Exception as _e:  # noqa: BLE001
        logger.warning(f"[Hub] 统一脚注失败（不影响回答）：{_e}")
    result["answer"] = (result.get("answer") or "").rstrip()
    try:
        from ncbi_gene_bibliography import bibliography_for_question

        bib_block = bibliography_for_question(question).get("answer_block") or ""
        _ans = result.get("answer") or ""
        # 去重标记跟着 ncbi_gene_bibliography 的输出格式走：URL 片段最稳，
        # 末尾那句「在 NCBI Gene 页查看全部 / All papers on the NCBI Gene page」做兜底。
        already = (
            "ncbi.nlm.nih.gov/gene/" in _ans
            or "NCBI Gene 页查看全部" in _ans
            or "All papers on the NCBI Gene page" in _ans
        )
        if bib_block and not already:
            result["answer"] += "\n\n" + bib_block
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Hub] NCBI Gene bibliography 附加失败：{e}")
    result["answer"] += _scope_footer("hub", depth)
    result["agent_trace"] = trace
    result["hub_mode"] = True
    result["answer_scope"] = "hub"
    result["answer_depth"] = depth
    result["specialists"] = specialists
    return result


def answer_corpus_stats(question: str, *, depth: str = "full") -> dict:
    """文献库和基因库的条数。不跑基因卡片，也不跑向量检索。"""
    from common_utils import empty_result

    depth = normalize_answer_depth(depth)
    sql = (
        "SELECT "
        "(SELECT COUNT(*) FROM gene_basic) AS gene_count, "
        "(SELECT COUNT(*) FROM literature_paper) AS paper_count, "
        "(SELECT COUNT(*) FROM literature_paper WHERE title_en IS NOT NULL AND TRIM(title_en)<>'') AS title_en, "
        "(SELECT COUNT(*) FROM literature_paper WHERE doi IS NOT NULL AND TRIM(doi)<>'') AS doi_n, "
        "(SELECT COUNT(*) FROM literature_paper WHERE journal IS NOT NULL AND TRIM(journal)<>'') AS journal_n, "
        "(SELECT MIN(year) FROM literature_paper WHERE year BETWEEN 1950 AND 2035) AS year_min, "
        "(SELECT MAX(year) FROM literature_paper WHERE year BETWEEN 1950 AND 2035) AS year_max"
    )
    try:
        from mysql_client import get_mysql_client

        res = get_mysql_client().execute_sql(sql, max_rows=1)
        row = ((res or {}).get("data") or [None])[0]
    except Exception as e:
        logger.warning(f"[Hub] 文献统计查询失败: {e}")
        row = None
    if not row:
        answer = "本地库这次没有返回文献条数，无法给出统计。"
    else:
        papers = int(row.get("paper_count") or 0)
        genes = int(row.get("gene_count") or 0)
        en = int(row.get("title_en") or 0)
        dois = int(row.get("doi_n") or 0)
        journals = int(row.get("journal_n") or 0)
        ymin = row.get("year_min") or "?"
        ymax = row.get("year_max") or "?"
        answer = (
            f"本地文献库现有 **{papers}** 篇，年份从 {ymin} 到 {ymax}。"
            f"其中英文题名 {en} 篇，有 DOI {dois} 篇，有期刊名 {journals} 篇。\n\n"
            f"本地基因库 **{genes}** 个基因。这是本库收录条数，不是 ZFIN 基因页上的 publication 计数。"
        )
    return {
        **empty_result(),
        "answer": answer,
        "sql": sql,
        "data": [row] if row else None,
        "row_count": 1 if row else 0,
        "hub_mode": True,
        "answer_scope": "gene_db",
        "answer_depth": depth,
        "agent_trace": [_trace_step("database", "ok" if row else "miss", "文献库计数", ms=0)],
        "specialists": ["database"],
        "task_type": "数据库查询",
    }


def run_scoped_chat(
    question: str,
    answer_scope: Optional[str] = None,
    answer_depth: Optional[str] = None,
    history: Optional[List[Any]] = None,
) -> dict:
    """按「回答范围」+「使用策略（部分/整体）」分发；支持多轮 history 展开追问。"""
    from zebrafish_prompt import (
        is_system_meta_question,
        is_capability_question,
        answer_capability,
        SYSTEM_META_REPLY,
        answer_citation_display_question,
        is_hub_product_howto_question,
        answer_hub_product_howto,
    )
    from gene_formatter import (
        is_biology_mechanism_question,
        is_citation_display_question,
        is_complaint_followup,
    )
    from intent_router import (
        classify_question_intent,
        is_general_lane_tier,
        TIER_E,
        safety_refusal_reply,
    )
    from common_utils import empty_result

    scope = normalize_answer_scope(answer_scope)
    depth = normalize_answer_depth(answer_depth)

    if is_citation_display_question(question):
        logger.info("[Hub] 引用展示元问题短路，不跑检索")
        return {
            **empty_result(),
            "answer": answer_citation_display_question(question, history),
            "hub_mode": True,
            "answer_scope": "meta",
            "answer_depth": depth,
            "intent_tier": "B",
            "skip_enrich": True,
            "agent_trace": [
                _trace_step("router", "ok", "citation_display_short_circuit", ms=0),
            ],
            "specialists": [],
        }

    if is_capability_question(question):
        logger.info("[Hub] 能力自述短路，不跑检索")
        return {
            **empty_result(),
            "answer": answer_capability(question),
            "hub_mode": True,
            "answer_scope": "meta",
            "answer_depth": depth,
            "intent_tier": "B",
            "skip_enrich": True,
            "agent_trace": [
                _trace_step("router", "ok", "capability_short_circuit", ms=0),
            ],
            "specialists": [],
            "task_type": "一般问答",
        }

    if is_hub_product_howto_question(question):
        logger.info("[Hub] 产品/界面用法短路，不跑文献")
        return {
            **empty_result(),
            "answer": answer_hub_product_howto(question),
            "hub_mode": True,
            "answer_scope": "meta",
            "answer_depth": depth,
            "intent_tier": "B",
            "skip_enrich": True,
            "agent_trace": [
                _trace_step("router", "ok", "product_howto_short_circuit", ms=0),
            ],
            "specialists": [],
            "task_type": "一般问答",
        }

    if is_complaint_followup(question):
        last_user = ""
        for item in reversed(history or []):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or item.get("text") or "").strip()
            if role in ("user", "human") and content and not is_complaint_followup(content):
                last_user = content
                break
        try:
            from zebrafish_prompt import answer_is_english

            _en = bool(answer_is_english(question))
        except Exception:
            _en = False
        if last_user and is_biology_mechanism_question(last_user):
            logger.info("[Hub] 抱怨追问：按上一科学题重跑文献")
            result = run_literature_only(last_user, depth=depth)
            prefix = (
                "The previous answer wrongly treated the question as a gene symbol for ID mapping. "
                "Below is a literature-based answer to the original question.\n\n"
                if _en
                else "上一答误把问句当成基因符号做了 ID 映射。下面按文献重新回答原问题。\n\n"
            )
            result["answer"] = prefix + (result.get("answer") or "")
            return result
        logger.info("[Hub] 抱怨追问短路")
        return {
            **empty_result(),
            "answer": (
                "The previous answer took a wrong path: it treated a full Chinese sentence as a "
                "gene symbol for historical gene-version unification, which led to "
                "“no local match”. Mechanism / transcription / duplication questions should "
                "use Hub full-pipeline literature search, with [n] title, journal, and year "
                "at the end (and a source link when a DOI exists). Please resend the original "
                "scientific question."
                if _en
                else (
                    "上一答走错了路：把整句中文当成基因符号做了「历史基因版本统一」，"
                    "所以会出现「未在本地库找到匹配」。机制/转录/复制类问题应走 Hub 全链路文献检索，"
                    "文末给出 [n] 题名、期刊、年份，有 DOI 时附原文链接。请把原来的科学问题再发一次。"
                )
            ),
            "hub_mode": True,
            "answer_scope": "meta",
            "answer_depth": depth,
            "intent_tier": "B",
            "skip_enrich": True,
            "agent_trace": [_trace_step("router", "ok", "complaint_short_circuit", ms=0)],
            "specialists": [],
        }

    if is_corpus_stats_question(question):
        logger.info("[Hub] 文献/库统计短路，直接查计数")
        return answer_corpus_stats(question, depth=depth)

    effective, hist, expanded = expand_question_with_history(question, history)
    if is_biology_mechanism_question(question):
        effective = question.strip()
        expanded = False
        hist = []
        if scope in ("id_map", "gene_db"):
            logger.info("[Hub] 机制题从 ID/基因库范围改走文献")
            scope = "literature"
    if expanded:
        logger.info(f"[Hub] 追问展开：{question[:60]} → {effective[:120]}")

    if is_capability_question(effective) or is_capability_question(question):
        logger.info("[Hub] 能力自述短路，跳过文献检索")
        result = {
            **empty_result(),
            "answer": answer_capability(question),
            "hub_mode": True,
            "answer_scope": "meta",
            "answer_depth": depth,
            "intent_tier": "B",
            "skip_enrich": True,
            "agent_trace": [
                _trace_step("router", "ok", "capability_short_circuit", ms=0),
            ],
            "specialists": [],
            "task_type": "一般问答",
        }
        if expanded:
            result["followup_expanded"] = True
            result["effective_question"] = effective
        return result

    if is_system_meta_question(effective) or is_system_meta_question(question):
        logger.info("[Hub] 系统元问题短路，跳过文献检索与脚注回填")
        result = {
            **empty_result(),
            "answer": SYSTEM_META_REPLY,
            "hub_mode": True,
            "answer_scope": "meta",
            "answer_depth": depth,
            "intent_tier": "B",
            "skip_enrich": True,
            "agent_trace": [
                _trace_step("router", "ok", "meta_short_circuit", ms=0),
            ],
            "specialists": [],
        }
        if expanded:
            result["followup_expanded"] = True
            result["effective_question"] = effective
        return result

    # 意图分层：C/D 不进 RAG；E 安全拒答；A 走原 Hub
    intent = classify_question_intent(effective, history=history)
    tier = intent.get("tier")
    logger.info(
        f"[Hub] intent={tier}/{intent.get('name')} conf={intent.get('confidence')} "
        f"范围={scope}（{ANSWER_SCOPES.get(scope)}）"
        f" 使用策略={depth}（{ANSWER_DEPTHS.get(depth)}）"
        f" history={len(hist)}"
    )
    if intent.get("tool_intent") == "coord_liftover":
        from coord_liftover import explain_coord_question

        lifted = explain_coord_question(effective)
        logger.info("[Hub] coord_liftover 不调用 sequence")
        try:
            from zebrafish_prompt import answer_is_english

            en = bool(answer_is_english(effective))
        except Exception:
            en = False
        answer = (
            "This is coordinate conversion, not sequence extraction. "
            "Please include the assembly and interval, e.g. GRCz11 chr5:24086227-24097805. "
            "The system will not call sequence, and will not treat sequence from a legacy "
            "interval as GRCz12tu."
            if en
            else (
                "这是坐标换算，不是取序列。"
                "请写明组装版本和区间，例如 GRCz11 chr5:24086227-24097805。"
                "系统不会调用 sequence，也不会把旧坐标上的序列当成 GRCz12tu。"
            )
        )
        extra = {}
        if lifted:
            answer = lifted.get("answer") or answer
            extra["liftover"] = lifted.get("liftover")
        return {
            **empty_result(),
            "answer": answer,
            "hub_mode": True,
            "answer_scope": scope,
            "answer_depth": depth,
            "intent": "coord_liftover",
            "intent_tier": tier,
            "skip_enrich": True,
            "tools_invoked": [],
            "sequence_blocked": True,
            "agent_trace": [
                _trace_step("router", "blocked", "coord_liftover_blocks_sequence", ms=0),
            ],
            "specialists": [],
            **extra,
        }
    if tier == TIER_E:
        return {
            **empty_result(),
            "answer": safety_refusal_reply(),
            "hub_mode": False,
            "answer_scope": "safety",
            "answer_depth": depth,
            "intent_tier": TIER_E,
            "skip_enrich": True,
            "agent_trace": [_trace_step("router", "ok", "safety_short_circuit", ms=0)],
            "specialists": [],
        }
    if is_general_lane_tier(tier):
        result = run_general_assistant(effective, tier=tier, history=hist)
        if expanded and isinstance(result, dict):
            result["followup_expanded"] = True
            result["effective_question"] = effective
        return result

    if scope == "literature":
        result = run_literature_only(effective, depth=depth)
    elif scope == "gene_db":
        result = run_gene_db_only(effective, depth=depth)
    elif scope == "id_map":
        result = run_id_map_only(effective, depth=depth)
    elif scope == "retrieval_debug":
        result = run_retrieval_debug_only(effective, depth=depth)
    else:
        result = run_hub_orchestration(effective, depth=depth)
    if isinstance(result, dict):
        result.setdefault("intent_tier", tier)
    if expanded and isinstance(result, dict):
        result["followup_expanded"] = True
        result["effective_question"] = effective
    return result


def hub_stats() -> dict:
    """Hub 仪表盘统计（产品化）。"""
    from gene_id_mapper import reference_genome_info
    from gene_toolkit import database_stats

    stats = {"reference": reference_genome_info(), "database": {}, "hub": {"agents": list(AGENT_LABELS.values())}}
    try:
        stats["database"] = database_stats()
    except Exception:
        pass

    try:
        from mysql_client import get_mysql_client
        from sqlalchemy import text

        db = next(get_mysql_client().get_db())
        for sql, key in (
            ("SELECT COUNT(*) AS n FROM gene_alias", "gene_alias"),
            ("SELECT COUNT(*) AS n FROM gene_xref", "gene_xref"),
            ("SELECT COUNT(*) AS n FROM gene_kegg", "gene_kegg"),
            ("SELECT COUNT(*) AS n FROM gene_kegg WHERE 1", "kegg_pathways_hint"),
            ("SELECT COUNT(DISTINCT pathway_id) AS n FROM gene_kegg", "kegg_pathway_count"),
            ("SELECT COUNT(*) AS n FROM gene_assembly_xref WHERE assembly_name='GRCz12tu'", "grcz12tu_mapped"),
        ):
            try:
                row = db.execute(text(sql)).mappings().first()
                stats["hub"][key] = int(row["n"] or 0)
            except Exception:
                stats["hub"][key] = 0
        db.close()
    except Exception:
        pass

    import os
    from config import _BASE_DIR

    cat = os.path.join(_BASE_DIR, "data", "literature", "literature_catalog.jsonl")
    if os.path.isfile(cat):
        try:
            with open(cat, encoding="utf-8") as f:
                stats["hub"]["literature_catalog_lines"] = sum(1 for _ in f)
        except OSError:
            stats["hub"]["literature_catalog_lines"] = 0
    try:
        from config import KNOWLEDGE_EXTRA_DIR

        if os.path.isdir(KNOWLEDGE_EXTRA_DIR):
            stats["hub"]["knowledge_d_files"] = sum(
                1
                for fn in os.listdir(KNOWLEDGE_EXTRA_DIR)
                if fn.endswith((".txt", ".md"))
            )
    except Exception:
        pass
    from datetime import datetime, timezone
    import time as _time

    stats["refreshed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stats["refreshed_ts"] = _time.time()
    stats["hub"]["multi_agent"] = True
    return stats
