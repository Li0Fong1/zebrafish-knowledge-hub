/* ==========================================================================
   crispr.js —— Tools 页：sgRNA 设计 / 引物设计（本地引擎 + 一键跳转对标）
   --------------------------------------------------------------------------
   后端：
     POST /api/tools/crispr/design   {target, enzyme, top_n, max_mismatch, off_target, add_t7}
     POST /api/tools/primer/design   {template, amplicon_min, amplicon_max, max_pairs, add_t7, off_target}
   外部对标（设计稿要求：把 CHOPCHOP 与 CRISPRScan 的长处用起来）：
     · 跳转时先把序列写入剪贴板，打开站点直接粘贴，避免对方站点参数不兼容
   说明：本地分数是透明规则加权，不是外部模型的复制；界面会明确标注这一点。
   ========================================================================== */
(function () {
  'use strict';

  var SGRNA_URL = '/api/tools/crispr/design';
  var PRIMER_URL = '/api/tools/primer/design';

  var TEXT = {
    zh: {
      running: '本地引擎计算中（含 BLAST 特异性筛查，约数秒～十几秒）…',
      fail: '计算失败：{msg}',
      needInput: '请先输入基因名或序列',
      sgrnaHead: '{enzyme} · PAM {pam} · 共 {n} 个候选，按综合分排序',
      colRank: '#', colSpacer: 'guide（5′→3′）', colPam: 'PAM', colStrand: '链',
      colPos: '位置', colGc: 'GC%', colEff: '效率分', colSpec: '特异性', colFinal: '综合分',
      colOff: '近同源', colT7: 'T7 模板',
      scopeGenome: '全基因组',
      scopeTx: '转录组',
      genomeReady: '脱靶范围：全基因组（已就绪，库 {n} MB）',
      genomeNotReady: '脱靶范围：全基因组（库未就绪）',
      loci: '位点',
      offPartial: '（全基因组模式只扫前 12 个候选，其余标未测）',
      t7Title: 'T7 体外转录模板（复制用）',
      offNone: '无 ≤{m} 错配近同源命中',
      offHits: '{n} 处 ≤{m} 错配命中（前 {k} 条见下）',
      offtargetSkipped: '脱靶筛查未执行：{why}',
      template: '模板：{label} · {n} bp',
      judge: '判断说明',
      primerTitle: '引物候选（按规则分排序）',
      colPair: '#', colFwd: '正向引物 (5′→3′)', colRev: '反向引物 (5′→3′)', colAmp: '产物',
      colTm: 'Tm（F/R）', colGcP: 'GC%（F/R）', colRisk: '风险', colScore: '规则分',
      riskHairpin: '发夹', riskDimer: '二聚体', riskNone: '—',
      tailed: '带 T7 尾：{seq}',
      jumpHead: '一键跳转权威工具对标',
      jumpCopy: '复制序列并打开',
      copied: '序列已复制：可直接在目标站点粘贴',
      copyFail: '复制失败，请手动复制模板序列',
      primerNote: '产物长度 {lo}–{hi} bp 为软约束；如需定量 PCR 请按仪器要求收窄。',
      noPair: '未找到满足条件的引物对',
      noCand: '未找到该 PAM 的候选'
    },
    en: {
      running: 'Running the local engine (includes a BLAST specificity scan; a few seconds)…',
      fail: 'Failed: {msg}',
      needInput: 'Enter a gene name or a sequence first',
      sgrnaHead: '{enzyme} · PAM {pam} · {n} candidates ranked by combined score',
      colRank: '#', colSpacer: 'guide (5′→3′)', colPam: 'PAM', colStrand: 'Strand',
      colPos: 'Pos', colGc: 'GC%', colEff: 'On-target', colSpec: 'Specificity', colFinal: 'Combined',
      colOff: 'Near-cognate', colT7: 'T7 template',
      scopeGenome: 'whole genome',
      scopeTx: 'transcriptome',
      genomeReady: 'Off-target scope: whole genome (ready, {n} MB db)',
      genomeNotReady: 'Off-target scope: whole genome (db not ready)',
      loci: 'loci',
      offPartial: '(genome mode scans the top 12 candidates only; the rest are marked n/a)',
      t7Title: 'T7 in-vitro transcription template (click to copy)',
      offNone: 'no near-cognate hits within {m} mismatches',
      offHits: '{n} hits within {m} mismatches (top {k} below)',
      offtargetSkipped: 'Off-target scan skipped: {why}',
      template: 'Template: {label} · {n} bp',
      judge: 'How to read',
      primerTitle: 'Primer candidates (ranked by rule score)',
      colPair: '#', colFwd: 'Forward (5′→3′)', colRev: 'Reverse (5′→3′)', colAmp: 'Product',
      colTm: 'Tm (F/R)', colGcP: 'GC% (F/R)', colRisk: 'Risk', colScore: 'Score',
      riskHairpin: 'hairpin', riskDimer: 'dimer', riskNone: '—',
      tailed: 'T7-tailed: {seq}',
      jumpHead: 'Open the authoritative tools to cross-check',
      jumpCopy: 'Copy sequence & open',
      copied: 'Sequence copied — paste it on the target site',
      copyFail: 'Copy failed, please copy the template manually',
      primerNote: 'Product-size range {lo}–{hi} bp is a soft constraint.',
      noPair: 'No primer pair satisfied the constraints',
      noCand: 'No candidate found for this PAM'
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
  function box() { return $('toolResult'); }
  function show(html) {
    var b = box();
    if (!b) return;
    b.innerHTML = html;
    b.classList.remove('hidden');
  }
  function scorePill(v) {
    if (v == null) return '<span class="pam-flag">—</span>';
    var cls = v >= 70 ? 'score-hi' : (v >= 50 ? 'score-mid' : 'score-lo');
    return '<span class="score-pill ' + cls + '">' + Number(v).toFixed(0) + '</span>';
  }

  /* ---------------- sgRNA ---------------- */

  function renderSgrna(d) {
    var cs = d.candidates || [];
    if (!cs.length) { show('<p class="tool-hint">' + esc(T('noCand')) + '</p>'); return; }
    var tpl = d.template || {};
    var head = '<p class="tool-hint">' + esc(fmt(T('template'), { label: tpl.label || tpl.source || '', n: tpl.length || 0 })) +
      '<br>' + esc(fmt(T('sgrnaHead'), { enzyme: d.enzyme, pam: d.pam_type, n: cs.length })) +
      ' · ' + esc(T('colOff')) + '：' + esc(d.offtarget_scope === 'genome' ? T('scopeGenome') : T('scopeTx')) + '</p>';

    var rows = cs.map(function (c, i) {
      var off = c.offtarget || null;
      var offTxt;
      if (d.offtarget_note && d.offtarget_note.indexOf('offtarget_skipped') === 0) {
        offTxt = '<span class="pam-flag">' + esc(fmt(T('offtargetSkipped'), { why: d.offtarget_note.replace('offtarget_skipped: ', '').slice(0, 60) })) + '</span>';
      } else if (!off) {
        offTxt = '<span class="pam-flag">' + esc(T('offPartial')) + '</span>';
      } else if (!off.near_mismatch_hits) {
        offTxt = '<span class="pam-flag">' + esc(fmt(T('offNone'), { m: 3 })) + '</span>';
      } else {
        var loci = (off.off_target_loci || []).slice(0, 3).map(function (l) {
          return esc(String(l.chrom || '')) + ':' + esc(l.start) + '-' + esc(l.end);
        }).join('、');
        var genes = (off.off_target_genes || []).slice(0, 3).map(function (g) {
          return esc(g.gene) + (g.hits > 1 ? '(' + g.hits + ')' : '');
        }).join('、');
        offTxt = '<span class="pam-flag">' + esc(fmt(T('offHits'), { n: off.near_mismatch_hits, m: 3, k: 2 })) +
          (genes ? '：' + genes : '') + (loci ? '<br><small class="pam-flag">' + esc(T('loci')) + ': ' + loci + '</small>' : '') + '</span>';
      }
      var t7 = c.t7_template
        ? '<button type="button" class="t7-copy" data-seq="' + esc(c.t7_template) + '" title="' + esc(T('t7Title')) + '">复制</button>'
        : '<span class="pam-flag">—</span>';
      return '<tr>' +
        '<td>' + (i + 1) + '</td>' +
        '<td class="mono">' + esc(c.spacer) + '</td>' +
        '<td class="mono">' + esc(c.pam) + '</td>' +
        '<td>' + esc(c.strand) + '</td>' +
        '<td>' + esc(c.start) + '-' + esc(c.end) + '</td>' +
        '<td>' + esc(c.gc_percent) + '</td>' +
        '<td>' + scorePill(c.efficiency_score) + '</td>' +
        '<td>' + scorePill(c.specificity_score == null ? null : c.specificity_score * 100) + '</td>' +
        '<td>' + scorePill(c.final_score) + '</td>' +
        '<td>' + offTxt + '</td>' +
        '<td>' + t7 + '</td>' +
        '</tr>';
    }).join('');

    show(head +
      '<div class="table-wrap"><table class="sgrna-table"><thead><tr>' +
      ['colRank', 'colSpacer', 'colPam', 'colStrand', 'colPos', 'colGc', 'colEff', 'colSpec', 'colFinal', 'colOff', 'colT7']
        .map(function (k) { return '<th>' + esc(T(k)) + '</th>'; }).join('') +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>');
  }

  function runSgrna() {
    var inp = $('crisprTargetInput');
    var target = inp ? inp.value.trim() : '';
    if (!target) { show('<p class="tool-hint">' + esc(T('needInput')) + '</p>'); return; }
    show('<span class="tool-loading">' + esc(T('running')) + '</span>');
    var payload = {
      target: target,
      enzyme: ($('crisprEnzyme') || {}).value || 'NGG',
      top_n: Number(($('crisprTopN') || {}).value || 20),
      max_mismatch: 3,
      off_target: true,
      add_t7: true,
      offtarget_scope: ($('crisprScope') || {}).value || 'transcriptome'
    };
    fetch(SGRNA_URL, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function (d) {
        if (!d) throw new Error('HTTP');
        if (d.success === false) throw new Error(d.error || 'error');
        renderSgrna(d);
      })
      .catch(function (e) {
        show('<p class="tool-hint">' + esc(fmt(T('fail'), { msg: String(e).slice(0, 120) })) + '</p>');
      });
  }

  /* ---------------- 引物 ---------------- */

  function renderPrimer(d) {
    var ps = d.pairs || [];
    if (!ps.length) { show('<p class="tool-hint">' + esc(T('noPair')) + '</p>'); return; }
    var tpl = d.template || {};
    var rows = ps.map(function (p, i) {
      var risk = [];
      if (p.hairpin_risk) risk.push(T('riskHairpin'));
      if (p.dimer_risk) risk.push(T('riskDimer'));
      var riskTxt = risk.length ? risk.join(' / ') : T('riskNone');
      var spec = p.specific_note ? '<br><small class="pam-flag">' + esc(p.specific_note) + '</small>' : '';
      var ogenes = (p.off_target_genes || []).slice(0, 4).map(function (o) {
        return '<span class="off-gene" title="' + esc(o.gene) + '">' + esc(o.gene) + '</span>';
      }).join(' ');
      var oloci = (p.off_target_loci || []).slice(0, 3).map(function (l) {
        return '<span class="off-gene">' + esc(String(l.chrom || '')) + ':' + esc(l.start) + '-' + esc(l.end) + '</span>';
      }).join(' ');
      var tail = p.forward_tailed
        ? '<br><small class="pam-flag">' + esc(fmt(T('tailed'), { seq: p.forward_tailed.slice(0, 26) + '…' })) + '</small>'
        : '';
      return '<tr>' +
        '<td>' + (i + 1) + '</td>' +
        '<td class="mono">' + esc(p.forward) + '<br><small class="pam-flag">' + esc(p.forward_pos) + '</small>' + tail + '</td>' +
        '<td class="mono">' + esc(p.reverse) + '<br><small class="pam-flag">' + esc(p.reverse_pos) + '</small></td>' +
        '<td>' + esc(p.product_size) + ' bp</td>' +
        '<td>' + esc(p.forward_tm) + ' / ' + esc(p.reverse_tm) + '<br><small class="pam-flag">Δ' + esc(p.tm_diff) + ' ℃</small></td>' +
        '<td>' + esc(p.forward_gc) + ' / ' + esc(p.reverse_gc) + '</td>' +
        '<td>' + esc(riskTxt) + spec + (ogenes ? '<div class="off-genes">' + ogenes + '</div>' : '') +
        (oloci ? '<div class="off-genes">' + oloci + '</div>' : '') + '</td>' +
        '<td>' + scorePill(p.score) + '</td>' +
        '</tr>';
    }).join('');

    show('<p class="tool-hint">' + esc(fmt(T('template'), { label: tpl.label || tpl.source || '', n: tpl.length || 0 })) +
      '<br>' + esc(T('primerTitle')) + (d.note ? ' · ' + esc(d.note) : '') + '</p>' +
      '<div class="table-wrap"><table class="sgrna-table"><thead><tr>' +
      ['colPair', 'colFwd', 'colRev', 'colAmp', 'colTm', 'colGcP', 'colRisk', 'colScore']
        .map(function (k) { return '<th>' + esc(T(k)) + '</th>'; }).join('') +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>');
  }

  function runPrimer() {
    var inp = $('primerInput');
    var template = inp ? inp.value.trim() : '';
    if (!template) { show('<p class="tool-hint">' + esc(T('needInput')) + '</p>'); return; }
    show('<span class="tool-loading">' + esc(T('running')) + '</span>');
    var payload = {
      template: template,
      amplicon_min: Number(($('primerAmpliconMin') || {}).value || 80),
      amplicon_max: Number(($('primerAmpliconMax') || {}).value || 300),
      max_pairs: 8,
      add_t7: true,
      off_target: true,
      offtarget_scope: ($('primerScope') || {}).value || 'transcriptome'
    };
    fetch(PRIMER_URL, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    })
      .then(function (r) { return r.json().catch(function () { return null; }); })
      .then(function (d) {
        if (!d) throw new Error('HTTP');
        if (d.success === false) throw new Error(d.error || 'error');
        renderPrimer(d);
      })
      .catch(function (e) {
        show('<p class="tool-hint">' + esc(fmt(T('fail'), { msg: String(e).slice(0, 120) })) + '</p>');
      });
  }

  /* ---------------- 跳转对标 ---------------- */

  var JUMPS = [
    { name: 'CHOPCHOP', url: 'https://chopchop.cbu.uib.no/', pane: 'sgrna' },
    { name: 'CRISPRscan', url: 'https://www.crisprscan.org/?page=sequence', pane: 'sgrna' },
    { name: 'Primer3web', url: 'https://primer3.ut.ee/', pane: 'primer' }
  ];

  /* 2026-10-31：跳转区只给本主题的外部工具。
     原来不管在哪个 tab 都把 CHOPCHOP / CRISPRscan / Primer3web 全列出来 ——
     sgRNA 页上挂个 Primer3web、引物页上挂 CRISPRscan，就是主题之间串功能。 */
  function currentPane() {
    var p = document.querySelector('.crispr-pane:not(.hidden)');
    return (p && p.getAttribute('data-crispr-pane')) || 'sgrna';
  }

  function renderJump() {
    var host = $('crisprJump');
    if (!host) return;
    var pane = currentPane();
    var list = JUMPS.filter(function (j) { return j.pane === pane; });
    // 2026-10-31：删掉「一键跳转权威工具对标：」这句引导语（sgRNA / 引物两页都删），
    // 只留可点的外链，链接自身就说明了用途。
    host.innerHTML = list.map(function (j) {
        return '<a href="' + esc(j.url) + '" target="_blank" rel="noopener" data-jump="' + esc(j.url) + '">' +
          esc(j.name) + ' ↗</a>';
      }).join('');
  }

  function copySeqToThen(seq, url) {
    var done = function (ok) {
      var host = $('crisprJump');
      if (host) {
        var note = host.querySelector('.jump-note');
        if (!note) { note = document.createElement('span'); note.className = 'jump-note tool-hint'; host.appendChild(note); }
        note.textContent = ' ' + (ok ? T('copied') : T('copyFail'));
      }
      if (url) window.open(url, '_blank', 'noopener');
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(seq).then(function () { done(true); }, function () { done(false); });
    } else {
      done(false);
    }
  }

  function bind() {
    document.querySelectorAll('.crispr-tab').forEach(function (t) {
      t.addEventListener('click', function () {
        var key = t.getAttribute('data-crispr-tab');
        document.querySelectorAll('.crispr-tab').forEach(function (x) {
          x.classList.toggle('active', x === t);
        });
        document.querySelectorAll('.crispr-pane').forEach(function (p) {
          p.classList.toggle('hidden', p.getAttribute('data-crispr-pane') !== key);
        });
        // 跳转区按当前 tab 提供合适的目标
        renderJump();
        // hero 的按钮组要指向当前可见的那一组控件（sgRNA: PAM/脱靶范围；引物: 特异性）
        if (window.ZFPageChips) window.ZFPageChips.onPaneChange(key);
      });
    });

    // 路由直接切 tab（app.js 的 setCrisprTab）：这里只负责刷新跳转区
    document.addEventListener('zf-crispr-pane', function () { renderJump(); });

    var s = $('crisprRunBtn');
    if (s) s.addEventListener('click', runSgrna);
    var p = $('primerRunBtn');
    if (p) p.addEventListener('click', runPrimer);

    // T7 模板一键复制
    var box = $('toolResult');
    if (box) {
      box.addEventListener('click', function (e) {
        var b = e.target.closest ? e.target.closest('button.t7-copy') : null;
        if (!b) return;
        copySeqToThen(b.getAttribute('data-seq') || '', '');
      });
    }

    // 跳转：优先把当前输入序列带上（复制到剪贴板，站点里直接粘贴）
    var jump = $('crisprJump');
    if (jump) {
      jump.addEventListener('click', function (e) {
        var a = e.target.closest ? e.target.closest('a[data-jump]') : null;
        if (!a) return;
        var seq = '';
        var t = $('crisprTargetInput');
        if (t && t.value.trim()) seq = t.value.trim();
        if (!seq) { var p2 = $('primerInput'); if (p2) seq = p2.value.trim(); }
        if (!seq) return;                         // 没输入就不拦，直接跳
        e.preventDefault();
        copySeqToThen(seq, a.getAttribute('data-jump'));
      });
    }
  }

  /* 能力探测：全基因组库未就绪时把选项置灰，不假装能用 */
  function applyCapabilities(cap) {
    var g = (cap && cap.genome) || {};
    var ready = !!g.ready;
    ['crisprScope', 'primerScope'].forEach(function (id) {
      var sel = $(id);
      if (!sel) return;
      Array.prototype.forEach.call(sel.options, function (o) {
        if (o.value !== 'genome') return;
        o.disabled = !ready;
        o.textContent = ready
          ? fmt(T('genomeReady'), { n: g.size_mb || '?' })
          : T('genomeNotReady');
      });
      if (!ready && sel.value === 'genome') sel.value = 'transcriptome';
    });
  }

  function fetchCapabilities() {
    fetch('/api/tools/crispr/capabilities', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) applyCapabilities(d); })
      .catch(function () { /* 探测失败就保持原样，后端仍会返回跳过原因 */ });
  }

  function boot() {
    if (!$('crisprRunBtn')) return;
    bind();
    renderJump();
    fetchCapabilities();
    document.addEventListener('hub-lang-change', function () {
      renderJump();
      fetchCapabilities();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
