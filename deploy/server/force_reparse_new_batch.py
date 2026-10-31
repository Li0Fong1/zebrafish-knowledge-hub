# -*- coding: utf-8 -*-
"""Quarantine not-gold JSON for new batch entry_nos so parse will redo."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RT = Path("/data/zoujin/deng/zebrafish_runtime")
BASE = Path("/data/zoujin/deng/zebrafish_gene_qa")
sys.path.insert(0, str(BASE))
os.environ["ZEBRA_RUNTIME"] = str(RT)
os.environ["ZEBRA_BASE"] = str(BASE)

enos_path = RT / "logs" / "scihub_new_entry_nos.txt"
if not enos_path.is_file():
    print("no entry nos file")
    raise SystemExit(0)

enos = {ln.strip() for ln in enos_path.read_text().splitlines() if ln.strip()}
print("new_enos", len(enos))

from core.entry import bootstrap_app

bootstrap_app()
from scripts.gold_hunt_fulltext import is_pdf_gold
from scripts.scihub_post_gold_pipeline import gold_json_path, load_rec

moved = 0
for eno in sorted(enos):
    rec = load_rec(eno)
    if rec is None or is_pdf_gold(rec):
        continue
    jp = gold_json_path(eno)
    if jp.is_file():
        jp.rename(jp.with_suffix(".json.pre_new_batch"))
        moved += 1
print("quarantined_not_gold_json", moved)
