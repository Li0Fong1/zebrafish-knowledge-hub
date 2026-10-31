# -*- coding: utf-8 -*-
"""导出 PubMed 全库判别效果演示文档。"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from mysql_client import MySQLClient
from sqlalchemy import text

OUT = ROOT / "data" / "literature" / "pubmed_judgement_demo.md"

MODE_DESC = {
    "hybrid": "规则高置信 + LLM 一句话总结",
    "llm": "灰色地带完整 LLM 判别",
    "meta": "XML 结构性问题，不经 LLM",
    "rule": "规则降级",
}


def main() -> int:
    client = MySQLClient()
    db = next(client.get_db())
    try:
        stats = dict(
            db.execute(
                text("SELECT judgement, COUNT(*) FROM literature_pubmed_judgement GROUP BY judgement")
            ).fetchall()
        )
        mode_stats = db.execute(
            text("SELECT mode, COUNT(*) FROM literature_pubmed_judgement GROUP BY mode")
        ).fetchall()

        queries = [
            ("SELECT pmid, title_zh, title_en, abstract_zh, judgement, reason, mode "
             "FROM literature_pubmed_judgement WHERE judgement='是' AND mode='hybrid' "
             "AND abstract_zh LIKE '%zebrafish%' ORDER BY judged_at DESC LIMIT 2"),
            ("SELECT pmid, title_zh, title_en, abstract_zh, judgement, reason, mode "
             "FROM literature_pubmed_judgement WHERE judgement='是' AND mode='llm' "
             "ORDER BY judged_at DESC LIMIT 2"),
            ("SELECT pmid, title_zh, title_en, abstract_zh, judgement, reason, mode "
             "FROM literature_pubmed_judgement WHERE judgement='否' AND mode='hybrid' "
             "ORDER BY judged_at DESC LIMIT 2"),
            ("SELECT pmid, title_zh, title_en, abstract_zh, judgement, reason, mode "
             "FROM literature_pubmed_judgement WHERE judgement='否' AND mode='llm' "
             "ORDER BY RAND() LIMIT 1"),
            ("SELECT pmid, title_zh, title_en, abstract_zh, judgement, reason, mode "
             "FROM literature_pubmed_judgement WHERE judgement='无法判别' AND mode='meta' "
             "ORDER BY judged_at DESC LIMIT 2"),
            ("SELECT pmid, title_zh, title_en, abstract_zh, judgement, reason, mode "
             "FROM literature_pubmed_judgement WHERE judgement='否' "
             "AND (title_en LIKE '%zebrafish%' OR title_en LIKE '%Danio rerio%') "
             "ORDER BY RAND() LIMIT 1"),
        ]
        seen: set[str] = set()
        samples = []
        for q in queries:
            for row in db.execute(text(q)).fetchall():
                if row[0] not in seen:
                    seen.add(row[0])
                    samples.append(row)
    finally:
        db.close()

    yes_n = int(stats.get("是", 0))
    no_n = int(stats.get("否", 0))
    unk_n = int(stats.get("无法判别", 0))
    total = yes_n + no_n + unk_n
    rejected = [r[0] for r in samples if r[4] in ("否", "无法判别")]

    lines = [
        "# PubMed 全库斑马鱼研究对象判别 — 效果演示",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}  |  "
        f"数据源：`literature_pubmed_judgement`（全库 {total:,} 篇已完成）",
        "",
        "---",
        "",
        "## 判别结果统计",
        "",
        "| 类别 | 数量 | 占比 |",
        "|------|------|------|",
        f"| 是 | {yes_n:,} | {100 * yes_n / total:.1f}% |",
        f"| 否 | {no_n:,} | {100 * no_n / total:.1f}% |",
        f"| 无法判别 | {unk_n:,} | {100 * unk_n / total:.1f}% |",
        f"| **合计** | **{total:,}** | 100% |",
        "",
        "### 处理模式分布",
        "",
        "| mode | 含义 | 数量 |",
        "|------|------|------|",
    ]
    for m, c in sorted(mode_stats, key=lambda x: -x[1]):
        lines.append(f"| {m} | {MODE_DESC.get(m, m)} | {c:,} |")

    lines += [
        "",
        "---",
        "",
        "## 字段说明",
        "",
        "| 字段 | 说明 |",
        "|------|------|",
        "| title_zh | 中文译名 |",
        "| abstract_zh | 一句话中文研究摘要（非全文翻译） |",
        "| 判断结果 | 是 / 否 / 无法判别 |",
        "",
        "---",
        "",
        "## 否或无法判别的文章编号（本演示样例）",
        "",
        ", ".join(rejected) if rejected else "（无）",
        "",
    ]

    for pmid, tz, te, az, judgement, reason, mode in samples:
        title = (tz or te or "（无标题）").strip()
        summary = (az or "无摘要").strip()
        lines += [
            "---",
            "",
            f"## {pmid}",
            "",
            f"标题:{title}",
            "",
            f"摘要:{summary}",
            "",
            f"判断结果:{judgement}",
            "",
            f"*mode={mode} · 理由：{reason}*",
            "",
        ]

    lines += [
        "---",
        "",
        "*以上为从全库中抽取的演示样例，完整数据见 MySQL 表 `literature_pubmed_judgement`。*",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入：{OUT}")
    print(f"样例篇数：{len(samples)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
