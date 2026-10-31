# -*- coding: utf-8 -*-
"""补译剩余顽固条目：动态查询 + JSON/规则兜底。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from mysql_client import MySQLClient
from sqlalchemy import text

from scripts.judge_literature_zebrafish_batch import (
    NO_ABSTRACT_LABEL,
    NO_ABSTRACT_ZH,
    _clip_summary,
    _has_chinese,
    _thread_llm,
)
from scripts.judge_rate_limit_guard import InsufficientBalanceError

PENDING_SQL = """
    SELECT pmid, title_en, abstract_en, title_zh, abstract_zh
    FROM literature_pubmed_judgement
    WHERE mode != 'meta'
      AND (
        (title_en IS NOT NULL AND CHAR_LENGTH(title_en) > 3
         AND (title_zh IS NULL OR title_zh = title_en OR title_zh NOT REGEXP '[一-龥]'))
        OR
        (abstract_en IS NOT NULL AND CHAR_LENGTH(abstract_en) > 40
         AND (abstract_zh IS NULL OR abstract_zh NOT REGEXP '[一-龥]'
              OR abstract_zh IN ('No abstract','无摘要','')))
      )
    ORDER BY pmid+0 ASC
"""


def _rule_title(te: str) -> str | None:
    te = (te or "").strip()
    m = re.match(r"^Ensembl\s+(\d{4})\.?$", te, re.I)
    if m:
        return f"Ensembl {m.group(1)} 基因组注释数据库"
    if re.match(r"^Corrigendum[:\s]", te, re.I):
        return "勘误：" + te.split(":", 1)[-1].strip()[:200]
    if len(te) <= 35 and te.endswith("."):
        name = te.rstrip(".")
        if name and not _has_chinese(name):
            return f"{name} 相关研究"
    return None


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    s, e = text.find("{"), text.rfind("}")
    if s >= 0 and e > s:
        return json.loads(text[s : e + 1])
    raise ValueError("no json")


def _llm_fix(te: str, ae: str, need_t: bool, need_a: bool, llm) -> tuple[str, str]:
    parts = []
    if need_t:
        parts.append(f"title_zh: 将「{te[:500]}」译为中文标题")
    if need_a:
        parts.append(f"abstract_zh: 用一句话中文概括：{ae[:1200]}")
    prompt = (
        "只返回 JSON，键名 title_zh 和/或 abstract_zh：\n"
        + "\n".join(parts)
    )
    raw = llm.chat([{"role": "user", "content": prompt}], max_new_tokens=320)
    data = _parse_json(raw)
    tz = str(data.get("title_zh") or "").strip()
    az = _clip_summary(str(data.get("abstract_zh") or ""))
    return tz, az


def main() -> int:
    client = MySQLClient()
    llm = _thread_llm()
    db = next(client.get_db())
    try:
        rows = db.execute(text(PENDING_SQL)).fetchall()
    finally:
        db.close()

    ok = 0
    for pmid, te, ae, tz, az in rows:
        te, ae = te or "", ae or ""
        tz, az = tz or "", az or ""
        need_t = not _has_chinese(tz) or tz.strip() == te.strip()
        need_a = (
            len(ae.strip()) > 40
            and not _has_chinese(az)
            and az.strip() not in (NO_ABSTRACT_LABEL, NO_ABSTRACT_ZH, "")
        )
        if not need_t and not need_a:
            continue

        new_tz = tz or te
        new_az = az or NO_ABSTRACT_ZH

        try:
            if need_t:
                ruled = _rule_title(te)
                if ruled:
                    new_tz = ruled
                else:
                    lt, _ = _llm_fix(te, ae, True, False, llm)
                    if _has_chinese(lt):
                        new_tz = lt
                    else:
                        ruled = _rule_title(te) or f"{te.rstrip('.')} 相关研究"
                        new_tz = ruled
            if need_a:
                _, la = _llm_fix(te, ae, False, True, llm)
                if _has_chinese(la):
                    new_az = la
                elif ae:
                    new_az = _clip_summary(ae[:200])
            db = next(client.get_db())
            try:
                db.execute(
                    text(
                        "UPDATE literature_pubmed_judgement SET title_zh=:tz, abstract_zh=:az WHERE pmid=:p"
                    ),
                    {"p": pmid, "tz": new_tz[:8000], "az": new_az[:16000]},
                )
                db.commit()
                ok += 1
                print(f"  OK {pmid}: {new_tz[:50]}")
            finally:
                db.close()
        except InsufficientBalanceError:
            print("余额不足")
            return 1
        except Exception as e:
            print(f"  FAIL {pmid}: {e}")

    print(f"完成 {ok}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
