# -*- coding: utf-8 -*-
"""回答后处理：正文实体双链、资料库合并补全、跨库映射、序列链、用到的库清单。

本地深链格式：#/gene/<symbol> 、#/sequence/<symbol> 、#/id/<token>
外链：ZFIN / Ensembl / NCBI / UniProt / GO / KEGG / Bgee / PubMed 等。
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import quote

# 避免重复包裹已是 markdown 链接的片段
_LINKED_SPAN = re.compile(r"\[[^\]]+\]\([^)]+\)")
_ENSEMBL_RE = re.compile(r"\b(ENS[A-Z]{0,4}G\d{8,}(?:\.\d+)?)\b", re.I)
_ZFIN_RE = re.compile(r"\b(ZDB-GENE-\d+-\d+)\b", re.I)
_UNIPROT_RE = re.compile(
    r"\b([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})\b"
)
_NCBI_GENE_RE = re.compile(r"\b(?:NCBI\s*Gene|GeneID|Entrez)[:\s#]*(\d{4,9})\b", re.I)
_GO_RE = re.compile(r"\b(GO:\d{7})\b", re.I)
_KEGG_RE = re.compile(r"\b(dre\d{5}|map\d{5}|ko\d{5})\b", re.I)
_REFSEQ_RE = re.compile(r"\b((?:NM|NR|NP|XM|XR|NC)_\d+(?:\.\d+)?)\b", re.I)
_GEO_RE = re.compile(r"\b(GSE\d+|GSM\d+|GDS\d+)\b", re.I)
_WP_RE = re.compile(r"\b(WP\d{4,6})\b", re.I)
_AE_RE = re.compile(r"\b(E-(?:GEOD|MTAB|MEXP|TABM)-\d+)\b", re.I)


def _is_zh() -> bool:
    """本次回答是不是中文（由请求入口按「用户点名 > 界面语言 > 提问语言」定好）。"""
    try:
        from zebrafish_prompt import current_answer_language

        return current_answer_language("") != "en"
    except Exception:  # noqa: BLE001
        return True


def _t(zh: str, en: str) -> str:
    """后处理追加的段落：中文答案给中文，英文答案给英文（不再中英混排）。"""
    return zh if _is_zh() else en

# 常见斑马鱼基因符号（小写匹配）；动态还会从 gene_detail / 问句抽取
_STOP = {
    "the", "and", "for", "with", "from", "this", "that", "gene", "genes",
    "protein", "zebrafish", "danio", "rerio", "expression", "pathway",
    "function", "mutation", "sequence", "dna", "rna", "mrna", "cds",
}


def _ext(db: str, db_id: str) -> Optional[str]:
    try:
        from gene_id_mapper import external_url

        return external_url(db, db_id)
    except Exception:
        return None


def _bgee_gene_url(ensembl_id: str) -> Optional[str]:
    eid = re.sub(r"\.\d+$", "", (ensembl_id or "").strip())
    if not eid:
        return None
    return f"https://www.bgee.org/gene/{eid}"


def _atlas_gene_url(ensembl_id: str) -> Optional[str]:
    eid = re.sub(r"\.\d+$", "", (ensembl_id or "").strip())
    if not eid:
        return None
    return f"https://www.ebi.ac.uk/gxa/genes/{eid}"


def _quickgo_url(gid: str) -> str:
    return f"https://www.ebi.ac.uk/QuickGO/term/{gid}"


def _kegg_url(kid: str) -> str:
    return f"https://www.kegg.jp/pathway/{kid}"


def _geo_url(acc: str) -> str:
    return f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}"


def _wikipathways_url(wp: str) -> str:
    return f"https://www.wikipathways.org/pathways/{wp.upper()}.html"


def _arrayexpress_url(acc: str) -> str:
    return f"https://www.ebi.ac.uk/biostudies/arrayexpress/studies/{acc}"


def local_gene_url(symbol: str) -> str:
    return f"#/gene/{quote(str(symbol).strip())}"


def local_seq_url(symbol: str) -> str:
    return f"#/sequence/{quote(str(symbol).strip())}"


def local_id_url(token: str) -> str:
    return f"#/id/{quote(str(token).strip())}"


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


class _LinkBudget:
    """同一 URL / 同一本地深链在整篇回答中至多出现一次超链。"""

    def __init__(self) -> None:
        self.used: set[str] = set()

    def claim(self, url: str) -> bool:
        key = _norm_url(url)
        if not key or key in self.used:
            return False
        self.used.add(key)
        return True

    def available(self, url: str) -> bool:
        key = _norm_url(url)
        return bool(key) and key not in self.used


def _md_dual(
    label: str,
    local: str,
    externals: list[tuple[str, str]],
    budget: Optional[_LinkBudget] = None,
) -> str:
    """本系统优先 + 旁路外站；budget 开启时每个 URL 仅首次成链，后续回落纯文本。"""
    if budget is None:
        parts = [f"[{label}]({local})"] if local else [label]
        for name, url in externals:
            if url:
                parts.append(f"[{name}]({url})")
        if len(parts) == 1:
            return parts[0]
        return parts[0] + "（" + " · ".join(parts[1:]) + "）"

    ext_parts: list[str] = []
    for name, url in externals:
        if url and budget.claim(url):
            ext_parts.append(f"[{name}]({url})")

    if local and budget.claim(local):
        head = f"[{label}]({local})"
    else:
        head = label

    if not ext_parts:
        return head
    return head + "（" + " · ".join(ext_parts) + "）"


def _gene_ext_pairs(detail: dict) -> list[tuple[str, str]]:
    """从 gene_detail 收集可挂外链的组学库（有 ID 才挂）。"""
    b = (detail.get("basic") or {}) if detail else {}
    std = (detail.get("standard") or {}) if detail else {}
    pairs: list[tuple[str, str]] = []
    seen_url: set[str] = set()

    def add(name: str, url: Optional[str]) -> None:
        u = _norm_url(url or "")
        if not u or u in seen_url:
            return
        seen_url.add(u)
        pairs.append((name, u))

    if b.get("zfin_id"):
        add("ZFIN", _ext("zfin", b["zfin_id"]))
    if b.get("ensembl_id"):
        add("Ensembl", _ext("ensembl", b["ensembl_id"]))
    ncbi = b.get("ncbi_gene_id") or std.get("ncbi_gene_id")
    if not ncbi:
        for x in detail.get("xrefs") or []:
            db = str(x.get("db_name") or "").lower()
            if db in ("ncbi_gene", "ncbi", "entrez") and str(x.get("db_id") or "").isdigit():
                ncbi = x.get("db_id")
                break
    if ncbi:
        add("NCBI Gene", _ext("ncbi_gene", str(ncbi)))
    if b.get("uniprot_id"):
        add("UniProt", _ext("uniprot", b["uniprot_id"]))
    if b.get("ensembl_id") and (detail.get("expression") or b.get("expression_summary")):
        add("Bgee", _bgee_gene_url(b["ensembl_id"]))
        add("Expression Atlas", _atlas_gene_url(b["ensembl_id"]))
    if std.get("refseq_id"):
        add("RefSeq", _ext("refseq", std["refseq_id"]))
    return pairs


def _collect_gene_symbols(question: str, answer: str, gene_detail: Optional[dict]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        n = (name or "").strip()
        if not n or len(n) < 2:
            return
        key = n.lower()
        if key in _STOP or key in seen:
            return
        if not re.match(r"^[A-Za-z][A-Za-z0-9._-]{1,31}$", n):
            return
        seen.add(key)
        found.append(n)

    if gene_detail:
        b = gene_detail.get("basic") or {}
        add(b.get("gene_symbol") or "")
        add(b.get("gene_name") or "")
        std = gene_detail.get("standard") or {}
        add(std.get("gene_symbol") or "")

    try:
        from gene_toolkit import extract_genes_from_question

        for g in extract_genes_from_question(question or "") or []:
            add(g)
    except Exception:
        pass

    # 答案里的拉丁基因样 token（短列表，避免误伤）
    for m in re.finditer(r"(?<![A-Za-z0-9_/])([a-z][a-z0-9]{1,10}(?:\.[0-9]+)?|[A-Z][a-z]{2,}[0-9][a-z0-9]*)(?![A-Za-z0-9_])", answer or ""):
        tok = m.group(1)
        if tok.lower() in seen:
            continue
        # 仅当已在库或问句出现过时才链（降低假阳性）；有 gene_detail 时放宽同家族
        if tok.lower() in {x.lower() for x in found}:
            add(tok)

    return found[:12]


def _load_gene_bundle(symbol: str) -> Optional[dict]:
    try:
        from gene_toolkit import get_gene_detail, normalize_gene_name

        name = normalize_gene_name(symbol) or symbol
        return get_gene_detail(name)
    except Exception:
        return None


def _mapping_rows(detail: dict) -> list[dict]:
    b = detail.get("basic") or {}
    std = detail.get("standard") or {}
    rows = []
    if b.get("gene_name") or b.get("gene_symbol"):
        rows.append(
            {
                "role": "本地库 GRCz11",
                "value": b.get("gene_symbol") or b.get("gene_name"),
                "local": local_gene_url(b.get("gene_symbol") or b.get("gene_name") or ""),
                "external": None,
            }
        )
    if std.get("gene_symbol"):
        rows.append(
            {
                "role": "RefSeq GRCz12tu 标准名",
                "value": std.get("gene_symbol"),
                "local": local_gene_url(std.get("gene_symbol")),
                "external": _ext("refseq", std.get("refseq_id") or "") if std.get("refseq_id") else None,
            }
        )
    if b.get("ensembl_id"):
        rows.append(
            {
                "role": "Ensembl",
                "value": b.get("ensembl_id"),
                "local": local_id_url(b.get("ensembl_id")),
                "external": _ext("ensembl", b.get("ensembl_id")),
            }
        )
    if b.get("zfin_id"):
        rows.append(
            {
                "role": "ZFIN",
                "value": b.get("zfin_id"),
                "local": local_id_url(b.get("zfin_id")),
                "external": _ext("zfin", b.get("zfin_id")),
            }
        )
    if b.get("uniprot_id"):
        rows.append(
            {
                "role": "UniProt",
                "value": b.get("uniprot_id"),
                "local": local_id_url(b.get("uniprot_id")),
                "external": _ext("uniprot", b.get("uniprot_id")),
            }
        )
    for x in (detail.get("xrefs") or [])[:8]:
        db = str(x.get("db_name") or "")
        did = str(x.get("db_id") or "")
        if not did:
            continue
        rows.append(
            {
                "role": db,
                "value": did,
                "local": local_id_url(did),
                "external": _ext(db, did),
            }
        )
    # aliases
    for a in (detail.get("aliases") or [])[:6]:
        al = a.get("alias") if isinstance(a, dict) else str(a)
        if al:
            rows.append(
                {
                    "role": "别名",
                    "value": al,
                    "local": local_gene_url(al),
                    "external": None,
                }
            )
    return rows


def _merged_info_markdown(detail: dict, budget: Optional[_LinkBudget] = None) -> str:
    budget = budget or _LinkBudget()
    b = detail.get("basic") or {}
    lines = ["", f"### {_t('本地资料补全', 'Local data supplement')}", ""]
    name = b.get("gene_symbol") or b.get("gene_name") or "?"
    lines.append(f"**{_t('基因', 'Gene')}** {_md_dual(name, local_gene_url(name), _gene_ext_pairs(detail), budget)}")
    if b.get("chromosome"):
        lines.append(
            f"- {_t('染色体', 'Chromosome')}：`{b.get('chromosome')}`"
            f"{_t('（GRCz11 坐标，勿与 GRCz12tu 混用）', ' (GRCz11 coordinates — not interchangeable with GRCz12tu)')}"
        )
    if b.get("gene_function") or b.get("expression_summary"):
        func = (b.get("gene_function") or "")[:280]
        if func:
            lines.append(f"- {_t('功能', 'Function')}：{func}")
        if b.get("expression_summary"):
            lines.append(f"- {_t('表达摘要', 'Expression summary')}：{b.get('expression_summary')}")
    go = detail.get("go") or []
    if go:
        bits = []
        for g in go[:6]:
            term = g.get("go_term") or ""
            gid = ""
            m = re.search(r"(GO:\d{7})", term)
            if m:
                gid = m.group(1)
            if gid:
                bits.append(_md_dual(gid, local_id_url(gid), [("QuickGO", _quickgo_url(gid))], budget))
            elif term:
                bits.append(term)
        if bits:
            lines.append("- GO：" + "；".join(bits))
    kegg = detail.get("kegg") or []
    if kegg:
        bits = []
        for k in kegg[:5]:
            pid = str(k.get("pathway_id") or "")
            pname = k.get("pathway_name") or pid
            if pid:
                bits.append(_md_dual(pname, local_id_url(pid), [("KEGG", _kegg_url(pid))], budget))
        if bits:
            lines.append("- KEGG：" + "；".join(bits))
    expr = detail.get("expression") or []
    if expr:
        # 按来源分组并强制带单位：Bgee = 表达分 0–100（存在性置信分，非丰度）；其余为 Atlas 胚胎 TPM
        groups = [
            (_t("Bgee 表达分（0–100，非丰度）", "Bgee expression score (0–100, not abundance)"),
             [r for r in expr if (r.get("data_source") or "").lower() == "bgee"], "/100"),
            (_t("Expression Atlas TPM（整胚时间序列为主）", "Expression Atlas TPM (mostly whole-embryo series)"),
             [r for r in expr if (r.get("data_source") or "").lower() != "bgee"], " TPM"),
        ]
        bgee_u = _bgee_gene_url(b.get("ensembl_id") or "") or "https://www.bgee.org/"
        atlas_u = _atlas_gene_url(b.get("ensembl_id") or "")
        expr_ext = [("Bgee", bgee_u)]
        if atlas_u:
            expr_ext.append(("Expression Atlas", atlas_u))
        chunks = []
        for title, rows, unit in groups:
            if not rows:
                continue
            top = sorted(
                rows,
                key=lambda r: float(r.get("expression_level") or r.get("tpm") or 0),
                reverse=True,
            )[:5]
            bits = []
            is_bgee = unit == "/100"
            for r in top:
                stage = "" if is_bgee else str(r.get("expression_stage") or "").strip()
                tis = r.get("tissue") or r.get("anatomical_entity") or stage or "?"
                if stage and str(tis).strip().lower() in ("whole embryo", "embryo", "胚胎"):
                    tis = f"{stage}{_t('（整胚）', ' (whole embryo)')}"
                elif stage and str(tis).strip() != stage:
                    tis = f"{stage}·{tis}"
                raw = r.get("expression_level") if r.get("expression_level") is not None else r.get("tpm")
                try:
                    val = f"{float(raw):g}"
                except (TypeError, ValueError):
                    val = "?"
                bits.append(f"{tis} {val}{unit}")
            chunks.append(f"{title}：" + "、".join(bits))
        if chunks:
            lines.append(
                f"- {_t('表达（数值已带单位与来源；Bgee 为 0–100 的表达存在性置信分，不是丰度）',
                        'Expression (units and sources included; Bgee is a 0–100 expression-presence score, not abundance)')}："
                + "；".join(chunks)
                + " · "
                + _md_dual(_t("组学表达库", "omics expression databases"), "", expr_ext, budget)
            )
    lines.append(
        f"- {_t('序列提取', 'Sequence extraction')}："
        f"{_md_dual(_t('打开本系统序列工具', 'Open the sequence tool'), local_seq_url(name), [], budget)}"
    )
    return "\n".join(lines)


def _mapping_markdown(rows: list[dict]) -> str:
    if not rows:
        return ""
    lines = ["", f"### {_t('跨库基因名映射', 'Cross-database gene ID mapping')}", "",
             _t("| 来源 | 标识 | 链接 |", "| Source | Identifier | Link |"), "|---|---|---|"]
    for r in rows:
        role = str(r.get("role") or "")
        if not _is_zh():
            role = _ROLE_EN.get(role.strip(), role)
        links = []
        if r.get("local"):
            links.append(f"[{_t('本库', 'this hub')}]({r['local']})")
        if r.get("external"):
            links.append(f"[{_t('原站', 'source site')}]({r['external']})")
        lines.append(f"| {role} | `{r.get('value')}` | {' · '.join(links) or '-'} |")
    return "\n".join(lines)


_ROLE_EN = {
    "别名": "Alias",
    "定位": "Locus",
    "染色体": "Chromosome",
    "同源": "Ortholog",
    "Ensembl": "Ensembl",
    "ZFIN": "ZFIN",
    "NCBI": "NCBI",
    "UniProt": "UniProt",
}


# 组学/身份库关键词：问句或答句命中则强制进「本次用到的库」（资料库 ≠ 仅 PubMed）
_OMICS_FORCE_RE = re.compile(
    r"ZFIN|Ensembl|UniProt|NCBI|RefSeq|Bgee|KEGG|QuickGO|Gene\s*Ontology|"
    r"WikiPathways|Expression\s*Atlas|ArrayExpress|\bGEO\b|"
    r"基因组|组装|映射|跨库|标准名|别名|同源|注释库|组学|"
    r"GRCz1[12]|GCF_049306965|基因身份|ID\s*映射",
    re.I,
)
_OMICS_ZFIN_RE = re.compile(
    r"ZFIN|ZDB-GENE|基因身份|别名|标准名|跨库|映射|斑马鱼基因(?:数据|资料)库",
    re.I,
)
_OMICS_ENS_RE = re.compile(r"Ensembl|ENS[A-Z]{0,4}G\d|基因组|组装|GRCz11|基因模型", re.I)
_OMICS_NCBI_RE = re.compile(r"NCBI|RefSeq|GRCz12|Entrez|GeneID|GCF_049306965|组装|标准名", re.I)
_OMICS_UNI_RE = re.compile(r"UniProt|蛋白序列|蛋白注释", re.I)
_OMICS_GO_RE = re.compile(r"\bGO\b|Gene\s*Ontology|QuickGO|功能注释", re.I)
_OMICS_KEGG_RE = re.compile(r"KEGG|通路|pathway", re.I)
_OMICS_BGEE_RE = re.compile(r"Bgee|组织表达|表达谱|表达量", re.I)
_OMICS_WP_RE = re.compile(r"WikiPathways|WP\d{4,}", re.I)
_OMICS_ATLAS_RE = re.compile(r"Expression\s*Atlas|表达图谱", re.I)
_OMICS_GEO_RE = re.compile(r"\bGEO\b|GSE\d+|GSM\d+|基因表达综合", re.I)
_OMICS_AE_RE = re.compile(r"ArrayExpress|E-(?:GEOD|MTAB|MEXP|TABM)-\d+", re.I)


def _infer_db_usage(
    answer: str,
    primary: Optional[dict],
    *,
    question: str = "",
    has_gene: bool,
    has_lit: bool,
    has_go: bool,
    has_kegg: bool,
    has_expr: bool,
    has_blast: bool,
) -> dict[str, bool]:
    """从问句+答句+gene_detail 推断本次用到的组学库（资料库=组学库，文献单独标注）。"""
    text = f"{question or ''}\n{answer or ''}"
    b = (primary or {}).get("basic") or {}
    std = (primary or {}).get("standard") or {}
    force_omics = bool(_OMICS_FORCE_RE.search(text))

    # 脚注/正文/问句里出现库名即视为本次用到；映射/基因组/组装类问题强制核心身份库
    has_zfin = bool(
        has_gene
        or b.get("zfin_id")
        or _ZFIN_RE.search(text)
        or re.search(r"zfin\.org", text, re.I)
        or re.search(r"ZFIN", text, re.I)
        or (force_omics and _OMICS_ZFIN_RE.search(text))
    )
    has_ensembl = bool(
        has_gene
        or b.get("ensembl_id")
        or _ENSEMBL_RE.search(text)
        or re.search(r"ensembl\.org", text, re.I)
        or re.search(r"Ensembl", text, re.I)
        or (force_omics and _OMICS_ENS_RE.search(text))
    )
    has_ncbi = bool(
        has_gene
        or std.get("gene_symbol")
        or b.get("ncbi_gene_id")
        or _REFSEQ_RE.search(text)
        or _NCBI_GENE_RE.search(text)
        or re.search(r"ncbi\.nlm\.nih\.gov|RefSeq|GRCz12|NCBI", text, re.I)
        or (force_omics and _OMICS_NCBI_RE.search(text))
    )
    has_uniprot = bool(
        b.get("uniprot_id")
        or re.search(r"uniprot\.org", text, re.I)
        or re.search(r"UniProt", text, re.I)
        or (force_omics and _OMICS_UNI_RE.search(text))
    )
    has_go2 = bool(
        has_go
        or _GO_RE.search(text)
        or re.search(r"QuickGO|Gene Ontology|\bGO\b", text, re.I)
        or (force_omics and _OMICS_GO_RE.search(text))
    )
    has_kegg2 = bool(
        has_kegg
        or _KEGG_RE.search(text)
        or re.search(r"KEGG", text, re.I)
        or (force_omics and _OMICS_KEGG_RE.search(text))
    )
    has_expr2 = bool(
        has_expr
        or re.search(r"bgee\.org", text, re.I)
        or re.search(r"Bgee", text, re.I)
        or (force_omics and _OMICS_BGEE_RE.search(text))
    )
    has_wp = bool(
        _WP_RE.search(text)
        or re.search(r"wikipathways\.org", text, re.I)
        or _OMICS_WP_RE.search(text)
    )
    has_atlas = bool(
        re.search(r"ebi\.ac\.uk/gxa", text, re.I)
        or _OMICS_ATLAS_RE.search(text)
        or (has_expr2 and b.get("ensembl_id"))
    )
    has_geo = bool(_GEO_RE.search(text) or _OMICS_GEO_RE.search(text))
    has_ae = bool(_AE_RE.search(text) or _OMICS_AE_RE.search(text))
    # 映射/组装类问题：至少挂上三大身份库，避免清单只剩 PubMed
    if force_omics and re.search(r"映射|组装|基因组|跨库|标准名|别名", text, re.I):
        has_zfin = True
        has_ensembl = True
        has_ncbi = True
    return {
        "ensembl": has_ensembl,
        "zfin": has_zfin,
        "ncbi": has_ncbi,
        "uniprot": has_uniprot,
        "bgee": has_expr2,
        "atlas": has_atlas,
        "go": has_go2,
        "kegg": has_kegg2,
        "wikipathways": has_wp,
        "geo": has_geo,
        "arrayexpress": has_ae,
        "lit": has_lit,
        "blast": has_blast,
    }


def _databases_used(
    *,
    has_gene: bool,
    has_lit: bool,
    has_go: bool,
    has_kegg: bool,
    has_expr: bool,
    has_blast: bool,
    answer: str = "",
    question: str = "",
    primary: Optional[dict] = None,
) -> list[dict]:
    flags = _infer_db_usage(
        answer,
        primary,
        question=question,
        has_gene=has_gene,
        has_lit=has_lit,
        has_go=has_go,
        has_kegg=has_kegg,
        has_expr=has_expr,
        has_blast=has_blast,
    )
    items = [
        {
            "id": "hub",
            "name": "斑马鱼基因知识中枢（本系统）",
            "url": "#/home",
            "note": "本地组学注释与问答（非仅文献）",
        }
    ]
    if flags["ensembl"]:
        items.append(
            {
                "id": "ensembl",
                "name": "Ensembl Danio rerio",
                "url": "https://www.ensembl.org/Danio_rerio/Info/Index",
                "note": "GRCz11 基因模型 / ID",
            }
        )
    if flags["zfin"]:
        items.append(
            {
                "id": "zfin",
                "name": "ZFIN",
                "url": "https://zfin.org/",
                "note": "斑马鱼基因身份与别名权威",
            }
        )
    if flags["ncbi"]:
        items.append(
            {
                "id": "ncbi",
                "name": "NCBI RefSeq GRCz12tu",
                "url": "https://www.ncbi.nlm.nih.gov/datasets/genome/GCF_049306965.2/",
                "note": "用户可见标准名 / BLAST / cDNA",
            }
        )
    if flags["uniprot"]:
        items.append(
            {
                "id": "uniprot",
                "name": "UniProt",
                "url": "https://www.uniprot.org/",
                "note": "蛋白序列与注释",
            }
        )
    if flags["bgee"]:
        items.append(
            {
                "id": "bgee",
                "name": "Bgee",
                "url": "https://www.bgee.org/",
                "note": "组织表达",
            }
        )
    if flags.get("atlas"):
        items.append(
            {
                "id": "expression_atlas",
                "name": "Expression Atlas",
                "url": "https://www.ebi.ac.uk/gxa/home",
                "note": "多物种表达图谱",
            }
        )
    if flags["go"]:
        items.append(
            {
                "id": "go",
                "name": "Gene Ontology / QuickGO",
                "url": "https://www.ebi.ac.uk/QuickGO/",
                "note": "GO 注释",
            }
        )
    if flags["kegg"]:
        items.append(
            {
                "id": "kegg",
                "name": "KEGG",
                "url": "https://www.kegg.jp/",
                "note": "通路",
            }
        )
    if flags.get("wikipathways"):
        items.append(
            {
                "id": "wikipathways",
                "name": "WikiPathways",
                "url": "https://www.wikipathways.org/",
                "note": "社区通路",
            }
        )
    if flags.get("geo"):
        items.append(
            {
                "id": "geo",
                "name": "NCBI GEO",
                "url": "https://www.ncbi.nlm.nih.gov/geo/",
                "note": "表达芯片 / RNA-seq 归档",
            }
        )
    if flags.get("arrayexpress"):
        items.append(
            {
                "id": "arrayexpress",
                "name": "ArrayExpress",
                "url": "https://www.ebi.ac.uk/biostudies/arrayexpress",
                "note": "EBI 表达实验归档",
            }
        )
    if flags["lit"]:
        items.extend(
            [
                {
                    "id": "pubmed",
                    "name": "PubMed",
                    "url": "https://pubmed.ncbi.nlm.nih.gov/",
                    "note": "开放文献索引（≠ 组学身份库）",
                },
                {
                    "id": "local_lit",
                    "name": "本地文献库",
                    "url": "#/literature",
                    "note": "本库 FAISS 文献证据（≠ ZFIN/Ensembl 等组学库）",
                },
            ]
        )
    if flags["blast"]:
        items.append(
            {
                "id": "blast",
                "name": "本系统 BLAST（GRCz12tu CDS）",
                "url": "#/blast",
                "note": "序列比对",
            }
        )
    return items


_RESOURCES_SECTION_RE = re.compile(
    r"(?m)^[ \t]*#{2,4}[ \t]*本次用到的(?:组学)?库与资料[^\n]*\n"
    r"(?:[ \t]*\n)*"
    r"(?:^[ \t]*[-*+][ \t]+[^\n]+\n?)+",
)
_LOCAL_LIT_BADGE_RE = re.compile(r"本地文献库\s*PubmedYes", re.I)

# 服务器侧原有的防护（2026-10-28 合并回本地）：没有有效 design_bundle 时，
# 不允许正文出现 CHOPCHOP / CRISPRscan / Primer-BLAST / Primer3 外链 —— 这些链接
# 带的是模型自己编的参数，点开对不上，属于"看起来能验证、其实无法核验"。
_UNVERIFIED_DESIGN_LINK_RE = re.compile(
    r"https?://[^\s)>\]]*(?:chopchop|crisprscan|primer-blast|primer3plus|primer3\.ut\.ee)[^\s)>\]]*",
    re.I,
)


def strip_unverified_design_links(answer: str, result: Optional[dict] = None) -> str:
    """没有有效 design_bundle 时，去掉 CHOPCHOP / CRISPRscan / Primer-BLAST 外链。"""
    text = answer or ""
    if not text or not _UNVERIFIED_DESIGN_LINK_RE.search(text):
        return text
    bundle = (result or {}).get("design_bundle") if isinstance(result, dict) else None
    try:
        from coord_crosswalk import verify_design_bundle

        if bundle and verify_design_bundle(bundle).get("valid"):
            return text
    except Exception:
        pass
    return _UNVERIFIED_DESIGN_LINK_RE.sub(
        "[外站设计链接已移除：缺少有效 design_bundle]",
        text,
    )


def _strip_resources_sections(text: str) -> str:
    """去掉模型自写的资料清单，避免与系统清单重复，并抹掉 PubmedYes 展示名。"""
    out = _RESOURCES_SECTION_RE.sub("", text or "")
    out = _LOCAL_LIT_BADGE_RE.sub("本地文献库", out)
    return out


def _resources_markdown(dbs: list[dict]) -> str:
    lines = [
        "",
        f"### {_t('本次用到的组学库与资料', 'Omics databases and sources used')}",
        "",
        "> " + _t(
            "资料库优先指 ZFIN / Ensembl / NCBI / UniProt / Bgee / Expression Atlas / GO / KEGG / "
            "WikiPathways / GEO / ArrayExpress 等组学库；PubMed 与本地文献库为文献证据通道。",
            "Omics databases mean ZFIN / Ensembl / NCBI / UniProt / Bgee / Expression Atlas / GO / "
            "KEGG / WikiPathways / GEO / ArrayExpress; PubMed and the local literature library are "
            "the literature-evidence channel.",
        ),
        "",
    ]
    for d in dbs:
        name = _LOCAL_LIT_BADGE_RE.sub("本地文献库", str(d.get("name") or ""))
        if name.strip() == "PubmedYes":
            name = "本地文献库"
        note = str(d.get("note") or "")
        if not _is_zh():
            name = _DB_NAME_EN.get(name.strip(), name)
            note = _DB_NOTE_EN.get(note.strip(), note)
        lines.append(f"- [{name}]({d['url']})" + (f"：{note}" if note else ""))
    return "\n".join(lines)


# 英文答案下，资料清单里的库名/备注也用英文（否则正文英文、脚注中文，正是用户说的"太零碎")
_DB_NAME_EN = {
    "斑马鱼基因知识中枢（本系统）": "Zebrafish Gene Hub (this system)",
    "本地文献库": "Local literature library",
    "本系统 BLAST（GRCz12tu CDS）": "This hub's BLAST (GRCz12tu CDS)",
}
_DB_NOTE_EN = {
    "本地组学注释与问答（非仅文献）": "local omics annotation and Q&A (not literature-only)",
    "GRCz11 基因模型 / ID": "GRCz11 gene models / IDs",
    "斑马鱼基因身份与别名权威": "authoritative zebrafish gene identity and aliases",
    "用户可见标准名 / BLAST / cDNA": "user-facing canonical symbols / BLAST / cDNA",
    "蛋白序列与注释": "protein sequences and annotation",
    "组织表达": "tissue expression",
    "多物种表达图谱": "multi-species expression atlas",
    "GO 注释": "GO annotations",
    "通路": "pathways",
    "社区通路": "community-curated pathways",
    "表达芯片 / RNA-seq 归档": "microarray / RNA-seq archive",
    "EBI 表达实验归档": "EBI expression experiment archive",
    "开放文献索引（≠ 组学身份库）": "open literature index (not an omics identity database)",
    "本库 FAISS 文献证据（≠ ZFIN/Ensembl 等组学库）":
        "FAISS literature evidence in this hub (not ZFIN/Ensembl-style omics data)",
    "序列比对": "sequence search",
}


def _protect_links(text: str) -> tuple[str, list[str]]:
    slots: list[str] = []

    def repl(m: re.Match) -> str:
        slots.append(m.group(0))
        return f"\x00L{len(slots)-1}\x00"

    return _LINKED_SPAN.sub(repl, text or ""), slots


def _restore_links(text: str, slots: list[str]) -> str:
    out = text
    for i, s in enumerate(slots):
        out = out.replace(f"\x00L{i}\x00", s)
    return out


def _seed_budget_from_slots(slots: list[str], budget: _LinkBudget) -> None:
    """已有 markdown 链的 URL 计入预算，避免再次挂同一目标。"""
    for s in slots:
        for m in re.finditer(r"\]\(([^)\s]+)\)", s or ""):
            u = _norm_url(m.group(1))
            if u:
                budget.used.add(u)


# 基因符号边界：把 '.' 算进 token，避免 hsp70 误匹配 hsp70.1
_GENE_LEFT = r"(?<![A-Za-z0-9_/#.\x00-])"
_GENE_RIGHT = r"(?![A-Za-z0-9_/#.\x00-])"


def _linkify_gene_symbols(
    body: str,
    gene_symbols: list[str],
    details_by_sym: dict[str, dict],
    budget: _LinkBudget,
) -> str:
    """按正文出现顺序链接基因；同一位置取最长符号；每个 URL 仅首次成链。"""
    if not gene_symbols or not body:
        return body
    cands: list[tuple[int, int, str, str]] = []
    for sym in gene_symbols:
        pat = re.compile(rf"{_GENE_LEFT}({re.escape(sym)}){_GENE_RIGHT}")
        for m in pat.finditer(body):
            cands.append((m.start(), m.end(), sym, m.group(1)))
    if not cands:
        return body
    # 同起点更长优先，再从左到右贪心去重叠
    cands.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    chosen: list[tuple[int, int, str, str]] = []
    occupied = -1
    for start, end, sym, text in cands:
        if start < occupied:
            continue
        chosen.append((start, end, sym, text))
        occupied = end
    repls: list[tuple[int, int, str]] = []
    for start, end, sym, text in chosen:
        detail = details_by_sym.get(sym.lower()) or details_by_sym.get(sym) or {}
        ext_pairs = _gene_ext_pairs(detail) if detail else []
        repls.append((start, end, _md_dual(text, local_gene_url(sym), ext_pairs, budget)))
    for start, end, repl in reversed(repls):
        body = body[:start] + repl + body[end:]
    return body


def linkify_answer_body(
    answer: str,
    gene_symbols: list[str],
    details_by_sym: dict[str, dict],
    budget: Optional[_LinkBudget] = None,
) -> str:
    """在正文中为实体加本库+外站双链（跳过已有链接与参考文献块）。

    同一 URL 整篇至多超链一次：结构化 ID 与基因符号共用预算；
    首次命中成链，后续同目标保持纯文本。
    """
    if not answer:
        return answer
    if "参考文献" in answer:
        body, sep, foot = answer.partition("参考文献")
    else:
        body, sep, foot = answer, "", ""

    budget = budget or _LinkBudget()
    body, slots = _protect_links(body)
    _seed_budget_from_slots(slots, budget)

    def _reprotect(text: str) -> str:
        """把新生成的 markdown 链纳入保护槽，避免后续替换嵌套进链接文本。"""
        nonlocal slots

        def repl(m: re.Match) -> str:
            slots.append(m.group(0))
            return f"\x00L{len(slots) - 1}\x00"

        return _LINKED_SPAN.sub(repl, text)

    def ens_sub(m: re.Match) -> str:
        eid = m.group(1)
        return _md_dual(eid, local_id_url(eid), [("Ensembl", _ext("ensembl", eid) or "")], budget)

    def zfin_sub(m: re.Match) -> str:
        zid = m.group(1)
        return _md_dual(zid, local_id_url(zid), [("ZFIN", _ext("zfin", zid) or "")], budget)

    def go_sub(m: re.Match) -> str:
        gid = m.group(1)
        return _md_dual(gid, local_id_url(gid), [("QuickGO", _quickgo_url(gid))], budget)

    def kegg_sub(m: re.Match) -> str:
        kid = m.group(1)
        return _md_dual(kid, local_id_url(kid), [("KEGG", _kegg_url(kid))], budget)

    def refseq_sub(m: re.Match) -> str:
        rid = m.group(1)
        return _md_dual(rid, local_id_url(rid), [("RefSeq", _ext("refseq", rid) or "")], budget)

    def uniprot_sub(m: re.Match) -> str:
        uid = m.group(1)
        return _md_dual(uid, local_id_url(uid), [("UniProt", _ext("uniprot", uid) or "")], budget)

    def ncbi_sub(m: re.Match) -> str:
        gid = m.group(1)
        label = m.group(0)
        return _md_dual(label, local_id_url(gid), [("NCBI Gene", _ext("ncbi_gene", gid) or "")], budget)

    def geo_sub(m: re.Match) -> str:
        acc = m.group(1)
        return _md_dual(acc, local_id_url(acc), [("GEO", _geo_url(acc))], budget)

    def wp_sub(m: re.Match) -> str:
        wp = m.group(1)
        return _md_dual(wp, local_id_url(wp), [("WikiPathways", _wikipathways_url(wp))], budget)

    def ae_sub(m: re.Match) -> str:
        acc = m.group(1)
        return _md_dual(acc, local_id_url(acc), [("ArrayExpress", _arrayexpress_url(acc))], budget)

    # 1) 结构化 ID 先挂（显式 ID 保留本库+原站）；已用 URL 后续不再重复
    body = _ENSEMBL_RE.sub(ens_sub, body)
    body = _ZFIN_RE.sub(zfin_sub, body)
    body = _GO_RE.sub(go_sub, body)
    body = _KEGG_RE.sub(kegg_sub, body)
    body = _REFSEQ_RE.sub(refseq_sub, body)
    body = _NCBI_GENE_RE.sub(ncbi_sub, body)
    body = _UNIPROT_RE.sub(uniprot_sub, body)
    body = _GEO_RE.sub(geo_sub, body)
    body = _WP_RE.sub(wp_sub, body)
    body = _AE_RE.sub(ae_sub, body)
    body = _reprotect(body)

    # 2) 基因符号按正文出现顺序；剩余可用外链挂在首次出现上
    body = _linkify_gene_symbols(body, gene_symbols, details_by_sym, budget)
    body = _reprotect(body)

    # 序列相关措辞 → 序列工具链（同样遵守 once-per-URL）
    if re.search(r"序列|cDNA|CDS|启动子|FASTA|blast", body, re.I) and gene_symbols:
        sym = gene_symbols[0]
        detail = details_by_sym.get(sym.lower()) or {}
        eid = ((detail.get("basic") or {}).get("ensembl_id") or "") if detail else ""
        tip = (
            "\n\n> 查序列："
            + _md_dual(
                sym + " 序列工具",
                local_seq_url(sym),
                [("Ensembl", _ext("ensembl", eid) or "")] if eid else [],
                budget,
            )
        )
        if "查序列：" not in body:
            body = body.rstrip() + tip

    body = _restore_links(body, slots)
    return body + (sep + foot if sep else "")


def enrich_chat_result(question: str, result: dict) -> dict:
    """对任意范围的聊天结果做 B8–B13 增强。"""
    if not isinstance(result, dict):
        return result
    answer = str(result.get("answer") or "")
    if not answer or answer.startswith("[错误]"):
        return result
    # 通用车道 / 安全 / 显式跳过：不灌资料块、不乱 linkify
    if result.get("skip_enrich"):
        return result
    scope = str(result.get("answer_scope") or "")
    tier = str(result.get("intent_tier") or "").upper()
    if scope in ("general", "safety", "meta", "adjacent") or tier in ("C", "D", "E", "B"):
        return result
    # 元问题 / 产品 howto 不灌资料块
    low = (question or "").lower()
    if any(k in (question or "") for k in ("你是谁", "怎么用", "如何使用", "快捷键")):
        return result

    # 问句直接点名 ZFIN / 映射 / 资料库时，正文推断前先打标，避免清单漏链
    force_zfin = bool(re.search(r"zfin|ZFIN|斑马鱼基因(?:数据|资料)库", question or "", re.I))

    gene_detail = result.get("gene_detail")
    symbols = _collect_gene_symbols(question, answer, gene_detail)
    details_by_sym: dict[str, dict] = {}
    if gene_detail:
        b = gene_detail.get("basic") or {}
        for key in (b.get("gene_symbol"), b.get("gene_name"), (gene_detail.get("standard") or {}).get("gene_symbol")):
            if key:
                details_by_sym[str(key).lower()] = gene_detail
    for sym in symbols:
        if sym.lower() in details_by_sym:
            continue
        d = _load_gene_bundle(sym)
        if d:
            details_by_sym[sym.lower()] = d
            if not gene_detail:
                gene_detail = d
                result["gene_detail"] = d

    link_budget = _LinkBudget()
    answer2 = linkify_answer_body(answer, symbols, details_by_sym, link_budget)
    # 先清掉模型可能自写的旧清单（常带「本地文献库 PubmedYes」），再统一插入系统清单
    answer2 = _strip_resources_sections(answer2)

    primary = gene_detail
    mapping_rows = _mapping_rows(primary) if primary else []
    wants_map = bool(
        re.search(r"版本|别名|映射|标准名|Ensembl|ZFIN|GRCz|同名|对应", question or "", re.I)
        or re.search(r"版本|别名|映射|标准名", answer, re.I)
    )
    wants_db = bool(symbols) or bool(primary) or bool(
        re.search(r"表达|GO|KEGG|通路|功能|染色体|注释", question or "")
    )

    extras: list[str] = []
    if wants_db and primary:
        extras.append(_merged_info_markdown(primary, link_budget))
    if wants_map and mapping_rows:
        extras.append(_mapping_markdown(mapping_rows))

    has_lit = bool(result.get("context")) or "参考文献" in answer2
    has_go = bool(primary and (primary.get("go") or []))
    has_kegg = bool(primary and (primary.get("kegg") or []))
    has_expr = bool(primary and (primary.get("expression") or primary.get("basic", {}).get("expression_summary")))
    has_blast = bool(result.get("blast")) or "blast" in low
    # 问句+答句联合推断：映射/基因组/ZFIN 等强制挂组学库，避免清单只剩文献
    dbs = _databases_used(
        has_gene=bool(symbols or primary),
        has_lit=has_lit,
        has_go=has_go,
        has_kegg=has_kegg,
        has_expr=has_expr,
        has_blast=has_blast,
        answer=answer2,
        question=question or "",
        primary=primary,
    )
    if force_zfin and not any(d.get("id") == "zfin" for d in dbs):
        # 插在 Hub 之后，保证「zfin呢?」类问题必有 ZFIN 超链
        insert_at = 1 if dbs and dbs[0].get("id") == "hub" else 0
        dbs.insert(
            insert_at,
            {
                "id": "zfin",
                "name": "ZFIN",
                "url": "https://zfin.org/",
                "note": "斑马鱼基因身份与别名权威",
            },
        )
    extras.append(_resources_markdown(dbs))

    # 插在参考文献之前
    block = "\n".join(x for x in extras if x).rstrip()
    if block:
        if "参考文献" in answer2:
            head, mid, tail = answer2.partition("参考文献")
            answer2 = head.rstrip() + "\n\n" + block + "\n\n" + mid + tail
        else:
            answer2 = answer2.rstrip() + "\n\n" + block

    result["answer"] = strip_unverified_design_links(
        _LOCAL_LIT_BADGE_RE.sub("本地文献库", answer2),
        result,
    )
    result["resource_panel"] = {
        "genes": symbols,
        "mappings": mapping_rows,
        "databases_used": dbs,
        "has_sequence_hint": bool(re.search(r"序列|cDNA|CDS|blast", answer2, re.I)),
    }
    result["name_mapping"] = mapping_rows
    return result
