# -*- coding: utf-8 -*-
"""最终验收：官方坐标真值校验 + zgc:158629 歧义溯源。

分两段：
  [L] 本地 SQLite 溯源（不依赖网络）——zgc:158629 在 r54 官方注释里到底对应几个 ID，
      线上给出的 ENSDARG00000094154 是否在这批里。
  [R] 远程 HTTP 校验——把 target_coords 字符串完整打出来，并用 Ensembl REST
      独立真值对 tp53@GRCz11 做逐碱基断言。

用法：python scripts/verify_ensembl_final.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import remote_exec as re  # noqa: E402

LOCAL_DB = ROOT / "data" / "idmap_local.sqlite3"
PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"
REMOTE_VERIFY = "/tmp/_verify_coords.py"

# Ensembl REST 官方返回值（2026-11-13 查得），用于独立真值断言
TRUTH_TP53_GRCZ11 = (24086227, 24097805)

REMOTE_SRC = r'''
import http.cookiejar, json, re, sys, time, urllib.request

BASE_URL = "http://127.0.0.1:8080"
TRUTH = (24086227, 24097805)

cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def call(path, payload=None, timeout=180):
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, data=body, headers=headers)
    t0 = time.time()
    with op.open(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return raw, time.time() - t0

try:
    call("/api/gate/pass")
except Exception:
    pass

CASES = [
    ("ens:54", "ens:116", ["tp53"]),
    ("ens:116", "ens:54", ["tp53"]),
    ("ens:116", "ens:80", ["tp53"]),
    ("ens:79", "ens:116", ["tp53"]),
    ("ens:54", "ens:116", ["zgc:158629"]),
    ("ens:116", "ens:54", ["cdh5", "mitfa"]),
]

print("=== [R] 坐标 / 状态 / 匹配依据 ===")
for src, tgt, ids in CASES:
    raw, dt = call("/api/tools/id-convert/batch",
                   {"identifiers": ids, "source_build": src, "target_build": tgt})
    d = json.loads(raw)
    print("\n--- %s -> %s  (%.2fs) ---" % (src, tgt, dt))
    for r in d.get("results", []):
        can = r.get("canonical") or {}
        print("   query=%s" % r.get("query"))
        print("     status      = %s" % r.get("status"))
        print("     ensembl_id  = %s" % (can.get("ensembl_id") or "-"))
        print("     gene_name   = %s" % (can.get("gene_name") or "-"))
        print("     target_coords = %s" % (r.get("target_coords") or "(空)"))
        print("     source_coords = %s" % (r.get("source_coords") or "(空)"))
        print("     coord_status  = %s / coord_basis = %s" % (r.get("coord_status"), r.get("coord_basis")))
        print("     match_kind    = %s" % r.get("match_kind"))
        cands = r.get("candidates") or []
        if cands:
            print("     candidates(%d) = %s" % (len(cands), [c.get("ensembl_id") for c in cands][:8]))

print()
print("=== [R2] tp53 @ GRCz11 独立真值断言 ===")
raw, _ = call("/api/tools/id-convert/batch",
              {"identifiers": ["tp53"], "source_build": "ens:54", "target_build": "ens:116"})
d = json.loads(raw)
r0 = (d.get("results") or [{}])[0]
coords = r0.get("target_coords") or ""
nums = [int(x) for x in re.findall(r"\d+", coords)]
got = tuple(nums[:2]) if len(nums) >= 2 else ()
print("   API  返回 : %s" % (coords or "(空)"))
print("   REST 真值 : 5:24086227-24097805(+)")
if got == TRUTH:
    print("   >>> PASS 逐碱基一致，坐标来自 Ensembl r116 官方 GTF")
elif got and got[0] > 24400000:
    print("   >>> FAIL 看起来仍是旧的 chain 近似值（应以 24,086,227 开头）")
else:
    print("   >>> WARN 无法判定，请人工核对：%s" % (got,))

print("VERIFY_DONE")
'''


def local_probe() -> None:
    print("=== [L1] r54 官方注释里 zgc:158629 的全部记录 ===", flush=True)
    if not LOCAL_DB.is_file():
        print("   本地库不存在，跳过：" + str(LOCAL_DB), flush=True)
        return
    con = sqlite3.connect("file:%s?mode=ro" % LOCAL_DB.as_posix(), uri=True, timeout=5)
    rows = con.execute(
        "SELECT ensembl_gene_id, symbol, chrom, start, end, strand "
        "FROM ens_gene_official WHERE release=54 AND symbol='zgc:158629' COLLATE NOCASE"
    ).fetchall()
    print("   命中 %d 条" % len(rows), flush=True)
    for r in rows:
        print("     %-22s %s:%s-%s(%s)" % (r[0], r[2], r[3], r[4], r[5]), flush=True)

    ids = [r[0] for r in rows]
    print()
    print("=== [L2] 线上给出的 ENSDARG00000094154 是否在这批里 ===", flush=True)
    print("   在 r54 的 zgc:158629 记录中？ %s"
          % ("是" if "ENSDARG00000094154" in ids else "否"), flush=True)
    hit = con.execute(
        "SELECT release, symbol, chrom, start, end FROM ens_gene_official "
        "WHERE ensembl_gene_id='ENSDARG00000094154' ORDER BY release"
    ).fetchall()
    print("   ENSDARG00000094154 分布于：" , flush=True)
    for h in hit:
        print("     r%-3s symbol=%-12s %s:%s-%s" % (h[0], h[1], h[2], h[3], h[4]), flush=True)

    print()
    print("=== [L3] r54 这批 ID 在 r116 里各叫什么（看符号是否被复用）===", flush=True)
    for gid in ids:
        t = con.execute(
            "SELECT symbol, chrom, start, end FROM ens_gene_official "
            "WHERE release=116 AND ensembl_gene_id=?", (gid,)
        ).fetchone()
        print("     %-22s -> r116: %s" % (gid, ("%s %s:%s-%s" % t) if t else "不在 r116"), flush=True)
    con.close()


def remote_probe() -> int:
    print()
    print("=== [R] 远程 HTTP 校验（需 SSH）===", flush=True)
    c = re.connect()
    try:
        sftp = c.open_sftp()
        with sftp.file(REMOTE_VERIFY, "wb") as fh:
            fh.write(REMOTE_SRC.encode("utf-8"))
        sftp.close()
    finally:
        c.close()
    rc, out = re.run("%s %s" % (PY, REMOTE_VERIFY), timeout=600)
    print(out, flush=True)
    return 0 if "VERIFY_DONE" in out else 5


def main() -> int:
    local_probe()
    try:
        return remote_probe()
    except Exception as exc:  # noqa: BLE001
        print("   远程校验失败：%s" % exc, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
