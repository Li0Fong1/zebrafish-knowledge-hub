# -*- coding: utf-8 -*-
"""启动菜单：由 Python 输出中文，避免 Windows CMD 解析 bat 时乱码/误执行。"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MENU = """
========================================
  斑马鱼基因问答体 - 启动菜单
========================================
  1. 命令行问答 (CLI)
  2. Web 界面 (http://127.0.0.1:8080)  <- 推荐试用
  3. 初始化 MySQL 数据库
  4. 采集经典基因 (Ensembl, ~35个)
  5. 全基因组采集 (Ensembl 最新版)
  6. 数据增强 (表达量+文献)
  7. 构建基因别名/ID映射 (NCBI+Ensembl, --repair-xrefs 修复)
  8. 采集 Bgee 成体组织表达
  9. 安装 BLAST+ 并构建 cDNA 索引
 10. 扩充文献 RAG (WoS+PubMed 广搜 zebrafish)
 11. 打包测试版 (给他人试用, 无需 Python)
 12. 导出基因数据 CSV (供外部清洗)
 13. 导入清洗后的 CSV 到 MySQL
 14. 文献→基因关联 (增量写入 gene_reference, 可与10并行)
 15. 公网访问 (临时隧道, 发链接给他人试用)
 16. 全面数据补全 (过夜自动, GO+文献+别名+BLAST)
 17. 导入 WoS RIS (含 wos数据包, 文献分层)
 18. GO 深度注释补全 (无 GO 基因)
 19. 过夜监听器 (17+14+18+07+20 自动重试)
 20. GRCz12tu 参考基因组 ID 映射
 21. 产品化全自动 (租户+工作流+Web+过夜)
 22. 全量数据导出 (MySQL+文献+WoS+RAG)
  0. 退出
========================================
"""


def _run_bat(rel: str, *args: str, title: Optional[str] = None) -> int:
    if title:
        os.environ["CONSOLE_TITLE"] = title
        try:
            import bootstrap
            bootstrap._fix_windows_console()
        except Exception:
            pass
    bat = os.path.join(ROOT, rel.replace("/", os.sep))
    cmd = ["cmd", "/c", "call", bat, *args]
    return subprocess.run(cmd, cwd=ROOT).returncode


def main() -> int:
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    os.chdir(ROOT)

    from core.entry import bootstrap_app

    bootstrap_app()

    from common_utils import safe_input, safe_print

    os.environ["CONSOLE_TITLE"] = "斑马鱼基因问答体"
    try:
        import bootstrap
        bootstrap._fix_windows_console()
    except Exception:
        pass

    actions = {
        "1": lambda: _run_bat(r"scripts\start_cli.bat"),
        "2": lambda: _run_bat(r"scripts\start_web.bat"),
        "3": lambda: _run_bat(r"scripts\init_database.bat"),
        "4": lambda: _run_bat(r"scripts\collect_genes.bat"),
        "5": lambda: _run_bat(r"scripts\collect_genome.bat"),
        "6": lambda: _run_bat(r"scripts\boost_data.bat"),
        "7": lambda: _run_bat(r"scripts\build_gene_aliases.bat"),
        "8": lambda: _run_bat(r"scripts\collect_tissue_expression.bat"),
        "9": lambda: _run_bat(r"scripts\setup_blast.bat"),
        "10": lambda: _run_bat(
            r"scripts\collect_literature.bat",
            "--source", "pubmed", "--pubmed-backend", "epmc",
            title="斑马鱼基因问答体 - 文献采集",
        ),
        "11": lambda: _run_bat(r"scripts\pack_for_testers.bat"),
        "12": lambda: _run_bat(r"scripts\export_gene_data_for_cleaning.bat"),
        "13": lambda: _run_bat(r"scripts\import_cleaned_gene_data.bat"),
        "14": lambda: _run_bat(
            r"scripts\link_literature_genes.bat",
            "--loop", "--interval", "120",
            title="斑马鱼基因问答体 - 文献关联",
        ),
        "15": lambda: _run_bat(
            r"scripts\expose_internet.bat",
            title="斑马鱼基因问答体 - 公网隧道",
        ),
        "16": lambda: _run_bat(
            r"scripts\run_full_data_pipeline.bat",
            title="斑马鱼基因问答体 - 全面数据补全",
        ),
        "17": lambda: _run_bat(
            r"scripts\import_wos_ris.bat",
            title="斑马鱼基因问答体 - WoS RIS 导入",
        ),
        "18": lambda: _run_bat(
            r"scripts\backfill_go.bat",
            title="斑马鱼基因问答体 - GO 补全",
        ),
        "19": lambda: _run_bat(
            r"scripts\start_overnight_supervisor.bat",
            title="斑马鱼基因问答体 - 过夜监听器",
        ),
        "20": lambda: _run_bat(
            r"scripts\build_grcz12tu_xref.bat",
            title="斑马鱼基因问答体 - GRCz12tu 映射",
        ),
        "21": lambda: _run_bat(
            r"scripts\start_product_autostart.bat",
            title="斑马鱼基因问答体 - 产品化全自动",
        ),
        "22": lambda: _run_bat(
            r"scripts\export_full_data_bundle.bat",
            title="斑马鱼基因问答体 - 全量数据导出",
        ),
    }

    while True:
        os.system("cls")
        safe_print(MENU.strip())
        safe_print("")
        choice = safe_input("请输入数字 [0-22] 后回车: ").strip()
        if choice == "0":
            return 0
        action = actions.get(choice)
        if action is None:
            safe_print("\n[提示] 无效输入。请重新输入 0-22。\n")
            safe_input("按回车返回菜单…")
            continue
        action()
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[错误] 启动菜单失败: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        raise SystemExit(1)
