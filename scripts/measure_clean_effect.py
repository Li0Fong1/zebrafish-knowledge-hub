# -*- coding: utf-8 -*-
"""测量真实语料经「清洗 → 切分」后的变化，为数据清洗图提供真实统计。

统计对象：`解析文件/yes_fulltext_gold/batch_*/papers/*.json` 抽样若干篇
统计口径：
    1. 原始正文长度 → clean_scientific_text 后长度 → clean_figure_artifacts 后长度
    2. 自然段切分（max=360 / min=110 / overlap=48）得到的段数与段长分布
    3. 各类清洗规则的触发率：参考文献截断 / 致谢截断 / 作者行剔除 / 图注伪影清理
    4. sections 章节种类分布（供「章节加权」说明）

输出：Figure/_clean_stats.json
用法：python scripts\\measure_clean_effect.py [--n 400] [--seed 42]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                                     # noqa: E402

from rag.text_clean import clean_scientific_text, _REF_START, _ACK_START, _AUTHOR_LINE  # noqa: E402
from rag.paragraph_split import split_natural_paragraphs     # noqa: E402

try:
    from rag.pdf_fulltext_align import clean_figure_artifacts
except Exception:                                            # 缺重依赖时不阻断
    clean_figure_artifacts = None

BASE = ROOT / "解析文件" / "yes_fulltext_gold"
OUT = FC.FIG_DIR / "_clean_stats.json"

_PANEL_LINE = re.compile(r"(?m)^[A-Fa-f]\*?\s*$")
_STAGE_TOKEN = re.compile(r"\b\d{1,3}-(?:cell|somite)\b")
_HPF_TOKEN = re.compile(r"(?im)^(?:~?\d+\s*h\.?p\.?f\.?\s*) +$")

# 语料构建脚本多处对 body_text 做 [:120000] 截断；记录上限命中率以便在图注中披露
CAP_CHARS = 119_000


def _split_params() -> dict:
    """从实现里读切分参数，避免文档与代码脱钩。"""
    import inspect
    sig = inspect.signature(split_natural_paragraphs)
    return {k: sig.parameters[k].default
            for k in ("max_chars", "min_chars", "overlap") if k in sig.parameters}


def pick(n_target: int, seed: int) -> list[Path]:
    random.seed(seed)
    batches = sorted([d for d in BASE.glob("batch_*") if d.is_dir()])
    random.shuffle(batches)
    out: list[Path] = []
    per = max(1, n_target // max(1, min(60, len(batches))))
    for b in batches:
        papers = sorted((b / "papers").glob("*.json"))
        if not papers:
            continue
        out += random.sample(papers, min(per, len(papers)))
        if len(out) >= n_target:
            break
    return out[:n_target]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--stability-seeds", default="",
                    help="额外抽样种子（逗号分隔），用于给出跨抽样稳定性区间")
    a = ap.parse_args()

    files = pick(a.n, a.seed)
    _digest = hashlib.sha256("\n".join(
        sorted(f.relative_to(BASE).as_posix() for f in files)).encode()).hexdigest()[:16]
    print(f"[clean-stats] sampling {len(files)} papers from {BASE.name} (digest {_digest})")

    rows = []
    sec_keys: Counter = Counter()
    for i, f in enumerate(files, 1):
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        body = d.get("body_text") or ""
        if len(body) < 500:
            continue
        sec_keys.update((d.get("sections") or {}).keys())

        raw = len(body)
        t1 = clean_scientific_text(body)
        t2 = clean_figure_artifacts(t1) if clean_figure_artifacts else t1
        paras = split_natural_paragraphs(t2, max_chars=360, min_chars=110, overlap=48)
        plens = [len(p) for p in paras]

        # 真实触发判定：严格复刻 clean_scientific_text 的顺序
        # （先在原文上判 refs 位置，再在截断后文本上判 ack 位置）
        tt = body.replace("\r\n", "\n").replace("\r", "\n")
        mrf = _REF_START.search(tt)
        refs_trig = bool(mrf and mrf.start() > len(tt) * 0.35)
        if refs_trig:
            tt = tt[: mrf.start()]
        mak = _ACK_START.search(tt)
        ack_trig = bool(mak and mak.start() > len(tt) * 0.5)

        rows.append({
            "entry": d.get("entry_no"), "pmid": d.get("pmid") or "",
            "raw_chars": raw, "clean_chars": len(t1), "artifact_chars": len(t2),
            "paras": len(paras),
            "para_mean": round(statistics.mean(plens), 1) if plens else 0,
            "para_med": int(statistics.median(plens)) if plens else 0,
            "para_min": min(plens) if plens else 0, "para_max": max(plens) if plens else 0,
            "refs_head": bool(mrf), "refs_trig": refs_trig,
            "ack_head": bool(mak), "ack_trig": ack_trig,
            "capped": raw >= CAP_CHARS,
            "author_lines": len([ln for ln in body.split("\n") if _AUTHOR_LINE.match(ln.strip())]),
            "panel_lines": len(_PANEL_LINE.findall(t1)),
            "stage_tokens": len(_STAGE_TOKEN.findall(t1)),
            "hpf_lines": len(_HPF_TOKEN.findall(t1)),
        })
        if i % 50 == 0:
            print(f"   ... {i}/{len(files)}")

    if not rows:
        print("[clean-stats] no usable samples"); return 1

    def agg(key):
        v = [r[key] for r in rows]
        return {"mean": round(statistics.mean(v), 1), "median": statistics.median(v),
                "min": min(v), "max": max(v)}

    n = len(rows)
    removed = [1 - r["clean_chars"] / r["raw_chars"] for r in rows if r["raw_chars"]]
    artifact = [(r["clean_chars"] - r["artifact_chars"]) / r["clean_chars"]
                for r in rows if r["clean_chars"]]
    stats = {
        "source": f"{BASE.name}/batch_*/papers/*.json",
        "sampled": n, "seed": a.seed,
        "sample_digest": _digest,
        "sampled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "corpus_is_live": True,
        "raw_chars": agg("raw_chars"), "clean_chars": agg("clean_chars"),
        "artifact_chars": agg("artifact_chars"),
        "chars_removed_ratio": {"mean": round(statistics.mean(removed) * 100, 2),
                                "median": round(statistics.median(removed) * 100, 2)},
        "artifact_removed_ratio": {"mean": round(statistics.mean(artifact) * 100, 3)}
        if artifact else {"mean": 0.0},
        "paras": agg("paras"), "para_mean": agg("para_mean"), "para_med": agg("para_med"),
        "rules": {
            "refs_section_present": round(sum(r["refs_head"] for r in rows) / n * 100, 1),
            "refs_truncation_triggered": round(sum(r["refs_trig"] for r in rows) / n * 100, 1),
            "ack_section_present": round(sum(r["ack_head"] for r in rows) / n * 100, 1),
            "ack_truncation_triggered": round(sum(r["ack_trig"] for r in rows) / n * 100, 1),
            "author_lines_total": sum(r["author_lines"] for r in rows),
            "panel_letters_total": sum(r["panel_lines"] for r in rows),
            "stage_tokens_total": sum(r["stage_tokens"] for r in rows),
            "hpf_lines_total": sum(r["hpf_lines"] for r in rows),
        },
        "corpus": {
            "min_chars_filter": 500,
            "body_cap_chars": CAP_CHARS,
            "cap_hits": sum(r["capped"] for r in rows),
            "cap_hits_pct": round(sum(r["capped"] for r in rows) / n * 100, 1),
        },
        "sections": dict(sec_keys.most_common()),
        "split_params": _split_params(),
    }

    # 跨抽样稳定性：同一样本量、不同种子，给出区间以便在图注中披露
    stab = []
    for s in [int(x) for x in (a.stability_seeds or "").split(",") if x.strip()]:
        if s == a.seed:
            continue
        rr = []
        for f in pick(a.n, s):
            try:
                d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                continue
            b = d.get("body_text") or ""
            if len(b) < 500:
                continue
            c1 = clean_scientific_text(b)
            c2 = clean_figure_artifacts(c1) if clean_figure_artifacts else c1
            rr.append((len(b), len(c1), len(split_natural_paragraphs(c2))))
        if rr:
            stab.append({"seed": s, "n": len(rr),
                         "raw_mean": round(statistics.mean([x[0] for x in rr]), 1),
                         "clean_mean": round(statistics.mean([x[1] for x in rr]), 1),
                         "paras_mean": round(statistics.mean([x[2] for x in rr]), 2)})
    if stab:
        stats["stability"] = stab
        raws = [stats["raw_chars"]["mean"]] + [x["raw_mean"] for x in stab]
        stats["stability_spread_pct"] = round((max(raws) - min(raws)) / statistics.mean(raws) * 100, 2)

    OUT.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] samples={n}")
    print(f"     raw {stats['raw_chars']['mean']:.0f} -> clean {stats['clean_chars']['mean']:.0f} "
          f"chars  (removed {stats['chars_removed_ratio']['mean']}% mean)")
    print(f"     paragraphs {stats['paras']['mean']:.1f} per paper, "
          f"mean len {stats['para_mean']['mean']:.0f}")
    print(f"     refs present {stats['rules']['refs_section_present']}% · "
          f"ack {stats['rules']['ack_section_present']}% · "
          f"panel letters {stats['rules']['panel_letters_total']} · "
          f"stage tokens {stats['rules']['stage_tokens_total']}")
    print(f"[saved] {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
