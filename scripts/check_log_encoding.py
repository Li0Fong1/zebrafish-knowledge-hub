# -*- coding: utf-8 -*-
"""扫描 logs/ 下日志是否为合法 UTF-8（只读检查，不修改任何数据）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from core.log_io import verify_utf8_file
from common_utils import safe_print


def main() -> int:
    log_dir = ROOT / "logs"
    if not log_dir.is_dir():
        safe_print("[提示] logs/ 目录不存在")
        return 0

    files = sorted(log_dir.glob("**/*.log"))
    if not files:
        safe_print("[提示] 未找到 .log 文件")
        return 0

    bad = 0
    safe_print(f"检查 {len(files)} 个日志文件（UTF-8，只读）…\n")
    for f in files:
        ok, msg = verify_utf8_file(f)
        rel = f.relative_to(ROOT)
        if ok:
            safe_print(f"  OK  {rel}")
        else:
            bad += 1
            safe_print(f"  !!  {rel} — {msg}")

    safe_print("")
    if bad:
        safe_print(f"[结果] {bad} 个文件编码异常（数据库与 JSON 数据未检查）")
        return 1
    safe_print("[结果] 全部日志均为合法 UTF-8，数据内容未损坏")
    safe_print("[提示] PowerShell 请用: python scripts/tail_log.py 18")
    safe_print("       或: Get-Content path -Encoding utf8 -Tail 20")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
