#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量 ID 互转：18 个装配 × 18 个装配（324 组）全组合清查（2026-11-12）。

运行位置：服务器项目根目录（要能连 MySQL、能读 UCSC chain 文件）
    PY=/data/zoujin/deng/miniconda3/envs/zebrafish/bin/python
    $PY scripts/audit_id_version_map.py --coords     # 阶段 1：真实 chain 坐标基准
    $PY scripts/audit_id_version_map.py              # 阶段 2：324 组全组合清查

为什么要分两阶段
----------------
线上 uvicorn 常驻约 19 GB，cgroup 上限 36.8 GB；而 36 条 UCSC chain 全量
加载会再吃掉 10–15 GB（单条慢链首加载 60–140 s），一个进程里既做坐标又做
324 组清查会撞 OOM。因此：
  · 阶段 1 独立进程按「目标版本」逐个做真实 lift_region，每算完一个版本就
    清链缓存 + malloc_trim 归还内存，落盘成坐标基准 JSON 后退出；
  · 阶段 2 复用这份**真实 chain 换算结果**（不再加载链），几分钟内跑完 324 组，
    验证 ID 转换本身，并逐组比对坐标是否与基准一致。

清查项
------
1. 每个 (来源, 目标) 组合都能返回、条数/顺序/query 一一对应、builds 标签正确；
2. 同一批标准 ID 在所有版本组合下解析到同一个基因实体（ZFIN/符号/NCBI）；
3. 不存在的 ID 一律 not_found，不出现跨版本误命中；
4. 同版本 A→A 恒等；A→B→A 往返仍回到同一基因；
5. 目标/来源坐标与阶段 1 的真实 chain 基准逐位一致；chain 失败时坐标留空
   并给出 code，绝不返回近似坐标；
6. 没有本地注释表的 16 个装配，结果必须标 identity_basis 且 notes 写明口径；
7. 306 个有序版本对的 chain 路由可达性。

产物：output/id_version_coord_baseline.json、id_version_map_audit.json / .md
"""
from __future__ import annotations

import ctypes
import gc
import json
import logging
import re
import sys
import time
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "database"), str(ROOT / "core"), str(ROOT)]
logging.getLogger().setLevel(logging.WARNING)

import id_version_map as ivm  # noqa: E402
import coord_liftover as cl  # noqa: E402
from coord_liftover import lift_region, resolve_route  # noqa: E402
from mysql_client import MySQLClient  # noqa: E402

OUT_DIR = ROOT / "output"
BASELINE_JSON = OUT_DIR / "id_version_coord_baseline.json"
OUT_JSON = OUT_DIR / "id_version_map_audit.json"
OUT_MD = OUT_DIR / "id_version_map_audit.md"

# 覆盖 6 类输入：符号 / Ensembl / NCBI Gene / ZFIN / UniProt / 第二个基因 / 负例
IDS = [
    "tp53", "ENSDARG00000035559", "30590", "ZDB-GENE-990415-270",
    "P79734", "sox9b", "NO_SUCH_GENE_ZFQA_9999",
]
NEG = IDS[-1]
POS = IDS[:-1]
SPAN_RE = re.compile(r"^[^:]+:[\d,]+-[\d,]+(?:\([+-]\))?$")


def xref(row, names):
    for x in row.get("xrefs") or []:
        if str(x.get("db_name") or "").lower() in names:
            return str(x.get("db_id") or "").strip()
    return ""


def identity(row):
    """基因实体的版本无关身份。"""
    c = row.get("canonical") or {}
    return {
        "zfin": (str(c.get("zfin_id") or "") or xref(row, ("zfin",))).strip(),
        "ensembl": str(c.get("ensembl_id") or "").split(".")[0].strip(),
        "symbol": str(c.get("gene_name") or "").strip().lower(),
        "ncbi": xref(row, ("ncbi_gene", "ncbi_gene_grcz12tu")),
    }


def identity_key(d):
    # ZFIN / 符号 / NCBI Gene 是最稳定锚点；Ensembl 主表可能带多个 ID，
    # 单独统计但不参与「是否同一个基因」的硬判定。
    return (d["zfin"], d["symbol"], d["ncbi"])


def fmt_span(chrom, start, end, strand=""):
    if chrom is None or start is None or end is None:
        return ""
    sg = str(strand or "").strip()
    return "%s:%s-%s%s" % (chrom, format(int(start), ","), format(int(end), ","),
                           "(%s)" % sg if sg in ("+", "-") else "")


def lift_key(hub, chrom, start, end, strand, target):
    return "|".join(str(x) for x in (hub, chrom, start, end, strand, target))


def rss_mb():
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except Exception:  # noqa: BLE001
        pass
    return -1


def release_chain_memory():
    """清链对象并让 glibc 把大块内存还给 OS，避免与线上 uvicorn 抢 cgroup。"""
    try:
        cl._loader.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:  # noqa: BLE001
        pass


def hub_coords(symbol="tp53"):
    c = MySQLClient()
    r = c.execute_sql(
        "SELECT chrom, start_pos, end_pos, strand FROM gene_master_grcz12tu"
        " WHERE symbol='%s' LIMIT 1" % symbol, max_rows=1)
    rows = r.get("data") or []
    if not rows:
        return None
    m = rows[0]
    return {"chrom": str(m["chrom"]), "start": int(m["start_pos"]),
            "end": int(m["end_pos"]), "strand": str(m.get("strand") or "+")}


def grcz11_official_coords(symbol="tp53"):
    c = MySQLClient()
    r = c.execute_sql(
        "SELECT x.chromosome, x.start_pos, x.end_pos, x.strand"
        " FROM gene_assembly_xref x JOIN gene_basic b ON b.gene_id=x.gene_id"
        " WHERE x.assembly_name='GRCz11' AND b.gene_name='%s' LIMIT 1" % symbol,
        max_rows=1)
    rows = r.get("data") or []
    if not rows:
        return ""
    m = rows[0]
    return fmt_span(m["chromosome"], m["start_pos"], m["end_pos"], m["strand"])


# ------------------------------------------------------------------ 阶段 1
def run_coords_phase(builds):
    """真实 chain 换算，落盘坐标基准；每算完一个目标版本就释放链内存并增量写盘。

    单条慢链首次加载可达 140 s，整轮要几十分钟；增量写盘 + completed_builds
    让进程若被 OOM 打断后可以直接续跑，不必从头再来。
    """
    t0 = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ents = ivm._resolve_entities(IDS)
    genes = []
    for e in ents:
        hc, hub = e.get("hub_coords"), e.get("hub_build")
        if hc and hub:
            genes.append({"query": e["query"], "hub": hub, **hc})
    print("[coords] genes with hub coords: %d / %d" % (len(genes), len(IDS)), flush=True)

    entries, done = {}, set()
    if BASELINE_JSON.is_file():
        try:
            old = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
            entries = dict(old.get("entries") or {})
            done = set(old.get("completed_builds") or [])
            print("[coords] resume: %d builds already done" % len(done), flush=True)
        except Exception as e:  # noqa: BLE001
            print("[coords] resume failed, start fresh: %s" % e, flush=True)

    for i, target in enumerate(builds, 1):
        if target in done:
            print("[coords] %2d/%d %-14s skipped (already done)" % (i, len(builds), target),
                  flush=True)
            continue
        for g in genes:
            key = lift_key(g["hub"], g["chrom"], g["start"], g["end"], g["strand"], target)
            d = lift_region(g["hub"], g["chrom"], g["start"], g["end"], g["strand"], target)
            entries[key] = {
                "ok": bool(d.get("ok")),
                "code": str(d.get("code") or ""),
                "span": fmt_span(d.get("chromosome"), d.get("start"), d.get("end"),
                                 d.get("strand")) if d.get("ok") else "",
                "hops": d.get("route_hops") or [],
            }
        done.add(target)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_seconds": round(time.time() - t0, 1),
            "builds": builds,
            "hub_genes": genes,
            "entries": entries,
            "completed_builds": sorted(done),
        }
        tmp = BASELINE_JSON.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(BASELINE_JSON)
        tp = genes[0] if genes else None
        sample = entries.get(lift_key(tp["hub"], tp["chrom"], tp["start"], tp["end"],
                                     tp["strand"], target), {}) if tp else {}
        print("[coords] %2d/%d %-14s tp53=%s rss=%dMB elapsed=%.0fs" %
              (i, len(builds), target, sample.get("span") or sample.get("code"),
               rss_mb(), time.time() - t0), flush=True)
        release_chain_memory()

    print("[coords] done → %s (%d entries, %d builds, %.0fs)" %
          (BASELINE_JSON, len(entries), len(done), time.time() - t0), flush=True)
    return 0


# ------------------------------------------------------------------ 阶段 2
def baseline_from_log(log_path, builds):
    """应急恢复：从 --coords 的进度日志里取回已完成的 tp53 真实换算结果。

    日志每行形如 ``[coords]  7/18 Zv8  tp53=5:22,115,728-22,127,306(+) rss=...``；
    失败行 tp53= 后面是 code。恢复出的基准只含 tp53，但主清查对 tp53 做逐版本
    严格比对，其余基因坐标会因缺基准而留空（不影响 ID 转换正确性判定）。
    """
    hc = hub_coords()
    if not hc:
        print("[recover] no hub coords", flush=True)
        return 1
    gene = {"query": "tp53", "hub": "GRCz12tu", **hc}
    entries, done = {}, []
    for line in Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\[coords\]\s+\d+/\d+\s+(\S+)\s+tp53=(\S+)", line)
        if not m:
            continue
        build, value = m.group(1), m.group(2)
        if build not in builds:
            continue
        ok = ":" in value and "-" in value  # 坐标形如 5:1,000-2,000(+)
        key = lift_key("GRCz12tu", hc["chrom"], hc["start"], hc["end"], hc["strand"], build)
        entries[key] = {"ok": ok, "code": "liftover_ok" if ok else value,
                        "span": value if ok else "", "hops": []}
        if build not in done:
            done.append(build)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "builds": builds,
        "hub_genes": [gene],
        "entries": entries,
        "completed_builds": sorted(done),
        "note": "recovered from --coords progress log (tp53 only)",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[recover] %d builds → %s" % (len(done), BASELINE_JSON), flush=True)
    return 0


def fake_lift_from_baseline(entries):
    def _fake(hub, chrom, start, end, strand, target):
        e = entries.get(lift_key(hub, chrom, start, end, strand, target))
        if not e:
            return (False, "baseline_missing", "", "")
        return (bool(e.get("ok")), str(e.get("code") or "liftover_failed"),
                str(e.get("span") or ""), "")
    return _fake


def main():
    t0 = time.time()
    builds = list(ivm.SUPPORTED_BUILDS)
    if "--recover-log" in sys.argv:
        i = sys.argv.index("--recover-log")
        return baseline_from_log(sys.argv[i + 1] if i + 1 < len(sys.argv)
                                 else "/tmp/zf_idv_audit_coords.log", builds)
    if "--coords" in sys.argv:
        return run_coords_phase(builds)

    baseline = {}
    chain_expected = {}
    if BASELINE_JSON.is_file():
        try:
            baseline = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print("[warn] baseline unreadable: %s" % e, flush=True)
    entries = baseline.get("entries") or {}
    # 主清查不再加载链：坐标走阶段 1 的真实换算基准，内存保持在 1 GB 内。
    ivm._lift_cached = fake_lift_from_baseline(entries)
    genes = baseline.get("hub_genes") or []
    tp_gene = next((g for g in genes if g.get("query") == "tp53"), None)
    if tp_gene:
        for b in builds:
            e = entries.get(lift_key(tp_gene["hub"], tp_gene["chrom"], tp_gene["start"],
                                    tp_gene["end"], tp_gene["strand"], b))
            if e:
                chain_expected[b] = e

    cap = ivm.build_capabilities()
    hc = hub_coords()
    g11 = grcz11_official_coords()
    print("[init] builds=%d routable=%d baseline=%d entries hub=%s grcz11_xref=%s" %
          (len(builds), len(cap.get("routable_pairs") or []), len(entries), hc, g11), flush=True)

    # 所有组合输入相同 → 缓存实体解析，避免 324 次重复打库（不改变被测逻辑）
    orig_resolve = ivm._resolve_entities
    ent_cache = {}

    def cached_resolve(ids):
        key = tuple(ids)
        if key not in ent_cache:
            ent_cache[key] = orig_resolve(list(ids))
        return ent_cache[key]

    ivm._resolve_entities = cached_resolve

    combos = [(s, t) for s in builds for t in builds]
    smoke = "--smoke" in sys.argv
    if smoke:
        combos = [("GRCz11", "GRCz12tu"), ("GRCz12tu", "GRCz11"),
                  ("Zv9", "GRCz11"), ("GRCz10", "GRCz12tu"), ("Zv3", "Zv3")]
    elif "--quick" in sys.argv:
        combos = combos[:8]

    base = ivm.convert_batch(IDS, builds[0], builds[0])
    reference = {}
    for r in base.get("results") or []:
        if r.get("status") == "unique":
            reference[r.get("query")] = identity_key(identity(r))
    print("[init] reference identities: %d / %d" % (len(reference), len(POS)), flush=True)

    failures, pair_details = [], []
    coord_basis = Counter()
    coord_codes = Counter()
    identity_counter = Counter()
    identity_drift = []
    neg_bad = []
    route_bad = []
    rt_total = rt_ok = 0

    for idx, (s, t) in enumerate(combos, 1):
        errs = []
        try:
            res = ivm.convert_batch(IDS, s, t)
        except Exception as e:  # noqa: BLE001
            failures.append({"source": s, "target": t,
                             "errors": ["exception: %s: %s" % (type(e).__name__, e)],
                             "trace": traceback.format_exc()[-800:]})
            continue
        rows = res.get("results") or []
        binfo = res.get("builds") or {}
        if res.get("count") != len(IDS) or len(rows) != len(IDS):
            errs.append("count mismatch: count=%s rows=%d" % (res.get("count"), len(rows)))
        if [r.get("query") for r in rows] != IDS:
            errs.append("query order/content mismatch")
        if binfo.get("source") != s or binfo.get("target") != t:
            errs.append("builds label wrong: %s" % binfo)
        if binfo.get("converted") != (s != t):
            errs.append("builds.converted wrong")

        neg_rows = [r for r in rows if r.get("query") == NEG]
        if not neg_rows or neg_rows[0].get("status") != "not_found":
            neg_bad.append("%s→%s: %s" % (s, t, neg_rows[0].get("status") if neg_rows else "missing"))
            errs.append("negative control resolved")

        pos_rows = [r for r in rows if r.get("query") in POS]
        unique = [r for r in pos_rows if r.get("status") == "unique"]
        for r in pos_rows:
            if r.get("status") != "unique":
                errs.append("positive ID not resolved: %s" % r.get("query"))
                continue
            ident = identity(r)
            identity_counter[identity_key(ident)] += 1
            ref = reference.get(r.get("query"))
            if ref and identity_key(ident) != ref:
                errs.append("identity drift for %s: %s != %s" %
                            (r.get("query"), identity_key(ident), ref))
                identity_drift.append("%s→%s %s" % (s, t, r.get("query")))
        if not unique:
            errs.append("no positive ID resolved")

        local_pair = s in ivm.LOCAL_TABLE_BUILDS and t in ivm.LOCAL_TABLE_BUILDS
        if not local_pair:
            if binfo.get("identity_basis") != "stable_ids_with_chain_coords":
                errs.append("identity_basis missing")
            tp = next((r for r in rows if r.get("query") == "tp53"), None)
            # 目标本身有本地注释表（GRCz11 / GRCz12tu）时输出的是真实版本专属 ID，
            # 不需要「稳定标识」说明；只有无表版本才必须有口径标注。
            if tp and not binfo.get("target_has_gene_table") and not any(
                    "跨版本稳定标识" in str(n) or "version-stable" in str(n)
                    for n in (tp.get("notes") or [])):
                errs.append("stable-id note missing")

        coord_ok = 0
        for r in rows:
            tc = str(r.get("target_coords") or "")
            sc = str(r.get("source_coords") or "")
            coord_basis[str(r.get("coord_basis") or "none")] += 1
            coord_codes[str(r.get("coord_status") or "none")] += 1
            if tc:
                if not SPAN_RE.match(tc):
                    errs.append("bad target span format: %s" % tc)
                coord_ok += 1
            if sc and not SPAN_RE.match(sc):
                errs.append("bad source span format: %s" % sc)
            if not r.get("coord_status") and not local_pair:
                errs.append("coord_status missing for %s" % r.get("query"))

        tp = next((r for r in rows if r.get("query") == "tp53"), None)
        if tp and not local_pair and t not in (ivm.BUILD_GRCZ11, ivm.BUILD_GRCZ12TU):
            exp = chain_expected.get(t)
            got = str(tp.get("target_coords") or "")
            if exp:
                if bool(exp.get("ok")) != bool(got) or (exp.get("ok") and exp.get("span") != got):
                    errs.append("tp53 target coords != chain baseline: got=%s expected=%s" %
                                (got, exp.get("span") or exp.get("code")))
                if str(tp.get("coord_status") or "") != str(exp.get("code") or ""):
                    errs.append("coord_status != baseline code: %s vs %s" %
                                (tp.get("coord_status"), exp.get("code")))
            elif entries:
                errs.append("no chain baseline for %s" % t)
        if tp and t == ivm.BUILD_GRCZ12TU and not local_pair:
            master_span = fmt_span(hc["chrom"], hc["start"], hc["end"], hc["strand"]) if hc else ""
            if str(tp.get("target_coords") or "") != master_span:
                errs.append("GRCz12tu coords != master table")
        if tp and t == ivm.BUILD_GRCZ11 and not local_pair and g11:
            if tp.get("coord_basis") == "official_xref" and str(tp.get("target_coords") or "") != g11:
                errs.append("GRCz11 official xref coords mismatch")

        ro = resolve_route(s, t)
        if not ro.get("ok"):
            route_bad.append("%s→%s: %s" % (s, t, ro.get("code")))

        if tp and tp.get("status") == "unique":
            z = identity(tp)["zfin"] or "tp53"
            try:
                back = ivm.convert_batch([z], t, s)
                br = (back.get("results") or [{}])[0]
                rt_total += 1
                if identity_key(identity(br)) == identity_key(identity(tp)):
                    rt_ok += 1
                else:
                    errs.append("round-trip identity changed: %s -> %s" %
                                (identity_key(identity(tp)), identity_key(identity(br))))
            except Exception as e:  # noqa: BLE001
                errs.append("round-trip exception: %s" % e)

        pair_details.append({"source": s, "target": t, "count": res.get("count"),
                             "unique": len(unique), "coord_ok": coord_ok,
                             "identity_basis": binfo.get("identity_basis"), "errors": errs})
        if errs:
            failures.append({"source": s, "target": t, "errors": errs})
        if idx % 20 == 0 or idx == len(combos):
            print("[pair] %d/%d ok=%d fail=%d elapsed=%.0fs rss=%dMB" %
                  (idx, len(combos), idx - len(failures), len(failures),
                   time.time() - t0, rss_mb()), flush=True)

    same = [p for p in pair_details if p["source"] == p["target"]]
    majority = identity_counter.most_common(1)[0][0] if identity_counter else None
    identity_variants = len(identity_counter)
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "elapsed_seconds": round(time.time() - t0, 1),
        "builds": builds,
        "pair_count": len(combos),
        "pairs_ok": len(combos) - len({(f["source"], f["target"]) for f in failures}),
        "pairs_failed": len({(f["source"], f["target"]) for f in failures}),
        "routable_pairs": len(cap.get("routable_pairs") or []),
        "route_bad": route_bad,
        "negative_control_bad": neg_bad,
        "identity_variants": identity_variants,
        "identity_drift": identity_drift,
        "reference_identities": {k: list(v) for k, v in reference.items()},
        "identity_majority": list(majority) if majority else None,
        "identity_distribution": [
            {"identity": list(k), "hits": v} for k, v in identity_counter.most_common(10)
        ],
        "coord_basis": dict(coord_basis),
        "coord_codes": dict(coord_codes),
        "roundtrip": {"total": rt_total, "ok": rt_ok},
        "same_build_pairs": len(same),
        "hub_coords": hc,
        "grcz11_official_coords": g11,
        "chain_baseline": {
            "file": str(BASELINE_JSON),
            "generated_at": baseline.get("generated_at"),
            "entries": len(entries),
            "genes": len(genes),
        },
        "chain_expected": chain_expected,
        "failures": failures,
        "pairs": pair_details,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 批量 ID 互转 · 全版本组合清查报告",
        "",
        "- 生成时间：%s" % result["generated_at"],
        "- 覆盖：%d 个装配 × %d 个装配 = **%d 组**（含 %d 组同版本）" %
        (len(builds), len(builds), len(combos), len(same)),
        "- 通过：**%d / %d**；失败：%d；chain 路由可达：%d 对；往返一致：%d / %d" %
        (result["pairs_ok"], len(combos), result["pairs_failed"],
         result["routable_pairs"], rt_ok, rt_total),
        "- 负例误命中：%d 次；身份变体：%d 种（正常 2：tp53 / sox9b）；身份串位：%d 次" %
        (len(neg_bad), identity_variants, len(identity_drift)),
        "- 坐标基准：`output/id_version_coord_baseline.json`（%d 条真实 chain 换算，%d 个基因），生成于 %s" %
        (len(entries), len(genes), baseline.get("generated_at") or "未生成"),
        "",
        "## 口径",
        "",
        "GRCz11 / GRCz12tu 使用本地基因 ID 对照表；其余 16 个装配没有独立注释表，"
        "输出跨版本稳定标识符（符号 / Ensembl / ZFIN / NCBI Gene / UniProt）"
        "+ 由 UCSC chain 换算的该版本坐标，并在结果中标注 identity_basis。",
        "清查分两阶段：先由独立进程按目标版本做真实 chain 换算（每版算完即释放链内存），"
        "再由主清查复用该基准跑 324 组，避免与线上服务争用 cgroup 内存。",
        "",
        "## 坐标换算来源分布", "", "| 来源 | 次数 |", "| --- | --- |",
    ]
    for k, v in coord_basis.most_common():
        lines.append("| %s | %d |" % (k, v))
    lines += ["", "## 坐标状态 code 分布", "", "| code | 次数 |", "| --- | --- |"]
    for k, v in coord_codes.most_common():
        lines.append("| %s | %d |" % (k, v))
    lines += ["", "## 真实 chain 基准（GRCz12tu tp53 → 各版本）", "",
              "| 目标版本 | 结果 | 坐标 | 跳数 |", "| --- | --- | --- | --- |"]
    for b in builds:
        e = chain_expected.get(b) or {}
        lines.append("| %s | %s | %s | %s |" %
                     (b, "OK" if e.get("ok") else (e.get("code") or "未测"),
                      e.get("span") or "—", " → ".join(e.get("hops") or []) or "—"))
    lines += ["", "## 失败明细", ""]
    if failures:
        for f in failures[:60]:
            lines.append("- **%s → %s**：%s" % (f["source"], f["target"], "；".join(f["errors"][:4])))
        if len(failures) > 60:
            lines.append("- …其余 %d 组见 JSON" % (len(failures) - 60))
    else:
        lines.append("无。所有版本组合均通过。")
    lines += ["", "原始逐组结果：`output/id_version_map_audit.json`。", ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("[done] %s / %s  pairs_ok=%d failed=%d elapsed=%.0fs" %
          (OUT_JSON, OUT_MD, result["pairs_ok"], result["pairs_failed"], time.time() - t0),
          flush=True)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
