# -*- coding: utf-8 -*-
"""
过夜任务监听器：文献 + GO + 基因命名/ID 映射（GRCz12tu）全自动重试。

批处理：18 WoS | 08 别名 | 21 GRCz12tu | 19 GO | 22 ID测试 | 23 RAG
守护：15 文献关联
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from core.log_io import open_log_append, read_lines_utf8, subprocess_log_env
from common_utils import safe_print

LOG_DIR = ROOT / "logs"
STATE_FILE = LOG_DIR / "overnight_supervisor.json"
MAIN_LOG = LOG_DIR / "overnight_supervisor.log"

POLL_SEC = 30
ROUND_COOLDOWN_SEC = 45
MAX_RETRY_DELAY_SEC = 300


def _python() -> Path:
    venv = ROOT / ".venv" / "Scripts" / "python.exe"
    return venv if venv.is_file() else Path(sys.executable)


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str) -> None:
    line = f"[{_ts()}] {msg}"
    safe_print(line)
    LOG_DIR.mkdir(exist_ok=True)
    with open_log_append(MAIN_LOG) as f:
        f.write(line + "\n")


def _read_tail(path: Path, lines: int = 40) -> str:
    if not path.is_file():
        return ""
    return "\n".join(read_lines_utf8(path, tail=lines))


def _read_err_tail(name: str, lines: int = 30) -> str:
    return _read_tail(LOG_DIR / f"overnight_{name}.err.log", lines)


@dataclass
class JobSpec:
    id: str
    module: str
    args: List[str]
    log_name: str
    daemon: bool = False
    description: str = ""


@dataclass
class JobState:
    status: str = "pending"  # pending | running | success | failed
    attempts: int = 0
    pid: Optional[int] = None
    last_error: str = ""
    last_success: str = ""
    history: List[dict] = field(default_factory=list)


JOBS: List[JobSpec] = [
    JobSpec(
        id="08_aliases",
        module="scripts.build_gene_aliases",
        args=["--repair-xrefs"],
        log_name="08_aliases",
        description="NCBI 别名/xref 重建（命名混乱）",
    ),
    JobSpec(
        id="21_grcz12",
        module="scripts.build_grcz12tu_xref",
        args=[],
        log_name="21_grcz12",
        description="GRCz12tu 参考基因组 ID 映射",
    ),
    JobSpec(
        id="18_wos",
        module="scripts.import_wos_ris",
        args=["--rebuild-rag"],
        log_name="18_wos",
        description="WoS RIS 导入 + RAG 重建",
    ),
    JobSpec(
        id="15_link",
        module="scripts.link_literature_genes",
        args=["--loop", "--interval", "120"],
        log_name="15_link",
        daemon=True,
        description="文献→基因关联（持续轮询）",
    ),
    JobSpec(
        id="19_go",
        module="scripts.backfill_go",
        args=[],
        log_name="19_go",
        description="GO 深度注释补全",
    ),
    JobSpec(
        id="22_idtest",
        module="scripts.test_id_mapping",
        args=[],
        log_name="22_idtest",
        description="ID 映射回归测试",
    ),
    JobSpec(
        id="23_rag",
        module="scripts.rebuild_naming_rag",
        args=[],
        log_name="23_rag",
        description="命名知识 RAG 重建",
    ),
    JobSpec(
        id="24_export",
        module="scripts.export_gene_data_for_cleaning",
        args=[],
        log_name="24_export",
        description="导出人工清洗 CSV 包",
    ),
]


def _bootstrap_db():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    from core.entry import bootstrap_app

    bootstrap_app()


def _count_enrich_eligible_no_go() -> int:
    _bootstrap_db()
    from sqlalchemy import text
    from mysql_client import get_mysql_client

    client = get_mysql_client()
    db = next(client.get_db())
    try:
        row = db.execute(text(
            """
            SELECT COUNT(*) AS n
            FROM gene_basic gb
            LEFT JOIN (SELECT gene_id, COUNT(*) AS n FROM gene_go GROUP BY gene_id) g
              ON gb.gene_id = g.gene_id
            WHERE gb.ensembl_id IS NOT NULL
              AND (g.n IS NULL OR g.n = 0)
              AND (
                gb.gene_function LIKE '%protein_coding%'
                OR gb.expression_summary LIKE '%protein_coding%'
              )
            """
        )).mappings().first()
        return int(row["n"] or 0)
    finally:
        db.close()


def validate_18_wos() -> Tuple[bool, str]:
    out = _read_tail(LOG_DIR / "overnight_18_wos.log", 30)
    err = _read_err_tail("18_wos", 20)
    if "Traceback" in err and "RAG 索引已重建" not in out:
        return False, "stderr 含异常堆栈"
    if "RAG 索引已重建" in out:
        return True, "WoS 入库且 RAG 已重建"
    if "[完成] 本次入库" in out and "重建 RAG" not in out:
        return False, "入库完成但未见 RAG 重建标记"
    return False, "尚未完成或未找到成功标记"


def validate_19_go(exit_code: int) -> Tuple[bool, str]:
    err = _read_err_tail("19_go", 25)
    if "Traceback" in err or exit_code not in (0, None):
        return False, f"进程异常退出 exit={exit_code}"
    try:
        remaining = _count_enrich_eligible_no_go()
    except Exception as e:
        return False, f"无法查询无 GO 基因数: {e}"
    if remaining == 0:
        return True, "可补全的 protein_coding 基因均已获得 GO"
    out = _read_tail(LOG_DIR / "overnight_19_go.log", 15)
    if "失败0" in out.replace(" ", "") or "失败 0" in out:
        if remaining <= 50:
            return True, f"本轮无失败项，剩余 {remaining} 个可能无 Ensembl GO"
    return False, f"仍有 {remaining} 个 protein_coding 基因无 GO，需重试"


def validate_15_link(proc: Optional[subprocess.Popen]) -> Tuple[bool, str]:
    if proc is not None and proc.poll() is None:
        return True, f"守护进程运行中 pid={proc.pid}"
    err = _read_err_tail("15_link", 20)
    if "Traceback" in err:
        return False, "关联进程崩溃（stderr 有堆栈）"
    return False, "关联进程已退出"


def validate_08_aliases(exit_code: int) -> Tuple[bool, str]:
    err = _read_err_tail("08_aliases", 20)
    if "Traceback" in err or exit_code not in (0, None):
        return False, f"别名构建异常 exit={exit_code}"
    out = _read_tail(LOG_DIR / "overnight_08_aliases.log", 20)
    if "gene_alias" in out or "xref" in out or "完成" in out or "修复" in out:
        try:
            _bootstrap_db()
            from sqlalchemy import text
            from mysql_client import get_mysql_client
            db = next(get_mysql_client().get_db())
            row = db.execute(text("SELECT COUNT(*) AS n FROM gene_alias")).mappings().first()
            db.close()
            n = int(row["n"] or 0)
            if n >= 5000:
                return True, f"gene_alias {n} 条"
        except Exception as e:
            return False, f"无法统计 gene_alias: {e}"
    return False, "别名/xref 未完成或数量不足"


def validate_21_grcz12(exit_code: int) -> Tuple[bool, str]:
    err = _read_err_tail("21_grcz12", 20)
    if "Traceback" in err or exit_code not in (0, None):
        return False, f"GRCz12tu 映射异常 exit={exit_code}"
    out = _read_tail(LOG_DIR / "overnight_21_grcz12.log", 15)
    if "[完成]" not in out and "[警告]" in out:
        return False, "GRCz12tu 匹配率不足"
    try:
        _bootstrap_db()
        from sqlalchemy import text
        from mysql_client import get_mysql_client
        db = next(get_mysql_client().get_db())
        row = db.execute(text(
            "SELECT COUNT(*) AS n FROM gene_assembly_xref WHERE assembly_name='GRCz12tu'"
        )).mappings().first()
        db.close()
        n = int(row["n"] or 0)
        if n >= 15000:
            return True, f"GRCz12tu 映射 {n} 基因"
        return False, f"GRCz12tu 映射仅 {n} 条，需重试"
    except Exception as e:
        if "[完成]" in out:
            return True, "日志显示完成（表统计暂不可用）"
        return False, str(e)


def validate_22_idtest(exit_code: int) -> Tuple[bool, str]:
    err = _read_err_tail("22_idtest", 30)
    out = _read_tail(LOG_DIR / "overnight_22_idtest.log", 25)
    if exit_code == 0 and "FAIL" not in out:
        return True, "ID 映射测试通过"
    if "全部通过" in out or (exit_code == 0 and "failed=0" in out.lower()):
        return True, "ID 映射测试通过"
    return False, f"ID 测试失败 exit={exit_code}"


def validate_23_rag(exit_code: int) -> Tuple[bool, str]:
    err = _read_err_tail("23_rag", 15)
    out = _read_tail(LOG_DIR / "overnight_23_rag.log", 10)
    if "Traceback" in err or exit_code not in (0, None):
        return False, f"RAG 重建异常 exit={exit_code}"
    if "RAG 索引已重建" in out or "[完成]" in out:
        return True, "命名知识 RAG 已重建"
    return False, "RAG 重建未完成"


def validate_24_export(exit_code: int) -> Tuple[bool, str]:
    err = _read_err_tail("24_export", 20)
    out = _read_tail(LOG_DIR / "overnight_24_export.log", 15)
    if "Traceback" in err or exit_code not in (0, None):
        return False, f"导出异常 exit={exit_code}"
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d")
    export_dir = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / f"zebrafish_gene_{stamp}"
    queue = export_dir / "06_cleaning_queue" / "queue_no_go.csv"
    if queue.is_file() and "[完成]" in out:
        return True, f"清洗包已导出 → {export_dir.name}"
    if "[完成]" in out:
        return True, "清洗 CSV 已导出"
    return False, "导出未完成或未找到清洗队列"


VALIDATORS: Dict[str, Callable[..., Tuple[bool, str]]] = {
    "18_wos": lambda _proc=None, _code=0: validate_18_wos(),
    "19_go": lambda _proc=None, code=0: validate_19_go(code),
    "15_link": lambda proc=None, _code=0: validate_15_link(proc),
    "08_aliases": lambda _proc=None, code=0: validate_08_aliases(code),
    "21_grcz12": lambda _proc=None, code=0: validate_21_grcz12(code),
    "22_idtest": lambda _proc=None, code=0: validate_22_idtest(code),
    "23_rag": lambda _proc=None, code=0: validate_23_rag(code),
    "24_export": lambda _proc=None, code=0: validate_24_export(code),
}


def _find_existing_pid(module: str) -> Optional[int]:
    """若同模块已在运行，返回其 PID（避免重复启动）。"""
    if os.name != "nt":
        return None
    try:
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object { $_.CommandLine -match '" + module.replace(".", "\\.") + "' } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", ps],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        creationflags=0x08000000 if __import__('sys').platform == 'win32' else 0)
        pids = [int(x.strip()) for x in out.splitlines() if x.strip().isdigit()]
        return pids[0] if pids else None
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


class _AdoptedProcess:
    """占位：表示监听器接管的已存在进程。"""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._code: Optional[int] = None

    def poll(self) -> Optional[int]:
        if self._code is not None:
            return self._code
        if os.name == "nt":
            try:
                out = subprocess.check_output(
                    ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", f"Get-Process -Id {self.pid} -ErrorAction SilentlyContinue"],
                    stderr=subprocess.DEVNULL,
                creationflags=0x08000000 if __import__('sys').platform == 'win32' else 0)
                if out:
                    return None
            except subprocess.CalledProcessError:
                pass
        self._code = 0
        return self._code

    @property
    def returncode(self) -> Optional[int]:
        return self.poll()


class Supervisor:
    def __init__(self) -> None:
        self.round_num = 1
        self.procs: Dict[str, subprocess.Popen] = {}
        self.states: Dict[str, JobState] = {j.id: JobState() for j in JOBS}
        self.log_handles: Dict[str, tuple] = {}
        self._load_state()

    def _load_state(self) -> None:
        if not STATE_FILE.is_file():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.round_num = int(data.get("round", 1))
            for jid, st in data.get("jobs", {}).items():
                if jid in self.states:
                    self.states[jid] = JobState(**{k: st[k] for k in asdict(JobState()) if k in st})
        except (json.JSONDecodeError, TypeError, KeyError):
            pass

    def _save_state(self) -> None:
        LOG_DIR.mkdir(exist_ok=True)
        payload = {
            "updated": _ts(),
            "round": self.round_num,
            "jobs": {jid: asdict(st) for jid, st in self.states.items()},
        }
        STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _record(self, job_id: str, event: str, detail: str = "") -> None:
        st = self.states[job_id]
        entry = {"time": _ts(), "event": event, "detail": detail}
        st.history.append(entry)
        st.history = st.history[-50:]
        self._save_state()

    def _already_success(self, job: JobSpec) -> bool:
        if job.daemon:
            return False
        ok, msg = VALIDATORS[job.id](None, 0)
        if ok:
            self.states[job.id].status = "success"
            self.states[job.id].last_success = msg
            return True
        return False

    def _spawn(self, job: JobSpec) -> None:
        if job.id in self.procs and self.procs[job.id].poll() is None:
            return

        existing = _find_existing_pid(job.module)
        if existing:
            self.procs[job.id] = _AdoptedProcess(existing)  # type: ignore[assignment]
            st = self.states[job.id]
            st.status = "running"
            st.pid = existing
            self._record(job.id, "adopt", f"pid={existing}")
            _log(f"↪ 接管已运行 [{job.id}] pid={existing}")
            return

        out_path = LOG_DIR / f"overnight_{job.log_name}.log"
        err_path = LOG_DIR / f"overnight_{job.log_name}.err.log"
        LOG_DIR.mkdir(exist_ok=True)

        with open_log_append(out_path) as fo:
            fo.write(
                f"\n{'=' * 60}\n[supervisor R{self.round_num}] {job.module} {' '.join(job.args)}\n"
                f"开始: {_ts()}\n{'=' * 60}\n"
            )
        fo = open_log_append(out_path)
        fe = open_log_append(err_path)
        cmd = [str(_python()), "-X", "utf8", "-m", job.module, *job.args]
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=fo,
            stderr=fe,
            env=subprocess_log_env(),
        )
        self.procs[job.id] = proc
        self.log_handles[job.id] = (fo, fe)
        st = self.states[job.id]
        st.status = "running"
        st.attempts += 1
        st.pid = proc.pid
        st.last_error = ""
        self._record(job.id, "start", f"attempt={st.attempts} pid={proc.pid}")
        _log(f"▶ 启动 [{job.id}] {job.description}  pid={proc.pid}  (第 {st.attempts} 次)")

    def _close_handles(self, job_id: str) -> None:
        handles = self.log_handles.pop(job_id, None)
        if handles:
            for h in handles:
                try:
                    h.close()
                except OSError:
                    pass

    def _finish_job(self, job: JobSpec, proc: subprocess.Popen) -> None:
        code = proc.returncode if proc.returncode is not None else -1
        self._close_handles(job.id)
        validator = VALIDATORS[job.id]
        ok, msg = validator(proc, code)

        st = self.states[job.id]
        if ok:
            st.status = "success"
            st.last_success = msg
            st.last_error = ""
            self._record(job.id, "success", msg)
            _log(f"✓ 成功 [{job.id}] {msg}")
        else:
            st.status = "failed"
            st.last_error = msg
            self._record(job.id, "failed", f"exit={code} {msg}")
            _log(f"✗ 失败 [{job.id}] exit={code} — {msg}")

    def _batch_jobs_done(self) -> bool:
        for job in JOBS:
            if job.daemon:
                continue
            st = self.states[job.id]
            if st.status not in ("success",):
                proc = self.procs.get(job.id)
                if proc is not None and proc.poll() is None:
                    return False
                if st.status != "success":
                    return False
        return True

    def _all_batch_success(self) -> bool:
        return all(
            self.states[j.id].status == "success"
            for j in JOBS
            if not j.daemon
        )

    def _jobs_need_run(self) -> List[JobSpec]:
        need: List[JobSpec] = []
        for job in JOBS:
            st = self.states[job.id]
            if job.daemon:
                proc = self.procs.get(job.id)
                if proc is None or proc.poll() is not None:
                    need.append(job)
                continue
            if st.status == "success":
                continue
            if self._already_success(job):
                _log(f"✓ 跳过 [{job.id}] 已完成 — {st.last_success}")
                continue
            proc = self.procs.get(job.id)
            if proc is None or proc.poll() is not None:
                need.append(job)
        return need

    def _poll_running(self) -> None:
        for job in JOBS:
            proc = self.procs.get(job.id)
            if proc is None or proc.poll() is not None:
                continue
            if job.daemon and proc.poll() is not None:
                self._finish_job(job, proc)
            # batch jobs handled when round ends

    def _collect_batch_results(self) -> None:
        for job in JOBS:
            if job.daemon:
                continue
            proc = self.procs.get(job.id)
            if proc is None:
                continue
            if proc.poll() is None:
                continue
            if self.states[job.id].status == "running":
                self._finish_job(job, proc)

    def _status_line(self) -> str:
        parts = []
        for job in JOBS:
            st = self.states[job.id]
            proc = self.procs.get(job.id)
            alive = proc is not None and proc.poll() is None
            mark = {"success": "OK", "failed": "FAIL", "running": "RUN", "pending": "WAIT"}.get(st.status, "?")
            pid = f"pid={proc.pid}" if alive else ""
            parts.append(f"{job.id}={mark}({st.attempts}){pid}")
        return " | ".join(parts)

    def run(self) -> int:
        LOG_DIR.mkdir(exist_ok=True)
        _log("=" * 60)
        _log("过夜监听器启动 — 失败自动重试直到全部成功")
        _log(f"状态文件: {STATE_FILE}")
        _log("=" * 60)

        for job in JOBS:
            if not job.daemon and self._already_success(job):
                _log(f"✓ 检测到 [{job.id}] 已完成 — {self.states[job.id].last_success}")

        try:
            while True:
                for job in self._jobs_need_run():
                    self._spawn(job)

                self._poll_running()
                _log(f"轮询 R{self.round_num}: {self._status_line()}")

                batch_running = any(
                    not j.daemon
                    and (p := self.procs.get(j.id)) is not None
                    and p.poll() is None
                    for j in JOBS
                )

                if not batch_running:
                    self._collect_batch_results()

                if self._all_batch_success():
                    _log("=" * 60)
                    _log("全部批处理任务已成功完成！")
                    for job in JOBS:
                        if not job.daemon:
                            _log(f"  [{job.id}] {self.states[job.id].last_success}")
                    _log("守护任务 15_link 继续运行；Ctrl+C 可停止监听器")
                    _log("=" * 60)
                    while True:
                        job15 = next(j for j in JOBS if j.id == "15_link")
                        proc = self.procs.get("15_link")
                        if proc is None or proc.poll() is not None:
                            _log("15_link 守护进程退出，正在重新拉起…")
                            self.states["15_link"].status = "failed"
                            self._spawn(job15)
                        time.sleep(POLL_SEC)
                    # unreachable unless break

                if not batch_running:
                    failed = [j.id for j in JOBS if not j.daemon and self.states[j.id].status == "failed"]
                    if failed:
                        delay = min(ROUND_COOLDOWN_SEC * self.round_num, MAX_RETRY_DELAY_SEC)
                        _log(f"第 {self.round_num} 轮结束，失败: {failed} — {delay}s 后重试")
                        self.round_num += 1
                        self._save_state()
                        time.sleep(delay)
                        for jid in failed:
                            self.states[jid].status = "pending"
                    else:
                        time.sleep(POLL_SEC)
                else:
                    time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            _log("监听器已中断（子进程可能仍在运行）")
            for proc in self.procs.values():
                if proc.poll() is None:
                    _log(f"  仍在运行 pid={proc.pid}")
            return 130


def main() -> int:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    return Supervisor().run()


if __name__ == "__main__":
    raise SystemExit(main())
