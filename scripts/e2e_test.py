# -*- coding: utf-8 -*-
"""端到端功能测试：把 api_server 的对外接口全量跑一遍，并做「重复问答 / 多模式 / 并发」压测。

设计要点：
  · 只用标准库（服务器上不需要额外依赖）
  · 默认打 http://127.0.0.1:8080（本机回环，避开公网隧道与 CDN 干扰）
  · 每个用例都要「真验证」：不只是 HTTP 200，还要检查返回体里的关键字段
  · 重复问答会对比「第 1 轮 vs 最后一轮」的延迟与答案长度，用来发现退化/抖动
  · 前后各读一次 uvicorn 的 RSS，用来判断长跑是否吃内存

用法（在服务器上）：
    python3 e2e_test.py                        # 默认 http://127.0.0.1:8080
    python3 e2e_test.py http://127.0.0.1:8080 --rounds 8 --concurrency 3
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8080"
ROUNDS = 6
CONCURRENCY = 3
for i, a in enumerate(sys.argv):
    if a.startswith("http"):
        BASE = a.rstrip("/")
    elif a == "--rounds" and i + 1 < len(sys.argv):
        ROUNDS = int(sys.argv[i + 1])
    elif a == "--concurrency" and i + 1 < len(sys.argv):
        CONCURRENCY = int(sys.argv[i + 1])


# --------------------------------------------------------------------------- 工具
def call(method: str, path: str, body=None, timeout: int = 180):
    url = BASE + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return {"ok": True, "status": r.status, "ms": int((time.time() - t0) * 1000), "raw": raw, "err": ""}
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        return {"ok": False, "status": e.code, "ms": int((time.time() - t0) * 1000), "raw": raw,
                "err": "HTTP %d" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "ms": int((time.time() - t0) * 1000), "raw": b"",
                "err": "%s: %s" % (type(e).__name__, str(e)[:80])}


def as_json(res):
    try:
        return json.loads(res["raw"].decode("utf-8", "replace"))
    except Exception:
        return None


def rss_mb():
    try:
        pid = subprocess.check_output(
            "pgrep -f 'uvicorn api[.]api_server' | head -1", shell=True).decode().strip()
        if not pid:
            return None
        out = subprocess.check_output("ps -o rss= -p " + pid, shell=True).decode().strip()
        return round(int(out) / 1024.0, 1)
    except Exception:
        return None


def brief(obj, n=110):
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        s = str(obj)
    return s[:n]


# --------------------------------------------------------------------------- 校验器
def j_ok(d):
    """通用：是 JSON 对象/数组，且没有明显的错误字段。

    注意：有些接口把**真实数据**放在 detail 字段（如 /api/tools/annotate），
    不能把 detail 一律当错误 —— 只看 success 或「detail 是字符串」这两种情况。
    """
    if d is None:
        return False, "响应不是 JSON"
    if isinstance(d, list):
        return True, "list len=%d" % len(d)
    if isinstance(d, dict):
        if d.get("success") is False:
            return False, "success=false: " + brief(d.get("error") or d)
        if d.get("error") and not d.get("ok"):
            return False, "error: " + brief(d.get("error"))
        if isinstance(d.get("detail"), str) and not d.get("ok"):
            return False, "detail: " + brief(d.get("detail"))
        return True, brief(d)
    return False, "非对象类型"


def has_keys(*keys):
    def v(d):
        ok, msg = j_ok(d)
        if not ok:
            return ok, msg
        for k in keys:
            if k not in d:
                return False, "缺字段 %s（实际字段: %s）" % (k, ",".join(list(d)[:12]))
        return True, brief(d)
    return v


def non_empty_list(path):
    def v(d):
        cur = d
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
        if isinstance(cur, list) and cur:
            return True, "len=%d" % len(cur)
        return False, "空或不是列表: %s" % brief(d)
    return v


def min_count(path, n):
    def v(d):
        cur = d
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
        # 注意：有些字段是**字典**（如 /api/gene/tp53 的 basic 是单条基因记录），
        # 也要算「有内容」，否则 len 拿不到会误判成计数不足。
        if isinstance(cur, (int, float)):
            val = cur
        elif isinstance(cur, (list, dict, str)):
            val = len(cur)
        else:
            val = None
        if val is not None and val >= n:
            return True, "%s=%s" % (".".join(map(str, path)), val)
        return False, "计数不足（需要≥%d，实际 %s）: %s" % (n, val, brief(d))
    return v


def gene_detail(d):
    """基因详情专用：basic 是**单条基因记录（字典）**，不是列表。"""
    ok, msg = j_ok(d)
    if not ok:
        return ok, msg
    basic = d.get("basic")
    if isinstance(basic, dict) and basic:
        name = basic.get("gene_name") or basic.get("gene_symbol") or ""
        if not name:
            return False, "basic 里没有基因名: " + brief(basic)
        return True, "gene=%s chrom=%s" % (name, basic.get("chromosome"))
    if isinstance(basic, list) and basic:
        return True, "basic len=%d" % len(basic)
    return False, "basic 为空: " + brief(d)


def chat_ok(d):
    ok, msg = j_ok(d)
    if not ok:
        return ok, msg
    err = d.get("error")
    if err:
        return False, "answer error: " + brief(err)
    ans = (d.get("answer") or "").strip()
    if len(ans) < 5:
        return False, "答案过短/为空: " + brief(d)
    return True, "task=%s, answer=%d 字" % (d.get("task_type"), len(ans))


# --------------------------------------------------------------------------- 用例表
# (名称, 方法, 路径, 请求体, 校验器)
TESTS = [
    ("健康检查", "GET", "/api/health", None, j_ok),
    ("用量/资源", "GET", "/api/usage", None, j_ok),

    ("Hub 统计", "GET", "/api/hub/stats", None, j_ok),
    ("研究动态(首页)", "GET", "/api/hub/research-feed", None, non_empty_list(["daily_list"])),
    ("维护状态", "GET", "/api/hub/maintenance/status", None, j_ok),
    ("访问统计", "GET", "/api/hub/visit-stats", None, j_ok),
    ("背景图池", "GET", "/api/hub/bg-pools", None, j_ok),
    ("图文库", "GET", "/api/hub/gallery?mode=mix&limit=8", None, j_ok),
    ("内容版本", "GET", "/api/hub/stream-version", None, j_ok),

    ("基因组总览", "GET", "/api/genome/overview", None, min_count(["chromosomes"], 25)),
    ("单条染色体基因", "GET", "/api/genome/chromosome?chrom=1&limit=5", None, min_count(["genes"], 1)),

    ("基因详情", "GET", "/api/gene/tp53", None, gene_detail),
    ("基因库搜索", "GET", "/api/genes/search?q=tp53", None, j_ok),
    ("同源/复制体", "GET", "/api/genes/orthologs?name=tp53", None, j_ok),
    ("基因名解析", "GET", "/api/tools/gene-resolve?q=tp53", None, j_ok),
    ("ID 换算(单个)", "GET", "/api/tools/id-convert?identifier=ZDB-GENE-990415-172", None, j_ok),
    ("ID 换算(批量GET)", "GET", "/api/tools/id-convert/batch?ids=tp53,sox9b", None, j_ok),
    ("ID 换算(批量POST)", "POST", "/api/tools/id-convert/batch",
     {"identifiers": ["tp53", "sox9b", "ENSDARG00000059595"]}, j_ok),
    ("ID 换算(自由文本)", "POST", "/api/tools/id-convert/text",
     {"text": "tp53, sox9b\nZDB-GENE-990415-172"}, j_ok),

    ("表达-基因谱", "GET", "/api/expression/gene?name=tp53", None, j_ok),
    ("表达-组织列表", "GET", "/api/expression/tissues", None, j_ok),
    ("表型-统计", "GET", "/api/phenotype/stats", None, j_ok),
    ("表型-按基因", "GET", "/api/phenotype/gene?name=tp53&limit=10", None, j_ok),
    ("表型-按术语", "GET", "/api/phenotype/search?q=curvature&limit=5", None, j_ok),

    ("序列提取", "GET", "/api/tools/sequence?gene=tp53&types=cdna", None, j_ok),
    ("一键注释", "GET", "/api/tools/annotate?gene=tp53", None, j_ok),
    ("富集(GO)", "GET", "/api/tools/enrichment?genes=tp53,sox9b,myod1&type=go&top_n=5", None, j_ok),
    ("通路浏览器", "GET", "/api/tools/kegg?q=p53", None, j_ok),
    ("工具统计", "GET", "/api/tools/stats", None, j_ok),
    ("CRISPR 能力", "GET", "/api/tools/crispr/capabilities", None, j_ok),
    ("知识库来源", "GET", "/api/knowledge/sources", None, j_ok),
    ("文献检索调试", "POST", "/api/retrieval/debug",
     {"query": "zebrafish hypoxia", "max_chars": 2000, "top_k": 5}, j_ok),

    ("向导预设", "GET", "/api/workflows/presets", None, j_ok),
    ("租户信息", "GET", "/api/tenants/current", None, j_ok),
]

# 表型术语排行（需要先拿到一个真实术语）
DYNAMIC = []


def run_static_tests(only: str = ""):
    tests = [t for t in TESTS if (not only or only in t[0] or only in t[2])]
    print("=" * 100)
    print("A. 接口连通性全扫 —— 共 %d 个用例%s" % (len(tests), ("（过滤: %s）" % only) if only else ""))
    print("=" * 100)
    rows = []
    for name, method, path, body, validator in tests:
        res = call(method, path, body, timeout=180)
        data = as_json(res)
        if res["ok"]:
            ok, note = validator(data)
        else:
            ok, note = False, "%s | %s" % (res["err"], (res["raw"][:110].decode("utf-8", "replace")))
        rows.append((name, method, path, res["status"], res["ms"], ok, note))
        print("  %-16s %-4s %-46s %s  %6dms  %s" % (
            name, method, path[:46], "PASS" if ok else "FAIL", res["ms"], note[:60]))
    return rows


def pick_tissue():
    d = as_json(call("GET", "/api/expression/tissues"))
    if isinstance(d, dict):
        for key in ("tissues", "items", "data", "list"):
            v = d.get(key)
            if isinstance(v, list) and v:
                first = v[0]
                return first if isinstance(first, str) else (first.get("tissue") or first.get("name"))
    return None


def run_dynamic_tests():
    print("\n" + "=" * 100)
    print("A2. 依赖真实数据的用例（组织 Top 基因 / 多基因对比 / 表型术语排行）")
    print("=" * 100)
    rows = []
    tissue = pick_tissue()
    if tissue:
        t = urllib.parse.quote(str(tissue))
        res = call("GET", "/api/expression/top?tissue=%s&n=5" % t)
        ok, note = j_ok(as_json(res)) if res["ok"] else (False, res["err"])
        rows.append(("表达-组织Top", "GET", "/api/expression/top?tissue=%s" % t, res["status"], res["ms"], ok, note))
        print("  %-16s %-46s %s  %6dms  %s" % ("表达-组织Top", ("/api/expression/top?tissue=%s" % t)[:46],
                                             "PASS" if ok else "FAIL", res["ms"], note[:50]))
    else:
        print("  ! 拿不到组织列表，跳过「组织 Top 基因」")

    res = call("GET", "/api/expression/compare?genes=tp53,sox9b&top=5")
    ok, note = j_ok(as_json(res)) if res["ok"] else (False, res["err"])
    rows.append(("表达-多基因对比", "GET", "/api/expression/compare", res["status"], res["ms"], ok, note))
    print("  %-16s %-46s %s  %6dms  %s" % ("表达-多基因对比", "/api/expression/compare?genes=tp53,sox9b",
                                         "PASS" if ok else "FAIL", res["ms"], note[:50]))
    return rows


def run_heavy_tests():
    print("\n" + "=" * 100)
    print("A3. 计算量较大的用例（BLAST / CRISPR / 引物）—— 允许慢，但不能失败")
    print("=" * 100)
    rows = []
    tpl = ("ATGGAGGAGCCGCAGTCAGATCCTAGCGTCGAGCCCCCTCTGAGTCAGGAAACATTTTCAGACCTATGGAAACTACTTCCTGAAAACAACGTTCTG"
           "TCCCCCTTGCCGTCCCAAGCAATGGATGATTTGATGCTGTCCCCGGACGATATTGAACAATGGTTCACTGAAGACCCAGGTCCAGATGAAGCTCCC")
    cases = [
        ("BLAST 比对", "POST", "/api/tools/blast", {"sequence": tpl, "top_k": 3, "evalue": 10.0}),
        ("CRISPR 设计", "POST", "/api/tools/crispr/design",
         {"target": "sox9b", "enzyme": "NGG", "top_n": 5, "max_mismatch": 3, "off_target": False}),
        ("引物设计", "POST", "/api/tools/primer/design",
         {"template": "sox9b", "max_pairs": 3, "off_target": False}),
    ]
    for name, method, path, body in cases:
        res = call(method, path, body, timeout=300)
        ok, note = j_ok(as_json(res)) if res["ok"] else (False, "%s | %s" % (res["err"], res["raw"][:100].decode("utf-8", "replace")))
        rows.append((name, method, path, res["status"], res["ms"], ok, note))
        print("  %-16s %-4s %-40s %s  %6dms  %s" % (name, method, path[:40], "PASS" if ok else "FAIL", res["ms"], note[:50]))
    return rows


# --------------------------------------------------------------------------- 重复问答
QUESTIONS = [
    ("auto", "tp53 在斑马鱼里主要有什么功能？"),
    ("auto", "sox9b 突变会导致什么表型？"),
    ("rag", "斑马鱼缺氧应答的关键通路是什么？"),
    ("db", "本地库一共有多少条基因记录？"),
    ("gene", "tp53 的表达谱是怎样的？"),
    ("fusion", "斑马鱼心脏再生的研究进展如何？"),
]


def run_repeat(fixed_question: str = "tp53 在斑马鱼里主要有什么功能？", rounds: int = 6):
    print("\n" + "=" * 100)
    print("B. 同一问题连续问 %d 次（看答案稳定性 / 延迟漂移 / 是否报错）" % rounds)
    print("=" * 100)
    lats, lens, fails = [], [], []
    for i in range(1, rounds + 1):
        res = call("POST", "/api/chat", {"question": fixed_question, "mode": "auto", "lang": "zh"}, timeout=300)
        d = as_json(res)
        ok = False
        if res["ok"] and d:
            ok, note = chat_ok(d)
        else:
            note = res["err"]
        if ok:
            ans = d.get("answer", "")
            lats.append(res["ms"])
            lens.append(len(ans))
            print("  第 %d 次: PASS  %6dms  答案 %5d 字  task=%s" % (i, res["ms"], len(ans), d.get("task_type")))
        else:
            fails.append("第 %d 次: %s" % (i, note))
            print("  第 %d 次: FAIL  %6dms  %s" % (i, res["ms"], str(note)[:70]))
    if lats:
        print("  ── 延迟: 首次 %dms / 中位 %dms / 最大 %dms；答案长度: 首次 %d / 中位 %d / 最大 %d" % (
            lats[0], int(statistics.median(lats)), max(lats),
            lens[0], int(statistics.median(lens)), max(lens)))
        drift = lats[-1] - lats[0]
        print("  ── 末次-首次 延迟差: %+dms（%s）" % (drift, "正常波动" if abs(drift) < 0.6 * max(lats) + 3000 else "⚠ 波动较大"))
    return {"fails": fails, "lats": lats, "lens": lens}


def run_modes():
    print("\n" + "=" * 100)
    print("C. 多模式问答（auto / rag / db / gene / fusion）各问一次")
    print("=" * 100)
    fails = []
    for mode, q in QUESTIONS:
        res = call("POST", "/api/chat", {"question": q, "mode": mode, "lang": "zh"}, timeout=300)
        d = as_json(res)
        ok, note = chat_ok(d) if (res["ok"] and d) else (False, res["err"])
        if not ok:
            fails.append("%s: %s" % (q, note))
        print("  %-6s %-30s %s  %6dms  %s" % (mode, q[:30], "PASS" if ok else "FAIL", res["ms"], str(note)[:46]))
    return fails


def run_concurrency(n=3):
    print("\n" + "=" * 100)
    print("D. 并发 %d 路同时问（看是否互相干扰 / 超时 / 报错）" % n)
    print("=" * 100)
    results = [None] * n
    qs = ["tp53 是什么基因？", "sox9b 有什么表型？", "斑马鱼有多少条染色体？"][:n]

    def worker(idx, q):
        res = call("POST", "/api/chat", {"question": q, "mode": "auto", "lang": "zh"}, timeout=300)
        d = as_json(res)
        ok, note = chat_ok(d) if (res["ok"] and d) else (False, res["err"])
        results[idx] = (q, res["ms"], ok, note)

    t0 = time.time()
    ths = [threading.Thread(target=worker, args=(i, q)) for i, q in enumerate(qs)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    total = int((time.time() - t0) * 1000)
    fails = []
    for q, ms, ok, note in results:
        if not ok:
            fails.append("%s: %s" % (q, note))
        print("  %-26s %s  %6dms  %s" % (q[:26], "PASS" if ok else "FAIL", ms, str(note)[:44]))
    print("  ── %d 路并发总耗时 %dms（若远大于单次延迟说明被串行化了，属预期；关键是不能报错）" % (n, total))
    return fails


def main():
    only = ""
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]
    print("端到端测试 @ %s   （项目：斑马鱼基因问答体）" % BASE)
    print("UTC 时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
    rss0 = rss_mb()
    print("uvicorn RSS（测试前）: %s MB" % rss0)

    rows = run_static_tests(only)
    if only:
        print("\n(---only 模式：只跑用例部分，跳过问答压测---)")
        fails = [r for r in rows if not r[5]]
        print("接口用例: %d 个，失败 %d 个" % (len(rows), len(fails)))
        for r in fails:
            print("  ✗ %-16s %s %s → %s" % (r[0], r[1], r[2], str(r[6])[:90]))
        cli_rc = 1 if fails else 0
        return cli_rc
    rows += run_dynamic_tests()
    rows += run_heavy_tests()

    rep = run_repeat(rounds=ROUNDS)
    mode_fails = run_modes()
    conc_fails = run_concurrency(CONCURRENCY)

    rss1 = rss_mb()
    print("\n" + "=" * 100)
    print("E. 汇总")
    print("=" * 100)
    fails = [r for r in rows if not r[5]]
    print("  接口用例: %d 个，失败 %d 个" % (len(rows), len(fails)))
    for r in fails:
        print("    ✗ %-16s %s %s → %s" % (r[0], r[1], r[2], str(r[6])[:90]))
    print("  重复问答失败: %d 次" % len(rep["fails"]))
    for f in rep["fails"]:
        print("    ✗", f)
    print("  多模式问答失败: %d 个" % len(mode_fails))
    for f in mode_fails:
        print("    ✗", f)
    print("  并发问答失败: %d 个" % len(conc_fails))
    for f in conc_fails:
        print("    ✗", f)
    print("  uvicorn RSS: %s MB → %s MB" % (rss0, rss1))
    total_fail = len(fails) + len(rep["fails"]) + len(mode_fails) + len(conc_fails)
    print("\n>>> 结论: %s" % ("全部通过 ✅" if total_fail == 0 else "有 %d 项失败 ❌" % total_fail))
    return 1 if total_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
