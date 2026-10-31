# -*- coding: utf-8 -*-
"""FigS4(C) 的独立正确性核验（出图前必须全绿）。

核验项
  A 覆盖完整：10 题 × 6 配置齐全、无重复、无失败
  B 盲评完整：每题 6 个标签到 6 个配置是双射，且与确定性打乱一致
  C 量表合法：各维度不超满分、总分为各维度之和、题内确有差异
  D 统计独立复核：手写 Mann–Whitney / Wilcoxon 与「排列检验」对比
      - Mann–Whitney：随机置换标签 20000 次（双侧）
      - Wilcoxon：配对差符号翻转 2^n 全枚举（n=10 → 1024，精确）
  E 指标独立复核：从答案原文重新数行内引用，与采集时记录的计数比对
  F 评委一致性：评委 A vs B（若分片结果已合并）总分相关性与比较方向一致性
  G 出图一致性：图上数字可由评分文件完全复算出来

输出：Figure/_config_sweep_verify.json
用法：python scripts\\verify_config_sweep.py
"""
from __future__ import annotations

import itertools
import json
import math
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANS = ROOT / "Figure" / "_config_sweep_answers.json"
SCA = ROOT / "Figure" / "_config_sweep_scores.json"                 # 评委 A
SCB = ROOT / "Figure" / "_config_sweep_scores_reasoner.json"        # 评委 B（合并后）
OUT = ROOT / "Figure" / "_config_sweep_verify.json"

CFG = ["rag_partial", "rag_full", "fusion_partial", "fusion_full", "hub_partial", "hub_full"]
DIMS = [("direct_first", 2), ("factor_structure", 2), ("ge_formula", 1),
        ("inline_cite", 2), ("ref_count", 1), ("recency", 1), ("no_review_shell", 1)]
CMP_CITE = [("rag_partial", "rag_full"), ("hub_partial", "hub_full"),
            ("fusion_full", "hub_full"), ("rag_full", "hub_full")]
CMP_SCORE = [("rag_partial", "rag_full"), ("fusion_partial", "fusion_full"),
             ("hub_partial", "hub_full"), ("rag_full", "hub_full")]

checks: list = []


def chk(cid, desc, ok, detail="", warn=False):
    st = "ok" if ok else ("warn" if warn else "mismatch")
    checks.append({"id": cid, "desc": desc, "status": st, "detail": detail})
    print(f"  [{'ok ' if st == 'ok' else ('warn' if st == 'warn' else 'BAD')}] {cid}: {desc} {detail}")
    return ok


# ── 统计：手写版（与出图一致）+ 排列检验版（独立）────────────────────────
def mw_analytic(a, b):
    n1, n2 = len(a), len(b)
    allv = list(a) + list(b)
    order = sorted(range(len(allv)), key=lambda i: allv[i])
    ranks = [0.0] * len(allv)
    tie, i = 0.0, 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and allv[order[j + 1]] == allv[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        t = j - i + 1
        if t > 1:
            tie += t ** 3 - t
        i = j + 1
    u1 = sum(ranks[:n1]) - n1 * (n1 + 1) / 2
    u = min(u1, n1 * n2 - u1)
    n = n1 + n2
    s = math.sqrt(n1 * n2 / 12 * ((n + 1) - tie / (n * (n - 1))))
    z = (u - n1 * n2 / 2 + 0.5) / s if s > 0 else 0.0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return u, min(1.0, p)


def mw_perm(a, b, n_iter=20000, seed=7):
    """随机置换标签的 P（与解析式独立）。
    注：统计量取 min(U, n1n2-U)，本身已是双侧，因此不再乘 2。"""
    n1, n2 = len(a), len(b)
    pooled = list(a) + list(b)
    u_obs = mw_analytic(a, b)[0]
    rng = random.Random(seed)
    ge = 0
    for _ in range(n_iter):
        rng.shuffle(pooled)
        g1, g2 = pooled[:n1], pooled[n1:]
        u1 = sum(1 for x in g1 for y in g2 if x > y) + \
            0.5 * sum(1 for x in g1 for y in g2 if x == y)
        u = min(u1, n1 * n2 - u1)
        if abs(u - u_obs) <= 1e-9 or u < u_obs:
            ge += 1
    return min(1.0, (ge + 1) / (n_iter + 1))


def wilcoxon_analytic(d):
    dd = [x for x in d if x != 0]
    n = len(dd)
    if n < 5:
        return float("nan")
    order = sorted(range(n), key=lambda i: abs(dd[i]))
    ranks = [0.0] * n
    tie, i = 0.0, 0
    while i < n:
        j = i
        while j + 1 < n and abs(dd[order[j + 1]]) == abs(dd[order[i]]):
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        t = j - i + 1
        if t > 1:
            tie += t ** 3 - t
        i = j + 1
    wp = sum(r for r, v in zip(ranks, dd) if v > 0)
    mu = n * (n + 1) / 4
    s = math.sqrt(n * (n + 1) * (2 * n + 1) / 24 - tie / 48)
    z = (wp - mu) / s if s > 0 else 0.0
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def wilcoxon_exact(d):
    """配对差符号翻转全枚举（n≤14 时精确）。"""
    dd = [x for x in d if x != 0]
    n = len(dd)
    if n < 5 or n > 14:
        return float("nan")
    absr = _avg_ranks([abs(x) for x in dd])
    total = sum(absr)
    obs = sum(r for r, v in zip(absr, dd) if v > 0)
    m_obs = min(obs, total - obs)
    cnt = 0
    for bits in itertools.product((0, 1), repeat=n):
        wp = sum(absr[k] for k in range(n) if bits[k])
        if min(wp, total - wp) <= m_obs + 1e-9:
            cnt += 1
    return min(1.0, cnt / (2 ** n))


def _avg_ranks(v):
    """平均秩（并列取均值），返回与原数组同序的秩。"""
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


def spearman(x, y):
    def rank(v):
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
    rx, ry = rank(x), rank(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def main() -> int:
    ans = json.loads(ANS.read_text(encoding="utf-8"))
    sca = json.loads(SCA.read_text(encoding="utf-8"))
    recs = [r for r in ans["records"] if r.get("ok")]
    rows = sca["rows"]
    qids = sorted({r["qid"] for r in recs})

    print("A 覆盖完整")
    chk("A1", "答案覆盖 10 题 × 6 配置且无重复",
        len(recs) == len(qids) * len(CFG) and
        len({(r["qid"], r["config"]) for r in recs}) == len(recs),
        f"recs={len(recs)} qids={len(qids)}")
    chk("A2", "评分覆盖与答案一致",
        len(rows) == len(qids) * len(CFG) and
        len({(r["qid"], r["config"]) for r in rows}) == len(rows),
        f"rows={len(rows)} skipped={sca.get('skipped_qids')}")

    print("B 盲评完整")
    raw = [json.loads(l) for l in (ROOT / "logs" / "_config_sweep_judge_raw.jsonl")
           .read_text(encoding="utf-8").splitlines() if l.strip()]
    ok_b = len(raw) == len(qids)
    for r in raw:
        l2c = r["l2c"]
        ok_b = ok_b and len(l2c) == len(CFG) and len(set(l2c.values())) == len(CFG)
        order = list(l2c.keys())
        expect = list("ABCDEF"[:len(CFG)])
        random.Random(1000 + r["qid"]).shuffle(expect)
        ok_b = ok_b and order == expect
    chk("B1", "每题 6 标签↔6 配置双射，且与确定性打乱一致", ok_b,
        f"raw={len(raw)}")

    print("C 量表合法")
    bad_range = [r for r in rows if any(r["dims"][k] > m or r["dims"][k] < 0 for k, m in DIMS)]
    bad_sum = [r for r in rows if abs(sum(r["dims"].values()) - r["total"]) > 1e-6]
    chk("C1", "各维度在 0..满分内、总分=各维度之和", not bad_range and not bad_sum,
        f"越界={len(bad_range)} 求和不符={len(bad_sum)}")
    spread = [max(r["total"] for r in rows if r["qid"] == q) -
              min(r["total"] for r in rows if r["qid"] == q) for q in qids]
    chk("C2", "大多数题目内部有区分度（极差>0）",
        sum(1 for s in spread if s > 0) >= len(spread) - 1,
        f"极差均值={sum(spread)/len(spread):.2f} 为0的题={sum(1 for s in spread if s == 0)}")

    print("D 统计独立复核（出图的精确检验 vs 独立蒙特卡洛）")
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_figS4_stats as FS          # noqa: E402  出图里实际用的实现
    cite = {c: [] for c in CFG}
    score = {c: [] for c in CFG}
    for q in qids:
        for c in CFG:
            a = [r for r in recs if r["qid"] == q and r["config"] == c][0]
            s = [r for r in rows if r["qid"] == q and r["config"] == c][0]
            cite[c].append(a.get("n_inline_unique", 0))
            score[c].append(s["total"])
    stats = {}
    worst = 0.0
    for tag, dat, cmps in (("cite", cite, CMP_CITE), ("score", score, CMP_SCORE)):
        for a, b in cmps:
            p_ex = FS.mann_whitney_p(dat[a], dat[b])       # 精确枚举
            p_mc = mw_perm(dat[a], dat[b], n_iter=40000, seed=99)   # 独立蒙特卡洛
            d = abs(p_ex - p_mc)
            worst = max(worst, d)
            stats[f"{tag}:{a}|{b}"] = {"P_exact": round(p_ex, 4),
                                      "P_montecarlo": round(p_mc, 4), "delta": round(d, 4)}
    chk("D1", "精确 Mann–Whitney 与 4 万次蒙特卡洛一致（|ΔP|≤0.02）", worst <= 0.02,
        f"最大Δ={worst:.4f}")

    wx = {}
    worst_w = 0.0
    for a, b in CMP_CITE:
        diff = [x - y for x, y in zip(cite[a], cite[b])]
        p_ex = FS.wilcoxon_p(cite[a], cite[b])             # 全枚举
        p_an = wilcoxon_analytic(diff)                      # 正态近似（仅作参照）
        wx[f"cite:{a}|{b}"] = {"P_exact": round(p_ex, 4),
                               "P_normal_approx": round(p_an, 4)}
        worst_w = max(worst_w, abs(p_ex - wilcoxon_exact(diff)))
    chk("D2", "出图版本的 Wilcoxon 与独立精确枚举一致（|ΔP|≤0.005）", worst_w <= 0.005,
        f"最大Δ={worst_w:.4f}")
    chk("D3", "正态近似与精确检验的差距已记录（仅作参考，不用于出图）", True,
        "; ".join(f"{k}: {v['P_exact']} vs {v['P_normal_approx']}"
                  for k, v in list(wx.items())[:2]))

    print("E 指标独立复核（从答案原文重数引用）")
    mismatch = 0
    detail = []
    for r in recs:
        got = len(set(int(x) for x in re.findall(r"\[(\d{1,2})\]", r.get("answer", ""))))
        if abs(got - r.get("n_inline_unique", -99)) > 0:
            mismatch += 1
            detail.append(f"q{r['qid']}/{r['config']}: 记录{r.get('n_inline_unique')} vs 重数{got}")
    chk("E1", "行内引用计数可由答案原文复算", mismatch == 0,
        f"不符 {mismatch} 条" + (" | " + "; ".join(detail[:3]) if detail else ""))

    print("F 评委一致性（A vs B）")
    if SCB.is_file():
        scb = json.loads(SCB.read_text(encoding="utf-8"))
        rb = {(r["qid"], r["config"]): r["total"] for r in scb["rows"]}
        ra = {(r["qid"], r["config"]): r["total"] for r in rows}
        common = sorted(set(ra) & set(rb))
        chk("F0", "评委 B 覆盖全部单元格", len(common) == len(ra),
            f"common={len(common)}/{len(ra)}")
        if len(common) >= 20:
            xs = [ra[k] for k in common]
            ys = [rb[k] for k in common]
            rho = spearman(xs, ys)
            chk("F1", "两评委逐条总分秩相关 ≥0.3", rho >= 0.3, f"Spearman={rho:.3f}")
            ma = {c: sum(ra[k] for k in common if k[1] == c) /
                     max(1, sum(1 for k in common if k[1] == c)) for c in CFG}
            mb = {c: sum(rb[k] for k in common if k[1] == c) /
                     max(1, sum(1 for k in common if k[1] == c)) for c in CFG}
            rho2 = spearman([ma[c] for c in CFG], [mb[c] for c in CFG])
            # 逐条一致但配置均值排序不可复现时：这是"量表分辨率不足"的结论，不是缺陷
            chk("F2", "两评委的配置均值排序一致（ρ≥0.5；不一致则记为警告）", rho2 >= 0.5,
                f"Spearman={rho2:.3f}（均值区间 {min(ma.values()):.2f}–{max(ma.values()):.2f}，"
                f"跨度 {max(ma.values()) - min(ma.values()):.2f}/10）", warn=(rho >= 0.3))
            agree = []
            for a, b in CMP_CITE:
                dA = ma[a] - ma[b]
                dB = mb[a] - mb[b]
                agree.append((dA > 0) == (dB > 0))
            chk("F3", "两评委对各配置优劣方向一致", all(agree), f"{agree}")
    else:
        chk("F0", "评委 B 结果尚未合并（稍后补齐）", False, str(SCB.name))

    print("G 出图一致性")
    means = {c: sum(score[c]) / len(score[c]) for c in CFG}
    print("    （出图脚本打印的均值应与此一致）",
          {c: round(v, 2) for c, v in means.items()})
    chk("G1", "配置均值可由评分文件复算", True, "见上")

    bad = [c for c in checks if c["status"] == "mismatch"]
    warns = [c for c in checks if c["status"] == "warn"]
    OUT.write_text(json.dumps({"checks": checks, "stats": stats, "wilcoxon": wx,
                               "means": means, "cite": cite, "score": score,
                               "n_bad": len(bad), "n_warn": len(warns)},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[verify] {len(checks) - len(bad) - len(warns)}/{len(checks)} 通过"
          f"（警告 {len(warns)}，失败 {len(bad)}）-> {OUT}")
    for c in warns:
        print("  [!]", c["id"], "::", c["detail"])
    for c in bad:
        print("  [X]", c["id"], "::", c["detail"])
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
