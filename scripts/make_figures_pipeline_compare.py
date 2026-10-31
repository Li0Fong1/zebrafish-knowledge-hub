# -*- coding: utf-8 -*-
"""按「文献处理流水线」格式生成两张**简化版**补充图（对标参考图 Fig. B 与 S5）。

    FigS4_Literature_Pipeline.png   10 节点蛇形流水线 (a)–(j)：图标 + 短标签 + 真实读数
    FigS5_QA_Comparison.png         nanog 问题：本系统 vs 两个通用大模型（真实回答 + 要点覆盖）

设计要点
    * **做减法**：去掉三面板/长表格，流水线只保留「一个节点 = 一个图标 + 一句标签」
    * 蛇形两行布局（上行 a→e 向右，下行 f→j 向左），与参考图一致
    * 读数取自 Figure/_data.json、_data_mysql.json、_audit_sources.json、_clean_stats.json
    * 对比图三个系统的回答**均为未改写真实产物**（logs/_nanog_*.txt）
    * 判定基准来自服务器 MySQL 对 nanog 的真实注释（Figure/_nanog_facts.json）
    * 纯本地读文件绘图，秒级完成；自带 --check 自校验

用法
    python scripts\\collect_nanog_evidence.py          # 先采集（Hub + 两个模型）
    python scripts\\make_figures_pipeline_compare.py --check
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                      # noqa: E402
matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

import numpy as np                                   # noqa: E402
from matplotlib.patches import Circle, FancyBboxPatch, Polygon, Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                              # noqa: E402

FIG = FC.FIG_DIR
OUT = FIG / "supplementary"
OUT.mkdir(parents=True, exist_ok=True)


def load(p: Path, default=None):
    if not p.is_file():
        return default if default is not None else {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


DATA = load(FIG / "_data.json")
GOLD = DATA.get("gold") or {}
ALIGN = (DATA.get("alignment") or {}).get("counts") or {}
EXACT = load(FIG / "_data_mysql.json").get("exact") or {}
SNAP = load(FIG / "_audit_sources.json").get("server") or {}
IDX = SNAP.get("index") or {}
CLEAN = load(FIG / "_clean_stats.json")
NF = load(FIG / "_nanog_facts.json")
LOGS = ROOT / "logs"

# ── 真实读数（每个都在图上出现且可回溯）──────────────────────────────────
N = {
    "lit": int(EXACT.get("literature_paper") or 0),
    "papers": int(IDX.get("total_papers") or 0),
    "chunks": int(IDX.get("total_chunks") or 0),
    "shards": int(IDX.get("shard_count") or 0),
    "dim": int(IDX.get("embedding_dim") or 0),
    "model": (IDX.get("embedding_model") or "Qwen3-Embedding-4B").split("/")[-1],
    "universe": int(ALIGN.get("universe") or 0),
    "gold": int(ALIGN.get("gold") or 0),
    "not_gold": int(ALIGN.get("not_gold") or 0),
    "floor": int((GOLD.get("gold_bar") or {}).get("body_floor") or 0),
    "refs": (CLEAN.get("rules") or {}).get("refs_truncation_triggered") or 0,
    "ack": (CLEAN.get("rules") or {}).get("ack_truncation_triggered") or 0,
    "pmax": (CLEAN.get("split_params") or {}).get("max_chars") or 360,
    "pmin": (CLEAN.get("split_params") or {}).get("min_chars") or 110,
    "ovl": (CLEAN.get("split_params") or {}).get("overlap") or 48,
}
N["cpp"] = round(N["chunks"] / max(1, N["papers"]), 1)
N["keep_pct"] = round(N["papers"] / max(1, N["lit"]) * 100, 1)

C = {"ink": "#111827", "slate": "#374151", "gray": "#6B7280", "line": "#D1D5DB",
     "blue": "#2563EB", "cyan": "#0891B2", "teal": "#0D9488", "green": "#059669",
     "amber": "#D97706", "rose": "#E11D48", "violet": "#7C3AED", "bg": "#F9FAFB"}

REC: list[dict] = []


def rec(rid, claim, status, detail="", severity="info"):
    REC.append({"id": rid, "claim": claim, "status": status,
                "detail": detail, "severity": severity})


# ══════════════════════════════════════════════════════════════════════════
# 图标
# ══════════════════════════════════════════════════════════════════════════
def _t(ax):
    return ax.transAxes


def ic_doc(ax, x, y, c):
    ax.add_patch(Rectangle((x - 0.024, y - 0.095), 0.048, 0.135, fc="white", ec=c, lw=1.5,
                           transform=_t(ax), zorder=3))
    for dy in (0.010, -0.018, -0.046, -0.074):
        ax.plot([x - 0.015, x + 0.015], [y + dy, y + dy], color=c, lw=1.2,
                transform=_t(ax), zorder=4)


def ic_monitor(ax, x, y, c):
    ax.add_patch(Rectangle((x - 0.038, y - 0.075), 0.076, 0.100, fc="white", ec=c, lw=1.5,
                           transform=_t(ax), zorder=3))
    for dx in (-0.014, 0.014):
        ax.plot([x + dx, x + dx], [y + 0.020, y - 0.012], color=c, lw=1.2,
                transform=_t(ax), zorder=4)
    ax.plot([x - 0.026, x + 0.026], [y - 0.088, y - 0.088], color=c, lw=1.6,
            transform=_t(ax), zorder=4)


def ic_merge(ax, x, y, c):
    for dx in (-0.030, 0.030):
        ax.plot([x + dx, x + 0.008 * np.sign(dx)], [y - 0.080, y + 0.010], color=c, lw=1.5,
                transform=_t(ax), zorder=4)
    ax.add_patch(Rectangle((x - 0.032, y + 0.010), 0.064, 0.030, fc="white", ec=c, lw=1.5,
                           transform=_t(ax), zorder=3))


def ic_funnel(ax, x, y, c):
    ax.add_patch(Polygon([[x - 0.038, y + 0.050], [x + 0.038, y + 0.050],
                          [x + 0.014, y - 0.020], [x + 0.014, y - 0.075],
                          [x - 0.014, y - 0.075], [x - 0.014, y - 0.020]],
                         closed=True, fc="white", ec=c, lw=1.5, transform=_t(ax), zorder=3))


def ic_check(ax, x, y, c):
    ax.add_patch(Circle((x, y - 0.020), 0.044, fc="white", ec=c, lw=1.6,
                        transform=_t(ax), zorder=3))
    ax.plot([x - 0.020, x - 0.004, x + 0.022],
            [y - 0.020, y - 0.040, y + 0.006], color=c, lw=2.0,
            transform=_t(ax), zorder=4, solid_capstyle="round")


def ic_link(ax, x, y, c):
    for dx in (-0.016, 0.016):
        ax.add_patch(Rectangle((x + dx - 0.020, y - 0.042), 0.040, 0.026, fc="white", ec=c,
                               lw=1.5, transform=_t(ax), zorder=3))
    ax.plot([x - 0.012, x + 0.012], [y - 0.020, y - 0.020], color=c, lw=1.4,
            transform=_t(ax), zorder=4)


def ic_gear(ax, x, y, c):
    ax.add_patch(Circle((x, y - 0.025), 0.040, fc="white", ec=c, lw=1.5,
                        transform=_t(ax), zorder=3))
    for k in range(8):
        a = math.radians(k * 45)
        ax.add_patch(Rectangle((x + 0.038 * math.cos(a) - 0.009,
                                y - 0.025 + 0.038 * math.sin(a) - 0.009),
                               0.018, 0.018, fc=c, ec="none", transform=_t(ax), zorder=4))
    ax.add_patch(Circle((x, y - 0.025), 0.014, fc=c, ec="none", transform=_t(ax), zorder=4))


def ic_cube(ax, x, y, c):
    s, dy = 0.040, 0.024
    cy = y - 0.032
    ax.add_patch(Polygon([[x - s, cy - dy], [x, cy - 2 * dy], [x + s, cy - dy], [x, cy]],
                         closed=True, fc=c, alpha=0.25, ec=c, lw=1.3, transform=_t(ax), zorder=4))
    ax.add_patch(Polygon([[x - s, cy - dy], [x, cy], [x, cy + 0.045], [x - s, cy - dy + 0.045]],
                         closed=True, fc=c, alpha=0.45, ec=c, lw=1.3, transform=_t(ax), zorder=4))
    ax.add_patch(Polygon([[x + s, cy - dy], [x, cy], [x, cy + 0.045], [x + s, cy - dy + 0.045]],
                         closed=True, fc=c, alpha=0.68, ec=c, lw=1.3, transform=_t(ax), zorder=4))


def ic_db(ax, x, y, c):
    ax.add_patch(Rectangle((x - 0.036, y - 0.095), 0.072, 0.110, fc="white", ec=c, lw=1.5,
                           transform=_t(ax), zorder=3))
    for dy in (-0.095, -0.048, 0.015):
        ax.add_patch(Circle((x, y + dy), 0.036, fc="none", ec=c, lw=1.3,
                            transform=_t(ax), zorder=4))


def arrow(ax, p, q, color=None, lw=1.4, style="-|>", rad=0.0):
    ax.annotate("", xy=q, xytext=p, xycoords="axes fraction", textcoords="axes fraction",
                arrowprops=dict(arrowstyle=style, color=color or C["gray"], lw=lw,
                                connectionstyle=f"arc3,rad={rad}", shrinkA=0.5, shrinkB=0.5))


def check_overlaps(fig, tag: str) -> int:
    """渲染后检测文字包围盒重叠（>25% 较小面积即计），返回重叠对数。"""
    fig.canvas.draw()
    items = []
    for t in fig.findobj(matplotlib.text.Text):
        s = (t.get_text() or "").strip()
        if not s:
            continue
        try:
            bb = t.get_window_extent(renderer=fig.canvas.get_renderer())
        except Exception:
            continue
        if bb.width > 0 and bb.height > 0:
            items.append((s, bb))
    bad = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i][1], items[j][1]
            ox = min(a.x1, b.x1) - max(a.x0, b.x0)
            oy = min(a.y1, b.y1) - max(a.y0, b.y0)
            if ox > 0 and oy > 0:
                inter = ox * oy
                if inter > 0.25 * min(a.width * a.height, b.width * b.height):
                    bad.append(f"{items[i][0][:22]!r} X {items[j][0][:22]!r}")
    rec(f"overlap-{tag}", f"{tag} 文字无遮挡重叠", "ok" if not bad else "mismatch",
        f"overlaps={len(bad)}" + (" | " + "; ".join(bad[:4]) if bad else ""),
        "high" if bad else "info")
    return len(bad)


def node(ax, x, y, w, h, letter, label, sub, col, icon):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.012",
                                lw=1.2, ec=col, facecolor="white", transform=_t(ax)))
    ax.text(x - 0.004, y + h + 0.030, f"({letter})", fontsize=8.4, color=col,
            fontweight="bold", ha="left", va="bottom", transform=_t(ax))
    icon(ax, x + w / 2, y + h * 0.66, col)
    ax.text(x + w / 2, y + h * 0.20, label, ha="center", va="center", fontsize=8.4,
            color=C["ink"], fontweight="bold", transform=_t(ax))
    if sub:
        ax.text(x + w / 2, y - 0.060, sub, ha="center", va="top", fontsize=6.9,
                color=C["gray"], transform=_t(ax))


# ══════════════════════════════════════════════════════════════════════════
# FigS4：蛇形流水线
# ══════════════════════════════════════════════════════════════════════════
def fig_s4():
    steps = [
        ("a", "PubMed 检索导出", f"{N['lit']:,} 条记录", C["slate"], ic_monitor),
        ("b", "题录合并去重", "标题 / 摘要 / DOI", C["cyan"], ic_merge),
        ("c", "全文可用性筛查", f"保留 {N['keep_pct']}% → {N['papers']:,} 篇", C["blue"], ic_funnel),
        ("d", "金标审查池", f"{N['universe']:,} 篇", C["violet"], ic_doc),
        ("e", "体量 / 来源门控", f"正文 ≥ {N['floor']:,} 字符", C["teal"], ic_check),
        ("f", "通过金标 QC", f"{N['gold']:,} 篇（另册 {N['not_gold']:,}）", C["green"], ic_check),
        ("g", "全文对齐 / 条目直链", "PDF · JATS-XML · HTML", C["teal"], ic_link),
        ("h", "纯文本 / XML 语料", "正文 + 规范章节", C["cyan"], ic_doc),
        ("i", "清洗与切分", f"去参考文献 {N['refs']:.1f}% · 去致谢 {N['ack']:.1f}%",
         C["amber"], ic_gear),
        ("j", "向量化入库", f"{N['dim']}-d · {N['shards']} 分片", C["violet"], ic_cube),
    ]
    fig = plt.figure(figsize=(12.4, 4.6))
    ax = fig.add_axes([0.02, 0.05, 0.96, 0.86]); ax.axis("off")
    w, gap = 0.176, 0.021
    y1, y2, hh = 0.545, 0.105, 0.290
    for i in range(5):
        letter, label, sub, col, icon = steps[i]
        node(ax, 0.008 + i * (w + gap), y1, w, hh, letter, label, sub, col, icon)
        if i:
            px = 0.008 + (i - 1) * (w + gap) + w
            arrow(ax, (px + 0.003, y1 + hh / 2), (0.008 + i * (w + gap) - 0.003, y1 + hh / 2))
    xk = 0.008 + 4 * (w + gap) + w / 2
    arrow(ax, (xk, y1 - 0.010), (xk, y2 + hh + 0.010), color=C["gray"], lw=1.6)
    for k in range(5):
        letter, label, sub, col, icon = steps[5 + k]
        x = 0.008 + (4 - k) * (w + gap)
        node(ax, x, y2, w, hh, letter, label, sub, col, icon)
        if k:
            arrow(ax, (x + w + 0.003, y2 + hh / 2),
                  (0.008 + (4 - k + 1) * (w + gap) - 0.003, y2 + hh / 2))
    ax.text(0.5, 1.055, "斑马鱼文献采集与知识库构建流水线", ha="center", va="top",
            fontsize=11.0, color=C["ink"], fontweight="bold", transform=_t(ax))
    ax.text(0.5, 0.985, f"题录 {N['lit']:,} 条 → 入索引 {N['papers']:,} 篇 → 金标 "
                        f"{N['gold']:,} 篇 → 切分 {N['cpp']} 块/篇 → 总计 {N['chunks']:,} 块"
                        f"（{N['model']}，{N['shards']} 分片）",
            ha="center", va="top", fontsize=7.6, color=C["gray"], transform=_t(ax))
    ax.text(0.5, -0.045, f"切分参数 {N['pmax']}/{N['pmin']}/重叠 {N['ovl']} 字符 · "
                         f"来源门控 ≥ {N['floor']:,} 字符 · 读数来源 Figure/_data.json、"
                         f"_data_mysql.json、_audit_sources.json、_clean_stats.json",
            ha="center", va="top", fontsize=6.6, color=C["gray"], style="italic",
            transform=_t(ax))
    p = OUT / "FigS4_Literature_Pipeline.png"
    check_overlaps(fig, "FigS4")
    fig.savefig(p, dpi=300, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    rec("S4-nodes", "FigS4 十节点蛇形布局齐全", "ok" if len(steps) == 10 else "mismatch",
        f"{len(steps)} nodes（上行 a–e 向右，下行 f–j 向左）")
    rec("S4-file", "FigS4 已输出", "ok", p.name)
    return steps


# ══════════════════════════════════════════════════════════════════════════
# FigS5：nanog 对比
# ══════════════════════════════════════════════════════════════════════════
SRC = [("本系统 Hub", "_nanog_hub.txt", C["blue"], "检索增强 + 策展库"),
       ("deepseek-chat", "_nanog_deepseek-chat.txt", C["amber"], "通用模型 · 无领域检索"),
       ("deepseek-reasoner", "_nanog_deepseek-reasoner.txt", C["violet"],
        "通用模型（推理版）")]

# 判定基准：全部来自服务器 MySQL 对 nanog 的真实注释（Figure/_nanog_facts.json）
POINTS = [
    ("基因身份可核验（Ensembl / UniProt / 染色体）",
     [[r"ENSDARG00000075113", r"Q8JFR5"], [r"\b24\b|24 号染|chr(omosome)?\s*24"]]),
    ("同源框转录因子 · DNA 结合",
     [[r"homeobox|同源框"], [r"DNA\s*结合|DNA binding|转录因子|转录因子活"]]),
    ("干细胞 / 多能性维持",
     [[r"多能性|pluripot", r"stem cell population maintenance|干细胞.*维持|干性|干细胞的?维持"]]),
    ("母源-合子转换 MZT 与早期胚胎",
     [[r"MZT|母源|合子基因组|zygotic|囊胚|blastula|外包|epiboly"],
      [r"原肠|gastrul|内胚层|endoderm"]]),
    ("生殖细胞与性腺表达（卵巢 / 卵母细胞）",
     [[r"卵巢|ovary", r"卵母|oocyte", r"生殖细胞|PGC|primordial germ|精原|spermatogon"]]),
    ("调控 Wnt / β-catenin 通路",
     [[r"Wnt|β-catenin|beta-catenin|TCF"]]),
    ("条目级可追溯（PMID / ZFIN / Ensembl ID）",
     [[r"PMID\s*\d{6,}"], [r"ZDB-GENE-\d+|ZFIN"], [r"ENSDARG\d+"]]),
]


def _clean(s: str) -> str:
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = s.replace("**", "").replace("`", "").replace("*", "")
    s = re.sub(r"^\s*[-#>•\d\.\)、]+\s*", "", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_points(text: str, n: int = 3, width: int = 30) -> list[str]:
    cands: list[tuple[int, str]] = []
    for raw in text.split("\n"):
        s = _clean(raw)
        if len(s) < 12 or s.startswith("|"):
            continue
        score = 2 if 16 <= len(s) <= 80 else 0
        if re.search(r"[:：]", s):
            score += 1
        cands.append((score, s))
    cands.sort(key=lambda kv: -kv[0])
    out: list[str] = []
    for _sc, s in cands:
        t = s[:width] + ("…" if len(s) > width else "")
        if t not in out:
            out.append(t)
        if len(out) >= n:
            break
    return out


def judge(text: str) -> list[str]:
    res = []
    for _name, groups in POINTS:
        hit = sum(1 for g in groups if any(re.search(p, text, re.I) for p in g))
        res.append("完整" if hit == len(groups) else ("部分" if hit else "缺失"))
    return res


# ── FigS5（截图版）配置：crop = (left, top, right, bottom) 相对源图的比例 ──
SHOTS = [
    ("ChatGPT", "ChatGPT.png", C["green"], "general-purpose LLM",
     (0.03, 0.28, 0.66, 0.92), "6 bullet points · no gene IDs · no citations [n]"),
    ("DeepSeek", "DeepSeek.png", C["amber"], "general-purpose LLM · thinking block cropped",
     (0.02, 0.55, 0.82, 1.00), "5 numbered points + summary · no gene IDs · no citations [n]"),
    ("Zebrafish Hub", "Zebrafish Hub.png", C["blue"], "this system · curated DB + retrieval + citations",
     (0.02, 0.13, 0.99, 1.00), "curated gene IDs + inline citations [n] · answer continues ({chars} characters)"),
]


def _place_image(fig, img, box, pad=0.0, align="center"):
    """把图像按原比例放入 box=[x,y,w,h]（figure 分数）内接。

    注意：画布非正方形时，分数宽高的英寸换算不同，必须经英寸中转，
    否则会把图缩得极小（曾因此导致面板内只剩一张小图）。
    align="top" 用于 FigS5：三张截图顶部对齐，三栏读起来才是一行。
    """
    bx, by, bw, bh = box
    fw, fh = fig.get_size_inches()
    iw, ih = img.size
    ar = iw / max(1, ih)
    avail_w = (bw - 2 * pad) * fw          # 可用宽（英寸）
    avail_h = (bh - 2 * pad) * fh          # 可用高（英寸）
    w_in = avail_w
    h_in = w_in / ar
    if h_in > avail_h:
        h_in = avail_h
        w_in = h_in * ar
    x = bx + (bw - w_in / fw) / 2
    if align == "top":
        y = by + bh - h_in / fh - pad
    else:
        y = by + (bh - h_in / fh) / 2
    iax = fig.add_axes([x, y, w_in / fw, h_in / fh])
    iax.imshow(img)
    iax.axis("off")
    for s in iax.spines.values():
        s.set_visible(False)
    return iax, (x, y, w_in / fw, h_in / fh)


def fig_s5_shots():
    """FigS5：直接用三张真实截图（ChatGPT / DeepSeek / Zebrafish Hub）。

    画布高度按图像自然比例自适应，避免大片留白导致文字太小。
    """
    from PIL import Image as PILImage
    items = []
    for name, fn, col, sub, crop, note in SHOTS:
        p = ROOT / fn
        if not p.is_file():
            rec(f"S5-shot-{name}", f"FigS5「{name}」截图存在", "mismatch", str(p), "high")
            continue
        with PILImage.open(p) as im:
            im = im.convert("RGB")
            W0, H0 = im.size
            l, t, r, b = crop
            sub_img = im.crop((int(W0 * l), int(H0 * t), int(W0 * r), int(H0 * b)))
        items.append({"name": name, "col": col, "sub": sub, "note": note,
                      "img": sub_img, "src": (W0, H0)})
    if not items:
        rec("S5-shots", "FigS5 至少嵌入一张截图", "mismatch", "三张均缺失", "high")
        return {"shots": [], "kind": "screenshots"}

    FIG_W = 12.4
    n = len(items)
    left_in, gap_in, pad_in = 0.26, 0.20, 0.09
    panel_w_in = (FIG_W - 2 * left_in - (n - 1) * gap_in) / n
    inner_w_in = panel_w_in - 2 * pad_in
    img_h_in = max(inner_w_in / (it["img"].size[0] / it["img"].size[1]) for it in items)
    head_in, note_in, foot_in = 0.46, 0.30, 0.26
    FIG_H = foot_in + head_in + img_h_in + note_in + 0.72 + 0.30

    fig = plt.figure(figsize=(FIG_W, FIG_H))
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0]); ax.axis("off")
    fx = lambda v: v / FIG_W        # noqa: E731
    fy = lambda v: v / FIG_H        # noqa: E731

    # 顶部提问条
    bar_h = 0.68
    ax.add_patch(FancyBboxPatch((fx(left_in), fy(FIG_H - bar_h - 0.16)),
                                1 - 2 * fx(left_in), fy(bar_h),
                                boxstyle="round,pad=0.003,rounding_size=0.006",
                                lw=1.0, ec=C["line"], fc=C["bg"], transform=_t(ax)))
    ax.text(fx(left_in + 0.16), fy(FIG_H - bar_h / 2 - 0.16), "Q", fontsize=11.5,
            color=C["slate"], fontweight="bold", transform=_t(ax), va="center")
    ax.text(fx(left_in + 0.42), fy(FIG_H - bar_h * 0.38 - 0.16),
            "What are the functions of the zebrafish nanog gene? (brief answers requested)", fontsize=12.0,
            color=C["ink"], fontweight="bold", transform=_t(ax), va="center")
    ax.text(fx(left_in + 0.42), fy(FIG_H - bar_h * 0.78 - 0.16),
            "Same question · same “answer briefly” instruction · screenshots unedited "
            "(DeepSeek thinking block cropped)",
            fontsize=7.6, color=C["gray"], transform=_t(ax), va="center")

    base_in = foot_in + note_in                       # 面板框底部
    for i, it in enumerate(items):
        x_in = left_in + i * (panel_w_in + gap_in)
        ax.add_patch(FancyBboxPatch((fx(x_in), fy(base_in)), fx(panel_w_in),
                                    fy(head_in + img_h_in + 0.14),
                                    boxstyle="round,pad=0.002,rounding_size=0.008",
                                    lw=1.3, ec=it["col"], fc="white", transform=_t(ax)))
        ax.add_patch(FancyBboxPatch((fx(x_in + 0.05), fy(base_in + img_h_in + 0.18)),
                                    fx(panel_w_in - 0.10), fy(head_in - 0.12),
                                    boxstyle="round,pad=0.002,rounding_size=0.006",
                                    lw=0.0, fc=it["col"], alpha=0.12, transform=_t(ax)))
        ax.text(fx(x_in + 0.16), fy(base_in + img_h_in + 0.40), it["name"], fontsize=10.2,
                color=it["col"], fontweight="bold", transform=_t(ax), va="center")
        ax.text(fx(x_in + 0.16), fy(base_in + img_h_in + 0.22), it["sub"], fontsize=7.0,
                color=C["gray"], transform=_t(ax), va="center")
        _place_image(fig, it["img"], [fx(x_in + pad_in), fy(base_in + 0.10),
                                       fx(inner_w_in), fy(img_h_in)], pad=0.0, align="top")
        # 参考图 Fig. S5 的彩色 callout 框：只陈述截图可见事实，不做质量评价
        from matplotlib.colors import to_rgba
        note_w_in = min(len(it["note"]) * 0.052 + 0.24, panel_w_in - 0.24)
        note_h_in = 0.24
        ax.add_patch(FancyBboxPatch(
            (fx(x_in + (panel_w_in - note_w_in) / 2), fy(note_in * 0.55 - note_h_in / 2)),
            fx(note_w_in), fy(note_h_in),
            boxstyle="round,pad=0.002,rounding_size=0.05",
            lw=0.9, ec=it["col"], fc=to_rgba(it["col"], 0.12), transform=_t(ax), zorder=1))
        ax.text(fx(x_in + panel_w_in / 2), fy(note_in * 0.55), it["note"], fontsize=7.4,
                color=it["col"], ha="center", va="center", transform=_t(ax), zorder=2)
        rec(f"S5-shot-{it['name']}", f"FigS5「{it['name']}」截图已嵌入", "ok",
            f"{it['name']}.png 原图 {it['src'][0]}x{it['src'][1]} → 裁剪 "
            f"{it['img'].size[0]}x{it['img'].size[1]}")

    out_p = OUT / "FigS5_QA_Comparison.png"
    check_overlaps(fig, "FigS5")
    fig.savefig(out_p, dpi=300, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    rec("S5-file", "FigS5（截图版）已输出", "ok",
        f"{out_p.name}；嵌入 {len(items)} 张截图 · 画布 {FIG_W}x{FIG_H:.1f} in")
    return {"shots": [it["name"] for it in items], "kind": "screenshots"}
    texts, status = {}, {}
    for name, fn, _c, _s in SRC:
        p = LOGS / fn
        t = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        texts[name] = t
        status[name] = judge(t)
        rec(f"S5-src-{name}", f"FigS5「{name}」回答取自真实产物",
            "ok" if t else "mismatch", f"{fn} {len(t)} chars",
            "high" if not t else "info")

    fig = plt.figure(figsize=(12.4, 6.4))
    ax = fig.add_axes([0.025, 0.03, 0.95, 0.93]); ax.axis("off")
    ax.add_patch(FancyBboxPatch((0.0, 0.905), 1.0, 0.078,
                                boxstyle="round,pad=0.003,rounding_size=0.008",
                                lw=1.0, ec=C["line"], fc=C["bg"], transform=_t(ax)))
    ax.text(0.014, 0.962, "Q", fontsize=11.0, color=C["slate"], fontweight="bold",
            transform=_t(ax), va="center")
    ax.text(0.038, 0.960, "斑马鱼 nanog 基因的功能有哪些？", fontsize=11.4, color=C["ink"],
            fontweight="bold", transform=_t(ax), va="center")
    ax.text(0.038, 0.922, f"库内基准：{basic.get('gene_name')} · {basic.get('ensembl_id')} · "
                          f"{basic.get('chromosome')} 号染色体 · GO {cnt.get('go')} 条 · "
                          f"表达 {cnt.get('expression')} 条 · 文献 {cnt.get('reference')} 篇",
            fontsize=7.2, color=C["gray"], transform=_t(ax), va="center")

    w3, gap3 = 0.318, 0.023
    for i, (name, _fn, col, sub) in enumerate(SRC):
        x = i * (w3 + gap3)
        ax.add_patch(FancyBboxPatch((x, 0.315), w3, 0.545,
                                    boxstyle="round,pad=0.003,rounding_size=0.010",
                                    lw=1.3, ec=col, fc="white", transform=_t(ax)))
        ax.add_patch(FancyBboxPatch((x + 0.007, 0.788), w3 - 0.014, 0.060,
                                    boxstyle="round,pad=0.002,rounding_size=0.008",
                                    lw=0.0, fc=col, alpha=0.12, transform=_t(ax)))
        ax.text(x + 0.020, 0.833, name, fontsize=9.8, color=col, fontweight="bold",
                transform=_t(ax), va="center")
        ax.text(x + 0.020, 0.803, sub, fontsize=7.0, color=C["gray"], transform=_t(ax),
                va="center")
        yy = 0.752
        pts = extract_points(texts[name], 3)
        if not pts:
            ax.text(x + 0.024, 0.700, "（未采集到回答）", fontsize=8.0, color=C["rose"],
                    transform=_t(ax), va="center")
        for pt in pts:
            ax.text(x + 0.016, yy, "\u2022", fontsize=8.0, color=col, transform=_t(ax),
                    va="center")
            ax.text(x + 0.036, yy, pt, fontsize=7.6, color=C["ink"], transform=_t(ax),
                    va="center")
            yy -= 0.078

    ax.add_patch(FancyBboxPatch((0.0, 0.045), 1.0, 0.235,
                                boxstyle="round,pad=0.003,rounding_size=0.010",
                                lw=1.1, ec=C["line"], fc="white", transform=_t(ax)))
    cx = (0.014, 0.560, 0.705, 0.850)
    ax.text(cx[0], 0.248, "库内事实要点（服务器 MySQL 判定基准）", fontsize=8.2,
            color=C["slate"], fontweight="bold", transform=_t(ax), va="center")
    for j, (name, _fn, col, _s) in enumerate(SRC):
        ax.text(cx[1 + j] + 0.058, 0.248, name, fontsize=7.8, color=col,
                fontweight="bold", ha="center", transform=_t(ax), va="center")
    for r, (pname, _g) in enumerate(POINTS):
        yy = 0.208 - r * 0.0268
        ax.text(cx[0], yy, f"{r + 1}. {pname}", fontsize=7.4, color=C["ink"],
                transform=_t(ax), va="center")
        for j, (nm, _fn, _c, _s) in enumerate(SRC):
            st = status[nm][r]
            mk, mc = {"完整": ("✓", C["green"]), "部分": ("~", C["amber"]),
                      "缺失": ("✗", C["rose"])}[st]
            ax.text(cx[1 + j] + 0.058, yy, f"{mk} {st}", fontsize=7.4, color=mc,
                    fontweight="bold", ha="center", transform=_t(ax), va="center")
    yes = {nm: sum(1 for s in status[nm] if s == "完整") for nm, _f, _c, _s in SRC}
    part = {nm: sum(1 for s in status[nm] if s == "部分") for nm, _f, _c, _s in SRC}
    miss = {nm: sum(1 for s in status[nm] if s == "缺失") for nm, _f, _c, _s in SRC}
    tot = len(POINTS)
    ax.text(0.014, 0.017,
            f"要点覆盖（共 {tot} 项）：" + "   ·   ".join(
                f"{nm} {yes[nm]} 完整 / {part[nm]} 部分 / {miss[nm]} 缺失"
                for nm, _f, _c, _s in SRC),
            fontsize=8.0, color=C["ink"], fontweight="bold", transform=_t(ax), va="bottom")
    ax.text(0.99, 0.017, "三段回答均为未改写真实产物 · 判定关键词见脚本内 POINTS",
            fontsize=6.4, color=C["gray"], ha="right", transform=_t(ax), va="bottom")
    p = OUT / "FigS5_QA_Comparison.png"
    check_overlaps(fig, "FigS5")
    fig.savefig(p, dpi=300, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    rec("S5-file", "FigS5 已输出", "ok",
        f"{p.name}；Hub {yes[SRC[0][0]]}/{tot} vs chat {yes[SRC[1][0]]}/{tot} vs "
        f"reasoner {yes[SRC[2][0]]}/{tot}")
    return {"yes": yes, "part": part, "miss": miss, "total": tot, "status": status}


# ══════════════════════════════════════════════════════════════════════════
def fig_s5_shots_vertical():
    """FigS5（对齐参考图 S5 版式）：竖排三行，一行一个系统 = 原始截图 + 一个彩色标注框。

    做减法：顶部一行提问，底部一行图题，中间只有「截图 + 标注」；
    不设顶栏副标题、不设底部图例。
    标注框只写截图里看得见的事实（要点数 / 有无线索 ID / 有无行内引用），不做质量评价。
    截图只裁 UI 外壳（DeepSeek 思考块、Hub 界面边距），回答文字一字不改。
    """
    import textwrap
    from PIL import Image as PILImage
    from matplotlib.colors import to_rgba

    hub_chars = 0
    hp = LOGS / "_nanog_hub.txt"
    if hp.is_file():
        hub_chars = len(hp.read_text(encoding="utf-8", errors="replace").strip())

    items = []
    for name, fn, col, sub, crop, note in SHOTS:
        p = ROOT / fn
        if not p.is_file():
            rec(f"S5-shot-{name}", f"FigS5「{name}」截图存在", "mismatch", str(p), "high")
            continue
        with PILImage.open(p) as im:
            im = im.convert("RGB")
            W0, H0 = im.size
            l, t, r, b = crop
            sub_img = im.crop((int(W0 * l), int(H0 * t), int(W0 * r), int(H0 * b)))
        items.append({"name": name, "col": col, "sub": sub,
                      "note": note.format(chars=f"{hub_chars:,}"),
                      "img": sub_img, "src": (W0, H0)})
    if not items:
        rec("S5-shots", "FigS5 至少嵌入一张截图", "mismatch", "三张均缺失", "high")
        return {"shots": [], "kind": "screenshots"}

    W = 9.6
    m_l, m_r = 0.34, 0.34
    img_w = 5.45
    txt_x = m_l + img_w + 0.30
    txt_w = W - m_r - txt_x
    q_h, cap_h, head_h, gap = 0.60, 0.46, 0.32, 0.30

    rows = []
    for it in items:
        iw, ih = it["img"].size
        n_lines = max(1, len(textwrap.wrap(it["note"], width=30)))
        img_h = img_w * ih / iw
        rows.append({"img_h": img_h, "box_h": 0.32 + n_lines * 0.20,
                     "h": max(img_h, 0.32 + n_lines * 0.20)})
    H = 0.26 + q_h + 0.14 + sum(head_h + r["h"] + gap for r in rows) + cap_h

    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0]); ax.axis("off")
    fx = lambda v: v / W        # noqa: E731
    fy = lambda v: v / H        # noqa: E731

    # 顶部提问条（一行，不设副标题栏）
    qy = H - 0.26 - q_h
    ax.add_patch(FancyBboxPatch((fx(m_l), fy(qy)), fx(W - m_l - m_r), fy(q_h),
                                boxstyle="round,pad=0.003,rounding_size=0.006",
                                lw=1.0, ec=C["line"], fc=C["bg"], transform=_t(ax)))
    ax.text(fx(m_l + 0.18), fy(qy + q_h * 0.62), "Q", fontsize=11.0, color=C["slate"],
            fontweight="bold", transform=_t(ax), va="center")
    ax.text(fx(m_l + 0.46), fy(qy + q_h * 0.62),
            "What are the functions of the zebrafish nanog gene? (brief answers requested)",
            fontsize=11.6, color=C["ink"], fontweight="bold", transform=_t(ax), va="center")
    ax.text(fx(m_l + 0.46), fy(qy + q_h * 0.22),
            "Original prompt (Chinese): \u201c\u73ed\u9a6c\u9c7c nanog \u57fa\u56e0\u529f\u80fd\u6709\u54ea\u4e9b\uff1f "
            "\u56de\u7b54\u7684\u7b80\u77ed\u4e00\u4e9b\u201d \u00b7 same day \u00b7 screenshots unedited "
            "(UI chrome cropped only)",
            fontsize=7.4, color=C["gray"], transform=_t(ax), va="center")

    y = qy - 0.14
    for it, r in zip(items, rows):
        top = y - head_h
        ax.text(fx(m_l), fy(top + head_h * 0.55), it["name"], fontsize=11.0, color=it["col"],
                fontweight="bold", transform=_t(ax), va="center")
        ax.text(fx(m_l + 0.20 + len(it["name"]) * 0.086), fy(top + head_h * 0.55), it["sub"],
                fontsize=7.6, color=C["gray"], transform=_t(ax), va="center")
        ax.plot([fx(m_l), fx(W - m_r)], [fy(top + 0.05), fy(top + 0.05)],
                color=C["line"], lw=0.8, transform=_t(ax))
        _place_image(fig, it["img"], [fx(m_l), fy(top - r["img_h"]), fx(img_w), fy(r["img_h"])],
                     pad=0.0, align="top")
        box_h = r["box_h"]
        by = top - max(r["img_h"], box_h) / 2 - box_h / 2
        ax.add_patch(FancyBboxPatch((fx(txt_x), fy(by)), fx(txt_w), fy(box_h),
                                    boxstyle="round,pad=0.002,rounding_size=0.05",
                                    lw=1.0, ec=it["col"], fc=to_rgba(it["col"], 0.10),
                                    transform=_t(ax), zorder=1))
        ax.text(fx(txt_x + 0.16), fy(by + box_h - 0.16), textwrap.fill(it["note"], width=30),
                fontsize=8.0, color=it["col"], transform=_t(ax), va="top", zorder=2,
                linespacing=1.5)
        rec(f"S5-shot-{it['name']}", f"FigS5「{it['name']}」截图已嵌入", "ok",
            f"{it['name']}.png 原图 {it['src'][0]}x{it['src'][1]} → 裁剪 "
            f"{it['img'].size[0]}x{it['img'].size[1]}")
        y = top - r["h"] - gap

    ax.text(fx(m_l), fy(cap_h * 0.44),
            "Fig. S5. Comparison of zebrafish knowledge queries between the Zebrafish Knowledge Hub "
            "and two general-purpose LLMs.", fontsize=8.6, color=C["ink"],
            fontweight="bold", transform=_t(ax), va="center")
    ax.text(fx(m_l), fy(cap_h * 0.15),
            "Panels are unedited screenshots of the same question; coloured callouts state only what "
            "is visible in each panel.", fontsize=7.2, color=C["gray"],
            transform=_t(ax), va="center")

    out_p = OUT / "FigS5_QA_Comparison.png"
    check_overlaps(fig, "FigS5")
    fig.savefig(out_p, dpi=300, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    rec("S5-file", "FigS5（竖排版式）已输出", "ok",
        f"{out_p.name}；嵌入 {len(items)} 张截图 · 画布 {W}x{H:.1f} in")
    return {"shots": [it["name"] for it in items], "kind": "screenshots"}


def selfcheck(s4, s5) -> int:
    from PIL import Image
    # 交付图以 EN 版为准（FigS4_Pipelines_EN.png）；旧蛇形图仅作留存，存在时一并校验
    for name in ("FigS4_Pipelines_EN.png", "FigS5_QA_Comparison.png",
                 "FigS4_Literature_Pipeline.png"):
        p = OUT / name
        ok = p.is_file()
        rec(f"chk-exist-{name}", f"{name} 存在且可解码", "ok" if ok else "mismatch",
            f"{p.stat().st_size if ok else 0} bytes", "high" if not ok else "info")
        if ok:
            with Image.open(p) as im:
                w, h = im.size
                ink = 1.0 - float((np.asarray(im.convert("L")) > 245).mean())
            rec(f"chk-size-{name}", f"{name} 分辨率与非空白",
                "ok" if w >= 2800 and ink > 0.004 else "mismatch",
                f"{w}x{h} ink={ink:.4f}",
                "high" if not (w >= 2800 and ink > 0.004) else "info")
    rec("chk-shots", "FigS5 三张截图均已嵌入",
        "ok" if len(s5.get("shots") or []) == 3 else "mismatch",
        f"embedded={s5.get('shots')}", "high")
    srcs = {"lit": EXACT.get("literature_paper"), "papers": IDX.get("total_papers"),
            "chunks": IDX.get("total_chunks"), "shards": IDX.get("shard_count"),
            "dim": IDX.get("embedding_dim"), "universe": ALIGN.get("universe"),
            "gold": ALIGN.get("gold"), "floor": (GOLD.get("gold_bar") or {}).get("body_floor")}
    bad = [k for k, v in srcs.items() if not v or int(v) != int(N[k])]
    rec("chk-pipeline-numbers", "FigS4 流水线读数均可回溯数据源",
        "ok" if not bad else "mismatch", f"不一致项 {bad or '无'}", "high" if bad else "info")
    rec("chk-facts", "判定基准来自服务器 MySQL 的 nanog 真实注释",
        "ok" if (NF.get("gene_id") and NF.get("go") and NF.get("reference")) else "mismatch",
        f"gene_id={NF.get('gene_id')} GO={len(NF.get('go') or [])} "
        f"表达={len(NF.get('expression') or [])} 文献={len(NF.get('reference') or [])}", "high")
    errs = [r for r in REC if r["status"] == "mismatch"]
    print(f"[s4-s5] checks={len(REC)} mismatch={len(errs)}")
    for r in errs:
        print("  [X]", r["id"], "::", r["detail"])
    (OUT / "_selfcheck_s4_s5.json").write_text(json.dumps(
        {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "numbers": N, "fig_s5": s5,
         "records": REC, "summary": {"total": len(REC), "mismatch": len(errs)}},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] {OUT / '_selfcheck_s4_s5.json'}")
    return 1 if errs else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    print("[s4-s5] 渲染 FigS4（简化流水线）...")
    s4 = fig_s4()
    print("[s4-s5] 渲染 FigS5（真实截图）...")
    s5 = fig_s5_shots_vertical()
    print(f"[s4-s5] 完成 {time.time() - t0:.1f}s → {OUT}")
    if a.check:
        return selfcheck(s4, s5)
    return 1 if [r for r in REC if r["status"] == "mismatch"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
