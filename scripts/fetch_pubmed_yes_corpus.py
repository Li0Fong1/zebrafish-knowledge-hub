# -*- coding: utf-8 -*-
"""
拉取判别为「是」的 PubMed 文献语料：本地 XML + Europe PMC 开放全文。

不调用 DeepSeek / 任何 LLM，仅 NCBI/Europe PMC 公开 API + 本地解析。

用法：
  python -m scripts.fetch_pubmed_yes_corpus --resume
  python -m scripts.fetch_pubmed_yes_corpus --resume --limit 100
  python -m scripts.fetch_pubmed_yes_corpus --skip-fulltext   # 仅摘要，调试
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from sqlalchemy import text

from common_utils import safe_print
from rag.pubmed_yes_corpus_io import load_done_pmids, output_path
from rag.pubmed_yes_source import PUBMED_YES_CSV, count_pubmed_yes_pmids, load_pubmed_yes_records
from mysql_client import MySQLClient
from rag.europepmc_collector import REQUEST_INTERVAL as EPMC_INTERVAL
from rag.literature_collector import _api_get, _sleep
from rag.network_resilience import (
    PROBE_INTERVAL,
    WAIT_LOG_INTERVAL,
    call_with_network_recovery,
    probe_network,
    wait_for_network,
)
from rag.pubmed_corpus_utils import epmc_is_open_access, fetch_pmc_fulltext, parse_pubmed_xml

from config import PUBMED_YES_CORPUS_JSONL as _CORPUS_PATH

CORPUS_PATH = Path(_CORPUS_PATH)

PROGRESS = ROOT / "data" / "literature" / "pubmed_yes_fetch.json"
MIGRATION = ROOT / "sql" / "literature_vector_chunk_migration.sql"


def _load_yes_rows(client: MySQLClient) -> list[tuple]:
    """从 PubMed 判别「是」清单加载，并补 MySQL raw_xml。"""
    by_pmid, order = load_pubmed_yes_records()
    if not order:
        raise FileNotFoundError(f"PubMed YES 清单为空，请检查 {PUBMED_YES_CSV}")

    raw_map: dict[str, str] = {}
    db = next(client.get_db())
    try:
        chunk = 200
        for i in range(0, len(order), chunk):
            part = order[i : i + chunk]
            placeholders = ", ".join(f":p{j}" for j in range(len(part)))
            params = {f"p{j}": p for j, p in enumerate(part)}
            rows = db.execute(
                text(f"SELECT pmid, raw_xml FROM literature_pubmed WHERE pmid IN ({placeholders})"),
                params,
            ).fetchall()
            for pmid, raw in rows:
                raw_map[str(pmid)] = raw or ""
    finally:
        db.close()

    out: list[tuple] = []
    for pmid in order:
        rec = by_pmid[pmid]
        raw = raw_map.get(pmid, "")
        out.append((
            pmid,
            rec.get("title_en") or "",
            rec.get("title_zh") or "",
            rec.get("abstract_en") or "",
            rec.get("abstract_zh") or "",
            raw,
        ))
    return out


def _ensure_schema(client: MySQLClient) -> None:
    if not MIGRATION.is_file():
        return
    db = next(client.get_db())
    try:
        for stmt in MIGRATION.read_text(encoding="utf-8").replace("\r\n", "\n").split(";"):
            s = " ".join(
                ln for ln in stmt.splitlines() if ln.strip() and not ln.strip().startswith("--")
            ).strip()
            if not s or s.upper().startswith("USE "):
                continue
            db.execute(text(s))
        db.commit()
    finally:
        db.close()


def _progress_file(worker_id: int, workers: int) -> Path:
    if workers <= 1:
        return PROGRESS
    return ROOT / "data" / "literature" / f"pubmed_yes_fetch_w{worker_id:02d}.json"


def _load_done() -> set[str]:
    done = load_done_pmids()
    if PROGRESS.is_file():
        try:
            done |= set(json.loads(PROGRESS.read_text(encoding="utf-8")).get("done_pmids") or [])
        except json.JSONDecodeError:
            pass
    return done


def _save_progress(state: dict, *, worker_id: int = 0, workers: int = 1) -> None:
    path = _progress_file(worker_id, workers)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _sync_global_progress(state: dict) -> None:
    """多 worker 时也更新全局进度，供看门狗/监管器读取。"""
    if not PROGRESS.is_file() and not state:
        return
    g: dict = {}
    if PROGRESS.is_file():
        try:
            g = json.loads(PROGRESS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            g = {}
    for key in (
        "last_heartbeat_at",
        "last_success_at",
        "network_paused",
        "network_online",
        "last_pmid",
        "status",
    ):
        if key in state:
            g[key] = state[key]
    g["updated_at"] = datetime.now().isoformat(timespec="seconds")
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")


def _touch_heartbeat(state: dict, *, pmid: str = "", success: bool = False, worker_id: int = 0, workers: int = 1) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    state["last_heartbeat_at"] = now
    state["network_online"] = probe_network()
    state["worker_id"] = worker_id
    state["workers"] = workers
    if pmid:
        state["last_pmid"] = pmid
    if success:
        state["last_success_at"] = now
    _save_progress(state, worker_id=worker_id, workers=workers)
    _sync_global_progress(state)


def _wait_for_network_resilient(
    state: dict,
    *,
    worker_id: int = 0,
    workers: int = 1,
    reason: str = "",
) -> None:
    """断网时阻塞等待；周期性刷新 heartbeat，避免看门狗误判卡死。"""
    if probe_network():
        state["network_paused"] = False
        state["network_online"] = True
        _touch_heartbeat(state, worker_id=worker_id, workers=workers)
        return
    prefix = f"[断网等待] {reason}" if reason else "[断网等待]"
    state["network_paused"] = True
    state["network_online"] = False
    state["status"] = "running"
    _touch_heartbeat(state, worker_id=worker_id, workers=workers)
    t0 = time.time()
    last_log = 0.0
    safe_print(f"{prefix} 网络不可用，等待恢复…")
    while True:
        if probe_network():
            state["network_paused"] = False
            state["network_online"] = True
            _touch_heartbeat(state, worker_id=worker_id, workers=workers)
            safe_print(f"{prefix} 网络已恢复（等待 {int(time.time() - t0)}s）")
            return
        now = time.time()
        if now - last_log >= WAIT_LOG_INTERVAL:
            safe_print(f"{prefix} 仍无网络，已等待 {int(now - t0)}s …")
            _touch_heartbeat(state, worker_id=worker_id, workers=workers)
            last_log = now
        time.sleep(PROBE_INTERVAL)


def _efetch_xml(pmid: str) -> str:
    return call_with_network_recovery(
        lambda: _api_get("/efetch.fcgi", {"db": "pubmed", "id": pmid, "retmode": "xml"}),
        log_fn=safe_print,
        label=f"NCBI efetch {pmid}",
    )


def run(
    *,
    resume: bool = True,
    limit: int = 0,
    skip_fulltext: bool = False,
    worker_id: int = 0,
    workers: int = 1,
) -> dict:
    workers = max(1, workers)
    worker_id = max(0, min(worker_id, workers - 1))
    out_path = output_path(worker_id, workers)

    client = MySQLClient()
    _ensure_schema(client)
    done = _load_done() if resume else set()
    db = next(client.get_db())
    try:
        safe_print(f"  [worker {worker_id}/{workers - 1}] 加载 PubMed 判别「是」列表…")
        rows = _load_yes_rows(client)
        safe_print(f"  [worker {worker_id}] 共 {len(rows):,} 篇，全局已完成 {len(done):,} 篇")
    finally:
        db.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if resume and out_path.is_file() else "w"
    t0 = time.time()
    n_ok = 0
    n_ft = 0
    n_skip = 0
    n_delegated = 0
    progress_state: dict = {
        "total_yes": len(rows),
        "status": "running",
        "done_pmids_count": len(done),
        "pending": len(rows) - len(done),
        "corpus_file": str(out_path),
        "worker_id": worker_id,
        "workers": workers,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    _touch_heartbeat(progress_state, worker_id=worker_id, workers=workers)

    if not probe_network():
        safe_print("[断网] 启动时无网络，等待恢复后再拉取…")
        progress_state["network_paused"] = True
        _wait_for_network_resilient(progress_state, worker_id=worker_id, workers=workers, reason="PubMed YES fetch")
        progress_state["network_paused"] = False
        _touch_heartbeat(progress_state, worker_id=worker_id, workers=workers)

    with open(out_path, mode, encoding="utf-8") as out:
        pending_idx = 0
        for pmid, te, tz, ae, az, raw_xml in rows:
            pmid = str(pmid)
            if pmid in done:
                n_skip += 1
                continue
            slot = pending_idx % workers
            pending_idx += 1
            if slot != worker_id:
                n_delegated += 1
                continue
            if limit > 0 and n_ok >= limit:
                break

            _touch_heartbeat(progress_state, pmid=pmid, worker_id=worker_id, workers=workers)

            parsed = parse_pubmed_xml(raw_xml or "")
            if not (raw_xml or "").strip():
                try:
                    fetched = _efetch_xml(pmid)
                    if fetched:
                        parsed = parse_pubmed_xml(fetched)
                except Exception as e:
                    safe_print(f"  [efetch失败] {pmid}: {e}")
                    _wait_for_network_resilient(
                        progress_state,
                        worker_id=worker_id,
                        workers=workers,
                        reason=f"NCBI efetch {pmid}",
                    )
                    continue
                _sleep()

            title_en = (te or parsed.get("title_en") or "").strip()
            if not title_en:
                safe_print(f"  [跳过] {pmid}: 无标题")
                done.add(pmid)
                continue
            title_zh = (tz or title_en).strip()
            abstract_en = (ae or parsed.get("abstract_en") or "").strip()
            abstract_zh = (az or "").strip()
            pmcid = parsed.get("pmcid") or ""
            body_text = ""
            has_fulltext = False
            pmcid_used = pmcid

            if not skip_fulltext:
                try:
                    oa, pmc_search = call_with_network_recovery(
                        lambda: epmc_is_open_access(pmid),
                        log_fn=safe_print,
                        label=f"EPMC OA {pmid}",
                    )
                    if pmc_search and not pmcid:
                        pmcid = pmc_search
                    if oa or pmcid:
                        body_text, pmcid_used, has_fulltext = call_with_network_recovery(
                            lambda: fetch_pmc_fulltext(pmid, pmcid, skip_precheck=True),
                            log_fn=safe_print,
                            label=f"EPMC fulltext {pmid}",
                        )
                    time.sleep(EPMC_INTERVAL)
                except Exception as e:
                    safe_print(f"  [全文失败] {pmid}: {e}")
                    time.sleep(EPMC_INTERVAL)

            rec = {
                "pmid": pmid,
                "title_en": title_en,
                "title_zh": title_zh,
                "abstract_en": abstract_en,
                "abstract_zh": abstract_zh,
                "journal": parsed.get("journal") or "",
                "year": parsed.get("year") or "",
                "doi": parsed.get("doi") or "",
                "pmcid": pmcid_used or pmcid,
                "body_text": body_text,
                "has_fulltext": has_fulltext,
                "fetched_at": datetime.now().isoformat(timespec="seconds"),
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            done.add(pmid)
            n_ok += 1
            if has_fulltext:
                n_ft += 1

            progress_state.update({
                "done_pmids_count": len(done),
                "session_fetched": n_ok,
                "session_fulltext": n_ft,
                "skipped_already": n_skip,
                "pending": len(rows) - len(done),
                "elapsed_min": round((time.time() - t0) / 60, 1),
                "network_paused": False,
            })
            _touch_heartbeat(progress_state, pmid=pmid, success=True, worker_id=worker_id, workers=workers)

            if n_ok % 50 == 0:
                rate = n_ok / max(time.time() - t0, 1) * 60
                safe_print(f"  [worker {worker_id}] 已获取 {n_ok:,} 篇（全文 {n_ft:,}）~{rate:.0f}篇/分")

    stats = {
        "total_yes": len(rows),
        "session_fetched": n_ok,
        "session_fulltext": n_ft,
        "skipped_already": n_skip,
        "delegated_to_other_workers": n_delegated,
        "corpus_file": str(out_path),
        "worker_id": worker_id,
        "workers": workers,
        "elapsed_min": round((time.time() - t0) / 60, 1),
    }
    pending = len(rows) - len(load_done_pmids())
    status = "completed" if pending <= 0 else "running"
    final_state = {
        **stats,
        "done_pmids_count": len(load_done_pmids()),
        "pending": pending,
        "status": status,
        "network_paused": False,
        "finished_at": datetime.now().isoformat(timespec="seconds") if pending <= 0 else None,
    }
    _touch_heartbeat(final_state, success=(n_ok > 0), worker_id=worker_id, workers=workers)
    _save_progress({k: v for k, v in final_state.items() if v is not None}, worker_id=worker_id, workers=workers)
    safe_print(
        f"[worker {worker_id} 完成] 本次获取 {n_ok:,} 篇，其中 PMC 全文 {n_ft:,} 篇；"
        f"语料 → {out_path}"
    )
    return stats


def main() -> int:
    p = argparse.ArgumentParser(description="PubMed 判别「是」文献语料获取（含 PMC 全文）")
    p.add_argument("--resume", action="store_true", default=True)
    p.add_argument("--no-resume", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--skip-fulltext", action="store_true", help="仅摘要，不拉 PMC 全文")
    p.add_argument("--worker-id", type=int, default=0, help="并发分片 worker 编号（0 起）")
    p.add_argument("--workers", type=int, default=1, help="并发 worker 总数")
    args = p.parse_args()
    try:
        run(
            resume=not args.no_resume,
            limit=args.limit,
            skip_fulltext=args.skip_fulltext,
            worker_id=args.worker_id,
            workers=args.workers,
        )
    except Exception as e:
        import traceback
        err_log = ROOT / "logs" / "pubmed_yes_fetch_live.log"
        msg = f"[崩溃] {datetime.now().isoformat(timespec='seconds')} {e}\n{traceback.format_exc()}"
        safe_print(msg)
        with open(err_log, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
        st = {"status": "crashed", "error": str(e), "crashed_at": datetime.now().isoformat(timespec="seconds")}
        if PROGRESS.is_file():
            try:
                old = json.loads(PROGRESS.read_text(encoding="utf-8"))
                old.update(st)
                st = old
            except Exception:
                pass
        _save_progress(st)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
