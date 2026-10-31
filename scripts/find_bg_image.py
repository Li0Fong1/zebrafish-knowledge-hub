"""在 Wikimedia Commons 搜索可用的背景图（自由授权），列出候选并下载缩略图预览。

用法：
    python -X utf8 scripts/find_bg_image.py search "underwater water plants"
    python -X utf8 scripts/find_bg_image.py thumb <序号>

输出候选表格：标题 / 尺寸 / 授权 / 直链。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

API = "https://commons.wikimedia.org/w/api.php"
UA = "ZebrafishHubBgPicker/1.0 (research; contact: local)"
OUT_DIR = os.path.join("解析文件", "bg_candidates")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read()


def search(term: str, limit: int = 12) -> list[dict]:
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": f"filetype:bitmap {term}",
        "gsrnamespace": "6",
        "gsrlimit": str(limit),
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": "1600",
    }
    data = json.loads(_get(API + "?" + urllib.parse.urlencode(params)))
    pages = (data.get("query") or {}).get("pages") or {}
    out = []
    for pid, p in pages.items():
        info = (p.get("imageinfo") or [{}])[0]
        if not info:
            continue
        meta = info.get("extmetadata") or {}

        def m(key: str) -> str:
            v = meta.get(key) or {}
            return str(v.get("value") or "")

        out.append(
            {
                "title": p.get("title", ""),
                "width": info.get("width", 0),
                "height": info.get("height", 0),
                "mime": info.get("mime", ""),
                "url": info.get("url", ""),
                "thumb": info.get("thumburl", ""),
                "license": m("LicenseShortName"),
                "artist": m("Artist"),
            }
        )
    out.sort(key=lambda x: -(x["width"] * x["height"]))
    return out


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: find_bg_image.py search <term> | candidates | thumb <n>")
        return
    cmd = sys.argv[1]
    os.makedirs(OUT_DIR, exist_ok=True)
    store = os.path.join(OUT_DIR, "candidates.json")

    if cmd == "search":
        term = " ".join(sys.argv[2:])
        rows = search(term)
        with open(store, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print(f"# {term} -> {len(rows)} hits")
        for i, r in enumerate(rows):
            if r["width"] < 1600:
                continue
            print(
                f"[{i:02d}] {r['width']}x{r['height']}  {r['license'][:22]:<22} "
                f"{r['title'][5:70]}"
            )
        return

    if cmd == "candidates":
        rows = json.load(open(store, encoding="utf-8"))
        for i, r in enumerate(rows):
            print(i, r["width"], r["height"], r["license"], r["url"])
        return

    if cmd == "thumb":
        rows = json.load(open(store, encoding="utf-8"))
        idx = int(sys.argv[2])
        r = rows[idx]
        ext = ".jpg" if "jpeg" in r["mime"] else ".png"
        path = os.path.join(OUT_DIR, f"c{idx:02d}{ext}")
        with open(path, "wb") as f:
            f.write(_get(r["thumb"] or r["url"]))
        print("saved", path, r["title"], r["license"])
        return


if __name__ == "__main__":
    main()
