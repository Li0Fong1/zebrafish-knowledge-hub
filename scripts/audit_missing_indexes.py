# -*- coding: utf-8 -*-
"""代码驱动索引审计：从源码 SQL 提取 (表, 过滤列)，与生产实际索引比对。

输出：缺失索引候选（表规模 > 5k 行 且 过滤列不是任何索引的首列）。
只读分析，不连库。
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INV = os.path.join(ROOT, "output", "_db_inventory.json")

SQL_RE = re.compile(r"(SELECT|select)\b[\s\S]{20,2000}?(?=[\"']\s*[,)]|\Z)")

# 从字符串字面量里抓 SQL（支持三引号与普通引号拼接，粗略但够用）
STR_RE = re.compile(r"(\"\"\"|'''|\"|')((?:\\.|(?!\1)[\s\S])*?)\1")

TABLE_REF_RE = re.compile(r"\b(?:FROM|from|JOIN|join)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?")
WHERE_RE = re.compile(r"\bWHERE\b([\s\S]*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|\bHAVING\b|\Z)",
                      re.IGNORECASE)
COND_COL_RE = re.compile(r"`?([a-zA-Z_][a-zA-Z0-9_]{2,})`?\s*(?:=|IN\s*\(|LIKE\b|>|<|>=|<=)")
JOIN_ON_RE = re.compile(r"\bON\b([\s\S]*?)(?:\bJOIN\b|\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\Z)",
                        re.IGNORECASE)


def strip_comments(s: str) -> str:
    s = re.sub(r"--[^\n]*", " ", s)
    s = re.sub(r"/\*[\s\S]*?\*/", " ", s)
    return s


def iter_py_files():
    for base in ("database", "api", "core"):
        d = os.path.join(ROOT, base)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.endswith(".py"):
                yield os.path.join(d, fn)


def collect_sql():
    """返回 [(file, sql)]"""
    out = []
    for path in iter_py_files():
        try:
            txt = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for m in STR_RE.finditer(txt):
            s = m.group(2)
            if not re.search(r"\bSELECT\b", s, re.IGNORECASE):
                continue
            if not re.search(r"\bFROM\b", s, re.IGNORECASE):
                continue
            # 排除提示词模板（表结构说明、LLM 指令），它们不是真正执行的 SQL
            if "【" in s or "表结构" in s or "必须遵守" in s or "输出1条" in s:
                continue
            out.append((os.path.basename(path), strip_comments(s)))
    return out


def main() -> int:
    inv = json.load(open(INV, encoding="utf-8"))
    rows = {r["table"]: r.get("rows") or 0 for r in inv["exact_rows"]}
    idx = {c["table"]: c["indexes"] for c in inv["checks"]}
    tables = set(inv["tables"])

    def first_cols(t):
        cols = set()
        for _name, v in (idx.get(t) or {}).items():
            if v["cols"]:
                cols.add(v["cols"][0])
        return cols

    hits = {}   # (table, col) -> {"n": 次数, "files": set(), "samples": set()}
    scanned = 0
    for fn, sql in collect_sql():
        tabs = [t for t in TABLE_REF_RE.findall(sql) if t in tables]
        if not tabs:
            continue
        scanned += 1
        conds = []
        for m in WHERE_RE.finditer(sql):
            conds.append(m.group(1))
        for m in JOIN_ON_RE.finditer(sql):
            conds.append(m.group(1))
        cols = set()
        for c in conds:
            for col in COND_COL_RE.findall(c):
                cols.add(col)
        for t in set(tabs):
            for col in cols:
                k = (t, col)
                e = hits.setdefault(k, {"n": 0, "files": set(), "sample": sql.strip()[:160]})
                e["n"] += 1
                e["files"].add(fn)

    cands = []
    for (t, col), e in hits.items():
        if rows.get(t, 0) < 5000:
            continue
        if col in first_cols(t):
            continue
        cands.append({
            "table": t, "column": col, "rows": rows[t], "hits": e["n"],
            "files": sorted(e["files"])[:4], "sample": e["sample"],
            "existing_first_cols": sorted(first_cols(t)),
        })
    cands.sort(key=lambda x: (-x["rows"], -x["hits"]))

    print("扫描到 SQL 语句: %d 条；表: %d 张" % (scanned, len(tables)))
    print("缺失索引候选: %d 个\n" % len(cands))
    for c in cands:
        print("## %s.%s  rows=%d  命中=%d  文件=%s"
              % (c["table"], c["column"], c["rows"], c["hits"], ",".join(c["files"])))
        print("   已有首列: %s" % ",".join(c["existing_first_cols"]))
        print("   样例: %s" % c["sample"].replace("\n", " ")[:150])
    json.dump(cands, open(os.path.join(ROOT, "output", "_index_candidates.json"), "w",
                          encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
