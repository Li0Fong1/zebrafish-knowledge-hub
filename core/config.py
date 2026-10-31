# -*- coding: utf-8 -*-
"""项目统一配置，支持环境变量覆盖。"""
import os

def _project_root() -> str:
    env = os.environ.get("ZEBRAFISH_PROJECT_ROOT")
    if env:
        return env
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_BASE_DIR = _project_root()

# MySQL
# 密码必须来自 .env / 环境变量：不设弱口令默认值，缺失时连接会显式失败，
# 避免配置丢失后静默回退到旧密码（也不会在源码中出现任何口令）。
DB_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3306")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "database": os.getenv("MYSQL_DATABASE", "zebrafish_db"),
    "charset": "utf8mb4",
}

# Web 服务
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))


def _default_faiss_dir() -> str:
    """FAISS 索引放在无中文路径下，避免 Windows 写入失败。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ZebrafishGeneQA", "faiss_index")


# RAG
KNOWLEDGE_PATH = os.getenv("KNOWLEDGE_PATH", os.path.join(_BASE_DIR, "rag", "knowledge.txt"))
FAISS_INDEX_DIR = os.getenv("FAISS_INDEX_DIR", _default_faiss_dir())
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
RAG_CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "512"))
RAG_CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "50"))
# 送入推理模型的检索片段上限（字符）。须 ≥ 精细打分 100 篇的打包预算，避免尾部文献被裁掉
RAG_CONTEXT_MAX_CHARS = int(os.getenv("RAG_CONTEXT_MAX_CHARS", "48000"))

# 回答长度（前端展示与 Prompt 截断）；过小会把文末参考文献截掉
ANSWER_MAX_CHARS = int(os.getenv("ANSWER_MAX_CHARS", "8000"))

# 云端/深度推理单次最大生成 token（20 条参考文献需要远大于 1200）
LLM_REASONING_MAX_TOKENS = int(os.getenv("LLM_REASONING_MAX_TOKENS", os.getenv("LLM_MAX_NEW_TOKENS", "4096")))

# 回答模式：balanced=完整通俗（DeepSeek 网页版风格，默认）| academic=论文精准
ANSWER_MODE = os.getenv("ANSWER_MODE", "balanced").lower()

# 快速模式：减少 LLM 调用次数（CPU 环境推荐开启，可设为 0 关闭）
FAST_MODE = os.getenv("FAST_MODE", "1") not in ("0", "false", "False")

# 大模型：local=仅本地 | api=仅云端 | hybrid=云端优先、失败回退本地
LLM_MODE = os.getenv("LLM_MODE", "hybrid").lower()
LLM_API_BASE = os.getenv("LLM_API_BASE", "https://api.deepseek.com/v1")
LLM_API_MODEL = os.getenv("LLM_API_MODEL", "deepseek-chat")
LLM_API_TIMEOUT = int(os.getenv("LLM_API_TIMEOUT", "150"))

# 参考基因组（本地库锚定 GRCz11；NCBI Datasets 现行参考 GRCz12tu）
TAXON_ID = 7955
REFERENCE_ASSEMBLY = os.getenv("REFERENCE_ASSEMBLY", "GRCz11")
NCBI_ASSEMBLY_ACCESSION = os.getenv("NCBI_ASSEMBLY_ACCESSION", "GCF_000002035.6")
NCBI_GENBANK_ACCESSION = os.getenv("NCBI_GENBANK_ACCESSION", "GCA_000002035.4")
ENSEMBL_RELEASE = os.getenv("ENSEMBL_RELEASE", "116")

# NCBI Datasets 现行参考组装 GRCz12tu（Tübingen 品系，2024+）
NCBI_REFERENCE_ASSEMBLY = os.getenv("NCBI_REFERENCE_ASSEMBLY", "GRCz12tu")
NCBI_REFERENCE_REFSEQ = os.getenv("NCBI_REFERENCE_REFSEQ", "GCF_049306965.2")
NCBI_REFERENCE_GENBANK = os.getenv("NCBI_REFERENCE_GENBANK", "GCA_049306965.2")
NCBI_REFERENCE_STRAIN = os.getenv("NCBI_REFERENCE_STRAIN", "Tuebingen")
NCBI_DATASETS_URL = f"https://www.ncbi.nlm.nih.gov/datasets/genome/?taxon={TAXON_ID}"
NCBI_REFERENCE_DATASETS_URL = (
    f"https://www.ncbi.nlm.nih.gov/datasets/genome/{NCBI_REFERENCE_REFSEQ}/"
)
# 用户可见工具口径：标准名/BLAST/cDNA = GRCz12tu；gene_basic 坐标/表达/GO·KEGG = GRCz11
ASSEMBLY_POLICY = (
    f"标准名、BLAST 核酸库、cDNA = NCBI RefSeq {NCBI_REFERENCE_ASSEMBLY}"
    f"（{NCBI_REFERENCE_REFSEQ}）；"
    f"本地库 Ensembl ID / 染色体坐标 / 表达 / GO·KEGG 注释 = {REFERENCE_ASSEMBLY}"
    f"（Ensembl {ENSEMBL_RELEASE}，{NCBI_ASSEMBLY_ACCESSION}）。"
    "跨版本不得混用坐标。"
)

# 任务类型（路由与调度统一使用中文标签）
REFERENCE_FASTA = os.getenv("REFERENCE_FASTA", os.path.join(_BASE_DIR, "data", "reference.fasta"))
KEGG_PATHWAYS_JSON = os.getenv("KEGG_PATHWAYS_JSON", os.path.join(_BASE_DIR, "data", "kegg_pathways.json"))
KNOWLEDGE_EXTRA_DIR = os.getenv("KNOWLEDGE_EXTRA_DIR", os.path.join(_BASE_DIR, "rag", "knowledge.d"))
KNOWLEDGE_UPLOAD_DIR = os.getenv("KNOWLEDGE_UPLOAD_DIR", os.path.join(_BASE_DIR, "rag", "uploads"))

# PubMed 判别「是」文献向量库（独立 FAISS，不占用 DeepSeek token）
def _default_faiss_pubmed_yes_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ZebrafishGeneQA", "faiss_pubmed_yes")


FAISS_PUBMED_YES_DIR = os.getenv("FAISS_PUBMED_YES_DIR", _default_faiss_pubmed_yes_dir())


def _default_faiss_pubmed_yes_gte_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ZebrafishGeneQA", "faiss_pubmed_yes_gte_mbase_v1")


# 旁路新索引（gte）；Hub 默认仍用 FAISS_PUBMED_YES_DIR，勿在未验收前改指向
FAISS_PUBMED_YES_GTE_DIR = os.getenv("FAISS_PUBMED_YES_GTE_DIR", _default_faiss_pubmed_yes_gte_dir())
EMBEDDING_MODEL_PATH = os.getenv(
    "EMBEDDING_MODEL_PATH",
    os.path.join(_BASE_DIR, "models", "bge-small-zh-v1.5"),
)
# 可选 MRL 截断维（Qwen3-Embedding-4B 旁路索引用 2048）；空则用模型原生维
_EMBEDDING_DIM_RAW = (os.getenv("EMBEDDING_DIM") or "").strip()
EMBEDDING_DIM = int(_EMBEDDING_DIM_RAW) if _EMBEDDING_DIM_RAW.isdigit() else None
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")
GTE_MULTILINGUAL_BASE_PATH = os.getenv(
    "GTE_MULTILINGUAL_BASE_PATH",
    os.path.join(_BASE_DIR, "models", "gte-multilingual-base"),
)
PUBMED_YES_CORPUS_JSONL = os.getenv(
    "PUBMED_YES_CORPUS_JSONL",
    os.path.join(_BASE_DIR, "data", "literature", "pubmed_yes_corpus.jsonl"),
)
PUBMED_YES_SHARD_PAPERS = int(os.getenv("PUBMED_YES_SHARD_PAPERS", "5000"))
# recursive 模式用；paragraph 模式以 PARA_* 为准（勿再 max(...,900)）
PUBMED_YES_CHUNK_SIZE = int(os.getenv("PUBMED_YES_CHUNK_SIZE", "360"))
PUBMED_YES_CHUNK_OVERLAP = int(os.getenv("PUBMED_YES_CHUNK_OVERLAP", "48"))
PUBMED_YES_EMBED_BATCH = int(os.getenv("PUBMED_YES_EMBED_BATCH", "64"))
# 自然段短语义块（试跑默认：约 3～5 句中文 / 2～3 句英文）
PUBMED_YES_PARA_MAX_CHARS = int(os.getenv("PUBMED_YES_PARA_MAX_CHARS", "360"))
PUBMED_YES_PARA_MIN_CHARS = int(os.getenv("PUBMED_YES_PARA_MIN_CHARS", "110"))
# 分字段检索权重（越大越优先；检索时 adjusted_L2 = raw_L2 / weight）
PUBMED_YES_FIELD_WEIGHT_TITLE = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_TITLE", "2.0"))
PUBMED_YES_FIELD_WEIGHT_ABSTRACT = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_ABSTRACT", "1.5"))
PUBMED_YES_FIELD_WEIGHT_BODY = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_BODY", "1.0"))
PUBMED_YES_FIELD_WEIGHT_META = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_META", "0.7"))
PUBMED_YES_FIELD_WEIGHT_MIXED = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_MIXED", "1.2"))
# section 级权重（事项二；未分段仍用 body）
PUBMED_YES_FIELD_WEIGHT_CONCLUSION = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_CONCLUSION", "1.5"))
PUBMED_YES_FIELD_WEIGHT_RESULTS = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_RESULTS", "1.45"))
PUBMED_YES_FIELD_WEIGHT_DISCUSSION = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_DISCUSSION", "1.25"))
PUBMED_YES_FIELD_WEIGHT_METHODS = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_METHODS", "0.85"))
PUBMED_YES_FIELD_WEIGHT_INTRODUCTION = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_INTRODUCTION", "0.75"))
PUBMED_YES_FIELD_WEIGHT_REFERENCES = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_REFERENCES", "0.4"))
PUBMED_YES_FIELD_WEIGHT_FIGURE_CAPTIONS = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_FIGURE_CAPTIONS", "0.9"))
PUBMED_YES_FIELD_WEIGHT_OTHER = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_OTHER", "0.8"))
PUBMED_YES_FIELD_WEIGHT_KEYWORDS = float(os.getenv("PUBMED_YES_FIELD_WEIGHT_KEYWORDS", "1.1"))
# 切块时按 section 拆 body（生产开启，检索按章节加权）
PUBMED_YES_SECTION_CHUNKING = os.getenv("PUBMED_YES_SECTION_CHUNKING", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
# 查询中英/专名扩写（如 HIF、线粒体）
PUBMED_YES_QUERY_EXPAND = os.getenv("PUBMED_YES_QUERY_EXPAND", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
# pdf库/已存在表无 PMID 文献向量化语料（ENO 键）
EXIST_ENO_CORPUS_JSONL = os.getenv(
    "EXIST_ENO_CORPUS_JSONL",
    os.path.join(_BASE_DIR, "data", "literature", "exist_eno_corpus.jsonl"),
)
EXIST_ENO_SHARD_PAPERS = int(os.getenv("EXIST_ENO_SHARD_PAPERS", "2000"))
# 0 = 不截断正文 / 不限制块数（长文全量入索引）
EXIST_ENO_MAX_BODY_CHARS = int(os.getenv("EXIST_ENO_MAX_BODY_CHARS", "0"))
EXIST_ENO_MAX_CHUNKS = int(os.getenv("EXIST_ENO_MAX_CHUNKS", "0"))
# paragraph=自然段（马铃薯最优）；recursive=旧递归切
PUBMED_YES_SPLIT_MODE = os.getenv("PUBMED_YES_SPLIT_MODE", "paragraph").strip().lower()
# 两阶段检索（按篇）：粗打分 500 篇 → 精细打分 100 篇 → 相关度+新旧合并后送 LLM
PUBMED_YES_RETRIEVE_CANDIDATES = int(os.getenv("PUBMED_YES_RETRIEVE_CANDIDATES", "500"))
PUBMED_YES_RERANK_TOP_N = int(os.getenv("PUBMED_YES_RERANK_TOP_N", "100"))
PUBMED_YES_UNIQUE_PAPERS = int(os.getenv("PUBMED_YES_UNIQUE_PAPERS", "100"))
# 按篇编号打包进大模型的字符预算（须能装下约 100 篇：前列长摘录 + 后列题名）
PUBMED_YES_PACK_CHARS = int(os.getenv("PUBMED_YES_PACK_CHARS", "48000"))
PUBMED_YES_RERANK = os.getenv("PUBMED_YES_RERANK", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
# 文末参考文献最少条数：机制/性状/功能默认 20；事实题在代码里固定 6
REF_FOOTER_MIN = os.getenv("REF_FOOTER_MIN", "20").strip()
# 文献 blend：相关度 + 近年为主（IF 只作轻权重）
PUBMED_YES_IF_WEIGHT = os.getenv("PUBMED_YES_IF_WEIGHT", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)
PUBMED_YES_BLEND_W_REL = float(os.getenv("PUBMED_YES_BLEND_W_REL", "0.57"))
PUBMED_YES_BLEND_W_YEAR = float(os.getenv("PUBMED_YES_BLEND_W_YEAR", "0.39"))
PUBMED_YES_BLEND_W_IF = float(os.getenv("PUBMED_YES_BLEND_W_IF", "0.04"))

# 任务类型（路由与调度统一使用中文标签）
TASK_TYPES = {
    "GENERAL": "通用聊天",
    "DB_QUERY": "数据库查询",
    "RAG_QUERY": "知识库查询",
    "MULTI_MODEL": "多模型融合",
    "GENE_DETAIL": "基因详情",
    "BLAST": "序列比对",
    "ENRICHMENT": "功能富集",
}
