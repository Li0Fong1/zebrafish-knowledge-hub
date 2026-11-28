# -*- coding: utf-8 -*-
"""发布前自检：静态资源的 ?v= 版本号是否跟得上文件改动。

背景：2026-11-09 起 /static/ 下带 ?v= 的 URL 改为浏览器长缓存
（Cache-Control: public, max-age=604800, immutable）。好处是弱网下重复访问
不用再拉 1.6 MB，代价是：**改了文件却没提升 ?v=，用户会继续用旧缓存最多 7 天**。

这个脚本就是那道保险：比对 index.html 里的 ?v= 版本号 与 web/ 下静态文件的
最新修改时间，版本号落后就报错退出（非 0），可挂到发布流程里。

用法：
    python scripts/check_static_version.py            # 检查，落后则退出码 1
    python scripts/check_static_version.py --quiet    # 只输出结论
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
INDEX = WEB / "index.html"
# 只关心会被长缓存的目录
WATCH_DIRS = ("css", "js", "assets")
VERSION_RE = re.compile(r"\?v=(\d{8})([a-z]?)")


def latest_version(index_text: str) -> tuple[dt.date, str]:
    """index.html 里出现过的最大版本号（按 日期+字母后缀 排序）。"""
    found = VERSION_RE.findall(index_text)
    if not found:
        return dt.date.min, ""
    key = max(found, key=lambda t: (t[0], t[1]))
    return dt.datetime.strptime(key[0], "%Y%m%d").date(), key[1]


def newest_file_mtime() -> tuple[dt.date, Path]:
    newest_date, newest_path = dt.date.min, None
    for d in WATCH_DIRS:
        base = WEB / d
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in (".css", ".js", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".woff2"):
                continue
            d2 = dt.date.fromtimestamp(f.stat().st_mtime)
            if d2 > newest_date:
                newest_date, newest_path = d2, f
    return newest_date, newest_path


def main() -> int:
    quiet = "--quiet" in sys.argv
    if not INDEX.is_file():
        print(f"[版本自检] 找不到 {INDEX}")
        return 2

    v_date, v_suffix = latest_version(INDEX.read_text(encoding="utf-8"))
    m_date, m_path = newest_file_mtime()

    if not quiet:
        print(f"[版本自检] index.html 版本号：{v_date:%Y-%m-%d}{v_suffix}")
        print(f"[版本自检] 静态文件最新修改：{m_date:%Y-%m-%d}  {m_path.relative_to(ROOT) if m_path else '-'}")

    if m_date > v_date:
        print(
            f"[版本自检] 失败：静态文件在 {m_date} 有改动，但 index.html 的 ?v= 还停在 "
            f"{v_date}。请把所有 ?v= 提升为 {m_date:%Y%m%d}a，否则用户最长 7 天看不到新样式。"
        )
        return 1

    print("[版本自检] 通过：版本号不落后于静态文件改动。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
