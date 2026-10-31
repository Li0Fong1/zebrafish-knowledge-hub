# -*- coding: utf-8 -*-
"""
判别质量保险：禁止 LLM 失败时静默降级为规则引擎；失败入重试队列，直至 LLM 成功入库。

仅结构性异常（无标题/XML 损坏）可不经 LLM 标记为无法判别。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from common_utils import safe_print

ROOT = Path(__file__).resolve().parents[1]
RETRY_FILE = ROOT / "data" / "literature" / "pubmed_judgement_retry.json"
ALLOW_RULE_FALLBACK = os.getenv("JUDGE_ALLOW_RULE_FALLBACK", "0").strip() in ("1", "true", "yes")
MAX_RETRY_ATTEMPTS = int(os.getenv("JUDGE_QUALITY_MAX_RETRY", "8"))
FAIL_WINDOW = float(os.getenv("JUDGE_QUALITY_FAIL_WINDOW_SEC", "60"))
FAIL_THRESHOLD = int(os.getenv("JUDGE_QUALITY_FAIL_THRESHOLD", "3"))
COOLDOWN_SEC = float(os.getenv("JUDGE_QUALITY_COOLDOWN_SEC", "20"))

# 允许不经 LLM 直接入库的原因（结构性问题）
META_REASONS = frozenset({"XML 无标题", "XML 解析失败"})


class QualityGuard:
    """全库判别质量保险：LLM-only，失败重试，异常失败率触发冷却。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._retry: dict[str, dict] = self._load_retry()
        self._fail_times: list[float] = []
        self.llm_ok = 0
        self.llm_fail = 0
        self.meta_ok = 0
        self.requeued = 0

    @staticmethod
    def _load_retry() -> dict[str, dict]:
        if RETRY_FILE.is_file():
            try:
                data = json.loads(RETRY_FILE.read_text(encoding="utf-8"))
                return dict(data.get("items") or {})
            except json.JSONDecodeError:
                pass
        return {}

    def _save_retry(self) -> None:
        RETRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        RETRY_FILE.write_text(
            json.dumps(
                {
                    "items": self._retry,
                    "count": len(self._retry),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def allow_rule_fallback(self) -> bool:
        return ALLOW_RULE_FALLBACK

    def is_meta_only(self, reason: str) -> bool:
        return reason in META_REASONS

    def record_llm_success(self) -> None:
        with self._lock:
            self.llm_ok += 1

    def record_meta(self) -> None:
        with self._lock:
            self.meta_ok += 1

    def record_llm_fail(self, pmid: str, error: str) -> None:
        with self._lock:
            self.llm_fail += 1
            now = time.time()
            self._fail_times.append(now)
            cutoff = now - FAIL_WINDOW
            self._fail_times = [t for t in self._fail_times if t >= cutoff]
            item = self._retry.get(pmid) or {"attempts": 0, "last_error": ""}
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["last_error"] = error[:300]
            item["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._retry[pmid] = item
            self._save_retry()
            if item["attempts"] == 1:
                safe_print(f"  [质量保险] PMID {pmid} LLM 失败，入重试队列（不降级规则）")

    def pop_retry_batch(self, limit: int = 100) -> list[str]:
        with self._lock:
            out = []
            for pmid, info in list(self._retry.items()):
                if int(info.get("attempts") or 0) >= MAX_RETRY_ATTEMPTS:
                    continue
                out.append(pmid)
                if len(out) >= limit:
                    break
            return out

    def clear_retry(self, pmid: str) -> None:
        with self._lock:
            if pmid in self._retry:
                del self._retry[pmid]
                self._save_retry()

    def maybe_cooldown(self) -> None:
        """连续 LLM 失败过多时暂停，避免雪崩式规则降级或 API 封禁。"""
        with self._lock:
            now = time.time()
            cutoff = now - FAIL_WINDOW
            recent = [t for t in self._fail_times if t >= cutoff]
            self._fail_times = recent
            if len(recent) >= FAIL_THRESHOLD:
                safe_print(
                    f"  [质量保险] {len(recent)} 次 LLM 失败/{FAIL_WINDOW:.0f}s "
                    f"→ 冷却 {COOLDOWN_SEC:.0f}s（保证质量）"
                )
                time.sleep(COOLDOWN_SEC)
                self._fail_times.clear()

    def requeue_non_llm_from_db(self, client) -> int:
        """将已入库的规则降级记录删回重试（结构性除外）。"""
        from sqlalchemy import text

        db = next(client.get_db())
        try:
            rows = db.execute(
                text(
                    "SELECT pmid, reason FROM literature_pubmed_judgement "
                    "WHERE mode = 'rule' AND reason NOT IN ('XML 无标题', 'XML 解析失败')"
                )
            ).fetchall()
            n = 0
            for pmid, reason in rows:
                db.execute(
                    text("DELETE FROM literature_pubmed_judgement WHERE pmid = :p"),
                    {"p": str(pmid)},
                )
                self.record_llm_fail(str(pmid), f"requeue_was_rule:{reason}")
                n += 1
            if n:
                db.commit()
                self.requeued = n
                safe_print(f"  [质量保险] 已将 {n} 条规则降级记录删回 LLM 重试队列")
            return n
        finally:
            db.close()

    def status(self) -> dict:
        with self._lock:
            return {
                "llm_ok": self.llm_ok,
                "llm_fail": self.llm_fail,
                "meta_ok": self.meta_ok,
                "retry_pending": len(self._retry),
                "requeued_from_db": self.requeued,
                "allow_rule_fallback": ALLOW_RULE_FALLBACK,
                "quality_policy": "llm_only",
            }

    @property
    def retry_count(self) -> int:
        with self._lock:
            return len(self._retry)
