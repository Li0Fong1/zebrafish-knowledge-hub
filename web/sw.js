/* Zebrafish Hub — 边缘弱网加速 Service Worker
 * ============================================================================
 * 为什么需要它：
 *   本站出口在洛杉矶，客户端在国内，单次回源实测 2~8 s，链路抖动时 18 KB
 *   (gzip 后) 的首页要 50 s 以上才传完。CDN 只缓存 /static/*，首页 HTML 每次
 *   仍要穿透隧道。SW 把首页与静态资源留在本地后：
 *     · 第二次打开 → 本地直出，0~20 ms，不再看链路脸色
 *     · 断网 / 隧道整体挂掉 → 仍能打开已访问过的页面（离线可读）
 *
 * 策略（宁可旧一点，也不要转圈）：
 *   navigation          → 有缓存先给缓存 + 后台刷新；无缓存才等网络
 *   /static/dist/*      → cache-first（文件名带 hash，永久可用）
 *   /static/fonts/*     → cache-first
 *   /api/*、/ws、SSE    → 完全不拦截，直连（流式响应不能被缓存）
 *   其他同源 GET        → stale-while-revalidate
 *
 * 发布新版：改 VERSION 即可，activate 时会清掉所有旧缓存。
 * 2026-11-12：批量 ID 互转扩到 18 个装配、边缘已 Purge Everything 后，
 * 浏览器 SW 仍可能把旧首页按 1 小时新鲜度直接给出 → 升版本强制丢弃旧缓存。
 * 注意：sw.js 不在 build_bundle 产物里，改它不会改变 index.html / dist 的 hash，
 * 因此不需要再 Purge CDN；浏览器对 SW 主脚本的更新检查会绕过 HTTP 缓存。
 * ==========================================================================*/
'use strict';

// 每次发版必须同步提升；旧版本会直出旧 index.html，引用可能已被清理的
// 旧 dist 文件而白屏，且 CDN Purge 管不到浏览器 SW 缓存。
const VERSION = 'v20261116a';
const CACHE = 'zfhub-' + VERSION;
const META = 'zfhub-meta-' + VERSION;

/* 首次安装只预置极小资源，绝不 addAll 大文件：弱网下 addAll 失败会让整个
   install 失败、SW 永远装不上。大资源交给运行时缓存自然填充。 */
const PRECACHE = [
  '/static/assets/brand/favicon.png?v=20261012a',
];

const HTML_MAX_AGE = 60 * 60 * 1000;   // 缓存 HTML 最多「新鲜」1 小时
const NAV_TIMEOUT = 4500;              // 无缓存时最多等 4.5 s

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await Promise.allSettled(PRECACHE.map((u) => cache.add(u)));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys.filter((k) => k !== CACHE && k !== META).map((k) => caches.delete(k))
    );
    await self.clients.claim();
  })());
});

self.addEventListener('message', (event) => {
  if (event.data === 'skip-waiting') self.skipWaiting();
});

function isBypass(url, request) {
  if (request.method !== 'GET') return true;
  if (url.origin !== self.location.origin) return true;
  const p = url.pathname;
  // 流式接口一律不碰：SW 插一手会让 SSE 卡住不出字
  if (p.startsWith('/api/') || p.startsWith('/ws')) return true;
  if (p === '/sw.js') return true;
  return false;
}

function cacheable(response) {
  if (!response) return false;
  if (response.status !== 200) return false;
  if (response.type === 'opaque') return false;
  // 带 Set-Cookie 的响应不写缓存：通行证必须每次由源站决定
  if (response.headers.get('set-cookie')) return false;
  return true;
}

async function put(cacheName, request, response) {
  try {
    if (!cacheable(response)) return;
    const cache = await caches.open(cacheName);
    await cache.put(request, response.clone());
  } catch (_) { /* 配额满 / 隐私模式，忽略 */ }
}

/* 页面导航：先给缓存（如果有），同时后台刷新 —— 用户永远不等回源 */
async function handleNavigate(event, url) {
  const req = event.request;
  const cache = await caches.open(CACHE);
  const meta = await caches.open(META);
  const cached = await cache.match('/', { ignoreSearch: true });

  // 后台刷新（不阻塞返回）
  const refresh = (async () => {
    const resp = await fetch(req, { credentials: 'same-origin', cache: 'no-cache' });
    if (cacheable(resp)) {
      await cache.put('/', resp.clone());
      await meta.put('/stamp', new Response(String(Date.now())));
    }
    return resp;
  })();

  if (cached) {
    // 1 小时内直接用；超龄则等后台刷新（最多 NAV_TIMEOUT）
    const stamp = await meta.match('/stamp');
    const ts = stamp ? parseInt(await stamp.text(), 10) || 0 : 0;
    const fresh = Date.now() - ts < HTML_MAX_AGE;
    if (fresh) {
      event.waitUntil(refresh.catch(() => {}));
      return cached;
    }
    try {
      return await Promise.race([
        refresh,
        new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), NAV_TIMEOUT)),
      ]);
    } catch (_) {
      event.waitUntil(refresh.catch(() => {}));
      return cached;
    }
  }

  // 从未访问过：只能等网络，但给个超时兜底
  try {
    return await Promise.race([
      refresh,
      new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), NAV_TIMEOUT * 4)),
    ]);
  } catch (err) {
    return offlinePage();
  }
}

function offlinePage() {
  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>网络暂时不可用 · Zebrafish Hub</title>
<style>
  html,body{height:100%;margin:0}
  body{display:flex;align-items:center;justify-content:center;background:#0b1220;
       color:#e6edf7;font:15px/1.7 -apple-system,"Segoe UI","PingFang SC",sans-serif}
  .box{max-width:420px;padding:32px 28px;text-align:center}
  h1{font-size:19px;font-weight:600;margin:0 0 10px}
  p{color:#93a4bd;margin:0 0 22px}
  button{background:#2f6fed;border:0;color:#fff;font-size:14px;padding:10px 22px;
         border-radius:8px;cursor:pointer}
  button:active{transform:translateY(1px)}
</style></head><body><div class="box">
<h1>暂时连不上服务器</h1>
<p>你的网络到本站的国际链路此刻不稳定。页面已缓存的部分仍可阅读，
   稍后刷新即可恢复。</p>
<button onclick="location.reload()">重新加载</button>
</div></body></html>`;
  return new Response(html, {
    status: 200,
    headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

async function cacheFirst(event, url) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(event.request, { ignoreSearch: false });
  if (hit) return hit;
  try {
    const resp = await fetch(event.request);
    await put(CACHE, event.request, resp);
    return resp;
  } catch (err) {
    const fallback = await cache.match(event.request, { ignoreSearch: true });
    if (fallback) return fallback;
    throw err;
  }
}

async function staleWhileRevalidate(event) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(event.request);
  const network = fetch(event.request)
    .then((resp) => { put(CACHE, event.request, resp); return resp; })
    .catch(() => null);
  if (cached) {
    event.waitUntil(network);
    return cached;
  }
  const resp = await network;
  if (resp) return resp;
  throw new Error('offline');
}

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (isBypass(url, event.request)) return;   // 交给浏览器原生处理

  if (event.request.mode === 'navigate') {
    event.respondWith(handleNavigate(event, url));
    return;
  }

  if (url.pathname.startsWith('/static/dist/') ||
      url.pathname.startsWith('/static/fonts/') ||
      url.pathname.startsWith('/static/assets/')) {
    event.respondWith(cacheFirst(event, url));
    return;
  }

  event.respondWith(staleWhileRevalidate(event));
});
