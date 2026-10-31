# -*- coding: utf-8 -*-
"""
pdf库 全量去重 + 合并进标准目录 pdf/ xml/ html/

策略：
1. 扫描 pdf库 下全部文件（含新增 kday* 文件夹）
2. 按 size+sha256 去重；同内容保留一份
3. 优先保留标准目录路径；新唯一文件按扩展名并入 pdf/xml/html
4. 重复副本移入 pdf库/_duplicates_quarantine/（不直接删）
5. 写出去重报告
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PDFKU = ROOT / "pdf库"
STD = {
    ".pdf": PDFKU / "pdf",
    ".xml": PDFKU / "xml",
    ".txt": PDFKU / "html",  # html 纯文本
    ".html": PDFKU / "html",
    ".htm": PDFKU / "html",
}
QUAR = PDFKU / "_duplicates_quarantine"
REPORT_DIR = ROOT / "解析文件"
SKIP_DIR_NAMES = {"_duplicates_quarantine"}


def _sha256(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _is_standard(path: Path) -> bool:
    for d in STD.values():
        if _is_under(path, d):
            return True
    return False


def _iter_files() -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(PDFKU):
        # 跳过隔离区
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        base = Path(dirpath)
        for name in filenames:
            p = base / name
            if p.is_file():
                out.append(p)
    return out


def _dest_for(path: Path) -> Path | None:
    ext = path.suffix.lower()
    folder = STD.get(ext)
    if not folder:
        return None
    return folder / path.name


def run(*, dry_run: bool = False, move_dupes: bool = True) -> dict:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    files = _iter_files()
    print(f"[scan] files={len(files)}", flush=True)

    # size 预分组
    by_size: dict[int, list[Path]] = defaultdict(list)
    for p in files:
        try:
            by_size[p.stat().st_size].append(p)
        except OSError:
            continue

    # hash 组
    by_hash: dict[str, list[Path]] = defaultdict(list)
    hashed = 0
    for size, group in by_size.items():
        if len(group) == 1:
            # 仍算 hash，便于报告
            p = group[0]
            try:
                by_hash[_sha256(p)].append(p)
                hashed += 1
            except OSError:
                continue
            continue
        for p in group:
            try:
                by_hash[_sha256(p)].append(p)
                hashed += 1
            except OSError:
                continue
        if hashed % 2000 == 0:
            print(f"  hashed={hashed}", flush=True)

    print(f"[hash] unique_contents={len(by_hash)} hashed_files={hashed}", flush=True)

    kept: list[str] = []
    merged: list[dict] = []
    quarantined: list[dict] = []
    skipped_ext: list[str] = []

    for digest, paths in by_hash.items():
        # 排序：标准目录优先，其次路径短、名字稳
        paths_sorted = sorted(
            paths,
            key=lambda p: (0 if _is_standard(p) else 1, len(str(p)), str(p).lower()),
        )
        primary = paths_sorted[0]

        # 若唯一内容不在标准目录，复制/移动进标准目录
        dest = _dest_for(primary)
        if dest is None:
            skipped_ext.append(str(primary))
            kept.append(str(primary))
            # 非标准扩展名的重复也隔离
            for extra in paths_sorted[1:]:
                if move_dupes and not dry_run:
                    qpath = QUAR / digest[:16] / extra.name
                    qpath.parent.mkdir(parents=True, exist_ok=True)
                    if extra.resolve() != qpath.resolve() and extra.exists():
                        shutil.move(str(extra), str(qpath))
                        quarantined.append({"from": str(extra), "to": str(qpath), "sha256": digest})
            continue

        if not _is_standard(primary):
            # 合并进标准目录
            action = "would_merge"
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    # 目标已有同名：比 hash
                    try:
                        if _sha256(dest) == digest:
                            action = "merge_skip_same"
                        else:
                            # 不同内容同名：加后缀
                            dest = dest.with_name(f"{dest.stem}__{digest[:8]}{dest.suffix}")
                            shutil.copy2(str(primary), str(dest))
                            action = "merge_renamed"
                    except OSError:
                        shutil.copy2(str(primary), str(dest))
                        action = "merge_copy"
                else:
                    shutil.copy2(str(primary), str(dest))
                    action = "merge_copy"
            merged.append({
                "sha256": digest,
                "from": str(primary),
                "to": str(dest),
                "action": action,
            })
            primary = dest if not dry_run else primary

        kept.append(str(primary))

        # 其余副本隔离
        for extra in paths_sorted:
            if extra.resolve() == Path(primary).resolve():
                continue
            # 标准目录内：不同文件名即使同内容也保留（entry_no 索引需要）
            if _is_standard(extra) and extra.name != Path(primary).name:
                kept.append(str(extra))
                continue
            if _is_standard(extra) and extra.name == Path(primary).name:
                # 同名同内容多余副本才隔离
                if extra.resolve() == Path(primary).resolve():
                    continue
            if move_dupes:
                rel = extra.relative_to(PDFKU) if _is_under(extra, PDFKU) else Path(extra.name)
                qpath = QUAR / digest[:16] / rel
                if dry_run:
                    quarantined.append({"from": str(extra), "to": str(qpath), "sha256": digest, "dry": True})
                else:
                    qpath.parent.mkdir(parents=True, exist_ok=True)
                    if extra.exists():
                        # 避免覆盖
                        if qpath.exists():
                            qpath = qpath.with_name(f"{qpath.stem}__dup{qpath.suffix}")
                        shutil.move(str(extra), str(qpath))
                        quarantined.append({"from": str(extra), "to": str(qpath), "sha256": digest})

    # 清空空的 kday 目录（可选保留外壳）
    emptied: list[str] = []
    if not dry_run:
        for name in os.listdir(PDFKU):
            p = PDFKU / name
            if not p.is_dir() or name in {"pdf", "xml", "html"} or name in SKIP_DIR_NAMES:
                continue
            # 若目录下已无文件，记一下
            has_file = any(f.is_file() for f in p.rglob("*"))
            if not has_file:
                emptied.append(name)

    summary = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "scanned_files": len(files),
        "unique_contents": len(by_hash),
        "kept": len(kept),
        "merged_into_std": len(merged),
        "quarantined": len(quarantined),
        "skipped_unknown_ext": len(skipped_ext),
        "empty_extra_dirs": emptied,
        "std_counts": {
            "pdf": len(list((PDFKU / "pdf").glob("*.pdf"))) if (PDFKU / "pdf").is_dir() else 0,
            "xml": len(list((PDFKU / "xml").glob("*.xml"))) if (PDFKU / "xml").is_dir() else 0,
            "html": len(list((PDFKU / "html").glob("*.txt"))) if (PDFKU / "html").is_dir() else 0,
        },
    }
    report = {
        "summary": summary,
        "merged": merged[:5000],
        "quarantined_sample": quarantined[:2000],
        "skipped_ext_sample": skipped_ext[:200],
    }
    out = REPORT_DIR / "pdf库_全量去重报告.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = REPORT_DIR / "pdf库_全量去重报告.md"
    md.write_text(
        "\n".join(
            [
                "# pdf库 全量去重报告",
                "",
                f"- 时间：{summary['at']}",
                f"- 扫描文件：{summary['scanned_files']}",
                f"- 唯一内容：{summary['unique_contents']}",
                f"- 合并进标准目录：{summary['merged_into_std']}",
                f"- 隔离重复：{summary['quarantined']}",
                f"- 标准目录现况：pdf={summary['std_counts']['pdf']} "
                f"xml={summary['std_counts']['xml']} html={summary['std_counts']['html']}",
                f"- 已空额外目录：{', '.join(emptied) if emptied else '(无)'}",
                "",
                "重复文件已移至 `pdf库/_duplicates_quarantine/`，确认无误后可手动删除。",
                f"详细 JSON：`{out.name}`",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"[report] {out}", flush=True)
    return summary


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-move", action="store_true", help="不去隔离，只报告")
    args = ap.parse_args()
    run(dry_run=args.dry_run, move_dupes=not args.no_move)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
