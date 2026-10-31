# -*- coding: utf-8 -*-
"""
是.md 文献全文级联下载：最大化 Europe PMC XML 全文获取。

优先级：
  ① Europe PMC JATS XML
  ② Unpaywall → OA PDF（并反查 PMCID 重试 XML）
  ③ OpenAlex → OA PDF（并反查 PMCID 重试 XML）
  ④ 出版社直链 PDF（PLOS/Frontiers/BMC）
  ⑤ PubMed 摘要 + 是.md 判别信息

用法：
  python -m scripts.yes_md_fulltext_cascade --limit 10
  python -m scripts.yes_md_fulltext_cascade --limit 100 --resume
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from rag.fulltext_cascade import (
    extract_pmcid_from_urls,
    fetch_jats_xml_cascade,
    jats_stats,
    normalize_pmcid,
    openalex_lookup,
    publisher_pdf_urls,
    resolve_all_ids,
    try_download_pdf,
    unpaywall_lookup,
)
from rag.knowledge_ingest import extract_pdf_text
from rag.literature_collector import _api_get, _sleep
from rag.pubmed_corpus_utils import parse_pubmed_xml
from scripts.yes_md_pdf_test_report import (
    _find_merged_ris,
    _find_yes_md,
    build_ris_title_index,
    lookup_ris_meta,
    parse_yes_md_entries,
)

OUT_XML = ROOT / "zebrafish_package" / "结果" / "fulltext_cascade" / "xml"
OUT_PDF = ROOT / "zebrafish_package" / "结果" / "fulltext_cascade" / "pdf"
REPORT_MD = ROOT / "zebrafish_package" / "结果" / "fulltext_cascade_report.md"
REPORT_JSON = ROOT / "zebrafish_package" / "结果" / "fulltext_cascade_report.json"
PROGRESS_JSON = ROOT / "zebrafish_package" / "结果" / "fulltext_cascade_progress.json"


def _safe_slug(title: str, n: int = 36) -> str:
    s = re.sub(r"[^\w\-]+", "_", title, flags=re.UNICODE).strip("_")
    return (s[:n] or "paper").lower()


def fetch_pubmed_abstract(pmid: str) -> dict[str, str]:
    if not pmid:
        return {}
    raw = _api_get("/efetch.fcgi", {"db": "pubmed", "id": pmid, "retmode": "xml"})
    _sleep()
    return parse_pubmed_xml(raw)


def cascade_download(entry: dict[str, str], ris_idx: dict[str, dict[str, str]]) -> dict[str, Any]:
    no = entry.get("entry_no", "?")
    title = entry.get("EN Title", "")
    safe_print(f"\n[{no}] 级联下载: {title[:70]}")
    ris_meta = lookup_ris_meta(title, ris_idx)
    ids = resolve_all_ids(
        title,
        doi=ris_meta.get("doi", ""),
        wos_an=ris_meta.get("an", ""),
    )

    result: dict[str, Any] = {
        "entry_no": no,
        "en_title": title,
        "zh_title": entry.get("ZH Title", ""),
        "judgment": entry.get("Judgment", ""),
        "reason": entry.get("Reason", ""),
        "doi": ids.get("doi", ""),
        "pmid": ids.get("pmid", ""),
        "pmcid": ids.get("pmcid", ""),
        "pmcid_candidates": ids.get("pmcid_candidates", []),
        "id_trace": ids.get("trace", []),
        "content_type": "",
        "source_step": "",
        "source_url": "",
        "file_path": "",
        "text_chars": 0,
        "xml_stats": {},
        "status": "pending",
        "attempts": [],
        "error": "",
    }

    def attempt(step: str, ok: bool, note: str = "") -> None:
        result["attempts"].append({"step": step, "ok": ok, "note": note[:200]})

    # ① Europe PMC XML — 遍历全部 PMCID 候选
    pmcid_list = list(dict.fromkeys(
        [ids.get("pmcid", "")]
        + ids.get("pmcid_candidates", [])
        + [extract_pmcid_from_urls(
            (ids.get("unpaywall") or {}).get("pdf_urls", [])
            + (ids.get("unpaywall") or {}).get("landing_urls", [])
            + (ids.get("openalex") or {}).get("pdf_urls", [])
            + (ids.get("openalex") or {}).get("landing_urls", [])
        )]
    ))
    pmcid_list = [normalize_pmcid(x) for x in pmcid_list if x]

    for pmcid in pmcid_list or [""]:
        raw, used, url, xml_src = fetch_jats_xml_cascade(
            pmid=ids.get("pmid", ""),
            pmcid=pmcid,
            doi=ids.get("doi", ""),
            pmcid_candidates=ids.get("pmcid_candidates", []),
        )
        time.sleep(0.5)
        if raw:
            fname = f"{no}_{used or pmcid or 'epmc'}_fulltext.xml"
            path = OUT_XML / fname
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            stats = jats_stats(raw)
            result.update({
                "content_type": "xml",
                "source_step": xml_src,
                "source_url": url,
                "file_path": str(path),
                "text_chars": stats.get("chars", 0),
                "xml_stats": stats,
                "pmcid": normalize_pmcid(used if used.upper().startswith("PMC") else pmcid),
                "status": "ok",
            })
            attempt(xml_src, True, url)
            safe_print(f"  ✓ XML {stats.get('chars', 0)} chars via {xml_src}/{used}")
            return result
        attempt("epmc_xml+ncbi", False, f"pmcid={pmcid or '-'}")

    # ② Unpaywall PDF + 反查 XML
    up = ids.get("unpaywall") or unpaywall_lookup(ids.get("doi", ""))
    for pdf_url in up.get("pdf_urls", []):
        pmc_from = extract_pmcid_from_urls([pdf_url] + up.get("landing_urls", []))
        if pmc_from and pmc_from not in pmcid_list:
            raw, used, url, xml_src = fetch_jats_xml_cascade(pmcid=pmc_from, pmid=ids.get("pmid", ""))
            if raw:
                path = OUT_XML / f"{no}_{used}_fulltext.xml"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                stats = jats_stats(raw)
                result.update({
                    "content_type": "xml",
                    "source_step": f"unpaywall→{xml_src}",
                    "source_url": url,
                    "file_path": str(path),
                    "text_chars": stats.get("chars", 0),
                    "xml_stats": stats,
                    "pmcid": pmc_from,
                    "status": "ok",
                })
                attempt(f"unpaywall→{xml_src}", True, url)
                safe_print(f"  ✓ XML via Unpaywall PMCID {pmc_from}")
                return result
        dest = OUT_PDF / f"{no}_unpaywall.pdf"
        ok, note = try_download_pdf(pdf_url, str(dest))
        attempt("unpaywall_pdf", ok, note)
        if ok:
            text = extract_pdf_text(str(dest))
            result.update({
                "content_type": "pdf",
                "source_step": "unpaywall_pdf",
                "source_url": pdf_url,
                "file_path": str(dest),
                "text_chars": len(text),
                "status": "ok",
            })
            safe_print(f"  ✓ PDF via Unpaywall {len(text)} chars")
            return result

    # ③ OpenAlex PDF + 反查 XML
    oa = ids.get("openalex") or openalex_lookup(
        doi=ids.get("doi", ""),
        pmid=ids.get("pmid", ""),
        title=title,
    )
    for pdf_url in oa.get("pdf_urls", []):
        pmc_from = extract_pmcid_from_urls([pdf_url] + oa.get("landing_urls", []))
        if pmc_from:
            raw, used, url, xml_src = fetch_jats_xml_cascade(pmcid=pmc_from, pmid=ids.get("pmid", ""))
            if raw:
                path = OUT_XML / f"{no}_{used}_fulltext.xml"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                stats = jats_stats(raw)
                result.update({
                    "content_type": "xml",
                    "source_step": f"openalex→{xml_src}",
                    "source_url": url,
                    "file_path": str(path),
                    "text_chars": stats.get("chars", 0),
                    "xml_stats": stats,
                    "pmcid": pmc_from,
                    "status": "ok",
                })
                attempt(f"openalex→{xml_src}", True, url)
                safe_print(f"  ✓ XML via OpenAlex PMCID {pmc_from}")
                return result
        dest = OUT_PDF / f"{no}_openalex.pdf"
        ok, note = try_download_pdf(pdf_url, str(dest))
        attempt("openalex_pdf", ok, note)
        if ok:
            text = extract_pdf_text(str(dest))
            result.update({
                "content_type": "pdf",
                "source_step": "openalex_pdf",
                "source_url": pdf_url,
                "file_path": str(dest),
                "text_chars": len(text),
                "status": "ok",
            })
            safe_print(f"  ✓ PDF via OpenAlex {len(text)} chars")
            return result

    # ④ 出版社直链
    for pdf_url in publisher_pdf_urls(ids.get("doi", "")):
        pmc_from = extract_pmcid_from_urls([pdf_url])
        if pmc_from:
            raw, used, url, xml_src = fetch_jats_xml_cascade(pmcid=pmc_from)
            if raw:
                path = OUT_XML / f"{no}_{used}_fulltext.xml"
                path.write_bytes(raw)
                stats = jats_stats(raw)
                result.update({
                    "content_type": "xml",
                    "source_step": f"publisher→{xml_src}",
                    "source_url": url,
                    "file_path": str(path),
                    "text_chars": stats.get("chars", 0),
                    "xml_stats": stats,
                    "status": "ok",
                })
                attempt(f"publisher→{xml_src}", True, url)
                safe_print(f"  ✓ XML via publisher PMCID")
                return result
        dest = OUT_PDF / f"{no}_publisher.pdf"
        ok, note = try_download_pdf(pdf_url, str(dest))
        attempt("publisher_pdf", ok, note)
        if ok:
            text = extract_pdf_text(str(dest))
            result.update({
                "content_type": "pdf",
                "source_step": "publisher_pdf",
                "source_url": pdf_url,
                "file_path": str(dest),
                "text_chars": len(text),
                "status": "ok",
            })
            safe_print(f"  ✓ PDF via publisher")
            return result

    # ⑤ 摘要降级
    if ids.get("pmid"):
        pub = fetch_pubmed_abstract(ids["pmid"])
        if pub.get("abstract_en"):
            result.update({
                "content_type": "abstract",
                "source_step": "pubmed_abstract",
                "text_chars": len(pub["abstract_en"]),
                "status": "degraded",
                "abstract_preview": pub["abstract_en"][:500],
            })
            attempt("pubmed_abstract", True)
            safe_print(f"  △ 摘要降级 {len(pub['abstract_en'])} chars")
            return result

    for key in ("EN Abstract", "ZH Abstract"):
        stub = entry.get(key, "")
        if stub and stub not in ("无摘要", "無摘要"):
            result.update({
                "content_type": "stub",
                "source_step": "yes_md_stub",
                "text_chars": len(stub),
                "status": "degraded",
                "abstract_preview": stub[:300],
            })
            attempt("yes_md_stub", True)
            safe_print(f"  △ 占位降级")
            return result

    result["status"] = "failed"
    result["error"] = "所有途径均未获取全文"
    attempt("all", False, result["error"])
    safe_print(f"  ✗ 失败")
    return result


def render_report(results: list[dict[str, Any]], *, generated_at: str) -> str:
    xml_n = sum(1 for r in results if r.get("content_type") == "xml")
    pdf_n = sum(1 for r in results if r.get("content_type") == "pdf")
    deg_n = sum(1 for r in results if r.get("status") == "degraded")
    fail_n = sum(1 for r in results if r.get("status") == "failed")
    lines = [
        "# 斑马鱼文献全文级联下载报告",
        "",
        f"- **生成时间**：{generated_at}",
        f"- **Europe PMC XML 全文**：{xml_n}/{len(results)}",
        f"- **PDF 全文（降级）**：{pdf_n}/{len(results)}",
        f"- **摘要/占位降级**：{deg_n}/{len(results)}",
        f"- **完全失败**：{fail_n}/{len(results)}",
        f"- **XML 目录**：`zebrafish_package/结果/fulltext_cascade/xml/`",
        f"- **PDF 目录**：`zebrafish_package/结果/fulltext_cascade/pdf/`",
        "",
        "## 级联优先级",
        "",
        "```",
        "DOI/PMID",
        "  ↓",
        "① Europe PMC XML / NCBI PMC efetch（最快最稳）",
        "  ↓ 失败",
        "② Unpaywall → OA PDF（并反查 PMCID 重试 XML）",
        "  ↓ 失败",
        "③ OpenAlex → OA PDF（并反查 PMCID 重试 XML）",
        "  ↓ 失败",
        "④ 出版社规则直链（PLOS/Frontiers/BMC）",
        "  ↓ 失败",
        "⑤ PubMed 摘要 + 是.md 判别信息（降级）",
        "```",
        "",
        "## 汇总表",
        "",
        "| 序号 | 标题 | DOI | PMID | PMCID | 类型 | 来源步骤 | 字符数 | 状态 |",
        "|---:|:---|:---|---:|---|:---:|:---|---:|:---|",
    ]
    for r in results:
        lines.append(
            f"| {r['entry_no']} | {(r.get('zh_title') or r['en_title'])[:24]} | "
            f"{(r.get('doi') or '-')[:22]} | {r.get('pmid') or '-'} | {r.get('pmcid') or '-'} | "
            f"{r.get('content_type') or '-'} | {r.get('source_step') or '-'} | "
            f"{r.get('text_chars', 0)} | {r.get('status')} |"
        )
    lines.extend(["", "## 逐篇详情", ""])
    for r in results:
        lines.extend([
            f"### {r['entry_no']}. {r.get('zh_title') or r['en_title']}",
            "",
            f"- DOI: {r.get('doi') or '无'} | PMID: {r.get('pmid') or '无'} | PMCID: {r.get('pmcid') or '无'}",
            f"- 结果: **{r.get('content_type')}** via `{r.get('source_step')}` ({r.get('text_chars', 0)} chars)",
            f"- 文件: `{r.get('file_path') or '无'}`",
            f"- ID 追踪: {', '.join(r.get('id_trace') or [])}",
        ])
        if r.get("xml_stats"):
            s = r["xml_stats"]
            lines.append(f"- XML 结构: {s.get('sections', 0)} 节 / 图{s.get('figures', 0)} / 表{s.get('tables', 0)} / 参考文献{s.get('refs', 0)}")
        if r.get("attempts"):
            lines.append("- 尝试记录:")
            for a in r["attempts"]:
                mark = "✓" if a["ok"] else "✗"
                lines.append(f"  - {mark} {a['step']}: {a.get('note', '')}")
        if r.get("abstract_preview"):
            lines.append(f"- 降级摘要: {r['abstract_preview'][:300]}...")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="是.md 全文级联下载")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--yes-md", type=Path, default=None)
    args = parser.parse_args()

    yes_md = args.yes_md or _find_yes_md()
    ris_idx = build_ris_title_index(_find_merged_ris())
    entries = parse_yes_md_entries(yes_md, args.limit)
    OUT_XML.mkdir(parents=True, exist_ok=True)
    OUT_PDF.mkdir(parents=True, exist_ok=True)

    done: dict[str, dict[str, Any]] = {}
    if args.resume and PROGRESS_JSON.is_file():
        try:
            prev = json.loads(PROGRESS_JSON.read_text(encoding="utf-8"))
            for r in prev.get("results") or []:
                if r.get("status") in ("ok", "degraded", "failed"):
                    done[str(r["entry_no"])] = r
        except json.JSONDecodeError:
            pass

    safe_print(f"共 {len(entries)} 篇，已完成 {len(done)}")
    results: list[dict[str, Any]] = []
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for entry in entries:
        no = entry["entry_no"]
        if no in done:
            safe_print(f"[{no}] 跳过")
            results.append(done[no])
            continue
        r = cascade_download(entry, ris_idx)
        results.append(r)
        PROGRESS_JSON.write_text(
            json.dumps({"generated_at": generated_at, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    REPORT_MD.write_text(render_report(results, generated_at=generated_at), encoding="utf-8")
    REPORT_JSON.write_text(
        json.dumps({"generated_at": generated_at, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    xml_n = sum(1 for r in results if r.get("content_type") == "xml")
    safe_print(f"\n完成: XML {xml_n}/{len(results)}")
    safe_print(f"报告: {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
