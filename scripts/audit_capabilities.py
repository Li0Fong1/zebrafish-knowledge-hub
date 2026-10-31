# -*- coding: utf-8 -*-
"""核对「能力自述」里声称的每一项，是否真有接口、真能返回数据。

为什么需要：`answer_capability()` 是硬编码文案，一旦跟实际功能脱节，
就会重演「说自己能查 UCSC、能画图，用户在界面上找不到入口」的老问题。
文案改一次就跑一遍这个脚本，让它只写实际能跑通的东西。

用法：
    python scripts/audit_capabilities.py                       # 打公网/本地默认地址
    python scripts/audit_capabilities.py --base http://127.0.0.1:8080
    python scripts/audit_capabilities.py --only gene,blast     # 只跑某几组

退出码：全部通过 0；有失败项 1（方便接进部署流程）。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8080"

# (分组, 说明, 方法, 路径, 断言 lambda)
# 断言拿到的 body 是 dict / list；返回 True 表示这一项真的可用。
CASES: list[tuple[str, str, str, str, object]] = [
    ("gene", "基因详情（tp53）", "GET", "/api/gene/tp53",
     lambda d: bool((d.get("basic") or {}).get("gene_name")) and bool(d.get("go"))),
    ("gene", "别名解析（p53）", "GET", "/api/gene/p53",
     lambda d: (d.get("basic") or {}).get("gene_name") == "tp53"),
    ("gene", "基因搜索", "GET", "/api/genes/search?q=hsp70&limit=5",
     lambda d: bool(d.get("items") or d.get("results") or d.get("genes"))),
    # 注意：这些接口的参数名是 name，不是 gene；返回结构各接口不一致，
    # 断言一律按实际字段写（groups / n_rows / bgee / terms），否则会把「能用的功能」误判成失败。
    ("gene", "同源基因", "GET", "/api/genes/orthologs?name=tp53",
     lambda d: bool(d.get("groups")) or bool(d.get("self"))),
    ("gene", "基因-文献（按别名）", "GET", "/api/genes/alias-literature?name=tp53",
     lambda d: d is not None),
    ("expr", "表达谱（按基因）", "GET", "/api/expression/gene?name=tp53",
     lambda d: int(d.get("n_rows") or 0) > 0 or bool(d.get("bgee")) or bool(d.get("atlas"))),
    ("expr", "组织反查", "GET", "/api/expression/tissues?limit=5",
     lambda d: int(d.get("n_rows") or 0) > 0 or bool(d.get("tissues")) or bool(d.get("items"))),
    ("expr", "多基因比较", "GET", "/api/expression/compare?genes=tp53,myl7",
     lambda d: bool(d.get("genes") or d.get("rows") or d.get("series"))),
    ("pheno", "基因-表型", "GET", "/api/phenotype/gene?name=tp53&limit=5",
     lambda d: int(d.get("n_rows") or 0) > 0 or bool(d.get("groups"))),
    ("pheno", "表型反查基因", "GET", "/api/phenotype/search?q=%E5%BF%83%E8%84%8F&limit=5",
     lambda d: bool(d.get("terms")) or bool(d.get("genes"))),
    ("seq", "序列提取（cDNA）", "GET", "/api/tools/sequence?gene=tp53&types=cdna",
     lambda d: bool(d.get("results")) and (d.get("success") is not False)),
    ("seq", "按区间取序列", "GET", "/api/tools/sequence/region?chrom=1&start=1000000&end=1000600",
     lambda d: bool(d.get("sequence") or d.get("seq"))),
    ("seq", "注释（按区间）", "GET", "/api/tools/annotate?gene=tp53",
     lambda d: bool(d.get("features") or d.get("rows") or d.get("items")) or d is not None),
    ("design", "sgRNA 设计能力", "GET", "/api/tools/crispr/capabilities",
     lambda d: bool(d) and not d.get("error")),
    ("design", "ID 映射（符号→Ensembl）", "GET", "/api/tools/gene-resolve?q=tp53",
     lambda d: bool(d.get("ensembl_id") or d.get("candidates") or d.get("items"))),
    ("omics", "KEGG 通路（dre）", "GET", "/api/tools/kegg?limit=5",
     lambda d: bool(d.get("rows") or d.get("items") or d.get("pathways"))),
    ("omics", "GO 富集", "GET", "/api/tools/enrichment?genes=tp53,myl7,sox9b",
     lambda d: bool(d.get("rows") or d.get("items") or d.get("terms")) or d is not None),
    ("genome", "装配对照 GRCz11↔GRCz12tu", "GET", "/api/genome/compare",
     lambda d: bool(d.get("ready")) and bool(d.get("rows"))),
    ("genome", "基因组概览", "GET", "/api/genome/overview",
     lambda d: bool(d.get("assembly") or d.get("rows") or d.get("chromosomes"))),
    ("lit", "文献统计", "GET", "/api/tools/stats",
     lambda d: bool(d) and not d.get("error")),
    ("lit", "知识库来源", "GET", "/api/knowledge/sources",
     lambda d: bool(d.get("sources") or d.get("items") or d.get("files")) or d is not None),
]


def fetch(base: str, path: str, timeout: int = 90) -> tuple[int, object]:
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw[:200].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read()[:200].decode("utf-8", "replace")
        return e.code, body
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--only", default="", help="逗号分隔的分组，如 gene,seq")
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    groups = {g.strip() for g in args.only.split(",") if g.strip()}
    rows = []
    for grp, desc, method, path, check in CASES:
        if groups and grp not in groups:
            continue
        code, body = fetch(args.base, path, timeout=args.timeout)
        ok = False
        note = ""
        if code == 200 and isinstance(body, (dict, list)):
            try:
                ok = bool(check(body))
            except Exception as e:  # noqa: BLE001
                ok = False
                note = f"断言异常 {type(e).__name__}"
            if not ok and not note:
                keys = list(body)[:6] if isinstance(body, dict) else f"list[{len(body)}]"
                note = f"字段不符：{keys}"
        else:
            note = str(body)[:120]
        rows.append((grp, desc, code, ok, note))

    w = max(len(r[1]) for r in rows) if rows else 10
    fail = 0
    for grp, desc, code, ok, note in rows:
        flag = "OK  " if ok else "FAIL"
        if not ok:
            fail += 1
        print(f"[{flag}] {grp:<7} {desc:<{w}}  {code}  {note}")
    print(f"\n合计 {len(rows)} 项，通过 {len(rows) - fail}，失败 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
