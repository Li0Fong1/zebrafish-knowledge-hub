# -*- coding: utf-8 -*-
"""把大图里的 C1/C2 面板替换成新版（去掉顶部标题的 C1/C2 图）。

做法：
  1. 读 big_figure.jpg（含 A/B/C1/C2 四面板）与 new_c1c2.png（新版 C1/C2）。
  2. 自动找 C1 面板的上边界：从图中部向下扫描，找到"C1"标题所在行——
     大图里 C1/C2 区域是底部约 40%，B 面板背景是浅蓝色，C 区域是白底，
     故用「连续白底行」的起点作为替换区上边界。
  3. 把 new_c1c2 等比缩放到大图宽度，贴到该上边界到图底，裁掉大图原 C 区。
  4. 输出 big_figure_replaced.png。

保守起见：先打印检测到的边界与尺寸，人工可核对；边界检测失败则不改，报错。
依赖：Pillow（本机 Python 通常自带）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "output" / "c1c2_replace"
BIG = WORK / "big_figure.jpg"
NEW = WORK / "new_c1c2.png"
OUT = WORK / "big_figure_replaced.png"


def _blue_at(img: Image.Image, y: int) -> float:
    w = img.width
    px = img.convert("RGB").load()
    step = max(1, w // 150)
    blue = cnt = 0
    for x in range(0, w, step):
        r, g, b = px[x, y]
        cnt += 1
        if b > r + 4 and b > 235 and r > 200:
            blue += 1
    return blue / cnt if cnt else 0.0


def find_c_top(img: Image.Image) -> int:
    """找 C 区域上边界：B 面板为浅蓝底，C 区为白底。
    统计每行「浅蓝像素」占比（B=蓝>红且整体很亮），找连续浅蓝结束的第一行。"""
    w, h = img.size
    rgb = img.convert("RGB")
    px = rgb.load()
    step = max(1, w // 150)

    def blue_ratio(y: int) -> float:
        blue = 0
        cnt = 0
        for x in range(0, w, step):
            r, g, b = px[x, y]
            cnt += 1
            # 浅蓝白底：b 明显 > r，且整体很亮
            if b > r + 4 and b > 235 and r > 200:
                blue += 1
        return blue / cnt if cnt else 0.0

    # 从 55% 往下：若上一行还蓝、这一行已不蓝（<8%），即为 B→C 边界
    for y in range(int(h * 0.55), int(h * 0.80)):
        if blue_ratio(y - 2) > 0.25 and blue_ratio(y) < 0.08:
            return y
    return -1


def main() -> int:
    if not BIG.is_file() or not NEW.is_file():
        print("缺少输入图，请先运行 _copy_c1c2_images.py")
        return 1
    big = Image.open(BIG).convert("RGB")
    new = Image.open(NEW).convert("RGB")
    bw, bh = big.size
    nw, nh = new.size
    print("大图尺寸：%d x %d" % (bw, bh))
    print("新C1C2尺寸：%d x %d" % (nw, nh))

    top = find_c_top(big)
    print("--- 蓝底比例诊断（供核对边界）---")
    for pct in range(50, 76, 3):
        y = int(bh * pct / 100)
        if y < bh:
            print("  y=%d (%d%%) blue=%.2f" % (y, pct, _blue_at(big, y)))
    if top < 0:
        print("未检测到 C 区上边界，未改动。请人工指定。")
        return 2
    print("检测到 C 区上边界 y=%d (%.1f%% 高度)" % (top, 100.0 * top / bh))

    # 新 C1C2 等比缩放到大图宽度
    scale = bw / nw
    new_h = int(nh * scale)
    new_scaled = new.resize((bw, new_h), Image.LANCZOS)
    print("新C1C2缩放后：%d x %d" % (bw, new_h))

    # 若缩放后高度与 C 区高度差异大，按 C 区高度强制拉伸会变形——
    # 这里选择：保持宽度铺满，高度以缩放后为准，不足则用白底补齐到图底。
    canvas = big.copy()
    c_height = bh - top
    if new_h > c_height:
        # 新图比 C 区高：裁掉新图底部多余部分
        new_scaled = new_scaled.crop((0, 0, bw, c_height))
        new_h = c_height
    canvas.paste(new_scaled, (0, top))
    # 若新图比 C 区矮，下方留原图（通常不会发生）
    canvas.save(OUT)
    print("已输出：%s" % OUT)
    print("替换区：y=%d..%d，新图高 %d" % (top, bh, new_h))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
