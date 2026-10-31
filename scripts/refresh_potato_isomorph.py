# -*- coding: utf-8 -*-
"""Refresh potato answers for isomorphic pairs; merge into _potato_qa_dump.json.

Uses the same auth/session/turns flow as logs/_ask_potato_agent.py.
"""
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
EVAL = ROOT / "项目阶段" / "01_金标全文" / "清单与验收" / "eval_isomorph_100_20260901.json"
OUT = ROOT / "logs" / "_potato_qa_dump.json"
BASE = "https://potato-agent.ynnu.edu.cn"
UA = "Mozilla/5.0 PotatoIsomorphProbe/1.0"


class Client:
    def __init__(self) -> None:
        self.cj = http.cookiejar.CookieJar()
        ctx = ssl.create_default_context()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=ctx),
        )

    def request(self, method: str, path: str, body=None, timeout=180):
        data = None
        headers = {"User-Agent": UA, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
                return resp.status, json.loads(text) if text[:1] in "{[" else {"_text": text}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"_text": raw}

    def json_ok(self, method, path, body=None, timeout=180):
        status, payload = self.request(method, path, body, timeout=timeout)
        if status >= 400:
            raise RuntimeError(f"{method} {path} -> {status} {str(payload)[:400]}")
        return payload


def last_assistant(messages):
    for m in reversed(messages or []):
        if str(m.get("role") or "") != "assistant":
            continue
        content = m.get("content") or ""
        if isinstance(content, list):
            content = "".join(
                str(p.get("text") or p.get("content") or "") if isinstance(p, dict) else str(p)
                for p in content
            )
        if content:
            return str(content)
    return ""


def extract_live(payload):
    live = payload.get("live") or (payload.get("session") or {}).get("live") or {}
    messages = payload.get("messages")
    if messages is None:
        messages = (payload.get("session") or {}).get("messages")
    return live, messages or []


def wait_idle(c: Client, session_id: str, timeout: float = 180) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            live_payload = c.json_ok("GET", f"/api/sessions/{session_id}/live", timeout=60)
        except Exception:
            time.sleep(3)
            continue
        live, _ = extract_live(live_payload)
        status = str((live or {}).get("status") or "")
        if status not in ("queued", "starting", "running", "awaiting_approval"):
            return
        time.sleep(3)


def ask_one(c: Client, session_id: str, q: str) -> tuple[str, str, float]:
    wait_idle(c, session_id, timeout=240)
    rid = str(uuid.uuid4())
    body = {
        "prompt": q,
        "mode": "chat",
        "request_id": rid,
        "attachments": [],
        "draft_title": q[:40],
    }
    last_err = None
    for attempt in range(5):
        try:
            turn = c.json_ok("POST", f"/api/sessions/{session_id}/turns", body, timeout=180)
            break
        except Exception as e:
            last_err = e
            msg = str(e)
            print(f"  turn retry {attempt+1}: {msg[:120]}", flush=True)
            if "409" in msg or "busy" in msg.lower():
                wait_idle(c, session_id, timeout=240)
                time.sleep(5)
                continue
            if "closed connection" in msg.lower() or "Remote end" in msg:
                # 新开 draft 会话
                session_id = "draft"
                time.sleep(3)
                continue
            time.sleep(5)
    else:
        raise RuntimeError(last_err or "turn failed")

    sess = turn.get("session") or {}
    session_id = str(sess.get("id") or session_id)
    t0 = time.time()
    last_len = -1
    stall = 0
    final_msgs = []
    while time.time() - t0 < 420:
        live_payload = c.json_ok("GET", f"/api/sessions/{session_id}/live", timeout=60)
        live, msgs = extract_live(live_payload)
        status = str((live or {}).get("status") or "")
        ans = last_assistant(msgs)
        clen = len(ans or "")
        if clen != last_len:
            last_len = clen
            stall = 0
            print(f"  status={status} chars={clen} t={int(time.time()-t0)}s", flush=True)
        else:
            stall += 1
        final_msgs = msgs
        if status == "awaiting_approval":
            approval = live.get("approval") or live.get("pending_approval") or {}
            aid = approval.get("id") or approval.get("approval_id") or live.get("approval_id")
            if aid:
                try:
                    c.json_ok(
                        "POST",
                        f"/api/sessions/{session_id}/approval",
                        {"choice": "session", "approval_id": aid},
                    )
                except Exception as e:
                    print("  approval failed", e, flush=True)
            time.sleep(2)
            continue
        if status in ("completed", "idle", "ready", "done", "error", "failed"):
            break
        if status not in ("queued", "starting", "running", "awaiting_approval", ""):
            break
        # 仅在明确完成后或长时间停滞且已有实质正文时退出
        if status == "" and clen > 200 and stall >= 12:
            break
        if clen > 200 and stall >= 20 and status == "running":
            # 可能卡在 running 但正文已停
            break
        time.sleep(3)
    wait_idle(c, session_id, timeout=60)
    return session_id, last_assistant(final_msgs), round(time.time() - t0, 1)


def main() -> None:
    from subprocess import check_call
    import sys

    check_call([sys.executable, str(ROOT / "scripts" / "build_eval_isomorph_100.py")])
    pairs = json.loads(EVAL.read_text(encoding="utf-8"))["isomorphic_pairs"]
    questions = [p["potato"] for p in pairs]

    # keep prior good answers (any dump schema)
    old: dict[str, dict] = {}
    if OUT.exists():
        for row in json.loads(OUT.read_text(encoding="utf-8")).get("qa") or []:
            q = (row.get("question") or "").strip()
            ans = ((row.get("assistant") or {}).get("content") or "").strip()
            if q and ans:
                old[q] = row

    c = Client()
    agr = c.json_ok("GET", "/api/legal/agreement")
    print("agreement", agr.get("version"), flush=True)
    c.json_ok(
        "POST",
        "/api/auth/temporary",
        {"agreement_version": agr.get("version"), "agreement_accepted": True},
        timeout=180,
    )
    print("temp auth ok", flush=True)
    c.json_ok("POST", "/api/runtime/start", timeout=300)
    print("runtime started", flush=True)

    session_id = "draft"
    qa = []
    for q in questions:
        prev_len = len(((old.get(q) or {}).get("assistant") or {}).get("content") or "")
        if prev_len > 120:
            print("CACHE", q[:40], flush=True)
            qa.append(old[q])
            continue
        print("ASK", q, flush=True)
        try:
            session_id, ans, elapsed = ask_one(c, session_id, q)
        except Exception as e:
            print("FAIL", e, flush=True)
            ans, elapsed = "", 0.0
            # 断线后换新会话
            session_id = "draft"
            time.sleep(5)
        row = {
            "question": q,
            "session_id": session_id,
            "assistant": {"content": ans},
            "elapsed_s": elapsed,
        }
        qa.append(row)
        prev = ((old.get(q) or {}).get("assistant") or {}).get("content") or ""
        # 禁止用空答覆盖已有长答
        if ans and len(ans) >= len(prev):
            old[q] = row
        elif q not in old:
            old[q] = row
        print("GOT", len(ans or ""), "chars", "kept", len(((old.get(q) or {}).get("assistant") or {}).get("content") or ""), flush=True)
        # persist incrementally（只写非空优先合并结果）
        OUT.write_text(
            json.dumps({"qa": list(old.values()), "source": "isomorph_pairs"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # final write in pair order — 空答不覆盖
    ordered = []
    for q in questions:
        if q in old and len((old[q].get("assistant") or {}).get("content") or "") > 0:
            ordered.append(old[q])
        else:
            ordered.append({"question": q, "assistant": {"content": ""}})
    # 若磁盘上已有更长答，合并回来
    if OUT.exists():
        try:
            disk = {((r.get("question") or "").strip()): r for r in json.loads(OUT.read_text(encoding="utf-8")).get("qa") or []}
            for i, q in enumerate(questions):
                drow = disk.get(q) or {}
                dlen = len((drow.get("assistant") or {}).get("content") or "")
                olen = len((ordered[i].get("assistant") or {}).get("content") or "")
                if dlen > olen:
                    ordered[i] = drow
        except Exception:
            pass
    OUT.write_text(
        json.dumps({"qa": ordered, "source": "isomorph_pairs"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    n_ok = sum(1 for r in ordered if len((r.get("assistant") or {}).get("content") or "") > 80)
    print("wrote", OUT, "n=", len(ordered), "ok=", n_ok)


if __name__ == "__main__":
    main()
