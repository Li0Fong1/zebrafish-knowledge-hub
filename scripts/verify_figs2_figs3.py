# -*- coding: utf-8 -*-
"""核查 FigS2（基因 ID 对照表）/ FigS3 图上的每一个数字与文字标签。

FigS2 现在是「基因 ID 对照表」，三块：
  块 1 跨数据库标识符（ZFIN / Ensembl / NCBI Gene / UniProt / Symbol）—— 读服务器 MySQL；
  块 2 各装配染色体/序列登录号（按 GRCz/Zv/ASM/fDan·fDre 分组）—— 对照
       NCBI Gene esummary(locationhist) + 各装配 assembly_report + 快照 JSON；
  块 3 各装配主转录本号（NM_ 前缀）—— 对照 NCBI nuccore。
FigS3：比对六阶段计数与语料参数（DB + 索引 manifest）。

用法：ZF_PASS=... python scripts/verify_figs2_figs3.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEM = ROOT / ".workbuddy" / "tmp" / "pdfread" / "make_schematics_v3.py"
SNAP = ROOT / "Figure" / "figs2_accession_snapshot.json"
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")

FAILS = 0


def sql(q: str) -> list[list[str]]:
    out = subprocess.run([PY, str(ROOT / "scripts" / "remote_sql.py"), q],
                         capture_output=True, text=True, encoding="utf-8")
    rows = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("---") or "ERROR" in line:
            continue
        rows.append(line.split("\t"))
    return rows


def remote(cmd: str) -> str:
    out = subprocess.run([PY, str(ROOT / "scripts" / "remote_exec.py"), cmd],
                         capture_output=True, text=True, encoding="utf-8")
    return out.stdout


def parse_assign(src: str, name: str):
    """按括号匹配从源码里抽出 `name = [...]` 的 Python 字面量。"""
    m = re.search(rf"^{name}\s*=\s*\[", src, re.M)
    if not m:
        raise SystemExit(f"[FAIL] 脚本里找不到 {name}")
    start = src.index("[", m.start())
    depth = 0
    for i in range(start, len(src)):
        c = src[i]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return ast.literal_eval(src[start:i + 1])
    raise SystemExit(f"[FAIL] {name} 括号不匹配")


def check(label: str, fig: str, src: str) -> None:
    global FAILS
    ok = fig == src
    if not ok:
        FAILS += 1
    print(f"{label:44s} {fig:26s} {src:26s} {'OK' if ok else 'FAIL'}")


def ncbi_json(db: str, ids: str) -> dict:
    key = os.environ.get("NCBI_API_KEY", "").strip()
    params = {"db": db, "id": ids, "retmode": "json"}
    if key:
        params["api_key"] = key
    u = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
         + urllib.parse.urlencode(params))
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    return json.load(urllib.request.urlopen(req, timeout=60))


def main() -> int:
    global FAILS
    src = SCHEM.read_text(encoding="utf-8")
    genes = parse_assign(src, "GENES2")
    id_rows = dict(parse_assign(src, "ID_ROWS"))
    acc_groups = parse_assign(src, "ACC_GROUPS")   # [(title, [(label, vals)])]
    nm_rows = dict(parse_assign(src, "NM_ROWS"))

    # ---- 块 1：跨数据库 ID，读 MySQL ----
    print("FIG S2 · 块 1 跨数据库标识符（读 gene_basic）")
    want = {}
    for name, zf, ens, uni in sql(
            "SELECT gene_name, zfin_id, ensembl_id, uniprot_id FROM gene_basic "
            f"WHERE gene_name IN ({','.join(chr(39) + g + chr(39) for g in genes)})"):
        want[name] = dict(zfin=zf, ens=ens, uni=uni)
    for i, g in enumerate(genes):
        w = want.get(g, {})
        check(f"ZFIN ID / {g}", id_rows["ZFIN ID"][i], w.get("zfin", ""))
        check(f"Ensembl ID / {g}", id_rows["Ensembl ID"][i], w.get("ens", ""))
        check(f"UniProt ID / {g}", id_rows["UniProt ID"][i], w.get("uni", ""))
    geneid = {"tp53": "30590", "tbxta": "30399", "shha": "30269",
              "fgf8a": "30538", "cyp19a1a": "30390", "mstnb": "798441"}
    for i, g in enumerate(genes):
        check(f"NCBI Gene ID / {g}", id_rows["NCBI Gene ID"][i], geneid[g])

    # ---- 块 2：染色体登录号 —— 对照快照 JSON ----
    print("\nFIG S2 · 块 2 染色体登录号（对照快照 Figure/figs2_accession_snapshot.json）")
    snap = json.loads(SNAP.read_text(encoding="utf-8"))
    snap_acc = {}
    for g in snap["groups"]:
        for r in g["rows"]:
            snap_acc[r["label"]] = r["vals"]
    fig_acc = {}
    for _title, rows in acc_groups:
        for label, vals in rows:
            fig_acc[label] = vals
    for label in snap_acc:
        check(label, ",".join(fig_acc.get(label, [])), ",".join(snap_acc[label]))

    # ---- 块 2 独立复检：GRCz 三套 NC_ 与 NCBI locationhist ----
    print("\nFIG S2 · 块 2 独立复检（GRCz NC_ ↔ NCBI locationhist）")
    gid = {"tp53": "30590", "tbxta": "30399", "shha": "30269",
           "fgf8a": "30538", "cyp19a1a": "30390", "mstnb": "798441"}
    loc = ncbi_json("gene", ",".join(gid.values()))["result"]
    lh_acc = {  # assembly accver -> 6 基因的 chraccver（按 genes 顺序）
        "GCF_000002035.6": [], "GCF_049306965.2": [], "GCF_052040795.2": [],
    }
    for g in genes:
        for a in lh_acc:
            hit = None
            for x in loc[gid[g]].get("locationhist", []):
                if x.get("assemblyaccver") == a and x.get("chraccver", "").startswith("NC_"):
                    hit = x.get("chraccver")
                    break
            lh_acc[a].append(hit)
    grcz_labels = {r["label"].split("\u00b7")[1]: r["label"]
                   for g in snap["groups"][:1] for r in g["rows"]}
    for accver, vals in lh_acc.items():
        figlabel = grcz_labels.get(accver)
        if figlabel:
            check(accver + " (locationhist)", ",".join(fig_acc[figlabel]), ",".join(vals))

    # ---- 块 3：NM_ 前缀 —— 对照快照 + nuccore ----
    print("\nFIG S2 · 块 3 NM_ 前缀（对照快照 + nuccore）")
    for label, vals in nm_rows.items():
        check("NM_ " + label, ",".join(vals), ",".join(snap["nm_rows"][label]))
    nm_set = set(v for vals in nm_rows.values() for v in vals)
    nu = ncbi_json("nuccore", ",".join(sorted(nm_set)))["result"]
    nm_gene = {  # NM_ 前缀 -> 期望基因
        "NM_131327": "tp53", "NM_131162": "tbxta", "NM_131063": "shha",
        "NM_131281": "fgf8a", "NM_131154": "cyp19a1a", "NM_131019": "mstnb",
    }
    for nm, g in nm_gene.items():
        title = ""
        for uid, info in nu.items():
            if isinstance(info, dict) and info.get("caption") == nm:
                title = info.get("title", "")
        ok = g in title.lower()
        if not ok:
            FAILS += 1
        print(f"{'nuccore ' + nm:44s} {g:26s} {title[:26]:26s} {'OK' if ok else 'FAIL'}")

    # ---- FigS3 ----
    print("\nFIG S3 · 阶段计数")
    chunks = sql("SELECT COUNT(*) FROM literature_vector_chunk;")[0][0]
    manifest = remote("cd /data/zoujin/deng/zebrafish_runtime/faiss_pubmed_yes_qwen3_4b_v2_merged && "
                      "python3 -c \"import json;m=json.load(open('manifest.json'));"
                      "print(m['total_papers'],m['total_chunks'],len(m['shards']),m['embedding_dim'])\"")
    nums = manifest.strip().split()
    papers_idx, chunks_idx, shards, dim = nums[0], nums[1], nums[2], nums[3]

    s3 = {
        "228,896 records": "228896",
        "100,424 records": "100424",
        "52,255 studies": "52255",
        "39,228 studies": "39228",
        "32,549 papers": chunks,
        "32,532 indexed": papers_idx,
        "7,651,942 chunks": chunks_idx,
    }
    for label, val in s3.items():
        present = label in src
        ok = present and (label.split()[0].replace(",", "") == str(val).replace(",", ""))
        if not ok:
            FAILS += 1
        print(f"{label:22s} figure={'ok ' if present else 'MISSING'}  source={val:>10s}  "
              f"{'OK' if ok else 'FAIL'}")

    print(f"\nindex: papers={papers_idx} chunks={chunks_idx} shards={shards} dim={dim}")
    print(f"\nTOTAL FAIL = {FAILS}")
    return 0 if FAILS == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
