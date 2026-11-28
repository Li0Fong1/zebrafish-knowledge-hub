# -*- coding: utf-8 -*-
"""Fig. S1 (raw / pre-cleaning version) - publications RETRIEVED before screening, by year.

数据口径
--------
来源：MySQL zebrafish_db.literature_pubmed（脚本 scripts/_query_preclean_year.py 解析
      raw_xml 里的 <PubDate>/<Year> 得到逐年分布；同时记录 NCBI 检索命中与 WoS 抓取量）
口径：清洗/筛选/去重之前的「原始检索落库」规模 —— 即项目最早抓回、尚未做任何
      数据处理（未判定、未去重、未入索引）的文献。
      literature_pubmed 落库 67,746 条；WoS 落库 1,408 条；合计原始文献池 69,154 条。
      当初产生这批数据的 NCBI 检索命中为 67,779（落库时）/ 实时 68,720。
版式：仿已定稿的 Fig.S1 学术浅蓝图（180 mm × 74 mm 双栏），英文标签，罗马体 Times New Roman；
      1951-1980 / 1981-1990 / 1991-2000 各合并为一根柱，2001 起逐年成柱；
      2026 为数据导出时尚未结束的年份（斜纹 partial 柱）；2027 仅 4 条且不完整，剔除。
说明：原始逐年峰值 2025 = 4,808，远高于清洗后语料，故 y 轴上限定为 5,200。

输出：Figure/EN_journal_figures/FigS1_raw_preclean_1951.{png,pdf}
      Figure/EN_journal_figures/_data/figS1_raw_preclean_year_counts.{json,csv}
      C:/Users/Lenovo/Desktop/斑马鱼论文/S1_raw_preclean.png
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_SRC = os.path.join(ROOT, "scripts", "_preclean_year.json")
OUT_DIR = os.path.join(ROOT, "Figure", "EN_journal_figures")
DATA_DIR = os.path.join(OUT_DIR, "_data")

LIGHT_FILL = "#8FB3CA"
LIGHT_EDGE = "#5B8CA9"
INK = "#1A1A1A"
AXIS = "#4D4D4D"

Y0, Y1 = 1951, 2026
GROUPS = [(1951, 1980), (1981, 1990), (1991, 2000)]
DETAIL_START = GROUPS[-1][1] + 1      # 2001
PARTIAL_YEAR = 2026
FIG_W_IN, FIG_H_IN = 7.09, 2.91       # 180 mm x 74 mm
GROUP_GAP = 1.5
SECTION_GAP = 1.4
Y_MAX = 5200
Y_TICKS = [0, 1000, 2000, 3000, 4000, 5000]
LABEL_OFFSET = Y_MAX * 0.012


def apply_font(font: str) -> str:
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


def load_raw() -> dict:
    with open(DATA_SRC, encoding="utf-8") as f:
        j = json.load(f)
    by_year = {int(k): int(v) for k, v in j["pubmed_by_year"].items()}
    return {
        "by_year": by_year,
        "pubmed_total": int(j["pubmed_total"]),
        "pubmed_no_year": int(j.get("pubmed_no_year", 0)),
        "wos_total": int(j.get("wos_total", 0)),
        "search_hit_then": 67779,   # 落库时 NCBI 检索命中（data/literature/pubmed_raw_import.json）
        "search_hit_live": 68720,   # 本次实时复算（_ncbi_scope_count.json OLD_QUERY）
    }


def build_bars(raw: dict[int, int]) -> list[tuple[str, int, bool]]:
    bars: list[tuple[str, int, bool]] = []
    for a, b in GROUPS:
        bars.append((f"{a}~{b}", sum(raw.get(y, 0) for y in range(a, b + 1)), False))
    for y in range(DETAIL_START, Y1 + 1):
        bars.append((str(y), raw.get(y, 0), y == PARTIAL_YEAR))
    return bars


def bar_positions(n_detail: int) -> list[float]:
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


def build_figure(bars: list[tuple[str, int, bool]]):
    xs = bar_positions(len([b for b in bars if not (b[1] == 0 and False)]) - len(GROUPS))  # placeholder
    # 重新按真实逐年数定位
    n_detail = len([b for b in bars if b[0].isdigit()])
    xs = bar_positions(n_detail)

    fig, ax = plt.subplots(figsize=(FIG_W_IN, FIG_H_IN))
    BAR_W = 0.74
    solid = [(x, v) for x, (_, v, p) in zip(xs, bars) if not p]
    ax.bar([x for x, _ in solid], [v for _, v in solid], width=BAR_W,
           color=LIGHT_FILL, edgecolor=LIGHT_EDGE, linewidth=0.3, zorder=3)
    part = [(x, v) for x, (_, v, p) in zip(xs, bars) if p]
    if part:
        ax.bar([x for x, _ in part], [v for _, v in part], width=BAR_W, color="white",
               edgecolor=LIGHT_EDGE, linewidth=0.6, hatch="////", zorder=3)

    labeled = 0
    for x, (_, v, _) in zip(xs, bars):
        if v <= 0:
            continue
        ax.text(x, v + LABEL_OFFSET, f"{v:,}", rotation=90, ha="center", va="bottom",
                fontsize=5.9, color=INK, zorder=4)
        labeled += 1

    tick_years = [2005, 2010, 2015, 2020, 2025]
    ax.set_xticks(xs[:len(GROUPS)] + [xs[len(GROUPS) + (y - DETAIL_START)] for y in tick_years])
    ax.set_xticklabels([f"{a}~\n{b}" for a, b in GROUPS] + [str(y) for y in tick_years])
    for t in ax.get_xticklabels()[:len(GROUPS)]:
        t.set_linespacing(1.02)

    ax.set_xlim(xs[0] - 0.9, xs[-1] + 0.9)
    ax.set_ylim(0, Y_MAX)
    ax.set_yticks(Y_TICKS)
    ax.set_yticklabels([f"{t:,}" if t else "0" for t in Y_TICKS])
    ax.set_xlabel("Publication year", labelpad=4)
    ax.set_ylabel("Number of publications", labelpad=4)

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
        ax.spines[side].set_linewidth(0.6)
    ax.set_axisbelow(True)
    ax.tick_params(which="both", direction="out")

    handles = [
        Patch(facecolor=LIGHT_FILL, edgecolor=LIGHT_EDGE, linewidth=0.3,
              label=f"{Y0}~{Y1-1}"),
        Patch(facecolor="white", edgecolor=LIGHT_EDGE, linewidth=0.6, hatch="////",
              label=f"{PARTIAL_YEAR} (partial)"),
    ]
    ax.legend(handles=handles, frameon=False, loc="lower left", bbox_to_anchor=(0.0, 1.012),
              ncol=2, handlelength=1.25, handleheight=0.8, columnspacing=1.3,
              handletextpad=0.5, borderaxespad=0.0)

    fig.subplots_adjust(left=0.098, right=0.985, top=0.885, bottom=0.185)
    return fig, ax, labeled, xs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", choices=("times", "arial"), default="times")
    args = parser.parse_args()
    font_name = apply_font(args.font)
    suffix = "" if args.font == "times" else "_arial"

    src = load_raw()
    raw = src["by_year"]
    bars = build_bars(raw)
    fig, ax, labeled, xs = build_figure(bars)

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    base = f"FigS1_raw_preclean_1951{suffix}"
    png = os.path.join(OUT_DIR, base + ".png")
    pdf = os.path.join(OUT_DIR, base + ".pdf")
    fig.savefig(png, dpi=600)
    fig.savefig(pdf)
    plt.close(fig)

    desk_dir = r"C:/Users/Lenovo/Desktop/斑马鱼论文"
    desk_png = os.path.join(desk_dir, f"S1_raw_preclean{suffix}.png")
    try:
        os.makedirs(desk_dir, exist_ok=True)
        shutil.copyfile(png, desk_png)
    except Exception as e:  # noqa
        desk_png = f"(复制失败: {e})"

    in_window = sum(v for k, v in raw.items() if Y0 <= k <= Y1)
    if suffix:
        print(f"font {font_name} | labeled {labeled} non-zero bars")
        print("written:", png, "|", pdf, "| desktop:", desk_png)
        return 0

    years = list(range(Y0, Y1 + 1))
    payload = {
        "figure": "Fig. S1 (pre-cleaning / raw retrieval)",
        "source": "MySQL literature_pubmed (raw_xml PubDate parsed by scripts/_query_preclean_year.py)",
        "scope": "publications RETRIEVED before any screening/dedup/indexing (raw pool)",
        "window": [Y0, Y1],
        "partial_year": PARTIAL_YEAR,
        "cleaning_note": "These are pre-cleaning counts: NOT yet screened (yes/no), NOT deduplicated, "
                        "NOT yet indexed. Duplicates and non-zebrafish records are still included.",
        "pool_totals": {
            "pubmed_raw_rows": src["pubmed_total"],
            "wos_raw_rows": src["wos_total"],
            "combined_raw_pool": src["pubmed_total"] + src["wos_total"],
            "ncbi_search_hit_at_import": src["search_hit_then"],
            "ncbi_search_hit_live_recheck": src["search_hit_live"],
            "pubmed_without_parseable_year_excluded": src["pubmed_no_year"],
            "pubmed_year_2027_excluded_incomplete": raw.get(2027, 0),
            "pubmed_in_window_1951_2026": in_window,
        },
        "counts_by_year": {str(y): raw.get(y, 0) for y in years},
        "plot_bars": {
            "grouped_bars": [
                {"label": f"{a}~{b}", "value": sum(raw.get(y, 0) for y in range(a, b + 1))}
                for a, b in GROUPS
            ],
            "individual_years": {"from": DETAIL_START, "to": Y1},
            "bar_count": len(bars),
            "labeled_bars": labeled,
        },
        "suggested_caption": (
            f"Fig. S1. Publications retrieved before screening, by year "
            f"({Y0}\u2013{2026}, n = {in_window:,} with a publication year; "
            f"raw pool = {src['pubmed_total']:,} PubMed + {src['wos_total']:,} Web of Science "
            f"= {src['pubmed_total'] + src['wos_total']:,}). "
            f"The first three bars aggregate {GROUPS[0][0]}\u2013{GROUPS[0][1]}, "
            f"{GROUPS[1][0]}\u2013{GROUPS[1][1]}, and {GROUPS[2][0]}\u2013{GROUPS[2][1]}; "
            f"individual years are shown from {DETAIL_START} onward. "
            f"These are pre-cleaning counts and therefore still include duplicate records and "
            f"studies not ultimately retained in the curated corpus. "
            f"{PARTIAL_YEAR} is incomplete at the time of data export (hatched bar)."
        ),
    }
    with open(os.path.join(DATA_DIR, "figS1_raw_preclean_year_counts.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA_DIR, "figS1_raw_preclean_year_counts.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["year", "raw_retrieved_count"])
        for y in years:
            w.writerow([y, raw.get(y, 0)])

    print(f"font {font_name} | in_window {in_window:,} | bars {len(bars)} | labeled {labeled}")
    print("grouped:", [(f"{a}~{b}", sum(raw.get(y, 0) for y in range(a, b + 1))) for a, b in GROUPS])
    print("written:", png, "|", pdf, "| desktop:", desk_png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
