/* Zebrafish Hub · 工具页统一溯源块（M2）
   目标：每个工具页都回答同一组问题——「数据从哪来 / 什么版本 / 我们怎么算的」，
   而不是各页各写一套说法。文案与版本号只在 source-badges.js 的 ZF_BADGES_DATA 里维护。
   实现：DOMContentLoaded 后为每个 .tool-panel 追加一次 <details class="provenance">，
   重复挂载会先移除旧块（幂等）。 */
(function () {
  "use strict";

  var PANELS = {
    toolGene: {
      title: "基因位置",
      sources: ["local", "ensembl", "zfin", "ncbi_refseq"],
      how: [
        "基因身份以 ZFIN / Ensembl 为准；符号按 NCBI RefSeq GRCz12tu 归一，坐标与注释保留 GRCz11（release 116）",
        "表达分两组，单位不同：Bgee Expression Score（0–100，表达存在性置信分）、Expression Atlas TPM（整张图以整胚时间序列为主）",
        "GO / 通路 / 同源来自 Ensembl、QuickGO、KEGG/WikiPathways 的本地镜像表"
      ]
    },
    toolBlast: {
      title: "BLAST 序列比对",
      sources: ["ncbi_refseq", "local"],
      how: [
        "比对库：GRCz12tu 标准转录本 / 基因组核酸库（本地建库，BLAST+ 2.17.0+）",
        "默认不做跨版本回退（GRCz12 → GRCz11 需显式开启），避免坐标错配",
        "命中位点坐标随库版本标注，未鉴定的旁系同源单独提示"
      ]
    },
    toolEnrich: {
      title: "GO 富集",
      sources: ["quickgo", "kegg", "wikipathways", "local"],
      how: [
        "背景集 = 本地注释库全部有 GO/KEGG 注释的基因（非全基因组）",
        "统计：Fisher 精确检验 + Benjamini–Hochberg 校正，报告校正后 q 值",
        "通路成员来自 KEGG dre / WikiPathways zebrafish / Reactome 镜像"
      ]
    },
    toolKegg: {
      title: "通路浏览",
      sources: ["kegg", "wikipathways", "local"],
      how: [
        "通路分类与编号直接采用 KEGG dre / WikiPathways 原始编号，便于对照原站",
        "基因–通路关系来自本地镜像表，覆盖度以本地快照为准"
      ]
    },
    toolIdResolve: {
      title: "跨库 ID 解析",
      sources: ["ensembl", "zfin", "ncbi_gene", "uniprot", "local"],
      how: [
        "解析三层：唯一命中 / 一对多歧义（列出候选）/ 未命中（不猜测）",
        "Ensembl ↔ ZFIN ↔ RefSeq ↔ UniProt 映射以镜像表为准；GRCz12tu 符号与 GRCz11 ID 分开标注"
      ]
    },
    toolSequence: {
      title: "位置查找序列",
      sources: ["ensembl", "ncbi_refseq", "local"],
      how: [
        "以 GRCz11 坐标从参考 cDNA / 基因组 FASTA 提取，正反向链与坐标一并返回",
        "未做坐标转换的序列不标注为 GRCz12tu"
      ]
    },
    toolAnnotate: {
      title: "一键注释",
      sources: ["local", "ensembl", "zfin", "bgee", "atlas", "quickgo", "kegg"],
      how: [
        "把身份、表达、GO、通路、同源、文献按同一套来源徽标汇总",
        "所有数值均在界面上带单位与来源；缺项显式写「无记录」，不填充推测值"
      ]
    },
    toolRetrieval: {
      title: "News 检索",
      sources: ["pubmed", "local_faiss", "local"],
      how: [
        "文献来源：PubMed / Europe PMC（按「以斑马鱼为研究对象或模型」标准筛选后入库）+ 本地上传入库文献",
        "检索流程：按篇 FAISS 粗排前 500 → bge-reranker-base 交叉编码器精排前 100 → 按相关度与章节权重打包 48,000 字符",
        "索引 qwen3_4b_v2_short_section：切片 360／110 字符、重叠 48、2048 维、170 个分片",
        "表中分数为 adjusted_L2 = raw_L2 / 章节权重（标题 2.0、摘要 1.5、结论 1.5、结果 1.45 …），只在同一索引内可比"
      ]
    },
    toolUpload: {
      title: "文献入库",
      sources: ["local_faiss", "local"],
      how: [
        "接受 PDF / TXT / MD（≤5 MB，UTF-8）：先做类型与大小校验，再解析（PyMuPDF / pypdf / pdfplumber / lxml）",
        "随后走与正式入库完全相同的清洗与切分规则（360／110 字符、重叠 48），写入 knowledge.d 与 uploads/",
        "入库后需重建向量分片才会被检索命中；未索引的文件会在结果中标注「已入库 / 待索引」",
        "组学库（ZFIN / Ensembl / NCBI / Bgee 等）是独立数据层，不经此通道上传；登录用户的知识库按账号隔离"
      ]
    },
    toolOps: {
      title: "运营看板",
      sources: ["local", "pubmed"],
      how: [
        "定时任务由服务器 crontab 驱动：文献周更（每周日 03:20）、图文库刷新（每 3 天 04:10）、统计重算（每小时）、存活守护（每分钟）",
        "运行状态读服务端只读接口 /api/hub/maintenance/status（文献规模、最近任务与时间）",
        "访问统计读 /api/hub/visit-stats：只统计最近 N 条访问日志的计数（请求数、独立来源数、状态分布、Top 路由）",
        "隐私口径：服务端与本页都**不返回、不展示任何 IP 明细**；因此不提供「访客地图」——那需要地理库并在合规前提下使用"
      ]
    },
    toolGenomes: {
      title: "Liftover",
      sources: ["local", "ensembl", "ncbi_refseq"],
      how: [
        "染色体长度 / GC 含量：直接扫本机 GRCz12tu 基因组 FASTA 逐条统计（不用目录值，不用 CSV 假设），缓存文件带 mtime 失效重算",
        "染色体编号从 FASTA 头部解析（如 “chromosome 5”），不按行号硬编号；表里的 RefSeq 登录号（NC_…）可直接到 NCBI 核对",
        "基因数按本地注释库 GRCz11 r116 的 chromosome 字段 GROUP BY 统计；GRCz12tu 长度与 GRCz11 基因数坐标系不同，已在页面与说明中标明，不做隐式换算",
        "文库大小读自本地 BLAST 库文件字节数（含 cdna / genome 核酸库与 CDS），与实际比对所用库一致"
      ]
    },
    toolExpression: {
      title: "表达",
      sources: ["bgee", "atlas", "local"],
      how: [
        "两组数据单位不同，分开呈现：Bgee 为表达分 0–100（表达存在性置信分，按解剖实体）；Expression Atlas 为 TPM（按胚胎分期，仅 marker 基因子集）",
        "Bgee 行里 expression_stage 存的是质量分而非分期，所以不把两者混进同一个坐标轴；单位在每个区块标题旁标注",
        "表达谱按分值降序，取分值最高的组织；多基因对比只使用共同覆盖的组织，未覆盖处写「无记录」而不补 0",
        "基因名先经本地基因库解析（符号 / 别名 / 跨库 ID）再查表达，避免同名或旧符号错配"
      ]
    },
    toolPhenotype: {
      title: "基因表型",
      sources: ["zfin", "pubmed", "local"],
      how: [
        "表型注释 = ZFIN 官方 phenoGeneCleanData_fish.txt，本地解析为 SQLite 索引（写入用临时文件 + os.replace 原子替换，失败不破坏旧索引）",
        "一行 = 一条「表型–基因–基因型–分期」注释；页面按表型 / 等位基因分组去重，并给出记录条数作为证据强度",
        "表型术语保留 ZFIN / PATO / ZFA 原始编号，不翻译、不改写，便于到 zfin.org 逐条核对",
        "文献编号为 ZFIN 的 ZDB-PUB；文献命中来自本地文献库题名匹配（含年份与 PubMed 链接），与表型注释本身相互独立"
      ]
    },
    toolAbout: {
      title: "关于本平台",
      sources: ["local", "ensembl", "ncbi_refseq", "zfin", "bgee", "atlas", "pubmed"],
      how: [
        "数据规模数字均为本机库实测快照（注释库、别名/同源表、表达表、ZFIN 表型索引、FASTA 实测染色体、BLAST 库、文献库）",
        "外部数据一律以「带版本标注的本地镜像」使用（坐标用 Ensembl GRCz11 r116，符号与序列用 NCBI RefSeq GRCz12tu），不让两套坐标系静默混用",
        "算法与口径逐条写明：BLAST+ 2.17.0+、ORA 用 Fisher + Benjamini–Hochberg、文献粗排 500 → 精排 100、表型多候选符号匹配",
        "单位分块标注：Bgee 是 0–100 表达存在性分，Expression Atlas 才是 TPM；缺失数据一律写「无记录」，不做零值填充"
      ]
    }
  };

  var DEFAULT = {
    title: "本页",
    sources: ["local"],
    how: ["数据来自本地镜像库，版本与快照见下方说明"]
  };

  /* 英文版：中文模式下显示中文，英文模式（默认）显示英文。
     这块文案在 JS 里拼句子（含标题拼接），交给词典逐字翻译会碎，
     所以在源头就把两语言都写好。 */
  var PANELS_EN = {
    toolGene: {
      title: "Gene locus",
      how: [
        "Gene identity follows ZFIN / Ensembl; symbols are normalized to NCBI RefSeq GRCz12tu, while coordinates and annotations stay on GRCz11 (release 116)",
        "Expression comes in two groups with different units: Bgee Expression Score (0–100, confidence that expression exists) and Expression Atlas TPM (mostly whole-embryo time series)",
        "GO / pathway / ortholog annotations come from local mirrors of Ensembl, QuickGO and KEGG/WikiPathways"
      ]
    },
    toolBlast: {
      title: "BLAST sequence alignment",
      how: [
        "Search databases: GRCz12tu standard transcripts and the genomic nucleotide library, both built locally with BLAST+ 2.17.0+",
        "No cross-version fallback by default (GRCz12 → GRCz11 must be enabled explicitly) to avoid coordinate mismatches",
        "Hit coordinates are labelled with the library version; unidentified paralogs are flagged separately"
      ]
    },
    toolEnrich: {
      title: "GO enrichment",
      how: [
        "Background set = every gene with GO/KEGG annotation in the local annotation library (not the whole genome)",
        "Statistics: Fisher's exact test + Benjamini–Hochberg correction; adjusted q values are reported",
        "Pathway members come from local mirrors of KEGG dre / WikiPathways zebrafish / Reactome"
      ]
    },
    toolKegg: {
      title: "Pathway browser",
      how: [
        "Pathway categories and IDs keep the original KEGG dre / WikiPathways numbering so they can be checked against the source sites",
        "Gene–pathway relations come from local mirror tables; coverage follows the local snapshot"
      ]
    },
    toolIdResolve: {
      title: "Cross-DB ID resolution",
      how: [
        "Three-level resolution: unique hit / one-to-many ambiguity (candidates listed) / not found (never guessed)",
        "Ensembl ↔ ZFIN ↔ RefSeq ↔ UniProt mapping follows the mirror tables; GRCz12tu symbols and GRCz11 IDs are labelled separately"
      ]
    },
    toolSequence: {
      title: "Sequence by locus",
      how: [
        "Extracted with GRCz11 coordinates from the reference cDNA / genome FASTA; both strands and coordinates are returned",
        "Sequences without coordinate conversion are never labelled as GRCz12tu"
      ]
    },
    toolAnnotate: {
      title: "One-shot annotation",
      how: [
        "Collects identity, expression, GO, pathways, orthologs and literature under one consistent set of source badges",
        "Every value carries its unit and source in the UI; missing entries are shown as “no record” and never filled with guesses"
      ]
    },
    toolRetrieval: {
      title: "News search",
      how: [
        "Sources: PubMed / Europe PMC (ingested after filtering for studies that use zebrafish as subject or model) plus locally uploaded documents",
        "Pipeline: per-paper FAISS coarse ranking top 500 → bge-reranker-base cross-encoder top 100 → packed to 48,000 characters by relevance and section weight",
        "Index qwen3_4b_v2_short_section: 360/110-character chunks, overlap 48, 2048 dimensions, 170 shards",
        "Table scores are adjusted_L2 = raw_L2 / section weight (title 2.0, abstract 1.5, conclusion 1.5, results 1.45 …) and are only comparable within the same index"
      ]
    },
    toolUpload: {
      title: "Document ingestion",
      how: [
        "Accepts PDF / TXT / MD (≤5 MB, UTF-8): type and size validation first, then parsing (PyMuPDF / pypdf / pdfplumber / lxml)",
        "Then the exact same cleaning and chunking rules as production ingestion (360/110 characters, overlap 48), written to knowledge.d and uploads/",
        "Uploaded files only become searchable after the vector shards are rebuilt; unindexed files are marked “ingested / pending index”",
        "Omics databases (ZFIN / Ensembl / NCBI / Bgee …) are a separate data layer and are not uploaded here; knowledge bases are isolated per account"
      ]
    },
    toolOps: {
      title: "Operations dashboard",
      how: [
        "Jobs are driven by the server crontab: weekly literature update (Sundays 03:20), gallery refresh (every 3 days 04:10), stats recompute (hourly), liveness guard (every minute)",
        "Runtime state is read from the read-only endpoint /api/hub/maintenance/status (literature scale, latest jobs and timestamps)",
        "Visit stats come from /api/hub/visit-stats: counts over the most recent N access-log lines (requests, unique clients, status classes, top routes)",
        "Privacy: neither the server nor this page returns or shows any IP detail; there is no visitor map, because that would need a geo database and a compliance basis"
      ]
    },
    toolGenomes: {
      title: "Liftover",
      how: [
        "Chromosome length / GC: measured by scanning the local GRCz12tu genome FASTA sequence by sequence (no catalogue values, no CSV assumptions); the cache invalidates on mtime change",
        "Chromosome numbers are parsed from FASTA headers (e.g. “chromosome 5”), never assigned from line order; the RefSeq accessions (NC_…) can be checked directly at NCBI",
        "Gene counts come from GROUP BY on the chromosome column of the local GRCz11 r116 annotation library; GRCz12tu lengths and GRCz11 gene counts use different coordinate systems, which is stated on the page instead of being silently converted",
        "Library sizes are read from the local BLAST database files (cDNA / genome nucleotide libraries and CDS), matching the libraries actually used for searches"
      ]
    },
    toolExpression: {
      title: "Expression",
      how: [
        "Two data sets with different units are kept apart: Bgee reports an expression score 0–100 (confidence that expression exists, by anatomical entity); Expression Atlas reports TPM (by embryonic stage, marker-gene subset only)",
        "In Bgee rows the expression_stage column holds a quality score rather than a stage, so the two are never plotted on one axis; each block is labelled with its unit",
        "Profiles are sorted by score and show the highest-scoring tissues; multi-gene comparison only uses tissues covered by all genes, and uncovered ones read “no record” rather than a filled-in zero",
        "Gene names are first resolved through the local gene library (symbol / alias / cross-DB ID) before expression is queried, avoiding same-name or outdated-symbol mismatches"
      ]
    },
    toolPhenotype: {
      title: "Gene phenotype",
      how: [
        "Phenotype annotations = the official ZFIN phenoGeneCleanData_fish.txt, parsed locally into a SQLite index (written to a temp file then swapped in with os.replace, so a failed build never damages the previous index)",
        "One row = one phenotype–gene–genotype–stage annotation; the page groups these by phenotype / allele and shows the record count as a measure of evidence strength",
        "Phenotype terms keep their original ZFIN / PATO / ZFA identifiers — never translated or rewritten — so each can be checked at zfin.org",
        "Publication IDs are ZFIN ZDB-PUB records; literature hits come from local paper-title matching (with year and PubMed link) and are independent of the phenotype annotations themselves"
      ]
    },
    toolAbout: {
      title: "About this platform",
      how: [
        "Data scale figures are measured snapshots of the local libraries (annotation, aliases/homology, expression, ZFIN phenotype index, FASTA-measured chromosomes, BLAST libraries, literature)",
        "External data is used as a version-labelled local mirror (Ensembl GRCz11 r116 for coordinates, NCBI RefSeq GRCz12tu for symbols and sequences), so two coordinate systems are never mixed silently",
        "Algorithms and their conventions are listed explicitly: BLAST+ 2.17.0+, ORA with Fisher + Benjamini–Hochberg, coarse-500 → rerank-100 literature retrieval, multi-candidate phenotype symbol matching",
        "Units are stated per block: Bgee reports a 0–100 expression-existence score while Expression Atlas reports TPM; missing data is shown as “no record”, never zero-filled"
      ]
    }
  };

  var DEFAULT_EN = {
    title: "this page",
    sources: ["local"],
    how: ["Data comes from the local mirror library; versions and snapshot are listed below"]
  };

  function isZh() {
    if (window.HubI18n && typeof window.HubI18n.isZh === "function") return window.HubI18n.isZh();
    var l = (document.documentElement.getAttribute("lang") || "en").toLowerCase();
    return l.indexOf("zh") === 0;
  }

  function badgesHtml(list) {
    if (window.ZFBadges && typeof window.ZFBadges.html === "function") {
      return window.ZFBadges.html(list.map(function (db) { return { db: db }; }));
    }
    return "";
  }

  function build(info) {
    var d = window.ZF_BADGES_DATA || {};
    var zh = isZh();
    var en = PANELS_EN[info.__id] || DEFAULT_EN;
    var el = document.createElement("details");
    el.className = "provenance";
    el.setAttribute("data-no-i18n", "1");   // 本块自己出双语文案，不交给词典逐字翻
    var list = (zh ? info.how : en.how) || [];
    var items = list.map(function (t) { return "<li>" + t + "</li>"; }).join("");
    el.innerHTML =
      "<summary>" + (zh ? "数据来源与算法（" + info.title + "）" : "Data sources & algorithms (" + en.title + ")") + "</summary>" +
      '<div class="prov-head">' + badgesHtml(info.sources || []) + "</div>" +
      "<ul>" + items + "</ul>" +
      '<div class="prov-head"><strong>' + (zh ? "快照" : "Snapshot") + '</strong><span>' + (d.snapshot || "—") + "</span></div>" +
      '<div class="prov-head"><strong>' + (zh ? "说明" : "Note") + "</strong><span>" +
      (zh ? "单位为原始库单位；跨库数值不可直接比较，缺记录处不填充推测值。"
          : "Values use each source's native units; cross-database numbers are not directly comparable, and missing records are never filled with guesses.") +
      "</span></div>";
    return el;
  }

  /* 2026-10-28 用户要求：各功能页底下那个「▶ 数据来源与算法（…）」折叠块全部下线。
     2026-10-30 补充：Liftover 页一并下线（用户改口，要求与其余功能页一致）。
     白名单现在为空 —— 全站都不挂；以后要恢复某页，把面板 id 加进这里即可。 */
  var KEEP_PANELS = {};

  function mount() {
    Object.keys(PANELS).forEach(function (id) {
      var panel = document.getElementById(id);
      if (!panel) return;
      if (KEEP_PANELS[id] !== 1) return;       // 不在白名单：不挂
      var old = panel.querySelector(":scope > .provenance");
      if (old) old.remove();
      var info = PANELS[id];
      info.__id = id;                        // build() 用它取英文版
      panel.appendChild(build(info));
    });
    // 未登记的面板给一份通用说明，避免出现"没有来源"的页面
    Array.prototype.forEach.call(document.querySelectorAll(".tool-panel"), function (panel) {
      if (KEEP_PANELS[panel.id] !== 1) return;
      if (!panel.querySelector(":scope > .provenance")) panel.appendChild(build(DEFAULT));
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
  // 语言切换后重建（文案是 JS 拼的，重建比逐字翻译可靠）
  document.addEventListener("hub-lang-change", mount);
  window.ZFProvenance = { mount: mount, PANELS: PANELS };
})();
