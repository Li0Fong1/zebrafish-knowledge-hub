/* ==========================================================================
   live-update.js —— 实时更新（SSE 推送优先，轮询兜底）
   --------------------------------------------------------------------------
   解决什么：
     改造前只有"固定定时器"（统计 60s、图源软刷 10min），**内容变了也不会立刻反映**，
     而且看不到任何"已更新"的反馈。本模块订阅后端 /api/hub/events（SSE），
     版本变化时只刷新两块真正会变的区域：研究影像流 + Live Atlas 统计。
   行为：
     · SSE 可用 → 事件驱动，秒级；不可用/断线 → 自动降级为轮询（可见 20s / 后台 120s）；
     · 后端不可达（如本地预览 501）→ 进入"离线预览"态，body.zf-offline + 顶部胶囊提示；
     · 页面不可见时**暂停一切请求**，回到前台立刻补一次（这台服务器资源紧张，必须省）；
     · localStorage.zfLive='0' 可整体关闭。
   依赖：app.js 暴露的 window.ZFRefresh.{gallery,stats}（缺失时静默跳过，不报错）。
   ========================================================================== */
(function () {
  'use strict';

  var VERSION_URL = '/api/hub/stream-version';
  var EVENTS_URL = '/api/hub/events';
  var POLL_VISIBLE_MS = 20000;   // 可见时轮询间隔
  var POLL_HIDDEN_MS = 120000;   // 后台轮询间隔（一般用不到，后台会直接停）
  var OFFLINE_AFTER = 3;         // 连续失败几次判定离线（2026-11-11: 2→3，隧道偶发抖动不该立刻报离线）
  // 2026-10-28：实测线上 /api/hub/stream-version 要 8-14s（MySQL count + 文件 stat + 共享机器），
  // 原来 8s 超时 → 连续两次就判「离线预览」，健康的后端被当成挂了。放宽到 25s。
  var FETCH_TIMEOUT_MS = 25000;
  var OFFLINE_RETRY_MS = 15000;   // 离线态下的重试间隔：不能像以前那样 120s 才回头
  var FRESH_SHOW_MS = 3200;      // "已更新"胶囊展示时长

  var state = {
    mode: 'idle',        // idle | sse | poll | offline | off
    version: '',
    lastChange: 0,
    fails: 0,
    demo: false,         // 本地预览/演示快照
    prevDb: null,        // 上一次的 MySQL 信号（算文献增量用）
    es: null,
    timer: null,
    pillTimer: null,
    started: false
  };

  // ---------- 文案（跟随 i18n 的 documentElement.lang） ----------
  var TEXT = {
    zh: {
      updated: '已更新',
      justNow: '刚刚',
      offline: '连接中断',
      demo: '演示数据',
      updatedTip: '影像流与统计已自动更新',
      offlineTip: '与服务器的实时连接中断，正在自动重连；页面内的检索与问答仍可手动重试',
      demoTip: '本地预览：数据为服务器快照（非实时），实时通道已连通',
      sseTip: '实时连接中（SSE）',
      pollTip: '实时连接中（轮询）',
      offlineNote: '实时连接中断，数值可能不是最新',
      demoNote: '演示快照（非实时）',
      liveNote: '实时',
      litNew: '文献',
      litNewTail: '· 点此刷新检索',
      litNewTip: '文献库已更新，点击刷新检索结果（不会自动覆盖你正在看的结果）'
    },
    en: {
      updated: 'Updated',
      justNow: 'just now',
      offline: 'Reconnecting',
      demo: 'Demo snapshot',
      updatedTip: 'Imagery and stats refreshed automatically',
      offlineTip: 'Live connection to the server dropped; reconnecting automatically. Search and Q&A still work on demand.',
      demoTip: 'Local preview: data is a captured snapshot (not live); live channel connected',
      sseTip: 'Live (SSE)',
      pollTip: 'Live (polling)',
      offlineNote: 'live link down - values may be stale',
      demoNote: 'demo snapshot (not live)',
      liveNote: 'live',
      litNew: 'literature',
      litNewTail: '- select to refresh',
      litNewTip: 'Library updated; select to re-run the search (results are never replaced silently)'
    }
  };

  function t() {
    var lang = (document.documentElement.getAttribute('lang') || 'en').toLowerCase();
    return lang.indexOf('zh') === 0 ? TEXT.zh : TEXT.en;
  }

  // ---------- 顶部胶囊 ----------
  function pillEl() {
    var el = document.getElementById('livePill');
    if (el) return el;
    var host = document.querySelector('.header-right') || document.querySelector('.status-bar');
    if (!host) return null;
    el = document.createElement('span');
    el.id = 'livePill';
    el.className = 'live-pill';
    el.hidden = true;
    el.innerHTML = '<i class="live-dot" aria-hidden="true"></i><span class="live-text"></span>';
    host.insertBefore(el, host.firstChild);
    return el;
  }

  function showPill(tone, text, tip, autoHideMs, onClick) {
    var el = pillEl();
    if (!el) return;
    el.hidden = false;
    el.classList.remove('is-fresh', 'is-offline', 'is-live', 'is-demo');
    if (tone) el.classList.add(tone);
    el.querySelector('.live-text').textContent = text;
    el.title = tip || '';
    el.onclick = typeof onClick === 'function' ? onClick : null;
    el.style.cursor = typeof onClick === 'function' ? 'pointer' : '';
    if (state.pillTimer) clearTimeout(state.pillTimer);
    if (autoHideMs) {
      state.pillTimer = setTimeout(function () {
        if (state.mode === 'offline' || state.demo) return;   // 离线/演示态常驻，不自动隐藏
        el.hidden = true;
      }, autoHideMs);
    }
  }

  function hidePill() {
    var el = document.getElementById('livePill');
    if (el) el.hidden = true;
  }

  /* Live Atlas 标题旁的独立标注：单独一个元素，
     因为 #homeStatsNote 会被 fillHomeStats() 反复重写，直接改它会被覆盖。 */
  function noteEl() {
    var el = document.getElementById('liveNote');
    if (el) return el;
    var anchor = document.getElementById('homeStatsNote');
    if (!anchor || !anchor.parentNode) return null;
    el = document.createElement('span');
    el.id = 'liveNote';
    el.className = 'live-note';
    anchor.parentNode.insertBefore(el, anchor.nextSibling);
    return el;
  }

  function renderNote() {
    var el = noteEl();
    if (!el) return;
    var c = t();
    var txt = '';
    var cls = '';
    if (state.mode === 'offline') { txt = c.offlineNote; cls = 'is-offline'; }
    else if (state.demo) { txt = c.demoNote; cls = 'is-demo'; }
    el.className = 'live-note' + (cls ? ' ' + cls : '');
    el.textContent = txt;
    el.hidden = !txt;
  }

  // ---------- 离线态 / 演示快照态 ----------
  function setOffline(on) {
    if (on) {
      state.mode = 'offline';
      document.body.classList.add('zf-offline');
      var c = t();
      showPill('is-offline', c.offline, c.offlineTip, 0);
    } else {
      document.body.classList.remove('zf-offline');
    }
    renderNote();
  }

  /* 本地预览 / 演示快照：数据不是实时的，必须让用户一眼看出（不能冒充实时） */
  function setDemo(on) {
    state.demo = !!on;
    if (on) {
      document.body.classList.add('zf-mock');
      var c = t();
      showPill('is-demo', c.demo, c.demoTip, 0);
    } else {
      document.body.classList.remove('zf-mock');
    }
    renderNote();
  }

  // ---------- 刷新动作（只碰会变的两块） ----------
  function refresh() {
    if (document.visibilityState !== 'visible') return;
    try {
      var R = window.ZFRefresh || {};
      if (typeof R.gallery === 'function') R.gallery();
      if (typeof R.stats === 'function') R.stats();
    } catch (e) { /* 静默：实时功能失败不影响主流程 */ }
  }

  /* 文献：**不自动刷新检索结果**（会把用户正在看的东西掀掉），
     而是提示"文献 +N"，点一下才重跑当前查询。 */
  function refreshLiterature() {
    try {
      var R = window.ZFRefresh || {};
      if (typeof R.literature === 'function') R.literature();
    } catch (e) { /* ignore */ }
  }

  function notifyLiterature(delta) {
    var c = t();
    var txt = c.litNew + ' +' + delta + ' ' + c.litNewTail;
    showPill('is-fresh', txt, c.litNewTip, 0, function () {
      refreshLiterature();
      var el = document.getElementById('livePill');
      if (el) { el.hidden = true; el.onclick = null; el.style.cursor = ''; }
    });
  }

  function applyChange(payload) {
    var ver = payload && payload.version;
    if (!ver) return;
    var db = (payload && payload.db) || {};
    var prev = state.prevDb || null;
    if (!ver || ver === state.version) { state.prevDb = db; return; }
    var first = !state.version;
    state.version = ver;
    state.lastChange = Date.now();
    state.prevDb = db;
    if (first) return;                      // 首次只记基线，不触发刷新
    var delta = 0;
    if (prev && db.literature_rows && prev.literature_rows &&
        db.literature_rows > prev.literature_rows) {
      delta = db.literature_rows - prev.literature_rows;
    }
    refresh();                              // 图片 + 统计：自动刷新
    var c = t();
    if (delta > 0) notifyLiterature(delta); // 文献：只提醒，点击才刷新
    else showPill('is-fresh', c.updated + ' · ' + c.justNow, c.updatedTip, FRESH_SHOW_MS);
  }

  // ---------- 轮询 ----------
  function schedulePoll(ms) {
    if (state.timer) clearTimeout(state.timer);
    state.timer = setTimeout(poll, ms);
  }

  function poll() {
    if (state.mode === 'off' || document.visibilityState !== 'visible') return;
    var ctl = typeof AbortController === 'function' ? new AbortController() : null;
    var to = ctl ? setTimeout(function () { ctl.abort(); }, FETCH_TIMEOUT_MS) : null;
    fetch(VERSION_URL + '?_=' + Date.now(), { credentials: 'same-origin', signal: ctl ? ctl.signal : undefined })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)); })
      .then(function (j) {
        if (!j || j.ok === false) throw new Error('bad payload');
        state.fails = 0;
        setDemo(!!j.mock);
        if (state.mode === 'offline') {
          state.mode = 'poll';
          document.body.classList.remove('zf-offline');
          var c = t();
          showPill('is-live', c.updated, c.pollTip, 2400);
        }
        applyChange(j);
        schedulePoll(POLL_VISIBLE_MS);
      })
      .catch(function () {
        state.fails += 1;
        if (state.fails >= OFFLINE_AFTER) setOffline(true);
        // 已判离线时用短间隔回探：后端一恢复就能自动摘掉「离线预览」，
        // 而不是让用户盯着一个过期状态两分钟。
        schedulePoll(state.fails >= OFFLINE_AFTER ? OFFLINE_RETRY_MS : POLL_VISIBLE_MS);
      })
      .then(function () { if (to) clearTimeout(to); });
  }

  // ---------- SSE ----------
  function stopSse() {
    if (state.es) {
      try { state.es.close(); } catch (e) { /* ignore */ }
      state.es = null;
    }
  }

  function startSse() {
    if (typeof window.EventSource !== 'function') { startPolling(); return; }
    stopSse();
    var es;
    try { es = new EventSource(EVENTS_URL); } catch (e) { startPolling(); return; }
    state.es = es;
    state.mode = 'sse';

    es.addEventListener('version', function (ev) {
      state.fails = 0;
      try {
        var j = JSON.parse(ev.data || '{}');
        applyChange(j);
      } catch (e) { /* ignore */ }
    });

    es.addEventListener('bye', function () { stopSse(); scheduleReconnect(1000); });

    es.onopen = function () {
      if (document.body.classList.contains('zf-offline')) {
        document.body.classList.remove('zf-offline');
        var c = t();
        showPill('is-live', c.updated, c.sseTip, 2400);
      }
    };

    es.onerror = function () {
      // SSE 断开：先降级轮询（不立刻重连，避免和轮询打架）
      stopSse();
      state.mode = 'poll';
      state.fails += 1;
      if (state.fails >= OFFLINE_AFTER) setOffline(true);
      poll();
    };
  }

  function scheduleReconnect(ms) {
    if (state.mode === 'off') return;
    if (state.timer) clearTimeout(state.timer);
    state.timer = setTimeout(function () {
      if (document.visibilityState === 'visible') startSse();
      else scheduleReconnect(5000);
    }, ms);
  }

  function startPolling() {
    state.mode = 'poll';
    poll();
  }

  // ---------- 可见性 ----------
  function onVisibility() {
    if (state.mode === 'off') return;
    if (document.visibilityState === 'visible') {
      if (state.mode === 'sse') { if (!state.es) startSse(); }
      else { poll(); }
    } else {
      stopSse();
      if (state.timer) { clearTimeout(state.timer); state.timer = null; }
    }
  }

  // ---------- 生命周期 ----------
  function start() {
    if (state.started) return;
    var pref;
    try { pref = localStorage.getItem('zfLive'); } catch (e) { pref = null; }
    if (pref === '0') { state.mode = 'off'; return; }
    state.started = true;
    document.addEventListener('visibilitychange', onVisibility);
    setInterval(renderNote, 3000);   // 统计重写只影响 #homeStatsNote，本标注独立存在

    // 语言切换后重绘胶囊文案
    try {
      new MutationObserver(function () {
        var el = document.getElementById('livePill');
        if (!el || el.hidden) return;
        var c = t();
        if (state.mode === 'offline') { el.querySelector('.live-text').textContent = c.offline; el.title = c.offlineTip; }
      }).observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] });
    } catch (e) { /* ignore */ }

    // 先取基线版本（顺带判活），再决定 SSE / 轮询
    var bootCtl = typeof AbortController === 'function' ? new AbortController() : null;
    var bootTo = bootCtl ? setTimeout(function () { bootCtl.abort(); }, FETCH_TIMEOUT_MS) : null;
    fetch(VERSION_URL + '?_=' + Date.now(),
          { credentials: 'same-origin', signal: bootCtl ? bootCtl.signal : undefined })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)); })
      .then(function (j) {
        if (!j || j.ok === false) throw new Error('bad payload');
        state.fails = 0;
        state.version = j.version || '';
        state.prevDb = j.db || null;
        setDemo(!!j.mock);
        startSse();
      })
      .catch(function () {
        state.fails = OFFLINE_AFTER;
        setOffline(true);
        schedulePoll(OFFLINE_RETRY_MS);   // 15s 后自己回头看，不等人刷新
      })
      .then(function () { if (bootTo) clearTimeout(bootTo); });
  }

  function stop() {
    state.mode = 'off';
    stopSse();
    if (state.timer) { clearTimeout(state.timer); state.timer = null; }
    document.removeEventListener('visibilitychange', onVisibility);
    document.body.classList.remove('zf-offline');
    hidePill();
  }

  window.ZFLive = {
    get state() { return state; },
    start: start,
    stop: stop,
    refresh: refresh,
    setOffline: setOffline,
    setDemo: setDemo,
    renderNote: renderNote,
    refreshLiterature: refreshLiterature
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(start, 1200); });
  } else {
    setTimeout(start, 1200);
  }
})();
