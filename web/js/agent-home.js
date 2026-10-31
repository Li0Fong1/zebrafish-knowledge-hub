/* ==========================================================================
   agent-home.js —— 主界面（6.png 版）静态文案 + 交互
   --------------------------------------------------------------------------
   职责边界（避免和 home-feed.js 抢同一块 DOM）：
     · 本文件：主视觉标题副标题、左卡标题、检索框占位与范围项、语言切换胶囊、
               右卡品牌文案与三个入口按钮、条款链接
     · home-feed.js：论文条目、候选池说明、更新记录、引用块（真数据）
   语言：跟随 i18n.js 的当前语言（document.documentElement.lang），
         切换后由 hub-lang-change 事件重绘，不依赖字典逐词替换。
   ========================================================================== */
(function () {
  'use strict';

  var TEXT = {
    zh: {
      heroSub: '面向斑马鱼研究的 AI 助手：整合多组学数据、知识图谱与智能分析。',
      cardTitle: '每日斑马鱼研究',
      searchPh: '例如 tp53、缺氧转录、ZDB-GENE-…',
      scopeChat: '智能问答', scopeGene: '基因', scopeLit: '文献',
      searchBtn: '搜索',
      brandTag: '— 斑马鱼研究 AI 助手 —',
      quickStart: '快速开始', signIn: '登录', register: '注册',
      preview: '研究预览', guide: '使用说明', terms: '使用协议', privacy: '数据来源'
    },
    en: {
      heroSub: 'An AI-powered research assistant for zebrafish, integrating multi-omics data, knowledge graphs and intelligent analysis.',
      cardTitle: 'Daily Zebrafish Research',
      searchPh: 'e.g. tp53, hypoxia transcription, ZDB-GENE-…',
      scopeChat: 'Ask AI', scopeGene: 'Genes', scopeLit: 'Literature',
      searchBtn: 'Search',
      brandTag: '— AI for Zebrafish Research —',
      quickStart: 'Quick Start', signIn: 'Sign in', register: 'Register',
      preview: 'Research Preview', guide: 'Guide', terms: 'Terms', privacy: 'Sources'
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
  function $(id) { return document.getElementById(id); }
  function setText(id, v) { var el = $(id); if (el) el.textContent = v; }

  function applyStatic() {
    setText('agentHeroSub', T('heroSub'));
    setText('agentDailyHead', T('cardTitle'));
    setText('agentBrandTag', T('brandTag'));
    setText('agentQuickStartText', T('quickStart'));
    setText('agentSignInText', T('signIn'));
    setText('agentRegisterText', T('register'));

    var inp = $('homeSearchInput');
    if (inp) {
      inp.placeholder = T('searchPh');
      inp.setAttribute('aria-label', T('searchPh'));
    }
    var sel = $('homeSearchScope');
    if (sel) {
      var opts = [['chat', T('scopeChat')], ['gene', T('scopeGene')], ['literature', T('scopeLit')]];
      opts.forEach(function (kv) {
        var o = sel.querySelector('option[value="' + kv[0] + '"]');
        if (o) o.textContent = kv[1];
      });
    }
    setText('homeSearchBtn', T('searchBtn'));

    var legal = document.querySelectorAll('.agent-legal-link');
    var legalKeys = ['guide', 'terms', 'privacy'];
    legal.forEach(function (el, i) {
      if (legalKeys[i]) el.textContent = T(legalKeys[i]);
    });

    // 语言胶囊的选中态
    var box = $('agentLangToggle');
    if (box) {
      box.querySelectorAll('button[data-lang]').forEach(function (b) {
        b.classList.toggle('active', b.getAttribute('data-lang') === curLang());
      });
    }
  }

  function bindLangToggle() {
    var box = $('agentLangToggle');
    if (!box) return;
    box.addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-lang]') : null;
      if (!b) return;
      var lang = b.getAttribute('data-lang');
      if (window.HubI18n && typeof window.HubI18n.setLang === 'function') {
        window.HubI18n.setLang(lang);
      }
      // HubI18n 会派发 hub-lang-change，统一在那里重绘
      applyStatic();
    });
  }

  function openGateForm(btnId) {
    // 复用既有欢迎弹窗与登录/注册流程（不改认证实现，只是把它叫出来）
    if (typeof window.showWelcomeGate === 'function') {
      window.showWelcomeGate();
    } else {
      var g = $('welcomeGate');
      if (g) {
        g.classList.remove('hidden');
        // 门的显隐现在由 <html class="zf-locked"> 统一驱动，兜底路径也要上锁
        document.documentElement.classList.add('zf-locked');
      }
    }
    setTimeout(function () { var b = $(btnId); if (b) b.click(); }, 30);
  }

  function bindCta() {
    var q = $('agentQuickStart');
    if (q) q.addEventListener('click', function () { location.hash = '#/chat'; });

    var s = $('agentSignIn');
    if (s) s.addEventListener('click', function () { openGateForm('showLoginBtn'); });

    var r = $('agentRegister');
    if (r) r.addEventListener('click', function () { openGateForm('showRegisterBtn'); });

    document.querySelectorAll('[data-agent-about]').forEach(function (el) {
      el.addEventListener('click', function () {
        if (window.ZFNav && typeof window.ZFNav.openAbout === 'function') window.ZFNav.openAbout();
      });
    });
  }

  function boot() {
    if (!$('agentHeroSub') && !$('agentLangToggle')) return;   // 首页结构不在，静默退出
    applyStatic();
    bindLangToggle();
    bindCta();
    document.addEventListener('hub-lang-change', applyStatic);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
