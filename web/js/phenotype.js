/* ==========================================================================
   phenotype.js —— Phenotype 功能页（基因 ↔ 表型）
   --------------------------------------------------------------------------
   数据来源：
     GET  /api/phenotype/stats                → 索引状态 + 常用表型词（PATO / ZFA 术语）
     GET  /api/phenotype/search?q=&limit=     → 表型关键词 → 命中条目 + 候选基因 + 本地文献
     GET  /api/phenotype/gene?name=&limit=    → 基因 → 表型分组（含分期、基因型、文献号）
     POST /api/phenotype/build-index?background=1 → 构建/刷新 ZFIN 索引（后台执行，幂等）
   口径：表型注释逐行来自 ZFIN 官方 phenoGeneCleanData_fish.txt（本地解析成 SQLite）；
        术语编号保留 ZFIN/PATO 原始编号不翻译；索引未构建时明确告知并提示构建。
   ========================================================================== */
(function () {
  'use strict';

  var STATS_URL = '/api/phenotype/stats';
  var SEARCH_URL = '/api/phenotype/search';
  var GENE_URL = '/api/phenotype/gene';
  var BUILD_URL = '/api/phenotype/build-index';

  var TEXT = {
    zh: {
      loading: '查询表型…', fail: '查询失败：{msg}',
      notReady: 'ZFIN 表型索引尚未构建', notReadyHint: '点右侧「构建 / 刷新 ZFIN 索引」即可（后台执行，约 1–2 分钟；构建期间本页可继续查文献）。',
      needQ: '请输入表型关键词或基因名',
      idxReady: '索引已就绪', idxRows: '注释条目', idxGenes: '覆盖基因', idxTerms: '表型术语', idxBuilt: '构建时间',
      commonTerms: '常用表型术语', commonTermsSub: '按覆盖基因数排序 · 点一下直接查',
      commonAo: '解剖实体（ZFA）', commonPato: '表型描述（PATO）',
      termTitle: '命中表型条目', termSub: '每行 = 一个「解剖实体 + 表型描述」组合，按覆盖基因数排序',
      termGenes: '候选基因', termGenesSub: '记录到该表型的基因（按注释条数降序）· 点符号看该基因的表型',
      litTitle: '本地文献命中', litSub: '按题名匹配本地文献库（与上面的表型注释相互独立，供溯源）',
      geneTitle: '{g} 的表型记录', geneSub: '按「解剖实体 + 表型描述」分组去重 · 来源：ZFIN phenoGeneCleanData_fish',
      geneEmpty: '{g} 在本库没有 ZFIN 表型注释记录',
      colAo: '解剖实体', colAoId: 'ZFA 编号', colPato: '表型描述', colPatoId: 'PATO 编号',
      colQuality: '程度', colGenes: '基因数', colRows: '条目', colSym: '符号', colN: '条目', colTypes: '表型类型',
      colStages: '发育分期', colPubs: '文献', colCount: '条数',
      kvRows: '注释条目', kvGroups: '表型分组', kvPubs: '涉及文献', kvGeneId: '基因库 ID', kvZfin: 'ZFIN 基因',
      kvMatched: '实际匹配符号', kvTried: '参与比较的候选（命中条数）：',
      pubTitle: '该基因相关的 ZFIN 文献编号', pubHint: '编号为 ZDB-PUB，可在 zfin.org 逐条核对；本地文献命中见下方。',
      buildBtnRunning: '构建中…（可继续查询）', buildStarted: '构建任务已在后台启动（通常 1–2 分钟）；完成后本页自动刷新。',
      buildDone: '索引已就绪：{rows} 条注释 · {genes} 个基因', buildFail: '构建失败：{msg}',
      statusFail: '无法读取索引状态：{msg}',
      checkAgain: '刷新索引状态', note: '说明', noRecord: '无记录'
    },
    en: {
      loading: 'Querying phenotypes…', fail: 'Query failed: {msg}',
      notReady: 'The ZFIN phenotype index has not been built yet', notReadyHint: 'Click “Build / refresh ZFIN index” (runs in the background, about 1–2 minutes); literature queries keep working meanwhile.',
      needQ: 'Enter a phenotype keyword or a gene name',
      idxReady: 'Index ready', idxRows: 'Annotation rows', idxGenes: 'Genes', idxTerms: 'Phenotype terms', idxBuilt: 'Built at',
      commonTerms: 'Common phenotype terms', commonTermsSub: 'Ranked by number of genes · click to query',
      commonAo: 'Anatomical entities (ZFA)', commonPato: 'Phenotype qualities (PATO)',
      termTitle: 'Matched phenotype entries', termSub: 'One row per anatomical-entity + quality pair, ranked by gene count',
      termGenes: 'Candidate genes', termGenesSub: 'Genes annotated with this phenotype (by number of annotations) · click a symbol to open its phenotypes',
      litTitle: 'Local literature hits', litSub: 'Title matches in the local paper library (independent of the annotations above, for provenance)',
      geneTitle: 'Phenotypes recorded for {g}', geneSub: 'Grouped by anatomical entity + quality · source: ZFIN phenoGeneCleanData_fish',
      geneEmpty: 'No ZFIN phenotype annotation for {g} in this library',
      colAo: 'Anatomical entity', colAoId: 'ZFA ID', colPato: 'Quality', colPatoId: 'PATO ID',
      colQuality: 'Degree', colGenes: 'Genes', colRows: 'Rows', colSym: 'Symbol', colN: 'Rows', colTypes: 'Phenotype types',
      colStages: 'Stages', colPubs: 'Publications', colCount: 'Count',
      kvRows: 'Annotation rows', kvGroups: 'Phenotype groups', kvPubs: 'Publications', kvGeneId: 'Library ID', kvZfin: 'ZFIN gene',
      kvMatched: 'Matched symbol', kvTried: 'Compared candidates (rows):',
      pubTitle: 'ZFIN publication IDs for this gene', pubHint: 'IDs are ZDB-PUB records and can be checked at zfin.org; local literature hits are listed below.',
      buildBtnRunning: 'Building… (you can keep querying)', buildStarted: 'Build started in the background (usually 1–2 minutes); this page refreshes automatically when done.',
      buildDone: 'Index ready: {rows} annotations · {genes} genes', buildFail: 'Build failed: {msg}',
      statusFail: 'Cannot read index status: {msg}',
      checkAgain: 'Refresh status', note: 'Note', noRecord: 'no record'
    }
  };

  function curLang() {
    var l = (document.documentElement.getAttribute('lang') || 'en').toLowerCase();
    return l.indexOf('zh') === 0 ? 'zh' : 'en';
  }
  function T(k) { var d = TEXT[curLang()] || TEXT.en; return d[k] != null ? d[k] : (TEXT.en[k] || ''); }
  function fmt(s, o) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return o[k] != null ? o[k] : m; }); }
  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function activeRoute() {
    return ((location.hash || '').replace(/^#\/?/, '').split(/[/?#]/)[0]) || 'home';
  }

  var lastPayload = null, lastMode = 'term', pollTimer = null;

  /* 2026-10-31：两个页面共用一个面板，#phenoMode 只是「值容器」。
     方向一律由路由写死：#/phenotype-gene = 基因 → 表型，#/phenotype-term = 表型 → 基因。
     这样任何一页都不会出现反方向的输入提示或结果。 */
  function routeMode() {
    return activeRoute() === 'phenotype-gene' ? 'gene' : 'term';
  }

  /* 进入页面时把方向、placeholder、结果区一次性对齐。
     换方向要清掉上一页的输入与结果 —— 否则在「表型查基因」页会看到上一步查来的基因表型。
     注意不能用 `#phenoMode` 的旧值判断是否换了方向：app.js 的路由处理会先把这个
     下拉改成新值再发 zf-feature-open，那时旧值已经没了。所以自己记一份 lastAppliedMode。 */
  var lastAppliedMode = null;

  function applyRoute() {
    var want = routeMode();
    var changed = lastAppliedMode !== null && lastAppliedMode !== want;
    lastAppliedMode = want;
    if ($('phenoMode')) $('phenoMode').value = want;
    lastMode = want;
    syncPlaceholder();
    if (changed) {
      if ($('phenoInput')) $('phenoInput').value = '';
      var box = $('toolResult');
      if (box) { box.innerHTML = ''; box.classList.add('hidden'); }
      lastPayload = null;
    }
    if (want === 'term') {
      // 只有「表型查基因」页摆常用表型术语（点了直接查表型）；
      // 「基因查表型」页留空，交给页面自己的空态提示，两页从此不再一模一样。
      var b = $('toolResult');
      if (!b || !b.textContent.trim()) loadStats();
    }
  }

  function showResult(html) {
    var box = $('toolResult');
    if (!box) return;
    box.innerHTML = html;
    box.classList.remove('hidden');
  }
  function block(title, sub, body) {
    return '<div class="zf-block">' + (title ? '<h4 class="zf-block-title">' + esc(title) + '</h4>' : '') +
      (sub ? '<p class="zf-block-sub">' + esc(sub) + '</p>' : '') + body + '</div>';
  }
  function kv(k, v) {
    return '<div class="zf-kv-item"><span class="zf-kv-k">' + esc(k) + '</span><span class="zf-kv-v">' + esc(v) + '</span></div>';
  }
  function link(name) {
    return '<a class="zf-link" href="#/gene/' + encodeURIComponent(name) + '">' + esc(name) + '</a>';
  }

  /* ---------------- 索引状态 ---------------- */

  function renderNotReady(st) {
    showResult(
      '<div class="zf-block"><h4 class="zf-block-title">' + esc(T('notReady')) + '</h4>' +
      '<p class="zf-block-sub">' + esc(T('notReadyHint')) + '</p>' +
      (st && st.source_files && st.source_files.length
        ? '<p class="zf-note">' + esc(curLang() === 'zh' ? '源文件：' : 'Source file: ') +
          st.source_files.map(function (f) { return esc(f.file) + ' (' + f.size_mb + ' MB)'; }).join(', ') + '</p>'
        : '') +
      '<p class="zf-note"><button type="button" class="zf-pill" data-act="build">' + esc(curLang() === 'zh' ? '立即构建索引' : 'Build the index now') + '</button> ' +
      '<button type="button" class="zf-pill" data-act="check">' + esc(T('checkAgain')) + '</button></p></div>');
  }

  function renderStats(st) {
    /* 2026-10-31：删掉顶上那块「索引已就绪 / 注释条目 / 覆盖基因 / 表型术语 / 构建时间」。
       这些是库的体检数据（后台口径），对用户「查表型」这件事没有用处，两个表型页都去掉。
       只保留下面的常用表型术语，点了能直接查。 */

    var pato = (st && st.top_terms || []).filter(function (x) { return x.kind === 'pato'; });
    var ao = (st && st.top_terms || []).filter(function (x) { return x.kind === 'ao'; });
    function chips(items) {
      return '<div class="zf-chips">' + items.map(function (x) {
        return '<button type="button" class="zf-pill" data-term="' + esc(x.name) + '">' +
          esc(x.name) + ' <small>' + x.genes + (curLang() === 'zh' ? ' 基因' : ' genes') + '</small></button>';
      }).join('') + '</div>';
    }
    var termsHtml = (pato.length || ao.length)
      ? block(T('commonTerms'), T('commonTermsSub'),
          (pato.length ? '<p class="zf-block-sub" style="margin:10px 0 6px">' + esc(T('commonPato')) + '</p>' + chips(pato) : '') +
          (ao.length ? '<p class="zf-block-sub" style="margin:14px 0 6px">' + esc(T('commonAo')) + '</p>' + chips(ao) : ''))
      : '';
    return termsHtml;
  }

  function loadStats() {
    return fetch(STATS_URL, { credentials: 'same-origin' })
      .then(readJson)
      .then(function (st) {
        if (!st || !st.index_ready) { renderNotReady(st); return st; }
        showResult(renderStats(st));
        return st;
      })
      .catch(function (e) {
        showResult('<p class="tool-hint">' + esc(fmt(T('statusFail'), { msg: String(e.message || e).slice(0, 120) })) + '</p>');
      });
  }

  function buildIndex() {
    var btn = $('phenoBuildBtn');
    if (btn) btn.textContent = T('buildBtnRunning');
    fetch(BUILD_URL + '?background=1', { method: 'POST', credentials: 'same-origin' })
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function () {
        showResult('<p class="tool-hint">' + esc(T('buildStarted')) + '</p>');
        var tries = 0;
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = setInterval(function () {
          tries += 1;
          fetch(STATS_URL, { credentials: 'same-origin' })
            .then(readJson)
            .then(function (st) {
              if (st && st.index_ready) {
                clearInterval(pollTimer); pollTimer = null;
                if (btn) btn.textContent = curLang() === 'zh' ? '构建 / 刷新 ZFIN 索引' : 'Build / refresh ZFIN index';
                showResult(renderStats(st) + '<p class="zf-note">' + esc(fmt(T('buildDone'),
                  { rows: st.rows, genes: st.genes })) + '</p>');
              } else if (tries > 40) {
                clearInterval(pollTimer); pollTimer = null;
                if (btn) btn.textContent = curLang() === 'zh' ? '构建 / 刷新 ZFIN 索引' : 'Build / refresh ZFIN index';
              }
            })
            .catch(function () { /* 轮询失败忽略，下轮再试 */ });
        }, 6000);
      })
      .catch(function (e) {
        showResult('<p class="tool-hint">' + esc(fmt(T('buildFail'), { msg: String(e.message || e).slice(0, 120) })) + '</p>');
      });
  }

  /* ---------------- 渲染：表型 → 基因 ---------------- */

  function literatureHtml(items) {
    items = items || [];
    if (!items.length) return '';
    return block(T('litTitle'), T('litSub'), '<ul class="zf-lit">' + items.map(function (p) {
      var t = p.title || '';
      return '<li>' + (p.url
        ? '<a class="zf-link" href="' + esc(p.url) + '" target="_blank" rel="noopener">' + esc(t) + '</a>'
        : esc(t)) +
        ' <span class="zf-muted">' + esc([p.journal, p.year].filter(Boolean).join(' · ')) + '</span></li>';
    }).join('') + '</ul>');
  }

  function renderTerm(d) {
    var terms = d.terms || [], genes = d.genes || [];
    var tRows = terms.map(function (t) {
      return '<tr><td><strong>' + esc(t.ao_name || '—') + '</strong><br><span class="zf-muted">' + esc(t.ao_id || '') + '</span></td>' +
        '<td>' + esc(t.pato_name || '—') + '<br><span class="zf-muted">' + esc(t.pato_id || '') + '</span></td>' +
        '<td>' + esc(t.quality || '—') + '</td>' +
        '<td class="zf-num">' + t.g + '</td>' +
        '<td class="zf-num">' + t.n + '</td></tr>';
    }).join('');
    var gRows = genes.map(function (g) {
      return '<tr><td>' + link(g.symbol || '') + '</td>' +
        '<td class="zf-muted">' + esc(g.zfin_gene_id || '—') + '</td>' +
        '<td class="zf-num">' + g.n + '</td>' +
        '<td class="zf-num">' + g.p + '</td></tr>';
    }).join('');

    showResult(
      block(T('termTitle'), T('termSub') + ' · ' + (curLang() === 'zh' ? '关键词：' : 'keyword: ') + (d.query || ''),
        tRows
          ? '<div class="table-wrap"><table class="gene-table"><thead><tr>' +
            '<th>' + esc(T('colAo')) + '</th><th>' + esc(T('colPato')) + '</th><th>' + esc(T('colQuality')) + '</th>' +
            '<th>' + esc(T('colGenes')) + '</th><th>' + esc(T('colRows')) + '</th></tr></thead><tbody>' + tRows + '</tbody></table></div>'
          : '<p class="tool-hint">' + esc(curLang() === 'zh' ? '没有命中的表型条目' : 'No matching phenotype entries') + '</p>') +
      (gRows ? block(T('termGenes'), T('termGenesSub'),
        '<div class="table-wrap"><table class="gene-table"><thead><tr>' +
        '<th>' + esc(T('colSym')) + '</th><th>ZFIN</th><th>' + esc(T('colN')) + '</th><th>' + esc(T('colTypes')) + '</th>' +
        '</tr></thead><tbody>' + gRows + '</tbody></table></div>') : '') +
      literatureHtml(d.literature));
  }

  /* ---------------- 渲染：基因 → 表型 ---------------- */

  function renderGenePheno(d) {
    if (!d || !d.ok) {
      if (d && !d.ready) { renderNotReady(null); return; }
      var name = ($('phenoInput') && $('phenoInput').value || '').trim();
      showResult('<p class="tool-hint">' + esc(fmt(T('geneEmpty'), { g: name })) + '</p>' + literatureHtml(d && d.literature));
      return;
    }
    var g = d.gene || {};
    var rows = (d.groups || []).map(function (x) {
      var stages = (x.stages || []).slice(0, 4).join(', ');
      var pubs = (x.pubs || []).slice(0, 3);
      return '<tr><td><strong>' + esc(x.ao_name || '—') + '</strong><br><span class="zf-muted">' + esc(x.ao_id || '') + '</span></td>' +
        '<td>' + esc(x.pato_name || '—') + '<br><span class="zf-muted">' + esc(x.pato_id || '') + '</span></td>' +
        '<td>' + esc(x.quality || '—') + '</td>' +
        '<td class="zf-num">' + x.count + '</td>' +
        '<td class="zf-muted">' + esc(stages || '—') + '</td>' +
        '<td class="zf-muted">' + pubs.map(function (p) { return esc(String(p)); }).join(', ') +
        ((x.pubs || []).length > 3 ? ' …' : '') + '</td></tr>';
    }).join('');

    var head = '<div class="zf-kv">' +
      kv(T('kvGeneId'), g.ensembl_id || g.gene_id || '—') +
      kv(T('kvZfin'), g.zfin_id || '—') +
      kv(T('kvRows'), d.n_rows || 0) +
      kv(T('kvGroups'), (d.groups || []).length) +
      kv(T('kvPubs'), (d.publications || []).length) +
      (d.matched_symbol ? kv(T('kvMatched'), d.matched_symbol) : '') +
      '</div>' +
      (d.tried && d.tried.length > 1
        ? '<p class="zf-note">' + esc(T('kvTried')) + ' ' + d.tried.map(function (x) {
            return esc(x.term) + ' (' + x.rows + ')';
          }).join(' · ') + '</p>'
        : '');

    showResult(
      block(fmt(T('geneTitle'), { g: g.gene_symbol || g.gene_name || '' }), T('geneSub') + ' · ' + (d.source || 'ZFIN'),
        head + (rows
          ? '<div class="table-wrap"><table class="gene-table"><thead><tr>' +
            '<th>' + esc(T('colAo')) + '</th><th>' + esc(T('colPato')) + '</th><th>' + esc(T('colQuality')) + '</th>' +
            '<th>' + esc(T('colCount')) + '</th><th>' + esc(T('colStages')) + '</th><th>' + esc(T('colPubs')) + '</th>' +
            '</tr></thead><tbody>' + rows + '</tbody></table></div>'
          : '<p class="tool-hint">' + esc(fmt(T('geneEmpty'), { g: g.gene_symbol || '' })) + '</p>')) +
      ((d.publications || []).length
        ? block(T('pubTitle'), T('pubHint'), '<div class="zf-chips">' + d.publications.map(function (p) {
            return '<a class="zf-pill" target="_blank" rel="noopener" href="https://zfin.org/' + esc(p.pub_id) + '">' +
              esc(p.pub_id) + ' <small>' + p.count + '</small></a>';
          }).join('') + '</div>')
        : '') +
      literatureHtml(d.literature));
  }

  /* ---------------- 查询 ---------------- */

  /* 2026-10-31：统一读取响应。Hub 重启的那几十秒里，cloudflared 会返回一张 HTML 错误页
     （"<!DOCTYPE html>…"），老代码直接 r.json() 就抛
     `Unexpected token '<', "<!DOCTYPE "... is not valid JSON` —— 用户看到的就是这行天书。
     现在按 content-type 先判类型：不是 JSON 就归成「服务正在重启/繁忙」，让人知道该等而不是该改。 */
  function readJson(r) {
    var ct = '';
    try { ct = (r.headers && r.headers.get('content-type')) || ''; } catch (e) { ct = ''; }
    if (ct.indexOf('json') === -1) {
      var err = new Error(r.status >= 500
        ? '服务正在重启或繁忙（HTTP ' + r.status + '），请 10 秒后重试'
        : '接口返回了网页而非常规数据（HTTP ' + r.status + '）');
      err.gateway = true;
      return Promise.reject(err);
    }
    return r.json().catch(function () {
      return Promise.reject(new Error('服务返回的数据不完整（HTTP ' + r.status + '），请重试'));
    });
  }

  function get(url) {
    return fetch(url, { credentials: 'same-origin' }).then(function (r) {
      return readJson(r).catch(function (e) { return { __err: e }; }).then(function (d) {
        if (d && d.__err) throw d.__err;
        if (!r.ok) throw new Error(String((d && (d.detail || d.error)) || ('HTTP ' + r.status)).slice(0, 140));
        return d;
      });
    });
  }

  function failOut(e) {
    showResult('<p class="tool-hint">' + esc(fmt(T('fail'), { msg: String(e.message || e).slice(0, 140) })) + '</p>');
  }

  function run() {
    /* 方向只认路由：页面上没有能改方向的控件，避免「基因查表型」页跑出表型→基因的结果。 */
    var mode = routeMode();
    if ($('phenoMode')) $('phenoMode').value = mode;
    var q = ($('phenoInput') && $('phenoInput').value || '').trim();
    lastMode = mode;
    if (!q) { if ($('phenoInput')) $('phenoInput').focus(); return; }
    showResult('<span class="tool-loading">' + esc(T('loading')) + '</span>');
    if (mode === 'gene') {
      return get(GENE_URL + '?name=' + encodeURIComponent(q) + '&limit=200')
        .then(function (d) { lastPayload = d; renderGenePheno(d); })
        .catch(failOut);
    }
    return get(SEARCH_URL + '?q=' + encodeURIComponent(q) + '&limit=40&literature=6')
      .then(function (d) { lastPayload = d; renderTerm(d); })
      .catch(failOut);
  }

  function runTerm(term) {
    if ($('phenoInput')) $('phenoInput').value = term;
    if ($('phenoMode')) $('phenoMode').value = 'term';
    run();
  }

  /* ---------------- 绑定 ---------------- */

  function syncPlaceholder() {
    var inp = $('phenoInput');
    if (!inp) return;
    // 看路由而不是看下拉：两页方向固定，placeholder 不该被别处的值带偏
    var m = routeMode();
    if ($('phenoMode')) $('phenoMode').value = m;
    inp.placeholder = m === 'gene'
      ? (curLang() === 'zh' ? '基因名，如 sox9b、tp53' : 'Gene name, e.g. sox9b, tp53')
      : (curLang() === 'zh' ? '表型关键词，如 increased curvature' : 'Phenotype keyword, e.g. increased curvature');
  }

  function bind() {
    var btn = $('phenoRunBtn');
    if (btn) btn.addEventListener('click', run);
    var inp = $('phenoInput');
    if (inp) inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); run(); } });
    var mode = $('phenoMode');
    // 2026-10-31：placeholder 原来只在 change 里同步，程序改值／直接进页面时可能是反方向的文案
    // （表型查基因页写着「基因名，如 sox9b」，用户照着输基因名自然查不到）。改成独立函数，bind/boot 都调。
    if (mode) mode.addEventListener('change', syncPlaceholder);
    syncPlaceholder();
    var build = $('phenoBuildBtn');
    if (build) build.addEventListener('click', buildIndex);

    var box = $('toolResult');
    if (box) {
      box.addEventListener('click', function (e) {
        var act = e.target.closest && e.target.closest('[data-act]');
        if (act) {
          if (act.getAttribute('data-act') === 'build') buildIndex();
          else loadStats();
          return;
        }
        var pill = e.target.closest && e.target.closest('[data-term]');
        if (pill) { runTerm(pill.getAttribute('data-term')); return; }
      });
    }

    document.addEventListener('hub-lang-change', function () {
      if (!lastPayload) return;
      if (lastMode === 'gene') renderGenePheno(lastPayload);
      else renderTerm(lastPayload);
    });
  }

  function boot() {
    if (!$('phenoRunBtn')) return;
    bind();
    document.addEventListener('zf-feature-open', function (e) {
      var route = (e && e.detail && e.detail.route) || activeRoute();
      if (!isPhenoRoute(route)) return;
      applyRoute();
    });
    /* 2026-10-31：两个表型页共用同一个面板，app.js 在同一面板内换路由时不会再发
       zf-feature-open（早期实测：切到「表型查基因」后 placeholder 仍是「基因名」）。
       这里自己盯 hash，保证每次进页面都按路由把方向、placeholder、结果区对齐。 */
    window.addEventListener('hashchange', function () {
      if (isPhenoRoute(activeRoute())) applyRoute();
    });
    // 首次直接用 URL 进入时，app.js 已经发过事件（早于本模块绑定），这里补一次
    if (isPhenoRoute(activeRoute())) applyRoute();
  }

  function isPhenoRoute(r) {
    return r === 'phenotype' || r === 'phenotype-gene' || r === 'phenotype-term';
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
