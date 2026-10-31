# -*- coding: utf-8 -*-
"""文献采集统一数据结构与进度管理。"""
import json
import os
import re
import shutil
from typing import Dict, List, Optional, Set

from config import _BASE_DIR
from common_utils import logger

PROGRESS_FILE = os.path.join(_BASE_DIR, "data", "ensembl", "literature_progress.json")
PROGRESS_BAK = PROGRESS_FILE + ".bak"
CURSOR_FILE = os.path.join(_BASE_DIR, "data", "ensembl", "literature_epmc_cursor.txt")
CATALOG_FILE = os.path.join(_BASE_DIR, "data", "literature", "literature_catalog.jsonl")


class ProgressLoadError(RuntimeError):
    """进度文件无法恢复，不应自动重试。"""


def _norm_doi(doi: str) -> str:
    d = (doi or "").strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d


class LiteratureProgress:
    def __init__(self):
        self.pmids: Set[str] = set()
        self.wos_ut: Set[str] = set()
        self.dois: Set[str] = set()
        self.epmc_ids: Set[str] = set()
        self.pubmed_broad_retstart: int = 0
        self.epmc_cursor: str = "*"
        self.epmc_hits_scanned: int = 0
        self._load()

    @staticmethod
    def _read_json(path: str) -> Optional[dict]:
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            return None
        with open(path, "rb") as f:
            head = f.read(64)
        if not head.strip(b"\x00 \t\r\n"):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _load_cursor_file(self):
        if not os.path.isfile(CURSOR_FILE):
            return
        try:
            with open(CURSOR_FILE, encoding="utf-8") as f:
                cursor = f.read().strip()
            if cursor:
                self.epmc_cursor = cursor
        except OSError as e:
            logger.warning("读取 EPMC cursor 失败: %s", e)

    def _apply_data(self, data: dict):
        self.pmids = set(data.get("pmids") or [])
        self.wos_ut = set(data.get("wos_ut") or [])
        self.dois = set(data.get("dois") or [])
        self.epmc_ids = set(data.get("epmc_ids") or [])
        self.pubmed_broad_retstart = int(data.get("pubmed_broad_retstart") or 0)
        self.epmc_cursor = str(data.get("epmc_cursor") or "*")
        self.epmc_hits_scanned = int(data.get("epmc_hits_scanned") or 0)
        self._load_cursor_file()

    def _rebuild_from_catalog(self) -> bool:
        if not os.path.isfile(CATALOG_FILE):
            return False
        pmids: Set[str] = set()
        wos_ut: Set[str] = set()
        dois: Set[str] = set()
        epmc_ids: Set[str] = set()
        with open(CATALOG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    art = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if art.get("pmid"):
                    pmids.add(str(art["pmid"]))
                if art.get("wos_ut"):
                    wos_ut.add(str(art["wos_ut"]))
                doi = _norm_doi(art.get("doi") or "")
                if doi:
                    dois.add(doi)
                epmc = (art.get("epmc_id") or "").strip()
                if epmc:
                    epmc_ids.add(epmc)
        self.pmids = pmids
        self.wos_ut = wos_ut
        self.dois = dois
        self.epmc_ids = epmc_ids
        self._load_cursor_file()
        return True

    def _archive_corrupt(self, path: str):
        if not os.path.isfile(path):
            return
        base, ext = os.path.splitext(path)
        archived = f"{base}.corrupt{ext}"
        n = 0
        while os.path.isfile(archived):
            n += 1
            archived = f"{base}.corrupt{n}{ext}"
        try:
            shutil.move(path, archived)
            logger.warning("已归档损坏进度文件: %s", archived)
        except OSError as e:
            logger.warning("归档损坏进度文件失败 (%s): %s", path, e)

    def _load(self):
        for path in (PROGRESS_FILE, PROGRESS_BAK):
            try:
                data = self._read_json(path)
            except json.JSONDecodeError:
                data = None
            if data is not None:
                self._apply_data(data)
                if path == PROGRESS_BAK:
                    logger.warning("主进度文件损坏，已从 .bak 恢复")
                    self.save()
                return

        if self._rebuild_from_catalog():
            logger.warning(
                "进度 JSON 损坏或为空，已从 catalog 重建: pmids=%d dois=%d",
                len(self.pmids),
                len(self.dois),
            )
            if os.path.isfile(PROGRESS_FILE):
                self._archive_corrupt(PROGRESS_FILE)
            self.save()
            return

        if os.path.isfile(PROGRESS_FILE):
            raise ProgressLoadError(
                f"无法读取 {PROGRESS_FILE}，且 catalog 不存在，无法重建进度"
            )

    def _atomic_write(self, path: str, content: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def save(self):
        os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
        payload = json.dumps(
            {
                "pmids": sorted(self.pmids),
                "wos_ut": sorted(self.wos_ut),
                "dois": sorted(self.dois),
                "epmc_ids": sorted(self.epmc_ids),
                "pubmed_broad_retstart": self.pubmed_broad_retstart,
                "epmc_cursor": self.epmc_cursor,
                "epmc_hits_scanned": self.epmc_hits_scanned,
            },
            ensure_ascii=False,
        )
        if os.path.isfile(PROGRESS_FILE) and os.path.getsize(PROGRESS_FILE) > 0:
            try:
                if self._read_json(PROGRESS_FILE) is not None:
                    shutil.copy2(PROGRESS_FILE, PROGRESS_BAK)
            except (json.JSONDecodeError, OSError):
                pass
        self._atomic_write(PROGRESS_FILE, payload)
        self._atomic_write(CURSOR_FILE, self.epmc_cursor)

    def is_seen(self, art: dict) -> bool:
        pmid = (art.get("pmid") or "").strip()
        wos = (art.get("wos_ut") or "").strip()
        doi = _norm_doi(art.get("doi") or "")
        epmc = (art.get("epmc_id") or "").strip()
        if pmid and pmid in self.pmids:
            return True
        if wos and wos in self.wos_ut:
            return True
        if doi and doi in self.dois:
            return True
        if epmc and epmc in self.epmc_ids:
            return True
        return False

    def mark(self, art: dict):
        if art.get("pmid"):
            self.pmids.add(str(art["pmid"]))
        if art.get("wos_ut"):
            self.wos_ut.add(str(art["wos_ut"]))
        doi = _norm_doi(art.get("doi") or "")
        if doi:
            self.dois.add(doi)
        if art.get("epmc_id"):
            self.epmc_ids.add(str(art["epmc_id"]))


def format_article(art: dict) -> str:
    src = art.get("source") or "unknown"
    lines = [f"【{src.upper()} {art.get('id_label') or art.get('pmid') or art.get('wos_ut') or art.get('doi') or '?'}】{art.get('title', '')}"]
    meta = []
    if art.get("journal"):
        meta.append(art["journal"])
    if art.get("year"):
        meta.append(str(art["year"]))
    if art.get("doi"):
        meta.append(f"DOI:{art['doi']}")
    if art.get("pmid"):
        meta.append(f"PMID:{art['pmid']}")
    if art.get("wos_ut"):
        meta.append(f"WOS:{art['wos_ut']}")
    if art.get("tier") == "core_genetics":
        meta.append("TIER:core_genetics")
    if meta:
        lines.append("来源信息：" + " | ".join(meta))
    if art.get("authors"):
        lines.append(f"作者：{art['authors']}")
    abstract = (art.get("abstract") or "").strip()
    if abstract:
        lines.append(f"摘要：{abstract}")
    else:
        lines.append("摘要：（无摘要，仅元数据）")
    return "\n".join(lines) + "\n"


def ingest_articles(articles: List[dict], batch_name: str, paths_out: List[str] | None = None) -> int:
    from knowledge_ingest import ingest_text
    from literature_tiers import classify_tier

    valid = [a for a in articles if (a.get("title") or "").strip()]
    if not valid:
        return 0

    broad, core = [], []
    for a in valid:
        tier = a.get("tier") or classify_tier(a)
        a["tier"] = tier
        (core if tier == "core_genetics" else broad).append(a)

    n = 0
    if broad:
        content = "\n".join(format_article(a) for a in broad)
        p = ingest_text(content, f"literature_{batch_name}.txt")
        if paths_out is not None and p:
            paths_out.append(p)
        append_catalog(broad)
        n += len(broad)
    if core:
        content = "\n".join(format_article(a) for a in core)
        p = ingest_text(content, f"literature_core_{batch_name}.txt")
        if paths_out is not None and p:
            paths_out.append(p)
        append_catalog(core)
        n += len(core)
    return n


def append_catalog(articles: List[dict]):
    os.makedirs(os.path.dirname(CATALOG_FILE), exist_ok=True)
    with open(CATALOG_FILE, "a", encoding="utf-8") as f:
        for art in articles:
            f.write(json.dumps(art, ensure_ascii=False) + "\n")
