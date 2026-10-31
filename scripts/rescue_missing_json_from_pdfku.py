# -*- coding: utf-8 -*-
"""
是表 missing_json 救援：把 pdfku_library_fulltext_gold 已有 JSON 同步进 yes_fulltext_gold。

背景：
  exist≈13585 / yes JSON≈5676 / missing≈7909；缺 JSON 几乎全部已在 pdfku 侧有产物，
  其中多数已 is_pdf_gold，但未写入是表战役目录，导致 pdf_gold≈32%。

做法（本地同步，不联网）：
  1) 对缺失 entry_no：若 pdfku 有 JSON → 复制到 yes_fulltext_gold
  2) 已是 pdf_gold 直接计入；草稿一并写入，供 hunt/rescue 继续冲击
  3) 够长但未金标的尝试 finalize_to_gold_bar（不垫文、不降门槛）

用法：
  python -u -m scripts.rescue_missing_json_from_pdfku
  python -u -m scripts.rescue_missing_json_from_pdfku --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_hunt_fulltext import (  # noqa: E402
    PDF_GOLD_BODY,
    equal_coverage_snapshot,
    finalize_to_gold_bar,
    is_pdf_gold,
)
from scripts.gold_upgrade_5680 import EXIST_CSV, load_exist  # noqa: E402

YES = ROOT / "解析文件" / "yes_fulltext_gold"
PDFKU = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
STATUS = ROOT / "解析文件" / "是表缺JSON补金标_状态.json"
STATUS_MD = ROOT / "解析文件" / "是表缺JSON补金标_状态.md"
LOG = ROOT / "解析文件" / "是表缺JSON补金标.log"


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _batch_no(eno: str) -> int:
    try:
        return int(eno) // 100 + 1
    except ValueError:
        return abs(hash(eno)) % 10_000_000 // 100 + 1


def _index(root: Path) -> dict[str, Path]:
    return {p.stem: p for p in root.glob("batch_*/papers/*.json")}


def _prefer(src: str) -> str:
    s = (src or "").strip()
    if s in ("pdf_align", "xml_jats_fulltext", "html_fulltext_gold"):
        return s
    if s.startswith("html"):
        return "html_fulltext_gold"
    if s.startswith("xml"):
        return "xml_jats_fulltext"
    if s.startswith("pdf"):
        return "pdf_align"
    return "xml_jats_fulltext" if "xml" in s else "pdf_align"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--gold-only", action="store_true", help="只同步已是 pdf_gold 的")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    rows = load_exist()
    exist = {str(r["entry_no"]) for r in rows}
    yes_idx = _index(YES)
    pdfku_idx = _index(PDFKU)
    missing = sorted(exist - set(yes_idx), key=lambda x: int(x) if x.isdigit() else 0)
    _log(
        f"START exist={len(exist)} yes_json={len(yes_idx)} pdfku_json={len(pdfku_idx)} "
        f"missing={len(missing)} dry={args.dry_run}"
    )

    stats = Counter()
    copied_gold = 0
    copied_draft = 0
    finalized = 0
    samples: list[dict] = []

    for i, eno in enumerate(missing, 1):
        if args.limit and i > args.limit:
            break
        src_path = pdfku_idx.get(eno)
        if not src_path:
            stats["no_pdfku_json"] += 1
            continue
        try:
            rec = json.loads(src_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            stats["bad_json"] += 1
            if len(samples) < 8:
                samples.append({"entry_no": eno, "error": str(exc)[:120]})
            continue

        was_gold = is_pdf_gold(rec)
        if args.gold_only and not was_gold:
            stats["skip_not_gold"] += 1
            continue

        # 够长未金标：本地 finalize（不联网）
        if (not was_gold) and len(rec.get("body_text") or "") >= PDF_GOLD_BODY:
            tip = (rec.get("title") or rec.get("title_en") or f"Entry {eno}").strip()
            prefer = _prefer(str(rec.get("source") or ""))
            try:
                neu = finalize_to_gold_bar(dict(rec), fallback_title=tip, prefer_source=prefer)
                if is_pdf_gold(neu):
                    rec = neu
                    was_gold = True
                    finalized += 1
                    stats["finalized_to_gold"] += 1
            except Exception:  # noqa: BLE001
                stats["finalize_fail"] += 1

        rec["entry_no"] = str(rec.get("entry_no") or eno)
        rec["enrich_from"] = rec.get("enrich_from") or "pdfku_library"
        rec["synced_from"] = "pdfku_library_fulltext_gold"
        rec["synced_at"] = datetime.now().isoformat(timespec="seconds")
        if was_gold:
            rec["corpus"] = "yes_fulltext_gold"
            rec["gold_tier"] = rec.get("gold_tier") or (
                "pdf_gold" if (rec.get("source") or "") == "pdf_align" else "fulltext_gold"
            )
            rec["complete"] = True

        bno = _batch_no(eno)
        dest_dir = YES / f"batch_{bno:04d}" / "papers"
        dest = dest_dir / f"{eno}.json"
        if args.dry_run:
            stats["would_copy"] += 1
            if was_gold:
                stats["would_copy_gold"] += 1
            else:
                stats["would_copy_draft"] += 1
        else:
            # 清掉其他 batch 里的同名残留
            for old in YES.glob(f"batch_*/papers/{eno}.json"):
                if old.resolve() != dest.resolve():
                    try:
                        old.unlink()
                    except OSError:
                        pass
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            if was_gold:
                copied_gold += 1
                stats["copied_gold"] += 1
            else:
                copied_draft += 1
                stats["copied_draft"] += 1
                src = (rec.get("source") or "") or "unknown"
                stats[f"draft_src:{src}"] += 1

        if i % 500 == 0:
            _log(f"progress {i}/{len(missing)} gold={copied_gold} draft={copied_draft} fin={finalized}")

    # 覆盖率快照
    cov = equal_coverage_snapshot(rows)
    EQUAL = YES / "pdf_gold_equal_status.json"
    if not args.dry_run:
        EQUAL.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")

    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "goal": "raise yes pdf_gold by syncing pdfku gold JSON into yes_fulltext_gold",
        "before_hint": {
            "exist_unique": 13585,
            "yes_json_approx": 5676,
            "missing_json_approx": 7909,
            "pct_pdf_gold_approx": 32.07,
        },
        "missing_at_start": len(missing),
        "dry_run": bool(args.dry_run),
        "gold_only": bool(args.gold_only),
        "copied_gold": copied_gold,
        "copied_draft": copied_draft,
        "finalized_to_gold": finalized,
        "stats": dict(stats),
        "equal_coverage_after": cov,
        "expected_path": (
            "1) 本脚本同步 pdfku→yes；"
            "2) gold_pdfku_library_full_gold 继续冲击本地短稿；"
            "3) gold_hunt_fulltext / gold_rescue_bad_tier 补齐草稿与坏 tier；"
            "4) 不 wipe FAISS；Hub 8080 保持。"
        ),
        "samples": samples,
    }
    STATUS.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = "\n".join(
        [
            "# 是表缺 JSON 补金标状态",
            "",
            f"- 更新: `{payload['updated_at']}`",
            f"- 启动时 missing: **{len(missing)}**",
            f"- 已同步 pdf_gold: **{copied_gold}**",
            f"- 已同步草稿: **{copied_draft}**",
            f"- finalize 升金标: **{finalized}**",
            f"- 同步后 pdf_gold: **{cov.get('pdf_gold_ok')}/{cov.get('exist_unique')}** "
            f"({cov.get('pct_pdf_gold')}%)",
            f"- missing_json 现: **{cov.get('missing_json')}**",
            f"- dry_run: `{args.dry_run}`",
            "",
            "## 预期路径",
            "",
            payload["expected_path"],
            "",
            "## stats",
            "",
            "```json",
            json.dumps(dict(stats), ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    STATUS_MD.write_text(md, encoding="utf-8")
    _log(
        f"DONE gold={copied_gold} draft={copied_draft} fin={finalized} "
        f"cov={cov.get('pdf_gold_ok')}/{cov.get('exist_unique')} ({cov.get('pct_pdf_gold')}%) "
        f"missing_json={cov.get('missing_json')}"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
