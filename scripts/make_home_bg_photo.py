"""用真实水下照片做首页背景（替代原来那块带磨砂面板的素材图）。

为什么要换图
------------
原素材 `斑马鱼背景图.jpeg` 中间压了一块**大面积的半透明白罩层**（实测内部 std 仅 1.9~3.8，
柔和洗白范围一直延伸到 x 228..1692 / y 244..1148）。罩层底下的画面已经被模糊压平，
无法还原；而图片本身是水彩风插画，整体对比度低 —— 这就是"背景看起来糊"的根因，
靠补画/羽化都解决不了，只能换一张**本身就是完整、清晰**的图。

选图
----
Wikimedia Commons: File:Forests Underwater (122868199).jpeg
  2048×1365, CC BY 3.0
  构图正好是：顶部水面 + 中间开阔水体 + 左右水草 + 底部沙床，
  和首页"中间放大白卡、两侧留景"的版式天然契合。

处理
----
1. cover 裁切成目标比例（默认 16:9.9，够宽屏 cover 用）。
2. 轻度降饱和 + 提亮：贴近站点"高调、淡蓝绿"的配色。
3. 顶栏/大标题压一层**很淡的白色渐变 scrim**，保证深色文字可读，
   但只作用在顶部 42%，中间水体的清晰度不受影响。
4. 四角轻微白色柔光，避免画面边缘抢视线。

⚠️ CC BY 3.0 要求署名 —— 见 web/assets/brand/CREDITS.md。

用法：
    python -X utf8 scripts/make_home_bg_photo.py
    python -X utf8 scripts/make_home_bg_photo.py --title "File:xxx.jpg" --out web/...
"""
from __future__ import annotations

import argparse
import io
import json
import os
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

API = "https://commons.wikimedia.org/w/api.php"
UA = "ZebrafishHubBgBuilder/1.0 (research; local)"
DEFAULT_TITLE = "File:Forests Underwater (122868199).jpeg"
DST = os.path.join("web", "assets", "brand", "zf-home-bg.jpeg")
CACHE = os.path.join("解析文件", "bg_candidates", "bg_source_photo.jpg")
OUT_W, OUT_H = 2560, 1584


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read()


def fetch_info(title: str) -> dict:
    params = {
        "action": "query", "format": "json", "titles": title,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": str(OUT_W),
    }
    data = json.loads(_get(API + "?" + urllib.parse.urlencode(params)))
    pages = (data.get("query") or {}).get("pages") or {}
    for _, p in pages.items():
        info = (p.get("imageinfo") or [{}])[0]
        if info:
            meta = info.get("extmetadata") or {}

            def m(k: str) -> str:
                v = meta.get(k) or {}
                return str(v.get("value") or "")

            return {
                "url": info.get("url", ""),
                "thumb": info.get("thumburl", "") or info.get("url", ""),
                "width": info.get("width", 0),
                "height": info.get("height", 0),
                "license": m("LicenseShortName"),
                "artist": m("Artist"),
                "descurl": info.get("descriptionurl", ""),
            }
    raise SystemExit(f"没找到 {title}")


def cover_crop(im: Image.Image, w: int, h: int) -> Image.Image:
    sw, sh = im.size
    scale = max(w / sw, h / sh)
    nw, nh = int(round(sw * scale)), int(round(sh * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = int((nh - h) * 0.42)          # 略偏上，保留更多水面与光束
    return im.crop((left, top, left + w, top + h))


def top_scrim(im: Image.Image, strength: float = 0.55, span: float = 0.42) -> Image.Image:
    """顶部白色渐变 scrim：让深色标题/导航在照片上依然清晰。"""
    w, h = im.size
    a = np.zeros((h, w), dtype=np.float32)
    edge = int(h * span)
    ramp = np.linspace(strength, 0.0, edge, dtype=np.float32) ** 1.25
    a[:edge, :] = ramp[:, None]
    # 四角轻微柔光
    yy = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
    xx = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    d = np.sqrt(xx * xx + yy * yy) / 1.4142
    a += np.clip((d - 0.72) / 0.28, 0, 1) * 0.16
    a = np.clip(a, 0, 1)[:, :, None]
    arr = np.asarray(im).astype(np.float32)
    white = np.full_like(arr, 255.0)
    return Image.fromarray(np.clip(arr * (1 - a) + white * a, 0, 255).astype(np.uint8))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default=DEFAULT_TITLE)
    ap.add_argument("--out", default=DST)
    ap.add_argument("--width", type=int, default=OUT_W)
    ap.add_argument("--height", type=int, default=OUT_H)
    a = ap.parse_args()

    info = fetch_info(a.title)
    print(f"[src] {info['width']}x{info['height']}  {info['license']}  {info['url']}")
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    if not os.path.exists(CACHE):
        with open(CACHE, "wb") as f:
            f.write(_get(info["url"]))
        print(f"[cache] {CACHE}")

    im = Image.open(io.BytesIO(open(CACHE, "rb").read())).convert("RGB")
    im = cover_crop(im, a.width, a.height)
    im = ImageEnhance.Color(im).enhance(0.90)          # 轻度降饱和
    im = ImageEnhance.Brightness(im).enhance(1.06)     # 提亮，贴近高调配色
    im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=90, threshold=2))
    im = top_scrim(im)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    im.save(a.out, "JPEG", quality=91, progressive=True, optimize=True)
    print(f"[out] {a.out} {im.size[0]}x{im.size[1]} {os.path.getsize(a.out)//1024} KB")


if __name__ == "__main__":
    main()
