# -*- coding: utf-8 -*-
"""构建 / 刷新 ZFIN 表型索引（本地 SQLite，从 ZFIN 官方 dump 解析）。

⚠️ 重要认知：ZFIN「索引」**不是 MySQL 索引**，而是把 ZFIN 官方表型注释
``phenoGeneCleanData_fish.txt`` 解析成的「本地 SQLite 文件」
（默认落在 ``$RT/zfin/zfin_phenotype.sqlite``，``$RT = dirname(BLAST_DB_DIR)``）。
它与 MySQL 是否启动**无关**；报错「ZFIN 表型索引尚未构建」只是说这个 sqlite 还不存在。

本机开发环境没有 ZFIN 源文件，无法在此构建；必须在「源文件所在机器」上运行
（生产服务器，或把 ``phenoGeneCleanData_fish.txt`` 放到 ``$RT/zfin/`` 的机器）。

用法：
    python scripts/build_zfin_phenotype_index.py            # 增量（已建且比源文件新则跳过）
    python scripts/build_zfin_phenotype_index.py --force    # 强制重建
    python scripts/build_zfin_phenotype_index.py --status   # 仅查看状态，不构建

环境变量：
    BLAST_DB_DIR   覆盖 blast 目录；其上级目录下的 zfin/ 即为源文件与索引目录。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "database")]


def _main() -> int:
    ap = argparse.ArgumentParser(description="构建 / 刷新 ZFIN 表型索引")
    ap.add_argument("--force", action="store_true", help="强制重建（忽略时间戳跳过逻辑）")
    ap.add_argument("--status", action="store_true", help="仅查看状态，不构建")
    args = ap.parse_args()

    import phenotype_toolkit as pt  # noqa: E402

    src = pt._source_files()
    zfin_dir = pt._zfin_dir()
    print("[ZFIN 索引] 预期源文件目录: %s" % zfin_dir)
    if not src:
        print("[ZFIN 索引] 未找到源文件（phenoGeneCleanData_fish.txt / pheno_fish.txt）。")
        print("              请把 ZFIN 官方 dump 放到上述目录后再构建；这通常是生产服务器侧的动作。")
        st = pt.status()
        print("[状态] " + json.dumps(st, ensure_ascii=False, indent=2))
        return 2 if not args.status else 0

    if args.status:
        print("[状态] " + json.dumps(pt.status(), ensure_ascii=False, indent=2))
        return 0

    print("[ZFIN 索引] 开始构建（force=%s）…" % args.force)
    res = pt.build_index(force=args.force)
    print("[构建结果] " + json.dumps(res, ensure_ascii=False, indent=2))
    if not res.get("ok"):
        return 1
    print("[状态] " + json.dumps(pt.status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
