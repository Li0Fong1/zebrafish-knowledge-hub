"""程序化生成首页背景插画（风格对齐原素材图：浅蓝水色 + 光束 + 水草 + 斑马鱼 + DNA）。

为什么自己画
------------
原素材 `斑马鱼背景图.jpeg` 是水彩风插画，但中间压了一块大面积半透明白罩层
（内部 std 仅 1.9~3.8，洗白区延伸到 x228..1692 / y244..1148），罩下画面已不可还原；
换成真实照片又和站点"高调淡蓝"的配色差太远。
所以这里**按原图的语言**重画一张：构图一致（左右水草+底部沙床+顶部光束+右上 DNA），
但整幅都是干净的、无任何面板。

输出采用 2 倍超采样后再缩小（LANCZOS），边缘干净；背景大面积为平滑渐变，
在 2560×1584 下体积很小。

用法：
    python -X utf8 scripts/make_home_bg_art.py
    python -X utf8 scripts/make_home_bg_art.py --width 2560 --height 1584 --seed 7
"""
from __future__ import annotations

import argparse
import math
import os
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter

DST = os.path.join("web", "assets", "brand", "zf-home-bg.jpeg")
PREVIEW = os.path.join("解析文件", "bg_candidates", "art_preview.jpg")

# ---------------- 调色板（对齐 premium.css 的 --p-* 系列） ----------------
C_TOP = (245, 251, 255)
C_SKY = (226, 243, 254)
C_MID = (198, 230, 249)
C_DEEP = (166, 211, 240)
C_SAND = (226, 233, 228)
C_SAND2 = (206, 217, 212)
C_ROCK = (166, 183, 196)
C_ROCK2 = (134, 154, 170)
PLANT_DARK = (38, 106, 82)
PLANT_MID = (63, 150, 111)
PLANT_LITE = (108, 189, 148)
FISH_BODY = (150, 182, 214)
FISH_STRIPE = (38, 78, 132)
FISH_FIN = (198, 220, 236)
DNA = (96, 154, 210)


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def lerp3(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# ---------------------------------------------------------------- 底层水体
def water_base(w: int, h: int) -> Image.Image:
    """竖直三段渐变 + 顶部日光晕。"""
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        if t < 0.45:
            c = lerp(C_TOP, C_SKY, t / 0.45)
        elif t < 0.80:
            c = lerp(C_SKY, C_MID, (t - 0.45) / 0.35)
        else:
            c = lerp(C_MID, C_DEEP, (t - 0.80) / 0.20)
        px[0, y] = c
    img = col.resize((w, h), Image.BILINEAR)

    # 日光晕：顶部中央的柔和暖白光
    glow = Image.new("L", (w, h), 0)
    gd = ImageDraw.Draw(glow)
    r = int(w * 0.42)
    gd.ellipse([w * 0.5 - r, -h * 0.30 - r * 0.5, w * 0.5 + r, -h * 0.30 + r], fill=170)
    glow = glow.filter(ImageFilter.GaussianBlur(w * 0.10))
    white = Image.new("RGB", (w, h), (255, 255, 255))
    img = Image.composite(white, img, glow.point(lambda v: int(v * 0.62)))
    return img


# ---------------------------------------------------------------- 光束
def light_shafts(h: int, w: int, rng: random.Random) -> Image.Image:
    lay = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(lay)
    for _ in range(6):
        cx = rng.uniform(0.08, 0.92) * w
        w0 = rng.uniform(0.010, 0.030) * w
        spread = rng.uniform(0.10, 0.26) * w
        drift = rng.uniform(-0.06, 0.06) * w
        bottom = rng.uniform(0.55, 0.95) * h
        alpha = rng.randint(18, 38)
        d.polygon(
            [(cx - w0, 0), (cx + w0, 0),
             (cx + drift + spread, bottom), (cx + drift - spread * 0.55, bottom)],
            fill=alpha,
        )
    lay = lay.filter(ImageFilter.GaussianBlur(w * 0.012))
    lay = lay.filter(ImageFilter.GaussianBlur(w * 0.006))
    return lay


# ---------------------------------------------------------------- 水草
def draw_blade(d: ImageDraw.ImageDraw, x0: float, y0: float, hgt: float, wd: float,
               bend: float, c0, c1, alpha: int, seg: int = 22):
    """一片水草叶：从基部到尖端逐渐变细，颜色渐变，带弯曲。"""
    left, right = [], []
    for i in range(seg + 1):
        t = i / seg
        cx = x0 + bend * (t ** 1.7)
        cy = y0 - hgt * t
        taper = (1.0 - t) ** 0.75
        taper *= 0.35 + 0.65 * math.sin(math.pi * min(1.0, t * 1.5 + 0.18))
        hw = max(0.6, wd * taper)
        left.append((cx - hw, cy))
        right.append((cx + hw, cy))
    for i in range(seg):
        quad = [left[i], left[i + 1], right[i + 1], right[i]]
        col = lerp(c0, c1, i / seg)
        d.polygon(quad, fill=(*col, alpha))


def plant_cluster(w: int, h: int, rng: random.Random, side: str, blur: float,
                  n: int, scale: float, alpha: int) -> Image.Image:
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    base_x = 0 if side == "left" else w
    span = w * 0.24 * scale
    for _ in range(n):
        rel = rng.random() ** 1.5
        x = base_x + (rel * span if side == "left" else -rel * span)
        y = h * rng.uniform(0.86, 1.02)
        wide = rng.random() < 0.22                       # 少量阔叶，避免全是细丝
        hgt = h * rng.uniform(0.14, 0.40) * scale if wide else h * rng.uniform(0.24, 0.66) * scale
        wd = (w * rng.uniform(0.009, 0.019) if wide else w * rng.uniform(0.0035, 0.0090)) * scale
        bend = (1 if side == "left" else -1) * w * rng.uniform(0.005, 0.05) * rng.choice([-1, 1])
        c0 = lerp(PLANT_DARK, PLANT_MID, rng.uniform(0, 1))
        c1 = lerp(PLANT_MID, PLANT_LITE, rng.uniform(0.2, 1))
        draw_blade(d, x, y, hgt, wd, bend, c0, c1, alpha)
    if blur > 0:
        lay = lay.filter(ImageFilter.GaussianBlur(blur))
    return lay


# ---------------------------------------------------------------- 岩石与沙床
def rocks(w: int, h: int, rng: random.Random, side: str) -> Image.Image:
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for k in range(4):
        cx = (rng.uniform(0.01, 0.13) if side == "left" else rng.uniform(0.87, 0.99)) * w
        cy = h * rng.uniform(0.90, 1.00)
        rx = w * rng.uniform(0.035, 0.075)
        ry = h * rng.uniform(0.028, 0.055)
        pts = []
        for i in range(26):
            a = 2 * math.pi * i / 26
            rr = 1 + rng.uniform(-0.16, 0.16)
            pts.append((cx + math.cos(a) * rx * rr, cy + math.sin(a) * ry * rr))
        col = lerp(C_ROCK, C_ROCK2, rng.uniform(0.1, 0.8))
        d.polygon(pts, fill=(*col, 235))
        # 高光
        d.polygon([(p[0] * 0.985 + cx * 0.015, p[1] * 0.93 + (cy - ry * 0.5) * 0.07) for p in pts[:13]],
                  fill=(*(lerp(col, (255, 255, 255), 0.35)), 90))
    return lay.filter(ImageFilter.GaussianBlur(w * 0.0015))


def sand(w: int, h: int, rng: random.Random) -> Image.Image:
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    top = h * 0.955
    d.polygon([(0, top), (w * 0.18, top - h * 0.012), (w * 0.42, top + h * 0.004),
               (w * 0.66, top - h * 0.008), (w * 0.86, top + h * 0.006), (w, top - h * 0.004),
               (w, h), (0, h)],
              fill=(*C_SAND, 255))
    for _ in range(320):
        x = rng.uniform(0, w)
        y = rng.uniform(top, h)
        r = rng.uniform(w * 0.0008, w * 0.0022)
        c = lerp(C_SAND2, (255, 255, 255), rng.uniform(0, 0.6))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(*c, rng.randint(60, 150)))
    return lay.filter(ImageFilter.GaussianBlur(w * 0.0012))


# ---------------------------------------------------------------- 斑马鱼
def fish_tile(size: int, angle: float, alpha: int) -> Image.Image:
    """在 size×size 的画布里画一条侧身斑马鱼，再整体旋转。"""
    ss = 4
    S = size * ss
    tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    cx, cy = S * 0.5, S * 0.5
    bw, bh = S * 0.40, S * 0.15

    # 尾鳍
    d.polygon([(cx - bw * 0.86, cy), (cx - bw * 1.55, cy - bh * 1.15),
               (cx - bw * 1.30, cy), (cx - bw * 1.55, cy + bh * 1.15)],
              fill=(*FISH_FIN, 215))
    # 背鳍 / 臀鳍
    d.polygon([(cx - bw * 0.25, cy - bh * 0.85), (cx + bw * 0.15, cy - bh * 1.55),
               (cx + bw * 0.30, cy - bh * 0.80)], fill=(*FISH_FIN, 190))
    d.polygon([(cx - bw * 0.20, cy + bh * 0.80), (cx + bw * 0.10, cy + bh * 1.40),
               (cx + bw * 0.25, cy + bh * 0.75)], fill=(*FISH_FIN, 170))
    # 身体
    body = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bd = ImageDraw.Draw(body)
    bd.ellipse([cx - bw, cy - bh, cx + bw, cy + bh], fill=(*FISH_BODY, 244))
    # 腹部提亮
    bd.ellipse([cx - bw * 0.80, cy - bh * 0.05, cx + bw * 0.72, cy + bh * 0.92],
               fill=(238, 246, 252, 190))
    # 纵向条纹（纵横鱼是**横向**条纹，沿体轴走向）
    for k in range(4):
        t = -0.58 + k * 0.38
        yy = cy + bh * t
        half = bw * math.sqrt(max(0.05, 1 - t * t)) * 0.94
        bd.line([(cx - half * 0.55, yy), (cx + half * 0.92, yy)],
                fill=(*FISH_STRIPE, 150), width=max(2, int(S * 0.011)))
    tile = Image.alpha_composite(tile, body)
    d = ImageDraw.Draw(tile)
    # 眼睛
    er = S * 0.017
    d.ellipse([cx + bw * 0.76 - er, cy - er * 1.2, cx + bw * 0.76 + er, cy + er * 0.8],
              fill=(255, 255, 255, 235))
    d.ellipse([cx + bw * 0.77 - er * 0.6, cy - er * 0.7, cx + bw * 0.77 + er * 0.6, cy + er * 0.5],
              fill=(26, 42, 60, 245))

    tile = tile.resize((size, size), Image.LANCZOS)
    tile = tile.rotate(angle, resample=Image.BICUBIC, expand=False)
    if alpha < 255:
        a = tile.split()[3].point(lambda v: int(v * alpha / 255))
        tile.putalpha(a)
    return tile


def school(w: int, h: int, rng: random.Random) -> Image.Image:
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    spots = [
        (0.135, 0.200, 0.95, -9), (0.215, 0.145, 0.72, 6), (0.075, 0.310, 0.66, 12),
        (0.855, 0.245, 0.92, 190), (0.905, 0.400, 0.70, 176), (0.780, 0.130, 0.60, 168),
        (0.500, 0.640, 0.52, -4), (0.585, 0.720, 0.44, 8),
    ]
    for fx, fy, sc, ang in spots:
        size = int(w * 0.115 * sc)
        t = fish_tile(size, ang, 235)
        lay.alpha_composite(t, (int(fx * w - size / 2), int(fy * h - size / 2)))
    return lay


# ---------------------------------------------------------------- DNA 双螺旋
def dna(w: int, h: int) -> Image.Image:
    """右上角的半透明 DNA 双螺旋（两条正弦曲线 + 碱基对横档）。"""
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    ox, oy = w * 0.805, h * 0.045
    L = h * 0.34
    amp = w * 0.036
    turns = 2.2
    n = 480
    lw = max(3, int(w * 0.0026))
    p1, p2 = [], []
    for i in range(n):
        t = i / (n - 1)
        ang = t * turns * 2 * math.pi
        y = oy + L * t
        p1.append((ox + math.sin(ang) * amp, y))
        p2.append((ox - math.sin(ang) * amp, y))
    d.line(p1, fill=(*DNA, 165), width=lw, joint="curve")
    d.line(p2, fill=(*DNA, 165), width=lw, joint="curve")
    for i in range(0, n, 16):
        t = i / (n - 1)
        ang = t * turns * 2 * math.pi
        y = oy + L * t
        d.line([(ox + math.sin(ang) * amp, y), (ox - math.sin(ang) * amp, y)],
               fill=(*DNA, 105), width=max(2, lw // 2))
    return lay.filter(ImageFilter.GaussianBlur(w * 0.0012))


# ---------------------------------------------------------------- 气泡
def bubbles(w: int, h: int, rng: random.Random) -> Image.Image:
    ss = 3
    lay = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for _ in range(60):
        x = rng.uniform(0.03, 0.97) * w * ss
        y = rng.uniform(0.05, 0.98) * h * ss
        r = rng.uniform(0.0018, 0.0058) * w * ss
        a = rng.randint(45, 105)
        d.ellipse([x - r, y - r, x + r, y + r], outline=(255, 255, 255, a), width=max(1, ss))
        d.ellipse([x - r * 0.45, y - r * 0.55, x + r * 0.02, y - r * 0.12],
                  fill=(255, 255, 255, min(200, a + 70)))
    return lay.resize((w, h), Image.LANCZOS)


# ---------------------------------------------------------------- 合成
def build(width: int, height: int, seed: int, dst: str, preview: bool) -> None:
    ss = 2
    W, H = width * ss, height * ss
    rng = random.Random(seed)

    base = water_base(W, H).convert("RGBA")

    shafts = light_shafts(H, W, rng)
    white = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    white.putalpha(shafts.point(lambda v: int(v * 1.15)))
    base = Image.alpha_composite(base, white)

    # 水面波纹：顶部几条极淡的波浪线，交代"水在头顶"
    surf = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(surf)
    for k in range(7):
        y0 = H * (0.012 + k * 0.011)
        amp = H * (0.004 + rng.random() * 0.004)
        pts = [(x, y0 + math.sin(x / (W * 0.06) + k * 1.7) * amp) for x in range(0, W, 8)]
        sd.line(pts, fill=(255, 255, 255, 70 - k * 5), width=max(2, int(W * 0.0014)), joint="curve")
    base = Image.alpha_composite(base, surf.filter(ImageFilter.GaussianBlur(W * 0.0015)))

    # 远景水草（虚一点）+ 近景水草（实）
    base = Image.alpha_composite(base, plant_cluster(W, H, rng, "left", W * 0.006, 26, 1.15, 96))
    base = Image.alpha_composite(base, plant_cluster(W, H, rng, "right", W * 0.006, 26, 1.15, 96))
    base = Image.alpha_composite(base, rocks(W, H, rng, "left"))
    base = Image.alpha_composite(base, rocks(W, H, rng, "right"))
    base = Image.alpha_composite(base, sand(W, H, rng))
    base = Image.alpha_composite(base, plant_cluster(W, H, rng, "left", 0, 56, 1.0, 232))
    base = Image.alpha_composite(base, plant_cluster(W, H, rng, "right", 0, 56, 1.0, 232))

    # 底部光斑：水下焦散打在沙床上
    dapple = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dapple)
    for _ in range(70):
        x = rng.uniform(0, W)
        y = rng.uniform(H * 0.80, H * 0.99)
        rx = rng.uniform(W * 0.008, W * 0.030)
        ry = rx * rng.uniform(0.18, 0.40)
        dd.ellipse([x - rx, y - ry, x + rx, y + ry], fill=(255, 255, 255, rng.randint(18, 46)))
    base = Image.alpha_composite(base, dapple.filter(ImageFilter.GaussianBlur(W * 0.006)))

    base = Image.alpha_composite(base, dna(W, H))
    base = Image.alpha_composite(base, school(W, H, rng))
    base = Image.alpha_composite(base, bubbles(W, H, rng))
    base = Image.alpha_composite(base, plant_cluster(W, H, rng, "right", 0, 10, 1.25, 150))

    # 四周极轻的白色柔光，保持"高调、通透"的观感
    vig = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vig)
    m = int(W * 0.10)
    vd.rectangle([m, m, W - m, H - m], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(W * 0.09))
    vig = ImageChops.invert(vig).point(lambda v: int(v * 0.20))
    veil = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    veil.putalpha(vig)
    base = Image.alpha_composite(base, veil)

    out = base.convert("RGB").resize((width, height), Image.LANCZOS)
    out = out.filter(ImageFilter.UnsharpMask(radius=1.2, percent=70, threshold=2))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    out.save(dst, "JPEG", quality=92, progressive=True, optimize=True)
    print(f"[out] {dst} {out.size[0]}x{out.size[1]} {os.path.getsize(dst)//1024} KB")

    if preview:
        os.makedirs(os.path.dirname(PREVIEW), exist_ok=True)
        out.resize((out.width // 2, out.height // 2), Image.LANCZOS).save(PREVIEW, "JPEG", quality=88)
        print(f"[preview] {PREVIEW}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=2560)
    ap.add_argument("--height", type=int, default=1584)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--dst", default=DST)
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    build(a.width, a.height, a.seed, a.dst, a.preview)


if __name__ == "__main__":
    main()
