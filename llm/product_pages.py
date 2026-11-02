# -*- coding: utf-8 -*-
"""功能页事实表 —— 回答「某个页面怎么用」时的唯一依据。

写这份表的原因（2026-11-02 全站功能页核查）：
`answer_hub_product_howto` 原来只有两套固定文案（序列类 / 通用类），
问「GO 富集页怎么用」也会回「中间输入框提问、右侧页签……」，
用户拿到的是一段与本页无关的说明。这里按页面各写一条，
内容全部对着 web/index.html 的面板、web/js/page-meta.js 的数据卡
和 api/api_server.py 的实际接口逐条核过。

约定：
- `aliases` 是正则片段，按 `ORDER` 顺序匹配，先长后短（位置查序列 先于 位置查找）。
- 只写界面上真有的输入、输出、上限和口径；没有的能力一律不写。
- 回答不跑文献检索，所以文案里不出现 PMID / 参考文献。
"""

# 顺序即优先级：更具体的页面名放前面，避免「位置查序列」被「位置查找」抢走。
ORDER = [
    "sequence-region",
    "phenotype-term",
    "phenotype-gene",
    "liftover",
    "expression",
    "genes",
    "gene",
    "blast",
    "go",
    "kegg",
    "sgrna",
    "primer",
    "id",
    "annotate",
    "upload",
    "about",
    "news",
    "ops",
    "chat",
]

PAGES = {
    "chat": {
        "title_zh": "Chat 对话",
        "title_en": "Chat",
        "route": "#/chat",
        "aliases": r"(聊天页|对话页|聊天界面|chat(?!gpt)|这个助手怎么用|怎么提问)",
        "zh": """**Chat 对话（#/chat）** —— 全站唯一的问答入口，不需要选模式。

- **输入**：中间输入框，最多 500 字；Enter 发送，或点「发送」。
- **左侧**：历史对话列表、搜索、开启新对话；窄屏收成抽屉，点「历史」展开。
- **右侧「回答详情」**：本轮走过的路由、SQL、检索片段、基因卡片，以及 9 个工具页签（基因 / BLAST / 富集 / KEGG / ID 解析 / 序列 / 注释 / 检索调试 / 入库）。
- **多轮**：自动带上文，输入框上方显示「已带入上文 N 轮」，可点「清空」从本轮重开。
- **引用**：正文的 [n] 是引用编号，文末给题名 / 期刊 / 年份对照表，有 DOI 附链接。""",
        "en": """**Chat (#/chat)** — the single question entry point; no mode to pick.

- **Input**: the box in the middle, up to 500 characters; press Enter or click “Send”.
- **Left**: conversation history, search, start a new chat; on narrow screens it collapses into a drawer opened by “History”.
- **Right “Answer details”**: the route taken, SQL, retrieved passages, gene cards, and 9 tool tabs (gene / BLAST / enrichment / KEGG / ID resolution / sequence / annotation / retrieval debug / upload).
- **Multi-turn**: context is carried automatically; “N turns of context” appears above the input and can be cleared.
- **Citations**: [n] in the text are citation numbers; a title / journal / year table follows the answer, with DOI links where available.""",
    },
    "gene": {
        "title_zh": "基因位置查找",
        "title_en": "Gene locus",
        "route": "#/gene",
        "aliases": r"(基因位置|位置查找(?!序列)|查位置|查基因坐标|基因.{0,3}坐标|gene\s*locus)",
        "zh": """**基因位置查找（#/gene）** —— 给一个基因名，看它落在基因组的哪里。

- **入口**：Omics › Gene › 位置查找；也支持链接直达（`#/gene/tp53` 会自动查）。
- **输入**：一个基因符号，例如 tp53、sox9b、ZDB-GENE-…
- **输出**：染色体、起止坐标、链方向、基因体序列。
- **口径**：坐标是 Ensembl GRCz11 r116；cDNA 与符号版本是 NCBI RefSeq GRCz12tu，两套不混用。
- **不在本页做**：「这个名字到底是哪个基因、还有哪些叫法」去「别名」页；表型去「基因查表型」页。""",
        "en": """**Gene locus (#/gene)** — give a gene name, see where it sits in the genome.

- **Entry**: Omics › Gene › 位置查找; deep links work too (`#/gene/tp53` runs automatically).
- **Input**: one gene symbol, e.g. tp53, sox9b, ZDB-GENE-…
- **Output**: chromosome, start/end coordinates, strand, gene-body sequence.
- **Assembly**: coordinates are Ensembl GRCz11 r116; cDNA and symbols are NCBI RefSeq GRCz12tu — the two are never mixed.
- **Not here**: “which gene is this name, and what else is it called” belongs to the aliases page; phenotypes belong to gene → phenotype.""",
    },
    "genes": {
        "title_zh": "基因别名",
        "title_en": "Gene aliases",
        "route": "#/genes",
        "aliases": r"(基因别名|别名|alias|旧名|同义名|former\s*name)",
        "zh": """**基因别名（#/genes）** —— 给一个名称，确认它是哪个基因、还有哪些叫法。

- **入口**：Omics › Gene › 别名；支持 `#/genes/p53` 直达。
- **输入**：符号、别名、旧名或跨库 ID（Ensembl / ZFIN / NCBI / UniProt），是模糊检索，会给候选列表。
- **输出**：候选 → 选中后给官方符号、别名与历史名、跨库 ID。
- **口径**：本地 3.2 万个基因、14.5 万条别名。
- **不在本页做**：坐标与基因体（去「位置查找」）；批量 ID 互转（去 Liftover 页）。""",
        "en": """**Gene aliases (#/genes)** — give a name, confirm which gene it is and what else it is called.

- **Entry**: Omics › Gene › 位置查找's neighbour “别名”; `#/genes/p53` opens directly.
- **Input**: symbol, alias, former name or cross-DB ID (Ensembl / ZFIN / NCBI / UniProt); fuzzy search returns candidates.
- **Output**: candidates → pick one to get the official symbol, aliases and historical names, cross-DB IDs.
- **Scale**: ~32k genes and ~145k alias entries locally.
- **Not here**: coordinates and gene body (gene locus page); batch ID conversion (Liftover page).""",
    },
    "liftover": {
        "title_zh": "Liftover",
        "title_en": "Liftover",
        "route": "#/liftover",
        "aliases": r"(liftover|坐标换算|坐标转换|坐标映射|登录号|accession|批量\s*id\s*互转|id\s*互转)",
        "zh": """**Liftover（#/liftover）** —— 一页两个模式：坐标换算 / 批量 ID 互转。

- **坐标换算**：先选来源版本 → 目标版本，再选染色体、填起止、选链。版本含 GRCz12ab / GRCz12tu / GRCz11 / GRCz10、Zv9–Zv3，以及 NCBI 品系装配 ASM1840007v1(T5D) / ASM3317019v3(Tubingen) / ASM2018471v1(AB) / fDanRer4.1 / fDreABH1.1 / fDreTuH1.2。
- **染色体下拉随来源版本变**：每项带该装配自己的登录号（如 `chr5 · CM124811.1`）。不同装配的 1–25 号是同源染色体，编号相同、序列不同，只能靠登录号区分。
- **算法**：UCSC over.chain，可多跳、碱基级；本地 34 条链覆盖 272 组版本对。
- **批量 ID 互转**：符号 / 别名 / Ensembl / ZFIN / NCBI Gene 互查，一次 ≤200 条。
- **装配对照**：本页结果区还给 GRCz12tu ↔ GRCz11 的逐染色体长度与基因数、本地 BLAST 库状态。
- **别误读**：换算只保证坐标对应，不保证区间还是同一个基因（品系装配有片段级增删），结果会把两端的基因一起给出。""",
        "en": """**Liftover (#/liftover)** — two modes on one page: coordinate liftover and batch ID conversion.

- **Coordinate liftover**: pick source → target assembly, then chromosome, start/end and strand. Assemblies include GRCz12ab / GRCz12tu / GRCz11 / GRCz10, Zv9–Zv3, and NCBI strain assemblies ASM1840007v1(T5D) / ASM3317019v3(Tubingen) / ASM2018471v1(AB) / fDanRer4.1 / fDreABH1.1 / fDreTuH1.2.
- **The chromosome list follows the source assembly**, and each entry carries that assembly's own accession (e.g. `chr5 · CM124811.1`). Chromosomes 1–25 are homologous across assemblies: same number, different sequence, distinguishable only by accession.
- **Method**: UCSC over.chain, multi-hop, base level; 34 chains cover 272 assembly pairs.
- **Batch ID conversion**: symbol / alias / Ensembl / ZFIN / NCBI Gene, up to 200 IDs per run.
- **Caveat**: a lift guarantees the coordinate, not the same gene (strain assemblies have megabase-scale indels), so both genes are reported.""",
    },
    "sequence-region": {
        "title_zh": "位置查序列",
        "title_en": "Sequence by region",
        "route": "#/sequence-region",
        "aliases": r"(位置(查找|查)序列|序列页|取序列|提取序列|启动子|cdna|fasta|按区间|区间序列|sequence\s*(by\s*)?region)",
        "zh": """**位置查序列（#/sequence-region）** —— 两个 tab：按基因 / 按染色体区间。

- **按基因**：填基因名（可批量，最多 20 个，逗号或换行分隔），勾 cDNA / 启动子 2 kb / 基因体，点「提取序列」。cDNA 取 GRCz12tu CDS；启动子与基因体走 GRCz11，上游默认 2 kb。
- **按区间**：填染色体 + 起止 + 链（负链取反向互补），装配可选 GRCz12tu 或 GRCz11；单条 ≤100 kb。
- **批量区间**：展开「批量区间」，每行一条（如 `8:1000-2000`），一次最多 50 条。
- **输出**：FASTA，可直接复制。
- **旧链接**：`#/sequence/<基因名>` 会落在「按基因」并自动回填。""",
        "en": """**Sequence by region (#/sequence-region)** — two tabs: by gene and by chromosome interval.

- **By gene**: enter gene names (batch up to 20, comma or newline separated), tick cDNA / promoter 2 kb / gene body, then “Extract”. cDNA comes from GRCz12tu CDS; promoter and gene body go through GRCz11 with 2 kb upstream by default.
- **By interval**: chromosome + start/end + strand (minus strand returns the reverse complement), assembly GRCz12tu or GRCz11; ≤100 kb per query.
- **Batch intervals**: one per line (e.g. `8:1000-2000`), up to 50 per run.
- **Output**: FASTA, copy-ready.
- **Old links**: `#/sequence/<gene>` opens the “by gene” tab pre-filled.""",
    },
    "expression": {
        "title_zh": "表达",
        "title_en": "Expression",
        "route": "#/expression",
        "aliases": r"(表达页|表达谱|表达查询|表达数据|bgee|expression\s*atlas|按组织)",
        "zh": """**表达（#/expression）** —— 按基因看组织，或按组织看基因。

- **三种模式**：按基因 / 按组织（给该组织的 Top 基因）/ 多基因对比；支持 `#/expression?mode=tissue&tp53` 这类深链。
- **输入**：基因名、别名或 Ensembl ID，多个基因用逗号分隔；按组织模式从下拉里选组织。
- **单位别混**：Bgee 给的是 0–100 的**表达存在性置信分**，不是 TPM；Expression Atlas（E-ERAD-475）给的是 TPM。
- **数据量**：319,344 条表达记录、24,245 个基因、1,151 个组织 / 解剖实体。""",
        "en": """**Expression (#/expression)** — browse by gene, or by tissue.

- **Three modes**: by gene, by tissue (top genes in that tissue), multi-gene comparison; deep links like `#/expression?mode=tissue&tp53` work.
- **Input**: gene name, alias or Ensembl ID; separate multiple genes with commas. Tissue mode picks from a dropdown.
- **Units**: Bgee reports a 0–100 **expression-existence confidence score**, not TPM; Expression Atlas (E-ERAD-475) reports TPM.
- **Scale**: 319,344 expression rows, 24,245 genes, 1,151 tissues / anatomical entities.""",
    },
    "phenotype-gene": {
        "title_zh": "基因查表型",
        "title_en": "Gene → phenotype",
        "route": "#/phenotype-gene",
        "aliases": r"(基因(查|看|找|→)\s*表型|表型页|表型查询|表型数据|表型索引|gene\s*(→|to)\s*phenotype)",
        "zh": """**基因查表型（#/phenotype-gene）** —— 一个基因，看 ZFIN 里记录的相关表型。

- **输入**：基因名，如 sox9b、tbx5a。
- **输出**：该基因对应的表型条目（entity + quality 组合）。
- **口径**：数据源 ZFIN phenoGeneCleanData_fish，171,079 条注释 / 8,029 个基因 / 20,971 个表型组合。
- **方向固定**：本页只做「基因 → 表型」；反方向用导航里的「表型查基因」，不在本页切换。""",
        "en": """**Gene → phenotype (#/phenotype-gene)** — one gene in, the phenotypes recorded in ZFIN out.

- **Input**: a gene name, e.g. sox9b, tbx5a.
- **Output**: phenotype entries for that gene (entity + quality pairs).
- **Source**: ZFIN phenoGeneCleanData_fish — 171,079 annotations, 8,029 genes, 20,971 phenotype pairs.
- **One direction only**: this page is gene → phenotype; use “Phenotype → gene” for the reverse.""",
    },
    "phenotype-term": {
        "title_zh": "表型查基因",
        "title_en": "Phenotype → gene",
        "route": "#/phenotype-term",
        "aliases": r"(表型(查|找|搜|→)\s*基因|表型.{0,4}(反查|找基因)|中文表型|表型词|表型关键词|phenotype\s*(→|to)\s*gene)",
        "zh": """**表型查基因（#/phenotype-term）** —— 给一个表型描述，找有该表型的基因。

- **输入**：表型关键词，中英文都行（如「心脏畸形」或 increased curvature）。
- **中文可查**：中文词会先映射成英文再匹配（心脏畸形 → heart + abnormal），不必自己翻译。
- **输出**：命中基因列表。
- **方向固定**：本页只做「表型 → 基因」；反方向用「基因查表型」。""",
        "en": """**Phenotype → gene (#/phenotype-term)** — give a phenotype description, find genes with it.

- **Input**: phenotype keywords in Chinese or English (e.g. “心脏畸形” or increased curvature).
- **Chinese works**: Chinese terms are mapped to English first (心脏畸形 → heart + abnormal), so no manual translation needed.
- **Output**: matching genes.
- **One direction only**: phenotype → gene; use “Gene → phenotype” for the reverse.""",
    },
    "blast": {
        "title_zh": "BLAST",
        "title_en": "BLAST",
        "route": "#/blast",
        "aliases": r"(blast|序列比对)",
        "zh": """**BLAST（#/blast）** —— 把你的序列比对到斑马鱼核酸库。

- **输入**：一条裸序列，或多条 FASTA（每条以 `>` 开头）；可「从剪贴板粘贴」或导入 .fa / .fasta / .fna / .txt 文件。最短 20 bp、单条最长 50 kb，**一次最多 20 条**。
- **库**：引擎 blastn，实际比对的是 GRCz12tu CDS 库（`grcz12tu_cds.fna`，与「按基因取 cDNA」同源）。本地另有 genome_db（全基因组），但那是 sgRNA / 引物的脱靶筛查在用，BLAST 页不走它，界面也没有选库开关。
- **输出**：命中基因与比对摘要；每条序列默认取前 10 条命中，界面上不提供调整。
- **跨装配回退**默认关闭，需要显式开启。""",
        "en": """**BLAST (#/blast)** — align your sequence against the zebrafish nucleotide library.

- **Input**: one raw sequence or multi-FASTA (each record starts with `>`); paste from clipboard or import .fa / .fasta / .fna / .txt. Minimum 20 bp and at most 50 kb per record, **up to 20 sequences per run**.
- **Library**: blastn against the GRCz12tu CDS library (`grcz12tu_cds.fna`, same source as “cDNA by gene”). A genome_db also exists locally, but it serves sgRNA / primer off-target screening — BLAST does not use it, and there is no library selector in the UI.
- **Output**: hit genes with alignment summaries; 10 hits per query by default, not adjustable in the UI.
- **Cross-assembly fallback** is off unless explicitly enabled.""",
    },
    "go": {
        "title_zh": "GO 富集",
        "title_en": "GO enrichment",
        "route": "#/go",
        "aliases": r"(go\s*富集|富集|enrich)",
        "zh": """**GO 富集（#/go）** —— 一串基因，看哪些 GO 条目显著富集。

- **输入**：逗号分隔的基因列表（如 tp53,hsp70,myl7），支持 `#/go/tp53,hsp70` 直接跑。
- **方法**：ORA —— Fisher 精确检验 + Benjamini–Hochberg 校正；背景集是本地有注释的基因。
- **输出**：GO（BP / MF / CC）与通路的富集表，可点「复制 TSV（发表用）」导出。
- **口径**：GO 注释 113,410 条、基因–通路 36,425 条，都来自本地库。""",
        "en": """**GO enrichment (#/go)** — a gene list in, enriched GO terms out.

- **Input**: comma-separated gene list (e.g. tp53,hsp70,myl7); `#/go/tp53,hsp70` runs directly.
- **Method**: ORA — Fisher's exact test with Benjamini–Hochberg correction; the background is locally annotated genes.
- **Output**: GO (BP / MF / CC) and pathway enrichment tables, exportable via “Copy TSV (for publication)”.
- **Scale**: 113,410 GO annotations and 36,425 gene–pathway rows from the local database.""",
    },
    "kegg": {
        "title_zh": "KEGG 通路",
        "title_en": "KEGG pathways",
        "route": "#/kegg",
        "aliases": r"(kegg|通路浏览器|通路页|pathway\s*browser|通路检索)",
        "zh": """**KEGG 通路（#/kegg）** —— 查斑马鱼通路里有哪些基因。

- **输入**：通路关键词（MAPK、p53、apoptosis）。
- **输出**：命中的通路列表，以及每条通路里的基因。
- **来源要说清**：这条通道是多来源的，结果逐行标注 Reactome / KEGG / WikiPathways，并给对应站点链接；实测关键词检索以 Reactome 条目居多，不是只有 KEGG。本地 36,425 条基因–通路记录。
- **不在本页做**：拿一串基因算显著性富集，那是「GO 富集」页。""",
        "en": """**KEGG pathways (#/kegg)** — see which genes sit in a zebrafish pathway.

- **Input**: a pathway keyword (MAPK, p53, apoptosis).
- **Output**: matching pathways, and the genes inside each one.
- **Sources, stated plainly**: this channel is multi-source — each row is labelled Reactome / KEGG / WikiPathways with a link to the respective site; in practice keyword searches return mostly Reactome entries, not KEGG only. 36,425 gene–pathway rows locally.
- **Not here**: significance testing on a gene list — that is the GO enrichment page.""",
    },
    "sgrna": {
        "title_zh": "sgRNA 设计",
        "title_en": "sgRNA design",
        "route": "#/sgrna",
        "aliases": r"(sgrna|向导\s*rna|grna|cas12a|saCas9|cas9|脱靶|chopchop)",
        "zh": """**sgRNA 设计（#/sgrna）** —— 给基因或靶序列，扫 PAM、打分、查脱靶。

- **输入**：基因名（自动取 CDS），或直接粘 DNA 序列（建议 ≥50 bp，含目标区）。
- **PAM**：SpCas9 · NGG、SpCas9 · NAG（备选）、Cas12a · TTTV、SaCas9 · NNGRRT。候选数默认 20，可调 5–60。
- **打分**：GC、poly-T、二级结构、位置权重四个维度。
- **脱靶**：筛查走本地 cdna_db / genome_db。
- **复核**：结果区可一键跳 CHOPCHOP。""",
        "en": """**sgRNA design (#/sgrna)** — gene or target sequence in; PAM scan, scoring and off-target check out.

- **Input**: a gene name (CDS fetched for you) or a pasted DNA sequence (≥50 bp around the target works best).
- **PAMs**: SpCas9 · NGG, SpCas9 · NAG (alternative), Cas12a · TTTV, SaCas9 · NNGRRT. Candidates default to 20, adjustable 5–60.
- **Scoring**: GC content, poly-T, secondary structure, position weight.
- **Off-targets**: scanned against the local cdna_db / genome_db.
- **Cross-check**: a one-click jump to CHOPCHOP from the results.""",
    },
    "primer": {
        "title_zh": "引物设计",
        "title_en": "Primer design",
        "route": "#/primer",
        "aliases": r"(引物|primer)",
        "zh": """**引物设计（#/primer）** —— 给模板，按产物长度与特异性出引物对。

- **输入**：基因名，或粘贴模板序列（模板 ≥120 bp 更准）。
- **参数**：产物长度默认 80–300 bp，可调 60–1000 / 100–3000。
- **特异性**：转录组（快）或全基因组（更全、较慢）。
- **输出**：引物对及 Tm、GC、二聚体检查。""",
        "en": """**Primer design (#/primer)** — give a template, get primer pairs by amplicon size and specificity.

- **Input**: a gene name, or a pasted template (≥120 bp works better).
- **Parameters**: amplicon 80–300 bp by default, adjustable to 60–1000 / 100–3000.
- **Specificity**: transcriptome (fast) or whole genome (more thorough, slower).
- **Output**: primer pairs with Tm, GC and dimer checks.""",
    },
    "id": {
        "title_zh": "跨库 ID 映射",
        "title_en": "ID mapping",
        "route": "#/id",
        "aliases": r"(跨库\s*id|id\s*(映射|转换|解析)|id\s*resolve|id\s*mapping|跨库)",
        "zh": """**跨库 ID 映射（#/id）** —— 符号、ZFIN、Entrez、Ensembl、UniProt 之间互转。

- **入口**：已从 Tools 菜单移出，路由保留，旧书签和 `#/id/<ID>` 直达仍可用。
- **两种模式**：跨库（目标库选 ZFIN / NCBI Gene / Ensembl / UniProt / 符号 GRCz12tu 或 GRCz11）；跨组装（来源组装可「自动识别」，目标组装必选）。
- **输入**：单个或批量 ID，批量每行一个。
- **消歧三态**：唯一命中 / 一对多（列候选）/ 未命中。
- **口径**：装配映射表 22,721 条。""",
        "en": """**ID mapping (#/id)** — convert between symbol, ZFIN, Entrez, Ensembl and UniProt.

- **Entry**: removed from the Tools menu but the route stays live; old bookmarks and `#/id/<ID>` still work.
- **Two modes**: cross-database (target: ZFIN / NCBI Gene / Ensembl / UniProt / symbol in GRCz12tu or GRCz11) and cross-assembly (source can be auto-detected, target required).
- **Input**: single or batch IDs, one per line.
- **Three states**: unique hit / one-to-many (candidates listed) / not found.
- **Scale**: 22,721 cross-assembly mapping rows.""",
    },
    "annotate": {
        "title_zh": "一键注释",
        "title_en": "One-click annotation",
        "route": "#/annotate",
        "aliases": r"(一键注释|注释页|annotate)",
        "zh": """**一键注释（#/annotate）** —— 一个基因，把各层信息汇总成一页。

- **输入**：基因符号。
- **输出**：身份、表达、GO、通路、同源、文献六块的汇总。
- **缺失处理**：没有记录就写「无记录」，不填推测值。""",
        "en": """**One-click annotation (#/annotate)** — one gene, every layer on one page.

- **Input**: a gene symbol.
- **Output**: identity, expression, GO, pathways, orthologs and literature in one summary.
- **Missing values**: shown as “no record”, never filled with guesses.""",
    },
    "upload": {
        "title_zh": "文献入库",
        "title_en": "Upload literature",
        "route": "#/upload",
        "aliases": r"(文献入库|上传文献|入库页|upload)",
        "zh": """**文献入库（#/upload）** —— 上传自己的文献，进入本地知识库。

- **入口**：已从 Tools 菜单移出，路由保留。
- **输入**：本地文件，PDF / TXT / MD，单文件 ≤5 MB（UTF-8）。
- **切分**：360 / 110 字符，重叠 48。
- **生效前提**：入库后要重建向量分片，检索才会命中这些新文献。""",
        "en": """**Upload literature (#/upload)** — add your own papers to the local library.

- **Entry**: removed from the Tools menu; the route remains.
- **Input**: PDF / TXT / MD, ≤5 MB per file (UTF-8).
- **Chunking**: 360 / 110 characters, 48 overlap.
- **Before it takes effect**: rebuild the vector shards, otherwise retrieval won't hit the new papers.""",
    },
    "about": {
        "title_zh": "平台说明",
        "title_en": "About",
        "route": "#/about",
        "aliases": r"(平台说明|关于本平台|关于这个?平台|引用方式|数据来源|about)",
        "zh": """**平台说明（#/about）** —— 这个平台做什么、数据从哪来、怎么引用。

- **三个章节**用页内 tab 切换：定位与范围 / 数据来源与算法 / 引用方式。
- **数据快照**：2026-10-15。
- **数据源**：Ensembl、NCBI、ZFIN、Bgee、QuickGO、KEGG。
- 首页左栏另有「引用本平台」折叠块，给出可复制的引用文本。""",
        "en": """**About (#/about)** — what this platform does, where the data comes from, how to cite it.

- **Three sections** as in-page tabs: scope, data sources and algorithms, citation.
- **Snapshot**: 2026-10-15.
- **Sources**: Ensembl, NCBI, ZFIN, Bgee, QuickGO, KEGG.
- The home page also has a collapsible “Cite this platform” block with copy-ready text.""",
    },
    "news": {
        "title_zh": "News",
        "title_en": "News",
        "route": "#/news",
        "aliases": r"(news|新闻页)",
        "zh": """**News（#/news）** —— 没有独立功能页，点击后落在首页。

- 顶部 News 与首页是同一个视图：每日研究信息流 + 文献更新记录 + 引用信息；导航仍高亮 News，旧书签 `#/news` 不会 404。
- **左栏「每日斑马鱼研究」**：选日期看当天论文；下方折叠区是文献更新记录（每周增量）与引用信息。""",
        "en": """**News (#/news)** — there is no separate page; it lands on the home view.

- News and home are the same view: the daily research feed, literature update log and citation info. The nav still highlights News and old `#/news` bookmarks don't 404.
- **Left column “Daily zebrafish research”**: pick a date to read that day's papers; the collapsible block below holds the weekly literature update log and citation text.""",
    },
    "ops": {
        "title_zh": "运营看板",
        "title_en": "Dashboard",
        "route": "#/ops",
        "aliases": r"(运营看板|运维|看板)",
        "zh": """**运营看板（#/ops）** —— 定时任务状态与访问量。

- **入口**：已从顶层导航移出，路由保留（`#/ops`）。
- **两个按钮**：刷新状态 / 立即重算统计。
- **内容**：定时任务（文献周更、图文库、统计、守护）与访问量聚合。
- **隐私口径**：只给计数，不返回、不展示任何 IP 明细。""",
        "en": """**Dashboard (#/ops)** — cron status and traffic.

- **Entry**: removed from the top nav; the route remains (`#/ops`).
- **Two buttons**: refresh status / recompute stats now.
- **Content**: scheduled jobs (weekly literature, gallery, stats, guard) and aggregated traffic.
- **Privacy**: counts only — no IP details are returned or shown.""",
    },
}

# 兜底：没命中具体页面时给功能地图，而不是一句含糊的「右侧页签」。
OVERVIEW_ZH = """这是本平台的功能地图（都从顶部导航进），说明用法不跑文献检索：

**Chat** —— 唯一问答入口；右侧「回答详情」里有 9 个工具页签。

**Omics › Genome**
- **Liftover**：坐标换算（UCSC over.chain，可多跳）与批量 ID 互转（≤200 条）两个模式。
- **位置查序列**：按基因取 cDNA / 启动子 / 基因体，或按染色体区间取，批量 ≤50 条。

**Omics › Gene**
- **别名**：给名称（符号 / 别名 / 旧名 / 跨库 ID）确认是哪个基因。
- **位置查找**：给基因名，出坐标（GRCz11）、链与基因体。
- **表达**：按基因 / 按组织 / 多基因对比三种模式。

**Omics › Phenotype**
- **基因查表型** 与 **表型查基因**：方向各一个入口，中文表型词可直接查。

**Tools**
- **BLAST**（blastn，一次 ≤20 条）、**GO 富集**（Fisher + BH，可导 TSV）、**KEGG 通路**、**sgRNA 设计**（4 种 PAM）、**引物设计**（产物长度可调）。

**其他**
- **News** 落在首页；**平台说明** 三章：定位与范围 / 数据来源 / 引用方式。

问具体某一页怎么用，直接说页面名就行，例如「Liftover 页怎么用」「表达页能按组织查吗」。"""

OVERVIEW_EN = """Here is the platform map (all under the top navigation); this is usage help, no literature search:

**Chat** — the single question entry point; “Answer details” on the right has 9 tool tabs.

**Omics › Genome**
- **Liftover**: coordinate liftover (UCSC over.chain, multi-hop) and batch ID conversion (≤200 IDs).
- **Sequence by region**: cDNA / promoter / gene body by gene, or by chromosome interval; batch up to 50.

**Omics › Gene**
- **Aliases**: give a name (symbol / alias / former name / cross-DB ID) and confirm which gene it is.
- **Gene locus**: gene name in, coordinates (GRCz11), strand and gene body out.
- **Expression**: by gene, by tissue, or multi-gene comparison.

**Omics › Phenotype**
- **Gene → phenotype** and **phenotype → gene**: one entry per direction; Chinese phenotype terms work directly.

**Tools**
- **BLAST** (blastn, ≤20 sequences), **GO enrichment** (Fisher + BH, TSV export), **KEGG pathways**, **sgRNA design** (4 PAMs), **primer design** (adjustable amplicon).

**Other**
- **News** lands on the home view; **About** has three sections: scope / sources / citation.

Ask about a page by name, e.g. “how do I use the Liftover page” or “can the expression page search by tissue”."""
