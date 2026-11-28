# -*- coding: utf-8 -*-
"""把 v8 里按字符级别打散的紫色标记，归并到整句粒度。

背景：紫色标注记录的是「相对 11-02 初稿改过的字符」，句子被整体重写时会裂成
`compi|led into|...` 这类逐字母碎片。这里只改 w:rPr/w:color，不动任何文字：
某句话里只要出现紫色字符，覆盖该句的 run 统一设为紫色；红色（EE0000）保持不动。
"""
from __future__ import annotations

import re
import shutil
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

DOC = Path(r"D:\PycharmProjects\斑马鱼基因问答体\论文学习\有批注版\AIzebrafish_manuscript20261102_批注整改_v8.docx")
TARGETS = [16, 26, 27, 36, 37, 42, 45, 46]
PURPLE, RED = "7030A0", "EE0000"
SPLIT = re.compile(r"(?<=[.;:])\s+")


def register_ns(xml_bytes: bytes) -> str:
    """沿用原文档的命名空间前缀，避免 ET 输出 ns0: 之类的前缀。"""
    head = xml_bytes[:4000].decode("utf-8", "replace")
    m = re.search(r"<w:document\b[^>]*>", head)
    if m:
        for pref, uri in re.findall(r'xmlns:([\w-]+)="([^"]+)"', m.group(0)):
            ET.register_namespace(pref, uri)
    return head


PRE_COLOR = {
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike",
    "dstrike", "outline", "shadow", "emboss", "imprint", "noProof",
    "snapToGrid", "vanish", "webHidden",
}


def ensure_color(run, P: str):
    """取 run 的 w:color；没有就在 w:rPr 里按 schema 顺序插一个。"""
    rpr = run.find(P + "rPr")
    if rpr is None:
        rpr = ET.Element(P + "rPr")
        run.insert(0, rpr)
    c = rpr.find(P + "color")
    if c is not None:
        return c
    c = ET.Element(P + "color")
    pos = 0
    for k, child in enumerate(list(rpr)):
        if child.tag.replace(P, "") in PRE_COLOR:
            pos = k + 1
    rpr.insert(pos, c)
    return c


def main() -> None:
    src = zipfile.ZipFile(DOC)
    items = [(i, src.read(i.filename)) for i in src.infolist()]
    src.close()
    idx = next(k for k, (i, _) in enumerate(items) if i.filename == "word/document.xml")
    info, data = items[idx]

    register_ns(data)
    P = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body_xml = data.split(b"?>", 1)[1].lstrip() if data.startswith(b"<?xml") else data
    root = ET.fromstring(body_xml)
    body = root.find(P + "body")
    paras = [e for e in body if e.tag == P + "p"]

    total = 0
    for pi in TARGETS:
        para = paras[pi]
        runs = [r for r in para if r.tag == P + "r"]
        text = "".join("".join(t.text or "" for t in r.iter(P + "t")) for r in runs)

        bounds, last = [], 0
        for m in SPLIT.finditer(text):
            bounds.append((last, m.start()))
            last = m.end()
        bounds.append((last, len(text)))
        sent_of = [0] * len(text)
        for si, (a, b) in enumerate(bounds):
            for k in range(a, b):
                sent_of[k] = si

        colors, spans = [], []
        off = 0
        for r in runs:
            n = len("".join(t.text or "" for t in r.iter(P + "t")))
            rpr = r.find(P + "rPr")
            c = rpr.find(P + "color") if rpr is not None else None
            colors.extend([c.get(P + "val") if c is not None else None] * n)
            spans.append((off, off + n, r, c))
            off += n
        if len(colors) != len(text):
            sys.exit(f"[FAIL] 段 {pi}: 字符数不一致 {len(colors)} vs {len(text)}")

        purple_sents = {sent_of[k] for k, c in enumerate(colors) if c == PURPLE}
        if not purple_sents:
            print(f"  [skip] 段 {pi} 无紫色字符")
            continue

        changed = 0
        for a, b, r, c in spans:
            if b == a:
                continue
            sent = Counter(sent_of[a:b]).most_common(1)[0][0]
            if sent not in purple_sents:
                continue
            if c is not None and c.get(P + "val") in (RED, PURPLE):
                continue
            ensure_color(r, P).set(P + "val", PURPLE)
            changed += 1
        total += changed
        print(f"  [ok] 段 {pi}: 补齐 {changed} 个 run 为紫色")

    out = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n' + ET.tostring(
        root, encoding="utf-8", xml_declaration=False
    )
    items[idx] = (info, out)
    tmp = DOC.with_suffix(".tmp.docx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for i, d in items:
            z.writestr(i, d)
    shutil.move(str(tmp), str(DOC))
    print(f"[DONE] 共补齐 {total} 个 run")


if __name__ == "__main__":
    main()
