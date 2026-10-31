# -*- coding: utf-8 -*-
"""短语义块试跑：旧参(~900/40) vs 新参(360/110/48/72) 小样本对比，不重嵌全库。

用法：
  python -m scripts.trial_short_chunk_compare
  python -m scripts.trial_short_chunk_compare --sample 40
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from rag.pubmed_yes_chunking import split_record_to_field_chunks

CORPUS = ROOT / "data" / "literature" / "pubmed_yes_corpus.jsonl"
OUT = ROOT / "解析文件" / "短语义块试跑对比.txt"
OUT_JSON = ROOT / "解析文件" / "短语义块试跑对比.json"

# 线上 oneshot 实际有效参（曾强制 max(chunk_size,900)）
OLD = dict(
    para_max_chars=900,
    para_min_chars=60,
    overlap=80,
    max_chunks=40,
    max_body_chars=60000,
    chunk_size=768,
)
# 试跑短语义块
NEW = dict(
    para_max_chars=360,
    para_min_chars=110,
    overlap=48,
    max_chunks=72,
    max_body_chars=60000,
    chunk_size=360,
)


def _sent_count(text: str) -> int:
    # 去掉 PMID 头再计句，避免前缀干扰
    t = text or ""
    if "\n" in t:
        t = t.split("\n", 1)[-1]
    parts = re.split(r"(?<=[。！？.!?;；])\s+", t.strip())
    return max(1, len([p for p in parts if p.strip()])) if t.strip() else 0


def _chunk_record(rec: dict, params: dict) -> list[dict]:
    return split_record_to_field_chunks(
        rec,
        chunk_size=params["chunk_size"],
        overlap=params["overlap"],
        max_body_chars=params["max_body_chars"],
        max_chunks=params["max_chunks"],
        split_mode="paragraph",
        para_max_chars=params["para_max_chars"],
        para_min_chars=params["para_min_chars"],
    )


def _stats(chunks: list[dict]) -> dict:
    if not chunks:
        return {"n": 0, "body_n": 0}
    texts = [c.get("text") or "" for c in chunks]
    lens = [len(t) for t in texts]
    sents = [_sent_count(t) for t in texts]
    body = [c for c in chunks if (c.get("field") or "") == "body"]
    body_lens = [len(c.get("text") or "") for c in body]
    body_sents = [_sent_count(c.get("text") or "") for c in body]
    fields: dict[str, int] = {}
    for c in chunks:
        f = str(c.get("field") or "mixed")
        fields[f] = fields.get(f, 0) + 1
    out = {
        "n": len(chunks),
        "chars_mean": round(statistics.mean(lens), 1),
        "chars_p50": int(statistics.median(lens)),
        "sents_mean": round(statistics.mean(sents), 2),
        "sents_p50": float(statistics.median(sents)),
        "sents_max": max(sents),
        "gt8_sents": sum(1 for s in sents if s > 8),
        "le5_sents": sum(1 for s in sents if s <= 5),
        "fields": fields,
        "body_n": len(body),
    }
    if body:
        out.update(
            {
                "body_chars_mean": round(statistics.mean(body_lens), 1),
                "body_chars_p50": int(statistics.median(body_lens)),
                "body_sents_mean": round(statistics.mean(body_sents), 2),
                "body_sents_p50": float(statistics.median(body_sents)),
                "body_gt8": sum(1 for s in body_sents if s > 8),
                "body_le5": sum(1 for s in body_sents if s <= 5),
            }
        )
    return out


def _recommend(so: dict, sn: dict, ratio: float) -> tuple[str, list[str]]:
    """返回 (GO|NO-GO|CONDITIONAL, 理由列表)。"""
    reasons: list[str] = []
    go = True
    # 目标：单块约 3～5 句、中位长度显著下降、块数可控上升
    new_body_sents = sn.get("body_sents_p50") or sn.get("sents_p50") or 99
    old_body_sents = so.get("body_sents_p50") or so.get("sents_p50") or 0
    new_p50 = sn.get("body_chars_p50") or sn.get("chars_p50") or 0
    old_p50 = so.get("body_chars_p50") or so.get("chars_p50") or 1
    le5_rate = (sn.get("body_le5") or sn.get("le5_sents") or 0) / max(1, sn.get("body_n") or sn.get("n") or 1)
    gt8_rate = (sn.get("body_gt8") or sn.get("gt8_sents") or 0) / max(1, sn.get("body_n") or sn.get("n") or 1)

    if 2.5 <= float(new_body_sents) <= 6.0:
        reasons.append(f"正文中位句数={new_body_sents}，落在约 3～5 句目标区（旧={old_body_sents}）。")
    else:
        go = False
        reasons.append(f"正文中位句数={new_body_sents}，偏离 3～5 句目标（旧={old_body_sents}）。")

    if new_p50 <= 450 and new_p50 < old_p50 * 0.7:
        reasons.append(f"正文中位长度 {old_p50}→{new_p50}，信息密度提升。")
    else:
        go = False
        reasons.append(f"正文中位长度变化不足或过短：{old_p50}→{new_p50}。")

    if 1.3 <= ratio <= 3.5:
        reasons.append(f"全文字段块数比(新/旧)={ratio}，可接受（max_chunks 40→72 吸收增量）。")
    elif ratio > 3.5:
        go = False
        reasons.append(f"块数比={ratio} 过高，全库体积/嵌入成本风险大，建议先调参或分层切。")
    else:
        reasons.append(f"块数比={ratio} 偏低，短块收益有限。")

    if le5_rate >= 0.65 and gt8_rate <= 0.15:
        reasons.append(f"正文 ≤5 句占比={le5_rate:.0%}，>8 句占比={gt8_rate:.0%}，稀释块明显减少。")
    else:
        go = False
        reasons.append(f"句数分布未达标：≤5句={le5_rate:.0%}，>8句={gt8_rate:.0%}。")

    verdict = "GO" if go else "NO-GO"
    if go and ratio > 2.5:
        verdict = "CONDITIONAL-GO"
        reasons.append("块数增幅偏高：建议全库重嵌前先做 200～500 篇小 FAISS 检索抽检。")
    return verdict, reasons


def _cap_note(hit_new: int, n: int) -> str | None:
    if n <= 0:
        return None
    rate = hit_new / n
    if rate >= 0.4:
        return (
            f"注意：{hit_new}/{n} 篇触顶 max_chunks=72（长文尾部仍会被截断）；"
            "全库重嵌时可酌情提高到 96～120，或保持 72 以控制体积。"
        )
    return None


def main(sample_n: int = 40) -> None:
    rows: list[dict] = []
    with CORPUS.open(encoding="utf-8") as f:
        for line in f:
            if len(rows) >= sample_n:
                break
            rec = json.loads(line)
            body = (rec.get("body_text") or "").strip()
            if len(body) < 2000:
                continue
            rows.append(rec)

    old_all: list[dict] = []
    new_all: list[dict] = []
    per: list[dict] = []
    hit_old_cap = 0
    hit_new_cap = 0
    for rec in rows:
        o = _chunk_record(rec, OLD)
        n = _chunk_record(rec, NEW)
        if len(o) >= OLD["max_chunks"]:
            hit_old_cap += 1
        if len(n) >= NEW["max_chunks"]:
            hit_new_cap += 1
        old_all.extend(o)
        new_all.extend(n)
        per.append(
            {
                "pmid": rec.get("pmid"),
                "body_chars": len(rec.get("body_text") or ""),
                "old": _stats(o),
                "new": _stats(n),
            }
        )

    so, sn = _stats(old_all), _stats(new_all)
    ratio = round(sn["n"] / max(1, so["n"]), 2)
    body_ratio = round((sn.get("body_n") or 0) / max(1, so.get("body_n") or 1), 2)
    verdict, reasons = _recommend(so, sn, ratio)
    cap = _cap_note(hit_new_cap, len(rows))
    if cap:
        reasons.append(cap)
        if verdict == "GO":
            verdict = "CONDITIONAL-GO"

    # 估算全库（线上约 13534 篇 / 516476 块）
    prod_papers, prod_chunks = 13534, 516476
    est_new_chunks = int(prod_chunks * ratio)
    est_extra = est_new_chunks - prod_chunks

    summary = {
        "sample_papers": len(rows),
        "params_old": OLD,
        "params_new": NEW,
        "old_all_chunks": so,
        "new_all_chunks": sn,
        "chunk_count_ratio_new_over_old": ratio,
        "body_chunk_ratio_new_over_old": body_ratio,
        "papers_hitting_old_max_chunks": hit_old_cap,
        "papers_hitting_new_max_chunks": hit_new_cap,
        "estimate_full_corpus": {
            "prod_papers": prod_papers,
            "prod_chunks_now": prod_chunks,
            "est_chunks_after_reembed": est_new_chunks,
            "est_delta_chunks": est_extra,
        },
        "recommendation": verdict,
        "reasons": reasons,
        "faiss_note": "未写入任何 FAISS；生产目录 faiss_pubmed_yes 未触碰。",
        "per_paper": per,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "短语义块试跑对比（分字段切块 dry-run，未重嵌全库）",
        "=" * 60,
        f"样本：有正文且 body≥2000 的文献 {len(rows)} 篇（pubmed_yes_corpus.jsonl）",
        "切块入口：rag/pubmed_yes_chunking.py :: split_record_to_field_chunks（paragraph）",
        "",
        "参数对比：",
        f"  旧（线上有效）：max_chars={OLD['para_max_chars']} min={OLD['para_min_chars']} "
        f"overlap={OLD['overlap']} max_chunks={OLD['max_chunks']}",
        f"  新（试跑）：    max_chars={NEW['para_max_chars']} min={NEW['para_min_chars']} "
        f"overlap={NEW['overlap']} max_chunks={NEW['max_chunks']}",
        "  说明：已去掉 max(chunk_size,900) 下限；config 默认 PARA_MAX=360 / OVERLAP=48 / MAX_CHUNKS=72。",
        "",
        "全文字段块合计：",
        f"  旧：n={so['n']} 均长={so.get('chars_mean')} 中位长={so.get('chars_p50')} "
        f"句均={so.get('sents_mean')} 中位句={so.get('sents_p50')} "
        f">8句={so.get('gt8_sents')}/{so['n']} 字段={so.get('fields')}",
        f"  新：n={sn['n']} 均长={sn.get('chars_mean')} 中位长={sn.get('chars_p50')} "
        f"句均={sn.get('sents_mean')} 中位句={sn.get('sents_p50')} "
        f"≤5句={sn.get('le5_sents')}/{sn['n']} 字段={sn.get('fields')}",
        f"  块数比(新/旧)={ratio}；正文块比={body_ratio}",
        f"  触顶 max_chunks：旧 {hit_old_cap}/{len(rows)} 篇；新 {hit_new_cap}/{len(rows)} 篇",
        "",
        "仅正文块：",
        f"  旧：n={so.get('body_n')} 中位长={so.get('body_chars_p50')} 中位句={so.get('body_sents_p50')} "
        f">8句={so.get('body_gt8')}",
        f"  新：n={sn.get('body_n')} 中位长={sn.get('body_chars_p50')} 中位句={sn.get('body_sents_p50')} "
        f"≤5句={sn.get('body_le5')} >8句={sn.get('body_gt8')}",
        "",
        "全库体积粗估（按块数比外推，非正式）：",
        f"  当前生产 ≈ {prod_papers} 篇 / {prod_chunks} 块",
        f"  若全量重嵌 ≈ {est_new_chunks} 块（Δ≈{est_extra:+d}）",
        "",
        "示例（前 8 篇）：",
    ]
    for p in per[:8]:
        lines.append(
            f"  PMID {p['pmid']} body={p['body_chars']}: "
            f"旧 {p['old']['n']}块/正文{p['old'].get('body_n')} 中位句{p['old'].get('body_sents_p50')} → "
            f"新 {p['new']['n']}块/正文{p['new'].get('body_n')} 中位句{p['new'].get('body_sents_p50')}"
        )

    lines += [
        "",
        f"【建议】{verdict}",
    ]
    for r in reasons:
        lines.append(f"  - {r}")
    lines += [
        "",
        "后续动作：",
        "  - GO / CONDITIONAL-GO：可安排全库 oneshot 重嵌；重嵌前请用户书面确认。",
        "  - NO-GO：先改 PARA_* / max_chunks 再抽样，勿动生产 FAISS。",
        "  - 本次未写入 FAISS；生产目录 %LOCALAPPDATA%\\ZebrafishGeneQA\\faiss_pubmed_yes 未删除、未覆盖。",
        "",
        f"JSON：{OUT_JSON}",
    ]
    text = "\n".join(lines) + "\n"
    OUT.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=40, help="抽样篇数（20～50）")
    args = ap.parse_args()
    main(sample_n=max(20, min(50, int(args.sample))))
