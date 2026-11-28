# -*- coding: utf-8 -*-
"""重排 FigS2（Gene Nomenclature ID 对照表）的版式，数据一字不改。

背景
----
原图由 matplotlib 画成 561.6 x 356.4 pt 的手绘表格，正文仅 4.6 px、
无网格无斑马纹、区块/子分组/数据行三级标签在视觉上几乎同级、
表头浮在顶部横线之上、脚注挤在灰底小字块里，底部还空了约 49 pt。
本脚本只重排格式：从**原 SVG 解析出全部文本**，按期刊补充表的版式重绘，
并在保存后校验「原图每条文本都仍出现在新图里」，防止改格式时丢 ID。

设计取向（避免花哨 / AI 味）：实色块 + 细线 + 等宽字体，无渐变、无阴影、
无圆角；配色沿用项目主题浅蓝与白色，正文改为高对比深蓝灰。

用法（务必用带 matplotlib 的解释器）：
    .venv/Scripts/python.exe scripts/redraw_figs2_table.py
    .venv/Scripts/python.exe scripts/redraw_figs2_table.py --dry-run   # 只解析+校验布局
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "Figure" / "EN_journal_figures" / "FigS2_Gene_Nomenclature.svg"
OUT_DIRS = [
    ROOT / "Figure" / "EN_journal_figures",
    ROOT / "论文学习",
]
NS = "{http://www.w3.org/2000/svg}"
STAMP = datetime.now().strftime("%Y%m%d")

# ---- 配色（浅蓝 / 白 / 深蓝灰，高对比，无渐变） ----
INK = "#0b3d57"          # 主文字
ACCENT = "#106a94"       # 主题蓝
HEADER_BG = "#0b5b83"    # 表头底
SECTION_BG = "#dceef8"   # 区块条
GROUP_BG = "#f0f7fb"     # 子分组条
ZEBRA = "#f8fcfe"        # 斑马纹
GRID = "#dbe8f0"         # 细线
COL_LINE = "#e8f1f7"     # 列分隔
FOOT = "#3b5a6b"         # 脚注
WHITE = "#ffffff"

SECTION_TITLES = (
    "Cross-database identifiers",
    "Chromosome accession per assembly",
    "Primary RefSeq transcript",
)
GROUP_MARK = "assemblies ("


# --------------------------------------------------------------------------- 解析

def source_svg() -> Path:
    """始终从首次备份的原版 SVG 解析。

    输出路径与源路径同名，第一次运行后 SRC 就变成新图了；若继续拿 SRC 当输入，
    会把新加的 'Assembly / ID' 当成一行数据、坐标系也不再是 pt，解析必然失败。
    """
    cands = sorted(
        (ROOT / "Figure" / "EN_journal_figures").glob(
            "_v1_before_*/FigS2_Gene_Nomenclature.svg"
        )
    )
    return cands[0] if cands else SRC


def parse_svg(path: Path):
    """从原 SVG 还原表头 / 三级行结构 / 脚注。"""
    root = ET.parse(path).getroot()
    items = []
    for el in root.iter(NS + "text"):
        s = "".join(el.itertext()).strip()
        if not s:
            continue
        items.append((float(el.get("y", "0")), float(el.get("x", "0")), s))

    # 表头 6 个基因名的 y 带微小浮点差异，必须严格按 x 排序，
    # 否则表头会与按 x 排好的数据列错位（曾出现过 fgf8a 跑到第一列）。
    header_cells = [(x, s) for y, x, s in items if y < 10 and x > 100]
    genes = [s for _x, s in sorted(header_cells)]
    footnotes = [s for y, x, s in sorted(items) if y >= 280]
    body = [(y, x, s) for y, x, s in items if 10 <= y < 280]

    # y 相差 < 0.35 pt 视为同一行（原图个别标签与数值差 0.1 pt）
    lines = []
    for y, x, s in sorted(body):
        if lines and abs(lines[-1][0] - y) < 0.35:
            lines[-1][1].append((x, s))
        else:
            lines.append((y, [(x, s)]))

    rows = []
    for _y, cells in lines:
        cells.sort()
        label = next((s for x, s in cells if x < 100), "")
        vals = [s for x, s in cells if x >= 100]
        if label.startswith(SECTION_TITLES):
            rows.append({"type": "section", "text": label})
        elif GROUP_MARK in label and not vals:
            rows.append({"type": "group", "text": label})
        elif vals:
            rows.append({"type": "data", "label": label, "vals": vals})
        else:
            raise SystemExit(f"无法归类的行：{label!r}")

    assert len(genes) == 6, genes
    assert all(len(r["vals"]) == 6 for r in rows if r["type"] == "data")
    return genes, rows, footnotes


def svg_texts(path: Path) -> Counter:
    root = ET.parse(path).getroot()
    return Counter(
        "".join(el.itertext()).strip()
        for el in root.iter(NS + "text")
        if "".join(el.itertext()).strip()
    )


def new_svg_layout(path: Path) -> tuple[list[str], list[tuple[str, ...]]]:
    """从新 SVG 按坐标还原「表头顺序 + 每个数据行的 6 列值」。"""
    root = ET.parse(path).getroot()
    items = []
    for el in root.iter(NS + "text"):
        s = "".join(el.itertext()).strip()
        if s:
            items.append((float(el.get("y", "0")), float(el.get("x", "0")), s))
    lines: list[tuple[float, list[tuple[float, str]]]] = []
    for y, x, s in sorted(items):
        if lines and abs(lines[-1][0] - y) < 1.0:
            lines[-1][1].append((x, s))
        else:
            lines.append((y, [(x, s)]))
    header: list[str] = []
    data: list[tuple[str, ...]] = []
    for _y, cells in lines:
        if len(cells) != 7:
            continue
        ordered = [s for _x, s in sorted(cells)]
        if ordered[0] == "Assembly / ID":
            header = ordered[1:]
        else:
            data.append(tuple(ordered))
    return header, data


def verify_layout(old_svg: Path, new_svg: Path, genes, rows) -> bool:
    """文本集合 + 位置双重校验：既要求一字不丢，也要求每个 ID 仍在原列。"""
    old, new = svg_texts(old_svg), svg_texts(new_svg)
    missing = old - new
    extra = new - old
    if missing:
        print("校验失败，以下原文丢失：", dict(missing))
        return False
    print(f"文本校验通过：原图 {sum(old.values())} 条全部保留；"
          f"新增结构标签 {sum(extra.values())} 条 -> {dict(extra)}")

    header, got = new_svg_layout(new_svg)
    if header != genes:
        print(f"表头顺序错位：期望 {genes}，实际 {header}")
        return False
    want = [tuple([r["label"]] + r["vals"])
            for r in rows if r["type"] == "data"]
    if got != want:
        for i, (a, b) in enumerate(zip(want, got)):
            if a != b:
                print(f"第 {i + 1} 个数据行错位：\n  原 {a}\n  新 {b}")
                break
        print(f"行数不一致：原 {len(want)} / 新 {len(got)}")
        return False
    print(f"位置校验通过：表头 {len(header)} 列顺序正确，"
          f"{len(want)} 个数据行 × 6 列逐格一致（共 {len(want) * 6} 个 ID）")
    return True


# --------------------------------------------------------------------------- 绘制

def draw(genes, rows, footnotes, out_png: Path, out_svg: Path) -> None:
    plt.rcParams["svg.fonttype"] = "none"   # 保留可编辑文字，便于校验与后续修改
    plt.rcParams["font.family"] = "Arial"
    plt.rcParams["axes.unicode_minus"] = False

    fig = plt.figure(figsize=(15.5, 11.0), dpi=200, facecolor=WHITE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    LEFT, RIGHT = 0.018, 0.986
    LABEL_FRAC = 0.305
    X0 = LEFT + (RIGHT - LEFT) * LABEL_FRAC
    COL_W = (RIGHT - X0) / 6.0

    H_HEADER, H_SECTION, H_GROUP, H_ROW = 0.036, 0.034, 0.024, 0.030
    y = 0.976
    data_idx = 0
    data_bands: list[tuple[float, float]] = []   # 仅数据行画列线，避免穿透区块标题

    def rect(x, yy, w, h, color, z=0):
        ax.add_patch(Rectangle((x, yy - h), w, h, facecolor=color,
                               edgecolor="none", zorder=z))

    def line(x1, x2, yy, color=GRID, lw=0.5, z=1):
        ax.plot([x1, x2], [yy, yy], color=color, lw=lw,
                solid_capstyle="butt", zorder=z)

    # 表头：深蓝实色条，基因名与下方 6 列严格同起点
    rect(LEFT, y, RIGHT - LEFT, H_HEADER, HEADER_BG, z=0)
    ax.text(LEFT + 0.008, y - H_HEADER / 2, "Assembly / ID",
            fontsize=9.6, color=WHITE, weight="bold", va="center", ha="left")
    for i, g in enumerate(genes):
        ax.text(X0 + i * COL_W + 0.006, y - H_HEADER / 2, g,
                fontsize=12.0, color=WHITE, weight="bold", va="center", ha="left")
    ax.plot([X0, X0], [y - H_HEADER, y], color="#7fb2cf", lw=0.8, zorder=2)
    y -= H_HEADER

    for row in rows:
        kind = row["type"]
        if kind == "section":
            rect(LEFT, y, RIGHT - LEFT, H_SECTION, SECTION_BG, z=0)
            rect(LEFT, y, 0.0042, H_SECTION, ACCENT, z=1)   # 左侧竖条标识层级
            ax.text(LEFT + 0.011, y - H_SECTION / 2, row["text"],
                    fontsize=10.8, color=INK, weight="bold", va="center")
            line(LEFT, RIGHT, y - H_SECTION, GRID, 0.7)
            y -= H_SECTION
        elif kind == "group":
            rect(LEFT, y, RIGHT - LEFT, H_GROUP, GROUP_BG, z=0)
            ax.text(LEFT + 0.011, y - H_GROUP / 2, row["text"],
                    fontsize=8.8, color=FOOT, style="italic", va="center")
            line(LEFT, RIGHT, y - H_GROUP, GRID, 0.4)
            y -= H_GROUP
        else:
            if data_idx % 2 == 1:
                rect(LEFT, y, RIGHT - LEFT, H_ROW, ZEBRA, z=0)
            # 第一列标签区淡底，强化「这是行名」
            rect(LEFT, y, X0 - LEFT, H_ROW, "#eef7fc" if data_idx % 2 == 0 else "#eaf4fa", z=0)
            ax.text(LEFT + 0.008, y - H_ROW / 2, row["label"],
                    fontsize=8.9, color=INK, weight="bold", va="center")
            for i, v in enumerate(row["vals"]):
                ax.text(X0 + i * COL_W + 0.006, y - H_ROW / 2, v,
                        fontsize=9.0, color=INK, va="center",
                        family="DejaVu Sans Mono")
            line(LEFT, RIGHT, y - H_ROW, GRID, 0.35)
            y -= H_ROW
            data_bands.append((y, y + H_ROW))
            data_idx += 1

    # 列分隔细线只画在数据行区间，长 ID 串不再串行，也不会穿透区块标题条
    for lo, hi in data_bands:
        for i in range(1, 6):
            xx = X0 + i * COL_W
            ax.plot([xx, xx], [lo, hi], color=COL_LINE, lw=0.45, zorder=1)
        ax.plot([X0, X0], [lo, hi], color=GRID, lw=0.8, zorder=1)
        ax.plot([RIGHT, RIGHT], [lo, hi], color=GRID, lw=0.6, zorder=1)

    # 脚注：去掉灰底块，改为清晰的分行小字，逐字保留原文
    fy = y - 0.020
    line(LEFT, RIGHT, fy + 0.010, ACCENT, 0.9)
    for i, txt in enumerate(footnotes):
        ax.text(LEFT + 0.002, fy - 0.004 - i * 0.0175, txt,
                fontsize=8.2, color=FOOT, va="center", ha="left")

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=200, facecolor=WHITE)
    fig.savefig(out_svg, format="svg", facecolor=WHITE)
    plt.close(fig)


# --------------------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只解析并打印结构，不写文件")
    args = ap.parse_args()

    src_svg = source_svg()
    genes, rows, footnotes = parse_svg(src_svg)
    n_data = sum(1 for r in rows if r["type"] == "data")
    n_sec = sum(1 for r in rows if r["type"] == "section")
    n_grp = sum(1 for r in rows if r["type"] == "group")
    print(f"数据源：{src_svg.relative_to(ROOT)}")
    print(f"解析：{len(genes)} 列基因 / {n_data} 数据行 / {n_sec} 区块 / "
          f"{n_grp} 子分组 / {len(footnotes)} 行脚注")
    if args.dry_run:
        for r in rows[:6]:
            print("  ", r["type"], r.get("text") or r.get("label"))
        return 0

    # 备份原件（PNG + SVG），不覆盖历史证据
    bdir = ROOT / "Figure" / "EN_journal_figures" / f"_v1_before_{STAMP}"
    bdir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "svg"):
        src = SRC.with_suffix("." + ext)
        dst = bdir / src.name
        # 重复运行时源文件已是新图，绝不能覆盖首次备份的原件
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)

    first_svg = None
    for d in OUT_DIRS:
        png = d / "FigS2_Gene_Nomenclature.png"
        svg = d / "FigS2_Gene_Nomenclature.svg"
        draw(genes, rows, footnotes, png, svg)
        print(f"已输出：{png}  /  {svg}")
        if first_svg is None:
            first_svg = svg

    if not verify_layout(src_svg, first_svg, genes, rows):
        return 1
    print(f"原图已备份到：{bdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
