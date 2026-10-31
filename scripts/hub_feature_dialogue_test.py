# -*- coding: utf-8 -*-
"""逐功能实操输入 + 连续多轮对话压测。用项目 .venv 运行。"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("HUB_BASE", "http://127.0.0.1:8080")
OUT = ROOT / "logs" / "hub_feature_dialogue_test.json"

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


def req(method: str, path: str, body=None, timeout: int = 300, *, retries: int = 4):
    headers = {"Accept": "application/json"}
    tok = _token()
    if tok:
        headers["X-Access-Token"] = tok
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    last = (0, "no attempt", 0.0)
    for attempt in range(max(1, retries)):
        r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        t0 = time.time()
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    return resp.status, json.loads(raw) if raw else {}, time.time() - t0
                except json.JSONDecodeError:
                    return resp.status, raw, time.time() - t0
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                parsed = raw[:500]
            sec = time.time() - t0
            last = (e.code, parsed, sec)
            if e.code == 429 and attempt + 1 < retries:
                detail = parsed.get("detail") if isinstance(parsed, dict) else str(parsed)
                wait = 3.0 + attempt * 2.0
                import re as _re

                m = _re.search(r"(\d+)\s*s", str(detail or ""))
                if m:
                    wait = max(wait, float(m.group(1)) + 1.0)
                time.sleep(wait)
                continue
            return e.code, parsed, sec
        except Exception as e:
            last = (0, str(e), time.time() - t0)
            if attempt + 1 < retries:
                time.sleep(1.5 + attempt)
                continue
            return last
    return last

def check(name: str, ok: bool, detail: str = "", *, warn: bool = False) -> None:
    CHECKS.append({"name": name, "ok": ok, "detail": detail, "warn": warn})
    tag = "OK" if ok else ("WARN" if warn else "FAIL")
    print(f"[{tag}] {name}: {detail}"[:320], flush=True)
    if not ok:
        (WARNINGS if warn else ERRORS).append(f"{name}: {detail}")


def chat(question: str, scope: str = "hub", depth: str = "partial", mode: str = "hub", timeout: int = 300, history=None, **extra):
    body = {
        "question": question,
        "mode": mode,
        "answer_scope": scope,
        "answer_depth": depth,
        **extra,
    }
    if history is not None:
        body["history"] = history
    return req("POST", "/api/chat", body, timeout=timeout)


def wait_idle(max_sec: float = 90.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < max_sec:
        code, h, _ = req("GET", "/api/health", timeout=15)
        if code == 200 and isinstance(h, dict) and not h.get("chat_busy"):
            return True
        time.sleep(1.5)
    return False


def main() -> int:
    t_all = time.time()
    code, health, _ = req("GET", "/api/health", timeout=15)
    if code != 200:
        check("service_up", False, str(health))
        return 2
    check("service_up", True, f"pubmed_ready={health.get('pubmed_ready')} busy={health.get('chat_busy')}")
    if not wait_idle(30):
        check("pre_idle", False, "chat still busy before tests")
        return 2

    # ========== 1) 各分析工具实操输入 ==========
    print("\n=== TOOLS ===", flush=True)
    code, g, sec = req("GET", "/api/gene/tp53", timeout=60)
    check("tool_gene_tp53", code == 200 and isinstance(g, dict), f"status={code} sec={sec:.1f} keys={list(g)[:8] if isinstance(g, dict) else None}")

    code, gs, sec = req("GET", "/api/genes/search?q=sox9&limit=5", timeout=30)
    n = len((gs.get("results") or []) if isinstance(gs, dict) else [])
    check("tool_genes_search", code == 200 and n >= 1, f"status={code} n={n} sec={sec:.1f}")

    code, gr, sec = req("GET", "/api/tools/gene-resolve?q=p53", timeout=60)
    # endpoint name may vary
    if code == 0 or code == 404:
        code, gr, sec = req("GET", "/api/gene/resolve?q=p53", timeout=60)
    if code == 0 or code == 404:
        code, gr, sec = req("POST", "/api/tools/id-convert", {"query": "p53"}, timeout=60)
    check("tool_gene_resolve_or_id", code == 200, f"status={code} sec={sec:.1f} body_type={type(gr).__name__}")

    for path, name in (
        ("/api/tools/id-convert?identifier=tp53", "id_convert_get"),
        ("/api/tools/id-convert/batch?ids=tp53,sox9b", "id_convert_batch"),
    ):
        code, body, sec = req("GET", path, timeout=60)
        check(f"tool_{name}", code == 200, f"status={code} sec={sec:.1f}")

    code, blast, sec = req(
        "POST",
        "/api/tools/blast",
        {
            "sequence": "ATGGCGCAAAACGACAGCCAAGAGTTCGCGGAGCTCTGGGAGAAGAATTTGATAAGTATTCAGCCCCCAGGTGGTGGCTCTTGCTGGGAC",
            "top_k": 3,
            "evalue": 0.01,
        },
        timeout=120,
    )
    hits = []
    if isinstance(blast, dict):
        nested = blast.get("result") if isinstance(blast.get("result"), dict) else {}
        hits = blast.get("hits") or nested.get("hits") or []
    check("tool_blast", code == 200 and len(hits) >= 1, f"status={code} hits={len(hits)} sec={sec:.1f}")

    code, enr, sec = req("GET", "/api/tools/enrichment?genes=tp53,sox9b,myl7", timeout=120)
    check("tool_enrichment", code == 200 and isinstance(enr, dict), f"status={code} keys={list(enr)[:8] if isinstance(enr, dict) else None} sec={sec:.1f}")

    code, kegg, sec = req("GET", "/api/tools/kegg?genes=tp53,sox9b", timeout=120)
    check("tool_kegg", code == 200, f"status={code} sec={sec:.1f}")

    code, seq, sec = req("GET", "/api/tools/sequence?gene=tp53&types=cdna", timeout=120)
    if code == 0:
        check("tool_sequence", False, f"status=0 timeout/sec={sec:.1f}（Ensembl 外网偶发超时）", warn=True)
    else:
        check("tool_sequence", code == 200 and isinstance(seq, dict) and seq.get("success") is not False, f"status={code} sec={sec:.1f}")

    code, ann, sec = req("GET", "/api/tools/annotate?gene=tp53", timeout=120)
    check("tool_annotate", code == 200, f"status={code} sec={sec:.1f}")

    code, dbg, sec = req(
        "POST",
        "/api/retrieval/debug",
        {"query": "zebrafish mitochondrial stress", "top_k": 5, "max_chars": 2500},
        timeout=180,
    )
    ok = code == 200 and isinstance(dbg, dict) and dbg.get("ok")
    ranked = (dbg.get("ranked") or []) if isinstance(dbg, dict) else []
    check("tool_retrieval_debug", ok and len(ranked) >= 1, f"status={code} ranked={len(ranked)} sec={sec:.1f}")

    # workflows each preset briefly
    code, presets, _ = req("GET", "/api/workflows/presets", timeout=30)
    slugs = []
    if isinstance(presets, dict):
        items = presets.get("presets") or presets.get("items") or []
        if isinstance(items, list):
            slugs = [p.get("slug") for p in items if isinstance(p, dict) and p.get("slug")]
        elif isinstance(presets.get("preset_by_scope"), dict):
            slugs = list(dict.fromkeys(presets["preset_by_scope"].values()))
    check("tool_presets_list", bool(slugs), f"slugs={slugs}")

    for slug in ("id_only", "literature_only", "gene_enrichment"):
        if slug not in slugs and slugs:
            continue
        wait_idle(60)
        code, wr, sec = req(
            "POST",
            "/api/workflows/run",
            {
                "preset_slug": slug,
                "input_text": "tp53" if slug != "literature_only" else "斑马鱼线粒体",
                "answer_scope": "id_map" if slug == "id_only" else ("literature" if slug == "literature_only" else "gene_db"),
                "answer_depth": "partial",
            },
            timeout=240,
        )
        st = wr.get("status") if isinstance(wr, dict) else None
        check(f"workflow_{slug}", code == 200 and st in ("success", "ok", "completed", None) or (code == 200 and isinstance(wr, dict)), f"status={code} run={st} sec={sec:.1f}")

    # ========== 2) 连续多轮对话（同 scope，带 history） ==========
    print("\n=== MULTI-TURN SAME SCOPE ===", flush=True)
    wait_idle(60)
    turns_gene = [
        ("gene_db", "tp53 基因的基本信息是什么"),
        ("gene_db", "它在哪些组织表达？"),  # 指代追问
        ("gene_db", "再补充它的 GO 注释"),
    ]
    hist: list = []
    for i, (scope, q) in enumerate(turns_gene, 1):
        wait_idle(90)
        code, data, sec = chat(q, scope=scope, depth="partial", history=hist)
        ans = (data.get("answer") if isinstance(data, dict) else "") or ""
        ok = code == 200 and len(ans) >= 30
        expanded = bool(isinstance(data, dict) and data.get("followup_expanded"))
        if i >= 2:
            low = ans.lower()
            continuity = any(k in ans or k in low for k in ("表达", "组织", "tissue", "expression", "tp53", "p53", "GO", "go"))
            if i == 2:
                check(f"dialogue_gene_turn{i}_continuity", continuity, f"expanded={expanded} snippet={ans[:120]}")
            if i == 3:
                check(f"dialogue_gene_turn{i}_continuity", continuity or "GO" in ans or "注释" in ans, f"expanded={expanded} snippet={ans[:120]}")
        check(f"dialogue_gene_turn{i}", ok, f"status={code} chars={len(ans)} sec={sec:.1f} expanded={expanded} q={q[:40]}")
        if ok:
            hist.append({"role": "user", "content": q})
            hist.append({"role": "assistant", "content": ans[:2000]})
        wait_idle(30)
        code_h, h, _ = req("GET", "/api/health", timeout=15)
        check(f"dialogue_gene_turn{i}_idle", code_h == 200 and not (isinstance(h, dict) and h.get("chat_busy")), f"busy={isinstance(h, dict) and h.get('chat_busy')}")

    wait_idle(60)
    turns_id = [
        ("id_map", "p53 对应的官方基因符号和 Ensembl ID"),
        ("id_map", "ZDB-GENE-990415-270 是什么基因"),
        ("id_map", "shh 和 shha 是同一个基因吗"),
    ]
    hist = []
    for i, (scope, q) in enumerate(turns_id, 1):
        wait_idle(60)
        code, data, sec = chat(q, scope=scope, depth="partial", history=hist)
        ans = (data.get("answer") if isinstance(data, dict) else "") or ""
        check(f"dialogue_id_turn{i}", code == 200 and len(ans) >= 20, f"status={code} chars={len(ans)} sec={sec:.1f}")
        if code == 200 and ans:
            hist.append({"role": "user", "content": q})
            hist.append({"role": "assistant", "content": ans[:2000]})

    wait_idle(60)
    turns_lit = [
        ("literature", "斑马鱼线粒体相关文献"),
        ("literature", "上面这些里哪篇讲应激？"),  # 强依赖上文
        ("literature", "再给一篇 HIF 相关的"),
    ]
    hist = []
    for i, (scope, q) in enumerate(turns_lit, 1):
        wait_idle(120)
        code, data, sec = chat(q, scope=scope, depth="partial", timeout=300, history=hist)
        ans = (data.get("answer") if isinstance(data, dict) else "") or ""
        expanded = bool(isinstance(data, dict) and data.get("followup_expanded"))
        offtopic = "仅回答与 Danio rerio" in ans or "请换个与斑马鱼基因相关" in ans
        check(
            f"dialogue_lit_turn{i}",
            code == 200 and len(ans) >= 40 and not offtopic,
            f"status={code} chars={len(ans)} sec={sec:.1f} expanded={expanded} offtopic={offtopic}",
        )
        if i == 2:
            has_substance = ("PMID" in ans or "pmid" in ans.lower() or "应激" in ans or "mitochond" in ans.lower()) and not offtopic
            check(f"dialogue_lit_turn{i}_ref", has_substance, f"追问应仍谈文献/应激：{ans[:160]}")
        if code == 200 and ans and not offtopic:
            hist.append({"role": "user", "content": q})
            hist.append({"role": "assistant", "content": ans[:2000]})

    # ========== 3) 跨 scope 连续切换 ==========
    print("\n=== CROSS-SCOPE CONTINUOUS ===", flush=True)
    cross = [
        ("id_map", "解析基因 sox9b"),
        ("gene_db", "这个基因的功能与表达"),
        ("literature", "sox9b 相关文献要点"),
        ("hub", "综合一下 sox9b 的 ID、功能、文献"),
    ]
    hist = []
    for i, (scope, q) in enumerate(cross, 1):
        wait_idle(120)
        code, data, sec = chat(q, scope=scope, depth="partial", timeout=300, history=hist)
        ans = (data.get("answer") if isinstance(data, dict) else "") or ""
        got_scope = data.get("answer_scope") if isinstance(data, dict) else None
        expanded = bool(isinstance(data, dict) and data.get("followup_expanded"))
        check(
            f"cross_turn{i}_{scope}",
            code == 200 and len(ans) >= 30,
            f"status={code} chars={len(ans)} got_scope={got_scope} expanded={expanded} sec={sec:.1f}",
        )
        if code == 200 and ans:
            hist.append({"role": "user", "content": q})
            hist.append({"role": "assistant", "content": ans[:2000]})

    # ========== 4) 连发冲突：上一问未完成时下一问 ==========
    print("\n=== BUSY / CONCURRENT ===", flush=True)
    wait_idle(60)
    results = {"a": None, "b": None}

    def _slow():
        results["a"] = chat("请用文献总结斑马鱼缺氧应答", scope="literature", depth="partial", timeout=300)

    def _fast_overlap():
        time.sleep(2.0)
        results["b"] = chat("tp53 是什么", scope="gene_db", depth="partial", timeout=60)

    t1 = threading.Thread(target=_slow)
    t2 = threading.Thread(target=_fast_overlap)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    ca, da, sa = results["a"] if results["a"] else (0, {}, 0)
    cb, db, sb = results["b"] if results["b"] else (0, {}, 0)
    # one should succeed; overlap may 503
    check("concurrent_slow_ok", ca == 200 and len((da.get("answer") if isinstance(da, dict) else "") or "") >= 30, f"status={ca} sec={sa:.1f}")
    detail_b = ""
    if isinstance(db, dict):
        detail_b = str(db.get("detail") or "")[:120]
    elif db is not None:
        detail_b = str(db)[:120]
    check(
        "concurrent_overlap_handled",
        cb in (200, 503) or bool(detail_b),
        f"status={cb} detail={detail_b} sec={sb:.1f}",
    )
    wait_idle(120)
    code, h, _ = req("GET", "/api/health", timeout=15)
    check("post_concurrent_idle", code == 200 and not (isinstance(h, dict) and h.get("chat_busy")), f"busy={isinstance(h, dict) and h.get('chat_busy')} sec={isinstance(h, dict) and h.get('chat_busy_seconds')}")

    # after busy, next chat must work
    code, data, sec = chat("myl7 基因符号确认", scope="id_map", depth="partial")
    ans = (data.get("answer") if isinstance(data, dict) else "") or ""
    check("post_busy_recovery_chat", code == 200 and len(ans) >= 15, f"status={code} chars={len(ans)} sec={sec:.1f}")

    # ========== 5) 同主题快速连续 5 轮（易出锁/空答） ==========
    print("\n=== RAPID 5-TURN ===", flush=True)
    rapid_qs = [
        ("id_map", "nanog"),
        ("gene_db", "nanog 功能"),
        ("gene_db", "它的染色体定位"),
        ("id_map", "nanog 的 ZFIN ID"),
        ("hub", "一句话总结 nanog"),
    ]
    hist = []
    for i, (scope, q) in enumerate(rapid_qs, 1):
        wait_idle(90)
        code, data, sec = chat(q, scope=scope, depth="partial", timeout=240, history=hist)
        ans = (data.get("answer") if isinstance(data, dict) else "") or ""
        expanded = bool(isinstance(data, dict) and data.get("followup_expanded"))
        check(f"rapid_turn{i}", code == 200 and len(ans) >= 10, f"status={code} chars={len(ans)} expanded={expanded} sec={sec:.1f} q={q}")
        if i >= 3 and "nanog" not in ans.lower() and i in (3, 4):
            check(f"rapid_turn{i}_topic", False, f"疑似丢上下文：{ans[:100]}", warn=True)
        if code == 200 and ans:
            hist.append({"role": "user", "content": q})
            hist.append({"role": "assistant", "content": ans[:2000]})

    wait_idle(30)
    code, h, _ = req("GET", "/api/health", timeout=15)
    check("final_idle", code == 200 and not (isinstance(h, dict) and h.get("chat_busy")), str(h.get("chat_busy") if isinstance(h, dict) else h))

    elapsed = time.time() - t_all
    summary = {
        "ok": len(CHECKS) - len(ERRORS) - len(WARNINGS),
        "errors": ERRORS,
        "warnings": WARNINGS,
        "checks": CHECKS,
        "elapsed_sec": round(elapsed, 1),
    }
    # recount properly
    summary["ok"] = sum(1 for c in CHECKS if c["ok"])
    summary["fail"] = sum(1 for c in CHECKS if not c["ok"] and not c.get("warn"))
    summary["warn"] = sum(1 for c in CHECKS if not c["ok"] and c.get("warn"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"\nSUMMARY ok={summary['ok']} fail={summary['fail']} warn={summary['warn']} "
        f"elapsed={elapsed:.1f}s -> {OUT}",
        flush=True,
    )
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
