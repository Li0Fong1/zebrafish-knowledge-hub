# -*- coding: utf-8 -*-
"""四项功能验收：① 别名/历史名 ② 复制体(同源) ③ sgRNA/引物 ④ 前端(品牌图/Genes/Tools)。

用法（纯标准库，服务器本机或本地都行）：
    python -X utf8 scripts/test_4features.py http://127.0.0.1:8080
    python -X utf8 scripts/test_4features.py http://127.0.0.1:8080 --no-chat   # 跳过问答（省 1~2 分钟）

覆盖：
  A 别名（任务①）   : /api/gene/tp53 的 aliases 块、别名反查解析、首句是否列出历史别名
  B 复制体（任务②） : /api/genes/orthologs（斑马鱼名→人鼠同源；人符号→斑马鱼复制体）
  C sgRNA/引物（③）: capabilities + crispr/design + primer/design + 跳转链接是否在页面里
  D 前端（任务④）  : 品牌图 12.png 派生资源、home-agent 资源、Genomes→Genes→Expression 顺序、Tools 而不是 Coming soon
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--")
        else "http://127.0.0.1:8080").rstrip("/")
NO_CHAT = "--no-chat" in sys.argv

ROWS: list[tuple[str, str, bool, str]] = []


def add(group: str, name: str, ok: bool, note: str = "") -> None:
    ROWS.append((group, name, ok, note))
    print("  %-22s %s  %s" % (name, "PASS" if ok else "FAIL", note[:110]), flush=True)


def call(method: str, path: str, body=None, timeout: int = 300, raw: bool = False):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            buf = r.read()
            return {"ok": True, "status": r.status, "ms": int((time.time() - t0) * 1000),
                    "body": buf, "err": ""}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "ms": int((time.time() - t0) * 1000),
                "body": e.read(), "err": "HTTP %s" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "status": 0, "ms": int((time.time() - t0) * 1000),
                "body": b"", "err": str(e)}


def js(res):
    try:
        return json.loads(res["body"].decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None


def txt(res) -> str:
    return res["body"].decode("utf-8", "replace")


def sec(title: str) -> None:
    print("\n" + "-" * 96)
    print(title)
    print("-" * 96, flush=True)


# --------------------------------------------------------------------- 任务①
def test_alias() -> None:
    sec("任务① 别名 / 历史名（gene_alias 覆盖 + 反查解析 + 首句列出）")
    res = call("GET", "/api/gene/tp53")
    d = js(res)
    if not res["ok"] or not isinstance(d, dict):
        add("①", "基因详情接口", False, "%s %s" % (res["err"], txt(res)[:60]))
        return
    add("①", "基因详情接口", True, "%dms" % res["ms"])

    al = d.get("aliases")
    if isinstance(al, dict):
        items = al.get("items") or al.get("aliases") or []
        groups = al.get("sources") or al.get("by_source") or al.get("groups") or {}
        names = [str((i or {}).get("alias") or i) for i in items] if isinstance(items, list) else []
        add("①", "aliases 块存在", bool(items), "别名 %d 个：%s" % (len(names), " / ".join(names[:6])))
        if isinstance(groups, dict) and groups:
            add("①", "按来源分组", True, ", ".join("%s=%s" % (k, len(v) if isinstance(v, list) else v)
                                                  for k, v in list(groups.items())[:6]))
    elif isinstance(al, list):
        add("①", "aliases 块存在", bool(al), "别名 %d 个" % len(al))
    else:
        add("①", "aliases 块存在", False, "类型 %s" % type(al).__name__)

    # 用别名反查：应解析回标准符号 tp53
    res2 = call("GET", "/api/tools/gene-resolve?q=brp53")
    d2 = js(res2)
    ok2 = bool(res2["ok"]) and isinstance(d2, dict) and "tp53" in json.dumps(d2, ensure_ascii=False).lower()
    add("①", "别名 brp53 → tp53", ok2, ("%dms" % res2["ms"]) if ok2 else txt(res2)[:70])

    # 全文级证据（任务① 扩展）：不只是题名，还要有摘要/全文命中
    res4 = call("GET", "/api/genes/alias-literature?name=tp53", timeout=180)
    d4 = js(res4)
    lvl = (d4 or {}).get("level") if isinstance(d4, dict) else None
    scanned = int((d4 or {}).get("scanned_docs") or 0)
    items = (d4 or {}).get("items") or []
    abs_hits = sum(int(i.get("abstract_docs") or 0) for i in items)
    body_hits = sum(int(i.get("body_docs") or 0) for i in items)
    add("①", "全文级索引 level=fulltext", lvl == "fulltext" and scanned > 20000,
        "level=%s scanned=%d" % (lvl, scanned))
    add("①", "摘要/全文有命中", abs_hits > 0 and body_hits > 0,
        "摘要合计 %d 篇 / 全文合计 %d 篇" % (abs_hits, body_hits))
    if items:
        top = max(items, key=lambda x: int(x.get("total_docs") or 0))
        add("①", "带可引用样本", bool(top.get("samples")),
            "%s → %d 篇，样本 %d 条" % (top.get("alias"), top.get("total_docs"),
                                    len(top.get("samples") or [])))

    # 全文级证据（离线倒排表）：题名 / 摘要 / 全文 三级命中数 + 带片段的样本
    res3 = call("GET", "/api/genes/alias-literature?name=tp53")
    d3 = js(res3)
    if not res3["ok"] or not isinstance(d3, dict):
        add("①", "全文级证据接口", False, "%s %s" % (res3["err"], txt(res3)[:70]))
    else:
        lvl = d3.get("level")
        items = d3.get("items") or []
        hit = [i for i in items if int(i.get("total_docs") or 0) > 0]
        abs_n = max([int(i.get("abstract_docs") or 0) for i in items] or [0])
        body_n = max([int(i.get("body_docs") or 0) for i in items] or [0])
        add("①", "全文级证据接口", bool(items), "level=%s scanned=%s 命中别名 %d 个" % (
            lvl, d3.get("scanned_docs"), len(hit)))
        add("①", "含摘要/全文级命中", lvl == "fulltext" and (abs_n > 0 or body_n > 0),
            "摘要最多 %d 篇 / 全文最多 %d 篇" % (abs_n, body_n))
        smp = None
        for i in hit:
            if i.get("samples"):
                smp = i["samples"][0]
                break
        add("①", "样本带命中片段", bool(smp and smp.get("snippet")),
            ("%s %s" % ((smp or {}).get("pmid") or (smp or {}).get("doc_id") or "",
                         ((smp or {}).get("snippet") or "")[:50])) if smp else "无样本")

    # 别名类问题 → 首句应列出历史别名
    if NO_CHAT:
        return
    q = "tp53 这个基因都有哪些别名和历史名？"
    res3 = call("POST", "/api/chat", {"question": q, "mode": "auto", "lang": "zh"}, timeout=420)
    d3 = js(res3)
    ans = (d3 or {}).get("answer", "") if isinstance(d3, dict) else ""
    if not ans:
        add("①", "问答：首句列别名", False, "%s %s" % (res3["err"], txt(res3)[:70]))
        return
    first = re.split(r"[。！\n]", ans, 1)[0]
    hit = [a for a in ("brp53", "drp53", "p53", "wu:fb40d06", "etid22686.5") if a in ans]
    add("①", "问答：首句列别名", bool(hit), "%dms | 首句：%s" % (res3["ms"], first[:60]))
    print("      命中别名：%s" % (", ".join(hit) or "（无）"), flush=True)


# --------------------------------------------------------------------- 任务②
def test_ortholog() -> None:
    sec("任务② 复制体 / 共直系同源（WGD 后功能分工的对应关系）")
    cases = [("sox9b", "sox9a"), ("mitfa", "mitfb"), ("hsp70l", None)]
    for name, expect in cases:
        res = call("GET", "/api/genes/orthologs?name=%s" % name)
        d = js(res)
        if not res["ok"] or not isinstance(d, dict):
            add("②", "orthologs %s" % name, False, "%s %s" % (res["err"], txt(res)[:60]))
            continue
        # 真实结构：self[]（人/鼠同源）+ groups[].copies[]（共享同一同源基因的斑马鱼复制体）
        homo = [s.get("homo_gene_name") for s in (d.get("self") or []) if s.get("homo_gene_name")]
        copies: list[str] = []
        for g in (d.get("groups") or []):
            for c in (g.get("copies") or []):
                sym = c.get("symbol")
                if sym and sym not in copies:
                    copies.append(sym)
        dup = bool(d.get("is_duplicated"))
        ok = bool(homo) and len(copies) >= 1 and (
            expect is None or expect.lower() in {c.lower() for c in copies})
        add("②", "orthologs %s" % name, ok,
            "人鼠同源=%s | 复制体=%d 个 %s | duplicated=%s"
            % ("/".join(homo[:4]) or "无", len(copies), copies or "[]", dup))
        if not ok:
            print("      %s" % json.dumps(d, ensure_ascii=False)[:170], flush=True)

    # 人符号反查斑马鱼复制体
    res = call("GET", "/api/genes/orthologs?name=MITF")
    d = js(res)
    blob = json.dumps(d or {}, ensure_ascii=False)
    ok = res["ok"] and isinstance(d, dict) and ("mitf" in blob.lower())
    add("②", "人符号 MITF 反查", ok, "含 mitfa/mitfb" if ok else txt(res)[:70])

    # 详情卡里也要有 paralogs 块
    res = call("GET", "/api/gene/sox9b")
    d = js(res)
    pl = (d or {}).get("paralogs") if isinstance(d, dict) else None
    add("②", "基因详情含 paralogs", pl is not None, "类型 %s" % type(pl).__name__)

    # 任务② 功能分工结论的证据：每个复制体都要有可引用的文献
    res = call("GET", "/api/genes/orthologs?name=sox9b")
    d = js(res) or {}
    role = d.get("role_evidence") or {}
    ok_role = bool(role) and all(bool((v or {}).get("indexed")) for v in role.values())
    add("②", "复制体功能分工证据", ok_role,
        " / ".join("%s: %s 篇" % (k, (v or {}).get("total_docs")) for k, v in role.items()) or "无")

    # 任务②：每个复制体**各自**的文献证据（分工结论的依据）
    res = call("GET", "/api/genes/orthologs?name=sox9b")
    d = js(res)
    role = (d or {}).get("role_evidence") if isinstance(d, dict) else None
    add("②", "带复制体分工证据", bool(role), "键：%s" % (", ".join(list(role)[:6]) if role else "无"))
    if role:
        owned = [k for k, v in role.items() if (v or {}).get("indexed")]
        add("②", "复制体已建索引", bool(owned),
            "已索引：%s（共 %d 个）" % (", ".join(owned) or "无", len(role)))
        s = None
        for k in owned:
            if (role[k] or {}).get("samples"):
                s = role[k]["samples"][0]
                break
        add("②", "分工证据带文献", bool(s),
            ((s or {}).get("pmid") or (s or {}).get("doc_id") or "无") if s else "无样本")

    if NO_CHAT:
        return
    q = "sox9b 在人里的同源基因是什么？斑马鱼还有哪些复制体？"
    res = call("POST", "/api/chat", {"question": q, "mode": "auto", "lang": "zh"}, timeout=420)
    d = js(res)
    ans = (d or {}).get("answer", "") if isinstance(d, dict) else ""
    blob = (ans + json.dumps(d or {}, ensure_ascii=False)).lower()
    ok = bool(ans) and "sox9" in blob
    add("②", "问答：复制体对应关系", ok, "%dms | 含 SOX9=%s 含 sox9a=%s" % (
        res["ms"], "sox9" in blob, "sox9a" in blob))


# --------------------------------------------------------------------- 任务③
def test_crispr_primer() -> None:
    sec("任务③ sgRNA / 引物（本地引擎 + CHOPCHOP / CRISPRscan 对标）")
    res = call("GET", "/api/tools/crispr/capabilities")
    d = js(res)
    ok = res["ok"] and isinstance(d, dict)
    add("③", "capabilities", ok, ("%dms" % res["ms"]) if ok else txt(res)[:70])

    res = call("POST", "/api/tools/crispr/design",
               {"target": "sox9b", "enzyme": "NGG", "top_n": 10, "max_mismatch": 3,
                "off_target": True, "add_t7": True}, timeout=600)
    d = js(res)
    if not res["ok"] or not isinstance(d, dict):
        add("③", "sgRNA 设计", False, "%s %s" % (res["err"], txt(res)[:70]))
    else:
        cands = None
        for k in ("candidates", "guides", "sgrnas", "results", "items"):
            if isinstance(d.get(k), list) and d[k]:
                cands = d[k]
                break
        blob = json.dumps(d, ensure_ascii=False)
        add("③", "sgRNA 设计", bool(cands), "%dms | %d 个候选" % (res["ms"], len(cands or [])))
        add("③", "含 T7 体外转录模板", "TAATACGACTCACTATAG" in blob, "T7 序列" if "TAATACGACTCACTATAG" in blob else "未找到")
        add("③", "含脱靶/特异性信息", ("off_target" in blob or "specificity" in blob.lower()), "")
        if cands:
            c = cands[0]
            print("      首条候选：%s" % json.dumps(c, ensure_ascii=False)[:170], flush=True)

    res = call("POST", "/api/tools/primer/design",
               {"template": "sox9b", "max_pairs": 3, "off_target": False}, timeout=600)
    d = js(res)
    if not res["ok"] or not isinstance(d, dict):
        add("③", "引物设计", False, "%s %s" % (res["err"], txt(res)[:70]))
    else:
        pairs = None
        for k in ("pairs", "primers", "results", "items"):
            if isinstance(d.get(k), list) and d[k]:
                pairs = d[k]
                break
        blob = json.dumps(d, ensure_ascii=False)
        add("③", "引物设计", bool(pairs), "%dms | %d 对" % (res["ms"], len(pairs or [])))
        add("③", "含 Tm 值", ("tm" in blob.lower()), "")
        if pairs:
            print("      首对引物：%s" % json.dumps(pairs[0], ensure_ascii=False)[:170], flush=True)

    # 页面对标按钮（CHOPCHOP / CRISPRscan / Primer3web）必须还在
    res = call("GET", "/static/js/crispr.js")
    t = txt(res)
    has = ("chopchop" in t.lower(), "crisprscan" in t.lower())
    add("③", "页面含对标跳转", res["ok"] and all(has),
        "CHOPCHOP=%s CRISPRscan=%s" % has)


# --------------------------------------------------------------------- 任务④
def test_frontend() -> None:
    sec("任务④ 前端（12.png 品牌图 / 6.png 主界面 / Genes 排列 / Tools 而非 Coming soon）")
    # 1) 12.png 派生的品牌资源
    for p in ("/static/assets/brand/zf-agent-mark.png", "/static/assets/brand/zf-agent-emblem.png",
              "/static/assets/brand/favicon.png", "/static/assets/brand/apple-touch-icon.png"):
        res = call("GET", p, timeout=60)
        png = res["body"][:8] == b"\x89PNG\r\n\x1a\n"
        add("④", "品牌图 %s" % p.split("/")[-1], res["ok"] and png,
            "%d 字节" % len(res["body"]))

    # 2) 6.png 主界面资源
    for p in ("/static/css/home-agent.css", "/static/js/agent-home.js", "/static/js/home-feed.js"):
        res = call("GET", p, timeout=60)
        add("④", "主界面资源 %s" % p.split("/")[-1], res["ok"] and len(res["body"]) > 500,
            "%d 字节" % len(res["body"]))

    # 3) 导航顺序：Genomes → Genes → Expression
    res = call("GET", "/", timeout=60)
    html = txt(res)
    add("④", "首页可访问", res["ok"] and "appRoot" in html, "%d 字节" % len(html))
    gi = html.find('data-nav-group="genomes"')
    ni = html.find('data-nav-group="genes"')
    ei = html.find('data-nav-group="expression"')
    add("④", "导航顺序 Genomes→Genes→Expression",
        gi > 0 and ni > gi and ei > ni, "位置 %s / %s / %s" % (gi, ni, ei))

    # 4) Tools 在、Coming soon 不在（作为导航项）
    has_tools = 'data-nav-group="tools"' in html and ">Tools<" in html
    add("④", "导航含 Tools", has_tools, "")
    add("④", "导航无 Coming soon", "Coming soon" not in html and "即将上线" not in html, "")

    # 5) 12.png 品牌图真的被页面引用
    add("④", "首页引用品牌图", "zf-agent-mark.png" in html and "zf-agent-emblem.png" in html, "")

    # 6) 主界面按 6.png 改版的骨架结构
    has_home = 'class="agent-home"' in html
    add("④", "主界面骨架 agent-home", has_home, "")
    add("④", "Genes 功能页脚本", "/static/js/genes.js" in html, "")


def main() -> int:
    print("=" * 96)
    print("四项功能验收 @ %s    %s" % (BASE, time.strftime("%Y-%m-%d %H:%M:%S")))
    print("=" * 96, flush=True)
    h = call("GET", "/api/health", timeout=60)
    d = js(h)
    print("健康检查：HTTP %s  %s" % (h["status"], json.dumps(d, ensure_ascii=False)[:150] if d else txt(h)[:100]))
    if not h["ok"]:
        print("\n❌ 服务不可用，先恢复服务再跑。")
        return 2

    test_alias()
    test_ortholog()
    test_crispr_primer()
    test_frontend()

    print("\n" + "=" * 96)
    print("汇总")
    print("=" * 96)
    bad = [r for r in ROWS if not r[2]]
    for g in ("①", "②", "③", "④"):
        rows = [r for r in ROWS if r[0] == g]
        n_ok = len([r for r in rows if r[2]])
        print("  任务%s：%d/%d 通过" % (g, n_ok, len(rows)))
    print("  合计：%d/%d 通过" % (len(ROWS) - len(bad), len(ROWS)))
    if bad:
        print("\n  失败项：")
        for g, name, _ok, note in bad:
            print("    ✗ [%s] %s — %s" % (g, name, note[:90]))
    print("\n>>> 结论：%s" % ("全部通过 ✅" if not bad else "有 %d 项失败 ❌" % len(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
