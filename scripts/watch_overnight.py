# -*- coding: utf-8 -*-
"""一次性查看过夜任务 / 监听器状态（UTF-8 终端输出，不乱码）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from core.log_io import print_log, read_lines_utf8, resolve_log_path
from common_utils import safe_print

LOG = ROOT / "logs"


def _tail(path: Path, n: int = 6) -> str:
    if not path.is_file():
        return "  (无日志)"
    lines = read_lines_utf8(path, tail=n)
    return "\n".join(f"  {ln}" for ln in lines)


def main() -> int:
    safe_print("=" * 60)
    safe_print("  过夜任务状态快照")
    safe_print("=" * 60)

    state_path = LOG / "overnight_supervisor.json"
    if state_path.is_file():
        data = json.loads(state_path.read_text(encoding="utf-8"))
        safe_print(f"\n监听器更新: {data.get('updated')}  轮次: R{data.get('round')}")
        for jid, st in data.get("jobs", {}).items():
            safe_print(
                f"  [{jid}] {st.get('status')}  attempts={st.get('attempts')}  "
                f"pid={st.get('pid')}  err={st.get('last_error') or '-'}"
            )
    else:
        safe_print("\n(监听器未运行或未生成状态文件)")

    sup = LOG / "overnight_supervisor.log"
    if sup.is_file():
        safe_print(f"\n--- 监听器 (tail) ---\n{_tail(sup, 8)}")

    for name, title in (
        ("18_wos", "WoS 导入"),
        ("19_go", "GO 补全"),
        ("15_link", "文献关联"),
    ):
        safe_print(f"\n--- {title} (tail) ---\n{_tail(LOG / f'overnight_{name}.log', 5)}")

    safe_print("\n查看日志（不乱码）:")
    safe_print("  python scripts/tail_log.py 18")
    safe_print("  python scripts/tail_log.py 15 -f")
    safe_print("  scripts\\tail_log.bat 19 -n 30")
    safe_print("\n勿用: Get-Content xxx.log -Tail 20  （默认 GBK 会乱码）")
    safe_print("若用 PowerShell: Get-Content xxx.log -Encoding utf8 -Tail 20")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--follow", "-f"):
        try:
            print_log(resolve_log_path("supervisor", ROOT), lines=20, follow=True)
        except KeyboardInterrupt:
            safe_print("\n[已停止跟踪]")
    else:
        raise SystemExit(main())
