# -*- coding: utf-8 -*-

"""

对合并结果中「无法判别」条目，按 PubMed 全库判别标准（混合规则 + LLM 灰区）重新判别。



输入/输出：项目阶段/05_数据与库/归档/data_export/literature_judgement_merged/

"""

from __future__ import annotations



import csv

import re

import sys

import zipfile

from collections import Counter

from datetime import datetime

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))



from core.entry import bootstrap_app



bootstrap_app()



from mysql_client import MySQLClient

from sqlalchemy import text



from scripts.judge_hybrid_triage import INVENTORY_HYBRID, triage_zebrafish

from scripts.judge_literature_zebrafish_batch import (

    INVENTORY_ECONOMY,

    INVENTORY_REASON_MAX,

    _llm_judge,

    _llm_judge_inventory,

    _thread_llm,

)

from scripts.judge_pubmed_inventory import _parse_pubmed

from scripts.resolve_merged_judgement_conflicts import COLS



OUT_DIR = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "literature_judgement_merged"

MERGED = OUT_DIR / "literature_judgement_merged.csv"

REJUDGE_LOG = OUT_DIR / "literature_judgement_rejudge_unknown.csv"

SUMMARY = OUT_DIR / "rejudge_unknown_summary.md"



NO_ABS = {"", "无摘要", "No abstract", "no abstract"}





def _clean(s: str) -> str:

    return (s or "").strip()





def _pick_text(row: dict) -> tuple[str, str]:

    title = _clean(row.get("英文标题")) or _clean(row.get("中文标题"))

    abstract = _clean(row.get("英文摘要")) or _clean(row.get("中文摘要"))

    if abstract in NO_ABS:

        abstract = ""

    return title, abstract





def _load_pubmed_xml(client: MySQLClient, pmids: list[str]) -> dict[str, tuple[str, str]]:

    if not pmids:

        return {}

    out: dict[str, tuple[str, str]] = {}

    db = next(client.get_db())

    try:

        chunk = 200

        for i in range(0, len(pmids), chunk):

            part = pmids[i : i + chunk]

            placeholders = ", ".join(f":p{j}" for j in range(len(part)))

            params = {f"p{j}": p for j, p in enumerate(part)}

            rows = db.execute(

                text(f"SELECT pmid, raw_xml FROM literature_pubmed WHERE pmid IN ({placeholders})"),

                params,

            ).fetchall()

            for pmid, raw in rows:

                if not raw:

                    continue

                try:

                    title, abstract = _parse_pubmed(raw)

                    if title or abstract:

                        out[str(pmid)] = (title, abstract)

                except Exception:

                    pass

    finally:

        db.close()

    return out





def _judge_gray(title: str, abstract: str) -> tuple[str, str, str]:

    llm = _thread_llm()

    judge_fn = _llm_judge_inventory if INVENTORY_ECONOMY else _llm_judge

    title_zh, abstract_zh, judgement, reason = judge_fn(title, abstract, llm=llm)

    if judgement not in ("是", "否", "无法判别"):

        judgement = "无法判别"

    return judgement, (reason or "")[:INVENTORY_REASON_MAX], "llm"





def _write_csv(path: Path, rows: list[dict], cols: tuple[str, ...]) -> None:

    with open(path, "w", encoding="utf-8-sig", newline="") as f:

        w = csv.DictWriter(f, fieldnames=cols)

        w.writeheader()

        w.writerows(rows)





def _write_summary(

    path: Path,

    before: Counter,

    after: Counter,

    changes: Counter,

    mode_stats: Counter,

    still_unknown: int,

    log_count: int,

) -> None:

    resolved = before["无法判别"] - after["无法判别"]

    lines = [

        "# 「无法判别」重新判别总结",

        "",

        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",

        "",

        "## 判别标准",

        "",

        "与 PubMed 全库判别一致：",

        f"- 混合规则预筛（JUDGE_INVENTORY_HYBRID={'开启' if INVENTORY_HYBRID else '关闭'}）",

        "- `clear_yes` / `clear_no`：规则直接给出「是/否」",

        "- `gray`：调用 LLM 完整判别（economy 模式）",

        "",

        "## 处理前后对比",

        "",

        "| 判断结果 | 处理前 | 处理后 | 变化 |",

        "|----------|--------|--------|------|",

    ]

    for j in ("是", "否", "无法判别"):

        b, a = before[j], after[j]

        lines.append(f"| {j} | {b:,} | {a:,} | {a - b:+,} |")

    lines.extend([

        "",

        f"- 原「无法判别」：**{before['无法判别']:,}** 篇",

        f"- 已解析为「是/否」：**{resolved:,}** 篇",

        f"- 仍为「无法判别」：**{still_unknown:,}** 篇",

        "",

        "## 重新判别明细",

        "",

        f"| 新结果 | 数量 |",

        f"|--------|------|",

    ])

    for j in ("是", "否", "无法判别"):

        if changes[j]:

            lines.append(f"| → {j} | {changes[j]:,} |")

    lines.extend([

        "",

        "## 处理模式",

        "",

        f"| 模式 | 数量 |",

        f"|------|------|",

    ])

    for mode, n in sorted(mode_stats.items(), key=lambda x: -x[1]):

        lines.append(f"| {mode} | {n:,} |")

    lines.extend([

        "",

        "## 输出文件",

        "",

        "- `literature_judgement_merged.csv` — 更新后的全量合并结果",

        "- `literature_judgement_是.csv` / `否.csv` / `无法判别.csv`",

        "- `literature_judgement_merged.zip` — 打包上交",

        f"- `literature_judgement_rejudge_unknown.csv` — 重新判别明细（{log_count:,} 条）",

        "",

    ])

    path.write_text("\n".join(lines), encoding="utf-8")





def main() -> int:

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    client = MySQLClient()



    rows: list[dict] = []

    unknown_idx: list[int] = []

    pmids_to_fetch: list[str] = []



    with open(MERGED, encoding="utf-8-sig", newline="") as f:

        for r in csv.DictReader(f):

            row = {k: r.get(k, "") for k in COLS}

            rows.append(row)

            if row.get("判断结果") == "无法判别":

                unknown_idx.append(len(rows) - 1)

                pmid = _clean(row.get("PMID"))

                if pmid:

                    pmids_to_fetch.append(pmid)



    before = Counter(r.get("判断结果", "") for r in rows)

    print(f"读取 {len(rows):,} 条，无法判别 {len(unknown_idx):,} 条")



    pubmed_text = _load_pubmed_xml(client, sorted(set(pmids_to_fetch)))

    print(f"  MySQL 补全标题/摘要: {len(pubmed_text):,} 条")



    log_rows: list[dict] = []

    changes = Counter()

    mode_stats = Counter()



    for idx in unknown_idx:

        row = rows[idx]

        old_j = row["判断结果"]

        old_reason = row.get("理由", "")

        title, abstract = _pick_text(row)



        pmid = _clean(row.get("PMID"))

        enriched = False

        if pmid and pmid in pubmed_text:

            t2, a2 = pubmed_text[pmid]

            if len(t2) > len(title):

                title = t2

                enriched = True

            if len(a2) > len(abstract):

                abstract = a2

                enriched = True

            if enriched:

                row["英文标题"] = t2 or row.get("英文标题", "")

                if a2:

                    row["英文摘要"] = a2



        if not title and not abstract:

            new_j = "无法判别"

            new_reason = "标题与摘要均为空，仍无法判别"

            mode = "meta"

        else:

            tier, pre_j, rule_reason = triage_zebrafish(title, abstract)

            if tier in ("clear_yes", "clear_no") and pre_j:

                new_j = pre_j

                new_reason = f"[重新判别] {rule_reason or ''}".strip()

                mode = "hybrid"

            else:

                new_j, llm_reason, mode = _judge_gray(title, abstract)

                new_reason = f"[重新判别] {llm_reason or ''}".strip()



        if new_j not in ("是", "否", "无法判别"):

            new_j = "无法判别"



        row["判断结果"] = new_j

        row["理由"] = new_reason

        row["处理模式"] = mode

        row["判别时间"] = datetime.now().strftime("%Y-%m-%d %H:%M")



        changes[new_j] += 1

        mode_stats[mode] += 1

        log_rows.append({

            "文献ID": row.get("文献ID", ""),

            "PMID": row.get("PMID", ""),

            "数据源": row.get("数据源", ""),

            "原判断结果": old_j,

            "新判断结果": new_j,

            "处理模式": mode,

            "MySQL补全": "是" if enriched else "否",

            "英文标题": title[:200],

            "原理由": old_reason[:300],

            "新理由": new_reason[:300],

        })



        if (len(log_rows) % 200) == 0:

            print(f"  已处理 {len(log_rows):,}/{len(unknown_idx):,}…")



    after = Counter(r.get("判断结果", "") for r in rows)

    still_unknown = after["无法判别"]



    _write_csv(MERGED, rows, COLS)

    log_cols = list(log_rows[0].keys()) if log_rows else []

    if log_rows:

        _write_csv(REJUDGE_LOG, log_rows, tuple(log_cols))



    for j in ("是", "否", "无法判别"):

        part = [r for r in rows if r.get("判断结果") == j]

        _write_csv(OUT_DIR / f"literature_judgement_{j}.csv", part, COLS)

        print(f"  {j}: {len(part):,}")



    _write_summary(SUMMARY, before, after, changes, mode_stats, still_unknown, len(log_rows))



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

            "3. 原「无法判别」条目已按 PubMed 判别标准重新判别（见 `rejudge_unknown_summary.md`）",

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

            "- `literature_judgement_rejudge_unknown.csv` — 重新判别明细",

            "- `rejudge_unknown_summary.md` — 重新判别总结",

            "- `literature_judgement_merged.zip` — 打包上交",

        ]),

        encoding="utf-8",

    )



    zip_path = OUT_DIR / "literature_judgement_merged.zip"

    names = [

        "README.md",

        "rejudge_unknown_summary.md",

        "literature_judgement_merged.csv",

        "literature_judgement_conflicts.csv",

        "literature_judgement_rejudge_unknown.csv",

        "literature_judgement_是.csv",

        "literature_judgement_否.csv",

        "literature_judgement_无法判别.csv",

    ]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:

        for name in names:

            p = OUT_DIR / name

            if p.is_file():

                zf.write(p, arcname=name)



    print(f"\n完成 → {OUT_DIR}")

    print(f"  无法判别: {before['无法判别']:,} → {after['无法判别']:,}")

    print(f"  是: {before['是']:,} → {after['是']:,}")

    print(f"  否: {before['否']:,} → {after['否']:,}")

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

