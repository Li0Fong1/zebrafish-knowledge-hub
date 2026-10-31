# -*- coding: utf-8 -*-
"""
斑马鱼文献 RAG 批量扩充（PubMed 摘要 → rag/knowledge.d）

通过 NCBI E-utilities 抓取 PubMed 摘要，写入知识库并重建 FAISS 索引。
"""
import os
import sys

# Spawned workers may not have run bootstrap_app yet; ensure project root + core
# are on sys.path so `import config` / `import bootstrap` resolve.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORE = os.path.join(_ROOT, "core")
# Prefer core/ modules as top-level names, then project root (shim/package imports).
for _p in (_CORE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
# Keep core ahead of root even if root was already on path (cwd / -m).
if _CORE in sys.path:
    try:
        sys.path.remove(_CORE)
    except ValueError:
        pass
    sys.path.insert(0, _CORE)

import bootstrap  # noqa: F401

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import List, Set, Optional

from config import _BASE_DIR, KNOWLEDGE_EXTRA_DIR
from common_utils import logger, safe_print

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PROGRESS_FILE = os.path.join(_BASE_DIR, "data", "ensembl", "literature_progress.json")
_BASE_INTERVAL = float(os.getenv("NCBI_REQUEST_INTERVAL", "0.5"))
_MAX_RETRIES = int(os.getenv("NCBI_MAX_RETRIES", "6"))
_RETRY_BACKOFF = float(os.getenv("NCBI_RETRY_BACKOFF", "3.0"))
CONTACT_EMAIL = os.getenv("NCBI_EMAIL", "zebrafish-gene-qa@local.dev")


def _effective_interval() -> float:
    """无有效 Key 时至少 0.34s（3 req/s），有 Key 可用更短间隔。"""
    if getattr(_api_get, "_key_disabled", False) or not os.getenv("NCBI_API_KEY", "").strip():
        return max(_BASE_INTERVAL, 0.34)
    return _BASE_INTERVAL


def _sleep():
    time.sleep(_effective_interval())

# 分批检索主题（参照 Potato Hub 文献覆盖广度，可续传增量扩充）
PUBMED_QUERIES = [
    ("zebrafish_core", "zebrafish[Title/Abstract]"),
    ("zebrafish_development", "Danio rerio[Organism] AND (development OR embryogenesis)"),
    ("zebrafish_genetics", "Danio rerio[Organism] AND (gene OR CRISPR OR knockout OR morpholino)"),
    ("zebrafish_disease", "zebrafish[Title/Abstract] AND (disease OR cancer OR model OR drug)"),
    ("zebrafish_neuro", "Danio rerio[Organism] AND (neuron OR brain OR behavior)"),
    ("zebrafish_cardio", "Danio rerio[Organism] AND (heart OR cardiac OR cardiovascular)"),
    ("zebrafish_immunity", "Danio rerio[Organism] AND (immune OR infection OR inflammation)"),
    ("zebrafish_regeneration", "zebrafish[Title/Abstract] AND (regeneration OR repair)"),
    ("zebrafish_toxicology", "Danio rerio[Organism] AND (toxicity OR stress OR environmental)"),
    ("zebrafish_signaling", "Danio rerio[Organism] AND (signaling pathway OR Wnt OR Hedgehog OR BMP)"),
    ("zebrafish_genomics", "Danio rerio[Organism] AND (RNA-seq OR transcriptome OR genome)"),
    ("zebrafish_crispr", "Danio rerio[Organism] AND (CRISPR OR Cas9 OR genome editing)"),
    ("zebrafish_metabolism", "Danio rerio[Organism] AND (metabolism OR obesity OR diabetes OR lipid)"),
    ("zebrafish_epigenetics", "Danio rerio[Organism] AND (epigenetic OR methylation OR chromatin)"),
    ("zebrafish_organogenesis", "Danio rerio[Organism] AND (organogenesis OR morphogenesis)"),
    ("zebrafish_cancer", "Danio rerio[Organism] AND (tumor OR oncology OR carcinogenesis)"),
    ("zebrafish_drug_screen", "zebrafish[Title/Abstract] AND (drug screening OR chemical screening)"),
    ("zebrafish_axial", "Danio rerio[Organism] AND (somite OR segmentation OR hox)"),
    ("zebrafish_vision", "Danio rerio[Organism] AND (retina OR vision OR eye development)"),
]

CLASSIC_GENE_QUERIES = [
    "tp53 zebrafish", "hsp70 zebrafish", "sox9b zebrafish", "nanog zebrafish",
    "myl7 zebrafish", "shha zebrafish", "bmp4 zebrafish", "vegfa zebrafish",
    "pax6a zebrafish", "mitfa zebrafish", "brca2 zebrafish", "rag1 zebrafish",
    "fgf8a zebrafish", "wnt11 zebrafish", "runx1 zebrafish", "gata1 zebrafish",
    "ntla zebrafish", "myod1 zebrafish", "dmd zebrafish", "mtor zebrafish",
    "mapk1 zebrafish", "notch1 zebrafish", "cxcr4 zebrafish", "cxcl12 zebrafish",
]


def _api_get(path: str, params: dict) -> str:
    params = dict(params)
    params.setdefault("email", CONTACT_EMAIL)
    params.setdefault("tool", "ZebrafishGeneQA")
    api_key = os.getenv("NCBI_API_KEY", "").strip()
    use_key = bool(api_key) and not getattr(_api_get, "_key_disabled", False)
    if use_key:
        params["api_key"] = api_key
    url = NCBI_BASE + path + "?" + urllib.parse.urlencode(params)
    last_err: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        req = urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if e.code == 400 and use_key and ("api-key" in body.lower() or "api key" in body.lower()):
                _api_get._key_disabled = True  # type: ignore[attr-defined]
                logger.warning(
                    "[文献] NCBI API Key 无效，已自动切换为无 Key 模式（限速 3 req/s）。"
                    "请到 https://www.ncbi.nlm.nih.gov/account/settings/ 申请正确 Key"
                )
                safe_print(
                    "  [警告] NCBI API Key 无效，已改用无 Key 模式（较慢）。"
                    "可在 .env 更新 NCBI_API_KEY 后重试"
                )
                retry_params = dict(params)
                retry_params.pop("api_key", None)
                url2 = NCBI_BASE + path + "?" + urllib.parse.urlencode(retry_params)
                with urllib.request.urlopen(
                    urllib.request.Request(url2, headers={"User-Agent": "ZebrafishGeneQA/1.0"}),
                    timeout=90,
                ) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            if e.code in (429, 500, 502, 503, 504):
                last_err = e
                wait = _RETRY_BACKOFF * (2 ** attempt)
                safe_print(
                    f"  [网络] NCBI HTTP {e.code}，{wait:.0f}s 后重试 ({attempt + 1}/{_MAX_RETRIES})…"
                )
                time.sleep(wait)
                continue
            raise urllib.error.HTTPError(e.url, e.code, e.reason, e.headers, body.encode())
        except (urllib.error.URLError, ConnectionResetError, TimeoutError, OSError) as e:
            last_err = e
            wait = _RETRY_BACKOFF * (2 ** attempt)
            safe_print(
                f"  [网络] 连接中断 ({type(e).__name__})，{wait:.0f}s 后重试 ({attempt + 1}/{_MAX_RETRIES})…"
            )
            time.sleep(wait)

    raise last_err if last_err else RuntimeError("NCBI 请求失败")


def _load_done() -> Set[str]:
    from literature_models import LiteratureProgress
    return LiteratureProgress().pmids


def _save_done(pmids: Set[str]):
    from literature_models import LiteratureProgress
    p = LiteratureProgress()
    p.pmids = set(pmids)
    p.save()


def search_pubmed(query: str, retmax: int = 200) -> List[str]:
    """PubMed 检索，支持 retstart 分页直到达到 retmax 或无更多结果。"""
    all_ids: List[str] = []
    retstart = 0
    page_size = min(500, max(retmax, 1))
    while len(all_ids) < retmax:
        fetch_n = min(page_size, retmax - len(all_ids))
        ids = search_pubmed_page(query, retstart=retstart, retmax=fetch_n)
        if not ids:
            break
        all_ids.extend(ids)
        retstart += len(ids)
        if len(ids) < fetch_n:
            break
    return all_ids[:retmax]


def _clean_json_text(text: str) -> str:
    """去除 NCBI 响应中偶发的非法控制字符（含未转义换行）。"""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return text.replace("\r", " ").replace("\n", " ")


def _loads_ncbi_json(text: str) -> dict:
    last_err: Optional[Exception] = None
    for cleaned in (text, _clean_json_text(text), re.sub(r"[\x00-\x1f]", " ", text)):
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            last_err = e
    raise last_err if last_err else json.JSONDecodeError("NCBI JSON 解析失败", text, 0)


def search_pubmed_page(query: str, retstart: int = 0, retmax: int = 200) -> List[str]:
    """单页 PubMed 检索（含网络/JSON 异常重试）。"""
    last_err: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES):
        try:
            data = _api_get("/esearch.fcgi", {
                "db": "pubmed",
                "term": query,
                "retmax": retmax,
                "retstart": retstart,
                "retmode": "json",
                "sort": "relevance",
            })
            _sleep()
            parsed = _loads_ncbi_json(data)
            return parsed.get("esearchresult", {}).get("idlist", [])
        except (json.JSONDecodeError, urllib.error.URLError, ConnectionResetError, OSError) as e:
            last_err = e
            wait = _RETRY_BACKOFF * (2 ** attempt)
            safe_print(
                f"  [网络] PubMed 分页失败 ({type(e).__name__})，{wait:.0f}s 后重试 "
                f"({attempt + 1}/{_MAX_RETRIES})…"
            )
            time.sleep(wait)
    raise last_err if last_err else RuntimeError("PubMed 分页检索失败")


def pubmed_total_count(query: str) -> int:
    data = _api_get("/esearch.fcgi", {
        "db": "pubmed",
        "term": query,
        "retmax": 0,
        "retmode": "json",
    })
    _sleep()
    parsed = _loads_ncbi_json(data)
    return int(parsed.get("esearchresult", {}).get("count", 0) or 0)


# 凡提到 zebrafish 或 Danio rerio 均采集
PUBMED_ZEBRAFISH_BROAD = (
    "zebrafish[Title/Abstract] OR "
    "Danio rerio[Title/Abstract] OR "
    "zebrafish[MeSH Terms] OR "
    "Danio rerio[Organism]"
)


def _gene_queries_from_file(limit: int = 50) -> List[str]:
    """从 data/gene_list.txt 生成基因专题检索。"""
    path = os.path.join(_BASE_DIR, "data", "gene_list.txt")
    if not os.path.isfile(path):
        return []
    queries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            g = line.strip().split("#")[0].strip()
            if g and re.match(r"^[a-zA-Z]", g):
                queries.append(f"{g} zebrafish")
            if len(queries) >= limit:
                break
    return queries


def fetch_abstracts(pmids: List[str]) -> List[dict]:
    if not pmids:
        return []
    articles = []
    chunk = 40
    for i in range(0, len(pmids), chunk):
        batch = pmids[i:i + chunk]
        last_err: Optional[Exception] = None
        xml_text = ""
        for attempt in range(_MAX_RETRIES):
            try:
                xml_text = _api_get("/efetch.fcgi", {
                    "db": "pubmed",
                    "id": ",".join(batch),
                    "retmode": "xml",
                })
                break
            except (urllib.error.URLError, ConnectionResetError, OSError) as e:
                last_err = e
                wait = _RETRY_BACKOFF * (2 ** attempt)
                safe_print(
                    f"  [网络] 摘要下载失败，{wait:.0f}s 后重试 ({attempt + 1}/{_MAX_RETRIES})…"
                )
                time.sleep(wait)
        else:
            raise last_err if last_err else RuntimeError("PubMed 摘要下载失败")
        _sleep()
        root = ET.fromstring(xml_text)
        for article in root.findall(".//PubmedArticle"):
            pmid = (article.findtext(".//PMID") or "").strip()
            title = (article.findtext(".//ArticleTitle") or "").strip()
            abstract_parts = [
                (ab.findtext("Label") or "") + ": " + (ab.findtext("AbstractText") or "")
                if ab.get("Label") else (ab.text or "")
                for ab in article.findall(".//Abstract/AbstractText")
            ]
            abstract = " ".join(p.strip() for p in abstract_parts if p.strip())
            journal = (article.findtext(".//Journal/Title") or "").strip()
            year = (article.findtext(".//PubDate/Year") or article.findtext(".//PubDate/MedlineDate") or "")[:4]
            if not pmid or not title:
                continue
            doi = ""
            for aid in article.findall(".//ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = (aid.text or "").strip()
            articles.append({
                "source": "pubmed",
                "id_label": f"PMID:{pmid}",
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "journal": journal,
                "year": year,
                "doi": doi,
            })
    return articles


def _format_article(art: dict) -> str:
    return (
        f"【PubMed {art['pmid']}】{art['title']}\n"
        f"期刊：{art.get('journal', '-')} ({art.get('year', '-')})\n"
        f"摘要：{art['abstract']}\n"
    )


def ingest_articles(articles: List[dict], batch_name: str) -> int:
    from knowledge_ingest import ingest_text
    if not articles:
        return 0
    content = "\n".join(_format_article(a) for a in articles)
    fname = f"literature_{batch_name}.txt"
    ingest_text(content, fname)
    return len(articles)


class LiteratureCollector:
    def __init__(self, progress=None):
        if progress is not None:
            self.done = progress.pmids
            self._progress = progress
        else:
            self._progress = None
            self.done = _load_done()
        self.stats = {"searched": 0, "new": 0, "skipped": 0, "files": 0}

    def collect(
        self,
        per_query: int = 150,
        include_genes: bool = True,
        rebuild_index: bool = True,
        resume: bool = True,
        gene_per_query: int = 40,
    ) -> dict:
        if not resume:
            self.done = set()

        safe_print(
            f"\n[文献采集] 已有 {len(self.done)} 篇 | 主题 {len(PUBMED_QUERIES)} 个 | "
            f"每主题最多 {per_query} 篇（分页检索）"
        )
        all_pmids: List[str] = []

        for name, query in PUBMED_QUERIES:
            pmids = search_pubmed(query, retmax=per_query)
            self.stats["searched"] += len(pmids)
            new = [p for p in pmids if p not in self.done]
            safe_print(f"  {name}: 检索 {len(pmids)} | 新增 {len(new)}")
            all_pmids.extend(new)

        if include_genes:
            gene_queries = list(CLASSIC_GENE_QUERIES) + _gene_queries_from_file()
            seen_gq = set()
            for gq in gene_queries:
                if gq in seen_gq:
                    continue
                seen_gq.add(gq)
                pmids = search_pubmed(gq, retmax=gene_per_query)
                new = [p for p in pmids if p not in self.done]
                all_pmids.extend(new)
            safe_print(f"  基因专题: {len(seen_gq)} 个查询")

        # 去重保序
        seen = set()
        pending = []
        for p in all_pmids:
            if p not in seen and p not in self.done:
                seen.add(p)
                pending.append(p)

        safe_print(f"\n[文献采集] 待下载摘要 {len(pending)} 篇")
        chunk = 100
        for i in range(0, len(pending), chunk):
            batch_pmids = pending[i:i + chunk]
            articles = fetch_abstracts(batch_pmids)
            if not articles:
                continue
            batch_name = f"pubmed_{batch_pmids[0]}_{len(articles)}"
            n = ingest_articles(articles, batch_name)
            for a in articles:
                self.done.add(a["pmid"])
            self.stats["new"] += n
            self.stats["files"] += 1
            self._save_progress()
            safe_print(f"  已入库 {i + len(batch_pmids)}/{len(pending)} 篇（累计 {len(self.done)}）")

        if rebuild_index and self.stats["new"] > 0:
            safe_print("\n[文献采集] 重建 RAG 向量索引 …")
            from knowledge_ingest import rebuild_rag_index
            rebuild_rag_index()
            safe_print("  RAG 索引已更新")

        safe_print(
            f"\n[完成] 新增文献 {self.stats['new']} 篇 | "
            f"知识库文件 +{self.stats['files']} | 累计 {len(self.done)} 篇"
        )
        return self.stats

    def _save_progress(self):
        if self._progress is not None:
            self._progress.pmids = self.done
            self._progress.save()
        else:
            _save_done(self.done)


def run_literature_collection(
    per_query: int = 150,
    include_genes: bool = True,
    rebuild_index: bool = True,
    resume: bool = True,
    gene_per_query: int = 40,
):
    safe_print("=" * 60)
    safe_print("  斑马鱼文献 RAG 批量扩充（PubMed 摘要）")
    safe_print("=" * 60)
    LiteratureCollector().collect(
        per_query=per_query,
        include_genes=include_genes,
        rebuild_index=rebuild_index,
        resume=resume,
        gene_per_query=gene_per_query,
    )
