# -*- coding: utf-8 -*-
"""
全自动全面斑马鱼数据补全（可过夜运行，断点续传）。

并行执行：
  - Ensembl REST 深度注释（GO / 同源 / 文献）
  - Europe PMC 广搜文献 → RAG
  - 文献 → gene_reference 关联

顺序收尾：
  - 补跑失败注释、主题文献、BLAST 索引、RAG 向量库

日志：logs/full_pipeline.log
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "logs")
MAIN_LOG = os.path.join(LOG_DIR, "full_pipeline.log")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.log_io import open_log_append, subprocess_log_env


def _python() -> str:
    venv = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    return venv if os.path.isfile(venv) else sys.executable


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open_log_append(MAIN_LOG) as f:
        f.write(line + "\n")


def _run_module(args: list, log_name: str) -> int:
    log_path = os.path.join(LOG_DIR, log_name)
    os.makedirs(LOG_DIR, exist_ok=True)
    py = _python()
    cmd = [py, "-X", "utf8", "-m"] + args
    _log(f"启动：{' '.join(cmd)} → {log_name}")
    os.makedirs(LOG_DIR, exist_ok=True)
    with open_log_append(log_path) as lf:
        lf.write(f"\n{'=' * 60}\n[{datetime.now()}] {' '.join(cmd)}\n{'=' * 60}\n")
        lf.flush()
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=lf,
            stderr=subprocess.STDOUT,
            env=subprocess_log_env(),
        )
        return proc.wait()


def _run_module_async(args: list, log_name: str) -> subprocess.Popen:
    log_path = os.path.join(LOG_DIR, log_name)
    os.makedirs(LOG_DIR, exist_ok=True)
    py = _python()
    cmd = [py, "-X", "utf8", "-m"] + args
    _log(f"后台：{' '.join(cmd)} → {log_name}")
    lf = open_log_append(log_path)
    lf.write(f"\n{'=' * 60}\n[{datetime.now()}] {' '.join(cmd)}\n{'=' * 60}\n")
    lf.flush()
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=lf,
        stderr=subprocess.STDOUT,
        env=subprocess_log_env(),
    )
    return proc


def _db_stats() -> dict:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    from core.entry import bootstrap_app

    bootstrap_app()
    from mysql_client import get_mysql_client

    c = get_mysql_client()
    stats = {}
    for table in (
        "gene_basic", "gene_expression", "gene_go", "homology_gene",
        "gene_reference", "gene_alias", "gene_xref",
    ):
        r = c.execute_sql(f"SELECT COUNT(*) AS n FROM {table}")
        stats[table] = int(r["data"][0]["n"]) if r.get("success") and r.get("data") else -1
    r = c.execute_sql(
        "SELECT COUNT(*) AS n FROM gene_basic gb "
        "LEFT JOIN gene_go g ON gb.gene_id=g.gene_id WHERE g.gene_id IS NULL"
    )
    stats["genes_no_go"] = int(r["data"][0]["n"]) if r.get("success") else -1
    return stats


def _enrich_done_count() -> int:
    import json

    p = os.path.join(ROOT, "data", "ensembl", "enrich_progress.json")
    if not os.path.isfile(p):
        return 0
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    return len(d.get("done", []))


def _wait_process(name: str, proc: subprocess.Popen, poll_sec: int = 60) -> int:
    while proc.poll() is None:
        _log(f"  … {name} 运行中 (pid={proc.pid})")
        time.sleep(poll_sec)
    code = proc.returncode or 0
    _log(f"  {name} 结束，exit={code}")
    return code


def main() -> int:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    os.chdir(ROOT)

    from core.entry import bootstrap_app

    bootstrap_app()

    _log("=" * 60)
    _log("全面斑马鱼数据补全流水线 — 开始")
    _log("=" * 60)

    try:
        before = _db_stats()
        _log(f"当前库：{before}")
    except Exception as e:
        _log(f"[警告] 无法读取 MySQL 统计（请确认 MySQL 已启动）：{e}")
        before = {}

    # ── 1. 别名 / ID 映射 ──
    _log("【阶段 1/6】刷新基因别名与跨库 ID 映射…")
    rc = _run_module(["scripts.build_gene_aliases", "--repair-xrefs"], "01_aliases.log")
    if rc != 0:
        _log("[警告] 别名映射未完全成功，继续后续阶段…")

    # ── 2. 并行：深度注释 + 文献广搜 + 文献关联 ──
    _log("【阶段 2/6】并行：Ensembl 深度注释 + EPMC 文献 + 文献→基因关联…")
    enrich_proc = _run_module_async(
        ["scripts.collect_genome", "--enrich-only", "--enrich-workers", "4"],
        "02_enrich.log",
    )
    lit_proc = _run_module_async(
        ["scripts.collect_literature", "--source", "pubmed", "--pubmed-backend", "epmc"],
        "03_literature.log",
    )
    link_proc = _run_module_async(
        ["scripts.link_literature_genes", "--loop", "--interval", "120"],
        "04_link_genes.log",
    )

    _log("等待 Ensembl 深度注释完成（最耗时，支持断点续传）…")
    enrich_rc = _wait_process("enrich", enrich_proc, poll_sec=120)
    if enrich_rc != 0:
        _log(f"[警告] enrich 退出码 {enrich_rc}，尝试补跑失败项…")

    # ── 3. 补跑无 GO 的基因 ──
    _log("【阶段 3/6】补跑注释失败 / 缺 GO 的基因…")
    _run_module(
        ["scripts.collect_genome", "--enrich-only", "--enrich-retry-failed", "--enrich-workers", "4"],
        "05_enrich_retry.log",
    )

    # 文献广搜 + 关联仍在后台运行，不中断（EPMC 约 50 万条，需数小时～数天）
    _log("【阶段 4/6】文献广搜与基因关联仍在后台继续（见 logs/03_literature.log、04_link_genes.log）")

    # ── 5. BLAST 索引 ──
    _log("【阶段 5/6】构建 BLAST cDNA 索引…")
    blast_args = ["scripts.setup_blast", "--build-db-only"]
    if os.path.isfile(os.path.join(ROOT, "data", "reference_genome.fasta")):
        _run_module(blast_args, "07_blast.log")
    else:
        _log("  跳过 BLAST：未找到 data/reference_genome.fasta")

    # ── 6. RAG 向量索引（文献仍在写入时仅触发，可能较慢）──
    if lit_proc.poll() is None:
        _log("【阶段 6/6】文献仍在采集，跳过全量 FAISS 重建（避免冲突）；"
             "广搜结束后可重启 Web 或手动运行 rebuild")
    else:
        _log("【阶段 6/6】重建 RAG 向量索引…")

        def _rebuild_rag():
            try:
                if ROOT not in sys.path:
                    sys.path.insert(0, ROOT)
                from rag.knowledge_ingest import rebuild_rag_index

                rebuild_rag_index()
                _log("RAG 向量索引重建完成")
            except Exception as e:
                _log(f"[警告] RAG 重建失败（关键词检索仍可用）：{e}")

        rag_thread = threading.Thread(target=_rebuild_rag, daemon=True)
        rag_thread.start()
        rag_thread.join(timeout=7200)
        if rag_thread.is_alive():
            _log("RAG 重建超时（>2h），仍在后台进行")

    try:
        after = _db_stats()
        enrich_n = _enrich_done_count()
        _log(f"完成后库：{after}")
        _log(f"Ensembl enrich 累计：{enrich_n} 基因")
    except Exception as e:
        _log(f"最终统计失败：{e}")

    report = os.path.join(LOG_DIR, "pipeline_report.txt")
    with open(report, "w", encoding="utf-8") as f:
        f.write(f"完成时间：{datetime.now()}\n")
        f.write(f"详细日志：{MAIN_LOG}\n")
        f.write("分步日志：logs/01_aliases.log … logs/07_blast.log\n")
        if before:
            f.write(f"\n开始前：{before}\n")
        try:
            f.write(f"\n结束后：{_db_stats()}\n")
            f.write(f"enrich_done={_enrich_done_count()}\n")
        except Exception:
            pass

    _log("=" * 60)
    _log(f"全面数据补全主流水线 — 结束（报告：{report}）")
    if lit_proc.poll() is None or link_proc.poll() is None:
        _log("文献广搜 / 基因关联仍在后台运行，请勿结束「ZebrafishFullPipeline」最小化窗口")
    _log("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
