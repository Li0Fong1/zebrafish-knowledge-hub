# -*- coding: utf-8 -*-
import bootstrap  # noqa: F401

import os
import re
import sys
import threading
import time
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["FAISS_DISABLE_AVX2"] = "1"
import warnings
warnings.filterwarnings("ignore")

from transformers_compat import patch_transformers_compat
patch_transformers_compat()

from langchain_community.vectorstores import FAISS
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import TextLoader
from langchain.embeddings.base import Embeddings
from sentence_transformers import SentenceTransformer
from common_utils import logger
from config import (
    _BASE_DIR, KNOWLEDGE_PATH, FAISS_INDEX_DIR, KNOWLEDGE_EXTRA_DIR,
    RAG_TOP_K, RAG_CHUNK_SIZE, RAG_CHUNK_OVERLAP,
)

DEFAULT_KNOWLEDGE = """
斑马鱼是鲤科短担尼鱼属的模式生物，基因组大小约1.4Gb，包含约26000个蛋白编码基因。
斑马鱼hsp70基因：热休克蛋白基因，定位于第12号染色体，在肝脏、肌肉组织中高表达，参与高温胁迫响应。
斑马鱼sox9b基因：性别决定相关基因，在胚胎发育阶段的性腺组织中特异性表达。
斑马鱼nanog基因：多能性调控基因，维持胚胎干细胞的自我更新能力，表达于囊胚期。
斑马鱼基因命名规则：通常以基因功能缩写+数字命名，如hsp70（热休克蛋白70kDa）。
斑马鱼与人类基因同源性：约70%的人类基因在斑马鱼中有直系同源基因，适合疾病模型研究。
"""

EMBEDDING_MODEL_PATH = os.getenv(
    "EMBEDDING_MODEL_PATH",
    os.path.join(_BASE_DIR, "models", "bge-small-zh-v1.5"),
)


def _default_faiss_pubmed_yes_gte_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ZebrafishGeneQA", "faiss_pubmed_yes_gte_mbase_v1")


# 新索引旁路目录（默认不接 Hub；重嵌脚本显式使用）
FAISS_PUBMED_YES_GTE_DIR = os.getenv(
    "FAISS_PUBMED_YES_GTE_DIR",
    _default_faiss_pubmed_yes_gte_dir(),
)
GTE_MULTILINGUAL_BASE_PATH = os.getenv(
    "GTE_MULTILINGUAL_BASE_PATH",
    os.path.join(_BASE_DIR, "models", "gte-multilingual-base"),
)


def _tokenize_query(question: str) -> list:
    q = (question or "").lower()
    tokens = re.findall(r"[a-z0-9]{2,}", q)
    for seg in re.findall(r"[\u4e00-\u9fff]+", q):
        if len(seg) >= 2:
            tokens.append(seg)
        for i in range(len(seg) - 1):
            tokens.append(seg[i : i + 2])
    seen = set()
    out = []
    for t in tokens:
        if t not in seen and len(t) >= 2:
            seen.add(t)
            out.append(t)
    return out


def _process_wide_cache() -> tuple[dict, "threading.Lock"]:
    """进程级共享的嵌入模型缓存（挂到 sys 上，而不是模块级全局变量）。

    线上实测问题：本文件会被以两种模块名重复导入 —— 一部分代码写
    `from rag_builder import ...`（rag/ 已在 sys.path 上），另一部分写
    `from rag.rag_builder import ...`。模块对象不同，模块级缓存也就各有一份，
    于是同一个 Qwen3-Embedding-4B（约 15GB）被加载两次，Hub 常驻内存直接顶到
    cgroup 36GB 上限，文献分片一个都装不下（表现为“0/170 分片，检索未就绪”）。
    缓存挂到 sys 后，无论模块被导入几次都共用同一份权重。
    """
    ns = getattr(sys, "_zf_embed_cache_ns", None)
    if ns is None:
        ns = {"cache": {}, "lock": threading.Lock()}
        setattr(sys, "_zf_embed_cache_ns", ns)
    return ns["cache"], ns["lock"]


_EMBED_CACHE, _EMBED_LOCK = _process_wide_cache()

# 顺手把本模块同时登记在两种名字下（同一个对象），后续导入不再产生第二份实例
try:
    if __name__ in ("rag_builder", "rag.rag_builder"):
        for _alias in ("rag_builder", "rag.rag_builder"):
            sys.modules.setdefault(_alias, sys.modules[__name__])
except Exception:  # pragma: no cover - 极端环境下的导入期保护
    pass


def _normalize_device(device: str) -> str:
    """无可用 CUDA 时把 cuda:* 归一化为 cpu。

    两个必须修的问题：
    1) 缓存键按 device 区分 → Hub 主链用 cpu、PubmedYes 用 cuda:1，同一个 4B 模型会被加载两份；
    2) 无 GPU 机器上按 cuda 构造会直接抛异常（PubmedYes 预热“第N次失败”的根源之一）。
    """
    d = (device or "").strip().lower()
    if not d.startswith("cuda"):
        return device
    try:
        import torch

        if torch.cuda.is_available() and torch.cuda.device_count() > 0:
            return device
    except Exception:
        pass
    return "cpu"


def get_shared_embedding(
    model_path: str | None = None,
    *,
    truncate_dim: int | None = None,
    device: str | None = None,
) -> "BgeEmbedding":
    """进程内复用同一嵌入模型，避免知识库 RAG + PubmedYes 双载 OOM。"""
    path = model_path or EMBEDDING_MODEL_PATH
    if truncate_dim is None:
        raw = (os.getenv("EMBEDDING_DIM") or "").strip()
        truncate_dim = int(raw) if raw.isdigit() else None
    device = device or os.getenv("EMBEDDING_DEVICE", "cpu")
    device = _normalize_device(device)
    key = (os.path.abspath(path), truncate_dim, device)
    with _EMBED_LOCK:
        hit = _EMBED_CACHE.get(key)
        if hit is not None:
            return hit
        emb = BgeEmbedding(path, truncate_dim=truncate_dim, device=device)
        _EMBED_CACHE[key] = emb
        return emb


class BgeEmbedding(Embeddings):
    def __init__(
        self,
        model_path: str,
        *,
        trust_remote_code: bool | None = None,
        truncate_dim: int | None = None,
        device: str | None = None,
    ):
        # 默认 CPU；可用 EMBEDDING_DEVICE=cuda:0 加速大模型查询编码
        device = device or os.getenv("EMBEDDING_DEVICE", "cpu")
        device = _normalize_device(device)
        kwargs = {"device": device}
        # gte / Qwen3-Embedding 等需执行仓库内 modeling 代码
        name = os.path.basename(os.path.normpath(model_path)).lower()
        if trust_remote_code is None:
            trust_remote_code = any(
                k in name for k in ("gte", "alibaba", "qwen", "embedding-4b", "embedding-8b")
            )
        if trust_remote_code:
            kwargs["trust_remote_code"] = True
        # GPU 上大模型用 fp16，避免 4B 以 fp32 占满 16GB
        if str(device).startswith("cuda"):
            try:
                import torch

                kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
            except Exception:
                pass
        # MRL：索引以 EMBEDDING_DIM（如 Qwen3-4B=2048）构建时须一致截断
        if truncate_dim is None:
            raw = (os.getenv("EMBEDDING_DIM") or "").strip()
            truncate_dim = int(raw) if raw.isdigit() else None
        self.truncate_dim = truncate_dim
        self.model = self._build_model(model_path, kwargs, truncate_dim)
        logger.info(
            f"[成功] 嵌入模型加载完成：{model_path} device={device} dim={truncate_dim or 'native'}"
        )

    @staticmethod
    def _build_model(model_path: str, kwargs: dict, truncate_dim: int | None):
        """构造 SentenceTransformer，带**竞态重试**。

        为什么要重试（2026-10-11 实测的真事故）：
          并发加载（知识库 RAG 线程 + PubmedYes 首次检索）时偶发
              RuntimeError: Cannot copy out of meta tensor; no data!
          这是 accelerate/torch 的加载竞态，不是模型缺失。当时 hub.log 里检索路径
          连续三次都栽在这里 → 文献检索整条失败 → 没有证据池 → 答案没有引用。
          外层的预热代码本来就有重试，但**检索路径没有**，所以补在这一层。
        """
        last: Exception | None = None
        for attempt in range(3):
            try:
                if truncate_dim:
                    try:
                        return SentenceTransformer(
                            model_path, truncate_dim=truncate_dim, **kwargs
                        )
                    except TypeError:
                        kwargs.pop("model_kwargs", None)
                        return SentenceTransformer(model_path, **kwargs)
                try:
                    return SentenceTransformer(model_path, **kwargs)
                except TypeError:
                    kwargs.pop("model_kwargs", None)
                    return SentenceTransformer(model_path, **kwargs)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if ("meta tensor" not in msg) and ("to_empty" not in msg):
                    raise
                last = e
                logger.warning(
                    f"[嵌入] 加载命中 meta-tensor 竞态（第 {attempt + 1}/3 次），3s 后重试：{msg[:110]}"
                )
                time.sleep(3)
        raise last if last is not None else RuntimeError("嵌入模型加载失败")

    def _encode(self, texts):
        import numpy as np

        try:
            if self.truncate_dim:
                vecs = self.model.encode(
                    texts,
                    normalize_embeddings=True,
                    truncate_dim=self.truncate_dim,
                )
            else:
                vecs = self.model.encode(texts, normalize_embeddings=True)
        except TypeError:
            vecs = self.model.encode(texts, normalize_embeddings=True)
            if self.truncate_dim:
                arr = np.asarray(vecs)
                if arr.ndim == 1:
                    arr = arr[: self.truncate_dim]
                    arr = arr / (np.linalg.norm(arr) + 1e-12)
                    return arr.tolist()
                arr = arr[:, : self.truncate_dim]
                norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-12
                return (arr / norms).tolist()
        return vecs.tolist() if hasattr(vecs, "tolist") else list(vecs)

    def embed_documents(self, texts):
        return self._encode(texts)

    def embed_query(self, text):
        out = self._encode([text])
        return out[0]


class ZebraFishRAG:
    def __init__(self, knowledge_path: str = None):
        self.knowledge_path = knowledge_path or KNOWLEDGE_PATH
        self.index_dir = FAISS_INDEX_DIR
        self.embedding = None
        self.db = None
        self.retriever = None
        self._init_knowledge_file()
        self._paragraphs = self._load_paragraphs()
        logger.info(f"[成功] 知识库已加载 {len(self._paragraphs)} 段，关键词检索立即可用")
        threading.Thread(target=self._build_rag_safe, daemon=True, name="rag-faiss").start()

    def _init_knowledge_file(self):
        if not os.path.exists(self.knowledge_path):
            os.makedirs(os.path.dirname(self.knowledge_path) or ".", exist_ok=True)
            with open(self.knowledge_path, "w", encoding="utf-8") as f:
                f.write(DEFAULT_KNOWLEDGE.strip())
            logger.info(f"[成功] 知识库文件已创建：{self.knowledge_path}")

    def _collect_knowledge_paths(self) -> list:
        paths = []
        if os.path.isfile(self.knowledge_path):
            paths.append(self.knowledge_path)
        if os.path.isdir(KNOWLEDGE_EXTRA_DIR):
            for fn in sorted(os.listdir(KNOWLEDGE_EXTRA_DIR)):
                if fn.endswith((".txt", ".md")):
                    paths.append(os.path.join(KNOWLEDGE_EXTRA_DIR, fn))
        return paths

    def _paragraph_priority(self, path: str) -> int:
        base = os.path.basename(path).lower()
        if base == "knowledge.txt":
            return 0
        if base.startswith("hub_"):
            return 1
        if base.startswith("literature_"):
            return 3
        return 2

    def _load_paragraphs(self) -> list:
        paras = []
        for path in self._collect_knowledge_paths():
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except OSError as e:
                logger.warning(f"读取知识库失败 {path}：{e}")
                continue
            priority = self._paragraph_priority(path)
            for block in re.split(r"\n\s*\n", text):
                block = block.strip()
                if block:
                    paras.append((priority, block))
        if not paras:
            paras = [(0, DEFAULT_KNOWLEDGE.strip())]
        return paras

    def _score_paragraph(self, question: str, tokens: list, priority: int, para: str) -> int:
        text_lower = para.lower()
        score = sum(text_lower.count(t) for t in tokens)
        title_m = re.match(r"【([^】]+)】", para)
        if title_m:
            title = title_m.group(1).lower()
            for t in tokens:
                if t in title:
                    score += 20
        q = question.lower()
        for phrase, bonus in (
            ("参考基因组", 25), ("基因组版本", 25), ("命名体系", 12),
            ("grcz11", 20), ("grcz10", 15), ("实验技术", 12),
            ("crispr", 35), ("基因编辑", 30), ("morpholino", 20),
        ):
            if phrase in q and phrase in text_lower:
                score += bonus
        if "实验技术" in para and any(k in q for k in ("crispr", "编辑", "注射", "转基因", "protocol")):
            score += 25
        if para.startswith("【PubMed") or para.startswith("摘要："):
            score -= 8
        if "TIER:core_genetics" in para or "literature_core_" in para:
            score += 35
        score += max(0, (3 - priority) * 12)
        return score

    def _keyword_retrieve(self, question: str, top_k: int = None) -> str:
        k = top_k or RAG_TOP_K
        tokens = _tokenize_query(question)
        if not tokens:
            return ""
        scored = []
        for priority, para in self._paragraphs:
            score = self._score_paragraph(question, tokens, priority, para)
            if score > 0:
                scored.append((score, para))
        scored.sort(key=lambda x: (-x[0], -len(x[1])))
        return "\n\n".join(p for _, p in scored[:k])

    def _knowledge_mtime(self) -> float:
        mtimes = []
        for p in self._collect_knowledge_paths():
            try:
                mtimes.append(os.path.getmtime(p))
            except OSError:
                pass
        return max(mtimes) if mtimes else 0.0

    def _load_documents(self):
        docs = []
        for path in self._collect_knowledge_paths():
            loader = TextLoader(path, encoding="utf-8")
            docs.extend(loader.load())
        if not docs:
            with open(self.knowledge_path, "w", encoding="utf-8") as f:
                f.write(DEFAULT_KNOWLEDGE.strip())
            loader = TextLoader(self.knowledge_path, encoding="utf-8")
            docs = loader.load()
        return docs

    def _build_rag_safe(self):
        try:
            self._build_rag()
        except Exception as e:
            logger.warning(f"FAISS 向量索引后台构建失败（关键词检索仍可用）：{e}")

    def _build_rag(self):
        documents = self._load_documents()
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "；", "，", " "],
        )
        texts = text_splitter.split_documents(documents)
        self.embedding = get_shared_embedding()
        meta_path = os.path.join(self.index_dir, "knowledge.mtime")
        os.makedirs(self.index_dir, exist_ok=True)

        current_mtime = self._knowledge_mtime()
        if os.path.isdir(self.index_dir) and os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    saved_mtime = float(f.read().strip())
                if abs(saved_mtime - current_mtime) < 0.001:
                    self.db = FAISS.load_local(
                        self.index_dir, self.embedding, allow_dangerous_deserialization=True
                    )
                    logger.info("[成功] 已加载缓存的 FAISS 向量索引")
                    self.retriever = self.db.as_retriever(search_kwargs={"k": RAG_TOP_K})
                    return
            except Exception as e:
                logger.warning(f"[警告] 索引缓存加载失败，将重建：{e}")

        if (os.getenv("SKIP_KNOWLEDGE_FAISS_REBUILD") or "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            logger.info("[提示] SKIP_KNOWLEDGE_FAISS_REBUILD=1，跳过知识库向量重建（关键词检索仍可用）")
            return

        logger.info("[后台] 正在构建 FAISS 向量索引（不影响当前问答）…")
        self.db = FAISS.from_documents(texts, self.embedding)
        try:
            self.db.save_local(self.index_dir)
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write(str(current_mtime))
            logger.info(f"[成功] 斑马鱼 RAG 向量索引构建完成：{self.index_dir}")
        except Exception as e:
            logger.warning(f"[警告] FAISS 索引无法写入磁盘（将仅内存运行）：{e}")
        self.retriever = self.db.as_retriever(search_kwargs={"k": RAG_TOP_K})

    def append_documents(self, paths: list) -> int:
        """把指定文本文件追加进已有 FAISS 索引（增量，不整体重嵌入）。

        为什么不直接 rebuild：knowledge.d 现在 160+ 个文件、63 MB，切块后十几万段，
        纯 CPU 上整体重建要几个小时；而每周新增只有几百篇。这里只嵌入新增的那几个
        文件，秒级完成，随后把 knowledge.mtime 指纹刷成最新，避免下次重启又判定
        「文件变过 → 全量重建」。
        """
        files = [p for p in (paths or []) if p and os.path.isfile(p)]
        if not files:
            return 0
        if self.embedding is None:
            self.embedding = get_shared_embedding()
        if self.db is None:
            self.db = FAISS.load_local(
                self.index_dir, self.embedding, allow_dangerous_deserialization=True
            )
        docs = []
        for p in files:
            docs.extend(TextLoader(p, encoding="utf-8").load())
        if not docs:
            return 0
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "；", "，", " "],
        )
        chunks = splitter.split_documents(docs)
        self.db.add_documents(chunks)
        self.db.save_local(self.index_dir)
        try:
            with open(os.path.join(self.index_dir, "knowledge.mtime"), "w", encoding="utf-8") as f:
                f.write(str(self._knowledge_mtime()))
        except OSError as e:
            logger.warning(f"[知识库] 索引指纹写入失败：{e}")
        self.retriever = self.db.as_retriever(search_kwargs={"k": RAG_TOP_K})
        # 关键词检索用的是内存里的段落表，也要立刻看到新内容
        self._paragraphs = self._load_paragraphs()
        logger.info(f"[知识库] 增量索引完成：{len(files)} 个文件 / {len(chunks)} 段")
        return len(chunks)

    def get_context(self, question: str) -> str:
        context = self._keyword_retrieve(question)
        pubmed_ctx = ""
        if os.getenv("PUBMED_YES_RAG", "1") not in ("0", "false", "False"):
            try:
                from rag.pubmed_yes_retriever import get_pubmed_yes_retriever, peek_pubmed_yes_retriever

                pr = peek_pubmed_yes_retriever() or get_pubmed_yes_retriever()
                if pr.ready:
                    pubmed_ctx = pr.get_context(question, top_k=max(RAG_TOP_K, 5))
            except Exception as e:
                logger.warning(f"PubMed YES 向量检索失败：{e}")
        if self.retriever:
            try:
                docs = self.retriever.invoke(question)
                faiss_ctx = "\n\n".join(doc.page_content.strip() for doc in docs)
                if faiss_ctx and len(faiss_ctx) > len(context):
                    context = faiss_ctx
            except Exception as e:
                logger.warning(f"向量检索失败: {e!r}")
        parts = [p for p in (pubmed_ctx, context) if p]
        merged = "\n\n".join(parts)
        if merged:
            logger.info(f"[检索] 检索到相关上下文：{merged[:50]}...")
        return merged


rag_instance = None
_rag_lock = threading.Lock()


def get_rag():
    global rag_instance
    if rag_instance is None:
        with _rag_lock:
            if rag_instance is None:
                rag_instance = ZebraFishRAG()
    return rag_instance
