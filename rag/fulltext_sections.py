# -*- coding: utf-8 -*-
"""全文章节切分：标题/摘要/引言/方法/结果/讨论/结论。"""
from __future__ import annotations

import re
from typing import Any

CANONICAL_SECTIONS = (
    "title",
    "abstract",
    "keywords",
    "introduction",
    "methods",
    "results",
    "discussion",
    "conclusion",
    "references",
    "figure_captions",
    "other",
)

# 入库/展示目标模块（抽不到填【无】）
TARGET_MODULES = (
    "title",
    "abstract",
    "keywords",
    "introduction",
    "methods",
    "results",
    "discussion",
    "conclusion",
    "references",
    "figure_captions",
)

MISSING = "【无】"

# 标题词 → 标准字段
_ALIAS_TO_CANON = (
    ("abstract", "abstract"),
    ("summary", "abstract"),
    ("keywords", "keywords"),
    ("key words", "keywords"),
    ("key-words", "keywords"),
    ("introduction", "introduction"),
    ("background", "introduction"),
    ("materials and methods", "methods"),
    ("material and methods", "methods"),
    ("experimental procedures", "methods"),
    ("experimental methods", "methods"),
    ("methods", "methods"),
    ("methodology", "methods"),
    ("experimental", "methods"),
    ("experimental section", "methods"),
    ("results and discussion", "results"),
    ("result", "results"),
    ("discussion", "discussion"),
    ("conclusions", "conclusion"),
    ("conclusion", "conclusion"),
    ("references", "references"),
    ("bibliography", "references"),
    ("literature cited", "references"),
    ("acknowledgments", "other"),
    ("acknowledgements", "other"),
    ("supplementary", "other"),
    ("supporting information", "other"),
)

_HEADING_RE = re.compile(
    r"(?is)"
    r"(?:(?<=\n)|(?<=^)|(?<=\s))"
    r"(?:"
    r"(?:\d{1,2}(?:\.\d{1,2})*\.?|[IVXLC]{1,6}\.?|[A-Z]\.)\s+"
    r")?"
    r"("
    r"abstract|summary|"
    r"key\s*-?\s*words?|keywords|"
    r"introduction|background|"
    r"materials?\s+and\s+methods|experimental\s+procedures|experimental\s+methods|methods?|methodology|"
    r"results?(?:\s+and\s+discussion)?|"
    r"discussion|"
    r"conclusions?|"
    r"references|bibliography|literature\s+cited|"
    r"acknowledg?ements?|supplementary(?:\s+materials?)?|supporting\s+information"
    r")"
    r"(?:\s*[:.\-–—])?"
    r"(?=\s+[A-Z0-9\"“(]|\s*$|\n)"
)

SECTION_LABELS = {
    "title": "Title",
    "abstract": "Abstract",
    "keywords": "Keywords",
    "introduction": "Introduction",
    "methods": "Materials and Methods",
    "results": "Results",
    "discussion": "Discussion",
    "conclusion": "Conclusion",
    "references": "References",
    "figure_captions": "Figure Captions",
    "other": "Other",
}

_NOISE_RE = re.compile(
    r"(?is)\b("
    r"skip to main content|cookie|sign in|subscribe|share this article|"
    r"download pdf|view saved research|cite this article|get shareable link|"
    r"cloudflare|please enable javascript|access provided by"
    r")\b"
)


def _canon_heading(raw: str) -> str:
    low = re.sub(r"\s+", " ", (raw or "").strip().lower())
    for alias, canon in _ALIAS_TO_CANON:
        if alias == low or low.startswith(alias):
            return canon
    return "other"


def empty_sections(*, title: str = "") -> dict[str, str]:
    out = {k: "" for k in CANONICAL_SECTIONS}
    out["title"] = (title or "").strip()
    return out


def fill_missing(sections: dict[str, str], *, keys: tuple[str, ...] | None = None) -> dict[str, str]:
    """目标字段抽不到则填【无】。"""
    keys = keys or TARGET_MODULES
    out = dict(sections or {})
    for k in keys:
        v = (out.get(k) or "").strip()
        out[k] = v if v else MISSING
    if "title" in out and not (out.get("title") or "").strip():
        out["title"] = MISSING
    return out


def extract_jats_extras(raw: bytes) -> dict[str, str]:
    """从 JATS 额外抽参考文献、图题。"""
    import xml.etree.ElementTree as ET

    refs: list[str] = []
    caps: list[str] = []
    if not (raw or b"").strip():
        return {"references": "", "figure_captions": ""}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {"references": "", "figure_captions": ""}

    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1] if "}" in tag else tag

    def text(el: ET.Element | None) -> str:
        if el is None:
            return ""
        return " ".join("".join(el.itertext()).split())

    for el in root.iter():
        tag = local(el.tag)
        if tag == "ref":
            t = text(el)
            if len(t) > 20:
                refs.append(t)
        elif tag == "fig":
            label = ""
            cap = ""
            for ch in el.iter():
                ct = local(ch.tag)
                if ct == "label" and not label:
                    label = text(ch)
                elif ct == "caption" and not cap:
                    cap = text(ch)
            line = " ".join(x for x in (label, cap) if x).strip()
            if len(line) > 10:
                caps.append(line)
        elif tag == "table-wrap":
            # 表题一并放入图题字段（图表题注）
            label = ""
            cap = ""
            for ch in el.iter():
                ct = local(ch.tag)
                if ct == "label" and not label:
                    label = text(ch)
                elif ct == "caption" and not cap:
                    cap = text(ch)
            line = " ".join(x for x in (label, cap) if x).strip()
            if len(line) > 10:
                caps.append(line)

    return {
        "references": "\n".join(refs[:800]),
        "figure_captions": "\n".join(caps[:200]),
    }


def section_stats(sections: dict[str, str]) -> dict[str, dict[str, int]]:
    return {
        k: {"chars": len(v or "")}
        for k, v in sections.items()
        if k != "title" and (v or "").strip()
    }


def _clean_noise(text: str) -> str:
    t = _NOISE_RE.sub(" ", text or "")
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def segment_body_text(body: str, *, title: str = "") -> dict[str, str]:
    """从扁平正文按章节标题切分。"""
    sections = empty_sections(title=title)
    text = _clean_noise(body or "")
    if not text:
        return sections

    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        # 无明显标题：尽量抽出 Abstract 段落后，其余进 other
        m = re.search(r"(?is)\babstract\b(.{200,4000}?)(?=\bintroduction\b|\bmethods?\b|$)", text)
        if m:
            sections["abstract"] = m.group(1).strip()
            rest = (text[: m.start()] + " " + text[m.end() :]).strip()
            sections["other"] = rest[:80000]
        else:
            sections["other"] = text[:80000]
        if not sections["title"] and text:
            sections["title"] = text[:200].split(".")[0][:200]
        return sections

    # 开头到第一个标题之前：常含标题残留/摘要前文字
    pre = text[: matches[0].start()].strip()
    buckets: dict[str, list[str]] = {k: [] for k in CANONICAL_SECTIONS if k != "title"}

    for i, m in enumerate(matches):
        canon = _canon_heading(m.group(1))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[m.end() : end].strip()
        if chunk:
            buckets.setdefault(canon, []).append(chunk)

    if pre:
        # 若尚无 abstract，且 pre 较短像摘要
        if not buckets.get("abstract") and 200 <= len(pre) <= 4000 and "abstract" not in pre.lower()[:30]:
            buckets["abstract"].append(pre)
        else:
            buckets["other"].append(pre)

    for k, parts in buckets.items():
        if parts:
            sections[k] = "\n\n".join(parts).strip()

    if not sections["title"]:
        sections["title"] = (title or "").strip()
    return sections


def sections_from_jats_analysis(analysis: dict[str, Any], *, title: str = "") -> dict[str, str]:
    """把 parse_jats_xml 输出归一到标准 sections。"""
    sections = empty_sections(title=title or (analysis.get("title") or ""))
    if analysis.get("abstract"):
        sections["abstract"] = str(analysis["abstract"]).strip()
    raw_secs = analysis.get("sections") or {}
    # parse_jats 的 sections 值可能是段落 list
    for name, val in raw_secs.items():
        if isinstance(val, list):
            text = "\n\n".join(str(x) for x in val if x)
        else:
            text = str(val or "")
        if not text.strip():
            continue
        canon = _canon_heading(str(name))
        if sections.get(canon):
            sections[canon] = (sections[canon] + "\n\n" + text).strip()
        else:
            sections[canon] = text.strip()
    # 分析里若已带 extras
    if analysis.get("references"):
        sections["references"] = str(analysis["references"]).strip()
    if analysis.get("figure_captions"):
        sections["figure_captions"] = str(analysis["figure_captions"]).strip()
    return sections


def format_sections_markdown(sections: dict[str, str], *, include_missing: bool = False) -> str:
    """把分节结果格式化为 Markdown，便于阅读区分 Abstract 等。"""
    order = (
        "title",
        "abstract",
        "keywords",
        "introduction",
        "methods",
        "results",
        "discussion",
        "conclusion",
        "references",
        "figure_captions",
        "other",
    )
    lines: list[str] = []
    for key in order:
        raw = (sections.get(key) or "").strip()
        if not raw or raw == MISSING:
            if include_missing and key in TARGET_MODULES:
                lines += [f"### {SECTION_LABELS.get(key, key)}", "", MISSING, ""]
            continue
        label = SECTION_LABELS.get(key, key)
        lines += [f"### {label}", "", raw, ""]
    return "\n".join(lines).rstrip() + "\n"


def build_section_payload(
    body: str,
    *,
    title: str = "",
    content_type: str = "",
    jats_analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一产出 sections + section_stats。"""
    jats_analysis = jats_analysis or {}
    if content_type == "xml" and (jats_analysis.get("sections") or jats_analysis.get("abstract")):
        sections = sections_from_jats_analysis(jats_analysis, title=title)
        # JATS 没切出正文时，再用扁平切分兜底
        core_chars = sum(len(sections.get(k) or "") for k in ("methods", "results", "discussion", "introduction"))
        if core_chars < 500 and body:
            fallback = segment_body_text(body, title=title or sections.get("title", ""))
            for k, v in fallback.items():
                if k == "title":
                    continue
                if not (sections.get(k) or "").strip() and v:
                    sections[k] = v
    else:
        sections = segment_body_text(body, title=title)

    if title and not sections.get("title"):
        sections["title"] = title.strip()

    stats = section_stats(sections)
    core = ("abstract", "introduction", "methods", "results", "discussion", "conclusion")
    filled = sum(1 for k in core if len(sections.get(k) or "") >= 80)
    return {
        "sections": sections,
        "section_stats": stats,
        "section_fill_count": filled,
        "sectioned": filled >= 2 or len(sections.get("methods") or "") >= 200 or len(sections.get("results") or "") >= 200,
    }


def attach_sections_to_hit(hit: dict[str, Any], *, jats_analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    """原地/拷贝均可：给命中记录挂上 sections。"""
    payload = build_section_payload(
        hit.get("body_text") or "",
        title=hit.get("en_title") or hit.get("title") or "",
        content_type=hit.get("content_type") or "",
        jats_analysis=jats_analysis,
    )
    out = dict(hit)
    out["sections"] = payload["sections"]
    analysis = dict(out.get("analysis") or {})
    analysis["section_stats"] = payload["section_stats"]
    analysis["section_fill_count"] = payload["section_fill_count"]
    analysis["body_char_count"] = analysis.get("body_char_count") or int(out.get("text_chars") or 0)
    out["analysis"] = analysis
    out["sectioned"] = payload["sectioned"]
    return out
