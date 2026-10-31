# -*- coding: utf-8 -*-
"""
事项二：section 权重小样 A/B（不建全库 FAISS，不重嵌现网）。

对比同一批文献：
  baseline: field=body（现网）
  section:  field=introduction/results/...（新权重表）

输出：解析文件/事项二_权重小样.txt
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from rag.pubmed_yes_chunking import (
    FIELD_WEIGHTS,
    adjust_l2_score,
    split_record_to_field_chunks,
    split_record_to_section_chunks,
)

CORPUS = ROOT / "data" / "literature" / "pubmed_yes_corpus.jsonl"
OUT = ROOT / "解析文件" / "事项二_权重小样.txt"
SAMPLE_N = 40
MAX_CHUNKS = 72

# 模拟三类 query 的「期望高权重 field」
QUERY_EXPECT = {
    "mechanism": ("introduction", "discussion"),  # 机制类易被 intro 抢
    "results": ("results", "conclusion", "abstract"),
    "methods": ("methods",),
}


def _load_sample(n: int) -> list[dict]:
    rows: list[dict] = []
    if not CORPUS.is_file():
        return rows
    with CORPUS.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            body = (rec.get("body_text") or "").strip()
            if len(body) < 800:
                continue
            rows.append(rec)
            if len(rows) >= n:
                break
    return rows


def _field_hist(chunks: list[dict]) -> Counter:
    return Counter(str(c.get("field") or "mixed") for c in chunks)


def _weighted_share(hist: Counter) -> dict[str, float]:
    # 用「权重×块数」近似检索偏好（L2 越小越好 → 权重大更优）
    total_w = 0.0
    weighted: dict[str, float] = {}
    for f, cnt in hist.items():
        w = float(FIELD_WEIGHTS.get(f, FIELD_WEIGHTS["mixed"]))
        score = w * cnt
        weighted[f] = score
        total_w += score
    if total_w <= 0:
        return {}
    return {k: round(v / total_w, 4) for k, v in sorted(weighted.items(), key=lambda x: -x[1])}


def main() -> int:
    rows = _load_sample(SAMPLE_N)
    if not rows:
        safe_print("[失败] 无足够正文语料做小样")
        return 1

    base_hist = Counter()
    sec_hist = Counter()
    sectioned_n = 0
    fallback_n = 0
    base_chunks = 0
    sec_chunks = 0
    demo_scores: list[str] = []

    for i, rec in enumerate(rows):
        b = split_record_to_field_chunks(rec, max_chunks=MAX_CHUNKS, use_sections=False)
        s = split_record_to_section_chunks(rec, max_chunks=MAX_CHUNKS)
        base_hist.update(_field_hist(b))
        sh = _field_hist(s)
        sec_hist.update(sh)
        base_chunks += len(b)
        sec_chunks += len(s)
        if any(f in sh for f in ("introduction", "results", "methods", "discussion", "conclusion")):
            sectioned_n += 1
        else:
            fallback_n += 1

        if i < 3:
            # 假想 raw L2=0.4，看 adjust 后排序偏好
            fields = list(dict.fromkeys(c.get("field") for c in s))[:8]
            adj = [(f, round(adjust_l2_score(0.4, f), 4), FIELD_WEIGHTS.get(f, FIELD_WEIGHTS["mixed"])) for f in fields]
            adj.sort(key=lambda x: x[1])
            demo_scores.append(
                f"  PMID={rec.get('pmid')} fields→adjL2(raw=0.4): "
                + ", ".join(f"{f}={a}(w={w})" for f, a, w in adj)
            )

    base_share = _weighted_share(base_hist)
    sec_share = _weighted_share(sec_hist)

    prefer_keys = ("abstract", "results", "conclusion", "introduction", "methods", "body", "discussion")
    lines = [
        "事项二：section 权重小样 A/B",
        f"时间: {datetime.now().isoformat(timespec='seconds')}",
        f"样本: {len(rows)} 篇（pubmed_yes_corpus 有正文）",
        f"max_chunks={MAX_CHUNKS}",
        "",
        "== 建议权重表（已写入 core/config + FIELD_WEIGHTS）==",
    ]
    for k in (
        "title",
        "abstract",
        "conclusion",
        "results",
        "discussion",
        "methods",
        "introduction",
        "body",
        "meta",
        "references",
        "figure_captions",
        "other",
        "mixed",
        "keywords",
    ):
        lines.append(f"  {k}={FIELD_WEIGHTS.get(k)}")
    lines += [
        "",
        "== 切块统计 ==",
        f"  baseline chunks={base_chunks}  hist={dict(base_hist)}",
        f"  section  chunks={sec_chunks}  hist={dict(sec_hist)}",
        f"  sectioned_papers={sectioned_n}  fallback_body={fallback_n}",
        "",
        "== 加权份额（weight×chunk_count 归一，近似检索偏好）==",
        f"  baseline: { {k: base_share.get(k) for k in prefer_keys if k in base_share} }",
        f"  section:  { {k: sec_share.get(k) for k in prefer_keys if k in sec_share} }",
        "",
        "== 方向性结论 ==",
    ]
    intro_b = base_share.get("introduction", 0.0) + base_share.get("body", 0.0)
    intro_s = sec_share.get("introduction", 0.0)
    rc_s = (sec_share.get("results") or 0) + (sec_share.get("conclusion") or 0) + (sec_share.get("abstract") or 0)
    rc_b = (base_share.get("abstract") or 0) + (base_share.get("body") or 0)
    lines.append(f"  baseline 正文多标为 body，加权份额 body+intro≈{intro_b:.4f}（intro 无法单独下调）")
    lines.append(f"  section 后 introduction 加权份额={intro_s:.4f}（权重 0.75，可单独压）")
    lines.append(f"  section 后 results+conclusion+abstract 加权份额={rc_s:.4f}（相对 baseline 可解释份额 {rc_b:.4f}）")
    lines.append("  全量 section 切块并入阶段 C 同一次重嵌；本小样不写旁路 FAISS、不改现网索引。")
    lines.append("")
    lines.append("== 抽检 adjust_l2（越小越优先）==")
    lines.extend(demo_scores or ["  (无)"])
    lines.append("")
    lines.append("== 查询期望（人工 spotcheck 用）==")
    for qtype, fields in QUERY_EXPECT.items():
        lines.append(f"  {qtype}: 期望高权重 field ∈ {fields}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    safe_print(f"[完成] 写入 {OUT}")
    for ln in lines[:20]:
        safe_print(ln)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
