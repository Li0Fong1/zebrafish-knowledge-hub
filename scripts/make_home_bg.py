# -*- coding: utf-8 -*-
"""把主界面背景图中央那块「软边半透明面板」抹掉，并输出清晰版。

问题（2026-10-17 用户反馈「背景有部分还是有点模糊」）：
    原图 `斑马鱼背景图.jpeg`（1920×1184）正中央有一块很大的圆角半透明面板，
    它的**边缘是设计成柔化模糊的**（原本用来压文字）。当作整页背景铺满后，
    这块面板正好落在内容区后面 —— 看上去就是「中间一片糊」。
    再叠加页面里 `.agent-hero` 自带的 backdrop-filter，模糊感更明显。

做法：
  1) 用边缘能量图定位面板矩形（实测约 x312..1632 / y314..1063），四周各外扩一点盖住软边；
  2) 逐行用**面板左右两侧的真实水色**做线性插值填充，并保留上亮下暗的水下纵向渐变
     （比直接涂一个纯色自然，也不会引入新的模糊纹理）；
  3) 接缝羽化 14px，避免出现硬边；
  4) 全图轻量锐化（UnsharpMask）+ 1.5× LANCZOS 超采样：
     高分屏（dpr 1.5）上浏览器就不会再把图放大，观感更锐。
  5) 输出 `web/assets/brand/zf-home-bg.jpeg`。

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts\\make_home_bg.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "斑马鱼背景图.jpeg"
OUT = ROOT / "web" / "assets" / "brand" / "zf-home-bg.jpeg"

# 面板矩形（原图像素，实测值；外扩 45px 盖住柔化边缘）
PANEL = (312, 314, 1632, 1063)
PAD = 45
FEATHER = 14
UPSCALE = 1.5
JPEG_Q = 88


def detect_panel(im: Image.Image) -> tuple[int, int, int, int]:
    """用边缘能量图估计面板矩形（失败时回退到实测值）。"""
    W, H = im.size
    tw, th = W // 24, H // 24
    g = np.asarray(im.resize((tw, th), Image.LANCZOS).convert("L"), dtype=np.float32)
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:] = np.abs(np.diff(g, axis=1))
    gy[1:, :] = np.abs(np.diff(g, axis=0))
    sm = np.asarray(
        Image.fromarray(np.clip((gx + gy) * 6, 0, 255).astype(np.uint8)).filter(
            ImageFilter.BoxBlur(2)),
        dtype=np.float32,
    )
    norm = sm / (sm.max() or 1.0)
    flat = norm < 0.12
    rows = np.where(flat.mean(axis=1) > 0.45)[0]
    cols = np.where(flat.mean(axis=0) > 0.45)[0]
    if len(rows) < 4 or len(cols) < 4:
        return PANEL
    sx, sy = W / tw, H / th
    return (int(cols[0] * sx), int(rows[0] * sy),
            int((cols[-1] + 1) * sx), int((rows[-1] + 1) * sy))


def _mirror_texture(src: np.ndarray, box: tuple[int, int, int, int], depth: int = 260) -> np.ndarray:
    """取面板**上方**的水面纹理，上下镜像拼接成面板大小（接缝天然连续）。"""
    x0, y0, x1, y1 = box
    h, w = y1 - y0, x1 - x0
    top = max(0, y0 - depth)
    ref = src[top:y0, x0:x1]
    if ref.shape[0] < 24:
        ref = src[0:max(24, y0), x0:x1]
    if ref.shape[0] < 8:
        return np.zeros((h, w, 3), dtype=np.float32)
    tiles, need, flip = [], h, False
    while need > 0 and len(tiles) < 60:
        t = ref[::-1] if flip else ref
        tiles.append(t)
        need -= t.shape[0]
        flip = not flip
    tex = np.concatenate(tiles, axis=0)[:h]

    # 镜像拼接处会有一条很淡的横向接缝（用户实测能看到）→ 在每条缝附近做局部竖向柔化
    rh = ref.shape[0]
    k = 1
    while rh * k < h and k < 40:
        cy = rh * k
        lo, hi = max(0, cy - 30), min(h, cy + 30)
        if hi - lo > 4:
            patch = Image.fromarray(np.clip(tex[lo:hi], 0, 255).astype(np.uint8)).filter(
                ImageFilter.GaussianBlur(7))
            tex[lo:hi] = np.asarray(patch, dtype=np.float32)
        k += 1
    return tex.astype(np.float32)


def fill_panel(a: np.ndarray, box: tuple[int, int, int, int], feather: int) -> np.ndarray:
    """用「左右真实水色插值」定色调 + 「上方镜像水面纹理」补细节，再对接缝羽化。

    只用色带会像一块死色块（仍被当成"面板"）；只用镜像纹理又会偏色。
    两者按 45% 纹理 / 55% 色带混合，既保留水下光斑与气泡，又跟上上下水色渐变。
    """
    H, W, _ = a.shape
    x0, y0, x1, y1 = box
    x0 = max(0, x0 - PAD)
    y0 = max(0, y0 - PAD)
    x1 = min(W, x1 + PAD)
    y1 = min(H, y1 + PAD)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return a

    out = a.copy()
    src = a.astype(np.float32)

    # —— 色调：面板左右两侧真实水色逐行插值 ——
    def ref_col(x_from: int, x_to: int) -> np.ndarray:
        lo, hi = min(x_from, x_to), max(x_from, x_to)
        return src[y0:y1, max(0, lo):min(W, hi)].mean(axis=1)

    left = ref_col(max(0, x0 - 26), max(1, x0 - 6))
    right = ref_col(min(W - 1, x1 + 6), min(W, x1 + 26))
    t = np.linspace(0.0, 1.0, x1 - x0, dtype=np.float32)[None, :, None]
    band = left[:, None, :] * (1 - t) + right[:, None, :] * t

    # —— 细节：上方水面纹理镜像拼接 ——
    tex = _mirror_texture(src, (x0, y0, x1, y1))
    if tex.shape[:2] == band.shape[:2]:
        fill = band * 0.55 + tex * 0.45
    else:
        fill = band

    fill = np.asarray(
        Image.fromarray(np.clip(fill, 0, 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(1.2)),
        dtype=np.float32,
    )

    region = out[y0:y1, x0:x1].astype(np.float32)
    alpha = np.ones((y1 - y0, x1 - x0, 1), dtype=np.float32)
    f = max(1, int(feather))
    ramp_y = np.clip(np.minimum(np.arange(y1 - y0), np.arange(y1 - y0)[::-1]) / f, 0, 1)
    ramp_x = np.clip(np.minimum(np.arange(x1 - x0), np.arange(x1 - x0)[::-1]) / f, 0, 1)
    alpha *= ramp_y[:, None, None] * ramp_x[None, :, None]

    out[y0:y1, x0:x1] = np.clip(region * (1 - alpha) + fill * alpha, 0, 255).astype(np.uint8)
    return out


def main() -> int:
    if not SRC.is_file():
        print("找不到源图：%s" % SRC)
        return 1
    im = Image.open(SRC).convert("RGB")
    W, H = im.size
    box = detect_panel(im)
    print("原图 %dx%d，面板估计 %s" % (W, H, box))

    a = fill_panel(np.asarray(im), box, FEATHER)
    fixed = Image.fromarray(a)

    # 轻量锐化（0.8/110%/2 是"看不出加工痕迹但明显更实"的一组值）
    fixed = fixed.filter(ImageFilter.UnsharpMask(radius=1.4, percent=110, threshold=2))

    w2, h2 = int(round(W * UPSCALE)), int(round(H * UPSCALE))
    fixed = fixed.resize((w2, h2), Image.LANCZOS)
    fixed = fixed.filter(ImageFilter.UnsharpMask(radius=1.0, percent=70, threshold=3))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fixed.save(OUT, "JPEG", quality=JPEG_Q, optimize=True, progressive=True, subsampling=1)
    kb = OUT.stat().st_size / 1024
    print("输出 %s  (%dx%d, %.0f KB)" % (OUT, w2, h2, kb))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
