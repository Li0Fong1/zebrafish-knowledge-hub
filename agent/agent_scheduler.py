# -*- coding: utf-8 -*-
import bootstrap  # noqa: F401

import os
import re
import warnings

warnings.filterwarnings("ignore")

from functools import lru_cache
import threading
from common_utils import logger, llm_cache
from llm_loader import get_llm, get_reasoning_llm
from config import TASK_TYPES

# ===================== 问答并发闸（多机并行） =====================
# 历史上这里是一把「全站串行锁」：任何时刻只允许一问，第二台机器提问直接 503。
# 现改由 agent/chat_gate.py 统一调度：多用户/多机并行，同一用户仍串行。
# 下面这几个名字保留，只为兼容旧引用（部署树里可能还 import 它们），主链路已不再依赖。
_chat_run_lock = threading.Lock()  # 兼容旧引用：不再用于串行问答
_chat_busy_since = None  # type: Optional[float]
_chat_busy_preview = ""
_chat_busy_meta_lock = threading.Lock()


def _gate_status() -> dict:
    """取并发闸状态；任何异常都退化为空字典，绝不让 /api/health 挂掉。"""
    try:
        from chat_gate import status as _st
    except Exception:
        try:
            from agent.chat_gate import status as _st  # type: ignore
        except Exception:
            return {}
    try:
        return _st() or {}
    except Exception:
        return {}


def chat_busy_status() -> dict:
    """供 /api/health 与前端展示：并发是否已满、还有几人在排队。

    语义已变：`chat_busy` 不再表示「有人在用」，而是「并发打满、新问需要排队」，
    这样前端不必再阻断输入，只要提示「排队中」即可。
    """
    import time as _time

    st = _gate_status()
    busy = bool(st.get("chat_busy"))
    with _chat_busy_meta_lock:
        since = _chat_busy_since
        preview = _chat_busy_preview
    elapsed = int(_time.time() - since) if (busy and since) else 0
    out = {
        "chat_busy": busy,
        "chat_busy_seconds": elapsed or int(st.get("chat_queue_oldest_wait_sec") or 0),
        "chat_busy_preview": (preview if busy else "") or (st.get("chat_busy_preview") or ""),
    }
    out.update(st)
    return out


def _mark_chat_busy(question: str) -> None:
    import time as _time

    global _chat_busy_since, _chat_busy_preview
    with _chat_busy_meta_lock:
        _chat_busy_since = _time.time()
        _chat_busy_preview = (question or "").strip()


def _mark_chat_idle() -> None:
    global _chat_busy_since, _chat_busy_preview
    with _chat_busy_meta_lock:
        _chat_busy_since = None
        _chat_busy_preview = ""


def _collect_gene_facts(question: str, max_genes: int = 6) -> str:
    """收集问题/上下文中涉及基因的本地库权威记录，约束 LLM 不编造。"""
    from gene_toolkit import (
        extract_genes_from_question, get_gene_detail, normalize_gene_name,
    )
    seen = set()
    rows = []
    for g in extract_genes_from_question(question or "", limit=max_genes + 2):
        gn = normalize_gene_name(g)
        if not gn or gn in seen:
            continue
        seen.add(gn)
        d = get_gene_detail(gn)
        if not d:
            continue
        b = d["basic"]
        rows.append(
            f"{b.get('gene_name')}: Ensembl={b.get('ensembl_id') or '-'} "
            f"Chr={b.get('chromosome') or '-'} ZFIN={b.get('zfin_id') or '-'}"
        )
        if len(rows) >= max_genes:
            break
    return "\n".join(rows)


def _llm_concurrency() -> int:
    """推理并发上限。

    · 显式配置 CHAT_LLM_CONCURRENCY 优先；
    · 未配置时按云端推理（网络 IO 密集）取全局并发数。
    """
    env = (os.getenv("CHAT_LLM_CONCURRENCY") or "").strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    try:
        from chat_gate import MAX_CONCURRENCY
    except Exception:
        MAX_CONCURRENCY = 4
    return max(1, MAX_CONCURRENCY)


class AgentScheduler:
    def __init__(self, max_workers: int = 1):
        # 并发闸接管「一问一条车道」的调度（见 chat_gate），这里不再做全站串行。
        # self.lock 只保护本地模型推理：云端推理放开并行，本地 7B 默认串行。
        self.lock = threading.Semaphore(_llm_concurrency())
        self._heavy_lock = threading.Lock()

        # 先加载轻量模型；深度推理由云端 API 承担
        self.light_llm = get_llm(tier="light")
        self._heavy_llm = None
        self._reasoning_llm = None
        self.is_heavy_degraded = True

        try:
            self._reasoning_llm = get_reasoning_llm()
            self.is_heavy_degraded = False
            logger.info(f"   深度推理：云端 API（{os.getenv('LLM_API_MODEL', 'deepseek-chat')}）")
        except Exception as e:
            logger.warning(f"[警告] 云端 API 初始化失败，回退本地轻量模型：{e}")
            self._heavy_llm = self.light_llm

        logger.info(f"[成功] 多线程智能体调度器启动，最大线程数：{max_workers}")
        logger.info(f"   轻量模型：已就绪")

    @property
    def heavy_llm(self):
        if self._reasoning_llm is not None:
            return self._reasoning_llm
        self._ensure_heavy()
        return self._heavy_llm or self.light_llm

    def _load_heavy_background(self):
        self._ensure_heavy()

    def _ensure_heavy(self):
        if self._reasoning_llm is not None or self._heavy_llm is not None:
            return
        with self._heavy_lock:
            if self._heavy_llm is not None or self._reasoning_llm is not None:
                return
            try:
                self._reasoning_llm = get_reasoning_llm()
                self.is_heavy_degraded = False
                logger.info("[成功] 深度推理已就绪（云端 API）")
            except Exception as e:
                logger.warning(f"云端 API 不可用，回退本地轻量模型：{e}")
                self._heavy_llm = self.light_llm
                self.is_heavy_degraded = True

    def _gene_detail_answer(self, question: str) -> dict:
        from common_utils import empty_result
        from gene_toolkit import (
            extract_gene_from_question, extract_genes_from_question,
            get_gene_detail, id_convert, normalize_gene_name,
        )
        from gene_formatter import (
            answer_from_gene_detail, answer_from_id_resolve, answer_gene_comparison,
            answer_reference_genome_versions, answer_gene_version_question,
            is_id_mapping_question, is_gene_comparison_question,
            is_reference_genome_question, is_gene_version_question,
            is_biology_mechanism_question, is_citation_display_question,
            finalize_academic_structured_answer,
        )
        from gene_id_mapper import reference_genome_info, resolve_gene_identifier
        from common_utils import answer_with_reasoning
        from llm_loader import uses_remote_reasoning
        from zebrafish_prompt import is_academic_mode
        from config import ENSEMBL_RELEASE, REFERENCE_ASSEMBLY

        result = empty_result()

        if is_citation_display_question(question):
            from zebrafish_prompt import answer_citation_display_question
            result["answer"] = answer_citation_display_question(question)
            return result

        if is_biology_mechanism_question(question):
            from common_utils import multi_model_pipeline_answer_detail
            return multi_model_pipeline_answer_detail(
                question, TASK_TYPES["RAG_QUERY"], self.light_llm, self.heavy_llm
            )

        if is_reference_genome_question(question):
            draft = answer_reference_genome_versions()
            if is_academic_mode():
                result["answer"] = finalize_academic_structured_answer(
                    draft,
                    f"本地库 MySQL（{REFERENCE_ASSEMBLY}）",
                    f"Ensembl release {ENSEMBL_RELEASE}",
                )
            else:
                result["answer"] = (
                    answer_with_reasoning(question, draft_hint=draft)
                    if uses_remote_reasoning() else draft
                )
            return result

        if is_gene_version_question(question):
            draft = answer_gene_version_question(question)
            if is_academic_mode():
                result["answer"] = finalize_academic_structured_answer(
                    draft,
                    "本地基因库",
                    f"Ensembl release {ENSEMBL_RELEASE}",
                )
            else:
                result["answer"] = (
                    answer_with_reasoning(question, draft_hint=draft)
                    if uses_remote_reasoning() else draft
                )
            return result

        if is_gene_comparison_question(question):
            result["answer"] = answer_gene_comparison(question)
            genes = extract_genes_from_question(question)
            if genes:
                detail = get_gene_detail(normalize_gene_name(genes[0]))
                if detail:
                    result["gene_detail"] = detail
            return result

        # 追问/指代类（剥离上下文后无新基因目标）：
        # 交由大模型结合对话上下文解释，避免被「关于基因 xxx：」前缀
        # 误导去查单基因 / ID 映射，造成答非所问。
        from gene_formatter import is_explanatory_followup, _strip_dialog_context
        if is_explanatory_followup(question):
            try:
                db_text = _collect_gene_facts(question)
                result["answer"] = answer_with_reasoning(
                    question,
                    db_text=db_text,
                    draft_hint=(
                        "这是用户在追问/质疑之前的回答。请结合对话上下文"
                        "（上文基因、上一问、上一答）解释清楚：之前结论是什么、"
                        "为什么这么说，直接、明确、有条理地回复用户。"
                        "若在问参考文献数量/是否只有一篇：根据上文回答里实际列出的"
                        "「参考文献」条数说明，不要把 PMID 数字当成基因 ID 去查。"
                        "以下提供了本地库中相关基因的权威记录，回答时以这些数据为准，"
                        "不要编造 Ensembl ID、染色体号或 ZFIN ID。"
                    ),
                )
                result["task_type"] = TASK_TYPES["GENERAL"]
                return result
            except Exception as e:
                logger.warning(f"[追问解释] 大模型解释失败，回退模板路由：{e}")

        # 只在用户当前问句里提基因/ID，忽略「上文PMID：…」注入块
        clean_q = _strip_dialog_context(question)
        gene = extract_gene_from_question(clean_q)
        if not gene:
            for pat in (
                r"ZDB-GENE-\d+-\d+",
                r"ENS[A-Z]+\d+(?:\.\d+)?",
                r"(?:NM|NR|NP|XM|XR|NC)_\d+(?:\.\d+)?",
            ):
                m = re.search(pat, clean_q, re.I)
                if m:
                    gene = m.group(0)
                    break
            # 裸数字仅在明确 ID 语境下才当 NCBI Gene ID（禁止把 PMID 当基因号）
            if not gene:
                m = re.search(r"\b(\d{4,9})\b", clean_q)
                if m and any(
                    k in clean_q.lower()
                    for k in ("ncbi", "gene id", "基因id", "entrez", "gi:", "id转换", "id 转换")
                ):
                    gene = m.group(1)
                elif m and re.search(r"PMID|参考文献|文献|脚注|doi", clean_q, re.I):
                    gene = None

        if not gene:
            result["answer"] = (
                "[提示] 未识别基因或 ID。示例：\n"
                "  · 符号：tp53、p53、shh\n"
                "  · Ensembl：ENSDARG0000000001\n"
                "  · ZFIN：ZDB-GENE-990415-270（tp53）\n"
                "  · NCBI Gene：30590\n"
                "  · RefSeq：NM_131558"
            )
            return result

        raw_gene = gene
        if not is_id_mapping_question(question) and re.fullmatch(r"\d{4,9}", str(gene)):
            is_id_q = True
        else:
            is_id_q = is_id_mapping_question(question)

        gene = normalize_gene_name(gene) if not re.match(r"^(ZDB-GENE|ENS|NM_|NR_|NP_)", raw_gene, re.I) else raw_gene

        if is_id_q or re.match(r"^(ZDB-GENE|ENS|(?:NM|NR|NP|XM|XR|NC)_|\d+$)", raw_gene, re.I):
            resolved = resolve_gene_identifier(raw_gene if raw_gene != gene else gene)
            result["id_mapping"] = resolved
            if resolved.get("resolved"):
                result["answer"] = answer_from_id_resolve(question, resolved)
                if resolved.get("canonical"):
                    detail = get_gene_detail(resolved["canonical"]["gene_name"])
                    if detail:
                        result["gene_detail"] = detail
                return result
            conv = id_convert(gene)
            result["answer"] = answer_from_id_resolve(question, conv) if conv.get("resolved") else (
                f"[错误] 没有解析到基因 {gene}：请确认符号拼写，或改用 Ensembl / ZFIN / NCBI 编号"
            )
            return result

        detail = get_gene_detail(gene)
        if not detail:
            conv = id_convert(gene)
            if conv.get("canonical"):
                detail = get_gene_detail(conv["canonical"]["gene_name"])
            elif conv.get("matches"):
                detail = get_gene_detail(conv["matches"][0]["gene_name"])
        if not detail:
            result["answer"] = f"[错误] 数据库中未找到基因：{gene}"
            return result

        ref = reference_genome_info()
        resolved = resolve_gene_identifier(detail["basic"]["gene_name"])
        detail["id_mapping"] = resolved
        result["gene_detail"] = detail
        # 深度回答：有 DeepSeek 时，把结构化基因数据 + 模板骨架喂给大模型，
        # 生成流畅、完整、直击问题的叙述（而非机械列表），并对缺失数据如实说明
        if uses_remote_reasoning():
            try:
                from common_utils import answer_with_reasoning
                skeleton = answer_from_gene_detail(question, detail, reference=ref)
                result["answer"] = answer_with_reasoning(
                    question,
                    gene_detail=detail,
                    draft_hint=skeleton,
                )
            except Exception as e:
                logger.warning(f"[基因详情] DeepSeek 综合失败，回退模板：{e}")
                result["answer"] = answer_from_gene_detail(question, detail, reference=ref)
        else:
            result["answer"] = answer_from_gene_detail(question, detail, reference=ref)
        return result

    def _blast_answer(self, question: str) -> dict:
        from common_utils import empty_result
        from seq_toolkit import run_local_blast, format_blast_answer, validate_sequence
        import re

        result = empty_result()
        seq = re.sub(r"(?i)blast|序列比对|比对|查询序列|核酸序列|cdna|mrna|：|:", "", question)
        seq = re.sub(r"\s+", "", seq)
        m = re.search(r"[ATCGNatcgn]{20,}", question)
        if m:
            seq = m.group(0)
        ok, normalized = validate_sequence(seq)
        if not ok:
            result["answer"] = (
                "[提示] 请在问题中粘贴核酸序列（≥20bp），或使用 Web 工具箱「序列比对」。"
                f" 错误：{normalized}"
            )
            return result
        blast_result = run_local_blast(normalized)
        result["blast"] = blast_result
        result["answer"] = format_blast_answer(blast_result)
        if blast_result.get("success") and blast_result.get("hits"):
            summary = self.heavy_llm.generate(
                f"根据以下 BLAST 结果用中文简要解读（150字内）：\n{result['answer']}"
            )
            result["answer"] = f"{result['answer']}\n\n[解读] {summary}"
        return result

    def _enrichment_answer(self, question: str) -> dict:
        from common_utils import empty_result
        from enrichment_toolkit import go_enrichment, kegg_enrichment, format_enrichment_answer
        from gene_toolkit import extract_genes_from_question

        result = empty_result()
        genes = extract_genes_from_question(question, limit=12)
        want_kegg = any(k in question.lower() for k in ["kegg", "通路", "pathway"])
        go_res = go_enrichment(genes if genes else None)
        kegg_res = kegg_enrichment(genes) if genes and want_kegg else None
        if not want_kegg and genes:
            kegg_res = kegg_enrichment(genes)
        result["enrichment"] = {"go": go_res, "kegg": kegg_res}
        result["answer"] = format_enrichment_answer(go_res, kegg_res)
        if result["answer"].startswith("[结果]") and not genes:
            result["answer"] = (
                "[提示] 功能富集分析已运行（全库 GO 背景）。"
                " 指定基因示例：对 tp53 hsp70 myl7 做 GO 富集"
            ) + "\n" + result["answer"]
        return result

    def _execute_task_detail(self, task_type: str, question: str) -> dict:
        llm_tasks = {
            TASK_TYPES["DB_QUERY"],
            TASK_TYPES["RAG_QUERY"],
            TASK_TYPES["MULTI_MODEL"],
            TASK_TYPES["GENERAL"],
        }

        def _body() -> dict:
            if task_type == TASK_TYPES["DB_QUERY"]:
                from common_utils import multi_model_pipeline_answer_detail
                result = multi_model_pipeline_answer_detail(
                    question, task_type, self.light_llm, self.heavy_llm
                )
            elif task_type == TASK_TYPES["RAG_QUERY"]:
                from common_utils import multi_model_pipeline_answer_detail
                result = multi_model_pipeline_answer_detail(
                    question, task_type, self.light_llm, self.heavy_llm
                )
            elif task_type == TASK_TYPES["MULTI_MODEL"]:
                from common_utils import multi_model_vote_answer_detail
                result = multi_model_vote_answer_detail(
                    question, self.light_llm, self.heavy_llm
                )
            elif task_type == TASK_TYPES["GENE_DETAIL"]:
                result = self._gene_detail_answer(question)
            elif task_type == TASK_TYPES["BLAST"]:
                result = self._blast_answer(question)
            elif task_type == TASK_TYPES["ENRICHMENT"]:
                result = self._enrichment_answer(question)
            else:
                from llm_chat import simple_chat
                result = {"answer": simple_chat(question, self.light_llm)}
                result.update({
                    "sql": None, "data": None, "row_count": None,
                    "context": None, "error": None,
                })
            result["task_type"] = task_type
            return result

        try:
            if task_type in llm_tasks:
                with self.lock:
                    return _body()
            return _body()
        except Exception as e:
            logger.error(f"任务执行失败：{e}")
            return {
                "answer": f"[错误] 系统异常：{str(e)}",
                "task_type": task_type,
                "sql": None, "data": None, "row_count": None,
                "context": None, "error": str(e),
            }

    def _execute_task(self, task_type: str, question: str) -> str:
        return self._execute_task_detail(task_type, question)["answer"]

    def run_task_detail(
        self, question: str, task_type: str = TASK_TYPES["GENERAL"],
    ) -> dict:
        """同步执行（一问一答，串行锁保证不堆积）。"""
        return self._execute_task_detail(task_type, question)

    def submit_task_detail(
        self, question: str, task_type: str = TASK_TYPES["GENERAL"], **_,
    ) -> dict:
        return self.run_task_detail(question, task_type)


# 全局调度器单例（延迟创建，避免 import 时阻塞 Web /health）
_scheduler_instance = None
_scheduler_lock = threading.Lock()


def get_agent_scheduler() -> AgentScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        with _scheduler_lock:
            if _scheduler_instance is None:
                _scheduler_instance = AgentScheduler(max_workers=1)
    return _scheduler_instance


class _AgentSchedulerProxy:
    def __getattr__(self, name):
        return getattr(get_agent_scheduler(), name)


agent_scheduler = _AgentSchedulerProxy()


# ===================== 资源预加载 =====================
_preload_done = False
_preload_lock = threading.Lock()
_rag_ready = False
_rag_loading = False
_heavy_loading = False


def get_resource_status() -> dict:
    sched = _scheduler_instance
    return {
        "rag_ready": _rag_ready,
        "rag_loading": _rag_loading and not _rag_ready,
        "heavy_loading": _heavy_loading and bool(
            sched and sched.is_heavy_degraded and sched._heavy_llm is None
        ),
    }


def preload_all_resources():
    """后台预加载 MySQL 与 RAG 知识库（不阻塞 Web 就绪）。"""
    global _preload_done, _rag_ready, _rag_loading, _heavy_loading
    with _preload_lock:
        if _preload_done:
            return
        _preload_done = True
    _rag_loading = True

    def _bg():
        global _rag_ready, _rag_loading, _heavy_loading
        logger.info("[预热] 后台加载数据库与 RAG 知识库…")
        try:
            from mysql_client import get_mysql_client
            get_mysql_client()
            from rag_builder import get_rag
            get_rag()
            _rag_ready = True
            logger.info("[成功] RAG 知识库预热完成")
        except Exception as e:
            logger.warning(f"RAG 预加载失败，首次知识库问答时将按需加载：{e}")
        finally:
            _rag_loading = False

    threading.Thread(target=_bg, daemon=True).start()


# ===================== 智能路由优化 =====================
@lru_cache(maxsize=256)
def route_task(question: str) -> str:
    db_keywords_weight = {
        "哪个": 2, "什么": 1, "多少": 1, "在哪里": 2,
        "功能": 3, "表达": 3, "染色体": 3, "组织": 3,
        "表达量": 3, "定位": 2, "查询": 2, "数据": 2
    }
    rag_keywords_weight = {
        "斑马鱼": 2, "研究": 2, "方法": 2, "模型": 2,
        "同源性": 5, "命名": 3, "理解": 3, "原理": 3,
        "作用机制": 3, "应用": 2, "为什么": 3
    }
    exclude_db_keywords = ["你对", "理解", "什么是", "什么叫", "为什么", "是什么", "介绍"]

    try:
        from gene_formatter import (
            is_reference_genome_question, is_gene_version_question,
            is_biology_mechanism_question, is_citation_display_question,
        )
        if is_citation_display_question(question) or is_biology_mechanism_question(question):
            return TASK_TYPES["RAG_QUERY"]
        if is_reference_genome_question(question) or is_gene_version_question(question):
            return TASK_TYPES["GENE_DETAIL"]
    except Exception:
        pass

    question_lower = question.lower()
    if re.search(r"ZDB-GENE-\d+-\d+", question, re.I):
        return TASK_TYPES["GENE_DETAIL"]
    if re.search(r"ENS[A-Z]+\d+", question, re.I):
        return TASK_TYPES["GENE_DETAIL"]
    if re.search(r"(?:NM|NR|NP|XM|XR|NC)_\d+", question, re.I):
        return TASK_TYPES["GENE_DETAIL"]
    if re.fullmatch(r"\s*\d{4,9}\s*", question.strip()):
        return TASK_TYPES["GENE_DETAIL"]
    if any(k in question_lower for k in (
        "命名混乱", "官方符号", "叫什么", "哪个基因", "同名", "别名对应",
        "id映射", "id 映射", "跨库", "版本号", "grcz10", "grcz11", "grcz12", "grcz12tu",
        "同一个", "同一基因", "一样吗", "是否相同", "相同吗", "是不是同一",
        "区别", "旁系", "对比", "比较",
    )):
        return TASK_TYPES["GENE_DETAIL"]

    # 含两个及以上基因符号的比较问句
    if re.search(r"\b[a-zA-Z][a-zA-Z0-9]{1,12}\b", question):
        gene_tokens = re.findall(r"\b[a-zA-Z][a-zA-Z0-9]{1,15}\b", question)
        stop = {"gene", "dna", "rna", "go", "kegg", "blast", "and", "or", "the", "vs"}
        gene_tokens = [t.lower() for t in gene_tokens if t.lower() not in stop and len(t) >= 2]
        if len(set(gene_tokens)) >= 2 and any(
            k in question_lower for k in ("和", "与", "跟", "vs", "对比", "比较", "区别", "同一", "一样")
        ):
            return TASK_TYPES["GENE_DETAIL"]

    if re.search(r"[ATCGNatcgn]{20,}", question) or any(
        k in question_lower for k in ["blast", "序列比对", "核酸序列", "cdna", "mrna"]
    ):
        return TASK_TYPES["BLAST"]

    if any(k in question_lower for k in ["富集", "kegg", "go富集", "通路分析", "pathway", "功能富集"]):
        return TASK_TYPES["ENRICHMENT"]

    # 方法/原理/编辑技术类 → 知识库（避免误走基因详情）
    if any(k in question_lower for k in (
        "crispr", "基因编辑", "编辑方法", "转基因", "显微注射", "protocol",
        "实验步骤", "怎么做", "如何做", "原理", "技术",
    )):
        return TASK_TYPES["RAG_QUERY"]

    # 带明确基因符号的「是什么/功能」→ 基因详情（如 hsp70基因是什么）
    try:
        from gene_toolkit import extract_gene_from_question
        if extract_gene_from_question(question) and any(
            k in question_lower for k in ("是什么", "什么叫", "什么是", "介绍", "功能", "干什么", "啥基因")
        ):
            return TASK_TYPES["GENE_DETAIL"]
    except Exception:
        pass

    # 含明确基因符号的表达/组织题才走基因详情。mRNA、GRCz11 这类词不是基因。
    try:
        from gene_toolkit import extract_gene_from_question as _eg_expr
        _expr_gene = _eg_expr(question)
    except Exception:
        _expr_gene = None
    if _expr_gene and any(
        k in question_lower for k in ["表达", "组织", "fpkm", "tpm", "哪个时期", "哪些时期"]
    ):
        return TASK_TYPES["GENE_DETAIL"]

    gene_detail_keywords = [
        "基因详情", "基因信息", "查询基因", "介绍基因", "id转换", "符号转换",
        "ensembl", "zfin", "命名", "别名", "ncbi", "refseq", "官方符号",
    ]
    if any(k in question_lower for k in gene_detail_keywords):
        return TASK_TYPES["GENE_DETAIL"]
    if re.search(r"[a-zA-Z][a-zA-Z0-9]{1,12}", question) and any(
        k in question_lower for k in ["详情", "信息", "注释", "同源", "文献", "表达数据"]
    ):
        return TASK_TYPES["GENE_DETAIL"]

    # 强制排除判断
    if any(k in question_lower for k in exclude_db_keywords):
        if any(k in question_lower for k in rag_keywords_weight):
            return TASK_TYPES["RAG_QUERY"]
        if re.search(r"[a-zA-Z]+\d*", question):
            return TASK_TYPES["RAG_QUERY"]
        return TASK_TYPES["MULTI_MODEL"]

    # 加权评分
    db_score = sum(w for k, w in db_keywords_weight.items() if k in question_lower)
    rag_score = sum(w for k, w in rag_keywords_weight.items() if k in question_lower)

    # 无具体基因符号的数据库查询：仅限全库统计类问题；
    # 其余（科普/机制/主题类，如“斑马鱼关于条纹形状表达”）降级为文献 RAG，
    # 避免 LLM 把物种名当基因名生成荒谬 SQL（gene_name='斑马鱼'）或输出 SQL 残句。
    _has_gene = False
    try:
        from gene_toolkit import extract_gene_from_question as _egx
        _has_gene = _egx(question) is not None
    except Exception:
        pass

    # 路由决策
    if db_score > rag_score and db_score >= 2:
        if not _has_gene and not any(k in question_lower for k in (
            "全库", "多少基因", "共多少", "库里有", "统计", "总共",
            "收录", "数据怎么样", "数据如何", "库里的", "数据情况", "整体数据",
        )):
            return TASK_TYPES["RAG_QUERY"]
        return TASK_TYPES["DB_QUERY"]
    elif rag_score > db_score and rag_score >= 2:
        return TASK_TYPES["RAG_QUERY"]
    elif db_score > 0 or rag_score > 0:
        return TASK_TYPES["RAG_QUERY"] if re.search(r"[a-zA-Z]+\d*", question) else TASK_TYPES["MULTI_MODEL"]
    else:
        return TASK_TYPES["GENERAL"]


# ===================== 统一入口 =====================
def agent_ask(question: str) -> str:
    return agent_ask_detail(question)["answer"]


def agent_ask_detail(question: str, history: list | None = None) -> dict:
    from hub_orchestrator import run_hub_orchestration, run_general_assistant
    from intent_router import (
        classify_question_intent,
        safety_refusal_reply,
        TIER_A,
        TIER_B,
        TIER_C,
        TIER_D,
        TIER_E,
    )
    from zebrafish_prompt import (
        is_system_meta_question,
        is_capability_question,
        answer_capability,
        SYSTEM_META_REPLY,
        OFF_TOPIC_REPLY,
    )
    from common_utils import empty_result
    from config import TASK_TYPES

    if is_capability_question(question):
        # 「你能做什么」用固定文案：模型自由发挥会许下平台没有的能力
        logger.info("[路由] 能力自述问题，短路（无文献包）")
        return {
            **empty_result(),
            "answer": answer_capability(question),
            "task_type": TASK_TYPES["GENERAL"],
            "answer_scope": "meta",
            "hub_mode": False,
            "intent_tier": TIER_B,
            "skip_enrich": True,
        }

    if is_system_meta_question(question):
        logger.info("[路由] 系统元问题，短路（无文献包）")
        return {
            **empty_result(),
            "answer": SYSTEM_META_REPLY,
            "task_type": TASK_TYPES["GENERAL"],
            "answer_scope": "meta",
            "hub_mode": False,
            "intent_tier": TIER_B,
            "skip_enrich": True,
        }

    intent = classify_question_intent(question, history=history)
    tier = intent.get("tier") or TIER_A
    logger.info(
        f"[路由] intent={tier}/{intent.get('name')} "
        f"conf={intent.get('confidence')} reasons={intent.get('reasons')}"
    )

    if tier == TIER_E:
        return {
            **empty_result(),
            "answer": safety_refusal_reply(),
            "task_type": TASK_TYPES["GENERAL"],
            "answer_scope": "safety",
            "intent_tier": TIER_E,
            "skip_enrich": True,
        }
    if tier == TIER_B:
        from zebrafish_prompt import (
            is_hub_product_howto_question,
            answer_hub_product_howto,
            answer_citation_display_question,
        )
        try:
            from gene_formatter import is_citation_display_question
        except Exception:
            is_citation_display_question = lambda _q: False  # noqa: E731
        if is_citation_display_question(question):
            ans = answer_citation_display_question(question, history)
        elif is_capability_question(question):
            ans = answer_capability(question)
        elif is_hub_product_howto_question(question):
            ans = answer_hub_product_howto(question)
        else:
            ans = SYSTEM_META_REPLY
        return {
            **empty_result(),
            "answer": ans,
            "task_type": TASK_TYPES["GENERAL"],
            "answer_scope": "meta",
            "hub_mode": False,
            "intent_tier": TIER_B,
            "skip_enrich": True,
        }
    if tier in (TIER_C, TIER_D):
        return run_general_assistant(question, tier=tier, history=history)
    if tier != TIER_A:
        # 未知 tier：兜底通用车道，避免罐头拒答
        logger.warning(f"[路由] 未知 intent tier={tier}，走通用车道")
        try:
            return run_general_assistant(question, tier=TIER_D, history=history)
        except Exception:
            return {
                **empty_result(),
                "answer": OFF_TOPIC_REPLY,
                "task_type": TASK_TYPES["GENERAL"],
                "intent_tier": tier,
                "skip_enrich": True,
            }
    return run_hub_orchestration(question)