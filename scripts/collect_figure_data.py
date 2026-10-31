# -*- coding: utf-8 -*-
"""汇总绘图所需的真实数据 → Figure/_data.json

数据来源（全部为项目内真实产物，不虚构）：
* 项目阶段/01_金标全文/清单与验收/eval_isomorph_100_20260901.json（100 题 + 20 对同构盲评）
* 项目阶段/01_金标全文/清单与验收/eval_30_questions_20260901.json（30 题固定题集）
* 项目阶段/01_金标全文/清单与验收/accept_latest.json（对齐验收 counts/checks）
* 项目阶段/01_金标全文/清单与验收/金标抽检_samples.json（金标合格率）
* 项目阶段/01_金标全文/清单与验收/向量化_五篇完整测试结果.json（索引 + 5 篇自命中）
* 解析文件/马铃薯完全对齐_最终结论.txt（管线参数与实测）
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                                        # noqa: E402

ACC = ROOT / "项目阶段" / "01_金标全文" / "清单与验收"
OUT_DIR = FC.FIG_DIR                                           # .env: FIG_OUTPUT_DIR
OUT_DIR.mkdir(parents=True, exist_ok=True)


def load(name: str):
    p = ACC / name
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


data: dict = {"generated_from": "项目内真实产物", "sources": []}

# ── 1. 对齐验收 counts ────────────────────────────────────────────────────
acc = load("accept_latest.json") or {}
if acc:
    data["sources"].append("accept_latest.json")
    data["alignment"] = {
        "updated_at": acc.get("updated_at"),
        "pass": acc.get("pass"),
        "checks": acc.get("checks") or {},
        "counts": acc.get("counts") or {},
    }

# ── 2. 评测题集 ──────────────────────────────────────────────────────────
ev100 = load("eval_isomorph_100_20260901.json") or {}
if ev100:
    data["sources"].append("eval_isomorph_100_20260901.json")
    qs = ev100.get("questions") or []
    data["eval"] = {
        "id": ev100.get("id"),
        "rubric": ev100.get("rubric") or {},
        "n_questions": len(qs),
        "types": dict(Counter(q.get("type") for q in qs).most_common()),
        "isomorph_pairs": len(ev100.get("isomorphic_pairs") or []),
        "pair_types": dict(Counter(p.get("type") for p in (ev100.get("isomorphic_pairs") or [])).most_common()),
    }

# ── 3. 金标抽检 ──────────────────────────────────────────────────────────
gold = load("金标抽检_samples.json") or {}
if gold:
    data["sources"].append("金标抽检_samples.json")
    data["gold"] = {
        "gold_bar": gold.get("gold_bar") or {},
        "yes": {k: v for k, v in (gold.get("yes") or {}).items()
                if k in ("total_json", "gold", "not_gold", "pct", "by_tier", "by_source")},
        "pdfku": {k: v for k, v in (gold.get("pdfku") or {}).items()
                  if k in ("total_json", "gold", "not_gold", "pct", "by_tier", "by_source")},
    }

# ── 4. 向量化五篇实测 ────────────────────────────────────────────────────
vec = load("向量化_五篇完整测试结果.json") or {}
if vec:
    data["sources"].append("向量化_五篇完整测试结果.json")
    man = vec.get("manifest") or {}
    cases = vec.get("cases") or []
    hub = vec.get("hub") or {}
    data["vector"] = {
        "generated_at": vec.get("generated_at"),
        "manifest": man,
        "gate": vec.get("gate") or {},
        "hub": {k: hub.get(k) for k in ("query_full", "source", "has_hits", "chars", "latency_ms")},
        "cases": [{"pmid": c.get("pmid"), "year": c.get("year"),
                   "self_rank": c.get("self_rank"), "journal": c.get("journal")} for c in cases],
    }

# ── 5. 管线参数（**优先取服务器权威快照**；仅当快照缺失时才回退到对齐结论文本）──
_auth_p = OUT_DIR / "_audit_sources.json"
_srv = {}
if _auth_p.is_file():
    _srv = (json.loads(_auth_p.read_text(encoding="utf-8")) or {}).get("server") or {}
_idx, _env = _srv.get("index") or {}, _srv.get("env") or {}
if _idx.get("total_papers"):
    _cand = _env.get("PUBMED_YES_RETRIEVE_CANDIDATES") or -1
    _topn = _env.get("PUBMED_YES_RERANK_TOP_N") or -1
    _pack = _env.get("PUBMED_YES_PACK_CHARS") or -1
    data["pipeline"] = {
        "papers": int(_idx["total_papers"]),
        "chunks": int(_idx["total_chunks"]),
        "split": _idx.get("chunking_mode") or _env.get("PUBMED_YES_SPLIT_MODE"),
        "retrieve": f"{_cand}->{_topn}",
        "config": (f"SPLIT={_env.get('PUBMED_YES_SPLIT_MODE')} CAND={_cand} TOPN={_topn} "
                   f"RERANK={_env.get('PUBMED_YES_RERANK')} PACK={_pack}"),
        "derived_from": "服务器权威快照 _audit_sources.json（非文档文本）",
    }
    data["sources"].append("服务器权威快照 _audit_sources.json（管线参数）")
else:
    txt = ROOT / "项目阶段" / "01_金标全文" / "文档" / "马铃薯完全对齐_最终结论.txt"
    if txt.is_file():
        t = txt.read_text(encoding="utf-8", errors="replace")
        data["sources"].append("项目阶段/01_金标全文/文档/马铃薯完全对齐_最终结论.txt")
        m = re.search(r"papers=(\d+)\s+chunks=(\d+)\s+split=(\w+)\s+([\d\->]+)", t)
        data["pipeline"] = {
            "papers": int(m.group(1)) if m else None,
            "chunks": int(m.group(2)) if m else None,
            "split": m.group(3) if m else None,
            "retrieve": m.group(4) if m else None,
            "config": None,          # 文档不含权威参数；不得臆述
            "spot_fields": {"body": 178, "title": 4, "abstract": 13, "meta": 5},
            "derived_from": "对齐结论文本（回落路径，参数以运行时为准）",
        }

# ── 6. 知识库/基因库规模（由服务器 MySQL 实测，见 _data_mysql.json） ─────
mp = OUT_DIR / "_data_mysql.json"
if mp.is_file():
    data["mysql"] = json.loads(mp.read_text(encoding="utf-8"))
    data["sources"].append("服务器 MySQL 实测（_data_mysql.json）")

# ── 7. 服务器权威快照（FAISS manifest / .env 参数 / MySQL 精确计数） ─────
ap = OUT_DIR / "_audit_sources.json"
if ap.is_file():
    srv = (json.loads(ap.read_text(encoding="utf-8")) or {}).get("server") or {}
    data["server_authority"] = {
        "index": srv.get("index") or {},
        "params": {k: v for k, v in (srv.get("env") or {}).items()
                   if k in ("PUBMED_YES_SPLIT_MODE", "PUBMED_YES_RETRIEVE_CANDIDATES",
                            "PUBMED_YES_RERANK_TOP_N", "PUBMED_YES_UNIQUE_PAPERS",
                            "PUBMED_YES_PACK_CHARS", "EMBEDDING_DIM",
                            "PUBMED_YES_SECTION_CHUNKING", "EMBEDDING_DEVICE")},
        "mysql": srv.get("mysql") or {},
    }
    data["sources"].append("服务器权威快照 _audit_sources.json（FAISS manifest + .env + MySQL）")

# ── 8. 口径订正：alignment.counts 中的 FAISS 字段若与权威清单不一致，一律以权威为准 ──
#    历史遗留：该块来自旧的对齐结论（13,534 篇 / 1,269 分片），若原样保留会被
#    verify_figures._known_numbers() 当作「合法数值」，等于给过时值发通行证。
try:
    _idx = (data.get("server_authority") or {}).get("index") or {}
    _tp = int(_idx.get("total_papers") or 0)
    _ts = int(_idx.get("shard_count") or 0)
    _c = ((data.get("alignment") or {}).get("counts") or {})
    if _c:
        if _tp and int(_c.get("faiss_papers") or 0) != _tp:
            _c["faiss_papers_legacy"] = _c.get("faiss_papers")
            _c["faiss_papers"] = _tp
        if _ts and int(_c.get("faiss_shards") or 0) != _ts:
            _c["faiss_shards_legacy"] = _c.get("faiss_shards")
            _c["faiss_shards"] = _ts
        data["alignment"]["counts"] = _c
except Exception as e:
    print("[warn] alignment.counts 订正失败:", e)

(OUT_DIR / "_data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
print("[ok] Figure/_data.json")
for k in ("alignment", "eval", "gold", "vector", "pipeline", "mysql", "server_authority"):
    v = data.get(k)
    print("  %-10s %s" % (k, "OK" if v else "MISSING"))
print("  counts:", json.dumps((data.get("alignment") or {}).get("counts"), ensure_ascii=False)[:220])
print("  eval:", json.dumps(data.get("eval"), ensure_ascii=False)[:250])
print("  pipeline:", json.dumps(data.get("pipeline"), ensure_ascii=False)[:200])
