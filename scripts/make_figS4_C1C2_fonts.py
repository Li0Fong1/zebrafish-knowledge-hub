# -*- coding: utf-8 -*-
"""Figure S4, panels C1 and C2 — retypeset in Times New Roman / Arial.

The source figure (S4.jpg) is a raster image: its mixed letterforms cannot be
changed in place.  These two panels are therefore re-rendered from the project's
own structured data, with a single font family throughout and the panel geometry
of the source figure preserved (six box plots, individual-answer dots, exact
two-sided permutation-test brackets).

Data (unchanged, read programmatically):
    Figure/_config_sweep_answers.json  -> n_inline_unique per answer
    Figure/_config_sweep_scores.json   -> blind 7-dimension rubric total (0-10)
Only question positions present in BOTH files are used (n = 10 per
configuration).  P values are exact two-sided permutation tests over all
C(20,10) label assignments, with mid-ranks for ties — the same hand-written
statistic used in scripts/make_figS4_stats.py.

No caption / footnote is drawn inside the image: the C1/C2 legends are delivered
separately as text.

Outputs
    Figure/EN_journal_figures/FigS4_C1C2_TimesNewRoman.png
    Figure/EN_journal_figures/FigS4_C1C2_Arial.png
    output/figS4_c1c2/panel_stats.json
"""
from __future__ import annotations

import itertools
import json
import pathlib
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                          # noqa: E402
import numpy as np                                       # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
ANS = ROOT / "Figure" / "_config_sweep_answers.json"
SCORES = ROOT / "Figure" / "_config_sweep_scores.json"
OUT_FIG = ROOT / "Figure" / "EN_journal_figures"
OUT_WORK = ROOT / "output" / "figS4_c1c2"
OUT_FIG.mkdir(parents=True, exist_ok=True)
OUT_WORK.mkdir(parents=True, exist_ok=True)

CFG = ["rag_partial", "rag_full", "fusion_partial",
       "fusion_full", "hub_partial", "hub_full"]
LABEL = ["RAG partial", "RAG full", "Fusion partial",
         "Fusion full", "Hub partial", "Hub full"]
COLOR = ["#7FB3D5", "#2471A3", "#F0B27A", "#CA6F1E", "#A9DFBF", "#1E8449"]
CMP_SCORE = [("rag_partial", "rag_full"), ("fusion_partial", "fusion_full"),
             ("hub_partial", "hub_full"), ("rag_full", "fusion_full"),
             ("fusion_full", "hub_full"), ("rag_full", "hub_full"),
             ("rag_partial", "hub_partial")]
CMP_CITE = [("rag_partial", "rag_full"), ("hub_partial", "hub_full"),
            ("fusion_full", "hub_full"), ("rag_full", "hub_full")]
INK = "#1B2733"


def _ranks(v):
    """Mid-ranks for ties."""
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def mann_whitney_p(a, b) -> float:
    """Exact two-sided permutation test over all C(n1+n2, n1) assignments."""
    n1, n2 = len(a), len(b)
    pooled = list(a) + list(b)
    ranks = _ranks(pooled)
    idx = list(range(n1 + n2))
    u1_obs = sum(ranks[:n1]) - n1 * (n1 + 1) / 2
    m_obs = min(u1_obs, n1 * n2 - u1_obs)
    ge = tot = 0
    for comb in itertools.combinations(idx, n1):
        u1 = sum(ranks[k] for k in comb) - n1 * (n1 + 1) / 2
        if min(u1, n1 * n2 - u1) <= m_obs + 1e-9:
            ge += 1
        tot += 1
    return min(1.0, ge / tot)


def assign_levels(pairs):
    out = []
    for i, j in sorted(pairs, key=lambda t: (t[1] - t[0], t[0])):
        lv = 1
        for a, b, l in out:
            if a <= i and j <= b:
                lv = max(lv, l + 1)
        while any(l == lv and not (j < a or b < i) for a, b, l in out):
            lv += 1
        out.append((i, j, lv))
    return out


def load_data():
    ans = json.loads(ANS.read_text(encoding="utf-8"))
    sc = json.loads(SCORES.read_text(encoding="utf-8"))
    recs = [r for r in ans["records"] if r.get("ok")]
    rows = sc["rows"]
    qids = sorted({r["qid"] for r in rows})
    score = {c: [] for c in CFG}
    cite = {c: [] for c in CFG}
    for q in qids:
        for c in CFG:
            a = [r for r in recs if r["qid"] == q and r["config"] == c]
            s = [r for r in rows if r["qid"] == q and r["config"] == c]
            if not a or not s:
                continue
            cite[c].append(a[0].get("n_inline_unique", 0))
            score[c].append(s[0]["total"])
    assert all(len(score[c]) == len(cite[c]) == 10 for c in CFG)
    return score, cite, qids


def describe(v):
    a = np.asarray(v, dtype=float)
    q1, med, q3 = np.percentile(a, [25, 50, 75])
    return dict(n=len(v), min=float(a.min()), q1=float(q1), median=float(med),
                q3=float(q3), max=float(a.max()), mean=round(float(a.mean()), 3))


def draw_panel(ax, data, title, ylab, comparisons, ymax, yticks,
               base, step, panel_tag, pvals):
    pos = list(range(1, len(CFG) + 1))
    bp = ax.boxplot([data[c] for c in CFG], positions=pos, widths=0.56,
                    patch_artist=True, showfliers=False,
                    medianprops=dict(color="#1B1F24", lw=1.6),
                    whiskerprops=dict(color="#4A5561", lw=1.1),
                    capprops=dict(color="#4A5561", lw=1.1),
                    boxprops=dict(lw=1.2, edgecolor="#3B4654"))
    for patch, col in zip(bp["boxes"], COLOR):
        patch.set_facecolor(col)
        patch.set_alpha(0.88)
    rng = np.random.default_rng(7)
    for x, c in zip(pos, CFG):
        v = data[c]
        ax.scatter(x + rng.uniform(-0.13, 0.13, len(v)), v, s=16,
                   color="#2C3E50", alpha=0.42, linewidths=0, zorder=3)
    ax.set_xticks(pos)
    ax.set_xticklabels(LABEL, rotation=42, ha="right", rotation_mode="anchor",
                       fontsize=15, color=INK)
    ax.set_ylabel(ylab, fontsize=18, color=INK, labelpad=8)
    ax.tick_params(axis="y", labelsize=15, colors=INK, length=5, width=1.1)
    ax.tick_params(axis="x", length=0)
    ax.set_yticks(yticks)
    ax.set_ylim(0, ymax)
    ax.set_xlim(0.45, len(CFG) + 0.55)
    ax.grid(axis="y", color="#E3E8EF", lw=0.9, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#8A94A6")
        ax.spines[s].set_linewidth(1.1)

    idx = {c: i + 1 for i, c in enumerate(CFG)}
    pairs = [(idx[a], idx[b]) for a, b in comparisons]
    levels = assign_levels(pairs)
    for i, j, lv in sorted(levels, key=lambda t: t[2]):
        a, b = CFG[i - 1], CFG[j - 1]
        p = mann_whitney_p(data[a], data[b])
        pvals[f"{a} vs {b}"] = round(p, 4)
        y = base + (lv - 1) * step
        ax.plot([i, i, j, j], [y, y + step * 0.17, y + step * 0.17, y],
                color="#3B4654", lw=1.15, clip_on=False, zorder=4)
        ax.text((i + j) / 2, y + step * 0.23, f"P = {p:.3f}", ha="center",
                va="bottom", fontsize=13.5, color=INK, clip_on=False)
    if title:  # 空标题时不绘制，避免留下空白行影响版面
        ax.set_title(title, fontsize=22, fontweight="bold", color=INK, pad=16)
    ax.text(-0.115, 1.115, panel_tag, transform=ax.transAxes, fontsize=27,
            fontweight="bold", color=INK, ha="left", va="bottom", clip_on=False)


def render(font_name, score, cite, qids, stats):
    if font_name == "Times New Roman":
        plt.rcParams["font.family"] = "serif"
        plt.rcParams["font.serif"] = ["Times New Roman"]
        plt.rcParams["mathtext.fontset"] = "stix"
    else:
        plt.rcParams["font.family"] = "sans-serif"
        plt.rcParams["font.sans-serif"] = ["Arial"]
        plt.rcParams["mathtext.fontset"] = "dejavusans"
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(1, 2, figsize=(14.4, 6.1), dpi=300)
    fig.subplots_adjust(left=0.075, right=0.985, bottom=0.235, top=0.87, wspace=0.30)
    p1, p2 = {}, {}
    # 2026-11-25：按用户要求去掉顶部居中的面板标题（Rubric score /
    # Unique inline citations），只保留左上角的 C1 / C2 标签。
    # set_title 传空串不会再绘制标题，同时保留原 pad 让坐标轴位置不变。
    draw_panel(axes[0], score, "", "Score", CMP_SCORE,
               14.4, [0, 2, 4, 6, 8, 10], 10.45, 0.86, "C1", p1)
    draw_panel(axes[1], cite, "", "Unique inline citations",
               CMP_CITE, 31, [0, 5, 10, 15, 20, 25], 25.6, 1.35, "C2", p2)
    safe = font_name.replace(" ", "")
    dst = OUT_FIG / f"FigS4_C1C2_{safe}.png"
    fig.savefig(dst, dpi=300, facecolor="white", bbox_inches="tight", pad_inches=0.22)
    plt.close(fig)
    stats["renders"][font_name] = dict(file=str(dst.relative_to(ROOT)).replace("\\", "/"),
                                       p_c1=p1, p_c2=p2)
    return dst


def main() -> int:
    score, cite, qids = load_data()
    stats = dict(
        source=dict(answers="Figure/_config_sweep_answers.json",
                    scores="Figure/_config_sweep_scores.json",
                    question_positions=qids, n_per_configuration=10),
        statistic="exact two-sided permutation test, mid-ranks for ties, "
                  "all C(20,10) label assignments enumerated",
        box="median and interquartile range; whiskers 1.5xIQR; dots = individual answers",
        panels=dict(
            C1=dict(measure="blind 7-dimension rubric total (0-10)",
                    groups={c: describe(score[c]) for c in CFG}),
            C2=dict(measure="distinct inline [n] citation markers per answer",
                    groups={c: describe(cite[c]) for c in CFG})),
        renders={})
    for fam in ("Times New Roman", "Arial"):
        p = render(fam, score, cite, qids, stats)
        print("saved", p, p.stat().st_size, "bytes")
    (OUT_WORK / "panel_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print("C1 medians", {c: statistics.median(score[c]) for c in CFG})
    print("C2 medians", {c: statistics.median(cite[c]) for c in CFG})
    print("C2 P", stats["renders"]["Times New Roman"]["p_c2"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
