/* Service Worker 注册（弱网加速）。
 *
 * 单独成文件而不是内联：CSP 里 script-src 目前还留着 'unsafe-inline'，
 * 但把注脚逻辑收敛到外部脚本后，将来去掉 unsafe-inline 时这里不用再改。
 *
 * 为什么用 load 事件：SW 注册会抢占带宽，弱网下会和首屏资源抢。等页面
 * 自身加载完再注册，保证首屏优先。
 */
(function () {
  if (!('serviceWorker' in navigator)) return;
  if (location.protocol !== 'https:' && location.hostname !== 'localhost'
      && location.hostname !== '127.0.0.1') return;

  function register() {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(function (reg) {
      // 每次进来顺手查一次更新；失败无所谓，下次再说
      try { reg.update(); } catch (e) { /* noop */ }
    }).catch(function () { /* 隐私模式 / 不支持，静默降级 */ });
  }

  if (document.readyState === 'complete') {
    register();
  } else {
    window.addEventListener('load', register, { once: true });
  }
})();
