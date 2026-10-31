# -*- coding: utf-8 -*-
"""Hub 细节全覆盖循环验收：每个工具端点 + 聊天范围 + 前端静态资源 + 边界输入。"""
from __future__ import annotations

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
OUT = ROOT / "logs" / "hub_detail_loop_test.json"

ERRORS: list[str] = []
WARNINGS: list[str] = []
CHECKS: list[dict] = []


def _token() -> str:
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("WEB_ACCESS_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("WEB_ACCESS_TOKEN", "")


def req(method: str, path: str, body=None, timeout: int = 240):
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
            return e.code, raw[:800]
    except Exception as e:
        return 0, str(e)


def check(name: str, ok: bool, detail: str = "", *, warn: bool = False) -> None:
    CHECKS.append({"name": name, "ok": ok, "detail": detail, "warn": warn})
    tag = "OK" if ok else ("WARN" if warn else "FAIL")
    print(f"[{tag}] {name}: {detail}"[:280], flush=True)
    if ok:
        return
    (WARNINGS if warn else ERRORS).append(f"{name}: {detail}")


def main() -> int:
    t0 = time.time()

    # --- hard gate: service must be up ---
    code, health0 = req("GET", "/api/health", timeout=15)
    if code != 200:
        check("service_up", False, f"Hub 未运行 status={code} body={health0}")
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(
            json.dumps({"errors": ERRORS, "checks": CHECKS}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("ABORT: Hub down", flush=True)
        return 2

    # --- static ---
    code, html = req("GET", "/", timeout=20)
    check("index_html", code == 200 and isinstance(html, str) and "斑马鱼" in html, f"status={code}")
    if isinstance(html, str):
        check("index_answerSyncHint", "answerSyncHint" in html, "present" if "answerSyncHint" in html else "missing")
        m = re.search(r"app\.js\?v=([\w.-]+)", html)
        check("index_app_cachebust", bool(m), f"v={m.group(1) if m else None}")
        check("index_auth_gate", "authGate" in html, "present" if "authGate" in html else "missing")

    for static_path in (
        "/static/js/app.js",
        "/static/js/payload-crypto.js",
        "/static/css/style.css",
    ):
        code, body = req("GET", static_path, timeout=20)
        check(f"static_{static_path.split('/')[-1]}", code == 200 and len(str(body)) > 50, f"status={code} len={len(str(body))}")

    # --- auth / tenants ---
    code, cfg = req("GET", "/api/auth/config", timeout=20)
    check("auth_config", code == 200 and isinstance(cfg, dict), f"status={code}")
    code, who = req("GET", "/api/auth/whoami", timeout=20)
    check("auth_whoami", code == 200 and isinstance(who, dict) and who.get("role"), f"{who}")
    code, tenant = req("GET", "/api/tenants/current", timeout=20)
    check("tenant_current", code == 200 and isinstance(tenant, dict), f"status={code} {tenant if isinstance(tenant, dict) else tenant}")

    # --- health / usage / stats ---
    code, health = req("GET", "/api/health", timeout=20)
    check("health", code == 200 and isinstance(health, dict) and health.get("pubmed_ready"), f"{health if not isinstance(health, dict) else {k: health.get(k) for k in ('pubmed_ready','mysql','rag','light_model','chat_busy')}}")
    code, usage = req("GET", "/api/usage", timeout=20)
    check("usage", code == 200, f"status={code}")
    code, stats = req("GET", "/api/hub/stats", timeout=60)
    check("hub_stats", code == 200 and isinstance(stats, dict) and int((stats.get("database") or {}).get("gene_basic") or 0) > 1000, f"status={code}")
    code, tstats = req("GET", "/api/tools/stats", timeout=60)
    check("tools_stats", code == 200, f"status={code}")

    # --- gene endpoints ---
    code, g = req("GET", "/api/gene/tp53", timeout=60)
    check("gene_tp53", code == 200 and isinstance(g, dict), f"status={code}")
    code, gs = req("GET", "/api/genes/search?q=mapk&limit=5", timeout=60)
    check("genes_search", code == 200 and isinstance(gs, dict) and len(gs.get("results") or gs.get("genes") or []) >= 1, f"status={code} keys={list(gs)[:8] if isinstance(gs, dict) else type(gs)}")

    # --- id tools ---
    code, r = req("GET", "/api/tools/gene-resolve?q=p53", timeout=60)
    check("gene_resolve", code == 200 and isinstance(r, dict) and r.get("resolved"), f"status={code}")
    code, r = req("GET", "/api/tools/id-convert?identifier=p53", timeout=60)
    check("id_convert_get", code == 200, f"status={code}")
    code, r = req("GET", "/api/tools/id-convert/batch?ids=p53,30590,shh", timeout=60)
    check("id_convert_batch_get", code == 200, f"status={code}")
    code, r = req("POST", "/api/tools/id-convert/batch", {"identifiers": ["p53", "ZDB-GENE-990415-270"]}, timeout=60)
    check("id_convert_batch_post", code == 200, f"status={code}")
    code, r = req("POST", "/api/tools/id-convert/text", {"text": "p53\nshh\n"}, timeout=60)
    check("id_convert_text", code == 200, f"status={code}")

    # --- analysis tools ---
    sample = "ATGGCTTCTAAGGAAGAAGATCTTCTGGAGGAGAAGGTGAAGGAGCTGCAGAAG"
    code, r = req("POST", "/api/tools/blast", {"sequence": sample, "top_k": 3}, timeout=120)
    check("blast", code == 200 and isinstance(r, dict), f"status={code} success={isinstance(r, dict) and r.get('success')}")
    if isinstance(r, dict) and r.get("success") is False:
        check("blast_success", False, str(r.get("error") or r)[:200], warn=True)

    code, r = req("GET", "/api/tools/enrichment?genes=tp53,hsp70,myl7", timeout=120)
    check("enrichment", code == 200, f"status={code}")
    code, r = req("GET", "/api/tools/kegg?q=MAPK", timeout=60)
    check("kegg", code == 200, f"status={code}")
    code, r = req("GET", "/api/tools/sequence?gene=tp53", timeout=90)
    check("sequence", code == 200, f"status={code}")
    code, r = req("GET", "/api/tools/annotate?gene=tp53", timeout=120)
    check("annotate", code == 200, f"status={code}")
    code, r = req("GET", "/api/knowledge/sources", timeout=30)
    check("knowledge_sources", code == 200, f"status={code}")

    # --- retrieval debug details ---
    code, dbg = req(
        "POST",
        "/api/retrieval/debug",
        {"query": "zebrafish tp53", "top_k": 5, "max_chars": 2500, "candidate_preview": 8},
        timeout=180,
    )
    ok = code == 200 and isinstance(dbg, dict) and dbg.get("ok") is True
    check(
        "retrieval_debug",
        ok,
        f"status={code} ranked={isinstance(dbg, dict) and dbg.get('after_rerank_top_n')} shards={isinstance(dbg, dict) and dbg.get('shards_loaded')} method={isinstance(dbg, dict) and dbg.get('rerank_method')}",
    )
    if ok:
        pack = dbg.get("hub_pack") or {}
        check("retrieval_pack", int(pack.get("chunks_included") or 0) >= 1, f"pack={pack.get('chunks_included')} chars={pack.get('total_chars')}")
        check("retrieval_ranked_rows", len(dbg.get("ranked") or []) >= 1, f"n={len(dbg.get('ranked') or [])}")

    # PMID exact path
    code, dbg2 = req(
        "POST",
        "/api/retrieval/debug",
        {"query": "PMID:30126845 zebrafish HIF", "top_k": 5, "max_chars": 2000},
        timeout=180,
    )
    check(
        "retrieval_pmid_query",
        code == 200 and isinstance(dbg2, dict) and dbg2.get("ok") is True,
        f"status={code} ok={isinstance(dbg2, dict) and dbg2.get('ok')}",
    )

    # empty query should 400
    code, bad = req("POST", "/api/retrieval/debug", {"query": "  ", "top_k": 5}, timeout=30)
    check("retrieval_empty_query_400", code in (400, 422), f"status={code}")

    # --- workflows ---
    code, presets = req("GET", "/api/workflows/presets", timeout=30)
    check("workflow_presets", code == 200 and isinstance(presets, dict) and presets.get("preset_by_scope"), f"status={code}")
    if isinstance(presets, dict):
        pbs = presets.get("preset_by_scope") or {}
        for scope in ("literature", "gene_db", "id_map", "hub", "retrieval_debug"):
            check(f"preset_scope_{scope}", scope in pbs, f"{scope}->{pbs.get(scope)}" if scope in pbs else f"missing in {list(pbs)}")

    code, runs = req("GET", "/api/workflows/runs?limit=5", timeout=30)
    check("workflow_runs_list", code == 200, f"status={code}")

    code, wr = req(
        "POST",
        "/api/workflows/run",
        {
            "preset_slug": "id_only",
            "input_text": "p53",
            "answer_scope": "id_map",
            "answer_depth": "partial",
        },
        timeout=120,
    )
    check(
        "workflow_run_id_only",
        code == 200 and isinstance(wr, dict) and (wr.get("answer_scope") == "id_map" or (wr.get("chat") or {}).get("answer_scope") == "id_map"),
        f"status={code} scope={isinstance(wr, dict) and wr.get('answer_scope')}",
    )

    # --- chat scopes ---
    scopes = [
        ("literature", "斑马鱼 sox9b 相关文献", 180),
        ("gene_db", "hsp70 基因功能", 120),
        ("id_map", "shh 和 shha 是同一个基因吗", 90),
        ("retrieval_debug", "zebrafish heart development", 180),
    ]
    for scope, q, to in scopes:
        code, chat = req(
            "POST",
            "/api/chat",
            {"question": q, "mode": "auto", "answer_scope": scope, "answer_depth": "partial"},
            timeout=to,
        )
        ans = (chat.get("answer") if isinstance(chat, dict) else "") or ""
        check(
            f"chat_{scope}",
            code == 200 and len(ans) >= 15,
            f"status={code} chars={len(ans)} scope={isinstance(chat, dict) and chat.get('answer_scope')}",
        )

    # edge: off-topic / empty
    code, chat = req(
        "POST",
        "/api/chat",
        {"question": "今天天气怎么样", "mode": "auto", "answer_scope": "literature", "answer_depth": "partial"},
        timeout=60,
    )
    ans = (chat.get("answer") if isinstance(chat, dict) else "") or ""
    check("chat_offtopic_handled", code == 200 and len(ans) >= 8, f"status={code} chars={len(ans)}")

    code, chat = req("POST", "/api/chat", {"question": "   ", "mode": "auto"}, timeout=30)
    check("chat_empty_rejected", code in (400, 422), f"status={code}")

    # gene detail mode
    code, chat = req("POST", "/api/chat", {"question": "tp53", "mode": "gene"}, timeout=90)
    ans = (chat.get("answer") if isinstance(chat, dict) else "") or ""
    check("chat_mode_gene", code == 200 and len(ans) >= 10, f"status={code} chars={len(ans)}")

    # after all chats, health should not be stuck busy long
    time.sleep(1)
    code, health2 = req("GET", "/api/health", timeout=20)
    if isinstance(health2, dict) and health2.get("chat_busy"):
        check("chat_not_stuck_busy", False, f"busy {health2.get('chat_busy_seconds')}s", warn=True)
    else:
        check("chat_not_stuck_busy", True, "idle")

    payload = {
        "elapsed_sec": round(time.time() - t0, 1),
        "errors": ERRORS,
        "warnings": WARNINGS,
        "checks": CHECKS,
        "error_count": len(ERRORS),
        "warn_count": len(WARNINGS),
        "ok_count": sum(1 for c in CHECKS if c["ok"]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\nSUMMARY ok={payload['ok_count']} errors={len(ERRORS)} warnings={len(WARNINGS)} "
        f"elapsed={payload['elapsed_sec']}s -> {OUT}",
        flush=True,
    )
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
