# -*- coding: utf-8 -*-
"""FigS4(C) 数据采集：固定题集 × 真实可切的 6 个配置。

为什么这样设计
--------------
参考图 Fig. S4(C) 比较的是「切分方式 × 检索深度」下生成结果得分。
本项目索引的切分方式是**已固定**的（paragraph_section，360/110/48），
没有 chunk100/chunk200 三套索引，无法复刻该维度。
但服务器 Hub 的 /api/chat 支持**请求级真实参数**：
    mode         : general|rag|fusion|hub|db|gene|auto
    answer_depth : partial|full   → 真实改变打包预算与候选量（见 agent/hub_orchestrator.py）
所以本实验做的是同一件事的精神：**固定题集，改变真实可切的配置，比较生成结果得分**。

被排除的配置（实测输出为演示占位内容，不是真实产物，故不参与评分）：
    answer_scope=gene_db  → 返回 ENSDARG0000000000001 / ZDB-GENE-990415-270 等演示块
    mode=general          → 同样带演示 ID 块

输出：Figure/_config_sweep_answers.json（增量保存，可断点续跑）
用法：.venv\\Scripts\\python.exe -X utf8 scripts\\collect_config_sweep.py
      SWEEP_WORKERS=4 SWEEP_LIMIT=20   # 可调
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Figure" / "_config_sweep_answers.json"
BASE = os.getenv("HUB_BASE", "http://127.0.0.1:8080")
WORKERS = int(os.getenv("SWEEP_WORKERS", "4"))
LIMIT = int(os.getenv("SWEEP_LIMIT", "0")) or None
TIMEOUT = float(os.getenv("SWEEP_TIMEOUT", "420"))
RETRIES = int(os.getenv("SWEEP_RETRIES", "4"))

# 6 个真实配置（顺序 = 图上从左到右）
CONFIGS = [
    ("rag_partial",    {"mode": "rag",   "answer_depth": "partial"}),
    ("rag_full",       {"mode": "rag",   "answer_depth": "full"}),
    ("fusion_partial", {"mode": "fusion", "answer_depth": "partial"}),
    ("fusion_full",    {"mode": "fusion", "answer_depth": "full"}),
    ("hub_partial",    {"mode": "hub",   "answer_depth": "partial"}),
    ("hub_full",       {"mode": "hub",   "answer_depth": "full"}),
]

_LOCK = threading.Lock()


def questions() -> list[dict]:
    p = ROOT / "logs" / "eval_isomorph" / "_eval_isomorph_answers.json"
    rows = json.loads(p.read_text(encoding="utf-8"))
    qs = [{"id": int(r["id"]), "type": r.get("type"), "q": r["q"]} for r in rows]
    return qs[:LIMIT] if LIMIT else qs


def counts(answer: str) -> dict:
    """与项目既有评测一致的客观量（不参与打分，仅供核对）。"""
    cites = [int(x) for x in re.findall(r"\[(\d{1,2})\]", answer)]
    return {
        "chars": len(answer),
        "n_inline_cites": len(cites),
        "n_inline_unique": len(set(cites)),
        "has_ref_section": bool(re.search(r"参考文献|References", answer)),
        "n_ensdarg": len(set(re.findall(r"ENSDARG\d{11}", answer))),
        "n_pmid_like": len(set(re.findall(r"\b\d{7,8}\b", answer))),
    }


def one_call(q: dict, cfg: str, kw: dict) -> dict:
    body = {"question": q["q"]}
    body.update(kw)
    t0 = time.time()
    rec = {"qid": q["id"], "type": q.get("type"), "q": q["q"], "config": cfg,
           "ok": False, "sec": 0.0, "answer": ""}
    for attempt in range(1, RETRIES + 1):
        req = urllib.request.Request(
            BASE + "/api/chat", data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            ans = (data.get("answer") or "").strip()
            rec.update({"ok": bool(ans), "answer": ans,
                        "sec": round(time.time() - t0, 1)})
            rec.update(counts(ans))
            if ans:
                rec.pop("error", None)          # 重试成功后清掉旧错误标记
                return rec
            err = "empty answer"
        except Exception as e:                                # noqa: BLE001
            err = f"{type(e).__name__}: {str(e)[:140]}"
        rec["error"] = err
        if attempt < RETRIES:
            time.sleep(15 * attempt)          # 429/5xx 退避：15/30/45s
    rec["sec"] = round(time.time() - t0, 1)
    return rec


def load_done() -> dict:
    if OUT.is_file():
        try:
            d = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:                                     # noqa: BLE001
            return {"generated_at": None, "records": []}
        return d if isinstance(d, dict) else {"generated_at": None, "records": []}
    return {"generated_at": None, "records": []}


def save(doc: dict) -> None:
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(OUT)


def main() -> int:
    qs = questions()
    doc = load_done()
    done = {(int(r["qid"]), r["config"]) for r in doc["records"] if r.get("ok")}
    todo = [(q, c, kw) for q in qs for c, kw in CONFIGS if (q["id"], c) not in done]
    total = len(qs) * len(CONFIGS)
    print(f"[sweep] 题={len(qs)} 配置={len(CONFIGS)} 目标={total} 已有={len(done)} "
          f"待跑={len(todo)} workers={WORKERS}", flush=True)
    if not todo:
        print("[sweep] 全部已完成", flush=True)
        return 0

    t0 = time.time()
    n = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(one_call, q, c, kw) for q, c, kw in todo]
        for f in futs:
            rec = f.result()
            with _LOCK:
                doc["records"] = [r for r in doc["records"]
                                  if not (int(r["qid"]) == rec["qid"]
                                          and r["config"] == rec["config"])]
                doc["records"].append(rec)
                doc["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                n += 1
                if n % 3 == 0 or not rec["ok"]:
                    save(doc)
            flag = "ok " if rec["ok"] else "ERR"
            print(f"[sweep] {n}/{len(todo)} {flag} q{rec['qid']:>3} {rec['config']:<15} "
                  f"{rec['sec']:6.1f}s chars={rec.get('chars', 0):5d} "
                  f"cites={rec.get('n_inline_unique', 0):3d} "
                  f"{rec.get('error', '')}", flush=True)
    save(doc)
    ok = sum(1 for r in doc["records"] if r.get("ok"))
    print(f"[sweep] 完成 {time.time() - t0:.1f}s  成功 {ok}/{total} -> {OUT}", flush=True)
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
