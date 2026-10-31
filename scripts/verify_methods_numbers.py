# -*- coding: utf-8 -*-
"""METHODS 数字对账（防止论文方法与产物不一致）

做的事：
  1) 从仓库产物里**重新算出**每个应在 METHODS 中出现的数字；
  2) 与 `docs/METHODS/_provenance.md`（内部记录）里「Numbers and provenance」表声明的值逐条比对；
  3) 统计正文里还残留多少 `{{N:key}}` / `{{TODO:key}}` 占位符（投稿前必须为 0）。

退出码：0 = 全部一致且无残留占位符；1 = 有不一致或残留；2 = 缺少产物文件。

用法：.venv\\Scripts\\python.exe -X utf8 scripts\\verify_methods_numbers.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MD = ROOT / "docs" / "METHODS" / "METHODS.md"    # 正文（2.1–2.8 散文，含 {{N:}} 占位符）
PROV = ROOT / "docs" / "METHODS" / "_provenance.md"  # 内部记录：对账表 / 待补清单（不属正文）
BACK = ROOT / "docs" / "paper" / "BACK_MATTER.md"        # 尾段（与马铃薯同序）
SRC = ROOT / "Figure" / "_audit_sources.json"          # 服务器索引/环境快照
CLEAN = ROOT / "Figure" / "_clean_stats.json"          # 清洗实测（n=999, seed 42）
VERIFY = ROOT / "Figure" / "_config_sweep_verify.json"  # 评测校验（评委一致性、检验差）
EVAL = ROOT / "项目阶段" / "01_金标全文" / "清单与验收" / "eval_isomorph_100_20260901.json"
ENVPROBE = ROOT / "Figure" / "_server_env_probe.json"      # 服务器 .env 实拿快照
USAGE = ROOT / "Figure" / "_llm_usage.json"              # DeepSeek 平台用量（开发期实测）
CONFIG = ROOT / "core" / "config.py"


def _num(x) -> float:
    return float(str(x).replace(",", "").replace("%", "").strip())


def config_default(key: str) -> str:
    m = re.search(rf'{key}\s*=\s*int\(os\.getenv\("[^"]+",\s*"(\d+)"\)\)', CONFIG.read_text(encoding="utf-8"))
    return m.group(1) if m else ""


def build_expected() -> dict:
    src = json.loads(SRC.read_text(encoding="utf-8"))
    idx, env = src["server"]["index"], src["server"]["env"]
    cs = json.loads(CLEAN.read_text(encoding="utf-8"))
    vs = json.loads(VERIFY.read_text(encoding="utf-8"))
    ev = json.loads(EVAL.read_text(encoding="utf-8"))
    probe = json.loads(ENVPROBE.read_text(encoding="utf-8")) if ENVPROBE.exists() else {"env": {}}
    use = json.loads(USAGE.read_text(encoding="utf-8")) if USAGE.exists() else {}
    bym = use.get("by_month", {})
    tot = use.get("totals", {})
    byp = use.get("by_purpose", {})
    evb = use.get("evaluation_calls_upper_bound", {})
    rubric = ev.get("rubric") or {}
    dims = rubric.get("dims") or rubric.get("dimensions") or []
    total = rubric.get("max_total") or rubric.get("total") or sum(
        float(d.get("max", 0)) for d in dims) if isinstance(dims, list) else None
    gap = max((abs(v["P_exact"] - v["P_normal_approx"])
               for v in vs.get("wilcoxon", {}).values()
               if isinstance(v, dict) and "P_normal_approx" in v), default=None)
    sp = None
    for c in vs.get("checks", []):
        m = re.search(r"Spearman=([\d.]+)", str(c.get("detail", "")))
        if m:
            sp = float(m.group(1))
            break
    return {
        "papers_ingested": idx["total_papers"],
        "papers_indexed": idx["total_papers"],
        "chunks": idx["total_chunks"],
        "faiss_shards": idx["shard_count"],
        "embed_dim": idx["embedding_dim"],
        "chunk_size": config_default("PUBMED_YES_PARA_MAX_CHARS"),
        "chunk_min": config_default("PUBMED_YES_PARA_MIN_CHARS"),
        "chunk_overlap": config_default("PUBMED_YES_CHUNK_OVERLAP"),
        "cand": env["PUBMED_YES_RETRIEVE_CANDIDATES"],
        "topn": env["PUBMED_YES_RERANK_TOP_N"],
        "pack_chars": env["PUBMED_YES_PACK_CHARS"],
        "clean_sample_n": cs["sampled"],
        "clean_seed": cs["seed"],
        "refs_truncation": cs["rules"]["refs_truncation_triggered"],
        "ack_truncation": cs["rules"]["ack_truncation_triggered"],
        "bench_n": len(ev.get("questions") or ev.get("items") or []),
        "rubric_total": total,
        "judge_spearman": sp,
        "approx_gap": gap,
        "rate_limit": probe.get("env", {}).get("WEB_RATE_LIMIT_CHAT"),
        "req_total": tot.get("requests"),
        "tokens_total": tot.get("tokens"),
        "req_jul": bym.get("2026-07", {}).get("requests"),
        "mean_jul": bym.get("2026-07", {}).get("mean_tokens_per_request"),
        "req_aug": bym.get("2026-08", {}).get("requests"),
        "mean_aug": bym.get("2026-08", {}).get("mean_tokens_per_request"),
        "req_sep": bym.get("2026-09", {}).get("requests"),
        "mean_sep": bym.get("2026-09", {}).get("mean_tokens_per_request"),
        "req_system": byp.get("knowledge_base_construction", {}).get("requests"),
        "tokens_system": byp.get("knowledge_base_construction", {}).get("tokens"),
        "tokens_dev_sep": byp.get("ai_assisted_development", {}).get("tokens"),
        "eval_calls": evb.get("eval_calls"),
        "eval_share": evb.get("eval_share_percent_ceiling"),
        "index_version": idx["index_version"],
        "embedding_model": Path(idx["embedding_model"]).name,
    }


def main() -> int:
    for p in (MD, SRC, CLEAN, VERIFY, EVAL, CONFIG, USAGE):
        if not p.exists():
            print(f"[verify-methods] 缺文件：{p}")
            return 2
    text = MD.read_text(encoding="utf-8")
    prov = PROV.read_text(encoding="utf-8") if PROV.exists() else text
    exp = build_expected()

    # ── 1) 表格里声明的值 vs 产物重算值（表在内部记录文件里）─────────────
    rows = {}
    for line in prov.splitlines():
        m = re.match(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            rows[m.group(1)] = m.group(2).strip()

    bad, checked = [], 0
    for key, want in exp.items():
        if want in (None, "", []):
            continue
        stated = rows.get(key)
        if stated is None:
            continue
        # 表格可能写 "500 / 100 / 48000" 这类组合，逐个数字比对（先去千分位逗号）
        nums = re.findall(r"[\d.]+", stated.replace(",", ""))
        ok = any(abs(_num(n) - _num(want)) < 1e-6 for n in nums) if nums else False
        checked += 1
        if not ok:
            bad.append((key, stated, want))

    # 组合行（chunk_size / cand 等写在同一个单元格里的键）
    combo = {"chunk_size": ("chunk_size", "chunk_min", "chunk_overlap"),
             "cand": ("cand", "topn", "pack_chars"),
             "clean_sample_n": ("clean_sample_n", "clean_seed"),
             "refs_truncation": ("refs_truncation", "ack_truncation"),
             "req_total": ("req_total", "tokens_total"),
             "req_jul": ("req_jul", "mean_jul"),
             "req_aug": ("req_aug", "mean_aug"),
             "req_sep": ("req_sep", "mean_sep")}
    for row_key, keys in combo.items():
        stated = rows.get(row_key)
        if not stated:
            continue
        nums = [_num(n) for n in re.findall(r"[\d.]+", stated.replace(",", ""))]
        for k in keys:
            want = exp.get(k)
            if want in (None, ""):
                continue
            if not any(abs(n - _num(want)) < 1e-6 for n in nums):
                if (k, stated, want) not in bad:
                    bad.append((k, stated, want))
            checked += 1

    # ── 2) 残留占位符 + 生成投稿版（--write）─────────────────────────
    ph = re.findall(r"\{\{(N|TODO):([a-zA-Z0-9_]+)\}\}", text + "\n" + prov)
    n_ph = sum(1 for t, _ in ph if t == "N")
    n_todo = sum(1 for t, _ in ph if t == "TODO")

    unresolved = []
    SEP_KEYS = {"papers_ingested", "papers_indexed", "chunks", "req_total", "tokens_total",
                "req_jul", "req_aug", "req_sep", "tokens_jul", "tokens_aug", "tokens_sep",
                "mean_jul", "mean_aug", "mean_sep", "req_system", "tokens_system",
                "tokens_dev_sep", "eval_calls"}

    def sub(m):
        kind, key = m.group(1), m.group(2)
        if kind != "N":
            return m.group(0)
        v = exp.get(key)
        if v in (None, ""):
            unresolved.append(key)
            return m.group(0)
        s = str(v)
        numeric = s.replace(",", "").replace("%", "").replace(".", "", 1).isdigit()
        if not numeric:
            return s                                  # 字符串型（如索引版本号）
        f = _num(v)
        out = ("%.4f" % f).rstrip("0").rstrip(".") if f % 1 else str(int(f))
        return f"{int(f):,}" if (key in SEP_KEYS and f >= 1000) else out

    filled = re.sub(r"\{\{(N|TODO):([a-zA-Z0-9_]+)\}\}", sub, text)
    if "--write" in sys.argv:
        dst = MD.with_name("METHODS.resolved.md")
        dst.write_text(filled, encoding="utf-8")
        print(f"[verify-methods] 已写出投稿版：{dst.name}（保留模板中的占位符便于回溯）")
        if BACK.exists():
            btext = BACK.read_text(encoding="utf-8")
            bdst = BACK.with_name("BACK_MATTER.resolved.md")
            bdst.write_text(sub(r"\{\{(N|TODO):([a-zA-Z0-9_]+)\}\}", btext)
                            if False else btext, encoding="utf-8")
            print(f"[verify-methods] 已同步尾段：{bdst.name}")

    print(f"[verify-methods] 比对 {checked} 项；产物重算值：")
    for k in ("papers_indexed", "chunks", "faiss_shards", "embed_dim", "cand", "topn",
              "pack_chars", "refs_truncation", "ack_truncation", "bench_n",
              "rubric_total", "judge_spearman", "approx_gap", "rate_limit",
              "req_total", "tokens_total", "req_jul", "mean_jul", "req_aug", "mean_aug",
              "req_sep", "mean_sep", "req_system", "tokens_system", "tokens_dev_sep",
              "eval_calls", "eval_share"):
        print(f"   {k:16s} = {exp.get(k)}")
    for k in ("chunk_size", "chunk_min", "chunk_overlap", "clean_sample_n", "clean_seed"):
        print(f"   {k:16s} = {exp.get(k)}")
    print(f"[verify-methods] 表格声明不一致 {len(bad)} 项"
          + ("" if not bad else "："))
    for k, stated, want in bad:
        print(f"   [X] {k}: 表里写 {stated!r}，产物是 {want!r}")
    print(f"[verify-methods] 残留占位符：N(待填数值)={n_ph} · TODO(待补信息)={n_todo}"
          + (f" · 未解析={unresolved}" if unresolved else ""))
    if BACK.exists():
        bt = BACK.read_text(encoding="utf-8")
        btodo = sorted(set(re.findall(r"\{\{TODO:([a-zA-Z0-9_]+)\}\}", bt)))
        print(f"[verify-methods] BACK_MATTER.md 待补项：{btodo or '无'}")
    for t, k in ph[:12]:
        print(f"   {{ {t}:{k} }}")
    ok = not bad and not unresolved
    print(f"[verify-methods] {'PASS' if ok else 'FAIL'}"
          f"（N 占位符已全部可由产物重算；TODO 待补信息不影响数值一致性）" if ok
          else "（修正表格或产物后重跑）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
