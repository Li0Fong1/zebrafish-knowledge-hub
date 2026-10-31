# -*- coding: utf-8 -*-
"""ID 解析 / 批量转换 回归测试。"""
from core.entry import bootstrap_app

bootstrap_app()

import json
import sys
import urllib.error
import urllib.request

from fastapi.testclient import TestClient

from api.api_server import app

CASES = [
    ("p53", True, "tp53"),
    ("30590", True, "tp53"),
    ("ZDB-GENE-990415-270", True, "tp53", True),  # 须本地库命中，不依赖 Ensembl 在线
    ("ENSDARG00000035559", True, "tp53"),
    ("shh", True, "shha"),
    ("ZDB-GENE-010321-1", False, None),  # 无效/非 tp53 示例 ID
    ("not_a_gene_xyz", False, None),
]

BATCH_TEXT = "p53\n30590\nZDB-GENE-990415-270"


def test_local_resolve():
    from gene_id_mapper import resolve_gene_identifier

    print("=== 本地 resolve_gene_identifier ===")
    failed = 0
    for item in CASES:
        query, expect_ok, expect_name = item[0], item[1], item[2]
        local_only = item[3] if len(item) > 3 else False
        r = resolve_gene_identifier(query, use_ensembl_fallback=not local_only)
        ok = r.get("resolved") == expect_ok
        name = (r.get("canonical") or {}).get("gene_name")
        if expect_ok and name != expect_name:
            ok = False
        tag = "local" if local_only else "full"
        status = "OK" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  [{status}] ({tag}) {query!r} -> resolved={r.get('resolved')} name={name!r}")
    return failed


def test_local_batch():
    from gene_toolkit import batch_id_convert, extract_identifiers_from_text

    print("\n=== 本地 batch_id_convert ===")
    ids = extract_identifiers_from_text(BATCH_TEXT)
    data = batch_id_convert(ids)
    failed = 0
    for r in data.get("results") or []:
        c = r.get("canonical") or {}
        ok = r.get("resolved") and c.get("gene_name") == "tp53"
        if not ok:
            failed += 1
        print(f"  [{'OK' if ok else 'FAIL'}] {r.get('query')} -> {c.get('gene_name')}")
    return failed


def _auth_headers() -> dict:
    try:
        from api.web_security import get_access_token
        token = get_access_token()
        if token:
            return {"X-Access-Token": token}
    except Exception:
        pass
    return {}


def test_api_routes():
    print("\n=== FastAPI TestClient 路由 ===")
    c = TestClient(app)
    headers = _auth_headers()
    failed = 0

    r = c.get("/api/tools/gene-resolve", params={"q": "p53"}, headers=headers)
    if r.status_code != 200 or not r.json().get("resolved"):
        print(f"  [FAIL] gene-resolve GET {r.status_code}")
        failed += 1
    else:
        print("  [OK] GET /api/tools/gene-resolve")

    r = c.post("/api/tools/id-convert/text", json={"text": BATCH_TEXT}, headers=headers)
    if r.status_code != 200:
        print(f"  [FAIL] id-convert/text POST {r.status_code} {r.text[:200]}")
        failed += 1
    else:
        n = sum(1 for x in r.json().get("results", []) if x.get("resolved"))
        print(f"  [OK] POST /api/tools/id-convert/text resolved={n}/3")

    r = c.post("/api/tools/id-convert/batch", json={"identifiers": ["p53", "30590"]}, headers=headers)
    if r.status_code != 200:
        print(f"  [FAIL] id-convert/batch POST {r.status_code}")
        failed += 1
    else:
        print("  [OK] POST /api/tools/id-convert/batch")

    return failed


def test_live_server(base="http://127.0.0.1:8080"):
    print(f"\n=== 运行中 Web 服务 {base} ===")
    failed = 0

    def _get(path):
        req = urllib.request.Request(base + path, headers=_auth_headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())

    def _post(path, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            base + path, data=data,
            headers={"Content-Type": "application/json", **_auth_headers()},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())

    try:
        st, _ = _get("/api/health")
        print(f"  [OK] health {st}")
    except Exception as e:
        print(f"  [SKIP] Web 未运行: {e}")
        return 0

    try:
        st, d = _get("/api/tools/gene-resolve?q=p53")
        print(f"  [{'OK' if d.get('resolved') else 'FAIL'}] gene-resolve")
        if not d.get("resolved"):
            failed += 1
    except Exception as e:
        print(f"  [FAIL] gene-resolve: {e}")
        failed += 1

    try:
        st, d = _post("/api/tools/id-convert/text", {"text": BATCH_TEXT})
        n = sum(1 for x in d.get("results", []) if x.get("resolved"))
        print(f"  [OK] batch text resolved={n}/3")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("  [WARN] batch POST 404 — Web 需重启以加载新路由（前端有 fallback）")
        else:
            print(f"  [FAIL] batch text HTTP {e.code}")
            failed += 1
    except Exception as e:
        print(f"  [FAIL] batch text: {e}")
        failed += 1

    return failed


def test_zfin_xref_in_db():
    from mysql_client import MySQLClient

    print("\n=== 数据库 ZFIN xref ===")
    c = MySQLClient()
    r = c.execute_sql(
        "SELECT COUNT(*) AS cnt FROM gene_xref WHERE db_name='zfin' AND db_id LIKE 'ZDB-GENE-%'"
    )
    cnt = (r.get("data") or [{}])[0].get("cnt", 0)
    r2 = c.execute_sql(
        "SELECT gb.gene_name FROM gene_xref gx JOIN gene_basic gb ON gx.gene_id=gb.gene_id "
        "WHERE gx.db_id='ZDB-GENE-990415-270' OR gx.db_id LIKE 'ZDB-GENE-990415-270|%' LIMIT 1"
    )
    tp53 = (r2.get("data") or [{}])[0].get("gene_name") if r2.get("data") else None
    r3 = c.execute_sql("SELECT COUNT(*) AS cnt FROM gene_xref WHERE db_id LIKE '%|%'")
    corrupt = (r3.get("data") or [{}])[0].get("cnt", 0)
    print(f"  ZDB-GENE xref 总数: {cnt}")
    print(f"  损坏 xref（含|）: {corrupt}")
    print(f"  ZDB-GENE-990415-270 -> {tp53!r}")
    failed = 0
    if corrupt:
        print("  [FAIL] 存在损坏 xref，请运行: python -m scripts.build_gene_aliases --repair-xrefs")
        failed += 1
    elif cnt < 1000:
        print("  [WARN] ZFIN xref 偏少，请运行: python -m scripts.build_gene_aliases")
        failed += 1 if tp53 != "tp53" else 0
    elif tp53 != "tp53":
        print("  [FAIL] tp53 ZFIN ID 未入库")
        failed += 1
    else:
        print("  [OK] ZFIN xref 正常")
    return failed


if __name__ == "__main__":
    total = 0
    total += test_zfin_xref_in_db()
    total += test_local_resolve()
    total += test_local_batch()
    total += test_api_routes()
    total += test_live_server()
    print(f"\n{'=' * 40}")
    if total:
        print(f"失败/警告项: {total}")
    else:
        print("全部通过 failed=0")
    sys.exit(1 if total else 0)
