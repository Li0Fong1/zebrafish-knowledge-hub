/* ==========================================================================
   home-feed.js —— 首页「今日研究 / 文献更新」渲染 + 一键增量更新
   --------------------------------------------------------------------------
   数据来源（都是真产物，接口逐段容错）：
     GET  /api/hub/research-feed          → daily / updates / years / stats / maintenance
     POST /api/hub/maintenance/run?job=literature  → 触发文献增量抓取+入库
     GET  /api/hub/maintenance/status     → 轮询任务是否还在跑
   设计原则：
     · 只读渲染，缺字段就留空，不编造数字；接口不可用时整块静默隐藏
     · 更新按钮触发后按 5s 轮询状态，跑完自动刷新一次列表
     · 页面不在可见状态时不轮询（共享服务器，省资源）
   ========================================================================== */
(function () {
  'use strict';

  var FEED_URL = '/api/hub/research-feed';
  var STATUS_URL = '/api/hub/maintenance/status';
  var RUN_URL = '/api/hub/maintenance/run?job=literature';
  var POLL_MS = 5000;
  var POLL_MAX = 240;          // 最长跟踪 20 分钟

  var timer = null, ticks = 0, lastFeed = null, lastRunning = false;

  /* ---------- 文案：整段由本文件负责，不再依赖 i18n 字典逐词替换 ---------- */
  var TEXT = {
    zh: {
      dailyHead: '每日斑马鱼研究', updHead: '文献更新', citeHead: '引用本平台',
      statusDone: '每日更新已完成 • {t}',
      loading: '加载中…', dailyUnavailable: '今日研究暂不可用',
      noTitle: '(无标题)', noAbstract: '（该条无摘要）',
      pool: '候选池 {n} 篇 · 来源 catalog {lines} 行 · 缓存 {at}',
      dayNote: '同候选池内按日期确定性选取',
      dayAria: '选择日期（查看当天的每日论文）',
      linkPubmed: 'PubMed 原文 →', linkDoi: 'DOI 原文 →', linkLit: '问助手这篇 →',
      linkSearch: '按题名检索原文 →', linkFree: '免费全文 →',      linkLocal: '在本库检索 →',      linkNoteSearch: '该条本库无 PMID，改为按题名检索',
      linkNoteDoiBad: '该条 DOI 与题名不符，已停用外链',
      moreStudies: '更多研究 →', moreUpdates: '更多更新 →',
      noUpdates: '暂无更新记录（文献同步日志为空）。',
      updDue: '已有新文献可抓取（到期）',
      updFresh: '已是最新（每 {h} 小时自动检查）',
      updLastNew: '上次检查 {t} · 新增 {n} 篇',
      updLastNone: '上次检查 {t} · 无新增',
      updLastUnknown: '上次检查 {t}',
      updNever: '尚未检查过',
      updDueShort: '已到更新周期',
      updFailed: '上次更新失败：{msg}',
      stageCollecting: '检索与入库中', stageIndexing: '建索引中', stageFinalizing: '整理首页数据',
      stageNew: '已入库 {n} 篇',
      detailNew: '新增 {n} 篇', detailUpsert: '写库 {n} 条', detailElapsed: '用时 {s}s',
      actWeekly: '每周自动更新', actManual: '手动更新', actCli: '命令行更新',
      actFulltext: '全文入库', actShards: '向量分片登记',
      actGoldMeta: '全文元数据回填', actGoldReady: '全文状态升级',
      updRunning: '更新进行中…', updRunningAt: '更新进行中…（已等 {s}s）',
      btnUpdate: '更新文献', btnUpdating: '更新中…',
      btnTitle: '增量抓取最新文献并入库（PubMed / Europe PMC / WoS）',
      triggering: '正在触发文献增量更新…', queued: '已有更新任务在跑，继续跟踪…',
      failed: '更新失败：{msg}', triggerFailed: '触发失败（服务不可达）',
      btnConfirm: '再点一次确认', 
      confirmHint: '将抓取新文献并入库；4 秒内再点一次开始',
      confirmMsg: '将开始文献增量更新：抓取新文献 → 入库 → 必要时重建向量索引。\n这一步会持续较长时间并占用服务器资源，确定现在执行？',
      notePapers: '库内文献 {n} 篇', noteYear: '最新年份 {y}（{c} 篇）',
      noteCatalog: 'catalog {n} 行', noteKnowledge: 'knowledge.d {n} 个文件',
      noteLast: '上次更新 {t}',
      citeLead: '如果本平台对你的研究有帮助，请引用：',
      citeScope: '平台能力：搜索基因、跑 BLAST、读文献精排；答案自动挂本库与 ZFIN / Ensembl / NCBI 等组学库双链（资料库 ≠ 仅 PubMed）。',
      citeNote: '引用本平台时请注明本地库与索引快照日期（出版物信息待补，不编造）。'
    },
    en: {
      dailyHead: "Daily Zebrafish Research", updHead: 'Latest Updates', citeHead: 'Cite Us',
      statusDone: 'Daily update completed • {t}',
      loading: 'Loading…', dailyUnavailable: "Today's pick is unavailable",
      noTitle: '(untitled)', noAbstract: '(no abstract for this record)',
      pool: 'pool {n} records · catalog {lines} lines · cache {at}',
      dayNote: 'deterministic pick within the same pool',
      dayAria: 'Pick a date to view that day\u2019s paper selection',
      linkPubmed: 'PubMed source →', linkDoi: 'DOI source →', linkLit: 'Ask the assistant →',
      linkSearch: 'Search by title →', linkFree: 'Free full text →',
      linkLocal: 'Search in this library →',
      linkNoteSearch: 'No PMID in this library — searching by title instead',
      linkNoteDoiBad: 'DOI does not match this title; link disabled',
      moreStudies: 'More studies →', moreUpdates: 'More updates →',
      noUpdates: 'No update log yet.',
      updDue: 'New literature is due for collection',
      updFresh: 'Up to date (auto-checked every {h} h)',
      updLastNew: 'Last check {t} · {n} new',
      updLastNone: 'Last check {t} · nothing new',
      updLastUnknown: 'Last check {t}',
      updNever: 'Never checked',
      updDueShort: 'due for refresh',
      updFailed: 'Last update failed: {msg}',
      stageCollecting: 'fetching & ingesting', stageIndexing: 'rebuilding index',
      stageFinalizing: 'refreshing home feed', stageNew: '{n} ingested',
      detailNew: '{n} new', detailUpsert: '{n} rows written', detailElapsed: '{s}s',
      actWeekly: 'Weekly auto-update', actManual: 'Manual update', actCli: 'CLI update',
      actFulltext: 'Full-text ingest', actShards: 'Vector shards registered',
      actGoldMeta: 'Full-text metadata backfill', actGoldReady: 'Full-text status upgrade',
      updRunning: 'Updating…', updRunningAt: 'Updating… ({s}s elapsed)',
      btnUpdate: 'Update literature', btnUpdating: 'Updating…',
      btnTitle: 'Incremental fetch and ingest of new literature (PubMed / Europe PMC / WoS)',
      triggering: 'Triggering incremental literature update…', queued: 'A job is already running, tracking it…',
      failed: 'Update failed: {msg}', triggerFailed: 'Could not trigger (service unreachable)',
      btnConfirm: 'Click again', 
      confirmHint: 'Fetches and ingests new papers; click again within 4s',
      confirmMsg: 'This runs an incremental literature update: fetch new records → ingest → rebuild the vector index if needed.\nIt may take a long while and uses server resources. Run it now?',
      notePapers: '{n} papers in library', noteYear: 'latest year {y} ({c} papers)',
      noteCatalog: 'catalog {n} lines', noteKnowledge: 'knowledge.d {n} files',
      noteLast: 'last update {t}',
      citeLead: 'If you find this platform helpful for your research, please cite:',
      citeScope: 'What it does: search genes, run BLAST, read reranked literature; every answer links back to this library and to ZFIN / Ensembl / NCBI (this is a knowledge base, not just PubMed).',
      citeNote: 'When citing, please state the local library and index snapshot date (publication details are pending; nothing is fabricated).'
    }
  };
  var CITATION = 'Zebrafish Hub: an AI-powered knowledge hub for zebrafish functional genomics.';

  function curLang() {
    var l = (document.documentElement.getAttribute('lang') || 'en').toLowerCase();
    return l.indexOf('zh') === 0 ? 'zh' : 'en';
  }
  function L(k) {
    var d = TEXT[curLang()] || TEXT.en;
    return d[k] != null ? d[k] : (TEXT.en[k] || '');
  }
  function fmt(s, o) {
    return String(s).replace(/\{(\w+)\}/g, function (m, k) { return o[k] != null ? o[k] : m; });
  }
  function setText(id, v) { var el = $(id); if (el) el.textContent = v; }

  /* 静态文案（标题 / 按钮 / 页脚）：随语言切换重绘 */
  function applyStatic() {
    setText('homeDailyHead', L('dailyHead'));
    setText('homeUpdHead', L('updHead'));
    setText('homeCiteHead', L('citeHead'));
    // 无障碍名也要跟着切，不能一边英文一边中文
    var sec = [['.home-daily', 'dailyHead'], ['.home-updates', 'updHead'], ['.home-cite', 'citeHead']];
    sec.forEach(function (s) {
      var el = document.querySelector(s[0]);
      if (el) el.setAttribute('aria-label', L(s[1]));
    });
    setText('homeDailyMore', L('moreStudies'));
    setText('homeUpdMore', L('moreUpdates'));
    setText('homeCiteNote', L('citeNote'));
    setText('homeCiteScope', L('citeScope'));
    var cite = $('homeCiteText');
    if (cite) {
      cite.innerHTML = esc(L('citeLead')) + ' <em>' + esc(CITATION) + '</em>';
    }
    var b = $('homeUpdateBtn');
    if (b) {
      b.title = L('btnTitle');
      b.textContent = lastRunning ? L('btnUpdating') : L('btnUpdate');
    }
  }

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function fmtTime(v) {
    if (v == null || v === '') return '';
    var n = Number(v);
    if (Number.isFinite(n) && n > 1e9) {          // epoch 秒
      return new Date(n * 1000).toLocaleString();
    }
    return String(v).replace('T', ' ').replace('Z', ' UTC');
  }
  function shortDate(s) {
    var t = String(s || '').slice(0, 10);
    return t || '';
  }
  function fmtDate(iso) {
    var s = String(iso || '').slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
    var p = s.split('-');
    var d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
    try {
      return d.toLocaleDateString(curLang() === 'zh' ? 'zh-CN' : 'en-US',
        { year: 'numeric', month: 'long', day: 'numeric' });
    } catch (e) { return s; }
  }

  /* 同步日志的 action 是脚本里的技术名，直接展示给读者看不出在做什么 */
  var ACTION_KEYS = {
    weekly_literature: 'actWeekly',
    manual_literature: 'actManual',
    cli_literature: 'actCli',
    register_pdfku_gold: 'actFulltext',
    register_shards: 'actShards',
    backfill_fulltext_gold_meta: 'actGoldMeta',
    upgrade_gold_status_ready: 'actGoldReady'
  };
  function actionLabel(a) {
    var k = ACTION_KEYS[a];
    return k ? L(k) : (a || '');
  }

  /* 触发者名和 action 名经常重复（手动更新 / manual_update），只在有意义时显示 */
  function actorName(a) {
    var v = String(a || '').trim();
    if (!v) return '';
    if (v === 'manual_update' || v === 'system' || v === 'auto') return '';
    return v;
  }

  function detailText(raw) {
    try {
      var d = JSON.parse(raw);
      // 文献更新事件：只挑「新增几篇 / 写库几条 / 用时」三个读者关心的数字
      if (d && d.trigger && (d.new != null || d.upserted_paper != null)) {
        var bits = [];
        if (d.new != null) bits.push(fmt(L('detailNew'), { n: d.new }));
        if (d.upserted_paper != null) bits.push(fmt(L('detailUpsert'), { n: d.upserted_paper }));
        if (d.elapsed_sec != null) bits.push(fmt(L('detailElapsed'), { s: d.elapsed_sec }));
        if (bits.length) return bits.join(' · ');
      }
      // 事件日志键很多，只挑对读者有意义的计数，最多 4 项
      var pri = ['new', 'inserted_paper', 'updated_paper', 'upserted_gold', 'scanned', 'registered', 'ok_files', 'skipped'];
      var keys = pri.filter(function (k) { return d[k] != null && typeof d[k] !== 'object'; });
      Object.keys(d).forEach(function (k) {
        if (keys.indexOf(k) >= 0 || k === 'action' || k === 'campaign') return;
        var v = d[k];
        if (v == null || v === '' || typeof v === 'object') return;
        keys.push(k);
      });
      var parts = keys.slice(0, 4).map(function (k) {
        var v = d[k];
        return (k + '=' + String(v)).slice(0, 60);
      });
      if (keys.length > 4) parts.push('…');
      if (parts.length) return parts.join(' · ');
    } catch (e) { /* 非 JSON 就原样截断 */ }
    return String(raw || '').slice(0, 120);
  }

  function renderDaily(feed) {
    var list = $('homePapers');
    if (!list) return;

    // 日选一篇为主，再取随后的 1～2 篇同一候选池，凑成设计稿里的多条目列表
    var entries = [];
    var dl = (feed && feed.daily_list) || [];
    if (dl.length) entries = dl.slice(0, 3);
    else if (feed && feed.daily) entries = [feed.daily];

    var noteEl = $('homeDailyNote');
    if (noteEl) {
      var d0 = entries[0];
      noteEl.textContent = d0 && d0.pool_size
        ? fmt(L('pool'), { n: d0.pool_size, lines: d0.pool_lines_scanned || '?', at: d0.pool_generated_at || '-' })
        : '';
    }

    // 日期下拉框（选中项不覆盖：由后端返回的 day 决定）
    var dateEl = $('homeAgentDate');
    if (dateEl) {
      dateEl.setAttribute('aria-label', L('dayAria'));
      var iso = (entries[0] && entries[0].date) || (feed && feed.day) || '';
      if (!dateEl.options.length) buildDayOptions(iso);
      if (iso && dateEl.value !== iso) dateEl.value = iso;
    }
    var dnote = $('homeAgentDateNote');
    if (dnote) dnote.textContent = L('dayNote');

    if (!entries.length) {
      list.innerHTML = '<p class="agent-paper-note">' +
        esc((feed && feed.daily_error) ? L('dailyUnavailable') : L('loading')) + '</p>';
      var ln0 = $('homeDailyLinkNote');
      if (ln0) ln0.textContent = '';
      renderStatus(feed);
      return;
    }

    list.innerHTML = entries.map(function (d) {
      var meta = [];
      if (d.journal) meta.push(esc(d.journal));
      else if (d.year) meta.push(esc(d.year));
      if (d.pmid) meta.push('PMID ' + esc(d.pmid));
      else if (d.doi) meta.push('DOI ' + esc(d.doi));

      var kind = d.link_kind || (d.pmid ? 'pubmed' : (d.doi ? 'doi' : 'search'));
      var label = kind === 'pubmed' ? L('linkPubmed')
        : kind === 'doi' ? L('linkDoi') : L('linkSearch');
      var url = d.url || '';
      var links = url
        ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(label) + '</a>'
        : '<a href="#/chat/q=' + encodeURIComponent(String(d.title || '').slice(0, 120)) + '">' + esc(L('linkLit')) + '</a>';
      if (d.free_url) {
        links += '<a href="' + esc(d.free_url) + '" target="_blank" rel="noopener">' + esc(L('linkFree')) + '</a>';
      }
      if (d.doi_untrusted) {
        links += '<span class="agent-paper-note">' + esc(L('linkNoteDoiBad')) + '</span>';
      }
      // 闭环：每日速览 → 直接在本地全文库里按题名检索（马铃薯 Daily Research 的目的就是"看得见就能往下查"）
      if (d.title) {
        // 2026-10-28：News 调试检索页已删除，「在本库检索」改为直接问助手
        // （助手内部就是本库全文检索那条链路），不再有去不掉的空页面。
        links += '<a class="is-local" href="#/chat/q=' +
          encodeURIComponent(String(d.title).slice(0, 120)) + '">' + esc(L('linkLocal')) + '</a>';
      }

      return '<article class="agent-paper">' +
        '<span class="agent-paper-ico" aria-hidden="true">📄</span>' +
        '<div class="agent-paper-body">' +
        '<p class="agent-paper-title">' + esc(d.title || L('noTitle')) + '</p>' +
        (meta.length ? '<p class="agent-paper-meta">' + meta.join(' | ') + '</p>' : '') +
        '<p class="agent-paper-abs">' + esc(d.abstract || L('noAbstract')) + '</p>' +
        '<div class="agent-paper-links">' + links + '</div>' +
        '</div></article>';
    }).join('');

    var note = $('homeDailyLinkNote');
    if (note) {
      var d2 = entries[0];
      note.textContent = (d2.link_kind === 'search') ? L('linkNoteSearch') : '';
    }
    renderStatus(feed);
  }

  /* 绿色状态条：每日更新已完成 • 时间 */
  function renderStatus(feed) {
    var el = $('homeAgentStatusText');
    if (!el) return;
    var m = (feed && feed.maintenance) || {};
    var t = m.last_ts ? fmtTime(m.last_ts) : fmtTime(feed && feed.generated_at);
    if (m.running) { el.textContent = L('updRunning'); return; }
    el.textContent = t ? fmt(L('statusDone'), { t: t }) : L('loading');
  }

  /* 更新列表只展示最近 3 条：首页要「少而准」，完整历史到文献页看 */
  var UPD_SHOW = 3;

  function renderUpdates(feed) {
    var list = $('homeUpdList');
    if (!list) return;
    var all = (feed && feed.updates) || [];
    var ups = all.slice(0, UPD_SHOW);
    if (!ups.length) {
      list.innerHTML = '<p class="home-upd-empty">' + esc(L('noUpdates')) + '</p>';
    } else {
      list.innerHTML = ups.map(function (u) {
        return '<div class="home-upd-item">' +
          '<span class="home-upd-date">' + esc(shortDate(u.event_time)) + '</span>' +
          '<span class="home-upd-body">' +
          '<span class="home-upd-title">' + esc(actionLabel(u.action) || '\u2014') +
          (u.index_version ? ' \u00b7 ' + esc(u.index_version) : '') + '</span>' +
          '<span class="home-upd-detail">' + (actorName(u.actor) ? esc(actorName(u.actor)) + ' \u2014 ' : '') +
          (u.detail ? esc(detailText(u.detail)) : '') + '</span>' +
          '</span></div>';
      }).join('');
    }
    var note = $('homeUpdNote');
    if (note) {
      var m0 = (feed && feed.maintenance) || {};
      var yrs = (feed && feed.years) || [];
      var bits = [];
      if (feed && feed.stats && feed.stats.papers) bits.push(fmt(L('notePapers'), { n: feed.stats.papers.toLocaleString() }));
      if (yrs.length) bits.push(fmt(L('noteYear'), { y: yrs[0].year, c: yrs[0].count }));
      if (m0.catalog_lines) bits.push(fmt(L('noteCatalog'), { n: m0.catalog_lines.toLocaleString() }));
      if (m0.knowledge_d_files) bits.push(fmt(L('noteKnowledge'), { n: m0.knowledge_d_files }));
      note.textContent = bits.join(' \u00b7 ');
    }
    renderUpdStatus(feed);
  }

  /* 阶段文案：collecting（检索入库）→ indexing（建索引）→ finalizing（重写首页缓存） */
  function stageLabel(pg) {
    var k = (pg && pg.stage) || '';
    if (k === 'indexing') return L('stageIndexing');
    if (k === 'finalizing') return L('stageFinalizing');
    return L('stageCollecting');
  }

  /* 状态行：跑过 ≠ 采到文献，所以必须把「上次检查时间 + 新增几篇」一起说清楚 */
  function renderUpdStatus(feed) {
    var st = $('homeUpdStatus');
    if (!st) return;
    var m = (feed && feed.maintenance) || {};
    if (m.running) {
      var pg = m.progress || {};
      var s = L('updRunning') + ' \u00b7 ' + stageLabel(pg);
      if (pg.new) s += ' \u00b7 ' + fmt(L('stageNew'), { n: pg.new });
      st.textContent = s;
      return;
    }
    if (m.last_ok === false && m.last_error) {
      st.textContent = fmt(L('updFailed'), { msg: String(m.last_error).slice(0, 70) });
      return;
    }
    if (!m.last_ts) { st.textContent = L('updNever'); return; }
    var t = fmtTime(m.last_ts);
    var n = (m.last_new == null) ? null : Number(m.last_new);
    var txt = n == null ? fmt(L('updLastUnknown'), { t: t })
      : n > 0 ? fmt(L('updLastNew'), { t: t, n: n })
        : fmt(L('updLastNone'), { t: t });
    if (m.due) txt += ' \u00b7 ' + L('updDueShort');
    st.textContent = txt;
  }

  /* 日期下拉框：最近 14 天（含今天），选中即可看那一天的确定性选取结果 */
  var DAYS = 14;
  /* 注意：不能用 toISOString——它按 UTC 换日，UTC+8 下会把“今天”算成昨天 */
  function isoOf(d) {
    var m = d.getMonth() + 1;
    var day = d.getDate();
    return d.getFullYear() + '-' + (m < 10 ? '0' : '') + m + '-' + (day < 10 ? '0' : '') + day;
  }
  function buildDayOptions(selected) {
    var sel = $('homeAgentDate');
    if (!sel) return;
    var keep = selected || sel.value || '';
    var opts = [];
    var now = new Date();
    for (var i = 0; i < DAYS; i++) {
      var d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
      var iso = isoOf(d);
      opts.push('<option value="' + iso + '">' + esc(fmtDate(iso)) + '</option>');
    }
    sel.innerHTML = opts.join('');
    if (keep) sel.value = keep;
  }

  function bindDaySelect() {
    var sel = $('homeAgentDate');
    if (!sel) return;
    sel.addEventListener('change', function () {
      var v = sel.value || '';
      loadFeed(v);
    });
  }

  function loadFeed(day) {
    var url = FEED_URL + '?_=' + Date.now() + (day ? '&day=' + encodeURIComponent(day) : '');
    return fetch(url, { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return null;
        lastFeed = d;
        renderDaily(d);
        renderUpdates(d);
        return d;
      })
      .catch(function () { return null; });
  }

  function rerender() {
    applyStatic();
    syncChipLabels();       // 语言切换时示例 chips 的标签/问题一起换
    buildDayOptions();      // 日期下拉框的日期格式随语言变
    if (lastFeed) { renderDaily(lastFeed); renderUpdates(lastFeed); }
    else loadFeed();
  }

  function setBtn(running, text) {
    var b = $('homeUpdateBtn');
    if (!b) return;
    lastRunning = !!running;
    b.disabled = !!running;
    b.textContent = text || L(running ? 'btnUpdating' : 'btnUpdate');
  }

  function pollRunning() {
    ticks += 1;
    if (ticks > POLL_MAX) { setBtn(false); loadFeed(); return; }
    if (document.hidden) {                    // 后台标签页降频，但别停（长任务要能回来）
      timer = setTimeout(pollRunning, 30000);
      return;
    }
    fetch(STATUS_URL + '?_=' + Date.now(), { credentials: 'same-origin', cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        var running = !!(s && s.running && s.running.literature);
        if (running) {
          var st = $('homeUpdStatus');
          if (st) {
            var pg = (s && s.progress) || {};
            var txt = fmt(L('updRunningAt'), { s: (ticks * POLL_MS / 1000 | 0) });
            txt += ' \u00b7 ' + stageLabel(pg);
            if (pg.new) txt += ' \u00b7 ' + fmt(L('stageNew'), { n: pg.new });
            st.textContent = txt;
          }
          timer = setTimeout(pollRunning, POLL_MS);
        } else if (ticks <= 3) {
          // 刚触发：任务可能还没真正起来，多给几个周期再判断结束
          timer = setTimeout(pollRunning, POLL_MS);
        } else {
          setBtn(false);
          loadFeed();
        }
      })
      .catch(function () { timer = setTimeout(pollRunning, POLL_MS); });
  }

  /* 2026-10-27：「更新文献」原来用 window.confirm 做二次确认。
     浏览器一旦拦截原生弹窗（勾过「阻止此页面创建更多对话框」、或内嵌 WebView），
     按钮就永久「点了没反应」。改成按钮自身两步确认：
     第一次点亮成「再点一次确认」，4 秒内再点一次才真正触发，超时自动复原。 */
  var updateArmed = false, updateArmTimer = 0;

  function disarmUpdate() {
    updateArmed = false;
    if (updateArmTimer) {
      clearTimeout(updateArmTimer);
      updateArmTimer = 0;
    }
    var b = $('homeUpdateBtn');
    if (!b) return;
    b.classList.remove('is-arming');
    if (!lastRunning) b.textContent = L('btnUpdate');
  }

  function bindUpdate() {
    var b = $('homeUpdateBtn');
    if (!b) return;
    b.addEventListener('click', function (e) {
      // 按钮现在挂在 <summary> 里：不挡住默认行为，点一下会把折叠框展开/收起
      e.preventDefault();
      e.stopPropagation();
      // 这个动作会真的去 PubMed/EPMC 增量抓取并入库，需要确认一次（页面内两步确认，不用原生 confirm）
      if (!updateArmed) {
        updateArmed = true;
        b.classList.add('is-arming');
        b.textContent = L('btnConfirm');
        var hint = $('homeUpdStatus');
        if (hint) hint.textContent = L('confirmHint');
        updateArmTimer = setTimeout(disarmUpdate, 4000);
        return;
      }
      disarmUpdate();
      var st = $('homeUpdStatus');
      if (st) st.textContent = L('triggering');
      setBtn(true, L('btnUpdating'));
      ticks = 0;
      if (timer) clearTimeout(timer);
      // 后端是「起线程后立刻返回」，所以这里马上开始轮询，不等 POST 回来
      timer = setTimeout(pollRunning, 1500);
      fetch(RUN_URL, { method: 'POST', credentials: 'same-origin' })
        .then(function (r) { return r.json().catch(function () { return null; }); })
        .then(function (res) {
          if (res && res.skipped) {
            if (st) st.textContent = L('queued');
            return;                            // 已有任务在跑，继续轮询跟踪
          }
          if (res && res.ok === false && res.error) {
            if (st) st.textContent = fmt(L('failed'), { msg: String(res.error).slice(0, 120) });
            if (timer) clearTimeout(timer);
            setBtn(false);
          }
        })
        .catch(function () {
          if (st) st.textContent = L('triggerFailed');
          if (timer) clearTimeout(timer);
          setBtn(false);
        });
    });
  }

  /* 快速示例：标签与发出的问题都跟随界面语言（英文界面发中文问题会导致中文回答） */
  function bindChips() {
    var box = $('homeChips');
    if (!box) return;
    box.addEventListener('click', function (e) {
      var chip = e.target.closest ? e.target.closest('.home-chip') : null;
      if (!chip) return;
      var inp = $('homeSearchInput'), sel = $('homeSearchScope'), btn = $('homeSearchBtn');
      var en = curLang() === 'en';
      var q = (en && chip.getAttribute('data-q-en')) || chip.getAttribute('data-q') || (chip.textContent || '').trim();
      var scope = chip.getAttribute('data-scope');
      if (inp) inp.value = q;
      if (sel && scope) sel.value = scope;
      if (btn) btn.click();          // 直接走首页既有搜索逻辑，不重复一份路由代码
    });
    syncChipLabels();
  }

  function syncChipLabels() {
    var en = curLang() === 'en';
    document.querySelectorAll('.home-chip').forEach(function (c) {
      var txt = (en && c.getAttribute('data-label-en')) || c.getAttribute('data-label-zh');
      if (txt) c.textContent = txt;
    });
  }

  function boot() {
    if (!$('homePapers') && !$('homeUpdList')) return;   // 首页结构不在，静默退出
    applyStatic();
    bindUpdate();
    bindChips();
    bindDaySelect();
    buildDayOptions();
    loadFeed();
    // 语言切换后按新语言重绘（i18n.js 会派发 hub-lang-change）
    document.addEventListener('hub-lang-change', rerender);
    // 每 5 分钟软刷新一次（仅首页可见时）
    setInterval(function () { if (!document.hidden) loadFeed(); }, 300000);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
