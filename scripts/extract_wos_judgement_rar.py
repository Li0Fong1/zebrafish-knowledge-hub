# -*- coding: utf-8 -*-
"""解压 result_wosc.rar 中的 WoS 判别 Markdown。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print

RAR = ROOT / "项目阶段" / "05_数据与库" / "归档" / "result_wosc.rar"
OUT = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "merged_judgement_full" / "_src" / "wos"
TARGET = OUT / "result_95000.md"


def main() -> int:
    if TARGET.is_file():
        safe_print(f"[跳过] 已存在 {TARGET}")
        return 0
    if not RAR.is_file():
        safe_print(f"[错误] 未找到 {RAR}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    import subprocess

    try:
        subprocess.run(
            ["tar", "-xf", str(RAR), "-C", str(OUT)],
            check=True,
            capture_output=True,
        )
        md_files = list(OUT.glob("*.md"))
        main_md = OUT / "result_95000.md"
        if not main_md.is_file() and md_files:
            md_files[0].replace(main_md)
        if main_md.is_file():
            safe_print(f"[成功] tar 解压 → {main_md}")
            return 0
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    try:
        import rarfile
    except ImportError:
        safe_print("[错误] tar/rarfile 均不可用，请手动解压 result_wosc.rar")
        return 1
    with rarfile.RarFile(RAR) as rf:
        for name in rf.namelist():
            if name.endswith(".md"):
                rf.extract(name, OUT)
                extracted = OUT / name
                if extracted != TARGET and extracted.is_file():
                    extracted.replace(TARGET)
                safe_print(f"[成功] 解压 {name} → {TARGET}")
                return 0
    safe_print("[错误] RAR 内未找到 .md 文件")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
