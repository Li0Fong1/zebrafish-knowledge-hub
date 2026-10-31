# -*- coding: utf-8 -*-
"""临时诊断 2：验证别名类问题的文末参考文献是否被补齐到 20~25 条、占位行是否清掉。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.entry import bootstrap_app  # noqa: E402

bootstrap_app()

from zebrafish_prompt import ensure_reference_footer  # noqa: E402

q = "tp53 都有哪些别名和历史名？"

sample = (
    "tp53 标准符号为 tp53，历史上还被称为 brp53 / drp53 / p53（NCBI、ZFIN）。\n\n"
    "参考文献（系统生成）：PMID 15292172 等。"
)

print("=== 输入 ===")
print(sample)
print("输入 [n] 行数 =", len(re.findall(r"(?m)^\[\d{1,3}\]\s+\S", sample)))

out = ensure_reference_footer(sample, "", question=q)

n_refs = len(re.findall(r"(?m)^\[\d{1,3}\]\s+\S", out))
print("\n=== 输出统计 ===")
print("文末 [n] 条数 =", n_refs)
print("仍含占位行「参考文献（系统生成）」 =", "参考文献（系统生成）" in out)
print("\n=== 输出前 1400 字 ===\n" + out[:1400])
