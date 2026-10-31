# -*- coding: utf-8 -*-
"""命令行问答入口。"""
from core.entry import bootstrap_app

bootstrap_app()

from common_utils import logger, llm_cache, clear_gpu_cache, safe_print, safe_input


@llm_cache(maxsize=64)
def generate_sql(question: str) -> str:
    from llm_loader import get_llm
    llm = get_llm(tier="light")
    from common_utils import build_sql_prompt, is_sql_valid, build_fallback_sql
    raw_output = llm.generate(build_sql_prompt(question))
    logger.info(f"[调试] 模型原始输出：{raw_output[:200]}")

    import re
    sql_match = re.search(r'SELECT.*?[;；]', raw_output, re.IGNORECASE | re.DOTALL)
    sql = sql_match.group().strip().replace('；', ';') if sql_match else ""

    if sql and is_sql_valid(sql):
        logger.info(f"[成功] 提取并校验通过SQL：{sql}")
        return sql
    logger.warning("[警告] SQL校验失败，使用兜底模板")
    return build_fallback_sql(question)


@llm_cache(maxsize=64)
def generate_answer(question: str, data: list) -> str:
    from llm_loader import get_llm
    llm = get_llm(tier="light")
    from common_utils import build_answer_prompt
    return llm.generate(build_answer_prompt(question, data))


def llm_ask_db(user_question: str) -> str:
    if not user_question.strip():
        return "[错误] 请输入有效问题"
    try:
        from mysql_client import get_mysql_client
        mysql_client = get_mysql_client()
        sql = generate_sql(user_question)
        if not sql:
            return "[错误] 未能生成有效SQL语句"
        res = mysql_client.execute_sql(sql)
        if not res["success"]:
            return f"[错误] SQL执行失败：{res['error']}"
        if not res["data"]:
            return "[错误] 未查询到相关数据"
        answer = generate_answer(user_question, res["data"])
        return f"[成功] {answer}\n（共查询到{res['count']}条数据）"
    except Exception as e:
        logger.error(f"问答流程异常：{e}")
        return f"[错误] 系统异常：{str(e)}"


def main():
    from agent_scheduler import agent_ask, preload_all_resources
    preload_all_resources()
    from llm_loader import should_use_api_reasoning
    from common_utils import is_exit_command

    logger.info("[成功] 斑马鱼基因多模型智能系统启动成功！")
    if should_use_api_reasoning():
        logger.info("[成功] 深度推理已启用：云端 API + 本地轻量 0.5B 兜底")
    else:
        logger.warning("[警告] 未配置云端 API，仅使用本地轻量 0.5B 单模型")
    logger.info("[提示] 输入「退出」或 exit 结束；支持：数据库查询、知识库问答、多模型融合")
    while True:
        user_input = safe_input("\n你：").strip()
        if is_exit_command(user_input):
            logger.info("[已退出] 系统已退出")
            clear_gpu_cache()
            break
        if not user_input:
            safe_print("AI：请输入有效问题！")
            continue
        safe_print("\nAI：", agent_ask(user_input))


if __name__ == "__main__":
    main()
