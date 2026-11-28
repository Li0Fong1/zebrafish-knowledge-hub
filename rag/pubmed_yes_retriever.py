# -*- coding: utf-8 -*-
"""PubMed 判别「是」文献分片 FAISS 检索器（字段加权 + 查询扩写 + PMID 精确命中）。"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("FAISS_DISABLE_AVX2", "1")

from langchain_community.vectorstores import FAISS

from config import (
    FAISS_PUBMED_YES_DIR,
    PUBMED_YES_PACK_CHARS,
    PUBMED_YES_RERANK,
    PUBMED_YES_RERANK_TOP_N,
    PUBMED_YES_RETRIEVE_CANDIDATES,
    PUBMED_YES_UNIQUE_PAPERS,
    RAG_TOP_K,
)
from rag.pubmed_yes_chunking import (
    adjust_l2_score,
    expand_queries,
    field_weight,
    infer_field_from_text,
    keyword_title_boost,
    topical_match_boost,
)
from rag.rerank import rerank_hits
from rag.rag_builder import get_shared_embedding

MANIFEST = "manifest.json"


# ---------------------------------------------------------------------------
# 内存预算：分片 + Qwen3-Embedding-4B 全量加载实测需 30GB 以上真实内存，
# 而服务器 cgroup memory.max 只有 36GB —— 不做预算就会被内核 OOM 杀掉整个 Hub
# （无 traceback，站点整体不可用，外部守护反复重启）。
# 这里改为“装得下多少装多少”，并将实际加载数写进返回体（shards_loaded/shards_total/partial）。
# ---------------------------------------------------------------------------
def _mem_headroom_mb() -> float:
    """可分配内存余量（MB）= cgroup 上限 − 匿名内存；取不到就返回 inf（如本地 Windows）。"""
    try:
        rel = (
            open("/proc/self/cgroup", encoding="utf-8")
            .read().strip().splitlines()[-1].split(":", 2)[-1].strip()
        )
        d = os.path.join("/sys/fs/cgroup", rel.lstrip("/")) if rel and rel != "/" else "/sys/fs/cgroup"
        for _ in range(10):
            mx = os.path.join(d, "memory.max")
            if os.path.isfile(mx):
                raw = open(mx, encoding="utf-8").read().strip()
                if raw.isdigit():
                    limit = int(raw) / 1048576.0
                    anon = 0.0
                    stat = os.path.join(d, "memory.stat")
                    if os.path.isfile(stat):
                        for line in open(stat, encoding="utf-8"):
                            k, _, v = line.partition(" ")
                            if k == "anon" and v.strip().isdigit():
                                anon = int(v.strip()) / 1048576.0
                                break
                    if not anon:
                        anon = int(open(os.path.join(d, "memory.current"), encoding="utf-8").read().strip()) / 1048576.0
                    return limit - anon
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
    except Exception:
        pass
    return float("inf")


# 每个分片加载前至少要留多少余量；不够就停止继续加载（宁可少加载，不能被 OOM 杀掉）
_PUBMED_YES_SHARD_RESERVE_MB = float(os.getenv("PUBMED_YES_SHARD_RESERVE_MB", "4000"))
# 分片预读窗口：后台线程领先加载进度 N 个分片下发 posix_fadvise(WILLNEED)。
# 2026-10-31 实测（服务器 HDD /dev/sdh1, rotational=1）：**无收益**，故默认关闭。
#   12 个分片冷读：窗口 0 = 23.1s，窗口 6 = 22.9s。
#   原因是瓶颈已经在盘上：dd iflag=direct 顺序读 246MB/s 就是上限，
#   单分片 296MB 读 1.2s + faiss 解析 0.28s，预读只是把同一份 I/O 提前排队。
# 保留开关：换 NVMe / 网络盘、或分片远小于读带宽时可能有用。
_PUBMED_YES_PREFETCH = int(os.getenv("PUBMED_YES_PREFETCH", "0") or 0)
# 不用 \b：中文紧跟 PMID 数字时 Unicode 词边界会失败（如 PMID:36795618相关）
_PMID_RE = re.compile(r"(?i)(?:pmid[:\s#]*)?(\d{6,9})(?!\d)")
_ENO_RE = re.compile(r"(?i)(?:eno[:\s#]*|entry[_-]?no[:\s#]*|【PDFKU ENO:)(\d+)(?!\d)")


class PubmedYesRetriever:
    """多 shard FAISS 合并检索；字段加权 + 可选查询扩写；支持 PMID 与 ENO。"""

    def __init__(self, index_dir: Optional[str] = None):
        self.index_dir = Path(index_dir or FAISS_PUBMED_YES_DIR)
        # PubmedYes 检索用独立设备（PUBMED_EMBEDDING_DEVICE，默认 GPU1），
        # 避免与 Hub 主嵌入（GPU0）争显存导致 CUDA OOM；get_shared_embedding 按 device 分缓存
        _dev = os.getenv("PUBMED_EMBEDDING_DEVICE", "") or "cuda:1"
        self.embedding = get_shared_embedding(device=_dev)
        self._shards: list[FAISS] = []
        self._shards_total: int = 0
        self._shards_skipped_low_mem: int = 0
        self._pmid_index: dict[str, list[tuple[str, dict]]] | None = None
        self._eno_index: dict[str, list[tuple[str, dict]]] | None = None
        self._prefetch_stop = threading.Event()
        self._load_shards()

    @staticmethod
    def _fadvise_willneed(path: Path) -> None:
        """告诉内核这个文件马上要整段读，先异步预读进 page cache。

        只影响 I/O 调度，不占进程匿名内存；Windows/无此 API 的平台直接跳过。
        """
        fn = getattr(os, "posix_fadvise", None)
        if fn is None:
            return
        try:
            fd = os.open(str(path), os.O_RDONLY)
        except Exception:
            return
        try:
            fn(fd, 0, 0, os.POSIX_FADV_WILLNEED)
        except Exception:
            pass
        finally:
            try:
                os.close(fd)
            except Exception:
                pass

    def _start_prefetch(self, shard_ids: list[int]) -> Optional[threading.Thread]:
        """后台预读线程：始终领先加载进度 _PUBMED_YES_PREFETCH 个分片。"""
        if _PUBMED_YES_PREFETCH <= 0:
            return None

        def _worker() -> None:
            i = 0
            total = len(shard_ids)
            while i < total and not self._prefetch_stop.is_set():
                done = len(self._shards) + self._shards_skipped_low_mem
                if i - done >= _PUBMED_YES_PREFETCH:
                    # 领先够了就等加载线程追上来，避免把整库 59GB 一次性灌进内存
                    if self._prefetch_stop.wait(0.2):
                        return
                    continue
                sd = self.index_dir / f"shard_{shard_ids[i]:04d}"
                self._fadvise_willneed(sd / "index.faiss")
                self._fadvise_willneed(sd / "index.pkl")
                i += 1

        t = threading.Thread(target=_worker, name="pubmed-prefetch", daemon=True)
        t.start()
        return t

    def _stop_prefetch(self, pf: Optional[threading.Thread]) -> None:
        self._prefetch_stop.set()
        if pf is not None and pf.is_alive():
            pf.join(timeout=2)

    def _load_shards(self) -> None:
        manifest_path = self.index_dir / MANIFEST
        shard_ids: list[int] = []
        if manifest_path.is_file():
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            shard_ids = sorted(m.get("shards") or [])
        if not shard_ids:
            shard_ids = sorted(
                int(p.name.split("_")[1])
                for p in self.index_dir.glob("shard_*")
                if (p / "index.faiss").is_file()
            )
        avail = [sid for sid in shard_ids if (self.index_dir / f"shard_{sid:04d}" / "index.faiss").is_file()]
        self._shards_total = len(avail)
        t0 = time.time()
        pf = self._start_prefetch(avail)
        try:
            for sid in avail:
                # 内存预算：装不下了就停（比加载到一半被 OOM 杀掉整个服务强得多）
                if _mem_headroom_mb() < _PUBMED_YES_SHARD_RESERVE_MB:
                    self._shards_skipped_low_mem = len(avail) - len(self._shards)
                    print(
                        f"[文献向量库] 内存余量不足，已加载 {len(self._shards)}/{self._shards_total} 个分片，"
                        f"余下 {self._shards_skipped_low_mem} 个跳过（检索结果为部分分片；"
                        f"可用 PUBMED_YES_SHARD_RESERVE_MB=0 关闭本限制）",
                        flush=True,
                    )
                    break
                sd = self.index_dir / f"shard_{sid:04d}"
                try:
                    vs = FAISS.load_local(
                        str(sd), self.embedding, allow_dangerous_deserialization=True
                    )
                    self._shards.append(vs)
                except Exception:
                    continue
                # 让出一点时间，避免预热期整机卡死导致 /api/health Failed to fetch
                try:
                    time.sleep(0.01)
                except Exception:
                    pass
        finally:
            self._stop_prefetch(pf)
            _n = len(self._shards)
            print(
                f"[文献向量库] 分片加载完成 {_n}/{self._shards_total}，耗时 {time.time() - t0:.1f}s"
                f"（预读窗口 {_PUBMED_YES_PREFETCH}）",
                flush=True,
            )

    def _ensure_id_indexes(self) -> None:
        if self._pmid_index is not None and self._eno_index is not None:
            return
        pmid_idx: dict[str, list[tuple[str, dict]]] = {}
        eno_idx: dict[str, list[tuple[str, dict]]] = {}
        for vs in self._shards:
            store = getattr(vs.docstore, "_dict", None)
            if not isinstance(store, dict):
                continue
            for doc in store.values():
                meta = dict(getattr(doc, "metadata", None) or {})
                text = getattr(doc, "page_content", "") or ""
                field = infer_field_from_text(text, meta)
                meta["field"] = field
                pmid = str(meta.get("pmid") or "").strip()
                eno = str(meta.get("entry_no") or "").strip()
                if pmid:
                    pmid_idx.setdefault(pmid, []).append((text, meta))
                if eno:
                    eno_idx.setdefault(eno, []).append((text, meta))
        order = {"title": 0, "abstract": 1, "mixed": 2, "body": 3, "meta": 4}
        for idx in (pmid_idx, eno_idx):
            for _k, items in idx.items():
                items.sort(key=lambda x: order.get((x[1].get("field") or "mixed"), 9))
        self._pmid_index = pmid_idx
        self._eno_index = eno_idx

    @staticmethod
    def extract_pmids(query: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for m in _PMID_RE.finditer(query or ""):
            pid = m.group(1)
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
        return out

    @staticmethod
    def extract_enos(query: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for m in _ENO_RE.finditer(query or ""):
            eno = m.group(1)
            if eno not in seen:
                seen.add(eno)
                out.append(eno)
        return out

    def lookup_pmid(self, pmid: str, *, limit: int = 8) -> list[dict]:
        self._ensure_id_indexes()
        assert self._pmid_index is not None
        return self._hits_from_items(self._pmid_index.get(str(pmid).strip()) or [], limit=limit, match="pmid_exact")

    def lookup_eno(self, entry_no: str, *, limit: int = 8) -> list[dict]:
        self._ensure_id_indexes()
        assert self._eno_index is not None
        return self._hits_from_items(self._eno_index.get(str(entry_no).strip()) or [], limit=limit, match="eno_exact")

    @staticmethod
    def _hits_from_items(items: list[tuple[str, dict]], *, limit: int, match: str) -> list[dict]:
        out = []
        for text, meta in items[:limit]:
            out.append(
                {
                    "score": 0.0,
                    "raw_score": 0.0,
                    "field": meta.get("field"),
                    "field_weight": field_weight(meta.get("field"), meta.get("section")),
                    "text": text,
                    "pmid": meta.get("pmid") or "",
                    "entry_no": meta.get("entry_no") or "",
                    "doc_id": meta.get("doc_id") or "",
                    "title_zh": meta.get("title_zh"),
                    "year": meta.get("year") or "",
                    "has_fulltext": meta.get("has_fulltext"),
                    "chunk_index": meta.get("chunk_index"),
                    "source": meta.get("source") or "local",
                    "match": match,
                }
            )
        return out

    @property
    def ready(self) -> bool:
        return bool(self._shards)

    def search(
        self,
        query: str,
        top_k: int = None,
        *,
        expand: bool | None = None,
        candidates: int | None = None,
    ) -> list[dict]:
        """粗打分 500 篇（FAISS 按篇去重）→ 精细打分 100 篇（CE/混合）→ 相关度+年份合并。"""
        coarse_n = int(candidates) if candidates is not None else PUBMED_YES_RETRIEVE_CANDIDATES
        fine_n = int(top_k) if top_k is not None else (
            PUBMED_YES_RERANK_TOP_N if PUBMED_YES_RERANK else RAG_TOP_K
        )
        coarse_n = max(fine_n, coarse_n)
        if not self._shards:
            return []

        exact: list[dict] = []
        for pid in self.extract_pmids(query):
            exact.extend(self.lookup_pmid(pid, limit=max(2, fine_n // 2)))
        for eno in self.extract_enos(query):
            exact.extend(self.lookup_eno(eno, limit=max(2, fine_n // 2)))
        seen_exact: set[str] = set()
        exact_dedup: list[dict] = []
        for h in exact:
            key = f"{h.get('doc_id') or h.get('pmid') or h.get('entry_no')}|{h.get('chunk_index')}|{hash((h.get('text') or '')[:80])}"
            if key in seen_exact:
                continue
            seen_exact.add(key)
            exact_dedup.append(h)
        exact = exact_dedup[:fine_n]

        queries = expand_queries(query, enabled=expand)
        # 每 shard 多取，保证粗排能凑够 500 篇而不是 500 块
        per_q = max(8, min(48, (coarse_n * 4) // max(1, len(self._shards))))
        best: dict[str, tuple[float, float, str, dict]] = {}
        query_vectors: list[tuple[str, list[float]]] = []
        for q in queries:
            try:
                query_vectors.append((q, self.embedding.embed_query(q)))
            except Exception:
                continue
        for q, q_vec in query_vectors:
            for vs in self._shards:
                try:
                    pairs = vs.similarity_search_with_score_by_vector(q_vec, k=per_q)
                except Exception:
                    continue
                for doc, score in pairs:
                    meta = dict(doc.metadata or {})
                    text = doc.page_content or ""
                    field = infer_field_from_text(text, meta)
                    meta["field"] = field
                    boost = keyword_title_boost(query, text, meta.get("title_zh"))
                    topic = topical_match_boost(query, text)
                    adj = adjust_l2_score(float(score), field, meta.get("section")) / max(1.0, boost) / max(0.1, topic)
                    key = f"{meta.get('doc_id') or meta.get('pmid')}|{meta.get('chunk_index')}|{hash(text[:120])}"
                    prev = best.get(key)
                    if prev is None or adj < prev[0]:
                        best[key] = (adj, float(score), text, meta)

        ranked = sorted(best.values(), key=lambda x: x[0])
        semantic: list[dict] = []
        seen_paper: set[str] = set()
        for adj, raw, text, meta in ranked:
            if meta.get("field") == "title" and self._is_bad_title_text(text or ""):
                continue
            hit = {
                "score": adj,
                "raw_score": raw,
                "field": meta.get("field"),
                "field_weight": field_weight(meta.get("field"), meta.get("section")),
                "text": text,
                "pmid": meta.get("pmid") or "",
                "entry_no": meta.get("entry_no") or "",
                "doc_id": meta.get("doc_id") or "",
                "title_zh": meta.get("title_zh"),
                "year": meta.get("year") or "",
                "has_fulltext": meta.get("has_fulltext"),
                "chunk_index": meta.get("chunk_index"),
                "source": meta.get("source") or "local",
                "match": "semantic",
            }
            pk = self._paper_key(hit)
            if pk in seen_paper:
                continue
            seen_paper.add(pk)
            semantic.append(hit)
            if len(semantic) >= coarse_n:
                break

        merged: list[dict] = []
        seen: set[str] = set()
        for h in exact + semantic:
            pk = self._paper_key(h)
            if pk in seen:
                continue
            seen.add(pk)
            if h.get("field") == "title" and self._is_bad_title_text(h.get("text") or ""):
                continue
            merged.append(h)
            if len(merged) >= coarse_n:
                break

        unique_n = max(fine_n, PUBMED_YES_UNIQUE_PAPERS)
        if PUBMED_YES_RERANK:
            ranked_hits = rerank_hits(query, merged, top_n=fine_n)
            return self.diversify_and_recency(
                ranked_hits,
                question=query,
                unique_papers=unique_n,
                max_chunks=unique_n,
            )
        return self.diversify_and_recency(
            merged[:fine_n],
            question=query,
            unique_papers=unique_n,
            max_chunks=unique_n,
        )

    @staticmethod
    def _is_bad_title_text(text: str) -> bool:
        low = (text or "").lower()
        if "doi 10." in low or "doi:10." in low or "doi: 10." in low:
            return True
        if re.search(r"10\.\d{4,9}/", low):
            return True
        return False

    @classmethod
    def _is_doi_as_title(cls, title: str) -> bool:
        t = (title or "").strip()
        if not t:
            return True
        low = t.lower()
        if low.startswith("doi ") or low.startswith("doi:") or low.startswith("http"):
            return True
        if t.startswith("10.") and "/" in t[:24] and len(t) < 200:
            return True
        if re.fullmatch(r"(?i)doi\s*10\.\S+", t.replace("：", ":")):
            return True
        return False

    @staticmethod
    def _paper_key(h: dict) -> str:
        p = str(h.get("pmid") or "").strip()
        if p.isdigit():
            return f"pmid:{p}"
        e = str(h.get("entry_no") or "").strip()
        if e:
            return f"eno:{e}"
        return f"doc:{h.get('doc_id') or hash((h.get('text') or '')[:80])}"

    @classmethod
    def _hit_year(cls, h: dict) -> int | None:
        """优先元数据 /「来源信息」行里的四位年，不用正文里引用文献的年份。"""
        now = datetime.now().year

        def _ok(y: int) -> bool:
            return 1980 <= y <= now + 1

        raw = h.get("year")
        if raw not in (None, "", "年不详"):
            try:
                y = int(str(raw).strip()[:4])
                if _ok(y):
                    return y
            except (TypeError, ValueError):
                pass
        blob = f"{h.get('title_zh') or ''}\n{h.get('text') or ''}"
        for line in blob.splitlines()[:24]:
            if "来源信息" not in line:
                continue
            # 「2017」「2017年」「2017 Jun」都算；不要求刚好独占一个 | 段
            for m in re.finditer(r"(?:19|20)\d{2}", line.replace("｜", "|")):
                y = int(m.group(0))
                if _ok(y):
                    return y
        title = (h.get("title_zh") or "")[:160]
        m = re.search(r"\b((?:19|20)\d{2})\b", title)
        if m:
            y = int(m.group(1))
            if _ok(y):
                return y
        return None

    @classmethod
    def _fill_years_from_db(cls, hits: list[dict]) -> None:
        """FAISS metadata 常缺 year：用 literature_paper 按 PMID/ENO 补发表年。"""
        need_p: list[str] = []
        need_e: list[str] = []
        for h in hits:
            if cls._hit_year(h):
                continue
            p = str(h.get("pmid") or "").strip()
            e = str(h.get("entry_no") or "").strip()
            if p.isdigit():
                need_p.append(p)
            elif e:
                need_e.append(e)
        if not need_p and not need_e:
            return
        try:
            from mysql_client import get_mysql_client

            clauses: list[str] = []
            if need_p:
                ins = ",".join(f"'{x}'" for x in dict.fromkeys(need_p) if x.isdigit())
                if ins:
                    clauses.append(f"pmid IN ({ins})")
            if need_e:
                ins = ",".join(
                    f"'{x}'" for x in dict.fromkeys(need_e) if re.fullmatch(r"[A-Za-z0-9_-]+", x)
                )
                if ins:
                    clauses.append(f"entry_no IN ({ins})")
            if not clauses:
                return
            sql = (
                "SELECT pmid, entry_no, year FROM literature_paper "
                f"WHERE year IS NOT NULL AND ({' OR '.join(clauses)})"
            )
            rows = get_mysql_client().execute_sql(sql, max_rows=200)
            data = (rows or {}).get("data") or []
            by_p: dict[str, int] = {}
            by_e: dict[str, int] = {}
            for r in data:
                try:
                    y = int(str(r.get("year")).strip()[:4])
                except (TypeError, ValueError):
                    continue
                if not (1980 <= y <= datetime.now().year + 1):
                    continue
                if r.get("pmid"):
                    by_p[str(r["pmid"])] = y
                if r.get("entry_no"):
                    by_e[str(r["entry_no"])] = y
            for h in hits:
                if cls._hit_year(h):
                    continue
                p = str(h.get("pmid") or "").strip()
                e = str(h.get("entry_no") or "").strip()
                y = by_p.get(p) or by_e.get(e)
                if y:
                    h["year"] = y
        except Exception:
            return

    @classmethod
    def _fill_titles_from_db(cls, hits: list[dict]) -> None:
        """用 literature_paper 补英文题名、期刊、DOI。显示优先英文题名。"""
        need_p: list[str] = []
        need_e: list[str] = []
        for h in hits:
            p = str(h.get("pmid") or "").strip()
            e = str(h.get("entry_no") or "").strip()
            if p.isdigit():
                need_p.append(p)
            elif e:
                need_e.append(e)
        if not need_p and not need_e:
            return
        try:
            from mysql_client import get_mysql_client

            clauses: list[str] = []
            if need_p:
                ins = ",".join(f"'{x}'" for x in dict.fromkeys(need_p) if x.isdigit())
                if ins:
                    clauses.append(f"pmid IN ({ins})")
            if need_e:
                ins = ",".join(
                    f"'{x}'" for x in dict.fromkeys(need_e) if re.fullmatch(r"[A-Za-z0-9_-]+", x)
                )
                if ins:
                    clauses.append(f"entry_no IN ({ins})")
            if not clauses:
                return
            sql = (
                "SELECT pmid, entry_no, title_en, title_zh, journal, doi FROM literature_paper "
                f"WHERE ({' OR '.join(clauses)})"
            )
            rows = get_mysql_client().execute_sql(sql, max_rows=200)
            by_p: dict[str, dict] = {}
            by_e: dict[str, dict] = {}
            for r in (rows or {}).get("data") or []:
                if r.get("pmid"):
                    by_p[str(r["pmid"])] = r
                if r.get("entry_no"):
                    by_e[str(r["entry_no"])] = r
            for h in hits:
                p = str(h.get("pmid") or "").strip()
                e = str(h.get("entry_no") or "").strip()
                row = by_p.get(p) or by_e.get(e)
                if not row:
                    continue
                te = str(row.get("title_en") or "").strip()
                tz = str(row.get("title_zh") or "").strip()
                if te and not cls._is_doi_as_title(te):
                    h["title_en"] = te
                if tz and not cls._is_doi_as_title(tz) and not h.get("title_zh"):
                    h["title_zh"] = tz
                journal = str(row.get("journal") or "").strip()
                doi = str(row.get("doi") or "").strip()
                if journal:
                    h["journal"] = journal
                if doi:
                    h["doi"] = doi
        except Exception:
            return

    @classmethod
    def _fill_zfqa_from_db(cls, hits: list[dict]) -> None:
        """按 PMID/ENO/doc_id 批量补 literature_paper.zfqa_id（进程内小缓存）。"""
        if not hits:
            return
        if not hasattr(cls, "_zfqa_cache"):
            cls._zfqa_cache: dict[str, str] = {}
        cache: dict[str, str] = cls._zfqa_cache
        need: list[tuple[str, str]] = []
        for h in hits:
            if h.get("zfqa_id"):
                continue
            p = str(h.get("pmid") or "").strip()
            e = str(h.get("entry_no") or "").strip()
            d = str(h.get("doc_id") or "").strip()
            for kind, val in (("pmid", p), ("eno", e), ("doc", d)):
                if not val:
                    continue
                ck = f"{kind}:{val}"
                if ck in cache:
                    h["zfqa_id"] = cache[ck]
                    break
                need.append((kind, val))
        if not need:
            return
        clauses: list[str] = []
        pmids = [v for k, v in dict.fromkeys(need) if k == "pmid" and v.isdigit()]
        enos = [v for k, v in dict.fromkeys(need) if k == "eno" and re.fullmatch(r"[A-Za-z0-9_-]+", v)]
        docs = [v for k, v in dict.fromkeys(need) if k == "doc" and v]
        if pmids:
            ins = ",".join(f"'{x}'" for x in pmids)
            clauses.append(f"pmid IN ({ins})")
        if enos:
            ins = ",".join(f"'{x}'" for x in enos)
            clauses.append(f"entry_no IN ({ins})")
        if docs:
            ins = ",".join(f"'{x}'" for x in docs if re.fullmatch(r"[A-Za-z0-9_:-]+", x))
            if ins:
                clauses.append(f"doc_id IN ({ins})")
        if not clauses:
            return
        try:
            from mysql_client import get_mysql_client

            sql = (
                "SELECT doc_id, pmid, entry_no, zfqa_id FROM literature_paper "
                f"WHERE zfqa_id IS NOT NULL AND zfqa_id<>'' AND ({' OR '.join(clauses)})"
            )
            rows = get_mysql_client().execute_sql(sql, max_rows=200)
            data = (rows or {}).get("data") or []
            for r in data:
                z = str(r.get("zfqa_id") or "").strip()
                if not z:
                    continue
                if r.get("pmid"):
                    cache[f"pmid:{r['pmid']}"] = z
                if r.get("entry_no"):
                    cache[f"eno:{r['entry_no']}"] = z
                if r.get("doc_id"):
                    cache[f"doc:{r['doc_id']}"] = z
            for h in hits:
                if h.get("zfqa_id"):
                    continue
                p = str(h.get("pmid") or "").strip()
                e = str(h.get("entry_no") or "").strip()
                d = str(h.get("doc_id") or "").strip()
                z = cache.get(f"pmid:{p}") or cache.get(f"eno:{e}") or cache.get(f"doc:{d}") or ""
                if z:
                    h["zfqa_id"] = z
        except Exception:
            return

    @classmethod
    def _fill_journal_from_db(cls, hits: list[dict]) -> None:
        """按 PMID/ENO 批量补 literature_paper.journal。"""
        need_p: list[str] = []
        need_e: list[str] = []
        for h in hits:
            if str(h.get("journal") or "").strip():
                continue
            p = str(h.get("pmid") or "").strip()
            e = str(h.get("entry_no") or "").strip()
            if p.isdigit():
                need_p.append(p)
            elif e:
                need_e.append(e)
        if not need_p and not need_e:
            return
        try:
            from mysql_client import get_mysql_client

            clauses: list[str] = []
            if need_p:
                ins = ",".join(f"'{x}'" for x in dict.fromkeys(need_p) if x.isdigit())
                if ins:
                    clauses.append(f"pmid IN ({ins})")
            if need_e:
                ins = ",".join(
                    f"'{x}'" for x in dict.fromkeys(need_e) if re.fullmatch(r"[A-Za-z0-9_-]+", x)
                )
                if ins:
                    clauses.append(f"entry_no IN ({ins})")
            if not clauses:
                return
            sql = (
                "SELECT pmid, entry_no, journal FROM literature_paper "
                f"WHERE journal IS NOT NULL AND journal<>'' AND ({' OR '.join(clauses)})"
            )
            rows = get_mysql_client().execute_sql(sql, max_rows=200)
            by_p: dict[str, str] = {}
            by_e: dict[str, str] = {}
            for r in (rows or {}).get("data") or []:
                j = str(r.get("journal") or "").strip()
                if not j:
                    continue
                if r.get("pmid"):
                    by_p[str(r["pmid"])] = j
                if r.get("entry_no"):
                    by_e[str(r["entry_no"])] = j
            for h in hits:
                if str(h.get("journal") or "").strip():
                    continue
                p = str(h.get("pmid") or "").strip()
                e = str(h.get("entry_no") or "").strip()
                j = by_p.get(p) or by_e.get(e)
                if j:
                    h["journal"] = j
        except Exception:
            return

    @classmethod
    def diversify_and_recency(
        cls,
        hits: list[dict],
        *,
        question: str = "",
        unique_papers: int = 100,
        max_chunks: int = 100,
    ) -> list[dict]:
        """相关度 + 近年 +（可选）期刊 IF；按篇去重。"""
        if not hits:
            return []
        cls._fill_years_from_db(hits)
        use_if = False
        try:
            from config import PUBMED_YES_IF_WEIGHT

            use_if = bool(PUBMED_YES_IF_WEIGHT)
        except Exception:
            use_if = os.getenv("PUBMED_YES_IF_WEIGHT", "1").strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
        if use_if:
            cls._fill_journal_from_db(hits)
        else:
            # 即使关闭 IF 权重，脚注仍需要刊名
            cls._fill_journal_from_db(hits)
        try:
            from rag.journal_enrich import enrich_hits
        except ImportError:
            try:
                from journal_enrich import enrich_hits
            except ImportError:
                enrich_hits = None  # type: ignore
        if enrich_hits is not None:
            # 先只走本地 XML/已有；批量外网放 pack / footer，避免拖慢粗排
            enrich_hits(hits, allow_network=False, persist=True)
        scored: list[tuple[float, dict]] = []
        rels = []
        for h in hits:
            rels.append(float(h.get("rerank_score") if h.get("rerank_score") is not None else (-(h.get("score") or 0))))
        lo, hi = (min(rels), max(rels)) if rels else (0.0, 1.0)
        span = (hi - lo) or 1.0
        now = datetime.now().year
        try:
            from config import (
                PUBMED_YES_BLEND_W_IF,
                PUBMED_YES_BLEND_W_REL,
                PUBMED_YES_BLEND_W_YEAR,
            )

            w_rel, w_year, w_if = (
                float(PUBMED_YES_BLEND_W_REL),
                float(PUBMED_YES_BLEND_W_YEAR),
                float(PUBMED_YES_BLEND_W_IF) if use_if else 0.0,
            )
        except Exception:
            w_rel, w_year, w_if = 0.55, 0.40, 0.05 if use_if else 0.0
        _wsum = (w_rel + w_year + w_if) or 1.0
        w_rel, w_year, w_if = w_rel / _wsum, w_year / _wsum, w_if / _wsum
        profile = "legacy"
        gamma = 0.0
        if use_if:
            try:
                try:
                    from rag.journal_if import GAMMA, blend_weights, detect_rank_profile, lookup_if
                except ImportError:
                    from journal_if import GAMMA, blend_weights, detect_rank_profile, lookup_if
                from zebrafish_prompt import classify_answer_type

                at = classify_answer_type(question) if question else "general"
                profile = detect_rank_profile(question, at)
                w = blend_weights(profile)
                w_rel, w_year, w_if = w["rel"], w["year"], w["if"]
                gamma = float(GAMMA.get(profile) or 0.0)
            except Exception:
                use_if = False
        _w_used = {"rel": round(w_rel, 3), "year": round(w_year, 3), "if": round(w_if, 3),
                   "profile": profile}
        for h, rel in zip(hits, rels):
            hh = dict(h)
            hh["blend_weights"] = _w_used
            y = cls._hit_year(hh)
            hh["year"] = y or hh.get("year") or ""
            rel_n = (rel - lo) / span
            # 存下来供证据池编号行与 RRS 相关度维使用：
            # 以前 rel_n 只用于算 blend_score 排序，分数本身没落盘 →
            # 下游只能拿「名次」当相关度的代理（用户 2026-10-11 指出这个问题）。
            hh["rel_n"] = round(float(rel_n), 4)
            if y:
                year_n = max(0.0, min(1.0, (y - 1998) / max(1.0, now - 1998)))
            else:
                year_n = 0.40
            if_score = 0.45
            if use_if:
                try:
                    ji = lookup_if(str(hh.get("journal") or ""))
                    if_score = ji.if_score
                    hh["if_tier"] = ji.tier
                    hh["if_score"] = if_score
                    hh["rank_profile"] = profile
                except Exception:
                    hh["if_tier"] = "T4"
                    hh["if_score"] = if_score
                year_star = max(year_n, gamma * if_score) if gamma else year_n
                hh["blend_score"] = w_rel * rel_n + w_year * year_star + w_if * if_score
            else:
                hh["blend_score"] = w_rel * rel_n + w_year * year_n
            scored.append((hh["blend_score"], hh))
        scored.sort(key=lambda x: x[0], reverse=True)
        ordered = [h for _, h in scored]
        unique: list[dict] = []
        seen: set[str] = set()
        for h in ordered:
            k = cls._paper_key(h)
            if k in seen:
                continue
            seen.add(k)
            unique.append(h)
            if len(unique) >= unique_papers:
                break
        extra_budget = max(0, max_chunks - len(unique))
        used_ids = {(cls._paper_key(h), h.get("chunk_index")) for h in unique}
        out = list(unique)
        allow = {cls._paper_key(h) for h in unique}
        for h in ordered:
            if extra_budget <= 0:
                break
            k = cls._paper_key(h)
            ident = (k, h.get("chunk_index"))
            if ident in used_ids or k not in allow:
                continue
            if sum(1 for x in out if cls._paper_key(x) == k) >= 2:
                continue
            out.append(h)
            used_ids.add(ident)
            extra_budget -= 1
        return out[:max_chunks]

    @staticmethod
    def _preview_text(text: str, n: int = 160) -> str:
        t = " ".join((text or "").replace("\n", " ").split())
        return t[:n] + ("…" if len(t) > n else "")

    @staticmethod
    def _hit_tag(h: dict) -> str:
        if h.get("pmid"):
            return f"PMID:{h.get('pmid')}"
        z = str(h.get("zfqa_id") or "").strip()
        if z:
            return z
        if h.get("entry_no"):
            return f"ENO:{h.get('entry_no')}"
        return str(h.get("doc_id") or "?")

    @staticmethod
    def _source_tier(h: dict) -> str:
        """来源可信度分层：本地库权威 > PubMed 官方 > 开放全文 > PDF 解析。"""
        src = str(h.get("source") or "").lower()
        if "pubmed" in src or "pmid" in src:
            return "PubMed官方"
        if "scihub" in src or "gold" in src or "html" in src or "xml" in src:
            return "本地库/开放全文"
        if "eno" in src or "pdf" in src or "relaxed" in src:
            return "PDF解析"
        return "本地库"

    @staticmethod
    def _recency_label(year) -> str:
        now = datetime.now().year
        try:
            y = int(str(year).strip()[:4])
        except (TypeError, ValueError):
            return "年份未知"
        if y >= now - 5:
            return "近五年"
        if y >= now - 10:
            return "近十年"
        if y >= now - 20:
            return "十年以上"
        return "较早"

    @staticmethod
    def _excerpt_budget(rank: int) -> int:
        """前列给够摘要，后列保题名；避免 100 篇时每篇只剩 120 字。"""
        if rank <= 20:
            return 520
        if rank <= 45:
            return 300
        if rank <= 75:
            return 180
        return 90

    @classmethod
    def _cut_evidence(cls, raw: str, excerpt_n: int) -> tuple[str, str]:
        raw = raw or ""
        src_line = ""
        for line in raw.splitlines()[:24]:
            if "来源信息" in line:
                src_line = line.strip()[:180]
                break
        if excerpt_n <= 0:
            return src_line, ""
        cut = raw
        for marker in ("摘要（中）：", "摘要（英）：", "摘要：", "Abstract", "ABSTRACT", "正文"):
            pos = raw.find(marker)
            if pos >= 0:
                cut = raw[pos:]
                break
        return src_line, cut[:excerpt_n]

    @staticmethod
    def _marked_span(text: str, markers: tuple[str, ...], stop_at: tuple[str, ...], n: int) -> str:
        raw = text or ""
        for marker in markers:
            i = raw.find(marker)
            if i < 0:
                continue
            rest = raw[i + len(marker) :]
            stop = len(rest)
            for other in stop_at:
                j = rest.find(other)
                if 0 <= j < stop:
                    stop = j
            bit = re.sub(r"\s+", " ", rest[:stop]).strip(" ：:")
            if bit:
                return bit[:n]
        return ""

    @classmethod
    def _related_paragraphs(cls, chunks: list[dict], n: int) -> str:
        """相关段落 = 命中段 + 其前一段 + 其后一段（有则都取）。

        检索按篇通常只留一块：在块内按空行切自然段，定位命中后再拼前后。
        """
        paras: list[str] = []
        for ch in chunks[:2]:
            raw = ch.get("text") or ""
            body = raw
            for marker in ("正文（中）：", "正文（英）：", "正文：", "正文"):
                i = raw.find(marker)
                if i >= 0:
                    body = raw[i + len(marker) :]
                    break
            else:
                body = cls._marked_span(raw, ("摘要（英）：", "摘要（中）：", "摘要："), (), 10_000) or raw
            bits = [
                re.sub(r"\s+", " ", p).strip()
                for p in re.split(r"\n\s*\n", body)
                if p.strip() and not p.strip().startswith("标题") and "来源信息" not in p[:12]
            ]
            if not bits:
                flat = re.sub(r"\s+", " ", body).strip()
                if flat:
                    bits = [flat]
            if not bits:
                continue
            # 块内已有多段上下文时，取中间偏前作命中，便于前后都有；否则首段即命中
            hit_idx = 1 if len(bits) >= 3 else 0
            ordered: list[str] = []
            if hit_idx > 0:
                ordered.append(bits[hit_idx - 1][:n])
            ordered.append(bits[hit_idx][:n])
            if hit_idx + 1 < len(bits):
                ordered.append(bits[hit_idx + 1][:n])
            # 去重保序（相邻段内容偶发重复时）
            for p in ordered:
                if p and p not in paras:
                    paras.append(p)
            break
        return "\n".join(f"- {p}" for p in paras[:3])

    @staticmethod
    def _journal_from_chunks(chunks: list[dict]) -> str:
        """来源信息行里的刊名。跳过 ENO / DOI / 年份。"""
        for ch in chunks[:2]:
            for line in (ch.get("text") or "").splitlines()[:24]:
                if "来源信息" not in line:
                    continue
                rest = line.split("来源信息", 1)[-1].lstrip("：: ")
                for bit in (b.strip() for b in rest.split("|")):
                    if not bit:
                        continue
                    if re.match(r"(?i)^(ENO|DOI|PMID|PMC|JUDGEMENT|FULLTEXT)\b", bit):
                        continue
                    if re.fullmatch(r"\d{4}", bit) or bit.startswith("10."):
                        continue
                    return bit[:180]
        return ""

    def _paper_block(self, i: int, g: dict, excerpt_n: int, title_n: int) -> str:
        # 检索相关度（0~1）：由 pack_for_ai 从 hits 的 rel_n 带过来；拿不到就回退旧格式
        try:
            rel = float(g.get("rel") or 0.0)
        except (TypeError, ValueError):
            rel = 0.0
        title = (g.get("title") or "").strip()
        if self._is_doi_as_title(title):
            title = ""
        if not title:
            blob = ""
            if g.get("chunks"):
                blob = g["chunks"][0].get("text") or ""
            for line in blob.splitlines():
                s = line.strip()
                if not s or "来源信息" in s or self._is_doi_as_title(s):
                    continue
                if s.startswith("摘要") or s.lower().startswith("abstract"):
                    continue
                title = s[:title_n]
                break
        if not title or self._is_doi_as_title(title):
            title = g.get("tag") or "local paper"
        year = g["year"] or "年不详"
        recency = self._recency_label(year)
        journal = (g.get("journal") or "").strip()
        if not journal and g.get("chunks"):
            journal = self._journal_from_chunks(g["chunks"])
        if not journal:
            try:
                from rag.journal_enrich import resolve_journal
            except ImportError:
                try:
                    from journal_enrich import resolve_journal
                except ImportError:
                    resolve_journal = None  # type: ignore
            if resolve_journal is not None:
                journal = resolve_journal(
                    pmid=str(g.get("pmid") or ""),
                    doi=str(g.get("doi") or ""),
                    title=str(g.get("title") or ""),
                    year=str(g.get("year") or ""),
                    entry_no=str(g.get("entry_no") or ""),
                    allow_network=True,
                    persist=True,
                )
            else:
                journal = "Unlisted journal"
        g["journal"] = journal
        doi = (g.get("doi") or "").strip()
        abstract = ""
        paras = ""
        if excerpt_n > 0 and g.get("chunks"):
            raw = "\n".join((c.get("text") or "") for c in g["chunks"][:2])
            abstract = self._marked_span(
                raw,
                ("摘要（英）：", "摘要（中）：", "摘要：", "Abstract:", "ABSTRACT"),
                ("正文（", "正文：", "正文:", "标题（", "来源信息"),
                min(280, excerpt_n),
            )
            paras = self._related_paragraphs(g["chunks"], min(220, max(80, excerpt_n)))
        lines = [
            f"[{i}] {year} | {g['tag']} | {g['tier']} | 相关#{i} rel={rel:.3f} · {recency}"
            if rel > 0
            else f"[{i}] {year} | {g['tag']} | {g['tier']} | 相关#{i} · {recency}",
            f"题名：{title}",
            f"期刊：{journal}",
        ]
        if doi:
            lines.append(f"DOI：{doi}")
        if abstract:
            lines.append(f"摘要：{abstract}")
        if paras:
            lines.append(f"相关段落：\n{paras}")
        return "\n".join(lines).rstrip()

    def pack_for_ai(self, hits: list[dict], *, max_chars: int | None = None) -> dict:
        """按篇编号 [1][2]… 打包。前列长摘录、后列保题名，尽量装下精细打分的约 100 篇。"""
        if max_chars is None:
            max_chars = PUBMED_YES_PACK_CHARS
        max_chars = max(8000, int(max_chars))
        self._fill_zfqa_from_db(hits)
        self._fill_titles_from_db(hits)
        self._fill_journal_from_db(hits)
        try:
            from rag.journal_enrich import enrich_hits
        except ImportError:
            try:
                from journal_enrich import enrich_hits
            except ImportError:
                enrich_hits = None  # type: ignore
        if enrich_hits is not None:
            enrich_hits(hits, allow_network=True, persist=True)
        groups: list[dict] = []
        index: dict[str, int] = {}
        for h in hits:
            key = self._paper_key(h)
            if key not in index:
                index[key] = len(groups)
                groups.append(
                    {
                        "key": key,
                        "tag": self._hit_tag(h),
                        "pmid": h.get("pmid") or "",
                        "entry_no": h.get("entry_no") or "",
                        "zfqa_id": h.get("zfqa_id") or "",
                        "year": self._hit_year(h),
                        "title": (
                            (h.get("title_en") or h.get("title_zh") or "").strip()
                        ),
                        "journal": (h.get("journal") or "").strip() or "Unlisted journal",
                        "doi": (h.get("doi") or "").strip(),
                        "tier": self._source_tier(h),
                        # 归一化语义相关度（同一篇的多个 chunk 取最大）
                        "rel": float(h.get("rel_n") or 0.0),
                        "chunks": [],
                    }
                )
            grp = index[key]
            try:
                grp_rel = float(h.get("rel_n") or 0.0)
            except (TypeError, ValueError):
                grp_rel = 0.0
            if grp_rel > groups[grp].get("rel", 0.0):
                groups[grp]["rel"] = grp_rel
            groups[grp]["chunks"].append(h)

        n_groups = len(groups)
        title_n = 90 if n_groups <= 60 else 72
        header = (
            f"下列证据已编号，共 {n_groups} 篇，已按相关度、新旧和影响因子对数值合并排序"
            "（编号越小综合分越高）；`rel=` 是检索相关度（0~1，越高越相关），"
            "**仅供你判断优先级，不要写进答案正文**。"
            "正文只引用和问题直接相关的编号，机制/功能题 4～8 个，事实题 2～4 个。"
            "禁止把本证据池整表贴进文末；文末参考文献由系统生成，不写 ZFQA。"
            "事实题文末至少 6 条。离题证据不要写进正文。\n\n"
        )
        excerpt_ns = [self._excerpt_budget(i) for i in range(1, n_groups + 1)]
        blocks = [
            self._paper_block(i, g, excerpt_ns[i - 1], title_n)
            for i, g in enumerate(groups, 1)
        ]
        budget = max(4000, max_chars - len(header))

        def packed_len(items: list[str]) -> int:
            if not items:
                return 0
            return sum(len(b) for b in items) + 2 * (len(items) - 1)

        # 超预算时从尾部压缩摘录，不删篇（题名+编号仍留给大模型引用）
        idx = n_groups - 1
        while packed_len(blocks) > budget and idx >= 0:
            if excerpt_ns[idx] > 0:
                excerpt_ns[idx] = 0 if excerpt_ns[idx] <= 100 else max(0, excerpt_ns[idx] // 2)
                blocks[idx] = self._paper_block(idx + 1, groups[idx], excerpt_ns[idx], title_n)
            else:
                idx -= 1

        while packed_len(blocks) > budget and len(blocks) > 40:
            blocks.pop()

        parts = blocks
        order: list[dict] = []
        for i, g in enumerate(groups[: len(parts)], 1):
            title = g["title"] or self._preview_text(g["chunks"][0].get("text") or "", title_n)
            year = g["year"] or "年不详"
            order.append(
                {
                    "order": i,
                    "rank": i,
                    "tag": g["tag"],
                    "pmid": g["pmid"],
                    "entry_no": g["entry_no"],
                    "zfqa_id": g.get("zfqa_id") or "",
                    "year": year,
                    "recency": self._recency_label(year),
                    "source_tier": g["tier"],
                    "excerpt_chars": excerpt_ns[i - 1] if i - 1 < len(excerpt_ns) else 0,
                    "block_chars": len(parts[i - 1]),
                    "preview": self._preview_text(title, 120),
                }
            )
        context = "\n\n".join(parts)
        if parts:
            context = header + context
        return {
            "max_chars": max_chars,
            "chunks_included": len(order),
            "papers_included": len(order),
            "total_chars": len(context),
            "context_chars": len(context),
            "order": order,
            "context": context,
            "context_preview_head": self._preview_text(context, 400),
        }

    def debug_search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        max_chars: int | None = None,
        expand: bool | None = None,
        candidate_preview: int = 30,
        candidates: int | None = None,
    ) -> dict:
        """实时检索调试：候选数 → 重排 topN → AI 打包明细（与 Hub 文献 Agent 同路径）。"""
        final_k = top_k or (PUBMED_YES_RERANK_TOP_N if PUBMED_YES_RERANK else RAG_TOP_K)
        default_cand = PUBMED_YES_RETRIEVE_CANDIDATES if PUBMED_YES_RERANK else final_k
        cand_k = max(final_k, int(candidates) if candidates is not None else default_cand)
        t0 = time.time()

        capture: dict = {"candidates": [], "method": None}
        if not self._shards:
            return {
                "ok": False,
                "error": "FAISS 未加载（shards 为空）",
                "query": query,
                "ready": False,
            }

        import rag.pubmed_yes_retriever as self_mod

        _orig = self_mod.rerank_hits

        def _wrap(q, hits, *, top_n=10, _orig=_orig, _cap=capture):
            _cap["candidates"] = list(hits)
            out = _orig(q, hits, top_n=top_n)
            if out:
                _cap["method"] = out[0].get("rerank_method")
            elif hits:
                _cap["method"] = "none"
            return out

        self_mod.rerank_hits = _wrap
        try:
            hits = self.search(
                query,
                top_k=final_k,
                expand=expand,
                candidates=cand_k,
            )
        finally:
            self_mod.rerank_hits = _orig

        cand_hits = capture["candidates"] or []
        if not cand_hits and not PUBMED_YES_RERANK:
            cand_hits = list(hits)

        rerank_method = capture["method"] or (
            (hits[0].get("rerank_method") if hits else None) or ("off" if not PUBMED_YES_RERANK else "unknown")
        )

        def _row(rank: int, h: dict) -> dict:
            return {
                "rank": rank,
                "pmid": h.get("pmid") or "",
                "entry_no": h.get("entry_no") or "",
                "doc_id": h.get("doc_id") or "",
                "field": h.get("field"),
                "field_weight": h.get("field_weight"),
                "raw_L2": h.get("raw_score"),
                "adj_L2": h.get("score"),
                "rerank_score": h.get("rerank_score"),
                # 最终位次不是按 rerank_score 排的：还要混年份/期刊（见 blend_weights）。
                # 不暴露这两个字段，用户会觉得"第 4 条分更高怎么排在后面"。
                "rel_n": h.get("rel_n"),
                "blend_score": h.get("blend_score"),
                "blend_weights": h.get("blend_weights"),
                "rerank_method": h.get("rerank_method") or rerank_method,
                "match": h.get("match"),
                "chunk_index": h.get("chunk_index"),
                "title_zh": h.get("title_zh") or "",
                "year": h.get("year") or self._hit_year(h) or "",
                "preview": self._preview_text(h.get("text") or "", 160),
            }

        ranked_rows = [_row(i, h) for i, h in enumerate(hits, 1)]
        cand_rows = [_row(i, h) for i, h in enumerate(cand_hits[: max(0, candidate_preview)], 1)]
        packed = self.pack_for_ai(hits, max_chars=max_chars)

        return {
            "ok": True,
            "query": query,
            "expanded_queries": expand_queries(query, enabled=expand),
            "elapsed_sec": round(time.time() - t0, 3),
            "ready": True,
            "shards_loaded": len(self._shards),
            "shards_total": self._shards_total,
            "shards_skipped_low_mem": self._shards_skipped_low_mem,
            # partial=true 表示只加载了部分分片（内存预算限制），检索结果不含未加载分片
            "partial": bool(self._shards_total and len(self._shards) < self._shards_total),
            "config": {
                "retrieve_candidates": cand_k,
                "rerank_top_n": PUBMED_YES_RERANK_TOP_N,
                "rerank_enabled": bool(PUBMED_YES_RERANK),
                "final_k": final_k,
                "cand_k": cand_k,
                "max_chars_budget": max_chars,
            },
            "candidates_before_rerank": len(cand_hits) if cand_hits else None,
            "after_rerank_top_n": len(hits),
            "rerank_method": rerank_method,
            "ranked": ranked_rows,
            "candidates_preview": cand_rows,
            "hub_pack": packed,
            "ai_submit": {
                "path": "PubmedYesRetriever.search → rerank_hits → get_context / pack_for_ai",
                "hub_agent": "agent/hub_orchestrator.py::_run_literature",
                "chunk_order": "重排后的 rank 顺序依次拼接，直到 max_chars",
                "separator": "\\n\\n",
                "max_chars": max_chars,
                "total_context_chars": packed["total_chars"],
                "chunks_submitted": packed["chunks_included"],
            },
        }

    def get_context(
        self,
        question: str,
        top_k: int = None,
        max_chars: int | None = None,
        hits: list[dict] | None = None,
    ) -> str:
        if hits is None:
            hits = self.search(question, top_k=top_k or PUBMED_YES_RERANK_TOP_N)
        packed = self.pack_for_ai(hits, max_chars=max_chars)
        return packed["context"]


_singleton_lock = threading.Lock()
_default_retriever: "PubmedYesRetriever | None" = None
# 加载中标志：避免聊天与预热并发双开 35 shard（第二路会等锁，不会双倍内存）
_loading = False


def get_pubmed_yes_retriever() -> "PubmedYesRetriever":
    """进程内单例：35 个 FAISS shard + embedding 只加载一次。"""
    global _default_retriever, _loading
    if _default_retriever is not None:
        return _default_retriever
    with _singleton_lock:
        if _default_retriever is None:
            _loading = True
            try:
                _default_retriever = PubmedYesRetriever()
            finally:
                _loading = False
        return _default_retriever


def peek_pubmed_yes_retriever() -> Optional["PubmedYesRetriever"]:
    """已就绪则返回单例；加载中或不存在则立即返回 None（不阻塞等 35 shard）。"""
    return _default_retriever


def pubmed_yes_loading() -> bool:
    return _loading and _default_retriever is None


# ---------------------------------------------------------------------------
# 2026-11-06 首问冷启动治理
#
# 线上实测：170 个分片首次冷载耗时 139.8s（内存不足时只装 62 片也一样慢），
# 而旧逻辑把它放在 /api/chat 请求线程里同步执行 → 用户看到的就是 90s 超时。
# 更讽刺的是，基因类问题随后走 gene_pubmed 候选、根本不做全库召回，这 140 秒
# 白花了。现在改为：请求线程绝不加载，只触发后台单飞预热；本轮立刻用
# gene_pubmed / Hub 小库出答。预热只在真实内存余量够时才启动，避免把 36GB
# cgroup 顶穿导致整站 OOM。
# ---------------------------------------------------------------------------
# 默认跟随 HUB_PUBMED_WARMUP：运维把预热关掉（共享服务器省内存档）时，
# 后台也不该偷偷拉起 20GB 的分片库，否则 36GB cgroup 一样会被顶穿。
_ASYNC_WARM_ENABLED = os.getenv(
    "PUBMED_YES_ASYNC_WARMUP",
    os.getenv("HUB_PUBMED_WARMUP", "0"),
).strip().lower() in ("1", "true", "yes")
_ASYNC_WARM_MIN_MB = float(os.getenv("PUBMED_YES_ASYNC_MIN_MB", "30000"))
_ASYNC_WARM_RETRY_SEC = float(os.getenv("PUBMED_YES_ASYNC_RETRY_SEC", "1800"))
_async_warm_lock = threading.Lock()
_async_warm_started = False
_async_warm_done = False
_async_warm_error = ""
_async_warm_skipped = ""
_async_warm_last_ts = 0.0


def ensure_pubmed_yes_retriever_async() -> bool:
    """后台单飞预热 PubmedYes；绝不阻塞当前请求。

    返回 True：已就绪 / 已启动加载 / 正在加载。
    返回 False：内存闸门判定装不下，本轮（及后续）主动放弃重索引，
                对话稳定走 gene_pubmed 快答路径。
    """
    global _async_warm_started, _async_warm_done, _async_warm_error
    global _async_warm_skipped, _async_warm_last_ts
    if _default_retriever is not None:
        return True
    if not _ASYNC_WARM_ENABLED:
        if _async_warm_skipped != "disabled":
            print(
                "[文献向量库] 后台预热按配置关闭（HUB_PUBMED_WARMUP=0）；"
                "文献问答走 gene_pubmed 快答，不加载 170 分片",
                flush=True,
            )
        _async_warm_skipped = "disabled"
        return False
    with _async_warm_lock:
        if _default_retriever is not None or _async_warm_started:
            return True
        now = time.time()
        if _async_warm_error and now - _async_warm_last_ts < _ASYNC_WARM_RETRY_SEC:
            return True  # 失败冷却期内不反复拉起重载
        try:
            headroom = _mem_headroom_mb()
        except Exception:
            headroom = float("inf")
        if headroom < _ASYNC_WARM_MIN_MB:
            if _async_warm_skipped != "low_mem":
                print(
                    f"[文献向量库] 余量 {headroom:.0f}MB < {_ASYNC_WARM_MIN_MB:.0f}MB，"
                    "跳过后台重索引预热；文献问答走 gene_pubmed 快答（不会 OOM）",
                    flush=True,
                )
            _async_warm_skipped = "low_mem"
            return False
        _async_warm_started = True
        _async_warm_error = ""
        _async_warm_last_ts = now

    def _worker() -> None:
        global _async_warm_started, _async_warm_done, _async_warm_error, _async_warm_last_ts
        try:
            get_pubmed_yes_retriever()
            _async_warm_done = True
            print("[文献向量库] 后台预热完成，后续文献问答启用完整语义召回", flush=True)
        except Exception as e:  # noqa: BLE001
            _async_warm_error = str(e)
            print(f"[文献向量库] 后台预热失败（不影响对话）：{e}", flush=True)
        finally:
            _async_warm_last_ts = time.time()
            with _async_warm_lock:
                _async_warm_started = False

    threading.Thread(target=_worker, name="pubmed-async-warm", daemon=True).start()
    return True


def pubmed_yes_async_status() -> dict:
    """给 /api/health 用的自证字段：预热是否启动、是否完成、放弃原因。"""
    return {
        "ready": _default_retriever is not None,
        "warming": _async_warm_started or pubmed_yes_loading(),
        "done": _async_warm_done,
        "skipped": _async_warm_skipped,
        "error": _async_warm_error,
        "min_headroom_mb": int(_ASYNC_WARM_MIN_MB),
    }
