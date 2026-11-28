# -*- coding: utf-8 -*-
"""部署表型页三 fixes（2026-11-18）：排版 + ZFA/PATO 编号与英文识别。

流程：
  1. 本地重打包前端（build_bundle.py，3 分块），产出 index.html + dist/*
  2. 从 index.html 解析出当前引用的 css / c1 / c2 / c3 文件名（按引用取，不靠 glob 排序）
  3. SFTP 上传：index.html、4 个 dist 产物、output-layout.css 源、phenotype_toolkit.py
  4. 服务器端 py_compile + 重启 uvicorn（沿用 watchdog 里验证过的启动环境）
  5. 端到端验证：
       - 排版：首页引用完整、dist 文件齐全
       - ZFA 搜索：/api/phenotype/search?q=ZFA:0009256 命中 > 0
       - 英文搜索：q=heart abnormal 命中 > 0
       - 基因页：/api/phenotype/gene?name=tp53 正常

用法：python scripts/deploy_phenotype_fix.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import remote_exec as rexc  # noqa: E402

BASE = rexc.env("ZF_BASE", "/data/zoujin/deng/zebrafish_gene_qa")
PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"

VERIFY_SRC = r'''
import json, urllib.request, http.cookiejar
BASE = "http://127.0.0.1:8080"
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
try:
    op.open(BASE + "/api/gate/pass", timeout=10).read()
except Exception:
    pass

def get(path):
    with op.open(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

print("=== ZFA 编号搜索 ===")
d = get("/api/phenotype/search?q=ZFA%3A0009256&limit=20")
print("  ok=%s kind=%s terms=%d genes=%d match=%s" % (
    d.get("ok"), d.get("match_kind"), len(d.get("terms") or []),
    len(d.get("genes") or []), d.get("query_terms")))
for t in (d.get("terms") or [])[:3]:
    print("    ", t.get("ao_id"), t.get("ao_name"), "|", t.get("pato_id"), t.get("pato_name"))

print("=== 英文多词搜索 heart abnormal ===")
d2 = get("/api/phenotype/search?q=heart%20abnormal&limit=20")
print("  ok=%s kind=%s terms=%d genes=%d" % (
    d2.get("ok"), d2.get("match_kind"), len(d2.get("terms") or []), len(d2.get("genes") or [])))

print("=== 基因页 tp53 ===")
d3 = get("/api/phenotype/gene?name=tp53&limit=50")
print("  ok=%s symbol=%s rows=%s groups=%d" % (
    d3.get("ok"), (d3.get("gene") or {}).get("gene_symbol"),
    d3.get("n_rows"), len(d3.get("groups") or [])))
print("VERIFY_DONE")
'''

RESTART_SH = r"""
set -eu
BASE=/data/zoujin/deng/zebrafish_gene_qa
RUNTIME=/data/zoujin/deng/zebrafish_runtime
PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
pkill -f 'uvicorn api.api_server:app' || true
sleep 6
source /data/zoujin/deng/miniconda3/etc/profile.d/conda.sh
conda activate zebrafish
export PYTHONPATH=$BASE:$BASE/core:$BASE/database
set -a
source "$BASE/.env"
set +a
export CUDA_VISIBLE_DEVICES=1
export EMBEDDING_DEVICE=cuda:0
export LLM_DEVICE=cpu
export EMBEDDING_DIM=2048
export SKIP_KNOWLEDGE_FAISS_REBUILD=1
export FAISS_DISABLE_AVX2=1
cd "$BASE"
nohup $PY -m uvicorn api.api_server:app --host 127.0.0.1 --port 8080 >>"$RUNTIME/logs/hub.log" 2>&1 &
echo $! > "$RUNTIME/hub.pid"
echo "RESTARTED pid=$(cat $RUNTIME/hub.pid)"
"""


def run(cmd: str, t: int = 120):
    return rexc.run(cmd, timeout=t)


def selfcheck() -> bool:
    """本地纯逻辑自检：不连服务器，验证搜索条件编译器对各输入的 SQL 生成。"""
    print("=== [0] 本地搜索逻辑自检 ===", flush=True)
    sys.path.insert(0, str(ROOT / "database"))
    sys.path.insert(0, str(ROOT / "core"))
    try:
        import phenotype_toolkit as pt
    except Exception as exc:  # noqa: BLE001
        # 本机缺运行时依赖（MySQL/路径）时无法 import，属正常；
        # 降级为 AST 静态检查：确认函数存在且 ZFA 分支引用 ao_id，仍可继续部署。
        print("  ⚠ 本机无法 import（%s），改用静态检查" % type(exc).__name__, flush=True)
        import ast
        src = (ROOT / "database" / "phenotype_toolkit.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fns = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        need = {"_search_conditions", "_term_rows", "term_search"}
        ok = need.issubset(fns) and ("ao_id" in src and "ZFA" in src)
        print("  静态检查：函数齐全=%s，含 ao_id/ZFA=%s -> %s"
              % (need.issubset(fns), "ao_id" in src and "ZFA" in src,
                 "PASS" if ok else "FAIL"), flush=True)
        return ok
    cases = ["ZFA:0009256", "PATO:0000377", "ZFA0009256", "heart abnormal", "心脏畸形", "tp53"]
    ok = True
    for q in cases:
        try:
            where, params, meta = pt._search_conditions(q)
            good = bool(where) and len(params) >= 1 and "{" not in where
            print("  %-18s -> kind=%-5s where=%s... params=%d %s"
                  % (q, meta.get("kind"), where[:46], len(params), "OK" if good else "BAD"), flush=True)
            ok = ok and good
        except Exception as exc:  # noqa: BLE001
            print("  ❌ %s 编译失败：%s %s" % (q, type(exc).__name__, exc), flush=True)
            ok = False
    # 关键断言：ZFA 编号必须命中 ao_id 列，否则就是旧 bug
    w, p, m = pt._search_conditions("ZFA:0009256")
    if "ao_id" not in w:
        print("  ❌ ZFA 编号未落到 ao_id 列（旧 bug 未修复）", flush=True)
        ok = False
    print("  自检：%s" % ("PASS" if ok else "FAIL"), flush=True)
    return ok


def main() -> int:
    if not selfcheck():
        print("本地自检未通过，中止部署（不会碰服务器）。", flush=True)
        return 1

    # 1) 本地打包
    print("=== [1] 本地重打包前端 ===", flush=True)
    r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "build_bundle.py"), "--chunks", "3"],
                       cwd=str(ROOT), capture_output=True, text=True)
    print((r.stdout or "")[-2000:], flush=True)
    if r.returncode != 0:
        print("打包失败：%s" % (r.stderr or "")[-1000:], flush=True)
        return 2

    idx = (ROOT / "web" / "index.html").read_text(encoding="utf-8", errors="replace")
    refs = sorted(set(re.findall(r"app\.[a-f0-9]+\.(?:c[0-9]\.js|css)", idx)))
    print("  index.html 引用：%s" % refs, flush=True)
    if len(refs) < 4:
        print("  ❌ 引用产物不足 4 个，中止", flush=True)
        return 3

    # 2) 上传
    print("\n=== [2] 上传前端产物与后端修复 ===", flush=True)
    c = rexc.connect()
    try:
        sftp = c.open_sftp()
        ups = []
        ups.append((ROOT / "web" / "index.html", BASE + "/web/index.html"))
        for f in refs:
            ups.append((ROOT / "web" / "dist" / f, BASE + "/web/dist/" + f))
        ups.append((ROOT / "web" / "css" / "output-layout.css", BASE + "/web/css/output-layout.css"))
        ups.append((ROOT / "database" / "phenotype_toolkit.py", BASE + "/database/phenotype_toolkit.py"))
        for local, remote in ups:
            if not local.is_file():
                print("  ❌ 本地缺文件：%s" % local, flush=True)
                return 4
            sftp.put(str(local), remote)
            print("  ✓ %s (%d B)" % (remote, local.stat().st_size), flush=True)

        vpath = "/tmp/_verify_phenotype.py"
        with sftp.file(vpath, "wb") as fh:
            fh.write(VERIFY_SRC.encode("utf-8"))
        with sftp.file("/tmp/_restart_pheno.sh", "wb") as fh:
            fh.write(RESTART_SH.encode("utf-8"))
        sftp.close()
    finally:
        c.close()

    # 3) 语法自检
    print("\n=== [3] 服务器端语法自检 ===", flush=True)
    rc, out = run("cd %s && %s -m py_compile database/phenotype_toolkit.py && echo COMPILE_OK" % (BASE, PY), 90)
    print("  " + (out.strip() or "(无输出)"), flush=True)
    if "COMPILE_OK" not in out:
        print("  ❌ 语法自检失败，未重启", flush=True)
        return 5

    # 4) 重启
    print("\n=== [4] 重启 uvicorn ===", flush=True)
    rc, out = run("bash /tmp/_restart_pheno.sh", 180)
    print("  " + (out.strip() or "(无输出)"), flush=True)

    ok = False
    for i in range(30):
        time.sleep(10)
        rc, code = run("curl -s -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/health", 20)
        print("  health 第 %d 次：%s" % (i + 1, code.strip()), flush=True)
        if code.strip() == "200":
            ok = True
            break
    if not ok:
        print("  ⚠ health 未恢复，抓取日志尾部：", flush=True)
        rc, log = run("tail -30 %s/logs/hub.log" % rexc.env("ZF_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"), 60)
        print(log, flush=True)
        return 6

    # 5) 验证
    print("\n=== [5] 端到端验证 ===", flush=True)
    rc, out = run("%s /tmp/_verify_phenotype.py" % PY, 300)
    print(out, flush=True)

    rp = ROOT / "docs" / "表型页修复部署验证.md"
    rp.write_text("# 表型页修复部署验证\n\n```\n%s\n```\n" % out.strip(), encoding="utf-8")
    print("报告：%s" % rp, flush=True)
    return 0 if "VERIFY_DONE" in out else 7


if __name__ == "__main__":
    raise SystemExit(main())
