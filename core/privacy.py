# -*- coding: utf-8 -*-
"""对话历史的留存与脱敏策略（PII）。

背景：`hub_user_conversations.payload` 存的是用户完整的对话 JSON，里面可能夹带
邮箱、手机号、身份证号等个人信息。此前是**原样落库、无限期保留**——一旦库被拖走
或有人拿到只读账号，这些信息就是明文。

本模块提供两件事，均由环境变量控制，默认值偏保守（脱敏开、留存 180 天）：

  · ``mask_payload``   递归脱敏（邮箱 / 手机号 / 身份证 / 长卡号）；
  · ``trim_by_retention`` 按会话时间裁剪超期会话，避免历史无限增长。

设计取舍：
  · 只做**确定性规则**替换，不做语义识别，宁可少脱敏也不误伤基因/坐标数字；
  · 与会话内容强相关的基因符号、坐标、PMID 一律不动；
  · 关掉开关即回到原行为（`HUB_HISTORY_MASK_PII=0`、`HUB_HISTORY_RETENTION_DAYS=0`）。
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Iterable, List, Tuple

# 邮箱：只保留首字符 + 域名（域名本身不是个人信息）
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# 中国大陆手机号：11 位、1 开头、第二位 3-9
_MOBILE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
# 身份证号：17 位数字 + 校验位（0-9/X）
_ID_CARD = re.compile(r"(?<!\d)(\d{6})\d{8}(\d{3}[\dXx])(?!\d)")
# 银行卡/长数字串：16~19 位
_LONG_CARD = re.compile(r"(?<!\d)(\d{12,15})(\d{4})(?!\d)")


def mask_text(text: str) -> str:
    """对单段文本做 PII 脱敏；非字符串原样返回。"""
    if not isinstance(text, str) or not text:
        return text
    s = _EMAIL.sub(lambda m: m.group(1) + "***@" + m.group(2), text)
    s = _MOBILE.sub(lambda m: m.group(1) + "****" + m.group(2), s)
    s = _ID_CARD.sub(lambda m: m.group(1) + "********" + m.group(2), s)
    s = _LONG_CARD.sub(lambda m: "****" + m.group(2), s)
    return s


def mask_obj(obj: Any) -> Any:
    """递归脱敏 dict / list / str。"""
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, list):
        return [mask_obj(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(mask_obj(x) for x in obj)
    if isinstance(obj, dict):
        return {k: mask_obj(v) for k, v in obj.items()}
    return obj


def mask_enabled() -> bool:
    return (os.getenv("HUB_HISTORY_MASK_PII", "1") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def mask_payload(payload: Any) -> Any:
    """按开关决定是否脱敏（关闭时原样返回，零开销）。"""
    return mask_obj(payload) if mask_enabled() else payload


def retention_days() -> int:
    """留存天数；0 或负数表示不限制。"""
    raw = (os.getenv("HUB_HISTORY_RETENTION_DAYS", "180") or "180").strip()
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        return 180
    return max(0, v)


def _ts_ms(value: Any) -> float:
    """把秒/毫秒/ISO 字符串统一成毫秒时间戳；解析不了返回 0。"""
    if isinstance(value, (int, float)):
        v = float(value)
        return v * 1000.0 if v < 1e12 else v
    if isinstance(value, str):
        s = value.strip()
        if s.isdigit():
            return _ts_ms(int(s))
        try:
            from datetime import datetime

            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000.0
        except Exception:  # noqa: BLE001
            return 0.0
    return 0.0


def conversation_ts(conv: dict) -> float:
    for key in ("updatedAt", "createdAt", "updated_at", "created_at", "ts", "time"):
        if isinstance(conv, dict) and conv.get(key):
            v = _ts_ms(conv[key])
            if v:
                return v
    return 0.0


def trim_by_retention(convs: Iterable[Any]) -> Tuple[List[Any], List[Any]]:
    """按留存期拆分会话，返回 (保留, 过期)。

    没有时间戳的会话一律保留（宁可多留，也不误删用户数据）。
    """
    days = retention_days()
    items = list(convs or [])
    if days <= 0:
        return items, []
    cutoff = (time.time() - days * 86400) * 1000.0
    kept, dropped = [], []
    for c in items:
        ts = conversation_ts(c) if isinstance(c, dict) else 0.0
        if ts and ts < cutoff:
            dropped.append(c)
        else:
            kept.append(c)
    return kept, dropped
