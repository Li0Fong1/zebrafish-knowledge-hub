# -*- coding: utf-8 -*-
"""生成首页「每日研究」用的轻量缓存（不逐请求解析 63MB 的 catalog）。

输入：data/literature/literature_catalog.jsonl（含 abstract / journal / year / doi / pmid）
输出：$RUNTIME/hub_recent_papers.json —— 约 200 条，只留必要字段，摘要截断到 600 字

用法（服务器上）：
    /data/zoujin/deng/miniconda3/envs/zebrafish/bin/python -X utf8 \
        scripts/build_hub_recent_papers.py [--top 200] [--max-abstract 600]

说明：本脚本只**读取**真实产物、不改动任何语料；缓存是只读派生物，可随时重建。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_UA = "ZebrafishHub/1.0 (link check; contact: hub admin)"
_PMID_IN_WOS_UT = re.compile(r"MEDLINE\s*:?\s*(\d{5,9})", re.I)


def _recover_pmid(d: dict) -> str:
    """先把语料里能推出来的 PMID 拿到手。

    实测：WoS 来源的记录 `pmid` 常常是空的，但 `wos_ut` 会写成
    `MEDLINE:8983196` / `WOS:MEDLINE:8983196`，里面就是 PMID。
    """
    pmid = str(d.get("pmid") or "").strip()
    if pmid.isdigit():
        return pmid
    m = _PMID_IN_WOS_UT.search(str(d.get("wos_ut") or ""))
    return m.group(1) if m else ""


def _norm_tokens(s: str) -> set[str]:
    s = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", (s or "").lower())
    return {t for t in s.split() if len(t) > 2}


def _title_match(a: str, b: str) -> bool:
    """题名是否同一篇（词集 Jaccard，容忍副标题/大小写/标点差异）。"""
    ta, tb = _norm_tokens(a), _norm_tokens(b)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    return (inter / max(1, min(len(ta), len(tb)))) >= 0.6


def _epmc_by_doi(doi: str, want_title: str, timeout: int = 12) -> dict:
    """Europe PMC 按 DOI 回查：拿到 PMID / PMCID / 开放获取标记，并校验 DOI 归属。

    为什么必须校验归属：库内有 847+ 个 DOI 属于「一 DOI 多论文」（DOI 是从全文
    参考文献表里抓错的）。若不校验就把 doi.org 链接挂上去，用户点开的是**另一篇**。
    """
    out = {"ok": False, "pmid": "", "pmcid": "", "is_oa": False, "title": "", "match": False}
    if not doi:
        return out
    try:
        import urllib.parse
        import urllib.request

        q = urllib.parse.quote(f'DOI:"{doi}"')
        url = f"{EPMC_SEARCH}?query={q}&format=json&resultType=core&pageSize=1"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        hits = ((data or {}).get("resultList") or {}).get("result") or []
        if not hits:
            return out
        h = hits[0]
        out.update({
            "ok": True,
            "pmid": str(h.get("pmid") or "").strip(),
            "pmcid": str(h.get("pmcid") or "").strip(),
            "is_oa": str(h.get("isOpenAccess") or "").upper() == "Y",
            "title": str(h.get("title") or "").strip(),
        })
        out["match"] = _title_match(want_title, out["title"])
    except Exception:  # noqa: BLE001
        return out
    return out


def _enrich_links(papers: list[dict], workers: int = 8, offline: bool = False) -> dict:
    """给每条算一个「点得开」的原文链接。

    优先级：PubMed（免费稳定）> 已验证归属的 DOI > Europe PMC 题名检索。
    返回计数统计，便于在缓存里留痕。
    """
    stats = {"pubmed": 0, "doi": 0, "search": 0, "free_pmc": 0, "doi_rejected": 0}

    def work(p: dict) -> None:
        doi = (p.get("doi") or "").strip()
        pmid = (p.get("pmid") or "").strip()
        info = {"pmid": "", "pmcid": "", "is_oa": False, "match": False}
        if doi and not offline:
            info = _epmc_by_doi(doi, p.get("title") or "")
            if info.get("pmid") and not pmid:
                pmid = info["pmid"]
            if info.get("ok") and not info.get("match"):
                # DOI 不属于这篇 → 不信这个 DOI
                p["doi_untrusted"] = True
                stats["doi_rejected"] += 1
        p["pmid"] = pmid
        if info.get("pmcid"):
            p["pmcid"] = info["pmcid"]
            p["free_url"] = f"https://europepmc.org/article/PMC/{info['pmcid']}"
            stats["free_pmc"] += 1
        if pmid:
            p["url"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            p["link_kind"] = "pubmed"
            stats["pubmed"] += 1
        elif doi and not p.get("doi_untrusted"):
            p["url"] = f"https://doi.org/{doi}"
            p["link_kind"] = "doi"
            stats["doi"] += 1
        else:
            import urllib.parse

            t = urllib.parse.quote((p.get("title") or "").split(".")[0][:160])
            p["url"] = f"https://europepmc.org/search?query=TITLE%3A%22{t}%22"
            p["link_kind"] = "search"
            stats["search"] += 1

    if offline:
        for p in papers:
            work(p)
    else:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            list(ex.map(work, papers))
    return stats


def _runtime_dirs() -> list[Path]:
    """运行时目录候选（按优先级，去重）。

    与 agent/hub_maintenance._runtime_root() 对齐：
      · ZEBRA_RUNTIME / ZEBRAFISH_RUNTIME 环境变量优先
      · 其次项目根目录（hub_maintenance 的默认落点）
      · 最后与项目同级的 zebrafish_runtime
    这样可以保证「谁读都能读到」，不依赖哪个进程导出过哪个变量。
    """
    out: list[Path] = []
    for key in ("ZEBRA_RUNTIME", "ZEBRAFISH_RUNTIME"):
        v = (os.getenv(key) or "").strip()
        if v:
            out.append(Path(v))
    out.append(ROOT)
    out.append(ROOT.parent / "zebrafish_runtime")
    uniq: list[Path] = []
    for p in out:
        if p not in uniq:
            uniq.append(p)
    return uniq


def _runtime_root() -> Path:
    return _runtime_dirs()[0]


def _catalog_candidates() -> list[Path]:
    cands = [ROOT / "data" / "literature" / "literature_catalog.jsonl"]
    cands += [d / "literature_catalog.jsonl" for d in _runtime_dirs()]
    return cands


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=200, help="保留条数（按年份倒序后再取尾部最新）")
    ap.add_argument("--max-abstract", type=int, default=600)
    ap.add_argument("--out", default="", help="输出路径（默认 $RUNTIME/hub_recent_papers.json）")
    ap.add_argument("--offline", action="store_true", help="不联网：只做本地 PMID 恢复与链接降级")
    ap.add_argument("--workers", type=int, default=8, help="Europe PMC 并发数")
    a = ap.parse_args(argv)

    src = next((p for p in _catalog_candidates() if p.is_file()), None)
    if src is None:
        print("[recent] 找不到 literature_catalog.jsonl，先确认语料路径")
        return 2

    kept: list[dict] = []
    seen: set[str] = set()
    total = 0
    with src.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            total += 1
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            title = (d.get("title") or "").strip()
            absr = (d.get("abstract") or "").strip()
            pmid = _recover_pmid(d)          # WoS 的 pmid 常为空，退到 wos_ut 里的 MEDLINE 号
            if not title or len(absr) < 200:      # 无摘要的不进「每日研究」
                continue
            key = pmid or (d.get("doi") or title)[:120]
            if key in seen:
                continue
            seen.add(key)
            kept.append({
                "pmid": pmid,
                "doi": (d.get("doi") or "").strip(),
                "title": title,
                "journal": (d.get("journal") or "").strip(),
                "year": str(d.get("year") or "").strip(),
                "source": (d.get("source") or "").strip(),
                "abstract": absr[: a.max_abstract],
            })

    kept.sort(key=lambda r: (r.get("year") or "", r.get("pmid") or ""))
    kept = kept[-a.top:]

    # —— 链接可用性（这一步就是为了「点了能看原文」）——
    t0 = time.time()
    link_stats = _enrich_links(kept, workers=max(1, a.workers), offline=bool(a.offline))
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": str(src),
        "scanned_lines": total,
        "kept": len(kept),
        "link_policy": (
            "PubMed（免费）> 经 Europe PMC 校验归属的 DOI > Europe PMC 题名检索；"
            "DOI 不属于本条的会被标记 doi_untrusted 且不用于外链"
        ),
        "link_stats": link_stats,
        "link_check_sec": round(time.time() - t0, 1),
        "papers": kept,
    }
    blob = json.dumps(payload, ensure_ascii=False, indent=1)
    targets = [Path(a.out)] if a.out else [d / "hub_recent_papers.json" for d in _runtime_dirs()]
    written: list[Path] = []
    for t in targets:
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text(blob, encoding="utf-8")
        written.append(t)
    for t in written:
        print(f"[recent] 扫描 {total} 行 → 保留 {len(kept)} 条 → {t}  ({t.stat().st_size/1024:.0f}KB)")
    print(f"[recent] 链接：PubMed {link_stats['pubmed']} · DOI(已验证) {link_stats['doi']} · "
          f"题名检索 {link_stats['search']} · 免费全文(PMC) {link_stats['free_pmc']} · "
          f"剔除不可信 DOI {link_stats['doi_rejected']}（{payload['link_check_sec']}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
