# -*- coding: utf-8 -*-
"""诊断 2：用 numpy 算"边缘能量"图，定位中央半透明面板的矩形范围。"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

SRC = Path(__file__).resolve().parents[1] / "斑马鱼背景图.jpeg"

im = Image.open(SRC).convert("RGB")
W, H = im.size
print("尺寸:", W, "x", H)

tw, th = W // 24, H // 24
g = np.asarray(im.resize((tw, th), Image.LANCZOS).convert("L"), dtype=np.float32)

gx = np.zeros_like(g)
gy = np.zeros_like(g)
gx[:, 1:] = np.abs(np.diff(g, axis=1))
gy[1:, :] = np.abs(np.diff(g, axis=0))
energy = gx + gy

# 平滑一下，避免单像素噪声
sm = np.asarray(
    Image.fromarray(np.clip(energy * 6, 0, 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(2)),
    dtype=np.float32,
)
mx = sm.max() or 1.0
norm = sm / mx

CH = "@%#*+=-:. "   # 能量低→@（平坦，像面板）；高→空格
print("\n边缘能量图（@ = 最平坦 = 面板内部；空格 = 纹理最强）")
print("     " + "".join(str(i % 10) for i in range(tw)))
for y in range(th):
    line = "".join(CH[min(8, int(norm[y, x] * 9))] for x in range(tw))
    print("%3d  %s" % (y, line))

# 面板估计：找"连续平坦"的最大矩形（按行/列能量阈值）
flat = norm < 0.12
rows = np.where(flat.mean(axis=1) > 0.45)[0]
cols = np.where(flat.mean(axis=0) > 0.45)[0]
if len(rows) and len(cols):
    y0, y1 = int(rows[0]), int(rows[-1])
    x0, x1 = int(cols[0]), int(cols[-1])
    print("\n估计面板（缩略图坐标）: x %d..%d  y %d..%d" % (x0, x1, y0, y1))
    sx, sy = W / tw, H / th
    print("估计面板（原图像素）  : x %d..%d  y %d..%d"
          % (x0 * sx, (x1 + 1) * sx, y0 * sy, (y1 + 1) * sy))
else:
    print("\n未找到明显平坦区")
