# -*- coding: utf-8
import json, ssl, time, uuid, http.cookiejar, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "logs" / "_potato_qa_dump.json"
BASE = "https://potato-agent.ynnu.edu.cn"
FIX = [
    "StSP6A 主要在哪些组织起作用",
    "马铃薯参考基因组常用组装名有哪些",
    "StCO 和光周期结薯有什么关系",
]

class C:
    def __init__(self):
        ctx = ssl.create_default_context()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.HTTPSHandler(context=ctx),
        )
    def j(self, m, p, b=None, t=180):
        h = {"User-Agent": "fix3/1.0", "Accept": "application/json"}
        d = json.dumps(b, ensure_ascii=False).encode() if b is not None else None
        if b is not None: h["Content-Type"] = "application/json"
        req = urllib.request.Request(BASE + p, data=d, headers=h, method=m)
        with self.op.open(req, timeout=t) as r:
            text = r.read().decode("utf-8", "replace")
            return json.loads(text) if text[:1] in "{[" else {"_t": text}
    def last(self, msgs):
        for m in reversed(msgs or []):
            if m.get("role") != "assistant": continue
            c = m.get("content") or ""
            if isinstance(c, list): c = "".join(str(x.get("text") or x) if isinstance(x, dict) else str(x) for x in c)
            if c: return str(c)

def ask(c, q):
    t = c.j("POST", "/api/sessions/draft/turns", {"prompt": q, "mode": "chat", "request_id": str(uuid.uuid4()), "attachments": [], "draft_title": q[:40]})
    sid = str((t.get("session") or {}).get("id") or "draft")
    t0 = time.time()
    ans, stall, last = "", 0, -1
    while time.time() - t0 < 360:
        lp = c.j("GET", f"/api/sessions/{sid}/live", t=60)
        live = lp.get("live") or (lp.get("session") or {}).get("live") or {}
        msgs = lp.get("messages") or (lp.get("session") or {}).get("messages") or []
        st = str(live.get("status") or "")
        ans = c.last(msgs) or ""
        if len(ans) != last: stall, last = 0, len(ans)
        else: stall += 1
        if st in ("completed", "idle", "ready", "done", "error", "failed"): break
        if len(ans) > 120 and stall >= 8: break
        time.sleep(3)
    return ans

def main():
    data = json.loads(OUT.read_text(encoding="utf-8"))
    by_q = {(r.get("question") or "").strip(): r for r in data.get("qa") or []}
    c = C()
    agr = c.j("GET", "/api/legal/agreement")
    c.j("POST", "/api/auth/temporary", {"agreement_version": agr.get("version"), "agreement_accepted": True})
    c.j("POST", "/api/runtime/start", t=300)
    for q in FIX:
        print("ASK", q, flush=True)
        try:
            ans = ask(c, q)
            if len(ans) > 80:
                by_q[q] = {"question": q, "assistant": {"content": ans}}
                print("OK", len(ans), flush=True)
            else:
                print("FAIL", len(ans), flush=True)
        except Exception as e:
            print("ERR", e, flush=True)
        time.sleep(5)
    EVAL = ROOT / "项目阶段/01_金标全文/清单与验收/eval_isomorph_100_20260901.json"
    ordered = [by_q.get(p["potato"]) or {"question": p["potato"], "assistant": {"content": ""}} for p in json.loads(EVAL.read_text(encoding="utf-8"))["isomorphic_pairs"]]
    OUT.write_text(json.dumps({"qa": ordered, "source": "isomorph_pairs"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("done", sum(1 for r in ordered if len((r.get("assistant") or {}).get("content") or "") > 80), "/20")

if __name__ == "__main__":
    main()
