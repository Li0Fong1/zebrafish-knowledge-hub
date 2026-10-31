# -*- coding: utf-8 -*-
"""项目全量自检：语法、导入、数据库、API、工具链、数据文件。"""
from __future__ import annotations

import compileall
import importlib
import json
import os
import sys
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.entry import bootstrap_app

bootstrap_app()

ISSUES: list[dict[str, str]] = []


def add(severity: str, module: str, issue: str, detail: str = "") -> None:
    ISSUES.append({
        "severity": severity,
        "module": module,
        "issue": issue,
        "detail": (detail or "")[:500],
    })


def check_syntax() -> None:
    mod = "语法检查"
    ok = compileall.compile_dir(
        str(ROOT / "scripts"),
        quiet=1,
        force=True,
    )
    if not ok:
        add("错误", mod, "scripts/ 目录存在语法/缩进错误", "见 compileall 输出，已知 overnight_watchdog.py:114")
    for pkg in ("core", "api", "agent", "cli", "database", "rag", "llm"):
        if not compileall.compile_dir(str(ROOT / pkg), quiet=1, force=True):
            add("错误", mod, f"{pkg}/ 目录存在语法错误", "")


def check_imports() -> None:
    mod = "模块导入"
    targets = [
        "api.api_server",
        "agent.hub_orchestrator",
        "agent.workflow_engine",
        "database.mysql_client",
        "database.gene_id_mapper",
        "database.gene_toolkit",
        "database.enrichment_toolkit",
        "database.sequence_toolkit",
        "rag.rag_builder",
        "rag.wos_collector",
        "rag.pubmed_yes_retriever",
        "llm.llm_api",
        "llm.zebrafish_prompt",
        "knowledge_ingest",
    ]
    for name in targets:
        try:
            importlib.import_module(name)
        except Exception as e:
            add("错误", mod, f"无法导入 {name}", str(e))


def check_mysql() -> None:
    mod = "MySQL 数据库"
    try:
        from mysql_client import MySQLClient

        c = MySQLClient()
    except Exception as e:
        add("错误", mod, "MySQL 连接失败", str(e))
        return

    tables = [
        ("gene_basic", "基因基础表"),
        ("gene_xref", "基因交叉引用"),
        ("literature_wos", "WoS 文献"),
    ]
    for tbl, label in tables:
        try:
            r = c.execute_sql(f"SELECT COUNT(*) AS cnt FROM {tbl}")
            cnt = (r.get("data") or [{}])[0].get("cnt", -1)
            if cnt == 0:
                add("警告", mod, f"{label}({tbl}) 为空", "count=0")
            elif cnt < 0:
                add("错误", mod, f"{label}({tbl}) 查询失败", str(r))
        except Exception as e:
            add("错误", mod, f"{label}({tbl}) 不存在或不可读", str(e))

    # Hub 文献主路径是 PubmedYes FAISS；MySQL literature_pubmed 可为空
    try:
        r = c.execute_sql("SELECT COUNT(*) AS cnt FROM literature_pubmed")
        cnt = (r.get("data") or [{}])[0].get("cnt", -1)
        if cnt == 0:
            from core.config import FAISS_PUBMED_YES_DIR
            faiss_dir = Path(FAISS_PUBMED_YES_DIR)
            has_faiss = faiss_dir.is_dir() and any(faiss_dir.glob("shard_*/index.faiss"))
            if has_faiss:
                add("信息", mod, "literature_pubmed 为空（已由 PubmedYes FAISS 承接）", f"faiss={faiss_dir}")
            else:
                add("警告", mod, "PubMed 文献(literature_pubmed) 为空且无 FAISS", "count=0")
        elif cnt < 0:
            add("错误", mod, "PubMed 文献(literature_pubmed) 查询失败", str(r))
    except Exception as e:
        add("信息", mod, "literature_pubmed 表不可用", str(e))

    try:
        r = c.execute_sql(
            "SELECT COUNT(*) AS cnt FROM gene_xref WHERE db_name='zfin' AND db_id LIKE 'ZDB-GENE-%'"
        )
        zfin = (r.get("data") or [{}])[0].get("cnt", 0)
        if zfin < 1000:
            add("警告", mod, "ZFIN xref 数据不足", f"count={zfin}，需运行 build_gene_aliases")
    except Exception:
        pass


def check_id_mapping() -> None:
    mod = "基因 ID 解析"
    try:
        from gene_id_mapper import resolve_gene_identifier

        for q, expect in [("p53", "tp53"), ("30590", "tp53"), ("shh", "shha")]:
            r = resolve_gene_identifier(q, use_ensembl_fallback=True)
            name = (r.get("canonical") or {}).get("gene_name")
            if not r.get("resolved") or name != expect:
                add("错误", mod, f"resolve({q!r}) 失败", f"resolved={r.get('resolved')} name={name!r} expect={expect!r}")
    except Exception as e:
        add("错误", mod, "resolve_gene_identifier 异常", str(e))


def check_gene_tools() -> None:
    mod = "基因分析工具"
    try:
        from gene_toolkit import get_gene_detail, search_genes

        d = get_gene_detail("hsp70")
        if not d:
            add("错误", mod, "get_gene_detail('hsp70') 返回空", "gene_basic 可能无数据")
        elif d["basic"]["gene_name"] != "hsp70.1":
            add("警告", mod, "hsp70 别名映射异常", f"got {d['basic']['gene_name']!r}")
        rows = search_genes("mapk")
        if not rows:
            add("警告", mod, "search_genes('mapk') 无结果", "")
    except Exception as e:
        add("错误", mod, "基因工具链异常", str(e))


def check_enrichment() -> None:
    mod = "GO/KEGG 富集"
    try:
        from enrichment_toolkit import go_enrichment, kegg_enrichment, kegg_pathway_search

        go = go_enrichment(["tp53", "hsp70", "myl7"], top_n=5)
        if not go.get("success"):
            add("警告", mod, "GO 富集失败", go.get("error", str(go))[:200])
        kegg = kegg_enrichment(["tp53", "mapk1", "fgf8a"], top_n=5)
        if not kegg.get("success"):
            add("警告", mod, "KEGG 富集失败", kegg.get("error", str(kegg))[:200])
        kw = kegg_pathway_search("MAPK")
        if not kw.get("success") or not kw.get("pathways"):
            add("警告", mod, "KEGG 通路检索失败", str(kw)[:200])
    except Exception as e:
        add("错误", mod, "富集模块异常", str(e))


def check_blast() -> None:
    mod = "BLAST"
    try:
        from seq_toolkit import BLAST_SAMPLE_SEQ, blast_library_status, run_local_blast, validate_sequence

        st = blast_library_status()
        if not st.get("blast_is_grcz12"):
            add("警告", mod, "本机无 GRCz12tu FASTA，跳过现网级 BLAST", json.dumps(st, ensure_ascii=False))
            return
        ok, msg = validate_sequence(BLAST_SAMPLE_SEQ)
        if not ok:
            add("警告", mod, "序列校验失败", msg)
            return
        blast = run_local_blast(BLAST_SAMPLE_SEQ, top_k=3, evalue=0.01)
        if blast.get("success") is False:
            add("警告", mod, "BLAST 运行失败", blast.get("error") or blast.get("note") or str(blast)[:200])
        elif (blast.get("assembly") or "") != "GRCz12tu":
            add("错误", mod, "BLAST 未锚定 GRCz12tu", str(blast.get("database") or blast.get("assembly")))
    except Exception as e:
        add("警告", mod, "BLAST 模块异常", str(e))


def check_api_routes() -> None:
    mod = "FastAPI 路由"
    try:
        from fastapi.testclient import TestClient
        from api.api_server import app
        from api.web_security import get_access_token

        headers = {"X-Access-Token": token} if (token := get_access_token()) else {}
        client = TestClient(app)
        r = client.get("/api/health")
        if r.status_code != 200:
            add("错误", mod, "/api/health 非 200", f"status={r.status_code}")

        r = client.get("/api/tools/gene-resolve", params={"q": "p53"}, headers=headers)
        if r.status_code == 401:
            add("警告", mod, "gene-resolve 需访问令牌", "请配置 WEB_ACCESS_TOKEN 或关闭 WEB_AUTH_REQUIRED")
        elif r.status_code != 200 or not r.json().get("resolved"):
            add("错误", mod, "gene-resolve 失败", f"status={r.status_code} body={r.text[:200]}")

        r = client.post("/api/tools/id-convert/text", json={"text": "p53\n30590"}, headers=headers)
        if r.status_code == 401:
            add("警告", mod, "id-convert 需访问令牌", "请配置 WEB_ACCESS_TOKEN")
        elif r.status_code != 200:
            add("错误", mod, "id-convert/text 失败", f"status={r.status_code}")
    except Exception as e:
        add("错误", mod, "TestClient 异常", str(e))


def check_live_web() -> None:
    mod = "Web 服务"
    from core.config import WEB_PORT

    url = f"http://127.0.0.1:{WEB_PORT}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            if resp.status != 200:
                add("警告", mod, "Web 服务响应异常", f"status={resp.status}")
    except Exception as e:
        add("信息", mod, "Web 服务未运行", str(e))


def check_llm_api() -> None:
    mod = "DeepSeek API"
    try:
        from llm_loader import get_api_llm, should_use_api_reasoning

        if not should_use_api_reasoning():
            add("信息", mod, "未启用云端 LLM", "LLM_MODE 非 hybrid 或未配置 LLM_API_KEY")
            return
        api = get_api_llm()
        ans = api.generate("测试：斑马鱼是什么？", max_new_tokens=30)
        if not ans or len(ans) < 5:
            add("警告", mod, "API 返回过短", repr(ans))
    except Exception as e:
        add("错误", mod, "DeepSeek API 调用失败", str(e))


def check_ncbi() -> None:
    mod = "NCBI E-utilities"
    import urllib.parse

    params = urllib.parse.urlencode({
        "db": "pubmed", "term": "zebrafish", "retmax": 1, "retmode": "json",
        "email": os.getenv("NCBI_EMAIL", "test@local.dev"), "tool": "ZebrafishGeneQA",
    })
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + params
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "ZebrafishGeneQA/1.0"}),
            timeout=30,
        ) as resp:
            data = json.loads(resp.read())
            count = data.get("esearchresult", {}).get("count")
            if not count:
                add("警告", mod, "NCBI 检索返回空", str(data)[:200])
    except Exception as e:
        add("警告", mod, "NCBI 连接失败", str(e))


def check_data_files() -> None:
    mod = "数据文件"
    paths = [
        (ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "merged_judgement_full" / "_src" / "wos" / "result_95000.md", "WoS 判别 MD", "警告"),
        (ROOT / "data" / "kegg_pathways.json", "KEGG 通路 JSON", "警告"),
        (ROOT / "models" / "bge-small-zh-v1.5", "嵌入模型目录", "错误"),
    ]
    for p, label, sev in paths:
        if not p.exists():
            add(sev, mod, f"{label} 缺失", str(p))


def check_rag_index() -> None:
    mod = "RAG/FAISS"
    from core.config import FAISS_INDEX_DIR, KNOWLEDGE_PATH

    index_dir = Path(FAISS_INDEX_DIR)
    index_files = list(index_dir.glob("*.faiss")) + list(index_dir.glob("*.pkl"))
    if not index_dir.is_dir():
        add("警告", mod, "FAISS 索引目录不存在", str(FAISS_INDEX_DIR))
    elif not index_files:
        add("警告", mod, "FAISS 索引未构建", f"运行 python -m scripts.rebuild_naming_rag；目录 {FAISS_INDEX_DIR}")
    if not os.path.isfile(KNOWLEDGE_PATH):
        add("信息", mod, "knowledge.txt 不存在", KNOWLEDGE_PATH)


def check_pubmed_corpus() -> None:
    mod = "PubMed 语料"
    corpus = ROOT / "data" / "literature" / "pubmed_yes_corpus.jsonl"
    vec_status = ROOT / "data" / "literature" / "pubmed_yes_vectorize_status.md"
    if corpus.is_file():
        lines = sum(1 for _ in corpus.open(encoding="utf-8", errors="replace"))
        if lines == 0:
            add("警告", mod, "pubmed_yes_corpus.jsonl 为空", "")
    else:
        add("信息", mod, "pubmed_yes_corpus.jsonl 未生成", str(corpus))
    if not vec_status.is_file():
        add("信息", mod, "向量化状态文件缺失", str(vec_status))


def check_wos_ris() -> None:
    mod = "WoS RIS"
    from rag.wos_ris_paths import discover_wos_ris_dirs, list_wos_ris_files

    dirs = discover_wos_ris_dirs()
    files = list_wos_ris_files(dirs)
    if not files:
        add("警告", mod, "未找到 RIS 文件", "; ".join(dirs) or "(无目录)")
    elif len(files) < 10:
        add("信息", mod, "RIS 文件数偏少", f"count={len(files)}")


def check_answer_visible_regression() -> None:
    mod = "可见回答/文末文献回归"
    p = ROOT / "logs" / "_test_answer_visible_regression.py"
    if not p.is_file():
        add("错误", mod, "回归脚本缺失", str(p))
        return
    import importlib.util

    spec = importlib.util.spec_from_file_location("answer_visible_regression", p)
    if spec is None or spec.loader is None:
        add("错误", mod, "无法加载回归脚本", str(p))
        return
    modu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modu)
    fails = modu.run_all()
    if fails:
        add("错误", mod, f"{len(fails)} 项失败（含 100 篇整表泄漏）", "; ".join(fails)[:500])


def check_test_scripts() -> None:
    mod = "测试脚本自身"
    p = ROOT / "scripts" / "test_analysis_tools.py"
    if not p.is_file():
        add("警告", mod, "test_analysis_tools.py 缺失", "")
        return
    src = p.read_text(encoding="utf-8")
    if "go_n = len(d.get" not in src:
        add("错误", mod, "test_analysis_tools.py 缺少空值保护", "hsp70 不存在时会 AttributeError")


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="项目全量自检")
    p.add_argument("--skip-db", action="store_true", help="跳过 MySQL / 基因库相关检查")
    args = p.parse_args()

    checks = [
        check_syntax,
        check_imports,
        check_api_routes,
        check_live_web,
        check_llm_api,
        check_ncbi,
        check_data_files,
        check_rag_index,
        check_pubmed_corpus,
        check_wos_ris,
        check_test_scripts,
        check_answer_visible_regression,
    ]
    if not args.skip_db:
        checks[2:2] = [
            check_mysql,
            check_id_mapping,
            check_gene_tools,
            check_enrichment,
            check_blast,
        ]

    for fn in checks:
        try:
            fn()
        except Exception as e:
            add("错误", fn.__name__, "检查过程异常", traceback.format_exc()[-400:])

    out_json = ROOT / "logs" / "full_project_test_results.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(ISSUES, ensure_ascii=False, indent=2), encoding="utf-8")

    sev_order = {"错误": 0, "警告": 1, "信息": 2}
    ISSUES.sort(key=lambda x: (sev_order.get(x["severity"], 9), x["module"]))

    print(f"FULL_TEST_ISSUES={len(ISSUES)}")
    for i, row in enumerate(ISSUES, 1):
        print(f"{i}\t{row['severity']}\t{row['module']}\t{row['issue']}\t{row['detail'][:120]}")
    return 1 if any(x["severity"] == "错误" for x in ISSUES) else 0


if __name__ == "__main__":
    raise SystemExit(main())
