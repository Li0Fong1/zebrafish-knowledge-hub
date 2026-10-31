# -*- coding: utf-8 -*-
import bootstrap  # noqa: F401  必须最先导入，修复乱码与中文模式

import logging
import os
import re
import sys
import threading
from functools import lru_cache


# ===================== 日志密钥脱敏 =====================
# 任何密钥都可能因异常栈、上游报错原文、调试打印而落进日志，
# 在格式化出口统一打码，是成本最低、覆盖面最广的一道防线。
_SECRET_RULES = (
    # OpenAI / DeepSeek 风格密钥
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    # Authorization: Bearer xxx
    (re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._\-]{8,})"), r"\1***"),
    # api_key=xxx / password: xxx / token=xxx 等赋值式
    (
        re.compile(
            r"(?i)\b((?:api[_-]?key|access[_-]?token|auth[_-]?token|"
            r"api[_-]?secret|password|passwd|secret|token)\s*[=:]\s*)"
            r"([^\s,;\"'&})]{4,})"
        ),
        r"\1***",
    ),
)


def redact_secrets(text: str) -> str:
    """把文本里的密钥/口令打码。用于日志、上游错误回显。"""
    if not text:
        return text
    try:
        for pat, repl in _SECRET_RULES:
            text = pat.sub(repl, text)
    except Exception:
        return text
    return text


# ===================== 编码与中文模式 =====================
def setup_console_encoding():
    """与 bootstrap 保持一致，供显式调用。"""
    bootstrap._fix_windows_console()


class _UTF8StreamHandler(logging.Handler):
    """直接向终端 buffer 写 UTF-8，避免 Cursor / PowerShell 中文乱码。"""

    terminator = "\n"

    def __init__(self, stream=None):
        super().__init__()
        self.stream = stream or sys.stderr

    def emit(self, record):
        try:
            msg = self.format(record) + self.terminator
            bootstrap.write_console(msg, err=(self.stream is sys.stderr))
        except Exception:
            self.handleError(record)


class _ChineseLogFormatter(logging.Formatter):
    """将日志级别显示为中文。"""
    _LEVEL_MAP = {
        "DEBUG": "调试",
        "INFO": "信息",
        "WARNING": "警告",
        "ERROR": "错误",
        "CRITICAL": "严重",
    }

    def format(self, record):
        record.levelname = self._LEVEL_MAP.get(record.levelname, record.levelname)
        return redact_secrets(super().format(record))


def setup_chinese_mode():
    """中文界面：屏蔽第三方库英文日志。"""
    bootstrap._silence_third_party()


setup_console_encoding()
setup_chinese_mode()

import re


# ===================== 流式输出通道（SSE） =====================
# 多机并行后同一时刻会有多个问答在跑，全局单 sink 会让 A 用户的 token 推给 B 用户。
# 因此改成「线程优先」：并发闸的每个任务跑在独立 worker 线程里，各自 set 自己的 sink，
# 互不干扰；未设置时（脚本 / 单线程场景）退回全局 sink，保持旧行为。
_stream_sink = None
_stream_sink_lock = threading.Lock()
_thread_sink = threading.local()


def set_stream_sink(sink):
    """设置流式 token 回调（每个 token 一个字符串）。返回旧 sink。

    优先写到当前线程：并发问答各自隔离。返回旧值便于调用方 finally 里复位。
    """
    global _stream_sink
    try:
        old = getattr(_thread_sink, "sink", None)
        _thread_sink.sink = sink
        return old
    except Exception:
        with _stream_sink_lock:
            old = _stream_sink
            _stream_sink = sink
            return old


def get_stream_sink():
    """当前线程的 sink 优先；没有则退回全局 sink（兼容脚本直调）。"""
    sink = getattr(_thread_sink, "sink", None)
    if sink is not None:
        return sink
    return _stream_sink


def clear_stream_sink():
    global _stream_sink
    if hasattr(_thread_sink, "sink"):
        try:
            _thread_sink.sink = None
        except Exception:
            pass
    with _stream_sink_lock:
        _stream_sink = None


def emit_stream(text):
    """向当前流式 sink 推送一段文本（空串或未设置时忽略）。"""
    if not text:
        return
    sink = _stream_sink
    if sink is not None:
        try:
            sink(text)
        except Exception:
            pass


# 流式 delta 在「参考文献」标题处截断，脚注只出现在 done.result.answer。
_REF_HEAD_STREAM = re.compile(
    r"(?:\n|^)[ \t]*(?:#{0,6}[ \t]*)?(?:\*\*[ \t]*)?"
    r"(?:参考文献|参考资料|引用文献|文献列表|参考研究|References|Bibliography)",
    re.I,
)
_STREAM_REF_TAIL = 24


def hold_stream_before_refs(
    held: bool, tail: str, text: str, *, final: bool = False
) -> tuple:
    """流式截断：标题之后的 token 全部丢掉。

    返回 (held, new_tail, emit)。保留 24 字尾巴，避免标题被拆到相邻 chunk。
    """
    if held:
        return True, "", ""
    blob = (tail or "") + (text or "")
    m = _REF_HEAD_STREAM.search(blob)
    if m:
        return True, "", blob[: m.start()]
    if final:
        return False, "", blob
    keep = _STREAM_REF_TAIL
    if len(blob) > keep:
        return False, blob[-keep:], blob[:-keep]
    return False, blob, ""


def safe_print(*args, **kwargs):
    """UTF-8 安全输出，兼容 Windows CMD / Cursor 终端。"""
    sep = kwargs.pop("sep", " ")
    end = kwargs.pop("end", "\n")
    flush = kwargs.pop("flush", True)
    text = sep.join(str(arg) for arg in args) + end
    try:
        bootstrap.write_console(text, err=False)
    except Exception:
        print(text, end="", flush=flush)


def safe_input(prompt: str = "") -> str:
    """中文安全输入，避免编码异常。"""
    try:
        if prompt:
            bootstrap.write_console(prompt, err=False)
        raw = sys.stdin.buffer.readline()
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return input(prompt)


# ===================== 日志配置（中文级别） =====================
_handler = _UTF8StreamHandler(sys.stderr)
_handler.setFormatter(_ChineseLogFormatter("%(asctime)s - %(levelname)s - %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)
logger = logging.getLogger(__name__)


def is_exit_command(text: str) -> bool:
    """判断用户是否要退出。"""
    return text.strip().lower() in {"exit", "quit", "q", "退出", "再见"}


# ===================== 显存清理 =====================
def clear_gpu_cache():
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
            logger.info("[成功] GPU显存已清理")
    except ImportError:
        pass


# ===================== 缓存装饰器 =====================
def llm_cache(maxsize=128):
    def decorator(func):
        cached_func = lru_cache(maxsize=maxsize)(func)

        def wrapper(*args, **kwargs):
            try:
                return cached_func(*args, **kwargs)
            except Exception as e:
                logger.error(f"缓存调用失败：{e}")
                return func(*args, **kwargs)

        return wrapper

    return decorator


# ===================== 多模型融合核心逻辑 =====================
def _trim_prompt(max_chars: int) -> int:
    from config import ANSWER_MAX_CHARS
    return max_chars or ANSWER_MAX_CHARS


def empty_result() -> dict:
    return {
        "answer": "",
        "task_type": "",
        "sql": None,
        "data": None,
        "row_count": None,
        "context": None,
        "gene_detail": None,
        "error": None,
    }


def build_fusion_prompt(question: str, answers: list) -> str:
    answer_str = "\n".join([f"模型{i + 1}回答：{ans}" for i, ans in enumerate(answers)])
    return f"""请把下列多个模型对同一问题的回答，整合成一份完整、准确、有条理的中文最终答案。
要求：信息全面、去重合并、逻辑连贯、尽量展开，不要为省字而删掉任何关键信息（不设字数上限）。
用户问题：{question}
{answer_str}
最终答案："""


def _reasoning_max_tokens() -> int:
    from config import LLM_REASONING_MAX_TOKENS
    return LLM_REASONING_MAX_TOKENS


def answer_with_reasoning(
    question: str,
    *,
    rag_context: str = "",
    db_text: str = "",
    gene_detail: dict = None,
    draft_hint: str = "",
) -> str:
    """DeepSeek / 本地推理；academic 模式下事实类问句直接返回审定草稿。"""
    from full_rag_chat import truncate_context
    from llm_loader import get_reasoning_llm
    from zebrafish_prompt import build_complete_user_prompt, is_academic_mode, normalize_visible_answer
    from gene_formatter import is_factual_template_question, finalize_academic_structured_answer
    from config import RAG_CONTEXT_MAX_CHARS, ENSEMBL_RELEASE, REFERENCE_ASSEMBLY

    if is_academic_mode() and draft_hint and draft_hint.strip() and is_factual_template_question(question):
        return finalize_academic_structured_answer(
            draft_hint.strip(),
            f"本地库 MySQL gene_basic（{REFERENCE_ASSEMBLY}）",
            f"Ensembl release {ENSEMBL_RELEASE}",
        )

    ctx = truncate_context(rag_context, max_chars=RAG_CONTEXT_MAX_CHARS) if rag_context else ""
    prompt = build_complete_user_prompt(
        question,
        rag_context=ctx,
        db_text=db_text,
        gene_detail=gene_detail,
        draft_hint=draft_hint,
    )
    answer = get_reasoning_llm().generate(prompt, max_new_tokens=_reasoning_max_tokens())
    answer = normalize_visible_answer(answer, question=question)
    if is_academic_mode() and draft_hint and draft_hint.strip():
        return finalize_academic_structured_answer(
            answer,
            "本地库 MySQL",
            f"Ensembl release {ENSEMBL_RELEASE}",
        )
    return answer


def _extract_sql(raw_output: str) -> str:
    sql_match = re.search(r'SELECT.*?[;；]', raw_output, re.IGNORECASE | re.DOTALL)
    return sql_match.group().strip().replace('；', ';') if sql_match else ""


def _run_db_pipeline(question: str, light_llm, heavy_llm) -> dict:
    from mysql_client import get_mysql_client
    from config import FAST_MODE

    result = empty_result()
    # 全库统计类问题（不含具体基因符号）：直接走库级统计 SQL，避免 LLM 把问句当基因名
    from gene_toolkit import extract_gene_from_question
    q_low = (question or "").lower()
    is_corpus_query = not extract_gene_from_question(question) and any(
        k in q_low for k in (
            "全库", "多少基因", "共多少", "库里有", "数据覆盖", "整体情况",
            "收录了", "总共", "多少篇", "统计", "数据怎么样", "数据如何",
            "库里的", "数据情况", "目前库", "库中", "整体数据", "数据库里",
            "现在.*数据", "数据.*情况",
        )
    )
    # 兜底：无基因的“数据/库/统计/整体”类问法即使上面未命中，也尝试库级统计
    if not is_corpus_query and not extract_gene_from_question(question):
        if re.search(r"(数据|库|统计|整体|收录|规模)", q_low):
            is_corpus_query = True
    if is_corpus_query:
        corpus_sql = _build_corpus_stats_sql()
        result["sql"] = corpus_sql
        mysql_client = get_mysql_client()
        res = mysql_client.execute_sql(corpus_sql)
        if res["success"] and res["data"]:
            result["data"] = res["data"]
            result["row_count"] = res["count"]
            import json
            data_preview = json.dumps(res["data"][:60], ensure_ascii=False, indent=2)
            from llm_loader import uses_remote_reasoning
            if uses_remote_reasoning():
                # 用别名避免遮蔽模块级 answer_with_reasoning
                from common_utils import answer_with_reasoning as _corpus_awr
                final_ans = _corpus_awr(question, db_text=data_preview)
            else:
                from llm_loader import get_reasoning_llm
                from zebrafish_prompt import build_db_user_prompt
                reasoning = get_reasoning_llm()
                final_ans = reasoning.generate(
                    build_db_user_prompt(question, data_preview),
                    max_new_tokens=_reasoning_max_tokens(),
                )
            result["answer"] = f"{final_ans}\n（依据全库统计：基因 {res['data'][0].get('gene_count') if res['data'] else '?'} 个 / 表达记录 {res['data'][0].get('expr_count') if res['data'] else '?'} 条）"
            return result

    # 兜底：无基因符号且非全库统计的问题，数据库结构化查询无意义（无法定位基因），
    # 降级为文献/科普回答，避免 LLM 把物种名当基因名生成荒谬 SQL 或输出 SQL 残句。
    if not extract_gene_from_question(question) and not any(
        k in q_low for k in ("全库", "多少基因", "共多少", "库里有", "统计", "总共",
                             "收录", "数据怎么样", "数据如何", "库里的", "数据情况", "整体数据",)
    ):
        logger.info(f"[数据库] 无基因符号且非统计类问题，转文献/科普回答：{question[:40]}")
        return _run_rag_pipeline(question, light_llm, heavy_llm)

    raw_sql = light_llm.generate(build_sql_prompt(question), max_new_tokens=80)
    logger.info(f"[调试] 轻量模型生成SQL：{raw_sql[:100]}")

    sql = _extract_sql(raw_sql) or raw_sql.strip()
    if not FAST_MODE:
        optimize_prompt = f"""仅输出优化后的SQL语句，禁止输出其他内容。
可用表：gene_basic,gene_expression,gene_go,homology_gene,gene_reference,gene
允许字段：gene_name,gene_symbol,tissue,gene_function,chromosome,expression_level,
go_term,go_category,species,homo_gene_name,homology_score,expression_stage,ensembl_id,zfin_id
原始SQL：{raw_sql}
优化SQL："""
        optimized_sql = heavy_llm.generate(optimize_prompt, max_new_tokens=80)
        sql = _extract_sql(optimized_sql) or sql

    if not is_sql_valid(sql):
        logger.warning("SQL校验失败，使用兜底SQL")
        sql = build_fallback_sql(question)

    result["sql"] = sql
    mysql_client = get_mysql_client()
    res = mysql_client.execute_sql(sql)
    # LLM 生成的 SQL 即使通过静态校验，也可能因列-表归属/语法在 MySQL 侧失败
    # （如 SELECT T2.gene_name 但 gene_name 属于 gene_basic）。执行失败时回退可靠模板。
    if not res["success"]:
        logger.warning(f"SQL执行失败，回退模板SQL：{res.get('error', '')[:120]}")
        fallback_sql = build_fallback_sql(question)
        if fallback_sql and fallback_sql != sql:
            result["sql"] = fallback_sql
            res = mysql_client.execute_sql(fallback_sql)
    if not res["success"]:
        result["error"] = res.get("error", "SQL执行失败")
        result["answer"] = f"[错误] {result['error']}"
        return result
    if not res["data"]:
        result["answer"] = "[错误] 未查询到相关数据"
        return result

    result["data"] = res["data"]
    result["row_count"] = res["count"]
    try:
        from gene_toolkit import extract_gene_from_question, get_gene_detail, normalize_gene_name
        from gene_formatter import answer_from_gene_detail, is_expression_question
        gname = extract_gene_from_question(question)
        if gname:
            gname = normalize_gene_name(gname)
            detail = get_gene_detail(gname)
            if detail:
                result["gene_detail"] = detail
                # 表达类或单基因查询：有 DeepSeek 时走完整叙述；否则模板兜底
                if is_expression_question(question) or res["count"] <= 30:
                    from llm_loader import uses_remote_reasoning
                    if uses_remote_reasoning():
                        try:
                            from common_utils import answer_with_reasoning
                            skeleton = answer_from_gene_detail(question, detail)
                            result["answer"] = answer_with_reasoning(
                                question,
                                gene_detail=detail,
                                draft_hint=skeleton,
                            )
                        except Exception as e:
                            logger.warning(f"[数据库] DeepSeek 综合失败，回退模板：{e}")
                            result["answer"] = answer_from_gene_detail(question, detail)
                    else:
                        result["answer"] = answer_from_gene_detail(question, detail)
                    result["answer"] += f"\n\n（SQL 返回 {res['count']} 条，以上依据 gene_basic / gene_expression 等表）"
                    return result
    except Exception as e:
        logger.warning(f"基因详情加载失败：{e}")

    from llm_loader import get_reasoning_llm, uses_remote_reasoning
    from zebrafish_prompt import build_db_user_prompt
    import json
    data_preview = json.dumps(res["data"][:40], ensure_ascii=False, indent=2)
    if res["count"] > 40:
        data_preview += f"\n…（共 {res['count']} 条，仅展示前 40 条）"
    if uses_remote_reasoning():
        final_ans = answer_with_reasoning(question, db_text=data_preview)
    else:
        reasoning = get_reasoning_llm()
        final_ans = reasoning.generate(
            build_db_user_prompt(question, data_preview),
            max_new_tokens=_reasoning_max_tokens(),
        )
        # 不再用轻量模型「精简回答」（旧版砍到120字，导致数据库回答不全面）
    result["answer"] = f"{final_ans}\n（共查询到{res['count']}条数据）"
    return result


def _format_rag_answer(question: str, context: str) -> str:
    from full_rag_chat import truncate_context
    excerpt = truncate_context(context, max_chars=3200)
    return (
        f"根据本地知识库，与「{question}」相关的内容如下：\n\n"
        f"{excerpt}\n\n"
        f"（来源：本地知识库检索；需要深度推理可选用「多模型融合」模式。）"
    )


def _synthesize_rag_answer(
    question: str,
    context: str,
    reasoning_llm=None,
    gene_detail: dict = None,
    draft_hint: str = "",
) -> str:
    try:
        answer = answer_with_reasoning(
            question,
            rag_context=context,
            gene_detail=gene_detail,
            draft_hint=draft_hint,
        )
        if answer and len(answer.strip()) >= 8:
            return answer.strip()
    except Exception as e:
        logger.warning(f"完整回答合成失败，回退原文摘录：{e}")
    return _format_rag_answer(question, context)


def _run_rag_pipeline(question: str, light_llm, heavy_llm) -> dict:
    from gene_formatter import (
        is_reference_genome_question, answer_reference_genome_versions,
        is_gene_version_question, answer_gene_version_question,
        is_factual_template_question, finalize_academic_structured_answer,
    )
    from zebrafish_prompt import is_academic_mode
    from config import ENSEMBL_RELEASE, REFERENCE_ASSEMBLY
    from rag_builder import get_rag
    from full_rag_chat import truncate_context

    result = empty_result()
    draft = ""
    if is_reference_genome_question(question):
        draft = answer_reference_genome_versions()
    elif is_gene_version_question(question):
        draft = answer_gene_version_question(question)

    if draft and is_academic_mode() and is_factual_template_question(question):
        result["answer"] = finalize_academic_structured_answer(
            draft,
            f"本地库 MySQL（{REFERENCE_ASSEMBLY}）",
            f"Ensembl release {ENSEMBL_RELEASE}",
        )
        return result

    raw_context = get_rag().get_context(question)
    result["context"] = truncate_context(raw_context, max_chars=4000)
    gene_detail = None
    try:
        from gene_toolkit import extract_gene_from_question, get_gene_detail
        gname = extract_gene_from_question(question)
        if gname:
            gene_detail = get_gene_detail(gname)
            result["gene_detail"] = gene_detail
    except Exception as e:
        logger.warning(f"基因详情加载失败：{e}")

    result["answer"] = _synthesize_rag_answer(
        question,
        raw_context,
        heavy_llm,
        gene_detail=gene_detail,
        draft_hint=draft,
    )
    return result


def multi_model_pipeline_answer(question: str, task_type: str, light_llm, heavy_llm) -> str:
    from config import TASK_TYPES
    if task_type == TASK_TYPES["DB_QUERY"]:
        return _run_db_pipeline(question, light_llm, heavy_llm)["answer"]
    elif task_type == TASK_TYPES["RAG_QUERY"]:
        return _run_rag_pipeline(question, light_llm, heavy_llm)["answer"]
    return "[错误] 未知任务类型"


def multi_model_pipeline_answer_detail(question: str, task_type: str, light_llm, heavy_llm) -> dict:
    from config import TASK_TYPES
    if task_type == TASK_TYPES["DB_QUERY"]:
        return _run_db_pipeline(question, light_llm, heavy_llm)
    elif task_type == TASK_TYPES["RAG_QUERY"]:
        return _run_rag_pipeline(question, light_llm, heavy_llm)
    return {**empty_result(), "answer": "[错误] 未知任务类型", "error": "未知任务类型"}


def multi_model_vote_answer(question: str, light_llm, heavy_llm) -> str:
    return multi_model_vote_answer_detail(question, light_llm, heavy_llm)["answer"]


def _fusion_persona(tier: str = "A") -> str:
    """融合模式人设：仅斑马鱼科学题用「领域专家」硬前缀。"""
    t = (tier or "A").upper()
    if t == "A":
        return "斑马鱼基因领域专家"
    return "靠谱的科学助手（专长斑马鱼研究，也可回答一般科学问题）"


def multi_model_vote_answer_detail(question: str, light_llm, heavy_llm) -> dict:
    """融合模式：有云端 API 时检索知识库后一次深度回答；否则双模型投票。"""
    from llm_loader import uses_remote_reasoning, get_reasoning_llm
    from full_rag_chat import truncate_context

    result = empty_result()
    tier = "A"
    try:
        from intent_router import classify_question_intent, is_general_lane_tier, TIER_E

        intent = classify_question_intent(question)
        tier = intent.get("tier") or "A"
        result["intent_tier"] = tier
        if tier == TIER_E:
            from intent_router import safety_refusal_reply

            result["answer"] = safety_refusal_reply()
            result["skip_enrich"] = True
            return result
        if is_general_lane_tier(tier):
            from hub_orchestrator import run_general_assistant

            return run_general_assistant(question, tier=tier)
    except Exception as e:
        logger.warning(f"[融合] intent 分流失败，按斑马鱼题继续：{e}")

    persona = _fusion_persona(tier)

    if uses_remote_reasoning():
        try:
            from rag_builder import get_rag
            from gene_toolkit import extract_gene_from_question, get_gene_detail
            ctx = get_rag().get_context(question)
            result["context"] = truncate_context(ctx, max_chars=4000)
            gdetail = None
            gname = extract_gene_from_question(question)
            if gname:
                gdetail = get_gene_detail(gname)
                result["gene_detail"] = gdetail
            result["answer"] = answer_with_reasoning(
                question, rag_context=ctx, gene_detail=gdetail,
            )
            return result
        except Exception as e:
            logger.warning(f"云端融合回答失败，回退本地双模型：{e}")

    if light_llm is heavy_llm:
        result["answer"] = light_llm.generate(f"{persona}，简洁回答问题：{question}")
        return result

    light_ans = light_llm.generate(f"{persona}，简洁回答问题：{question}")
    heavy_ans = heavy_llm.generate(f"{persona}，准确详细回答问题：{question}")
    result["answer"] = heavy_llm.generate(build_fusion_prompt(question, [light_ans, heavy_ans]))
    return result


# ===================== SQL相关工具 =====================
ALLOWED_TABLES = {
    "gene_basic", "gene_expression", "gene_go", "homology_gene",
    "gene_reference", "gene"
}
ALLOWED_JOIN_PAIRS = {
    ("gene_basic", "gene_expression"),
    ("gene_basic", "gene_go"),
    ("gene_basic", "homology_gene"),
    ("gene_basic", "gene_reference"),
}
ALLOWED_COLUMNS = {
    "gene_name", "gene_symbol", "tissue", "gene_function", "chromosome",
    "expression_level", "gene_id", "go_term", "go_category", "species",
    "homo_gene_name", "homology_score", "expression_stage",
    "ensembl_id", "zfin_id", "interpro_domain", "uniprot_id",
    "expression_summary", "pubmed_id", "title", "journal", "publish_year",
}


def is_sql_valid(sql: str) -> bool:
    table_matches = re.findall(r'(?:FROM|JOIN)\s+`?(\w+)`?', sql, re.IGNORECASE)
    table_list = [t.lower() for t in table_matches]
    for table in table_list:
        if table not in ALLOWED_TABLES:
            return False
    if len(table_list) > 1:
        main_table = table_list[0]
        for join_table in table_list[1:]:
            pair = tuple(sorted([main_table, join_table]))
            if pair not in ALLOWED_JOIN_PAIRS:
                return False
    field_pattern = re.compile(r'SELECT\s+(.*?)\s+FROM', re.IGNORECASE | re.DOTALL)
    field_match = field_pattern.search(sql)
    if field_match:
        raw_fields = re.split(r',\s*', field_match.group(1))
        for f in raw_fields:
            f_clean = re.sub(r'`|\s|.*\.', '', f).lower()
            if f_clean and f_clean not in ALLOWED_COLUMNS and "*" not in f:
                return False
    # 校验带别名的列是否真实属于该表（防 LLM 臆造 T2.gene_name 等）
    # gene_name/gene_symbol 只在 gene_basic；expression_level/stage/tissue 在 gene_expression
    col_owner = {
        "gene_name": "gene_basic", "gene_symbol": "gene_basic",
        "chromosome": "gene_basic", "gene_function": "gene_basic",
        "expression_summary": "gene_basic", "ensembl_id": "gene_basic",
        "zfin_id": "gene_basic", "uniprot_id": "gene_basic",
        "interpro_domain": "gene_basic",
        "expression_level": "gene_expression", "expression_stage": "gene_expression",
        "tissue": "gene_expression",
        "go_term": "gene_go", "go_category": "gene_go",
        "species": "homology_gene", "homo_gene_name": "homology_gene",
        "homology_score": "homology_gene",
        "pubmed_id": "gene_reference", "title": "gene_reference",
        "journal": "gene_reference", "publish_year": "gene_reference",
    }
    # 找出 SQL 中出现的表别名（gb/ge/gh/gg/gr/T1/T2 等）
    alias_to_table = {}
    for m in re.finditer(r'(?:FROM|JOIN)\s+`?(\w+)`?\s+(?:AS\s+)?`?([a-zA-Z]\w*)`?', sql, re.IGNORECASE):
        table, alias = m.group(1).lower(), m.group(2).lower()
        alias_to_table[alias] = table
    # 带别名前缀的列引用（如 gb.gene_name / T2.gene_name）
    for m in re.finditer(r'([a-zA-Z]\w*)\.(\w+)', sql):
        alias, col = m.group(1).lower(), m.group(2).lower()
        owner = col_owner.get(col)
        if owner and alias in alias_to_table and alias_to_table[alias] != owner:
            return False
    return True


def _build_corpus_stats_sql() -> str:
    """全库统计：基因数、表达记录数、GO 数、同源数、文献数（一行多列）。"""
    return (
        "SELECT "
        "(SELECT COUNT(*) FROM gene_basic) AS gene_count, "
        "(SELECT COUNT(*) FROM gene_expression) AS expr_count, "
        "(SELECT COUNT(*) FROM gene_go) AS go_count, "
        "(SELECT COUNT(*) FROM homology_gene) AS homo_count, "
        "(SELECT COUNT(*) FROM gene_reference) AS ref_count;"
    )


def build_fallback_sql(question: str) -> str:
    from gene_toolkit import extract_gene_from_question, normalize_gene_name
    gene = extract_gene_from_question(question)
    if gene:
        safe_name = normalize_gene_name(gene).replace("'", "''")
        q = question.lower()
        if any(k in q for k in ("表达", "组织", "fpkm", "tpm", "时期", "阶段")):
            return (
                f"SELECT ge.expression_stage, ge.tissue, ge.expression_level "
                f"FROM gene_basic gb JOIN gene_expression ge ON gb.gene_id=ge.gene_id "
                f"WHERE gb.gene_name = '{safe_name}' OR gb.gene_symbol = '{safe_name}' "
                f"ORDER BY ge.expression_level DESC;"
            )
        return (
            f"SELECT gb.gene_name, gb.chromosome, gb.gene_function, gb.expression_summary, "
            f"gb.ensembl_id, gb.zfin_id FROM gene_basic gb "
            f"WHERE gb.gene_name = '{safe_name}' OR gb.gene_symbol = '{safe_name}' LIMIT 1;"
        )
    gene_match = re.search(r'([a-zA-Z][a-zA-Z0-9]*)', question)
    if gene_match:
        gene_name = normalize_gene_name(gene_match.group(1).strip()).replace("'", "''")
        if re.fullmatch(r'[a-zA-Z][a-zA-Z0-9.]*', gene_name):
            return (
                f"SELECT ge.expression_stage, ge.tissue, ge.expression_level "
                f"FROM gene_basic gb JOIN gene_expression ge ON gb.gene_id=ge.gene_id "
                f"WHERE gb.gene_name = '{gene_name}' ORDER BY ge.expression_level DESC;"
            )
    return "SELECT gene_name, gene_function FROM gene_basic LIMIT 3;"


def build_sql_prompt(question: str) -> str:
    return f"""【强制规则】仅输出1条SELECT语句，末尾带分号，无其他文字。

【表结构（必须遵守，不要臆造列）】
- gene_basic(gb): gene_id, gene_name, gene_symbol, chromosome, gene_function, expression_summary, ensembl_id, zfin_id, uniprot_id, interpro_domain
- gene_expression(ge): gene_id, tissue, expression_stage, expression_level  ← 注意：此表【没有】gene_name/gene_symbol，需经 gene_id 关联 gene_basic
- gene_go: gene_id, go_term, go_category
- homology_gene: gene_id, species, homo_gene_name, homology_score
- gene_reference: gene_id, pubmed_id, title, journal, publish_year

【JOIN 规则】
- 表达/组织/TPM/FPKM 类问题：SELECT gb.gene_name, ge.tissue, ge.expression_stage, ge.expression_level FROM gene_basic gb JOIN gene_expression ge ON gb.gene_id=ge.gene_id WHERE gb.gene_name='<gene>' ORDER BY ge.expression_level DESC;
- 别名查询：WHERE gb.gene_name='<基因>' OR gb.gene_symbol='<基因>'（兼容 p53→tp53）
- 同源：JOIN homology_gene；GO：JOIN gene_go；文献：JOIN gene_reference
- 跨表引用必须用【表的别名】（gb./ge./gh./gg./gr.），且该列必须真实存在于该表

【LIKE 用法】
- 模糊匹配用单百分号：LIKE '%tp53%'（不要写 %%，不要造列）

【字段白名单】
gene_name,gene_symbol,tissue,gene_function,chromosome,expression_level,gene_id,go_term,go_category,species,homo_gene_name,homology_score,expression_stage,ensembl_id,zfin_id,interpro_domain,uniprot_id,expression_summary,pubmed_id,title,journal,publish_year

问题：{question}
SQL："""


def _looks_bgee_stage(stage: str) -> bool:
    """Bgee 行的 expression_stage 形如 "Bgee gold quality"；Atlas 行是发育时期名。"""
    return str(stage or "").strip().lower().startswith("bgee")


def _format_rows_for_answer(question: str, data: list) -> str:
    """表达查询：Bgee 为 Expression Score(0–100)，Expression Atlas 为整胚 TPM。"""
    if not data:
        return "无相关数据"
    q = question.lower()
    expr_keys = {"expression_stage", "expression_level", "tissue"}
    if not any(k in (data[0] or {}) for k in expr_keys):
        return "\n".join(str(row) for row in data)

    stages = []
    for row in data:
        stage = row.get("expression_stage")
        level = row.get("expression_level")
        tissue = row.get("tissue") or ""
        if stage is not None and level is not None:
            stages.append((str(stage), float(level), str(tissue)))

    if stages and any(k in q for k in ("表达", "组织", "tissue", "fpkm", "tpm")):
        stages.sort(key=lambda x: -x[1])
        bgee = [s for s in stages if _looks_bgee_stage(s[0])]
        atlas = [s for s in stages if not _looks_bgee_stage(s[0])]
        lines = []
        if bgee:
            lines.append(
                "【Bgee 表达分 Expression Score，0–100】越接近 100 表示越确定该处有表达，"
                "是表达存在性置信分，**不是**丰度 / TPM / FPKM / 拷贝数："
            )
            for stage, level, tissue in bgee[:12]:
                name = tissue or stage
                lines.append(f"- {name}: {level:g}/100")
            if len(bgee) > 12:
                lines.append(f"- … 共 {len(bgee)} 条 Bgee 表达调用")
        if atlas:
            lines.append(
                "【Expression Atlas TPM（E-ERAD-475 整胚发育时间序列为主，含少量标注组织样本）】"
                "按「时期｜组织」列出："
            )
            for stage, level, tissue in atlas[:12]:
                lines.append(f"- {stage}｜{tissue or 'whole embryo'}: {level:g} TPM")
            if len(atlas) > 12:
                lines.append(f"- … 共 {len(atlas)} 个时期")
        return "\n".join(lines)

    return "\n".join(str(row) for row in data)


def build_answer_prompt(question: str, data: list) -> str:
    from config import ANSWER_MAX_CHARS
    data_str = _format_rows_for_answer(question, data)
    return f"""基于查询结果，用简洁中文回答用户问题，控制在{ANSWER_MAX_CHARS}字以内。
数值必须带单位与来源：Bgee 为 Expression Score（0–100，表达存在性置信分，不是丰度/TPM）；Expression Atlas 为 TPM。
若数据为发育时期表达，请按时期列举，不要笼统说"胚胎组织"；不要编造库中不存在的成体组织（如肝脏、胸腺）除非查询结果里有。
问题：{question}
查询数据：
{data_str}
回答："""


def multi_model_answer(question: str, light_llm=None, heavy_llm=None) -> str:
    if light_llm is None or heavy_llm is None:
        from llm_loader import get_llm
        light_llm = get_llm(tier="light")
        heavy_llm = get_llm(tier="heavy")
    return multi_model_vote_answer(question, light_llm, heavy_llm)