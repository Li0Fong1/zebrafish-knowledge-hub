"""生成"极简干净"的首页背景：浅蓝竖直渐变 + 顶部柔光。

用途：在拿到正式生成图之前的**过渡背景**。没有任何插画元素，不会显得"废稿"；
同时保留 `zf-home-bg.jpeg` 这个文件名，等正式图到手直接
`python -X utf8 scripts/apply_home_bg.py --src <新图>` 一条命令覆盖即可。

关键细节：8bit 大范围渐变容易出现**色带（banding）**，这里叠加了 ±1.2 的
蓝噪声抖动，肉眼看不出噪点，但色阶过渡平滑。

用法：
    python -X utf8 scripts/make_home_bg_clean.py
    python -X utf8 scripts/make_home_bg_clean.py --width 2560 --height 1584
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

DST = os.path.join("web", "assets", "brand", "zf-home-bg.jpeg")

# 站点的浅色主题色阶（与 premium.css 的 --p-bg / --p-surface 对齐）
STOPS = [
    (0.00, (252, 254, 255)),
    (0.28, (243, 250, 254)),
    (0.55, (231, 245, 253)),
    (0.78, (219, 238, 251)),
    (1.00, (206, 231, 249)),
]


def gradient(w: int, h: int) -> np.ndarray:
    ys = np.linspace(0, 1, h, dtype=np.float32)
    out = np.zeros((h, w, 3), dtype=np.float32)
    for i in range(len(STOPS) - 1):
        p0, c0 = STOPS[i]
        p1, c1 = STOPS[i + 1]
        m = (ys >= p0) & (ys <= p1)
        t = (ys[m] - p0) / max(1e-6, p1 - p0)
        for c in range(3):
            out[m, :, c] = (c0[c] + (c1[c] - c0[c]) * t)[:, None]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=2560)
    ap.add_argument("--height", type=int, default=1584)
    ap.add_argument("--dst", default=DST)
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args()
    w, h = a.width, a.height

    arr = gradient(w, h)

    # 顶部中央柔光：让标题区域更亮、更有"通透"感（纯渐变，不做插画）
    glow = Image.new("L", (w, h), 0)
    gd = ImageDraw.Draw(glow)
    rx, ry = int(w * 0.62), int(h * 1.05)
    gd.ellipse([w * 0.5 - rx, -ry * 0.72, w * 0.5 + rx, -ry * 0.72 + ry * 1.6], fill=120)
    glow = glow.filter(ImageFilter.GaussianBlur(w * 0.12))
    g = (np.asarray(glow).astype(np.float32) / 255.0)[:, :, None]
    arr = arr * (1 - g * 0.75) + 255.0 * (g * 0.75)

    # 四角极轻收边，避免大片同色显得空
    yy = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
    xx = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
    d = np.sqrt(xx * xx + yy * yy) / 1.4142
    v = np.clip((d - 0.78) / 0.22, 0, 1) * 0.045
    arr = arr * (1 - v[:, :, None]) + np.array([186, 214, 236], dtype=np.float32) * v[:, :, None]

    # 抖动：消除 8bit 渐变色带
    rng = np.random.default_rng(a.seed)
    noise = rng.normal(0.0, 1.2, (h, w, 1)).astype(np.float32)
    arr = arr + noise

    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    os.makedirs(os.path.dirname(a.dst), exist_ok=True)
    out.save(a.dst, "JPEG", quality=94, progressive=True, optimize=True)
    print(f"[out] {a.dst} {w}x{h} {os.path.getsize(a.dst)//1024} KB")


if __name__ == "__main__":
    main()
