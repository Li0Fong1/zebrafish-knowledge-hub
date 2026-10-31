/* Zebrafish Hub · 输入框不再遮挡内容（M1）
   问题：composer 曾用 fixed/半透明叠在消息之上，滚到底部会压住最后一条回答。
   做法：把 #view-chat 变成 flex 纵向布局（消息区 flex:1 内部滚动，输入区正常流），
        并把实测高度写入 CSS 变量 --composer-h，供 padding 兜底。 */
(function () {
  "use strict";

  var root = document.documentElement;

  function fit() {
    var form = document.querySelector("#view-chat .input-area");
    if (!form) return;
    var h = Math.ceil(form.getBoundingClientRect().height || 0);
    if (h > 0) root.style.setProperty("--composer-h", h + "px");
  }

  function observe() {
    var view = document.getElementById("view-chat");
    var msgs = document.getElementById("messages");
    var form = document.querySelector("#view-chat .input-area");
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(fit);
      if (form) ro.observe(form);
      if (msgs) ro.observe(msgs);
    } else {
      window.addEventListener("resize", fit);
    }
    if (view && window.MutationObserver) {
      new MutationObserver(fit).observe(view, { attributes: true, attributeFilter: ["class"] });
    }
    if (form) {
      form.addEventListener("input", fit, { passive: true });
      form.addEventListener("focusin", fit);
    }
    window.addEventListener("resize", fit);
    fit();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", observe);
  else observe();

  window.ZFComposer = { fit: fit };
})();
