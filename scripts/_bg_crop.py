# -*- coding: utf-8 -*-
"""把生成后的背景图中心裁一块出来，便于肉眼判断面板是否真的被填平。"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "web" / "assets" / "brand" / "zf-home-bg.jpeg"
DST = ROOT / "解析文件" / "_bg_center.png"

im = Image.open(SRC)
W, H = im.size
box = (int(W * 0.22), int(H * 0.16), int(W * 0.78), int(H * 0.84))
im.crop(box).save(DST)
print("已裁剪:", DST, "尺寸", im.crop(box).size)
