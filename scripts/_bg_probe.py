# -*- coding: utf-8 -*-
"""诊断：定位背景图中央那块"半透明圆角面板"的范围（取局部对比度最低的大区域）。"""
from pathlib import Path

from PIL import Image, ImageFilter, ImageStat

SRC = Path(__file__).resolve().parents[1] / "斑马鱼背景图.jpeg"

im = Image.open(SRC).convert("RGB")
W, H = im.size
print("尺寸:", W, "x", H)

# 用 1/16 缩略图做统计，避开 JPEG 噪声
tw, th = W // 16, H // 16
g = im.resize((tw, th), Image.LANCZOS).convert("L")

# 局部标准差（3x3 邻域近似）：std ≈ sqrt(E[x^2] - E[x]^2)
blur = g.filter(ImageFilter.BoxBlur(1))
blur2 = g.point(lambda v: v * v).filter(ImageFilter.BoxBlur(1))
st = ImageStat.Stat(blur2).mean[0]
mean = ImageStat.Stat(blur).mean[0]

px_b = blur.load()
px_b2 = blur2.load()

CH = " .:-=+*#%@"
print("\n局部平坦度地图（@ = 最平坦/最像面板，空格 = 纹理最强）")
print("    " + "".join(str((i // 10) % 10) for i in range(tw)))
for y in range(th):
    row = []
    for x in range(tw):
        b, b2 = px_b[x, y], px_b2[x, y]
        var = max(0.0, b2 - b * b)
        sd = var ** 0.5
        # 标准差越小越平坦 → 用反序字符
        idx = int(max(0, min(9, 9 - sd * 1.6)))
        row.append(CH[idx])
    print("%3d " % y + "".join(row))
print("\n单格 = %d x %d 像素" % (W / tw, H / th))
