# -*- coding: utf-8 -*-
"""Hub 问答意图路由：五档 tier，替换二元 in-scope / OFF_TOPIC 闸门。

A 斑马鱼科学 → 全链路 Hub
B 产品/元问题 → 短路说明
C 邻域生物 → 通用专业回答（不编造本地库）
D 闲聊/一般 → 正常助手回答
E 有害 → 简短安全拒答
"""
from __future__ import annotations

import gzip
import logging
import os
import re
import threading
import time
from typing import Any, Optional

_log = logging.getLogger(__name__)

# 对外稳定别名（agent_scheduler / hub_orchestrator 使用）
TIER_A = INTENT_A = "A"
TIER_B = INTENT_B = "B"
TIER_C = INTENT_C = "C"
TIER_D = INTENT_D = "D"
TIER_E = INTENT_E = "E"

TIER_NAMES = {
    TIER_A: "zebrafish_science",
    TIER_B: "product_meta",
    TIER_C: "adjacent_biology",
    TIER_D: "general_chitchat",
    TIER_E: "harmful",
}

HARMFUL_REPLY = (
    "这个请求涉及危险或违法内容，我不能提供帮助。"
    "如果有斑马鱼基因、基因组或文献相关问题，我很乐意思答。"
)

_HARMFUL_RE = re.compile(
    r"("
    r"制作.{0,8}(炸弹|炸药|毒气|毒药|毒品)|"
    r"(炸弹|炸药|制毒|冰毒|海洛因).{0,12}(怎么|如何|教程|配方)|"
    r"(杀人|谋杀|暗杀).{0,8}(方法|教程)|"
    r"(自杀|自残).{0,10}(方法|怎么|如何|教程)|"
    r"(儿童|未成年).{0,20}(色情|性|裸)|"
    r"csam|child\s*porn|"
    r"(黑客入侵|盗取密码|绕过验证码).{0,12}(教程|怎么)|"
    r"(制造|合成).{0,8}(沙林|芥子气|蓖麻毒素)|"
    r"how\s+to\s+(make|build)\s+(a\s+)?(bomb|explosive|poison)|"
    r"weaponize|bioweapon"
    r")",
    re.I,
)

_ZEBRAFISH_EXPLICIT_RE = re.compile(
    r"("
    r"斑马鱼|zebrafish|danio\s*rerio|\bdanio\b|\brerio\b|"
    r"\bzfin\b|zdb-gene|grcz\d*|ensembl\s*release|"
    r"ensdarg|gcf_049306965|gcf_000002035|"
    r"鱼苗|胚胎.{0,6}(鱼|zebrafish)|"
    r"原位杂交|morpholino|\bcrispr.{0,12}(斑马鱼|zebrafish|danio)"
    r")",
    re.I,
)

_ZF_GENE_MARKERS = (
    "tp53", "p53", "hsp70", "sox9", "sox9a", "sox9b", "nanog", "myl7",
    "cmlc2", "noto", "ntl", "tbxta", "shha", "shhb", "fgf8a", "chordin",
    "bmp4", "gata1a", "tal1", "kdrl", "flk1", "vegfaa", "heartstrings",
)

_ZF_SCIENCE_KW = (
    "基因", "基因组", "表达", "转录", "染色体", "突变", "敲除", "敲入",
    "转基因", "等位", "表型", "性状", "通路", "富集", "同源", "注释",
    "go", "kegg", "blast", "cdna", "mrna", "refseq", "ensembl",
    "组装", "参考基因组", "文献", "pmid", "发育", "再生", "毒性",
    "缺氧", "应激", "胚胎", "品系", "等位基因",
    "文獻",
)

_ADJACENT_BIO_RE = re.compile(
    r"("
    r"小鼠|大白鼠|大鼠|\bmouse\b|\bmice\b|\brat\b|人类基因|人源|"
    r"果蝇|酵母|线虫|拟南芥|哺乳动物|脊椎动物|"
    r"western\s*blot|pcr|qpcr|rna[- ]?seq|chip[- ]?seq|"
    r"克隆|质粒|转染|细胞系|hela|细胞培养|"
    r"孟德尔|遗传学|表观遗传|基因组学|蛋白质组|"
    r"抗体|抗原|免疫组化|流式|"
    r"crispr|cas9|knockout|knockin|homolog|"
    r"dna\b|rna\b|蛋白|酶|代谢|通路|信号转导|"
    r"癌症|肿瘤|神经科学|发育生物学"
    r")",
    re.I,
)

_GENERAL_RE = re.compile(
    r"("
    r"天气|气温|下雨|股票|比特币|基金|理财|"
    r"写代码|写个脚本|帮我写|编程题|leetcode|"
    r"python|javascript|java\b|typescript|golang|"
    r"王者荣耀|游戏攻略|做饭|菜谱|旅游|机票|"
    r"讲个笑话|你好|早上好|晚安|谢谢|在吗|"
    r"chatgpt怎么用|帮我写作业|作文|"
    r"翻译成|translate\b|summarize\b|"
    r"what'?s\s+the\s+weather|hello\b|hi\b|hey\b"
    r")",
    re.I,
)

_ID_RE = re.compile(
    r"(ZDB-GENE-\d+-\d+|ENS[A-Z]*G\d{8,}(?:\.\d+)?|"
    r"(?:NM|NR|NP|XM|XR|NC)_\d+(?:\.\d)?)",
    re.I,
)

# 明确指向「别的物种」——此时即使基因符号命中，也应走邻域生物车道，
# 否则「小鼠的 p53 有哪些结构域」会被当成斑马鱼问题去查本地库。
_OTHER_SPECIES_RE = re.compile(
    r"(小鼠|大白鼠|大鼠|人类|人体|病人|患者|果蝇|酵母|线虫|拟南芥|家鸡|"
    r"斑马鱼以外|非斑马鱼|"
    r"人\s?(?=[a-zA-Z]{0,4}\d)|"  # 「人p53」「人 TP53」：中文里人 + 带数字的基因符号
    r"\bmouse\b|\bmice\b|\brat\b|\bhuman\b|\bdrosophila\b|"
    r"\byeast\b|\bc\.?\s?elegans\b|\barabidopsis\b|\bchicken\b|\bxenopus\b)",
    re.I,
)

# 指代性生物词：「这个基因 / 该蛋白 / 这条通路」。用户没说是哪个，但意图明确是
# 科学问题，应走专业车道由它反问，而不是当闲聊答。
_DEOCTIC_BIO_RE = re.compile(
    r"(这个|该|此|这条|那条|上述)(基因|蛋白|通路|突变|表型|转录本|等位)", re.I
)


def safety_refusal_reply() -> str:
    return HARMFUL_REPLY


_COORD_INTENT_RE = re.compile(
    r"(坐标换算|座標換算|坐标转换|座標轉換|liftover|lift[- ]over|"
    r"GRCz11.{0,24}(换成|換成|转到|轉到|换算|換算).{0,12}GRCz12|"
    r"(?:chr)?\d{1,2}\s*:\s*\d+\s*[-–]\s*\d+)",
    re.I,
)
_ID_INTENT_RE = re.compile(
    r"(编号转换|編號轉換|跨库\s*ID|跨庫\s*ID|id[- ]convert|基因编号|基因編號)",
    re.I,
)


def detect_tool_intent(question: str) -> Optional[str]:
    """程序分流：坐标换算与编号转换分开。坐标题回 coord_liftover。"""
    text = question or ""
    if _COORD_INTENT_RE.search(text):
        return "coord_liftover"
    if _ID_INTENT_RE.search(text):
        return "id_crosswalk"
    return None


def _pack(tier: str, confidence: float, reasons: list[str]) -> dict:
    return {
        "tier": tier,
        "name": TIER_NAMES.get(tier, tier),
        "confidence": confidence,
        "reasons": reasons,
    }


def _done(question: str, tier: str, confidence: float, reasons: list[str]) -> dict:
    packed = _pack(tier, confidence, reasons)
    packed["tool_intent"] = detect_tool_intent(question)
    return packed


def _meta_tier(question: str) -> bool:
    try:
        from zebrafish_prompt import (
            is_system_meta_question,
            is_hub_product_howto_question,
            is_capability_question,
        )
        from gene_formatter import is_citation_display_question

        # 「你能做什么」优先：它要的是能力清单，不是模型自述
        if is_capability_question(question):
            return True
        if is_system_meta_question(question):
            return True
        if is_hub_product_howto_question(question):
            return True
        if is_citation_display_question(question):
            return True
    except Exception:
        pass
    return False


def _has_zf_gene_marker(q: str) -> bool:
    """白名单常见斑马鱼基因。允许 a/b/l 之类旁系同源后缀：
    `hsp70` 也要能匹配 `hsp70a` / `hsp70b`，否则「hsp70a 和 hsp70b 的功能差异」
    会因为白名单只写了 hsp70 而落到通用车道，完全不查本地库。"""
    low = q.lower()
    for g in _ZF_GENE_MARKERS:
        if re.search(rf"(?<![a-z0-9]){re.escape(g)}[a-z]{{0,2}}(?![a-z0-9])", low):
            return True
    return False


# ── 本地斑马鱼基因符号词典 ────────────────────────────────────────────
# 白名单只有二十几个基因，本地库实际有 4.6 万个符号。用户问一个不在白名单里的
# 真基因（如 elavl1b、sox19a、kit）时，靠关键词打分常常凑不够阈值，会被当成通用
# 闲聊，于是既不查本地库也不给证据。这里用 NCBI Danio_rerio.gene_info 建符号集合，
# 命中即视为强证据。懒加载 + 6 小时过期，加载约 0.15s。
_GENE_LEXICON: Optional[set] = None
_GENE_LEXICON_AT = 0.0
_GENE_LEXICON_LOCK = threading.Lock()
_GENE_LEXICON_TTL = 6 * 3600

_TOKEN_RE = re.compile(r"[a-z][a-z0-9]{1,15}")

# 看着像基因名、其实是常见技术词 / 产品名，不能当作斑马鱼基因证据
_NOT_GENE_TOKENS = {
    "python3", "python2", "utf8", "utf16", "utf32", "mp3", "mp4", "h264", "h265",
    "covid19", "covid", "iphone", "ipad", "ios15", "http2", "http3", "windows10",
    "windows11", "office365", "gpt4", "gpt3", "gpt5", "chatgpt", "usb3", "wifi6",
    "rtx4090", "gtx1080", "web3", "es6", "bs4", "vue3", "css3", "html5",
}

_GENE_CTX_WORDS = (
    "基因", "表达", "突变", "敲除", "敲低", "敲入", "蛋白", "转录", "等位",
    "同源", "染色体", "表型", "原位", "抗体", "探针", "胚胎", "斑马鱼",
    "gene", "expression", "mutation", "knockout", "knockdown", "protein",
    "allele", "ortholog", "phenotype",
)


def _lexicon_candidates() -> list[str]:
    cands: list[str] = []
    env = (os.environ.get("ZF_GENE_INFO") or "").strip()
    if env:
        cands.append(env)
    try:
        from config import _BASE_DIR  # type: ignore

        cands.append(os.path.join(_BASE_DIR, "data", "ncbi_cache", "Danio_rerio.gene_info.gz"))
    except Exception:
        pass
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands.append(os.path.join(here, "data", "ncbi_cache", "Danio_rerio.gene_info.gz"))
    out: list[str] = []
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out


def _gene_lexicon() -> set:
    """斑马鱼基因符号 + 同义名（小写）。读不到文件时返回空集，路由退化为原规则。"""
    global _GENE_LEXICON, _GENE_LEXICON_AT
    now = time.time()
    if _GENE_LEXICON is not None and now - _GENE_LEXICON_AT < _GENE_LEXICON_TTL:
        return _GENE_LEXICON
    with _GENE_LEXICON_LOCK:
        if _GENE_LEXICON is not None and time.time() - _GENE_LEXICON_AT < _GENE_LEXICON_TTL:
            return _GENE_LEXICON
        lex: set = set()
        for path in _lexicon_candidates():
            if not os.path.isfile(path):
                continue
            try:
                opener = gzip.open if path.endswith(".gz") else open
                with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if line.startswith("#"):
                            continue
                        cols = line.rstrip("\n").split("\t")
                        if len(cols) < 5:
                            continue
                        sym = (cols[2] or "").strip().lower()
                        if len(sym) >= 3 and sym != "-":
                            lex.add(sym)
                        for alias in (cols[4] or "").split("|"):
                            alias = alias.strip().lower()
                            if len(alias) >= 3 and alias != "-":
                                lex.add(alias)
                if lex:
                    break
            except Exception as e:  # 词典缺失不影响主流程
                _log.warning(f"[路由] 基因符号词典读取失败 {path}: {e}")
        _GENE_LEXICON = lex
        _GENE_LEXICON_AT = time.time()
        if not lex:
            _log.warning("[路由] 未找到斑马鱼基因符号词典，基因判定仅依赖关键词")
        return lex


def gene_tokens_in(question: str) -> list[str]:
    """挑出问题里确实命中本地基因符号的 token。

    分强弱两档，避免 `cat` / `kit` / `jun` 这类恰好是基因符号的普通英文词把闲聊
    误判成科学问题：
      · 强：长度 ≥4 的符号，或带数字的符号（tp53 / myl7 / cmlc2 / p53）
      · 弱：长度 3 的纯字母符号（cat / kit / jun）
    返回值前缀 "!" 表示强命中、"?" 表示弱命中。旁系同源后缀（hsp70a → hsp70）
    按去掉尾部 1～2 个字母的词干再查一次。
    """
    lex = _gene_lexicon()
    if not lex:
        return []
    hits: list[str] = []
    for m in _TOKEN_RE.finditer((question or "").lower()):
        tok = m.group(0)
        if tok in _NOT_GENE_TOKENS:
            continue
        candidate = None
        if tok in lex:
            candidate = tok
        elif len(tok) >= 5 and tok[-1].isalpha() and tok[:-1] in lex:
            candidate = tok[:-1]
        elif len(tok) >= 6 and tok[-2:].isalpha() and tok[:-2] in lex:
            candidate = tok[:-2]
        if not candidate:
            continue
        has_digit = any(c.isdigit() for c in tok)
        strong = len(candidate) >= 4 or has_digit
        hits.append(("!" if strong else "?") + tok)
    return hits



def _zebrafish_science_score(q: str) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    if _ZEBRAFISH_EXPLICIT_RE.search(q):
        score += 0.7
        reasons.append("explicit_zebrafish")
    if _ID_RE.search(q):
        score += 0.45
        reasons.append("gene_or_seq_id")
    if _has_zf_gene_marker(q):
        score += 0.35
        reasons.append("common_zf_gene")
    try:
        toks = gene_tokens_in(q)
    except Exception as e:  # 词典异常不能拖垮路由
        _log.debug(f"[路由] 基因符号匹配跳过：{e}")
        toks = []
    strong = [t[1:] for t in toks if t.startswith("!")]
    weak = [t[1:] for t in toks if t.startswith("?")]
    if strong:
        score += 0.45
        reasons.append("gene_symbol:" + ",".join(strong[:3]))
    elif weak:
        # 3 字母符号单看太弱，容易把闲聊误判成科学问题；
        # 配上「基因 / 表达 / 突变」这类语境词就足够可信（「kit 基因的突变」）。
        low_q = q.lower()
        has_ctx = any(k in low_q for k in _GENE_CTX_WORDS)
        score += 0.45 if has_ctx else 0.12
        reasons.append(
            ("gene_symbol_ctx:" if has_ctx else "gene_symbol_weak:") + ",".join(weak[:3])
        )
    try:
        from gene_toolkit import question_has_digit_leading_gene

        if question_has_digit_leading_gene(q):
            score += 0.45
            reasons.append("digit_leading_symbol")
    except Exception:
        pass
    low = q.lower()
    hit_kw = [k for k in _ZF_SCIENCE_KW if k in low]
    if hit_kw:
        score += min(0.25, 0.06 * len(hit_kw))
        reasons.append(f"science_kw:{','.join(hit_kw[:4])}")
    try:
        from gene_formatter import is_biology_mechanism_question

        if is_biology_mechanism_question(q) and (
            _ZEBRAFISH_EXPLICIT_RE.search(q) or _has_zf_gene_marker(q) or "基因" in q
        ):
            score += 0.35
            reasons.append("mechanism_science")
    except Exception:
        pass
    if re.search(
        r"(基因.{0,8}(是什么|功能|表达|位于|染色体)|"
        r"(功能|表达|位于哪).{0,6}基因|"
        r"哪些组织表达|转录本|等位基因)",
        q,
    ):
        score += 0.3
        reasons.append("gene_qa_pattern")
    if _DEOCTIC_BIO_RE.search(q):
        score += 0.35
        reasons.append("deictic_bio")
    # 「某某有哪些文献/文獻」会命中关键词。短名单以外的基因符号本身没有加分，
    # 会在进入文献范围之前被当成闲聊。能对到本地基因或别名时，留在科学问答。
    if re.search(r"文[献獻]", q):
        try:
            from ncbi_gene_bibliography import genes_in_question

            found = genes_in_question(q, limit=1)
            if found.get("genes") or found.get("ambiguous"):
                score += 0.6
                reasons.append("resolvable_gene_literature")
        except Exception:
            pass
    return score, reasons


def classify_question_intent(
    question: str,
    history: Optional[list[Any]] = None,
) -> dict:
    """返回 {tier, name, confidence, reasons}。history 用于短追问消歧。"""
    q = (question or "").strip()
    if len(q) < 1:
        return _done(q, TIER_D, 0.4, ["empty"])

    if _HARMFUL_RE.search(q):
        return _done(q, TIER_E, 0.95, ["harmful_pattern"])

    if _meta_tier(q):
        return _done(q, TIER_B, 0.95, ["product_or_meta"])

    zf_score, zf_reasons = _zebrafish_science_score(q)
    if zf_score >= 0.55:
        # 明确问的是别的物种（且没提斑马鱼）：交给邻域生物车道，别拿本地斑马鱼库硬答
        if _OTHER_SPECIES_RE.search(q) and not _ZEBRAFISH_EXPLICIT_RE.search(q):
            return _done(q, TIER_C, 0.8, ["other_species", *zf_reasons])
        return _done(q, 
            TIER_A,
            min(0.98, 0.55 + zf_score * 0.3),
            zf_reasons,
        )

    if history and len(q) < 40:
        last_user = ""
        for item in reversed(history or []):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role in ("user", "human"):
                last_user = str(item.get("content") or item.get("text") or "").strip()
                break
        if last_user and last_user != q:
            prev = classify_question_intent(last_user, history=None)
            if prev.get("tier") == TIER_A and re.search(
                r"^(那|那么|还有|继续|为什么|怎么|详细|具体|同上|这个|它|该基因)",
                q,
            ):
                return _done(q, TIER_A, 0.75, ["followup_after_A", *zf_reasons])

    if _GENERAL_RE.search(q) and zf_score < 0.35:
        return _done(q, TIER_D, 0.85, ["general_pattern"])

    # 明确问别的物种 + 有生物语境：走邻域生物车道（通用专业回答，不查本地斑马鱼库）
    if _OTHER_SPECIES_RE.search(q) and not _ZEBRAFISH_EXPLICIT_RE.search(q):
        low_q = q.lower()
        if (
            any(k in low_q for k in _GENE_CTX_WORDS)
            or _ADJACENT_BIO_RE.search(q)
            or _DEOCTIC_BIO_RE.search(q)
        ):
            return _done(q, TIER_C, 0.78, ["other_species_ctx", *zf_reasons])

    if _ADJACENT_BIO_RE.search(q) and not _ZEBRAFISH_EXPLICIT_RE.search(q):
        return _done(q, TIER_C, 0.8, ["adjacent_biology", *zf_reasons])

    if zf_score >= 0.35:
        return _done(q, TIER_A, 0.65, ["likely_zf_science", *zf_reasons])

    return _done(q, TIER_D, 0.55, ["default_general", *zf_reasons])


def is_hub_science_tier(tier: str) -> bool:
    return (tier or "").upper() == TIER_A


def is_general_lane_tier(tier: str) -> bool:
    """C/D 走通用助手（E 由调用方单独安全拒答）。"""
    return (tier or "").upper() in (TIER_C, TIER_D)
