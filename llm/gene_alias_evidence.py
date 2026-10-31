# -*- coding: utf-8 -*-
"""任务① 基因别名（含历史名）+ 任务② 复制体/共直系同源：证据构建与提示词块。

设计要点（与用户确认过的方案一致）：
  · 只在「基因身份 / 别名 / 历史名」类问题触发别名检索，普通问题不额外增加耗时；
  · 文献先做**题名级**本地匹配（literature_paper.title_en / title_zh，一次全表扫描，秒级），
    命中别名再交给向量检索补证据（由 hub_orchestrator._run_literature 追加别名查询）；
  · 复制体关系只用本地 homology_gene 推导（同一物种 + 同一同源基因名），不联网、不编造；
  · 提示词里明确要求：**第一句列出该基因的所有历史别名**，并给出别名—文献的对应关系。

注意：本地 literature_paper 表只有题名/年份/期刊等字段（没有摘要列），
      所以这里的“题名级命中”是可核验的口径，不会宣称是全文检索结果。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from common_utils import logger

# 触发词：问的是「这个基因叫什么/有哪些别名/历史名」这类身份问题
ALIAS_INTENT_RE = re.compile(
    r"(别名|又名|又称|也叫|曾用名|旧名|历史名|同义名|另名|别的名字|其他名字|"
    r"叫什么|命名|改名|更名|标准名|符号|symbol|"
    r"alias|aliases|synonym|synonyms|formerly|also\s+known\s+as|a\.k\.a|"
    r"previous\s+name|renamed|gene\s+name)",
    re.I,
)

# 同源 / 复制体意图：即使不是别名问题，也要把复制体关系展示出来
ORTHO_INTENT_RE = re.compile(
    r"(同源|直系同源|共直系|复制体|复制基因|旁系同源|亚功能化|新功能化|功能分工|"
    r"ortholog|co-?ortholog|paralog|ohnolog|duplicat|subfunctional|neofunctional|"
    r"human\s+ortholog|mouse\s+ortholog)",
    re.I,
)


# 用户**明确**问「某个写法在文献里出现多少次 / 文献里怎么用」时才需要本地语料统计
CORPUS_OCCURRENCE_RE = re.compile(
    r"(多少篇|几篇|出现过?|出现过多少次|命中(?:多少|几次|篇)?|"
    r"用过几次|使用频率|常见写法|常见称呼|语料|全文(?:中|里)|文献(?:中|里)|哪些文献|"
    r"how\s+many\s+papers|how\s+often|hit\s+count|occurrence|mentioned\s+in|used\s+in\s+the\s+literature)",
    re.I,
)


def asks_corpus_occurrence(question: str) -> bool:
    """是否要本地语料命中统计。默认 False——别名问题只回答别名本身。"""
    return bool(question) and bool(CORPUS_OCCURRENCE_RE.search(question))


def is_alias_question(question: str) -> bool:
    return bool(question) and bool(ALIAS_INTENT_RE.search(question))


def is_ortholog_question(question: str) -> bool:
    return bool(question) and bool(ORTHO_INTENT_RE.search(question))


def _resolve_gene(question: str, gene_detail: Optional[dict] = None) -> Optional[str]:
    """从 gene_detail 或问题文本里拿到基因名。"""
    if gene_detail:
        basic = gene_detail.get("basic") or {}
        for key in ("gene_symbol", "gene_name"):
            v = (basic.get(key) or "").strip()
            if v:
                return v
    try:
        from gene_toolkit import extract_gene_from_question

        return extract_gene_from_question(question)
    except Exception:
        return None


def lookup_aliases(gene: str) -> dict:
    """别名块（含历史名与来源）。找不到基因时返回空结构，不抛错。"""
    empty = {"gene": gene, "gene_id": None, "items": [], "groups": {}, "count": 0}
    if not gene:
        return empty
    try:
        from gene_toolkit import alias_block, gene_id_of

        gid = gene_id_of(gene)
        if not gid:
            return empty
        blk = alias_block(gid)
        blk["gene"] = gene
        blk["gene_id"] = gid
        return blk
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[别名] 查询失败：{e}")
        return empty


def alias_title_evidence(
    aliases: List[str],
    *,
    max_aliases: int = 24,
    samples: int = 2,
    row_cap: int = 2000,
) -> List[dict]:
    """题名级别名—文献匹配：一次全表扫描，统计每个别名在题名中的命中数。

    返回 [{'alias','count','samples':[{'pmid','title','year','journal'}]}]，按命中数降序。
    """
    picks: List[str] = []
    seen = set()
    for a in aliases or []:
        s = (a or "").strip()
        if len(s) < 3 or s.lower() in seen:
            continue
        seen.add(s.lower())
        picks.append(s)
    picks = picks[:max(1, int(max_aliases))]
    if not picks:
        return []

    clauses, params = [], {}
    for i, a in enumerate(picks):
        k = "a%d" % i
        params[k] = "%" + a + "%"
        clauses.append(f"(title_en LIKE :{k} OR title_zh LIKE :{k})")
    sql = (
        "SELECT pmid, doc_id, entry_no, title_en, title_zh, year, journal "
        "FROM literature_paper WHERE " + " OR ".join(clauses) + f" LIMIT {int(row_cap)}"
    )
    try:
        from mysql_client import get_mysql_client

        res = get_mysql_client().execute_sql(sql, params=params, max_rows=int(row_cap))
        rows = (res.get("data") or []) if res.get("success") else []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[别名] 题名匹配失败：{e}")
        return []

    out: List[dict] = []
    lowered = [(a, a.lower()) for a in picks]
    for a, lo in lowered:
        # 词边界：否则 p53 会把 tp53 也算进去（本轮实测：169 vs 真实的 28 篇）
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(lo) + r"(?![a-z0-9])")
        hits = []
        for r in rows:
            t = ((r.get("title_en") or "") + " " + (r.get("title_zh") or "")).lower()
            if pat.search(t):
                hits.append(r)
        if not hits:
            continue
        # 取样优先给带 PMID / DOI 的记录，便于用户直接核对原文
        hits.sort(key=lambda r: (0 if str(r.get("pmid") or "").strip() else 1,
                                0 if str(r.get("doi") or "").strip() else 1))
        out.append({
            "alias": a,
            "count": len(hits),
            "samples": [
                {
                    "pmid": str(r.get("pmid") or "").strip(),
                    "doi": str(r.get("doi") or "").strip(),
                    "title": (r.get("title_en") or r.get("title_zh") or "")[:120],
                    "year": str(r.get("year") or ""),
                    "journal": (r.get("journal") or "")[:60],
                }
                for r in hits[:max(1, int(samples))]
            ],
        })
    out.sort(key=lambda x: -x["count"])
    return out


def build_alias_evidence(question: str, gene_detail: Optional[dict] = None) -> Optional[dict]:
    """别名问题的完整证据包。

    文献证据分两级（**按需触发**：只有别名/身份类问题才会走到这里，普通问题零开销）：
      · fulltext —— 读离线倒排表 gene_alias_doc / gene_alias_lit_stat，
                    给出「题名 / 摘要 / 全文」三级命中数 + 带片段的样本（首选）；
      · title    —— 该基因还没建倒排表时的**降级**路径：只扫题名（3 万行一次扫描，秒级）。
    两条路都只报事实：没命中就说没命中，不编造。
    """
    gene = _resolve_gene(question, gene_detail)
    if not gene:
        return None
    blk = lookup_aliases(gene)
    if not blk.get("items"):
        return {"gene": gene, "aliases": blk, "literature": [], "top_aliases": [],
                "level": "none", "note": "本地库没有该基因的别名记录"}

    # 用户只问别名（没问"在文献里出现多少次"）→ 不查本地语料，也就不会把命中统计带进回答
    if not asks_corpus_occurrence(question):
        return {"gene": gene, "aliases": blk, "literature": [], "top_aliases": [],
                "level": "aliases_only", "note": ""}

    ft = None
    try:
        from gene_alias_fulltext import alias_fulltext_evidence

        ft = alias_fulltext_evidence(gene)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[别名] 全文级证据不可用，降级为题名级：{e}")

    if ft and ft.get("items"):
        tops = [{"alias": i["alias"], "count": i["total_docs"]} for i in ft["items"]]
        return {"gene": gene, "aliases": blk, "fulltext": ft, "literature": [],
                "top_aliases": tops, "level": "fulltext", "note": ""}

    lit = alias_title_evidence([it["alias"] for it in blk["items"]])
    return {"gene": gene, "aliases": blk, "literature": lit,
            "top_aliases": [{"alias": r["alias"], "count": r["count"]} for r in lit],
            "level": "title", "note": ""}


def extra_alias_queries(question: str, gene_detail: Optional[dict] = None, *, limit: int = 2) -> List[str]:
    """给向量检索用的追加查询：命中最多的历史别名（不含标准符号）。"""
    ev = build_alias_evidence(question, gene_detail)
    if not ev:
        return []
    std = ((ev.get("aliases") or {}).get("standard_symbol") or ev.get("gene") or "").lower()
    out: List[str] = []
    for row in (ev.get("top_aliases") or []):
        a = row.get("alias") or ""
        if a.lower() == std or int(row.get("count") or 0) <= 0:
            continue
        out.append(a)
        if len(out) >= max(1, int(limit)):
            break
    return out


# ---------------------------------------------------------------------------
# 提示词块
# ---------------------------------------------------------------------------

def _alias_counts(items: List[dict], standard_symbol: str = "") -> tuple:
    """(总条数, 标准符号条数, 别名/历史名条数)。

    为什么必须有：2026-10-28 线上实测，模型把 8+4=12 条写成「15 个别名…含标准符号共 16 个」——
    数字由模型自己数就会错。这里先算好，再在提示词里给硬约束。
    """
    std_low = (standard_symbol or "").strip().lower()
    n_std = 0
    for it in items or []:
        alias_low = (it.get("alias") or "").strip().lower()
        label = it.get("source_label") or ""
        if it.get("is_primary") or "标准" in label or (std_low and alias_low == std_low):
            n_std += 1
    total = len(items or [])
    return total, n_std, max(0, total - n_std)


def format_alias_evidence_block(ev: dict, lang: str = "zh", question: str = "") -> str:
    """把别名证据写成提示词块（中英各一套措辞）。

    question：用来判断用户是否**明确**要本地语料命中统计；默认只给别名清单 + 来源
    （2026-10-28：别名类问题不再附带本地语料命中篇数）。
    """
    if not ev:
        return ""
    aliases = ev.get("aliases") or {}
    items = aliases.get("items") or []
    if not items:
        return ""
    en = (lang or "zh").lower().startswith("en")
    want_stats = asks_corpus_occurrence(question)

    grouped: Dict[str, List[str]] = {}
    for it in items:
        grouped.setdefault(it.get("source_label") or it.get("source") or "?", []).append(it["alias"])
    total, n_std, n_alias = _alias_counts(items, aliases.get("standard_symbol") or ev.get("gene") or "")
    if en:
        alias_lines = "; ".join("%s (%d): %s" % (k, len(v), " / ".join(v)) for k, v in grouped.items())
        pre = (
            "Gene: %s. Local records: %d in total = %d standard symbol(s) + %d alias/historical name(s).\n"
            "  By source: %s\n"
            "【NUMBER RULE】Every count in the answer must equal these numbers "
            "(%d incl. the standard symbol, %d excluding it); per-source counts must match the list. "
            "Never recompute or report a different total.\n"
            % (ev.get("gene"), total, n_std, n_alias, alias_lines, total, n_alias)
        )
    else:
        alias_lines = "; ".join("%s（%d 个）: %s" % (k, len(v), " / ".join(v)) for k, v in grouped.items())
        pre = (
            "基因：%s。本地权威记录共 %d 条 = 标准符号 %d 个 + 别名/历史名 %d 个。\n"
            "  按来源：%s\n"
            "【数字约束】回答里出现的数量必须等于这里的数字（共 %d 条；不含标准符号 %d 个），"
            "各来源分组的个数以清单为准；不要自己相加、也不要另报一个数。\n"
            % (ev.get("gene"), total, n_std, n_alias, alias_lines, total, n_alias)
        )

    # 全文级证据可用时，直接交给 gene_alias_fulltext 出块（自带完整别名清单）
    ft = ev.get("fulltext") or {}
    if ev.get("level") == "fulltext" and ft.get("items"):
        from gene_alias_fulltext import format_fulltext_block

        return format_fulltext_block(ft, lang, alias_lines=pre, want_stats=want_stats)

    if not want_stats:
        # 题名级：同样不输出本地命中统计
        if en:
            return (
                pre
                + "【SCOPE】The user asked for the aliases only. List every alias / historical name "
                "with its source, using exactly the numbers above. Do NOT report any local-corpus "
                "hit statistics, do NOT write 'no local record' for an alias, and do NOT split the "
                "list into seen/unseen groups. No content unrelated to the question.\n"
            )
        return (
            pre
            + "【回答范围】用户问的就是别名：给出全部别名与历史名并标注来源，数量用上面的数字。"
            "禁止输出任何本地语料命中统计（各写法命中多少篇、“本地库未见记录”、"
            "“未出现过”之类），也不要把别名拆成「出现过 / 没出现过」两组；"
            "不要输出与问题无关的内容。\n"
        )

    lit = ev.get("literature") or []
    lit_lines = []
    for row in lit[:12]:
        smp = row.get("samples") or []
        ex = ""
        if smp:
            s = smp[0]
            cite = ("PMID %s" % s["pmid"]) if s.get("pmid") else (
                ("DOI %s" % s["doi"]) if s.get("doi") else "无 PMID/DOI")
            ex = " | e.g. %s (%s) %s" % (cite, s.get("year") or "", (s.get("title") or "")[:80])
        lit_lines.append("  - %s → %d title hits%s" % (row.get("alias"), row.get("count", 0), ex))

    if en:
        head = (
            "【GENE ALIAS EVIDENCE · cached locally, do not invent】\n"
            f"Gene: {ev.get('gene')} (local library). All recorded aliases/historical names:\n"
            f"  {alias_lines}\n"
        )
        if lit_lines:
            head += (
                "Title-level counts in the local library (25k+ records; title match only, NOT full text):\n"
                + "\n".join(lit_lines) + "\n"
            )
        else:
            head += "Title-level counts: none — no local title contains these aliases (say so, do not invent).\n"
        head += (
            "REQUIRED FORMAT: the FIRST sentence of your answer must list ALL historical aliases "
            "(with sources: ZFIN / NCBI / UniProt / Ensembl), e.g. "
            "\"This gene is known as X, previously also called a / b / c (ZFIN, NCBI).\" "
            "Then state which aliases actually appear in the local literature.\n"
        )
        return head

    head = (
        "【基因别名证据 · 来自本地库，禁止编造】\n"
        f"基因：{ev.get('gene')}（本地库）。全部别名与历史名：\n  {alias_lines}\n"
    )
    if lit_lines:
        head += (
            "本地库题名级命中（3 万余篇文献，仅题名匹配，不是全文检索）：\n"
            + "\n".join(lit_lines) + "\n"
        )
    else:
        head += "本地库题名级命中：无（没有任何题名包含这些别名，请如实说明，不要编造）。\n"
    head += (
        "【强制格式】回答的**第一句**必须列出该基因的全部历史别名，并标注来源"
        "（ZFIN / NCBI / UniProt / Ensembl），例如“……该基因标准名为 X，历史上还被称为 a / b / c"
        "（ZFIN、NCBI）”。随后再用一句话说明哪些别名在本地文献题名中真正出现过。\n"
    )
    return head


def format_paralog_prompt_block(paralogs: dict, lang: str = "zh",
                                role_ev: Optional[dict] = None) -> str:
    """复制体（共直系同源）证据块：人/鼠一个基因 ↔ 斑马鱼多个复制体。

    role_ev：可选，每个复制体**各自**的文献证据
    （`gene_alias_fulltext.copy_role_evidence`），用来支撑「哪个复制体保留了哪个功能」。
    """
    if not paralogs:
        return ""
    groups = paralogs.get("groups") or []
    if not groups:
        return ""
    en = (lang or "zh").lower().startswith("en")
    lines = []
    copies_all: List[str] = []
    for g in groups[:4]:
        cs = (g.get("copies") or [])[:6]
        copies = "、".join(
            "%s%s（%.1f%s）" % (
                c.get("symbol") or c.get("gene_id"),
                "*" if c.get("is_self") else "",
                float(c.get("score") or 0),
                "，本基因" if c.get("is_self") else "",
            )
            for c in cs
        )
        for c in cs:
            s = c.get("symbol")
            if s and s not in copies_all:
                copies_all.append(s)
        lines.append("  - %s 的 %s 同源 → 斑马鱼侧 %d 个共直系同源：%s" % (
            g.get("species"), g.get("homo_gene_name"), g.get("n_copies") or 0, copies))

    # 每个复制体自己的文献证据（有就附上，没有就要求写成「证据不足」）
    ev_lines: List[str] = []
    if role_ev:
        try:
            from gene_alias_fulltext import format_copy_role_lines

            ev_lines = format_copy_role_lines(role_ev, copies_all, lang)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[复制体] 分工证据拼接失败（不影响回答）：{e}")
    ev_block = ""
    if ev_lines:
        ev_block = (("\nEach copy's own local literature evidence:\n" if en
                     else "\n各复制体**各自**的本地文献证据：\n") + "\n".join(ev_lines) + "\n")
    if en:
        return (
            "【TELEOST WGD · duplicate / co-ortholog evidence, local homology_gene】\n"
            + "\n".join(lines) + "\n" + ev_block
            + ("These are co-orthologs of a single human/mouse gene (a hallmark of the teleost "
               "whole-genome duplication). Functional partitioning (sub/neofunctionalization) must be "
               "judged ONLY from the literature listed below; if evidence is insufficient, say so.\n"
               if paralogs.get("is_duplicated") else
               "No one-to-many duplication detected for this gene.\n")
            + "REQUIRED: when duplicates exist, add a short section "
              "\"Genome duplication & functional partitioning\" containing:\n"
              "  (1) a small table: zebrafish copy | human/mouse ortholog | identity | evidence;\n"
              "  (2) a per-copy CONCLUSION on which function each copy retained / lost / gained, "
              "each conclusion followed by its PMID/DOI; write \"not reported\" when the local "
              "literature gives no support — never invent a function split.\n"
        )
    return (
        "【硬骨鱼全基因组复制 · 复制体/共直系同源证据，来自本地 homology_gene】\n"
        + "\n".join(lines) + "\n" + ev_block
        + ("这些是同一个人类/小鼠基因的共直系同源（硬骨鱼全基因组复制的典型结果）；"
           "功能分工（亚功能化/新功能化）只能依据**下面列出的文献**判断，证据不足就写证据不足。\n"
           if paralogs.get("is_duplicated") else
           "该基因未检测到一对多的复制关系。\n")
        + "【强制格式】存在复制体时，回答中要加一个小节「基因组复制与分工」，内容包含：\n"
          "  （1）小表格：斑马鱼复制体 | 人/鼠同源基因 | 相似度 | 证据文献编号；\n"
          "  （2）**逐条给出功能分工结论**：哪个复制体保留/承担了哪种功能、哪个丢失或获得了新功能，"
          "每条结论后面必须紧跟证据文献编号（PMID/DOI）；\n"
          "  （3）本地文献没有支持的，明确写「未报道/证据不足」，**绝不允许编造功能分工**。\n"
    )
