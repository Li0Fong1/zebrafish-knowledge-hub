# -*- coding: utf-8 -*-
"""
对合并结果中剩余「无法判别」条目，从 PubMed API（esummary + efetch）补拉元数据后重新判别。

用法：
  python -m scripts.rejudge_merged_unknown_api
"""
from __future__ import annotations

import csv
import sys
import time
import urllib.error
import zipfile
from collections import Counter
from datetime import datetime
from http.client import IncompleteRead
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

import xml.etree.ElementTree as ET

from rag.literature_collector import (
    _MAX_RETRIES,
    _RETRY_BACKOFF,
    _api_get,
    _loads_ncbi_json,
    _sleep,
)
from scripts.judge_hybrid_triage import INVENTORY_HYBRID, triage_zebrafish
from scripts.rejudge_merged_unknown import (
    MERGED,
    OUT_DIR,
    REJUDGE_LOG,
    SUMMARY,
    _clean,
    _judge_gray,
    _pick_text,
    _write_csv,
)
from scripts.resolve_merged_judgement_conflicts import COLS

API_LOG = OUT_DIR / "literature_judgement_rejudge_api.csv"
API_SUMMARY = OUT_DIR / "rejudge_api_summary.md"

NO_ABS = {"", "无摘要", "No abstract", "no abstract"}


def _api_get_retry(path: str, params: dict) -> str:
    last_err: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return _api_get(path, params)
        except (urllib.error.URLError, ConnectionResetError, OSError, TimeoutError, IncompleteRead) as e:
            last_err = e
            wait = _RETRY_BACKOFF * (2 ** attempt)
            print(f"  [重试] {path} ({type(e).__name__})，{wait:.0f}s 后重试 ({attempt + 1}/{_MAX_RETRIES})…")
            time.sleep(wait)
    raise last_err if last_err else RuntimeError(f"NCBI 请求失败: {path}")


def _parse_efetch_abstracts(pmids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(pmids), 40):
        batch = pmids[i : i + 40]
        xml = _api_get_retry("/efetch.fcgi", {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"})
        _sleep()
        root = ET.fromstring(xml)
        for art in root.findall(".//PubmedArticle"):
            pmid = (art.findtext(".//PMID") or "").strip()
            parts: list[str] = []
            for ab in art.findall(".//Abstract/AbstractText"):
                label = ab.get("Label") or ""
                txt = (ab.text or "").strip()
                if label and txt:
                    parts.append(f"{label}: {txt}")
                elif txt:
                    parts.append(txt)
            abstract = " ".join(parts).strip()
            if abstract:
                out[pmid] = abstract
    return out


def _parse_esummary_titles(pmids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(pmids), 20):
        batch = pmids[i : i + 20]
        data = _api_get_retry("/esummary.fcgi", {"db": "pubmed", "id": ",".join(batch), "retmode": "json"})
        _sleep()
        parsed = _loads_ncbi_json(data)
        for pid in batch:
            title = (parsed.get("result", {}).get(pid, {}).get("title") or "").strip()
            if title:
                out[pid] = title
    return out


def fetch_pubmed_metadata(pmids: list[str]) -> dict[str, dict[str, str]]:
    pmids = sorted({p for p in pmids if p})
    titles = _parse_esummary_titles(pmids)
    abstracts = _parse_efetch_abstracts(pmids)
    meta: dict[str, dict[str, str]] = {}
    for pid in pmids:
        meta[pid] = {
            "title": titles.get(pid, ""),
            "abstract": abstracts.get(pid, ""),
        }
    return meta


def _regenerate_outputs(rows: list[dict], before: Counter, after: Counter, api_log: list[dict]) -> None:
    _write_csv(MERGED, rows, COLS)
    for j in ("是", "否", "无法判别"):
        part = [r for r in rows if r.get("判断结果") == j]
        _write_csv(OUT_DIR / f"literature_judgement_{j}.csv", part, COLS)

    resolved = before["无法判别"] - after["无法判别"]
    changes = Counter(r["新判断结果"] for r in api_log)
    mode_stats = Counter(r["处理模式"] for r in api_log)

    API_SUMMARY.write_text(
        "\n".join([
            "# PubMed API 补拉元数据 — 重新判别总结",
            "",
            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 元数据补拉",
            "",
            "- 标题：`esummary.fcgi`（可补全 PubMed-not-MEDLINE 等 efetch 无标题记录）",
            "- 摘要：`efetch.fcgi` XML",
            "",
            f"- 处理条目：**{len(api_log):,}** 篇（原仍为「无法判别」）",
            f"- API 补到标题：**{sum(1 for r in api_log if r['API补标题'] == '是'):,}** 篇",
            f"- API 补到摘要：**{sum(1 for r in api_log if r['API补摘要'] == '是'):,}** 篇",
            "",
            "## 判别标准",
            "",
            "与 PubMed 全库判别一致：",
            f"- 混合规则预筛（JUDGE_INVENTORY_HYBRID={'开启' if INVENTORY_HYBRID else '关闭'}）",
            "- `clear_yes` / `clear_no`：规则直接给出「是/否」",
            "- `gray`：调用 LLM 完整判别",
            "",
            "## 处理前后对比",
            "",
            "| 判断结果 | 处理前 | 处理后 | 变化 |",
            "|----------|--------|--------|------|",
            f"| 是 | {before['是']:,} | {after['是']:,} | {after['是'] - before['是']:+,} |",
            f"| 否 | {before['否']:,} | {after['否']:,} | {after['否'] - before['否']:+,} |",
            f"| 无法判别 | {before['无法判别']:,} | {after['无法判别']:,} | {after['无法判别'] - before['无法判别']:+,} |",
            "",
            f"- 本批解析为「是/否」：**{resolved:,}** 篇",
            f"- 本批仍为「无法判别」：**{after['无法判别']:,}** 篇",
            "",
            "## 本批判别结果",
            "",
            "| 新结果 | 数量 |",
            "|--------|------|",
            f"| → 是 | {changes.get('是', 0):,} |",
            f"| → 否 | {changes.get('否', 0):,} |",
            f"| → 无法判别 | {changes.get('无法判别', 0):,} |",
            "",
            "## 处理模式",
            "",
            "| 模式 | 数量 |",
            "|------|------|",
        ] + [f"| {mode} | {n:,} |" for mode, n in sorted(mode_stats.items(), key=lambda x: -x[1])] + [
            "",
            "## 输出文件",
            "",
            "- `literature_judgement_rejudge_api.csv` — 本批明细",
            "- `literature_judgement_merged.csv` — 更新后的全量",
            "",
        ]),
        encoding="utf-8",
    )

    readme = OUT_DIR / "README.md"
    readme.write_text(
        "\n".join([
            "# PubMed + WoS 斑马鱼判别 — 合并去重完整结果（最终版）",
            "",
            f"更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 处理说明",
            "",
            "1. PubMed 与 WoS 合并去重（103,525 篇）",
            "2. 跨库冲突以 **PubMed 判别为准**（见 `literature_judgement_conflicts.csv`）",
            "3. 原「无法判别」2,037 篇已按 PubMed 标准重新判别（见 `rejudge_unknown_summary.md`）",
            "4. 剩余条目已从 PubMed API 补拉元数据并再次判别（见 `rejudge_api_summary.md`）",
            "",
            "## 最终统计",
            "",
            f"| 判断结果 | 数量 |",
            f"|----------|------|",
            f"| 是 | {after['是']:,} |",
            f"| 否 | {after['否']:,} |",
            f"| 无法判别 | {after['无法判别']:,} |",
            f"| **合计** | {len(rows):,} |",
            "",
            "## 文件",
            "",
            "- `literature_judgement_merged.csv` — 全量",
            "- `literature_judgement_conflicts.csv` — 冲突明细",
            "- `literature_judgement_rejudge_unknown.csv` — 第一轮重新判别明细",
            "- `literature_judgement_rejudge_api.csv` — API 补拉判别明细",
            "- `rejudge_unknown_summary.md` / `rejudge_api_summary.md`",
            "- `literature_judgement_merged.zip` — 打包上交",
        ]),
        encoding="utf-8",
    )

    zip_path = OUT_DIR / "literature_judgement_merged.zip"
    names = [
        "README.md",
        "rejudge_unknown_summary.md",
        "rejudge_api_summary.md",
        "literature_judgement_merged.csv",
        "literature_judgement_conflicts.csv",
        "literature_judgement_rejudge_unknown.csv",
        "literature_judgement_rejudge_api.csv",
        "literature_judgement_是.csv",
        "literature_judgement_否.csv",
        "literature_judgement_无法判别.csv",
    ]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            p = OUT_DIR / name
            if p.is_file():
                zf.write(p, arcname=name)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    target_idx: list[int] = []
    pmids: list[str] = []

    with open(MERGED, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            row = {k: r.get(k, "") for k in COLS}
            rows.append(row)
            if row.get("判断结果") == "无法判别":
                target_idx.append(len(rows) - 1)
                pmid = _clean(row.get("PMID"))
                if pmid:
                    pmids.append(pmid)

    before = Counter(r.get("判断结果", "") for r in rows)
    print(f"剩余无法判别 {len(target_idx):,} 条，PMID {len(pmids):,} 个")

    print("1/3 PubMed API 补拉元数据…")
    api_meta = fetch_pubmed_metadata(pmids)
    title_ok = sum(1 for m in api_meta.values() if m.get("title"))
    abs_ok = sum(1 for m in api_meta.values() if m.get("abstract"))
    print(f"  esummary 标题: {title_ok}/{len(pmids)}")
    print(f"  efetch 摘要: {abs_ok}/{len(pmids)}")

    print("2/3 重新判别…")
    api_log: list[dict] = []
    for idx in target_idx:
        row = rows[idx]
        old_j = row["判断结果"]
        old_reason = row.get("理由", "")
        title, abstract = _pick_text(row)
        pmid = _clean(row.get("PMID"))

        api_title = api_meta.get(pmid, {}).get("title", "") if pmid else ""
        api_abs = api_meta.get(pmid, {}).get("abstract", "") if pmid else ""
        got_title = got_abs = False

        if api_title and len(api_title) > len(title):
            title = api_title
            row["英文标题"] = api_title
            got_title = True
        if api_abs and len(api_abs) > len(abstract):
            abstract = api_abs
            row["英文摘要"] = api_abs
            got_abs = True

        if not title and not abstract:
            new_j = "无法判别"
            new_reason = "[API补拉] 标题与摘要仍不可用，无法判别"
            mode = "meta"
        else:
            tier, pre_j, rule_reason = triage_zebrafish(title, abstract)
            if tier in ("clear_yes", "clear_no") and pre_j:
                new_j = pre_j
                new_reason = f"[API补拉] {rule_reason or ''}".strip()
                mode = "hybrid"
            else:
                new_j, llm_reason, mode = _judge_gray(title, abstract)
                new_reason = f"[API补拉] {llm_reason or ''}".strip()

        if new_j not in ("是", "否", "无法判别"):
            new_j = "无法判别"

        row["判断结果"] = new_j
        row["理由"] = new_reason
        row["处理模式"] = mode
        row["判别时间"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        api_log.append({
            "文献ID": row.get("文献ID", ""),
            "PMID": pmid,
            "数据源": row.get("数据源", ""),
            "原判断结果": old_j,
            "新判断结果": new_j,
            "处理模式": mode,
            "API补标题": "是" if got_title else "否",
            "API补摘要": "是" if got_abs else "否",
            "英文标题": title[:200],
            "原理由": old_reason[:300],
            "新理由": new_reason[:300],
        })

    after = Counter(r.get("判断结果", "") for r in rows)
    print(f"  无法判别: {before['无法判别']:,} → {after['无法判别']:,}")

    print("3/3 写入文件…")
    if api_log:
        _write_csv(API_LOG, api_log, tuple(api_log[0].keys()))
    _regenerate_outputs(rows, before, after, api_log)

    for j in ("是", "否", "无法判别"):
        print(f"  {j}: {after[j]:,}")

    print(f"\n完成 → {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
