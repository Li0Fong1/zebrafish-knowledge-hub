"""首页背景重建 v3：把素材图中间那块「磨砂白罩层」换成真实的水体。

为什么不能用 v2 的思路
----------------------
v2 用矩形蒙版 + 镜像水草，结果：
  · 矩形边界太硬 → 视觉上仍是一块"磨砂边框"
  · 镜像水草 → 出现"左半边画面被倒影进中间"的鬼影
v3 的两个关键改变：
  ① **椭圆柔化蒙版**：没有一点直边，中心 100% 换成水体，向外平滑过渡到原图；
  ② **低频环境色外推**：把原图"有效像素"做归一化卷积外推（normalized convolution），
     得到一张没有面板痕迹的低频色场，再把填充区的中低频颜色拉过去
     —— 这样上下左右的水色天然连续，不会有一块"亮一点/冷一点"的矩形。

面板真实范围
------------
内部完全平光区：x 312..1632, y 314..1063（std 1.9~3.8，画面已不可还原）
柔和洗白延伸到：x 228..1692, y 244..1148（到这里 std 才回到 20+）
→ 填充必须覆盖到软边界，否则会留一圈平光带。

用法：
    python -X utf8 scripts/make_home_bg_v3.py
    python -X utf8 scripts/make_home_bg_v3.py --preview
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.ndimage import gaussian_filter, gaussian_filter1d

SRC = "斑马鱼背景图.jpeg"
DST = os.path.join("web", "assets", "brand", "zf-home-bg.jpeg")
PREVIEW = os.path.join("解析文件", "bg_candidates", "bg_v3_preview.jpg")

# 面板柔和边界（实测；见模块 docstring）
PANEL_SOFT = (228, 244, 1692, 1148)


def vstretch(strip: np.ndarray, out_h: int) -> np.ndarray:
    im = Image.fromarray(np.clip(strip, 0, 255).astype(np.uint8))
    return np.asarray(im.resize((im.width, out_h), Image.BICUBIC)).astype(np.float32)


def value_noise(shape, scale: float, rng) -> np.ndarray:
    hh = max(2, int(shape[0] / scale) + 2)
    ww = max(2, int(shape[1] / scale) + 2)
    small = (rng.random((hh, ww)) * 255).astype(np.uint8)
    up = Image.fromarray(small).resize((shape[1], shape[0]), Image.BICUBIC)
    return np.asarray(up).astype(np.float32) / 255.0


def caustics(shape, rng) -> np.ndarray:
    f = (0.55 * value_noise(shape, 46, rng)
         + 0.28 * value_noise(shape, 19, rng)
         + 0.17 * value_noise(shape, 8, rng))
    f = gaussian_filter(f, 1.0)
    f -= f.mean()
    f /= (f.std() + 1e-6)
    return np.clip(f, -2.5, 2.5)


def light_shafts(h: int, w: int, rng) -> np.ndarray:
    prof = np.zeros(w, dtype=np.float32)
    for _ in range(7):
        cx = rng.uniform(0, w)
        sd = rng.uniform(w * 0.035, w * 0.10)
        prof += rng.uniform(0.5, 1.0) * np.exp(-((np.arange(w) - cx) ** 2) / (2 * sd ** 2))
    prof = gaussian_filter1d(prof, w * 0.006)
    prof /= (prof.max() + 1e-6)
    fade = np.linspace(1.0, 0.08, h) ** 1.2
    out = np.empty((h, w), dtype=np.float32)
    base = np.arange(w, dtype=np.float32)
    tilt = rng.uniform(-0.08, 0.08)
    for y in range(h):
        out[y] = np.interp(base + tilt * y, base, prof, left=0.0, right=0.0) * fade[y]
    return out


def normalized_extrapolate(img: np.ndarray, valid: np.ndarray, sigma: float) -> np.ndarray:
    """只拿有效像素做归一化卷积（高斯）外推，得到一张平滑的低频色场。

    valid: (h,w) 0/1，1 = 真实像素可用

    ⚠️ 必须在**降采样网格**上算：直接对 1920×1184 用 σ=300 会生成 2400px 的高斯核，
    又慢又吃内存（实测卡死）。这里先缩到 1/16，σ 同比例缩放，算完再双三次放大 ——
    我们要的本来就是低频色场，粗网格完全够用。
    """
    H, W = img.shape[:2]
    ds = 16
    sh, sw = max(8, H // ds), max(8, W // ds)

    def down(a: np.ndarray, ch: int) -> np.ndarray:
        out = np.empty((sh, sw, ch), dtype=np.float32)
        for c in range(ch):
            m = Image.fromarray(np.clip(a[:, :, c], 0, 255).astype(np.uint8))
            out[:, :, c] = np.asarray(m.resize((sw, sh), Image.BICUBIC)).astype(np.float32)
        return out

    def up(a: np.ndarray) -> np.ndarray:
        out = np.empty((H, W, a.shape[2]), dtype=np.float32)
        for c in range(a.shape[2]):
            m = Image.fromarray(a[:, :, c].astype(np.float32), mode="F")
            out[:, :, c] = np.asarray(m.resize((W, H), Image.BICUBIC)).astype(np.float32)
        return out

    im_s = down(img, 3)
    v_s = np.asarray(Image.fromarray((valid * 255).astype(np.uint8))
                     .resize((sw, sh), Image.BICUBIC)).astype(np.float32) / 255.0

    sig_s = max(2.0, sigma / ds)
    den = gaussian_filter(v_s, sig_s)
    den = np.maximum(den, 0.02)
    out_s = np.stack([gaussian_filter(im_s[:, :, c] * v_s, sig_s) / den for c in range(3)], axis=2)
    return up(out_s)


def ellipse_mask(h: int, w: int, cx: float, cy: float, rx: float, ry: float,
                 inner: float = 0.80) -> np.ndarray:
    """椭圆蒙版：d<inner 处 alpha=1，到 d=1 平滑降到 0（没有任何直边）。"""
    yy = (np.arange(h, dtype=np.float32)[:, None] - cy) / ry
    xx = (np.arange(w, dtype=np.float32)[None, :] - cx) / rx
    d = np.sqrt(yy * yy + xx * xx)
    t = np.clip((d - inner) / max(1e-6, 1.0 - inner), 0.0, 1.0)
    a = 0.5 - 0.5 * np.cos(np.pi * t)          # smoothstep（余弦）
    a[d <= inner] = 1.0
    a[d >= 1.0] = 0.0
    return a[:, :, None].astype(np.float32)


def add_bubbles(rgb, box, rng):
    y0, y1, x0, x1 = box
    h, w = y1 - y0, x1 - x0
    ss = 2
    canvas = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    for _ in range(44):
        cx, cy = rng.uniform(0.03, 0.97) * w * ss, rng.uniform(0.03, 0.97) * h * ss
        r = rng.uniform(1.6, 5.0) * ss
        a = int(rng.uniform(24, 70))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, a), width=max(1, ss))
        d.ellipse([cx - r * .45, cy - r * .55, cx + r * .05, cy - r * .12],
                  fill=(255, 255, 255, min(140, a + 55)))
    canvas = canvas.resize((w, h), Image.LANCZOS)
    lay = np.asarray(canvas).astype(np.float32)
    al = lay[:, :, 3:4] / 255.0
    rgb[y0:y1, x0:x1] = rgb[y0:y1, x0:x1] * (1 - al) + lay[:, :, :3] * al
    return rgb


def build(src: str, dst: str, preview: bool) -> None:
    orig = Image.open(src).convert("RGB")
    img = np.asarray(orig).astype(np.float32)
    h, w = img.shape[:2]
    x0, y0, x1, y1 = PANEL_SOFT
    pw, ph = x1 - x0, y1 - y0
    print(f"[panel] 软边界 x={x0}..{x1} y={y0}..{y1} ({pw}x{ph})")

    rng = np.random.default_rng(20261022)

    # ---------- ① 有效像素掩码 + 低频环境色外推 ----------
    #    ⚠️ σ 必须够大：面板洞有 1464x904，中心离最近的有效像素 ~450px。
    #    σ=120 时归一化卷积的分母在中心几乎为 0（e^-17），除法发散 → 中间出现一块黑块。
    valid = np.ones((h, w), dtype=np.float32)
    valid[y0:y1, x0:x1] = 0.0
    ambient = normalized_extrapolate(img, valid, sigma=300)
    den_probe = gaussian_filter(valid, 300)
    print(f"[ambient] 分母最小值={den_probe.min():.4f}（>0.05 才安全）")

    # ---------- ② 水柱：面板上方横带纵向拉伸 ----------
    top_band = img[max(0, y0 - 280):y0 + 6, :, :]
    column = vstretch(top_band, ph)[:, x0:x1, :]

    # ---------- ③ 焦散 + 光束 ----------
    column = column * (1.0 + 0.032 * caustics((ph, pw), rng)[:, :, None])
    column = column * (1.0 + 0.060 * light_shafts(ph, pw, rng)[:, :, None])

    fill = img.copy()
    fill[y0:y1, x0:x1] = column

    # ---------- ④ 水底：面板下缘外的沙石带镜像向上 ----------
    floor_h = min(64, h - y1)
    if floor_h > 8:
        floor = img[y1:y1 + floor_h, x0:x1][::-1, :]
        rows = np.arange(ph)
        band = floor[np.clip(np.abs(rows - (ph - floor_h)), 0, floor_h - 1)]
        ramp = np.clip(1.0 - (ph - rows) / float(floor_h), 0.0, 1.0) ** 1.5
        a = (ramp[:, None, None] * 0.80)
        fill[y0:y1, x0:x1] = fill[y0:y1, x0:x1] * (1 - a) + band * a

    # ---------- ⑤ 气泡 ----------
    fill = add_bubbles(fill, (y0, y1, x0, x1), rng)

    # ---------- ⑥ 低频颜色对齐：把填充区的中低频色拉向环境色 ----------
    #    只动低频（σ=90），高频水纹/气泡原样保留 → 不会糊。
    low_fill = np.stack([gaussian_filter(fill[y0:y1, x0:x1, c], 90) for c in range(3)], axis=2)
    amb = ambient[y0:y1, x0:x1]
    delta = np.clip((amb - low_fill) * 0.75, -28, 28)
    fill[y0:y1, x0:x1] += delta
    print(f"[color] 低频对齐量 mean={delta.mean(axis=(0,1)).round(2)} "
          f"absmax={np.abs(delta).max():.1f}")

    # ---------- ⑦ 椭圆柔化合成 ----------
    #    ⚠️ alpha 必须在软边界**之内**就已经是 1，否则那圈"洗白"像素会保留一部分，
    #    看上去就是一圈淡蓝色磨砂边框。这里椭圆比软边界各大 70px，inner=0.93：
    #    软边界处 d≈0.91 → alpha=1（完全替换），只在软边界外 70px 处才开始渐隐。
    a = ellipse_mask(h, w, cx=(x0 + x1) / 2, cy=(y0 + y1) / 2,
                     rx=pw / 2 + 70, ry=ph / 2 + 70, inner=0.93)
    blended = img * (1 - a) + fill * a

    # ---------- ⑧ 输出 ----------
    out = Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8))
    out = out.filter(ImageFilter.UnsharpMask(radius=1.3, percent=105, threshold=2))
    out = out.resize((int(w * 1.5), int(h * 1.5)), Image.LANCZOS)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    out.save(dst, "JPEG", quality=90, progressive=True, optimize=True)
    print(f"[out] {dst} {out.size[0]}x{out.size[1]} {os.path.getsize(dst)//1024} KB")

    if preview:
        os.makedirs(os.path.dirname(PREVIEW), exist_ok=True)
        out.resize((out.width // 2, out.height // 2), Image.LANCZOS).save(PREVIEW, "JPEG", quality=88)
        print(f"[preview] {PREVIEW}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--dst", default=DST)
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    build(a.src, a.dst, a.preview)


if __name__ == "__main__":
    main()
