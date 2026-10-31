# -*- coding: utf-8 -*-
"""数据库问题收尾：修掉体检发现的两处真实数据问题（带备份、默认可干跑）。

体检发现（scripts/check_database_health.py）
------------------------------------------
1. **gene_kegg 重复行**：同一 (gene_id, pathway_id) 有多行（实测 165 组）。
   影响：计数类统计虚高；做通路富集时靠 `set()` 去重才没出错。
2. **一个 DOI 挂多篇不同论文**（实测 2,651 个 DOI 重复 → 严格判定 847 个不可信）。
   根因：DOI 多半是从**全文参考文献表**里抓来的（方法学论文被反复引用），
   不是论文自身的 DOI。影响：脚注「原文」会指向另一篇论文。

做法
----
- 默认 **干跑**（只统计与打印，不写库）
- `--apply` 才会写库：先 `mysqldump` 备份 gene_kegg / literature_paper，
  同时把逐行改动写成 CSV（便于回滚对照），再改
- DOI 修复用 **Europe PMC 按 PMID（其次题名）回查**：
  · 查到且题名匹配，且 DOI 不同 → 改成正确 DOI
  · 查到但题名不匹配 → 清空 DOI（脚注自动退回 PMID / 检索链接，不留错误链接）
  · 查不到 → 不动（保守）
- 修完自动复核（重复行数 / 重复 DOI 数）并打印前后对比

用法（服务器，项目根目录）
------------------------
    PYTHONPATH=$BASE:$BASE/core:$BASE/database python -X utf8 scripts/cleanup_database_issues.py
    ... --apply                 # 真改（会先备份）
    ... --only dup-kegg         # 只修重复通路
    ... --only doi              # 只修 DOI
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
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

EPMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_UA = "ZebrafishHub/1.0 (db cleanup)"


def _db():
    from mysql_client import get_mysql_client

    return next(get_mysql_client().get_db())


def _norm_doi(d: str) -> str:
    return re.sub(r"(?i)^https?://(?:dx\.)?doi\.org/", "", (d or "").strip()).strip(" .;").lower()


def _tokens(s: str) -> set[str]:
    s = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", (s or "").lower())
    return {t for t in s.split() if len(t) > 2}


def _title_match(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / max(1, min(len(ta), len(tb))) >= 0.6


# --------------------------------------------------------------------- 备份
def backup(rt: Path, tables: list[str]) -> Path:
    """用 --defaults-extra-file 备份（密码不进命令行）。

    mysqldump 优先用运行时自带的 client（`$RT/mysql80/bin/mysqldump`），
    没有再退回 PATH —— 之前踩过 "PATH 里没有 mysqldump" 的坑。
    """
    from config import DB_CONFIG

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = rt / f"db_backup_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cnf = out_dir / ".my.cnf"
    cnf.write_text(
        "[client]\n"
        f"user={DB_CONFIG['user']}\n"
        f"password={DB_CONFIG['password']}\n"
        f"host={DB_CONFIG['host']}\n"
        f"port={DB_CONFIG['port']}\n",
        encoding="utf-8",
    )
    os.chmod(cnf, 0o600)
    dump_bin = ""
    for cand in (rt / "mysql80" / "bin" / "mysqldump",
                 rt.parent / "mysql80" / "bin" / "mysqldump",
                 Path("/data/zoujin/deng/zebrafish_runtime/mysql80/bin/mysqldump")):
        if cand.is_file():
            dump_bin = str(cand)
            break
    if not dump_bin:
        import shutil as _sh

        dump_bin = _sh.which("mysqldump") or ""
    dumped = []
    for t in tables:
        f = out_dir / f"{t}.sql"
        if not dump_bin:
            dumped.append(f"{t}:NO_MYSQLDUMP")
            continue
        cmd = (f"{dump_bin} --defaults-extra-file={cnf} --single-transaction "
               f"--skip-lock-tables {DB_CONFIG['database']} {t} > {f}")
        try:
            r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=1800)
            ok = r.returncode == 0 and f.exists() and f.stat().st_size > 0
            dumped.append(f"{t}:{'ok' if ok else 'FAIL ' + (r.stderr or '')[:80]}")
        except Exception as e:  # noqa: BLE001
            dumped.append(f"{t}:ERR {type(e).__name__}")
    print(f"[backup] {out_dir} → {' | '.join(dumped)}", flush=True)
    return out_dir


# ----------------------------------------------------------- 修复 1：重复通路
def fix_dup_kegg(db, apply: bool, csv_writer) -> dict:
    rows = db.execute(text(
        "SELECT id, gene_id, pathway_id, pathway_name, source FROM gene_kegg "
        "ORDER BY gene_id, pathway_id, id"
    )).fetchall()
    groups: dict[tuple, list] = defaultdict(list)
    for r in rows:
        groups[(r[1], r[2])].append((r[0], (r[3] or ""), (r[4] or "")))

    def rank(rec):
        _id, name, src = rec
        return (1 if name.strip() else 0, 1 if src == "kegg_rest" else 0, -_id)

    to_delete: list[tuple] = []
    for key, recs in groups.items():
        if len(recs) < 2:
            continue
        keep = max(recs, key=rank)
        for rec in recs:
            if rec[0] != keep[0]:
                to_delete.append((rec[0], key[0], key[1], rec[1][:60], keep[1][:60]))
    print(f"[dup-kegg] 重复组 {sum(1 for v in groups.values() if len(v) > 1)} 个 → 待删 {len(to_delete)} 行", flush=True)
    for r in to_delete[:5]:
        print(f"    id={r[0]} gene={r[1]} pathway={r[2]} 删除'{r[3]}' 保留'{r[4]}'", flush=True)
    if apply and to_delete:
        for r in to_delete:
            db.execute(text("DELETE FROM gene_kegg WHERE id=:i"), {"i": r[0]})
            if csv_writer:
                csv_writer.writerow(["dup-kegg", "delete", r[0], r[1], r[2], r[3], r[4]])
        db.commit()
    return {"groups": sum(1 for v in groups.values() if len(v) > 1), "deleted": len(to_delete) if apply else 0,
            "planned": len(to_delete)}


# -------------------------------------------------------------- 修复 2：DOI
def _epmc_batch(ids: list[str], timeout: int = 25) -> dict:
    """按 PMID 批量回查（EXT_ID:）→ {pmid: {doi, title}}。"""
    out: dict[str, dict] = {}
    if not ids:
        return out
    q = " OR ".join(f"EXT_ID:{i}" for i in ids)
    try:
        import urllib.parse
        import urllib.request

        url = f"{EPMC_SEARCH}?query={urllib.parse.quote(q)}&format=json&resultType=core&pageSize=50"
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        for h in ((data.get("resultList") or {}).get("result") or []):
            pm = str(h.get("pmid") or "").strip()
            if pm:
                out[pm] = {"doi": str(h.get("doi") or "").strip(),
                           "title": str(h.get("title") or "").strip()}
    except Exception:  # noqa: BLE001
        return out
    return out


def _epmc_by_doi_titles(doi: str, timeout: int = 20):
    """按 DOI 查 Europe PMC，返回该 DOI 名下所有题名。

    返回值三态，务必区分（否则会把网络故障当成"DOI 不存在"而误清）：
      · list[str] —— 查询成功（空列表 = EPMC 里确实没有这个 DOI）
      · None      —— 查询失败（超时/网络），调用方应跳过、不改
    """
    if not doi:
        return None
    try:
        import urllib.parse
        import urllib.request

        q = 'DOI:"%s"' % doi          # 注意：Python 3.10 的 f-string 表达式里不能出现反斜杠
        url = (f"{EPMC_SEARCH}?query={urllib.parse.quote(q)}"
               f"&format=json&resultType=core&pageSize=50")
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        hits = ((data.get("resultList") or {}).get("result") or [])
        return [str(h.get("title") or "") for h in hits]
    except Exception:  # noqa: BLE001
        return None


def fix_doi(db, apply: bool, csv_writer, batch: int = 10, limit: int = 0, workers: int = 8) -> dict:
    cols = {r[0] for r in db.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=DATABASE() AND table_name='literature_paper'"
    )).fetchall()}
    title_col = "title_en" if "title_en" in cols else "title"
    dup = db.execute(text(
        "SELECT LOWER(TRIM(doi)) d, COUNT(*) n FROM literature_paper "
        "WHERE doi IS NOT NULL AND TRIM(doi)<>'' GROUP BY d HAVING COUNT(*)>1"
    )).fetchall()
    bad = [r[0] for r in dup]
    print(f"[doi] 一段 DOI 多论文：{len(bad)} 个 DOI", flush=True)
    if not bad:
        return {"conflict_dois": 0, "rows": 0, "fixed": 0, "cleared": 0, "unchanged": 0}

    rows: list[tuple] = []
    for i in range(0, len(bad), 200):
        chunk = bad[i:i + 200]
        inlist = ",".join("'" + d.replace("'", "") + "'" for d in chunk)
        rows += db.execute(text(
            f"SELECT doc_id, pmid, {title_col}, doi FROM literature_paper "
            f"WHERE LOWER(TRIM(doi)) IN ({inlist})"
        )).fetchall()
    if limit:
        rows = rows[:limit]
        print(f"[doi] --limit {limit}：本次只处理前 {len(rows)} 行")

    with_pmid = [r for r in rows if str(r[1] or "").strip()]
    no_pmid = [r for r in rows if not str(r[1] or "").strip()]
    print(f"[doi] 涉及 {len(rows)} 行（有 PMID {len(with_pmid)} · 无 PMID {len(no_pmid)}）", flush=True)

    fixed = cleared = unchanged = 0

    # —— 第一遍：有 PMID 的按 PMID 回查（最可靠）——
    by_pmid: dict[str, list] = defaultdict(list)
    for doc_id, pmid, title, doi in with_pmid:
        by_pmid[str(pmid).strip()].append((doc_id, title, doi))
    lookups: dict[str, dict] = {}
    pmids = list(by_pmid.keys())
    for i in range(0, len(pmids), batch):
        lookups.update(_epmc_batch(pmids[i:i + batch]))
    print(f"[doi] Europe PMC 按 PMID 回查 {len(pmids)} 个 → 命中 {len(lookups)}", flush=True)
    for pmid, items in by_pmid.items():
        hit = lookups.get(pmid)
        for doc_id, title, old_doi in items:
            if not hit:
                unchanged += 1
                continue
            new_doi = _norm_doi(hit.get("doi"))
            if not _title_match(str(title or ""), hit.get("title") or ""):
                if apply:
                    db.execute(text("UPDATE literature_paper SET doi=NULL WHERE doc_id=:d"), {"d": doc_id})
                if csv_writer:
                    csv_writer.writerow(["doi", "clear", doc_id, pmid, old_doi, "", "title_mismatch"])
                cleared += 1
            elif new_doi and new_doi != _norm_doi(old_doi):
                if apply:
                    db.execute(text("UPDATE literature_paper SET doi=:n WHERE doc_id=:d"),
                               {"n": hit.get("doi").strip(), "d": doc_id})
                if csv_writer:
                    csv_writer.writerow(["doi", "fix", doc_id, pmid, old_doi, hit.get("doi"), "doi_corrected"])
                fixed += 1
            else:
                unchanged += 1
    if apply:
        db.commit()

    # —— 第二遍：无 PMID 的按 DOI 回查 EPMC，题名对得上才算这个 DOI 属于它 ——
    if no_pmid:
        uniq = sorted({_norm_doi(d) for _i, _p, _t, d in no_pmid if _norm_doi(d)})
        print(f"[doi] 无 PMID 行按 DOI 回查 {len(uniq)} 个唯一 DOI…", flush=True)
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            got = list(ex.map(_epmc_by_doi_titles, uniq))
        tmap = dict(zip(uniq, got))
        n_err = sum(1 for v in got if v is None)
        for doc_id, _pmid, title, old_doi in no_pmid:
            k = _norm_doi(old_doi)
            titles = tmap.get(k, None)
            if titles is None or not titles:
                if titles is None:
                    unchanged += 1          # 查询失败：不动（保守）
                else:
                    if apply:
                        db.execute(text("UPDATE literature_paper SET doi=NULL WHERE doc_id=:d"), {"d": doc_id})
                    if csv_writer:
                        csv_writer.writerow(["doi", "clear", doc_id, "", old_doi, "", "doi_not_in_epmc"])
                    cleared += 1            # EPMC 明确无此 DOI → 假 DOI
                continue
            if any(_title_match(str(title or ""), t) for t in titles):
                unchanged += 1
            else:
                if apply:
                    db.execute(text("UPDATE literature_paper SET doi=NULL WHERE doc_id=:d"), {"d": doc_id})
                if csv_writer:
                    csv_writer.writerow(["doi", "clear", doc_id, "", old_doi, "", "title_mismatch_no_pmid"])
                cleared += 1
        if apply:
            db.commit()
        print(f"[doi] 第二遍完成（查询失败跳过 {n_err} 个 DOI）", flush=True)

    print(f"[doi] 计划：改成正确 DOI {fixed} 行 · 清空错误 DOI {cleared} 行 · 保持不动 {unchanged} 行", flush=True)
    return {"conflict_dois": len(bad), "rows": len(rows), "with_pmid": len(with_pmid),
            "no_pmid": len(no_pmid), "fixed": fixed, "cleared": cleared, "unchanged": unchanged}


# ------------------------------------------------------------------ 复核
def recheck(db) -> dict:
    dup_kegg = db.execute(text(
        "SELECT COUNT(*) FROM (SELECT gene_id, pathway_id FROM gene_kegg "
        "GROUP BY gene_id, pathway_id HAVING COUNT(*)>1) x"
    )).scalar()
    dup_doi = db.execute(text(
        "SELECT COUNT(*) FROM (SELECT LOWER(TRIM(doi)) d FROM literature_paper "
        "WHERE doi IS NOT NULL AND TRIM(doi)<>'' GROUP BY d HAVING COUNT(*)>1) x"
    )).scalar()
    total_kegg = db.execute(text("SELECT COUNT(*) FROM gene_kegg")).scalar()
    no_doi = db.execute(text(
        "SELECT COUNT(*) FROM literature_paper WHERE doi IS NULL OR TRIM(doi)=''"
    )).scalar()
    return {"dup_kegg_groups": dup_kegg, "dup_doi": dup_doi,
            "gene_kegg_rows": total_kegg, "rows_without_doi": no_doi}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的写库（默认只干跑）")
    ap.add_argument("--only", default="", help="逗号分隔：dup-kegg,doi")
    ap.add_argument("--limit", type=int, default=0, help="DOI 修复只处理前 N 行（试点用）")
    ap.add_argument("--batch", type=int, default=10)
    a = ap.parse_args(argv)
    only = {x.strip() for x in a.only.split(",") if x.strip()}

    rt = Path(os.getenv("ZEBRA_RUNTIME") or os.getenv("ZEBRAFISH_RUNTIME") or ROOT)
    print(f"数据库收尾 @ zebrafish_db（{'写库模式' if a.apply else '干跑模式'}）\n" + "=" * 72, flush=True)
    db = _db()
    before = recheck(db)
    print(f"[before] 重复通路组={before['dup_kegg_groups']} · gene_kegg 行={before['gene_kegg_rows']} · "
          f"重复 DOI={before['dup_doi']} · 无 DOI 行={before['rows_without_doi']}", flush=True)

    csv_path = None
    writer = None
    fh = None
    if a.apply:
        backup(rt, ["gene_kegg", "literature_paper"])
        csv_path = rt / f"db_cleanup_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        fh = csv_path.open("w", newline="", encoding="utf-8-sig")
        writer = csv.writer(fh)
        writer.writerow(["issue", "action", "key1", "key2", "key3", "old", "new", "note"])

    summary = {}
    try:
        if not only or "dup-kegg" in only:
            summary["dup_kegg"] = fix_dup_kegg(db, a.apply, writer)
        if not only or "doi" in only:
            summary["doi"] = fix_doi(db, a.apply, writer, batch=a.batch, limit=a.limit)
    finally:
        if fh:
            fh.close()
        after = recheck(db)
        db.close()

    print("-" * 72)
    print(f"[after]  重复通路组={after['dup_kegg_groups']} · gene_kegg 行={after['gene_kegg_rows']} · "
          f"重复 DOI={after['dup_doi']} · 无 DOI 行={after['rows_without_doi']}", flush=True)
    print("变化："
          f"重复通路组 {before['dup_kegg_groups']}→{after['dup_kegg_groups']} · "
          f"重复 DOI {before['dup_doi']}→{after['dup_doi']} · "
          f"无 DOI 行 {before['rows_without_doi']}→{after['rows_without_doi']}", flush=True)
    print("明细：" + json.dumps(summary, ensure_ascii=False), flush=True)
    if csv_path:
        print(f"逐行改动记录：{csv_path}", flush=True)
    if not a.apply:
        print("\n（干跑完成，未写库。确认无误后加 --apply 执行）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
