# -*- coding: utf-8 -*-
"""初始化多租户表 + 同步预置工作流。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from tenant_service import ensure_default_tenant, ensure_tenant_schema
from workflow_store import seed_presets


def main() -> int:
    ok = ensure_tenant_schema()
    if not ok:
        safe_print("[错误] 租户/工作流表迁移失败")
        return 1
    tid = ensure_default_tenant()
    n = seed_presets()
    safe_print(f"[成功] 默认租户 id={tid}，预置工作流 {n} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
