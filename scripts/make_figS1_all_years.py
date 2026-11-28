# -*- coding: utf-8 -*-
"""Fig. S1 (all-records-with-year variant).

与 make_figS1_corpus.py 的区别：
  * 这里统计的是 literature_paper 中「全部带出版年份」的记录（不论是否有全文），
    而非仅 has_fulltext=1 的子集。
  * 剔除脏数据：2027 年（未来日期、无全文的 64 条幻影记录）不计入。
  * 1990–2026 为绘制窗口；1951–1989 另有 51 条（已在总数中说明，不单独画柱）。
  * 沿用浅色版配色：浅蓝实心柱 + 深灰蓝描边，2026 斜纹 partial。

输入：scripts/_db_papers.tsv （由 scripts/_fetch_db_years.py 从 MySQL 重新拉取）
输出：Figure/EN_journal_figures/FigS1_Publications_by_Year_all.{png,pdf}
      以及桌面 斑马鱼论文/S1_all.png 副本
"""
from __future__ import annotations

import collections
import json
import os
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

# --- 浅色版配色（与 S1.png 一致）---
LIGHT_GRID = "#EDF2F7"
LIGHT_FILL = "#A8CDE0"
LIGHT_EDGE = "#5E9DBC"
LIGHT_LABEL = "#1B3A4B"
INK = "#0D1520"

Y0, Y1 = 1990, 2026          # 绘制窗口
PARTIAL_YEAR = 2026          # 导出时点（2026-11-12）仍不完整
FUTURE_YEAR = 2027           # 脏数据：未来日期，剔除

plt.rcParams.update({
    "font.family": "Arial",
    "font.size": 7.2,
    "axes.edgecolor": "#8C8C8C",
    "axes.linewidth": 0.6,
    "xtick.color": "#333333",
    "ytick.color": "#333333",
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "xtick.major.size": 2.4,
    "ytick.major.size": 2.4,
    "xtick.major.width": 0.6,
    "ytick.major.width": 0.6,
    "axes.labelcolor": INK,
    "axes.labelsize": 7.5,
    "legend.fontsize": 5.8,
    "figure.dpi": 150,
    "hatch.linewidth": 0.7,
})


def read_all_with_year(path: str):
    """Return (year->count, summary). 统计全部带年份记录，剔除未来年份脏数据。"""
    counts: collections.Counter = collections.Counter()
    summary = collections.Counter()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 7:
                continue
            year_raw = parts[1]
            summary["rows"] += 1
            if not year_raw.isdigit():
                summary["no_year"] += 1
                continue
            y = int(year_raw)
            if y > Y1:          # 2027 等未来日期脏数据
                summary["future_dirty"] += 1
                continue
            summary["with_year"] += 1
            if y < Y0:
                summary["pre_1990"] += 1
            counts[y] += 1
    return dict(counts), dict(summary)


def main() -> int:
    tsv = DEFAULT_TSV
    if not os.path.isfile(tsv):
        print(f"missing input: {tsv}\nrun scripts/_fetch_db_years.py first", file=sys.stderr)
        return 2

    counts, summary = read_all_with_year(tsv)
    years = list(range(Y0, Y1 + 1))
    values = [counts.get(y, 0) for y in years]
    plotted = sum(values)
    pre = summary.get("pre_1990", 0)

    fig, ax = plt.subplots(figsize=(7.1, 2.85))

    for year, value in zip(years, values):
        if year == PARTIAL_YEAR:
            ax.bar(year, value, width=0.74, color="white",
                   edgecolor=LIGHT_EDGE, linewidth=0.8, hatch="////", zorder=3)
        else:
            ax.bar(year, value, width=0.74, color=LIGHT_FILL, edgecolor=LIGHT_EDGE,
                   linewidth=0.4, zorder=3)

    for year, value in zip(years, values):
        ax.text(year, value + 40, f"{value:,}", rotation=90, ha="center", va="bottom",
                fontsize=5.6, color=LIGHT_LABEL, zorder=4)

    ax.set_xlim(Y0 - 0.7, Y1 + 0.7)
    ax.set_ylim(0, 2600)
    ax.set_xticks(range(Y0, Y1 + 1, 5))
    ax.set_yticks([0, 500, 1000, 1500, 2000, 2500])
    ax.set_xlabel("Publication year")
    ax.set_ylabel("Publications")

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#8C8C8C")
        ax.spines[side].set_linewidth(0.6)
    ax.yaxis.grid(True, color=LIGHT_GRID, linewidth=0.55)
    ax.set_axisbelow(True)

    ax.text(0.012, 0.86, f"All records with a publication year, n = {plotted:,}",
            transform=ax.transAxes, ha="left", va="top", fontsize=6.2, color=LIGHT_LABEL)
    ax.text(0.012, 0.78,
            f"+ {pre:,} records before {Y0} (1951\u20131989), not plotted\n"
            f"{summary.get('future_dirty',0):,} future-dated (2027) excluded",
            transform=ax.transAxes, ha="left", va="top", fontsize=5.4, color="#555555")

    handles = [
        Patch(facecolor=LIGHT_FILL, edgecolor=LIGHT_EDGE, linewidth=0.4,
              label=f"{Y0}\u2013{Y1-1}"),
        Patch(facecolor="white", edgecolor=LIGHT_EDGE, linewidth=0.8, hatch="////",
              label=f"{PARTIAL_YEAR} (partial)"),
    ]
    ax.legend(handles=handles, frameon=False, loc="upper left", handlelength=1.4,
              handleheight=0.9, borderaxespad=0.3, labelspacing=0.35,
              ncol=2, columnspacing=1.1)

    fig.subplots_adjust(left=0.075, right=0.995, top=0.965, bottom=0.175)

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    png = os.path.join(OUT_DIR, "FigS1_Publications_by_Year_all.png")
    pdf = os.path.join(OUT_DIR, "FigS1_Publications_by_Year_all.pdf")
    fig.savefig(png, dpi=600)
    fig.savefig(pdf)
    plt.close(fig)

    # 复制到桌面 斑马鱼论文 目录，便于直接使用
    desk_dir = r"C:/Users/Lenovo/Desktop/斑马鱼论文"
    desk_png = os.path.join(desk_dir, "S1_all.png")
    try:
        os.makedirs(desk_dir, exist_ok=True)
        shutil.copyfile(png, desk_png)
    except Exception as e:
        desk_png = f"(复制失败: {e})"

    payload = {
        "source": "MySQL literature_paper (via scripts/_fetch_db_years.py, refreshed 2026-11-12)",
        "scope": "all records with a publication year (has_fulltext not required)",
        "window": [Y0, Y1],
        "partial_year": PARTIAL_YEAR,
        "excluded": {"future_dated_year": FUTURE_YEAR, "future_dated_count": summary.get("future_dirty", 0)},
        "summary": {
            "total_rows": summary.get("rows", 0),
            "with_year_total": summary.get("with_year", 0),
            "no_year": summary.get("no_year", 0),
            "future_dirty_excluded": summary.get("future_dirty", 0),
            "plotted_1990_2026": plotted,
            "pre_1990_not_plotted": pre,
            "earliest_year": min(counts) if counts else None,
        },
        "counts_by_year": {str(y): counts.get(y, 0) for y in years},
        "decades": {
            "1990-2000": sum(counts.get(y, 0) for y in range(1990, 2001)),
            "2001-2010": sum(counts.get(y, 0) for y in range(2001, 2011)),
            "2011-2020": sum(counts.get(y, 0) for y in range(2011, 2021)),
            "2021-2025": sum(counts.get(y, 0) for y in range(2021, 2026)),
            "2026": counts.get(2026, 0),
        },
    }
    with open(os.path.join(DATA_DIR, "figS1_year_counts_all.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(f"total rows {summary.get('rows',0):,} | with year (excl future) {summary.get('with_year',0):,} "
          f"| future-dirty excluded {summary.get('future_dirty',0)}")
    print(f"plotted {Y0}-{Y1}: {plotted:,} | pre-{Y0} (not plotted): {pre}")
    print("decades:", payload["decades"])
    print(f"written: {png}")
    print(f"written: {pdf}")
    print(f"desktop copy: {desk_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
