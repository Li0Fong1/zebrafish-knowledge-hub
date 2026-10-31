# -*- coding: utf-8 -*-
"""斑马鱼知识中枢 · 图集统一视觉规范（学术出版级）。

与参考图集（灰黑白单色系）刻意区分：采用「青绿-靛蓝」双主色学术配色，
面板布局按内容自适应（网格 / 时序 / 漏斗 / 双栏），编号采用连续 Fig. 1-8。
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# ── 调色板 ────────────────────────────────────────────────────────────────
C = {
    "teal":   "#0E7490",   # 主色 深青
    "cyan":   "#06B6D4",   # 亮青
    "indigo": "#4F46E5",   # 靛蓝
    "violet": "#7C3AED",   # 紫
    "amber":  "#F59E0B",   # 琥珀（强调）
    "rose":   "#E11D48",   # 玫红（警示/错误）
    "emerald": "#059669",  # 翠绿（达标）
    "slate":  "#334155",   # 深板岩（正文）
    "gray":   "#94A3B8",   # 中性灰
    "light":  "#E2E8F0",   # 浅底
    "paper":  "#FFFFFF",
    "ink":    "#0F172A",
}
SEQ = [C["teal"], C["indigo"], C["amber"], C["violet"], C["cyan"],
       C["emerald"], C["rose"], C["gray"]]

LAYOUT = {"figsize": (11.0, 6.2), "dpi": 300}

try:                                     # 输出分辨率取自项目配置（.env: FIG_DPI）
    from fig_config import FIG_DPI as _FIG_DPI
    LAYOUT["dpi"] = int(_FIG_DPI)
except Exception:                        # 独立使用时回退内置默认
    pass


def use_style() -> None:
    """全局 rcParams：白底、无边框、雅黑中文 + Arial 数字。"""
    for fam in ("Microsoft YaHei", "SimHei"):
        try:
            font_manager.findfont(fam, fallback_to_default=False)
            break
        except Exception:
            continue
    plt.rcParams.update({
        "font.family": ["Microsoft YaHei", "DejaVu Sans"],
        "axes.unicode_minus": False,
        "figure.facecolor": C["paper"],
        "axes.facecolor": C["paper"],
        "savefig.facecolor": C["paper"],
        "axes.edgecolor": C["gray"],
        "axes.linewidth": 0.9,
        "axes.labelcolor": C["slate"],
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.titlecolor": C["ink"],
        "axes.labelsize": 9.5,
        "xtick.color": C["slate"],
        "ytick.color": C["slate"],
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "legend.fontsize": 8.5,
        "legend.frameon": False,
        "grid.color": C["light"],
        "grid.linewidth": 0.8,
        "lines.linewidth": 1.9,
        "figure.dpi": 110,
        "savefig.dpi": LAYOUT["dpi"],
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.18,
    })


def panel(ax, letter: str, title: str = "", dx: float = -0.075, dy: float = 1.045):
    """统一面板标签：(A) / (B) ... 加粗，后接面板标题。"""
    txt = f"({letter})" + (f"  {title}" if title else "")
    ax.text(dx, dy, txt, transform=ax.transAxes, ha="left", va="bottom",
            fontsize=11, fontweight="bold", color=C["ink"], clip_on=False)


def strip(ax, left=True, bottom=True):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_visible(left)
    ax.spines["bottom"].set_visible(bottom)


def box(ax, x, y, w, h, text, fc="#F8FAFC", ec="#0E7490", fs=8.6, lw=1.3,
        tc=None, weight="normal", rounding=0.02, va="center"):
    """流程图节点：圆角矩形 + 居中文字。"""
    from matplotlib.patches import FancyBboxPatch
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle=f"round,pad=0.004,rounding_size={rounding}",
                       linewidth=lw, edgecolor=ec, facecolor=fc, zorder=2)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va=va, fontsize=fs,
            color=tc or C["ink"], fontweight=weight, zorder=3, linespacing=1.35)
    return p


def arrow(ax, xy_from, xy_to, color=None, lw=1.5, style="-|>", rad=0.0, ls="-"):
    """流程箭头。"""
    from matplotlib.patches import FancyArrowPatch
    a = FancyArrowPatch(xy_from, xy_to, arrowstyle=style, mutation_scale=11,
                        linewidth=lw, color=color or C["teal"], zorder=1,
                        connectionstyle=f"arc3,rad={rad}", linestyle=ls,
                        shrinkA=1.5, shrinkB=1.5)
    ax.add_patch(a)
    return a


def blank(ax):
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")


def title(fig, text: str, sub: str = ""):
    fig.suptitle(text, fontsize=13.5, fontweight="bold", color=C["ink"],
                 x=0.012, ha="left", y=0.988)
    if sub:
        fig.text(0.012, 0.930, sub, fontsize=9.2, color=C["gray"], ha="left",
                 va="top")


def note(fig, text: str):
    fig.text(0.012, 0.012, text, fontsize=7.8, color=C["gray"], ha="left", va="bottom")


def hbar(ax, labels, values, colors=None, fmt="{:,.0f}", logx=True, xlabel=""):
    import numpy as np
    y = np.arange(len(labels))[::-1]
    colors = colors or [C["teal"]] * len(labels)
    bars = ax.barh(y, values, color=colors, height=0.62, zorder=3)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    if logx:
        ax.set_xscale("log")
    ax.set_xlabel(xlabel)
    ax.xaxis.grid(True, zorder=0)
    ax.set_axisbelow(True)
    strip(ax)
    mx = max(values) if values else 1
    for b, v in zip(bars, values):
        ax.text(v * (1.12 if logx else 1.012), b.get_y() + b.get_height() / 2,
                fmt.format(v), va="center", fontsize=8.2, color=C["slate"])
    if logx:
        ax.set_xlim(right=mx * 3.2)
    return bars
