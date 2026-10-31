/* ==========================================================================
   chat-polish.js —— 问答页 P1 增强（与 app.js 解耦，只做两件小事）
   --------------------------------------------------------------------------
   1) 行内引用样式：默认**保留朴素的方括号 `[3]`**（2026-10-11 用户要求回归）。
      附带的胶囊徽标实现保留着，把下面 CITE_BADGE 改成 true 即可切回：
      · 用 TreeWalker 只改文本节点，跳过 <a>/<code>/<pre>，不碰 Markdown 链接 [x](url)
      · 天然幂等（处理过的编号已不在文本节点里），流式输出时防抖重跑
   2) 快速示例双语：英文界面下标签与**实际发送的问题**都切英文
      （否则英文界面点一下就发中文问题 → 中文回答，2026-10-11 用户反馈的问题）
   ========================================================================== */
(function () {
  'use strict';

  /* ---------------------------------------------------- 行内引用样式开关 */
  // 为什么默认关：徽标把 `[3]` 变成上标小胶囊后，编号不再显眼，用户觉得
  // "引用很少/看不清"。方括号是学术写作的通行写法，直接照旧。
  var CITE_BADGE = false;
  var CITE_RE = /\[(\d{1,3})\](?!\()/g;   // (?!\() 排除 Markdown 链接语法 [1](url)

  function badgeCitations(root) {
    if (!root || !root.querySelectorAll) return 0;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var v = node.nodeValue;
        if (!v || v.indexOf('[') < 0) return NodeFilter.FILTER_REJECT;
        var p = node.parentElement;
        if (!p) return NodeFilter.FILTER_REJECT;
        // 跳过链接/代码/已处理过的徽标
        if (p.closest('a, code, pre, sup.cite-badge, .cite-badge')) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    var texts = [], n;
    while ((n = walker.nextNode())) texts.push(n);

    var hits = 0;
    texts.forEach(function (t) {
      CITE_RE.lastIndex = 0;
      if (!CITE_RE.test(t.nodeValue)) return;
      CITE_RE.lastIndex = 0;
      var frag = document.createDocumentFragment();
      var last = 0, m;
      while ((m = CITE_RE.exec(t.nodeValue))) {
        if (m.index > last) frag.appendChild(document.createTextNode(t.nodeValue.slice(last, m.index)));
        var sup = document.createElement('sup');
        sup.className = 'cite-badge';
        sup.textContent = m[1];
        sup.title = '引用 ' + m[1] + '（见文末参考文献）';
        frag.appendChild(sup);
        last = m.index + m[0].length;
        hits += 1;
      }
      if (last < t.nodeValue.length) frag.appendChild(document.createTextNode(t.nodeValue.slice(last)));
      if (t.parentNode) t.parentNode.replaceChild(frag, t);
    });
    return hits;
  }

  /* ---------------------------------------------------- 启动 */
  function boot() {
    var log = document.getElementById('chatLog') || document.querySelector('.chat-log, .messages');
    var timer = null;
    var scan = function () {
      if (CITE_BADGE && log) badgeCitations(log);
    };
    var schedule = function () {
      if (!CITE_BADGE) return;                 // 关掉徽标时不必扫 DOM
      if (timer) clearTimeout(timer);
      timer = setTimeout(scan, 350);           // 流式输出防抖
    };

    scan();

    if (CITE_BADGE && log && window.MutationObserver) {
      new MutationObserver(schedule).observe(log, {
        childList: true, subtree: true, characterData: true,
      });
    }
    document.addEventListener('hub-lang-change', function () {
      schedule();
    });
    // 路由切到聊天页时补扫一次（消息可能是先前渲染的）
    window.addEventListener('hashchange', function () {
      if ((location.hash || '').indexOf('#/chat') === 0) schedule();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
