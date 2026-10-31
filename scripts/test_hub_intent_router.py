# -*- coding: utf-8 -*-
"""Hub 意图分层路由验收：A/B/C/D/E，以及不再返回罐头 OFF_TOPIC_REPLY。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "core", ROOT / "agent", ROOT / "database", ROOT / "llm", ROOT / "api"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

RESULTS = []


def rec(name, ok, detail=""):
    RESULTS.append((name, "PASS" if ok else "FAIL", detail))
    print(("PASS" if ok else "FAIL"), name, str(detail)[:220], flush=True)


def test_classify_tiers():
    from intent_router import classify_question_intent, TIER_A, TIER_B, TIER_C, TIER_D

    cases = [
        ("hsp70 功能", TIER_A),
        ("斑马鱼 tp53 在哪些组织表达", TIER_A),
        ("ZDB-GENE-990415-270 是什么", TIER_A),
        ("你是谁", TIER_B),
        ("小鼠 p53", TIER_C),
        ("PCR 退火温度怎么选", TIER_C),
        ("今天天气怎么样", TIER_D),
        ("帮我写一段 python 排序代码", TIER_D),
        ("hello", TIER_D),
    ]
    for q, want in cases:
        got = classify_question_intent(q)
        ok = got.get("tier") == want
        rec(f"tier:{want}:{q[:24]}", ok, f"got={got}")


def test_followup_history_to_a():
    from intent_router import classify_question_intent, TIER_A

    hist = [
        {"role": "user", "content": "斑马鱼 hsp70 的功能是什么"},
        {"role": "assistant", "content": "hsp70 是热休克蛋白…"},
    ]
    got = classify_question_intent("它在哪些组织表达？", history=hist)
    rec("followup_anaphora→A", got.get("tier") == TIER_A, got)


def test_agent_ask_routing():
    """天气/小鼠走 general；你是谁走 meta；hsp70 走 hub。不返回旧罐头拒答。"""
    from zebrafish_prompt import OFF_TOPIC_REPLY, SYSTEM_META_REPLY

    canned_marker = "仅回答与 Danio rerio"
    rec("OFF_TOPIC_REPLY_demoted", canned_marker not in OFF_TOPIC_REPLY, OFF_TOPIC_REPLY[:80])

    def fake_general(question, tier="D", history=None):
        from common_utils import empty_result
        from config import TASK_TYPES

        return {
            **empty_result(),
            "answer": f"[general:{tier}] {question}",
            "task_type": TASK_TYPES["GENERAL"],
            "answer_scope": "general" if tier == "D" else "adjacent",
            "intent_tier": tier,
            "skip_enrich": tier == "D",
            "skip_reference_footer": True,
            "hub_mode": False,
            "agent_trace": [],
            "specialists": [],
        }

    def fake_hub(question, **kwargs):
        from common_utils import empty_result

        return {
            **empty_result(),
            "answer": f"[hub] {question}",
            "hub_mode": True,
            "intent_tier": "A",
            "answer_scope": "hub",
        }

    with patch("hub_orchestrator.run_general_assistant", side_effect=fake_general) as p_gen:
        with patch("hub_orchestrator.run_hub_orchestration", side_effect=fake_hub) as p_hub:
            from agent_scheduler import agent_ask_detail

            r1 = agent_ask_detail("今天天气怎么样")
            rec(
                "weather→general_not_canned",
                r1.get("intent_tier") == "D"
                and canned_marker not in (r1.get("answer") or "")
                and r1.get("answer_scope") == "general"
                and not p_hub.called,
                r1.get("answer", "")[:100],
            )

            p_hub.reset_mock()
            p_gen.reset_mock()
            r2 = agent_ask_detail("小鼠 p53")
            rec(
                "mouse_p53→adjacent_not_refuse",
                r2.get("intent_tier") == "C"
                and canned_marker not in (r2.get("answer") or "")
                and r2.get("answer_scope") == "adjacent"
                and not p_hub.called,
                r2.get("answer", "")[:100],
            )

            p_hub.reset_mock()
            p_gen.reset_mock()
            r3 = agent_ask_detail("你是谁")
            rec(
                "who_are_you→meta",
                r3.get("answer_scope") == "meta"
                and SYSTEM_META_REPLY[:12] in (r3.get("answer") or ""),
                (r3.get("answer") or "")[:80],
            )

            p_hub.reset_mock()
            p_gen.reset_mock()
            r4 = agent_ask_detail("hsp70 功能")
            rec(
                "hsp70→hub_path",
                p_hub.called
                and not p_gen.called
                and (r4.get("answer") or "").startswith("[hub]"),
                f"hub={p_hub.called} gen={p_gen.called} ans={r4.get('answer')}",
            )


def test_scoped_chat_routes_general():
    from hub_orchestrator import run_scoped_chat

    with patch("hub_orchestrator.run_general_assistant") as gen:
        gen.return_value = {
            "answer": "通用回答",
            "answer_scope": "general",
            "intent_tier": "D",
            "skip_enrich": True,
            "hub_mode": False,
            "agent_trace": [],
            "specialists": [],
        }
        with patch("hub_orchestrator.run_hub_orchestration") as hub:
            r = run_scoped_chat("今天天气怎么样", "hub", "full")
            rec(
                "scoped_chat_weather→general",
                gen.called and not hub.called and r.get("intent_tier") == "D",
                f"gen={gen.called} hub={hub.called}",
            )


def test_enrich_skips_tier_d():
    from answer_enrichment import enrich_chat_result

    raw = {
        "answer": "今天多云。我专长是斑马鱼研究。",
        "intent_tier": "D",
        "answer_scope": "general",
        "skip_enrich": True,
    }
    out = enrich_chat_result("今天天气怎么样", dict(raw))
    rec(
        "enrich_skips_D",
        out.get("answer") == raw["answer"] and "资料来源" not in (out.get("answer") or ""),
        out.get("answer", "")[:80],
    )


def test_prompts_no_hard_refuse():
    from zebrafish_prompt import (
        ZEBRAFISH_BALANCED_SYSTEM_PROMPT,
        GENERAL_ASSISTANT_SYSTEM_PROMPT,
        get_system_prompt,
        use_system_prompt,
    )

    bal = ZEBRAFISH_BALANCED_SYSTEM_PROMPT
    ok = "无关礼貌拒答" not in bal and "仅斑马鱼相关问题" not in bal
    rec("balanced_prompt_softened", ok, "no hard refuse phrases")

    with use_system_prompt(GENERAL_ASSISTANT_SYSTEM_PROMPT):
        sp = get_system_prompt()
    rec("general_system_override", "专长是斑马鱼" in sp or "通用" in sp, sp[:80])


def main():
    test_classify_tiers()
    test_followup_history_to_a()
    test_agent_ask_routing()
    test_scoped_chat_routes_general()
    test_enrich_skips_tier_d()
    test_prompts_no_hard_refuse()
    failed = [r for r in RESULTS if r[1] == "FAIL"]
    print("---")
    print(f"total={len(RESULTS)} pass={len(RESULTS)-len(failed)} fail={len(failed)}")
    if failed:
        for name, st, detail in failed:
            print("FAIL", name, detail)
        sys.exit(1)
    print("ALL PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
