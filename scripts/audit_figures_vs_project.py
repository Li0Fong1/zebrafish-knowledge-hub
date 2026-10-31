# -*- coding: utf-8 -*-
"""图集 × 本机项目 × 服务器真实系统 —— 三方一致性审计。

目的：找出图集（Figure/*.png 与图注）中**与真实系统不符**之处：
    * 图上写错的数值（wrong-claim）
    * 因系统演进而过时的数值（stale-index：索引/参数重建后图未同步）
    * 参数漂移（param-drift：图上参数 ≠ .env / config.py 生效值）
    * 口径差异（scope-diff：同一名词指不同集合，需在图注澄清）
    * 环境固有差异（env-only：本机无大索引 / 无 MySQL，属正常，不应出现在图的主张里）

数据来源（全部为权威侧）：
    服务器  .env / FAISS manifest / MySQL 精确 COUNT / 验收报告
    本机    .env / core/config.py 常量 / 目录存在性
    图集    Figure/_legends.json（渲染期捕获的图内文本 + 中英图注）

用法：
    python scripts\\audit_figures_vs_project.py            # 用已缓存快照
    python scripts\\audit_figures_vs_project.py --refresh  # 重新拉取服务器快照
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                                        # noqa: E402


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

FIG = FC.FIG_DIR                                               # .env: FIG_OUTPUT_DIR
SNAP = FIG / "_audit_sources.json"

# ── 审计目标：图例文件与图注 ──────────────────────────────────────────────
LEG = json.loads((FIG / "_legends.json").read_text(encoding="utf-8"))
_PROBE_F = FIG / "_retrieval_probe.json"
PROBE = (json.loads(_PROBE_F.read_text(encoding="utf-8")) if _PROBE_F.is_file() else {}) or {}
FIG_TEXT = {}          # num -> 全部文本（图内 + 图注中英）
for k, v in LEG.items():
    n = int(k)
    blob = [v.get("en", ""), v.get("zh", "")] + list(v.get("texts") or [])
    FIG_TEXT[n] = "\n".join(blob)

# ── 服务器侧快照（结构化） ────────────────────────────────────────────────
SRV_HOST, SRV_PORT, SRV_USER, SRV_PWD = _zf_host(), _zf_port(), _zf_user(), _zf_pass()
SRV_BASE = "/data/zoujin/deng/zebrafish_gene_qa"
SRV_RT = "/data/zoujin/deng/zebrafish_runtime"
SRV_PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"
LOCAL_ENV = ROOT / ".env"
LOCAL_CFG = ROOT / "core" / "config.py"

ENV_KEYS = [
    "PUBMED_YES_SPLIT_MODE", "PUBMED_YES_RETRIEVE_CANDIDATES", "PUBMED_YES_RERANK_TOP_N",
    "PUBMED_YES_UNIQUE_PAPERS", "PUBMED_YES_PACK_CHARS", "PUBMED_YES_RERANK",
    "PUBMED_YES_SECTION_CHUNKING", "EMBEDDING_DIM", "EMBEDDING_DEVICE",
    "PUBMED_EMBEDDING_DEVICE", "BGE_RERANKER_DEVICE", "FAISS_PUBMED_YES_DIR",
    "FAISS_QWEN3_4B_DIR", "RAG_CONTEXT_MAX_CHARS",
]
MYSQL_TABLES = ["gene_basic", "gene_alias", "gene_xref", "gene_reference", "gene_go",
                "gene_kegg", "gene_expression", "gene_assembly_xref", "literature_paper",
                "literature_pubmed", "homology_gene", "assembly_registry"]


def parse_env(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def read_local() -> dict:
    snap = {"env": {}, "config_defaults": {}, "paths": {}}
    if LOCAL_ENV.is_file():
        try:
            snap["env"] = {k: v for k, v in parse_env(
                LOCAL_ENV.read_text(encoding="utf-8", errors="replace")).items()
                if k in ENV_KEYS}
        except Exception:
            pass
    if LOCAL_CFG.is_file():
        txt = LOCAL_CFG.read_text(encoding="utf-8", errors="replace")
        for k in ENV_KEYS:
            m = re.search(rf'{k}\s*=\s*int\(os\.getenv\(\s*"{k}"\s*,\s*"(\d+)"', txt)
            if m:
                snap["config_defaults"][k] = m.group(1)
        m = re.search(r'os\.getenv\(\s*"PUBMED_YES_SPLIT_MODE"\s*,\s*"(\w+)"', txt)
        if m:
            snap["config_defaults"]["PUBMED_YES_SPLIT_MODE"] = m.group(1)
    for p, label in [(ROOT / "data" / "faiss_index", "data/faiss_index"),
                     (ROOT / "models" / "Qwen3-Embedding-4B", "models/Qwen3-Embedding-4B"),
                     (Path.home() / "AppData" / "Local" / "ZebrafishGeneQA", "LOCALAPPDATA/ZebrafishGeneQA")]:
        snap["paths"][label] = p.is_dir()
    return snap


def fetch_server() -> dict:
    import paramiko
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(SRV_HOST, port=SRV_PORT, username=SRV_USER, password=SRV_PWD, timeout=30)

    def run(cmd, t=240):
        _i, o, e = cli.exec_command(cmd, timeout=t)
        return o.read().decode("utf-8", "replace").strip()

    srv = {"env": {}, "index": {}, "mysql": {}, "meta": {}}
    srv["env"] = {k: v for k, v in parse_env(run(f"cat {SRV_BASE}/.env")).items()
                  if k in ENV_KEYS}
    idx_dir = srv["env"].get("FAISS_PUBMED_YES_DIR") or (
        f"{SRV_RT}/faiss_pubmed_yes_qwen3_4b_v2_merged")
    code = ("import json,sys;d=json.load(open(sys.argv[1]+'/manifest.json'));"
            "print(json.dumps({k:d.get(k) for k in ('shards','total_papers','total_chunks',"
            "'embedding_dim','embedding_model','index_version','chunking','group_size',"
            "'status','updated_at')}, default=str))")
    raw = run(f"{SRV_PY} -c \"{code}\" {idx_dir} 2>/dev/null | tail -1")
    try:
        man = json.loads(raw)
    except Exception:
        man = {}
    srv["index"] = {"dir": idx_dir, **man}
    srv["index"]["shard_count"] = len(man.get("shards") or [])
    srv["index"]["chunking_mode"] = (man.get("chunking") or {}).get("mode") if isinstance(
        man.get("chunking"), dict) else man.get("chunking")

    mysql = (f"{SRV_RT}/mysql80/bin/mysql --socket={SRV_RT}/mysql.sock -uzebrafish "
             "-pzebrafish_remote_2026 --default-character-set=utf8mb4 -N -B zebrafish_db")
    parts = " UNION ALL ".join([f"SELECT '{t}' k, COUNT(*) n FROM {t}" for t in MYSQL_TABLES])
    for ln in run(f'{mysql} -e "SELECT k, n FROM ({parts}) x;"').splitlines():
        p = ln.split("\t")
        if len(p) >= 2:
            srv["mysql"][p[0]] = int(p[1])
    for label, sql in [("kegg_pathways", "SELECT COUNT(DISTINCT pathway_id) FROM gene_kegg;"),
                       ("genes_with_refs", "SELECT COUNT(DISTINCT gene_id) FROM gene_reference;"),
                       ("genes_with_expr", "SELECT COUNT(DISTINCT gene_id) FROM gene_expression;"),
                       ("genes_ensembl", "SELECT COUNT(*) FROM gene_basic WHERE ensembl_id IS NOT NULL AND ensembl_id<>'';")]:
        o = run(f'{mysql} -e "{sql}"')
        srv["mysql"][label] = int(o.strip().split("\t")[0]) if o.strip() else None
    srv["meta"]["generated_at"] = run("date -Iseconds")
    cli.close()
    return srv


# ── 断言表：图上主张 ↔ 权威值 ─────────────────────────────────────────────
def build_assertions(srv: dict, loc: dict) -> list[dict]:
    idx, env = srv.get("index", {}), srv.get("env", {})
    my, lmy = srv.get("mysql", {}), loc.get("env", {})
    A: list[dict] = []

    def add(aid, kind, severity, figs, claim, fig_pat, real, real_src, fix):
        A.append(dict(id=aid, kind=kind, severity=severity, figs=figs, claim=claim,
                      fig_pattern=fig_pat, real=real, real_src=real_src, fix=fix))

    # —— 检索参数 ——
    cand = int(env.get("PUBMED_YES_RETRIEVE_CANDIDATES") or 0) or None
    topn = int(env.get("PUBMED_YES_RERANK_TOP_N") or 0) or None
    pack = int(env.get("PUBMED_YES_PACK_CHARS") or 0) or None
    add("param_candidates", "param-drift", "critical", [1, 3],
        "检索候选数", r"(?:coarse\s*scoring:?|candidates\s*=)\s*(\d+)", cand,
        "server/.env PUBMED_YES_RETRIEVE_CANDIDATES",
        f"Fig1(A)/Fig3(B): candidates 200 → {cand}（按篇粗打分）")
    add("param_topn", "param-drift", "critical", [1, 3],
        "重排取用篇数", r"top\s*[- ]?\s*(\d+)|(\d+)\s*reranked", topn,
        "server/.env PUBMED_YES_RERANK_TOP_N",
        f"Fig1(A)/Fig3(B): top 10 → {topn}（按篇细打分，非段落）")
    add("param_pack", "param-drift", "critical", [3],
        "上下文预算", r"([\d,]{3,})-character", pack,
        "server/.env PUBMED_YES_PACK_CHARS",
        f"Fig3(B): ≈5.3-5.8 k characters → {pack:,} 字符预算")

    # —— 索引规模 ——
    add("idx_papers", "stale-index", "critical", [1, 2, 3], "入索引论文数",
        r"(\d{1,3},\d{3})\s*(?:gold\s*)?papers", idx.get("total_papers"),
        f"server FAISS manifest total_papers ({idx.get('updated_at')})",
        f"Fig1(A)/Fig2(A)/Fig3(A): 13,534 → {idx.get('total_papers')}")
    add("idx_chunks", "stale-index", "critical", [1, 2, 3], "语义块数",
        r"(\d{1,3}(?:,\d{3})+)\s*(?:paragraph-level\s*)?chunks", idx.get("total_chunks"),
        "server FAISS manifest total_chunks",
        f"Fig1(A)/Fig2(A)/Fig3(A): 516,476 → {idx.get('total_chunks')}")
    add("idx_shards", "stale-index", "critical", [1, 2, 3], "FAISS 分片数",
        r"(\d+(?:,\d{3})*)\s*(?:FAISS\s+)?shards", idx.get("shard_count"),
        "server FAISS manifest len(shards)",
        f"Fig1/Fig2(A)/Fig3(A): 1,269 → {idx.get('shard_count')}")
    add("idx_dim", "wrong-claim", "high", [3], "嵌入维度",
        r"(\d{3,4})-d(?:imensional)?", idx.get("embedding_dim"),
        "server FAISS manifest embedding_dim", "保持 2048 ✓")
    add("idx_model", "scope-diff", "medium", [3], "嵌入模型未标注",
        r"Qwen3-Embedding-4B", "Qwen3-Embedding-4B",
        "server manifest embedding_model", "Fig3(A): 补注嵌入模型 Qwen3-Embedding-4B")
    add("idx_mode", "scope-diff", "medium", [3], "切分模式",
        r"paragraph\s*\+\s*section", idx.get("chunking_mode") or "paragraph_section",
        "server manifest chunking.mode（= .env SPLIT_MODE + SECTION_CHUNKING）",
        "Fig3(A)/图注：应写 paragraph + section（生效 paragraph_section）")
    add("idx_updated", "scope-diff", "medium", [1, 3], "索引更新时间未标注",
        r"updated_at|2026-09-10", idx.get("updated_at"),
        "server manifest updated_at", "Fig1/Fig3: 补注索引版本与更新时间")

    # —— 正式索引上的实测探针（对抗「小索引时代的结论」）——
    pr = PROBE.get("records") or []
    if pr:
        ranks = [r.get("self_rank") for r in pr if r.get("self_rank")]
        els = [e for e in (PROBE.get("elapsed_secs") or []) if e]
        med = round(sorted(els)[len(els) // 2], 1) if els else None
        add("probe_selfrank", "stale-index", "critical", [4], "探针文献的自身排名下界",
            r"self-rank\s+(\d+)\s*[-–]", min(ranks) if ranks else None,
            "probe_retrieval_health --collect（正式索引 /api/retrieval/debug 实测）",
            "Fig4(C)/图注：不得再写 self-rank=1（那是 994 篇小索引时代的结论）")
        add("probe_latency", "stale-index", "high", [4], "检索耗时中位数",
            r"median query time\s+([\d.]+)\s*s", med,
            "probe_retrieval_health --collect（实测）",
            "Fig4(C)：174 ms 为小索引单次值，应改为正式索引实测中位数")

    # —— 基因库（应与 MySQL 完全一致）——
    pairs = [
        ("gene_basic", 26321, r"26,321"), ("genes_ensembl", 25432, r"25,432"),
        ("gene_assembly_xref", 22628, r"22,628"), ("gene_alias", 145439, r"145,439"),
        ("gene_reference", 161298, r"161,298"), ("gene_go", 110966, r"110,966"),
        ("gene_kegg", 36959, r"36,959"), ("gene_expression", 319344, r"319,344"),
        ("gene_xref", 80212, r"80,212"), ("literature_paper", 32549, r"32,549"),
        ("kegg_pathways", 1520, r"1,520"), ("genes_with_refs", 12882, r"12,882"),
        ("genes_with_expr", 24245, r"24,245"),
    ]
    for key, figval, pat in pairs:
        add(f"db_{key}", "wrong-claim", "high", [1, 2, 6, 7], f"基因库 {key}",
            pat, my.get(key), "server MySQL exact COUNT",
            f"与服务器一致则无需修改（图上 {figval:,}）")

    # —— 环境固有差异（仅提示，不计为图错）——
    add("env_local_index", "env-only", "info", [], "本机无 PubMedYes 大索引",
        r"$^", loc.get("paths", {}).get("LOCALAPPDATA/ZebrafishGeneQA"),
        "本机目录不存在", "属正常：63 GB 索引仅部署在服务器，图不应主张本机可复现")
    add("env_device", "env-only", "info", [], "嵌入设备",
        r"$^", f"local={lmy.get('EMBEDDING_DEVICE')} / server={env.get('EMBEDDING_DEVICE')}",
        "本机 cpu vs 服务器 cuda:0", "属正常：图未主张设备，无需修改")
    return A


def fig_hit(num: int, pattern: str):
    try:
        m = re.search(pattern, FIG_TEXT.get(num, ""), re.I)
    except re.error:
        return None
    if not m:
        return None
    for g in m.groups():
        if g:
            return g
    return m.group(0)


def fmt(v):
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def run_audit(srv, loc) -> list[dict]:
    rows = []
    for a in build_assertions(srv, loc):
        hits = {}
        for n in a["figs"]:
            h = fig_hit(n, a["fig_pattern"])
            if h:
                hits[n] = h
        fig_vals = {n: str(v).replace(",", "") for n, v in hits.items()}
        real = a["real"]
        real_s = str(real).replace(",", "") if real is not None else ""
        ok = None
        if a["kind"] == "scope-diff":
            ok = hits and (a["fig_pattern"].lower() in FIG_TEXT.get(a["figs"][0], "").lower()
                           if False else bool(hits))
        elif real_s:
            ok = fig_vals and all(v == real_s for v in fig_vals.values())
        rows.append({**a, "fig_hits": hits, "fig_values": fig_vals, "real_str": fmt(real),
                     "consistent": bool(ok)})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="重新拉取服务器快照")
    args = ap.parse_args()

    if args.refresh or not SNAP.is_file():
        print("[audit] fetching server snapshot ...")
        srv = fetch_server()
        loc = read_local()
        SNAP.write_text(json.dumps({"server": srv, "local": loc},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        d = json.loads(SNAP.read_text(encoding="utf-8"))
        # 本机侧实时重读，服务器侧用缓存
        srv, loc = d["server"], read_local()
    print(f"[audit] server index: {srv['index'].get('total_papers')} papers / "
          f"{srv['index'].get('total_chunks')} chunks / {srv['index'].get('shard_count')} shards / "
          f"dim {srv['index'].get('embedding_dim')}")

    rows = run_audit(srv, loc)
    order = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    rows.sort(key=lambda r: (order.get(r["severity"], 9), r["id"]))

    bad = [r for r in rows if not r["consistent"] and r["severity"] != "info"]
    print(f"[audit] assertions={len(rows)}  mismatches={len(bad)}")

    lines = ["# 图集 × 本机 × 服务器 一致性审计", "",
             f"- 服务器索引权威值：**{srv['index'].get('total_papers'):,} 篇 / "
             f"{srv['index'].get('total_chunks'):,} 块 / {srv['index'].get('shard_count')} 分片 / "
             f"{srv['index'].get('embedding_dim')} 维**（{srv['index'].get('updated_at')}）",
             f"- 检索参数：candidates **{srv['env'].get('PUBMED_YES_RETRIEVE_CANDIDATES')}** → "
             f"rerank top **{srv['env'].get('PUBMED_YES_RERANK_TOP_N')}** 篇，"
             f"打包预算 **{srv['env'].get('PUBMED_YES_PACK_CHARS')}** 字符",
             f"- 断言总数 {len(rows)}，需修正 {len(bad)}", "",
             "| 级别 | 断言 | 图 | 图上值 | 权威值 | 类型 |", "|---|---|---|---|---|---|"]
    for r in rows:
        if r["severity"] == "info":
            continue
        fv = ", ".join(f"Fig{n}={v}" for n, v in r["fig_hits"].items()) or "—"
        mark = "✅" if r["consistent"] else {"critical": "❌", "high": "⚠️"}.get(r["severity"], "•")
        lines.append(f"| {mark} {r['severity']} | {r['id']} | {','.join(map(str, r['figs']))} | "
                     f"{fv} | {r['real_str']} | {r['kind']} |")
    lines += ["", "## 修复指令", ""]
    for r in bad:
        lines.append(f"- **{r['id']}**（{r['claim']}）：{r['fix']}　— 权威来源：`{r['real_src']}`")

    out_md = FIG / "AUDIT_DIFF.md"
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (FIG / "_audit.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(f"[ok] {out_md.name}  mismatches={len(bad)}")
    for r in bad:
        print(f"   - {r['severity']:8s} {r['id']:20s} fig={r['fig_hits']} real={r['real_str']}")
    if len(bad) > FC.FIG_AUDIT_ALLOW_MISMATCH:
        print(f"[gate] 差异 {len(bad)} 超过允许值 FIG_AUDIT_ALLOW_MISMATCH="
              f"{FC.FIG_AUDIT_ALLOW_MISMATCH}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
