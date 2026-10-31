# -*- coding: utf-8 -*-
"""
从 zebrafish_package/结果/markdown/是.md 取前 N 篇，下载 PDF、解析并生成测试汇报。

用法：
  python -m scripts.yes_md_pdf_test_report
  python -m scripts.yes_md_pdf_test_report --limit 10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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
from rag.knowledge_ingest import extract_pdf_text
from rag.literature_collector import _api_get, _sleep
from rag.network_resilience import call_with_network_recovery, urlopen_with_retry
from rag.pubmed_corpus_utils import (
    epmc_is_open_access,
    fetch_pmc_fulltext,
    parse_pubmed_xml,
)

YES_MD = ROOT / "zebrafish_package" / "结果" / "markdown" / "是.md"
MERGED_RIS = ROOT / "zebrafish_package" / "原數據" / "merged_corpus" / "merged_0000.ris"
OUT_DIR = ROOT / "zebrafish_package" / "结果" / "pdf_parse_test"
REPORT_MD = ROOT / "zebrafish_package" / "结果" / "pdf_parse_test_report.md"
REPORT_JSON = ROOT / "zebrafish_package" / "结果" / "pdf_parse_test_report.json"

PDF_URLS = [
    "https://europepmc.org/backend/ptpmcrender.fcgi?accid={pmcid}&blobtype=pdf",
    "https://europepmc.org/articles/{pmcid}?pdf=render",
]


def _find_yes_md() -> Path:
    if YES_MD.is_file():
        return YES_MD
    for p in (ROOT / "zebrafish_package").rglob("是.md"):
        return p
    raise FileNotFoundError("未找到 是.md")


def _find_merged_ris() -> Path:
    if MERGED_RIS.is_file():
        return MERGED_RIS
    for p in (ROOT / "zebrafish_package").rglob("merged_0000.ris"):
        return p
    raise FileNotFoundError("未找到 merged_0000.ris")


def _parse_yes_md_block(block: str) -> Optional[dict[str, str]]:
    m = re.search(r"### (\d+)\.", block)
    if not m:
        return None
    num = int(m.group(1))
    fields: dict[str, str] = {}
    for fm in re.finditer(r"\| (\w+(?: \w+)*) \| (.+?) \|", block):
        fields[fm.group(1)] = fm.group(2).strip()
    rec = dict(fields)
    rec["entry_no"] = str(num)
    return rec


def iter_yes_md_entries(
    path: Path,
    *,
    limit: int = 0,
    worker_id: int = 0,
    workers: int = 1,
):
    """流式迭代 是.md 条目；limit<=0 表示全部；支持 worker 分片。"""
    count = 0
    block_lines: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.strip() == "---":
                if block_lines:
                    entry = _parse_yes_md_block("\n".join(block_lines))
                    block_lines = []
                    if entry and (int(entry["entry_no"]) % workers == worker_id):
                        yield entry
                        count += 1
                        if limit > 0 and count >= limit:
                            return
                continue
            block_lines.append(line.rstrip("\n"))
        if block_lines:
            entry = _parse_yes_md_block("\n".join(block_lines))
            if entry and (int(entry["entry_no"]) % workers == worker_id):
                yield entry


def parse_yes_md_entries(path: Path, limit: int) -> list[dict[str, str]]:
    if limit <= 0:
        return list(iter_yes_md_entries(path, limit=0))
    return list(iter_yes_md_entries(path, limit=limit))


def build_ris_title_index(path: Path) -> dict[str, dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    recs = re.split(r"\n(?=TY  - )", text)
    idx: dict[str, dict[str, str]] = {}
    for rec in recs:
        title_m = re.search(r"^TI  - (.+)$", rec, re.M)
        if not title_m:
            continue
        title = title_m.group(1).strip()
        meta: dict[str, str] = {"title": title}
        for tag, key in (("DO", "doi"), ("AN", "an"), ("PY", "year"), ("T2", "journal")):
            m = re.search(rf"^{tag}  - (.+)$", rec, re.M)
            if m:
                meta[key] = m.group(1).strip()
        idx[title.lower()] = meta
    return idx


def lookup_ris_meta(title: str, ris_idx: dict[str, dict[str, str]]) -> dict[str, str]:
    key = title.strip().lower()
    if key in ris_idx:
        return ris_idx[key]
    for k, v in ris_idx.items():
        if key[:50] in k or k[:50] in key:
            return v
    return {}


def _title_similar(a: str, b: str) -> bool:
    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (s or "").lower())
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    return short in long or long[: min(len(long), 80)] in short


def _epmc_search_title(title: str) -> dict[str, str]:
    q = urllib.parse.urlencode({
        "query": f'TITLE:"{title[:180]}"',
        "format": "json",
        "pageSize": 3,
        "resultType": "core",
    })
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{q}"
    data = call_with_network_recovery(
        lambda: _epmc_json(url, label=f"EPMC title {title[:40]}"),
        label="EPMC title search",
    )
    results = (data.get("resultList") or {}).get("result") or []
    if not results:
        return {}
    for r in results:
        found = str(r.get("title") or "").strip()
        if not _title_similar(title, found):
            continue
        return {
            "pmid": str(r.get("pmid") or "").strip(),
            "pmcid": str(r.get("pmcid") or "").strip(),
            "doi": str(r.get("doi") or "").strip(),
            "journal": str(r.get("journalTitle") or "").strip(),
            "year": str(r.get("pubYear") or "").strip(),
            "is_oa": str(r.get("isOpenAccess") or "").upper() in ("Y", "YES", "TRUE"),
            "matched_title": found,
        }
    return {}


def _epmc_json(url: str, *, label: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
    with urlopen_with_retry(req, timeout=60, label=label) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ncbi_idconv(identifier: str) -> dict[str, str]:
    if not identifier:
        return {}
    q = urllib.parse.urlencode({
        "ids": identifier,
        "format": "json",
        "versions": "no",
    })
    url = f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
    try:
        with urlopen_with_retry(req, timeout=60, label=f"idconv {identifier}") as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}
    records = data.get("records") or []
    if not records:
        return {}
    r = records[0]
    return {
        "pmid": str(r.get("pmid") or "").strip(),
        "pmcid": str(r.get("pmcid") or "").strip(),
        "doi": str(r.get("doi") or "").strip(),
    }


def esearch_pmid_by_title(title: str) -> str:
    term = f"{title}[Title]"
    xml = _api_get("/esearch.fcgi", {"db": "pubmed", "term": term, "retmax": 3})
    m = re.search(r"<Id>(\d+)</Id>", xml)
    return m.group(1) if m else ""


def fetch_pubmed_xml(pmid: str) -> str:
    if not pmid:
        return ""
    xml = _api_get("/efetch.fcgi", {"db": "pubmed", "id": pmid, "retmode": "xml"})
    return xml


def normalize_pmcid(pmcid: str) -> str:
    p = (pmcid or "").strip().upper()
    if not p:
        return ""
    return p if p.startswith("PMC") else f"PMC{p}"


def download_pdf(pmcid: str, dest: Path, *, timeout: float = 20.0) -> tuple[bool, str]:
    pmcid = normalize_pmcid(pmcid)
    if not pmcid:
        return False, "无 PMCID"
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err = ""
    for tpl in PDF_URLS:
        url = tpl.format(pmcid=pmcid)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            if len(data) < 1024:
                last_err = "响应过小"
                continue
            if not data[:5].startswith(b"%PDF"):
                last_err = "非 PDF 内容"
                continue
            dest.write_bytes(data)
            return True, url
        except Exception as e:
            last_err = str(e)
            time.sleep(1.0)
    return False, last_err or "下载失败"


def summarize_text(text: str, max_chars: int = 1200) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 3] + "..."


def extract_sections(text: str) -> dict[str, str]:
    """轻量分段：摘要/方法/结果/讨论/结论。"""
    if not text:
        return {}
    patterns = [
        ("abstract", r"(?is)\babstract\b[:\s]*(.{80,2500}?)(?=\b(?:introduction|background|keywords|materials)\b)"),
        ("introduction", r"(?is)\bintroduction\b[:\s]*(.{80,3000}?)(?=\b(?:materials|methods|results)\b)"),
        ("methods", r"(?is)\b(?:materials?\s+and\s+methods?|methods?)\b[:\s]*(.{80,3000}?)(?=\bresults\b)"),
        ("results", r"(?is)\bresults\b[:\s]*(.{80,3000}?)(?=\b(?:discussion|conclusions?)\b)"),
        ("discussion", r"(?is)\bdiscussion\b[:\s]*(.{80,2500}?)(?=\b(?:conclusions?|references|acknowledg)\b)"),
        ("conclusion", r"(?is)\bconclusions?\b[:\s]*(.{80,1500}?)(?=\b(?:references|acknowledg)\b)"),
    ]
    out: dict[str, str] = {}
    for name, pat in patterns:
        m = re.search(pat, text)
        if m:
            out[name] = summarize_text(m.group(1), 800)
    return out


def resolve_ids(entry: dict[str, str], ris_meta: dict[str, str]) -> dict[str, Any]:
    title = entry.get("EN Title", "")
    doi = ris_meta.get("doi", "")
    pmid = ""
    pmcid = ""
    an = ris_meta.get("an", "")
    epmc: dict[str, Any] = {}
    if an.startswith("MEDLINE:"):
        pmid = an.split(":", 1)[1]

    if doi:
        conv = ncbi_idconv(doi)
        _sleep()
        pmid = pmid or conv.get("pmid", "")
        pmcid = pmcid or conv.get("pmcid", "")
    if pmid and not pmcid:
        conv = ncbi_idconv(pmid)
        _sleep()
        pmcid = pmcid or conv.get("pmcid", "")
    if not pmid:
        pmid = esearch_pmid_by_title(title)
        _sleep()
        if pmid and not pmcid:
            conv = ncbi_idconv(pmid)
            _sleep()
            pmcid = conv.get("pmcid", "")
    if not pmcid and pmid:
        oa, pmc_from = epmc_is_open_access(pmid)
        time.sleep(EPMC_INTERVAL)
        if pmc_from:
            pmcid = pmc_from
    if not pmid or not doi:
        epmc = _epmc_search_title(title)
        time.sleep(EPMC_INTERVAL)
        pmid = pmid or epmc.get("pmid", "")
        pmcid = pmcid or epmc.get("pmcid", "")
        doi = doi or epmc.get("doi", "")

    return {
        "title_en": title,
        "doi": doi,
        "pmid": pmid,
        "pmcid": normalize_pmcid(pmcid),
        "wos_ut": an if an.startswith("WOS:") else "",
        "journal": ris_meta.get("journal") or epmc.get("journal", ""),
        "year": ris_meta.get("year") or epmc.get("year", ""),
        "is_oa": epmc.get("is_oa", False),
    }


def process_entry(entry: dict[str, str], ris_idx: dict[str, dict[str, str]], out_dir: Path) -> dict[str, Any]:
    no = entry.get("entry_no", "?")
    title = entry.get("EN Title", "")
    safe_print(f"\n[{no}] {title[:70]}...")
    ris_meta = lookup_ris_meta(title, ris_idx)
    ids = resolve_ids(entry, ris_meta)

    result: dict[str, Any] = {
        "entry_no": no,
        "en_title": title,
        "zh_title": entry.get("ZH Title", ""),
        "judgment": entry.get("Judgment", ""),
        "reason": entry.get("Reason", ""),
        "zh_abstract_stub": entry.get("ZH Abstract", ""),
        "en_abstract_stub": entry.get("EN Abstract", ""),
        **ids,
        "pdf_path": "",
        "pdf_download_ok": False,
        "pdf_download_note": "",
        "parse_source": "",
        "char_count": 0,
        "sections": {},
        "text_preview": "",
        "status": "pending",
        "error": "",
    }

    pdf_path = out_dir / f"{no}_{_safe_slug(title)}.pdf"
    text = ""
    parse_source = ""

    if ids.get("pmid"):
        raw_xml = fetch_pubmed_xml(ids["pmid"])
        _sleep()
        pub = parse_pubmed_xml(raw_xml)
        if pub.get("abstract_en"):
            text = pub["abstract_en"]
            parse_source = "pubmed_abstract"
        body, used_pmc, ok_ft = fetch_pmc_fulltext(ids["pmid"], ids.get("pmcid", ""))
        time.sleep(EPMC_INTERVAL)
        if body.strip():
            text = body
            parse_source = "pmc_xml"
            if used_pmc:
                result["pmcid"] = normalize_pmcid(used_pmc)

    if ids.get("pmcid"):
        ok, note = download_pdf(ids["pmcid"], pdf_path, timeout=20.0)
        result["pdf_download_ok"] = ok
        result["pdf_download_note"] = note
        if ok:
            result["pdf_path"] = str(pdf_path)
            try:
                pdf_text = extract_pdf_text(str(pdf_path))
                if len(pdf_text.strip()) > len(text.strip()):
                    text = pdf_text
                    parse_source = "pdf"
            except Exception as e:
                result["error"] = f"PDF解析失败: {e}"
        time.sleep(1.0)

    if not text.strip():
        for key in ("EN Abstract", "ZH Abstract"):
            stub = entry.get(key, "")
            if stub and stub not in ("无摘要", "無摘要"):
                text = stub
                parse_source = "yes_md_stub"
                break

    result["parse_source"] = parse_source
    result["char_count"] = len(text)
    result["sections"] = extract_sections(text)
    result["text_preview"] = summarize_text(text, 1500)
    if text.strip():
        result["status"] = "ok"
    else:
        result["status"] = "no_text"
        result["error"] = result["error"] or "未能获取可解析正文"
    return result


def _safe_slug(title: str, max_len: int = 40) -> str:
    s = re.sub(r"[^\w\-]+", "_", title, flags=re.UNICODE).strip("_")
    return (s[:max_len] or "paper").lower()


def render_report_md(results: list[dict[str, Any]], *, generated_at: str) -> str:
    ok_pdf = sum(1 for r in results if r.get("pdf_download_ok"))
    ok_text = sum(1 for r in results if r.get("status") == "ok")
    lines = [
        "# 斑马鱼文献 PDF 解析测试汇报",
        "",
        f"- 生成时间：{generated_at}",
        f"- 数据来源：`zebrafish_package/结果/markdown/是.md`（前 {len(results)} 篇）",
        f"- PDF 成功下载：{ok_pdf}/{len(results)}",
        f"- 正文/摘要解析成功：{ok_text}/{len(results)}",
        "",
        "## 汇总表",
        "",
        "| 序号 | 英文标题 | PMID | PMCID | PDF | 解析来源 | 字符数 | 判别理由 |",
        "|---:|---|---:|---|:---:|:---:|---:|---|",
    ]
    for r in results:
        pdf_flag = "✓" if r.get("pdf_download_ok") else "✗"
        lines.append(
            f"| {r['entry_no']} | {r['en_title'][:55]} | {r.get('pmid') or '-'} | "
            f"{r.get('pmcid') or '-'} | {pdf_flag} | {r.get('parse_source') or '-'} | "
            f"{r.get('char_count', 0)} | {r.get('reason', '')[:30]} |"
        )
    lines.extend(["", "## 逐篇详情", ""])
    for r in results:
        lines.extend([
            f"### {r['entry_no']}. {r.get('zh_title') or r['en_title']}",
            "",
            f"- **英文标题**：{r['en_title']}",
            f"- **判别**：{r.get('judgment')} — {r.get('reason')}",
            f"- **DOI**：{r.get('doi') or '无'}",
            f"- **PMID**：{r.get('pmid') or '无'}",
            f"- **PMCID**：{r.get('pmcid') or '无'}",
            f"- **PDF 下载**：{'成功' if r.get('pdf_download_ok') else '失败'} ({r.get('pdf_download_note') or '无'})",
            f"- **解析来源**：{r.get('parse_source') or '无'}",
            f"- **提取字符数**：{r.get('char_count', 0)}",
        ])
        if r.get("pdf_path"):
            lines.append(f"- **PDF 路径**：`{r['pdf_path']}`")
        if r.get("sections"):
            lines.append("- **结构化分段**：")
            for k, v in r["sections"].items():
                lines.append(f"  - {k}：{v[:200]}...")
        if r.get("text_preview"):
            lines.extend(["", "**正文/摘要预览**", "", f"> {r['text_preview']}", ""])
        if r.get("error") and r.get("status") != "ok":
            lines.append(f"- **备注**：{r['error']}")
        lines.append("")
    lines.extend([
        "## 测试结论（自动生成）",
        "",
        "1. 流水线可从「是.md」条目反查 DOI/PMID/PMCID 并尝试开放获取 PDF。",
        f"2. 本批 {len(results)} 篇中 PDF 直链成功率 {ok_pdf}/{len(results)}，"
        f"含 PMC XML / PubMed 摘要降级后文本获取率 {ok_text}/{len(results)}。",
        "3. 老文献、会议摘要类条目常无 OA PDF，需依赖 Europe PMC XML 或摘要补全。",
        "4. 建议汇报时展示：序号 9（PLOS ONE，OA 全文）与序号 7（Toxicology Letters）作为成功样例；",
        "   序号 1/2（会议摘要、无 DOI）作为边界样例。",
        "",
    ])
    return "\n".join(lines)


def _save_outputs(
    results: list[dict[str, Any]],
    *,
    out_md: Path,
    out_json: Path,
    generated_at: str,
) -> None:
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_report_md(results, generated_at=generated_at), encoding="utf-8")
    out_json.write_text(
        json.dumps({"generated_at": generated_at, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="是.md 前 N 篇 PDF 下载解析测试汇报")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--yes-md", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=REPORT_MD)
    parser.add_argument("--out-json", type=Path, default=REPORT_JSON)
    parser.add_argument("--resume", action="store_true", help="跳过已在 JSON 中完成的条目")
    args = parser.parse_args()

    yes_md = args.yes_md or _find_yes_md()
    ris_path = _find_merged_ris()
    entries = parse_yes_md_entries(yes_md, args.limit)
    ris_idx = build_ris_title_index(ris_path)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    done: dict[str, dict[str, Any]] = {}
    if args.resume and args.out_json.is_file():
        try:
            prev = json.loads(args.out_json.read_text(encoding="utf-8"))
            for r in prev.get("results") or []:
                if r.get("status") in ("ok", "no_text"):
                    done[str(r.get("entry_no"))] = r
        except json.JSONDecodeError:
            pass

    safe_print(f"读取 {yes_md}，共 {len(entries)} 篇（已完成 {len(done)}）")
    results: list[dict[str, Any]] = []
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for entry in entries:
        no = entry.get("entry_no", "?")
        if no in done:
            safe_print(f"[{no}] 跳过（已完成）")
            results.append(done[no])
            continue
        result = process_entry(entry, ris_idx, OUT_DIR)
        results.append(result)
        _save_outputs(results, out_md=args.out_md, out_json=args.out_json, generated_at=generated_at)

    _save_outputs(results, out_md=args.out_md, out_json=args.out_json, generated_at=generated_at)
    safe_print(f"\n汇报已写入：\n  {args.out_md}\n  {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
