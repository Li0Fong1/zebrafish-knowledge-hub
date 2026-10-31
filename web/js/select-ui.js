/* ==========================================================================
   自绘下拉：把所有原生 <select> 换成我们自己的菜单（2026-10-28）
   --------------------------------------------------------------------------
   为什么要换：原生 <select> 的选项弹层是**浏览器自己画的独立窗口**，位置不在
   我们的 CSS 控制范围内。实测在 Chrome 里，控件靠近视口下方时弹层会向上翻并
   压住上一行；页面里还有 `backdrop-filter` 等合成层时，弹层甚至会飘到控件之外
   很远的地方（用户截图里「正链 / 负链」「智能问答 / 基因」都这样）。
   这类问题在 CSS 层无解，只能不用原生弹层 —— 于是：

     · 原生 select 仍留在 DOM 里，仍是**唯一的值载体**（.value / .selectedIndex /
       change 事件全部照旧），所有既有脚本一行都不用改；
     · 它被设为透明且不可点（opacity:0 + pointer-events:none + tabindex=-1），
       尺寸仍由它决定，所以布局、栅格、flex 宽度都不会变；
     · 它上面盖一颗自绘按钮显示当前选项；点开时菜单挂到 <body> 上、position:fixed，
       位置按按钮的 getBoundingClientRect 现算，因此永远不会被祖先的
       overflow/transform/backdrop-filter 影响，也不会被裁切；
     · 动态追加的 option（日期、组织列表等）用 MutationObserver 跟着更新。
   ========================================================================== */
(function () {
  "use strict";

  var SEL_ATTR = "data-zf-sel";           // 已接管标记，避免重复包一层
  var openMenu = null;                    // 当前打开的菜单控制器（同一时间只开一个）

  function textOf(sel) {
    var o = sel.options[sel.selectedIndex];
    if (!o) return "";
    return (o.textContent || "").replace(/\s+/g, " ").trim();
  }

  function enhance(sel) {
    if (!sel || sel.getAttribute(SEL_ATTR)) return;
    // 多选 / 列表式（size>1）本来就不是弹层，保持原样
    if (sel.multiple || sel.size > 1) return;
    // 2026-10-31：显式隐藏的 select（例如表型页那个只用来承载方向值的 #phenoMode）不要包。
    // 之前照包不误，包出来的 .zf-sel 按钮是可见的，于是「查询」按钮左边多出一颗空白小胶囊，
    // 用户截图里问的就是这个。只判内联 display（面板整体 .hidden 时 select 自身仍是默认值，
    // 那种情况依旧要包装，否则路由切换后下拉会没有样式）。
    if (sel.style && sel.style.display === "none") return;

    sel.setAttribute(SEL_ATTR, "1");

    var wrap = document.createElement("span");
    wrap.className = "zf-sel";
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(sel);

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "zf-sel-btn";
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    var label = sel.getAttribute("aria-label") || "";
    if (label) btn.setAttribute("aria-label", label);
    var txt = document.createElement("span");
    txt.className = "zf-sel-txt";
    var caret = document.createElement("span");
    caret.className = "zf-sel-caret";
    caret.setAttribute("aria-hidden", "true");
    btn.appendChild(txt);
    btn.appendChild(caret);
    wrap.appendChild(btn);

    sel.tabIndex = -1;                    // 键盘 Tab 别停在它上面（否则又会弹出原生弹层）

    function paint() {
      var t = textOf(sel);
      txt.textContent = t;
      btn.title = t;
      btn.disabled = !!sel.disabled;
      // 空选项（如「选择组织…」）显示成淡色提示态
      var cur = sel.options[sel.selectedIndex];
      wrap.classList.toggle("is-empty", !cur || !String(cur.value || ""));
    }
    paint();

    var mo = new MutationObserver(function () { paint(); });
    mo.observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled"] });
    sel.addEventListener("change", paint);

    /* ── 菜单 ─────────────────────────────────────────────── */
    var menu = null;
    var anchor = null;   // {top, left, width}

    function place() {
      if (!menu || !anchor) return;
      var vh = window.innerHeight || 800;
      var vw = window.innerWidth || 1200;
      var gap = 6;
      var mh = menu.offsetHeight;
      var top = anchor.bottom + gap;
      if (top + mh > vh - 8) {
        var up = anchor.top - gap - mh;
        if (up > 8) { top = up; menu.classList.add("is-up"); }
        else { top = Math.max(8, vh - 8 - mh); menu.classList.remove("is-up"); }
      } else {
        menu.classList.remove("is-up");
      }
      var left = anchor.left;
      if (left + anchor.width > vw - 8) left = Math.max(8, vw - 8 - anchor.width);
      menu.style.top = Math.round(top) + "px";
      menu.style.left = Math.round(left) + "px";
      menu.style.minWidth = Math.round(anchor.width) + "px";
    }

    function build() {
      menu = document.createElement("div");
      menu.className = "zf-sel-menu";
      menu.setAttribute("role", "listbox");

      /* 2026-10-31：支持 <optgroup> 分组。Liftover 的两个版本下拉各有 11/13 项，
         平铺成一长条很难扫；HTML 里已经按「GRCz 系列 / Zv 系列 / 其他组装」分了组，
         这里把 label 画成吸顶的小标题。其它没有 optgroup 的下拉完全不受影响。 */
      function addOption(o) {
        // 纯占位项（`<option value="" disabled>请选择…</option>`）不进菜单：
        // 它在按钮上已经以淡色提示显示，再在列表头部占一行只是噪音。
        if (!String(o.value || "") && o.disabled) return;
        var i = Array.prototype.indexOf.call(sel.options, o);
        if (i < 0) return;
        var it = document.createElement("button");
        it.type = "button";
        it.className = "zf-sel-opt";
        it.setAttribute("role", "option");
        it.textContent = (o.textContent || "").replace(/\s+/g, " ").trim();
        var on = i === sel.selectedIndex;
        it.classList.toggle("is-on", on);
        it.setAttribute("aria-selected", on ? "true" : "false");
        if (o.disabled) it.disabled = true;
        it.addEventListener("mousedown", function (e) { e.preventDefault(); });
        it.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          choose(i);
        });
        menu.appendChild(it);
      }

      Array.prototype.forEach.call(sel.children || [], function (node) {
        if (!node) return;
        if (node.tagName === "OPTGROUP") {
          var opts = Array.prototype.filter.call(node.children || [], function (c) {
            return c && c.tagName === "OPTION";
          });
          // 整组都不可用（liftover.js 的 filterTargetBuilds 会把不可达目标置灰）就连标题一起省掉
          var usable = opts.some(function (c) { return !c.disabled; });
          if (!opts.length || !usable) return;
          var groupName = (node.getAttribute("label") || "").trim();
          if (groupName) {
            var head = document.createElement("div");
            head.className = "zf-sel-group";
            head.setAttribute("aria-hidden", "true");
            head.textContent = groupName;
            menu.appendChild(head);
          }
          opts.forEach(addOption);
        } else if (node.tagName === "OPTION") {
          addOption(node);
        }
      });
      document.body.appendChild(menu);
    }

    function choose(i, keepOpen) {
      var opt = sel.options[i];
      if (!opt || opt.disabled) return;
      var changed = sel.selectedIndex !== i || sel.value !== opt.value;
      sel.selectedIndex = i;
      sel.value = opt.value;
      if (changed) sel.dispatchEvent(new Event("change", { bubbles: true }));
      paint();
      if (!keepOpen) close();
    }

    function onDocPointer(e) {
      if (menu && menu.contains(e.target)) return;
      if (wrap.contains(e.target)) return;
      close();
    }
    function onKey(e) {
      if (e.key === "Escape") { close(); btn.focus(); return; }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!menu) open();
        var i = sel.selectedIndex < 0 ? 0 : sel.selectedIndex;
        var next = e.key === "ArrowDown"
          ? Math.min(sel.options.length - 1, i + 1)
          : Math.max(0, i - 1);
        choose(next, true);   // 方向键预览时不关菜单，回车/点击才落定
        return;
      }
      if (e.key === "Enter" && menu) { e.preventDefault(); close(); }
    }
    function onScrollOrResize() { place(); }

    function open() {
      if (openMenu && openMenu !== close) openMenu();
      openMenu = close;
      var r = btn.getBoundingClientRect();
      anchor = { top: r.top, bottom: r.bottom, left: r.left, width: Math.max(120, r.width) };
      build();
      wrap.classList.add("is-open");
      btn.setAttribute("aria-expanded", "true");
      place();
      // 菜单在 body 上，用 fixed 定位 + 现算坐标，滚动时跟着走
      window.addEventListener("scroll", onScrollOrResize, true);
      window.addEventListener("resize", onScrollOrResize);
      document.addEventListener("pointerdown", onDocPointer, true);
      document.addEventListener("keydown", onKey, true);
    }

    function close() {
      if (menu) {
        menu.remove();
        menu = null;
      }
      wrap.classList.remove("is-open");
      btn.setAttribute("aria-expanded", "false");
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
      document.removeEventListener("pointerdown", onDocPointer, true);
      document.removeEventListener("keydown", onKey, true);
      if (openMenu === close) openMenu = null;
    }

    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (wrap.classList.contains("is-open")) close();
      else open();
    });

    // 外部脚本改了值（例如页头按钮组写回 select）时，paint 由 change 事件兜住；
    // 有些地方直接改 selectedIndex 不发事件，这里再用 MutationObserver 兜一层。
    try {
      new MutationObserver(paint).observe(sel, { attributes: true, attributeFilter: ["value"] });
    } catch (e) { /* 老浏览器忽略 */ }
  }

  function enhanceAll(root) {
    var scope = root || document;
    Array.prototype.forEach.call(scope.querySelectorAll("select"), enhance);
  }

  /* 路由切换 / 页面隐藏时把打开的菜单关掉：菜单挂在 body 上，
     不跟着视图切换走，否则会「飘」到下一个页面上（实测切到 #/chat 后还浮在那里）。 */
  function closeOpen() {
    if (typeof openMenu === "function") openMenu();
  }

  function boot() {
    enhanceAll(document);
    window.addEventListener("hashchange", closeOpen);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) closeOpen();
    });
    // 之后才插进 DOM 的下拉（极少）也接管
    try {
      new MutationObserver(function (muts) {
        muts.forEach(function (m) {
          Array.prototype.forEach.call(m.addedNodes || [], function (n) {
            if (!n || n.nodeType !== 1) return;
            if (n.tagName === "SELECT") enhance(n);
            else if (n.querySelectorAll) enhanceAll(n);
          });
        });
      }).observe(document.body, { childList: true, subtree: true });
    } catch (e) { /* ignore */ }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }

  window.ZFSelectUI = { enhanceAll: enhanceAll, enhance: enhance };
})();
