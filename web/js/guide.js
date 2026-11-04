/* 使用说明 · 全部「书面注释」的唯一归宿
   ------------------------------------------------------------------
   设计原则
   1. 功能页只留「顾名思义」的一句话（做什么），不放口径与边界。
   2. 口径、单位、数据版本、为什么某个功能不做某件事 —— 全部收进本页。
   3. 文案写成 [中文, English] 双语数组，跟随 hub_lang 直接重渲染，
      不走 i18n.js 的文本节点匹配（那套机制遇到长段落容易被拆节点）。
*/
(function () {
  "use strict";

  function T(zh, en) { return { zh: zh, en: en }; }

  var SECTIONS = [
    /* ---------------- 1. 上手 ---------------- */
    {
      id: "start",
      nav: T("快速上手", "Quick start"),
      title: T("三步就能用", "Three steps"),
      lead: T("不用注册。打开就能问，答案都带出处。", "No account needed. Ask right away — every answer carries sources."),
      steps: [
        T("在底部输入框打一个基因名或一句话，例如 tp53，回车。",
          "Type a gene name or a question in the box at the bottom — e.g. tp53 — and press Enter."),
        T("答案里的编号可以点，直接回到文献或 ZFIN / Ensembl / NCBI 原站。",
          "Citations are clickable and open the paper or the original ZFIN / Ensembl / NCBI record."),
        T("要序列、比对、引物，用顶部 Tools；要坐标换算或按基因查位置，用 Omics。",
          "Need sequences, alignments or primers? Use Tools. Need coordinate conversion or a gene locus? Use Omics.")
      ],
      note: T("访客的对话只存在这台电脑上；登录后同步到自己的云端空间，互不打扰。",
        "Guest chats stay on this device. Sign in to sync them to your own cloud space.")
    },

    /* ---------------- 2. 入口 ---------------- */
    {
      id: "where",
      nav: T("去哪找功能", "Find a tool"),
      title: T("入口按对象分：Omics 三区 + 工具 + 新闻", "Grouped by object: three Omics areas, tools, news"),
      lead: T("不知道点哪个时，看这张表。", "Not sure where to start? This table maps each task to a tool."),
      table: [
        [T("Chat", "Chat"), T("问问题：基因功能、表型、文献、实验设计", "Ask anything: function, phenotype, literature, design")],
        [T("Omics → Genome", "Omics → Genome"), T("Liftover 坐标换算、按区间取序列", "Liftover, sequence by region")],
        [T("Omics → Gene", "Omics → Gene"), T("别名、按基因查位置、组织表达", "Aliases, gene locus, expression")],
        [T("Omics → Phenotype", "Omics → Phenotype"), T("基因 → 表型、表型 → 基因", "Gene → phenotype, phenotype → genes")],
        [T("Tools", "Tools"), T("BLAST、GO、KEGG、sgRNA 设计、引物设计", "BLAST, GO, KEGG, sgRNA, primers")],
        [T("News", "News"), T("新进来的文献、检索排序过程", "New papers, ranking trace")],
        [T("平台说明", "About the platform"), T("定位与范围、数据来源、引用方式", "Scope, data sources, citation")]
      ]
    },

    /* ---------------- 3. 模块说明（原页面注释的搬家目的地） ---------------- */
    {
      id: "modules",
      nav: T("每个功能的口径", "Notes per tool"),
      title: T("逐条口径", "Tool by tool"),
      lead: T("平时不用看。对某个数字有疑问时，展开对应那一条。", "You can skip this. Open a row only when a number looks odd."),
      mods: [
        {
          name: T("Liftover（坐标换算）", "Liftover"),
          one: T("把旧组装的一段区间换到 GRCz12tu；页内另有装配长度对比。", "Lift an older-assembly region over to GRCz12tu; chromosome-length comparison sits on the same page."),
          body: [
            T("GRCz12tu 的长度取自本机 FASTA 实测；GRCz11 的长度取 Ensembl 记录值，取一次缓存 7 天。",
              "GRCz12tu lengths are measured on the local FASTA; GRCz11 lengths are Ensembl records cached for 7 days."),
            T("只列染色体级别的长度、GC、基因数与密度，不列基因名——两版的 SYMBOL 与 NAME 本来就一致，列出来没有信息量。",
              "Only chromosome-level length, GC, gene count and density are listed. Gene names are omitted because SYMBOL and NAME are identical in both assemblies."),
            /* 2026-11-04：原写「链文件只备了指到 GRCz12tu 的方向，反向没有链」，
               与实测不符——服务器上 danRer11→GCF_049306965.2 与 GCF_049306965.2→danRer11
               两条链都在，反向可以精确回投。改成照实描述。 */
            T("坐标换算走 UCSC over.chain，碱基级：GRCz11 / GRCz10 / Zv9 / Zv8 → GRCz12tu 是主方向；GRCz11 与 GRCz12tu 之间装了双向链，也能把 GRCz12tu 的区间精确回投到 GRCz11。缺链的方向明确拒绝，不给近似坐标；跨链断裂点时同样不给数。",
              "Liftover runs on UCSC over.chain files at base-pair resolution. GRCz11, GRCz10, Zv9 and Zv8 all convert into GRCz12tu, and the GRCz11 ↔ GRCz12tu pair is installed in both directions, so a GRCz12tu interval can also be mapped back to GRCz11 exactly. Directions without a chain are refused outright with no approximate coordinates; regions that cross a chain break are refused as well.")
          ]
        },
        {
          name: T("基因别名 / 基因位置", "Gene aliases / locus"),
          one: T("按符号、别名或跨库 ID 找基因，点开是基因卡。", "Find a gene by symbol, alias or cross-database ID."),
          body: [
            T("可检索 32,139 条基因记录（含 7,493 条 ZFIN 克隆 / EST 旧名，仍可按旧名查到；其中 22,370 条带 UniProt 蛋白编号，2,779 条经 Swiss-Prot 审阅）：标准符号、ZFIN / NCBI / UniProt 别名、Entrez 与 Ensembl ID 都能命中。",
              "32,139 gene records are searchable (including 7,493 legacy ZFIN clone / EST entries, still reachable by their old names; 22,370 of them carry a UniProt protein accession, 2,779 Swiss-Prot reviewed) by canonical symbol, ZFIN / NCBI / UniProt alias, Entrez ID and Ensembl ID."),
            T("符号统一按 GRCz12tu 标准名解析；基因卡上的坐标一律给 GRCz12tu（RefSeq 原始坐标，或 GRCz11 注释经碱基级 liftover 投影），表达与 GO 注释出自 Ensembl r116。",
              "Symbols are resolved against GRCz12tu canonical symbols; every coordinate on the card is given on GRCz12tu (native RefSeq positions, or GRCz11 annotations projected by base-level liftover), while expression and GO annotations come from Ensembl release 116.")
          ]
        },
        {
          name: T("组织表达谱", "Expression atlas"),
          one: T("按基因看组织，按组织看基因，或几个基因并排比。", "Browse by gene, by tissue, or compare several genes."),
          body: [
            T("两组数据单位不同，分开呈现：Bgee 表达分是 0–100 的相对分，按解剖实体 / 组织给；Expression Atlas 是 TPM，按胚胎分期给，且只覆盖 marker 基因子集。",
              "Two units are kept apart: Bgee expression score is a 0–100 relative score per anatomical entity / tissue; Expression Atlas reports TPM per developmental stage and only for a marker-gene subset."),
            T("两组的数字不能横着比——它们的量纲和取样范围都不一样。",
              "Numbers from the two sources are not comparable: different scales, different coverage.")
          ]
        },
        {
          name: T("基因–表型", "Gene–phenotype"),
          one: T("给表型看哪些基因，或给基因看有哪些表型。", "From phenotype to genes, or from gene to phenotypes."),
          body: [
            T("数据来自 ZFIN 官方表型注释（phenoGeneCleanData_fish.txt），本地解析成 SQLite 索引，再用本地文献库补题名命中。",
              "Data comes from ZFIN's official phenotype annotations (phenoGeneCleanData_fish.txt), parsed into a local SQLite index and supplemented with title matches from the local literature library."),
            T("文献编号是 ZFIN 的 ZDB-PUB，可以直接在 zfin.org 上核对；首次使用请先点「构建 / 刷新 ZFIN 索引」。",
              "Publication IDs are ZFIN ZDB-PUB records you can verify on zfin.org. On first use, run “Build / refresh ZFIN index”.")
          ]
        },
        {
          name: T("BLAST", "BLAST"),
          one: T("把你的序列比对到斑马鱼核酸库。", "Align your sequence against the zebrafish nucleotide library."),
          body: [
            T("引擎是 NCBI BLAST+ 的 blastn，库是 NCBI RefSeq GRCz12tu（GCF_049306965），与 cDNA 提取用的是同一版参考。",
              "The engine is NCBI BLAST+ blastn against NCBI RefSeq GRCz12tu (GCF_049306965) — the same reference used for cDNA extraction."),
            T("批量时逐条提交，结果按查询分组，可以一键复制整批 TSV。单次上限 20 条。",
              "Batch jobs are submitted one query at a time, grouped per query on output, with one-click TSV copy. Up to 20 queries per run.")
          ]
        },
        {
          name: T("序列提取", "Sequence extraction"),
          one: T("按基因名或按染色体区间取序列，可批量。", "Retrieve sequences by gene name or genomic interval, in batches."),
          body: [
            T("序列一律从参考装配直接取出，不支持上传自定义基因组；每段结果都会标注装配版本、来源与登录号。",
              "All sequence is read straight from the reference assemblies; custom genomes cannot be uploaded. Each record is labelled with assembly, source and accession."),
            T("cDNA 走 GRCz12tu（RefSeq）；启动子与基因体走 GRCz11（Ensembl，与本地注释坐标一致）。按坐标取单条最多 100 kb，默认 GRCz12tu 经 NCBI EFetch 取区间，选 GRCz11 则走 Ensembl REST。",
              "cDNA comes from GRCz12tu (RefSeq); promoters and gene bodies from GRCz11 (Ensembl, aligned with local annotation coordinates). Single intervals are capped at 100 kb — GRCz12tu goes through NCBI EFetch, GRCz11 through Ensembl REST.")
          ]
        },
        {
          name: T("GO / KEGG 富集", "GO / KEGG enrichment"),
          one: T("一串基因丢进去，看哪些功能显著富集。", "Drop in a gene list and see which functions are enriched."),
          body: [
            T("过表达分析（ORA）：Fisher 精确检验 + Benjamini–Hochberg 校正。基因先按 GRCz12tu 标准名解析，注释来自本地 gene_go / gene_kegg 表。",
              "Over-representation analysis: Fisher's exact test with Benjamini–Hochberg correction. Genes are resolved to GRCz12tu symbols; annotations come from the local gene_go / gene_kegg tables.")
          ]
        },
        {
          name: T("通路浏览器", "Pathway browser"),
          one: T("查斑马鱼 dre 通路里有哪些基因。", "Look up which genes sit in a zebrafish dre pathway."),
          body: [
            T("合并 KEGG、WikiPathways、Reactome 三个来源的 dre 通路，与基因组装配版本无关。",
              "Merges dre pathways from KEGG, WikiPathways and Reactome — independent of assembly version.")
          ]
        },
        {
          name: T("跨库 ID 映射", "Cross-database ID mapping"),
          one: T("符号、ZFIN、Entrez、Ensembl 之间互转。", "Map between symbol, ZFIN, Entrez and Ensembl IDs."),
          body: [
            T("结果分三态：unique（唯一命中）、ambiguous（一对多，候选单独列出）、not_found（未收录）。对照表是本地 GRCz11 与 NCBI RefSeq GRCz12tu 的标准名。",
              "Results come in three states: unique, ambiguous (one-to-many, candidates listed) and not_found. The mapping table links local GRCz11 to NCBI RefSeq GRCz12tu canonical names.")
          ]
        },
        {
          name: T("sgRNA / 引物设计", "sgRNA / primer design"),
          one: T("扫 PAM 出 sgRNA，或按规则出引物。", "Scan PAMs for sgRNA, or design primers by rule."),
          body: [
            T("本地引擎按 CHOPCHOP / CRISPRscan 的通用规则做双链 PAM 枚举，再按 GC、连续 T、二级结构、位置权重打分，可选转录组或全基因组脱靶筛查。",
              "The local engine enumerates PAMs on both strands following common CHOPCHOP / CRISPRscan rules, then scores by GC, poly-T, secondary structure and position weight, with optional transcriptome or genome-wide off-target screening."),
            T("引物按长度 18–25 nt、Tm 差 ≤2 ℃、GC 40–70%、无发夹与二聚体、3′ 端稳定来设计，可加 T7 / SP6 启动子尾做体外转录。",
              "Primers follow 18–25 nt, Tm difference ≤2 °C, GC 40–70%, no hairpins or dimers, stable 3′ end; a T7 / SP6 promoter tail can be appended for in vitro transcription."),
            T("打分是本地的透明规则，不是训练模型；投稿前建议用 CHOPCHOP / CRISPRscan 复核。",
              "Scoring is transparent local rules, not a trained model. Re-check with CHOPCHOP / CRISPRscan before publication.")
          ]
        },
        {
          name: T("一键注释", "One-click annotation"),
          one: T("一个基因，把 GO、表达、同源、文献汇总成一页。", "One gene, all layers on one page."),
          body: [
            T("整合 GO、表达、同源、跨库 ID 与关联文献。符号走 GRCz12tu；注释表仍为本地 GRCz11 库。",
              "Combines GO, expression, orthologs, cross-database IDs and related literature. Symbols follow GRCz12tu; annotation tables remain the local GRCz11 library.")
          ]
        },
        {
          name: T("News 检索台", "News"),
          one: T("看清楚 AI 读文献前，文章是怎么排序和打包的。", "See how papers are ranked and packed before the model reads them."),
          body: [
            T("实时流程：先用 FAISS 粗打分取 500 篇 → CrossEncoder 精排留 100 篇 → 按相关度与新旧合并打包后提交给 AI，与问答里用的是同一条通道。",
              "Live pipeline: FAISS coarse scoring narrows to 500 → CrossEncoder reranking keeps 100 → results are merged by relevance and recency into a packed context for the model, the same channel used by chat.")
          ]
        },
        {
          name: T("文献入库", "Upload literature"),
          one: T("上传自己的 PDF，进入本地知识库。", "Add your own PDFs to the local knowledge base."),
          body: [
            T("支持 PDF / TXT / MD，单文件 ≤5 MB，需 UTF-8——文件落到 uploads/ 与 knowledge.d，随后重建 RAG 索引。",
              "Accepts PDF / TXT / MD up to 5 MB, UTF-8 required. Files land in uploads/ and knowledge.d, then the RAG index is rebuilt."),
            T("组学身份库（ZFIN / Ensembl 等）走独立数据层，不经过这个入口。",
              "Genomic identity libraries (ZFIN / Ensembl and others) live in a separate layer and are not uploaded here.")
          ]
        },
        {
          name: T("运营看板", "Dashboard"),
          one: T("定时任务跑了没、今天多少人用。", "Whether the cron jobs ran, and how many visitors today."),
          body: [
            T("定时任务与访问统计都来自服务端只读接口；访问统计只返回计数，不含任何 IP 明细。",
              "Cron status and traffic come from read-only server endpoints. Traffic statistics are counts only — no IP details are stored.")
          ]
        }
      ]
    },

    /* ---------------- 4. 术语 ---------------- */
    {
      id: "terms",
      nav: T("术语与单位", "Terms & units"),
      title: T("几个会反复出现的词", "Words that keep coming up"),
      lead: T("看不懂某个标签时查这里。", "Check here when a label looks unfamiliar."),
      glossary: [
        [T("GRCz11 / GRCz12tu", "GRCz11 / GRCz12tu"), T("斑马鱼的两代参考装配。本地注释与坐标在 GRCz11；BLAST 与 cDNA 在 GRCz12tu。", "Two generations of the zebrafish reference assembly. Local annotation and coordinates sit on GRCz11; BLAST and cDNA on GRCz12tu.")],
        [T("Bgee 表达分", "Bgee score"), T("0–100 的相对表达分，同一基因在不同组织之间可以比。", "A 0–100 relative expression score, comparable across tissues within one gene.")],
        [T("TPM", "TPM"), T("每百万转录本数，Expression Atlas 用的单位；按胚胎分期给。", "Transcripts per million, the unit used by Expression Atlas, reported per developmental stage.")],
        [T("ORA / FDR", "ORA / FDR"), T("过表达分析；FDR 是多重检验校正后的显著性，看这一列比看 p 值稳。", "Over-representation analysis; FDR is the multiple-testing-corrected significance — trust it over raw p-values.")],
        [T("粗排 / 精排", "Coarse / rerank"), T("先用向量快速筛（粗排），再用交叉编码器逐篇细读（精排）。", "Vector search narrows the pool (coarse), then a cross-encoder reads each pair (rerank).")],
        [T("cDNA / 启动子 / 基因体", "cDNA / promoter / gene body"), T("转录本序列 / 转录起点上游 2 kb / 基因在基因组上的整段。", "Transcript sequence / 2 kb upstream of the TSS / the full genomic region.")]
      ]
    },

    /* ---------------- 5. 来源与引用 ---------------- */
    {
      id: "sources",
      nav: T("数据来源", "Data sources"),
      title: T("每个数字都能回原站核对", "Every number can be checked upstream"),
      lead: T("页面上出现的规模数字都是本机库实测快照，不是估计值。", "All counts shown in the interface are measured snapshots of the local library, not estimates."),
      /* 2026-10-27 去重：这份来源清单原本与「关于 → 数据来源」逐条重复。
         完整表（含版本、用途、每层怎么算）以关于页为准，这里只做指路。 */
      note: T("注释坐标来自 Ensembl，序列与 BLAST 库来自 NCBI RefSeq，基因身份与表型来自 ZFIN，表达来自 Bgee / Expression Atlas，通路来自 KEGG / WikiPathways / Reactome。",
        "Coordinates come from Ensembl, sequence and the BLAST library from NCBI RefSeq, gene identity and phenotype from ZFIN, expression from Bgee / Expression Atlas, pathways from KEGG / WikiPathways / Reactome."),
      cite: T("引用本平台时请注明访问日期与数据快照日期，并同时引用上列原始数据库。",
        "When citing this platform, include the access date and the data snapshot date, and cite the original databases above as well."),
      link: { href: "#/about/data", text: T("完整来源表（版本 · 用途 · 算法）", "Full source table (version · purpose · method)") }
    },

    /* ---------------- 6. 协议 ---------------- */
    {
      id: "terms-of-use",
      nav: T("使用协议", "Terms of use"),
      title: T("使用协议与隐私", "Terms of use and privacy"),
      lead: T("一段话说清，不写废话。", "Plain language throughout."),
      list: [
        T("本平台是研究预览版，用于科研信息检索，不构成临床诊断或用药建议。", "This is a research preview for scientific information retrieval. It is not clinical diagnosis or medical advice."),
        T("回答由模型基于本地知识库与公开数据库生成，可能有错。关键结论请回到原文或原站核对。", "Answers are model-generated from the local knowledge base and public databases and may be wrong. Verify critical conclusions against the original source."),
        T("上传的文献请确保你有权使用；文件仅用于本机知识库索引。", "Only upload literature you have the right to use. Files are used solely for the local knowledge base index."),
        T("访客对话只保存在本机浏览器；注册登录后对话存到自己的云端空间，按账号隔离。", "Guest chats are stored in this browser only. After signing in, chats are stored in your own cloud space, isolated per account."),
        T("访问统计只记计数，不记录 IP 明细。", "Usage statistics record counts only, never IP details.")
      ],
      foot: T("© 2026 Zebrafish Agent · College of Life Sciences, Hunan Normal University",
        "© 2026 Zebrafish Agent · College of Life Sciences, Hunan Normal University")
    }
  ];

  /* 2026-10-28：默认语言改为英文（i18n.js 同步改），存储键升到 hub_lang_v2。
     这里原来写死 lang="zh" 且只读旧键 hub_lang —— 结果 i18n 已经是英文了，
     文档页仍按中文渲染，出现「英文界面点开使用说明是中文」。 */
  var lang = "en";
  function curLang() {
    try {
      var v = localStorage.getItem("hub_lang_v2");
      if (v === "zh" || v === "en") return v;
    } catch (_) {}
    var d = (document.documentElement.getAttribute("lang") || "").toLowerCase();
    return d.indexOf("zh") === 0 ? "zh" : "en";
  }
  function S(t) { return t ? (t[lang] || t.zh || "") : ""; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function render() {
    lang = curLang();
    var body = document.getElementById("gdBody");
    var toc = document.getElementById("gdToc");
    if (!body) return;

    var head = {
      zh: { kicker: "使用说明", h: "这里说清每个功能怎么算", sub: "界面上只留最必要的一句话，口径、单位与边界都在这一页。对某个数字有疑问时再来查。" },
      en: { kicker: "Guide", h: "How each tool actually works", sub: "The interface keeps only the essentials. Definitions, units and limits live here — come back when a number looks odd." }
    }[lang] || {};

    var html = '<div class="gd-hero"><p class="gd-kicker">' + esc(head.kicker) +
      '</p><h2 class="gd-h">' + esc(head.h) + '</h2><p class="gd-sub">' + esc(head.sub) + "</p></div>";

    html += SECTIONS.map(function (sec) {
      var out = '<section class="gd-sec" id="gd-' + sec.id + '">';
      out += '<h3 class="gd-sec-h">' + esc(S(sec.title)) + "</h3>";
      if (sec.lead) out += '<p class="gd-lead">' + esc(S(sec.lead)) + "</p>";

      if (sec.steps) {
        out += '<ol class="gd-steps">' + sec.steps.map(function (s, i) {
          return '<li><span class="gd-num">' + (i + 1) + '</span><span class="gd-step-t">' + esc(S(s)) + "</span></li>";
        }).join("") + "</ol>";
      }

      if (sec.table) {
        out += '<div class="gd-table">' + sec.table.map(function (row) {
          return '<div class="gd-row"><span class="gd-row-k">' + esc(S(row[0])) +
            '</span><span class="gd-row-v">' + esc(S(row[1])) + "</span></div>";
        }).join("") + "</div>";
      }

      if (sec.mods) {
        out += '<div class="gd-mods">' + sec.mods.map(function (m) {
          return '<details class="gd-mod"><summary class="gd-mod-sum">' +
            '<span class="gd-mod-name">' + esc(S(m.name)) + "</span>" +
            '<span class="gd-mod-one">' + esc(S(m.one)) + "</span>" +
            '<span class="gd-caret" aria-hidden="true">▾</span></summary>' +
            '<div class="gd-mod-body">' + (m.body || []).map(function (p) {
              return "<p>" + esc(S(p)) + "</p>";
            }).join("") + "</div></details>";
        }).join("") + "</div>";
      }

      if (sec.glossary) {
        out += '<dl class="gd-gloss">' + sec.glossary.map(function (g) {
          return '<div class="gd-gloss-item"><dt>' + esc(S(g[0])) + "</dt><dd>" + esc(S(g[1])) + "</dd></div>";
        }).join("") + "</dl>";
      }

      if (sec.list) {
        out += '<ul class="gd-list">' + sec.list.map(function (x) {
          return "<li>" + esc(S(x)) + "</li>";
        }).join("") + "</ul>";
      }

      if (sec.note) out += '<p class="gd-note">' + esc(S(sec.note)) + "</p>";
      if (sec.cite) out += '<p class="gd-note">' + esc(S(sec.cite)) + "</p>";
      if (sec.link) {
        out += '<p class="gd-foot"><a class="gd-link" href="' + sec.link.href + '">' +
          esc(S(sec.link.text)) + " →</a></p>";
      }
      if (sec.foot) out += '<p class="gd-foot">' + esc(S(sec.foot)) + "</p>";
      return out + "</section>";
    }).join("");

    body.innerHTML = html;

    if (toc) {
      toc.innerHTML = SECTIONS.map(function (sec, i) {
        return '<a class="gd-toc-a" href="#/guide/' + sec.id + '" data-gd="' + sec.id + '">' +
          '<span class="gd-toc-n">' + (i + 1) + "</span>" + esc(S(sec.nav)) + "</a>";
      }).join("");
    }
    activeFromHash();
    // 重复点同一节目录时 hash 不变、不会触发 hashchange，这里补一次定位
    document.querySelectorAll(".gd-toc-a").forEach(function (a) {
      a.addEventListener("click", function () { setTimeout(activeFromHash, 0); });
    });
  }

  function activeFromHash() {
    var h = (location.hash || "").split("/")[2] || "";
    document.querySelectorAll(".gd-toc-a").forEach(function (a) {
      a.classList.toggle("active", a.getAttribute("data-gd") === h);
    });
    if (h) {
      var el = document.getElementById("gd-" + h);
      // 滚动容器是 #view-guide 本身（overflow:auto），window.scrollTo 对它无效，
      // 必须用 scrollIntoView；scroll-margin-top 已在 CSS 里留出呼吸位。
      if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }

  /* 点击目录：hash 变化时 app.js 会重新 applyRoute，这里只负责滚动与高亮 */
  document.addEventListener("hub-lang-change", render);
  document.addEventListener("DOMContentLoaded", render);
  if (document.readyState !== "loading") render();
  window.addEventListener("hashchange", function () {
    if ((location.hash || "").indexOf("#/guide") === 0) activeFromHash();
  });
  window.ZFGuide = { render: render, sections: SECTIONS };
})();
