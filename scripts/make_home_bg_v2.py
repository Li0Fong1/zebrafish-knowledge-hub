"""重建首页背景图：去掉中间那块"磨砂白面板"，补出完整的水下场景。

背景
----
原图 `斑马鱼背景图.jpeg`（1920×1184）是一张素材缩略图：中间压了一块半透明白圆角面板
用来放文字。实测面板内的亮度标准差只有 1.9~3.8（正常区域 23~28），说明面板下面
**没有可还原的画面**（已经被模糊压平），只能重新画。

做法（全部基于原图自身的纹理，不引入任何外部素材/授权问题）
------------------------------------------------------------
1. 面板定位：用亮度/饱和度突变找出圆角矩形边界（也可硬编码兜底）。
2. 水柱延续：取面板正上方那条"水面 + 光束"横带，纵向拉伸铺满面板高度
   —— 光束自然变长，符合水下越深越弥散的感觉。
3. 焦散纹理：多频值噪声 → 高反差化 → 轻微高斯平滑，叠成 ±2% 的亮度起伏，
   保证这块区域**有细节、不糊**。
4. 光束：随机若干条竖直高斯光带，随深度淡出，并带一点倾斜。
5. 两侧水草：把面板左右两侧的水草镜像"探进"面板内 ~170px，用 alpha 渐隐，
   看起来像水草从边缘向中间生长；不会出现对称镜像的违和感。
6. 水底：面板下缘外的那条沙石带镜像向上延伸 ~70px 做地面。
7. 气泡：程序化撒一些，大小 2~7px，带高光点。
8. 合成：圆角矩形蒙版 → 高斯羽化 7px → 贴回原图，接缝不可见。
9. 输出：UnsharpMask → LANCZOS ×1.5 → 2880×1776 JPEG。

用法：
    python -X utf8 scripts/make_home_bg_v2.py
    python -X utf8 scripts/make_home_bg_v2.py --preview   # 额外输出 1/2 预览图
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.ndimage import gaussian_filter, gaussian_filter1d

SRC = "斑马鱼背景图.jpeg"
DST = os.path.join("web", "assets", "brand", "zf-home-bg.jpeg")
PREVIEW = os.path.join("解析文件", "bg_candidates", "bg_preview.jpg")

# 面板兜底矩形（x0, y0, x1, y1）——自动检测失败时使用
PANEL_FALLBACK = (312, 314, 1632, 1063)

# 面板的**真实软边界**（2026-10-22 实测）。
# 那块白色罩层不是硬边矩形，而是一块带很宽柔和过渡的圆角罩：
#   内部完全平光区 312..1632 / 314..1063（std 1.9~3.8）
#   向外逐渐“洗白”，到 x≈228 / 1692、y≈244 / 1148 才恢复到真实纹理（std>20）
# 只盖内部平光区、不盖外面那圈洗白区，就会剩下一圈平光带 ——
# 视觉上永远是一块“磨砂边框”。所以必须按软边界整块替换。
PANEL_SOFT = (228, 244, 1692, 1148)


# ---------------------------------------------------------------- 面板定位
def detect_panel(img: np.ndarray) -> tuple[int, int, int, int]:
    """找中间那块"平光区"。判据：局部亮度的行/列标准差骤降。"""
    g = img.mean(axis=2)
    h, w = g.shape
    # 每一行的横向标准差：面板内部几乎恒定
    row_std = gaussian_filter1d(g.std(axis=1), 3)
    col_std = gaussian_filter1d(g.std(axis=0), 3)
    thr_r = row_std.max() * 0.30
    thr_c = col_std.max() * 0.30
    rows = np.where(row_std < thr_r)[0]
    cols = np.where(col_std < thr_c)[0]
    if len(rows) < 20 or len(cols) < 20:
        return PANEL_FALLBACK
    y0, y1 = int(rows.min()), int(rows.max())
    x0, x1 = int(cols.min()), int(cols.max())
    # 合理性检查：必须是图中间一块够大的区域
    if (x1 - x0) < w * 0.3 or (y1 - y0) < h * 0.3:
        return PANEL_FALLBACK
    return x0, y0, x1, y1


# ---------------------------------------------------------------- 纹理工具
def vstretch(strip: np.ndarray, out_h: int) -> np.ndarray:
    """把一条横带纵向拉伸到指定高度（双线性）。"""
    im = Image.fromarray(np.clip(strip, 0, 255).astype(np.uint8))
    return np.asarray(im.resize((im.width, out_h), Image.BICUBIC)).astype(np.float32)


def value_noise(shape: tuple[int, int], scale: float, rng) -> np.ndarray:
    """低频值噪声：小图随机 → 放大（得到平滑的连续场）。"""
    hh = max(2, int(shape[0] / scale) + 2)
    ww = max(2, int(shape[1] / scale) + 2)
    small = (rng.random((hh, ww)) * 255).astype(np.uint8)
    up = Image.fromarray(small).resize((shape[1], shape[0]), Image.BICUBIC)
    return np.asarray(up).astype(np.float32) / 255.0


def caustics(shape: tuple[int, int], rng) -> np.ndarray:
    """水下焦散：多频噪声归一化后压成 ±1 的起伏场。"""
    f = (0.55 * value_noise(shape, 42, rng)
         + 0.28 * value_noise(shape, 17, rng)
         + 0.17 * value_noise(shape, 7, rng))
    f = gaussian_filter(f, 1.1)
    f -= f.mean()
    f /= (f.std() + 1e-6)
    return np.clip(f, -2.5, 2.5)


def light_shafts(h: int, w: int, rng) -> np.ndarray:
    """竖直光带：随机位置/宽度/强度的高斯条，随深度淡出，并轻微倾斜。"""
    prof = np.zeros(w, dtype=np.float32)
    for _ in range(7):
        cx = rng.uniform(0, w)
        sd = rng.uniform(w * 0.03, w * 0.11)
        amp = rng.uniform(0.45, 1.0)
        prof += amp * np.exp(-((np.arange(w) - cx) ** 2) / (2 * sd ** 2))
    prof = gaussian_filter1d(prof, w * 0.004)
    prof /= (prof.max() + 1e-6)

    fade = np.linspace(1.0, 0.10, h) ** 1.15
    out = np.empty((h, w), dtype=np.float32)
    base = np.arange(w, dtype=np.float32)
    tilt = rng.uniform(-0.10, 0.10)
    for y in range(h):
        shift = tilt * y
        out[y] = np.interp(base + shift, base, prof, left=0.0, right=0.0) * fade[y]
    return out


def add_bubbles(rgb: np.ndarray, mask_region, rng) -> np.ndarray:
    """在水柱里撒气泡（画在放大 2x 的临时画布上再缩回来，边缘更细腻）。"""
    y0, y1, x0, x1 = mask_region
    h, w = y1 - y0, x1 - x0
    ss = 2
    canvas = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    for _ in range(46):
        cx = rng.uniform(0.02, 0.98) * w * ss
        cy = rng.uniform(0.02, 0.98) * h * ss
        r = rng.uniform(1.6, 5.2) * ss
        a = int(rng.uniform(26, 78))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, a), width=max(1, ss))
        d.ellipse([cx - r * 0.42, cy - r * 0.52, cx + r * 0.05, cy - r * 0.10],
                  fill=(255, 255, 255, min(150, a + 60)))
    canvas = canvas.resize((w, h), Image.LANCZOS)
    lay = np.asarray(canvas).astype(np.float32)
    alpha = lay[:, :, 3:4] / 255.0
    rgb[y0:y1, x0:x1] = rgb[y0:y1, x0:x1] * (1 - alpha) + lay[:, :, :3] * alpha
    return rgb


def rounded_rect_mask(w: int, h: int, rr: int, blur: float) -> np.ndarray:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=rr, fill=255)
    m = m.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(m).astype(np.float32)[:, :, None] / 255.0


def match_boundary(fill: np.ndarray, img: np.ndarray, rect) -> np.ndarray:
    """把填充区四边的色差拉平。

    面板在原图里带一圈向外扩散的柔光，即使把矩形盖住，边缘左右/上下也会差一两个色阶 ——
    肉眼看就是「一块矩形糊在上面」。这里量出四边「外侧真实像素」与「内侧填充像素」的均值差，
    沿法线方向做双线性过渡（边上修正量满值、越往中心越小），再高斯平滑，台阶就消失了。
    """
    ex0, ey0, ex1, ey1 = rect
    h, w = img.shape[:2]
    out = fill.copy()

    def diff(outer_slice, inner_slice) -> np.ndarray:
        o = img[outer_slice[0], outer_slice[1]]
        i = fill[inner_slice[0], inner_slice[1]]
        if o.size == 0 or i.size == 0:
            return np.zeros(3, dtype=np.float32)
        return o.reshape(-1, 3).mean(axis=0) - i.reshape(-1, 3).mean(axis=0)

    pad = 12
    d_top = diff((slice(max(0, ey0 - pad - 2), max(1, ey0 - 2)), slice(ex0 + 40, ex1 - 40)),
                 (slice(ey0 + 2, ey0 + pad), slice(ex0 + 40, ex1 - 40)))
    d_bot = diff((slice(min(h, ey1 + 2), min(h, ey1 + pad + 2)), slice(ex0 + 40, ex1 - 40)),
                 (slice(ey1 - pad, ey1 - 2), slice(ex0 + 40, ex1 - 40)))
    d_left = diff((slice(ey0 + 40, ey1 - 40), slice(max(0, ex0 - pad - 2), max(1, ex0 - 2))),
                  (slice(ey0 + 40, ey1 - 40), slice(ex0 + 2, ex0 + pad)))
    d_right = diff((slice(ey0 + 40, ey1 - 40), slice(min(w, ex1 + 2), min(w, ex1 + pad + 2))),
                   (slice(ey0 + 40, ey1 - 40), slice(ex1 - pad, ex1 - 2)))

    hh, ww = ey1 - ey0, ex1 - ex0
    ty = np.linspace(0.0, 1.0, hh, dtype=np.float32)[:, None, None]
    tx = np.linspace(0.0, 1.0, ww, dtype=np.float32)[None, :, None]
    corr_v = (1 - ty) * d_top[None, None, :] + ty * d_bot[None, None, :]
    corr_h = (1 - tx) * d_left[None, None, :] + tx * d_right[None, None, :]
    corr = 0.5 * corr_v + 0.5 * corr_h
    # 低频平滑：修正量本身必须是很慢的渐变，否则会看出“边框”
    corr = np.stack([gaussian_filter(corr[:, :, c], 45) for c in range(3)], axis=2)
    out[ey0:ey1, ex0:ex1] += corr
    return out


# ---------------------------------------------------------------- 主流程
def build(src: str = SRC, dst: str = DST, preview: bool = False) -> None:
    orig = Image.open(src).convert("RGB")
    img = np.asarray(orig).astype(np.float32)
    h, w = img.shape[:2]

    x0, y0, x1, y1 = detect_panel(img)
    sx0, sy0, sx1, sy1 = PANEL_SOFT
    if 0 <= sx0 < sx1 <= w and 0 <= sy0 < sy1 <= h and (sx1 - sx0) > (x1 - x0):
        print(f"[panel] 检测到平光区 x={x0}..{x1} y={y0}..{y1}，改用真实软边界 x={sx0}..{sx1} y={sy0}..{sy1}")
        x0, y0, x1, y1 = sx0, sy0, sx1, sy1
    pw, ph = x1 - x0, y1 - y0
    print(f"[panel] 填充区 x={x0}..{x1}  y={y0}..{y1}  ({pw}x{ph})")

    rng = np.random.default_rng(20261022)
    fill = img.copy()

    # ---- 2. 水柱延续：取面板上方横带，纵向拉伸铺满
    top_band = img[max(0, y0 - 300):y0, :, :]
    column = vstretch(top_band, ph)[:, x0:x1, :]

    # ---- 3. 焦散细节（多频叠加，肉眼能看到真实水纹而不是一块平光）
    caus = caustics((ph, pw), rng)
    column = column * (1.0 + 0.034 * caus[:, :, None])

    # ---- 4. 光束（越往下越淡）
    shaft = light_shafts(ph, pw, rng)
    column = column * (1.0 + 0.065 * shaft[:, :, None])

    # 统一偏青蓝一点，和原图两侧水色对齐（取面板左右两侧的平均色做参照）
    ref = np.concatenate([img[y0:y1, max(0, x0 - 120):x0].reshape(-1, 3),
                          img[y0:y1, x1:min(w, x1 + 120)].reshape(-1, 3)], axis=0)
    ref_mean = ref.mean(axis=0)
    cur_mean = column.reshape(-1, 3).mean(axis=0)
    column = column + (ref_mean - cur_mean) * 0.55

    fill[y0:y1, x0:x1] = column

    # ---- 5. 两侧水草探进来：镜像贴入，alpha 从边缘向内渐隐
    intrude = 230
    for src_x0, src_x1, side in ((0, x0, "left"), (x1, w, "right")):
        sw = src_x1 - src_x0
        if sw < 40:
            continue
        band = img[y0:y1, src_x0:src_x1]
        if side == "right":
            band = band[:, ::-1]                     # 右侧镜像，朝内生长
        else:
            band = band[:, ::-1]                     # 左侧也翻一次，避免直接重复
        band = vstretch(band, ph)
        band = np.asarray(Image.fromarray(np.clip(band, 0, 255).astype(np.uint8))
                          .resize((intrude, ph), Image.LANCZOS)).astype(np.float32)
        ramp = np.linspace(0.92, 0.0, intrude) ** 0.85
        if side == "left":
            tx0, tx1 = x0, x0 + intrude
        else:
            tx0, tx1 = x1 - intrude, x1
            ramp = ramp[::-1]
        a = ramp[None, :, None]
        dst_region = fill[y0:y1, tx0:tx1]
        fill[y0:y1, tx0:tx1] = dst_region * (1 - a) + band * a

    # ---- 6. 水底：面板下缘外那条沙石带镜像向上延伸
    if y1 < h - 12:
        floor_h = min(70, h - y1)
        floor = img[y1:y1 + floor_h, x0:x1][::-1, :]      # 上下翻转，向上延伸
        rows = np.arange(ph)
        idxs = np.clip(np.abs(rows - (ph - floor_h)), 0, floor_h - 1)
        band = floor[idxs]
        ramp = np.clip(1.0 - (ph - rows) / float(floor_h), 0.0, 1.0) ** 1.4
        a = ramp[:, None, None] * 0.85
        out = fill[y0:y1, x0:x1]
        fill[y0:y1, x0:x1] = out * (1 - a) + band * a

    # ---- 7. 气泡
    fill = add_bubbles(fill, (y0, y1, x0, x1), rng)

    # ---- 8. 边界色差校正 + 合成回原图
    # ⚠️ 蒙版**不能向外扩张**：向外多盖 26px 会把面板边缘那圈真实水草/岩石换成平滑填充，
    #    结果就是一圈 std≈1.6 的平光带（实测），肉眼/模型都判定为“磨砂边框”。
    #    只做 6px 窄羽化，外面完全保留原图。
    EXPAND = 6
    ex0, ey0 = max(0, x0 - EXPAND), max(0, y0 - EXPAND)
    ex1, ey1 = min(w, x1 + EXPAND), min(h, y1 + EXPAND)
    fill = match_boundary(fill, img, (x0, y0, x1, y1))

    m = rounded_rect_mask(pw, ph, rr=40, blur=26)
    blended = img.copy()
    blended[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - m) + fill[y0:y1, x0:x1] * m

    # ---- 9. 输出
    out = Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8))
    out = out.filter(ImageFilter.UnsharpMask(radius=1.4, percent=115, threshold=2))
    out = out.resize((int(w * 1.5), int(h * 1.5)), Image.LANCZOS)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    out.save(dst, "JPEG", quality=90, progressive=True, optimize=True)
    print(f"[out] {dst}  {out.size[0]}x{out.size[1]}  {os.path.getsize(dst)//1024} KB")

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
