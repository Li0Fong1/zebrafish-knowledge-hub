# -*- coding: utf-8 -*-
"""按服务器实测值校正 v8 论文与中文译稿中的数据库数字（2026-11-05）。

只替换 document.xml 里的字面串，zip 其余条目原样复制，格式/批注/紫色标注不受影响。
每个替换串都断言出现次数，出现次数不符直接失败，避免误改。
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

BASE = Path(r"D:\PycharmProjects\斑马鱼基因问答体\论文学习\有批注版")

PAPER = BASE / "AIzebrafish_manuscript20261102_批注整改_v8.docx"
ZH = BASE / "V8修订部分_中文译稿.docx"

PAPER_EDITS = [
    # 段 23：库规模改为当前实测（32,139 / 118,174 / 109,508）
    (
        "The initial build held 32,564 curated gene records, 145,439 aliases and "
        "80,212 cross-database identifier records",
        "The curated build holds 32,139 gene records, 118,174 aliases and "
        "109,508 cross-database identifier records",
    ),
    # 段 23：落到 GRCz12tu 的记录数与比例
    ("28,284 of them (86.9%)", "27,872 of them (86.7%)"),
    # 段 23：未落位记录数
    ("4,280 records", "4,267 records"),
    ("(13.1%)", "(13.3%)"),
    # 段 24：两个映射数字
    ("27,831 map onto 24,964", "27,872 map onto 24,963"),
]

ZH_EDITS = [
    (
        "初次建库收录 32,564 条基因记录、145,439 条别名与 80,212 条跨库标识符记录",
        "建库收录 32,139 条基因记录、118,174 条别名与 109,508 条跨库标识符记录",
    ),
    ("28,284 条（86.9%）", "27,872 条（86.7%）"),
    ("4,280 条记录（13.1%）", "4,267 条记录（13.3%）"),
    ("27,831 条", "27,872 条"),
    ("24,964 个", "24,963 个"),
]


def rewrite(path: Path, edits: list[tuple[str, str]]) -> None:
    src = zipfile.ZipFile(path)
    items = [(i, src.read(i.filename)) for i in src.infolist()]
    src.close()

    idx = next(k for k, (i, _) in enumerate(items) if i.filename == "word/document.xml")
    info, data = items[idx]
    xml = data.decode("utf-8")

    for old, new in edits:
        n = xml.count(old)
        if n != 1:
            sys.exit(f"[FAIL] {path.name}: {old!r} 出现 {n} 次（期望 1）")
        xml = xml.replace(old, new)

    items[idx] = (info, xml.encode("utf-8"))
    tmp = path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for i, d in items:
            z.writestr(i, d)
    shutil.move(str(tmp), str(path))
    print(f"[OK] {path.name}: 替换 {len(edits)} 处")


if __name__ == "__main__":
    rewrite(PAPER, PAPER_EDITS)
    rewrite(ZH, ZH_EDITS)
