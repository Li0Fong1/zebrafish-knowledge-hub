# -*- coding: utf-8 -*-
"""模型对比图 x 轴标签改为短名（横排，居中对齐各刻度）。

映射：
  ZRA -> ZA
  HY4 -> Hy4
  QW  -> Qwen
  DBO -> Doubao
  GEM -> Gemini

从 models_original.jpg 重新生成（不叠加之前斜排版本）：
  1. 擦掉旧标签带 y=1732..1766
  2. 每个刻度中心横排写短名，字号 11，深色
  3. 输出 models_relabeled_short.png
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "output" / "model_labels"
IMG = WORK / "models_original.jpg"
OUT = WORK / "models_relabeled_short.png"

NEW_LABELS = ["ZA", "Hy4", "Qwen", "Doubao", "Gemini"]
PANELS = [
    [42, 82, 122, 165, 208],
    [300, 340, 380, 422, 465],
    [552, 595, 638, 680, 723],
]
# 每个面板擦除带的左边界；必须在该面板 Y 轴 "0" 刻度右侧，否则会擦掉 "0"
# C: "0" 在 x≈15-25 → 30  ; D: "0" 在 x≈283-293 → 296 ; E: "0" 在 x≈538-548 → 550
PANEL_ERASE_LEFT = [30, 296, 550]
LABEL_Y = 1748          # 文字垂直中心
# 2026-11-25 修复：原先通栏 [0, 1732, w, 1768] 刷白，把三个面板左侧的
# Y 轴 "0" 刻度与 X 轴基线左端一并擦掉（用户指出"Y轴被吃了"）。
# 改为「每个旧标签各擦一个小矩形」，只盖文字，绝不触碰 Y 轴区域。
ERASE_TOP, ERASE_BOT = 1738, 1762   # 基线在 y≈1730，起点避开它
ERASE_HALF = 16                      # 每个标签左右各擦 16px


def font_at(size: int):
    for p in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> int:
    if not IMG.is_file():
        print("缺少原图：" + str(IMG))
        return 1
    original = Image.open(IMG).convert("RGB")
    img = original.copy()
    w, h = img.size
    print("原图：%d x %d" % (w, h))
    # 以 803x1920 为基准；若新图尺寸不同，按比例缩放全部坐标
    sx, sy = w / 803.0, h / 1920.0
    if abs(sx - 1) > 0.01 or abs(sy - 1) > 0.01:
        print("检测到尺寸变化，坐标按比例缩放 sx=%.3f sy=%.3f" % (sx, sy))
    y_top, y_bot = int(ERASE_TOP * sy), int(ERASE_BOT * sy)
    label_y = int(LABEL_Y * sy)
    font_size = max(8, int(11 * min(sx, sy)))

    d = ImageDraw.Draw(img)
    # 按面板擦除一条「标签带」：左起点从该面板第一个标签左缘开始，
    # 不越过左侧 Y 轴的 "0" 刻度（C≈15-25 / D≈283-293 / E≈538-548）。
    # 上边界 1738 在 X 轴基线(y≈1730)之下，基线保持完整。
    for i, panel in enumerate(PANELS):
        l = int(PANEL_ERASE_LEFT[i] * sx)
        r = int((panel[-1] + ERASE_HALF + 4) * sx)
        d.rectangle([l, y_top, r, y_bot], fill=(255, 255, 255))

    # 兜底：把每个面板 Y 轴左侧的窄条（"0" 刻度数字 + 轴线）从原图贴回。
    # 只贴到各面板擦除左边界之前，不进标签区，避免旧标签残留。
    y0, y1 = int(1700 * sy), int(1780 * sy)
    for i, panel in enumerate(PANELS):
        axis_left = int((panel[0] - 40) * sx)
        stop = int(PANEL_ERASE_LEFT[i] * sx)
        if stop > axis_left:
            guard = original.crop((axis_left, y0, stop, y1))
            img.paste(guard, (axis_left, y0))
    d = ImageDraw.Draw(img)

    font = font_at(font_size)
    for panel in PANELS:
        for cx0, lab in zip(panel, NEW_LABELS):
            cx = int(cx0 * sx)
            box = d.textbbox((0, 0), lab, font=font)
            tw = box[2] - box[0]
            left = int(cx - tw / 2)
            if left < 2:
                left = 2
            if left + tw > w - 2:
                left = w - 2 - tw
            d.text((left, label_y - int(7 * sy)), lab, font=font, fill=(18, 24, 34))
    img.save(OUT)
    print("已输出：%s" % OUT)
    print("映射：ZRA->ZA  HY4->Hy4  QW->Qwen  DBO->Doubao  GEM->Gemini")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
