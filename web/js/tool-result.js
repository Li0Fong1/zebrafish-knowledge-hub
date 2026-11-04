/* ==========================================================================
   tool-result.js —— 结果区的「按页面隔离」层
   --------------------------------------------------------------------------
   问题（2026-11-04 用户反馈）：功能页共用一个 #toolResult 节点。从「别名」页
   切到「Liftover」时，上一页的基因列表会原样留在这页的结果位里；更糟的是
   上一页还有在飞的 fetch，回来晚了一步，就把别人的结果画到新页面上。

   做法：结果按**路由**分桶存。写入时先记到本路由名下；只有当当前路由就是
   写入方时，才真正画到节点上。切页时 swap(route) 只恢复这一页自己的结果，
   别的页一个字都带不过来。
   ========================================================================== */
(function () {
  'use strict';

  var store = Object.create(null);
  var mounted = null;          // 节点当前画的是哪个路由的结果

  function routeNow() {
    var raw = (location.hash || '#/home').replace(/^#\/?/, '');
    return (raw.split('/')[0] || 'home').toLowerCase();
  }
  function node() { return document.getElementById('toolResult'); }

  /* 写入某页的结果。route 省略时按写入发生时的路由判定。 */
  function write(html, route) {
    route = (route || routeNow()).toLowerCase();
    store[route] = html || '';
    if (routeNow() !== route) return;          // 不是当前页：只记下来，不画
    var el = node();
    if (!el) return;
    el.innerHTML = store[route];
    el.classList.toggle('hidden', !store[route]);
    mounted = route;
  }

  /* 切页：把节点换成这一页自己的结果（没有就是空的 + 隐藏）。 */
  function swap(route) {
    route = (route || routeNow()).toLowerCase();
    var el = node();
    mounted = route;
    if (!el) return;
    var html = store[route] || '';
    el.innerHTML = html;
    el.classList.toggle('hidden', !html);
    el.removeAttribute('data-lift-result');
  }

  /* 主动清掉某页的结果（重跑、报错、清空输入时用）。 */
  function clear(route) {
    route = (route || routeNow()).toLowerCase();
    delete store[route];
    if (routeNow() === route) {
      var el = node();
      if (el) { el.innerHTML = ''; el.classList.add('hidden'); }
    }
  }

  function current() { return mounted; }

  window.ZFResult = {
    write: write,
    swap: swap,
    clear: clear,
    route: routeNow,
    current: current,
    node: node
  };
})();
