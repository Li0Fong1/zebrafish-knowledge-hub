# -*- coding: utf-8 -*-
"""Figure S4 — FULL four-panel figure, retypeset in Times New Roman / Arial.

The previous delivery replaced only panels C1/C2, which left the author's large
A/B figure "missing".  This script rebuilds the WHOLE figure:

  * Panels A and B (the two pipeline schematics) come from the author's raster
    (1280x1603).  Every text line is erased with a locally sampled background
    colour and re-set in a single font family.  Icons, arrows, the blue ranking
    bars and all layout geometry are left untouched — no content is redrawn.
  * Panels C1/C2 are the vector re-render from scripts/make_figS4_C1C2_fonts.py,
    so they already use the matching font and the project's real data.

Text-line boxes were detected programmatically (horizontal dilation +
connected components on the source raster) and each one was verified visually
against a numbered proof sheet before being mapped to its exact wording.

Outputs
    Figure/EN_journal_figures/FigS4_Full_TimesNewRoman.png
    Figure/EN_journal_figures/FigS4_Full_Arial.png
    output/figS4_c1c2/full_text_map.json
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = pathlib.Path(r"C:/Users/Lenovo/.workbuddy/clipboard-images/"
                   "clipboard-2026-11-11T13-55-30-438Z-9488df8b.jpg")
C1C2 = {
    "Times New Roman": ROOT / "Figure/EN_journal_figures/FigS4_C1C2_TimesNewRoman.png",
    "Arial": ROOT / "Figure/EN_journal_figures/FigS4_C1C2_Arial.png",
}
OUT_FIG = ROOT / "Figure" / "EN_journal_figures"
OUT_WORK = ROOT / "output" / "figS4_c1c2"
OUT_FIG.mkdir(parents=True, exist_ok=True)
OUT_WORK.mkdir(parents=True, exist_ok=True)

FONTS = {
    "Times New Roman": ("C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/timesbd.ttf"),
    "Arial": ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
}
INK = (26, 26, 26)
FALLBACK_BG = (247, 248, 252)

AB_H = 810                 # source rows occupied by panels A/B
OUT_W = 2400
S = OUT_W / 1280           # 1.875
GAP = 36

# index -> exact wording; kind controls weight.  Verified against the numbered
# proof sheet output/figS4_c1c2/AB_boxes.png.  Indices absent here are arrows,
# icons or the blue ranking bars and MUST NOT be erased.
TEXT_MAP = {
    0:  ("A", "label"),
    1:  ("Literature in", "title"),
    2:  ("Paragraphs cleaning", "title"),
    3:  ("Chunking", "title"),
    4:  ("Embedding", "title"),
    5:  ("FAISS index", "title"),
    6:  ("plain text", "title"),
    11: ("DeepSeek", "note"),
    12: ("Remove paragraphs irrelevant", "note"),
    13: ("Qwen3-Embedding-4B", "note"),
    14: ("to scientific knowledge", "note"),
    15: ("Rephrase user query", "title"),
    16: ("B", "label"),
    17: ("Vectorize texts", "title"),
    18: ("Retrieve texts", "title"),
    19: ("Knowledge database", "title"),
    20: ("User input", "title"),
    23: ("Lightweight model", "note"),
    27: ("(Qwen2.5-0.5B-Instruct)", "note"),
    28: ("Qwen3-Embedding-4B", "note"),
    29: ("Cloud-hosted model", "note"),
    30: ("(DeepSeek)", "note"),
    31: ("Generate answer", "title"),
    32: ("Summarize", "title"),
    33: ("Reranking texts", "title"),
    40: ("DeepSeek", "note"),
    41: ("bge-reranker-base", "note"),
}


def detect_boxes(arr_gray):
    ink = arr_gray[:AB_H, :] < 165
    dil = ndimage.binary_dilation(ink, structure=np.ones((1, 14), dtype=bool))
    lab, _ = ndimage.label(dil)
    boxes = []
    for sl in ndimage.find_objects(lab):
        if sl is None:
            continue
        y0, y1 = sl[0].start, sl[0].stop
        x0, x1 = sl[1].start, sl[1].stop
        h, w = y1 - y0, x1 - x0
        if 8 <= h <= 46 and 14 <= w <= 560:
            boxes.append([x0, y0, x1, y1])
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def fit_font(dr, text, path, target_h, lo=12, hi=74):
    """Pick the pixel size whose rendered ink height best matches the source."""
    best, best_err = None, 1e9
    for s in range(lo, hi + 1):
        f = ImageFont.truetype(path, s)
        bb = dr.textbbox((0, 0), text, font=f)
        h = bb[3] - bb[1]
        err = abs(h - target_h)
        if err < best_err:
            best, best_err = f, err
        if h > target_h * 1.25:
            break
    return best


def local_bg(arr, box):
    """Background colour sampled just above the line, so the pale blue gradient
    of the A/B panels is preserved rather than over-painted with flat white."""
    x0, y0, x1, y1 = box
    for ya, yb in ((y0 - 9, y0 - 4), (y1 + 4, y1 + 9)):
        if ya < 0:
            continue
        strip = arr[ya:yb, x0:x1].reshape(-1, 3)
        if strip.size == 0:
            continue
        strip = strip[strip.min(axis=1) > 225]
        if len(strip) < 8:
            continue
        col = np.median(strip, axis=0)
        if col.min() > 225:
            return tuple(int(c) for c in col)
    return FALLBACK_BG


def build(font_name):
    reg, bold = FONTS[font_name]
    src = Image.open(SRC).convert("RGB")
    assert src.size == (1280, 1603), src.size
    boxes = detect_boxes(np.asarray(src.convert("L")))
    assert len(boxes) == 42, len(boxes)

    ab = src.crop((0, 0, 1280, AB_H)).resize(
        (OUT_W, round(AB_H * S)), Image.LANCZOS)
    ab_arr = np.asarray(ab)
    dr = ImageDraw.Draw(ab)
    audit = []

    for i, (text, kind) in TEXT_MAP.items():
        x0, y0, x1, y1 = boxes[i]
        bg = local_bg(ab_arr, [round(x0 * S), round(y0 * S),
                               round(x1 * S), round(y1 * S)])
        bx = [round(x0 * S) - 5, round(y0 * S) - 4,
              round(x1 * S) + 5, round(y1 * S) + 4]
        dr.rectangle(bx, fill=bg)
        path = bold if kind in ("label", "title") else reg
        target_h = (y1 - y0) * S
        f = fit_font(dr, text, path, target_h)
        bb = dr.textbbox((0, 0), text, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        cx = (x0 + x1) / 2 * S
        cy = (y0 + y1) / 2 * S
        dr.text((cx - tw / 2 - bb[0], cy - th / 2 - bb[1]),
                text, font=f, fill=INK)
        audit.append(dict(index=i, text=text, kind=kind,
                          src_box=[x0, y0, x1, y1],
                          erase_box=bx, bg=list(bg), font_size=f.size))

    c = Image.open(C1C2[font_name]).convert("RGB")
    ch = round(c.height * OUT_W / c.width)
    c = c.resize((OUT_W, ch), Image.LANCZOS)

    H = ab.height + GAP + ch
    canvas = Image.new("RGB", (OUT_W, H), "white")
    canvas.paste(ab, (0, 0))
    canvas.paste(c, (0, ab.height + GAP))
    safe = font_name.replace(" ", "")
    dst = OUT_FIG / f"FigS4_Full_{safe}.png"
    canvas.save(dst, optimize=True)
    return dst, canvas.size, audit


def main() -> int:
    report = {}
    for fam in ("Times New Roman", "Arial"):
        p, size, audit = build(fam)
        report[fam] = dict(file=str(p.relative_to(ROOT)).replace("\\", "/"),
                           size=list(size), text_lines=audit)
        print("saved", p, size, p.stat().st_size, "bytes",
              "| retypeset lines:", len(audit))
    (OUT_WORK / "full_text_map.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
