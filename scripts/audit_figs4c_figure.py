# -*- coding: utf-8 -*-
"""FigS4(C) 统计图 —— 独立核查（8 层，逐层给结论）

L1 溯源：每个画出来的数都能回到原始产物
L2 设计：配对完整、两面板同批题
L3 盲评：标签双射、位置无偏、评委回执里不含配置名
L4 统计：精确检验 vs 独立蒙特卡洛、置换不变性、边界样本、多重比较校正
L5 稳健：留一法、换评委、换指标、效应量（中位差 + bootstrap CI）
L6 图上数字与数据一致（像素级反解：箱体位置↔分位数）
L7 可复现：连续两次出图逐字节一致
L8 结论与事实一致：引用编号是否真的对应一份列表；量表饱和是否有直接证据

输出：Figure/_figs4c_audit.json、Figure/FIGS4C_AUDIT.md
用法：python scripts\\audit_figs4c_figure.py
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import make_figS4_stats as FS            # noqa: E402  被审对象（统计与配色）

ANS = ROOT / "Figure" / "_config_sweep_answers.json"
SCA = ROOT / "Figure" / "_config_sweep_scores.json"
SCB = ROOT / "Figure" / "_config_sweep_scores_reasoner.json"
RAWA = ROOT / "logs" / "_config_sweep_judge_raw.jsonl"
SUMM = ROOT / "Figure" / "_figs4c_summary.json"
PNG = ROOT / "Figure" / "supplementary" / "FigS4_Stats_EN.png"
OUTJ = ROOT / "Figure" / "_figs4c_audit.json"
OUTM = ROOT / "Figure" / "FIGS4C_AUDIT.md"

CFG = FS.CFG
DIMS = [("direct_first", 2), ("factor_structure", 2), ("ge_formula", 1),
        ("inline_cite", 2), ("ref_count", 1), ("recency", 1), ("no_review_shell", 1)]
DEMO = re.compile(r"ENSDARG0{12,}|\[演示\]|待补充|TODO")
# 文末编号列表条目：行首 `[n] 正文` 或 `n. 正文`（两种实际出现的格式）
ENTRY = re.compile(r"(?:^|\n)\s*(?:\[(\d{1,2})\]|(\d{1,2})[.、)])\s*([^\n]{20,})")


def _ref_like(s: str) -> bool:
    return bool(re.search(r"\b(19|20)\d{2}\b", s) or re.search(r"doi|https?://", s, re.I)
                or len(re.findall(r"[A-Za-z]{3,}", s)) >= 6)


def parse_ref_entries(a: str):
    """解析答案里的文献条目 → [(编号或 0, 正文)]。
    两种实际出现的格式：
      (a) 带编号：行首 `[n] 标题` 或 `n. 标题`（hub 及部分 rag/fusion）
      (b) 无编号：`参考文献（由系统生成）：A,et al...DOI:...；B,et al...` 分号分隔（部分 rag/fusion）
    """
    out, seen = [], set()
    for m in ENTRY.finditer(a):
        body = m.group(3)
        if _ref_like(body):
            n = int(m.group(1) or m.group(2))
            if (n, body[:40]) not in seen:
                seen.add((n, body[:40]))
                out.append((n, body))
    hdr = None
    for mm in re.finditer(r"(?:参考文献|References?)\s*[（(][^）)]{0,12}[）)]\s*[：:]|(?:参考文献|References?)\s*[：:]", a, re.I):
        hdr = mm
    if hdr:
        tail = a[hdr.end(): hdr.end() + 4000]
        for seg in re.split(r"[；;]|\n+", tail):
            s = seg.strip().strip("。.")
            if len(s) >= 25 and _ref_like(s):
                if (0, s[:40]) not in seen:
                    seen.add((0, s[:40]))
                    out.append((0, s))
    return out

res: list = []


def rec(cid, layer, desc, status, detail=""):
    res.append({"id": cid, "layer": layer, "desc": desc, "status": status, "detail": detail})
    tag = {"ok": "ok ", "warn": "warn", "bad": "BAD"}[status]
    print(f"  [{tag}] {cid} {desc} {detail}" if detail else f"  [{tag}] {cid} {desc}")
    return status == "ok"


# ══════════════════════════════════════════════════════════════════════════
def main() -> int:
    ans = json.loads(ANS.read_text(encoding="utf-8"))
    sca = json.loads(SCA.read_text(encoding="utf-8"))
    summ = json.loads(SUMM.read_text(encoding="utf-8"))
    recs = [r for r in ans["records"] if r.get("ok")]
    rows = sca["rows"]
    qids = sorted({r["qid"] for r in recs})
    A = {(r["qid"], r["config"]): r for r in rows}

    # ── L1 溯源 ──────────────────────────────────────────────────────────
    print("L1 溯源")
    rec("L1.1", "L1", "答案与评分覆盖一致（10 题 × 6 配置，无重复）",
        "ok" if (len(recs) == 60 and len(rows) == 60
                 and len({(r["qid"], r["config"]) for r in recs}) == 60
                 and len({(r["qid"], r["config"]) for r in rows}) == 60) else "bad",
        f"recs={len(recs)} rows={len(rows)}")

    # 独立重解析评委回执 → 与评分文件逐条比对（纯函数复算）
    raw = [json.loads(l) for l in RAWA.read_text(encoding="utf-8").splitlines() if l.strip()]
    redone = {}
    for r in raw:
        obj = json.loads(r["raw"])
        fixed = {}
        for k, v in obj.items():
            m = re.search(r"[A-F]", str(k).upper())
            if m and isinstance(v, dict):
                fixed.setdefault(m.group(0), v)
        for lab, cfg in r["l2c"].items():
            got = fixed.get(lab) or {}
            dims = {}
            for k, mx in DIMS:
                try:
                    v = round(float(got.get(k, 0)) * 4) / 4.0
                except Exception:                                  # noqa: BLE001
                    v = 0.0
                dims[k] = max(0.0, min(float(mx), v))
            redone[(r["qid"], cfg)] = round(sum(dims.values()) * 4) / 4.0
    diff = [(k, redone.get(k), A[k]["total"]) for k in A if abs(redone.get(k, -99) - A[k]["total"]) > 1e-9]
    rec("L1.2", "L1", "评分文件可由评委回执纯函数复算（独立解析器）",
        "ok" if not diff else "bad", f"不一致 {len(diff)} 条")

    dup = []
    for q in qids:
        hs = [hashlib.sha1(r["answer"].encode()).hexdigest()
              for r in recs if r["qid"] == q]
        if len(set(hs)) != len(hs):
            dup.append(q)
    rec("L1.3", "L1", "同一题 6 份答案文本互不相同（否则对比无意义）",
        "ok" if not dup else "bad", f"重复题 {dup}")

    bad_len = [k for k, r in ((str((x["qid"], x["config"])), x) for x in recs)
               if r["chars"] != len(r["answer"])]
    rec("L1.4", "L1", "记录 chars == len(answer)", "ok" if not bad_len else "bad",
        f"不符 {len(bad_len)}")

    demo = [x for x in recs if DEMO.search(x["answer"])]
    rec("L1.5", "L1", "答案不含演示占位标记（ENSDARG+12个0 / [演示] 等）",
        "ok" if not demo else "bad", f"命中 {len(demo)}")

    bad_cite = []
    for x in recs:
        c = len(set(int(v) for v in re.findall(r"\[(\d{1,2})\]", x["answer"])))
        if c != x.get("n_inline_unique"):
            bad_cite.append((x["qid"], x["config"], x.get("n_inline_unique"), c))
    rec("L1.6", "L1", "行内引用去重数可由答案原文复算", "ok" if not bad_cite else "bad",
        f"不符 {len(bad_cite)}")

    nent, cov1, cov2, backedlist = {}, [], [], {c: 0 for c in CFG}
    for x in recs:
        a = x["answer"]
        inline = sorted({int(v) for v in re.findall(r"\[(\d{1,2})\]", a)})
        ents = parse_ref_entries(a)
        enum = {n for n, _b in ents if n}
        mx = max(enum) if enum else 0
        nent[(x["qid"], x["config"])] = len(ents)
        cov1.append(sum(1 for n in inline if n <= mx) / max(1, len(inline)))
        cov2.append(sum(1 for n in inline if n in enum) / max(1, len(inline)))
        if ents and len(ents) >= (max(inline) if inline else 0):
            backedlist[x["config"]] += 1
    mc1, mc2 = float(np.mean(cov1)), float(np.mean(cov2))
    n_back = sum(backedlist.values())
    rec("L1.7a", "L1", "行内 [n] 有显式编号列表对应（编号覆盖）",
        "ok" if mc1 >= 0.95 else "warn",
        f"覆盖 {mc1:.3f}，精确命中 {mc2:.3f}（部分 rag/fusion 答案的文献列表无编号或缺失，只有顺序）")
    rec("L1.7b", "L1", "凡文末存在可解析文献列表的答案，条目数 ≥ 行内最大编号",
        "ok" if n_back == sum(1 for k in nent if nent[k] > 0) else "bad",
        f"满足 {n_back}/{sum(1 for k in nent if nent[k] > 0)}")
    rec("L1.7c", "L1", "行内标记有文末列表背书的答案数（按配置，供图注披露）", "ok",
        "; ".join(f"{c}: {backedlist[c]}/10" for c in CFG))

    # ── L2 设计 ──────────────────────────────────────────────────────────
    print("L2 设计与配对")
    ok2 = all(sum(1 for r in recs if r["qid"] == q and r["config"] == c) == 1
              for q in qids for c in CFG)
    rec("L2.1", "L2", "每题×每配置恰好一次（配对完整）", "ok" if ok2 else "bad")
    n_per = {c: sum(1 for r in rows if r["config"] == c) for c in CFG}
    rec("L2.2", "L2", "两面板同批题、每配置 n 相同",
        "ok" if len(set(n_per.values())) == 1 else "bad", f"n={n_per}")

    # ── L3 盲评 ──────────────────────────────────────────────────────────
    print("L3 盲评完整性")
    bij = True
    for r in raw:
        _o = list(r["l2c"].keys())
        e = list("ABCDEF")
        random.Random(1000 + r["qid"]).shuffle(e)
        bij = bij and _o == e and len(set(r["l2c"].values())) == 6
    rec("L3.1", "L3", "标签↔配置为双射且与确定性打乱一致", "ok" if bij else "bad")
    pos = {c: {L: 0 for L in "ABCDEF"} for c in CFG}
    for r in raw:
        for L, c in r["l2c"].items():
            pos[c][L] += 1
    worst = max(max(v.values()) for v in pos.values())
    rec("L3.2", "L3", "标签位置分布无明显偏置（任一格 ≤5/10）",
        "ok" if worst <= 5 else "warn", f"最大 {worst}/10")
    leak = [r["qid"] for r in raw
            if re.search(r"\brag\b|\bfusion\b|\bhub\b|partial|full", r["raw"], re.I)]
    rec("L3.3", "L3", "评委回执中不出现配置名（盲性未被破坏）",
        "ok" if not leak else "bad", f"疑似泄漏 {leak}")

    # ── 数据面 ───────────────────────────────────────────────────────────
    cite = {c: [] for c in CFG}
    score = {c: [] for c in CFG}
    cite_total = {c: [] for c in CFG}
    for q in qids:
        for c in CFG:
            a = [r for r in recs if r["qid"] == q and r["config"] == c][0]
            s = A[(q, c)]
            cite[c].append(a.get("n_inline_unique", 0))
            cite_total[c].append(a.get("n_inline_cites", 0))
            score[c].append(s["total"])

    # ── L4 统计 ──────────────────────────────────────────────────────────
    print("L4 统计正确性")
    def mc_mw(a, b, n=20000, seed=99):
        """独立于 FS.mann_whitney_p 的蒙特卡洛置换检验（向量化）"""
        a = np.asarray(a, float)
        b = np.asarray(b, float)
        n1, n2 = len(a), len(b)
        pooled = np.concatenate([a, b])

        def u_stat(g1, g2):
            d = g1[:, :, None] - g2[:, None, :]
            u = (d > 0).sum(-1).sum(-1) + 0.5 * (d == 0).sum(-1).sum(-1)
            return np.minimum(u, n1 * n2 - u)

        u_obs = float(u_stat(a[None, :], b[None, :])[0])
        rng = np.random.default_rng(seed)
        cnt, batch = 0, 500
        for _ in range(n // batch):
            perm = np.array([rng.permutation(pooled) for _ in range(batch)])
            cnt += int((u_stat(perm[:, :n1], perm[:, n1:]) <= u_obs + 1e-9).sum())
        return (cnt + 1) / (n + 1), float(FS.mann_whitney_p(a.tolist(), b.tolist()))
    worst_mc = 0.0
    for tag, dat, cmps in (("C2", cite, FS.CMP_CITE), ("C1", score, FS.CMP_SCORE)):
        for a, b in cmps:
            p_mc, p_ex = mc_mw(dat[a], dat[b])
            worst_mc = max(worst_mc, abs(p_mc - p_ex))
    rec("L4.1", "L4", "精确 MW 与 2 万次独立蒙特卡洛一致（|ΔP|≤0.02）",
        "ok" if worst_mc <= 0.02 else "bad", f"最大Δ={worst_mc:.4f}")

    inv = True
    for a, b in FS.CMP_CITE:
        p1 = FS.mann_whitney_p(cite[a], cite[b])
        p2 = FS.mann_whitney_p(list(reversed(cite[a])), list(reversed(cite[b])))
        inv = inv and abs(p1 - p2) < 1e-12
    rec("L4.2", "L4", "置换不变性：样本顺序不影响 P", "ok" if inv else "bad")

    rec("L4.3", "L4", "边界：全等样本给出 P=1",
        "ok" if abs(FS.mann_whitney_p([5] * 10, [5] * 10) - 1.0) < 1e-12 else "bad",
        f"P={FS.mann_whitney_p([5]*10, [5]*10):.4f}")

    tests = []
    for panel, dat, cmps in (("C1", score, FS.CMP_SCORE), ("C2", cite, FS.CMP_CITE)):
        for a, b in cmps:
            dw = [x - y for x, y in zip(dat[a], dat[b])]
            tests.append({"panel": panel, "pair": f"{a}|{b}",
                          "P_mw": FS.mann_whitney_p(dat[a], dat[b]),
                          "P_wilcoxon": FS.wilcoxon_p(dat[a], dat[b])})
    for fam, key in (("按面板", "panel"), ("合并全部", None)):
        grp = {}
        for t in tests:
            grp.setdefault(t[key] if key else "all", []).append(t)
        for gname, items in grp.items():
            s = sorted(items, key=lambda t: t["P_mw"])
            m = len(s)
            for i, t in enumerate(s):
                t[f"holm_{fam}_{gname}"] = min(1.0, t["P_mw"] * (m - i))
    sig_holm_panel = [t["pair"] for t in tests
                      if t["panel"] == "C2" and t["holm_按面板_C2"] < 0.05]
    sig_holm_all = [t["pair"] for t in tests if t["holm_合并全部_all"] < 0.05]
    rec("L4.4", "L4", "多重比较校正后仍有显著项（Holm，按面板 4 检验）",
        "ok" if sig_holm_panel else "warn",
        f"{sig_holm_panel}；若把两面板 11 个检验合并校正则剩 {sig_holm_all or '无'}")

    # ── L5 稳健性 ────────────────────────────────────────────────────────
    print("L5 稳健性")
    keep_mw, keep_wx = [], []
    for drop in qids:
        idx = [i for i, q in enumerate(qids) if q != drop]
        for a, b in [("rag_full", "hub_full"), ("fusion_full", "hub_full")]:
            xa = [cite[a][i] for i in idx]
            xb = [cite[b][i] for i in idx]
            keep_mw.append(FS.mann_whitney_p(xa, xb) < 0.05)
            keep_wx.append(FS.wilcoxon_p(xa, xb) < 0.05)
    rec("L5.1", "L5", "留一法：删任一题后两个显著对比仍显著",
        "ok" if all(keep_mw) and all(keep_wx) else ("warn" if any(keep_mw) else "bad"),
        f"MW {sum(keep_mw)}/{len(keep_mw)}，Wilcoxon {sum(keep_wx)}/{len(keep_wx)} 仍<0.05")

    if SCB.is_file():
        scb = json.loads(SCB.read_text(encoding="utf-8"))
        B = {(r["qid"], r["config"]): r["total"] for r in scb["rows"]}
        sb = {c: [B[(q, c)] for q in qids] for c in CFG}
        maxp = max(FS.mann_whitney_p(sb[a], sb[b]) for a, b in FS.CMP_SCORE)
        rec("L5.2", "L5", "换评委 B：C1 结论一致（六组差异均不显著）",
            "ok" if maxp > 0.05 else "warn", f"B 的最小 P={min(FS.mann_whitney_p(sb[a], sb[b]) for a, b in FS.CMP_SCORE):.3f}")
        d2 = [FS.mann_whitney_p(cite[a], cite[b]) for a, b in [("rag_full", "hub_full"),
                                                              ("fusion_full", "hub_full")]]
        rec("L5.3", "L5", "C2 用累计引用数替代去重数，结论方向一致",
            "ok" if all(p < 0.05 for p in
                        [FS.mann_whitney_p(cite_total[a], cite_total[b])
                         for a, b in [("rag_full", "hub_full"), ("fusion_full", "hub_full")]])
            else "warn",
            f"去重数 P={['%.3f' % p for p in d2]}")
    else:
        rec("L5.2", "L5", "评委 B 结果缺失", "warn")

    hl = []
    for a, b in [("rag_full", "hub_full"), ("fusion_full", "hub_full")]:
        d = [x - y for x, y in zip(cite[a], cite[b])]
        med = float(np.median(d))
        rng = np.random.default_rng(11)
        boot = [float(np.median(rng.choice(d, len(d), replace=True))) for _ in range(4000)]
        lo, hi = float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))
        hl.append({"pair": f"{a}|{b}", "median_diff": med, "ci95": [lo, hi]})
    rec("L5.4", "L5", "效应量：Hodges–Lehmann 中位差 95% CI 不含 0",
        "ok" if all(h["ci95"][0] > 0 or h["ci95"][1] < 0 for h in hl) else "warn",
        "; ".join(f"{h['pair']}: {h['median_diff']:+.1f} [{h['ci95'][0]:.1f},{h['ci95'][1]:.1f}]"
                  for h in hl))

    # ── L6 图上数字 ↔ 数据（像素级反解）──────────────────────────────────
    print("L6 图上数字与数据一致（像素级）")
    img = np.asarray(Image.open(PNG).convert("RGB")).astype(int)
    H, W, _ = img.shape
    tb = summ["tight_bbox_in"]
    pad, dpi = summ["pad_inches"], summ["dpi"]
    PW = round((tb[2] + 2 * pad) * dpi)
    PH = round((tb[3] + 2 * pad) * dpi)
    sx, sy = W / max(1, PW), H / max(1, PH)
    FW, FH = summ["figsize_in"]

    # 从「坐标轴矩形(figure 分数) + xlim/ylim」独立重建数据→PNG 像素的仿射映射
    def data_to_xy(panel, x, y):
        fr = summ["transforms"][panel]["axes_rect_frac"]
        xl, yl = summ[panel]["xlim"], summ[panel]["ylim"]
        xin = fr[0] * FW + (x - xl[0]) / (xl[1] - xl[0]) * fr[2] * FW
        yin = fr[1] * FH + (y - yl[0]) / (yl[1] - yl[0]) * fr[3] * FH
        return ((xin - tb[0] + pad) * dpi * sx, H - (yin - tb[1] + pad) * dpi * sy)

    # 自洽校验：仿射映射 vs 出图时记录的 transData 锚点（英寸）
    anchor_dev = 0.0
    for panel in ("C1", "C2"):
        tr = summ["transforms"][panel]
        y0, y1 = summ[panel]["ylim"]
        for y, key in ((y0, "disp_y_at_ymin_in"), (y1, "disp_y_at_ymax_in")):
            _c, row = data_to_xy(panel, 1.0, y)
            r_anchor = H - (tr[key] - tb[1] + pad) * dpi * sy
            anchor_dev = max(anchor_dev, abs(row - r_anchor))
        for i, cfg in enumerate(CFG):
            c, _ = data_to_xy(panel, i + 1, y0)
            c_anchor = (tr["disp_x_centers_in"][cfg] - tb[0] + pad) * dpi * sx
            anchor_dev = max(anchor_dev, abs(c - c_anchor))
    rec("L6.0", "L6", "几何映射自洽：仿射重建 vs 出图锚点（≤1px）",
        "ok" if anchor_dev <= 1 else "bad", f"最大Δ={anchor_dev:.2f}px")

    def rendered(hexc, alpha=0.85):
        c = [int(hexc[i:i + 2], 16) for i in (1, 3, 5)]
        return [alpha * v + (1 - alpha) * 255 for v in c]

    # 箱体填充面必然从数据边界向内缩：箱边线半宽 1.1pt/2 + 抗锯齿 1px + 叠加散点半径 sqrt(7)pt/2
    half_stroke = 1.1 / 72.0 * dpi / 2
    dot_r = math.sqrt(7) / 2 / 72.0 * dpi
    tol_box = half_stroke + 1.0 + dot_r
    worst_box, details, insets, wdev, degen = 0.0, [], [], 0.0, []
    for panel in ("C1", "C2"):
        y0p = summ[panel]["ylim"][0]
        for i, cfg in enumerate(CFG):
            st = summ[panel]["stats"][cfg]
            r_q3 = data_to_xy(panel, i + 1, st["q3"])[1]
            r_q1 = data_to_xy(panel, i + 1, st["q1"])[1]
            r_med = data_to_xy(panel, i + 1, st["median"])[1]
            col = int(round(data_to_xy(panel, i + 1, y0p)[0]))
            # 中位线位置：Q1..Q3 区间内最靠中间的深色像素（退化箱只画中位线）
            dark = [r for r in range(int(r_q3) - 2, int(r_q1) + 3) if img[r, col].max() < 90]
            if dark:
                wdev = max(wdev, abs(float(np.mean(dark)) - r_med))
            if abs(r_q1 - r_q3) < 2:                       # Q1==Q3：退化箱，无填充面
                degen.append(f"{panel}/{cfg}")
                continue
            tgt = rendered(summ[panel]["colors"][cfg])
            rows = [r for r in range(H)
                    if abs(img[r, col, 0] - tgt[0]) < 14 and abs(img[r, col, 1] - tgt[1]) < 14
                    and abs(img[r, col, 2] - tgt[2]) < 14]
            if not rows:
                details.append(f"{panel}/{cfg}: 未找到箱体填充色(列 {col})")
                worst_box = 999
                continue
            top, bot = min(rows), max(rows)
            in_top, in_bot = top - r_q3, r_q1 - bot      # 正值=向内缩（PNG 像素）
            insets += [in_top, in_bot]
            # 关键：测到的填充区间必须被数据推得的区间包住（错位会被立刻抳出），且内缩不超过上述容差
            dev = max(-in_top, -in_bot, in_top - tol_box, in_bot - tol_box)
            worst_box = max(worst_box, dev)
            details.append(f"{panel}/{cfg}: 内缩 {in_top:.1f}/{in_bot:.1f}px")
    med_in = float(np.median(insets)) if insets else 0.0
    rec("L6.1", "L6", "箱体像素区间 ↔ 数据 Q1/Q3 区间（包含 且 内缩 ≤ 半线宽+抗锯齿+散点半径）",
        "ok" if worst_box <= 0 else "bad",
        f"最大越界/超缩 {max(0.0, worst_box):.1f}px；中位内缩={med_in:.1f}px；容差={tol_box:.1f}px"
        f"（半线宽 {half_stroke:.1f} + 抗锯齿 1.0 + 散点半径 {dot_r:.1f}）；退化箱 {len(degen)} 个"
        + (" | " + "; ".join(details[:3]) if worst_box > 0 else ""))
    rec("L6.1b", "L6", "中位线像素位置 ↔ 数据中位数（≤4px）",
        "ok" if wdev <= 4 else "bad", f"最大Δ={wdev:.1f}px")

    # 支架横线：每条比较一条横线，端点必须落在该比较两个箱体的中心列上（防“P 值贴错比较对”）
    br_bad, br_cnt = [], {}
    for panel in ("C1", "C2"):
        cen = {i + 1: data_to_xy(panel, i + 1, summ[panel]["ylim"][0])[0] for i in range(len(CFG))}
        r_top = int(data_to_xy(panel, 1, summ[panel]["ylim"][1])[1]) + 2
        r_bot = int(min(data_to_xy(panel, i + 1, summ[panel]["stats"][c]["whisk_hi"])[1]
                        for i, c in enumerate(CFG))) - 8
        lo, hi = max(0, int(cen[1] - 120)), min(W, int(cen[len(CFG)] + 120))
        clusters = []
        for r in range(max(0, r_top), max(0, r_bot)):
            idx = np.flatnonzero(img[r, lo:hi].max(axis=1) < 130)
            if not idx.size:
                continue
            for s in np.split(idx, np.flatnonzero(np.diff(idx) > 3) + 1):
                if s.size < 40:
                    continue
                x0, x1 = lo + int(s[0]), lo + int(s[-1])
                for c in clusters:
                    if abs(c["x0"] - x0) <= 3 and abs(c["x1"] - x1) <= 3:
                        c["rows"].append(r)
                        break
                else:
                    clusters.append({"x0": x0, "x1": x1, "rows": [r]})
        exp = [(cen[CFG.index(a) + 1], cen[CFG.index(b) + 1], f"{a}|{b}")
               for a, b, _lv in summ[panel]["pairs"]]
        matched = []
        for c in clusters:
            best = min(exp, key=lambda e: abs(e[0] - c["x0"]) + abs(e[1] - c["x1"]))
            d = abs(best[0] - c["x0"]) + abs(best[1] - c["x1"])
            if d <= 8:
                matched.append(best[2])
            else:
                br_bad.append(f"{panel}: 线段 x{c['x0']}-{c['x1']} 不匹配任何比较对（最近 {d:.0f}px）")
        br_cnt[panel] = (len(clusters), len(exp), len(set(matched)))
        if len(clusters) != len(exp) or len(set(matched)) != len(exp):
            br_bad.append(f"{panel}: 支架数 {len(clusters)} vs 比较数 {len(exp)}，唯一匹配 {len(set(matched))}")
    rec("L6.4", "L6", "支架横线端点 ↔ 对应比较对的两个箱体中心（一一对应，≤8px）",
        "ok" if not br_bad else "bad",
        "; ".join(f"{k}: 线段{br_cnt[k][0]}/比较{br_cnt[k][1]}/匹配{br_cnt[k][2]}" for k in br_cnt)
        + (" | " + "; ".join(br_bad[:2]) if br_bad else ""))

    pair_ok = True
    for panel in ("C1", "C2"):
        for a, b in [(p[0], p[1]) for p in summ[panel]["pairs"]]:
            want = FS.mann_whitney_p((cite if panel == "C2" else score)[a],
                                     (cite if panel == "C2" else score)[b])
            got = summ[panel]["p_values"][f"{a}|{b}"]
            pair_ok = pair_ok and abs(want - got) < 1e-12
    rec("L6.2", "L6", "图上每个 P 值 ↔ 对应比较对（可复算）", "ok" if pair_ok else "bad")

    txt = PNG.read_bytes()
    rec("L6.3", "L6", "摘要里的 n 与评分文件一致",
        "ok" if summ["n_per"] == n_per else "bad", f"{summ['n_per']}")

    # ── L7 可复现 ────────────────────────────────────────────────────────
    print("L7 可复现性")
    h0 = hashlib.sha1(txt).hexdigest()
    r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "make_figS4_stats.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(ROOT))
    h1 = hashlib.sha1(PNG.read_bytes()).hexdigest()
    rec("L7.1", "L7", "重跑出图逐字节一致（确定性渲染）", "ok" if h0 == h1 else "warn",
        f"{h0[:10]} vs {h1[:10]}")

    # ── L8 结论 ↔ 事实 ──────────────────────────────────────────────────
    print("L8 结论与事实一致")
    MAXTOTAL = float(sum(mx for _k, mx in DIMS))
    sat, spread = [], []
    for q in qids:
        full = 0
        for k, mx in DIMS:
            vals = [A[(q, c)]["dims"][k] for c in CFG]
            if all(abs(v - mx) < 1e-9 for v in vals):
                full += 1
        sat.append(full)
        spread.append(max(A[(q, c)]["total"] for c in CFG) - min(A[(q, c)]["total"] for c in CFG))
    sat_frac = sum(sat) / (len(sat) * len(DIMS))
    spread_frac = float(np.mean(spread)) / MAXTOTAL
    rec("L8.1", "L8", "“量表饱和/差异小”有直接证据（同分维度比 或 同题极差比）",
        "ok" if (sat_frac >= 0.33 or spread_frac <= 0.25) else "warn",
        f"{sat_frac * 100:.0f}% 的评分维度六组同分（均 {np.mean(sat):.1f}/7）；同题极差均值 "
        f"{np.mean(spread):.2f}/{MAXTOTAL:.0f} 分（{spread_frac * 100:.0f}% 量表）")
    dens = {c: float(np.mean([nent[(q, c)] for q in qids])) for c in CFG}
    ratio = dens["hub_full"] / max(1e-9, dens["rag_full"])
    cm = {c: float(np.mean(cite[c])) for c in CFG}
    base = max(cm["rag_partial"], cm["rag_full"], cm["fusion_partial"], cm["fusion_full"])
    lo = min(cm["hub_partial"] / base, cm["hub_full"] / base)
    hi = max(cm["hub_partial"] / base, cm["hub_full"] / base)
    rec("L8.2", "L8", "hub 引用更多这一说法有列表支撑（文末文献条目数均值）",
        "ok" if ratio > 2 else "warn",
        "; ".join(f"{c}: {dens[c]:.1f}" for c in CFG))
    rec("L8.3", "L8", "图注所述倍数区间（2.5–3.7×）与实测一致",
        "ok" if 2.5 <= lo and hi <= 3.7 + 1e-9 else "bad",
        f"实测 {lo:.2f}–{hi:.2f}×（面板 C2 均值 {cm['hub_partial']:.1f}/{cm['hub_full']:.1f} vs 最高基线 {base:.1f}）")

    bad = [x for x in res if x["status"] == "bad"]
    warn = [x for x in res if x["status"] == "warn"]
    OUTJ.write_text(json.dumps({"checks": res, "n_bad": len(bad), "n_warn": len(warn),
                                "holm": [{"pair": t["pair"], "panel": t["panel"],
                                          "P_mw": t["P_mw"],
                                          "holm_panel": t.get("holm_按面板_" + t["panel"]),
                                          "holm_all": t.get("holm_合并全部_all")}
                                         for t in tests],
                                "effect_sizes": hl, "saturation": sat, "ref_like": dens,
                                "markers_backed": backedlist, "box_inset_px_median": med_in,
                                "box_inset_tol_px": tol_box, "n_with_ref_list": n_back},
                               ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# FigS4(C) 统计图核查报告", "",
          f"- 结论：**{len(res) - len(bad) - len(warn)}/{len(res)} 通过**，警告 {len(warn)}，失败 {len(bad)}",
          f"- 生成时间：见 `_figs4c_audit.json`；被审对象：`Figure/supplementary/FigS4_Stats_EN.png`", ""]
    for layer in sorted({x["layer"] for x in res}):
        md.append(f"## {layer}")
        for x in [y for y in res if y["layer"] == layer]:
            mark = {"ok": "✅", "warn": "⚠️", "bad": "❌"}[x["status"]]
            md.append(f"- {mark} **{x['id']}** {x['desc']}" + (f" — {x['detail']}" if x["detail"] else ""))
        md.append("")
    OUTM.write_text("\n".join(md), encoding="utf-8")

    print(f"\n[audit] {len(res) - len(bad) - len(warn)}/{len(res)} 通过（警告 {len(warn)}，失败 {len(bad)}）")
    for x in warn:
        print("  [!]", x["id"], "::", x["detail"])
    for x in bad:
        print("  [X]", x["id"], "::", x["detail"])
    print(f"  -> {OUTJ.name} / {OUTM.name}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
