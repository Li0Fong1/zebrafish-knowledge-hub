# -*- coding: utf-8 -*-
"""在线拉取全文（内存解析，不落盘 PDF/XML 文件）。"""
from __future__ import annotations

import io
import json
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from rag.fulltext_cascade import (
    EPMC_REST,
    epmc_search,
    fetch_jats_xml_cascade,
    jats_stats,
    normalize_doi,
    normalize_pmcid,
    openalex_lookup,
    publisher_pdf_urls,
    resolve_all_ids,
    unpaywall_lookup,
)
from rag.literature_collector import _api_get, _sleep
from rag.network_resilience import urlopen_with_retry
from rag.pubmed_corpus_utils import _jats_body_to_text, fetch_pmc_fulltext

FULLTEXT_MAX_CHARS = int(__import__("os").getenv("ONLINE_FULLTEXT_MAX_CHARS", "80000"))
# 合格条件二选一：①解析出全文结构；②可用正文 ≥3000 字
MIN_USABLE_BODY = int(__import__("os").getenv("ONLINE_FULLTEXT_MIN_CHARS", "3000"))
MIN_FULLTEXT_CHARS = MIN_USABLE_BODY  # 兼容旧名
MIN_PARSED_BODY = int(__import__("os").getenv("ONLINE_FULLTEXT_MIN_PARSED", "800"))
# 快模式：短超时、少重试、不入断网死等；默认开启（可 ONLINE_FULLTEXT_FAST=0 关闭）
FAST_DEFAULT = __import__("os").getenv("ONLINE_FULLTEXT_FAST", "1").strip() not in ("0", "false", "False")
FAST_TIMEOUT = float(__import__("os").getenv("ONLINE_FULLTEXT_TIMEOUT", "12"))
FAST_RETRIES = int(__import__("os").getenv("ONLINE_FULLTEXT_RETRIES", "3"))
FAST_URL_WORKERS = int(__import__("os").getenv("ONLINE_FULLTEXT_URL_WORKERS", "4"))
# 单篇累计 403/网络失败达到上限则标记 missed 并跳过剩余源
HARD_FAIL_LIMIT = int(__import__("os").getenv("ONLINE_FULLTEXT_HARD_FAIL_LIMIT", "3"))


def html_to_text(raw: bytes) -> str:
    if not raw.strip():
        return ""
    try:
        from lxml import html as lhtml

        tree = lhtml.fromstring(raw)
        for xp in ("//script", "//style", "//nav", "//header", "//footer", "//aside"):
            for node in tree.xpath(xp):
                node.drop_tree()
        text = tree.text_content()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", errors="replace"))
    return " ".join(text.split())


def fetch_url_bytes(
    url: str,
    *,
    timeout: float | None = None,
    accept: str = "",
    fast: bool = False,
) -> tuple[bytes, int, str]:
    headers = {"User-Agent": "ZebrafishGeneQA/1.0 (research bot)"}
    if accept:
        headers["Accept"] = accept
    if timeout is None:
        timeout = FAST_TIMEOUT if fast else 45.0
    max_retries = FAST_RETRIES if fast else 6
    try:
        req = urllib.request.Request(url, headers=headers)
        with urlopen_with_retry(
            req,
            timeout=timeout,
            max_retries=max_retries,
            backoff=1.5 if fast else 3.0,
            wait_on_disconnect=not fast,
            label=url[:60],
        ) as resp:
            return resp.read(), getattr(resp, "status", 200) or 200, ""
    except urllib.error.HTTPError as e:
        return b"", e.code, str(e)
    except Exception as e:
        return b"", 0, str(e)


def extract_pdf_bytes(data: bytes) -> str:
    if not data.startswith(b"%PDF"):
        return ""
    try:
        from pypdf import PdfReader

        max_pages = int(__import__("os").getenv("WEB_UPLOAD_MAX_PDF_PAGES", "80"))
        reader = PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages[:max_pages]:
            t = (page.extract_text() or "").strip()
            if t:
                parts.append(t)
        return "\n\n".join(parts)
    except Exception:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
                tmp.write(data)
                tmp.flush()
                from rag.knowledge_ingest import extract_pdf_text

                return extract_pdf_text(tmp.name)
        except Exception:
            return ""


def _text_from_raw(raw: bytes) -> tuple[str, str]:
    if not raw.strip():
        return "", "unknown"
    if raw[:5].startswith(b"%PDF"):
        return extract_pdf_bytes(raw), "pdf"
    head = raw[:4000].lower()
    if b"<article" in head or b"article-title" in head:
        return _jats_body_to_text(raw), "xml"
    if b"<html" in head or b"<body" in head:
        return html_to_text(raw), "html"
    txt = raw.decode("utf-8", errors="replace").strip()
    if txt.startswith("{"):
        try:
            data = json.loads(txt)
            if isinstance(data, dict):
                for key in ("data", "content", "fullText", "body", "abstract"):
                    val = data.get(key)
                    if isinstance(val, str) and len(val) > 200:
                        return val[:FULLTEXT_MAX_CHARS], "json"
        except json.JSONDecodeError:
            pass
    return " ".join(txt.split())[:FULLTEXT_MAX_CHARS], "text"


def _is_hard_network_fail(code: int | str, err: str = "") -> bool:
    """403/401/429 或连接类失败，计入硬失败上限。"""
    try:
        c = int(code)
    except (TypeError, ValueError):
        c = 0
    if c in (401, 403, 407, 429, 502, 503, 504):
        return True
    note = f"{code} {err}".lower()
    keys = (
        "403",
        "401",
        "429",
        "urlerror",
        "connectionreset",
        "timed out",
        "timeout",
        "remote end closed",
        "connection aborted",
        "network is unreachable",
        "name or service not known",
        "getaddrinfo",
    )
    return any(k in note for k in keys)


def _hard_fail_label(code: int | str, err: str = "") -> str:
    try:
        c = int(code)
    except (TypeError, ValueError):
        c = 0
    if c in (401, 403, 407):
        return f"HTTP {c}"
    if c == 429:
        return "HTTP 429"
    if c in (502, 503, 504):
        return f"HTTP {c}"
    return (err or str(code) or "network_error")[:80]


def _is_supplement_only(text: str) -> bool:
    low = text.lower()
    if "morpholino oligonucleotide" in low and len(text) < 1500:
        return True
    if "sequences are as follows" in low and len(text) < 2000:
        return True
    return False


_ABSTRACT_HOST_MARKERS = (
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov/pubmed",
)


def _is_abstract_landing_url(url: str) -> bool:
    low = (url or "").lower()
    return any(m in low for m in _ABSTRACT_HOST_MARKERS)


def _looks_like_abstract_only(text: str) -> bool:
    """摘要页 / PubMed 记录包装（即使 ≥3000 字也不算全文）。"""
    low = (text or "").lower()
    n = len(text or "")
    # PubMed 导出包装：Abstract + MeSH / Publication types
    if "mesh terms" in low and ("publication types" in low or "substances" in low) and n < 12000:
        return True
    if "no abstract available" in low and n < 4000:
        return True
    if n >= MIN_USABLE_BODY:
        return False
    if re.search(r"\babstract\b", low) and not re.search(
        r"\b(introduction|methods?|results?|discussion|references)\b", low
    ):
        return True
    return False


def _is_parsed_fulltext(ct: str, text: str, *, raw: bytes = b"", stats: dict[str, Any] | None = None) -> bool:
    """成功解析出全文结构（JATS/XML 或 PDF 抽出正文）。"""
    n = len(text or "")
    if n < MIN_PARSED_BODY:
        return False
    if ct == "xml":
        st = stats or {}
        # JATS：有段落/章节/图表，或抽出正文足够长
        if int(st.get("sections") or 0) >= 1 or int(st.get("figures") or 0) >= 1:
            return True
        if int(st.get("chars") or 0) >= MIN_PARSED_BODY:
            return True
        if raw and (b"<body" in raw[:8000].lower() or b"<sec" in raw[:8000].lower()):
            return n >= MIN_PARSED_BODY
        return False
    if ct == "pdf":
        return n >= MIN_PARSED_BODY
    return False


def _reject_non_fulltext(
    text: str,
    ct: str,
    *,
    raw: bytes = b"",
    stats: dict[str, Any] | None = None,
    url: str = "",
) -> str | None:
    """
    非全文则返回原因；合格返回 None。
    合格条件：①解析出全文；或 ②可用正文 ≥3000 字（排除摘要页）。
    """
    if not text or not text.strip():
        return "非全文：空文本"
    if _is_abstract_landing_url(url):
        return "非全文：PubMed 摘要页"
    if _is_supplement_only(text):
        return "非全文：疑似补充材料"
    if _looks_like_abstract_only(text):
        return "非全文：疑似仅摘要"
    if _is_parsed_fulltext(ct, text, raw=raw, stats=stats):
        return None
    if len(text) >= MIN_USABLE_BODY:
        return None
    return f"非全文：未解析出全文且正文不足 {len(text)}<{MIN_USABLE_BODY}"


def _try_bytes(raw: bytes, *, step: str, url: str) -> tuple[str, str] | None:
    text, ct = _text_from_raw(raw)
    stats = jats_stats(raw) if ct == "xml" and raw else {}
    if _reject_non_fulltext(text, ct, raw=raw if ct == "xml" else b"", stats=stats, url=url):
        return None
    return text, ct


def _collect_url_candidates(
    ids: dict[str, Any],
    *,
    doi: str,
    fast: bool = False,
) -> list[tuple[str, str]]:
    """(step_label, url) 按优先级。快模式只保留高命中 OA 源。"""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(step: str, url: str) -> None:
        url = (url or "").strip()
        if not url or url in seen:
            return
        # 跳过仅摘要的 PubMed 落地页，避免假命中
        if _is_abstract_landing_url(url):
            return
        seen.add(url)
        out.append((step, url))

    if doi:
        up = ids.get("unpaywall") or unpaywall_lookup(doi)
        pdf_n, land_n = (1, 1) if fast else (3, 2)
        for u in (up.get("pdf_urls") or [])[:pdf_n]:
            add("unpaywall_pdf", u)
        for u in (up.get("landing_urls") or [])[:land_n]:
            add("unpaywall_landing", u)
        if not fast:
            for u in publisher_pdf_urls(doi)[:4]:
                add("publisher_pdf", u)

    oa = ids.get("openalex") or openalex_lookup(doi=doi, pmid=ids.get("pmid") or "", title="")
    pdf_n, land_n = (1, 1) if fast else (3, 2)
    for u in (oa.get("pdf_urls") or [])[:pdf_n]:
        add("openalex_pdf", u)
    for u in (oa.get("landing_urls") or [])[:land_n]:
        add("openalex_landing", u)

    pmcid = normalize_pmcid(ids.get("pmcid") or "")
    pmid = (ids.get("pmid") or "").strip()
    if pmcid and not fast:
        add("epmc_article_html", f"https://europepmc.org/article/PMC/{pmcid.lstrip('PMC')}")
    if pmid and not fast:
        add("epmc_med_html", f"https://europepmc.org/article/MED/{pmid}")
    if doi and not fast:
        add("doi_redirect", f"https://doi.org/{doi}")
        try:
            from scripts.yes_md_web_xml_hunt import _semantic_scholar_xml_urls

            for u in _semantic_scholar_xml_urls(doi=doi, pmid=pmid)[:2]:
                add("semantic_scholar", u)
        except Exception:
            pass
    return out


def _try_bytes(raw: bytes, *, step: str, url: str) -> tuple[str, str] | None:
    text, ct = _text_from_raw(raw)
    stats = jats_stats(raw) if ct == "xml" and raw else {}
    if _reject_non_fulltext(text, ct, raw=raw if ct == "xml" else b"", stats=stats, url=url):
        return None
    return text, ct


def online_fetch_fulltext(
    entry: dict[str, str],
    *,
    ids: dict[str, Any] | None = None,
    use_hunt: bool = True,
    fast: bool | None = None,
) -> dict[str, Any]:
    """全网 ID 解析 + 多源在线全文，仅返回 JSON，不写本地文件。"""
    if fast is None:
        fast = FAST_DEFAULT
    # 快模式默认不 hunt（多站顺序试是最大瓶颈）；显式 use_hunt=True 仍可开
    if fast and use_hunt and __import__("os").getenv("ONLINE_FULLTEXT_FORCE_HUNT", "").strip() not in ("1", "true", "True"):
        use_hunt = False

    no = entry.get("entry_no", "?")
    title = entry.get("EN Title", "")
    result: dict[str, Any] = {
        "entry_no": no,
        "en_title": title,
        "doi": "",
        "pmid": "",
        "pmcid": "",
        "content_type": "",
        "source_step": "",
        "source_url": "",
        "text_chars": 0,
        "body_text": "",
        "body_preview": "",
        "xml_stats": {},
        "analysis": {},
        "status": "skipped_no_fulltext",
        "attempts": [],
        "fast": fast,
    }

    def attempt(step: str, ok: bool, note: str = "") -> None:
        result["attempts"].append({"step": step, "ok": ok, "note": str(note)[:200]})

    hard_fails = 0
    hard_notes: list[str] = []

    def note_hard_fail(step: str, code: int | str = 0, err: str = "") -> bool:
        """记录硬失败；达到上限返回 True（应立即中止本篇）。"""
        nonlocal hard_fails
        if not _is_hard_network_fail(code, err):
            return False
        hard_fails += 1
        label = _hard_fail_label(code, err)
        hard_notes.append(f"{step}:{label}")
        attempt(step, False, f"硬失败 {hard_fails}/{HARD_FAIL_LIMIT} {label}")
        if hard_fails >= HARD_FAIL_LIMIT:
            msg = f"网络/403 达上限 {HARD_FAIL_LIMIT}，跳过并标记（{' | '.join(hard_notes[-3:])}）"
            attempt("hard_fail_cap", False, msg)
            result["status"] = "skipped_hard_fail"
            result["miss_reason"] = msg
            return True
        return False

    if ids is None:
        ids = resolve_all_ids(title, doi="", wos_an="")
    result["doi"] = normalize_doi(ids.get("doi") or "")
    result["pmid"] = (ids.get("pmid") or "").strip()
    result["pmcid"] = normalize_pmcid(ids.get("pmcid") or "")

    def finalize(text: str, *, step: str, url: str, ct: str, raw: bytes = b"") -> dict[str, Any]:
        stats = jats_stats(raw) if ct == "xml" and raw else {}
        reason = _reject_non_fulltext(text, ct, raw=raw if ct == "xml" else b"", stats=stats, url=url)
        if reason:
            attempt(step, False, reason)
            return result
        jats_analysis: dict[str, Any] = {}
        # XML：尽量用 JATS 章节；快模式也做（只解析结构，成本可接受）
        if ct == "xml" and raw:
            try:
                from scripts.yes_md_xml_fulltext_report import parse_jats_xml

                jats_analysis = parse_jats_xml(raw)
            except Exception as exc:
                jats_analysis = {"parse_error": str(exc)[:200]}
        body = text[:FULLTEXT_MAX_CHARS]
        from rag.fulltext_sections import build_section_payload

        sec_payload = build_section_payload(
            body,
            title=title,
            content_type=ct,
            jats_analysis=jats_analysis,
        )
        result.update({
            "content_type": ct,
            "source_step": step,
            "source_url": url,
            "text_chars": len(body),
            "body_text": body,
            "body_preview": body[:800],
            "xml_stats": stats,
            "sections": sec_payload["sections"],
            "sectioned": sec_payload["sectioned"],
            "analysis": {
                "section_stats": sec_payload["section_stats"],
                "section_fill_count": sec_payload["section_fill_count"],
                "gene_mentions": (jats_analysis.get("gene_mentions") or [])[:30],
                "zebrafish_terms": (jats_analysis.get("zebrafish_terms") or [])[:20],
                "body_char_count": jats_analysis.get("body_char_count") or len(body),
            },
            "status": "ok",
            "fulltext_gate": "parsed" if _is_parsed_fulltext(ct, text, raw=raw, stats=stats) else "usable_body_3000",
        })
        attempt(step, True, f"{len(body)} chars sections={sec_payload['section_fill_count']}")
        return result

    def try_raw(raw: bytes, *, step: str, url: str) -> dict[str, Any] | None:
        if not raw:
            return None
        text, ct = _text_from_raw(raw)
        out = finalize(text, step=step, url=url, ct=ct, raw=raw if ct == "xml" else b"")
        return out if out.get("status") == "ok" else None

    # --- 快路径优先：有 PMCID 先拿 XML；再并行打 OA URL；hunt 放最后或关掉 ---

    # A) EPMC/NCBI XML（有 PMCID/PMID 时命中快）
    if result["pmcid"] or result["pmid"] or result["doi"]:
        raw, _used, url, src = fetch_jats_xml_cascade(
            pmid=result["pmid"],
            pmcid=result["pmcid"],
            doi=result["doi"] if not fast else "",
            pmcid_candidates=(ids.get("pmcid_candidates") or [])[:2] if fast else (ids.get("pmcid_candidates") or []),
        )
        if not fast:
            _sleep()
        out = try_raw(raw, step=src, url=url)
        if out:
            return out

    # B) EPMC fullText（仅慢模式或有 PMCID）
    if result["pmcid"] or (not fast and result["pmid"]):
        text, used_pmc, ok = fetch_pmc_fulltext(result["pmid"], result["pmcid"], skip_precheck=True)
        if ok and text:
            out = finalize(text, step="epmc_fulltext_api", url=f"{EPMC_REST}/{used_pmc}/fullTextXML", ct="text")
            if out.get("status") == "ok":
                return out
        attempt("epmc_fulltext_api", False, "无 PMC 全文")

    # C) OA URL：快模式并行下载，再按优先级解析（避免并发写 result）
    candidates = _collect_url_candidates(ids, doi=result["doi"], fast=fast)
    if candidates and fast and FAST_URL_WORKERS > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        fetched: dict[str, tuple[bytes, int, str]] = {}

        def _one(item: tuple[str, str]) -> tuple[str, str, bytes, int, str]:
            step, url = item
            raw_u, code, err = fetch_url_bytes(url, accept="*/*", fast=True)
            return step, url, raw_u, code, err

        with ThreadPoolExecutor(max_workers=min(FAST_URL_WORKERS, len(candidates))) as pool:
            futs = [pool.submit(_one, c) for c in candidates]
            for fut in as_completed(futs):
                step, url, raw_u, code, err = fut.result()
                fetched[url] = (raw_u, code, err)

        for step, url in candidates:
            raw_u, code, err = fetched.get(url, (b"", 0, "missing"))
            if not raw_u:
                if _is_hard_network_fail(code, err):
                    if note_hard_fail(step, code, err):
                        return result
                else:
                    attempt(step, False, code or err)
                continue
            out = try_raw(raw_u, step=step, url=url)
            if out:
                return out
            # 有响应但非全文：不计入硬失败；403 空体已在上面处理
            attempt(step, False, f"HTTP {code}, {len(raw_u)} bytes")
    else:
        for step, url in candidates:
            raw_u, code, err = fetch_url_bytes(url, accept="*/*", fast=fast)
            if not raw_u:
                if _is_hard_network_fail(code, err):
                    if note_hard_fail(step, code, err):
                        return result
                else:
                    attempt(step, False, code or err)
                if not fast:
                    time.sleep(0.25)
                continue
            out = try_raw(raw_u, step=step, url=url)
            if out:
                return out
            attempt(step, False, f"HTTP {code}, {len(raw_u)} bytes")
            if not fast:
                time.sleep(0.25)

    # D) 慢模式：EPMC HTML
    if not fast:
        for path in [
            f"{result['pmcid']}/fullTextHTML" if result["pmcid"] else "",
            f"MED/{result['pmid']}/fullTextHTML" if result["pmid"] else "",
        ]:
            if not path:
                continue
            url_h = f"{EPMC_REST}/{path}"
            raw_h, code, err = fetch_url_bytes(url_h)
            if not raw_h:
                if note_hard_fail("epmc_html", code, err):
                    return result
            out = try_raw(raw_h, step="epmc_html", url=url_h)
            if out:
                return out
            attempt("epmc_html", False, code)

    # E) hunt（慢且贵，默认关）
    if use_hunt:
        try:
            from scripts.yes_md_web_xml_hunt import hunt_xml_sources

            for rec in hunt_xml_sources(ids, title):
                step = rec.get("site") or "hunt"
                if rec.get("ok") and rec.get("_raw"):
                    out = try_raw(rec["_raw"], step=f"hunt_{step}", url=rec.get("url") or "")
                    if out:
                        return out
                code = rec.get("http_code") or 0
                err = str(rec.get("error") or "")
                if note_hard_fail(f"hunt_{step}", code, err):
                    return result
                attempt(f"hunt_{step}", bool(rec.get("ok")), code or err)
        except Exception as exc:
            if note_hard_fail("hunt", 0, str(exc)):
                return result
            attempt("hunt", False, str(exc))

    # F) 标题补 PMC（仅慢模式）
    if not fast and title and not result["pmcid"]:
        q = title[:120].replace('"', "")
        for r in epmc_search(f'TITLE:"{q}"', page_size=3):
            pid = normalize_pmcid(str(r.get("pmcid") or ""))
            if not pid:
                continue
            raw2, used2, url2, src2 = fetch_jats_xml_cascade(pmcid=pid)
            out = try_raw(raw2, step=f"epmc_title_{src2}", url=url2)
            if out:
                return out
            attempt(f"epmc_title_{src2}", False, used2)

    # 无 DOI/PMC/OA 线索时快速失败，少浪费重试
    if fast and not (result["doi"] or result["pmcid"] or result["pmid"]):
        attempt("all", False, "无 DOI/PMCID，非全文跳过并标记")
        result["status"] = "skipped_no_fulltext"
        return result

    attempt("all", False, "无合格全文（超时重试耗尽或非全文），已跳过并标记")
    result["status"] = "skipped_no_fulltext"
    return result
