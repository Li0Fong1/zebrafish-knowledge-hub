# -*- coding: utf-8 -*-
"""导出 PubMed 全库判别 + 中文补译 最终结果摘要。"""
from __future__ import annotations

import re
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

from scripts.judge_literature_zebrafish_batch import _has_chinese

OUT = ROOT / "data" / "literature" / "pubmed_judgement_final_summary.md"

MODE_DESC = {
    "hybrid": "规则高置信 + LLM 一句话总结",
    "llm": "灰色地带完整 LLM 判别",
    "meta": "XML 结构性问题，不经 LLM",
    "rule": "规则降级",
}


def _zh_stats(db) -> dict:
    total = int(
        db.execute(text("SELECT COUNT(*) FROM literature_pubmed_judgement WHERE mode != 'meta'")).scalar()
    )
    no_title = int(
        db.execute(
            text(
                """
                SELECT COUNT(*) FROM literature_pubmed_judgement
                WHERE mode != 'meta'
                  AND title_en IS NOT NULL AND CHAR_LENGTH(title_en) > 3
                  AND (title_zh IS NULL OR title_zh = title_en OR title_zh NOT REGEXP '[一-龥]')
                """
            )
        ).scalar()
    )
    no_abs = int(
        db.execute(
            text(
                """
                SELECT COUNT(*) FROM literature_pubmed_judgement
                WHERE mode != 'meta'
                  AND abstract_en IS NOT NULL AND CHAR_LENGTH(abstract_en) > 40
                  AND (abstract_zh IS NULL OR abstract_zh NOT REGEXP '[一-龥]'
                       OR abstract_zh IN ('No abstract','无摘要',''))
                """
            )
        ).scalar()
    )
    return {
        "total_non_meta": total,
        "has_title_zh": total - no_title,
        "missing_title_zh": no_title,
        "missing_abstract_zh": no_abs,
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
        zh = _zh_stats(db)

        missing = db.execute(
            text(
                """
                SELECT pmid, title_en, title_zh, abstract_zh, judgement, mode
                FROM literature_pubmed_judgement
                WHERE mode != 'meta'
                  AND (
                    (title_en IS NOT NULL AND CHAR_LENGTH(title_en) > 3
                     AND (title_zh IS NULL OR title_zh = title_en OR title_zh NOT REGEXP '[一-龥]'))
                    OR
                    (abstract_en IS NOT NULL AND CHAR_LENGTH(abstract_en) > 40
                     AND (abstract_zh IS NULL OR abstract_zh NOT REGEXP '[一-龥]'
                          OR abstract_zh IN ('No abstract','无摘要','')))
                  )
                ORDER BY pmid+0 ASC
                LIMIT 50
                """
            )
        ).fetchall()

        sample_queries = [
            ("是", "hybrid", 3),
            ("是", "llm", 3),
            ("否", "hybrid", 2),
            ("否", "llm", 2),
            ("无法判别", "llm", 1),
        ]
        samples = []
        seen: set[str] = set()
        for judgement, mode, lim in sample_queries:
            rows = db.execute(
                text(
                    """
                    SELECT pmid, title_en, title_zh, abstract_en, abstract_zh, judgement, reason, mode
                    FROM literature_pubmed_judgement
                    WHERE judgement = :j AND mode = :m
                      AND title_zh REGEXP '[一-龥]'
                      AND abstract_zh REGEXP '[一-龥]'
                    ORDER BY judged_at DESC
                    LIMIT :lim
                    """
                ),
                {"j": judgement, "m": mode, "lim": lim},
            ).fetchall()
            for row in rows:
                if row[0] not in seen:
                    seen.add(row[0])
                    samples.append(row)
    finally:
        db.close()

    yes_n = int(stats.get("是", 0))
    no_n = int(stats.get("否", 0))
    unk_n = int(stats.get("无法判别", 0))
    total = yes_n + no_n + unk_n
    title_pct = 100 * zh["has_title_zh"] / max(zh["total_non_meta"], 1)
    done = zh["missing_title_zh"] == 0 and zh["missing_abstract_zh"] == 0

    lines = [
        "# PubMed 全库斑马鱼判别 + 中文补译 — 最终结果",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 数据表：`literature_pubmed_judgement`（共 **{total:,}** 篇）",
        "",
        "---",
        "",
        "## 一、任务完成情况",
        "",
        f"| 项目 | 结果 |",
        f"|------|------|",
        f"| 斑马鱼研究对象判别 | **已完成**（{total:,} / {total:,}） |",
        f"| 中文标题补译 | **{zh['has_title_zh']:,}** / {zh['total_non_meta']:,}（{title_pct:.2f}%） |",
        f"| 仍缺中文标题 | {zh['missing_title_zh']:,} 篇 |",
        f"| 仍缺中文摘要 | {zh['missing_abstract_zh']:,} 篇 |",
        f"| 整体状态 | {'**全部完成**' if done else '**基本完成**（少量条目待补）'} |",
        "",
        "---",
        "",
        "## 二、判别结果统计",
        "",
        "| 判断结果 | 数量 | 占比 |",
        "|----------|------|------|",
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
        "## 三、字段说明",
        "",
        "| 字段 | 说明 |",
        "|------|------|",
        "| title_en | PubMed 英文原标题 |",
        "| title_zh | 中文译名 |",
        "| abstract_zh | 一句话中文研究摘要（非全文翻译） |",
        "| judgement | 是 / 否 / 无法判别 |",
        "| reason | 判别理由 |",
        "| mode | hybrid / llm / meta |",
        "",
        "---",
        "",
        "## 四、中文样例（各类型抽取）",
        "",
    ]

    for pmid, te, tz, ae, az, judgement, reason, mode in samples:
        lines += [
            f"### {pmid}",
            "",
            f"- **中文标题**：{(tz or te or '').strip()}",
            f"- **英文标题**：{(te or '').strip()}",
            f"- **中文摘要**：{(az or '无摘要').strip()}",
            f"- **判断结果**：{judgement}",
            f"- **理由**：{reason}",
            f"- **mode**：{mode}",
            "",
        ]

    lines += [
        "---",
        "",
        "## 五、仍未中译的条目",
        "",
    ]
    if not missing:
        lines.append("（无，全部已完成中文补译。）")
    else:
        lines.append(f"共 **{len(missing)}** 条（最多展示 50 条）：")
        lines.append("")
        lines.append("| PMID | 英文标题 | 缺标题 | 缺摘要 | 判别 |")
        lines.append("|------|----------|--------|--------|------|")
        for pmid, te, tz, az, judgement, mode in missing:
            nt = not _has_chinese(tz or "") or (tz or "").strip() == (te or "").strip()
            na = not _has_chinese(az or "")
            te_short = (te or "")[:60].replace("|", " ") + ("…" if te and len(te) > 60 else "")
            lines.append(f"| {pmid} | {te_short} | {'是' if nt else ''} | {'是' if na else ''} | {judgement} |")

    lines += [
        "",
        "---",
        "",
        "*完整数据见 MySQL `literature_pubmed_judgement`；演示样例另见 `data/literature/pubmed_judgement_demo.md`。*",
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入：{OUT}")
    print(f"缺标题={zh['missing_title_zh']} 缺摘要={zh['missing_abstract_zh']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
