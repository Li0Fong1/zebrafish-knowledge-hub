#统计检验摘要（精确置换检验）

- `cite:rag_partial|rag_full`: P_exact=0.5979, P_montecarlo=0.601, delta=0.0031
- `cite:hub_partial|hub_full`: P_exact=0.3747, P_montecarlo=0.3738, delta=0.0009
- `cite:fusion_full|hub_full`: P_exact=0.013, P_montecarlo=0.013, delta=0.0
- `cite:rag_full|hub_full`: P_exact=0.0158, P_montecarlo=0.0152, delta=0.0006
- `score:rag_partial|rag_full`: P_exact=0.5416, P_montecarlo=0.5409, delta=0.0007
- `score:fusion_partial|fusion_full`: P_exact=0.4671, P_montecarlo=0.4673, delta=0.0002
- `score:hub_partial|hub_full`: P_exact=0.8379, P_montecarlo=0.8355, delta=0.0024
- `score:rag_full|hub_full`: P_exact=0.8394, P_montecarlo=0.8388, delta=0.0006

## 一致性检查（14 项）

- [ok] `A1` 答案覆盖 10 题 × 6 配置且无重复 — recs=60 qids=10
- [ok] `A2` 评分覆盖与答案一致 — rows=60 skipped=[]
- [ok] `B1` 每题 6 标签↔6 配置双射，且与确定性打乱一致 — raw=10
- [ok] `C1` 各维度在 0..满分内、总分=各维度之和 — 越界=0 求和不符=0
- [ok] `C2` 大多数题目内部有区分度（极差>0） — 极差均值=2.05 为0的题=1
- [ok] `D1` 精确 Mann–Whitney 与 4 万次蒙特卡洛一致（|ΔP|≤0.02） — 最大Δ=0.0031
- [ok] `D2` 出图版本的 Wilcoxon 与独立精确枚举一致（|ΔP|≤0.005） — 最大Δ=0.0000
- [ok] `D3` 正态近似与精确检验的差距已记录（仅作参考，不用于出图） — cite:rag_partial|rag_full: 0.5312 vs 0.3951; cite:hub_partial|hub_full: 0.2266 vs 0.2065
- [ok] `E1` 行内引用计数可由答案原文复算 — 不符 0 条
- [ok] `F0` 评委 B 覆盖全部单元格 — common=60/60
- [ok] `F1` 两评委逐条总分秩相关 ≥0.3 — Spearman=0.754
- [warn] `F2` 两评委的配置均值排序一致（ρ≥0.5；不一致则记为警告） — Spearman=-0.029（均值区间 7.97–8.53，跨度 0.55/10）
- [ok] `F3` 两评委对各配置优劣方向一致 — [True, True, True, True]
- [ok] `G1` 配置均值可由评分文件复算 — 见上

> 本文件由 `scripts/build_figshare_package.py` 从检验结果 JSON 派生（仅格式转换）。
