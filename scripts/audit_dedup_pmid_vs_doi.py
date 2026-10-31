# -*- coding: utf-8 -*-

"""对比 PMID 优先 vs DOI 优先去重的可靠性（只读分析）。"""

from __future__ import annotations



import csv

import hashlib

import re

import sys

from collections import Counter, defaultdict

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))



from scripts.export_merged_judgement_full import (

    PUBMED_CSV,

    _build_wos_index_map,

    _dedup_key,

    _load_pubmed_rows,

    _norm_doi,

    _norm_title,

    _parse_wos_md,

    _resolve_wos_md,

)



MERGED = ROOT / "项目阶段/05_数据与库/归档/data_export/literature_judgement_merged/literature_judgement_merged.csv"

OUT = ROOT / "项目阶段/05_数据与库/归档/data_export/literature_judgement_merged/dedup_pmid_vs_doi_audit.md"





def _key_pmid_first(row: dict) -> tuple[str, str]:

    return _dedup_key(row)  # pmid > doi > wos > title





def _key_doi_first(row: dict) -> tuple[str, str]:

    doi = _norm_doi(row.get("DOI") or "")

    if doi:

        return ("doi", doi)

    pmid = (row.get("PMID") or "").strip()

    if pmid:

        return ("pmid", pmid)

    wos = (row.get("WOS_UT") or "").strip()

    if wos:

        return ("wos", wos)

    title = _norm_title(row.get("英文标题") or row.get("中文标题") or "")

    if title:

        return ("title", title[:200])

    rid = (row.get("文献ID") or "").strip()

    return ("id", rid or hashlib.md5(str(row).encode()).hexdigest())





def _merge_sim(rows: list[dict], key_fn, *, use_title_cross: bool = True) -> tuple[list[dict], dict]:

    merged: dict[tuple[str, str], dict] = {}

    title_index: dict[str, tuple[str, str]] = {}

    stats = Counter()



    def register_title(row: dict, key: tuple[str, str]) -> None:

        for field in ("英文标题", "中文标题"):

            t = _norm_title(row.get(field) or "")

            if len(t) >= 20:

                title_index.setdefault(t, key)



    def find_title_key(row: dict) -> tuple[str, str] | None:

        for field in ("英文标题", "中文标题"):

            t = _norm_title(row.get(field) or "")

            if len(t) >= 20 and t in title_index:

                return title_index[t]

        return None



    def merge_into(old: dict, row: dict, cross: str) -> None:

        stats[cross] += 1

        if row["数据源"] not in old["数据源"]:

            old["数据源"] = f"{old['数据源']};{row['数据源']}"

        for f in ("WOS_UT", "DOI", "PMID"):

            if not old.get(f) and row.get(f):

                old[f] = row[f]

        if row.get("PMID") and not str(old.get("文献ID", "")).startswith("PMID:"):

            old["文献ID"] = f"PMID:{row['PMID']}"



    # PubMed first

    for r in rows[: len(rows) // 2 if False else 0]:

        pass



    pubmed_rows = [r for r in rows if r["数据源"] == "PubMed"]

    wos_rows = [r for r in rows if r["数据源"] == "WoS"]

    # rows is already combined - need separate lists

    return [], {}





def _load_all_input_rows() -> tuple[list[dict], list[dict]]:

    pub = _load_pubmed_rows()

    index_map = _build_wos_index_map()

    wos = list(_parse_wos_md(_resolve_wos_md(), index_map))

    return pub, wos





def _simulate(pub: list[dict], wos: list[dict], key_fn, *, title_cross: bool) -> tuple[int, dict, dict]:

    merged: dict[tuple[str, str], dict] = {}

    title_index: dict[str, tuple[str, str]] = {}

    stats = Counter()



    def register_title(row: dict, key: tuple[str, str]) -> None:

        for field in ("英文标题", "中文标题"):

            t = _norm_title(row.get(field) or "")

            if len(t) >= 20:

                title_index.setdefault(t, key)



    def find_title_key(row: dict) -> tuple[str, str] | None:

        for field in ("英文标题", "中文标题"):

            t = _norm_title(row.get(field) or "")

            if len(t) >= 20 and t in title_index:

                return title_index[t]

        return None



    def merge_into(old: dict, row: dict, cross: str) -> None:

        stats[cross] += 1

        if row["数据源"] not in old["数据源"]:

            old["数据源"] = f"{old['数据源']};{row['数据源']}"

        for f in ("WOS_UT", "DOI", "PMID"):

            if not old.get(f) and row.get(f):

                old[f] = row[f]



    for r in pub:

        key = key_fn(r)

        merged[key] = dict(r)

        register_title(r, key)



    for r in wos:

        key = key_fn(r)

        if key in merged:

            merge_into(merged[key], r, key[0])

            register_title(merged[key], key)

            continue

        if title_cross:

            tkey = find_title_key(r)

            if tkey and tkey in merged:

                merge_into(merged[tkey], r, "cross_title")

                register_title(merged[tkey], tkey)

                continue

        merged[key] = dict(r)

        register_title(r, key)



    # quality metrics on merged output

    by_pmid: dict[str, list] = defaultdict(list)

    by_doi: dict[str, list] = defaultdict(list)

    for row in merged.values():

        p = (row.get("PMID") or "").strip()

        d = _norm_doi(row.get("DOI") or "")

        if p:

            by_pmid[p].append(row)

        if d:

            by_doi[d].append(row)



    q = {

        "merged_total": len(merged),

        "dup_pmid_rows": sum(len(v) - 1 for v in by_pmid.values() if len(v) > 1),

        "dup_pmid_groups": sum(1 for v in by_pmid.values() if len(v) > 1),

        "dup_doi_rows": sum(len(v) - 1 for v in by_doi.values() if len(v) > 1),

        "dup_doi_groups": sum(1 for v in by_doi.values() if len(v) > 1),

        "doi_multi_pmid_groups": sum(

            1 for v in by_doi.values()

            if len({(x.get("PMID") or "").strip() for x in v if (x.get("PMID") or "").strip()}) > 1

        ),

        "pmid_multi_doi_groups": sum(

            1 for v in by_pmid.values()

            if len({_norm_doi(x.get("DOI") or "") for x in v if _norm_doi(x.get("DOI") or "")}) > 1

        ),

        "both_pubmed_wos": sum(1 for r in merged.values() if "PubMed" in r["数据源"] and "WoS" in r["数据源"]),

        "no_pmid_no_doi": sum(

            1 for r in merged.values()

            if not (r.get("PMID") or "").strip() and not _norm_doi(r.get("DOI") or "")

        ),

    }

    return len(merged), dict(stats), q





def _pairwise_diff(pub, wos):

    """找出 PMID 优先与 DOI 优先合并结果不同的条目对。"""

    def build(key_fn, title_cross):

        m: dict[tuple[str, str], dict] = {}

        ti: dict[str, tuple[str, str]] = {}

        def rt(row, key):

            for field in ("英文标题", "中文标题"):

                t = _norm_title(row.get(field) or "")

                if len(t) >= 20:

                    ti.setdefault(t, key)

        def ft(row):

            for field in ("英文标题", "中文标题"):

                t = _norm_title(row.get(field) or "")

                if len(t) >= 20 and t in ti:

                    return ti[t]

            return None

        def mi(old, row):

            if row["数据源"] not in old["数据源"]:

                old["数据源"] = f"{old['数据源']};{row['数据源']}"

            for f in ("WOS_UT", "DOI", "PMID"):

                if not old.get(f) and row.get(f):

                    old[f] = row[f]

        for r in pub:

            k = key_fn(r)

            m[k] = dict(r)

            rt(r, k)

        for r in wos:

            k = key_fn(r)

            if k in m:

                mi(m[k], r); rt(m[k], k); continue

            if title_cross:

                tk = ft(r)

                if tk and tk in m:

                    mi(m[tk], r); rt(m[tk], tk); continue

            m[k] = dict(r); rt(r, k)

        return m



    mp = build(_key_pmid_first, True)

    md = build(_key_doi_first, True)



    # index rows by wos_ut or pmid+title signature

    def sig(row):

        return "|".join([

            (row.get("PMID") or "").strip(),

            _norm_doi(row.get("DOI") or ""),

            (row.get("WOS_UT") or "").strip(),

            _norm_title(row.get("英文标题") or "")[:80],

        ])



    sp = {sig(v): v for v in mp.values()}

    sd = {sig(v): v for v in md.values()}

    # diff: same wos_ut ends in different merge groups

    wos_ut_to_pmid_key = {}

    wos_ut_to_doi_key = {}

    for k, v in mp.items():

        for r in [v]:

            w = (r.get("WOS_UT") or "").strip()

            if w:

                wos_ut_to_pmid_key[w] = k

    for k, v in md.items():

        for r in [v]:

            w = (r.get("WOS_UT") or "").strip()

            if w:

                wos_ut_to_doi_key[w] = k



    disagree = []

    for w, kp in wos_ut_to_pmid_key.items():

        kd = wos_ut_to_doi_key.get(w)

        if kd and kd != kp:

            disagree.append((w, kp, kd))

    return disagree, mp, md





def _analyze_merged_doi_dupes():

    rows = list(csv.DictReader(open(MERGED, encoding="utf-8-sig")))

    by_doi = defaultdict(list)

    for r in rows:

        d = _norm_doi(r.get("DOI") or "")

        if d:

            by_doi[d].append(r)



    multi = {d: rs for d, rs in by_doi.items() if len(rs) > 1}

    title_same = title_diff = pmid_diff = 0

    samples = []

    for d, rs in multi.items():

        titles = {_norm_title(r.get("英文标题") or r.get("中文标题") or "") for r in rs}

        pmids = {(r.get("PMID") or "").strip() for r in rs}

        if len(titles) == 1:

            title_same += 1

        else:

            title_diff += 1

        if len({p for p in pmids if p}) > 1:

            pmid_diff += 1

        if len(samples) < 8 and title_diff > 0:

            samples.append((d, rs))



    return {

        "doi_dup_groups": len(multi),

        "title_identical_groups": title_same,

        "title_different_groups": title_diff,

        "multi_pmid_groups": pmid_diff,

        "samples": samples,

    }





def main() -> int:

    pub, wos = _load_all_input_rows()

    n1, s1, q1 = _simulate(pub, wos, _key_pmid_first, title_cross=True)

    n2, s2, q2 = _simulate(pub, wos, _key_doi_first, title_cross=True)

    n1_notitle, _, q1nt = _simulate(pub, wos, _key_pmid_first, title_cross=False)

    n2_notitle, _, q2nt = _simulate(pub, wos, _key_doi_first, title_cross=False)

    disagree, mp, md = _pairwise_diff(pub, wos)

    doi_audit = _analyze_merged_doi_dupes()



    # coverage on raw inputs

    pub_pmid = sum(1 for r in pub if (r.get("PMID") or "").strip())

    pub_doi = sum(1 for r in pub if _norm_doi(r.get("DOI") or ""))

    wos_pmid = sum(1 for r in wos if (r.get("PMID") or "").strip())

    wos_doi = sum(1 for r in wos if _norm_doi(r.get("DOI") or ""))



    lines = [

        "# PMID 优先 vs DOI 优先去重 — 可靠性审计",

        "",

        "## 原始输入覆盖率",

        "",

        f"| 库 | 条数 | 有 PMID | 有 DOI |",

        f"|----|------|---------|--------|",

        f"| PubMed | {len(pub):,} | {pub_pmid:,} ({pub_pmid/len(pub)*100:.1f}%) | {pub_doi:,} ({pub_doi/len(pub)*100:.1f}%) |",

        f"| WoS | {len(wos):,} | {wos_pmid:,} ({wos_pmid/len(wos)*100:.1f}%) | {wos_doi:,} ({wos_doi/len(wos)*100:.1f}%) |",

        "",

        "**说明**：PubMed 导出 CSV 未带 DOI 字段，故 PubMed 行 DOI=0；DOI 主要来自 WoS/RIS。",

        "",

        "## 模拟合并结果（含跨库标题匹配）",

        "",

        "| 指标 | PMID 优先（现行） | DOI 优先 |",

        "|------|-------------------|----------|",

        f"| 合并后总数 | {q1['merged_total']:,} | {q2['merged_total']:,} |",

        f"| PubMed+WoS 双源行 | {q1['both_pubmed_wos']:,} | {q2['both_pubmed_wos']:,} |",

        f"| 合并后 PMID 重复组 | {q1['dup_pmid_groups']:,} | {q2['dup_pmid_groups']:,} |",

        f"| 合并后 DOI 重复组 | {q1['dup_doi_groups']:,} | {q2['dup_doi_groups']:,} |",

        f"| 同一 DOI 对应多 PMID 组 | {q1['doi_multi_pmid_groups']:,} | {q2['doi_multi_pmid_groups']:,} |",

        f"| 无 PMID 且无 DOI | {q1['no_pmid_no_doi']:,} | {q2['no_pmid_no_doi']:,} |",

        "",

        "### 去重键命中次数（WoS 并入时）",

        "",

        f"- PMID 优先：{s1}",

        f"- DOI 优先：{s2}",

        "",

        "## 模拟合并（**关闭**跨库标题匹配）",

        "",

        f"| 合并后总数 | {q1nt['merged_total']:,} | {q2nt['merged_total']:,} |",

        f"| PubMed+WoS 双源 | {q1nt['both_pubmed_wos']:,} | {q2nt['both_pubmed_wos']:,} |",

        f"| DOI 重复组 | {q1nt['dup_doi_groups']:,} | {q2nt['dup_doi_groups']:,} |",

        "",

        f"## 现行合并文件中 DOI 重复（PMID 优先后残留）",

        "",

        f"- DOI 重复组：**{doi_audit['doi_dup_groups']:,}**",

        f"- 其中标题完全相同：{doi_audit['title_identical_groups']:,}",

        f"- 其中标题不同：{doi_audit['title_different_groups']:,}",

        f"- 其中对应多个不同 PMID：{doi_audit['multi_pmid_groups']:,}",

        "",

        f"## WoS_UT 合并分组不一致（PMID 优先 vs DOI 优先）",

        "",

        f"- 不一致 WoS 记录数：**{len(disagree):,}**",

        "",

    ]



    if doi_audit["samples"]:

        lines.append("### DOI 重复且标题不同 — 样例")

        lines.append("")

        for d, rs in doi_audit["samples"][:5]:

            lines.append(f"**DOI `{d}`** ({len(rs)} 行)")

            for r in rs[:3]:

                lines.append(

                    f"- PMID={r.get('PMID','')} WOS={r.get('WOS_UT','')[:20]} "

                    f"源={r.get('数据源','')} 标题={(r.get('英文标题') or '')[:70]}"

                )

            lines.append("")



    lines.extend([

        "## 结论（自动生成，需人工抽检）",

        "",

        "见终端输出。",

        "",

    ])

    OUT.write_text("\n".join(lines), encoding="utf-8")



    print("=== SUMMARY ===")

    print("PMID-first merged:", q1["merged_total"], "DOI-first:", q2["merged_total"])

    print("PMID-first doi_dup_groups:", q1["dup_doi_groups"], "DOI-first:", q2["dup_doi_groups"])

    print("PMID-first doi_multi_pmid:", q1["doi_multi_pmid_groups"], "DOI-first:", q2["doi_multi_pmid_groups"])

    print("Current file doi_dup_groups:", doi_audit["doi_dup_groups"])

    print("WoS merge group disagreements:", len(disagree))

    print("Wrote", OUT)

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

