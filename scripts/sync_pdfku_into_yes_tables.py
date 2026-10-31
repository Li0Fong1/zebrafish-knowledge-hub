# -*- coding: utf-8 -*-

"""

将 pdf库 全部文献视为判别「是」，补入：

  1) 项目阶段/05_数据与库/归档/data_export/literature_judgement_merged/literature_judgement_是.csv

  2) literature_judgement_merged.csv（同步把判断结果改为「是」）

  3) 解析文件/已存在表.csv（金标战役 load_exist 入口）



并尽量从 pdfku 金标 JSON / 文件名补全标题 DOI；PMID 能解析则写上。



用法：

  .\\.venv\\Scripts\\python.exe -u -m scripts.sync_pdfku_into_yes_tables

  .\\.venv\\Scripts\\python.exe -u -m scripts.sync_pdfku_into_yes_tables --dry-run

"""

from __future__ import annotations



import argparse

import csv

import json

import re

import shutil

import sys

from collections import defaultdict

from datetime import datetime

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(ROOT))



from scripts.gold_upgrade_5680 import PDFKU_HTML, PDFKU_PDF, PDFKU_XML, _doi_from_pdfku_stem



YES_CSV = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "literature_judgement_merged" / "literature_judgement_是.csv"

MERGED_CSV = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "literature_judgement_merged" / "literature_judgement_merged.csv"

NO_CSV = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "literature_judgement_merged" / "literature_judgement_否.csv"

UNK_CSV = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "literature_judgement_merged" / "literature_judgement_无法判别.csv"

EXIST_CSV = ROOT / "解析文件" / "已存在表.csv"

PDFKU_GOLD = ROOT / "解析文件" / "pdfku_library_fulltext_gold"

REPORT = ROOT / "解析文件" / "pdf库补入是表报告.json"

REPORT_MD = ROOT / "解析文件" / "pdf库补入是表报告.md"





def _now() -> str:

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")





def _backup(path: Path) -> None:

    if not path.is_file():

        return

    bak = path.with_suffix(path.suffix + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    shutil.copy2(path, bak)





def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:

    if not path.is_file():

        return [], []

    with path.open(encoding="utf-8-sig", newline="") as f:

        r = csv.DictReader(f)

        fields = list(r.fieldnames or [])

        rows = [{k: (v or "") for k, v in row.items()} for row in r]

    return fields, rows





def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as f:

        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")

        w.writeheader()

        for row in rows:

            w.writerow({k: row.get(k, "") for k in fields})





def _catalog_pdfku() -> dict[str, dict]:

    """entry_no -> {doi, has_pdf, has_xml, has_html, stem}"""

    out: dict[str, dict] = {}

    for folder, pattern, flag in (

        (PDFKU_PDF, "*.pdf", "has_pdf"),

        (PDFKU_XML, "*.xml", "has_xml"),

        (PDFKU_HTML, "*.txt", "has_html"),

    ):

        if not folder.is_dir():

            continue

        for p in folder.glob(pattern):

            m = re.match(r"^(\d+)_", p.stem)

            if not m:

                continue

            eno = m.group(1)

            doi = _doi_from_pdfku_stem(p.stem)

            rec = out.setdefault(

                eno,

                {

                    "entry_no": eno,

                    "doi": doi,

                    "has_pdf": False,

                    "has_xml": False,

                    "has_html": False,

                    "stem": p.stem,

                },

            )

            if doi and not rec.get("doi"):

                rec["doi"] = doi

            rec[flag] = True

    return out





def _load_gold_meta() -> dict[str, dict]:

    """entry_no -> title/pmid/doi from pdfku gold json if any."""

    meta: dict[str, dict] = {}

    if not PDFKU_GOLD.is_dir():

        return meta

    for p in PDFKU_GOLD.glob("batch_*/papers/*.json"):

        try:

            rec = json.loads(p.read_text(encoding="utf-8"))

        except Exception:

            continue

        eno = str(rec.get("entry_no") or p.stem)

        meta[eno] = {

            "title_en": rec.get("title_en") or rec.get("title") or "",

            "title_zh": rec.get("title_zh") or "",

            "doi": (rec.get("doi") or "").strip(),

            "pmid": str(rec.get("pmid") or "").strip(),

            "journal": rec.get("journal") or "",

            "abstract_en": (rec.get("abstract_en") or "")[:2000],

            "abstract_zh": (rec.get("abstract_zh") or "")[:2000],

        }

    return meta





def _yes_fields_default() -> list[str]:

    return [

        "文献源",

        "文献ID",

        "PMID",

        "WOS_UT",

        "DOI",

        "英文标题",

        "中文标题",

        "英文摘要",

        "中文摘要",

        "判断结果",

        "理由",

        "处理模式",

        "登记时间",

    ]





def _exist_fields_default() -> list[str]:

    return [

        "q12_no",

        "entry_no",

        "en_title",

        "zh_title",

        "journal",

        "an",

        "doi",

        "pmid",

        "pmcid",

        "content_type",

        "source_step",

        "text_chars",

        "source_url",

    ]





def run(*, dry_run: bool = False) -> dict:

    catalog = _catalog_pdfku()

    gold_meta = _load_gold_meta()



    yes_fields, yes_rows = _read_csv(YES_CSV)

    if not yes_fields:

        yes_fields = _yes_fields_default()

    merged_fields, merged_rows = _read_csv(MERGED_CSV)

    if not merged_fields:

        merged_fields = yes_fields

    exist_fields, exist_rows = _read_csv(EXIST_CSV)

    if not exist_fields:

        exist_fields = _exist_fields_default()



    yes_by_doi = {

        (r.get("DOI") or "").strip().lower(): r

        for r in yes_rows

        if (r.get("DOI") or "").strip()

    }

    merged_by_doi = {

        (r.get("DOI") or "").strip().lower(): r

        for r in merged_rows

        if (r.get("DOI") or "").strip()

    }

    exist_by_eno = {str(r.get("entry_no") or "").strip(): r for r in exist_rows}

    exist_by_doi = {

        (r.get("doi") or "").strip().lower(): r

        for r in exist_rows

        if (r.get("doi") or "").strip()

    }



    added_yes: list[dict] = []

    updated_merged: list[str] = []

    added_exist: list[dict] = []

    already_yes = 0

    already_exist = 0



    for eno, info in sorted(catalog.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):

        doi = (info.get("doi") or "").strip()

        doi_l = doi.lower()

        g = gold_meta.get(eno) or {}

        title_en = g.get("title_en") or ""

        title_zh = g.get("title_zh") or ""

        pmid = g.get("pmid") or ""

        journal = g.get("journal") or ""

        abs_en = g.get("abstract_en") or ""

        abs_zh = g.get("abstract_zh") or ""

        if g.get("doi") and not doi:

            doi = g["doi"].strip()

            doi_l = doi.lower()

            info["doi"] = doi



        # --- 是表 ---

        if doi_l and doi_l in yes_by_doi:

            already_yes += 1

            # 确保判断结果为是

            if (yes_by_doi[doi_l].get("判断结果") or "").strip() != "是":

                yes_by_doi[doi_l]["判断结果"] = "是"

                yes_by_doi[doi_l]["理由"] = (yes_by_doi[doi_l].get("理由") or "") + "|pdf库确认是"

        else:

            lit_id = f"PDFKU:{eno}"

            row = {k: "" for k in yes_fields}

            row.update(

                {

                    "文献源": "pdf库",

                    "文献ID": lit_id,

                    "PMID": pmid,

                    "WOS_UT": "",

                    "DOI": doi,

                    "英文标题": title_en or f"[pdf库 entry_no={eno}]",

                    "中文标题": title_zh,

                    "英文摘要": abs_en,

                    "中文摘要": abs_zh,

                    "判断结果": "是",

                    "理由": "pdf库收录，按项目约定全部视为判别「是」",

                    "处理模式": "pdfku_sync",

                    "登记时间": _now(),

                }

            )

            yes_rows.append(row)

            if doi_l:

                yes_by_doi[doi_l] = row

            added_yes.append({"entry_no": eno, "doi": doi, "pmid": pmid})



        # --- merged：有则改是，无则追加 ---

        if doi_l and doi_l in merged_by_doi:

            mr = merged_by_doi[doi_l]

            if (mr.get("判断结果") or "").strip() != "是":

                mr["判断结果"] = "是"

                mr["理由"] = ((mr.get("理由") or "") + "|pdf库确认是").strip("|")

                mr["处理模式"] = mr.get("处理模式") or "pdfku_sync"

                updated_merged.append(doi)

        elif doi_l or eno:

            row = {k: "" for k in merged_fields}

            row.update(

                {

                    "文献源": "pdf库",

                    "文献ID": f"PDFKU:{eno}",

                    "PMID": pmid,

                    "DOI": doi,

                    "英文标题": title_en or f"[pdf库 entry_no={eno}]",

                    "中文标题": title_zh,

                    "英文摘要": abs_en,

                    "中文摘要": abs_zh,

                    "判断结果": "是",

                    "理由": "pdf库收录，按项目约定全部视为判别「是」",

                    "处理模式": "pdfku_sync",

                    "登记时间": _now(),

                }

            )

            merged_rows.append(row)

            if doi_l:

                merged_by_doi[doi_l] = row



        # --- 已存在表（金标入口）---

        if eno in exist_by_eno or (doi_l and doi_l in exist_by_doi):

            already_exist += 1

            # 补全缺失字段

            er = exist_by_eno.get(eno) or (exist_by_doi.get(doi_l) if doi_l else None)

            if er is not None:

                if not (er.get("doi") or "").strip() and doi:

                    er["doi"] = doi

                if not (er.get("pmid") or "").strip() and pmid:

                    er["pmid"] = pmid

                if not (er.get("en_title") or "").strip() and title_en:

                    er["en_title"] = title_en

                if not (er.get("zh_title") or "").strip() and title_zh:

                    er["zh_title"] = title_zh

        else:

            ctype = "pdf" if info.get("has_pdf") else ("xml" if info.get("has_xml") else "html")

            er = {k: "" for k in exist_fields}

            er.update(

                {

                    "q12_no": "",

                    "entry_no": eno,

                    "en_title": title_en or f"[pdf库 entry_no={eno}]",

                    "zh_title": title_zh,

                    "journal": journal,

                    "an": "",

                    "doi": doi,

                    "pmid": pmid,

                    "pmcid": "",

                    "content_type": ctype,

                    "source_step": "pdfku_sync_to_yes",

                    "text_chars": "",

                    "source_url": "",

                }

            )

            exist_rows.append(er)

            exist_by_eno[eno] = er

            if doi_l:

                exist_by_doi[doi_l] = er

            added_exist.append({"entry_no": eno, "doi": doi, "content_type": ctype})



    # 从否/无法判别中按 DOI 剔除（已升为是）

    removed_no = removed_unk = 0

    _, no_rows = _read_csv(NO_CSV)

    _, unk_rows = _read_csv(UNK_CSV)

    yes_dois = {(r.get("DOI") or "").strip().lower() for r in yes_rows if (r.get("DOI") or "").strip()}

    new_no = [r for r in no_rows if (r.get("DOI") or "").strip().lower() not in yes_dois]

    new_unk = [r for r in unk_rows if (r.get("DOI") or "").strip().lower() not in yes_dois]

    removed_no = len(no_rows) - len(new_no)

    removed_unk = len(unk_rows) - len(new_unk)



    summary = {

        "at": datetime.now().isoformat(timespec="seconds"),

        "pdfku_unique_entry_no": len(catalog),

        "already_in_yes": already_yes,

        "added_to_yes": len(added_yes),

        "yes_total_after": len(yes_rows) if not dry_run else len(yes_rows),

        "merged_flipped_to_yes": len(updated_merged),

        "already_in_exist": already_exist,

        "added_to_exist": len(added_exist),

        "exist_total_after": len(exist_rows),

        "removed_from_否": removed_no,

        "removed_from_无法判别": removed_unk,

        "dry_run": dry_run,

    }



    if not dry_run:

        _backup(YES_CSV)

        _backup(MERGED_CSV)

        _backup(EXIST_CSV)

        if NO_CSV.is_file():

            _backup(NO_CSV)

        if UNK_CSV.is_file():

            _backup(UNK_CSV)



        _write_csv(YES_CSV, yes_fields, yes_rows)

        _write_csv(MERGED_CSV, merged_fields, merged_rows)

        _write_csv(EXIST_CSV, exist_fields, exist_rows)

        if NO_CSV.is_file():

            no_fields, _ = _read_csv(NO_CSV)

            _write_csv(NO_CSV, no_fields or yes_fields, new_no)

        if UNK_CSV.is_file():

            unk_fields, _ = _read_csv(UNK_CSV)

            _write_csv(UNK_CSV, unk_fields or yes_fields, new_unk)



    report = {

        "summary": summary,

        "added_yes_sample": added_yes[:100],

        "added_exist_sample": added_exist[:100],

    }

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    REPORT_MD.write_text(

        "\n".join(

            [

                "# pdf库补入是表报告",

                "",

                f"- 时间：{summary['at']}",

                f"- pdf库唯一 entry_no：{summary['pdfku_unique_entry_no']}",

                f"- 已在是表：{summary['already_in_yes']}",

                f"- **新补入是表：{summary['added_to_yes']}**",

                f"- merged 中改判为是：{summary['merged_flipped_to_yes']}",

                f"- 已在已存在表：{summary['already_in_exist']}",

                f"- **新补入已存在表（金标入口）：{summary['added_to_exist']}**",

                f"- 从否表移除：{summary['removed_from_否']}；从无法判别移除：{summary['removed_from_无法判别']}",

                f"- dry_run：{dry_run}",

                "",

                "说明：pdf库文献按约定全部视为「是」。已存在表供 `gold_hunt_fulltext` 按是表金标标准处理；",

                "是表供判别统计；向量化仍优先需 PMID，后续可对新增行补 PMID 后纳入 YES FAISS。",

            ]

        ),

        encoding="utf-8",

    )

    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

    print(f"[report] {REPORT_MD}", flush=True)

    return summary





def main() -> int:

    ap = argparse.ArgumentParser()

    ap.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()

    run(dry_run=args.dry_run)

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

