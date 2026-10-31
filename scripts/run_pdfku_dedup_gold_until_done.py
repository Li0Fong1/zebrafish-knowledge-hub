# -*- coding: utf-8 -*-
"""
pdf库：去重后 → 金标战役 → 测试循环 → 导出实在达不到的清单。

用法：
  .\\.venv\\Scripts\\python.exe -u -m scripts.run_pdfku_dedup_gold_until_done
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
OUT = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
LOG = ROOT / "logs" / "pdfku_dedup_gold_until_done.log"
FAIL_MD = ROOT / "解析文件" / "pdf库_实在达不到金标_清单.md"
FAIL_CSV = ROOT / "解析文件" / "pdf库_实在达不到金标_清单.csv"
FAIL_JSON = ROOT / "解析文件" / "pdf库_实在达不到金标_清单.json"

os.environ.setdefault("FAISS_DISABLE_AVX2", "1")


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _run(args: list[str], *, timeout: int | None = None) -> int:
    _log("RUN " + " ".join(args))
    p = subprocess.run(args, cwd=str(ROOT), timeout=timeout)
    _log(f"EXIT {p.returncode}")
    return int(p.returncode)


def _coverage() -> dict:
    from scripts.gold_hunt_fulltext import is_pdf_gold

    gold = 0
    not_gold = 0
    fails: list[dict] = []
    for p in OUT.glob("batch_*/papers/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            not_gold += 1
            fails.append({"entry_no": p.stem, "reason": "json_broken", "path": str(p)})
            continue
        if is_pdf_gold(rec):
            gold += 1
        else:
            not_gold += 1
            body = len(rec.get("body_text") or "")
            fails.append({
                "entry_no": str(rec.get("entry_no") or p.stem),
                "doi": rec.get("doi") or "",
                "title": (rec.get("title_zh") or rec.get("title_en") or "")[:200],
                "source": rec.get("source") or "",
                "gold_tier": rec.get("gold_tier") or "",
                "body_len": body,
                "score": rec.get("score"),
                "strict_issues": rec.get("strict_issues") or [],
                "reason": _reason(rec, body),
                "path": str(p),
            })
    return {"gold": gold, "not_gold": not_gold, "total": gold + not_gold, "fails": fails}


def _reason(rec: dict, body: int) -> str:
    from scripts.gold_hunt_fulltext import PDF_GOLD_BODY

    issues = rec.get("strict_issues") or []
    if body <= 0:
        return "无正文/抽取失败"
    if body < PDF_GOLD_BODY:
        return f"正文过短({body}<{PDF_GOLD_BODY})"
    if issues:
        return "strict未过:" + ";".join(str(x) for x in issues[:5])
    if (rec.get("score") or 0) < 100:
        return f"score不足({rec.get('score')})"
    src = rec.get("source") or ""
    if src not in {"pdf_align", "xml_jats_fulltext", "html_fulltext_gold"}:
        return f"来源不合格({src})"
    return "未达金标(其他)"


def _clear_blacklist() -> None:
    bl = OUT / "pdf_extract_blacklist.jsonl"
    if bl.is_file():
        bl.write_text("", encoding="utf-8")
        _log("cleared pdf_extract_blacklist")
    ur = OUT / "unreachable_oa_registry.jsonl"
    if ur.is_file():
        # 不整文件清空，留给战役；仅记录
        _log(f"unreachable_oa exists size={ur.stat().st_size}")


def _export_fails(fails: list[dict], *, note: str) -> None:
    import csv

    FAIL_JSON.write_text(
        json.dumps(
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "note": note,
                "n": len(fails),
                "by_reason": dict(Counter(f.get("reason") or "" for f in fails)),
                "items": fails,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    headers = [
        "entry_no", "doi", "title", "source", "gold_tier", "body_len",
        "score", "reason", "strict_issues", "path",
    ]
    with FAIL_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for row in fails:
            r = dict(row)
            r["strict_issues"] = ";".join(str(x) for x in (row.get("strict_issues") or []))
            w.writerow(r)

    by = Counter(f.get("reason") or "" for f in fails)
    lines = [
        "# pdf库 · 实在达不到金标清单",
        "",
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 说明：{note}",
        f"- 未达金标篇数：**{len(fails)}**",
        "",
        "## 原因分布",
        "",
    ]
    for k, v in by.most_common():
        lines.append(f"- {k}: {v}")
    lines += ["", "## 明细（前 200）", ""]
    for i, f in enumerate(fails[:200], 1):
        lines.append(
            f"{i}. entry_no=`{f.get('entry_no')}` DOI=`{f.get('doi')}` "
            f"body={f.get('body_len')} reason={f.get('reason')}"
        )
        if f.get("title"):
            lines.append(f"   标题：{f.get('title')}")
    lines += [
        "",
        f"完整 CSV：`{FAIL_CSV.name}`",
        f"完整 JSON：`{FAIL_JSON.name}`",
    ]
    FAIL_MD.write_text("\n".join(lines), encoding="utf-8")
    _log(f"exported fails n={len(fails)} -> {FAIL_MD.name}")


def _pdfku_alive() -> bool:
    try:
        import subprocess as sp

        r = sp.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "commandline"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return "gold_pdfku_library_full_gold" in (r.stdout or "")
    except Exception:
        return False


def main() -> int:
    _log("=== pdf库 去重后金标闭环启动 ===")

    # 1) 若尚未去重报告，先去重
    dedup_report = ROOT / "解析文件" / "pdf库_全量去重报告.json"
    if not dedup_report.is_file():
        _run([PY, "-u", "-m", "scripts.pdfku_dedup_merge"])
    else:
        _log(f"dedup report exists: {dedup_report}")

    # 2) 多轮：清黑名单 → 战役(若未在跑) → 统计；连续两轮金标不增则收束
    prev_gold = -1
    stall = 0
    for round_i in range(1, 9):
        _log(f"--- gold round {round_i} ---")
        _clear_blacklist()
        if _pdfku_alive():
            _log("pdfku 战役已在运行，本轮只监测+修复，不重复拉起")
            time.sleep(900)  # 等 15 分钟让战役推进
        else:
            code = _run(
                [
                    PY, "-u", "-m", "scripts.gold_pdfku_library_full_gold",
                    "--hours", "2",
                    "--workers", "2",
                    "--rounds", "4",
                    "--start-online",
                ],
                timeout=None,
            )
            _log(f"campaign_exit={code}")
        cov = _coverage()
        _log(f"coverage gold={cov['gold']} not={cov['not_gold']} total={cov['total']}")
        # 每轮都刷新达不到清单（中间态）
        _export_fails(
            cov["fails"],
            note=f"第{round_i}轮监测快照；未达={cov['not_gold']}",
        )
        if cov["not_gold"] == 0 and cov["total"] > 0:
            _export_fails([], note="全部达到金标")
            _log("=== 全部金标完成 ===")
            return 0
        if cov["gold"] <= prev_gold:
            stall += 1
            _log(f"gold stalled stall={stall}")
        else:
            stall = 0
        prev_gold = cov["gold"]
        if stall >= 2:
            _log("连续两轮无新增金标，导出达不到清单并结束")
            _export_fails(
                cov["fails"],
                note="自动战役平台期后仍未达金标；需人工补 PDF/OCR/OA",
            )
            return 0
        try:
            _run([PY, "-u", "-m", "scripts.gold_pdfku_repair_format_adapt"], timeout=3600)
        except Exception as e:
            _log(f"repair skip: {e}")
        time.sleep(2)

    cov = _coverage()
    _export_fails(cov["fails"], note="达到最大轮次后仍未全部金标")
    _log("=== 达到最大轮次，已导出达不到清单 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
