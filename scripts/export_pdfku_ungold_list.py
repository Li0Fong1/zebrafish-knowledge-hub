# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.entry import bootstrap_app

bootstrap_app()

from scripts.gold_hunt_fulltext import PDF_GOLD_BODY, is_pdf_gold

OUT = ROOT / "解析文件" / "pdfku_library_fulltext_gold"
FAIL_MD = ROOT / "解析文件" / "pdf库_实在达不到金标_清单.md"
FAIL_CSV = ROOT / "解析文件" / "pdf库_实在达不到金标_清单.csv"
FAIL_JSON = ROOT / "解析文件" / "pdf库_实在达不到金标_清单.json"


def reason_of(rec: dict, body: int) -> str:
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


def main() -> int:
    fails: list[dict] = []
    gold = 0
    for p in OUT.glob("batch_*/papers/*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            fails.append({
                "entry_no": p.stem, "doi": "", "title": "", "source": "",
                "gold_tier": "", "body_len": 0, "score": "", "reason": "json_broken",
                "strict_issues": "", "path": str(p),
            })
            continue
        if is_pdf_gold(rec):
            gold += 1
            continue
        body = len(rec.get("body_text") or "")
        issues = rec.get("strict_issues") or []
        fails.append({
            "entry_no": str(rec.get("entry_no") or p.stem),
            "doi": rec.get("doi") or "",
            "title": (rec.get("title_zh") or rec.get("title_en") or "")[:200],
            "source": rec.get("source") or "",
            "gold_tier": rec.get("gold_tier") or "",
            "body_len": body,
            "score": rec.get("score"),
            "reason": reason_of(rec, body),
            "strict_issues": ";".join(str(x) for x in issues[:8]),
            "path": str(p),
        })

    by = Counter(f["reason"] for f in fails)
    note = "去重完成后当前未达金标快照；战役/监测仍在跑，清单会更新"
    FAIL_JSON.write_text(
        json.dumps(
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "note": note,
                "gold": gold,
                "n": len(fails),
                "by_reason": dict(by),
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
        w.writerows(fails)

    lines = [
        "# pdf库 · 实在达不到金标清单",
        "",
        f"- 时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 说明：{note}",
        f"- 已金标：{gold}",
        f"- 未达金标篇数：**{len(fails)}**",
        "",
        "## 原因分布",
        "",
    ]
    for k, v in by.most_common():
        lines.append(f"- {k}: {v}")
    lines += ["", "## 明细（前 300）", ""]
    for i, f in enumerate(fails[:300], 1):
        lines.append(
            f"{i}. entry_no=`{f['entry_no']}` DOI=`{f['doi']}` "
            f"body={f['body_len']} reason={f['reason']}"
        )
        if f.get("title"):
            lines.append(f"   标题：{f['title']}")
    lines += [
        "",
        f"完整 CSV：`{FAIL_CSV.name}`",
        f"完整 JSON：`{FAIL_JSON.name}`",
    ]
    FAIL_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"gold={gold} fails={len(fails)}")
    print("by_reason", dict(by.most_common(15)))
    print("wrote", FAIL_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
