# -*- coding: utf-8 -*-
"""富集分析统计：Fisher 精确检验 + Benjamini-Hochberg FDR。"""
from typing import List, Tuple

try:
    from scipy.stats import fisher_exact
except ImportError:
    fisher_exact = None


def fisher_enrichment_pvalue(k: int, n: int, M: int, N: int) -> float:
    """
    超几何 / Fisher 精确检验（单尾，富集方向）。
    k: 目标基因集中注释到该 term 的数量
    n: 目标基因集大小
    M: 背景中注释到该 term 的基因数
    N: 背景基因总数（universe）
    """
    if fisher_exact is None:
        return 1.0
    if k <= 0 or n <= 0 or M <= 0 or N <= 0:
        return 1.0
    a = k
    b = n - k
    c = M - k
    d = N - M - n + k
    if a < 0 or b < 0 or c < 0 or d < 0:
        return 1.0
    _, p = fisher_exact([[a, b], [c, d]], alternative="greater")
    return float(p)


def benjamini_hochberg(pvalues: List[float]) -> List[float]:
    """Benjamini-Hochberg FDR 校正，返回与输入等长的 padj 列表。"""
    n = len(pvalues)
    if n == 0:
        return []
    if n == 1:
        return [min(1.0, pvalues[0])]

    indexed = sorted(enumerate(pvalues), key=lambda x: x[1])
    padj = [1.0] * n
    prev = 1.0
    for rank in range(n, 0, -1):
        idx, p = indexed[rank - 1]
        q = min(prev, p * n / rank)
        padj[idx] = q
        prev = q
    return padj


def enrichment_metrics(k: int, n: int, M: int, N: int) -> Tuple[float, str, str]:
    """富集倍数、GeneRatio、BgRatio 字符串（clusterProfiler 风格）。"""
    fold = (k / n) / (M / N) if n and M else 0.0
    gene_ratio = f"{k}/{n}"
    bg_ratio = f"{M}/{N}"
    return round(fold, 4), gene_ratio, bg_ratio
