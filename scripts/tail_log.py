# -*- coding: utf-8 -*-
"""
UTF-8 安全查看日志（替代 PowerShell Get-Content，避免乱码）。

用法:
  python scripts/tail_log.py 18
  python scripts/tail_log.py logs/overnight_19_go.log -n 30
  python scripts/tail_log.py 15 -f
  python scripts/check_log_encoding.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from core.log_io import LOG_PRESETS, print_log, resolve_log_path, verify_utf8_file
from common_utils import safe_print


def main() -> int:
    parser = argparse.ArgumentParser(description="UTF-8 查看日志（不乱码）")
    parser.add_argument(
        "log",
        nargs="?",
        default="supervisor",
        help=f"日志路径或预设: {', '.join(sorted(LOG_PRESETS))}",
    )
    parser.add_argument("-n", "--lines", type=int, default=20, help="显示末尾行数")
    parser.add_argument("-f", "--follow", action="store_true", help="持续跟踪")
    parser.add_argument("--verify", action="store_true", help="仅验证 UTF-8 编码")
    args = parser.parse_args()

    path = resolve_log_path(args.log, ROOT)

    if args.verify:
        ok, msg = verify_utf8_file(path)
        safe_print(f"{path.name}: {msg}")
        return 0 if ok else 1

    try:
        print_log(path, lines=args.lines, follow=args.follow)
    except KeyboardInterrupt:
        safe_print("\n[已停止跟踪]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
