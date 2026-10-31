# -*- coding: utf-8 -*-
"""
文献批量判别：斑马鱼是否为主要研究对象 + 中文一句话摘要。

LLM 返回精简 Markdown（非 JSON），摘要与理由均为中文：
  摘要:用中文写一句话研究总结
  判断结果:是|否|无法判别
  理由:用一句话总结得出上面结论的原因

标题沿用原文，不再调用 LLM 翻译。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry import bootstrap_app

bootstrap_app()

from common_utils import safe_print
from scripts.curate_literature_test_batch import Article, _parse_txt, assess_zebrafish_primary
from scripts.judge_rate_limit_guard import InsufficientBalanceError, is_balance_error
from scripts.literature_txt_names import index_prefix

DEFAULT_IN = ROOT / "项目阶段" / "05_数据与库" / "归档" / "data_export" / "test" / "literature_test_100"
FORMAT_NAME = "zebrafish_judgement_1000(format).md"

# 经测试的安全上限：LLM 逐篇调用 + 断点续传 + 重试
MAX_LLM_SAFE = int(os.getenv("JUDGE_MAX_LLM_SAFE", "1000"))
MAX_RULE_SAFE = int(os.getenv("JUDGE_MAX_RULE_SAFE", "9999"))
MAX_INPUT_CHARS = int(os.getenv("JUDGE_MAX_INPUT_CHARS", "6000"))
MAX_OUTPUT_TOKENS = int(os.getenv("JUDGE_MAX_OUTPUT_TOKENS", "128"))
# 全库判别：输入截断 + 中文一句话摘要 + MD；输出统一 128 token
INVENTORY_ECONOMY = os.getenv("JUDGE_INVENTORY_ECONOMY", "1").strip() in ("1", "true", "yes")
INVENTORY_INPUT_CHARS = int(os.getenv("JUDGE_INVENTORY_INPUT_CHARS", "2200"))
INVENTORY_OUTPUT_TOKENS = int(os.getenv("JUDGE_INVENTORY_MAX_OUTPUT_TOKENS", "128"))
INVENTORY_TRANSLATE_TOKENS = int(os.getenv("JUDGE_INVENTORY_TRANSLATE_MAX_TOKENS", "128"))
INVENTORY_REASON_MAX = int(os.getenv("JUDGE_INVENTORY_REASON_MAX", "120"))
INVENTORY_SUMMARY_MAX = int(os.getenv("JUDGE_INVENTORY_SUMMARY_MAX", "120"))
NO_ABSTRACT_LABEL = "No abstract"
NO_ABSTRACT_ZH = "无摘要"
LLM_RETRIES = int(os.getenv("JUDGE_LLM_RETRIES", "4"))
LLM_RETRY_WAIT = float(os.getenv("JUDGE_LLM_RETRY_WAIT", "3.0"))
CHECKPOINT_EVERY = int(os.getenv("JUDGE_CHECKPOINT_EVERY", "25"))

_tls = threading.local()


def _thread_llm():
    """每线程复用一个 ApiLLM，避免重复初始化与日志刷屏。"""
    if not hasattr(_tls, "llm"):
        from llm.llm_api import ApiLLM

        _tls.llm = ApiLLM()
    return _tls.llm

ARTICLE_FILE_RE = re.compile(r"^(\d{3,4})_")


@dataclass
class Judgement:
    no: str
    title_en: str
    abstract_en: str
    title_zh: str
    abstract_zh: str
    result: str  # 是 | 否 | 无法判别
    reason: str = ""


def _article_no(filename: str) -> str:
    m = ARTICLE_FILE_RE.match(filename)
    return m.group(1) if m else filename[:4]


def _parse_llm_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


_MD_FIELD_KEYS = {
    "标题": "title_zh",
    "Title": "title_zh",
    "摘要": "abstract_zh",
    "Summary": "abstract_zh",
    "Abstract": "abstract_zh",
    "判断结果": "judgement",
    "Judgement": "judgement",
    "Judgment": "judgement",
    "理由": "reason",
    "Reason": "reason",
}


def _parse_llm_md(raw: str, *, required: frozenset[str] | None = None) -> dict:
    """解析 LLM 返回的精简 Markdown 行格式：字段名:内容"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|md)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in (":", "："):
            if sep not in line:
                continue
            key, val = line.split(sep, 1)
            key, val = key.strip(), val.strip()
            field = _MD_FIELD_KEYS.get(key)
            if field and val:
                out[field] = val
            break
    need = required or frozenset()
    missing = [k for k in need if not out.get(k)]
    if missing:
        raise ValueError(f"MD 缺少字段: {', '.join(missing)}")
    return out


def _has_chinese(s: str) -> bool:
    return bool(s and re.search(r"[\u4e00-\u9fff]", s))


def _clip_summary(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return NO_ABSTRACT_ZH
    if len(s) <= INVENTORY_SUMMARY_MAX:
        return s
    return s[: INVENTORY_SUMMARY_MAX - 1].rstrip() + "…"


def _orig_title(title: str) -> str:
    return (title or "").strip()


def _prep_abstract_input(abstract: str | None, *, empty_label: str = NO_ABSTRACT_LABEL) -> str:
    abs_raw = (abstract or "").strip()
    if not abs_raw:
        return empty_label
    abstract_text = abs_raw[:INVENTORY_INPUT_CHARS]
    if len(abs_raw) > INVENTORY_INPUT_CHARS:
        abstract_text += "…"
    return abstract_text


def _md_judge_spec() -> str:
    return (
        "严格按下列 Markdown 行格式输出（不要 JSON、不要代码块、不要输出标题行）：\n"
        f"摘要:用中文写一句话研究总结（不超过 {INVENTORY_SUMMARY_MAX} 字符；"
        f"无摘要写「{NO_ABSTRACT_ZH}」）\n"
        "判断结果:是 或 否 或 无法判别\n"
        f"理由:用一句话总结得出上面结论的原因（不超过 {INVENTORY_REASON_MAX} 字符）"
    )


def _md_summarize_spec() -> str:
    return (
        "严格按下列 Markdown 行格式输出（不要 JSON、不要代码块、不要输出标题行）：\n"
        f"摘要:用中文写一句话研究总结（不超过 {INVENTORY_SUMMARY_MAX} 字符；"
        f"无摘要写「{NO_ABSTRACT_ZH}」）"
    )


def _llm_judge(
    title: str,
    abstract: str,
    llm=None,
    on_rate_limit=None,
) -> Tuple[str, str, str, str]:
    """返回 (title, summary, judgement, reason)；title 始终为原文标题。"""
    llm = llm or _thread_llm()
    abstract_text = _prep_abstract_input(abstract, empty_label=NO_ABSTRACT_LABEL)
    title_text = _orig_title(title)[:2000]
    prompt = f"""You are a zebrafish (Danio rerio) literature expert. Read the title and abstract.

【Criteria】Is this study PRIMARILY using zebrafish as the research subject/model? Mention-only or control/background use → 否. Insufficient info → 无法判别.

Title: {title_text}
Abstract: {abstract_text}

{_md_judge_spec()}"""

    last_err: Optional[Exception] = None
    for attempt in range(LLM_RETRIES):
        try:
            raw = llm.chat(
                [{"role": "user", "content": prompt}],
                max_new_tokens=MAX_OUTPUT_TOKENS,
            )
            data = _parse_llm_md(raw, required=frozenset({"abstract_zh", "judgement"}))
            return (
                title_text,
                _clip_summary(str(data.get("abstract_zh") or "")),
                str(data.get("judgement") or "无法判别").strip(),
                str(data.get("reason") or "")[:INVENTORY_REASON_MAX].strip(),
            )
        except Exception as e:
            if is_balance_error(e):
                raise InsufficientBalanceError(str(e)) from e
            last_err = e
            err_s = str(e).lower()
            is_rl = "429" in err_s or "rate" in err_s or "too many" in err_s
            if is_rl and on_rate_limit:
                on_rate_limit()
            if is_rl:
                wait = LLM_RETRY_WAIT * (2 ** attempt) * 2
            else:
                wait = LLM_RETRY_WAIT * (2 ** attempt)
            safe_print(f"    LLM 重试 {attempt + 1}/{LLM_RETRIES}：{e}，{wait:.0f}s…")
            time.sleep(wait)
    raise last_err if last_err else RuntimeError("LLM 判别失败")


# 判别标准原文（全库/样本共用，保证 economy 模式不降级）
_JUDGE_CRITERIA = (
    "判断该研究是否**主要以斑马鱼为研究对象**（实验、模型、主要观察对象均为斑马鱼/Danio rerio），"
    "而非其它鱼类或其它生物。仅提及斑马鱼、或斑马鱼仅作对照/背景/方法学引用，应判「否」。"
    "信息不足无法判断则判「无法判别」。"
)


def _llm_judge_inventory(
    title: str,
    abstract: str,
    llm=None,
    on_rate_limit=None,
) -> Tuple[str, str, str, str]:
    """
    全库 economy：中文一句话摘要 + MD 输出，判别标准不变；标题不经过 LLM。
    """
    llm = llm or _thread_llm()
    title_text = _orig_title(title)[:800]
    abstract_text = _prep_abstract_input(abstract)

    prompt = (
        f"You are a zebrafish (Danio rerio) literature expert.\n\n"
        f"【Criteria】{_JUDGE_CRITERIA}\n\n"
        f"Title: {title_text}\n"
        f"Abstract: {abstract_text}\n\n"
        f"{_md_judge_spec()}"
    )

    last_err: Optional[Exception] = None
    for attempt in range(LLM_RETRIES):
        try:
            raw = llm.chat(
                [{"role": "user", "content": prompt}],
                max_new_tokens=INVENTORY_OUTPUT_TOKENS,
            )
            data = _parse_llm_md(raw, required=frozenset({"abstract_zh", "judgement"}))
            return (
                title_text,
                _clip_summary(str(data.get("abstract_zh") or "")),
                str(data.get("judgement") or "无法判别").strip(),
                str(data.get("reason") or "")[:INVENTORY_REASON_MAX].strip(),
            )
        except Exception as e:
            if is_balance_error(e):
                raise InsufficientBalanceError(str(e)) from e
            last_err = e
            err_s = str(e).lower()
            is_rl = "429" in err_s or "rate" in err_s or "too many" in err_s
            if is_rl and on_rate_limit:
                on_rate_limit()
            if is_rl:
                wait = LLM_RETRY_WAIT * (2 ** attempt) * 2
            else:
                wait = LLM_RETRY_WAIT * (2 ** attempt)
            safe_print(f"    LLM 重试 {attempt + 1}/{LLM_RETRIES}：{e}，{wait:.0f}s…")
            time.sleep(wait)
    raise last_err if last_err else RuntimeError("LLM 判别失败")


def _llm_translate_inventory(
    title: str,
    abstract: str,
    llm=None,
    on_rate_limit=None,
) -> Tuple[str, str]:
    """混合模式：规则已定 judgement，LLM 仅生成中文一句话摘要。"""
    llm = llm or _thread_llm()
    title_text = _orig_title(title)[:800]
    abstract_text = _prep_abstract_input(abstract)

    prompt = (
        "Summarize the following academic record in ONE sentence in Chinese.\n\n"
        f"Title: {title_text}\n"
        f"Abstract: {abstract_text}\n\n"
        f"{_md_summarize_spec()}"
    )

    last_err: Optional[Exception] = None
    for attempt in range(LLM_RETRIES):
        try:
            raw = llm.chat(
                [{"role": "user", "content": prompt}],
                max_new_tokens=INVENTORY_TRANSLATE_TOKENS,
            )
            data = _parse_llm_md(raw, required=frozenset({"abstract_zh"}))
            return (
                title_text,
                _clip_summary(str(data.get("abstract_zh") or "")),
            )
        except Exception as e:
            if is_balance_error(e):
                raise InsufficientBalanceError(str(e)) from e
            last_err = e
            err_s = str(e).lower()
            is_rl = "429" in err_s or "rate" in err_s or "too many" in err_s
            if is_rl and on_rate_limit:
                on_rate_limit()
            wait = LLM_RETRY_WAIT * (2 ** attempt) * (2 if is_rl else 1)
            safe_print(f"    LLM 总结重试 {attempt + 1}/{LLM_RETRIES}：{e}，{wait:.0f}s…")
            time.sleep(wait)
    raise last_err if last_err else RuntimeError("LLM 总结失败")


def _llm_refresh_summary_reason(
    title: str,
    abstract: str,
    judgement: str,
    llm=None,
    on_rate_limit=None,
) -> Tuple[str, str]:
    """按新规范刷新 abstract_zh 与 reason，不改变 judgement。"""
    llm = llm or _thread_llm()
    title_text = _orig_title(title)[:800]
    abstract_text = _prep_abstract_input(abstract)
    judgement = (judgement or "无法判别").strip()

    prompt = (
        "You are a zebrafish (Danio rerio) literature expert.\n\n"
        f"Title: {title_text}\n"
        f"Abstract: {abstract_text}\n"
        f"判断结果（已确定，请勿修改）:{judgement}\n\n"
        "严格按下列 Markdown 行格式输出（不要 JSON、不要代码块、不要输出标题行、不要输出判断结果行）：\n"
        f"摘要:用中文写一句话研究总结（不超过 {INVENTORY_SUMMARY_MAX} 字符；"
        f"无摘要写「{NO_ABSTRACT_ZH}」）\n"
        f"理由:用一句话总结得出上面结论的原因（不超过 {INVENTORY_REASON_MAX} 字符）"
    )

    last_err: Optional[Exception] = None
    for attempt in range(LLM_RETRIES):
        try:
            raw = llm.chat(
                [{"role": "user", "content": prompt}],
                max_new_tokens=INVENTORY_OUTPUT_TOKENS,
            )
            data = _parse_llm_md(raw, required=frozenset({"abstract_zh", "reason"}))
            return (
                _clip_summary(str(data.get("abstract_zh") or "")),
                str(data.get("reason") or "")[:INVENTORY_REASON_MAX].strip(),
            )
        except Exception as e:
            if is_balance_error(e):
                raise InsufficientBalanceError(str(e)) from e
            last_err = e
            err_s = str(e).lower()
            is_rl = "429" in err_s or "rate" in err_s or "too many" in err_s
            if is_rl and on_rate_limit:
                on_rate_limit()
            wait = LLM_RETRY_WAIT * (2 ** attempt) * (2 if is_rl else 1)
            safe_print(f"    LLM 刷新重试 {attempt + 1}/{LLM_RETRIES}：{e}，{wait:.0f}s…")
            time.sleep(wait)
    raise last_err if last_err else RuntimeError("LLM 刷新失败")


def _extract_md_field(raw: str, field: str) -> str | None:
    """从 LLM 原始输出中提取字段，解析失败时的兜底。"""
    try:
        return _parse_llm_md(raw, required=frozenset({field})).get(field)
    except ValueError:
        pass
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|md)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    label = "标题" if field == "title_zh" else "摘要"
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(label):
            for sep in (":", "："):
                if sep in line:
                    val = line.split(sep, 1)[1].strip()
                    if val:
                        return val
        if _has_chinese(line) and len(line) > 4:
            if field == "title_zh" and len(line) < 200:
                return line
            if field == "abstract_zh" and len(line) >= 8:
                return line
    if field == "abstract_zh":
        zh = re.findall(r"[\u4e00-\u9fff][\u4e00-\u9fff，。；：、（）\w\s\-]{8,}", text)
        if zh:
            return zh[0].strip()
    return None


def _llm_backfill_zh(
    title_en: str,
    abstract_en: str,
    abstract_zh: str = "",
    *,
    need_title: bool = True,
    need_summary: bool = True,
    llm=None,
    on_rate_limit=None,
) -> Tuple[str, str]:
    """补全中文标题与一句话摘要（仅翻译，不改变判别结果）。"""
    llm = llm or _thread_llm()
    title_en = (title_en or "")[:800].strip()
    abs_en = _prep_abstract_input(abstract_en)
    cur_sum = (abstract_zh or "").strip()

    def _call(prompt: str, field: str, *, retries: int = 2) -> str:
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            try:
                raw = llm.chat(
                    [{"role": "user", "content": prompt}],
                    max_new_tokens=int(os.getenv("JUDGE_BACKFILL_MAX_TOKENS", "256")),
                )
                val = _extract_md_field(raw, field)
                if val:
                    return val.strip()
                raise ValueError(f"MD 缺少字段: {field}")
            except InsufficientBalanceError:
                raise
            except Exception as e:
                if is_balance_error(e):
                    raise InsufficientBalanceError(str(e)) from e
                last_err = e
                err_s = str(e).lower()
                is_rl = "429" in err_s or "rate" in err_s or "too many" in err_s
                if is_rl and on_rate_limit:
                    on_rate_limit()
                if attempt + 1 < retries:
                    wait = LLM_RETRY_WAIT * (2 ** attempt) * (2 if is_rl else 1)
                    safe_print(f"    中译重试 {attempt + 1}/{retries}：{e}，{wait:.0f}s…")
                    time.sleep(wait)
        raise last_err if last_err else RuntimeError("中译补全失败")

    tz = title_en
    az = _clip_summary(cur_sum or NO_ABSTRACT_ZH)

    if need_title:
        tz = _call(
            f"将下列英文论文标题译为中文，只输出一行：标题:中文译名\n\n{title_en}",
            "title_zh",
        )
    if need_summary:
        src = cur_sum if cur_sum and cur_sum not in (NO_ABSTRACT_LABEL, NO_ABSTRACT_ZH) else abs_en
        az = _clip_summary(
            _call(
                f"用一句话中文概括下列研究摘要（不超过{INVENTORY_SUMMARY_MAX}字；"
                f'无实质内容写「{NO_ABSTRACT_ZH}」。只输出一行：摘要:...\n\n{src}',
                "abstract_zh",
            )
        )
    return tz, az


def _fallback_judge(art: Article) -> Judgement:
    ok, reason = assess_zebrafish_primary(art)
    no = _article_no(art.filename)
    result = "是" if ok else "否"
    abs_en = art.abstract or ""
    summary = _clip_summary(abs_en[:INVENTORY_SUMMARY_MAX * 2]) if abs_en else NO_ABSTRACT_LABEL
    return Judgement(
        no=no,
        title_en=art.title,
        abstract_en=abs_en or NO_ABSTRACT_LABEL,
        title_zh=_orig_title(art.title),
        abstract_zh=summary,
        result=result,
        reason=reason,
    )


def judge_article(art: Article, use_llm: bool) -> Judgement:
    no = _article_no(art.filename)
    if use_llm:
        try:
            title_zh, abstract_zh, judgement, reason = _llm_judge(art.title, art.abstract)
            if judgement not in ("是", "否", "无法判别"):
                judgement = "无法判别"
            return Judgement(
                no=no,
                title_en=art.title,
                abstract_en=art.abstract or "",
                title_zh=title_zh,
                abstract_zh=abstract_zh,
                result=judgement,
                reason=reason,
            )
        except Exception as e:
            safe_print(f"  [{no}] LLM 最终失败，规则回退: {e}")
    j = _fallback_judge(art)
    j.reason = j.reason or "规则引擎"
    return j


def _render_md(results: List[Judgement]) -> str:
    yes_n = sum(1 for r in results if r.result == "是")
    no_n = sum(1 for r in results if r.result == "否")
    unk_n = sum(1 for r in results if r.result == "无法判别")
    rejected = [r.no for r in results if r.result in ("否", "无法判别")]

    lines = [
        "# 斑马鱼研究对象判别结果",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 文献数量：{len(results)}",
        "",
        "## 判别结果统计",
        "",
        "| 类别 | 数量 |",
        "|------|------|",
        f"| 是 | {yes_n} |",
        f"| 否 | {no_n} |",
        f"| 无法判别 | {unk_n} |",
        f"| 合计 | {len(results)} |",
        "",
        "## 否或无法判别的文章编号",
        "",
        ", ".join(rejected) if rejected else "（无）",
        "",
    ]

    for r in results:
        out_result = "否" if r.result == "无法判别" else r.result
        lines.extend([
            "---",
            "",
            f"## {r.no}",
            "",
            f"标题:{r.title_zh}",
            "",
            f"摘要:{r.abstract_zh}",
            "",
            f"判断结果:{out_result}",
            "",
        ])
    return "\n".join(lines)


def _write_single(output_dir: Path, r: Judgement) -> None:
    out_result = "否" if r.result == "无法判别" else r.result
    (output_dir / f"result_{r.no}.md").write_text(
        f"## {r.no}\n\n标题:{r.title_zh}\n\n摘要:{r.abstract_zh}\n\n判断结果:{out_result}\n",
        encoding="utf-8",
    )


def _save_checkpoint(output_dir: Path, results: List[Judgement], done_files: List[str]) -> None:
    ck = output_dir / "result_checkpoint.json"
    ck.write_text(
        json.dumps(
            {"done_files": done_files, "results": [asdict(r) for r in results]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (output_dir / "result.md").write_text(_render_md(results), encoding="utf-8")


def _load_checkpoint(output_dir: Path) -> Tuple[List[Judgement], set]:
    ck = output_dir / "result_checkpoint.json"
    if not ck.is_file():
        return [], set()
    data = json.loads(ck.read_text(encoding="utf-8"))
    results = [Judgement(**r) for r in data.get("results") or []]
    done = set(data.get("done_files") or [])
    return results, done


def run_batch(
    input_dir: Path,
    output_dir: Path,
    *,
    limit: int = 0,
    resume: bool = False,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    use_llm = bool(os.getenv("LLM_API_KEY", "").strip())
    max_safe = MAX_LLM_SAFE if use_llm else MAX_RULE_SAFE

    files = sorted(
        p for p in input_dir.glob("*.txt") if ARTICLE_FILE_RE.match(p.name)
    )
    if limit > 0:
        files = files[:limit]

    if len(files) > max_safe:
        raise ValueError(
            f"批量 {len(files)} 篇超过安全上限 {max_safe}（{'LLM' if use_llm else '规则'}模式）。"
            f"请分批或提高 JUDGE_MAX_* 环境变量。"
        )

    results: List[Judgement] = []
    done_files: set = set()
    if resume:
        results, done_files = _load_checkpoint(output_dir)
        safe_print(f"  续传：已完成 {len(done_files)} 篇")

    safe_print(
        f"  待判别 {len(files)} 篇（上限 {max_safe}，{'LLM' if use_llm else '规则'}）→ {output_dir}"
    )

    processed = 0
    for i, path in enumerate(files, 1):
        if path.name in done_files:
            continue
        art = _parse_txt(path)
        j = judge_article(art, use_llm)
        results.append(j)
        done_files.add(path.name)
        _write_single(output_dir, j)
        processed += 1
        safe_print(f"  [{i}/{len(files)}] {j.no} → {j.result}")

        if use_llm:
            time.sleep(float(os.getenv("JUDGE_REQUEST_INTERVAL", "0.35")))

        if processed % CHECKPOINT_EVERY == 0:
            _save_checkpoint(output_dir, results, sorted(done_files))
            safe_print(f"  [checkpoint] 已保存 {len(results)} 篇")

    results.sort(key=lambda r: int(r.no))
    md = _render_md(results)
    main_out = output_dir / "result.md"
    main_out.write_text(md, encoding="utf-8")

    stats = {
        "total": len(results),
        "yes": sum(1 for r in results if r.result == "是"),
        "no": sum(1 for r in results if r.result == "否"),
        "unknown": sum(1 for r in results if r.result == "无法判别"),
        "rejected_ids": [r.no for r in results if r.result in ("否", "无法判别")],
        "max_safe": max_safe,
        "mode": "llm" if use_llm else "rule",
        "output": str(main_out),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }
    (output_dir / "result_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ck = output_dir / "result_checkpoint.json"
    if ck.is_file():
        ck.unlink(missing_ok=True)
    safe_print(f"\n[完成] 判别 {len(results)} 篇 → {main_out}")
    return stats


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="input_dir", default=str(DEFAULT_IN))
    p.add_argument("--out", dest="output_dir", default="")
    p.add_argument("--limit", type=int, default=0, help=f"最多处理篇数，0=全部（LLM 安全上限 {MAX_LLM_SAFE}）")
    p.add_argument("--resume", action="store_true", help="从 checkpoint 续传")
    args = p.parse_args()
    in_dir = Path(args.input_dir)
    out_dir = Path(args.output_dir) if args.output_dir else in_dir / "result"
    if not in_dir.is_dir():
        safe_print(f"输入目录不存在：{in_dir}")
        return 1
    run_batch(in_dir, out_dir, limit=args.limit, resume=args.resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
