/* ==========================================================================
   page-meta.js —— 功能页左栏「本页数据」卡（借鉴 PotatoOmics 的 Default locus 参数表）
   --------------------------------------------------------------------------
   土豆每个功能页左栏都先摆一张数据元信息表（Assembly / Size / Genes / DOI），
   让人一眼知道"这页有什么数据、口径是什么"。这里给 16 个页面都补上：
     · 静态事实：版本、覆盖对象数、口径（都是本库实测过的真实数字）
     · 动态事实：能取真数据的页面直接打接口（genomes / expression / phenotype / literature）
   标签用中英双语自带，值多为数字或英文标识（天然双语），因此不进 i18n 词典。
   ========================================================================== */
(function () {
  'use strict';

  /* 导出放在最前：即使后面某段代码异常，路由主控（app.js）仍能调到 load */
  window.ZFPageMeta = { load: load, static: STATIC, live: LIVE };

  /* 静态事实：[标签zh, 标签en, 值] */
  var STATIC = {
    genomes: [
      ['装配', 'Assembly', 'GRCz12tu (GCF_049306965.2)'],
      ['序列数', 'Sequences', '25（1–25 号染色体）'],
      ['总长度', 'Total length', '1.45 Gb'],
      ['平均 GC', 'Mean GC', '37.11%'],
      ['已注释基因', 'Annotated genes', '53,513'],
      ['本地 BLAST 库', 'BLAST libraries', 'cdna 51.9 MB / genome 345.5 MB']
    ],
    expression: [
      ['表达记录', 'Expression rows', '319,343'],
      ['基因数', 'Genes', '24,089'],
      ['组织 / 解剖实体', 'Tissues (Bgee)', '1,151'],
      ['Bgee 单位', 'Bgee unit', 'score 0–100'],
      ['Atlas 单位', 'Atlas unit', 'TPM (E-ERAD-475)']
    ],
    phenotype: [
      ['索引', 'Index', 'SQLite · ready'],
      ['注释条目', 'Annotations', '171,079'],
      ['覆盖基因', 'Genes', '8,029'],
      ['表型术语组合', 'Entity + quality pairs', '20,971'],
      ['数据源', 'Source', 'ZFIN phenoGeneCleanData_fish']
    ],
    gene: [
      ['基因库', 'Genes', '21,088'],
      ['别名', 'Aliases', '118,174'],
      ['同源 / 复制体', 'Orthologs / paralogs', '31,456'],
      ['坐标版本', 'Coordinate assembly', 'NCBI RefSeq GRCz12tu'],
      ['符号版本', 'Symbol assembly', 'NCBI RefSeq GRCz12tu'],
      ['命中口径', 'Match fields', 'symbol / alias / cross-DB ID']
    ],
    genes: [
      ['基因库', 'Genes', '21,088'],
      ['别名索引', 'Alias index', '118,174'],
      ['可检索 ID', 'Searchable IDs', 'ZFIN / NCBI / UniProt / Ensembl'],
      ['染色体编号', 'Chromosomes', '1–25 + scaffolds']
    ],
    blast: [
      ['引擎', 'Engine', 'BLAST+ 2.17.0+'],
      ['转录组库', 'Transcriptome DB', 'cdna_db · 51.9 MB'],
      ['基因组库', 'Genome DB', 'genome_db · 345.5 MB'],
      ['建库源序列', 'Source FASTA', 'GRCz12tu CDS / genome'],
      ['跨装配回退', 'Cross-assembly fallback', 'off（需显式开启）']
    ],
    sequence: [
      ['序列来源', 'Sequence source', 'GRCz12tu CDS'],
      ['坐标来源', 'Coordinate source', 'GRCz11（含链方向）'],
      ['支持类型', 'Region types', 'cDNA / promoter / genomic'],
      ['版本标注', 'Version tag', 'yes']
    ],
    enrich: [
      ['统计方法', 'Statistics', 'ORA · Fisher + Benjamini–Hochberg'],
      ['背景集', 'Background set', '本地有注释的基因'],
      ['注释来源', 'Annotation', 'GO + KEGG / WikiPathways'],
      ['通路条目', 'Gene–pathway rows', '36,793']
    ],
    kegg: [
      ['通路编号', 'Pathway IDs', 'KEGG dre / WikiPathways'],
      ['基因–通路条目', 'Gene–pathway rows', '36,793'],
      ['GO 注释条目', 'GO annotations', '110,632']
    ],
    idresolve: [
      ['覆盖库', 'Databases', 'Ensembl / ZFIN / NCBI / UniProt'],
      ['消歧口径', 'Resolution', '唯一命中 / 一对多 / 未命中'],
      ['装配映射表', 'Assembly xref', '30,525 条']
    ],
    annotate: [
      ['汇总内容', 'Aggregates', '身份 · 表达 · GO · 通路 · 同源 · 文献'],
      ['缺失处理', 'Missing values', '写「无记录」，不填充推测值']
    ],
    crispr: [
      ['引擎', 'Engine', '本地引擎（透明规则）'],
      ['PAM', 'PAMs', 'NGG / NAG / TTTV / NNGRRT'],
      ['打分维度', 'Scoring axes', 'GC · poly-T · 二级结构 · 位置权重'],
      ['脱靶筛查库', 'Off-target DBs', 'cdna_db / genome_db']
    ],
    retrieval: [
      ['文献库', 'Library', '32,549 篇元数据'],
      ['全文金标', 'Full-text gold', '29,221 篇'],
      ['粗排 → 精排', 'Coarse → rerank', '500 → 100'],
      ['打包上限', 'Packed size', '48,000 字符'],
      ['索引分片', 'Index shards', '170']
    ],
    upload: [
      ['接受格式', 'Accepted types', 'PDF / TXT / MD'],
      ['单文件上限', 'Size limit', '5 MB'],
      ['切分规则', 'Chunking', '360 / 110 字符 · overlap 48'],
      ['生效前提', 'Required', '重建向量分片']
    ],
    ops: [
      ['定时任务', 'Cron jobs', '文献周更 · 图文库 · 统计 · 守护'],
      ['访问统计', 'Visit stats', '最近访问日志计数'],
      ['隐私口径', 'Privacy', '不返回、不展示任何 IP 明细']
    ],
    about: [
      ['快照日期', 'Snapshot', '2026-10-15'],
      ['数据源', 'Sources', 'Ensembl / NCBI / ZFIN / Bgee / QuickGO / KEGG'],
      ['引用', 'Citation', '见「引用方式」章节']
    ]
  };
  /* 动态事实：page → { url, map(data) -> facts } */
  var LIVE = {
    genomes: {
      url: '/api/genome/overview',
      map: function (d) {
        var libs = (d.libraries || []).filter(function (l) { return l.ready; });
        var libTxt = libs.map(function (l) { return l.size_mb + ' MB'; }).join(' / ');
        return [
          ['装配', 'Assembly', d.assembly || '—'],
          ['序列数', 'Sequences', (d.n_sequences || 0) + ' / ' + ((d.chromosomes || []).length) + ' numbered'],
          ['总长度', 'Total length', (d.total_length_gb || '—') + ' Gb'],
          ['平均 GC', 'Mean GC', (d.mean_gc == null ? '—' : d.mean_gc + '%')],
          ['已注释基因', 'Annotated genes', String(d.gene_total || '—')],
          ['本地 BLAST 库', 'BLAST libraries', libTxt || '—']
        ];
      }
    },
    expression: {
      url: '/api/expression/tissues',
      map: function (d) {
        return [
          ['表达记录', 'Expression rows', '319,343'],
          ['基因数', 'Genes', '24,089'],
          ['组织 / 解剖实体', 'Tissues (Bgee)', String((d && d.count) || '1,151')],
          ['Bgee 单位', 'Bgee unit', 'score 0–100'],
          ['Atlas 单位', 'Atlas unit', 'TPM (E-ERAD-475)']
        ];
      }
    },
    phenotype: {
      url: '/api/phenotype/stats',
      map: function (d) {
        var built = d.built_at || (d.meta && d.meta.built_at) || '—';
        return [
          ['索引状态', 'Index', d.index_ready ? 'ready' : 'not built'],
          ['注释条目', 'Annotations', String(d.rows == null ? '—' : d.rows)],
          ['覆盖基因', 'Genes', String(d.genes == null ? '—' : d.genes)],
          ['表型术语组合', 'Entity + quality pairs', String(d.terms == null ? '—' : d.terms)],
          ['构建时间', 'Built at', built],
          ['数据源', 'Source', 'ZFIN phenoGeneCleanData_fish']
        ];
      }
    },
    literature: {
      url: '/api/hub/research-feed',      map: function (d) {
        var st = d.stats || {};
        var facts = [
          ['文献库', 'Library', '32,549 篇元数据'],
          ['全文金标', 'Full-text gold', '29,221 篇'],
          ['粗排 → 精排', 'Coarse → rerank', '500 → 100']
        ];
        if (st.pool != null) {
          facts.push(['当日候选池', "Today's pool",
            String(st.pool) + (lang() === 'zh' ? ' 篇' : ' papers')]);
        }
        if (st.catalog_lines != null) facts.push(['题录行数', 'Catalog lines', String(st.catalog_lines)]);
        return facts;
      }
    }
  };

  function lang() {
    var l = (document.documentElement.getAttribute('lang') || 'en').toLowerCase();
    return l.indexOf('zh') === 0 ? 'zh' : 'en';
  }
  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function routeName() {
    return ((location.hash || '').replace(/^#\/?/, '').split(/[/?#]/)[0]) || 'home';
  }
  /* 路由 → 事实键（文献检索台与 literature 路由共用一份） */
  var ROUTE_KEY = { literature: 'retrieval' };

  /* 取（必要时自建）数据卡容器：挂在输入卡体末尾，保证一定可见 */
  function hostEl() {
    var h = document.getElementById('featureAside');
    if (h) return h;
    var m = document.getElementById('featureMount');
    if (!m || !m.parentNode) return null;
    h = document.createElement('div');
    h.id = 'featureAside';
    h.className = 'feature-aside';
    m.parentNode.insertBefore(h, m.nextSibling);
    return h;
  }

  /* 静态事实里的少量中文值 → 英文（英文模式下替换；中文模式原样） */
  var VALUE_EN = {
    '25（1–25 号染色体）': '25 (chr 1–25)',
    '本地有注释的基因': 'locally annotated genes',
    '唯一命中 / 一对多 / 未命中': 'unique / one-to-many / not found',
    '身份 · 表达 · GO · 通路 · 同源 · 文献': 'identity · expression · GO · pathways · orthologs · literature',
    '写「无记录」，不填充推测值': 'shown as “no record”, never filled with guesses',
    '本地引擎（透明规则）': 'local engine (transparent rules)',
    'GC · poly-T · 二级结构 · 位置权重': 'GC · poly-T · secondary structure · position weight',
    '32,549 篇元数据': '32,549 records',
    '29,221 篇': '29,221 full-text',
    '48,000 字符': '48,000 chars',
    '360 / 110 字符 · overlap 48': '360 / 110 chars · overlap 48',
    '重建向量分片': 'vector shards rebuild',
    '文献周更 · 图文库 · 统计 · 守护': 'weekly literature / gallery / stats / guard',
    '最近访问日志计数': 'counts over recent access logs',
    '不返回、不展示任何 IP 明细': 'no IP details returned or shown',
    '见「引用方式」章节': 'see the Citation section',
    'GRCz11（含链方向）': 'GRCz11 (strand included)',
    'off（需显式开启）': 'off (explicit opt-in)',
    '30,525 条': '30,525',
    '25 条 / 1.45 Gb': '25 / 1.45 Gb'
  };

  function render(route, facts) {
    var host = hostEl();
    if (!host) {
      // hero 还没挂载：重试几次
      render._tries = (render._tries || 0) + 1;
      if (render._tries <= 8) setTimeout(function () { load(route); }, 220);
      return;
    }
    render._tries = 0;
    var zh = lang() === 'zh';
    if (!facts || !facts.length) {
      // 自诊断：事实缺失也留一行，便于区分“脚本没跑”与“数据缺失”
      host.innerHTML = '<div class="fk-item"><span class="fk-v">—</span><span class="fk-k">' +
        esc(zh ? ('本页数据待补（' + route + '）') : ('no facts for ' + route)) + '</span></div>';
      host.hidden = false;
      return;
    }
    var items = facts.slice(0, 6).map(function (f) {
      var label = zh ? f[0] : f[1];
      var value = f[2];
      if (!zh && VALUE_EN[value]) value = VALUE_EN[value];
      return '<div class="fk-item"><span class="fk-v">' + esc(value) + '</span>' +
        '<span class="fk-k">' + esc(label) + '</span></div>';
    }).join('');
    host.innerHTML = items;
    host.hidden = false;
  }

  var lastRoute = null;
  function load(route) {
    if (!route) return;
    lastRoute = route;
    var key = route;
    if (route === 'literature') {
      // 文献检索台既有静态兜底，又能拉当日 feed 统计
      render(route, STATIC.retrieval);
      fetch(LIVE.literature.url, { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (routeName() !== route) return;
          var facts = LIVE.literature.map(d) || [];
          render(route, facts.length ? facts : STATIC.retrieval);
        })
        .catch(function () { /* 保留静态事实 */ });
      return;
    }
    var live = LIVE[key];
    if (!live) { render(route, STATIC[key]); return; }
    render(route, STATIC[key]);                       // 先用静态顶上，不空白
    fetch(live.url, { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (routeName() !== route) return;            // 路由守卫：切页后不再画
        var facts = (live.map(d) || []);
        // 动态成功就用动态（不再拼静态，否则同一项会显示两遍）；动作为空时回退静态兜底
        render(route, facts.length ? facts : (STATIC[key] || []));
      })
      .catch(function () { /* 接口失败就保留静态事实 */ });
  }

  function boot() {
    document.addEventListener('zf-feature-open', function (e) {
      if (!route || route === 'home' || route === 'chat') {
        var host = document.getElementById('featureKpis');
        if (host) { host.hidden = true; host.innerHTML = ''; }
        return;
      }
      setTimeout(function () { load(routeName()); }, 90);
    });
    document.addEventListener('hub-lang-change', function () {
      if (lastRoute) load(lastRoute);
    });
    window.addEventListener('hashchange', function () {
      var r = routeName();
      if (r && r !== 'home' && r !== 'chat') setTimeout(function () { load(routeName()); }, 120);
    });
    var r = routeName();
    if (r && r !== 'home' && r !== 'chat') {
      // 多时机尝试，避开外壳挂载 / 清空之争端
      [120, 420, 900].forEach(function (ms) {
        setTimeout(function () { load(routeName() || r); }, ms);
      });
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
