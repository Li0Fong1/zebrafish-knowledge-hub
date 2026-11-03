# -*- coding: utf-8 -*-
"""斑马鱼基因问答 Web API 服务。"""
from core.entry import bootstrap_app

bootstrap_app()

import asyncio
import hashlib
import json
import os
import queue
import re
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from config import WEB_HOST, WEB_PORT, _BASE_DIR
from common_utils import logger, set_stream_sink, clear_stream_sink, hold_stream_before_refs
from web_security import (
    WebSecurityMiddleware,
    RequestGuardMiddleware,
    SecurityHeadersMiddleware,
    PayloadCryptoMiddleware,
    auth_config,
    get_access_token,
    get_cors_origins,
    hub_credential_slot,
    note_hub_login_fail,
    note_hub_login_ok,
)

WEB_DIR = os.path.join(_BASE_DIR, "web")

_warmup_lock = threading.Lock()
_warmup_started = False
_warmup_done = False
_warmup_error = None
_pubmed_warmup_started = False
_pubmed_warmup_done = False
_pubmed_warmup_error = None
# 因内存不足主动跳过重预热（不等于失败：首次访问会按需加载）
_pubmed_warmup_skipped = False
# 跳过的原因："disabled"（HUB_PUBMED_WARMUP=0，按配置不预热，属正常状态）
#              "low_mem"（内存余量不够，属告警）
# 供 /api/health 与运维脚本区分“按需加载”和“真的装不下”。
_pubmed_warmup_skip_reason = None
_access_stats = {"chat": 0, "requests": 0}

# 兼容性保留：旧版「全站串行锁」的等待上限，现已由并发闸（chat_gate）接管
_CHAT_LOCK_WAIT_SEC = float(os.getenv("CHAT_LOCK_WAIT_SEC", "45"))
# 新并发闸：单个问题允许排队等待的最长时间（超时返回 503，前端提示稍后再试）
_CHAT_QUEUE_WAIT_SEC = float(os.getenv("CHAT_QUEUE_WAIT_SEC", "900"))

# /api/warmup 专用状态（与启动预热分开计数，幂等）
_rag_warmup_started = False
_rag_warmup_done = False
_rag_warmup_error = None


# ---------------------------------------------------------------------------
# 内存闸门（cgroup v2 感知）—— 治"总卡/反复重启"的根因
#
# 现场证据（2026-10-03 服务端实测）：
#   用户 slice memory.max = 36 GB，memory.events 里 oom_kill = 714 次；
#   uvicorn 启动后 RSS 20 秒内 27.2GB → 30.8GB，撞上限被内核 OOM 秒杀
#   —— 无 traceback、无 "hub exited" 日志（守护脚本同时被带走），
#   外部 cron 守护只会把它拉起来再来一次，于是每 2~3 分钟死一次。
#
# 触发者：PubmedYes（35 个 FAISS 分片 + Qwen3-Embedding-4B + CrossEncoder）
#   与 RAG/重模型**并行**预热，峰值内存必然越过 36GB。
# 对策：重预热前先看"真正可分配余量"，不够就跳过（宁可少预热，不能被 OOM 杀掉）。
# ---------------------------------------------------------------------------
def _cgroup_v2_dir() -> str:
    """定位当前进程所属的 cgroup v2 目录（找不到就回落到根）。"""
    try:
        last = open("/proc/self/cgroup", encoding="utf-8").read().strip().splitlines()[-1]
        rel = last.split(":", 2)[-1].strip()
        if rel and rel != "/":
            return os.path.join("/sys/fs/cgroup", rel.lstrip("/"))
    except Exception:
        pass
    return "/sys/fs/cgroup"


def _mem_info() -> dict:
    """{limit_mb, used_mb, headroom_mb}。

    headroom 用 `memory.current - inactive_file` 估算：page cache 可回收，
    不计入"还能用多少"，否则刚跑过一轮的机器会被误判成内存紧张而白白跳过预热。
    无 cgroup 限制时用 /proc/meminfo 的 MemAvailable 兜底。
    """
    out: dict = {"limit_mb": None, "used_mb": None, "headroom_mb": None}
    d = _cgroup_v2_dir()
    # 逐级向上找“最近一个真正设了上限”的 cgroup：线上 uvicorn 跑在
    # /user.slice/user-1005.slice/user@1005.service/app.slice/run-*.scope，
    # 该 scope 自身 memory.max=max，真正的 36GB 上限在 user-1005.slice 上。
    for _ in range(10):
        mx = os.path.join(d, "memory.max")
        if os.path.isfile(mx):
            try:
                raw = open(mx, encoding="utf-8").read().strip()
            except Exception:
                raw = ""
            if raw.isdigit():
                limit_mb = int(raw) / 1048576.0
                used_mb = int(open(os.path.join(d, "memory.current"), encoding="utf-8").read().strip()) / 1048576.0
                inactive_mb = 0.0
                st = os.path.join(d, "memory.stat")
                if os.path.isfile(st):
                    for line in open(st, encoding="utf-8"):
                        k, _, v = line.partition(" ")
                        if k == "inactive_file" and v.strip().isdigit():
                            inactive_mb = int(v.strip()) / 1048576.0
                            break
                out["limit_mb"] = limit_mb
                out["used_mb"] = used_mb
                out["headroom_mb"] = limit_mb - max(0.0, used_mb - inactive_mb)
                out["cgroup"] = d
                return out
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    try:
        info: dict = {}
        for line in open("/proc/meminfo", encoding="utf-8"):
            k, _, v = line.partition(":")
            info[k.strip()] = int(v.split()[0])
        if "MemAvailable" in info:
            out["headroom_mb"] = info["MemAvailable"] / 1024.0
            if "MemTotal" in info:
                out["limit_mb"] = info["MemTotal"] / 1024.0
                out["used_mb"] = out["limit_mb"] - out["headroom_mb"]
    except Exception:
        pass
    return out


def _mem_headroom_mb() -> float:
    h = _mem_info().get("headroom_mb")
    return float(h) if h is not None else float("inf")


def _reclaim_memory() -> float:
    """把空闲堆还给操作系统，返回回收后的余量（MB）。

    为什么需要（2026-10-28 线上实测）：单次问答后 uvicorn RSS 从 2.5GB 涨到 27GB，
    两次之后 33.6GB —— cgroup 上限 36GB，余量只剩 887MB，再来一个请求就被内核
    直接 OOM 杀掉（hub_supervise.log 里 code=137），用户看到的是整站 502／「打不开」。
    Python 对象先 gc.collect() 释放，再让 glibc 用 malloc_trim 把空闲 arena 还给系统；
    否则进程会一直贴着上限跑。
    """
    try:
        import gc

        gc.collect()
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes

        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001
        pass
    return _mem_headroom_mb()


def _mem_fields() -> dict:
    """给 /api/health 用的内存字段（-1 表示取不到）。"""
    info = _mem_info()
    def _i(x):
        return int(x) if isinstance(x, (int, float)) else -1
    return {
        "mem_limit_mb": _i(info.get("limit_mb")),
        "mem_used_mb": _i(info.get("used_mb")),
        "mem_headroom_mb": _i(info.get("headroom_mb")),
    }


def _mem_gate(need_mb: float, what: str) -> bool:
    """余量不足则放弃这次重预热。HUB_WARMUP_MEM_GUARD=0 可整体关闭本闸门。"""
    if os.getenv("HUB_WARMUP_MEM_GUARD", "1").strip().lower() in ("0", "false", "no"):
        return True
    info = _mem_info()
    head = info.get("headroom_mb")
    if head is None:
        return True
    if head < need_mb:
        logger.warning(
            f"[预热] 跳过{what}：可分配余量仅 {head:.0f}MB < 需要 {need_mb:.0f}MB"
            f"（cgroup 上限 {info.get('limit_mb') or 0:.0f}MB / 已用 {info.get('used_mb') or 0:.0f}MB）；"
            f"该资源改为首次访问时按需加载。如需强制预热：HUB_WARMUP_MEM_GUARD=0"
        )
        return False
    return True


# 重预热的内存门槛（MB，可用环境变量覆盖）
_WARMUP_PRELOAD_MIN_MB = float(os.getenv("HUB_WARMUP_MIN_FREE_MB", "4000"))
# PubmedYes（35 个 FAISS 分片 + Qwen3-Embedding-4B + CE）实测要 30GB 以上的真实内存；
# 本机 cgroup memory.max 只有 36GB，实测“预热也死、不预热也活”——死循环的元凶。
# 因此默认不预热（首次文献检索时按需加载）；确认内存预算充足后再用 HUB_PUBMED_WARMUP=1 打开。
_PUBMED_WARMUP_ENABLED = os.getenv("HUB_PUBMED_WARMUP", "0").strip().lower() in ("1", "true", "yes")
_PUBMED_WARMUP_MIN_MB = float(os.getenv("HUB_PUBMED_WARMUP_MIN_MB", "30000"))
# RAG/重模型（4B 嵌入 + 知识库索引）常驻约 16~18GB。共享服务器上默认仍预热（与原来一致）；
# 只在明确要求“省内存”时设 HUB_WARMUP_RAG=0 → 启动后只留轻量模型，重模型改为首次访问按需加载。
_RAG_WARMUP_ENABLED = os.getenv("HUB_WARMUP_RAG", "1").strip().lower() in ("1", "true", "yes")


def _warmup_pubmed_yes_faiss_ce():
    """后台预热 PubmedYes FAISS 分片 + CrossEncoder，避免首请求加载 35 shard。"""
    global _pubmed_warmup_started, _pubmed_warmup_done, _pubmed_warmup_error, _pubmed_warmup_skipped
    global _pubmed_warmup_skip_reason
    _pubmed_warmup_started = True
    try:
        if not _PUBMED_WARMUP_ENABLED:
            _pubmed_warmup_skipped = True
            _pubmed_warmup_skip_reason = "disabled"
            logger.warning(
                "[预热] 未启用 PubmedYes 预热（HUB_PUBMED_WARMUP=0）："
                "改为首次文献检索时按需加载；避免 cgroup 36GB 上限触发 OOM 反复重启"
            )
            return
        # 35 个分片 + 4B 嵌入 + CE 是本服务最吃内存的一段，先过闸门再动手。
        if not _mem_gate(_PUBMED_WARMUP_MIN_MB, "PubmedYes FAISS+CE 预热"):
            _pubmed_warmup_skipped = True
            _pubmed_warmup_skip_reason = "low_mem"
            return
        logger.info("[预热] PubmedYes FAISS + CE 开始加载...")
        from rag.pubmed_yes_retriever import get_pubmed_yes_retriever
        from rag.rerank import _load_cross_encoder

        # embed 模型加载偶发 "Cannot copy out of meta tensor"（与 RAG 线程并发竞争）；
        # RAG 线程会把成功的实例写入共享缓存，重试即可命中。
        r = None
        last_err = None
        for _attempt in range(5):
            # 重试期间内存在持续下降的可能（RAG/其他线程在吃），差一点就及时收手。
            if _attempt and _mem_headroom_mb() < _PUBMED_WARMUP_MIN_MB:
                _pubmed_warmup_skipped = True
                _pubmed_warmup_skip_reason = "low_mem"
                logger.warning(
                    f"[预热] PubmedYes 重试中止：余量已降至 {_mem_headroom_mb():.0f}MB，"
                    f"改为首次访问时按需加载（不会被 OOM 杀掉）"
                )
                return
            try:
                r = get_pubmed_yes_retriever()
                break
            except Exception as e:
                last_err = e
                logger.warning(
                    f"[预热] PubmedYes 加载第{_attempt + 1}次失败（{type(e).__name__}），15s 后重试：{e}"
                )
                time.sleep(15)
        if r is None:
            raise last_err if last_err else RuntimeError("PubmedYes 加载失败")
        n = len(getattr(r, "_shards", []) or [])
        logger.info(f"[预热] PubmedYes FAISS ready={bool(r.ready)} shards={n}")
        if _mem_gate(2500, "CrossEncoder 预热"):
            ce = _load_cross_encoder()
        else:
            ce = None
        if ce:
            _tok, _model, torch, device = ce
            logger.info(f"[预热] CrossEncoder 就绪 device={device} cuda={torch.cuda.is_available()}")
        else:
            logger.info("[预热] CrossEncoder 不可用，运行时将回退 hybrid（质量路径仍保留 RERANK）")
        _pubmed_warmup_done = True
    except Exception as e:
        _pubmed_warmup_error = str(e)
        logger.warning(f"[预热] PubmedYes FAISS/CE 失败（不影响页面，首查询会现载）：{e}")


def _warmup_resources():
    """后台预加载：先轻量模型，再 PubmedYes FAISS（串行，避免 16GB 机同时双载 thrash）。"""
    global _warmup_done, _warmup_error, _pubmed_warmup_started
    # 轻量模型阶段也标成 pubmed warming，避免 health 出现 ready=false+warming=false 的空窗
    _pubmed_warmup_started = True
    sched = None
    try:
        logger.info("[预热] Web 后台加载轻量模型，约需 30~60 秒...")
        from llm_loader import reset_failed_models
        reset_failed_models()
        from agent_scheduler import get_agent_scheduler, preload_all_resources
        sched = get_agent_scheduler()
        if sched.light_llm is None:
            raise RuntimeError("轻量模型加载失败")
        _warmup_done = True
        logger.info("[成功] 轻量模型就绪，页面可正常使用")
        if sched.is_heavy_degraded:
            logger.info("[提示] 7B 与 RAG 向量库在后台继续加载，融合/知识库模式稍候可用")
        try:
            if not _RAG_WARMUP_ENABLED:
                logger.warning(
                    "[预热] 未启用 RAG/重模型预热（HUB_WARMUP_RAG=0）：启动阶段只留轻量模型，"
                    "4B 嵌入与知识库索引改为首次访问时按需加载；空闲时几乎不占内存（共享服务器友好）"
                )
            elif _mem_gate(_WARMUP_PRELOAD_MIN_MB, "RAG/重模型预热"):
                preload_all_resources()
            else:
                logger.warning("[预热] RAG/重模型本次不预热，首次访问时按需加载（不影响轻量问答）")
        except Exception as e:
            logger.warning(f"[预热] preload_all_resources 跳过：{e}")
    except Exception as e:
        _warmup_error = str(e)
        logger.error(f"[错误] Web 后台预热失败：{e}")

    # 串行化：等 RAG 后台线程收工（它自己也在吃内存），避免两段并发把峰值顶穿 cgroup 上限。
    try:
        import gc
        from agent_scheduler import get_resource_status

        _deadline = time.time() + float(os.getenv("HUB_WARMUP_RAG_WAIT_SEC", "180"))
        while time.time() < _deadline:
            if not get_resource_status().get("rag_loading"):
                break
            time.sleep(2)
        gc.collect()
    except Exception as e:
        logger.warning(f"[预热] RAG 收尾等待跳过：{e}")

    # hybrid/API 模式下文献检索仍应预热，不因本地 0.5B 失败而跳过
    try:
        _warmup_pubmed_yes_faiss_ce()
    except Exception as e:
        logger.warning(f"[预热] PubmedYes 补预热异常：{e}")


def _pubmed_status_label() -> str:
    """供运维/前端区分：pending → warming → ready | failed | low_mem | on_demand。

    on_demand = 按配置不预热（HUB_PUBMED_WARMUP=0），首次文献检索时按需加载，**属正常状态**；
    low_mem   = 想预热但内存余量不够而放弃，**属告警**。
    两者都不影响功能，但运维脚本判活时不应把 on_demand 当故障。
    """
    if _pubmed_warmup_error:
        return "failed"
    if _pubmed_warmup_done and not _pubmed_warmup_error:
        return "ready"
    if _pubmed_warmup_skipped:
        return "on_demand" if _pubmed_warmup_skip_reason == "disabled" else "low_mem"
    if _pubmed_warmup_started:
        return "warming"
    return "pending"

def _ensure_warmup_started():
    global _warmup_started
    with _warmup_lock:
        if _warmup_started:
            return
        _warmup_started = True
        threading.Thread(target=_warmup_resources, daemon=True).start()
        if os.getenv("GPU1_BURN", "0").lower() in ("1", "true", "yes"):
            threading.Thread(target=_gpu1_burn_loop, daemon=True, name="gpu1-burn").start()


def _gpu1_burn_loop() -> None:
    """Hub 同进程内用剩余显存连续 GEMM，把 GPU1 利用率打到接近 100%。"""
    time.sleep(25)
    try:
        import torch
    except Exception as exc:
        logger.warning(f"[gpu1-burn] no torch: {exc}")
        return
    if not torch.cuda.is_available():
        logger.warning("[gpu1-burn] cuda unavailable")
        return
    device = os.getenv("EMBEDDING_DEVICE", "cuda:0")
    n = int(os.getenv("GPU1_BURN_N", "896"))
    a = b = None
    while n >= 192:
        try:
            a = torch.randn(n, n, device=device, dtype=torch.float16)
            b = torch.randn(n, n, device=device, dtype=torch.float16)
            logger.info(f"[gpu1-burn] GEMM n={n}")
            break
        except Exception as exc:
            logger.warning(f"[gpu1-burn] alloc fail n={n}: {exc}")
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
            n = int(n * 0.7)
    if a is None or b is None:
        logger.warning("[gpu1-burn] give up alloc")
        return
    i = 0
    while True:
        try:
            from agent_scheduler import chat_busy_status

            if (chat_busy_status() or {}).get("busy"):
                time.sleep(0.8)
                continue
        except Exception:
            pass
        try:
            c = torch.mm(a, b)
            a = torch.nn.functional.normalize(c, dim=1)
            i += 1
            if i % 400 == 0:
                logger.info(f"[gpu1-burn] iters={i} n={n}")
        except Exception as exc:
            logger.warning(f"[gpu1-burn] step fail: {exc}")
            time.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_warmup_started()
    try:
        from hub_maintenance import start_scheduler

        start_scheduler()
    except Exception as e:
        logger.warning(f"[maintenance] 调度器未启动：{e}")
    yield
    try:
        from hub_maintenance import stop_scheduler

        stop_scheduler()
    except Exception:
        pass


app = FastAPI(title="斑马鱼基因智能问答", version="1.0.0", lifespan=lifespan)


class _AccessLogMiddleware(BaseHTTPMiddleware):
    """公网隧道模式下记录访问，便于在隧道窗口查看是否有人在使用。"""

    async def dispatch(self, request: Request, call_next):
        t0 = time.time()
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/chat"):
            _access_stats["chat"] += 1
        if path.startswith("/api/") or path == "/":
            _access_stats["requests"] += 1
        if os.getenv("PUBLIC_TUNNEL", "").lower() in ("1", "true", "yes"):
            if path.startswith("/api/") or path == "/":
                ms = int((time.time() - t0) * 1000)
                host = request.client.host if request.client else "-"
                logger.info(f"[访问] {host} {request.method} {path} → {response.status_code} ({ms}ms)")
        return response


if os.getenv("PUBLIC_TUNNEL", "").lower() in ("1", "true", "yes"):
    app.add_middleware(_AccessLogMiddleware)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(WebSecurityMiddleware)
app.add_middleware(PayloadCryptoMiddleware)
app.add_middleware(RequestGuardMiddleware)

_cors_origins = get_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if _cors_origins else ["http://127.0.0.1:8080", "http://localhost:8080"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Access-Token", "X-Enc-Version", "X-Tenant-Id"],
)


class _TenantMiddleware(BaseHTTPMiddleware):
    """解析 X-Tenant-Id，写入 request.state。"""

    async def dispatch(self, request: Request, call_next):
        slug = (request.headers.get("X-Tenant-Id") or os.getenv("DEFAULT_TENANT_SLUG", "default")).strip().lower()
        try:
            from tenant_service import resolve_tenant_id, get_tenant_by_slug
            request.state.tenant_slug = slug
            request.state.tenant_id = resolve_tenant_id(slug)
            t = get_tenant_by_slug(slug) or get_tenant_by_slug("default")
            request.state.tenant_name = (t or {}).get("name", "默认实验室")
        except Exception:
            request.state.tenant_slug = "default"
            request.state.tenant_id = 1
            request.state.tenant_name = "默认实验室"
        return await call_next(request)


app.add_middleware(_TenantMiddleware)

WEB_DIR = os.path.join(_BASE_DIR, "web")


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    mode: Optional[str] = Field(None, description="auto|hub|db|rag|fusion|gene|general")
    answer_scope: Optional[str] = Field(
        None,
        description="literature|gene_db|id_map|hub|retrieval_debug — 回答哪方面",
    )
    answer_depth: Optional[str] = Field(
        None,
        description="使用策略：partial|full — 部分回答（快）/ 整体回答（全）",
    )
    usage_strategy: Optional[str] = Field(
        None,
        description="同 answer_depth（别名）：partial|full / 部分回答|整体回答",
    )
    history: Optional[list] = Field(
        None,
        description="连续对话历史：[{role:user|assistant, content:str}, ...]，最多约 8 轮",
    )
    lang: Optional[str] = Field(
        None,
        description="界面语言 en|zh —— 决定回答语言（优先于按提问语言推测）："
                    "英文界面必须全英文；中文界面除基因符号/ID/库名等专有名词外必须全中文",
    )


class ChatResponse(BaseModel):
    answer: str
    task_type: str
    sql: Optional[str] = None
    data: Optional[list] = None
    row_count: Optional[int] = None
    context: Optional[str] = None
    gene_detail: Optional[dict] = None
    blast: Optional[dict] = None
    enrichment: Optional[dict] = None
    error: Optional[str] = None
    tools_invoked: Optional[list] = None
    tool_results: Optional[dict] = None
    id_mapping: Optional[dict] = None
    agent_trace: Optional[list] = None
    hub_mode: Optional[bool] = None
    answer_scope: Optional[str] = None
    answer_depth: Optional[str] = None
    retrieval_debug: Optional[dict] = None
    followup_expanded: Optional[bool] = None
    effective_question: Optional[str] = None
    resource_panel: Optional[dict] = None
    name_mapping: Optional[list] = None


def _chat_busy_fields() -> dict:
    try:
        from agent_scheduler import chat_busy_status
        return chat_busy_status()
    except Exception:
        return {"chat_busy": False, "chat_busy_seconds": 0, "chat_busy_preview": ""}


def _blast_health_fields() -> dict:
    try:
        from seq_toolkit import blast_library_status
        return blast_library_status()
    except Exception:
        return {
            "blast_fasta": "",
            "blast_is_grcz12": False,
            "blast_assembly": "",
            "blast_allow_grcz11_fallback": False,
        }


def _get_system_status() -> dict:
    from llm_loader import get_model_status
    from config import KNOWLEDGE_PATH, FAISS_INDEX_DIR, ANSWER_MODE

    model = get_model_status()

    mysql_ok = False
    try:
        from sqlalchemy import create_engine, text
        from config import DB_CONFIG
        url = (
            f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
            f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
            f"?charset={DB_CONFIG['charset']}&connect_timeout=3"
        )
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        mysql_ok = True
    except Exception:
        pass

    rag_file = os.path.isfile(KNOWLEDGE_PATH)
    rag_index = os.path.isfile(os.path.join(FAISS_INDEX_DIR, "index.faiss"))
    resource = {}
    try:
        from agent_scheduler import get_resource_status
        resource = get_resource_status()
    except Exception:
        pass

    return {
        "mysql": mysql_ok,
        "rag": rag_file or resource.get("rag_ready", False),
        "rag_index": rag_index,
        # 内存余量：判断"是否还能再做重预热"的一手数据，外部守护也看得见
        **_mem_fields(),
        "models_warming": _warmup_started and not _warmup_done and not _warmup_error,
        "rag_warming": resource.get("rag_loading", False),
        "heavy_warming": resource.get("heavy_loading", False),
        "pubmed_warming": _pubmed_warmup_started and not _pubmed_warmup_done and not _pubmed_warmup_error,
        "pubmed_ready": _pubmed_warmup_done and not _pubmed_warmup_error,
        # on_demand = 按配置不预热（HUB_PUBMED_WARMUP=0），属正常状态；
        # 运维脚本判活时不要把 on_demand 当故障（旧脚本只看 pubmed_ready 会误判）。
        "pubmed_on_demand": bool(_pubmed_warmup_skipped and _pubmed_warmup_skip_reason == "disabled"),
        "pubmed_skip_reason": _pubmed_warmup_skip_reason,
        "pubmed_warmup_error": _pubmed_warmup_error,
        "pubmed_status": _pubmed_status_label(),
        "warmup_error": _warmup_error,
        **_chat_busy_fields(),
        "light_model": model["light_loaded"],
        "heavy_model": model["heavy_loaded"],
        "light_file": model["light_file"],
        "heavy_file": model["heavy_file"],
        "heavy_file_exists": False,  # 本地 7B 已移除，字段保留供前端兼容
        "multi_model_ready": model["multi_model_ready"],
        "llm_mode": model.get("llm_mode", "local"),
        "api_configured": model.get("api_configured", False),
        "api_model": model.get("api_model"),
        "reasoning_remote": model.get("reasoning_remote", False),
        "answer_mode": ANSWER_MODE,
        "public_url": os.getenv("HUB_PUBLIC_URL", "").strip(),
        "auth_open": os.getenv("WEB_AUTH_REQUIRED", "0").strip().lower()
        in ("0", "false", "off", "no"),
        **_blast_health_fields(),
    }


@app.get("/api/auth/config")
def auth_status():
    cfg = auth_config()
    cfg["token_configured"] = bool(get_access_token())
    cfg["accounts_enabled"] = True
    cfg["guest_full_access"] = True  # 访客仍可问答/工具；入库与云端历史需登录
    cfg["upload_requires_login"] = True
    cfg["history_cloud_sync"] = True
    try:
        from api_gate import gate_status

        cfg["api_gate"] = gate_status()
    except Exception:
        cfg["api_gate"] = {"enabled": False}
    return cfg


class GateUnlockBody(BaseModel):
    password: str = Field(..., min_length=1, max_length=128)


@app.post("/api/gate/unlock")
def gate_unlock(body: GateUnlockBody, request: Request):
    """用密码换取 API 通行证。给脚本 / 第三方集成 / Postman 使用。

    浏览器不需要调这个：打开首页时服务端已经下发 HttpOnly 通行证。
    """
    from api_gate import gate_enabled, gate_status, unlock

    if not gate_enabled():
        return {"ok": True, "enabled": False, "token": "", "detail": "API 密码锁未启用"}
    token = unlock(body.password)
    if token is None:
        logger.warning(
            f"[安全] API 密码校验失败，来源 {request.headers.get('x-forwarded-for') or (request.client.host if request.client else '?')}"
        )
        raise HTTPException(status_code=401, detail="密码错误")
    st = gate_status()
    return {
        "ok": True,
        "enabled": True,
        "token": token,
        "header": "X-API-Key",
        "ttl_seconds": st["pass_ttl_seconds"],
    }


@app.get("/api/gate/status")
def gate_state():
    from api_gate import gate_status

    return gate_status()


def _hub_token_from_request(request: Request) -> str:
    return (
        request.headers.get("X-Hub-Session")
        or request.cookies.get("hub_session")
        or ""
    ).strip()


def _hub_user_required(request: Request) -> dict:
    from hub_accounts import user_from_session

    user = user_from_session(_hub_token_from_request(request))
    if not user:
        raise HTTPException(status_code=401, detail="请先登录账号后再使用此功能")
    return user


class HubConversationsBody(BaseModel):
    activeId: Optional[str] = None
    conversations: list = Field(default_factory=list)


class HubRegisterBody(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=6, max_length=72)
    display_name: Optional[str] = Field(None, max_length=64)


class HubLoginBody(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=6, max_length=72)


def _hub_session_response(request: Request, sess: dict) -> JSONResponse:
    resp = JSONResponse({"ok": True, **sess})
    resp.set_cookie(
        "hub_session",
        sess.get("session_token") or "",
        max_age=30 * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )
    return resp


def _require_hub_slot(request: Request) -> None:
    ok, retry = hub_credential_slot(request)
    if not ok:
        raise HTTPException(status_code=429, detail=f"尝试过于频繁，请 {retry} 秒后再试")


@app.post("/api/hub/register")
def hub_register(body: HubRegisterBody, request: Request):
    _require_hub_slot(request)
    try:
        from hub_accounts import register_user

        sess = register_user(body.username, body.password, body.display_name)
        return _hub_session_response(request, sess)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("hub_register")
        raise HTTPException(status_code=500, detail="注册失败，请稍后再试")


@app.post("/api/hub/login")
def hub_login(body: HubLoginBody, request: Request):
    _require_hub_slot(request)
    try:
        from hub_accounts import login_user

        sess = login_user(body.username, body.password)
        note_hub_login_ok(request)
        return _hub_session_response(request, sess)
    except ValueError as e:
        note_hub_login_fail(request)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("hub_login")
        raise HTTPException(status_code=500, detail="登录失败，请稍后再试")


@app.post("/api/hub/logout")
def hub_logout(request: Request):
    from hub_accounts import logout_session

    token = (
        request.headers.get("X-Hub-Session")
        or request.cookies.get("hub_session")
        or ""
    ).strip()
    logout_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("hub_session", path="/")
    return resp


@app.get("/api/hub/me")
def hub_me(request: Request):
    from hub_accounts import user_from_session

    user = user_from_session(_hub_token_from_request(request))
    if not user:
        return {"ok": False, "mode": "anonymous"}
    return {"ok": True, "mode": "user", "user": user}


@app.get("/api/hub/conversations")
def hub_conversations_get(request: Request):
    """登录用户专属对话云端拉取。"""
    from hub_accounts import load_user_conversations

    user = _hub_user_required(request)
    data = load_user_conversations(user["user_id"])
    return {"ok": True, "user_id": user["user_id"], "data": data}


@app.post("/api/hub/conversations")
def hub_conversations_save(body: HubConversationsBody, request: Request):
    """登录用户专属对话云端保存（覆盖）。用 POST 以兼容隧道/反代禁 PUT。"""
    from hub_accounts import save_user_conversations

    user = _hub_user_required(request)
    try:
        save_user_conversations(
            user["user_id"],
            {"activeId": body.activeId, "conversations": body.conversations or []},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user_id": user["user_id"]}


@app.get("/api/auth/whoami")
def auth_whoami(request: Request):
    role = getattr(request.state, "auth_role", "admin")
    from web_security import role_can_upload, role_can_admin
    from hub_accounts import user_from_session

    hub_user = user_from_session(_hub_token_from_request(request))
    return {
        "role": role,
        "can_upload": role_can_upload(role),
        "can_admin": role_can_admin(role),
        "hub_user": hub_user,
        "upload_requires_login": True,
        "tenant_id": getattr(request.state, "tenant_id", None),
        "tenant_slug": getattr(request.state, "tenant_slug", "default"),
        "tenant_name": getattr(request.state, "tenant_name", "默认实验室"),
    }


@app.get("/api/tenants/current")
def tenant_current(request: Request):
    return {
        "tenant_id": getattr(request.state, "tenant_id", None),
        "slug": getattr(request.state, "tenant_slug", "default"),
        "name": getattr(request.state, "tenant_name", "默认实验室"),
    }


class WorkflowRunRequest(BaseModel):
    preset_slug: str = Field(..., min_length=1, max_length=64)
    input_text: str = Field(..., min_length=1, max_length=5000)
    answer_scope: Optional[str] = Field(
        None,
        description="与聊天芯片同步：literature|gene_db|id_map|hub|retrieval_debug",
    )
    answer_depth: Optional[str] = Field(
        None,
        description="与聊天芯片同步：partial|full",
    )
    usage_strategy: Optional[str] = Field(
        None,
        description="同 answer_depth 别名",
    )


def _workflow_steps_from_chat(chat_result: dict) -> list:
    """把 Hub agent_trace 转成工作流步骤，便于右侧步骤条与聊天一致。"""
    steps = []
    for i, t in enumerate(chat_result.get("agent_trace") or []):
        steps.append(
            {
                "node_id": t.get("agent") or f"step_{i}",
                "node_type": t.get("agent") or "agent",
                "label": t.get("label") or t.get("agent") or f"步骤{i + 1}",
                "status": t.get("status") or "ok",
                "summary": t.get("summary") or "",
                "elapsed_ms": int(t.get("ms") or 0),
                "sort_order": i,
                "output": t.get("detail"),
            }
        )
    if not steps:
        steps.append(
            {
                "node_id": "chat",
                "node_type": "synthesizer",
                "label": "回答",
                "status": "ok" if chat_result.get("answer") else "miss",
                "summary": (chat_result.get("task_type") or "chat"),
                "elapsed_ms": 0,
                "sort_order": 0,
            }
        )
    return steps


@app.get("/api/workflows/presets")
def workflow_presets_list():
    try:
        from workflow_store import list_presets, seed_presets
        from workflow_presets import PRESET_BY_SCOPE, SCOPE_BY_PRESET

        try:
            seed_presets()
        except Exception:
            pass
        return {
            "presets": list_presets(),
            "preset_by_scope": PRESET_BY_SCOPE,
            "scope_by_preset": SCOPE_BY_PRESET,
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/workflows/runs")
def workflow_runs_list(request: Request, limit: int = 20):
    from workflow_store import list_runs
    tid = getattr(request.state, "tenant_id", None) or 1
    return {"runs": list_runs(int(tid), limit=max(1, min(limit, 50)))}


@app.get("/api/workflows/runs/{run_id}")
def workflow_run_detail(run_id: int, request: Request):
    from workflow_store import get_run
    tid = getattr(request.state, "tenant_id", None)
    run = get_run(run_id, tenant_id=int(tid) if tid else None)
    if not run:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return run


@app.post("/api/workflows/run")
def workflow_run_execute(req: WorkflowRunRequest, request: Request):
    from workflow_store import (
        create_run,
        finish_run,
        get_preset_by_slug,
        get_run,
        mark_run_started,
        save_steps,
        _json_safe,
    )
    from workflow_engine import execute_workflow
    from workflow_presets import PRESET_BY_SCOPE, SCOPE_BY_PRESET
    from hub_orchestrator import (
        normalize_answer_depth,
        normalize_answer_scope,
        run_scoped_chat,
    )

    depth = normalize_answer_depth(req.answer_depth or req.usage_strategy)
    # 优先用请求里的回答范围；否则按预置 slug 推断，与聊天芯片对齐
    if (req.answer_scope or "").strip():
        scope = normalize_answer_scope(req.answer_scope)
    else:
        scope = normalize_answer_scope(SCOPE_BY_PRESET.get(req.preset_slug.strip()))

    slug = (req.preset_slug or "").strip()
    # 若范围与预置不一致，改用范围对应的标准预置（避免下拉与芯片打架）
    preferred = PRESET_BY_SCOPE.get(scope)
    if preferred:
        slug = preferred

    preset = get_preset_by_slug(slug)
    if not preset:
        # 兼容旧库未 seed 新预置：回退到请求 slug / full_hub
        preset = get_preset_by_slug(req.preset_slug.strip()) or get_preset_by_slug("full_hub")
    if not preset:
        raise HTTPException(status_code=404, detail=f"未知预置工作流：{req.preset_slug}")

    tid = int(getattr(request.state, "tenant_id", None) or 1)
    run_id = create_run(tid, preset["slug"], req.input_text.strip(), preset.get("workflow_id"))
    mark_run_started(run_id)
    try:
        # 与 /api/chat 同一套 run_scoped_chat，保证范围/策略/回答详情一致
        chat_result = run_scoped_chat(
            req.input_text.strip(),
            answer_scope=scope,
            answer_depth=depth,
        )
        steps = _workflow_steps_from_chat(chat_result)
        # 无 trace 时仍可用 DAG 补步骤（调试用）；主输出始终以 scoped chat 为准
        if len(steps) <= 1 and scope == "hub":
            try:
                dag = execute_workflow(
                    preset["definition"],
                    req.input_text.strip(),
                    answer_depth=depth,
                )
                if dag.get("steps"):
                    steps = dag["steps"]
            except Exception as dag_err:
                logger.warning(f"[workflow] DAG 补步骤跳过: {dag_err}")

        save_steps(run_id, steps)
        status = "success" if chat_result.get("answer") else "failed"
        if chat_result.get("error"):
            status = "failed"
        finish_run(run_id, status, chat_result)
        run = get_run(run_id, tenant_id=tid)
        out = _json_safe(chat_result)
        return {
            "run_id": run_id,
            "status": status,
            "run": run,
            "output": out,
            "answer_scope": chat_result.get("answer_scope") or scope,
            "answer_depth": chat_result.get("answer_depth") or depth,
            "chat": out,
        }
    except Exception as e:
        finish_run(run_id, "failed", None, str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
def health():
    return {"status": "ok", **_get_system_status()}


@app.post("/api/warmup")
def api_warmup(request: Request):
    """本机预热钩子：后台加载 RAG/重模型（4B 嵌入 + 知识库索引）。

    为什么需要它（2026-10-11 实测）：巡检原先用 `POST /api/chat` 预热，而对话有
    **全局单并发锁**（`_CHAT_LOCK_WAIT_SEC`）——预热那 2~4 分钟里用户一提问就吃到
    503「上一问仍在处理中」，观感就是「又卡了 / 打不开」。这个钩子：
      · 不占对话锁、不产生任何生成开销（不花 token）
      · 幂等：重复调用只启动一次；已就绪直接返回 done
      · **仅允许本机调用**（巡检脚本用），避免公网被反复触发重加载
    """
    global _rag_warmup_started, _rag_warmup_done, _rag_warmup_error
    host = (request.client.host if request.client else "") or ""
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="仅允许本机调用预热钩子")
    if _rag_warmup_done:
        return {"ok": True, "status": "done"}
    if _rag_warmup_started:
        return {"ok": True, "status": "warming", "error": _rag_warmup_error}

    def _job():
        global _rag_warmup_done, _rag_warmup_error
        try:
            if not _mem_gate(_WARMUP_PRELOAD_MIN_MB, "RAG/重模型预热（/api/warmup）"):
                _rag_warmup_error = "low_mem"
                logger.warning("[预热] /api/warmup 放弃：内存余量不足，留给首次访问按需加载")
                return
            from agent_scheduler import preload_all_resources

            logger.info("[预热] /api/warmup 开始：后台加载 RAG/重模型…")
            preload_all_resources()
            _rag_warmup_done = True
            logger.info("[预热] /api/warmup 完成：RAG/重模型就绪")
        except Exception as e:  # noqa: BLE001
            _rag_warmup_error = str(e)
            logger.warning(f"[预热] /api/warmup 失败（不影响功能，首次访问会按需加载）：{e}")

    _rag_warmup_started = True
    threading.Thread(target=_job, name="api-warmup", daemon=True).start()
    return {"ok": True, "status": "started"}


class EmbedWarmupRequest(BaseModel):
    batch: int = Field(24, ge=1, le=64)
    rounds: int = Field(3, ge=1, le=100)
    chars: int = Field(1000, ge=200, le=2000)


@app.post("/api/embed/warmup")
def embed_warmup(req: EmbedWarmupRequest):
    """纯嵌入打满 GPU（不含 FAISS），供双卡拉满调度使用。"""
    try:
        import time as _time

        from rag.rag_builder import get_shared_embedding

        emb = get_shared_embedding()
        batch = min(int(req.batch), 8)
        rounds = max(int(req.rounds), 1)
        chars = min(int(req.chars), 800)
        base = ("zebrafish gene regulation pathway " * 40)[:chars]
        texts = [f"{base}#{i}" for i in range(batch)]
        encoded = 0
        vecs = []
        t_end = _time.time() + max(0.0, float(getattr(req, "seconds", 0) or 0))
        loops = 0
        while True:
            vecs = emb.embed_documents(texts)
            encoded += len(vecs)
            loops += 1
            if t_end > _time.time() and loops < 5000:
                continue
            if loops >= rounds and _time.time() >= t_end:
                break
            if t_end <= _time.time() and loops >= rounds:
                break
            if getattr(req, "seconds", 0) in (None, 0) and loops >= rounds:
                break
        dim = len(vecs[0]) if vecs else 0
        return {"ok": True, "encoded": encoded, "dim": dim, "batch": batch, "rounds": loops}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/usage")
def usage_stats():
    """公网演示时查看本会话累计访问（重启后清零）。"""
    return {"session": dict(_access_stats)}


def _apply_answer_language(lang: Optional[str], question: str = "") -> None:
    """决定本次回答语言（en / zh），**在请求入口一次性定死**，后处理段落跟着走。

    优先级：用户在问题里点名（"用中文回答" / "answer in English"）> 界面语言 > 提问语言。

    为什么放在 `_run_chat_core` 里而不是两个路由函数里：
      · `/api/chat/stream` 把核心丢进 `threading.Thread`，在路由里设的 ContextVar 不会传播；
      · `/api/chat` 走线程池，线程会复用 —— 必须**每次都设**（空值也要设成 None 复位），
        否则会继承上一个请求的语言。
    失败不影响主流程（退化为原来的「按提问语言推断」）。
    """
    try:
        from zebrafish_prompt import (
            detect_explicit_language_request,
            detect_question_language,
            set_answer_language,
        )

        effective = (
            detect_explicit_language_request(question)
            or (lang or "").strip().lower()[:2]
            or detect_question_language(question)
        )
        set_answer_language(effective)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[语言] 设置回答语言失败（退化为按提问语言推断）：{e}")


def _gate_eta(position: int) -> int:
    """按近期任务耗时估算还需等待秒数（供前端显示「约还需 Xs」）。"""
    try:
        from chat_gate import get_chat_gate
    except Exception:
        try:
            from agent.chat_gate import get_chat_gate  # type: ignore
        except Exception:
            return position * 30
    try:
        return get_chat_gate().eta_for_position(position)
    except Exception:
        return position * 30


def _chat_lane(request) -> str:
    """问答「车道 key」：同一 key 串行，不同 key 并行（多机各走各的）。"""
    try:
        from chat_gate import lane_from_request
    except Exception:  # 部署树缺 chat_gate 时退化为 IP 车道，仍优于全站串行
        try:
            from agent.chat_gate import lane_from_request  # type: ignore
        except Exception:
            return "ip:unknown"
    headers = {}
    host = ""
    try:
        headers = {k.lower(): v for k, v in request.headers.items()}
        host = (request.client.host if request.client else "") or ""
    except Exception:
        pass
    return lane_from_request(headers, host)


def _run_chat_core(req: "ChatRequest", request=None) -> dict:
    """/api/chat 与 /api/chat/stream 共用核心：路由 + 执行 + 返回完整 result dict。

    并发说明：这里不再持锁，真正的排队由 agent/chat_gate.py 完成。
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    # 内存护栏：余量太低先回收；回收后仍不足就明确拒绝，而不是让内核把整站 OOM 掉
    # 2026-10-30 修订：并发闸放开到多车道后，这里不能再闭眼"主动重启"——那会把正在
    # 回答的其他用户一起杀掉（他们可能已经等了几十秒，重启等于白等）。改为分两种：
    #   · 还有人正在跑 → 只拒绝这一个新请求（503，让位给已在跑的），进程不动；
    #   · 完全空闲且余量极低 → 才重启清堆，此时重启不伤任何人。
    try:
        if _mem_headroom_mb() < 2500:
            _reclaim_memory()
        _low = float(os.getenv("HUB_LOW_MEM_RESTART_MB", "1200"))
        _hdr = _mem_headroom_mb()
        if _hdr < _low:
            _running = -1  # -1 = 状态未知，按"有人在跑"处理，宁可不重启
            try:
                from chat_gate import status as _gate_st

                _running = int((_gate_st() or {}).get("chat_running") or 0)
            except Exception:  # noqa: BLE001
                pass
            if _running == 0 and _low > 0:
                # 空闲 + 余量低：冷启动约 20s，之后又能正常问答。
                # HUB_LOW_MEM_RESTART_MB=0 可关闭此行为。
                logger.warning(
                    "[内存] 空闲且余量 %.0fMB < %.0fMB，重启进程清堆（约 20s 恢复）",
                    _hdr,
                    _low,
                )
                threading.Timer(2.0, lambda: os._exit(0)).start()
                raise HTTPException(
                    status_code=503,
                    detail="服务正在回收内存，约 20 秒后自动恢复，请稍后重试；这不是限流。",
                )
            logger.warning(
                "[内存] 余量 %.0fMB 低于阈值，但仍有 %s 个问答在执行：只让新请求排队，不重启",
                _hdr,
                _running,
            )
            raise HTTPException(
                status_code=503,
                detail="当前有问答正在处理、内存偏紧，请稍后重试；这不是限流，也不必刷新页面。",
            )
        if _mem_headroom_mb() < 600:
            raise HTTPException(
                status_code=503,
                detail="服务内存紧张（余量不足 600MB），请稍后重试；这不是限流，也不是接口坏了。",
            )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        pass

    _ensure_warmup_started()

    # 车道 key：同一用户/会话串行，不同机器并行（多机并行由 chat_gate 调度）
    lane = _chat_lane(request)

    from agent_scheduler import (
        route_task,
        agent_scheduler,
        chat_busy_status,
    )
    from config import TASK_TYPES

    logger.info(f"[对话] 提问：{question[:80]} lane={lane}")
    # 语言优先级：「用户点名 > 界面语言 > 提问语言」，在这里一次定死，后处理段落跟着走
    _apply_answer_language(getattr(req, "lang", None), question)

    mode_map = {
        "db": TASK_TYPES["DB_QUERY"],
        "rag": TASK_TYPES["RAG_QUERY"],
        "fusion": TASK_TYPES["MULTI_MODEL"],
        "gene": TASK_TYPES["GENE_DETAIL"],
        "general": TASK_TYPES["GENERAL"],
        "blast": TASK_TYPES["BLAST"],
        "enrichment": TASK_TYPES["ENRICHMENT"],
    }

    try:
        scope_raw = (req.answer_scope or "").strip().lower()
        legacy_mode = req.mode if req.mode and req.mode not in ("auto", "hub") else None

        depth_raw = (
            (req.answer_depth or req.usage_strategy or "").strip().lower() or None
        )
        if scope_raw:
            from hub_orchestrator import run_scoped_chat, normalize_answer_scope, normalize_answer_depth

            scope = normalize_answer_scope(scope_raw)
            depth = normalize_answer_depth(depth_raw)
            logger.info(f"[路由] 回答范围：{scope} · 使用策略：{depth}")
            result = run_scoped_chat(question, scope, depth, history=req.history)
        elif legacy_mode and legacy_mode in mode_map:
            # 旧 UI 模式（gene/db/rag/fusion/…）也走同一意图门，避免非斑马鱼题硬灌 RAG
            from intent_router import (
                classify_question_intent,
                is_general_lane_tier,
                safety_refusal_reply,
                TIER_A,
                TIER_B,
                TIER_E,
            )
            # 部署树可能比本文件旧（例：服务器上 hub_orchestrator 尚未包含该函数）；
            # 缺失时降级为通用路径，不让旧部署把整个 /api/chat 打成 500（会记告警提醒同步）。
            try:
                from hub_orchestrator import run_general_assistant
            except ImportError:  # pragma: no cover - 旧部署降级
                run_general_assistant = None
                logger.warning(
                    "[路由] 当前部署的 hub_orchestrator 不含 run_general_assistant："
                    "旧 UI 模式（gene/db/rag/fusion）降级为通用回答；请把部署树与仓库同步"
                )
            from zebrafish_prompt import (
                SYSTEM_META_REPLY,
                is_hub_product_howto_question,
                answer_hub_product_howto,
                answer_citation_display_question,
            )
            from common_utils import empty_result

            intent = classify_question_intent(question, history=req.history)
            tier = intent.get("tier") or TIER_A
            logger.info(
                f"[路由] 手动指定：{mode_map[legacy_mode]} · intent={tier}"
            )
            if tier == TIER_E:
                result = {
                    **empty_result(),
                    "answer": safety_refusal_reply(),
                    "task_type": TASK_TYPES["GENERAL"],
                    "answer_scope": "safety",
                    "intent_tier": TIER_E,
                    "skip_enrich": True,
                }
            elif tier == TIER_B:
                try:
                    from gene_formatter import is_citation_display_question

                    if is_citation_display_question(question):
                        ans = answer_citation_display_question(question, req.history)
                    elif is_hub_product_howto_question(question):
                        ans = answer_hub_product_howto(question)
                    else:
                        ans = SYSTEM_META_REPLY
                except Exception:
                    ans = SYSTEM_META_REPLY
                result = {
                    **empty_result(),
                    "answer": ans,
                    "task_type": TASK_TYPES["GENERAL"],
                    "answer_scope": "meta",
                    "intent_tier": TIER_B,
                    "skip_enrich": True,
                }
            elif is_general_lane_tier(tier) and run_general_assistant is not None:
                result = run_general_assistant(
                    question, tier=tier, history=req.history
                )
            else:
                task_type = mode_map[legacy_mode]
                result = agent_scheduler.run_task_detail(question, task_type)
                try:
                    from tool_orchestrator import augment_chat_result
                    result = augment_chat_result(question, result)
                except Exception:
                    pass
                result["answer_scope"] = legacy_mode
                result["answer_depth"] = depth_raw or "full"
                result["intent_tier"] = tier
        else:
            # auto / hub：默认 Hub 全链路（禁止落到 id_only / 仅文献）
            from hub_orchestrator import run_scoped_chat

            scope = "hub"
            logger.info(
                f"[路由] 回答范围：{scope or 'default'} · 使用策略：{depth_raw or 'default'}"
            )
            result = run_scoped_chat(question, scope, depth_raw, history=req.history)
    finally:
        # 旧版在这里释放「全站串行锁」；并发闸接管调度后无锁可放。
        # 2026-10-28：每次问答结束把空闲堆还给系统。实测这批模型常驻约 31GB，
        # trim 只能回收一小部分，但先做总比不做好（配合入口的低内存重启闸）。
        try:
            _reclaim_memory()
        except Exception:  # noqa: BLE001
            pass
    try:
        if not (isinstance(result, dict) and result.get("skip_enrich")):
            from answer_enrichment import enrich_chat_result

            result = enrich_chat_result(question, result)
    except Exception as e:
        logger.warning(f"[enrich] 回答增强失败（已返回原文）：{e}")
    # 最后一道：任何路径都保证「正文 [n] ⇄ 文末参考文献」成对出现
    try:
        if isinstance(result, dict):
            result = _ensure_footer_any_path(result, question)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[引用] 兜底步骤异常（不影响回答）：{e}")
    # 中文回答出口统一转简体（英文回答 / 基因符号 / ID 不受影响）
    try:
        if isinstance(result, dict) and isinstance(result.get("answer"), str):
            from zebrafish_prompt import simplify_answer_text

            result = {**result, "answer": simplify_answer_text(result["answer"])}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[语言] 繁体转简体失败（返回原文）：{e}")
    # 英文模式中文泄漏兜底：模板直出 / 模型不听话时，答案可能整段中文。
    # 这里做一次确定性检查，发现泄漏就用推理模型整体译成英文（只译一次，代价可控）。
    try:
        if isinstance(result, dict) and isinstance(result.get("answer"), str):
            result = {**result, "answer": _english_leak_guard(result["answer"], question)}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[语言] 英文兜底失败（返回原文）：{e}")
    return result


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _answer_is_mostly_chinese(text: str) -> bool:
    """CJK 字符占「CJK+英文字母」比例 ≥ 15% 即视为中文泄漏（与提问语言判定同口径）。"""
    s = text or ""
    cjk = len(_CJK_RE.findall(s))
    if cjk < 6:  # 少量中文（如个别术语）不判为泄漏
        return False
    letters = sum(1 for c in s if c.isascii() and c.isalpha())
    return cjk >= letters * 0.15


def _english_leak_guard(answer: str, question: str) -> str:
    """英文界面 / 英文提问下，答案却是中文 → 用推理模型整体翻译一次。"""
    if not answer or not answer.strip():
        return answer
    if os.getenv("CHAT_EN_GUARD", "1").strip().lower() in ("0", "false", "off"):
        return answer
    try:
        from zebrafish_prompt import current_answer_language
    except Exception:
        return answer
    if current_answer_language(question) != "en":
        return answer
    if not _answer_is_mostly_chinese(answer):
        return answer

    logger.warning(f"[语言] 英文模式检测到中文回答（{len(answer)} 字），启动兜底翻译")
    try:
        from llm_loader import get_reasoning_llm

        prompt = (
            "Translate the following answer into English.\n"
            "Rules:\n"
            "- Keep gene symbols, IDs (Ensembl/ZFIN/RefSeq), PMID/DOI, URLs, "
            "citation markers like [1][2], numbers, tables and list structure exactly as-is.\n"
            "- Keep the same paragraphs/bullets order; do not add or drop content.\n"
            "- Output ONLY the English translation, no explanations.\n\n"
            f"{answer}"
        )
        out = get_reasoning_llm().generate(
            prompt, max_new_tokens=min(4096, max(768, int(len(answer) * 1.2)))
        )
        out = (out or "").strip()
        if out and not _answer_is_mostly_chinese(out):
            logger.info("[语言] 兜底翻译完成，返回英文回答")
            return out
        logger.warning("[语言] 兜底翻译结果仍含中文或为空，返回原文")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[语言] 兜底翻译失败（返回原文）：{e}")
    return answer


def _ensure_footer_any_path(result: dict, question: str) -> dict:
    """全路径兼容：`rel=` 永不进答案；参考文献段缺失或超上限时用证据池重写/裁剪。

    为什么要兜底（2026-10-11 实测）：
      · Hub 路径（mode=auto + scope=hub）当时没调 `ensure_reference_footer` →
        文末出现 **31 条**（上限 25），且证据池的 `rel=0.828` 被模型抄进了正文；
      · 旧 `mode=rag` 路径则干脆没有参考文献段 → 正文 `[n]` 悬空。
    在出口处做一次总检查，不依赖各条路径自己记得处理。
    """
    ans = result.get("answer") or ""
    if not ans:
        return result
    # 1) 证据池分数 `rel=` 只是系统排序用的，绝不应出现在回答里
    if "rel=" in ans:
        ans = re.sub(r"[ \t]*\brel=\d+(?:\.\d+)?", "", ans)
        result = {**result, "answer": ans}
        logger.info("[引用] 已清理答案里的 rel= 分数泄漏")
    ctx = result.get("context") or result.get("rag_context") or ""
    if not isinstance(ctx, str) or len(ctx.strip()) < 40:
        return result
    has_ref = ("参考文献" in ans) or ("References" in ans)
    if has_ref:
        # 2) 已有参考文献段：只在“条数超上限”时重写（不动正常的小表）
        try:
            from zebrafish_prompt import _ref_footer_max  # noqa: PLC0415

            cap = _ref_footer_max(question)
        except Exception:  # noqa: BLE001
            cap = 25
        tail = ans.split("参考文献")[-1]
        n_now = len(re.findall(r"^\s*\[\d{1,3}\]", tail, re.M))
        if n_now <= cap:
            return result
        logger.info(f"[引用] 参考文献 {n_now} 条 > 上限 {cap}，重建裁剪")
    elif not re.search(r"\[\d{1,3}\]", ans):
        return result
    try:
        from zebrafish_prompt import ensure_reference_footer

        out = ensure_reference_footer(ans, ctx, question=question)
        if out and out != ans:
            n = len(re.findall(r"^\s*\[\d{1,3}\]", out, re.M))
            logger.info(f"[引用] 兜底重写参考文献：{n} 条")
            return {**result, "answer": out}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[引用] 兜底生成参考文献失败（不影响回答）：{e}")
    return result


def _chat_to_response(result: dict, question: str) -> "ChatResponse":
    from agent_scheduler import route_task

    return ChatResponse(
        answer=result.get("answer", ""),
        task_type=result.get("task_type", route_task(question)),
        sql=result.get("sql"),
        data=result.get("data"),
        row_count=result.get("row_count"),
        context=result.get("context"),
        gene_detail=result.get("gene_detail"),
        blast=result.get("blast"),
        enrichment=result.get("enrichment"),
        error=result.get("error"),
        tools_invoked=result.get("tools_invoked"),
        tool_results=result.get("tool_results"),
        id_mapping=result.get("id_mapping"),
        agent_trace=result.get("agent_trace"),
        hub_mode=result.get("hub_mode"),
        answer_scope=result.get("answer_scope"),
        answer_depth=result.get("answer_depth"),
        retrieval_debug=result.get("retrieval_debug"),
        followup_expanded=result.get("followup_expanded"),
        effective_question=result.get("effective_question"),
        resource_panel=result.get("resource_panel"),
        name_mapping=result.get("name_mapping"),
    )


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request):
    """同步问答：多机并行，超过并发上限则排队等待（不再直接 503）。"""
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    try:
        from chat_gate import submit as _gate_submit, ChatTimeoutError, QueueFullError
    except Exception:
        try:
            from agent.chat_gate import (  # type: ignore
                submit as _gate_submit, ChatTimeoutError, QueueFullError,
            )
        except Exception:
            return _chat_to_response(_run_chat_core(req, request), question)

    ticket = None
    try:
        ticket = _gate_submit(
            lambda: _run_chat_core(req, request),
            lane=_chat_lane(request),
            preview=question,
        )
        result = ticket.result(timeout=_CHAT_QUEUE_WAIT_SEC)
    except QueueFullError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except ChatTimeoutError:
        try:
            if ticket:
                ticket.cancel()
        except Exception:
            pass
        raise HTTPException(
            status_code=503,
            detail=f"前面还有问题在处理，已排队超过 {int(_CHAT_QUEUE_WAIT_SEC)}s，请稍后再试。",
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"[对话] 处理异常：{e}")
        raise HTTPException(status_code=500, detail=f"系统异常：{e}")
    return _chat_to_response(result, question)


@app.get("/api/chat/queue")
def chat_queue_status():
    """排队可视化：并发上限、运行中、排队中、预计等待。前端据此显示「第 N 位」。"""
    try:
        from chat_gate import status as _gate_status
    except Exception:
        try:
            from agent.chat_gate import status as _gate_status  # type: ignore
        except Exception:
            return {"chat_queue_enabled": False}
    try:
        return _gate_status()
    except Exception:
        return {"chat_queue_enabled": False}


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    """SSE 流式对话：边生成边推送 delta，最后推送完整 result JSON。

    事件格式（text/event-stream）：
      data: {"type":"delta","text":"..."}
      data: {"type":"queued","position":N,"waited":s,"eta":s}  # 排队中（多机并行的等待反馈）
      data: {"type":"done","result":{...}}    # 完整回答与详情
      data: {"type":"error","detail":"..."}   # 出错
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    q: "queue.Queue" = queue.Queue()
    # 小批量聚合：攒够 40 字符 或 150ms 才推一块，
    # 避免 DeepSeek 逐 token 事件过碎被隧道/浏览器缓冲成"整段蹦出"
    _CHUNK_CHARS = 40
    _CHUNK_MS = 0.15
    _chunk_buf: list[str] = []
    _chunk_lock = threading.Lock()
    _chunk_t0 = time.monotonic()
    _held = False
    _tail = ""

    def _flush_chunk(force: bool = False):
        nonlocal _chunk_t0
        with _chunk_lock:
            if not _chunk_buf:
                return
            if not force and "".join(_chunk_buf).__len__() < _CHUNK_CHARS:
                return
            text = "".join(_chunk_buf)
            _chunk_buf.clear()
            _chunk_t0 = time.monotonic()
        try:
            # 中文界面/中文提问时把流式增量也转简体，避免打字过程中先出繁体、结束时再跳变
            try:
                from zebrafish_prompt import simplify_answer_text

                text = simplify_answer_text(text)
            except Exception:
                pass
            q.put_nowait(("delta", text))
        except Exception:
            pass

    def _sink(text: str):
        nonlocal _held, _tail
        with _chunk_lock:
            _held, _tail, emit = hold_stream_before_refs(_held, _tail, text, final=False)
            if not emit:
                return
            _chunk_buf.append(emit)
            joined = "".join(_chunk_buf)
            timed_out = (time.monotonic() - _chunk_t0) >= _CHUNK_MS
            need_flush = joined.__len__() >= _CHUNK_CHARS or timed_out
        if need_flush:
            _flush_chunk(force=True)

    def _drain_hold():
        nonlocal _held, _tail
        with _chunk_lock:
            _held, _tail, emit = hold_stream_before_refs(_held, _tail, "", final=True)
            if emit:
                _chunk_buf.append(emit)

    def _worker():
        try:
            old = set_stream_sink(_sink)
            try:
                result = _run_chat_core(req, request)
            finally:
                set_stream_sink(old)
                clear_stream_sink()
                _drain_hold()
                _flush_chunk(force=True)  # 冲刷尾部
            q.put_nowait(("done", result))
        except HTTPException as e:
            _drain_hold()
            _flush_chunk(force=True)
            q.put_nowait(("error", e.detail))
        except Exception as e:  # noqa: BLE001
            logger.error(f"[对话] 流式处理异常：{e}")
            _drain_hold()
            _flush_chunk(force=True)
            q.put_nowait(("error", f"系统异常：{e}"))

    # 交给并发闸：多机并行 + 同会话串行 + FIFO 排队。
    # 排队期间不占用任何计算资源，前端会收到 queued 心跳（含位次与预计等待）。
    try:
        from chat_gate import submit as _gate_submit
    except Exception:
        try:
            from agent.chat_gate import submit as _gate_submit  # type: ignore
        except Exception:
            _gate_submit = None

    ticket = None
    if _gate_submit is not None:
        try:
            ticket = _gate_submit(
                _worker, lane=_chat_lane(request), preview=question
            )
        except Exception as e:  # noqa: BLE001 - 队列满等异常：降级为直接执行
            logger.warning(f"[对话] 入队失败，直接执行：{e}")
            ticket = None
    if ticket is None:
        threading.Thread(target=_worker, daemon=True).start()

    import json

    async def _gen():
        try:
            while True:
                try:
                    kind, payload = q.get(timeout=1)
                except queue.Empty:
                    # 排队中：告诉前端「第几位、等了多久、预计还要多久」，
                    # 避免用户只看到转圈以为卡死（DeepSeek/豆包 同款等待反馈）
                    if ticket is not None and ticket.state == "queued":
                        try:
                            _eta = _gate_eta(ticket.position)
                        except Exception:
                            _eta = ticket.position * 30
                        yield (
                            "data: " + json.dumps({
                                "type": "queued",
                                "position": ticket.position,
                                "waited": int(ticket.waited),
                                "eta": _eta,
                            }, ensure_ascii=False) + "\n\n"
                        )
                    else:
                        # 心跳：保持连接活跃
                        yield ": keep-alive\n\n"
                    continue
                if kind == "delta":
                    yield f"data: {json.dumps({'type': 'delta', 'text': payload}, ensure_ascii=False)}\n\n"
                elif kind == "error":
                    yield f"data: {json.dumps({'type': 'error', 'detail': payload}, ensure_ascii=False)}\n\n"
                    break
                else:  # done
                    # 用 jsonable_encoder 把 Decimal / numpy 等转成可 JSON 序列化类型，
                    # 避免 result 含 MySQL 数值（Decimal）时 json.dumps 抛 TypeError 中断 SSE。
                    from fastapi.encoders import jsonable_encoder
                    data = {
                        "type": "done",
                        "result": jsonable_encoder(_chat_to_response(payload, question).model_dump()),
                    }
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                    break
        finally:
            # 客户端断开（刷新/关标签页）：还没轮到的排队任务直接撤掉，别白占资源
            try:
                if ticket is not None:
                    ticket.cancel()
            except Exception:
                pass

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/gene/{gene_name}")
def gene_detail(gene_name: str):
    from file_security import validate_path_param
    from gene_toolkit import get_gene_detail, normalize_gene_name

    safe_name, err = validate_path_param(gene_name, label="基因名", max_len=64)
    if err:
        raise HTTPException(status_code=400, detail=err)
    detail = get_gene_detail(safe_name)
    if not detail:
        raise HTTPException(status_code=404, detail=f"未找到基因：{normalize_gene_name(safe_name)}")
    # 坐标由 gene_toolkit.get_gene_detail 统一挂载（locus 字段），此处不再重复查一次。
    return detail


_GENE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:\- ]{1,128}$")


def _safe_gene_token(raw: str, label: str = "搜索词"):
    """检索词校验：只允许基因名/别名/ID 里会出现的字符。

    不能用 validate_path_param：那个按文件路径标准把 `:` 判非法，
    于是 `wu:fp53a03`（EST 克隆号）、`si:ch211-121i18.1` 这类合法输入会被 400 掉。
    """
    t = (raw or "").strip()
    if not t:
        return "", None
    if not _GENE_TOKEN_RE.match(t):
        return None, "%s 含非法字符（只允许字母、数字、下划线、点、冒号、连字符）" % label
    return t, None


class BatchGeneSearchRequest(BaseModel):
    """批量基因检索请求：queries 支持换行 / 逗号 / 分号 / 制表符分隔。"""
    queries: str = Field(..., min_length=1, max_length=20000)
    limit: int = Field(10, ge=1, le=60)
    max_items: int = Field(200, ge=1, le=500)


@app.get("/api/genes/region")
def genes_region(assembly: str = "GRCz12tu", chrom: str = "",
                 start: int = 0, end: int = 0, limit: int = 50):
    """按位置查基因：给定装配 + 染色体 + 区间，返回与之重叠的基因。

    数据来自 gene_region_annotation（GRCz12tu 的 GFF 原生坐标与投影坐标）
    与 gene_assembly_xref 两路合并，去重后按起点排序。

    与「按位置取序列」是两个动作：这里只回答「这段里有哪些基因」，
    不出碱基。取序列仍走 /api/tools/sequence/region。
    """
    from file_security import validate_path_param
    from region_genes import genes_in_span

    asm, err = validate_path_param(assembly, label="装配版本", max_len=32)
    if err:
        raise HTTPException(status_code=400, detail=err)
    chrom_s, err = validate_path_param(chrom, label="染色体", max_len=64)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if not chrom_s:
        raise HTTPException(status_code=400, detail="缺少染色体参数")

    start, end = int(start), int(end)
    if start <= 0 or end <= 0:
        raise HTTPException(status_code=400, detail="start / end 必须为正整数")
    if end < start:
        raise HTTPException(status_code=400, detail="end 不能小于 start")
    if end - start > 10_000_000:
        raise HTTPException(status_code=400, detail="区间超过 10 Mb，请缩小范围")

    limit = max(1, min(int(limit), 500))
    genes = genes_in_span(asm, chrom_s, start, end, limit)
    return {
        "assembly": asm,
        "chromosome": chrom_s,
        "start": start,
        "end": end,
        "count": len(genes),
        "genes": genes,
        "note": ("区间为 1-based 闭区间；基因坐标与该区间只要有重叠就返回。"
                 if genes else "该区间内没有检出基因。"),
    }


@app.get("/api/genes/search")
def gene_search(q: str = "", limit: int = 20, browse: int = 0):
    """基因检索（**分级返回**）。

    q 支持符号 / 别名 / ZFIN·Ensembl ID；browse=1 且 q 为空时列出全部。

    返回结构（2026-11-03 整改）：
      groups  按匹配层级分组，每组带 label/hint/same_gene/count/results
              层级：exact_symbol / exact_alias / exact_id / conflict /
                    prefix / contains / descriptive / function_text
      results 仅「同义 + 名字相关」，**不含**描述提及与功能提及（向后兼容）
      related 描述提及 + 功能提及（不是同名）
      counts  各层级条数；same_gene_count 同义条数
      note    一句话说明命中构成

    之所以这样拆：旧实现把「功能描述里提到 p53」和「p53 的别名」拍平成一个
    列表，前端写「N genes matched」，看上去像是 30 个 p53 别名。
    """
    from gene_match import search_classified

    safe_q = q
    if q.strip():
        safe_q, err = _safe_gene_token(q, "搜索词")
        if err:
            raise HTTPException(status_code=400, detail=err)
    limit = max(1, min(int(limit), 60))
    data = search_classified(safe_q, limit=limit, browse=bool(browse))
    data["count"] = len(data.get("results") or [])
    data["browse"] = bool(browse)
    return data


@app.post("/api/genes/search/batch")
def gene_search_batch(req: BatchGeneSearchRequest):
    """批量检索：多行 / 逗号 / 分号分隔，逐个走同一套分级规则。

    每个输入项返回自己的分组结果；顶层再给一份汇总，便于前端一次渲染。
    """
    from gene_match import search_classified

    raw = [x.strip() for x in re.split(r"[\n\r,;，；\t]+", req.queries or "") if x.strip()]
    items = []
    for token in raw[: int(req.max_items)]:
        safe, err = _safe_gene_token(token, "基因名")
        if err:
            items.append({"query": token, "error": err, "total": 0,
                          "same_gene_count": 0, "groups": [], "results": []})
            continue
        d = search_classified(safe, limit=int(req.limit), browse=False)
        d["count"] = len(d.get("results") or [])
        items.append(d)

    found = sum(1 for x in items if x.get("same_gene_count"))
    return {
        "queries": items,
        "total_queries": len(items),
        "resolved": found,
        "unresolved": [x["query"] for x in items if not x.get("same_gene_count")],
        "truncated": len(raw) > int(req.max_items),
    }


def _attach_copy_roles(block: dict) -> dict:
    """任务②：给复制体块附上「每个复制体各自的文献证据」。

    证据来自本地全文倒排表（gene_alias_lit_stat / gene_alias_doc）；
    没建索引的复制体会带 indexed=false，前端/提示词据此写「证据不足」，不编造分工。
    """
    try:
        syms = []
        for g in (block.get("groups") or []):
            for c in (g.get("copies") or []):
                s = c.get("symbol")
                if s and s not in syms:
                    syms.append(s)
        if not syms:
            return block
        from gene_alias_fulltext import copy_role_evidence

        block["role_evidence"] = copy_role_evidence(syms)
        block["role_evidence_note"] = ("每个复制体的文献命中数来自本地全文倒排表；"
                                      "indexed=false 表示该复制体尚未建索引（分工结论须写「证据不足」）。")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[复制体] 分工证据附加失败（不影响接口）：{e}")
    return block


@app.get("/api/genes/orthologs")
def gene_orthologs(name: str):
    """任务②：复制体 / 共直系同源。

    斑马鱼基因名 → 它在人/鼠里的同源基因，以及共享同一同源基因的其他斑马鱼
    基因（硬骨鱼全基因组复制后常见的“一对多”关系）；
    人类/小鼠符号 → 反查斑马鱼的全部共直系同源（复制体）。
    """
    from file_security import validate_path_param
    from gene_toolkit import co_orthologs_by_human, normalize_gene_name, paralog_block

    safe_name, err = validate_path_param(name, label="基因名", max_len=64)
    if err:
        raise HTTPException(status_code=400, detail=err)

    normalized = normalize_gene_name(safe_name)

    # 先当作斑马鱼基因试；本地查不到该基因时，再当作人/鼠符号反查
    gid = None
    try:
        from gene_toolkit import _client  # 内部只读复用

        r = _client().execute_sql(
            "SELECT gene_id FROM gene_basic WHERE gene_name = :n OR gene_symbol = :n LIMIT 1",
            params={"n": normalized},
        )
        if r.get("success") and r.get("data"):
            gid = r["data"][0]["gene_id"]
    except Exception:
        gid = None

    if gid:
        block = paralog_block(gid)
        block["query"] = normalized
        block["mode"] = "zebrafish_gene"
        if not block.get("groups"):
            block["note"] = "本地库里没有该基因的人/鼠同源记录。"
        return _attach_copy_roles(block)

    block = co_orthologs_by_human(safe_name)
    block["mode"] = "human_symbol"
    if not block.get("groups"):
        raise HTTPException(status_code=404, detail=f"未找到同源关系：{normalized}")
    return _attach_copy_roles(block)


@app.get("/api/genes/alias-literature")
def gene_alias_literature(name: str):
    """任务①：**全文级**别名文献证据（按需触发那条链路的数据源）。

    返回每个别名在 题名 / 摘要 / 全文 里的命中篇数 + 带命中片段的样本；
    该基因尚未建倒排表时降级为题名级（level=title），不会报错。
    """
    from file_security import validate_path_param
    from gene_alias_evidence import build_alias_evidence

    safe_name, err = validate_path_param(name, label="基因名", max_len=64)
    if err:
        raise HTTPException(status_code=400, detail=err)

    ev = build_alias_evidence(safe_name)
    if not ev:
        raise HTTPException(status_code=404, detail=f"未找到基因：{safe_name}")

    ft = ev.get("fulltext") or {}
    if ft.get("items"):
        return {
            "gene": ev.get("gene"),
            "level": "fulltext",
            "scanned_docs": ft.get("scanned_docs"),
            "built_at": ft.get("built_at"),
            "items": ft["items"],
        }
    return {
        "gene": ev.get("gene"),
        "level": ev.get("level") or "title",
        "scanned_docs": 0,
        "built_at": "",
        "items": [{
            "alias": r.get("alias"), "source": "",
            "title_docs": int(r.get("count") or 0), "abstract_docs": 0, "body_docs": 0,
            "total_docs": int(r.get("count") or 0), "samples": r.get("samples") or [],
        } for r in (ev.get("literature") or [])],
    }


@app.get("/api/hub/stats")
def hub_dashboard_stats(fresh: int = 0):
    """Hub 产品化仪表盘：多 Agent + 命名映射 + 文献规模。

    fresh=1 强制重算；默认优先返回 maintenance 缓存（含 refreshed_at）。
    """
    try:
        if not fresh:
            try:
                from hub_maintenance import get_cached_stats

                cached = get_cached_stats()
                if cached and cached.get("refreshed_at"):
                    return cached
            except Exception:
                pass
        from hub_orchestrator import hub_stats
        from hub_maintenance import audit_literature

        stats = hub_stats()
        try:
            audit = audit_literature()
            stats.setdefault("hub", {})
            stats["hub"]["knowledge_d_files"] = audit["knowledge_d"]["file_count"]
            stats["hub"]["literature_catalog_lines"] = stats["hub"].get(
                "literature_catalog_lines", audit["catalog"]["lines"]
            ) or audit["catalog"]["lines"]
            stats["hub"]["progress_pmids"] = audit["progress_pmids"]
        except Exception:
            pass
        import time as _time
        from datetime import datetime, timezone

        stats["refreshed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stats["refreshed_ts"] = _time.time()
        return stats
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/hub/maintenance/status")
def hub_maintenance_status():
    try:
        from hub_maintenance import status, audit_literature

        out = status()
        out["audit"] = audit_literature()
        return out
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


class FeedbackPayload(BaseModel):
    """使用反馈 / 问题报告（轻量，不落数据库）。"""

    message: str = ""
    contact: str = ""
    page: str = ""


_FEEDBACK_HITS: dict = {}


@app.post("/api/hub/feedback")
def hub_feedback(payload: FeedbackPayload):
    """接收使用反馈并追加写入 `$RT/feedback/feedback.jsonl`，便于离线排查。

    限制：内容 4–4000 字符；同进程内每 60 s 最多 5 条（内存限流，重启即清）。
    隐私：只记录用户主动填写的内容 + 页面地址，不采集 IP / 账号。
    """
    import datetime
    import json as _json

    msg = (payload.message or "").strip()
    if len(msg) < 4:
        raise HTTPException(status_code=400, detail="反馈内容太短（至少 4 个字符）")
    if len(msg) > 4000:
        raise HTTPException(status_code=400, detail="反馈内容过长（上限 4000 字符）")

    now = time.time()
    hits = [t for t in _FEEDBACK_HITS.get("all", []) if now - t < 60]
    if len(hits) >= 5:
        raise HTTPException(status_code=429, detail="反馈过于频繁，请 1 分钟后再试")
    hits.append(now)
    _FEEDBACK_HITS["all"] = hits

    runtime = os.environ.get("ZF_RUNTIME_DIR") or os.path.join(
        os.path.dirname(_BASE_DIR), "zebrafish_runtime")
    out_dir = os.path.join(runtime, "feedback")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "feedback.jsonl")
    rec = {
        "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "message": msg,
        "contact": (payload.contact or "").strip()[:120],
        "page": (payload.page or "").strip()[:200],
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info("收到用户反馈：%s…（已存 %s）", msg[:60], path)
    return {"ok": True, "saved_to": path}


@app.get("/api/hub/visit-stats")
def hub_visit_stats(lines: int = 20000):
    """聚合访问统计（M3-5）。

    隐私：只返回**计数**（请求数、独立来源数、状态分布、Top 路由），
    绝不返回任何 IP 明细；因此这里给不了"访客地图"（那需要地理库 + IP 明细）。
    uvicorn 默认访问日志没有时间戳，所以统计口径是"最近 N 条访问日志窗口"，
    窗口起止时间取自日志文件 mtime。
    """
    import re
    import collections
    import time as _time

    runtime = os.environ.get("ZF_RUNTIME_DIR") or os.path.join(
        os.path.dirname(_BASE_DIR), "zebrafish_runtime"
    )
    log_path = os.path.join(runtime, "logs", "hub.log")
    if not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail=f"日志不存在：{log_path}")

    n = max(200, min(int(lines or 20000), 200000))
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = min(size, 4_000_000)
            f.seek(size - chunk)
            raw = f.read().decode("utf-8", "replace")
        rows = raw.splitlines()[-n:]
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))

    access_re = re.compile(r'INFO:\s+(\S+?):\d+\s+-\s+"(\w+)\s+(\S+)\s+HTTP/1\.[01]"\s+(\d{3})')
    total = 0
    statuses = collections.Counter()
    routes = collections.Counter()
    clients = set()
    for line in rows:
        m = access_re.search(line)
        if not m:
            continue
        client, method, path, code = m.group(1), m.group(2), m.group(3), m.group(4)
        total += 1
        statuses[code[0] + "xx"] += 1
        routes[path.split("?")[0]] += 1
        clients.add(client)

    try:
        mtime = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(os.path.getmtime(log_path)))
    except Exception:
        mtime = None

    return {
        "ok": True,
        "window": {"lines_scanned": len(rows), "access_lines": total, "log_mtime": mtime, "log_bytes": size},
        "requests": total,
        "unique_clients": len(clients),
        "status_classes": dict(statuses.most_common()),
        "top_routes": [{"route": r, "count": c} for r, c in routes.most_common(12)],
        "privacy_note": "只统计计数，不返回 IP 明细；如需地图需另接地理库并在合规前提下使用。",
    }


@app.post("/api/hub/maintenance/run")
def hub_maintenance_run(
    job: str = "stats",
    dry_run: int = 0,
    max_pubmed: Optional[int] = None,
    background: int = 1,
    trigger: str = "manual",
):
    """手动触发维护任务：literature | gallery | stats | all | audit。

    默认 background=1：起线程后立刻返回，任务进度由 /api/hub/maintenance/status 轮询。
    一次文献更新实测可能跑几小时，而这条请求要穿过 Cloudflare 隧道（约 100 秒
    就 524），同步等结果必然被前端判成「触发失败」。
    """
    try:
        from hub_maintenance import run_job, audit_literature, start_async_job

        j = (job or "stats").strip().lower()
        if j == "audit":
            return audit_literature()
        if background and not dry_run and j in ("literature", "lit", "papers", "gallery", "stats"):
            return start_async_job(
                {"lit": "literature", "papers": "literature"}.get(j, j),
                trigger=trigger,
                max_pubmed=max_pubmed,
            )
        return run_job(j, dry_run=bool(dry_run), max_pubmed=max_pubmed)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.get("/api/hub/research-feed")
def hub_research_feed(updates: int = 6, top_years: int = 6, day: str = ""):
    """首页「研究动态」：每日一文 + 更新历史 + 年度分布 + 维护状态。

    数据全部来自真实产物，逐段容错（任一环失败不影响其余字段）：
      · 每日一文  ← $RUNTIME/hub_recent_papers.json（由 scripts/build_hub_recent_papers.py
                    从 data/literature/literature_catalog.jsonl 生成的只读派生缓存）
      · 更新历史  ← MySQL literature_sync_log（文献同步/入库的真实记录）
      · 年度分布  ← MySQL literature_paper（按年计数）
      · 维护状态  ← agent/hub_maintenance.status() + audit_literature()
    可选参数 day=YYYY-MM-DD：在同一候选池内按该日期做确定性选取（首页日期下拉框）。
    """
    import hashlib
    from datetime import datetime, date

    # 日期下拉框：day=YYYY-MM-DD 时按该日期做确定性选取（同一候选池）
    pick_day = date.today()
    day_arg = (day or "").strip()
    if day_arg:
        try:
            pick_day = datetime.strptime(day_arg, "%Y-%m-%d").date()
        except ValueError:
            pick_day = date.today()

    out: dict = {"ok": True, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "day": pick_day.isoformat()}

    # —— 每日一文（确定性按日期选取）——
    try:
        from pathlib import Path

        from hub_maintenance import _runtime_root

        # 缓存可能落在任一个运行时目录（构建进程与服务的环境变量未必一致），逐个候选找
        _cands: list[Path] = []
        for _k in ("ZEBRA_RUNTIME", "ZEBRAFISH_RUNTIME"):
            _v = (os.environ.get(_k) or "").strip()
            if _v:
                _cands.append(Path(_v) / "hub_recent_papers.json")
        _base = Path(_runtime_root())
        _cands.append(_base / "hub_recent_papers.json")
        _cands.append(_base.parent / "zebrafish_runtime" / "hub_recent_papers.json")
        cache = next((p for p in _cands if p.is_file()), _cands[0])
        if cache.is_file():
            d = json.loads(cache.read_text(encoding="utf-8"))
            papers = [p for p in (d.get("papers") or []) if p.get("title")]
            if papers:
                idx = int(hashlib.md5(pick_day.isoformat().encode()).hexdigest(), 16) % len(papers)

                def _pack_row(row: dict) -> dict:
                    """单条论文的对外结构（链接优先级：缓存的 url > PubMed > DOI）。"""
                    p_pmid = str(row.get("pmid") or "").strip()
                    p_doi = str(row.get("doi") or "").strip()
                    if row.get("url"):
                        p_url = row["url"]
                        p_kind = row.get("link_kind") or (
                            "pubmed" if p_pmid else ("doi" if p_doi else "search")
                        )
                    elif p_pmid:
                        p_url, p_kind = f"https://pubmed.ncbi.nlm.nih.gov/{p_pmid}/", "pubmed"
                    elif p_doi:
                        p_url, p_kind = f"https://doi.org/{p_doi}", "doi"
                    else:
                        p_url, p_kind = "", "none"
                    return {
                        "date": pick_day.isoformat(),
                        "pmid": p_pmid,
                        "doi": p_doi,
                        "title": row.get("title"),
                        "journal": row.get("journal"),
                        "year": row.get("year"),
                        "abstract": (row.get("abstract") or "")[:600],
                        "url": p_url,
                        "link_kind": p_kind,
                        "free_url": row.get("free_url") or "",
                        "pmcid": row.get("pmcid") or "",
                        "doi_untrusted": bool(row.get("doi_untrusted")),
                        "pool_size": len(papers),
                        "pool_source": d.get("source"),
                        "pool_generated_at": d.get("generated_at"),
                        "pool_lines_scanned": d.get("scanned_lines"),
                        "link_stats": d.get("link_stats") or {},
                    }

                out["daily"] = _pack_row(papers[idx])
                # 首页卡片（6.png）展示 2～3 条：同一候选池内顺延取，仍是可复现的确定性选取
                out["daily_list"] = [
                    _pack_row(papers[(idx + k) % len(papers)]) for k in range(min(3, len(papers)))
                ]
            else:
                out["daily_error"] = "缓存内没有可用条目"
        else:
            out["daily_error"] = f"缺少缓存 {cache}（先跑 scripts/build_hub_recent_papers.py）"
    except Exception as e:
        out["daily_error"] = f"{type(e).__name__}: {e}"

    # —— 更新历史 + 年度分布 + 总量（MySQL）——
    try:
        from mysql_client import get_mysql_client
        from sqlalchemy import text

        db = next(get_mysql_client().get_db())
        try:
            rows = db.execute(
                text("SELECT event_time, actor, action, index_version, detail_json "
                     "FROM literature_sync_log ORDER BY id DESC LIMIT :n"),
                {"n": max(1, int(updates))},
            ).mappings().all()
            out["updates"] = [
                {
                    "event_time": str(r["event_time"]),
                    "actor": r["actor"],
                    "action": r["action"],
                    "index_version": r["index_version"],
                    "detail": (r["detail_json"] or "")[:400],
                }
                for r in rows
            ]
            yrs = db.execute(
                text("SELECT year, COUNT(*) AS n FROM literature_paper "
                     "WHERE year IS NOT NULL AND year<>'' GROUP BY year ORDER BY year DESC LIMIT :n"),
                {"n": max(1, int(top_years))},
            ).mappings().all()
            out["years"] = [{"year": str(r["year"]), "count": int(r["n"] or 0)} for r in yrs]
            tot = db.execute(text("SELECT COUNT(*) AS n FROM literature_paper")).mappings().first()
            out["stats"] = {"papers": int((tot or {}).get("n") or 0)}
        finally:
            db.close()
    except Exception as e:
        out["db_error"] = f"{type(e).__name__}: {e}"

    # —— 维护状态（上次更新 / 是否到期 / 目录规模）——
    try:
        from hub_maintenance import status as _maint_status, audit_literature

        st = _maint_status()
        au = audit_literature()
        state = st.get("state") or {}
        last = state.get("literature_last_result") or {}
        out["maintenance"] = {
            "enabled": st.get("enabled"),
            "running": (st.get("running") or {}).get("literature"),
            "due": (st.get("due") or {}).get("literature"),
            "interval_hours": round(float((st.get("intervals_sec") or {}).get("literature") or 0) / 3600.0, 2),
            "last_ts": state.get("literature_last_ts"),
            "last_result": last,
            # 首页徽章直接读这两个：跑过一次 ≠ 采到过文献，0 篇时不能说「已是最新」
            "last_new": last.get("new"),
            "last_ok": last.get("ok"),
            "last_error": (last.get("error") or "") if last.get("ok") is False else "",
            "progress": st.get("progress"),
            "runs": state.get("runs"),
            "catalog_lines": (au.get("catalog") or {}).get("lines"),
            "knowledge_d_files": (au.get("knowledge_d") or {}).get("file_count"),
            "progress_pmids": au.get("progress_pmids"),
        }
    except Exception as e:
        out["maintenance_error"] = f"{type(e).__name__}: {e}"

    return out


@app.get("/api/hub/bg-pools")
def hub_bg_pools(force: int = 0):
    """各功能页独立流动背景图池（随图库 3 日轮换更新）。"""
    try:
        from hub_maintenance import _bg_pools_path, partition_bg_pools, BG_POOL_KEYS
        from pathlib import Path
        import json as _json

        path = _bg_pools_path()
        static_path = Path(WEB_DIR) / "assets" / "zf-gallery" / "bg_pools.json"
        if force:
            try:
                from hub_maintenance import run_gallery_job

                return run_gallery_job(force=True)
            except Exception as e:
                raise HTTPException(status_code=503, detail=str(e)) from e
        for p in (static_path, path):
            if p.is_file():
                try:
                    data = _json.loads(p.read_text(encoding="utf-8"))
                    if data.get("pools"):
                        return data
                except Exception:
                    pass
        # 冷启动：从当前 gallery 本地项即时切分
        try:
            from api.gallery_feed import get_gallery_feed
        except ImportError:
            from gallery_feed import get_gallery_feed  # type: ignore

        feed = get_gallery_feed(WEB_DIR, mode="mix", limit=240, force=False)
        pools = partition_bg_pools(list(feed.get("items") or []))
        return {
            "refreshed_at": feed.get("refreshed_at"),
            "count": feed.get("count") or 0,
            "pools": pools,
            "pool_keys": list(BG_POOL_KEYS),
            "ephemeral": True,
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


class RetrievalDebugRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    max_chars: int = Field(48000, ge=500, le=96000)
    top_k: Optional[int] = Field(default=None, ge=1, le=200)
    candidate_preview: int = Field(30, ge=0, le=200)


@app.post("/api/retrieval/debug")
def retrieval_debug(req: RetrievalDebugRequest):
    """检索调试：候选数 → 重排 topN → AI 打包（与 Hub 文献 Agent 同路径）。"""
    safe_q = (req.query or "").strip()
    if not safe_q:
        raise HTTPException(status_code=400, detail="查询不能为空")
    try:
        from rag.pubmed_yes_retriever import get_pubmed_yes_retriever
        from config import PUBMED_YES_PACK_CHARS, PUBMED_YES_RERANK_TOP_N

        r = get_pubmed_yes_retriever()
        if not r.ready:
            raise HTTPException(status_code=503, detail="PubmedYes FAISS 未就绪")
        top_k = req.top_k
        if top_k is not None and int(top_k) < int(PUBMED_YES_RERANK_TOP_N):
            top_k = None
        max_chars = int(req.max_chars)
        if max_chars < 24000:
            max_chars = int(PUBMED_YES_PACK_CHARS)
        return r.debug_search(
            safe_q,
            top_k=top_k,
            max_chars=max_chars,
            candidate_preview=req.candidate_preview,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/tools/stats")
def tools_stats():
    from gene_toolkit import database_stats, go_term_summary
    try:
        return {"database": database_stats(), "go_summary": go_term_summary()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


class BatchIdRequest(BaseModel):
    identifiers: list[str] = Field(..., min_length=1, max_length=200)


class IdConvertTextRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)


@app.post("/api/tools/id-convert/batch")
def tools_id_convert_batch(req: BatchIdRequest):
    from gene_toolkit import batch_id_convert
    return batch_id_convert(req.identifiers)


@app.get("/api/tools/id-convert/batch")
def tools_id_convert_batch_get(ids: str = ""):
    """GET 批量 ID 解析（ids 逗号/换行分隔，兼容旧前端）。"""
    from gene_toolkit import batch_id_convert
    id_list = [x.strip() for x in re.split(r"[,;\n、，；]+", ids) if x.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail="请提供 ids 参数")
    return batch_id_convert(id_list[:200])


@app.post("/api/tools/id-convert/text")
def tools_id_convert_text(req: IdConvertTextRequest):
    """从多行文本批量解析 ID（逗号/换行分隔）。"""
    from gene_toolkit import batch_id_convert, extract_identifiers_from_text
    ids = extract_identifiers_from_text(req.text)
    if not ids:
        raise HTTPException(status_code=400, detail="未识别有效 ID")
    return batch_id_convert(ids)


@app.get("/api/tools/id-convert")
def tools_id_convert(identifier: str):
    from gene_toolkit import id_convert
    return id_convert(identifier)


@app.get("/api/tools/gene-resolve")
def tools_gene_resolve(q: str):
    from gene_id_mapper import resolve_gene_identifier
    return resolve_gene_identifier(q)


class BlastRequest(BaseModel):
    sequence: str = Field(..., min_length=20, max_length=50000)
    top_k: int = Field(10, ge=1, le=50)
    evalue: float = Field(10.0, gt=0)


@app.post("/api/tools/blast")
def tools_blast(req: BlastRequest):
    from seq_toolkit import run_local_blast, format_blast_answer, blast_tsv_export
    result = run_local_blast(req.sequence.strip(), top_k=req.top_k, evalue=req.evalue)
    return {
        "result": result,
        "summary": format_blast_answer(result),
        "tsv": blast_tsv_export(result),
    }


class CrisprRequest(BaseModel):
    target: str = Field(..., min_length=2, max_length=200000)
    enzyme: str = Field("NGG", max_length=16)
    top_n: int = Field(20, ge=5, le=60)
    max_mismatch: int = Field(3, ge=0, le=5)
    off_target: bool = True
    add_t7: bool = False
    offtarget_scope: str = Field("transcriptome", max_length=16)


class PrimerRequest(BaseModel):
    # 允许直接给基因名（如 sox9b 只有 5 个字符），由后端解析成 cDNA 模板
    template: str = Field(..., min_length=2, max_length=200000)
    amplicon_min: int = Field(80, ge=50, le=2000)
    amplicon_max: int = Field(300, ge=60, le=3000)
    max_pairs: int = Field(8, ge=1, le=20)
    add_t7: bool = False
    off_target: bool = False
    offtarget_scope: str = Field("transcriptome", max_length=16)


@app.get("/api/tools/crispr/capabilities")
def tools_crispr_capabilities():
    """sgRNA/引物工具的能力探测：两种脱靶参考库是否就绪。

    前端据此把「全基因组」选项置灰/放开，并显示库大小——未建库时不假装能用。
    """
    from crispr_toolkit import genome_db_status

    gdb = genome_db_status()
    tx_ready = True
    tx_size = None
    try:
        from seq_toolkit import BLAST_DB
        import os as _os

        if not _os.path.isfile(BLAST_DB + ".nin"):
            tx_ready = False
        else:
            tx_size = round(sum(_os.path.getsize(BLAST_DB + s) for s in (".nin", ".nsq")
                                if _os.path.isfile(BLAST_DB + s)) / 1048576.0, 1)
    except Exception:
        tx_ready = False
    return {
        "transcriptome": {"ready": tx_ready, "size_mb": tx_size, "db": "cdna_db"},
        "genome": {"ready": bool(gdb.get("ready")), "size_mb": gdb.get("size_mb"),
                   "fasta": gdb.get("fasta") or "", "error": gdb.get("error") or "",
                   "db": "genome_db"},
    }


# ── 基因组 / 表达 / 表型：三个功能页的真实数据接口（2026-10-15）──────────────
# 原则同前：只报本地真实数据，缺数据就返回原因，不编造；单位/版本一律写进响应。

@app.get("/api/genome/overview")
def genome_overview():
    """基因组页：组装登记 + 染色体（长度/GC/基因数）+ 本地比对库规模。"""
    try:
        from genome_toolkit import overview

        return overview()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/genome/chromosome")
def genome_chromosome(chrom: str, limit: int = 60):
    """某染色体上的基因列表（本地注释库按染色体编号）。"""
    try:
        from genome_toolkit import chromosome_genes

        genes = chromosome_genes(chrom, limit)
        return {"ok": True, "chromosome": chrom, "count": len(genes), "genes": genes}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/genome/compare")
def genome_compare():
    """基因组页：GRCz11 ↔ GRCz12tu 逐染色体长度对比（GRCz11 侧联网取一次并缓存 7 天）。"""
    try:
        from genome_toolkit import chrom_compare

        return chrom_compare()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


# --- 兼容接口（旧坐标页 / 旧书签仍在用；新前端走 /api/genome/liftover） ---------
@app.get("/api/tools/genome-coord")
def tools_genome_coord(
    q: str = "",
    region: str = "",
    zfin_id: str = "",
    lang: Optional[str] = None,
):
    """按基因查各组装版本基因座；标准版为 GRCz12tu。"""
    _apply_answer_language(lang)
    from coord_crosswalk import genome_coord

    return genome_coord(q=q, region=region, zfin_id=zfin_id)


@app.get("/api/tools/coord-parse")
def tools_coord_parse(q: str = ""):
    """解析区间字符串 → 来源版本/染色体/起终点，供前端自动填入。"""
    from coord_liftover import agent_coord_parse

    return agent_coord_parse(q=q or "")


@app.get("/api/tools/coord-liftover")
def tools_coord_liftover(
    q: str = "",
    source_build: str = "",
    chromosome: str = "",
    start: int | None = None,
    end: int | None = None,
    strand: str = "+",
):
    """旧组装区间 → GRCz12tu（UCSC chain）。本接口不取序列。"""
    from coord_liftover import agent_coord_liftover

    return agent_coord_liftover(
        q=q or "",
        source_build=source_build or "",
        chromosome=chromosome or "",
        start=start,
        end=end,
        strand=strand or "+",
    )


@app.get("/api/genome/liftover")
def genome_liftover(
    build: str = "",
    chrom: str = "",
    start: int = 0,
    end: int = 0,
    strand: str = "+",
    region: str = "",
    to: str = "",
    target_build: str = "",
):
    """坐标换算：任意两个已收录组装之间（UCSC over.chain，可多跳、碱基级）。本接口不取序列。

    用法：
      ?build=GRCz11&chrom=5&start=1000&end=2000&strand=+&to=GRCz12tu
      ?region=GRCz11 chr5:1000-2000&to=GRCz12ab

    2026-10-31 修「缺链明确失败」（用户反馈：选什么组合都失败）：
      · 旧实现把目标写死成 TARGET_BUILD，而 coord_liftover v3 里它已经是空串（不设默认）；
      · 前端一直传 to=，可本函数**没有声明 to/target_build**，FastAPI 会静默丢弃该参数 →
        每次换算都落到 need_target_build，再被兜底分支解释成「这段坐标在链文件里对不上」，
        用户看到的就是「明明有链却说缺链」。
      现在三件事一起修：接收目标版本；缺目标明确回 need_target_build；
      真缺链回 chain_unavailable／missing_chain 并说清是哪一段，绝不返回近似坐标。
    """
    from coord_liftover import (
        SELECTABLE_BUILDS,
        lift_region,
        normalize_build,
        parse_region_query,
    )

    target_raw = (target_build or to or "").strip()
    if (region or "").strip():
        parsed = parse_region_query(region)
        if not parsed:
            raise HTTPException(
                status_code=400,
                detail="没认出「源版本 + 染色体:起-止」，例如 GRCz11 chr5:1000-2000",
            )
        build = build or str(parsed.get("source_build") or "")
        chrom = chrom or str(parsed.get("chromosome") or "")
        start = start or int(parsed.get("start") or 0)
        end = end or int(parsed.get("end") or 0)
        target_raw = target_raw or str(parsed.get("target_build") or "")

    source_build = normalize_build((build or "").strip())
    target = normalize_build(target_raw)
    base = {
        "ok": False,
        "source_build": source_build or (build or ""),
        "target_build": target or target_raw,
        "source_span": (f"{chrom}:{start}-{end}" if chrom else ""),
        "target_span": None,
    }
    supported = " / ".join(SELECTABLE_BUILDS)
    if not source_build:
        raise HTTPException(status_code=400, detail=f"未知的源版本「{build}」；可选 {supported}")
    if not target_raw:
        return {
            **base,
            "code": "need_target_build",
            "message_zh": "请选择目标版本后再换算。",
            "message_en": "Pick a target assembly first.",
        }
    if not target:
        raise HTTPException(status_code=400, detail=f"未知的目标版本「{target_raw}」；可选 {supported}")
    if not chrom or start <= 0 or end <= 0:
        raise HTTPException(status_code=400, detail="需要染色体与起止坐标（start/end 均 ≥1）")

    try:
        out = lift_region(source_build, chrom, start, end, strand=strand, target_build=target)
    except ModuleNotFoundError as e:  # pyliftover 未安装
        raise HTTPException(status_code=503, detail=f"服务器缺少 pyliftover：{e}") from e
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    code = out.get("code") or ""
    pair = f"{source_build} → {target}"
    if code in ("liftover_ok", "already_on_target_build"):
        # 2026-10-31：除了坐标，再给「两边各自落在哪个基因 / 哪条参考序列」。
        # 不同品系装配之间常有片段级增删，同一段数字在两边对应的基因可以完全不同
        # （实测 ASM1840007v1 chr5:24,086,227 是 si:dkey-90a24.1 区，而 GRCz11 同坐标是 tp53），
        # 只给坐标用户会以为链算错了。注释缺失的装配返回空表，不猜。
        try:
            from region_genes import chrom_accession, genes_in_span

            if out.get("chromosome") and out.get("start"):
                out["target_accession"] = chrom_accession(target, out.get("chromosome") or "")
                out["target_genes"] = genes_in_span(
                    target, out.get("chromosome") or "", int(out["start"]), int(out["end"])
                )
            out["source_accession"] = chrom_accession(source_build, chrom)
            out["source_genes"] = genes_in_span(source_build, chrom, start, end)
        except Exception:
            pass
    if code == "liftover_ok":
        out["message_zh"] = f"{source_build} {out.get('source_span')} → {target} {out.get('target_span')}（{out.get('strand')} 链）"
        out["message_en"] = f"{source_build} {out.get('source_span')} → {target} {out.get('target_span')} (strand {out.get('strand')})"
    elif code == "already_on_target_build":
        out["message_zh"] = f"这段本来就在 {target} 上，无需换算。"
        out["message_en"] = f"This span is already on {target}; nothing to convert."
    elif code == "need_target_build":
        out["message_zh"] = "请选择目标版本后再换算。"
        out["message_en"] = "Pick a target assembly first."
    elif code in ("chain_unavailable", "maf_unavailable", "missing_chain"):
        out["message_zh"] = f"本机没有 {pair} 的链文件，无法换算（不给近似坐标）。"
        out["message_en"] = f"No chain file for {pair} on this server; no approximate coordinates are returned."
    elif code == "liftover_split":
        out["message_zh"] = "这段区间跨了链的断裂点，无法作为一个连续区间换算。请缩小范围后分段换算。"
        out["message_en"] = "This span crosses a chain break; split it into smaller pieces and convert separately."
    else:
        out["message_zh"] = "这段坐标在链文件里对不上，无法换算（不给近似值）。"
        out["message_en"] = "This span has no match in the chain file; no approximate value is returned."
    return out


@app.get("/api/genome/liftover-routes")
def genome_liftover_routes():
    """可换算的成对路由（含每条边是否存在链文件），供 Liftover 页过滤「目标版本」下拉。

    前端 liftover.js 一直在请求这个地址（/api/genome/liftover-routes），但后端从来没实现过，
    404 之后前端只能退回写死的 FALLBACK 映射 —— 这正是「只有 GRCz11→GRCz12tu 像能用」的另一半原因。
    """
    from coord_liftover import list_supported_pairs

    return {"ok": True, **list_supported_pairs()}


@app.get("/api/genome/chromosomes")
def genome_chromosomes(build: str = ""):
    """某装配可选为来源的染色体清单，供 Liftover 页的染色体下拉。

    2026-11-02（用户）：染色体号应随基因组版本变化，不该手打。
    值取自链头真实 tName（经 assembly report 归一到 1–25 / MT），
    所以「下拉里能选到的」＝「链文件里换算得了的」，不含 chrUn_* 与 contig。
    """
    from coord_liftover import list_build_chromosomes

    try:
        return list_build_chromosomes(build)
    except Exception as e:  # 链文件缺失/损坏时不让整页 500
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/gene/master/stats")
def gene_master_stats():
    """GRCz12tu 非冗余基因主表概览：总数、功能基因数、各映射方法命中数。"""
    try:
        from grcz12tu_master import master_stats

        return master_stats()
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/gene/master/search")
def gene_master_search(q: str = "", functional_only: int = 1, biotype: str = "",
                       chrom: str = "", limit: int = 50, offset: int = 0):
    """按符号 / ID 检索 GRCz12tu 主表。"""
    try:
        from grcz12tu_master import master_search

        return master_search(q=q, functional_only=bool(functional_only), biotype=biotype,
                             chrom=chrom, limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/gene/master/resolve")
def gene_master_resolve(name: str = ""):
    """任意本地标识符 → GRCz12tu 落点（含映射依据）。"""
    try:
        from grcz12tu_master import resolve_identifier

        out = resolve_identifier(name)
        if not out.get("ok"):
            raise HTTPException(status_code=404, detail=out.get("error") or "未找到")
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/expression/gene")
def expression_gene(name: str, top: int = 24):
    """基因表达谱：Bgee 组织分（0–100） + Expression Atlas 胚胎分期 TPM。"""
    try:
        from expression_toolkit import gene_profile

        out = gene_profile(name, top_tissues=max(4, min(int(top or 24), 80)))
        if not out.get("ok"):
            raise HTTPException(status_code=404, detail=out.get("error") or "未找到表达数据")
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/expression/tissues")
def expression_tissues():
    """组织列表（含覆盖基因数），供"按组织看表达"用。"""
    try:
        from expression_toolkit import tissue_index

        items = tissue_index()
        return {"ok": True, "count": len(items), "unit": "expression score (0–100)",
                "source": "Bgee (data_source=bgee)", "tissues": items}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/expression/top")
def expression_top(tissue: str, n: int = 30):
    """某组织里表达最高的基因。"""
    try:
        from expression_toolkit import top_in_tissue

        out = top_in_tissue(tissue, n)
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=out.get("error") or "参数错误")
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/expression/compare")
def expression_compare(genes: str, top: int = 12):
    """多基因表达对比（2–8 个基因 × 共同覆盖度最高的组织）。"""
    try:
        from expression_toolkit import compare_genes

        items = [g.strip() for g in re.split(r"[,;\n、，；\s]+", genes or "") if g.strip()]
        out = compare_genes(items, top_tissues=max(4, min(int(top or 12), 40)))
        if not out.get("ok"):
            raise HTTPException(status_code=400, detail=out.get("error") or "参数错误")
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/phenotype/stats")
def phenotype_stats():
    """表型页能力探测：ZFIN 索引是否就绪 + 常用表型词。"""
    try:
        from phenotype_toolkit import status, term_index

        st = status()
        st["top_terms"] = term_index(limit=24) if st.get("index_ready") else []
        return st
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/phenotype/gene")
def phenotype_gene(name: str, limit: int = 200):
    """基因 → 表型（ZFIN 注释聚合）+ 该基因的本地文献兜底。"""
    try:
        from phenotype_toolkit import gene_phenotypes, literature_hits

        out = gene_phenotypes(name, limit)
        if out.get("ok"):
            out["literature"] = literature_hits(
                (out.get("gene") or {}).get("gene_symbol") or name, limit=6)
        return out
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.get("/api/phenotype/search")
def phenotype_search(q: str, limit: int = 40, literature: int = 8):
    """表型关键词 → 命中表型条目 + 候选基因（ZFIN） + 本地文献命中。"""
    try:
        from phenotype_toolkit import literature_hits, term_search

        out = term_search(q, limit)
        out["literature"] = literature_hits(q, max(1, min(int(literature or 8), 30)))
        return out
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post("/api/phenotype/build-index")
def phenotype_build_index(force: int = 0, background: int = 1):
    """构建/刷新 ZFIN 表型索引（幂等）。默认后台执行，避免请求超时。"""
    try:
        from phenotype_toolkit import build_index, status

        if not background:
            return build_index(force=bool(force))
        import threading

        def _job() -> None:
            try:
                logger.info("ZFIN 表型索引构建结果：%s", build_index(force=bool(force)))
            except Exception as exc:  # noqa: BLE001
                logger.error("ZFIN 表型索引构建失败：%s", exc)

        threading.Thread(target=_job, daemon=True).start()
        return {"ok": True, "started": True, "status": status()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


@app.post("/api/tools/crispr/design")
def tools_crispr_design(req: CrisprRequest):
    """sgRNA 设计（本地引擎）：PAM 枚举 → 规则打分 → 转录组特异性筛查。"""
    from crispr_toolkit import design_sgrna

    return design_sgrna(
        req.target.strip(),
        enzyme=req.enzyme,
        top_n=req.top_n,
        max_mismatch=req.max_mismatch,
        off_target=req.off_target,
        add_t7=req.add_t7,
        offtarget_scope=(req.offtarget_scope or "transcriptome").strip().lower(),
    )


@app.post("/api/tools/primer/design")
def tools_primer_design(req: PrimerRequest):
    """引物设计（本地引擎）：长度/GC/Tm/二聚体/发夹 + 产物长度约束。"""
    from crispr_toolkit import design_primers

    lo, hi = int(req.amplicon_min), int(req.amplicon_max)
    if hi < lo:
        lo, hi = hi, lo
    return design_primers(
        req.template.strip(),
        amplicon_min=lo,
        amplicon_max=hi,
        max_pairs=req.max_pairs,
        add_t7=req.add_t7,
        off_target=req.off_target,
        offtarget_scope=(req.offtarget_scope or "transcriptome").strip().lower(),
    )


@app.get("/api/tools/enrichment")
def tools_enrichment(
    genes: str = "",
    type: str = "both",
    padj: float = 0.05,
    top_n: int = 20,
):
    from enrichment_toolkit import (
        go_enrichment,
        kegg_enrichment,
        enrichment_tsv_export,
        format_enrichment_answer,
    )
    gene_list = [g.strip() for g in genes.split(",") if g.strip()] if genes else None
    out = {}
    if type in ("go", "both"):
        out["go"] = go_enrichment(gene_list, top_n=top_n, padj_cutoff=padj)
    if type in ("kegg", "both"):
        out["kegg"] = (
            kegg_enrichment(gene_list, top_n=top_n, padj_cutoff=padj)
            if gene_list
            else {"success": False, "error": "KEGG 需指定基因"}
        )
    out["tsv"] = enrichment_tsv_export(out.get("go"), out.get("kegg"))
    out["summary"] = format_enrichment_answer(out.get("go"), out.get("kegg"))
    return out


@app.get("/api/tools/kegg")
def tools_kegg(q: str = "", genes: str = ""):
    from enrichment_toolkit import kegg_pathway_search
    gene_list = [g.strip() for g in genes.split(",") if g.strip()]
    return kegg_pathway_search(q, gene_list or None)


@app.get("/api/tools/sequence")
def tools_sequence(gene: str, types: str = "cdna,promoter"):
    from file_security import validate_path_param
    from sequence_toolkit import extract_sequences, format_sequence_answer

    safe, err = validate_path_param(gene, label="基因名", max_len=64)
    if err:
        raise HTTPException(status_code=400, detail=err)
    kinds = [k.strip() for k in types.split(",") if k.strip()]
    data = extract_sequences(safe, kinds=kinds or ["cdna", "promoter"])
    data["summary"] = format_sequence_answer(data)
    return data


@app.get("/api/tools/sequence/region")
def tools_sequence_region(
    chrom: str,
    start: int,
    end: int,
    strand: str = "+",
    assembly: str = "GRCz12tu",
):
    """按染色体坐标取序列：默认 GRCz12tu（NCBI RefSeq，与 BLAST/cDNA 同版），可选 GRCz11（Ensembl）。"""
    from sequence_toolkit import fetch_region_sequence

    data = fetch_region_sequence(chrom, start, end, strand=strand, assembly=assembly)
    return data


@app.get("/api/tools/annotate")
def tools_annotate(gene: str):
    from file_security import validate_path_param
    from gene_annotator_toolkit import annotate_gene, format_annotator_answer

    safe, err = validate_path_param(gene, label="基因名", max_len=64)
    if err:
        raise HTTPException(status_code=400, detail=err)
    data = annotate_gene(safe)
    if data.get("success"):
        data["summary"] = format_annotator_answer(data)
    return data


@app.get("/api/knowledge/sources")
def knowledge_sources():
    from knowledge_ingest import list_knowledge_sources
    return list_knowledge_sources()


@app.post("/api/knowledge/upload")
async def knowledge_upload(request: Request, file: UploadFile = File(...)):
    from file_security import validate_upload_bytes, uploads_enabled
    from knowledge_ingest import ingest_upload_file, rebuild_rag_index
    import tempfile

    if not uploads_enabled():
        raise HTTPException(status_code=403, detail="文件上传已禁用（WEB_ALLOW_UPLOAD=0）")

    # 入库写操作需登录：避免访客往共享知识库灌文件
    hub_user = _hub_user_required(request)

    raw = await file.read()
    safe_name, data, err = validate_upload_bytes(
        file.filename or "upload.txt",
        raw,
        file.content_type,
    )
    if err:
        raise HTTPException(status_code=400, detail=err)

    tenant_slug = getattr(request.state, "tenant_slug", "default")
    # 按登录用户分子目录，降低串库风险
    tenant_slug = f"{tenant_slug}_u{hub_user['user_id']}"
    suffix = os.path.splitext(safe_name)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        saved = ingest_upload_file(tmp_path, safe_name, tenant_slug=tenant_slug)
        rebuild_rag_index()
        return {
            "success": True,
            "saved": saved,
            "tenant": tenant_slug,
            "user_id": hub_user["user_id"],
            "message": "已入库并重建 RAG 索引（按账号隔离）",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@app.get("/api/hub/gallery")
def hub_gallery_feed(mode: str = "mix", limit: int = 200, force: int = 0):
    """斑马鱼研究图：本地 + iNaturalist 最新/热度 + Commons 科研检索 + Openverse；镜像到 live/。"""
    try:
        try:
            from api.gallery_feed import get_gallery_feed
        except ImportError:
            from gallery_feed import get_gallery_feed  # type: ignore
        return get_gallery_feed(WEB_DIR, mode=mode, limit=limit, force=bool(force))
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


# ---------------------------------------------------------------------------
# 实时更新（2026-10-01）：内容版本信号 + SSE 推送
# ---------------------------------------------------------------------------
# 设计要点（这台共享机器资源紧张，必须自保）：
#   1) 版本号只读**文件系统 mtime/size 与维护状态**，不查数据库 → 极轻，可高频轮询；
#   2) 任何异常都降级为 200 + ok:false / 空版本，**绝不 500**，不能因为实时功能拖垮 Hub；
#   3) SSE 连接数有上限（HUB_SSE_MAX_CLIENTS，默认 8），单连接最长 HUB_SSE_MAX_SEC（默认 600s）
#      后自然结束并让客户端重连 —— 防止连接堆积、也方便穿过隧道。
# 前端消费方：web/js/live-update.js（拿到版本变化后只刷新"影像流 + 统计"两块）。
_LIVE_SSE_CLIENTS = 0
_LIVE_SSE_LOCK = threading.Lock()
_LIVE_SSE_MAX_CLIENTS = int(os.getenv("HUB_SSE_MAX_CLIENTS", "8"))
_LIVE_SSE_TICK_SEC = float(os.getenv("HUB_SSE_TICK_SEC", "5"))
_LIVE_SSE_MAX_SEC = float(os.getenv("HUB_SSE_MAX_SEC", "600"))
_LIVE_SSE_HEARTBEAT_SEC = float(os.getenv("HUB_SSE_HEARTBEAT_SEC", "25"))


def _maint_module():
    """hub_maintenance 在 agent/ 下；两种导入路径都试，失败返回 None。"""
    try:
        from agent import hub_maintenance as m  # type: ignore
        return m
    except Exception:
        pass
    try:
        import hub_maintenance as m  # type: ignore
        return m
    except Exception:
        return None


# MySQL 侧“新数据”探针：带 TTL 缓存（默认 30s）、失败静默。
# 目的：图片/统计能靠文件 mtime 感知，但“新文献入库”发生在数据库里，
# 不探库就做不到秒级感知 —— 而每次 tick 都查库又会拖累这台共享机器。
_LIVE_DB = {"t": 0.0, "val": {}, "engine": None, "lock": threading.Lock()}
_LIVE_DB_TTL = float(os.getenv("HUB_LIVE_DB_TTL", "30"))

# 版本信号本身的短缓存（见 _live_version 的说明）：SSE + 轮询共用，避免重复计算
_LIVE_VER = {"t": 0.0, "val": {}, "lock": threading.Lock()}
_LIVE_VER_TTL = float(os.getenv("HUB_LIVE_VER_TTL", "3"))


def _db_signal() -> dict:
    """返回 {literature_rows, literature_max_id, gene_basic}；取不到返回 {}。"""
    now = time.time()
    with _LIVE_DB["lock"]:
        if _LIVE_DB["t"] and (now - _LIVE_DB["t"]) < _LIVE_DB_TTL:
            return dict(_LIVE_DB["val"])
    val: dict = {}
    try:
        from sqlalchemy import create_engine, text
        from config import DB_CONFIG
        eng = _LIVE_DB.get("engine")
        if eng is None:
            url = (
                f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
                f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
                f"?charset={DB_CONFIG['charset']}&connect_timeout=3"
            )
            eng = create_engine(url, pool_pre_ping=True, pool_size=1, max_overflow=0)
            _LIVE_DB["engine"] = eng
        with eng.connect() as conn:
            for key, sql in (
                ("literature_rows", "SELECT COUNT(*) FROM literature_paper"),
                ("literature_max_id", "SELECT COALESCE(MAX(id), 0) FROM literature_paper"),
                ("gene_basic", "SELECT COUNT(*) FROM gene_basic"),
            ):
                try:
                    val[key] = int(conn.execute(text(sql)).scalar() or 0)
                except Exception:
                    pass
    except Exception:
        val = {}
    with _LIVE_DB["lock"]:
        _LIVE_DB["t"] = now
        _LIVE_DB["val"] = val
    return dict(val)


def _live_version() -> dict:
    """内容版本信号：任一参与项变化 → version 变化（sha1 前 16 位）。

    2026-10-28：加 3s 进程内缓存。
    实测线上这个接口要 8-14s。SSE 每个客户端每 5s 调一次、外加轮询兜底，
    每个调用都要 stat 若干文件 + 走一次 MySQL count；并发几个客户端就会把
    一个 worker 占满，反而拖慢其它接口（前端还因此误判成「离线预览」）。
    版本信号本来就不需要毫秒级精度，3s 内直接复用上一次结果。
    """
    now = time.time()
    with _LIVE_VER["lock"]:
        if _LIVE_VER["t"] and (now - _LIVE_VER["t"]) < _LIVE_VER_TTL:
            val = dict(_LIVE_VER["val"])
            val["at"] = now          # at 是"本次答复时间"，不能沿用旧值
            return val

    parts: dict = {}

    def _stat(label: str, path: str) -> None:
        try:
            st = os.stat(path)
            parts[label] = f"{int(st.st_mtime)}:{int(st.st_size)}"
        except Exception:
            parts[label] = "0:0"

    gallery_dir = os.path.join(WEB_DIR, "assets", "zf-gallery")
    rt = os.getenv("ZEBRA_RUNTIME") or os.getenv("ZF_RUNTIME") or ""
    logs = os.path.join(rt, "logs") if rt else ""
    m = _maint_module()
    if m is not None:
        try:
            state_file = str(m._state_path())
            pools_file = str(m._bg_pools_path())
            stats_file = str(m._stats_cache_path())
        except Exception:
            state_file = os.path.join(logs, "hub_maintenance_state.json") if logs else ""
            pools_file = os.path.join(gallery_dir, "bg_pools.json")
            stats_file = os.path.join(logs, "hub_stats_cache.json") if logs else ""
    else:
        state_file = os.path.join(logs, "hub_maintenance_state.json") if logs else ""
        pools_file = os.path.join(gallery_dir, "bg_pools.json")
        stats_file = os.path.join(logs, "hub_stats_cache.json") if logs else ""

    _stat("maint_state", state_file)
    _stat("bg_pools", pools_file)
    _stat("stats_cache", stats_file)
    _stat("gallery_dir", gallery_dir)
    # 文献的可检索性依赖 FAISS 索引重建：索引文件也必须进版本信号，
    # 否则"新文献入库"和"真的能检索到"会被混为一谈。
    # 实测线上是**分片布局**（manifest.json + shard_XXXX/），没有单体 index.faiss，
    # 所以两种布局都探，取到哪个算哪个。
    faiss_dir = os.getenv("FAISS_PUBMED_YES_DIR") or os.getenv("FAISS_INDEX_DIR") or ""
    if faiss_dir:
        _stat("faiss_index", os.path.join(faiss_dir, "index.faiss"))
        _stat("faiss_manifest", os.path.join(faiss_dir, "manifest.json"))
        _stat("faiss_dir", faiss_dir)

    maint: dict = {}
    if m is not None:
        try:
            st = (m.status() or {}).get("state") or {}
            for k in ("literature_last_ts", "gallery_last_ts", "stats_last_ts"):
                maint[k] = st.get(k, 0)
        except Exception:
            maint = {}

    raw = "|".join(f"{k}={v}" for k, v in sorted(parts.items()))
    raw += "|" + "|".join(f"{k}={maint.get(k, 0)}" for k in
                          ("literature_last_ts", "gallery_last_ts", "stats_last_ts"))
    # MySQL 侧新数据（带 TTL 缓存的探针）：让"新文献入库 / 新基因行"也能被版本号感知
    db = _db_signal()
    raw += "|" + "|".join(f"{k}={db.get(k, 0)}" for k in
                          ("literature_rows", "literature_max_id", "gene_basic"))
    out = {
        "ok": True,
        "version": hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16],
        "parts": parts,
        "maint": maint,
        "db": db,
        "at": time.time(),
    }
    with _LIVE_VER["lock"]:
        _LIVE_VER["t"] = now
        _LIVE_VER["val"] = out
    return dict(out)


@app.get("/api/hub/stream-version")
def hub_stream_version():
    """轻量版本信号（前端轮询兜底用）。永不 500。"""
    try:
        return _live_version()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[实时] 版本信号计算失败：{type(e).__name__}: {e}")
        return {"ok": False, "version": "", "error": f"{type(e).__name__}: {e}"}


def _live_sse_release() -> None:
    global _LIVE_SSE_CLIENTS
    with _LIVE_SSE_LOCK:
        _LIVE_SSE_CLIENTS = max(0, _LIVE_SSE_CLIENTS - 1)


@app.get("/api/hub/events")
async def hub_events():
    """SSE：内容版本变化即推送 `event: version`；心跳保活；到时限自然结束（客户端重连）。"""
    global _LIVE_SSE_CLIENTS
    with _LIVE_SSE_LOCK:
        if _LIVE_SSE_CLIENTS >= _LIVE_SSE_MAX_CLIENTS:
            return JSONResponse(status_code=503, content={"ok": False, "detail": "实时连接数已达上限，请稍后"})
        _LIVE_SSE_CLIENTS += 1

    async def _gen():
        try:
            yield "retry: 5000\n\n"
            t0 = time.time()
            last = ""
            hb = time.time()
            while (time.time() - t0) < _LIVE_SSE_MAX_SEC:
                try:
                    # **不能直接调同步函数**：_live_version 会 stat 文件 + 查 MySQL，
                    # 在这个 async 生成器里同步跑会阻塞整个事件循环（所有请求一起变慢）。
                    cur = await asyncio.to_thread(_live_version)
                    ver = cur.get("version") or ""
                except Exception:  # noqa: BLE001
                    cur, ver = {"ok": False}, ""
                if ver and ver != last:
                    last = ver
                    yield "event: version\ndata: " + json.dumps(cur, ensure_ascii=False) + "\n\n"
                    hb = time.time()
                elif (time.time() - hb) >= _LIVE_SSE_HEARTBEAT_SEC:
                    hb = time.time()
                    yield f": ping {int(time.time())}\n\n"
                await asyncio.sleep(_LIVE_SSE_TICK_SEC)
            yield "event: bye\ndata: {}\n\n"
        finally:
            _live_sse_release()

    return StreamingResponse(_gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    })


@app.get("/")
def index(request: Request):
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.isfile(index_path):
        # 首页必须每次回源校验，否则改完 HTML 里的 ?v= 版本号用户也拿不到
        resp = FileResponse(index_path, headers={
            "Cache-Control": "no-cache, must-revalidate",
            "Pragma": "no-cache",
        })
        # API 密码锁通行证：浏览器打开页面即获得，后续同源请求自动携带，
        # 前端代码无需改动；未配置 API_GATE_KEY 时不做任何事。
        try:
            from api_gate import set_pass_cookie
            set_pass_cookie(resp, request)
        except Exception as e:  # 通行证失败不能影响页面本身
            logger.warning(f"[安全] 通行证下发失败（不影响页面访问）：{e}")
        return resp
    return {"message": "前端未找到，请确认 web/index.html 存在"}


if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def main():
    import uvicorn
    logger.info(f"[成功] Web 服务启动：http://{WEB_HOST}:{WEB_PORT}")
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT, log_level="warning")


if __name__ == "__main__":
    main()
