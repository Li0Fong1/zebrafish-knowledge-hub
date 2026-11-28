# -*- coding: utf-8 -*-
"""UCSC chain 座標換算：顯式來源／目標；缺鏈／缺 MAF 明確失敗，不偽造座標。

第 2 期（2026-10-29）相對舊版：
- 由「只→GRCz12tu」改為 **有向成對路由**（可多跳）。
- 可選構建含本機已收錄的 Zv／GRCz（含 GRCz12ab、GRCz13ab）；缺鏈明確失敗。
- 統一失敗碼：need_source_build / need_target_build / unsupported_build /
  chain_unavailable / maf_unavailable / missing_chain / liftover_split /
  liftover_failed / already_on_target_build / liftover_ok。
"""
from __future__ import annotations

import gzip
import json
import os
import re
from collections import deque
from functools import lru_cache
from pathlib import Path

CHAIN_DIR = Path("/data/zoujin/deng/zebrafish_runtime/liftover")
# 與 genome_info／本機序列一致：GRCz12tu = GCF_049306965.2
REPORT_NAME = "GCF_049306965.2_GRCz12tu_assembly_report.txt"
REPORT_12AB_NAME = "GCA_052040795.1_GRCz12ab_assembly_report.txt"
# 第 3 期：不設默認目標。常量僅作文檔／舊別名，不得在 lift／API 裡自動填入。
DEFAULT_TARGET_BUILD = ""
TARGET_BUILD = ""
TOOL_NAME = "coord_liftover"
TOOL_VERSION = "hub_coord_liftover_v3e"

# 產品可選構建（含 Zv 系列較早版本；UI 與路由另有來源／目標白名單）
SELECTABLE_BUILDS = (
    "GRCz13ab",
    "GRCz12ab",
    "GRCz12tu",
    "GRCz11",
    "GRCz10",
    "Zv9",
    "Zv8",
    "Zv7",
    "Zv6",
    "Zv5",
    "Zv4",
    "Zv3",
)

# 2026-11-05：放开 13ab 作来源与目标（复用 12ab 的链，见 EDGE_CHAINS 的 13ab 条目）
# 2026-11-01：放开 12tu／12ab 作来源（反链已重建并实测，见 STRAIN_ASSEMBLIES 的 edges）
UI_SOURCE_BUILDS = (
    "GRCz13ab", "GRCz12tu", "GRCz12ab", "GRCz11", "GRCz10",
    "Zv9", "Zv8", "Zv7", "Zv6", "Zv5", "Zv4", "Zv3",
)
UI_TARGET_BUILDS = (
    "GRCz13ab", "GRCz12ab", "GRCz12tu", "GRCz11", "GRCz10",
    "Zv9", "Zv8", "Zv7", "Zv6", "Zv5", "Zv4", "Zv3",
)

# 預留：目前無產品屏蔽項
BLOCKED_BUILDS: dict[str, str] = {}

BUILD_ALIASES = {
    "grcz13ab": "GRCz13ab",
    "gcf_052040795.2": "GRCz13ab",
    "gca_052040795.2": "GRCz13ab",
    "grcz12tu": "GRCz12tu",
    "grcz12": "GRCz12tu",
    "gcf_049306965.1": "GRCz12tu",
    "gcf_049306965.2": "GRCz12tu",
    "grcz12ab": "GRCz12ab",
    "gcf_052040795.1": "GRCz12ab",
    "gca_052040795.1": "GRCz12ab",
    "grcz11": "GRCz11",
    "danrer11": "GRCz11",
    "zv11": "GRCz11",
    "grcz10": "GRCz10",
    "danrer10": "GRCz10",
    "zv10": "GRCz10",
    "zv9": "Zv9",
    "danrer7": "Zv9",
    "grcz9": "Zv9",
    "zv8": "Zv8",
    "danrer6": "Zv8",
    "zv7": "Zv7",
    "danrer5": "Zv7",
    "zv6": "Zv6",
    "danrer4": "Zv6",
    "zv5": "Zv5",
    "danrer3": "Zv5",
    "zv4": "Zv4",
    "danrer2": "Zv4",
    "zv3": "Zv3",
    "danrer1": "Zv3",
}

# 單跳 chain：有檔才視為可走；多跳由 BFS 組合。
# 2026-10-29：Zv3＝danRer1／Zv4＝danRer2；缺 danRer2ToDanRer1 時以 chainSwap 補。
EDGE_CHAINS: dict[tuple[str, str], str] = {
    ("Zv3", "Zv4"): "danRer1ToDanRer2.over.chain.gz",
    ("Zv4", "Zv3"): "danRer2ToDanRer1.over.chain.gz",  # chainSwap 自 forward
    ("Zv4", "Zv5"): "danRer2ToDanRer3.over.chain.gz",
    ("Zv5", "Zv4"): "danRer3ToDanRer2.over.chain.gz",
    ("Zv5", "Zv6"): "danRer3ToDanRer4.over.chain.gz",
    ("Zv6", "Zv5"): "danRer4ToDanRer3.over.chain.gz",
    ("Zv6", "Zv7"): "danRer4ToDanRer5.over.chain.gz",
    ("Zv7", "Zv6"): "danRer5ToDanRer4.over.chain.gz",
    ("Zv7", "Zv8"): "danRer5ToDanRer6.over.chain.gz",
    ("Zv8", "Zv7"): "danRer6ToDanRer5.over.chain.gz",
    ("Zv8", "Zv9"): "danRer6ToDanRer7.over.chain.gz",
    ("Zv9", "Zv8"): "danRer7ToDanRer6.over.chain.gz",
    ("Zv9", "GRCz11"): "danRer7ToDanRer11.over.chain.gz",
    ("Zv9", "GRCz10"): "danRer7ToDanRer10.over.chain.gz",
    ("GRCz10", "GRCz11"): "danRer10ToDanRer11.over.chain.gz",
    ("GRCz10", "Zv9"): "danRer10ToDanRer7.over.chain.gz",
    ("GRCz11", "GRCz12tu"): "danRer11ToGCF_049306965.2.over.chain.gz",
    ("GRCz11", "GRCz12ab"): "danRer11ToGCA_052040795.1.over.chain.gz",
    # 2026-11-05：GRCz13ab（GCF_052040795.2 / GCA_052040795.2）与 GRCz12ab（GCA_052040795.1）
    # 是同一 M-AB 单倍型装配的相邻版本，故复用 12ab 的链。注意：13ab 对 chr19 做了重新注释
    # （NC_141039.1→.2、CM124825.1→.2，坐标整体偏移约 5 kb），因此 chr19 的换算有约 5 kb 的
    # 系统误差；其余 24 条染色体两版坐标一致，换算精确。12ab 的 chr19 换算不受影响。
    # 正向走 GCF 链（目标端 NC_141xxx 命名，与 13ab 的 RefSeq 登录号一致）；
    # 反向走 GCA 链（源端 CM124xxx 命名，13ab 报告 GenBank 列同样收录）。
    ("GRCz11", "GRCz13ab"): "danRer11ToGCF_052040795.1.over.chain.gz",
    ("GRCz13ab", "GRCz11"): "GCA_052040795.1ToDanRer11.over.chain.gz",
    ("GRCz11", "GRCz10"): "danRer11ToDanRer10.over.chain.gz",
    ("GRCz11", "Zv9"): "danRer11ToDanRer7.over.chain.gz",
    # chainSwap 自 forward 生成（部署腳本寫入 liftover/）
    ("GRCz12tu", "GRCz11"): "GCF_049306965.2ToDanRer11.over.chain.gz",
    ("GRCz12ab", "GRCz11"): "GCA_052040795.1ToDanRer11.over.chain.gz",
}


# ---------------------------------------------------------------------------
# 2026-10-31：NCBI 品系装配（Danio rerio, taxid 7955）接入 Liftover
# 筛选口径（用户给定，两条同时满足）：
#   ① 高质量：contig N50 ≥ 1 Mb（如 GRCz13ab / GRCz12tu 的 59 Mb 级，ASM1840007v1 的 1.2 Mb）
#   ② 覆盖不同品系：Tubingen / AB / T5D / M-AB / SAT / Tu …
# 清单取自 NCBI datasets genome dataset_report（2026-10-31 快照，全库 24 个装配，
# contig N50 ≥ 1 Mb 且 status=current 的 10 个，去掉 GRCz11/GRCz12tu 本身与本机无链者）。
# 每个装配的 assembly_report.txt 已放在 CHAIN_DIR，用来做「5」↔「CP068739.1」互转——
# 品系装配的 FASTA 头是 CP_/CM_ accession，不映射就永远对不上链。
STRAIN_ASSEMBLIES: dict[str, dict] = {
    "ASM1840007v1": {
        "accession": "GCA_018400075.1", "strain": "T5D", "contig_n50": 1203134,
        "report": "GCA_018400075.1_ASM1840007v1_assembly_report.txt",
        "edges": {
            ("ASM1840007v1", "GRCz11"): "GCA_018400075.1ToDanRer11.over.chain.gz",
            ("GRCz11", "ASM1840007v1"): "danRer11ToGCA_018400075.1.over.chain.gz",
        },
    },
    "ASM3317019v3": {
        "accession": "GCA_033170195.3", "strain": "Tubingen", "contig_n50": 59265136,
        "report": "GCA_033170195.3_ASM3317019v3_assembly_report.txt",
        "edges": {
            ("ASM3317019v3", "GRCz12tu"): "GCA_033170195.3ToGCF_049306965.1.over.chain.gz",
            # 2026-11-01 反链：由正向链逐 block 重建（原文件块尺寸损坏，pyliftover 拒载）
            ("GRCz12tu", "ASM3317019v3"): "GCF_049306965.1ToGCA_033170195.3.over.chain.gz",
        },
    },
    "ASM2018471v1": {
        "accession": "GCA_020184715.1", "strain": "AB", "contig_n50": 21088021,
        "report": "GCA_020184715.1_ASM2018471v1_assembly_report.txt",
        "edges": {
            ("ASM2018471v1", "GRCz12tu"): "GCA_020184715.1ToGCF_049306965.1.over.chain.gz",
            ("GRCz12tu", "ASM2018471v1"): "GCF_049306965.1ToGCA_020184715.1.over.chain.gz",
        },
    },
    "fDanRer4.1": {
        "accession": "GCA_944039275.1", "strain": "", "contig_n50": 6927137,
        "report": "GCA_944039275.1_fDanRer4.1_assembly_report.txt",
        "edges": {
            ("fDanRer4.1", "GRCz12tu"): "GCA_944039275.1ToGCF_049306965.1.over.chain.gz",
            ("GRCz12tu", "fDanRer4.1"): "GCF_049306965.1ToGCA_944039275.1.over.chain.gz",
        },
    },
    "fDreABH1.1": {
        "accession": "GCA_903684865.1", "strain": "AB", "contig_n50": 4531818,
        "report": "GCA_903684865.1_fDreABH1.1_assembly_report.txt",
        "edges": {
            ("fDreABH1.1", "GRCz12tu"): "GCA_903684865.1ToGCF_049306965.1.over.chain.gz",
            ("GRCz12tu", "fDreABH1.1"): "GCF_049306965.1ToGCA_903684865.1.over.chain.gz",
        },
    },
    "fDreTuH1.2": {
        "accession": "GCA_903684855.2", "strain": "Tu", "contig_n50": 3603372,
        "report": "GCA_903684855.2_fDreTuH1.2_assembly_report.txt",
        "edges": {
            ("fDreTuH1.2", "GRCz12tu"): "GCA_903684855.2ToGCF_049306965.1.over.chain.gz",
            ("GRCz12tu", "fDreTuH1.2"): "GCF_049306965.1ToGCA_903684855.2.over.chain.gz",
        },
    },
}

STRAIN_NAMES = tuple(STRAIN_ASSEMBLIES)
STRAIN_BY_ACCESSION = {m["accession"]: (n, m) for n, m in STRAIN_ASSEMBLIES.items()}

SELECTABLE_BUILDS = SELECTABLE_BUILDS + STRAIN_NAMES
UI_SOURCE_BUILDS = UI_SOURCE_BUILDS + STRAIN_NAMES
UI_TARGET_BUILDS = UI_TARGET_BUILDS + STRAIN_NAMES

for _name, _meta in STRAIN_ASSEMBLIES.items():
    BUILD_ALIASES[_name.lower()] = _name
    BUILD_ALIASES[_meta["accession"].lower()] = _name
    BUILD_ALIASES[_meta["accession"].replace(".", "").lower()] = _name
    EDGE_CHAINS.update(_meta["edges"])


def normalize_build(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    return BUILD_ALIASES.get(raw.lower(), raw)


def list_edge_status() -> list[dict]:
    rows = []
    for (src, tgt), chain in sorted(EDGE_CHAINS.items()):
        path = CHAIN_DIR / chain
        rows.append({
            "source": src,
            "target": tgt,
            "chain": chain,
            "present": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else 0,
        })
    return rows


def resolve_route(source: str, target: str) -> dict:
    """回傳 {ok, code, chains, hops}；不執行換算。"""
    src = normalize_build(source)
    tgt = normalize_build(target)
    if not src:
        return {"ok": False, "code": "need_source_build", "chains": [], "hops": []}
    if not tgt:
        return {"ok": False, "code": "need_target_build", "chains": [], "hops": []}
    if src in BLOCKED_BUILDS or tgt in BLOCKED_BUILDS:
        return {"ok": False, "code": "unsupported_build", "chains": [], "hops": []}
    if src == tgt:
        return {"ok": True, "code": "already_on_target_build", "chains": [], "hops": [src]}
    # 2026-11-05：GRCz13ab 已放开（复用 12ab 链 + 13ab 报告），不再拒绝。
    # 2026-11-01：放开 12tu／12ab 作来源——反链（GCF_049306965.[12]To…、
    # GCA_052040795.1ToDanRer11）均已入库并逐链实测，BFS 可直接走。
    if tgt and tgt not in UI_TARGET_BUILDS and tgt not in SELECTABLE_BUILDS:
        return {"ok": False, "code": "unsupported_build", "chains": [], "hops": []}
    # BFS on present edges only
    present = {
        (a, b): ch
        for (a, b), ch in EDGE_CHAINS.items()
        if (CHAIN_DIR / ch).is_file()
    }
    if not present:
        return {"ok": False, "code": "missing_chain", "chains": [], "hops": []}
    q = deque([(src, [])])
    seen = {src}
    while q:
        node, path = q.popleft()
        for (a, b), ch in present.items():
            if a != node or b in seen:
                continue
            new_path = path + [((a, b), ch)]
            if b == tgt:
                return {
                    "ok": True,
                    "code": "route_ok",
                    "chains": [c for _, c in new_path],
                    "hops": [src] + [bb for (_, bb), _ in new_path],
                }
            seen.add(b)
            q.append((b, new_path))
    # 無 chain 路徑；MAF 亦無
    return {
        "ok": False,
        "code": "chain_unavailable",
        "chains": [],
        "hops": [],
        "maf": "maf_unavailable",
    }


# 舊 ROUTES：僅「→ GRCz12tu」視圖，供舊 import 相容
ROUTES = {
    "GRCz11": ["danRer11ToGCF_049306965.2.over.chain.gz"],
    "GRCz10": [
        "danRer10ToDanRer11.over.chain.gz",
        "danRer11ToGCF_049306965.2.over.chain.gz",
    ],
    "Zv9": [
        "danRer7ToDanRer11.over.chain.gz",
        "danRer11ToGCF_049306965.2.over.chain.gz",
    ],
    "Zv8": [
        "danRer6ToDanRer7.over.chain.gz",
        "danRer7ToDanRer11.over.chain.gz",
        "danRer11ToGCF_049306965.2.over.chain.gz",
    ],
    "Zv7": [
        "danRer5ToDanRer6.over.chain.gz",
        "danRer6ToDanRer7.over.chain.gz",
        "danRer7ToDanRer11.over.chain.gz",
        "danRer11ToGCF_049306965.2.over.chain.gz",
    ],
    "Zv6": [
        "danRer4ToDanRer5.over.chain.gz",
        "danRer5ToDanRer6.over.chain.gz",
        "danRer6ToDanRer7.over.chain.gz",
        "danRer7ToDanRer11.over.chain.gz",
        "danRer11ToGCF_049306965.2.over.chain.gz",
    ],
    "Zv5": [
        "danRer3ToDanRer4.over.chain.gz",
        "danRer4ToDanRer5.over.chain.gz",
        "danRer5ToDanRer6.over.chain.gz",
        "danRer6ToDanRer7.over.chain.gz",
        "danRer7ToDanRer11.over.chain.gz",
        "danRer11ToGCF_049306965.2.over.chain.gz",
    ],
    "Zv4": [
        "danRer2ToDanRer3.over.chain.gz",
        "danRer3ToDanRer4.over.chain.gz",
        "danRer4ToDanRer5.over.chain.gz",
        "danRer5ToDanRer6.over.chain.gz",
        "danRer6ToDanRer7.over.chain.gz",
        "danRer7ToDanRer11.over.chain.gz",
        "danRer11ToGCF_049306965.2.over.chain.gz",
    ],
    "Zv3": [
        "danRer1ToDanRer2.over.chain.gz",
        "danRer2ToDanRer3.over.chain.gz",
        "danRer3ToDanRer4.over.chain.gz",
        "danRer4ToDanRer5.over.chain.gz",
        "danRer5ToDanRer6.over.chain.gz",
        "danRer6ToDanRer7.over.chain.gz",
        "danRer7ToDanRer11.over.chain.gz",
        "danRer11ToGCF_049306965.2.over.chain.gz",
    ],
}

_BUILD_RE = re.compile(
    r"(GRCz13ab|GRCz12tu|GRCz12ab|GRCz12|GRCz11|GRCz10|Zv9|Zv8|Zv7|Zv6|Zv5|Zv4|Zv3|"
    r"danRer11|danRer10|danRer7|danRer6|danRer5|danRer4|danRer3|danRer2|danRer1|"
    r"GCF_052040795\.[12]|GCA_052040795\.[12]|GCF_049306965\.[12])",
    re.I,
)
_REGION_RE = re.compile(
    r"(?:chr)?([0-9]{1,2}|MT|M)\s*:\s*([0-9][0-9,]*)\s*(?:[-–—－−~～至到一]|\.{2,3})\s*([0-9][0-9,]*)",
    re.I,
)
_TARGET_RE = re.compile(
    r"(?:转(?:到|成|为|换到|换为|换至)|换算(?:到|成|为)|转换(?:到|成|为)|"
    r"lift(?:ed)?\s*(?:to|into)|convert(?:ed)?\s*to|to)\s*"
    r"(GRCz13ab|GRCz12tu|GRCz12ab|GRCz12|GRCz11|GRCz10|Zv9|Zv8|Zv7|Zv6|Zv5|Zv4|Zv3|"
    r"danRer11|danRer10|danRer7|danRer6|danRer5|danRer4|danRer3|danRer2|danRer1)",
    re.I,
)


def _normalize_region_text(text: str) -> str:
    raw = text or ""
    trans = str.maketrans("０１２３４５６７８９：．，　", "0123456789:., ")
    raw = raw.translate(trans)
    raw = raw.replace("\u00a0", " ").replace("\u3000", " ")
    return raw


def parse_region_query(text: str) -> dict | None:
    raw = _normalize_region_text(text)
    region = _REGION_RE.search(raw.replace(" ", "") if ":" in raw else raw)
    if region is None:
        region = _REGION_RE.search(raw)
    if region is None:
        return None
    builds = []
    for match in _BUILD_RE.finditer(raw):
        name = normalize_build(match.group(1))
        if name and name not in builds:
            builds.append(name)
    tgt = ""
    tm = _TARGET_RE.search(raw)
    if tm:
        tgt = normalize_build(tm.group(1))
    # 未寫「轉到／to」時不猜目標；來源取句中第一個組裝名
    source = builds[0] if builds else ""
    if tgt and source == tgt and len(builds) > 1:
        source = next((n for n in builds if n != tgt), source)
    if not tgt:
        tgt = ""
    chrom = region.group(1).upper()
    if chrom == "M":
        chrom = "MT"
    return {
        "source_build": source,
        "target_build": tgt,
        "chromosome": chrom,
        "start": int(region.group(2).replace(",", "")),
        "end": int(region.group(3).replace(",", "")),
    }


def _ucsc_chrom(chrom: str) -> str:
    raw = chrom[3:] if chrom.lower().startswith("chr") else chrom
    if raw.upper() in {"MT", "M"}:
        return "chrM"
    if raw.isdigit():
        return f"chr{int(raw)}"
    return chrom


def _load_assembly_report_maps(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """回傳 (accession→display, display→主 accession)。"""
    acc_to_disp: dict[str, str] = {}
    disp_to_acc: dict[str, str] = {}
    if not path.is_file():
        return acc_to_disp, disp_to_acc
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7 or parts[1] != "assembled-molecule":
            continue
        mol = parts[2].strip()
        chrom = "MT" if mol.upper() in {"MT", "M"} else mol
        if not (chrom.isdigit() or chrom == "MT"):
            # 12ab 等可能用 scaffold 名；仍收 accession↔自身，方便原樣查詢
            chrom = mol or chrom
        # 2026-10-31：只收 GenBank / RefSeq 兩欄的 accession。
        # 舊版把 parts[0]（Sequence-Name，如 "chr1"）也塞进 accs，GenBank 報告（GCA_*）
        # 沒有 NC_ 時 fallback 就取到了 "chr1" 本身，導致 "5" → "5" 的自映射，
        # 品系裝配的 chain 查詢端永遠拿不到 CP068739.1 —— 正向換算全部 liftover_failed。
        accs = []
        for acc in (parts[4], parts[6]):
            acc = (acc or "").strip()
            if acc and acc.lower() != "na":
                acc_to_disp[acc] = chrom
                accs.append(acc)
        if accs and chrom not in disp_to_acc:
            # 優先 RefSeq（GCF 報告裡 parts[6] 常為 NC_／NW_），沒有則用 GenBank（GCA 報告是 CM_／CP_）
            prefer = next((a for a in accs if a.startswith(("NC_", "NW_", "NT_"))), accs[0])
            disp_to_acc[chrom] = prefer
            disp_to_acc[f"chr{chrom}" if chrom.isdigit() else chrom] = prefer
    return acc_to_disp, disp_to_acc


@lru_cache(maxsize=1)
def _chrom_maps_12tu() -> tuple[dict[str, str], dict[str, str]]:
    return _load_assembly_report_maps(CHAIN_DIR / REPORT_NAME)


@lru_cache(maxsize=1)
def _chrom_maps_12ab() -> tuple[dict[str, str], dict[str, str]]:
    return _load_assembly_report_maps(CHAIN_DIR / REPORT_12AB_NAME)


@lru_cache(maxsize=16)
def _chrom_maps_strain(accession: str) -> tuple[dict[str, str], dict[str, str]]:
    """品系装配的 (accession→显示名, 显示名→accession)；找不到报告就返回空表。"""
    meta = STRAIN_BY_ACCESSION.get(accession)
    if not meta:
        return {}, {}
    return _load_assembly_report_maps(CHAIN_DIR / meta[1]["report"])


@lru_cache(maxsize=1)
def _chrom_map() -> dict[str, str]:
    """accession／chr* → 顯示用染色體名（數字或 MT）。"""
    out = {f"chr{i}": str(i) for i in range(1, 26)}
    out["chrM"] = "MT"
    out["chrMT"] = "MT"
    acc_to_disp, _ = _chrom_maps_12tu()
    out.update(acc_to_disp)
    acc_ab, _ = _chrom_maps_12ab()
    # 12ab 的 CM_ 等不覆蓋已有 12tu NC_ 映射
    for k, v in acc_ab.items():
        out.setdefault(k, v)
    # 品系装配：CP_/CM_/NC_ → 数字染色体名（反向换算时目标端就是这些编号）
    for _acc in STRAIN_BY_ACCESSION:
        acc_to_disp, _ = _chrom_maps_strain(_acc)
        for k, v in acc_to_disp.items():
            out.setdefault(k, v)
    return out


def _display_chrom(name: str) -> str:
    mapped = _chrom_map().get(name)
    if mapped:
        return mapped
    raw = name[3:] if name.lower().startswith("chr") else name
    if raw.isdigit():
        return str(int(raw))
    if raw.upper() in {"M", "MT"}:
        return "MT"
    return name


_ACC_FAMILY_RE = re.compile(r"([A-Z]+)_?\d")


def _acc_family(name: str) -> str:
    """accession 的字母族：CM111179.1→CM，NC_133176.1→NC，CP068738.1→CP。"""
    m = _ACC_FAMILY_RE.match(name or "")
    return m.group(1).rstrip("_") if m else ""


@lru_cache(maxsize=64)
def _chain_query_family(chain_name: str) -> str:
    """读 chain 头几条，判断查询端命名族：'chr' 或 accession 字母族（NC/CM/CP…）。

    2026-11-01：同一批链的命名并不统一——Tubingen 的 .1 链 12tu 侧是 chr25，
    fDanRer4.1 的 .1 链 12tu 侧却是 NC_133182.1；12ab 链用 CM124807.1。
    所以必须按文件实际内容判定，不能靠链名猜。

    2026-11-01 晚：实测确认 pyliftover 只以链头 **tName（第 3 列）** 为查询入口
    （qName 侧查询一律返回空），且全部 34 条链都是 tName=源装配、qName=目标装配。
    故命名族必须取 tName，取 qName 会把「目标端」的命名习惯误当成查询端。
    """
    path = CHAIN_DIR / (chain_name or "")
    if not path.is_file():
        return ""
    fams: dict[str, int] = {}
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if line.startswith("chain"):
                    parts = line.split()
                    if len(parts) >= 8:
                        nm = parts[2]
                        key = "chr" if nm.lower().startswith("chr") else _acc_family(nm)
                        if key:
                            fams[key] = fams.get(key, 0) + 1
                if i >= 4000:
                    break
    except Exception:
        return ""
    if not fams:
        return ""
    return max(fams.items(), key=lambda kv: kv[1])[0]


@lru_cache(maxsize=32)
def _display_to_accessions(report_name: str) -> dict[str, list[str]]:
    """显示名 → 该染色体的全部 accession（GenBank 与 RefSeq 都留，供按族挑选）。"""
    path = CHAIN_DIR / (report_name or "")
    out: dict[str, list[str]] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7 or parts[1] != "assembled-molecule":
            continue
        mol = parts[2].strip()
        chrom = "MT" if mol.upper() in {"MT", "M"} else mol
        if not (chrom.isdigit() or chrom == "MT"):
            chrom = mol or chrom
        accs = []
        for acc in (parts[4], parts[6]):
            acc = (acc or "").strip()
            if acc and acc.lower() != "na" and acc not in accs:
                accs.append(acc)
        if not accs:
            continue
        for key in {chrom, f"chr{chrom}" if chrom.isdigit() else chrom}:
            bucket = out.setdefault(key, [])
            for a in accs:
                if a not in bucket:
                    bucket.append(a)
    return out


def _query_report_for_chain(chain_name: str) -> str:
    """这条链「查询端」装配的 assembly_report（用于数字染色体↔accession）。"""
    name = chain_name or ""
    m = re.match(r"^(GC[AF]_\d+\.\d+)To", name)
    if m and m.group(1) in STRAIN_BY_ACCESSION:
        return STRAIN_BY_ACCESSION[m.group(1)][1]["report"]
    if name.startswith("GCA_052040795") or name.startswith("GCF_052040795"):
        return REPORT_12AB_NAME
    if name.startswith(("GCF_049306965", "GCA_049306965")):
        # 只有 12tu 在查询端（链名以它开头）时，查询端才是 12tu 的序列命名。
        # 2026-11-02：原写作 "GCF_049306965" in name，把 danRer11ToGCF_049306965.2
        # 这类「12tu 在目标端」的链也算进来，导致 GRCz11 的 chr5 被贴上 12tu 的
        # CM111183.1 —— 标错登录号比不标更糟（用户会拿它去 NCBI 搜到别的序列）。
        return REPORT_NAME
    return ""


@lru_cache(maxsize=64)
def _chain_tnames(chain_name: str) -> tuple[str, ...]:
    """链文件里实际出现的 tName（= pyliftover 的查询侧 = 源装配命名）。

    2026-11-01：与其按链名猜命名族，不如直接用文件里真实存在的名字。
    MT 这类小染色体尤其容易踩坑（.1 链用 CM_、.2 报告给 NC_，同一显示名对应
    两个 accession 家族），用真实 tName 反查最稳。

    2026-11-12：**加落盘缓存**。取这些名字要把整个 gz 链文件解压扫一遍
    （GRCz11+GRCz12tu 共 34.7 MB），实测首次 5.0 s / 6.8 s。而「列染色体清单」
    这个下拉框接口每次都要它 —— 进程一重启，第一个打开 Liftover 页的人就要
    白等 5~7 s，期间还霸占着单进程 uvicorn 的执行流，把其他请求一起拖慢。
    链文件内容不会变，把扫出来的名字按「文件名+大小+mtime」落盘后，
    后续冷启动只需读一个小 JSON（毫秒级）。
    """
    path = CHAIN_DIR / (chain_name or "")
    if not path.is_file():
        return ()

    try:
        st = path.stat()
    except OSError:
        return _scan_chain_tnames(path)

    cache = _tnames_cache_path(chain_name, st)
    if cache is not None and cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return tuple(str(x) for x in data)
        except Exception:
            pass  # 缓存坏了就当没有，重扫一遍覆盖

    names = _scan_chain_tnames(path)
    if cache is not None:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps(list(names), ensure_ascii=False), encoding="utf-8")
            powv = tmp.replace(cache)
            del powv
        except Exception:
            pass  # 目录只读等情况：退化为纯内存缓存，不影响功能

    if os.environ.get("ZF_VERBOSE_CHAIN_CACHE"):
        import logging

        logging.getLogger(__name__).info(
            "[liftover] 扫描链名 %s → %d 个（已写缓存）", chain_name, len(names)
        )
    return names


def _tnames_cache_path(chain_name: str, st) -> "Path | None":
    """链名清单的落盘缓存路径；目录不可写时由调用方静默跳过。"""
    if not chain_name:
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", chain_name)
    # 大小与 mtime 进文件名：链文件一换就是另一个文件，旧缓存自然不会再被读到，
    # 也就不会读到过期名单。
    return CHAIN_DIR / ".cache" / f"{safe}.{st.st_size}.{int(st.st_mtime)}.tnames.json"


def _scan_chain_tnames(path: Path) -> tuple[str, ...]:
    """真正去读链文件取 tName（慢路径，只在缓存缺失时走）。"""
    names: list[str] = []
    seen: set[str] = set()
    try:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("chain"):
                    parts = line.split()
                    if len(parts) >= 3:
                        nm = parts[2]
                        if nm and nm not in seen:
                            seen.add(nm)
                            names.append(nm)
    except Exception:
        return tuple(names)
    return tuple(names)


@lru_cache(maxsize=64)
def _chain_tnames_by_display(chain_name: str) -> dict[str, tuple[str, ...]]:
    """显示名（1..25／MT／contig 名）→ 该链文件里真实存在的 tName 元组。"""
    out: dict[str, list[str]] = {}
    for tn in _chain_tnames(chain_name):
        try:
            key = _display_chrom(tn)
        except Exception:
            continue
        out.setdefault(key, []).append(tn)
    return {k: tuple(v) for k, v in out.items()}


def _query_chrom_candidates(chain_name: str, chrom: str) -> list[str]:
    """按可能性从高到低给出查询名候选（首个即旧接口的返回值）。"""
    name = chain_name or ""
    raw = chrom[3:] if chrom.lower().startswith("chr") else chrom
    fam = _chain_query_family(name)
    cands: list[str] = []

    def add(x: str) -> None:
        if x and x not in cands:
            cands.append(x)

    # 0) 最权威：链文件里真实存在的 tName 中，显示名與用户输入一致的那些
    want = _display_chrom(chrom)
    real = _chain_tnames_by_display(name).get(want, [])
    if fam and fam != "chr":
        for tn in real:
            if _acc_family(tn) == fam:
                add(tn)
    for tn in real:
        add(tn)

    accs: list[str] = []
    report = _query_report_for_chain(name)
    if report:
        table = _display_to_accessions(report)
        accs = list(table.get(raw) or table.get(chrom) or [])
    if fam == "chr":
        add(_ucsc_chrom(chrom))
    for a in accs:  # 与链头实际字母族一致的 accession 优先
        if fam and fam != "chr" and _acc_family(a) == fam:
            add(a)
    for a in accs:
        add(a)
    if chrom.startswith(("NC_", "NW_", "NT_", "CM", "CP", "CA", "OX")):
        add(chrom)
    add(_ucsc_chrom(chrom))
    add(chrom)
    return cands


def _query_chrom_for_chain(chain_name: str, chrom: str) -> str:
    """依 chain 查詢端命名習慣選 chrN 或 accession（取最可能的候选）。"""
    cands = _query_chrom_candidates(chain_name, chrom)
    return cands[0] if cands else _ucsc_chrom(chrom)


@lru_cache(maxsize=16)
def _loader(chain_name: str):
    from pyliftover import LiftOver

    return LiftOver(str(CHAIN_DIR / chain_name))


def _lift_point(chain_name: str, chrom: str, pos: int, strand: str):
    try:
        hits = _loader(chain_name).convert_coordinate(chrom, pos - 1, strand)
    except Exception:
        return None
    if not hits:
        return None
    top = hits[0]
    return top[0], int(top[1]) + 1, top[2] if top[2] in {"+", "-"} else strand


def lift_region(
    source_build: str,
    chrom: str,
    start: int,
    end: int,
    strand: str = "+",
    target_build: str = "",
) -> dict:
    src = normalize_build(source_build)
    tgt = normalize_build(target_build)
    source_span = f"{chrom}:{start}-{end}"
    base = {
        "source_build": src,
        "target_build": tgt,
        "source_span": source_span,
        "target_span": None,
        "chromosome": None,
        "start": None,
        "end": None,
        "strand": None,
        "chain": None,
        "ok": False,
        "route_hops": [],
    }
    if start <= 0 or end < start:
        base["code"] = "liftover_failed"
        return base
    if not src:
        base["code"] = "need_source_build"
        return base
    if not tgt:
        base["code"] = "need_target_build"
        return base

    route = resolve_route(src, tgt)
    base["route_hops"] = route.get("hops") or []
    if route.get("code") == "already_on_target_build":
        base.update({
            "ok": True,
            "code": "already_on_target_build",
            "target_span": source_span,
            "chromosome": chrom,
            "start": start,
            "end": end,
            "strand": strand if strand in {"+", "-"} else "+",
            "chain": "",
        })
        return base
    if not route.get("ok"):
        code = route.get("code") or "chain_unavailable"
        base["code"] = code
        if route.get("maf") == "maf_unavailable" and code == "chain_unavailable":
            base["maf"] = "maf_unavailable"
        if route.get("detail"):
            base["detail"] = route["detail"]
        return base

    chains = route["chains"]
    missing = [name for name in chains if not (CHAIN_DIR / name).is_file()]
    if missing:
        base["code"] = "missing_chain"
        base["chain"] = ",".join(missing)
        return base

    cur_chrom = chrom
    cur_start, cur_end = start, end
    cur_strand = strand if strand in {"+", "-"} else "+"
    for chain_name in chains:
        # 2026-11-01：链的查询端命名族因文件而异（chrN／NC_／CM／CP…），
        # 按候选顺序逐个试，取第一个「两端同链」的结果。
        left = right = None
        partial = None
        for q_chrom in _query_chrom_candidates(chain_name, cur_chrom):
            cand_left = _lift_point(chain_name, q_chrom, cur_start, cur_strand)
            cand_right = _lift_point(chain_name, q_chrom, cur_end, cur_strand)
            if cand_left and cand_right and cand_left[0] == cand_right[0]:
                left, right = cand_left, cand_right
                break
            if partial is None and (cand_left or cand_right):
                partial = (cand_left, cand_right)
        if left is None:
            left, right = partial if partial else (None, None)
        if not left or not right or left[0] != right[0]:
            base["code"] = "liftover_split" if left and right else "liftover_failed"
            base["chain"] = chain_name
            return base
        cur_chrom = left[0]
        cur_strand = left[2]
        cur_start, cur_end = (left[1], right[1]) if left[1] <= right[1] else (right[1], left[1])
    old_len = end - start + 1
    new_len = cur_end - cur_start + 1
    if new_len <= 0 or new_len > old_len * 5 or old_len > new_len * 5:
        base["code"] = "liftover_failed"
        base["chain"] = chains[-1]
        return base
    display = _display_chrom(cur_chrom)
    base.update({
        "ok": True,
        "code": "liftover_ok",
        "target_span": f"{display}:{cur_start}-{cur_end}",
        "chromosome": display,
        "start": cur_start,
        "end": cur_end,
        "strand": cur_strand,
        "chain": ">".join(chains),
    })
    return base


def _answer_en(question: str = "") -> bool:
    try:
        from zebrafish_prompt import answer_is_english

        return bool(answer_is_english(question))
    except Exception:
        return False


def _error_message(code: str, source: str, target: str, en: bool) -> str:
    if code == "need_source_build":
        return (
            "Which source assembly is this region on? (e.g. GRCz11, GRCz10, Zv9). No default."
            if en
            else "请先说明来源组装版本（例如 GRCz11／GRCz10／Zv9）。系统不设默认来源。"
        )
    if code == "need_target_build":
        return (
            "Which target assembly should this be lifted over to? (e.g. GRCz12tu, GRCz12ab, GRCz13ab). No default."
            if en
            else "请先选择目标组装版本（例如 GRCz12tu／GRCz12ab／GRCz13ab）。系统不设默认目标。"
        )
    if code == "unsupported_build":
        return (
            "This assembly is not supported for liftover."
            if en
            else "该组装版本不支持换算。"
        )
    if code in {"chain_unavailable", "missing_chain"}:
        return (
            f"No UCSC chain route from {source} to {target}; no MAF fallback is available. "
            "No approximate coordinates were returned."
            if en
            else f"没有 {source} → {target} 的 UCSC chain 路由；亦无 MAF 备援。系统没有伪造坐标。"
        )
    if code == "maf_unavailable":
        return (
            "MAF fallback is not installed."
            if en
            else "本机没有可用 MAF，无法做无 chain 时的辅助换算。"
        )
    if code == "liftover_split":
        return (
            "This region crosses a chain breakpoint; split it and try again."
            if en
            else "区间跨了链断裂点，请缩小范围分段换算。"
        )
    return (
        "No alignment block covers this region in the UCSC chain; no approximate coordinates were returned."
        if en
        else "无法用 UCSC chain 换算该区间。系统没有伪造坐标。"
    )


def genome_coord_liftover(text: str) -> dict | None:
    parsed = parse_region_query(text)
    if not parsed or not parsed.get("source_build"):
        return None
    target = parsed.get("target_build") or ""
    source = parsed["source_build"]
    en = _answer_en(text)
    if not target:
        lifted = {
            "ok": False,
            "code": "need_target_build",
            "source_build": source,
            "target_build": "",
            "chromosome": parsed["chromosome"],
            "source_span": f"{parsed['chromosome']}:{parsed['start']}-{parsed['end']}",
            "target_span": "",
            "chain": "",
        }
    else:
        lifted = lift_region(
            source, parsed["chromosome"], parsed["start"], parsed["end"], target_build=target
        )
    ok = bool(lifted.get("ok"))
    code = lifted.get("code")
    coordinates = {name: None for name in SELECTABLE_BUILDS}
    coordinates[source] = {
        "genome_build": source,
        "chromosome": parsed["chromosome"],
        "start": parsed["start"],
        "end": parsed["end"],
        "strand": "+",
        "source_table": "query",
    }
    if ok:
        coordinates[target] = {
            "genome_build": target,
            "chromosome": lifted.get("chromosome"),
            "start": lifted.get("start"),
            "end": lifted.get("end"),
            "strand": lifted.get("strand"),
            "source_table": lifted.get("chain") or "ucsc_chain",
        }
    return {
        "ok": ok,
        "status": "success" if ok else "failed",
        "query": text,
        "zfin_id": None,
        "symbol": None,
        "source_build": source,
        "target_build": target,
        "genome_build": (target if ok else source),
        "coord_state": None,
        "design_bundle": None,
        "coordinates": coordinates,
        "liftover": lifted,
        "provenance": {"method": "ucsc_chain", "chain": lifted.get("chain")},
        "error": None if ok else {
            "code": code,
            "message": _error_message(code or "", source, target, en),
        },
    }


def explain_coord_question(question: str, lang: str | None = None) -> dict | None:
    """坐标换算说明。

    ``lang`` 为界面语言（en/zh）；未传时沿用「按提问语言推断」的旧行为。
    """
    payload = genome_coord_liftover(question)
    if payload is None:
        return None
    lifted = payload["liftover"]
    if lang and str(lang).lower().startswith(("en", "zh")):
        en = str(lang).lower().startswith("en")
    else:
        en = _answer_en(question)
    code = lifted.get("code")
    if code == "already_on_target_build":
        answer = (
            f"Already on {lifted.get('target_build')} {lifted.get('target_span')}."
            if en
            else f"这段区间已在 {lifted.get('target_build')} {lifted.get('target_span')}。"
        )
    elif code == "need_target_build":
        answer = _error_message(code, lifted.get("source_build") or "", "", en)
    elif lifted.get("ok"):
        answer = (
            f"UCSC chain: {lifted.get('source_build')} {lifted.get('source_span')} → "
            f"{lifted.get('target_build')} {lifted.get('target_span')}."
            if en
            else (
                f"已用 UCSC chain 把 {lifted.get('source_build')} {lifted.get('source_span')} "
                f"换算到 {lifted.get('target_build')} {lifted.get('target_span')}。"
            )
        )
    else:
        answer = _error_message(
            code or "liftover_failed",
            lifted.get("source_build") or "",
            lifted.get("target_build") or "",
            en,
        )
    return {
        "answer": answer,
        "intent": "coord_liftover",
        "task_type": "Coordinate conversion" if en else "坐标换算",
        "tools_invoked": [TOOL_NAME],
        "sequence_blocked": True,
        "skip_enrich": True,
        "liftover": lifted,
    }


def agent_coord_liftover(
    q: str = "",
    source_build: str = "",
    chromosome: str = "",
    start: int | None = None,
    end: int | None = None,
    strand: str = "+",
    target_build: str = "",
) -> dict:
    provenance = {
        "method": "ucsc_chain",
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
    }

    def _envelope(status: str, code: str, data: dict | None = None, error: dict | None = None):
        return {
            "tool": TOOL_NAME,
            "status": status,
            "code": code,
            "data": data or {},
            "provenance": provenance,
            "sequence_blocked": True,
            "error": error,
        }

    sb = normalize_build(source_build)
    tb = normalize_build(target_build)
    chrom = (chromosome or "").strip()
    text = (q or "").strip()

    if sb and chrom and start is not None and end is not None:
        if not tb:
            return _envelope(
                "missing",
                "need_target_build",
                data={"source_build": sb, "chromosome": chrom, "start": start, "end": end},
                error={"code": "need_target_build", "message": "请提供 target_build（不设默认）。"},
            )
        lifted = lift_region(sb, chrom, int(start), int(end), strand or "+", target_build=tb)
        provenance["chain"] = lifted.get("chain")
        provenance["target_build"] = tb
        coordinates = {name: None for name in SELECTABLE_BUILDS}
        coordinates[sb] = {
            "genome_build": sb,
            "chromosome": chrom,
            "start": int(start),
            "end": int(end),
            "strand": strand if strand in {"+", "-"} else "+",
            "source_table": "query",
        }
        if lifted.get("ok"):
            coordinates[tb] = {
                "genome_build": tb,
                "chromosome": lifted.get("chromosome"),
                "start": lifted.get("start"),
                "end": lifted.get("end"),
                "strand": lifted.get("strand"),
                "source_table": lifted.get("chain") or "ucsc_chain",
            }
        data = {
            "query": text or f"{sb} {chrom}:{start}-{end} → {tb}",
            "source_build": sb,
            "target_build": tb,
            "genome_build": tb if lifted.get("ok") else sb,
            "liftover": lifted,
            "coordinates": coordinates,
        }
        code = lifted.get("code") or ("liftover_ok" if lifted.get("ok") else "liftover_failed")
        status = "ok" if lifted.get("ok") else "error"
        err = None if lifted.get("ok") else {
            "code": code,
            "message": _error_message(code, sb, tb, False),
        }
        return _envelope(status, code, data=data, error=err)

    if not text:
        return _envelope(
            "missing",
            "empty_query",
            error={"code": "empty_query", "message": "请提供 q，或 source_build+chromosome+start+end+target_build。"},
        )

    parsed = parse_region_query(text)
    if parsed is not None and not parsed.get("source_build"):
        return _envelope(
            "missing",
            "need_source_build",
            data={"query": text, "parsed": parsed},
            error={"code": "need_source_build", "message": "请先声明来源组装版本。"},
        )
    if parsed is not None and not parsed.get("target_build") and not tb:
        return _envelope(
            "missing",
            "need_target_build",
            data={"query": text, "parsed": parsed},
            error={"code": "need_target_build", "message": "请声明目标组装版本（例如转到 GRCz12tu）。"},
        )
    if parsed is not None and tb and not parsed.get("target_build"):
        parsed["target_build"] = tb
        # re-lift via structured fields
        return agent_coord_liftover(
            q=text,
            source_build=parsed["source_build"],
            chromosome=parsed["chromosome"],
            start=parsed["start"],
            end=parsed["end"],
            strand=strand or "+",
            target_build=tb,
        )

    payload = genome_coord_liftover(text)
    if payload is None:
        return _envelope(
            "missing",
            "need_source_build",
            data={"query": text},
            error={"code": "need_source_build", "message": "无法解析来源版本与区间。"},
        )
    provenance["chain"] = (payload.get("liftover") or {}).get("chain")
    provenance["target_build"] = payload.get("target_build")
    code = (payload.get("liftover") or {}).get("code") or payload.get("status") or "liftover_failed"
    status = "ok" if payload.get("ok") else "error"
    return _envelope(
        status,
        code,
        data={
            "query": payload.get("query"),
            "source_build": payload.get("source_build"),
            "target_build": payload.get("target_build"),
            "genome_build": payload.get("genome_build"),
            "liftover": payload.get("liftover"),
            "coordinates": payload.get("coordinates"),
        },
        error=payload.get("error"),
    )


def agent_coord_parse(q: str = "") -> dict:
    text = (q or "").strip()
    if not text:
        return {
            "ok": False,
            "status": "missing",
            "code": "empty_query",
            "parsed": None,
            "error": {"code": "empty_query", "message": "请提供 q。"},
        }
    parsed = parse_region_query(text)
    if parsed is None:
        return {
            "ok": False,
            "status": "missing",
            "code": "parse_failed",
            "parsed": None,
            "query": text,
            "error": {"code": "parse_failed", "message": "无法识别染色体区间。"},
        }
    if not parsed.get("source_build"):
        return {
            "ok": False,
            "status": "missing",
            "code": "need_source_build",
            "parsed": parsed,
            "query": text,
            "error": {"code": "need_source_build", "message": "已识别区间，但缺少来源组装版本。"},
        }
    return {
        "ok": True,
        "status": "ok",
        "code": "parsed",
        "query": text,
        "parsed": parsed,
    }


def list_supported_pairs() -> dict:
    """給前端／文檔：可走邊與缺邊摘要（依 UI 來源／目標白名單）。"""
    edges = list_edge_status()
    pairs_ok = []
    for src in UI_SOURCE_BUILDS:
        for tgt in UI_TARGET_BUILDS:
            if src == tgt:
                continue
            r = resolve_route(src, tgt)
            if r.get("ok") and r.get("code") != "already_on_target_build":
                pairs_ok.append({
                    "source": src,
                    "target": tgt,
                    "chains": r.get("chains"),
                    "hops": r.get("hops"),
                })
    return {
        "selectable_builds": list(SELECTABLE_BUILDS),
        "ui_source_builds": list(UI_SOURCE_BUILDS),
        "ui_target_builds": list(UI_TARGET_BUILDS),
        "blocked_builds": list(BLOCKED_BUILDS.keys()),
        "edges": edges,
        "strain_assemblies": [
            {"name": n, "accession": m["accession"], "strain": m["strain"],
             "contig_n50": m["contig_n50"],
             "edges": [{"source": a, "target": b, "chain": c} for (a, b), c in m["edges"].items()]}
            for n, m in STRAIN_ASSEMBLIES.items()
        ],
        "routable_pairs": pairs_ok,
        "notes": {
            "chromosomes": "GET /api/genome/chromosomes?build=<build> 给前端染色体下拉；值取自链头 tName",
            "GRCz13ab": "source+target (2026-11-05 起放开；复用 12ab 链 + GCA_052040795.2 报告)",
            "GRCz12tu": "source+target (2026-11-01 起可作来源；反链已重建并实测)",
            "GRCz12ab": "source+target (2026-11-01 起可作来源)",
            "coverage": "sources Zv3–Zv9/GRCz10/GRCz11/GRCz12tu/GRCz12ab/GRCz13ab → 全部目标；12tu chain=.2（12tu↔品系走 .1 链）",
        },
    }


# ---------------------------------------------------------------------------
# 2026-11-02：染色体下拉
# ---------------------------------------------------------------------------
# 需求（用户）：染色体号随所选基因组版本变化，不该让人手打。
# 口径：只列「该装配作为来源时，链文件里真实存在 tName」的主染色体（1–25 / MT）。
#   真名取自链头，选中就一定对得上链 —— 不按 1–25 硬编码，避免 fDanRer4.1 这类
#   只覆盖部分染色体的装配给出选不通的选项。
#   某装配没有出边链／链文件不在本机时，退回 1–25 + MT（前端仍可用，换算时才报缺链）。

@lru_cache(maxsize=32)
def _source_chains_for(build: str) -> tuple[str, ...]:
    """該裝配作為來源（= 查詢側）時可用的鏈，排序固定以便結果穩定。"""
    return tuple(sorted(c for (s, _t), c in EDGE_CHAINS.items() if s == build))


@lru_cache(maxsize=32)
def _chromosomes_of_build(build: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """(显示名, 链头真名元组) 序列，已按数值排序；MT 置末。"""
    seen: dict[str, set[str]] = {}
    for chain in _source_chains_for(build):
        try:
            table = _chain_tnames_by_display(chain)
        except Exception:
            continue
        for disp, tnames in table.items():
            key = _display_chrom(disp)
            if key.upper() in {"M", "MT"}:
                key = "MT"
            elif not key.isdigit():
                # chrUn_*、contig 等非主染色体不放进下拉
                continue
            bucket = seen.setdefault(key, set())
            bucket.update(tnames)
    keys = sorted((k for k in seen if k.isdigit()), key=int)
    if "MT" in seen:
        keys.append("MT")
    return tuple((k, tuple(sorted(seen[k]))) for k in keys)


def _dedup(*items: str) -> list[str]:
    """按顺序去重（保持原序），空串丢掉。"""
    out: list[str] = []
    for x in items:
        x = (x or "").strip()
        if x and x not in out:
            out.append(x)
    return out


_CHROM_SCHEME_NOTES = {
    "genbank": (
        "本版按 GenBank 登录号命名染色体（如 {ex}）；同一编号在不同装配下是不同序列。",
        "Named by GenBank accession (e.g. {ex}); the same number is a different "
        "sequence in another assembly.",
    ),
    "refseq": (
        "本版按 RefSeq 登录号命名染色体（如 {ex}）；同一编号在不同装配下是不同序列。",
        "Named by RefSeq accession (e.g. {ex}); the same number is a different "
        "sequence in another assembly.",
    ),
    "mixed": (
        "本版多条链混用 UCSC chr* 与登录号（如 {ex}），下拉按链头实际写法列出。",
        "Chains mix UCSC chr* names and accessions (e.g. {ex}); the list follows "
        "the chain headers.",
    ),
    "ucsc": (
        "本版沿用 UCSC 的 chr1–chr25 命名；链头本身就是染色体名，无额外登录号。",
        "Uses the UCSC chr1–chr25 naming; the chain headers already carry these "
        "names, so no accession is shown.",
    ),
    "unknown": (
        "该装配暂无可用链或 assembly report，下面只是通用清单，换算仍需链。",
        "No chain or assembly report yet, so this is a generic list; a liftover "
        "still needs a chain.",
    ),
}


@lru_cache(maxsize=32)
def _chrom_details_of_build(build: str) -> dict[str, dict[str, str]]:
    """显示名（1..25／MT）→ 该装配自己的序列命名。

    取自 NCBI assembly report 的三列：
      Sequence-Name      该装配的官方序列名（fDanRer4.1 是 SUPER_5、
                         ASM2018471v1 是 Chromosome5、fDreABH1.1 就是 chr5）
      Assigned-Molecule  同源染色体编号，用来归一到 1..25
      GenBank/RefSeq     该装配真正唯一的序列标识（CM124811.1 / OX063294.1 …）

    为什么要这层：所有斑马鱼装配的「5 号染色体」是同一段同源染色体的不同测序版本，
    编号天然一样，序列完全不同。只给 chr5 看不出这是哪个装配的 chr5，
    能把它们区分开的只有登录号。
    """
    out: dict[str, dict[str, str]] = {}
    for chain in _source_chains_for(build):
        report = _query_report_for_chain(chain)
        if not report:
            continue
        path = CHAIN_DIR / report
        if not path.is_file():
            continue
        fam = _chain_query_family(chain)
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7 or parts[1] != "assembled-molecule":
                continue
            official = parts[0].strip()
            mol = parts[2].strip()
            key = "MT" if mol.upper() in {"MT", "M"} else mol
            if not (key.isdigit() or key == "MT"):
                continue
            accs = [a.strip() for a in (parts[4], parts[6])
                    if a and a.strip() and a.strip().lower() != "na"]
            if not accs:
                continue
            # 报告里 GenBank 与 RefSeq 两栏常同时有值：按链头实际用的字母族挑，
            # 免得链头写 CM124811.1 却标成 NC_ 号，对不上用户手里的坐标文件。
            acc = accs[0]
            for a in accs:
                if fam and fam != "chr" and _acc_family(a) == fam:
                    acc = a
                    break
            if acc.startswith(("NC_", "NW_", "NT_")):
                scheme = "refseq"
            elif acc.startswith(("CM", "CP", "OX", "LR", "CA", "AB", "FN")):
                scheme = "genbank"
            else:
                scheme = "other"
            out.setdefault(key, {"official": official, "accession": acc,
                                 "scheme": scheme})
    return out


def list_build_chromosomes(build: str = "") -> dict:
    """某装配可作为来源的染色体清单（供前端下拉）。

    返回 value（提交值，如 "5" / "MT"）与 label（显示名，如 "chr5" / "chrMT"）。
    """
    b = normalize_build(build)
    if not b:
        return {
            "ok": False, "code": "need_source_build",
            "message_zh": "请先选择来源版本，再取染色体列表。",
            "message_en": "Select a source assembly first, then load its chromosomes.",
            "chromosomes": [],
        }
    if b not in SELECTABLE_BUILDS:
        return {
            "ok": False, "code": "unsupported_build", "build": b,
            "message_zh": f"「{b}」未收录，无法列出染色体。",
            "message_en": f"“{b}” is not a supported assembly.",
            "chromosomes": [],
        }

    rows = list(_chromosomes_of_build(b))
    source = "chain"
    if not rows:
        rows = [(str(i), ()) for i in range(1, 26)] + [("MT", ())]
        source = "default"

    details = _chrom_details_of_build(b)
    items: list[dict] = []
    for name, tnames in rows:
        base = "chrMT" if name == "MT" else f"chr{name}"
        d = details.get(name) or {}
        acc = d.get("accession") or ""
        official = d.get("official") or ""
        # 链头本来就叫 chr5 的装配（GRCz11 / Zv 系列 / fDreABH1.1）不必再贴一遍名字；
        # 链头是 CM124811.1 这类登录号的，必须贴出来，否则 25 个 chr5 长得一模一样。
        show = bool(acc) and acc.lower() not in {base.lower(), name.lower()}
        items.append({
            "value": name,
            "label": f"{base} · {acc}" if show else base,
            "short": base,
            "accession": acc,
            "official": official,
            "names": list(tnames),   # 链头里对得上的写法（排查用）
            # official 常见两种冗余写法：与 base 相同（"chr5"）、或就是裸编号（"5"）。
            # 两种都去掉，只留真正带信息的（Chromosome5 / SUPER_5）。
            "title": " · ".join(_dedup(
                base,
                "" if official in {name, base} else official,
                acc,
            )),
        })

    counts: dict[str, int] = {}
    for d in details.values():
        sc = d.get("scheme") or ""
        if sc:
            counts[sc] = counts.get(sc, 0) + 1
    total_d = sum(counts.values())
    if source != "chain":
        scheme = "unknown"
    elif not counts:
        scheme = "ucsc"
    else:
        top, n_top = max(counts.items(), key=lambda kv: kv[1])
        # 主流命名占比够高就按主流说（12ab 是 25 条 CM 号 + MT 的 1 条 NC_ 号，
        # 整体仍是 GenBank 登录号体系），否则才说混用。
        scheme = top if n_top >= total_d * 0.8 else "mixed"
    example = next((i["accession"] for i in items if i["accession"]), "")

    note_zh, note_en = _CHROM_SCHEME_NOTES[scheme]
    return {
        "ok": True,
        "build": b,
        "source": source,   # chain = 取自链头真名；default = 无链时退回 1–25 + MT
        "naming": {
            "scheme": scheme,
            "example": example,
            "note_zh": note_zh.format(ex=example or "—"),
            "note_en": note_en.format(ex=example or "n/a"),
        },
        "total": len(items),
        "chromosomes": items,
    }
