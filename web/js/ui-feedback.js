/* ==========================================================================
   ui-feedback.js —— 统一操作反馈（toast）
   --------------------------------------------------------------------------
   解决什么：清查时发现"复制全文 / 导出 Markdown"点完**没有任何可见反馈**
   （只 flash 了一下按钮文字，滚动到别处就看不见，也没有 aria-live）。
   提供一个极小的 toast：右下角、2.4s 自动消失、可堆叠、带 aria-live。
   用法：
     ZFUi.toast("已复制", "ok");      // ok | err | info（默认 info）
   注意：本文件必须在 app.js 之后加载（app.js 会在操作回调里调用它）。
   ========================================================================== */
(function () {
  'use strict';

  var HOST_ID = 'zfToastHost';
  var MAX = 3;

  function host() {
    var el = document.getElementById(HOST_ID);
    if (el) return el;
    el = document.createElement('div');
    el.id = HOST_ID;
    el.className = 'zf-toast-host';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.setAttribute('aria-atomic', 'false');
    document.body.appendChild(el);
    return el;
  }

  function toast(text, kind) {
    if (!text) return;
    var h = host();
    // 同类提示不重复刷屏，只刷新时间
    var last = h.lastElementChild;
    if (last && last.dataset.text === String(text)) {
      if (last._t) clearTimeout(last._t);
      last._t = setTimeout(function () { last.remove(); }, 2400);
      return;
    }
    var el = document.createElement('div');
    el.className = 'zf-toast' + (kind ? ' is-' + kind : '');
    el.dataset.text = String(text);
    el.textContent = String(text);
    h.appendChild(el);
    while (h.children.length > MAX) h.removeChild(h.firstElementChild);
    requestAnimationFrame(function () { el.classList.add('is-in'); });
    el._t = setTimeout(function () {
      el.classList.remove('is-in');
      setTimeout(function () { el.remove(); }, 200);
    }, 2400);
  }

  window.ZFUi = { toast: toast };
})();
