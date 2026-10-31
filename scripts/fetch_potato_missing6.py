# -*- coding: utf-8 -*-
"""Fetch only missing potato isomorph answers — one fresh session per question."""
from __future__ import annotations

import json
import ssl
import time
import uuid
from pathlib import Path

import http.cookiejar
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "_potato_qa_dump.json"
BASE = "https://potato-agent.ynnu.edu.cn"
UA = "Mozilla/5.0 PotatoMissing6/1.0"

MISSING = [
    "马铃薯基因编辑常用哪些方法",
    "比较早熟与晚熟马铃薯的遗传差异",
    "马铃薯块茎形状遗传相关关键文献怎么说",
    "马铃薯 GWAS 常用来研究哪些性状",
    "StSP6A 主要在哪些组织起作用",
    "马铃薯参考基因组常用组装名有哪些",
]


class Client:
    def __init__(self) -> None:
        self.cj = http.cookiejar.CookieJar()
        ctx = ssl.create_default_context()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=ctx),
        )

    def json_ok(self, method, path, body=None, timeout=180):
        data = headers = None
        headers = {"User-Agent": UA, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        with self.opener.open(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            payload = json.loads(text) if text[:1] in "{[" else {"_text": text}
            if resp.status >= 400:
                raise RuntimeError(f"{method} {path} -> {resp.status} {payload}")
            return payload


def last_assistant(messages):
    for m in reversed(messages or []):
        if str(m.get("role") or "") != "assistant":
            continue
        c = m.get("content") or ""
        if isinstance(c, list):
            c = "".join(
                str(p.get("text") or p.get("content") or "") if isinstance(p, dict) else str(p)
                for p in c
            )
        if c:
            return str(c)
    return ""


def ask_one(c: Client, q: str) -> str:
    rid = str(uuid.uuid4())
    turn = c.json_ok(
        "POST",
        "/api/sessions/draft/turns",
        {
            "prompt": q,
            "mode": "chat",
            "request_id": rid,
            "attachments": [],
            "draft_title": q[:40],
        },
        timeout=180,
    )
    session_id = str((turn.get("session") or {}).get("id") or "draft")
    t0 = time.time()
    last_len = -1
    stall = 0
    ans = ""
    while time.time() - t0 < 420:
        live_payload = c.json_ok("GET", f"/api/sessions/{session_id}/live", timeout=60)
        live = live_payload.get("live") or (live_payload.get("session") or {}).get("live") or {}
        msgs = live_payload.get("messages") or (live_payload.get("session") or {}).get("messages") or []
        status = str(live.get("status") or "")
        ans = last_assistant(msgs)
        clen = len(ans)
        if clen != last_len:
            print(f"  {status} chars={clen} t={int(time.time()-t0)}s", flush=True)
            last_len = clen
            stall = 0
        else:
            stall += 1
        if status == "awaiting_approval":
            aid = (live.get("approval") or {}).get("id") or live.get("approval_id")
            if aid:
                try:
                    c.json_ok(
                        "POST",
                        f"/api/sessions/{session_id}/approval",
                        {"choice": "session", "approval_id": aid},
                    )
                except Exception:
                    pass
            time.sleep(2)
            continue
        if status in ("completed", "idle", "ready", "done", "error", "failed"):
            break
        if clen > 150 and stall >= 10:
            break
        time.sleep(3)
    return ans


def main() -> None:
    old: dict[str, dict] = {}
    if OUT.exists():
        for row in json.loads(OUT.read_text(encoding="utf-8")).get("qa") or []:
            q = (row.get("question") or "").strip()
            if q:
                old[q] = row

    c = Client()
    agr = c.json_ok("GET", "/api/legal/agreement")
    c.json_ok(
        "POST",
        "/api/auth/temporary",
        {"agreement_version": agr.get("version"), "agreement_accepted": True},
    )
    c.json_ok("POST", "/api/runtime/start", timeout=300)
    print("runtime ok", flush=True)

    for q in MISSING:
        prev = len(((old.get(q) or {}).get("assistant") or {}).get("content") or "")
        if prev > 120:
            print("SKIP", q[:40], prev, flush=True)
            continue
        print("ASK", q, flush=True)
        for attempt in range(3):
            try:
                ans = ask_one(c, q)
                if len(ans) > 80:
                    old[q] = {"question": q, "assistant": {"content": ans}}
                    OUT.write_text(
                        json.dumps({"qa": list(old.values()), "source": "isomorph_pairs"}, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    print("OK", len(ans), flush=True)
                    break
                print("short", len(ans), "retry", attempt + 1, flush=True)
            except Exception as e:
                print("ERR", attempt + 1, str(e)[:120], flush=True)
            time.sleep(8)
        time.sleep(5)

    # merge with full pair order from eval json
    EVAL = ROOT / "项目阶段" / "01_金标全文" / "清单与验收" / "eval_isomorph_100_20260901.json"
    pairs = json.loads(EVAL.read_text(encoding="utf-8"))["isomorphic_pairs"]
    ordered = []
    for p in pairs:
        q = p["potato"]
        if q in old:
            ordered.append(old[q])
        else:
            ordered.append({"question": q, "assistant": {"content": ""}})
    OUT.write_text(
        json.dumps({"qa": ordered, "source": "isomorph_pairs"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    n_ok = sum(1 for r in ordered if len((r.get("assistant") or {}).get("content") or "") > 80)
    print("FINAL ok=", n_ok, "/ 20", flush=True)


if __name__ == "__main__":
    main()
