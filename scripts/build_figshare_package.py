# -*- coding: utf-8 -*-
"""Figshare 数据包构建脚本（可直接重跑，幂等）。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts\\build_figshare_package.py
    .venv\\Scripts\\python.exe -X utf8 scripts\\build_figshare_package.py --check

原则：
  * **只搬运与派生**：原始产物一律逐字节复制，派生文件（CSV/MD）只做格式转换，
    绝不重算、绝不补值；源文件缺失时打印 SKIP 而不是造数据。
  * 每个产物在 MANIFEST.json 里记录 sha256 / 字节数 / 来源路径。
  * README.md 里的待办（仓库地址、Figshare DOI）保持占位，不猜。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "paper" / "data_package"

# 源文件 → 包内相对路径（逐字节复制）。空文件/缺失文件会记入 MANIFEST 的 skipped 字段，不静默忽略。
COPY_MAP = [
    # 01 基准题与评分细则
    ("项目阶段/01_金标全文/清单与验收/eval_isomorph_100_20260901.json", "01_benchmark/benchmark_100_questions.source.json"),
    ("项目阶段/01_金标全文/清单与验收/eval_30_questions_20260901.json", "01_benchmark/benchmark_30_questions.source.json"),
    # 02 原始输出与盲评输入
    ("logs/eval_isomorph/_eval_isomorph_answers.json", "02_raw_outputs/eval_isomorph_answers.json"),
    ("logs/eval_isomorph/_eval_isomorph_result.json", "02_raw_outputs/eval_isomorph_result.json"),
    ("logs/eval_isomorph/blind_pack.json", "02_raw_outputs/blind_pack.json"),
    ("logs/eval_isomorph/_blind_label_secrets.json", "02_raw_outputs/blind_label_secrets.json"),
    ("logs/eval_isomorph_50/_eval_isomorph_answers.json", "02_raw_outputs/eval_isomorph_50_answers.json"),
    ("logs/eval_isomorph_50/_eval_isomorph_result.json", "02_raw_outputs/eval_isomorph_50_result.json"),
    ("logs/eval_isomorph_50/盲评打分表.md", "02_raw_outputs/blind_scoring_sheet_50.md"),
    ("Figure/_config_sweep_answers.json", "02_raw_outputs/config_sweep_answers.json"),
    ("logs/_config_sweep_judge_raw.jsonl", "02_raw_outputs/judge_raw_deepseek-chat.jsonl"),
    ("logs/_config_sweep_judge_raw_reasoner.jsonl", "02_raw_outputs/judge_raw_deepseek-reasoner.jsonl"),
    ("logs/_config_sweep_judge_raw_reasoner_p1.jsonl", "02_raw_outputs/judge_raw_reasoner_p1.jsonl"),
    ("logs/_config_sweep_judge_raw_reasoner_p2.jsonl", "02_raw_outputs/judge_raw_reasoner_p2.jsonl"),
    ("logs/_config_sweep_judge_raw_reasoner_p3.jsonl", "02_raw_outputs/judge_raw_reasoner_p3.jsonl"),
    ("logs/_config_sweep_judge_raw_reasoner_p4.jsonl", "02_raw_outputs/judge_raw_reasoner_p4.jsonl"),
    ("logs/_config_sweep_judge_raw_reasoner_p5.jsonl", "02_raw_outputs/judge_raw_reasoner_p5.jsonl"),
    # 03 评分
    ("Figure/_config_sweep_scores.json", "03_scoring/config_sweep_scores.json"),
    ("Figure/_config_sweep_scores_reasoner.json", "03_scoring/config_sweep_scores_reasoner.json"),
    ("Figure/_config_sweep_scores_reasoner_p1.json", "03_scoring/config_sweep_scores_reasoner_p1.json"),
    ("Figure/_config_sweep_scores_reasoner_p2.json", "03_scoring/config_sweep_scores_reasoner_p2.json"),
    ("Figure/_config_sweep_scores_reasoner_p3.json", "03_scoring/config_sweep_scores_reasoner_p3.json"),
    ("Figure/_config_sweep_scores_reasoner_p4.json", "03_scoring/config_sweep_scores_reasoner_p4.json"),
    ("Figure/_config_sweep_scores_reasoner_p5.json", "03_scoring/config_sweep_scores_reasoner_p5.json"),
    # 04 统计检验与图统计
    ("Figure/_config_sweep_verify.json", "04_statistics/config_sweep_verify.json"),
    ("Figure/_figs4c_summary.json", "04_statistics/figs4c_summary.json"),
    ("Figure/_figs4c_audit.json", "04_statistics/figs4c_audit.json"),
    # 05 数值出处（与 scripts/verify_methods_numbers.py 的数据源一致）
    ("Figure/_audit_sources.json", "05_provenance/audit_sources.json"),
    ("Figure/_clean_stats.json", "05_provenance/clean_stats.json"),
    ("Figure/_server_env_probe.json", "05_provenance/server_env_probe.json"),
    ("Figure/_llm_usage.json", "05_provenance/llm_usage.json"),
    # 服务器工具版本核实产物（scripts/probe_tool_versions.py 生成；已脱敏主机名）
    ("docs/paper/provenance/tool_versions.json", "05_provenance/tool_versions.json"),
]

TODO_ITEMS = [
    ("data_deposit", "上传本包后从 Figshare 回填 DOI"),
]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_verbatim(rel_src: str, rel_dst: str, manifest: list, skipped: list) -> bool:
    src = ROOT / rel_src
    dst = OUT / rel_dst
    if not src.exists():
        print(f"SKIP  源文件不存在：{rel_src}")
        skipped.append({"path": rel_dst, "source": rel_src, "reason": "missing"})
        return False
    if src.stat().st_size == 0:
        print(f"SKIP  源文件为空（0 字节）：{rel_src}")
        skipped.append({"path": rel_dst, "source": rel_src, "reason": "empty"})
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    manifest.append({
        "path": rel_dst, "kind": "verbatim", "source": rel_src,
        "bytes": dst.stat().st_size, "sha256": sha256(dst),
    })
    print(f"COPY  {rel_dst}  <- {rel_src}  ({dst.stat().st_size} B)")
    return True


def write_derived(rel_dst: str, text: str, note: str, manifest: list) -> None:
    dst = OUT / rel_dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    manifest.append({
        "path": rel_dst, "kind": "derived", "derived_from": note,
        "bytes": dst.stat().st_size, "sha256": sha256(dst),
    })
    print(f"WRITE {rel_dst}  ({dst.stat().st_size} B)")


def _join(v) -> str:
    if isinstance(v, (list, tuple)):
        return " | ".join(str(x) for x in v)
    return "" if v is None else str(v)


def derive_benchmark_csv() -> tuple[str, str] | None:
    src = ROOT / "项目阶段/01_金标全文/清单与验收/eval_isomorph_100_20260901.json"
    if not src.exists():
        print("SKIP  基准题源文件缺失，跳过 CSV 派生")
        return None
    d = json.loads(src.read_text(encoding="utf-8"))
    qs = d.get("questions") or []
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "type", "question", "must_include", "forbidden"])
    for q in qs:
        w.writerow([q.get("id"), q.get("type"), q.get("q"), _join(q.get("must")), _join(q.get("forbid"))])
    return buf.getvalue(), f"{src.relative_to(ROOT)} · questions[{len(qs)}]"


def derive_pairs_csv() -> tuple[str, str] | None:
    src = ROOT / "项目阶段/01_金标全文/清单与验收/eval_isomorph_100_20260901.json"
    if not src.exists():
        return None
    d = json.loads(src.read_text(encoding="utf-8"))
    pairs = d.get("isomorphic_pairs") or []
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["pair_id", "type", "zebrafish_question", "potato_question"])
    for p in pairs:
        w.writerow([p.get("pair_id"), p.get("type"), p.get("zebrafish"), p.get("potato")])
    return buf.getvalue(), f"{src.relative_to(ROOT)} · isomorphic_pairs[{len(pairs)}]"


def derive_rubric_md() -> tuple[str, str] | None:
    src = ROOT / "项目阶段/01_金标全文/清单与验收/eval_isomorph_100_20260901.json"
    if not src.exists():
        return None
    d = json.loads(src.read_text(encoding="utf-8"))
    r = d.get("rubric") or {}
    dims = r.get("dimensions") or []
    lines = ["#评分细则（7 维，满分 10）", "",
             f"- 盲评口径：{r.get('blind_scale', '(未记录)')}",
             f"- 维度数：{len(dims)}；满分：{sum(int(x.get('max') or 0) for x in dims)}", "",
             "| 维度 ID | 名称 | 满分 |", "| --- | --- | --- |"]
    for x in dims:
        lines.append(f"| `{x.get('id')}` | {x.get('name')} | {x.get('max')} |")
    lines += ["", "> 本文件由 `scripts/build_figshare_package.py` 从基准题 JSON 派生（仅格式转换）。"]
    return "\n".join(lines) + "\n", f"{src.relative_to(ROOT)} · rubric"


def derive_scoring_csv() -> tuple[str, str] | None:
    src = ROOT / "Figure/_config_sweep_scores.json"
    if not src.exists():
        return None
    d = json.loads(src.read_text(encoding="utf-8"))
    rows = d.get("rows") or []
    raw_dims = d.get("dims") or []
    # dims 可能是 ["a","b"] 或 [{"id":"a",...}]，两种都接住
    dims = [x.get("id") if isinstance(x, dict) else str(x) for x in raw_dims]
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["qid", "config", "total"] + [f"dim_{x}" for x in dims])
    for r in rows:
        sc = r.get("dims") or r.get("scores") or {}
        w.writerow([r.get("qid"), r.get("config"), r.get("total")] + [sc.get(x, "") for x in dims])
    return buf.getvalue(), f"{src.relative_to(ROOT)} · rows[{len(rows)}], dims[{len(dims)}]"


def derive_stats_md() -> tuple[str, str] | None:
    src = ROOT / "Figure/_config_sweep_verify.json"
    if not src.exists():
        return None
    d = json.loads(src.read_text(encoding="utf-8"))
    lines = ["#统计检验摘要（精确置换检验）", ""]
    for k, v in (d.get("stats") or {}).items():
        if isinstance(v, dict):
            bits = ", ".join(f"{kk}={vv}" for kk, vv in v.items())
            lines.append(f"- `{k}`: {bits}")
    checks = d.get("checks") or []
    if checks:
        lines += ["", f"## 一致性检查（{len(checks)} 项）", ""]
        for c in checks:
            lines.append(f"- [{c.get('status')}] `{c.get('id')}` {c.get('desc')} — {c.get('detail')}")
    lines += ["", "> 本文件由 `scripts/build_figshare_package.py` 从检验结果 JSON 派生（仅格式转换）。"]
    return "\n".join(lines) + "\n", f"{src.relative_to(ROOT)} · stats+checks"


def readme_text(manifest: list, skipped: list) -> str:
    by_kind: dict[str, list] = {}
    for m in manifest:
        by_kind.setdefault(m["path"].split("/")[0], []).append(m)
    def section(prefix: str, title: str, blurb: str) -> list[str]:
        items = sorted(by_kind.get(prefix, []), key=lambda x: x["path"])
        if not items:
            return []
        out = [f"### {prefix} — {title}", "", blurb, "", "| 文件 | 类型 | 字节 |", "| --- | --- | --- |"]
        out += [f"| `{i['path']}` | {i['kind']} | {i['bytes']} |" for i in items]
        out.append("")
        return out

    lines = [
        "# Zebrafish Knowledge Hub — evaluation & provenance data package",
        "",
        "本包对应 `docs/METHODS/METHODS.md`（§2.8 Performance evaluation）与 `docs/paper/BACK_MATTER.md`",
        "（DATA AND CODE AVAILABILITY）中承诺的可复核产物：100 题基准、盲评输入、原始输出、评分表与精确置换检验结果。",
        "",
        "## 构建方式（可复现）",
        "",
        "```bash",
        ".venv\\Scripts\\python.exe -X utf8 scripts\\build_figshare_package.py",
        "```",
        "",
        "原始产物**逐字节复制**（`kind=verbatim`）；CSV/Markdown 只是格式转换（`kind=derived`，",
        "由脚本从对应源文件生成，未重算任何数值）。全部文件的 sha256 见 `MANIFEST.json`。",
        "",
    ]
    lines += section("01_benchmark", "基准题与评分细则",
                     "100 题基准（含原 30 题）、20 对同构题（斑马鱼 ↔ 马铃薯）与 7 维评分细则；CSV 为便于传阅的派生视图。")
    lines += section("02_raw_outputs", "原始输出与盲评输入",
                     "各配置的原始回答与自动指标、交给评委的盲评包，以及 A/B 解密表（用于验证盲法确实成立）。")
    lines += section("03_scoring", "评分表",
                     "评委逐维打分明细（JSON 为原始文件，CSV 为同一数据的扁平视图）。")
    lines += section("04_statistics", "统计检验与图统计",
                     "精确置换检验结果与一致性检查；FigS4 汇总统计与其像素级审计记录。")
    lines += section("05_provenance", "数值出处",
                     "论文正文中每个数字到仓库产物的对应关系：服务器/索引快照、清洗触发率、LLM API 用量。")
    lines += [
        "## 复算与核对",
        "",
        "```bash",
        "# 重算 METHODS 中的全部数字并与论文文本比对（含占位符检查）",
        ".venv\\Scripts\\python.exe -X utf8 scripts\\verify_methods_numbers.py --write",
        "# 图 S4 的像素级审计（几何反演 + 图-数一致性）",
        ".venv\\Scripts\\python.exe -X utf8 scripts\\audit_figs4c_figure.py",
        "```",
        "",
        "## 许可与来源",
        "",
        "- 从公开库下载的注释数据（Ensembl GRCz11 r116、NCBI RefSeq GRCz12tu、ZFIN、Bgee v15.2、EBI Expression Atlas E-ERAD-475）",
        "  遵循各库自身条款；本包只包含**我们自己的派生结果**，不重新分发原始数据库文件。",
        "- 文献题录来自 PubMed / Europe PMC；本包只含 PMID 级引用与统计，不含全文。",
        "- 基准题、评分细则、原始回答与统计结果默认以 CC BY 4.0 发布（如与期刊政策冲突，以期刊要求为准）。",
        "",
        "## 待补（不编造，留占位）",
        "",
    ]
    lines += [f"- `{{{{TODO:{k}}}}}`：{desc}" for k, desc in TODO_ITEMS]
    lines += [
        "- 代码仓库：正文已按“出版时公开”处理（*The code will be released upon publication.*），",
        "  正式 Gitee/GitHub 地址待公开后回填，故本包不再保留仓库地址占位符。",
    ]
    if skipped:
        lines += ["", "## 本次未能纳入的文件（如实记录，未构造替代品）", ""]
        lines += [f"- `{s['source']}` → {s['path']}（{s['reason']}）" for s in skipped]
    lines += [
        "",
        f"_生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验已有包与 MANIFEST 是否一致")
    args = ap.parse_args()

    if args.check:
        mf = OUT / "MANIFEST.json"
        if not mf.exists():
            print("MANIFEST.json 不存在，先运行不带 --check 的构建")
            return 1
        data = json.loads(mf.read_text(encoding="utf-8"))
        bad = 0
        for f in data.get("files", []):
            p = OUT / f["path"]
            if not p.exists():
                print(f"MISSING {f['path']}"); bad += 1; continue
            if sha256(p) != f["sha256"]:
                print(f"HASH-MISMATCH {f['path']}"); bad += 1
        # 盲点修复：包内存在但 MANIFEST 未收录的文件同样算问题
        # （此前 tool_versions.json 就因手工放入而漏检，导致清单与实际不一致）
        recorded = {f["path"] for f in data.get("files", [])}
        extras = []
        for p in sorted(OUT.rglob("*")):
            if not p.is_file() or p.name == "MANIFEST.json":
                continue
            rel = p.relative_to(OUT).as_posix()
            if rel not in recorded:
                extras.append(rel)
        for rel in extras:
            print(f"EXTRA-NOT-IN-MANIFEST {rel}")
        bad += len(extras)
        print(f"check done: {len(recorded)} recorded, {len(extras)} extra, {bad} problem(s)")
        return 0 if bad == 0 else 2

    OUT.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    skipped: list[dict] = []

    for rel_src, rel_dst in COPY_MAP:
        copy_verbatim(rel_src, rel_dst, manifest, skipped)

    derived = [
        ("01_benchmark/benchmark_100_questions.csv", derive_benchmark_csv),
        ("01_benchmark/isomorphic_pairs.csv", derive_pairs_csv),
        ("01_benchmark/rubric.md", derive_rubric_md),
        ("03_scoring/config_sweep_scores.csv", derive_scoring_csv),
        ("04_statistics/config_sweep_verify_summary.md", derive_stats_md),
    ]
    for rel_dst, fn in derived:
        res = fn()
        if not res:
            continue
        text, note = res
        write_derived(rel_dst, text, note, manifest)

    write_derived("README.md", readme_text(manifest, skipped), "scripts/build_figshare_package.py", manifest)

    payload = {
        "package": "zebrafish-knowledge-hub-evaluation",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "generator": "scripts/build_figshare_package.py",
        "root": str(ROOT),
        "file_count": len(manifest),
        "totals": {
            "verbatim": sum(1 for m in manifest if m["kind"] == "verbatim"),
            "derived": sum(1 for m in manifest if m["kind"] == "derived"),
            "bytes": sum(int(m["bytes"]) for m in manifest),
        },
        "skipped": skipped,
        "pending_placeholders": [k for k, _ in TODO_ITEMS],
        "files": manifest,
    }
    (OUT / "MANIFEST.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成：{payload['file_count']} 个文件，{payload['totals']['bytes']} 字节 → {OUT}")
    if skipped:
        print("跳过（已如实记入 MANIFEST.skipped）：" + ", ".join(f"{s['path']}({s['reason']})" for s in skipped))
    print("待补占位：" + ", ".join(payload["pending_placeholders"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
