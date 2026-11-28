# -*- coding: utf-8 -*-
"""扫描项目根目录的散落文件，生成整理建议清单（只读，不移动）。

分类规则：
  - 图片/截图      → docs/图件/ 或 _assets/
  - 报告/文档 .md   → docs/报告/
  - 一次性/临时脚本 → scripts/归档/ 或 _archive/
  - 备份目录       → 保持（已按 _backup_* 命名）
  - 数据文件       → 保持或 data/
  - 运行代码       → 保持（api/core/database/rag/llm/web/sql/scripts/deploy/cli）

输出 docs/文件夹整理建议.md：每个文件一行，给出建议目标目录与理由。
不移动、不删除任何文件；移动动作由单独的 organize_project_files.py 在用户确认后执行。
"""
from __future__ import annotations

import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 保留在根目录的已知文件/目录
KEEP_FILES = {
    "启动.bat", "config.py", ".env", ".env.example", ".gitignore",
    "README.md", "requirements.txt", "pyproject.toml", "setup.py",
    "部署Ensembl官方数据.bat", "重启并验证.bat", "验收坐标.bat",
    "一键清理与体检.bat", "修复凭据清理.bat",
    "改数据库密码_管理员运行.bat",  # 常用运维入口，保留
    "LICENSE",  # 许可证，必须在根
    "Figure legend.pptx",  # 图集产物，与 Figure/ 配套
}
KEEP_DIRS = {
    "api", "core", "database", "rag", "llm", "web", "sql", "scripts",
    "deploy", "cli", "docs", "data", "models", "tools", "results_md",
    ".workbuddy", "__pycache__", "templates", "static", "tests",
    # 运行/数据目录：文件量大且被代码引用，保持原位，不归档
    "logs", "runtime_logs", "output", "merged_corpus", "patches",
    "_shots", "解析文件", "测试集", "论文学习", "参考文件", "密码箱",
    "项目阶段", "Figure", "agent",
    # 归档备份目录已规范命名，保持
    "_backup_7b_removed_20261031", "_presync_backup_20260930", "_server_web",
}

IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
DOC_EXT = {".md", ".docx", ".pdf", ".txt", ".rst"}
ARCHIVE_HINT = ("_tmp", "tmp_", "备份", "副本", "old", "old_", "_old", "test_", "copy", "副本")


def classify(p: Path) -> tuple[str, str]:
    name = p.name
    ext = p.suffix.lower()
    if ext in IMG_EXT:
        return "docs/图件/", "图片/截图归档"
    if p.is_dir():
        if name.startswith("_backup") or name.startswith("backup"):
            return "(保持)", "备份目录，已规范命名"
        if name.startswith("_") or any(h in name for h in ARCHIVE_HINT):
            return "_archive/", "临时/归档目录"
        return "(保持)", "未识别目录，先不自动移动"
    if name in KEEP_FILES:
        return "(保持)", "项目入口/配置/常用脚本"
    if ext in DOC_EXT:
        return "docs/报告/", "文档/报告"
    if name.startswith("_") or any(h in name for h in ARCHIVE_HINT):
        return "scripts/归档/", "一次性/临时脚本"
    if ext == ".py":
        return "scripts/归档/", "散落的 Python 脚本"
    if ext in {".bat", ".ps1", ".sh"}:
        return "scripts/归档/", "散落的运维脚本"
    if ext in {".json", ".jsonl", ".csv", ".tsv"}:
        return "data/", "数据文件"
    return "(待判断)", "未识别类型，需人工确认"


def main() -> int:
    lines = [
        "# 文件夹整理建议（只读扫描）",
        "",
        "- 扫描时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "- 说明：本清单**不改变任何文件**，仅为整理建议。确认后再执行移动。",
        "",
        "## 根目录散落文件建议",
        "",
        "| 文件/目录 | 类型 | 建议位置 | 理由 |",
        "|---|---|---|---|",
    ]
    items = []
    for p in sorted(ROOT.iterdir(), key=lambda x: x.name):
        if p.name in KEEP_DIRS or p.name in KEEP_FILES:
            continue
        if p.name.startswith("."):
            continue
        target, reason = classify(p)
        typ = "目录" if p.is_dir() else "文件"
        items.append((p.name, typ, target, reason))

    for name, typ, target, reason in items:
        lines.append("| %s | %s | %s | %s |" % (name, typ, target, reason))

    move = [x for x in items if x[2] != "(保持)" and x[2] != "(待判断)"]
    lines += [
        "",
        "## 汇总",
        "",
        "- 根目录待整理项：**%d**" % len(move),
        "- 其中图片/截图：%d；文档：%d；脚本/归档：%d；数据：%d" % (
            sum(1 for x in move if x[2] == "docs/图件/"),
            sum(1 for x in move if x[2] == "docs/报告/"),
            sum(1 for x in move if x[2] in ("scripts/归档/", "_archive/")),
            sum(1 for x in move if x[2] == "data/"),
        ),
        "- 待人工判断：%d" % sum(1 for x in items if x[2] == "(待判断)"),
        "",
        "## 整理原则（待确认）",
        "",
        "1. 只移动，不删除；移动前先做整目录备份。",
        "2. 每批最多 10 项，逐批校验。",
        "3. 保留所有项目入口、配置、常用 bat；不碰 api/core/database/rag/llm/web/sql/scripts/deploy/cli/docs/data/models/tools/results_md。",
        "4. 下划线开头的临时脚本统一进 scripts/归档/，便于日后清理。",
        "",
    ]

    rp = ROOT / "docs" / "文件夹整理建议.md"
    rp.parent.mkdir(exist_ok=True)
    rp.write_text("\n".join(lines), encoding="utf-8")
    print("扫描完成：%s" % rp)
    print("待整理 %d 项，待判断 %d 项" % (len(move), sum(1 for x in items if x[2] == "(待判断)")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
