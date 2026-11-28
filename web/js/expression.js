/* ==========================================================================
   expression.js —— Expression 功能页（组织表达谱）
   --------------------------------------------------------------------------
   数据来源（本地表达表 gene_expression，541,347 行 / 25,247 个基因，含 ZFIN 222,004 行）：
     GET /api/expression/tissues            → 组织索引（1,151 个解剖实体 + 覆盖基因数）
     GET /api/expression/gene?name=&top=    → 单基因：Bgee 组织分（0–100）+ Atlas 分期 TPM
     GET /api/expression/top?tissue=&n=     → 某组织里表达最高的基因
     GET /api/expression/compare?genes=&top= → 多基因 × 共同组织矩阵
   口径：两组单位不同（Bgee 表达分 0–100 / Atlas TPM），分区块显示，不跨组比较；
        未知值显示为「无记录」，不补 0。
   ========================================================================== */
(function () {
  'use strict';

  var TISSUES_URL = '/api/expression/tissues';
  var GENE_URL = '/api/expression/gene';
  var TOP_URL = '/api/expression/top';
  var COMPARE_URL = '/api/expression/compare';

  var TEXT = {
    zh: {
      loading: '查询表达…', fail: '查询失败：{msg}',
      needGene: '请输入基因名（如 tp53、sox9b）；对比模式可用逗号分隔多个基因',
      needTissue: '请先选择一个组织',
      bgeeTitle: 'Bgee 组织表达', bgeeSub: '0–100',
      atlasTitle: 'Expression Atlas 胚胎分期', atlasSub: 'TPM',
      zfinTitle: 'ZFIN 定性表达记录', zfinSub: '组织', zfinUnit: '条记录',
      noZfin: '本地表中该基因没有 ZFIN 表达记录',
      scaledNote: '条长按本组分数范围拉伸',
      topTitle: '组织 Top 基因', topSub: '',
      cmpTitle: '多基因对比', cmpSub: '',
      colGene: '基因', colLevel: '表达分', colStage: '分期', colTpm: 'TPM', colQuality: '质量分',
      colTissue: '组织', colCover: '覆盖基因数', colChr: '染色体', colSym: '符号',
      kvGene: '基因', kvGeneId: '基因库 ID', kvCoordLib: '坐标库 ID', kvChr: '染色体',
      kvTissues: '组织数', kvStages: '分期数', kvRows: '表达记录', kvMax: '最高表达组织',
      note: '说明', noRecord: '无记录', noAtlas: '本地表中该基因没有 Atlas 分期记录', noBgee: '本地表中该基因没有 Bgee 组织记录',
      pickTissueHint: '下方组织列表可点击直接查：',
      tissuesLoaded: '已加载 {n} 个组织', openGene: '打开基因卡'
    },
    en: {
      loading: 'Querying expression…', fail: 'Query failed: {msg}',
      needGene: 'Enter a gene symbol (e.g. tp53, sox9b); in compare mode, separate gene symbols with commas',
      needTissue: 'Select a tissue first',
      bgeeTitle: 'Bgee tissue expression', bgeeSub: '0–100',
      atlasTitle: 'Expression Atlas embryonic stages', atlasSub: 'TPM',
      zfinTitle: 'ZFIN qualitative expression', zfinSub: 'Anatomy', zfinUnit: 'records',
      noZfin: 'No ZFIN expression records for this gene in the local table',
      scaledNote: 'bars scaled within this group',
      topTitle: 'Top genes in a tissue', topSub: '',
      cmpTitle: 'Multi-gene comparison', cmpSub: '',
      colGene: 'Gene', colLevel: 'Score', colStage: 'Stage', colTpm: 'TPM', colQuality: 'Quality',
      colTissue: 'Tissue', colCover: 'Genes', colChr: 'Chr', colSym: 'Symbol',
      kvGene: 'Gene', kvGeneId: 'Library ID', kvCoordLib: 'Coord. library ID', kvChr: 'Chr',
      kvTissues: 'Tissues', kvStages: 'Stages', kvRows: 'Expression rows', kvMax: 'Top tissue',
      note: 'Note', noRecord: 'No record', noAtlas: 'No Atlas stage rows for this gene in the local table', noBgee: 'No Bgee tissue rows for this gene in the local table',
      pickTissueHint: 'Click a tissue below to query it directly:',
      tissuesLoaded: '{n} tissues loaded', openGene: 'Open gene card'
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
  function num(v) { return (v == null || v === '') ? '—' : Number(v).toFixed(2).replace(/\.00$/, ''); }
  function activeRoute() {
    return ((location.hash || '').replace(/^#\/?/, '').split(/[/?#]/)[0]) || 'home';
  }

  var lastPayload = null, lastMode = 'gene';

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
  /* Bgee 分数常挤在 99.9 附近，按 0–100 归一后所有条几乎等长。
     stretch=true 时按本组最低 / 最高分拉伸到 10%~100%，只提高视觉区分度，
     行末仍显示原始分，副标题标注条长口径，避免把放大后的差距读成真实差距。 */
  function barRows(items, max, warm, stretch) {
    var vals = items.map(function (it) { return Number(it.level) || 0; });
    var lo = vals.length ? Math.min.apply(null, vals) : 0;
    var hi = vals.length ? Math.max.apply(null, vals) : 0;
    var span = hi - lo;
    return items.map(function (it) {
      var pct = (stretch && span > 0.000001)
        ? 10 + Math.round((((Number(it.level) || 0) - lo) / span) * 90)
        : Math.max(2, Math.round((it.level / (max || 100)) * 100));
      return '<div class="zf-bar-row"><span class="zf-bar-label" title="' + esc(it.label) + '">' + esc(it.label) + '</span>' +
        '<span class="zf-bar"><span class="zf-bar-fill' + (warm ? ' is-warm' : '') + '" style="width:' + pct + '%"></span></span>' +
        '<span class="zf-bar-val">' + num(it.level) + '</span></div>';
    }).join('');
  }

  /* ---------------- 渲染：ZFIN 定性表达 ----------------
     后端 gene_profile 已返回 zfin 组（组织 + 时期 + 来源，无数值分数）。
     这里按组织聚合成记录条数单独成块，不与 Bgee 分值 / Atlas TPM 同轴比较。 */
  function renderZfinBlock(d) {
    var zf = d.zfin || {};
    var rows = zf.all_records || zf.records || [];
    var agg = {}, srcs = {};
    rows.forEach(function (r) {
      var k = r.tissue || '—';
      agg[k] = (agg[k] || 0) + 1;
      var s = r.source || 'zfin';
      srcs[s] = (srcs[s] || 0) + 1;
    });
    var items = Object.keys(agg).map(function (k) { return { label: k, level: agg[k] }; })
      .sort(function (a, b) { return b.level - a.level; });
    var srcText = Object.keys(srcs).sort().map(function (s) { return s + ' ' + srcs[s]; }).join(' · ');
    var sub = T('zfinSub') + ' · ' + (zf.n_records || rows.length) + ' ' + T('zfinUnit') +
      (srcText ? ' · ' + srcText : '');
    return block(T('zfinTitle'), sub,
      items.length ? barRows(items.slice(0, 24), items[0].level || 1, false)
        : '<p class="tool-hint">' + esc(T('noZfin')) + '</p>');
  }

  /* ---------------- 渲染：单基因 ---------------- */

  function renderGene(d) {
    var g = d.gene || {};
    var bgee = d.bgee || {}, atlas = d.atlas || {};
    var tRows = (bgee.tissues || []).map(function (t) { return { label: t.tissue, level: t.level }; });
    // 同一分期可能有多条（成体组织各自一行），带上 tissue 才看得出区别，
    // 否则界面上会出现两行完全相同的「成体」。
    var sRows = (atlas.stages || []).map(function (s) {
      return { label: s.tissue ? (s.stage + ' · ' + s.tissue) : s.stage, level: s.level };
    });

    var head = '<div class="zf-kv">' +
      '<div class="zf-kv-item"><span class="zf-kv-k">' + esc(T('kvGene')) + '</span><span class="zf-kv-v">' + esc(g.gene_symbol || g.gene_name || '—') + '</span></div>' +
      '<div class="zf-kv-item"><span class="zf-kv-k">' + esc(T('kvGeneId')) + '</span><span class="zf-kv-v">' + esc(g.ensembl_id || g.zfin_id || g.gene_id || '—') + '</span></div>' +
      '<div class="zf-kv-item"><span class="zf-kv-k">' + esc(T('kvChr')) + '</span><span class="zf-kv-v">' + esc(g.chromosome || '—') + '</span></div>' +
      '<div class="zf-kv-item"><span class="zf-kv-k">' + esc(T('kvTissues')) + '</span><span class="zf-kv-v">' + (bgee.n_tissues || 0) + '</span></div>' +
      '<div class="zf-kv-item"><span class="zf-kv-k">' + esc(T('kvStages')) + '</span><span class="zf-kv-v">' + (atlas.n_stages || 0) + '</span></div>' +
      '<div class="zf-kv-item"><span class="zf-kv-k">' + esc(T('kvRows')) + '</span><span class="zf-kv-v">' + (d.n_rows || 0) + '</span></div>' +
      '</div>';

    /* 2026-11-07：用户要求功能页不再出现解释性小字，这段「共 N 个组织…显示前 M 个」
       说明整段移除；tRows 仍按分值截断，结果本身不变。 */
    var more = '';

    var html = block('', '', head) +
      block(T('bgeeTitle'), T('bgeeSub') + ' · ' + T('scaledNote'),
        tRows.length ? barRows(tRows, 100, false, true) + more
          : '<p class="tool-hint">' + esc(T('noBgee')) + '</p>') +
      block(T('atlasTitle'), T('atlasSub'),
        sRows.length ? barRows(sRows, Math.max.apply(null, sRows.map(function (x) { return x.level; }).concat([1])), true)
          : '<p class="tool-hint">' + esc(T('noAtlas')) + '</p>') +
      renderZfinBlock(d) +
      (d.note ? '<p class="zf-note"><strong>' + esc(T('note')) + (curLang() === 'zh' ? '：' : ': ') + '</strong>' +
        esc(curLang() === 'zh' ? d.note : (d.note_en || d.note)) + '</p>' : '') +
      '<p class="zf-note"><a class="zf-link" href="#/gene/' + encodeURIComponent(g.gene_name || g.gene_symbol || '') + '">' +
      esc(T('openGene')) + ' →</a></p>';

    showResult(html);
  }

  /* ---------------- 渲染：组织 Top ---------------- */

  function renderTop(d) {
    var rows = d.genes || [];
    if (!rows.length) {
      showResult('<p class="tool-hint">' + esc(fmt(T('fail'), { msg: T('noRecord') })) + '</p>');
      return;
    }
    var items = rows.map(function (r) { return { label: r.gene_symbol || r.gene_name, level: r.level }; });
    var table = '<div class="table-wrap"><table class="gene-table"><thead><tr>' +
      '<th>' + esc(T('colSym')) + '</th><th>' + esc(T('colLevel')) + '</th><th>' + esc(T('colChr')) + '</th><th>' + esc(T('colGene')) + '</th>' +
      '</tr></thead><tbody>' + rows.map(function (r) {
        var sym = r.gene_symbol || r.gene_name || '';
        return '<tr class="gene-row" data-name="' + esc(r.gene_name || sym) + '">' +
          '<td><strong>' + esc(sym) + '</strong></td>' +
          '<td class="zf-num">' + num(r.level) + '</td>' +
          '<td>' + esc(r.chromosome || '—') + '</td>' +
          '<td class="zf-muted">' + esc(r.ensembl_id || r.zfin_id || '—') + '</td></tr>';
      }).join('') + '</tbody></table></div>';

    showResult(block(d.tissue || '', T('topSub') + ' · ' + (d.unit || '') + ' · ' + T('scaledNote'),
      barRows(items, 100, false, true) + table));
  }

  /* ---------------- 渲染：多基因对比 ---------------- */

  function renderCompare(d) {
    var genes = d.genes || [], tissues = d.tissues || [], matrix = d.matrix || [];
    if (!genes.length || !tissues.length) {
      showResult('<p class="tool-hint">' + esc(curLang() === 'zh' ? '没有共同覆盖的组织可比较' : 'No commonly covered tissues to compare') + '</p>');
      return;
    }
    var matrixValues = [];
    matrix.forEach(function (row) { row.forEach(function (v) { if (v != null) matrixValues.push(v); }); });
    var max = Math.max.apply(null, matrixValues.concat([1]));
    var min = matrixValues.length ? Math.min.apply(null, matrixValues) : 0;
    var span = max - min;

    var head = '<tr><th>' + esc(T('colTissue')) + '</th>' + genes.map(function (g) {
      return '<th>' + esc(g) + '</th>';
    }).join('') + '</tr>';

    var body = tissues.map(function (t, ti) {
      return '<tr><td>' + esc(t) + '</td>' + genes.map(function (g, gi) {
        var v = matrix[gi] ? matrix[gi][ti] : null;
        if (v == null) return '<td class="zf-muted">—</td>';
        var pct = span > 0.000001
          ? 8 + Math.round((((v - min) / span) * 92))
          : Math.max(3, Math.round((v / max) * 100));
        return '<td class="zf-num"><span class="zf-bar" style="display:inline-block;width:46px;vertical-align:middle">' +
          '<span class="zf-bar-fill" style="width:' + pct + '%"></span></span> ' + num(v) + '</td>';
      }).join('') + '</tr>';
    }).join('');

    showResult(block(T('cmpTitle'), T('cmpSub') + ' · ' + (d.unit || '') + ' · ' + T('scaledNote'),
      '<div class="table-wrap"><table class="zf-matrix"><thead>' + head + '</thead><tbody>' + body + '</tbody></table></div>'));
  }

  /* ---------------- 组织下拉 / 组织列表 ---------------- */

  var tissuesLoaded = false;
  function loadTissues(force) {
    var sel = $('exprTissue');
    if (!sel) return Promise.resolve(null);
    if (tissuesLoaded && !force) return Promise.resolve(null);
    return fetch(TISSUES_URL, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var items = (d && d.tissues) || [];
        sel.innerHTML = '<option value="">' + esc(curLang() === 'zh' ? '选择组织…' : 'Select a tissue…') + '</option>' +
          items.map(function (t) {
            return '<option value="' + esc(t.tissue) + '">' + esc(t.tissue) + '</option>';
          }).join('');
        tissuesLoaded = true;
        return items;
      })
      .catch(function () { return null; });
  }

  /* ---------------- 请求 ---------------- */

  function get(url) {
    return fetch(url, { credentials: 'same-origin' }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (d) {
        if (!r.ok) throw new Error(String((d && (d.detail || d.error)) || ('HTTP ' + r.status)).slice(0, 140));
        return d;
      });
    });
  }

  function run() {
    var mode = ($('exprMode') && $('exprMode').value) || 'gene';
    var q = ($('exprQueryInput') && $('exprQueryInput').value || '').trim();
    var tissue = ($('exprTissue') && $('exprTissue').value) || '';
    lastMode = mode;

    if (mode === 'tissue') {
      if (!tissue) { if ($('exprTissue')) $('exprTissue').focus(); return; }
      showResult('<span class="tool-loading">' + esc(T('loading')) + '</span>');
      return get(TOP_URL + '?tissue=' + encodeURIComponent(tissue) + '&n=30')
        .then(function (d) { lastPayload = d; renderTop(d); })
        .catch(failOut);
    }
    if (!q) { if ($('exprQueryInput')) $('exprQueryInput').focus(); return; }

    showResult('<span class="tool-loading">' + esc(T('loading')) + '</span>');
    if (mode === 'compare') {
      return get(COMPARE_URL + '?genes=' + encodeURIComponent(q) + '&top=14')
        .then(function (d) { lastPayload = d; renderCompare(d); })
        .catch(failOut);
    }
    return get(GENE_URL + '?name=' + encodeURIComponent(q) + '&top=24')
      .then(function (d) { lastPayload = d; renderGene(d); })
      .catch(failOut);
  }

  function failOut(e) {
    showResult('<p class="tool-hint">' + esc(fmt(T('fail'), { msg: String(e.message || e).slice(0, 140) })) + '</p>');
  }

  /* ---------------- 绑定 ---------------- */

  function bind() {
    var btn = $('exprRunBtn');
    if (btn) btn.addEventListener('click', run);
    var inp = $('exprQueryInput');
    if (inp) inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); run(); } });
    var mode = $('exprMode');
    if (mode) mode.addEventListener('change', function () {
      var isTissue = mode.value === 'tissue';
      if (isTissue) loadTissues(false);
      if (inp) inp.placeholder = isTissue
        ? (curLang() === 'zh' ? '（组织模式：请用下方下拉框选组织）' : '(tissue mode: select a tissue from the list)')
        : (curLang() === 'zh' ? '基因名 / 别名 / Ensembl，如 tp53；对比模式用逗号分隔' : 'Gene name / alias / Ensembl, e.g. tp53; commas in compare mode');
    });
    var sel = $('exprTissue');
    if (sel) sel.addEventListener('change', function () {
      if (!sel.value) return;
      if ($('exprMode')) $('exprMode').value = 'tissue';
      run();
    });

    var box = $('toolResult');
    if (box) {
      box.addEventListener('click', function (e) {
        var tr = e.target.closest ? e.target.closest('tr.gene-row') : null;
        if (!tr) return;
        var name = tr.getAttribute('data-name');
        if (name) location.hash = '#/gene/' + encodeURIComponent(name);
      });
    }

    document.addEventListener('hub-lang-change', function () {
      if (!lastPayload) return;
      if (lastMode === 'tissue') renderTop(lastPayload);
      else if (lastMode === 'compare') renderCompare(lastPayload);
      else renderGene(lastPayload);
    });
  }

  function boot() {
    if (!$('exprRunBtn')) return;
    bind();
    document.addEventListener('zf-feature-open', function (e) {
      var route = (e && e.detail && e.detail.route) || '';
      if (route !== 'expression') return;
      loadTissues(false);
      var box = $('toolResult');
      if (box && !box.textContent.trim() && $('exprQueryInput') && !$('exprQueryInput').value) run();
    });
    // 首次直接用 URL 进入时，app.js 已经发过事件（早于本模块绑定），这里补一次
    if (activeRoute() === 'expression') {
      loadTissues(false);
      var b = $('toolResult');
      if (b && !b.textContent.trim() && $('exprQueryInput') && !$('exprQueryInput').value) run();
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
