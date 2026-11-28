/* ==========================================================================
   about.js —— About 页（2026-11-09 简化版）
   单张卡片、三小节顺序呈现：平台简介 / 数据规模 / 数据来源 / 引用方式。
   去掉了原来的三个 tab、后台说明小字、算法与口径表、免责段和底部链接。
   数字沿用 2026-11-04 本机实测快照，与各功能页同口径。
   ========================================================================== */
(function () {
  'use strict';

  var TEXT = {
    zh: {
      title: 'Zebrafish Agent',
      lead: '斑马鱼基因知识中枢：把本地注释、表达、表型与文献全文放在同一入口，每条答案都能回溯到库内记录或原始数据。',
      scale: '数据规模',
      colItem: '对象', colValue: '规模',
      sources: '数据来源',
      cite: '引用方式',
      citeBlock: 'Zebrafish Agent (斑马鱼基因知识中枢). Zebrafish gene knowledge hub: local annotation, expression, phenotype and literature retrieval. 2026. Access date: {date}.'
    },
    en: {
      title: 'Zebrafish Agent',
      lead: 'A zebrafish gene knowledge hub: local annotation, expression, phenotype and full-text literature behind one entry point. Every answer links back to a library record or its original source.',
      scale: 'Data scale',
      colItem: 'Item', colValue: 'Scale',
      sources: 'Data sources',
      cite: 'How to cite',
      citeBlock: 'Zebrafish Agent (斑马鱼基因知识中枢). Zebrafish gene knowledge hub: local annotation, expression, phenotype and literature retrieval. 2026. Access date: {date}.'
    }
  };

  /* 只保留「对象 / 规模」两列，删掉原来第三列的长段来源说明 */
  var SCALE = [
    { zh: ['基因注释库', '21,088 个基因 · 32,139 条记录'],
      en: ['Gene annotation', '21,088 genes · 32,139 records'] },
    { zh: ['别名与同源表', '118,174 条别名 · 31,456 条同源'],
      en: ['Aliases & homology', '118,174 aliases · 31,456 homology rows'] },
    { zh: ['表达表', '541,347 行 · 25,247 个基因（含 ZFIN 222,004 行）'],
      en: ['Expression', '541,347 rows · 25,247 genes (including 222,004 ZFIN rows)'] },
    { zh: ['GO 注释', '248,885 条 · 22,576 个基因（Ensembl + ZFIN 合并集）'],
      en: ['GO annotations', '248,885 rows · 22,576 genes (Ensembl + ZFIN union)'] },
    { zh: ['表型注释', '171,079 条 · 8,029 个基因'],
      en: ['Phenotype annotations', '171,079 rows · 8,029 genes'] },
    { zh: ['基因组与染色体', '25 条染色体 · 1,448,791,966 bp'],
      en: ['Genome & chromosomes', '25 chromosomes · 1,448,791,966 bp'] },
    { zh: ['BLAST 库', '转录组 51.9 MB · 全基因组 345.5 MB'],
      en: ['BLAST libraries', 'transcriptome 51.9 MB · genome 345.5 MB'] },
    { zh: ['文献库', '32,964 篇题录 · 32,549 篇全文金标'],
      en: ['Literature library', '32,964 records · 32,549 full-text entries'] }
  ];

  /* 只列数据源名称，不再展示算法与口径 */
  var SOURCES = [
    'Ensembl GRCz11 r116',
    'NCBI RefSeq GRCz12tu',
    'ZFIN (identity, phenotype, wild-type expression, gene-to-GO)',
    'Bgee v15.2 / Expression Atlas E-ERAD-475',
    'Ensembl GO / ZFIN gene-to-GO / KEGG / WikiPathways / Reactome',
    'PubMed / Europe PMC'
  ];

  function curLang() {
    var l = (document.documentElement.getAttribute('lang') || 'en').toLowerCase();
    return l.indexOf('zh') === 0 ? 'zh' : 'en';
  }
  function T(k) { var d = TEXT[curLang()] || TEXT.en; return d[k] != null ? d[k] : (TEXT.en[k] || ''); }
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
  function pick(pair) { return curLang() === 'zh' ? pair.zh : pair.en; }

  function routeName() {
    return ((location.hash || '').replace(/^#\/?/, '').split(/[/?#]/)[0]) || 'home';
  }
  function isAbout() { return routeName() === 'about'; }

  function syncBodyClass() {
    // About 单栏模式：隐藏左输入卡、放开结果区高度；离开 About 时还原
    document.body.classList.toggle('about-single', isAbout());
  }

  function render() {
    syncBodyClass();
    var host = $('toolResult');
    if (!host) return;
    // 路由守卫：延迟回调不得把 About 内容画到别的功能页
    if (!isAbout()) return;
    // 左侧原来的三个 tab 按钮整块隐藏，页面只剩一张结果卡片
    var panel = $('toolAbout');
    if (panel) panel.classList.add('hidden');

    var rows = SCALE.map(function (s) {
      var p = pick(s);
      return '<tr><td>' + esc(p[0]) + '</td><td>' + esc(p[1]) + '</td></tr>';
    }).join('');
    var chips = SOURCES.map(function (s) {
      return '<span>' + esc(s) + '</span>';
    }).join('');
    var today = new Date().toISOString().slice(0, 10);

    host.innerHTML =
      // 内容由本文件按语言自行渲染，必须挡住全局 i18n 的二次翻译
      '<div class="about-clean" data-no-i18n="1">' +
        '<h4 class="about-h">' + esc(T('title')) + '</h4>' +
        '<p class="about-lead">' + esc(T('lead')) + '</p>' +
        '<h4 class="about-h">' + esc(T('scale')) + '</h4>' +
        '<table class="about-table"><thead><tr><th>' + esc(T('colItem')) +
          '</th><th>' + esc(T('colValue')) + '</th></tr></thead><tbody>' + rows +
        '</tbody></table>' +
        '<h4 class="about-h">' + esc(T('sources')) + '</h4>' +
        '<div class="about-sources">' + chips + '</div>' +
        '<h4 class="about-h">' + esc(T('cite')) + '</h4>' +
        '<pre class="about-cite">' + esc(T('citeBlock').replace('{date}', today)) + '</pre>' +
      '</div>';
    host.classList.remove('hidden');
  }

  function ensureRendered() {
    // app.js 打开功能页时会清空 #toolResult，时机可能晚于首轮渲染，补一次
    if (!isAbout()) return;
    var host = $('toolResult');
    if (host && host.querySelector('.about-clean')) return;
    render();
  }
  function scheduleRender() {
    setTimeout(render, 80);
    setTimeout(ensureRendered, 350);
    setTimeout(ensureRendered, 1000);
  }

  function boot() {
    if (!$('toolAbout')) return;
    function onRoute() {
      syncBodyClass();
      if (isAbout()) scheduleRender();
    }
    document.addEventListener('hub-lang-change', function () {
      if (isAbout()) render();
    });
    document.addEventListener('zf-feature-open', onRoute);
    window.addEventListener('hashchange', onRoute);
    syncBodyClass();
    if (isAbout()) scheduleRender();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
