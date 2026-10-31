# -*- coding: utf-8 -*-
"""采集 nanog 问题的对比证据（全部为真实产物，不人工改写）：

    1. 服务器 MySQL：nanog 基因的库内事实（基础注释 / GO / KEGG / 表达 / 文献）
    2. 服务器 Hub：POST http://127.0.0.1:8080/api/chat 的同题回答（生产链路）
    3. DeepSeek：deepseek-chat 与 deepseek-reasoner 的同题回答（真实 API 调用）

输出：
    Figure/_nanog_facts.json          库内事实（作为判定基准）
    logs/_nanog_hub.txt               Hub 回答（未改写）
    logs/_nanog_deepseek-chat.txt     通用模型回答（未改写）
    logs/_nanog_deepseek-reasoner.txt 通用模型回答（未改写）
    logs/_nanog_manifest.json         采集元信息（时间 / 耗时 / 模型 / 端点）

用法：python scripts\\collect_nanog_evidence.py [--no-hub] [--no-llm]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                                     # noqa: E402


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

FIG = FC.FIG_DIR
LOGS = ROOT / "logs"
QUESTION = "斑马鱼 nanog 基因的功能有哪些？"

SRV_HOST, SRV_PORT, SRV_USER, SRV_PWD = _zf_host(), _zf_port(), _zf_user(), _zf_pass()
SRV_RT = "/data/zoujin/deng/zebrafish_runtime"
SRV_PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"
HUB_URL = "http://127.0.0.1:8080/api/chat"
MYSQL = (f"{SRV_RT}/mysql80/bin/mysql --socket={SRV_RT}/mysql.sock -uzebrafish "
         f"-pzebrafish_remote_2026 --default-character-set=utf8mb4 -N -B zebrafish_db")


def _env(key: str, default: str = "") -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return default


def connect():
    import paramiko
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(SRV_HOST, port=SRV_PORT, username=SRV_USER, password=SRV_PWD, timeout=20)
    return cli


def run(cli, cmd: str, t: int = 60) -> str:
    _i, o, _e = cli.exec_command(cmd, timeout=t)
    return o.read().decode("utf-8", "replace").strip()


def run_py(cli, code: str, arg: str = "", t: int = 240) -> str:
    b64 = base64.b64encode(code.encode()).decode()
    return run(cli, f"echo {b64} | base64 -d | {SRV_PY} - {arg}", t=t)


# ── 1. 库内事实 ───────────────────────────────────────────────────────────
FACT_SQL = {
    "basic": ("SELECT gene_id,gene_symbol,gene_name,ensembl_id,zfin_id,chromosome,"
              "COALESCE(gene_function,''),COALESCE(interpro_domain,''),"
              "COALESCE(uniprot_id,''),COALESCE(expression_summary,'') "
              "FROM gene_basic WHERE gene_id={gid}"),
    "go": "SELECT go_category,go_term FROM gene_go WHERE gene_id={gid} LIMIT 40",
    "kegg": ("SELECT pathway_id,pathway_name,COALESCE(category,'') FROM gene_kegg "
             "WHERE gene_id={gid} LIMIT 20"),
    "expression": ("SELECT tissue,expression_stage,COALESCE(expression_level,'') "
                   "FROM gene_expression WHERE gene_id={gid} LIMIT 25"),
    "reference": ("SELECT pubmed_id,COALESCE(publish_year,''),COALESCE(journal,''),"
                  "LEFT(COALESCE(title,''),150) FROM gene_reference WHERE gene_id={gid} "
                  "ORDER BY publish_year DESC LIMIT 12"),
    "counts": ("SELECT (SELECT COUNT(*) FROM gene_go WHERE gene_id={gid}),"
               "(SELECT COUNT(*) FROM gene_kegg WHERE gene_id={gid}),"
               "(SELECT COUNT(*) FROM gene_expression WHERE gene_id={gid}),"
               "(SELECT COUNT(*) FROM gene_reference WHERE gene_id={gid})"),
}


def collect_facts(cli) -> dict:
    # 先定位 nanog 的 gene_id（符号或别名命中）
    gid_txt = run(cli, f"{MYSQL} -e \"SELECT gene_id FROM gene_alias WHERE LOWER(alias) "
                       f"LIKE '%nanog%' LIMIT 1\" 2>/dev/null")
    if not gid_txt.strip().isdigit():
        gid_txt = run(cli, f"{MYSQL} -e \"SELECT gene_id FROM gene_basic WHERE LOWER(gene_symbol) "
                           f"LIKE '%nanog%' OR LOWER(gene_name) LIKE '%nanog%' LIMIT 1\" 2>/dev/null")
    gid = int(gid_txt.strip() or 0)
    print(f"[facts] nanog gene_id = {gid}")
    facts: dict = {"gene_id": gid, "question": QUESTION}

    def q(key, sql, split_tab=True):
        raw = run(cli, f"{MYSQL} -e \"{sql.format(gid=gid)}\" 2>/dev/null", t=60)
        rows = [r.split("\t") for r in raw.splitlines() if r.strip()]
        facts[key] = rows if split_tab else raw
        return rows

    b = q("basic", FACT_SQL["basic"])
    if b:
        keys = ("gene_id", "gene_symbol", "gene_name", "ensembl_id", "zfin_id", "chromosome",
                "gene_function", "interpro_domain", "uniprot_id", "expression_summary")
        facts["basic"] = dict(zip(keys, (b[0] + [""] * len(keys))[:len(keys)]))
    q("go", FACT_SQL["go"])
    q("kegg", FACT_SQL["kegg"])
    q("expression", FACT_SQL["expression"])
    q("reference", FACT_SQL["reference"])
    cnt = q("counts", FACT_SQL["counts"])
    if cnt:
        facts["counts"] = dict(zip(("go", "kegg", "expression", "reference"), cnt[0]))
    # 别名
    facts["aliases"] = [r[0] for r in
                        [x.split("\t") for x in run(
                            cli, f"{MYSQL} -e \"SELECT alias FROM gene_alias WHERE "
                                 f"gene_id={gid} LIMIT 20\" 2>/dev/null").splitlines() if x.strip()]]
    (FIG / "_nanog_facts.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[facts] counts = {facts.get('counts')}")
    return facts


# ── 2. Hub 回答 ───────────────────────────────────────────────────────────
def collect_hub(cli) -> str:
    code = (
        "import json,sys,urllib.request\n"
        "body=json.dumps({'question':sys.argv[1]}).encode()\n"
        "req=urllib.request.Request(sys.argv[2],data=body,"
        "headers={'Content-Type':'application/json'})\n"
        "try:\n"
        "    with urllib.request.urlopen(req,timeout=400) as r:\n"
        "        d=json.loads(r.read().decode('utf-8','replace'))\n"
        "    print(json.dumps({'ok':True,'answer':d.get('answer') or ''},ensure_ascii=False))\n"
        "except Exception as e:\n"
        "    print(json.dumps({'ok':False,'err':repr(e)[:300]}))\n")
    out = run_py(cli, code, f"{json.dumps(QUESTION)} {HUB_URL}", t=460)
    try:
        d = json.loads(out.splitlines()[-1])
    except Exception:
        d = {"ok": False, "err": out[-200:]}
    if d.get("ok") and d.get("answer"):
        (LOGS / "_nanog_hub.txt").write_text(d["answer"], encoding="utf-8")
        print(f"[hub] ok, {len(d['answer'])} chars")
        return d["answer"]
    print(f"[hub] FAILED: {d.get('err')}")
    return ""


# ── 3. 通用模型回答 ───────────────────────────────────────────────────────
def ask_deepseek(model: str) -> str:
    key = _env("LLM_API_KEY")
    base = _env("LLM_API_BASE", "https://api.deepseek.com/v1").rstrip("/")
    if not key:
        print(f"[llm:{model}] no key")
        return ""
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": QUESTION}],
        "temperature": 0.3, "stream": False,
    }).encode()
    req = urllib.request.Request(f"{base}/chat/completions", data=payload,
                                headers={"Content-Type": "application/json",
                                         "Authorization": f"Bearer {key}"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        ans = (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if ans:
            (LOGS / f"_nanog_{model}.txt").write_text(ans, encoding="utf-8")
        print(f"[llm:{model}] {'ok' if ans else 'empty'} {len(ans)} chars "
              f"in {time.time() - t0:.1f}s")
        return ans
    except Exception as e:
        print(f"[llm:{model}] FAILED {repr(e)[:160]}")
        return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-hub", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--models", default="deepseek-chat,deepseek-reasoner")
    a = ap.parse_args()

    manifest: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                      "question": QUESTION, "steps": {}}
    cli = None
    try:
        cli = connect()
        print("[srv] connected")
        t0 = time.time()
        facts = collect_facts(cli)
        manifest["steps"]["facts"] = {"ok": bool(facts.get("gene_id")),
                                      "sec": round(time.time() - t0, 1)}
        if not a.no_hub:
            t0 = time.time()
            ans = collect_hub(cli)
            manifest["steps"]["hub"] = {"ok": bool(ans), "chars": len(ans),
                                        "sec": round(time.time() - t0, 1),
                                        "endpoint": HUB_URL}
    except Exception as e:
        print("[srv] error:", repr(e)[:200])
        manifest["steps"]["server_error"] = repr(e)[:200]
    finally:
        if cli:
            cli.close()

    if not a.no_llm:
        for m in [x.strip() for x in a.models.split(",") if x.strip()]:
            t0 = time.time()
            ans = ask_deepseek(m)
            manifest["steps"][f"llm:{m}"] = {"ok": bool(ans), "chars": len(ans),
                                             "sec": round(time.time() - t0, 1),
                                             "base": _env("LLM_API_BASE")}

    (LOGS / "_nanog_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[ok] manifest ->", LOGS / "_nanog_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
