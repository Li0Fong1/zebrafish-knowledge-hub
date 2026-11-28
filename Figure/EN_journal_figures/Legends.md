# Figure legends (v2, for the re-drawn figures)

All explanatory content removed from the artwork is written here; the canvases now carry only
panel letters, axis labels, step names and numbers.

---

**Fig. 1. Architecture of the Zebrafish Knowledge Hub.**
(A) A zebrafish-specific query is answered either by a general large language model, which relies on
unspecific training data and static knowledge and offers no zebrafish-specific resources, or by the
Zebrafish Research Assistant (ZRA), which routes the query to three curated modules: a literature
knowledge database (39,228 Q1/Q2 publications, 32,549 papers with full text, 7,651,942 indexed chunks
in 170 FAISS shards, retrieved through RAG), a gene database (32,139 ZFIN-anchored genes, 118,174
aliases, 109,508 cross-database identifiers, 110,632 GO and 171,079 phenotype annotations, 319,343
expression records, and coordinates on GRCz11 and GRCz12tu) and a toolkit (BLAST+, identifier
conversion, GO/KEGG enrichment, sequence and promoter extraction, sgRNA and primer design).
All counts are exact values of the deployed instance.

**Fig. 2. Ablation of retrieval configurations on ten benchmark questions.**
(A) Distribution of the total rubric score for the six configurations (RAG, Fusion and Hub, each with
a partial and a full index); the horizontal bar marks the mean, printed above each violin.
(B) The same scores decomposed into the seven rubric dimensions.
(C) End-to-end latency of the same runs. `partial` and `full` are the two answering depths exposed by
the service: both search the same 170-shard index and the same top-100 passages, but `partial` disables
the query-expansion step and previews a smaller candidate list, whereas `full` expands the query and
packs the larger candidate set into the answer. Answers were scored blind by deepseek-chat on seven
dimensions summing to 10 points; n = 10 questions per configuration. The spread across configuration
means is 0.55 points out of 10 (7.97–8.53) and no pair of configurations separates significantly under
the exact permutation test.

**Fig. 3. Inter-judge agreement of the LLM-as-a-judge procedure.**
(A) Item-level agreement between the two judges for the 60 scored answers (10 questions × 6
configurations); the dashed line is y = x and Spearman ρ = 0.754. (B) Mean score per configuration
for each judge. Judge B (deepseek-reasoner) is systematically stricter and does not reproduce the
ranking of judge A (rank correlation ρ = −0.03), which is why configuration effects are reported as
non-significant rather than ranked.

**Fig. S1. Publications per year in the analysed zebrafish literature corpus.**
Papers in the full-text corpus, plotted by publication year; each bar is annotated with the exact
number of papers published in that year. The hatched bar is 2026, which is incomplete at the export
date (2026-10-10). Bars cover 1990-2026 (32,481 papers); 51 papers dated before 1990 (earliest 1951)
are not drawn. Publication years are those recorded for each paper in the PubMed/WoS record.
The corpus was derived from 32,884 screened records, of which 32,549 yielded full text and 32,532
entered the vector index (7,651,942 chunks in 170 FAISS shards). The 17 records that yielded no
resolvable publication year (9 pdf_only, 1 pdf_fallback_not_gold, and 7 that produced no chunks) are
the same 17 absent from the index; of the 32,532 indexed papers, 51 predate 1990, leaving the 32,481
shown here. Only records that passed screening and yielded full text are plotted, so the bar totals
reconcile with the corpus reported in the Methods rather than with the larger screening catalogue
(35,261 candidate records).

**Fig. S2. The same six loci carry many identifiers across databases and assemblies.**
Each column is one gene. The upper block lists, for every gene, the identifier assigned by each major
database: the ZFIN identifier, the Ensembl gene identifier, the NCBI Gene identifier, the UniProt
accession and the symbol(s); these stay constant from database to database. The middle block shows how
the chromosome accession changes across assemblies. For the RefSeq assemblies the accession is NC_,
versioned .3 through .7 across Zv7, Zv8, Zv9 and GRCz11 (all NC_007xxx) before the new NC_133xxx
(GRCz12tu) and NC_141xxx (GRCz13ab) series are introduced. For the GenBank strain assemblies
(ASM1840007v1 T5D, ASM3317019v3 Tübingen, ASM2018471v1 AB, fDanRer4.1, fDreABH1.1 AB, fDreTuH1.2 Tu)
the accession is CP_/CM_/OX_/LR_; these assemblies carry no gene annotation, so the accession shown is
the chromosome on which the orthologous locus is projected by liftover from GRCz12tu. The lower block
lists the primary RefSeq transcript (NM_), whose prefix is stable while its version is bumped on
re-annotation (e.g. cyp19a1a NM_131154.3 → .4). The NCBI Gene identifier, by contrast, is unchanged in
every assembly; identity is carried by the ZFIN identifier and never by a chromosome accession or a
coordinate. In the symbol row the historical name precedes the current ZFIN symbol (p53/tp53, ta/tbxta,
shh/shha, fgf8/fgf8a, cyp19a/cyp19a1a, mstn/mstnb). Sources: NCBI Gene esummary, NCBI assembly reports,
Ensembl releases 79/116, ZFIN.

**Fig. S3. Pipeline of literature processing.**
Six stages, with the record count produced by each: retrieval from Web of Science and PubMed
(228,896 records), deduplication (100,424), relevance triage by a rule engine with the language model
applied only to the ambiguous band (52,255 primary studies), the CAS 2024 Q1/Q2 journal filter (39,228
studies), full-text acquisition (32,549 papers) and text extraction (32,532 indexed papers, 7,651,942
chunks).

**Fig. S4. Pipelines of the knowledge database.**
(A) Offline: full texts are cleaned with position-gated rules, cut into section-aware chunks of at most
360 characters with a 48-character overlap, embedded with Qwen3-Embedding-4B into 2,048 dimensions and
stored in a 170-shard FAISS index. (B) Online: the query is rewritten with the conversation context,
500 candidate papers are retrieved by section-weighted vector search, a cross-encoder reranker keeps
the top 100 passages, and the answer is generated within a 48,000-character context under a fixed
citation contract.

**Fig. S5. Effect of rule-based full-text cleaning.**
(A) Characters per full text before and after cleaning (mean and median, n = 999 papers, seed 42).
(B) Share of sampled papers in which the reference list or the acknowledgment section was detected and
truncated; the position gate prevents a heading that occurs in the body from truncating real content.

**Fig. S6. Scale and coverage of the curated zebrafish gene database.**
(A) Row counts of the twelve largest tables of the production database (exact COUNT(*), log scale).
(B) Share of the 32,139 curated genes that carry an Ensembl identifier, expression evidence, or at
least one linked publication.

**Fig. S7. Composition of the curated literature catalogue.**
(A) The twelve most represented journals. (B) Provenance of the records (Web of Science versus PubMed)
and their scope tier (core genetics versus the broader set of studies that use zebrafish as the study
subject in environmental and toxicological research). Journal names were case-normalised before
counting.

**Fig. S8. Commercial LLM usage during development.**
(A) Tokens consumed per month, separated into knowledge-base construction (July–August 2026) and
AI-assisted software development (September 2026). (B) Number of API requests on a log scale. The two
months of construction have opposite payload profiles: many short screening calls in July (273,356
requests, 278 tokens each) and few long full-text calls in August (2,963 requests, 211,910 tokens
each). Totals over the three months are 278,649 requests and 1.14 billion tokens. Embedding and
reranking run locally and consume no API tokens.
