# -*- coding: utf-8 -*-
"""表型数据层（本地）：ZFIN 表型注释（基因→表型 / 表型→候选基因） + 本地文献命中。

数据来源两条腿，都是真实数据、都可回溯：
  · **ZFIN** 官方表型注释 ``phenoGeneCleanData_fish.txt``（从 zfin.org 下载，落在
    ``$RT/zfin/``），构建成本地 **SQLite 索引** ``$RT/zfin/zfin_phenotype.sqlite`` ——
    这样不依赖 MySQL 建表权限，查询毫秒级。
  · **本地文献库**（``literature_paper`` 题名 / ``literature_fulltext_gold`` 章节）
    —— 表型关键词的文献命中，永远可用（即使 ZFIN 索引还没建）。

文件里没有表头（ZFIN clean 格式）。列序**实测**（对前 30000 行做逐列填充率统计得到，不再靠猜）：
  0 表型行ID · 1 基因符号(ZFIN) · 2 ZFIN 基因ID · 3-6 部分行有值（等位基因/构造相关）·
  7 解剖实体ID(ZFA) · 8 解剖实体名 · 9 表型本体ID(PATO) · 10 表型描述 · 11 异常/程度 ·
  12-17 少量行有值 · 18 鱼ID · 19 基因型 · 20 起始时期 · 21 结束时期 · 22 基因-环境 ·
  23 文献(ZDB-PUB) · 24 图(ZDB-FIG)

注意：本文件的**基因符号用 ZFIN 口径**，与本地库 gene_basic 的 gene_symbol 可能不同
（例：tp53 在本地库主符号是 p53，ZFIN 写 tp53；ZDB-GENE 号也可能有新旧两版），
所以按基因查表型时会把「符号 / gene_name / 全部别名 / 输入词」一起当候选，
逐个在索引里数命中行数，取命中最多的那个，并把匹配到的符号一并返回（可核对）。
"""
from __future__ import annotations

import bootstrap  # noqa: F401

import json
import os
import re
import sqlite3
import time
from typing import Dict, List, Optional

from common_utils import logger


def _blast_dir() -> str:
    from seq_toolkit import BLAST_DIR

    return BLAST_DIR


def _runtime_dir() -> str:
    return os.path.dirname(_blast_dir())


def _zfin_dir() -> str:
    return os.path.join(_runtime_dir(), "zfin")


def _sqlite_path() -> str:
    return os.path.join(_zfin_dir(), "zfin_phenotype.sqlite")


def _source_files() -> List[str]:
    d = _zfin_dir()
    out = []
    for name in ("phenoGeneCleanData_fish.txt", "pheno_fish.txt"):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.path.getsize(p) > 1024:
            out.append(p)
    return out


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_sqlite_path(), timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------------ 构建索引

def build_index(force: bool = False, limit_files: Optional[int] = None) -> dict:
    """把 ZFIN 表型文件解析进 SQLite（幂等：已建且比源文件新则跳过）。"""
    files = _source_files()
    if limit_files:
        files = files[:limit_files]
    if not files:
        return {"ok": False, "error": "未找到 ZFIN 表型文件（%s）" % _zfin_dir()}
    path = _sqlite_path()
    os.makedirs(_zfin_dir(), exist_ok=True)
    newest = max(os.path.getmtime(f) for f in files)
    if (not force) and os.path.isfile(path) and os.path.getmtime(path) >= newest:
        with _conn() as conn:
            n = conn.execute("SELECT COUNT(*) FROM pheno").fetchone()[0]
        return {"ok": True, "skipped": True, "rows": n}

    tmp = path + ".tmp"
    if os.path.isfile(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.executescript("""
            CREATE TABLE pheno (
                pheno_id TEXT, symbol TEXT, zfin_gene_id TEXT,
                ao_id TEXT, ao_name TEXT, pato_id TEXT, pato_name TEXT, quality TEXT,
                fish_id TEXT, genotype TEXT, stage_start TEXT, stage_end TEXT,
                genox TEXT, pub_id TEXT, figure TEXT
            );
            CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        """)
        rows, genes, terms = 0, set(), set()
        file_stats: Dict[str, dict] = {}
        t0 = time.time()
        for f in files:
            accepted, skipped = 0, 0
            with open(f, encoding="utf-8", errors="replace") as fh:
                batch = []
                for line in fh:
                    if not line.strip():
                        continue
                    p = line.rstrip("\n").split("\t")
                    if len(p) < 25:
                        skipped += 1
                        continue
                    accepted += 1

                    def g(i: int) -> str:
                        return (p[i] or "").strip() if i < len(p) else ""

                    rec = (g(0), g(1), g(2),
                           g(7), g(8), g(9), g(10), g(11),
                           g(18), g(19), g(20), g(21), g(22), g(23), g(24))
                    batch.append(rec)
                    if g(1):
                        genes.add(g(1))
                    if g(8):
                        terms.add(g(8))
                    if len(batch) >= 5000:
                        conn.executemany("INSERT INTO pheno VALUES (%s)" % ",".join(["?"] * 15), batch)
                        rows += len(batch)
                        batch = []
                if batch:
                    conn.executemany("INSERT INTO pheno VALUES (%s)" % ",".join(["?"] * 15), batch)
                    rows += len(batch)
            file_stats[os.path.basename(f)] = {"accepted": accepted, "skipped": skipped}
        conn.executescript("""
            CREATE INDEX idx_pheno_symbol ON pheno(symbol);
            CREATE INDEX idx_pheno_gene   ON pheno(zfin_gene_id);
            CREATE INDEX idx_pheno_ao     ON pheno(ao_name);
            CREATE INDEX idx_pheno_pato   ON pheno(pato_name);
            CREATE INDEX idx_pheno_pub    ON pheno(pub_id);
        """)
        conn.executemany("INSERT INTO meta VALUES (?,?)", [
            ("built_at", time.strftime("%Y-%m-%d %H:%M:%S")),
            ("rows", str(rows)),
            ("genes", str(len(genes))),
            ("elapsed_s", "%.1f" % (time.time() - t0)),
            ("sources", "; ".join(os.path.basename(f) for f in files)),
            ("file_stats", json.dumps(file_stats, ensure_ascii=False)),
        ])
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, path)
    return {"ok": True, "rows": rows, "genes": len(genes), "terms": len(terms),
            "elapsed_s": round(time.time() - t0, 1)}


def status() -> dict:
    files = _source_files()
    path = _sqlite_path()
    out = {"source_files": [{"file": os.path.basename(f), "size_mb": round(os.path.getsize(f) / 1048576, 1)}
                            for f in files],
           "index_path": path, "index_ready": os.path.isfile(path)}
    if out["index_ready"]:
        try:
            with _conn() as conn:
                out["rows"] = conn.execute("SELECT COUNT(*) FROM pheno").fetchone()[0]
                out["genes"] = conn.execute("SELECT COUNT(DISTINCT symbol) FROM pheno").fetchone()[0]
                out["terms"] = conn.execute(
                    "SELECT COUNT(*) FROM (SELECT 1 FROM pheno GROUP BY ao_name, pato_name)").fetchone()[0]
                meta = {r["k"]: r["v"] for r in conn.execute("SELECT k, v FROM meta")}
                out["meta"] = meta
                out["built_at"] = meta.get("built_at")
        except Exception as exc:  # noqa: BLE001
            out["error"] = str(exc)
    out["note"] = ("ZFIN 官方表型注释（phenoGeneCleanData_fish.txt）本地解析成的 SQLite 索引；"
                   "文献编号是 ZFIN 的 ZDB-PUB，可在 zfin.org/<ZDB-PUB-…> 核对。"
                   "同目录下的 pheno_fish.txt 列结构不同（25 列以下），未纳入本索引，"
                   "其是否纳入可在 meta.file_stats 里看到。")
    return out


# ------------------------------------------------------------------ 查询

def _match_candidates(key: str, row: dict) -> List[str]:
    """把「输入词 / 本地符号 / 本地 gene_name / 全部别名」拼成候选列表（去重）。"""
    from mysql_client import get_mysql_client

    out: List[str] = []

    def add(v: object) -> None:
        s = str(v or "").strip()
        if s and s.lower() not in {x.lower() for x in out}:
            out.append(s)

    add(row.get("gene_symbol"))
    add(row.get("gene_name"))
    add(key)
    gid = row.get("gene_id")
    if gid:
        res = get_mysql_client().execute_sql(
            "SELECT alias FROM gene_alias WHERE gene_id = :g"
            " AND alias_type IN ('symbol','synonym','previous_name','zfin_id') LIMIT 40",
            params={"g": gid}, max_rows=0)
        for r in res.get("data") or []:
            add(r.get("alias"))
    return out[:40]


def gene_phenotypes(gene: str, limit: int = 200) -> dict:
    """基因 → 表型条目（按解剖实体 + 表型描述聚合）。

    匹配口径（透明、可核对）：把符号 / gene_name / 别名 / 输入词都当候选，
    逐个在 ZFIN 索引里统计命中行数，取命中最多的那个符号取数；不命中的候选
    与各自行数一并返回（``tried``），便于看出「为什么是这个符号」。
    """
    if not os.path.isfile(_sqlite_path()):
        return {"ok": False, "ready": False, "error": "ZFIN 表型索引尚未构建", "rows": []}
    key = (gene or "").strip()
    if not key:
        return {"ok": False, "error": "缺少基因名"}
    from mysql_client import get_mysql_client

    client = get_mysql_client()
    # 两步查：先把别名解析成 gene_id（某些 MySQL 版本不支持在 IN 子查询里写 LIMIT），
    # 再按「符号 / gene_name / Ensembl / ZFIN ID / gene_id」命中 gene_basic。
    gids: List[int] = []
    ares = client.execute_sql(
        "SELECT gene_id FROM gene_alias WHERE lower(alias) = lower(:k) LIMIT 5",
        params={"k": key}, max_rows=0)
    if ares.get("success", True):
        for r in ares.get("data") or []:
            try:
                gids.append(int(r.get("gene_id")))
            except (TypeError, ValueError):
                continue
    in_sql = ""
    params: Dict[str, object] = {"k": key}
    if gids:
        ph = []
        for i, gid in enumerate(gids[:5]):
            params["g%d" % i] = gid
            ph.append(":g%d" % i)
        in_sql = " OR gene_id IN (%s)" % ", ".join(ph)
    res = client.execute_sql(
        "SELECT gene_id, gene_name, gene_symbol, zfin_id, ensembl_id, chromosome"
        " FROM gene_basic WHERE lower(gene_name) = lower(:k) OR lower(gene_symbol) = lower(:k)"
        " OR lower(ensembl_id) = lower(:k) OR lower(zfin_id) = lower(:k)" + in_sql + " LIMIT 1",
        params=params, max_rows=0)
    query_ok = bool(res.get("success", True))
    row = (res.get("data") or [{}])[0] if res.get("data") else {}
    symbol = (row.get("gene_symbol") or row.get("gene_name") or key)
    zfin = (row.get("zfin_id") or "").strip()
    cap = max(1, min(int(limit or 200), 1000))

    cands = _match_candidates(key, row)
    if zfin and zfin.lower() not in {c.lower() for c in cands}:
        cands.insert(0, zfin)

    with _conn() as conn:
        tried: List[dict] = []
        best, best_n = (cands[0] if cands else key), -1
        for cand in cands:
            n = conn.execute(
                "SELECT COUNT(*) FROM pheno WHERE symbol = ? COLLATE NOCASE"
                " OR zfin_gene_id = ? COLLATE NOCASE", (cand, cand)).fetchone()[0]
            tried.append({"term": cand, "rows": int(n)})
            if n > best_n:
                best, best_n = cand, int(n)
        rows: List[dict] = []
        if best_n > 0:
            rows = [dict(r) for r in conn.execute(
                "SELECT pheno_id, symbol, zfin_gene_id, ao_id, ao_name, pato_id, pato_name, quality,"
                " genotype, stage_start, stage_end, pub_id, figure"
                " FROM pheno WHERE symbol = ? COLLATE NOCASE OR zfin_gene_id = ? COLLATE NOCASE"
                " LIMIT ?", (best, best, cap))]
    groups: Dict[str, dict] = {}
    pubs: Dict[str, int] = {}
    for r in rows:
        gk = "%s|%s" % (r.get("ao_name") or "-", r.get("pato_name") or "-")
        g = groups.setdefault(gk, {"ao_id": r.get("ao_id"), "ao_name": r.get("ao_name"),
                                   "pato_id": r.get("pato_id"), "pato_name": r.get("pato_name"),
                                   "quality": r.get("quality"), "count": 0,
                                   "stages": set(), "pubs": set()})
        g["count"] += 1
        for f in ("stage_start", "stage_end"):
            if r.get(f):
                g["stages"].add(r[f])
        if r.get("pub_id"):
            g["pubs"].add(r["pub_id"])
            pubs[r["pub_id"]] = pubs.get(r["pub_id"], 0) + 1
    out_groups = []
    for g in sorted(groups.values(), key=lambda x: -x["count"]):
        g["stages"] = sorted(g["stages"])
        g["pubs"] = sorted(g["pubs"])
        out_groups.append(g)
    return {"ok": True, "ready": True, "gene": {"gene_name": row.get("gene_name") or key,
                                               "gene_symbol": symbol, "zfin_id": zfin or None,
                                               "ensembl_id": row.get("ensembl_id"),
                                               "chromosome": row.get("chromosome")},
            "gene_query_ok": query_ok if row else False,
            "n_rows": len(rows), "groups": out_groups[:80],
            "matched_symbol": best if best_n > 0 else None,
            "tried": sorted(tried, key=lambda x: -x["rows"])[:12],
            "publications": [{"pub_id": p, "count": c} for p, c in sorted(pubs.items(), key=lambda kv: -kv[1])][:40],
            "source": "ZFIN phenoGeneCleanData_fish"}


def term_index(limit: int = 60) -> List[dict]:
    """常用表型描述（PATO）+ 解剖实体（ZFA）：按覆盖基因数排序。"""
    if not os.path.isfile(_sqlite_path()):
        return []
    with _conn() as conn:
        pato = [{"kind": "pato", "name": r["pato_name"], "id": r["pato_id"],
                 "genes": r["g"], "rows": r["n"]} for r in conn.execute(
            "SELECT pato_name, MIN(pato_id) pato_id, COUNT(DISTINCT symbol) g, COUNT(*) n"
            " FROM pheno WHERE pato_name <> '' GROUP BY pato_name ORDER BY g DESC LIMIT ?", (limit,))]
        ao = [{"kind": "ao", "name": r["ao_name"], "id": r["ao_id"],
               "genes": r["g"], "rows": r["n"]} for r in conn.execute(
            "SELECT ao_name, MIN(ao_id) ao_id, COUNT(DISTINCT symbol) g, COUNT(*) n"
            " FROM pheno WHERE ao_name <> '' GROUP BY ao_name ORDER BY g DESC LIMIT ?", (limit,))]
    return pato + ao


# 中文表型词 → ZFIN 里真正出现的英文词。
# 背景：ZFIN 表型注释（phenoGeneCleanData_fish）全英文，中文用户问「心脏畸形」
# 以前是 LIKE '%心脏%'，必然 0 命中，界面上看起来像"这个库没有表型数据"。
# 这张表只做**词面翻译**，不做任何推断；翻不出来就按原词查，并如实返回 0 条。
_ZH_PHENO_TERMS: dict[str, list[str]] = {
    # 器官 / 解剖
    "心脏": ["heart"], "心管": ["heart tube"], "血管": ["vasculature", "vessel"],
    "主动脉": ["aorta"], "静脉": ["vein"], "血液": ["blood"], "造血": ["hematopoiesis", "haematopoiesis"],
    "血细胞": ["blood cell"], "红细胞": ["erythrocyte"], "中性粒细胞": ["neutrophil"],
    "巨噬细胞": ["macrophage"], "胸腺": ["thymus"], "脾": ["spleen"], "肾": ["kidney"],
    "肝脏": ["liver"], "肝": ["liver"], "肠": ["intestine"], "肠道": ["intestine"],
    "胰": ["pancreas"], "胰腺": ["pancreas"], "鳔": ["swim bladder"], "肌肉": ["muscle"],
    "骨骼": ["skeleton"], "骨": ["bone", "skeleton"], "软骨": ["cartilage"], "鳍": ["fin"],
    "尾": ["tail"], "体节": ["somite"], "脊索": ["notochord"], "神经管": ["neural tube"],
    "神经嵴": ["neural crest"], "侧线": ["lateral line"], "脑": ["brain"], "神经": ["neuron", "nervous"],
    "轴突": ["axon"], "视网膜": ["retina"], "眼": ["eye"], "眼睛": ["eye"], "晶体": ["lens"],
    "耳": ["ear"], "内耳": ["inner ear"], "耳石": ["otolith"], "皮肤": ["skin"], "鳞": ["scale"],
    "卵": ["egg", "oocyte"], "胚胎": ["embryo"], "幼鱼": ["larva"], "孵化": ["hatching"],
    # 表型性质
    "畸形": ["abnormal"], "异常": ["abnormal"], "缺失": ["absent", "decreased"],
    "减少": ["decreased"], "增多": ["increased"], "水肿": ["edema"], "出血": ["hemorrhage"],
    "色素": ["pigment"], "黑色素": ["melanophore", "melanin"], "致死": ["lethal"],
    "运动": ["locomotion", "motility"], "行为": ["behavior", "behaviour"],
    "发育": ["development"], "形态": ["morphology"], "生长": ["growth"],
}
_ZH_TERMS_SORTED = sorted(_ZH_PHENO_TERMS, key=len, reverse=True)


def _expand_phenotype_query(key: str) -> tuple[list[list[str]], bool]:
    """中文查询 → 英文表型词分组；英文查询原样返回。

    返回 (词组列表, 是否发生过翻译)。语义是「组间 AND、组内 OR」：
      · 「心脏畸形」→ [[heart], [abnormal]]        → heart AND abnormal
      · 「色素缺失」→ [[pigment], [absent, decreased]] → pigment AND (absent OR decreased)
    组内是同一个中文词的多种英文说法（同义），必须 OR；否则「缺失」会被要求
    同时出现 absent 和 decreased，一条都查不到。
    """
    if not re.search(r"[\u4e00-\u9fff]", key):
        return [[key]], False
    groups: list[list[str]] = []
    rest = key
    for zh in _ZH_TERMS_SORTED:
        if zh in rest:
            alts = [w for w in _ZH_PHENO_TERMS[zh] if w]
            if alts:
                groups.append(alts)
            rest = rest.replace(zh, " ")
    if not groups:
        return [[key]], False
    return groups[:3], True


def _search_conditions(key: str) -> tuple[str, list, dict]:
    """把用户输入编译成 (WHERE 子句, 参数, 说明)。

    覆盖三类输入（这是 2026-11-18 修复的核心：旧版只搜名字三列，
    编号 ZFA:0009256 / PATO:xxx 一律 0 命中，英文多词整串 LIKE 也 0 命中）：
      1. 本体编号：ZFA:ddddddd / PATO:ddddddd —— 直接精确匹配 ao_id / pato_id；
         也接受裸数字编号（如 0009256）与无冒号写法（ZFA0009256）。
      2. 中文：走 _expand_phenotype_query 词面翻译，组间 AND、组内 OR。
      3. 英文：先尝试整串精确短语；多词再拆成逐词 AND（每词匹配名字/质量/编号），
         这样 "increased heart size" 不会被当成一个不存在的完整短语。
    """
    meta = {"kind": "text", "terms": []}

    # ① 本体编号识别（ZFA / PATO，大小写不敏感，容忍缺冒号）
    m = re.match(r"^\s*(ZFA|PATO)\s*:?\s*([0-9]{4,})\s*$", key, re.I)
    if m:
        prefix, digits = m.group(1).upper(), m.group(2)
        # ZFIN 库里编号形如 ZFA:0009256；兼容存储时有没有前缀两种写法
        col = "ao_id" if prefix == "ZFA" else "pato_id"
        canon = "%s:%s" % (prefix, digits)
        meta.update(kind="id", terms=[canon])
        where = ("%s = ? OR %s = ? OR %s LIKE ?" % (col, col, col),)
        return where[0], [canon, digits, "%" + digits + "%"], meta

    # ② 中文 → 英文词组
    if re.search(r"[\u4e00-\u9fff]", key):
        groups, _translated = _expand_phenotype_query(key)
        conds, params = [], []
        for alts in groups:
            ors = []
            for k in alts:
                ors.append("ao_name LIKE ? OR pato_name LIKE ? OR quality LIKE ?"
                           " OR ao_id LIKE ? OR pato_id LIKE ?")
                params.extend(["%" + k + "%"] * 5)
            conds.append("(" + " OR ".join(ors) + ")")
        meta.update(kind="zh", terms=[w for grp in groups for w in grp])
        return " AND ".join(conds), params, meta

    # ③ 英文：整串作为一个词组；多词再补一组「逐词 AND」由调用方合并（见 term_search）
    conds, params = [], []
    for k in [key]:
        ors = []
        for col in ("ao_name", "pato_name", "quality", "ao_id", "pato_id"):
            ors.append(col + " LIKE ?")
            params.append("%" + k + "%")
        conds.append("(" + " OR ".join(ors) + ")")
    meta["terms"] = [key]
    return " AND ".join(conds), params, meta


def _term_rows(conn, where: str, params: list, limit: int, limit_genes: int) -> tuple[list, list]:
    """按统一 WHERE 取表型条目与候选基因（供短语/拆词两轮复用）。"""
    terms: list[dict] = []
    genes: list[dict] = []
    seen_t: set = set()
    for r in conn.execute(
        "SELECT ao_name, ao_id, pato_name, pato_id, quality, COUNT(*) n,"
        " COUNT(DISTINCT symbol) g FROM pheno WHERE " + where +
        " GROUP BY ao_name, pato_name, ao_id, pato_id ORDER BY g DESC, n DESC LIMIT ?",
        (*params, limit),
    ):
        d = dict(r)
        sig = (d.get("ao_id"), d.get("ao_name"), d.get("pato_id"), d.get("pato_name"))
        if sig in seen_t:
            continue
        seen_t.add(sig)
        terms.append(d)
    seen_g: set = set()
    for r in conn.execute(
        "SELECT symbol, MIN(zfin_gene_id) zfin_gene_id, COUNT(*) n,"
        " COUNT(DISTINCT pato_name) p FROM pheno WHERE " + where +
        " GROUP BY symbol ORDER BY n DESC LIMIT ?",
        (*params, limit_genes),
    ):
        d = dict(r)
        if d.get("symbol") in seen_g:
            continue
        seen_g.add(d.get("symbol"))
        genes.append(d)
    return terms, genes


def term_search(q: str, limit: int = 40) -> dict:
    """表型关键词 → 命中表型条目 + 候选基因（ZFIN）。

    支持：ZFA/PATO 编号、中文词面翻译、英文短语与英文多词拆词 AND。
    英文多词用「先整串、不足再拆词」两轮，既保住精确短语，又避免整串 0 命中。
    """
    key = (q or "").strip()
    if not key:
        return {"ok": False, "error": "缺少关键词"}
    if not os.path.isfile(_sqlite_path()):
        return {"ok": False, "ready": False, "error": "ZFIN 表型索引尚未构建", "genes": [], "terms": []}

    where, params, meta = _search_conditions(key)
    terms: list[dict] = []
    genes: list[dict] = []
    with _conn() as conn:
        terms, genes = _term_rows(conn, where, params, limit, limit)

        # 英文多词：整串命中不足时，拆成逐词 AND 再查一轮并合并。
        if meta["kind"] == "text" and len(key.split()) >= 2 and len(terms) < 3:
            words = [w for w in re.split(r"[\s,;、，；]+", key) if len(w) >= 2]
            if len(words) >= 2:
                conds, wparams = [], []
                for w in words:
                    ors = []
                    for col in ("ao_name", "pato_name", "quality", "ao_id", "pato_id"):
                        ors.append(col + " LIKE ?")
                        wparams.append("%" + w + "%")
                    conds.append("(" + " OR ".join(ors) + ")")
                t2, g2 = _term_rows(conn, " AND ".join(conds), wparams, limit, limit)
                have_t = {(d.get("ao_id"), d.get("pato_id")) for d in terms}
                have_g = {d.get("symbol") for d in genes}
                terms += [d for d in t2 if (d.get("ao_id"), d.get("pato_id")) not in have_t]
                genes += [d for d in g2 if d.get("symbol") not in have_g]
                meta["terms"] = words
                meta["kind"] = "words"

    genes.sort(key=lambda d: -int(d.get("n") or 0))
    out = {
        "ok": True,
        "ready": True,
        "query": key,
        "terms": terms[:limit],
        "genes": genes[:limit],
        "source": "ZFIN phenoGeneCleanData_fish",
        "match_kind": meta["kind"],
    }
    if meta["terms"]:
        out["query_terms"] = meta["terms"]
    return out


# ------------------------------------------------------------------ 文献兜底

def literature_hits(q: str, limit: int = 10) -> List[dict]:
    """本地文献库里该表型/关键词的命中（题名级，永远可用）。"""
    key = (q or "").strip()
    if not key:
        return []
    from mysql_client import get_mysql_client

    like = "%" + key + "%"
    cap = max(1, min(int(limit or 10), 50))
    res = get_mysql_client().execute_sql(
        "SELECT pmid, doi, title_en, title_zh, journal, year FROM literature_paper"
        " WHERE title_en LIKE :k OR title_zh LIKE :k OR doi LIKE :k"
        " ORDER BY year DESC LIMIT %d" % cap, params={"k": like}, max_rows=0)
    rows = res.get("data") or []
    return [{"pmid": r.get("pmid"), "doi": r.get("doi"),
             "title": r.get("title_en") or r.get("title_zh"),
             "journal": r.get("journal"), "year": r.get("year"),
             "url": ("https://pubmed.ncbi.nlm.nih.gov/%s/" % r["pmid"]) if r.get("pmid") else None}
            for r in rows]
