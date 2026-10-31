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

# 2026-10-29 UI：12tu／12ab 不作來源；13ab 來源與目標皆暫隱
UI_SOURCE_BUILDS = ("GRCz11", "GRCz10", "Zv9", "Zv8", "Zv7", "Zv6", "Zv5", "Zv4", "Zv3")
UI_TARGET_BUILDS = (
    "GRCz12ab", "GRCz12tu", "GRCz11", "GRCz10",
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
    ("GRCz11", "GRCz10"): "danRer11ToDanRer10.over.chain.gz",
    ("GRCz11", "Zv9"): "danRer11ToDanRer7.over.chain.gz",
    # chainSwap 自 forward 生成（部署腳本寫入 liftover/）
    ("GRCz12tu", "GRCz11"): "GCF_049306965.2ToDanRer11.over.chain.gz",
    ("GRCz12ab", "GRCz11"): "GCA_052040795.1ToDanRer11.over.chain.gz",
}


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
    # GRCz13ab：UI 暫隱；API 亦拒絕（缺 chain／MAF）
    if src == "GRCz13ab" or tgt == "GRCz13ab":
        return {
            "ok": False,
            "code": "unsupported_build",
            "chains": [],
            "hops": [],
            "detail": "GRCz13ab 暂不可选（本机仅有 chrom.sizes／assembly_report；无 over.chain／MAF）",
            "maf": "maf_unavailable",
        }
    # GRCz12tu／12ab：不作来源（产品策略：只作目标）
    if src in {"GRCz12tu", "GRCz12ab"}:
        return {
            "ok": False,
            "code": "unsupported_build",
            "chains": [],
            "hops": [],
            "detail": f"{src} 不可作为来源版本；请选 Zv3–Zv9／GRCz10／GRCz11，目标可选 {src}",
        }
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
        accs = []
        for acc in (parts[0], parts[4], parts[6]):
            acc = (acc or "").strip()
            if acc and acc.lower() != "na":
                acc_to_disp[acc] = chrom
                accs.append(acc)
        if accs and chrom not in disp_to_acc:
            # 優先 RefSeq（GCF 報告裡 parts[6] 常為 NC_／NW_）
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


def _query_chrom_for_chain(chain_name: str, chrom: str) -> str:
    """依 chain 查詢端命名習慣選 chrN 或 RefSeq／GenBank accession。"""
    name = chain_name or ""
    raw = chrom[3:] if chrom.lower().startswith("chr") else chrom
    # 反鏈：查詢端是 GCF／GCA 組裝的 accession
    if name.startswith("GCF_049306965"):
        _, disp_to_acc = _chrom_maps_12tu()
        hit = disp_to_acc.get(raw) or disp_to_acc.get(chrom) or disp_to_acc.get(f"chr{raw}")
        if hit:
            return hit
        # 已是 NC_／NW_ 則原樣
        if chrom.startswith(("NC_", "NW_", "NT_", "CM_")):
            return chrom
    if name.startswith("GCA_052040795") or name.startswith("GCF_052040795"):
        _, disp_to_acc = _chrom_maps_12ab()
        hit = disp_to_acc.get(raw) or disp_to_acc.get(chrom) or disp_to_acc.get(f"chr{raw}")
        if hit:
            return hit
        if chrom.startswith(("NC_", "NW_", "NT_", "CM_", "CA")):
            return chrom
    # danRer* 正向：chrN
    return _ucsc_chrom(chrom)


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
        q_chrom = _query_chrom_for_chain(chain_name, cur_chrom)
        left = _lift_point(chain_name, q_chrom, cur_start, cur_strand)
        right = _lift_point(chain_name, q_chrom, cur_end, cur_strand)
        # 反鏈偶發：用戶已貼 accession 但報告映射失敗時，再試原名字面
        if (not left or not right) and q_chrom != cur_chrom:
            left = left or _lift_point(chain_name, cur_chrom, cur_start, cur_strand)
            right = right or _lift_point(chain_name, cur_chrom, cur_end, cur_strand)
        if (not left or not right) and q_chrom != _ucsc_chrom(cur_chrom):
            left = left or _lift_point(chain_name, _ucsc_chrom(cur_chrom), cur_start, cur_strand)
            right = right or _lift_point(chain_name, _ucsc_chrom(cur_chrom), cur_end, cur_strand)
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
            "Which source assembly is this span on? (e.g. GRCz11 / GRCz10 / Zv9). No default."
            if en
            else "请先说明来源组装版本（例如 GRCz11／GRCz10／Zv9）。系统不设默认来源。"
        )
    if code == "need_target_build":
        return (
            "Which target assembly should I convert to? (e.g. GRCz12tu / GRCz12ab / GRCz13ab). No default."
            if en
            else "请先选择目标组装版本（例如 GRCz12tu／GRCz12ab／GRCz13ab）。系统不设默认目标。"
        )
    if code == "unsupported_build":
        return (
            "This assembly is not supported for conversion."
            if en
            else "该组装版本不支持换算。"
        )
    if code in {"chain_unavailable", "missing_chain"}:
        return (
            f"No UCSC chain route for {source} → {target}; MAF fallback unavailable. "
            "No coordinates were invented."
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
            "Span crosses a chain break; split and retry."
            if en
            else "区间跨了链断裂点，请缩小范围分段换算。"
        )
    return (
        "Interval could not be lifted with UCSC chain; no coordinates invented."
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


def explain_coord_question(question: str) -> dict | None:
    payload = genome_coord_liftover(question)
    if payload is None:
        return None
    lifted = payload["liftover"]
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
        "routable_pairs": pairs_ok,
        "notes": {
            "GRCz13ab": "hidden in UI (source+target); no over.chain/MAF",
            "GRCz12tu": "target only in UI (not offered as source)",
            "GRCz12ab": "target only in UI (not offered as source)",
            "coverage": "UI sources Zv3–Zv9/GRCz10/GRCz11 → targets incl. 12tu/12ab; 13ab hidden; 12tu chain=.2",
        },
    }
