# -*- coding: utf-8 -*-
"""Chat 语义理解层（2026-11-06）。

解决的问题（来自用户实测截图）：
  ·「现在你数据库有多少基因」→ 以前落到通用大模型，答成"人类/小鼠/斑马鱼各有多少基因"
    的科普长文，还被截断，既不完整也不回答本平台；
  ·「tp53 的坐标及所有别名」→ 以前只命中一个意图，给坐标就丢别名，给别名就丢坐标；
  · 换个说法（"帮我看看""还有啥叫法""库里有多少条"）就识别不出来，像"听不懂人话"。

这一层只做**理解与拆解**，不查库、不调模型：
  · normalize()        统一全半角、大小写、口语前缀，让同一意图的不同说法收敛；
  · platform_stats_spec 识别"本平台数据规模"类问题（直接查库计数，秒回、不编数字）；
  · task_specs()       把一句话里的多个诉求拆成多个子任务，交给工具栏底层逐项执行；
  · merge_parts()      把多个子任务的真实结果拼成一段完整回答，缺哪项就明说哪项。

设计约束：只在意图明确时短路；机制/原理/综述类问句一律放行给文献链路。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- 归一化

# 口语前缀：去掉后不改变语义，但能让关键词匹配稳定命中
_POLITE = re.compile(
    r"^(请问|麻烦|劳驾|打扰一下|不好意思|你好|hi|hello|please|could you|can you|would you|"
    r"我想(知道|问一下|了解)|想问(一下|下)|请教|帮我|帮忙|给(我|俺)|来(一)?(份|个)|"
    r"能不能|可以|能否|可不可以)[，,、\s]*",
    re.I,
)
_FILLER = re.compile(r"(一下|一下子|看看|查查|查一查|找找|给我|帮我|顺便|麻烦|呗|呢|吧|啊|嘛)")

# 同义改写：把口语说法映射成标准词，仅用于匹配，不改写传给工具的原文。
# 顺序有讲究：先吃掉「在哪儿表达」这类复合短语，再处理单独的「在哪儿」，
# 否则「mstn 都在哪儿表达呀」会被「在哪儿 → 坐标」抢走，答成染色体坐标。
_SYNONYMS: List[tuple] = [
    # ① 表达类口语（必须排最前）
    (r"(在哪儿|在哪里|在哪|哪儿|哪里|啥地方|什么地方|哪些地方|在哪些组织|哪些组织|什么组织)"
     r"[^，,。;；]{0,6}表达", "组织表达"),
    (r"表达(呀|吗|呢|情况|分布|位置|水平|高不高|多不多)", "表达谱"),
    # ② 别名类口语：「叫过啥名字」「都叫过啥」「还有啥叫法」
    (r"(叫过啥|叫过什么|叫过哪些|叫什么名字|叫啥名字|都叫啥|都叫什么|都叫过啥|"
     r"还有啥叫法|还有啥名字|还有哪些名字|有啥别名|别的名字|其他名字|"
     r"以前叫啥|以前叫什么|旧名字|老名字|所有叫法)", "别名"),
    (r"还有(什么|啥|哪些)?(叫法|名字|名称|别名)", "别名"),
    (r"(其他|其它|别的)(叫法|名字|名称)", "别名"),
    (r"又(叫|称|名)", "别名"),
    (r"也(叫|称)", "别名"),
    (r"(同义|旧|曾(用|经叫)|历史)(名|名称|符号)", "别名"),
    # ③ 数量 / 本库 / 位置
    (r"(一共|总共|总计|合计|全部)?(有)?(多少|几个|几条|几篇|多少条|多少个|数量|总数|总量|规模)", "多少"),
    (r"(库里|库中|数据库里|本库|收录了|收录的|本地库|平台里|你(们)?(这|的)?(库|数据库|平台))", "本库"),
    (r"(在哪儿|在哪里|位于哪|定位在哪|定位到哪|落在哪|在哪)", "坐标"),
    (r"(基因组位置|染色体位置|物理位置)", "坐标"),
    (r"(表达情况|表达数据|表达情况如何)", "表达谱"),
    (r"(有哪些组织|什么组织|在哪种组织)", "在哪些组织"),
    (r"(换(成|到)|映射(到|成)|转(成|到)|lift\s*over)", "换算"),
    (r"(序列信息|碱基序列)", "序列"),
]


def normalize(question: str) -> str:
    """NFKC 全半角统一 + 去口语前缀 + 同义归一（只用于匹配）。"""
    q = unicodedata.normalize("NFKC", (question or "").strip())
    low = q.lower()
    # 中文标点统一成半角，方便 "、" 一类分隔
    low = low.translate(str.maketrans("，：；？！（）【】、", ",:;?!()[]/"))
    for _ in range(3):
        new = _POLITE.sub("", low).strip()
        if new == low:
            break
        low = new
    low = _FILLER.sub("", low)
    # 前缀可能嵌套（"我想知道帮我看看…"），多迭代几轮直到稳定，避免残留口语前缀
    # 影响关键词匹配（原 3 轮在长前缀下会残留，故放宽到 5 轮）。
    for _ in range(5):
        new = _POLITE.sub("", low).strip()
        if new == low:
            break
        low = new
    for pat, rep in _SYNONYMS:
        low = re.sub(pat, rep, low)
    return re.sub(r"\s+", " ", low).strip()


def lang_of(question: str) -> str:
    """按提问语言推断 zh/en（仅在未拿到界面语言时作为兜底）。

    修复：像「tp53」「mstn」这种**只含一个拉丁词**的提问，是基因符号而不是英文问句，
    以前会被判成 en，导致中文界面下工具回答出现英文标题 + 中文正文的中英混排。
    """
    q = (question or "").strip()
    if re.search(r"[\u4e00-\u9fff]", q):
        return "zh"
    if not re.search(r"[A-Za-z]{3,}", q):
        return "zh"
    if not re.search(r"\s", q):
        return "zh"  # 单个拉丁词 = 基因符号
    return "en"


# ---------------------------------------------------------------- 平台数据规模

_STAT_TOPICS = (
    ("genes", ("基因", "gene", "基因数", "基因条目")),
    ("papers", ("文献", "论文", "paper", "文章", "文献数")),
    ("aliases", ("别名", "同义名", "alias")),
    ("expression", ("表达", "表达记录", "expression")),
    ("go", ("go", "go 注释", "功能注释")),
    ("kegg", ("kegg", "通路", "pathway")),
    ("phenotype", ("表型", "phenotype")),
)
_STAT_WORDS = ("多少", "数量", "总数", "总量", "规模", "几条", "几篇", "统计", "count", "how many", "size of")
_STAT_SCOPE = ("本库", "数据库", "本地库", "库", "平台", "收录", "zebrafish agent", "你", "your", "this")
# 泛问"数据规模/数据量/收录量"即使不带"库"字也应当认定为统计题（用户常说"数据规模多大"）
_STAT_GENERIC = ("数据规模", "数据量", "收录量", "数据总量", "整体规模", "整体数据", "全部数据")


def platform_stats_spec(question: str) -> Optional[dict]:
    """「本平台/你数据库里有多少基因」这类问题 → 直接查库计数，不进大模型。

    修复：① 泛问"数据规模/数据量"不带"库"字也能命中；② 未点名具体维度
    （基因/文献/…）的泛问默认给出全量概览，不再因 topics 为空而落大模型编数字。
    """
    q = normalize(question)
    if not q or len(q) > 60:
        return None
    # 关键词要在「归一化后的文本 + 原文」两份上匹配：
    # normalize() 会把「多少/几个/规模」统一替换成「多少」，
    # 于是「数据规模多大」这类泛问在归一化后丢掉了「数据规模」这个关键线索，
    # 之前会被判成非统计题、落回大模型编数字。
    hay = q + " " + (question or "").lower()
    # 明确在讨论某个具体基因 / 区间 / 序列时不是统计题
    if re.search(r"chr\s*\d+\s*:|\d{6,}\s*[-–]\s*\d{6,}|[ATCGN]{20,}|ZDB-GENE-|ENSDARG\d+|NM_\d+", hay, re.I):
        return None
    # 数量词或泛问词（"数据规模/数据量"不含"多少"等数量词，需单独放行）任一命中即视为统计意图
    if not (any(w in hay for w in _STAT_WORDS) or any(w in hay for w in _STAT_GENERIC)):
        return None
    has_scope = any(w in hay for w in _STAT_SCOPE)
    has_generic = any(w in hay for w in _STAT_GENERIC)
    if not (has_scope or has_generic):
        return None
    topics = [name for name, kws in _STAT_TOPICS if any(k in hay for k in kws)]
    # 泛问"数据量/规模/总共"未点名具体维度 → 默认给全量概览，避免落大模型编数字
    if not topics and (has_scope or has_generic):
        topics = [name for name, _ in _STAT_TOPICS]
    if not topics:
        return None
    return {"tool": "platform_stats", "topics": topics, "q": question}


# ---------------------------------------------------------------- 复合任务拆解

_MECHANISM = ("原理", "机制", "为什么", "怎么工作", "综述", "研究进展", "如何调控",
              "protocol", "review", "how does", "why ", "what is the mechanism")

# 每个方面：tool → 触发词。命中几个方面就拆成几个子任务。
_ASPECTS: List[tuple] = [
    ("gene_locus", ("坐标", "位置", "哪个染色体", "哪条染色体", "染色体上", "基因定位",
                    "基因组位置", "locus", "coordinate", "genomic position", "located on", "position of")),
    ("id_resolve", ("别名", "同义名", "旧名", "其他名称", "还有哪些叫法", "所有叫法", "官方符号",
                    "gene name", "symbol", "synonym", "alias", "id 转换", "对应哪个基因", "所有别名")),
    ("expression", ("表达", "表达谱", "表达量", "在哪些组织", "组织表达", "表达数据", "tpm", "fpkm",
                    "bgee", "胚胎分期", "expression", "expression profile", "expressed in", "expression level")),
    ("sequence", ("序列", "cdna", "启动子", "promoter", "mrna 序列", "基因序列", "转录本序列",
                  "fasta", "coding sequence", "sequence", "transcript")),
    ("go", ("go富集", "go 富集", "富集分析", "go terms", "go term", "enrichment", "过度表征", "ora")),
    ("kegg", ("kegg", "通路", "pathway")),
    ("sgrna", ("sgrna", "grna", "guide rna", "向导", "靶点", "靶位点")),
    ("primer", ("引物", "primer")),
    ("phenotype_gene", ("表型", "phenotype", "突变体")),
    ("annotate", ("一键注释", "综合注释", "全面注释", "注释汇总", "注释", "功能", "作用",
                  "所有信息", "全部信息", "详细信息", "综合信息", "annotate")),
]
# 这些词出现在"要结果"的语境里才算动作，避免把"sgRNA 的原理"抢走
_ACTION = ("帮我", "给我", "请", "查", "找", "列出", "设计", "生成", "提取", "取", "算", "换算",
           "转换", "分析", "跑", "做", "看一下", "有哪些", "是什么", "多少", "及", "和", "以及",
           "还有", "show", "give", "list", "find", "design", "convert", "extract", "run", "and")


def _has(q: str, *words: str) -> bool:
    return any(w in q for w in words)


# 定义 / 介绍类问句：用户是在「问 X 是什么、怎么用」，而不是真要设计 / 做富集。
# 这类必须放行给大模型，否则「sgRNA 是什么」会被误当成设计任务。
# 中英文都要覆盖：否则 "what is sgRNA" 仍会被误命中工具、回"请提供基因名"。
_DEF_WORDS = (
    "是什么", "怎么用", "怎么设计", "如何设计", "原理", "介绍", "讲讲",
    "是什么东西", "是什么呀", "是什么吗", "干嘛的", "什么是",
    "what is", "what are", "what's", "how to", "how do",
    "define", "explain", "describe", "tell me about",
)


def _is_definition(n: str) -> bool:
    return _has(n, *_DEF_WORDS)


# 中文紧挨基因符号时 \b 不成立（汉字在 Unicode 里算 word char），
# 例如「tp53的坐标」必须用前后向断言来切。
_LATIN_TOKEN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9]{1,11})(?![A-Za-z0-9])")
_STOP_TOKEN = {
    "grcz", "grcz10", "grcz11", "grcz12", "grcz12tu", "grcz12ab", "grcz13ab",
    "zv7", "zv8", "zv9", "chr", "blast", "kegg", "go", "sgrna", "grna", "crispr",
    "primer", "pcr", "fasta", "cdna", "mrna", "liftover", "gene", "genes",
    "sequence", "expression", "phenotype", "assembly", "refseq", "ensembl",
    "ncbi", "zfin", "tpm", "fpkm", "bgee", "atlas", "tissue", "tissues",
    "and", "the", "what", "which", "show", "give", "list", "find", "design",
    "convert", "extract", "help", "how", "many", "your", "this", "that", "with",
    # 常见物种/通用词，避免被 robust_gene 当成基因符号（如 "human tp53" 抽成 human）
    "human", "mouse", "rat", "zebrafish", "danio", "fish", "model", "species",
    "cell", "cells", "organism", "dna", "rna", "protein", "proteins",
    "data", "study", "studies", "result", "results", "method", "methods",
    "analysis", "review", "paper", "article", "research", "sample", "samples",
    # 2026-11-09：英文代词不是基因符号。生产库 gene_alias 有一条 alias='its' 指向
    # sycp2，导致「What about its expression in adult tissues?」被当成 sycp2（q10 实测）。
    # 加入停用词后，该句会走 _gene_from_history() 从上一轮取回 myl7。
    "its", "it", "they", "them", "their", "theirs", "his", "her", "hers",
    "he", "she", "these", "those", "about", "adult", "adults", "same", "other",
    "another",
}


def _looks_like_gene(token: str):
    """候选词是否真能解析到基因。三态：True / False / None(=无法判定, 数据库不可用)。"""
    try:
        from gene_toolkit import _resolves_to_gene

        return _resolves_to_gene(token)
    except Exception:
        return None


def robust_gene(text: str) -> Optional[str]:
    """从「tp53的坐标」这类紧贴中文的写法里取出基因符号。

    **候选必须真的能解析到基因**。旧实现是"返回第一个不在停用词表里的英文单词"，
    于是：
      · "Can coordinates from GRCz11 and GRCz12tu be mixed?"  → 取出 "Can"
      · "Which zebrafish reference genome assembly is used ..." → 取出 "reference"
      · "Roughly how many protein-coding genes ..."            → 取出 "Roughly"
    这些词被当成基因一路带下去，最终答成 "No coordinate record for Can in the
    local library"，整题报废。英文停用词是举不完的，所以改为逐个查库裁决。
    """
    m = re.search(r"(ZDB-GENE-\d+-\d+|ENSDARG\d+(?:\.\d+)?|(?:NM|NR|NP|XM|XR|NC|NG)_\d+)", text or "", re.I)
    if m:
        return m.group(1)

    cands = []
    unknown = 0
    for m in _LATIN_TOKEN.finditer(text or ""):
        tok = m.group(1)
        if tok.lower() in _STOP_TOKEN or len(tok) < 2:
            continue
        cands.append(tok)
        verdict = _looks_like_gene(tok)
        if verdict is None:
            unknown += 1
            continue
        if verdict:
            return tok
    if cands and unknown == len(cands):
        # 数据库不可用，全部无法判定 —— 退回旧行为，避免整站认不出基因
        return cands[0]
    return None


def _gene_from_history(question: str, history: Optional[List[Any]]) -> Optional[str]:
    """「它有哪些别名」「这个基因在哪些组织表达」→ 从上一轮补出基因名。"""
    q = normalize(question)
    if not re.search(r"(它|这个基因|该基因|这个|此基因|that gene|this gene|its)", q):
        return None
    for item in reversed(history or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").lower()
        if role not in ("user", "human"):
            continue
        text = str(item.get("content") or item.get("text") or "")
        g = robust_gene(text)
        if g:
            return g
    return None


def task_specs(question: str, history: Optional[List[Any]] = None) -> List[dict]:
    """把问句拆成 1~N 个可执行的子任务；返回空列表表示交回原链路。"""
    # 延迟导入，避免与 chat_tool_dispatch 形成模块级循环引用
    from chat_tool_dispatch import (
        _gene, _genes, _parse_span, _parse_builds, _dna, _ID_TOKEN, _action,
    )

    q = (question or "").strip()
    if not q or len(q) > 400:
        return []
    n = normalize(q)

    stats = platform_stats_spec(q)
    if stats:
        return [stats]

    span = _parse_span(q)
    builds = _parse_builds(q)
    dna = _dna(q)
    gene = _gene(q) or robust_gene(q) or _gene_from_history(q, history)
    gs = _genes(q)
    if gene and gene not in gs:
        gs = [gene] + gs

    # 机制/原理题：没有给出区间或序列时一律放行
    if _has(n, *_MECHANISM) and not (span or dna):
        return []

    specs: List[dict] = []
    add = lambda tool, **kw: specs.append({"tool": tool, **kw})

    # ---- 区间 / 序列类（优先，避免被"基因序列"误判） ----
    if span and _has(n, "换算", "转换", "转到", "lift", "liftover", "对应到", "映射到", "convert"):
        add("liftover", span=span, builds=builds)
    elif span and _has(n, "序列", "碱基", "fasta", "sequence") and not _has(n, "哪些基因", "什么基因"):
        add("sequence_region", span=span, builds=builds)
    elif span and _has(n, "基因", "gene") and _has(n, "哪些", "什么", "列出", "里面", "落在", "区间内", "重叠", "list"):
        add("genes_region", span=span, builds=builds)
    elif dna and (_has(n, "blast", "比对", "同源", "align", "match") or len(q.strip()) < 80):
        add("blast", dna=dna)

    # ---- 基因维度：一句话里可能同时要坐标 + 别名 + 表达 + 序列 ----
    if gene and not (span or dna):
        hit = [(tool, kws) for tool, kws in _ASPECTS if any(k in n for k in kws)]
        # 「表型 → 基因」是反向检索，单独处理
        if any(t == "phenotype_gene" for t, _ in hit) and _has(n, "哪些基因", "什么基因", "找基因", "表型查基因", "which genes", "genes with"):
            hit = [(t, k) for t, k in hit if t != "phenotype_gene"]
            add("phenotype_term", keyword=q)
        for tool, _ in hit:
            if tool == "gene_locus":
                add("gene_locus", gene=gene)
            elif tool == "id_resolve":
                add("id_resolve", gene=gene)
            elif tool == "expression":
                add("expression", gene=gene)
            elif tool == "sequence":
                add("sequence", gene=gene, q=q)
            elif tool == "phenotype_gene":
                add("phenotype_gene", gene=gene)
            elif tool in ("sgrna", "primer", "go", "kegg"):
                # 命名了基因且关键词命中：默认直接服务该工具（设计 / 富集），
                # 仅当问句是「是什么 / 原理 / 介绍」等定义类时才放行给大模型，
                # 否则「tp53 的引物」「tp53 的 sgRNA」会被动作词门槛整条丢弃，回答不全。
                if len(hit) == 1 and not _action(q) and _is_definition(n):
                    continue
                if tool == "sgrna":
                    add("sgrna", gene=gene, dna=dna)
                elif tool == "primer":
                    add("primer", gene=gene, dna=dna)
                elif tool == "go":
                    add("go", genes=gs or ([gene] if gene else []))
                else:
                    add("kegg", genes=gs or ([gene] if gene else []), keyword=q)
            elif tool == "annotate":
                add("annotate", gene=gene)

        # 一句话里「坐标 / 别名 + ID」：用户显式要 ID 时，必须也给出跨库标识符
        # （NCBI Gene / Ensembl / ZFIN 等），否则只回坐标会显得「回答不全面」。
        # 注意：Python3 的 \w 把汉字也算作单词字符，\bid\b 在「及id」这种中文后接拉丁词时
        # 左边界不成立；改用 ASCII 字母边界，既能在中文后接 id 时命中，又能挡掉 guide/middle 等。
        _wants_id = bool(
            re.search(r"(?<![a-zA-Z])id(?![a-zA-Z])", n)
            or _has(n, "编号", "基因号", "ncbi gene id", "gene id", "ensembl 编号", "数据库编号")
        )
        if _wants_id and gene and not any(s["tool"] == "id_resolve" for s in specs):
            add("id_resolve", gene=gene)

        # 跨库 ID 直接问「对应哪个基因 / 别名」也要给别名
        tok = _ID_TOKEN.search(q)
        if tok and not any(s["tool"] == "id_resolve" for s in specs):
            if _has(n, "别名", "对应", "转换", "id", "是什么", "哪个基因", "官方符号", "gene name"):
                add("id_resolve", gene=tok.group(0))

    # 兜底：纯「这是哪个基因」型提问（如 Which gene is ZDB-GENE-…?）若上面的分支都没命中，
    # 仍走确定性 ID 反查。否则会落到通用聊天，由大模型自由发挥、答成一段 "[Error] …" 话术，
    # 既不像回答，也让同一类题因句式不同而走不同链路（同一 ID 换种问法结果不一致）。
    if not specs and not span and not dna:
        _tok_fallback = _ID_TOKEN.search(q)
        if _tok_fallback and _has(n, "哪个基因", "什么基因", "对应", "别名", "官方符号",
                                  "which gene", "what gene", "corresponds", "refers to",
                                  "stands for", "gene symbol"):
            add("id_resolve", gene=_tok_fallback.group(0))

    if not specs:
        return []
    # 去重（同一 tool 只保留一次），并限制总数，避免一句话触发一长串调用
    seen, out = set(), []
    for s in specs:
        key = (s["tool"], str(s.get("gene") or s.get("keyword") or s.get("span") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out[:8]


# ---------------------------------------------------------------- 答案拼装

_SECTION = {
    "gene_locus": "坐标",
    "id_resolve": "别名与 ID",
    "expression": "表达",
    "sequence": "序列",
    "sequence_region": "区间序列",
    "genes_region": "区间内基因",
    "liftover": "坐标换算",
    "blast": "序列比对",
    "go": "GO 富集",
    "kegg": "KEGG 通路",
    "sgrna": "sgRNA 设计",
    "primer": "引物设计",
    "phenotype_gene": "表型",
    "phenotype_term": "表型查基因",
    "annotate": "基因注释",
    "platform_stats": "数据规模",
}


_SECTION_EN = {
    "gene_locus": "Coordinates",
    "id_resolve": "Aliases and IDs",
    "expression": "Expression",
    "sequence": "Sequence",
    "sequence_region": "Region sequence",
    "genes_region": "Genes in region",
    "liftover": "Coordinate conversion",
    "blast": "Sequence alignment",
    "go": "GO enrichment",
    "kegg": "KEGG pathways",
    "sgrna": "sgRNA design",
    "primer": "Primer design",
    "phenotype_gene": "Phenotypes",
    "phenotype_term": "Genes by phenotype",
    "annotate": "Gene annotation",
    "platform_stats": "Data scale",
}


# ID 映射块开头的直答句（"In short: tp53 — …" / "简答：…"），拼多工具答案时提到最前
_LEAD_LINE_RE = re.compile(r"(?m)^(?:In short:[^\n]*|简答：[^\n]*)\n*")


def merge_parts(parts: List[dict], lang: str = "zh") -> str:
    """把多个子任务的真实结果拼成完整回答；单项时不加多余标题。"""
    good = [p for p in parts if p and (p.get("answer") or "").strip()]
    if not good:
        return ""
    if len(good) == 1:
        return good[0]["answer"].strip()
    blocks = []
    for p in good:
        tool = p.get("tool") or ""
        label = _SECTION.get(tool, "结果")
        title = label if lang == "zh" else _SECTION_EN.get(tool, "Result")
        body = p["answer"].strip()
        # 执行器自带小节标题（中文【…】或英文[…]）时不再重复包一层
        if (body.startswith("【") and "】" in body[:14]) or re.match(r"^\[[^\]\n]{1,32}\]", body):
            blocks.append(body)
        else:
            blocks.append("【%s】\n%s" % (title, body) if lang == "zh" else "**[%s]**\n%s" % (title, body))
    # 直答句置顶：ID 映射块自带「In short: …/简答：…」一句，而多工具题（如
    # 「给 tp53 的 ZFIN/NCBI/Ensembl 与 GRCz12tu 坐标」）里坐标块排在前面，
    # 于是读者先看到一屏 [Coordinates]，第二块才冒出直答句。提到最前面。
    lead = ""
    for i, b in enumerate(blocks):
        m = _LEAD_LINE_RE.search(b)
        if m and not lead:
            lead = m.group(0).strip()
            blocks[i] = (b[: m.start()] + b[m.end():]).strip()
            break
    if lead:
        blocks.insert(0, lead)
    return "\n\n".join(b for b in blocks if b)
