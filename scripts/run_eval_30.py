# -*- coding: utf-8 -*-
"""A 包 30 题验收：按题型选 scope，检查 must/forbid/参考文献/[n]。"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "项目阶段" / "01_金标全文" / "清单与验收" / "eval_30_questions_20260901.json"
OUT = Path(os.getenv("EVAL_OUT", str(ROOT / "logs" / "_eval_30_result.json")))
BASE = os.getenv("HUB_BASE", "http://127.0.0.1:8080")

SCOPE = {
    "trait": ("hub", "partial"),
    "lit": ("hub", "partial"),
    "method": ("hub", "partial"),
    "compare": ("hub", "partial"),
    "gene": ("gene_db", "partial"),
    "expr": ("gene_db", "partial"),
    "asm": ("gene_db", "partial"),
    "id": ("id_map", "partial"),
}


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


def grade(item: dict, ans: str, scope: str) -> list[str]:
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
    # 文献/性状/方法：有编号证据时应有参考文献
    if item.get("type") in ("trait", "lit", "method", "compare") and scope == "hub":
        if re.search(r"\[\d{1,2}\]", low) and not re.search(r"参考文献", low):
            fails.append("missing_参考文献")
    return fails


def main() -> int:
    data = json.loads(EVAL.read_text(encoding="utf-8"))
    rows = []
    pass_n = fail_n = 0
    for q in data["questions"]:
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
        fails.extend(grade(q, ans, scope))
        ok = not fails
        pass_n += int(ok)
        fail_n += int(not ok)
        rows.append(
            {
                "id": q["id"],
                "type": typ,
                "scope": scope,
                "ok": ok,
                "sec": sec,
                "fails": fails,
                "chars": len(ans),
                "head": ans[:180],
            }
        )
        tag = "PASS" if ok else "FAIL"
        print(f"[{tag}] #{q['id']} {typ} {sec}s fails={fails}", flush=True)

    # public url check (optional)
    url_ok = True
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=15) as resp:
            health = json.loads(resp.read().decode())
        pub = (health.get("public_url") or "").strip()
        if pub:
            try:
                with urllib.request.urlopen(pub.rstrip("/") + "/api/health", timeout=20) as r2:
                    url_ok = r2.status == 200
            except Exception:
                url_ok = False
        else:
            url_ok = False
            pub = ""
    except Exception:
        health = {}
        pub = ""
        url_ok = False

    summary = {
        "pass": pass_n,
        "fail": fail_n,
        "total": len(rows),
        "all_pass": fail_n == 0,
        "public_url": pub,
        "public_url_ok": url_ok,
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"SUMMARY pass={pass_n}/{len(rows)} public_url_ok={url_ok} -> {OUT}",
        flush=True,
    )
    return 0 if fail_n == 0 and url_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
