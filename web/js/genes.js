/* ==========================================================================
   genes.js —— Genes 功能页（基因库搜索 / 浏览 / 基因卡）
   --------------------------------------------------------------------------
   数据来源：
     GET /api/genes/search?q=&limit=&browse=1   → 32,139 条基因记录的符号 / 别名 / ZFIN / Ensembl 检索
                                                  （返回按 matched_by 分级：同义 / 名字相关 / 仅提及）
     POST /api/genes/search/batch               → 多行批量，逐条按同一套分级判定
     GET /api/genes/region?assembly=&chrom=&start=&end= → 按位置查基因（只列基因，不取序列）
     GET /api/gene/{name}                       → 基础信息 + 表达 + GO + KEGG + 文献
                                                  （含 aliases 别名块 / paralogs 复制体块）
     GET /api/genes/orthologs?name=             → 复制体与共直系同源（任务②，独立可查）
   设计：只渲染真数据；缺字段留空，不编造。列表点行出卡片，卡片里可跳到基因工作台。
   ========================================================================== */
(function () {
  'use strict';

  var SEARCH_URL = '/api/genes/search';
  var DETAIL_URL = '/api/gene/';
  var ORTHO_URL = '/api/genes/orthologs';
  var PAGE = 30;

  var TEXT = {
    zh: {
      searching: '检索中…', empty: '没有命中', emptyHint: '试试符号、别名或 ID，例如 p53 / tp53 / ZDB-GENE-990415-270',
      count: '命中 {n} 个基因', browse: '基因库前 {m} 个（按符号排序）',
      loadCard: '基因卡读取中…', cardFail: '基因卡读取失败',       searchFail: '检索失败：{msg}',
      colSymbol: '符号', colName: '名称', colChr: '染色体', colFn: '功能注释',
      colWhy: '命中依据', colPos: '区间（1-based）',
      sumSame: '{n} 个与输入同义', sumConflict: '{n} 条同名冲突待确认',
      sumLower: '{n} 个是名字含该串的其他基因', sumDesc: '{n} 个仅在描述或注释里提到',
      noneSame: '没有同义基因：下面列出的都不是同一个基因，只是名字里含该串或注释里提到它。',
      regionNeed: '请填写染色体与起止坐标。',
      regionEmpty: '该区间内没有检出基因。',
      regionHead: '区间 {asm} {chrom}:{start}–{end} 内有 {n} 个基因',
      regionFail: '位置查询失败：{msg}',
      batchEmpty: '请先粘贴要查的名字，每行一个或用逗号分隔。',
      batchHead: '{n} 条输入，其中 {ok} 条命中同义基因',
      batchHit: '{n} 个同义基因', batchNoSame: '没有同义基因',
      batchTrunc: '超过 200 条，只处理了前 200 条。',
      batchFail: '批量检索失败：{msg}',
      colSymbolName: '符号 / 名称', sameAsSymbol: '与符号相同',
      nameMerged: '名称与符号相同，已合并为一列',
      basic: '基础信息', aliases: '别名（含历史名）', ortho: '同源与复制体', expression: '表达（Top）',
      go: 'GO 注释', kegg: 'KEGG 通路', refs: '关联文献', openWorkbench: '看位置',
      moreNotes: '更多注释',
      moreHint: '表达 / GO / KEGG / 文献各有专页；这里按需展开，不默认铺满整张卡。',
      aliasNone: '本地库暂无别名记录', aliasCount: '{n} 个别名 · 来源：{src}',
      dupYes: '存在复制体（硬骨鱼全基因组复制）', dupNo: '未见一对多复制关系',
      copies: '{n} 个共直系同源', scoreShort: '相似度 {s}', selfCopy: '当前基因',
      orthoNote: '复制体由本地 homology_gene 的“同一物种 + 同一同源基因名”推导；功能分工（亚功能化/新功能化）需文献支持。',
      geneId: '基因 ID', ensembl: 'Ensembl', zfin: 'ZFIN', uniprot: 'UniProt',
      locus: '位置', locusTake: '按这段取序列 →', locusNote: 'GRCz11 注释坐标；换到 GRCz12tu 需先 Liftover',
      locusNa: '注释目录里查不到坐标',
      noData: '—'
    },
    en: {
      searching: 'Searching…', empty: 'No match', emptyHint: 'Try a symbol, alias or ID, e.g. p53 / tp53 / ZDB-GENE-990415-270',
      count: '{n} genes matched', browse: 'First {m} genes by symbol',
      loadCard: 'Loading gene card…', cardFail: 'Failed to load gene card',       searchFail: 'Search failed: {msg}',
      colSymbol: 'Symbol', colName: 'Name', colChr: 'Chr', colFn: 'Function',
      colWhy: 'Matched on', colPos: 'Interval (1-based)',
      sumSame: '{n} match the query itself', sumConflict: '{n} name conflict(s) to review',
      sumLower: '{n} are other genes whose symbol contains it', sumDesc: '{n} only mention it in text',
      noneSame: 'No gene actually matches: everything below is a different gene that merely contains the string or mentions it.',
      regionNeed: 'Please provide a chromosome and start/end coordinates.',
      regionEmpty: 'No genes overlap this interval.',
      regionHead: '{n} genes in {asm} {chrom}:{start}–{end}',
      regionFail: 'Region query failed: {msg}',
      batchEmpty: 'Paste the names to look up, one per line or comma-separated.',
      batchHead: '{n} queries, {ok} resolved to the same gene',
      batchHit: '{n} matching gene(s)', batchNoSame: 'no exact gene',
      batchTrunc: 'More than 200 items — only the first 200 were processed.',
      batchFail: 'Batch search failed: {msg}',
      colSymbolName: 'Symbol / name', sameAsSymbol: 'same as symbol',
      nameMerged: 'name equals symbol — merged into one column',
      basic: 'Basic', aliases: 'Aliases (incl. historical)', ortho: 'Orthologs & duplicates', expression: 'Expression (top)',
      go: 'GO', kegg: 'KEGG pathways', refs: 'Linked papers', openWorkbench: 'Locus',
      moreNotes: 'More annotations',
      moreHint: 'Expression, GO, KEGG and papers each have a dedicated page; expand only if needed.',
      aliasNone: 'No alias records in the local library', aliasCount: '{n} aliases · sources: {src}',
      dupYes: 'Duplicates present (teleost whole-genome duplication)', dupNo: 'No one-to-many duplication found',
      copies: '{n} co-orthologs', scoreShort: 'identity {s}', selfCopy: 'this gene',
      orthoNote: 'Duplicates are derived from homology_gene (same species + same ortholog name); functional partitioning needs literature evidence.',
      geneId: 'Gene ID', ensembl: 'Ensembl', zfin: 'ZFIN', uniprot: 'UniProt',
      locus: 'Locus', locusTake: 'Retrieve this region →', locusNote: 'GRCz11 annotation; convert to GRCz12tu via Liftover',
      locusNa: 'no coordinates in the annotation catalog',
      noData: '—'
    }
  };

  function curLang() {
    var l = (document.documentElement.getAttribute('lang') || 'en').toLowerCase();
    return l.indexOf('zh') === 0 ? 'zh' : 'en';
  }
  function T(k) {
    var d = TEXT[curLang()] || TEXT.en;
    return d[k] != null ? d[k] : (TEXT.en[k] || '');
  }
  function fmt(s, o) {
    return String(s).replace(/\{(\w+)\}/g, function (m, k) { return o[k] != null ? o[k] : m; });
  }
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function txt(v) { return (v == null || v === '') ? T('noData') : esc(v); }

  function resultBox() { return $('toolResult'); }
  function cardHost() { return $('featureGeneCardHost'); }
  /* 结果一律写进"本页"的桶（见 tool-result.js）：即使请求回来得晚，
     也不会把基因列表画到 Liftover 之类别的页面上去。 */
  function showResult(html) {
    if (window.ZFResult) { window.ZFResult.write(html, 'genes'); return; }
    var box = resultBox();
    if (!box) return;
    box.innerHTML = html;
    box.classList.remove('hidden');
  }

  /* ---------------- 结果渲染（按匹配层级分组） ----------------
     2026-11-03 整改：旧版把后端返回的扁平列表直接铺成「N genes matched」，
     于是「功能注释里提到 p53」的 rpmb / crebbpb 看起来像 p53 的别名。
     现在后端返回 groups（每级带 label / hint / same_gene），这里照分组渲染：
       · 同义层级（符号 / 别名 / ID）默认展开
       · 名称相关（前缀 / 包含）默认展开，但标题写明「不同基因」
       · 仅提及（描述别名 / 功能注释）与同名冲突默认折叠
     每个分组都把「命中依据」一列摆出来，用户能直接看到是哪条别名/描述命中的。 */

  var FOLDED_TIERS = { descriptive: true, function_text: true, conflict: true };

  function tierText(g, key) {
    var l = g[key] || {};
    return l[curLang()] || l.zh || '';
  }
  function num(v) {
    return v == null || v === '' ? '' : Number(v).toLocaleString('en-US');
  }
  function val(id) {
    var el = $(id);
    return el ? String(el.value || '').trim() : '';
  }

  function rowHtml(r, showWhy) {
    var sym = String(r.standard_symbol || r.gene_symbol || r.gene_name || '').trim();
    var name = String(r.gene_name || '').trim();
    var differs = !!name && name !== sym;
    var why = showWhy && r.matched_value ? String(r.matched_value) : '';
    var cells = '<td class="gene-cell-sym"><strong>' + txt(sym) + '</strong>' +
      (differs ? '<span class="gene-cell-name">' + esc(name) + '</span>' : '') + '</td>' +
      '<td>' + txt(r.chromosome) + '</td>';
    if (showWhy) {
      cells += '<td class="gene-cell-why">' + (why ? '<span class="gene-why">' + esc(why) + '</span>'
        : '<span class="tool-hint">—</span>') + '</td>';
    }
    cells += '<td class="gene-cell-fn">' + (r.ensembl_id ? '<span class="tool-hint">' + esc(r.ensembl_id) + '</span>'
      : '<span class="tool-hint">—</span>') + '</td>';
    return '<tr class="gene-row" data-name="' + esc(r.gene_name || sym) + '" data-gid="' + esc(r.gene_id || '') + '">' + cells + '</tr>';
  }

  /* 「命中依据」只在真正有依据可讲的时候出现：别名 / 标识符 / 冲突 / 描述命中。
     前缀、包含这两层命中的就是符号本身，再摆一列「命中依据 = 符号」等于把
     同一个词印两遍（用户 2026-11-04 反馈）；符号层同理，左边第一列已经是它。 */
  var WHY_TIERS = { exact_alias: true, exact_id: true, conflict: true, descriptive: true, function_text: true };

  function groupHtml(g) {
    var rows = g.results || [];
    if (!rows.length) return '';
    var folded = !!FOLDED_TIERS[g.tier];
    var showWhy = !!WHY_TIERS[g.tier];
    var th = '<th>' + esc(T('colSymbol')) + '</th><th>' + esc(T('colChr')) + '</th>' +
      (showWhy ? '<th>' + esc(T('colWhy')) + '</th>' : '') + '<th>Ensembl</th>';
    var body = rows.map(function (r) { return rowHtml(r, showWhy); }).join('');
    /* 2026-11-04：分组下方那行解释性小字全部撤掉（分组标题已经说明这是什么、
       条数也在标题右侧）。结果区只放结果，口径放在「平台说明」。 */
    var inner = '<div class="table-wrap"><table class="gene-table gene-table-compact">' +
      '<thead><tr>' + th + '</tr></thead><tbody>' + body + '</tbody></table></div>';
    var cls = 'gene-group' + (g.same_gene ? ' is-same' : ' is-lower') + (folded ? ' is-folded' : '');
    return '<details class="' + cls + '" data-tier="' + esc(g.tier) + '"' + (folded ? '' : ' open') + '>' +
      '<summary><span class="gene-group-title">' + esc(tierText(g, 'label')) + '</span>' +
      '<span class="gene-group-count">' + rows.length + (g.capped ? '+' : '') + '</span></summary>' + inner + '</details>';
  }

  /* 汇总行按分组实际条数算；某层被 limit 截断时显示「N+」，
     不把截断值当成真实总数（否则「20 个仅在描述里提到」会冒充全量）。 */
  function summaryHtml(data, browse) {
    if (browse) return fmt(T('browse'), { m: data.total || 0 });
    var groups = (data && data.groups) || [];
    function of(tiers) {
      var n = 0, plus = false;
      groups.forEach(function (g) {
        if (tiers.indexOf(g.tier) >= 0) { n += g.count || 0; if (g.capped) plus = true; }
      });
      return { n: n, s: n + (plus ? '+' : '') };
    }
    var same = of(['exact_symbol', 'exact_alias', 'exact_id']);
    var conf = of(['conflict']);
    var lower = of(['prefix', 'contains']);
    var desc = of(['descriptive', 'function_text']);
    var bits = [];
    if (same.n) bits.push(fmt(T('sumSame'), { n: same.s }));
    if (conf.n) bits.push(fmt(T('sumConflict'), { n: conf.s }));
    if (lower.n) bits.push(fmt(T('sumLower'), { n: lower.s }));
    if (desc.n) bits.push(fmt(T('sumDesc'), { n: desc.s }));
    return bits.join(' · ');
  }

  /* 语言切换后要把已经画出来的结果按新语言重画一遍。
     这里记住「最后一份结果 + 它的画法」，切语言时原样再画一次；否则切到英文
     后表头、分组标题、命中依据还是中文（2026-11-04 用户反馈「中英文未同步」）。 */
  var lastPaint = null;
  var lastCard = null;

  function renderGroups(data, browse) {
    lastPaint = function () { renderGroups(data, browse); };
    var groups = (data && data.groups) || [];
    var has = groups.some(function (g) { return (g.results || []).length; });
    if (!has) {
      showResult('<p class="tool-hint">' + esc(T('empty')) + ' · ' + esc(T('emptyHint')) + '</p>');
      return;
    }
    var head = '<div class="gene-list-head">' + esc(summaryHtml(data, browse)) + '</div>';
    showResult(head + groups.map(groupHtml).join(''));
  }

  /* ---------------- 按位置查基因 ---------------- */

  function renderRegion(d) {
    lastPaint = function () { renderRegion(d); };
    var genes = (d && d.genes) || [];
    if (!genes.length) {
      showResult('<p class="tool-hint">' + esc(T('regionEmpty')) + '</p>');
      return;
    }
    var head = fmt(T('regionHead'), {
      chrom: d.chromosome, start: num(d.start), end: num(d.end),
      asm: d.assembly, n: genes.length
    });
    var body = genes.map(function (g) {
      var span = num(g.start) + '–' + num(g.end) + (g.strand ? ' (' + esc(g.strand) + ')' : '');
      return '<tr class="gene-row" data-name="' + esc(g.symbol) + '" data-gid="' + esc(g.gene_id || '') + '">' +
        '<td class="gene-cell-sym"><strong>' + txt(g.symbol) + '</strong></td>' +
        '<td>' + span + '</td>' +
        '<td><span class="tool-hint">' + esc(g.gene_id || '—') + '</span></td></tr>';
    }).join('');
    showResult('<div class="gene-list-head">' + esc(head) + '</div>' +
      '<div class="table-wrap"><table class="gene-table gene-table-compact"><thead><tr>' +
      '<th>' + esc(T('colSymbol')) + '</th><th>' + esc(T('colPos')) + '</th><th>NCBI Gene</th>' +
      '</tr></thead><tbody>' + body + '</tbody></table></div>');
  }

  function runRegion() {
    var chrom = val('geneRegionChrom');
    var asm = val('geneRegionAsm') || 'GRCz12tu';
    var s = parseInt(val('geneRegionStart'), 10);
    var e = parseInt(val('geneRegionEnd'), 10);
    if (!chrom || !s || !e) {
      showResult('<p class="tool-hint">' + esc(T('regionNeed')) + '</p>');
      return;
    }
    showResult('<span class="tool-loading">' + esc(T('searching')) + '</span>');
    var url = '/api/genes/region?assembly=' + encodeURIComponent(asm) +
      '&chrom=' + encodeURIComponent(chrom) + '&start=' + s + '&end=' + e + '&limit=200';
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (x) { throw new Error((x && x.detail) || 'HTTP'); }); })
      .then(renderRegion)
      .catch(function (err) {
        showResult('<p class="tool-hint">' + esc(fmt(T('regionFail'), { msg: String(err.message || err).slice(0, 90) })) + '</p>');
      });
  }

  /* ---------------- 批量 ---------------- */

  function renderBatch(d) {
    lastPaint = function () { renderBatch(d); };
    var items = (d && d.queries) || [];
    var head = fmt(T('batchHead'), { n: d.total_queries || 0, ok: d.resolved || 0 });
    var blocks = items.map(function (it) {
      var same = (it.groups || []).filter(function (g) { return g.same_gene; })
        .reduce(function (acc, g) { return acc.concat(g.results || []); }, []);
      var status = it.error ? esc(it.error)
        : (same.length ? fmt(T('batchHit'), { n: same.length }) : esc(T('batchNoSame')));
      var title = '<div class="gene-batch-head"><strong>' + esc(it.query) + '</strong>' +
        '<span class="tool-hint">' + status + '</span></div>';
      if (!same.length) return '<div class="gene-batch-item">' + title + '</div>';
      var body = same.map(function (r) {
        var why = (r.matched_by === 'exact_alias' || r.matched_by === 'exact_id')
          ? (r.matched_value || '') : '';
        return '<tr class="gene-row" data-name="' + esc(r.gene_name || r.gene_symbol) + '">' +
          '<td class="gene-cell-sym"><strong>' + txt(r.standard_symbol || r.gene_name) + '</strong></td>' +
          '<td>' + txt(r.chromosome) + '</td>' +
          '<td class="gene-cell-why">' + (why ? '<span class="gene-why">' + esc(why) + '</span>'
            : '<span class="tool-hint">—</span>') + '</td></tr>';
      }).join('');
      return '<div class="gene-batch-item">' + title +
        '<div class="table-wrap"><table class="gene-table gene-table-compact"><tbody>' + body +
        '</tbody></table></div></div>';
    }).join('');
    var trunc = d.truncated ? '<p class="tool-hint">' + esc(T('batchTrunc')) + '</p>' : '';
    showResult('<div class="gene-list-head">' + esc(head) + '</div>' + trunc + blocks);
  }

  function runBatch() {
    var raw = val('geneBatchInput');
    if (!raw) {
      showResult('<p class="tool-hint">' + esc(T('batchEmpty')) + '</p>');
      return;
    }
    showResult('<span class="tool-loading">' + esc(T('searching')) + '</span>');
    fetch('/api/genes/search/batch', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ queries: raw, limit: 10, max_items: 200 })
    })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (x) { throw new Error((x && x.detail) || 'HTTP'); }); })
      .then(renderBatch)
      .catch(function (err) {
        showResult('<p class="tool-hint">' + esc(fmt(T('batchFail'), { msg: String(err.message || err).slice(0, 90) })) + '</p>');
      });
  }

  /* ---------------- 按名称检索 ---------------- */

  function runSearch(browse) {
    var inp = $('geneSearchInput');
    var q = inp ? inp.value.trim() : '';
    if (!q && !browse) { if (inp) inp.focus(); return; }
    showResult('<span class="tool-loading">' + esc(T('searching')) + '</span>');
    var url = SEARCH_URL + '?limit=' + PAGE + '&browse=' + (browse ? 1 : 0) +
      (q ? '&q=' + encodeURIComponent(q) : '');
    fetch(url, { credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function (d) {
        if (!d) { showResult('<p class="tool-hint">' + esc(fmt(T('searchFail'), { msg: 'HTTP' })) + '</p>'); return; }
        renderGroups(d, browse);
      })
      .catch(function (e) {
        showResult('<p class="tool-hint">' + esc(fmt(T('searchFail'), { msg: String(e).slice(0, 80) })) + '</p>');
      });
  }

  /* ---------------- 基因卡 ---------------- */

  function aliasHtml(d) {
    var a = d.aliases || {};
    var items = a.items || [];
    if (!items.length) return '<p class="tool-hint">' + esc(T('aliasNone')) + '</p>';
    var srcs = Object.keys(a.groups || {}).map(function (k) {
      var label = (items.filter(function (i) { return i.source === k; })[0] || {}).source_label || k;
      return label;
    });
    var chips = items.map(function (i) {
      var cls = 'alias-chip' + (i.is_primary ? ' is-primary' : (i.is_id_like ? ' is-id' : ''));
      return '<span class="' + cls + '" title="' + esc(i.source_label || '') + '">' + esc(i.alias) + '</span>';
    }).join('');
    return '<p class="tool-hint">' + esc(fmt(T('aliasCount'), { n: items.length, src: srcs.join(' / ') })) + '</p>' +
      '<div class="alias-chips">' + chips + '</div>';
  }

  function orthoHtml(paralogs) {
    // 注意：入参就是 paralogs 子对象本体（不是整个 gene_detail），不要再多取一层
    var p = paralogs || {};
    var groups = p.groups || [];
    if (!groups.length) return '<p class="tool-hint">' + esc(T('dupNo')) + '</p>';
    var badge = p.is_duplicated
      ? '<span class="ortho-badge is-dup">' + esc(T('dupYes')) + '</span>'
      : '<span class="ortho-badge">' + esc(T('dupNo')) + '</span>';
    var blocks = groups.map(function (g) {
      var copies = (g.copies || []).map(function (c) {
        var name = c.symbol || String(c.gene_id || '');
        var tag = c.is_self ? '<em>' + esc(T('selfCopy')) + '</em>' : '';
        return '<span class="ortho-copy' + (c.is_self ? ' is-self' : '') + '">' + esc(name) +
          ' <small>' + esc(fmt(T('scoreShort'), { s: (c.score || 0).toFixed(1) })) + '</small>' + tag + '</span>';
      }).join('<span class="ortho-arrow" aria-hidden="true">·</span>');
      return '<div class="ortho-group"><p class="ortho-head">' +
        '<strong>' + esc(g.homo_gene_name) + '</strong> <small>' + esc(g.species) + ' · ' +
        esc(fmt(T('copies'), { n: g.n_copies })) + '</small></p>' +
        '<div class="ortho-copies">' + copies + '</div></div>';
    }).join('');
    return badge + blocks + '<p class="tool-hint">' + esc(T('orthoNote')) + '</p>';
  }

  /* 坐标行（2026-10-28）
     gene_basic 只有染色体号，坐标由后端 /api/gene/ 的 locus 块补（GRCz11 / Ensembl r116）。
     取不到就写「查不到坐标」——**不拿染色体号编一个区间**。
     装配版本必须明写：GRCz11 的坐标直接丢给默认 GRCz12tu 的取序列会取错位置，
     所以跳转时把 assembly 一起带过去。 */
  function locusLi(loc) {
    if (!loc || loc.start == null || loc.end == null) {
      return '<li><span>' + esc(T('locus')) + '</span><span class="tool-hint">' + esc(T('locusNa')) + '</span></li>';
    }
    var span = loc.chromosome + ':' + Number(loc.start).toLocaleString('en-US') +
               '–' + Number(loc.end).toLocaleString('en-US') + ' (' + (loc.strand || '') + ')';
    return '<li><span>' + esc(T('locus')) + '</span>' + esc(span) +
      ' <small class="gene-locus-asm">' + esc(loc.assembly) + '</small>' +
      ' <a class="zf-link" href="#" data-gene-seq="1"' +
      ' data-chrom="' + esc(loc.chromosome) + '"' +
      ' data-start="' + esc(String(loc.start)) + '"' +
      ' data-end="' + esc(String(loc.end)) + '"' +
      ' data-asm="' + esc(loc.assembly || 'GRCz12tu') + '">' +
      esc(T('locusTake')) + '</a></li>';
  }

  /* 2026-10-31 边界重划：本页（别名）只答「这是哪个基因、它还有哪些名字」。
     所以卡片默认只展开【基础 ID】【别名】【同源与复制体】三段；
     表达 / GO / KEGG / 文献各有专页，收进底部一个折叠块按需展开，
     免得两页都堆成同一张「什么都有」的基因大卡。
     坐标行（locusLi）整段移走 —— 那是「位置查找」页的职责，
     卡片右上角的「看位置」链接跳过去即可。 */
  function renderCard(d, orthoExtra) {
    lastCard = function () { renderCard(d, orthoExtra); };
    var host = cardHost();
    if (!host) return;
    var b = d.basic || {};
    // 2026-10-28：库里 gene_name 才是标准符号，gene_symbol 是「常用别名」（见建表注释）。
    // 之前这里取 gene_symbol 优先，于是 tp53 的详情卡标题显示成 p53，把正名降成了小字。
    var sym = String((d.standard || {}).gene_symbol || b.gene_name || b.gene_symbol || '').trim();
    var aliasMain = String(b.gene_symbol || '').trim();
    var altName = aliasMain && aliasMain !== sym ? aliasMain : '';
    var expr = (d.expression || []).slice(0, 8);
    var go = (d.go || []).slice(0, 10);
    var kegg = (d.kegg || []).slice(0, 8);
    var refs = (d.references || []).slice(0, 6);
    var paralogs = d.paralogs || orthoExtra || {};

    /* 其余注释：折叠，按条数在 summary 里报个数，不展开也能知道有多少 */
    var counts = [];
    if (expr.length) counts.push(T('expression') + ' ' + expr.length);
    if (go.length) counts.push('GO ' + go.length);
    if (kegg.length) counts.push('KEGG ' + kegg.length);
    if (refs.length) counts.push(T('refs') + ' ' + refs.length);

    var moreBody = '';
    if (expr.length) {
      moreBody += '<div class="gene-card-sec"><label>' + esc(T('expression')) + '</label><ul class="kv">' +
        expr.map(function (e) {
          return '<li><span>' + esc(e.tissue || '—') + '</span>' + esc(String(e.expression_level == null ? '' : e.expression_level)) +
            (e.expression_stage ? ' <small>' + esc(e.expression_stage) + '</small>' : '') + '</li>';
        }).join('') + '</ul></div>';
    }
    if (go.length) {
      moreBody += '<div class="gene-card-sec"><label>' + esc(T('go')) + '</label><div class="alias-chips">' +
        go.map(function (g) { return '<span class="alias-chip is-id">' + esc(g.go_term) + '</span>'; }).join('') +
        '</div></div>';
    }
    if (kegg.length) {
      moreBody += '<div class="gene-card-sec"><label>' + esc(T('kegg')) + '</label><ul class="kv">' +
        kegg.map(function (k) {
          return '<li><span>' + esc(k.pathway_id || '') + '</span>' + esc(k.pathway_name || '') + '</li>';
        }).join('') + '</ul></div>';
    }
    if (refs.length) {
      moreBody += '<div class="gene-card-sec"><label>' + esc(T('refs')) + '</label><ul class="kv">' +
        refs.map(function (r) {
          var pid = r.pubmed_id ? String(r.pubmed_id) : '';
          var title = esc(String(r.title || '').slice(0, 130));
          return '<li><span>' + esc(String(r.publish_year || '')) + '</span>' +
            (pid ? '<a href="https://pubmed.ncbi.nlm.nih.gov/' + esc(pid) + '/" target="_blank" rel="noopener">' + title + '</a>' : title) +
            '</li>';
        }).join('') + '</ul></div>';
    }
    var moreHtml = moreBody
      ? '<details class="gene-card-more"><summary>' + esc(T('moreNotes')) +
        (counts.length ? ' <small>' + esc(counts.join(' · ')) + '</small>' : '') +
        '</summary>' + moreBody +
        '<p class="tool-hint">' + esc(T('moreHint')) + '</p></details>'
      : '';

    host.innerHTML =
      '<div class="gene-card gene-card-full">' +
      '<div class="gene-card-head"><strong>' + txt(sym) + '</strong>' +
      (altName ? '<span class="gene-card-alt">' + esc(altName) + '</span>' : '') +
      '<a class="gene-card-link" href="#/gene/' + encodeURIComponent(b.gene_name || sym) + '">' +
      esc(T('openWorkbench')) + ' →</a></div>' +
      '<div class="gene-card-sec"><label>' + esc(T('basic')) + '</label>' +
      '<ul class="kv"><li><span>' + esc(T('geneId')) + '</span>' + txt(b.gene_id) + '</li>' +
      '<li><span>' + esc(T('ensembl')) + '</span>' + txt(b.ensembl_id) + '</li>' +
      '<li><span>' + esc(T('zfin')) + '</span>' + txt(b.zfin_id) + '</li>' +
      '<li><span>' + esc(T('uniprot')) + '</span>' + txt(b.uniprot_id) + '</li>' +
      '<li><span>' + esc(T('colChr')) + '</span>' + txt(b.chromosome) + '</li>' +
      '</ul>' +
      (b.gene_function ? '<p class="gene-card-text">' + esc(String(b.gene_function).slice(0, 400)) + '</p>' : '') +
      '</div>' +
      '<div class="gene-card-sec"><label>' + esc(T('aliases')) + '</label>' + aliasHtml(d) + '</div>' +
      '<div class="gene-card-sec"><label>' + esc(T('ortho')) + '</label>' + orthoHtml(paralogs) + '</div>' +
      moreHtml +
      '</div>';
    host.classList.remove('hidden');
  }

  function openGene(name) {
    var host = cardHost();
    if (host) {
      host.innerHTML = '<p class="tool-hint">' + esc(T('loadCard')) + '</p>';
      host.classList.remove('hidden');
    }
    fetch(DETAIL_URL + encodeURIComponent(name), { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) throw new Error(T('cardFail'));
        renderCard(d, null);
      })
      .catch(function (e) {
        if (host) host.innerHTML = '<p class="tool-hint">' + esc(T('cardFail')) + ' · ' + esc(String(e).slice(0, 80)) + '</p>';
      });
  }

  /* ---------------- 绑定 ---------------- */

  function bind() {
    var s = $('geneSearchBtn');
    if (s) s.addEventListener('click', function () { runSearch(false); });
    var b = $('geneBrowseBtn');
    if (b) b.addEventListener('click', function () { runSearch(true); });

    var rb = $('geneRegionBtn');
    if (rb) rb.addEventListener('click', runRegion);
    var bb = $('geneBatchBtn');
    if (bb) bb.addEventListener('click', runBatch);

    /* 三个模式：按名称 / 按位置 / 批量。切换只动面板显隐，不清结果区，
       这样用户可以在几个模式之间来回看同一批结果。 */
    Array.prototype.forEach.call(document.querySelectorAll('.gene-tab'), function (tab) {
      tab.addEventListener('click', function () {
        var mode = tab.getAttribute('data-gene-mode');
        Array.prototype.forEach.call(document.querySelectorAll('.gene-tab'), function (t) {
          t.classList.toggle('active', t === tab);
        });
        Array.prototype.forEach.call(document.querySelectorAll('[data-gene-pane]'), function (p) {
          p.classList.toggle('hidden', p.getAttribute('data-gene-pane') !== mode);
        });
      });
    });

    ['geneRegionChrom', 'geneRegionStart', 'geneRegionEnd'].forEach(function (id) {
      var el = $(id);
      if (el) el.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); runRegion(); }
      });
    });

    var inp = $('geneSearchInput');
    if (inp) {
      inp.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); runSearch(false); }
      });
    }

    var box = resultBox();
    if (box) {
      box.addEventListener('click', function (e) {
        var tr = e.target.closest ? e.target.closest('tr.gene-row') : null;
        if (!tr) return;
        var name = tr.getAttribute('data-name');
        if (!name) return;
        Array.prototype.forEach.call(box.querySelectorAll('tr.gene-row.is-active'),
          function (x) { x.classList.remove('is-active'); });
        tr.classList.add('is-active');
        openGene(name);
      });
    }

    document.addEventListener('hub-lang-change', function () {
      // 语言切换：本页已经画出来的结果与卡片按新语言重画（只在本页时做）
      if (window.ZFResult && window.ZFResult.route() !== 'genes') return;
      if (lastPaint) lastPaint();
      if (lastCard) lastCard();
    });
  }

  function boot() {
    if (!$('geneSearchBtn')) return;      // 页面结构不在，静默退出
    bind();
    // 只有真正进入 Genes 页时才自动列一批（避免每次打开首页都打 DB）
    document.addEventListener('zf-feature-open', function (e) {
      var route = (e && e.detail && e.detail.route) || '';
      if (route !== 'genes') return;
      /* 2026-10-31：#/genes/<name> 是「直接开这张卡」的深链（位置页的「查别名」跳过来），
         已经指定了基因就不该再铺一遍全库前 30 个 —— 那会变成「先列 30 个无关基因，
         再在下面挂你要的那张卡」。 */
      if (/^#\/genes\/.+/.test(location.hash || '')) return;
      var box = resultBox();
      if (box && !box.textContent.trim()) runSearch(true);
    });
  }

  /* 2026-10-31：#/genes/<name> 深链要能直接开某张别名卡（位置页的「查别名」跳过来） */
  window.ZFGenes = { open: openGene, search: runSearch };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();

  /* 卡片上的「按这段取序列」：把坐标 + 装配写进 sessionStorage，
     交给 #/sequence-region 回填（与 Liftover 结果卡同一套机制）。 */
  document.addEventListener('click', function (e) {
    var el = e.target && e.target.closest ? e.target.closest('[data-gene-seq]') : null;
    if (!el) return;
    e.preventDefault();
    try {
      sessionStorage.setItem('zf_seq_region', JSON.stringify({
        chrom: el.getAttribute('data-chrom') || '',
        start: el.getAttribute('data-start') || '',
        end: el.getAttribute('data-end') || '',
        assembly: el.getAttribute('data-asm') || 'GRCz11'
      }));
    } catch (_) {}
    location.hash = '#/sequence-region';
  });

})();
