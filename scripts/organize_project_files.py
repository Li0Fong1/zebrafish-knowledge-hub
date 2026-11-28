# -*- coding: utf-8 -*-
"""执行本机文件夹整理（只移动，不删除）。

安全约束（严格遵守）：
  1. 只移动，不删除、不覆盖；目标已存在则跳过并报告。
  2. 移动前把每个源逐份复制到 _整理备份/<时间戳>/ 作为备份，校验大小一致后才移动源。
  3. 每批最多 10 项，逐批校验；任何一项失败立即停止后续批次。
  4. 生成 _整理备份/<时间戳>/回滚清单.json，记录每个移动，支持 --rollback 还原。
  5. 默认 dry-run（只打印计划，不动文件）；--apply 才真正执行。
  6. 只处理 docs/文件夹整理建议.md 里列出的白名单，不扫描、不猜测、不扩大范围。

用法：
  python scripts/organize_project_files.py            # 预览
  python scripts/organize_project_files.py --apply    # 执行
  python scripts/organize_project_files.py --rollback # 用最新备份回滚
"""
from __future__ import annotations

import argparse
import datetime
import filecmp
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = ROOT / "_整理备份"
BATCH = 10

# 白名单：(源相对路径, 目标目录相对路径) —— 与 docs/文件夹整理建议.md 完全一致
MOVES = [
    ("12.png", "docs/图件"),
    ("6.png", "docs/图件"),
    ("ChatGPT.png", "docs/图件"),
    ("DeepSeek.png", "docs/图件"),
    ("Zebrafish Hub.png", "docs/图件"),
    ("实例图.png", "docs/图件"),
    ("屏幕截图 2026-10-22 175207.png", "docs/图件"),
    ("数据清洗图.png", "docs/图件"),
    ("斑马鱼背景图.jpeg", "docs/图件"),
    ("_ms_full.txt", "docs/报告"),
    ("_ms_text.txt", "docs/报告"),
    ("nud.txt", "docs/报告"),
    ("pushout.txt", "docs/报告"),
    ("马铃薯AI智能体.pdf", "docs/报告"),
    # 注意：_docx_media/_ms_media/_opt/_v3/_verify 被 4 个脚本以相对路径引用
    # （fix_figS4_captions.py、patch_figS4_panelA.py、revise_manuscript_v2.py、
    # _remote_deploy_audit_fix.py），移动会破坏引用，故从整理清单移除，保持原位。
    # bootstrap.py 是根目录 shim（re-export core.bootstrap），被 import 使用，保持原位。
    # hub_recent_papers.json 被 api_server.py / agent 引用，移动会破坏线上，保持原位。
    ("zebrafish-hub-再问一次-tp53-表达-2026-09-30.json", "data"),
]


def dir_size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def cmp_ok(src: Path, dst: Path) -> bool:
    """源与备份是否一致（文件比内容，目录比文件数与总大小）。"""
    if src.is_file():
        return dst.is_file() and dst.stat().st_size == src.stat().st_size and filecmp.cmp(src, dst, shallow=False)
    if not dst.is_dir():
        return False
    s = [f for f in src.rglob("*") if f.is_file()]
    d = [f for f in dst.rglob("*") if f.is_file()]
    return len(s) == len(d) and sum(f.stat().st_size for f in s) == sum(f.stat().st_size for f in d)


def preview() -> None:
    print("=== 预览（未移动任何文件）===")
    for i, (src_rel, dst_dir) in enumerate(MOVES, 1):
        src = ROOT / src_rel
        dst = ROOT / dst_dir / src_rel.split("/")[-1]
        flag = "OK" if src.exists() else "缺失"
        conflict = " [目标已存在，将跳过]" if dst.exists() else ""
        size = dir_size(src) if src.exists() else 0
        print("  %2d. %-46s -> %-16s %s %s %s"
              % (i, src_rel, dst_dir + "/", flag, _human(size), conflict))
    print("\n共 %d 项；每批 %d 项。加 --apply 执行。" % (len(MOVES), BATCH))


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%.1f%s" % (n, unit)
        n /= 1024.0
    return "%.1fTB" % n


def do_apply() -> int:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_ROOT / ts
    backup.mkdir(parents=True, exist_ok=False)
    manifest = []
    failures = []
    moved = 0

    for batch_no, start in enumerate(range(0, len(MOVES), BATCH), 1):
        batch = MOVES[start:start + BATCH]
        print("\n=== 第 %d 批（%d 项）===" % (batch_no, len(batch)), flush=True)
        batch_moved = 0
        for src_rel, dst_dir in batch:
            src = ROOT / src_rel
            name = src_rel.split("/")[-1]
            dst_root = ROOT / dst_dir
            dst = dst_root / name
            if not src.exists():
                print("  跳过（源不存在）：%s" % src_rel, flush=True)
                continue
            if dst.exists():
                print("  跳过（目标已存在，防覆盖）：%s" % dst, flush=True)
                continue
            try:
                dst_root.mkdir(parents=True, exist_ok=True)
                # 1) 先备份
                bk = backup / dst_dir / name
                bk.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, bk)
                else:
                    shutil.copy2(src, bk)
                # 2) 校验备份
                if not cmp_ok(src, bk):
                    failures.append("%s -> 备份校验失败" % src_rel)
                    print("  ❌ 备份校验失败，停止：%s" % src_rel, flush=True)
                    break
                # 3) 移动源
                shutil.move(str(src), str(dst))
                if not dst.exists():
                    failures.append("%s -> 移动后目标缺失" % src_rel)
                    print("  ❌ 移动后目标缺失，停止：%s" % src_rel, flush=True)
                    break
                manifest.append({"src": src_rel, "dst": str(dst.relative_to(ROOT).as_posix()),
                                 "backup": str(bk.relative_to(ROOT).as_posix())})
                moved += 1
                batch_moved += 1
                print("  ✓ %s -> %s" % (src_rel, dst.relative_to(ROOT).as_posix()), flush=True)
            except Exception as exc:  # noqa: BLE001
                failures.append("%s -> %s" % (src_rel, type(exc).__name__))
                print("  ❌ %s 失败：%s" % (src_rel, exc), flush=True)
                break
        if failures:
            print("\n⚠ 本批有失败，停止后续批次（已完成 %d 项）。" % moved, flush=True)
            break
        print("  本批完成 %d 项" % batch_moved, flush=True)

    mf = backup / "回滚清单.json"
    mf.write_text(json.dumps({"time": ts, "moves": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")

    rp = ROOT / "docs" / "文件夹整理执行报告.md"
    lines = [
        "# 文件夹整理执行报告", "",
        "- 时间：%s" % ts,
        "- 成功移动：%d / %d 项" % (moved, len(MOVES)),
        "- 备份目录：`%s`" % backup.relative_to(ROOT).as_posix(),
        "- 回滚清单：`%s`" % mf.relative_to(ROOT).as_posix(),
        "", "## 明细", "",
    ]
    lines += ["  ✓ %s -> %s" % (m["src"], m["dst"]) for m in manifest] or ["  （无）"]
    lines += ["", "## 失败", ""]
    lines += ["  " + f for f in failures] or ["  （无）"]
    lines += ["", "回滚：`python scripts/organize_project_files.py --rollback`", ""]
    rp.write_text("\n".join(lines), encoding="utf-8")

    print("\n=== 完成：移动 %d 项，失败 %d 项 ===" % (moved, len(failures)), flush=True)
    print("备份：%s" % backup, flush=True)
    print("报告：%s" % rp, flush=True)
    return 1 if failures else 0


def rollback() -> int:
    mfs = sorted(BACKUP_ROOT.glob("*/回滚清单.json"))
    if not mfs:
        print("没有找到回滚清单。")
        return 1
    mf = mfs[-1]
    data = json.loads(mf.read_text(encoding="utf-8"))
    moves = data.get("moves", [])
    print("=== 回滚：%s（%d 项）===" % (data.get("time"), len(moves)))
    ok = fail = 0
    for m in reversed(moves):
        src = ROOT / m["src"]
        dst = ROOT / m["dst"]
        if not dst.exists():
            print("  跳过（目标不存在）：%s" % m["dst"])
            fail += 1
            continue
        if src.exists():
            print("  跳过（原位置已占用）：%s" % m["src"])
            fail += 1
            continue
        try:
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
            ok += 1
            print("  ↩ %s -> %s" % (m["dst"], m["src"]))
        except Exception as exc:  # noqa: BLE001
            fail += 1
            print("  ❌ 回滚失败：%s %s" % (m["dst"], exc))
    print("回滚完成：成功 %d，失败 %d" % (ok, fail))
    return 0 if fail == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    args = ap.parse_args()
    if args.rollback:
        return rollback()
    if args.apply:
        return do_apply()
    preview()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
