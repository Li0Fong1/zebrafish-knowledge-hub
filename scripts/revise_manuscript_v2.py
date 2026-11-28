# -*- coding: utf-8 -*-
"""Second revision pass (2026-11-11, evening).

Two jobs, both reported by the author:

1.  The screening/cleaning stages were described as "a language model judges
    relevance", while the caption of Fig. S4A still said that DeepSeek removes
    paragraphs that are irrelevant to scientific knowledge.  The pipeline is in
    fact rule-governed: the criteria are fixed by the authors, cleaning is a
    deterministic rule set with no model call at all, and where a model is used
    it applies the authors' rules instead of judging freely.  The prose is
    rewritten accordingly, and panel (A) of Fig. S4 is repainted (see
    scripts/patch_figS4_panelA.py).

2.  The two judges are reported under the provider's dashboard labels,
    deepseek-flash and deepseek-v4-pro, not under the API aliases.

Every changed fragment keeps the yellow-highlight + purple-type marking, and
embedded figures are preserved (rebuild() detaches and re-attaches w:drawing).

Usage:  python scripts/revise_manuscript_v2.py --dry-run
        python scripts/revise_manuscript_v2.py --write
"""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

import docx

from revise_manuscript import rebuild

ROOT = Path(__file__).resolve().parents[1]
DOC = Path(r"C:/Users/Lenovo/Desktop/新建文件夹/AIzebrafish_manuscript_20261111_revised.docx")
FIG_OLD = ROOT / "_docx_media" / "image3.jpeg"
FIG_NEW = ROOT / "_docx_media" / "figS4_patched.jpeg"

EM = "\u2014"          # em dash
RSQ = "\u2019"        # right single quote

EDITS: dict[int, list[tuple[str, str]]] = {
    # ---- 2.1 screening: rule-governed, not model-discretionary ---------------
    16: [
        (
            "Titles and abstracts of all 100,424 records were submitted to a large language "
            "model (DeepSeek-V4.1-Flash, temperature 0.3) to judge whether zebrafish was a "
            "primary research subject rather than an incidental mention. Screening was executed "
            "in approximately 2,700 batches and produced three judgments: Yes (52,255), No "
            "(34,805), and Uncertain (13,364). To retain studies more comprehensively, uncertain "
            "studies were further manually examined. Subsequent quality-control rounds corrected "
            "incomplete bilingual fields and inconsistent judgment" + "\u2013" + "reason pairs.",
            "Screening was governed by an inclusion criterion fixed in advance by the authors "
            + EM + " whether zebrafish was the organism under study or merely an incidental "
            "mention " + EM + " which was then applied to every record by a large language model "
            "(deepseek-flash, temperature 0.3). The model served as a rule-following classifier "
            "rather than as an autonomous judge of relevance: the criterion, and not the "
            "discretion of the model, determined what entered the corpus, so that the same record "
            "and the same criterion always produced the same verdict. Screening was executed in "
            "approximately 2,700 batches and yielded 52,255 Yes, 34,805 No and 13,364 Uncertain "
            "verdicts; the uncertain records were then examined manually, so that inclusion "
            "remained more permissive than the automated pass alone would have allowed. "
            "Subsequent quality-control rounds corrected incomplete bilingual fields and "
            "inconsistent verdict" + "\u2013" + "reason pairs.",
        )
    ],
    # ---- 2.2 cleaning: explicit rules, no model at this step -----------------
    19: [
        (
            "To improve retrieval quality, non-scientific material was removed from each full "
            "text before indexing.",
            "To improve retrieval quality, non-scientific material was removed from each full "
            "text before indexing. Removal was governed throughout by an explicit rule set "
            "written by the authors and executed deterministically; no language model decided "
            "which paragraphs of a paper were scientific, so that the outcome depends only on the "
            "input text and on the rules and is reproducible exactly.",
        )
    ],
    # ---- 2.4 model labels ----------------------------------------------------
    29: [
        (
            "generation uses the deepseek-chat endpoint" + EM + "labelled DeepSeek-V4.1-Flash in "
            "the provider dashboard" + EM + "at a temperature of 0.3; deepseek-reasoner is "
            "invoked for reasoning-intensive and judging steps.",
            "generation uses the deepseek-chat endpoint, reported as deepseek-flash on the "
            "provider dashboard, at a temperature of 0.3, whereas deepseek-reasoner, reported as "
            "deepseek-v4-pro, is invoked for reasoning-intensive and judging steps.",
        )
    ],
    33: [
        (
            "which is listed under the deepseek-v4-flash label on the dashboard.",
            "which is listed as deepseek-flash on the dashboard.",
        )
    ],
    # ---- 3.1 judges ----------------------------------------------------------
    41: [
        (
            "Each answer was scored independently by two judges (deepseek-chat and "
            "deepseek-reasoner), whose per-item agreement was Spearman 0.801 (n = 10)",
            "Each answer was scored independently by two judges, deepseek-flash and "
            "deepseek-v4-pro, reached through the deepseek-chat and deepseek-reasoner API aliases, "
            "whose per-item agreement was Spearman 0.801 (n = 10)",
        )
    ],
    # ---- 3.2 screening, restated in the results ------------------------------
    46: [
        (
            "Records were then screened by a language model, with manual adjudication of "
            "uncertain cases. The titles and abstracts of all 100,424 deduplicated records were "
            "submitted to DeepSeek-V4.1-Flash, which judged for every record whether zebrafish "
            "was the primary research subject or an incidental mention; mention-only publications "
            "were excluded at this stage. Applied to the 100,424 deduplicated records, the "
            "screening partitioned them into three tiers at once: ",
            "Records were then screened against an inclusion criterion fixed in advance by the "
            "authors rather than by open-ended model judgement. The titles and abstracts of all "
            "100,424 deduplicated records were passed one at a time to deepseek-flash, which was "
            "required to apply that criterion " + EM + " whether zebrafish was the organism under "
            "study or an incidental mention " + EM + " and to return a verdict together with its "
            "reason; mention-only publications were excluded at this stage. The screening "
            "partitioned the corpus into three tiers at once: ",
        )
    ],
    52: [
        (
            "noise removal is thus governed by explicit rules rather than delegated to a "
            "language model judging isolated paragraphs",
            "noise removal is thus governed throughout by explicit rules written by the authors, "
            "rather than delegated to a language model judging isolated paragraphs",
        )
    ],
    # ---- Fig. S4 caption -----------------------------------------------------
    54: [
        (
            "(A) Pipeline of paper cleaning and vectorisation.",
            "(A) Pipeline of paper cleaning and vectorisation. Cleaning is executed by "
            "deterministic rules written by the authors, and no language model takes part in this "
            "step.",
        )
    ],
}


def swap_figure(doc, old_bytes: bytes, new_bytes: bytes) -> bool:
    """Replace the Fig. S4 image part in place, keeping its extent and relation id."""
    for rel in doc.part.rels.values():
        if "image" not in rel.reltype:
            continue
        part = rel.target_part
        if part.blob == old_bytes:
            part._blob = new_bytes
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    doc = docx.Document(str(DOC))
    paras = doc.paragraphs

    for idx, edits in sorted(EDITS.items()):
        text = paras[idx].text
        for old, _ in edits:
            n = text.count(old)
            flag = "OK " if n == 1 else f"!! {n}"
            print(f"[{idx}] {flag} {old[:64]!r}  (n={n})")
            if n != 1:
                raise SystemExit("abort: edit does not match uniquely")

    if not args.write:
        print("\ndry-run only")
        return 0

    marked = 0
    for idx, edits in sorted(EDITS.items()):
        marked += rebuild(paras[idx], edits)
        print(f"[{idx}] rewritten, {marked} characters marked so far")

    old_bytes = FIG_OLD.read_bytes()
    new_bytes = FIG_NEW.read_bytes()
    ok = swap_figure(doc, old_bytes, new_bytes)
    print("figure replaced:", ok)

    bak = DOC.with_suffix(".docx.bak_before_revise2_20261111")
    if not bak.exists():
        shutil.copy2(DOC, bak)
        print("backup ->", bak)

    tmp = DOC.with_suffix(".docx.tmp")
    doc.save(str(tmp))
    os.replace(tmp, DOC)
    print("saved ->", DOC)
    print("marked characters:", marked)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
