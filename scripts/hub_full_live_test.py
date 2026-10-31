# -*- coding: utf-8 -*-
"""Hub 全量在线验收：health / stats / 检索调试 / 范围聊天 / 基因工具。"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("HUB_BASE", "http://127.0.0.1:8080")
OUT = ROOT / "logs" / "hub_full_live_test.json"

ERRORS: list[str] = []
WARNINGS: list[str] = []
RESULTS: dict = {"base": BASE, "checks": []}


def _token() -> str:
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("WEB_ACCESS_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("WEB_ACCESS_TOKEN", "")


def req(method: str, path: str, body: dict | None = None, timeout: int = 240) -> tuple[int, dict | str]:
    headers = {"Accept": "application/json"}
    tok = _token()
    if tok:
        headers["X-Access-Token"] = tok
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


def check(name: str, ok: bool, detail: str = "", *, warn: bool = False) -> None:
    RESULTS["checks"].append({"name": name, "ok": ok, "detail": detail, "warn": warn})
    tag = "OK" if ok else ("WARN" if warn else "FAIL")
    print(f"[{tag}] {name}: {detail}"[:240], flush=True)
    if ok:
        return
    if warn:
        WARNINGS.append(f"{name}: {detail}")
    else:
        ERRORS.append(f"{name}: {detail}")


def main() -> int:
    t_all = time.time()

    code, health = req("GET", "/api/health", timeout=20)
    check("health_http", code == 200, f"status={code}")
    if isinstance(health, dict):
        check("health_pubmed_ready", bool(health.get("pubmed_ready")), str(health.get("pubmed_warmup_error") or health.get("pubmed_ready")))
        check("health_mysql", bool(health.get("mysql")), str(health.get("mysql")))
        check("health_rag", bool(health.get("rag")), str(health.get("rag")))
        check("health_light", bool(health.get("light_model") or health.get("light_file")), str(health.get("light_model")))
        if health.get("chat_busy"):
            check("health_chat_idle", False, f"busy {health.get('chat_busy_seconds')}s preview={health.get('chat_busy_preview')}", warn=True)

    code, stats = req("GET", "/api/hub/stats", timeout=60)
    check("hub_stats_http", code == 200, f"status={code}")
    if isinstance(stats, dict):
        db = stats.get("database") or {}
        check("hub_stats_genes", int(db.get("gene_basic") or 0) > 1000, f"gene_basic={db.get('gene_basic')}")
        hub = stats.get("hub") or {}
        check("hub_stats_agents", bool(hub.get("agents")), f"agents={hub.get('agents')}")

    code, who = req("GET", "/api/auth/whoami", timeout=20)
    check("auth_whoami", code == 200, f"status={code} body={who}")

    code, gene = req("GET", "/api/tools/gene-resolve?q=p53", timeout=60)
    check(
        "gene_resolve_p53",
        code == 200 and isinstance(gene, dict) and bool(gene.get("resolved")),
        f"status={code} resolved={isinstance(gene, dict) and gene.get('resolved')} name={(gene.get('canonical') or {}).get('gene_name') if isinstance(gene, dict) else None}",
    )

    t0 = time.time()
    code, dbg = req(
        "POST",
        "/api/retrieval/debug",
        {"query": "zebrafish HIF", "top_k": 5, "max_chars": 2000, "candidate_preview": 5},
        timeout=180,
    )
    elapsed = round(time.time() - t0, 1)
    ok_dbg = (
        code == 200
        and isinstance(dbg, dict)
        and dbg.get("ok") is True
        and int(dbg.get("after_rerank_top_n") or 0) >= 1
        and int(dbg.get("shards_loaded") or 0) >= 1
    )
    check(
        "retrieval_debug",
        ok_dbg,
        f"status={code} ok={isinstance(dbg, dict) and dbg.get('ok')} ranked={isinstance(dbg, dict) and dbg.get('after_rerank_top_n')} shards={isinstance(dbg, dict) and dbg.get('shards_loaded')} sec={elapsed}",
    )
    if ok_dbg and elapsed > 90:
        check("retrieval_debug_latency", False, f"{elapsed}s too slow", warn=True)

    # 部分回答 · 仅文献（应走加速路径）
    t0 = time.time()
    code, chat = req(
        "POST",
        "/api/chat",
        {
            "question": "斑马鱼 hif 基因有几个旁系同源？",
            "mode": "auto",
            "answer_scope": "literature",
            "answer_depth": "partial",
        },
        timeout=300,
    )
    elapsed = round(time.time() - t0, 1)
    ans = (chat.get("answer") if isinstance(chat, dict) else "") or ""
    check(
        "chat_literature_partial",
        code == 200 and len(ans) >= 40,
        f"status={code} chars={len(ans)} scope={isinstance(chat, dict) and chat.get('answer_scope')} sec={elapsed}",
    )
    if code == 200 and elapsed > 120:
        check("chat_literature_partial_latency", False, f"{elapsed}s too slow", warn=True)

    # 基因范围
    t0 = time.time()
    code, chat2 = req(
        "POST",
        "/api/chat",
        {
            "question": "tp53 是什么基因",
            "mode": "auto",
            "answer_scope": "gene_db",
            "answer_depth": "partial",
        },
        timeout=180,
    )
    elapsed = round(time.time() - t0, 1)
    ans2 = (chat2.get("answer") if isinstance(chat2, dict) else "") or ""
    check(
        "chat_gene_db_partial",
        code == 200 and len(ans2) >= 20,
        f"status={code} chars={len(ans2)} sec={elapsed}",
    )

    # ID 映射
    code, chat3 = req(
        "POST",
        "/api/chat",
        {
            "question": "p53 对应的官方基因符号和 Ensembl ID",
            "mode": "auto",
            "answer_scope": "id_map",
            "answer_depth": "partial",
        },
        timeout=120,
    )
    ans3 = (chat3.get("answer") if isinstance(chat3, dict) else "") or ""
    check(
        "chat_id_map",
        code == 200 and ("tp53" in ans3.lower() or "ensembl" in ans3.lower() or "ENSDARG" in ans3),
        f"status={code} snippet={ans3[:120]!r}",
    )

    # retrieval_debug scope via chat
    code, chat4 = req(
        "POST",
        "/api/chat",
        {
            "question": "zebrafish mitochondria",
            "mode": "auto",
            "answer_scope": "retrieval_debug",
            "answer_depth": "partial",
        },
        timeout=180,
    )
    check(
        "chat_retrieval_debug_scope",
        code == 200 and isinstance(chat4, dict) and (chat4.get("answer_scope") == "retrieval_debug" or "检索" in (chat4.get("answer") or "")),
        f"status={code} scope={isinstance(chat4, dict) and chat4.get('answer_scope')}",
    )

    # workflow presets
    code, presets = req("GET", "/api/workflows/presets", timeout=30)
    check(
        "workflow_presets",
        code == 200 and isinstance(presets, (dict, list)),
        f"status={code} type={type(presets).__name__}",
    )

    RESULTS["elapsed_sec"] = round(time.time() - t_all, 1)
    RESULTS["errors"] = ERRORS
    RESULTS["warnings"] = WARNINGS
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSUMMARY errors={len(ERRORS)} warnings={len(WARNINGS)} elapsed={RESULTS['elapsed_sec']}s -> {OUT}", flush=True)
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
