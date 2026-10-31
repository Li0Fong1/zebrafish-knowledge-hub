# -*- coding: utf-8 -*-
"""Hub 后台维护：文献增量入库、图库轮换、Live Atlas 统计刷新。

周期（可环境变量覆盖）：
- 文献：默认每 7 天增量抓取（PubMed/EuropePMC/WoS）→ knowledge.d → FAISS
- 图库：默认每 3 天 force refresh 一轮镜像
- 统计：默认每 6 小时重算 hub_stats 缓存

与 API 进程同生命周期；任务幂等，日志落 logs/hub_maintenance.log。
手动触发：POST /api/hub/maintenance/run 或 CLI `python -m agent.hub_maintenance`。
"""
from __future__ import annotations

import bootstrap  # noqa: F401

import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from common_utils import logger

# ---- intervals (seconds) ----
LIT_INTERVAL_SEC = int(os.getenv("HUB_MAINT_LIT_SEC", str(7 * 24 * 3600)))
GALLERY_INTERVAL_SEC = int(os.getenv("HUB_MAINT_GALLERY_SEC", str(3 * 24 * 3600)))
STATS_INTERVAL_SEC = int(os.getenv("HUB_MAINT_STATS_SEC", str(6 * 3600)))
TICK_SEC = int(os.getenv("HUB_MAINT_TICK_SEC", "60"))
ENABLED = os.getenv("HUB_MAINTENANCE", "1").strip().lower() not in ("0", "false", "no", "off")

# Weekly literature：默认小批量增量，避免一次打满 NCBI/EPMC
LIT_MAX_PUBMED = int(os.getenv("HUB_MAINT_LIT_MAX_PUBMED", "80"))
LIT_WOS_MAX = int(os.getenv("HUB_MAINT_LIT_WOS_MAX", "40"))
LIT_SOURCES = [
    s.strip()
    for s in os.getenv("HUB_MAINT_LIT_SOURCES", "pubmed,epmc").split(",")
    if s.strip()
]


def _runtime_root() -> Path:
    rt = os.environ.get("ZEBRA_RUNTIME") or ""
    if rt:
        return Path(rt)
    from config import _BASE_DIR

    return Path(_BASE_DIR)


def _logs_dir() -> Path:
    d = _runtime_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path() -> Path:
    return _logs_dir() / "hub_maintenance_state.json"


def _log_path() -> Path:
    return _logs_dir() / "hub_maintenance.log"


def _stats_cache_path() -> Path:
    return _logs_dir() / "hub_stats_cache.json"


def _bg_pools_path() -> Path:
    return _logs_dir() / "hub_bg_pools.json"


def _recent_papers_candidates() -> list[Path]:
    """首页「每日研究」缓存的所有可能落点。

    与 scripts/build_hub_recent_papers.py 的 _runtime_dirs() 对齐，避免
    「写到一个地方、读另一个地方」的路径漂移。
    """
    out: list[Path] = []
    for key in ("ZEBRA_RUNTIME", "ZEBRAFISH_RUNTIME"):
        v = (os.getenv(key) or "").strip()
        if v:
            out.append(Path(v) / "hub_recent_papers.json")
    out.append(_runtime_root() / "hub_recent_papers.json")
    out.append(Path(__file__).resolve().parents[1] / "hub_recent_papers.json")
    uniq: list[Path] = []
    seen: set[str] = set()
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


_recent_papers_lock = threading.Lock()


def refresh_recent_papers() -> dict:
    """重建首页「每日研究」轻量缓存（只读派生物）。

    文献更新后调用；失败只记日志，绝不把主任务标记为失败。
    """
    with _recent_papers_lock:
        try:
            from scripts.build_hub_recent_papers import main as build_main

            rc = int(build_main([]))
            files = [str(p) for p in _recent_papers_candidates() if p.is_file()]
            _append_log(f"recent_papers rebuild rc={rc} files={len(files)}")
            return {"ok": rc == 0, "rc": rc, "paths": files}
        except Exception as e:  # noqa: BLE001
            _append_log(f"recent_papers rebuild FAIL {type(e).__name__}: {e}")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append_log(msg: str) -> None:
    line = f"[{_now_iso()}] {msg}\n"
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    logger.info(f"[maintenance] {msg}")


def _blank_state() -> dict:
    return {
        "literature_last_ts": 0.0,
        "gallery_last_ts": 0.0,
        "stats_last_ts": 0.0,
        "literature_last_result": None,
        "gallery_last_result": None,
        "stats_last_result": None,
        "literature_progress": None,
        "runs": 0,
    }


def _state_candidates() -> list[Path]:
    """状态文件的全部可能落点。

    历史遗留：早期部署把状态写在 `zebrafish_runtime/logs/`，现在进程没有
    `ZEBRA_RUNTIME` 环境变量，`_runtime_root()` 回落到项目目录，于是出现两份
    各自漂移的状态（一份 10-20、一份 10-24）。只读时把两边合并成一份，
    写只写规范位置，避免「该跑不跑」或「重复跑」。
    """
    cands: list[Path] = [Path(_state_path())]
    try:
        from config import _BASE_DIR

        cands.append(Path(_BASE_DIR).parent / "zebrafish_runtime" / "logs" / "hub_maintenance_state.json")
    except Exception:
        pass
    for key in ("ZEBRA_RUNTIME", "ZEBRAFISH_RUNTIME"):
        v = (os.environ.get(key) or "").strip()
        if v:
            cands.append(Path(v) / "logs" / "hub_maintenance_state.json")
    uniq: list[Path] = []
    seen: set[str] = set()
    for p in cands:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


def _ts_of(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("ts") or "")
    return ""


def load_state() -> dict:
    """读取状态：多份候选文件按「时间戳取最大」合并，避免分叉导致判断错位。"""
    merged = _blank_state()
    for path in _state_candidates():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for k, v in data.items():
            if k.endswith("_last_ts"):
                try:
                    merged[k] = max(float(merged.get(k) or 0), float(v or 0))
                except (TypeError, ValueError):
                    pass
            elif k == "runs":
                try:
                    merged[k] = max(int(merged.get(k) or 0), int(v or 0))
                except (TypeError, ValueError):
                    pass
            elif k.endswith("_last_result") or k.endswith("_progress"):
                if not isinstance(v, dict):
                    continue
                if _ts_of(v) >= _ts_of(merged.get(k)):
                    merged[k] = v
            elif k not in merged:
                merged[k] = v
    # 进度只在「任务确实在跑」时有意义：比最后一次结果还旧的进度一律作废，
    # 否则旧进程遗留的 collecting 会一直挂着。
    prog = merged.get("literature_progress")
    if isinstance(prog, dict) and _ts_of(prog) and _ts_of(prog) < _ts_of(merged.get("literature_last_result")):
        merged["literature_progress"] = None
    return merged


def save_state(state: dict) -> None:
    path = _state_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


_lock = threading.Lock()
_running: dict[str, bool] = {"literature": False, "gallery": False, "stats": False, "all": False}
_scheduler_started = False
_stop_event = threading.Event()


def status() -> dict:
    st = load_state()
    now = time.time()

    def due(last: float, interval: int) -> bool:
        return (now - float(last or 0)) >= interval

    return {
        "enabled": ENABLED,
        "now": _now_iso(),
        "intervals_sec": {
            "literature": LIT_INTERVAL_SEC,
            "gallery": GALLERY_INTERVAL_SEC,
            "stats": STATS_INTERVAL_SEC,
        },
        "running": dict(_running),
        "due": {
            "literature": due(st.get("literature_last_ts", 0), LIT_INTERVAL_SEC),
            "gallery": due(st.get("gallery_last_ts", 0), GALLERY_INTERVAL_SEC),
            "stats": due(st.get("stats_last_ts", 0), STATS_INTERVAL_SEC),
        },
        "state": st,
        "progress": st.get("literature_progress"),
        "paths": {
            "knowledge_extra": _knowledge_extra(),
            "uploads": _uploads_dir(),
            "stats_cache": str(_stats_cache_path()),
            "bg_pools": str(_bg_pools_path()),
            "log": str(_log_path()),
            "state": str(_state_path()),
        },
    }


def _knowledge_extra() -> str:
    try:
        from config import KNOWLEDGE_EXTRA_DIR

        return KNOWLEDGE_EXTRA_DIR
    except Exception:
        return ""


# ---------------------------------------------------------------- 文献更新产物
# 采集器是流式入库的，跑完只回一个 {"new": n}。首页要显示「新增 N 篇」、要写
# literature_sync_log / literature_paper，就必须拿到这批具体是哪几条。
def _progress_writer():
    """返回一个节流的进度回调：采集器每页都会调，这里 5 秒才落一次盘。"""
    last = {"t": 0.0}

    def cb(stage: str, info: dict) -> None:
        now = time.time()
        if stage == "collecting" and now - last["t"] < 5:
            return
        last["t"] = now
        try:
            st = load_state()
            cur = dict(st.get("literature_progress") or {})
            cur.update({k: v for k, v in (info or {}).items() if isinstance(v, (int, float, str))})
            cur["stage"] = stage
            cur["ts"] = _now_iso()
            cur["updated_at"] = round(now, 0)
            st["literature_progress"] = cur
            save_state(st)
        except Exception:
            pass

    return cb


def _doc_id_of(rec: dict) -> tuple[str, str]:
    """(doc_id, pmid)：优先 PMID，其次 DOI，最后 EPMC id。"""
    pmid = str(rec.get("pmid") or "").strip()
    if pmid.isdigit():
        return f"pmid:{pmid}", pmid
    doi = str(rec.get("doi") or "").strip().lower()
    if doi:
        return ("doi:" + doi)[:64], ""
    epmc = str(rec.get("epmc_id") or "").strip()
    if epmc:
        return f"epmc:{epmc}"[:64], ""
    return "", ""


SYNC_ACTIONS = {
    "auto": "weekly_literature",
    "manual": "manual_literature",
    "cli": "cli_literature",
}


def _persist_literature(records: list, *, trigger: str, elapsed: float, stats: dict) -> dict:
    """把本次新入库的文献写进 MySQL：主表 + 同步日志。

    这是首页「更新记录」唯一的数据来源（literature_sync_log），也是「库内文献 N 篇」
    与年度分布的数据来源（literature_paper）。不写的话，即使真的采到了文献，界面
    也永远停在旧数字上 —— 这正是 8 月以来「更新记录」不动的原因。
    """
    out: dict[str, Any] = {"papers": 0, "logged": False}
    records = list(records or [])
    if not records:
        return out
    try:
        try:
            from mysql_client import get_mysql_client  # type: ignore
            from literature_store import upsert_paper, write_sync_log  # type: ignore
        except ImportError:
            from database.mysql_client import get_mysql_client  # type: ignore
            from database.literature_store import upsert_paper, write_sync_log  # type: ignore
    except Exception as e:  # noqa: BLE001
        out["error"] = f"import: {type(e).__name__}: {e}"
        return out

    db = None
    try:
        db = next(get_mysql_client().get_db())
        n = 0
        for rec in records:
            doc_id, pmid = _doc_id_of(rec)
            if not doc_id:
                continue
            upsert_paper(
                db,
                {
                    "doc_id": doc_id,
                    "pmid": pmid or None,
                    "doi": (str(rec.get("doi") or "").strip() or None),
                    "source": (str(rec.get("source") or "pubmed")[:22] + "_incremental")[:32],
                    "title_en": (rec.get("title") or None),
                    "year": rec.get("year"),
                    "journal": (str(rec.get("journal") or "")[:512] or None),
                    "judgement": "是",
                },
            )
            n += 1
        action = SYNC_ACTIONS.get(trigger, "manual_literature")
        write_sync_log(
            db,
            action=action,
            actor="system" if trigger == "auto" else "manual_update",
            detail={
                "new": (stats or {}).get("new", n),
                "upserted_paper": n,
                "trigger": trigger,
                "elapsed_sec": round(float(elapsed), 1),
                "sources": (stats or {}).get("sources") or "",
                "index": (stats or {}).get("index") or {},
            },
        )
        db.commit()
        out["papers"] = n
        out["logged"] = True
        out["action"] = action
    except Exception as e:  # noqa: BLE001
        try:
            if db is not None:
                db.rollback()
        except Exception:
            pass
        out["error"] = f"{type(e).__name__}: {e}"
        _append_log(f"literature persist FAIL {out['error']}")
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
    return out


def start_async_job(name: str = "literature", *, trigger: str = "manual",
                    max_pubmed: Optional[int] = None) -> dict:
    """后台起一个维护任务并立刻返回。

    为什么必须异步：一次文献更新是「抓取 → 入库 → 建索引」，实测跑过 4 小时；
    前端是隔着 Cloudflare 隧道发的 POST（约 100 秒就 524），同步等结果必然
    报「触发失败」。改成起线程 + 前端轮询 /maintenance/status。
    """
    name = (name or "literature").strip().lower()
    if name not in ("literature", "gallery", "stats"):
        return {"ok": False, "error": f"unknown job: {name}"}
    with _lock:
        if _running.get(name):
            return {"ok": False, "skipped": True, "reason": "already_running", "job": name}

    def _run() -> None:
        try:
            if name == "literature":
                run_literature_job(max_pubmed=max_pubmed, trigger=trigger)
            elif name == "gallery":
                run_gallery_job(force=True)
            else:
                run_stats_job()
        except Exception as e:  # noqa: BLE001
            _append_log(f"async {name} FAIL {type(e).__name__}: {e}")
            with _lock:
                _running[name] = False

    threading.Thread(target=_run, daemon=True, name=f"hub-job-{name}").start()
    return {"ok": True, "started": True, "job": name, "trigger": trigger, "ts": _now_iso()}


def _uploads_dir() -> str:
    try:
        from config import KNOWLEDGE_UPLOAD_DIR

        return KNOWLEDGE_UPLOAD_DIR
    except Exception:
        return ""


def audit_literature() -> dict:
    """审计文献入库路径与规模（不抓网）。"""
    from config import KNOWLEDGE_EXTRA_DIR, KNOWLEDGE_PATH, KNOWLEDGE_UPLOAD_DIR, FAISS_INDEX_DIR, _BASE_DIR

    extra = KNOWLEDGE_EXTRA_DIR
    extra_files = []
    if os.path.isdir(extra):
        extra_files = sorted(
            f for f in os.listdir(extra) if f.endswith((".txt", ".md"))
        )
    catalog = os.path.join(_BASE_DIR, "data", "literature", "literature_catalog.jsonl")
    catalog_n = 0
    if os.path.isfile(catalog):
        with open(catalog, encoding="utf-8") as f:
            catalog_n = sum(1 for _ in f)
    progress = os.path.join(_BASE_DIR, "data", "ensembl", "literature_progress.json")
    pmid_n = 0
    if os.path.isfile(progress):
        try:
            data = json.loads(Path(progress).read_text(encoding="utf-8"))
            pmid_n = len(data.get("pmids") or [])
        except Exception:
            pass
    upload_tenants: list[dict] = []
    if os.path.isdir(KNOWLEDGE_UPLOAD_DIR):
        for name in sorted(os.listdir(KNOWLEDGE_UPLOAD_DIR))[:40]:
            p = os.path.join(KNOWLEDGE_UPLOAD_DIR, name)
            if os.path.isdir(p):
                upload_tenants.append(
                    {"tenant": name, "files": len(os.listdir(p))}
                )
    faiss_ok = os.path.isdir(FAISS_INDEX_DIR) and any(
        Path(FAISS_INDEX_DIR).glob("*")
    )
    return {
        "ok": True,
        "knowledge_main": {"path": KNOWLEDGE_PATH, "exists": os.path.isfile(KNOWLEDGE_PATH)},
        "knowledge_d": {
            "path": extra,
            "exists": os.path.isdir(extra),
            "file_count": len(extra_files),
            "sample": extra_files[:8],
        },
        "uploads": {"path": KNOWLEDGE_UPLOAD_DIR, "tenants": upload_tenants},
        "catalog": {"path": catalog, "lines": catalog_n},
        "progress_pmids": pmid_n,
        "faiss_index": {"path": FAISS_INDEX_DIR, "ready": faiss_ok},
        "adoption": (
            "上传 → rag/uploads/<tenant_u{id}>/ 原件 + rag/knowledge.d/ 文本；"
            "rebuild_rag_index() 重建 FAISS；Hub RAG / PubmedYes 下次检索即可命中。"
        ),
    }


def run_literature_job(*, dry_run: bool = False, max_pubmed: Optional[int] = None,
                       trigger: str = "auto", _reserved: bool = False) -> dict:
    """增量文献：多源 scrape → parse/ingest → 追加向量索引 → 写库/写同步日志。

    trigger: auto（每 7 天自动）/ manual（首页按钮）/ cli，只影响同步日志里的
    action 命名，方便首页「更新记录」区分是谁触发的。
    _reserved: 由 start_async_job 占位后调用，避免自己的互斥判断把任务挡掉。
    """
    with _lock:
        if _running["literature"] and not _reserved:
            return {"ok": False, "skipped": True, "reason": "already_running"}
        _running["literature"] = True
    t0 = time.time()
    result: dict[str, Any] = {"ok": False, "job": "literature", "dry_run": dry_run,
                              "trigger": trigger, "ts": _now_iso()}
    try:
        audit = audit_literature()
        result["audit_before"] = {
            "knowledge_d_files": audit["knowledge_d"]["file_count"],
            "catalog_lines": audit["catalog"]["lines"],
            "progress_pmids": audit["progress_pmids"],
        }
        if dry_run:
            result["ok"] = True
            result["message"] = "dry-run：跳过网络抓取，仅审计路径"
            result["plan"] = {
                "sources": LIT_SOURCES or ["pubmed"],
                "max_pubmed": max_pubmed if max_pubmed is not None else LIT_MAX_PUBMED,
                "wos_max": LIT_WOS_MAX,
                "target": audit["knowledge_d"]["path"],
                "rebuild": True,
            }
            _append_log(f"literature dry-run ok files={audit['knowledge_d']['file_count']}")
            return result

        max_pm = LIT_MAX_PUBMED if max_pubmed is None else int(max_pubmed)
        sources = list(LIT_SOURCES) or ["pubmed"]
        # multi collector 认 wos / pubmed；epmc 走 pubmed_backend=epmc
        mc_sources = []
        for s in sources:
            sl = s.lower()
            if sl in ("wos", "pubmed"):
                mc_sources.append(sl)
            elif sl in ("epmc", "europepmc"):
                if "pubmed" not in mc_sources:
                    mc_sources.append("pubmed")
        if not mc_sources:
            mc_sources = ["pubmed"]

        # 进度先落一条「开始」，前端第一次轮询就能看到「采集中」
        st = load_state()
        st["literature_progress"] = {
            "stage": "collecting",
            "new": 0,
            "started_at": round(t0, 0),
            "ts": _now_iso(),
            "trigger": trigger,
        }
        save_state(st)
        _append_log(f"literature start trigger={trigger} max_pubmed={max_pm} sources={mc_sources}")

        from multi_literature_collector import (
            run_multi_source_collection,
            take_new_records,
        )

        stats = run_multi_source_collection(
            sources=mc_sources,
            max_pubmed=max_pm,
            wos_per_query=LIT_WOS_MAX if "wos" in mc_sources else 0,
            broad_only=True,
            rebuild_index=True,
            resume=True,
            import_wos_ris=("wos" in mc_sources),
            pubmed_backend="epmc",
            on_stage=_progress_writer(),
        )
        stats["sources"] = ",".join(mc_sources)
        new_records = take_new_records()
        audit_after = audit_literature()
        result["ok"] = True
        result["stats"] = stats
        result["audit_after"] = {
            "knowledge_d_files": audit_after["knowledge_d"]["file_count"],
            "catalog_lines": audit_after["catalog"]["lines"],
            "progress_pmids": audit_after["progress_pmids"],
        }
        result["elapsed_sec"] = round(time.time() - t0, 2)

        # 写库 + 写同步日志：0 篇也记一条状态，但只有真有新增才写同步日志
        if new_records:
            result["persist"] = _persist_literature(
                new_records,
                trigger=trigger,
                elapsed=result["elapsed_sec"],
                stats=stats,
            )
        st = load_state()
        st["literature_last_ts"] = time.time()
        st["literature_last_result"] = {
            "ok": True,
            "new": (stats or {}).get("new", 0),
            "new_papers": (result.get("persist") or {}).get("papers", 0),
            "index": (stats or {}).get("index") or {},
            "elapsed_sec": result["elapsed_sec"],
            "trigger": trigger,
            "ts": _now_iso(),
        }
        # 首页「每日研究」缓存还要重建（实测约 50 秒）：这段时间不能说「检索与入库中」，
        # 否则前端会以为还在抓取，且已入库数会消失。改成 finalizing，并在缓存写完才清除。
        st["literature_progress"] = {
            "stage": "finalizing",
            "new": (stats or {}).get("new", 0),
            "ts": _now_iso(),
            "updated_at": round(time.time(), 0),
        }
        st["runs"] = int(st.get("runs") or 0) + 1
        save_state(st)
        # 文献更新后同步重建首页「每日研究」缓存（只读派生物，失败不影响主流程）
        result["recent_papers"] = refresh_recent_papers()
        st = load_state()
        st["literature_progress"] = None
        save_state(st)
        _append_log(
            f"literature ok new={(stats or {}).get('new')} "
            f"papers={(result.get('persist') or {}).get('papers', 0)} "
            f"elapsed={result['elapsed_sec']}s trigger={trigger}"
        )
        return result
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["trace"] = traceback.format_exc()[-2000:]
        _append_log(f"literature FAIL {result['error']}")
        try:
            st = load_state()
            st["literature_last_ts"] = time.time()
            st["literature_last_result"] = {
                "ok": False,
                "new": 0,
                "error": result["error"][:300],
                "trigger": trigger,
                "ts": _now_iso(),
            }
            st["literature_progress"] = None
            save_state(st)
        except Exception:
            pass
        return result
    finally:
        with _lock:
            _running["literature"] = False


def run_gallery_job(*, force: bool = True, web_dir: Optional[str] = None) -> dict:
    """每 3 天轮换一批图：force refresh + 重建页面背景池。"""
    with _lock:
        if _running["gallery"]:
            return {"ok": False, "skipped": True, "reason": "already_running"}
        _running["gallery"] = True
    result: dict[str, Any] = {"ok": False, "job": "gallery", "ts": _now_iso()}
    try:
        from config import _BASE_DIR

        wd = web_dir or os.path.join(_BASE_DIR, "web")
        try:
            from api.gallery_feed import get_gallery_feed, build_gallery_feed
        except ImportError:
            from gallery_feed import get_gallery_feed, build_gallery_feed  # type: ignore

        # force 清缓存并后台拉取；同步再 build 一轮保证磁盘有新镜像
        instant = get_gallery_feed(wd, mode="mix", limit=240, force=bool(force))
        try:
            full = build_gallery_feed(wd, mode="mix", limit=240)
        except Exception as e:
            full = instant
            result["build_warn"] = f"{type(e).__name__}: {e}"

        items = list((full or {}).get("items") or (instant or {}).get("items") or [])
        pools = partition_bg_pools(items)
        _bg_pools_path().write_text(
            json.dumps(
                {
                    "refreshed_at": _now_iso(),
                    "count": len(items),
                    "pools": {k: v for k, v in pools.items()},
                    "batch_id": int(time.time()),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        # 也写到 web 静态目录，便于前端直取
        static_pools = Path(wd) / "assets" / "zf-gallery" / "bg_pools.json"
        try:
            static_pools.parent.mkdir(parents=True, exist_ok=True)
            static_pools.write_text(
                json.dumps(
                    {
                        "refreshed_at": _now_iso(),
                        "count": len(items),
                        "pools": pools,
                        "batch_id": int(time.time()),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

        result["ok"] = True
        result["count"] = len(items)
        result["live_count"] = (full or instant or {}).get("live_count")
        result["pool_keys"] = list(pools.keys())
        result["pool_sizes"] = {k: len(v) for k, v in pools.items()}
        st = load_state()
        st["gallery_last_ts"] = time.time()
        st["gallery_last_result"] = {
            "ok": True,
            "count": len(items),
            "ts": _now_iso(),
            "pool_sizes": result["pool_sizes"],
        }
        st["runs"] = int(st.get("runs") or 0) + 1
        save_state(st)
        _append_log(f"gallery ok count={len(items)} pools={result['pool_sizes']}")
        return result
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        result["trace"] = traceback.format_exc()[-1500:]
        _append_log(f"gallery FAIL {result['error']}")
        return result
    finally:
        with _lock:
            _running["gallery"] = False


# 各功能页独立图池（与导航/路由对齐）
BG_POOL_KEYS = (
    "home",
    "chat",
    "gene",
    "blast",
    "enrich",
    "kegg",
    "id",
    "sequence",
    "literature",
    "annotate",
    "upload",
    "hub",  # alias chat / multi-agent
)


def partition_bg_pools(items: list[dict]) -> dict[str, list[str]]:
    """把图库 URL 均匀且可复现地划入各页独立池（非共享同一顺序）。
    同时按 URL 与**内容指纹**去重：live/ 镜像图与静态 img_0xx 常为同一张图，
    若只按 URL 去重，同一张图会同时出现在多个页的池子里。"""
    urls = []
    seen_hash: set[str] = set()
    for it in items:
        u = (it or {}).get("url") or ""
        if not u or not str(u).startswith("/static/"):
            continue
        ck = (it or {}).get("content_key") or ""
        if ck and ck in seen_hash:
            continue
        if ck:
            seen_hash.add(ck)
        urls.append(str(u))
    # 去重保序
    seen = set()
    uniq = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    pools: dict[str, list[str]] = {k: [] for k in BG_POOL_KEYS}
    if not uniq:
        return pools
    n = len(BG_POOL_KEYS)
    # 交错切片 + 每池再旋转，保证页间集合差异大
    for i, u in enumerate(uniq):
        pools[BG_POOL_KEYS[i % n]].append(u)
    for idx, key in enumerate(BG_POOL_KEYS):
        arr = pools[key]
        if len(arr) < 4 and uniq:
            # 池太小则从全量里取偏移子集补齐（仍保持不同起点）
            start = (idx * 7) % len(uniq)
            extra = uniq[start:] + uniq[:start]
            for u in extra:
                if u not in arr:
                    arr.append(u)
                if len(arr) >= max(8, len(uniq) // max(1, n // 2)):
                    break
        # 旋转，避免与相邻池同序
        if arr:
            rot = (idx * 3 + 1) % len(arr)
            pools[key] = arr[rot:] + arr[:rot]
    return pools


def run_stats_job() -> dict:
    """重算 Live Atlas / hub stats 并落盘缓存。"""
    with _lock:
        if _running["stats"]:
            return {"ok": False, "skipped": True, "reason": "already_running"}
        _running["stats"] = True
    result: dict[str, Any] = {"ok": False, "job": "stats", "ts": _now_iso()}
    try:
        from hub_orchestrator import hub_stats

        stats = hub_stats()
        # 附加 knowledge.d / catalog 实时数
        audit = audit_literature()
        stats.setdefault("hub", {})
        stats["hub"]["knowledge_d_files"] = audit["knowledge_d"]["file_count"]
        stats["hub"]["literature_catalog_lines"] = audit["catalog"]["lines"]
        stats["hub"]["progress_pmids"] = audit["progress_pmids"]
        stats["refreshed_at"] = _now_iso()
        stats["refreshed_ts"] = time.time()
        _stats_cache_path().write_text(
            json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        result["ok"] = True
        result["refreshed_at"] = stats["refreshed_at"]
        result["snapshot"] = {
            "genes": (stats.get("database") or {}).get("gene_basic"),
            "papers": stats["hub"].get("literature_catalog_lines"),
            "knowledge_d": stats["hub"].get("knowledge_d_files"),
            "go": (stats.get("database") or {}).get("gene_go"),
            "kegg": stats["hub"].get("kegg_pathway_count"),
        }
        st = load_state()
        st["stats_last_ts"] = time.time()
        st["stats_last_result"] = {
            "ok": True,
            "ts": stats["refreshed_at"],
            "snapshot": result["snapshot"],
        }
        st["runs"] = int(st.get("runs") or 0) + 1
        save_state(st)
        _append_log(f"stats ok {result['snapshot']}")
        return result
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        _append_log(f"stats FAIL {result['error']}")
        return result
    finally:
        with _lock:
            _running["stats"] = False


def get_cached_stats() -> Optional[dict]:
    path = _stats_cache_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def run_job(name: str, **kwargs) -> dict:
    name = (name or "").strip().lower()
    if name in ("lit", "literature", "papers"):
        return run_literature_job(**kwargs)
    if name in ("gallery", "images", "bg"):
        return run_gallery_job(**kwargs)
    if name in ("stats", "atlas", "live_atlas"):
        return run_stats_job()
    if name in ("all", "tick"):
        with _lock:
            if _running["all"]:
                return {"ok": False, "skipped": True, "reason": "all_already_running"}
            _running["all"] = True
        try:
            out = {
                "ok": True,
                "job": "all",
                "literature": run_literature_job(dry_run=kwargs.get("dry_run", False)),
                "gallery": run_gallery_job(force=True),
                "stats": run_stats_job(),
            }
            out["ok"] = all(
                (out[k] or {}).get("ok") or (out[k] or {}).get("skipped")
                for k in ("literature", "gallery", "stats")
            )
            return out
        finally:
            with _lock:
                _running["all"] = False
    return {"ok": False, "error": f"unknown job: {name}"}


def _scheduler_loop() -> None:
    _append_log("scheduler started")
    # 启动后稍等再跑统计，避免与模型预热抢资源
    time.sleep(min(45, TICK_SEC * 2))
    while not _stop_event.is_set():
        if not ENABLED:
            _stop_event.wait(TICK_SEC)
            continue
        try:
            st = load_state()
            now = time.time()
            if now - float(st.get("stats_last_ts") or 0) >= STATS_INTERVAL_SEC:
                run_stats_job()
            if now - float(st.get("gallery_last_ts") or 0) >= GALLERY_INTERVAL_SEC:
                run_gallery_job(force=True)
            if now - float(st.get("literature_last_ts") or 0) >= LIT_INTERVAL_SEC:
                # 周更：后台小批量；失败不阻塞下一周期
                run_literature_job(dry_run=False, trigger="auto")
        except Exception as e:
            _append_log(f"scheduler tick error: {type(e).__name__}: {e}")
        _stop_event.wait(TICK_SEC)
    _append_log("scheduler stopped")


def start_scheduler() -> bool:
    """在 API lifespan 中调用；幂等。"""
    global _scheduler_started
    if not ENABLED:
        _append_log("scheduler disabled by HUB_MAINTENANCE=0")
        return False
    with _lock:
        if _scheduler_started:
            return False
        _scheduler_started = True
    t = threading.Thread(target=_scheduler_loop, name="hub-maintenance", daemon=True)
    t.start()
    return True


def stop_scheduler() -> None:
    _stop_event.set()


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Hub maintenance CLI")
    p.add_argument(
        "job",
        nargs="?",
        default="status",
        choices=("status", "audit", "literature", "gallery", "stats", "all"),
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max-pubmed", type=int, default=None)
    p.add_argument("--force-gallery", action="store_true", default=True)
    args = p.parse_args(argv)

    if args.job == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if args.job == "audit":
        print(json.dumps(audit_literature(), ensure_ascii=False, indent=2))
        return 0
    if args.job == "literature":
        out = run_literature_job(dry_run=args.dry_run, max_pubmed=args.max_pubmed, trigger="cli")
    elif args.job == "gallery":
        out = run_gallery_job(force=args.force_gallery)
    elif args.job == "stats":
        out = run_stats_job()
    else:
        out = run_job("all", dry_run=args.dry_run)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
