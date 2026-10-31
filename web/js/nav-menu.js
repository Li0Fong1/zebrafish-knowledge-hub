/* Zebrafish Hub · 导航下拉 + 关于弹层（M1）
   目标：顶部只留 6 项，11 个工具收进 Tools；无框架依赖，可直接删除本文件回退 */
(function () {
  "use strict";

  /* 先挂「脚本已就绪」标记（必须在任何可能抛错的代码之前）。
     premium.css 里的悬停兜底只在 html 上没有 .nav-ready 时才生效 ——
     这样正常浏览器是纯点击交互，不会出现「点击展开的菜单 + 悬停展开的菜单」双开；
     而一旦本文件加载失败/被拦截，纯 CSS 悬停仍然能打开下拉。 */
  try {
    var _h = document.documentElement;
    if (_h.classList) _h.classList.add("nav-ready");
    else _h.className += " nav-ready";
  } catch (e) {}

  /* ── 兼容层（2026-10-18）─────────────────────────────────────────────
     用户要求「至少 IE 与 Chrome 都能点开」。IE11 没有 Element.closest / NodeList.forEach /
     Element.remove / new Event()，只要其中任何一个抛错，整个点击委托就失效 → 下拉全死。
     这里全部走自带的小工具函数，缺什么补什么，任何浏览器都不会因为 API 缺失而中断。 */
  function each(list, fn) {
    for (var i = 0; i < (list ? list.length : 0); i++) fn(list[i], i);
  }
  function isMatch(el, sel) {
    var f = el.matches || el.msMatchesSelector || el.webkitMatchesSelector;
    try { return f ? f.call(el, sel) : false; } catch (e) { return false; }
  }
  function hit(node, sel) {
    var el = node && node.nodeType === 3 ? node.parentNode : node;   /* IE: 可能是文本节点 */
    if (!el) return null;
    if (el.closest) { try { return el.closest(sel); } catch (e) {} }
    while (el && el.nodeType === 1) {
      if (isMatch(el, sel)) return el;
      el = el.parentNode;
    }
    return null;
  }
  function removeEl(el) {
    if (!el || !el.parentNode) return;
    if (el.remove) el.remove(); else el.parentNode.removeChild(el);
  }
  function fireHashChange() {
    var ev;
    try { ev = new Event("hashchange"); } catch (e) {
      ev = document.createEvent("Event");
      ev.initEvent("hashchange", true, true);
    }
    document.dispatchEvent(ev);
  }

  /* 下拉父项高亮：路由 → 所属分组（2026-10-28 导航重组：Genomics 拆成 Omics，
     内部按 Genome / Gene / Phenotype 三区；Tools 只留 BLAST / GO / KEGG / sgRNA / 引物；
     Literature 改名 News。旧路由已由 app.js 的 ROUTE_ALIAS 折到新名，这里只列新名。 */
  var ROUTE_GROUP = {
    omics: ["liftover", "sequence-region", "genes", "gene", "expression",
            "phenotype-gene", "phenotype-term"],
    tools: ["blast", "go", "kegg", "sgrna", "primer"],
    more: ["about"]
  };

  function closeAll(except) {
    each(document.querySelectorAll(".nav-item.open"), function (it) {
      if (it !== except) {
        it.classList.remove("open");
        var m = it.querySelector(".nav-menu");
        if (m) unfixMenu(m);            /* 关掉时把 fixed 定位还原，避免坐标残留 */
      }
      var t = it.querySelector("[data-nav-toggle]");
      if (t) t.setAttribute("aria-expanded", it === except ? "true" : "false");
    });
  }

  /* ── 裁剪检测 + fixed 兜底 ────────────────────────────────────────────
     只要菜单的某个祖先设了 overflow: hidden/auto/scroll，绝对定位的菜单就会被裁掉
     （这正是「点了没反应」的真正原因）。此时把菜单改成 position: fixed 脱离裁剪。 */
  function clippedBy(menu) {
    var r = menu.getBoundingClientRect();
    if (!r.height) return false;
    var pad = 1;
    var el = menu.parentNode;
    while (el && el.nodeType === 1) {
      var cs = window.getComputedStyle ? window.getComputedStyle(el) : null;
      if (cs) {
        var ox = cs.overflowX, oy = cs.overflowY;
        if ((ox && ox !== "visible") || (oy && oy !== "visible")) {
          var ar = el.getBoundingClientRect();
          if (r.bottom > ar.bottom + pad || r.top < ar.top - pad ||
              r.right > ar.right + pad || r.left < ar.left - pad) return true;
        }
      }
      el = el.parentNode;
    }
    return false;
  }

  function unfixMenu(menu) {
    if (!menu || !menu.getAttribute("data-nav-fixed")) return;
    menu.removeAttribute("data-nav-fixed");
    menu.classList.remove("nav-menu-fixed");
    menu.style.position = "";
    menu.style.left = "";
    menu.style.top = "";
    menu.style.minWidth = "";
  }

  function fixMenu(item, menu) {
    var anchor = item.querySelector("[data-nav-toggle]") || item;
    var r = anchor.getBoundingClientRect();
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var w = menu.offsetWidth || 236;
    var left = r.left;
    if (left + w > vw - 8) left = Math.max(8, vw - w - 8);
    menu.setAttribute("data-nav-fixed", "1");
    menu.classList.add("nav-menu-fixed");
    menu.style.position = "fixed";
    menu.style.left = Math.round(left) + "px";
    menu.style.top = Math.round(r.bottom + 4) + "px";
    menu.style.minWidth = Math.round(w) + "px";
  }

  /* 打开后校验一次：若被祖先裁剪，就切成 fixed 重新落位 */
  function ensureVisible(item) {
    var menu = item.querySelector(".nav-menu");
    if (!menu) return;
    if (clippedBy(menu)) fixMenu(item, menu);
  }

  function toggle(item) {
    var open = item.classList.contains("open");
    closeAll(open ? null : item);
    if (!open) {
      item.classList.add("open");
      var t = item.querySelector("[data-nav-toggle]");
      if (t) t.setAttribute("aria-expanded", "true");
      ensureVisible(item);
    }
  }

  function currentRoute() {
    var raw = (location.hash || "#/home").replace(/^#\/?/, "");
    return (raw.split("/")[0] || "home");
  }

  /* 当前路由落在某个下拉里时，让父项也高亮 */
  function syncParentActive() {
    var route = currentRoute();
    each(document.querySelectorAll(".nav-item"), function (item) {
      var key = item.getAttribute("data-nav-group");
      var kids = ROUTE_GROUP[key] || [];
      var hit = kids.indexOf(route) !== -1;
      var toggleEl = item.querySelector("[data-nav-toggle]");
      if (toggleEl) toggleEl.classList.toggle("active", hit);
      if (hit) item.setAttribute("data-nav-hit", "1");
      else item.removeAttribute("data-nav-hit");
      each(item.querySelectorAll(".nav-menu a"), function (a) {
        a.classList.toggle("active", a.getAttribute("data-route") === route);
      });
    });
  }

  /* ── About：数据来源与快照（要求 4：信息从哪来） ───────────────────── */
  function aboutHtml() {
    var d = window.ZF_BADGES_DATA || {};
    var zh = !(window.HubI18n && typeof window.HubI18n.isZh === "function")
      ? ((document.documentElement.getAttribute("lang") || "en").toLowerCase().indexOf("zh") === 0)
      : window.HubI18n.isZh();
    var rows = (d.sources || []).map(function (s) {
      var link = s.url ? '<a href="' + s.url + '" target="_blank" rel="noopener">' + s.name + "</a>" : s.name;
      var used = zh ? s.used : (s.used_en || s.used);
      return "<tr><td>" + link + "</td><td>" + (s.version || "-") + "</td><td>" + (used || "-") + "</td></tr>";
    }).join("");
    var steps = (d.pipeline || []).map(function (p) {
      var step = zh ? p.step : (p.step_en || p.step);
      var detail = zh ? p.detail : (p.detail_en || p.detail);
      return "<li><b>" + step + "</b> — " + detail + "</li>";
    }).join("");
    return [
      '<button type="button" class="icon-btn zf-modal-close" data-modal-close aria-label="' + (zh ? "关闭" : "Close") + '">✕</button>',
      "<h2>About · Zebrafish Hub</h2>",
      '<p style="margin:0;color:var(--ink-2);font-size:13px">' + (zh
        ? "斑马鱼专用 AI 研究助手：本地结构化库 + 全文文献向量库 + 多路线作答，所有结论可回溯到本库表或原站链接。"
        : "A zebrafish-focused AI research assistant: local structured databases + a full-text literature vector store + multi-route answering; every conclusion traces back to a local table or the source site."
      ) + "</p>",
      "<h3>" + (zh ? "数据来源" : "Data sources") + "</h3>",
      "<table><thead><tr><th>" + (zh ? "来源" : "Source") + "</th><th>" + (zh ? "版本 / 组装" : "Version / assembly") + "</th><th>" + (zh ? "用途" : "Used for") + "</th></tr></thead><tbody>" + rows + "</tbody></table>",
      "<h3>" + (zh ? "我们怎么算的" : "How we compute") + "</h3><ol style=\"margin:0;padding-left:20px;font-size:12.5px;color:var(--ink-2);line-height:1.75\">" + steps + "</ol>",
      "<h3>" + (zh ? "快照" : "Snapshot") + "</h3>",
      '<p style="margin:0;font-size:12.5px;color:var(--ink-2)">' + (zh
        ? '本地库与索引快照日期：<span class="prov-mono">' + (d.snapshot || "-") + "</span>；模型与索引版本见上表。引用本系统时请注明快照日期。"
        : 'Local library and index snapshot date: <span class="prov-mono">' + (d.snapshot_en || d.snapshot || "-") + "</span>. Model and index versions are in the table above; please cite the snapshot date."
      ) + "</p>"
    ].join("");
  }

  function openAbout() {
    if (document.querySelector(".zf-modal-mask")) return;
    var mask = document.createElement("div");
    mask.className = "zf-modal-mask";
    mask.setAttribute("role", "dialog");
    mask.setAttribute("aria-modal", "true");
    var box = document.createElement("div");
    box.className = "zf-modal";
    box.innerHTML = aboutHtml();
    mask.appendChild(box);
    mask.addEventListener("click", function (e) {
      var x = e.target || e.srcElement;
      if (x === mask || hit(x, "[data-modal-close]")) removeEl(mask);
    });
    document.body.appendChild(mask);
  }

  document.addEventListener("click", function (e) {
    var target = e.target || e.srcElement;
    if (!target || target.nodeType === 3) target = target && target.parentNode;
    /* Agent 下拉项：预选问答模式后再跳转 */
    var modeLink = hit(target, "[data-nav-mode]");
    if (modeLink) {
      var mode = modeLink.getAttribute("data-nav-mode");
      closeAll(null);
      var btn = document.querySelector('.mode-btn[data-mode="' + mode + '"]');
      if (btn) btn.click();
      if (location.hash !== "#/chat") location.hash = "#/chat";
      return;
    }
    var t = hit(target, "[data-nav-toggle]");
    if (t) {
      e.preventDefault();
      var item = hit(t, ".nav-item");
      /* 菜单已经展开时再点一次父级：直接进该分组的主页面。
         否则用户点「基因组 / 表达 / 表型」这类父级只会看到菜单开合，像"点了没反应"。 */
      var primary = t.getAttribute("data-nav-primary");
      if (item && item.classList.contains("open") && primary) {
        closeAll(null);
        if (location.hash === primary) fireHashChange();
        else location.hash = primary;
        return;
      }
      toggle(item);
      return;
    }
    if (hit(target, "[data-nav-about]")) {
      e.preventDefault();
      closeAll(null);
      openAbout();
      return;
    }
    if (!hit(target, ".nav-item")) closeAll(null);
  }, true);
  /* ↑ 第三个参数 true = 捕获阶段监听。
     为什么（2026-10-18 用户反馈某些浏览器下拉"点不出来"）：冒泡阶段一旦有别的脚本/
     浏览器扩展对点击调用 stopPropagation，这个委托监听就永远收不到事件，下拉再也打不开。
     捕获阶段先拿到事件，就不受下游阻断了。 */

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" || e.keyCode === 27) {
      closeAll(null);
      var m = document.querySelector(".zf-modal-mask");
      if (m) removeEl(m);
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      var openMenu = document.querySelector(".nav-item.open .nav-menu");
      if (!openMenu) return;
      var links = [];
      each(openMenu.querySelectorAll("a"), function (a) { links.push(a); });
      if (!links.length) return;
      var idx = links.indexOf(document.activeElement);
      var next = e.key === "ArrowDown" ? (idx + 1) % links.length : (idx <= 0 ? links.length - 1 : idx - 1);
      links[next].focus();
      e.preventDefault();
    }
  });

  window.addEventListener("hashchange", function () {
    closeAll(null);
    syncParentActive();
  });
  /* 窗口尺寸/滚动变了，fixed 定位的菜单会飘，直接收起（比留着错位的菜单好）。
     注意只监听 window 自身的滚动，不加 capture —— 否则页面里任何子元素滚动都会误关菜单。 */
  window.addEventListener("resize", function () { closeAll(null); });
  window.addEventListener("scroll", function () { closeAll(null); });
  document.addEventListener("DOMContentLoaded", syncParentActive);
  if (document.readyState !== "loading") syncParentActive();

  /* About 弹窗文案是 JS 拼的，切语言时重建（而不是逐字翻译） */
  document.addEventListener("hub-lang-change", function () {
    var box = document.querySelector(".zf-modal");
    if (box) box.innerHTML = aboutHtml();
  });

  window.ZFNav = { openAbout: openAbout, syncParentActive: syncParentActive };
})();
