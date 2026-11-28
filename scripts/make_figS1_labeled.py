# -*- coding: utf-8 -*-
"""Fig. S1 - publications per year with exact counts labelled.

Data: data/literature/literature_catalog.jsonl (same source as
.workbuddy/tmp/pdfread/year_hist.json), recounted here so the labels and the
bars can never drift apart.
"""
from __future__ import annotations

import collections
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG = os.path.join(ROOT, "data", "literature", "literature_catalog.jsonl")
OUT_DIR = os.path.join(ROOT, "Figure", "EN_journal_figures")

INK = "#0D1520"
GRID = "#E3E9F1"
TEAL = "#106A94"
TEAL_M = "#7FB0CE"

Y0, Y1 = 1990, 2026
FULL_UNTIL = 2020
RECENT = (2021, 2025)

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


def read_counts() -> dict[int, int]:
    counter: collections.Counter = collections.Counter()
    with open(CATALOG, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                year = int(str(json.loads(line).get("year"))[:4])
            except Exception:
                continue
            counter[year] += 1
    return dict(counter)


def main() -> int:
    counts = read_counts()
    years = list(range(Y0, Y1 + 1))
    values = [counts.get(y, 0) for y in years]
    total = sum(values)

    fig, ax = plt.subplots(figsize=(7.1, 2.85))

    for year, value in zip(years, values):
        if year <= FULL_UNTIL:
            ax.bar(year, value, width=0.74, color=TEAL_M, edgecolor="none", zorder=3)
        elif RECENT[0] <= year <= RECENT[1]:
            ax.bar(year, value, width=0.74, color=TEAL, edgecolor="none", zorder=3)
        else:
            ax.bar(year, value, width=0.74, color="white",
                   edgecolor=TEAL, linewidth=0.8, hatch="////", zorder=3)

    for year, value in zip(years, values):
        ax.text(year, value + 45, f"{value:,}", rotation=90, ha="center", va="bottom",
                fontsize=5.6, color=INK, zorder=4)

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
    ax.yaxis.grid(True, color=GRID, linewidth=0.55)
    ax.set_axisbelow(True)

    handles = [
        Patch(facecolor=TEAL_M, edgecolor="none", label=f"{Y0}\u2013{FULL_UNTIL}"),
        Patch(facecolor=TEAL, edgecolor="none", label=f"{RECENT[0]}\u2013{RECENT[1]}"),
        Patch(facecolor="white", edgecolor=TEAL, linewidth=0.8, hatch="////",
              label="2026 (partial)"),
    ]
    ax.legend(handles=handles, frameon=False, loc="upper left", handlelength=1.4,
              handleheight=0.9, borderaxespad=0.3, labelspacing=0.35,
              ncol=3, columnspacing=1.1)

    fig.subplots_adjust(left=0.075, right=0.995, top=0.965, bottom=0.175)

    os.makedirs(OUT_DIR, exist_ok=True)
    png = os.path.join(OUT_DIR, "FigS1_Publications_by_Year_labeled.png")
    pdf = os.path.join(OUT_DIR, "FigS1_Publications_by_Year_labeled.pdf")
    fig.savefig(png, dpi=600)
    fig.savefig(pdf)
    plt.close(fig)

    print(f"years {Y0}-{Y1}, total {total:,}")
    print(f"2021-2025 sum {sum(counts.get(y, 0) for y in range(2021, 2026)):,}")
    print(f"written: {png}")
    print(f"written: {pdf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
