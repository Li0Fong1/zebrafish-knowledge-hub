# -*- coding: utf-8 -*-
"""对话历史留存清理（按 hub_user_conversations.updated_at 删除超期行）。

默认 dry-run，只统计不删；真正删除必须显式加 ``--apply``。

用法：
    python scripts/purge_conversations.py                 # 看会删多少（默认 180 天）
    python scripts/purge_conversations.py --days 90       # 换阈值
    python scripts/purge_conversations.py --apply         # 真删

建议由 supervisor/cron 每月执行一次 dry-run，确认后再 --apply。
阈值也可用环境变量 HUB_HISTORY_RETENTION_DAYS（与落库裁剪保持一致）。
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))

from sqlalchemy import text  # noqa: E402

from mysql_client import MySQLClient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="留存天数，默认取 HUB_HISTORY_RETENTION_DAYS/180")
    ap.add_argument("--apply", action="store_true", help="真正执行删除（默认只统计）")
    args = ap.parse_args()

    days = args.days or int((os.getenv("HUB_HISTORY_RETENTION_DAYS") or "180").strip() or 180)
    if days <= 0:
        print("[SKIP] 留存天数 <= 0（表示不清理）")
        return 0

    client = MySQLClient()
    db = next(client.get_db())
    try:
        table = db.execute(text("SHOW TABLES LIKE 'hub_user_conversations'")).first()
        if not table:
            print("[SKIP] 表 hub_user_conversations 不存在（未启用账号体系）")
            return 0

        stat = db.execute(
            text(
                "SELECT COUNT(*) AS n, MIN(updated_at) AS oldest, MAX(updated_at) AS newest"
                " FROM hub_user_conversations"
            )
        ).mappings().first()
        print(f"[现状] 共 {stat['n']} 行；最早 {stat['oldest']}；最新 {stat['newest']}")

        doomed = db.execute(
            text(
                "SELECT COUNT(*) AS n FROM hub_user_conversations"
                f" WHERE updated_at < DATE_SUB(NOW(), INTERVAL {int(days)} DAY)"
            )
        ).mappings().first()
        n = int(doomed["n"] or 0)
        print(f"[计划] 超过 {days} 天未更新的记录：{n} 行")

        if not args.apply:
            print("[DRY-RUN] 未删除。确认无误后加 --apply 执行。")
            return 0
        if n == 0:
            return 0

        res = db.execute(
            text(
                "DELETE FROM hub_user_conversations"
                f" WHERE updated_at < DATE_SUB(NOW(), INTERVAL {int(days)} DAY)"
            )
        )
        db.commit()
        print(f"[OK] 已删除 {res.rowcount} 行")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
