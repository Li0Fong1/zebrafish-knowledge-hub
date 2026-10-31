# -*- coding: utf-8 -*-
"""本地前端预览服务（仅用于 UI 开发/验收，不参与线上）

线上由 Nginx 把 /static/* 映射到 web/；本地没有 Nginx，所以用一个最小 HTTP 服务等价映射：
    /            -> web/index.html
    /static/xxx  -> web/xxx
    /api/*       -> 默认返回 501（前端进入"离线预览"态）
    *            -> 若命中 web/ 下的文件则返回，否则回 index.html（hash 路由不需要回退）

默认还会挂一层 **离线演示 API**（--no-mock 可关），用来端到端验收 UI 与实时更新：
    /api/hub/stats            真实服务器快照（2026-09-01），带 _mock/_source 标注，**不冒充实时**
    /api/hub/bg-pools         读 web/assets/zf-gallery/bg_pools.json
    /api/hub/gallery          读 web/assets/zf-gallery/manifest.json
    /api/hub/stream-version   内容版本信号（本地 mtime 哈希），带 mock:true
    /api/hub/events           SSE 推送版本变化 —— 验证 web/js/live-update.js
    /api/hub/maintenance/status  维护状态静态演示值
    /api/hub/__touch          人为触发一次版本变化（验证"已更新"胶囊），仅本地

用法：
    .venv\\Scripts\\python.exe -X utf8 scripts\\preview_web.py            # 默认 8099，带演示 API
    .venv\\Scripts\\python.exe -X utf8 scripts\\preview_web.py 8123
    .venv\\Scripts\\python.exe -X utf8 scripts\\preview_web.py 8099 --no-mock   # 只做静态映射

    # 连真实后端（本地 uvicorn 或线上 Hub）：/api/* 原样反代过去，前端不再报
    # 「preview server: no API」。改前端代码时最常用这个，能拿到真数据。
    .venv\\Scripts\\python.exe -X utf8 scripts\\preview_web.py 8099 --api=http://127.0.0.1:8100
    .venv\\Scripts\\python.exe -X utf8 scripts\\preview_web.py 8099 --api=https://xxx.trycloudflare.com

注意：--api 优先级高于离线演示 API；读不到后端时回落 502 并带上原因，不伪装成成功。
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
GALLERY = WEB / "assets" / "zf-gallery"
SNAPSHOT = ROOT / "项目阶段" / "04_运维静音自愈" / "证明归档" / "_full_audit_20260901.json"
PREVIEW_DATA = ROOT / "scripts" / "preview_data"
PAPERS_CACHE = PREVIEW_DATA / "hub_recent_papers.json"
FEED_DB = PREVIEW_DATA / "research_feed_db.json"

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}

MOCK = True                      # 由 main() 按命令行覆盖
API_BASE = ""                    # 由 --api= 覆盖：非空时 /api/* 全部反代到它
_LOCK = threading.Lock()
_TOUCH = {"n": 0, "lit": 0}      # /api/hub/__touch 计数：人为制造版本变化（lit 模拟新文献）


# --------------------------------------------------------------------------
# 离线演示数据（全部来自仓库内的真实快照，不做假数字）
# --------------------------------------------------------------------------
def _read_snapshot() -> dict:
    try:
        return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def stats_payload() -> dict:
    snap = _read_snapshot()
    hs = ((snap.get("server") or {}).get("hub_stats") or {})
    payload = dict(hs) if hs else {}
    payload["_mock"] = True
    payload["_source"] = str(SNAPSHOT.relative_to(ROOT)).replace(os.sep, "/")
    payload["_note"] = "本地预览返回的是服务器快照，非实时；线上由 /api/hub/stats 实时计算。"
    payload["refreshed_at"] = str(snap.get("compiled_at") or "").replace(" ", "T") + "Z"
    payload["reference"] = dict(payload.get("reference") or {})
    payload["reference"].setdefault("user_facing_reference", "GRCz12tu")
    payload["hub"] = dict(payload.get("hub") or {})
    payload["database"] = dict(payload.get("database") or {})
    return payload


def _stat_label(p: Path) -> str:
    try:
        st = p.stat()
        return f"{int(st.st_mtime)}:{int(st.st_size)}"
    except Exception:
        return "0:0"


def version_payload() -> dict:
    pairs = {
        "bg_pools": _stat_label(GALLERY / "bg_pools.json"),
        "manifest": _stat_label(GALLERY / "manifest.json"),
        "gallery_dir": _stat_label(GALLERY),
        "index_html": _stat_label(WEB / "index.html"),
        "touch": str(_TOUCH["n"]),
    }
    raw = "|".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    # 模拟线上 _db_signal()：literature_rows 变化 => 前端弹"文献 +N"
    db = {
        "literature_rows": 32549 + _TOUCH["lit"],
        "literature_max_id": 900000 + _TOUCH["lit"],
        "gene_basic": 26321,
    }
    raw += "|" + "|".join(f"{k}={v}" for k, v in sorted(db.items()))
    return {
        "ok": True,
        "version": hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16],
        "parts": pairs,
        "maint": {"literature_last_ts": 0, "gallery_last_ts": 0, "stats_last_ts": 0},
        "db": db,
        "mock": True,
        "at": time.time(),
    }


def maintenance_payload() -> dict:
    return {
        "_mock": True,
        "enabled": True,
        "now": time.time(),
        "intervals_sec": {"literature": 604800, "gallery": 259200, "stats": 21600},
        "running": {"literature": False, "gallery": False, "stats": False},
        "due": {"literature": False, "gallery": False, "stats": False},
        "state": {"literature_last_ts": 0, "gallery_last_ts": 0, "stats_last_ts": 0},
        "audit": {"note": "本地预览为静态演示值；线上由 agent/hub_maintenance.py 提供"},
    }


def bg_pools_payload() -> dict:
    try:
        return json.loads((GALLERY / "bg_pools.json").read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "pools": {}}


def gallery_payload(limit: int = 240) -> dict:
    try:
        man = json.loads((GALLERY / "manifest.json").read_text(encoding="utf-8"))
        items = man.get("items") if isinstance(man, dict) else man
        if not isinstance(items, list):
            items = []
        out = []
        for it in items[:limit]:
            f = it.get("file") if isinstance(it, dict) else str(it)
            if not f:
                continue
            out.append({
                "url": "/static/assets/zf-gallery/" + f,
                "title": (it.get("title") if isinstance(it, dict) else "") or f,
                "license": (it.get("license") if isinstance(it, dict) else "") or "",
                "artist": (it.get("artist") if isinstance(it, dict) else "") or "",
            })
        return {"ok": True, "_mock": True, "warming": False, "total": len(out), "items": out}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "warming": False, "items": [], "error": str(e)}


def research_feed_payload(day: str = "", updates: int = 6, top_years: int = 6) -> dict:
    """镜像线上 /api/hub/research-feed 的结构。

    数据来源：scripts/fetch_preview_data.py 从服务器抓下来的**真实快照**——
      · 候选池 $RT/hub_recent_papers.json（200 条，服务器上的只读派生缓存）
      · MySQL 真实统计（论文总数 / 年度分布 / 文献同步记录）
    每日选取规则与线上一致：md5(日期) % 候选池大小（可复现、且日期下拉框能用）。
    """
    from datetime import date as _date, datetime as _dt

    pick = _date.today()
    if day:
        try:
            pick = _dt.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            pass

    out: dict = {
        "ok": True,
        "generated_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        "day": pick.isoformat(),
        "_mock": True,
        "_note": "本地预览：数据为服务器真实快照（非实时）",
    }

    cache: dict = {}
    papers: list = []
    try:
        cache = json.loads(PAPERS_CACHE.read_text(encoding="utf-8"))
        papers = [p for p in (cache.get("papers") or []) if p.get("title")]
    except Exception as e:  # noqa: BLE001
        out["daily_error"] = "本地预览：读不到候选池（先跑 scripts/fetch_preview_data.py）: %s" % e

    if papers:
        idx = int(hashlib.md5(pick.isoformat().encode()).hexdigest(), 16) % len(papers)

        def pack(row: dict) -> dict:
            pmid = str(row.get("pmid") or "").strip()
            doi = str(row.get("doi") or "").strip()
            if row.get("url"):
                url = row["url"]
                kind = row.get("link_kind") or ("pubmed" if pmid else ("doi" if doi else "search"))
            elif pmid:
                url, kind = "https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid, "pubmed"
            elif doi:
                url, kind = "https://doi.org/%s" % doi, "doi"
            else:
                url, kind = "", "none"
            return {
                "date": pick.isoformat(), "pmid": pmid, "doi": doi,
                "title": row.get("title"), "journal": row.get("journal"),
                "year": row.get("year"), "abstract": (row.get("abstract") or "")[:600],
                "url": url, "link_kind": kind,
                "free_url": row.get("free_url") or "", "pmcid": row.get("pmcid") or "",
                "doi_untrusted": bool(row.get("doi_untrusted")),
                "pool_size": len(papers), "pool_source": cache.get("source"),
                "pool_generated_at": cache.get("generated_at"),
                "pool_lines_scanned": cache.get("scanned_lines"),
                "link_stats": cache.get("link_stats") or {},
            }

        out["daily"] = pack(papers[idx])
        out["daily_list"] = [pack(papers[(idx + k) % len(papers)])
                             for k in range(min(3, len(papers)))]
    elif "daily_error" not in out:
        out["daily_error"] = "本地预览：候选池里没有可用条目"

    try:
        db = json.loads(FEED_DB.read_text(encoding="utf-8"))
    except Exception:
        db = {}

    if db.get("updates"):
        out["updates"] = db["updates"][: max(1, int(updates))]
    if db.get("years"):
        out["years"] = db["years"][: max(1, int(top_years))]
    if db.get("papers_total"):
        out["stats"] = {"papers": int(db["papers_total"])}
    st = db.get("maintenance") or {}
    out["maintenance"] = {
        "enabled": True, "running": False, "due": False, "interval_hours": 168.0,
        "last_ts": st.get("literature_last_ts") or 0,
        "last_result": st.get("literature_last_result") or "",
        "runs": st.get("runs"),
        "catalog_lines": cache.get("scanned_lines"),
        "knowledge_d_files": None,
        "progress_pmids": None,
    }
    out["_fetched_at"] = db.get("_fetched_at")
    return out


def genome_overview_payload() -> dict:
    """镜像 database/genome_toolkit.overview()（字段与线上一致）。

    数据来源：scripts/fetch_preview_data.py 从服务器抓的真实快照：
      · genome_info.json   本机 FASTA 实测的 25 条染色体长度 / GC
      · genome_preview.json  MySQL 按染色体基因数 + assembly_registry + BLAST 库文件大小
    """
    import re as _re

    try:
        info = json.loads((PREVIEW_DATA / "genome_info.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ready": False, "chromosomes": [],
                "error": "本地预览：缺 genome_info.json（先跑 scripts/fetch_preview_data.py）: %s" % e}
    try:
        g = json.loads((PREVIEW_DATA / "genome_preview.json").read_text(encoding="utf-8"))
    except Exception:
        g = {}

    per = g.get("chrom_counts") or {}
    chroms = []
    for seq in info.get("chromosomes") or []:
        name = str(seq.get("name") or "")
        chr_no = str(seq.get("chr") or "")
        if not chr_no:
            m = _re.match(r"^NC_(\d+)\.", name)
            chr_no = str(int(m.group(1)) - 133175) if m else ""
        length = int(seq.get("length") or 0)
        genes = int(per.get(chr_no, 0) or 0)
        chroms.append({
            "chromosome": chr_no, "accession": name, "length": length,
            "length_mb": round(length / 1048576, 1) if length else None,
            "gc": seq.get("gc"), "gene_count": genes,
            "gene_density": round(genes / (length / 1e6), 1) if length else 0.0,
        })
    chroms.sort(key=lambda r: (int(r["chromosome"]) if r["chromosome"].isdigit() else 99))

    lib_files = g.get("lib_files") or {}
    lib_items = [
        ("cdna_db", "转录组 BLAST 库（GRCz12tu CDS）", "Transcriptome BLAST DB (GRCz12tu CDS)",
         "cdna_db.nsq", "blastn 转录组脱靶筛查用", "for transcriptome off-target screening with blastn"),
        ("genome_db", "全基因组 BLAST 库（GRCz12tu）", "Whole-genome BLAST DB (GRCz12tu)",
         "genome_db.nsq", "blastn 全基因组脱靶筛查用", "for whole-genome off-target screening with blastn"),
        ("cds", "CDS FASTA（GRCz12tu）", "CDS FASTA (GRCz12tu)",
         "grcz12tu_cds.fna", "建库源序列", "source sequences for building the DB"),
        ("genome", "基因组 FASTA（GRCz12tu）", "Genome FASTA (GRCz12tu)",
         "grcz12tu_genome.fna", "建库源序列 / 染色体长度来源",
         "source sequences for the DB / source of chromosome lengths"),
    ]
    libraries = []
    for key, label, label_en, fname, note, note_en in lib_items:
        size = int(lib_files.get(fname) or 0)
        libraries.append({"key": key, "label": label, "label_en": label_en, "file": fname,
                          "note": note, "note_en": note_en,
                          "size_mb": round(size / 1048576, 1), "ready": size > 0})

    total_len = int(info.get("total_length") or 0)
    gc_vals = [c.get("gc") for c in chroms if c.get("gc") is not None]
    return {
        "ready": True, "error": None,
        "assembly": info.get("assembly") or "GRCz12tu",
        "source_fasta": info.get("source_fasta"),
        "n_sequences": info.get("n_sequences") or len(chroms),
        "total_length": total_len,
        "total_length_gb": round(total_len / 1e9, 2) if total_len else None,
        "mean_gc": round(sum(gc_vals) / len(gc_vals), 2) if gc_vals else None,
        "gene_total": g.get("total"), "gene_other": g.get("other"),
        "chromosomes": chroms,
        "assemblies": g.get("assemblies") or [],
        "libraries": libraries,
        "note": "染色体长度与 GC 为 GRCz12tu 序列实测（BLAST 建库用的同一份 FASTA，本地扫描得到）；"
                "基因数按本地注释库的染色体编号统计（本地注释坐标 = GRCz11 / Ensembl r116）。"
                "两者版本不同，仅用于看分布趋势，不做坐标换算。",
        "note_en": "Chromosome lengths and GC are measured on the GRCz12tu sequences (the same FASTA "
                   "the BLAST databases were built from, scanned locally); gene counts are grouped by "
                   "chromosome in the local annotation library (local annotation coordinates = GRCz11 / "
                   "Ensembl release 116). The two use different assembly versions, so they are shown to "
                   "indicate distribution only — no coordinate conversion is applied.",
        "_mock": True,
        "_note": "本地预览：数据为服务器真实快照（非实时）",
    }


def chromosome_genes_payload(chrom: str, limit: int = 60) -> dict:
    """镜像 /api/genome/chromosome：从快照里取该染色体的基因示例。"""
    import re as _re

    try:
        g = json.loads((PREVIEW_DATA / "genome_preview.json").read_text(encoding="utf-8"))
    except Exception:
        g = {}
    if not _re.fullmatch(r"\d+", str(chrom or "")):
        return {"ok": True, "chromosome": chrom, "count": 0, "genes": []}
    cap = max(1, min(int(limit or 60), 300))
    genes = (g.get("chrom_genes") or {}).get(str(chrom), [])[:cap]
    return {"ok": True, "chromosome": chrom, "count": len(genes), "genes": genes,
            "_mock": True}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):        # 静默
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):                        # noqa: N802
        self.do_GET()

    def _json(self, obj: dict, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ---------------- API（反代到真实后端） ----------------
    def _proxy(self) -> bool:
        """--api= 指定后端时，把 /api/* 原样转发过去（含 query / body / 方法）。

        为什么需要：本地没有 Nginx，预览服务只能自己扮演「静态映射 + 反代」两件事。
        不加这一层，前端点「按坐标提取」永远只看到 501 preview server: no API，
        会让人误判成线上缺接口。
        """
        if not API_BASE:
            return False
        import urllib.error
        import urllib.request

        target = API_BASE.rstrip("/") + self.path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {}
        for k, v in self.headers.items():
            if k.lower() in ("host", "connection", "accept-encoding", "content-length"):
                continue
            headers[k] = v
        req = urllib.request.Request(target, data=body, headers=headers,
                                     method=self.command)
        try:
            resp = urllib.request.urlopen(req, timeout=180)
        except urllib.error.HTTPError as e:      # 4xx/5xx 也要把后端原文透出去
            payload = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type") or "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
            return True
        except Exception as e:                    # noqa: BLE001
            msg = json.dumps({"error": "preview proxy failed", "target": target,
                              "reason": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
            self._send(502, msg.encode("utf-8"), "application/json; charset=utf-8")
            return True

        ctype = resp.headers.get("Content-Type") or "application/json"
        if "text/event-stream" in ctype:
            # SSE：头先发，之后边收边转；客户端断开就结束，不缓冲整条流
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    chunk = resp.read(1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                resp.close()
            return True

        payload = resp.read()
        self.send_response(resp.status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)
        resp.close()
        return True

    # ---------------- API（离线演示） ----------------
    def _api(self, path: str) -> bool:
        if not MOCK:
            return False
        if path == "/api/hub/stats":
            self._json(stats_payload())
        elif path == "/api/hub/stream-version":
            self._json(version_payload())
        elif path == "/api/hub/bg-pools":
            self._json(bg_pools_payload())
        elif path == "/api/hub/gallery":
            self._json(gallery_payload())
        elif path == "/api/genome/overview":
            self._json(genome_overview_payload())
        elif path == "/api/genome/chromosome":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                _lim = int((qs.get("limit") or ["60"])[0])
            except ValueError:
                _lim = 60
            self._json(chromosome_genes_payload((qs.get("chrom") or [""])[0], _lim))
        elif path == "/api/hub/maintenance/status":
            self._json(maintenance_payload())
        elif path == "/api/hub/research-feed":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

            def _int(key: str, default: int) -> int:
                try:
                    return int((qs.get(key) or [str(default)])[0])
                except ValueError:
                    return default

            self._json(research_feed_payload(
                day=(qs.get("day") or [""])[0],
                updates=_int("updates", 6),
                top_years=_int("top_years", 6),
            ))
        elif path == "/api/hub/__touch":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            with _LOCK:
                _TOUCH["n"] += 1
                if qs.get("lit", ["0"])[0] not in ("0", "", "false"):
                    try:
                        _TOUCH["lit"] += max(1, int(qs["lit"][0]))
                    except ValueError:
                        _TOUCH["lit"] += 1
            self._json({"ok": True, "touch": _TOUCH["n"], "lit": _TOUCH["lit"], **version_payload()})
        elif path == "/api/hub/events":
            self._sse()
        else:
            return False
        return True

    def _sse(self) -> None:
        """SSE：版本变化即推送 —— 用于端到端验证 web/js/live-update.js。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 5000\n\n")
            self.wfile.flush()
            last = ""
            hb = time.time()
            t0 = time.time()
            while time.time() - t0 < 300:
                cur = version_payload()
                ver = cur.get("version") or ""
                if ver and ver != last:
                    last = ver
                    data = json.dumps(cur, ensure_ascii=False)
                    self.wfile.write(f"event: version\ndata: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    hb = time.time()
                elif time.time() - hb >= 20:
                    hb = time.time()
                    self.wfile.write(f": ping {int(time.time())}\n\n".encode("utf-8"))
                    self.wfile.flush()
                time.sleep(3)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def do_POST(self):                        # noqa: N802
        if self.path.split("?", 1)[0].startswith("/api/") and self._proxy():
            return
        self._send(501, b'{"error":"preview server: no API"}', "application/json; charset=utf-8")

    do_PUT = do_POST                          # noqa: N815
    do_DELETE = do_POST                       # noqa: N815

    def do_GET(self):                         # noqa: N802
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/api/"):
            if self._proxy() or self._api(path):
                return
            self._send(501, b'{"error":"preview server: no API"}', "application/json; charset=utf-8")
            return
        if path in ("/", "/index.html"):
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]
        else:
            rel = path.lstrip("/")
        target = (WEB / rel).resolve()
        if WEB.resolve() not in target.parents and target != WEB.resolve():
            self._send(403, b"forbidden")
            return
        if not target.is_file():
            if not Path(rel).suffix:          # 无扩展名 -> 交给前端路由
                target = WEB / "index.html"
            else:
                self._send(404, b"not found")
                return
        self._send(200, target.read_bytes(), MIME.get(target.suffix.lower(), "application/octet-stream"))


def main() -> int:
    global MOCK, API_BASE
    rest = sys.argv[1:]
    if "--no-mock" in rest:
        MOCK = False
    for a in rest:
        if a.startswith("--api="):
            API_BASE = a.split("=", 1)[1].strip().rstrip("/")
    args = [a for a in rest if not a.startswith("--")]
    port = int(args[0]) if args else 8099
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler) as httpd:
        httpd.daemon_threads = True
        print(f"[preview] http://127.0.0.1:{port}/  (web={WEB})")
        if API_BASE:
            print(f"[preview] /api/* -> {API_BASE}（真实后端，反代）")
        else:
            print("[preview] " + ("离线演示 API 已开（真实快照 2026-09-01，非实时）" if MOCK
                                  else "仅静态映射（/api/* -> 501），加 --api=<后端地址> 可连真实接口"))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
