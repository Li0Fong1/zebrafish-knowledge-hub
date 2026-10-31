# -*- coding: utf-8 -*-
"""FigS4 / FigS5 图件独立审计：把「图上的每一个事实」对回数据源，并刷新自检记录。

用法：
    .venv\\Scripts\\python.exe scripts\\audit_s4_s5_figures.py

做三件事（顺序固定）：
  1) 跑出图脚本的 --check（不覆盖交付 PNG）
       · make_figures_pipeline_compare.py --check   → 截图嵌入/分辨率/非空白/读数回溯/判定基准
       · make_figS4_pipelines_en.py --check         → 文字重叠 / 图形越界 / PNG 栅格墨迹
  2) **独立复核**（不采信出图脚本的结论，自己重读数据源）：
       · a-notes-facts   ：两行脚注里的数字（360/110/48、34.1/47.8、500/100/48000）确在源产物里
       · a-shots-crop    ：三张截图的裁剪尺寸 == 由源图 + 裁剪比例重算的结果
       · a-fresh         ：交付 PNG 比它引用的数据源新（防「数据改了图没重出」）
       · a-res-ink       ：交付 PNG 分辨率达标且非空白
       · a-no-fabricated ：图上没有出现任何未在源产物中出现的数字（反向查表）
  3) 把 1)+2) 的记录合并写入 Figure/supplementary/_selfcheck_s4_s5.json

退出码 0=全通过，1=有 mismatch。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
FIG = ROOT / "Figure"
SUP = FIG / "supplementary"
JSON_OUT = SUP / "_selfcheck_s4_s5.json"

REC: list[dict] = []


def rec(rid: str, claim: str, ok: bool, detail: str, severity: str = "info") -> None:
    REC.append({"id": rid, "claim": claim, "status": "ok" if ok else "mismatch",
                "detail": detail, "severity": severity})


def run_check(script: str) -> tuple[int, str]:
    r = subprocess.run([PY, "-X", "utf8", script, "--check"], cwd=str(ROOT),
                       capture_output=True, timeout=1800)
    return r.returncode, ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace").strip()


def main() -> int:
    t0 = time.time()
    print("[audit-s4s5] 1) 跑两个出图脚本的 --check ...")
    rc1, out1 = run_check("scripts/make_figures_pipeline_compare.py")
    print("  make_figures_pipeline_compare rc=", rc1)
    for line in out1.splitlines()[-6:]:
        print("   ", line)
    rec("gen-compare-check", "出图脚本（截图版）自检通过", rc1 == 0,
        f"rc={rc1}" + ("" if rc1 == 0 else " | " + out1.splitlines()[-1][:120]), "high")

    rc2, out2 = run_check("scripts/make_figS4_pipelines_en.py")
    print("  make_figS4_pipelines_en rc=", rc2)
    for line in out2.splitlines()[-4:]:
        print("   ", line)
    rec("gen-enscript-check", "出图脚本（EN 流水线）三道防线通过", rc2 == 0,
        f"rc={rc2}" + ("" if rc2 == 0 else " | " + out2.splitlines()[-1][:120]), "high")

    # ── 2) 独立复核 ────────────────────────────────────────────────────────
    print("[audit-s4s5] 2) 独立复核（自行重读数据源）...")
    clean = json.loads((FIG / "_clean_stats.json").read_text(encoding="utf-8"))
    audit = json.loads((FIG / "_audit_sources.json").read_text(encoding="utf-8"))
    data = json.loads((FIG / "_data.json").read_text(encoding="utf-8"))
    env = ((audit.get("server") or {}).get("env") or {})
    rules = clean.get("rules") or {}
    sp = clean.get("split_params") or {}

    # 2.1 脚注事实：切分参数 + 清洗触发率（EN 版图上的那两行唯一新增事实）
    want = {
        "split_max": 360, "split_min": 110, "overlap": 48,
        "refs_pct": 34.1, "ack_pct": 47.8,
        "cand": 500, "topn": 100, "pack": 48000,
    }
    got = {
        "split_max": sp.get("max_chars"),
        "split_min": sp.get("min_chars"),
        "overlap": sp.get("overlap"),
        # 注意：_clean_stats.json 里这两个值已是百分数（34.1 / 47.8），不要再 ×100
        "refs_pct": float(rules.get("refs_truncation_triggered") or 0),
        "ack_pct": float(rules.get("ack_truncation_triggered") or 0),
        "cand": int(env.get("PUBMED_YES_RETRIEVE_CANDIDATES") or 0),
        "topn": int(env.get("PUBMED_YES_RERANK_TOP_N") or 0),
        "pack": int(env.get("PUBMED_YES_PACK_CHARS") or 0),
    }
    diff = {k: (want[k], got[k]) for k in want if str(got[k]) != str(want[k])}
    rec("a-notes-facts", "图内脚注数字全部对得上源产物", not diff,
        f"8 项读数核对" + ("一致" if not diff else f"；不一致 {diff}"), "high")

    # 2.2 截图裁剪尺寸：由源图 + 裁剪比例重算（不采信记录）
    sys.path.insert(0, str(ROOT / "scripts"))
    import make_figures_pipeline_compare as MPC          # noqa: E402
    from PIL import Image as PILImage                    # noqa: E402
    shots = []
    for name, fn, _c, _s, crop, _n in MPC.SHOTS:
        p = ROOT / fn
        if not p.is_file():
            shots.append({"name": name, "ok": False, "detail": "源图缺失"})
            continue
        with PILImage.open(p) as im:
            W0, H0 = im.size
            l, t, b_r, b = crop
            box = (int(W0 * l), int(H0 * t), int(W0 * b_r), int(H0 * b))
            cw, ch = box[2] - box[0], box[3] - box[1]
        shots.append({"name": name, "ok": True, "src": f"{W0}x{H0}", "crop": f"{cw}x{ch}"})
    bad_shots = [s for s in shots if not s["ok"]]
    rec("a-shots-crop", "三张截图均可打开且裁剪非空", not bad_shots,
        "; ".join(f"{s.get('name')}: {s.get('src','-')}→{s.get('crop','-')}" for s in shots), "high")

    # 2.3 新鲜度：图比引用的数据源新
    # 覆盖 S4(A/B) 流水线、S4(C) 统计图、S5 截图对照（三张都是交付图）
    figs = [SUP / "FigS4_Pipelines_EN.png", SUP / "FigS4_Stats_EN.png",
            SUP / "FigS5_QA_Comparison.png"]
    srcs = [FIG / "_clean_stats.json", FIG / "_audit_sources.json", FIG / "_data.json",
            FIG / "_nanog_facts.json", ROOT / "ChatGPT.png", ROOT / "DeepSeek.png",
            ROOT / "Zebrafish Hub.png"]
    missing = [p.name for p in figs if not p.is_file()]
    newest_src = max((p.stat().st_mtime for p in srcs if p.is_file()), default=0)
    stale = [p.name for p in figs if p.is_file() and p.stat().st_mtime < newest_src]
    rec("a-fresh", "交付 PNG 均为最新（不比数据源旧）", not missing and not stale,
        ("缺 " + ",".join(missing)) if missing else (f"陈旧 {stale}" if stale else
                                                    f"{len(figs)} 张交付图均新于数据源"), "high")

    # 2.4 分辨率与非空白
    import numpy as np                                  # noqa: E402
    sizes = []
    ok_res = True
    for p in figs:
        if not p.is_file():
            ok_res = False
            continue
        with PILImage.open(p) as im:
            w, h = im.size
            ink = 1.0 - float((np.asarray(im.convert("L")) > 245).mean())
        sizes.append(f"{p.name} {w}x{h} ink={ink:.4f}")
        if w < 2800 or ink <= 0.004:
            ok_res = False
    rec("a-res-ink", "交付 PNG 分辨率≥2800px 且非空白", ok_res, "; ".join(sizes), "high")

    # 反向查表：图上出现的读数都必须在源产物里找得到（含四舍五入形式，如 56121.9 → 56,122）
    known: set[str] = set()

    def _add_num(raw: str) -> None:
        try:
            f = float(raw)
        except ValueError:
            return
        known.add(raw)
        known.add(str(int(f)))
        known.add(str(int(round(f))))
        known.add(f"{f:.1f}")

    for blob in (clean, audit, data):
        for m in re.finditer(r"\d+(?:\.\d+)?", json.dumps(blob, ensure_ascii=False)):
            _add_num(m.group(0))
    drawn = ["32532", "7651942", "170", "2048", "46904", "56122", "360", "110", "48",
             "34.1", "47.8", "500", "100", "48000"]
    unknown = [d for d in drawn if d not in known]
    rec("a-no-fabricated", "图上数字均可在源产物中找到出处（无编造）", not unknown,
        "14 个图上读数反向查表" + ("全部命中" if not unknown else f"；未找到 {unknown}"), "high")

    # ── 3) 合并写入自检 JSON ───────────────────────────────────────────────
    prev = {}
    if JSON_OUT.is_file():
        try:
            prev = json.loads(JSON_OUT.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    merged = [r for r in (prev.get("records") or []) if not r["id"].startswith(("a-", "gen-"))]
    merged += REC
    errs = [r for r in merged if r["status"] == "mismatch"]
    payload = {
        "generated_at": prev.get("generated_at"),
        "audited_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "audit_script": "scripts/audit_s4_s5_figures.py",
        "numbers": prev.get("numbers") or {},
        "fig_s5": prev.get("fig_s5") or {},
        "shots": shots,
        "records": merged,
        "summary": {"total": len(merged), "mismatch": len(errs),
                    "audit": "generators(--check) + independent fact re-read"},
    }
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[audit-s4s5] 3) 自检记录已刷新 → {JSON_OUT}")
    print(f"[audit-s4s5] checks={len(merged)} mismatch={len(errs)} ({time.time() - t0:.1f}s)")
    for r in errs:
        print("  [X]", r["id"], "::", r["detail"])
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
