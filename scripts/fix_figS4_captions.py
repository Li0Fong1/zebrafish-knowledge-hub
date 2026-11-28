# -*- coding: utf-8 -*-
"""Fix-up pass after the second revision (2026-11-11).

1.  The three Fig. S4 panel captions are auto-numbered by a Word list
    (numPr) that already renders "(A)", "(B)" and "(C)"; an earlier pass also
    typed those prefixes into the text, so the PDF printed "(A) (A) ...".
    The typed prefixes are removed and the list numbering is left in charge.
2.  Panel (A) of the figure is swapped for the repainted version whose caption
    reads "Author-defined rules" instead of "DeepSeek - Remove paragraphs
    irrelevant to scientific knowledge".

Usage: python scripts/fix_figS4_captions.py [--write]
"""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

import docx
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[1]
DOC = Path(r"C:/Users/Lenovo/Desktop/新建文件夹/AIzebrafish_manuscript_20261111_revised.docx")
FIG_NEW = ROOT / "_docx_media" / "figS4_patched.jpeg"

TARGETS = {54: "(A) ", 55: "(B) ", 56: "(C) "}


def strip_prefix(paragraph, prefix: str) -> bool:
    for run in paragraph.runs:
        if run.text.startswith(prefix):
            run.text = run.text[len(prefix):]
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    doc = docx.Document(str(DOC))
    for idx, prefix in TARGETS.items():
        par = doc.paragraphs[idx]
        numbered = par._p.find(qn("w:pPr")) is not None and \
            par._p.find(qn("w:pPr")).find(qn("w:numPr")) is not None
        print(f"[{idx}] numbered={numbered} text={par.text[:60]!r}")
        if not numbered:
            raise SystemExit(f"abort: paragraph {idx} is not auto-numbered")

    if not args.write:
        print("\ndry-run only")
        return 0

    for idx, prefix in TARGETS.items():
        ok = strip_prefix(doc.paragraphs[idx], prefix)
        print(f"[{idx}] prefix removed: {ok}")

    new_bytes = FIG_NEW.read_bytes()
    swapped = False
    for rel in doc.part.rels.values():
        if "image" in rel.reltype and rel.target_part.partname == "/word/media/image3.jpeg":
            rel.target_part._blob = new_bytes
            swapped = True
    print("figure swapped:", swapped)

    bak = DOC.with_suffix(".docx.bak_before_fix3_20261111")
    if not bak.exists():
        shutil.copy2(DOC, bak)

    tmp = DOC.with_suffix(".docx.tmp")
    doc.save(str(tmp))
    os.replace(tmp, DOC)
    print("saved ->", DOC)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
