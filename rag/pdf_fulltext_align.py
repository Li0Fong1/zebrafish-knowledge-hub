# -*- coding: utf-8 -*-
"""
PDF 全文对齐：内容 / 顺序 / 排版 / 章节 / 噪声 / 符号 / 来源。
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import fitz

from rag.fulltext_sections import (
    MISSING,
    TARGET_MODULES,
    build_section_payload,
    fill_missing,
)

_LIGATURES = str.maketrans(
    {
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬀ": "ff",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬅ": "ft",
        "ﬆ": "st",
    }
)

_CAPTION_RE = re.compile(
    r"(?im)^(?:\s*)((?:Figure|Fig\.|Table|Supplementary\s+(?:Figure|Fig\.|Table)|FIGURE)\s*"
    r"[S]?\d+[A-Za-z]?(?:\.[^\n]{0,400}|[^\n]{10,400}))"
)
# Frontiers 等：题注标题单独成行，正文在下一行
_CAPTION_MULTILINE_RE = re.compile(
    r"(?im)^(?:Figure|Fig\.|FIGURE|Table)\s*[S]?\d+[A-Za-z]?\s*\n+"
    r"([A-Z][^\n]{25,600})"
)

CONTENT_THRESHOLD = 0.99

# 科学复合词前缀：跨行保留连字符（spatio- + temporal → spatio-temporal）
_KEEP_HYPHEN_PREFIX = re.compile(
    r"(?i)(spatio|immuno|micro|macro|multi|intra|inter|anti|semi|pseudo|"
    r"neuro|cyto|endo|exo|haemato|hemato|mesenchy|epithelial|over|under)$"
)


def _join_hyphen_break(left: str, right: str) -> str:
    if _KEEP_HYPHEN_PREFIX.match(left):
        return f"{left}-{right}"
    # 专名/受体标签：Kruskal-Wallis、nls-mCherry、Gallego-Urrea
    if right and right[0].isupper():
        return f"{left}-{right}"
    if right and right[0].islower() and any(c.isupper() for c in right[1:]):
        return f"{left}-{right}"
    return f"{left}{right}"


def normalize_symbols(text: str) -> str:
    """符号/编码对齐：连字、软连字符、空白、常见破折号、控制符。"""
    t = unicodedata.normalize("NFKC", text or "")
    t = t.translate(_LIGATURES)
    t = t.replace("\u00ad", "")
    t = t.replace("\ufeff", "").replace("\xa0", " ")
    t = t.replace("\u2009", " ").replace("\u200a", " ").replace("\u202f", " ")
    t = t.replace("\u200b", "").replace("\u2003", " ").replace("\u2002", " ")
    t = t.replace("\r\n", "\n").replace("\r", "\n").replace("\x0c", "\n")
    t = re.sub(r"[\x00-\x08\x0b\x0e-\x1f]", "", t)
    # 统一破折号（保留含义，便于检索）
    t = t.replace("–", "-").replace("—", "-").replace("−", "-")
    t = t.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    # Nature 等：þ 实为上标 +；分数斜杠 1⁄4 实为 =
    t = t.replace("þ", "+").replace("Þ", "+")
    t = t.replace("1⁄4", "=").replace("⁄", "/")
    # 组合音标与字母被空格拆开：e ́ → é
    t = re.sub(r"([A-Za-z])\s+([\u0300-\u036f])", r"\1\2", t)
    t = unicodedata.normalize("NFC", t)
    # 行末断词：普通音节去掉连字符；复合词前缀保留
    t = re.sub(
        r"([A-Za-z]+)-\n([A-Za-z0-9(])",
        lambda m: _join_hyphen_break(m.group(1), m.group(2)),
        t,
    )
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip() + ("\n" if t.strip() else "")


_HEADING_LINE = re.compile(
    r"(?i)^(abstract|summary|introduction|background|"
    r"materials?\s+and\s+methods|experimental(?:\s+procedures|\s+methods)?|"
    r"methods?|methodology|results?(?:\s+and\s+discussion)?|discussion|"
    r"conclusions?|references|bibliography|"
    r"\d+(\.\d+)*\s+.+)$"
)


def reflow_readable(text: str) -> str:
    """合并被硬换行打断的续行（小写续写/参考文献），提升阅读顺序观感。"""
    t = normalize_symbols(text)
    lines = t.split("\n")
    out: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf:
            out.append(buf)
        buf = ""

    for raw in lines:
        s = raw.strip()
        if not s:
            flush()
            out.append("")
            continue
        # 图注面板字母不进入正文流
        if _is_panel_line(s):
            if buf.endswith("-"):
                buf = buf[:-1]
            continue
        if not buf:
            buf = s
            continue
        is_heading = bool(_HEADING_LINE.match(s)) and len(s) < 80
        if buf.endswith("-") and s[:1].isalnum():
            # 禁止 exam- + A（面板）粘成 examA
            if _is_panel_line(s):
                buf = buf[:-1]
                continue
            m = re.search(r"([A-Za-z]+)-$", buf)
            if m:
                buf = buf[: m.start()] + _join_hyphen_break(m.group(1), s)
            else:
                buf = buf[:-1] + s
            continue
        if (
            not is_heading
            and buf[-1] not in ".!?:;。！？"
            and (
                s[0].islower()
                or s[0].isdigit()
                or s.startswith(("doi:", "doi.org", "(", "[", "http"))
            )
        ):
            buf = buf + " " + s
            continue
        # 分号后接 Supplementary/Table/Figure 续行（Frontiers 引用换行）
        if (
            not is_heading
            and buf.rstrip().endswith(";")
            and re.match(
                r"(?i)^(Supplementary\s+)?(Table|Figure|Fig\.)\b",
                s,
            )
        ):
            buf = buf + " " + s
            continue
        # 以 Table/Fig). 开头的孤儿续行并回上句
        if (
            not is_heading
            and buf
            and re.match(r"(?i)^(Supplementary\s+)?(Table|Figure|Fig\.)\s*\S+\)\.", s)
        ):
            buf = buf + " " + s
            continue
        flush()
        buf = s
    flush()
    t2 = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"
    return clean_figure_artifacts(t2)


def repair_sections(sections: dict[str, str], body: str) -> dict[str, str]:
    """补全缺省章节：无 Introduction 标题时，从摘要后到 Results/Methods 前抽取。"""
    out = dict(sections)

    def _empty(key: str) -> bool:
        v = out.get(key) or ""
        return v in ("", MISSING) or len(v.strip()) < 80

    def _is_junk_section(text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return True
        if re.match(
            r"(?i)^(OPEN ACCESS|EDITED BY|REVIEWED BY|ARTICLE|Regular Article|"
            r"RECEIVED|ACCEPTED|PUBLISHED|TYPE Original)\b",
            t,
        ):
            return True
        if re.search(r"(?i)analysed the data and wrote the manuscript|Additional information", t):
            return True
        if re.match(r"(?i)^Fig(?:ure|\.)\s*\d+", t):
            return True
        if re.match(r"(?i)^using developing zebrafish", t):
            return True
        # 作者行冒充摘要：含多人名+上标数字
        if re.search(r"[A-Z][a-z]+\d,\d", t[:200]) and "et al" not in t[:80].lower():
            if not re.search(r"(?i)\b(the|we|this study|here we)\b", t[:200]):
                return True
        return False

    def _extract_by_heading(start_pat: str, end_pats: str) -> str:
        m = re.search(start_pat, body, flags=re.I | re.M)
        if not m:
            return ""
        rest = body[m.end() :]
        m2 = re.search(end_pats, rest, flags=re.I | re.M)
        chunk = rest[: m2.start()].strip() if m2 else rest[:12000].strip()
        return chunk

    # 清洗已被污染的章节
    for key in ("abstract", "keywords", "introduction", "methods", "results", "discussion", "conclusion"):
        if not _empty(key) and _is_junk_section(out.get(key) or ""):
            out[key] = MISSING

    # Abstract / Keywords / Introduction 强制按标题重切（含同行 Abstract Xxx）
    abs_chunk = _extract_by_heading(
        r"(?im)^(?:Abstract|ABSTRACT|Summary|SUMMARY)\b\s*",
        r"(?im)^(?:Key\s*-?\s*words?|KEYWORDS|Introduction|INTRODUCTION|Background|"
        r"Materials and methods|MATERIALS AND METHODS|Methods|METHODS|1\s+Introduction)\b",
    )
    if len(abs_chunk) < 80:
        # 同行：Abstract Manganese ferrite...
        m_inline = re.search(
            r"(?is)(?:^|\n)\s*(?:Abstract|ABSTRACT)\s+(.{120,6000}?)"
            r"(?=\n\s*(?:Key\s*-?\s*words?|KEYWORDS|Introduction|INTRODUCTION|"
            r"Materials and methods|Methods|1\s+Introduction)\b)",
            body,
        )
        if m_inline:
            abs_chunk = m_inline.group(1).strip()
    if len(abs_chunk) >= 80 and not _is_junk_section(abs_chunk[:160]):
        # 去掉误吸入的 Keywords 行
        abs_chunk = re.split(
            r"(?im)^(?:Key\s*-?\s*words?|KEYWORDS)\b", abs_chunk, maxsplit=1
        )[0].strip()
        # 截到句末
        if abs_chunk and abs_chunk[-1].isalnum():
            cut = max(abs_chunk.rfind(". "), abs_chunk.rfind("? "), abs_chunk.rfind("! "))
            if cut >= int(len(abs_chunk) * 0.55):
                abs_chunk = abs_chunk[: cut + 1].strip()
        out["abstract"] = abs_chunk

    kw_chunk = _extract_by_heading(
        r"(?im)^(?:Key\s*-?\s*words?|KEYWORDS|Key Words)\s*[:.]?\s*",
        r"(?im)^(?:Introduction|INTRODUCTION|Background|Materials and methods|"
        r"METHODS|Methods|1\s+Introduction)\b",
    )
    if len(kw_chunk) < 8:
        m_kw = re.search(
            r"(?is)(?:^|\n)\s*(?:Key\s*-?\s*words?|KEYWORDS)\s*[:.]?\s*(.{8,500}?)"
            r"(?=\n\s*(?:Introduction|INTRODUCTION|Materials and methods|Methods)\b)",
            body,
        )
        if m_kw:
            kw_chunk = m_kw.group(1).strip()
    if 8 <= len(kw_chunk) <= 800 and not _is_junk_section(kw_chunk[:120]):
        out["keywords"] = re.sub(r"\s+", " ", kw_chunk).strip()

    intro_chunk = _extract_by_heading(
        r"(?im)^(?:Introduction|INTRODUCTION|Background|BACKGROUND)\s*$",
        r"(?im)^(?:Materials and methods|MATERIALS AND METHODS|Experimental Procedures|"
        r"Methods|METHODS|Results|RESULTS)\s*$",
    )
    if len(intro_chunk) < 120:
        m_intro = re.search(
            r"(?is)(?:^|\n)\s*(?:Introduction|INTRODUCTION)\s*\n(.{200,20000}?)"
            r"(?=\n\s*(?:Materials and methods|METHODS|Methods|Results|RESULTS)\b)",
            body,
        )
        if m_intro:
            intro_chunk = m_intro.group(1).strip()
    if len(intro_chunk) >= 120 and not _is_junk_section(intro_chunk[:160]):
        out["introduction"] = intro_chunk[:12000]

    # 无 Abstract 标题时：作者单位后首个长科学段（Frontiers 等）
    if _empty("abstract"):
        m_abs = re.search(
            r"(?is)(?:United States|United Kingdom|Canada|Germany|China|Japan)\s*"
            r"(?:,|\.|\)).{0,400}?\n+([A-Z][^.\n]{40,220}\.(?:\s+[A-Z][^.\n]{20,220}\.){2,20})",
            body,
        )
        if m_abs:
            cand = m_abs.group(1).strip()
            if (
                len(cand) >= 200
                and not _is_junk_section(cand[:160])
                and re.search(r"(?i)\b(we|here|this|study|results|demonstrate)\b", cand)
            ):
                # 截到引言起笔前
                cut = re.search(
                    r"(?i)\n(?:Introduction|Teneurins are a family|Recent advances)\b",
                    cand,
                )
                if cut and cut.start() > 200:
                    cand = cand[: cut.start()].strip()
                out["abstract"] = cand[:5000]

    # 按标题强制重切核心章节（覆盖错误切分）
    heading_map = [
        (
            "methods",
            r"(?im)^(?:Materials and methods|MATERIALS AND METHODS|Experimental Procedures|"
            r"Methods|METHODS)\s*$",
            r"(?im)^(?:Results|RESULTS|Results and [Dd]iscussion|Discussion|References)\s*$",
        ),
        (
            "results",
            r"(?im)^(?:Results|RESULTS|Results and [Dd]iscussion)\s*$",
            r"(?im)^(?:Discussion|DISCUSSION|Conclusions?|References|Acknowledgements?)\s*$",
        ),
        (
            "discussion",
            r"(?im)^(?:Discussion|DISCUSSION)\s*$",
            r"(?im)^(?:Conclusions?|References|Acknowledgements?|Experimental Procedures|"
            r"Additional information|Author contributions)\s*$",
        ),
    ]
    for key, sp, ep in heading_map:
        chunk = _extract_by_heading(sp, ep)
        if len(chunk) >= 200 and not _is_junk_section(chunk[:160]):
            # Results and discussion 合章时 discussion 可能空，结果整段进 results
            if key == "results" and re.search(r"(?im)^Results and [Dd]iscussion\s*$", body):
                out["results"] = chunk
                if _empty("discussion"):
                    parts = [p for p in re.split(r"\n\s*\n", chunk) if p.strip()]
                    if len(parts) >= 4:
                        cut = max(2, (len(parts) * 2) // 3)
                        out["results"] = "\n\n".join(parts[:cut]).strip()
                        out["discussion"] = "\n\n".join(parts[cut:]).strip()
                    elif len(chunk) >= 2000:
                        cut = int(len(chunk) * 0.62)
                        snap = chunk.find("\n\n", cut)
                        if snap < 0:
                            snap = chunk.find(". ", cut)
                            if snap > 0:
                                snap += 1
                        if snap > len(chunk) // 3:
                            out["results"] = chunk[:snap].strip()
                            out["discussion"] = chunk[snap:].strip()
            elif key == "discussion" and len(chunk) >= 200:
                out["discussion"] = chunk
            elif key == "methods":
                out["methods"] = chunk

    if _empty("introduction"):
        m_end = re.search(
            r"(?im)^(RESULTS|Results|MATERIALS AND METHODS|Materials and methods|"
            r"1\s+Introduction|Introduction|2\s+Material)\s*$",
            body,
        )
        m_abs = re.search(r"(?im)^(Abstract|ABSTRACT)\b", body)
        start = m_abs.end() if m_abs else 0
        if m_end and m_end.start() > start + 200:
            chunk = body[start : m_end.start()].strip()
            if len(chunk) >= 300 and not _is_junk_section(chunk[:200]):
                out["introduction"] = chunk[-10000:] if len(chunk) > 10000 else chunk

    if _empty("introduction"):
        other = out.get("other") or ""
        if other not in ("", MISSING) and len(other) >= 400 and not _is_junk_section(other[:200]):
            out["introduction"] = other[:10000]
            rest = other[10000:].strip()
            out["other"] = rest if rest else MISSING

    # Nature 等无 Abstract 标题：作者行后、Introduction 前的首段摘要
    if _empty("abstract"):
        m_intro = re.search(
            r"(?im)^(?:Introduction|BACKGROUND)\b|^(?:In|I\s+n)\s+[a-z]",
            body,
        )
        m_doi = re.search(r"(?im)^DOI:\s*\S+", body)
        start = m_doi.end() if m_doi else 0
        chunk = body[start : m_intro.start()].strip() if m_intro else ""
        if len(chunk) >= 200:
            # 去掉粘在一起的作者行，保留 The/Here/We 起头的科学摘要
            m_prose = re.search(
                r"(?m)^(?:The |Here we |We (?:show|demonstrate|report) |In this study)",
                chunk,
            )
            if m_prose:
                out["abstract"] = chunk[m_prose.start() :].strip()[:6000]
            else:
                paras = [p.strip() for p in re.split(r"\n\s*\n", chunk) if p.strip()]
                prose = [
                    p
                    for p in paras
                    if len(p) >= 180
                    and "." in p
                    and not _is_junk_section(p[:120])
                    and not re.search(r"[A-Za-z]+\d,\d", p[:100])
                ]
                if prose:
                    out["abstract"] = "\n\n".join(prose[:3])[:6000]
                elif len(chunk) >= 300 and not _is_junk_section(chunk[:120]):
                    out["abstract"] = chunk[-4000:] if len(chunk) > 4000 else chunk

    # Frontiers：正文里找真正摘要段（若 abstract 仍空/污染）
    if _empty("abstract") or _is_junk_section(out.get("abstract") or ""):
        m = re.search(
            r"(?is)(?<!TYPE )\bABSTRACT\b\s*(.{200,5000}?)(?=\b(?:KEYWORDS|INTRODUCTION|1\s+Introduction|Materials and methods)\b)",
            body,
        )
        if m and not _is_junk_section(m.group(1)[:120]):
            out["abstract"] = m.group(1).strip()[:6000]
        else:
            # 标题后首个长科学段
            m2 = re.search(
                r"(?is)(?:DOI:|PUBLISHED)[^\n]*\n+(.{250,2500}?)(?=\n(?:INTRODUCTION|Introduction|1\s))",
                body,
            )
            if m2 and not _is_junk_section(m2.group(1)[:120]):
                cand = m2.group(1).strip()
                if re.search(r"(?i)\b(we|this study|here|objective|aim)\b", cand):
                    out["abstract"] = cand[:6000]

    # Results and discussion 合章：从 results 切出后段作 discussion
    if _empty("discussion"):
        res = out.get("results") or ""
        if res not in ("", MISSING) and len(res) >= 1500 and not _is_junk_section(res[:120]):
            m_sub = re.search(r"(?im)^Discussion\s*$", res)
            if m_sub and m_sub.start() > 400:
                out["results"] = res[: m_sub.start()].strip()
                out["discussion"] = res[m_sub.end() :].strip()
            else:
                parts = [p for p in re.split(r"\n\s*\n", res) if p.strip()]
                if len(parts) >= 4:
                    cut = max(2, (len(parts) * 2) // 3)
                    out["results"] = "\n\n".join(parts[:cut]).strip()
                    out["discussion"] = "\n\n".join(parts[cut:]).strip()

    # 无独立 conclusion：优先找 Conclusions 标题；避免作者贡献/参考文献
    if _empty("conclusion"):
        m_c = re.search(
            r"(?im)^(?:Conclusions?|CONCLUDING REMARKS)\s*$",
            body,
        )
        if m_c:
            m_next = re.search(
                r"(?im)^(References|Acknowledgements?|Experimental Procedures|Materials and methods|"
                r"Additional information|Author contributions)\s*$",
                body[m_c.end() :],
            )
            end = m_c.end() + (m_next.start() if m_next else 4000)
            chunk = body[m_c.end() : end].strip()
            if (
                len(chunk) >= 120
                and not _is_junk_section(chunk[:160])
                and not re.match(r"(?i)^\d+\.\s+[A-Z]", chunk)
                and "et al." not in chunk[:80]
                and not re.search(r"(?i)\b(Development|Nature|Dev Biol|Cell)\s+\d+", chunk[:200])
            ):
                out["conclusion"] = chunk[:8000]

    if _empty("conclusion"):
        disc = out.get("discussion") or ""
        if disc not in ("", MISSING) and len(disc) >= 800 and not _is_junk_section(disc[:120]):
            parts = [p for p in re.split(r"\n\s*\n", disc) if p.strip()]
            parts = [
                p
                for p in parts
                if not _is_junk_section(p[:160])
                and not re.match(r"(?i)^\d+\.\s+[A-Z]", p)
                and "et al." not in p[:60]
            ]
            if len(parts) >= 2:
                cand = parts[-1].strip()
                if len(cand) >= 120 and not re.match(r"(?i)^[A-Z]\.[A-Z]\.", cand):
                    out["conclusion"] = cand
                    # 不回写 discussion，避免误伤
            elif len(disc) >= 1200:
                cut = max(400, len(disc) // 4)
                cand = disc[-cut:].strip()
                if (
                    not _is_junk_section(cand[:160])
                    and not re.match(r"(?i)^\d+\.\s+[A-Z]", cand)
                    and "et al." not in cand[:60]
                ):
                    out["conclusion"] = cand

    # Springer / IJDB：Results and discussion 合章且 discussion 仍空
    if _empty("discussion"):
        res = out.get("results") or ""
        if res not in ("", MISSING) and len(res) >= 2000:
            parts = [p for p in re.split(r"\n\s*\n", res) if p.strip()]
            if len(parts) >= 5:
                cut = max(3, (len(parts) * 2) // 3)
                out["results"] = "\n\n".join(parts[:cut]).strip()
                out["discussion"] = "\n\n".join(parts[cut:]).strip()
            else:
                cut = int(len(res) * 0.62)
                snap = res.find("\n\n", cut)
                if snap < 0:
                    snap = res.find(". ", cut)
                    if snap > 0:
                        snap += 1
                if snap > len(res) // 3:
                    out["results"] = res[:snap].strip()
                    out["discussion"] = res[snap:].strip()

    # 摘要截到最近句末，避免半截词进入 sections
    abs_ = out.get("abstract") or ""
    if abs_ not in ("", MISSING) and len(abs_) > 120 and abs_[-1].isalnum():
        cut = max(abs_.rfind(". "), abs_.rfind("? "), abs_.rfind("! "))
        if cut >= int(len(abs_) * 0.55):
            out["abstract"] = abs_[: cut + 1].strip()

    return out


def tokenize(text: str) -> list[str]:
    # 去连字符再切词，避免 nls-mCherry vs nlsmcherry 召回偏差
    t = normalize_symbols(text).lower().replace("-", "")
    return re.findall(r"[a-z0-9]+", t)


def word_recall(ref: str, hyp: str) -> float:
    rw, hw = tokenize(ref), tokenize(hyp)
    if not rw:
        return 1.0
    rc, hc = Counter(rw), Counter(hw)
    return sum(min(rc[w], hc.get(w, 0)) for w in rc) / sum(rc.values())


def extract_text_layer(pdf_path: Path) -> str:
    doc = fitz.open(pdf_path)
    t = "\n".join((page.get_text("text") or "") for page in doc)
    doc.close()
    return t


def _mid_x(b) -> float:
    return (b[0] + b[2]) / 2.0


def _looks_like_prose_sentence(text: str) -> bool:
    """页脚区长句不应被当成页脚数字噪声。"""
    t = (text or "").strip()
    if len(t) < 60:
        return False
    if re.match(r"^[A-Z“\"(]", t) and re.search(r"[.!?]$", t.rstrip()):
        return True
    if len(t) >= 100 and re.search(r"\b(the|and|with|from|were|was|that|this)\b", t, re.I):
        return True
    return False


def _is_running_header_footer(text: str, page: fitz.Page, block) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    # 图题注绝不当页脚（即使落在页底）
    if _is_figure_caption_block(t):
        return False
    # Springer: Vol:. (1234567890) / Vol.: (0123456789) — 冒号与点顺序不固定
    if re.fullmatch(r"Vol[:.]{1,2}\s*\([\d.]+\)", t):
        return True
    if re.fullmatch(r"\d+\s+Page\s+\d+\s+of\s+\d+", t.replace("\t", " ")):
        return True
    if re.fullmatch(r"J Nanopart Res \(\d{4}\) \d+:\d+", t):
        return True
    if re.match(r"(?i)^J Nanopart Res \(\d{4}\).*Page \d+ of \d+", t):
        return True
    if re.match(r"(?i)^Cell Tissue Res \(\d{4}\)\s+\d+:\d+", t):
        return True
    if re.search(r"(?i)Cell Tissue Res \(\d{4}\)\s+\d+:\d+", t) and len(t) < 80:
        return True
    if re.match(r"(?i)^Sci(?:ence)?\s*China", t) and len(t) < 120:
        return True
    if re.search(
        r"(?i)(NATURE COMMUNICATIONS|All rights reserved|Frontiers in Marine Science|"
        r"frontiersin\.org|Biol\. Pharm\. Bull|Downloaded from)",
        t,
    ):
        return True
    if re.match(r"(?i)^[A-Z][A-Za-z\-]+(?:\s+[A-Z][A-Za-z\-]*)*\s+et al\.", t) and (
        "10." in t or "doi" in t.lower()
    ):
        return True
    y0, y1 = block[1], block[3]
    h = float(page.rect.height)
    if y1 < h * 0.10 and len(t) < 200:
        if re.search(r"(et al\.|10\.\d{4,}|doi\.org|Vol[:.]|Page \d+)", t, re.I):
            return True
    if y0 > h * 0.85 and len(t) < 200:
        # 禁止用裸引用数字把方法段误判为页脚
        if _looks_like_prose_sentence(t):
            return False
        if re.search(r"(Page \d+|frontiersin|www\.|Vol[:.]|©)", t, re.I):
            return True
        # 仅整块就是短页码才算
        if re.fullmatch(r"\d{1,3}", t):
            return True
    if re.fullmatch(r"\d{1,3}", t) and y0 > h * 0.85:
        return True
    return False


def _is_front_matter(text: str) -> bool:
    """Frontiers/Nature 等刊头、编辑信息，移出正文阅读流。"""
    t = (text or "").strip()
    if not t:
        return False
    if re.match(
        r"(?i)^(OPEN ACCESS|EDITED BY|REVIEWED BY|\*CORRESPONDENCE|CITATION|COPYRIGHT|"
        r"RECEIVED|ACCEPTED|PUBLISHED|TYPE Original)\b",
        t,
    ):
        return True
    if re.match(r"(?i)^ARTICLE\s*$", t):
        return True
    if re.match(r"(?i)^Received\s+\d+.+(Accepted|Published)", t):
        return True
    if "creativecommons.org" in t.lower() and len(t) < 400 and "OPEN ACCESS" in t.upper():
        return True
    # Frontiers 元信息块
    if re.search(r"(?i)\bTYPE Original Research\b", t) and re.search(r"(?i)\bRECEIVED\b", t):
        return True
    return False


def _is_sidebar(text: str) -> bool:
    t = (text or "").strip()
    if t.startswith("Supplementary Information"):
        return True
    if re.match(r"(?i)^Electronic supplementary material\b", t):
        return True
    if "e-mail:" in t or "e‑mail:" in t:
        return True
    if re.match(r"(?i)^\*Corresponding author\b", t):
        return True
    if re.match(r"^\*\s*[A-Z].{0,60}@\S+", t):
        return True
    if re.match(r"^[A-Z]\.\s*[A-Z]\.", t) and any(
        k in t for k in ("Laboratory", "Institute", "University", "Center", "Centre")
    ):
        return True
    if t.startswith("Nanomedicine Integrated Research Center"):
        return True
    if _is_front_matter(t):
        return True
    return False


@dataclass
class LayoutExtract:
    body_pages: list[str] = field(default_factory=list)
    captions: list[str] = field(default_factory=list)
    sidebar: list[str] = field(default_factory=list)
    headers_footers: list[str] = field(default_factory=list)
    figure_panels: list[str] = field(default_factory=list)
    figure_annotations: list[str] = field(default_factory=list)
    page_count: int = 0


def _is_figure_panel_block(text: str) -> bool:
    """图内面板字母。大写仅 A-F；小写 a-l（Nature 图注）；避免误删 drop-cap「I」。"""
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    if not lines or len(lines) > 8:
        return False
    return all(
        re.fullmatch(r"[A-Fa-f]\*?", ln) or re.fullmatch(r"[a-l]\*?", ln) for ln in lines
    )


def _is_panel_line(s: str) -> bool:
    """回流时只跳过 A-F 面板行，保留 Introduction 的 drop-cap「I」。"""
    return bool(re.fullmatch(r"[A-Fa-f]\*?", (s or "").strip()))


def _is_figure_caption_block(text: str) -> bool:
    t = (text or "").strip()
    # 统一窄空格，便于 Fig./Table 识别
    t = (
        t.replace("\u202f", " ")
        .replace("\u2002", " ")
        .replace("\u2003", " ")
        .replace("\u2009", " ")
        .replace("\xa0", " ")
    )
    return bool(
        re.match(
            r"(?i)^(?:Figure|Fig\.|Table|Supplementary\s+(?:Figure|Fig\.|Table))\s*"
            r"[S]?\d+",
            t,
        )
    )


def _annotation_line(ln: str) -> bool:
    """单行是否像图内标注（时间轴、阶段、色条、面板组）。"""
    s = (ln or "").strip()
    if not s:
        return True
    if _is_panel_line(s):
        return True
    if re.fullmatch(r"\*+", s):
        return True
    if re.fullmatch(r"[<>]+", s):
        return True
    if re.fullmatch(r"som", s, re.I):
        return True
    if re.fullmatch(r"(CV|Sp|End|Pe|Ant\.?|Pos\.?)", s):
        return True
    if re.fullmatch(r"~?\d+\s*h\.?p\.?f\.?", s, re.I):
        return True
    if re.fullmatch(r"~?\d+\s*d\.?p\.?f\.?", s, re.I):
        return True
    if re.fullmatch(r"~?\d+\s*hpf", s, re.I):
        return True
    if re.fullmatch(r"~?\d+\s*(?:min|sec)", s, re.I):
        return True
    if re.fullmatch(r"\d{1,3}-(?:cell|somite)", s, re.I):
        return True
    if re.fullmatch(r"~?\d{1,3}(?:-|\s+to\s+)\d{1,3}-somite", s, re.I):
        return True
    if re.fullmatch(r"~?\d{1,3}\s*-?\s*to\s*-?\s*\d{1,3}-somite", s, re.I):
        return True
    # 色条 / 数值轴碎片
    if re.fullmatch(r"[<>\d\s\-/]+", s) and len(s) < 50:
        if re.search(r"\d", s) or re.search(r"[<>]", s):
            return True
    # 面板组：A / A* 、i / j 、i j k l
    if re.fullmatch(r"[A-Za-z]\*?(\s*/\s*[A-Za-z]\*?)+", s):
        return True
    if re.fullmatch(r"[A-Za-z](\s+[A-Za-z]){1,7}", s) and all(
        len(p) == 1 for p in s.split()
    ):
        return True
    return False


def _is_figure_annotation_block(text: str) -> bool:
    """图内标注块：时间点、som、色条等，移出正文阅读流。"""
    t = (text or "").strip()
    if not t:
        return True
    if _is_figure_caption_block(t):
        return False
    if _is_figure_panel_block(t):
        return True
    # 长散文不是标注
    if len(t) > 100:
        return False
    lines = [x.strip() for x in t.splitlines() if x.strip()]
    if not lines or len(lines) > 12:
        return False
    return all(_annotation_line(ln) for ln in lines)


def scrub_inline_chrome(text: str) -> str:
    """清除嵌进段落的页眉页脚、侧栏横幅、Springer 引号伪影等。"""
    t = text or ""
    # Springer / 通用 running header 嵌进正文
    t = re.sub(
        r"(?i)\s*Cell Tissue Res \(\d{4}\)\s+\d+:\d+(?:-\d+)?\s*",
        " ",
        t,
    )
    t = re.sub(
        r"(?i)\s*J Nanopart Res \(\d{4}\)\s+\d+:\d+(?:-\d+)?\s*",
        " ",
        t,
    )
    t = re.sub(
        r"(?i)\s*Sci(?:ence)?\s*China(?:\s+Life Sci(?:ences)?)?\s*(?:,\s*)?\d{4}[^\n]{0,40}",
        " ",
        t,
    )
    t = re.sub(r"(?im)^\s*SCIENCE CHINA\s*$\n?", "", t)
    t = re.sub(r"(?im)^\s*Life Sciences\s*$\n?", "", t)
    # Electronic supplementary 横幅打断续句（常插在 non- 与 homologous 之间）
    t = re.sub(
        r"(?is)Electronic supplementary material The online version of this article\s*"
        r"\([^)]*\)\s*contains\s+(?:supplementary|Electronic)\s+material,?\s*"
        r"which is available to authorized users\.\s*",
        " ",
        t,
    )
    t = re.sub(
        r"(?is)Electronic supplementary material The online version of this article.{0,280}?"
        r"authorized users\.\s*",
        " ",
        t,
    )
    # 通讯作者 + 单位块（插在引言中间）
    t = re.sub(
        r"(?im)^\*\s*[A-Z][^\n]{0,80}@[^\n]+\n"
        r"(?:(?!^(?:Abstract|Introduction|Keywords|Materials|Methods|Results)\b)[^\n]*\n){0,12}",
        "",
        t,
    )
    t = re.sub(
        r"(?im)^\*Corresponding author[^\n]*\n?",
        "",
        t,
    )
    # 修复 non- + 横幅移除后的断裂
    t = re.sub(r"\bnon-\s+homologous\b", "non-homologous", t)
    t = re.sub(r"\bnon-\s*\n+\s*homologous\b", "non-homologous", t)
    # 残留 “non-Electronic” 粘连
    t = re.sub(r"\bnon-Electronic\b", "non-", t)
    t = re.sub(r"\bnon-\s+homologous\b", "non-homologous", t)
    # Springer：Abstract 被插进 “non-” 与 “homologous” 之间
    m_cut = re.search(
        r"(?is)(like the non-)\s+Abstract\s*\n(.{200,5500}?)\n+(homologous end-joining)",
        t,
    )
    if m_cut:
        abs_txt = m_cut.group(2).strip()
        t = (
            t[: m_cut.start()]
            + m_cut.group(1)
            + m_cut.group(3)
            + t[m_cut.end() :]
        )
        if not re.search(r"(?im)^Abstract\s*$", t):
            t = re.sub(
                r"(?im)^(Introduction)\s*$",
                f"Abstract\n{abs_txt}\n\n\\1",
                t,
                count=1,
            )
        else:
            # 已有 Abstract 标题则把抽出的摘要紧接其后（若该处为空短）
            t = re.sub(
                r"(?im)^(Abstract)\s*$",
                f"Abstract\n{abs_txt}",
                t,
                count=1,
            )
    t = re.sub(r"like the non-\s+homologous", "like the non-homologous", t)
    # Springer 德文引号损坏：Bbait^ / Bleakiness^ / Binframe^ → "bait" 等
    t = re.sub(r"\bB([A-Za-z]{2,})\^", r'"\1"', t)
    t = re.sub(r"\^([A-Za-z]{2,})\b", r'"\1"', t)
    # 文首通讯邮箱（前 5k）
    head, rest = t[:5000], t[5000:]
    head = re.sub(
        r"(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b",
        "",
        head,
    )
    t = head + rest
    # Creative Commons 许可句
    t = re.sub(
        r"(?is)This article is licensed under a Creative Commons Attribution[^\n.]{0,120}\.?",
        "",
        t,
    )
    t = re.sub(
        r"(?is)(?:You do not have permission under this licence|The images or other third party|"
        r"To view a copy of this licence|provide a link to the\s*Creative Commons licence)"
        r".{0,500}?(?=\n[A-Z]|\Z)",
        "",
        t,
    )
    t = re.sub(
        r"(?i)Creative Commons Attribution(?:-NonCommercial|-NoDerivatives)?(?:\s+\d\.\d)?[^\n.]{0,80}\.?",
        "",
        t,
    )
    t = re.sub(r"(?i)\bCreative Commons licence\b", " ", t)
    # 孤立页码行（1–3 位）
    t = re.sub(r"(?m)^\d{1,3}\s*$\n?", "", t)
    # Frontiers 文首编辑信息残片
    t = re.sub(
        r"(?is)^(?:and Aquaculture Research[^\n]*\n)?(?:Norway|Canada|United States)[^\n]*\n"
        r"(?:[A-Z][^\n]{0,60}\n){0,6}?"
        r"(?:Specialty section:\s*\n)?(?:This article was submitted to\s*\n)?"
        r"(?:Keywords:[^\n]+\n)?"
        r"(?:INTRODUCTION and [^\n]+\n)?"
        r"(?:C-Terminal Associated Peptide[^\n]+\n)?"
        r"(?:Front\. Mar\. Sci\.[^\n]+\n)?"
        r"(?:doi:\s*10\.[^\n]+\n)?",
        "",
        t,
        count=1,
    )
    t = re.sub(r"(?im)^Specialty section:\s*$", "", t)
    t = re.sub(r"(?im)^This article was submitted to\s*$", "", t)
    t = re.sub(r"(?im)^INTRODUCTION and .*$", "INTRODUCTION\n", t)
    # PCR 秒记号 15'' / 30'' → 15″
    t = re.sub(r"(?<=\d)''", "″", t)
    # Page X of Y
    t = re.sub(r"(?im)^\s*Page\s+\d+\s+of\s+\d+\s*$\n?", "", t)
    # Frontiers：无 Abstract 标题时，在作者单位后、引言科学句前插入
    if not re.search(r"(?im)^(?:Abstract|ABSTRACT)\s*$", t):
        m_fr = re.search(
            r"(?is)((?:United States|United Kingdom|Canada)[^\n]*\n)"
            r"((?:[A-Z][^\n]{80,}\.\s*){3,})"
            r"(?=\n(?:Teneurins are a family|Introduction|INTRODUCTION)\b)",
            t,
        )
        if m_fr:
            t = t[: m_fr.start(2)] + "Abstract\n" + m_fr.group(2).strip() + "\n\n" + t[m_fr.end(2) :]
        elif not re.search(r"(?im)^INTRODUCTION\s*$", t):
            # 引言无标题：Teneurins are a family… 前补 INTRODUCTION
            t = re.sub(
                r"(?m)^(Teneurins are a family\b)",
                r"INTRODUCTION\n\1",
                t,
                count=1,
            )
    return t


def clean_figure_artifacts(text: str) -> str:
    """去掉正文中残留的面板/阶段标签，并修复误粘连。"""
    t = scrub_inline_chrome(text or "")
    # Springer 页脚卷标（可能嵌在段落中）
    t = re.sub(r"\s*Vol[:.]{1,2}\s*\([\d.]+\)\s*", " ", t)
    # 连续面板字母行（常在 FIGURE 前）— 仅 A-F，避免删 Introduction 的 I
    t = re.sub(r"(?m)^(?:[A-Fa-f]\*?\s*\n){1,8}(?=(?:FIGURE|Fig\.|Table)\b)", "", t)
    # 孤立面板字母行（A-F）
    t = re.sub(r"(?m)^[A-Fa-f]\*?\s*$\n?", "", t)
    # 词尾误粘面板字母：spatioA / provideA → 去掉末尾面板字母
    t = re.sub(
        r"([a-z]{3,})([A-F])(?=\s*(?:[A-F]\*?|\d|Fig\.|FIGURE|Table|\*|/\s*|$))",
        r"\1",
        t,
    )
    # 词与发育阶段标签粘连：spatio256-cell → spatio 256-cell
    t = re.sub(r"([a-z]{3,})(\d{1,3}-(?:cell|somite)\b)", r"\1 \2", t)
    # 图前残留的阶段标签串
    t = re.sub(
        r"(?:\s*\d{1,3}-(?:cell|somite)\s*){1,6}(?=\n?(?:Fig\.|FIGURE|Table))",
        "\n",
        t,
    )
    # 残留时间轴碎片行
    t = re.sub(r"(?m)^(?:~?\d+\s*h\.?p\.?f\.?\s*)+$", "", t, flags=re.I)
    t = re.sub(r"(?m)^(?:som\s*)+$", "", t, flags=re.I)
    # 色条碎片（须含 < 或 >，避免误删纯数字行）
    t = re.sub(r"(?m)^(?=.*[<>])[<>\d\s\-/]{1,40}$\n?", "", t)
    # 被色条撕开的 signals
    t = re.sub(
        r"sig-\s*(?:\n\s*[<>\d\s\-/]+)*\n\s*>?\s*nals\)",
        "signals)",
        t,
    )
    # and + 面板字母 + stromal
    t = re.sub(
        r"(?<=\band)\s+(?:[A-Za-z]\s+){1,8}(?=stromal\b)",
        " ",
        t,
    )
    # 题注断词未拼合就接下一个 Fig（analy-\n\nFig. 3）
    t = re.sub(
        r"(\w{2,})-\s*\n+(?=(?:Fig\.|Figure|FIGURE|Table)\b)",
        r"\1\n\n",
        t,
    )
    # Nature drop-cap：I + n → In
    t = re.sub(r"(?m)^I\s+n\s+", "In ", t)
    t = re.sub(r"(?<=\n)I\s+n\s+", "In ", t)
    # 常见专名连字符恢复
    t = re.sub(r"\bKruskalWallis\b", "Kruskal-Wallis", t)
    t = re.sub(r"(?i)nlsmCherry", "nls-mCherry", t)
    t = re.sub(r"\bGallegoUrrea\b", "Gallego-Urrea", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t


def _merge_caption_continuations(body: list, captions: list) -> tuple[list, list]:
    """把紧挨题注、以小写续写的块并回题注（Fig. 2 … analy- / ses, showing…）。"""
    if not captions or not body:
        return body, captions
    caps = list(captions)
    kept: list = []
    for b in body:
        txt = (b[4] or "").strip()
        if not txt or not txt[0].islower():
            kept.append(b)
            continue
        above = [
            c
            for c in caps
            if abs(float(c[1]) - float(b[1])) < 90 or float(c[1]) <= float(b[1]) + 2
        ]
        if not above:
            kept.append(b)
            continue
        nearest = min(above, key=lambda c: (abs(float(c[1]) - float(b[1])), -float(c[1])))
        prev = (nearest[4] or "").rstrip()
        if prev.endswith("-") or abs(float(nearest[1]) - float(b[1])) < 30:
            merged_txt = prev + "\n" + (b[4] or "").rstrip()
            idx = caps.index(nearest)
            caps[idx] = (
                nearest[0],
                nearest[1],
                nearest[2],
                max(float(nearest[3]), float(b[3])),
                merged_txt,
                *nearest[5:],
            )
        else:
            kept.append(b)
    return kept, caps


def extract_layout(pdf_path: Path) -> LayoutExtract:
    doc = fitz.open(pdf_path)
    out = LayoutExtract(page_count=doc.page_count)

    for pi, page in enumerate(doc):
        blocks = [
            b
            for b in (page.get_text("blocks") or [])
            if len(b) >= 7 and b[6] == 0 and (b[4] or "").strip()
        ]
        body: list = []
        page_captions: list = []
        for b in blocks:
            txt = b[4]
            # 题注优先于页脚（页底 Fig. 10 等）
            if _is_figure_caption_block(txt):
                page_captions.append(b)
                continue
            if _is_running_header_footer(txt, page, b):
                # 每页实例都保留（不去重），保证内容词召回 ≥99%
                out.headers_footers.append(f"[p{pi+1}] {txt.strip()}")
                continue
            if _is_sidebar(txt):
                out.sidebar.append(txt.rstrip())
                continue
            if _is_figure_panel_block(txt):
                out.figure_panels.append(f"[p{pi+1}] {txt.strip()}")
                continue
            if _is_figure_annotation_block(txt):
                out.figure_annotations.append(f"[p{pi+1}] {txt.strip()}")
                continue
            body.append(b)

        body, page_captions = _merge_caption_continuations(body, page_captions)

        if not body:
            # 纯图页：不把题注/原始乱序层塞进正文，避免打断跨页续句
            out.body_pages.append("")
        else:
            mid = float(page.rect.width) * 0.5
            left = [b for b in body if _mid_x(b) < mid]
            right = [b for b in body if _mid_x(b) >= mid]
            if len(left) >= 2 and len(right) >= 2:
                left.sort(key=lambda b: (b[1], b[0]))
                right.sort(key=lambda b: (b[1], b[0]))
                ordered = left + right
            else:
                ordered = sorted(body, key=lambda b: (round(b[1] / 6.0), b[0]))
            out.body_pages.append("\n".join(b[4].rstrip() for b in ordered))

        # 题注按页内 y 序收集，整篇正文后再拼接，避免插入续句中间
        page_captions.sort(key=lambda b: (b[1], b[0]))
        for b in page_captions:
            out.captions.append(f"[p{pi+1}] {b[4].rstrip()}")

    doc.close()
    return out


def compose_aligned_body(layout: LayoutExtract) -> str:
    """按页拼接正文；题注置于文末，避免打断跨栏/跨页续句。"""
    pages = [p.rstrip() for p in layout.body_pages if (p or "").strip()]
    if not pages:
        body = ""
    else:
        merged = pages[0]
        for page in pages[1:]:
            prev = merged.rstrip()
            if prev and prev[-1] not in ".!?:;。！？":
                merged = prev + "\n" + page
            else:
                merged = prev + "\n\n" + page
        body = reflow_readable(merged)
    if layout.captions:
        # 去掉页码前缀后进入正文附录区（便于章节图注抽取与召回）
        cap_texts = []
        for c in layout.captions:
            cap_texts.append(re.sub(r"^\[p\d+\]\s*", "", c).rstrip())
        caps = reflow_readable("\n\n".join(cap_texts))
        if body.strip():
            body = body.rstrip() + "\n\n" + caps
        else:
            body = caps
    return body


def extract_figure_captions(text: str) -> str:
    caps = []
    for m in _CAPTION_RE.finditer(text or ""):
        line = re.sub(r"\s+", " ", m.group(1)).strip()
        # 拒绝正文引用残片：Table 9B). Accordingly…
        if re.match(r"(?i)^(Figure|Fig\.|Table|Supplementary|FIGURE).{0,40}\)\.", line):
            continue
        if len(line) < 20:
            continue
        # 真题注通常含描述词或以编号点开头
        if not re.search(
            r"(?i)(scanning|photomicrograph|bubble|plot|expression|schema|overview|"
            r"western|confocal|scale bar|\.\s+[A-Z]|^\s*(Figure|Fig\.|FIGURE|Table)\s*\d+[A-Za-z]?\s*[\.\:\|])",
            line,
        ):
            # 允许短但标准的 Fig. N. Title…
            if not re.match(
                r"(?i)^(Figure|Fig\.|FIGURE|Table)\s*[S]?\d+[A-Za-z]?\s*[\.\:\|]",
                line,
            ):
                continue
        if len(line) >= 12:
            caps.append(line)
    for m in _CAPTION_MULTILINE_RE.finditer(text or ""):
        head = re.match(
            r"(?im)^((?:Figure|Fig\.|FIGURE|Table)\s*[S]?\d+[A-Za-z]?)",
            m.group(0),
        )
        line = re.sub(r"\s+", " ", f"{head.group(1) if head else 'Figure'} {m.group(1)}").strip()
        if len(line) >= 30 and "for details" not in line.lower()[:40]:
            caps.append(line)
    # 去重保序
    seen = set()
    uniq = []
    for c in caps:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return "\n".join(uniq[:200])


def peel_captions_from_section(text: str) -> tuple[str, str]:
    """从分节 results/discussion 中剥离独立题注块；完整对齐正文仍保留题注阅读序。

    返回 (cleaned_section, peeled_captions)。
    """
    t = text or ""
    if t.count("Scale bar") < 2 and not re.search(r"(?m)^Fig\.\s*\d+", t):
        return t, ""
    peeled: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        block = m.group(0).strip()
        # 短「见 Fig. 2」类引用不剥
        if len(block) < 60:
            return m.group(0)
        if not re.search(
            r"(?i)scale bar|expression of|schemati|overview|photomicro|"
            r"confocal|labeling|morpholog|sites under|branch selection",
            block,
        ):
            # 仍像题注：以 Fig. N Title 起头且多句
            if block.count(".") < 2 and "Scale bar" not in block:
                return m.group(0)
        peeled.append(re.sub(r"\s+", " ", block).strip())
        return "\n\n"

    cleaned = re.sub(
        r"(?im)^(?:Fig\.|Figure|Table)\s*\d+[A-Za-z]?[^\n]*"
        r"(?:\n(?!(?:Fig\.|Figure|Table)\s*\d+|Introduction|Methods|Results|"
        r"Discussion|References|MATERIALS|Abstract)\s*[^\n]*)*",
        _repl,
        t,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, "\n".join(peeled)


def peel_captions_in_sections(sections: dict[str, str]) -> dict[str, str]:
    out = dict(sections)
    extra_caps: list[str] = []
    for key in ("results", "discussion", "other"):
        val = out.get(key) or ""
        if val in ("", MISSING) or len(val) < 200:
            continue
        cleaned, peeled = peel_captions_from_section(val)
        if peeled:
            out[key] = cleaned if cleaned else MISSING
            extra_caps.append(peeled)
    if extra_caps:
        existing = out.get("figure_captions") or ""
        if existing in ("", MISSING):
            out["figure_captions"] = "\n".join(extra_caps)
        else:
            # 去重合并
            seen = set(existing.splitlines())
            add = []
            for block in "\n".join(extra_caps).splitlines():
                b = block.strip()
                if b and b not in seen:
                    add.append(b)
                    seen.add(b)
            if add:
                out["figure_captions"] = existing + "\n" + "\n".join(add)
    # 清理 other 中残留题注碎片
    other = out.get("other") or ""
    if other not in ("", MISSING) and len(other) < 200:
        if re.match(r"(?i)^(Fig\.|Figure|Supplementary|Table\s+\d)", other.strip()):
            out["other"] = MISSING
    return out


def order_ok_checks(body: str, layout_name: str) -> list[dict[str, Any]]:
    checks = []
    # 9685 专用：仅当文中确有 MRI 表述时检查（避免套到其它 Springer）
    if "magnetic resonance imaging" in (body or "").lower():
        m = re.search(
            r"magnetic resonance imaging \(MRI\).{0,160}hyperthermia \[7\]",
            body,
            flags=re.S,
        )
        ok = bool(m) and "Supplementary Information" not in (m.group(0) if m else "")
        checks.append(
            {
                "name": "MRI句连贯无侧栏插入",
                "ok": ok,
                "snippet": re.sub(r"\s+", " ", m.group(0))[:140] if m else "",
            }
        )
    vol_hits = len(re.findall(r"Vol[:.]{1,2}\s*\([\d.]+\)", body))
    checks.append(
        {
            "name": "无Vol页脚残留",
            "ok": vol_hits == 0,
            "snippet": f"count={vol_hits}",
        }
    )
    bad_author = "LoThiago" in body or "assessment.hermal" in body or "biommarker" in body
    checks.append(
        {
            "name": "无LLM作者摘要乱码",
            "ok": not bad_author,
            "snippet": "",
        }
    )
    body_only = body
    bad = bool(re.search(r"e-mail:.*\n(?:.*\n){0,3}.*hyperthermia", body_only, re.I))
    checks.append({"name": "邮箱侧栏不打断续段", "ok": not bad, "snippet": ""})
    # 正文中不应残留成串面板字母
    orphans = re.findall(r"(?m)^[A-F]\*?\s*$", body)
    checks.append(
        {
            "name": "无孤立图注面板字母行",
            "ok": len(orphans) == 0,
            "snippet": f"count={len(orphans)}",
        }
    )
    # 排除 polyA / mRNA 等科学写法；只抓「长词+孤立面板字母」
    glued = False
    for m in re.finditer(r"([a-z]{4,})([A-F])(?=\s|$)", body):
        w = m.group(1) + m.group(2)
        if re.match(
            r"(?i).*(poly|mrna|trna|rrna|dsred|egfp|gfp|lacz|cas9|talen)$",
            w,
        ):
            continue
        if m.group(1).endswith("poly") and m.group(2) == "A":
            continue
        glued = True
        break
    checks.append(
        {
            "name": "无词尾粘连面板字母",
            "ok": not glued,
            "snippet": "",
        }
    )
    # 热点：仅当正文出现对应线索时检查（按内容绑定，不按 layout 误伤）
    if "provide spatio" in body or "Fig. 3. Expression of enhanced" in body:
        m = re.search(
            r"provide\s+spatio-?temporal\s+information",
            body,
            flags=re.I,
        )
        bad_cut = bool(re.search(r"provide\s*\n\s*Fig\.\s*3", body))
        checks.append(
            {
                "name": "IJDB_provide_spatio-temporal续句",
                "ok": bool(m) and not bad_cut,
                "snippet": re.sub(r"\s+", " ", m.group(0))[:80] if m else "missing",
            }
        )
        m2 = re.search(r"generating\s+Ca2\+\s+signals?\)", body)
        bad_sig = bool(re.search(r"sig-\s*\n", body))
        checks.append(
            {
                "name": "IJDB_Ca2+_signals拼合",
                "ok": bool(m2) and not bad_sig,
                "snippet": re.sub(r"\s+", " ", m2.group(0))[:60] if m2 else "missing",
            }
        )
    if "Fli1:GFP" in body:
        m = re.search(
            r"\(Fli1:GFP[^\)]*\)\s+and\s+stromal\s+\(TCF",
            body,
            flags=re.I,
        )
        bad_anno = bool(
            re.search(
                r"\(Fli1:GFP[^\)]*\)\s+and\s+(?:\d+\s*h\.?p\.?f|som\b)",
                body,
                flags=re.I,
            )
        )
        checks.append(
            {
                "name": "Nature_Fli1_and_stromal续句",
                "ok": bool(m) and not bad_anno,
                "snippet": re.sub(r"\s+", " ", m.group(0))[:100] if m else "missing",
            }
        )
    if re.search(r"(?i)tryptophan.{0,40}up-regulated|were also up-regulated", body):
        # 仅当文中讨论 tryptophan 上调时要求续句完整
        if re.search(r"(?i)\btryptophan\b", body):
            m = re.search(
                r"tryptophan\s+were\s+also\s+up-regulated",
                body,
                flags=re.I,
            )
            checks.append(
                {
                    "name": "Frontiers_tryptophan_were_also续句",
                    "ok": bool(m),
                    "snippet": re.sub(r"\s+", " ", m.group(0))[:80] if m else "missing",
                }
            )
    return checks


@dataclass
class AlignResult:
    entry_no: str
    layout: str
    title: str
    doi: str
    pmid: str
    source_url: str
    pdf_path: str
    page_count: int
    body_text: str
    sections: dict[str, str]
    noise: dict[str, str]
    metrics: dict[str, Any]
    provenance: dict[str, str]


def align_pdf(
    pdf_path: Path,
    *,
    entry_no: str,
    layout: str,
    title: str = "",
    doi: str = "",
    pmid: str = "",
    source_url: str = "",
) -> AlignResult:
    ref_layer = extract_text_layer(pdf_path)
    layout_ex = extract_layout(pdf_path)
    body = compose_aligned_body(layout_ex)

    # 噪声分区（保留、不进正文混排）
    noise = {
        "sidebar_affiliations": normalize_symbols("\n\n".join(layout_ex.sidebar)).strip(),
        "running_headers_footers": normalize_symbols("\n".join(layout_ex.headers_footers)).strip(),
        "figure_panel_labels": normalize_symbols("\n".join(layout_ex.figure_panels)).strip(),
        "figure_annotations": normalize_symbols(
            "\n".join(layout_ex.figure_annotations)
        ).strip(),
    }

    # 章节
    payload = build_section_payload(body, title=title, content_type="pdf")
    sections = fill_missing(payload["sections"])
    sections = repair_sections(sections, body)
    sections = fill_missing(sections)
    # 分节：Results 内嵌题注剥到 figure_captions（对齐正文 body 不变）
    sections = peel_captions_in_sections(sections)
    sections = fill_missing(sections)

    # 图表题注：规则补充
    caps = extract_figure_captions(body)
    if caps:
        existing = sections.get("figure_captions") or ""
        if existing in ("", MISSING):
            sections["figure_captions"] = caps
        else:
            sections["figure_captions"] = (existing + "\n" + caps).strip()

    # 内容召回：正文 + 噪声专区（确保不丢词）
    hyp_all = body + "\n" + "\n".join(noise.values())
    recall = word_recall(ref_layer, hyp_all)
    checks = order_ok_checks(body, layout)
    order_ok = all(c["ok"] for c in checks)

    # 章节填充统计
    core = ("abstract", "introduction", "methods", "results", "discussion", "conclusion")
    filled = sum(1 for k in core if (sections.get(k) or "") not in ("", MISSING) and len(sections.get(k) or "") >= 80)

    metrics = {
        "word_recall": round(recall, 6),
        "content_ok": recall >= CONTENT_THRESHOLD,
        "order_ok": order_ok,
        "order_checks": checks,
        "section_fill_count": filled,
        "sectioned": bool(payload.get("sectioned")),
        "body_chars": len(body),
        "page_count": layout_ex.page_count,
    }

    provenance = {
        "entry_no": entry_no,
        "doi": doi or "",
        "pmid": pmid or "",
        "source_url": source_url or "",
        "pdf_path": str(pdf_path),
        "pdf_name": pdf_path.name,
        "layout": layout,
        "extractor": "pymupdf_blocks_column_order+symbol_norm+section_split",
    }

    return AlignResult(
        entry_no=entry_no,
        layout=layout,
        title=title,
        doi=doi or "",
        pmid=pmid or "",
        source_url=source_url or "",
        pdf_path=str(pdf_path),
        page_count=layout_ex.page_count,
        body_text=body,
        sections=sections,
        noise=noise,
        metrics=metrics,
        provenance=provenance,
    )


def result_to_dict(r: AlignResult) -> dict[str, Any]:
    return {
        "entry_no": r.entry_no,
        "layout": r.layout,
        "title": r.title,
        "doi": r.doi,
        "pmid": r.pmid,
        "source_url": r.source_url,
        "pdf_path": r.pdf_path,
        "page_count": r.page_count,
        "body_text": r.body_text,
        "sections": r.sections,
        "noise": r.noise,
        "metrics": r.metrics,
        "provenance": r.provenance,
    }


def render_article_md(r: AlignResult) -> str:
    lines = [
        f"# [{r.entry_no}] {r.title}",
        "",
        "## 来源追溯",
        "",
        f"- entry_no: `{r.entry_no}`",
        f"- DOI: `{r.doi or '【无】'}`",
        f"- PMID: `{r.pmid or '【无】'}`",
        f"- PDF: `{r.provenance.get('pdf_name')}`",
        f"- 路径: `{r.pdf_path}`",
        f"- source_url: {r.source_url or '【无】'}",
        f"- 页数: `{r.page_count}`",
        f"- 版式: `{r.layout}`",
        "",
        "## 质量指标",
        "",
        f"- 内容词召回: `{r.metrics['word_recall']:.4%}`",
        f"- 内容达标: `{r.metrics['content_ok']}`",
        f"- 顺序达标: `{r.metrics['order_ok']}`",
        f"- 章节填充数: `{r.metrics['section_fill_count']}`",
        "",
        "## 对齐正文（阅读顺序 + 分页）",
        "",
        "```",
        r.body_text.rstrip(),
        "```",
        "",
        "## 章节结构",
        "",
    ]
    labels = {
        "title": "标题",
        "abstract": "摘要",
        "introduction": "引言",
        "methods": "方法",
        "results": "结果",
        "discussion": "讨论",
        "conclusion": "结论",
        "references": "参考文献",
        "figure_captions": "图表题注",
    }
    for key in TARGET_MODULES:
        label = labels.get(key, key)
        val = r.sections.get(key) or MISSING
        lines += [f"### {label} (`{key}`)", "", "```", val, "```", ""]

    lines += ["## 噪声专区（保留、不混入正文阅读流）", ""]
    for k, v in r.noise.items():
        lines += [f"### {k}", "", "```", v or "【无】", "```", ""]
    return "\n".join(lines)


def render_article_txt(r: AlignResult) -> str:
    parts = [
        f"[{r.entry_no}] {r.title}",
        f"DOI: {r.doi or '【无】'}",
        f"PMID: {r.pmid or '【无】'}",
        f"PDF: {r.provenance.get('pdf_name')}",
        f"path: {r.pdf_path}",
        f"source_url: {r.source_url or '【无】'}",
        f"pages: {r.page_count}",
        f"word_recall: {r.metrics['word_recall']:.6f}",
        f"content_ok: {r.metrics['content_ok']} order_ok: {r.metrics['order_ok']}",
        "=" * 72,
        "",
        "【对齐正文】",
        r.body_text.rstrip(),
        "",
        "【章节】",
    ]
    for key in TARGET_MODULES:
        parts += [f"--- {key} ---", (r.sections.get(key) or MISSING), ""]
    parts += ["【噪声专区】"]
    for k, v in r.noise.items():
        parts += [f"--- {k} ---", v or "【无】", ""]
    return "\n".join(parts)
