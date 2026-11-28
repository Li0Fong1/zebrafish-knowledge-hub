/* ==========================================================================
   genomes.js —— Genomes 功能页（装配对比 / 染色体对比）
   --------------------------------------------------------------------------
   2026-10-24 改版（用户反馈）：
     旧版「选一条染色体 → 列出该染色体上的基因符号」，而 SYMBOL 与 NAME 完全一样，
     等于把基因名清单抄一遍，没有信息量。现在只回答**基因组之间**的问题：

     GET /api/genome/overview  → 装配登记 + 25 条染色体（GRCz12tu 实测长度 / GC）+ 本地 BLAST 库
     GET /api/genome/compare   → 逐染色体 GRCz12tu（本地实测） vs GRCz11（Ensembl REST，缓存 7 天）

   口径（页面里也写清楚，避免误读）：
     · GRCz12tu 长度 / GC = 本机基因组 FASTA 实测（与 BLAST 建库同一份）
     · GRCz11 长度 = Ensembl REST（在线取一次，缓存 7 天）；取不到就只显示 GRCz12tu 一侧
     · 基因数 = 本地注释库（GRCz11 r116）按 chromosome 分组计数
     · 坐标换算不在本页：Liftover 是独立页（#/liftover，走 UCSC over.chain），
       这里只比长度，页面上明确指路，避免两处各说一套
   ========================================================================== */
(function () {
  'use strict';

  var OVERVIEW_URL = '/api/genome/overview';
  var COMPARE_URL = '/api/genome/compare';

  var TEXT = {
    zh: {
      loading: '读取基因组信息…',
      fail: '读取失败：{msg}',
      kAssembly: '装配版本', kSequences: '序列条数', kLength: '总长度', kGc: '平均 GC',
      kGenes: '已注释基因',
      asmTitle: '装配登记',
      asmSub: '',
      colAsm: '装配', colAsmAcc: 'RefSeq', colAsmStrain: '品系', colAsmRel: 'Ensembl', colAsmRole: '在本系统中的用途',
      roleAnchor: '本地注释锚点（坐标 / 表达 / GO）', roleRef: '标准名 / BLAST / cDNA',
      cmpTitle: '逐染色体对比',
      cmpSub: 'Δ > 0 表示 GRCz12tu 更长',
      colChr: '染色体', col12: 'GRCz12tu', col11: 'GRCz11', colDelta: 'Δ Mb', colDeltaPct: 'Δ %',
      colGc: 'GC', colGenes: '基因数', colDensity: '密度',
      cmpFail: 'GRCz11 一侧读取失败（{msg}）：下表只显示 GRCz12tu 实测值',
      libTitle: '本地比对库',
      colLib: '库', colLibSize: '大小', colLibNote: '用途',
      noLiftover: '这一区只比长度',
      noLiftoverBody: 'GRCz12tu 为本地实测、GRCz11 为 Ensembl 记录。要把旧组装的一段坐标换到 GRCz12tu，去 Liftover 页：UCSC over.chain、碱基级。',
      noLiftoverMore: '去 Liftover →',
      openSeq: '去按坐标取序列 →',
      polTitle: '这套系统里，每一步该用哪一版',
      polSub: '两版装配并存不是随便的：各功能按自己的数据源走，页面上都已标注。',
      colStep: '功能 / 环节', colWhich: '用哪版', colWhy: '为什么',
      polRows: [
        ['基因符号 / 标准名', 'GRCz12tu', '标准名以 NCBI RefSeq 现行参考（GCF_049306965）为准'],
        ['BLAST 核酸比对', 'GRCz12tu', '比对库就是这一版的 CDS / 基因组 FASTA'],
        ['cDNA 提取', 'GRCz12tu', '与 BLAST 同一份序列，避免“比对用的 A、提取给 B”'],
        ['基因坐标 / 启动子 / 基因体', 'GRCz11', '本地注释锚定在 Ensembl r116（坐标系不同）'],
        ['表达量 / GO / KEGG / 表型', 'GRCz11', '本地注释表全部以 GRCz11 基因为主键'],
        ['跨库 ID 转换', '两版对照', '本地 GRCz11 与 RefSeq GRCz12tu 标准名互查'],
      ]
    },
    en: {
      loading: 'Loading genome information…',
      fail: 'Load failed: {msg}',
      kAssembly: 'Assembly', kSequences: 'Sequences', kLength: 'Total length', kGc: 'Mean GC',
      kGenes: 'Annotated genes',
      asmTitle: 'Assembly registry',
      asmSub: '',
      colAsm: 'Assembly', colAsmAcc: 'RefSeq', colAsmStrain: 'Strain', colAsmRel: 'Ensembl', colAsmRole: 'Role in this hub',
      roleAnchor: 'local annotation anchor (coordinates / expression / GO)', roleRef: 'canonical symbols / BLAST / cDNA',
      cmpTitle: 'Chromosome-by-chromosome comparison',
      cmpSub: 'Δ > 0 means GRCz12tu is longer',
      colChr: 'Chr', col12: 'GRCz12tu', col11: 'GRCz11', colDelta: 'Δ Mb', colDeltaPct: 'Δ %',
      colGc: 'GC', colGenes: 'Genes', colDensity: 'Density',
      cmpFail: 'GRCz11 data could not be retrieved ({msg}); only GRCz12tu values are shown below',
      libTitle: 'Local BLAST databases',
      colLib: 'Library', colLibSize: 'Size', colLibNote: 'Purpose',
      noLiftover: 'Length comparison only',
      noLiftoverBody: 'GRCz12tu is measured locally; GRCz11 comes from Ensembl records. To lift an older-assembly region over to GRCz12tu, open Liftover: UCSC over.chain, base-pair resolution.',
      noLiftoverMore: 'Open Liftover →',
      openSeq: 'Retrieve sequence by coordinates →',
      polTitle: 'Which assembly each part of this hub uses',
      polSub: 'The two assemblies coexist for a reason; each tool follows its own data source and says so on the page.',
      colStep: 'Step', colWhich: 'Assembly', colWhy: 'Why',
      polRows: [
        ['Gene symbols / canonical names', 'GRCz12tu', 'Canonical names follow the NCBI RefSeq reference (GCF_049306965)'],
        ['BLAST (nucleotide)', 'GRCz12tu', 'The search database is the CDS / genome FASTA of this assembly'],
        ['cDNA extraction', 'GRCz12tu', 'Same sequences BLAST uses, so results stay consistent'],
        ['Gene coordinates / promoter / gene body', 'GRCz11', 'Local annotation is anchored to Ensembl r116 (different coordinate system)'],
        ['Expression / GO / KEGG / phenotype', 'GRCz11', 'All local annotation tables are keyed by GRCz11 genes'],
        ['Cross-database ID convert', 'Both assemblies', 'Local GRCz11 ↔ RefSeq GRCz12tu canonical names'],
      ]
    }
  };

  function curLang() {
    var l = (document.documentElement.getAttribute('lang') || 'en').toLowerCase();
    return l.indexOf('zh') === 0 ? 'zh' : 'en';
  }
  function T(k) { var d = TEXT[curLang()] || TEXT.en; return d[k] != null ? d[k] : (TEXT.en[k] || ''); }
  function fmt(s, o) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return o[k] != null ? o[k] : m; }); }
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function fmtInt(v) { return (v == null || v === '') ? '—' : Number(v).toLocaleString('en-US'); }
  function num(v) { return (v == null || v === '') ? '—' : v; }
  function activeRoute() {
    return ((location.hash || '').replace(/^#\/?/, '').split(/[/?#]/)[0]) || 'home';
  }

  var lastOverview = null, lastCompare = null;

  function showResult(html) {
    var box = $('toolResult');
    if (!box) return;
    box.innerHTML = html;
    box.classList.remove('hidden');
  }

  function block(title, sub, body) {
    return '<div class="zf-block"><h4 class="zf-block-title">' + esc(title) + '</h4>' +
      (sub ? '<p class="zf-block-sub">' + esc(sub) + '</p>' : '') + body + '</div>';
  }

  function table(headers, rows) {
    return '<div class="table-wrap"><table class="gene-table"><thead><tr>' +
      headers.map(function (h) { return '<th>' + esc(h) + '</th>'; }).join('') +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  }

  /* ---------------- 概览数字 ---------------- */

  function summaryHtml(d) {
    var items = [
      [T('kAssembly'), d.assembly || 'GRCz12tu'],
      [T('kSequences'), d.n_sequences || '—'],
      [T('kLength'), (d.total_length_gb || '—') + ' Gb'],
      [T('kGc'), (d.mean_gc == null ? '—' : d.mean_gc + '%')],
      [T('kGenes'), fmtInt(d.gene_total)]
    ];
    return '<div class="zf-kv">' + items.map(function (pair) {
      return '<div class="zf-kv-item"><span class="zf-kv-k">' + esc(pair[0]) + '</span>' +
        '<span class="zf-kv-v"><strong>' + esc(pair[1]) + '</strong></span></div>';
    }).join('') + '</div>';
  }

  /* ---------------- 装配登记 ---------------- */

  function asmHtml(d) {
    var rows = (d.assemblies || []).map(function (a) {
      var role = a.is_local_anchor ? T('roleAnchor') : (a.is_ncbi_reference ? T('roleRef') : '—');
      return '<tr>' +
        '<td><strong>' + esc(a.assembly_name || '—') + '</strong></td>' +
        '<td class="zf-muted">' + esc(a.ncbi_refseq_accession || '—') + '</td>' +
        '<td class="zf-muted">' + esc(a.strain || '—') + '</td>' +
        '<td class="zf-muted">' + esc(a.ensembl_release || a.ensembl_assembly || '—') + '</td>' +
        '<td>' + esc(role) + '</td></tr>';
    }).join('');
    if (!rows) return '';
    return block(T('asmTitle'), T('asmSub'),
      table([T('colAsm'), T('colAsmAcc'), T('colAsmStrain'), T('colAsmRel'), T('colAsmRole')], rows));
  }

  /* ---------------- 逐染色体对比 ---------------- */

  function compareHtml(cmp) {
    if (!cmp || !cmp.rows || !cmp.rows.length) {
      var why = (cmp && cmp.grc11 && cmp.grc11.error) || '—';
      return block(T('cmpTitle'), '', '<p class="tool-hint warn">' + esc(fmt(T('cmpFail'), { msg: why })) + '</p>');
    }
    var rows = cmp.rows.map(function (r) {
      var delta = r.delta_mb;
      var deltaTxt = (delta == null) ? '—' : (delta > 0 ? '+' : '') + delta;
      var pctTxt = (r.delta_pct == null) ? '—' : (r.delta_pct > 0 ? '+' : '') + r.delta_pct + '%';
      var cls = (delta == null) ? '' : (delta >= 0 ? ' is-up' : ' is-down');
      return '<tr>' +
        '<td><strong>' + esc(r.chromosome) + '</strong></td>' +
        '<td class="zf-num">' + num(r.length_mb) + ' Mb</td>' +
        '<td class="zf-num">' + (r.length_grc11_mb == null ? '—' : r.length_grc11_mb + ' Mb') + '</td>' +
        '<td class="zf-num' + cls + '">' + deltaTxt + '</td>' +
        '<td class="zf-num' + cls + '">' + pctTxt + '</td>' +
        '<td class="zf-num">' + (r.gc == null ? '—' : r.gc + '%') + '</td>' +
        '<td class="zf-num">' + fmtInt(r.gene_count) + '</td>' +
        '<td class="zf-num">' + num(r.gene_density) + '</td></tr>';
    }).join('');
    var sub = T('cmpSub');
    if (!(cmp.grc11 && cmp.grc11.ready)) {
      sub += ' · ' + fmt(T('cmpFail'), { msg: (cmp.grc11 && cmp.grc11.error) || '—' });
    }
    return block(T('cmpTitle'), sub, table(
      [T('colChr'), T('col12'), T('col11'), T('colDelta'), T('colDeltaPct'), T('colGc'), T('colGenes'), T('colDensity')],
      rows
    ));
  }

  /* ---------------- 本地库 ---------------- */

  function libHtml(d) {
    var zh = curLang() === 'zh';
    var rows = (d.libraries || []).map(function (l) {
      var label = zh ? l.label : (l.label_en || l.label);
      var note = zh ? l.note : (l.note_en || l.note);
      return '<tr><td><strong>' + esc(label || l.key) + '</strong><div class="zf-muted zf-mono">' + esc(l.file || '') + '</div></td>' +
        '<td class="zf-num">' + (l.ready ? l.size_mb + ' MB' : '—') + '</td>' +
        '<td class="zf-muted">' + esc(note || '') + '</td></tr>';
    }).join('');
    if (!rows) return '';
    return block(T('libTitle'), '', table([T('colLib'), T('colLibSize'), T('colLibNote')], rows));
  }

  function noLiftoverHtml() {
    return '<div class="zf-block zf-callout"><h4 class="zf-block-title">' + esc(T('noLiftover')) + '</h4>' +
      '<p class="zf-block-sub">' + esc(T('noLiftoverBody')) + ' ' +
      '<a href="#/liftover" class="zf-link">' + esc(T('noLiftoverMore')) + '</a></p>' +
      '<p class="zf-block-sub"><a href="#/sequence-region" class="zf-link">' + esc(T('openSeq')) + '</a></p></div>';
  }

  /* 「哪一步用哪一版」对照表：不需要接口，本身就是装配分用的说明 */
  function policyHtml() {
    var rows = T('polRows').map(function (r) {
      var which = String(r[1] || '');
      var cls = which.indexOf('GRCz12tu') >= 0 ? ' is-12' : (which.indexOf('GRCz11') >= 0 ? ' is-11' : '');
      return '<tr><td>' + esc(r[0]) + '</td>' +
        '<td><span class="zf-asm-tag' + cls + '">' + esc(which) + '</span></td>' +
        '<td class="zf-muted">' + esc(r[2]) + '</td></tr>';
    }).join('');
    /* 2026-10-31：这块「哪一步用哪版装配」的解释性表格整块下线（用户要求结果区不要口径注释）。 */
    return '';
  }

  /* ---------------- CSV 导出 ---------------- */

  function exportCsv() {
    var cmp = lastCompare;
    if (!cmp || !cmp.rows || !cmp.rows.length) {
      showResult('<p class="tool-hint warn">' + esc(curLang() === 'zh'
        ? '还没有可导出的对比数据，请先点「加载对比表」。'
        : 'No data to export. Select Load comparison first.') + '</p>');
      return;
    }
    var head = ['chromosome', 'grcz12tu_mb', 'grcz11_mb', 'delta_mb', 'delta_pct', 'gc_percent', 'gene_count', 'gene_per_mb'];
    var lines = [head.join(',')];
    cmp.rows.forEach(function (r) {
      lines.push([r.chromosome, r.length_mb, r.length_grc11_mb, r.delta_mb, r.delta_pct,
        r.gc, r.gene_count, r.gene_density].map(function (v) {
        return v == null ? '' : String(v);
      }).join(','));
    });
    var blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'grcz11_vs_grcz12tu_chromosomes.csv';
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  }

  function renderAll() {
    var d = lastOverview;
    if (!d || !d.ready) {
      var why = (d && (d.error || d.note)) || '—';
      showResult('<p class="tool-hint">' + esc(fmt(T('fail'), { msg: why })) + '</p>');
      return;
    }
    showResult(summaryHtml(d) + asmHtml(d) + compareHtml(lastCompare) + policyHtml() + libHtml(d) + noLiftoverHtml());
  }

  /* ---------------- 请求 ---------------- */

  function get(url) {
    return fetch(url, { credentials: 'same-origin' }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (d) {
        if (!r.ok) {
          var msg = (d && (d.detail || d.error)) || ('HTTP ' + r.status);
          throw new Error(String(msg).slice(0, 160));
        }
        return d;
      });
    });
  }

  function loadAll() {
    showResult('<span class="tool-loading">' + esc(T('loading')) + '</span>');
    return get(OVERVIEW_URL).then(function (d) {
      lastOverview = d;
      renderAll();
      return get(COMPARE_URL).then(function (cmp) {
        lastCompare = cmp;
        renderAll();
      }).catch(function (e) {
        lastCompare = { rows: [], grc11: { ready: false, error: String(e.message || e).slice(0, 120) } };
        renderAll();
      });
    }).catch(function (e) {
      showResult('<p class="tool-hint warn">' + esc(fmt(T('fail'), { msg: String(e.message || e).slice(0, 160) })) + '</p>');
    });
  }

  /* ---------------- 绑定 ---------------- */

  function bind() {
    var b = $('genomeLoadBtn');
    if (b) b.addEventListener('click', function () { loadAll(); });
    var c = $('genomeCsvBtn');
    if (c) c.addEventListener('click', exportCsv);

    document.addEventListener('hub-lang-change', function () {
      if (lastOverview) renderAll();
    });
  }

  function boot() {
    if (!$('genomeLoadBtn')) return;
    bind();
    document.addEventListener('zf-feature-open', function (e) {
      var route = (e && e.detail && e.detail.route) || '';
      // 2026-10-28：Liftover 页的默认动作是「换算」，不能一进来就把结果区塞满对比表。
      // 旧 #/genomes 已别名到 liftover，这里不再自动加载，交给折叠区里的按钮。
      if (route !== 'genomes') return;
      var box = $('toolResult');
      if (box && !box.textContent.trim()) loadAll();
    });
    if (activeRoute() === 'genomes') {
      var b = $('toolResult');
      if (b && !b.textContent.trim()) loadAll();
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
