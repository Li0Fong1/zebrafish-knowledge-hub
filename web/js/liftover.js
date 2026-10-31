/* ==========================================================================
   liftover.js —— Omics / Genome / Liftover 功能页
   --------------------------------------------------------------------------
   显式来源／目标成对换算（第 3 期：强制自选，不设默认）：

     GET /api/genome/liftover?build=GRCz11&chrom=5&start=...&end=...&to=GRCz12tu

   口径：
     · 来源与目标都必须由用户选择；缺一 → need_source_build／need_target_build；
     · 已收录版本均可选自选；缺链 → chain_unavailable；
     · GRCz13ab 尚无公开／本机 chain，亦无 MAF → chain_unavailable + maf_unavailable；
     · 跨断裂点（split）或对不上 → 不给近似坐标。
   后端 coord_liftover.lift_region 决定成败；前端只翻译 code。
   ========================================================================== */
(function () {
  'use strict';

  var API = '/api/genome/liftover';

  var TEXT = {
    zh: {
      loading: '换算中…',
      loadingSec: '换算中… {s}s（首次要建链索引，冷启动可能 1–2 分钟）',
      loadingTip: '链文件较大，首次换算需要建索引；之后同样的查询约 1 秒。',
      fail: '换算失败：{msg}',
      needRegion: '请填染色体与起止坐标',
      needSource: '请先选择来源组装版本（不设默认）',
      needTarget: '请先选择目标组装版本（不设默认）',
      okTitle: '换算结果',
      kSrc: '源区间',
      kTgt: '目标区间',
      kStrand: '链方向',
      kLen: '长度',
      kChain: '经过的链',
      takeSeq: '按这段坐标取序列',
      backNote: '路由：{from} → {to}（缺链／缺 MAF 时不给近似坐标）',
      codeMissing: '服务器上没有这条换算链，无法换算',
      codeUnavailable: '没有 {from} → {to} 的 UCSC chain 路由；亦无 MAF 备援',
      codeUnsupported: '该组装版本不支持换算',
      codeSplit: '这段跨了链的断裂点，请缩小范围分段换算',
      codeFail: '这段坐标在链文件里对不上，无法换算',
      codeReverse: '不支持换到 {to}：当前没有可用路由',
      noApprox: '没有给出任何近似坐标。',

      /* ── 批量 ID 互转 ───────────────────────────────────────────── */
      idNeed: '先粘贴 ID：每行一个，或用逗号/顿号分隔',
      idTooMany: '一次最多 200 条，已取前 200 条',
      idRunning: '互转中…（最多 200 条，需逐条查库）',
      idFail: '互转失败：{msg}',
      idSummary: '{n} 条输入：唯一命中 {u} · 多候选 {a} · 未匹配 {m}',
      idColQuery: '输入',
      idColStatus: '匹配',
      idColStd: '符号',
      idColLocal: '本地符号',
      idColIds: '跨库 ID',
      idColEns: 'Ensembl',
      idColZfin: 'ZFIN',
      idColNcbi: 'NCBI Gene',
      idColChrom: '染色体',
      stUnique: '唯一',
      stAmb: '多候选 {n}',
      stNone: '未匹配',
      copyOk: '已复制表格（制表符分隔，可直接粘进 Excel）',
      copyFail: '复制失败，请手动选中表格',
      csvEmpty: '还没有结果可导出'
    },
    en: {
      loading: 'Converting…',
      loadingSec: 'Converting… {s}s (first run builds the chain index — cold start can take 1–2 min)',
      loadingTip: 'The chain file is large: the first conversion builds an index, later ones take about a second.',
      fail: 'Conversion failed: {msg}',
      needRegion: 'Chromosome and start/end are required',
      needSource: 'Choose a source assembly first (no default)',
      needTarget: 'Choose a target assembly first (no default)',
      okTitle: 'Result',
      kSrc: 'Source span',
      kTgt: 'Target span',
      kStrand: 'Strand',
      kLen: 'Length',
      kChain: 'Chain',
      takeSeq: 'Fetch this sequence',
      backNote: 'Route: {from} → {to} (no approximate coords if chain/MAF missing)',
      codeMissing: 'The chain file for this conversion is missing on the server',
      codeUnavailable: 'No UCSC chain route for {from} → {to}; MAF fallback unavailable',
      codeUnsupported: 'This assembly is not supported for conversion',
      codeSplit: 'This span crosses a chain break — narrow it down and convert in pieces',
      codeFail: 'No match for this span in the chain file',
      codeReverse: 'Converting to {to} is not supported: no routable path',
      noApprox: 'No approximate coordinates were returned.',

      /* ── batch ID mapping ───────────────────────────────────────── */
      idNeed: 'Paste IDs first: one per line, or comma-separated',
      idTooMany: 'Up to 200 per run — using the first 200',
      idRunning: 'Mapping… (up to 200, one lookup each)',
      idFail: 'Mapping failed: {msg}',
      idSummary: '{n} inputs: {u} unique · {a} ambiguous · {m} not found',
      idColQuery: 'Input',
      idColStatus: 'Match',
      idColStd: 'Symbol',
      idColLocal: 'Local symbol',
      idColIds: 'Cross-DB IDs',
      idColEns: 'Ensembl',
      idColZfin: 'ZFIN',
      idColNcbi: 'NCBI Gene',
      idColChrom: 'Chr',
      stUnique: 'unique',
      stAmb: 'ambiguous ({n})',
      stNone: 'not found',
      copyOk: 'Table copied (tab-separated — paste straight into Excel)',
      copyFail: 'Copy failed — select the table manually',
      csvEmpty: 'No results to export yet'
    }
  };

  function lang() {
    var l = (document.documentElement.getAttribute('lang') || 'en').toLowerCase();
    return l.indexOf('zh') === 0 ? 'zh' : 'en';
  }
  function T(k) {
    var d = TEXT[lang()] || TEXT.en;
    return d[k] != null ? d[k] : (TEXT.en[k] || '');
  }
  function fmt(s, o) {
    return String(s).replace(/\{(\w+)\}/g, function (m, k) { return o[k] != null ? o[k] : m; });
  }
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function showResult(html, mode) {
    var box = $('toolResult');
    if (!box) return;
    box.innerHTML = html;
    box.classList.remove('hidden');
    // 记下这份结果属于哪个模式：切模式时据此决定要不要清掉（见 setMode）
    box.setAttribute('data-lift-result', mode === 'idmap' ? 'idmap' : 'coord');
  }

  function get(url) {
    return fetch(url, { credentials: 'same-origin' }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (d) {
        if (!r.ok) {
          var msg = (d && (d.detail || d.error)) || ('HTTP ' + r.status);
          throw new Error(String(msg).slice(0, 200));
        }
        return d;
      });
    });
  }

  /* 只认有数的结果：ok 且 target_span 非空 */
  function okCard(d) {
    var srcLen = (d.start != null && d.end != null) ? (Number(d.end) - Number(d.start) + 1) : null;
    var rows = [
      [T('kSrc'), d.source_build + ' ' + (d.source_span || '')],
      [T('kTgt'), d.target_build + ' ' + (d.target_span || '')],
      [T('kStrand'), d.strand || '+'],
      [T('kLen'), srcLen ? srcLen.toLocaleString('en-US') + ' bp' : '—'],
      [T('kChain'), d.chain || '—']
    ];
    var body = rows.map(function (r) {
      return '<tr><th>' + esc(r[0]) + '</th><td class="prov-mono">' + esc(r[1]) + '</td></tr>';
    }).join('');
    var seqBtn = d.chromosome
      ? '<button type="button" class="tool-secondary-btn" data-lift-take-seq="1"' +
        ' data-chrom="' + esc(d.chromosome) + '" data-start="' + esc(d.start) + '" data-end="' + esc(d.end) + '"' +
        ' data-asm="' + esc(d.target_build || 'GRCz12tu') + '">' +
        esc(T('takeSeq')) + '</button>'
      : '';
    return '<div class="tool-summary">' + esc(T('okTitle')) + ' · ' +
      esc(d.source_build) + ' → ' + esc(d.target_build) + '</div>' +
      '<div class="table-wrap"><table class="gene-table"><tbody>' + body + '</tbody></table></div>' +
      (seqBtn ? '<div class="tool-row">' + seqBtn + '</div>' : '');
  }

  /* 失败时讲清楚「为什么没有数」，而不是给一个像真的区间 */
  function failCard(d) {
    var code = d.code || '';
    var msg;
    if (code === 'unsupported_build') {
      msg = T('codeUnsupported') + ' ' + T('noApprox');
    } else if (code === 'chain_unavailable' || code === 'maf_unavailable' || code === 'missing_chain') {
      msg = (code === 'missing_chain' ? T('codeMissing') : fmt(T('codeUnavailable'), {
        from: d.source_build || '', to: d.target_build || ''
      })) + ' ' + T('noApprox');
    } else if (code === 'need_source_build') {
      msg = T('needSource');
    } else if (code === 'need_target_build') {
      msg = T('needTarget');
    } else if (code === 'target_build_not_supported') {
      msg = fmt(T('codeReverse'), { from: d.source_build || '', to: d.target_build || '' }) + ' ' + T('noApprox');
    } else if (code === 'liftover_split') {
      msg = T('codeSplit');
    } else {
      msg = T('codeFail') + ' ' + T('noApprox');
    }
    if (lang() === 'zh' && d.message_zh) msg = d.message_zh;
    if (lang() !== 'zh' && d.message_en) msg = d.message_en;
    return '<div class="tool-summary warn">' + esc(code || 'liftover_failed') + '</div>' +
      '<p class="tool-hint warn">' + esc(msg) + '</p>' +
      '<p class="tool-hint">' + esc((d.source_build || '') + ' ' + (d.source_span || '')) + '</p>';
  }

  function applyPasteLine(line) {
    var s = String(line || '').trim();
    if (!s) return false;
    var m = s.match(/\b(GRCz\d+[a-z]*|Zv\d+|ASM1840007v1|fDanRer4\.1)\b/i);
    if (m && $('liftSourceBuild')) {
      $('liftSourceBuild').value = m[1];
      $('liftSourceBuild').dispatchEvent(new Event('change', { bubbles: true }));
    }
    var r = s.match(/(?:chr)?\s*([0-9XYM]+)\s*[:\s]+\s*(\d+)\s*[-–—－−~～至到一]+\s*(\d+)/i);
    if (!r) r = s.match(/^\s*([0-9XYM]+)\s+(\d+)\s+(\d+)\s*$/i);
    if (!r) return false;
    if ($('liftChrom')) $('liftChrom').value = r[1];
    if ($('liftStart')) $('liftStart').value = r[2];
    if ($('liftEnd')) $('liftEnd').value = r[3];
    return true;
  }

  function run() {
    var paste = (($('liftPasteInput') && $('liftPasteInput').value) || '').trim();
    var chrom = (($('liftChrom') && $('liftChrom').value) || '').trim();
    var start = (($('liftStart') && $('liftStart').value) || '').trim();
    var end = (($('liftEnd') && $('liftEnd').value) || '').trim();
    if ((!chrom || !start || !end) && paste) {
      applyPasteLine(paste.split(/[\n;]+/)[0] || '');
      chrom = (($('liftChrom') && $('liftChrom').value) || '').trim();
      start = (($('liftStart') && $('liftStart').value) || '').trim();
      end = (($('liftEnd') && $('liftEnd').value) || '').trim();
    }
    var build = (($('liftSourceBuild') && $('liftSourceBuild').value) || '').trim();
    var target = (($('liftTargetBuild') && $('liftTargetBuild').value) || '').trim();
    var strand = ($('liftStrand') && $('liftStrand').value) || '+';
    if (!build) {
      showResult('<p class="tool-hint warn">' + esc(T('needSource')) + '</p>');
      return;
    }
    if (!target) {
      showResult('<p class="tool-hint warn">' + esc(T('needTarget')) + '</p>');
      return;
    }
    if (!chrom || !start || !end) {
      showResult('<p class="tool-hint warn">' + esc(T('needRegion')) + '</p>');
      return;
    }
    var url = API + '?build=' + encodeURIComponent(build) +
      '&chrom=' + encodeURIComponent(chrom.replace(/^chr/i, '')) +
      '&start=' + encodeURIComponent(start) + '&end=' + encodeURIComponent(end) +
      '&strand=' + encodeURIComponent(strand) +
      '&to=' + encodeURIComponent(target);
    // 冷启动实测可到 90s（第一次要建链索引），只写「换算中…」像卡死了。
    // 3 秒后开始报秒数并说明原因，用户知道该等而不是反复点。
    var t0 = Date.now();
    showResult('<span class="tool-loading">' + esc(T('loading')) + '</span>');
    var tick = setInterval(function () {
      var s = Math.round((Date.now() - t0) / 1000);
      if (s < 3) return;
      var box = $('toolResult');
      if (!box) return;
      box.innerHTML = '<p class="tool-hint">' + esc(fmt(T('loadingSec'), { s: s })) + '</p>' +
        '<p class="tool-hint">' + esc(T('loadingTip')) + '</p>';
    }, 1000);
    get(url).then(function (d) {
      clearInterval(tick);
      if (d && d.ok && d.target_span) showResult(okCard(d));
      else showResult(failCard(d || {}));
    }).catch(function (e) {
      clearInterval(tick);
      showResult('<p class="tool-hint warn">' + esc(fmt(T('fail'), { msg: String(e.message || e).slice(0, 200) })) + '</p>');
    });
  }

  /* 选来源后隐藏不可用目标；不选来源则全显。
     口径（与「只会藏同版本」对账）：
       · 主线 Zv3–Zv9／GRCz10／11／12*：按 routable_pairs（可多跳，Zv6→12tu 仍可见）
       · 侧挂 ASM1840007v1／fDanRer4.1：只按本机有档案的单跳 edges（否则 BFS 会通到全表，看起来像没过滤）
     数据：GET /api/genome/liftover-routes */
  var ROUTES_API = '/api/genome/liftover-routes';
  var routesReady = false;
  var reachableBySource = {}; // 多跳可到达
  var directBySource = {};    // 单跳 present edge
  var CORE_BUILDS = {
    Zv3: 1, Zv4: 1, Zv5: 1, Zv6: 1, Zv7: 1, Zv8: 1, Zv9: 1,
    GRCz10: 1, GRCz11: 1, GRCz12tu: 1, GRCz12ab: 1
  };
  // API 失败时侧挂仍可按已知直连边过滤
  var FALLBACK_DIRECT = {
    ASM1840007v1: { GRCz11: 1, GRCz10: 1 },
    'fDanRer4.1': { GRCz12tu: 1 }
  };

  function unwrapRoutes(d) {
    if (!d || typeof d !== 'object') return null;
    return d.data && typeof d.data === 'object' && !Array.isArray(d.data) ? d.data : d;
  }

  function buildPairMap(payload) {
    var map = {};
    var pairs = (payload && payload.routable_pairs) || [];
    pairs.forEach(function (p) {
      if (!p || !p.source || !p.target) return;
      if (!map[p.source]) map[p.source] = {};
      map[p.source][p.target] = 1;
    });
    return map;
  }

  function buildDirectMap(payload) {
    var map = {};
    var edges = (payload && payload.edges) || [];
    edges.forEach(function (e) {
      if (!e || !e.source || !e.target || !e.present) return;
      if (!map[e.source]) map[e.source] = {};
      map[e.source][e.target] = 1;
    });
    return map;
  }

  function isCoreBuild(b) {
    return !!(b && CORE_BUILDS[b]);
  }

  function targetAllowed(src, tgt) {
    if (!tgt) return true;
    if (src && tgt === src) return false;
    if (!src) return true;
    // 侧挂：只显示单跳有链的目标（API 未就绪时用 FALLBACK）
    if (!isCoreBuild(src)) {
      var dmap = routesReady ? directBySource : FALLBACK_DIRECT;
      var dset = (dmap && dmap[src]) || FALLBACK_DIRECT[src] || {};
      return !!dset[tgt];
    }
    // 主线：API 未就绪时先全显（仅藏同版本）；就绪后按多跳可到达
    if (!routesReady) return true;
    var set = reachableBySource[src];
    if (!set) return false;
    return !!set[tgt];
  }

  function filterTargetBuilds() {
    var srcEl = $('liftSourceBuild');
    var tgtEl = $('liftTargetBuild');
    if (!tgtEl) return;
    var src = srcEl ? String(srcEl.value || '').trim() : '';
    Array.prototype.forEach.call(tgtEl.options || [], function (opt) {
      if (!opt || !opt.value) return; // 占位「请选择…」始终保留
      var show = targetAllowed(src, opt.value);
      opt.hidden = !show;
      opt.disabled = !show;
    });
    var cur = String(tgtEl.value || '').trim();
    if (cur && !targetAllowed(src, cur)) {
      tgtEl.value = '';
      tgtEl.dispatchEvent(new Event('change', { bubbles: true }));
    }
    // hero 药丸与下拉共用同一套口径
    document.querySelectorAll('[data-chip-target="liftTargetBuild"]').forEach(function (b) {
      var v = b.getAttribute('data-chip-value') || '';
      var show = targetAllowed(src, v);
      b.hidden = !show;
      b.style.display = show ? '' : 'none';
      if (!show) {
        b.classList.remove('is-on');
        b.setAttribute('aria-pressed', 'false');
      }
    });
    if (srcEl) {
      srcEl.setAttribute('data-lift-filter', routesReady ? 'routes' : 'fallback');
    }
    if (window.ZFPageChips && window.ZFPageChips.syncAll) {
      window.ZFPageChips.syncAll();
    }
  }

  function loadReachableRoutes() {
    // 先套侧挂 fallback，避免路由表返回前点 ASM 仍像「只藏同版本」
    directBySource = FALLBACK_DIRECT;
    filterTargetBuilds();
    return get(ROUTES_API).then(function (d) {
      var payload = unwrapRoutes(d) || {};
      reachableBySource = buildPairMap(payload);
      var fromApi = buildDirectMap(payload);
      // 合并 fallback，避免 edges 缺字段时侧挂失滤
      directBySource = Object.assign({}, FALLBACK_DIRECT, fromApi);
      Object.keys(FALLBACK_DIRECT).forEach(function (k) {
        directBySource[k] = Object.assign({}, FALLBACK_DIRECT[k], fromApi[k] || {});
      });
      routesReady = true;
      filterTargetBuilds();
    }).catch(function () {
      routesReady = false;
      reachableBySource = {};
      directBySource = FALLBACK_DIRECT;
      filterTargetBuilds();
    });
  }

  function bind() {
    var btn = $('liftRunBtn');
    if (btn) btn.addEventListener('click', run);
    ['liftChrom', 'liftStart', 'liftEnd'].forEach(function (id) {
      var el = $(id);
      if (el) el.addEventListener('keydown', function (e) { if (e.key === 'Enter') run(); });
    });
    var paste = $('liftPasteInput');
    if (paste) {
      paste.addEventListener('change', function () { applyPasteLine((paste.value || '').split(/[\n;]+/)[0] || ''); });
      paste.addEventListener('blur', function () { applyPasteLine((paste.value || '').split(/[\n;]+/)[0] || ''); });
    }

    var srcSel = $('liftSourceBuild');
    if (srcSel && !srcSel.dataset.routeFilterBound) {
      srcSel.dataset.routeFilterBound = '1';
      srcSel.addEventListener('change', filterTargetBuilds);
    }
    // hero 重画（切 tab／切语言）后药丸会复位为全显，再滤一遍
    document.addEventListener('hub-lang-change', function () {
      setTimeout(filterTargetBuilds, 0);
    });
    var metaRow = $('featureMetaRow');
    if (metaRow && !metaRow.dataset.routeFilterObs) {
      metaRow.dataset.routeFilterObs = '1';
      try {
        new MutationObserver(function () { filterTargetBuilds(); })
          .observe(metaRow, { childList: true, subtree: true });
      } catch (_) {}
    }
    loadReachableRoutes();

    /* 结果卡上的「按这段坐标取序列」：把目标区间存进 sessionStorage，
       跳到 Genome / 位置查序列（#/sequence-region），由 app.js 回填并直接跑。 */
    document.addEventListener('click', function (e) {
      var el = e.target && e.target.closest ? e.target.closest('[data-lift-take-seq]') : null;
      if (!el) return;
      e.preventDefault();
      try {
        sessionStorage.setItem('zf_seq_region', JSON.stringify({
          chrom: el.getAttribute('data-chrom') || '',
          start: el.getAttribute('data-start') || '',
          end: el.getAttribute('data-end') || '',
          assembly: el.getAttribute('data-asm') ||
            (($('liftTargetBuild') && $('liftTargetBuild').value) || 'GRCz12tu')
        }));
      } catch (_) {}
      location.hash = '#/sequence-region';
    });

    bindModes();
  }

  /* ==========================================================================
     批量 ID 互转（2026-10-28）
     --------------------------------------------------------------------------
     数据来源就是本地库：gene_basic（符号 / Ensembl / ZFIN / 染色体）、
     gene_xref（Ensembl / ZFIN / NCBI Gene，另有 RefSeq GCF_049306965.1 一版）、
     gene_alias（NCBI / UniProt / Ensembl 别名）。
     后端 /api/tools/id-convert/batch 一次最多 200 条，逐条走
     gene_id_mapper.resolve_gene_identifier：唯一命中 / 多候选 / 未命中三级，
     未命中就留空——不猜、不模糊匹配。
     ========================================================================== */
  var BATCH_API = '/api/tools/id-convert/batch';
  var MAX_IDS = 200;

  var lastRows = [];     // 最近一次结果，供切语言重画 / 导出 / 复制
  var lastInputs = 0;
  var lastCapped = false;

  function postJson(url, body) {
    return fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (d) {
        return { ok: r.ok, status: r.status, data: d };
      });
    });
  }

  function splitIds(raw) {
    var out = [];
    var seen = {};
    String(raw || '').split(/[\s,;，；、]+/).forEach(function (s) {
      var v = (s || '').trim().replace(/^["']|["']$/g, '');
      if (!v) return;
      var k = v.toLowerCase();
      if (seen[k]) return;
      seen[k] = 1;
      out.push(v);
    });
    return out;
  }

  function xrefOf(r, names) {
    var list = r.xrefs || [];
    for (var i = 0; i < list.length; i++) {
      if (names.indexOf(String(list[i].db_name || '').toLowerCase()) >= 0) {
        return String(list[i].db_id || '');
      }
    }
    return '';
  }

  function flatten(r) {
    var c = r.canonical || {};
    var std = r.standard || {};
    var status = r.status || (r.resolved ? 'unique' : 'not_found');
    var cands = r.candidates || [];
    return {
      query: r.query || '',
      status: status,
      candCount: cands.length,
      candNames: cands.slice(0, 3).map(function (x) { return x.gene_name || x.ensembl_id || ''; }).join(' / '),
      std: std.gene_symbol || '',
      local: c.gene_name || '',
      ens: c.ensembl_id || xrefOf(r, ['ensembl']),
      zfin: c.zfin_id || xrefOf(r, ['zfin']),
      ncbi: xrefOf(r, ['ncbi_gene', 'ncbi_gene_grcz12tu']),
      chrom: c.chromosome || ''
    };
  }

  function cell(v) {
    return '<td>' + (v ? esc(v) : '<span class="id-map-empty">—</span>') + '</td>';
  }

  /* 瞬时提示（缺输入 / 正在跑 / 报错 / 复制成功）写在左栏按钮下面；
     结果表本身写进右栏 #toolResult —— 这一页是「左输入 / 右结果」两栏，
     8 列表格塞进 420px 的左栏只会横向溢出。 */
  function hint(msg, warn) {
    var el = $('idMapHint');
    if (!el) return;
    el.className = 'tool-hint' + (warn ? ' warn' : '');
    el.textContent = msg || '';
  }

  function renderIdMap() {
    var u = 0, a = 0, m = 0;
    lastRows.forEach(function (r) {
      if (r.status === 'unique') u++;
      else if (r.status === 'ambiguous') a++;
      else m++;
    });
    // 五列：输入 / 匹配 / 符号 / 跨库 ID（三行叠放）/ 染色体。
    // 早先拆成 8 列（Ensembl、ZFIN、NCBI 各占一列），在 626px 的结果栏里必然横向溢出，
    // 三个 ID 挤成一列反而更好扫；导出 CSV 仍是分开的字段，不影响表格软件。
    var head = ['idColQuery', 'idColStatus', 'idColStd', 'idColIds', 'idColChrom']
      .map(function (k) { return '<th>' + esc(T(k)) + '</th>'; }).join('');
    var body = lastRows.map(function (r) {
      var st;
      if (r.status === 'ambiguous') {
        st = '<span class="id-map-badge is-amb">' + esc(fmt(T('stAmb'), { n: r.candCount })) + '</span>' +
          (r.candNames ? '<span class="id-map-cands">' + esc(r.candNames) + '</span>' : '');
      } else if (r.status === 'unique') {
        st = '<span class="id-map-badge is-ok">' + esc(T('stUnique')) + '</span>';
      } else {
        st = '<span class="id-map-badge is-none">' + esc(T('stNone')) + '</span>';
      }
      var stdCell = r.std ? esc(r.std) : '<span class="id-map-empty">—</span>';
      if (r.local && r.local !== r.std) {
        stdCell += '<span class="id-map-local">' + esc(r.local) + '</span>';
      }
      var ids = [
        ['Ensembl', r.ens],
        ['ZFIN', r.zfin],
        ['NCBI', r.ncbi]
      ].map(function (pair) {
        return '<span class="id-map-idli"><em>' + pair[0] + '</em>' +
          (pair[1] ? esc(pair[1]) : '<span class="id-map-empty">—</span>') + '</span>';
      }).join('');
      return '<tr>' +
        '<td class="prov-mono">' + esc(r.query) + '</td>' +
        '<td>' + st + '</td>' +
        '<td class="prov-mono">' + stdCell + '</td>' +
        '<td class="id-map-ids prov-mono">' + ids + '</td>' +
        cell(r.chrom) +
        '</tr>';
    }).join('');
    var summary = '<p class="tool-hint' + (lastCapped ? ' warn' : '') + '" id="idMapSummary">' +
      esc(fmt(T('idSummary'), { n: lastInputs, u: u, a: a, m: m }) +
        (lastCapped ? ' · ' + T('idTooMany') : '')) + '</p>';
    showResult('<div class="id-map-result">' + summary +
      '<div class="table-wrap" id="idMapWrap"><table class="gene-table id-map-table" id="idMapTable">' +
      '<thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table></div></div>', 'idmap');
    var has = lastRows.length > 0;
    ['idMapCsvBtn', 'idMapCopyBtn'].forEach(function (id) {
      var b = $(id);
      if (b) b.disabled = !has;
    });
  }

  function runIdMap() {
    var raw = ($('idMapInput') && $('idMapInput').value) || '';
    var ids = splitIds(raw);
    var cnt = $('idMapCount');
    if (cnt) cnt.textContent = ids.length ? ids.length + ' / ' + MAX_IDS : '';
    if (!ids.length) {
      hint(T('idNeed'), true);
      return;
    }
    lastCapped = ids.length > MAX_IDS;
    if (lastCapped) ids = ids.slice(0, MAX_IDS);
    lastInputs = ids.length;
    hint(T('idRunning'));
    var btn = $('idMapRunBtn');
    if (btn) btn.disabled = true;
    postJson(BATCH_API, { identifiers: ids }).then(function (res) {
      var d = res.data || {};
      if (!res.ok || !d.results) {
        var msg = (d && (d.detail || d.message)) || ('HTTP ' + res.status);
        hint(fmt(T('idFail'), { msg: String(msg).slice(0, 200) }), true);
        return;
      }
      lastRows = d.results.map(flatten);
      hint('');
      renderIdMap();
    }).catch(function (e) {
      hint(fmt(T('idFail'), { msg: String(e.message || e).slice(0, 200) }), true);
    }).then(function () {
      if (btn) btn.disabled = false;
    });
  }

  function idMapMatrix() {
    var head = ['idColQuery', 'idColStatus', 'idColStd', 'idColLocal', 'idColEns', 'idColZfin', 'idColNcbi', 'idColChrom']
      .map(function (k) { return T(k); });
    var rows = lastRows.map(function (r) {
      var st = r.status === 'unique' ? T('stUnique')
        : (r.status === 'ambiguous' ? fmt(T('stAmb'), { n: r.candCount }) + ' ' + r.candNames : T('stNone'));
      return [r.query, st.trim(), r.std, r.local, r.ens, r.zfin, r.ncbi, r.chrom];
    });
    return [head].concat(rows);
  }

  function csvCell(v) {
    var s = String(v == null ? '' : v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function downloadCsv() {
    if (!lastRows.length) { hint(T('csvEmpty'), true); return; }
    var lines = idMapMatrix().map(function (r) { return r.map(csvCell).join(','); });
    var blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
    var a = document.createElement('a');
    var d = new Date();
    var p = function (n) { return String(n).padStart(2, '0'); };
    a.href = URL.createObjectURL(blob);
    a.download = 'zebrafish_id_map_' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '.csv';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 400);
  }

  function copyTable() {
    if (!lastRows.length) return;
    var text = idMapMatrix().map(function (r) { return r.join('\t'); }).join('\n');
    var done = function () {
      hint(T('copyOk'));
      setTimeout(function () {
        var el = $('idMapHint');
        if (el && el.textContent === T('copyOk')) hint('');
      }, 2400);
    };
    var legacy = function () {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.top = '-1000px';
      ta.setAttribute('readonly', 'readonly');
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      ta.remove();
      if (ok) done(); else hint(T('copyFail'), true);
    };
    // 非 https / 文档未聚焦 / 无权限时 navigator.clipboard 会 reject，
    // 这时退回 execCommand；两条路都失败才提示手动复制。
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, legacy);
      return;
    }
    legacy();
  }

  /* ── 模式切换：坐标换算 ⇄ 批量 ID 互转 ─────────────────────────────── */
  function setMode(name) {
    var mode = name === 'idmap' ? 'idmap' : 'coord';
    document.querySelectorAll('.lift-mode-tab').forEach(function (t) {
      var on = t.getAttribute('data-lift-mode') === mode;
      t.classList.toggle('active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    document.querySelectorAll('.lift-pane').forEach(function (p) {
      p.classList.toggle('hidden', p.getAttribute('data-lift-pane') !== mode);
    });
    // 右栏里如果还留着另一种模式的结果，清掉（否则会看着坐标结果去贴 ID）
    var tr = $('toolResult');
    if (tr && tr.getAttribute('data-lift-result') && tr.getAttribute('data-lift-result') !== mode) {
      tr.innerHTML = '';
      tr.classList.add('hidden');
      tr.removeAttribute('data-lift-result');
    }
    // hero 那排按钮组跟着换：坐标模式给「源版本 / 链」，ID 模式没有可选项 → 不画
    if (window.ZFPageChips && window.ZFPageChips.onPaneChange) {
      window.ZFPageChips.onPaneChange(mode);
    }
    if (mode === 'coord') {
      setTimeout(filterTargetBuilds, 0);
    }
    if (mode === 'idmap') {
      var ta = $('idMapInput');
      if (ta && !ta.value) ta.focus();
    }
  }

  window.ZFLift = { setMode: setMode, mode: function () {
    var a = document.querySelector('.lift-mode-tab.active');
    return a ? a.getAttribute('data-lift-mode') : 'coord';
  } };

  function bindModes() {
    document.querySelectorAll('.lift-mode-tab').forEach(function (t) {
      t.addEventListener('click', function () {
        setMode(t.getAttribute('data-lift-mode'));
      });
    });
    var runBtn = $('idMapRunBtn');
    if (runBtn) runBtn.addEventListener('click', runIdMap);
    var csvBtn = $('idMapCsvBtn');
    if (csvBtn) csvBtn.addEventListener('click', downloadCsv);
    var cpBtn = $('idMapCopyBtn');
    if (cpBtn) cpBtn.addEventListener('click', copyTable);
    var clBtn = $('idMapClearBtn');
    if (clBtn) clBtn.addEventListener('click', function () {
      var ta = $('idMapInput');
      if (ta) { ta.value = ''; ta.focus(); }
      lastRows = [];
      lastInputs = 0;
      lastCapped = false;
      var tr = $('toolResult');
      if (tr) { tr.innerHTML = ''; tr.classList.add('hidden'); tr.removeAttribute('data-lift-result'); }
      hint('');
      var c = $('idMapCount');
      if (c) c.textContent = '';
      ['idMapCsvBtn', 'idMapCopyBtn'].forEach(function (id) { var b = $(id); if (b) b.disabled = true; });
    });
    var ta = $('idMapInput');
    if (ta) {
      ta.addEventListener('input', function () {
        var c = $('idMapCount');
        var n = splitIds(ta.value).length;
        if (c) c.textContent = n ? n + ' / ' + MAX_IDS : '';
      });
      ta.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runIdMap(); }
      });
    }
    // 切语言后：表头、状态徽标、汇总句都是渲染时取的文案，必须重画一遍
    document.addEventListener('hub-lang-change', function () {
      if (lastRows.length) renderIdMap();
      hint('');
    });
    setMode((location.hash || '').indexOf('idmap') >= 0 ? 'idmap' : 'coord');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bind);
  } else {
    bind();
  }
})();
