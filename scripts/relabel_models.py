# -*- coding: utf-8 -*-
"""把模型对比图 C/D/E 三个面板 x 轴的 5 个简称换成全称（小字斜排）。

映射：
  ZRA -> Zebrafish Agent
  HY4 -> Hy4
  QW  -> Qwen 3.7-Max
  DBO -> Doubao Seed 2.0 Pro
  GEM -> Gemini 3.5 Flash-Lite

做法：
  1. 原图 803x1920；x 轴标签行在 y≈1736-1758（网格图确认）。
  2. 用白底矩形擦掉该带（保留 x 轴线，线在 y≈1730）。
  3. 在每个面板的 5 个标签中心位置，用旋转 ~28° 的小号字体写全称。
  4. 输出 models_relabeled.png。

坐标（原图像素）：
  面板 C 中心 x 组：约 42, 82, 122, 165, 208  （5 个小提琴）
  面板 D 中心 x 组：约 300, 340, 380, 422, 465
  面板 E 中心 x 组：约 552, 595, 638, 680, 723
  标签基线 y ≈ 1745
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "output" / "model_labels"
IMG = WORK / "models_original.jpg"
OUT = WORK / "models_relabeled.png"

NEW_LABELS = [
    "Zebrafish Agent",
    "Hy4",
    "Qwen 3.7-Max",
    "Doubao Seed 2.0 Pro",
    "Gemini 3.5 Flash-Lite",
]

# 每个面板 5 个标签的锚点 x（小提琴中心）
PANELS = [
    [42, 82, 122, 165, 208],
    [300, 340, 380, 422, 465],
    [552, 595, 638, 680, 723],
]
LABEL_Y = 1744
ERASE_TOP, ERASE_BOT = 1736, 1762
ANGLE = 50  # 更陡，水平投影更窄，避免相邻全称重叠


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (r"C:\Windows\Fonts\arial.ttf",
                 r"C:\Windows\Fonts\segoeui.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_rotated(base: Image.Image, text: str, x: int, y: int,
                 font, angle: int, fill=(20, 25, 35)) -> None:
    """文字向左上斜排，右下端对准刻度 (x,y)，避免盖住右侧邻居。"""
    tmp = Image.new("RGBA", (300, 40), (255, 255, 255, 0))
    d = ImageDraw.Draw(tmp)
    d.text((2, 2), text, font=font, fill=(*fill, 255))
    # 逆时针旋转 angle，文字从左下指向右上；右下端(t=0)贴 x
    rot = tmp.rotate(angle, expand=True, resample=Image.BICUBIC)
    # 让旋转图层的右端靠近刻度点 x，底端靠近 y
    base.paste(rot, (int(x - rot.width * 0.62), int(y - rot.height * 0.78)), rot)


def main() -> int:
    if not IMG.is_file():
        print("缺少原图：" + str(IMG))
        return 1
    img = Image.open(IMG).convert("RGB")
    w, h = img.size
    print("原图：%d x %d" % (w, h))

    d = ImageDraw.Draw(img)
    # 1) 擦掉旧标签带：用白底覆盖（x 轴线在 y≈1730，保留）
    d.rectangle([0, ERASE_TOP, w, ERASE_BOT], fill=(255, 255, 255))
    print("已擦除标签带 y=%d..%d" % (ERASE_TOP, ERASE_BOT))

    # 2) 重写全称，小字斜排。字号 13；全称较长，斜排后宽度约 110px
    font = load_font(11)
    for panel in PANELS:
        for cx, label in zip(panel, NEW_LABELS):
            draw_rotated(img, label, cx, LABEL_Y + 6, font, ANGLE)

    img.save(OUT)
    print("已输出：%s" % OUT)
    print("映射：" + " | ".join(
        "%s->%s" % (a, b) for a, b in zip(
            ["ZRA", "HY4", "QW", "DBO", "GEM"], NEW_LABELS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
