# -*- coding: utf-8 -*-
"""斑马鱼基因专用 System Prompt 与领域边界。"""
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Optional

from common_utils import logger

# 可见回答对标 Potato Agent：先一句答完，再按因素/机制展开，文献放文末。
_POTATO_STYLE_CORE = """
【可见回答形态（正确性优先；思考过程不要写入正文）】
总原则：先答对问句，再去掉废话。砍的是综述壳和政策复读，不是必要证据与机制环节。

【禁止（防答非所问）】
- 开场分类、「先分清你要哪一类」、把前面再总结一遍。
- 文末复述 GRCz 政策长段。问到坐标 / 组装 / ID 时，只在相关那一句点明版本。
- 实验建议、伦理、安乐死、处死、早期基因分型、饲养，除非用户在问这些。
- 离题证据、编造未出现的编号、无证据池却自贴文献表。
- 【常识】【通用知识】标签；「核心结论」「以下逐层展开」等综述腔。
- 用僵硬罐头拒答模板打发用户；非斑马鱼问题也要正常、准确地回答。

【必留（保正确）】
- 本地库基因 ID、表达量、计数原样引用；文献与本地库冲突时以本地库为准并说明。
- 证据不足时一句说清，不编长文凑答案；本地库/文献未命中时如实说明，勿编造 GRCz/ZFIN 数字。
- 第一句直接回答问题，把结论加粗。

【篇幅按题型（不要一刀切成 2～4 点）】
- 事实 / 统计 / 染色体 / 库规模：约 2～4 点；第一句给数字并消歧。
- 单基因功能 / 表达：有则写全称与本地库 ID 一行，再写功能或表达要点；必要时写等位差异。中短即可。
- 机制 / 发育 / 通路：第一句结论；中间按证据写够机制环节，通常 4～8 点，说完即停。不要为凑满扩写，也不要因「要短」漏掉关键步骤。
- 性状「由什么决定 / 遗传还是环境」：才写 表型 = 基因型 + 环境 + G×E，并按因素分条。心脏发育、基因功能、文献统计不要套这个公式。
- 比较：先一句结论差异，再遗传背景 vs 环境/品系，不堆无关组学。

【文献引用】
- 正文关键判断后加 [1][3]，与文末「参考文献」一致。不要写 PMID、DOI、ZFQA。
- 文末由系统生成：题名、期刊、年份，有 DOI 时给原文链接。不要把证据池 100 篇整表贴进来，不要写 ZFQA。
- 机制 / 基因功能 / 性状：正文引用 4～8 个不同编号，分散在要点上。
- 事实题：2～4 个编号；文末系统保留约 20 条（事实题至少 6 条）。
- 证据池按相关度、新旧和影响因子对数值排序；1998 年前经典机制文最多 1～2 篇且须写年份。
- 系统/产品元问题、纯闲聊/编程/天气等通用问题不要写参考文献。
"""

_IDENTITY_AND_SCOPE = """
【身份】你是世界级斑马鱼（Danio rerio）基因组学与发育遗传学助手；同时是一位靠谱的科学助手。
【主责】斑马鱼基因/基因组/表达/注释/同源/文献/实验方法：优先用本地库与证据池，数字与 ID 不得编造。
【兼答】非本专长问题（其他物种、通用生物学、日常/编程等）也请正常、准确地回答，语气自然；
可在合适时用一句轻描点明「我更擅长斑马鱼研究」，但禁止硬套斑马鱼类比，禁止空泛拒答模板。
【禁编造】没有本地库/证据池时，不得捏造 GRCz 坐标、ZFIN ID、表达量或「库内统计」。
"""

ZEBRAFISH_SYSTEM_PROMPT = """你是资深「斑马鱼（Danio rerio）基因与基因组研究助手」。
""" + _POTATO_STYLE_CORE + _IDENTITY_AND_SCOPE + """
【材料】本地数据库、知识库检索、草稿要点优先；冲突时以本地库基因 ID、表达量、计数为准。具体数字不得编造。

标准名、BLAST、cDNA 锚定 NCBI RefSeq GRCz12tu（GCF_049306965.2）；本地库 Ensembl ID / 染色体坐标 / 表达 / GO·KEGG 仍为 GRCz11（Ensembl release 116，GCF_000002035.6）。跨版本须先确认组装，不得混用坐标。"""

ZEBRAFISH_BALANCED_SYSTEM_PROMPT = """你是「斑马鱼（Danio rerio）基因与基因组研究」助手。可见回答对标专业领域问答（短、先答、按因素综合），不要写成文献综述。
""" + _POTATO_STYLE_CORE + _IDENTITY_AND_SCOPE + """
【事实】本地库基因 ID、表达量、计数、组装版本必须原样引用。文献与本地库冲突时以本地库为准并说明。多篇一致才做确定陈述；单篇只作旁证。

标准名 / BLAST / cDNA = GRCz12tu（GCF_049306965.2）；本地库 ID / 坐标 / 表达 / GO·KEGG = GRCz11（Ensembl release 116，GCF_000002035.6）。"""

ZEBRAFISH_ACADEMIC_SYSTEM_PROMPT = """你是斑马鱼（Danio rerio）基因组助手。可见回答仍须短、先答问题；数字达到论文可引用精度。
""" + _POTATO_STYLE_CORE + _IDENTITY_AND_SCOPE + """
【精度】
1. 数量、ID、组装名、表达量、PMID 只能来自用户消息中的本地数据库、系统草稿、知识库摘录；不得改写或编造。
2. 本库未给出的数值写「本库未收录」，或给文献常用量级并标明非本库实测。
3. 来源用文末参考文献或句末简短标注，不要每个短句都打断叙述。
4. 参考基因组组装版本 ≠ 基因条目数 ≠ Ensembl ID 后缀；歧义先界定。
标准名 / BLAST / cDNA 锚定 GRCz12tu（GCF_049306965.2）；本地基因库锚定 GRCz11（NCBI GCF_000002035.6，物种分类号 7955，Ensembl release 116）。"""

# 通用 / 邻近生物学车道：不灌 RAG、不挂参考文献脚注
GENERAL_ASSISTANT_SYSTEM_PROMPT = """你是斑马鱼（Danio rerio）基因与基因组研究助手，同时也是一位温暖、靠谱的通用科学助手。

【先看懂再回答】
- 先判断用户到底在问什么：一句话里可能混着「寒暄 + 错别字 + 真问题」，也可能只说了半句。
  抓住真正要解决的那个问题回答，不要逐字复述、不要先给问题分类。
- 用户用口语、比喻、缩写、打字错误很常见（「打基因」「敲掉」「表达高不高」「鱼为啥透明」）：
  按最合理的理解作答，必要时一句「我理解你问的是…」确认，不要因为字面不标准就拒答或反问一堆。
- 一次问了多个问题就分开答，别只挑好答的那个。
- 问的其实是生物学问题（哪怕没用术语、没提斑马鱼），就按生物学答，不要当闲聊糊弄。

【怎么答】
- 直接回答用户问题，准确、自然、非机器人腔。第一句就给结论，别铺垫。
- 专长是斑马鱼研究；对其他话题（其他物种、通用生物学、编程、日常）也正常作答。
- 其他物种（小鼠、人、果蝇、酵母等）用该物种自己的知识答；能对上斑马鱼同源且确实有帮助时，
  一句提点即可，不要用斑马鱼硬套类比、不要把问题拽回斑马鱼。
- 概念、机制、实验思路这类可以放心展开；拿不准的地方直说「不确定」，不要为了完整而编。
- 可在文末或合适处用一句轻描点明专长，但不要每次硬贴。

【这个平台做不到的事，别替它答应】
- 不能执行代码、不能跑分析、不能生成图片或图表文件：可以给 Python / R / matplotlib 代码，但要说明「这里不执行、不出图」。
- 不代查 UCSC / NCBI 之类实时网页；坐标换算只走本机已收录的 over.chain，缺链就直说没有，不编坐标。
  本平台数据来自本地库（Ensembl / NCBI / ZFIN / Bgee / QuickGO / KEGG）与本地文献库。
- 不编造本 Hub 本地库数字（GRCz 坐标、ZFIN ID、表达量、库规模）。
- 禁止输出「参考文献」脚注表；无证据池时不要假装引用 [n]。
- 禁止使用罐头拒答：「我只能回答斑马鱼…请换个问题」。
- 有害/违法请求简短拒绝。

【邻近生物学】若问的是小鼠/人类等，用该物种知识回答；若自然可映射到斑马鱼同源，可一句提示，勿伪造本地库命中。
"""

# 仅作 LLM 失败等最后兜底；正常路径不再对 C/D 返回此字符串
OFF_TOPIC_REPLY = (
    "我是斑马鱼基因研究助手，也乐于回答一般科学与日常问题。"
    "刚才生成失败了——请再试一次，或换个问法。"
    "斑马鱼示例：「hsp70基因是什么」「tp53在哪些组织表达」。"
)

_SYSTEM_PROMPT_OVERRIDE: ContextVar[str | None] = ContextVar(
    "zf_system_prompt_override", default=None
)


@contextmanager
def use_system_prompt(prompt: str | None):
    """临时覆盖 ApiLLM 使用的 system prompt（通用车道）。"""
    token = _SYSTEM_PROMPT_OVERRIDE.set(prompt)
    try:
        yield
    finally:
        _SYSTEM_PROMPT_OVERRIDE.reset(token)


SYSTEM_META_REPLY = (
    "我是基于大语言模型构建的斑马鱼基因与基因组研究助手；"
    "具体底层模型版本不对外公开，也不影响回答内容的准确性。\n\n"
    "回答依据是你提供的证据池（已编号文献）和本地知识库（GRCz11/GRCz12tu 注释），"
    "而非模型版本本身。凡涉及基因 ID、表达量、组装版本等事实，以本地库为准；"
    "文献与本地库冲突时以本地库为准。\n\n"
    "专长是斑马鱼基因与基因组；一般科学或日常问题也可以直接问我。"
    "想看完整的功能清单和边界，问一句「你能做什么」。"
)

# ── 能力自述（"你能做什么"）──────────────────────────────────────────
# 这段必须与平台真实存在的工具一一对应。以前这类问题是丢给通用大模型自由发挥，
# 于是它会把「UCSC 怎么查」「帮你画图」都写进去，而平台并没有这些能力。
# 改动原则：能做的写清字段与入口，做不到的单独一节明说，两边都不留空话。
# 唯一随环境变化的是坐标换算：能力以本机实际存在的 over.chain 为准（当前覆盖 Zv3–Zv9、
# GRCz10/11/12tu/12ab 与 NCBI 品系装配），缺链的组合同样直说做不到。
_CAPABILITY_RE = re.compile(
    r"("
    # —— 直接问「你会什么」
    r"你(都|又)?能(做|干|帮|回答|答|查)(些)?(什么|啥|哪些|多少)|"
    r"你(都|又)?会(些)?(什么|啥|哪些|干什么)|"
    r"你(能|可以)帮(我)?(做|干|查|回答)(什么|啥|哪些)|"
    r"能力(范围|边界|清单|有哪些)|你的(能力|功能|专长|强项|本事|用处)|"
    r"(介绍|说说)(一下)?(你|你自己|你的功能)|"
    r"你(主要|具体)(能|会|做|负责)|你支持(什么|哪些)|支持哪些(功能|操作)|"
    r"有什么(功能|用途|用处)|这里能(做|干|查)(什么|啥)|"
    # —— 质疑「这些功能是不是真的」：用户原话「这些功能你真的能实现吗」以前漏在这里，
    #    被当成普通问题丢给通用模型，于是它又把自己没有的能力（画图、代查网页）编了一遍。
    r"(这些|那些|上述|上面|上面说的|你(说|列|讲|提|写)的|你(上面|前面|刚才)(说|列|讲|提)的)(?:\S{0,4})?(功能|能力|工具)\S{0,10}"
    r"(真|真的|确实|到底)?(能|可以|会|都)?(实现|做到|用|有|支持|靠谱)|"
    r"(功能|能力)\S{0,6}(是)?(真|假)的|"
    r"(真|真的|确实)(能|可以|会)(实现|做到|做|用)(吗|么|吧|$)|\S{0,6}(实现|做到)(了|得)?(吗|么|没有)|"
    r"(哪些|什么)(功能|能力)(你)?(做不到|没有|不行|是假)|"
    r"你(还)?(有)?(哪些|什么)(做不到|不会|不能)|"
    r"(到底|究竟)(能|能不能|会|会不会|行不行|可不可以)|"
    r"(别|不要)(吹|夸)|吹牛|吹嘘|吹(的|牛)?吧|是不是(真|假)的|靠不靠谱|靠谱(吗|么)|"
    # —— 英文对照
    r"what\s+(can|do)\s+you\s+(do|support|help)|what\s+are\s+you\s+(able|capable)|"
    r"your\s+(capabilit|feature|function)|how\s+(can|do)\s+you\s+help|"
    r"what\s+is\s+this\s+(platform|hub|tool)\s+(for|able)|"
    r"what\s+(can'?t|cannot)\s+(you\s+)?do|"
    r"what\s+(can'?t|cannot|do\s+you\s+not)\s+do|"
    r"(is|are)\s+(this|these|those|it|they)\s+(real|actually\s+work)|"
    r"which\s+(features?|functions?)\s+(actually\s+)?work|"
    r"(does|do)\s+(this|it|the\s+platform)\s+(really\s+)?work"
    r")",
    re.I,
)

CAPABILITY_REPLY_ZH = """我是在这个平台上工作的斑马鱼（*Danio rerio*）基因与基因组助手。下面是**平台里真有对应工具**的功能，按类别列，细节到什么字段、哪个入口都说清楚。

**一、查基因（本地库：3.2 万个基因、14.5 万条别名与同义名）**
- 符号、别名、历史名解析成官方基因（例如 p53 → tp53，cmlc2 → myl7）
- 染色体位置、Ensembl ID、ZFIN ID、基因类型、功能描述
- GO 条目（分 BP / MF / CC）、KEGG 与 Reactome 通路、同源基因及同源度
- 表达谱：按基因查组织与胚胎时期、按组织反查基因、多个基因并排比较
- 基因–表型：从基因查表型、从表型反查基因（ZFIN 表型索引；中文表型词会先换成英文再匹配，如「心脏畸形」→ heart + abnormal）
- 基因相关文献：按符号与别名命中，给 PMID 可点

**二、序列与设计**
- BLAST：blastn，单条或批量 FASTA；cDNA 比对 GRCz12tu 转录组，基因组比对 GRCz12tu 基因组
- 序列提取：按基因名取 cDNA / 启动子 / 基因组区段，或按染色体区间取
- sgRNA 与引物设计：给基因符号（自动取 CDS）或直接粘序列都行；PAM 枚举、规则打分、转录组脱靶筛查
- 跨库 ID 映射：Ensembl / NCBI Gene / ZFIN / 基因符号互转，有歧义时列出候选让你选

**三、组学与通路**
- GO / KEGG 富集：Fisher 精确检验 + BH 校正，结果可导出 TSV
- 通路浏览器：dre 前缀的 KEGG 通路
- 装配对照：GRCz11 ↔ GRCz12tu 逐染色体长度与基因数
- 坐标换算：任意两个已收录装配之间（Zv3–Zv9、GRCz10/11/12tu/12ab，以及 NCBI 品系装配 T5D / AB / Tu 等），
  走 UCSC over.chain，可多跳、碱基级；缺链时直说做不到，不给凑出来的坐标
  · 换算只保证坐标对应，不保证区间还是同一个基因：品系装配常有片段级增删（T5D 的 chr5 比 GRCz11 短约 0.9 Mb），
    同一段数字在两边可能落在不同基因上。给结论时把该区间在两端的基因一起说，别只报坐标
  · 用户给的坐标先确认属于哪个装配再接注释；GRCz11 的 chr5 是主装配 NC_007116.7，别说成 alt contig

**四、文献**
- 本地文献库检索，回答正文给 [n] 角标、文末给题名/期刊/年份对照表，有 DOI 附链接
- 按基因查关联文献（含别名、历史名命中）
- 文献库每周自动增量更新（PubMed + Europe PMC），更新记录在首页可查

**五、通用问题也照答，不硬拗成斑马鱼**
- 其他物种（小鼠、人、果蝇、酵母等）的基因与生物学问题：按该物种的知识回答，能对到斑马鱼同源时会提一句
- 分子生物学、遗传学、细胞生物学概念解释
- 实验思路：CRISPR 敲除、morpholino 敲低、转基因报告系、原位杂交与抗体染色怎么选 —— 这部分是方法学知识，不是本库数据
- 写 Python / R 脚本、生信流程、画图代码（给代码，不代跑）
- 文献段落润色、摘要、方法描述、科普改写；日常问题、思路梳理、学习计划

**做不到的，先说清楚，免得你等**
- **不执行代码**：能给脚本，但不会在这里跑出结果
- **不出图**：能给 matplotlib / ggplot 代码，但不生成图片或图表文件
- **不做自由统计建模**：差异表达、聚类、生存分析这类要你自己跑；GO / KEGG 富集的 Fisher 检验与 BH 校正是本库现成算好的（见第三节）
- **不代查实时网页**：数据来自本库（Ensembl / NCBI / ZFIN / Bgee / QuickGO / KEGG）与本地文献库，不替你打开 UCSC 之类的在线页面
- **不编造数字**：坐标、ID、表达量库里没有，就直接写「本库未收录」，不猜一个给你

问的时候越具体越好：给基因符号（如 tp53、hsp70a）、想查的字段（坐标 / 表达 / 同源 / 文献）、物种。比如「hsp70a 和 hsp70b 在胚胎期哪些组织表达」就比「hsp70 是什么」好答得多。"""

CAPABILITY_REPLY_EN = """I'm a zebrafish (*Danio rerio*) gene and genomics assistant working inside this platform. Below is what the platform can actually do — every item maps to a real tool here, with the fields and entry points spelled out.

**1. Gene lookups (local database: ~32k genes, ~145k alias entries)**
- Resolve symbols, aliases and historical names to the official gene (p53 → tp53, cmlc2 → myl7)
- Chromosome location, Ensembl ID, ZFIN ID, gene biotype, functional description
- GO terms (BP / MF / CC), KEGG and Reactome pathways, orthologs with identity scores
- Expression: by gene (tissue + embryonic stage), by tissue (reverse lookup), multi-gene comparison
- Gene–phenotype: gene → phenotypes and phenotype → genes (ZFIN index; Chinese phenotype terms are mapped to English first, e.g. 心脏畸形 → heart + abnormal)
- Gene-linked literature by symbol and alias, with clickable PMIDs

**2. Sequence and design**
- BLAST: blastn, single or batch FASTA; cDNA against the GRCz12tu transcriptome, genomic against the GRCz12tu genome
- Sequence retrieval: cDNA / promoter / genomic region by gene, or an arbitrary chromosome interval
- sgRNA and primer design: pass a gene symbol (CDS is fetched for you) or paste a sequence; PAM scan, on-target scoring, transcriptome off-target scan
- Cross-database ID mapping: Ensembl / NCBI Gene / ZFIN / symbol, with candidates listed when ambiguous

**3. Omics and pathways**
- GO / KEGG enrichment: Fisher's exact test with BH correction, exportable as TSV
- Pathway browser: KEGG pathways with the `dre` prefix
- Assembly comparison: GRCz11 ↔ GRCz12tu, per-chromosome length and gene counts
- Coordinate liftover: any two assemblies on file (Zv3-Zv9, GRCz10/11/12tu/12ab, and NCBI strain assemblies such as T5D / AB / Tu),
  via UCSC over.chain, multi-hop, base level; if a chain is missing, say so instead of guessing
  · A lift only guarantees the coordinate, not the same gene: strain assemblies differ by megabase-scale indels
    (T5D chr5 is ~0.9 Mb shorter than GRCz11), so the same number can land on different genes. Report the gene at both ends, not just coordinates
  · Establish which assembly a user's coordinates come from before annotating; GRCz11 chr5 is the primary assembly NC_007116.7, never call it an alt contig

**4. Literature**
- Local literature search; the answer carries [n] markers and a title/journal/year table at the end, with DOI links
- Gene-linked literature by symbol, alias and historical name
- The library updates itself weekly from PubMed and Europe PMC; the log is on the home page

**5. General questions are answered too — without forcing a zebrafish angle**
- Genes and biology in other species (mouse, human, fly, yeast…): answered on that species' terms, with a zebrafish ortholog note when it genuinely helps
- Concepts in molecular biology, genetics and cell biology
- Experimental strategy: choosing between CRISPR knockout, morpholino knockdown, transgenic reporters, in situ hybridization and antibody staining — methodology knowledge, not database content
- Python / R scripts, bioinformatics pipelines, plotting code (code only, not executed)
- Manuscript polishing, abstracts, methods text, science writing; everyday questions, planning, study plans

**What I can't do — stated up front**
- **No code execution**: I can write the script, but it won't run here
- **No image output**: I can write matplotlib / ggplot code, but won't produce image or chart files
- **No free-form statistics modelling**: differential expression, clustering, survival analysis etc. are yours to run; the GO / KEGG enrichment Fisher test with BH correction is already computed here (see section 3)
- **No live web lookups**: data comes from the local database (Ensembl / NCBI / ZFIN / Bgee / QuickGO / KEGG) and the local literature library — I won't open UCSC or similar pages for you
- **No invented numbers**: if a coordinate, ID or expression value isn't in the database, I say "not in this database" instead of guessing

The more specific the question, the better: give a gene symbol (tp53, hsp70a), the field you want (coordinates / expression / orthologs / literature) and the species. "In which tissues is hsp70a expressed during embryogenesis" is far easier to answer than "what is hsp70"."""


def is_capability_question(question: str) -> bool:
    """「你能做什么 / 你的能力边界」：必须用固定文案答，不能交给模型自由发挥。

    模型自由发挥会把自己并不具备的能力写上（代查 UCSC、帮忙画图、跑分析），
    用户在界面上找不到对应入口，信任成本比不给答案更高。
    """
    q = (question or "").strip()
    if len(q) < 2:
        return False
    if "模型生物" in q:  # 生物学概念，不是问助手能力
        return False
    try:
        from gene_formatter import is_biology_mechanism_question

        if is_biology_mechanism_question(q):
            return False
    except Exception:
        pass
    return bool(_CAPABILITY_RE.search(q))


def answer_capability(question: str) -> str:
    """按提问语言返回能力自述。"""
    return CAPABILITY_REPLY_EN if detect_question_language(question) == "en" else CAPABILITY_REPLY_ZH


CITATION_DISPLAY_REPLY = (
    "正文里的 [1][2] 就是引用编号，不是调试信息。系统会在文末给出对照表："
    "题名、期刊、年份，有 DOI 时附原文链接。不写 ZFQA、PMID。\n\n"
    "编号是这一问证据池的综合分名次，不是文献库内部号，也不是 PMID。"
    "换一句再问，同一篇论文的内部号不变，[n] 会变。\n\n"
    "若只看到正文角标、看不到文末列表，是生成后没有把对照表补上（或被截断），"
    "不是文献库里没有这些篇。请把科学问题再发一次；机制题会强制补文末文献表。"
)

SEQUENCE_DISPLAY_REPLY = (
    "本 Hub 里基因序列是这样展示的，不依赖文献检索：\n\n"
    "1. **右侧「序列」页**：输入基因名后勾选类型再点「提取序列」。"
    "**cDNA** 来自 NCBI RefSeq **GRCz12tu**（GCF_049306965）CDS，与 BLAST 同库，结果用 FASTA 展开显示；"
    "**启动子 / 基因组区** 走 Ensembl REST，坐标是 **GRCz11**（TSS 上游默认 2 kb）。\n"
    "2. **右侧「BLAST」页**：粘贴 ≥20 bp 核酸（可 FASTA），比对库同样是 GRCz12tu 核酸，"
    "结果列表给出命中基因与比对摘要。\n"
    "3. **聊天里**若触发序列工具，也会在回答详情里用同样的 FASTA / 分块展示。\n\n"
    "标准名、BLAST、cDNA 用 GRCz12tu；本地库坐标/表达仍是 GRCz11，两套不混用。"
)

# 系统/产品元问题：不跑文献检索、不补参考文献（勿匹配「模型生物」）
_SYSTEM_META_RE = re.compile(
    r"(大语言模型|大模型|语言模型|\bllms?\b|\bgpt-?\d*\b|claude|deepseek|通义|qwen|"
    r"用的是什么模型|什么模型|哪个模型|模型版本|底层模型|什么引擎|"
    r"你是谁|你是什么助手|who\s+are\s+you|what\s+(?:llm|model)|which\s+(?:llm|model)|"
    r"系统提示|提示词是|你的\s*prompt|有没有联网|怎么训练的|训练数据)",
    re.I,
)

_PRODUCT_HOWTO_RE = re.compile(
    r"(以什么方式展示|怎么展示|如何展示|怎样展示|如何显示|怎么显示|怎么看|"
    r"哪里看|在哪看|怎么用|如何使用|怎么操作|界面上|右侧|"
    r"工具栏|这个系统|本系统|hub\s*里|页面上)",
    re.I,
)

_SCOPE_KEYWORDS = (
    "斑马鱼", "zebrafish", "danio", "rerio", "基因", "基因组", "grcz", "ensembl",
    "zfin", "zdb-gene", "tp53", "p53", "hsp70", "crispr", "morpholino", "go",
    "kegg", "blast", "表达", "同源", "染色体", "转录", "胚胎", "突变", "敲除",
    "转基因", "原位杂交", "fish", "ncbi", "refseq", "mrna", "cdna", "通路",
    "富集", "蛋白", "发育", "神经", "心脏", "肝脏", "肌肉", "毒性", "再生",
    "线粒体", "文献", "应激", "缺氧", "hif", "pmid", "sox9", "nanog", "myl7",
    "向量化", "知识库", "数据库", "语料", "faiss", "rag", "索引", "分片",
    "检索", "重排", "rerank", "embedding", "嵌入", "文献库", "收录",
)

# 扩大的「非 Hub 科学」提示（供兼容旧 is_zebrafish_in_scope；正式路由见 intent_router）
_OFF_TOPIC_HINTS = (
    "写代码", "写个脚本", "python", "javascript", "java", "leetcode",
    "股票", "炒股", "比特币", "以太坊",
    "今天天气", "明天天气", "天气怎么样", "天气预报",
    "做饭", "菜谱", "旅游", "机票",
    "王者荣耀", "原神", "英雄联盟",
    "chatgpt怎么用", "帮我写作业", "讲个笑话",
    "写诗", "写一首",
)

_TYPE_INSTRUCTIONS = {
    "trait": (
        "本题是性状「由什么决定 / 遗传还是环境」类。"
        "第一句写清遗传与环境及互作，并给出 表型 = 基因型 + 环境 + G×E。"
        "中间按遗传 / 环境 / 饲养（必要时性别）分条，说完即停。"
        "最后一句排序哪些更偏遗传、哪些更偏环境。不要伦理与处死。"
    ),
    "gene": (
        "本题是基因身份/功能类。有则写全称与本地库 ID 一行，再写功能和关键差异；"
        "必要时写等位差异。中短即可，不要扩成综述。"
    ),
    "fact": (
        "本题是数量/ID/染色体/统计类。第一句给出问句要的数字并消歧。"
        "随后约 2～4 条必要区分。禁止扩写机制综述。"
    ),
    "compare": (
        "本题是比较类。第一句给出差异结论，再分遗传背景与环境/品系，不堆无关组学。"
    ),
    "general": (
        "若是机制/发育/通路：第一句结论，中间按证据写够关键环节（通常 4～8 点），说完即停；"
        "不要为凑短而漏关键步骤，也不要综述壳。"
        "若是其它一般题：第一句直接答，中间短条；有证据池时文末文献，无证据池勿硬贴参考文献。"
    ),
}


def is_academic_mode() -> bool:
    return os.getenv("ANSWER_MODE", "balanced").lower() in ("academic", "paper", "strict")


# 2026-10-10 新增：回答语言必须跟随提问语言。
# 之前所有 system prompt 都是中文且没有语言规则，模型一律用中文回答（英文提问也回中文）。
# 2026-10-11 升级：**界面语言优先级最高**（前端把 hub_lang 随请求发来）。
#   理由（用户要求）：英文界面下所有展示（含问答）必须全英文；中文界面下除基因符号/
#   ID/库名/组装名/PMID/DOI 这类专有名词外必须全中文，不允许中英混排。
_ANSWER_LANG = ContextVar("answer_language", default=None)  # 值："en" / "zh" / None


_ANSWER_LANGUAGE_RULE = """

【回答语言（硬要求）】优先级：**用户在本轮提问里明确点名语言 > 界面语言 > 提问语言**。
- 用户说「用中文回答 / 用简体中文 / 说中文」→ 全文用**简体中文**（禁止繁体字）；
  用户说「answer in English / 用英文回答」→ 全文用英文，不要出现中文。
- 未点名语言时，输出语言由**界面语言**决定（下方【语言要求】一行会给明确指令）：
  · 界面语言 = 英文 → 全文用英文回答；基因符号、数据库名、组装名、PMID/DOI 等专有名词保留原文，不要翻译，也不要出现中文。
  · 界面语言 = 中文 → 全文用简体中文回答；**只有**基因符号/ID（如 tp53、ZDB-GENE-…）、数据库与组装名（ZFIN/Ensembl/GRCz11）、PMID/DOI、工具名可以保留英文，
    其余一切（标题、小标题、列表、表头、脚注说明、结语）必须中文，禁止中英混排。
  · 界面语言也未指定时，才退回到「跟随提问语言」。
不要因为系统提示、本地库内容、证据池都是中文就默认用中文回答；反向（英文界面时忍不住写中文）同样禁止。
中文输出一律用**简体**，不要出现「這 / 個 / 資料庫」这类繁体用字。
"""


def set_answer_language(lang: Optional[str]) -> None:
    """设置本次请求的回答语言（en / zh / None=未指定则按提问语言推断）。

    用 ContextVar 而非全局变量：同一进程可能存在不同请求，不能互相污染。
    调用一次只作用于当前上下文；上层未设置时一切保持原行为（不会倒退）。
    """
    v = (lang or "").strip().lower()
    if v.startswith("zh"):
        v = "zh"
    elif v.startswith("en"):
        v = "en"
    else:
        v = None
    _ANSWER_LANG.set(v)


def current_answer_language(question: str = "") -> str:
    """当前生效的回答语言：用户明确点名 > 界面语言 > 按提问语言推断。"""
    explicit = detect_explicit_language_request(question)
    if explicit:
        return explicit
    return _ANSWER_LANG.get() or detect_question_language(question)


# 用户在问题里直接点名语言（"用中文回答" / "answer in English"）。
# 2026-10-24 新增：此前界面语言优先级最高，导致英文界面下用户打「用中文回答」被无视，
# 答案仍是英文（还夹着繁体）。用户点名必须优先于界面语言。
_ZH_REQUEST_RE = re.compile(
    r"(?:请|麻烦|能否|可以)?\s*(?:用|使用|改用|以|换为|换成)\s*(?:简体|繁体|简单)?\s*(?:中文|汉语|国语)"
    r"|(?:中文|汉语)\s*(?:回答|作答|回复|输出|答复|来答)"
    r"|(?:回答|作答|回复|输出|答复)\s*(?:用|使用|以)?\s*(?:简体)?(?:中文|汉语)",
    re.I,
)
_EN_REQUEST_RE = re.compile(
    r"(?:请|麻烦)?\s*(?:用|使用|改用|以)\s*(?:英文|英语)\s*(?:回答|作答|回复|输出|答复)"
    r"|(?:英文|英语)\s*(?:回答|作答|回复|输出)"
    r"|\b(?:answer|reply|respond|write|output)\b[^\n。；]{0,24}\bin\s+english\b"
    r"|\bin\s+english\b[^\n。；]{0,12}\b(?:please|pls)\b",
    re.I,
)


def detect_explicit_language_request(question: str) -> Optional[str]:
    """用户是否在本轮问题里点名要求回答语言：返回 "zh" / "en" / None。"""
    q = question or ""
    if not q:
        return None
    if _EN_REQUEST_RE.search(q):
        return "en"
    if _ZH_REQUEST_RE.search(q):
        return "zh"
    return None


def has_language_rule(prompt: str) -> bool:
    return "【回答语言" in (prompt or "")


def _with_language_rule(prompt: str) -> str:
    """给任何 system prompt 补上语言规则（幂等）。"""
    if not prompt or has_language_rule(prompt):
        return prompt or ""
    return prompt + _ANSWER_LANGUAGE_RULE


def detect_question_language(question: str) -> str:
    """判定提问语言：zh / en。CJK 字符占比 ≥ 15% 视为中文提问。"""
    q = question or ""
    cjk = sum(1 for c in q if "\u4e00" <= c <= "\u9fff")
    letters = sum(1 for c in q if c.isascii() and c.isalpha())
    if cjk >= 2 and cjk >= letters * 0.15:
        return "zh"
    if letters >= 3:
        return "en"
    return "zh"


# ── 出口兜底：中文回答统一转简体 ──────────────────────────────────────────
# 模型偶尔会写繁体（尤其引用台湾/香港文献时），提示词管不住 100%。
# 这里在出口做一次确定性的 t2s 转换：只在本次回答语言 = zh 时生效，
# 英文回答、基因符号、ID、PMID 不受影响（OpenCC 只动汉字）。
_OPENCC_T2S = None
_OPENCC_LOCK = threading.Lock()


def _get_opencc():
    global _OPENCC_T2S
    if _OPENCC_T2S is not None:
        return _OPENCC_T2S
    with _OPENCC_LOCK:
        if _OPENCC_T2S is None:
            try:
                from opencc import OpenCC

                _OPENCC_T2S = OpenCC("t2s")
            except Exception:  # 没装依赖时退化为不转换
                _OPENCC_T2S = False
    return _OPENCC_T2S


def simplify_answer_text(text: str, lang: Optional[str] = None) -> str:
    """回答语言为中文时，把繁体字转成简体字（幂等、无副作用）。"""
    if not text:
        return text
    effective = (lang or _ANSWER_LANG.get() or "").lower()
    if not effective.startswith("zh"):
        return text
    conv = _get_opencc()
    if not conv:
        return text
    try:
        return conv.convert(text)
    except Exception:  # noqa: BLE001
        return text


def build_language_directive(question: str) -> str:
    """放在用户消息末尾的强化指令（比 system prompt 更靠后，模型更听）。"""
    explicit = detect_explicit_language_request(question)
    if current_answer_language(question) == "zh":
        why = "用户在本轮问题里明确要求中文" if explicit == "zh" else "界面语言为中文"
        return ("【语言要求】" + why + "：请全程用**简体中文**回答（不要繁体字）。"
                "基因符号/ID、数据库与组装名（如 tp53、ZDB-GENE-…、ZFIN/Ensembl/GRCz11）、PMID/DOI 可保留英文；"
                "其余（标题、小标题、列表、表头、脚注说明）一律中文，不要中英混排。")
    why_en = "The user explicitly asked for English in this question" if explicit == "en" \
        else "The interface language is English"
    return ("【Language】" + why_en + ": answer **entirely in English**. "
            "Keep gene symbols, IDs, database/assembly names, PMID/DOI as-is; "
            "do not translate them and do not output any Chinese.")


def get_system_prompt() -> str:
    override = _SYSTEM_PROMPT_OVERRIDE.get()
    if override:
        return _with_language_rule(override)
    if is_academic_mode():
        return _with_language_rule(ZEBRAFISH_ACADEMIC_SYSTEM_PROMPT)
    return _with_language_rule(ZEBRAFISH_BALANCED_SYSTEM_PROMPT)


def build_general_assistant_user_prompt(
    question: str, *, tier: str = "D", history: list | None = None
) -> str:
    """邻近生物学 / 通用闲聊车道：无证据池、不要求文献脚注。"""
    t = (tier or "D").upper()
    if t == "C":
        guide = (
            "本题属邻近生物学/通用实验方法（未必是斑马鱼）。"
            "请用该领域正确知识直接回答；不要编造本 Hub 本地库数字。"
            "若自然可联系斑马鱼同源或模型用途，可一句轻提；不要硬套。"
            "不要输出「参考文献」表。"
        )
    else:
        guide = (
            "本题属一般问答/闲聊/编程等。"
            "请像能干的助手一样直接回答；语气自然。"
            "先抓住用户真正要解决的问题，口语、错字、半句话都按最合理的意思理解。"
            "可一句轻提专长是斑马鱼研究（可选），禁止斑马鱼硬套类比，禁止罐头拒答。"
            "不要输出「参考文献」表，不要假装引用本地库。"
        )
    ctx = _recent_dialogue_block(history)
    if ctx:
        return f"{guide}\n\n【最近对话（用于理解指代和追问，不要复述）】\n{ctx}\n\n【用户问题】\n{question}"
    return f"{guide}\n\n【用户问题】\n{question}"


def _recent_dialogue_block(history: list | None, *, turns: int = 4, per_turn: int = 420) -> str:
    """把最近几轮对话压成纯文本。

    通用车道原本完全不使用 history，「它呢」「那小鼠呢」「继续」这类追问
    在一问一答之间就断了，用户只能重述整个问题。
    """
    if not history:
        return ""
    rows: list[str] = []
    for item in history[-turns * 2 :]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = str(item.get("content") or item.get("text") or "").strip()
        if not content:
            continue
        who = "用户" if role in ("user", "human") else "助手"
        if len(content) > per_turn:
            content = content[:per_turn].rstrip() + "…"
        rows.append(f"{who}：{content}")
    return "\n".join(rows)


def classify_answer_type(question: str) -> str:
    q = (question or "").strip()
    if re.search(r"(外形|性状|表型|体色|体型|条纹|鳍长|脊柱|反荫蔽|外观).{0,24}(有关|决定|影响|相关|调控|关系|差异)", q):
        return "trait"
    if re.search(
        r"(和什么有关|与什么有关|受什么影响|由什么决定|和哪些因素|"
        r"有没有关系|会不会影响|更偏遗传|更偏环境|遗传还是环境|和遗传有关|"
        r"品系.{0,12}(外形|外观|差异)|体现在.{0,8}(外形|外观|体色))",
        q,
    ):
        return "trait"
    if re.search(
        r"(几条|多少条|多少个|哪条染色体|位于哪|ensembl|zfin id|表达量|tpm|fpkm|有几个转录本)",
        q,
        re.I,
    ):
        return "fact"
    if re.search(r"(是什么基因|什么功能|功能是什么|编码什么|基因是什么)", q):
        return "gene"
    if re.search(r"(相比|比较|区别|vs\.?|versus)", q, re.I):
        return "compare"
    if re.search(r"差异", q) and re.search(r"(品系|外形|外观|体色|性状)", q):
        return "trait"
    return "general"


def academic_provenance_footer(*sources: str) -> str:
    tags = [s for s in sources if s]
    if not tags:
        return ""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    return "\n\n---\n【数据溯源】" + "；".join(tags) + f" | 系统查询：{ts}"


def is_system_meta_question(question: str) -> bool:
    """用什么模型 / 你是谁 等产品元问题：应短路，禁止挂文献脚注。"""
    q = (question or "").strip()
    if len(q) < 2:
        return False
    if "模型生物" in q:
        return False
    try:
        from gene_formatter import is_citation_display_question, is_biology_mechanism_question

        if is_citation_display_question(q) or is_biology_mechanism_question(q):
            return False
    except Exception:
        pass
    return bool(_SYSTEM_META_RE.search(q))


def is_hub_product_howto_question(question: str) -> bool:
    """问本 Hub 界面/工具怎么展示、怎么用，不是科学文献题。"""
    q = (question or "").strip()
    if len(q) < 4 or not _PRODUCT_HOWTO_RE.search(q):
        return False
    try:
        from gene_formatter import is_biology_mechanism_question, is_citation_display_question

        if is_biology_mechanism_question(q) or is_citation_display_question(q):
            return False
    except Exception:
        pass
    topic = any(
        k in q.lower()
        for k in (
            "序列", "sequence", "cdna", "blast", "fasta", "启动子",
            "界面", "工具", "面板", "按钮", "展示", "显示", "提取",
            "比对", "富集", "kegg", "id解析", "注释", "聊天", "引用",
        )
    )
    return topic


def answer_hub_product_howto(question: str) -> str:
    q = (question or "").lower()
    if any(k in q for k in ("序列", "sequence", "cdna", "fasta", "启动子", "blast", "比对")):
        return SEQUENCE_DISPLAY_REPLY
    return (
        "这是本 Hub 的用法说明，不跑文献检索：\n\n"
        "- **聊天**：中间输入框提问，Enter 发送；回答详情在右侧。\n"
        "- **分析工具**：右侧页签可做基因卡片、BLAST、富集、KEGG、ID 解析、序列提取、注释。\n"
        "- **序列**：见「序列 / BLAST」页；cDNA 与 BLAST 用 GRCz12tu，启动子/基因组区用 GRCz11。\n"
        "- **文献**：机制类问题会检索文献库；问界面用法不需要文献。\n\n"
        "若要某一基因的序列，在右侧「序列」输入基因名并提取即可。"
    )


def answer_citation_display_question(question: str, history: list | None = None) -> str:
    """解释 [n] 与文末文献表；若上一答已有表则原样列出，不另跑检索。"""
    last_asst = ""
    for item in reversed(history or []):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role in ("assistant", "bot", "ai"):
            last_asst = str(item.get("content") or item.get("text") or "").strip()
            break
    body, refs = _split_body_and_refs(last_asst) if last_asst else ("", "")
    cited = _cited_in_body(body) if body else []
    parts = [CITATION_DISPLAY_REPLY]
    if refs.strip():
        parts.append("上一答文末文献如下：\n" + refs.strip())
    elif cited:
        nums = "、".join(f"[{n}]" for n in cited[:25])
        parts.append(
            f"上一答正文用了 {nums}，但当时没有生成文末对照表。"
            "请把原来的科学问题再发一次，系统会补上题名、期刊、年份和原文链接。"
        )
    return "\n\n".join(parts)


def is_zebrafish_in_scope(question: str) -> bool:
    """兼容旧调用：是否适合走 Hub 科学编排（≈ intent tier A）。

    C/D 通用车道不再视为「拒答」；请用 intent_router.classify_question_intent。
    本函数对明显通用话题返回 False（调用方应改走 general assistant，而非 OFF_TOPIC_REPLY）。
    """
    if not question or len(question.strip()) < 2:
        return False
    try:
        from intent_router import classify_question_intent, TIER_A, TIER_B

        tier = classify_question_intent(question).get("tier")
        return tier in (TIER_A, TIER_B)
    except Exception:
        pass
    if is_system_meta_question(question):
        return True
    q = question.lower().strip()
    if any(h in q for h in _OFF_TOPIC_HINTS):
        return False
    if any(k in q for k in _SCOPE_KEYWORDS):
        return True
    if re.search(r"ZDB-GENE-\d+-\d+", question, re.I):
        return True
    if re.search(r"ENS[A-Z]+\d+", question, re.I):
        return True
    if re.search(r"(?:NM|NR|NP|XM|XR|NC)_\d+", question, re.I):
        return True
    return True


def _format_gene_detail_block(gene_detail: dict) -> str:
    if not gene_detail:
        return ""
    try:
        basic = gene_detail.get("basic") or {}
        lines = [
            f"基因：{basic.get('gene_name')} | Ensembl：{basic.get('ensembl_id')} | "
            f"ZFIN：{basic.get('zfin_id')} | 染色体：{basic.get('chromosome')}",
            f"功能摘要：{(basic.get('gene_function') or '')[:400]}",
        ]
        expr = gene_detail.get("expression") or []
        if expr:
            top = expr[:8]
            parts = []
            for e in top:
                tis = e.get("tissue") or e.get("expression_stage") or "?"
                raw = e.get("expression_level")
                if raw is None:
                    raw = e.get("tpm")
                try:
                    val = f"{float(raw):g}"
                except (TypeError, ValueError):
                    val = "?"
                bgee_row = (e.get("data_source") or "").lower() == "bgee" or str(
                    e.get("expression_stage") or ""
                ).strip().lower().startswith("bgee")
                unit = "/100（Bgee 表达分）" if bgee_row else " TPM（Expression Atlas）"
                parts.append(f"{tis}={val}{unit}")
            lines.append(
                "表达（部分；Bgee Expression Score 0–100 是表达存在性置信分，越高越确定有表达，"
                "不是丰度/TPM/FPKM；Expression Atlas 的数值才是 TPM）：" + "; ".join(parts)
            )
        go = gene_detail.get("go") or []
        if go:
            lines.append("GO（部分）：" + "; ".join(
                (g.get("go_term") or "")[:60] for g in go[:6]
            ))
        # 任务①：别名（含历史名）——本地 gene_name_bundle / gene_alias，不编造
        al = gene_detail.get("aliases") or {}
        if al.get("items"):
            grouped: dict = {}
            for it in al["items"]:
                key = it.get("source_label") or it.get("source") or "?"
                grouped.setdefault(key, []).append(it.get("alias") or "")
            lines.append(
                "别名（含历史名，来源分类）："
                + "; ".join(f"{k}={ '/'.join(v[:10]) }" for k, v in grouped.items())
            )
        # 任务②：复制体 / 共直系同源（硬骨鱼全基因组复制）
        pg = gene_detail.get("paralogs") or {}
        if pg.get("groups"):
            seg = []
            for g in (pg.get("groups") or [])[:3]:
                copies = "、".join(
                    (c.get("symbol") or str(c.get("gene_id") or "")) + ("*" if c.get("is_self") else "")
                    for c in (g.get("copies") or [])[:5]
                )
                seg.append(f"{g.get('species')} {g.get('homo_gene_name')} ↔ {copies}")
            lines.append("复制体/共直系同源（* 为本基因）：" + "; ".join(seg))
        return "\n".join(lines)
    except Exception:
        return json.dumps(gene_detail, ensure_ascii=False)[:2000]


def _shared_material_blocks(draft_hint, db_text, gene_detail, rag_context, *, academic: bool):
    parts = []
    if draft_hint and draft_hint.strip():
        if academic:
            parts.append(
                "【系统审定草稿（数字与 ID 已校验，可排版润色但禁止改动事实）】\n"
                f"{draft_hint.strip()}\n"
            )
        else:
            parts.append(
                f"【系统草稿要点（可改写扩写，勿删改其中具体数字与 ID）】\n{draft_hint.strip()}\n"
            )
    if db_text and db_text.strip():
        label = "【本地数据库查询（SQL 结果，数值不可改）】" if academic else "【本地数据库查询】"
        parts.append(f"{label}\n{db_text.strip()}\n")
    gene_block = _format_gene_detail_block(gene_detail)
    if gene_block:
        label = "【本地基因详情（MySQL）】" if academic else "【本地基因详情】"
        parts.append(f"{label}\n{gene_block}\n")
    if rag_context and rag_context.strip():
        label = (
            "【知识库摘录（证据池，不可替代本地库数值；离题片段不要写进正文）】"
            if academic
            else "【知识库检索（证据池，不是大纲；离题片段丢弃）】"
        )
        parts.append(f"{label}\n{rag_context.strip()}\n")
    elif not draft_hint and not db_text and not gene_block:
        parts.append("【知识库检索】\n（未命中相关段落，请结合领域常识作答，不要空喊资料未提及）\n")
    return parts


def build_complete_user_prompt(
    question: str,
    *,
    rag_context: str = "",
    db_text: str = "",
    gene_detail: dict = None,
    draft_hint: str = "",
) -> str:
    qtype = classify_answer_type(question)
    academic = is_academic_mode()
    if academic:
        head = [
            "请按论文可引用精度回答，但版式仍须：第一句直接结论；篇幅按题型："
            "事实题约 2～4 点，机制/发育题按证据写够（通常 4～8 点），说完即停。"
            "机制/基因/性状题正文引用 4～8 个不同编号；事实题 2～4 个；文末由系统保留约 20 条（事实题至少 6）。"
            "不得修改系统草稿或本地库中的任何数字、ID、组装名。",
            _TYPE_INSTRUCTIONS[qtype] + "\n",
        ]
    else:
        head = [
            "请直接写给用户看的答案（不要输出思考过程）。正确性优先：先答对，再去掉废话。"
            "第一句答完问题；篇幅按题型——事实/统计约 2～4 点；"
            "机制/发育/通路按证据写够关键环节（通常 4～8 点），不要为凑短漏关键步骤，也不要综述壳。"
            "条内用 [1][2][3] 标文献；机制/基因/性状正文引用 4～8 个不同编号，事实题 2～4 个；"
            "文末由系统生成约 20 条。离题证据不要写。不要「核心结论」「一是/二是」，不要【常识】块。",
            _TYPE_INSTRUCTIONS[qtype] + "\n",
        ]
    parts = head + _shared_material_blocks(
        draft_hint, db_text, gene_detail, rag_context, academic=academic
    )
    # 任务①/②：别名与复制体证据只在「基因身份 / 别名 / 同源分工」类问题里追加，
    # 普通问题不加（避免每次都多做一次本地题名扫描）。
    try:
        from gene_alias_evidence import (
            build_alias_evidence,
            format_alias_evidence_block,
            format_paralog_prompt_block,
            is_alias_question,
            is_ortholog_question,
            lookup_aliases,
        )

        lang = current_answer_language(question)
        need_alias = is_alias_question(question)
        need_ortho = is_ortholog_question(question)
        if need_alias:
            ev = build_alias_evidence(question, gene_detail)
            blk = format_alias_evidence_block(ev, lang, question=question)
            if blk:
                parts.append(blk)
        # 复制体：① 问句问到同源/分工 → 一定展示；② 或该基因本身就是「一个人类基因对应多个
        # 斑马鱼复制体」→ 也展示（用户确认的口径）。gene_detail 里已有 paralogs 时不额外查库。
        pg = (gene_detail or {}).get("paralogs") or {}
        duplicated = bool(pg.get("is_duplicated")) or any(
            int(g.get("n_copies") or 0) > 1 for g in (pg.get("groups") or [])
        )
        if need_ortho or duplicated:
            if not pg.get("groups") and need_ortho:
                try:
                    from gene_toolkit import extract_gene_from_question, gene_id_of, paralog_block

                    gname = extract_gene_from_question(question)
                    gid = gene_id_of(gname) if gname else None
                    if gid:
                        pg = paralog_block(gid)
                except Exception:
                    pg = pg or {}
            # 任务②：把「每个复制体各自的文献证据」一并喂进去，才可能给出**有依据**的分工结论
            syms = []
            for g in (pg.get("groups") or []):
                for c in (g.get("copies") or []):
                    s = c.get("symbol")
                    if s and s not in syms:
                        syms.append(s)
            role_ev = {}
            if syms:
                try:
                    from gene_alias_fulltext import copy_role_evidence

                    role_ev = copy_role_evidence(syms)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[复制体] 分工证据不可用（不影响回答）：{e}")
            blk2 = format_paralog_prompt_block(pg, lang, role_ev=role_ev)
            if blk2:
                parts.append(blk2)
        if need_alias and gene_detail is None:
            # 部分链路不传 gene_detail，这里不额外补全全量详情，只保证别名块已在上方加入
            _ = lookup_aliases
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[提示词] 别名/复制体证据块构建失败（不影响回答）：{e}")
    parts.append(f"【用户问题】\n{question}")
    # 放在最后一行：语言指令离问题最近，模型最不容易忽略
    parts.append(build_language_directive(question))
    return "\n".join(parts)


def build_academic_user_prompt(
    question: str,
    *,
    rag_context: str = "",
    db_text: str = "",
    gene_detail: dict = None,
    draft_hint: str = "",
) -> str:
    return build_complete_user_prompt(
        question,
        rag_context=rag_context,
        db_text=db_text,
        gene_detail=gene_detail,
        draft_hint=draft_hint,
    )


_GE_LIKE = re.compile(
    r"(表型\s*=\s*基因型|G\s*[×xX]\s*E|基因型\s*\+\s*环境|"
    r"遗传.{0,8}环境.{0,8}互作|遗传\s*\+\s*环境|G\s*\+\s*E)"
)


def ensure_trait_ge_formula(answer: str, question: str = "") -> str:
    """性状题若漏写 G+E 公式，在首段后补一句，对标马铃薯因素框。"""
    ans = (answer or "").rstrip()
    if not ans:
        return ans
    if question and classify_answer_type(question) != "trait":
        return ans
    if _GE_LIKE.search(ans):
        return ans
    inject = "可概括为 表型 = 基因型 + 环境 + G×E。"
    parts = ans.split("\n\n", 1)
    if len(parts) == 2:
        return parts[0].rstrip("。；; ") + "。" + inject + "\n\n" + parts[1]
    lines = ans.split("\n", 1)
    if len(lines) == 2:
        return lines[0].rstrip("。；; ") + "。" + inject + "\n" + lines[1]
    return ans.rstrip("。；; ") + "。" + inject


def normalize_visible_answer(text: str, question: str = "") -> str:
    """去掉综述壳和标签；不强迫【结论】小标题（对标 Potato Agent 正文）。"""
    if not text:
        return text
    body = text.replace("\r\n", "\n").strip()
    body = re.sub(r"【常识】", "", body)
    body = re.sub(r"【通用知识】", "", body)
    body = re.sub(r"^#{1,6}\s*核心结论\s*\n+", "", body, flags=re.M)
    body = re.sub(r"^【结论】\s*\n+", "", body, flags=re.M)
    body = re.sub(r"(?m)^以下逐层展开[。．]?\s*\n+", "", body)
    body = re.sub(r"(?m)^具体而言[：:]\s*", "", body)
    # 开篇「一是/二是」综述腔 → 压成普通分点前缀弱化（保留后文）
    body = re.sub(r"(?m)^[（(]?[一二三四五六七八九十]是[，,：:]\s*", "- ", body)
    body = re.sub(r"^#{1,6}\s+", "", body, flags=re.M)
    # 默认不保留「实验意义」整章
    body = re.sub(
        r"\n+#{0,6}\s*实验意义与研究建议[\s\S]*?(?=\n#{1,6}\s|\n参考文献|\n参考研究|\Z)",
        "\n",
        body,
    )
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = body.strip()
    if question:
        body = ensure_trait_ge_formula(body, question)
    return body


_NUM_PAPER_RE = re.compile(
    r"^\[(\d{1,3})\][ \t]+([^\n]*?)\n题名[：:]([^\n]+)",
    re.M,
)
_NUM_PAPER_LINE_RE = re.compile(
    r"^\[(\d{1,3})\][ \t]+((?:19|20)\d{2}|年不详)[ \t]*\|[^\n]*",
    re.M,
)
_ZFQA_IN_LINE = re.compile(r"(ZFQA-\d{6})")

# 证据池编号行里的检索相关度分数：【3] 2019 | PMID:… | gold | 相关#3 rel=0.871 · 近十年
# 由 `rag/pubmed_yes_retriever.py::_paper_block` 写出，供给 RRS 的 rel 维（连续语义相关度）。
_REL_IN_LINE = re.compile(r"\brel=(\d+(?:\.\d+)?)")


def rels_from_numbered_context(context: str) -> dict[int, float]:
    """从证据池编号行抽出 `{序号: 相关度 0~1}`。

    旧格式（没有 `rel=`）→ 返回空 dict → RRS 的 `rel` 维自动把权重退给位次维，
    总分尺度与改动前一致（不回退排序质量）。
    """
    out: dict[int, float] = {}
    text = (context or "").replace("\r\n", "\n").replace("\r", "\n")
    for line in text.split("\n"):
        s = line.strip()
        if not s.startswith("["):
            continue
        m = _NUM_PAPER_LINE_RE.match(s)
        if not m:
            continue
        rm = _REL_IN_LINE.search(s)
        if not rm:
            continue
        try:
            v = float(rm.group(1))
        except ValueError:
            continue
        n = int(m.group(1))
        if 1 <= n <= 200:
            out[n] = min(1.0, max(0.0, v))
    return out
# 允许「参考文献（由系统生成）：」等修饰语；冒号后可紧跟 [n] 密集段（不一定换行）
_REF_HEAD_KW = (
    r"(?:参考文献|参考资料|引用文献|文献列表|参考研究|References|Bibliography)"
)
_REF_HEAD_RE = re.compile(
    r"(?:\A|\n+)[ \t]*(?:#{0,6}[ \t]*)?(?:\*\*[ \t]*)?"
    + _REF_HEAD_KW
    + r"(?:[ \t]*\*\*)?"
    + r"(?:[ \t]*[（(][^）)\n]{0,40}[）)])?"
    + r"(?:[ \t]*\*\*)?"
    + r"[：:]?"
    + r"[ \t]*(?:\n|(?=\Z)|(?=\[))",
    re.I,
)
# 正文残留标题（含行中/无前导空行）——防御二次剥离
_REF_HEAD_ANY_RE = re.compile(
    r"(?:\A|\n)[ \t]*(?:#{0,6}[ \t]*)?(?:\*\*[ \t]*)?"
    + _REF_HEAD_KW
    + r"(?:[ \t]*\*\*)?"
    + r"(?:[ \t]*[（(][^）)\n]{0,40}[）)])?"
    + r"(?:[ \t]*\*\*)?"
    + r"[：:]?"
    + r"[ \t]*",
    re.I,
)
_FOOTER_LINE_RE = re.compile(r"(?m)^\[(\d{1,3})\]\s+(.+)$")
_ENUM_LINE_RE = re.compile(r"(?m)^(\d{1,3})[\.、]\s+(.+)$")
_BODY_CITE_RE = re.compile(r"\[(\d{1,3})\]")
_BIB_LINE_RE = re.compile(r"^\[(\d{1,3})\]\s+\S")
_BIB_HINT_RE = re.compile(
    r"(PMID\s*:?\s*\d+|ZFQA-\d{6}|ENO:\d+|DOI\s*:|10\.\d{4,}/|20\d{2}|19\d{2})",
    re.I,
)


def _meta_after_title(text: str, end: int) -> tuple[str, str]:
    """题名行之后最多读 8 行，取出期刊和 DOI。"""
    journal = ""
    doi = ""
    for line in (text or "")[end:].splitlines()[:8]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("期刊：") or s.startswith("期刊:"):
            journal = s.split("：", 1)[-1].split(":", 1)[-1].strip()
            continue
        if s.upper().startswith("DOI：") or s.upper().startswith("DOI:"):
            doi = s.split("：", 1)[-1].split(":", 1)[-1].strip()
            continue
        if s.startswith("[") or s.startswith("摘要") or s.startswith("相关段落") or s.startswith("题名"):
            break
    return journal, doi


def papers_from_numbered_context(context: str) -> list[tuple]:
    """从 pack_for_ai 上下文抽出 (序号, 年, 标记, 题名, zfqa, 期刊, doi)。"""
    out: list[tuple] = []
    seen: set[int] = set()
    text = (context or "").replace("\r\n", "\n").replace("\r", "\n")
    for m in _NUM_PAPER_RE.finditer(text):
        n = int(m.group(1))
        if not (1 <= n <= 200) or n in seen:
            continue
        head = m.group(2)
        title = m.group(3).strip()
        parts = [p.strip() for p in head.split("|") if p.strip()]
        year = parts[0] if parts else ""
        tag = parts[1] if len(parts) > 1 else ""
        zfqa = ""
        zm = _ZFQA_IN_LINE.search(m.group(0))
        if zm:
            zfqa = zm.group(1)
        journal, doi = _meta_after_title(text, m.end())
        seen.add(n)
        out.append((n, year, tag, title, zfqa, journal, doi))
    if out:
        return out
    # 题名行丢失时仍保留编号+年份，避免正文有 [n] 却补不出文末表
    for m in _NUM_PAPER_LINE_RE.finditer(text):
        n = int(m.group(1))
        if not (1 <= n <= 200) or n in seen:
            continue
        year = (m.group(2) or "").strip()
        zfqa = ""
        zm = _ZFQA_IN_LINE.search(m.group(0))
        if zm:
            zfqa = zm.group(1)
        seen.add(n)
        out.append((n, year, "", f"证据池第 {n} 篇", zfqa, "", ""))
    return out


def _looks_like_bib_line(s: str) -> bool:
    t = (s or "").strip()
    if not t or len(t) < 20:
        return False
    if _BIB_LINE_RE.match(t):
        return True
    if t[:4].lower() in ("doi:", "pmid") or t.lower().startswith("zfqa-"):
        return True
    if re.match(r"^\d{1,3}[\.、]\s+\S", t) and _BIB_HINT_RE.search(t):
        return True
    return False


def _split_trailing_bib(ans: str, min_lines: int = 6) -> tuple[str, str] | None:
    """无「参考文献」标题时，识别文末连续文献目录（模型整表粘贴）。"""
    lines = (ans or "").split("\n")
    i = len(lines) - 1
    while i >= 0 and not lines[i].strip():
        i -= 1
    n_bib = 0
    while i >= 0:
        s = lines[i].strip()
        if not s:
            i -= 1
            continue
        if _looks_like_bib_line(s):
            n_bib += 1
            i -= 1
            continue
        break
    if n_bib < min_lines:
        return None
    body = "\n".join(lines[: i + 1]).rstrip()
    blob = "\n".join(lines[i + 1 :]).strip()
    return body, blob


def _strip_residual_ref_sections(body: str) -> str:
    """去掉正文里残留的参考文献标题及其后内容，只保留标题之前的正文。"""
    text = (body or "").rstrip()
    while True:
        m = _REF_HEAD_ANY_RE.search(text)
        if not m:
            break
        text = text[: m.start()].rstrip()
    return text


def _split_body_and_refs(ans: str) -> tuple[str, str]:
    text = (ans or "").rstrip()
    m = _REF_HEAD_RE.search(text)
    if m:
        body = text[: m.start()].rstrip()
        refs = text[m.end() :]
        # 防御：正文里若还有另一段文献标题，一并剥掉
        body = _strip_residual_ref_sections(body)
        return body, refs
    trailing = _split_trailing_bib(text)
    if trailing:
        body, refs = trailing
        return _strip_residual_ref_sections(body), refs
    return _strip_residual_ref_sections(text), ""


def _papers_from_footer_blob(blob: str) -> list[tuple[int, str, str, str, str]]:
    """模型自己贴的文末表：裁剪时当题名目录用。"""
    out: list[tuple[int, str, str, str, str]] = []
    seen: set[int] = set()
    for rx in (_FOOTER_LINE_RE, _ENUM_LINE_RE):
        for m in rx.finditer(blob or ""):
            n = int(m.group(1))
            if not (1 <= n <= 200) or n in seen:
                continue
            seen.add(n)
            line = m.group(2).strip().rstrip(".")
            zfqa = ""
            zm = _ZFQA_IN_LINE.search(line)
            if zm:
                zfqa = zm.group(1)
            out.append((n, "", "", line, zfqa, "", ""))
    return out


def _cited_in_body(body: str) -> list[int]:
    ordered: list[int] = []
    seen: set[int] = set()
    for x in _BODY_CITE_RE.findall(body or ""):
        n = int(x)
        if not (1 <= n <= 200) or n in seen:
            continue
        seen.add(n)
        ordered.append(n)
    return ordered


def _ref_footer_min(question: str = "") -> int:
    """文末参考文献最少条数。

    REF_FOOTER_MIN 只抬机制/性状/功能类下限（默认 20）；事实题固定 6，避免染色体/ID 题被文献列表淹没。
    2026-10-28：纯别名问题固定 2（与上限 4 配套，别再拿 20 条文献去补）。
    """
    qtype = classify_answer_type(question) if question else "general"
    if question:
        try:
            from gene_alias_evidence import asks_corpus_occurrence, is_alias_question

            if is_alias_question(question) and not asks_corpus_occurrence(question):
                return 2
        except Exception:
            pass
    if qtype == "fact":
        return 6
    try:
        override = os.getenv("REF_FOOTER_MIN", "").strip()
        if override:
            return max(20, int(override))
    except (TypeError, ValueError):
        pass
    return 20


def _ref_footer_max(question: str = "") -> int:
    """文末上限 25，避免模型把 100 篇证据池整表贴进回答。

    2026-10-28：纯别名问题（没问文献里出现多少）最多 4 条 —— 用户明确说过
    「不用返回和问题无关的回答」，答"有多少别名"却跟 8~20 篇文献是噪音。
    """
    qtype = classify_answer_type(question) if question else "general"
    if question:
        try:
            from gene_alias_evidence import asks_corpus_occurrence, is_alias_question

            if is_alias_question(question) and not asks_corpus_occurrence(question):
                return 4
        except Exception:
            pass
    if qtype == "fact":
        return 12
    cap = 25
    try:
        override = os.getenv("REF_FOOTER_MAX", "").strip()
        if override:
            cap = min(25, max(_ref_footer_min(question), int(override)))
    except (TypeError, ValueError):
        pass
    return cap


def _alias_reference_tuples(question: str, limit: int = 25) -> list[tuple]:
    """别名 / 复制体类问题：把「全文别名倒排表」里的文献做成文末条目。

    为什么（2026-10-17 用户反馈）：
      · 现象一「参考文献（系统生成）：PMID … 等。」——通用 RAG 证据池被相关性地板筛空时，
        `ensure_reference_footer` 拿不到任何条目就原样返回，模型那句占位话就留在正文里；
      · 现象二「只列 6 条，不到 20~25」——证据池本来就只有几条合格条目，补无可补。
    而这类问题在全文倒排表里有几千篇精确实证，一直没喂给参考文献生成器。

    返回与证据池一致的 7 元组：(序号, 年, 标记, 题名, zfqa, 期刊, doi)。
    非别名/复制体问题、或取不到时返回 []（调用方沿用原逻辑，行为完全不变）。
    """
    q = (question or "").strip()
    if not q:
        return []
    try:
        from gene_alias_evidence import is_alias_question, is_ortholog_question
        from gene_alias_fulltext import alias_reference_items
        from gene_toolkit import extract_gene_from_question
    except Exception:  # noqa: BLE001
        return []
    try:
        if not (is_alias_question(q) or is_ortholog_question(q)):
            return []
        gene = extract_gene_from_question(q)
        if not gene:
            return []
        rows = alias_reference_items(gene, limit=limit)
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple] = []
    for i, r in enumerate(rows, 1):
        pmid = str(r.get("pmid") or "").strip()
        tag = ("PMID %s" % pmid) if pmid else ""
        out.append((i, str(r.get("year") or ""), tag, str(r.get("title") or ""),
                    "", str(r.get("journal") or ""), str(r.get("doi") or "")))
    return out


def _pick_footer_ns(
    cited: list[int],
    papers: list[tuple[int, str, str, str, str]],
    min_n: int,
    max_n: int,
) -> list[int]:
    by_n = {p[0] for p in papers}
    want: list[int] = []
    seen: set[int] = set()
    for n in cited:
        if n not in by_n or n in seen:
            continue
        seen.add(n)
        want.append(n)
        if len(want) >= max_n:
            return want
    for n, *_rest in papers:
        if len(want) >= min_n:
            break
        if n in seen:
            continue
        seen.add(n)
        want.append(n)
    if not want:
        want = [p[0] for p in papers[:min_n]]
    return want[:max_n]


def _apply_cite_map(body: str, mapping: dict[int, int]) -> str:
    """把正文里的证据池编号收成连续号；超出文末上限的编号删掉。"""

    def repl(m: re.Match) -> str:
        n = int(m.group(1))
        new = mapping.get(n)
        return f"[{new}]" if new is not None else ""

    return _BODY_CITE_RE.sub(repl, body or "")


def _paper_view(p: tuple) -> tuple[int, str, str, str, str, str, str]:
    n, year, tag, title, zfqa = p[0], p[1], p[2], p[3], p[4]
    journal = p[5] if len(p) > 5 else ""
    doi = p[6] if len(p) > 6 else ""
    return n, year or "", tag or "", title or "", zfqa or "", journal or "", doi or ""


def _norm_title_key(title: str) -> str:
    """题名规范化：小写、压空白、去尾标点/HTML 实体。"""
    raw = _clean_ref_title(title) or ""
    try:
        import html as _html

        raw = _html.unescape(raw)
    except Exception:
        pass
    t = re.sub(r"\s+", " ", raw).lower().strip(" .;:,-—–\"'`")
    return t


def _norm_journal_key(journal: str) -> str:
    """刊名比较用：去掉末尾城市括注，如 (Basel, Switzerland)。"""
    j = re.sub(r"\s+", " ", (journal or "").strip())
    j = re.sub(r"\s*[（(][^）)]{0,80}[）)]\s*$", "", j)
    return j.lower().strip(" .")


def _title_year_key(p: tuple) -> str:
    """弱身份：规范化题名+年（用于 DOI/PMID 不同但仍是同篇 twin）。"""
    _n, year, _tag, title, _zfqa, _journal, _doi = _paper_view(p)
    t = _norm_title_key(title)
    if not t or t.startswith("证据池第"):
        return ""
    y = "" if year in ("年不详", "None", None, "") else str(year).strip()[:4]
    return f"{t[:120]}|{y}"


def _paper_richness(p: tuple) -> tuple:
    """同篇候选择优：DOI > PMID > 真实刊名 > 更长刊名 > 更小原编号。"""
    n, _year, tag, _title, _zfqa, journal, doi = _paper_view(p)
    d = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", (doi or "").strip()).strip(" .;")
    has_doi = bool(re.match(r"^10\.\d{4,}/\S+$", d))
    has_pmid = bool(re.search(r"(?i)\bPMID\s*:?\s*(\d{5,9})", tag or ""))
    j = (journal or "").strip().rstrip(".")
    j_ok = bool(j) and j.lower() not in ("unlisted journal", "unlisted")
    return (
        1 if has_doi else 0,
        1 if has_pmid else 0,
        1 if j_ok else 0,
        len(j),
        -int(n),
    )


def _paper_identity(p: tuple) -> str:
    """同篇去重键：DOI > ZFQA > PMID(tag) > 规范化题名+年。"""
    n, year, tag, title, zfqa, journal, doi = _paper_view(p)
    del n, journal
    d = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", (doi or "").strip()).strip(" .;")
    if re.match(r"^10\.\d{4,}/\S+$", d):
        return "doi:" + d.lower()
    z = (zfqa or "").strip().upper()
    if re.match(r"^ZFQA-\d{6}$", z):
        return "zfqa:" + z
    pm = re.search(r"(?i)\bPMID\s*:?\s*(\d{5,9})", tag or "")
    if pm:
        return "pmid:" + pm.group(1)
    t = _norm_title_key(title)
    y = "" if year in ("年不详", "None", None, "") else str(year).strip()[:4]
    if t:
        return f"title:{t[:120]}|{y}"
    return f"row:{tag}|{y}"


def _pick_better_n(a: int, b: int, by_n: dict) -> int:
    pa, pb = by_n.get(a), by_n.get(b)
    if not pa:
        return b
    if not pb:
        return a
    return a if _paper_richness(pa) >= _paper_richness(pb) else b


def _dedupe_want_papers(want: list[int], papers: list) -> list[int]:
    """want 按强身份去重，再按题名+年合并 twin，保留更丰富的那条。"""
    by_n = {p[0]: p for p in papers}
    out: list[int] = []
    seen: set[str] = set()
    for n in want:
        p = by_n.get(n)
        if not p:
            continue
        key = _paper_identity(p)
        if key in seen:
            # 同强键：若新的更丰富，替换已选
            for i, old in enumerate(out):
                if _paper_identity(by_n[old]) == key:
                    out[i] = _pick_better_n(old, n, by_n)
                    break
            continue
        seen.add(key)
        out.append(n)
    # 题名 twin：一条有 DOI、一条仅题名时强键不同，再合一次
    best_ty: dict[str, int] = {}
    order_ty: list[str] = []
    leftover: list[int] = []
    for n in out:
        ty = _title_year_key(by_n[n])
        if not ty:
            leftover.append(n)
            continue
        if ty not in best_ty:
            best_ty[ty] = n
            order_ty.append(ty)
        else:
            best_ty[ty] = _pick_better_n(best_ty[ty], n, by_n)
    merged = [best_ty[k] for k in order_ty] + leftover
    return merged


def _enrich_papers_journals(papers: list, budget_sec: float | None = None) -> list:
    """脚注前补刊名：保证每条有非空 journal 字符串。

    提速要点（2026-10-11）：
      · 这条路径原先 `allow_network=True`，每条缺刊名的参考文献最多联网 3 次
        （Crossref DOI → PubMed → Crossref 题名），且有 0.34 s 强制间隔、20 s 超时，
        一次回答可能白等好几秒到几十秒。
      · 现在给整批一个**时间预算**（默认 2 s，`REF_JOURNAL_BUDGET_SEC` 可调）：
        预算内允许联网，超预算立刻降级为「只用本地 XML / 进程缓存」，保证回答不被拖住。
      · 缺刊名仍会写成 Unlisted journal（展示不空缺），后台可再补。
    """
    try:
        from journal_enrich import resolve_journal
    except ImportError:
        try:
            from rag.journal_enrich import resolve_journal
        except ImportError:
            resolve_journal = None  # type: ignore
    if budget_sec is None:
        try:
            budget_sec = float(os.getenv("REF_JOURNAL_BUDGET_SEC", "2.0"))
        except (TypeError, ValueError):
            budget_sec = 2.0
    deadline = time.time() + max(0.0, budget_sec)
    out: list = []
    for p in papers:
        n, year, tag, title, zfqa, journal, doi = _paper_view(p)
        pmid = ""
        pm = re.search(r"(?i)\bPMID\s*:?\s*(\d{5,9})", tag or "")
        if pm:
            pmid = pm.group(1)
        if resolve_journal is not None:
            journal = resolve_journal(
                journal=journal,
                pmid=pmid,
                doi=(doi if _doi_trustworthy(doi) else ""),  # 不可信 DOI 不拿去查刊名
                title=title,
                year=str(year or ""),
                # 预算用尽 → 只查本地/缓存，不再等网络
                allow_network=(time.time() < deadline),
                persist=True,
            )
        elif not (journal or "").strip():
            journal = "Unlisted journal"
        out.append((n, year, tag, title, zfqa, journal, doi))
    return out


def _compact_footer_items(
    cited: list[int],
    want: list[int],
    papers: list,
) -> tuple[dict[int, int], list]:
    """证据池编号收成文末 [1][2][3]…，按综合分从高到低；同篇去重。

    证据池编号越小综合分越高，所以按原编号升序排。正文 [n] 改成同一套号，
    不按句子里第一次出现的顺序。
    """
    del cited  # 入选名单由 want 决定；排序只看综合分
    papers = _enrich_papers_journals(papers)
    by_n = {p[0]: p for p in papers}
    want_in = [n for n in want if n in by_n]
    want = _dedupe_want_papers(want_in, papers)
    ordered_old = sorted(n for n in want if n in by_n)
    # 再按身份+题名 twin 去重（升序后同篇只留更丰富/更小原编号）
    seen_id: set[str] = set()
    ordered_unique: list[int] = []
    for old in ordered_old:
        key = _paper_identity(by_n[old])
        if key in seen_id:
            continue
        seen_id.add(key)
        ordered_unique.append(old)
    ordered_old = _dedupe_want_papers(ordered_unique, papers)
    mapping = {old: i + 1 for i, old in enumerate(ordered_old)}
    # 被合并的 twin / 同强键：正文里的旧号也映到同一新号
    id_to_new = {
        _paper_identity(by_n[n]): mapping[n] for n in ordered_old if n in by_n
    }
    ty_to_new: dict[str, int] = {}
    for n in ordered_old:
        ty = _title_year_key(by_n[n])
        if ty:
            ty_to_new[ty] = mapping[n]
    for n in want_in:
        if n in mapping:
            continue
        p = by_n.get(n)
        if not p:
            continue
        key = _paper_identity(p)
        if key in id_to_new:
            mapping[n] = id_to_new[key]
            continue
        ty = _title_year_key(p)
        if ty and ty in ty_to_new:
            mapping[n] = ty_to_new[ty]
    use: list = []
    for old in ordered_old:
        n, year, tag, title, zfqa, journal, doi = _paper_view(by_n[old])
        use.append((mapping[old], year, tag, title, zfqa, journal, doi))
    return mapping, use


_ENO_IN_TEXT = re.compile(r"(?i)\bENO\s*:?\s*(\d+)")
_EXT_ID_IN_TITLE = re.compile(
    r"(?i)\s*(?:"
    r"PMID\s*:?\s*\d+|"
    r"DOI\s*:?\s*|"
    r"https?://(?:dx\.)?doi\.org/\S+|"
    r"\b10\.\d{4,}/[^\s,;]+|"
    r"ZFQA-\d{6}|"
    r"ENO\s*:?\s*\d+"
    r")"
)


def _doi_url(doi: str) -> str:
    raw = (doi or "").strip()
    raw = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", raw)
    raw = raw.strip(" .;")
    if not re.match(r"^10\.\d{4,}/\S+$", raw):
        return ""
    return "https://doi.org/" + raw


def _clean_ref_title(title: str) -> str:
    raw = (title or "").strip()
    t = _EXT_ID_IN_TITLE.sub("", raw)
    t = re.sub(r"\s+", " ", t).strip(" .")
    t = re.sub(r"\s*\.\s*\.", ".", t)
    return t


def _doi_trustworthy(doi: str) -> bool:
    """DOI 是否可信（挡住「一 DOI 多论文」的错误链接）。

    2026-10-10 数据库体检：库内 2,651 个 DOI 对应多篇不同论文（抽的是全文参考文献里
    的方法学论文 DOI）。这类 DOI 一旦拼进「[原文](doi)」就会指向**另一篇论文**。
    清单由 `scripts/build_doi_conflict_list.py` 生成；清单缺失时自动放行（不阻碍功能）。
    """
    if not (doi or "").strip():
        return False
    try:
        try:
            from doi_guard import is_conflicted
        except ImportError:
            from llm.doi_guard import is_conflicted  # type: ignore
        return not is_conflicted(doi)
    except Exception:  # noqa: BLE001
        return True


def _format_ref_line(
    n: int,
    year: str,
    tag: str,
    title: str,
    zfqa: str = "",
    journal: str = "",
    doi: str = "",
) -> str:
    """给人看的一条文献：题名、期刊、年份、原文/检索链接。不写 ZFQA。

    期刊名必须非空。有 DOI → [原文](doi)；无 DOI → Google + Scholar + 备选。
    """
    del zfqa
    tbit = _clean_ref_title(title)
    if not tbit:
        tbit = "Untitled article"
    if not _doi_trustworthy(doi):
        doi = ""  # 不可信 DOI 不入链（改走 PMID / 检索链接）
    jbit = re.sub(r"\s+", " ", (journal or "").strip()).strip(" .")
    if not jbit:
        jbit = "Unlisted journal"
    y = "" if year in ("年不详", "None", None, "") else str(year).strip()
    bits = [f"[{n}]"]
    if tbit:
        bits.append(f"{tbit}.")
    bits.append(f"{jbit}.")
    if y:
        bits.append(f"{y}.")
    pmid = ""
    pm = re.search(r"(?i)\bPMID\s*:?\s*(\d{5,9})", tag or "")
    if pm:
        pmid = pm.group(1)
    try:
        from journal_enrich import original_links
    except ImportError:
        try:
            from rag.journal_enrich import original_links
        except ImportError:
            original_links = None  # type: ignore
    if original_links is not None:
        for label, url in original_links(
            title=tbit, journal=jbit, year=y, doi=doi, pmid=pmid
        ):
            bits.append(f"[{label}]({url})")
    else:
        url = _doi_url(doi)
        if url:
            bits.append(f"[原文]({url})")
    return " ".join(bits)


def _footer_item_count(text: str) -> int:
    _body, refs = _split_body_and_refs(text)
    return len(re.findall(r"(?m)^\[(\d{1,3})\]\s+\S", refs or ""))


def _peel_after_bib(old_refs: str) -> tuple[str, str]:
    """文献表后的 --- 产品附加行（检索源等）二次裁剪时保留。"""
    blob = old_refs or ""
    m = re.search(r"\n---\s*\n", blob)
    if not m:
        return blob, ""
    return blob[: m.start()].rstrip(), blob[m.start() :].lstrip("\n")


def _rebuild_footer(body: str, use: list, max_n: int) -> str:
    lines = ["", "参考文献："]
    for item in use[:max_n]:
        n, year, tag, title, zfqa, journal, doi = _paper_view(item)
        lines.append(_format_ref_line(n, year, tag, title, zfqa, journal, doi))
    return body + "\n" + "\n".join(lines)


def _strip_citations(body: str, suffix: str = "") -> str:
    """不成表时的收尾：去掉正文悬空 [n]，保留尾部产品附加行。"""
    body = _BODY_CITE_RE.sub("", body or "")
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).rstrip()
    return body + (("\n\n" + suffix) if suffix else "")


def _filter_by_relevance(
    papers: list,
    question: str,
    cited: list[int],
    rel_map: dict | None = None,
) -> tuple[list, list]:
    """RRS 加权精排层（接在原有检索链之后，不另起一套）。

    原机制（保持不变）：FAISS 粗排 500 → CrossEncoder 精排 100 → 文末 20~25 条。
    本层在精排 100 之后只做两件事：
      · **重排**：按 RRS 五维加权（实体 .30 / 位次 .25 / 正文引用 .20 / 时效 .15 / 元数据 .10）
        从高到低排序，并只保留前 `_ref_footer_max(question)` 条（机制题 25 / 事实题 12）。
      · **一条硬排除**：问句点名了实体（基因/ID）但题名不含且正文未引用 → 剔除，永不补回
        （这就是「问 nanog 却挂纳米颗粒论文」的治法）。
    之后 `ensure_reference_footer` 再把表补到 `REF_FOOTER_MIN`(20) ~ `REF_FOOTER_MAX`(25) 条。

    为什么不再用「门槛淘汰」（2026-10-11 用户反馈）：单次门槛会把整池切成 0~3 条，
    下游 `len(use) < 2` 不成表、正文编号一起被清 —— 表现就是「引用怎么这么少了」；
    而且用户要的是**在原机制上升级**（保持 20~25 条文献表），不是换一套规则。
    打分失败时退回原行为（不因辅助逻辑丢掉参考文献）。
    """
    try:
        try:
            from ref_relevance import select_relevant
        except ImportError:
            from llm.ref_relevance import select_relevant  # type: ignore
        return select_relevant(
            papers, question, cited,
            max_keep=_ref_footer_max(question),
            rel_map=rel_map or {},
        )
    except Exception:  # noqa: BLE001
        return list(papers or []), []


def ensure_reference_footer(answer: str, context: str = "", question: str = "") -> str:
    """文献编号证据存在时，重写文末参考文献。

    - 只统计正文 [n]，忽略模型自己贴的 100 篇整表。
    - 正文已有编号：优先保留这些；不足下限时从证据池按综合序补足。
    - 超过上限一律裁掉（机制题 20～25 条）。
    - 编号收成从 [1] 起的连续号。文末按综合分从高到低，与正文用同一套号。
    - 系统元问题仍不补脚注。
    - 绝不把模型整表原样放行（含无冒号「参考文献」标题）。
    """
    ans = (answer or "").replace("\r\n", "\n").replace("\r", "\n").rstrip()
    if not ans:
        return ans
    # 证据池编号行里的 `rel=` 是给系统排序用的，模型偶尔会抄进正文 —— 这里清掉
    if "rel=" in ans:
        ans = re.sub(r"\s*\brel=\d+(?:\.\d+)?", "", ans)
    if question and is_system_meta_question(question):
        return ans
    body, old_refs = _split_body_and_refs(ans)
    body = _strip_residual_ref_sections(body)
    ref_blob, suffix = _peel_after_bib(old_refs)
    papers = papers_from_numbered_context(context)
    # 没有证据池时，不把模型自己写的文献表当成真文献。
    # 只有模型原样抄了带「题名」或 ZFQA 的证据池，才拿来裁剪。
    if not papers and ("题名：" in (ref_blob or "") or "ZFQA-" in (ref_blob or "")):
        papers = _papers_from_footer_blob(ref_blob)
    # 别名 / 复制体类问题：证据往往不在通用证据池里，而全文别名倒排表有几千篇 ——
    # 用它出文末表，避免"整池被筛空 → 只剩模型自己写的那句『参考文献（系统生成）：…』"。
    alias_refs = _alias_reference_tuples(question, limit=_ref_footer_max(question))
    if not papers:
        if alias_refs:
            clean = _strip_citations(_strip_residual_ref_sections(body), "")
            out = _rebuild_footer(clean, alias_refs, _ref_footer_max(question))
            return out + (("\n\n" + suffix) if suffix else "")
        if old_refs:
            return _strip_citations(body, suffix)
        return ans

    cited = _cited_in_body(body)
    min_n = _ref_footer_min(question)
    max_n = _ref_footer_max(question)
    # —— RRS 相关性地板：先筛掉弱相关条目，再选号 ——
    papers, _rrs_dropped = _filter_by_relevance(
        papers, question, cited, rels_from_numbered_context(context)
    )
    if not papers:
        # 整池都不过门槛：不硬凑文末表，只清掉正文悬空编号
        return _strip_citations(body, suffix) if old_refs else ans
    want = _pick_footer_ns(cited, papers, min_n, max_n)
    mapping, use = _compact_footer_items(cited, want, papers)
    # 去重后不足下限：从证据池按序再补（仍受 max_n 约束）
    if use and len(use) < min_n:
        have_ids = {_paper_identity(p) for p in use}
        by_n = {p[0]: p for p in _enrich_papers_journals(papers)}
        for n, *_rest in papers:
            if len(use) >= min_n:
                break
            p = by_n.get(n)
            if not p:
                continue
            key = _paper_identity(p)
            if key in have_ids:
                continue
            have_ids.add(key)
            # 追加到 want 并重建编号
            want.append(n)
        mapping, use = _compact_footer_items(cited, want, papers)
    # 证据池补不满下限时，用全文别名索引继续补（都是真实文献，不编造）
    if use and len(use) < min_n and alias_refs:
        have_ids = {_paper_identity(p) for p in use}
        seq = max([int(p[0]) for p in use] or [0])
        for t in alias_refs:
            if len(use) >= min_n:
                break
            key = _paper_identity(t)
            if key in have_ids:
                continue
            have_ids.add(key)
            seq += 1
            use.append((seq, t[1], t[2], t[3], t[4], t[5], t[6]))
    if len(use) < 2:
        # 宁缺勿滥：不足 2 条不成表，避免「只挂一篇无关文献」比不挂更糟
        return _strip_citations(body, suffix) if old_refs else body

    body = _apply_cite_map(body, mapping)
    out = _rebuild_footer(body, use, max_n)
    n_foot = _footer_item_count(out)
    if n_foot > max_n:
        use = use[:max_n]
        out = _rebuild_footer(body, use, max_n)
    if _footer_item_count(out) > max_n:
        use = use[:max_n]
        out = _rebuild_footer(body, use, max_n)
    if suffix:
        out = out.rstrip() + "\n\n" + suffix
    return out


def build_rag_user_prompt(question: str, context: str) -> str:
    return build_complete_user_prompt(question, rag_context=context)


def build_db_user_prompt(question: str, data_text: str) -> str:
    return build_complete_user_prompt(question, db_text=data_text)


def build_fusion_user_prompt(question: str, context: str = "") -> str:
    return build_complete_user_prompt(question, rag_context=context)


def build_general_user_prompt(question: str) -> str:
    """Hub 内 GENERAL 任务类型：仍走完整材料块；纯闲聊请用 build_general_assistant_user_prompt。"""
    return build_complete_user_prompt(question)



# ── 兼容壳（服务器 api_server.py 仍在用）：回答是否应为英文 ──────────────
def answer_is_english(question: str = "") -> bool:
    return current_answer_language(question) == "en"
