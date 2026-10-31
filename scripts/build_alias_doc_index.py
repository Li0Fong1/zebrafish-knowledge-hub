# -*- coding: utf-8 -*-
"""任务① 扩展：把「别名 → 文献」从题名级升级到**摘要 / 全文级**。

为什么必须离线建表
------------------
2026-10-16 实测：`literature_paper` 只有题名（没有摘要列）；`literature_fulltext_gold`
有 32,549 行但 `body_text` **全为空** —— 正文按设计外置磁盘，真身是
`解析文件/yes_fulltext_gold/batch_*/papers/*.json`（含 `title` / `sections.abstract` / `body_text`）。
所以 SQL 里 `LIKE` 拿不到全文，只能**离线扫一遍语料落表**，线上查询才可能秒回。

匹配规则（关键，否则会算错）
--------------------------
用**词边界**匹配，避免 `p53` 把 `tp53` 也算进去：
    正则 `(?<![a-z0-9])别名(?![a-z0-9])`（在已小写的文本上跑）；
别名里的 `-` `.` `:` 都按字面转义。

落两张表
--------
· `gene_alias_lit_stat` —— 精确计数（题名/摘要/全文各命中多少篇 + 去重总数），不受样本截断影响；
· `gene_alias_doc`      —— 明细样本（每别名最多 `--max-docs` 篇，带命中片段），供回答给引用。

用法
----
    python -X utf8 -m scripts.build_alias_doc_index --gene tp53
    python -X utf8 -m scripts.build_alias_doc_index --genes tp53,sox9b,mitfa
    python -X utf8 -m scripts.build_alias_doc_index --gene tp53 --rebuild
    python -X utf8 -m scripts.build_alias_doc_index --gene tp53 --limit 800   # 抽样试跑
    python -X utf8 -m scripts.build_alias_doc_index --list                    # 看已建了哪些基因
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 必须先 bootstrap_app()：它负责加载项目根 .env（光 `import bootstrap` 不加载 .env，
# 会让 MySQL 回退成 root@localhost 直接 1045 拒绝连接 —— 本轮已踩过）。
from core.entry import bootstrap_app  # noqa: E402

bootstrap_app()

CORPUS = ROOT / "解析文件" / "yes_fulltext_gold"
SQL_FILE = ROOT / "sql" / "gene_alias_doc_migration.sql"
MIN_ALIAS_LEN = 3          # 2 个字符的别名噪声太大
T0 = time.time()


def log(msg: str) -> None:
    print("[%6.1fs] %s" % (time.time() - T0, msg), flush=True)


def client():
    from mysql_client import get_mysql_client

    return get_mysql_client()


def q(sql: str, params: Optional[dict] = None, max_rows: int = 200) -> List[dict]:
    res = client().execute_sql(sql, params=params or {}, max_rows=max_rows)
    if not res.get("success"):
        raise RuntimeError(res.get("error") or "查询失败")
    return res.get("data") or []


def session_exec(stmts: List[Tuple[str, Optional[dict]]]) -> int:
    """在一个会话里执行多条写入（参数化绑定，避免别名里的冒号被 SQLAlchemy 当绑定变量）。"""
    from sqlalchemy import text

    cli = client()
    db = next(cli.get_db())
    n = 0
    try:
        for sql, params in stmts:
            db.execute(text(sql), params or {})
            n += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return n


def ensure_tables() -> None:
    if not SQL_FILE.is_file():
        raise RuntimeError("找不到建表 SQL：%s" % SQL_FILE)
    raw = SQL_FILE.read_text(encoding="utf-8")
    # 先**逐行**去掉注释，再按分号切分。
    # 坑：若直接按分号切再判 `startswith('--')`，那么"注释行 + CREATE TABLE"在同一段里
    # 会被整段当成注释丢掉 —— 表现是"建表就绪"却根本不建表，后面才报 1146 表不存在。
    ddl = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("--"))
    stmts = [s.strip() for s in ddl.split(";") if s.strip()]
    stmts = [s for s in stmts if not s.upper().startswith("USE ")]
    if not stmts:
        raise RuntimeError("建表 SQL 解析后为空，请检查 %s" % SQL_FILE)
    session_exec([(s, None) for s in stmts])
    log("建表就绪（已执行 %d 条 DDL）" % len(stmts))


# --------------------------------------------------------------------------- 别名
def aliases_of_gene(gene: str) -> List[dict]:
    """返回 [{'alias','source'}]，含标准符号本身；去过重、过滤过短与纯数字。"""
    from gene_toolkit import alias_block, gene_id_of

    gid = gene_id_of(gene)
    out: List[dict] = []
    seen = set()

    def _add(a: str, src: str) -> None:
        s = (a or "").strip()
        if len(s) < MIN_ALIAS_LEN or s.isdigit():
            return
        k = s.lower()
        if k in seen:
            return
        seen.add(k)
        out.append({"alias": s, "source": src})

    if gid:
        blk = alias_block(gid)
        for it in blk.get("items") or []:
            _add(it.get("alias"), it.get("source") or it.get("source_label") or "other")
    _add(gene, "standard")
    return out


def copies_of_gene(gene: str) -> List[str]:
    """该基因的全部共直系同源（复制体）——供 --from-paralogs 用（任务②需要它们各自的证据）。"""
    from gene_toolkit import gene_id_of, paralog_block

    gid = gene_id_of(gene)
    if not gid:
        return []
    pg = paralog_block(gid) or {}
    out: List[str] = []
    for g in (pg.get("groups") or []):
        for c in (g.get("copies") or []):
            s = c.get("symbol")
            if s and s not in out:
                out.append(s)
    return out


def compile_matchers(aliases: List[dict]) -> List[Tuple[str, str, re.Pattern]]:
    """把别名编译成带词边界的正则（避免 p53 把 tp53 也算上）。"""
    out = []
    for a in aliases:
        key = a["alias"].lower()
        pat = re.compile(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])")
        out.append((a["alias"], key, pat))
    return out


# --------------------------------------------------------------------------- 语料
def paper_meta() -> Dict[str, dict]:
    """预载 literature_paper（3 万行，约几 MB），用于把 eno/pmid 映射到 doc_id 与年份期刊。"""
    rows = q("SELECT doc_id, pmid, entry_no, doi, year, journal FROM literature_paper",
             max_rows=200000)
    by_key: Dict[str, dict] = {}
    for r in rows:
        did = str(r.get("doc_id") or "")
        eno = str(r.get("entry_no") or "")
        pmid = str(r.get("pmid") or "")
        if did:
            by_key["doc_id:" + did] = r
        if eno:
            by_key["eno:" + eno] = r
        if pmid:
            by_key["pmid:" + pmid] = r
    log("文献主档预载：%d 行" % len(rows))
    return by_key


def iter_papers(limit: int = 0):
    files = sorted(CORPUS.glob("batch_*/papers/*.json"))
    if limit:
        files = files[: int(limit)]
    log("语料文件：%d 个（%s）" % (len(files), CORPUS))
    return files


def snippet(text: str, m: re.Match, width: int = 110) -> str:
    a = max(0, m.start() - width)
    b = min(len(text), m.end() + width)
    s = re.sub(r"\s+", " ", text[a:b]).strip()
    return (("…" if a > 0 else "") + s + ("…" if b < len(text) else ""))[:590]


# --------------------------------------------------------------------------- 并行扫描
FIELD_PRIORITY = {"title": 0, "abstract": 1, "body": 2}


def _scan_chunk(payload):
    """worker：扫一批文件，返回（逐字段命中集合, 候选明细, 本批处理数）。

    为什么用多进程：单进程实测 12 分钟连 1/8 语料都跑不完（纯 CPU 瓶颈，
    json.loads + 13 条正则），而这台机 load 只有 1 左右、核很多 —— 并行后才可接受。
    """
    paths, alias_specs, cap = payload
    matchers = [(a, k, re.compile(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])"))
                for a, k in alias_specs]
    pre = re.compile("|".join(re.escape(k) for _a, k in alias_specs))
    counts = {k: {"title": set(), "abstract": set(), "body": set()} for _a, k in alias_specs}
    cand: Dict[str, List[dict]] = {k: [] for _a, k in alias_specs}
    n = 0
    for fp in paths:
        n += 1
        try:
            raw = Path(fp).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        low = raw.lower()
        if not pre.search(low):        # 快筛：不含任何别名 → 跳过 json 解析
            continue
        try:
            doc = json.loads(raw)
        except Exception:
            continue
        del raw, low
        sec = doc.get("sections") or {}
        raw_fields = {
            "title": str(doc.get("title") or ""),
            "abstract": str(sec.get("abstract") or ""),
            "body": str(doc.get("body_text") or ""),
        }
        pmid = str(doc.get("pmid") or "")
        eno = str(doc.get("entry_no") or Path(fp).stem)
        doc_id = ("pmid:" + pmid) if pmid else ("eno:" + eno)
        lowered = {k: v.lower() for k, v in raw_fields.items() if v}

        for _alias, key, pat in matchers:
            found: Dict[str, int] = {}
            for fname, text in lowered.items():
                c = len(pat.findall(text))
                if c:
                    found[fname] = c
            if not found:
                continue
            for fname in found:
                counts[key][fname].add(doc_id)
            best_f = sorted(found.items(), key=lambda kv: (FIELD_PRIORITY[kv[0]], -kv[1]))[0][0]
            m = pat.search(lowered[best_f])
            cand[key].append({
                "doc_id": doc_id,
                "field": best_f,
                "hits": sum(found.values()),
                "snippet": snippet(raw_fields[best_f], m) if m else "",
                "prio": FIELD_PRIORITY[best_f],
            })
    # 裁剪，避免 worker 回传过大（最终还会再按 max_docs 截）
    for k, items in cand.items():
        if len(items) > cap:
            items.sort(key=lambda r: (r["prio"], -r["hits"]))
            cand[k] = items[:cap]
    return counts, cand, n


# --------------------------------------------------------------------------- 主流程


def build_for_gene(gene: str, max_docs: int, limit: int, rebuild: bool,
                   workers_arg: int = 0) -> dict:
    al = aliases_of_gene(gene)
    if not al:
        log("⚠️ %s：本地库没有别名记录，跳过" % gene)
        return {"gene": gene, "aliases": 0}

    if not rebuild:
        have = q("SELECT COUNT(*) AS n FROM gene_alias_lit_stat WHERE gene_name = :g",
                 {"g": gene}, max_rows=1)
        if have and int(have[0].get("n") or 0) > 0:
            log("ℹ️ %s：已有统计记录，跳过（要重建加 --rebuild）" % gene)
            return {"gene": gene, "skipped": True}

    log("%s：%d 个别名参与扫描 → %s" % (gene, len(al),
                                     " / ".join(a["alias"] for a in al[:10])))

    files = iter_papers(limit)
    alias_specs = [(a["alias"], a["alias"].lower()) for a in al]

    # —— 多进程并行扫描 ——
    workers = int(workers_arg or 0) or min(8, max(1, (os.cpu_count() or 4) - 1))
    chunks = [c for c in (files[i::workers] for i in range(workers)) if c]
    log("并行度：%d 个进程，每批约 %d 篇" % (len(chunks), len(files) // max(1, len(chunks))))

    docs: Dict[str, Dict[str, set]] = {k: {"title": set(), "abstract": set(), "body": set()}
                                       for _a, k in alias_specs}
    cand: Dict[str, List[dict]] = {k: [] for _a, k in alias_specs}
    cap = max(60, int(max_docs) * 2)
    done = 0
    from concurrent.futures import ProcessPoolExecutor, as_completed

    with ProcessPoolExecutor(max_workers=len(chunks)) as ex:
        futs = [ex.submit(_scan_chunk, (c, alias_specs, cap)) for c in chunks]
        for fut in as_completed(futs):
            c_counts, c_cand, n_scanned = fut.result()
            for k, fields in c_counts.items():
                for fname, ids in fields.items():
                    docs[k][fname] |= ids
            for k, items in c_cand.items():
                cand[k].extend(items)
            done += n_scanned
            log("  已扫描 %d/%d" % (done, len(files)))

    matched_docs: set = set()
    for k in docs:
        matched_docs |= docs[k]["title"] | docs[k]["abstract"] | docs[k]["body"]

    # ---- 写统计表 ----
    stat_stmts: List[Tuple[str, Optional[dict]]] = []
    if rebuild:
        stat_stmts.append(("DELETE FROM gene_alias_lit_stat WHERE gene_name = :g", {"g": gene}))
        stat_stmts.append(("DELETE FROM gene_alias_doc WHERE gene_name = :g", {"g": gene}))

    src_of = {a["alias"].lower(): a["source"] for a in al}
    stats = []
    for alias, key in alias_specs:
        d = docs[key]
        union = d["title"] | d["abstract"] | d["body"]
        stats.append({
            "alias": alias, "key": key, "source": src_of.get(key, "other"),
            "title": len(d["title"]), "abstract": len(d["abstract"]), "body": len(d["body"]),
            "total": len(union),
        })
    stats.sort(key=lambda s: -s["total"])
    for s in stats:
        stat_stmts.append((
            "INSERT INTO gene_alias_lit_stat "
            "(gene_name, alias, alias_key, alias_source, title_docs, abstract_docs, body_docs,"
            " total_docs, scanned_docs) "
            "VALUES (:g, :a, :k, :src, :t, :ab, :b, :tot, :n) "
            "ON DUPLICATE KEY UPDATE alias_source=VALUES(alias_source), title_docs=VALUES(title_docs),"
            " abstract_docs=VALUES(abstract_docs), body_docs=VALUES(body_docs),"
            " total_docs=VALUES(total_docs), scanned_docs=VALUES(scanned_docs), built_at=NOW()",
            {"g": gene, "a": s["alias"], "k": s["key"], "src": s["source"],
             "t": s["title"], "ab": s["abstract"], "b": s["body"], "tot": s["total"],
             "n": len(files)},
        ))

    # ---- 写明细表（每别名保留最值得引用的 N 篇） ----
    meta = paper_meta()   # 扫描阶段用不到它（doc_id 由 eno/pmid 直接推），只为补年份/期刊
    detail_rows = 0
    for alias, key in alias_specs:
        items = cand[key]
        if not items:
            continue
        # 明细里只有 doc_id，年份/期刊/PMID 从主档补回来
        for r in items:
            m = meta.get("doc_id:" + str(r.get("doc_id") or "")) or {}
            r["pmid"] = str(m.get("pmid") or "")
            r["doi"] = str(m.get("doi") or "")
            r["year"] = m.get("year")
            r["journal"] = str(m.get("journal") or "")[:500]
        items.sort(key=lambda r: (r["prio"], 0 if r["pmid"] else 1, -r["hits"],
                                  -(int(r["year"]) if str(r["year"] or "").isdigit() else 0)))
        keep: List[dict] = []
        seen_doc = set()
        for r in items:
            if r["doc_id"] in seen_doc:
                continue
            seen_doc.add(r["doc_id"])
            keep.append(r)
            if len(keep) >= max_docs:
                break
        for r in keep:
            stat_stmts.append((
                "INSERT INTO gene_alias_doc "
                "(gene_name, alias, alias_key, doc_id, pmid, doi, field, hits, snippet, year, journal) "
                "VALUES (:g, :a, :k, :d, :p, :doi, :f, :h, :s, :y, :j) "
                "ON DUPLICATE KEY UPDATE alias=VALUES(alias), field=VALUES(field), hits=VALUES(hits),"
                " snippet=VALUES(snippet), year=VALUES(year), journal=VALUES(journal), built_at=NOW()",
                {"g": gene, "a": alias, "k": key, "d": r["doc_id"], "p": r["pmid"] or None,
                 "doi": r["doi"] or None, "f": r["field"], "h": r["hits"], "s": r["snippet"],
                 "y": int(r["year"]) if str(r["year"] or "").isdigit() else None,
                 "j": r["journal"] or None},
            ))
            detail_rows += 1

    log("写库：统计 %d 行 + 明细 %d 行…" % (len(stats), detail_rows))
    for i in range(0, len(stat_stmts), 400):
        session_exec(stat_stmts[i:i + 400])
    log("%s 完成：扫描 %d 篇，命中 %d 篇" % (gene, len(files), len(matched_docs)))
    for s in stats[:12]:
        log("   %-22s 题名 %5d · 摘要 %5d · 全文 %6d · 去重 %6d"
            % (s["alias"], s["title"], s["abstract"], s["body"], s["total"]))
    return {"gene": gene, "docs": len(matched_docs), "stats": stats}


def show_list() -> None:
    rows = q("SELECT gene_name, COUNT(*) AS n, SUM(total_docs) AS docs, MAX(built_at) AS t "
             "FROM gene_alias_lit_stat GROUP BY gene_name ORDER BY t DESC", max_rows=200)
    if not rows:
        print("（还没有任何基因建过倒排表）")
        return
    print("%-20s %6s %10s  %s" % ("gene", "aliases", "docs", "built_at"))
    for r in rows:
        print("%-20s %6s %10s  %s" % (r["gene_name"], r["n"], r["docs"], r["t"]))


def main() -> int:
    ap = argparse.ArgumentParser(description="建「别名 → 文献」全文级倒排表")
    ap.add_argument("--gene", help="单个基因（标准符号）")
    ap.add_argument("--genes", help="多个基因，逗号分隔")
    ap.add_argument("--from-paralogs", dest="from_paralogs",
                    help="建该基因**及其全部复制体**（任务②的分工结论要用它们各自的证据）")
    ap.add_argument("--max-docs", type=int, default=300, help="每个别名保留的明细篇数上限")
    ap.add_argument("--limit", type=int, default=0, help="只扫前 N 篇（抽样试跑）")
    ap.add_argument("--rebuild", action="store_true", help="先删旧数据再重建")
    ap.add_argument("--workers", type=int, default=0, help="并行进程数（0=自动，默认 CPU 核数-1，上限 8）")
    ap.add_argument("--list", action="store_true", help="列出已建好的基因")
    args = ap.parse_args()

    ensure_tables()
    if args.list:
        show_list()
        return 0

    genes: List[str] = []
    if args.genes:
        genes += [g.strip() for g in args.genes.split(",") if g.strip()]
    if args.gene:
        genes.append(args.gene.strip())
    if args.from_paralogs:
        base = args.from_paralogs.strip()
        if base and base not in genes:
            genes.append(base)
        extra = copies_of_gene(base) if base else []
        for c in extra:
            if c not in genes:
                genes.append(c)
        log("--from-paralogs %s → 连同复制体共 %d 个基因：%s" % (base, len(genes), ", ".join(genes)))
    if not genes:
        ap.error("要么给 --gene/--genes/--from-paralogs，要么用 --list")

    for g in genes:
        build_for_gene(g, max_docs=args.max_docs, limit=args.limit, rebuild=args.rebuild,
                       workers_arg=args.workers)
    show_list()
    return 0


if __name__ == "__main__":
    sys.exit(main())
