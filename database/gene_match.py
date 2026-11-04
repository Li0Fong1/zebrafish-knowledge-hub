# -*- coding: utf-8 -*-
"""基因检索的**匹配分级**模块（唯一出口）。

背景（2026-11-03 整改）
----------------------
旧实现在 `gene_toolkit.search_genes()` 里用一条 SQL 把 12 个 `LIKE '%kw%'`
串成 OR，四类语义完全不同的命中被拍平成一个列表，前端统一写成
「N genes matched」。检索 `p53` 于是返回 30 条，其中混进了：

  · `crebbpb` —— 它的别名里有个 EST 克隆号 `wu:fp53a03`（含 "p53" 三字符）
  · `mdm4`    —— 别名是基因全称描述 `mdm4, p53 regulator`
  · `rprma`   —— 别名是 `reprimo, tp53 dependent g2 arrest mediator candidate a`
  · `nop53`   —— 名字里含 "p53"，但它是另一个基因

这些都不是 p53 的别名。问题不在数据本身（ZFIN/NCBI 就是这么发布的），
在于**没有区分「同名」和「提到」**。

本模块把命中拆成 7 个层级，每个结果必须带 `matched_by` 与 `matched_value`：

  exact_symbol   gene_name / gene_symbol / standard_symbol 完全相等  → 就是它
  exact_alias    某条别名完全相等（排除 ID 型与描述型）             → 就是它
  exact_id       Ensembl / ZFIN / NCBI GeneID 完全相等             → 就是它
  prefix         符号以关键词开头（tp53 → tp53bp1）                → 不同基因
  contains       符号包含关键词（p53 → nop53）                     → 不同基因
  descriptive    描述型别名/克隆编号包含关键词                      → 只是提到
  function_text  功能描述文字里提到关键词                            → 只是提到

三条硬规则（防止同类问题复发）：
  R1  ID 型串（`zgc:` `si:` `wu:` `im:` `fi:` `ZDB-GENE-` `ENSDARG…` 纯数字）
      **不参与任何模糊匹配**，只能走 exact_id。
  R2  描述型串（含「, 」、词数 ≥4 且带 protein/regulator/candidate 等词、
      或长度 >40）不得算作精确别名，只能进 `descriptive`。
  R3  `descriptive` 与 `function_text` 永远不进 `results`，只进 `related`；
      调用方若只用 `results`，绝不会把这些当成同名基因。

所有入口（Genes 页、批量、按位置、聊天路由）都必须走 `search_classified()`。
"""
from __future__ import annotations

import re
from typing import Any, Optional

# ---- 层级定义（顺序即优先级，越靠前越可信）--------------------------------
TIERS = (
    "exact_symbol",
    "exact_alias",
    "exact_id",
    "conflict",
    "prefix",
    "contains",
    "descriptive",
    "function_text",
)

#: 可以称为「同一个基因」的层级
SAME_GENE_TIERS = ("exact_symbol", "exact_alias", "exact_id")
#: 只是名字相关、并非同一基因
LOWER_TIERS = ("prefix", "contains")

TIER_LABELS = {
    "exact_symbol": {"zh": "符号完全匹配", "en": "exact symbol"},
    "exact_alias": {"zh": "别名完全匹配", "en": "exact alias"},
    "exact_id": {"zh": "标识符完全匹配", "en": "exact identifier"},
    "conflict": {"zh": "同名冲突（需人工确认）", "en": "name conflict (needs review)"},
    "prefix": {"zh": "符号前缀相同（不同基因）", "en": "symbol starts with the query (different gene)"},
    "contains": {"zh": "符号包含该串（不同基因）", "en": "symbol contains the query (different gene)"},
    "descriptive": {"zh": "描述别名里提到（非同名）", "en": "mentioned in a descriptive alias (not a synonym)"},
    "function_text": {"zh": "功能注释里提到（非同名）", "en": "mentioned in the function text (not a synonym)"},
}

# 2026-11-04：分组下方那行解释性小字（TIER_HINTS）整段删除。
# 用户反馈：结果区只放结果，分组标题已经说明「这是什么」，再跟一句
# 「这些基因的符号里含输入串，是另外的基因，不是别名」属于噪音；口径说明
# 统一放「平台说明」。这里保留注释，避免以后有人再把同类文案加回来。

# ---- R1：ID 型串 ----------------------------------------------------------
_ID_PREFIXES = (
    "ensdarg", "ensdart", "ensdarp", "ensdar", "ensdag",          # Ensembl
    "zdb-gene", "zdb-rs", "zdb-",                                  # ZFIN
    "zgc:", "si:", "wu:", "im:", "fi:", "eu:", "cb:", "fc:", "fj:",
    "ns:", "dkeyp-", "dkey-", "ch73-", "ch211-", "busm1-", "xx-",
    "uniprot", "ipr", "go:", "hgnc", "mgi:", "rgd:",
)
_ID_RE = re.compile(r"^(ensdarg|ensdart|ensdarp|zdb-gene|zdb-rs)\d", re.I)
_CLONE_COLON_RE = re.compile(r"^[a-z]{1,6}:[a-z0-9._-]+$", re.I)


def is_id_like(s: str) -> bool:
    """判断一个串是不是标识符 / 克隆编号（R1）。"""
    t = (s or "").strip().lower()
    if not t:
        return False
    if t.isdigit():                       # 纯数字 = NCBI GeneID
        return True
    if _ID_RE.match(t):
        return True
    if t.startswith(_ID_PREFIXES):
        return True
    if _CLONE_COLON_RE.match(t):          # 任意 xxx:yyy 形式的克隆号
        return True
    return False


# ---- R2：描述型串 ---------------------------------------------------------
_DESCRIPTOR_WORDS = {
    "protein", "proteins", "regulator", "candidate", "homolog", "homologue",
    "factor", "subunit", "domain", "containing", "contains", "induced",
    "inducible", "dependent", "binding", "precursor", "isoform", "family",
    "member", "complex", "enzyme", "kinase", "receptor", "transporter",
    "putative", "probable", "antigen", "effector", "mediator", "associated",
    "related", "like", "type", "alpha", "beta", "gamma", "chain", "component",
    "regulatory", "interacting", "responsive", "repressor", "activator",
}


def is_descriptive(s: str) -> bool:
    """判断别名是不是「基因全称描述」而非真正的同义词（R2）。"""
    t = (s or "").strip()
    if not t:
        return False
    if ", " in t:                          # ZFIN 全称一律 "Xxx, yyy" 形式
        return True
    if len(t) > 40:
        return True
    words = re.split(r"[\s\-_/]+", t.lower())
    if len(words) >= 4 and any(w in _DESCRIPTOR_WORDS for w in words):
        return True
    if len(words) >= 3 and sum(1 for w in words if w in _DESCRIPTOR_WORDS) >= 2:
        return True
    return False


def alias_role(alias: str) -> str:
    """给一条别名归类：junk / id / descriptive / synonym。"""
    t = (alias or "").strip()
    # 2026-11-04：库里有 1,756 行别名就是一个「-」。这类串既不是标识符也不是
    # 同义词，按 synonym 处理会让搜「-」时返回上千个基因。统一按 junk 丢弃。
    if len(t) < 2 or not re.search(r"[0-9a-zA-Z]", t):
        return "junk"
    if is_id_like(t):
        return "id"
    if is_descriptive(t):
        return "descriptive"
    return "synonym"


# ---- 查询 -----------------------------------------------------------------
def _client():
    from mysql_client import get_mysql_client

    return get_mysql_client()


_SELECT = """
SELECT gb.gene_id, gb.gene_name, gb.gene_symbol, gb.chromosome,
       gb.ensembl_id, gb.zfin_id, gb.uniprot_id,
       SUBSTRING(COALESCE(gb.gene_function, ''), 1, 160) AS gene_function,
       n.standard_symbol AS standard_symbol
FROM gene_basic gb
LEFT JOIN gene_name_bundle n ON n.gene_id = gb.gene_id
"""


def _rows(sql: str, max_rows: int, params: dict) -> list[dict]:
    res = _client().execute_sql(sql, max_rows=max_rows, params=params)
    if not res.get("success"):
        return []
    return res.get("data") or []


def _alias_rows(kw: str, max_rows: int) -> list[dict]:
    """取回「别名完全等于关键词」的行，附带命中的别名原文。"""
    return _rows(
        _SELECT.replace("FROM gene_basic gb", ", ga.alias AS matched_value FROM gene_basic gb")
        + """
JOIN gene_alias ga ON ga.gene_id = gb.gene_id
WHERE LOWER(ga.alias) = LOWER(:kw)
LIMIT :lim
""", max_rows, {"kw": kw, "lim": max_rows})


def search_classified(keyword: str, limit: int = 20, browse: bool = False) -> dict:
    """分级检索。返回 dict，见模块文档。

    参数
    ----
    keyword : 用户输入（符号 / 别名 / 标识符 / 功能词）
    limit   : 每个层级最多取多少条
    browse  : True 且关键词为空时，按符号列出前 N 个（基因库浏览）
    """
    kw = (keyword or "").strip()
    lim = max(1, min(int(limit), 60))

    if not kw:
        return _browse(lim, browse)

    like = f"%{kw}%"
    like_prefix = f"{kw}%"
    params_base = {"kw": kw, "like": like, "pre": like_prefix}

    # 关键词本身是标识符时，不做符号模糊匹配（R1 的延伸：ID 查询就该只返回 ID 命中）
    id_query = is_id_like(kw)

    buckets: dict[str, list[dict]] = {t: [] for t in TIERS}
    seen: dict[int, dict] = {}

    def add(tier: str, rows: list[dict], value_key: Optional[str] = None,
            value_sql_alias: str = "matched_value") -> None:
        for r in rows:
            gid = r.get("gene_id")
            if gid is None:
                continue
            if gid in seen:
                # 已按更高层级收录：只补记一条命中原因，不改层级
                prev = seen[gid]
                val = r.get(value_sql_alias) or (r.get(value_key) if value_key else None)
                if val and val not in prev["matched_values"]:
                    prev["matched_values"].append(str(val))
                continue
            item = dict(r)
            val = item.pop(value_sql_alias, None)
            if val is None and value_key:
                val = item.get(value_key)
            item["matched_by"] = tier
            item["matched_value"] = str(val) if val is not None else kw
            item["matched_values"] = [item["matched_value"]] if val is not None else []
            item["same_gene"] = tier in SAME_GENE_TIERS
            item["confidence"] = _confidence(tier)
            seen[gid] = item
            buckets[tier].append(item)

    # 1) 正式符号完全匹配（gene_name / name_bundle.standard_symbol 才是正式符号）
    canonical = _rows(
        _SELECT + """
WHERE LOWER(gb.gene_name) = LOWER(:kw)
   OR LOWER(COALESCE(n.standard_symbol, '')) = LOWER(:kw)
LIMIT :lim
""", lim, {**params_base, "lim": lim})
    add("exact_symbol", canonical)
    canonical_ids = {r.get("gene_id") for r in canonical}

    # 1b) 命中 gb.gene_symbol 不等于命中正式符号：该列在本地库里是「常用别名」，
    #     所以只按别名层级计（tp53 的 gene_symbol 就是 p53）。
    #     同一个「常用别名」被多条记录占用时（库里确有 3 组），同样不能当成
    #     同一个基因，进冲突层。
    gsym_rows = _rows(
        _SELECT + """
WHERE LOWER(COALESCE(gb.gene_symbol, '')) = LOWER(:kw)
LIMIT :lim
""", lim, {**params_base, "lim": lim})
    if len({r.get("gene_id") for r in gsym_rows}) > 1:
        add("conflict", gsym_rows)
    else:
        add("exact_alias", gsym_rows)

    # 2) 别名完全匹配。先按 R1/R2 归类，再分别入桶：
    #    synonym    → exact_alias（这就是同一个基因）
    #    id 型      → exact_id（用户输入的就是标识符，属于精确查找）
    #    descriptive→ descriptive（全称描述，照实标「描述」）
    alias_rows = _alias_rows(kw, lim * 4)
    # 2026-11-04：同一条别名串如果挂在**不同 gene_id** 上，这条别名本身就是歧义的
    # （`actb1` 同时挂在 actb1 与 actb2 上、`taa` 挂在 29 个基因上）。这种绝不能说成
    # 「不同名字指的是同一个基因/同一个 ID」—— 一律进 conflict，由人确认。
    alias_owner: dict[str, set] = {}
    for r in alias_rows:
        key = str(r.get("matched_value") or "").strip().lower()
        alias_owner.setdefault(key, set()).add(r.get("gene_id"))
    ambiguous = {k for k, v in alias_owner.items() if len(v) > 1}

    alias_syn, alias_id, alias_desc, alias_amb = [], [], [], []
    for r in alias_rows:
        val = str(r.get("matched_value") or "")
        role = alias_role(val)
        if role == "junk":
            continue
        if role == "synonym" and val.strip().lower() in ambiguous:
            # 歧义别名：这条名字确实登记在某些基因下，但不止一个基因在用，
            # 不能判定「指的是同一个基因」→ 单列冲突层，由人确认
            alias_amb.append(r)
            continue
        if role == "synonym":
            alias_syn.append(r)
        elif role == "id":
            alias_id.append(r)
        else:
            alias_desc.append(r)
    # 别名恰好等于「另一个基因的正式符号」→ 别名表存在错挂（actb2 挂着 actb1），
    # 这种必须单列 conflict，绝不能当成同一个基因。
    conflict_rows, alias_syn_ok = [], []
    for r in alias_syn:
        if canonical_ids and r.get("gene_id") not in canonical_ids:
            conflict_rows.append(r)
        else:
            alias_syn_ok.append(r)
    add("exact_alias", alias_syn_ok)
    add("conflict", conflict_rows)
    add("conflict", alias_amb)

    # 3) 标识符完全匹配（本地字段 + 上面的 ID 型别名）
    add("exact_id", _rows(
        _SELECT + """
WHERE LOWER(COALESCE(gb.ensembl_id, '')) = LOWER(:kw)
   OR LOWER(COALESCE(gb.zfin_id, '')) = LOWER(:kw)
LIMIT :lim
""", lim, {**params_base, "lim": lim}))
    add("exact_id", alias_id)
    if alias_desc:
        add("descriptive", alias_desc)

    if not id_query:
        # 4) 符号前缀（不同基因）
        add("prefix", _rows(
            _SELECT + """
WHERE (gb.gene_name LIKE :pre OR gb.gene_symbol LIKE :pre OR n.standard_symbol LIKE :pre)
  AND LOWER(gb.gene_name) <> LOWER(:kw)
LIMIT :lim
""", lim, {**params_base, "lim": lim}), value_key="gene_name")

        # 5) 符号包含（不同基因）
        add("contains", _rows(
            _SELECT + """
WHERE (gb.gene_name LIKE :like OR gb.gene_symbol LIKE :like OR n.standard_symbol LIKE :like)
  AND gb.gene_name NOT LIKE :pre
  AND LOWER(gb.gene_name) <> LOWER(:kw)
LIMIT :lim
""", lim, {**params_base, "lim": lim}), value_key="gene_name")

        # 6) 描述型别名 / 克隆编号里出现（非同名）——含 R1 的克隆号，故必须单列
        desc_rows = _rows(
            _SELECT.replace("FROM gene_basic gb", ", ga.alias AS matched_value FROM gene_basic gb")
            + """
JOIN gene_alias ga ON ga.gene_id = gb.gene_id
WHERE ga.alias LIKE :like
  AND LOWER(ga.alias) <> LOWER(:kw)
LIMIT :lim
""", lim * 3, {**params_base, "lim": lim * 3})
        # 只保留 R1/R2 认定的「不是同义词」的串；真正的同义词已在 exact_alias 处理
        desc_rows = [r for r in desc_rows
                     if alias_role(r.get("matched_value") or "") in ("id", "descriptive")]
        add("descriptive", desc_rows)

        # 7) 功能注释里提到（非同名）
        add("function_text", _rows(
            _SELECT + """
WHERE gb.gene_function LIKE :like
LIMIT :lim
""", lim, {**params_base, "lim": lim}), value_key="gene_function")

    # 同一个基因如果既有同义词完全匹配、又出现在描述里，保留在 exact_alias
    # 同一基因的多份记录（不同来源各导入一条）在结果里合并成一行，
    # 免得同一个符号出现两行、还各自带着不同的 ID。
    for t in TIERS:
        if buckets[t]:
            buckets[t] = _merge_dupes(buckets[t])
    counts = {t: len(buckets[t]) for t in TIERS}
    # 这几层是按 limit 截断取的，条数达到 limit 时真实总数可能更多，
    # 必须标 capped，前端显示「N+」，不能把截断值当成总数。
    CAPPABLE = ("prefix", "contains", "descriptive", "function_text")
    groups = [
        {
            "tier": t,
            "label": TIER_LABELS[t],
            "same_gene": t in SAME_GENE_TIERS,
            "count": counts[t],
            "capped": t in CAPPABLE and counts[t] >= lim,
            "results": buckets[t],
        }
        for t in TIERS if buckets[t]
    ]

    results = [r for t in TIERS if t in SAME_GENE_TIERS + LOWER_TIERS for r in buckets[t]]
    related = [r for t in ("descriptive", "function_text") for r in buckets[t]]

    return {
        "query": kw,
        "is_id_query": id_query,
        "counts": counts,
        "total": sum(counts.values()),
        "same_gene_count": sum(counts[t] for t in SAME_GENE_TIERS),
        "groups": groups,
        "results": results,
        "related": related,
        "note": _note(kw, counts, id_query),
    }


def _ident_key(r: dict) -> tuple:
    """判断两条结果是不是「同一个基因」的键。

    2026-11-04：库里有同一基因被导入成两条记录的情况（`her6` 一条来自 ZFIN/Ensembl、
    一条来自 RefSeq，两条 ZFIN ID 相同）。检索时两行并排显示，用户看到的就是
    「不同的名字 / 不同的 ID，凭什么说是同一个」。这里按「权威 ID + 染色体」判定：
    染色体不同的绝不合并 —— `ntla`（chr9）与 `tbxta`（chr19）共用一个 ZFIN ID，
    那是 ID 本身有问题，属于冲突，不是同一条记录。
    """
    z = str(r.get("zfin_id") or "").strip().lower()
    e = str(r.get("ensembl_id") or "").strip().lower()
    c = str(r.get("chromosome") or "").strip().lower()
    if z:
        return ("z", z, c)
    if e:
        return ("e", e, c)
    return ("s", str(r.get("standard_symbol") or r.get("gene_name") or "").lower(), c)


def _merge_dupes(rows: list[dict]) -> list[dict]:
    """同一条基因的多份记录合并成一行，缺的字段互相补。"""
    out: list[dict] = []
    seen: dict[tuple, dict] = {}
    for r in rows:
        k = _ident_key(r)
        keep = seen.get(k)
        if keep is None:
            seen[k] = r
            out.append(r)
            continue
        keep.setdefault("merged_gene_ids", []).append(r.get("gene_id"))
        for f in ("ensembl_id", "zfin_id", "uniprot_id", "chromosome"):
            if not keep.get(f) and r.get(f):
                keep[f] = r[f]
    return out


def _confidence(tier: str) -> float:
    return {
        "exact_symbol": 1.0, "exact_alias": 0.98, "exact_id": 1.0,
        "conflict": 0.0,
        "prefix": 0.55, "contains": 0.4, "descriptive": 0.2, "function_text": 0.15,
    }.get(tier, 0.1)


def _note(kw: str, counts: dict, id_query: bool) -> str:
    same = sum(counts[t] for t in SAME_GENE_TIERS)
    lower = sum(counts[t] for t in LOWER_TIERS)
    desc = counts["descriptive"] + counts["function_text"]
    conflict = counts["conflict"]
    if id_query and same:
        return f"「{kw}」按标识符精确命中 {same} 个基因。"
    if same == 0 and lower == 0 and desc == 0 and conflict == 0:
        return f"「{kw}」没有命中。"
    parts = []
    if same:
        parts.append(f"{same} 个基因与「{kw}」同义（符号或登记别名）")
    if conflict:
        parts.append(f"{conflict} 个基因把这个名字登记为别名，但它同时是另一个基因的正式符号，需人工确认")
    if lower:
        parts.append(f"{lower} 个是名字里含该串的其他基因")
    if desc:
        parts.append(f"{desc} 个只是在描述或功能注释里提到它")
    return "；".join(parts) + "。"


def _browse(lim: int, browse: bool) -> dict:
    if not browse:
        return {"query": "", "is_id_query": False, "counts": {t: 0 for t in TIERS},
                "total": 0, "same_gene_count": 0, "groups": [], "results": [],
                "related": [], "note": "", "browse": False}
    rows = _rows(
        _SELECT + """
ORDER BY COALESCE(NULLIF(n.standard_symbol, ''), gb.gene_name) ASC
LIMIT :lim
""", lim, {"lim": lim})
    for r in rows:
        r["matched_by"] = "browse"
        r["matched_value"] = None
        r["matched_values"] = []
        r["same_gene"] = False
        r["confidence"] = 1.0
    return {
        "query": "", "is_id_query": False,
        "counts": {t: 0 for t in TIERS}, "total": len(rows),
        "same_gene_count": 0,
        "groups": [{"tier": "browse", "label": {"zh": "基因库浏览", "en": "browse"},
                    "same_gene": False, "count": len(rows), "results": rows}],
        "results": rows, "related": [], "note": "", "browse": True,
    }
