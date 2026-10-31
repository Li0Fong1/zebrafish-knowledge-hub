# -*- coding: utf-8 -*-
"""从 12.png 生成网站品牌资源（主 logo / 圆形徽标 / favicon / 触屏图标）。

用法（本地）：.venv\\Scripts\\python.exe scripts\\make_brand_assets.py
产出：
  web/assets/brand/zf-agent-mark.png         全图（含 ZEBRAFISH AGENT 字样），用于 hero / 空态 / 品牌卡
  web/assets/brand/zf-agent-emblem.png       圆形徽标方图（去掉底部文字），用于顶栏 logo
  web/assets/brand/favicon.png               64×64 站点图标
  web/assets/brand/apple-touch-icon.png      180×180 触屏图标
  docs/design/6.png, docs/design/12.png      设计参考图归档
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC_MARK = ROOT / "12.png"
SRC_HOME = ROOT / "6.png"
BRAND = ROOT / "web" / "assets" / "brand"
DESIGN = ROOT / "docs" / "design"


def _trim_alpha(img: Image.Image) -> Image.Image:
    """去掉纯白/近白背景边（保持简单：仅做轻微白边裁剪）。"""
    gray = img.convert("L")
    # 背景接近 250+，把 <245 视作内容
    mask = gray.point(lambda v: 255 if v < 245 else 0)
    box = mask.getbbox()
    return img.crop(box) if box else img


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    DESIGN.mkdir(parents=True, exist_ok=True)

    if not SRC_MARK.exists():
        raise SystemExit(f"缺少源图：{SRC_MARK}")
    mark = Image.open(SRC_MARK).convert("RGBA")
    w, h = mark.size
    print(f"12.png 原始尺寸: {w}x{h}")

    # 1) 全图：长边压到 900，保持比例
    full = mark.copy()
    if max(full.size) > 900:
        scale = 900 / max(full.size)
        full = full.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    full.save(BRAND / "zf-agent-mark.png", optimize=True)
    print(f"写出 zf-agent-mark.png {full.size}")

    # 2) 圆形徽标：截取上半部（只保留圆形徽标，去掉底部 "ZEBRAFISH AGENT" 字样），再居中裁成正方形
    emblem_src = mark.crop((0, 0, w, int(h * 0.72)))
    ew, eh = emblem_src.size
    side = min(ew, eh)
    left = (ew - side) // 2
    top = max(0, (eh - side) // 2 - int(side * 0.04))
    emblem = emblem_src.crop((left, top, left + side, min(eh, top + side)))
    emblem = emblem.resize((512, 512), Image.LANCZOS)
    emblem.save(BRAND / "zf-agent-emblem.png", optimize=True)
    print("写出 zf-agent-emblem.png 512x512")

    # 3) favicon 与触屏图标（白底，避免深色浏览器标签看不清）
    for name, size in (("favicon.png", 64), ("apple-touch-icon.png", 180)):
        canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        icon = emblem.resize((size, size), Image.LANCZOS)
        canvas.alpha_composite(icon)
        canvas.convert("RGB").save(BRAND / name, optimize=True)
        print(f"写出 {name} {size}x{size}")

    # 4) 设计参考图归档
    if SRC_HOME.exists():
        shutil.copy2(SRC_HOME, DESIGN / "6_home_design.png")
        print("归档 docs/design/6_home_design.png")
    shutil.copy2(SRC_MARK, DESIGN / "12_brand_mark.png")
    print("归档 docs/design/12_brand_mark.png")


if __name__ == "__main__":
    main()
