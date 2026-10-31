# -*- coding: utf-8 -*-
"""过夜一键启动：清理 → 单监管器(4 worker) → 完成监控。"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYW = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
STATUS = ROOT / "data" / "literature" / "pubmed_yes_fetch_overnight_status.md"

KILL_TAGS = (
    "pubmed_yes_fetch_supervisor",
    "fetch_pubmed_yes_corpus",
    "overnight_pubmed_yes_fetch_guard",
    "overnight_pubmed_yes_fetch_watchdog",
    "monitor_pubmed_yes_fetch_complete",
    "run_pubmed_yes_fetch_worker.bat",
    "run_pubmed_yes_vectorize_worker.bat",
    "vectorize_pubmed_judgement_yes",
)


def _kill(tags: tuple[str, ...]) -> None:
    for exe in ("python.exe", "pythonw.exe", "cmd.exe"):
        try:
            out = subprocess.check_output(
                ["wmic", "process", "where", f"Name='{exe}'", "get", "ProcessId,CommandLine"],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for ln in out.splitlines():
                if not any(t in ln for t in tags):
                    continue
                parts = ln.strip().split()
                if parts and parts[-1].isdigit():
                    subprocess.run(["taskkill", "/PID", parts[-1], "/F"], capture_output=True)
        except Exception:
            pass


def _count(tag: str) -> int:
    n = 0
    for exe in ("python.exe", "pythonw.exe"):
        try:
            out = subprocess.check_output(
                ["wmic", "process", "where", f"Name='{exe}'", "get", "ProcessId,CommandLine"],
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for ln in out.splitlines():
                if tag in ln:
                    parts = ln.strip().split()
                    if parts and parts[-1].isdigit():
                        n += 1
        except Exception:
            pass
    return n


def _write_status(done: int, note: str) -> None:
    STATUS.write_text(
        "\n".join([
            "# PubMed YES Fetch 过夜状态",
            "",
            f"更新时间：{datetime.now().isoformat(timespec='seconds')}",
            f"进度：**{done:,} / 46,878**",
            f"备注：{note}",
            "",
            "进程目标：监管器 1 个 + worker 4 个",
            "",
            "日志：",
            "- `logs/pubmed_yes_fetch_supervisor.log`",
            "- `logs/pubmed_yes_fetch_w00.log` ~ `w03.log`",
            "- `logs/pubmed_yes_fetch_monitor.log`",
        ]),
        encoding="utf-8",
    )


def main() -> int:
    for p in (
        ROOT / "data/literature/pubmed_yes_fetch_supervisor.lock",
        ROOT / "data/literature/pubmed_yes_fetch_guard.lock",
        ROOT / "data/literature/pubmed_yes_fetch_monitor.lock",
        ROOT / "data/literature/pubmed_yes_fetch_watchdog.lock",
    ):
        p.unlink(missing_ok=True)

    _kill(KILL_TAGS)
    time.sleep(4)

    subprocess.Popen(
        ["cmd", "/c", str(ROOT / "logs" / "run_pubmed_yes_supervisor.bat")],
        cwd=str(ROOT),
        creationflags=0x08000000,
    )
    time.sleep(3)
    subprocess.Popen(
        ["cmd", "/c", str(ROOT / "logs" / "run_pubmed_yes_fetch_monitor.bat")],
        cwd=str(ROOT),
        creationflags=0x08000000,
    )
    time.sleep(25)

    # 去重：只保留 1 监管器、1 监控
    sups = []
    mons = []
    for exe in ("python.exe", "pythonw.exe"):
        try:
            out = subprocess.check_output(
                ["wmic", "process", "where", f"Name='{exe}'", "get", "ProcessId,CommandLine"],
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            )
            for ln in out.splitlines():
                parts = ln.strip().split()
                if not parts or not parts[-1].isdigit():
                    continue
                pid = int(parts[-1])
                if "pubmed_yes_fetch_supervisor" in ln:
                    sups.append(pid)
                elif "monitor_pubmed_yes_fetch_complete" in ln:
                    mons.append(pid)
        except Exception:
            pass
    for pid in sorted(set(sups))[:-1]:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    for pid in sorted(set(mons))[:-1]:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)

    ns = _count("pubmed_yes_fetch_supervisor")
    nw = _count("fetch_pubmed_yes_corpus")
    nm = _count("monitor_pubmed_yes_fetch_complete")

    sys.path.insert(0, str(ROOT))
    from rag.pubmed_yes_corpus_io import count_unique_pmids

    done = count_unique_pmids()
    note = f"监管器={ns} worker={nw} 监控={nm}"
    _write_status(done, note)

    print("过夜 fetch 已启动（4 并发 + 断网保护 + 完成监控）")
    print(f"  当前进度: {done:,} / 46,878")
    print(f"  进程: {note}")
    print(f"  早晨查看: {STATUS}")
    print("  可安全关闭 Cursor")
    return 0 if ns >= 1 and nw >= 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
