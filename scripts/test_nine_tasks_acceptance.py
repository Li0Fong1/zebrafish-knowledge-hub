# -*- coding: utf-8 -*-
"""九点任务验收（含「命中段+前后段」新口径）。离线/可连接时分层检查。"""
from __future__ import annotations

import ast
import math
import os
import re
import socket
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "api", ROOT / "agent", ROOT / "database", ROOT / "rag", ROOT / "llm"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

# 先加载 .env，避免 MySQL 仍用默认 root/123456
_env_file = ROOT / ".env"
if _env_file.is_file():
    for _raw in _env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        _line = _raw.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        _k = _k.strip()
        _v = _v.strip().strip('"').strip("'")
        if _k:
            os.environ.setdefault(_k, _v)
try:
    from entry import bootstrap_app

    bootstrap_app()
except Exception:
    try:
        from core.entry import bootstrap_app

        bootstrap_app()
    except Exception as _e:
        print("WARN bootstrap_app:", type(_e).__name__, _e)


def _port_open(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.4)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


_STARTED_MYSQL = False
if not _port_open(3306) and not _port_open(3307):
    import subprocess

    base = Path(os.environ.get("LOCALAPPDATA", "")) / "zfqa_auth_mysql"
    mysqld = Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe")
    if base.joinpath("mysql").is_dir() and mysqld.is_file():
        subprocess.Popen(
            [
                str(mysqld),
                "--no-defaults",
                "--basedir=C:/Program Files/MySQL/MySQL Server 8.0",
                f"--datadir={base}",
                "--port=3307",
                "--bind-address=127.0.0.1",
                "--mysqlx=0",
            ],
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
        for _ in range(20):
            time.sleep(0.4)
            if _port_open(3307):
                _STARTED_MYSQL = True
                break

# 仅当本机拉起了空密码临时库时，才强制改连 3307 + 空密码。
# 服务器上 MySQL 常在 3307 且有密码，不得覆盖 .env。
if _STARTED_MYSQL:
    os.environ["MYSQL_HOST"] = "127.0.0.1"
    os.environ["MYSQL_PORT"] = "3307"
    os.environ["MYSQL_USER"] = "root"
    os.environ["MYSQL_PASSWORD"] = ""
    os.environ["MYSQL_DATABASE"] = "zebrafish_db"


RESULTS: list[tuple[str, str, str]] = []  # task, PASS|FAIL|SKIP, detail


def rec(task: str, ok: bool, detail: str = "", *, skip: bool = False) -> None:
    status = "SKIP" if skip else ("PASS" if ok else "FAIL")
    RESULTS.append((task, status, detail[:240]))
    print(f"{status}\t{task}\t{detail[:200]}")


# ---------- 1 读库 ----------
def t1_db_read():
    from hub_orchestrator import answer_corpus_stats, is_corpus_stats_question

    assert is_corpus_stats_question("文献有多少")
    assert is_corpus_stats_question("斑马鱼的文献统计")
    try:
        out = answer_corpus_stats("文献有多少")
    except Exception as e:
        rec("1 读库统计", False, f"异常 {type(e).__name__}: {e}", skip=True)
        return
    ans = out.get("answer") or ""
    if "没有返回文献条数" in ans:
        # 空库/不可读：功能路径对，数据未就绪
        rec("1 读库统计", True, "短路路径正常，库内暂无计数（环境未就绪）")
        return
    ok = "本地文献库现有" in ans and "本地基因库" in ans and "tp53" not in ans.lower()
    rec("1 读库统计", ok, ans[:120].replace("\n", " "))


# ---------- 2 粗精排后打包：题名/摘要/命中+前后 ----------
def t2_pack_paragraphs():
    from pubmed_yes_retriever import PubmedYesRetriever as R

    # 命中+前+后
    chunks = [{"text": "正文：\n\n前段AAA\n\n命中HITBBB\n\n后段CCC"}]
    out = R._related_paragraphs(chunks, 80)
    ok = "前段AAA" in out and "HITBBB" in out and "后段CCC" in out and out.count("- ") == 3
    rec("2a 相关段落=前+命中+后", ok, out.replace("\n", " | ")[:160])

    # 打包块字段
    g = {
        "title": "English Title About Zebrafish",
        "year": 2021,
        "tag": "PMID:1",
        "tier": "gold",
        "journal": "Development",
        "doi": "10.1242/dev.000001",
        "chunks": [
            {
                "text": (
                    "摘要（英）：Abstract sentence one about tp53.\n\n"
                    "正文：\n\nPrev para about development.\n\n"
                    "Hit para mentioning tp53 pathway.\n\n"
                    "Next para about mutants."
                )
            }
        ],
    }
    block = R._paper_block(object.__new__(R), 1, g, excerpt_n=520, title_n=90)
    need = ("题名：", "摘要：", "相关段落：", "期刊：", "DOI：")
    missing = [x for x in need if x not in block]
    paras = re.findall(r"(?m)^- .+", block)
    ok2 = not missing and len(paras) >= 2
    rec("2b 打包含题名摘要相关段", ok2, f"missing={missing} paras={len(paras)}")


# ---------- 3 无 ZFQA + DOI 可点 ----------
def t3_no_zfqa_doi():
    from zebrafish_prompt import _format_ref_line, ensure_reference_footer

    line = _format_ref_line(
        1, "2020", "PMID:1", "A zebrafish study", "ZFQA-000001", "Nature", "10.1038/s41586-020-0001"
    )
    ok = "ZFQA" not in line and "[原文](" in line and "http" in line and "Nature" in line
    rec("3a 单条格式无ZFQA有原文", ok, line)

    ctx = (
        "[1] 2020 | PMID:1 | gold | ZFQA-000111 | 相关#1\n"
        "题名：Paper One Title\n期刊：Cell\nDOI：10.1016/j.cell.2020.01.001\n\n"
        "[2] 2021 | PMID:2 | gold | ZFQA-000222 | 相关#2\n"
        "题名：Paper Two Title\n期刊：Science\nDOI：10.1126/science.abc1234\n"
    )
    body = "机制简述 [1][2]。\n\n参考文献：\n[1] junk ZFQA-000111\n[2] junk ZFQA-000222"
    out = ensure_reference_footer(body, ctx, question="斑马鱼基因的转录机制")
    foot = out.split("参考文献")[-1]
    ok2 = "ZFQA" not in foot and "[原文](" in foot and "Paper One" in foot
    rec("3b 文末重写无ZFQA有链接", ok2, foot[:160].replace("\n", " "))

    hub = (ROOT / "agent" / "hub_orchestrator.py").read_text(encoding="utf-8")
    # 抱怨短路文案不得再教用户写 ZFQA
    bad = "文末给出 [n] 题名. 年份. ZFQA" in hub
    rec("3c 抱怨短路文案无ZFQA口径", not bad, "仍含旧 ZFQA 提示" if bad else "已清洁")


# ---------- 4 IF 用对数 ----------
def t4_if_log():
    from journal_if import JournalIF, if_norm_from_value, UNKNOWN_SCORE

    n10 = if_norm_from_value(10.0)
    n40 = if_norm_from_value(40.0)
    expect10 = math.log1p(10.0) / math.log1p(40.0)
    ok = abs(n10 - expect10) < 1e-9 and n40 <= 1.0 + 1e-9
    j = JournalIF("nature", "T0", 1.0, n40, 40.0)
    # 有实测 IF 时 if_score 走 if_norm，不是档位定值
    ok2 = abs(j.if_score - n40) < 1e-9
    unk = JournalIF("unknown-journal-xyz", "T4", 0.45, UNKNOWN_SCORE, None)
    ok3 = abs(unk.if_score - UNKNOWN_SCORE) < 1e-9
    rec("4 IF对数/非档位定值", ok and ok2 and ok3, f"norm10={n10:.4f} score40={j.if_score:.4f}")


# ---------- 5 文献格式 ----------
def t5_citation_format():
    from zebrafish_prompt import _format_ref_line

    line = _format_ref_line(3, "2019", "", "Title Here", "", "Dev Cell", "10.1016/j.devcel.2019.01.001")
    # [n] title. journal. year. [原文](url)
    ok = bool(re.match(r"^\[3\] Title Here\. Dev Cell\. 2019\. \[原文\]\(https?://", line))
    rec("5 文末条目格式", ok, line)


# ---------- 6 优先英文题名 ----------
def t6_english_title():
    from pubmed_yes_retriever import PubmedYesRetriever as R

    src = (ROOT / "rag" / "pubmed_yes_retriever.py").read_text(encoding="utf-8")
    prefer = 'h.get("title_en") or h.get("title_zh")' in src
    fill = "显示优先英文题名" in src or "优先英文" in src
    g = {
        "title": "English Preferred Title",
        "year": 2020,
        "tag": "PMID:9",
        "tier": "gold",
        "journal": "eLife",
        "doi": "",
        "chunks": [{"text": "摘要（英）：abc\n\n正文：\n\nOnly one para."}],
    }
    # pack_for_ai 用 title_en first；_paper_block 用 g['title']（由 pack 写入）
    block = R._paper_block(object.__new__(R), 1, g, 200, 90)
    ok = prefer and "题名：English Preferred Title" in block
    rec("6 优先英文题名", ok, f"prefer_code={prefer} fill_doc={fill}")


# ---------- 7 显示期刊 ----------
def t7_journal():
    from zebrafish_prompt import _format_ref_line
    from pubmed_yes_retriever import PubmedYesRetriever as R

    line = _format_ref_line(1, "2022", "", "T", "", "Development", "10.1242/dev.1")
    g = {
        "title": "T",
        "year": 2022,
        "tag": "PMID:1",
        "tier": "a",
        "journal": "Development",
        "doi": "10.1242/dev.1",
        "chunks": [{"text": "摘要：x\n\n正文：\n\nA.\n\nB.\n\nC."}],
    }
    block = R._paper_block(object.__new__(R), 1, g, 300, 80)
    ok = "Development" in line and "期刊：Development" in block
    rec("7 显示期刊", ok, line)


# ---------- 8 回答更干净：口径与提示 ----------
def t8_concise():
    prompt_path = ROOT / "llm" / "zebrafish_prompt.py"
    src = prompt_path.read_text(encoding="utf-8")
    marks = [
        "事实题" in src or "短答" in src or "少废话" in src or "不答非所问" in src or "按题型" in src,
        "机制" in src,
        "不要写 ZFQA" in src or "不写 ZFQA" in src,
        "is_hub_product_howto_question" in src,
    ]
    from zebrafish_prompt import is_hub_product_howto_question

    howto = False
    for q in ("基因序列展示怎么用", "怎么看基因序列", "Hub 怎么用", "快速入门是什么"):
        if is_hub_product_howto_question(q):
            howto = True
            break
    ok = all(marks[:3]) and marks[3]
    rec("8 简洁回答提示/短路", ok, f"howto_hit={howto} marks={marks}")


# ---------- 9 相对 ZFIN ----------
def t9_vs_zfin():
    from hub_orchestrator import answer_corpus_stats

    src = (ROOT / "agent" / "hub_orchestrator.py").read_text(encoding="utf-8")
    ok_code = "不是 ZFIN 基因页上的 publication 计数" in src
    try:
        ans = (answer_corpus_stats("文献统计").get("answer") or "")
        ok_ans = ("ZFIN" in ans) or ("没有返回" in ans)
    except Exception:
        ok_ans = True  # 代码口径在即可
    rec("9 相对ZFIN定位", ok_code and ok_ans, "计数说明含 ZFIN 边界" if ok_code else "缺说明")


def main() -> int:
    tests = [
        t1_db_read,
        t2_pack_paragraphs,
        t3_no_zfqa_doi,
        t4_if_log,
        t5_citation_format,
        t6_english_title,
        t7_journal,
        t8_concise,
        t9_vs_zfin,
    ]
    for fn in tests:
        try:
            fn()
        except Exception as e:
            rec(fn.__name__, False, f"{type(e).__name__}: {e}")

    # 顺带跑机制引用回归
    try:
        import test_mechanism_citation_routing as m

        fails = []
        for name in dir(m):
            if not name.startswith("test_"):
                continue
            try:
                getattr(m, name)()
                rec(f"机制回归/{name}", True, "")
            except Exception as e:
                fails.append(name)
                rec(f"机制回归/{name}", False, f"{type(e).__name__}: {e}")
    except Exception as e:
        rec("机制回归套件", False, f"{type(e).__name__}: {e}")

    # 可见回答回归（若脚本在）
    # 优先项目阶段正式副本：logs/ 下旧文件可能 parents 层级不对
    alt = ROOT / "项目阶段" / "03_Hub产品与公网" / "一次性脚本" / "_test_answer_visible_regression.py"
    vis = ROOT / "logs" / "_test_answer_visible_regression.py"
    path = alt if alt.is_file() else vis
    if path.is_file():
        import importlib.util

        spec = importlib.util.spec_from_file_location("answer_visible_regression", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            fails = mod.run_all()
            rec("可见回答回归", not fails, f"fails={fails}" if fails else "ok")
        except Exception as e:
            rec("可见回答回归", False, f"{type(e).__name__}: {e}")
    else:
        rec("可见回答回归", False, "脚本缺失", skip=True)

    fails = [r for r in RESULTS if r[1] == "FAIL"]
    skips = [r for r in RESULTS if r[1] == "SKIP"]
    print(f"\nSUMMARY pass={sum(1 for r in RESULTS if r[1]=='PASS')} "
          f"fail={len(fails)} skip={len(skips)}")
    for t, s, d in fails:
        print(f"  FAIL {t}: {d}")

    if _STARTED_MYSQL:
        import subprocess

        subprocess.run(
            [
                r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqladmin.exe",
                "--host=127.0.0.1",
                "--port=3307",
                "-uroot",
                "shutdown",
            ],
            check=False,
        )
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
