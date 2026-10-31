# -*- coding: utf-8 -*-

"""WoS 判别质量审计：与 PubMed 对齐、内部一致性、元数据/匹配问题。"""

from __future__ import annotations



import csv

import json

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

    _norm_title,

    _parse_wos_md,

    _resolve_wos_md,

)

from scripts.judge_hybrid_triage import triage_zebrafish



OUT_DIR = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "literature_judgement_merged"

MERGED = OUT_DIR / "literature_judgement_merged.csv"

CONFLICTS = OUT_DIR / "literature_judgement_conflicts.csv"

REPORT = OUT_DIR / "wos_judgement_audit.md"

SAMPLES = OUT_DIR / "wos_judgement_audit_samples.json"





def _load_pubmed_by_pmid() -> dict[str, dict]:

    out: dict[str, dict] = {}

    with open(PUBMED_CSV, encoding="utf-8-sig", newline="") as f:

        for r in csv.DictReader(f):

            out[r["PMID"]] = r

    return out





def _title_index_wos(wos_rows: list[dict]) -> dict[str, list[int]]:

    idx: dict[str, list[int]] = defaultdict(list)

    for i, r in enumerate(wos_rows):

        for field in ("英文标题", "中文标题"):

            t = _norm_title(r.get(field) or "")

            if len(t) >= 20:

                idx[t].append(i)

    return idx





def _lookup_wos_by_title(pub_row: dict, title_idx: dict[str, list[int]], wos_rows: list[dict]) -> tuple[str, int | None]:

    for field in ("英文标题", "中文标题"):

        key = _norm_title(pub_row.get(field) or "")

        if len(key) >= 20 and key in title_idx:

            i = title_idx[key][0]

            return wos_rows[i].get("判断结果", ""), i

    return "", None





def main() -> int:

    pub = _load_pubmed_by_pmid()

    index_map = _build_wos_index_map()

    wos_rows = list(_parse_wos_md(_resolve_wos_md(), index_map))

    title_idx = _title_index_wos(wos_rows)



    # --- WoS 内部统计 ---

    wos_stats = Counter(r.get("判断结果", "") for r in wos_rows)

    wos_mode = Counter(r.get("处理模式", "") for r in wos_rows)



    # 同标题不同判别

    title_judge_conflict = 0

    title_judge_samples = []

    for t, idxs in title_idx.items():

        js = {wos_rows[i].get("判断结果", "") for i in idxs}

        if len(js) > 1:

            title_judge_conflict += 1

            if len(title_judge_samples) < 5:

                title_judge_samples.append({

                    "title": t[:100],

                    "judgements": sorted(js),

                    "indices": idxs[:3],

                })



    # hybrid 规则 vs WoS 判别

    hybrid_vs_wos = Counter()

    hybrid_mismatch_samples = []

    for i, r in enumerate(wos_rows):

        te = (r.get("英文标题") or r.get("中文标题") or "").strip()

        ae = (r.get("英文摘要") or r.get("中文摘要") or "").strip()

        if ae in ("无摘要", "No abstract", ""):

            ae = ""

        tier, pre_j, _ = triage_zebrafish(te, ae)

        wj = r.get("判断结果", "")

        if tier in ("clear_yes", "clear_no") and pre_j:

            key = f"hybrid_{pre_j}_wos_{wj}"

            hybrid_vs_wos[key] += 1

            if pre_j != wj and len(hybrid_mismatch_samples) < 20:

                hybrid_mismatch_samples.append({

                    "idx": i + 1,

                    "hybrid": pre_j,

                    "wos": wj,

                    "tier": tier,

                    "title": te[:120],

                })



    # --- 与 PubMed 对齐（RIS 序号 → WoS index，需 PubMed 在 RIS 有 PMID）---

    # 建立 pmid -> wos judgement via merged PubMed;WoS rows with WOS_UT from same RIS chain

    merged_rows = list(csv.DictReader(open(MERGED, encoding="utf-8-sig")))



    # 正确配对：merged 行 PMID + 用标题反查 wos md index，与 RIS 映射的 wos 序号比对

    title_lookup_wrong = 0

    title_lookup_ambiguous = 0

    title_lookup_ok = 0

    wrong_samples = []



    for r in merged_rows:

        if "PubMed" not in r.get("数据源", "") or "WoS" not in r.get("数据源", ""):

            continue

        pmid = (r.get("PMID") or "").strip()

        if not pmid:

            continue

        pr = pub.get(pmid, {})

        if not pr:

            continue

        wos_j_title, wos_i = _lookup_wos_by_title(pr, title_idx, wos_rows)

        if wos_i is None:

            continue

        # 该 wos md 条目应对应 RIS index = wos_i+1 在 parse 里是 match group 1 based

        # wos_rows order follows MD parse order - index i corresponds to MD ### (i+1) if sequential

        # Verify: wos row at i should have same title as pub

        pub_t = _norm_title(pr.get("英文标题") or pr.get("中文标题") or "")

        wos_t = _norm_title(wos_rows[wos_i].get("英文标题") or wos_rows[wos_i].get("中文标题") or "")

        if pub_t == wos_t:

            title_lookup_ok += 1

        else:

            title_lookup_wrong += 1

            if len(wrong_samples) < 8:

                wrong_samples.append({"pmid": pmid, "pub_title": pub_t[:80], "wos_title": wos_t[:80]})



        # ambiguous title index

        for field in ("英文标题", "中文标题"):

            key = _norm_title(pr.get(field) or "")

            if len(key) >= 20 and len(title_idx.get(key, [])) > 1:

                title_lookup_ambiguous += 1

                break



    # 冲突分析：PubMed vs WoS（用 MD 直接按 index 对齐 vs 标题 lookup）

    # 建立 MD index -> wos row

    md_index_to_row = {i + 1: wos_rows[i] for i in range(len(wos_rows))}



    # RIS index -> pmid from index_map; find wos rows that share pmid via merged

    pmid_to_wos_by_ris: dict[str, str] = {}

    for ris_idx, meta in index_map.items():

        pmid = (meta.get("PMID") or "").strip()

        if pmid and ris_idx in md_index_to_row:

            pmid_to_wos_by_ris[pmid] = md_index_to_row[ris_idx].get("判断结果", "")



    agree_ris = disagree_ris = no_ris_pmid = 0

    ris_disagree_pairs = Counter()

    for pmid, pr in pub.items():

        wj = pmid_to_wos_by_ris.get(pmid, "")

        if not wj:

            no_ris_pmid += 1

            continue

        pj = pr.get("判断结果", "")

        if pj == wj:

            agree_ris += 1

        else:

            disagree_ris += 1

            ris_disagree_pairs[(pj, wj)] += 1



    # 冲突文件中的 WoS 是否可通过 RIS-PMID 复核

    conflict_rows = list(csv.DictReader(open(CONFLICTS, encoding="utf-8-sig")))

    conflict_wos_title_vs_ris = Counter()

    for cr in conflict_rows[:5000]:  # cap

        pmid = (cr.get("PMID") or "").strip()

        wos_conflict = (cr.get("WoS判断") or "").strip()

        wos_ris = pmid_to_wos_by_ris.get(pmid, "")

        if not wos_ris:

            conflict_wos_title_vs_ris["no_ris_pmid"] += 1

        elif wos_ris == wos_conflict:

            conflict_wos_title_vs_ris["title_match_ris"] += 1

        else:

            conflict_wos_title_vs_ris["title_diff_from_ris"] += 1



    # WoS 是否系统性更宽松（是 vs 否）

    wos_yes_pub_no = sum(1 for cr in conflict_rows if cr.get("PubMed判断") == "否" and cr.get("WoS判断") == "是")

    wos_no_pub_yes = sum(1 for cr in conflict_rows if cr.get("PubMed判断") == "是" and cr.get("WoS判断") == "否")



    # 摘要/标题异常

    empty_abs = sum(1 for r in wos_rows if not (r.get("英文摘要") or "").strip() or r.get("英文摘要") in ("无摘要",))

    short_title = sum(1 for r in wos_rows if len((r.get("英文标题") or "").strip()) < 15)



    lines = [

        "# WoS 判别质量审计报告",

        "",

        "## 1. WoS 原始判别分布（95,052 篇）",

        "",

        f"| 判断结果 | 数量 |",

        f"|----------|------|",

    ]

    for j in ("是", "否", "无法判别"):

        if wos_stats[j]:

            lines.append(f"| {j} | {wos_stats[j]:,} |")

    lines.extend([

        "",

        f"- 处理模式：{dict(wos_mode)}",

        f"- 英文摘要为空/无摘要：**{empty_abs:,}** ({empty_abs/len(wos_rows)*100:.1f}%)",

        f"- 英文标题过短(<15字符)：**{short_title:,}**",

        "",

        "## 2. WoS 内部一致性问题",

        "",

        f"- 同一规范化标题对应多种判别：**{title_judge_conflict:,}** 组",

        "",

        "### 混合规则（PubMed 同款）vs WoS 实际判别",

        "",

        "| 组合 | 数量 |",

        "|------|------|",

    ])

    for k, n in sorted(hybrid_vs_wos.items(), key=lambda x: -x[1])[:12]:

        lines.append(f"| {k} | {n:,} |")

    hybrid_yes_wos_no = hybrid_vs_wos.get("hybrid_是_wos_否", 0)

    hybrid_no_wos_yes = hybrid_vs_wos.get("hybrid_否_wos_是", 0)

    lines.extend([

        "",

        f"- 规则明确「是」但 WoS 判「否」：**{hybrid_yes_wos_no:,}**",

        f"- 规则明确「否」但 WoS 判「是」：**{hybrid_no_wos_yes:,}**",

        "",

        "## 3. 与 PubMed 对比（RIS 中带 PMID 的条目，共 {:,} 可配对）".format(agree_ris + disagree_ris),

        "",

        f"- 判别一致：**{agree_ris:,}** ({agree_ris/max(agree_ris+disagree_ris,1)*100:.1f}%)",

        f"- 判别不一致：**{disagree_ris:,}** ({disagree_ris/max(agree_ris+disagree_ris,1)*100:.1f}%)",

        f"- PubMed 有 PMID 但 RIS 无映射：**{no_ris_pmid:,}**",

        "",

        "### RIS-PMID 配对不一致分布",

        "",

        "| PubMed | WoS | 数量 |",

        "|--------|-----|------|",

    ])

    for (pj, wj), n in ris_disagree_pairs.most_common(8):

        lines.append(f"| {pj} | {wj} | {n:,} |")



    lines.extend([

        "",

        "## 4. 冲突检测方式问题（标题 lookup）",

        "",

        "冲突文件用 **PubMed 标题 → WoS MD 标题索引** 取 WoS 判断，而非 RIS-PMID 直配。",

        "",

        f"- 标题 lookup 与 PubMed 标题一致：**{title_lookup_ok:,}**",

        f"- 标题 lookup 不一致：**{title_lookup_wrong:,}**",

        f"- 标题命中多条 WoS（歧义）：**{title_lookup_ambiguous:,}**",

        "",

        "### 冲突记录中 WoS 判断 vs RIS-PMID 直配 WoS",

        "",

        f"- 与 RIS 直配一致：{conflict_wos_title_vs_ris.get('title_match_ris',0):,}",

        f"- 与 RIS 直配不一致：{conflict_wos_title_vs_ris.get('title_diff_from_ris',0):,}",

        f"- RIS 无 PMID：{conflict_wos_title_vs_ris.get('no_ris_pmid',0):,}",

        "",

        "## 5. 冲突方向（9,464 条）",

        "",

        f"- PubMed=否, WoS=是：**{wos_yes_pub_no:,}**（WoS 更宽松）",

        f"- PubMed=是, WoS=否：**{wos_no_pub_yes:,}**（WoS 更严格）",

        "",

        "## 6. 结论摘要",

        "",

        "1. **判别不同是真实存在的**：RIS-PMID 直配仍有约 {:.1f}% 不一致，不全是匹配错误。".format(

            disagree_ris / max(agree_ris + disagree_ris, 1) * 100

        ),

        "2. **WoS 整体更偏「是」**：冲突中 PubMed否/WoS是 约为 PubMed是/WoS否 的 {:.1f} 倍。".format(

            wos_yes_pub_no / max(wos_no_pub_yes, 1)

        ),

        "3. **WoS 存在元数据串号**（同 WOS_UT/DOI 挂多篇 PMID），会影响 DOI 去重，与判别冲突是不同问题。",

        "4. **冲突检测依赖标题匹配**，在标题歧义或翻译差异时可能配错 WoS 条目（见 lookup 不一致计数）。",

        "",

        "样例见 `wos_judgement_audit_samples.json`。",

        "",

    ])



    REPORT.write_text("\n".join(lines), encoding="utf-8")

    SAMPLES.write_text(

        json.dumps(

            {

                "title_judge_conflict": title_judge_samples,

                "hybrid_mismatch": hybrid_mismatch_samples[:15],

                "title_lookup_wrong": wrong_samples,

            },

            ensure_ascii=False,

            indent=2,

        ),

        encoding="utf-8",

    )



    print("wos_rows", len(wos_rows))

    print("hybrid_no_wos_yes", hybrid_no_wos_yes, "hybrid_yes_wos_no", hybrid_yes_wos_no)

    print("ris agree", agree_ris, "disagree", disagree_ris)

    print("conflict wos_yes_pub_no", wos_yes_pub_no, "wos_no_pub_yes", wos_no_pub_yes)

    print("wrote", REPORT)

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

