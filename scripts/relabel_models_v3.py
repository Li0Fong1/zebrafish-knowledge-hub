# -*- coding: utf-8 -*-
"""v3：在 v2 基础上修 C 面板左侧出界 + 标签整体上移。

v2 已解决重叠（72° 近竖排 + 小字），但 C 面板第一个标签 Zebrafish Agent
斜排后左上端超出图片左边界。修法：
  - 文字画在贴合尺寸的透明图层（先 textbbox 再建画布），旋转后按真实 bbox 贴
  - 若左端越界，整体右移到 x=4
  - 标签底端上移 18px（LABEL_BASE_Y 1760 -> 1742），不贴图底
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "output" / "model_labels"
IMG = WORK / "models_original.jpg"
OUT = WORK / "models_relabeled_v3.png"

NEW_LABELS = ["Zebrafish Agent", "Hy4", "Qwen 3.7-Max",
              "Doubao Seed 2.0 Pro", "Gemini 3.5 Flash-Lite"]
PANELS = [
    [42, 82, 122, 165, 208],
    [300, 340, 380, 422, 465],
    [552, 595, 638, 680, 723],
]
LABEL_BASE_Y = 1742
ANGLE = 72
ERASE_TOP, ERASE_BOT = 1728, 1795


def font_at(size: int):
    for p in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def place_label(base, text, anchor_x, anchor_y, size=9):
    font = font_at(size)
    # 精确贴合文字尺寸，避免透明画布虚胖导致 bbox 计算错
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tmp = Image.new("RGBA", (tw + 4, th + 4), (255, 255, 255, 0))
    d = ImageDraw.Draw(tmp)
    d.text((2 - bbox[0], 2 - bbox[1]), text, font=font, fill=(18, 24, 34, 255))
    rot = tmp.rotate(ANGLE, expand=True, resample=Image.BICUBIC)
    # 右下端对准刻度，文字向左上伸展
    left = int(anchor_x - rot.width * 0.85)
    top = int(anchor_y - rot.height * 0.60)
    if left < 4:
        left = 4
    if left + rot.width > base.width - 4:
        left = base.width - 4 - rot.width
    base.paste(rot, (left, top), rot)
    return left, rot.width


def main() -> int:
    img = Image.open(IMG).convert("RGB")
    print("原图：%d x %d" % img.size)
    d = ImageDraw.Draw(img)
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
