# -*- coding: utf-8 -*-
"""大样本同构评测 + 自动指标 + 盲评包。

用法：
  HUB_BASE=http://127.0.0.1:8080 python scripts/run_eval_isomorph.py
  EVAL_LIMIT=40 python scripts/run_eval_isomorph.py          # 抽样
  EVAL_PAIRS_ONLY=1 python scripts/run_eval_isomorph.py      # 只跑同构斑马鱼题
  POTATO_LIVE=1 python scripts/run_eval_isomorph.py          # 另拉马铃薯同构题（慢）
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "项目阶段" / "01_金标全文" / "清单与验收" / "eval_isomorph_100_20260901.json"
OUT_DIR = Path(os.getenv("EVAL_OUT_DIR", str(ROOT / "logs" / "eval_isomorph")))
BASE = os.getenv("HUB_BASE", "http://127.0.0.1:8080")
POTATO_DUMP = ROOT / "logs" / "_potato_qa_dump.json"

SCOPE = {
    "trait": ("hub", "partial"),
    "lit": ("hub", "partial"),
    "method": ("hub", "partial"),
    "compare": ("hub", "partial"),
    "gene": ("gene_db", "partial"),
    "expr": ("gene_db", "partial"),
    "asm": ("gene_db", "partial"),
    "id": ("id_map", "partial"),
    "fact": ("gene_db", "partial"),
}

REVIEW_PAT = re.compile(
    r"(核心结论|一是[，,：:]|二是[，,：:]|三是[，,：:]|四是[，,：:]|五是[，,：:]|"
    r"以下逐层展开|综上所述综述|文献综述|"
    r"#{1,3}\s*[0-9一二三四五六七八九十]+[\.、]\s*|FELASA|安乐死|处死方法)",
    re.I,
)
GE_PAT = re.compile(
    r"(表型\s*=\s*基因型|G\s*[×xX]\s*E|基因型\s*\+\s*环境|"
    r"遗传.{0,8}环境.{0,8}互作|遗传\s*\+\s*环境|G\s*\+\s*E)"
)


def req(path: str, body: dict, timeout: int = 300) -> tuple[int, dict | str]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    r = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def extract_ref_years(ans: str) -> list[int]:
    years = []
    # 文末参考文献块优先
    m = re.search(r"参考文献[：:]?\s*([\s\S]+)$", ans)
    blob = m.group(1) if m else ans
    for y in re.findall(r"\b(19[89]\d|20[0-2]\d)\b", blob):
        yi = int(y)
        if 1980 <= yi <= datetime.now().year + 1:
            years.append(yi)
    return years


def auto_metrics(ans: str, qtype: str) -> dict:
    cites = sorted({int(x) for x in re.findall(r"\[(\d{1,2})\]", ans or "")})
    has_ref = bool(re.search(r"参考文献", ans or ""))
    years = extract_ref_years(ans or "")
    now = datetime.now().year
    recent = [y for y in years if y >= now - 10]
    first = (ans or "").strip().splitlines()[0] if (ans or "").strip() else ""
    review_hits = REVIEW_PAT.findall(ans or "")
    return {
        "chars": len(ans or ""),
        "inline_cites": cites,
        "n_inline_unique": len(cites),
        "has_参考文献": has_ref,
        "ge_formula": bool(GE_PAT.search(ans or "")) if qtype == "trait" else None,
        "reviewish": bool(review_hits),
        "review_hits": list({h if isinstance(h, str) else h[0] for h in review_hits})[:8],
        "ref_years": years[:40],
        "n_ref_years": len(years),
        "pct_recent_10y": round(len(recent) / len(years), 3) if years else None,
        "median_year": sorted(years)[len(years) // 2] if years else None,
        "first_line": first[:120],
        "first_line_ok": bool(first) and not first.startswith("#") and "核心结论" not in first,
    }


def grade_hard(item: dict, ans: str, scope: str) -> list[str]:
    fails: list[str] = []
    low = ans or ""
    if len(low.strip()) < 40:
        fails.append("answer_too_short")
    for m in item.get("must") or []:
        if m == "[":
            if not re.search(r"\[\d{1,2}\]", low):
                fails.append("missing_inline_cite")
        elif m.lower() not in low.lower():
            fails.append(f"missing_must:{m}")
    for f in item.get("forbid") or []:
        if f and f in low:
            fails.append(f"hit_forbid:{f}")
    if item.get("type") in ("trait", "lit", "method", "compare") and scope == "hub":
        if re.search(r"\[\d{1,2}\]", low) and not re.search(r"参考文献", low):
            fails.append("missing_参考文献")
        met = auto_metrics(low, item.get("type") or "")
        if met["reviewish"]:
            fails.append("reviewish_shell")
        if item.get("type") == "trait" and met["ge_formula"] is False:
            fails.append("missing_GE_formula")
    return fails


def load_potato_cache() -> dict[str, str]:
    if not POTATO_DUMP.exists():
        return {}
    data = json.loads(POTATO_DUMP.read_text(encoding="utf-8"))
    out = {}
    for row in data.get("qa") or []:
        q = (row.get("question") or "").strip()
        ans = ((row.get("assistant") or {}).get("content") or "").strip()
        if q and ans:
            out[q] = ans
    return out


def potato_live_ask(questions: list[str]) -> dict[str, str]:
    """可选：调用现成脚本逻辑的精简版。失败则返回空。"""
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "ask_potato", ROOT / "logs" / "_ask_potato_agent.py"
        )
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader
        # 不直接 exec 全脚本；仅提示用户用 POTATO_LIVE
        return {}
    except Exception:
        return {}


def build_blind_pack(pairs_rows: list[dict], rubric: dict, out_path: Path) -> None:
    """同构题盲评包：系统标签打乱为 A/B。"""
    rng = random.Random(20260901)
    sheets = []
    for row in pairs_rows:
        z = row.get("zebrafish_answer") or ""
        p = row.get("potato_answer") or ""
        if not z:
            continue
        # 若无马铃薯答，仍出盲评单，B 为空并标注
        order = ["zebrafish", "potato"]
        rng.shuffle(order)
        mapping = {"A": order[0], "B": order[1]}
        answers = {
            "A": z if mapping["A"] == "zebrafish" else p,
            "B": z if mapping["B"] == "zebrafish" else p,
        }
        sheets.append(
            {
                "pair_id": row["pair_id"],
                "type": row.get("type"),
                "zebrafish_q": row.get("zebrafish_q"),
                "potato_q": row.get("potato_q"),
                "label_map_secret": mapping,  # 评完后揭盲用
                "system_A_answer": answers["A"],
                "system_B_answer": answers["B"],
                "potato_missing": not bool(p),
                "score_form": {
                    dim["id"]: {"A": None, "B": None, "max": dim["max"]}
                    for dim in rubric.get("dimensions") or []
                },
            }
        )
    # 对外盲评文件去掉 secret；密钥另存
    public = []
    secrets = {}
    for s in sheets:
        secrets[s["pair_id"]] = s["label_map_secret"]
        pub = {k: v for k, v in s.items() if k != "label_map_secret"}
        public.append(pub)
    out_path.write_text(json.dumps({"rubric": rubric, "sheets": public}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_path.parent / "_blind_label_secrets.json").write_text(
        json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 人类可读
    md = ["# 盲评打分表（同构对照）", "", "评分：每维 0=差 … 满分=好。先看题，再看系统 A/B，勿猜测来源。", ""]
    for s in public:
        md.append(f"## {s['pair_id']} · {s.get('type')}")
        md.append(f"- 斑马鱼题：{s.get('zebrafish_q')}")
        md.append(f"- 马铃薯题：{s.get('potato_q')}")
        if s.get("potato_missing"):
            md.append("- 注意：马铃薯侧暂无抓取答案，请只评有正文的一侧或稍后补抓。")
        md.append("")
        md.append("### 系统 A")
        md.append(s.get("system_A_answer") or "（空）")
        md.append("")
        md.append("### 系统 B")
        md.append(s.get("system_B_answer") or "（空）")
        md.append("")
        md.append("打分：direct_first / factor_structure / ge_formula / inline_cite / ref_count / recency / no_review_shell")
        md.append("")
    (out_path.parent / "盲评打分表.md").write_text("\n".join(md), encoding="utf-8")


def main() -> int:
    if not EVAL.exists():
        # build first
        from subprocess import check_call
        import sys

        check_call([sys.executable, str(ROOT / "scripts" / "build_eval_isomorph_100.py")])
    data = json.loads(EVAL.read_text(encoding="utf-8"))
    questions = list(data["questions"])
    pairs = list(data.get("isomorphic_pairs") or [])
    if os.getenv("EVAL_PAIRS_ONLY", "").strip() in ("1", "true", "yes"):
        pair_qs = {p["zebrafish"] for p in pairs}
        questions = [q for q in questions if q["q"] in pair_qs]
    limit = os.getenv("EVAL_LIMIT")
    if limit:
        questions = questions[: int(limit)]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    potato_cache = load_potato_cache()

    rows = []
    pass_n = fail_n = 0
    for q in questions:
        typ = q.get("type") or "hub"
        scope, depth = SCOPE.get(typ, ("hub", "partial"))
        t0 = time.time()
        code, body = req(
            "/api/chat",
            {"question": q["q"], "answer_scope": scope, "answer_depth": depth},
            timeout=300,
        )
        sec = round(time.time() - t0, 1)
        ans = (body.get("answer") if isinstance(body, dict) else "") or ""
        fails = []
        if code != 200:
            fails.append(f"http_{code}")
        fails.extend(grade_hard(q, ans, scope))
        met = auto_metrics(ans, typ)
        ok = not fails
        pass_n += int(ok)
        fail_n += int(not ok)
        rows.append(
            {
                "id": q["id"],
                "type": typ,
                "scope": scope,
                "pair_id": q.get("pair_id"),
                "q": q["q"],
                "ok": ok,
                "sec": sec,
                "fails": fails,
                "metrics": met,
                "answer": ans,
            }
        )
        tag = "PASS" if ok else "FAIL"
        print(
            f"[{tag}] #{q['id']} {typ} {sec}s cites={met['n_inline_unique']} "
            f"recent={met['pct_recent_10y']} review={met['reviewish']} fails={fails}",
            flush=True,
        )

    # pair bundle
    z_by_q = {r["q"]: r for r in rows}
    pair_rows = []
    for p in pairs:
        zr = z_by_q.get(p["zebrafish"])
        pair_rows.append(
            {
                "pair_id": p["pair_id"],
                "type": p.get("type"),
                "potato_q": p["potato"],
                "zebrafish_q": p["zebrafish"],
                "zebrafish_answer": (zr or {}).get("answer") or "",
                "zebrafish_metrics": (zr or {}).get("metrics"),
                "potato_answer": potato_cache.get(p["potato"], ""),
                "potato_metrics": auto_metrics(potato_cache.get(p["potato"], ""), p.get("type") or "")
                if potato_cache.get(p["potato"])
                else None,
            }
        )

    build_blind_pack(pair_rows, data.get("rubric") or {}, OUT_DIR / "blind_pack.json")

    # summary
    hub_rows = [r for r in rows if r["scope"] == "hub"]
    recent_vals = [r["metrics"]["pct_recent_10y"] for r in hub_rows if r["metrics"]["pct_recent_10y"] is not None]
    review_rate = sum(1 for r in hub_rows if r["metrics"]["reviewish"]) / max(1, len(hub_rows))
    cite_avg = sum(r["metrics"]["n_inline_unique"] for r in hub_rows) / max(1, len(hub_rows))
    ge_ok = [
        r
        for r in hub_rows
        if r["type"] == "trait" and r["metrics"].get("ge_formula") is True
    ]
    trait_n = sum(1 for r in hub_rows if r["type"] == "trait")

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n": len(rows),
        "pass": pass_n,
        "fail": fail_n,
        "pass_rate": round(pass_n / max(1, len(rows)), 4),
        "hub_n": len(hub_rows),
        "hub_avg_unique_cites": round(cite_avg, 3),
        "hub_reviewish_rate": round(review_rate, 4),
        "hub_avg_pct_recent_10y": round(sum(recent_vals) / len(recent_vals), 4) if recent_vals else None,
        "trait_ge_formula_rate": round(len(ge_ok) / max(1, trait_n), 4),
        "gates": {
            "pass_rate_ge_0_85": (pass_n / max(1, len(rows))) >= 0.85,
            "reviewish_rate_le_0_15": review_rate <= 0.15,
            "avg_cites_ge_3": cite_avg >= 3.0,
            "recent10y_ge_0_35": (sum(recent_vals) / len(recent_vals) if recent_vals else 0) >= 0.35,
            "trait_ge_ge_0_8": (len(ge_ok) / max(1, trait_n)) >= 0.8,
        },
    }
    summary["all_auto_gates_pass"] = all(summary["gates"].values())

    result = {
        "summary": summary,
        "rows": [
            {k: v for k, v in r.items() if k != "answer"}
            | {"answer_sha1": hashlib.sha1((r.get("answer") or "").encode("utf-8")).hexdigest()[:12],
               "head": (r.get("answer") or "")[:220]}
            for r in rows
        ],
        "pairs": pair_rows,
    }
    out_json = OUT_DIR / "_eval_isomorph_result.json"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # full answers separately
    (OUT_DIR / "_eval_isomorph_answers.json").write_text(
        json.dumps([{k: r[k] for k in ("id", "q", "type", "answer", "metrics", "fails")} for r in rows], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("SUMMARY", json.dumps(summary, ensure_ascii=False), flush=True)
    print("OUT", out_json, flush=True)
    print("BLIND", OUT_DIR / "盲评打分表.md", flush=True)
    return 0 if summary["all_auto_gates_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
