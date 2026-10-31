/* ==========================================================================
   feedback.js —— 右下角固定反馈入口（对齐 PotatoOmics 的 Feedback 按钮）
   --------------------------------------------------------------------------
   闭环：前端表单（内容 + 可选联系方式）→ POST /api/hub/feedback →
   服务端追加写入 $RT/feedback/feedback.jsonl（不落数据库、不采集 IP/账号）。
   另带「复制诊断信息」：页面地址、语言、主题、UA、时间，便于用户一并发来。
   ========================================================================== */
(function () {
  'use strict';

  var TEXT = {
    zh: {
      open: '反馈', title: '反馈与问题报告',
      sub: '用得不顺手、数据有疑问、或想加功能，都可写在这里。我们会按记录逐条看。',
      ph: '描述你遇到的问题或建议（至少 4 个字）…',
      contactPh: '联系方式（可选，便于回访）',
      send: '提交', copying: '复制诊断信息', copied: '已复制', close: '关闭',
      tooShort: '内容太短，请至少写 4 个字。',
      sending: '提交中…', ok: '已收到，谢谢！记录已写入服务端。',
      fail: '提交失败：{msg}', rate: '提交过于频繁，请稍后再试。'
    },
    en: {
      open: 'Feedback', title: 'Feedback & bug reports',
      sub: 'Tell us what feels off, what looks wrong in the data, or what feature you miss — every entry is recorded.',
      ph: 'Describe the problem or suggestion (at least 4 characters)…',
      contactPh: 'Contact (optional, for follow-up)',
      send: 'Send', copying: 'Copy diagnostics', copied: 'Copied', close: 'Close',
      tooShort: 'Too short — please write at least 4 characters.',
      sending: 'Sending…', ok: 'Received — thank you. Logged on the server.',
      fail: 'Failed: {msg}', rate: 'Too many submissions — please retry in a minute.'
    }
  };

  function lang() {
    var l = (document.documentElement.getAttribute('lang') || 'en').toLowerCase();
    return l.indexOf('zh') === 0 ? 'zh' : 'en';
  }
  function T(k) { var d = TEXT[lang()] || TEXT.en; return d[k] != null ? d[k] : (TEXT.en[k] || ''); }
  function $(id) { return document.getElementById(id); }
  function fmt(s, o) { return String(s).replace(/\{(\w+)\}/g, function (m, k) { return o[k] != null ? o[k] : m; }); }

  var panel, fab, state;

  function diagnostics() {
    var parts = [
      'URL: ' + location.href,
      'Lang: ' + (document.documentElement.getAttribute('lang') || 'en'),
      'Theme: ' + (document.body.classList.contains('dark') ? 'dark' : 'light'),
      'UA: ' + navigator.userAgent,
      'At: ' + new Date().toISOString()
    ];
    return parts.join('\n');
  }

  function setState(text, cls) {
    if (!state) return;
    state.textContent = text || '';
    state.className = 'fp-state' + (cls ? ' ' + cls : '');
  }

  function open() {
    panel.hidden = false;
    fab.setAttribute('aria-expanded', 'true');
    var ta = $('feedbackMessage');
    if (ta) ta.focus();
  }
  function close() {
    panel.hidden = true;
    fab.setAttribute('aria-expanded', 'false');
  }
  function toggle() { panel.hidden ? open() : close(); }

  function send() {
    var ta = $('feedbackMessage');
    var msg = ta ? ta.value.trim() : '';
    if (msg.length < 4) { setState(T('tooShort'), 'bad'); if (ta) ta.focus(); return; }
    var btn = $('feedbackSend');
    if (btn) btn.disabled = true;
    setState(T('sending'));
    fetch('/api/hub/feedback', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: msg,
        contact: ($('feedbackContact') || {}).value || '',
        page: location.href
      })
    }).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (d) {
        if (!r.ok) {
          throw new Error(r.status === 429 ? T('rate') : ((d && (d.detail || d.error)) || ('HTTP ' + r.status)));
        }
        return d;
      });
    }).then(function () {
      setState(T('ok'), 'ok');
      if (ta) ta.value = '';
      var c = $('feedbackContact');
      if (c) c.value = '';
      setTimeout(close, 1800);
    }).catch(function (e) {
      setState(fmt(T('fail'), { msg: String(e.message || e).slice(0, 120) }), 'bad');
    }).then(function () {
      if (btn) btn.disabled = false;
    });
  }

  function copyDiag() {
    var txt = diagnostics();
    var done = function () { setState(T('copied'), 'ok'); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(done, function () { setState(txt.slice(0, 80), ''); });
    } else {
      setState(txt.slice(0, 120), '');
    }
  }

  function boot() {
    fab = $('feedbackFab');
    panel = $('feedbackPanel');
    state = $('feedbackState');
    if (!fab || !panel) return;

    fab.addEventListener('click', toggle);
    var b = $('feedbackSend');
    if (b) b.addEventListener('click', send);
    var c = $('feedbackCopy');
    if (c) c.addEventListener('click', copyDiag);
    var x = $('feedbackClose');
    if (x) x.addEventListener('click', close);
    var ta = $('feedbackMessage');
    if (ta) {
      ta.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
      });
    }
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !panel.hidden) close();
    });
    // 文案随语言刷新
    var sync = function () {
      fab.textContent = T('open');
      var t = $('feedbackTitle'); if (t) t.textContent = T('title');
      var s = $('feedbackSub'); if (s) s.textContent = T('sub');
      var m = $('feedbackMessage'); if (m) m.placeholder = T('ph');
      var ct = $('feedbackContact'); if (ct) ct.placeholder = T('contactPh');
      var sv = $('feedbackSend'); if (sv) sv.textContent = T('send');
      var cp = $('feedbackCopy'); if (cp) cp.textContent = T('copying');
      var cl = $('feedbackClose'); if (cl) cl.textContent = T('close');
    };
    sync();
    document.addEventListener('hub-lang-change', sync);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
