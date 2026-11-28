#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量 ID 互转「功能页」真实 HTTP 链路验证（在服务器上运行）。

与 scripts/audit_id_version_map.py 互补：
  · 审计脚本在独立进程里直调 convert_batch()，覆盖 18×18=324 组，
    坐标复用真实 chain 基准，不占线上 uvicorn 内存；
  · 本脚本走 **HTTP POST /api/tools/id-convert/batch**，与前端按钮
    （web/js/liftover.js 的 runIdMap）完全同一条请求路径，验证
    JSON 契约、版本白名单、身份一致性、坐标格式与耗时。

用法（服务器）：
    PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
    $PY scripts/test_idmap_http_matrix.py                # 本机 127.0.0.1:8080
    $PY scripts/test_idmap_http_matrix.py --public      # 公网 https://zebrafish-hub.top

为什么不把 324 组全走 HTTP：线上是单进程 uvicorn，其中 6 个装配的
UCSC chain 首次加载要 60–140 s，全部打过去会把常驻进程拖到超时甚至
撞 cgroup 内存上限。这 6 个慢链版本由独立进程的审计脚本覆盖；本脚本
在 HTTP 层覆盖其余 12 版 + 2 个本地表版本，并对慢链版本做「负例契约」
（负例不解析实体、不触发 chain，因此零成本即可验证版本白名单）。
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8080"
if "--public" in sys.argv:
    BASE = "https://zebrafish-hub.top"

IDS = ["tp53", "ENSDARG00000035559", "30590", "ZDB-GENE-990415-270",
       "NO_SUCH_GENE_ZFQA_9999"]
NEG = IDS[-1]
POS = IDS[:-1]
EXPECT_SYMBOL = "tp53"
EXPECT_ZFIN = "ZDB-GENE-990415-270"
EXPECT_NCBI = "30590"
SPAN_RE = re.compile(r"^[^:]+:[\d,]+-[\d,]+(?:\([+-]\))?$")

# 线上实测首次加载 60–140 s 的 6 条慢链（见 web/js/assembly-versions.js）
SLOW = {"GRCz13ab", "ASM1840007v1", "ASM2018471v1", "fDanRer4.1",
        "fDreABH1.1", "fDreTuH1.2"}

OUT = ROOT / "output" / "idmap_http_matrix.json"
COOKIE = ""      # /api/gate/pass 下发的通行证；本机回环同样受 API 门禁保护


def get_pass():
    global COOKIE
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + "/api/gate/pass"),
                                    timeout=30) as r:
            COOKIE = (r.headers.get("Set-Cookie") or "").split(";")[0]
    except Exception as e:  # noqa: BLE001
        print("[warn] gate/pass 取票失败: %s" % e, flush=True)


def http(method, path, body=None, timeout=180, cookie=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if cookie or COOKIE:
        headers["Cookie"] = cookie or COOKIE
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
        return r.status, json.loads(raw), time.time() - t0, dict(r.headers)


def xref(row, names):
    for x in row.get("xrefs") or []:
        if str(x.get("db_name") or "").lower() in names:
            return str(x.get("db_id") or "").strip()
    return ""


def ident(row):
    c = row.get("canonical") or {}
    return (str(c.get("zfin_id") or "").strip() or xref(row, ("zfin",)),
            str(c.get("gene_name") or "").strip().lower(),
            xref(row, ("ncbi_gene", "ncbi_gene_grcz12tu")),
            str(c.get("ensembl_id") or "").split(".")[0].strip())


def main():
    print("[init] base=%s" % BASE, flush=True)
    get_pass()
    print("[init] gate cookie=%s" % ("OK" if COOKIE else "无"), flush=True)
    code, cap, _, _ = http("GET", "/api/tools/id-convert/builds", timeout=30)
    builds = cap.get("builds") or []
    print("[init] builds=%d" % len(builds), flush=True)
    if len(builds) < 18:
        print("[fail] builds 清单不足 18 个", flush=True)
        return 2

    # 前端下拉框 option value 与后端白名单一致性
    src_html = (ROOT / "web" / "index.src.html").read_text(encoding="utf-8", errors="replace")
    block = re.search(r'id="idMapSourceBuild".*?</select>', src_html, re.S)
    opts = re.findall(r'<option value="([^"]+)"', block.group(0)) if block else []
    ui_errs = []
    if sorted(opts) != sorted(builds):
        ui_errs.append("UI options != API builds: UI=%s API=%s" % (sorted(opts), sorted(builds)))
    print("[check] 前端下拉 %d 项 vs 后端 %d 项：%s" %
          (len(opts), len(builds), "一致" if not ui_errs else "不一致"), flush=True)

    fast = [b for b in builds if b not in SLOW]
    # 覆盖：全部同版本、GRCz11 → 各快版本、各快版本 → GRCz12tu、
    #       GRCz12tu ⇄ GRCz11 往返、若干跨非本地版对
    full = "--full" in sys.argv
    pairs = []
    # 2026-11-13：首版把 18 个同版本全放进正例矩阵，第一组 GRCz13ab→GRCz13ab
    # 触发 138 s 慢链加载，把单进程 uvicorn 打到连接中断（守护随后自动重启）。
    # 这 6 个版本改由下方「负例契约」覆盖：负例不解析实体、不调用 _coords_for，
    # 因此零成本即可验证版本白名单，且不会拖垮线上进程。
    for b in fast:
        pairs.append((b, b))
        pairs.append(("GRCz11", b))
        pairs.append((b, "GRCz12tu"))
    pairs += [("GRCz12tu", "GRCz11"), ("Zv9", "GRCz10"),
              ("ASM3317019v3", "GRCz11"), ("GRCz11", "ASM3317019v3"),
              ("Zv3", "GRCz12tu"), ("GRCz12tu", "Zv9")]
    pairs = [(s, t) for s, t in pairs if s not in SLOW and t not in SLOW]
    # 默认「安全模式」：只跑 4 个本地表组合（不加载任何 chain），
    # 其余版本由下方负例契约覆盖。--full 才跑会触发链加载的正例矩阵
    # （仅用于诊断慢链耗时，不要在用户在线时段跑）。
    if not full:
        pairs = [("GRCz11", "GRCz11"), ("GRCz11", "GRCz12tu"),
                 ("GRCz12tu", "GRCz11"), ("GRCz12tu", "GRCz12tu")]
    seen, combos = set(), []
    for p in pairs:
        if p not in seen:
            seen.add(p)
            combos.append(p)

    results, failures = [], []
    slow_contract = []
    consecutive_http_errors = 0
    for i, (s, t) in enumerate(combos, 1):
        errs = []
        try:
            status, d, sec, _ = http("POST", "/api/tools/id-convert/batch",
                                     {"identifiers": IDS, "source_build": s, "target_build": t})
            consecutive_http_errors = 0
        except Exception as e:  # noqa: BLE001
            consecutive_http_errors += 1
            failures.append({"source": s, "target": t, "errors": ["HTTP: %s" % e]})
            results.append({"source": s, "target": t, "ok": False, "sec": -1})
            print("[pair] %d/%d %s→%s FAIL http=%s" % (i, len(combos), s, t, e), flush=True)
            if consecutive_http_errors >= 2:
                print("[abort] 连续 2 次 HTTP 异常，停止以免拖累线上服务", flush=True)
                break
            continue
        rows = d.get("results") or []
        binfo = d.get("builds") or {}
        if status != 200:
            errs.append("HTTP %s" % status)
        if d.get("count") != len(IDS) or len(rows) != len(IDS):
            errs.append("count/rows=%s/%d" % (d.get("count"), len(rows)))
        if [r.get("query") for r in rows] != IDS:
            errs.append("query 顺序不符")
        if binfo.get("source") != s or binfo.get("target") != t:
            errs.append("builds 标签错误 %s" % binfo.get("source"))
        if binfo.get("converted") != (s != t):
            errs.append("builds.converted 错误")
        local_pair = s in ("GRCz11", "GRCz12tu") and t in ("GRCz11", "GRCz12tu")
        want_basis = "local_table" if local_pair else "stable_ids_with_chain_coords"
        if binfo.get("identity_basis") != want_basis:
            errs.append("identity_basis=%s 期望 %s" % (binfo.get("identity_basis"), want_basis))
        for r in rows:
            q = r.get("query")
            if q == NEG:
                if r.get("status") != "not_found":
                    errs.append("负例误命中 status=%s" % r.get("status"))
                continue
            if r.get("status") != "unique":
                errs.append("%s 未唯一命中 (%s)" % (q, r.get("status")))
                continue
            z, sym, ncbi, ens = ident(r)
            if sym != EXPECT_SYMBOL or z != EXPECT_ZFIN or ncbi != EXPECT_NCBI:
                errs.append("%s 身份串位 sym=%s zfin=%s ncbi=%s" % (q, sym, z, ncbi))
            if q.startswith("ENSDARG") and not ens.startswith("ENSDARG"):
                errs.append("Ensembl 输入未回填 ensembl_id")
            tc, sc = str(r.get("target_coords") or ""), str(r.get("source_coords") or "")
            if tc and not SPAN_RE.match(tc):
                errs.append("目标坐标格式错 %s" % tc)
            if sc and not SPAN_RE.match(sc):
                errs.append("来源坐标格式错 %s" % sc)
            if s == t and sc and tc and sc != tc:
                errs.append("同版本来源/目标坐标不一致 %s != %s" % (sc, tc))
            if not local_pair and not r.get("coord_status"):
                errs.append("缺 coord_status")
        ok = not errs
        results.append({"source": s, "target": t, "ok": ok, "sec": round(sec, 3),
                        "identity_basis": binfo.get("identity_basis"),
                        "coord_summary": binfo.get("coord_summary"), "errors": errs})
        if not ok:
            failures.append({"source": s, "target": t, "errors": errs})
        if i % 10 == 0 or i == len(combos):
            print("[pair] %d/%d ok=%d fail=%d" %
                  (i, len(combos), i - len(failures), len(failures)), flush=True)

    # 18 个版本「作为来源 / 作为目标」的负例契约：负例不解析实体、
    # 不调用 _coords_for，因此零 chain 成本，可安全覆盖全部版本名。
    contract_jobs = [(b, "GRCz12tu") for b in builds] + [("GRCz11", b) for b in builds]
    for b_s, b_t in contract_jobs:
        try:
            status, d, sec, _ = http("POST", "/api/tools/id-convert/batch",
                                     {"identifiers": [NEG], "source_build": b_s,
                                      "target_build": b_t}, timeout=30)
            bi = d.get("builds") or {}
            ok = (status == 200 and bi.get("source") == b_s and bi.get("target") == b_t
                  and bi.get("converted") == (b_s != b_t)
                  and (d.get("results") or [{}])[0].get("status") == "not_found")
            slow_contract.append({"source": b_s, "target": b_t, "ok": bool(ok),
                                  "sec": round(sec, 3)})
        except Exception as e:  # noqa: BLE001
            slow_contract.append({"source": b_s, "target": b_t, "ok": False, "error": str(e)})
        time.sleep(0.55)   # /api/tools 桶限速，拉开间隔避免 429

    # ---- Ensembl release 组合（2026-11-13 新增维度）----
    # 5 个 release 两两 + 与 2 个本地装配混选；坐标走离线落盘表，不加载链。
    ens_values = [str(x.get("value")) for x in (cap.get("ensembl_releases") or [])]
    ens_checks = []
    if ens_values:
        jobs = [(a, b) for a in ens_values for b in ens_values]
        jobs += [("GRCz11", b) for b in ens_values]
        jobs += [(a, "GRCz12tu") for a in ens_values]
        for s, t in jobs:
            errs = []
            try:
                status, d, sec, _ = http("POST", "/api/tools/id-convert/batch",
                                         {"identifiers": IDS, "source_build": s,
                                          "target_build": t}, timeout=60)
                bi = d.get("builds") or {}
                rows = d.get("results") or []
                if status != 200 or len(rows) != len(IDS):
                    errs.append("HTTP/count %s/%d" % (status, len(rows)))
                if bi.get("source") != s or bi.get("target") != t:
                    errs.append("builds 标签错误")
                if bi.get("identity_basis") != "ensembl_release_gene_set":
                    errs.append("identity_basis=%s" % bi.get("identity_basis"))
                for r in rows:
                    q = r.get("query")
                    if q == NEG:
                        if r.get("status") != "not_found":
                            errs.append("负例误命中")
                        continue
                    stt = r.get("status")
                    if stt not in ("unique", "not_in_release"):
                        errs.append("%s 状态异常 %s" % (q, stt))
                        continue
                    z, sym, ncbi, ens = ident(r)
                    if sym != EXPECT_SYMBOL or z != EXPECT_ZFIN or ncbi != EXPECT_NCBI:
                        errs.append("%s 身份串位" % q)
                    if t.startswith("ens:") and stt == "unique" and not ens.startswith("ENSDARG"):
                        errs.append("%s 目标 release 未给出 ENSDARG ID" % q)
                    if r.get("coord_status") == "not_precomputed":
                        errs.append("坐标未预计算")
            except Exception as e:  # noqa: BLE001
                errs.append("HTTP: %s" % e)
            ens_checks.append({"source": s, "target": t, "ok": not errs, "errors": errs})
            time.sleep(0.55)
        ek = sum(1 for x in ens_checks if x["ok"])
        print("Ensembl release 组合: %d / %d 通过" % (ek, len(ens_checks)), flush=True)
        for x in ens_checks:
            if not x["ok"]:
                print("  FAIL ens %s→%s %s" % (x["source"], x["target"], x["errors"][:3]), flush=True)

    secs = [r["sec"] for r in results if r["sec"] > 0]
    summary = {
        "base": BASE,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "builds_count": len(builds),
        "ui_option_match": not ui_errs,
        "ui_errors": ui_errs,
        "http_pairs": len(combos),
        "http_pass": len(combos) - len(failures),
        "http_fail": len(failures),
        "slow_contract": slow_contract,
        "build_contract_pass": sum(1 for x in slow_contract if x["ok"]),
        "build_contract_total": len(slow_contract),
        "ensembl_checks": ens_checks,
        "ensembl_pass": sum(1 for x in ens_checks if x["ok"]),
        "ensembl_total": len(ens_checks),
        "latency": {"min": round(min(secs), 3) if secs else None,
                    "max": round(max(secs), 3) if secs else None,
                    "avg": round(sum(secs) / len(secs), 3) if secs else None},
        "failures": failures,
        "results": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== 汇总 ===", flush=True)
    print("前端下拉与后端一致: %s" % summary["ui_option_match"], flush=True)
    print("HTTP 组合: %d 通过 / %d 失败" % (summary["http_pass"], summary["http_fail"]), flush=True)
    print("耗时 min/avg/max = %s / %s / %s s" %
          (summary["latency"]["min"], summary["latency"]["avg"], summary["latency"]["max"]), flush=True)
    cok = sum(1 for x in slow_contract if x["ok"])
    print("版本负例契约: %d / %d 通过" % (cok, len(slow_contract)), flush=True)
    for x in slow_contract:
        if not x["ok"]:
            print("  FAIL contract %s→%s %s" % (x["source"], x["target"], x.get("error")), flush=True)
    if failures:
        for f in failures[:20]:
            print("  FAIL %s→%s %s" % (f["source"], f["target"], f["errors"][:3]), flush=True)
    print("产物: %s" % OUT, flush=True)
    return 0 if (not failures and not ui_errs and
                 summary["build_contract_pass"] == summary["build_contract_total"] and
                 summary["ensembl_pass"] == summary["ensembl_total"]) else 1


if __name__ == "__main__":
    sys.exit(main())
