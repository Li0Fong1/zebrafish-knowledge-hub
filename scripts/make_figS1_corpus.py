# -*- coding: utf-8 -*-
"""Fig. S1 (v2) - publications per year in the ANALYSED CORPUS.

Why this version exists
-----------------------
The 2026-11-09 version counted ``data/literature/literature_catalog.jsonl``,
which is the *screening catalogue* (35,261 candidate records, mostly WoS).
The manuscript reports the *analysed corpus* taken from MySQL
``literature_paper`` (32,884 screened -> 32,549 with full text -> 32,532 with a
publication year = the indexed corpus).  Only 38% of the catalogue overlaps the
corpus, so the old figure did not add up against the text (35,067 vs 32,532).

This script plots the corpus instead, so that
    sum of bars (1990-2026) + pre-1990 records == records in the index.

Input   : tab-separated dump of ``literature_paper``
          (columns: doi, year, gold_status, title, source, judgement, has_fulltext)
          produced by ``scripts/_fetch_db_years.py`` (read-only).
Output  : Figure/EN_journal_figures/FigS1_Publications_by_Year_corpus.{png,pdf}
          Figure/EN_journal_figures/_data/figS1_year_counts.json  (sidecar, for audit)
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TSV = os.path.join(ROOT, "scripts", "_db_papers.tsv")
OUT_DIR = os.path.join(ROOT, "Figure", "EN_journal_figures")
DATA_DIR = os.path.join(OUT_DIR, "_data")

INK = "#0D1520"
GRID = "#E3E9F1"
TEAL = "#106A94"

# --- 浅色版配色（--palette light）-----------------------------------------
# 深色版（默认）沿用 TEAL 实心柱；浅色版改为淡蓝实心 + 同色系描边，
# 描边是为了 600 dpi 缩到单栏宽时柱体边界不糊掉，不是阴影/渐变。
LIGHT_GRID = "#EDF2F7"
LIGHT_FILL = "#A8CDE0"
LIGHT_EDGE = "#5E9DBC"
LIGHT_PARTIAL_EDGE = "#5E9DBC"
LIGHT_LABEL = "#1B3A4B"

Y0, Y1 = 1990, 2026          # plotted window
PARTIAL_YEAR = 2026          # incomplete at export date (2026-10-10)

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


def read_corpus(path: str) -> tuple[dict[int, int], dict[str, int]]:
    """Return (year -> count of full-text records, summary counters)."""
    counts: collections.Counter = collections.Counter()
    summary = collections.Counter()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 7:
                continue
            year_raw, has_full = parts[1], parts[6]
            summary["screened"] += 1
            if has_full != "1":
                summary["no_fulltext"] += 1
                continue
            summary["fulltext"] += 1
            if not year_raw.isdigit():
                summary["no_year"] += 1
                continue
            counts[int(year_raw)] += 1
    return dict(counts), dict(summary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tsv", default=DEFAULT_TSV, help="literature_paper TSV dump")
    parser.add_argument("--palette", choices=("ink", "light"), default="ink",
                        help="ink=原有深青实心柱（默认）；light=浅蓝实心柱")
    parser.add_argument("--out-suffix", default="", help="输出文件名后缀，如 _light")
    args = parser.parse_args()

    light = args.palette == "light"
    bar_fill = LIGHT_FILL if light else TEAL
    bar_edge = LIGHT_EDGE if light else "none"
    bar_lw = 0.4 if light else 0.0
    label_color = LIGHT_LABEL if light else INK
    grid_color = LIGHT_GRID if light else GRID

    if not os.path.isfile(args.tsv):
        print(f"missing input: {args.tsv}\nrun scripts/_fetch_db_years.py first", file=sys.stderr)
        return 2

    counts, summary = read_corpus(args.tsv)
    years = list(range(Y0, Y1 + 1))
    values = [counts.get(y, 0) for y in years]
    plotted = sum(values)
    pre = sum(c for y, c in counts.items() if y < Y0)
    earliest = min(counts) if counts else None

    fig, ax = plt.subplots(figsize=(7.1, 2.85))

    for year, value in zip(years, values):
        if year == PARTIAL_YEAR:
            ax.bar(year, value, width=0.74, color="white",
                   edgecolor=(LIGHT_PARTIAL_EDGE if light else TEAL),
                   linewidth=0.8, hatch="////", zorder=3)
        else:
            ax.bar(year, value, width=0.74, color=bar_fill, edgecolor=bar_edge,
                   linewidth=bar_lw, zorder=3)

    for year, value in zip(years, values):
        ax.text(year, value + 40, f"{value:,}", rotation=90, ha="center", va="bottom",
                fontsize=5.6, color=label_color, zorder=4)

    ax.set_xlim(Y0 - 0.7, Y1 + 0.7)
    ax.set_ylim(0, 3000)
    ax.set_xticks(range(Y0, Y1 + 1, 5))
    ax.set_yticks([0, 500, 1000, 1500, 2000, 2500])
    ax.set_xlabel("Publication year")
    ax.set_ylabel("Publications")

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#8C8C8C")
        ax.spines[side].set_linewidth(0.6)
    ax.yaxis.grid(True, color=grid_color, linewidth=0.55)
    ax.set_axisbelow(True)

    ax.text(0.012, 0.80, f"Analysed corpus, n = {plotted:,}", transform=ax.transAxes,
            ha="left", va="top", fontsize=6.2, color=label_color)

    handles = [
        Patch(facecolor=bar_fill, edgecolor=bar_edge, linewidth=bar_lw,
              label=f"{Y0}\u2013{Y1 - 1}"),
        Patch(facecolor="white", edgecolor=(LIGHT_PARTIAL_EDGE if light else TEAL),
              linewidth=0.8, hatch="////",
              label=f"{PARTIAL_YEAR} (partial)"),
    ]
    ax.legend(handles=handles, frameon=False, loc="upper left", handlelength=1.4,
              handleheight=0.9, borderaxespad=0.3, labelspacing=0.35,
              ncol=2, columnspacing=1.1)

    fig.subplots_adjust(left=0.075, right=0.995, top=0.965, bottom=0.175)

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    suffix = args.out_suffix or ("_light" if light else "")
    png = os.path.join(OUT_DIR, f"FigS1_Publications_by_Year_corpus{suffix}.png")
    pdf = os.path.join(OUT_DIR, f"FigS1_Publications_by_Year_corpus{suffix}.pdf")
    fig.savefig(png, dpi=600)
    fig.savefig(pdf)
    plt.close(fig)

    payload = {
        "source": "MySQL literature_paper (via scripts/_fetch_db_years.py)",
        "window": [Y0, Y1],
        "partial_year": PARTIAL_YEAR,
        "summary": {
            "screened": summary.get("screened", 0),
            "with_fulltext": summary.get("fulltext", 0),
            "without_fulltext": summary.get("no_fulltext", 0),
            "with_year": sum(counts.values()),
            "without_year": summary.get("no_year", 0),
            "plotted": plotted,
            "pre_1990": pre,
            "earliest_year": earliest,
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
    with open(os.path.join(DATA_DIR, "figS1_year_counts.json"), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(f"screened {summary.get('screened', 0):,} | full text {summary.get('fulltext', 0):,} "
          f"| with year {sum(counts.values()):,}")
    print(f"plotted {Y0}-{Y1}: {plotted:,} | pre-{Y0}: {pre} (earliest {earliest})")
    print("decades:", payload["decades"])
    print(f"written: {png}")
    print(f"written: {pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
