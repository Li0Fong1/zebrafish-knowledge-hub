# -*- coding: utf-8 -*-
"""Patch panel (A) of the Fig. S4 raster so that it stops contradicting the text.

The figure asserted "DeepSeek - Remove paragraphs irrelevant to scientific
knowledge", while the cleaning stage in rag/text_clean.py contains no model call
at all: it is a deterministic, position-aware rule set.  This script repaints the
caption block under the icon, replaces the DeepSeek mark with a gear, and leaves
every other pixel of the figure untouched.

Usage: python scripts/patch_figS4_panelA.py [--write]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "_docx_media" / "image3.jpeg"
OUT = ROOT / "_docx_media" / "figS4_patched.jpeg"

BG = (247, 248, 252)
INK = (26, 26, 26)
FONT = "C:/Windows/Fonts/arial.ttf"

# geometry measured from the original raster (1280 x 1603)
ICON_BOX = (326, 104, 470, 210)      # x0, y0, x1, y1  - DeepSeek mark
CAP_BOX = (262, 212, 532, 298)       # caption block under the mark
CX = 396                             # horizontal centre of the step
LINES = [
    "Author-defined rules",
    "Remove references, acknowledgements",
    "and page furniture",
]


def draw_gear(img: Image.Image, cx: int, cy: int, h: int) -> None:
    """Gear in the same line-art idiom as the other icons of panel (A).

    Geometry mirrors ic_gear() in scripts/make_figS4_pipelines_en.py: a ring of
    radius 0.30 h with 0.17 h squares on a 0.41 h pitch and a solid hub.
    Drawn at 6x and reduced, so the edges stay clean at 220 dpi.
    """
    s = 6
    R, sq, pitch, hub, lw = 0.30 * h, 0.17 * h, 0.41 * h, 0.12 * h, max(2, 0.021 * h)
    canvas = int((pitch + sq) * 2 * s) + 8
    big = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    c = canvas / 2
    blue = (43, 108, 246, 255)
    # body: white disc with a blue rim
    d.ellipse([c - R * s, c - R * s, c + R * s, c + R * s], fill=BG + (255,),
              outline=blue, width=int(lw * s))
    # eight teeth
    half = sq * s / 2
    for k in range(8):
        a = np.pi / 2 * k / 2
        px, py = c + pitch * s * np.cos(a), c + pitch * s * np.sin(a)
        d.rectangle([px - half, py - half, px + half, py + half], fill=blue)
    # redraw the rim so the teeth read as attached, then the hub
    d.ellipse([c - R * s, c - R * s, c + R * s, c + R * s], outline=blue, width=int(lw * s))
    d.ellipse([c - hub * s, c - hub * s, c + hub * s, c + hub * s], fill=blue)
    size = int(canvas / s)
    big = big.resize((size, size), Image.LANCZOS)
    img.paste(big, (int(cx - size / 2), int(cy - size / 2)), big)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    im = Image.open(SRC).convert("RGB")
    d = ImageDraw.Draw(im)

    # 1. erase the DeepSeek mark and the old caption
    d.rectangle(ICON_BOX, fill=BG)
    d.rectangle(CAP_BOX, fill=BG)

    # 2. gear, centred on the old icon
    draw_gear(im, CX, 158, 92)

    # 3. new caption: pick the size whose ink width matches the original lines
    size = 18                                  # matched to the original 18 px Arial
    for cand in range(24, 11, -1):
        f = ImageFont.truetype(FONT, cand)
        if max(f.getlength(t) for t in LINES) <= 330:
            size = cand
            break
    f = ImageFont.truetype(FONT, size)
    print(f"font size chosen: {size}px, widths: "
          + ", ".join(f"{t!r}={f.getlength(t):.0f}" for t in LINES))
    for line, yc in zip(LINES, (231, 257, 283)):
        w = d.textlength(line, font=f)
        d.text((CX - w / 2, yc - size * 0.62), line, font=f, fill=INK)

    if not args.write:
        im.save(OUT.with_name("_figS4_preview.png"))
        print(f"preview -> {OUT.with_name('_figS4_preview.png')}")
        return 0
    im.save(OUT, quality=96, subsampling=0)
    print(f"written -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
