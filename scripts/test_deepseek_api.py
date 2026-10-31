# -*- coding: utf-8 -*-
"""DeepSeek / OpenAI 兼容 API 连通性测试。"""
from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from llm_loader import get_api_llm, get_reasoning_llm, should_use_api_reasoning
from zebrafish_prompt import ZEBRAFISH_SYSTEM_PROMPT


def main():
    safe_print("【云端大模型 API 检测】")
    if not should_use_api_reasoning():
        safe_print("  未启用：请在 .env 设置 LLM_MODE=hybrid 与 LLM_API_KEY")
        return 1
    safe_print(f"  System Prompt 长度：{len(ZEBRAFISH_SYSTEM_PROMPT)} 字")
    try:
        api = get_api_llm()
        ans = api.generate("用一句话说明斑马鱼 tp53 基因的作用。", max_new_tokens=120)
        safe_print(f"  API 直连：成功\n  回答：{ans[:200]}")
    except Exception as e:
        safe_print(f"  API 直连：失败 — {e}")
        return 1
    try:
        r = get_reasoning_llm()
        ans2 = r.generate("GRCz11 是什么？", max_new_tokens=100)
        safe_print(f"  推理链路：成功\n  回答：{ans2[:200]}")
    except Exception as e:
        safe_print(f"  推理链路：失败 — {e}")
        return 1
    safe_print("\n  全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
