/* Zebrafish Hub · 中英互译（默认英文，顶栏「中 / EN」按钮切回中文）
 *
 * 设计要点
 *  - 不侵入业务代码：本文件只做「运行时字典替换」，HTML 文本 / placeholder / title / aria-label
 *  - 保护区域：聊天内容、Markdown 正文、代码块、工具结果（不翻译，避免动到用户数据/答案）
 *  - 默认英文：localStorage.hub_lang 为空时按 en 渲染；用户切换后记住选择
 *  - 动态渲染：MutationObserver 覆盖 JS 之后插入的 UI 文案（状态栏、按钮、提示等）
 */
(function () {
  "use strict";

  var ZH2EN = {
    /* ── 聊天页：状态 / 队列 / 错误 / 侧栏（2026-10-11 补：英文模式下仍有中文）── */
    "部分回答": "Partial answer",
    "整体回答": "Full answer",
    "正在思考…": "Thinking…",
    "正在检索知识库并推理（CPU 约 30~120 秒，请耐心等待）…":
      "Searching the knowledge base and reasoning (CPU, ~30–120 s)…",
    "正在查询基因库（通常数秒内）…": "Looking up the gene DB (usually a few seconds)…",
    "正在生成 SQL 并查询（约 30~90 秒）…": "Generating SQL and querying (~30–90 s)…",
    "多模型融合推理中（CPU 约 60~180 秒）…": "Multi-model fusion reasoning (CPU, ~60–180 s)…",
    "检索调试摘要": "Retrieval debug summary",
    "今天": "Today",
    "昨天": "Yesterday",
    "近 7 天": "Last 7 days",
    "近 30 天": "Last 30 days",
    "更早": "Earlier",
    "无匹配聊天": "No matching chats",
    "重命名": "Rename",
    "删除": "Delete",
    "重命名对话": "Rename chat",
    "删除这个对话？": "Delete this chat?",
    "确认删除": "Confirm delete",
    "已删除对话": "Chat deleted",
    "保存": "Save",
    "取消": "Cancel",
    "复制": "Copy",
    "重新生成": "Regenerate",
    "清空": "Clear",
    "导出 Markdown": "Export Markdown",
    "导出 JSON": "Export JSON",
    "复制全文": "Copy all",
    "游客会话仅存本机；分享请导出文件":
      "Guest sessions stay on this device; export a file to share",
    "服务暂时无响应，请刷新页面或稍候再试。":
      "The service is temporarily unresponsive — refresh the page or try again shortly.",
    "服务暂时繁忙，请稍候再试": "The service is busy — please try again shortly.",
    "服务返回了网页而非常规接口响应，通常是网关错误页或服务未启动":
      "the server returned a web page instead of an API response (usually a gateway error page, or the service is not running)",
    "请求失败": "Request failed",
    "处理出错": "Processing error",
    "未生成有效回答": "No valid answer was generated",
    "排队已满（最多 8 个），请等当前回答完成后再发。":
      "Queue is full (max 8) — wait for the current answer to finish.",
    "请求失败：无法连接服务（可能仍在加载或刚重启）。请稍后重试。":
      "Request failed: cannot reach the service (it may still be loading, or just restarted). Try again later.",
    "请求失败：服务端未返回具体原因，请重试；细节见右侧「回答详情」。":
      "Request failed: the server gave no details — retry; see “Answer details” on the right.",
    "核心模型仍在预热，请稍候再发送（状态栏显示「仍在加载」且发送按钮灰掉时）。":
      "Core models are still warming up — wait until the status bar shows ready before sending.",
    "预热中请稍候：服务正在加载文献 FAISS，暂时无法连接。请等状态栏预热结束后再问。":
      "Warming up: the literature FAISS index is still loading. Wait until the status bar is ready.",
    /* ── 页面标题 ── */
    "Zebrafish Hub · 斑马鱼基因知识中枢": "Zebrafish Hub · Zebrafish Knowledge Hub",

    /* ── 顶栏 / 导航 / 侧栏 ── */
    "首页": "Home",
    "智能问答": "Chat",
    "问答": "Chat",
    "基因": "Genes",
    "富集": "Enrichment",
    "通路": "Pathways",
    "ID映射": "ID Map",
    "ID 映射": "ID Mapping",
    "序列": "Sequence",
    "文献": "Literature",
    "注释": "Annotation",
    "入库": "Upload",
    "回到首页": "Back to home",
    "历史问答": "Chat history",
    "打开历史": "Open history",
    "切换主题": "Toggle theme",
    "检测中...": "Checking...",
    "访客": "Guest",
    "登录": "Sign in",
    "注册": "Sign up",
    "退出": "Sign out",
    "已登录": "Signed in",
    "主导航": "Main navigation",
    "模块快捷入口": "Module shortcuts",
    "智能体": "Agent",
    "关于": "About",

    /* ── 首页主视觉 ── */
    "从基因身份到文献证据": "From gene identity to literature evidence",
    "本地注释、跨库映射与 PubmedYes 精排，汇于同一中枢——答案可追溯到本库与原站。":
      "Local annotation, cross-database mapping and PubmedYes reranking in one hub — every answer traces back to this library or the source site.",
    "例如 tp53、缺氧转录、ZDB-GENE-…": "e.g. tp53, hypoxia transcription, ZDB-GENE-…",
    "搜索范围": "Search scope",
    "搜索基因、文献或直接提问": "Search genes, literature, or ask a question",
    "更强大的数据探索，请用": "For deeper data exploration, use",
    "搜索": "Search",
    "进入智能问答": "Open Chat",
    "基因工作台": "Gene Workbench",

    /* ── 6.png 改版：导航与 Genes / Tools 新入口 ── */
    "即将上线": "Coming soon",
    /* ── 使用说明页入口（首页 CTA 下方 / 欢迎弹窗底部共用） ── */
    "使用说明": "Guide",
    "使用协议": "Terms of use",
    "数据来源": "Data sources",
    /* 功能页精简后仅剩的两条口径提示 */
    "单条 ≤100 kb；默认 GRCz12tu，与 BLAST 同版。": "Single interval ≤100 kb; GRCz12tu by default, the same assembly as BLAST.",
    "Bgee 分与 Atlas TPM 不可横向比较。": "Bgee scores and Atlas TPM are not comparable.",
    /* 导航（index.html 里这些标签是英文原稿，这里补「中文 → 英文」，
       反向表 EN2ZH 会自动生成，中文模式下才能显示中文） */
    "看板": "Dashboard",
    "基因组": "Genomes",
    /* 2026-10-27 导航重组：顶层 Genomes → Genomics，下拉里 Liftover + Genes / Expression 子工具栏。
       分组标题在 HTML 里写的是英文 "Genes" / "Expression"；"Genes" 的中文由上面已有的
       「基因 → Genes」反向得到，"Expression" 由「表达 → Expression」反向得到，这里只补 Genomics。 */
    "基因组学": "Genomics",
    "基因库": "Gene library",
    "表达": "Expression",
    "表型": "Phenotype",
    "运维": "Operations",
    "工具": "Tools",
    "会话": "Chats",
    "工作台": "Workbench",
    "本地库": "Local library",
    "基因详情": "Gene detail",
    "数据库查询": "Database query",
    "知识库问答": "Knowledge-base Q&A",
    "多模型融合": "Multi-model fusion",
    /* 表达/表型菜单里原本漏翻的 3 项（2026-10-15 补：英文模式下会露出中文） */
    "多基因表达对比": "Multi-gene comparison",
    "≥2 个基因 × 共同覆盖组织": "≥2 genes × shared tissues",
    "表型术语排行": "Phenotype term ranking",
    "常用 PATO / ZFA · 覆盖基因数": "common PATO / ZFA · genes covered",
    "按基因看表型": "Phenotypes by gene",
    "分期 · 基因型 · ZDB-PUB": "stage · genotype · ZDB-PUB",
    "引用方式": "Cite Us",
    /* 下拉菜单里的一行小字：漏译会让 EN 模式出现「Genome comparison + 中文说明」的中英混排 */
    "装配对照": "Assembly comparison",
    /* 2026-10-28 导航重组：Genomics → Omics（Genome / Gene / Phenotype 三区），
       文献台改名 News。菜单项标题是小字里唯一的短词，必须能整串命中，
       否则英文界面会出现「Omics 下拉里全是中文」。 */
    "位置查序列": "Sequence by region",
    "位置查找序列": "Sequence by region",
    "位置查找": "Gene locus",
    "基因位置查找": "Gene locus",
    "基因别名": "Gene aliases",
    "基因查表型": "Gene → phenotype",
    "表型查基因": "Phenotype → gene",
    "GO 富集": "GO enrichment",
    "按染色体区间取序列（基因组层面）": "by chromosome interval (genome-level)",
    "按基因查坐标、基因体与序列": "coordinates, gene body and sequence",
    "给基因，看关联表型": "gene in, phenotypes out",
    "给表型，找候选基因": "phenotype in, genes out",
    "GRCz11/10 · Zv9/8 → GRCz12tu，碱基级": "GRCz11/10 · Zv9/8 → GRCz12tu, base-pair level",
    "按基因 · 按组织 · 多基因对比": "by gene · by tissue · multi-gene",
    "产物长度 · 模板特异性": "amplicon size · template specificity",
    "PAM 扫描 · 打分 · 脱靶": "PAM scan · scoring · off-target",
    "GRCz11 ↔ GRCz12tu · 逐染色体长度（不做 liftover）":
      "GRCz11 ↔ GRCz12tu · per-chromosome lengths (no liftover)",
    "GRCz11 ↔ GRCz12tu · 逐染色体长度": "GRCz11 ↔ GRCz12tu · length per chromosome",
    "blastn · 单条或批量 FASTA": "blastn · single or batch FASTA",
    "按基因 / 按染色体区间": "by gene / by chromosome region",
    "已经在新的对话里（输入框已清空）": "Already in a new chat (input cleared)",
    /* 导航菜单里新增的真实入口（2026-10-15：原来「基因组/表达/表型」菜单里只有"即将上线"，点了不跳转） */
    "GRCz12tu 基因组 / 转录组库": "GRCz12tu genome / transcriptome DB",
    "序列提取": "Sequence extraction",
    "cDNA / 启动子 / 基因组区": "cDNA / promoter / genomic region",
    "基因表达": "Gene expression",
    "批量检索基因": "Bulk gene search",
    "符号 / 别名 / 跨库 ID": "Symbol / alias / cross-DB IDs",
    "表型相关文献检索": "Phenotype-related literature search",
    "基因功能注释": "Gene function annotation",
    "GO / 通路汇总": "GO / pathway summary",
    "突变体表型库": "Mutant phenotype atlas",
    "组织 × 时期表达热图": "Tissue × stage expression heatmap",
    "脱靶范围：转录组（快，GRCz12tu CDS）": "Off-target scope: transcriptome (fast, GRCz12tu CDS)",
    "脱靶范围：全基因组（更全，较慢）": "Off-target scope: whole genome (broader, slower)",
    "特异性：转录组（快）": "Specificity: transcriptome (fast)",
    "特异性：全基因组（更全，较慢）": "Specificity: whole genome (broader, slower)",
    "脱靶筛查范围": "Off-target scope",
    "引物特异性筛查范围": "Primer specificity scope",
    "选择日期（查看当天的每日论文）": "Pick a date to view that day's selection",
    "同候选池内按日期确定性选取": "Deterministic pick inside the same candidate pool",
    "关于本平台": "About this platform",
    // 2026-10-31：顶部 About 下拉三合一，入口统一改叫「平台说明」
    "平台说明": "About the platform",
    "数据来源与算法": "Data sources & algorithms",
    "引用方式": "How to cite",
    // 2026-10-28：关于下拉新增的第三条入口（原先是首页右卡底部那行链接）
    "使用说明 · 使用协议 · 数据来源": "Guide · Terms · Data sources",
    "口径 · 条款 · 出处": "Method · terms · sources",
    "定位与范围": "Scope & positioning",
    "本库 / ZFIN / Ensembl / NCBI": "Local library / ZFIN / Ensembl / NCBI",
    "Cite Us": "Cite Us",
    "本地引擎": "Local engine",
    "可跳 CHOPCHOP": "Jump to CHOPCHOP",
    "基因组浏览器": "Genome browser",
    "组装与坐标总览": "Assemblies & coordinates",
    "基因库搜索": "Gene library",
    "基因搜索词": "Gene search term",
    "Cas 酶 / PAM": "Cas enzyme / PAM",
    "候选数": "Candidates",
    "SpCas9 · NAG（备选 PAM）": "SpCas9 · NAG (alternative PAM)",
    "Cas12a · TTTV（T7 体外转录常用）": "Cas12a · TTTV (common for T7 IVT)",
    "SaCas9 · NNGRRT": "SaCas9 · NNGRRT",
    "最小产物长度": "Min amplicon",
    "最大产物长度": "Max amplicon",
    "符号 / 别名 / ZFIN / Ensembl": "Symbol / alias / ZFIN / Ensembl",
    "表达 · GO · 同源": "Expression · GO · orthologs",
    "组织 × 时期表达热图": "Tissue × stage expression heatmap",
    "表达谱比对": "Expression profile comparison",
    "突变体表型库": "Mutant phenotype atlas",
    "表型–基因关联": "Phenotype–gene links",
    "sgRNA / 引物设计": "sgRNA / primer design",
    "PAM 扫描 · 打分 · 脱靶": "PAM scan · scoring · off-target",
    "PAM 扫描 · 双链打分 · 转录组特异性": "PAM scan · dual-strand scoring · transcriptome specificity",
    "本地注释 32,564 基因 · 表达 319,344 行 / 24,245 基因 · ZFIN 表型 171,079 条 · 文献 32,549 篇 · 染色体 25 条 / 1.45 Gb":
      "32,564 annotated genes · 319,344 expression rows / 24,245 genes · 171,079 ZFIN phenotype annotations · 32,549 papers · 25 chromosomes / 1.45 Gb",
    "本地注释 26,321 基因 · 表达 319,344 行 / 24,245 基因 · ZFIN 表型 171,079 条 · 文献 32,863 篇 · 染色体 25 条 / 1.45 Gb":
      "32,564 annotated genes · 319,344 expression rows / 24,245 genes · 171,079 ZFIN phenotype annotations · 32,549 papers · 25 chromosomes / 1.45 Gb",
    "32,564 genes · 符号 / 别名 / 跨库 ID": "32,564 genes · symbol / alias / cross-DB ID",
    "GRCz12tu 符号": "GRCz12tu symbol",
    "覆盖本地基因库全部基因：标准符号、ZFIN/NCBI/UniProt 别名、Entrez / Ensembl ID 均可检索，点条目查看基因卡。":
      "Covers every gene in the local library: standard symbol, ZFIN/NCBI/UniProt aliases and Entrez/Ensembl IDs are searchable; click a row for the gene card.",
    "输入关键词，或点「浏览全部」": "Type a keyword, or click Browse all",
    "等待检索": "Waiting for a search",
    "搜 p53、tp53、ZDB-GENE-990415-270 或 Ensembl ID；列表在右侧，点一行出基因卡。":
      "Search p53, tp53, ZDB-GENE-990415-270 or an Ensembl ID; the list appears on the right, click a row for the gene card.",
    "符号锚定 GRCz12tu · 别名与同源来自本地库": "Symbols follow GRCz12tu · aliases and orthologs come from the local library",
    "本地引擎按 CHOPCHOP / CRISPRscan 的通用规则做 PAM 枚举与打分，并提供一键跳转对标；引物按 Tm / GC / 二级结构规则设计。":
      "The local engine enumerates PAMs and scores guides following the general practice of CHOPCHOP / CRISPRscan, with one-click cross-checks; primers follow Tm / GC / secondary-structure rules.",
    "基因名或粘贴序列": "Gene name or pasted sequence",
    "等待设计": "Waiting for a design",
    "sgRNA：输入基因名或序列 → 扫 PAM 并打分；引物：给模板序列或基因名。":
      "sgRNA: enter a gene name or sequence → scan PAMs and score; primers: provide a template sequence or a gene name.",
    "打分为本地透明规则实现 · 权威评分请跳 CHOPCHOP / CRISPRScan 复核":
      "Scores come from transparent local rules · cross-check with CHOPCHOP / CRISPRScan",
    "覆盖 32,564 个基因：标准符号、ZFIN/NCBI/UniProt 别名、Entrez / Ensembl ID 均可命中；点结果行看基因卡（含别名、同源与复制对）。":
      "Search 32,564 genes by standard symbol, ZFIN/NCBI/UniProt alias or Entrez/Ensembl ID; click a row for the gene card (aliases, orthologs and duplicates included).",
    "符号 / 别名 / ZFIN / Ensembl，如 p53、ZDB-GENE-990415-270": "Symbol / alias / ZFIN / Ensembl, e.g. p53, ZDB-GENE-990415-270",
    "浏览全部": "Browse all",
    "sgRNA 设计": "sgRNA design",
    "引物设计": "Primer design",
    "扫 PAM 并打分": "Scan PAM & score",
    "设计引物": "Design primers",
    "sgRNA 靶点输入": "sgRNA target input",
    "引物模板输入": "Primer template input",
    "基因名（如 sox9b）或粘贴 DNA 序列（建议 ≥50 bp，含目标区）":
      "Gene name (e.g. sox9b) or paste a DNA sequence (≥50 bp including the target region)",
    "基因名（如 tp53）或粘贴模板序列（≥120 bp）": "Gene name (e.g. tp53) or paste a template sequence (≥120 bp)",
    "本地引擎：双链 PAM 枚举 → GC / 多 T / 二级结构 / 位置权重打分 → 转录组（GRCz12tu CDS）特异性命中计数；权威打分可一键跳 CHOPCHOP / CRISPRscan。":
      "Local engine: dual-strand PAM enumeration → GC / poly-T / secondary-structure / position weighting → off-target scan (transcriptome or whole genome); jump to CHOPCHOP / CRISPRscan for authoritative scores.",
    "本地引擎：双链 PAM 枚举 → GC / 多 T / 二级结构 / 位置权重打分 → 脱靶筛查（可选转录组 / 全基因组）；权威打分可一键跳 CHOPCHOP / CRISPRscan。":
      "Local engine: dual-strand PAM enumeration → GC / poly-T / secondary-structure / position weighting → off-target scan (transcriptome or whole genome); jump to CHOPCHOP / CRISPRscan for authoritative scores.",
    "规则式引物设计：长度 18～25 nt · Tm 差 ≤2 ℃ · GC 40～70% · 无发夹/二聚体 · 3′ 端稳定；可选加 T7/SP6 启动子尾用于体外转录。":
      "Rule-based primer design: 18–25 nt, ΔTm ≤ 2 °C, GC 40–70%, no hairpin/dimer, stable 3′ end; optional T7/SP6 promoter tails for in-vitro transcription.",

    /* ── 实时库规模 ── */
    "实时库规模": "Live library stats",
    "库规模实时来自 /api/hub/stats": "Live counts from /api/hub/stats",
    "本地基因": "Local genes",
    "本地文献": "Local papers",
    "GO 注释": "GO annotations",
    "KEGG 通路": "KEGG pathways",
    "表达记录": "Expression records",
    "GRCz12tu 映射": "GRCz12tu mappings",
    "跨库 xref": "Cross-DB xrefs",
    "组装锚定": "Assembly anchors",

    /* ── 功能模块卡片 ── */
    "功能模块": "Workbenches",
    "功能主视觉": "Feature hero",
    "功能页": "Feature page",
    "一功能一界面 · 卡片影像随研究图流同步轮播":
      "One page per tool · card imagery synced with the research feed",
    "Hub 多 Agent · 文献精排 · 实体双链": "Multi-agent hub · literature reranking · entity links",
    "卡片 · 表达 · GO · 同源": "Cards · expression · GO · orthologs",
    "GRCz12tu RefSeq CDS": "GRCz12tu RefSeq CDS",
    "GO/KEGG 富集": "GO/KEGG Enrichment",
    "GO 富集": "GO enrichment",
    "ORA · Fisher + BH": "ORA · Fisher + BH",
    "通路浏览器": "Pathway Browser",
    "KEGG / WikiPathways": "KEGG / WikiPathways",
    "ID 映射": "ID Mapping",
    "跨库 · 三态消歧": "Cross-DB · tri-state disambiguation",
    "序列提取": "Sequence Extraction",
    "cDNA · 启动子 · 基因组区": "cDNA · promoter · genomic region",
    "一键注释": "One-click Annotation",
    "本地库整合视图": "Integrated local-library view",
    "文献检索台": "Literature Search",
    "粗排 500 · 精排 100": "Retrieve 500 · rerank 100",
    "PDF / 文本进 RAG": "PDF / text into RAG",

    /* ── 影像流 ── */
    "斑马鱼研究影像流": "Research Imagery",
    "混合": "Mix",
    "最新": "Latest",
    "热度": "Hot",
    "科研顶流": "Top research",
    "刷新": "Refresh",
    "影像马赛克": "Mosaic",
    "图源：开放许可 · 本地缓存 + 实时聚合（iNaturalist / Commons / Openverse）":
      "Sources: open licenses · local cache + live aggregation (iNaturalist / Commons / Openverse)",

    /* ── 问答页 ── */
    "斑马鱼智能体": "Zebrafish Agent",
    "我是斑马鱼研究助手，Hello!": "I'm the Zebrafish Agent Assistant, Hello!",
    "Zebrafish AI 助手": "Zebrafish AI Assistant",
    "搜索基因、跑 BLAST、读文献精排；答案自动挂本库与 ZFIN / Ensembl / NCBI 等组学库双链（资料库 ≠ 仅 PubMed）。":
      "Search genes, run BLAST, read reranked literature; every answer links back to this library and to ZFIN / Ensembl / NCBI (this is a knowledge base, not just PubMed).",
    "tp53 功能": "tp53 function",
    "tp53 转录机制": "tp53 transcription mechanism",
    "本地库规模": "Library size",
    "跨库映射": "Cross-DB mapping",
    "组学库映射": "Omics database mapping",
    "请输入你的问题吧！": "Type your question here!",
    "发送": "Send",
    "工具": "Tools",
    "＋ 新对话": "＋ New chat",
    "新对话": "New chat",
    "Hub 多Agent": "Hub multi-agent",
    "基因详情": "Gene detail",
    "数据库查询": "Database query",
    "知识库问答": "Knowledge-base Q&A",
    "多模型融合": "Multi-model fusion",
    "搜索聊天…": "Search chats…",
    "发送第一条消息后 会出现在这里": "Your first message will appear here",
    "访客仅本机保存；登录后同步到云端，按账号专属隔离":
      "Guest chats stay on this device; sign in to sync to your own cloud space.",
    "访客仅本机 · 登录后云端同步": "Guest: this device · Signed in: your cloud",
    "回答详情": "Answer detail",
    "上一问处理中（可排队）": "Previous question is processing (you can queue)",
    "处理中…可继续排队": "Processing… you can keep queueing",
    "提问": "Ask",

    /* ── 悬停提示 / 无障碍标签（2026-10-18：用户反馈英文模式下仍出现中文提示） ──
       这些串大多是用 .title / aria-label 动态写上去的，文本节点扫描看不到，
       但 i18n 的 ATTRS 覆盖 title/placeholder/aria-label/alt，只要有词条就会翻。 */
    "文献库预热中，问答与工具均可正常使用":
      "Literature library is warming up — Q&A and tools are available",
    "预热中请稍候": "Warming up, please wait",
    "展开右侧面板": "Expand right panel",
    "折叠右侧面板": "Collapse right panel",
    "反馈": "Feedback",
    "组织": "Tissue",

    /* ── 文献链接标签（英文模式下漏出中文，2026-10-18 全站扫描发现） ── */
    "原文": "Source",
    "PubMed 原文 →": "PubMed source →",
    "本库检索 →": "Search in this library →",
    "查看原文": "View source",

    /* ── 状态栏 ── */
    "仍在加载…": "Still loading…",
    "加载完毕": "Ready",
    "可提问": "Ready to ask",
    "文献暂不可用·其它可问": "Literature unavailable · other queries OK",
    "加载失败": "Load failed",
    "处理中": "Processing",
    "核心模型仍在预热，请稍候再发送（状态栏显示「仍在加载」且发送按钮灰掉时）。":
      "Core models are still warming up — please wait until the status bar shows Ready.",

    /* ── 登录 / 账号 ── */
    "斑马鱼基因知识中枢": "Zebrafish Knowledge Hub",
    "可选注册登录；也可直接快速入门，无需账号即可使用全部功能。":
      "Sign-in is optional — use Quick start to access everything without an account.",
    "快速入门": "Quick start",
    "快速入门可体验问答与工具；登录后对话云端专属、入库按账号隔离。":
      "Quick start gives you Q&A and tools; signing in stores chats in your own cloud space and isolates uploads per account.",
    "密码（6～72 位）": "Password (6–72 characters)",
    "再次输入密码": "Confirm password",
    "两次输入的密码不一致": "Passwords do not match",
    "密码需 6～72 位": "Password must be 6–72 characters",
    "已有账号？去登录": "Already have an account? Sign in",
    /* 2026-10-18 用户反馈「英文模式下出现了中文」：欢迎门的这几个串只在 app.js 里
       用 textContent 硬写中文，词典里没有对词条 → EN 模式下漏成中文。 */
    "注册并进入": "Create account & enter",
    "用户名需 2～32 位（字母、数字、下划线或中文）":
      "Username must be 2–32 characters (letters, digits, underscore or Chinese)",
    /* 后端 api/hub_accounts.py 直接抛出的中文，前端原样显示 → 必须逐条覆盖 */
    "用户名或密码错误": "Incorrect username or password",
    "用户名已被注册": "Username already taken",
    "用户名需 2～32 位（字母/数字/下划线/中文）":
      "Username must be 2–32 characters (letters, digits, underscore or Chinese)",
    "对话数据过大，请删除部分历史后再同步":
      "Chat data is too large — delete some history before syncing",
    "请检查用户名（2～32 位）和密码（6～72 位）":
      "Check the username (2–32 characters) and password (6–72 characters)",
    "提交中…": "Submitting…",
    "用户名（2～32 位）": "Username (2–32 characters)",
    "显示名（选填）": "Display name (optional)",
    /* 浏览器标签页标题：整串做键，translateString 只替换整串匹配 */
    "Zebrafish Agent · 斑马鱼智能研究助手":
      "Zebrafish Agent · AI Research Assistant",
    "尝试过于频繁，请稍后再试": "Too many attempts, please try again later",
    "当前为公开演示：无需访问令牌": "Public demo: no access token required",
    "当前令牌为只读，无法上传（需 admin/upload 令牌）":
      "Read-only token cannot upload (admin/upload token required)",
    "只读令牌无法上传文件": "Read-only token cannot upload files",
    "公网/受保护模式：请输入访问令牌后继续使用":
      "Protected mode: enter the access token to continue",
    "入库需先登录账号（保护专属知识库）。请点右上角登录/注册。":
      "Sign in to upload (your library is private). Use Sign in / Sign up at the top right.",

    /* ── 工具页通用 ── */
    "填写参数后运行": "Fill in the parameters, then run",
    "在左侧提交后，结果会呈现在这里。": "Results appear here after you submit on the left.",
    "发送问题后显示路由、SQL、检索片段与基因卡片。":
      "After you send a question, routing, SQL, retrieved snippets and gene cards appear here.",
    "基因名，如 tp53": "Gene name, e.g. tp53",
    "基因名，如 tp53、sox9b": "Gene names, e.g. tp53, sox9b",
    "基因名/Ensembl/ZFIN / GRCz12tu 标准名": "Gene / Ensembl / ZFIN / GRCz12tu canonical symbol",
    "基因列表，逗号分隔，如 tp53,hsp70,myl7": "Gene list, comma-separated, e.g. tp53,hsp70,myl7",
    "单个：p53；多个用顿号/逗号分隔": "Single: p53; multiple separated by commas",
    "关键词搜索基因": "Search genes by keyword",
    "批量 ID 转换（每行一个）": "Batch ID conversion (one per line)",
    "批量解析": "Resolve in batch",
    "批量解析失败": "Batch resolution failed",
    "坐标（TSS 上游默认 2kb）": "Coordinates (default 2 kb upstream of TSS)",
    "启动子": "Promoter",
    "基因组区": "Genomic region",
    "序列比对": "Sequence alignment",
    "库为 NCBI RefSeq": "Database: NCBI RefSeq",
    "原名 / 本地库（GRCz11 Ensembl）": "Original name / local library (GRCz11 Ensembl)",
    "多候选基因（点击切换）": "Multiple candidates (click to switch)",
    "填入测试序列": "Fill in a test sequence",
    "从剪贴板粘贴": "Paste from clipboard",
    "强制刷新外网聚合": "Force refresh external aggregation",
    "工作流编排": "Workflow orchestration",
    "基因详情卡片": "Gene detail card",
    "本地库无匹配": "No match in local library",
    "未解析": "Unresolved",
    "（无）": "(none)",
    "未建立 ID 链": "No ID mapping chain",
    "无匹配基因": "No matching gene",
    "无": "None",
    "无（查询即标准名）": "None (query already canonical)",
    "原站": "Source site",
    "本库": "This library",
    "你": "You",
    "复制": "Copy",
    "复制失败": "Copy failed",
    "已复制": "Copied",
    "复制 TSV": "Copy TSV",
    "复制 TSV（发表用）": "Copy TSV (for publication)",
    "上传失败": "Upload failed",
    "上传并重建索引中...": "Uploading and rebuilding the index…",
    "失败，请重试": "Failed — please try again",
    "搜索中…": "Searching…",
    "比对中…": "Aligning…",
    "ORA 富集分析中…": "Running ORA enrichment…",
    "检索 KEGG 通路…": "Searching KEGG pathways…",
    "按当前使用策略查询基因…": "Querying genes with the active strategy…",
    "按当前使用策略做 ID 映射…": "Mapping IDs with the active strategy…",
    "Ensembl 序列提取中…": "Extracting sequence from Ensembl…",
    "基因注释中…": "Annotating gene…",
    "批量解析中…": "Resolving in batch…",
    "本次用到的组学库 / 跨库映射": "Omics databases and cross-DB mappings used",
    "在「富集」页输入基因列表可做 ORA 统计显著性":
      "Enter a gene list on the Enrichment page for ORA significance testing",
    "AI 字符预算": "AI character budget",
    "AI 打包顺序": "AI packing order",
    "Bgee 组织": "Bgee tissue",
    "Bgee 组织表达": "Bgee tissue expression",
    "CDS（与 BLAST 同库）。启动子/基因组区：Ensembl REST":
      "CDS (same DB as BLAST). Promoter / genomic region: Ensembl REST",
    "GO / 通路 ORA（Fisher + BH）。基因按 GRCz12tu 标准名解析，注释来自本地 gene_go / gene_kegg":
      "GO / pathway ORA (Fisher + BH). Genes resolved to GRCz12tu symbols; annotations from local gene_go / gene_kegg",
    "实时：粗打分 500 篇 → cross_encoder 精打分 100 篇 → 按相关度+新旧合并打包后提交 AI（同 Hub 文献 Agent）":
      "Live: coarse-score 500 papers → cross_encoder rerank 100 → merge by relevance + recency and send to AI (same as Hub literature agent)",
    "完整原名/标准名对照请切到「ID解析」标签":
      "Switch to the ID parse tab for the full original/canonical name mapping",
    "提示：Web 服务版本较旧，已用逐条解析；重启 Web（启动.bat → 2）可启用完整批量 API。":
      "Note: this web service is an older release, so items were resolved one at a time. Restart it to enable the full batch API.",
    "本次已自动联动": "Auto-linked this turn: ",
    "工具": "Tools",
    "功能": "Feature",
    "ID解析": "ID parsing",
    "Hub 全链路": "Full Hub pipeline",
    "Hub 多 Agent 编排": "Hub multi-agent orchestration",
    "仅 ID 映射": "ID mapping only",
    "仅基因/数据库": "Genes / database only",
    "仅文献 RAG": "Literature RAG only",
    "检索调试": "Retrieval debug",
    "检索调试（不调用大模型）": "Retrieval debug (no LLM call)",
    "已同步：整体回答 · Hub 全链路": "Synced: full answer · full Hub pipeline",
    "聊天": "Chat",
    "组学": "Omics",
    "新闻": "News",
    "导入": "Import",
    "上传": "Upload",
    "选择文件": "Choose file",
    "开始上传": "Start upload",

    /* ── 影像流徽标 / 访客状态 ── */
    "热": "HOT",
    "新": "NEW",
    "研": "RSCH",
    "库": "LIB",
    "访客 · 快速入门": "Guest · Quick start",

    /* ── 面板 / 工具页通用 ── */
    "发送第一条消息后": "After you send the first message,",
    "会出现在这里": "it will appear here",
    "路由类型": "Route type",
    "生成 SQL": "Generated SQL",
    "查询结果": "Query result",
    "知识库检索（RAG Top-K）": "Knowledge-base retrieval (RAG Top-K)",
    "查基因": "Gene search",
    "解析": "Resolve",
    "跨库 ID": "Cross-DB IDs",
    "提取序列": "Extract sequence",
    "检索通路": "Search pathways",
    "标准名（仅 RefSeq GCF_049306965.2）": "Canonical name (RefSeq GCF_049306965.2 only)",
    "调试检索": "Debug retrieval",
    "重排 Top-N": "Rerank Top-N",
    "独立页也可打开：": "Also available as a standalone page:",
    "PDF/文本入库": "PDF / text upload",
    "运行工作流": "Run workflow",
    "输入": "Input",
    "结果": "Results",
    "结构化输出 · 可追溯": "Structured output · traceable",
    "等待运行": "Waiting to run",
    "没有账号？去注册": "No account? Sign up",
    "返回": "Back",
    "访问验证": "Access verification",
    "请输入管理员提供的访问令牌": "Enter the access token provided by the administrator",
    "验证并进入": "Verify and continue",
    "答案锚定本库与公开原站 · Zebrafish Hub": "Answers anchored to this library and public sources · Zebrafish Hub",
    "租户：默认实验室 (default)": "Tenant: default lab (default)",
    "Hub 全链路 — ID + 基因 + 文献 + 富集并行编排": "Full Hub pipeline — ID, gene, literature and enrichment in parallel",
    "仅文献 RAG — PubmedYes FAISS → 重排 → 文献合成（不跑基因/富集）": "Literature RAG only — PubmedYes FAISS → rerank → synthesis (no gene/enrichment)",
    "基因库 + 富集 — 基因详情与 GO/KEGG（不跑文献 FAISS）": "Gene DB + enrichment — gene details and GO/KEGG (no literature FAISS)",
    "仅 ID 映射 — 跨库 ID / 官方符号解析": "ID mapping only — cross-DB IDs / official symbol resolution",
    "检索调试 — 仅检索调试摘要，不调用大模型合成": "Retrieval debug — debug summary only, no LLM synthesis",
    "本地库整合": "Local library integration",
    "汇总 GO / 表达 / 同源 / 跨库 ID / 关联文献为一页视图。":
      "GO / expression / orthologs / cross-DB IDs / linked literature in one view.",
    "输入基因符号": "Enter a gene symbol",
    "尚未生成注释": "No annotation yet",
    "一键汇总 GO、表达、同源与关联文献。": "Aggregate GO, expression, orthologs and linked literature in one click.",

    /* ── 属性文案（placeholder / aria-label） ── */
    "首页主视觉": "Feature hero",
    "搜索范围": "Search scope",
    "折叠/展开右侧面板": "Collapse / expand right panel",
    "新对话 (Ctrl+Shift+L)": "New chat (Ctrl+Shift+L)",
    "通路关键词，如 MAPK、p53": "Pathway keyword, e.g. MAPK, p53",
    "查询，如：斑马鱼线粒体 / zebrafish HIF": "Query, e.g. zebrafish mitochondria / zebrafish HIF",
    "输入基因名或问题，如 tp53 的功能与相关文献":
      "Enter a gene name or question, e.g. tp53 function and related literature",
    "用户名（2～32 位）": "Username (2–32 characters)",
    "显示名（可选）": "Display name (optional)",
    "访问令牌": "Access token",

    /* ── 前缀通配（长说明文字，匹配开头即可） ── */
    "~粘贴核酸序列（≥20 bp":
      "Paste a nucleotide sequence (≥20 bp, FASTA supported; you can also use Paste from clipboard below)",
    "~符号按 GRCz12tu 标准名解析":
      "Symbols are resolved to GRCz12tu; card content comes from the local GRCz11 Ensembl r116 library.",
    "~（GCF_049306965）核酸":
      "Nucleic acid (GCF_049306965); the test sequence is the zebrafish tp53 coding region. Ctrl+V paste or drag a file is supported.",
    "~斑马鱼 dre 通路检索":
      "Zebrafish dre pathway search (KEGG / WikiPathways / Reactome), aligned with the genome assembly.",
    "~跨库 ID · 三态":
      "Cross-DB IDs · tri-state unique / ambiguous / not_found · local GRCz12tu library.",
    "~整合 GO / 表达 / 同源":
      "Aggregate GO / expression / orthologs / cross-DB IDs / linked literature. Symbols use GRCz12tu; annotation tables remain GRCz11.",
    "~仅 PDF/TXT/MD":
      "PDF/TXT/MD only, ≤5 MB, UTF-8; up to 300 files per library; server-side magic-number + script scanning.",
    "~聊天：整体回答":
      "Chat: full answer · Hub pipeline; workflow preset full_hub (chat scope unchanged).",
    "~整合视图":
      "Integrated view · symbols GRCz12tu · tables from the local library",
    "~符号走 GRCz12tu":
      "GO / expression / orthologs / cross-DB IDs / linked literature in one page view.",

    /* ── 基因 / 文献 / 入库页剩余文案 ── */
    "查符号或关键词": "Search symbol or keyword",
    "尚未查询基因": "No gene queried yet",
    "输入 tp53、sox9b 或 ZDB-GENE-…，结果卡片会出现在右侧。":
      "Enter tp53, sox9b or ZDB-GENE-…; result cards appear on the right.",
    "符号锚定 GRCz12tu · 注释来自本地 GRCz11":
      "Symbols anchored to GRCz12tu · annotations from local GRCz11",
    "粗排 500 → 精排 100": "Retrieve 500 → rerank 100",
    "粗排 500 → CrossEncoder 精排 100 → AI 打包":
      "Retrieve 500 → CrossEncoder rerank 100 → AI packing",
    "自然语言查询": "Natural-language query",
    "尚未检索文献": "No literature search yet",
    "提交查询后可查看精排表与 AI 打包顺序。":
      "After submitting a query you can view the rerank table and AI packing order.",
    "文献入库": "Literature Upload",
    "选择本地文献文件": "Choose a local literature file",
    "尚未上传文献": "No literature uploaded yet",
    "文献 → rag/knowledge.d + uploads/ · 组学库为独立数据层":
      "Literature → rag/knowledge.d + uploads/ · omics DBs are a separate layer",
    "~按 GRCz12tu 标准名解析":
      "Resolved to GRCz12tu canonical symbols; card content comes from the local GRCz11 library (coordinates / expression / GO).",
    "~PubmedYes FAISS 粗打分":
      "PubmedYes FAISS coarse scoring followed by CrossEncoder reranking; inspect the rerank and AI packing order here.",
    "~上传文献进本地知识库":
      "Upload literature into the local knowledge base and rebuild RAG (omics identity databases such as ZFIN/Ensembl are separate).",
    "~支持 PDF / TXT / MD":
      "Supports PDF / TXT / MD; uploaded files go to knowledge.d and the index is rebuilt. Omics databases (ZFIN etc.) are not uploaded here.",

    /* ── 各工具页（BLAST / 富集 / 通路 / ID 等） ── */
    "BLAST 序列比对": "BLAST Sequence Alignment",
    "粘贴 FASTA / 裸序列": "Paste FASTA / raw sequence",
    "等待比对": "No alignment run yet",
    "填入 ≥20 bp 核酸序列后运行；命中将按 e-value 排序。":
      "Fill in ≥20 bp of nucleotide sequence and run; hits are sorted by e-value.",
    "库：NCBI RefSeq GRCz12tu · GCF_049306965": "DB: NCBI RefSeq GRCz12tu · GCF_049306965",
    "GO / KEGG 富集": "GO / KEGG Enrichment",
    "输入基因列表做富集分析；注释来自本地 gene_go / gene_kegg。":
      "Enter a gene list for enrichment analysis; annotations come from local gene_go / gene_kegg.",
    "逗号分隔基因列表": "Comma-separated gene list",
    "尚未运行富集": "No enrichment run yet",
    "例如 tp53,hsp70,myl7 — 显著条目会按 FDR 列出。":
      "e.g. tp53,hsp70,myl7 — significant terms are listed by FDR.",
    "ORA 注释表：本地 gene_go / gene_kegg": "ORA annotation tables: local gene_go / gene_kegg",
    "按关键词检索斑马鱼 dre 通路，与基因组组装版本无关。":
      "Search zebrafish dre pathways by keyword (independent of the genome assembly).",
    "通路关键词": "Pathway keyword",
    "尚未检索通路": "No pathway search yet",
    "试 MAPK、p53、apoptosis 等关键词。": "Try keywords such as MAPK, p53, apoptosis.",
    "斑马鱼 dre · KEGG / WikiPathways / Reactome": "Zebrafish dre · KEGG / WikiPathways / Reactome",
    "跨库 ID 映射": "Cross-DB ID Mapping",
    "符号 ⇄ Ensembl ⇄ ZFIN ⇄ NCBI · 可批量": "Symbol ⇄ Ensembl ⇄ ZFIN ⇄ NCBI · batch",
    "三态消歧": "Tri-state disambiguation",
    "单个或批量 ID": "Single or batch IDs",
    "尚未解析 ID": "No IDs resolved yet",
    "~核酸比对库为":
      "Nucleotide BLAST database is GRCz12tu CDS (GCF_049306965). FASTA or raw sequence is supported.",
    "~本地 GRCz11 与 NCBI RefSeq":
      "Canonical-name mapping between local GRCz11 and NCBI RefSeq GRCz12tu; supports single or batch IDs with tri-state resolution.",
    "重排后 top_k": "top_k after rerank",
    "支持符号、ZFIN、Entrez、Ensembl；歧义候选会单独列出。":
      "Supports symbols, ZFIN, Entrez and Ensembl; ambiguous candidates are listed separately.",
    "本地 GRCz11 ↔ RefSeq GRCz12tu 标准名":
      "Local GRCz11 ↔ RefSeq GRCz12tu canonical names",
    "基因名 + 区段选项": "Gene name + region options",
    "尚未提取序列": "No sequence extracted yet",
    "勾选 cDNA / 启动子 / 基因组区后提取。":
      "Check cDNA / promoter / genomic region and extract.",
    "cDNA：GRCz12tu · 启动子/基因组：Ensembl GRCz11":
      "cDNA: GRCz12tu · promoter/genome: Ensembl GRCz11",
    "~cDNA 走 GRCz12tu":
      "cDNA uses GRCz12tu; promoter / genomic region use Ensembl REST GRCz11.",

    /* ── 2026-10-24 序列提取 / BLAST / 基因组对比 改版新增 ── */
    "历史对话": "History",
    "搜索历史对话…": "Search history…",
    "下载 CSV": "Download CSV",

    /* ── 2026-10-28 Liftover 双模式 / 批量 ID 互转 ── */
    "坐标换算": "Coordinate liftover",
    "显式来源／目标 · UCSC chain": "Explicit source and target · UCSC chain",
    "旧装配区间 → GRCz12tu": "Old-assembly region → GRCz12tu",
    /* 2026-10-31：这组表单标签一直漏翻，英文界面下「来源版本 / 染色体 / 起点」还是中文；
       同轮给两个版本下拉加了 optgroup 分组，分组标题也走 label 属性翻译。 */
    "来源版本": "Source assembly",
    "目标版本": "Target assembly",
    "请选择来源版本": "Select a source assembly",
    "请选择目标版本": "Select a target assembly",
    /* 用 "Chr." 而不是 "Chromosome"：左栏只有 ~150px 一列，全拼会把「起点」挤到下一行 */
    "染色体": "Chr.",
    "起点": "Start",
    "终点": "End",
    "链": "Strand",
    "换算": "Lift over",
    "粘贴区间": "Paste a region",
    "例如：GRCz11 chr5:24086227-24097805": "e.g. GRCz11 chr5:24086227-24097805",
    "GRCz 系列": "GRCz assemblies",
    "Zv 系列": "Zv assemblies",
    "NCBI 品系组装（contig N50 ≥ 1 Mb）": "NCBI strain assemblies (contig N50 ≥ 1 Mb)",
    "其他组装": "Other assemblies",

    /* ── 2026-10-31 别名 / 位置查找 两页边界重划 ── */
    "查名称": "Search",
    "查位置": "Locate",
    "基因名称检索": "Gene name search",
    "基因名，如 tp53": "Gene name, e.g. tp53",
    "符号 / 别名 / 旧名 / 跨库 ID，如 p53": "Symbol / alias / former name / cross-DB ID, e.g. p53",
    "给名称找基因 · 符号 ⇄ 别名 ⇄ 跨库 ID": "Name → gene · symbol ⇄ alias ⇄ cross-DB ID",
    "给基因看落点 · 坐标 · 链 · 基因体序列": "Gene → locus · coordinates · strand · gene-body sequence",
    "批量 ID 互转": "Batch ID mapping",
    "符号 ⇄ Ensembl ⇄ ZFIN ⇄ NCBI": "Symbol ⇄ Ensembl ⇄ ZFIN ⇄ NCBI",
    "Liftover 模式": "Liftover mode",
    "每行一个 ID": "One ID per line",
    "每行一个 ID（符号 / 别名 / Ensembl / ZFIN / NCBI Gene ID），最多 200 条":
      "One ID per line (symbol / alias / Ensembl / ZFIN / NCBI Gene ID), up to 200",
    "批量互转": "Map IDs",
    "复制表格": "Copy table",
    "清空": "Clear",
    "本地库 gene_basic / gene_xref（GRCz11 Ensembl 116 · NCBI Gene · ZFIN）；标准符号按 RefSeq GCF_049306965.2（GRCz12tu）。":
      "Source: local gene_basic / gene_xref (GRCz11 Ensembl 116 · NCBI Gene · ZFIN); standard symbols follow RefSeq GCF_049306965.2 (GRCz12tu). ",
    "未匹配的留空，不做猜测。": "Unmatched IDs are left blank — never guessed.",
    "要一次换一批 ID（符号 ⇄ Ensembl ⇄ ZFIN ⇄ NCBI）？":
      "Need to map a batch of IDs (symbol ⇄ Ensembl ⇄ ZFIN ⇄ NCBI)?",
    "批量 ID 互转 →": "Batch ID mapping →",
    "源版本与链在上方按钮组里切换（GRCz11 = Ensembl 注释坐标）。单向：本库只备了指向 GRCz12tu 的 UCSC over.chain（GRCz11 / GRCz10 / Zv9 / Zv8 → GRCz12tu，碱基级）。反方向没有链文件，不做，也不给近似值。":
      "Switch source build and strand with the buttons above (GRCz11 = Ensembl annotation coordinates). One-way only: the library ships UCSC over.chain files that point at GRCz12tu (GRCz11 / GRCz10 / Zv9 / Zv8 → GRCz12tu, base-pair level). There is no reverse chain, so no reverse conversion and no approximations.",
    "为什么不做坐标转换（liftover）": "Why no coordinate liftover",
    "去按染色体区间取序列 →": "Retrieve sequence by region →",
    "只回答装配之间的问题：两条染色体长度差多少、基因密度差多少。GRCz12tu 长度为本机 FASTA 实测；GRCz11 长度为 Ensembl 记录值（取一次缓存 7 天）。不列基因名——两版的 SYMBOL 和 NAME 本来就相同，列出来没有信息量。":
      "Only assembly-vs-assembly questions: how much the two builds differ in chromosome length and gene density. GRCz12tu lengths are measured on the local FASTA; GRCz11 lengths are Ensembl records cached for 7 days. Gene names are not listed — SYMBOL and NAME are identical across both builds, so the list carries no information.",
    "基因名（可批量）": "Gene name(s), batch supported",
    "按染色体区间": "By genomic region",
    "按基因": "By gene",
    "启动子 2 kb": "Promoter (2 kb)",
    "基因体": "Gene body",
    "区段": "Region",
    "参考装配": "Assembly",
    "来源": "Source",
    "NCBI RefSeq（与 BLAST 同库）": "NCBI RefSeq (same DB as BLAST)",
    "Ensembl REST（本地注释坐标）": "Ensembl REST (local annotation coordinates)",
    "所有序列都由参考装配直接取出，不支持上传自定义基因组；每段结果会标注 assembly / 来源 / 登录号。":
      "All sequences are taken from reference assemblies; custom genomes are not accepted. Every block is labelled with assembly / source / accession.",
    "区间（染色体 + 起止）": "Region (chromosome + start/end)",
    "批量区间（每行一条，最多 50 条）": "Batch regions (one per line, up to 50)",
    "批量提取": "Extract all",
    "正链 +": "Forward strand +",
    "负链 −": "Reverse strand −",
    "按坐标提取": "Extract by coordinates",
    "序列输入（1 条裸序列，或批量 FASTA）": "Sequence input (one raw sequence, or batch FASTA)",
    "导入 FASTA 文件": "Import FASTA file",
    "填入 3 条示例": "Insert 3 sample sequences",
    "运行 BLAST": "Run BLAST",
    "GRCz11 ↔ GRCz12tu · 逐染色体长度与基因密度": "GRCz11 ↔ GRCz12tu · length & gene density per chromosome",
    "看清两套参考装配差在哪里：25 条染色体的长度、GC、基因数与密度逐条对照，并说明本系统哪一步该用哪一版。坐标换算只做长度级比较，不做 liftover。":
      "See how the two reference assemblies differ: chromosome-by-chromosome length, GC, gene count and density, plus which assembly each part of this hub uses. Coordinates are compared by length only — no liftover.",
    "两版装配": "Two assemblies",
    "逐染色体对比": "Per-chromosome comparison",
    "加载两版染色体的实测对照": "Load the measured comparison of both assemblies",
    "点「加载对比表」：GRCz12tu 用本机实测值，GRCz11 从 Ensembl 取一次并缓存 7 天。":
      "Click “Load comparison”: GRCz12tu uses local measurements; GRCz11 is fetched once from Ensembl and cached for 7 days.",
    "GRCz12tu = 本机 FASTA 实测 · GRCz11 = Ensembl 记录值 · 不做坐标 liftover":
      "GRCz12tu = measured on the local FASTA · GRCz11 = Ensembl values · no coordinate liftover",
    "按基因 · 按染色体区间 · 支持批量": "By gene · by region · batch supported",
    "两条路：按基因名取 cDNA / 启动子 / 基因体（可一次多个基因）；或给 chr:start-end 取任意区间（可一次多行）。所有序列都从参考装配直接取出，每段结果都标注 assembly、来源与登录号。":
      "Two routes: by gene name for cDNA / promoter / gene body (multiple genes at once), or chr:start-end for any region (one per line). All sequences come from reference assemblies; every block is labelled with assembly, source and accession.",
    "GRCz12tu / GRCz11": "GRCz12tu / GRCz11",
    "批量 ≤20": "Up to 20 per batch",
    "基因名（可多个），或 染色体:起-止": "Gene name(s), or chromosome:start-end",
    "按基因：输入 tp53，勾 cDNA / 启动子；按坐标：填 8 / 起 / 止，或用「批量区间」每行一条。":
      "By gene: enter tp53 and check cDNA / promoter; by coordinates: fill chr / start / end, or use batch regions (one per line).",
    "cDNA：GRCz12tu（RefSeq）· 启动子 / 基因体：GRCz11（Ensembl，本地注释坐标）":
      "cDNA: GRCz12tu (RefSeq) · promoter / gene body: GRCz11 (Ensembl, local annotation coordinates)",
    "本地 BLAST+（blastn）比对 NCBI RefSeq GRCz12tu 核酸库。单条可直接粘裸序列，多条用 FASTA（每条以 > 开头）一次提交，也可导入 .fa / .fasta 文件；结果按查询分组。":
      "Local BLAST+ (blastn) against the NCBI RefSeq GRCz12tu nucleotide library. Paste one raw sequence, or a multi-record FASTA (each starting with >), or import a .fa / .fasta file; results are grouped per query.",
    "一次最多 20 条查询序列，请分批提交": "At most 20 query sequences per run; please split into batches",
    "已识别 0 条序列 · 一次最多 20 条": "0 sequences detected · up to 20 per run",
    "引擎为 NCBI BLAST+ blastn，库是 NCBI RefSeq GRCz12tu（GCF_049306965，与 cDNA 提取同版）。批量时逐条提交、结果按查询分组，可一键复制整批 TSV。":
      "Engine: NCBI BLAST+ blastn; database: NCBI RefSeq GRCz12tu (GCF_049306965, the same assembly used for cDNA extraction). Batch queries are submitted one by one, results grouped per query; the whole batch can be copied as TSV.",
    /* 2026-10-27：这些是 EN 模式下仍显示中文的漏译项（下拉说明 / 选项 / 工具页提示） */
    "每条序列：前 5 条命中": "Top 5 hits per sequence",
    "每条序列：前 10 条命中": "Top 10 hits per sequence",
    "每条序列：前 20 条命中": "Top 20 hits per sequence",
    "启动子 / 基因体": "Promoter / gene body",
    "GRCz12tu · 与 BLAST / cDNA 同版": "GRCz12tu · same assembly as BLAST / cDNA",
    "GRCz11 · 与本地注释坐标一致": "GRCz11 · matches local annotation coordinates",
    "单条最多 100 kb。默认 GRCz12tu：按本地登记的 RefSeq 登录号向 NCBI EFetch 取区间，与 BLAST 用的是同一版参考；选 GRCz11 则走 Ensembl REST，用于和本地注释坐标对齐。":
      "Up to 100 kb per region. Default GRCz12tu: the region comes from NCBI EFetch using the locally registered RefSeq accession — the same reference BLAST uses. Choosing GRCz11 goes through Ensembl REST so coordinates line up with the local annotation.",
    "真实 liftover 需要厂商提供的装配间比对链文件（chain / AGP）。本地没有 GRCz11→GRCz12tu 的链文件，Ensembl 也尚未收录 GRCz12tu，所以这里只做长度级对比，不假装能换算坐标。要取某段序列，请到「序列提取 → 按染色体区间」，默认就是 GRCz12tu（与 BLAST 同版）。":
      "A real liftover needs a vendor-provided alignment chain between assemblies (chain / AGP). There is no GRCz11→GRCz12tu chain locally, and Ensembl does not host GRCz12tu yet, so this page only compares lengths and does not pretend to convert coordinates. To pull a sequence, use Sequence → by chromosome region, which defaults to GRCz12tu (the same build as BLAST).",
    "文件上传已禁用（WEB_ALLOW_UPLOAD=0）": "File upload is disabled (WEB_ALLOW_UPLOAD=0)",


    /* ── 运营看板 / 运维页（2026-10-13 补：英文模式下曾残留中文）── */
    "运营看板": "Operations dashboard",
    "定时任务 · 可信度可追溯": "Scheduled jobs · traceable provenance",
    "定时任务与访问统计来自服务端只读接口；访问统计仅返回计数，不含任何 IP 明细。":
      "Job and visit data come from read-only server endpoints; visit stats return counts only, with no IP details.",
    "定时任务计划与最近状态、文献推送规模、聚合访问统计（仅计数，无 IP 明细）。":
      "Job schedule and latest status, literature scale, aggregated visit counts (counts only, no IP details).",
    "cron 任务": "cron jobs",
    "仅聚合统计": "Aggregate counts only",
    "点右侧刷新获取服务端最新状态": "Click Refresh to load the latest server state",
    "正在读取服务端状态": "Reading server state",
    "状态来自 /api/hub/maintenance/status 与 /api/hub/visit-stats。":
      "State comes from /api/hub/maintenance/status and /api/hub/visit-stats.",
    "定时任务由服务器 crontab 驱动 · 访问统计不收集 IP 明细":
      "Jobs are driven by the server crontab · visit stats never collect IP details",
    "定时任务计划（服务器 crontab）": "Job schedule (server crontab)",
    "任务": "Job",
    "计划": "Schedule",
    "实现": "Implementation",
    "文献周更（PubMed / Europe PMC）": "Weekly literature update (PubMed / Europe PMC)",
    "每周日 03:20": "Sundays 03:20",
    "图文库刷新": "Gallery refresh",
    "每 3 天 04:10": "Every 3 days 04:10",
    "Hub 统计重算": "Hub stats recompute",
    "每小时第 5 分钟": "Hourly at minute 5",
    "Hub / 隧道存活守护": "Hub and tunnel health check",
    "每分钟": "Every minute",
    "维护任务状态（服务端 · 已启用）": "Maintenance jobs (server · enabled)",
    "维护任务状态（服务端 · 已关闭）": "Maintenance jobs (server · disabled)",
    "服务端时间": "Server time",
    "间隔": "Interval",
    "状态": "Status",
    "· 状态 2xx": "· status 2xx",
    "最近运行": "Last run",
    "最近结果": "Last result",
    "运行中": "Running",
    "待运行": "Due",
    "正常": "OK",
    "已启用": "Enabled",
    "已关闭": "Disabled",
    "尚未运行": "Never run",
    "文献周更": "Weekly literature update",
    "统计重算": "Stats recompute",
    "全量": "Full",
    "文献与知识库规模": "Literature & knowledge-base scale",
    "knowledge.d 文件": "knowledge.d files",
    "· 文献目录行": "· catalog rows",
    "· 进度 PMID": "· PMIDs processed",
    "文献目录行": "catalog rows",
    "进度 PMID": "PMIDs processed",
    "访问统计（聚合，仅计数）": "Visit stats (aggregated, counts only)",
    "访问统计": "Visit stats",
    "维护状态": "Maintenance status",
    "窗口": "Window",
    "条访问日志（扫描": "access lines (scanned",
    "行）· 独立来源": "rows) · unique clients",
    "· 状态": "· status",
    "日志更新时间": "Log updated",
    "路由": "Route",
    "次数": "Hits",
    "读取服务端状态…": "Reading server state…",
    "只统计计数，不返回 IP 明细；如需地图需另接地理库并在合规前提下使用。":
      "Counts only — no IP details are returned; a visitor map would need a separate geo database and a compliance review.",
    "加载中…": "Loading…",
    "刷新状态": "Refresh",
    "立即重算统计": "Recompute stats now",
    "快照": "Snapshot",
    "说明": "Note",
    "2026-09-30（本地库与索引快照）": "2026-09-30 (local library & index snapshot)",

    /* ── 其余残留中英/pill 文案 ── */
    "GRCz12tu · 表达 · GO": "GRCz12tu · Expression · GO",
    "表达 · GO": "Expression · GO",
    "cDNA / 启动子": "cDNA / promoter",
    "别名 · 同源": "Aliases · Orthologs",
    "dre 通路": "dre pathways",
    "前 100 条打包": "Pack top 100",
    "Fisher 精确检验": "Fisher's exact test",
    "BH-FDR 校正": "BH-FDR correction",
    "多来源": "Multi-source",
    "支持批量": "Batch supported",
    "唯一 / 歧义 / 未命中": "unique / ambiguous / not_found",
    "FASTA 输出": "FASTA output",
    "一键汇总": "One-click summary",
    "多层整合": "Multi-layer integration",
    "RAG 入库": "RAG ingest",
    "租户：默认实验室": "Tenant: default lab",
    "文献通道：PDF/TXT/MD ≤5MB UTF-8 → uploads/ + knowledge.d → 重建 RAG。组学库（ZFIN/Ensembl 等）不经此上传。":
      "Document channel: PDF/TXT/MD ≤5MB UTF-8 → uploads/ + knowledge.d → rebuild RAG. Omics databases (ZFIN / Ensembl …) are not uploaded here.",
    "Hub 多 Agent": "Hub multi-agent",
    "自动路由": "Auto routing",
    "定时任务 · 访问统计": "Scheduled jobs · Visit stats",
    "引用本平台": "Cite Us",
    "清空上文，从本轮重新开始": "Clear context and restart from this turn",
    "导出为 Markdown 文件（含问答与引用）": "Export as Markdown (Q&A plus citations)",
    "导出为 JSON（含完整字段，便于归档）": "Export as JSON (all fields, for archiving)",
    "复制整段对话为 Markdown 文本": "Copy the whole conversation as Markdown",
    "本地库 · GRCz11 r116": "Local library · GRCz11 r116",
    "本地全文库 · qwen3_4b_v2_short_section": "Local full-text library · qwen3_4b_v2_short_section",
    "选择要入库的文献文件（PDF / TXT / MD）": "Choose a document to ingest (PDF / TXT / MD)",
    "工作流预设": "Workflow preset",

    /* ── 聊天「引用与溯源」面板 + 工具结果 UI（2026-10-13 补：英文模式下仍中文）── */
    "参考文献": "References",
    "引用与溯源": "Citations & sources",
    "引用条目": "Citations",
    "检索统计": "Retrieval stats",
    "粗排": "Coarse",
    "精排": "Rerank",
    "打包字符": "Packed characters",
    "上下文": "Context",
    "本条信息来自": "This answer draws on",
    "本地全文库": "Local full-text library",
    "数据来源：": "Data sources:",
    "本地库暂无别名记录": "No alias in the local library",
    "别名": "Aliases",
    "同源基因": "Orthologs",
    "复制体": "Paralogs",
    "表达": "Expression",
    "功能注释": "Functional annotation",
    "通路": "Pathways",
    "文献": "Literature",
    "基础信息": "Basic info",

    /* ── 2026-10-15：Genomes / Expression / Phenotype 三个真实功能页 ── */
    /* 导航菜单 */
    "组装与染色体总览": "Assemblies & chromosomes",
    "25 条染色体 · 长度 / GC / 基因数": "25 chromosomes · length / GC / gene count",
    "BLAST 序列比对": "BLAST sequence alignment",
    "组织表达谱": "Tissue expression profiles",
    "Bgee 表达分 · 按组织 / 分期": "Bgee score · by tissue / stage",
    "组织 Top 基因": "Top genes in a tissue",
    "某组织里表达最高的基因": "Top-scoring genes in a tissue",
    "单基因卡（含表达）": "Single-gene card with expression",
    "基因–表型查询": "Gene–phenotype search",
    "表型↔基因 双向 · 页内切换": "Phenotype ↔ gene · switch inside",
    /* 2026-10-27 导航二次收口 + 侧栏改版 */
    "表达谱": "Expression",
    "按基因 · 按组织 · 多基因对比（页内切换）": "By gene · by tissue · multi-gene (switch inside)",
    "开启新对话": "New chat",
    "搜索历史对话…": "Search chats…",
    "搜索历史对话": "Search chats",
    "推理步骤": "Reasoning steps",
    "ZFIN 注释 · 表型↔基因 双向": "ZFIN annotations · phenotype ↔ gene",
    "表型相关文献": "Phenotype literature",
    "本地全文库题名命中": "Local paper-title matches",
    /* 面板控件 */
    "加载总览": "Load overview",
    "选择染色体": "Choose a chromosome",
    "选择染色体…": "Pick a chromosome…",
    "看该染色体基因": "Genes on that chromosome",
    "表达查询输入": "Expression query input",
    "查询模式": "Query mode",
    "按基因看表达谱": "Profile by gene",
    "按组织看 Top 基因": "Top genes by tissue",
    "多基因对比": "Compare genes",
    "选择组织": "Choose a tissue",
    "选择组织…": "Pick a tissue…",
    "查询": "Search",
    "表型查询输入": "Phenotype query input",
    "按表型查基因": "Genes by phenotype",
    "按基因查表型": "Phenotypes by gene",
    "构建 / 刷新 ZFIN 索引": "Build / refresh ZFIN index",
    "基因名 / 别名 / Ensembl，如 tp53、sox9b；多基因用逗号分隔":
      "Gene name / alias / Ensembl, e.g. tp53, sox9b; separate genes with commas",
    "基因名，如 sox9b、tp53": "Gene name, e.g. sox9b, tp53",
    "表型关键词，如 increased curvature": "Phenotype keyword, e.g. increased curvature",
    "染色体长度 / GC 来自本机 GRCz12tu 基因组 FASTA 实测（25 条）；基因数按本地注释库（GRCz11 · Ensembl r116）的染色体编号统计；下方同时列出本地 BLAST 库规模。":
      "Chromosome length / GC are measured on the local GRCz12tu genome FASTA (25 sequences); gene counts are grouped by chromosome in the local annotation library (GRCz11 · Ensembl r116); local BLAST library sizes are listed below.",
    "两组数据单位不同、分开呈现：Bgee 表达分（0–100，按解剖实体 / 组织）；Expression Atlas TPM（按胚胎分期，仅 marker 基因子集）。数值不可跨组比较。":
      "The two data sets use different units and are shown separately: Bgee expression score (0–100, by anatomical entity / tissue) and Expression Atlas TPM (by embryonic stage, marker-gene subset only). Values must not be compared across the two.",
    "数据来源：ZFIN 官方表型注释（phenoGeneCleanData_fish.txt，本地解析为 SQLite 索引）+ 本地文献库题名命中。文献编号是 ZFIN 的 ZDB-PUB，可在 zfin.org 核对。":
      "Sources: official ZFIN phenotype annotations (phenoGeneCleanData_fish.txt, parsed locally into a SQLite index) plus title matches from the local paper library. Publication IDs are ZFIN ZDB-PUB records and can be checked at zfin.org.",
    /* 页面主视觉（PAGE_META） */
    "基因组总览": "Genome overview",
    "基因组对比": "Genome comparison",
    "GRCz11 ↔ GRCz12tu · 逐染色体长度 · 不做 liftover":
      "GRCz11 ↔ GRCz12tu · per-chromosome lengths · no liftover",
    "只回答两个装配之间的问题：谁做了标准名 / BLAST，谁锚定本地注释，以及 25 条染色体两版长度差多少。基因名清单已移除 —— 那个列表里 SYMBOL 和 NAME 是同一个值，没有信息量。":
      "Answers only cross-assembly questions: which build carries canonical symbols / BLAST, which one anchors local annotation, and how the 25 chromosomes differ in length. The old gene-name list is gone — SYMBOL and NAME were the same value there.",
    "两版装配": "Two assemblies",
    "长度对比": "Length comparison",
    "点「加载对比表」": "Click “Load comparison table”",
    "尚未加载对比": "Comparison not loaded yet",
    "不做坐标 liftover · 取区间序列请用「序列 → 按染色体区间」":
      "No coordinate liftover · pull a region via Sequence → by coordinates",
    "加载对比表": "Load comparison",
    "本页不做坐标转换（liftover）": "No coordinate liftover on this page",
    "逐染色体对比": "Chromosome-by-chromosome comparison",
    "装配登记": "Assembly registry",
    "本地比对库": "Local BLAST databases",
    "去按坐标取序列 →": "Retrieve sequence by coordinates →",
    "GRCz12tu 为本地 FASTA 实测长度；GRCz11 为 Ensembl 记录长度。Δ 为正表示 GRCz12tu 更长":
      "GRCz12tu is measured on the local FASTA; GRCz11 is recorded by Ensembl. Positive Δ means GRCz12tu is longer",
    "同一套系统里并存两版坐标：标准名 / BLAST / cDNA 用 GRCz12tu，基因注释与表达用 GRCz11":
      "Two assemblies coexist: canonical symbols / BLAST / cDNA use GRCz12tu; annotation and expression use GRCz11",
    "本地注释锚点（坐标 / 表达 / GO）": "local annotation anchor (coordinates / expression / GO)",
    "标准名 / BLAST / cDNA": "canonical symbols / BLAST / cDNA",
    "在本系统中的用途": "Role in this hub",
    "本地表达表 · 315k 条记录 · 24,245 个基因": "Local expression table · 315k rows · 24,245 genes",
    "按基因看组织表达谱（Bgee 表达分按解剖实体降序），或按组织看表达最高的基因，也可以多基因并排对比共同覆盖的组织。":
      "View a tissue profile per gene (Bgee scores sorted by anatomical entity), list the highest-scoring genes for one tissue, or compare several genes across their commonly covered tissues.",
    "Bgee 表达分": "Bgee expression score",
    "Atlas TPM（分期）": "Atlas TPM (stages)",
    "输入基因名，或选组织": "Enter a gene name, or pick a tissue",
    "尚未查询表达": "No expression query yet",
    "输入 tp53、sox9b 看组织谱；或切到「按组织看 Top 基因」选一个组织；多基因用逗号分隔可对比。":
      "Enter tp53 or sox9b for a tissue profile; switch to “Top genes by tissue” and pick a tissue; separate several genes with commas to compare.",
    "数据来自本机表达表 · 单位与来源已在结果中标注":
      "Data comes from the local expression table · units and sources are labelled in the results",
    "ZFIN 表型注释 · 双向检索": "ZFIN phenotype annotations · search both ways",
    "两个方向查：给表型看有哪些基因被记录到该表型，或给基因看它被记录了哪些表型；结果按表型 / 等位基因分组，并给出分期与文献编号。":
      "Search both directions: give a phenotype to see which genes are annotated with it, or give a gene to see which phenotypes it carries; results are grouped by phenotype / allele with stages and publication IDs.",
    "表型 ↔ 基因": "phenotype ↔ gene",
    "输入表型关键词或基因名": "Enter a phenotype keyword or a gene name",
    "尚未查询表型": "No phenotype query yet",
    "例：表型「increased curvature」、或基因「sox9b」；首次使用请先点「构建 / 刷新 ZFIN 索引」。":
      "Try the phenotype “increased curvature” or the gene “sox9b”; on first use, run “Build / refresh ZFIN index”.",
    "表型 = ZFIN 官方注释本地索引 · 文献号 ZDB-PUB 可在 zfin.org 核对":
      "Phenotypes = official ZFIN annotations in a local index · ZDB-PUB IDs can be verified at zfin.org",
    /* 表达表 atlas 分期列里的少量中文标签（源数据即中文，英文模式下需要对照） */
    "成体": "adult",
    "热应激": "heat stress",
    "应激": "stress",

    /* ── 2026-10-15：About 真页面 + 常显工具栏 ── */
    "定位与范围 · 数据规模": "Scope · data scale",
    "本库 / ZFIN / Ensembl / NCBI / Bgee": "Local / ZFIN / Ensembl / NCBI / Bgee",
    "Cite Us · 边界与免责": "Cite Us · limits",
    "数字均为本机库实测快照；列出的每个数据源都能在原站逐条核对。":
      "All figures are measured snapshots of the local libraries; every listed source can be checked line by line at its source site.",
    "本地注释 · 组学库双链 · 可复核": "Local annotation · omics cross-links · verifiable",
    "斑马鱼基因知识中枢：把本地注释库、表达表、ZFIN 表型注释与文献全文库放在同一入口；每条结论都能回到库里或原站核对。":
      "A zebrafish gene knowledge hub: local annotation, expression tables, ZFIN phenotype annotations and a full-text literature library behind one entry point — every conclusion can be traced back to the library or the source site.",
    "数据快照 2026-10-15": "Snapshot 2026-10-15",
    "选择左侧章节": "Pick a section on the left",
    "定位与范围 / 数据来源与算法 / 引用方式 —— 三章都是实测数字与可复核来源。":
      "Scope / Data & methods / How to cite — all three chapters use measured figures and checkable sources.",
    "本页数字为本机库实测快照 · 与各功能页同一口径":
      "All figures here are measured snapshots of the local libraries · same definitions as the feature pages",
    "服务在线": "Online",
  };

  /* ── 2026-10-28：英文界面里混中文的收口（溯源面板 / 编排步骤 / 自动工具卡）──
     这批词条不是猜的：用英文界面真问了一句 what is tp53，把渲染结果里所有含汉字的
     文本节点导出来（logs/_i18n_leaks_raw.json），一条一条对照补的。
     分两类：
       1. 固定文案（本表）—— 步骤名、表头、按钮、口径说明；
       2. 带变量的句子（下面的 EXTRA_RULES）—— 步数、命中数、状态名。
     新增 UI 文案时，往这两处补即可；漏了会被 logs/_cdp_i18n_scan.py 抓出来。 */
  var EXTRA_20261028 = {
    "历史": "History",
    "两版装配逐染色体长度对比": "Compare chromosome lengths",
    "路由 Agent": "Router agent",
    "基因分析 Agent": "Gene analysis agent",
    "ID 映射 Agent": "ID mapping agent",
    "富集分析 Agent": "Enrichment agent",
    "文献 RAG Agent": "Literature RAG agent",
    "综合 Agent": "Synthesis agent",
    "意图与范围": "Intent & scope",
    "实体解析": "Entity resolution",
    "通路检索": "Pathway search",
    "基因详情": "Gene detail",
    "一键注释": "One-click annotation",
    "文献检索": "Literature search",
    "序列提取": "Sequence extraction",
    "BLAST 比对": "BLAST alignment",
    "ID 解析": "ID resolution",
    "GO/KEGG 富集": "GO / KEGG enrichment",
    "KEGG 通路": "KEGG pathways",
    "ID 映射": "ID mapping",
    "基因位置": "Gene locus",
    "GO 富集": "GO enrichment",
    "位置查找序列": "Sequence by region",
    "入库": "Library upload",
    "在本页打开 →": "Open here →",
    "通用聊天": "General chat",
    "Hub 全链路": "Full Hub pipeline",
    "整体回答": "Full answer",
    "部分回答": "Partial answer",
    "自动路由": "auto-routed",
    "已解析": "resolved",
    "已汇总": "summarized",
    "已提取": "extracted",
    "已比对": "aligned",
    "已检索": "searched",
    "已取基因卡片": "gene card loaded",
    "基因注释": "Gene annotation",
    "基因功能注释": "Gene function annotation",
    "无匹配通路": "no matching pathway",
    "GO 显著": "GO significant",
    "通路显著": "Significant pathways",
    "这次富集看的是哪些方面": "What this enrichment covers",
    "GO 三个方面": "GO aspects",
    "通路来源库": "Pathway sources",
    "生物学过程": "Biological process",
    "细胞组分": "Cellular component",
    "分子功能": "Molecular function",
    "方面未标注": "aspect not annotated",
    "Fisher 精确检验 + BH 校正，q≤0.05": "Fisher's exact test + BH correction, q ≤ 0.05",
    "ORA（Fisher 精确检验）+ Benjamini–Hochberg 校正":
      "ORA (Fisher's exact test) + Benjamini–Hochberg correction",
    "Reactome 反应通路": "Reactome reactions",
    "GO 富集 · 每条已标注方面（BP/CC/MF）":
      "GO enrichment · each term tagged with its aspect (BP/CC/MF)",
    "通路富集 · 每条已标注来源库": "Pathway enrichment · each entry tagged with its source library",
    "q 值(padj)": "q value (padj)",
    "p 值": "p value",
    "富集倍数": "Fold enrichment",
    "命中/条目": "Hits / terms",
    "命中基因": "Hit genes",
    "命中/通路": "Hits / pathways",
    "KEGG 类目": "KEGG category",
    "类别": "Category",
    "基因（节选）": "Genes (excerpt)",
    "名称未收录，可点 QuickGO 查看": "Term not indexed — open in QuickGO",
    "查看纯文本原始结果": "View raw text output",
    "命中的通路来自哪些库": "Source databases for these pathways",
    "KEGG 通路（REST 实时取回）": "KEGG pathways (live REST)",
    "WikiPathways 通路": "WikiPathways pathways",
    "(查询输入)": "(query input)",
    "服务正在回收内存，约 20 秒后自动恢复，请稍后重试；这不是限流。":
      "The service is reclaiming memory and will be back in about 20 s — please retry shortly. This is not rate limiting.",
    "服务返回了网页而非常规接口响应，通常是网关错误页或服务未启动":
      "the service returned an HTML page instead of an API response — usually a gateway error page or the service is down",
    /* ── 表达 / 基因卡片 ── */
    "胸腺": "thymus",
    "精巢": "testis",
    "胚胎": "embryo",
    "两组数值": "Two data sets",
    "单位与来源不同，不能直接比较": "Different units and sources — not directly comparable",
    "Bgee 组织 / 发育表达": "Bgee tissue / developmental expression",
    "Expression Atlas 表达量": "Expression Atlas levels",
    "非丰度": "presence, not abundance",
    "）· 按分数排序": ") · sorted by score",
    "单位：Expression Score（0–100，表达存在性置信分，":
      "Unit: Expression Score (0–100; confidence that expression is present, ",
    "单位：TPM（转录本每百万映射读段数）· 以整胚发育时间序列为主，含少量标注组织样本":
      "Unit: TPM (transcripts per million mapped reads) · mostly whole-embryo developmental time series, with a few annotated tissue samples",
    "在基因卡片中打开": "Open in gene card",
    "原名/原版本：": "Original name / assembly:",
    "基因/库范围": "Gene / library scope",
    "标准名/标准版本：": "Canonical name / assembly:",
    /* ── sgRNA / 引物口径说明 ── */
    "评分为本地透明规则的加权结果（效率×0.72 + 特异性×0.28）；权威活性预测请用 CHOPCHOP / CRISPRScan 复核（可用下方跳转按钮）。":
      "Scores are transparent local rule weights (efficiency × 0.72 + specificity × 0.28); for authoritative activity prediction cross-check with CHOPCHOP / CRISPRScan (links below).",
    "Tm 用 SantaLucia 1998 最近邻热力学估算（50 mM Na+、0.5 µM 引物）；引物按规则打分（Tm 差、产物长度、二聚体、发夹），未做完整 NN 二聚体搜索。":
      "Tm estimated with SantaLucia 1998 nearest-neighbour thermodynamics (50 mM Na⁺, 0.5 µM primer); primers are scored by rule (Tm difference, product length, dimers, hairpins) — no full NN dimer search.",
  };
  Object.keys(EXTRA_20261028).forEach(function (k) {
    if (!(k in ZH2EN)) ZH2EN[k] = EXTRA_20261028[k];
  });

  /* 反向表（英文 → 中文），用于切回中文；以 ~ 开头的通配键不参与反向 */
  var EN2ZH = {};
  Object.keys(ZH2EN).forEach(function (k) {
    if (k.charAt(0) === "~") return;
    var v = ZH2EN[k];
    if (v && !(v in EN2ZH)) EN2ZH[v] = k;
  });

  /* 顶栏五项的中文名（2026-10-28）
     --------------------------------------------------------------------------
     词典里 "Chat" 被多个中文键指向（智能问答 / 问答 / 聊天），反查只会取到第一个，
     于是中文界面把导航项显示成「智能问答」，词不达意；"Omics" 干脆没有词条。
     这里对这五个词做显式覆盖，只影响这几个固定名称，不动其余翻译。 */
  var NAV_ZH = { "Chat": "聊天", "Omics": "组学", "Tools": "工具", "News": "新闻", "About": "关于" };
  Object.keys(NAV_ZH).forEach(function (k) { EN2ZH[k] = NAV_ZH[k]; });

  /* 前缀通配表（按长度降序，避免短前缀抢先匹配） */
  var PREFIX = [];
  Object.keys(ZH2EN).forEach(function (k) {
    if (k.charAt(0) === "~") PREFIX.push([k.slice(1), ZH2EN[k]]);
  });
  PREFIX.sort(function (a, b) { return b[0].length - a[0].length; });

  function prefixLookup(t) {
    if (t.length < 6) return undefined;
    for (var i = 0; i < PREFIX.length; i++) {
      if (t.indexOf(PREFIX[i][0]) === 0) return PREFIX[i][1];
    }
    return undefined;
  }

  /* 带变量的动态文案（正则规则），仅 中→英 方向生效 */
  function translateImagery(s) {
    return s.replace(/^影像流 ([\d,]+) 张/, "Imagery $1")
      .replace(/^影像池 ([\d,]+) 张/, "Image pool $1")
      .replace(/实时\/镜像/g, "live/mirror")
      .replace(/后台拉取中/g, "fetching…")
      .replace(/强制刷新/g, "force refresh")
      .replace(/本地/g, "local")
      .replace(/缓存/g, "cached")
      .replace(/实时/g, "live")
      .replace(/模式/g, "mode")
      .replace(/分页池/g, "paged pools")
      .replace(/池刷新/g, "pool refreshed")
      .replace(/分页/g, "pages")
      .replace(/瞬时/g, "ephemeral")
      .replace(/维护批次/g, "maintained batch")
      .replace(/张/g, "images");
  }

  var RULES = [
    /* ── 聊天流程里的带参数句子（2026-10-11）── */
    [/^📥 已加入队列（剩余 (\d+) 个）：(.*)$/, "📥 Queued ($1 pending): $2"],
    [/^📥 服务正在处理上一问，已加入队列[：:](.*)$/, "📥 Previous answer still running — queued: $1"],
    [/^请求失败[（(]HTTP (\d+)[）)]$/, "Request failed (HTTP $1)"],
    [/^请求失败[：:](.*)$/, "Request failed: $1"],
    [/^已带入上文 (\d+) 轮$/, "Context: $1 turns"],
    [/^已带入上文 (\d+) 条$/, "Context: $1 items"],
    [/^整体回答 · Hub 编排中…$/, "Full answer · Hub orchestrating…"],
    [/^部分回答 · Hub 编排中…$/, "Partial answer · Hub orchestrating…"],
    [/^整体回答 - Hub 编排中\.\.\.$/, "Full answer — Hub orchestrating…"],
    [/^部分回答 - Hub 编排中\.\.\.$/, "Partial answer — Hub orchestrating…"],
    [/^实时库规模 · 用户锚 (.+) · 本地注释 (.+) · 刷新 (.+)$/, "Live Atlas · anchor $1 · local annotation $2 · updated $3"],
    [/^处理中 (\d+)s$/, "Processing $1s"],
    /* ── 多机并行的排队提示（2026-10-28）── */
    [/^排队中 · 前面还有 (\d+) 位（已等 (\d+)s，约还需 (\d+)s）$/,
      "Queued · $1 ahead (waited $2s, ~$3s left)"],
    [/^排队中 · 前面还有 (\d+) 位（已等 (\d+)s）$/, "Queued · $1 ahead (waited $2s)"],
    [/^排队中 · 即将开始（已等 (\d+)s，约还需 (\d+)s）$/, "Queued · starting now (waited $1s, ~$2s left)"],
    [/^排队中 · 即将开始（已等 (\d+)s）$/, "Queued · starting now (waited $1s)"],
    [/^服务并行已满（(\d+) 人在排队），可直接发送$/, "At capacity ($1 queued) — you can still send"],
    [/^服务在线 · (\d+) 人问答中$/, "Online · $1 chat(s) running"],
    [/^📥 已加入队列（剩余 (\d+) 个）：(.+)$/, "📥 Queued ($1 pending): $2"],
    [/^📥 服务正在处理上一问，已加入队列：(.+)$/, "📥 Previous question still processing, queued: $1"],
    [/^找到 ([\d,]+) 条（点击详情查看完整卡片）$/, "Found $1 records (click for the full card)"],
    [/^匹配 (\d+) 条通路（最多 15 条）$/, "Matched $1 pathways (max 15)"],
    [/^共 ([\d,]+) 条\)$/, "of $1)"],
    [/^KEGG：无显著通路（padj≤(.+)）。可到「KEGG」页按关键词检索。$/,
      "KEGG: no significant pathway (padj ≤ $1). Try keyword search on the KEGG page."],
    /* ── 运营看板：间隔格式与读取失败（2026-10-13）── */
    [/^条访问日志（扫描 ([\d,]+) 行）· 独立来源$/, "access lines ($1 scanned) · unique clients"],
    /* 聊天/工具结果里的带数字标签 */
    [/^引用条目（(\d+)）$/, "Citations ($1)"],
    [/^([\d,]+) 个别名 · 来源：(.*)$/, "$1 aliases · source: $2"],
    [/^共 (\d+) 个候选，按综合分排序 · 近同源：(.*)$/, "$1 candidates, ranked by combined score · near-cognate check: $2"],
    [/^模板：cDNA · (\S+) · (\d+) bp$/, "Template: cDNA · $1 · $2 bp"],
    [/^· 状态 (2xx|3xx|4xx|5xx)$/, "· status $1"],
    /* 服务端 429 详情是中文，且带秒数 */
    [/^请求过于频繁，请 (\d+)s 后重试$/, "Too many requests — retry in $1s"],
    [/^读取失败[：:]请求过于频繁，请 (\d+)s 后重试$/, "Read failed: too many requests — retry in $1s"],
    [/^已识别 (\d+) 条序列 · 合计 ([\d,]+) bp · 一次最多 20 条$/, "$1 sequences detected · $2 bp total · up to 20 per run"],
    [/^已识别 (\d+) 条序列 · 一次最多 20 条$/, "$1 sequences detected · up to 20 per run"],
    [/^一次最多 20 个基因，请分批提取$/, "At most 20 genes per run; please split into batches"],
    [/^一次最多 50 条区间，请分批提交$/, "At most 50 regions per run; please split into batches"],
    [/^请每行填一条区间，如 8:1000-2000$/, "Enter one region per line, e.g. 8:1000-2000"],
    [/^批量提取：(\d+) 个基因 · 区段 (.+)$/, "Batch extraction: $1 genes · regions $2"],
    [/^批量区间：(\d+) 条 · (.+) · (.+) 链$/, "Batch regions: $1 · $2 · $3 strand"],
    [/^读取失败[：:](.*)$/, "Read failed: $1"],
    [/^([\d.]+) 周$/, "$1 wk"],
    [/^([\d.]+) 天$/, "$1 days"],
    [/^([\d.]+) 小时$/, "$1 h"],
    [/^([\d.]+) 分钟$/, "$1 min"],
    [/^([\d.]+) 秒$/, "$1 s"]
  ];

  /* ── 2026-10-28：带变量的 UI 句子（同样来自实测导出，不是凭空写的）──
     RULES 只做「正则 → 替换串」，够用的都放这里；需要嵌套翻译的（如
     「打开 <标签> →」「后端报告已调用：A、B」）在 rule() 里单独处理。 */
  [
    [/^换算到 (.+)$/, "Lift over to $1"],
    [/^本次编排（(\d+) 步）$/, "This run ($1 steps)"],
    [/^本次查询（(\d+) 步）$/, "This run ($1 steps)"],
    [/^🔬 本次已自动联动 (\d+) 项$/, "🔬 $1 tool(s) auto-linked"],
    [/^命中 (\d+) 条通路$/, "$1 pathways matched"],
    [/^GO (\d+) 项 · KEGG (\d+) 条（padj≤(.+)）$/, "GO $1 terms · KEGG $2 pathways (padj ≤ $3)"],
    [/^GO (\d+) 条$/, "$1 GO terms"],
    [/^一对多候选 (\d+) 个$/, "$1 candidates"],
    [/^打包 ([\d,]+) 字符$/, "$1 chars packed"],
    [/^粗排 ([\d,]+) → 精排 ([\d,]+)$/, "coarse $1 → rerank $2"],
    [/^背景基因 ([\d,]+)（GeneRatio = 命中\/前景）$/, "Background genes $1 (GeneRatio = hits / foreground)"],
    [/^标准名 RefSeq (.+?)（GRCz12tu）· 本地库 GRCz11 · (.+)$/,
      "Canonical name: RefSeq $1 (GRCz12tu) · local library GRCz11 · $2"],
    [/^本条无 RefSeq (.+) 组装对照$/, "No RefSeq $1 assembly cross-reference for this record"],
    [/^基因 (.+) 未找到$/, "Gene $1 not found"],
    [/^解析 未命中：(.+)$/, "Resolved: no hit for $1"],
    [/^ORA 显著 GO 项 (\d+)$/, "ORA-significant GO terms: $1"],
    [/^RAG\[(.+?)\] (\d+) 字符 \/ PMID (\d+)$/, "RAG[$1] $2 chars / $3 PMIDs"],
    [/^用文献编号证据合成（约 (\d+) 篇 \/ ([\d,]+) 字）$/, "Synthesized from citation evidence (~$1 papers / $2 chars)"],
    [/^([\d.,–\-]+ bp)（可调）$/, "$1 (adjustable)"],
    [/^Primer candidates \(ranked by rule score\) · 已附 T7 启动子尾（(.+?)）用于体外转录模板扩增$/,
      "Primer candidates (ranked by rule score) · T7 promoter tail ($1) appended for in-vitro transcription template amplification"],
    [/^转录组内近全长 ≤2 错配命中 (\d+) 处（(.+?)）$/,
      "Transcriptome near-full-length hits (≤2 mismatches): $1 ($2)"],
    [/^解析 成功：(.+)$/, "Resolved: $1"],
    [/^基因 (.+) 已加载$/, "Gene $1 loaded"],
    [/^共 ([\d,]+) 条$/, "$1 records"],
    [/^RefSeq (.+?) 标准名 · (.+)$/, "RefSeq $1 canonical name · $2"],
    [/^RefSeq (.+?) 标准名$/, "RefSeq $1 canonical name"],
    [/^本地 (.+?) · (.+)$/, "Local $1 · $2"],
    [/^(\d+hpf)（整胚）$/, "$1 (whole embryo)"],
  ].forEach(function (r) { RULES.push(r); });

  /* 不翻译的区域：聊天答案正文、Markdown 正文、代码、序列（保护用户数据与答案）。
     工具结果区（.tool-result）不再整块跳过——那里除 <pre>/<code> 外都是我们自己渲染的 UI；
     需要保护的单个节点或容器标 data-no-i18n="1" 即可。 */
  var SKIP_SEL = "pre, code, script, style, .md-body, .msg-body, [data-no-i18n]";
  /* 2026-10-31：加 "label" —— <optgroup label="GRCz 系列"> 这类分组标题只写在属性里，
     没有文本节点可翻，不加就出现「英文界面里下拉分组标题还是中文」。 */
  var ATTRS = ["title", "placeholder", "aria-label", "alt", "label"];
  var STORE_KEY = "hub_lang_v2";

  /* 聊天区整体跳过（保护答案不破机器翻译），但**状态/队列提示**是 UI 文案，
     标注 data-i18n-live="1" 就则显式放行（app.js 的 typing 占位与入队提示都标了）。 */
  function skipped(el) {
    if (!el || !el.closest) return false;
    if (el.closest("[data-i18n-live]")) return false;
    return !!el.closest(SKIP_SEL);
  }

  var lang = "en";
  try {
    /* 2026-10-28 用户要求：整个 Hub（含欢迎门）**默认英文**，只有用户主动切到中文之后
       才显示中文。原逻辑是「没存过选择就跟浏览器语言走」，中文浏览器一进来就是中文界面，
       与「默认英文」相反，所以删掉 navigator.language 判断，缺省值恒为 en。
       存储键同步升到 v2：老访客 localStorage 里可能留着 hub_lang=zh，
       如果继续读旧键，他们刷新后仍是中文，看起来像「默认没改」。
       旧键只做清理，不再回灌。 */
    try { localStorage.removeItem("hub_lang"); } catch (e0) { /* ignore */ }
    var saved = localStorage.getItem(STORE_KEY);
    if (saved === "zh" || saved === "en") lang = saved;
  } catch (e) { /* ignore */ }

  function rule(t) {
    if (/^影像流 |^影像池 /.test(t)) return translateImagery(t);
    // 等待提示是「句子 + 括号补充」，括号里的秒数要留着：先译前半句再拼英文括号。
    var mWait = t.match(/^(.*)（已等待 (\d+)s，(.*)）$/);
    if (mWait) {
      var base = translateString(mWait[1]);
      return (base || mWait[1]) + " (waited " + mWait[2] + "s — " + waitTail(mWait[3]) + ")";
    }
    /* 下面两条要「套着翻」：句子里的标签/列表本身也是中文，
       正则替换只能换骨架，必须再调一次 translateString。 */
    var mOpen = t.match(/^打开 ?(.+?) ?→$/);
    if (mOpen) return "Open " + translateString(mOpen[1]) + " →";
    var mCall = t.match(/^后端报告已调用：(.+)$/);
    if (mCall) {
      return "Backend reported calls: " + mCall[1].split("、").map(function (x) {
        return translateString(x);
      }).join(", ");
    }
    // 「路由 → 通用聊天（整体回答）intent=…」：括号内外都是词条，得分别翻
    var mRoute = t.match(/^路由 → (.+?)（(.+?)）(.*)$/);
    if (mRoute) {
      return "Route → " + translateString(mRoute[1]) + " (" + translateString(mRoute[2]) + ")" +
        (mRoute[3] ? " " + mRoute[3] : "");
    }
    // 本地库无匹配的提示，尾巴是「scripts/a 与 scripts/b」，连接词也要换掉
    var mNoMatch = t.match(/^未在本地库（GRCz11）找到匹配；可运行 (.+)$/);
    if (mNoMatch) {
      return "No match in the local library (GRCz11); run " +
        mNoMatch[1].replace(/\s*与\s*/g, " and ");
    }
    // 「基因/库范围 → 基因详情（整体回答）」「主任务完成（基因详情）」：三段都要翻
    var mArrow = t.match(/^(.+?) → (.+?)（(.+?)）$/);
    if (mArrow) {
      return translateString(mArrow[1]) + " → " + translateString(mArrow[2]) +
        " (" + translateString(mArrow[3]) + ")";
    }
    var mDone = t.match(/^主任务完成（(.+?)）$/);
    if (mDone) return "Main task done (" + translateString(mDone[1]) + ")";
    // 「请求失败（HTTP 502）：<后端中文原因>」：原因也要翻
    var mFail = t.match(/^请求失败（HTTP (\d+)）[：:](.+)$/);
    if (mFail) {
      return "Request failed (HTTP " + mFail[1] + "): " + translateString(mFail[2]);
    }
    for (var i = 0; i < RULES.length; i++) {
      var m = t.match(RULES[i][0]);
      if (m) return t.replace(RULES[i][0], RULES[i][1]);
    }
    return undefined;
  }

  function waitTail(s) {
    var k = (s || "").trim();
    if (k.indexOf("文献库首载") === 0) return "the first literature load can be slow";
    if (k.indexOf("已等待") === 0) return k;
    return k || "please wait";
  }

  function hasCJK(s) {
    return /[\u4e00-\u9fff]/.test(s);
  }

  /* 复合串兜底：UI 里很多「A · B · C」是变量拼出来的（如「通用聊天 · hub · full」
     「通用聊天 · Hub 全链路 · 整体回答」），整串永远进不了词典。
     这里按 " · " 拆开逐段查，**所有段落都能落定才采用** —— 宁可整句不译，
     也不要出现半中半英。 */
  function compose(t) {
    if (t.indexOf("·") < 0) return undefined;
    // 「成体·胸腺」「成体 · 胸腺」两种写法都有（前者是数据里带的），统一按 · 拆
    var parts = t.split(/\s*·\s*/);
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i].trim();
      if (ZH2EN[p] !== undefined) { out.push(ZH2EN[p]); continue; }
      if (hasCJK(p)) return undefined;      // 有一段没词条 → 整句放弃
      out.push(parts[i]);                    // 纯英文/数字原样保留
    }
    return out.join(" · ");
  }

  function translateString(s) {
    if (!s) return s;
    var t = s.trim();
    if (!t) return s;
    var out = lang === "en" ? ZH2EN[t] : EN2ZH[t];
    if (out === undefined && lang === "en") {
      out = rule(t);
      if (out === undefined) out = prefixLookup(t);
      if (out === undefined) out = compose(t);
      /* 带图标前缀的标签（「🔗 ID 解析」「📊 GO/KEGG 富集」「🛤 KEGG 通路」）：
         整串进不了词典，剥掉前面的符号再翻一次，图标原样接回去。 */
      if (out === undefined) {
        var mIcon = t.match(/^([^\w\u4e00-\u9fff]+)\s*([\s\S]+)$/);
        if (mIcon && hasCJK(mIcon[2])) {
          var rest = translateString(mIcon[2]);
          if (rest !== mIcon[2]) out = mIcon[1] + rest;
        }
      }
    }
    if (out === undefined || out === t) return s;
    return s.replace(t, out);
  }

  function inSkip(el) {
    return skipped(el);
  }

  /* 原文缓存：翻译必须「可逆」。每个文本节点 / 属性都记住最初的原文，
     切换语言时从原文重新翻译，而不是在已翻译的结果上再翻一次——
     否则 RULES / PREFIX 生成的英文（如 "Context: 0 turns"）没有反向词条，
     切回中文时就会残留英文。 */
  var ORIG_TEXT = new WeakMap();
  var LAST_TEXT = new WeakMap();
  var ORIG_ATTR = new WeakMap();

  function applyTextNode(n) {
    // 动态插入的节点（MutationObserver 路径）也要遵守 data-no-i18n，
    // 否则「使用说明」页这种自带双语的块会被反向表二次翻译成中文/英文。
    if (n.parentElement && skipped(n.parentElement)) return;
    if (!ORIG_TEXT.has(n)) ORIG_TEXT.set(n, n.nodeValue);
    var nv = translateString(ORIG_TEXT.get(n));
    LAST_TEXT.set(n, nv);
    if (nv !== n.nodeValue) n.nodeValue = nv;
  }

  function attrState(el) {
    var st = ORIG_ATTR.get(el);
    if (!st) { st = { orig: {}, last: {} }; ORIG_ATTR.set(el, st); }
    return st;
  }

  function applyAttr(el, name) {
    var cur = el.getAttribute && el.getAttribute(name);
    if (cur == null) return;
    var st = attrState(el);
    if (!Object.prototype.hasOwnProperty.call(st.orig, name)) st.orig[name] = cur;
    var nv = translateString(st.orig[name]);
    st.last[name] = nv;
    if (nv !== cur) el.setAttribute(name, nv);
  }

  function applyAttrsTo(el) {
    /* 坑（2026-10-18）：walk(root) 只给 root 的**子节点**翻译属性，root 自身从来不翻。
       而 app.js 的状态栏是每次健康轮询都重建（innerHTML）一遍的 —— 新插入的
       `<span class="badge ok" title="…">` 永远轮不到 translateString，
       表现就是“切到英文后悬停提示仍是中文”，手动 refresh() 才正常。 */
    if (!el || el.nodeType !== 1) return;
    if (el.matches && el.matches(SKIP_SEL) && !el.closest("[data-i18n-live]")) return;
    for (var a = 0; a < ATTRS.length; a++) applyAttr(el, ATTRS[a]);
  }

  function walk(root) {
    if (!root) return;
    var kids = root.childNodes;
    if (!kids) return;
    for (var i = 0; i < kids.length; i++) {
      var c = kids[i];
      if (c.nodeType === 3) {
        if (skipped(root)) {
          /* 例外（2026-10-18）：答案气泡 `.md-body` 默认整体不翻（里面是模型正文），
             但**系统生成的文献链接标签**（如「原文」）属于界面文案。
             只当整串能在词典里精确命中时才翻，模型自己写的句子不可能整串命中。

             2026-10-28 补：显式标了 data-no-i18n 的元素（顶层导航 Chat / Omics /
             Tools / News / About、品牌名）是作者**故意要保留原文**，一律不翻。
             原写法只看 `lang === "en"`，中文界面下反而会按反向表把这些词翻成中文
             （Chat → 智能问答、Tools → 工具、About → 关于），标记等于失效，
             用户看到的顶栏就不再是约定的英文五项。 */
          var hardSkip = root.closest ? root.closest("[data-no-i18n]") : null;
          var inBody = root.closest ? root.closest(".md-body, .msg-body") : null;
          if (hardSkip && !inBody) continue;
          var pa = root.closest ? root.closest("a, button") : null;
          if (!(pa && lang === "en" && ZH2EN[(c.nodeValue || "").trim()])) continue;
        }
        applyTextNode(c);
        continue;
      }
      if (c.nodeType !== 1) continue;
      if (c.matches && c.matches(SKIP_SEL) && !c.closest("[data-i18n-live]")) continue;
      for (var a = 0; a < ATTRS.length; a++) applyAttr(c, ATTRS[a]);
      var tag = c.tagName;
      if (tag === "TEXTAREA" || tag === "INPUT") continue; // 保护用户输入内容
      walk(c);
    }
  }

  /* 显式放行的区域：标了 data-i18n-live 的节点即使嵌在被保护的答案正文里，
     也要翻译（如聊天里的「引用与溯源」面板、参考文献标题、来源徽标行）。
     注意：递归 walk() 遇到被保护的父节点会停下，所以这里要单独再走一遍。 */
  function walkLive() {
    var list;
    try { list = document.querySelectorAll("[data-i18n-live]"); } catch (e) { return; }
    for (var i = 0; i < list.length; i++) walk(list[i]);
  }

  var liveTimer = 0;
  function scheduleLive() {
    if (liveTimer) return;
    liveTimer = setTimeout(function () { liveTimer = 0; walkLive(); walkAnswerLinks(); }, 180);
  }

  /* 答案气泡（.md-body / .msg-body）整体在 SKIP_SEL 里（里面是模型正文），
     但**系统生成的链接标签**（「原文」之类）属于界面文案。
     元素级已经被 skip 掉，文本节点的例外根本走不到 —— 所以单独走一遍。
     只翻“整串能在词典里精确命中”的文本，模型自己写的句子不可能整串命中。 */
  function walkAnswerLinks() {
    var list;
    try {
      list = document.querySelectorAll(
        ".md-body a, .msg-body a, a.md-a," +
        ".md-body button, .msg-body button, button.msg-action-btn," +
        "[data-i18n-live] a, [data-i18n-live] button"
      );
    } catch (e) { return; }
    for (var i = 0; i < list.length; i++) {
      var a = list[i];
      if (a.closest("[data-no-i18n]")) continue;
      for (var j = 0; j < a.childNodes.length; j++) {
        var n = a.childNodes[j];
        if (n.nodeType === 3 && ZH2EN[(n.nodeValue || "").trim()]) applyTextNode(n);
      }
    }
    /* 兜底：有些参考文献行是**未渲染的 markdown**（`[原文](https://doi.org/…)` 原样显示），
       整串不可能精确命中词典 → 只替换里面的界面标签本身。 */
    if (lang === "en") {
      var all;
      try { all = document.querySelectorAll(".md-body, .msg-body"); } catch (e2) { return; }
      for (var k = 0; k < all.length; k++) {
        var tw = document.createTreeWalker(all[k], NodeFilter.SHOW_TEXT);
        while (tw.nextNode()) {
          var tn = tw.currentNode;
          if (tn.nodeValue && tn.nodeValue.indexOf("[原文](") >= 0) {
            tn.nodeValue = tn.nodeValue.replace(/\[原文\]\(/g, "[Source](");
          }
        }
      }
    }
  }

  function syncButton() {
    var b = document.getElementById("langToggleBtn");
    if (!b) return;
    b.textContent = lang === "en" ? "中 / EN" : "EN / 中";
    /* 2026-11-02：这行原来两边都写中文，英文界面下悬停仍是中文。
       按钮本身是 data-no-i18n，走不到词典，必须按当前语言直接给串。 */
    b.title = lang === "en"
      ? "Switch to Chinese"
      : "切换到英文";
  }

  function mountButton() {
    if (document.getElementById("langToggleBtn")) return;
    var anchor = document.getElementById("themeToggleBtn");
    if (!anchor || !anchor.parentNode) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.id = "langToggleBtn";
    btn.className = anchor.className || "icon-btn";
    btn.setAttribute("data-no-i18n", "1");
    btn.addEventListener("click", function () {
      setLang(lang === "en" ? "zh" : "en");
    });
    anchor.parentNode.insertBefore(btn, anchor);
    syncButton();
  }

  function setLang(next, silent) {
    lang = next === "zh" ? "zh" : "en";
    if (!silent) {
      try { localStorage.setItem(STORE_KEY, lang); } catch (e) { /* ignore */ }
    }
    document.documentElement.setAttribute("lang", lang === "en" ? "en" : "zh-CN");
    /* 2026-10-28：首帧防闪的开关（见 index.html <head>）。翻译已经跑完一遍，门户可以画出来了。 */
    document.documentElement.classList.remove("zf-lang-pending");
    walk(document.body);
    walkLive();   // 被保护正文里的 UI 外壳（引用与溯源等）也要跟上
    walkAnswerLinks();  // 答案里的文献链接标签（原文…）
    if (document.title) {
      var t = translateString(document.title);
      if (t !== document.title) document.title = t;
    }
    syncButton();
    if (!silent) {
      document.dispatchEvent(new CustomEvent("hub-lang-change", { detail: { lang: lang } }));
    }
  }

  var observer = new MutationObserver(function (muts) {
    for (var i = 0; i < muts.length; i++) {
      var m = muts[i];
      if (m.type === "characterData") {
        // 值等于上次输出 → 是我们自己写的，忽略；否则视为业务侧改了文案，重记原文
        if (LAST_TEXT.get(m.target) === m.target.nodeValue) continue;
        ORIG_TEXT.set(m.target, m.target.nodeValue);
        applyTextNode(m.target);
        continue;
      }
      if (m.type === "attributes") {
        var el = m.target;
        var cur = el.getAttribute && el.getAttribute(m.attributeName);
        if (cur == null) continue;
        var st = attrState(el);
        if (st.last[m.attributeName] === cur) continue;
        st.orig[m.attributeName] = cur;
        applyAttr(el, m.attributeName);
        continue;
      }
      for (var j = 0; j < m.addedNodes.length; j++) {
        var n = m.addedNodes[j];
        if (n.nodeType === 3) {
          if (n.parentElement && skipped(n.parentElement)) continue;
          applyTextNode(n);
        } else if (n.nodeType === 1) {
          if (n.matches && n.matches(SKIP_SEL) && !n.closest("[data-i18n-live]")) continue;
          applyAttrsTo(n);
          walk(n);
        }
      }
    }
    // 新增/改动的节点里可能带 data-i18n-live（如聊天气泡里的溯源面板），
    // 它们嵌在被保护正文里，递归 walk 到不了 —— 批量后再补一遍（防抖）。
    scheduleLive();
  });

  function init() {
    mountButton();
    setLang(lang);
    observer.observe(document.documentElement, {
      childList: true, subtree: true, characterData: true,
      attributes: true, attributeFilter: ATTRS
    });
    // app.js 在异步数据回填后会重建部分 DOM（导航/状态栏/用户区），
    // 这里做几次幂等重放（翻译可重复执行且无副作用），随后交由 MutationObserver 接管。
    [150, 500, 1200, 2500, 4500].forEach(function (ms) {
      setTimeout(function () { mountButton(); setLang(lang, true); }, ms);
    });
    window.addEventListener("load", function () {
      mountButton();
      setLang(lang, true);
    });
    // 语言状态暴露给调试
    window.HubI18n = {
      get lang() { return lang; },
      setLang: setLang,
      refresh: function () { mountButton(); setLang(lang, true); },
      /* 给 app.js / provenance.js 等脚本用的双语选择器：T(中文, 英文) */
      T: function (zh, en) { return lang === "en" ? (en == null ? zh : en) : zh; },
      isZh: function () { return lang !== "en"; },
      /* 单串翻译，给测试脚本和需要自行拼文案的模块用 */
      t: function (s) { return translateString(s); },
      dict: ZH2EN
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
