# -*- coding: utf-8 -*-
"""实时刷新文献导入/判别进度（默认每 10 分钟）。"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROGRESS = ROOT / "data" / "literature" / "pubmed_raw_import.json"
JUDGE_PROGRESS = ROOT / "data" / "literature" / "pubmed_judgement_progress.json"
JUDGE_JOB = ROOT / "logs" / "pubmed_judgement_job.json"
PIPELINE_JOB = ROOT / "logs" / "background_pipeline_job.json"
LIT_BASE = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "test" / "literature_test_100"
RESULT_STATS = LIT_BASE / "result" / "result_stats.json"
INTERVAL = float(os.getenv("WATCH_INTERVAL", "600"))


def _tail(path: Path, n: int = 3) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:]


def _pipeline_log() -> Path | None:
    if PIPELINE_JOB.is_file():
        try:
            p = json.loads(PIPELINE_JOB.read_text(encoding="utf-8")).get("log")
            if p and Path(p).is_file():
                return Path(p)
        except json.JSONDecodeError:
            pass
    logs = sorted(ROOT.glob("logs/background_pipeline_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _pubmed_log() -> Path | None:
    logs = sorted(ROOT.glob("logs/pubmed_import_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _judge_log() -> Path | None:
    if JUDGE_JOB.is_file():
        try:
            p = json.loads(JUDGE_JOB.read_text(encoding="utf-8")).get("log")
            if p and Path(p).is_file():
                return Path(p)
        except json.JSONDecodeError:
            pass
    logs = sorted(ROOT.glob("logs/pubmed_judgement_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return logs[0] if logs else None


def _judge_counts() -> dict | None:
    try:
        from core.entry import bootstrap_app

        bootstrap_app()
        from mysql_client import MySQLClient
        from sqlalchemy import text

        client = MySQLClient()
        db = next(client.get_db())
        try:
            total = int(db.execute(text("SELECT COUNT(*) FROM literature_pubmed")).scalar() or 0)
            done = int(
                db.execute(text("SELECT COUNT(*) FROM literature_pubmed_judgement")).scalar() or 0
            )
            yes_n = int(
                db.execute(
                    text("SELECT COUNT(*) FROM literature_pubmed_judgement WHERE judgement='是'")
                ).scalar()
                or 0
            )
            no_n = int(
                db.execute(
                    text("SELECT COUNT(*) FROM literature_pubmed_judgement WHERE judgement='否'")
                ).scalar()
                or 0
            )
            unk = int(
                db.execute(
                    text(
                        "SELECT COUNT(*) FROM literature_pubmed_judgement WHERE judgement='无法判别'"
                    )
                ).scalar()
                or 0
            )
            llm_n = int(
                db.execute(
                    text("SELECT COUNT(*) FROM literature_pubmed_judgement WHERE mode='llm'")
                ).scalar()
                or 0
            )
        finally:
            db.close()
        quality = None
        if JUDGE_PROGRESS.is_file():
            try:
                quality = json.loads(JUDGE_PROGRESS.read_text(encoding="utf-8")).get("quality")
            except json.JSONDecodeError:
                pass
        return {
            "total": total,
            "done": done,
            "yes": yes_n,
            "no": no_n,
            "unknown": unk,
            "llm": llm_n,
            "quality": quality,
        }
    except Exception:
        return None


def _db_count() -> int | None:
    try:
        from core.entry import bootstrap_app

        bootstrap_app()
        from mysql_client import MySQLClient
        from sqlalchemy import text

        client = MySQLClient()
        db = next(client.get_db())
        try:
            return int(db.execute(text("SELECT COUNT(*) FROM literature_pubmed")).scalar() or 0)
        finally:
            db.close()
    except Exception:
        return None


def _running() -> bool:
    try:
        import subprocess

        out = subprocess.check_output(
            ["wmic", "process", "where", "Name='pythonw.exe'", "get", "CommandLine"],
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        low = out.lower()
        return (
            "import_pubmed_raw" in low
            or "background_pubmed" in low
            or "judge_pubmed_inventory" in low
        )
    except Exception:
        return False


def _render() -> str:
    lines = [f"=== 斑马鱼文献任务进度 {datetime.now().strftime('%H:%M:%S')} ===", ""]

    if PROGRESS.is_file():
        p = json.loads(PROGRESS.read_text(encoding="utf-8"))
        total = int(p.get("total") or 67056)
        imported = int(p.get("imported") or 0)
        year = p.get("current_year", "?")
        status = p.get("status") or "running"
        pct = 100 * imported / max(total, 1)
        bar_len = 30
        filled = int(bar_len * imported / max(total, 1))
        bar = "█" * filled + "░" * (bar_len - filled)
        db_n = _db_count()
        lines.extend([
            "【PubMed 导入】",
            f"  进度: {imported:,} / {total:,}  ({pct:.1f}%)",
            f"  数据库: {db_n:,} 篇" if db_n is not None else "  数据库: (查询失败)",
            f"  当前年份: {year}  状态: {status}",
            f"  [{bar}]",
            "",
        ])
    else:
        lines.append("【PubMed】进度文件不存在\n")

    jc = _judge_counts()
    if jc and jc["total"] > 0:
        done, total = jc["done"], jc["total"]
        pct = 100 * done / max(total, 1)
        bar_len = 30
        filled = int(bar_len * done / max(total, 1))
        bar = "█" * filled + "░" * (bar_len - filled)
        status = "已完成" if done >= total else "进行中"
        q = jc.get("quality") or {}
        retry_n = q.get("retry_pending", 0)
        lines.extend([
            f"【PubMed 全库判别】{status}",
            f"  进度: {done:,} / {total:,}  ({pct:.1f}%)",
            f"  是: {jc['yes']:,}  否: {jc['no']:,}  无法判别: {jc['unknown']:,}",
            f"  LLM: {jc.get('llm', 0):,}  质量重试队列: {retry_n}",
            f"  [{bar}]",
            "",
        ])
    elif RESULT_STATS.is_file():
        s = json.loads(RESULT_STATS.read_text(encoding="utf-8"))
        lines.extend([
            "【文献判别】已完成",
            f"  是: {s.get('yes')}  否: {s.get('no')}  无法判别: {s.get('unknown')}",
            f"  输出: {s.get('output')}",
            "",
        ])
    else:
        ck = LIT_BASE / "result" / "result_checkpoint.json"
        if ck.is_file():
            c = json.loads(ck.read_text(encoding="utf-8"))
            n = len(c.get("results") or [])
            lines.extend([f"【文献判别】进行中 checkpoint: {n} 篇", ""])
        else:
            lines.extend(["【文献判别】等待 PubMed 完成 (≥95%)", ""])

    lines.append(f"【后台进程】{'运行中' if _running() else '未检测到 (可能已完成或已停止)'}")

    jlog = _judge_log()
    plog = _pipeline_log()
    plog2 = _pubmed_log()
    if jlog:
        lines.extend(["", f"--- 全库判别日志 ({jlog.name}) ---"])
        lines.extend(_tail(jlog, 4))
    elif plog:
        lines.extend(["", f"--- 流水线日志 ({plog.name}) ---"])
        lines.extend(_tail(plog, 4))
    if plog2 and not jlog:
        lines.extend(["", f"--- PubMed 导入日志 ({plog2.name}) ---"])
        lines.extend(_tail(plog2, 6))

    lines.append("")
    lines.append(f"刷新间隔 {INTERVAL:.0f}s，Ctrl+C 退出")
    return "\n".join(lines)


def main() -> int:
    once = "--once" in sys.argv
    try:
        while True:
            os.system("cls" if os.name == "nt" else "clear")
            print(_render())
            if once:
                break
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print("\n已退出监控")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
