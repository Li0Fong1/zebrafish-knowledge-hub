# -*- coding: utf-8 -*-

"""

全量数据导出：MySQL 全部表 + 文献目录 + WoS RIS + RAG 知识库 + 参考序列/FAISS/BLAST + 元数据。



用法：

  python scripts/export_full_data_bundle.py

  python -m scripts.export_full_data_bundle --out 项目阶段/05_数据与库/归档/data_export/my_full_bundle

  python -m scripts.export_full_data_bundle --zip

"""

from __future__ import annotations



import csv

import hashlib

import json

import os

import shutil

import sys

import zipfile

from datetime import datetime

from pathlib import Path

from typing import Dict, List, Optional, Tuple



ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:

    sys.path.insert(0, str(ROOT))



from core.entry import bootstrap_app



bootstrap_app()



import argparse



from common_utils import safe_print

from config import (

    ENSEMBL_RELEASE,

    FAISS_INDEX_DIR,

    KNOWLEDGE_EXTRA_DIR,

    KNOWLEDGE_PATH,

    KNOWLEDGE_UPLOAD_DIR,

    NCBI_ASSEMBLY_ACCESSION,

    NCBI_REFERENCE_ASSEMBLY,

    REFERENCE_ASSEMBLY,

    TAXON_ID,

    _BASE_DIR,

)

from mysql_client import MySQLClient



CATALOG_JSONL = os.path.join(_BASE_DIR, "data", "literature", "literature_catalog.jsonl")

WOS_DIR = os.path.join(_BASE_DIR, "data", "wos_import")

LITERATURE_DIR = os.path.join(_BASE_DIR, "data", "literature")

ENSEMBL_META_DIR = os.path.join(_BASE_DIR, "data", "ensembl")

CLEANING_SUBDIR = "06_cleaning"



REFERENCE_FILES = [

    ("data/reference_cdna.fasta", "reference_cdna.fasta"),

    ("data/reference_genome.fasta", "reference_genome.fasta"),

    ("data/reference.fasta", "reference.fasta"),

]

ENSEMBL_REFERENCE_FILES = [

    ("data/ensembl/E-ERAD-475-tpms.tsv", "ensembl/E-ERAD-475-tpms.tsv"),

    ("data/ensembl/E-ERAD-475-tpms-markers.tsv", "ensembl/E-ERAD-475-tpms-markers.tsv"),

]

# 与 reference_genome.fasta 内容相同，仅 manifest 记录，不重复复制

ENSEMBL_CDNA_DUPLICATE = "data/ensembl/Danio_rerio.GRCz11.cdna.all.fa"





def _client() -> MySQLClient:

    return MySQLClient()





def _list_mysql_tables() -> List[str]:

    r = _client().execute_sql(

        "SELECT TABLE_NAME AS t FROM information_schema.TABLES "

        "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME",

        max_rows=500,

    )

    if not r.get("success"):

        raise RuntimeError(r.get("error") or "无法列出 MySQL 表")

    return [row["t"] for row in (r.get("data") or []) if row.get("t")]





def _export_table_csv(table: str, path: str) -> int:

    r = _client().execute_sql(f"SELECT * FROM `{table}`", max_rows=50_000_000)

    if not r.get("success"):

        raise RuntimeError(f"{table}: {r.get('error')}")

    rows = r.get("data") or []

    os.makedirs(os.path.dirname(path), exist_ok=True)

    if not rows:

        Path(path).write_text("", encoding="utf-8-sig")

        return 0

    with open(path, "w", encoding="utf-8-sig", newline="") as f:

        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))

        w.writeheader()

        w.writerows(rows)

    return len(rows)





def _copy_tree(src: str, dst: str, *, patterns: Tuple[str, ...] = ("*",)) -> Tuple[int, int]:

    """复制目录或单文件，返回 (文件数, 总字节)。"""

    if not os.path.exists(src):

        return 0, 0

    files = 0

    total = 0

    if os.path.isfile(src):

        os.makedirs(os.path.dirname(dst), exist_ok=True)

        shutil.copy2(src, dst)

        return 1, os.path.getsize(dst)

    for root, _dirs, fnames in os.walk(src):

        rel = os.path.relpath(root, src)

        out_root = os.path.join(dst, rel) if rel != "." else dst

        os.makedirs(out_root, exist_ok=True)

        for fn in fnames:

            s = os.path.join(root, fn)

            d = os.path.join(out_root, fn)

            shutil.copy2(s, d)

            files += 1

            total += os.path.getsize(d)

    return files, total





def _jsonl_to_csv(jsonl_path: str, csv_path: str) -> int:

    if not os.path.isfile(jsonl_path):

        return 0

    rows: List[dict] = []

    with open(jsonl_path, encoding="utf-8") as f:

        for line in f:

            line = line.strip()

            if not line:

                continue

            try:

                rows.append(json.loads(line))

            except json.JSONDecodeError:

                continue

    if not rows:

        Path(csv_path).write_text("", encoding="utf-8-sig")

        return 0

    keys: List[str] = []

    seen = set()

    for row in rows[:500]:

        for k in row:

            if k not in seen:

                seen.add(k)

                keys.append(k)

    for row in rows[500:]:

        for k in row:

            if k not in seen:

                seen.add(k)

                keys.append(k)

    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:

        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")

        w.writeheader()

        for row in rows:

            w.writerow({k: row.get(k, "") for k in keys})

    return len(rows)





def _dir_size(path: str) -> int:

    if os.path.isfile(path):

        return os.path.getsize(path)

    total = 0

    for root, _d, files in os.walk(path):

        for fn in files:

            try:

                total += os.path.getsize(os.path.join(root, fn))

            except OSError:

                pass

    return total





def _file_md5(path: str) -> Optional[str]:

    if not os.path.isfile(path):

        return None

    h = hashlib.md5()

    with open(path, "rb") as f:

        while True:

            chunk = f.read(65536)

            if not chunk:

                break

            h.update(chunk)

    return h.hexdigest()





def _resolve_faiss_dir() -> Optional[str]:

    """优先使用含 index.faiss 的目录（项目内或 FAISS_INDEX_DIR）。"""

    candidates = [

        os.path.join(_BASE_DIR, "data", "faiss_index"),

        FAISS_INDEX_DIR,

    ]

    seen = set()

    for d in candidates:

        if not d or d in seen:

            continue

        seen.add(d)

        if os.path.isfile(os.path.join(d, "index.faiss")):

            return d

    for d in candidates:

        if d and os.path.isdir(d):

            return d

    return None





def _resolve_blast_dir() -> Optional[str]:

    """BLAST 索引目录（data/blast 或 AppData/ZebrafishGeneQA/blast）。"""

    try:

        from database.seq_toolkit import BLAST_DIR

    except Exception:

        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")

        BLAST_DIR = os.path.join(base, "ZebrafishGeneQA", "blast")

    local = os.path.join(_BASE_DIR, "data", "blast")

    for d in (local, BLAST_DIR):

        if os.path.isfile(os.path.join(d, "cdna_db.nin")):

            return d

    return None





def _copy_reference_data(ref_dir: str, manifest: Dict) -> None:

    """复制参考序列、FAISS、NCBI 缓存、BLAST 索引等大文件。"""

    ref_manifest: Dict = {}

    safe_print("\n[5/7] 参考数据（FASTA / FAISS / NCBI / BLAST）…")



    for rel, name in REFERENCE_FILES:

        src = os.path.join(_BASE_DIR, rel.replace("/", os.sep))

        if not os.path.isfile(src):

            ref_manifest[name] = {"status": "missing"}

            continue

        dst = os.path.join(ref_dir, name)

        os.makedirs(os.path.dirname(dst), exist_ok=True)

        shutil.copy2(src, dst)

        ref_manifest[name] = {"bytes": os.path.getsize(dst), "path": f"07_reference_data/{name}"}

        safe_print(f"  {name}: {os.path.getsize(dst) // (1024 * 1024)} MB")



    dup_src = os.path.join(_BASE_DIR, ENSEMBL_CDNA_DUPLICATE.replace("/", os.sep))

    genome_src = os.path.join(_BASE_DIR, "data", "reference_genome.fasta")

    if os.path.isfile(dup_src):

        dup_md5 = _file_md5(dup_src)

        genome_md5 = _file_md5(genome_src)

        if genome_md5 and dup_md5 == genome_md5:

            ref_manifest["Danio_rerio.GRCz11.cdna.all.fa"] = {

                "status": "skipped_duplicate",

                "same_as": "reference_genome.fasta",

                "md5": dup_md5,

                "bytes_on_disk": os.path.getsize(dup_src),

            }

            safe_print("  Danio_rerio.GRCz11.cdna.all.fa: 与 reference_genome.fasta 相同，跳过重复")

        else:

            dst = os.path.join(ref_dir, "ensembl", "Danio_rerio.GRCz11.cdna.all.fa")

            os.makedirs(os.path.dirname(dst), exist_ok=True)

            shutil.copy2(dup_src, dst)

            ref_manifest["Danio_rerio.GRCz11.cdna.all.fa"] = {

                "bytes": os.path.getsize(dst),

                "path": "07_reference_data/ensembl/Danio_rerio.GRCz11.cdna.all.fa",

            }

            safe_print(f"  cdna.all.fa: {os.path.getsize(dst) // (1024 * 1024)} MB")



    for rel, name in ENSEMBL_REFERENCE_FILES:

        src = os.path.join(_BASE_DIR, rel.replace("/", os.sep))

        if not os.path.isfile(src):

            continue

        dst = os.path.join(ref_dir, name.replace("/", os.sep))

        os.makedirs(os.path.dirname(dst), exist_ok=True)

        shutil.copy2(src, dst)

        ref_manifest[name] = {"bytes": os.path.getsize(dst), "path": f"07_reference_data/{name}"}

        safe_print(f"  {os.path.basename(name)}: {os.path.getsize(dst) // (1024 * 1024)} MB")



    ncbi_src = os.path.join(_BASE_DIR, "data", "ncbi_cache")

    if os.path.isdir(ncbi_src):

        n, sz = _copy_tree(ncbi_src, os.path.join(ref_dir, "ncbi_cache"))

        ref_manifest["ncbi_cache"] = {"files": n, "bytes": sz, "path": "07_reference_data/ncbi_cache"}

        safe_print(f"  ncbi_cache: {n} 文件 ({sz // (1024 * 1024)} MB)")



    faiss_src = _resolve_faiss_dir()

    if faiss_src:

        n, sz = _copy_tree(faiss_src, os.path.join(ref_dir, "faiss_index"))

        ref_manifest["faiss_index"] = {

            "files": n,

            "bytes": sz,

            "source": faiss_src,

            "path": "07_reference_data/faiss_index",

        }

        safe_print(f"  faiss_index: {n} 文件 ({sz // (1024 * 1024)} MB) ← {faiss_src}")

    else:

        ref_manifest["faiss_index"] = {"status": "missing"}



    blast_src = _resolve_blast_dir()

    if blast_src:

        n, sz = _copy_tree(blast_src, os.path.join(ref_dir, "blast_db"))

        ref_manifest["blast_db"] = {

            "files": n,

            "bytes": sz,

            "source": blast_src,

            "path": "07_reference_data/blast_db",

        }

        safe_print(f"  blast_db: {n} 文件 ({sz // (1024 * 1024)} MB) ← {blast_src}")

    else:

        ref_manifest["blast_db"] = {"status": "missing", "hint": "运行 scripts/setup_blast.py 可构建"}



    manifest["reference_data"] = ref_manifest





def _embed_cleaning_export(out_dir: str, manifest: Dict) -> None:

    """内嵌生成人工清洗专用包（非复制旧目录）。"""

    import importlib.util



    cleaning_dir = os.path.join(out_dir, CLEANING_SUBDIR)

    safe_print("\n[6/7] 人工清洗专用数据 …")

    mod_path = os.path.join(_BASE_DIR, "scripts", "export_gene_data_for_cleaning.py")

    spec = importlib.util.spec_from_file_location("export_gene_data_for_cleaning", mod_path)

    if spec is None or spec.loader is None:

        manifest["cleaning"] = {"status": "error", "hint": "无法加载 export_gene_data_for_cleaning.py"}

        safe_print("  [失败] 无法加载清洗导出模块")

        return

    mod = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(mod)

    stats = mod.export_all(cleaning_dir)

    manifest["cleaning"] = {

        "path": CLEANING_SUBDIR,

        "stats": stats,

        "bytes": _dir_size(cleaning_dir),

    }

    safe_print(f"  清洗包已写入 {CLEANING_SUBDIR}/ ({_dir_size(cleaning_dir) // (1024 * 1024)} MB)")





def _zip_bundle(out_dir: str) -> str:

    """将导出目录打包为 zip（deflate，适合跨平台分发）。"""

    zip_path = out_dir.rstrip(os.sep) + ".zip"

    safe_print(f"\n[打包] 正在创建 {zip_path} …")

    base = os.path.dirname(out_dir)

    folder = os.path.basename(out_dir)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:

        for root, _dirs, files in os.walk(out_dir):

            for fn in files:

                abs_p = os.path.join(root, fn)

                arc = os.path.join(folder, os.path.relpath(abs_p, out_dir))

                zf.write(abs_p, arc)

    sz = os.path.getsize(zip_path)

    safe_print(f"  zip 完成: {sz // (1024 * 1024)} MB")

    return zip_path





def export_full_bundle(out_dir: str, *, make_zip: bool = False) -> Dict:

    os.makedirs(out_dir, exist_ok=True)

    mysql_dir = os.path.join(out_dir, "01_mysql")

    lit_dir = os.path.join(out_dir, "02_literature")

    rag_dir = os.path.join(out_dir, "03_rag_knowledge")

    meta_dir = os.path.join(out_dir, "04_import_metadata")

    stats_dir = os.path.join(out_dir, "05_manifest")

    ref_dir = os.path.join(out_dir, "07_reference_data")

    for d in (mysql_dir, lit_dir, rag_dir, meta_dir, stats_dir, ref_dir):

        os.makedirs(d, exist_ok=True)



    manifest: Dict = {

        "exported_at": datetime.now().isoformat(timespec="seconds"),

        "mysql_tables": {},

        "files": {},

        "reference_data": {},

    }

    safe_print(f"全量导出 → {out_dir}")



    safe_print("\n[1/7] MySQL 全部表 …")

    for table in _list_mysql_tables():

        safe_print(f"  {table} …")

        path = os.path.join(mysql_dir, f"{table}.csv")

        try:

            n = _export_table_csv(table, path)

            manifest["mysql_tables"][table] = {"rows": n, "path": f"01_mysql/{table}.csv"}

        except Exception as e:

            safe_print(f"    [跳过] {table}: {e}")

            manifest["mysql_tables"][table] = {"error": str(e)}



    safe_print("\n[2/7] 文献数据 …")

    if os.path.isfile(CATALOG_JSONL):

        dst_jsonl = os.path.join(lit_dir, "literature_catalog.jsonl")

        shutil.copy2(CATALOG_JSONL, dst_jsonl)

        n_cat = _jsonl_to_csv(CATALOG_JSONL, os.path.join(lit_dir, "literature_catalog.csv"))

        manifest["files"]["literature_catalog"] = {"lines": n_cat, "jsonl": True, "csv": True}

        safe_print(f"  literature_catalog: {n_cat:,} 篇")

    _copy_tree(LITERATURE_DIR, os.path.join(lit_dir, "literature_meta"))

    if os.path.isdir(WOS_DIR):

        n, sz = _copy_tree(WOS_DIR, os.path.join(lit_dir, "wos_ris"))

        manifest["files"]["wos_ris"] = {"files": n, "bytes": sz}

        safe_print(f"  WoS RIS: {n} 个文件 ({sz // (1024 * 1024)} MB)")



    safe_print("\n[3/7] RAG 知识库 …")

    if os.path.isfile(KNOWLEDGE_PATH):

        shutil.copy2(KNOWLEDGE_PATH, os.path.join(rag_dir, "knowledge.txt"))

    if os.path.isdir(KNOWLEDGE_EXTRA_DIR):

        n, sz = _copy_tree(KNOWLEDGE_EXTRA_DIR, os.path.join(rag_dir, "knowledge.d"))

        manifest["files"]["knowledge.d"] = {"files": n, "bytes": sz}

        safe_print(f"  knowledge.d: {n} 文件 ({sz // (1024 * 1024)} MB)")

    if os.path.isdir(KNOWLEDGE_UPLOAD_DIR):

        n, sz = _copy_tree(KNOWLEDGE_UPLOAD_DIR, os.path.join(rag_dir, "uploads"))

        manifest["files"]["uploads"] = {"files": n, "bytes": sz}



    safe_print("\n[4/7] 导入元数据 …")

    meta_items = [

        ("data/ensembl/gene_catalog.json", "gene_catalog.json"),

        ("data/ensembl/enrich_progress.json", "enrich_progress.json"),

        ("data/ensembl/literature_progress.json", "literature_progress.json"),

        ("data/ensembl/literature_epmc_cursor.txt", "literature_epmc_cursor.txt"),

        ("data/literature/gene_link_progress.json", "gene_link_progress.json"),

        ("data/kegg_pathways.json", "kegg_pathways.json"),

        ("data/gene_list.txt", "gene_list.txt"),

        ("data/ensembl/E-ERAD-475-tpms.tsv", "E-ERAD-475-tpms.tsv"),

        ("data/ensembl/E-ERAD-475-tpms-markers.tsv", "E-ERAD-475-tpms-markers.tsv"),

    ]

    for rel, name in meta_items:

        src = os.path.join(_BASE_DIR, rel.replace("/", os.sep))

        if os.path.isfile(src):

            shutil.copy2(src, os.path.join(meta_dir, name))

            manifest["files"][name] = {"bytes": os.path.getsize(src)}

    sql_dst = os.path.join(meta_dir, "sql_schema")

    os.makedirs(sql_dst, exist_ok=True)

    sql_src = os.path.join(_BASE_DIR, "sql")

    if os.path.isdir(sql_src):

        for fn in os.listdir(sql_src):

            if fn.endswith(".sql"):

                shutil.copy2(os.path.join(sql_src, fn), os.path.join(sql_dst, fn))



    _copy_reference_data(ref_dir, manifest)

    _embed_cleaning_export(out_dir, manifest)



    safe_print("\n[7/7] 生成清单 …")

    total_bytes = _dir_size(out_dir)

    manifest["bundle_bytes"] = total_bytes

    readme = os.path.join(out_dir, "00_README.md")

    cleaning_readme = os.path.join(out_dir, CLEANING_SUBDIR, "00_数据目录.md")

    with open(readme, "w", encoding="utf-8") as f:

        f.write("# 斑马鱼基因问答体 — 完整数据包（含人工清洗）\n\n")

        f.write(f"- 导出时间：{manifest['exported_at']}\n")

        f.write(f"- 参考基因组：{REFERENCE_ASSEMBLY} / NCBI {NCBI_REFERENCE_ASSEMBLY}\n")

        f.write(f"- Ensembl release：{ENSEMBL_RELEASE} · taxon {TAXON_ID}\n\n")

        f.write("## 目录\n\n")

        f.write("| 目录 | 内容 |\n|------|------|\n")

        f.write("| `01_mysql/` | **全部 MySQL 表** CSV（原始表，含租户/工作流等） |\n")

        f.write("| `02_literature/` | 文献目录 JSONL+CSV、WoS RIS 原文件、关联进度 |\n")

        f.write("| `03_rag_knowledge/` | RAG 知识库 knowledge.d + knowledge.txt + 用户上传 |\n")

        f.write("| `04_import_metadata/` | 采集进度 JSON、KEGG、基因目录、SQL 结构 |\n")

        f.write("| `05_manifest/` | 行数统计 manifest.json |\n")

        f.write("| `06_cleaning/` | **★ 人工清洗专用**：队列、主表、样例、质量说明 |\n")

        f.write("| `07_reference_data/` | 参考 FASTA、FAISS 向量索引、NCBI GFF、BLAST 索引 |\n\n")

        f.write("## 人工清洗（优先看这里）\n\n")

        f.write("1. 打开 `06_cleaning/00_数据目录.md` 了解字段与流程\n")

        f.write("2. 先处理 `06_cleaning/06_cleaning_queue/queue_no_go.csv`\n")

        f.write("3. 清洗完成后放入 `项目阶段/05_数据与库/归档/data_export/cleaned_in/`，运行菜单 **14** 导回\n\n")

        if os.path.isfile(cleaning_readme):

            f.write("详细清洗说明见：`06_cleaning/00_数据目录.md`\n\n")

        f.write("## MySQL 表\n\n")

        for t, info in manifest["mysql_tables"].items():

            if "rows" in info:

                f.write(f"- `{t}`: {info['rows']:,} 行\n")

            else:

                f.write(f"- `{t}`: 导出失败\n")

        cleaning_stats = (manifest.get("cleaning") or {}).get("stats") or {}

        if cleaning_stats:

            f.write("\n## 清洗队列表\n\n")

            for k, v in cleaning_stats.items():

                if k.startswith("queue_"):

                    f.write(f"- `{k}`: {v:,} 行\n")

        f.write("\n## 说明\n\n")

        f.write("- 本包已合并「全量备份」与「人工清洗专用包」，无需再分别下载\n")

        f.write("- `01_mysql/` 为原始表；`06_cleaning/02_tables/` 为带 gene_name 联表的清洗友好视图\n")

        f.write("- `gene_reference`：文献→基因关联（来自 catalog + 链接脚本）\n")

        f.write("- `literature_catalog.jsonl`：~20 万篇 WoS/PubMed/EPMC 元数据\n")

        f.write("- `07_reference_data/` 含全基因组 cDNA、FAISS、NCBI GFF、BLAST 索引\n")

        f.write("- `Danio_rerio.GRCz11.cdna.all.fa` 与 `reference_genome.fasta` 内容相同，仅保留一份\n")

        f.write(f"- 包总大小约 {total_bytes // (1024 * 1024)} MB\n")



    with open(os.path.join(stats_dir, "manifest.json"), "w", encoding="utf-8") as mf:

        json.dump(manifest, mf, ensure_ascii=False, indent=2)



    safe_print(f"\n[完成] 全量导出完毕\n  {out_dir}\n  总大小: {total_bytes // (1024 * 1024)} MB")

    if make_zip:

        manifest["zip_path"] = _zip_bundle(out_dir)

    return manifest





def main() -> int:

    p = argparse.ArgumentParser(description="完整数据包：MySQL + 文献 + RAG + 参考数据 + 人工清洗")

    p.add_argument("--out", default="", help="输出目录")

    p.add_argument("--zip", action="store_true", help="导出完成后打包为 .zip")

    args = p.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    out = args.out or os.path.join(_BASE_DIR, "项目阶段", "05_数据与库", "归档", "data_export", f"zebrafish_gene_complete_{stamp}")

    export_full_bundle(out, make_zip=args.zip)

    return 0





if __name__ == "__main__":

    raise SystemExit(main())

