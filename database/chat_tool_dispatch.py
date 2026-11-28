# -*- coding: utf-8 -*-
"""Chat 直连工具栏。

用户在 Chat 里用自然语言点名要某个工具的结果时（例如「帮我设计 tp53 的 sgRNA」
「GRCz11 chr5:24086227-24097805 换成 GRCz12tu」「mstn 在哪些组织表达」），
这里直接调用**与网页工具栏完全相同的底层工具**，把真实结果写进回答正文，
而不是只把结果塞进 tool_results 让右侧边栏展示。

设计要点：
  · 只在意图明确时短路（要有动作词 + 对象），机制/原理类问句一律放行给原链路；
  · 不调用大模型、不跑文献检索，因此是确定性的、秒回的；
  · 回答正文只写数据本身，不写编排路径、内部口径或「请去某页」。
"""
import bootstrap  # noqa: F401

import copy
import re
import threading
import time
from typing import Any, Dict, List, Optional

from common_utils import logger

TOOL_LABELS = {
    "liftover": "Liftover 坐标换算",
    "sequence_region": "位置查找序列",
    "genes_region": "区间内基因",
    "expression": "表达谱",
    "phenotype_gene": "基因查表型",
    "phenotype_term": "表型查基因",
    "blast": "BLAST 比对",
    "go": "GO 富集",
    "kegg": "KEGG 通路",
    "sgrna": "sgRNA 设计",
    "primer": "引物设计",
    "sequence": "基因序列 / 启动子",
    "annotate": "基因注释",
    "id_resolve": "ID 互转 / 基因别名",
    "gene_locus": "基因坐标",
    "platform_stats": "数据规模",
}

# 英文界面的工具名（用于 tools_invoked / task_type / 侧栏卡片标题）。
TOOL_LABELS_EN = {
    "liftover": "Liftover coordinate conversion",
    "sequence_region": "Region sequence",
    "genes_region": "Genes in region",
    "expression": "Expression profile",
    "phenotype_gene": "Phenotypes by gene",
    "phenotype_term": "Genes by phenotype",
    "blast": "BLAST",
    "go": "GO enrichment",
    "kegg": "KEGG pathways",
    "sgrna": "sgRNA design",
    "primer": "Primer design",
    "sequence": "Gene sequence / promoter",
    "annotate": "Gene annotation",
    "id_resolve": "ID convert / aliases",
    "gene_locus": "Gene coordinates",
    "platform_stats": "Data scale",
}

# ---------------------------------------------------------------- 解析辅助

_SPAN_COLON = re.compile(
    r"(?:chr|chromosome|染色体)?\s*([0-9]{1,2}|[XYxy]|MT|mt|M)\s*[:：]\s*"
    r"(\d[\d,]*)\s*(?:-|–|—|~|到|至)\s*(\d[\d,]*)", re.I)
_SPAN_SPACE = re.compile(
    r"\bchr\s*([0-9]{1,2}|[XYxy]|MT|mt)\s+(\d[\d,]*)\s*(?:-|–|—|~)\s*(\d[\d,]*)", re.I)
_BUILD_RE = re.compile(
    r"\b(GRCz\s?\d{1,2}(?:tu|ab)?|Zv\s?\d|danRer\d+|GCF_\d{9}\.\d|GCA_\d{9}\.\d)\b", re.I)
_DNA_SEQ = re.compile(r"[ATCGNatcgn]{20,}")
_ID_TOKEN = re.compile(
    r"ZDB-GENE-\d+-\d+|ENS[A-Z]+\d+(?:\.\d+)?|(?:NM|NR|NP|XM|XR|NC|NG)_\d+(?:\.\d+)?",
    re.I)

# 非基因符号的英文词（装配名、工具名、通用词），防止被当成基因。
_NOT_GENE = {
    "grcz", "grcz10", "grcz11", "grcz12", "grcz12tu", "grcz12ab", "grcz13ab",
    "zv7", "zv8", "zv9", "chr", "blast", "kegg", "go", "sgrna", "grna", "crispr",
    "primer", "pcr", "fasta", "cdna", "mrna", "liftover", "gene", "genes",
    "sequence", "expression", "phenotype", "assembly", "refseq", "ensembl",
    "ncbi", "zfin", "tpm", "fpkm", "bgee", "atlas", "padj", "tissue", "tissues",
    "region", "chrom", "chromosome", "start", "end", "strand", "list", "find",
    "design", "convert", "help", "show", "give", "what", "which", "the", "and",
    # 与 chat_understanding._STOP_TOKEN 对齐：避免把物种/通用词当成基因符号
    "human", "mouse", "rat", "zebrafish", "danio", "fish", "model", "species",
    "cell", "cells", "organism", "dna", "rna", "protein", "proteins",
    "data", "study", "studies", "result", "results", "method", "methods",
    "analysis", "review", "paper", "article", "research", "sample", "samples",
    # 2026-11-09：英文代词不是基因符号。生产库 gene_alias 里恰好有一条 alias='its'
    # 指向 sycp2，导致「What about its expression in adult tissues?」被解析成 sycp2
    # （见 output/全链路十题实测_20261109.md F3）。指代应由多轮上下文展开处理。
    "its", "it", "they", "them", "their", "theirs", "his", "her", "hers",
    "he", "she", "these", "those", "this", "that", "who", "whom", "whose",
    "about", "adult", "adults", "same", "other", "another",
}


def _num(s: str) -> int:
    return int(str(s).replace(",", "").replace("，", ""))


def _parse_span(q: str) -> Optional[dict]:
    for pat in (_SPAN_COLON, _SPAN_SPACE):
        m = pat.search(q)
        if not m:
            continue
        chrom, s, e = m.group(1), _num(m.group(2)), _num(m.group(3))
        if s > e:
            s, e = e, s
        if s <= 0 or e - s + 1 > 10_000_000:
            return None
        return {"chrom": chrom, "start": s, "end": e}
    return None


def _parse_builds(q: str) -> List[str]:
    from coord_liftover import normalize_build

    out: List[str] = []
    for m in _BUILD_RE.finditer(q):
        b = normalize_build(re.sub(r"\s+", "", m.group(1)))
        if b and b not in out:
            out.append(b)
    return out


def _gene(q: str) -> Optional[str]:
    """从问句里取基因符号 / 跨库 ID；过滤掉装配名和工具名。"""
    m = _ID_TOKEN.search(q)
    if m:
        return m.group(0)
    try:
        from gene_toolkit import extract_gene_from_question

        g = extract_gene_from_question(q)
    except Exception:
        return None
    if not g:
        return None
    if g.lower() in _NOT_GENE:
        return None
    if re.fullmatch(r"\d+", g):
        # 纯数字一般不是基因符号；但「NCBI gene ID 30590 对应哪个基因」里，
        # 那串数字就是唯一正确的检索键，此时必须放行。
        if not re.search(r"(ncbi|gene\s*id|基因\s*id|entrez|gi:)", q, re.I):
            return None
    return g


def _genes(q: str, limit: int = 12) -> List[str]:
    try:
        from gene_toolkit import extract_genes_from_question

        raw = extract_genes_from_question(q, limit=limit + 6)
    except Exception:
        return []
    return [g for g in raw if g.lower() not in _NOT_GENE and not re.fullmatch(r"\d+", g)][:limit]


def _dna(q: str) -> Optional[str]:
    m = _DNA_SEQ.search(re.sub(r"\s", "", q))
    if not m:
        return None
    try:
        from seq_toolkit import validate_sequence

        ok, seq = validate_sequence(m.group(0))
        return seq if ok else None
    except Exception:
        return m.group(0).upper()


def _is_en(q: str) -> bool:
    return bool(re.search(r"[A-Za-z]{3,}", q)) and not re.search(r"[\u4e00-\u9fff]", q)


def _has(q: str, *words: str) -> bool:
    low = q.lower()
    return any(w.lower() in low for w in words)


def _t(lang: str, zh: str, en: str) -> str:
    """按回答语言选择文案；默认中文。所有执行器的用户可见文案都经此开关。"""
    return zh if (lang or "zh") != "en" else en


def _action(q: str) -> bool:
    """是否带明确「要结果」的动作；避免把原理/介绍类问句抢走。"""
    return _has(q, "帮我", "给我", "请", "查", "找", "列出", "列出", "设计", "生成",
                "提取", "取", "算", "换算", "转换", "分析", "跑", "做", "看一下",
                "有哪些", "是什么", "多少", "show", "give", "list", "find", "design",
                "convert", "extract", "fetch", "run", "calculate")


# ---------------------------------------------------------------- 意图识别
# 2026-11-06：原先这里有一份独立的「单意图」识别（约 80 行），与
# ``chat_understanding.task_specs`` 的「多维度拆解」各写一套 —— 同一句话可能得到
# 两种结论，改一边漏一边，是「回答不全」反复复发的结构性原因。
# 现统一到 task_specs，本函数只保留**向后兼容适配**（返回第一个子任务）。
# 合并前做过分差回归：160+ 组问法（基因 × 维度 × 动作词、区间 / 序列 / BLAST /
# 表型 / 英文），旧层能命中的新层全部能命中，故删除重复实现。


def detect_tool_intent(question: str) -> Optional[dict]:
    """兼容旧调用：返回第一个子任务；无意图返回 None。

    新代码请直接用 ``chat_understanding.task_specs()``（可返回多个维度）。
    """
    try:
        from chat_understanding import task_specs

        specs = task_specs(question)
    except Exception:  # noqa: BLE001 - 兼容入口不应把异常抛给调用方
        return None
    return specs[0] if specs else None


# ---------------------------------------------------------------- 各工具执行

def _wrap(tool: str, answer: str, results: dict, card: str = "") -> dict:
    return {"tool": tool, "answer": answer.strip(), "tool_results": results, "card": card or answer.strip()}


def _exec_liftover(spec: dict, q: str, lang: str = "zh") -> Optional[dict]:
    from coord_liftover import explain_coord_question

    payload = explain_coord_question(q, lang=lang)
    if not payload or not payload.get("answer"):
        return None
    lifted = payload.get("liftover") or {}
    return _wrap("liftover", payload["answer"], {"liftover": lifted})


def _exec_sequence_region(spec: dict, q: str, lang: str = "zh") -> dict:
    from sequence_toolkit import fetch_region_sequence

    span = spec["span"]
    builds = spec.get("builds") or []
    asm = "GRCz11" if any("11" in b for b in builds) else "GRCz12tu"
    strand = "-" if _has(q, "负链", "反向", "reverse", "minus strand") else "+"
    data = fetch_region_sequence(span["chrom"], span["start"], span["end"], strand, asm)
    if not data.get("success"):
        return _wrap("sequence_region", _t(lang, "未能取出该区间序列：%s", "Could not fetch the sequence for this region: %s") % (data.get("error") or _t(lang, "未知原因", "unknown reason")),
                     {"sequence_region": data})
    seq = data.get("sequence") or ""
    head = _t(
        lang,
        "%s chr%s:%d-%d（%s链），共 %d nt。" % (
            data.get("assembly"), data.get("chromosome"), data.get("start"),
            data.get("end"), data.get("strand"), data.get("length")),
        "%s chr%s:%d-%d (%s strand), %d nt in total." % (
            data.get("assembly"), data.get("chromosome"), data.get("start"),
            data.get("end"), data.get("strand"), data.get("length")),
    )
    if len(seq) <= 500:
        body = data.get("fasta") or (">%s\n%s" % (data.get("region"), seq))
    else:
        body = _t(
            lang,
            ">%s\n%s\n…（此处仅展示前 500 个碱基，整段共 %d nt）" % (
                data.get("region"), seq[:500], len(seq)),
            ">%s\n%s\n... (first 500 bases shown; the full region is %d nt)" % (
                data.get("region"), seq[:500], len(seq)),
        )
    return _wrap("sequence_region", head + "\n\n```fasta\n" + body + "\n```",
                 {"sequence_region": data}, head + "\n" + body)


def _exec_genes_region(spec: dict, q: str, lang: str = "zh") -> dict:
    from region_genes import genes_in_span

    span = spec["span"]
    builds = spec.get("builds") or []
    asm = next((b for b in builds if b in ("GRCz11", "GRCz12tu", "GRCz12ab")), "GRCz12tu")
    rows = genes_in_span(asm, span["chrom"], span["start"], span["end"], limit=30)
    head = "%s chr%s:%d-%d " % (asm, span["chrom"], span["start"], span["end"])
    if not rows:
        return _wrap("genes_region", head + _t(lang, "区间内本地注释中没有收录到基因。", "has no genes annotated in the local library within this region."), {"genes_region": rows})
    lines = [head + _t(lang, "区间内收录到 %d 个基因：" % len(rows), "contains %d genes in the local library:" % len(rows)), ""]
    for g in rows:
        strand = g.get("strand") or "?"
        strand_txt = ("%s链" % strand) if lang == "zh" else ("%s strand" % strand)
        lines.append("· %s   %d-%d（%s）  NCBI Gene %s" % (
            g.get("symbol") or "-", g.get("start") or 0, g.get("end") or 0,
            strand_txt, g.get("gene_id") or "-"))
    return _wrap("genes_region", "\n".join(lines), {"genes_region": rows})


def _exec_expression(spec: dict, q: str, lang: str = "zh") -> dict:
    from expression_toolkit import gene_profile

    gene = spec["gene"]
    d = gene_profile(gene, lang=lang)
    if not d.get("ok"):
        return _wrap("expression", _t(lang, "没有查到 %s 的表达数据：%s", "No expression data found for %s: %s") % (gene, d.get("error") or _t(lang, "无记录", "no record")),
                     {"expression": d})
    info = d.get("gene") or {}
    bgee = (d.get("bgee") or {}).get("tissues") or []
    atlas = (d.get("atlas") or {}).get("stages") or []
    # 用**提问里的符号**做主名：问 myl7 却写成 "cmlc2 (myl7)"，读者第一眼会觉得答错基因
    sym = str(info.get("gene_symbol") or gene)
    prim = str(gene or sym)
    other = sym if sym.lower() != prim.lower() else str(info.get("gene_name") or "")
    label = "%s (%s)" % (prim, other) if other and other.lower() != prim.lower() else prim
    n_rows = d.get("n_rows") or 0
    lines = [_t(lang, "%s 的表达数据，共 %d 条记录。", "Expression data for %s: %d record%s.") % (
        label, n_rows, "" if n_rows == 1 else "s")]
    if bgee:
        lines += ["", _t(lang, "按组织（Bgee 表达置信分 0–100，取前 %d 项）：" % len(bgee), "By tissue (Bgee confidence score 0–100, top %d):" % len(bgee))]
        lines += ["- %s: %s" % (t.get("tissue") or "-", t.get("level")) for t in bgee[:12]]
    if atlas:
        lines += ["", _t(lang, "按胚胎分期（Expression Atlas，TPM）：", "By embryonic stage (Expression Atlas, TPM):")]
        for s in atlas[:12]:
            label2 = s.get("stage") or "-"
            if s.get("tissue"):
                label2 += " / " + s["tissue"]
            lines.append("- %s: %s TPM" % (label2, s.get("level")))
    zfin = (d.get("zfin") or {}).get("records") or []
    if zfin:
        lines += ["", _t(lang, "按 ZFIN 表达记录（组织 / 时期，定性）：", "ZFIN expression records (anatomy / stage, qualitative):")]
        for z in zfin[:12]:
            lines.append("- %s / %s" % (z.get("tissue") or "-", z.get("stage") or "-"))
    if not bgee and not atlas and not zfin:
        lines.append(_t(lang, "本地库暂未收录该基因的组织或分期表达记录。", "No tissue or stage expression records for this gene in the local library."))
    lines += ["", _t(lang, "注：Bgee 为 0–100 置信分，Atlas 为 TPM，ZFIN 为定性组织与时期记录，三组数值不能直接比较。", "Note: Bgee scores are 0–100 confidence, Atlas values are TPM, and ZFIN records are qualitative anatomy and stage. The three are not directly comparable.")]
    return _wrap("expression", "\n".join(lines), {"expression": d})


def _exec_phenotype_gene(spec: dict, q: str, lang: str = "zh") -> dict:
    from phenotype_toolkit import gene_phenotypes

    gene = spec["gene"]
    d = gene_phenotypes(gene)
    if not d.get("ok") or not d.get("ready"):
        return _wrap("phenotype_gene", _t(lang, "没有查到 %s 的表型记录。" % gene, "No phenotype records found for %s." % gene), {"phenotype_gene": d})
    groups = d.get("groups") or []
    head = _t(lang, "%s 的表型记录共 %d 条", "%s has %d phenotype records") % (gene, d.get("n_rows") or 0)
    if not groups:
        return _wrap("phenotype_gene", head + _t(lang, "，本地库中没有可归类的表型条目。", "; no classifiable phenotype entries in the local library."), {"phenotype_gene": d})
    lines = [head + _t(lang, "，按「解剖部位 · 表型」归并后为 %d 组：" % len(groups), "; merged by anatomy · phenotype into %d groups:" % len(groups)), ""]
    for g in groups[:20]:
        cnt = g.get("count") or 0
        raw_stages = g.get("stages") or []
        # ZDB-STAGE-xxx 是内部主键，读者看不懂；有可读分期名才写，否则只报个数
        named = [str(x) for x in raw_stages if not str(x).upper().startswith("ZDB-STAGE")][:3]
        if lang == "zh":
            if named:
                tail = "（%d 条，时期 %s）" % (cnt, "、".join(named))
            elif raw_stages:
                tail = "（%d 条，涉及 %d 个发育分期）" % (cnt, len(raw_stages))
            else:
                tail = "（%d 条）" % cnt
        else:
            unit = "record" if cnt == 1 else "records"
            if named:
                tail = " (%d %s; stages %s)" % (cnt, unit, "; ".join(named))
            elif raw_stages:
                _n = len(raw_stages)
                tail = " (%d %s; %d developmental stage%s)" % (
                    cnt, unit, _n, "" if _n == 1 else "s")
            else:
                tail = " (%d %s)" % (cnt, unit)
        lines.append("- %s · %s%s" % (
            g.get("ao_name") or "-", g.get("pato_name") or "-", tail))
    if len(groups) > 20:
        lines.append(_t(lang, "…另有 %d 组未列出。" % (len(groups) - 20), "...and %d more groups not listed." % (len(groups) - 20)))
    return _wrap("phenotype_gene", "\n".join(lines), {"phenotype_gene": d})


def _clean_phenotype_keyword(q: str) -> str:
    """从问句里剥掉提问套话，只留下表型描述（中英文都保留）。"""
    zh_filler = (
        "表型查基因", "帮我", "麻烦", "请", "查一下", "查询", "列出", "找一下", "找出",
        "给我", "哪些基因", "什么基因", "有哪些", "哪些", "什么", "表型", "基因",
        "相关", "的", "有", "是", "？", "?", "：", ":", "，", ",", "。", "、",
    )
    en_filler = ("show", "list", "find", "genes", "gene", "with", "phenotype", "related", "to")
    key = q
    for w in zh_filler:
        key = key.replace(w, " ")
    for w in en_filler:
        key = re.sub(r"\b%s\b" % w, " ", key, flags=re.I)
    return re.sub(r"\s+", " ", key).strip() or q.strip()


def _exec_phenotype_term(spec: dict, q: str, lang: str = "zh") -> dict:
    from phenotype_toolkit import term_search

    key = _clean_phenotype_keyword(q)
    d = term_search(key)
    if not d.get("ok") or not d.get("ready"):
        return _wrap("phenotype_term", _t(lang, "表型索引暂不可用。", "The phenotype index is temporarily unavailable."), {"phenotype_term": d})
    genes = d.get("genes") or []
    terms = d.get("terms") or []
    lines = [_t(lang, "表型关键词「%s」的检索结果：" % key, "Search results for phenotype keyword \"%s\":" % key)]
    if terms:
        lines += ["", _t(lang, "匹配到的表型条目（前 %d 项）：" % min(len(terms), 10), "Matching phenotype entries (top %d):" % min(len(terms), 10))]
        for t in terms[:10]:
            if lang == "zh":
                lines.append("· %s · %s · %s（%s 条记录，覆盖 %s 个基因）" % (
                    t.get("ao_name") or "-", t.get("pato_name") or "-",
                    t.get("quality") or "-", t.get("n") or 0, t.get("g") or 0))
            else:
                lines.append("· %s · %s · %s (%s records across %s genes)" % (
                    t.get("ao_name") or "-", t.get("pato_name") or "-",
                    t.get("quality") or "-", t.get("n") or 0, t.get("g") or 0))
    if genes:
        lines += ["", _t(lang, "相关基因（前 %d 个，按记录数排序）：" % len(genes), "Related genes (top %d by record count):" % len(genes))]
        lines += [
            ("· %s  %s 条记录" % (g.get("symbol") or "-", g.get("n") or 0))
            if lang == "zh" else
            ("· %s  %s records" % (g.get("symbol") or "-", g.get("n") or 0))
            for g in genes[:20]
        ]
    if not genes and not terms:
        lines.append(_t(lang, "没有匹配到表型或基因，可换一个更具体的表型词再试。", "No matching phenotype or gene found; try a more specific phenotype term."))
    return _wrap("phenotype_term", "\n".join(lines), {"phenotype_term": d})


def _exec_blast(spec: dict, q: str, lang: str = "zh") -> dict:
    from seq_toolkit import run_local_blast, format_blast_answer

    seq = spec["dna"]
    raw = run_local_blast(seq, top_k=10, evalue=10.0)
    if raw.get("success"):
        text = format_blast_answer(raw, lang=lang)
    else:
        text = _t(lang, "序列比对失败：%s", "BLAST failed: %s") % (raw.get("error") or _t(lang, "未知原因", "unknown reason"))
    return _wrap("blast", text, {"blast": {"result": raw, "summary": text}})


def _exec_go(spec: dict, q: str, lang: str = "zh") -> dict:
    from enrichment_toolkit import go_lookup, format_enrichment_answer, format_annotation_answer

    genes = spec["genes"]
    d = go_lookup(genes)
    if d.get("success"):
        if d.get("mode") == "annotation":
            # 单基因：给注释清单，不假装有统计显著性
            text = format_annotation_answer(d, lang)
        elif lang == "en":
            text = _format_enrichment_en(d, None)
        else:
            text = format_enrichment_answer(d, None)
    else:
        text = _t(lang, "GO 富集失败：%s", "GO enrichment failed: %s") % (d.get("error") or _t(lang, "未知原因", "unknown reason"))
    return _wrap("go", text, {"enrichment": {"go": d, "kegg": None, "summary": text}})


def _format_enrichment_en(go_result: Optional[dict], kegg_result: Optional[dict]) -> str:
    """英文富集摘要（只保留结论性字段，避免中文标签混入英文回答）。"""
    lines: List[str] = []
    if go_result and go_result.get("success"):
        sig_n = go_result.get("significant_count", 0)
        tot_n = go_result.get("total_tested", "?")
        if go_result.get("terms"):
            lines.append(
                "GO over-representation (Fisher's exact test, BH-FDR, padj <= %s): %s/%s terms significant; "
                "foreground %s genes vs background %s." % (
                    go_result.get("padj_cutoff", 0.05), sig_n, tot_n,
                    go_result.get("target_gene_count", "?"), go_result.get("universe_size", "?"))
            )
            for i, t in enumerate(go_result["terms"][:8], 1):
                genes = ", ".join(t.get("genes", [])[:5]) or "-"
                gid = t.get("go_id") or ""
                name = t.get("term_name") or gid or "(unnamed)"
                lines.append(
                    "%d. [%s] %s%s\n   padj=%.2e | GeneRatio=%s | BgRatio=%s | FC=%s | genes: %s" % (
                        i, t.get("aspect_label") or "-", name,
                        (" (%s)" % gid) if gid else "",
                        float(t.get("padj") or 0), t.get("gene_ratio"), t.get("bg_ratio"),
                        t.get("fold_enrichment"), genes))
        elif go_result.get("error"):
            lines.append("[GO] %s" % go_result["error"])
        else:
            lines.append("[GO] No significantly enriched terms (consider relaxing padj or extending GO annotation).")

    if kegg_result and kegg_result.get("success"):
        if kegg_result.get("pathways"):
            lines.append(
                "KEGG / WikiPathways / Reactome over-representation (Fisher + BH): %s/%s pathways significant; "
                "background %s genes." % (
                    kegg_result.get("significant_count", 0), kegg_result.get("total_tested", "?"),
                    kegg_result.get("universe_size", "?"))
            )
            for i, p in enumerate(kegg_result["pathways"][:6], 1):
                genes = ", ".join(p.get("overlap_genes", [])) or "-"
                lines.append(
                    "%d. [%s] %s %s\n   padj=%.2e | GeneRatio=%s | FC=%s | genes: %s" % (
                        i, p.get("source_label") or "-", p["pathway_id"], p["pathway_name"],
                        float(p.get("padj") or 0), p.get("gene_ratio"),
                        p.get("fold_enrichment"), genes))
        elif kegg_result.get("error"):
            lines.append("[KEGG] %s" % kegg_result["error"])

    if not lines:
        return "[Result] No significant enrichment found. Check gene symbols, GO annotation and KEGG data."
    return "\n".join(lines)


def _exec_kegg(spec: dict, q: str, lang: str = "zh") -> dict:
    from enrichment_toolkit import (
        kegg_lookup, kegg_pathway_search, format_enrichment_answer, format_annotation_answer,
    )

    genes = spec.get("genes") or []
    if genes:
        d = kegg_lookup(genes)
        if d.get("mode") == "annotation":
            # 单基因：该基因所在的通路清单（与 GO 页同一口径）
            summary = format_annotation_answer(d, lang)
        elif lang == "en":
            summary = _format_enrichment_en(None, d)
        else:
            summary = format_enrichment_answer(None, d)
        payload = {"go": None, "kegg": d, "summary": summary}
    else:
        d = kegg_pathway_search(q, None)
        lines = [_t(lang, "KEGG 通路检索结果：", "KEGG pathway search results:")]
        for p in (d.get("pathways") or [])[:12]:
            # 2026-11-07：原来取 p["gene_count"]，但 kegg_pathway_search 给的字段是
            # pathway_gene_count，于是「相关基因 N 个」永远显示 "-"。这里两个都兜。
            n_gene = p.get("pathway_gene_count") or p.get("gene_count") or "-"
            if lang == "zh":
                lines.append("· %s（%s）  相关基因 %s 个" % (
                    p.get("pathway_name") or p.get("name") or "-",
                    p.get("pathway_id") or p.get("id") or "-", n_gene))
            else:
                lines.append("· %s (%s)  %s genes" % (
                    p.get("pathway_name") or p.get("name") or "-",
                    p.get("pathway_id") or p.get("id") or "-", n_gene))
        if not (d.get("pathways") or []):
            lines.append(_t(lang, "没有匹配到通路。", "No matching pathway found."))
        payload = {"summary": "\n".join(lines)}
    return _wrap("kegg", payload["summary"], {"kegg": d, "enrichment": payload if genes else None})


def _exec_sgrna(spec: dict, q: str, lang: str = "zh") -> dict:
    from crispr_toolkit import design_sgrna

    target = spec.get("dna") or spec.get("gene")
    if not target:
        return _wrap("sgrna", _t(lang, "请给出要设计 sgRNA 的基因名或一段 DNA 序列。",
                                 "Please provide a gene symbol or a DNA sequence to design sgRNAs."), {"sgrna": {}})
    d = design_sgrna(target, top_n=8, off_target=False)
    if not d.get("success"):
        msg = _t(lang, "sgRNA 设计失败：%s", "sgRNA design failed: %s") % (
            d.get("error") or _t(lang, "未知原因", "unknown reason"))
        sugg = [s.get("symbol") for s in (d.get("suggestions") or []) if s.get("symbol")]
        if sugg:
            msg += _t(lang, "。是不是想查：%s", ". Did you mean: %s") % "、".join(sugg[:5])
        return _wrap("sgrna", msg, {"sgrna": d})
    cands = d.get("candidates") or []
    tpl = d.get("template") or {}
    lines = [_t(
        lang,
        "以 %s 为模板（%s，%s bp），按 %s PAM 找到 %d 个候选，推荐前 %d 个：" % (
            tpl.get("label") or target, tpl.get("gene") or "指定序列", tpl.get("length") or "?",
            d.get("pam_type") or "NGG", d.get("candidate_total") or 0, len(cands)),
        "Template %s (%s, %s bp); %d candidates found for %s PAM; top %d recommended:" % (
            tpl.get("label") or target, tpl.get("gene") or "given sequence", tpl.get("length") or "?",
            d.get("candidate_total") or 0, d.get("pam_type") or "NGG", len(cands)),
    ), ""]
    for i, c in enumerate(cands, 1):
        strand_txt = ("%s链" % (c.get("strand") or "?")) if lang == "zh" else ("%s strand" % (c.get("strand") or "?"))
        lines.append("%d. %s  PAM %s  %s  %s-%s  GC %.0f%%  %s %.1f" % (
            i, c.get("spacer") or "-", c.get("pam") or "-", strand_txt,
            c.get("start") or "?", c.get("end") or "?", float(c.get("gc") or 0),
            _t(lang, "评分", "score"),
            float(c.get("final_score") or c.get("efficiency_score") or 0)))
    if d.get("offtarget_note"):
        lines += ["", str(d["offtarget_note"])]
    return _wrap("sgrna", "\n".join(lines), {"sgrna": d})


def _exec_primer(spec: dict, q: str, lang: str = "zh") -> dict:
    from crispr_toolkit import design_primers

    target = spec.get("dna") or spec.get("gene")
    if not target:
        return _wrap("primer", _t(lang, "请给出要设计引物的基因名或一段 DNA 序列。",
                                  "Please provide a gene symbol or a DNA sequence to design primers."), {"primer": {}})
    d = design_primers(target, max_pairs=5, off_target=False)
    if not d.get("success"):
        msg = _t(lang, "引物设计失败：%s", "Primer design failed: %s") % (
            d.get("error") or _t(lang, "未知原因", "unknown reason"))
        sugg = [s.get("symbol") for s in (d.get("suggestions") or []) if s.get("symbol")]
        if sugg:
            msg += _t(lang, "。是不是想查：%s", ". Did you mean: %s") % "、".join(sugg[:5])
        return _wrap("primer", msg, {"primer": d})
    pairs = d.get("pairs") or []
    if not pairs:
        return _wrap("primer", _t(lang, "该模板下没有满足长度与 Tm 约束的引物对。",
                                  "No primer pair under this template satisfies the length and Tm constraints."), {"primer": d})
    lines = [_t(
        lang,
        "共筛出 %d 对候选引物，推荐前 %d 对：" % (d.get("pair_total") or len(pairs), len(pairs)),
        "%d candidate primer pairs screened; top %d recommended:" % (d.get("pair_total") or len(pairs), len(pairs)),
    ), ""]
    for i, p in enumerate(pairs, 1):
        lines += [
            _t(lang,
               "%d. 产物 %s bp  Tm %.1f/%.1f ℃（差 %.1f）  GC %.0f%%/%.0f%%" % (
                   i, p.get("product_size") or "?", p.get("forward_tm") or 0,
                   p.get("reverse_tm") or 0, p.get("tm_diff") or 0,
                   p.get("forward_gc") or 0, p.get("reverse_gc") or 0),
               "%d. product %s bp  Tm %.1f/%.1f C (delta %.1f)  GC %.0f%%/%.0f%%" % (
                   i, p.get("product_size") or "?", p.get("forward_tm") or 0,
                   p.get("reverse_tm") or 0, p.get("tm_diff") or 0,
                   p.get("forward_gc") or 0, p.get("reverse_gc") or 0)),
            _t(lang, "   正向 %s  %s", "   forward %s  %s") % (p.get("forward") or "-", p.get("forward_pos") or ""),
            _t(lang, "   反向 %s  %s", "   reverse %s  %s") % (p.get("reverse") or "-", p.get("reverse_pos") or ""),
        ]
    return _wrap("primer", "\n".join(lines), {"primer": d})


def _exec_sequence(spec: dict, q: str, lang: str = "zh") -> dict:
    from sequence_toolkit import extract_sequences, format_sequence_answer

    gene = spec["gene"]
    # 按问句点名决定取哪几类：问「蛋白序列」却回核酸，属于答非所问。
    kinds: list = []
    if _has(q, "蛋白", "氨基酸", "protein", "amino acid", "uniprot", "peptide"):
        kinds.append("protein")
    if _has(q, "启动子", "promoter", "upstream", "上游"):
        kinds.append("promoter")
    if _has(q, "基因组", "genomic"):
        kinds.append("genomic")
    if _has(q, "cds", "编码序列", "coding sequence", "orf", "cdna", "mrna", "转录本"):
        kinds.append("cdna")
    if not kinds:
        kinds = ["cdna", "promoter"]
    # 明确「把序列取出来」的诉求 → 给完整序列；否则只给 80 字符预览
    full = _has(q, "extract", "return", "provide", "give", "output", "fetch", "retrieve",
                "提取", "给出", "返回", "输出", "列出", "fasta", "full", "complete", "完整")
    # 问句里点名了装配就按那个版本取（"tp53 的 GRCz11 序列"），不点名用 auto。
    asm = "auto"
    builds = _parse_builds(q)
    if builds:
        asm = "GRCz11" if any("11" in b for b in builds) else "GRCz12tu"
    # 问句点名了长度就精确交付该长度（"2 kb promoter" → 2000 bp），
    # 而不是默认的「上游 2000 + 下游 200」=2201 bp，否则用户核对时对不上。
    up = down = None
    m_len = re.search(r"(\d+(?:\.\d+)?)\s*(kb|kbp|k\b|bp)", q or "", re.I)
    if m_len:
        val = float(m_len.group(1))
        up = int(round(val * 1000)) if m_len.group(2).lower().startswith("k") else int(round(val))
        down = 0
    d = extract_sequences(gene, kinds=kinds, assembly=asm,
                          promoter_upstream=up, promoter_downstream=down)
    text = format_sequence_answer(d, lang=lang, full=full)
    d["summary"] = text
    return _wrap("sequence", text, {"sequence": d})


def _exec_annotate(spec: dict, q: str, lang: str = "zh") -> dict:
    from gene_annotator_toolkit import annotate_gene, format_annotator_answer

    gene = spec["gene"]
    d = annotate_gene(gene)
    if d.get("success"):
        text = format_annotator_answer(d, lang=lang)
    else:
        text = _t(lang, "注释失败：%s", "Annotation failed: %s") % (d.get("error") or _t(lang, "未知原因", "unknown reason"))
    return _wrap("annotate", text, {"annotator": d})


_ZFIN_ID_RE = re.compile(r"^ZDB-GENE-\d+-\d+$", re.I)


def _unresolved_id_text(token: str, lang: str = "zh") -> str:
    """ID 解析失败时说清楚「为什么失败、下一步怎么问」，而不是一句 Could not resolve。"""
    if _ZFIN_ID_RE.match((token or "").strip()):
        return _t(
            lang,
            "%s 不在本地斑马鱼基因注释里，因此无法映射到基因符号。这个形式的 ZFIN ID "
            "通常意味着该记录已退役或 ID 有笔误——请先核对 ID，或改用基因符号 / "
            "Ensembl ID / RefSeq 登录号提问。（ZFIN 对自身未收录的 ID 会返回 Record Not Found。）",
            "%s is not present in the local zebrafish gene annotation, so it could not "
            "be mapped to a gene symbol. A ZFIN ID in this form usually means the record "
            "has been retired or the ID was mistyped — check it first, or ask with the "
            "gene symbol, Ensembl ID or RefSeq accession instead. (ZFIN itself answers "
            "\"Record Not Found\" for IDs it does not hold.)",
        ) % token
    return _t(
        lang,
        "没有解析到 %s 对应的基因。请改用基因符号、Ensembl ID 或 RefSeq 登录号提问。",
        "%s could not be mapped to a gene in the local annotation. Try the gene symbol, "
        "Ensembl ID or RefSeq accession instead.",
    ) % token


def _exec_id_resolve(spec: dict, q: str, lang: str = "zh") -> dict:
    from gene_id_mapper import resolve_gene_identifier
    from gene_formatter import answer_from_id_resolve

    token = spec["gene"]
    d = resolve_gene_identifier(token)
    if not d.get("resolved"):
        return _wrap("id_resolve", _unresolved_id_text(token, lang), {"id_resolve": d})
    text = answer_from_id_resolve(q, d, lang=lang)
    return _wrap("id_resolve", text, {"id_resolve": d})


def _exec_gene_locus(spec: dict, q: str, lang: str = "zh") -> dict:
    """基因坐标：直接取本地基因库的 locus，不经过大模型。"""
    from gene_toolkit import get_gene_detail, normalize_gene_name
    from gene_formatter import format_locus_block

    gene = spec.get("gene") or ""
    detail = None
    try:
        detail = get_gene_detail(normalize_gene_name(gene) or gene)
    except Exception:
        detail = None
    if not detail:
        try:
            from gene_id_mapper import resolve_gene_identifier

            resolved = resolve_gene_identifier(gene) or {}
            canon = (resolved.get("canonical") or {}).get("gene_name")
            if canon:
                detail = get_gene_detail(canon)
        except Exception:
            pass
    if not detail:
        return _wrap("gene_locus", _t(lang, "本地库没有查到 %s 的坐标记录。",
                                      "No coordinate record for %s in the local library.") % gene,
                     {"gene_locus": None})
    basic = detail.get("basic") or {}
    locus = detail.get("locus") or {}
    text = format_locus_block(basic.get("gene_name") or gene, locus, lang=lang)
    return _wrap("gene_locus", text, {"gene_locus": locus})


# 统计口径：全部按本库真实记录计数；查不到就如实说查不到，绝不编数字。
_STAT_SQL = (
    "SELECT "
    "(SELECT COUNT(*) FROM gene_basic) AS genes, "
    "(SELECT COUNT(*) FROM literature_paper) AS papers, "
    "(SELECT COUNT(*) FROM gene_alias) AS aliases, "
    "(SELECT COUNT(*) FROM gene_expression) AS expression, "
    "(SELECT COUNT(*) FROM gene_go) AS go_rows, "
    "(SELECT COUNT(*) FROM gene_kegg) AS kegg_rows"
)
_STAT_LABELS = [
    ("genes", "基因", "genes"),
    ("papers", "文献", "papers"),
    ("aliases", "基因别名", "gene aliases"),
    ("expression", "表达记录", "expression records"),
    ("go", "GO 注释", "GO annotations"),
    ("kegg", "KEGG 注释", "KEGG annotations"),
]


def _exec_platform_stats(spec: dict, q: str, lang: str = "zh") -> dict:
    # 语言由调用方（界面语言 > 显式请求 > 提问语言）决定，不再从问句反推，
    # 否则「本库有多少基因」在英文界面下仍会输出中文。
    lang = "en" if (lang or "zh").lower().startswith("en") else "zh"
    try:
        from mysql_client import get_mysql_client

        res = get_mysql_client().execute_sql(_STAT_SQL, max_rows=1)
        row = ((res or {}).get("data") or [None])[0]
    except Exception as e:  # noqa: BLE001
        logger.warning("[Chat工具] 数据规模统计失败：%s", e)
        row = None
    if not row:
        text = ("本次没能连上本地库，暂时无法给出准确条数；请稍后再问一次。"
                if lang == "zh" else
                "The local database did not respond, so I cannot give an exact count right now.")
        return _wrap("platform_stats", text, {"platform_stats": None})

    col_of = {"genes": "genes", "papers": "papers", "aliases": "aliases",
              "expression": "expression", "go": "go_rows", "kegg": "kegg_rows"}
    vals = {k: int(row.get(col_of[k]) or 0) for k, _, _ in _STAT_LABELS}
    topics = set(spec.get("topics") or ["genes"])
    label = {k: (zh if lang == "zh" else en) for k, zh, en in _STAT_LABELS}
    order = [k for k, _, _ in _STAT_LABELS]
    primary = [k for k in order if k in topics] or ["genes"]
    rest = [k for k in order if k not in primary]

    def _n(v: int) -> str:
        return format(v, ",")

    if lang == "zh":
        head = "、".join("**%s %s**" % (_n(vals[k]), label[k]) for k in primary)
        lines = ["本平台本地库目前收录 %s。" % head, "",
                 "本库其他数据：" + "、".join("%s %s" % (label[k], _n(vals[k])) for k in rest) + "。",
                 "", "以上均为本库实际收录条数，按库内记录统计，不是外部数据库的规模。"]
    else:
        head = ", ".join("**%s %s**" % (_n(vals[k]), label[k]) for k in primary)
        lines = ["This platform currently holds %s in its local library." % head, "",
                 "Other local records: " + ", ".join("%s %s" % (_n(vals[k]), label[k]) for k in rest) + ".",
                 "", "These are counts of records actually stored in this library, not external database sizes."]
    return _wrap("platform_stats", "\n".join(lines), {"platform_stats": vals})


_EXECUTORS = {
    "gene_locus": _exec_gene_locus,
    "platform_stats": _exec_platform_stats,
    "liftover": _exec_liftover,
    "sequence_region": _exec_sequence_region,
    "genes_region": _exec_genes_region,
    "expression": _exec_expression,
    "phenotype_gene": _exec_phenotype_gene,
    "phenotype_term": _exec_phenotype_term,
    "blast": _exec_blast,
    "go": _exec_go,
    "kegg": _exec_kegg,
    "sgrna": _exec_sgrna,
    "primer": _exec_primer,
    "sequence": _exec_sequence,
    "annotate": _exec_annotate,
    "id_resolve": _exec_id_resolve,
}


# ---------------------------------------------------------------- 结果缓存
# 确定性答案（查库/换算/计数）加一层短 TTL 缓存：同一问题重复问、或前端重发时
# 不再重复查库，配合意图前置短路，把常见问题的响应压到毫秒级。
_CACHE: Dict[str, tuple] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 900
_CACHE_MAX = 300


def _cache_key(question: str, depth: str, history: Optional[list], lang: str = "zh") -> str:
    try:
        from chat_understanding import normalize

        q = normalize(question)
    except Exception:
        q = (question or "").strip().lower()
    tail = ""
    if history:
        for item in list(history)[-2:]:
            if isinstance(item, dict):
                tail += "|" + str(item.get("content") or "")[:60]
    # 语言必须进 key：否则中文答案被缓存后，英文界面会直接命中中文缓存。
    return "%s|%s|%s|%s" % (q, depth or "full", lang or "zh", tail)


def _cache_get(key: str) -> Optional[dict]:
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if not hit:
            return None
        ts, val = hit
        if time.time() - ts > _CACHE_TTL:
            _CACHE.pop(key, None)
            return None
    return copy.deepcopy(val)


def _cache_put(key: str, val: dict) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            for k in sorted(_CACHE, key=lambda x: _CACHE[x][0])[:60]:
                _CACHE.pop(k, None)
        _CACHE[key] = (time.time(), copy.deepcopy(val))


# ---------------------------------------------------------------- 对外入口

def answer_with_tool(question: str, depth: str = "full",
                     history: Optional[list] = None,
                     lang: Optional[str] = None) -> Optional[dict]:
    """命中工具栏意图时返回完整 chat result；否则 None（交回原链路）。

    支持一句话里的多个诉求（如「tp53 的坐标及所有别名」），逐项调用底层工具后
    拼成一段完整回答，不再出现「只答一半」。

    ``lang`` 优先用调用方传入的界面/回答语言（en/zh）；未传时回退到
    ``current_answer_language``（界面语言）再回退到 ``lang_of（按提问语言推断）``。
    这样英文界面下工具栏回答也会是英文，且纯基因符号问句不会被误判为英文。
    """
    if not (question or "").strip():
        return None
    # 语义理解层在此处导入（模块级导入会与 chat_understanding 形成循环依赖）。
    # 注意：必须先完成导入再使用 lang_of，否则 lang_of 会被 Python 视为局部变量，
    # 触发 UnboundLocalError —— 该异常会被上层 except 吞掉，导致工具栏整体失效、
    # 回答回退到「基因详情」，表现为「只答一半」。
    from chat_understanding import task_specs, merge_parts, lang_of

    # 解析回答语言：优先显式传入，其次界面语言（ContextVar），最后按提问推断
    if not lang:
        try:
            from zebrafish_prompt import current_answer_language

            lang = current_answer_language(question)
        except Exception:  # noqa: BLE001
            lang = None
    if not lang:
        lang = lang_of(question)
    lang = "en" if str(lang).lower().startswith("en") else "zh"

    key = _cache_key(question, depth, history, lang)
    cached = _cache_get(key)
    if cached is not None:
        cached["chat_cache_hit"] = True
        return cached

    try:
        specs = task_specs(question, history=history)
    except Exception as e:  # noqa: BLE001
        # 理解层异常时放行原链路，而不是猜测意图 —— 猜错会让用户拿到"答非所问"，
        # 放行至少能由大模型/文献链路兜底。
        logger.warning("[Chat工具] 语义理解异常，放行原链路：%s", e)
        specs = []
    if not specs:
        return None

    parts: List[dict] = []
    tool_results: Dict[str, Any] = {}
    used: List[str] = []
    failed: List[str] = []
    labels = TOOL_LABELS if lang == "zh" else TOOL_LABELS_EN
    for spec in specs:
        tool = spec.get("tool")
        fn = _EXECUTORS.get(tool)
        if not fn:
            failed.append(tool)
            continue
        try:
            try:
                out = fn(spec, question, lang=lang)
            except TypeError as sig_err:
                # 执行器尚未支持 lang 参数时（后续新增工具可能忘记加），
                # 不能因一个参数不匹配就丢掉整个维度 —— 回退为两参调用。
                if "lang" not in str(sig_err):
                    raise
                out = fn(spec, question)
        except Exception as e:  # noqa: BLE001
            logger.warning("[Chat工具] %s 执行失败：%s", tool, e)
            out = None
        if not out or not out.get("answer"):
            failed.append(tool)
            continue
        parts.append({"tool": tool, "answer": out["answer"]})
        used.append(labels.get(tool, tool))
        tool_results.update(out.get("tool_results") or {})

    if not parts:
        return None

    answer = merge_parts(parts, lang=lang) if len(parts) > 1 else parts[0]["answer"].strip()
    # #15：多工具问答里若有维度静默失败，明确告知用户，而不是给「部分答案」而不自知
    if failed and parts:
        sep = "、" if lang == "zh" else ", "
        miss = sep.join(sorted({labels.get(f, f) for f in failed}))
        answer = answer + "\n\n" + _t(
            lang,
            "（注：以下维度暂时未能返回结果：%s）" % miss,
            "(Note: the following aspects could not be returned: %s)" % miss,
        )
    if not answer:
        return None

    from common_utils import empty_result

    single = len(parts) == 1
    primary = parts[0]["tool"]
    label = labels.get(primary, primary) if single else " · ".join(used)
    result = empty_result()
    result.update({
        "answer": answer,
        "task_type": label,
        "tools_invoked": used,
        "tool_results": tool_results,
        "chat_tool": primary if single else "multi",
        "chat_tool_card": {"title": label, "body": answer},
        "hub_mode": True,
        "answer_scope": "tool",
        "answer_depth": depth or "full",
        "intent_tier": "A",
        "skip_enrich": True,
        "skip_reference_footer": True,
        "specialists": [],
        "agent_trace": [],
    })
    _cache_put(key, result)
    return copy.deepcopy(result)
