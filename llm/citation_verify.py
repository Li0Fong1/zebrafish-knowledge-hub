# -*- coding: utf-8 -*-
"""文献标识符核验车道 —— 问「这篇论文讲了什么 / 确认一下这个 DOI」时用。

为什么单独开一条车道（2026-11-02 Chat 评测发现）：
问「确认这篇 2024 Nature 论文（DOI 10.1038/s41586-024-07891-2）讲了什么」，
系统看到句中有 tp53 之外的基因词就进了基因详情链路，最后返回 6180 字的基因卡片，
DOI 一次未提。这不是生成幻觉，是**检索错位**——用户要核验一篇具体的论文，
系统却给了另一个问题的答案。

本模块只做一件事：拿标识符去本地文献库（`literature_paper`）精确核验，
命中就给真实元数据，未命中就明确说「未收录、无法核实」，绝不推测题名与结论。
"""
from __future__ import annotations

import re
from typing import Any, Optional

# DOI：10.xxxx/后缀。后缀允许到空白/全角标点为止，避免把「讲了什么」吃进来。
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s，。、；;）)\]】\"'？?]+", re.I)

# PMID：带前缀（PMID: 123 / pubmed 123 / PMID 号 123）才算；
# 光秃秃一串数字可能是年份、坐标、基因号，不认。
_PMID_RE = re.compile(r"(?:pmid|pm\s*id|pubmed(?:\s*id)?)\s*[:：#]?\s*(\d{4,9})", re.I)

# 在问这篇论文本身（而不是在问「DOI 是什么格式」这类概念题）
_ABOUT_PAPER_RE = re.compile(
    r"(讲(了|的)?(是)?什么|说的什么|说(了|的)?(是)?什么|讲了?些?什么|"
    r"是什么(文章|论文|研究|东西|文献)|"
    r"确认|核实|核验|查一下|查查|查下|帮我查|是不是(真|存在)|是否(存在|收录|真实)|"
    r"有没有(这|这篇)|找不到|搜不到|哪(一|个)年|发表(在|于)?哪|出处|"
    r"这篇|这篇文章|这篇论文|这个\s*doi|这个\s*pmid|"
    r"what\s+(does|is|was)\b|what'?s\s+(this|the)\s+(paper|article|study)|"
    r"tell\s+me\s+about|about\s+this\s+(paper|article|study)|"
    r"verify|check\s+(this|whether)|does\s+this\s+exist|real\s+or\s+fake)",
    re.I,
)

# 概念/格式类提问：即使含 DOI 也不该走核验（如「DOI 命名规则」「怎么引用 DOI」）
_CONCEPT_RE = re.compile(
    r"(doi\s*(的)?(格式|规则|命名|构成|含义|怎么(写|生成|申请)|是什么(意思)?)|"
    r"什么(是|叫)\s*doi|how\s+to\s+(cite|format|get)\s+(a\s+)?doi|"
    r"(怎么|如何)(引用|引用文献)|reference\s+style|citation\s+format)",
    re.I,
)

# 明显是产品/界面问题，交给产品车道
_PRODUCT_RE = re.compile(
    r"(页面上|界面上|哪里(看|查|填|输入)|怎么用这个?网站|这个网站|本网站|你们(的)?网站)",
    re.I,
)


def extract_identifiers(question: str) -> dict[str, str]:
    """抽出 DOI / PMID；抽不到返回空串。DOI 去掉尾随标点。"""
    q = question or ""
    out = {"doi": "", "pmid": ""}
    m = _DOI_RE.search(q)
    if m:
        doi = m.group(0).rstrip(".,;:)]}>。，；：）】》")
        # 去掉常见的中文/英文尾缀
        out["doi"] = doi
    m2 = _PMID_RE.search(q)
    if m2:
        out["pmid"] = m2.group(1)
    return out


def is_citation_verify_question(question: str) -> bool:
    """有标识符实体 + 在问这篇论文本身，且不是概念题/界面题。"""
    q = (question or "").strip()
    if len(q) < 6:
        return False
    ids = extract_identifiers(q)
    if not (ids["doi"] or ids["pmid"]):
        return False
    if _CONCEPT_RE.search(q) or _PRODUCT_RE.search(q):
        return False
    return bool(_ABOUT_PAPER_RE.search(q))


def _query(client, sql: str, params: dict) -> list[dict[str, Any]]:
    """执行只读查询；库不可用时抛异常，不能把「查不了」当成「没有」。

    `MySQLClient.execute_sql` 失败时**不抛异常**，而是返回 `{"success": False}`。
    早期版本只取 `.get("data") or []`，于是连不上库时会拿到空列表，
    顺理成章地回答「这篇不在库里」——把故障说成了事实。
    """
    res = client.execute_sql(sql, params=params, max_rows=0)
    if res.get("success") is False:
        raise RuntimeError(str(res.get("error") or "数据库查询失败")[:200])
    return res.get("data") or []


def _lookup(ids: dict[str, str]) -> tuple[list[dict[str, Any]], str]:
    """先查主文献库，未命中再查基因文献表。

    两张表不是一回事：`literature_paper` 是可追加的全文/题录库，
    `ncbi_gene_bibliography` 是基因卡片右侧参考文献的来源。
    用户刚在卡片上看到 PMID，转头问「这篇讲了什么」，
    如果只查第一张表就会答「不在库里」，自相矛盾。
    """
    where, params = [], {}
    if ids.get("doi"):
        where.append("LOWER(doi) = :doi")
        params["doi"] = ids["doi"].lower()
    if ids.get("pmid"):
        where.append("pmid = :pmid")
        params["pmid"] = str(ids["pmid"])
    if not where:
        return [], ""

    from mysql_client import get_mysql_client

    client = get_mysql_client()
    sql = (
        "SELECT pmid, doi, title_en, title_zh, journal, year, has_fulltext, gold_status"
        " FROM literature_paper WHERE " + " OR ".join(where) + " LIMIT 5"
    )
    rows = _query(client, sql, params)
    if rows:
        return [dict(r, _src="lib") for r in rows], "lib"

    if ids.get("pmid"):
        sql2 = (
            "SELECT pmid, symbol, title, pubdate FROM ncbi_gene_bibliography"
            " WHERE pmid = :pmid LIMIT 5"
        )
        rows2 = _query(client, sql2, {"pmid": str(ids["pmid"])})
        if rows2:
            return [dict(r, _src="bib") for r in rows2], "bib"
    return [], ""


def _fmt_hit(row: dict[str, Any]) -> str:
    title = str(row.get("title_en") or row.get("title_zh") or row.get("title") or "").strip()
    parts = []
    if title:
        parts.append(f"**{title}**")
    meta = []
    for key in ("journal", "year", "pubdate"):
        if row.get(key):
            meta.append(str(row[key]).strip())
    if meta:
        parts.append(" · ".join(meta))
    links = []
    if row.get("doi"):
        links.append(f"DOI: {row['doi']}")
    if row.get("pmid"):
        links.append(f"PMID: {row['pmid']}")
    if row.get("symbol"):
        links.append(f"NCBI Gene: {row['symbol']}")
    if links:
        parts.append("，".join(links))
    return "；".join(p for p in parts if p)


# DOI 前缀 ↔ 出版方（只列归属明确的几族，用来发现「DOI 与期刊串了」的脏记录）。
# 宁可漏判也不要误报：对不出家族的 DOI 一律不表态。
_DOI_FAMILY = [
    (re.compile(r"^10\.1371/journal\.pbio", re.I), ("plos",), "PLOS Biology"),
    (re.compile(r"^10\.1371/journal\.", re.I), ("plos",), "PLOS"),
    (re.compile(r"^10\.1038/", re.I), ("nature",), "Nature Portfolio"),
    (
        re.compile(r"^10\.1124/", re.I),
        ("molecular pharmacology", "pharmacological", "drug metabolism", "aspet"),
        "ASPET（Molecular Pharmacology 等）",
    ),
    (
        re.compile(r"^10\.1242/", re.I),
        ("journal of cell science", "development", "disease models", "biology open"),
        "The Company of Biologists",
    ),
    (
        re.compile(r"^10\.1093/", re.I),
        ("nucleic acids", "human molecular", "bioinformatics", "oxford", "g3", "genetics"),
        "Oxford University Press",
    ),
    (re.compile(r"^10\.1016/", re.I), ("elsevier", "cell", "journal of", "biochemical"), "Elsevier"),
]


def _doi_family_mismatch(row: dict[str, Any]) -> str:
    """DOI 前缀的出版方与该行期刊明显不符时返回说明，否则空串。"""
    doi = str(row.get("doi") or "").strip()
    journal = str(row.get("journal") or "").strip().lower()
    if not doi or not journal:
        return ""
    for pat, needles, label in _DOI_FAMILY:
        if pat.search(doi):
            if not any(n in journal for n in needles):
                return f"DOI 前缀属 {label}，但这行写的期刊是「{row.get('journal')}」"
            return ""
    return ""


def _dedup_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一篇在库里常有多条（期刊大小写、PMID 有无不同），按主键去重。

    有 DOI 时以 DOI 为准：同一 DOI 就是同一篇，PMID 字段缺失不该被当成另一条。
    """
    seen: dict[tuple, dict[str, Any]] = {}
    order: list[tuple] = []
    for r in rows:
        doi = str(r.get("doi") or "").strip().lower()
        if doi:
            key = ("doi", doi)
        else:
            title = re.sub(
                r"\s+",
                " ",
                str(r.get("title_en") or r.get("title_zh") or r.get("title") or ""),
            ).strip().lower()
            key = ("title", title, str(r.get("pmid") or "").strip())
        if key not in seen:
            seen[key] = r
            order.append(key)
            continue
        # 同一篇的多条记录里留字段最全的那条（有的行缺 PMID 或缺全文标记）
        if _filled(r) > _filled(seen[key]):
            seen[key] = r
    return [seen[k] for k in order]


def _filled(row: dict[str, Any]) -> int:
    return sum(1 for v in row.values() if str(v or "").strip())


def answer_citation_verify(question: str) -> tuple[str, dict[str, Any]]:
    """返回 (答文, 元信息)。元信息里 hit / kind 用于埋轨迹。"""
    ids = extract_identifiers(question or "")
    kind = "doi" if ids["doi"] else "pmid"
    value = ids["doi"] or ids["pmid"]
    en = bool(re.search(r"[A-Za-z]{4,}", question or "")) and not re.search(
        r"[\u4e00-\u9fff]", question or ""
    )

    try:
        rows, src = _lookup(ids)
    except Exception as exc:  # 库连不上时不能假装查过
        msg = (
            f"The literature library is currently unreachable ({type(exc).__name__}), "
            f"so I cannot verify {kind.upper()} {value}. I will not guess its title or findings."
            if en
            else f"本地文献库当前连不上（{type(exc).__name__}），无法核实 {kind.upper()} {value}。"
            "我不会凭印象替你编造题名或结论。"
        )
        return msg, {"hit": False, "kind": kind, "value": value, "error": type(exc).__name__}

    if rows:
        rows = _dedup_rows(rows)
        lines = []
        if en:
            lines.append(f"Found in the local literature library — {kind.upper()} {value}:")
        else:
            lines.append(f"本地文献库里有这条记录（{kind.upper()} {value}）：")
        lines.append("")
        warns = []
        for r in rows[:3]:
            lines.append("- " + _fmt_hit(r))
            bad = _doi_family_mismatch(r)
            if bad:
                warns.append(bad)
        full = any(str(r.get("has_fulltext") or "").lower() in ("1", "true", "yes") for r in rows)
        lines.append("")
        if warns:
            lines.append(
                "**Caveat**: " + "；".join(warns) + ". That DOI/journal pairing looks wrong, so I "
                "will not treat this record's metadata as fact — verify at https://doi.org/. "
                "I am not going to repeat a bad record as if it were checked."
                if en
                else "**但这条记录本身有问题**：" + "；".join(warns) + "。"
                "这个 DOI 与期刊对不上，说明库里这条可能串了行。"
                "我不会把它当事实转述——请到 https://doi.org/ 核对该标识符。"
            )
            lines.append("")
        elif full:
            lines.append(
                "The full text is in the library, so I can answer what the paper does from the "
                "stored sections — ask me the specific question and I will quote it."
                if en
                else "该篇在库内有全文，可以直接问它的具体结论，我会按库内正文回答并标注段落来源。"
            )
        elif src == "bib":
            lines.append(
                "This entry comes from the NCBI Gene bibliography attached to a gene card — "
                "title and date only, no abstract or full text, so I will not summarise findings "
                "the library does not hold."
                if en
                else "这条来自基因卡片右侧的 NCBI Gene 参考文献表，库里只有题名与日期、"
                "没有摘要和正文，所以我能确认它被收录过，但不会替它编结论。"
            )
        else:
            lines.append(
                "Only the bibliographic record is stored (no full text), so I can confirm the "
                "paper exists and give its title/journal/year — but not summarise findings the "
                "library does not contain."
                if en
                else "库里只有题录、没有这篇的全文，所以我能确认它存在并给出题名/期刊/年份，"
                "但不会替它编摘要——正文不在库里，编出来的结论就不可靠。"
            )
        return "\n".join(lines), {
            "hit": True,
            "kind": kind,
            "value": value,
            "n": len(rows),
            "src": src,
        }

    # 未命中：明确说没收录，并给出可自行核验的路径，但不猜内容
    if kind == "doi":
        link = f"https://doi.org/{value}"
    else:
        link = f"https://pubmed.ncbi.nlm.nih.gov/{value}/"
    if en:
        msg = (
            f"Not found: DOI/PMID `{value}` is **not in this Hub's literature library**, "
            f"so I cannot confirm what it says or whether it exists.\n\n"
            f"I am not going to invent a title or findings for it. You can verify it yourself at {link}.\n\n"
            f"If you check it there and want the zebrafish angle, tell me the real title and I will "
            f"search the library for related papers that *are* indexed."
        )
    else:
        msg = (
            f"没查到：`{value}` **不在本 Hub 的文献库里**，所以我无法确认它讲了什么，"
            f"也无法确认它是否真实存在。\n\n"
            f"这类题我不会编题名或结论来凑答案。要自行核验可打开 {link}。\n\n"
            f"如果你在那边确认过，把真实题名告诉我，我可以在已入库的文献里找相关的研究。"
        )
    return msg, {"hit": False, "kind": kind, "value": value, "n": 0}
