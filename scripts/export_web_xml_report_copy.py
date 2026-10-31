# -*- coding: utf-8 -*-

"""复制 web_xml_site_report 到 项目阶段/05_数据与库/归档/data_export 并生成 HTML 便于查看。"""

from __future__ import annotations



import html

import shutil

import sys

from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]

SRC_MD = ROOT / "zebrafish_package" / "结果" / "web_xml_site_report.md"

SRC_JSON = ROOT / "zebrafish_package" / "结果" / "web_xml_site_report.json"

OUT = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export"

OUT.mkdir(parents=True, exist_ok=True)





def normalize_markdown_text(raw: str) -> str:

    """去掉 BOM/控制字符，统一为 LF。双重 BOM 会导致 Cursor 文本视图空白。"""

    text = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")

    return "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)





def main() -> int:

    if not SRC_MD.is_file():

        print(f"源文件不存在: {SRC_MD}")

        return 1

    text = normalize_markdown_text(SRC_MD.read_text(encoding="utf-8-sig"))

    if len(text.strip()) < 100:

        print(f"源文件内容过短 ({len(text)} bytes)，请重新运行 yes_md_web_xml_hunt")

        return 1

    # 与项目其他 .md 一致：UTF-8 无 BOM + LF（见 .vscode/settings.json files.encoding=utf8）

    for target in (OUT / "web_xml_site_report.md", ROOT / "web_xml_site_report.md"):

        target.write_text(text, encoding="utf-8", newline="\n")

    (OUT / "web_xml_site_report.txt").write_text(text, encoding="utf-8", newline="\n")

    if SRC_JSON.is_file():

        shutil.copy2(SRC_JSON, OUT / "web_xml_site_report.json")

    html_page = (

        "<!DOCTYPE html><html><head><meta charset='utf-8'>"

        "<title>XML 网点汇报</title>"

        "<style>body{font-family:Segoe UI,sans-serif;max-width:1200px;margin:2em auto;"

        "padding:0 1.5em;line-height:1.6}pre{white-space:pre-wrap;word-wrap:break-word}</style>"

        "</head><body><pre>"

        + html.escape(text)

        + "</pre></body></html>"

    )

    (OUT / "web_xml_site_report.html").write_text(html_page, encoding="utf-8")

    bom = (OUT / "web_xml_site_report.md").read_bytes()[:3]

    has_bom = bom == b"\xef\xbb\xbf"

    print(f"OK: {len(text)} chars, BOM={'yes' if has_bom else 'no'}")

    print(f"  {OUT / 'web_xml_site_report.md'}")

    print(f"  {OUT / 'web_xml_site_report.txt'}")

    print(f"  {OUT / 'web_xml_site_report.html'}")

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

