"""把任意一张生成好的背景图接入站点：裁切 → 调色 → 覆盖 → 自动升版本号 → 推送。

配合 `docs/背景图生成提示词.md` 使用：用专业生图工具出图后，跑一条命令即可上线。

用法：
    python -X utf8 scripts/apply_home_bg.py --src "D:\\下载\\bg.png"
    python -X utf8 scripts/apply_home_bg.py --src bg.png --no-push
    python -X utf8 scripts/apply_home_bg.py --src bg.png --width 2560 --height 1584 \
        --brightness 1.04 --saturation 0.95

做了什么
--------
1. cover 裁切到目标尺寸（默认 2560×1584，16:10 附近），中心略偏上以保留顶部光束；
2. 可选：亮度/饱和/锐化微调（默认提亮 6%、降饱和 4%，贴合站点高调配色）；
3. 覆盖 `web/assets/brand/zf-home-bg.jpeg`；
4. 自动把 `web/css/premium.css` 里 `zf-home-bg.jpeg?v=` 和 `web/index.html` 里
   `premium.css?v=` 换成新时间戳 —— **这一步必须做**，否则浏览器/边缘缓存不更新；
5. 调 `scripts/push_web.py` 推到服务器并打印公网地址与健康状态。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys

from PIL import Image, ImageEnhance, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DST = os.path.join(ROOT, "web", "assets", "brand", "zf-home-bg.jpeg")
CSS = os.path.join(ROOT, "web", "css", "premium.css")
HTML = os.path.join(ROOT, "web", "index.html")


def cover_crop(im: Image.Image, w: int, h: int, v_anchor: float = 0.45) -> Image.Image:
    sw, sh = im.size
    scale = max(w / sw, h / sh)
    nw, nh = int(round(sw * scale)), int(round(sh * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = int((nh - h) * v_anchor)
    return im.crop((left, top, left + w, top + h))


def bump_versions(token: str) -> list[str]:
    """把背景图与 CSS 的 ?v= 一起换成新 token。返回被改的内容摘要。"""
    changed = []

    css = open(CSS, encoding="utf-8").read()
    new_css, n1 = re.subn(r"(zf-home-bg\.jpeg\?v=)[0-9A-Za-z]+", rf"\g<1>{token}", css)
    if n1:
        open(CSS, "w", encoding="utf-8", newline="\n").write(new_css)
        changed.append(f"premium.css: 背景图 ?v= -> {token}（{n1} 处）")

    html = open(HTML, encoding="utf-8").read()
    new_html, n2 = re.subn(r"(/static/css/premium\.css\?v=)[0-9A-Za-z]+", rf"\g<1>{token}", html)
    if n2:
        open(HTML, "w", encoding="utf-8", newline="\n").write(new_html)
        changed.append(f"index.html: premium.css ?v= -> {token}")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="生成的背景图路径")
    ap.add_argument("--width", type=int, default=2560)
    ap.add_argument("--height", type=int, default=1584)
    ap.add_argument("--brightness", type=float, default=1.06)
    ap.add_argument("--saturation", type=float, default=0.96)
    ap.add_argument("--sharp", type=float, default=90.0, help="UnsharpMask percent，0=不锐化")
    ap.add_argument("--anchor", type=float, default=0.45, help="纵向裁切锚点 0=顶部 1=底部")
    ap.add_argument("--quality", type=int, default=92)
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()

    if not os.path.exists(a.src):
        print(f"[err] 找不到源图：{a.src}")
        return 2

    im = Image.open(a.src)
    print(f"[src] {os.path.basename(a.src)} {im.size[0]}x{im.size[1]} {im.mode}")
    im = im.convert("RGB")
    if im.size[0] < a.width or im.size[1] < a.height:
        print(f"[warn] 源图小于目标尺寸 {a.width}x{a.height}，会被放大、可能变糊")

    im = cover_crop(im, a.width, a.height, a.anchor)
    if abs(a.saturation - 1.0) > 1e-3:
        im = ImageEnhance.Color(im).enhance(a.saturation)
    if abs(a.brightness - 1.0) > 1e-3:
        im = ImageEnhance.Brightness(im).enhance(a.brightness)
    if a.sharp > 0:
        im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=int(a.sharp), threshold=2))

    os.makedirs(os.path.dirname(DST), exist_ok=True)
    im.save(DST, "JPEG", quality=a.quality, progressive=True, optimize=True)
    print(f"[out] {os.path.relpath(DST, ROOT)}  {a.width}x{a.height}  {os.path.getsize(DST)//1024} KB")

    token = dt.datetime.now().strftime("%Y%m%d%H%M")
    for line in bump_versions(token):
        print(f"[ver] {line}")

    if a.no_push:
        print("[skip] 未推送。手动上线：python -X utf8 scripts/push_web.py "
              "web/assets/brand/zf-home-bg.jpeg web/css/premium.css web/index.html")
        return 0

    cmd = [sys.executable, "-X", "utf8", os.path.join(ROOT, "scripts", "push_web.py"),
           "web/assets/brand/zf-home-bg.jpeg", "web/css/premium.css", "web/index.html"]
    print("[push] " + " ".join(cmd[3:]))
    rc = subprocess.call(cmd, cwd=ROOT)
    if rc == 0:
        print("\n[fyi] 浏览器端请 Ctrl+F5 强刷；公网地址见上面输出。")
    return rc


if __name__ == "__main__":
    sys.exit(main())
