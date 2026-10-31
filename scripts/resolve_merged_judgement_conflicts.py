# -*- coding: utf-8 -*-
"""
处理 PubMed+WoS 合并结果中的判别冲突。

规则：**以 PubMed 判别为准**（WoS 仅作参考，冲突时不覆盖 PubMed）。
"""
from __future__ import annotations

import csv
import re
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MERGED = ROOT / "项目阶段/05_数据与库/归档/data_export/literature_judgement_merged/literature_judgement_merged.csv"
PUBMED_CSV = ROOT / "项目阶段/05_数据与库/归档/data_export/pubmed_judgement_full/pubmed_judgement_full.csv"
WOS_MD = ROOT / "项目阶段/05_数据与库/归档/data_export/merged_judgement_full/_src/wos/result_95000.md"
OUT_DIR = ROOT / "项目阶段/05_数据与库/归档/data_export/literature_judgement_merged"

COLS = (
    "数据源",
    "文献ID",
    "PMID",
    "WOS_UT",
    "DOI",
    "英文标题",
    "中文标题",
    "英文摘要",
    "中文摘要",
    "PubMed判断",
    "WoS判断",
    "冲突",
    "判断结果",
    "理由",
    "处理模式",
    "判别时间",
)

RANK = {"否": 0, "无法判别": 1, "是": 2}


def norm_title(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = re.sub(r"[^\w\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def resolve(pub_j: str, wos_j: str) -> str:
    """冲突时以 PubMed 为准。"""
    if pub_j in RANK:
        return pub_j
    if wos_j in RANK:
        return wos_j
    return "无法判别"


def load_pubmed() -> dict[str, dict]:
    out: dict[str, dict] = {}
    with open(PUBMED_CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            out[r["PMID"]] = r
    return out


def load_wos_judgement_index() -> dict[str, str]:
    md = WOS_MD.read_text(encoding="utf-8")
    pat = re.compile(
        r"^###\s+(\d+)\s*\n\n"
        r"- \*\*中文标题\*\*：(.+?)\n"
        r"- \*\*英文标题\*\*：(.+?)\n"
        r"- \*\*中文摘要\*\*：(.+?)\n"
        r"- \*\*英文摘要\*\*：(.+?)\n"
        r"- \*\*判断结果\*\*：(.+?)\n",
        re.M,
    )
    idx: dict[str, str] = {}
    for m in pat.finditer(md):
        j = m.group(6).strip()
        for t in (m.group(3), m.group(2)):
            key = norm_title(t)
            if len(key) >= 20:
                idx.setdefault(key, j)
    return idx


def lookup_wos(pub_row: dict, wos_idx: dict[str, str]) -> str:
    for t in (pub_row.get("英文标题", ""), pub_row.get("中文标题", "")):
        key = norm_title(t)
        if len(key) >= 20 and key in wos_idx:
            return wos_idx[key]
    return ""


def _write_csv(path: Path, rows: list[dict], cols: tuple[str, ...]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    pub = load_pubmed()
    wos_idx = load_wos_judgement_index()

    rows: list[dict] = []
    conflicts: list[dict] = []
    changed = 0

    with open(MERGED, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            pub_j = ""
            wos_j = ""
            is_conflict = "否"
            old_j = r.get("判断结果", "")

            if "PubMed" in r["数据源"] and "WoS" in r["数据源"]:
                pr = pub.get(r.get("PMID", ""), {})
                pub_j = (pr.get("判断结果") or "").strip()
                wos_j = lookup_wos(pr, wos_idx) if pr else ""
                if pub_j and wos_j and pub_j != wos_j:
                    is_conflict = "是"
                    final_j = resolve(pub_j, wos_j)
                    reason = re.sub(r"\[冲突处理\][^\n]*", "", (r.get("理由") or "")).strip()
                    note = f"[冲突处理] 以PubMed为准: PubMed={pub_j}, WoS={wos_j}"
                    if note not in reason:
                        reason = (reason + " " + note).strip() if reason else note
                    if final_j != old_j:
                        changed += 1
                    r["判断结果"] = final_j
                    r["理由"] = reason
                    conflicts.append({
                        **{k: r.get(k, "") for k in COLS if k not in ("PubMed判断", "WoS判断", "冲突")},
                        "PubMed判断": pub_j,
                        "WoS判断": wos_j,
                        "冲突": "是",
                        "原判断结果": old_j,
                    })

            row = {k: r.get(k, "") for k in COLS if k not in ("PubMed判断", "WoS判断", "冲突")}
            row["PubMed判断"] = pub_j
            row["WoS判断"] = wos_j
            row["冲突"] = is_conflict
            rows.append(row)

    # 非跨库行补空列
    for row in rows:
        if not row.get("PubMed判断") and row.get("数据源") == "PubMed":
            pr = pub.get(row.get("PMID", ""), {})
            row["PubMed判断"] = (pr.get("判断结果") or "").strip()
        if not row.get("WoS判断") and row.get("数据源") == "WoS":
            row["WoS判断"] = row.get("判断结果", "")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    merged_path = OUT_DIR / "literature_judgement_merged.csv"
    _write_csv(merged_path, rows, COLS)

    conflict_path = OUT_DIR / "literature_judgement_conflicts.csv"
    conflict_cols = list(COLS) + ["原判断结果"]
    with open(conflict_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=conflict_cols)
        w.writeheader()
        w.writerows(conflicts)

    jstats = {"是": 0, "否": 0, "无法判别": 0}
    for row in rows:
        j = row.get("判断结果", "")
        if j in jstats:
            jstats[j] += 1

    for j in ("是", "否", "无法判别"):
        part = [r for r in rows if r.get("判断结果") == j]
        _write_csv(OUT_DIR / f"literature_judgement_{j}.csv", part, COLS)

    readme = OUT_DIR / "README.md"
    readme.write_text(
        "\n".join([
            "# PubMed + WoS 斑马鱼判别 — 合并去重完整结果（冲突已处理）",
            "",
            f"更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
            "## 冲突处理",
            "",
            "跨库合并中 PubMed 与 WoS 判别不一致的条目，**以 PubMed 判别为准**（WoS 保留在 `WoS判断` 列供参考）。",
            "",
            f"- 冲突总数：**{len(conflicts):,}** 篇",
            f"- 判断结果被修订：**{changed:,}** 篇（恢复/统一为 PubMed 结果）",
            f"- 冲突明细：`literature_judgement_conflicts.csv`",
            "",
            "## 合并后统计",
            "",
            f"| 判断结果 | 数量 |",
            f"|----------|------|",
            f"| 是 | {jstats['是']:,} |",
            f"| 否 | {jstats['否']:,} |",
            f"| 无法判别 | {jstats['无法判别']:,} |",
            f"| **合计** | {len(rows):,} |",
            "",
            "## 文件",
            "",
            "- `literature_judgement_merged.csv`",
            "- `literature_judgement_conflicts.csv`",
            "- `literature_judgement_merged.zip`",
        ]),
        encoding="utf-8",
    )

    zip_path = OUT_DIR / "literature_judgement_merged.zip"
    names = [
        "README.md",
        "literature_judgement_merged.csv",
        "literature_judgement_conflicts.csv",
        "literature_judgement_是.csv",
        "literature_judgement_否.csv",
        "literature_judgement_无法判别.csv",
    ]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in names:
            p = OUT_DIR / name
            if p.is_file():
                zf.write(p, arcname=name)

    print(f"conflicts={len(conflicts):,} changed={changed:,}")
    print(f"stats 是={jstats['是']:,} 否={jstats['否']:,} 无法={jstats['无法判别']:,} total={len(rows):,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
