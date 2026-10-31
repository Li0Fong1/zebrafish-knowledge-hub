# -*- coding: utf-8 -*-
"""检索健康度实测探针 —— 在**正式索引**上重测「题名自命中排名」与「检索耗时」。

为什么需要它
    图集早期使用的检索指标（self-rank 全为 1、latency 174 ms、context 2,342 字符）
    实测自 994 篇的**小测试索引**，在 32,532 篇正式索引上不成立。
    该脚本经 `/api/retrieval/debug`（与 Hub 文献 Agent 同路径）实测权威指标，
    产出 `Figure/_retrieval_probe.json`，供 `make_figures.py` 与审计器统一取用。

用法
    python scripts\\probe_retrieval_health.py --launch    # 上传探针并在服务器后台启动
    python scripts\\probe_retrieval_health.py --collect   # 拉回结果并写入 Figure/_retrieval_probe.json
    python scripts\\probe_retrieval_health.py --status    # 只看进度
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                                        # noqa: E402

FIG = FC.FIG_DIR                                               # .env: FIG_OUTPUT_DIR
OUT = FIG / "_retrieval_probe.json"

HOST, PORT, USER, PWD = _zf_host(), _zf_port(), _zf_user(), _zf_pass()
RT = "/data/zoujin/deng/zebrafish_runtime"
PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"
REMOTE_SCRIPT = f"{RT}/_probe_health.py"
REMOTE_RESULT = f"{RT}/_probe_health_result.json"

PROBE_CODE = r'''
import json, time, urllib.request


# ---- 服务器凭据：一律从环境变量或仓库根 .env 读取，源码内不得出现明文 ----
def _zf_env(name: str, default: str = "") -> str:
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        envf = Path(__file__).resolve().parents[1] / ".env"
        for line in envf.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return default


def _zf_required(name: str) -> str:
    val = _zf_env(name)
    if not val:
        raise SystemExit(f"缺少 {name}：请在 .env 或环境变量中设置（源码内不留服务器标识与凭据）")
    return val


def _zf_host() -> str:
    return _zf_required("ZF_HOST")


def _zf_port() -> int:
    return int(_zf_required("ZF_PORT"))


def _zf_user() -> str:
    return _zf_required("ZF_USER")


def _zf_pass() -> str:
    pw = _zf_env("ZF_PASS")
    if not pw:
        raise SystemExit("缺少服务器密码：请在 .env 或环境变量中设置 ZF_PASS（源码内不得硬编码凭据）")
    return pw
BASE = "http://127.0.0.1:8080"
CASES = [
 ("1465413", "Major histocompatibility complex class II genes of zebrafish."),
 ("7597068", "Efficient induction of point mutations allowing recovery of specific locus mutations in zebrafish."),
 ("7994634", "Derivation and characterization of a zebrafish liver cell line."),
 ("8618902", "Genetic heterogeneity in isogenic homozygous clonal zebrafish."),
 ("8893009", "Insertional mutagenesis and rapid cloning of essential genes in zebrafish."),
]
RESULT = "__RESULT_PATH__"


def call(q, timeout=300):
    req = urllib.request.Request(
        BASE + "/api/retrieval/debug",
        data=json.dumps({"query": q, "candidate_preview": 3}).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode())
    return round(time.time() - t0, 2), body


res = []
for pmid, title in CASES:
    rec = {"pmid": pmid, "title": title}
    try:
        dt, b = call(title)
        ranked = b.get("ranked") or []
        ranks = [i for i, h in enumerate(ranked, 1) if str(h.get("pmid")) == pmid]
        pack = b.get("hub_pack") if isinstance(b.get("hub_pack"), dict) else {}
        rec.update({
            "elapsed_sec": dt,
            "self_rank": ranks[0] if ranks else None,
            "self_within_top": len(ranked),
            "rank1_pmid": (ranked[0].get("pmid") or ranked[0].get("doc_id")) if ranked else None,
            "rank1_title": (ranked[0].get("title_zh") or "")[:60] if ranked else "",
            "pack_chars": pack.get("chars") or pack.get("used_chars") or pack.get("max_chars"),
            "config": b.get("config"),
            "shards_loaded": b.get("shards_loaded"),
            "rerank_method": b.get("rerank_method"),
            "candidates_before_rerank": b.get("candidates_before_rerank"),
            "after_rerank_top_n": b.get("after_rerank_top_n"),
        })
    except Exception as e:
        rec["error"] = repr(e)[:200]
    res.append(rec)
    with open(RESULT, "w", encoding="utf-8") as f:
        json.dump({"records": res, "finished": len(res) == len(CASES),
                   "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, f,
                  ensure_ascii=False, indent=1)
print("PROBE_DONE", len(res))
'''


def _client():
    import paramiko
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, port=PORT, username=USER, password=PWD, timeout=30)
    return c


def _run(cli, cmd, t=60):
    _i, o, e = cli.exec_command(cmd, timeout=t)
    return o.read().decode("utf-8", "replace").strip()


def launch():
    cli = _client()
    sftp = cli.open_sftp()
    tmp = ROOT / "logs" / "_probe_health.py"
    tmp.write_text(PROBE_CODE.replace("__RESULT_PATH__", REMOTE_RESULT), encoding="utf-8")
    sftp.put(str(tmp), REMOTE_SCRIPT)
    sftp.close()
    _run(cli, f"pkill -9 -f _probe_health.py; rm -f {REMOTE_RESULT}; echo cleaned")
    _run(cli, f"cd {RT} && (nohup {PY} {REMOTE_SCRIPT} > {RT}/_probe_health.log 2>&1 "
              f"< /dev/null &) ; echo LAUNCHED")
    time.sleep(5)
    n = _run(cli, "pgrep -fc _probe_health.py")
    print(f"[probe] launched on server (running={n}); 5 probes x ~35 s")
    cli.close()


def _status(cli) -> dict:
    raw = _run(cli, f"cat {REMOTE_RESULT} 2>/dev/null")
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def status():
    cli = _client()
    d = _status(cli)
    n = _run(cli, "pgrep -fc _probe_health.py")
    recs = d.get("records") or []
    print(f"[probe] records={len(recs)}/5 finished={d.get('finished')} running={n}")
    for r in recs:
        print("   %s  self_rank=%-5s rank1=%-12s %s s" % (
            r.get("pmid"), r.get("self_rank"), r.get("rank1_pmid"), r.get("elapsed_sec")))
    cli.close()
    return d


def collect():
    cli = _client()
    d = _status(cli)
    recs = d.get("records") or []
    if late := [r for r in recs if r.get("self_rank") is None and not r.get("error")]:
        print(f"[probe] 警告：{len(late)} 条自身文献未进入 top-100")
    if not d.get("finished"):
        print(f"[probe] 未跑完（{len(recs)}/5），请稍后再 collect")
        cli.close()
        return 1
    snap = json.loads((FIG / "_audit_sources.json").read_text(encoding="utf-8"))
    idx = snap.get("server", {}).get("index", {})
    payload = {
        "generated_at": d.get("generated_at"),
        "index": {"papers": idx.get("total_papers"), "chunks": idx.get("total_chunks"),
                  "shards": idx.get("shard_count"), "version": idx.get("index_version"),
                  "updated_at": idx.get("updated_at")},
        "probe_endpoint": "/api/retrieval/debug (same path as the Hub literature agent)",
        "records": recs,
        "self_ranks": [r.get("self_rank") for r in recs],
        "elapsed_secs": [r.get("elapsed_sec") for r in recs if r.get("elapsed_sec")],
        "config": (recs[0].get("config") if recs else {}) or {},
        "all_within_top": all(r.get("self_rank") for r in recs),
        "best_rank": min([r["self_rank"] for r in recs if r.get("self_rank")] or [0]) or None,
        "worst_rank": max([r["self_rank"] for r in recs if r.get("self_rank")] or [0]) or None,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[probe] -> {OUT.name}")
    for r in recs:
        print("   %s  self_rank=%-5s rank1=%-12s %6.1fs  %s" % (
            r.get("pmid"), r.get("self_rank"), r.get("rank1_pmid"),
            r.get("elapsed_sec") or 0, r.get("error", "")))
    cli.close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--launch", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.launch:
        launch(); return 0
    if a.collect:
        return collect()
    status(); return 0


if __name__ == "__main__":
    raise SystemExit(main())
