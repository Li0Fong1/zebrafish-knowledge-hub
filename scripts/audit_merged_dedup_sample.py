# -*- coding: utf-8 -*-
"""抽样 100 篇审计合并去重可信度。"""
from __future__ import annotations

import csv
import json
import random
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "项目阶段/05_数据与库/归档/data_export/literature_judgement_merged/literature_judgement_merged.csv"
OUT = ROOT / "项目阶段/05_数据与库/归档/data_export/literature_judgement_merged/dedup_audit_sample100.md"


def norm_title(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def title_sim(a: str, b: str) -> float:
    ta, tb = norm_title(a), norm_title(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    sa, sb = set(ta.split()), set(tb.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def load_wos_md() -> list[dict]:
    md = (ROOT / "项目阶段/05_数据与库/归档/data_export/merged_judgement_full/_src/wos/result_95000.md").read_text(encoding="utf-8")
    pat = re.compile(
        r"^###\s+(\d+)\s*\n\n"
        r"- \*\*中文标题\*\*：(.+?)\n"
        r"- \*\*英文标题\*\*：(.+?)\n"
        r"- \*\*中文摘要\*\*：(.+?)\n"
        r"- \*\*英文摘要\*\*：(.+?)\n"
        r"- \*\*判断结果\*\*：(.+?)\n"
        r"- \*\*理由\*\*：(.+?)\n"
        r"- \*\*mode\*\*：(.+?)\n",
        re.M,
    )
    out: list[dict] = []
    for m in pat.finditer(md):
        out.append({
            "idx": int(m.group(1)),
            "title_en": m.group(3).strip(),
            "title_zh": m.group(2).strip(),
            "judgement": m.group(6).strip(),
        })
    return out


def main() -> int:
    rows: list[dict] = []
    with open(CSV, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    both = [r for r in rows if "PubMed" in r["数据源"] and "WoS" in r["数据源"]]
    pub_only = [r for r in rows if r["数据源"] == "PubMed"]
    wos_only = [r for r in rows if r["数据源"] == "WoS"]

    pub: dict[str, dict] = {}
    with open(ROOT / "项目阶段/05_数据与库/归档/data_export/pubmed_judgement_full/pubmed_judgement_full.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            pub[r["PMID"]] = r

    wos_md_list = load_wos_md()
    wos_by_en: dict[str, dict] = {}
    wos_by_zh: dict[str, dict] = {}
    for wr in wos_md_list:
        ke = norm_title(wr["title_en"])
        kz = norm_title(wr["title_zh"])
        if len(ke) >= 20:
            wos_by_en.setdefault(ke, wr)
        if len(kz) >= 20:
            wos_by_zh.setdefault(kz, wr)

    def lookup_wos(title_en: str, title_zh: str = "") -> dict | None:
        for key, store in ((norm_title(title_en), wos_by_en), (norm_title(title_zh), wos_by_zh)):
            if len(key) >= 20 and key in store:
                return store[key]
        return None

    random.seed(42)
    sample: list[dict] = []
    sample.extend(random.sample(both, min(40, len(both))))
    sample.extend(random.sample(pub_only, min(30, len(pub_only))))
    sample.extend(random.sample(wos_only, min(30, len(wos_only))))

    issues: list[dict] = []
    checks: list[dict] = []

    for i, r in enumerate(sample, 1):
        src = r["数据源"]
        flags: list[str] = []
        extra: dict = {}

        if "PubMed" in src and "WoS" in src:
            pr = pub.get(r.get("PMID", ""), {})
            wr = lookup_wos(pr.get("英文标题", ""), pr.get("中文标题", "")) if pr else None
            if not pr:
                flags.append("无法回溯 PubMed")
            else:
                sim_mp = title_sim(r.get("英文标题", ""), pr.get("英文标题", ""))
                extra["sim_merged_pub"] = round(sim_mp, 3)
                if sim_mp < 0.98:
                    flags.append(f"合并行与 PubMed 标题不完全一致({sim_mp:.2f})")
            if wr and pr:
                sim = max(
                    title_sim(pr.get("英文标题", ""), wr["title_en"]),
                    title_sim(pr.get("中文标题", ""), wr["title_zh"]),
                )
                extra["sim_pub_wos"] = round(sim, 3)
                extra["pub_j"] = pr.get("判断结果")
                extra["wos_j"] = wr.get("judgement")
                if sim < 0.95:
                    flags.append(f"跨库标题相似度偏低({sim:.2f})")
                if pr.get("判断结果") != wr.get("judgement"):
                    flags.append(f"两库判别不一致(PubMed={pr.get('判断结果')}, WoS={wr.get('judgement')})")
            elif pr:
                flags.append("WoS 原库中未找到同标题记录")

        elif src == "PubMed" and r.get("PMID", "") not in pub:
            flags.append("PMID 不在原 PubMed 库")
        elif src == "WoS":
            wr = lookup_wos(r.get("英文标题", ""), r.get("中文标题", ""))
            if not wr:
                flags.append("WoS 标题未在原 MD 中找到")
            elif wr.get("judgement") != r.get("判断结果"):
                flags.append(f"与原 WoS 判别不一致({wr.get('judgement')} vs {r.get('判断结果')})")

        if not (r.get("英文标题") or r.get("中文标题")):
            flags.append("缺标题")

        item = {
            "no": i,
            "src": src,
            "id": r.get("文献ID", ""),
            "judgement": r.get("判断结果", ""),
            "title": (r.get("英文标题") or r.get("中文标题") or "")[:100],
            "flags": flags,
            **extra,
        }
        checks.append(item)
        if flags:
            issues.append(item)

    # 全量跨库质量
    cross_sims: list[float] = []
    jud_agree = jud_conflict = jud_total = 0
    for r in both:
        pr = pub.get(r.get("PMID", ""), {})
        if not pr:
            continue
        wr = lookup_wos(pr.get("英文标题", ""), pr.get("中文标题", ""))
        if not wr:
            continue
        sim = max(
            title_sim(pr.get("英文标题", ""), wr["title_en"]),
            title_sim(pr.get("中文标题", ""), wr["title_zh"]),
        )
        cross_sims.append(sim)
        jud_total += 1
        if pr.get("判断结果") == wr.get("judgement"):
            jud_agree += 1
        else:
            jud_conflict += 1

    buckets = Counter()
    for s in cross_sims:
        if s >= 0.98:
            buckets[">=0.98"] += 1
        elif s >= 0.95:
            buckets["0.95-0.98"] += 1
        elif s >= 0.85:
            buckets["0.85-0.95"] += 1
        else:
            buckets["<0.85"] += 1

    high_conf = buckets[">=0.98"] + buckets["0.95-0.98"]
    high_conf_rate = high_conf / len(cross_sims) * 100 if cross_sims else 0
    low_sim_rate = buckets["<0.85"] / len(cross_sims) * 100 if cross_sims else 0
    issue_rate = len(issues) / len(sample) * 100

    # 漏合并检测：WoS-only 标题是否仍命中 PubMed 标题集
    pub_titles = {norm_title(r.get("英文标题", "")) for r in pub.values()}
    pub_titles |= {norm_title(r.get("中文标题", "")) for r in pub.values()}
    pub_titles = {t for t in pub_titles if len(t) >= 20}
    leak = 0
    for r in wos_only:
        te = norm_title(r.get("英文标题", ""))
        tz = norm_title(r.get("中文标题", ""))
        if te in pub_titles or tz in pub_titles:
            leak += 1
    leak_rate = leak / len(wos_only) * 100 if wos_only else 0

    if high_conf_rate >= 95 and low_sim_rate <= 2 and leak_rate <= 1 and issue_rate <= 15:
        verdict = "**总体可信度：较高。** 跨库合并基本准确，字段保留 PubMed 优先策略合理。"
    elif high_conf_rate >= 88 and low_sim_rate <= 5:
        verdict = "**总体可信度：中等偏高。** 可用于上交，但建议对「两库判别不一致」条目做 spot check。"
    else:
        verdict = "**总体可信度：中等。** 存在标题近邻误并或漏并风险，建议优化去重规则。"

    lines = [
        "# 合并去重可信度 — 100 篇抽样审计",
        "",
        f"抽样：跨库合并 40 + PubMed 独有 30 + WoS 独有 30",
        "",
        "## 结论",
        "",
        verdict,
        "",
        "## 全量跨库合并（{} 篇）".format(len(both)),
        "",
        f"- 可回溯并比对：**{jud_total:,}** 篇",
        f"- 标题相似度均值：**{sum(cross_sims)/len(cross_sims):.3f}**" if cross_sims else "",
        f"- 高置信（≥0.95）：**{high_conf_rate:.1f}%**",
        f"- 低置信（<0.85）：**{low_sim_rate:.1f}%**（{buckets['<0.85']} 篇）",
        f"- 两库判别一致：**{jud_agree/jud_total*100:.1f}%**（{jud_agree}/{jud_total}）" if jud_total else "",
        f"- 两库判别冲突：**{jud_conflict:,}** 篇（合并后保留 PubMed 判别）",
        f"- WoS 独有中疑似漏并：**{leak_rate:.2f}%**（{leak}/{len(wos_only)}）",
        "",
        "### 相似度分布",
        "",
        "| 区间 | 篇数 |",
        "|------|------|",
    ]
    for k in (">=0.98", "0.95-0.98", "0.85-0.95", "<0.85"):
        lines.append(f"| {k} | {buckets[k]:,} |")

    lines += [
        "",
        "## 100 篇抽样",
        "",
        f"- 疑点：**{len(issues)} / 100**（{issue_rate:.0f}%）",
        "",
    ]

    if issues:
        lines += ["### 疑点样例（前 15 条）", ""]
        for it in issues[:15]:
            fl = "; ".join(it["flags"])
            lines.append(f"- #{it['no']} `{it['id']}` [{it['src']}] → {fl}")
            lines.append(f"  - {it['title']}")

    ok = [c for c in checks if not c["flags"]]
    lines += ["", f"### 正常样例（{min(8, len(ok))} 条）", ""]
    for it in random.sample(ok, min(8, len(ok))):
        extra = f" sim={it['sim_pub_wos']}" if "sim_pub_wos" in it else ""
        lines.append(f"- #{it['no']} `{it['id']}` [{it['src']}] {it['judgement']}{extra}")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "sample_issues": len(issues),
        "issue_rate_pct": round(issue_rate, 1),
        "cross_merge": len(both),
        "cross_verified": jud_total,
        "high_conf_rate_pct": round(high_conf_rate, 1),
        "low_sim_rate_pct": round(low_sim_rate, 1),
        "judgement_agree_pct": round(jud_agree / jud_total * 100, 1) if jud_total else 0,
        "judgement_conflict": jud_conflict,
        "leak_rate_pct": round(leak_rate, 2),
        "report": str(OUT),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
