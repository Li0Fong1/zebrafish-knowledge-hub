# -*- coding: utf-8 -*-
"""Fig. S1 - unique (deduplicated) publications by year, 1951-2026.

数据口径
--------
来源：MySQL zebrafish_db.literature_paper（scripts/_fetch_db_years.py 拉取的 TSV）
清洗：剔除 year 为空 (17) 与 year > 2026 (64，未来日期脏数据)
去重：有 DOI -> 按规范化 DOI 分组；无 DOI -> 按规范化标题分组（占位标题除外）；
      无 DOI 且标题为占位/空 -> 不合并，单独计 1 篇。
      每组只计 1 篇，年份取组内 MIN(year)。
验证：同一规则已用纯 SQL 在数据库端独立复算，76 个年份逐年一致
      （scripts/_audit_year_counts.py -> sql_dedup_1951_2026）。

版式（期刊双栏规范）：按最终印刷尺寸 180 mm × 74 mm 出图，不再大画布后整体缩小；
1951-1980 / 1981-1990 / 1991-2000 各合并为一根柱（这三段每年仅 0-19 篇，逐年成柱
会挤成一片贴轴的极矮柱），合并段与逐年段之间留一道小空隙；2001 起逐年单独成柱。
字号统一（刻度 6.6 pt / 轴标题 7.6 pt / 柱顶数值 5.9 pt），仅对非零柱标注数值，
图内不放说明小字。默认罗马体 Times New Roman，可 --font arial 切换。
口径与「前三柱为区间合并」的说明写入 sidecar 的 suggested_caption，供论文图注使用。

输出：Figure/EN_journal_figures/FigS1_Publications_by_Year_dedup_1951.{png,pdf}
      Figure/EN_journal_figures/_data/figS1_year_counts_dedup_1951.{json,csv}
      C:/Users/Lenovo/Desktop/斑马鱼论文/S1_dedup_1951.png
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import shutil
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TSV = os.path.join(ROOT, "scripts", "_db_papers.tsv")
OUT_DIR = os.path.join(ROOT, "Figure", "EN_journal_figures")
DATA_DIR = os.path.join(OUT_DIR, "_data")

LIGHT_GRID = "#E6EAEE"
LIGHT_FILL = "#8FB3CA"
LIGHT_EDGE = "#5B8CA9"
INK = "#1A1A1A"
AXIS = "#4D4D4D"

Y0, Y1 = 1951, 2026
# 区间合并：这三段每年仅 0-19 篇，逐年成柱会挤成贴轴的极矮柱；2001 起逐年单独成柱。
GROUPS = [(1951, 1980), (1981, 1990), (1991, 2000)]
DETAIL_START = GROUPS[-1][1] + 1      # 2001
PARTIAL_YEAR = 2026
PLACE = re.compile(r"^(ZFQA-\d+ ENO:\d+|Entry \d+ fulltext)$", re.I)

# 版面：按期刊双栏图宽度绘制（180 mm x 74 mm），出图即最终印刷尺寸，
# 字号用绝对 pt（不再缩放到 300 mm 宽再整体缩小），保证字号与柱体的比例正常。
FIG_W_IN, FIG_H_IN = 7.09, 2.91       # 180 mm x 74 mm
GROUP_GAP = 1.5        # 三根合并柱之间的类别间距（给两行区间标签留位置）
SECTION_GAP = 1.4      # 合并段与逐年段之间的分隔，提示两者时间粒度不同
Y_MAX = 2700           # y 轴上限（留出柱顶数值的位置）
LABEL_OFFSET = Y_MAX * 0.012

def apply_font(font: str) -> str:
    """罗马体 Times New Roman 或 Arial；返回实际字体名。"""
    name = "Times New Roman" if font == "times" else "Arial"
    plt.rcParams.update({
        "font.family": name,
        "mathtext.fontset": "custom" if font == "times" else "dejavusans",
        "font.size": 7,
        "axes.edgecolor": AXIS,
        "axes.linewidth": 0.6,
        "xtick.color": INK,
        "ytick.color": INK,
        "xtick.labelsize": 6.6,
        "ytick.labelsize": 6.6,
        "xtick.major.size": 2.4,
        "ytick.major.size": 2.4,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "xtick.major.pad": 3.0,
        "ytick.major.pad": 3.0,
        "axes.labelcolor": INK,
        "axes.labelsize": 7.6,
        "legend.fontsize": 6.3,
        "figure.dpi": 150,
        "hatch.linewidth": 0.6,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })
    return name


def norm(s: str) -> str:
    return " ".join((s or "").split()).strip().lower()


def compute(path: str):
    """Return (dedup year->count, raw year->count, stats)."""
    groups: dict[str, list[int]] = collections.defaultdict(list)
    singles: list[int] = []
    raw: collections.Counter = collections.Counter()
    stats = collections.Counter()
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 7:
                continue
            yr = p[1]
            stats["rows"] += 1
            if not yr.isdigit():
                stats["no_year"] += 1
                continue
            y = int(yr)
            if y > Y1:
                stats["future_dirty"] += 1
                continue
            stats["with_year_clean"] += 1
            if Y0 <= y <= Y1:
                raw[y] += 1
            doi, title = norm(p[0]), norm(p[3])
            if doi:
                key = "D:" + doi
            elif title and not PLACE.match((p[3] or "").strip()):
                key = "T:" + title
            else:
                key = None
            if key is None:
                stats["unmergeable"] += 1
                singles.append(y)
            else:
                groups[key].append(y)
    dedup: collections.Counter = collections.Counter()
    for ys in groups.values():
        m = min(ys)
        if Y0 <= m <= Y1:
            dedup[m] += 1
    for y in singles:
        if Y0 <= y <= Y1:
            dedup[y] += 1
    stats["unique_publications"] = sum(dedup.values())
    stats["raw_in_window"] = sum(raw.values())
    stats["duplicates_removed"] = stats["raw_in_window"] - stats["unique_publications"]
    return dict(dedup), dict(raw), dict(stats)


def bar_positions(n_detail: int) -> list[float]:
    """类别轴坐标：三根合并柱放宽间隔，之后是一段小空隙，再接逐年柱（等距）。"""
    xs: list[float] = []
    x = 0.0
    for _ in GROUPS:
        xs.append(x)
        x += GROUP_GAP
    x = xs[-1] + SECTION_GAP
    for _ in range(n_detail):
        xs.append(x)
        x += 1.0
    return xs


def build_figure(dedup: dict[int, int]):
    """构建图形对象。返回 (fig, ax, bars, labeled, xs)。

    bars 为 [(x轴标签, 数值, 是否 partial)]，xs 为实际类别坐标；出图与核查脚本
    共用同一份逻辑，避免核查脚本重写绘图规则而产生分歧。
    """
    # 早期每年仅 0-19 篇，若逐年成柱会挤成一片贴轴的极矮柱且标注堆叠，故按区间合并：
    # 1951-1980 / 1981-1990 / 1991-2000 各成一根柱，2001 起逐年单独成柱。
    bars: list[tuple[str, int, bool]] = []
    for a, b in GROUPS:
        bars.append((f"{a}~{b}", sum(dedup.get(y, 0) for y in range(a, b + 1)), False))
    detail_years = list(range(DETAIL_START, Y1 + 1))
    bars += [(str(y), dedup.get(y, 0), y == PARTIAL_YEAR) for y in detail_years]
    xs = bar_positions(len(detail_years))

    fig, ax = plt.subplots(figsize=(FIG_W_IN, FIG_H_IN))

    BAR_W = 0.74
    solid = [(x, v) for x, (_, v, p) in zip(xs, bars) if not p]
    ax.bar([x for x, _ in solid], [v for _, v in solid], width=BAR_W,
           color=LIGHT_FILL, edgecolor=LIGHT_EDGE, linewidth=0.3, zorder=3)
    part = [(x, v) for x, (_, v, p) in zip(xs, bars) if p]
    if part:
        ax.bar([x for x, _ in part], [v for _, v in part], width=BAR_W, color="white",
               edgecolor=LIGHT_EDGE, linewidth=0.6, hatch="////", zorder=3)

    YTOP = Y_MAX
    # 只标注非零柱；竖排避免相邻柱的数字互相重叠；标签紧跟柱顶（不虚构高度）。
    labeled = 0
    for x, (_, v, _) in zip(xs, bars):
        if v <= 0:
            continue
        ax.text(x, v + LABEL_OFFSET, f"{v:,}", rotation=90, ha="center", va="bottom",
                fontsize=5.9, color=INK, zorder=4)
        labeled += 1

    # 类别轴：三根合并柱的区间标签折两行；逐年只标 2005/2010/2015/2020/2025。
    tick_years = [2005, 2010, 2015, 2020, 2025]
    ax.set_xticks(xs[:len(GROUPS)] + [xs[len(GROUPS) + (y - DETAIL_START)] for y in tick_years])
    ax.set_xticklabels([f"{a}~\n{b}" for a, b in GROUPS] + [str(y) for y in tick_years])
    for t in ax.get_xticklabels()[:len(GROUPS)]:
        t.set_linespacing(1.02)

    ax.set_xlim(xs[0] - 0.9, xs[-1] + 0.9)
    ax.set_ylim(0, YTOP)
    ax.set_yticks([0, 500, 1000, 1500, 2000, 2500])
    ax.set_yticklabels(["0", "500", "1,000", "1,500", "2,000", "2,500"])
    ax.set_xlabel("Publication year", labelpad=4)
    ax.set_ylabel("Number of publications", labelpad=4)

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(0.6)
    # 每根柱均有数值标注，故不再画横向网格线：避免网格线穿过柱顶数字（如 2,286 跨 2,500 刻度）
    ax.set_axisbelow(True)
    ax.tick_params(which="both", direction="out")

    handles = [
        Patch(facecolor=LIGHT_FILL, edgecolor=LIGHT_EDGE, linewidth=0.3,
              label=f"{Y0}~{Y1-1}"),
        Patch(facecolor="white", edgecolor=LIGHT_EDGE, linewidth=0.6, hatch="////",
              label=f"{PARTIAL_YEAR} (partial)"),
    ]
    # 图例放到坐标区上方（横排一行），避免压住网格线与柱顶数值
    ax.legend(handles=handles, frameon=False, loc="lower left", bbox_to_anchor=(0.0, 1.012),
              ncol=2, handlelength=1.25, handleheight=0.8, columnspacing=1.3,
              handletextpad=0.5, borderaxespad=0.0)

    fig.subplots_adjust(left=0.098, right=0.985, top=0.885, bottom=0.185)
    return fig, ax, bars, labeled, xs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", choices=("times", "arial"), default="times",
                        help="times=罗马体 Times New Roman（默认）；arial=Arial")
    args = parser.parse_args()
    font_name = apply_font(args.font)
    suffix = "" if args.font == "times" else "_arial"

    if not os.path.isfile(DEFAULT_TSV):
        print(f"missing input: {DEFAULT_TSV}", file=sys.stderr)
        return 2
    dedup, raw, stats = compute(DEFAULT_TSV)

    years = list(range(Y0, Y1 + 1))
    fig, ax, bars, labeled, xs = build_figure(dedup)
    n = len(bars)
    detail_years = list(range(DETAIL_START, Y1 + 1))

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    base = f"FigS1_Publications_by_Year_dedup_1951{suffix}"
    png = os.path.join(OUT_DIR, base + ".png")
    pdf = os.path.join(OUT_DIR, base + ".pdf")
    fig.savefig(png, dpi=600)
    fig.savefig(pdf)
    plt.close(fig)

    desk_dir = r"C:/Users/Lenovo/Desktop/斑马鱼论文"
    desk_png = os.path.join(desk_dir, f"S1_dedup_1951{suffix}.png")
    try:
        os.makedirs(desk_dir, exist_ok=True)
        shutil.copyfile(png, desk_png)
    except Exception as e:
        desk_png = f"(复制失败: {e})"

    if suffix:  # 数据文件只由默认版写出，避免重复
        print(f"font {font_name} | labeled {labeled} non-zero years")
        print("written:", png)
        print("written:", pdf)
        print("desktop:", desk_png)
        return 0

    payload = {
        "source": "MySQL literature_paper (TSV via scripts/_fetch_db_years.py, refreshed 2026-11-12)",
        "scope": "unique publications, deduplicated by DOI / normalized title",
        "window": [Y0, Y1],
        "partial_year": PARTIAL_YEAR,
        "cleaning": {
            "no_year_excluded": stats.get("no_year", 0),
            "future_dated_excluded": stats.get("future_dirty", 0),
            "duplicate_records_removed": stats.get("duplicates_removed", 0),
            "unmergeable_placeholder_rows_kept": stats.get("unmergeable", 0),
        },
        "dedup_rule": "doi -> normalized doi; no doi -> normalized title (placeholder titles excluded); "
                      "otherwise keep as individual record; year = MIN(year) within group",
        "summary": {
            "total_rows": stats.get("rows", 0),
            "raw_in_window_before_dedup": stats.get("raw_in_window", 0),
            "unique_publications": stats.get("unique_publications", 0),
            "pre_1990": sum(dedup.get(y, 0) for y in range(Y0, 1990)),
            "1990_2026": sum(dedup.get(y, 0) for y in range(1990, Y1 + 1)),
            "earliest_year": min(y for y, v in dedup.items() if v > 0),
        },
        "counts_by_year": {str(y): dedup.get(y, 0) for y in years},
        "counts_by_year_before_dedup": {str(y): raw.get(y, 0) for y in years},
        "plot_bars": {
            "grouped_bars": [
                {"label": f"{a}-{b}", "years": b - a + 1,
                 "value": sum(dedup.get(y, 0) for y in range(a, b + 1))}
                for a, b in GROUPS
            ],
            "individual_years": {"from": DETAIL_START, "to": Y1},
            "bar_count": n,
            "labeled_bars": labeled,
            "check_sum": sum(v for _, v, _ in bars),
        },
        "decades": {
            "1951-1989": sum(dedup.get(y, 0) for y in range(Y0, 1990)),
            "1990-2000": sum(dedup.get(y, 0) for y in range(1990, 2001)),
            "2001-2010": sum(dedup.get(y, 0) for y in range(2001, 2011)),
            "2011-2020": sum(dedup.get(y, 0) for y in range(2011, 2021)),
            "2021-2025": sum(dedup.get(y, 0) for y in range(2021, 2026)),
            "2026": dedup.get(2026, 0),
        },
        "suggested_caption": (
            f"Fig. S1. Unique publications by year in the analysed corpus "
            f"(1951\u2013{2026}, n = {stats['unique_publications']:,}). "
            f"The first three bars aggregate all publications from "
            + ", ".join(f"{a}\u2013{b} (n = {sum(dedup.get(y, 0) for y in range(a, b + 1)):,})"
                        for a, b in GROUPS[:-1])
            + f" and {GROUPS[-1][0]}\u2013{GROUPS[-1][1]} "
              f"(n = {sum(dedup.get(y, 0) for y in range(GROUPS[-1][0], GROUPS[-1][1] + 1)):,}); "
            f"individual years are shown from {DETAIL_START} onward. "
            f"Records were deduplicated by DOI (or by normalized title when DOI was absent), "
            f"removing {stats['duplicates_removed']:,} duplicate records; "
            f"{stats.get('future_dirty',0)} future-dated (2027) records and "
            f"{stats.get('no_year',0)} records without a publication year were excluded. "
            f"{PARTIAL_YEAR} is incomplete at the time of data export (hatched bar)."
        ),
    }
    with open(os.path.join(DATA_DIR, "figS1_year_counts_dedup_1951.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA_DIR, "figS1_year_counts_dedup_1951.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["year", "unique_publications", "before_dedup"])
        for y in years:
            w.writerow([y, dedup.get(y, 0), raw.get(y, 0)])

    print(f"font {font_name} | rows {stats['rows']:,} | raw in window {stats['raw_in_window']:,} "
          f"| unique {stats['unique_publications']:,} | duplicates removed {stats['duplicates_removed']:,} "
          f"| bars {n} ({len(GROUPS)} grouped + {len(detail_years)}) | labeled {labeled}")
    print("grouped:", [(f"{a}-{b}", sum(dedup.get(y, 0) for y in range(a, b + 1))) for a, b in GROUPS],
          "| individual", DETAIL_START, "-", Y1, "=", f"{sum(dedup.get(y, 0) for y in detail_years):,}")
    print("decades:", payload["decades"])
    print("written:", png)
    print("written:", pdf)
    print("desktop:", desk_png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
