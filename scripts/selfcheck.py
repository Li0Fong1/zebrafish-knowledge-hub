# -*- coding: utf-8 -*-
"""一键自检：把"人工全量测试"变成一条可重复的命令。

用法（在仓库根目录执行）：
    .venv\\Scripts\\python.exe scripts\\selfcheck.py          # 全部检查
    .venv\\Scripts\\python.exe scripts\\selfcheck.py --git    # 额外做「拟提交清单」安全检查

检查项：
  1. requirements.txt 的固定版本 与 METHODS 声明的 6 个工具版本 是否逐一一致
  2. methods   : scripts/verify_methods_numbers.py（METHODS 里每个数字能否由产物重算）
  3. datapack  : scripts/build_figshare_package.py --check（含"包内多出文件"检测）
  4. frontend  : web/js/*.js 语法（node --check）
  5. python    : 关键模块可编译（py_compile）
  6. gates     : 引用相关性打分 RRS + 富集方面/来源标注（scripts/test_ref_relevance.py / test_enrichment_labels.py）
  7. figures   : S4/S5 图件（出图脚本 --check + 独立重读数据源 + 反向查表）
  8. secrets   : 仓库内不得出现服务器凭据 / 主机名（排除 .gitignore 覆盖的目录）
  9. --git     : 临时 git 目录算「拟提交清单」，逐文件扫描敏感词

退出码：0 = 全部通过；1 = 有失败项。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
RESULTS: list[tuple[str, bool, str]] = []

# 与 .gitignore 保持一致（用于排除不应进仓库/不应扫描的大目录与本机环境）
IGNORED_DIRS = {".venv", "venv", "__pycache__", "node_modules", "models", "pdf库", "merged_corpus",
                "data", "logs", "解析文件", "项目阶段", "参考文件", "tools", "release", "runtime_logs",
                "_server_web", "_presync_backup_20260930", ".vscode", ".idea", ".workbuddy"}
BINARY_SUFFIX = {".png", ".jpg", ".jpeg", ".pdf", ".pptx", ".zip", ".exe", ".pyc", ".fa", ".fasta",
                 ".gz", ".tar", ".whl", ".dll", ".so", ".bin", ".xlsx", ".docx"}


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str]:
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, timeout=timeout)
    out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace").strip()
    return r.returncode, out


def check_requirements() -> None:
    """requirements.txt ↔ METHODS 声明的 6 个工具版本。"""
    req = {}
    rp = ROOT / "requirements.txt"
    if not rp.exists():
        record("req/versions", False, "requirements.txt 缺失")
        return
    for line in rp.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if "==" in line and not line.startswith("-"):
            k, v = line.split("==", 1)
            req[k.strip().lower().split("[")[0]] = v.strip()
    claims: dict[str, str] = {}
    for doc in ("docs/METHODS/METHODS.md", "docs/paper/CLAIM_AUDIT.md"):
        p = ROOT / doc
        if not p.exists():
            continue
        t = p.read_text(encoding="utf-8", errors="replace")
        for disp, pat in (("pymupdf", r"PyMuPDF\s*([\d.]+)"), ("pypdf", r"pypdf\s*([\d.]+)"),
                          ("pdfplumber", r"pdfplumber\s*([\d.]+)"), ("lxml", r"lxml\s*([\d.]+)"),
                          ("fastapi", r"FastAPI\s*([\d.]+)"), ("uvicorn", r"Uvicorn\s*([\d.]+)")):
            m = re.search(pat, t)
            if m:
                claims.setdefault(disp, m.group(1))
    if not claims:
        record("req/versions", False, "在 METHODS/CLAIM_AUDIT 里找不到声明版本")
        return
    bad = [f"{k}(req={req.get(k, '缺失')} vs 声明={v})" for k, v in claims.items() if req.get(k) != v]
    record("req/versions", not bad,
           f"{len(claims) - len(bad)}/{len(claims)} 与 METHODS 一致" + ("；不一致: " + ", ".join(bad) if bad else ""))


def check_scripts() -> None:
    for name, args in (("methods", ["scripts/verify_methods_numbers.py"]),
                       ("datapack", ["scripts/build_figshare_package.py", "--check"])):
        p = ROOT / args[0]
        if not p.exists():
            record(name, False, f"{args[0]} 不存在")
            continue
        rc, out = run([PY, "-X", "utf8", *args])
        tail = [l for l in out.splitlines() if l.strip()][-1:] or [""]
        record(name, rc == 0, tail[0][:120])


def check_frontend() -> None:
    js = sorted((ROOT / "web" / "js").glob("*.js"))
    if not js:
        record("frontend", False, "web/js 下没有 js 文件")
        return
    node = shutil.which("node")
    if not node:
        record("frontend", True, f"未安装 node，跳过 {len(js)} 个文件的语法检查")
        return
    bad = []
    for f in js:
        rc, out = run([node, "--check", str(f)], timeout=120)
        if rc != 0:
            bad.append(f"{f.name}: {out.splitlines()[-1][:80] if out else 'syntax error'}")
    record("frontend", not bad, f"{len(js)} 个 js" + ("；失败: " + " | ".join(bad) if bad else " 语法全部通过"))


def check_python() -> None:
    targets = []
    for d in ("api", "rag", "llm", "agent", "core", "database", "cli"):
        p = ROOT / d
        if p.is_dir():
            targets += [f for f in p.rglob("*.py") if "__pycache__" not in f.parts]
    bad = []
    for f in targets:
        rc, out = run([PY, "-X", "utf8", "-m", "py_compile", str(f)], timeout=120)
        if rc != 0:
            bad.append(f"{f.relative_to(ROOT)}: {out.splitlines()[-1][:80] if out else 'compile error'}")
    record("python", not bad, f"{len(targets)} 个模块" + ("；失败: " + " | ".join(bad[:3]) if bad else " 全部可编译"))


def iter_repo_files():
    for p in ROOT.rglob("*"):
        if not p.is_file() or set(p.parts) & IGNORED_DIRS:
            continue
        if p.suffix.lower() in BINARY_SUFFIX or p.name == ".env":
            continue
        try:
            if p.stat().st_size > 3_000_000:
                continue
        except OSError:
            continue
        yield p


def check_quality_gates() -> None:
    """质量闸门：引用相关性打分（RRS）+ 富集方面/来源标注。

    对应 2026-10-10 的两项需求（无关引用、富集看不出方面），
    两个脚本都是离线可复算的（不联网、不连库、不调模型）。
    """
    cases = [
        ("refs-rrs", "scripts/test_ref_relevance.py"),
        ("enrich-labels", "scripts/test_enrichment_labels.py"),
    ]
    for name, rel in cases:
        p = ROOT / rel
        if not p.exists():
            record(name, False, f"{rel} 不存在")
            continue
        rc, out = run([PY, "-X", "utf8", rel], timeout=600)
        tail = [l for l in out.splitlines() if l.strip()][-1:] or [""]
        record(name, rc == 0, tail[0][:130])


def check_figures() -> None:
    """S4/S5 图件：出图脚本 --check + 独立重读数据源 + 反向查表。"""
    p = ROOT / "scripts" / "audit_s4_s5_figures.py"
    if not p.exists():
        record("figures-s4s5", False, "scripts/audit_s4_s5_figures.py 不存在")
        return
    rc, out = run([PY, "-X", "utf8", "scripts/audit_s4_s5_figures.py"], timeout=1800)
    tail = [l for l in out.splitlines() if l.strip()][-1:] or [""]
    record("figures-s4s5", rc == 0, tail[0][:130])


SECRET_PAT = re.compile(r"REDACTED_HOST|REDACTED_SSH_PASS", re.I)
# 本文件自身含待搜索的模式串，必须自排除，否则永远误报
SELF_PATH = Path(__file__).resolve()


def check_secrets() -> None:
    hits = []
    for p in iter_repo_files():
        if p.resolve() == SELF_PATH:
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if SECRET_PAT.search(t):
            hits.append(str(p.relative_to(ROOT)))
    record("secrets", not hits, f"源码区命中 {len(hits)} 个文件"
           + ("：" + ", ".join(hits[:5]) if hits else "（无服务器主机名/密码）"))


def check_git_plan() -> None:
    """用临时 git 目录算「拟提交清单」，逐文件扫描敏感词（不污染工作区）。"""
    git = shutil.which("git")
    if not git:
        record("git-plan", True, "未安装 git，跳过")
        return
    tmp = tempfile.mkdtemp(prefix="zf_selfcheck_git_")
    try:
        subprocess.run([git, "init", "-q", tmp], check=True, capture_output=True)
        rc, out = run([git, "-c", "core.quotepath=false", "-c", "core.safecrlf=false",
                       "--git-dir", str(Path(tmp) / ".git"), "--work-tree", str(ROOT),
                       "add", "-A", "--dry-run"], timeout=900)
        files = re.findall(r"^add '(.+)'$", out, re.M)
        if not files:
            record("git-plan", False, "拟提交清单为空（.gitignore 可能过宽）")
            return
        total = sum((ROOT / f).stat().st_size for f in files if (ROOT / f).is_file())
        hits = []
        for f in files:
            p = ROOT / f
            if not p.is_file() or p.suffix.lower() in BINARY_SUFFIX or p.name == ".gitignore":
                continue
            if p.resolve() == SELF_PATH:
                continue
            try:
                t = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if SECRET_PAT.search(t):
                hits.append(f)
        record("git-plan", not hits,
               f"拟提交 {len(files)} 文件 / {total / 1048576:.1f} MB"
               + ("；敏感命中: " + ", ".join(hits[:5]) if hits else "；无敏感命中"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--git", action="store_true", help="额外做「拟提交清单」安全检查")
    args = ap.parse_args()
    print(f"selfcheck @ {ROOT}\n" + "-" * 68)
    check_requirements()
    check_scripts()
    check_frontend()
    check_python()
    check_quality_gates()
    check_figures()
    check_secrets()
    if args.git:
        check_git_plan()
    print("-" * 68)
    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"共 {len(RESULTS)} 项：通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}"
          + (f" → {failed}" if failed else " → 全部通过"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
