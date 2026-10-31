# -*- coding: utf-8 -*-

"""

导出 PubMed 全库斑马鱼判别完整结果（上交用）。



产出目录：项目阶段/05_数据与库/归档/data_export/pubmed_judgement_full/

  - pubmed_judgement_full.csv      全量 CSV（Excel 可开，UTF-8 BOM）

  - pubmed_judgement_full.md       全量 Markdown（与 zebrafish_judgement 格式一致）

  - pubmed_judgement_是.csv        按判断结果拆分（可选）

  - pubmed_judgement_否.csv

  - pubmed_judgement_无法判别.csv

  - README.md                      说明与统计



用法：python -m scripts.export_pubmed_judgement_full

"""

from __future__ import annotations



import csv

import sys

import zipfile

from datetime import datetime

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))



from core.entry import bootstrap_app



bootstrap_app()



from mysql_client import MySQLClient

from sqlalchemy import text



OUT_DIR = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "pubmed_judgement_full"



CSV_COLS = (

    "PMID",

    "英文标题",

    "中文标题",

    "英文摘要",

    "中文摘要",

    "判断结果",

    "理由",

    "处理模式",

    "判别时间",

)



SQL = """

    SELECT pmid, title_en, title_zh, abstract_en, abstract_zh,

           judgement, reason, mode, judged_at

    FROM literature_pubmed_judgement

    ORDER BY pmid+0 ASC

"""





def _row_dict(r) -> dict:

    pmid, te, tz, ae, az, j, reason, mode, judged_at = r

    ts = judged_at.strftime("%Y-%m-%d %H:%M:%S") if judged_at else ""

    return {

        "PMID": pmid,

        "英文标题": (te or "").strip(),

        "中文标题": (tz or te or "").strip(),

        "英文摘要": (ae or "").strip(),

        "中文摘要": (az or "").strip(),

        "判断结果": j,

        "理由": (reason or "").strip(),

        "处理模式": mode or "",

        "判别时间": ts,

    }





def _write_csv(path: Path, rows: list[dict]) -> None:

    with open(path, "w", encoding="utf-8-sig", newline="") as f:

        w = csv.DictWriter(f, fieldnames=CSV_COLS)

        w.writeheader()

        w.writerows(rows)





def _write_md(path: Path, rows: list[dict], stats: dict) -> None:

    rejected = [r["PMID"] for r in rows if r["判断结果"] in ("否", "无法判别")]

    lines = [

        "# 斑马鱼研究对象判别结果（PubMed 全库）",

        "",

        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",

        f"- 文献数量：{stats['total']:,}",

        "",

        "## 判别结果统计",

        "",

        "| 类别 | 数量 |",

        "|------|------|",

        f"| 是 | {stats['是']:,} |",

        f"| 否 | {stats['否']:,} |",

        f"| 无法判别 | {stats['无法判别']:,} |",

        f"| 合计 | {stats['total']:,} |",

        "",

        "## 否或无法判别的文章编号",

        "",

    ]

    # 长列表分行，每行约 20 个 PMID

    chunk: list[str] = []

    for i, pid in enumerate(rejected):

        chunk.append(pid)

        if len(chunk) >= 20:

            lines.append(", ".join(chunk))

            chunk = []

    if chunk:

        lines.append(", ".join(chunk))

    lines.append("")

    lines.append("---")

    lines.append("")



    for r in rows:

        lines += [

            f"## {r['PMID']}",

            "",

            f"标题:{r['中文标题']}",

            "",

            f"摘要:{r['中文摘要'] or '无摘要'}",

            "",

            f"判断结果:{r['判断结果']}",

            "",

            "---",

            "",

        ]



    path.write_text("\n".join(lines), encoding="utf-8")





def _write_readme(path: Path, stats: dict, files: list[str]) -> None:

    path.write_text(

        "\n".join([

            "# PubMed 斑马鱼研究对象判别 — 上交数据包",

            "",

            f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",

            "",

            "## 数据说明",

            "",

            "本目录为 PubMed 导入文献（约 67,021 篇）经斑马鱼研究对象判别后的**完整结果**。",

            "",

            "### 判别标准",

            "",

            "判断该文献是否**以斑马鱼（Danio rerio）为主要研究对象/实验模型**；",

            "仅提及、对照或背景用途 → **否**；信息不足 → **无法判别**。",

            "",

            "### 字段说明",

            "",

            "| 字段 | 说明 |",

            "|------|------|",

            "| PMID | PubMed 文献编号 |",

            "| 英文标题 / 中文标题 | 原文标题及中文译名 |",

            "| 英文摘要 / 中文摘要 | 原文摘要及一句话中文概括 |",

            "| 判断结果 | 是 / 否 / 无法判别 |",

            "| 理由 | 判别依据简述 |",

            "| 处理模式 | hybrid（规则+LLM）/ llm / meta |",

            "",

            "## 统计",

            "",

            f"- **是**：{stats['是']:,} 篇（{100 * stats['是'] / stats['total']:.1f}%）",

            f"- **否**：{stats['否']:,} 篇（{100 * stats['否'] / stats['total']:.1f}%）",

            f"- **无法判别**：{stats['无法判别']:,} 篇（{100 * stats['无法判别'] / stats['total']:.1f}%）",

            f"- **合计**：{stats['total']:,} 篇",

            "",

            "## 文件清单",

            "",

        ] + [f"- `{name}`" for name in files] + [

            "",

            "## 推荐使用",

            "",

            "- **Excel 打开**：`pubmed_judgement_full.csv`（UTF-8 BOM）",

            "- **全文检索 / 归档**：`pubmed_judgement_full.md`",

            "- **打包上交**：`pubmed_judgement_full.zip`",

            "",

            "数据源：MySQL `literature_pubmed_judgement`",

        ]),

        encoding="utf-8",

    )





def main() -> int:

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    client = MySQLClient()

    db = next(client.get_db())

    try:

        raw = db.execute(text(SQL)).fetchall()

    finally:

        db.close()



    rows = [_row_dict(r) for r in raw]

    stats = {"total": len(rows), "是": 0, "否": 0, "无法判别": 0}

    for r in rows:

        j = r["判断结果"]

        if j in stats:

            stats[j] += 1



    csv_all = OUT_DIR / "pubmed_judgement_full.csv"

    md_all = OUT_DIR / "pubmed_judgement_full.md"

    readme = OUT_DIR / "README.md"

    zip_path = OUT_DIR / "pubmed_judgement_full.zip"



    print(f"写入 CSV（{len(rows):,} 篇）…")

    _write_csv(csv_all, rows)



    by_j: dict[str, list[dict]] = {"是": [], "否": [], "无法判别": []}

    for r in rows:

        if r["判断结果"] in by_j:

            by_j[r["判断结果"]].append(r)



    split_files: list[Path] = []

    for j, part in by_j.items():

        p = OUT_DIR / f"pubmed_judgement_{j}.csv"

        _write_csv(p, part)

        split_files.append(p)

        print(f"  {j}: {len(part):,} 篇 → {p.name}")



    print(f"写入 Markdown（{len(rows):,} 篇，体积较大请稍候）…")

    _write_md(md_all, rows, stats)



    file_names = [

        "README.md",

        "pubmed_judgement_full.csv",

        "pubmed_judgement_full.md",

        "pubmed_judgement_是.csv",

        "pubmed_judgement_否.csv",

        "pubmed_judgement_无法判别.csv",

    ]

    _write_readme(readme, stats, file_names)



    print("打包 ZIP…")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:

        for name in file_names:

            p = OUT_DIR / name

            if p.is_file():

                zf.write(p, arcname=name)



    mb = zip_path.stat().st_size / 1024 / 1024

    print(f"\n完成。输出目录：{OUT_DIR}")

    print(f"  全量 CSV: {csv_all} ({csv_all.stat().st_size / 1024 / 1024:.1f} MB)")

    print(f"  全量 MD:  {md_all} ({md_all.stat().st_size / 1024 / 1024:.1f} MB)")

    print(f"  ZIP:      {zip_path} ({mb:.1f} MB)")

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

