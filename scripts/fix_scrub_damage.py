# -*- coding: utf-8 -*-
"""修复 scrub_credentials.py 的 123456 误伤，并精确重做凭据清理。

背景：
  上一轮清理把独立数字 123456 也当成口令（CRED 正则第三条），
  在 CRED_CONTEXT 命中的行里做了替换，误伤了：
    - models/*/vocab.json 里的 token id（value 位置的数字 123456 → <set-via-env>）
  真口令（*_remote_NNNN / dengN_NNNNN）的替换是正确的，保留。

本脚本做两件事：
  1. restore：把三个 vocab.json 里的 <set-via-env> 还原为 123456，并做 JSON 合法性校验。
  2. recount：重新统计当前仍含明文口令的文件（只按真口令特征，不含 123456），
     输出docs/凭据清理报告_复核.md；不改动任何 py/sh/bat（避免二次误伤）。

用法：
  python scripts/fix_scrub_damage.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 真正的明文口令特征（不含通用的 123456）
REAL_CRED = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9]*_remote_[0-9]{4}|deng[0-9]_[0-9]{4,})"
)

VOCABS = [
    "models/Qwen-0.5B-Chat/vocab.json",
    "models/Qwen3-Embedding-4B/vocab.json",
    "models/gte-multilingual-base/vocab.json",
]

# 上一轮清理对 json/md/txt 的替换标记（_doc_repl）
DOC_MARK = "<set-via-env>"


def restore_vocabs() -> list[str]:
    lines = ["## 词表恢复（123456 误伤）", ""]
    for rel in VOCABS:
        p = ROOT / rel
        if not p.is_file():
            lines.append("- 跳过（不存在）：%s" % rel)
            continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        n = raw.count(DOC_MARK)
        if n == 0:
            # 没有标记，校验 JSON 是否已合法
            try:
                json.loads(raw)
                lines.append("- 未改动（已合法）：%s" % rel)
            except Exception as exc:
                lines.append("- ⚠ 无标记但 JSON 非法：%s %s" % (rel, str(exc)[:80]))
            continue
        # 尝试两种还原：裸数字（原 value 是无引号数字）与带引号数字。
        # 取第一个能通过 JSON 校验的结果，确保不写坏词表。
        candidates = [
            raw.replace(DOC_MARK, "123456"),
            raw.replace('"%s"' % DOC_MARK, '"123456"').replace(DOC_MARK, "123456"),
        ]
        fixed, data = None, None
        for cand in candidates:
            try:
                parsed = json.loads(cand)
                if isinstance(parsed, dict):
                    fixed, data = cand, parsed
                    break
            except Exception:
                continue
        if fixed is None:
            lines.append("- ❌ 两种还原均非法，未写盘：%s" % rel)
            continue
        p.write_text(fixed, encoding="utf-8")
        lines.append("- 已恢复 %d 处，JSON 合法，%d 个 token：%s" % (n, len(data), rel))
    return lines


def count_real_creds() -> list[str]:
    """只统计真口令特征，避免把合法数字 123456 再次算成问题。"""
    lines = ["", "## 真口令复核（不含通用数字误报）", ""]
    skip = {".git", ".workbuddy", "__pycache__", "data", "解析文件",
            "_shots", "论文学习", "测试集", "models", "node_modules", ".venv"}
    hits = []
    for dirpath, dirnames, filenames in __import__("os").walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if not fn.endswith((".py", ".sh", ".bat", ".sql", ".md", ".cnf",
                                ".yml", ".yaml", ".toml", ".ini", ".txt", ".example")):
                continue
            if fn.startswith(".env") and fn != ".env.example":
                continue
            p = Path(dirpath) / fn
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            n = len(REAL_CRED.findall(text))
            if n:
                hits.append((p.relative_to(ROOT).as_posix(), n))
    hits.sort(key=lambda x: -x[1])
    lines.append("- 仍含真口令字面量的文件数：**%d**" % len(hits))
    lines.append("- 说明：这些多为归档/一次性脚本；生产代码目录（api/core/database/web）应为空。")
    lines.append("")
    for rel, n in hits[:80]:
        lines.append("  %-72s %d 处" % (rel, n))
    if len(hits) > 80:
        lines.append("  ... 其余 %d 个文件略" % (len(hits) - 80))
    return lines


def main() -> int:
    out = [
        "# 凭据清理误伤修复与复核报告",
        "",
        "- 修复脚本：scripts/fix_scrub_damage.py",
        "- 原则：只恢复被误伤的数据文件，不二次改动 py/sh/bat；不记录任何口令原值。",
        "",
    ]
    out += restore_vocabs()
    out += count_real_creds()
    out += [
        "",
        "## 下一步",
        "",
        "1. 三个 vocab.json 恢复后需重新校验模型能加载（本地若有测试）。",
        "2. 归档/一次性脚本中的真口令：建议直接删除这些脚本，或统一改为从 .env 读取 ZF_PASS。",
        "3. 服务器上同样存在明文口令，见 scripts/clean_server_credentials.py。",
        "",
    ]
    rp = ROOT / "docs" / "凭据清理误伤修复报告.md"
    rp.parent.mkdir(exist_ok=True)
    rp.write_text("\n".join(out), encoding="utf-8")
    print("修复完成，报告：%s" % rp)
    for ln in out:
        if ln.startswith("- 已恢复") or ln.startswith("- ❌") or ln.startswith("- ⚠") or "文件数" in ln:
            print(ln)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
