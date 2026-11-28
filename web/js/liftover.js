/* ==========================================================================
   liftover.js —— Omics / Genome / Liftover 功能页
   --------------------------------------------------------------------------
   显式来源／目标成对换算（第 3 期：强制自选，不设默认）：

     GET /api/genome/liftover?build=GRCz11&chrom=5&start=...&end=...&to=GRCz12tu

   口径：
     · 来源与目标都必须由用户选择；缺一 → need_source_build／need_target_build；
     · 已收录版本均可选自选；缺链 → chain_unavailable；
     · GRCz13ab 2026-11-05 起可作来源与目标（复用 GRCz12ab 链 + GCA_052040795.2 报告）；
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
      needSource: '请先选择来源版本（默认 GRCz11）',
      needTarget: '请先选择目标版本（默认 GRCz12tu）',
      chromPlaceholder: '选择染色体',
      chromPickSource: '先选来源版本',
      chromNeedSource: '来源版本未选：这里先给 1–25 + MT 的通用清单，选好来源版本后会换成该装配的真实染色体。',
      okTitle: '换算结果',
      kSrc: '源区间',
      kTgt: '目标区间',
      kStrand: '链方向',
      kLen: '长度',
      kChain: '经过的链',
      kTgtGene: '区间基因',
      kTgtSeq: '参考序列',
      geneMismatch: '两端区间基因不同：{s} → {t}。请确认这串坐标确实取自 {from}——不同品系装配的同一段数字常落在不同基因上。',
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
      idColUni: 'UniProt',
      idColChrom: '染色体',
      idColTgtCoord: '目标坐标(chain)',
      stUnique: '唯一',
      stAmb: '多候选 {n}',
      stNone: '未匹配',
      stNotInRelease: '该 release 未收录',
      copyOk: '已复制表格（制表符分隔，可直接粘进 Excel）',
      copyFail: '复制失败，请手动选中表格',
      csvEmpty: '还没有结果可导出',
      /* 2026-11-12：18 个装配全开。非 GRCz11/GRCz12tu 的版本没有独立基因
         注释表，必须把「稳定标识符 + chain 坐标」的口径讲清楚，不能让用户
         误以为手里拿的是该版本专属 ID。 */
      idVersionNote: '所选装配没有独立基因注释表：符号 / Ensembl / ZFIN / NCBI / UniProt 是跨版本稳定标识；坐标由 UCSC chain 换算到该版本，未伪造「版本专属 ID」。',
      idEnsReleaseNote: 'Ensembl 版本口径：ENSDARG 是跨 release 稳定标识，这里给出目标 release 基因集中该基因所用的 ID；本地收录 4 个历史基因集 + r116 当前集，不含官方 ID History（退役/合并/拆分），查不到即标为未收录，不伪造 ID。',
      idCoordCaveat: '跨版本坐标是 UCSC chain 换算得到的对应基因组区间，不是该版本的官方基因注释坐标；与各数据库注释边界实测存在几十 bp ~ 几 kb 的差异，换算失败一律留空，不给近似坐标。',
      coordLiftoverFailed: '链换算失败',
      coordSplit: '跨链断裂点',
      coordNotPrecomputed: '未预计算',
      coordNoHub: '无源坐标',
      coordNotResolved: '未解析',
      idCoordStat: '目标版本坐标：{ok}/{n} 条经换算链得到',
      idColSrcCoord: '来源坐标',
      idColCoordStatus: '坐标状态'
    },
    en: {
      loading: 'Lifting over…',
      loadingSec: 'Lifting over… {s}s (the first run builds the chain index — a cold start can take 1–2 min)',
      loadingTip: 'Chain files are large, so the first lift-over builds an index; later queries take about a second.',
      fail: 'Lift-over failed: {msg}',
      needRegion: 'Chromosome, start and end are required',
      needSource: 'Select a source assembly first (GRCz11 by default)',
      needTarget: 'Select a target assembly first (GRCz12tu by default)',
      chromPlaceholder: 'Chromosome',
      chromPickSource: 'Select source',
      chromNeedSource: 'No source assembly has been selected. The generic set of chromosomes 1–25 plus MT is shown; selecting an assembly replaces it with the chromosomes present in that assembly.',
      okTitle: 'Result',
      kSrc: 'Source region',
      kTgt: 'Target region',
      kStrand: 'Strand',
      kLen: 'Length',
      kChain: 'Chain',
      kTgtGene: 'Overlapping genes',
      kTgtSeq: 'Reference sequence',
      geneMismatch: 'The two ends hit different genes: {s} → {t}. Verify that these coordinates originate from {from} — identical coordinates frequently correspond to different genes across strain assemblies.',
      takeSeq: 'Retrieve sequence for this region',
      backNote: 'Route: {from} → {to} (approximate coordinates are never returned when a chain is unavailable)',
      codeMissing: 'The chain file for this lift-over is not available on the server',
      codeUnavailable: 'No UCSC chain route from {from} to {to}; no MAF fallback is available',
      codeUnsupported: 'This assembly is not supported for liftover',
      codeSplit: 'This region crosses a chain breakpoint — narrow it and lift over in segments',
      codeFail: 'No alignment block covers this region in the chain file',
      codeReverse: 'Cannot lift over to {to}: no routable path',
      noApprox: 'No approximate coordinates were returned.',

      /* ── batch ID convert ───────────────────────────────────────── */
      idNeed: 'Paste IDs first: one per line, or comma-separated',
      idTooMany: 'Up to 200 per run — using the first 200',
      idRunning: 'Converting… (up to 200, one lookup each)',
      idFail: 'Conversion failed: {msg}',
      idSummary: '{n} inputs: {u} unique · {a} ambiguous · {m} not found',
      idColQuery: 'Input',
      idColStatus: 'Match',
      idColStd: 'Symbol',
      idColLocal: 'Local symbol',
      idColIds: 'Cross-database identifiers',
      idColEns: 'Ensembl',
      idColZfin: 'ZFIN',
      idColNcbi: 'NCBI Gene',
      idColUni: 'UniProt',
      idColChrom: 'Chr',
      idColTgtCoord: 'Target coords (chain)',
      stUnique: 'unique',
      stAmb: 'ambiguous ({n})',
      stNone: 'not found',
      stNotInRelease: 'not in release',
      copyOk: 'Table copied in tab-separated format',
      copyFail: 'Copy failed — select the table manually',
      csvEmpty: 'No results to export yet',
      idVersionNote: 'This assembly has no independent gene annotation table: symbol / Ensembl / ZFIN / NCBI / UniProt are version-stable identifiers; coordinates are lifted by UCSC chain. No assembly-specific IDs were invented.',
      idEnsReleaseNote: 'Ensembl release scope: ENSDARG IDs are stable across releases; shown here is the ID used for this gene in the target release gene set. Four historical sets plus current r116 are available locally; official ID History (retired/merged/split) is not included, and missing entries are reported rather than invented.',
      idCoordCaveat: 'Cross-build coordinates are genomic intervals lifted by UCSC chain, NOT official gene annotation coordinates for that assembly. Differences of tens of bp to a few kb versus database annotations were measured; failed conversions are left blank and never approximated.',
      coordLiftoverFailed: 'chain failed',
      coordSplit: 'chain breakpoint',
      coordNotPrecomputed: 'not precomputed',
      coordNoHub: 'no source coords',
      coordNotResolved: 'unresolved',
      idCoordStat: 'Target coordinates: {ok}/{n} resolved through the chain route',
      idColSrcCoord: 'Source coordinates',
      idColCoordStatus: 'Coordinate status'
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
  /* 最近一次「本地校验」提示的 key：切语言后按新语言重画，
     否则英文界面里会留着一条中文警告。 */
  var lastHintKey = '';
  /* 最近一份坐标换算结果：切语言后要按新语言重画（否则英文界面上留着中文标签） */
  var lastCoord = null;

  function showHint(key) {
    lastHintKey = key;
    showResult('<p class="tool-hint warn">' + esc(T(key)) + '</p>');
  }

  function showResult(html, mode) {
    var box = $('toolResult');
    if (!box) return;
    /* 结果只写进 Liftover 自己的桶：别的页面上即使有请求回来，
       也不会把坐标结果画到别人的结果区（见 tool-result.js）。 */
    if (window.ZFResult) window.ZFResult.write(html, 'liftover');
    else { box.innerHTML = html; box.classList.remove('hidden'); }
    if (window.ZFResult && window.ZFResult.route() !== 'liftover') return;
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
  function chainLabel(d) {
    // 2026-10-31：不直接把 .over.chain 文件名丢给用户。
    // UCSC 的目标端常写 danRer11（＝GRCz11 的旧名），和界面上选的「GRCz11」看着像两个版本，
    // 用户会以为链配错了。这里显示路由本身（A → B [→ C]），文件名放 title 里备查。
    var hops = Array.isArray(d.route_hops) ? d.route_hops.filter(Boolean) : [];
    if (hops.length) return hops.join(' → ');
    if (d.source_build && d.target_build) return d.source_build + ' → ' + d.target_build;
    return d.chain || '—';
  }

  function geneSymbols(list) {
    return (Array.isArray(list) ? list : [])
      .map(function (g) { return g && g.symbol ? String(g.symbol) : ''; })
      .filter(Boolean);
  }

  function okCard(d) {
    var srcLen = (d.start != null && d.end != null) ? (Number(d.end) - Number(d.start) + 1) : null;
    var tgtGenes = geneSymbols(d.target_genes);
    var rows = [
      [T('kSrc'), d.source_build + ' ' + (d.source_span || '')],
      [T('kTgt'), d.target_build + ' ' + (d.target_span || '')]
    ];
    if (d.target_accession) rows.push([T('kTgtSeq'), d.target_accession]);
    if (tgtGenes.length) rows.push([T('kTgtGene'), tgtGenes.join(' · ')]);
    rows.push([T('kStrand'), d.strand || '+']);
    rows.push([T('kLen'), srcLen ? srcLen.toLocaleString('en-US') + ' bp' : '—']);
    rows.push([T('kChain'), chainLabel(d)]);

    var body = rows.map(function (r) {
      var title = (r[0] === T('kChain') && d.chain) ? ' title="' + esc(d.chain) + '"' : '';
      return '<tr><th>' + esc(r[0]) + '</th><td class="prov-mono"' + title + '>' + esc(r[1]) + '</td></tr>';
    }).join('');

    // 两端都注释到了、却没有任何一个同名基因 → 这段坐标很可能不是这个装配的
    // （不同品系装配之间常有片段级增删：同一串数字在两边落在不同基因上）。
    var srcGenes = geneSymbols(d.source_genes);
    var disjoint = srcGenes.length && tgtGenes.length &&
      !srcGenes.some(function (s) { return tgtGenes.indexOf(s) >= 0; });
    var warn = disjoint
      ? '<p class="tool-hint warn">' + esc(fmt(T('geneMismatch'), {
        s: srcGenes.join(' · '), t: tgtGenes.join(' · '), from: d.source_build || ''
      })) + '</p>'
      : '';

    var seqBtn = d.chromosome
      ? '<button type="button" class="tool-secondary-btn" data-lift-take-seq="1"' +
        ' data-chrom="' + esc(d.chromosome) + '" data-start="' + esc(d.start) + '" data-end="' + esc(d.end) + '"' +
        ' data-asm="' + esc(d.target_build || 'GRCz12tu') + '">' +
        esc(T('takeSeq')) + '</button>'
      : '';
    return '<div class="tool-summary">' + esc(T('okTitle')) + ' · ' +
      esc(d.source_build) + ' → ' + esc(d.target_build) + '</div>' +
      '<div class="table-wrap"><table class="gene-table"><tbody>' + body + '</tbody></table></div>' +
      warn +
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
    setChromValue(r[1]);
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
      showHint('needSource');
      return;
    }
    if (!target) {
      showHint('needTarget');
      return;
    }
    if (!chrom || !start || !end) {
      showHint('needRegion');
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
    lastHintKey = '';
    showResult('<span class="tool-loading">' + esc(T('loading')) + '</span>');
    var tick = setInterval(function () {
      var s = Math.round((Date.now() - t0) / 1000);
      if (s < 3) return;
      showResult('<p class="tool-hint">' + esc(fmt(T('loadingSec'), { s: s })) + '</p>');
    }, 1000);
    get(url).then(function (d) {
      clearInterval(tick);
      lastCoord = d || {};
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
    GRCz10: 1, GRCz11: 1, GRCz12tu: 1, GRCz12ab: 1, GRCz13ab: 1
  };
  // API 失败时侧挂仍可按已知直连边过滤
  // 2026-10-31：NCBI 品系装配（contig N50 ≥ 1 Mb）。每条都只写到 GRCz11 或 GRCz12tu 的单跳链，
  // 所以这里按「实际有链的目标」列，不写多跳可达项（多跳由后端 BFS 处理，界面上不铺开）。
  var FALLBACK_DIRECT = {
    ASM1840007v1: { GRCz11: 1 },
    ASM3317019v3: { GRCz12tu: 1 },
    ASM2018471v1: { GRCz12tu: 1 },
    'fDanRer4.1': { GRCz12tu: 1 },
    'fDreABH1.1': { GRCz12tu: 1 },
    'fDreTuH1.2': { GRCz12tu: 1 }
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
    // 侧挂（NCBI 品系装配）：路由表就绪后按真实可达列（可多跳，如 ASM1840007v1 → GRCz11 → GRCz12tu）；
    // 路由表没回来之前只按已知单跳兜底，避免把整个目标列表都点亮。
    if (!isCoreBuild(src)) {
      if (routesReady) {
        var set0 = reachableBySource[src];
        return !!(set0 && set0[tgt]);
      }
      var dset = FALLBACK_DIRECT[src] || {};
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

  /* ==========================================================================
     染色体下拉（2026-11-02）
     --------------------------------------------------------------------------
     用户要求：染色体编号应随所选基因组版本变化，不该手打。
     数据：GET /api/genome/chromosomes?build=<来源版本>
       后端从链头 tName 取该装配真实存在的染色体（经 assembly report 归一到
       1–25 / MT），所以下拉里能选到的，就是链文件里换算得了的。
     每个版本只取一次（chromCache）；接口失败时退回 1–25 + MT，不影响换算本身。
     ========================================================================== */
  var CHROM_API = '/api/genome/chromosomes';
  var chromCache = {};
  /* 每个装配的染色体命名方案（GenBank / RefSeq / UCSC）。
     不同品系的染色体编号天然相同、序列完全不同，只有登录号能区分，
     所以这份说明必须跟着装配走，不能只留一个 chrN。 */
  var chromNaming = {};
  var chromSeq = 0;

  function chromFallback() {
    var out = [];
    for (var i = 1; i <= 25; i++) out.push({ value: String(i), label: 'chr' + i });
    out.push({ value: 'MT', label: 'chrMT' });
    return out;
  }

  function fillChromSelect(list, keep) {
    var sel = $('liftChrom');
    if (!sel) return;
    var prev = keep != null ? keep : String(sel.value || '');
    sel.innerHTML = '';
    var ph = document.createElement('option');
    ph.value = '';
    /* 2026-11-02：原来占位是一个「—」，加上未选来源时整框禁用，用户以为控件坏了。
       改成明确的动作提示，并且任何时候都不禁用。 */
    ph.textContent = T('chromPlaceholder') || 'Select chromosome';
    sel.appendChild(ph);
    var hit = false;
    (list || []).forEach(function (c) {
      var o = document.createElement('option');
      o.value = String(c.value);
      o.textContent = c.label || c.value;
      /* 悬停给出全称：官方序列名 + 登录号（如 chr5 · Chromosome5 · CM035059.1），
         窄框里 label 可能被截断，title 不截断。 */
      if (c.title) o.title = c.title;
      sel.appendChild(o);
      if (String(c.value) === prev) hit = true;
    });
    /* 粘贴进来的染色体号可能不在本版列表里（chrUn_* 等）：补一个选项保留用户输入，
       换算时由后端按多候选解析；解析不到会明确失败，不会给出近似值。 */
    if (prev && !hit) {
      var extra = document.createElement('option');
      extra.value = prev;
      extra.textContent = /^\d+$/.test(prev) ? 'chr' + prev
        : (prev === 'MT' ? 'chrMT' : prev);
      sel.appendChild(extra);
      hit = true;
    }
    sel.value = hit ? prev : '';
  }

  function setChromValue(v) {
    var val = String(v == null ? '' : v).trim();
    if (!val) return;
    var sel = $('liftChrom');
    if (!sel) return;
    var exists = false;
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].value === val) { exists = true; break; }
    }
    if (!exists) {
      var o = document.createElement('option');
      o.value = val;
      o.textContent = /^\d+$/.test(val) ? 'chr' + val : (val === 'MT' ? 'chrMT' : val);
      sel.appendChild(o);
    }
    sel.value = val;
  }

  function srcSelEl() { return $('liftSourceBuild'); }

  /* 染色体下拉下方那行「本版沿用 UCSC 的 chr1–chr25 命名……」说明已撤（2026-11-04）。
     用户的原话是：这类与结果无关的小注释不要出现在结果区/输入区；染色体怎么命名
     属于使用说明的范围，不占功能页的版面。函数保留成空实现，调用点不用改。 */
  function renderChromNote() {
    var el = $('liftChromNote');
    if (!el) return;
    el.textContent = '';
    el.hidden = true;
    el.removeAttribute('title');
  }

  function loadChromosomes() {
    var sel = $('liftChrom');
    if (!sel) return Promise.resolve();
    var build = (($('liftSourceBuild') && $('liftSourceBuild').value) || '').trim();
    if (!build) {
      /* 2026-11-02 修正（用户第二次反馈）：染色体必须由版本决定，没选版本就不能有值。
         上一版为了「点得开」先塞了 1–25 通用清单，结果还没选版本框里就出现 chr2，
         正好违背了这条规则。现在清空 + 禁用，占位直接写「先选来源版本」；
         同时给来源下拉加强调色，指明该先点哪里。
         另外浏览器会恢复表单控件上次的值（刷新后 select 仍显示 chr2），
         所以这里强制 value 置空，bind() 末尾再补一次以防恢复晚于本次调用。 */
      fillChromSelect([], '');
      var ph = sel.options[0];
      if (ph) ph.textContent = T('chromPickSource');
      sel.value = '';
      if (sel.selectedIndex !== 0) sel.selectedIndex = 0;
      sel.disabled = true;
      sel.setAttribute('title', T('chromNeedSource'));
      renderChromNote(null);
      if (srcSelEl()) srcSelEl().classList.add('needs-pick');
      return Promise.resolve();
    }
    sel.disabled = false;
    sel.removeAttribute('title');
    if (srcSelEl()) srcSelEl().classList.remove('needs-pick');
    /* 之前浏览器恢复进来的值不属于这个装配时，上面的 fillChromSelect 会把它
       补成一个选项（粘贴场景用）。但「没选过版本就刷新页面」属于恢复残留，
       来源下拉此时是空占位，直接丢弃这个值更符合规则。 */
    if (chromCache[build]) {
      fillChromSelect(chromCache[build]);
      renderChromNote(chromNaming[build]);
      return Promise.resolve();
    }
    /* 链头解析（首次约 1–7 s）：先摆一份通用清单，别让下拉空着；
       真实清单回来再替换，选中项保持不变。 */
    if (sel.options.length <= 1) fillChromSelect(chromFallback());
    var seq = ++chromSeq;
    return get(CHROM_API + '?build=' + encodeURIComponent(build)).then(function (d) {
      if (seq !== chromSeq) return;   // 期间又切了版本，丢弃过期响应
      var list = ((d && d.ok && d.chromosomes) || []).map(function (c) {
        return {
          value: String(c.value),
          label: c.label || ('chr' + c.value),
          title: c.title || '',
          accession: c.accession || ''
        };
      });
      if (!list.length) list = chromFallback();
      chromCache[build] = list;
      chromNaming[build] = (d && d.naming) || null;
      fillChromSelect(list);
      renderChromNote(chromNaming[build]);
      /* 换装配后，原来选的那条染色体不一定还在新清单里（例如切到没有 MT 的
         GRCz12ab）。fillChromSelect 为了粘贴场景会把它补成选项保留下来，
         这里按「该装配真实清单」再校一次：不在就清空，避免框里挂着一个
         这个版本根本没有的染色体。 */
      var keep = String(sel.value || '');
      if (keep) {
        var ok = list.some(function (c) { return String(c.value) === keep; });
        if (!ok) {
          sel.value = '';
          if (sel.selectedIndex !== 0) sel.selectedIndex = 0;
        }
      }
    }).catch(function () {
      if (seq !== chromSeq) return;
      fillChromSelect(chromFallback());
      chromNaming[build] = null;
      renderChromNote(null);
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
      srcSel.addEventListener('change', function () {
        filterTargetBuilds();
        loadChromosomes();
      });
    }
    /* 2026-11-07：链方向从 hero 药丸改成面板内 #liftStrand 下拉。改链会让上一次
       换算结果（含 strand 字段）失效，必须清掉，否则切语言重绘时会把 + 链的结果
       重新画到 - 链的选择下面，看起来像"改了没生效"。 */
    var strandSel = $('liftStrand');
    if (strandSel) {
      strandSel.addEventListener('change', function () {
        lastCoord = null;
        var box = $('toolResult');
        if (window.ZFResult) window.ZFResult.clear('liftover');
        else if (box) { box.innerHTML = ''; box.classList.add('hidden'); }
        if (box) box.removeAttribute('data-lift-result');
        hint('');
      });
    }
    // 默认 GRCz11 → GRCz12tu：注释坐标出自 Ensembl r116（GRCz11），而本平台展示与取序列
    // 统一用 GRCz12tu，所以进页就摆好这一对；用户仍可改任意版本。这里先按来源把目标下拉滤一遍。
    filterTargetBuilds();
    // 首次进入：按当前来源版本填一次染色体下拉
    loadChromosomes();
    /* 浏览器的表单状态恢复会晚于脚本执行：刷新后 #liftChrom 可能被填回上次的
       chr2，而来源下拉默认已是 GRCz11 → 会出现「染色体属于别的版本」。这里补几次重跑
       （来源为空就清空），再监听 bfcache 返回。 */
    [0, 150, 500].forEach(function (ms) { setTimeout(loadChromosomes, ms); });
    window.addEventListener('pageshow', function (e) {
      if (e && e.persisted) loadChromosomes();
    });
    // hero 重画（切 tab／切语言）后药丸会复位为全显，再滤一遍
    document.addEventListener('hub-lang-change', function () {
      setTimeout(function () {
        filterTargetBuilds();
        // 占位项文案是 JS 生成的，切语言后不会自动跟着变，重填一次（走缓存）
        loadChromosomes();
        var nb = (($('liftSourceBuild') && $('liftSourceBuild').value) || '').trim();
        renderChromNote(nb ? chromNaming[nb] : null);
        if (lastHintKey) showHint(lastHintKey);
        if (lastCoord) {
          if (lastCoord.ok && lastCoord.target_span) showResult(okCard(lastCoord));
          else if (Object.keys(lastCoord).length) showResult(failCard(lastCoord));
        }
      }, 0);
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
  var lastBuilds = null; // {source,target,converted} —— 版本转换时用于表头/汇总

  /* 版本选择：原版本 / 目标版本，2026-11-12 起与坐标换算一样是全部 18 个装配。
     GRCz11 ⇄ GRCz12tu 走本地 ID 对照表；其余组合后端给稳定标识符 + chain 坐标。
     两端都选同一版本时，仍等价于原来的同库互查（GRCz11/GRCz12tu 行为不变）。 */
  function currentBuilds() {
    var s = $('idMapSourceBuild');
    var t = $('idMapTargetBuild');
    return {
      source: (s && s.value) || 'GRCz11',
      target: (t && t.value) || 'GRCz11'
    };
  }

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
      // 2026-10-31：UniProt 进跨库 ID（库内 gene_xref 已回填 21k 条 UniProt accession）
      uni: c.uniprot_id || xrefOf(r, ['uniprot']),
      // 2026-11-06：跨版本时后端会给目标版本坐标（target_coords），
      // 比只显示染色体号有用得多；同版本时仍显示染色体。
      // 2026-11-12：18 版全开后后端另给来源坐标与坐标换算状态，供导出/清查。
      chrom: r.target_coords || c.chromosome || '',
      srcCoords: r.source_coords || '',
      coordStatus: r.coord_status || '',
      stableOnly: r.identity_basis === 'stable_ids_with_chain_coords'
    };
  }

  function cell(v) {
    return '<td>' + (v ? esc(v) : '<span class="id-map-empty">—</span>') + '</td>';
  }

  /* 2026-11-13 独立核验：约 28.6% 的跨版本坐标无法由 UCSC chain 换算。
     空坐标不能只画一个「—」让人以为是没查到，要写明是链失败 / 断裂点 /
     无源坐标；坐标本身也要说清是换算区间而非官方注释坐标。 */
  var COORD_KEYS = {
    liftover_failed: 'coordLiftoverFailed',
    liftover_split: 'coordSplit',
    not_precomputed: 'coordNotPrecomputed',
    no_hub_coords: 'coordNoHub',
    not_resolved: 'coordNotResolved'
  };
  function coordCell(r) {
    if (r.chrom) return '<td>' + esc(r.chrom) + '</td>';
    var code = r.coordStatus || '';
    var txt = COORD_KEYS[code] ? T(COORD_KEYS[code]) : (code || '—');
    return '<td><span class="id-map-empty" title="' + esc(code) + '">' +
      esc(txt) + '</span></td>';
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
    var crossBuild = !!(lastBuilds && (lastBuilds.identity_basis === 'stable_ids_with_chain_coords' ||
                                       lastBuilds.identity_basis === 'ensembl_release_gene_set'));
    var head = ['idColQuery', 'idColStatus', 'idColStd', 'idColIds', 'idColChrom']
      .map(function (k) {
        // 跨版本时这一列是 chain 换算区间，不是官方染色体/注释坐标，表头要写明
        var t = T((k === 'idColChrom' && crossBuild) ? 'idColTgtCoord' : k);
        // 跨版本时在「标准名」列标出目标装配，避免把 GRCz12tu 符号当成 GRCz11 的看；
        // Ensembl release 用友好标签（Ensembl r54），而不是 ens:54 这种内部值。
        if (k === 'idColStd' && lastBuilds && lastBuilds.converted) {
          t += ' · ' + (lastBuilds.target_label || lastBuilds.target);
        }
        return '<th>' + esc(t) + '</th>';
      }).join('');
    var body = lastRows.map(function (r) {
      var st;
      if (r.status === 'ambiguous') {
        st = '<span class="id-map-badge is-amb">' + esc(fmt(T('stAmb'), { n: r.candCount })) + '</span>' +
          (r.candNames ? '<span class="id-map-cands">' + esc(r.candNames) + '</span>' : '');
      } else if (r.status === 'unique') {
        st = '<span class="id-map-badge is-ok">' + esc(T('stUnique')) + '</span>';
      } else if (r.status === 'not_in_release') {
        /* 基因存在，但目标 Ensembl release 基因集里没有它 —— 不是「查不到」，
           更不能冒充成未命中。 */
        st = '<span class="id-map-badge is-amb">' + esc(T('stNotInRelease')) + '</span>';
      } else {
        st = '<span class="id-map-badge is-none">' + esc(T('stNone')) + '</span>';
      }
      var stdCell = r.std ? esc(r.std) : '<span class="id-map-empty">—</span>';
      if (r.local && r.local !== r.std) {
        stdCell += '<span class="id-map-local">' + esc(r.local) + '</span>';
      }
      // 目标是 Ensembl release 时，第一行标出具体 release，让「Ensembl 版本」
      // 这个选择维度在结果里看得见，而不是仍旧笼统写 Ensembl。
      var ensLabel = (lastBuilds && lastBuilds.target_ensembl_release)
        ? 'Ensembl r' + lastBuilds.target_ensembl_release : 'Ensembl';
      var ids = [
        [ensLabel, r.ens],
        ['ZFIN', r.zfin],
        ['NCBI', r.ncbi],
        ['UniProt', r.uni]
      ].map(function (pair) {
        return '<span class="id-map-idli"><em>' + pair[0] + '</em>' +
          (pair[1] ? esc(pair[1]) : '<span class="id-map-empty">—</span>') + '</span>';
      }).join('');
      return '<tr>' +
        '<td class="prov-mono">' + esc(r.query) + '</td>' +
        '<td>' + st + '</td>' +
        '<td class="prov-mono">' + stdCell + '</td>' +
        '<td class="id-map-ids prov-mono">' + ids + '</td>' +
        coordCell(r) +
        '</tr>';
    }).join('');
    var summary = '<p class="tool-hint' + (lastCapped ? ' warn' : '') + '" id="idMapSummary">' +
      esc(fmt(T('idSummary'), { n: lastInputs, u: u, a: a, m: m }) +
        (lastBuilds && lastBuilds.converted ? ' · ' +
          (lastBuilds.source_label || lastBuilds.source) + ' → ' +
          (lastBuilds.target_label || lastBuilds.target) : '') +
        (lastCapped ? ' · ' + T('idTooMany') : '')) + '</p>';
    /* 18 版全开后的口径说明：只要有一端没有本地注释表，就讲清楚
       「稳定 ID + chain 坐标」，并报出坐标换算命中数（不给近似坐标）。 */
    var notesHtml = '';
    if (lastBuilds && lastBuilds.identity_basis === 'stable_ids_with_chain_coords') {
      notesHtml += '<p class="tool-hint" id="idMapVersionNote">' + esc(T('idVersionNote')) + '</p>';
    }
    if (lastBuilds && lastBuilds.identity_basis === 'ensembl_release_gene_set') {
      notesHtml += '<p class="tool-hint" id="idMapEnsNote">' + esc(T('idEnsReleaseNote')) + '</p>';
    }
    if (lastBuilds && (lastBuilds.identity_basis === 'stable_ids_with_chain_coords' ||
                       lastBuilds.identity_basis === 'ensembl_release_gene_set')) {
      notesHtml += '<p class="tool-hint" id="idMapCoordCaveat">' + esc(T('idCoordCaveat')) + '</p>';
    }
    var cs = lastBuilds && lastBuilds.coord_summary;
    if (cs && cs.total) {
      notesHtml += '<p class="tool-hint" id="idMapCoordStat">' +
        esc(fmt(T('idCoordStat'), { ok: cs.ok, n: cs.total })) + '</p>';
    }
    showResult('<div class="id-map-result">' + summary + notesHtml +
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
    var builds = currentBuilds();
    postJson(BATCH_API, {
      identifiers: ids,
      source_build: builds.source,
      target_build: builds.target
    }).then(function (res) {
      var d = res.data || {};
      if (!res.ok || !d.results) {
        var msg = (d && (d.detail || d.message)) || ('HTTP ' + res.status);
        hint(fmt(T('idFail'), { msg: String(msg).slice(0, 200) }), true);
        return;
      }
      lastBuilds = d.builds || null;
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
    var head = ['idColQuery', 'idColStatus', 'idColStd', 'idColLocal', 'idColEns', 'idColZfin', 'idColNcbi', 'idColUni', 'idColChrom', 'idColSrcCoord', 'idColCoordStatus']
      .map(function (k) { return T(k); });
    var rows = lastRows.map(function (r) {
      var st = r.status === 'unique' ? T('stUnique')
        : (r.status === 'ambiguous' ? fmt(T('stAmb'), { n: r.candCount }) + ' ' + r.candNames
          : (r.status === 'not_in_release' ? T('stNotInRelease') : T('stNone')));
      return [r.query, st.trim(), r.std, r.local, r.ens, r.zfin, r.ncbi, r.uni, r.chrom,
        r.srcCoords || '', r.coordStatus || ''];
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
      if (window.ZFResult) window.ZFResult.clear('liftover');
      else { tr.innerHTML = ''; tr.classList.add('hidden'); }
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
      lastBuilds = null;
      var tr = $('toolResult');
      if (window.ZFResult) window.ZFResult.clear('liftover');
      else if (tr) { tr.innerHTML = ''; tr.classList.add('hidden'); }
      if (tr) tr.removeAttribute('data-lift-result');
      hint('');
      var c = $('idMapCount');
      if (c) c.textContent = '';
      ['idMapCsvBtn', 'idMapCopyBtn'].forEach(function (id) { var b = $(id); if (b) b.disabled = true; });
    });
    // 版本一改，旧结果立刻作废（否则会把 GRCz11 的结果当 GRCz12tu 的读）
    ['idMapSourceBuild', 'idMapTargetBuild'].forEach(function (id) {
      var sel = $(id);
      if (!sel) return;
      sel.addEventListener('change', function () {
        if (!lastRows.length) return;
        lastRows = [];
        lastBuilds = null;
        var host = $('toolResult');
        if (window.ZFResult) window.ZFResult.clear('liftover');
        else if (host) { host.innerHTML = ''; host.classList.add('hidden'); }
        if (host) host.removeAttribute('data-lift-result');
        hint('');
      });
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
