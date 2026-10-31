# -*- coding: utf-8 -*-
"""Hub 更深一层细节：全链路聊天、工作流预置、BLAST 命中、模块导入、前端关键符号。"""
from __future__ import annotations

import ast
import importlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from core.entry import bootstrap_app

bootstrap_app()

BASE = os.environ.get("HUB_BASE", "http://127.0.0.1:8080")
OUT = ROOT / "logs" / "hub_deep_loop_test.json"
ERRORS: list[str] = []
WARNINGS: list[str] = []
CHECKS: list[dict] = []


def _token() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.strip().startswith("WEB_ACCESS_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("WEB_ACCESS_TOKEN", "")


def req(method: str, path: str, body=None, timeout: int = 300):
    tok = _token()
    headers = {"Accept": "application/json"}
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
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, raw[:500]
    except Exception as e:
        return 0, str(e)


def check(name: str, ok: bool, detail: str = "", *, warn: bool = False) -> None:
    CHECKS.append({"name": name, "ok": ok, "detail": detail, "warn": warn})
    print(f"[{'OK' if ok else ('WARN' if warn else 'FAIL')}] {name}: {detail}"[:280], flush=True)
    if not ok:
        (WARNINGS if warn else ERRORS).append(f"{name}: {detail}")


def main() -> int:
    t0 = time.time()

    code, health = req("GET", "/api/health", timeout=15)
    if code != 200:
        check("service_up", False, str(health))
        return 2
    check("service_up", True, "pubmed_ready=" + str(health.get("pubmed_ready")))

    # imports
    mods = [
        "api.api_server",
        "agent.hub_orchestrator",
        "agent.workflow_engine",
        "workflow_presets",
        "workflow_store",
        "tool_orchestrator",
        "rag.pubmed_yes_retriever",
        "rag.rag_builder",
        "rag.rerank",
        "gene_toolkit",
        "gene_id_mapper",
        "enrichment_toolkit",
    ]
    for m in mods:
        try:
            importlib.import_module(m)
            check(f"import_{m}", True, "ok")
        except Exception as e:
            check(f"import_{m}", False, str(e))

    # syntax of critical packages (ignore known optional empty stubs)
    _ignore_empty = {"grobid_client.py", "grobid_tei_map.py", "__init__.py"}
    for pkg in ("api", "agent", "rag", "database", "core"):
        bad = []
        for p in (ROOT / pkg).rglob("*.py"):
            if p.name in _ignore_empty and p.stat().st_size == 0:
                continue
            if p.stat().st_size == 0:
                bad.append(f"EMPTY:{p.name}")
                continue
            try:
                ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError as e:
                bad.append(f"{p.name}:{e.lineno}")
        check(f"syntax_{pkg}", not bad, ",".join(bad[:8]) or "clean")

    # workflow presets content
    from workflow_presets import PRESETS, PRESET_BY_SCOPE, SCOPE_BY_PRESET

    check("presets_count", len(PRESETS) >= 5, f"n={len(PRESETS)}")
    for s in ("literature", "gene_db", "id_map", "hub", "retrieval_debug"):
        check(f"preset_map_{s}", s in PRESET_BY_SCOPE and PRESET_BY_SCOPE[s] in SCOPE_BY_PRESET, PRESET_BY_SCOPE.get(s))

    # BLAST should return hits for zebrafish CDS sample
    sample = "ATGGCTTCTAAGGAAGAAGATCTTCTGGAGGAGAAGGTGAAGGAGCTGCAGAAG"
    code, blast = req("POST", "/api/tools/blast", {"sequence": sample, "top_k": 3}, timeout=120)
    check("blast_http", code == 200 and isinstance(blast, dict), f"status={code}")
    hits = []
    if isinstance(blast, dict):
        nested = blast.get("result") if isinstance(blast.get("result"), dict) else {}
        hits = (
            blast.get("hits")
            or nested.get("hits")
            or blast.get("results")
            or blast.get("alignments")
            or []
        )
        check(
            "blast_hits",
            len(hits) >= 1 or int(nested.get("hit_count") or 0) >= 1,
            f"hits={len(hits)} hit_count={nested.get('hit_count')} summary={(blast.get('summary') or '')[:80]}",
        )

    # sequence tool should include cdna or error clearly
    code, seq = req("GET", "/api/tools/sequence?gene=tp53&cdna=1", timeout=90)
    check("sequence_tp53", code == 200 and isinstance(seq, dict), f"status={code} keys={list(seq)[:10] if isinstance(seq, dict) else type(seq)}")

    # annotate
    code, ann = req("GET", "/api/tools/annotate?gene=tp53", timeout=120)
    check("annotate_tp53", code == 200 and isinstance(ann, dict), f"status={code}")

    # enrichment structure
    code, enr = req("GET", "/api/tools/enrichment?genes=tp53,sox9b,myl7", timeout=120)
    check("enrichment_struct", code == 200 and isinstance(enr, dict), f"status={code} keys={list(enr)[:12] if isinstance(enr, dict) else None}")

    # workflow literature_only
    code, wr = req(
        "POST",
        "/api/workflows/run",
        {
            "preset_slug": "literature_only",
            "input_text": "斑马鱼线粒体",
            "answer_scope": "literature",
            "answer_depth": "partial",
        },
        timeout=240,
    )
    check(
        "workflow_literature_only",
        code == 200 and isinstance(wr, dict),
        f"status={code} run_status={isinstance(wr, dict) and wr.get('status')} scope={isinstance(wr, dict) and wr.get('answer_scope')}",
    )
    if isinstance(wr, dict) and wr.get("run_id"):
        code2, detail = req("GET", f"/api/workflows/runs/{wr['run_id']}", timeout=30)
        check("workflow_run_detail", code2 == 200, f"status={code2}")

    # hub full chat (may be slower)
    code, chat = req(
        "POST",
        "/api/chat",
        {
            "question": "tp53 的功能与相关文献",
            "mode": "hub",
            "answer_scope": "hub",
            "answer_depth": "partial",
        },
        timeout=300,
    )
    ans = (chat.get("answer") if isinstance(chat, dict) else "") or ""
    check(
        "chat_hub_partial",
        code == 200 and len(ans) >= 40,
        f"status={code} chars={len(ans)} scope={isinstance(chat, dict) and chat.get('answer_scope')} trace={len((chat.get('agent_trace') or []) if isinstance(chat, dict) else [])}",
    )

    # retrieval debug pack order consistency
    code, dbg = req(
        "POST",
        "/api/retrieval/debug",
        {"query": "zebrafish sox9", "top_k": 8, "max_chars": 3000, "candidate_preview": 10},
        timeout=180,
    )
    ok = code == 200 and isinstance(dbg, dict) and dbg.get("ok")
    check("deep_retrieval", ok, f"status={code}")
    if ok:
        ranked = dbg.get("ranked") or []
        pack = (dbg.get("hub_pack") or {}).get("order") or []
        check("deep_retrieval_order", len(ranked) >= 1 and len(pack) >= 1, f"ranked={len(ranked)} pack={len(pack)}")
        if pack and ranked:
            pack_pmid = str(pack[0].get("pmid") or "")
            rank_pmids = {str(h.get("pmid") or "") for h in ranked}
            check(
                "deep_retrieval_pack_align",
                bool(pack[0].get("tag")) and (not pack_pmid or pack_pmid in rank_pmids),
                f"pack0={pack[0].get('tag')} pack_pmid={pack_pmid}",
            )

    # frontend JS parse
    app_js = ROOT / "web" / "js" / "app.js"
    try:
        # not valid as module AST always; just ensure non-empty and key symbols
        txt = app_js.read_text(encoding="utf-8")
        for sym in ("updateSyncHint", "loadHealth", "runScopedChatRequest", "SCOPE_TO_PRESET", "retrievalBtn"):
            check(f"js_has_{sym}", sym in txt, "present" if sym in txt else "missing")
    except Exception as e:
        check("js_app_readable", False, str(e))

    # no empty critical files
    for rel in (
        "rag/pubmed_yes_retriever.py",
        "rag/rag_builder.py",
        "agent/hub_orchestrator.py",
        "agent/workflow_engine.py",
        "database/workflow_presets.py",
        "database/tool_orchestrator.py",
    ):
        p = ROOT / rel
        check(f"file_{rel.replace('/', '_')}", p.is_file() and p.stat().st_size > 100, f"size={p.stat().st_size if p.is_file() else 0}")

    payload = {
        "elapsed_sec": round(time.time() - t0, 1),
        "errors": ERRORS,
        "warnings": WARNINGS,
        "checks": CHECKS,
        "ok_count": sum(1 for c in CHECKS if c["ok"]),
        "error_count": len(ERRORS),
        "warn_count": len(WARNINGS),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSUMMARY ok={payload['ok_count']} errors={len(ERRORS)} warnings={len(WARNINGS)} elapsed={payload['elapsed_sec']}s", flush=True)
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
