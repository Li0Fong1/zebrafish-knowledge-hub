# -*- coding: utf-8 -*-
"""只读实算：两个评委各 60 条总分均值，核对论文正文 8.35/7.38 是否真实。

不做任何改动、不联网，纯本地读取 JSON。结果写 docs/评分均值核验.md。
用法：python scripts/verify_judge_means.py
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FILES = [
    ("评委 A", ROOT / "Figure" / "_config_sweep_scores.json"),
    ("评委 B", ROOT / "Figure" / "_config_sweep_scores_reasoner.json"),
]

lines = [
    "# 双评委总分均值核验", "",
    "核对论文正文：\"mean total rubric scores of 8.35 and 7.38 out of 10 from the two judges\"", "",
]

results = {}
for label, p in FILES:
    if not p.is_file():
        lines.append("- %s：文件不存在 %s" % (label, p.name))
        continue
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = d.get("rows") or []
    totals = [float(r.get("total") or 0) for r in rows if r.get("total") is not None]
    mean = sum(totals) / len(totals) if totals else float("nan")
    results[label] = mean
    # 按配置再算一遍，便于定位 8.35 是不是某个配置值
    by_cfg: dict[str, list[float]] = {}
    for r in rows:
        by_cfg.setdefault(r.get("config", "?"), []).append(float(r.get("total") or 0))
    lines += [
        "## %s" % label, "",
        "- 文件：`%s`" % p.relative_to(ROOT).as_posix(),
        "- 文件记录的 model：`%s`" % d.get("model"),
        "- 条目数：%d" % len(totals),
        "- **全部条目均值：%.4f**" % mean,
        "- 中位数：%.4f" % statistics.median(totals),
        "- 最小 / 最大：%.2f / %.2f" % (min(totals), max(totals)),
        "",
        "按配置均值：",
        "",
    ]
    for cfg, vals in sorted(by_cfg.items()):
        lines.append("  - %-16s n=%d mean=%.4f" % (cfg, len(vals), sum(vals) / len(vals)))
    lines.append("")

lines += [
    "## 与论文正文对照", "",
    "- 论文写：评委 A = 8.35，评委 B = 7.38（n = 10）",
]
for label, mean in results.items():
    lines.append("- 实算 %s = %.4f" % (label, mean))
lines += [
    "",
    "说明：正文写的 n=10，但评分文件是 10 题 × 6 配置 = 60 条。",
    "若正文的 8.35/7.38 指全部条目均值，与上表实算值对照即可判定真伪。",
    "",
]

rp = ROOT / "docs" / "评分均值核验.md"
rp.parent.mkdir(exist_ok=True)
rp.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(l for l in lines if "均值" in l or "实算" in l or "条目数" in l))
print("报告：%s" % rp)
