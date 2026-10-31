# -*- coding: utf-8 -*-
"""斑马鱼研究图实时源：本地 + iNaturalist 最新/热度 + Commons + 可选 Openverse。

策略：
- HTTP 瞬时返回本地/缓存（不堵首屏）
- 后台拉外网 → 镜像到 /static/assets/zf-gallery/live/（同域可显示、量更大）
- 前端可轮询直到 warming=false
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

UA = "ZebrafishHubGalleryFeed/1.1 (research UI; open-license; mirror locally)"
COMMONS = "https://commons.wikimedia.org/w/api.php"
INAT = "https://api.inaturalist.org/v1/observations"
OPENVERSE = "https://api.openverse.org/v1/images/"

_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
_CACHE_TTL = 900  # 15 min 更跟得上「最新/热度」
_WARMING = False
_WARM_LOCK = threading.Lock()


def _safe_int(v: Any, default: int = 0) -> int:
    """iNat 的 votes 常为 list，不能直接 int()。"""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, list):
        return len(v)
    if isinstance(v, dict):
        return _safe_int(v.get("score") or v.get("count") or v.get("total"), default)
    if isinstance(v, str):
        s = v.strip()
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s)
        try:
            return int(float(s))
        except Exception:
            return default
    return default


def _http_json(url: str, timeout: int = 8) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _http_bytes(url: str, timeout: int = 12) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if len(data) < 1200:
            return None
        return data
    except Exception:
        return None


def _live_dir(web_dir: str | Path) -> Path:
    d = Path(web_dir) / "assets" / "zf-gallery" / "live"
    d.mkdir(parents=True, exist_ok=True)
    return d


# 内容指纹缓存：同一路径在 size/mtime 未变时不再重读磁盘
_HASH_CACHE: dict[tuple[str, int, int], str] = {}


def _content_key(path: Path) -> str:
    """图片内容指纹（sha1）。同一张图不管存在几个文件名下，指纹相同。"""
    try:
        st = path.stat()
    except OSError:
        return ""
    ck = (str(path), int(st.st_size), int(st.st_mtime))
    hit = _HASH_CACHE.get(ck)
    if hit:
        return hit
    h = hashlib.sha1()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
    except OSError:
        return ""
    _HASH_CACHE[ck] = h.hexdigest()
    return _HASH_CACHE[ck]


def local_file_index(web_dir: str | Path) -> dict[str, str]:
    """站内图库的内容指纹 → URL（用于镜像前查重；只扫图片文件）。"""
    root = Path(web_dir) / "assets" / "zf-gallery"
    idx: dict[str, str] = {}
    exts = (".jpg", ".jpeg", ".png", ".webp")
    for p in list(root.glob("*.*")) + list((root / "live").glob("*.*")):
        if p.suffix.lower() not in exts:
            continue
        k = _content_key(p)
        if not k:
            continue
        rel = p.relative_to(root).as_posix()
        idx.setdefault(k, f"/static/assets/zf-gallery/{rel}")
    return idx


def _local_items(web_dir: str | Path) -> list[dict]:
    out: list[dict] = []
    root = Path(web_dir) / "assets" / "zf-gallery"
    # 按内容指纹去重：live/ 镜像图与静态 img_0xx 常为同一张图（仅文件不同名）
    seen_key: set[str] = set()
    seen_url: set[str] = set()
    dup_dropped = 0

    def _push(item: dict, fs_path: Path | None) -> None:
        nonlocal dup_dropped
        url = item.get("url") or ""
        if not url or url in seen_url:
            dup_dropped += 1
            return
        key = _content_key(fs_path) if fs_path is not None else ""
        if key and key in seen_key:
            dup_dropped += 1
            return
        if key:
            seen_key.add(key)
            item["content_key"] = key
        seen_url.add(url)
        out.append(item)

    # live/ 优先：最新镜像排在前面，首页/珊瑚流先看到「新图」
    live = root / "live"
    if live.is_dir():
        for p in sorted(live.glob("*.*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            meta_path = p.with_suffix(p.suffix + ".json")
            meta = {}
            if meta_path.is_file():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            _push(
                {
                    "url": f"/static/assets/zf-gallery/live/{p.name}",
                    "title": meta.get("title") or p.stem,
                    "license": meta.get("license") or "",
                    "artist": meta.get("artist") or "",
                    "landing": meta.get("landing") or "",
                    "source": meta.get("source") or "live-mirror",
                    "rank": meta.get("rank") or "latest",
                    "timestamp": meta.get("timestamp") or "",
                    "score": meta.get("score") or 0,
                    "mirrored": True,
                },
                p,
            )
    man = root / "manifest.json"
    if man.is_file():
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
            for it in data.get("items") or []:
                fn = it.get("file")
                if not fn:
                    continue
                _push(
                    {
                        "url": f"/static/assets/zf-gallery/{fn}",
                        "title": it.get("title") or fn,
                        "license": it.get("license") or "",
                        "artist": it.get("artist") or "",
                        "landing": it.get("commons") or "",
                        "source": "local",
                        "rank": "cached",
                        "timestamp": it.get("timestamp") or "",
                    },
                    root / fn,
                )
        except Exception:
            pass
    if dup_dropped:
        _LAST_LOCAL_DEDUP["dropped"] = dup_dropped
    return out


_LAST_LOCAL_DEDUP: dict[str, int] = {"dropped": 0}


def _inat(order_by: str, pages: int = 4, per_page: int = 50, deadline_sec: float = 35.0) -> list[dict]:
    out = []
    t_end = time.time() + max(8.0, float(deadline_sec))
    for page in range(1, pages + 1):
        if time.time() >= t_end:
            break
        qs = urllib.parse.urlencode(
            {
                "taxon_name": "Danio rerio",
                "photos": "true",
                "quality_grade": "research,needs_id",
                "per_page": str(per_page),
                "page": str(page),
                "order": "desc",
                "order_by": order_by,
            }
        )
        try:
            remain = max(3.0, t_end - time.time())
            data = _http_json(f"{INAT}?{qs}", timeout=min(12, int(remain)))
        except Exception:
            break
        results = data.get("results") or []
        if not results:
            break
        for obs in results:
            try:
                score = (
                    _safe_int(obs.get("faves_count"))
                    + _safe_int(obs.get("votes")) * 2
                    + _safe_int(obs.get("comments_count"))
                    + _safe_int(obs.get("identifications_count"))
                )
                for ph in obs.get("photos") or []:
                    url = (
                        (ph.get("url") or "")
                        .replace("square", "large")
                        .replace("small", "large")
                        .replace("medium", "large")
                    )
                    if not url:
                        continue
                    lic = (ph.get("license_code") or obs.get("license_code") or "") or ""
                    if lic.lower() in ("all-rights-reserved", "reserved"):
                        continue
                    out.append(
                        {
                            "url": url,
                            "title": f"iNat {obs.get('id')}",
                            "license": lic,
                            "artist": (obs.get("user") or {}).get("login") or "",
                            "landing": obs.get("uri")
                            or f"https://www.inaturalist.org/observations/{obs.get('id')}",
                            "source": "inaturalist",
                            "rank": "hot" if order_by == "votes" else "latest",
                            "timestamp": obs.get("created_at") or obs.get("observed_on") or "",
                            "score": score,
                        }
                    )
            except Exception:
                continue
        time.sleep(0.12)
    return out


def _commons_search(query: str, limit: int = 50, soft: int = 6) -> list[dict]:
    out = []
    sroffset = 0
    while len(out) < limit:
        qs = urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srnamespace": "6",
                "srlimit": "40",
                "sroffset": str(sroffset),
                "srsort": "create_timestamp_desc",
                "format": "json",
            }
        )
        try:
            data = _http_json(f"{COMMONS}?{qs}", timeout=timeout)
        except Exception:
            break
        titles = [h["title"] for h in (data.get("query") or {}).get("search") or []]
        if not titles:
            break
        for i in range(0, len(titles), 20):
            batch = titles[i : i + 20]
            qs2 = urllib.parse.urlencode(
                {
                    "action": "query",
                    "titles": "|".join(batch),
                    "prop": "imageinfo",
                    "iiprop": "url|mime|extmetadata|timestamp",
                    "iiurlwidth": "720",
                    "format": "json",
                }
            )
            try:
                info = _http_json(f"{COMMONS}?{qs2}", timeout=timeout)
            except Exception:
                break
            for page in (info.get("query") or {}).get("pages", {}).values():
                if "imageinfo" not in page:
                    continue
                ii = page["imageinfo"][0]
                mime = (ii.get("mime") or "").lower()
                if not mime.startswith("image/") or "svg" in mime or "gif" in mime or "tiff" in mime:
                    continue
                url = ii.get("thumburl") or ii.get("url")
                if not url:
                    continue
                meta = ii.get("extmetadata") or {}
                artist = re.sub(
                    r"<[^>]+>", "", (meta.get("Artist", {}) or {}).get("value") or ""
                ).strip()[:120]
                title = page.get("title") or ""
                # 科研向加权
                low = title.lower()
                score = 2
                for kw in ("confocal", "fluorescence", "microscop", "embryo", "larva", "crispr", "transgenic"):
                    if kw in low:
                        score += 3
                out.append(
                    {
                        "url": url,
                        "title": title,
                        "license": (meta.get("LicenseShortName", {}) or {}).get("value") or "",
                        "artist": artist,
                        "landing": "https://commons.wikimedia.org/wiki/"
                        + urllib.parse.quote(title.replace(" ", "_")),
                        "source": "commons",
                        "rank": "research" if score >= 5 else "latest",
                        "timestamp": ii.get("timestamp") or "",
                        "score": score,
                    }
                )
        cont = data.get("continue") or {}
        if "sroffset" not in cont:
            break
        sroffset = int(cont["sroffset"])
        time.sleep(0.25)
    return out[:limit]


def _openverse(limit: int = 40, timeout: int = 6) -> list[dict]:
    out = []
    for page in (1, 2):
        qs = urllib.parse.urlencode(
            {
                "q": 'zebrafish OR "Danio rerio" (microscopy OR embryo OR fluorescence OR confocal)',
                "license": "cc0,by,by-sa,pdm",
                "page": str(page),
                "page_size": "40",
                "mature": "false",
            }
        )
        try:
            data = _http_json(f"{OPENVERSE}?{qs}", timeout=timeout)
        except Exception:
            break
        for it in data.get("results") or []:
            url = it.get("url") or ""
            if not url:
                continue
            out.append(
                {
                    "url": url,
                    "title": it.get("title") or "zebrafish",
                    "license": it.get("license") or "",
                    "artist": (it.get("creator") or "")[:120],
                    "landing": it.get("foreign_landing_url") or it.get("license_url") or "",
                    "source": "openverse",
                    "rank": "research",
                    "timestamp": it.get("created_on") or "",
                    "score": 4,
                }
            )
            if len(out) >= limit:
                return out
        time.sleep(0.2)
    return out


def _dedupe(items: list[dict]) -> list[dict]:
    """按 URL 与**内容指纹**双重去重（同一张图不同文件名只保留先出现的一个）。"""
    seen: set[str] = set()
    seen_hash: set[str] = set()
    out = []
    for it in items:
        key = (it.get("url") or "").split("?")[0]
        if not key or key in seen:
            continue
        ck = it.get("content_key") or ""
        if ck and ck in seen_hash:
            continue
        seen.add(key)
        if ck:
            seen_hash.add(ck)
        out.append(it)
    return out


def _mirror_items(web_dir: str | Path, items: list[dict], max_n: int = 120) -> list[dict]:
    """把外网图落到本地 live/，浏览器走同域，显示更稳、更多。
    落盘前先按内容指纹查重：同一张图已在站内（静态库或 live/）时不重复保存，
    直接复用已有 URL——这是“图片重复出现”的根因治理。"""
    live = _live_dir(web_dir)
    existing = local_file_index(web_dir)           # 内容指纹 → 站内 URL
    dup_hits = 0
    # 先挑出需要镜像的外链（跳过已是本站路径的）
    todo: list[tuple[int, dict, str, Path, Path]] = []
    passthrough: list[tuple[int, dict]] = []
    for idx, it in enumerate(items):
        url = it.get("url") or ""
        if not url or url.startswith("/static/"):
            passthrough.append((idx, it))
            continue
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
        ext = ".jpg"
        low = url.lower().split("?")[0]
        if low.endswith(".png"):
            ext = ".png"
        elif low.endswith(".webp"):
            ext = ".webp"
        fname = f"{digest}{ext}"
        dest = live / fname
        meta_path = live / f"{fname}.json"
        if dest.is_file() and dest.stat().st_size >= 1200:
            local_it = dict(it)
            local_it["url"] = f"/static/assets/zf-gallery/live/{fname}"
            local_it["mirrored"] = True
            passthrough.append((idx, local_it))
            continue
        if len(todo) < max_n:
            todo.append((idx, it, url, dest, meta_path))
        else:
            passthrough.append((idx, it))

    def _one(job: tuple[int, dict, str, Path, Path]) -> tuple[int, dict]:
        nonlocal dup_hits
        idx, it, url, dest, meta_path = job
        blob = _http_bytes(url, timeout=14)
        if not blob:
            return idx, it
        key = hashlib.sha1(blob).hexdigest()
        known = existing.get(key)
        if known:                                   # 内容已存在 → 不落盘，复用既有 URL
            dup_hits += 1
            local_it = dict(it)
            local_it["url"] = known
            local_it["mirrored"] = True
            local_it["content_key"] = key
            local_it["deduped"] = True
            return idx, local_it
        try:
            dest.write_bytes(blob)
            meta_path.write_text(json.dumps(it, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return idx, it
        existing[key] = f"/static/assets/zf-gallery/live/{dest.name}"
        local_it = dict(it)
        local_it["url"] = f"/static/assets/zf-gallery/live/{dest.name}"
        local_it["mirrored"] = True
        local_it["content_key"] = key
        return idx, local_it

    results: dict[int, dict] = {i: it for i, it in passthrough}
    if todo:
        with ThreadPoolExecutor(max_workers=8) as pool:
            fmap = {pool.submit(_one, j): j for j in todo}
            done, not_done = wait(list(fmap.keys()), timeout=100)
            for fut in done:
                try:
                    idx, it = fut.result()
                    results[idx] = it
                except Exception:
                    j = fmap[fut]
                    results[j[0]] = j[1]
            for fut in not_done:
                j = fmap[fut]
                results[j[0]] = j[1]
                fut.cancel()

    return [results[i] for i in sorted(results.keys())]


def build_gallery_feed(web_dir: str | Path, mode: str = "mix", limit: int = 200) -> dict:
    mode = (mode or "mix").lower()
    limit = max(40, min(int(limit or 200), 320))
    local = _local_items(web_dir)

    live_latest: list[dict] = []
    live_hot: list[dict] = []
    commons: list[dict] = []
    commons_sci: list[dict] = []
    openverse: list[dict] = []
    errors: list[str] = []

    # 先串行拉 iNat（国内可达）；Commons/Openverse 机房常不通，短超时且可跳过
    try:
        live_latest = _inat("created_at", 1, 40, deadline_sec=28)
    except Exception as e:
        errors.append(f"inat_latest:{type(e).__name__}:{e}")
    try:
        live_hot = _inat("votes", 1, 40, deadline_sec=28)
    except Exception as e:
        errors.append(f"inat_hot:{type(e).__name__}:{e}")

    # Commons/Openverse：仅当显式开启（ZEBRA_GALLERY_REMOTE=1），避免拖垮 Hub
    if os.environ.get("ZEBRA_GALLERY_REMOTE", "").strip() in ("1", "true", "yes"):
        pool = ThreadPoolExecutor(max_workers=3)
        try:
            jobs = {
                pool.submit(
                    _commons_search,
                    'zebrafish OR "Danio rerio" filetype:bitmap',
                    20,
                    4,
                ): "commons",
                pool.submit(
                    _commons_search,
                    "zebrafish (confocal OR fluorescence OR microscopy OR embryo OR transgenic) filetype:bitmap",
                    20,
                    4,
                ): "commons_sci",
                pool.submit(_openverse, 15, 4): "openverse",
            }
            done, not_done = wait(list(jobs.keys()), timeout=10)
            for fut in done:
                name = jobs[fut]
                try:
                    data = fut.result()
                    if name == "commons":
                        commons = data
                    elif name == "commons_sci":
                        commons_sci = data
                    else:
                        openverse = data
                except Exception as e:
                    errors.append(f"{name}:{type(e).__name__}:{e}")
            for fut in not_done:
                errors.append(f"{jobs[fut]}:timeout")
                fut.cancel()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    if mode == "latest":
        merged = (
            [x for x in local if x.get("rank") == "latest"]
            + live_latest
            + commons
            + local
        )
    elif mode == "hot":
        merged = (
            sorted(
                [x for x in local if x.get("rank") == "hot"] + live_hot,
                key=lambda x: x.get("score") or 0,
                reverse=True,
            )
            + sorted(commons_sci, key=lambda x: x.get("score") or 0, reverse=True)
            + local
        )
    elif mode == "research":
        merged = (
            sorted(
                [x for x in local if x.get("rank") == "research"] + commons_sci + openverse,
                key=lambda x: x.get("score") or 0,
                reverse=True,
            )
            + live_hot
            + commons
            + local
        )
    else:
        merged = live_hot + live_latest + commons_sci + commons + openverse + local

    raw = _dedupe(merged)
    # 增量镜像：已有大量 live 时少下，避免拖垮进程
    existing_live = sum(1 for x in local if "live/" in (x.get("url") or ""))
    mirror_cap = 24 if existing_live >= 60 else min(120, limit)
    items = _mirror_items(web_dir, raw, max_n=mirror_cap)
    items = _dedupe(items)[:limit]
    if not items and local:
        items = local[:limit]

    live_count = sum(
        1
        for x in items
        if x.get("mirrored")
        or x.get("source") not in ("local", None, "")
        or "live/" in (x.get("url") or "")
    )
    return {
        "ok": True,
        "mode": mode,
        "count": len(items),
        "local_count": len(local),
        "live_count": live_count,
        "refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ttl_sec": _CACHE_TTL,
        "sources": {
            "local": len(local),
            "inat_latest": len(live_latest),
            "inat_hot": len(live_hot),
            "commons": len(commons),
            "commons_sci": len(commons_sci),
            "openverse": len(openverse),
        },
        "errors": errors,
        "dedupe": {"local_dropped": _LAST_LOCAL_DEDUP.get("dropped", 0)},
        "note": "开放许可聚合：iNaturalist / Commons 科研检索 / Openverse；已镜像到本站 live/（按内容指纹去重）",
        "warming": False,
        "items": items,
    }


def _local_payload(web_dir: str | Path, mode: str, limit: int, warming: bool = False) -> dict:
    local = _local_items(web_dir)
    mode = (mode or "mix").lower()
    if mode == "latest":
        ranked = [x for x in local if x.get("rank") == "latest"] + local
    elif mode == "hot":
        ranked = sorted(local, key=lambda x: x.get("score") or 0, reverse=True)
    elif mode == "research":
        ranked = (
            [x for x in local if x.get("rank") == "research"]
            + sorted(local, key=lambda x: x.get("score") or 0, reverse=True)
        )
    else:
        ranked = local
    items = _dedupe(ranked)[:limit]
    return {
        "ok": True,
        "mode": mode,
        "count": len(items),
        "local_count": len(local),
        "live_count": sum(1 for x in items if "live/" in (x.get("url") or "")),
        "refreshed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ttl_sec": _CACHE_TTL,
        "sources": {"local": len(local)},
        "errors": [],
        "note": "本地+已镜像图瞬时响应；后台补齐最新/高热/科研图" if warming else "本地图库",
        "warming": warming,
        "items": items,
    }


def _warm_cache(web_dir: str | Path, mode: str, limit: int, key: str) -> None:
    global _WARMING
    try:
        payload = build_gallery_feed(web_dir, mode=mode, limit=limit)
        payload["_key"] = key
        payload["cached"] = False
        payload["warming"] = False
        _CACHE["ts"] = time.time()
        _CACHE["payload"] = payload
        try:
            rt = os.environ.get("ZEBRA_RUNTIME") or ""
            if rt:
                path = Path(rt) / "logs" / "_gallery_feed_cache.json"
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                err_path = Path(rt) / "logs" / "_gallery_warm_last.txt"
                err_path.write_text(
                    f"ok count={payload.get('count')} live={payload.get('live_count')} "
                    f"src={payload.get('sources')} err={payload.get('errors')}\n",
                    encoding="utf-8",
                )
        except Exception:
            pass
    except Exception as e:
        try:
            rt = os.environ.get("ZEBRA_RUNTIME") or ""
            if rt:
                Path(rt).joinpath("logs", "_gallery_warm_last.txt").write_text(
                    f"FAIL {type(e).__name__}: {e}\n", encoding="utf-8"
                )
        except Exception:
            pass
    finally:
        with _WARM_LOCK:
            _WARMING = False


def get_gallery_feed(web_dir: str | Path, mode: str = "mix", limit: int = 200, force: bool = False) -> dict:
    """瞬时返回 live 优先本地图；后台静默刷新。force=1 必清缓存并标 warming，便于前端轮询。"""
    global _WARMING
    key = f"{mode}:{limit}"
    now = time.time()
    cached = _CACHE.get("payload")

    if force:
        _CACHE["ts"] = 0
        _CACHE["payload"] = None
        cached = None
        try:
            rt = os.environ.get("ZEBRA_RUNTIME") or ""
            if rt:
                path = Path(rt) / "logs" / "_gallery_feed_cache.json"
                if path.is_file():
                    path.unlink()
        except Exception:
            pass

    if (
        not force
        and cached
        and cached.get("_key") == key
        and not cached.get("warming")
        and now - float(_CACHE.get("ts") or 0) < _CACHE_TTL
    ):
        out = dict(cached)
        out["cached"] = True
        # TTL 过半时后台再刷一轮
        if now - float(_CACHE.get("ts") or 0) > _CACHE_TTL * 0.55:
            with _WARM_LOCK:
                should = not _WARMING
                if should:
                    _WARMING = True
            if should:
                threading.Thread(
                    target=_warm_cache,
                    args=(web_dir, mode, limit, key),
                    daemon=True,
                    name="gallery-warm",
                ).start()
        return out

    if not force:
        try:
            rt = os.environ.get("ZEBRA_RUNTIME") or ""
            path = Path(rt) / "logs" / "_gallery_feed_cache.json" if rt else None
            if path and path.is_file() and now - path.stat().st_mtime < _CACHE_TTL:
                disk = json.loads(path.read_text(encoding="utf-8"))
                if disk.get("_key") == key and disk.get("items") and not disk.get("warming"):
                    disk["cached"] = True
                    _CACHE["ts"] = now
                    _CACHE["payload"] = disk
                    return disk
        except Exception:
            pass

    # force 或冷启动：瞬时本地图；force 必标 warming 以便前端轮询
    probe = _local_items(web_dir)
    live_n = sum(1 for x in probe if "live/" in (x.get("url") or ""))
    warming = bool(force) or live_n < 20

    instant = _local_payload(web_dir, mode, limit, warming=warming)
    if force:
        instant["warming"] = True
        instant["forced"] = True
        instant["note"] = "强制刷新已触发：后台正在拉最新/热度/科研图…"
        # 立即可见变化：旋转一段，避免「点了没反应」
        items = list(instant.get("items") or [])
        if len(items) > 6:
            mid = max(3, len(items) // 4)
            instant["items"] = items[mid:] + items[:mid]
            instant["count"] = len(instant["items"])
    elif live_n >= 20:
        instant["note"] = "已镜像 live 图优先展示；后台静默更新最新/热度"
    else:
        instant["note"] = "本地图较少，后台拉取最新/热度中…"
    instant["_key"] = key
    instant["cached"] = False
    _CACHE["ts"] = now
    _CACHE["payload"] = instant

    with _WARM_LOCK:
        # force 时即使正在 warm 也再踢一脚
        should = (not _WARMING) or force
        if should:
            _WARMING = True
    if should:
        threading.Thread(
            target=_warm_cache,
            args=(web_dir, mode, limit, key),
            daemon=True,
            name="gallery-warm-force" if force else "gallery-warm",
        ).start()
    return instant
