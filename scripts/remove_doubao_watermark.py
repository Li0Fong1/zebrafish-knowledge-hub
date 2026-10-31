"""擦掉豆包 / 即梦这类生图工具压在角落的 "XXAI生成" 水印。

为什么不是"整块模糊"也不是"整块填充"
------------------------------------
水印是**半透明白字 + 淡投影**。这里先用大窗口中值滤波估出"没有文字时长什么样"
（`base`），把明显亮于 base 的像素认成笔画（限制在水印框内 + 去掉零星噪点 + 膨胀），
再只把这些笔画像素换成 base，最后用羽化 alpha 回贴。
好处：文字以外的岩石/水草结构原样保留，不会出现一块平滑的"涂抹感"。

用法
----
    python -X utf8 scripts/remove_doubao_watermark.py --src in.png --out out.png
    python -X utf8 scripts/remove_doubao_watermark.py --src in.png --box 1740,1082,1938,1154
"""
from __future__ import annotations

import argparse
import os

import numpy as np
from PIL import Image
from scipy import ndimage


def default_box(w: int, h: int) -> tuple[int, int, int, int]:
    """右下角一块：宽约 10% 图宽、高约 5% 图高。"""
    bw, bh = max(140, int(w * 0.10)), max(46, int(h * 0.05))
    return w - bw - int(w * 0.02), h - bh - int(h * 0.02), w - int(w * 0.02), h - int(h * 0.03)


def inpaint_text(im: Image.Image, box: tuple[int, int, int, int], thr: float = 11.0,
                 base_win: int = 31, grow: int = 4, feather: float = 1.4) -> Image.Image:
    w, h = im.size
    l, t, r, b = box
    l, t, r, b = max(0, l), max(0, t), min(w, r), min(h, b)
    if r - l < 8 or b - t < 8:
        raise SystemExit(f"[err] 水印框太小：{box}")

    arr = np.asarray(im).astype(np.float32)
    pad = 32
    L, T, R, B = max(0, l - pad), max(0, t - pad), min(w, r + pad), min(h, b + pad)
    reg = arr[T:B, L:R]

    base = np.stack(
        [ndimage.median_filter(reg[:, :, c], size=base_win, mode="nearest") for c in range(3)],
        axis=2,
    )
    diff = reg.mean(axis=2) - base.mean(axis=2)

    mask = diff > thr
    inner = np.zeros_like(mask)
    inner[t - T:b - T, l - L:r - L] = True          # 只在用户给的框里找字
    mask &= inner
    lab, n = ndimage.label(mask)
    if n:
        sizes = ndimage.sum(mask, lab, range(1, n + 1))
        mask = np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s >= 18])
    if not mask.any():
        print("[warn] 框内没找到明显亮于背景的笔画，退化为整框中值填充")
        mask = inner
    mask = ndimage.binary_dilation(mask, iterations=grow)

    alpha = ndimage.gaussian_filter(mask.astype(np.float32), feather)[:, :, None]
    out = arr.copy()
    out[T:B, L:R] = reg * (1.0 - alpha) + base * alpha
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--box", default="", help="left,top,right,bottom（原图坐标）")
    ap.add_argument("--thr", type=float, default=11.0, help="笔画阈值，水印更淡就调小")
    ap.add_argument("--win", type=int, default=31, help="估背景的中值窗口（越大越忽略细结构）")
    a = ap.parse_args()

    im = Image.open(a.src).convert("RGB")
    box = tuple(int(v) for v in a.box.split(",")) if a.box else default_box(*im.size)
    if len(box) != 4:
        print("[err] --box 需要 4 个整数")
        return 2

    out_im = inpaint_text(im, box, thr=a.thr, base_win=a.win)
    out = a.out or os.path.splitext(a.src)[0] + "_nowm.png"
    out_im.save(out)
    print(f"[ok] {os.path.basename(a.src)} {im.size[0]}x{im.size[1]}  水印框={box}  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
