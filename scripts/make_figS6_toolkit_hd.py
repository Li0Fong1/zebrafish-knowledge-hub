# -*- coding: utf-8 -*-
"""Figure S6 — Toolkit integrated in Zebrafish Agent (high-definition remake).

Content source (authoritative): the six screenshots supplied by the author on
2026-11-11, SECOND BATCH (13:55).  Five tool pages carry REAL query results;
the sixth is the top navigation bar with the Tools drop-down expanded.

Clarity strategy (no content is redrawn or invented):
  * The first batch contained three screenshots downsampled to 830x496.
    This second batch supplies ALL five tool pages at native 1920x1146, so
    every plate is reproduced at ~1:1 with only a light unsharp mask —
    no interpolation blur at all.
  * Every plate is placed at ~1:1 source resolution on a 4200 px canvas, so
    the lettering inside the screenshots stays legible when the figure is
    placed at journal column width.
  * A red rectangle marks the "Tools" item in the navigation bar.

Typography: Times New Roman throughout (regular / bold), matching the
British-academic house style requested for this figure set.

Outputs
  Figure/EN_journal_figures/FigS6_Toolkit_HD.png
  output/figS6_hd/<tool>_plate.png         (five enhanced plates, standalone)
  output/figS6_hd/build_report.json        (provenance + geometry)
"""
from __future__ import annotations

import json
import pathlib

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLIP = pathlib.Path(r"C:/Users/Lenovo/.workbuddy/clipboard-images")
OUT_FIG = ROOT / "Figure" / "EN_journal_figures"
OUT_WORK = ROOT / "output" / "figS6_hd"
OUT_FIG.mkdir(parents=True, exist_ok=True)
OUT_WORK.mkdir(parents=True, exist_ok=True)

# ── geometry ──────────────────────────────────────────────────────────────
W = 4200
MARGIN = 100
PLATE_W = 1950
COL_GAP = 100
PLATE_H = round(PLATE_W * 1146 / 1920)          # 1164
COL_X = [MARGIN, MARGIN + PLATE_W + COL_GAP]    # 100, 2150
NAV_SCALE = 2.0
NAV_W, NAV_H = 3840, 630
NAV_X = (W - NAV_W) // 2                         # 180

TITLE_SIZE, SUB_SIZE, FOOT_SIZE = 78, 52, 58
TITLE_H, SUB_H, ROW_GAP = 100, 74, 96
TOP_PAD, NAV_GAP, ARROW_H, ARROW_GAP, FOOT_GAP, BOTTOM_PAD = 80, 56, 116, 46, 130, 90

ROW_H = TITLE_H + SUB_H + PLATE_H + ROW_GAP
H = (TOP_PAD + NAV_H + NAV_GAP + ARROW_H + ARROW_GAP
     + 3 * ROW_H + FOOT_GAP + FOOT_SIZE + BOTTOM_PAD)

# ── fonts ─────────────────────────────────────────────────────────────────
F_REG = r"C:/Windows/Fonts/times.ttf"
F_BOLD = r"C:/Windows/Fonts/timesbd.ttf"
f_title = ImageFont.truetype(F_BOLD, TITLE_SIZE)
f_sub = ImageFont.truetype(F_REG, SUB_SIZE)
f_foot = ImageFont.truetype(F_REG, FOOT_SIZE)

INK = "#1B2733"
GRAY = "#4A5561"
RED = "#D81E1E"
BORDER = "#C9D3DD"

# ── source files (verified by visual inspection, 2026-11-11) ──────────────
TOOLS = [
    dict(no=1, name="BLAST",
         desc="Search sequence similarity against reference databases",
         file="clipboard-2026-11-11T13-55-30-422Z-16fc7840.jpg", col=0, row=0),
    dict(no=2, name="GO annotation",
         desc="Retrieve GO annotations for a query gene",
         file="clipboard-2026-11-11T13-55-30-429Z-ae9ada13.jpg", col=1, row=0),
    dict(no=3, name="Pathway enrichment",
         desc="Perform pathway enrichment analysis for a gene list",
         file="clipboard-2026-11-11T13-55-30-432Z-ea0c02c9.jpg", col=0, row=1),
    dict(no=4, name="sgRNA design",
         desc="Design and rank candidate sgRNAs",
         file="clipboard-2026-11-11T13-55-30-425Z-8384dca6.jpg", col=1, row=1),
    dict(no=5, name="Primer design",
         desc="Design primer pairs for a target gene",
         file="clipboard-2026-11-11T13-55-30-434Z-9adef9bb.jpg", col=0, row=2),
]
NAV_FILE = "clipboard-2026-11-11T13-55-30-436Z-cdbb76b0.jpg"
# red box around the highlighted "Tools" chip in the ORIGINAL 1920x315 nav image
TOOLS_CHIP = (866, 22, 954, 86)


def enhance(path: pathlib.Path, key: str) -> tuple[Image.Image, dict]:
    """Upsample to the plate size; sharpen deterministically. No content edits."""
    im = Image.open(path).convert("RGB")
    src_w, src_h = im.size
    plate = im.resize((PLATE_W, PLATE_H), Image.LANCZOS)
    # the two 1920-wide originals need only a whisper of sharpening;
    # the three 830-wide ones need a little more to keep glyph edges crisp
    if src_w < 1200:
        plate = plate.filter(ImageFilter.UnsharpMask(radius=1.15, percent=145, threshold=2))
        plate = ImageEnhance.Contrast(plate).enhance(1.05)
        plate = ImageEnhance.Sharpness(plate).enhance(1.12)
    else:
        plate = plate.filter(ImageFilter.UnsharpMask(radius=0.6, percent=80, threshold=3))
        plate = ImageEnhance.Sharpness(plate).enhance(1.06)
    plate.save(OUT_WORK / f"{key}_plate.png")
    return plate, dict(source=str(path), source_size=[src_w, src_h],
                       plate_size=[PLATE_W, PLATE_H],
                       scale=round(PLATE_W / src_w, 3),
                       sharpening="strong" if src_w < 1200 else "light")


def centre_text(dr: ImageDraw.ImageDraw, cx: float, y: float, text: str,
                font: ImageFont.FreeTypeFont, fill: str) -> None:
    box = dr.textbbox((0, 0), text, font=font)
    dr.text((cx - (box[2] - box[0]) / 2, y), text, font=font, fill=fill)


def main() -> int:
    canvas = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(canvas)
    report = dict(canvas=[W, H], plate=[PLATE_W, PLATE_H], font="Times New Roman",
                  tools=[], nav={}, red_box=[])

    # ── navigation bar ────────────────────────────────────────────────────
    nav_src = Image.open(CLIP / NAV_FILE).convert("RGB")
    assert nav_src.size == (1920, 315), nav_src.size
    nav = nav_src.resize((NAV_W, NAV_H), Image.LANCZOS)
    nav = nav.filter(ImageFilter.UnsharpMask(radius=0.7, percent=95, threshold=3))
    nav_y = TOP_PAD
    canvas.paste(nav, (NAV_X, nav_y))
    x0, y0, x1, y1 = TOOLS_CHIP
    box = [NAV_X + int(x0 * NAV_SCALE) - 10, nav_y + int(y0 * NAV_SCALE) - 8,
           NAV_X + int(x1 * NAV_SCALE) + 10, nav_y + int(y1 * NAV_SCALE) + 8]
    dr.rectangle(box, outline=RED, width=9)
    report["nav"] = dict(source=str(CLIP / NAV_FILE), source_size=list(nav_src.size),
                         placed=[NAV_X, nav_y, NAV_W, NAV_H], scale=NAV_SCALE)
    report["red_box"] = box

    # ── downward arrow ────────────────────────────────────────────────────
    ay = nav_y + NAV_H + NAV_GAP
    cx = W // 2
    stem_w, head_w, head_h = 22, 74, 52
    dr.rectangle([cx - stem_w // 2, ay, cx + stem_w // 2, ay + ARROW_H - head_h], fill=INK)
    dr.polygon([(cx - head_w // 2, ay + ARROW_H - head_h),
                (cx + head_w // 2, ay + ARROW_H - head_h),
                (cx, ay + ARROW_H)], fill=INK)

    # ── five tool plates ──────────────────────────────────────────────────
    rows_top = ay + ARROW_H + ARROW_GAP
    for t in TOOLS:
        col_x = COL_X[t["col"]]
        row_y = rows_top + t["row"] * ROW_H
        centre = col_x + PLATE_W / 2
        centre_text(dr, centre, row_y, f"Tool {t['no']}: {t['name']}", f_title, INK)
        centre_text(dr, centre, row_y + TITLE_H + 2, t["desc"], f_sub, GRAY)
        plate, meta = enhance(CLIP / t["file"], f"tool{t['no']}")
        py = row_y + TITLE_H + SUB_H
        canvas.paste(plate, (col_x, py))
        dr.rectangle([col_x - 1, py - 1, col_x + PLATE_W, py + PLATE_H],
                     outline=BORDER, width=3)
        report["tools"].append(dict(no=t["no"], name=t["name"], desc=t["desc"],
                                    col=t["col"], row=t["row"],
                                    pos=[col_x, py], **meta))

    # ── footer ────────────────────────────────────────────────────────────
    fy = rows_top + 3 * ROW_H + FOOT_GAP
    dr.text((MARGIN, fy), "Toolkit integrated in Zebrafish Agent.",
            font=f_foot, fill=INK)

    dst = OUT_FIG / "FigS6_Toolkit_HD.png"
    canvas.save(dst, optimize=True)
    (OUT_WORK / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", dst, canvas.size, dst.stat().st_size, "bytes")
    for t in report["tools"]:
        print("  tool%-2d %-20s src=%s scale=%.3f %s" %
              (t["no"], t["name"], t["source_size"], t["scale"], t["sharpening"]))
    print("  red box", report["red_box"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
