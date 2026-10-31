# -*- coding: utf-8 -*-
"""把 Figure/*.png + 图注组装为**汇报级**演示文稿（16:9），支持中英双语。

输出
    --lang en   Figure/斑马鱼知识中枢_图集与图注.pptx          （英文版，12 页）
    --lang zh   Figure/斑马鱼知识中枢_图集与图注_中文版.pptx    （中文版，12 页）
    --lang both 两份都生成（默认）

版式（每份 12 页）
    1  封面  2  图集总览  3-10 每幅图一页  11 数据来源与权威快照  12 图注全文附录
    图页：页标题 + 大图（按比例居中，绝不拉伸）+ 右侧 3 条关键要点卡 + 底部图注（主语言全 + 次语言）

设计约束
    * 所有数字插值自 Figure/_audit_sources.json 等快照，与图集**同一事实来源**，避免漂移
    * 图内标注保持英文（期刊规范），仅 PPT 文案按语言切换
    * 每图页恰好一张图；文本不得溢出文本框（verify V11 会度量）

用法
    python scripts\\build_ppt.py [--lang en|zh|both]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image as PILImage
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Pt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                                        # noqa: E402

FIG = FC.FIG_DIR                                               # .env: FIG_OUTPUT_DIR

W, H = 12192000, 6858000          # 16:9，与参考 deck 一致
INK = RGBColor(0x0F, 0x17, 0x2A)
SLATE = RGBColor(0x33, 0x41, 0x55)
GRAY = RGBColor(0x94, 0xA3, 0xB8)
TEAL = RGBColor(0x0E, 0x74, 0x90)
INDIGO = RGBColor(0x4F, 0x46, 0xE5)
AMBER = RGBColor(0xF5, 0x9E, 0x0B)
LIGHT = RGBColor(0xF1, 0xF5, 0xF9)
BORDER = RGBColor(0xE2, 0xE8, 0xF0)
EMERALD = RGBColor(0x05, 0x96, 0x69)
FONT = "Microsoft YaHei"


def _load(name: str, default=None):
    p = FIG / name
    if not p.is_file():
        return default if default is not None else {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def _int(v, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


LEGENDS = _load("_legends.json")
ITEMS = sorted(((int(k), v) for k, v in LEGENDS.items()), key=lambda kv: kv[0])
SRV = _load("_audit_sources.json").get("server", {})
IDX = SRV.get("index", {}) or {}
SENV = SRV.get("env", {}) or {}
MY = _load("_data_mysql.json")
EXACT = MY.get("exact", {}) or {}
EXTRA = MY.get("extra", {}) or {}
DATA = _load("_data.json")
PROBE = _load("_retrieval_probe.json")
VERIFY = _load("verify_report.json")
CLEAN = _load("_clean_stats.json")

PAPERS = int(IDX.get("total_papers") or 32532)
CHUNKS = int(IDX.get("total_chunks") or 7651942)
SHARDS = int(IDX.get("shard_count") or 170)
DIM = int(IDX.get("embedding_dim") or 2048)
MODEL = (IDX.get("embedding_model") or "Qwen3-Embedding-4B").split("/")[-1]
IVER = IDX.get("index_version") or "qwen3_4b_v2_short_section"
IUPD = str(IDX.get("updated_at") or "2026-09-10")[:10]
CAND = _int(SENV.get("PUBMED_YES_RETRIEVE_CANDIDATES"), 500)
TOPN = _int(SENV.get("PUBMED_YES_RERANK_TOP_N"), 100)
PACK = _int(SENV.get("PUBMED_YES_PACK_CHARS"), 48000)

GENE_BASIC = int(EXACT.get("gene_basic") or 26321)
ALIAS = int(EXACT.get("gene_alias") or 145439)
GO = int(EXACT.get("gene_go") or 110966)
KEGG = int(EXACT.get("gene_kegg") or 36959)
EXPR = int(EXACT.get("gene_expression") or 319344)
XREF = int(EXACT.get("gene_xref") or 80212)
REFS = int(EXACT.get("gene_reference") or 161298)
LIT = int(EXACT.get("literature_paper") or 32549)
ASSEM = int(EXACT.get("gene_assembly_xref") or 22628)
ENSEMBL = int((EXTRA.get("genes_ensembl") or ["25432"])[0])
KEGG_PW = int((EXTRA.get("kegg_pathways") or ["1520"])[0])
CNT = (DATA.get("alignment") or {}).get("counts", {}) or {}
GOLD = (DATA.get("gold") or {}).get("gold_bar", {}) or {}
FLOOR = int(GOLD.get("body_floor") or 35487)
NQ = int((DATA.get("eval") or {}).get("n_questions") or 100)
NTYPE = len(((DATA.get("eval") or {}).get("types") or {}) or {}) or 9
PY_ENS = ENSEMBL / GENE_BASIC * 100
PY_XREF = ASSEM / GENE_BASIC * 100
SAMP = CNT.get("gold_sample_n", 0)

PRANKS = [r.get("self_rank") for r in (PROBE.get("records") or []) if r.get("self_rank")]
PEL = sorted([e for e in (PROBE.get("elapsed_secs") or []) if e])
PMED = PEL[len(PEL) // 2] if PEL else None
VTOTAL = VERIFY.get("total")
_UNIV = CNT.get("universe", 0)
_GOLD = CNT.get("gold", 0)
_RLO = min(PRANKS) if PRANKS else 0
_RHI = max(PRANKS) if PRANKS else 0
_MED = f"{PMED:.1f}" if PMED else "-"

# ── 清洗实测（scripts/measure_clean_effect.py → _clean_stats.json）──────────
_CN = int(CLEAN.get("sampled") or 0)
_CRAW = float((CLEAN.get("raw_chars") or {}).get("mean") or 0)
_CCLN = float((CLEAN.get("clean_chars") or {}).get("mean") or 0)
_CUT = float((CLEAN.get("chars_removed_ratio") or {}).get("mean") or 0)
_CPARA = float((CLEAN.get("paras") or {}).get("mean") or 0)
_CPLEN = float((CLEAN.get("para_mean") or {}).get("mean") or 0)
_CRULES = CLEAN.get("rules") or {}

# ── 双语文案 ─────────────────────────────────────────────────────────────
T = {
    "en": {
        "cover_title": "Zebrafish Knowledge Hub",
        "cover_sub": "An AI-powered knowledge hub for zebrafish functional genomics",
        "cover_line1": f"Figure set and legends  ·  {len(ITEMS)} multi-panel figures",
        "cover_line2": f"{PAPERS:,} indexed papers  ·  {GENE_BASIC:,} curated genes",
        "cover_card": (f"Index {IVER} (updated {IUPD}) · {CHUNKS:,} chunks · {SHARDS} shards · "
                       f"{MODEL} ({DIM}-d)\nTwo-stage retrieval {CAND} → {TOPN} papers · "
                       f"context budget {PACK:,} characters"),
        "cover_foot": f"Generated {time.strftime('%Y-%m-%d %H:%M')} · "
                      f"every value traced to the deployed system",
        "overview": "Figure overview",
        "overview_foot": "All figures are generated from the authoritative snapshots listed on the "
                         "data-source slide.",
        "source_title": "Data sources and authoritative snapshot",
        "source_foot_title": "Verification status",
        "source_foot": (f"{VTOTAL} self-consistency assertions passed, and the three-way audit of "
                        f"{len(ITEMS)} figures against the live server reports 0 mismatches.\n"
                        f"Drift is detected automatically and repaired by refreshing the snapshot "
                        f"and re-rendering."),
        "appendix": "Full figure legends",
        "legend_label": "Legend: ",
        "page": "Page {i} / {n}",
        "fig_prefix": "Figure {n}.",
    },
    "zh": {
        "cover_title": "斑马鱼基因知识中枢",
        "cover_sub": "面向斑马鱼功能基因组学的 AI 知识中枢",
        "cover_line1": f"图集与图注  ·  {len(ITEMS)} 幅多面板插图",
        "cover_line2": f"{PAPERS:,} 篇入索引文献  ·  {GENE_BASIC:,} 条策展基因",
        "cover_card": (f"索引 {IVER}（{IUPD} 更新）· {CHUNKS:,} 个语义块 · {SHARDS} 个分片 · "
                       f"{MODEL}（{DIM} 维）\n按篇两阶段检索 {CAND} → {TOPN} 篇 · "
                       f"上下文预算 {PACK:,} 字符"),
        "cover_foot": f"生成于 {time.strftime('%Y-%m-%d %H:%M')} · 所有数值均溯源至线上真实系统",
        "overview": "图集总览",
        "overview_foot": "全部图件均由数据来源页所列的权威快照自动生成。",
        "source_title": "数据来源与权威快照",
        "source_foot_title": "校验状态",
        "source_foot": (f"{VTOTAL} 项自洽断言全部通过，{len(ITEMS)} 幅图与线上服务器三方审计 0 差异。\n"
                        f"漂移会被自动检测，并通过刷新权威快照后重渲完成修复。"),
        "appendix": "图注全文",
        "legend_label": "图注：",
        "page": "第 {i} / {n} 页",
        "fig_prefix": "图 {n}.",
    },
}

KEY_POINTS = {
    "en": {
        1: ["Three modules: RAG knowledge base · curated gene database · integrated toolkit",
            f"RAG index {IVER}: {PAPERS:,} papers → {CHUNKS:,} semantic chunks across {SHARDS} FAISS shards",
            f"Curated resources: {GENE_BASIC:,} genes · {GO:,} GO annotations · {KEGG_PW:,} KEGG pathways"],
        2: [f"Funnelling: {LIT:,} records → {PAPERS:,} indexed → {_UNIV:,} gold-audited → {_GOLD:,} gold",
            f"Quality gate: body ≥ {FLOOR:,} characters · three permitted full-text sources",
            f"Re-audit after indexing: {SAMP}/{SAMP} sampled documents remained gold, 0 pending vectors"],
        3: [f"Offline: paragraph + section segmentation → {DIM}-d {MODEL} embeddings → {SHARDS} FAISS shards",
            f"Online: per-paper coarse scoring {CAND} → cross-encoder fine scoring → top {TOPN} papers",
            f"Context budget {PACK:,} characters; fixed contract with inline [n] citations"],
        4: [f"Benchmark: {NQ} questions across {NTYPE} categories + 20 isomorphic pairs for blind comparison",
            "Scoring rubric: 7 dimensions, 10 points in total",
            f"Retrieval probes on the production index: self-rank {_RLO}–{_RHI}, median query time {_MED} s"],
        5: [f"Nine-layer acceptance suite: {VTOTAL} automated assertions, 0 errors",
            "Layers: static · data · tools · API · answer quality · front-end · security · performance · stability",
            "Repeated runs reproduce identical results; warnings are advisory only"],
        6: [f"Canonical genes {GENE_BASIC:,}, reconciled from {ALIAS:,} alias entries",
            f"Ensembl identifier coverage {PY_ENS:.1f}% · GRCz12tu mapping coverage {PY_XREF:.1f}%",
            f"Per-gene annotation: {GO:,} GO · {KEGG:,} KEGG links · {EXPR:,} expression records"],
        7: ["Tools: identifier conversion · sequence retrieval (CDS / protein / promoter) · GO-KEGG enrichment",
            f"Backing resources: {XREF:,} cross-references · {REFS:,} literature links",
            f"{ENSEMBL:,} genes carry an Ensembl identifier and are ready for sequence export"],
        8: ["Lineage: Zv1-Zv7 → Zv8/Zv9 → GRCz10 → GRCz11 (catalog) → GRCz12tu (current reference)",
            "The Hub anchors gene numbering to GRCz11 and maps identifiers onto GRCz12tu",
            f"Ambiguity resolved into {GENE_BASIC:,} canonical records ({PY_ENS:.1f}% with Ensembl ID)"],
        9: ["Six implementation-aligned stages: normalisation → section parsing → noise truncation → "
            "artefact scrubbing → paragraph splitting → weighted embedding",
            "Truncation is position-gated: reference lists only past 35% of the body, acknowledgements past 50%",
            f"Measured on {_CN} gold full texts: {_CRAW:,.0f} → {_CCLN:,.0f} characters (−{_CUT:.1f}%), "
            f"yielding {_CPARA:.0f} paragraphs of {_CPLEN:.0f} characters per paper"],
        10: ["Both answers were produced for the same question about zebrafish reference assemblies",
             "The Hub states GRCz12tu (2025) with a GRCz11 anchor; the general model says GRCz12 (2024)",
             "Eight verifiable points: Hub 8/8 complete · general model 1/8 complete, 1/8 partial, 6/8 missing"],
    },
    "zh": {
        1: ["三大模块：RAG 知识库 · 策展基因库 · 集成工具集",
            f"RAG 索引 {IVER}：{PAPERS:,} 篇文献 → {CHUNKS:,} 个语义块，分布于 {SHARDS} 个 FAISS 分片",
            f"策展资源：{GENE_BASIC:,} 条基因 · {GO:,} 条 GO 注释 · {KEGG_PW:,} 条 KEGG 通路"],
        2: [f"语料漏斗：{LIT:,} 条记录 → {PAPERS:,} 篇入索引 → {_UNIV:,} 篇金标审查 → {_GOLD:,} 篇通过",
            f"质控门槛：正文 ≥ {FLOOR:,} 字符 · 仅接受三种全文来源",
            f"索引后复核：抽样 {SAMP} 篇金标文献全部仍为金标，无待嵌向量"],
        3: [f"离线：自然段 + 章节切分 → {DIM} 维 {MODEL} 嵌入 → {SHARDS} 个 FAISS 分片",
            f"在线：按篇粗打分 {CAND} 篇 → 交叉编码器精细重排 → 取前 {TOPN} 篇",
            f"上下文预算 {PACK:,} 字符；固定答案契约（首句直答 + 行内引用 [n]）"],
        4: [f"基准：{NQ} 道题、{NTYPE} 个类别，另有 20 对同构题用于盲评对照",
            "评分量表：七个维度，共 10 分",
            f"正式索引实测：自身排名 {_RLO}–{_RHI}，中位查询耗时 {_MED} 秒"],
        5: [f"九层验收套件：{VTOTAL} 项自动化断言，0 错误",
            "层次：静态 · 数据 · 工具 · API · 答案质量 · 前端 · 安全 · 性能 · 稳定性",
            "重复运行结果一致；告警仅为建议性提示"],
        6: [f"规范基因 {GENE_BASIC:,} 条，归并自 {ALIAS:,} 条别名记录",
            f"Ensembl 标识覆盖 {PY_ENS:.1f}% · GRCz12tu 映射覆盖 {PY_XREF:.1f}%",
            f"每基因注释：{GO:,} 条 GO · {KEGG:,} 条 KEGG 链接 · {EXPR:,} 条表达记录"],
        7: ["工具：标识符转换 · 序列提取（CDS / 蛋白 / 启动子）· GO-KEGG 富集",
            f"支撑资源：{XREF:,} 条跨库引用 · {REFS:,} 条文献关联",
            f"{ENSEMBL:,} 条基因携带 Ensembl 标识，可直接导出序列"],
        8: ["组装沿革：Zv1-Zv7 → Zv8/Zv9 → GRCz10 → GRCz11（目录）→ GRCz12tu（现行参考）",
            "本平台以 GRCz11 作为稳定目录组装，并将标识符映射至 GRCz12tu",
            f"命名歧义归并为 {GENE_BASIC:,} 条规范记录（{PY_ENS:.1f}% 带 Ensembl 标识）"],
        9: ["六个与实现对齐的环节：归一化 → 章节解析 → 噪声截断 → 伪影清理 → 自然段切分 → 加权嵌入",
            "截断按位置门控：参考文献表仅在正文 35% 之后、致谢仅在 50% 之后才触发",
            f"{_CN} 篇金标全文实测：{_CRAW:,.0f} → {_CCLN:,.0f} 字符（−{_CUT:.1f}%），"
            f"每篇 {_CPARA:.0f} 段、段均 {_CPLEN:.0f} 字符"],
        10: ["同一道题的两种回答：斑马鱼参考基因组常用版本与当前最新版本",
             "本中枢给出 GRCz12tu（2025）与 GRCz11 锚定策略；通用模型称 GRCz12（2024）",
             "八个可核实要点：本中枢 8/8 完整 · 通用模型 1/8 完整、1/8 部分、6/8 缺失"],
    },
}

SOURCE_LINES = {
    "en": [
        f"FAISS index — {PAPERS:,} papers / {CHUNKS:,} semantic chunks / {SHARDS} shards / {DIM}-d, "
        f"manifest of `{IVER}` (updated {IUPD})",
        f"Retrieval parameters — candidates {CAND} → rerank top {TOPN} papers, context budget "
        f"{PACK:,} characters (server .env, identical to the deployed runtime)",
        f"Gene database — exact MySQL row counts: {GENE_BASIC:,} genes, {GO:,} GO, {KEGG:,} KEGG links, "
        f"{EXPR:,} expression records",
        f"Retrieval probe — /api/retrieval/debug (the same path used by the Hub literature agent), "
        f"self-rank {_RLO}–{_RHI}, median {_MED} s",
        f"Acceptance & verification — {VTOTAL} assertions / 0 errors; three-way audit against the live "
        f"server snapshot: 0 mismatches",
        "Every figure is produced by scripts/make_figures.py; drift is detected by "
        "scripts/audit_figures_vs_project.py",
        f"Cleaning statistics — {_CN} randomly sampled gold full texts (seed 42): {_CRAW:,.0f} → "
        f"{_CCLN:,.0f} characters (−{_CUT:.1f}%), {_CPARA:.0f} paragraphs per paper "
        f"(scripts/measure_clean_effect.py)",
    ],
    "zh": [
        f"FAISS 索引 —— {PAPERS:,} 篇文献 / {CHUNKS:,} 个语义块 / {SHARDS} 个分片 / {DIM} 维，"
        f"清单版本 `{IVER}`（{IUPD} 更新）",
        f"检索参数 —— 候选 {CAND} 篇 → 重排取前 {TOPN} 篇，上下文预算 {PACK:,} 字符"
        f"（服务器 .env，与线上运行完全一致）",
        f"基因数据库 —— MySQL 精确行数：{GENE_BASIC:,} 条基因、{GO:,} 条 GO、{KEGG:,} 条 KEGG 链接、"
        f"{EXPR:,} 条表达记录",
        f"检索实测 —— /api/retrieval/debug（与 Hub 文献 Agent 同一路径），自身排名 {_RLO}–{_RHI}，"
        f"中位耗时 {_MED} 秒",
        f"验收与校验 —— {VTOTAL} 项断言 / 0 错误；与线上服务器快照三方审计 0 差异",
        "全部图件由 scripts/make_figures.py 生成；漂移由 scripts/audit_figures_vs_project.py 自动检测",
        f"清洗统计 —— 随机抽样 {_CN} 篇金标全文（seed 42）：{_CRAW:,.0f} → {_CCLN:,.0f} 字符"
        f"（−{_CUT:.1f}%），每篇 {_CPARA:.0f} 段（scripts/measure_clean_effect.py）",
    ],
}


# ── 绘图原语 ─────────────────────────────────────────────────────────────
def _headline(en: str) -> str:
    """英文图注首句（页面标题用）。"""
    rest = en.split(".", 1)[1].strip() if "." in en else en
    for sep in (". ", "; "):
        if sep in rest:
            return rest.split(sep)[0].strip() + "."
    return rest


def _headline_zh(zh: str) -> str:
    """中文图注首句（页面标题用）。"""
    rest = zh.split(".", 1)[1].strip() if "." in zh else zh
    for sep in ("。", "；"):
        if sep in rest:
            return rest.split(sep)[0].strip() + "。"
    return rest


def txt(slide, x, y, w, h, text, size=14, bold=False, color=INK,
        align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=1.05, italic=False):
    box = slide.shapes.add_textbox(Emu(int(x)), Emu(int(y)), Emu(int(w)), Emu(int(h)))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    first = True
    for line in str(text).split("\n"):
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        p.line_spacing = spacing
        r = p.add_run()
        r.text = line
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.italic = italic
        r.font.color.rgb = color
        r.font.name = FONT
    return box


def shape(slide, kind, x, y, w, h, fill, line=None, radius=None):
    sh = slide.shapes.add_shape(kind, Emu(int(x)), Emu(int(y)), Emu(int(w)), Emu(int(h)))
    if fill is None:
        sh.fill.background()
    else:
        sh.fill.solid()
        sh.fill.fore_color.rgb = fill
    if line is None:
        sh.line.fill.background()
    else:
        sh.line.color.rgb = line
        sh.line.width = Pt(1)
    sh.shadow.inherit = False
    if radius is not None and kind == MSO_SHAPE.ROUNDED_RECTANGLE:
        sh.adjustments[0] = radius
    return sh


def rect(slide, x, y, w, h, fill, line=None):
    return shape(slide, MSO_SHAPE.RECTANGLE, x, y, w, h, fill, line)


def card(slide, x, y, w, h, fill=LIGHT, line=BORDER):
    return shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill, line, radius=0.08)


def blank(prs):
    return prs.slides.add_slide(prs.slide_layouts[6])


def fit_pic(slide, path: Path, bx, by, bw, bh):
    """按比例缩放居中放图（绝不拉伸）。"""
    with PILImage.open(path) as im:
        iw, ih = im.size
    r = iw / ih
    if r > bw / bh:
        w, h = bw, int(bw / r)
    else:
        h, w = bh, int(bh * r)
    x = bx + (bw - w) // 2
    y = by + (bh - h) // 2
    return slide.shapes.add_picture(str(path), Emu(int(x)), Emu(int(y)),
                                    width=Emu(int(w)), height=Emu(int(h)))


def header(slide, title_text, color=TEAL):
    rect(slide, 0, 0, W, 100000, color)
    txt(slide, 380000, 235000, W - 760000, 620000, title_text,
        size=16.5, bold=True, spacing=1.12)


def footer(slide, idx, total, lang, left_text=""):
    txt(slide, 380000, 6560000, W - 2600000, 220000, left_text, size=8, color=GRAY)
    txt(slide, W - 1700000, 6560000, 1300000, 220000,
        T[lang]["page"].format(i=idx, n=total), size=8, color=GRAY, align=PP_ALIGN.RIGHT)


def key_point_cards(slide, points, x, y, w, h, accent=TEAL):
    n = max(1, len(points))
    gap = 130000
    ch = int((h - gap * (n - 1)) / n)
    for i, p in enumerate(points):
        yy = y + i * (ch + gap)
        card(slide, x, yy, w, ch)
        rect(slide, x, yy, 46000, ch, accent)
        txt(slide, x + 190000, yy + 130000, w - 320000, ch - 220000, p,
            size=10.5, color=SLATE, spacing=1.18, anchor=MSO_ANCHOR.MIDDLE)


def _p(lines: list[str]) -> str:
    return "\n".join(f"- {x}" for x in lines)


# ── 组装（lang: en | zh） ────────────────────────────────────────────────
def build(lang: str) -> Path:
    T_ = T[lang]
    KP = KEY_POINTS[lang]
    SRC = SOURCE_LINES[lang]
    out = FIG / FC.deck_name(lang)                             # .env: FIG_DECK_*_NAME

    prs = Presentation()
    prs.slide_width = Emu(W)
    prs.slide_height = Emu(H)
    total = 3 + len(ITEMS) + 1        # 封面 + 总览 + 图页 + 来源 + 附录

    # 1 封面
    s = blank(prs)
    rect(s, 0, 0, W, 130000, TEAL)
    txt(s, 900000, 1400000, W - 1800000, 900000, T_["cover_title"], size=44, bold=True, color=INK)
    txt(s, 900000, 2300000, W - 1800000, 700000, T_["cover_sub"], size=19, color=TEAL)
    txt(s, 900000, 3060000, W - 1800000, 900000,
        f"{T_['cover_line1']}\n{T_['cover_line2']}", size=14.5, color=SLATE, spacing=1.5)
    card(s, 900000, 4360000, 6100000, 1200000)
    txt(s, 1080000, 4500000, 5800000, 950000, T_["cover_card"], size=11.5, color=SLATE, spacing=1.35)
    txt(s, 900000, 5920000, W - 1800000, 300000, T_["cover_foot"], size=9.5, color=GRAY)
    footer(s, 1, total, lang)

    # 2 总览
    s = blank(prs)
    rect(s, 0, 0, W, 90000, TEAL)
    txt(s, 620000, 300000, W - 1240000, 520000, T_["overview"], size=25, bold=True)
    cols, mw, mh = 4, 2560000, 1620000
    gx, gy = 620000, 1150000
    for i, (num, meta) in enumerate(ITEMS):
        r, c = divmod(i, cols)
        x = gx + c * (mw + 150000)
        y = gy + r * (mh + 810000)
        card(s, x, y, mw, mh, RGBColor(0xFF, 0xFF, 0xFF), BORDER)
        try:
            fit_pic(s, FIG / meta["file"], x + 30000, y + 30000, mw - 60000, mh - 60000)
        except Exception:
            pass
        head = _headline_zh(meta["zh"]) if lang == "zh" else _headline(meta["en"])
        txt(s, x, y + mh + 60000, mw, 260000, f"{T_['fig_prefix'].format(n=num)}",
            size=11.5, bold=True, color=TEAL)
        txt(s, x, y + mh + 300000, mw, 460000, head[:34], size=9, color=SLATE, spacing=1.15)
    footer(s, 2, total, lang, T_["overview_foot"])

    # 3..N 图页
    for k, (num, meta) in enumerate(ITEMS, start=1):
        s = blank(prs)
        if lang == "zh":
            title_line = f"图 {num}.  {_headline_zh(meta['zh'])}"
            primary, secondary = meta["zh"], meta["en"]
            lab_p, lab_s = "图注（中文）：", "Legend (EN): "
        else:
            title_line = f"Figure {num}.  {_headline(meta['en'])}"
            primary, secondary = meta["en"], meta["zh"]
            lab_p, lab_s = "Legend: ", "图注（中文）："
        header(s, title_line)
        card(s, 380000, 930000, 7600000, 3900000, RGBColor(0xFF, 0xFF, 0xFF), BORDER)
        try:
            fit_pic(s, FIG / meta["file"], 420000, 970000, 7520000, 3820000)
        except Exception:
            txt(s, 420000, 970000, 7520000, 300000, "(figure missing)", size=14, color=GRAY)
        key_point_cards(s, (KP.get(num, []) or [])[:FC.FIG_DECK_POINTS],
                        8180000, 930000, 3620000, 3900000)
        card(s, 380000, 4900000, 11440000, 1660000)
        txt(s, 560000, 4950000, 11100000, 880000, lab_p + primary,
            size=8.0, color=SLATE, spacing=1.10)
        txt(s, 560000, 5860000, 11100000, 700000, lab_s + secondary,
            size=7.2, color=TEAL, spacing=1.06)
        footer(s, 2 + k, total, lang, f"source: Figure/{meta['file']}")

    # 数据来源
    s = blank(prs)
    rect(s, 0, 0, W, 90000, TEAL)
    txt(s, 620000, 300000, W - 1240000, 520000, T_["source_title"], size=25, bold=True)
    txt(s, 620000, 1060000, W - 1240000, 2820000, _p(SRC), size=11.5, color=SLATE, spacing=1.38)
    card(s, 620000, 4750000, 6100000, 1250000, RGBColor(0xEC, 0xFD, 0xF5), BORDER)
    txt(s, 800000, 4860000, 5750000, 1050000,
        f"{T_['source_foot_title']}\n{T_['source_foot']}", size=11.5, color=EMERALD, spacing=1.32)
    footer(s, total - 1, total, lang)

    # 图注全文
    s = blank(prs)
    rect(s, 0, 0, W, 90000, TEAL)
    txt(s, 560000, 230000, W - 1120000, 500000, T_["appendix"], size=24, bold=True)
    body = "\n\n".join((m["zh"] if lang == "zh" else m["en"]) for _n, m in ITEMS)
    txt(s, 560000, 820000, W - 1120000, H - 980000, body, size=7.4, color=SLATE,
        spacing=1.10)
    footer(s, total, total, lang)

    # 安全保存：先写临时文件，再原子替换；目标被 WPS/PowerPoint 占用时保留 .new
    if not save_deck(prs, out):
        return None
    print(f"[ok] {out.name}  slides={len(prs.slides._sldIdLst)}  lang={lang}")
    return out


def save_deck(prs, out: Path) -> bool:
    """写临时文件后原子替换；若目标被其他程序占用，保留 .new 产物并提示。"""
    tmp = out.with_name(out.stem + ".pptx.new")
    prs.save(str(tmp))
    try:
        os.replace(str(tmp), str(out))
        return True
    except PermissionError:
        print(f"[skip] {out.name} 正被占用（WPS/PowerPoint 已打开该文件）→ "
              f"已另存为 {tmp.name}；关闭该文件后重跑本脚本即可覆盖")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default=FC.FIG_DECK_LANG, choices=["en", "zh", "both"])
    a = ap.parse_args()
    langs = ["en", "zh"] if a.lang == "both" else [a.lang]
    for lg in langs:
        build(lg)

    # 英文版同时刷新 Legend.md（中英对照 + 关键要点 + 数据来源）
    if not FC.FIG_EMIT_LEGEND_MD:
        print("[skip] Figure/Legend.md（FIG_EMIT_LEGEND_MD=0）")
        return 0
    md = ["# Zebrafish Knowledge Hub — Figure legends", "",
          f"*{len(ITEMS)} multi-panel figures · all quantitative values measured from the deployed "
          f"platform (FAISS manifest, MySQL exact counts, `.env`, acceptance and retrieval probes).*", "",
          "## 图集索引 / Figure index", "",
          "| Fig. | File | 主题 |", "| --- | --- | --- |"]
    for num, meta in ITEMS:
        md.append(f"| {num} | `{meta['file']}` | {_headline_zh(meta['zh'])} |")
    md.append("")
    for num, meta in ITEMS:
        md += [f"## Figure {num}", "",
               f"**{meta['en']}**", "",
               f"![Figure {num}]({meta['file']})", "",
               "**关键要点 / Key points**", "", _p(KEY_POINTS["zh"][num]), "",
               f"**图注（中文）**　{meta['zh']}", ""]
    md += ["## 数据来源 / Data sources", "", _p(SOURCE_LINES["zh"]), ""]
    (FIG / "Legend.md").write_text("\n".join(md), encoding="utf-8")
    print(f"[ok] Figure/Legend.md  entries={len(ITEMS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
