# 图集回归门禁报告

- 生成时间：2026-09-28 10:33:03

## 权威快照（修复依据）

- 索引：**32,532 篇 / 7,651,942 块 / 170 分片 / 2048 维** / `qwen3_4b_v2_short_section` / 2026-09-10
- 模型：`Qwen3-Embedding-4B` · 切分 `paragraph_section`
- 参数：candidates **500** → rerank top **100** 篇 · 打包预算 **48000** 字符 · 切分 `paragraph` + section 加权（生效 `paragraph_section`）

- 检索实测（2026-09-27T22:08:41）：self-rank 14-56 · 中位耗时 16.8 s

## 修复阶段

| 步骤 | 说明 | 耗时 | 退出码 | 关键输出 |
|---|---|---|---|---|
| `verify_figures.py` | 自洽校验 | 13.3s | 0 | TOTAL=268 PASS=268 ERRORS=0 WARNINGS=0 |
| `audit_figures_vs_project.py` | 三方一致性审计 | 0.2s | 0 | [audit] server index: 32532 papers / 7651942 chunks / 170 shards / dim 2048 |

## 回归门禁

- 自洽校验：268 项断言 / 错误 0 / 警告 0（下限 200）
- 三方审计：27 条断言 / 差异 0（允许 0）
- 结论：**PASS ✅**

## 待处置差异

无 —— 图集与真实系统完全一致。
