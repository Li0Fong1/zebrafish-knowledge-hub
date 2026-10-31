# -*- coding: utf-8 -*-
"""过夜中等强度编排（不含参考文献独立向量库）。

顺序:
  1) 续跑 ENO→gte 直至 8012
  2) 登记全部 gte 分片到 MySQL
  3) 金标 meta 批量入库（PMID + ENO 语料）
  4) 内存够则启动 Hub
  5) 启动 gold_auto_throttle（中等静音战役）

用法:
  python -m scripts.overnight_medium_ops --detach
  python -m scripts.overnight_medium_ops --foreground
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
if not PY.is_file():
    PY = Path(sys.executable)

STATUS = ROOT / "解析文件" / "overnight_medium_ops_status.json"
LOG = ROOT / "logs" / "overnight_medium_ops.log"
LOCK = ROOT / "解析文件" / "overnight_medium_ops.run.lock"
ENO_TARGET = 8012
FAISS_MAN = Path(
    os.path.expandvars(r"%LOCALAPPDATA%\ZebrafishGeneQA\faiss_pubmed_yes_gte_mbase_v1\manifest.json")
)


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _write_status(**kw) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    cur = {}
    if STATUS.is_file():
        try:
            cur = json.loads(STATUS.read_text(encoding="utf-8"))
        except Exception:
            cur = {}
    cur.update(kw)
    cur["updated_at"] = datetime.now().isoformat(timespec="seconds")
    STATUS.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")


def _free_gb() -> float:
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return round(int(out.strip()) / (1024 * 1024), 2)
    except Exception:
        return -1.0


def _py_procs() -> list[dict]:
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(out or "[]")
        if isinstance(data, dict):
            data = [data]
        return data or []
    except Exception:
        return []


def _find(needle: str) -> list[int]:
    hits = []
    for row in _py_procs():
        cmd = str(row.get("CommandLine") or "")
        pid = row.get("ProcessId")
        if pid and needle in cmd:
            hits.append(int(pid))
    return hits


def _kill(needle: str) -> None:
    for pid in _find(needle):
        subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
        _log(f"killed {needle} pid={pid}")


def _nice(pid: int) -> None:
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-Process -Id {pid}).PriorityClass='BelowNormal'",
            ],
            capture_output=True,
        )
    except Exception:
        pass


def _spawn(args: list[str], log_path: Path, env: dict | None = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = log_path.open("a", encoding="utf-8")
    fh.write(f"\n==== {datetime.now().isoformat()} {' '.join(args)} ====\n")
    fh.flush()
    e = os.environ.copy()
    if env:
        e.update(env)
    flags = 0x08000000 | 0x00000008 | 0x00004000  # CREATE_NO_WINDOW|DETACHED|NEW_PROCESS_GROUP
    p = subprocess.Popen(
        [str(PY), "-u", *args],
        cwd=str(ROOT),
        stdout=fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=e,
        creationflags=flags,
        close_fds=False,
    )
    time.sleep(1.5)
    _nice(p.pid)
    _log(f"spawn pid={p.pid} args={args} poll={p.poll()}")
    return int(p.pid)


def _eno_count() -> int:
    if not FAISS_MAN.is_file():
        return 0
    try:
        m = json.loads(FAISS_MAN.read_text(encoding="utf-8-sig"))
        return int(m.get("eno_papers") or 0)
    except Exception:
        return 0


def _manifest_snapshot() -> dict:
    if not FAISS_MAN.is_file():
        return {}
    try:
        m = json.loads(FAISS_MAN.read_text(encoding="utf-8-sig"))
        return {
            "eno_papers": m.get("eno_papers"),
            "total_papers": m.get("total_papers"),
            "total_chunks": m.get("total_chunks"),
            "updated_at": m.get("updated_at"),
            "n_shards": len(m.get("shards") or []),
        }
    except Exception:
        return {}


def phase_eno() -> None:
    _log("PHASE eno: ensure resume")
    while True:
        n = _eno_count()
        snap = _manifest_snapshot()
        free = _free_gb()
        _write_status(phase="eno", eno=n, free_gb=free, manifest=snap)
        if n >= ENO_TARGET:
            _log(f"ENO done {n}/{ENO_TARGET}")
            return
        living = _find("vectorize_exist_eno")
        if not living:
            if free >= 0 and free < 1.2:
                _log(f"ENO not running and free_gb={free} too low; wait")
                time.sleep(45)
                continue
            _log(f"ENO stalled at {n}; resume")
            _spawn(
                ["-m", "scripts.vectorize_exist_eno", "--resume"],
                ROOT / "logs" / "eno_gte_embed_stdout.log",
                env={
                    "EXIST_ENO_RUNTIME_SHARD": "50",
                    "PUBMED_YES_SECTION_CHUNKING": "1",
                    "FAISS_DISABLE_AVX2": "1",
                    "HF_ENDPOINT": os.getenv("HF_ENDPOINT") or "https://hf-mirror.com",
                },
            )
        else:
            _log(f"ENO running pids={living} progress={n}/{ENO_TARGET} free={free}")
        time.sleep(90)


def phase_register() -> None:
    _log("PHASE register shards")
    _write_status(phase="register_shards")
    r = subprocess.run(
        [str(PY), "-u", "-m", "scripts.register_gte_shards_to_db", "--index-version", "gte_mbase_v1"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _log(f"register rc={r.returncode} out={(r.stdout or '')[-400:]} err={(r.stderr or '')[-400:]}")
    _write_status(phase="register_shards", register_rc=r.returncode)


def phase_gold_meta() -> None:
    _log("PHASE gold meta backfill")
    _write_status(phase="gold_meta")
    for corpus, camp in (
        ("data/literature/pubmed_yes_corpus.jsonl", "pmid_corpus_gold_meta_v1"),
        ("data/literature/exist_eno_corpus.jsonl", "eno_corpus_gold_meta_v1"),
    ):
        r = subprocess.run(
            [
                str(PY),
                "-u",
                "-m",
                "scripts.backfill_literature_fulltext_gold_from_corpus",
                "--corpus",
                corpus,
                "--batch",
                "80",
                "--sleep-ms",
                "8",
                "--campaign-id",
                camp,
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        _log(f"gold_meta {corpus} rc={r.returncode} tail={(r.stdout or '')[-300:]}")
    _write_status(phase="gold_meta_done")


def phase_hub() -> None:
    _log("PHASE hub")
    free = _free_gb()
    if _find("api.api_server"):
        _log("Hub already up")
        _write_status(phase="hub", hub="already")
        return
    # wait for ENO worker memory to drop
    for _ in range(20):
        free = _free_gb()
        if free >= 3.5 and not _find("vectorize_exist_eno"):
            break
        _log(f"wait Hub free_gb={free}")
        time.sleep(20)
    free = _free_gb()
    if free < 2.5:
        _log(f"skip Hub free_gb={free}")
        _write_status(phase="hub", hub="skipped_low_mem", free_gb=free)
        return
    pid = _spawn(["-X", "utf8", "-m", "api.api_server"], ROOT / "logs" / "api_server_overnight.out.log")
    _write_status(phase="hub", hub_pid=pid, free_gb=free)
    time.sleep(8)


def phase_gold_campaign() -> None:
    _log("PHASE gold medium campaign")
    # clear pause marker if present
    pause = ROOT / "解析文件" / "gold_campaigns_paused.json"
    if pause.is_file():
        try:
            pause.unlink()
            _log("removed gold_campaigns_paused.json")
        except Exception as e:
            _log(f"pause unlink err {e}")
    if not _find("gold_auto_throttle"):
        _spawn(
            ["-m", "scripts.gold_auto_throttle", "--detach"],
            ROOT / "logs" / "gold_auto_throttle_overnight.log",
        )
    else:
        _log("gold_auto_throttle already living")
    # also ensure quiet keep_alive if throttle alone is light
    if not _find("gold_keep_alive") and not _find("gold_quiet_mode"):
        free = _free_gb()
        if free >= 2.0:
            _spawn(["-m", "scripts.gold_quiet_mode"], ROOT / "logs" / "gold_quiet_overnight.log")
    _write_status(phase="gold_campaign", free_gb=_free_gb())


def _acquire_lock():
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fp = open(LOCK, "a+", encoding="utf-8")
    try:
        if sys.platform == "win32":
            import msvcrt

            fp.seek(0)
            msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fp.close()
        return None
    fp.seek(0)
    fp.truncate()
    fp.write(f"{os.getpid()}\n{datetime.now().isoformat(timespec='seconds')}\n")
    fp.flush()
    return fp


def run_all() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    lock_fp = _acquire_lock()
    if lock_fp is None:
        _log("another overnight_medium_ops holds lock — exit")
        return 0
    _log("=== overnight_medium_ops start ===")
    _write_status(phase="start", note="no refs independent index")
    try:
        # keep Hub down during ENO
        if _find("vectorize_exist_eno") or _eno_count() < ENO_TARGET:
            _kill("api.api_server")
        phase_eno()
        # give OS a moment after ENO exits
        time.sleep(15)
        phase_register()
        phase_gold_meta()
        phase_hub()
        phase_gold_campaign()
        snap = _manifest_snapshot()
        _write_status(phase="idle_watch", done=True, manifest=snap, free_gb=_free_gb())
        _log("=== initial pipeline done; watch loop ===")
        # keep watching: if ENO somehow incomplete, or gold dies, restart
        while True:
            n = _eno_count()
            if n < ENO_TARGET and not _find("vectorize_exist_eno"):
                _log("watch: ENO slipped; resume")
                phase_eno()
                phase_register()
            if not _find("gold_auto_throttle") and not _find("gold_keep_alive"):
                free = _free_gb()
                if free >= 2.0:
                    _log("watch: gold died; restart throttle")
                    phase_gold_campaign()
            if not _find("api.api_server"):
                free = _free_gb()
                if free >= 3.5 and not _find("vectorize_exist_eno"):
                    _log("watch: Hub down; restart")
                    phase_hub()
            _write_status(
                phase="idle_watch",
                eno=_eno_count(),
                free_gb=_free_gb(),
                hub=bool(_find("api.api_server")),
                gold=bool(_find("gold_auto_throttle") or _find("gold_keep_alive")),
                manifest=_manifest_snapshot(),
            )
            time.sleep(180)
    except Exception as e:
        _log(f"FATAL {e!r}")
        _write_status(phase="fatal", error=str(e))
        return 1
    finally:
        try:
            lock_fp.close()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detach", action="store_true")
    ap.add_argument("--foreground", action="store_true")
    args = ap.parse_args()
    if args.detach and not args.foreground:
        # avoid duplicate orchestrators
        living = [p for p in _find("overnight_medium_ops") if True]
        # exclude self later; simple: if another with --foreground exists
        others = []
        for row in _py_procs():
            cmd = str(row.get("CommandLine") or "")
            pid = row.get("ProcessId")
            if pid and "overnight_medium_ops" in cmd and "--foreground" in cmd:
                others.append(int(pid))
        if others:
            print(f"overnight already running {others}")
            return 0
        pid = _spawn(
            ["-m", "scripts.overnight_medium_ops", "--foreground"],
            LOG,
        )
        print(f"detached overnight pid={pid}")
        _write_status(orchestrator_pid=pid, phase="detached")
        return 0
    return run_all()


if __name__ == "__main__":
    raise SystemExit(main())
