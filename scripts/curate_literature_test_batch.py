# -*- coding: utf-8 -*-

"""

测试文献批处理：研究对象以斑马鱼为主体则保留 → 内容总结 → 相似去重择优。



用法：

  python scripts/curate_literature_test_batch.py

  python scripts/curate_literature_test_batch.py --in 项目阶段/05_数据与库/归档/data_export/test/literature_test_100

"""

from __future__ import annotations



import argparse

import json

import os

import re

import shutil

import sys

from dataclasses import dataclass, field

from datetime import datetime

from difflib import SequenceMatcher

from pathlib import Path

from typing import Dict, List, Optional, Tuple



ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))



from core.entry import bootstrap_app



bootstrap_app()



from common_utils import safe_print

from scripts.literature_txt_names import build_literature_filename, extract_record_id



# 其他模式生物（出现频繁且实验对象明确时降权/剔除）

OTHER_ORGANISMS = [

    "cyprinus carpio", "common carp", "carp", "mus musculus", "mice", "mouse model",

    "human patients", "human subjects", "homo sapiens", "rattus", "rats ", "rat model",

    "drosophila", "xenopus", "chicken embryo", "arabidopsis", "caenorhabditis",

    "sus scrofa", "pig model", "canine", "murine",

]



EXPERIMENT_ON_OTHER = re.compile(

    r"(?:performed|conducted|exposed|exposure|injected|studied|evaluated|assessed|using)"

    r".{0,120}?"

    r"(?:cyprinus carpio|\bcarp\b|mice|mouse|human patients|rats?\b|drosophila|xenopus)",

    re.I | re.S,

)



ZF_RE = re.compile(r"zebrafish|danio\s+rerio", re.I)

# 斑马鱼作为实验对象/模型的表述

ZF_AS_SUBJECT = re.compile(

    r"(?:in|using|with|of|from)\s+(?:the\s+)?(?:zebrafish|danio\s+rerio)"

    r"|zebrafish\s+(?:embryo|larvae|larva|model|mutant|transgenic|retina|brain|heart|gill)"

    r"|danio\s+rerio\s+(?:embryo|larvae|model)"

    r"|exposed\s+to.{0,80}?zebrafish"

    r"|zebrafish\s+(?:were|was|are|is)\s+",

    re.I,

)

GENE_RE = re.compile(

    r"\b(?:crispr|cas9|knockout|knockdown|morpholino|transgenic|mutant|"

    r"rna-?seq|transcriptome|promoter|enhancer|gata|sox|notch|wnt|bmp|tp53)\b",

    re.I,

)





@dataclass

class Article:

    path: Path

    filename: str

    source: str = ""

    id_label: str = ""

    title: str = ""

    meta_line: str = ""

    authors: str = ""

    abstract: str = ""

    raw: str = ""

    zf_primary: bool = False

    filter_reason: str = ""

    summary: str = ""

    topic_tags: List[str] = field(default_factory=list)

    comprehensiveness: float = 0.0

    duplicate_of: str = ""

    kept: bool = False





def _parse_txt(path: Path) -> Article:

    text = path.read_text(encoding="utf-8")

    lines = [ln.rstrip() for ln in text.splitlines()]

    art = Article(path=path, filename=path.name, raw=text)



    if not lines:

        return art



    m = re.match(r"^【([^】]+)】(.+)$", lines[0])

    if m:

        art.id_label = m.group(1).strip()

        art.title = m.group(2).strip()

        if " " in art.id_label:

            art.source = art.id_label.split()[0]



    for ln in lines[1:]:

        if ln.startswith("来源信息："):

            art.meta_line = ln

        elif ln.startswith("作者："):

            art.authors = ln.replace("作者：", "").strip()

        elif ln.startswith("摘要："):

            art.abstract = ln.replace("摘要：", "").strip()



    if art.abstract in ("（无摘要，仅元数据）", ""):

        art.abstract = ""

    return art





def _count_mentions(text: str, patterns: List[str]) -> int:

    t = text.lower()

    n = 0

    for p in patterns:

        n += len(re.findall(re.escape(p), t))

    return n





def assess_zebrafish_primary(art: Article) -> Tuple[bool, str]:

    """研究对象以斑马鱼为主体则保留。"""

    title = art.title or ""

    abstract = art.abstract or ""

    combined = f"{title}\n{abstract}"

    zf_in_title = bool(ZF_RE.search(title))

    zf_count = len(ZF_RE.findall(combined))

    subject_signals = len(ZF_AS_SUBJECT.findall(combined))

    other_count = _count_mentions(combined, OTHER_ORGANISMS)



    # 标题即表明研究对象为斑马鱼（含 WoS 无摘要）

    if zf_in_title:

        if not abstract:

            return True, "研究对象：标题明确为斑马鱼（仅元数据）"

        if abstract and EXPERIMENT_ON_OTHER.search(abstract) and subject_signals == 0 and zf_count <= 1:

            return False, "实验主要在其它物种上进行，斑马鱼非研究对象"

        return True, "研究对象：标题以斑马鱼为主体"



    if zf_count == 0:

        return False, "未涉及斑马鱼，研究对象非 Danio rerio"



    # 摘要中斑马鱼为实验对象

    if subject_signals >= 1 or zf_count >= 2:

        if abstract and EXPERIMENT_ON_OTHER.search(abstract) and subject_signals == 0:

            return False, "实验对象主要为其他物种"

        if other_count >= 4 and zf_count < other_count:

            return False, "研究对象以其他模式生物为主"

        return True, "研究对象：斑马鱼为实验模型/研究材料"



    if re.search(r"\breview\b|\bmeta-?analysis\b|\bbibliometric\b", combined, re.I) and zf_count >= 2:

        return True, "综述类研究，斑马鱼为核心主题之一"



    return False, "仅提及斑马鱼，研究对象非斑马鱼主体"





def _extract_topic_tags(text: str) -> List[str]:

    tags = []

    rules = [

        ("发育", r"embryo|development|organogenesis|morphogenesis"),

        ("神经", r"neuron|brain|retina|behavior|neural"),

        ("毒理", r"toxic|exposure|pollutant|microplastic|stress"),

        ("遗传", r"gene|crispr|knockout|mutant|transgenic|genome"),

        ("疾病", r"cancer|tumor|disease|infection|inflammation"),

        ("心血管", r"heart|cardiac|vascular"),

        ("免疫", r"immune|immunity|macrophage"),

        ("表达调控", r"promoter|enhancer|transcription|gata|cis-acting"),

    ]

    t = text.lower()

    for name, pat in rules:

        if re.search(pat, t, re.I):

            tags.append(name)

    return tags or ["综合"]





def build_summary(art: Article) -> str:

    """仔细分析文献内容并生成结构化总结。"""

    abstract = (art.abstract or "").strip()

    tags = _extract_topic_tags(f"{art.title} {abstract}")

    art.topic_tags = tags



    if not abstract:

        return (

            f"研究对象：斑马鱼（Danio rerio）。\n"

            f"主题领域：{', '.join(tags)}。\n"

            f"研究问题：{art.title}\n"

            f"说明：仅元数据无摘要，内容细节有限。"

        )



    sentences = re.split(r"(?<=[.!?。！？])\s+", abstract)

    lead = " ".join(sentences[:2]).strip()

    finding = ""

    for s in sentences:

        if re.search(

            r"we (show|found|demonstrate|report|identify|conclude|observed)"

            r"|results (show|indicate|suggest)|our (study|data|results)",

            s,

            re.I,

        ):

            finding = s.strip()

            break

    if not finding and len(sentences) > 2:

        finding = sentences[-1].strip()



    method_hint = ""

    m = re.search(

        r"(crispr|cas9|knockout|morpholino|transgenic|rna-?seq|microinjection|"

        r"whole-?mount|in situ|zebrafish embryo[^.]{0,60})",

        abstract,

        re.I,

    )

    if m:

        method_hint = m.group(1)



    parts = [

        f"研究对象：斑马鱼（Danio rerio）。",

        f"主题领域：{', '.join(tags)}。",

        f"研究问题：{art.title}",

        f"方法/材料：{lead[:300]}{'...' if len(lead) > 300 else ''}",

    ]

    if method_hint:

        parts.append(f"关键技术：{method_hint[:120]}")

    if finding:

        parts.append(f"主要发现：{finding[:320]}{'...' if len(finding) > 320 else ''}")

    return "\n".join(parts)





def score_comprehensiveness(art: Article) -> float:

    """功能全面性 + 内容详细度，用于相似文献择优。"""

    abstract = art.abstract or ""

    score = 0.0

    score += min(len(abstract), 3000) / 100.0

    score += 10.0 if abstract else 0.0

    score += 4.0 * len(GENE_RE.findall(abstract))

    score += 3.0 * len(ZF_AS_SUBJECT.findall(f"{art.title}\n{abstract}"))

    score += 2.5 * len(art.topic_tags)

    score += 2.0 if art.authors else 0.0

    score += 2.0 if "DOI:" in art.meta_line else 0.0

    score += 3.0 if ZF_RE.search(art.title) else 0.0

    score += len(art.summary) / 80.0

    return round(score, 2)





def _summary_similarity(a: str, b: str) -> float:

    if not a or not b:

        return 0.0

    return SequenceMatcher(None, a.lower(), b.lower()).ratio()





def _topic_overlap(a: Article, b: Article) -> bool:

    return bool(set(a.topic_tags) & set(b.topic_tags))





def _cluster_similarity(a: Article, b: Article) -> float:

    text_a = f"{a.title}\n{a.abstract}\n{a.summary}"

    text_b = f"{b.title}\n{b.abstract}\n{b.summary}"

    summary_sim = _summary_similarity(a.summary, b.summary)

    title_sim = _summary_similarity(a.title, b.title)

    body_sim = SequenceMatcher(None, text_a.lower(), text_b.lower()).ratio()

    tag_bonus = 0.08 if _topic_overlap(a, b) else 0.0

    return max(summary_sim, title_sim * 0.95, body_sim * 0.85) + tag_bonus





def deduplicate(articles: List[Article], threshold: float = 0.68) -> None:

    """相似总结/主题聚类，保留综合分最高的一篇。"""

    n = len(articles)

    parent = list(range(n))



    def find(x: int) -> int:

        while parent[x] != x:

            parent[x] = parent[parent[x]]

            x = parent[x]

        return x



    def union(a: int, b: int) -> None:

        ra, rb = find(a), find(b)

        if ra != rb:

            parent[rb] = ra



    for i in range(n):

        for j in range(i + 1, n):

            sim = _cluster_similarity(articles[i], articles[j])

            title_sim = _summary_similarity(articles[i].title, articles[j].title)

            if sim >= threshold or (title_sim >= 0.78 and _topic_overlap(articles[i], articles[j])):

                union(i, j)



    clusters: Dict[int, List[int]] = {}

    for i in range(n):

        clusters.setdefault(find(i), []).append(i)



    for _root, members in clusters.items():

        if len(members) == 1:

            articles[members[0]].kept = True

            continue

        best = max(members, key=lambda idx: articles[idx].comprehensiveness)

        for idx in members:

            if idx == best:

                articles[idx].kept = True

            else:

                articles[idx].kept = False

                articles[idx].duplicate_of = articles[best].filename





def format_output(art: Article) -> str:

    lines = [

        art.raw.rstrip(),

        "",

        "=" * 50,

        "【处理记录】",

        f"研究对象以斑马鱼为主体：{'是' if art.zf_primary else '否'}",

        f"筛选说明：{art.filter_reason}",

        f"综合评分：{art.comprehensiveness}",

        f"主题标签：{', '.join(art.topic_tags)}",

        "",

        "【内容总结】",

        art.summary,

    ]

    return "\n".join(lines) + "\n"





def curate(in_dir: Path, out_dir: Path, *, sim_threshold: float = 0.72) -> dict:

    files = sorted(p for p in in_dir.glob("*.txt") if not p.name.startswith("00_"))

    if not files:

        raise RuntimeError(f"未找到文献 txt：{in_dir}")



    articles = [_parse_txt(p) for p in files]

    stats = {

        "input": len(articles),

        "removed_not_zebrafish_subject": [],

        "removed_duplicate": [],

        "kept": [],

    }



    primary: List[Article] = []

    for art in articles:

        ok, reason = assess_zebrafish_primary(art)

        art.zf_primary = ok

        art.filter_reason = reason

        if ok:

            art.summary = build_summary(art)

            art.comprehensiveness = score_comprehensiveness(art)

            primary.append(art)

        else:

            stats["removed_not_zebrafish_subject"].append(

                {"file": art.filename, "title": art.title, "reason": reason}

            )



    deduplicate(primary, threshold=sim_threshold)



    if out_dir.exists():

        shutil.rmtree(out_dir)

    out_dir.mkdir(parents=True, exist_ok=True)



    kept_idx = 0

    for art in primary:

        if art.kept:

            kept_idx += 1

            rid = extract_record_id(id_label=art.id_label, meta_line=art.meta_line)

            out_name = build_literature_filename(

                kept_idx,

                art.source or (art.id_label.split()[0] if art.id_label else "UNK"),

                rid,

                art.title,

            )

            (out_dir / out_name).write_text(format_output(art), encoding="utf-8")

            stats["kept"].append(

                {

                    "file": out_name,

                    "source_file": art.filename,

                    "title": art.title,

                    "score": art.comprehensiveness,

                    "tags": art.topic_tags,

                }

            )

        else:

            stats["removed_duplicate"].append(

                {

                    "file": art.filename,

                    "duplicate_of": art.duplicate_of,

                    "title": art.title,

                    "score": art.comprehensiveness,

                }

            )



    report_lines = [

        "# 文献测试批处理报告",

        "",

        f"- 处理时间：{datetime.now().isoformat(timespec='seconds')}",

        f"- 输入目录：`{in_dir}`",

        f"- 输出目录：`{out_dir}`",

        "",

        "## 处理规则",

        "",

        "1. **保留**：研究对象以斑马鱼（Danio rerio）为主体",

        "2. **总结**：逐篇分析主题、方法、主要发现",

        "3. **去重**：总结高度相似时，保留功能更全面、内容更详尽的文献",

        "",

        "## 统计",

        "",

        f"| 阶段 | 数量 |",

        f"|------|------|",

        f"| 输入 | {stats['input']} |",

        f"| 剔除（研究对象非斑马鱼） | {len(stats['removed_not_zebrafish_subject'])} |",

        f"| 进入去重 | {len(primary)} |",

        f"| 去重剔除 | {len(stats['removed_duplicate'])} |",

        f"| **最终保留** | **{len(stats['kept'])}** |",

        "",

        "## 保留文献",

        "",

    ]

    for item in stats["kept"]:

        report_lines.append(

            f"- `{item['file']}`（评分 {item['score']}，{', '.join(item['tags'])}）\n"

            f"  {item['title'][:100]}"

        )



    report_lines.extend(["", "## 剔除：研究对象非斑马鱼", ""])

    for item in stats["removed_not_zebrafish_subject"]:

        report_lines.append(f"- `{item['file']}`：{item['reason']}\n  {item['title'][:90]}")



    report_lines.extend(["", "## 剔除：内容高度相似（择优保留）", ""])

    for item in stats["removed_duplicate"]:

        report_lines.append(

            f"- `{item['file']}`（评分 {item['score']}）→ 保留 `{item['duplicate_of']}`\n"

            f"  {item['title'][:90]}"

        )



    (out_dir / "00_处理报告.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")



    index_lines = ["序号\t文件名\t评分\t主题\t标题"]

    for i, item in enumerate(stats["kept"], 1):

        index_lines.append(

            f"{i:03d}\t{item['file']}\t{item['score']}\t{','.join(item['tags'])}\t{item['title'][:80]}"

        )

    (out_dir / "01_总结索引.txt").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    (out_dir / "processing_stats.json").write_text(

        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"

    )



    return stats





def main() -> int:

    p = argparse.ArgumentParser(description="测试文献：斑马鱼研究对象筛选 + 总结 + 去重择优")

    p.add_argument(

        "--in",

        dest="in_dir",

        default=str(ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "test" / "literature_test_100"),

    )

    p.add_argument(

        "--out",

        default="",

        help="输出目录，默认 项目阶段/05_数据与库/归档/data_export/test/literature_test_processed_时间戳",

    )

    p.add_argument("--similarity", type=float, default=0.61, help="总结相似度阈值")

    args = p.parse_args()



    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    out = args.out or str(ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "test" / f"literature_test_processed_{stamp}")



    stats = curate(Path(args.in_dir), Path(out), sim_threshold=args.similarity)

    safe_print(

        f"\n[完成] 输入 {stats['input']} → 保留 {len(stats['kept'])} 篇\n"

        f"  非斑马鱼研究对象剔除 {len(stats['removed_not_zebrafish_subject'])}"

        f" | 去重剔除 {len(stats['removed_duplicate'])}\n"

        f"  输出：{out}"

    )

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

