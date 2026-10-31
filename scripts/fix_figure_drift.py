# -*- coding: utf-8 -*-
"""图集漂移修复调度器 —— 发现差异 → 分类处置 → 执行修复 → 回归门禁。

设计原则
    * 单一事实来源：图面与图注的所有指标/参数均插值自 `Figure/_audit_sources.json`
      （服务器 FAISS manifest + .env + MySQL 精确计数）→ 「修复=刷新快照 + 重渲」
    * 幂等：重复执行不会累积改动
    * 门禁：只有「自洽校验 0 错误」且「三方审计 0 差异」才判定修复成功（退出码 0）
    * 可自证：`--selftest` 人为注入漂移，验证审计能感知、修复能回归

用法
    python scripts\\fix_figure_drift.py --plan       # 只输出修复计划（dry-run）
    python scripts\\fix_figure_drift.py --auto       # 刷新快照 → 重渲 → 组装 → 校验 → 审计 → 报告
    python scripts\\fix_figure_drift.py --gate       # 只做回归门禁（不刷新、不重渲）
    python scripts\\fix_figure_drift.py --selftest   # 注入漂移 → 检测 → 修复 → 复验
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fig_config as FC                                        # noqa: E402

FIG = FC.FIG_DIR                                               # .env: FIG_OUTPUT_DIR
SCRIPTS = ROOT / "scripts"
PY = sys.executable
SNAP = FIG / "_audit_sources.json"
AUDIT_JSON = FIG / "_audit.json"
VERIFY_JSON = FIG / "verify_report.json"
FIX_MD = FIG / "FIX_REPORT.md"

# 差异类型 → 处置策略
DISPOSITION = {
    "stale-index": ("auto", "快照驱动：刷新权威快照并重渲即修复；若仍不一致，说明该数字在图面是硬编码，需插值化"),
    "param-drift": ("auto", "参数来自服务器 .env（经快照注入）；刷新 + 重渲即修复"),
    "wrong-claim": ("auto", "数据库指标来自 MySQL 精确计数（经快照注入）；刷新 + 重渲即修复"),
    "scope-diff": ("manual", "口径差异：需人工确认图注表述或补充限定语"),
    "env-only": ("accept", "本机/服务器环境固有差异，属正常，不做修改"),
}


def run_step(script: str, extra: list[str] | None = None) -> dict:
    t0 = time.time()
    cmd = [PY, "-X", "utf8", str(SCRIPTS / script)] + (extra or [])
    p = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT))
    return {"script": script, "extra": extra or [], "rc": p.returncode,
            "sec": round(time.time() - t0, 1),
            "out": (p.stdout or "").strip(), "err": (p.stderr or "").strip()[-400:]}


def summarize(r: dict) -> str:
    txt = r["out"]
    for line in txt.splitlines():
        if any(k in line for k in ("TOTAL=", "mismatches=", "[ok]", "server index:")):
            return line.strip()[:120]
    return (txt.splitlines()[-1][:120] if txt else "")


def load(url: Path):
    try:
        return json.loads(url.read_text(encoding="utf-8"))
    except Exception:
        return None


def classify() -> list[dict]:
    rows = load(AUDIT_JSON) or []
    out = []
    for r in rows:
        if r.get("consistent") or r.get("severity") == "info":
            continue
        kind = r.get("kind", "")
        action, note = DISPOSITION.get(kind, ("manual", "未分类差异，需人工判断"))
        out.append({"id": r["id"], "kind": kind, "severity": r["severity"],
                    "real": r.get("real_str"), "fig": r.get("fig_hits"),
                    "action": action, "note": note, "fix": r.get("fix", "")})
    return out


def gate() -> dict:
    v = load(VERIFY_JSON) or {}
    a = load(AUDIT_JSON) or []
    mism = [r for r in a if not r.get("consistent") and r.get("severity") != "info"]
    checks = {
        "verify_no_errors": (v.get("errors") == 0),
        "verify_min_assertions": ((v.get("total") or 0) >= FC.FIG_VERIFY_MIN_ASSERTIONS),
        "audit_within_allowance": (len(mism) <= FC.FIG_AUDIT_ALLOW_MISMATCH),
    }
    return {"verify_total": v.get("total"), "verify_errors": v.get("errors"),
            "verify_warnings": v.get("warnings"),
            "audit_assertions": len(a), "audit_mismatches": len(mism),
            "checks": checks,
            "pass": all(checks.values())}


def freshness_notes() -> list[str]:
    """按配置阈值检查快照/实测数据的新鲜度。"""
    notes = []
    for label, fname, limit in (("权威快照", "_audit_sources.json", FC.FIG_SNAPSHOT_MAX_AGE_DAYS),
                                ("检索实测", "_retrieval_probe.json", FC.FIG_PROBE_MAX_AGE_DAYS)):
        p = FIG / fname
        if not p.is_file():
            notes.append(f"{label}（{fname}）不存在 → 需先生成")
            continue
        days = (time.time() - p.stat().st_mtime) / 86400
        if days > limit:
            notes.append(f"{label}（{fname}）已 {days:.0f} 天未更新，超过阈值 {limit} 天 → 建议刷新")
    return notes


def write_report(steps: list[dict], g: dict, plan: list[dict], title: str):
    snap = (load(SNAP) or {}).get("server", {})
    idx = snap.get("index", {})
    env = snap.get("env", {})
    lines = [f"# {title}", "", f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    lines += ["## 权威快照（修复依据）", "",
              f"- 索引：**{idx.get('total_papers'):,} 篇 / {idx.get('total_chunks'):,} 块 / "
              f"{idx.get('shard_count')} 分片 / {idx.get('embedding_dim')} 维** / "
              f"`{idx.get('index_version')}` / {str(idx.get('updated_at'))[:10]}",
              f"- 模型：`{(idx.get('embedding_model') or '').split('/')[-1]}` · 切分 "
              f"`{idx.get('chunking_mode')}`",
              f"- 参数：candidates **{env.get('PUBMED_YES_RETRIEVE_CANDIDATES')}** → rerank top "
              f"**{env.get('PUBMED_YES_RERANK_TOP_N')}** 篇 · 打包预算 "
              f"**{env.get('PUBMED_YES_PACK_CHARS')}** 字符 · 切分 "
              f"`{env.get('PUBMED_YES_SPLIT_MODE')}` + section 加权（生效 "
              f"`{idx.get('chunking_mode')}`）", ""]
    probe = load(FIG / "_retrieval_probe.json") or {}
    if probe.get("records"):
        rk = [r["self_rank"] for r in probe["records"] if r.get("self_rank")]
        el = sorted([e for e in (probe.get("elapsed_secs") or []) if e])
        lines += [f"- 检索实测（{probe.get('generated_at')}）：self-rank {min(rk)}-{max(rk)}"
                  + (f" · 中位耗时 {el[len(el)//2]:.1f} s" if el else "") + "", ""]
    lines += ["## 修复阶段", "", "| 步骤 | 说明 | 耗时 | 退出码 | 关键输出 |", "|---|---|---|---|---|"]
    desc = {"audit_figures_vs_project.py": "三方一致性审计", "make_figures.py": "渲染图集",
            "build_ppt.py": "组装 PPT（中、英双语两份）+ 图注", "verify_figures.py": "自洽校验"}
    for s in steps:
        lines.append(f"| `{s['script']}`{(' ' + ' '.join(s['extra'])) if s['extra'] else ''} | "
                     f"{desc.get(s['script'], '')} | {s['sec']}s | {s['rc']} | {summarize(s)} |")
    lines += ["", "## 回归门禁", "",
              f"- 自洽校验：{g.get('verify_total')} 项断言 / 错误 {g.get('verify_errors')} / "
              f"警告 {g.get('verify_warnings')}（下限 {FC.FIG_VERIFY_MIN_ASSERTIONS}）",
              f"- 三方审计：{g.get('audit_assertions')} 条断言 / 差异 {g.get('audit_mismatches')}"
              f"（允许 {FC.FIG_AUDIT_ALLOW_MISMATCH}）",
              f"- 结论：**{'PASS ✅' if g.get('pass') else 'FAIL ❌'}**", ""]
    notes = freshness_notes()
    if notes:
        lines += ["## 新鲜度提示", ""] + [f"- {x}" for x in notes] + [""]
    if plan:
        lines += ["## 待处置差异", "", "| 断言 | 类型 | 图上值 | 权威值 | 处置 | 说明 |",
                  "|---|---|---|---|---|---|"]
        for p in plan:
            fv = ", ".join(f"Fig{k}={v}" for k, v in (p["fig"] or {}).items()) or "—"
            lines.append(f"| {p['id']} | {p['kind']} | {fv} | {p['real']} | "
                         f"{ {'auto': '自动修复', 'manual': '需人工决策', 'accept': '接受' }.get(p['action'], p['action']) } | "
                         f"{p['note']} |")
        lines.append("")
    else:
        lines += ["## 待处置差异", "", "无 —— 图集与真实系统完全一致。", ""]
    FIX_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"[fix] report -> {FIX_MD.name}")


def cmd_plan():
    plan = classify()
    print(f"[fix] --plan: 待处置差异 {len(plan)} 处")
    for p in plan:
        print(f"   - [{p['severity']}] {p['id']:20s} kind={p['kind']:12s} action={p['action']:6s} "
              f"fig={p['fig']} real={p['real']}")
    if not plan:
        print("   (无差异，无需修复)")
    return 0


def cmd_auto():
    steps = []
    print("[fix] 阶段 1/5 刷新服务器权威快照 ...")
    steps.append(run_step("audit_figures_vs_project.py", ["--refresh"]))
    print("[fix] 阶段 2/5 按快照重渲图集 ...")
    steps.append(run_step("make_figures.py"))
    print("[fix] 阶段 3/5 重建中英双语 PPT 与图注 ...")
    steps.append(run_step("build_ppt.py"))
    print("[fix] 阶段 4/5 自洽校验 ...")
    steps.append(run_step("verify_figures.py"))
    print("[fix] 阶段 5/5 三方审计 ...")
    steps.append(run_step("audit_figures_vs_project.py"))

    plan = classify()
    g = gate()
    for s in steps:
        print(f"   {s['script']:34s} rc={s['rc']} {s['sec']:6.1f}s  {summarize(s)}")
    write_report(steps, g, plan, "图集漂移修复报告（自动闭环）")

    if plan:
        print(f"\n[fix] 仍有 {len(plan)} 处差异需处置：")
        for p in plan:
            print(f"   - {p['id']} ({p['action']}) {p['note']}")
    print(f"[fix] 门禁：{'PASS' if g['pass'] else 'FAIL'}  "
          f"(verify errors={g.get('verify_errors')}, audit mismatches={g.get('audit_mismatches')})")
    return 0 if g["pass"] else 1


def cmd_gate():
    steps = [run_step("verify_figures.py"), run_step("audit_figures_vs_project.py")]
    plan = classify()
    g = gate()
    write_report(steps, g, plan, "图集回归门禁报告")
    for s in steps:
        print(f"   {s['script']:34s} rc={s['rc']} {s['sec']:6.1f}s  {summarize(s)}")
    print(f"[fix] 门禁：{'PASS' if g['pass'] else 'FAIL'}")
    return 0 if g["pass"] else 1


def cmd_selftest():
    """注入漂移 → 验证审计能感知 → 执行修复 → 验证回归。"""
    if not SNAP.is_file():
        print("[fix] 缺少 _audit_sources.json，请先跑 --auto"); return 2
    bak = SNAP.with_suffix(".json.bak")
    shutil.copy2(SNAP, bak)
    ok = {"injected": False, "detected": False, "healed": False}
    try:
        d = json.loads(SNAP.read_text(encoding="utf-8"))
        orig = d["server"]["index"]["total_papers"]
        d["server"]["index"]["total_papers"] = orig + 1234        # 注入 +1234 篇漂移
        SNAP.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        ok["injected"] = True
        print(f"[selftest] 已注入漂移：total_papers {orig:,} → {orig+1234:,}（图未重渲）")

        r = run_step("audit_figures_vs_project.py")
        ok["detected"] = "mismatches=0" not in r["out"]
        print(f"[selftest] 审计感知：{'YES ✅' if ok['detected'] else 'NO ❌'}  {summarize(r)}")

        # 修复：还原权威快照并重渲
        shutil.copy2(bak, SNAP)
        print("[selftest] 执行修复：还原权威快照 + 重渲图集 ...")
        run_step("make_figures.py")
        r2 = run_step("audit_figures_vs_project.py")
        ok["healed"] = "mismatches=0" in r2["out"]
        print(f"[selftest] 修复回归：{'YES ✅' if ok['healed'] else 'NO ❌'}  {summarize(r2)}")
    finally:
        shutil.copy2(bak, SNAP)
        bak.unlink(missing_ok=True)
        run_step("make_figures.py")      # 确保产物回到权威值
        print("[selftest] 已还原快照并重渲")
    good = ok["injected"] and ok["detected"] and ok["healed"]
    print(f"[selftest] 结论：修复管道{'有效 ✅' if good else '异常 ❌'}  {ok}")
    return 0 if good else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return cmd_selftest()
    if a.auto:
        return cmd_auto()
    if a.gate:
        return cmd_gate()
    return cmd_plan()


if __name__ == "__main__":
    raise SystemExit(main())
