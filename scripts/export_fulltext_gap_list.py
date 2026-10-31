# -*- coding: utf-8 -*-
"""
导出 Round2 全文缺口清单，并标注建议获取渠道（VPN / 科研通 / 等待 / 可跳过）。

用法：
  python -m scripts.export_fulltext_gap_list
  python -m scripts.export_fulltext_gap_list --merge-first
  python -m scripts.export_fulltext_gap_list --priority P0
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from rag.dedup_ris_index import load_dedup_ris_index, lookup_dedup_ris
from rag.fulltext_cascade import normalize_doi, resolve_ids_from_ris_meta
from scripts.yes_md_pdf_test_report import _find_yes_md, iter_yes_md_entries

OUT_ROOT = ROOT / "zebrafish_package" / "结果" / "fulltext_round2"
ROUND1_JSONL = ROOT / "zebrafish_package" / "结果" / "xml_full_batch" / "results.jsonl"
JSONL_PATH = OUT_ROOT / "results.jsonl"
EXPORT_DIR = ROOT / "zebrafish_package" / "结果"

# DOI 前缀 → 出版社（用于 VPN 优先）
VPN_DOI_PREFIXES: dict[str, str] = {
    "10.1016": "Elsevier",
    "10.1007": "Springer",
    "10.1186": "BioMed Central/Springer",
    "10.1038": "Nature",
    "10.1002": "Wiley",
    "10.1111": "Wiley",
    "10.1093": "Oxford",
    "10.1080": "Taylor & Francis",
    "10.1083": "Rockefeller/RUP",
    "10.1242": "Company of Biologists",
    "10.1089": "Mary Ann Liebert",
    "10.1021": "ACS",
    "10.1073": "PNAS",
    "10.1126": "Science/AAAS",
}

# 常见 OA 出版社（Round2 已尝试，缺口可优先科研通）
OA_DOI_PREFIXES = {"10.1371", "10.7554", "10.3389", "10.3390", "10.1039"}


def _all_jsonl_paths() -> list[Path]:
    paths = sorted(OUT_ROOT.glob("results_w*.jsonl"))
    if JSONL_PATH.is_file():
        paths.append(JSONL_PATH)
    return paths


def _load_round1_fulltext() -> set[str]:
    done: set[str] = set()
    if not ROUND1_JSONL.is_file():
        return done
    with ROUND1_JSONL.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("xml_type") == "fulltext" and int(r.get("text_chars") or 0) >= 3000:
                done.add(str(r.get("entry_no")))
    return done


def _load_round2_fulltext() -> dict[str, dict[str, Any]]:
    by_no: dict[str, dict[str, Any]] = {}
    for p in _all_jsonl_paths():
        if p == JSONL_PATH and list(OUT_ROOT.glob("results_w*.jsonl")):
            continue
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                no = str(r.get("entry_no") or "")
                if not no:
                    continue
                ct = r.get("content_type") or ""
                if r.get("status") == "ok" and ct in ("xml", "pdf") and int(r.get("text_chars") or 0) >= 500:
                    by_no[no] = r
    return by_no


def _load_round2_done() -> set[str]:
    done: set[str] = set()
    for p in OUT_ROOT.glob("progress_w*.json"):
        try:
            state = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        done.update(str(x) for x in (state.get("done_entry_nos") or []))
    if JSONL_PATH.is_file():
        with JSONL_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line.strip())
                    if r.get("entry_no"):
                        done.add(str(r["entry_no"]))
                except json.JSONDecodeError:
                    continue
    return done


def _guess_publisher(doi: str) -> str:
    doi = normalize_doi(doi)
    if not doi:
        return ""
    for prefix, name in VPN_DOI_PREFIXES.items():
        if doi.startswith(prefix):
            return name
    return ""


def _has_real_en_abstract(entry: dict[str, str]) -> bool:
    ab = (entry.get("EN Abstract") or "").strip()
    if not ab or ab in ("无摘要", "無摘要", "N/A"):
        return False
    if "由标题提炼" in (entry.get("ZH Abstract") or "") and len(ab) < 80:
        return False
    return len(ab) >= 80


def _priority(entry: dict[str, str], *, has_doi: bool) -> str:
    judgment = (entry.get("Judgment") or "").strip()
    if judgment != "是":
        return "P3"
    if has_doi and _has_real_en_abstract(entry):
        return "P0"
    if has_doi:
        return "P1"
    return "P2"


def _recommend_action(
    *,
    has_fulltext: bool,
    round2_done: bool,
    doi: str,
    publisher: str,
    priority: str,
) -> tuple[str, str]:
    if has_fulltext:
        return "done", "已有 XML/PDF 全文"
    if not round2_done:
        return "wait_round2", "Round2 尚未处理，等待 OA 级联"
    if not doi:
        if priority in ("P0", "P1"):
            return "skip_or_manual", "无 DOI：先查 RIS/标题补 ID，或保留摘要层"
        return "skip", "无 DOI 且低优先级：保留摘要层即可"

    prefix = doi.split("/", 1)[0] if "/" in doi else doi
    if publisher or prefix in VPN_DOI_PREFIXES:
        return "vpn", f"湖南师大 VPN → {publisher or VPN_DOI_PREFIXES.get(prefix, '出版社库')}"
    if prefix in OA_DOI_PREFIXES:
        return "ablesci", "OA 刊仍缺全文：科研通 DOI 求助"
    if priority == "P0":
        return "vpn_then_ablesci", "先试 VPN，失败再科研通（P0 核心）"
    return "ablesci", "科研通 DOI 求助（10–20 积分）"


def _row(entry: dict[str, str], ris_idx: dict[str, dict[str, str]], ctx: dict[str, Any]) -> dict[str, str]:
    no = entry.get("entry_no", "")
    title = entry.get("EN Title", "")
    ris_meta = lookup_dedup_ris(title, ris_idx)
    ids = resolve_ids_from_ris_meta(ris_meta, title=title, enrich=False)
    doi = normalize_doi(ids.get("doi") or ris_meta.get("doi") or "")
    pmid = (ids.get("pmid") or "").strip()
    publisher = _guess_publisher(doi)
    journal = (ris_meta.get("journal") or "").strip()

    r2 = ctx["round2"].get(no)
    has_fulltext = no in ctx["round1_full"] or bool(r2)
    round2_done = no in ctx["round2_done"]
    pri = _priority(entry, has_doi=bool(doi))
    action, action_note = _recommend_action(
        has_fulltext=has_fulltext,
        round2_done=round2_done,
        doi=doi,
        publisher=publisher,
        priority=pri,
    )

    fulltext_source = ""
    if r2:
        fulltext_source = f"round2:{r2.get('content_type')}/{r2.get('source_step')}"
    elif no in ctx["round1_full"]:
        fulltext_source = "round1:jats_fulltext"

    return {
        "entry_no": no,
        "priority": pri,
        "judgment": (entry.get("Judgment") or "").strip(),
        "en_title": title,
        "doi": doi,
        "pmid": pmid,
        "publisher": publisher,
        "journal": journal,
        "has_fulltext": "yes" if has_fulltext else "no",
        "round2_done": "yes" if round2_done else "no",
        "fulltext_source": fulltext_source,
        "action": action,
        "action_note": action_note,
        "ablesci_url": f"https://www.ablesci.com/assist/create?doi={doi}" if doi else "",
        "vpn_hint": publisher or ("ScienceDirect/Wiley/Springer" if doi else ""),
    }


def export_gap_list(*, merge_first: bool = False, priority_filter: str = "") -> Path:
    if merge_first:
        from scripts.yes_md_fulltext_round2 import merge_worker_jsonl

        n = merge_worker_jsonl()
        safe_print(f"已合并 Round2 分片：{n} 篇")

    yes_md = _find_yes_md()
    ris_idx = load_dedup_ris_index()
    ctx = {
        "round1_full": _load_round1_fulltext(),
        "round2": _load_round2_fulltext(),
        "round2_done": _load_round2_done(),
    }

    rows: list[dict[str, str]] = []
    for entry in iter_yes_md_entries(yes_md, limit=0):
        rows.append(_row(entry, ris_idx, ctx))

    if priority_filter:
        rows = [r for r in rows if r["priority"] == priority_filter.upper()]

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    csv_all = EXPORT_DIR / f"fulltext_gap_list_{ts}.csv"
    csv_gaps = EXPORT_DIR / f"fulltext_gap_action_{ts}.csv"
    csv_p0 = EXPORT_DIR / f"fulltext_gap_P0_{ts}.csv"
    summary_json = EXPORT_DIR / f"fulltext_gap_summary_{ts}.json"
    summary_md = EXPORT_DIR / f"fulltext_gap_summary_{ts}.md"

    fieldnames = list(rows[0].keys()) if rows else []
    with csv_all.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    gap_rows = [r for r in rows if r["action"] not in ("done", "wait_round2")]
    gap_rows.sort(key=lambda r: (r["priority"], int(r["entry_no"])))
    with csv_gaps.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(gap_rows)

    p0_rows = [r for r in gap_rows if r["priority"] == "P0"]
    with csv_p0.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(p0_rows)

    by_action = Counter(r["action"] for r in rows)
    by_pri = Counter(r["priority"] for r in rows)
    has_ft = sum(1 for r in rows if r["has_fulltext"] == "yes")
    stats = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(rows),
        "has_fulltext": has_ft,
        "wait_round2": by_action.get("wait_round2", 0),
        "gap_actionable": len(gap_rows),
        "gap_p0": len(p0_rows),
        "by_action": dict(by_action),
        "by_priority": dict(by_pri),
        "files": {
            "all": str(csv_all),
            "gaps": str(csv_gaps),
            "p0": str(csv_p0),
        },
    }
    summary_json.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# 全文缺口清单汇总",
        "",
        f"- **生成时间**：{stats['generated_at']}",
        f"- **总条目**：{stats['total']}",
        f"- **已有全文**：{stats['has_fulltext']}",
        f"- **等待 Round2**：{stats['wait_round2']}",
        f"- **待人工/actionable 缺口**：{stats['gap_actionable']}",
        f"- **P0 核心缺口**：{stats['gap_p0']}",
        "",
        "## 建议渠道分布",
        "",
    ]
    for action, cnt in sorted(by_action.items(), key=lambda x: -x[1]):
        md_lines.append(f"- `{action}`：{cnt}")
    md_lines.extend([
        "",
        "## 输出文件",
        "",
        f"- 全量：`{csv_all.name}`",
        f"- 缺口（不含已完成/等待）：`{csv_gaps.name}`",
        f"- P0 优先：`{csv_p0.name}`",
        "",
        "## action 说明",
        "",
        "| action | 含义 |",
        "|--------|------|",
        "| `done` | 已有全文 |",
        "| `wait_round2` | 等 OA 脚本跑完 |",
        "| `vpn` | 湖南师大 VPN 下 PDF |",
        "| `ablesci` | 科研通 DOI 求助 |",
        "| `vpn_then_ablesci` | 先 VPN，再科研通 |",
        "| `skip` | 低优先级，保留摘要层 |",
        "| `skip_or_manual` | 无 DOI，可手动补 ID 或跳过 |",
    ])
    summary_md.write_text("\n".join(md_lines), encoding="utf-8", newline="\n")

    # 固定最新副本便于引用
    for src, name in [
        (csv_gaps, "fulltext_gap_action.csv"),
        (csv_p0, "fulltext_gap_P0.csv"),
        (summary_md, "fulltext_gap_summary.md"),
    ]:
        (EXPORT_DIR / name).write_text(src.read_text(encoding="utf-8-sig" if str(src).endswith(".csv") else "utf-8"), encoding="utf-8-sig" if str(src).endswith(".csv") else "utf-8")

    safe_print(f"全量 {len(rows)} 条 → {csv_all.name}")
    safe_print(f"缺口 {len(gap_rows)} 条 → {csv_gaps.name}")
    safe_print(f"P0 {len(p0_rows)} 条 → {csv_p0.name}")
    safe_print(f"已有全文 {has_ft} | 等待Round2 {stats['wait_round2']} | 汇总 → fulltext_gap_summary.md")
    return csv_gaps


def main() -> int:
    parser = argparse.ArgumentParser(description="导出全文缺口 CSV 及渠道建议")
    parser.add_argument("--merge-first", action="store_true", help="导出前先合并 Round2 worker 分片")
    parser.add_argument("--priority", default="", help="仅导出指定优先级，如 P0")
    args = parser.parse_args()
    export_gap_list(merge_first=args.merge_first, priority_filter=args.priority)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
