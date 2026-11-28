# -*- coding: utf-8 -*-
"""Manuscript revision pass (2026-11-11).

Applies text-level corrections to the zebrafish manuscript and marks every
changed fragment with a yellow highlight and purple font, as requested.

Design notes
------------
* Only paragraphs that actually change are rebuilt; all other runs are untouched.
* Rebuild preserves per-character formatting by cloning the rPr of the source run.
* Every (old -> new) pair must occur exactly once in its paragraph, otherwise the
  script aborts before writing anything (fail-fast, no partial output).

Usage:  python scripts/revise_manuscript.py --dry-run
        python scripts/revise_manuscript.py --write
"""
from __future__ import annotations

import argparse
import copy
import os
import sys

import docx
from docx.oxml.ns import qn
from docx.shared import RGBColor
from docx.enum.text import WD_COLOR_INDEX

SRC = r"C:/Users/Lenovo/Desktop/新建文件夹/AIzebrafish_manuscript_20261107.docx"
DST = r"C:/Users/Lenovo/Desktop/新建文件夹/AIzebrafish_manuscript_20261111_revised.docx"

PURPLE = "7030A0"

# ---------------------------------------------------------------- edit table
# index -> list of (old, new). Use "*WHOLE*" as old to replace the whole paragraph.
EDITS: dict[int, list[tuple[str, str]]] = {
    # ---- Introduction ----------------------------------------------------
    3: [
        ("*ALL*（", " ("),
        ("*ALL*）", ")"),
        ("*ALL*；", "; "),
        ("*ALL*  (", " ("),
        ("selective breeding programs", "selective breeding programmes"),
        (
            "First, the number of related publications has nearly doubled over the past decade, "
            "making conventional literature mining increasingly challenging (Supplemental Figure 1).",
            "First, the annual output of zebrafish papers has risen from 1,188 in 2011 to a peak of "
            "2,470 in 2020 and has since fallen back to roughly 1,400\u20131,500 papers per year, "
            "making conventional literature mining increasingly challenging (Supplemental Figure 1).",
        ),
    ],
    5: [
        (
            "*WHOLE*",
            "Fig. S1. Annual output of the zebrafish literature analysed in this study. Bars give the "
            "number of full-text papers published in each year from 1990 to 2025, and the hatched bar "
            "gives 2026, which was incomplete at the export date (10 October 2026); each bar is "
            "labelled with its exact count. The analysed corpus holds 32,481 papers dated between "
            "1990 and 2026; a further 51 papers published before 1990 (earliest 1951) are not shown. "
            "Output rose from 1,188 papers in 2011 to a peak of 2,470 in 2020 and has since declined "
            "to about 1,400\u20131,500 papers per year.",
        )
    ],
    7: [
        (
            "*WHOLE*",
            "Fig. S2. Inconsistencies in gene nomenclature across different versions of the zebrafish "
            "reference genome. Accession numbers of six loci are listed for twelve zebrafish "
            "assemblies, showing that a single gene carries a different identifier in each assembly "
            "version.",
        )
    ],
    9: [
        (
            "which relies on human curation but limits both coverage and timeliness",
            "but this reliance on manual curation limits both its coverage and its timeliness",
        ),
        (
            "However, there is no such system exists for zebrafish",
            "However, no such system exists for zebrafish",
        ),
        ("toward operational autonomy", "towards operational autonomy"),
    ],
    11: [
        ("(网页; Figure 1)", "(https://zebrafish-hub.top; Figure 1)"),
        (
            "and sourced from over 39228 high-quality zebrafish research articles",
            "and sourced from 32,549 full-text articles selected from 39,228 high-quality zebrafish "
            "studies",
        ),
        (
            "Furthermore, the Zebrafish Research Assistant was developed to integrates all platform "
            "modules to facilitate intuitive, natural language-based interaction, which designed to "
            "lower the barrier for aquaculture researchers",
            "Furthermore, the Zebrafish Research Assistant was developed to integrate all platform "
            "modules and to support intuitive, natural-language interaction, lowering the barrier for "
            "aquaculture researchers",
        ),
        ("standardized gene identifier mapping layer", "standardised gene identifier mapping layer"),
        (
            "and designed with direct relevance to aquaculture research",
            "and designed to be directly relevant to aquaculture research",
        ),
    ],
    # ---- Methods ---------------------------------------------------------
    16: [
        ("(DeepSeek-V4.1-Flash-1017, temperature 0.3)", "(DeepSeek-V4.1-Flash, temperature 0.3)"),
        ("downloaded via authorized library access", "downloaded via authorised library access"),
        ("while preserving paragraph organization", "while preserving paragraph organisation"),
    ],
    19: [("Text extracted from PDF was normalized at page level", "Text extracted from PDF was normalised at page level")],
    29: [
        ("\u2014labeled", "\u2014labelled"),
        (
            "Users do not choose a route; the chain is the same for every turn, and modules that a "
            "question does not need are skipped.",
            "Users do not choose a route, and the chain is identical for every turn: modules that a "
            "question does not need are skipped, but the answer itself is always composed at full "
            "depth, and no abbreviated answering mode is offered in the deployed interface.",
        ),
    ],
    32: [("can be categorized into two distinct purposes", "can be categorised into two distinct purposes")],
    # ---- Tools -----------------------------------------------------------
    38: [
        (
            "GO enrichment runs the standard over-representation test (Fisher\u2019s exact test with "
            "Benjamini-Hochberg correction) against the genes annotated in the local database, keeping "
            "the test conditioned on the same annotation set that produced the query. Pathway browsing "
            "consolidates KEGG, WikiPathways and Reactome into one local table, so that the pathways of "
            "a gene or gene list, their member genes and the overlap with the query are retrieved across "
            "all three resources in a single pass; it reports membership only and applies no statistical "
            "test.",
            "GO annotation and pathway enrichment share one interface: a single gene returns its Gene "
            "Ontology terms, or its KEGG, WikiPathways and Reactome pathways, whereas a gene list is "
            "tested for over-representation with Fisher\u2019s exact test and Benjamini-Hochberg "
            "correction against the same annotations held in the local database. Pathway membership is "
            "consolidated from the three resources into one local table, so that the member genes and "
            "their overlap with the query are retrieved in a single pass.",
        ),
        ("source resource.2.8", "source resource."),
    ],
    39: [("*WHOLE*", "2.8 Performance evaluation")],
    # ---- Evaluation ------------------------------------------------------
    40: [
        (
            "The zebrafish instance of each pair was submitted to the deployed Hub through its single "
            "answering chain, which internally selected the appropriate specialist for each item (12 "
            "by the full pipeline, seven by the gene-detail branch and one by the identifier branch), "
            "and all 20 answers passed the automatic gates derived from the rubric below.",
            "The zebrafish instance of each pair was submitted to the deployed Hub, and every one of "
            "the 20 items was answered by the same complete chain, in which the gene card, the "
            "identifier mapping and the literature retrieval are executed in one pass; no abbreviated "
            "or branch-specific answer is produced. All 20 answers passed the automatic gates derived "
            "from the rubric below.",
        )
    ],
    # ---- Results ---------------------------------------------------------
    44: [("*WHOLE*", "3.1 A high-quality, specialised zebrafish literature corpus")],
    45: [("A high-quality, specialized zebrafish research corpus", "A high-quality, specialised zebrafish research corpus")],
    46: [
        (
            "Records were then screened by a rule-first hybrid triage. A deterministic rule engine "
            "encoding the inclusion criterion as explicit regular-expression rules assigned the clear "
            "positive and clear negative tiers, while a large language model adjudicated only the "
            "ambiguous band, so that publications mentioning \u201czebrafish\u201d without addressing it "
            "as the primary research subject were filtered out. Applied to the 100,424 deduplicated "
            "records, the triage partitioned them into three tiers at once: 52,255 studies retained, "
            "spanning zebrafish genetics, metabolism, physiology, genomics and functional genomics; "
            "34,805 excluded as mention-only; and 13,364 ambiguous records referred to manual "
            "examination.",
            "Records were then screened by a language model, with manual adjudication of uncertain "
            "cases. The titles and abstracts of all 100,424 deduplicated records were submitted to "
            "DeepSeek-V4.1-Flash, which judged for every record whether zebrafish was the primary "
            "research subject or an incidental mention; mention-only publications were excluded at "
            "this stage. Applied to the "
            "100,424 deduplicated records, the screening partitioned them into three tiers at once: "
            "52,255 studies retained, spanning zebrafish genetics, metabolism, physiology, genomics and "
            "functional genomics; 34,805 classified as mention-only; and 13,364 uncertain records "
            "referred to manual examination.",
        )
    ],
    51: [("*WHOLE*", "3.2 A retrieval-augmented knowledge base built from cleaned full texts")],
    53: [
        (
            "*WHOLE*",
            "Fig. S4. Construction of the zebrafish literature knowledge base.",
        )
    ],
    54: [("*WHOLE*", "(A) Pipeline of paper cleaning and vectorisation.")],
    55: [("*WHOLE*", "(B) Pipeline of query and answer generation for zebrafish knowledge.")],
    56: [("*WHOLE*", "(C) Rubric scores and inline citations across six answering configurations.")],
    57: [
        (
            "*WHOLE*",
            "Each of the six configurations\u2014three answering routes (literature retrieval only, "
            "fusion of the gene database with the literature, and the complete Hub chain) crossed with "
            "two answering depths (partial and full)\u2014was run on the same ten benchmark items and "
            "scored against the seven-dimension, 10-point rubric; n = 10 items per configuration. "
            "Panels show the distribution of rubric totals with individual answers overlaid and the mean "
            "marked (C1), and the number of distinct inline citations per answer (C2). The deployed "
            "service corresponds to the Hub full configuration, for which the two judges returned mean "
            "totals of 8.35 and 7.38 and an inter-judge agreement of Spearman 0.801 (n = 10).",
        )
    ],
    59: [("*WHOLE*", "3.3 Answer quality of the deployed Hub")],
    60: [
        (
            "The deployed Hub was evaluated on ten benchmark items, yielding mean total rubric scores "
            "of 8.35 and 7.38 out of 10 from the two judges.",
            "The deployed Hub was evaluated on ten benchmark items answered by the complete chain, "
            "yielding mean total rubric scores of 8.35 and 7.38 out of 10 from the two judges.",
        ),
        (
            "Answers were densely cited, with 18.3 distinct inline citations on average, and in all ten "
            "items every inline marker was supported by a numbered reference list",
            "For these ten items the answers were densely cited (18.3 distinct inline citations on "
            "average), and in every one of them each inline marker was supported by a numbered "
            "reference list",
        ),
        ("rather than merely fluent.\u3001", "rather than merely fluent."),
    ],
    61: [
        (
            "*WHOLE*",
            "Fig. S5. Comparison of zebrafish knowledge queries between the Zebrafish Research Assistant "
            "and four general-purpose LLMs. The assistant is labelled \u201cZebrafish Agent\u201d in the "
            "interface. Green type marks statements verified against PubMed or the underlying record and "
            "red type marks statements that are contradicted by the record; the verdict for each answer "
            "is given at the top right.",
        )
    ],
    63: [("*WHOLE*", "3.4 Cross-version gene nomenclature and the analysis toolkit")],
    64: [
        ("A toolkit comprising Blast, ID converter, sgRNA, Primer design, Gene Ontology and Kyoto "
         "Encyclopedia of Genes and Genomes enrichment tools was integrated to facilitate rapid BLAST "
         "queries, gene domain and symbol annotation, different zebrafish genome to reference zebrafish "
         "genome ID conversion, and functional enrichment analyses (Supplemental Figure 7).",
         "A toolkit comprising BLAST, GO annotation, pathway enrichment, sgRNA design and primer design "
         "was integrated to support rapid sequence searches, gene-domain and symbol annotation, "
         "conversion of identifiers between zebrafish genome versions, and functional enrichment "
         "analysis (Supplemental Figure 6)."),
    ],
    66: [
        (
            "*WHOLE*",
            "Fig. S6. The five analysis tools integrated in the Zebrafish Knowledge Hub: BLAST, GO "
            "annotation, pathway enrichment, sgRNA design and primer design. Each tool returns "
            "field-labelled results that are displayed together with their data provenance.",
        )
    ],
    67: [("*WHOLE*", "3.5 Agentic orchestration and comparison with general-purpose LLMs")],
    68: [
        ("the assistant analyzes user intent", "the assistant analyses user intent"),
        ("to synthesize research insights", "to synthesise research insights"),
        ("customized promoter sequences", "customised promoter sequences"),
        (
            "against the open-source models Hunyuan Hy4 and Qwen3-235B-A22B (thinking enabled) as well "
            "as the closed-source models Doubao 2.0 pro and Gemini 3.5 Flash-Lite",
            "against the open-weight models Hunyuan Hy4 and Qwen 3.7-Max (thinking enabled) as well as "
            "the proprietary models Doubao Seed 2.0 Pro and Gemini 3.5 Flash-Lite",
        ),
        (
            "The Zebrafish Research Assistant outperformed general LLMs across all three evaluation "
            "categories.",
            "The Zebrafish Research Assistant outperformed all four models in each of the three "
            "evaluation categories examined here\u2014specialised knowledge queries, gene-detail "
            "comprehension and sequence extraction (Supplemental Figures 5, 7 and 8).",
        ),
        ("pronounced score polarization", "pronounced score polarisation"),
        ("specialized database design", "specialised database design"),
        ("answering specialized, domain-specific queries", "answering specialised, domain-specific queries"),
    ],
    70: [
        (
            "*WHOLE*",
            "Fig. S7. Comparison of gene-detail comprehension between the Zebrafish Research Assistant "
            "and four general-purpose LLMs.",
        )
    ],
    71: [
        (
            "*WHOLE*",
            "Correct answers are shown in green and fabricated statements in red.",
        )
    ],
    74: [
        (
            "*WHOLE*",
            "Fig. S8. Comparison of sequence extraction between the Zebrafish Research Assistant and "
            "four general-purpose LLMs.",
        )
    ],
    75: [
        (
            "*WHOLE*",
            "Fabricated gene identifiers and sequences produced by the general-purpose LLMs are "
            "highlighted with a pink background.",
        )
    ],
    77: [
        ("a widely utilized model species", "a widely utilised model species"),
        ("gene entity normalization workflow", "gene-entity normalisation workflow"),
        (
            "offer a highly transferable framework for constructing similar platforms in other "
            "biological domains.",
            "offer a highly transferable framework for constructing similar platforms in other "
            "biological domains. The Zebrafish Knowledge Hub is freely accessible at "
            "https://zebrafish-hub.top.",
        ),
    ],
}


def _rpr_of(run):
    return run._r.find(qn("w:rPr"))


def rebuild(paragraph, edits):
    """Replace text inside a paragraph, preserving per-character formatting.

    Runs that carry an image (w:drawing) hold no text; they are detached before the
    rebuild and re-inserted at their original character offset, so embedded figures
    survive the rewrite.
    """
    runs = [r for r in paragraph.runs]
    if not runs:
        return 0

    chars: list[tuple[str, object]] = []          # (char, rPr element or None)
    drawings: list[tuple[int, object]] = []       # (char offset, w:r element)
    offset = 0
    for run in runs:
        el = run._r
        if el.findall(".//" + qn("w:drawing")) or el.findall(".//" + qn("w:pict")):
            drawings.append((offset, el))
            continue
        rpr = _rpr_of(run)
        for ch in run.text:
            chars.append((ch, rpr))
            offset += 1

    text = "".join(c for c, _ in chars)
    new_flags = [False] * len(text)               # True where text was inserted

    for old, new in edits:
        if old == "*WHOLE*":
            continue
        pattern = old[5:] if old.startswith("*ALL*") else old
        hits = text.count(pattern)
        if old.startswith("*ALL*"):
            pass  # replace-all patterns may only appear after an earlier edit in the same paragraph
        elif hits != 1:
            raise SystemExit(f"[ABORT] '{pattern[:60]}…' occurs {hits}x (expected 1)")

    # Whole-paragraph replacement: keep the formatting of the first character.
    whole = [n for o, n in edits if o == "*WHOLE*"]
    if whole:
        base = chars[0][1] if chars else None
        text = whole[0]
        chars = [(ch, base) for ch in text]
        new_flags = [True] * len(text)
    else:
        for old, new in edits:
            if old == "*WHOLE*":
                continue
            if old.startswith("*ALL*"):
                pattern = old[5:]
                while pattern in text:
                    i = text.find(pattern)
                    chars[i:i + len(pattern)] = [(ch, chars[i][1]) for ch in new]
                    new_flags[i:i + len(pattern)] = [True] * len(new)
                    text = text[:i] + new + text[i + len(pattern):]
            else:
                i = text.find(old)
                chars[i:i + len(old)] = [(ch, chars[i][1]) for ch in new]
                new_flags[i:i + len(old)] = [True] * len(new)
                text = text[:i] + new + text[i + len(old):]

    # Rebuild runs, grouping consecutive characters with identical rPr and flag.
    body = paragraph._p
    for run in runs:
        if run._r.getparent() is body:
            body.remove(run._r)

    def emit(segment, rpr, changed):
        run = paragraph.add_run()
        run.text = segment
        if rpr is not None:
            run._r.insert(0, copy.deepcopy(rpr))
        if changed:
            run.font.highlight_color = WD_COLOR_INDEX.YELLOW
            run.font.color.rgb = RGBColor.from_string(PURPLE)
        return run

    buf, cur_rpr, cur_flag = "", None, None
    for ch, rpr, flag in zip(text, [c[1] for c in chars], new_flags):
        if buf and (rpr is not cur_rpr or flag != cur_flag):
            emit(buf, cur_rpr, cur_flag)
            buf = ""
        buf += ch
        cur_rpr, cur_flag = rpr, flag
    if buf:
        emit(buf, cur_rpr, cur_flag)

    # Re-attach embedded images at their original character offset.
    if drawings:
        new_runs = list(paragraph.runs)
        cum, acc = [], 0
        for r in new_runs:
            acc += len(r.text)
            cum.append(acc)
        for off, el in drawings:
            if not new_runs:
                body.append(el)
            elif off <= 0:
                new_runs[0]._r.addprevious(el)
            else:
                idx = next((i for i, c in enumerate(cum) if c >= off), len(new_runs) - 1)
                new_runs[idx]._r.addnext(el)
    return sum(new_flags)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    doc = docx.Document(SRC)
    paras = doc.paragraphs
    total_changed = 0

    for idx, edits in sorted(EDITS.items()):
        p = paras[idx]
        text = p.text
        for old, new in edits:
            if old == "*WHOLE*":
                continue
            pattern = old[5:] if old.startswith("*ALL*") else old
            n = text.count(pattern)
            state = "OK " if (n == 1 or old.startswith("*ALL*")) else f"!! {n}"
            print(f"[{idx}] {state} {pattern[:70]!r}  (n={n})")
            if state != "OK ":
                raise SystemExit("abort: edit does not match uniquely")
        if not args.write:
            continue
        n = rebuild(p, edits)
        total_changed += n
        print(f"[{idx}] rewritten, {n} characters marked")

    if args.write:
        doc.save(DST)
        print(f"\nsaved -> {DST}")
        print(f"marked characters: {total_changed}")
    else:
        print("\ndry-run only")


if __name__ == "__main__":
    main()
