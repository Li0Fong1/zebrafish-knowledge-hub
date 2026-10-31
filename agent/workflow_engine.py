# -*- coding: utf-8 -*-
"""工作流 DAG 执行引擎（复用 Hub Agent 节点）。"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from common_utils import logger

NODE_LABELS = {
    "input": "输入",
    "id_resolve": "ID 映射",
    "gene_detail": "基因详情",
    "literature_rag": "文献 RAG",
    "enrichment": "GO/KEGG 富集",
    "synthesizer": "综合报告",
}

_PARTIAL_KEY = {
    "id_resolve": "id_mapper",
    "gene_detail": "gene_analyst",
    "literature_rag": "literature",
    "enrichment": "enrichment",
}


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


def _topo_layers(nodes: List[dict]) -> List[List[dict]]:
    by_id = {n["id"]: n for n in nodes}
    deps: Dict[str, set] = {}
    for n in nodes:
        d = set(n.get("depends") or [])
        for dep in d:
            if dep not in by_id:
                raise ValueError(f"未知依赖节点：{dep}")
        deps[n["id"]] = d

    layers: List[List[dict]] = []
    done: set = set()
    remaining = set(by_id.keys())
    while remaining:
        layer = [nid for nid in remaining if deps[nid].issubset(done)]
        if not layer:
            raise ValueError("工作流存在循环依赖")
        layers.append([by_id[nid] for nid in layer])
        done.update(layer)
        remaining -= set(layer)
    return layers


def _run_node(
    node: dict,
    question: str,
    partials: Dict[str, Any],
    *,
    answer_depth: str = "full",
) -> Tuple[dict, dict]:
    """返回 (step_record, payload)。"""
    node_id = node["id"]
    ntype = node.get("type") or node_id
    label = node.get("label") or NODE_LABELS.get(ntype, ntype)
    t0 = time.time()
    depth = (answer_depth or "full").strip().lower()
    try:
        from hub_orchestrator import _lit_pack_chars

        lit_chars = _lit_pack_chars(depth)
    except Exception:
        lit_chars = 48000

    try:
        if ntype == "input":
            step = {
                "node_id": node_id,
                "node_type": ntype,
                "label": label,
                "status": "ok",
                "summary": f"输入 {len(question)} 字符 · 策略={depth}",
                "elapsed_ms": _ms(t0),
                "output": {"text": question, "answer_depth": depth},
            }
            return step, {"text": question}

        from hub_orchestrator import (
            _run_id_mapper,
            _run_gene_analyst,
            _run_literature,
            _run_enrichment,
            _merge_partials,
            _synthesize_answer,
        )

        if ntype == "id_resolve":
            payload = _run_id_mapper(question)
            ok = payload.get("resolved")
            sym = (payload.get("canonical") or {}).get("gene_name") or payload.get("query")
            summary = f"解析 {'成功' if ok else '未命中'}：{sym or question[:24]}"
            status = "ok" if ok else "miss"
            step = {
                "node_id": node_id,
                "node_type": ntype,
                "label": label,
                "status": status,
                "summary": summary,
                "elapsed_ms": _ms(t0),
                "output": payload,
            }
            return step, payload

        if ntype == "gene_detail":
            payload = _run_gene_analyst(question)
            if payload.get("skipped"):
                step = {
                    "node_id": node_id,
                    "node_type": ntype,
                    "label": label,
                    "status": "skip",
                    "summary": "未检测到基因符号",
                    "elapsed_ms": _ms(t0),
                }
                return step, payload
            ok = payload.get("success")
            summary = f"基因 {payload.get('gene')} {'已加载' if ok else '未找到'}"
            step = {
                "node_id": node_id,
                "node_type": ntype,
                "label": label,
                "status": "ok" if ok else "miss",
                "summary": summary,
                "elapsed_ms": _ms(t0),
                "output": payload,
            }
            return step, payload

        if ntype == "literature_rag":
            payload = _run_literature(question, max_chars=lit_chars)
            ok = payload.get("has_hits")
            summary = f"RAG 检索 {payload.get('chars', 0)} 字符（{depth}）"
            step = {
                "node_id": node_id,
                "node_type": ntype,
                "label": label,
                "status": "ok" if ok else "miss",
                "summary": summary,
                "elapsed_ms": _ms(t0),
                "output": {"chars": payload.get("chars"), "has_hits": ok},
            }
            return step, payload

        if ntype == "enrichment":
            payload = _run_enrichment(question)
            if payload.get("skipped"):
                step = {
                    "node_id": node_id,
                    "node_type": ntype,
                    "label": label,
                    "status": "skip",
                    "summary": "无基因列表，跳过富集",
                    "elapsed_ms": _ms(t0),
                }
                return step, payload
            ngo = len((payload.get("go") or {}).get("significant") or [])
            summary = f"ORA 显著 GO 项 {ngo}"
            step = {
                "node_id": node_id,
                "node_type": ntype,
                "label": label,
                "status": "ok",
                "summary": summary,
                "elapsed_ms": _ms(t0),
                "output": {"genes": payload.get("genes"), "go_sig": ngo},
            }
            return step, payload

        if ntype == "synthesizer":
            from agent_scheduler import route_task, agent_scheduler
            from tool_orchestrator import augment_chat_result
            from intent_router import (
                classify_question_intent,
                is_general_lane_tier,
                TIER_E,
                safety_refusal_reply,
            )
            from hub_orchestrator import run_general_assistant
            from common_utils import empty_result

            intent = classify_question_intent(question)
            tier = intent.get("tier")
            if tier == TIER_E:
                out = {
                    **empty_result(),
                    "answer": safety_refusal_reply(),
                    "intent_tier": TIER_E,
                    "skip_enrich": True,
                }
                step = {
                    "node_id": node_id,
                    "node_type": ntype,
                    "label": label,
                    "status": "miss",
                    "summary": "安全拒答",
                    "elapsed_ms": _ms(t0),
                }
                return step, out
            if is_general_lane_tier(tier):
                out = run_general_assistant(question, tier=tier)
                step = {
                    "node_id": node_id,
                    "node_type": ntype,
                    "label": label,
                    "status": "ok",
                    "summary": f"通用车道 tier={tier}",
                    "elapsed_ms": _ms(t0),
                }
                return step, out

            hub_partials = {_PARTIAL_KEY.get(k, k): v for k, v in partials.items() if k != "input"}
            task_type = route_task(question)
            result = agent_scheduler.run_task_detail(question, task_type)
            result = _merge_partials(result, hub_partials)
            try:
                result = augment_chat_result(question, result)
            except Exception as e:
                logger.warning(f"[Workflow] 自动工具失败: {e}")
            trace_stub = [{"label": label, "status": "ok", "summary": f"主任务 {task_type}", "ms": _ms(t0)}]
            result["answer"] = _synthesize_answer(result, trace_stub)
            step = {
                "node_id": node_id,
                "node_type": ntype,
                "label": label,
                "status": "ok",
                "summary": f"综合报告完成（{task_type}）",
                "elapsed_ms": _ms(t0),
            }
            return step, result

    except Exception as e:
        logger.warning(f"[Workflow] 节点 {node_id} 失败: {e}")
        step = {
            "node_id": node_id,
            "node_type": ntype,
            "label": label,
            "status": "error",
            "summary": str(e)[:160],
            "elapsed_ms": _ms(t0),
        }
        return step, {"error": str(e)}

    step = {
        "node_id": node_id,
        "node_type": ntype,
        "label": label,
        "status": "skip",
        "summary": "未知节点类型",
        "elapsed_ms": _ms(t0),
    }
    return step, {}


def execute_workflow(
    definition: dict,
    input_text: str,
    *,
    answer_depth: str = "full",
) -> dict:
    """执行工作流定义，返回 steps + output。answer_depth 与聊天「使用策略」对齐。"""
    nodes = definition.get("nodes") or []
    if not nodes:
        raise ValueError("工作流无节点")

    question = (input_text or "").strip()
    if not question:
        raise ValueError("输入不能为空")

    depth = (answer_depth or "full").strip().lower() or "full"
    layers = _topo_layers(nodes)
    partials: Dict[str, Any] = {}
    steps: List[dict] = []
    sort_order = 0
    final_output: dict = {}

    for layer in layers:
        if len(layer) == 1:
            node = layer[0]
            step, payload = _run_node(node, question, partials, answer_depth=depth)
            step["sort_order"] = sort_order
            sort_order += 1
            steps.append(step)
            ntype = node.get("type") or node["id"]
            partials[ntype] = payload
            if ntype == "synthesizer":
                final_output = payload
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(layer))) as pool:
                futs = {
                    pool.submit(_run_node, n, question, dict(partials), answer_depth=depth): n
                    for n in layer
                }
                for fut in as_completed(futs):
                    node = futs[fut]
                    step, payload = fut.result()
                    step["sort_order"] = sort_order
                    sort_order += 1
                    steps.append(step)
                    ntype = node.get("type") or node["id"]
                    partials[ntype] = payload
                    if ntype == "synthesizer":
                        final_output = payload
            steps.sort(key=lambda s: s.get("sort_order", 0))

    if not final_output and partials.get("synthesizer"):
        final_output = partials["synthesizer"]
    if final_output and isinstance(final_output, dict):
        final_output.setdefault("answer_depth", depth)

    failed = any(s.get("status") == "error" for s in steps)
    return {
        "status": "failed" if failed else "success",
        "steps": steps,
        "output": final_output,
        "answer_depth": depth,
        "partials": {k: v for k, v in partials.items() if k != "input"},
    }
