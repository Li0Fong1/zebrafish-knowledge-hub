# -*- coding: utf-8 -*-

"""从 literature_catalog.jsonl 抽取 N 篇文献为独立 txt，供测试。"""

from __future__ import annotations



import argparse

import json

import re

import shutil

import sys

from collections import defaultdict

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))



from core.entry import bootstrap_app



bootstrap_app()



from rag.literature_models import format_article

from scripts.literature_txt_names import build_literature_filename_from_dict



CATALOG = ROOT / "data" / "literature" / "literature_catalog.jsonl"



def _load_records() -> list:

    records = []

    with CATALOG.open(encoding="utf-8") as f:

        for line in f:

            line = line.strip()

            if not line:

                continue

            try:

                obj = json.loads(line)

            except json.JSONDecodeError:

                continue

            if (obj.get("title") or "").strip():

                records.append(obj)

    return records





def _stratified_sample(records: list, n: int) -> list:

    by_src: dict = defaultdict(list)

    for r in records:

        by_src[r.get("source", "unknown")].append(r)



    quotas = [

        ("pubmed", min(70, n)),

        ("wos", min(25, max(0, n - 70))),

        ("europepmc", min(5, max(0, n - 95))),

    ]



    def pick(pool: list, count: int, offset: int = 0) -> list:

        if not pool or count <= 0:

            return []

        step = max(1, len(pool) // count)

        out = []

        i = offset

        while len(out) < count and i < len(pool):

            out.append(pool[i])

            i += step

        return out[:count]



    picked: list = []

    for src, quota in quotas:

        picked.extend(pick(by_src.get(src, []), quota, 100 if src == "pubmed" else 50))



    seen: set = set()

    uniq: list = []

    for r in picked:

        key = (

            (r.get("pmid") or "").strip()

            or (r.get("wos_ut") or "").strip()

            or (r.get("doi") or "").strip().lower()

            or (r.get("title") or "")[:80]

        )

        if key in seen:

            continue

        seen.add(key)

        uniq.append(r)



    if len(uniq) < n:

        for r in records[5000:]:

            key = (

                (r.get("pmid") or "").strip()

                or (r.get("wos_ut") or "").strip()

                or (r.get("doi") or "").strip().lower()

                or (r.get("title") or "")[:80]

            )

            if key in seen:

                continue

            seen.add(key)

            uniq.append(r)

            if len(uniq) >= n:

                break

    return uniq[:n]





def export_sample(out_dir: Path, n: int = 100) -> int:

    records = _load_records()

    if not records:

        raise RuntimeError(f"未找到文献记录：{CATALOG}")



    sample = _stratified_sample(records, n)

    if out_dir.exists():

        for p in out_dir.glob("*.txt"):

            p.unlink()

    else:

        out_dir.mkdir(parents=True, exist_ok=True)



    index_lines = [

        f"# 文献测试样本 {len(sample)} 篇",

        f"# 来源: {CATALOG}",

        "",

        "序号\t文件名\t来源\t标题",

    ]



    total = len(sample)

    for i, art in enumerate(sample, 1):

        src = (art.get("source") or "unknown").upper()

        fname = build_literature_filename_from_dict(art, i, total=total)

        (out_dir / fname).write_text(format_article(art), encoding="utf-8")

        title = (art.get("title") or "")[:80]

        from scripts.literature_txt_names import index_prefix



        index_lines.append(f"{index_prefix(i, total=total)}\t{fname}\t{src}\t{title}")



    (out_dir / "00_索引.txt").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    return len(sample)





def main() -> int:

    p = argparse.ArgumentParser(description="抽取文献样本为 txt")

    p.add_argument("-n", "--count", type=int, default=100, help="抽取篇数")

    p.add_argument(

        "--out",

        default="",

        help="输出目录，默认 项目阶段/05_数据与库/归档/data_export/literature_test_100",

    )

    args = p.parse_args()

    out = Path(args.out) if args.out else ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "literature_test_100"

    n = export_sample(out, args.count)

    print(f"已导出 {n} 篇 → {out}")

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

