# -*- coding: utf-8 -*-
"""引用相关性打分 RRS（Reference Relevance Score）—— 文献条目级打分。

为什么需要它
------------
文末参考文献原先的办法是「正文引过的 + 按证据池顺序补足下限」
（见 `llm/zebrafish_prompt.py::_pick_footer_ns` 与 `ensure_reference_footer`）。
证据池里一旦混进弱相关条目（跨库检索的常见副作用），就会按序号被补进文末，
出现「问 nanog 却挂纳米颗粒/纳米塑料论文」这类无关引用。

本模块把「谁有资格进文末表」从「按序号凑数」改成**可复算的打分 + 门槛**，
沿用既有 7 维评分细则（`docs/paper/data_package/01_benchmark/rubric.md`）的
「显式权重 / 可复算 / 有下限」思路，但对象换成单条文献。

维度与权重（满分 1.0，权重和为 1）
--------------------------------
| 维 | 权重 | 取值 |
| --- | --- | --- |
| `entity`  | 0.30 | 问句里的基因符号/ID 在题名中按独立词命中 → 1.0，否则 0.0 |
| `rank`    | 0.25 | 证据池排序（跨语言语义检索的结果）：绝对位次 0.6 + 相对位次 0.4 |
| `cite`    | 0.20 | 正文出现过 `[n]`（模型确实用了这条证据）→ 1.0，否则 0.0 |
| `recency` | 0.15 | ≤5 年 1.0 / ≤10 年 0.8 / ≤20 年 0.5 / 更早 0.3 / 年不详 0.2 |
| `meta`    | 0.10 | DOI 0.4 + PMID 0.4 + 真实刊名 0.2 |

环境变量：`REF_RRS_MIN`（有实体问句门槛，默认 0.50）、
`REF_RRS_MIN_NO_ENTITY`（无实体问句门槛，默认 0.40）、
`REF_RRS_HARD_ENTITY`（默认 1）、`REF_RRS_DEBUG`（默认 0）。

判据（三条硬规则）
----------------
定位：**原检索链之上的加权精排层**（原链不动）：
    FAISS 粗排 500 → CrossEncoder 精排 100 → 【本层：RRS 五维加权重排】取前 25/12 → 文末 20~25 条
1. 五维加权求和得 RRS（0~1），**只用于排序**，不做门槛淘汰
   （`REF_RRS_MIN` 仅在显式传 `min_score=` 时生效，留给调试/回归）。
   · 实体 0.30：题名是否按独立词命中问句里的基因符号/ID
   · 池内位次 0.25：上游精排名次（绝对档位 0.6 + 相对位次 0.4）
   · 正文引用 0.20：答案正文里是否引用了它
   · 时效 0.15：按年份新旧
   · 元数据 0.10：DOI / PMID / 真实刊名 是否齐全
   然后再加上**对数影响因子加成**：`RRS += IF_K · ln(1 + IF)`（不是“归一化值×权重”）；
   IF_K 默认 0.01，未知刊不加成不扣分。
2. 问句点名了实体（基因符号/ID）时：`entity = 0` 且正文未引用 → **直接淘汰**。
   这条是治「查 A 基因却挂 B 主题论文」的关键——它把无可辩驳的证据去掉，
   而不是靠关键词黑名单（换成「纳米颗粒对胚胎免疫的影响」这类问句，相关文献照样能过，
   因为闸门是**随问句变化的**）。
3. 问句没有实体（表型/机制类问题）时 `entity` 维不参与，
   其余各维（含「对数影响因子」）权重按比例重新归一化。

数量控制：加权序取前 `REF_FOOTER_MAX`（机制题 25 / 事实题 12），
下游 `ensure_reference_footer` 再补到 `REF_FOOTER_MIN`（20）→ 最终 20~25 条。
环境变量：
- `REF_RRS_MIN` / `REF_RRS_MIN_NO_ENTITY`（0.50 / 0.40）门槛，**默认流程不用**
- `REF_RRS_HARD_ENTITY`（默认 `1`）是否启用第 2 条硬规则
- `REF_RRS_DEBUG`（默认 `0`）为 1 时把逐条打分写日志

本模块只做纯文本判断：不联网、不调模型、结果可复算。
"""
from __future__ import annotations

import math
import os
import re

__all__ = [
    "extract_entities",
    "score_paper",
    "select_relevant",
    "rrs_min",
    "rrs_min_no_entity",
    "hard_entity_enabled",
    "WEIGHTS",
    "if_score_of",
    "if_log_of",
    "if_k",
]

# 权重（和为 1.0）；无实体问句时 entity 权重按比例分摊给其余各维
#
# 关于「对数影响因子」（2026-10-11 用户澄清「是影响因子取对数，不是写死权重」）：
#   它**不是**归一化值×固定权重，而是把 `ln(1+IF)` 本身作为**加成项**直接加在总分上：
#       RRS = 0.30·entity + 0.25·rank + 0.20·cite + 0.15·recency + 0.10·meta
#             + IF_K · ln(1 + IF)
#   取对数就是它的全部意义：IF 从 1.7 → 50.5 是 30 倍，而对数只把它压成 4 倍，
#   这样高刊能稳定排在「同等相关」的前面，又不会把离题的高分刊顶上来。
#   IF_K 默认 0.01：Nature（IF 50.5 → ln1p=3.94）加成 ≈ +0.039，
#   与检索层 blend 里 `w_if=0.04` 的量级一致（同一个数量级，不是同一个算法）。
#   未知刊/无 IF 值：**不加成也不扣分**（log 项取 0）。
WEIGHTS = {
    "entity": 0.30,
    "rank": 0.15,      # 上游综合排序名次（含年份与 IF 的 blend，不只是语义）
    "rel": 0.10,       # 上游**连续语义相关度** rel_n（0~1）—— 待证据池带出 rel= 分数
    "cite": 0.20,
    "recency": 0.15,
    "meta": 0.10,
}

# 相关度总量仍是 0.75（entity+rank+rel+cite）；把原来 rank 的 0.25 拆成「位次 0.15 + 连续分 0.10」，
# 因为位次里还混着年份与 IF 的 blend，不能把「名次」当成纯相关度。
# 拿不到 rel 分数时（旧证据池 / Hub 小库路径）→ rel 的 0.10 自动退给 rank，
# 使总分尺度与改动前完全一致（已用回归测试锁定）。
REL_WEIGHT_FALLBACK_TO = "rank"


def if_k() -> float:
    """对数影响加成的缩放系数 K（`REF_RRS_IF_K`，默认 0.01）。

    为什么要这个系数：`ln(1+IF)` 的取值在 0~4.6，直接加上去会把 5 维加权（0~1）里
    的相关性信号冲淡；乘 0.01 后最大加成 ≈0.046，刚好在「能微调排序、不主导排序」的区间。
    """
    try:
        v = float(os.getenv("REF_RRS_IF_K", "") or "")
    except (TypeError, ValueError):
        v = 0.0
    return v if v > 0 else 0.01

# 绝对位次分档（证据池序号 → 分）
_RANK_STEPS = ((3, 1.0), (8, 0.85), (15, 0.7), (25, 0.5), (40, 0.35))
_RANK_FLOOR = 0.20

# 明确的标识符形态（直接当实体，置信度最高）
# 注意：Python 的 \b 把中文也当词字符，中英混排（「斑马鱼nanog基因」）会拆不出词，
# 所以这里统一用显式 ASCII 边界。
_A = r"(?<![A-Za-z0-9])"
_B = r"(?![A-Za-z0-9])"
_ID_PATTERNS = (
    re.compile(_A + r"ZDB-GENE-\d{6}(?:-\d{3,4})?" + _B, re.I),
    re.compile(_A + r"ENSDAR[GTP]\d{11}" + _B, re.I),
    re.compile(_A + r"(?:WP|R-DRE-)\w{3,}" + _B, re.I),
)

# 小写词形 token（长度 2–12，允许内部连字符/点）；同样用 ASCII 边界
try:
    _TOKEN_RE = re.compile(_A + r"[A-Za-z][A-Za-z0-9.\-]{1,11}" + _B)
except re.error:  # pragma: no cover - 正则异常时退回旧写法
    _TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9.\-]{1,11}\b")

# 非实体词表：功能词、常用英文、以及「主题名词」（它们描述话题，不是基因符号）。
# 命中这些词**不**作为实体，避免把它们变成硬门槛（例如 hypoxia 不该淘汰无 hypoxia 题名的文献）。
_STOPWORDS = {
    # 英文功能词
    "the", "and", "for", "with", "from", "that", "this", "what", "how", "why", "which",
    "when", "where", "does", "did", "are", "is", "was", "were", "has", "have", "can",
    "could", "would", "should", "about", "into", "than", "then", "them", "they", "you",
    "your", "our", "its", "his", "her", "not", "also", "more", "most", "any", "all",
    "list", "show", "give", "tell", "explain", "introduce", "find", "search", "please",
    # 模式生物与通用名词
    "zebrafish", "danio", "rerio", "gene", "genes", "genome", "genomic", "mrna", "cdna",
    "protein", "proteins", "sequence", "sequences", "promoter", "transcript", "transcripts",
    "wildtype", "wild", "type", "mutant", "mutants", "mutation", "mutations", "allele",
    "knockdown", "knockout", "knockin", "crispr", "morpholino", "sirna", "shrna",
    # 功能/表型/过程类主题词
    "expression", "regulation", "regulate", "function", "functional", "role", "mechanism",
    "development", "developmental", "embryo", "embryos", "embryonic", "larvae", "larval",
    "adult", "juvenile", "tissue", "tissues", "cell", "cells", "cellular", "organ",
    "hypoxia", "apoptosis", "apoptotic", "inflammation", "inflammatory", "immune",
    "immunity", "stress", "toxicity", "toxic", "toxicology", "exposure", "treatment",
    "nanoparticle", "nanoparticles", "nanoplastic", "nanoplastics", "nanomaterial",
    "nanomaterials", "silver", "carbon", "nanotube", "nanotubes", "fullerene", "drug",
    "drugs", "disease", "cancer", "tumor", "tumour", "signaling", "signalling", "pathway",
    "pathways", "network", "behavior", "behaviour", "morphology", "phenotype", "phenotypic",
    "heart", "brain", "liver", "kidney", "muscle", "retina", "retinal", "eye", "blood",
    "bone", "skin", "gut", "intestine", "intestinal", "neuron", "neural", "neural",
    "regeneration", "regenerative", "repair", "pigment", "pigmentation", "spatial",
    "single", "transcriptomic", "transcriptomics", "proteomic", "proteomics", "omics",
    "analysis", "enrichment", "annotation", "database", "database", "atlas", "tool",
    "human", "mouse", "rat", "mammal", "mammalian", "conserved", "homolog", "homology",
    "ortholog", "orthologs", "paralog", "family", "domain", "motif", "binding",
}


def _float_env(key: str, default: float) -> float:
    try:
        v = float((os.getenv(key) or "").strip())
    except (TypeError, ValueError):
        return default
    return v


def _bool_env(key: str, default: bool = True) -> bool:
    raw = (os.getenv(key) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def rrs_min() -> float:
    """入选门槛，默认 0.50。"""
    return min(0.95, max(0.05, _float_env("REF_RRS_MIN", 0.50)))


def rrs_min_no_entity() -> float:
    """无实体问句的门槛（默认 0.40）。

    这类问句用不上 `entity` 维，四维满打满算只有 0.70，若沿用 0.50 会把
    位次尚可但元数据偏弱的正常条目误伤；0.40 相当于「位次靠前 或 有正文引用
    + 元数据完整」就收。
    """
    return min(0.95, max(0.05, _float_env("REF_RRS_MIN_NO_ENTITY", 0.40)))


def hard_entity_enabled() -> bool:
    """是否启用「点名了实体但题名不含该实体 → 直接淘汰」。"""
    return _bool_env("REF_RRS_HARD_ENTITY", True)


def extract_entities(question: str, limit: int = 6) -> list[str]:
    """从问句里抽出「基因符号 / 数据库 ID」这类实体词。

    只认三类，宁可漏也不误伤（误伤会把不该淘汰的文献淘汰掉）：
    1. 明确的 ID 形态：ZDB-GENE-… / ENSDARG… / ENSDART… / ENSDARP…
    2. 含数字的短词：tp53、pax6a、cd4、sox2
    3. 不含数字的小写短词且不在非实体词表里：nanog、myod、bmp2b 等

    返回小写、去重、保序（最多 limit 个）。
    """
    q = (question or "").strip()
    if not q:
        return []
    out: list[str] = []
    # 先抽 ID，并把命中的片段从问句里抹掉，避免同一个 ID 又被 token 规则切成碎片
    scan = q
    for rx in _ID_PATTERNS:
        for m in list(rx.finditer(q)):
            out.append(m.group(0).lower())
        scan = rx.sub(" ", scan)
    for m in _TOKEN_RE.finditer(scan):
        tok = m.group(0)
        low = tok.lower()
        if low in _STOPWORDS:
            continue
        digits = any(c.isdigit() for c in tok)
        if digits and len(tok) <= 12:
            out.append(low)
            continue
        # 纯字母：只收短小写词（nanog/tp53 类），大写开头多为句首或英文普通词
        if len(tok) <= 10 and tok.isalpha() and tok.islower():
            out.append(low)
    uniq: list[str] = []
    seen: set[str] = set()
    for t in out:
        if t in seen:
            continue
        seen.add(t)
        uniq.append(t)
        if len(uniq) >= limit:
            break
    return uniq


def entity_hit(title: str, entities: list[str]) -> bool:
    """题名是否按独立词命中任一声明实体。"""
    t = (title or "").lower()
    if not t or not entities:
        return False
    for e in entities:
        if not e:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(e) + r"(?![a-z0-9])", t):
            return True
    return False


def _rank_score(n: int, pool_size: int) -> float:
    """证据池位次分：绝对档位 0.6 + 相对位次 0.4。"""
    n = max(1, int(n or 1))
    abs_s = _RANK_FLOOR
    for upto, s in _RANK_STEPS:
        if n <= upto:
            abs_s = s
            break
    size = max(2, int(pool_size or 2))
    rel_s = 1.0 - (n - 1) / float(size - 1)
    rel_s = min(1.0, max(0.0, rel_s))
    return round(0.6 * abs_s + 0.4 * rel_s, 4)


def _recency_score(year) -> float:
    y = str(year or "").strip()
    m = re.search(r"(19|20)\d{2}", y)
    if not m:
        return 0.2
    import datetime

    cur = datetime.date.today().year
    age = cur - int(m.group(0))
    if age <= 5:
        return 1.0
    if age <= 10:
        return 0.8
    if age <= 20:
        return 0.5
    return 0.3


def _meta_score(year, tag: str, journal: str, doi: str) -> float:
    """DOI 0.4 + PMID 0.4 + 真实刊名 0.2。"""
    d = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", (doi or "").strip()).strip(" .;")
    s = 0.4 if re.match(r"^10\.\d{4,}/\S+$", d) else 0.0
    if re.search(r"(?i)\bPMID\s*:?\s*(\d{5,9})", tag or ""):
        s += 0.4
    j = re.sub(r"\s+", " ", (journal or "").strip()).strip(" .")
    if j and j.lower() not in ("unlisted journal", "unlisted"):
        s += 0.2
    return round(min(1.0, s), 4)


# 对数影响因子：复用检索层口径（rag/journal_if.py），并做本地缓存（打分会对同一刊反复查）
_IF_CACHE: dict[str, float] = {}
_IF_LOG_CACHE: dict[str, float] = {}


def if_log_of(journal: str) -> float:
    """期刊的**对数影响因子原值** `ln(1 + IF)`（不是归一化值，也不是档位分）。

    · 有实测 IF → 直接取对数（Nature 50.5 → 3.94；eLife 6.4 → 1.95；PLOS ONE 2.9 → 1.36）
    · 无 IF / 查不到 → **0**（不加成也不扣分）
    为什么取对数：IF 的原始跨度是 30 倍以上，对数把它压到 4 倍，
    使它能在「同等相关」时影响排序，而不会主导排序。
    """
    key = (journal or "").strip().lower()
    if key in _IF_LOG_CACHE:
        return _IF_LOG_CACHE[key]
    out = 0.0
    try:
        try:
            from rag.journal_if import lookup_if
        except ImportError:
            from journal_if import lookup_if  # type: ignore
        if key:
            info = lookup_if(journal)
            v = getattr(info, "if_value", None)
            if v is not None and float(v) > 0:
                out = math.log1p(float(v))
    except Exception:  # noqa: BLE001
        out = 0.0
    if len(_IF_LOG_CACHE) > 4000:
        _IF_LOG_CACHE.clear()
    _IF_LOG_CACHE[key] = out
    return out


def if_score_of(journal: str) -> float:
    """归一化对数影响因子（0~1，未知刊 0.45）—— 仅供与检索层 blend 对抛/展示。

    `log1p(IF) / log1p(40)`，取值范围 0~1；**未知刊/无刊名 = 0.45**（不惩罚，
    与本库实际情况匹配：多数证据来自 Development / eLife / 斑马鱼专刊）。
    导入失败或查表异常时也退回 0.45（恒定中性，不影响相对顺序）。

    RRS 的排序加成用的是 `if_log_of()`（对数原值 `ln(1+IF)`），不是本函数的归一化值。
    """
    key = (journal or "").strip().lower()
    if key in _IF_CACHE:
        return _IF_CACHE[key]
    score = 0.45
    try:
        try:
            from rag.journal_if import UNKNOWN_SCORE, lookup_if
        except ImportError:
            from journal_if import UNKNOWN_SCORE, lookup_if  # type: ignore
        score = float(UNKNOWN_SCORE)
        if key:
            info = lookup_if(journal)
            if info is not None:
                score = float(getattr(info, "if_score", score))
    except Exception:  # noqa: BLE001
        score = 0.45
    if len(_IF_CACHE) > 4000:
        _IF_CACHE.clear()
    _IF_CACHE[key] = score
    return score


def score_paper(
    paper,
    *,
    question: str = "",
    entities: list[str] | None = None,
    cited_ns: set[int] | None = None,
    pool_size: int = 0,
    rel: float | None = None,
) -> dict:
    """给单条文献打分。

    `paper` 为 `(n, year, tag, title, zfqa, journal, doi)`（与 `_paper_view` 同序，
    多余元素忽略）。

    `rel`：上游**连续语义相关度** `rel_n`（0~1，来自证据池编号行的 `rel=`）。
    传 None = 拿不到分数 → `rel` 维权重（0.10）自动退给 `rank`，总分尺度不变。
    """
    p = tuple(paper) + ("",) * max(0, 7 - len(paper))
    n = int(p[0] or 0)
    year, tag, title, _zfqa, journal, doi = p[1], p[2], p[3], p[4], p[5], p[6]
    ents = extract_entities(question) if entities is None else entities
    cited = cited_ns or set()
    has_entity = bool(ents)

    dims = {
        "entity": 1.0 if (has_entity and entity_hit(str(title), ents)) else 0.0,
        "rank": _rank_score(n, pool_size or n),
        "rel": 0.0 if rel is None else max(0.0, min(1.0, float(rel))),
        "cite": 1.0 if n in cited else 0.0,
        "recency": _recency_score(year),
        "meta": _meta_score(year, str(tag), str(journal), str(doi)),
        "if": if_log_of(str(journal)),          # 对数影响因子原值 ln(1+IF)
    }
    weights = dict(WEIGHTS)
    rel_available = rel is not None
    if not rel_available:
        # 拿不到上游相关度分数：把这部分权重退给位次维，总分尺度与改动前一致
        fb = weights.pop("rel")
        weights[REL_WEIGHT_FALLBACK_TO] = weights.get(REL_WEIGHT_FALLBACK_TO, 0.0) + fb
        dims.pop("rel")
    if not has_entity:
        # 无实体问句：entity 维不参与，权重按比例分摊
        drop = weights.pop("entity")
        total = sum(weights.values()) or 1.0
        weights = {k: v + drop * (v / total) for k, v in weights.items()}

    base = sum(weights[k] * dims[k] for k in weights)
    # 对数影响因子：ln(1+IF) 乘一个缩放系数，**加**在五维加权分之上（不是一个权重维）
    k_if = if_k()
    bonus_if = round(k_if * dims["if"], 6)
    score = round(base + bonus_if, 6)
    blocked = False
    if has_entity and hard_entity_enabled() and dims["entity"] == 0.0 and not dims["cite"]:
        blocked = True  # 点名了实体、题名里没有、正文也没引用 → 无可辩驳证据
    return {
        "n": n,
        "score": score,
        "dims": dims,
        "base_score": round(base, 6),
        "if_bonus": bonus_if,
        "if_k": k_if,
        "rel_available": rel_available,
        "weights_used": weights,
        "has_entity_question": has_entity,
        "blocked_by_entity": blocked,
    }


def select_relevant(
    papers: list,
    question: str = "",
    cited_ns=None,
    *,
    rel_map: dict | None = None,
    min_score: float | None = None,
    max_keep: int = 0,
    min_keep: int = 0,
    debug: bool | None = None,
) -> tuple[list, list]:
    """RRS = 在**原有检索链之上**的「加权精排层」，不是另起一套筛选。

    原机制（保持不变）：
        FAISS 粗排 500 → CrossEncoder 精排 100 → 取若干条 → 文末 20~25 条
    本层只做两件事（都在精排 100 之后）：
      1) **重排**：按 RRS 五维加权分从高到低排（实体 0.30 / 池内位次 0.25 /
         正文引用 0.20 / 时效 0.15 / 元数据 0.10）。返回顺序 = 加权序，
         下游「取到 20~25 条」拿到的就是加权后最靠前的那些。
      2) **一条硬排除**：问句点名了实体（基因符号/ID），而该条题名不含该实体、
         正文也没引用它 → 剔除（这就是「问 nanog 却挂纳米颗粒论文」的治法）。
         被硬排除的**永不在下游补足回来**。

    参数：
      · `max_keep`：只保留加权序前 N（传 `_ref_footer_max(question)`，即 25 / 事实题 12）。
      · `min_keep`：保留以兼容旧调用（=不足下限时的补足语义），现在**已不需要**：
        因为不再按门槛切池，池子只要够，下游自然会补到 20~25 条。
      · `min_score`：显式门槛（默认 None = 不做门槛淘汰），仅为调试/回归保留。

    返回 `(kept, dropped)`；kept 为**加权序**（不是原池序）。
    """
    papers = list(papers or [])
    if not papers:
        return [], []
    ents = extract_entities(question)
    cited = set(int(x) for x in (cited_ns or []))
    size = len(papers)
    gate = float(min_score) if min_score is not None else None

    scored: list[tuple[float, bool, object]] = []   # (score, blocked, paper)
    for p in papers:
        info = score_paper(
            p,
            entities=ents,
            cited_ns=cited,
            pool_size=size,
            rel=(rel_map or {}).get(int(p[0])),
        )
        scored.append((float(info["score"]), bool(info["blocked_by_entity"]), p))

    eligible = [(s, p) for s, blocked, p in scored if not blocked]
    if gate is not None:
        eligible = [(s, p) for s, p in eligible if s >= gate]
    # 加权精排（稳定排序：同分保持原精排序）
    eligible.sort(key=lambda t: -t[0])
    kept = [p for _s, p in eligible]
    if max_keep and len(kept) > int(max_keep):
        kept = kept[: int(max_keep)]
    if min_keep and len(kept) < len(eligible):
        kept = [p for _s, p in eligible]

    kept_ids = {id(p) for p in kept}
    dropped = [p for p in papers if id(p) not in kept_ids]

    if debug is None:
        debug = _bool_env("REF_RRS_DEBUG", False)
    if debug:
        try:
            from common_utils import logger

            kept_now = {id(p) for p in kept}
            for s, blocked, p in scored:
                logger.info(
                    "[RRS] n=%s score=%.3f blocked=%s keep=%s title=%s",
                    p[0], s, blocked, id(p) in kept_now, str(p[3])[:70],
                )
        except Exception:  # noqa: BLE001
            pass
    return kept, dropped
