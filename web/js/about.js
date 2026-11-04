/* ==========================================================================
   about.js —— About 真页面（定位与范围 / 数据来源与算法 / 引用方式）
   --------------------------------------------------------------------------
   为什么有这个文件：顶部 About 菜单原来 3 个条目全部指向 #/home（点了没内容），
   属于"有三角形没内容"。这里把它做成真页面，三章内容都写实：
   数字是本机库实测快照（与线上一致），每个数据源都给出可核对的原始站点。
   ========================================================================== */
(function () {
  'use strict';

  var TEXT = {
    zh: {
      tabScope: '定位与范围', tabData: '数据来源与算法', tabCite: '引用方式',
      scopeTitle: '这个平台是做什么的',
      scopeLead: '斑马鱼基因知识中枢：把「本地注释库 + 表达数据 + 表型注释 + 文献全文库」放在同一入口。' +
        '先给出可复核的结论与数字，再把每条结论指回库里或原站——不做只有标题的搜索结果。',
      scopeH3: '数据规模（本机实测快照 2026-11-04）',
      scopeNote: '这些数字都可以在对应页面看到同样口径的数值：基因库 → 基因组总览页；表达 → 表达谱页；' +
        '表型 → 基因–表型页；文献 → News。',
      dataTitle: '数据来源与算法',
      dataLead: '外部数据一律以「本地镜像 + 版本标注」的方式使用：镜像落盘后本地查询，页面同时写明装配版本与口径，' +
        '避免出现"同一个基因两套坐标混用"这类 silently wrong。',
      citeTitle: '引用本平台',
      citeScope: '平台能力：搜索基因、跑 BLAST、读文献精排；答案自动挂本库与 ZFIN / Ensembl / NCBI 等组学库双链（资料库 ≠ 仅 PubMed）。',
      citeLead: '若本项目对您的研究有帮助，请按下面方式引用；同时请引用您实际用到的原始数据库（Ensembl / NCBI / ZFIN / Bgee 等）。',
      citeBlock: 'Zebrafish Agent (斑马鱼基因知识中枢). Zebrafish gene knowledge hub: local annotation, expression, phenotype and literature retrieval. 2026. Access date: {date}.',
      citeNote: '原始数据库建议同时引用：Ensembl（基因组与注释）、NCBI RefSeq（GRCz12tu 序列）、ZFIN（原位与表型注释）、' +
        'Bgee 与 Expression Atlas（表达）、KEGG / WikiPathways（通路）、QuickGO（GO）。',
      discTitle: '边界与免责',
      discBody: '本平台的注释、预测评分与富集结果均为「计算结论」，可用于筛选与假设生成；' +
        '是否成立需回到实验与原始文献验证。缺失数据一律显示为「无记录」，不做零值或推测填充。',
      colItem: '对象', colValue: '规模', colSrc: '来源 / 说明'
    },
    en: {
      tabScope: 'Scope', tabData: 'Data & methods', tabCite: 'How to cite',
      scopeTitle: 'What this platform does',
      scopeLead: 'A zebrafish gene knowledge hub: local annotation, expression, phenotype and full-text literature behind one entry point. ' +
        'Every answer carries checkable numbers and links back to the library or the source site — no title-only search results.',
      scopeH3: 'Data scale (measured on this machine, snapshot 2026-11-04)',
      scopeNote: 'The same figures use the same definitions on their pages: genes → Genome overview; expression → Expression; ' +
        'phenotypes → Gene–phenotype; literature → Literature console.',
      dataTitle: 'Data & methods',
      dataLead: 'External data is always used as a local mirror with an explicit version label, so two coordinate systems never get mixed silently.',
      citeTitle: 'How to cite',
      citeScope: 'What it does: search genes, run BLAST, read reranked literature; every answer links back to this library and to ZFIN / Ensembl / NCBI (this is a knowledge base, not just PubMed).',
      citeLead: 'If this platform helped your research, please cite it as below, and also cite the original databases you actually used (Ensembl / NCBI / ZFIN / Bgee …).',
      citeBlock: 'Zebrafish Agent (斑马鱼基因知识中枢). Zebrafish gene knowledge hub: local annotation, expression, phenotype and literature retrieval. 2026. Access date: {date}.',
      citeNote: 'Recommended co-citations: Ensembl (genome & annotation), NCBI RefSeq (GRCz12tu sequences), ZFIN (in-situ & phenotype), ' +
        'Bgee and Expression Atlas (expression), KEGG / WikiPathways (pathways), QuickGO (GO).',
      discTitle: 'Limits & disclaimer',
      discBody: 'Annotations, scores and enrichment results are computational conclusions meant for screening and hypothesis generation; ' +
        'they need experimental and literature confirmation. Missing data is always shown as “no record”, never as a filled-in zero or a guess.',
      colItem: 'Item', colValue: 'Scale', colSrc: 'Source / note'
    }
  };

  /* 实测快照：与各页面显示口径一致 */
  var SCALE = [
    /* 2026-11-04 三次核对：基因数改用「有正式符号」口径。
       21,088 = gene_basic.name_kind='symbol'，可与 NCBI Gene / ZFIN 的命名基因数直接对照。
       32,139 是记录数：另有 7,493 条克隆 / EST 号、92 条 LOC 占位名、1,839 条
       Ensembl 编号代名、1,627 条 ZFIN 编号代名 —— 都是真实位点，但不是命名基因，
       报「基因总数」时不能算进去。22,370 条带唯一 UniProt 蛋白编号（2,779 条
       Swiss-Prot 审阅），30,159 条有 ZFIN 官方编号，23,950 条带 GRCz12tu 坐标。 */
    { zh: ['基因注释库', '21,088 个基因 · 32,139 条记录', '21,088 条有正式基因符号，可与 NCBI / ZFIN 直接对照；另有 7,493 条克隆 / EST 旧名、3,466 条以编号代名的记录（仍可按旧名或编号检索）。22,370 条带 UniProt 蛋白编号（2,779 条经 Swiss-Prot 审阅），30,159 条有 ZFIN 官方编号，23,950 条带 GRCz12tu 坐标（RefSeq 原始坐标 + GRCz11 注释经碱基级 liftover）'],
      en: ['Gene annotation', '21,088 genes · 32,139 records', '21,088 carry a formal gene symbol and can be matched directly against NCBI / ZFIN; a further 7,493 are legacy clone / EST entries and 3,466 are named only by an identifier (still searchable by old name or ID). 22,370 carry a UniProt protein accession (2,779 Swiss-Prot reviewed), 30,159 carry an official ZFIN ID and 23,950 carry GRCz12tu coordinates (native RefSeq positions plus GRCz11 annotations projected by base-level liftover)'] },
    { zh: ['别名与同源表', '142,614 条别名 / 31,456 条同源', 'gene_alias / homology_gene（用于把旧名、别名、复制体解析到标准符号）'],
      en: ['Aliases & homology', '142,614 aliases / 31,456 homology rows', 'gene_alias / homology_gene — resolves old names, aliases and paralogs'] },
    { zh: ['表达表', '319,343 行 / 24,089 个基因', 'Bgee 表达分（0–100）+ Expression Atlas TPM（E-ERAD-475，marker 子集）'],
      en: ['Expression table', '319,343 rows / 24,089 genes', 'Bgee score (0–100) + Expression Atlas TPM (E-ERAD-475, marker subset)'] },
    { zh: ['表型注释', '171,079 条 / 8,029 个基因', 'ZFIN phenoGeneCleanData_fish.txt 本地解析为 SQLite 索引；20,971 个「解剖实体+PATO」组合'],
      en: ['Phenotype annotations', '171,079 rows / 8,029 genes', 'ZFIN phenoGeneCleanData_fish.txt parsed into a local SQLite index; 20,971 entity+quality pairs'] },
    { zh: ['基因组与染色体', '25 条染色体 / 1,448,791,966 bp', '本机 GRCz12tu FASTA 实测长度与 GC（平均 37.11%）'],
      en: ['Genome & chromosomes', '25 chromosomes / 1,448,791,966 bp', 'length and GC measured on the local GRCz12tu FASTA (mean 37.11%)'] },
    { zh: ['BLAST 库', '转录组 51.9 MB / 全基因组 345.5 MB', 'BLAST+ 2.17.0+ 本地核酸库（GRCz12tu CDS / 基因组）'],
      en: ['BLAST libraries', 'transcriptome 51.9 MB / genome 345.5 MB', 'local nucleotide DBs built with BLAST+ 2.17.0+ (GRCz12tu CDS / genome)'] },
    { zh: ['文献库', '32,549 篇元数据 · 29,221 篇全文金标', 'PubMed / Europe PMC 过滤后入库；FAISS 粗排 500 → cross-encoder 精排 100'],
      en: ['Literature library', '32,549 records · 29,221 full-text gold', 'filtered PubMed / Europe PMC; FAISS coarse top 500 → cross-encoder rerank 100'] }
  ];

  var SOURCES = [
    { zh: ['Ensembl GRCz11 r116', '基因身份与 GO / 通路注释；坐标经碱基级 liftover 呈现于 GRCz12tu'], en: ['Ensembl GRCz11 r116', 'gene identity with GO / pathway annotation; coordinates presented on GRCz12tu via base-level liftover'] },
    { zh: ['NCBI RefSeq GRCz12tu (GCF_049306965.2)', '标准符号、CDS / 基因组序列、染色体登录号'], en: ['NCBI RefSeq GRCz12tu (GCF_049306965.2)', 'standard symbols, CDS / genome sequences, chromosome accessions'] },
    { zh: ['ZFIN', '基因命名与原位、表型注释（ZDB-PUB 可核对）'], en: ['ZFIN', 'gene naming, in-situ and phenotype annotations (ZDB-PUB IDs are checkable)'] },
    { zh: ['Bgee / Expression Atlas', '组织表达分与胚胎分期 TPM'], en: ['Bgee / Expression Atlas', 'tissue expression scores and embryonic-stage TPM'] },
    { zh: ['QuickGO / KEGG dre / WikiPathways', 'GO 术语与通路编号'], en: ['QuickGO / KEGG dre / WikiPathways', 'GO terms and pathway identifiers'] },
    { zh: ['PubMed / Europe PMC', '文献元数据与摘要；全文入库前先按「以斑马鱼为对象」过滤'], en: ['PubMed / Europe PMC', 'record metadata and abstracts; full texts are filtered for zebrafish-focused studies'] }
  ];

  var METHODS = [
    { zh: ['BLAST 比对', 'BLAST+ 2.17.0+，默认不做跨装配隐式回退（GRCz12 → GRCz11 需显式开启），命中坐标标注库版本'],
      en: ['BLAST', 'BLAST+ 2.17.0+ with no implicit cross-assembly fallback (GRCz12 → GRCz11 must be enabled explicitly); hit coordinates keep the library version'] },
    { zh: ['富集分析', 'ORA：Fisher 精确检验 + Benjamini–Hochberg 校正，报告校正后 q 值；背景集 = 本地有注释的基因'],
      en: ['Enrichment', "ORA: Fisher's exact test + Benjamini–Hochberg correction with adjusted q values; background = locally annotated genes"] },
    { zh: ['文献检索', '粗排 500 篇 → cross-encoder 精排 100 篇 → 按 48,000 字符打包；表内分数 adjusted_L2 = raw_L2 / 章节权重'],
      en: ['Literature retrieval', 'coarse top 500 → cross-encoder rerank 100 → packed to 48,000 characters; scores are adjusted_L2 = raw_L2 / section weight'] },
    { zh: ['表型匹配', 'ZFIN 符号与本地符号可能不同（例：tp53 / p53）→ 符号、别名、跨库 ID 多候选匹配并返回实际命中符号'],
      en: ['Phenotype matching', 'ZFIN symbols may differ from local ones (e.g. tp53 / p53) → multi-candidate matching over symbols, aliases and cross-DB IDs, reporting the symbol actually matched'] },
    { zh: ['单位口径', 'Bgee 是 0–100 表达存在性置信分，Expression Atlas 才是 TPM；两者分块呈现，不跨组比较'],
      en: ['Units', 'Bgee reports a 0–100 expression-existence confidence score; only Expression Atlas reports TPM. The two are shown in separate blocks and never compared'] }
  ];

  function curLang() {
    var l = (document.documentElement.getAttribute('lang') || 'en').toLowerCase();
    return l.indexOf('zh') === 0 ? 'zh' : 'en';
  }
  function T(k) { var d = TEXT[curLang()] || TEXT.en; return d[k] != null ? d[k] : (TEXT.en[k] || ''); }
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function pick(pair) { return curLang() === 'zh' ? pair.zh : pair.en; }

  var tab = 'scope';
  var TABS = { scope: 1, data: 1, cite: 1 };

  function routeArg() {
    var raw = (location.hash || '').replace(/^#\/?/, '');
    var parts = raw.split(/[/?#]/);
    return (parts[1] || '').toLowerCase();
  }
  function syncFromRoute() {
    var a = routeArg();
    // 2026-10-31：入口合并成单个「平台说明」后，从导航点进来（#/about 无章节参数）
    // 一律从第一章看起；带章节的旧书签（#/about/data）仍然直达对应章节。
    tab = TABS[a] ? a : 'scope';
  }

  function block(title, sub, body) {
    return '<div class="zf-block">' + (title ? '<h4 class="zf-block-title">' + esc(title) + '</h4>' : '') +
      (sub ? '<p class="zf-block-sub">' + esc(sub) + '</p>' : '') + body + '</div>';
  }
  function table(heads, rows) {
    return '<div class="table-wrap"><table class="gene-table"><thead><tr>' +
      heads.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('') +
      '</tr></thead><tbody>' + rows.map(function (r) {
        return '<tr>' + r.map(function (c, i) {
          return i === 0 ? '<td><strong>' + esc(c) + '</strong></td>' : '<td>' + esc(c) + '</td>';
        }).join('') + '</tr>';
      }).join('') + '</tbody></table></div>';
  }

  function routeName() {
    return ((location.hash || '').replace(/^#\/?/, '').split(/[/?#]/)[0]) || 'home';
  }
  function isAbout() { return routeName() === 'about'; }

  function render() {
    var host = $('toolResult');
    if (!host) return;
    // 路由守卫：延迟回调（hashchange / 语言切换）不得把 About 内容画到别的功能页
    if (!isAbout()) return;
    var html = '';
    if (tab === 'data') {
      html = block(T('dataTitle'), T('dataLead'),
        table(curLang() === 'zh' ? ['数据源', '用在哪'] : ['Source', 'Used for'], SOURCES.map(pick)) +
        '<h4 class="zf-block-title" style="margin-top:18px">' +
        esc(curLang() === 'zh' ? '算法与口径' : 'Algorithms & conventions') + '</h4>' +
        table(curLang() === 'zh' ? ['环节', '做法'] : ['Step', 'How it works'], METHODS.map(pick)));
    } else if (tab === 'cite') {
      var today = new Date().toISOString().slice(0, 10);
      html = block(T('citeTitle'), T('citeLead'),
        '<p class="zf-note">' + esc(T('citeScope')) + '</p>' +
        '<pre class="about-cite">' + esc(T('citeBlock').replace('{date}', today)) + '</pre>' +
        '<p class="zf-note">' + esc(T('citeNote')) + '</p>') +
        block(T('discTitle'), '', '<p class="zf-note">' + esc(T('discBody')) + '</p>');
    } else {
      html = block(T('scopeTitle'), T('scopeLead'),
        '<h4 class="zf-block-title" style="margin-top:14px">' + esc(T('scopeH3')) + '</h4>' +
        table([T('colItem'), T('colValue'), T('colSrc')],
          SCALE.map(function (s) { var p = pick(s); return [p[0], p[1], p[2]]; })) +
        '<p class="zf-note">' + esc(T('scopeNote')) + '</p>');
    }
    host.innerHTML = html +
      // 2026-10-31：顶部 About 下拉合并为单一直达入口后，「使用说明 / 协议 / 数据来源」
      // 在进站后就没有别的入口了（欢迎门上的三条链接关门即不可见），在这里补一条常驻链接。
      '<p class="zf-note" style="margin-top:16px"><a href="#/guide" data-guide-open>' +
      esc(curLang() === 'zh' ? '使用说明 · 使用协议 · 数据来源 →' : 'Guide · Terms · Data sources →') +
      '</a></p>';
    host.classList.remove('hidden');
    syncTabs();
  }

  function syncTabs() {
    Array.prototype.forEach.call(document.querySelectorAll('.about-tab'), function (b) {
      b.classList.toggle('active', b.getAttribute('data-about-tab') === tab);
      b.textContent = T('tab' + b.getAttribute('data-about-tab').charAt(0).toUpperCase() +
        b.getAttribute('data-about-tab').slice(1));
    });
  }

  function boot() {
    if (!$('toolAbout')) return;
    Array.prototype.forEach.call(document.querySelectorAll('.about-tab'), function (b) {
      b.addEventListener('click', function () {
        tab = b.getAttribute('data-about-tab') || 'scope';
        // 章节写进 URL（#/about/data …）但不触发 hashchange：页面已渲染好，
        // 再让 app.js 重进一次功能页只会闪一下。replaceState 只同步地址栏，供刷新和分享。
        try { history.replaceState(null, '', '#/about/' + tab); } catch (e) { /* file:// 下忽略 */ }
        render();
      });
    });
    document.addEventListener('hub-lang-change', function () {
      if (!isAbout()) return;
      syncTabs(); render();
    });
    document.addEventListener('zf-feature-open', function (e) {
      if ((e && e.detail && e.detail.route) !== 'about') return;
      // 延后一拍：app.js 打开页面时会先清空 #toolResult，同步渲染会被它清掉
      setTimeout(function () { syncFromRoute(); render(); }, 80);
    });
    window.addEventListener('hashchange', function () {
      var active = ((location.hash || '').replace(/^#\/?/, '').split(/[/?#]/)[0]) || 'home';
      if (active !== 'about') return;
      setTimeout(function () { syncFromRoute(); render(); }, 80);
    });
    var active = ((location.hash || '').replace(/^#\/?/, '').split(/[/?#]/)[0]) || 'home';
    if (active === 'about') { syncFromRoute(); setTimeout(render, 80); }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
