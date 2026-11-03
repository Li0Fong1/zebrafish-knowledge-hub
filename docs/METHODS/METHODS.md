# METHODS (Zebrafish Knowledge Hub)

## 2.1 Processing literatures

Zebrafish-related literature was retrieved by searching titles or abstracts from PubMed,
Europe PMC, and the Web of Science Core Collection, using “zebrafish” and “*Danio rerio*”
as keywords together with their Boolean combinations. Relevant studies, including their
titles, abstracts, journal information, PMIDs and DOIs, were exported for subsequent
filtering. Each record was then submitted to an LLM-based screening step designed to
discard papers that merely mention zebrafish in the title or abstract but are not focused
on zebrafish research; the screening criterion was defined as “whether the study primarily
uses zebrafish as the research subject or model”, so that mention-only, control-only, and
background uses were excluded, while records with insufficient information were retained
for manual inspection.

Screening was implemented as a rule-first hybrid triage rather than as a single LLM pass
(`scripts/judge_hybrid_triage.py`). A deterministic rule engine that encodes the same
criterion as explicit regular-expression rules first classified every record into
`clear_yes`, `clear_no` or `gray`; the two clear tiers were decided by the rules, whereas
the ambiguous `gray` band was passed to an LLM judge
(`scripts/judge_literature_zebrafish_batch.py`) whose prompt carries the criterion text
quoted above and which returns `yes`, `no` or `not_classifiable` together with a one-line
justification. Records without an abstract were never auto-rejected but carried forward as
`not_classifiable` for manual inspection. A quality guard
(`scripts/judge_quality_guard.py`) prevents silent degradation: when an LLM call fails, or
when a record still carries a rule-derived verdict, the record is requeued instead of being
silently downgraded to a rule decision, so that no exclusion rests on a failed API call.
Records that also appeared in the Web of Science export were reconciled against the PubMed
judgement (`scripts/resolve_merged_judgement_conflicts.py`), where the PubMed decision takes
precedence and the WoS list serves as a reference; the remaining disagreements and
`not_classifiable` records were re-examined manually. Full texts of the retained studies
were downloaded as PDF or
PubMed Central XML, and their plain text was extracted with **PyMuPDF** (`page.get_text("text")`,
v1.28.0, https://pymupdf.readthedocs.io/), with `pypdf` (v6.13.3) and `pdfplumber`
(v0.11.10) as fallbacks for malformed PDFs and `lxml` (v6.1.1) for the XML (JATS) route,
followed by encoding normalisation (NFKC, ligature and line-break repair) so that the
original text organisation was preserved.

The resulting collection — **{{N:papers_ingested}} papers** — was designated as the
zebrafish literature corpus.

## 2.2 Zebrafish literature knowledge database construction

**Cleaning.** To improve retrieval quality, non-scientific text was removed before
segmentation. Each document was passed through a position-gated cleaning pipeline
implemented in `rag/text_clean.py` and `rag/pdf_fulltext_align.py`, which (i) removes
running page headers and footers by detecting repeated page-boundary blocks, (ii)
truncates the reference list, (iii) truncates acknowledgements, funding statements and
author-contribution sections, (iv) removes author / corresponding-author / conflict-of-interest
lines, (v) cleans figure-caption artefacts and publisher footers that survive text
extraction, and (vi) normalises encodings and line breaks (NFKC, ligature and hyphenation
repair, blank-line collapse). Rules (ii) and (iii) are gated by the position of the section
heading: the reference list is cut only when its heading occurs beyond 35% of the document,
and acknowledgements are cut only when the heading occurs beyond 25% and the section lies in
the second half, so that a “References” line inside the body can never truncate real
content. The real trigger rates of the pipeline were measured on a random sample of
{{N:clean_sample_n}} full texts (seed {{N:clean_seed}}) with
`scripts/measure_clean_effect.py`: reference-list truncation fired on
{{N:refs_truncation}}% and acknowledgement truncation on {{N:ack_truncation}}% of the
sampled papers, confirming that these sections are indeed present rather than assumed.

**Segmentation and vectorisation.** The cleaned corpus was segmented with a
section-aware paragraph strategy (`paragraph_section`): each natural paragraph was cut into
chunks of at most {{N:chunk_size}} characters (minimum {{N:chunk_min}} characters) with a
{{N:chunk_overlap}}-character overlap, and a section label was attached to every chunk so
that retrieval could be section-weighted — title 2.0, abstract 1.5, conclusion 1.5,
results 1.45, discussion 1.25, mixed 1.2, keywords 1.1, body 1.0, figure captions 0.9,
methods 0.85, other 0.8, and introduction 0.75, with reference sections down-weighted to
0.4 (weights are multiplicative on the L2 distance, i.e. `adjusted_L2 = raw_L2 / weight`).
An open-source embedding model, **Qwen3-Embedding-4B**, was employed to vectorise every
chunk into **{{N:embed_dim}} dimensions**, and the embeddings were indexed with **FAISS**
as {{N:faiss_shards}} shards covering **{{N:chunks}} chunks from {{N:papers_indexed}}
papers** (index version `{{N:index_version}}`, snapshot 2026-09-10); original text, section
label, DOI, PMID and paper identifier were stored alongside each chunk in the relational
store so that every retrieved passage stays traceable to its source record.

**Retrieval and answer generation.** For retrieval, the user query was first rewritten by
an LLM (`deepseek-chat`) using the multi-turn conversation context, so that pronouns,
abbreviations and implicit intents were expanded. The rewritten query was embedded with
the same model and parameters, and a two-stage retrieval was performed: coarse scoring
retrieved **{{N:cand}} candidate papers**, and a cross-encoder reranker
(**bge-reranker-base**, with a hybrid vector/keyword scorer as fallback) re-scored them to
keep the **top {{N:topn}}** highest-ranked passages; the final order also blends publication
year and journal weight. The selected passages, the
user question, and the fixed answer contract (a direct first-sentence answer, factor-wise
organisation, inline `[n]` citations, and a trailing numbered reference list without
dangling citations) were finally packed into a **{{N:pack_chars}}-character context budget**
and sent to the answering LLM. All retrieval and packing parameters are configuration
variables of the production service and were identical for all experiments reported here,
so that the effect of the answering route could be isolated.

## 2.3 Structured gene database construction

**Agent-side schema, ingestion and name-resolution layer (Kaifeng Qu).** To let the agent
resolve inconsistent gene nomenclature across genome versions, a relational schema (MySQL
8.0.36, database `zebrafish_db`) was created from scratch, comprising gene identity
(`gene_basic`, `gene_alias`, `gene_xref`), annotation (`gene_go`, `gene_kegg`), expression
(`gene_expression`) and literature-linkage (`gene_reference`) tables, together with the
ingestion and registration scripts that populate them idempotently. Because user queries
and historical papers use different identifiers, a resolution layer was implemented that
maps any incoming symbol, alias, or cross-database identifier onto the **NCBI RefSeq
assembly GRCz12tu (GCF_049306965.2) canonical symbol** by three-state logic —
`unique` / `ambiguous` / `not_found` — while the annotation content shown in the cards is
read from the local **Ensembl GRCz11 release 116** tables. GRCz12tu is therefore the
default assembly of the system: every coordinate-bearing result is reported on it. Gene
intervals that are natively annotated on GRCz12tu are taken directly from the RefSeq
annotation; the remaining gene-level intervals, which exist only in the GRCz11 annotation,
are projected onto GRCz12tu by **base-level liftover** using the UCSC chain files
(danRer11 → GCF_049306965.2 and its reverse), not by identifier crosswalk alone
(1,747 intervals projected, 160 genes without chain coverage reported as unplaced, and 40
projections whose length drifted by more than 20% flagged for review). Each coordinate
recorded in `gene_assembly_xref` therefore carries the field that produced it — RefSeq
native or liftover-projected — and the interface exposes that provenance with every
position it displays. Annotation values that exist only in Ensembl release 116 (GO terms,
Bgee/Expression Atlas values, KEGG pathways) are retained exactly as read from that
release, but every genomic interval attached to them is projected onto GRCz12tu by the
same liftover procedure, so no GRCz11 coordinate is presented as a GRCz12tu coordinate.
Ambiguous cases return all
candidate mappings instead of silently choosing one, so no identifier is overwritten or
guessed.

**Omics annotation data (\<Author B\>, to be confirmed).** Gene coordinates, symbols, Gene Ontology terms,
KEGG pathways, cross-database identifiers and expression records (Bgee v15.2 Expression
Score, 0–100; EBI Expression Atlas E-ERAD-475 embryo TPM) were collected and curated by
Author B and imported into the tables above; the agent treats them as a read-only source of
truth.

## 2.4 AI agent and Zebrafish Knowledge Hub construction

The **Zebrafish Research Assistant** agent is powered by the DeepSeek API
(`deepseek-chat` for routing, SQL generation and answer synthesis; `deepseek-reasoner` for
reasoning-intensive and judging tasks), accessed directly through its API
(https://api.deepseek.com/). The model was employed as a frozen reasoning engine without
any further pre-training or fine-tuning. It analyses user intent, selects an answering
route, invokes the structured database, the vector store, or external tools, and
synthesises the final answer from the retrieved context.

The agent is implemented as a multi-agent pipeline rather than a single prompt chain. A
five-tier intent gate (`agent/intent_router.py`) first classifies each turn: zebrafish
science (A) enters the full pipeline, product/meta questions (B) get a short explanatory
answer, adjacent biology (C) and ordinary conversation (D) are answered by a lightweight
general assistant that never queries the local database, and harmful requests (E) are
refused. A science turn is then executed by a hub orchestrator
(`agent/hub_orchestrator.py`) that selects one of the answer scopes — literature only,
gene DB only, ID mapping only, or the full Hub chain — and one of two depths: `partial`
(main path plus at most one specialist, enrichment skipped) or `full` (additional
specialists, longer context, query expansion). An answer-enrichment stage attaches
cross-database links (Ensembl, ZFIN, NCBI, UniProt, Bgee, Expression Atlas, QuickGO, KEGG
and PubMed) to the entities mentioned in the answer, so that each statement can be traced
back to either a local table or a public resource. Each answer carries bookkeeping metadata
(task type, generated SQL, retrieved context, gene card, tool invocations and their
structured results) that is exposed through the API response and the interface evidence
panel.

Concurrency is governed by a chat gate (`agent/chat_gate.py`) rather than a global lock:
at most `CHAT_MAX_CONCURRENCY` answers run at once (default 3), further requests wait in a
FIFO queue (64 positions, 900 s maximum wait, with the estimated wait returned to the
client), and the turns of one session stay strictly serial. Heavy models are loaded lazily
behind a lock, and MySQL plus the vector index are preloaded in the background at start-up,
so that health checks answer while the service is still warming. At deployment level a
watchdog (`deploy/hub_watchdog.sh`) probes the service every five minutes, restarts it when
the health check fails, and reclaims memory when the host is short of it and no session has
been active.

The Hub was developed as a **FastAPI** application (v0.137.2, https://fastapi.tiangolo.com/)
served by Uvicorn (v0.49.0); the public endpoint is published through a Cloudflare tunnel,
and an Nginx reverse-proxy profile is provided for institutional deployment. Python was used for the
backend logic (routing, retrieval, database access, agent orchestration, tool endpoints),
while HTML, CSS and vanilla JavaScript (hash-based routing, bilingual zh/en interface,
source badges, evidence panel) were used for the web-based user interface. The same
frontend is used for all tools described below, so that every result is displayed with its
data provenance.

## 2.5 API usage and research costs

The language-model APIs were provided by DeepSeek (https://api.deepseek.com/), whereas the
embedding model (Qwen3-Embedding-4B) and the cross-encoder reranker (bge-reranker-base) were
executed locally on our own GPU nodes, and the literature-cleaning stage is implemented as
deterministic, position-gated rules rather than LLM calls. Consequently the embedding,
reranking and cleaning stages incur no API cost, and all API expenditure of the project is
confined to literature screening and full-text parsing during knowledge-base construction,
and to answer generation and blind judging during evaluation.

Usage on the provider platform separates into two clearly distinct purposes (Table 2.1).

*Knowledge-base construction (July–August 2026).* Building the corpus consumed
**{{N:req_system}} requests and {{N:tokens_system}} tokens**. The two months have opposite
payload profiles, which reflects the two different jobs: July shows {{N:req_jul}} requests at a
mean of {{N:mean_jul}} tokens per request, consistent with screening titles and abstracts one
record at a time, whereas August shows only {{N:req_aug}} requests at a mean of
{{N:mean_aug}} tokens per request, consistent with submitting whole full texts for parsing and
gene-entity extraction.

*AI-assisted software development (September 2026).* The remaining
**{{N:tokens_dev_sep}} tokens** in September were consumed by the AI coding environments
described in section 2.6 (Cursor and Trae) driving the same provider account; their agentic
editing sends large repository contexts with every request, which explains the mean of
{{N:mean_sep}} tokens per request in that month. The {{N:eval_calls}} evaluation calls of this
study (60 blinded answer generations and 120 judge calls, each bounded by the context-packing
limit below) account for **at most {{N:eval_share}}%** of that month and are included there.

At run time, a per-request budget was enforced by the context packing limit
({{N:pack_chars}} characters) and per-client rate limits on the public service: 30 chat
requests, 120 API calls and 5 uploads per minute (`api/web_security.py`). Model labels are reported exactly as shown on the provider
dashboard; the model configured for generation in the deployed system corresponds to the
`deepseek-v4-flash` label.

**Table 2.1. API usage of the project (DeepSeek platform, as reported by the provider).**

| Purpose | Period | Model (provider label) | Requests | Tokens |
| --- | --- | --- | --- | --- |
| Knowledge-base construction | 2026-07 | deepseek-v4-flash | 273,356 | 75,898,279 |
| Knowledge-base construction | 2026-08 | deepseek-v4-flash | 2,963 | 627,891,392 |
| **Subtotal (construction)** | — | — | **276,319** | **703,789,671** |
| AI-assisted development (incl. evaluation calls) | 2026-09 | deepseek-flash | 1,985 | 433,762,479 |
| AI-assisted development | 2026-09 | deepseek-v4-flash | 302 | 912,580 |
| AI-assisted development | 2026-09 | deepseek-v4-pro | 43 | 1,768,737 |
| **Subtotal (development)** | — | — | **2,330** | **436,443,796** |
| **Total** | — | — | **{{N:req_total}}** | **{{N:tokens_total}}** |

## 2.6 Use of AI-assisted technologies

The software underlying this work was implemented with the assistance of AI coding
environments (Cursor and Trae), which were used for code editing, refactoring and debugging,
and parts of the manuscript text and figure captions were drafted with the same AI-assisted
tools. All AI-generated or AI-edited content was subsequently reviewed, verified and revised
by the authors, who validated every quantitative statement against the primary artefacts of
the project (corpus inventories, index manifests, scoring tables and raw model responses) and
who take full responsibility for the content of the manuscript. The deployed system itself
relies on the DeepSeek API for language generation, while embedding, reranking and cleaning
are executed locally or by deterministic rules; no generative AI tool is listed as an author.

## 2.7 Tools development

The Toolkit integrated in the Hub contains eleven tools, all developed in this work:
(i) **Gene Workbench**, which resolves a symbol or identifier through the resolution layer
and renders coordinates, expression, GO and cross-database identifiers; (ii) **BLAST**,
built on the NCBI BLAST+ toolkit (v2.17.0+, conda build, invoked through the
`BLAST_BIN` / `BLAST_DB_DIR` configuration) against a local RefSeq GRCz12tu CDS database
(`blast/cdna_db`, built by `_build_blast_db.py`); (iii) **GO/KEGG enrichment**, an over-representation
analysis with Fisher’s exact test and Benjamini–Hochberg correction computed against the
local background; (iv) **Pathway browser**; (v) **cross-database ID mapping** with the
three-state logic described above; (vi) **sequence extraction** (cDNA and genomic intervals
on the default GRCz12tu assembly; promoter windows from the annotated transcription start
site on GRCz11, with the assembly recorded in every record); (vii)
**one-click annotation**; (viii) **literature search workbench** with the two-stage
retrieval described in 2.2; (ix) **retrieval debugger**, which exposes the
coarse/reranked candidate list for reproducibility; (x) **document ingestion**, which
parses uploaded PDF/plain text, applies the cleaning pipeline and registers the resulting
chunks into the index; and (xi) **sgRNA and primer design** (`/api/tools/crispr/design`,
`/api/tools/primer/design`), which enumerates candidate sgRNAs under SpCas9 NGG/NAG,
Cas12a TTTV and SaCas9 NNGRRT PAMs, optionally appends T7 or SP6 transcription tails, and
reports off-target hits from a transcriptome scan by default (genome-wide scan optional),
with an explicit flag when a scan cannot be completed.
Tool responses return structured objects (never free text) so that
the frontend can label each field with its source resource and link. A visitor-statistics
view (`GET /api/hub/visit-stats`) aggregates the web-server access log into request counts,
distinct clients, the status-class distribution and the most frequently requested routes,
surfaced on the operations page; it keeps aggregate counts only, is used for operations
monitoring, and is not part of the results reported here.

## 2.8 Performance evaluation

A benchmark of **{{N:bench_n}} questions** was assembled covering knowledge-base queries,
gene-detail comprehension and sequence extraction, and the corresponding reference answers
were curated manually (`eval_isomorph_100_20260901.json`). Each of the 100 questions exists
in several isomorphs, i.e. paraphrases with identical meaning, so that answer quality can
be compared across configurations of the same system.

Scoring followed an LLM-as-a-judge framework with the project’s own seven-dimension rubric
(direct first-sentence answer, factor structure, gene/expression formulation, inline
citations, reference count, recency, and absence of a review-shell answer; total
{{N:rubric_total}} points). Candidate answers were **blinded**: for every question the
answers from the compared configurations were assigned shuffled labels A–F with a
deterministic seed, and the judge never saw the configuration names. To reduce the known
biases of LLM judges, every comparison was scored **twice by two independent judges**
(`deepseek-chat` and `deepseek-reasoner`), the per-item agreement was quantified
(Spearman {{N:judge_spearman}}), and configurations whose mean ranking was not reproducible
between the two judges were reported as such rather than as a ranking. Statistics were
computed with exact two-sided permutation tests written for this study (Mann–Whitney U over
all C(20,10) label assignments, and paired Wilcoxon by full 2^n sign-flip enumeration),
because with n = 10 per configuration and heavy ties (0.25-point score steps, zero-inflated
citation counts) the normal approximation deviates from the exact p-value by up to
{{N:approx_gap}} (largest observed gap among the comparisons reported here).

The database, indexes and the service itself were additionally validated by automated
assertion suites that recompute every reported number from the underlying artefacts
(corpus inventory, cleaning trigger rates, retrieval-fidelity probe, rubric coverage, and
the figure values themselves). The two panels comparing configuration behaviour are plotted
directly from the stored verbatim answers (`_config_sweep_answers.json`, written by real
calls to the service) together with the blinded score tables, whose every entry an
independent audit re-derives from the raw judge receipts with zero inconsistencies, and the
figure's own geometry is then reverse-read from the rendered image; no intermediate summary
enters this pipeline. The benchmark questions, the curated reference answers, the blinded
judge
inputs and raw outputs, the scoring tables, and the exact test results have been deposited
in {{TODO:data_deposit}}.
