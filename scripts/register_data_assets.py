# -*- coding: utf-8 -*-
"""登记本机斑马鱼数据根目录到 data_asset_registry（不搬二进制进库）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from core.entry import bootstrap_app
bootstrap_app()

from sqlalchemy import text
from mysql_client import MySQLClient


def walk_stats(path: Path, max_files=5_000_000):
    n, b = 0, 0
    if not path.exists():
        return None, None
    if path.is_file():
        return 1, path.stat().st_size
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in {".venv", "__pycache__", ".git"}]
        for fn in filenames:
            n += 1
            if n > max_files:
                return n, b
            try:
                b += (Path(dirpath) / fn).stat().st_size
            except OSError:
                pass
    return n, b


def main():
    sql_path = ROOT / "sql" / "data_asset_registry_migration.sql"
    client = MySQLClient()
    ddl = sql_path.read_text(encoding="utf-8")
    # strip USE
    for stmt in ddl.split(";"):
        s = stmt.strip()
        if not s or s.upper().startswith("USE "):
            continue
        with client.engine.begin() as conn:
            conn.execute(text(s))

    appdata = Path(os.environ.get("LOCALAPPDATA", "")) / "ZebrafishGeneQA"
    assets = [
        ("local:workspace", "code", "0", "workspace", ROOT, 1, "core,database", "本机主仓库"),
        ("local:pdf_lib", "pdf", "1", "workspace", ROOT / "pdf库", 0, None, "源 PDF/HTML，禁止搬进 Git"),
        ("local:gold_json", "gold_json", "1", "workspace", ROOT / "解析文件", 1, "literature_fulltext_gold", "金标 JSON 战役目录"),
        ("local:data", "corpus", "5", "workspace", ROOT / "data", 0, None, "文献语料/参考基因组/KEGG"),
        ("local:merged_corpus", "corpus", "2", "workspace", ROOT / "merged_corpus", 0, None, "合并语料"),
        ("local:models", "model", "2", "workspace", ROOT / "models", 0, None, "嵌入/生成权重"),
        ("local:faiss_appdata", "faiss", "2", "local_cache", appdata / "faiss_pubmed_yes_gte_mbase_v1", 1, "literature_vector_shard", "PubMed yes GTE 索引"),
        ("local:gold_stage", "cache", "1", "local_cache", Path(r"D:\ZebrafishGeneQA\gold_stage"), 0, None, "金标切块缓存 paper_parts"),
        ("local:faiss_data", "faiss", "2", "workspace", ROOT / "data" / "faiss_index", 1, "literature_vector_shard", "仓库内 FAISS"),
        ("server:qa", "code", "3", "server", Path("/data/zoujin/deng/zebrafish_gene_qa"), 1, None, "服务器代码树（需 SSH 并集拉取）"),
        ("server:runtime", "corpus", "4", "server", Path("/data/zoujin/deng/zebrafish_runtime"), 0, None, "服务器 MySQL/日志/模型运行时"),
        ("optical:pending", "external", "0", "optical", Path("(未挂载)"), 0, None, "本机无光盘盘符；插入后拷入 pdf库 再跑解析"),
        ("archive:early_rag_qa", "code", "0", "other", ROOT / "项目阶段" / "00_环境与配置" / "归档" / "早期原型_rag_qa.py", 0, None, "从 D:/PycharmProjects/rag_qa.py 归档"),
    ]

    upsert = text(
        """
        INSERT INTO data_asset_registry
        (asset_id, role, stage, location_kind, root_path, file_count, byte_size, notes, in_mysql, mysql_tables)
        VALUES (:id, :role, :stage, :kind, :path, :n, :b, :notes, :in_mysql, :tables)
        ON DUPLICATE KEY UPDATE
          role=VALUES(role), stage=VALUES(stage), location_kind=VALUES(location_kind),
          root_path=VALUES(root_path), file_count=VALUES(file_count), byte_size=VALUES(byte_size),
          notes=VALUES(notes), in_mysql=VALUES(in_mysql), mysql_tables=VALUES(mysql_tables)
        """
    )

    with client.engine.begin() as conn:
        for asset_id, role, stage, kind, path, in_mysql, tables, notes in assets:
            n, b = walk_stats(path) if str(path) not in ("(未挂载)",) and not str(path).startswith("/data/") else (None, None)
            print(f"{asset_id} n={n} bytes={b}", flush=True)
            conn.execute(
                upsert,
                {
                    "id": asset_id,
                    "role": role,
                    "stage": stage,
                    "kind": kind,
                    "path": str(path),
                    "n": n,
                    "b": b,
                    "notes": notes,
                    "in_mysql": in_mysql,
                    "tables": tables,
                },
            )
        rows = conn.execute(text("SELECT COUNT(*) FROM data_asset_registry")).scalar()
        print("registry_rows", rows, flush=True)


if __name__ == "__main__":
    main()
