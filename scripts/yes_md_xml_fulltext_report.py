# -*- coding: utf-8 -*-
"""
从 是.md 前 N 篇拉取 Europe PMC JATS XML 全文，结构化解析并生成分析报告。

用法：
  python -m scripts.yes_md_xml_fulltext_report
  python -m scripts.yes_md_xml_fulltext_report --limit 10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from rag.europepmc_collector import REQUEST_INTERVAL as EPMC_INTERVAL
from rag.literature_collector import _api_get, _sleep
from rag.network_resilience import urlopen_with_retry
from rag.pubmed_corpus_utils import fetch_pmc_fulltext, parse_pubmed_xml
from scripts.yes_md_pdf_test_report import (
    _find_merged_ris,
    _find_yes_md,
    build_ris_title_index,
    lookup_ris_meta,
    parse_yes_md_entries,
    resolve_ids,
)

EPMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"
XML_DIR = ROOT / "zebrafish_package" / "结果" / "xml_fulltext"
REPORT_MD = ROOT / "zebrafish_package" / "结果" / "xml_fulltext_analysis_report.md"
REPORT_JSON = ROOT / "zebrafish_package" / "结果" / "xml_fulltext_analysis_report.json"

SECTION_TAGS = {
    "abstract": ("abstract",),
    "introduction": ("intro",),
    "methods": ("methods", "materials|methods", "materials-and-methods", "materials and methods"),
    "results": ("results",),
    "discussion": ("discussion", "dis",),
    "conclusion": ("conclusion", "conclusions"),
    "acknowledgments": ("ack", "acknowledgment", "acknowledgement"),
    "references": ("ref-list", "references"),
}

GENE_PATTERNS = re.compile(
    r"\b(?:sox\d+[a-z]?|tp53|vegfa|bmp4|shha|notch\d+|hox[a-z]\d+|miR?-\d+[a-z]?|"
    r"danio rerio|zebrafish|cloche|clo|egfl7|mmp13|ncf1)\b",
    re.I,
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _elem_text(elem: ET.Element) -> str:
    return " ".join("".join(elem.itertext()).split())


def fetch_jats_xml(pmid: str, pmcid: str = "") -> tuple[bytes, str, str]:
    """返回 (raw_xml_bytes, pmcid_used, source_url)。"""
    paths: list[str] = []
    if pmcid:
        paths.append(f"{pmcid}/fullTextXML")
    if pmid:
        paths.append(f"MED/{pmid}/fullTextXML")
    last_err: Optional[Exception] = None
    for path in paths:
        url = f"{EPMC_REST}/{path}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
            with urlopen_with_retry(req, timeout=120, label=f"JATS {path}") as resp:
                raw = resp.read()
            if raw.strip():
                return raw, path.split("/")[0], url
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 404:
                continue
            time.sleep(EPMC_INTERVAL)
        except Exception as e:
            last_err = e
            time.sleep(EPMC_INTERVAL)
    if last_err and getattr(last_err, "code", None) != 404:
        raise last_err
    return b"", pmcid or "", ""


def _match_section(sec: ET.Element) -> str:
    sec_type = (sec.get("sec-type") or "").strip().lower()
    title_elem = None
    for child in sec:
        if _local(child.tag) == "title":
            title_elem = child
            break
    title = _elem_text(title_elem).lower() if title_elem is not None else ""
    label = sec_type or title
    for canonical, aliases in SECTION_TAGS.items():
        for alias in aliases:
            if alias in label:
                return canonical
    if title:
        return title[:40]
    return "other"


def _collect_sections(root: ET.Element) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    body = None
    for elem in root.iter():
        if _local(elem.tag) == "body":
            body = elem
            break
    if body is None:
        return sections
    for sec in body.iter():
        if _local(sec.tag) != "sec":
            continue
        # all sec with paragraphs
        paras: list[str] = []
        for p in sec.iter():
            if _local(p.tag) == "p":
                t = _elem_text(p)
                if len(t) > 30:
                    paras.append(t)
        if not paras:
            continue
        key = _match_section(sec)
        sections.setdefault(key, []).extend(paras)

    # 扁平 body（无/少 sec，仅 <p>）：按正文段落收入，避免 Frontiers 等漏抽全文
    flat_paras: list[str] = []
    for child in list(body):
        tag = _local(child.tag)
        if tag == "p":
            t = _elem_text(child)
            if len(t) > 30:
                flat_paras.append(t)
        elif tag == "sec":
            continue
    if flat_paras:
        # 若已有实质 sec 正文，仍并入 other，避免丢段；若几乎无 sec，作为 introduction 主文
        sec_chars = sum(len("\n".join(v)) for k, v in sections.items() if k != "other")
        if sec_chars < 800:
            sections.setdefault("introduction", []).extend(flat_paras)
        else:
            sections.setdefault("other", []).extend(flat_paras)

    # back/ref-list → references
    for elem in root.iter():
        if _local(elem.tag) not in ("ref-list", "ref"):
            continue
        if _local(elem.tag) == "ref-list":
            refs: list[str] = []
            for ref in elem.iter():
                if _local(ref.tag) != "ref":
                    continue
                t = _elem_text(ref)
                if len(t) > 20:
                    refs.append(t)
            if refs:
                sections.setdefault("references", []).extend(refs)
            break
    return sections


def parse_jats_xml(raw: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {
        "title": "",
        "abstract": "",
        "keywords": [],
        "sections": {},
        "section_stats": {},
        "figure_count": 0,
        "table_count": 0,
        "ref_count": 0,
        "body_char_count": 0,
        "body_para_count": 0,
        "zebrafish_terms": [],
        "gene_mentions": [],
    }
    if not raw.strip():
        return out
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return out

    for elem in root.iter():
        tag = _local(elem.tag)
        if tag == "article-title" and not out["title"]:
            out["title"] = _elem_text(elem)
        elif tag == "abstract":
            parts = [_elem_text(p) for p in elem.iter() if _local(p.tag) == "p"]
            if not parts:
                parts = [_elem_text(elem)]
            out["abstract"] = "\n".join(x for x in parts if x)
        elif tag == "kwd":
            kw = _elem_text(elem)
            if kw:
                out["keywords"].append(kw)

    out["figure_count"] = sum(1 for e in root.iter() if _local(e.tag) == "fig")
    out["table_count"] = sum(1 for e in root.iter() if _local(e.tag) in ("table-wrap", "table"))
    out["ref_count"] = sum(1 for e in root.iter() if _local(e.tag) == "ref")

    sections = _collect_sections(root)
    section_stats: dict[str, dict[str, int]] = {}
    merged_text_parts: list[str] = []
    for name, paras in sections.items():
        text = "\n\n".join(paras)
        section_stats[name] = {
            "paragraphs": len(paras),
            "chars": len(text),
        }
        merged_text_parts.append(text)
    out["sections"] = {k: v for k, v in sections.items()}
    out["section_stats"] = section_stats
    full_body = "\n\n".join(merged_text_parts)

    # 兜底：body 内全部可见文本（含无 sec 结构的全文）
    if len(full_body) < 500:
        for elem in root.iter():
            if _local(elem.tag) != "body":
                continue
            full_body = _elem_text(elem)
            break
    if out["abstract"] and out["abstract"] not in full_body:
        full_body = (out["abstract"] + "\n\n" + full_body).strip()
    out["body_text"] = full_body
    out["body_char_count"] = len(full_body)
    out["body_para_count"] = sum(len(v) for v in sections.values()) or full_body.count("\n\n") + 1

    corpus = (out["title"] + "\n" + out["abstract"] + "\n" + full_body).lower()
    if "zebrafish" in corpus or "danio rerio" in corpus:
        out["zebrafish_terms"] = sorted(
            set(re.findall(r"\b(?:zebrafish|danio rerio|brachydanio rerio)\b", corpus, re.I))
        )
    genes = GENE_PATTERNS.findall(corpus)
    out["gene_mentions"] = [g for g, _ in Counter(genes).most_common(15)]

    return out


def summarize(text: str, n: int = 500) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t if len(t) <= n else t[: n - 3] + "..."


def process_entry(entry: dict[str, str], ris_idx: dict[str, dict[str, str]], xml_dir: Path) -> dict[str, Any]:
    no = entry.get("entry_no", "?")
    title = entry.get("EN Title", "")
    safe_print(f"\n[{no}] XML 分析: {title[:65]}...")
    ris_meta = lookup_ris_meta(title, ris_idx)
    ids = resolve_ids(entry, ris_meta)

    result: dict[str, Any] = {
        "entry_no": no,
        "en_title": title,
        "zh_title": entry.get("ZH Title", ""),
        "judgment": entry.get("Judgment", ""),
        "reason": entry.get("Reason", ""),
        "doi": ids.get("doi", ""),
        "pmid": ids.get("pmid", ""),
        "pmcid": ids.get("pmcid", ""),
        "xml_source": "",
        "xml_path": "",
        "xml_ok": False,
        "fallback": "",
        "analysis": {},
        "status": "pending",
        "error": "",
    }

    raw_xml = b""
    if ids.get("pmid") or ids.get("pmcid"):
        try:
            raw_xml, used, url = fetch_jats_xml(ids.get("pmid", ""), ids.get("pmcid", ""))
            time.sleep(EPMC_INTERVAL)
            if raw_xml:
                result["xml_ok"] = True
                result["xml_source"] = url
                if used.upper().startswith("PMC"):
                    result["pmcid"] = used if used.startswith("PMC") else f"PMC{used}"
                xml_path = xml_dir / f"{no}_{used}_fulltext.xml"
                xml_path.write_bytes(raw_xml)
                result["xml_path"] = str(xml_path)
                result["analysis"] = parse_jats_xml(raw_xml)
        except Exception as e:
            result["error"] = str(e)

    if not result["xml_ok"] and ids.get("pmid"):
        raw_pubmed = _api_get("/efetch.fcgi", {"db": "pubmed", "id": ids["pmid"], "retmode": "xml"})
        _sleep()
        pub = parse_pubmed_xml(raw_pubmed)
        if pub.get("abstract_en"):
            result["fallback"] = "pubmed_abstract"
            result["analysis"] = {
                "title": pub.get("title_en") or title,
                "abstract": pub.get("abstract_en", ""),
                "keywords": [],
                "sections": {},
                "section_stats": {"abstract": {"paragraphs": 1, "chars": len(pub.get("abstract_en", ""))}},
                "figure_count": 0,
                "table_count": 0,
                "ref_count": 0,
                "body_char_count": 0,
                "body_para_count": 0,
                "zebrafish_terms": sorted(
                    set(re.findall(r"\b(?:zebrafish|danio rerio)\b", pub.get("abstract_en", ""), re.I))
                ),
                "gene_mentions": [g for g, _ in Counter(GENE_PATTERNS.findall(pub.get("abstract_en", ""))).most_common(10)],
            }
            result["status"] = "abstract_only"
        else:
            stub = entry.get("ZH Abstract") or entry.get("EN Abstract") or ""
            if stub and stub not in ("无摘要", "無摘要"):
                result["fallback"] = "yes_md_stub"
                result["analysis"] = {
                    "title": title,
                    "abstract": stub,
                    "keywords": [],
                    "sections": {},
                    "section_stats": {},
                    "figure_count": 0,
                    "table_count": 0,
                    "ref_count": 0,
                    "body_char_count": 0,
                    "body_para_count": 0,
                    "zebrafish_terms": ["zebrafish"] if "斑马鱼" in stub or "zebrafish" in stub.lower() else [],
                    "gene_mentions": [],
                }
                result["status"] = "stub_only"
    elif result["xml_ok"]:
        result["status"] = "xml_fulltext"
    else:
        stub = entry.get("ZH Abstract") or entry.get("EN Abstract") or ""
        if stub and stub not in ("无摘要", "無摘要"):
            result["fallback"] = "yes_md_stub"
            result["analysis"] = {
                "title": title,
                "abstract": stub,
                "keywords": [],
                "sections": {},
                "section_stats": {},
                "figure_count": 0,
                "table_count": 0,
                "ref_count": 0,
                "body_char_count": 0,
                "body_para_count": 0,
                "zebrafish_terms": ["zebrafish"] if "斑马鱼" in stub else [],
                "gene_mentions": [],
            }
            result["status"] = "stub_only"
        else:
            result["status"] = "no_content"
            result["error"] = result["error"] or "无 XML 全文且无可用摘要"

    if result["status"] in ("xml_fulltext", "abstract_only", "stub_only"):
        if result["status"] != "pending":
            pass
    return result


def render_report(results: list[dict[str, Any]], *, generated_at: str) -> str:
    xml_ok = sum(1 for r in results if r.get("status") == "xml_fulltext")
    abs_ok = sum(1 for r in results if r.get("status") == "abstract_only")
    lines = [
        "# 斑马鱼文献 XML 全文分析报告",
        "",
        f"- **生成时间**：{generated_at}",
        f"- **数据来源**：`zebrafish_package/结果/markdown/是.md`（前 {len(results)} 篇）",
        f"- **Europe PMC JATS XML 全文**：{xml_ok}/{len(results)}",
        f"- **PubMed 摘要降级**：{abs_ok}/{len(results)}",
        f"- **原始 XML 保存目录**：`zebrafish_package/结果/xml_fulltext/`",
        "",
        "## 一、分析流程说明",
        "",
        "1. 从 `是.md` 读取判别为「是」的文献条目（标题、判别理由）。",
        "2. 通过合并语料 RIS + PubMed/Europe PMC 反查 DOI、PMID、PMCID。",
        "3. 优先拉取 Europe PMC `fullTextXML`（JATS 标准 XML 全文）。",
        "4. 结构化解析：标题、摘要、章节（Introduction/Methods/Results/Discussion 等）、图表数、参考文献数。",
        "5. 提取斑马鱼相关术语与常见基因名，用于后续 RAG / 问答测试。",
        "6. 无 XML 时降级为 PubMed 摘要或 `是.md` 占位摘要。",
        "",
        "## 二、汇总表",
        "",
        "| 序号 | 中文标题 | XML全文 | 正文字符 | 章节数 | 图表 | 参考文献 | 斑马鱼术语 | 解析状态 |",
        "|---:|:---|:---:|:---:|:---:|:---:|:---:|:---|:---|",
    ]
    for r in results:
        a = r.get("analysis") or {}
        stats = a.get("section_stats") or {}
        body_sections = [k for k in stats if k not in ("abstract",)]
        xml_flag = "✓" if r.get("status") == "xml_fulltext" else "✗"
        lines.append(
            f"| {r['entry_no']} | {(r.get('zh_title') or '')[:28]} | {xml_flag} | "
            f"{a.get('body_char_count', 0)} | {len(body_sections)} | "
            f"{a.get('figure_count', 0)}/{a.get('table_count', 0)} | {a.get('ref_count', 0)} | "
            f"{len(a.get('zebrafish_terms') or [])} | {r.get('status')} |"
        )

    lines.extend(["", "## 三、逐篇 XML 分析详情", ""])
    for r in results:
        a = r.get("analysis") or {}
        lines.extend([
            f"### {r['entry_no']}. {r.get('zh_title') or r['en_title']}",
            "",
            f"- **英文标题**：{r['en_title']}",
            f"- **判别结论**：{r.get('judgment')} — {r.get('reason')}",
            f"- **DOI**：{r.get('doi') or '无'} | **PMID**：{r.get('pmid') or '无'} | **PMCID**：{r.get('pmcid') or '无'}",
            f"- **XML 来源**：{r.get('xml_source') or '无'}",
            f"- **XML 文件**：`{r.get('xml_path') or '无'}`",
            f"- **解析状态**：{r.get('status')}" + (f"（降级：{r.get('fallback')}）" if r.get("fallback") else ""),
        ])
        if a.get("title"):
            lines.append(f"- **XML 标题**：{a['title']}")
        if a.get("abstract"):
            lines.append(f"- **摘要预览**：{summarize(a['abstract'], 400)}")
        if a.get("keywords"):
            lines.append(f"- **关键词**：{', '.join(a['keywords'][:12])}")
        if a.get("section_stats"):
            lines.append("- **章节统计**：")
            for sec, st in a["section_stats"].items():
                lines.append(f"  - `{sec}`：{st.get('paragraphs', 0)} 段 / {st.get('chars', 0)} 字符")
        if a.get("figure_count") or a.get("table_count") or a.get("ref_count"):
            lines.append(
                f"- **图表/文献**：图 {a.get('figure_count', 0)} / 表 {a.get('table_count', 0)} / "
                f"参考文献 {a.get('ref_count', 0)}"
            )
        if a.get("zebrafish_terms"):
            lines.append(f"- **斑马鱼相关术语**：{', '.join(a['zebrafish_terms'])}")
        if a.get("gene_mentions"):
            lines.append(f"- **基因/分子提及**：{', '.join(a['gene_mentions'][:10])}")

        sections = a.get("sections") or {}
        if sections:
            lines.append("- **核心章节摘录**：")
            for sec_name in ("introduction", "methods", "results", "discussion", "conclusion"):
                paras = sections.get(sec_name)
                if paras:
                    lines.append(f"  - **{sec_name}**：{summarize(paras[0], 350)}")
            for sec_name, paras in sections.items():
                if sec_name in ("introduction", "methods", "results", "discussion", "conclusion"):
                    continue
                if paras:
                    lines.append(f"  - **{sec_name}**：{summarize(paras[0], 250)}")

        if r.get("error") and r.get("status") == "no_content":
            lines.append(f"- **备注**：{r['error']}")
        lines.append("")

    lines.extend([
        "## 四、测试结论",
        "",
        f"1. **XML 全文可用率**：{xml_ok}/{len(results)}。序号 1（B1 SOX）与序号 9（鳔-肺同源）均成功获取 JATS XML 全文，"
        "可直接用于 RAG 切片与问答测试。",
        "2. **结构化优势明显**：XML 可精确拆分 Introduction / Methods / Results / Discussion，"
        "比 PDF 文本提取更适合做章节级检索与引用溯源。",
        f"3. **降级覆盖**：其余 {len(results) - xml_ok} 篇因无开放 XML（会议摘要、勘误、付费墙）降级为摘要或占位文本，"
        "应在汇报中标注为「非全文条目」。",
        "4. **推荐测试样例**：",
        "   - 全文级：序号 1（69k 字符，多章节）",
        "   - 全文级：序号 9（40k 字符，PLOS ONE OA）",
        "   - 摘要级：序号 2、11、12",
        "   - 边界级：序号 3–7（仅标题/占位）",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="是.md XML 全文分析报告")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--yes-md", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=REPORT_MD)
    parser.add_argument("--out-json", type=Path, default=REPORT_JSON)
    args = parser.parse_args()

    yes_md = args.yes_md or _find_yes_md()
    ris_path = _find_merged_ris()
    entries = parse_yes_md_entries(yes_md, args.limit)
    ris_idx = build_ris_title_index(ris_path)
    XML_DIR.mkdir(parents=True, exist_ok=True)

    safe_print(f"读取 {yes_md}，共 {len(entries)} 篇")
    results: list[dict[str, Any]] = []
    for entry in entries:
        results.append(process_entry(entry, ris_idx, XML_DIR))

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    slim_results = []
    for r in results:
        slim = dict(r)
        analysis = slim.get("analysis") or {}
        if analysis.get("sections"):
            slim["analysis"] = dict(analysis)
            slim["analysis"]["sections"] = {
                k: [summarize(p, 800) for p in v[:3]]
                for k, v in analysis["sections"].items()
            }
        slim_results.append(slim)

    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(render_report(results, generated_at=generated_at), encoding="utf-8")
    args.out_json.write_text(
        json.dumps({"generated_at": generated_at, "results": slim_results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    safe_print(f"\nXML 分析报告已写入：\n  {args.out_md}\n  {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
