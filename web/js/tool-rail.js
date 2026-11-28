/* ==========================================================================
   tool-rail.js —— feature 页常显工具栏
   --------------------------------------------------------------------------
   为什么有这个文件：功能和路由一直都在（14 个工具页 + 3 个组学页），
   但入口全被塞进顶部下拉菜单里，用户"看不到有多少功能"，会以为被砍掉了。
   这里把全部工具页铺成一条常显的横向工具栏：一眼可见、一点直达、当前页高亮。
   标签按语言直接渲染（不走 i18n 词典），并在 hub-lang-change 时重绘。
   ========================================================================== */
(function () {
  'use strict';

  /* 顺序 = 日常使用频次：查询 → 组学 → 序列/设计 → 注释 → 文献 → 入库 → 运维 */
  var ITEMS = [
    { route: 'gene', zh: '基因工作台', en: 'Gene workbench', href: '#/gene' },
    { route: 'genes', zh: '基因库搜索', en: 'Gene search', href: '#/genes' },
    { route: 'genomes', zh: '基因组总览', en: 'Genomes', href: '#/genomes' },
    { route: 'expression', zh: '表达谱', en: 'Expression', href: '#/expression' },
    { route: 'phenotype', zh: '基因–表型', en: 'Phenotype', href: '#/phenotype' },
    { route: 'blast', zh: 'BLAST 比对', en: 'BLAST', href: '#/blast' },
    { route: 'sequence', zh: '序列提取', en: 'Sequence', href: '#/sequence' },
    { route: 'id', zh: '跨库 ID 转换', en: 'Batch ID convert', href: '#/id' },
    { route: 'enrich', zh: 'GO', en: 'GO', href: '#/enrich' },
    { route: 'kegg', zh: '通路富集', en: 'Pathway enrichment', href: '#/kegg' },
    { route: 'crispr', zh: 'sgRNA / 引物', en: 'sgRNA / primers', href: '#/crispr' },
    { route: 'annotate', zh: '一键注释', en: 'Annotate', href: '#/annotate' },
    { route: 'literature', zh: '文献检索台', en: 'Literature', href: '#/literature' },
    { route: 'upload', zh: '入库', en: 'Ingest', href: '#/upload' },
    { route: 'ops', zh: '运维看板', en: 'Operations', href: '#/ops' },
    { route: 'about', zh: '平台说明', en: 'About', href: '#/about' }
  ];

  /* 按「用途」分两组，每组一行 —— 比 16 个 pill 平铺更看得出结构 */
  var GROUPS = [
    { zh: '查询与序列', en: 'Find & sequence',
      keys: ['gene', 'genes', 'genomes', 'expression', 'phenotype', 'blast', 'sequence', 'id'] },
    { zh: '分析与数据', en: 'Analysis & data',
      keys: ['enrich', 'kegg', 'crispr', 'annotate', 'literature', 'upload', 'ops', 'about'] }
  ];

  function itemOf(route) {
    for (var i = 0; i < ITEMS.length; i++) if (ITEMS[i].route === route) return ITEMS[i];
    return null;
  }

  function lang() {
    var l = (document.documentElement.getAttribute('lang') || 'en').toLowerCase();
    return l.indexOf('zh') === 0 ? 'zh' : 'en';
  }
  function currentRoute() {
    var raw = (location.hash || '#/home').replace(/^#\/?/, '');
    return raw.split(/[/?#]/)[0] || 'home';
  }

  function render() {
    var host = document.getElementById('toolRail');
    if (!host) return;
    var zh = lang() === 'zh';
    var cur = currentRoute();
    var rows = GROUPS.map(function (g) {
      var links = g.keys.map(function (k) {
        var it = itemOf(k);
        if (!it) return '';
        var active = it.route === cur;
        return '<a class="tool-rail-item' + (active ? ' is-active' : '') + '" href="' + it.href +
          '" data-rail="' + it.route + '"' + (active ? ' aria-current="page"' : '') + '>' +
          (zh ? it.zh : it.en) + '</a>';
      }).join('');
      return '<div class="tool-rail-row">' +
        '<span class="tool-rail-label">' + (zh ? g.zh : g.en) + '</span>' +
        '<span class="tool-rail-items">' + links + '</span></div>';
    }).join('');
    host.innerHTML = rows +
      '<span class="tool-rail-count">' + (zh
        ? ITEMS.length + ' 个工具 · 点击直达'
        : ITEMS.length + ' tools · select to open') + '</span>';

    // 顺带驱动「本页数据」卡：tool-rail 的 render 已验证在各种时序下都会跑，
    // 比 page-meta 自己监听事件更可靠（首次直接开 URL 时事件早于 defer 脚本）。
    try {
      if (window.ZFPageMeta && typeof window.ZFPageMeta.load === 'function') {
        window.ZFPageMeta.load(cur);
      }
    } catch (err) { /* 数据卡失败不影响工具栏 */ }
  }

  function boot() {
    if (!document.getElementById('toolRail')) return;
    render();
    // 首帧时 page-meta 可能还没就绪，稍后再驱动一次数据卡
    setTimeout(render, 240);
    window.addEventListener('hashchange', function () { setTimeout(render, 40); });
    document.addEventListener('zf-feature-open', function () { setTimeout(render, 40); });
    document.addEventListener('hub-lang-change', function () { setTimeout(render, 40); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  window.ZFToolRail = { render: render, items: ITEMS };
})();
