# -*- coding: utf-8 -*-
import bootstrap  # noqa: F401

from common_utils import logger, clear_gpu_cache, llm_cache, safe_print, safe_input, is_exit_command
@llm_cache(maxsize=64)
def simple_chat(question: str, llm=None) -> str:
    from common_utils import answer_with_reasoning
    return answer_with_reasoning(question)

if __name__ == "__main__":
    logger.info("[成功] 斑马鱼基因聊天系统启动成功！")
    while True:
        user_input = safe_input("\n你：").strip()
        if is_exit_command(user_input):
            logger.info("👋 系统退出")
            clear_gpu_cache()
            break
        if not user_input:
            safe_print("AI：请输入有效问题！")
            continue
        ans = simple_chat(user_input)
        safe_print("\nAI：", ans)