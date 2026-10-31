# -*- coding: utf-8 -*-
"""现网最小回归：health / 检索 500→100 / BLAST / 基因解析。

  python -m scripts.hub_regression_min
  python -m scripts.hub_regression_min --chat   # 另跑性状题与元问题（较慢）

环境：HUB_BASE 默认 http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("HUB_BASE", "http://127.0.0.1:8080")
OUT = ROOT / "logs" / "hub_regression_min.json"
BLAST_SEQ = "ATGGCTTCTAAGGAAGAAGATCTTCTGGAGGAGAAGGTGAAGGAGCTGCAGAAG"

CHECKS: list[dict] = []


def req(method: str, path: str, body=None, timeout: int = 240):
    headers = {"Accept": "application/json"}
    tok = ""
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("WEB_ACCESS_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if tok:
        headers["X-Access-Token"] = tok
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else {}, round(time.time() - t0, 1)
            except json.JSONDecodeError:
                return resp.status, {"_raw": raw[:2000]}, round(time.time() - t0, 1)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw) if raw else {}, round(time.time() - t0, 1)
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:2000]}, round(time.time() - t0, 1)
    except Exception as e:
        return 0, {"error": str(e)}, round(time.time() - t0, 1)


def check(name: str, ok: bool, detail: str) -> None:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}"[:240], flush=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--chat", action="store_true", help="加跑性状题 / 元问题")
    args = p.parse_args()

    code, health, _ = req("GET", "/api/health", timeout=15)
    check("health", code == 200 and isinstance(health, dict) and health.get("status") == "ok", f"status={code}")
    check("pubmed_ready", bool(isinstance(health, dict) and health.get("pubmed_ready")), str((health or {}).get("pubmed_ready")))
    check("mysql", bool(isinstance(health, dict) and health.get("mysql")), str((health or {}).get("mysql")))

    code, gene, sec = req("GET", "/api/tools/gene-resolve?q=p53", timeout=60)
    name = ""
    if isinstance(gene, dict):
        name = ((gene.get("canonical") or {}).get("gene_name") or "")
    check("gene_p53", code == 200 and name.lower() == "tp53", f"name={name} sec={sec}")

    code, blast, sec = req("POST", "/api/tools/blast", {"sequence": BLAST_SEQ, "top_k": 3}, timeout=90)
    nested = blast.get("result") if isinstance(blast, dict) else {}
    hits = []
    hit_count = 0
    if isinstance(blast, dict):
        hits = blast.get("hits") or (nested.get("hits") if isinstance(nested, dict) else []) or []
        hit_count = int((nested or {}).get("hit_count") or 0) if isinstance(nested, dict) else 0
    check("blast", code == 200 and (len(hits) >= 1 or hit_count >= 1), f"http={code} hits={len(hits)} hit_count={hit_count} sec={sec}")

    code, dbg, sec = req(
        "POST",
        "/api/retrieval/debug",
        {"query": "hif1aa zebrafish hypoxia function", "top_k": 100, "max_chars": 48000, "candidate_preview": 3},
        timeout=220,
    )
    pack = (dbg.get("hub_pack") or {}) if isinstance(dbg, dict) else {}
    cand = dbg.get("candidates_before_rerank") if isinstance(dbg, dict) else None
    after = dbg.get("after_rerank_top_n") if isinstance(dbg, dict) else None
    included = pack.get("papers_included")
    check(
        "retrieval_500_100",
        cand == 500 and after == 100 and int(included or 0) >= 80,
        f"cand={cand} after={after} included={included} sec={sec}",
    )

    if args.chat:
        code, chat, sec = req(
            "POST",
            "/api/chat",
            {"question": "斑马鱼的外形和什么有关？", "mode": "auto", "answer_scope": "hub", "answer_depth": "full"},
            timeout=240,
        )
        ans = (chat.get("answer") if isinstance(chat, dict) else "") or ""
        footer = 0
        if "参考文献" in ans:
            footer = len(re.findall(r"(?m)^\[\d{1,2}\]\s+", ans.split("参考文献")[-1]))
        gxe = any(s in ans for s in ("G×E", "GxE", "基因型 + 环境", "遗传 + 环境", "表型 ="))
        check("chat_trait", code == 200 and gxe and 20 <= footer <= 25, f"gxe={gxe} footer={footer} sec={sec}")

        code, meta, sec = req(
            "POST",
            "/api/chat",
            {"question": "你是什么模型？", "mode": "auto", "answer_scope": "hub", "answer_depth": "partial"},
            timeout=60,
        )
        mans = (meta.get("answer") if isinstance(meta, dict) else "") or ""
        check("chat_meta_no_refs", "参考文献" not in mans, f"sec={sec} head={mans[:80]!r}")

    fail = [c for c in CHECKS if not c["ok"]]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"base": BASE, "checks": CHECKS, "fail_n": len(fail)}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"SUMMARY fail={len(fail)}/{len(CHECKS)} -> {OUT}", flush=True)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
