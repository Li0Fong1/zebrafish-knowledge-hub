# -*- coding: utf-8 -*-
"""FigS4 (C)：不同 Hub 配置下生成结果的对比（箱线图 + Mann–Whitney U 显著横括线）

版式对齐参考图 Fig. S4(C)。两个面板：
    (C1) 项目自带的 7 维量表得分（0–10）——盲评
    (C2) 每条答案的行内引用去重数（客观量，非评分）
两面板用同一批题、同一批答案，横轴为 6 个真实配置（作答路线 × 作答深度）。

数据来源（全部真实）：
    Figure/_config_sweep_answers.json  ← scripts/collect_config_sweep.py（真实调 Hub /api/chat）
    Figure/_config_sweep_scores.json   ← scripts/score_config_sweep.py（盲评，配置名对评委不可见）
统计：手写双尾 Mann–Whitney U（含并列校正，正态近似），不依赖 scipy；
      另打印配对 Wilcoxon 作为交叉核对。

★ 防坑：_sanity_artists() 扫异常放大图形；_raster_ink_check() 在 PNG 上验证文字真有墨迹。

输出：Figure/supplementary/FigS4_Stats_EN.png
用法：python scripts\\make_figS4_stats.py
"""
from __future__ import annotations

import itertools
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                              # noqa: E402
from make_figures_pipeline_compare import C, rec, REC   # noqa: E402

ANS = ROOT / "Figure" / "_config_sweep_answers.json"
SCORES = ROOT / "Figure" / "_config_sweep_scores.json"
SUMMARY = ROOT / "Figure" / "_figs4c_summary.json"      # 供独立审计反解几何/数值
OUT = FC.FIG_DIR / "supplementary"
OUT.mkdir(parents=True, exist_ok=True)

TEXT_BOXES: list = []
CFG = ["rag_partial", "rag_full", "fusion_partial", "fusion_full", "hub_partial", "hub_full"]
LABEL = {"rag_partial": "rag\npartial", "rag_full": "rag\nfull",
         "fusion_partial": "fusion\npartial", "fusion_full": "fusion\nfull",
         "hub_partial": "hub\npartial", "hub_full": "hub\nfull"}
COLOR = {"rag_partial": "#7FB3D5", "rag_full": "#2471A3",
         "fusion_partial": "#F0B27A", "fusion_full": "#CA6F1E",
         "hub_partial": "#A9DFBF", "hub_full": "#1E8449"}
CMP_SCORE = [("rag_partial", "rag_full"), ("fusion_partial", "fusion_full"),
             ("hub_partial", "hub_full"), ("rag_full", "fusion_full"),
             ("fusion_full", "hub_full"), ("rag_full", "hub_full"),
             ("rag_partial", "hub_partial")]
CMP_CITE = [("rag_partial", "rag_full"), ("hub_partial", "hub_full"),
            ("fusion_full", "hub_full"), ("rag_full", "hub_full")]


# ── 统计：用**精确置换检验**（n=10 且并列多，正态近似不可靠）────────────
def _ranks(v):
    """平均秩（并列取均值）。"""
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
    """双尾 Mann–Whitney：枚举全部标签分配（C(n, n1)，n=20 时 184756 种），
    并列用平均秩；返回 P(min U ≤ 观测 min U)。"""
    n1, n2 = len(a), len(b)
    if not n1 or not n2:
        return float("nan")
    pooled = list(a) + list(b)
    ranks = _ranks(pooled)
    idx = list(range(n1 + n2))
    u1_obs = sum(ranks[:n1]) - n1 * (n1 + 1) / 2
    m_obs = min(u1_obs, n1 * n2 - u1_obs)
    ge = 0
    tot = 0
    for comb in itertools.combinations(idx, n1):
        s = 0.0
        for k in comb:
            s += ranks[k]
        u1 = s - n1 * (n1 + 1) / 2
        if min(u1, n1 * n2 - u1) <= m_obs + 1e-9:
            ge += 1
        tot += 1
    return min(1.0, ge / tot)


def wilcoxon_p(a, b) -> float:
    """配对 Wilcoxon：符号翻转 2^n 全枚举（n≤14 精确）。"""
    d = [x - y for x, y in zip(a, b) if x != y]
    n = len(d)
    if n < 5 or n > 16:
        return float("nan")
    ar = _ranks([abs(x) for x in d])
    total = sum(ar)
    obs = sum(r for r, v in zip(ar, d) if v > 0)
    m_obs = min(obs, total - obs)
    ge = 0
    for bits in itertools.product((0, 1), repeat=n):
        w = sum(r for r, bit in zip(ar, bits) if bit)
        if min(w, total - w) <= m_obs + 1e-9:
            ge += 1
    return min(1.0, ge / (2 ** n))


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


# ── 两道防线 ─────────────────────────────────────────────────────────────
def _render(fig):
    fig.set_dpi(300)
    cv = FigureCanvasAgg(fig)
    cv.draw()
    return cv, cv.get_renderer()


def _sanity_artists(fig, r):
    pw, ph = fig.get_size_inches() * fig.dpi
    bad = []
    for ax in fig.axes:
        for a in list(ax.patches) + list(ax.lines) + list(ax.collections):
            try:
                b = a.get_window_extent(renderer=r)
            except Exception:                                 # noqa: BLE001
                continue
            if b.width > 0.75 * pw or b.height > 0.90 * ph:
                bad.append(f"{type(a).__name__} {b.width:.0f}x{b.height:.0f}px")
    return bad


def _text_axes_overlap(fig, r):
    """图级文字（标题/脚注）与坐标轴区域的重叠面积；面板内的 P 值标注本就应在轴内，不计。"""
    bad = []
    for name, t in TEXT_BOXES:
        if name not in ("suptitle", "note1", "note2"):
            continue
        b = t.get_window_extent(renderer=r)
        for k, ax in enumerate(fig.axes):
            ab = ax.get_window_extent(renderer=r)
            ix = max(0.0, min(b.x1, ab.x1) - max(b.x0, ab.x0))
            iy = max(0.0, min(b.y1, ab.y1) - max(b.y0, ab.y0))
            if ix > 1 and iy > 1:
                bad.append(f"{name}×axes{k + 1} {ix:.0f}x{iy:.0f}px")
    return bad


def _raster_ink_check(fig, r, path):
    tb = fig.get_tightbbox(r)
    pad, dpi = 0.1, fig.dpi
    PW, PH = round((tb.width + 2 * pad) * dpi), round((tb.height + 2 * pad) * dpi)
    img = plt.imread(path)
    sx, sy = img.shape[1] / max(1, PW), img.shape[0] / max(1, PH)
    PW, PH = img.shape[1], img.shape[0]
    miss = []
    for name, t in TEXT_BOXES:
        if not (t.get_text() or "").strip():
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
        if float((patch.max(axis=2) < 0.65).mean()) < 0.02:
            miss.append(name + " 无墨迹")
    return miss


def _stats(v):
    """与 matplotlib 箱线图一致的分位数（linear 插值）。"""
    a = np.asarray(v, dtype=float)
    q1, med, q3 = np.percentile(a, [25, 50, 75])
    iqr = q3 - q1
    lo = a[a >= q1 - 1.5 * iqr]
    hi = a[a <= q3 + 1.5 * iqr]
    return {"n": len(v), "min": float(a.min()), "q1": float(q1), "median": float(med),
            "q3": float(q3), "max": float(a.max()),
            "whisk_lo": float(lo.min()), "whisk_hi": float(hi.max()), "mean": float(a.mean())}


# ── 单个面板 ─────────────────────────────────────────────────────────────
def panel(ax, data, pdf, title, ylab, comparisons, ymin, ymax_pad, ymax, geom=None,
          panel_key=""):
    pos = list(range(1, len(CFG) + 1))
    bp = ax.boxplot([data[c] for c in CFG], positions=pos, widths=0.56, patch_artist=True,
                    showfliers=False, medianprops=dict(color="#1B1F24", lw=1.4),
                    whiskerprops=dict(color="#5A6474", lw=1.0),
                    capprops=dict(color="#5A6474", lw=1.0), boxprops=dict(lw=1.1))
    for patch, c in zip(bp["boxes"], CFG):
        patch.set_facecolor(COLOR[c]); patch.set_alpha(0.85)
        patch.set_edgecolor("#3B4654")
    rng = np.random.default_rng(7)
    for x, c in zip(pos, CFG):
        v = data[c]
        ax.scatter(x + rng.uniform(-0.13, 0.13, len(v)), v, s=7, color="#2C3E50",
                   alpha=0.35, linewidths=0, zorder=3)
    ax.set_xticks(pos)
    ax.set_xticklabels([LABEL[c].replace("\n", " ") + f"\n(n={len(data[c])})" for c in CFG],
                       fontsize=7.6, color="#3B4654")
    ax.set_ylabel(ylab, fontsize=8.6, color="#3B4654")
    ax.tick_params(axis="y", labelsize=8.0, colors="#3B4654")
    ax.grid(axis="y", color="#E3E8EF", lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#8A94A6")

    idx = {c: i + 1 for i, c in enumerate(CFG)}
    pairs = [(idx[a], idx[b]) for a, b in comparisons]
    levels = assign_levels(pairs)
    span = ymax - ymin
    step = span * 0.085
    base = max(max(data[c]) for c in CFG) + span * 0.03
    for i, j, lv in sorted(levels, key=lambda t: t[2]):
        a, b = CFG[i - 1], CFG[j - 1]
        p = mann_whitney_p(data[a], data[b])
        pdf[(panel_key, a, b)] = p
        y = base + (lv - 1) * step
        ax.plot([i, i, j, j], [y, y + step * 0.16, y + step * 0.16, y],
                color="#3B4654", lw=1.0, clip_on=False, zorder=4)
        t = ax.text((i + j) / 2, y + step * 0.22, f"P = {p:.3f}", ha="center", va="bottom",
                    fontsize=7.4, color=C["ink"])
        TEXT_BOXES.append((f"{title}-P{i}{j}", t))
    ax.set_ylim(ymin, base + max(lv for _i, _j, lv in levels) * step + ymax_pad)
    ax.set_xlim(0.45, len(CFG) + 0.55)
    t = ax.set_title(title, fontsize=10.6, color=C["ink"], fontweight="bold", pad=8)
    TEXT_BOXES.append((title, t))

    if geom is not None:                       # 几何/数值快照（供审计反解）
        ax.figure.canvas.draw()
        geom[panel_key] = {
            "ylim": [float(ax.get_ylim()[0]), float(ax.get_ylim()[1])],
            "xlim": [float(ax.get_xlim()[0]), float(ax.get_xlim()[1])],
            "box_width": 0.56,
            "stats": {c: _stats(data[c]) for c in CFG},
            "positions": {c: i + 1 for i, c in enumerate(CFG)},
            "p_values": {f"{CFG[i-1]}|{CFG[j-1]}": pdf[(panel_key, CFG[i - 1], CFG[j - 1])]
                         for i, j, _lv in levels},
            "pairs": [[CFG[i - 1], CFG[j - 1], lv] for i, j, lv in levels],
            "colors": {c: COLOR[c] for c in CFG},
        }


def main() -> int:
    t0 = time.time()
    if not (ANS.is_file() and SCORES.is_file()):
        print("[figS4-C] 缺数据文件，先跑 collect_config_sweep.py + score_config_sweep.py")
        return 2
    ans = json.loads(ANS.read_text(encoding="utf-8"))
    sc = json.loads(SCORES.read_text(encoding="utf-8"))
    recs = [r for r in ans["records"] if r.get("ok")]
    rows = sc["rows"]
    maxtotal = float(sc.get("max_total") or 10)
    skipped = sc.get("skipped_qids") or []

    # 只用「两道数据都齐」的题，保证两面板同一批样本
    have_q = sorted({r["qid"] for r in rows})
    cite = {c: [] for c in CFG}
    score = {c: [] for c in CFG}
    for q in have_q:
        for c in CFG:
            a = [r for r in recs if r["qid"] == q and r["config"] == c]
            s = [r for r in rows if r["qid"] == q and r["config"] == c]
            if not a or not s:
                continue
            cite[c].append(a[0].get("n_inline_unique", 0))
            score[c].append(s[0]["total"])
    n_per = {c: len(cite[c]) for c in CFG}
    rec("C-n", "两面板样本量一致（同一批题，逐配置）",
        "ok" if len(set(n_per.values())) == 1 and n_per[CFG[0]] >= 8 else "mismatch",
        f"n={n_per}", "high")
    rec("C-rubric", "量表满分与项目一致（10）", "ok" if abs(maxtotal - 10) < 1e-6 else "mismatch",
        f"max_total={maxtotal}", "high")
    rec("C-judge", "评委未出现整题作废", "ok" if not skipped else "mismatch",
        f"skipped={skipped}", "high")

    W_IN, H_IN = 13.2, 6.2
    fig = plt.figure(figsize=(W_IN, H_IN))
    ax1 = fig.add_axes([0.055, 0.315, 0.415, 0.52])
    ax2 = fig.add_axes([0.545, 0.315, 0.415, 0.52])
    pdf = {}
    geom = {"figsize_in": [W_IN, H_IN], "dpi": 300,
            "configs": CFG, "n_per": n_per, "judge_model": sc.get("model")}
    panel(ax1, score, pdf, "(C1)  Rubric score per answer (7-dimension rubric, 0–10)",
          "Score", CMP_SCORE, 0, 0.8, maxtotal, geom, "C1")
    cmax = max(max(v) for v in cite.values()) or 10
    panel(ax2, cite, pdf, "(C2)  Inline citations per answer (unique [n])",
          "Unique inline citations", CMP_CITE, 0, 0.7, max(cmax, 6), geom, "C2")
    # 数据空间→图形的两个锚点（单位为英寸，与画布 dpi 无关），供审计把数据值反解到 PNG 行
    geom["transforms"] = {}
    for key, ax in (("C1", ax1), ("C2", ax2)):
        cap_dpi = float(fig.dpi)                     # 捕获时画布 dpi（可能仍是默认 100）
        y0, y1 = ax.get_ylim()
        bb = ax.get_position()
        p0 = ax.transData.transform((1.0, y0))
        p1 = ax.transData.transform((1.0, y1))
        px = {c: float(ax.transData.transform((i + 1, y0))[0])
              for i, c in enumerate(CFG)}
        geom["transforms"][key] = {
            "capture_dpi": cap_dpi,
            "disp_y_at_ymin_in": float(p0[1] / cap_dpi),
            "disp_y_at_ymax_in": float(p1[1] / cap_dpi),
            "disp_x_centers_in": {c: float(v / cap_dpi) for c, v in px.items()},
            "axes_rect_frac": [float(bb.x0), float(bb.y0), float(bb.width), float(bb.height)],
            "axes_left_in": float(bb.x0 * W_IN),
            "axes_bottom_in": float(bb.y0 * H_IN),
        }

    t = fig.text(0.055, 0.975, "Six real, request-level Hub configurations — same 10 fixed questions each",
                 fontsize=11.6, color=C["ink"], fontweight="bold", va="top")
    TEXT_BOXES.append(("suptitle", t))
    t = fig.text(0.055, 0.245,
                 f"Configurations = answering route (rag / fusion / hub) × answer depth (partial / full), requested from the production Hub API.\n"
                 f"The knowledge index is identical in all six runs (paragraph sections, 360 / 110 characters, 48-character overlap), so the reference\n"
                 f"figure's segmentation axis cannot be varied here and is not claimed.  n = {n_per[CFG[0]]} questions per configuration, paired across\n"
                 f"configurations; boxes show median and interquartile range, whiskers 1.5×IQR, dots are individual answers.",
                 fontsize=7.3, color=C["gray"], va="top")
    TEXT_BOXES.append(("note1", t))
    # 多重比较说明里的数字全部由数据算出后注入图注，避免图注与核查分叉
    c2_p = {f"{a}|{b}": mann_whitney_p(cite[a], cite[b]) for a, b in CMP_CITE}
    c1_p = {f"{a}|{b}": mann_whitney_p(score[a], score[b]) for a, b in CMP_SCORE}
    srt = sorted(c2_p.values())
    holm_c2 = {p: min(1.0, p * (len(srt) - i)) for i, p in enumerate(srt)}
    allp = sorted(list(c2_p.values()) + list(c1_p.values()))
    holm_all = {p: min(1.0, p * (len(allp) - i)) for i, p in enumerate(allp)}
    sig2 = sorted(p for p in c2_p.values() if p < 0.05)
    p1, p2 = sig2[0], sig2[1]
    t = fig.text(0.055, 0.150,
                 "C1: scored blind to configuration with the project's own rubric (0–10, 0.25 steps) by an external model; differences are small and not significant, i.e. the rubric saturates,\n"
                 "and the configuration-level ranking is not reproducible between two independent judges (Spearman −0.03).  C2: an objective count from the same answers — hub answers carry\n"
                 "2.5–3.7× more distinct inline citation markers than rag/fusion, and every hub_full answer backs its markers with an in-text numbered list (hub_partial 8/10, rag/fusion 4–6/10),\n"
                 "so the hub counts are verifiable end-to-end; for the remaining rag/fusion answers the reference list is absent from the returned text.  P values are exact two-sided permutation\n"
                 "tests (Mann–Whitney over all C(20,10) label assignments; paired Wilcoxon by full 2^10 sign-flip enumeration), hand-written without scipy.  Multiplicity: the two significant C2\n"
                 f"comparisons are P = {p1:.3f} / {p2:.3f}; Holm-corrected within the four C2 tests they become {holm_c2[p1]:.3f} / {holm_c2[p2]:.3f}, and pooled across all eleven tests of both panels\n"
                 f"{holm_all[p1]:.2f} / {holm_all[p2]:.2f} — i.e. suggestive, not family-wise significant, at n = {n_per[CFG[0]]} per configuration.",
                 fontsize=7.3, color=C["gray"], va="top")
    TEXT_BOXES.append(("note2", t))

    cv, r = _render(fig)
    big = _sanity_artists(fig, r)
    rec("C-artist-size", "无异常放大图形", "ok" if not big else "mismatch",
        "; ".join(big[:3]) or "all fine", "high")
    ov = _text_axes_overlap(fig, r)
    rec("C-overlap", "标题/脚注与面板不重叠", "ok" if not ov else "mismatch",
        "; ".join(ov[:3]) or "all fine", "high")
    p = OUT / "FigS4_Stats_EN.png"
    fig.savefig(p, dpi=300, facecolor="white", bbox_inches="tight")
    tb = fig.get_tightbbox(r)
    geom["tight_bbox_in"] = [float(tb.x0), float(tb.y0), float(tb.width), float(tb.height)]
    geom["pad_inches"] = 0.1
    SUMMARY.write_text(json.dumps(geom, ensure_ascii=False, indent=1), encoding="utf-8")
    miss = _raster_ink_check(fig, r, p)
    rec("C-ink", "标题/脚注/P 值在 PNG 上可见", "ok" if not miss else "mismatch",
        f"texts={len(TEXT_BOXES)} missing={len(miss)}"
        + (" | " + "; ".join(miss[:4]) if miss else ""), "high")
    plt.close(fig)

    print(f"[figS4-C] {time.time() - t0:.1f}s -> {p}")
    for c in CFG:
        print(f"  {c:<15} n={n_per[c]:2d}  score mean={np.mean(score[c]):5.2f} "
              f"| cites mean={np.mean(cite[c]):5.1f}")
    for key, _dat, _cmps in (("C1", score, CMP_SCORE), ("C2", cite, CMP_CITE)):
        print(f"\n  {key} P (Mann–Whitney):")
        for (kk, a, b), pv in sorted(((k, v) for k, v in pdf.items() if k[0] == key),
                                     key=lambda kv: kv[1]):
            print(f"    {a:<15} vs {b:<15} = {pv:.4f}")
    print("\n  配对 Wilcoxon（同一批题配对，交叉核对）:")
    for a, b in CMP_SCORE:
        print(f"    score {a:<15} vs {b:<15} = {wilcoxon_p(score[a], score[b]):.4f}")
    for a, b in CMP_CITE:
        print(f"    cites {a:<15} vs {b:<15} = {wilcoxon_p(cite[a], cite[b]):.4f}")
    errs = [x for x in REC if x["status"] == "mismatch"]
    print(f"[figS4-C] checks={len(REC)} mismatch={len(errs)}")
    for x in errs:
        print("  [X]", x["id"], "::", x["detail"])
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
