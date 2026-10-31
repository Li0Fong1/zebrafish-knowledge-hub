/* Zebrafish Hub · 来源徽标（M1/M2 共用单一事实源）
   用途：任何结果/证据都标注「哪个库 + 版本 + 可点链接」，避免各页面各写一套拼链接逻辑。
   用法：
     ZFBadges.render(document.querySelector("#x"), [{db:"ensembl", id:"ENSDARG00000035559"}]);
     el.innerHTML = ZFBadges.html([{db:"zfin", id:"ZDB-GENE-990415-270"}, {db:"local", id:"gene_basic"}]);
   —— db 取值见 REGISTRY；未登记的 db 会渲染为纯文本徽标，不会丢信息。 */
(function () {
  "use strict";

  var REG = {
    ensembl: {
      label: "Ensembl", version: "GRCz11 r116",
      url: function (id) { return "https://www.ensembl.org/Danio_rerio/Gene/Summary?g=" + String(id).split(".")[0]; }
    },
    zfin: {
      label: "ZFIN", version: "ZDB",
      url: function (id) { return "https://zfin.org/" + id; }
    },
    ncbi_gene: {
      label: "NCBI Gene", version: "Entrez",
      url: function (id) { return "https://www.ncbi.nlm.nih.gov/gene/" + String(id).replace(/[^\d]/g, ""); }
    },
    ncbi_refseq: {
      label: "NCBI RefSeq", version: "GRCz12tu",
      url: function () { return "https://www.ncbi.nlm.nih.gov/datasets/genome/GCF_049306965.1/"; }
    },
    uniprot: {
      label: "UniProt", version: "2026_01",
      url: function (id) { return "https://www.uniprot.org/uniprotkb/" + id + "/entry"; }
    },
    bgee: {
      label: "Bgee", version: "v15.2",
      url: function (id) { return "https://www.bgee.org/gene/" + String(id).split(".")[0]; }
    },
    atlas: {
      label: "Expression Atlas", version: "E-ERAD-475",
      url: function (id) { return "https://www.ebi.ac.uk/gxa/genes/" + String(id).split(".")[0]; }
    },
    quickgo: {
      label: "QuickGO", version: "GO",
      url: function (id) { return "https://www.ebi.ac.uk/QuickGO/term/" + id; }
    },
    kegg: {
      label: "KEGG", version: "dre",
      url: function (id) { return "https://www.kegg.jp/entry/" + id; }
    },
    wikipathways: {
      label: "WikiPathways", version: "zebrafish",
      url: function (id) { return "https://www.wikipathways.org/pathways/" + id + ".html"; }
    },
    pubmed: {
      label: "PubMed", version: "PMID",
      url: function (id) { return "https://pubmed.ncbi.nlm.nih.gov/" + String(id).replace(/[^\d]/g, "") + "/"; }
    },
    local: { label: "本地库", label_en: "Local library", version: "GRCz11 r116", url: null, local: true },
    local_faiss: { label: "本地全文库", label_en: "Local full-text library", version: "qwen3_4b_v2_short_section", url: null, local: true }
  };

  /* 双语选择器：徽标会出现在「数据来源与算法」块里，而那块自己管文案（data-no-i18n），
     所以徽标标签必须在源头就是双语的。 */
  function T(zh, en) {
    if (window.HubI18n && typeof window.HubI18n.T === "function") return window.HubI18n.T(zh, en);
    var l = (document.documentElement.getAttribute("lang") || "en").toLowerCase();
    return l.indexOf("zh") === 0 ? zh : en;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function badge(s) {
    var db = (s && s.db) || "local";
    var meta = REG[db] || { label: db, version: "" };
    var id = s && s.id ? String(s.id) : "";
    var ver = s && s.version ? s.version : meta.version;
    var url = (s && s.url) || (meta.url && id ? meta.url(id) : null);
    var label = T(meta.label, meta.label_en || meta.label);
    var inner = '<span class="src-db">' + esc(label) + "</span>" +
      (ver ? ' <span class="src-ver">' + esc(ver) + "</span>" : "");
    var title = (label + (ver ? " · " + ver : "") + (id ? " · " + id : "")).trim();
    if (url) {
      return '<a class="src-badge" href="' + esc(url) + '" target="_blank" rel="noopener" title="' + esc(title) + '">' + inner + "</a>";
    }
    return '<span class="src-badge' + (meta.local ? " is-local" : "") + '" title="' + esc(title) + '">' + inner + "</span>";
  }

  function html(list) {
    if (!list || !list.length) return "";
    return list.map(badge).join("");
  }

  function render(target, list) {
    var el = typeof target === "string" ? document.querySelector(target) : target;
    if (!el) return;
    el.innerHTML = html(list);
    el.classList.add("src-row");
  }

  /* About 弹层与溯源块用的登记资料（版本/快照来自仓库内真实产物） */
  window.ZF_BADGES_DATA = {
    snapshot: "2026-09-30（本地库与索引快照）",
    sources: [
      { name: "Ensembl", version: "GRCz11 · release 116", used: "基因坐标 / 转录本 / GO / 同源" },
      { name: "NCBI RefSeq", version: "GRCz12tu · GCF_049306965.1", used: "标准名解析 / BLAST 核酸库", url: "https://www.ncbi.nlm.nih.gov/datasets/genome/GCF_049306965.1/" },
      { name: "ZFIN", version: "ZDB-GENE", used: "基因身份与别名权威", url: "https://zfin.org/" },
      { name: "Bgee", version: "v15.2 Expression Score (0–100)", used: "组织 / 发育表达", url: "https://www.bgee.org/" },
      { name: "Expression Atlas", version: "E-ERAD-475 (TPM)", used: "胚胎时期表达", url: "https://www.ebi.ac.uk/gxa/home" },
      { name: "QuickGO", version: "GO 注释", used: "功能注释", url: "https://www.ebi.ac.uk/QuickGO/" },
      { name: "KEGG / WikiPathways", version: "dre", used: "通路", url: "https://www.kegg.jp/" },
      { name: "PubMed / Europe PMC", version: "全文 32,532 篇", used: "文献全文与精排证据", url: "https://pubmed.ncbi.nlm.nih.gov/" }
    ],
    pipeline: [
      { step: "文献清洗", detail: "切分前切除作者行 / 单位 / 参考文献 / 致谢，并按章节识别与加权" },
      { step: "切分", detail: "按自然段与章节切分：360 / 110 字符、重叠 48（index qwen3_4b_v2_short_section）" },
      { step: "向量化", detail: "Qwen3-Embedding-4B → 2048 维；170 个 FAISS 分片，共 7,651,942 个语义块" },
      { step: "检索", detail: "按篇粗排取前 500 → 交叉编码器精排取前 100 → 48,000 字符预算打包" },
      { step: "作答", detail: "固定答案契约：首句直答、按因素分点、行内 [n]、文末参考文献无悬空引用" }
    ]
  };

  window.ZFBadges = { REG: REG, badge: badge, html: html, render: render };
})();
