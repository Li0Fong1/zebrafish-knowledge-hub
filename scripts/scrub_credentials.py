# -*- coding: utf-8 -*-
"""永久移除源码中的硬编码凭据。

- 用结构化特征识别凭据字面量，脚本自身不含任何明文口令。
- Python 文件改为 os.environ 读取（缺失即报错，不再有默认明文）；
  shell/bat 改为环境变量引用；文档/示例改为占位符。
- 每个 Python 文件改动后用 ast.parse 校验，失败自动还原该文件。
- 不创建任何备份副本（备份里会残留明文，违背"永久删除"）。
- 报告只写文件路径与替换次数，绝不输出原值。

用法：
    python scripts/scrub_credentials.py --dry-run   # 预览
    python scripts/scrub_credentials.py             # 实际执行
"""
from __future__ import annotations

import argparse
import ast
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 结构化特征：只匹配独特的口令构词，绝不含通用数字。
# 2026-11-16 事故：曾把独立的 123456 纳入特征，误伤 330 个文件（含模型词表）。
# 通用弱口令数字无法与合法数据区分，必须由人工在明确上下文里单独处理。
CRED = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9]*_remote_[0-9]{4}"
    r"|deng[0-9]_[0-9]{4,})"
)

TARGET_EXT = {
    ".py", ".bat", ".sh", ".sql", ".md", ".json",
    ".cnf", ".yml", ".yaml", ".toml", ".ini", ".txt",
    ".example", ".ps1", ".cfg",
}
SKIP_DIRS = {
    ".git", ".workbuddy", "__pycache__", ".venv", "node_modules",
    "_shots", "_backup_7b_removed_20261031", "_presync_backup_20260930",
    "_server_web", "_verify", "_v3", "_opt", "_ms_media", "_docx_media",
    "解析文件", "论文学习", "测试集", "参考文件", "数据库整理结果",
    "数据库整理最终版", "密码箱", "data", "logs", "output",
}

MYSQL_HINT = re.compile(
    r"mysql|pymysql|create_engine|password|passwd|db_pass|db_pwd|"
    r"database|connect\(|-p", re.I)
SSH_HINT = re.compile(r"zf_pass|ssh|paramiko|sftp|remote_host|hostname", re.I)

# 只有凭据上下文里的字面量才处理，避免误伤 random.seed(...) 一类的普通数字
CRED_CONTEXT = re.compile(
    r"password|passwd|pwd|-p\b|zf_pass|secret|token|credential|"
    r"mysql|db_pass|db_pwd|create_engine|connect\(|api_key|auth", re.I)

# 与 CRED 一致：只含真口令构词，不含通用数字
CRED_LITERAL = (r"(?:[A-Za-z][A-Za-z0-9]*_remote_[0-9]{4}"
               r"|deng[0-9]_[0-9]{4,})")

# os.environ.get("X", "<literal>") / os.getenv("X", "<literal>") 去掉默认明文
GETENV_DEFAULT = re.compile(
    r"(os\.environ\.get|os\.getenv)\(\s*([\"'])([A-Za-z0-9_]+)\2\s*,\s*"
    r"([\"'])" + CRED_LITERAL + r"\4\s*\)"
)


def _py_repl(line: str) -> str:
    # 先处理 getenv/get 的默认值
    line = GETENV_DEFAULT.sub(lambda m: '__import__("os").environ["%s"]' % m.group(3), line)
    if not CRED.search(line) or not CRED_CONTEXT.search(line):
        return line

    var = ("ZF_PASS" if (SSH_HINT.search(line) and not MYSQL_HINT.search(line))
           else "MYSQL_PASSWORD")

    # 带引号的字面量赋值 → 环境变量读取
    line = re.sub(r"([\"'])" + CRED_LITERAL + r"\1",
                  '__import__("os").environ["%s"]' % var, line)
    # -p<literal> 形式的裸口令（拼在字符串里的 shell 命令）
    line = re.sub(r"-p" + CRED_LITERAL + r"\b",
                  '-p"$MYSQL_PASSWORD"', line)
    return line


def _shell_repl(line: str, is_bat: bool, is_cnf: bool) -> str:
    if not CRED.search(line) or not CRED_CONTEXT.search(line):
        return line
    if is_cnf:
        return CRED.sub("<set-via-env>", line)
    var = ("ZF_PASS" if (SSH_HINT.search(line) and not MYSQL_HINT.search(line))
           else "MYSQL_PASSWORD")
    token = ("%" + var + "%") if is_bat else ("$" + var)
    return CRED.sub(token, line)


def _doc_repl(line: str) -> str:
    if not CRED.search(line) or not CRED_CONTEXT.search(line):
        return line
    return CRED.sub("<set-via-env>", line)


def process(path: Path, dry: bool) -> tuple[int, str]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return 0, "skip(encoding)"
    if not CRED.search(text) and not GETENV_DEFAULT.search(text):
        return 0, ""

    suffix = path.suffix.lower()
    is_py = suffix == ".py"
    is_bat = suffix == ".bat"
    is_shell = suffix in {".sh", ".ps1", ".cfg", ".cnf", ".ini"}

    lines = text.splitlines(keepends=True)
    hits = 0
    out = []
    for line in lines:
        before = line
        if is_py:
            after = _py_repl(line)
        elif is_bat or is_shell:
            after = _shell_repl(line, is_bat, suffix in {".cnf", ".cfg", ".ini"})
        else:
            after = _doc_repl(line)
        if after != before:
            hits += 1
        out.append(after)

    new_text = "".join(out)
    if hits == 0 and new_text == text:
        return 0, ""

    if is_py:
        # 采用 __import__("os").environ[...]，无需插入 import，
        # 不会破坏 from __future__ 首行约束或模块 docstring 位置。
        try:
            ast.parse(new_text)
        except SyntaxError as exc:
            return 0, "REVERT(syntax:%s)" % exc.lineno

    if not dry:
        try:
            path.write_bytes(new_text.encode("utf-8"))
        except OSError as exc:
            return 0, "skip(write:%s)" % exc.__class__.__name__
    return hits, "ok"


def iter_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            # .env 是运行必需的真实配置，绝不能改写；只处理示例文件。
            # .env.local / .env.bak 之类同样跳过。
            if fn.startswith(".env") and fn != ".env.example":
                continue
            p = Path(dirpath) / fn
            if p.suffix.lower() in TARGET_EXT or fn == ".env.example":
                yield p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    report = []
    total_files = 0
    total_lines = 0
    problems = []
    # 熔断：本项目的真口令文件历史上约 20 个；超过 60 基本可判定为特征过宽。
    # 一旦触发，立即停止写盘并报告，避免重演 330 文件误伤。
    MAX_FILES = 60
    for p in iter_files():
        if total_files >= MAX_FILES:
            problems.append("  ⚠ 触发熔断：已改动 %d 个文件（上限 %d），停止处理。" % (total_files, MAX_FILES))
            break
        n, status = process(p, args.dry_run)
        if n:
            total_files += 1
            total_lines += n
            report.append("  %-70s %d 行  %s" % (p.relative_to(ROOT).as_posix(), n, status))
        if status.startswith("REVERT") or status.startswith("skip(write"):
            problems.append("  %s -> %s" % (p.relative_to(ROOT).as_posix(), status))

    lines = [
        "# 凭据清理报告",
        "",
        "- 模式：%s" % ("预览（未写盘）" if args.dry_run else "已执行"),
        "- 清理文件数：%d" % total_files,
        "- 替换行数：%d" % total_lines,
        "- 说明：凭据改为环境变量读取，报告不记录任何原值。",
        "",
        "## 涉及文件",
        "",
    ]
    lines += report or ["  （无）"]
    lines += ["", "## 需要人工关注", ""]
    lines += problems or ["  （无）"]
    lines += [
        "",
        "## 后续",
        "",
        "1. 在运行这些脚本的环境里配置 MYSQL_PASSWORD / ZF_PASS（可放项目 .env）。",
        "2. 这些值曾出现在源码中，务必在数据库侧轮换一次口令。",
        "3. 若仓库有提交历史，历史版本中的值无法通过改工作区清除。",
        "",
    ]

    out_dir = ROOT / "docs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "凭据清理报告.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")

    print("文件数=%d 行数=%d 异常=%d" % (total_files, total_lines, len(problems)))
    print("报告：%s" % out_path)
    for r in report:
        print(r)
    for q in problems:
        print(q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
