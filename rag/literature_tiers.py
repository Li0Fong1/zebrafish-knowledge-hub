# -*- coding: utf-8 -*-
"""文献分层：broad（广搜）与 core_genetics（精选遗传/基因组学）。"""
import re
from typing import Literal

Tier = Literal["broad", "core_genetics"]

_ZF_KW = re.compile(
    r"\b(zebrafish|danio\s+rerio|danio_rerio|d\.?\s*rerio)\b",
    re.I,
)
_CORE_KW = re.compile(
    r"\b("
    r"gene|genetic|genome|genomic|crispr|cas9|knockout|knockdown|morpholino|"
    r"mutant|mutation|transgenic|expression|transcriptome|rnaseq|rna-seq|"
    r"chromatin|epigenetic|go term|gene ontology|ensembl|zfin|"
    r"developmental|embryogenesis|morphogenesis|signaling pathway|"
    r"zebrafish model|model organism"
    r")\b",
    re.I,
)
_NOISE_KW = re.compile(
    r"\b(toxicity|toxicology|pharmacology|drug screening|environmental|"
    r"nanoparticle|microplastic|pesticide|heavy metal|aquaculture feed)\b",
    re.I,
)


def classify_tier(art: dict) -> Tier:
    """判定文献 tier；core_genetics 用于 Potato 式精选 RAG 子库。"""
    if art.get("tier") == "core_genetics":
        return "core_genetics"
    text = " ".join([
        art.get("title") or "",
        art.get("abstract") or "",
        " ".join(art.get("keywords") or []),
    ])
    if not _ZF_KW.search(text):
        return "broad"
    if not _CORE_KW.search(text):
        return "broad"
    if _NOISE_KW.search(text) and not _CORE_KW.search((art.get("title") or "")):
        return "broad"
    return "core_genetics"


def tag_articles(articles: list) -> list:
    for art in articles:
        art["tier"] = classify_tier(art)
    return articles
