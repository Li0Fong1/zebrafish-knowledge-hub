# -*- coding: utf-8 -*-
"""FigS4 (EN): 两张英文流程图，版式对齐参考图 Fig. S4 的 (A)/(B)。

    (A) Literature cleaning and vectorization
    (B) Query and answer generation

命名原则（按用户要求）：**用直白、看得懂的名字**，不用抽象术语
    · 不用 "Artefact scrubbing" / "Noise truncation" 之类
    · 「金标解析」一律写成 literature parsing
读数取自 _clean_stats.json / _audit_sources.json / _data.json

★ 教训（必须保留的两道防线）★
    图标在**单位空间**绘制，必须由 icon_at() 注入等比缩放变换。
    若在 icon_at() 之外直接调用图标函数，图标会按单位尺寸（0.66×0.60 整幅坐标）
    画满全图，把其它节点的文字白底盖掉 —— 曾导致 B 面板只剩 "User input" 和
    残缺的 "R"/"D" 两个字母。现在：
      1) _it() 缺少变换时直接抛错；
      2) _sanity_artists() 扫描是否有超过画布 30% 宽的图形；
      3) _raster_ink_check() 在 PNG 上逐条验证标签/子说明真的有墨迹。

输出：Figure/supplementary/FigS4_Pipelines_EN.png
用法：python scripts\\make_figS4_pipelines_en.py
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                      # noqa: E402
import numpy as np                                   # noqa: E402
matplotlib.rcParams["font.sans-serif"] = ["DejaVu Sans", "Microsoft YaHei"]
matplotlib.rcParams["axes.unicode_minus"] = False

from matplotlib.backends.backend_agg import FigureCanvasAgg                    # noqa: E402
from matplotlib.patches import Circle, Ellipse, FancyBboxPatch, Polygon, Rectangle  # noqa: E402
from matplotlib.transforms import Affine2D           # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                              # noqa: E402
from make_figures_pipeline_compare import (          # noqa: E402
    C, N, CLEAN, arrow, check_overlaps, _t, rec, REC,
)

OUT = FC.FIG_DIR / "supplementary"
OUT.mkdir(parents=True, exist_ok=True)

# ── 真实读数（清洗统计 + 服务器 .env 快照，缺失时回退到已核实值）──────────
RAW = (CLEAN.get("raw_chars") or {}).get("mean") or 0
CLN = (CLEAN.get("clean_chars") or {}).get("mean") or 0
_AUD = json.loads((FC.FIG_DIR / "_audit_sources.json").read_text(encoding="utf-8"))
_ENV = (_AUD.get("server") or {}).get("env") or {}
CAND = int(_ENV.get("PUBMED_YES_RETRIEVE_CANDIDATES") or 500)
TOPN = int(_ENV.get("PUBMED_YES_RERANK_TOP_N") or 100)
PACK = int(_ENV.get("PUBMED_YES_PACK_CHARS") or 48000)

# ══════════════════════════════════════════════════════════════════════════
# 图标：单位空间（中心 (0,0)，纵向 ±0.5，横向等比）——由 icon_at() 缩放
# ══════════════════════════════════════════════════════════════════════════
_ICON_CTX: dict = {"tr": None}
_TEXT_BOXES: list = []          # 供栅格自检：(说明, Text 对象)


def _it(ax):
    tr = _ICON_CTX.get("tr")
    if tr is None:
        raise RuntimeError("图标必须在 icon_at() 内部绘制：缺少缩放变换会画满全图")
    return tr


def icon_at(ax, x, y, size, col, icon):
    """在 (x, y) [axes 分数] 画一个高度为 size [axes 分数] 的等比图标。"""
    fw, fh = ax.figure.get_size_inches()
    _ICON_CTX["tr"] = (Affine2D().scale(size * fh / fw, size).translate(x, y)
                       + ax.transAxes)
    try:
        icon(ax, 0.0, 0.0, col)
    finally:
        _ICON_CTX["tr"] = None


def ic_doc(ax, x, y, c):
    ax.add_patch(Rectangle((x - 0.30, y - 0.46), 0.60, 0.92, fc="white", ec=c,
                           lw=1.5, transform=_it(ax), zorder=3))
    for dy in (0.26, 0.08, -0.10, -0.28):
        ax.plot([x - 0.19, x + 0.19], [y + dy, y + dy], color=c, lw=1.1,
                transform=_it(ax), zorder=4)


def ic_gear(ax, x, y, c):
    ax.add_patch(Circle((x, y), 0.30, fc="white", ec=c, lw=1.5,
                        transform=_it(ax), zorder=3))
    for k in range(8):
        a = math.radians(k * 45)
        ax.add_patch(Rectangle((x + 0.41 * math.cos(a) - 0.085,
                                y + 0.41 * math.sin(a) - 0.085),
                               0.17, 0.17, fc=c, ec="none", transform=_it(ax), zorder=4))
    ax.add_patch(Circle((x, y), 0.12, fc=c, ec="none", transform=_it(ax), zorder=4))


def ic_cube(ax, x, y, c):
    s = 0.46
    top = [[x, y + 0.47], [x + s, y + 0.21], [x, y - 0.05], [x - s, y + 0.21]]
    left = [[x - s, y + 0.21], [x, y - 0.05], [x, y - 0.51], [x - s, y - 0.25]]
    right = [[x + s, y + 0.21], [x, y - 0.05], [x, y - 0.51], [x + s, y - 0.25]]
    ax.add_patch(Polygon(top, closed=True, fc=c, alpha=0.22, ec=c, lw=1.2,
                         transform=_it(ax), zorder=4))
    ax.add_patch(Polygon(left, closed=True, fc=c, alpha=0.45, ec=c, lw=1.2,
                         transform=_it(ax), zorder=4))
    ax.add_patch(Polygon(right, closed=True, fc=c, alpha=0.70, ec=c, lw=1.2,
                         transform=_it(ax), zorder=4))


def ic_db(ax, x, y, c):
    ax.add_patch(Rectangle((x - 0.33, y - 0.30), 0.66, 0.60, fc="white", ec=c,
                           lw=1.4, transform=_it(ax), zorder=3))
    for dy in (0.30, 0.00, -0.30):
        ax.add_patch(Ellipse((x, y + dy), 0.66, 0.30, fc="none", ec=c, lw=1.3,
                             transform=_it(ax), zorder=4))


def ic_stack(ax, x, y, c):
    """三层堆叠（FAISS 分片存储）。"""
    for i, dy in enumerate((-0.30, 0.00, 0.30)):
        ax.add_patch(Rectangle((x - 0.46, y + dy - 0.13), 0.92, 0.26, fc="white", ec=c,
                               lw=1.3, transform=_it(ax), zorder=3 + i))


def ic_user(ax, x, y, c):
    ax.add_patch(Circle((x, y + 0.20), 0.25, fc="none", ec=c, lw=1.5,
                        transform=_it(ax), zorder=3))
    ax.add_patch(Polygon([[x - 0.44, y - 0.47], [x - 0.24, y - 0.06],
                          [x + 0.24, y - 0.06], [x + 0.44, y - 0.47]],
                         closed=True, fc="none", ec=c, lw=1.5, transform=_it(ax), zorder=3))


def ic_flow(ax, x, y, c):
    """改写（上下两条反向箭头）。"""
    for dy, sgn in ((0.17, 1), (-0.17, -1)):
        ax.plot([x - 0.42 * sgn, x + 0.28 * sgn], [y + dy, y + dy], color=c, lw=1.5,
                transform=_it(ax), zorder=4)
        ax.add_patch(Polygon([[x + 0.44 * sgn, y + dy], [x + 0.24 * sgn, y + dy + 0.13],
                              [x + 0.24 * sgn, y + dy - 0.13]],
                             closed=True, fc=c, ec="none", transform=_it(ax), zorder=4))


def ic_bubble(ax, x, y, c):
    ax.add_patch(FancyBboxPatch((x - 0.42, y - 0.16), 0.84, 0.62,
                                boxstyle="round,pad=0.002,rounding_size=0.16",
                                lw=1.5, ec=c, fc="white", transform=_it(ax), zorder=3))
    ax.add_patch(Polygon([[x - 0.14, y - 0.17], [x + 0.06, y - 0.17], [x - 0.14, y - 0.47]],
                         closed=True, fc="white", ec=c, lw=1.5, transform=_it(ax), zorder=3))
    for dy in (0.28, 0.06):
        ax.plot([x - 0.26, x + 0.24], [y + dy, y + dy], color=c, lw=1.2,
                transform=_it(ax), zorder=4)


# ══════════════════════════════════════════════════════════════════════════
# 节点 / 行
# ══════════════════════════════════════════════════════════════════════════
def block(ax, x, y, w, h, label, sub, col, icon, fs_label=9.6, fs_sub=7.6):
    """一个节点：彩色圆角框 + 图标 + 粗体标签 + 灰色子说明。"""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.012",
                                lw=1.3, ec=col, facecolor="white", transform=_t(ax), zorder=1))
    icon_at(ax, x + w / 2, y + h * 0.655, h * 0.56, col, icon)
    t1 = ax.text(x + w / 2, y + h * 0.20, label, ha="center", va="center",
                 fontsize=fs_label, color=C["ink"], fontweight="bold",
                 transform=_t(ax), zorder=5)
    _TEXT_BOXES.append((f"label:{label}", t1))
    if sub:
        t2 = ax.text(x + w / 2, y - 0.018, sub, ha="center", va="top", fontsize=fs_sub,
                     color=C["gray"], transform=_t(ax), zorder=5, linespacing=1.35)
        _TEXT_BOXES.append((f"sub:{sub[:26]}", t2))


def row(ax, steps, y, h, w, gap, x0):
    """横向排布一行节点并连箭头，返回各节点中心 x。"""
    centers = []
    for i, (label, sub, col, icon) in enumerate(steps):
        x = x0 + i * (w + gap)
        block(ax, x, y, w, h, label, sub, col, icon)
        centers.append(x + w / 2)
        if i:
            px = x0 + (i - 1) * (w + gap) + w
            arrow(ax, (px + 0.002, y + h / 2), (x - 0.002, y + h / 2), lw=1.5)
    return centers


# ══════════════════════════════════════════════════════════════════════════
# 自检：图形尺寸哨兵 + PNG 栅格墨迹
# ══════════════════════════════════════════════════════════════════════════
def _render(fig):
    fig.set_dpi(300)
    cv = FigureCanvasAgg(fig)
    cv.draw()
    return cv, cv.get_renderer()


def _sanity_artists(fig, r):
    """任何单个图形对象都不该超过画布 30% 宽（防图标缩放丢失）。"""
    ax = fig.axes[0]
    pw, ph = fig.get_size_inches() * fig.dpi
    bad = []
    for a in list(ax.patches) + list(ax.lines):
        try:
            b = a.get_window_extent(renderer=r)
        except Exception:
            continue
        if b.width > 0.30 * pw or b.height > 0.60 * ph:
            bad.append(f"{type(a).__name__} {b.width:.0f}x{b.height:.0f}px")
    return bad


def _raster_ink_check(fig, r, path):
    """在 PNG 上逐条验证：每个标签/子说明的包围盒里真的有墨迹。"""
    tb = fig.get_tightbbox(r)                        # 英寸
    pad = 0.1                                        # savefig 默认 pad_inches
    dpi = fig.dpi
    PW = round((tb.width + 2 * pad) * dpi)
    PH = round((tb.height + 2 * pad) * dpi)
    img = plt.imread(path)
    # savefig 的 tight bbox 与 get_tightbbox 可能差 ±1px：用实际尺寸做比例映射
    sx = img.shape[1] / max(1, PW)
    sy = img.shape[0] / max(1, PH)
    PW, PH = img.shape[1], img.shape[0]
    miss = []
    for name, t in _TEXT_BOXES:
        if not t.get_visible() or not (t.get_text() or "").strip():
            miss.append(name + " (不可见)")
            continue
        b = t.get_window_extent(renderer=r)
        x0 = (b.x0 / dpi - tb.x0 + pad) * dpi * sx
        x1 = (b.x1 / dpi - tb.x0 + pad) * dpi * sx
        y0 = PH - (b.y1 / dpi - tb.y0 + pad) * dpi * sy
        y1 = PH - (b.y0 / dpi - tb.y0 + pad) * dpi * sy
        xs, xe = max(0, int(x0)), min(PW, int(np.ceil(x1)))
        ys, ye = max(0, int(y0)), min(PH, int(np.ceil(y1)))
        patch = img[ys:ye, xs:xe, :3]
        if patch.size == 0:
            miss.append(name + " (越界)")
            continue
        ink = float((patch.max(axis=2) < 0.65).mean())
        if ink < 0.02:
            miss.append(f"{name} 墨迹={ink:.3f}")
    return miss


# ══════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="只校验不出图（渲染到临时文件跑完三道防线后删除，不覆盖交付 PNG）")
    args = ap.parse_args()
    t0 = time.time()
    # 减负（2026-10-08）：无脚注段落 → 高度降为 **3.9 英寸**（注为两行，预留 0.30）
    W_IN, H_IN = 13.4, 3.9
    fig = plt.figure(figsize=(W_IN, H_IN))
    ax = fig.add_axes([0.0, 0.0, 1.0, 1.0])
    ax.axis("off")
    fx = lambda v: v / W_IN                          # noqa: E731  英寸 -> 分数
    fy = lambda v: v / H_IN                          # noqa: E731

    # ── (A) Literature cleaning and vectorization ────────────────────────
    # 面板标识对齐参考图：只留粗体字母 A/B（面板标题交给图注 Figure/Legend.md）
    ax.text(fx(0.14), fy(3.78), "A",
            fontsize=15.5, color=C["ink"], fontweight="bold", transform=_t(ax), va="top")
    steps_a = [
        ("Literature texts", "(PDF / XML full text)", C["slate"], ic_doc),
        ("Paragraph cleaning", f"(remove refs / acknowledgements;\n{N['refs']:.1f}% / {N['ack']:.1f}% of papers)", C["cyan"], ic_gear),
        ("Clean texts", f"(split {N['pmax']} / {N['pmin']} chars, overlap {N['ovl']};\n{CLN:,.0f} of {RAW:,.0f} characters)", C["teal"], ic_doc),
        ("Vectorize texts", f"({N['model']} · {N['dim']:,}-d)", C["violet"], ic_cube),
        ("Store in FAISS", f"({N['shards']} shards)", C["amber"], ic_stack),
        ("Knowledge database", f"({N['papers']:,} papers · {N['chunks']:,} chunks)",
         C["green"], ic_db),
    ]
    wa = (13.12 - 5 * 0.16) / 6                      # 2.053 in
    row(ax, steps_a, fy(2.37), fy(1.15), fx(wa), fx(0.16), fx(0.14))
    # 格式对齐参考图 Fig. S4：读数写在各自节点的圆括号注里，不再另起脚注段落。
    # 切分参数（360/110/48）与清洗触发率（34.1%/47.8%）已并入对应节点注；
    # 完整句子版保留在 Figure/Legend.md 与 Figure/supplementary/README.md。

    # ── (B) Query and answer generation ──────────────────────────────────
    ax.text(fx(0.14), fy(1.83), "B",
            fontsize=15.5, color=C["ink"], fontweight="bold", transform=_t(ax), va="top")
    steps_b = [
        ("User input", "(multi-turn session)", C["slate"], ic_user),
        ("Rewrite query", "(DeepSeek · context-aware)", C["violet"], ic_flow),
        ("Vectorize query", f"(same {N['dim']:,}-d model)", C["violet"], ic_cube),
        ("Retrieve texts", f"(top {CAND} candidates;\nreads the index built in A)", C["blue"], ic_db),
        ("Rerank texts", f"(cross-encoder · top {TOPN})", C["cyan"], ic_gear),
        ("Summarize", f"(Qwen3 · {PACK:,}-char input)", C["teal"], ic_stack),
        ("Generate answer", "(cited, grounded answer)", C["green"], ic_bubble),
    ]
    wb = (13.12 - 6 * 0.14) / 7                      # 1.754 in
    row(ax, steps_b, fy(0.42), fy(1.15), fx(wb), fx(0.14), fx(0.14))
    # (B) 不再另起脚注：“与 (A) 索引的关系”已写进 Retrieve texts 的节点注；
    # “带行内引用”已写进 Generate answer 的节点注。

    # ── 校验与输出 ───────────────────────────────────────────────────────
    cv, r = _render(fig)
    check_overlaps(fig, "FigS4-EN")
    big = _sanity_artists(fig, r)
    rec("s4en-artist-size", "无异常放大的图形对象（图标缩放已生效）",
        "ok" if not big else "mismatch",
        "max<=30% canvas width" if not big else "; ".join(big[:3]), "high")
    p = OUT / "FigS4_Pipelines_EN.png"
    if args.check:
        tmp = OUT / "._figS4EN_check.png"          # 校验用临时文件，不污染交付图
        fig.savefig(tmp, dpi=300, facecolor="white", bbox_inches="tight")
        miss = _raster_ink_check(fig, r, tmp)
        try:
            tmp.unlink()
        except OSError:
            pass
    else:
        fig.savefig(p, dpi=300, facecolor="white", bbox_inches="tight")
        miss = _raster_ink_check(fig, r, p)
    rec("s4en-ink", "每个标签/子说明在 PNG 上都真实可见（栅格墨迹）",
        "ok" if not miss else "mismatch",
        f"texts={len(_TEXT_BOXES)} missing={len(miss)}"
        + (" | " + "; ".join(miss[:4]) if miss else ""), "high")
    plt.close(fig)

    errs = [x for x in REC if x["status"] == "mismatch"]
    print(f"[figS4-EN] {time.time() - t0:.1f}s -> "
          + ("(check only，未出图)" if args.check else str(p)))
    print(f"[figS4-EN] checks={len(REC)} mismatch={len(errs)}")
    for x in errs:
        print("  [X]", x["id"], "::", x["detail"])
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
