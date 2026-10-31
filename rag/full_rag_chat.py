# -*- coding: utf-8 -*-
import bootstrap  # noqa: F401

import os
os.environ["FAISS_DISABLE_AVX2"] = "1"

from common_utils import logger, clear_gpu_cache, llm_cache, safe_print, safe_input, is_exit_command


def truncate_context(context: str, max_chars: int = None) -> str:
    from config import RAG_CONTEXT_MAX_CHARS
    limit = max_chars if max_chars is not None else RAG_CONTEXT_MAX_CHARS
    if not context or len(context) <= limit:
        return context or ""
    return context[:limit].rstrip() + "…"


def build_rag_prompt(question: str, max_chars: int = None, context: str = None) -> str:
    from rag_builder import get_rag
    from config import ANSWER_MAX_CHARS
    limit = max_chars or ANSWER_MAX_CHARS
    if context is None:
        context = get_rag().get_context(question)
    context = truncate_context(context)
    prompt = f"""参考以下斑马鱼知识库内容，用简洁中文回答用户问题（控制在{limit}字以内）：
参考资料：
{context if context else '无相关参考资料'}

用户问题：{question}
回答："""
    return prompt


@llm_cache(maxsize=64)
def rag_chat(question: str, llm=None) -> str:
    if llm is None:
        from llm_loader import get_llm
        llm = get_llm(tier="heavy")
    prompt = build_rag_prompt(question)
    answer = llm.generate(prompt)
    return answer if answer else "未能找到相关答案"


if __name__ == "__main__":
    logger.info("[成功] 斑马鱼基因RAG问答系统启动成功！")
    logger.info("[提示] 输入「退出」或 exit 结束，输入问题即可查询")
    while True:
        user_input = safe_input("\n你：").strip()
        if is_exit_command(user_input):
            logger.info("👋 系统退出")
            clear_gpu_cache()
            break
        if not user_input:
            safe_print("AI：请输入有效问题！")
            continue
        answer = rag_chat(user_input)
        safe_print("\nAI：", answer)