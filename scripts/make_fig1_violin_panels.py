# -*- coding: utf-8 -*-
"""
Figure 1 panels C-E: real-data violin plots (code-rendered, reproducible).

Run:
    .venv/Scripts/python.exe scripts/make_fig1_violin_panels.py

SOURCE OF DATA (the only source; nothing transcribed from images):
    output/采集包/compare_metrics.json
      -> per_question[1..49].sys[S0_Hub|S1_Hy4|S2_Qwen|S3_Doubao|S4_Gemini]

Each violin is the observed per-question distribution of ONE system on the
same benchmark.  Two exclusions are applied, both taken from the data itself:

  * The Hub reused ONE answer at 10 repeat positions (reused == True:
    20/21/23/26/27/28/31/38/39/40).  These are not independent samples
    (see output/采集包/五系统横向对比.md, section 0), so for a paired
    comparison those 10 positions are dropped for EVERY system -> 39
    unique prompts.
  * Qwen question 29 is {"empty": True} (no answer) -> excluded, never
    counted as a zero.

Therefore n = 39 for Hub / Hunyuan / Doubao / Gemini, and n = 38 for Qwen.
Use --include-reused to reproduce the 49-position view for comparison only.

Panels (objective answer features, NOT quality scores):
    C  chars       characters per answer     y 0-4000
    D  lines       lines per answer          y 0-60
    E  ids_total   database IDs per answer   y 0-8

Shapes are drawn by matplotlib's standard violinplot (Gaussian KDE, Scott
bandwidth).  The horizontal bar inside each violin is the observed median;
the vertical bar spans observed Q1-Q3.

Outputs (--out-dir, default output/fig1_violin/):
    violin_C.png / violin_D.png / violin_E.png   transparent, no text, 300 dpi
    violin_stats.json                            full reproducible summary
"""
import argparse
import json
import math
import pathlib
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "output" / "采集包" / "compare_metrics.json"

SYS = ["S0_Hub", "S1_Hy4", "S2_Qwen", "S3_Doubao", "S4_Gemini"]
PANELS = {
    "C": {"metric": "chars", "ymax": 4000.0, "ticks": [4000, 2000, 0]},
    "D": {"metric": "lines", "ymax": 60.0, "ticks": [60, 30, 0]},
    "E": {"metric": "ids_total", "ymax": 8.0, "ticks": [8, 4, 0]},
}

# geometry in the local 409 x 450 frame used on the canvas
CENTRES = [93.0, 155.0, 217.0, 279.0, 341.0]
VIOLIN_W = 46.0
Y_TOP, Y_BOTTOM = 10.0, 440.0
GRID, FILL, EDGE, INK = "#EDF2F7", "#A8CDE0", "#5E9DBC", "#1B2733"


def values_for(metric, include_reused=False):
    """Read raw per-question values straight from the structured JSON."""
    data = json.loads(SRC.read_text(encoding="utf-8"))
    pq = data["per_question"]
    reused_positions = {q for q, rec in pq.items()
                        if (rec["sys"].get("S0_Hub") or {}).get("reused")}
    out = {s: [] for s in SYS}
    for qid in sorted(pq, key=int):
        if not include_reused and qid in reused_positions:
            continue
        for s in SYS:
            cell = pq[qid]["sys"].get(s)
            # empty == no answer produced; exclude rather than treat as zero
            if cell is None or cell.get("empty"):
                continue
            if metric == "ids_total":
                ids = cell.get("ids")
                if ids is None:
                    continue
                v = sum(float(x) for x in ids.values()
                        if isinstance(x, (int, float)) and not isinstance(x, bool))
            else:
                raw = cell.get(metric)
                if isinstance(raw, bool):
                    v = 1.0 if raw else 0.0
                elif isinstance(raw, (int, float)):
                    v = float(raw)
                else:
                    continue
            out[s].append(v)
    return out


def y_local(value, ymax):
    return Y_BOTTOM - (value / ymax) * (Y_BOTTOM - Y_TOP)


def bandwidth(vals):
    """Silverman's rule of thumb; fully deterministic."""
    n = len(vals)
    sd = statistics.pstdev(vals) if n > 1 else 0.0
    sv = sorted(vals)
    iqr = sv[int(0.75 * (n - 1))] - sv[int(0.25 * (n - 1))]
    sigma = min(x for x in (sd, iqr / 1.34) if x > 0) if (sd > 0 or iqr > 0) else 1.0
    h = 0.9 * sigma * (n ** (-0.2))
    return h if h > 1e-9 else 1e-9


def kde_outline(vals, ymax, steps=100):
    """Gaussian KDE evaluated ONLY on [min, max] (cut=0). Endpoints -> 0."""
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        grid = [lo - 0.25 * (ymax / 100.0), lo, lo + 0.25 * (ymax / 100.0)]
        dens = [0.0, 1.0, 0.0]
    else:
        grid = [lo + (hi - lo) * i / steps for i in range(steps + 1)]
        h = bandwidth(vals)
        n = len(vals)
        norm = 1.0 / (n * h * math.sqrt(2 * math.pi))
        dens = []
        for g in grid:
            acc = 0.0
            for v in vals:
                z = (g - v) / h
                if -6.0 < z < 6.0:
                    acc += math.exp(-0.5 * z * z)
            dens.append(acc * norm)
        peak = max(dens) or 1.0
        dens = [d / peak for d in dens]
        dens[0], dens[-1] = 0.0, 0.0          # cut=0: no density outside the data
    return [y_local(g, ymax) for g in grid], dens


def render(panel, vals_by_sys, path):
    cfg = PANELS[panel]
    ymax = cfg["ymax"]
    fig = plt.figure(figsize=(4.09, 4.50), dpi=300)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 409)
    ax.set_ylim(450, 0)                     # y grows downward, like the canvas
    ax.axis("off")

    ax.plot([55, 385], [225, 225], color=GRID, lw=1.5, solid_capstyle="butt")
    ax.plot([55, 385], [10, 10], color=GRID, lw=1.5, solid_capstyle="butt")
    ax.plot([55, 55, 385], [10, 440, 440], color=INK, lw=2, solid_capstyle="butt")
    for yy in (10, 225, 440):
        ax.plot([49, 55], [yy, yy], color=INK, lw=2, solid_capstyle="butt")

    for cx, s in zip(CENTRES, SYS):
        ys, dens = kde_outline(vals_by_sys[s], ymax)
        half = [d * (VIOLIN_W / 2.0) for d in dens]
        ax.fill_betweenx(ys, [cx - w for w in half], [cx + w for w in half],
                         facecolor=FILL, edgecolor=EDGE, linewidth=1.5, zorder=2)

    for cx, s in zip(CENTRES, SYS):
        sv = sorted(vals_by_sys[s])
        n = len(sv)
        q1, q3 = sv[int(0.25 * (n - 1))], sv[int(0.75 * (n - 1))]
        med = statistics.median(sv)
        ax.plot([cx, cx], [y_local(q1, ymax), y_local(q3, ymax)],
                color=EDGE, lw=4, solid_capstyle="round", zorder=3)
        ax.plot([cx - 11, cx + 11], [y_local(med, ymax)] * 2,
                color=INK, lw=2.5, solid_capstyle="butt", zorder=4)

    fig.savefig(path, transparent=True, dpi=300)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(ROOT / "output" / "fig1_violin"))
    ap.add_argument("--include-reused", action="store_true",
                    help="comparison only: keep the 10 Hub repeat positions")
    args = ap.parse_args()
    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    stats = {"source": str(SRC.relative_to(ROOT)).replace("\\", "/"),
             "generator": "scripts/make_fig1_violin_panels.py",
             "scope": ("49 question positions INCLUDING 10 Hub repeats"
                       if args.include_reused else
                       "39 unique prompts; 10 Hub repeat positions excluded for all systems"),
             "panels": {}}
    for panel in ("C", "D", "E"):
        cfg = PANELS[panel]
        vals = values_for(cfg["metric"], args.include_reused)
        png = out / ("violin_%s.png" % panel)
        render(panel, vals, png)
        stats["panels"][panel] = {"metric": cfg["metric"], "ymax": cfg["ymax"],
                                  "ticks": cfg["ticks"], "systems": {}}
        for s in SYS:
            v = sorted(vals[s])
            n = len(v)
            stats["panels"][panel]["systems"][s] = {
                "n": n, "min": v[0], "q1": v[int(0.25 * (n - 1))],
                "median": statistics.median(v), "q3": v[int(0.75 * (n - 1))],
                "max": v[-1], "mean": round(statistics.mean(v), 3),
                "zeros": sum(1 for x in v if x == 0),
            }
        print("%s %-10s %s" % (panel, cfg["metric"], " | ".join(
            "%s n=%d med=%g" % (s, stats["panels"][panel]["systems"][s]["n"],
                                stats["panels"][panel]["systems"][s]["median"])
            for s in SYS)))
    (out / "violin_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote:", out)


if __name__ == "__main__":
    main()
