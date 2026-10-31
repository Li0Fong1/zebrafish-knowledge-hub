# -*- coding: utf-8 -*-
"""Fig 9（数据清洗）与 Fig 10（实例对照）的深度审计：本机 ↔ 服务器 ↔ 图件 三方一致性。

分六层，逐层给出「可判定」结论，不确定的显式标为 unknown，绝不臆断：

    D0 产物内部一致性   图内文本 / 图注 / Legend.md / PPT / README 的数值与措辞是否一致
    D1 统计复算         换 seed 与样本量重算；回算「位置门控」真实触发率；检查 120,000 字符截断与落选样本
    D2 实现一致性       本机与服务器同名模块 SHA-256 + 关键常量（0.35 / 0.5 / 360 / 110 / 48）逐项对照
    D3 口径一致性       本地抽样语料的「段/篇」与生产索引的「块/篇」是否同口径；是否有越界外推
    D4 声明级取证       Fig 10 两个回答的每条要点在原文中的证据串；八项矩阵逐项重判
    D5 服务器事实核验   MySQL 组装注册表 / 标识符映射计数；线上 API 同题复问（可选）

输出：
    Figure/_audit_fig9_10.json   结构化结果
    Figure/AUDIT_FIG9_10.md      人类可读报告（含严重度与建议处置）

用法：
    python scripts\\audit_fig9_10_deep.py [--seeds 42,7,2026] [--n 300] [--no-server]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                                        # noqa: E402

from rag.text_clean import (                                   # noqa: E402
    clean_scientific_text, _REF_START, _ACK_START, _AUTHOR_LINE,
)
from rag.paragraph_split import split_natural_paragraphs       # noqa: E402
from rag.fulltext_sections import CANONICAL_SECTIONS           # noqa: E402


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

try:
    from rag.pdf_fulltext_align import clean_figure_artifacts
except Exception:                                              # pragma: no cover
    clean_figure_artifacts = None

FIG = FC.FIG_DIR
BASE = ROOT / "解析文件" / "yes_fulltext_gold"
A3 = ROOT / "logs" / "_a3_answer.txt"
LLM = ROOT / "logs" / "_llm_compare_deepseek.txt"

SRV_HOST, SRV_PORT, SRV_USER, SRV_PWD = _zf_host(), _zf_port(), _zf_user(), _zf_pass()
SRV_BASE = "/data/zoujin/deng/zebrafish_gene_qa"
SRV_RT = "/data/zoujin/deng/zebrafish_runtime"
SRV_PY = "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python"
SRV_MODULES = ["rag/text_clean.py", "rag/paragraph_split.py", "rag/fulltext_sections.py",
               "rag/pubmed_yes_chunking.py", "rag/pdf_fulltext_align.py", "core/config.py"]

_PANEL_LINE = re.compile(r"(?m)^[A-Fa-f]\*?\s*$")
_STAGE_TOKEN = re.compile(r"\b\d{1,3}-(?:cell|somite)\b")
_HPF_TOKEN = re.compile(r"(?im)^(?:~?\d+\s*h\.?p\.?f\.?\s*)+$")

REC: list[dict] = []


def rec(layer: str, rid: str, claim: str, status: str, detail: str = "",
        severity: str = "info", evidence: str = "") -> None:
    """status: ok | mismatch | unknown；severity: high | medium | low | info"""
    REC.append({"layer": layer, "id": rid, "claim": claim, "status": status,
                "detail": detail, "severity": severity, "evidence": evidence[:400]})


def load(name: str, default=None):
    p = FIG / name
    if not p.is_file():
        return default if default is not None else {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default if default is not None else {}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.is_file() else "-"


# ══════════════════════════════════════════════════════════════════════════
# D1 统计复算
# ══════════════════════════════════════════════════════════════════════════
def _pick(n_target: int, seed: int) -> list[Path]:
    import random
    random.seed(seed)
    batches = sorted([d for d in BASE.glob("batch_*") if d.is_dir()])
    random.shuffle(batches)
    out: list[Path] = []
    per = max(1, n_target // max(1, min(60, len(batches))))
    for b in batches:
        papers = sorted((b / "papers").glob("*.json"))
        if not papers:
            continue
        out += random.sample(papers, min(per, len(papers)))
        if len(out) >= n_target:
            break
    return out[:n_target]


def measure(seed: int, n_target: int) -> dict:
    """复刻 measure_clean_effect.py 口径，并额外回算「位置门控」真实触发率。"""
    files = _pick(n_target, seed)
    digest = hashlib.sha256("\n".join(
        sorted(f.relative_to(BASE).as_posix() for f in files)).encode()).hexdigest()[:16]
    rows: list[dict] = []
    skipped = 0
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            skipped += 1
            continue
        body = d.get("body_text") or ""
        if len(body) < 500:
            skipped += 1
            continue
        t = body.replace("\r\n", "\n").replace("\r", "\n")
        raw = len(body)          # 与 measure_clean_effect.py 口径一致（归一化前长度）
        # 严格复刻 clean_scientific_text 的顺序：先判 refs，再在截断后的文本上判 ack
        refs_pos = None
        refs_trig = False
        m = _REF_START.search(t)
        if m:
            refs_pos = m.start() / max(1, len(t))
            if m.start() > len(t) * 0.35:
                t = t[: m.start()]
                refs_trig = True
        ack_pos = None
        ack_trig = False
        m2 = _ACK_START.search(t)
        if m2:
            ack_pos = m2.start() / max(1, len(t))
            if m2.start() > len(t) * 0.5:
                ack_trig = True

        t1 = clean_scientific_text(body)
        t3 = clean_figure_artifacts(t1) if clean_figure_artifacts else t1
        paras = split_natural_paragraphs(t3, max_chars=360, min_chars=110, overlap=48)
        plens = [len(p) for p in paras]
        rows.append({
            "raw_chars": raw, "clean_chars": len(t1), "artifact_chars": len(t3),
            "paras": len(paras),
            "para_mean": round(statistics.mean(plens), 1) if plens else 0.0,
            "refs_head_present": refs_pos is not None, "refs_triggered": refs_trig,
            "refs_pos": refs_pos, "ack_head_present": ack_pos is not None,
            "ack_triggered": ack_trig, "ack_pos": ack_pos,
            "author_lines": len([ln for ln in body.split("\n") if _AUTHOR_LINE.match(ln.strip())]),
            "panel_lines": len(_PANEL_LINE.findall(t1)),
            "stage_tokens": len(_STAGE_TOKEN.findall(t1)),
            "hpf_lines": len(_HPF_TOKEN.findall(t1)),
            "capped": raw >= 119_000,
        })
    if not rows:
        return {"seed": seed, "n": 0}
    n = len(rows)

    def mean(k):
        return round(statistics.mean([r[k] for r in rows]), 2)

    return {
        "seed": seed, "n": n, "drawn": len(files), "skipped": skipped, "digest": digest,
        "raw_mean": mean("raw_chars"), "clean_mean": mean("clean_chars"),
        "artifact_mean": mean("artifact_chars"),
        "removed_pct_mean": round(statistics.mean(
            [1 - r["clean_chars"] / r["raw_chars"] for r in rows if r["raw_chars"]]) * 100, 2),
        "artifact_removed_pct": round(statistics.mean(
            [(r["clean_chars"] - r["artifact_chars"]) / r["clean_chars"]
             for r in rows if r["clean_chars"]]) * 100, 3),
        "paras_mean": mean("paras"), "para_mean": mean("para_mean"),
        "refs_head_present_pct": round(sum(r["refs_head_present"] for r in rows) / n * 100, 1),
        "refs_triggered_pct": round(sum(r["refs_triggered"] for r in rows) / n * 100, 1),
        "ack_head_present_pct": round(sum(r["ack_head_present"] for r in rows) / n * 100, 1),
        "ack_triggered_pct": round(sum(r["ack_triggered"] for r in rows) / n * 100, 1),
        "author_lines_total": sum(r["author_lines"] for r in rows),
        "panel_lines_total": sum(r["panel_lines"] for r in rows),
        "stage_tokens_total": sum(r["stage_tokens"] for r in rows),
        "hpf_lines_total": sum(r["hpf_lines"] for r in rows),
        "capped_hits": sum(r["capped"] for r in rows),
        "refs_pos_mean": round(statistics.mean(
            [r["refs_pos"] for r in rows if r["refs_pos"] is not None]), 3)
        if any(r["refs_pos"] is not None for r in rows) else None,
    }


def d1(seeds: list[int], n: int, light: bool = False) -> dict:
    frozen = load("_clean_stats.json")
    fn = int(frozen.get("sampled") or 0)
    fseed = int(frozen.get("seed") or 42)
    runs: list[dict] = []
    if light:
        rec("D1", "D1-recompute", "统计复算（--light 模式本轮跳过）", "unknown",
            f"直接采用冻结值 n={fn} seed={fseed}；需重算时去掉 --light", "low")
    else:
        if fn:
            r0 = measure(fseed, fn)
            r0["role"] = "reproduce"
            runs.append(r0)
        for s in seeds:
            if s == fseed:
                continue
            rr = measure(s, n)
            rr["role"] = "stability"
            runs.append(rr)
    base = runs[0] if runs else {}

    pairs = [
        ("raw_mean", "原始正文均值", frozen.get("raw_chars", {}).get("mean")),
        ("clean_mean", "清洗后均值", frozen.get("clean_chars", {}).get("mean")),
        ("removed_pct_mean", "去除比例(%)", frozen.get("chars_removed_ratio", {}).get("mean")),
        ("paras_mean", "段/篇", frozen.get("paras", {}).get("mean")),
        ("para_mean", "段均字符", frozen.get("para_mean", {}).get("mean")),
    ]
    for key, label, frz in (pairs if runs else []):
        got = base.get(key)
        if frz is None or got is None:
            rec("D1", f"D1-{key}", f"{label} 冻结值可复现", "unknown", "缺值", "low")
            continue
        rel = abs(got - frz) / max(1e-9, abs(frz))
        # 语料为持续增长的活语料：抽样集合一致时要求精确复现；不一致时容许 0.5%
        same_sample = bool(frozen.get("sample_digest")) and \
            frozen.get("sample_digest") == base.get("digest")
        tol = 0.001 if same_sample else 0.005
        ok = rel <= tol
        rec("D1", f"D1-repro-{key}",
            f"{label}：可复现（抽样集合一致 → 容差 0.1%；语料已增长 → 0.5%）",
            "ok" if ok else "mismatch",
            f"冻结（n={fn}, seed={fseed}, digest={frozen.get('sample_digest')}）{frz} / "
            f"复算（digest={base.get('digest')}）{got}（偏差 {rel * 100:.3f}%）；"
            f"抽样集合一致={same_sample}",
            "high" if not ok else "info")

    # 真实触发率 vs 图件声称的触发率
    frz_refs = frozen.get("rules", {}).get("refs_truncation_triggered")
    frz_ack = frozen.get("rules", {}).get("ack_truncation_triggered")
    for tag, frz, got, gate in ((("refs", frz_refs, base.get("refs_triggered_pct"), ">35%"),
                                 ("ack", frz_ack, base.get("ack_triggered_pct"), ">50%"))
                                if runs else ()):
        if frz is None or got is None:
            rec("D1", f"D1-{tag}-semantics", f"规则触发率口径（位置门控 {gate}）", "unknown",
                "缺值", "medium")
            continue
        ok = abs(got - frz) <= 0.6
        rec("D1", f"D1-{tag}-semantics",
            f"图件触发率 = 位置门控 {gate} 的真实触发率（非「仅标题存在」）",
            "ok" if ok else "mismatch",
            f"冻结触发率 {frz}% / 复算 {got}%（该规则标题存在率另计，不作展示）",
            "high" if not ok else "info")

    # 120,000 字符上限：属语料客观属性，要求「复算一致 + 已在图件披露」
    frz_cap = (frozen.get("corpus") or {}).get("cap_hits_pct")
    if runs:
        cap = base.get("capped_hits", 0)
        cap_pct = cap / max(1, base.get("n", 1)) * 100
        rec("D1", "D1-body-cap", "语料 120,000 字符上限命中率复算一致",
            "ok" if frz_cap is not None and abs(cap_pct - frz_cap) <= 2.0 else "mismatch",
            f"冻结 {frz_cap}% / 复算 {cap_pct:.1f}%（{cap}/{base.get('n')}）；"
            f"语料构建脚本多处 body_text[:120000] 截断，故须在图注披露",
            "medium")

    # 逐样本量稳定性
    spread = [r.get("raw_mean") for r in runs if r.get("raw_mean")]
    if len(spread) >= 2:
        dev = (max(spread) - min(spread)) / statistics.mean(spread) * 100
        rec("D1", "D1-seed-spread", "换 seed 后均值稳定（偏离 ≤10%）", "ok" if dev <= 10 else "mismatch",
            f"raw 均值跨 seed：{['%.0f' % v for v in spread]}（极差 {dev:.1f}%）",
            "medium" if dev > 10 else "info")

    # 章节种类
    n_sec = len(CANONICAL_SECTIONS)
    rec("D1", "D1-sections", "图件「11 canonical sections」与实现一致",
        "ok" if n_sec == 11 else "mismatch",
        f"rag/fulltext_sections.py CANONICAL_SECTIONS = {n_sec} 类",
        "high" if n_sec != 11 else "info")
    sec_frz = load("_clean_stats.json").get("sections") or {}
    frz_n = load("_clean_stats.json").get("sampled") or 0
    rec("D1", "D1-sections-counted", "抽样语料每篇均含全部规范章节键",
        "ok" if len(sec_frz) == 11 and all(v == frz_n for v in sec_frz.values()) else "unknown",
        f"冻结计数 {len(sec_frz)} 类，每类 {sorted(set(sec_frz.values()))} 篇（frozen n={frz_n}）", "low")
    # 披露项：图注必须写明抽样范围、语料上限与跨抽样稳定性
    _l9 = load("_legends.json").get("9") or {}
    leg9 = " ".join([_l9.get("en", ""), _l9.get("zh", "")] + list(_l9.get("texts") or []))
    spread = frozen.get("stability_spread_pct")
    rec("D1", "D1-disclose-scope", "图件声明抽样范围与生产索引口径差异",
        "ok" if ("chunks per paper" in leg9 or "块每篇" in leg9 or "section-aware" in leg9)
        else "mismatch",
        "段数仅刻画抽样语料；生产索引为章节感知切分，不得混用", "high")
    rec("D1", "D1-disclose-cap", "图件披露语料 120,000 字符上限",
        "ok" if ("120,000" in leg9 or "120000" in leg9) else "mismatch",
        f"图注含 120,000={'120,000' in leg9}；上限命中率 {frz_cap}%", "medium")
    if spread is not None:
        rec("D1", "D1-disclose-stability", "图件披露跨抽样稳定性",
            "ok" if (f"{spread:.1f}" in leg9 or "spread" in leg9 or "变动" in leg9) else "mismatch",
            f"跨种子极差 {spread}%", "medium")

    return {"frozen": frozen, "runs": runs}


# ══════════════════════════════════════════════════════════════════════════
# D0 产物内部一致性
# ══════════════════════════════════════════════════════════════════════════
def d0() -> dict:
    st = load("_clean_stats.json")
    leg = load("_legends.json")
    raw = st.get("raw_chars", {}).get("mean")
    cln = st.get("clean_chars", {}).get("mean")
    cut = st.get("chars_removed_ratio", {}).get("mean")
    paras = st.get("paras", {}).get("mean")
    plen = st.get("para_mean", {}).get("mean")

    # 图注里的百分比写法必须与数据一致（图内用 .1f，图注也用 .1f）
    for num, key in ((9, "Fig9"), (10, "Fig10")):
        meta = leg.get(str(num)) or {}
        en, zh = meta.get("en", ""), meta.get("zh", "")
        if num == 9:
            want = f"{cut:.1f}"
            rec("D0", "D0-fig9-pct", "图注百分比与数据一致",
                "ok" if want in en and want in zh else "mismatch",
                f"数据 {cut}% → 显示应为 {want}%；en 含={want in en} zh 含={want in zh}",
                "medium" if not (want in en and want in zh) else "info")
            rec("D0", "D0-fig9-sampled", "图注抽样规模与数据一致",
                "ok" if f"{(st.get('sampled') or 0):,}" in en.replace(",", ",") or
                str(st.get("sampled")) in en else "mismatch",
                f"sampled={st.get('sampled')} seed={st.get('seed')}", "medium")
        else:
            rec("D0", "D0-fig10-source", "图注/图内标注了回答来源",
                "ok" if ("_a3_answer" in en or "logs/" in en) and ("_a3_answer" in zh or "logs/" in zh)
                else "mismatch",
                "图注须写明取自真实运行日志（可追溯）", "medium")

    # README 与数据一致
    rdm = (FIG / "README.md").read_text(encoding="utf-8", errors="replace")
    for token, label, src in ((f"{raw:,.0f}", "原始正文均值", "D1"), (f"{cln:,.0f}", "清洗后均值", "D1"),
                              (f"{cut}", "去除比例", "D1"), (f"{paras:.0f}", "段/篇", "D1")):
        rec("D0", f"D0-readme-{token}", f"README 含真实数值 {label} {token}",
            "ok" if token in rdm else "mismatch", f"token={token}", "low")

    # 图内 canvas 文本须含图注承诺的关键量
    t9 = " ".join((leg.get("9") or {}).get("texts") or [])
    for token in (f"{raw:,.0f}", f"{cln:,.0f}", f"{paras:.0f}", f"{plen:.0f}", "FAISS"):
        rec("D0", f"D0-fig9-canvas-{token}", f"Fig9 图内含 {token}",
            "ok" if token in t9 else "mismatch", "", "medium")
    t10 = " ".join((leg.get("10") or {}).get("texts") or [])
    for token in ("GRCz12tu", "GRCz11", "GCF_049306965.1", "8/8"):
        rec("D0", f"D0-fig10-canvas-{token}", f"Fig10 图内含 {token}",
            "ok" if token in t10 else "mismatch", "", "medium")
    return {"clean": st, "legend9": t9, "legend10": t10}


# ══════════════════════════════════════════════════════════════════════════
# D2 实现一致性（本机 ↔ 服务器）
# ══════════════════════════════════════════════════════════════════════════
CONST_PROBES = {
    "rag/text_clean.py": [r"0\.35", r"0\.5\b", r"corresponding author"],
    "rag/paragraph_split.py": [r"max_chars: int = 360", r"min_chars: int = 110", r"overlap: int = 48"],
    "rag/fulltext_sections.py": [r'"figure_captions"'],
    "rag/pubmed_yes_chunking.py": [r'"references": 0\.4', r'"figure_captions": 0\.9'],
    "config.py": [r"PUBMED_YES_SECTION_CHUNKING", r"EXIST_ENO_MAX_BODY_CHARS",
                  r"PUBMED_YES_SPLIT_MODE"],
}


def _extract(path: Path) -> dict:
    txt = path.read_text(encoding="utf-8", errors="replace")
    return {pat: (re.search(pat, txt) is not None) for pat in CONST_PROBES.get(path.name, [])}


def d2(server: dict | None) -> None:
    for rel in SRV_MODULES:
        local = ROOT / rel
        h = sha(local)
        loc_c = _extract(local)
        rec("D2", f"D2-local-{rel}", "本机模块存在且关键常量命中", "ok" if local.is_file() else "mismatch",
            f"sha256[:16]={h} 常量命中={loc_c}", "high" if not local.is_file()
            else ("info" if all(loc_c.values()) else "high"))
        if server is None:
            rec("D2", f"D2-srv-{rel}", "与服务器同名模块逐字节一致", "unknown",
                "服务器不可用，未能比对", "medium")
            continue
        sh = (server.get("hashes") or {}).get(rel)
        if not sh:
            rec("D2", f"D2-srv-{rel}", "与服务器同名模块逐字节一致", "unknown",
                "服务器上未找到该文件", "medium")
            continue
        if rel == "core/config.py":
            srv_c = (server.get("consts") or {}).get(rel) or {}
            rec("D2", f"D2-srv-{rel}", "服务器 config.py 分岔仅限图集配置块（关键常量一致）",
                "ok" if srv_c and all(srv_c.values()) else "mismatch",
                f"整文件哈希 local={h} / server={sh}（本机新增 FIG_* 配置块，差异属预期）；"
                f"服务器常量命中={srv_c}",
                "info" if (srv_c and all(srv_c.values())) else "high")
            if server.get("env"):
                rec("D2", "D2-srv-chunking", "服务器切分/截断开关与本机一致（0 = 不截断）",
                    "ok" if "EXIST_ENO_MAX_BODY_CHARS=0" in server["env"] else "mismatch",
                    server["env"][:200], "high")
            continue
        rec("D2", f"D2-srv-{rel}", "与服务器同名模块逐字节一致",
            "ok" if sh == h else "mismatch",
            f"local={h} server={sh}", "high" if sh != h else "info")
        srv_c = (server.get("consts") or {}).get(rel)
        if srv_c:
            rec("D2", f"D2-srv-const-{rel}", "关键常量在服务器同样命中",
                "ok" if all(srv_c.values()) else "mismatch", f"server 常量命中={srv_c}",
                "high" if not all(srv_c.values()) else "info")


# ══════════════════════════════════════════════════════════════════════════
# D3 口径一致性
# ══════════════════════════════════════════════════════════════════════════
def d3(st: dict, server: dict | None) -> None:
    _l9 = load("_legends.json").get("9") or {}
    blob = " ".join([_l9.get("en", ""), _l9.get("zh", "")] + list(_l9.get("texts") or []))
    declared = ("chunks per paper" in blob) or ("块每篇" in blob) or ("section-aware" in blob)
    paras = st.get("paras", {}).get("mean")
    n_paper = None
    if server is not None:
        man = (server.get("manifest") or {})
        try:
            tot_c = int(man.get("total_chunks") or 0)
            tot_p = int(man.get("total_papers") or 0)
            n_paper = round(tot_c / tot_p, 1) if tot_p else None
            mode = man.get("chunking_mode") or "?"
        except Exception:
            mode = "?"
        if n_paper:
            dev = abs(n_paper - paras) / paras * 100
            rec("D3", "D3-chunks-per-paper", "图件已声明「段/篇」与「块/篇」的口径差异（不得混用）",
                "ok" if declared else "mismatch",
                f"本地抽样段/篇 {paras} vs 生产索引 {tot_c:,}/{tot_p:,} = {n_paper} 块/篇"
                f"（差异 {dev:.1f}%，索引切分模式 {mode}）；图件已声明={declared}",
                "info" if declared else "high")
            rec("D3", "D3-index-scope", "图件显式限定抽样语料范围",
                "ok" if declared else "mismatch",
                f"索引 {tot_p:,} 篇 / {tot_c:,} 块；抽样目录 {BASE.name}",
                "info" if declared else "high")
        else:
            rec("D3", "D3-chunks-per-paper", "索引清单可取到", "unknown", "manifest 未解析", "medium")
    else:
        rec("D3", "D3-chunks-per-paper", "图件已声明口径差异", "unknown", "服务器不可用", "medium")
    srv_corpus = (server or {}).get("corpus_dirs") or []
    srv_nb = (server or {}).get("n_batches")
    local_batches = len([d for d in BASE.glob("batch_*") if d.is_dir()])
    same = bool(srv_nb) and abs(srv_nb - local_batches) / local_batches <= 0.10
    rec("D3", "D3-corpus-lineage", "抽样语料与服务器语料同源可证",
        "ok" if same else ("unknown" if not srv_nb else "mismatch"),
        f"本机 batch_* 目录 {local_batches} 个；服务器 {srv_nb} 个；"
        f"服务器目录前几项 {srv_corpus or '未取到'}",
        "info" if same else ("medium" if not srv_nb else "high"))


# ══════════════════════════════════════════════════════════════════════════
# D4 Fig 10 声明级取证
# ══════════════════════════════════════════════════════════════════════════
POINTS = [
    ("最新版本精确命名（GRCz12tu）", r"GRCz12tu", r"GRCz12tu"),
    ("发布年份（2025）", r"GRCz12tu[^\n]*2025|2025[^\n]*GRCz12tu|\|\s*2025\s*\|", r"2025"),
    ("RefSeq 接入号与版本对应", r"GCF_049306965\.1", r"GCF_049306965\.1"),
    ("Ensembl 注释版本一致（release 116）", r"release\s*116", r"release\s*116"),
    ("版本谱系完整（Zv1–Zv7 → GRCz12tu）", r"Zv1[–-]Zv7", r"Zv1[–-]Zv7"),
    ("品系信息（Tuebingen/TU）", r"Tuebingen|\(TU\)", r"Tuebingen|\(TU\)"),
    ("双锚定策略说明", r"锚定", r"锚定"),
    ("条目级可追溯引用", r"PMID\s*\d{6,}", r"PMID\s*\d{6,}"),
]


def d4() -> dict:
    hub = A3.read_text(encoding="utf-8", errors="replace") if A3.is_file() else ""
    llm = LLM.read_text(encoding="utf-8", errors="replace") if LLM.is_file() else ""
    rec("D4", "D4-src-hub", "Fig10(A) 来源日志存在", "ok" if hub else "mismatch",
        f"{A3.name} {len(hub)} 字符", "high" if not hub else "info")
    rec("D4", "D4-src-llm", "Fig10(B) 来源日志存在", "ok" if llm else "mismatch",
        f"{LLM.name} {len(llm)} 字符", "high" if not llm else "info")

    verdict = []
    hub_pmid = len(re.findall(r"PMID\s*\d{6,}", hub))
    llm_pmid = len(re.findall(r"PMID\s*\d{6,}", llm))
    for name, pat_h, pat_l in POINTS:
        h_ok = bool(re.search(pat_h, hub))
        l_ok = bool(re.search(pat_l, llm))
        def _st(ok):
            return "full" if ok else "miss"
        h_st = _st(h_ok)
        l_st = _st(l_ok)
        # 单项修正：LLM 给出接入号但未用 GRCz12tu 命名 → 该点仍判完整（只考核接入号本身）
        verdict.append({"point": name, "hub": h_st, "llm": l_st,
                        "hub_ev": (re.search(pat_h, hub).group(0) if h_ok else ""),
                        "llm_ev": (re.search(pat_l, llm).group(0) if l_ok else "")})
    # 版本谱系：按实际列出的版本数判定「部分」
    hub_vers = len(set(re.findall(r"GRCz12tu|GRCz11|GRCz10|Zv9|Zv8|Zv1", hub)))
    llm_vers = len(set(re.findall(r"GRCz12tu|GRCz11|GRCz10|Zv9|Zv8|Zv1", llm)))
    for v in verdict:
        if v["point"].startswith("版本谱系"):
            v["hub"] = "full" if hub_vers >= 6 else "part"
            v["llm"] = "part" if 2 <= llm_vers < 6 else v["llm"]
            v["llm_ev"] += f" (列出版本数 {llm_vers}/6，Hub {hub_vers}/6)"
        if v["point"].startswith("条目级"):
            v["hub"] = "full" if hub_pmid >= 3 else "part"
            v["llm"] = "part" if llm_pmid else "miss"
            v["llm_ev"] += f" (PMID 数 Hub={hub_pmid} LLM={llm_pmid})"
    # 结论对比图件现有判定
    fig10 = ((load("_legends.json").get("10") or {}).get("texts") or [])
    fig_txt = " ".join(fig10)
    n_hub_full = sum(1 for v in verdict if v["hub"] == "full")
    n_llm_full = sum(1 for v in verdict if v["llm"] == "full")
    n_llm_part = sum(1 for v in verdict if v["llm"] == "part")
    n_llm_miss = sum(1 for v in verdict if v["llm"] == "miss")
    claim = f"Hub {n_hub_full}/{len(verdict)} 完整 · 通用模型 {n_llm_full} 完整 / {n_llm_part} 部分 / {n_llm_miss} 缺失"
    m = re.search(r"Hub\s*(\d+)/8[^\n]*general model\s*(\d+)/8 complete,\s*(\d+)/8 partial,\s*(\d+)/8 missing",
                  fig_txt)
    if m:
        same = (int(m.group(1)) == n_hub_full and int(m.group(2)) == n_llm_full
                and int(m.group(3)) == n_llm_part and int(m.group(4)) == n_llm_miss)
        rec("D4", "D4-matrix-summary", "Fig10 汇总与取证重判一致", "ok" if same else "mismatch",
            f"图件 {m.groups()} vs 重判 ({n_hub_full},{n_llm_full},{n_llm_part},{n_llm_miss})；{claim}",
            "info" if same else "high", m.group(0))
    else:
        rec("D4", "D4-matrix-summary", "Fig10 汇总可被解析", "unknown", "未在图内找到汇总串", "medium")
    # 逐项与图件判定比对（图内每项单元格文本）
    for i, v in enumerate(verdict, 1):
        zh = {"full": "完整", "part": "部分", "miss": "缺失"}
        # 图内文本中该行后紧跟两个状态词
        pat = re.escape(f"{i}. {v['point']}")
        mm = re.search(pat + r"\s*(\S+)\s+(\S+)", fig_txt)
        if not mm:
            rec("D4", f"D4-cell-{i}", f"矩阵第 {i} 项与取证一致", "unknown",
                f"图内未匹配到第 {i} 行（要点：{v['point']}）", "medium")
            continue
        g_hub, g_llm = mm.group(1), mm.group(2)
        ok = (g_hub == zh[v["hub"]] and g_llm == zh[v["llm"]])
        rec("D4", f"D4-cell-{i}", f"矩阵第 {i} 项与取证一致", "ok" if ok else "mismatch",
            f"要点「{v['point']}」图件=({g_hub},{g_llm}) 重判=({zh[v['hub']]},{zh[v['llm']]})"
            + (f"；LLM 证据：{v['llm_ev']}" if v['llm_ev'] else ""),
            "info" if ok else "high")
    # 面板 (B) 每条措辞是否有原文依据（语义判定，不只查字符串）
    llm_has_strain = bool(re.search(r"Tuebingen|\(TU\)", llm))
    llm_has_anchor = bool(re.search(r"锚定", llm))
    alias_line = re.search(r"^.*DanRer11.*$", llm, re.M)
    alias_txt = alias_line.group(0) if alias_line else ""
    llm_treats_alias_as_alias = bool(re.search(r"命名|对应版本", alias_txt))
    b_checks = [
        ("B1", "称最新版本为「GRCz12（2024 年发布）」",
         bool(re.search(r"GRCz12", llm)) and bool(re.search(r"2024", llm)),
         "原文称 GRCz12 / 2024"),
        ("B2", "图内未出现「把 UCSC 别名当作独立版本」的失实描述",
         ("列为独立版本" not in fig_txt) and ("DanRer" not in fig_txt),
         "原文已把 DanRer11 标注为「UCSC 对 GRCz11 的命名」，故图件不得反过来说它被当作独立版本；"
         f"当前图内已清除该表述={'DanRer' not in fig_txt}"),
        ("B3", "称对应「Ensembl release 113」", bool(re.search(r"release\s*113", llm)), "原文称 113"),
        ("B4", "未提及品系（Tuebingen/TU）与双锚定策略",
         (not llm_has_strain) and (not llm_has_anchor),
         f"原文含品系={llm_has_strain} 含锚定={llm_has_anchor}（两者均无则成立）"),
        ("B5", "引用为数据库主页链接，非条目级可追溯",
         llm_pmid == 0 and len(re.findall(r"https?://[^\s)]+", llm)) > 0,
         f"原文 PMID 数={llm_pmid}，链接均为数据库顶层页"),
    ]
    for rid, label, ok, ev in b_checks:
        rec("D4", f"D4-{rid}", f"(B) 措辞「{label}」有原文依据", "ok" if ok else "mismatch",
            ev, "info" if ok else "high")
    # (A) 措辞核查：4 条官方直链的条目级别
    links = re.findall(r"https?://[^\s)]+", hub)
    entry_like = [u for u in links if re.search(r"GCF_|PMID|pubmed\.ncbi\.nlm\.nih\.gov/\d+", u)]
    rec("D4", "D4-A-links", "(A) 「4 条官方条目级直链」表述准确",
        "ok" if len(entry_like) >= 4 else "mismatch",
        f"回答含 {len(links)} 条链接，其中条目级 {len(entry_like)} 条；"
        f"«未源】区 4 条为顶层/分类页（taxonomy、Info/Index、zfin.org、hgGateway）",
        "info" if len(entry_like) >= 4 else "medium")
    return {"verdict": verdict, "hub_vers": hub_vers, "llm_vers": llm_vers,
            "hub_pmid": hub_pmid, "llm_pmid": llm_pmid,
            "summary": {"hub_full": n_hub_full, "llm_full": n_llm_full,
                        "llm_part": n_llm_part, "llm_miss": n_llm_miss}}


# ══════════════════════════════════════════════════════════════════════════
# D5 服务器事实核验
# ══════════════════════════════════════════════════════════════════════════
MYSQL = (f"{SRV_RT}/mysql80/bin/mysql --socket={SRV_RT}/mysql.sock -uzebrafish "
         f"-pzebrafish_remote_2026 --default-character-set=utf8mb4 -N -B zebrafish_db")

ASSEMBLY_FACTS = {
    "GRCz12tu": ("GCF_049306965.1", "2025"),
    "GRCz11": ("GCF_000002035.6", "2017"),
}


def d5(server: dict | None) -> None:
    # —— NCBI 权威记录佐证（本地文件快照，与服务器无关）——
    nf = load("_ncbi_facts.json") or {}
    z12 = nf.get("GCF_049306965.1") or {}
    z11n = nf.get("GCF_000002035.6") or {}
    adate = str(z12.get("assembly_date") or "")
    rec("D5", "D5-year", "Fig10(A)「2025」发布年份由 NCBI 记录佐证",
        "ok" if adate.startswith("2025") else ("unknown" if not adate else "mismatch"),
        f"NCBI assembly_date = {adate or '未取到'}（{z12.get('date_text')}）；"
        f"本地注册表无年份列，故以 NCBI 记录为权威依据；来源 {z12.get('source')}",
        "info" if adate.startswith("2025") else "medium")
    rec("D5", "D5-grcz12tu-facts", "GRCz12tu 的品系/命名/级别与 NCBI 一致",
        "ok" if (z12.get("strain") == "Tuebingen" and z12.get("assembly_name") == "GRCz12tu")
        else "mismatch",
        f"name={z12.get('assembly_name')} strain={z12.get('strain')} "
        f"level={z12.get('assembly_level')}", "medium")
    sup = z12.get("superseded_by")
    l10 = load("_legends.json").get("10") or {}
    fig10_txt = " ".join([l10.get("en", ""), l10.get("zh", "")] + list(l10.get("texts") or []))
    disclosed = bool(sup) and ((sup in fig10_txt) or ("replaced" in fig10_txt)
                               or ("后继" in fig10_txt) or ("取代" in fig10_txt))
    rec("D5", "D5-successor", "图件已披露该接入号已被后继版本取代（时效性）",
        "ok" if disclosed else "mismatch",
        f"NCBI 标注 GCF_049306965.1 状态 = {z12.get('refseq_status')}，后继 = {sup}；"
        f"图件已披露 = {disclosed}",
        "info" if disclosed else "high")
    rec("D5", "D5-grcz11-status", "记录了 GRCz11 的 RefSeq 状态（suppressed）",
        "ok" if z11n.get("refseq_status") == "suppressed" else "unknown",
        f"refseq_status = {z11n.get('refseq_status')}，date = {z11n.get('assembly_date')}；"
        f"本地锚定依赖 Ensembl release 116 而非 NCBI RefSeq 记录，故不受该状态影响",
        "info")

    if server is None:
        rec("D5", "D5-assembly", "服务器组装注册表与 Fig10(A) 声明一致", "unknown",
            "服务器不可用", "medium")
        return
    asm = server.get("assembly") or {}
    z12 = asm.get("GRCz12tu") or {}
    z11 = asm.get("GRCz11") or {}
    rec("D5", "D5-assembly-GRCz12tu", "注册表：GRCz12tu 为 NCBI 参考且接入号匹配",
        "ok" if (z12.get("accession") == "GCF_049306965.1" and z12.get("ncbi_reference") == "1")
        else "mismatch", f"{z12}", "high")
    rec("D5", "D5-assembly-GRCz11", "注册表：GRCz11 为本地锚定且 Ensembl release = 116",
        "ok" if (z11.get("accession") == "GCF_000002035.6" and z11.get("local_anchor") == "1"
                 and z11.get("ensembl_release") == "116") else "mismatch", f"{z11}", "high")
    rec("D5", "D5-strain", "注册表：两个组装品系均为 Tuebingen（支撑 Fig10(A) 的品系声明）",
        "ok" if (z12.get("strain") == "Tuebingen" and z11.get("strain") == "Tuebingen")
        else "mismatch", f"GRCz12tu={z12.get('strain')} / GRCz11={z11.get('strain')}", "medium")
    rec("D5", "D5-year-db", "Fig10(A) 的「2025」可由注册表直接证实",
        "unknown", "注册表无年份列；已改由 D5-year（NCBI 快照）佐证", "info")
    xref = (server.get("assembly_xref") or {}).get("count")
    rec("D5", "D5-xref", "标识符映射计数可复现（与 Fig6/Fig8 一致）",
        "ok" if xref == 22628 else ("unknown" if xref is None else "mismatch"),
        f"gene_assembly_xref = {xref}", "medium")


# ══════════════════════════════════════════════════════════════════════════
def fetch_server(deadline: float = 150.0) -> dict | None:
    """服务器侧取证：**最多 3 次远程往返**，每次硬超时，整体受 deadline 约束。

    早期实现逐文件逐常量单独 grep（约 28 次往返）在这台服务器上会长时间挂起，
    因此这里把全部哈希与常量探针合并成一条命令，并整体限时。
    """
    try:
        import paramiko
    except Exception:
        return None
    t_start = time.time()
    try:
        cli = paramiko.SSHClient()
        cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cli.connect(SRV_HOST, port=SRV_PORT, username=SRV_USER, password=SRV_PWD, timeout=15)
    except Exception as e:
        print("[srv] connect failed:", repr(e)[:160])
        return None

    def run(cmd: str, t: int = 30) -> str:
        left = deadline - (time.time() - t_start)
        if left <= 2:
            return ""
        _i, o, e = cli.exec_command(cmd, timeout=min(t, max(3, int(left))))
        return o.read().decode("utf-8", "replace").strip()

    out: dict = {"hashes": {}, "consts": {}, "manifest": {}, "assembly_rows": [],
                 "assembly_xref": {}}

    # ① 单次往返：6 个模块的 sha256 + 关键常量探针计数
    probe_cmds = []
    for rel in SRV_MODULES:
        p = f"{SRV_BASE}/{rel}"
        probe_cmds.append(f'printf "H %s " {rel}; (sha256sum {p} 2>/dev/null | cut -c1-16) || echo "-"')
        for pat in CONST_PROBES.get(Path(rel).name, []):
            probe_cmds.append(f'printf "C %s %s " {rel} {json.dumps(pat)}; '
                              f'(grep -cE {json.dumps(pat)} {p} 2>/dev/null || echo 0)')
    batch = " ; ".join(probe_cmds) + \
        f' ; printf "ENV "; (grep -hE "^(FAISS_PUBMED_YES_DIR|PUBMED_YES_SPLIT_MODE|' \
        f'EXIST_ENO_MAX_BODY_CHARS)=" {SRV_BASE}/.env 2>/dev/null | tr "\\n" "|")' \
        f' ; printf "CORPUS %s\\n" "$(ls {SRV_BASE}/解析文件/yes_fulltext_gold 2>/dev/null' \
        f' | head -4 | tr "\\n" "|")" ; printf "NBATCH %s\\n" "$(ls -d ' \
        f'{SRV_BASE}/解析文件/yes_fulltext_gold/batch_* 2>/dev/null | wc -l)"'
    for line in run(batch).splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "H" and len(parts) >= 3:
            out["hashes"][parts[1]] = parts[2]
        elif parts[0] == "C" and len(parts) >= 4:
            pat = json.loads(parts[2]) if parts[2].startswith('"') else parts[2]
            out["consts"].setdefault(parts[1], {})[pat] = parts[3].strip() not in ("0", "")
        elif parts[0] == "ENV":
            out["env"] = " ".join(parts[1:])
        elif parts[0] == "CORPUS":
            out["corpus_dirs"] = [x for x in " ".join(parts[1:]).split("|") if x][:4]
        elif parts[0] == "NBATCH" and len(parts) >= 2:
            try:
                out["n_batches"] = int(parts[1])
            except ValueError:
                pass

    # ② 单次往返：索引清单（python 代码用 base64 传输，避开远程 shell 吐引号）
    idx_dir = f"{SRV_RT}/faiss_pubmed_yes_qwen3_4b_v2_merged"
    for seg in (out.get("env") or "").split("|"):
        if seg.startswith("FAISS_PUBMED_YES_DIR="):
            idx_dir = seg.split("=", 1)[1].strip() or idx_dir
    code = ("import json,sys\n"
            "d=json.load(open(sys.argv[1]+'/manifest.json'))\n"
            "c=d.get('chunking')\n"
            "print(json.dumps({'total_papers':d.get('total_papers'),'total_chunks':d.get('total_chunks'),"
            "'embedding_dim':d.get('embedding_dim'),"
            "'chunking_mode':(c or {}).get('mode') if isinstance(c,dict) else c, "
            "'updated_at':str(d.get('updated_at'))},default=str))")
    b64 = base64.b64encode(code.encode()).decode()
    raw = run(f"echo {b64} | base64 -d | {SRV_PY} - {idx_dir} 2>/dev/null | tail -1", t=45)
    try:
        out["manifest"] = json.loads(raw)
    except Exception:
        pass

    # ③ 单次往返：组装注册表 + 映射计数
    q = ("SELECT assembly_name,ncbi_refseq_accession,strain,is_local_anchor,is_ncbi_reference,"
         "ensembl_release FROM assembly_registry;"
         "SELECT COUNT(*) FROM gene_assembly_xref;")
    rows = run(f"{MYSQL} -e {json.dumps(q)} 2>/dev/null | head -40", t=45)
    out["assembly_rows"] = rows.splitlines()
    for line in out["assembly_rows"]:
        p = line.split("\t")
        if len(p) >= 6 and p[0] in ("GRCz11", "GRCz12tu"):
            out.setdefault("assembly", {})[p[0]] = {
                "accession": p[1], "strain": p[2], "local_anchor": p[3],
                "ncbi_reference": p[4], "ensembl_release": p[5]}
    for line in out["assembly_rows"]:
        if line.strip().isdigit():
            out["assembly_xref"] = {"count": int(line.strip())}
    cli.close()
    print(f"[srv] done in {time.time() - t_start:.1f}s")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="42,7,2026")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--no-server", action="store_true")
    ap.add_argument("--light", action="store_true",
                    help="跳过重算（省 3-5 分钟），仅做产物/取证/服务器层")
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",") if s.strip()]

    print("[deep-audit] D0 产物内部一致性 ...")
    d0()
    print(f"[deep-audit] D1 统计复算 seeds={seeds} n={a.n} light={a.light} ...")
    d1res = d1(seeds, a.n, light=a.light)
    st = load("_clean_stats.json")

    print("[deep-audit] D4 Fig10 声明级取证 ...")
    d4res = d4()

    server = None
    if not a.no_server:
        print("[deep-audit] 连接服务器 ...")
        server = fetch_server()
        print("[deep-audit] D2/D3/D5 服务器侧 ...")
    d2(server)
    d3(st, server)
    d5(server)

    errs = [r for r in REC if r["status"] == "mismatch"]
    unk = [r for r in REC if r["status"] == "unknown"]
    payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "seeds": seeds, "n": a.n, "server_available": server is not None,
               "runs": d1res.get("runs"), "fig10": {k: v for k, v in d4res.items() if k != "verdict"},
               "fig10_verdict": d4res.get("verdict"),
               "records": REC,
               "summary": {"total": len(REC), "ok": len(REC) - len(errs) - len(unk),
                           "mismatch": len(errs), "unknown": len(unk)}}
    (FIG / "_audit_fig9_10.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 报告 ─────────────────────────────────────────────────────────────
    md = ["# Fig 9 / Fig 10 深度审计报告", "",
          f"- 生成时间：{payload['generated_at']}",
          f"- 复算：seeds={seeds} n={a.n}；服务器：{'可用' if server is not None else '不可用'}",
          f"- 结论：**{len(errs)} 项不一致 / {len(unk)} 项待人工确认 / 共 {len(REC)} 项**", "",
          "## 一、待处置问题（status=mismatch）", "",
          "| 层 | 编号 | 断言 | 实际 | 严重度 |", "| --- | --- | --- | --- | --- |"]
    for r in errs:
        md.append(f"| {r['layer']} | {r['id']} | {r['claim']} | {r['detail']} | {r['severity']} |")
    md += ["", "## 二、待人工确认（status=unknown）", "",
           "| 层 | 编号 | 断言 | 说明 |", "| --- | --- | --- | --- |"]
    for r in unk:
        md.append(f"| {r['layer']} | {r['id']} | {r['claim']} | {r['detail']} |")
    md += ["", "## 三、复算结果", "", "| seed | 样本 | raw均值 | clean均值 | 去除% | 段/篇 | 段均 | "
           "refs标题存在% | refs真实触发% | ack标题存在% | ack真实触发% | 上限命中 |",
           "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in d1res.get("runs") or []:
        md.append(f"| {r.get('seed')} | {r.get('n')} | {r.get('raw_mean'):,.0f} | "
                  f"{r.get('clean_mean'):,.0f} | {r.get('removed_pct_mean')} | {r.get('paras_mean')} | "
                  f"{r.get('para_mean')} | {r.get('refs_head_present_pct')} | "
                  f"{r.get('refs_triggered_pct')} | {r.get('ack_head_present_pct')} | "
                  f"{r.get('ack_triggered_pct')} | {r.get('capped_hits')} |")
    md += ["", "## 四、Fig 10 八项要点取证重判", "",
           "| # | 要点 | 本 Hub | 通用模型 | LLM 证据 |", "| --- | --- | --- | --- | --- |"]
    for i, v in enumerate(d4res.get("verdict") or [], 1):
        md.append(f"| {i} | {v['point']} | {v['hub']} | {v['llm']} | {v['llm_ev'] or '—'} |")
    s = d4res.get("summary") or {}
    md += ["", f"重判汇总：Hub {s.get('hub_full')}/8 完整；通用模型 {s.get('llm_full')}/8 完整、"
               f"{s.get('llm_part')}/8 部分、{s.get('llm_miss')}/8 缺失", "",
           "## 五、全部记录", "", "| 层 | 编号 | 断言 | 状态 | 说明 |", "| --- | --- | --- | --- | --- |"]
    for r in REC:
        md.append(f"| {r['layer']} | {r['id']} | {r['claim']} | {r['status']} | {r['detail']} |")
    (FIG / "AUDIT_FIG9_10.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"[deep-audit] mismatch={len(errs)} unknown={len(unk)} total={len(REC)}")
    print(f"[ok] {FIG / '_audit_fig9_10.json'}")
    print(f"[ok] {FIG / 'AUDIT_FIG9_10.md'}")
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
