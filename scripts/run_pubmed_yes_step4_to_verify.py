# -*- coding: utf-8 -*-
"""
步骤 4→验收：等 fetch 完成 → 合并 → 全量 embed → verify；失败则按原因修补后再测。

用法：
  .\\.venv\\Scripts\\python.exe -u -m scripts.run_pubmed_yes_step4_to_verify
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
LOG = ROOT / "logs" / "pubmed_yes_step4_verify.log"
PROGRESS = ROOT / "data" / "literature" / "pubmed_yes_fetch.json"
os.environ.setdefault("FAISS_DISABLE_AVX2", "1")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def _log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _run(args: list[str], *, timeout: int | None = None) -> int:
    _log("RUN " + " ".join(args))
    p = subprocess.run(args, cwd=str(ROOT), timeout=timeout)
    _log(f"EXIT {p.returncode}")
    return int(p.returncode)


def _fetch_done() -> tuple[bool, int, int]:
    from rag.pubmed_yes_corpus_io import count_unique_pmids
    from rag.pubmed_yes_source import count_pubmed_yes_pmids

    done = count_unique_pmids()
    total = count_pubmed_yes_pmids()
    # 允许少量失败：覆盖 ≥ 95%
    ok = total > 0 and done >= int(total * 0.95)
    return ok, done, total


def _wait_fetch(max_hours: float = 12.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < max_hours * 3600:
        ok, done, total = _fetch_done()
        _log(f"等待 fetch: {done}/{total} ({100 * done / total:.1f}%)" if total else f"等待 fetch: {done}")
        if ok:
            return True
        # 若监管器已标 completed 也放行
        if PROGRESS.is_file():
            try:
                st = json.loads(PROGRESS.read_text(encoding="utf-8")).get("status")
                if st == "completed" and done > 0:
                    return True
            except Exception:
                pass
        time.sleep(120)
    return False


def _ensure_supervisor() -> None:
    """若没有 fetch/supervisor，拉起一个。"""
    try:
        import psutil  # type: ignore
    except ImportError:
        psutil = None
    running = False
    if psutil:
        for p in psutil.process_iter(["cmdline"]):
            cl = " ".join(p.info.get("cmdline") or [])
            if "pubmed_yes_fetch_supervisor" in cl or "fetch_pubmed_yes_corpus" in cl:
                running = True
                break
    else:
        # Windows 粗检
        r = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "commandline"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        running = "fetch_pubmed_yes" in (r.stdout or "")

    if running:
        _log("fetch 进程已在跑，不重复拉起")
        return

    env = os.environ.copy()
    env["FAISS_DISABLE_AVX2"] = "1"
    env["PUBMED_YES_SKIP_FULLTEXT"] = "1"
    env["PUBMED_YES_FETCH_WORKERS"] = env.get("PUBMED_YES_FETCH_WORKERS", "4")
    lock = ROOT / "data" / "literature" / "pubmed_yes_fetch_supervisor.lock"
    lock.unlink(missing_ok=True)
    _log("拉起 pubmed_yes_fetch_supervisor")
    subprocess.Popen(
        [PY, "-u", "-m", "scripts.pubmed_yes_fetch_supervisor"],
        cwd=str(ROOT),
        env=env,
        stdout=open(ROOT / "logs" / "pubmed_yes_fetch_supervisor_stdout.log", "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )


def _patch_common_failures(verify_output: str) -> list[str]:
    """根据验收输出做有限自动修补，返回已做动作。"""
    actions: list[str] = []
    from rag.pubmed_yes_corpus_io import merge_shards_to_main

    if "语料为空" in verify_output or "语料过少" in verify_output:
        _ensure_supervisor()
        actions.append("ensure_fetch_supervisor")
    if "缺少 manifest" in verify_output or "FAISS total_papers=0" in verify_output:
        actions.append("need_embed")
    if "PubmedYesRetriever 未就绪" in verify_output:
        actions.append("need_embed")
    if "Hub literature" in verify_output and "异常" in verify_output:
        actions.append("hub_code_check")
    # 总是尝试合并分片，无害
    try:
        n = merge_shards_to_main()
        actions.append(f"merge_shards={n}")
    except Exception as e:
        actions.append(f"merge_fail:{e}")
    return actions


def _acquire_step4_lock() -> bool:
    lock = ROOT / "data" / "literature" / "pubmed_yes_step4_verify.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    my = os.getpid()
    if lock.is_file():
        try:
            old = int(lock.read_text(encoding="utf-8").strip())
        except ValueError:
            old = -1
        if old != my:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {old}", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if str(old) in (r.stdout or ""):
                _log(f"步骤4验收已在运行 pid={old}，本进程退出")
                return False
        lock.unlink(missing_ok=True)
    lock.write_text(str(my), encoding="utf-8")
    return True


def main() -> int:
    if not _acquire_step4_lock():
        return 0
    _log("=== 步骤4→验收 启动 ===")
    _ensure_supervisor()

    if not _wait_fetch():
        _log("fetch 超时未达 95% 覆盖，仍继续尝试合并/嵌入/验收")
    else:
        _log("fetch 覆盖达标")

    from rag.pubmed_yes_corpus_io import merge_shards_to_main

    n = merge_shards_to_main()
    _log(f"合并语料 unique={n}")

    # 全量嵌入（resume）
    code = _run([PY, "-u", "-m", "scripts.vectorize_pubmed_judgement_yes", "--phase", "embed", "--resume"])
    if code != 0:
        _log("embed 失败，将在验收循环中重试")

    # 测试→修复→再测
    for round_i in range(1, 8):
        _log(f"--- 验收第 {round_i} 轮 ---")
        proc = subprocess.run(
            [PY, "-u", "-m", "scripts.verify_pubmed_yes_vectorize"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        print(out, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(out + "\n")
        if proc.returncode == 0:
            _log("=== 验收通过，步骤4+5（检索接入）OK ===")
            return 0

        actions = _patch_common_failures(out)
        _log(f"修补动作: {actions}")
        if "need_embed" in actions:
            _run([PY, "-u", "-m", "scripts.vectorize_pubmed_judgement_yes", "--phase", "embed", "--resume"])
        if "ensure_fetch_supervisor" in actions:
            ok, done, total = _fetch_done()
            if not ok:
                _log(f"语料不足 {done}/{total}，再等 10 分钟…")
                time.sleep(600)
                merge_shards_to_main()
                _run([PY, "-u", "-m", "scripts.vectorize_pubmed_judgement_yes", "--phase", "embed", "--resume"])
        time.sleep(5)

    _log("=== 验收仍未通过，请看 logs/pubmed_yes_step4_verify.log ===")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
