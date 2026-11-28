#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把首页的静态资源打包，减少跨境请求数（2026-11-12）。

## 为什么要做

公网链路是「客户端 → Cloudflare 边缘(PDX/LAX) → 隧道 → 源站(国内)」，一次
请求要跨两趟太平洋。实测单次回源 TTFB 2.6~7 s，大响应常常传不完就断。而首页
原本引用了 17 个 CSS + 24 个 JS，共 46 个 /static 请求 —— 冷启动时等于把这条
烂链路走 46 遍，页面必然「转圈很久 / 打不开」。

打包后只剩 1 个 CSS + 1 个 JS，请求数 46 → 2。

## 做法

· 源文件是 `web/index.src.html`（改页面就改它），本脚本产出 `web/index.html`；
· CSS 按 <link> 出现顺序原样拼接（层叠顺序不能变）；
· JS 按 <script> 出现顺序原样拼接（都是普通脚本，无 ESM / 无 use strict / 无
  currentScript，所以直接拼接语义等价）；`i18n.js` 原本在 <head> 里，挪到
  bundle 最前面，保证它比其它脚本先执行；
· 产物文件名带内容哈希，因此可以 immutable 长缓存，改内容必然换名字，不会再
  出现「改了前端用户还拿旧缓存」这一类问题。

用法：
    python scripts/build_bundle.py            # 生成
    python scripts/build_bundle.py --check    # 只报告，不写文件
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(BASE, "web")
SRC = os.path.join(WEB, "index.src.html")
OUT = os.path.join(WEB, "index.html")
DIST = os.path.join(WEB, "dist")   # URL /static/dist/* → web/dist/*

LINK_RE = re.compile(r'[ \t]*<link[^>]+rel="stylesheet"[^>]+href="(/static/css/[^"]+)"[^>]*>\s*\n')
SCRIPT_RE = re.compile(r'[ \t]*<script[^>]+src="(/static/js/[^"]+)"[^>]*></script>\s*\n')

CSS_HEADER = """/*! 打包产物，勿手改；源: web/css/*.css，构建: scripts/build_bundle.py */
"""


def minify_css(text: str) -> str:
    """保守压缩 CSS：去注释 + 收敛空白 + 去掉标点两侧空格，字符串内容原样保留。

    线上 CSS 有 493 KB，其中大量是历年加的中文注释；实测只去注释就能让
    gzip 从 107 KB 掉到 65 KB，弱网链路上这是实打实的几秒钟。
    """
    out = []
    i, n = 0, len(text)
    quote = ""
    pending = False
    for_punct = "{};,"
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            if pending and out and out[-1] not in for_punct:
                out.append(" ")
            pending = False
            quote = ch
            out.append(ch)
            i += 1
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            pending = True
            continue
        if ch.isspace():
            pending = True
            i += 1
            continue
        if pending:
            if ch not in for_punct and out and out[-1] not in for_punct:
                out.append(" ")
            pending = False
        out.append(ch)
        i += 1
    return "".join(out)


def minify_js(text: str) -> str:
    """保守压缩 JS：只删「整行注释」和空行，绝不碰行内代码。

    行内 `//` 可能出现在 URL、正则、字符串里，正则化删注释风险太大，收益也
    有限；整行注释在本项目里占绝大多数，删掉即可。
    """
    out = []
    in_block = False
    for ln in text.splitlines():
        t = ln.strip()
        if in_block:
            if "*/" in t:
                in_block = False
            continue
        if not t:
            continue
        if t.startswith("/*"):
            if "*/" not in t:
                in_block = True
            continue
        if t.startswith("//"):
            continue
        out.append(ln.rstrip())
    return "\n".join(out)


def read(path: str) -> str:
    with io.open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def strip_ver(url: str) -> str:
    return url.split("?", 1)[0]


def local_path(url: str) -> str:
    """URL → 磁盘路径。静态目录挂的是 /static → web/，所以 /static/css/x.css
    实际落在 web/css/x.css。"""
    p = strip_ver(url)
    if p.startswith("/static/"):
        p = p[len("/static/"):]
    return os.path.join(WEB, p.replace("/", os.sep))


def digest(data: bytes, n: int = 8) -> str:
    return hashlib.sha1(data).hexdigest()[:n]


def main() -> int:
    check_only = "--check" in sys.argv

    if not os.path.isfile(SRC):
        # 第一次运行：把当前 index.html 存成源文件
        html = read(OUT)
        if not check_only:
            with io.open(SRC, "w", encoding="utf-8", newline="\n") as f:
                f.write(html)
        print("[init] 已创建 %s" % os.path.relpath(SRC, BASE))
    else:
        html = read(SRC)

    css_urls = LINK_RE.findall(html)
    js_urls = SCRIPT_RE.findall(html)

    # 幂等：已经打包过的源文件不再重复处理
    if any("/static/dist/" in u for u in css_urls + js_urls):
        print("[skip] index.src.html 里已经是打包后的引用")
        return 0

    missing = [u for u in css_urls + js_urls if not os.path.isfile(local_path(u))]
    if missing:
        print("[err] 文件不存在：")
        for m in missing:
            print("   ", m)
        return 2

    # ── CSS：先放自托管字体，再按原顺序拼接 ────────────────────────────
    parts_css = [CSS_HEADER]
    font_css = os.path.join(WEB, "css", "fonts-local.css")
    if os.path.isfile(font_css):
        parts_css.append("/* ==== fonts-local.css ==== */\n" + read(font_css))
    for u in css_urls:
        body = read(local_path(u))
        body = re.sub(r"@charset[^;]+;\s*", "", body)
        parts_css.append("/* ==== %s ==== */\n%s\n" % (os.path.basename(strip_ver(u)), body))
    css_text = "".join(parts_css)
    if "--no-minify" not in sys.argv:
        css_text = minify_css(css_text)
    css_blob = css_text.encode("utf-8")

    # ── JS：i18n 提到最前，其余按原顺序；按 gzip 体积切成 N 块 ──────────
    # 为什么要切：隧道单次回源 200 KB 以上基本传不完（实测 236 KB 的整包
    # 下到一半就断）。切成每块 ~60 KB（gzip 后）既能让浏览器并行拉，又不会
    # 撞上「大到传不完」的阈值。
    # 默认必须是 3：隧道单次回源 200 KB 以上基本传不完，单块 ~236 KB
    # 会重现「首页/JS 下到一半断掉」的故障。漏传 --chunks 不能再退化成 1 块。
    want_chunks = 3
    for i, a in enumerate(sys.argv):
        if a == "--chunks" and i + 1 < len(sys.argv):
            want_chunks = max(1, int(sys.argv[i + 1]))
    head_js = [u for u in js_urls if "/i18n.js" in u]
    tail_js = [u for u in js_urls if u not in head_js]
    ordered = head_js + tail_js

    def gz_len(b: bytes) -> int:
        import gzip as _gzip
        return len(_gzip.compress(b, 6))

    bodies = []
    for u in ordered:
        bodies.append((u, read(local_path(u)).lstrip("\ufeff")))
    sizes = [gz_len(b.encode("utf-8")) for _, b in bodies]
    total = sum(sizes) or 1
    target = total / float(want_chunks)

    # 贪心：装满就封箱；单个文件比目标还大时独占一箱（app.js 就是这样）
    buckets, cur, cur_sz = [], [], 0
    for (u, b), sz in zip(bodies, sizes):
        if cur and cur_sz + sz > target and len(buckets) < want_chunks - 1:
            buckets.append(cur)
            cur, cur_sz = [], 0
        cur.append((u, b))
        cur_sz += sz
    if cur:
        buckets.append(cur)

    js_chunks = []
    for idx, bucket in enumerate(buckets, 1):
        parts = ["/*! 打包产物，勿手改；构建: scripts/build_bundle.py */\n"]
        for u, b in bucket:
            parts.append("\n/* ==== %s ==== */\n%s\n" % (os.path.basename(strip_ver(u)), b))
        chunk_text = "".join(parts)
        if "--no-minify" not in sys.argv:
            chunk_text = minify_js(chunk_text)
        blob = chunk_text.encode("utf-8")
        js_chunks.append(("app.%s.c%d.js" % (digest(blob), idx), blob))

    css_name = "app.%s.css" % digest(css_blob)
    css_url = "/static/dist/" + css_name
    js_urls_out = ["/static/dist/" + n for n, _ in js_chunks]

    print("CSS  %2d 个 → %s (%.1f KB raw)" % (
        len(css_urls) + (1 if os.path.isfile(font_css) else 0), css_name, len(css_blob) / 1024.0))
    for (n, b), bucket in zip(js_chunks, buckets):
        print("JS   %2d 个 → %s (%.1f KB raw, 约 %.0f KB gzip)" % (
            len(bucket), n, len(b) / 1024.0, gz_len(b) / 1024.0))

    if check_only:
        return 0

    if not os.path.isdir(DIST):
        os.makedirs(DIST)
    # 产物写入推迟到 HTML 改写与校验全部通过之后。
    # 旧实现先删旧产物再校验 HTML，一旦 </body> 校验失败就 return，
    # 会留下「index.html 仍引用旧代、旧代却已被删」的白屏状态。

    # ── 改写 HTML ──────────────────────────────────────────────────────
    out = html

    def css_repl(m: re.Match) -> str:
        return '__CSS_PLACEHOLDER__\n'

    # 逐个删掉原来的 link，只在第一个位置留下占位
    first = [True]

    def _kill_link(m: re.Match) -> str:
        if first[0]:
            first[0] = False
            return '  <link rel="stylesheet" href="%s" />\n' % css_url
        return ""

    # 去掉 Google Fonts 的 preconnect 与外部样式（国内 TLS 直接失败，且阻塞首绘）
    out = re.sub(r'[ \t]*<link[^>]+href="https://fonts\.(googleapis|gstatic)\.com[^"]*"[^>]*>\s*\n', "", out)
    out = LINK_RE.sub(_kill_link, out)

    # JS 全部删掉，只在 </body> 前放打包脚本：放在 <head> 会阻塞解析，
    # 且此时 DOM 还没建好（原来 24 个脚本都在 </body> 前，位置必须保持）。
    # defer：解析完 DOM 再按顺序执行，且多个分块可以并行下载。
    out = SCRIPT_RE.sub("", out)
    if out.count("</body>") != 1:
        print("[err] </body> 出现 %d 次，无法安全插入脚本" % out.count("</body>"))
        return 4
    tags = "".join('  <script defer src="%s"></script>\n' % u for u in js_urls_out)
    out = out.replace("</body>", tags + "</body>")
    out = out.replace("__CSS_PLACEHOLDER__\n", "")

    if css_url not in out or js_urls_out[0] not in out:
        print("[err] 改写失败：没有插入占位")
        return 3

    # ── 校验通过后才动产物盘 ──────────────────────────────────────────
    # 当前线上 index.html 引用的文件必须保留：Service Worker 会让用户
    # 在发版后仍拿着旧 HTML 副本（最多 HTML_MAX_AGE），删掉旧代即 404 白屏。
    def _protected_refs() -> set:
        try:
            with io.open(OUT, "r", encoding="utf-8") as fh:
                cur = fh.read()
        except OSError:
            return set()
        return set(re.findall(r"app\.[a-f0-9]+\.(?:c\d+\.js|css)", cur))

    protected = _protected_refs()
    old = [
        fn for fn in os.listdir(DIST)
        if fn.startswith("app.") and (fn.endswith(".css") or fn.endswith(".js"))
        and fn not in protected
    ]
    old.sort(key=lambda f: os.path.getmtime(os.path.join(DIST, f)), reverse=True)
    # 每代 = want_chunks 个 JS 分块 + 1 个 CSS；保留 8 代，且按代数而非
    # 固定文件数，避免单块/多块切换时把同一代拆到阈值两侧。
    keep_files = max(32, want_chunks * 8 + 8)
    for fn in old[keep_files:]:
        os.remove(os.path.join(DIST, fn))
    with open(os.path.join(DIST, css_name), "wb") as f:
        f.write(css_blob)
    for n, b in js_chunks:
        with open(os.path.join(DIST, n), "wb") as f:
            f.write(b)

    with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)

    print("HTML 重写完成 → %s (%d → %d 字节)" % (
        os.path.relpath(OUT, BASE), len(html.encode("utf-8")), len(out.encode("utf-8"))))
    print("请求数 %d → 2 (+ favicon/touch-icon)" % (len(css_urls) + len(js_urls)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
