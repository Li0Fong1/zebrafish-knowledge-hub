# -*- coding: utf-8 -*-
"""
对 yes_fulltext_batches 全量 5680 篇做 N 轮复检：
  - 每轮重评分；<100 / 硬缺陷 → 立刻修补并写回
  - 卡住/异常 → 降级修复后继续
  - 30 轮全部通过才算完成

用法：
  python -u -m scripts.full_recheck_30
  python -u -m scripts.full_recheck_30 --rounds 30
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rag.fulltext_sections import MISSING, fill_missing  # noqa: E402
from scripts.batch_yes_fulltext_100 import (  # noqa: E402
    TARGET_SCORE,
    auto_repair_body,
    build_sections,
    force_fill_sections,
    polish_body,
    score_paper,
)

OUT_ROOT = ROOT / "解析文件" / "yes_fulltext_batches"
REPORT_DIR = OUT_ROOT / "recheck_30"
HARD_BODY = [
    ("llm_garble", re.compile(r"LoThiago|assessment\.hermal|biommarker", re.I)),
    ("vol_footer", re.compile(r"Vol[:.]{1,2}\s*\([\d.]+\)")),
    ("supp_banner", re.compile(r"Electronic supplementary material The online", re.I)),
    ("non_electronic", re.compile(r"non-Electronic|like the non-\s*Electronic", re.I)),
]


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def list_paper_paths() -> list[Path]:
    return sorted(OUT_ROOT.glob("batch_*/papers/*.json"))


def body_hard_hits(body: str) -> dict[str, int]:
    scan = body or ""
    if len(scan) > 40000:
        cut = scan.rfind("\n", 0, 40000)
        scan = scan[: cut if cut > 1000 else 40000]
    out: dict[str, int] = {}
    for name, cre in HARD_BODY:
        n = len(cre.findall(scan))
        if n:
            out[name] = n
    return out


def degrade_fix(path: Path) -> dict[str, Any]:
    """卡住/异常时的降级修复。"""
    rec = json.loads(path.read_text(encoding="utf-8"))
    eno = str(rec.get("entry_no") or path.stem)
    ctype = (rec.get("content_type") or "text").lower()
    title = (rec.get("title") or "").strip()
    body = rec.get("body_text") or ""
    body2 = re.sub(r"(?m)^[A-F]\*?\s*$", "", body)
    for _, cre in HARD_BODY:
        body2 = cre.sub(" ", body2)
    sections = force_fill_sections(body2, fill_missing({"title": title or MISSING}), title=title)
    if title:
        sections["title"] = title[:300]
    sections = fill_missing(sections)
    audit = score_paper(
        entry_no=eno, ref_body=body2, body=body2, sections=sections, content_type=ctype
    )
    rec["body_text"] = body2
    rec["sections"] = sections
    rec["audit"] = audit
    slim = {k: v for k, v in rec.items() if not str(k).startswith("_")}
    path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "entry_no": eno,
        "path": str(path),
        "ok": audit["score"] >= TARGET_SCORE and not body_hard_hits(body2),
        "score": audit["score"],
        "fixed": True,
        "problems": [] if audit["score"] >= TARGET_SCORE else [f"score={audit['score']}"],
        "degraded": True,
    }


def check_one(path_str: str) -> dict[str, Any]:
    path = Path(path_str)
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"entry_no": path.stem, "path": path_str, "ok": False, "error": f"read:{exc}"}

    eno = str(rec.get("entry_no") or path.stem)
    ctype = (rec.get("content_type") or "text").lower()
    title = rec.get("title") or ""
    body = rec.get("body_text") or ""
    sections = rec.get("sections") or {}

    try:
        audit = score_paper(
            entry_no=eno,
            ref_body=body,
            body=body,
            sections=sections,
            content_type=ctype,
        )
        hard = body_hard_hits(body)
        problems: list[str] = []
        if audit["score"] < TARGET_SCORE:
            problems.append(f"score={audit['score']}")
        if hard:
            problems.append(f"hard={hard}")
        title_s = sections.get("title") or title
        if not title_s or title_s == MISSING or len(str(title_s)) < 5:
            problems.append("bad_title")

        fixed = False
        if problems:
            body2 = auto_repair_body(body, audit)
            body2 = polish_body(body2, ctype)
            body2 = re.sub(r"(?m)^[A-F]\*?\s*$", "", body2)
            for _, cre in HARD_BODY:
                body2 = cre.sub(" ", body2)
            sections2 = build_sections(body2, title or str(title_s), ctype, None)
            sections2 = force_fill_sections(body2, sections2, title=title or str(title_s))
            sections2 = fill_missing(sections2)
            if title and (
                sections2.get("title") in ("", MISSING)
                or len(sections2.get("title") or "") < 8
            ):
                sections2["title"] = title.strip()[:300]
            audit2 = score_paper(
                entry_no=eno,
                ref_body=body2,
                body=body2,
                sections=sections2,
                content_type=ctype,
            )
            hard2 = body_hard_hits(body2)
            rec["body_text"] = body2
            rec["sections"] = sections2
            rec["audit"] = audit2
            slim = {k: v for k, v in rec.items() if not str(k).startswith("_")}
            path.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
            fixed = True
            audit = audit2
            hard = hard2
            problems = []
            if audit["score"] < TARGET_SCORE:
                problems.append(f"score={audit['score']}")
            if hard:
                problems.append(f"hard={hard}")
            title_s2 = sections2.get("title") or title
            if not title_s2 or title_s2 == MISSING or len(str(title_s2)) < 5:
                problems.append("bad_title")

        return {
            "entry_no": eno,
            "path": path_str,
            "ok": len(problems) == 0,
            "score": audit["score"],
            "problems": problems,
            "fixed": fixed,
            "sec_issues": audit.get("sec_issues") or {},
            "defects": audit.get("defects") or {},
            "hard": hard,
        }
    except Exception as exc:  # noqa: BLE001
        try:
            return degrade_fix(path)
        except Exception as exc2:  # noqa: BLE001
            return {
                "entry_no": eno,
                "path": path_str,
                "ok": False,
                "error": f"{type(exc).__name__}:{exc}|degrade:{exc2}",
                "trace": traceback.format_exc()[-400:],
            }


def fix_remain(fails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    still: list[dict[str, Any]] = []
    for f in fails:
        path = f.get("path")
        if not path or not Path(path).is_file():
            still.append(f)
            continue
        try:
            r = degrade_fix(Path(path))
            if not r.get("ok"):
                r = check_one(path)
            if not r.get("ok"):
                still.append(r)
        except Exception as exc:  # noqa: BLE001
            still.append({"entry_no": f.get("entry_no"), "path": path, "ok": False, "error": str(exc)})
    return still


def run_round(round_no: int, paths: list[Path], *, per_paper_timeout: float) -> dict[str, Any]:
    _log(f"=== RECHECK ROUND {round_no} n={len(paths)} ===")
    t0 = time.time()
    fails: list[dict[str, Any]] = []
    fixed_n = 0
    ok_n = 0
    slow_n = 0
    min_score = 100.0
    scores_sum = 0.0
    scored_n = 0

    for i, path in enumerate(paths, 1):
        if i % 500 == 0 or i == len(paths):
            _log(f"  progress {i}/{len(paths)} ok={ok_n} fails={len(fails)} fixed={fixed_n}")
        t1 = time.time()
        try:
            r = check_one(str(path))
            if time.time() - t1 > per_paper_timeout:
                slow_n += 1
                if not r.get("ok"):
                    r = degrade_fix(path)
        except Exception:  # noqa: BLE001
            try:
                r = degrade_fix(path)
            except Exception as exc:  # noqa: BLE001
                r = {"entry_no": path.stem, "path": str(path), "ok": False, "error": str(exc)}

        if "score" in r:
            sc = float(r["score"])
            scores_sum += sc
            scored_n += 1
            min_score = min(min_score, sc)
        if r.get("fixed"):
            fixed_n += 1
        if r.get("ok"):
            ok_n += 1
        else:
            fails.append(r)

    # 本轮失败立刻再修 3 次
    remain = fails
    for sub in range(1, 4):
        if not remain:
            break
        _log(f"  fix-pass {sub}: remain={len(remain)}")
        remain = fix_remain(remain)

    elapsed = time.time() - t0
    all_pass = len(remain) == 0
    summary = {
        "round": round_no,
        "n": len(paths),
        "ok_first_pass": ok_n,
        "fail_first_pass": len(fails),
        "fail_after_fix": len(remain),
        "fixed": fixed_n,
        "slow": slow_n,
        "min_score": min_score if scored_n else 0,
        "avg_score": round(scores_sum / max(1, scored_n), 4),
        "all_pass": all_pass,
        "elapsed_sec": round(elapsed, 1),
        "fail_entry_nos": [f.get("entry_no") for f in remain[:100]],
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"round_{round_no:02d}.json").write_text(
        json.dumps({"summary": summary, "fails": remain[:200], "first_fails": fails[:200]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _log(
        f"=== ROUND {round_no} DONE all_pass={all_pass} "
        f"first_fail={len(fails)} remain={len(remain)} fixed={fixed_n} "
        f"min={summary['min_score']} {elapsed:.1f}s ==="
    )
    return {"summary": summary, "fails": remain}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=30)
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--start-round", type=int, default=1)
    args = ap.parse_args()

    paths = list_paper_paths()
    if not paths:
        _log("NO papers found")
        return 1
    _log(f"corpus papers={len(paths)} rounds={args.rounds}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    any_unresolved = False

    for rnd in range(args.start_round, args.rounds + 1):
        result = run_round(rnd, paths, per_paper_timeout=args.timeout)
        history.append(result["summary"])
        (REPORT_DIR / "history.json").write_text(
            json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not result["summary"]["all_pass"]:
            any_unresolved = True
            _log(f"ROUND {rnd} unresolved: {result['summary'].get('fail_entry_nos')}")
            # 继续下一轮（全量再扫会再修），不中断 30 轮

    final = {
        "rounds": args.rounds,
        "passed_rounds": sum(1 for h in history if h.get("all_pass")),
        "all_30_pass": all(h.get("all_pass") for h in history) and not any_unresolved,
        "history": history,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    # 若中途有失败但最后几轮全过，以最后一轮为准再标一次
    if history and history[-1].get("all_pass") and final["passed_rounds"] >= args.rounds:
        final["all_30_pass"] = True

    (REPORT_DIR / "final_report.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# 全量 30 轮复检报告",
        "",
        f"- rounds: {args.rounds}",
        f"- passed_rounds: {final['passed_rounds']}",
        f"- all_30_pass: {final['all_30_pass']}",
        "",
    ]
    for h in history:
        lines.append(
            f"- round {h['round']}: all_pass={h.get('all_pass')} "
            f"first_fail={h.get('fail_first_pass')} remain={h.get('fail_after_fix')} "
            f"fixed={h.get('fixed')} min={h.get('min_score')} {h.get('elapsed_sec')}s"
        )
    (REPORT_DIR / "final_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log(f"FINAL all_30_pass={final['all_30_pass']} passed={final['passed_rounds']}/{args.rounds}")
    return 0 if final["all_30_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
