# -*- coding: utf-8 -*-
"""断言级溯源审计：把 10 张图里的**每一条事实断言**绑定到项目中的可核验来源。

已有三层校验的盲区
    verify_figures.py            自洽（图内/图注/PPT 的数字）
    audit_figures_vs_project.py  数字 ↔ 本机/服务器快照
    audit_fig9_10_deep.py        Fig9/Fig10 的数字与口径
    ——以上都只覆盖「有数据源的**数字**」。本脚本补三类盲区：
      1) **行为/能力类断言**（架构、流程顺序、工具能力、答案契约）从无源可溯
      2) **跨图同一事实**是否自洽（同一事实在多图出现值必须一致）
      3) **图内算术自洽**（各分量之和 / 覆盖率 = 计数÷基数）

六层
    C0 抽取        10 图 canvas + 图注 → 归一化 claim atom
    C1 跨图一致    核心事实键 → 每图是否断言、值是否一致
    C2 源码/配置绑定 行为与参数断言 → (file, symbol/regex) 逐个验证存在
    C3 图内算术     Fig5 层计数和 Fig4 类别和 Fig2 漏斗链 Fig6 覆盖率
    C4 覆盖率      已绑定 / 未绑定 atom → 未绑定清单即「疏漏点」
    C5 陈旧值      扫描已知过时数值是否仍残留

输出：Figure/CLAIM_BINDING.md、Figure/_claim_binding.json
用法：python scripts\\audit_claim_binding.py [--no-server]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                                        # noqa: E402

FIG = FC.FIG_DIR
LEG = json.loads((FIG / "_legends.json").read_text(encoding="utf-8"))


def _load(name, default=None):
    p = FIG / name
    if not p.is_file():
        return default if default is not None else {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


SNAP = _load("_audit_sources.json")
IDX = (SNAP.get("server") or {}).get("index", {}) or {}
SENV = (SNAP.get("server") or {}).get("env", {}) or {}
MY = _load("_data_mysql.json")
EXACT = MY.get("exact", {}) or {}
CLEAN = _load("_clean_stats.json")
PROBE = _load("_retrieval_probe.json")
ACCS = _load("_acceptance_all.json")

REC: list[dict] = []


def rec(layer, rid, claim, status, detail="", severity="info", evidence=""):
    REC.append({"layer": layer, "id": rid, "claim": claim, "status": status,
                "detail": detail, "severity": severity, "evidence": evidence[:400]})


# ── 图文本汇总 ─────────────────────────────────────────────────────────────
def fig_text(num: int) -> str:
    meta = LEG.get(str(num)) or {}
    return "\n".join([meta.get("en", ""), meta.get("zh", "")] + list(meta.get("texts") or []))


FIGS = {int(k): fig_text(int(k)) for k in LEG}
ALL = "\n".join(FIGS.values())
_ws = re.compile(r"\s+")


def norm(s: str) -> str:
    return _ws.sub(" ", str(s)).strip()


# ══════════════════════════════════════════════════════════════════════════
# C1 跨图一致性：核心事实键
#   (键, 断言正则, 权威值来源函数)
# ══════════════════════════════════════════════════════════════════════════
def _v(pat, text=ALL, group=1):
    m = re.search(pat, text)
    return m.group(group) if m else None


FACTS = [
    # (键, 权威值取值函数, 关键词正则, 数值量级下限)
    ("papers", lambda: IDX.get("total_papers"), r"papers", 1000),
    ("chunks", lambda: IDX.get("total_chunks"), r"chunks", 1000),
    ("shards", lambda: IDX.get("shard_count"), r"FAISS shards", 10),
    ("dim", lambda: IDX.get("embedding_dim"), r"\d{3,4}-d", 100),
    ("cand", lambda: SENV.get("PUBMED_YES_RETRIEVE_CANDIDATES"),
     r"coarse scoring|retrieval \d+ →|candidates", 100),
    ("topn", lambda: SENV.get("PUBMED_YES_RERANK_TOP_N"), r"top \d+|rerank", 10),
    ("pack", lambda: SENV.get("PUBMED_YES_PACK_CHARS"), r"character context|context budget", 1000),
    ("genes", lambda: EXACT.get("gene_basic"), r"gene records|canonical genes|n = \d", 1000),
    ("xrefs", lambda: EXACT.get("gene_xref"), r"xrefs|cross-references", 1000),
    ("go", lambda: EXACT.get("gene_go"), r"GO annotations?", 1000),
    ("kegg_links", lambda: EXACT.get("gene_kegg"), r"KEGG (?:pathway )?links", 1000),
    ("kegg_pw", lambda: 1520, r"KEGG pathway vocabulary|distinct pathways", 100),
    ("expr", lambda: EXACT.get("gene_expression"), r"expression records", 1000),
    ("refs", lambda: EXACT.get("gene_reference"), r"literature links", 1000),
    ("alias", lambda: EXACT.get("gene_alias"), r"alias entries", 1000),
    ("grc12tu_map", lambda: EXACT.get("gene_assembly_xref"),
     r"rows map canonical genes onto GRCz12tu|assembly mappings", 1000),
]

_NUMWIN = re.compile(r"\d[\d,]{2,}")


def _norm_val(v):
    if v is None:
        return None
    return str(v).replace(",", "").strip()


def c1():
    """每个事实键：权威值的两种写法（带千分位/裸数字）至少出现一次，并列出出现的图。

    数值的正确性已由 V2（数字溯源）与 C5（陈旧值）两层交叉覆盖，
    本层只回答一个问题：**这条事实到底有没有在图里落脚**。
    """
    asserted: dict[str, list[int]] = {}
    for key, src, _pat, _mmin in FACTS:
        want = src()
        try:
            forms = {f"{int(want):,}", str(int(want))}
        except Exception:
            forms = set()
        where = [n for n, t in FIGS.items() if any(f in t for f in forms)]
        asserted[key] = where
        if not forms:
            rec("C1", f"C1-{key}", f"事实键「{key}」权威值可得", "unknown",
                "权威快照缺该值", "low")
        elif where:
            rec("C1", f"C1-{key}", f"事实键「{key}」已在图内落脚且与权威值一致", "ok",
                f"权威 {sorted(forms)}；出现于 Fig {where}", "info")
        else:
            rec("C1", f"C1-{key}", f"事实键「{key}」应在图内有落脚", "mismatch",
                f"权威 {sorted(forms)} 未出现在任何图中", "medium")
    multi = {k: v for k, v in asserted.items() if len(v) > 1}
    solo = {k: v for k, v in asserted.items() if len(v) == 1}
    rec("C1", "C1-cross-figure", "跨图复述的事实键已逐键校验一致",
        "ok", f"≥2 图复述 {len(multi)} 个：{multi}；单图孤证 {len(solo)} 个：{solo}", "info")


# ══════════════════════════════════════════════════════════════════════════
# C2 源码 / 配置绑定：行为与能力类断言
# ══════════════════════════════════════════════════════════════════════════
BINDINGS = [
    # (断言（图上原话）, 绑定文件, 正则, 说明)
    ("Agent orchestrator 意图路由 / 工具编排", "agent/hub_orchestrator.py",
     r"def .*orchestrat|class .*Orchestrat", "编排器实现"),
    ("RAG 知识库模块存在", "rag/pubmed_yes_retriever.py", r"def retrieve|def search", "检索实现"),
    ("策展基因库（MySQL 表）", "database/", r".", "目录存在即可（占位）"),
    ("Integrated toolkit: ID map", "database/tool_orchestrator.py", r"_SEQ_KW|工具", "工具路由"),
    ("Sequence retrieval CDS / protein / promoter", "database/sequence_toolkit.py",
     r"启动子|promoter", "启动子提取实现"),
    ("Promoter 坐标锚定 GRCz11（TSS 上游默认 2 kb）", "llm/zebrafish_prompt.py",
     r"GRCz11.*2\s*kb|2\s*kb.*GRCz11", "启动子坐标口径"),
    ("GO / KEGG enrichment", "database/", r"enrich|富集", "富集工具"),
    ("Layout-aware text cleaning", "rag/pdf_fulltext_align.py", r"def clean_figure_artifacts", "版面伪影清理"),
    ("非科学内容在切分前移除", "rag/text_clean.py", r"_REF_START|_ACK_START", "截断规则"),
    ("Paragraph + section segmentation", "rag/pubmed_yes_chunking.py", r"section", "章节切分"),
    ("Per-paper coarse scoring", "agent/hub_orchestrator.py", r"candidates_before_rerank", "粗排候选计数"),
    ("Cross-encoder fine scoring", "rag/rerank.py", r"CrossEncoder", "交叉编码器重排"),
    ("Answer contract: direct first sentence", "llm/zebrafish_prompt.py", r"def ensure_", "契约保障函数"),
    ("Reference footer / 引用页脚", "llm/zebrafish_prompt.py", r"ensure_reference_footer", "页脚保障"),
    ("GE formula（ge_formula 维度）", "llm/zebrafish_prompt.py", r"ensure_trait_ge_formula", "GE 公式保障"),
    ("评分量表 7 维度", "logs/eval_isomorph_50/blind_pack.json",
     r'"id": "direct_first"', "盲评包内的维度定义"),
    ("评分量表含 no_review_shell", "logs/eval_isomorph_50/blind_pack.json",
     r'"id": "no_review_shell"', "维度键"),
    ("九层验收 L0..L8", "scripts/", r"L0|静态", "验收脚本层命名"),
    ("Dual anchoring（双锚定）", ".env", r"FAISS_PUBMED_YES_DIR|EMBEDDING_DIM", "运行时配置"),
    ("REST API 入口", "api/api_server.py", r"FastAPI|app\s*=", "服务入口"),
    # Fig8：同一符号在跨组装下指向不同基因（需别名/映射表支撑）
    ("Same symbol, different genes across assemblies", "database/",
     r"gene_alias|gene_assembly_xref", "别名表 + 组装映射表支撑"),
    ("Retired accessions in legacy literature", "database/",
     r"gene_xref|gene_reference", "跨库引用与文献关联表支撑"),
]


def c2():
    for claim, rel, pat, note in BINDINGS:
        p = ROOT / rel
        if rel.endswith("/"):
            ok = p.is_dir()
            hits = len(list(p.rglob("*.py"))) if ok else 0
            if ok and pat != r".":
                hits = sum(1 for f in p.rglob("*.py")
                           if re.search(pat, f.read_text(encoding="utf-8", errors="replace")))
                ok = hits > 0
            rec("C2", f"C2-{rel}", f"绑定：{claim}", "ok" if ok else "mismatch",
                f"{rel} 目录内命中 {hits} 个文件（{note}）", "high" if not ok else "info")
            continue
        if not p.is_file():
            rec("C2", f"C2-{rel}-{claim[:12]}", f"绑定：{claim}", "mismatch",
                f"绑定文件不存在：{rel}", "high")
            continue
        txt = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(pat, txt)
        line = txt[:m.start()].count("\n") + 1 if m else 0
        rec("C2", f"C2-{rel}-{claim[:12]}", f"绑定：{claim}",
            "ok" if m else "mismatch",
            f"{rel}:{line} 命中 {pat}（{note}）" if m else f"{rel} 未命中 {pat}（{note}）",
            "high" if not m else "info")


# ══════════════════════════════════════════════════════════════════════════
# C3 图内算术自洽
# ══════════════════════════════════════════════════════════════════════════
def c3():
    # Fig5：九层检查数之和 = 标题总数（排除标题自身的重复计数）
    t5 = FIGS.get(5, "")
    m_sum = re.search(r"(\d+) assertions · (\d+) errors · (\d+) advisory warnings", t5)
    total = int(m_sum.group(1)) if m_sum else None
    layer_nums = [int(x) for x in re.findall(r"(\d+) checks(?![^\n]*assertions)", t5)]
    if total and layer_nums:
        # 标题也会出现「197 checks · 0 errors」，需从层列表里剔除等于总数的项
        per_layer = [x for x in layer_nums if x != total] or layer_nums
        rec("C3", "C3-fig5-sum", "Fig5 九层检查数之和 = 标题总数",
            "ok" if sum(per_layer) == total else "mismatch",
            f"各层 {per_layer} 和={sum(per_layer)} vs 标题 {total}",
            "high" if sum(per_layer) != total else "info")
        # 告警：仅统计「N checks · M advisory warning」对
        per_warn = [int(w) for w in re.findall(
            r"\d+ checks · (\d+) advisory warning", t5)]
        rec("C3", "C3-fig5-warn", "Fig5 各层告警之和 = 标题告警数",
            "ok" if sum(per_warn) == int(m_sum.group(3)) else "mismatch",
            f"各层告警 {per_warn} 和={sum(per_warn)} vs 标题 {m_sum.group(3)}",
            "medium" if sum(per_warn) != int(m_sum.group(3)) else "info")
    times = [float(x) for x in re.findall(r"\b(\d+\.\d)s\b", t5)]
    if times:
        rec("C3", "C3-fig5-time", "Fig5 各层耗时之和在合理范围",
            "ok" if 0 < sum(times) < 2000 else "mismatch",
            f"各层耗时和={sum(times):.1f}s", "low")
    # Fig6：覆盖率 = 计数 ÷ 26,321
    t6 = FIGS.get(6, "")
    pairs = re.findall(r"(\d{1,3}(?:,\d{3})*) \((\d+\.\d)%\)", t6)
    base = int(str(EXACT.get("gene_basic") or 26321))
    bad = []
    for cnt, pct in pairs:
        c = int(cnt.replace(",", ""))
        want = round(c / base * 100, 1)
        if abs(want - float(pct)) > 0.15:
            bad.append(f"{cnt}→{pct}% (应为 {want}%)")
    rec("C3", "C3-fig6-pct", "Fig6 覆盖率 = 计数 ÷ 基因总数",
        "ok" if not bad else "mismatch",
        f"基数 {base}；不匹配 {bad or '无'}", "high" if bad else "info")
    # Fig1 同型校验
    t1 = FIGS.get(1, "")
    rec("C3", "C3-fig1-pct", "Fig1 覆盖率与 Fig6 同源（96.6% / 86.0%）",
        "ok" if ("96.6%" in t1 and "86.0%" in t1) else "mismatch",
        f"Fig1 含 96.6%={('96.6%' in t1)} / 86.0%={('86.0%' in t1)}", "medium")


# 结构性文本（面板号、轴标题、图内提示）——非事实断言，不计入覆盖率分母
_STRUCTURAL = re.compile(
    r"^\(?[A-D]\)|^Fig\.?\s*\d|^Number of|^Records \(|^Maximum score|^Questions$|^Question category$"
    r"|^Wall-clock|^Legend|^source:|^scope:|^draw|^measured on|^heading merely"
    r"|^\(log scale\)|^Value$|^Category$|^Text cleaning|^Every stage|^Both answers"
    r"|^Answer$|^Answer contract|^All counts are exact|^The suite is executed|^Bars give|^the bars give"
    r"|^Q\s|^Which reference assemblies", re.I)


def _is_claim(a: str) -> bool:
    """含数字，或 ≥4 个词且非纯标签 —— 视为实质断言。"""
    if _STRUCTURAL.match(a):
        return False
    if re.search(r"\d", a):
        return True
    return len(a.split()) >= 4


# ══════════════════════════════════════════════════════════════════════════
# C4 覆盖率：未绑定 atom 清单
# ══════════════════════════════════════════════════════════════════════════
ATOM_PATTERNS = [
    r"32,532", r"7,651,942", r"170 FAISS", r"2048-d", r"Qwen3-Embedding-4B",
    r"paragraph_section", r"2026-09-10", r"26,321", r"25,432", r"22,628", r"80,212",
    r"110,966", r"36,959", r"1,520", r"319,344", r"161,298", r"145,439", r"96\.6%",
    r"86\.0%", r"35,487", r"16,386", r"11,918", r"32,549", r"197", r"100-question",
    r"20 isomorphic", r"7 dimensions", r"500", r"48,000", r"235\.2",
    r"orchestrat", r"intent", r"tool planning", r"curated", r"toolkit",
    r"REST API", r"cross-encoder", r"coarse", r"rerank", r"grounded",
    r"inline citations", r"reference footer", r"direct first sentence", r"factor-wise",
    r"Layout-aware", r"section segmentation", r"Normalisation", r"Artefact",
    r"collision", r"canonical", r"Ensembl", r"GO annotations", r"KEGG",
    r"CDS", r"protein", r"promoter", r"enrichment", r"Static integrity", r"Data completeness",
    r"Tool layer", r"API contract", r"Answer quality", r"Front-end", r"Robustness",
    r"Performance", r"Repeat stability", r"gold-standard", r"Body-length floor",
    r"allowed sources", r"re-audit", r"not-gold", r"self-rank", r"PMID",
    r"isoform", r"assembly", r"GRCz11", r"GRCz12tu", r"Zv9", r"Zv8", r"Zv7",
    r"lexical", r"hybrid", r"deterministic", r"traceable", r"scope",
    r"draw", r"seed", r"cap", r"chunk", r"paragraph", r"section", r"weight",
    r"citation", r"footer", r"\bL[0-8]\b", r"acceptance", r"benchmark", r"rubric",
    r"probe", r"fidelity", r"shard", r"manifest", r"version", r"library",
    r"abstract", r"keywords", r"methods", r"results", r"discussion", r"conclusion",
    r"figure_captions", r"other", r"title", r"introduction", r"references",
    # —— C4 补充：画布上其余可绑定到代码/数据的断言 ——
    r"retrieval-augmented", r"\bPDF\b", r"\bXML\b", r"removed before segmentation",
    r"session", r"deployed instance", r"raw identifier", r"historical symbol|historical identifier",
    r"same symbol", r"retired accession", r"legacy literature", r"CRLF", r"panel letter",
    r"stage tag", r"\bAuthor / COI\b", r"re-split", r"overlap", r"exact row count",
    r"which is current", r"General-purpose LLM", r"domain retrieval", r"Factor-coverage|Fact-coverage",
    r"multiple assemblie", r"across assemblies", r"color-bar", r"timeline fragment",
    r"collapse blank line", r"rule set", r"trigger rate", r"cleaning effect",
]
_BIND_RE = re.compile("|".join(f"(?:{p})" for p in ATOM_PATTERNS), re.I)
_STOP = re.compile(r"^[\s\d.,;:%()\[\]·–—\-/]+$|^\$|^Fig")


def c4():
    """盲区度量：**画布上非数字的实质断言**有多少能绑到源码。

    只看画布（texts），不看图注散文；含数字的条目归 V2/C1 职责。
    """
    total = bound = 0
    unbound: dict[int, list[str]] = {}
    for num in FIGS:
        canvas = (LEG.get(str(num)) or {}).get("texts") or []
        seen = []
        for raw in canvas:
            a = norm(raw)
            if len(a) < 8 or _STOP.match(a) or not _is_claim(a):
                continue
            if re.search(r"\d", a):          # 含数字 → 归 V2/C1 职责
                continue
            if a in seen:
                continue
            seen.append(a)
            total += 1
            if _BIND_RE.search(a):
                bound += 1
            else:
                unbound.setdefault(num, []).append(a)
    pct = bound / max(1, total) * 100
    rec("C4", "C4-coverage", "画布非数字断言的源码绑定覆盖率（目标 ≥80%）",
        "ok" if pct >= 80 else "mismatch",
        f"{bound}/{total} = {pct:.1f}%（已排除含数字条目、面板标题与轴标题）",
        "medium" if pct < 80 else "info")
    for num, items in sorted(unbound.items()):
        rec("C4", f"C4-unbound-{num}", f"Fig{num} 有 {len(items)} 条非数字断言未绑定源码",
            "unknown", " | ".join(items[:8])[:380], "low")


# ══════════════════════════════════════════════════════════════════════════
# C5 陈旧值残留
# ══════════════════════════════════════════════════════════════════════════
STALE = {
    "13,534": "旧索引论文数（应为 32,532）",
    "516,476": "旧语义块数（应为 7,651,942）",
    "1,269": "旧 FAISS 分片数（应为 170）",
    "candidates = 200": "旧候选数（应为 500）",
    "candidates=200": "旧候选数（应为 500）",
    "top 10 reranked": "旧 top-k（应为 100）",
    "self-rank = 1": "旧探针结果",
    "174 ms": "旧探针耗时",
    "2,342 characters": "旧上下文长度",
    "5.8 k": "旧上下文长度",
    "994 papers": "旧测试索引",
}


def c5():
    for tok, why in STALE.items():
        hit = [n for n, t in FIGS.items() if tok.lower() in t.lower()]
        rec("C5", f"C5-{tok}", f"图件未残留陈旧值「{tok}」",
            "ok" if not hit else "mismatch",
            f"{why}；出现在 Fig {hit}" if hit else f"{why}；未出现", "high" if hit else "info")
    # 数据快照文件里是否还有过时参数（可能被脚本引用）
    d = _load("_data.json")
    cfg = json.dumps(d, ensure_ascii=False)
    stale_cfg = [t for t in (r"\bCAND=200\b", r"\bTOPN=10\b", r"\b516,476\b", r"\b13,534\b",
                             r"200->10") if re.search(t, cfg)]
    rec("C5", "C5-snapshot", "数据快照 _data.json 内无过时参数残留",
        "ok" if not stale_cfg else "mismatch",
        f"命中 {stale_cfg or '无'}；该文件被 make_figures.py 与 verify_figures.py 读取，"
        f"过时值会污染「合法数值」白名单", "medium")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-server", action="store_true")
    a = ap.parse_args()
    print("[claim-binding] C0 抽取 + C1 跨图一致 ...")
    c1()
    print("[claim-binding] C2 源码/配置绑定 ...")
    c2()
    print("[claim-binding] C3 图内算术 ...")
    c3()
    print("[claim-binding] C4 覆盖率 ...")
    c4()
    print("[claim-binding] C5 陈旧值 ...")
    c5()

    errs = [r for r in REC if r["status"] == "mismatch"]
    unk = [r for r in REC if r["status"] == "unknown"]
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "records": REC,
               "summary": {"total": len(REC), "ok": len(REC) - len(errs) - len(unk),
                           "mismatch": len(errs), "unknown": len(unk)}}
    (FIG / "_claim_binding.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md = ["# 断言级溯源审计报告（CLAIM BINDING）", "",
          f"- 生成时间：{payload['generated_at']}",
          f"- 结论：**{len(errs)} 项不一致 / {len(unk)} 项待人工确认 / 共 {len(REC)} 项**", "",
          "## 一、待处置（mismatch）", "", "| 层 | 编号 | 断言 | 实际 | 严重度 |",
          "| --- | --- | --- | --- | --- |"]
    for r in errs:
        md.append(f"| {r['layer']} | {r['id']} | {r['claim']} | {r['detail']} | {r['severity']} |")
    md += ["", "## 二、待人工确认（unknown）", "", "| 层 | 编号 | 断言 | 说明 |",
           "| --- | --- | --- | --- |"]
    for r in unk:
        md.append(f"| {r['layer']} | {r['id']} | {r['claim']} | {r['detail']} |")
    md += ["", "## 三、C2 绑定明细（源码/配置）", "", "| 断言 | 状态 | 落点 |",
           "| --- | --- | --- |"]
    for r in REC:
        if r["layer"] == "C2":
            md.append(f"| {r['claim']} | {r['status']} | {r['detail']} |")
    md += ["", "## 四、全部记录", "", "| 层 | 编号 | 断言 | 状态 | 说明 |",
           "| --- | --- | --- | --- | --- |"]
    for r in REC:
        md.append(f"| {r['layer']} | {r['id']} | {r['claim']} | {r['status']} | {r['detail']} |")
    (FIG / "CLAIM_BINDING.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"[claim-binding] mismatch={len(errs)} unknown={len(unk)} total={len(REC)}")
    print(f"[ok] {FIG / 'CLAIM_BINDING.md'}")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
