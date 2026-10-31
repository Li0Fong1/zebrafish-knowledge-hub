# -*- coding: utf-8 -*-
"""生成 DOI 冲突清单：一个 DOI 挂在多篇不同论文上 → 这些 DOI 不可信。

用法（服务器，项目根目录）：
    python -X utf8 scripts/build_doi_conflict_list.py [--min-rows 2] [--out path]

产物：`doi_conflicts.json`（默认写运行时目录与项目根，两边都写，谁读都能读到）
结构：{"generated_at": "...", "rule": "...", "total_rows_scanned": N, "conflict_count": M, "dois": [...]}

- 只读数据库，只写 JSON（派生物，可随时重建）。
- 判定：同一 DOI 出现在 ≥ min-rows 行，且这些行的题名（title_en，取前 80 字规范化）
  不只一种 → 视为「一 DOI 多论文」。
  （同一篇论文被重复收录时题名相同，不算冲突，避免误伤真正的重复行。）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "database"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _load_env_defaults(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k:
            os.environ.setdefault(k, v)


_load_env_defaults(ROOT / ".env")

from sqlalchemy import text  # noqa: E402


def _norm_title(t: str) -> str:
    s = re.sub(r"\s+", " ", (t or "").strip().lower())
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff ]+", "", s)
    return s[:80]


def _out_dirs() -> list[Path]:
    out: list[Path] = []
    for key in ("ZEBRA_RUNTIME", "ZEBRAFISH_RUNTIME"):
        v = (os.getenv(key) or "").strip()
        if v:
            out.append(Path(v))
    out.append(ROOT)
    out.append(ROOT.parent / "zebrafish_runtime")
    uniq, seen = [], set()
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


def build(min_rows: int = 2) -> dict:
    from mysql_client import get_mysql_client

    db = next(get_mysql_client().get_db())
    try:
        cols = {
            r[0] for r in db.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=DATABASE() AND table_name='literature_paper'"
            )).fetchall()
        }
        title_col = "title_en" if "title_en" in cols else "title"
        rows = db.execute(text(
            f"SELECT doi, {title_col} AS t FROM literature_paper "
            f"WHERE doi IS NOT NULL AND TRIM(doi)<>''"
        )).fetchall()
    finally:
        db.close()

    by_doi: dict[str, set[str]] = {}
    for doi, title in rows:
        d = re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", str(doi).strip()).strip(" .;").lower()
        if not d:
            continue
        by_doi.setdefault(d, set()).add(_norm_title(str(title or "")))

    conflicts = sorted(
        d for d, titles in by_doi.items()
        if len(titles) >= min_rows and len(titles) > 1
    )
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "rule": (
            f"同一 DOI 出现在 ≥{min_rows} 行且题名不止一种 → 视为不可信"
            "（DOI 多半取自全文参考文献表，不是论文自身 DOI）"
        ),
        "total_rows_scanned": len(rows),
        "unique_dois": len(by_doi),
        "conflict_count": len(conflicts),
        "dois": conflicts,
    }
    blob = json.dumps(payload, ensure_ascii=False, indent=1)
    written = []
    for d in _out_dirs():
        try:
            d.mkdir(parents=True, exist_ok=True)
            t = d / "doi_conflicts.json"
            t.write_text(blob, encoding="utf-8")
            written.append(t)
        except OSError:
            continue
    return {"payload": payload, "written": [str(p) for p in written]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-rows", type=int, default=2)
    ap.add_argument("--out", default="", help="额外指定输出文件")
    a = ap.parse_args(argv)
    res = build(min_rows=max(2, a.min_rows))
    p = res["payload"]
    print(f"[doi-guard] 扫描 {p['total_rows_scanned']} 行 / 去重后 {p['unique_dois']} 个 DOI "
          f"→ 冲突 {p['conflict_count']} 个")
    for w in res["written"]:
        print(f"[doi-guard] 写入 {w}")
    if a.out:
        Path(a.out).write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[doi-guard] 写入 {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
