"""用在线生图服务（Pollinations / Flux）生成首页背景图。

背景
----
本项目本身没有接入生图模型（只有 DeepSeek 文本接口，本机 torch 为 CPU 版且无 diffusers），
所以走 Pollinations 的免鉴权 text-to-image 接口（Flux 系列模型）。

用法：
    python -X utf8 scripts/gen_bg_pollinations.py --probe
    python -X utf8 scripts/gen_bg_pollinations.py --seed 42
    python -X utf8 scripts/gen_bg_pollinations.py --seed 7 --width 1920 --height 1200
    python -X utf8 scripts/gen_bg_pollinations.py --prompt-file docs/背景图提示词.md

产物落在 解析文件/bg_candidates/gen_<seed>.jpg（不直接覆盖线上背景，先人工挑）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import urllib.parse
import urllib.request

OUT_DIR = os.path.join("解析文件", "bg_candidates")
UA = "ZebrafishHubBgGen/1.0"

# ---------------------------------------------------------------- 提示词
# 正向：把原素材 `斑马鱼背景图.jpeg` 的"特色"逐条翻译成英文，并显式要求"完整、无面板"
PROMPT_ZH = (
    "宽幅水下场景背景插画，用于科研网站首页。淡雅水彩风，浅蓝到天青的水体竖直渐变；"
    "顶部是水面，阳光穿过水面形成柔和光束向下扩散；左右两侧各有茂密的绿色水草（细长叶+少量阔叶），"
    "配圆润的灰色岩石；底部是浅色沙床与细小砂粒；水中有几条斑马鱼（蓝白横向条纹）游动；"
    "右上角有一个半透明的 DNA 双螺旋装饰；零散小气泡；整体明亮通透、留白多、中央区域干净。"
    "整张图必须完整连续，不能有文字、水印、边框、白框、磨砂面板、留白方块。"
)

PROMPT_EN = (
    "Bright high-key underwater illustration for a science website, soft watercolor and flat vector mix, "
    "white to pale aqua to light sky-blue vertical gradient, very light, airy and clean, "
    "gentle wide sunbeams from the water surface at the very top, "
    "lush green seaweed and slender aquatic plants along the far left and far right edges, "
    "smooth round pale grey rocks at the bottom left and bottom right corners, "
    "light beige sandy seabed along the bottom edge, "
    "a few small zebrafish with dark blue and white horizontal stripes swimming on the left and right, "
    "a faint pale blue DNA double helix line-art ornament in the top right corner, "
    "tiny air bubbles, wide clean empty pale water in the center, plenty of negative space. "
    "Full-bleed continuous background, no text, no frame, no border, no white rectangle, "
    "no frosted glass panel, no blank placeholder block, no ui mockup."
)

NEGATIVE = (
    "text, letters, watermark, logo, signature, frame, border, white rectangle, "
    "frosted glass panel, placeholder box, ui mockup, template with blank panel, "
    "dark, gloomy, murky, deep ocean, night, underwater cave, coral reef, "
    "oversaturated, cluttered, heavy shadows, comic outlines"
)


def build_prompt(extra: str = "") -> str:
    p = PROMPT_EN
    if extra:
        p += ", " + extra
    return p


def generate(prompt: str, width: int, height: int, seed: int, model: str,
             timeout: int = 300) -> bytes:
    q = urllib.parse.urlencode({
        "width": width, "height": height, "seed": seed,
        "model": model, "nologo": "true", "enhance": "false",
        "negative_prompt": NEGATIVE,
    })
    url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(prompt)}?{q}"
    headers = {"User-Agent": UA}
    # 2026-10-22：免费匿名调用开始返回 402 Payment Required。
    # 到 https://auth.pollinations.ai 领一个免费 token 后设置环境变量即可：
    #   $env:POLLINATIONS_TOKEN="pk_..."
    tok = os.getenv("POLLINATIONS_TOKEN", "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
        print("[auth] 使用 POLLINATIONS_TOKEN")
    else:
        print("[auth] 未设置 POLLINATIONS_TOKEN，可能遇到 402")
    print(f"[req] model={model} {width}x{height} seed={seed}")
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if len(data) < 5000:
        raise RuntimeError(f"返回内容过小（{len(data)} B），可能被限流：{data[:200]!r}")
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--width", type=int, default=1536)
    ap.add_argument("--height", type=int, default=960)
    ap.add_argument("--model", default="flux")
    ap.add_argument("--extra", default="", help="追加在提示词末尾的补充描述")
    ap.add_argument("--prompt-file", default="", help="从 md/txt 里读取正向提示词（优先）")
    ap.add_argument("--probe", action="store_true", help="只测连通性，生成 512x320 小图")
    ap.add_argument("--batch", action="store_true", help="按 --seeds 批量生成，便于挑图")
    ap.add_argument("--seeds", default="1,7,42,88", help="批量模式的 seed 列表")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    if a.prompt_file and os.path.exists(a.prompt_file):
        txt = open(a.prompt_file, encoding="utf-8").read()
        m = re.search(r"```(?:text)?\n(.*?)```", txt, re.S)
        prompt = (m.group(1) if m else txt).strip()
        print(f"[prompt] 来自 {a.prompt_file}，{len(prompt)} 字符")
    else:
        prompt = build_prompt(a.extra)

    w, h = (512, 320) if a.probe else (a.width, a.height)
    os.makedirs(OUT_DIR, exist_ok=True)

    seeds = [a.seed]
    if a.batch:
        seeds = [int(s) for s in a.seeds.split(",") if s.strip()]

    ok = 0
    for sd in seeds:
        dst = (a.out if (a.out and len(seeds) == 1)
               else os.path.join(OUT_DIR, f"gen_{'probe' if a.probe else sd}.jpg"))
        for attempt in range(3):
            try:
                t0 = time.time()
                data = generate(prompt, w, h, sd, a.model)
                with open(dst, "wb") as f:
                    f.write(data)
                print(f"[ok] {dst}  {len(data)//1024} KB  {time.time()-t0:.1f}s")
                ok += 1
                break
            except Exception as e:  # noqa: BLE001
                print(f"[retry {attempt+1}/3] seed={sd} {type(e).__name__}: {e}")
                time.sleep(6 + attempt * 8)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
