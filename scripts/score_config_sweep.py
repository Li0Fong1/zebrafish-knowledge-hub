# -*- coding: utf-8 -*-
"""FigS4(C) 打分：用项目自带的 7 维量表（共 10 分）对 6 个配置的答案盲评。

- 每题的 6 份答案用固定随机种子打乱后标为 A–F，评委看不到配置名（盲评）
- 维度与满分完全取自 项目阶段/01_金标全文/清单与验收/eval_isomorph_100_20260901.json
- 与该维度无关的题目允许按满分计（避免类别差异污染配置比较）
- 评委要求先排序再打分、满分罕见、同题必须拉开差距；0.25 分档
- 解析失败（键名不全/非法 JSON）时整题作废，**绝不补 0**

输入：Figure/_config_sweep_answers.json
输出：Figure/_config_sweep_scores[.model].json、logs/_config_sweep_judge_raw[.model].jsonl
用法：.venv\\Scripts\\python.exe -X utf8 scripts\\score_config_sweep.py
      SCORE_JUDGE_MODEL=deepseek-reasoner ...   # 换评委模型做交叉核对
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = os.getenv("SCORE_JUDGE_MODEL", "deepseek-chat")
PART = os.getenv("SCORE_PART", "").strip()          # 分片标签（并行跑时用）
QIDS = [int(x) for x in re.findall(r"\d+", os.getenv("SCORE_QIDS", ""))]  # 仅跑这些题
ANS = ROOT / "Figure" / "_config_sweep_answers.json"
SUFFIX = "" if MODEL == "deepseek-chat" else "_" + MODEL.replace("deepseek-", "")
if PART:
    SUFFIX += "_" + PART
OUT = ROOT / "Figure" / f"_config_sweep_scores{SUFFIX}.json"
RAW = ROOT / "logs" / f"_config_sweep_judge_raw{SUFFIX}.jsonl"

DIMS = [
    ("direct_first", "第一句直接回答问题（不要先铺垫背景）", 2),
    ("factor_structure", "分型/结构清晰，条目化组织，层次分明", 2),
    ("ge_formula", "性状类问题给出遗传与环境（G+E）的等价表述或明确区分", 1),
    ("inline_cite", "行内 [n] 引用且与文末参考文献一一对应", 2),
    ("ref_count", "相关参考文献数量适中（约 8–15 条）", 1),
    ("recency", "含近年文献（近十年可见），不是陈旧文献堆叠", 1),
    ("no_review_shell", "没有综述壳/与问题无关的章节堆砌", 1),
]
MAXTOTAL = sum(d[2] for d in DIMS)


def _env(key: str, default: str = "") -> str:
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return os.getenv(key, default)


def judge_call(prompt: str, model: str = MODEL) -> str:
    key = _env("LLM_API_KEY")
    base = _env("LLM_API_BASE", "https://api.deepseek.com/v1").rstrip("/")
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
    }
    if "reasoner" not in model:                       # reasoner 不支持 JSON 模式参数
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        base + "/chat/completions", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return d["choices"][0]["message"]["content"]


def norm_keys(obj: dict) -> dict:
    """容错：评委可能把键写成「答案 D」「D：」「answer A」，统一抽成单个字母。"""
    out = {}
    for k, v in (obj or {}).items():
        m = re.search(r"[A-F]", str(k).upper())
        if m and isinstance(v, dict):
            out.setdefault(m.group(0), v)
    return out


def build_prompt(q: str, labeled: dict) -> str:
    rub = "\n".join(f"- {k}（0–{m} 分）：{desc}" for k, desc, m in DIMS)
    blocks = "\n\n".join(f"### 答案 {lab}\n{txt[:6000]}" for lab, txt in labeled.items())
    first = DIMS[0][0]
    n = len(labeled)
    return f"""你是斑马鱼基因知识库的严格评审。下面同一道题有 {n} 份回答（已随机标号，顺序无意义）。

题目：{q}

第一步（必做，在心里完成）：把这 {n} 份回答两两对比一遍，排出优劣顺序。
第二步：按下列 7 个维度**逐份独立打分**；某个维度与本题无关时按满分计。

评分维度（每份回答在每个维度不能超过其满分）：
{rub}

打分锚点（每档可用 **0.25 分**细化，总分为各维度之和）：
- 满分：该维度完全满足，找不出可扣之处
- 0.75 倍满分：基本满足，有小的遗漏
- 半满：只满足一半，需要读者自己补推断
- 0.25 倍满分：勉强沾边
- 0 分：未满足

强制要求（不遵守则结果无效）：
1. **满分应当罕见**：同一维度下最多 1–2 份回答可拿满分，其余必须扣分。
2. 同一道题的 {n} 份回答之间**必须拉开差距**；若出现总分完全相同，请重新比较并区分（除非逐字等价）。
3. 只看回答本身（是否直接、是否条目化、引用是否与文末对应、参考文献数量与新旧、有无无关章节堆砌），不因语气或长度加分。

{blocks}

只输出 JSON，键就是答案标号字母本身（例如 "A"、"B"，不要加「答案」等前缀），格式：
{{"A": {{"{first}": 0, ...}}, "B": {{...}}, ...}}
所有 {n} 份回答都要出现，每份包含全部 7 个维度，取值为 0 / 0.25 / 0.5 / … 不超过该维度满分。"""


def main() -> int:
    doc = json.loads(ANS.read_text(encoding="utf-8"))
    recs = [r for r in doc["records"] if r.get("ok")]
    configs = sorted({r["config"] for r in recs})
    by_q: dict = {}
    for r in recs:
        by_q.setdefault(int(r["qid"]), {})[r["config"]] = r

    rows: list = []
    skipped: list = []
    RAW.parent.mkdir(parents=True, exist_ok=True)
    if QIDS:
        print(f"[judge] 仅跑题号 {QIDS}（分片 {PART or '-'}）", flush=True)
    with RAW.open("a", encoding="utf-8") as fh:
        for qid in sorted(by_q):
            if QIDS and qid not in QIDS:
                continue
            have = by_q[qid]
            if len(have) < len(configs):
                print(f"[judge] q{qid} 配置不全({len(have)}/{len(configs)})，跳过", flush=True)
                continue
            labels = list("ABCDEFG"[:len(configs)])
            order = labels[:]
            random.Random(1000 + qid).shuffle(order)
            l2c = dict(zip(order, configs))
            labeled = {lab: have[c]["answer"] for lab, c in l2c.items()}
            q = have[configs[0]]["q"]

            parsed, last = None, ""
            for attempt in (1, 2):
                try:
                    last = judge_call(build_prompt(q, labeled))
                    cand = norm_keys(json.loads(last))
                    if set(cand) >= set(labels):
                        parsed = cand
                        break
                    print(f"[judge] q{qid} 第{attempt}次键名不全: {sorted(cand)}", flush=True)
                except Exception as e:                        # noqa: BLE001
                    print(f"[judge] q{qid} 第{attempt}次失败 {type(e).__name__} "
                          f"{str(e)[:120]}", flush=True)
            fh.write(json.dumps({"qid": qid, "l2c": l2c, "raw": last},
                                ensure_ascii=False) + "\n")
            if not parsed:
                print(f"[judge] q{qid} 解析失败，整题作废（不补 0）", flush=True)
                skipped.append(qid)
                continue

            for lab, c in l2c.items():
                got = parsed.get(lab) or {}
                dims = {}
                for k, _desc, m in DIMS:
                    try:
                        v = round(float(got.get(k, 0)) * 4) / 4.0     # 0.25 分档
                    except Exception:                             # noqa: BLE001
                        v = 0.0
                    dims[k] = max(0.0, min(float(m), v))
                rows.append({"qid": qid, "config": c, "dims": dims,
                             "total": round(sum(dims.values()) * 4) / 4.0})
            avg = sum(r["total"] for r in rows[-len(configs):]) / len(configs)
            print(f"[judge] q{qid:>3} 完成 均分={avg:.2f}", flush=True)

    OUT.write_text(json.dumps({"dims": [d[0] for d in DIMS], "max_total": MAXTOTAL,
                               "model": MODEL, "skipped_qids": skipped,
                               "rows": rows}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n[judge] 共 {len(rows)} 条 -> {OUT}" + (f"（作废题 {skipped}）" if skipped else ""))
    for c in configs:
        v = sorted(r["total"] for r in rows if r["config"] == c)
        if v:
            n = len(v)
            med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
            print(f"  {c:<15} n={n:3d} mean={sum(v)/n:5.2f} median={med:5.2f} "
                  f"min={min(v)} max={max(v)}")
    spreads = []
    for qid in sorted({r["qid"] for r in rows}):
        t = [r["total"] for r in rows if r["qid"] == qid]
        spreads.append(max(t) - min(t))
    if spreads:
        zero = sum(1 for s in spreads if s == 0)
        print(f"  同题极差: 均值 {sum(spreads)/len(spreads):.2f}  为 0 的题数 {zero}/{len(spreads)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
