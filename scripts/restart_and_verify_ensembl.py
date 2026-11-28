# -*- coding: utf-8 -*-
"""重启线上服务并端到端验证 Ensembl 官方基因集。

为什么需要这一步：
  deploy_ensembl_official.py 只把数据和 .py 文件放到了服务器磁盘上，
  但 uvicorn 进程内存里还驻留着旧的 ensembl_version_map 模块。
  不重启就永远是旧的 chain 近似逻辑，官方表形同虚设。

流程：
  1. 语法自检 + 确认 ens_gene_official 表已在
  2. 按 three_hour_watchdog.py 里验证过的环境变量重启 uvicorn
  3. 轮询 /api/health 直到就绪
  4. 上传并执行服务器端验证脚本（查表 + 打 HTTP 转换接口）

用法（项目根目录）：
    python scripts/restart_and_verify_ensembl.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import remote_exec as re  # noqa: E402

BASE = re.env("ZF_BASE", "/data/zoujin/deng/zebrafish_gene_qa")
RUNTIME = re.env("ZF_RUNTIME", "/data/zoujin/deng/zebrafish_runtime")
DB = RUNTIME + "/idmap_data.sqlite3"
PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"
REMOTE_VERIFY = "/tmp/_verify_ens_online.py"

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

SERVER_VERIFY = r'''
import http.cookiejar, json, sqlite3, sys, time, urllib.request

DB = sys.argv[1]
BASE_URL = "http://127.0.0.1:8080"

print("=== [A] 落盘库里的官方基因集 ===")
cons = sqlite3.connect("file:%s?mode=ro" % DB, uri=True, timeout=5)
tot = 0
for rel, n in cons.execute("select release,count(*) from ens_gene_official group by release order by release"):
    print("   r%-3d %6d 行" % (rel, n)); tot += n
print("   合计 %d 行" % tot)
print("   r116 tp53 =", cons.execute(
    "select ensembl_gene_id,chrom,start,end,strand from ens_gene_official "
    "where release=116 and symbol='tp53' collate nocase").fetchall())
cons.close()

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
except Exception as exc:
    print("   (gate warn: %s)" % exc)

print()
print("=== [B] builds 端点 ===")
try:
    raw, _ = call("/api/tools/id-convert/builds")
    d = json.loads(raw)
    print("   builds=%d" % len(d.get("builds", [])))
    print("   ensembl_releases=%s" % [x["value"] for x in d.get("ensembl_releases", [])])
    print("   release_pair_count=%s" % d.get("release_pair_count"))
except Exception as exc:
    print("   ❌ %s" % exc)

CASES = [
    ("ens:54", "ens:116", ["tp53", "ENSDARG00000035559"]),
    ("ens:116", "ens:54", ["tp53"]),
    ("ens:116", "ens:80", ["tp53"]),
    ("ens:79", "ens:116", ["tp53"]),
    ("ens:54", "ens:116", ["zgc:158629"]),
]

print()
print("=== [C] Ensembl release 互转（真实数据验收）===")
for src, tgt, ids in CASES:
    try:
        raw, dt = call("/api/tools/id-convert/batch",
                       {"identifiers": ids, "source_build": src, "target_build": tgt})
        d = json.loads(raw)
    except Exception as exc:
        print("\n--- %s -> %s  ❌ %s" % (src, tgt, exc)); continue
    print("\n--- %s -> %s  (%.2fs) ---" % (src, tgt, dt))
    for r in d.get("results", []):
        can = r.get("canonical") or {}
        tc = r.get("target_coords") or {}
        if not isinstance(tc, dict):
            tc = {}
        coords = "-"
        if tc.get("start"):
            coords = "%s:%s-%s(%s)" % (tc.get("chrom"), tc.get("start"), tc.get("end"), tc.get("strand"))
        print("   %-22s status=%-10s id=%-22s %s" % (
            r.get("query"), r.get("status"), can.get("ensembl_id") or "-", coords))
        cand = r.get("candidates") or []
        if cand:
            print("      候选 %d 个: %s" % (len(cand), [c.get("ensembl_id") for c in cand][:6]))
    if d.get("results"):
        print("   [raw] " + json.dumps(d["results"][0], ensure_ascii=False)[:600])

print()
print("=== [D] 非 Ensembl 路径是否退化（应仍是毫秒级）===")
try:
    raw, dt = call("/api/tools/id-convert/batch",
                   {"identifiers": ["tp53"], "source_build": "GRCz11", "target_build": "Zv9"})
    print("   GRCz11->Zv9  %.2fs  %s" % (dt, raw[:200]))
except Exception as exc:
    print("   ❌ %s" % exc)

print()
print("VERIFY_DONE")
'''


def sh(cmd: str, timeout: int = 120):
    return re.run(cmd, timeout=timeout)


def main() -> int:
    print("=== [1] 服务器端语法与数据自检 ===", flush=True)
    rc, out = sh("cd %s && %s -m py_compile database/ensembl_version_map.py "
                 "&& echo COMPILE_OK" % (BASE, PY), timeout=120)
    print("   " + (out.strip() or "(无输出)"), flush=True)
    if "COMPILE_OK" not in out:
        print("   ❌ 语法自检未通过，未重启（线上仍是旧版本，但至少没被弄坏）")
        return 2

    rc, out = sh("%s -c \"import sqlite3;c=sqlite3.connect('file:%s?mode=ro',uri=True);"
                 "print(c.execute('select count(*) from ens_gene_official').fetchone()[0])\""
                 % (PY, DB), timeout=60)
    print("   ens_gene_official 总行数: " + out.strip(), flush=True)
    if not out.strip().isdigit() or int(out.strip()) < 100000:
        print("   ❌ 官方表不在或行数异常，先跑 deploy_ensembl_official.py")
        return 3

    print("\n=== [2] 重启 uvicorn（加载新模块）===", flush=True)
    rc, out = sh("ps -eo pid,args | grep -E 'uvicorn api[.]api_server:app' | grep -v grep | awk '{print $1}'",
                 timeout=30)
    print("   重启前 pid: " + (out.strip() or "(无)"), flush=True)
    rc, out = sh(RESTART_SH, timeout=180)
    print("   " + (out.strip() or "(无输出)"), flush=True)

    print("\n=== [3] 等待 health 就绪 ===", flush=True)
    ok = False
    for i in range(36):
        time.sleep(10)
        rc, out = sh("curl -s -m 8 http://127.0.0.1:8080/api/health", timeout=20)
        txt = out.strip()
        code = "?"
        rc2, c2 = sh("curl -s -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/health",
                     timeout=20)
        code = c2.strip()
        print("   第 %2d 次: code=%s %s" % (i + 1, code, txt[:120]), flush=True)
        if code == "200" and '"status"' in txt:
            ok = True
            break
    if not ok:
        print("   ⚠ 5 分钟内未就绪，抓取日志尾部：" , flush=True)
        rc, log = sh("tail -40 %s/logs/hub.log" % RUNTIME, timeout=60)
        print(log, flush=True)
        return 4

    print("\n=== [4] 端到端验证 ===", flush=True)
    c = re.connect()
    try:
        sftp = c.open_sftp()
        with sftp.file(REMOTE_VERIFY, "wb") as fh:
            fh.write(SERVER_VERIFY.encode("utf-8"))
        sftp.close()
    finally:
        c.close()

    rc, out = sh("%s %s %s" % (PY, REMOTE_VERIFY, DB), timeout=600)
    print(out, flush=True)

    print("=== 完成 ===", flush=True)
    return 0 if "VERIFY_DONE" in out else 5


if __name__ == "__main__":
    raise SystemExit(main())
