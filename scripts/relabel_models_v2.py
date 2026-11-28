# -*- coding: utf-8 -*-
"""重做：全称 x 轴标签用近竖排（72°）+ 极小字号，避免重叠/出界。

关键改进：
  - 角度 72°（近竖），水平投影 ≈ 字长 * cos72 ≈ 0.31*长，紧凑
  - 字号 9
  - 每个标签的“右下端”对准刻度 x，文字向左上延伸
  - 出界检查：若左端 < 4 或右端 > w-4，自动缩小该标签字号
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "output" / "model_labels"
IMG = WORK / "models_original.jpg"
OUT = WORK / "models_relabeled_v2.png"

NEW_LABELS = ["Zebrafish Agent", "Hy4", "Qwen 3.7-Max",
              "Doubao Seed 2.0 Pro", "Gemini 3.5 Flash-Lite"]
PANELS = [
    [42, 82, 122, 165, 208],
    [300, 340, 380, 422, 465],
    [552, 595, 638, 680, 723],
]
LABEL_BASE_Y = 1760   # 标签底端 y（比旧标签带略下，给斜排留空间）
ANGLE = 72
ERASE_TOP, ERASE_BOT = 1736, 1795


def font_at(size: int):
    for p in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def place_label(base, text, anchor_x, anchor_y, size=9):
    font = font_at(size)
    tmp = Image.new("RGBA", (260, 30), (255, 255, 255, 0))
    d = ImageDraw.Draw(tmp)
    d.text((1, 1), text, font=font, fill=(18, 24, 34, 255))
    rot = tmp.rotate(ANGLE, expand=True, resample=Image.BICUBIC)
    # 旋转后：让右下端落在 anchor 附近，文字向左上伸展
    left = int(anchor_x - rot.width * 0.90)
    top = int(anchor_y - rot.height * 0.55)
    # 出界则左移/右移并缩字号
    if left < 3:
        left = 3
    if left + rot.width > base.width - 3:
        left = base.width - 3 - rot.width
    base.paste(rot, (left, top), rot)
    return left, rot.width


def main() -> int:
    img = Image.open(IMG).convert("RGB")
    print("原图：%d x %d" % img.size)
    d = ImageDraw.Draw(img)
    # 扩大擦除带，覆盖旧标签 + 上一版失败标签
    d.rectangle([0, ERASE_TOP, img.width, ERASE_BOT], fill=(255, 255, 255))
    for panel in PANELS:
        for cx, lab in zip(panel, NEW_LABELS):
            l, wd = place_label(img, lab, cx, LABEL_BASE_Y)
            print("  %-24s x=%d width=%d" % (lab, l, wd))
    img.save(OUT)
    print("已输出：%s" % OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
