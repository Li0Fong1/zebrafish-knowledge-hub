# -*- coding: utf-8 -*-
"""
三日收口战役（只在服务器跑）：

  解析缺口 → 近门槛限流修复 → 新金标 GPU0 向量化 → 无法金标台账 → DB → 验收。

护栏：loadavg / MemAvailable / GPU0 空闲显存 / Hub chat_busy / 单实例 flock /
PDF 抽取池=1 / 不覆盖 gte / 不关主机 / 不碰 GPU1 Hub。

本机禁止跑本模块。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME = Path(os.environ.get("ZEBRA_RUNTIME", "/data/zoujin/deng/zebrafish_runtime"))
BASE = Path(os.environ.get("ZEBRA_BASE", str(ROOT)))
PY = os.environ.get(
    "ZEBRA_PY",
    "/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python",
)
LOG_DIR = RUNTIME / "logs" / "scheduler"
LOG = LOG_DIR / "three_day_closeout.log"
STATE = LOG_DIR / "three_day_state.json"
ACCEPT = LOG_DIR / "accept_latest.json"
LOCK = LOG_DIR / "three_day_closeout.lock"
PIDF = LOG_DIR / "three_day_closeout.pid"
INBOX_PARSE_LOG = RUNTIME / "logs" / "inbox_pdf_closeout.jsonl"
CAMPAIGN = "three_day_closeout"
FLOOR = 35487
NEAR_MIN = int(os.environ.get("CLOSEOUT_NEAR_MIN", "20000"))
MAX_LOAD_RATIO = float(os.environ.get("CLOSEOUT_MAX_LOAD_RATIO", "0.65"))
MIN_AVAIL_GIB = float(os.environ.get("CLOSEOUT_MIN_AVAIL_GIB", "22"))
GPU0_MIN_FREE_MB = int(os.environ.get("CLOSEOUT_GPU0_MIN_FREE_MB", "2000"))
REPAIR_BATCH = int(os.environ.get("CLOSEOUT_REPAIR_BATCH", "4"))
REPAIR_ROUNDS = int(os.environ.get("CLOSEOUT_REPAIR_ROUNDS", "2"))
REPAIR_MAX_SEC = int(os.environ.get("CLOSEOUT_REPAIR_MAX_SEC", str(36 * 3600)))
VEC_LIMIT = int(os.environ.get("CLOSEOUT_VEC_LIMIT", "0"))
SLEEP_BUSY = int(os.environ.get("CLOSEOUT_SLEEP_BUSY", "40"))


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _save(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def loadavg() -> float:
    try:
        return os.getloadavg()[0]
    except Exception:
        return 0.0


def mem_avail_gib() -> float:
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) / (1024 * 1024)
    except Exception:
        pass
    return 999.0


def gpu_free_mb() -> list[int]:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            text=True,
            timeout=20,
        )
        return [int(x.strip()) for x in out.strip().splitlines() if x.strip()]
    except Exception:
        return []


def hub_health() -> dict[str, Any]:
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/health", timeout=8) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"status": "down", "error": str(exc)}


def resources_ok(*, need_gpu0: bool = False) -> tuple[bool, str]:
    n = os.cpu_count() or 8
    la = loadavg()
    if la > n * MAX_LOAD_RATIO:
        return False, f"loadavg {la:.2f}>{n * MAX_LOAD_RATIO:.1f}"
    av = mem_avail_gib()
    if av < MIN_AVAIL_GIB:
        return False, f"mem {av:.1f}GiB<{MIN_AVAIL_GIB}"
    h = hub_health()
    if h.get("chat_busy"):
        return False, "chat_busy"
    if need_gpu0:
        frees = gpu_free_mb()
        if frees and frees[0] < GPU0_MIN_FREE_MB:
            return False, f"gpu0_free {frees[0]}MB<{GPU0_MIN_FREE_MB}"
    return True, "ok"


def acquire_lock() -> Any:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "w", encoding="utf-8")
    if os.name != "nt":
        import fcntl

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _log("another closeout holds lock; exit")
            fh.close()
            return None
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def _pgrep(pat: str) -> str:
    p = subprocess.run(["pgrep", "-af", pat], capture_output=True, text=True)
    return (p.stdout or "").strip()


def stop_wasteful() -> None:
    for pat in (
        "scripts.gpu0_embed_worker",
        "scripts.hub_gpu1_pulse",
        "scripts.dual_gpu_scheduler",
        "scripts.scihub_dual_gpu_vectorize",
    ):
        subprocess.run(["pkill", "-f", pat], capture_output=True)
    time.sleep(3)
    _log("stopped dummy gpu0 / pulse / extra scheduler / dual vec")


def wait_gpu0_drain(*, max_wait: int = 180) -> None:
    t0 = time.time()
    while time.time() - t0 < max_wait:
        used = []
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                text=True,
                timeout=20,
            )
            used = [int(x.strip()) for x in out.strip().splitlines() if x.strip()]
        except Exception:
            return
        if not used or used[0] < 2500:
            _log(f"gpu0 drained used={used}")
            return
        _log(f"wait gpu0 drain used={used}")
        time.sleep(6)
    _log("gpu0 drain wait timed out; continue")


def setup_paths() -> None:
    os.environ.setdefault("ZEBRA_RUNTIME", str(RUNTIME))
    os.environ.setdefault("ZEBRA_BASE", str(BASE))
    os.environ.setdefault("GOLD_OUT", str(RUNTIME / "gold_ingest" / "yes_fulltext_gold"))
    os.environ.setdefault(
        "SCIHUB_INBOX",
        str(RUNTIME / "gold_ingest" / "pdfku_inbox" / "scihub_batch"),
    )
    os.environ.setdefault("SCIHUB_PARSE_LOG", str(RUNTIME / "logs" / "scihub_gold_nohup.log"))
    os.environ.setdefault("FAISS_QWEN3_4B_DIR", str(RUNTIME / "faiss_pubmed_yes_qwen3_4b_v1"))
    os.environ.setdefault("QWEN3_4B_DIR", str(RUNTIME / "models" / "Qwen3-Embedding-4B"))
    os.chdir(BASE)
    if str(BASE) not in sys.path:
        sys.path.insert(0, str(BASE))
    from core.entry import bootstrap_app

    bootstrap_app()
    try:
        from scripts.gold_upgrade_5680 import set_pdf_extract_pool_size

        set_pdf_extract_pool_size(1)
    except Exception as exc:
        _log(f"set_pdf_extract_pool_size skip: {exc}")


def gold_json_path(eno: str) -> Path:
    try:
        n = int(eno)
    except ValueError:
        n = abs(hash(eno)) % 10_000_000
    gold_out = Path(os.environ.get("GOLD_OUT", str(RUNTIME / "gold_ingest" / "yes_fulltext_gold")))
    return gold_out / f"batch_{n // 100 + 1:04d}" / "papers" / f"{eno}.json"


def entry_no_of(pdf: Path) -> str:
    m = re.match(r"^(\d+)", pdf.stem)
    return m.group(1) if m else pdf.stem


def inbox_pdfs() -> list[Path]:
    folder = RUNTIME / "gold_ingest" / "pdfku_inbox" / "pdf"
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.pdf") if p.stat().st_size > 5000)


def parse_inbox_if_needed() -> dict[str, Any]:
    pdfs = inbox_pdfs()
    missing: list[Path] = []
    for pdf in pdfs:
        eno = entry_no_of(pdf)
        if not gold_json_path(eno).is_file():
            missing.append(pdf)
    _log(f"inbox pdf n={len(pdfs)} missing_json={len(missing)}")
    if not missing:
        return {"inbox": len(pdfs), "parsed": 0, "skipped": len(pdfs)}
    script = RUNTIME / "gold_ingest" / "scripts" / "gold_parse_on_server.py"
    if not script.is_file():
        _log(f"missing parse script {script}")
        return {"inbox": len(pdfs), "error": "no_script", "missing": len(missing)}
    env = os.environ.copy()
    env["PDF_PARSE_TIMEOUT"] = os.environ.get("PDF_PARSE_TIMEOUT", "180")
    env["PROJECT"] = str(BASE)
    env["GOLD_OUT"] = str(RUNTIME / "gold_ingest" / "yes_fulltext_gold")
    INBOX_PARSE_LOG.parent.mkdir(parents=True, exist_ok=True)
    parsed = 0
    gold_n = 0
    for pdf in missing:
        ok, why = resources_ok()
        if not ok:
            _log(f"inbox parse pause {why}")
            time.sleep(SLEEP_BUSY)
        cmd = [PY, "-u", str(script), "--pdf", str(pdf)]
        try:
            p = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=240,
            )
        except subprocess.TimeoutExpired:
            row = {"pdf": str(pdf), "status": "timeout", "gold": False, "entry_no": entry_no_of(pdf)}
            with INBOX_PARSE_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            _log(f"inbox timeout {pdf.name}")
            continue
        out = (p.stdout or "") + (p.stderr or "")
        last = None
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.startswith("{") and "entry_no" in ln:
                try:
                    last = json.loads(ln)
                except Exception:
                    continue
        if last:
            with INBOX_PARSE_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(last, ensure_ascii=False) + "\n")
            parsed += 1
            if last.get("gold"):
                gold_n += 1
            _log(f"inbox parsed {last.get('entry_no')} gold={last.get('gold')} body={last.get('body_len')}")
        else:
            _log(f"inbox parse no-json {pdf.name} rc={p.returncode} tail={out[-300:]}")
    return {"inbox": len(pdfs), "missing": len(missing), "parsed": parsed, "new_gold": gold_n}


def mysql_cmd() -> list[str]:
    binp = str(RUNTIME / "mysql80" / "bin" / "mysql")
    sock = str(RUNTIME / "mysql.sock")
    return [
        binp,
        f"--socket={sock}",
        "--protocol=SOCKET",
        "-uzebrafish",
        "-pzebrafish_remote_2026",
        "zebrafish_db",
        "-N",
        "-B",
    ]


def mysql_query(sql: str) -> str:
    p = subprocess.run(mysql_cmd(), input=sql + "\n", capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        tcp = mysql_cmd()
        tcp = [
            tcp[0],
            "-h127.0.0.1",
            "-P3307",
            "-uzebrafish",
            "-pzebrafish_remote_2026",
            "zebrafish_db",
            "-N",
            "-B",
        ]
        p = subprocess.run(tcp, input=sql + "\n", capture_output=True, text=True, timeout=60)
    text = (p.stdout or "") + (p.stderr or "")
    lines = [ln for ln in text.splitlines() if "Using a password" not in ln and "Warning" not in ln]
    return "\n".join(lines).strip()


def load_unreachable_ids() -> set[str]:
    from scripts.scihub_post_gold_pipeline import UNREACHABLE

    ids: set[str] = set()
    if not UNREACHABLE.is_file():
        return ids
    with UNREACHABLE.open(encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            try:
                o = json.loads(ln)
            except Exception:
                continue
            rid = str(o.get("id") or o.get("entry_no") or "").strip()
            if rid:
                ids.add(rid)
    return ids


def universe_enos() -> dict[str, dict[str, Any]]:
    from scripts.scihub_post_gold_pipeline import parse_log_rows

    rows = dict(parse_log_rows())
    if INBOX_PARSE_LOG.is_file():
        with INBOX_PARSE_LOG.open(encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                try:
                    o = json.loads(ln)
                except Exception:
                    continue
                eno = str(o.get("entry_no") or "").strip()
                if eno:
                    rows[eno] = o
    for pdf in inbox_pdfs():
        eno = entry_no_of(pdf)
        rows.setdefault(eno, {"entry_no": eno, "pdf": str(pdf), "status": "inbox_unparsed", "gold": False})
    return rows


def classify() -> dict[str, Any]:
    from scripts.gold_hunt_fulltext import is_pdf_gold
    from scripts.scihub_post_gold_pipeline import load_rec

    rows = universe_enos()
    gold: list[str] = []
    near: list[str] = []
    far: list[str] = []
    timeout: list[str] = []
    body_of: dict[str, int] = {}
    for eno, row in rows.items():
        if row.get("gold") and gold_json_path(eno).is_file():
            gold.append(eno)
            continue
        rec = load_rec(eno)
        if rec and is_pdf_gold(rec):
            gold.append(eno)
            continue
        bl = int(row.get("body_len") or 0)
        if rec:
            bl = max(bl, len(rec.get("body_text") or ""))
        body_of[eno] = bl
        st = str(row.get("status") or "")
        if st == "timeout":
            timeout.append(eno)
        elif bl >= NEAR_MIN:
            near.append(eno)
        else:
            far.append(eno)
    near_sorted = sorted(set(near + timeout), key=lambda e: body_of.get(e, 0), reverse=True)
    return {
        "gold": sorted(set(gold), key=lambda x: int(x) if x.isdigit() else x),
        "near": near_sorted,
        "far": sorted(set(far), key=lambda x: int(x) if x.isdigit() else x),
        "body_of": body_of,
        "rows": rows,
    }


def run_repair_pass() -> dict[str, Any]:
    setup_paths()
    from scripts.gold_hunt_fulltext import is_pdf_gold
    from scripts.scihub_post_gold_pipeline import load_rec, repair_one

    groups = classify()
    candidates = groups["near"]
    _log(
        f"classify gold={len(groups['gold'])} near={len(candidates)} far={len(groups['far'])}"
    )
    groups.pop("rows", None)
    groups.pop("body_of", None)
    t0 = time.time()
    stats: dict[str, Any] = {
        "tried": 0,
        "gold": 0,
        "still_short": 0,
        "new_gold": [],
        "paused": 0,
        "timed_out_budget": False,
    }
    for i in range(0, len(candidates), REPAIR_BATCH):
        if time.time() - t0 > REPAIR_MAX_SEC:
            stats["timed_out_budget"] = True
            _log(f"repair time budget {REPAIR_MAX_SEC}s hit at i={i}")
            break
        waits = 0
        while True:
            ok, why = resources_ok()
            if ok:
                break
            waits += 1
            stats["paused"] += 1
            _log(f"repair pause {why}")
            time.sleep(SLEEP_BUSY)
            if waits >= 30:
                _log("repair too many pauses; skip this batch")
                break
        if waits >= 30:
            continue
        batch = candidates[i : i + REPAIR_BATCH]
        for eno in batch:
            rec0 = load_rec(eno)
            if rec0 and is_pdf_gold(rec0):
                stats["gold"] += 1
                continue
            stats["tried"] += 1
            res = None
            for rnd in range(1, REPAIR_ROUNDS + 1):
                try:
                    res = repair_one(eno, round_i=rnd)
                except Exception as exc:
                    _log(f"repair_one {eno} exc={exc}")
                    res = {"gold": False, "status": "exception", "body_len": 0}
                if res.get("gold"):
                    break
            rec = load_rec(eno)
            if rec and is_pdf_gold(rec):
                stats["gold"] += 1
                stats["new_gold"].append(eno)
            else:
                stats["still_short"] += 1
            if stats["tried"] % 10 == 0:
                _log(f"repair progress { {k: stats[k] for k in ('tried','gold','still_short','paused')} }")
                _save(STATE, {"phase": "repair", **{k: stats[k] for k in stats if k != "new_gold"}, "i": i})
    stats["elapsed_sec"] = int(time.time() - t0)
    stats["new_gold_n"] = len(stats["new_gold"])
    _save(STATE, {"phase": "repair_done", **{k: v for k, v in stats.items() if k != "new_gold"}, "new_gold_n": stats["new_gold_n"]})
    return stats


def run_vectorize() -> dict[str, Any]:
    wait_gpu0_drain(max_wait=120)
    ok, why = resources_ok(need_gpu0=True)
    waits = 0
    while not ok and waits < 40:
        _log(f"vectorize wait {why}")
        time.sleep(SLEEP_BUSY)
        wait_gpu0_drain(max_wait=30)
        ok, why = resources_ok(need_gpu0=True)
        waits += 1
    env = os.environ.copy()
    env.update(
        {
            "ZEBRA_RUNTIME": str(RUNTIME),
            "ZEBRA_BASE": str(BASE),
            "CUDA_VISIBLE_DEVICES": "0",
            "QWEN3_4B_EMBED_BATCH": env.get("QWEN3_4B_EMBED_BATCH", "4"),
            "QWEN3_4B_DOC_MICRO": env.get("QWEN3_4B_DOC_MICRO", "32"),
            "GOLD_OUT": str(RUNTIME / "gold_ingest" / "yes_fulltext_gold"),
            "SCIHUB_PARSE_LOG": str(RUNTIME / "logs" / "scihub_gold_nohup.log"),
            "FAISS_QWEN3_4B_DIR": str(RUNTIME / "faiss_pubmed_yes_qwen3_4b_v1"),
            "QWEN3_4B_DIR": str(RUNTIME / "models" / "Qwen3-Embedding-4B"),
        }
    )
    cmd = [PY, "-u", "-m", "scripts.scihub_post_gold_pipeline", "--mode", "vectorize", "--gpu", "0"]
    if VEC_LIMIT:
        cmd.extend(["--limit", str(VEC_LIMIT)])
    _log(f"vectorize start cmd={' '.join(cmd)}")
    p = subprocess.run(
        cmd,
        cwd=str(BASE),
        env=env,
        capture_output=True,
        text=True,
        timeout=8 * 3600,
    )
    tail = ((p.stdout or "") + (p.stderr or ""))[-800:]
    _log(f"vectorize rc={p.returncode} tail={tail}")
    return {"rc": p.returncode, "tail": tail[-400:]}


def ledger_remaining() -> int:
    setup_paths()
    from scripts.gold_hunt_fulltext import is_pdf_gold
    from scripts.scihub_post_gold_pipeline import find_inbox_pdf, load_rec, record_unreachable

    already = load_unreachable_ids()
    rows = universe_enos()
    n = 0
    for eno, row in rows.items():
        rec = load_rec(eno)
        if rec and is_pdf_gold(rec):
            continue
        if eno in already:
            continue
        pdf = str(find_inbox_pdf(eno) or row.get("pdf") or "")
        bl = int(row.get("body_len") or 0)
        if rec:
            bl = max(bl, len(rec.get("body_text") or ""))
        reason = str(row.get("status") or "short_or_fail")
        if bl < NEAR_MIN:
            reason = f"short_lt_{NEAR_MIN}_{reason}"
        else:
            reason = f"still_short_after_repair_{reason}"
        record_unreachable(
            eno,
            reason,
            pdf=pdf,
            body_len=bl,
            rounds=REPAIR_ROUNDS,
            campaign=CAMPAIGN,
            source="three_day_closeout",
        )
        already.add(eno)
        n += 1
        if n % 200 == 0:
            _log(f"ledgered {n}")
    _log(f"ledger remaining not-gold n={n}")
    return n


def run_register() -> dict[str, Any]:
    env = os.environ.copy()
    env["ZEBRA_RUNTIME"] = str(RUNTIME)
    env["ZEBRA_BASE"] = str(BASE)
    p = subprocess.run(
        [PY, "-u", "-m", "scripts.scihub_post_gold_pipeline", "--mode", "register"],
        cwd=str(BASE),
        env=env,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    tail = ((p.stdout or "") + (p.stderr or ""))[-500:]
    _log(f"register rc={p.returncode} {tail}")
    return {"rc": p.returncode, "tail": tail[-300:]}


def accept_now() -> dict[str, Any]:
    setup_paths()
    from scripts.gold_hunt_fulltext import is_pdf_gold
    from scripts.scihub_post_gold_pipeline import DONE_IDS, FAISS_DIR, GTE_DIR, _load_json, load_rec

    rows = universe_enos()
    gold_enos: list[str] = []
    not_enos: list[str] = []
    for eno, row in rows.items():
        if row.get("gold") and gold_json_path(eno).is_file():
            gold_enos.append(eno)
            continue
        rec = load_rec(eno)
        if rec and is_pdf_gold(rec):
            gold_enos.append(eno)
        else:
            not_enos.append(eno)
    done = set(str(x) for x in _load_json(DONE_IDS, []))
    pending_vec = [e for e in gold_enos if e not in done]
    bad_gold = 0
    checked = 0
    for eno in gold_enos[:120]:
        rec = load_rec(eno)
        checked += 1
        if not rec or not is_pdf_gold(rec):
            bad_gold += 1
    un_ids = load_unreachable_ids()
    missing_un = [e for e in not_enos if e not in un_ids]
    mf: dict[str, Any] = {}
    if (FAISS_DIR / "manifest.json").is_file():
        mf = json.loads((FAISS_DIR / "manifest.json").read_text(encoding="utf-8"))
    gte_ok = (GTE_DIR / "manifest.json").is_file()
    h = hub_health()
    db_ready = False
    db_body = False
    db_err = ""
    try:
        ready_s = mysql_query("SELECT COUNT(*) FROM literature_paper WHERE gold_status='ready'")
        body_s = mysql_query(
            "SELECT SUM(IFNULL(body_path,'')!=''), COUNT(*) FROM literature_fulltext_gold"
        )
        db_ready = int((ready_s.split() or ["0"])[0] or 0) > 0
        parts = body_s.replace("\t", " ").split()
        db_body = len(parts) >= 2 and parts[0] == parts[1] and int(parts[0] or 0) > 0
    except Exception as exc:
        db_err = str(exc)
    missing_db = 0
    for i in range(0, min(len(gold_enos), 4000), 200):
        chunk = gold_enos[i : i + 200]
        ins = ",".join("'" + e.replace("'", "") + "'" for e in chunk)
        got = mysql_query(
            f"SELECT entry_no FROM literature_paper WHERE gold_status='ready' AND entry_no IN ({ins})"
        )
        have = {ln.strip() for ln in got.splitlines() if ln.strip()}
        missing_db += sum(1 for e in chunk if e not in have)
    wd_alive = bool(_pgrep("scripts.three_hour_watchdog"))
    checks = {
        "universe_logged": len(rows) >= 13841,
        "gold_vectorized": len(pending_vec) == 0,
        "gold_sample_still_gold": bad_gold == 0,
        "not_gold_ledgered": len(missing_un) == 0,
        "faiss_ready": mf.get("status") == "ready",
        "faiss_dim_2048": int(mf.get("embedding_dim") or 0) == 2048,
        "gte_preserved": gte_ok,
        "hub_ok": h.get("status") == "ok" and bool(h.get("pubmed_ready")),
        "db_ready_positive": db_ready,
        "db_body_path_aligned": db_body,
        "db_gold_sample_ready": missing_db == 0,
    }
    report = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "pass": all(checks.values()),
        "checks": checks,
        "counts": {
            "universe": len(rows),
            "gold": len(gold_enos),
            "not_gold": len(not_enos),
            "pending_vec": len(pending_vec),
            "missing_unreachable": len(missing_un),
            "bad_gold_sample": bad_gold,
            "gold_sample_n": checked,
            "missing_db_ready": missing_db,
            "faiss_papers": mf.get("total_papers"),
            "faiss_shards": len(mf.get("shards") or []),
            "watchdog_alive": wd_alive,
        },
        "pending_vec_sample": pending_vec[:20],
        "missing_un_sample": missing_un[:20],
        "hub": {"status": h.get("status"), "pubmed_ready": h.get("pubmed_ready"), "mysql": h.get("mysql")},
        "db_err": db_err,
        "resources": {
            "loadavg": loadavg(),
            "mem_avail_gib": round(mem_avail_gib(), 2),
            "gpu_free_mb": gpu_free_mb(),
        },
    }
    _save(ACCEPT, report)
    _log(f"ACCEPT pass={report['pass']} checks={checks} counts={report['counts']}")
    return report


def fix_from_accept(rep: dict[str, Any]) -> dict[str, Any]:
    ch = rep.get("checks") or {}
    acted: list[str] = []
    if not ch.get("gold_vectorized"):
        run_vectorize()
        acted.append("vectorize")
    if not ch.get("not_gold_ledgered"):
        ledger_remaining()
        acted.append("ledger")
    if not ch.get("db_gold_sample_ready") or not ch.get("db_body_path_aligned") or not ch.get("db_ready_positive"):
        run_register()
        acted.append("register")
    if not ch.get("hub_ok"):
        _log("hub not ok; watchdog may restart; closeout will not kill hub")
        acted.append("hub_flag")
    return {"acted": acted}


def closeout() -> int:
    lock = acquire_lock()
    if lock is None:
        return 0
    try:
        os.nice(10)
    except Exception:
        pass
    PIDF.write_text(str(os.getpid()), encoding="utf-8")
    _log("=== three_day_closeout START ===")
    stop_wasteful()
    wait_gpu0_drain()
    inbox = parse_inbox_if_needed()
    _log(f"inbox {inbox}")
    # 先嵌已有金标（含 inbox 已解析待嵌），再修近门槛 —— 最快先闭环向量化
    _log("phase early vectorize pending gold")
    early_vec = run_vectorize()
    _log(f"early vectorize {early_vec.get('rc')}")
    # ledger hopeless shorts first so coverage is already high if repair is killed
    setup_paths()
    groups = classify()
    from scripts.scihub_post_gold_pipeline import find_inbox_pdf, record_unreachable

    already = load_unreachable_ids()
    far_n = 0
    body_of = groups.get("body_of") or {}
    for eno in groups["far"]:
        if eno in already:
            continue
        bl = int(body_of.get(eno) or (groups.get("rows") or {}).get(eno, {}).get("body_len") or 0)
        record_unreachable(
            eno,
            f"short_lt_{NEAR_MIN}",
            pdf=str(find_inbox_pdf(eno) or ""),
            body_len=bl,
            rounds=0,
            campaign=CAMPAIGN,
            source="three_day_closeout",
        )
        far_n += 1
    _log(f"pre-ledger far shorts n={far_n}")
    stats = run_repair_pass()
    _log(f"repair done { {k: stats[k] for k in stats if k != 'new_gold'} }")
    vec = run_vectorize()
    led = ledger_remaining()
    reg = run_register()
    rep = accept_now()
    if not rep.get("pass"):
        _log("accept FAIL → mapped fix")
        fix_from_accept(rep)
        rep = accept_now()
    _save(
        STATE,
        {
            "phase": "done",
            "pass": bool(rep.get("pass")),
            "inbox": inbox,
            "early_vectorize_rc": early_vec.get("rc"),
            "repair": {k: v for k, v in stats.items() if k != "new_gold"},
            "vectorize_rc": vec.get("rc"),
            "ledger_n": led,
            "register_rc": reg.get("rc"),
        },
    )
    _log(f"=== closeout END pass={rep.get('pass')} ===")
    return 0 if rep.get("pass") else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accept-only", action="store_true")
    ap.add_argument("--vectorize-only", action="store_true")
    ap.add_argument("--ledger-only", action="store_true")
    ap.add_argument("--register-only", action="store_true")
    ap.add_argument("--fix-gaps", action="store_true")
    args = ap.parse_args()
    if args.accept_only:
        r = accept_now()
        return 0 if r.get("pass") else 2
    if args.vectorize_only:
        run_vectorize()
        return 0
    if args.ledger_only:
        ledger_remaining()
        return 0
    if args.register_only:
        run_register()
        return 0
    if args.fix_gaps:
        r = accept_now()
        if r.get("pass"):
            return 0
        fix_from_accept(r)
        r2 = accept_now()
        return 0 if r2.get("pass") else 2
    return closeout()


if __name__ == "__main__":
    raise SystemExit(main())
